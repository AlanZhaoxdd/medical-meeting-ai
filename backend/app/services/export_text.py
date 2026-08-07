from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from app.schemas.export import TextExportSection, TextPreviewRead
from app.services.export_bundle import (
    AnalysisBundle,
    normalize_speaker_name,
    source_index,
)


SECTION_HEADERS: list[tuple[str, str]] = [
    ("summary", "会议总述"),
    ("overview", "会议概况"),
    ("consensus", "核心结论与共识"),
    ("decisions", "关键决策点"),
    ("open_items", "待确认事项"),
    ("divergence", "分歧与遗留问题"),
    ("actions", "行动项"),
    ("next_meeting", "下次会议与跟进安排"),
]

SECTION_ALIASES: dict[str, str] = {
    "会议总述": "summary",
    "会议概况": "overview",
    "核心结论与共识": "consensus",
    "共识": "consensus",
    "关键决策点": "decisions",
    "切点问题": "decisions",
    "待确认事项": "open_items",
    "开放性问题": "open_items",
    "分歧与遗留问题": "divergence",
    "分歧": "divergence",
    "行动项": "actions",
    "下次会议与跟进安排": "next_meeting",
    "下次会议": "next_meeting",
}


def _known_header(title: str) -> str | None:
    cleaned = re.sub(r"^#+\s*", "", title).strip()
    for alias, key in SECTION_ALIASES.items():
        if cleaned == alias or cleaned.startswith(alias):
            return key
    return None


def split_minutes_sections(content: str) -> list[tuple[str, str, list[int]]]:
    """Split the confirmed AI minutes markdown into (key, body, citations)."""

    sections: list[tuple[str, str, list[int]]] = []
    if not content:
        return sections
    parts = re.split(r"(?m)^(##+)\s+(.+?)\s*$", content)
    pending_intro = parts[0].strip()
    if pending_intro:
        sections.append(("intro", pending_intro, []))
    for index in range(1, len(parts) - 1, 3):
        title = parts[index + 1].strip()
        body = parts[index + 2].strip()
        citations = [
            int(value)
            for value in re.findall(r"\[(\d+)\]", body)
            if value.isdigit()
        ]
        sections.append((title, body, citations))
    return sections


def _strip_markdown(value: str) -> str:
    text = value
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    return text.strip()


def _escape_paragraph(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _line_items(body: str) -> list[str]:
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[-*+•]\s+", stripped):
            lines.append(_strip_markdown(re.sub(r"^[-*+•]\s+", "", stripped)))
        elif re.match(r"^\d+[.、]\s+", stripped):
            lines.append(_strip_markdown(re.sub(r"^\d+[.、]\s+", "", stripped)))
    return lines


def _parse_minutes(bundle: AnalysisBundle) -> dict[str, tuple[str, list[int]]]:
    """Map confirmed minutes markdown to canonical section keys."""

    minutes_module = next(
        (module for module in bundle.modules if module.get("id") == "minutes"),
        None,
    )
    content = str((minutes_module or {}).get("content") or "")
    parsed: list[tuple[str, str, list[int]]] = []
    for title, body, citations in split_minutes_sections(content):
        key = _known_header(title)
        parsed.append((key or title, body, citations))
    result: dict[str, tuple[str, list[int]]] = {}
    for key, body, citations in parsed:
        if key in SECTION_HEADERS_MAP and key not in result:
            result[key] = (body, citations)
    if "summary" not in result and parsed and parsed[0][0] == "intro":
        result["summary"] = (parsed[0][1], parsed[0][2])
    return result


SECTION_HEADERS_MAP = {key: title for key, title in SECTION_HEADERS}


def _attendee_viewpoints(bundle: AnalysisBundle) -> tuple[str, list[int]]:
    by_speaker: dict[str, list[str]] = {}
    for block in bundle.transcript_blocks:
        if not block.speaker or not (block.text or "").strip():
            continue
        text = _strip_markdown(block.text).strip()
        if text:
            by_speaker.setdefault(normalize_speaker_name(block.speaker), []).append(text)
    if not by_speaker:
        return "", []
    lines = [
        f"**{speaker}**：" + " ".join(parts[:3])
        for speaker, parts in list(by_speaker.items())[:40]
    ]
    return "\n".join(lines), []


def compose_export_sections(
    bundle: AnalysisBundle,
    *,
    selected: list[str] | None,
    show_attendee_names: bool,
    include_timestamps: bool,
) -> list[TextExportSection]:
    """Build the ordered export sections from the confirmed analysis only."""

    minutes = _parse_minutes(bundle)
    sources = bundle.sources
    source_by_index = {source_index(item): item for item in sources}
    topics: list[str] = []
    for question in bundle.questions:
        label = question.content.strip()
        if label and label not in topics:
            topics.append(label)

    def make(
        key: str,
        title: str,
        body: str | None,
        citations: list[int],
        items: list[str] | None = None,
    ) -> TextExportSection:
        cleaned = body.strip() if body else ""
        if items is None:
            items = _line_items(cleaned)
        return TextExportSection(
            key=key,
            title=title,
            content=cleaned if not items else cleaned,
            items=items,
            citations=sorted(set(c for c in citations if 1 <= c <= len(sources))),
        )

    available: dict[str, TextExportSection] = {}
    if (body := minutes.get("summary")):
        available["summary"] = make("summary", "会议核心摘要", body[0], body[1])
    if (body := minutes.get("overview")):
        available["overview"] = make("overview", "会议基本信息", body[0], body[1])
    if (body := minutes.get("consensus")):
        available["consensus"] = make("consensus", "会议共识", body[0], body[1])
    if (body := minutes.get("decisions")):
        available["cutoff"] = make("cutoff", "切点问题及分析", body[0], body[1])
    if (body := minutes.get("open_items")):
        available["open"] = make("open", "开放性问题及分析", body[0], body[1])
    if (body := minutes.get("divergence")):
        available["divergence"] = make("divergence", "分歧与待确认问题", body[0], body[1])
    if (body := minutes.get("actions")):
        available["actions"] = make("actions", "行动项", body[0], body[1])
    viewpoints, viewpoint_citations = _attendee_viewpoints(bundle)
    if viewpoints:
        available["viewpoints"] = make(
            "viewpoints", "参会者观点", viewpoints, viewpoint_citations
        )
    if topics:
        available["topics"] = make("topics", "主要议题", None, [], topics)

    ordered_keys = [
        "overview",
        "summary",
        "topics",
        "viewpoints",
        "consensus",
        "divergence",
        "cutoff",
        "open",
        "actions",
        "ai",
        "sources",
    ]
    if selected is not None:
        allowed = set(selected)
        ordered_keys = [key for key in ordered_keys if key in allowed]
    sections: list[TextExportSection] = []
    for key in ordered_keys:
        if key == "ai":
            minutes_body = minutes.get("summary", ("", []))
            if minutes_body[0]:
                sections.append(make("ai", "AI 分析结论", minutes_body[0], minutes_body[1]))
            continue
        if key == "sources":
            if not sources:
                continue
            sections.append(
                TextExportSection(
                    key="sources",
                    title="引用来源或知识库依据",
                    content=None,
                    items=[
                        f"[{source_index(item)}] {item.get('title', '来源')}："
                        f"{str(item.get('snippet') or '')[:200]}"
                        for item in sources
                    ],
                    citations=[],
                )
            )
            continue
        section = available.get(key)
        if section is None:
            continue
        sections.append(section)

    if not show_attendee_names:
        for section in sections:
            if section.key == "viewpoints" and section.content:
                section.content = re.sub(
                    r"\*\*([^*]+)\*\*：", "**参会者**：", section.content
                )
    return sections


def default_file_name(bundle: AnalysisBundle, fmt: str) -> str:
    safe = re.sub(r"[\\/:*?\"<>|\s]+", "-", bundle.meeting.title).strip("-")
    safe = (safe or "会议成果")[:60]
    return f"{safe}-AI会议纪要-{datetime.now(timezone.utc).strftime('%Y%m%d')}.{fmt}"


def build_text_preview(
    bundle: AnalysisBundle,
    *,
    selected: list[str] | None,
    show_attendee_names: bool,
    template: str,
    include_cover: bool,
) -> TextPreviewRead:
    sections = compose_export_sections(
        bundle,
        selected=selected,
        show_attendee_names=show_attendee_names,
        include_timestamps=False,
    )
    return TextPreviewRead(
        meeting_id=bundle.meeting.id,
        meeting_title=bundle.meeting.title,
        starts_at=bundle.meeting.starts_at.isoformat() if bundle.meeting.starts_at else None,
        ends_at=bundle.meeting.ends_at.isoformat() if bundle.meeting.ends_at else None,
        location=bundle.meeting.location,
        organizer=bundle.meeting.organizer,
        topic=bundle.meeting.topic,
        analysis_version=bundle.analysis_version,
        template=template,
        include_cover=include_cover,
        sections=sections,
        sources=[
            {
                "index": source_index(item),
                "title": item.get("title"),
                "snippet": item.get("snippet"),
                "type": item.get("type"),
            }
            for item in bundle.sources
        ],
    )


def _docx_body_text(sections: list[TextExportSection]) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for section in sections:
        if section.items:
            blocks.append((section.title, "\n".join(f"• {item}" for item in section.items)))
        elif section.content:
            blocks.append((section.title, _strip_markdown(section.content)))
    return blocks


def render_text_docx(
    bundle: AnalysisBundle,
    *,
    include_cover: bool,
    template: str,
    sections: list[TextExportSection],
    include_references: bool,
) -> bytes:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Pt

    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = section.page_width
    style = document.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)

    if include_cover:
        document.add_heading(bundle.meeting.title, level=0)
        document.add_paragraph("会议成果导出 · 文字版会议纪要")
        if bundle.meeting.starts_at:
            document.add_paragraph(
                f"会议日期：{bundle.meeting.starts_at.strftime('%Y-%m-%d %H:%M')}"
            )
        if bundle.meeting.organizer:
            document.add_paragraph(f"组织方：{bundle.meeting.organizer}")
        if bundle.meeting.location:
            document.add_paragraph(f"地点：{bundle.meeting.location}")
        document.add_page_break()

    body_sections = sections
    if not include_references:
        body_sections = [section for section in sections if section.key != "sources"]
    for title, body in _docx_body_text(body_sections):
        document.add_heading(title, level=1)
        for paragraph_text in body.splitlines():
            document.add_paragraph(paragraph_text)
    if template == "minimal":
        document.add_paragraph()
        document.add_paragraph("—— 本纪要由 AI 会议纪要系统生成 ——")

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def render_text_pdf(
    bundle: AnalysisBundle,
    *,
    include_cover: bool,
    template: str,
    sections: list[TextExportSection],
    include_references: bool,
) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    base = ParagraphStyle(
        "base",
        fontName="STSong-Light",
        fontSize=10.5,
        leading=17,
        wordWrap="CJK",
    )
    heading = ParagraphStyle(
        "heading",
        parent=base,
        fontSize=14,
        leading=20,
        spaceBefore=12,
        spaceAfter=6,
        textColor="#123c53",
    )
    title_style = ParagraphStyle(
        "title",
        parent=base,
        fontSize=22,
        leading=30,
        spaceAfter=18,
        alignment=1,
        textColor="#123c53",
    )
    meta = ParagraphStyle("meta", parent=base, fontSize=9.5, leading=14)

    story: list[Any] = []
    if include_cover:
        story.append(Paragraph(bundle.meeting.title, title_style))
        story.append(Paragraph("会议成果导出 · 文字版会议纪要", base))
        story.append(Spacer(1, 10 * mm))
        if bundle.meeting.starts_at:
            story.append(
                Paragraph(
                    f"会议日期：{bundle.meeting.starts_at.strftime('%Y-%m-%d %H:%M')}",
                    meta,
                )
            )
        if bundle.meeting.organizer:
            story.append(Paragraph(f"组织方：{bundle.meeting.organizer}", meta))
        if bundle.meeting.location:
            story.append(Paragraph(f"地点：{bundle.meeting.location}", meta))
        story.append(Spacer(1, 14 * mm))

    body_sections = sections
    if not include_references:
        body_sections = [section for section in sections if section.key != "sources"]
    for section in body_sections:
        story.append(Paragraph(section.title, heading))
        if section.items:
            for item in section.items:
                story.append(Paragraph(f"• {_escape_paragraph(item)}", base))
        elif section.content:
            for paragraph in _strip_markdown(section.content).splitlines():
                if paragraph.strip():
                    story.append(Paragraph(_escape_paragraph(paragraph), base))
    if template == "minimal":
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph("—— 本纪要由 AI 会议纪要系统生成 ——", meta))

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    document.build(story)
    return buffer.getvalue()


def render_text_file(
    bundle: AnalysisBundle,
    *,
    fmt: str,
    include_cover: bool,
    template: str,
    selected: list[str] | None,
    show_attendee_names: bool,
    include_references: bool,
    include_timestamps: bool,
) -> tuple[bytes, str]:
    sections = compose_export_sections(
        bundle,
        selected=selected,
        show_attendee_names=show_attendee_names,
        include_timestamps=include_timestamps,
    )
    if fmt == "docx":
        content = render_text_docx(
            bundle,
            include_cover=include_cover,
            template=template,
            sections=sections,
            include_references=include_references,
        )
        return content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    content = render_text_pdf(
        bundle,
        include_cover=include_cover,
        template=template,
        sections=sections,
        include_references=include_references,
    )
    return content, "application/pdf"
