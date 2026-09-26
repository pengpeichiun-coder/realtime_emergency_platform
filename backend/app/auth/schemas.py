from datetime import datetime
from typing import Annotated
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from app.context import Role


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Input):
    email: EmailStr
    password: Annotated[SecretStr, Field(min_length=12, max_length=128)]

    @field_validator("email", mode="before")
    @classmethod
    def normalize(cls, value):
        return value.strip().casefold() if isinstance(value, str) else value


class Register(Credentials):
    organization_name: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)


class UserCreate(Credentials):
    display_name: str = Field(min_length=1, max_length=120)
    role: Role


class RoleUpdate(Input):
    role: Role


class ActiveUpdate(Input):
    is_active: bool


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    email: str
    display_name: str
    role: Role
    is_active: bool
    can_reset_demo: bool = False
    created_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
