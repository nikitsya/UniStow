from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.sessions import current_user
from database import get_db
from services.booking_eligibility import booking_eligibility
from services.lockers import read_lockers
from services.payments import owned_rental

router = APIRouter()
frontend_directory = Path(__file__).resolve().parents[2] / "frontend"
templates = Jinja2Templates(directory=frontend_directory / "templates")


async def current_rentals(db, user):
    return (await db.execute(text("""
                           SELECT rentals.id, rentals.locker_id, rentals.status, lockers.number
                           FROM rentals
                                    JOIN lockers ON lockers.id = rentals.locker_id
                           WHERE user_id = :user_id
                             AND (status IN ('active', 'overdue')
                               OR (status = 'completed' AND EXISTS (SELECT 1
                                                                    FROM overdue_charges c
                                                                    WHERE c.rental_id = rentals.id
                                                                      AND c.status IN ('failed', 'pending', 'review')))
                               OR (status = 'pending'
                                   AND (reserved_until > CURRENT_TIMESTAMP
                                       OR EXISTS (SELECT 1 FROM payments WHERE payments.rental_id = rentals.id))))
                           ORDER BY rentals.id DESC
                                  """), {"user_id": user["id"]})).mappings().all() if user else []


async def completed_rentals(db, user):
    rows = (await db.execute(text("""
                           SELECT r.id, r.completed_at, l.number
                           FROM rentals r
                                    JOIN lockers l ON l.id = r.locker_id
                           WHERE r.user_id = :user_id
                             AND r.status = 'completed'
                           ORDER BY r.completed_at DESC, r.id DESC
                           LIMIT 5
                                  """), {"user_id": user["id"]})).mappings().all()
    return [dict(row, completed_on=row["completed_at"].astimezone(
        ZoneInfo("Europe/Dublin")).strftime("%d %B %Y at %H:%M")) for row in rows]


@router.get("/")
async def home(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    return templates.TemplateResponse(
        request, "pages/home.html", {"title": "UniStow", "lockers": await read_lockers(db), "user": user,
                                     "eligibility": await booking_eligibility(db, user['id'] if user else None),
                                     "own_rentals": {r["locker_id"]: r for r in await current_rentals(db, user)
                                                     if r['status'] != 'completed'}}
    )


@router.get("/rentals")
async def my_rentals(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        return RedirectResponse("/login?my_rentals=true", status_code=303)
    completed = await completed_rentals(db, user)
    completed_ids = {r["id"] for r in completed}
    current = await current_rentals(db, user)
    outstanding_ids = {r["id"] for r in current if r["status"] == "completed"}
    for completed_rental in completed:
        completed_rental["has_outstanding"] = completed_rental["id"] in outstanding_ids
    reservations = [r for r in current if r["id"] not in completed_ids]
    return templates.TemplateResponse(
        request, "rentals/my_rentals.html", {"title": "My rentals | UniStow", "user": user,
                                             "eligibility": await booking_eligibility(db, user['id']),
                                             "reservations": reservations, "completed_rentals": completed}
    )


@router.get("/contact")
async def contact(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    context: dict[str, object] = {"title": "Contact | UniStow"}
    try:
        context["user"] = await current_user(db, request)
    except SQLAlchemyError:
        context["account_unavailable"] = True
    return templates.TemplateResponse(request, "pages/contact.html", context)


@router.get("/register")
def registration(request: Request):
    return templates.TemplateResponse(
        request, "account/register.html", {"title": "Create account | UniStow"}
    )


@router.get("/login")
def sign_in(request: Request, locker_id: int | None = None, rental_id: int | None = None,
            show_rentals: Annotated[bool, Query(alias="my_rentals")] = False):
    destination = (f"/rentals/{rental_id}" if rental_id is not None else
                   f"/lockers/{locker_id}/rent" if locker_id is not None else
                   "/rentals" if show_rentals else "/")
    return templates.TemplateResponse(
        request, "account/login.html", {"title": "Sign in | UniStow", "destination": destination}
    )


@router.get("/rentals/{rental_id}")
async def reservation(request: Request, rental_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        return RedirectResponse(f"/login?rental_id={rental_id}", status_code=303)
    owned = await owned_rental(db, rental_id, user["id"])
    charges = (await db.execute(text("""
                              SELECT status, COALESCE(SUM(amount_cents), 0) AS amount
                              FROM overdue_charges
                              WHERE rental_id = :id
                                AND status <> 'waived'
                              GROUP BY status
                                     """), {"id": rental_id})).mappings().all()
    additional = {row["status"]: row["amount"] for row in charges}
    billing = {
        "paid": additional.get("paid", 0),
        "pending": additional.get("pending", 0) + additional.get("review", 0),
        "failed": additional.get("failed", 0),
        "additional": sum(additional.values()),
        "total": owned["total_price_cents"] + sum(additional.values()),
        "recovery_pending": await db.scalar(text("""
                                           SELECT amount_cents
                                           FROM overdue_payments
                                           WHERE rental_id = :id
                                             AND status = 'pending'
                                           """), {"id": rental_id}) or 0,
    }
    billing["checkout_amount"] = billing["recovery_pending"] or billing["failed"]
    billing["unpaid"] = max(0, billing["failed"] - billing["recovery_pending"])
    ends_at = owned["paid_until"].astimezone(ZoneInfo("Europe/Dublin"))
    last_seen = owned.get("last_seen_at")
    device_online = last_seen is not None and 0 <= (
            datetime.now(timezone.utc) - last_seen
    ).total_seconds() < 15
    door_state = owned.get("door_state") if device_online else "unknown"
    events = getattr(request.app.state, "door_events", None)
    if owned["number"] == 4 and events is not None:
        door_state = events.latest(last_seen) or door_state
    open_test = getattr(request.app.state, "open_test_client", None) is not None
    controller_ready = device_online and owned.get("device_ready", False) and owned.get("door_state") in ("open",
                                                                                                          "closed")
    physical = getattr(request.app.state, "open_client", None) is not None
    open_ready = (events is not None and bool(events.ready_controller())) if physical else controller_ready
    return templates.TemplateResponse(request, "rentals/reservation.html", {
        "open_test": open_test, "http_ready": controller_ready,
        "title": "My rental | UniStow", "rental": owned, "billing": billing,
        "eligibility": await booking_eligibility(db, user['id']),
        "ends_at": ends_at.strftime("%d %B %Y at %H:%M"),
        "door_state": door_state, "device_online": device_online,
        "can_open": (owned["number"] == 4 and owned["status"] == "active"
                     and owned.get("payment_status") == "paid"
                     and owned["paid_until"] > datetime.now(timezone.utc)
                     and (open_test or open_ready)),
    })


@router.get("/lockers/{locker_id}/rent")
async def rental(request: Request, locker_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        return RedirectResponse(f"/login?locker_id={locker_id}", status_code=303)
    lockers = await read_lockers(db, locker_id)
    if not lockers:
        raise HTTPException(status_code=404, detail="Locker not found")
    locker = lockers[0]
    if locker["status"] != "available":
        return templates.TemplateResponse(
            request, "pages/error.html", {"title": "Locker unavailable | UniStow",
                                          "message": "This locker is temporarily unavailable. Please choose another locker.",
                                          "retry_url": "/"}, status_code=409
        )
    return templates.TemplateResponse(
        request, "rentals/rental.html", {"title": f"{locker['name']} | UniStow", "locker": locker,
                                         "eligibility": await booking_eligibility(db, user['id'])}
    )
