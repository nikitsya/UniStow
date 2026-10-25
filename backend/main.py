from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from routes import api, pages

app = FastAPI(
    title="PayBox", docs_url=None, redoc_url=None, openapi_url=None
)
app.mount("/static", StaticFiles(directory=pages.frontend_directory / "static"), name="static")

app.include_router(pages.router)
app.include_router(api.router)


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


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exception: SQLAlchemyError):
    message = "The service is temporarily unavailable. Please try again shortly."
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=503, content={"detail": message})
    return pages.templates.TemplateResponse(
        request, "error.html", {"title": "PayBox", "message": message}, status_code=503
    )
