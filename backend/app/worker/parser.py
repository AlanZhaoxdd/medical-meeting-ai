from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import anyio

from app.core.exceptions import AppException


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_INVISIBLE_RE = re.compile(r"[\u200b-\u200d\u2060\ufeff]")


def _clean_common_text(value: str) -> str:
    """Remove parser artefacts without flattening meaningful line boundaries."""
    cleaned = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _HTML_BREAK_RE.sub("\n", cleaned)
    cleaned = cleaned.replace("\u00a0", " ").replace("\u3000", " ")
    cleaned = _INVISIBLE_RE.sub("", _HTML_COMMENT_RE.sub("", cleaned))
    lines = [re.sub(r"[\t\f\v ]+", " ", line).strip() for line in cleaned.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def clean_table_markdown(value: str) -> str:
    """Normalize Docling's markdown tables for a human-readable transcript preview."""
    rows: list[str] = []
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _clean_common_text(raw_line)
        if not line:
            continue
        if "|" not in line:
            rows.append(line)
            continue
        cells = line.split("|")
        leading_pipe = not cells[0].strip()
        trailing_pipe = not cells[-1].strip()
        start = 1 if leading_pipe else 0
        end = -1 if trailing_pipe else len(cells)
        normalized = [_clean_common_text(cell).replace("\n", " ") for cell in cells[start:end]]
        if normalized and all(re.fullmatch(r":?-{3,}:?", cell) for cell in normalized):
            prefix = "|" if leading_pipe else ""
            suffix = "|" if trailing_pipe else ""
            rows.append(f"{prefix}{'|'.join(normalized)}{suffix}")
            continue
        prefix = "| " if leading_pipe else ""
        suffix = " |" if trailing_pipe else ""
        rows.append(f"{prefix}{' | '.join(normalized)}{suffix}")
    return "\n".join(rows).strip()


def clean_transcript_text(value: str, *, table: bool = False) -> str:
    cleaned = _clean_common_text(value)
    if table or ("|" in cleaned and "\n" in cleaned):
        return clean_table_markdown(cleaned)
    return cleaned


def _docx_table_rows(path: str) -> list[list[list[str]]]:
    """Read Word tables losslessly; Docling Markdown flattens merged cells."""
    from docx import Document as WordDocument

    document = WordDocument(path)
    return [
        [[_clean_common_text(cell.text) for cell in row.cells] for row in table.rows]
        for table in document.tables
    ]


def table_rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    column_count = max(len(row) for row in rows)

    def render_cell(value: str) -> str:
        return value.replace("|", "｜").replace("\n", "<br>")

    rendered: list[str] = []
    for row in rows:
        cells = [render_cell(value) for value in row]
        cells.extend([""] * (column_count - len(cells)))
        rendered.append(f"| {' | '.join(cells)} |")
    rendered.insert(1, f"|{'|'.join(['---'] * column_count)}|")
    return "\n".join(rendered)


def parse_transcript(content: bytes) -> list[dict[str, Any]]:
    try:
        payload = json.loads(content)
        segments = payload["segments"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AppException(422, "invalid_transcript_json", "逐字稿 JSON 格式无效") from exc
    blocks: list[dict[str, Any]] = []
    for order, segment in enumerate(segments):
        try:
            text = clean_transcript_text(str(segment["text"]))
            speaker = str(segment["speaker"]).strip()
            start_ms = int(segment["start_ms"])
            end_ms = int(segment["end_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppException(
                422, "invalid_transcript_segment", f"第 {order + 1} 个逐字稿片段无效"
            ) from exc
        if not text or end_ms <= start_ms:
            raise AppException(
                422, "invalid_transcript_segment", f"第 {order + 1} 个逐字稿片段无效"
            )
        blocks.append(
            {
                "block_id": f"transcript-{order:06d}",
                "block_type": "speech",
                "order": order,
                "heading_path": [],
                "text": text,
                "speaker": speaker,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "content_hash": _hash(text),
            }
        )
    return blocks


def parse_plain_text(content: bytes, suffix: str) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppException(422, "invalid_text_encoding", "文本文件必须使用 UTF-8") from exc
    blocks: list[dict[str, Any]] = []
    heading_path: list[str] = []
    for line in (line.strip() for line in text.splitlines()):
        line = clean_transcript_text(line)
        if not line:
            continue
        is_heading = suffix in {".md", ".markdown"} and line.startswith("#")
        if is_heading:
            level = len(line) - len(line.lstrip("#"))
            title = line[level:].strip()
            heading_path = heading_path[: level - 1] + [title]
            block_type = "heading"
            value = title
        else:
            block_type = "paragraph"
            value = line
        order = len(blocks)
        blocks.append(
            {
                "block_id": f"text-{order:06d}",
                "block_type": block_type,
                "order": order,
                "heading_path": heading_path.copy(),
                "text": value,
                "content_hash": _hash(value),
            }
        )
    return blocks


def _parse_docling_file(path: str) -> list[dict[str, Any]]:
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(path, raises_on_error=True)
    blocks: list[dict[str, Any]] = []
    heading_path: list[str] = []
    suffix = Path(path).suffix.lower()
    word_tables = _docx_table_rows(path) if suffix == ".docx" else []
    table_index = 0
    table_descendant_level: int | None = None
    for item, level in result.document.iterate_items():
        item_level = int(level or 0)
        if suffix == ".docx" and table_descendant_level is not None:
            if item_level > table_descendant_level:
                continue
            table_descendant_level = None
        label = str(getattr(item, "label", "text")).lower()
        text = clean_transcript_text(str(getattr(item, "text", "") or ""))
        table_markdown = None
        structured_rows = None
        if "table" in label:
            structured_rows = word_tables[table_index] if table_index < len(word_tables) else None
            table_index += 1
            if structured_rows:
                table_markdown = clean_table_markdown(table_rows_to_markdown(structured_rows))
                text = table_markdown
            else:
                exporter = getattr(item, "export_to_markdown", None)
                table_markdown = clean_table_markdown(
                    exporter() if callable(exporter) else text
                )
                text = text or table_markdown
            if suffix == ".docx":
                table_descendant_level = item_level
        text = clean_transcript_text(text, table="table" in label)
        if not text:
            continue
        if "title" in label or "heading" in label or "section" in label:
            depth = max(1, int(level or 1))
            heading_path = heading_path[: depth - 1] + [text]
            block_type = "heading"
        elif "table" in label:
            block_type = "table"
        elif "list" in label:
            block_type = "list"
        else:
            block_type = "paragraph"
        provenance = (getattr(item, "prov", None) or [None])[0]
        page_number = getattr(provenance, "page_no", None)
        bbox_obj = getattr(provenance, "bbox", None)
        bbox = None
        if bbox_obj is not None:
            bbox = {
                key: float(getattr(bbox_obj, key))
                for key in ("l", "t", "r", "b")
                if getattr(bbox_obj, key, None) is not None
            }
        index = len(blocks)
        blocks.append(
            {
                "block_id": f"docling-{index:06d}",
                "block_type": block_type,
                "order": index,
                "heading_path": heading_path.copy(),
                "text": text,
                "table_markdown": table_markdown,
                "page_number": page_number,
                "slide_number": page_number if suffix == ".pptx" else None,
                "bbox": bbox,
                "content_hash": _hash(text),
                **({"table_rows": structured_rows} if structured_rows else {}),
            }
        )
    return blocks


async def parse_document_bytes(
    content: bytes, filename: str, source_type: str
) -> list[dict[str, Any]]:
    suffix = Path(filename).suffix.lower()
    if source_type == "transcript":
        return parse_transcript(content)
    if suffix in {".txt", ".md", ".markdown"}:
        return parse_plain_text(content, suffix)
    with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
        temporary.write(content)
        temporary.flush()
        return await anyio.to_thread.run_sync(_parse_docling_file, temporary.name)
