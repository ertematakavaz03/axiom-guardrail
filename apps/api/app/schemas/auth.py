from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from apps.api.app.db.models import MemberRole
from apps.api.app.schemas.common import ORMModel


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    organization_name: str = Field(min_length=2, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class MembershipResponse(ORMModel):
    organization_id: uuid.UUID
    role: MemberRole


class UserResponse(ORMModel):
    id: uuid.UUID
    email: str
    created_at: datetime
    memberships: list[MembershipResponse] = []


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
