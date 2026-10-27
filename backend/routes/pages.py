from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from auth.sessions import current_user
from database import get_db
from services.lockers import read_lockers
from services.payments import owned_rental

router = APIRouter()
frontend_directory = Path(__file__).resolve().parents[2] / "frontend"
templates = Jinja2Templates(directory=frontend_directory / "templates")


def current_rentals(db, user):
    return db.execute(text("""
                           SELECT rentals.id, rentals.locker_id, rentals.status, lockers.number
                           FROM rentals
                                    JOIN lockers ON lockers.id = rentals.locker_id
                           WHERE user_id = :user_id
                             AND (status IN ('active', 'overdue') OR (status = 'pending'
                               AND (reserved_until > CURRENT_TIMESTAMP
                                   OR EXISTS (SELECT 1 FROM payments WHERE payments.rental_id = rentals.id))))
                           ORDER BY rentals.id DESC
                           """), {"user_id": user["id"]}).mappings().all() if user else []


@router.get("/")
def home(request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    return templates.TemplateResponse(
        request, "home.html", {"title": "PayBox", "lockers": read_lockers(db), "user": user,
                               "own_rentals": {r["locker_id"]: r for r in current_rentals(db, user)}}
    )


@router.get("/rentals")
def my_rentals(request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        return RedirectResponse("/login?my_rentals=true", status_code=303)
    reservations = current_rentals(db, user)
    return templates.TemplateResponse(
        request, "my_rentals.html", {"title": "My rentals | PayBox", "user": user,
                                     "reservations": reservations}
    )


@router.get("/register")
def registration(request: Request):
    return templates.TemplateResponse(
        request, "register.html", {"title": "Create account | PayBox"}
    )


@router.get("/login")
def sign_in(request: Request, locker_id: int | None = None, rental_id: int | None = None,
            my_rentals: bool = False):
    destination = (f"/rentals/{rental_id}" if rental_id is not None else
                   f"/lockers/{locker_id}/rent" if locker_id is not None else
                   "/rentals" if my_rentals else "/")
    return templates.TemplateResponse(
        request, "login.html", {"title": "Sign in | PayBox", "destination": destination}
    )


@router.get("/rentals/{rental_id}")
def reservation(request: Request, rental_id: int, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        return RedirectResponse(f"/login?rental_id={rental_id}", status_code=303)
    rental = owned_rental(db, rental_id, user["id"])
    ends_at = rental["paid_until"].astimezone(ZoneInfo("Europe/Dublin"))
    last_seen = rental.get("last_seen_at")
    device_online = last_seen is not None and 0 <= (
            datetime.now(timezone.utc) - last_seen
    ).total_seconds() < 15
    return templates.TemplateResponse(request, "reservation.html", {
        "title": "My rental | PayBox", "rental": rental,
        "ends_at": ends_at.strftime("%d %B %Y at %H:%M"),
        "device_online": device_online,
        "can_open": (rental["number"] == 4 and rental["status"] == "active"
                     and rental.get("payment_status") == "paid"
                     and rental["paid_until"] > datetime.now(timezone.utc)
                     and device_online and rental.get("device_ready", False)
                     and rental.get("door_state") in ("open", "closed")),
    })


@router.get("/lockers/{locker_id}/rent")
def rental(request: Request, locker_id: int, db: Annotated[Session, Depends(get_db)]):
    if current_user(db, request) is None:
        return RedirectResponse(f"/login?locker_id={locker_id}", status_code=303)
    lockers = read_lockers(db, locker_id)
    if not lockers:
        raise HTTPException(status_code=404, detail="Locker not found")
    locker = lockers[0]
    if locker["status"] != "available":
        raise HTTPException(status_code=409, detail="This locker is unavailable")
    return templates.TemplateResponse(
        request, "rental.html", {"title": f"{locker['name']} | PayBox", "locker": locker}
    )
