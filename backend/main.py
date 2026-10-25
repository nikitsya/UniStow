from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from database import engine
from routes import api, pages


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    engine.dispose()


app = FastAPI(
    title="PayBox", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
app.mount("/static", StaticFiles(directory=pages.frontend_directory / "static"), name="static")
app.include_router(pages.router)
app.include_router(api.router)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exception: SQLAlchemyError):
    message = "Locker information is temporarily unavailable. Please try again shortly."
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=503, content={"detail": message})
    return pages.templates.TemplateResponse(
        request, "error.html", {"title": "PayBox", "message": message}, status_code=503
    )
