from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from database import get_db
from lockers import read_lockers
from payments import create_checkout
from rentals import reserve_locker
from schemas import Login, Registration, RentalRequest
from sessions import check_browser_request, current_user, end_session, start_session
from users import authenticate_user, create_user

router = APIRouter(prefix="/api")


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


@router.post("/register", status_code=201)
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
