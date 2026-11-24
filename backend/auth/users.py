from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from models import User

password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash("Unused password for timing protection")


async def authenticate_user(db: AsyncSession, email: str, password: str):
    user = await db.scalar(select(User).where(User.email == email))
    valid = await run_in_threadpool(password_hasher.verify,
        password, user.password_hash if user else dummy_password_hash
    )
    if user and valid:
        return {"id": user.id, "email": user.email}
    return None


async def create_user(db: AsyncSession, email: str, password: str):
    user = User(email=email, password_hash=await run_in_threadpool(password_hasher.hash, password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # The unique constraint also protects against concurrent registrations.
        if await db.scalar(select(User.id).where(User.email == email)) is not None:
            return None
        raise
    await db.refresh(user)
    return {"id": user.id, "email": user.email}
