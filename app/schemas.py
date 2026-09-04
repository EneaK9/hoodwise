from typing import Literal

from pydantic import BaseModel, Field


class SignupIn(BaseModel):
    email: str
    password: str = Field(min_length=10, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


class ChatIn(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    variant_id: str | None = None
    vin: str | None = None


class VinIn(BaseModel):
    vin: str = Field(min_length=17, max_length=17)


class VinFuelIn(BaseModel):
    vin: str = Field(min_length=17, max_length=17)
    fuel: Literal["diesel", "gasoline"]


class VehiclePickIn(BaseModel):
    variant_id: str
    vin: str | None = None
    nickname: str | None = Field(default=None, max_length=80)


class SessionClaimIn(BaseModel):
    session_id: str
