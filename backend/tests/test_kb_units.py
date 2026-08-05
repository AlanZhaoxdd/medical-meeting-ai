import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1 import documents as documents_api
from app.core.config import get_settings
from app.core.exceptions import AppException, ConflictError
from app.core.security import create_access_token, decode_access_token
from app.ingestion.chunking import build_chunks, source_locator
from app.ingestion.state import ensure_transition
from app.ingestion.validation import safe_filename, validate_upload
from app.schemas.kb import DocumentStatus, SourceRef
from app.services.vector_store import reciprocal_rank_fusion
from app.worker.extraction import _parse_knowledge_extraction, _raw_structured_value
from app.worker.graph import _input_version
from app.worker.parser import parse_transcript


def test_upload_extension_mime_size_and_safe_filename() -> None:
    assert safe_filename("../../患者 讨论.pdf") == "患者_讨论.pdf"
    digest = validate_upload("报告.pdf", "application/pdf", b"%PDF-test", 100)
    assert len(digest) == 64
    with pytest.raises(AppException):
        validate_upload("报告.pdf", "text/plain", b"not-pdf", 100)
    with pytest.raises(AppException):
        validate_upload("报告.txt", "text/plain", b"x" * 101, 100)


def test_heading_table_and_transcript_chunks_preserve_sources() -> None:
    blocks = [
        {
            "block_id": "h1",
            "block_type": "heading",
            "order": 0,
            "heading_path": ["疗效"],
            "text": "疗效",
            "page_number": 1,
        },
        {
            "block_id": "p1",
            "block_type": "paragraph",
            "order": 1,
            "heading_path": ["疗效"],
            "text": "主要终点达到预设标准。",
            "page_number": 1,
        },
        {
            "block_id": "t1",
            "block_type": "table",
            "order": 2,
            "heading_path": ["安全性"],
            "text": "安全性表",
            "table_markdown": "|事件|例数|\\n|---|---|\\n|恶心|2|",
            "page_number": 2,
        },
    ]
    chunks = build_chunks(blocks, target_tokens=50, max_tokens=80)
    assert len(chunks) == 2
    assert chunks[0]["source_block_ids"] == ["h1", "p1"]
    assert chunks[1]["content_type"] == "table"
    assert chunks[1]["source_locator"]["page_number"] == 2

    transcript = parse_transcript(
        json.dumps(
            {
                "language": "zh-CN",
                "segments": [
                    {
                        "speaker": "专家A",
                        "start_ms": 1000,
                        "end_ms": 8500,
                        "text": "发言内容",
                    }
                ],
            }
        ).encode()
    )
    assert source_locator(transcript)["time_range"] == {
        "start_ms": 1000,
        "end_ms": 8500,
    }


def test_chunk_overlap_and_long_table_repeats_header() -> None:
    blocks = [
        {
            "block_id": f"p{index}",
            "block_type": "paragraph",
            "order": index,
            "heading_path": ["结论"],
            "text": "一二三四五",
        }
        for index in range(4)
    ]
    chunks = build_chunks(blocks, target_tokens=10, max_tokens=15, overlap_tokens=5)
    assert chunks[0]["source_block_ids"] == ["p0", "p1"]
    assert chunks[1]["source_block_ids"][:2] == ["p1", "p2"]

    table = {
        "block_id": "table-1",
        "block_type": "table",
        "order": 5,
        "heading_path": ["数据"],
        "text": "长表",
        "table_markdown": "|项目|数值|\n|---|---|\n"
        + "\n".join(f"|指标{index}|{index}|" for index in range(8)),
    }
    table_chunks = build_chunks([table], target_tokens=40, max_tokens=40)
    assert len(table_chunks) > 1
    assert all("|---|---|" in chunk["content"] for chunk in table_chunks)
    assert all(chunk["source_block_ids"] == ["table-1"] for chunk in table_chunks)


def test_chunk_ids_are_unique_per_document_and_stable_for_retries() -> None:
    blocks = [
        {
            "block_id": "p1",
            "block_type": "paragraph",
            "order": 0,
            "heading_path": [],
            "text": "相同的会议内容",
        }
    ]
    first_attempt = build_chunks(blocks, document_id="document-a")
    retry = build_chunks(blocks, document_id="document-a")
    another_document = build_chunks(blocks, document_id="document-b")

    assert first_attempt[0]["chunk_id"] == retry[0]["chunk_id"]
    assert first_attempt[0]["chunk_id"] != another_document[0]["chunk_id"]


def test_source_reference_and_state_machine_guards() -> None:
    assert SourceRef(chunk_id="chunk-1", quote="原文").chunk_id == "chunk-1"
    with pytest.raises(ValidationError):
        SourceRef(quote="没有定位")
    ensure_transition("UPLOADED", DocumentStatus.PARSING)
    with pytest.raises(ConflictError):
        ensure_transition("UPLOADED", DocumentStatus.PUBLISHED)


def test_rrf_and_jwt() -> None:
    fused = reciprocal_rank_fusion(["a", "b"], ["b", "c"])
    assert fused["b"] > fused["a"]
    get_settings().jwt_secret_key = "unit-test-secret-key-at-least-32-characters"
    token = create_access_token(user_id="user", organization_id="org", token_version=2)
    assert decode_access_token(token)["ver"] == 2


def test_ingestion_input_version_is_fixed_length_and_deterministic() -> None:
    version = _input_version(
        sha256="a" * 64,
        template_id="232c9ac9-9029-4333-9d76-00ab476bc2e4",
        template_version=1,
        embedding_version="bge-m3-v1",
    )
    assert len(version) == 64
    assert version == _input_version(
        sha256="a" * 64,
        template_id="232c9ac9-9029-4333-9d76-00ab476bc2e4",
        template_version=1,
        embedding_version="bge-m3-v1",
    )
    assert version != _input_version(
        sha256="b" * 64,
        template_id="232c9ac9-9029-4333-9d76-00ab476bc2e4",
        template_version=1,
        embedding_version="bge-m3-v1",
    )


@pytest.mark.asyncio
async def test_reindex_rebuilds_chunks_without_reparsing_or_ocr(monkeypatch) -> None:
    document = SimpleNamespace(
        status=DocumentStatus.AWAITING_REVIEW.value,
        vector_sync_status="SYNCED",
    )
    captured: dict[str, str] = {}

    async def get_document(*_args, **_kwargs):
        return document

    async def new_job(_session, _document, start_node: str):
        captured["start_node"] = start_node
        return SimpleNamespace(job_id="semantic-reindex")

    class Session:
        async def commit(self) -> None:
            return None

    monkeypatch.setattr(documents_api, "_get_document", get_document)
    monkeypatch.setattr(documents_api, "_new_job", new_job)
    monkeypatch.setattr(documents_api, "_dispatch_job", lambda _job_id: None)
    monkeypatch.setattr(documents_api, "serialize_job", lambda job: job)

    await documents_api.reindex_document(
        uuid4(), uuid4(), Session(), SimpleNamespace()
    )

    assert captured["start_node"] == "build_chunks"
    assert document.status == DocumentStatus.CHUNKING.value
    assert document.vector_sync_status == "PENDING"


@pytest.mark.asyncio
async def test_reindex_rejects_published_document(monkeypatch) -> None:
    document = SimpleNamespace(
        status=DocumentStatus.PUBLISHED.value,
        vector_sync_status="SYNCED",
    )

    async def get_document(*_args, **_kwargs):
        return document

    monkeypatch.setattr(documents_api, "_get_document", get_document)

    with pytest.raises(AppException) as caught:
        await documents_api.reindex_document(
            uuid4(), uuid4(), SimpleNamespace(), SimpleNamespace()
        )

    assert caught.value.code == "published_document_reindex_requires_new_version"


def test_structured_extraction_parses_mapping_and_json() -> None:
    payload = {
        "items": [
            {
                "item_type": "topic",
                "title": "主要议题",
                "normalized_content": "讨论方案。",
                "source_refs": [{"chunk_id": "chunk-1", "quote": "讨论方案"}],
                "confidence": 0.9,
            }
        ]
    }
    assert _parse_knowledge_extraction(payload) is not None
    assert _parse_knowledge_extraction(json.dumps(payload, ensure_ascii=False)) is not None
    fenced = f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    assert _parse_knowledge_extraction(fenced) is not None
    assert _parse_knowledge_extraction({"items": [{"title": "缺少字段"}]}) is None


def test_structured_extraction_handles_openai_tool_call_shapes() -> None:
    payload = {"items": []}
    assert _raw_structured_value({"content": json.dumps(payload)}) == json.dumps(payload)
    assert _raw_structured_value(
        {"tool_calls": [{"function": {"arguments": json.dumps(payload)}}]}
    ) == json.dumps(payload)
