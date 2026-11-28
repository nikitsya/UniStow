import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from typing import Annotated

from auth.sessions import check_browser_request, current_user, end_session, start_session
from auth.users import authenticate_user, create_user
from database import get_db
from schemas import (CheckoutResponse, CommandResponse, LockerResponse, Login, Registration,
                     RentalRequest, RentalResponse, StatusResponse, UserResponse, WebhookResponse)
from services.commands import authorised_locker, record_command, request_open
from services.lockers import read_lockers
from services.overdue_payments import create_outstanding_checkout, reconcile_outstanding
from services.payment_confirmation import cancel_pending_reservation, process_event, reconcile_rental, verify_event
from services.payments import create_checkout
from services.rentals import reserve_locker

router = APIRouter(prefix="/api")


@router.post("/rentals/{rental_id}/outstanding/checkout", response_model=CheckoutResponse,
             dependencies=[Depends(check_browser_request)])
async def outstanding_checkout(rental_id: int, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    try:
        return await create_outstanding_checkout(db, rental_id, user["id"])
    except (stripe.StripeError, ValueError):
        raise HTTPException(503, "Outstanding payment is temporarily unavailable.") from None


@router.post("/rentals/{rental_id}/outstanding/reconcile", response_model=StatusResponse,
             dependencies=[Depends(check_browser_request)])
async def outstanding_reconcile(rental_id: int, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    try:
        return await reconcile_outstanding(db, rental_id, user["id"])
    except stripe.StripeError:
        raise HTTPException(503, "Payment verification is temporarily unavailable.") from None


@router.post("/stripe/webhook", response_model=WebhookResponse)
async def stripe_webhook(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    event = verify_event(await request.body(), request.headers.get("stripe-signature", ""))
    await process_event(db, event)
    return {"received": True}


@router.post("/rentals/{rental_id}/reconcile", response_model=StatusResponse,
             dependencies=[Depends(check_browser_request)])
async def reconcile(rental_id: int, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    rental = await reconcile_rental(db, rental_id, user["id"])
    return {"status": rental["status"]}


@router.post("/rentals/{rental_id}/cancel", response_model=StatusResponse,
             dependencies=[Depends(check_browser_request)])
async def cancel_reservation(rental_id: int, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    await cancel_pending_reservation(db, rental_id, user["id"])
    return {"status": "cancelled"}


@router.post("/rentals/{rental_id}/checkout", response_model=CheckoutResponse,
             dependencies=[Depends(check_browser_request)])
async def checkout(rental_id: int, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return await create_checkout(db, rental_id, user["id"])


@router.post("/rentals", response_model=RentalResponse, status_code=201, dependencies=[Depends(check_browser_request)])
async def create_rental(details: RentalRequest, request: Request,
                        db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return await reserve_locker(db, user["id"], details.locker_id, details.days, details.payment_method_consent)


@router.post("/register", response_model=UserResponse, status_code=201, dependencies=[Depends(check_browser_request)])
async def register(details: Registration, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await create_user(db, details.email, details.password)
    if user is None:
        raise HTTPException(status_code=409, detail="This email is already registered.")
    return user


@router.get("/lockers", response_model=list[LockerResponse])
async def get_lockers(db: Annotated[AsyncSession, Depends(get_db)]):
    return await read_lockers(db)


@router.post("/login", response_model=UserResponse, dependencies=[Depends(check_browser_request)])
async def login(details: Login, request: Request, response: Response,
                db: Annotated[AsyncSession, Depends(get_db)]):
    user = await authenticate_user(db, details.email, details.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    await start_session(db, request, response, user["id"])
    return user


@router.get("/me", response_model=UserResponse)
async def me(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


@router.post("/logout", status_code=204, dependencies=[Depends(check_browser_request)])
async def logout(request: Request, response: Response, db: Annotated[AsyncSession, Depends(get_db)]):
    await end_session(db, request, response)


@router.post("/rentals/{rental_id}/test-open", response_model=CommandResponse,
             dependencies=[Depends(check_browser_request)])
async def test_open_locker(rental_id: int, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    client = getattr(request.app.state, "open_test_client", None)
    if client is None:
        raise HTTPException(409, "Open test mode is disabled.")
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    _, rental, now = await authorised_locker(db, rental_id, user["id"])
    expires_at = min(now.timestamp() + 10, rental["paid_until"].timestamp())
    await db.commit()
    events = request.app.state.door_events
    return await run_in_threadpool(lambda: events.test_open(client, expires_at))


@router.post("/rentals/{rental_id}/open", response_model=CommandResponse, status_code=202,
             dependencies=[Depends(check_browser_request)])
async def open_locker(rental_id: int, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if getattr(request.app.state, "open_test_client", None) is not None:
        raise HTTPException(409, "Physical commands are disabled during the delivery test.")
    client = getattr(request.app.state, "open_client", None)
    if client is not None:
        locker, rental, now = await authorised_locker(db, rental_id, user["id"])
        events = request.app.state.door_events
        controller = events.ready_controller()
        if not controller:
            raise HTTPException(409, "The locker controller is offline or busy.")
        command_id, expiry = await record_command(db, locker, rental, now, controller, "open", dispatched=True)
        return await run_in_threadpool(lambda: events.open(client, command_id, expiry, controller))
    return await request_open(db, rental_id, user["id"])


@router.post("/rentals/{rental_id}/finish", response_model=CommandResponse, status_code=202,
             dependencies=[Depends(check_browser_request)])
async def finish_rental(rental_id: int, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if request.headers.get("X-UniStow-Collect-All") != "yes":
        raise HTTPException(422, "Confirm that you will collect all your belongings.")
    if getattr(request.app.state, "open_test_client", None) is not None:
        raise HTTPException(409, "Physical commands are disabled during the delivery test.")
    return await request_open(db, rental_id, user["id"], action="finish")
