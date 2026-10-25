from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="PayBox", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

unavailable_lockers = {1, 2, 3, 5, 6, 7, 8, 9}
demo_lockers = [
    {
        "id": number,
        "name": f"Locker {number}",
        "size": "Small" if number <= 6 else "Large",
        "price_per_day_cents": 100 if number <= 6 else 150,
        "status": "unavailable" if number in unavailable_lockers else "available",
        "door_state": "unknown",
        "is_simulated": True,
    }
    for number in range(1, 10)
]


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request, "home.html", {"title": "PayBox", "lockers": demo_lockers}
    )


@app.get("/lockers/{locker_id}/rent")
def rental(request: Request, locker_id: int):
    locker = next((item for item in demo_lockers if item["id"] == locker_id), None)

    if locker is None:
        raise HTTPException(status_code=404, detail="Locker not found")
    if locker["status"] != "available":
        raise HTTPException(status_code=409, detail="This locker is unavailable")

    return templates.TemplateResponse(
        request, "rental.html", {"title": f"{locker['name']} | PayBox", "locker": locker}
    )


@app.get("/api/lockers")
def get_lockers():
    return demo_lockers
