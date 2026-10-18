# TEMSAFE backend

Basic Python and FastAPI application. The existing root README describes the
earlier PayBox proposal; this directory contains the TEMSAFE backend.

## Development environment

Use Python 3.11 or later. From the repository root:

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The backend uses its own virtual environment, separate from any environment at
the repository root. No database or external service credentials are required.

## Run locally

From `backend`, with the virtual environment activated:

```sh
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- Interactive API documentation: <http://127.0.0.1:8000/docs>
- Alternative API documentation: <http://127.0.0.1:8000/redoc>
- OpenAPI schema: <http://127.0.0.1:8000/openapi.json>
- Application health: <http://127.0.0.1:8000/health>

Check the running application in another terminal:

```sh
curl --fail http://127.0.0.1:8000/health
```

Expected response: `{"status":"ok"}`. This checks application availability;
it does not check any future database or device integrations.

Press `Ctrl+C` to stop the server and run `deactivate` to leave the environment.

## Structure

```text
backend/
├── app/
│   ├── __init__.py
│   ├── main.py          # Application entry point
│   └── api/
│       ├── __init__.py
│       └── health.py    # Health endpoint and response schema
├── README.md
└── requirements.txt
```

Add API routers under `app/api` and register them in `app/main.py` as the
application grows.
