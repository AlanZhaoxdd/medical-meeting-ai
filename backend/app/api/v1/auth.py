from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUserDependency
from app.core.config import get_settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.db.session import get_session
from app.models.kb import (
    Organization,
    OrganizationMembership,
    RefreshToken,
    User,
)
from app.schemas.kb import (
    CurrentUser,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    Role,
    TokenResponse,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["认证"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


async def _issue_tokens(
    session: AsyncSession, *, user: User, organization_id: UUID
) -> TokenResponse:
    settings = get_settings()
    raw_refresh, token_hash = create_refresh_token()
    now = datetime.now(timezone.utc)
    session.add(
        RefreshToken(
            token_hash=token_hash,
            user_id=user.id,
            organization_id=organization_id,
            token_version=user.token_version,
            created_at=now,
            expires_at=now + timedelta(days=settings.refresh_token_days),
        )
    )
    await session.commit()
    return TokenResponse(
        access_token=create_access_token(
            user_id=str(user.id),
            organization_id=str(organization_id),
            token_version=user.token_version,
        ),
        refresh_token=raw_refresh,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: SessionDependency) -> TokenResponse:
    user = User(
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        status="active",
        token_version=0,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("email_already_registered", "该邮箱已注册") from exc
    organization = Organization(
        name=payload.organization_name or f"{payload.display_name}的组织",
        status="active",
        created_by=user.id,
    )
    session.add(organization)
    await session.flush()
    session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            role=Role.OWNER.value,
            status="active",
        )
    )
    await record_audit(
        session,
        organization_id=organization.id,
        actor_id=user.id,
        action="auth.register",
        resource_type="user",
        resource_id=str(user.id),
    )
    await session.commit()
    return await _issue_tokens(session, user=user, organization_id=organization.id)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: SessionDependency) -> TokenResponse:
    user = await session.scalar(
        select(User).where(User.email == payload.email.strip().lower(), User.status == "active")
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("邮箱或密码错误")
    membership = await session.scalar(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.status == "active",
        )
        .order_by(OrganizationMembership.created_at)
    )
    if membership is None:
        raise UnauthorizedError("用户尚未加入组织")
    await record_audit(
        session,
        organization_id=membership.organization_id,
        actor_id=user.id,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
    )
    return await _issue_tokens(
        session, user=user, organization_id=membership.organization_id
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, session: SessionDependency) -> TokenResponse:
    now = datetime.now(timezone.utc)
    token = await session.scalar(
        select(RefreshToken)
        .where(
            RefreshToken.token_hash == hash_refresh_token(payload.refresh_token),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        .with_for_update()
    )
    if token is None:
        raise UnauthorizedError("刷新令牌无效或已撤销")
    token.revoked_at = now
    user = await session.scalar(
        select(User).where(User.id == token.user_id, User.status == "active")
    )
    if user is None or user.token_version != token.token_version:
        raise UnauthorizedError()
    await session.commit()
    return await _issue_tokens(
        session, user=user, organization_id=token.organization_id
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: LogoutRequest, session: SessionDependency) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(payload.refresh_token))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await session.commit()


@router.get("/me", response_model=CurrentUser)
async def me(current: CurrentUserDependency) -> CurrentUser:
    return CurrentUser(
        id=str(current.user_id),
        email=current.email,
        display_name=current.display_name,
        organization_id=str(current.organization_id),
        role=current.role,
    )
