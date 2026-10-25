from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

app = FastAPI(title="PayBox", docs_url=None, redoc_url=None, openapi_url=None)

templates = Jinja2Templates(directory="templates")


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request, "home.html", {"title": "PayBox", "message": "Welcome to PayBox"}
    )
