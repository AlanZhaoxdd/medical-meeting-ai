from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models.export import (
    ExportFileFormat,
    ExportRecord,
    ExportStatus,
    ExportType,
    PptOutline,
)
from app.schemas.export import PptDeckSpec
from app.services.export_bundle import load_analysis_bundle
from app.services.export_chart_service import (
    delete_chart_specs_for_plan,
    list_chart_specs,
    plan_charts,
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
        template=config.get("template", "formal"),
        selected=config.get("sections"),
        show_attendee_names=bool(config.get("show_attendee_names", True)),
        include_references=bool(config.get("include_references", True)),
        include_timestamps=bool(config.get("include_timestamps", False)),
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
    if config.get("include_charts", True):
        async with factory() as session:
            specs = await list_chart_specs(
                session,
                meeting_id=record.meeting_id,
                analysis_version=record.analysis_version,
            )
        for spec in specs:
            if spec.valid and spec.spec:
                chart_images[str(spec.id)] = render_chart_png(dict(spec.spec))
    # LLM outlines reference chart ids that may not match persisted ChartSpec
    # ids. Deterministically inject one valid chart per slide when the outline
    # has chartIds, or a single default chart when no chart id matches.
    default_chart = next(iter(chart_images.values()), None)
    slides = deck.slides
    for slide in slides:
        is_chart_slide = slide.type == "charts"
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
    target_id = config.get("target_question_id")
    metric = str(config.get("metric") or "independent_speakers")
    async with factory() as session:
        await update_export_progress(
            session,
            export_id=record.id,
            stage="analyzing",
            progress=35,
            message="AI 正在识别可统计的主题、证据和立场",
        )
    async with factory() as session:
        await delete_chart_specs_for_plan(
            session,
            meeting_id=record.meeting_id,
            analysis_version=record.analysis_version,
            chart_type=chart_type,
            target_id=UUID(target_id) if target_id else None,
        )
        specs = await plan_charts(
            session,
            bundle=bundle,
            chart_type=chart_type,
            target_question_id=UUID(target_id) if target_id else None,
            metric=metric,
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
