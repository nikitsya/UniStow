from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

app = FastAPI(title="PayBox", docs_url=None, redoc_url=None, openapi_url=None)

templates = Jinja2Templates(directory="templates")

demo_lockers = [
    {
        "id": 1,
        "name": "Locker 1",
        "status": "available",
        "door_state": "unknown",
        "is_simulated": True,
    }
]


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request, "home.html", {"title": "PayBox", "message": "Welcome to PayBox"}
    )


@app.get("/api/lockers")
def get_lockers():
    return demo_lockers
