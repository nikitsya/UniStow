from pwdlib import PasswordHash
from sqlalchemy import text
from sqlalchemy.orm import Session

password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash("Unused password for timing protection")


def authenticate_user(db: Session, email: str, password: str):
    user = db.execute(
        text("SELECT id, email, password_hash FROM users WHERE email = :email"),
        {"email": email},
    ).mappings().first()
    valid = password_hasher.verify(
        password, user["password_hash"] if user else dummy_password_hash
    )
    if user and valid:
        return {"id": user["id"], "email": user["email"]}
    return None


def create_user(db: Session, email: str, password: str):
    user = db.execute(
        text("""
            INSERT INTO users (email, password_hash)
            VALUES (:email, :password_hash)
            ON CONFLICT (email) DO NOTHING
            RETURNING id, email
        """),
        {"email": email, "password_hash": password_hasher.hash(password)},
    ).mappings().first()
    db.commit()
    return dict(user) if user else None
