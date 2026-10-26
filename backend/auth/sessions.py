import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

COOKIE_NAME = "paybox_session"
SESSION_SECONDS = 24 * 60 * 60


def check_browser_request(request: Request):
    # Cross-origin forms cannot send this header; CORS is not enabled.
    if request.headers.get("X-PayBox-Request") != "1":
        raise HTTPException(status_code=403, detail="Invalid request.")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def remove_session(db: Session, request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.execute(text("DELETE FROM sessions WHERE token_hash = :token_hash"),
                   {"token_hash": token_hash(token)})


def start_session(db: Session, request: Request, response: Response, user_id: int):
    remove_session(db, request)
    token = secrets.token_urlsafe(32)
    db.execute(text("""
                    INSERT INTO sessions (token_hash, user_id, expires_at)
                    VALUES (:token_hash, :user_id, :expires_at)
                    """), {"token_hash": token_hash(token), "user_id": user_id,
                           "expires_at": datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)})
    db.commit()
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")


def current_user(db: Session, request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user = db.execute(text("""
                           SELECT users.id, users.email
                           FROM sessions
                                    JOIN users ON users.id = sessions.user_id
                           WHERE sessions.token_hash = :token_hash
                             AND sessions.expires_at > :now
                           """),
                      {"token_hash": token_hash(token), "now": datetime.now(timezone.utc)}).mappings().first()
    return dict(user) if user else None


def end_session(db: Session, request: Request, response: Response):
    remove_session(db, request)
    db.commit()
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax",
                           secure=request.url.scheme == "https")
