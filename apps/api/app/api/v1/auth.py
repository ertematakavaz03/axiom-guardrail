from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.app.config import Settings, get_settings
from apps.api.app.db.models import User
from apps.api.app.db.session import get_session
from apps.api.app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from apps.api.app.security.auth import get_current_user
from apps.api.app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    return await AuthService(session, settings).register(payload)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    response = await AuthService(session, settings).login(payload)
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason_code": "INVALID_CREDENTIALS", "message": "Invalid email or password"},
        )
    return response


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    user = await session.scalar(
        select(User).options(selectinload(User.memberships)).where(User.id == current_user.id)
    )
    return UserResponse.model_validate(user)
