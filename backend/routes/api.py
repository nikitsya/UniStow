from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from lockers import read_lockers

router = APIRouter(prefix="/api")


@router.get("/lockers")
def get_lockers(db: Annotated[Session, Depends(get_db)]):
    return read_lockers(db)
