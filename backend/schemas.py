from pydantic import BaseModel, EmailStr, Field, field_validator


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

    @field_validator("overdue_consent")
    @classmethod
    def require_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Please agree to the overdue terms.")
        return value
