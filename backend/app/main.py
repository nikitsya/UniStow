"""FastAPI entry point for TEMSAFE."""

from fastapi import FastAPI

from app.api.health import router as health_router

app = FastAPI(
    title="TEMSAFE API",
    description="Backend API for TEMSAFE.",
    version="0.1.0",
)
app.include_router(health_router)
