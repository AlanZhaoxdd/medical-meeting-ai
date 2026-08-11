from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models.export import (
    ChartSelection,
    ExportFileFormat,
    ExportRecord,
    ExportStatus,
    ExportType,
    PptOutline,
)
from app.schemas.export import PptDeckSpec, PptSlideOut
from app.services.export_bundle import load_analysis_bundle
from app.services.export_chart_service import (
    delete_chart_specs_for_plan,
    ensure_default_cutpoint_template,
    list_chart_specs,
    plan_numeric_chart,
)
from app.services.export_model_clients import ChartPlanModelClient, PptOutlineModelClient
from app.services.export_ppt import render_ppt_bytes
from app.services.export_service import claim_export_record, update_export_progress
from app.services.export_text import default_file_name, render_text_file
from app.services.storage import ObjectStorage


async def _run_export(export_id: str) -> dict[str, str]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            record = await claim_export_record(session, UUID(export_id))
            if record is None:
                existing = await session.get(ExportRecord, UUID(export_id))
                status = existing.status.value if existing is not None else "MISSING"
                return {"export_id": export_id, "status": status}
            if record.status in {ExportStatus.COMPLETED, ExportStatus.CANCELLED}:
                return {"export_id": export_id, "status": record.status.value}
            bundle = await load_analysis_bundle(
                session,
                meeting_id=record.meeting_id,
                organization_id=record.organization_id,
            )
            export_type = record.export_type
            if export_type is ExportType.TEXT:
                await _run_text_export(factory, record, bundle)
            elif export_type is ExportType.PPT:
                if record.config.get("mode") == "outline":
                    await _run_ppt_outline(factory, record, bundle)
                else:
                    await _run_ppt_export(factory, record, bundle)
            elif export_type is ExportType.CHART:
                await _run_chart_plan(factory, record, bundle)
            else:
                raise AppException(422, "export_type_invalid", "未知导出类型")
        return {"export_id": export_id, "status": "COMPLETED"}
    except Exception as exc:
        async with factory() as session:
            current = await session.get(ExportRecord, UUID(export_id))
            if current is not None and current.status not in {
                ExportStatus.COMPLETED,
                ExportStatus.CANCELLED,
            }:
                code = getattr(exc, "code", "export_failed")
                message = getattr(exc, "message", str(exc))
                await update_export_progress(
                    session,
                    export_id=UUID(export_id),
                    stage="failed",
                    progress=current.progress,
                    message="导出失败",
                    status=ExportStatus.FAILED,
                    error_code=str(code),
                    error_message=message[:2000],
                    completed=True,
                )
        raise
    finally:
        await engine.dispose()


async def _run_text_export(
    factory: async_sessionmaker,
    record: ExportRecord,
    bundle: object,
) -> None:
    from app.services.export_bundle import AnalysisBundle

    bundle = bundle if isinstance(bundle, AnalysisBundle) else bundle
    config = record.config or {}
    fmt = (record.file_format or ExportFileFormat.DOCX).value
    async with factory() as session:
        await update_export_progress(
            session,
            export_id=record.id,
            stage="rendering",
            progress=60,
            message="正在排版文字版纪要",
        )
    content, content_type = render_text_file(
        bundle,
        fmt=fmt,
        include_cover=bool(config.get("include_cover", True)),
        show_attendee_names=bool(config.get("show_attendee_names", True)),
        include_references=bool(config.get("include_references", True)),
        include_citation_markers=bool(config.get("include_citation_markers", True)),
    )
    file_name = str(config.get("file_name") or default_file_name(bundle, fmt))
    object_key = f"exports/{record.meeting_id}/{record.id}.{fmt}"
    storage = ObjectStorage()
    await storage.put(object_key, content, content_type)
    async with factory() as session:
        await update_export_progress(
            session,
            export_id=record.id,
            stage="completed",
            progress=100,
            message="导出完成",
            status=ExportStatus.COMPLETED,
            completed=True,
            file_name=file_name,
            storage_key=object_key,
            content_type=content_type,
            file_size=len(content),
        )
async def _run_ppt_export(
    factory: async_sessionmaker,
    record: ExportRecord,
    bundle: object,
) -> None:
    from app.services.export_bundle import AnalysisBundle
    from app.services.export_charts import render_chart_png

    bundle = bundle if isinstance(bundle, AnalysisBundle) else bundle
    config = record.config or {}
    async with factory() as session:
        outline = await session.scalar(
            select(PptOutline).where(
                PptOutline.meeting_id == record.meeting_id,
                PptOutline.analysis_version == record.analysis_version,
            )
        )
        if outline is None:
            raise AppException(409, "ppt_outline_missing", "PPT 大纲尚未生成")
        deck = PptDeckSpec.model_validate(
            {
                "title": outline.title,
                "subtitle": outline.subtitle,
                "theme": outline.theme,
                "slides": outline.slides or [],
            }
        )
    async with factory() as session:
        await update_export_progress(
            session,
            export_id=record.id,
            stage="generating",
            progress=45,
            message="正在生成 PPT 内容",
        )

    chart_images: dict[str, bytes] = {}
    chart_interpretations: dict[str, str] = {}
    chart_data: dict[str, dict[str, object]] = {}
    if config.get("include_charts", True):
        async with factory() as session:
            await ensure_default_cutpoint_template(
                session,
                organization_id=record.organization_id,
            )
            await session.commit()
            specs = await list_chart_specs(
                session,
                meeting_id=record.meeting_id,
                analysis_version=record.analysis_version,
            )
        for spec in specs:
            if spec.valid and spec.spec:
                data = dict(spec.spec)
                chart_id = str(spec.id)
                chart_data[chart_id] = data
                chart_images[chart_id] = render_chart_png(data)
                interpretation = data.get("interpretation")
                if interpretation:
                    chart_interpretations[chart_id] = str(interpretation)
    preferred_ids: list[str] = []
    async with factory() as session:
        selection = await session.scalar(
            select(ChartSelection).where(
                ChartSelection.meeting_id == record.meeting_id,
                ChartSelection.analysis_version == record.analysis_version,
            )
        )
        if selection is not None:
            preferred_ids = [str(value) for value in (selection.chart_ids or [])]
    preferred = [chart_id for chart_id in preferred_ids if chart_id in chart_images]
    default_chart = next(iter(chart_images.values()), None)
    slides = deck.slides
    if preferred:
        # Every selected chart gets its own 数据图表 slide. Reuse existing
        # charts slides from the LLM outline, otherwise inject new slides
        # (before 引用来源 when present) so the selection is always visible.
        chart_slide_indexes = [
            index
            for index, slide in enumerate(slides)
            if str(slide.type or "") == "charts"
        ]
        insert_at = next(
            (
                index
                for index, slide in enumerate(slides)
                if str(slide.type or "") == "sources"
            ),
            len(slides),
        )
        for offset, chart_id in enumerate(preferred[:6]):
            if offset < len(chart_slide_indexes):
                slides[chart_slide_indexes[offset]].chartIds = [chart_id]
            else:
                slides.insert(
                    insert_at,
                    PptSlideOut(
                        pageNumber=insert_at + 1,
                        type="charts",
                        title="数据图表",
                        bullets=[],
                        chartIds=[chart_id],
                    ),
                )
                insert_at += 1
        for index, slide in enumerate(slides, start=1):
            slide.pageNumber = index
    else:
        # LLM outlines reference chart ids that may not match persisted
        # ChartSpec ids. Deterministically inject one valid chart per slide
        # when the outline has chartIds, or a single default chart when no
        # chart id matches.
        for slide in slides:
            is_chart_slide = str(slide.type or "") == "charts"
            wanted = [str(chart_id) for chart_id in (slide.chartIds or [])]
            if wanted:
                resolved = [chart_id for chart_id in wanted if chart_id in chart_images]
                if not resolved and default_chart is not None:
                    resolved = [next(iter(chart_images.keys()))]
                slide.chartIds = resolved
            elif default_chart is not None and is_chart_slide:
                slide.chartIds = [next(iter(chart_images.keys()))]

    content = render_ppt_bytes(
        bundle,
        deck,
        include_charts=config.get("include_charts", True),
        include_references=config.get("include_references", True),
        anonymous_attendees=config.get("anonymous_attendees", False),
        chart_images=chart_images,
        chart_interpretations=chart_interpretations,
        chart_data=chart_data,
        report_unit=str(config.get("report_unit") or "").strip() or None,
        presenter=str(config.get("presenter") or "").strip() or None,
    )
    file_name = str(config.get("file_name") or f"{bundle.meeting.title}-会议汇报.pptx")
    object_key = f"exports/{record.meeting_id}/{record.id}.pptx"
    ppt_content_type = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    storage = ObjectStorage()
    await storage.put(
        object_key,
        content,
        ppt_content_type,
    )
    async with factory() as session:
        await update_export_progress(
            session,
            export_id=record.id,
            stage="completed",
            progress=100,
            message="PPT 导出完成",
            status=ExportStatus.COMPLETED,
            completed=True,
            file_name=file_name,
            storage_key=object_key,
            content_type=ppt_content_type,
            file_size=len(content),
        )


async def _run_ppt_outline(
    factory: async_sessionmaker,
    record: ExportRecord,
    bundle: object,
) -> None:
    from sqlalchemy import delete

    from app.models.export import PptOutline
    from app.services.export_bundle import AnalysisBundle

    bundle = bundle if isinstance(bundle, AnalysisBundle) else bundle
    config = record.config or {}
    async with factory() as session:
        await update_export_progress(
            session,
            export_id=record.id,
            stage="generating_outline",
            progress=40,
            message="AI 正在生成 PPT 大纲",
        )
        await session.execute(
            delete(PptOutline).where(
                PptOutline.meeting_id == record.meeting_id,
                PptOutline.analysis_version == record.analysis_version,
            )
        )
        deck = await PptOutlineModelClient().generate(
            {
                "meeting_context": {
                    "title": bundle.meeting.title,
                    "date": (
                        bundle.meeting.starts_at.isoformat()
                        if bundle.meeting.starts_at
                        else None
                    ),
                    "location": bundle.meeting.location,
                    "organizer": bundle.meeting.organizer,
                    "topic": bundle.meeting.topic,
                },
                "minutes": [
                    {
                        "title": module.get("title"),
                        "content": module.get("content"),
                        "items": module.get("items"),
                    }
                    for module in bundle.modules
                ],
                "cutpoint_questions": [
                    {"id": str(q.id), "content": q.content}
                    for q in bundle.questions
                    if q.question_type.value == "cut_point"
                ],
                "open_questions": [
                    {"id": str(q.id), "content": q.content}
                    for q in bundle.questions
                    if q.question_type.value == "open_ended"
                ],
                "sources": bundle.sources,
            }
        )
        outline = PptOutline(
            meeting_id=record.meeting_id,
            organization_id=record.organization_id,
            analysis_version=record.analysis_version,
            title=deck.title,
            subtitle=deck.subtitle,
            theme=config.get("theme", deck.theme),
            slides=[slide.model_dump(mode="json") for slide in deck.slides],
            generated_by=record.created_by,
        )
        session.add(outline)
        await update_export_progress(
            session,
            export_id=record.id,
            stage="completed",
            progress=100,
            message="PPT 大纲生成完成，可预览与编辑",
            status=ExportStatus.COMPLETED,
            completed=True,
        )


async def _run_chart_plan(
    factory: async_sessionmaker,
    record: ExportRecord,
    bundle: object,
) -> None:
    from app.services.export_bundle import AnalysisBundle

    bundle = bundle if isinstance(bundle, AnalysisBundle) else bundle
    config = record.config or {}
    chart_type = str(config.get("chart_type") or "bar")
    template_id = config.get("template_id")
    resolved_template_id = UUID(template_id) if template_id else None
    cutpoint_key = config.get("cutpoint_key")
    prepared_chart = bool(config.get("prepared_chart", True))
    async with factory() as session:
        await update_export_progress(
            session,
            export_id=record.id,
            stage="analyzing",
            progress=35,
            message="AI 正在抽取医学数值、临床人群和原文证据",
        )
    async with factory() as session:
        await delete_chart_specs_for_plan(
            session,
            meeting_id=record.meeting_id,
            analysis_version=record.analysis_version,
            chart_type=chart_type,
            target_id=None,
            template_id=resolved_template_id,
            template_version=config.get("template_version"),
        )
        specs = await plan_numeric_chart(
            session,
            bundle=bundle,
            chart_type=chart_type,
            organization_id=record.organization_id,
            template_id=resolved_template_id,
            template_version=config.get("template_version"),
            cutpoint_key=None if prepared_chart else cutpoint_key,
            indicator_mode=config.get("indicator_mode"),
            count_mode=config.get("count_mode"),
            title=config.get("title"),
            prepared_chart=prepared_chart,
            model_client=ChartPlanModelClient(),
        )
        await session.commit()
        await update_export_progress(
            session,
            export_id=record.id,
            stage="completed",
            progress=100,
            message=f"图表分析完成（生成 {len(specs)} 张图表）",
            status=ExportStatus.COMPLETED,
            completed=True,
        )
