from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import engine, get_db
from lockers import read_lockers


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    engine.dispose()


app = FastAPI(
    title="PayBox", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exception: SQLAlchemyError):
    message = "Locker information is temporarily unavailable. Please try again shortly."
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=503, content={"detail": message})
    return templates.TemplateResponse(
        request, "error.html", {"title": "PayBox", "message": message}, status_code=503
    )


@app.get("/")
def home(request: Request, db: Annotated[Session, Depends(get_db)]):
    return templates.TemplateResponse(
        request, "home.html", {"title": "PayBox", "lockers": read_lockers(db)}
    )


@app.get("/lockers/{locker_id}/rent")
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


@app.get("/api/lockers")
def get_lockers(db: Annotated[Session, Depends(get_db)]):
    return read_lockers(db)
