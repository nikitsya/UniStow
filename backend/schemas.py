from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Literal


class Registration(BaseModel):
    email: EmailStr
    password: str = Field(min_length=15, max_length=128, repr=False)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class Login(Registration):
    password: str = Field(min_length=1, max_length=128, repr=False)


class RentalRequest(BaseModel):
    locker_id: int = Field(gt=0, strict=True)
    days: int = Field(ge=1, le=4, strict=True)
    overdue_consent: bool = Field(strict=True)
    payment_method_consent: bool = Field(default=False, strict=True)

    @field_validator("overdue_consent")
    @classmethod
    def require_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Please agree to the overdue terms.")
        return value


class UserResponse(BaseModel):
    id: int
    email: str


class LockerResponse(BaseModel):
    id: int
    number: int
    name: str
    size: str
    price_per_day_cents: int
    status: Literal["available", "unavailable", "offline"]
    door_state: Literal["open", "closed", "unknown"]
    last_seen_at: datetime | None


class RentalResponse(BaseModel):
    id: int
    user_id: int
    locker_id: int
    status: str
    prepaid_days: int
    total_price_cents: int
    reserved_until: datetime
    paid_until: datetime


class CheckoutResponse(BaseModel):
    url: str


class StatusResponse(BaseModel):
    status: str


class WebhookResponse(BaseModel):
    received: bool


class CommandResponse(BaseModel):
    status: Literal["queued", "opening", "test_acknowledged"]
    command_id: str


class HeartbeatResponse(BaseModel):
    locker_number: int
    door_state: Literal["open", "closed"]


class DeviceCommandResponse(BaseModel):
    id: str
    action: Literal["open", "finish"]
    valid_for_seconds: float


class CommandPollResponse(BaseModel):
    command: DeviceCommandResponse | None
