from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from lockers import read_lockers
from schemas import Registration
from users import create_user

router = APIRouter(prefix="/api")


@router.post("/register", status_code=201)
def register(details: Registration, db: Annotated[Session, Depends(get_db)]):
    user = create_user(db, details.email, details.password)
    if user is None:
        raise HTTPException(status_code=409, detail="This email is already registered.")
    return user


@router.get("/lockers")
def get_lockers(db: Annotated[Session, Depends(get_db)]):
    return read_lockers(db)
