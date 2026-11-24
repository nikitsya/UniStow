from pwdlib import PasswordHash
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash("Unused password for timing protection")


async def authenticate_user(db: AsyncSession, email: str, password: str):
    user = (await db.execute(
        text("SELECT id, email, password_hash FROM users WHERE email = :email"),
        {"email": email},
    )).mappings().first()
    valid = await run_in_threadpool(password_hasher.verify,
        password, user["password_hash"] if user else dummy_password_hash
    )
    if user and valid:
        return {"id": user["id"], "email": user["email"]}
    return None


async def create_user(db: AsyncSession, email: str, password: str):
    user = (await db.execute(
        text("""
             INSERT INTO users (email, password_hash)
             VALUES (:email, :password_hash)
             ON CONFLICT (email) DO NOTHING
             RETURNING id, email
             """),
        {"email": email, "password_hash": await run_in_threadpool(password_hasher.hash, password)},
    )).mappings().first()
    await db.commit()
    return dict(user) if user else None
