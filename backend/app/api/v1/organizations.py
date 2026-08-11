from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_exact_role
from app.core.exceptions import AppException, ConflictError, NotFoundError
from app.db.session import get_session
from app.models.kb import OrganizationMembership, User
from app.schemas.kb import MemberCreate, MemberRead, MemberUpdate, Role
from app.services.audit import record_audit

router = APIRouter(prefix="/organizations/current/members", tags=["组织成员"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
AdminDependency = Annotated[AuthContext, Depends(require_exact_role(Role.ADMIN))]


def serialize_member(user: User, membership: OrganizationMembership) -> MemberRead:
    return MemberRead(
        user_id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=Role(membership.role),
        status=membership.status,
        created_at=membership.created_at,
    )


@router.get("", response_model=list[MemberRead])
async def list_members(
    session: SessionDependency, current: AdminDependency
) -> list[MemberRead]:
    rows = (
        await session.execute(
            select(User, OrganizationMembership)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(
                OrganizationMembership.organization_id == current.organization_id,
                OrganizationMembership.status == "active",
            )
            .order_by(OrganizationMembership.created_at)
        )
    ).all()
    return [serialize_member(user, membership) for user, membership in rows]


@router.post("", response_model=MemberRead, status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: MemberCreate,
    session: SessionDependency,
    current: AdminDependency,
) -> MemberRead:
    user = await session.scalar(
        select(User).where(User.email == payload.email.strip().lower(), User.status == "active")
    )
    if user is None:
        raise NotFoundError("已注册用户", "user_not_found")
    membership = OrganizationMembership(
        organization_id=current.organization_id,
        user_id=user.id,
        role=payload.role.value,
        status="active",
    )
    session.add(membership)
    try:
        await record_audit(
            session,
            organization_id=current.organization_id,
            actor_id=current.user_id,
            action="member.add",
            resource_type="user",
            resource_id=str(user.id),
            metadata={"role": payload.role.value},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("membership_exists", "用户已是组织成员") from exc
    await session.refresh(membership)
    return serialize_member(user, membership)


@router.patch("/{user_id}", response_model=MemberRead)
async def update_member(
    user_id: UUID,
    payload: MemberUpdate,
    session: SessionDependency,
    current: AdminDependency,
) -> MemberRead:
    membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == current.organization_id,
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.status == "active",
        )
    )
    user = await session.get(User, user_id)
    if membership is None or user is None:
        raise NotFoundError("组织成员", "membership_not_found")
    if membership.role == Role.OWNER.value and current.role != Role.OWNER:
        raise AppException(403, "owner_change_forbidden", "只有 owner 可以变更 owner 角色")
    membership.role = payload.role.value
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action="member.role_change",
        resource_type="user",
        resource_id=str(user_id),
        metadata={"role": payload.role.value},
    )
    await session.commit()
    return serialize_member(user, membership)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: UUID,
    session: SessionDependency,
    current: AdminDependency,
) -> Response:
    membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == current.organization_id,
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.status == "active",
        )
    )
    if membership is None:
        raise NotFoundError("组织成员", "membership_not_found")
    if membership.role == Role.OWNER.value:
        raise AppException(409, "owner_cannot_be_removed", "不能移除组织 owner")
    membership.status = "removed"
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action="member.remove",
        resource_type="user",
        resource_id=str(user_id),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
