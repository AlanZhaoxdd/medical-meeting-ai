import pytest

from app.ingestion.chunking import (
    CHUNKER_VERSION,
    build_chunks,
    prepare_semantic_units,
)


def _paragraph(
    block_id: str,
    order: int,
    text: str,
    *,
    heading_path: list[str] | None = None,
    **metadata: object,
) -> dict[str, object]:
    return {
        "block_id": block_id,
        "block_type": "paragraph",
        "order": order,
        "heading_path": heading_path or ["语义分块"],
        "text": text,
        **metadata,
    }


def test_low_similarity_starts_a_new_chunk() -> None:
    blocks = [
        _paragraph("p1", 0, "alpha beta"),
        _paragraph("p2", 1, "gamma delta"),
        _paragraph("p3", 2, "epsilon zeta"),
    ]

    chunks = build_chunks(
        blocks,
        document_id="semantic-low-similarity",
        target_tokens=4,
        max_tokens=20,
        semantic_vectors=[[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]],
        similarity_threshold=0.9,
    )

    assert [chunk["source_block_ids"] for chunk in chunks] == [["p1"], ["p2", "p3"]]


def test_high_similarity_keeps_adjacent_units_together() -> None:
    blocks = [
        _paragraph("p1", 0, "alpha beta"),
        _paragraph("p2", 1, "gamma delta"),
        _paragraph("p3", 2, "epsilon zeta"),
    ]

    chunks = build_chunks(
        blocks,
        document_id="semantic-high-similarity",
        target_tokens=2,
        max_tokens=20,
        semantic_vectors=[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
        similarity_threshold=0.9,
    )

    assert len(chunks) == 1
    assert chunks[0]["source_block_ids"] == ["p1", "p2", "p3"]


def test_heading_table_and_speaker_are_hard_boundaries() -> None:
    blocks = [
        {
            "block_id": "h1",
            "block_type": "heading",
            "order": 0,
            "heading_path": ["背景"],
            "text": "背景",
        },
        _paragraph("p1", 1, "背景说明", heading_path=["背景"], speaker="专家A"),
        {
            "block_id": "h2",
            "block_type": "heading",
            "order": 2,
            "heading_path": ["结果"],
            "text": "结果",
        },
        _paragraph("p2", 3, "结果说明", heading_path=["结果"], speaker="专家A"),
        _paragraph("p3", 4, "补充说明", heading_path=["结果"], speaker="专家B"),
        {
            "block_id": "t1",
            "block_type": "table",
            "order": 5,
            "heading_path": ["结果"],
            "text": "结果表",
            "table_markdown": "|项目|数值|\n|---|---|\n|样本|10|",
        },
        _paragraph("p4", 6, "表后说明", heading_path=["结果"], speaker="专家B"),
    ]

    chunks = build_chunks(
        blocks,
        document_id="structural-boundaries",
        max_tokens=100,
        semantic_vectors=[[1.0, 0.0]] * len(prepare_semantic_units(blocks, 100)),
        similarity_threshold=0.9,
    )

    assert [chunk["source_block_ids"] for chunk in chunks] == [
        ["h1", "p1"],
        ["h2", "p2"],
        ["p3"],
        ["t1"],
        ["p4"],
    ]


def test_prepare_semantic_units_is_ordered_and_ignores_empty_text() -> None:
    blocks = [
        _paragraph("p2", 2, "second"),
        _paragraph("empty", 1, "   "),
        _paragraph("p1", 0, "first"),
    ]

    units = prepare_semantic_units(blocks, max_tokens=20)

    assert [unit["block_id"] for unit in units] == ["p1", "p2"]


def test_semantic_vector_count_must_match_prepared_units() -> None:
    blocks = [
        _paragraph("p1", 0, "first"),
        _paragraph("p2", 1, "second"),
    ]

    with pytest.raises(ValueError, match="semantic vector count"):
        build_chunks(
            blocks,
            semantic_vectors=[[1.0, 0.0]],
            max_tokens=20,
        )


def test_unit_indexes_track_units_through_merge_and_overlap() -> None:
    blocks = [
        _paragraph("p1", 0, "alpha beta"),
        _paragraph("p2", 1, "gamma delta"),
        _paragraph("p3", 2, "epsilon zeta"),
    ]
    units = prepare_semantic_units(blocks, max_tokens=20)

    chunks = build_chunks(
        blocks,
        document_id="unit-indexes",
        target_tokens=4,
        max_tokens=20,
        semantic_vectors=[[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]],
        similarity_threshold=0.9,
        prepared_units=units,
        include_unit_indexes=True,
    )

    assert [chunk["unit_indexes"] for chunk in chunks] == [[0], [1, 2]]


def test_unit_indexes_absent_by_default() -> None:
    blocks = [
        _paragraph("p1", 0, "alpha"),
        _paragraph("p2", 1, "beta"),
    ]

    chunks = build_chunks(
        blocks,
        document_id="no-unit-indexes",
        max_tokens=20,
        semantic_vectors=[[1.0, 0.0], [1.0, 0.0]],
        similarity_threshold=0.9,
    )

    assert "unit_indexes" not in chunks[0]


def test_chunk_ids_are_stable_and_source_locator_is_preserved() -> None:
    blocks = [
        _paragraph(
            "p1",
            0,
            "开场内容",
            heading_path=["讨论"],
            page_number=3,
            slide_number=7,
            speaker="主持人",
            start_ms=100,
            end_ms=800,
        ),
        _paragraph(
            "p2",
            1,
            "继续内容",
            heading_path=["讨论"],
            page_number=3,
            slide_number=7,
            speaker="主持人",
            start_ms=800,
            end_ms=1600,
        ),
    ]
    vectors = [[1.0, 0.0], [1.0, 0.0]]

    first = build_chunks(
        list(reversed(blocks)),
        document_id="stable-document",
        max_tokens=20,
        semantic_vectors=vectors,
        similarity_threshold=0.9,
    )
    retry = build_chunks(
        blocks,
        document_id="stable-document",
        max_tokens=20,
        semantic_vectors=vectors,
        similarity_threshold=0.9,
    )

    assert first[0]["chunk_id"] == retry[0]["chunk_id"]
    assert first[0]["chunker_version"] == CHUNKER_VERSION == "semantic-v3"
    assert first[0]["source_locator"] == {
        "block_ids": ["p1", "p2"],
        "page_number": 3,
        "slide_number": 7,
        "speaker": "主持人",
        "time_range": {"start_ms": 100, "end_ms": 1600},
    }


def test_long_units_are_split_before_embedding() -> None:
    blocks = [_paragraph("long", 0, "医学证据。" * 80)]

    units = prepare_semantic_units(
        blocks,
        max_tokens=100,
        max_characters=80,
    )

    assert len(units) > 1
    assert all(len(str(unit["text"])) <= 80 for unit in units)
    assert all(len(str(unit["text"])) > 0 for unit in units)


def test_overlap_never_pushes_a_chunk_past_maximum() -> None:
    blocks = [
        _paragraph("p1", 0, "甲" * 70),
        _paragraph("p2", 1, "乙" * 30),
        _paragraph("p3", 2, "丙" * 95),
    ]

    chunks = build_chunks(
        blocks,
        document_id="bounded-overlap",
        target_tokens=100,
        max_tokens=100,
        overlap_tokens=40,
    )

    assert all(chunk["token_count"] <= 100 for chunk in chunks)
    assert [chunk["source_block_ids"] for chunk in chunks] == [
        ["p1", "p2"],
        ["p3"],
    ]


def test_oversized_atomic_table_row_is_split_with_repeated_header() -> None:
    table = {
        "block_id": "oversized-table",
        "block_type": "table",
        "order": 0,
        "heading_path": ["数据"],
        "text": "超长表格",
        "table_markdown": "|项目|数值|\n|---|---|\n|说明|" + "甲" * 200 + "|",
    }

    units = prepare_semantic_units([table], max_tokens=50)

    assert len(units) > 1
    assert all("|项目|数值|" in str(unit["table_markdown"]) for unit in units)
    assert all("|---|---|" in str(unit["table_markdown"]) for unit in units)
    assert all(len(str(unit["table_markdown"])) <= 80 for unit in units)
