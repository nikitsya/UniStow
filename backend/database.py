import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

password = os.environ.get("POSTGRES_PASSWORD")
if not password:
    raise RuntimeError("Set POSTGRES_PASSWORD in the project's .env file.")

engine = create_engine(
    URL.create(
        "postgresql+psycopg",
        username="paybox",
        password=password,
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5433")),
        database="paybox",
    ),
    pool_pre_ping=True,
    connect_args={"connect_timeout": 3},
)

SessionLocal = sessionmaker(autoflush=False, bind=engine)


def get_db():
    with SessionLocal() as db:
        yield db
