from fastapi import FastAPI

app = FastAPI(title="PayBox", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/")
def home() -> dict[str, str]:
    return {"message": "PayBox is running"}
