import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from routes import api, device, pages
from services.door_events import lifespan as device_lifespan
from services.overdue_billing import billing_worker


@asynccontextmanager
async def lifespan(app):
    with billing_worker(os.getenv("UNISTOW_OVERDUE_BILLING") == "1"):
        async with device_lifespan(app):
            yield


app = FastAPI(
    lifespan=lifespan, title="UniStow", docs_url=None, redoc_url=None, openapi_url=None
)
app.mount("/static", StaticFiles(directory=pages.frontend_directory / "static"), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(device.router)


@app.middleware("http")
async def prevent_account_caching(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exception: RequestValidationError):
    errors = [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
        for error in exception.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(Exception)
@app.exception_handler(SQLAlchemyError)
async def service_error(request: Request, exception: Exception):
    logging.getLogger(__name__).error("Request failed on %s (%s)", request.url.path, type(exception).__name__)
    message = ("We can’t show your rental right now." if request.url.path.startswith("/rentals")
               else "We can’t load this page right now. Please try again shortly.")
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=503, content={"detail": message})
    return pages.templates.TemplateResponse(
        request, "error.html", {"title": "Please try again | UniStow", "message": message,
                                "retry_url": request.url.path}, status_code=503
    )
