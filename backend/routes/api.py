from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from auth.sessions import check_browser_request, current_user, end_session, start_session
from auth.users import authenticate_user, create_user
from database import get_db
from schemas import Login, Registration, RentalRequest
from services.commands import authorised_locker, record_command, request_open
from services.lockers import read_lockers
from services.payment_confirmation import cancel_pending_reservation, process_event, reconcile_rental, verify_event
from services.payments import create_checkout
from services.rentals import reserve_locker

router = APIRouter(prefix="/api")


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Annotated[Session, Depends(get_db)]):
    event = verify_event(await request.body(), request.headers.get("stripe-signature", ""))
    await run_in_threadpool(process_event, db, event)
    return {"received": True}


@router.post("/rentals/{rental_id}/reconcile", dependencies=[Depends(check_browser_request)])
def reconcile(rental_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    rental = reconcile_rental(db, rental_id, user["id"])
    return {"status": rental["status"]}


@router.post("/rentals/{rental_id}/cancel", dependencies=[Depends(check_browser_request)])
def cancel_reservation(rental_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    cancel_pending_reservation(db, rental_id, user["id"])
    return {"status": "cancelled"}


@router.post("/rentals/{rental_id}/checkout", dependencies=[Depends(check_browser_request)])
def checkout(rental_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return create_checkout(db, rental_id, user["id"])


@router.post("/rentals", status_code=201, dependencies=[Depends(check_browser_request)])
def create_rental(details: RentalRequest, request: Request,
                  db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return reserve_locker(db, user["id"], details.locker_id, details.days)


@router.post("/register", status_code=201, dependencies=[Depends(check_browser_request)])
def register(details: Registration, db: Annotated[Session, Depends(get_db)]):
    user = create_user(db, details.email, details.password)
    if user is None:
        raise HTTPException(status_code=409, detail="This email is already registered.")
    return user


@router.get("/lockers")
def get_lockers(db: Annotated[Session, Depends(get_db)]):
    return read_lockers(db)


@router.post("/login", dependencies=[Depends(check_browser_request)])
def login(details: Login, request: Request, response: Response,
          db: Annotated[Session, Depends(get_db)]):
    user = authenticate_user(db, details.email, details.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    start_session(db, request, response, user["id"])
    return user


@router.get("/me")
def me(request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


@router.post("/logout", status_code=204, dependencies=[Depends(check_browser_request)])
def logout(request: Request, response: Response, db: Annotated[Session, Depends(get_db)]):
    end_session(db, request, response)


@router.post("/rentals/{rental_id}/test-open", dependencies=[Depends(check_browser_request)])
def test_open_locker(rental_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    client = getattr(request.app.state, "open_test_client", None)
    if client is None:
        raise HTTPException(409, "Open test mode is disabled.")
    user = current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    _, rental, now = authorised_locker(db, rental_id, user["id"])
    expires_at = min(now.timestamp() + 10, rental["paid_until"].timestamp())
    db.commit()
    events = request.app.state.door_events
    return events.test_open(client, expires_at)


@router.post("/rentals/{rental_id}/open", status_code=202, dependencies=[Depends(check_browser_request)])
def open_locker(rental_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if getattr(request.app.state, "open_test_client", None) is not None:
        raise HTTPException(409, "Physical commands are disabled during the delivery test.")
    client = getattr(request.app.state, "open_client", None)
    if client is not None:
        locker, rental, now = authorised_locker(db, rental_id, user["id"])
        events = request.app.state.door_events
        controller = events.ready_controller()
        if not controller:
            raise HTTPException(409, "The locker controller is offline or busy.")
        command_id, expiry = record_command(db, locker, rental, now, controller, "open", dispatched=True)
        return events.open(client, command_id, expiry, controller)
    return request_open(db, rental_id, user["id"])


@router.post("/rentals/{rental_id}/finish", status_code=202, dependencies=[Depends(check_browser_request)])
def finish_rental(rental_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(db, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if request.headers.get("X-UniStow-Collect-All") != "yes":
        raise HTTPException(422, "Confirm that you will collect all your belongings.")
    if getattr(request.app.state, "open_test_client", None) is not None:
        raise HTTPException(409, "Physical commands are disabled during the delivery test.")
    return request_open(db, rental_id, user["id"], action="finish")
