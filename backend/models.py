from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Identity, Text, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(password_hash) > 0"),
        CheckConstraint("email = lower(btrim(email)) AND email <> ''", name="normalised_email"),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Locker(Base):
    __tablename__ = "lockers"
    __table_args__ = (
        CheckConstraint("number > 0"),
        CheckConstraint("size IN ('small', 'large')"),
        CheckConstraint("price_per_day_cents >= 0"),
        CheckConstraint("door_state IN ('open', 'closed', 'unknown')"),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    number: Mapped[int] = mapped_column(unique=True)
    size: Mapped[str] = mapped_column(Text)
    price_per_day_cents: Mapped[int]
    is_enabled: Mapped[bool] = mapped_column(server_default=false())
    door_state: Mapped[str] = mapped_column(Text, server_default=text("'unknown'"))
    device_ready: Mapped[bool] = mapped_column(server_default=false())
    controller_id: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
