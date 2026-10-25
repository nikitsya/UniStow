from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from lockers import read_lockers

router = APIRouter()
frontend_directory = Path(__file__).resolve().parent.parent / "frontend"
templates = Jinja2Templates(directory=frontend_directory / "templates")


@router.get("/")
def home(request: Request, db: Annotated[Session, Depends(get_db)]):
    return templates.TemplateResponse(
        request, "home.html", {"title": "PayBox", "lockers": read_lockers(db)}
    )


@router.get("/lockers/{locker_id}/rent")
def rental(request: Request, locker_id: int, db: Annotated[Session, Depends(get_db)]):
    lockers = read_lockers(db, locker_id)
    if not lockers:
        raise HTTPException(status_code=404, detail="Locker not found")
    locker = lockers[0]
    if locker["status"] != "available":
        raise HTTPException(status_code=409, detail="This locker is unavailable")
    return templates.TemplateResponse(
        request, "rental.html", {"title": f"{locker['name']} | PayBox", "locker": locker}
    )


@router.get("/api/lockers")
def get_lockers(db: Annotated[Session, Depends(get_db)]):
    return read_lockers(db)
