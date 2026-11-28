from datetime import date, datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Text, false, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from typing import Any

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


class UserSession(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


class Rental(Base):
    __tablename__ = "rentals"

    # database/schema.sql owns the existing constraints and partial indexes.
    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    locker_id: Mapped[int] = mapped_column(ForeignKey("lockers.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    prepaid_days: Mapped[int] = mapped_column(server_default=text("1"))
    reserved_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_price_cents: Mapped[int]
    overdue_daily_fee_cents: Mapped[int] = mapped_column(server_default=text("100"))
    overdue_terms_version: Mapped[str] = mapped_column(Text)
    overdue_terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("status IN ('pending', 'paid', 'expired')"),)

    rental_id: Mapped[int] = mapped_column(ForeignKey("rentals.id", ondelete="RESTRICT"), primary_key=True)
    request_key: Mapped[str] = mapped_column(Text, unique=True)
    checkout_params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checkout_session_id: Mapped[str | None] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    payment_intent_id: Mapped[str | None] = mapped_column(Text, unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(Text)
    stripe_payment_method_id: Mapped[str | None] = mapped_column(Text)
    payment_method_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    checkout_session_id: Mapped[str] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LockerCommand(Base):
    __tablename__ = "locker_commands"
    __table_args__ = (
        CheckConstraint("action IN ('open', 'finish')"),
        CheckConstraint("expires_at > created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    rental_id: Mapped[int] = mapped_column(ForeignKey("rentals.id"))
    locker_id: Mapped[int] = mapped_column(ForeignKey("lockers.id"))
    controller_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OverdueCharge(Base):
    __tablename__ = "overdue_charges"

    # database/schema.sql owns the existing constraints and indexes.
    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    rental_id: Mapped[int] = mapped_column(ForeignKey("rentals.id", ondelete="RESTRICT"))
    charge_date: Mapped[date]
    amount_cents: Mapped[int] = mapped_column(server_default=text("100"))
    currency: Mapped[str] = mapped_column(Text, server_default=text("'eur'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(Text, unique=True)
    request_key: Mapped[str | None] = mapped_column(Text, unique=True)
    request_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    first_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OverduePayment(Base):
    __tablename__ = "overdue_payments"

    # database/schema.sql owns the existing constraints and partial index.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    rental_id: Mapped[int] = mapped_column(ForeignKey("rentals.id"))
    charge_ids: Mapped[list[int]] = mapped_column(JSONB)
    amount_cents: Mapped[int]
    checkout_params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checkout_session_id: Mapped[str | None] = mapped_column(Text, unique=True)
    payment_intent_id: Mapped[str | None] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
