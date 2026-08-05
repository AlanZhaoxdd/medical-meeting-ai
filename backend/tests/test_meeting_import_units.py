from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.api.v1 import meeting_imports as api
from app.core.auth import AuthContext, require_role
from app.core.exceptions import ForbiddenError, NotFoundError
from app.main import app
from app.models.kb import MeetingImport, MeetingImportStatus
from app.schemas.kb import Role
from app.worker.celery_app import celery_app
from app.worker.meeting_import import extract_deterministic_metadata


def auth_context(role: Role = Role.EDITOR) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        organization_id=uuid4(),
        email="editor@example.com",
        display_name="编辑",
        role=role,
        token_version=0,
    )


def import_record(status: MeetingImportStatus) -> MeetingImport:
    now = datetime.now(timezone.utc)
    return MeetingImport(
        id=uuid4(),
        organization_id=uuid4(),
        knowledge_base_id=uuid4(),
        document_id=uuid4(),
        filename="meeting.txt",
        safe_filename="meeting.txt",
        mime_type="text/plain",
        sha256="a" * 64,
        size_bytes=10,
        status=status,
        current_step=status.value.lower(),
        metadata_json={},
        can_retry=status is MeetingImportStatus.FAILED,
        cancel_requested=False,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )


class FakeSession:
    def __init__(self, result: Any = None) -> None:
        self.result = result
        self.statement: Any = None
        self.added: list[Any] = []

    async def scalar(self, statement: Any) -> Any:
        self.statement = statement
        return self.result

    def add(self, item: Any) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        return None

    async def refresh(self, item: Any) -> None:
        return None


def test_config_uses_server_upload_limit_and_flattens_mime_types() -> None:
    config = api._config()

    assert config.max_upload_bytes > 0
    assert {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".json"} <= set(
        config.allowed_extensions
    )
    assert "application/pdf" in config.allowed_mime_types
    assert config.mime_types[".pdf"] == ["application/pdf"]
    assert api._advisory_lock_key("f" * 64) < 0
    assert api._advisory_lock_key("0" * 64) == 0


def test_existing_document_with_kb_state_cannot_be_reused_for_meeting_import() -> None:
    document = SimpleNamespace(status="UPLOADED")
    assert api._document_has_derived_state(
        document, has_ingestion_job=True, has_chunks=False
    )
    assert api._document_has_derived_state(
        document, has_ingestion_job=False, has_chunks=True
    )
    document.status = "PUBLISHED"
    assert api._document_has_derived_state(
        document, has_ingestion_job=False, has_chunks=False
    )


def test_statuses_map_to_real_progress_and_user_safe_failure() -> None:
    assert api.serialize_meeting_import(import_record(MeetingImportStatus.UPLOADED)).progress == 20
    assert api.serialize_meeting_import(import_record(MeetingImportStatus.PARSING)).progress == 55
    ready = api.serialize_meeting_import(import_record(MeetingImportStatus.READY_FOR_REVIEW))
    assert ready.progress == 100

    failed = import_record(MeetingImportStatus.FAILED)
    failed.failure_code = "parse_failed"
    failed.failure_message = "无法解析原件"
    serialized = api.serialize_meeting_import(failed)
    assert serialized.failure == {
        "code": "parse_failed",
        "message": "无法解析原件",
        "displayable": "无法解析原件",
    }


def test_metadata_extraction_is_deterministic_and_source_only() -> None:
    blocks = [
        {
            "block_id": "info",
            "block_type": "table",
            "page_number": 1,
            "text": "| 会议名称 | 季度医学会议 | 政策 |\n| 记录人 | 张三 | 政策 |",
        },
        {"block_type": "speech", "text": "讨论行动项", "speaker": "专家甲"},
        {"block_type": "speech", "text": "补充意见", "speaker": "专家甲"},
    ]

    metadata = extract_deterministic_metadata(blocks, filename="fallback.txt")

    assert metadata["title"] == "季度医学会议"
    assert metadata["recorder"] == "张三"
    assert metadata["title_source"] == [{"block_id": "info", "page_number": 1}]
    assert metadata["transcript_start_index"] == 1
    assert "participants" not in metadata
    assert "action_items" not in metadata


async def test_import_roles_and_organization_filter() -> None:
    editor_guard = require_role(Role.EDITOR)
    assert (await editor_guard(auth_context(Role.EDITOR))).role is Role.EDITOR
    with pytest.raises(ForbiddenError):
        await editor_guard(auth_context(Role.REVIEWER))
    with pytest.raises(ForbiddenError):
        await editor_guard(auth_context(Role.VIEWER))

    session = FakeSession()
    with pytest.raises(NotFoundError):
        await api._get_import(session, auth_context(), uuid4())  # type: ignore[arg-type]
    statement = str(session.statement)
    assert "meeting_imports.id" in statement
    assert "meeting_imports.organization_id" in statement


async def test_failed_import_can_retry_and_active_import_can_cancel(monkeypatch: Any) -> None:
    monkeypatch.setattr(api, "_dispatch_import", lambda _: None)
    current = auth_context()

    failed = import_record(MeetingImportStatus.FAILED)
    failed.organization_id = current.organization_id
    retry_session = FakeSession(failed)
    retried = await api.retry_meeting_import(
        failed.id, retry_session, current  # type: ignore[arg-type]
    )
    assert retried.status is MeetingImportStatus.UPLOADED
    assert retried.can_retry is False

    active = import_record(MeetingImportStatus.PARSING)
    active.organization_id = current.organization_id
    cancel_session = FakeSession(active)
    cancelled = await api.cancel_meeting_import(
        active.id, cancel_session, current  # type: ignore[arg-type]
    )
    assert cancelled.status is MeetingImportStatus.CANCELLED
    assert cancelled.can_retry is False


def test_openapi_registers_import_contract_and_authentication() -> None:
    schema = app.openapi()
    expected = {
        "/api/v1/meeting-imports/config",
        "/api/v1/meeting-imports",
        "/api/v1/meeting-imports/{import_id}",
        "/api/v1/meeting-imports/{import_id}/retry",
        "/api/v1/meeting-imports/{import_id}/cancel",
    }
    assert expected <= set(schema["paths"])
    assert schema["paths"]["/api/v1/meeting-imports"]["post"]["security"]
    assert celery_app.conf.beat_schedule["reconcile-stale-meeting-imports"]["task"] == (
        "app.worker.tasks.reconcile_meeting_imports"
    )
