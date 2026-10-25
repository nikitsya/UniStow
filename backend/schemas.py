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
