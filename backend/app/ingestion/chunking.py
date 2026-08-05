from __future__ import annotations

import hashlib
import math
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

CHUNKER_VERSION = "semantic-v3"


def estimate_tokens(text: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    non_chinese = len(re.findall(r"[A-Za-z0-9]+|[^\s\w]", text))
    return chinese + non_chinese


def source_locator(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    locator: dict[str, Any] = {"block_ids": [block["block_id"] for block in blocks]}
    for field in ("page_number", "slide_number", "speaker"):
        values = [block.get(field) for block in blocks if block.get(field) is not None]
        if values:
            locator[field] = values[0] if len(set(values)) == 1 else values
    starts = [int(block["start_ms"]) for block in blocks if block.get("start_ms") is not None]
    ends = [int(block["end_ms"]) for block in blocks if block.get("end_ms") is not None]
    if starts and ends:
        locator["time_range"] = {"start_ms": min(starts), "end_ms": max(ends)}
    return locator


def _split_table(block: dict[str, Any], max_tokens: int) -> list[dict[str, Any]]:
    markdown = str(block.get("table_markdown") or "").strip()
    lines = [line for line in markdown.splitlines() if line.strip()]
    if len(lines) < 3 or estimate_tokens(markdown) <= max_tokens:
        return [block]

    header = lines[:2]
    parts: list[dict[str, Any]] = []
    rows: list[str] = []
    for row in lines[2:]:
        candidate = "\n".join([*header, *rows, row])
        if rows and estimate_tokens(candidate) > max_tokens:
            part = dict(block)
            part["table_markdown"] = "\n".join([*header, *rows])
            parts.append(part)
            rows = []
        rows.append(row)
    if rows:
        part = dict(block)
        part["table_markdown"] = "\n".join([*header, *rows])
        parts.append(part)
    return parts or [block]


def _block_text(block: dict[str, Any]) -> str:
    return str(block.get("table_markdown") or block.get("text", "")).strip()


def _split_text_fragments(
    text: str, *, max_tokens: int, max_characters: int
) -> list[str]:
    parts: list[str] = []
    remaining = text.strip()
    while remaining:
        upper = min(len(remaining), max_characters)
        if estimate_tokens(remaining[:upper]) > max_tokens:
            low, high = 1, upper
            while low < high:
                middle = (low + high + 1) // 2
                if estimate_tokens(remaining[:middle]) <= max_tokens:
                    low = middle
                else:
                    high = middle - 1
            upper = max(1, low)
        boundary = [
            match.end()
            for match in re.finditer(r"[\n。！？!?；;]", remaining[:upper])
            if match.end() >= upper // 2
        ]
        if boundary:
            upper = boundary[-1]
        text_part = remaining[:upper].strip()
        remaining = remaining[upper:].strip()
        if text_part:
            parts.append(text_part)
    return parts


def _split_long_block(
    block: dict[str, Any], *, max_tokens: int, max_characters: int
) -> list[dict[str, Any]]:
    text = _block_text(block)
    if block.get("block_type") == "table":
        if estimate_tokens(text) <= max_tokens and len(text) <= max_characters:
            return [block]
        lines = [line for line in text.splitlines() if line.strip()]
        header = lines[:2] if len(lines) >= 3 else []
        header_text = "\n".join(header).strip()
        body_text = "\n".join(lines[2:] if header else lines).strip()
        if header_text:
            header_tokens = estimate_tokens(header_text)
            header_characters = len(header_text)
            if header_tokens < max_tokens and header_characters < max_characters:
                fragments = _split_text_fragments(
                    body_text,
                    max_tokens=max(1, max_tokens - header_tokens),
                    max_characters=max(1, max_characters - header_characters - 1),
                )
                table_parts: list[dict[str, Any]] = []
                for fragment in fragments:
                    table_markdown = f"{header_text}\n{fragment}"
                    part = dict(block)
                    part["text"] = table_markdown
                    part["table_markdown"] = table_markdown
                    table_parts.append(part)
                if table_parts:
                    return table_parts

        # If even the header is oversized, retain all table content and split it
        # as text rather than failing the document ingestion job.
        return [
            {**block, "text": fragment, "table_markdown": fragment}
            for fragment in _split_text_fragments(
                text, max_tokens=max_tokens, max_characters=max_characters
            )
        ]
    if estimate_tokens(text) <= max_tokens and len(text) <= max_characters:
        return [block]

    parts: list[dict[str, Any]] = []
    for text_part in _split_text_fragments(
        text, max_tokens=max_tokens, max_characters=max_characters
    ):
        part = dict(block)
        part["text"] = text_part
        parts.append(part)
    return parts


def prepare_semantic_units(
    blocks: list[dict[str, Any]],
    max_tokens: int,
    max_characters: int = 32_000,
) -> list[dict[str, Any]]:
    """Return the stable ordered units whose embeddings drive semantic boundaries."""

    if max_tokens <= 0 or max_characters <= 0:
        raise ValueError("semantic unit limits must be positive")
    return [
        unit
        for block in sorted(blocks, key=lambda item: item["order"])
        for part in _split_table(block, max_tokens)
        for unit in _split_long_block(
            part, max_tokens=max_tokens, max_characters=max_characters
        )
        if _block_text(unit)
    ]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("semantic vectors must be non-empty and have equal dimensions")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _is_structural_boundary(
    group: list[dict[str, Any]], block: dict[str, Any]
) -> bool:
    if not group:
        return False
    if block.get("block_type") in {"heading", "table"}:
        return True
    if block.get("heading_path") != group[0].get("heading_path"):
        return True
    previous_speaker = group[-1].get("speaker")
    current_speaker = block.get("speaker")
    return bool(
        previous_speaker
        and current_speaker
        and previous_speaker != current_speaker
    )


def build_chunks(
    blocks: list[dict[str, Any]],
    *,
    document_id: str = "",
    target_tokens: int = 700,
    max_tokens: int = 1000,
    overlap_tokens: int = 100,
    semantic_vectors: list[list[float]] | None = None,
    similarity_threshold: float = 0.65,
    max_unit_characters: int = 32_000,
    prepared_units: list[dict[str, Any]] | None = None,
    include_unit_indexes: bool = False,
) -> list[dict[str, Any]]:
    if not -1 <= similarity_threshold <= 1:
        raise ValueError("similarity_threshold must be between -1 and 1")
    chunks: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    group_indexes: list[int] = []
    group_tokens = 0
    group_is_overlap = False

    def flush(*, keep_overlap: bool = False) -> None:
        nonlocal group, group_indexes, group_is_overlap, group_tokens
        if not group:
            return
        content = "\n\n".join(_block_text(block) for block in group).strip()
        chunk: dict[str, Any] = {
            "chunk_id": str(
                uuid5(
                    NAMESPACE_URL,
                    (
                        f"{document_id}:{len(chunks)}:"
                        f"{hashlib.sha256(content.encode()).hexdigest()}"
                    ),
                )
            ),
            "chunk_index": len(chunks),
            "content": content,
            "heading_path": group[0].get("heading_path", []),
            "content_type": group[0].get("block_type", "text"),
            "token_count": estimate_tokens(content),
            "source_block_ids": [block["block_id"] for block in group],
            "source_locator": source_locator(group),
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "chunker_version": CHUNKER_VERSION,
        }
        if include_unit_indexes:
            chunk["unit_indexes"] = list(group_indexes)
        chunks.append(chunk)
        carry: list[dict[str, Any]] = []
        carry_indexes: list[int] = []
        carry_tokens = 0
        if keep_overlap and overlap_tokens > 0:
            heading_path = group[-1].get("heading_path")
            for position in range(len(group) - 1, -1, -1):
                candidate = group[position]
                candidate_tokens = estimate_tokens(_block_text(candidate))
                if (
                    candidate.get("block_type") in {"heading", "table"}
                    or candidate.get("heading_path") != heading_path
                    or candidate.get("speaker") != group[-1].get("speaker")
                    or carry_tokens + candidate_tokens > overlap_tokens
                ):
                    break
                carry.insert(0, candidate)
                carry_indexes.insert(0, group_indexes[position])
                carry_tokens += candidate_tokens
        group, group_tokens = carry, carry_tokens
        group_indexes = carry_indexes
        group_is_overlap = bool(carry)

    expanded_blocks = (
        list(prepared_units)
        if prepared_units is not None
        else prepare_semantic_units(blocks, max_tokens, max_unit_characters)
    )
    if semantic_vectors is not None and len(semantic_vectors) != len(expanded_blocks):
        raise ValueError("semantic vector count must match prepared semantic units")
    minimum_semantic_tokens = max(1, min(target_tokens, max_tokens) // 2)
    for index, block in enumerate(expanded_blocks):
        text = _block_text(block)
        token_count = estimate_tokens(text)
        is_semantic_boundary = bool(
            semantic_vectors is not None
            and index > 0
            and group_tokens >= minimum_semantic_tokens
            and _cosine_similarity(
                semantic_vectors[index - 1], semantic_vectors[index]
            )
            < similarity_threshold
        )
        structural_boundary = _is_structural_boundary(group, block)
        exceeds_maximum = bool(group and group_tokens + token_count > max_tokens)
        if group_is_overlap and (
            structural_boundary or is_semantic_boundary or exceeds_maximum
        ):
            group = []
            group_indexes = []
            group_tokens = 0
            group_is_overlap = False
            structural_boundary = False
            exceeds_maximum = False
        if structural_boundary:
            flush()
        elif group and is_semantic_boundary:
            flush()
        elif exceeds_maximum:
            flush(keep_overlap=True)
            if group and group_tokens + token_count > max_tokens:
                group = []
                group_indexes = []
                group_tokens = 0
                group_is_overlap = False
        group.append(block)
        group_indexes.append(index)
        group_tokens += token_count
        group_is_overlap = False
        if block.get("block_type") == "table" or (
            semantic_vectors is None and group_tokens >= target_tokens
        ):
            flush(keep_overlap=block.get("block_type") != "table")
    if not group_is_overlap:
        flush()
    return chunks
