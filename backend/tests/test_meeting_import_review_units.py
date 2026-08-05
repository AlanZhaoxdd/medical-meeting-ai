from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from docx import Document as WordDocument
from pydantic import ValidationError

from app.api.v1 import meeting_imports as api
from app.core.auth import AuthContext
from app.core.exceptions import AppException, ConflictError, NotFoundError
from app.main import app
from app.models.kb import (
    DocumentBlock,
    IngestionJob,
    MeetingImport,
    MeetingImportStatus,
    TranscriptRevision,
    TranscriptRevisionBlock,
    TranscriptRevisionStatus,
)
from app.models.meeting import Meeting
from app.schemas.kb import Role
from app.schemas.meeting_import import ConfirmRequest, MeetingMetadataPatch
from app.worker.meeting_import import extract_deterministic_metadata, prepare_transcript_blocks
from app.worker.parser import _docx_table_rows, clean_table_markdown, clean_transcript_text


def _item(metadata: dict[str, Any] | None = None) -> MeetingImport:
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
        size_bytes=1,
        status=MeetingImportStatus.READY_FOR_REVIEW,
        current_step="ready_for_review",
        metadata_json=metadata or {},
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )


def test_chinese_literal_matching_is_not_regex() -> None:
    assert api._match_positions("医学(会议) 医学", "医学(", False) == [(0, 3)]


def test_parser_removes_docling_rich_cell_markers_from_tables() -> None:
    raw = "| <!-- rich cell -->   |   政策指引   |\n|---|---|"
    assert clean_table_markdown(raw) == "|  | 政策指引 |\n|---|---|"
    assert clean_transcript_text("说明 <!-- rich cell --> 内容") == "说明 内容"


def test_parser_normalizes_invisible_spaces_breaks_and_line_endings() -> None:
    assert clean_transcript_text("\ufeff第一行\u00a0 内容\r\n\u200b第二行") == (
        "第一行 内容\n第二行"
    )


def test_python_docx_preserves_merged_table_label_and_value_cells(tmp_path: Path) -> None:
    document = WordDocument()
    table = document.add_table(rows=2, cols=4)
    table.rows[0].cells[0].text = "会议信息"
    table.rows[0].cells[3].text = "政策指引"
    table.rows[1].cells[0].text = "会议名称："
    table.rows[1].cells[1].text = "区域专家顾问会"
    table.rows[1].cells[1].merge(table.rows[1].cells[2])
    table.rows[1].cells[3].text = "不要提取"
    path = tmp_path / "meeting.docx"
    document.save(path)

    rows = _docx_table_rows(str(path))[0]

    assert rows[1] == ["会议名称：", "区域专家顾问会", "区域专家顾问会", "不要提取"]
    assert clean_table_markdown("| 会议目的 | 第一行<br>第二行 |") == (
        "| 会议目的 | 第一行 第二行 |"
    )


def test_english_matching_defaults_case_insensitive() -> None:
    assert api._match_positions("Meeting MEETING", "meeting", False) == [(0, 7), (8, 15)]


def test_case_sensitive_matching_only_matches_exact_case() -> None:
    assert api._match_positions("Meeting meeting", "meeting", True) == [(8, 15)]


def test_position_index_can_select_second_occurrence() -> None:
    positions = api._match_positions("a a a", "a", False)
    assert positions[1] == (2, 3)


def test_scope_is_literal_and_block_selector_is_independent() -> None:
    assert api._match_positions("a+b", "a+", False) == [(0, 2)]


def test_metadata_requires_title_and_timezone_aware_ordered_times() -> None:
    with pytest.raises(AppException, match="标题"):
        api._validate_meeting_values({"title": "", "starts_at": datetime.now(timezone.utc)})
    starts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(AppException, match="时区"):
        api._validate_meeting_values(
            {
                "title": "M",
                "starts_at": starts.replace(tzinfo=None),
                "ends_at": starts + timedelta(hours=1),
            }
        )
    with pytest.raises(AppException, match="晚于"):
        api._validate_meeting_values({"title": "M", "starts_at": starts, "ends_at": starts})


def test_extracted_meeting_date_supplies_full_day_compatibility_times() -> None:
    values = {"title": "M", "meeting_date": "2024年03月16日"}

    api._validate_meeting_values(values)

    assert values["starts_at"].isoformat() == "2024-03-16T00:00:00+00:00"
    assert values["ends_at"].isoformat() == "2024-03-17T00:00:00+00:00"


def test_meeting_date_removes_legacy_time_suggestions_and_confirmation() -> None:
    item = _item(
        {
            "title": "会议",
            "meeting_date": "2024年03月16日",
            "starts_at": "2026-08-03T00:00:00+00:00",
            "ends_at": "2026-08-04T00:00:00+00:00",
        }
    )

    fields, _, _ = api._metadata_fields(item)

    assert fields["starts_at"]["suggested_value"] is None
    assert fields["ends_at"]["suggested_value"] is None
    assert fields["starts_at"]["needs_confirmation"] is False
    assert fields["ends_at"]["needs_confirmation"] is False


def test_metadata_confidence_label_and_real_source_are_preserved() -> None:
    item = _item(
        {
            "title": "AI title",
            "title_source": [{"block_id": "b1"}],
            "title_confidence_label": "高置信度",
        }
    )
    fields, _, _ = api._metadata_fields(item)
    assert fields["title"]["confidence_label"] == "高置信度"
    assert fields["title"]["source"] == [{"block_id": "b1"}]


def test_metadata_clear_is_explicit_and_accepted_suggestion_is_not_modified() -> None:
    item = _item({"title": "AI title", "location": "Room A"})
    accepted = MeetingMetadataPatch(expected_version=1, title="AI title")
    api._apply_metadata(item, accepted)
    fields, _, _ = api._metadata_fields(item)
    assert fields["title"]["user_modified"] is False
    cleared = MeetingMetadataPatch(expected_version=2, location=None)
    api._apply_metadata(item, cleared)
    fields, _, _ = api._metadata_fields(item)
    assert fields["location"]["value"] is None
    assert fields["location"]["user_modified"] is True


def test_confirmed_metadata_does_not_mark_accepted_ai_suggestions_modified() -> None:
    item = _item({"title": "AI title"})
    meeting = type(
        "MeetingStub",
        (),
        {
            "title": "AI title",
            "starts_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "ends_at": datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            "location": None,
            "online_url": None,
            "organizer": None,
            "topic": None,
            "description": None,
        },
    )()
    fields, _, _ = api._metadata_fields(item, meeting)  # type: ignore[arg-type]
    assert fields["title"]["user_modified"] is False


def test_deterministic_title_is_not_guessed_from_heading_or_filename() -> None:
    result = extract_deterministic_metadata(
        [{"block_id": "h1", "block_type": "heading", "text": "Title", "page_number": 2}],
        filename="fallback.txt",
    )
    assert result["title"] is None
    assert result["title_source"] == []
    assert result["title_confidence_label"] == "无法可靠识别"


def test_deterministic_metadata_extracts_meeting_info_and_transcript_boundary() -> None:
    blocks = [
        {
            "block_id": "info",
            "block_type": "table",
            "text": (
                "| 会议名称 | 区域专家顾问会 |\n"
                "| 会议目的 | 讨论临床实践 |\n"
                "| 讨论题目 | CKM 综合管理 |\n"
                "| 会议日期 | 2026年08月10日 |\n"
                "| 顾问选择标准 | 专业影响力 |\n"
                "| 参会顾问姓名 | 李医生、王医生 |\n"
                "| 诺和诺德内部参会人及参会原因 | XLH：会议组织 |\n"
                "| 记录人 | 张三 |"
            ),
        },
        {"block_id": "discussion", "block_type": "heading", "text": "具体讨论内容"},
        {"block_id": "body", "block_type": "paragraph", "text": "正文内容"},
    ]
    metadata = extract_deterministic_metadata(blocks, filename="fallback.txt")

    assert metadata["title"] == "区域专家顾问会"
    assert metadata["meeting_purpose"] == "讨论临床实践"
    assert metadata["discussion_topics"] == "CKM 综合管理"
    assert metadata["advisor_selection_criteria"] == "专业影响力"
    assert metadata["advisor_names"] == "李医生、王医生"
    assert metadata["internal_attendees"] == "XLH：会议组织"
    assert metadata["recorder"] == "张三"
    assert metadata["transcript_start_index"] == 2
    assert "participants" not in metadata
    assert "topics" not in metadata
    assert "action_items" not in metadata


def test_three_column_first_page_table_uses_only_value_column_and_first_match() -> None:
    blocks = [
        {
            "block_id": "info",
            "block_type": "table",
            "page_number": 1,
            "text": "Docling flattened text that must not win",
            "table_markdown": (
                "| 会议信息 |  | 政策指引 |\n"
                "|---|---|---|\n"
                "| 会议名称 | 区域专家顾问会 | 每场会议主题相符 |\n"
                "| 会议目的 | <!-- rich cell -->明确目标 | 不应提取的政策内容 |\n"
                "| 讨论题目 | 第一行<br>第二行 | 不应进入正文 |\n"
                "| 会议日期 | 2026年08月10日 |  |\n"
                "| 顾问选择标准 | 专业影响力 | 政策 |\n"
                "| 参会顾问姓名 | 李医生、王医生 | 5-10人 |\n"
                "| 诺和诺德内部参会人及参会原因 | XLH：会议组织 | 内部政策 |\n"
                "| 记录人 | 张三 | 应为会议组织者 |"
            ),
        },
        {
            "block_id": "later-info",
            "block_type": "table",
            "page_number": 2,
            "table_markdown": "| 会议名称 | 错误覆盖值 | 政策 |\n| 记录人 | 李四 | 政策 |",
        },
        {"block_id": "discussion", "block_type": "heading", "text": "会议具体讨论内容："},
        {"block_id": "body", "block_type": "paragraph", "text": "正文"},
    ]

    metadata = extract_deterministic_metadata(blocks, filename="meeting.docx")

    assert metadata["title"] == "区域专家顾问会"
    assert metadata["meeting_purpose"] == "明确目标"
    assert metadata["discussion_topics"] == "第一行 第二行"
    assert metadata["recorder"] == "张三"
    assert metadata["title_source"] == [{"block_id": "info", "page_number": 1}]
    assert metadata["transcript_start_index"] == 3
    assert [block["text"] for block in blocks[metadata["transcript_start_index"] :]] == [
        "正文"
    ]
    assert "政策" not in " ".join(
        str(metadata.get(key) or "") for key in (
            "title",
            "meeting_purpose",
            "discussion_topics",
            "meeting_date",
            "advisor_selection_criteria",
            "advisor_names",
            "internal_attendees",
            "recorder",
        )
    )


def test_lossless_docx_rows_restore_merged_labels_and_aggregate_internal_attendees() -> None:
    blocks = [
        {
            "block_id": "info",
            "block_type": "table",
            "text": "|  | 会议 | 会议 | 政策 |",
            "table_rows": [
                ["会议信息", "", "", "政策指引"],
                ["会议名称：", "区域专家顾问会", "区域专家顾问会", "政策一"],
                ["会议目的：", "明确临床需求", "明确临床需求", "政策二"],
                ["讨论题目：", "主题一\n主题二", "主题一\n主题二", "政策三"],
                ["会议日期：", "2026年08月10日", "2026年08月10日", ""],
                ["顾问选择标准：", "专业影响力", "专业影响力", "政策四"],
                ["参会顾问姓名：", "李医生、王医生", "李医生、王医生", "5-10人"],
                [
                    "诺和诺德内部参会人（Initial）以及参会原因：",
                    "TGD",
                    "会议组织",
                    "内部政策",
                ],
                [
                    "诺和诺德内部参会人（Initial）以及参会原因：",
                    "QIHG",
                    "医学答疑",
                    "内部政策",
                ],
                ["记录人：", "张三", "张三", "应为会议组织者"],
            ],
        },
        {"block_id": "body", "block_type": "paragraph", "text": "正文"},
    ]

    metadata = extract_deterministic_metadata(blocks, filename="meeting.docx")

    assert metadata["title"] == "区域专家顾问会"
    assert metadata["meeting_purpose"] == "明确临床需求"
    assert metadata["discussion_topics"] == "主题一\n主题二"
    assert metadata["meeting_date"] == "2026年08月10日"
    assert metadata["advisor_selection_criteria"] == "专业影响力"
    assert metadata["advisor_names"] == "李医生、王医生"
    assert metadata["internal_attendees"] == "TGD：会议组织\nQIHG：医学答疑"
    assert metadata["recorder"] == "张三"
    assert "政策" not in "\n".join(
        str(metadata[key])
        for key in (
            "title",
            "meeting_purpose",
            "discussion_topics",
            "meeting_date",
            "advisor_selection_criteria",
            "advisor_names",
            "internal_attendees",
            "recorder",
        )
    )
    assert metadata["transcript_start_index"] == 1


def test_transcript_tables_drop_policy_column_without_mutating_source() -> None:
    source = {
        "block_id": "body-table",
        "block_type": "table",
        "order": 1,
        "text": "original complete table",
        "table_markdown": "original complete table",
        "table_rows": [
            ["日程，发言人及具体讨论内容", "政策指引"],
            ["09:00 正文内容", "不进入纪要正文"],
        ],
        "content_hash": "a" * 64,
    }

    prepared = prepare_transcript_blocks([source], transcript_start_index=0)

    assert len(prepared) == 1
    assert "正文内容" in prepared[0]["text"]
    assert "政策指引" not in prepared[0]["text"]
    assert "不进入纪要正文" not in prepared[0]["text"]
    assert prepared[0]["content_hash"] != source["content_hash"]
    assert source["text"] == "original complete table"


def test_transcript_boundary_falls_back_to_after_meeting_info_table() -> None:
    blocks = [
        {
            "block_id": "info",
            "block_type": "table",
            "text": "| 会议名称 | 会议 | 政策 |\n| 记录人 | 张三 | 政策 |",
        },
        {"block_id": "body", "block_type": "paragraph", "text": "正文"},
    ]
    metadata = extract_deterministic_metadata(blocks, filename="meeting.docx")
    assert metadata["transcript_start_index"] == 1


@pytest.mark.parametrize("heading", ["具体讨论内容", "讨论内容如下", "具体讨论纪要"])
def test_similar_short_discussion_headings_start_the_body(heading: str) -> None:
    blocks = [
        {"block_id": "intro", "block_type": "paragraph", "text": "前言"},
        {"block_id": "marker", "block_type": "heading", "text": heading},
        {"block_id": "body", "block_type": "paragraph", "text": "正文"},
    ]
    metadata = extract_deterministic_metadata(blocks, filename="meeting.txt")
    assert metadata["transcript_start_index"] == 2


def test_discussion_topic_label_is_not_a_body_boundary() -> None:
    blocks = [
        {"block_id": "topic", "block_type": "heading", "text": "讨论题目"},
        {"block_id": "body", "block_type": "paragraph", "text": "正文"},
    ]
    metadata = extract_deterministic_metadata(blocks, filename="meeting.txt")
    assert metadata["transcript_start_index"] == 0


def test_revision_history_exposes_version_number_and_audit_fields() -> None:
    assert {"version", "revision_number", "created_by", "confirmed_by", "confirmed_at"} <= set(
        api.RevisionRead.model_fields
    )
    assert TranscriptRevisionStatus.CONFIRMED.value == "CONFIRMED"


def test_original_document_block_and_draft_block_are_separate_rows() -> None:
    document_block = DocumentBlock(
        document_id=uuid4(),
        block_id="b1",
        block_type="paragraph",
        order=0,
        text="original",
        content_hash="a" * 64,
    )
    draft_block = TranscriptRevisionBlock(
        revision_id=uuid4(),
        block_id="b1",
        block_type="paragraph",
        order=0,
        text="edited",
        content_hash="b" * 64,
    )
    assert document_block.text != draft_block.text
    assert document_block.block_id == draft_block.block_id


def test_confirmed_graph_path_reads_revision_blocks_only_when_active() -> None:
    source = inspect.getsource(
        __import__("app.worker.graph", fromlist=["_build_chunks"])._build_chunks
    )
    assert "TranscriptRevisionBlock" in source
    assert 'TranscriptRevision.status == "CONFIRMED"' in source


def test_review_worker_persists_only_source_blocks_and_draft_revision() -> None:
    """The preview worker must not accidentally become a RAG ingestion path."""
    source = inspect.getsource(
        __import__("app.worker.meeting_import", fromlist=["run_meeting_import_async"])
        .run_meeting_import_async
    )
    assert "DocumentBlock(" in source
    assert "TranscriptRevision(" in source
    assert "TranscriptRevisionBlock(" in source
    assert "Chunk(" not in source
    assert "KnowledgeItem(" not in source
    assert "Embedding(" not in source
    assert "build_chunks" not in source
    assert "no chunks, embeddings or knowledge drafts exist" in source


def test_confirmed_job_starts_at_extract_knowledge() -> None:
    item = _item()
    document = SimpleNamespace(
        id=item.document_id,
        active_transcript_revision_id=uuid4(),
    )

    class Session:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def add(self, value: Any) -> None:
            self.added.append(value)

    session = Session()
    job = api._enqueue_confirmed_job(session, item, document)  # type: ignore[arg-type]
    assert isinstance(job, IngestionJob)
    assert job.current_node == "extract_knowledge"
    assert job.status == "QUEUED"
    assert job.result_summary == {
        "source": "confirmed_transcript_revision",
        "revision_id": str(document.active_transcript_revision_id),
    }
    assert session.added == [job]


async def test_confirmation_survives_enqueue_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker outage leaves a durable queued job without rolling back the Meeting."""
    full_discussion_topics = "长讨论题目" * 80
    item = _item(
        {
            "title": "Confirmed meeting",
            "starts_at": "2026-01-01T00:00:00+00:00",
            "ends_at": "2026-01-01T01:00:00+00:00",
            "meeting_purpose": "确认治疗路径",
            "discussion_topics": full_discussion_topics,
            "meeting_date": "2026年01月01日 08:00-09:00",
            "advisor_selection_criteria": "相关领域专家",
            "advisor_names": "李医生、王医生",
            "internal_attendees": "XLH：会议组织",
            "recorder": "张三",
        }
    )
    revision = TranscriptRevision(
        id=uuid4(),
        document_id=item.document_id,
        import_id=item.id,
        version=1,
        status=TranscriptRevisionStatus.DRAFT,
        created_by=item.created_by,
    )
    document = SimpleNamespace(
        id=item.document_id,
        active_transcript_revision_id=None,
        meeting_id=None,
        vector_sync_status="SYNCED",
    )

    async def get_import(*_: Any, **__: Any) -> MeetingImport:
        return item

    async def current_draft(*_: Any, **__: Any) -> TranscriptRevision:
        return revision

    monkeypatch.setattr(api, "_get_import", get_import)
    monkeypatch.setattr(api, "_current_draft", current_draft)

    class Begin:
        async def __aenter__(self) -> "Begin":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

    class Session:
        def __init__(self) -> None:
            self.added: list[Any] = []
            self.scalar_calls = 0
            self.commit_count = 0
            self.rollback_count = 0

        async def scalar(self, _: Any) -> Any:
            self.scalar_calls += 1
            # First query checks idempotency-key ownership; the second loads
            # the document; the final query loads the persisted job marker.
            if self.scalar_calls == 2:
                return document
            if self.scalar_calls == 3:
                return next(
                    value
                    for value in self.added
                    if isinstance(value, IngestionJob)
                    and value.job_id.startswith("meeting-vector-")
                )
            if self.scalar_calls == 4:
                return next(
                    (
                        value
                        for value in self.added
                        if isinstance(value, IngestionJob)
                        and value.job_id == f"meeting-import-{item.id}"
                    ),
                    None,
                )
            return None

        def add(self, value: Any) -> None:
            if isinstance(value, (Meeting, IngestionJob)) and value.id is None:
                value.id = uuid4()
            self.added.append(value)

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            self.commit_count += 1

        async def rollback(self) -> None:
            self.rollback_count += 1

        def begin(self) -> Begin:
            return Begin()

    session = Session()
    session.add(
        IngestionJob(
            job_id=f"meeting-vector-{item.id}-{revision.id}-v{revision.version}",
            organization_id=item.organization_id,
            knowledge_base_id=item.knowledge_base_id,
            document_id=document.id,
            status="COMPLETED",
            current_node="embed_chunks",
            progress=60,
            result_summary={
                "revision_id": str(revision.id),
                "revision_version": revision.version,
                "mode": "vector_only",
            },
        )
    )
    current = AuthContext(
        item.created_by, item.organization_id, "a@example.com", "A", Role.EDITOR, 0
    )

    from app.worker import celery_app

    def fail_send(*_: Any, **__: Any) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(celery_app.celery_app, "send_task", fail_send)
    result = await api.confirm_import(
        item.id,
        api.ConfirmRequest(expected_version=1, expected_metadata_version=1),
        session,  # type: ignore[arg-type]
        current,
        idempotency_key="confirm-test-key",
    )

    job = next(
        value
        for value in session.added
        if isinstance(value, IngestionJob) and value.job_id == f"meeting-import-{item.id}"
    )
    meeting = next(value for value in session.added if isinstance(value, Meeting))
    assert result.status == MeetingImportStatus.CONFIRMED.value
    assert result.rag_status == "QUEUED"
    assert result.rag_retryable is True
    assert job.current_node == "extract_knowledge"
    assert job.status == "QUEUED"
    assert job.error_code == "enqueue_failed"
    assert job.result_summary["retryable"] is True
    assert revision.status is TranscriptRevisionStatus.CONFIRMED
    assert document.active_transcript_revision_id == revision.id
    assert item.confirmed_revision_id == revision.id
    assert meeting.id == item.meeting_id
    assert meeting.description == "确认治疗路径"
    assert meeting.topic == full_discussion_topics[:255]
    assert meeting.meeting_info == {
        "meeting_purpose": "确认治疗路径",
        "discussion_topics": full_discussion_topics,
        "meeting_date": "2026年01月01日 08:00-09:00",
        "advisor_selection_criteria": "相关领域专家",
        "advisor_names": "李医生、王医生",
        "internal_attendees": "XLH：会议组织",
        "recorder": "张三",
    }
    assert session.commit_count == 1
    assert session.rollback_count == 0


def test_review_routes_are_registered_and_authenticated() -> None:
    schema = app.openapi()
    for path in (
        "/api/v1/meeting-imports/{import_id}/review",
        "/api/v1/meeting-imports/{import_id}/find",
        "/api/v1/meeting-imports/{import_id}/replace",
        "/api/v1/meeting-imports/{import_id}/metadata",
        "/api/v1/meeting-imports/{import_id}/confirm",
    ):
        methods = schema["paths"][path]
        operation = next(iter(methods.values()))
        assert operation["security"]


def test_import_lookup_statement_is_org_scoped() -> None:
    class Session:
        statement: Any = None

        async def scalar(self, statement: Any) -> Any:
            self.statement = statement
            return None

    session = Session()
    current = AuthContext(uuid4(), uuid4(), "a@example.com", "A", Role.EDITOR, 0)
    with pytest.raises(NotFoundError):
        import asyncio

        asyncio.run(api._get_import(session, current, uuid4()))
    assert "meeting_imports.organization_id" in str(session.statement)


def test_confirm_requires_independent_metadata_version() -> None:
    with pytest.raises(ValidationError):
        ConfirmRequest(expected_version=1)


async def test_confirmed_import_is_not_editable() -> None:
    item = _item()
    item.status = MeetingImportStatus.CONFIRMED

    class Session:
        async def scalar(self, statement: Any) -> Any:
            return item

    current = AuthContext(
        item.created_by, item.organization_id, "a@example.com", "A", Role.EDITOR, 0
    )
    with pytest.raises(ConflictError, match="不可编辑"):
        await api._get_review_import(Session(), current, item.id, write=True)  # type: ignore[arg-type]
