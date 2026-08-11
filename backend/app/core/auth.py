from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.kb import KnowledgeBase, OrganizationMembership, User
from app.schemas.kb import Role

bearer = HTTPBearer(auto_error=False)
SessionDependency = Annotated[AsyncSession, Depends(get_session)]

ROLE_LEVEL = {
    Role.VIEWER: 10,
    Role.REVIEWER: 20,
    Role.EDITOR: 30,
    Role.ADMIN: 40,
    Role.OWNER: 50,
}


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    organization_id: UUID
    email: str
    display_name: str
    role: Role
    token_version: int


async def get_current_user(
    session: SessionDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthContext:
    if credentials is None:
        raise UnauthorizedError()
    return await _authenticate(session, credentials)


async def get_optional_current_user(
    session: SessionDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthContext | None:
    if credentials is None:
        return None
    return await _authenticate(session, credentials)


async def _authenticate(
    session: AsyncSession, credentials: HTTPAuthorizationCredentials
) -> AuthContext:
    payload = decode_access_token(credentials.credentials)
    try:
        user_id = UUID(str(payload.get("sub")))
        organization_id = UUID(str(payload.get("org")))
    except (TypeError, ValueError) as exc:
        raise UnauthorizedError() from exc
    statement = (
        select(User, OrganizationMembership)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(
            User.id == user_id,
            User.status == "active",
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
        )
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise UnauthorizedError()
    user, membership = row
    if user.token_version != int(payload.get("ver", -1)):
        raise UnauthorizedError()
    return AuthContext(
        user_id=user.id,
        organization_id=organization_id,
        email=user.email,
        display_name=user.display_name,
        role=Role(membership.role),
        token_version=user.token_version,
    )


CurrentUserDependency = Annotated[AuthContext, Depends(get_current_user)]
OptionalCurrentUserDependency = Annotated[
    AuthContext | None, Depends(get_optional_current_user)
]


def require_role(
    minimum: Role,
) -> Callable[[AuthContext], Awaitable[AuthContext]]:
    async def dependency(current: CurrentUserDependency) -> AuthContext:
        if ROLE_LEVEL[current.role] < ROLE_LEVEL[minimum]:
            raise ForbiddenError()
        return current

    return dependency


def require_exact_role(
    required: Role,
) -> Callable[[AuthContext], Awaitable[AuthContext]]:
    """Require one exact role for administration-only endpoints.

    ``require_role`` intentionally implements the existing role hierarchy, so
    an owner also satisfies an admin minimum. Settings are deliberately a
    separate permission boundary: the owner-facing account is a minutes
    editor and must not inherit IT administration access.
    """

    async def dependency(current: CurrentUserDependency) -> AuthContext:
        if current.role != required:
            raise ForbiddenError()
        return current

    return dependency


async def require_kb_access(
    session: AsyncSession,
    current: AuthContext,
    kb_id: UUID,
) -> KnowledgeBase:
    statement = select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.organization_id == current.organization_id,
    )
    # Minutes editors retain business access to existing/import-linked KBs.
    # IT admin remains the only role that can reach the settings surface.
    if current.role not in {Role.EDITOR, Role.OWNER}:
        statement = statement.where(KnowledgeBase.deleted_at.is_(None))
    kb = await session.scalar(statement)
    if kb is None:
        raise ForbiddenError("知识库不存在或无权访问")
    return kb
