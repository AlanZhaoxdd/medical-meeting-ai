from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.export import (
    ExportFileFormat,
    ExportRecord,
    ExportStatus,
    ExportType,
)
from app.models.kb import OutboxEvent


async def create_export_record(
    session: AsyncSession,
    *,
    organization_id: UUID,
    meeting_id: UUID,
    analysis_version: int,
    export_type: ExportType,
    file_format: ExportFileFormat | None,
    config: dict[str, Any],
    created_by: UUID | None,
    file_name: str | None = None,
) -> ExportRecord:
    record = ExportRecord(
        organization_id=organization_id,
        meeting_id=meeting_id,
        analysis_version=analysis_version,
        export_type=export_type,
        file_format=file_format,
        status=ExportStatus.PENDING,
        current_stage="pending",
        progress=0,
        config=config,
        created_by=created_by,
        file_name=file_name,
    )
    session.add(record)
    await session.flush()
    session.add(
        OutboxEvent(
            idempotency_key=f"export:{record.id}",
            event_type="export.requested",
            aggregate_id=str(meeting_id),
            payload={
                "export_id": str(record.id),
                "meeting_id": str(meeting_id),
                "analysis_version": analysis_version,
            },
            status="PENDING",
        )
    )
    await session.commit()
    await session.refresh(record)
    return record


async def claim_export_record(
    session: AsyncSession, export_id: UUID, *, lease_seconds: int = 1800
) -> ExportRecord | None:
    from uuid import uuid4

    token = uuid4()
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(ExportRecord)
        .where(
            ExportRecord.id == export_id,
            ExportRecord.status.in_([ExportStatus.PENDING, ExportStatus.ANALYZING]),
        )
        .values(
            status=ExportStatus.ANALYZING,
            attempt_token=token,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            started_at=now,
            current_stage="analyzing",
            progress=5,
            message="正在准备导出内容",
            error_code=None,
            error_message=None,
        )
    )
    if result.rowcount != 1:
        return await session.get(ExportRecord, export_id)
    await session.commit()
    return await session.get(ExportRecord, export_id)


async def update_export_progress(
    session: AsyncSession,
    *,
    export_id: UUID,
    stage: str,
    progress: int,
    message: str,
    status: ExportStatus | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    completed: bool = False,
    file_name: str | None = None,
    storage_key: str | None = None,
    content_type: str | None = None,
    file_size: int | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {
        "current_stage": stage,
        "progress": progress,
        "message": message,
        "updated_at": now,
        "lease_expires_at": now + timedelta(minutes=30),
    }
    if status is not None:
        values["status"] = status
    if error_code is not None:
        values["error_code"] = error_code
    if error_message is not None:
        values["error_message"] = error_message[:2000]
    if completed:
        values["completed_at"] = now
        values["progress"] = 100
    if file_name is not None:
        values["file_name"] = file_name
    if storage_key is not None:
        values["storage_key"] = storage_key
    if content_type is not None:
        values["content_type"] = content_type
    if file_size is not None:
        values["file_size"] = file_size
    await session.execute(
        update(ExportRecord).where(ExportRecord.id == export_id).values(**values)
    )
    await session.commit()


async def get_export_record(session: AsyncSession, export_id: UUID) -> ExportRecord:
    record = await session.get(ExportRecord, export_id)
    if record is None:
        raise NotFoundError("导出记录", "export_not_found")
    return record


async def list_export_records(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    organization_id: UUID,
    page: int,
    page_size: int,
) -> tuple[list[ExportRecord], int]:
    base = (
        select(ExportRecord)
        .where(
            ExportRecord.meeting_id == meeting_id,
            ExportRecord.organization_id == organization_id,
        )
        .order_by(ExportRecord.created_at.desc(), ExportRecord.id.desc())
    )
    total = int(
        await session.scalar(
            select(func.count()).select_from(base.order_by(None).subquery())
        )
        or 0
    )
    items = list(
        (await session.scalars(base.offset((page - 1) * page_size).limit(page_size))).all()
    )
    return items, total


async def retry_export_record(session: AsyncSession, export_id: UUID) -> ExportRecord:
    record = await session.get(ExportRecord, export_id, with_for_update=True)
    if record is None:
        raise NotFoundError("导出记录", "export_not_found")
    if record.status not in {ExportStatus.FAILED, ExportStatus.CANCELLED}:
        raise ConflictError("export_not_retryable", "当前状态不可重试")
    record.status = ExportStatus.PENDING
    record.current_stage = "pending"
    record.progress = 0
    record.error_code = None
    record.error_message = None
    record.completed_at = None
    record.retry_count += 1
    session.add(
        OutboxEvent(
            idempotency_key=f"export-retry:{record.id}:{record.retry_count}",
            event_type="export.requested",
            aggregate_id=str(record.meeting_id),
            payload={"export_id": str(record.id), "meeting_id": str(record.meeting_id)},
            status="PENDING",
        )
    )
    await session.commit()
    return record


async def cancel_export_record(session: AsyncSession, export_id: UUID) -> ExportRecord:
    record = await session.get(ExportRecord, export_id, with_for_update=True)
    if record is None:
        raise NotFoundError("导出记录", "export_not_found")
    if record.status in {
        ExportStatus.COMPLETED,
        ExportStatus.CANCELLED,
        ExportStatus.FAILED,
    }:
        raise ConflictError("export_not_cancellable", "当前状态不可取消")
    record.status = ExportStatus.CANCELLED
    record.message = "导出任务已取消"
    record.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return record
