from pwdlib import PasswordHash
from sqlalchemy import text
from sqlalchemy.orm import Session

password_hasher = PasswordHash.recommended()


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
