from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb import AuditEvent


async def record_audit(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            organization_id=organization_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata or {},
        )
    )
