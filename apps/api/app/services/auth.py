from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.app.config import Settings
from apps.api.app.db.models import MemberRole, Organization, OrganizationMember, User
from apps.api.app.errors import ConflictError
from apps.api.app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from apps.api.app.security.auth import create_access_token, hash_password, verify_password
from apps.api.app.services.audit import add_audit


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def register(self, payload: RegisterRequest) -> TokenResponse:
        normalized_email = payload.email.lower()
        if await self.session.scalar(select(User.id).where(User.email == normalized_email)):
            raise ConflictError("An account with this email already exists")
        organization = Organization(name=payload.organization_name)
        user = User(email=normalized_email, password_hash=hash_password(payload.password))
        self.session.add_all([organization, user])
        await self.session.flush()
        membership = OrganizationMember(organization=organization, user=user, role=MemberRole.OWNER)
        self.session.add(membership)
        add_audit(
            self.session,
            organization_id=organization.id,
            user_id=user.id,
            project_id=None,
            action="auth.register",
            resource_type="user",
            resource_id=user.id,
        )
        await self.session.commit()
        registered_user = await self.session.scalar(
            select(User).options(selectinload(User.memberships)).where(User.id == user.id)
        )
        if registered_user is None:  # pragma: no cover - guarded by the committed insert
            raise RuntimeError("Registered user could not be reloaded")
        return self._token_response(registered_user)

    async def login(self, payload: LoginRequest) -> TokenResponse | None:
        user = await self.session.scalar(
            select(User)
            .options(selectinload(User.memberships))
            .where(User.email == payload.email.lower())
        )
        if user is None or not verify_password(payload.password, user.password_hash):
            return None
        return self._token_response(user)

    def _token_response(self, user: User) -> TokenResponse:
        token, expires_in = create_access_token(user.id, self.settings)
        return TokenResponse(
            access_token=token,
            expires_in=expires_in,
            user=UserResponse.model_validate(user),
        )
