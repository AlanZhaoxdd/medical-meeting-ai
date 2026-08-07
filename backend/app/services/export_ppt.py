from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from app.schemas.export import PptDeckSpec
from app.services.export_bundle import AnalysisBundle, source_index


SLIDE_TYPE_TITLES = {
    "cover": "会议汇报",
    "summary": "会议核心摘要",
    "topics": "主要议题与参会者观点",
    "viewpoints": "参会者观点",
    "cutoff_questions": "切点问题分析",
    "charts": "数据图表",
    "consensus": "共识、分歧与待确认事项",
    "actions": "行动项与下一步建议",
    "sources": "引用来源",
}


def slide_default_title(slide_type: str) -> str:
    return SLIDE_TYPE_TITLES.get(slide_type, slide_type)


def _strip_speaker(bullet: str) -> str:
    return re.sub(r"^\s*[\[（(]?[^\]）)]{0,12}[\]）)]?\s*[:：]\s*", "", bullet).strip()


def _build_deck(
    bundle: AnalysisBundle,
    spec: PptDeckSpec,
    *,
    include_charts: bool,
    include_references: bool,
    anonymous_attendees: bool,
    chart_images: dict[str, bytes],
) -> Any:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]
    navy = RGBColor(0x12, 0x3C, 0x53)
    teal = RGBColor(0x16, 0x8B, 0x82)
    purple = RGBColor(0x6C, 0x4F, 0xD0)
    light = RGBColor(0xEF, 0xF4, 0xF2)
    text_color = RGBColor(0x31, 0x4E, 0x62)
    muted = RGBColor(0x6F, 0x83, 0x90)
    white = RGBColor(0xFF, 0xFF, 0xFF)

    def set_font(run: Any, size: float, color: RGBColor, bold: bool = False) -> None:
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = "Microsoft YaHei"
        try:
            run.font._rPr.set(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}ea",
                "Microsoft YaHei",
            )
        except Exception:
            pass

    def add_header(slide: Any, title: str, page_number: int, total: int) -> None:
        from pptx.util import Inches as In

        band = slide.shapes.add_shape(1, In(0), In(0), presentation.slide_width, In(0.92))
        band.fill.solid()
        band.fill.fore_color.rgb = navy
        band.line.fill.background()
        band.shadow.inherit = False
        title_box = slide.shapes.add_textbox(In(0.55), In(0.18), In(11), In(0.62))
        title_frame = title_box.text_frame
        title_frame.word_wrap = True
        paragraph = title_frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = title
        set_font(run, 24, white, bold=True)

        page_box = slide.shapes.add_textbox(
            In(12.3), In(7.08), In(0.85), In(0.35)
        )
        page_run = page_box.text_frame.paragraphs[0].add_run()
        page_run.text = f"{page_number} / {total}"
        set_font(page_run, 10, muted)

    def add_footer(slide: Any, text: str, color: RGBColor = teal) -> None:
        box = slide.shapes.add_textbox(In(0.55), In(7.08), In(9), In(0.35))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = text
        set_font(run, 10, color)

    def add_bullets(slide: Any, bullets: list[Any], anonymous: bool) -> None:
        box = slide.shapes.add_textbox(In(0.8), In(1.5), In(11.7), In(5.2))
        frame = box.text_frame
        frame.word_wrap = True
        for index, bullet in enumerate(bullets):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.space_after = Pt(10)
            bullet_data = bullet.model_dump() if not isinstance(bullet, dict) else bullet
            text = str(bullet_data.get("text") or "")
            if anonymous:
                text = _strip_speaker(text)
            run = paragraph.add_run()
            run.text = f"•  {text}"
            set_font(run, 18, text_color)

    def add_sources_slide(slide: Any) -> None:
        box = slide.shapes.add_textbox(In(0.8), In(1.5), In(11.7), In(5.4))
        frame = box.text_frame
        frame.word_wrap = True
        sources = bundle.sources
        for index, source in enumerate(sources[:24]):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.space_after = Pt(7)
            run = paragraph.add_run()
            run.text = (
                f"[{source_index(source)}] {source.get('title', '来源')}"
                f"（{source.get('type', '')}）"
            )
            set_font(run, 13, text_color)

    slides = spec.slides
    total = len(slides)
    deck_title = spec.title or bundle.meeting.title
    deck_subtitle = spec.subtitle or ""
    if not deck_subtitle:
        if bundle.meeting.starts_at:
            deck_subtitle = bundle.meeting.starts_at.strftime("%Y-%m-%d")
        if bundle.meeting.organizer:
            deck_subtitle = f"{deck_subtitle} · {bundle.meeting.organizer}" if deck_subtitle else bundle.meeting.organizer

    for index, slide_spec in enumerate(slides, start=1):
        slide = presentation.slides.add_slide(blank)
        slide_type = str(slide_spec.type or "summary")
        title = slide_spec.title or slide_default_title(slide_type)
        if slide_type == "cover":
            from pptx.util import Inches as In

            background = slide.shapes.add_shape(
                1, 0, 0, presentation.slide_width, presentation.slide_height
            )
            background.fill.solid()
            background.fill.fore_color.rgb = navy
            background.line.fill.background()
            background.shadow.inherit = False
            title_box = slide.shapes.add_textbox(In(0.9), In(2.1), In(11.5), In(1.3))
            run = title_box.text_frame.paragraphs[0].add_run()
            run.text = deck_title
            set_font(run, 40, white, bold=True)
            subtitle_box = slide.shapes.add_textbox(In(0.9), In(3.4), In(11.5), In(0.8))
            run = subtitle_box.text_frame.paragraphs[0].add_run()
            run.text = deck_subtitle or "会议成果汇报"
            set_font(run, 18, RGBColor(0xC7, 0xE0, 0xE6))
            note = slide.shapes.add_textbox(In(0.9), In(6.4), In(11.5), In(0.5))
            run = note.text_frame.paragraphs[0].add_run()
            run.text = f"{index} / {total} · AI 会议纪要系统"
            set_font(run, 11, RGBColor(0x9F, 0xC7, 0xC4))
        else:
            add_header(slide, title, index, total)
            if slide_type == "sources" and include_references:
                add_sources_slide(slide)
            else:
                bullets = slide_spec.bullets or []
                add_bullets(slide, bullets, anonymous_attendees)
                if include_charts and slide_spec.chartIds:
                    chart = slide_spec.chartIds[0]
                    png = chart_images.get(chart)
                    if png:
                        slide.shapes.add_picture(
                            BytesIO(png),
                            In(7.2),
                            In(1.6),
                            width=In(5.5),
                        )
                add_footer(slide, f"{deck_title} · {title}")
        notes = slide_spec.speakerNotes or ""
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    return presentation


def render_ppt_bytes(
    bundle: AnalysisBundle,
    spec: PptDeckSpec,
    *,
    include_charts: bool,
    include_references: bool,
    anonymous_attendees: bool,
    chart_images: dict[str, bytes],
) -> bytes:
    presentation = _build_deck(
        bundle,
        spec,
        include_charts=include_charts,
        include_references=include_references,
        anonymous_attendees=anonymous_attendees,
        chart_images=chart_images,
    )
    buffer = BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()
