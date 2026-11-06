import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

password = os.environ.get("POSTGRES_PASSWORD")
if not password:
    raise RuntimeError("Set POSTGRES_PASSWORD in the project's .env file.")

connect_args = {"connect_timeout": 3}
root_certificate = os.environ.get("POSTGRES_SSLROOTCERT")
if root_certificate:
    connect_args.update(sslmode="verify-full", sslrootcert=str(project_root / root_certificate))

engine = create_engine(
    URL.create(
        "postgresql+psycopg",
        username=os.environ.get("POSTGRES_USER", "unistow"),
        password=password,
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5433")),
        database=os.environ.get("POSTGRES_DB", "unistow"),
    ),
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(autoflush=False, bind=engine)


def get_db():
    with SessionLocal() as db:
        yield db
