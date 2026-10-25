from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from lockers import read_lockers
from sessions import current_user

router = APIRouter()
frontend_directory = Path(__file__).resolve().parents[2] / "frontend"
templates = Jinja2Templates(directory=frontend_directory / "templates")


@router.get("/")
def home(request: Request, db: Annotated[Session, Depends(get_db)]):
    return templates.TemplateResponse(
        request, "home.html", {"title": "PayBox", "lockers": read_lockers(db),
                               "user": current_user(db, request)}
    )


@router.get("/register")
def registration(request: Request):
    return templates.TemplateResponse(
        request, "register.html", {"title": "Create account | PayBox"}
    )


@router.get("/login")
def sign_in(request: Request):
    return templates.TemplateResponse(request, "login.html", {"title": "Sign in | PayBox"})


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
