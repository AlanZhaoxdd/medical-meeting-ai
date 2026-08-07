from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUserDependency
from app.core.exceptions import ConflictError, NotFoundError
from app.db.session import get_session
from app.models.export import (
    ExportFileFormat,
    ExportRecord,
    ExportStatus,
    ExportType,
    PptOutline,
)
from app.models.meeting import Meeting
from app.schemas.export import (
    ChartSpecRead,
    ExportRecordListRead,
    ExportRecordRead,
    PptDeckSpec,
    PptExportCreate,
    PptOutlineRead,
    PptSlideOut,
    TextExportCreate,
    TextPreviewRead,
)
from app.services.export_bundle import load_analysis_bundle
from app.services.export_chart_service import (
    chart_spec_to_read,
    list_chart_specs,
)
from app.services.export_charts import render_chart_png, render_chart_svg
from app.services.export_model_clients import PptOutlineModelClient
from app.services.export_service import (
    cancel_export_record,
    create_export_record,
    get_export_record,
    list_export_records,
    retry_export_record,
)
from app.services.export_text import build_text_preview, default_file_name
from app.services.storage import ObjectStorage

router = APIRouter(tags=["会议成果导出"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


async def _meeting(
    session: AsyncSession, meeting_id: UUID, organization_id: UUID
) -> Meeting:
    meeting = await session.scalar(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.deleted_at.is_(None))
    )
    if meeting is None or meeting.organization_id != organization_id:
        raise NotFoundError("会议", "meeting_not_found")
    return meeting


def _serialize(
    record: ExportRecord,
    *,
    storage: ObjectStorage | None = None,
    include_download: bool = True,
) -> ExportRecordRead:
    download_url: str | None = None
    if (
        include_download
        and record.status is ExportStatus.COMPLETED
        and record.storage_key
    ):
        try:
            download_url = (storage or ObjectStorage()).presigned_url(record.storage_key)
        except Exception:
            download_url = None
    return ExportRecordRead(
        export_id=record.id,
        meeting_id=record.meeting_id,
        analysis_version=record.analysis_version,
        export_type=record.export_type.value,
        file_format=record.file_format.value if record.file_format else None,
        status=record.status.value,
        progress=record.progress,
        current_stage=record.current_stage,
        message=record.message,
        error_code=record.error_code,
        error_message=record.error_message,
        file_name=record.file_name,
        download_url=download_url,
        config=record.config or {},
        retry_count=record.retry_count,
        created_by=record.created_by,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


async def _active_export_check(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    export_type: ExportType,
) -> None:
    existing = await session.scalar(
        select(ExportRecord)
        .where(
            ExportRecord.meeting_id == meeting_id,
            ExportRecord.export_type == export_type,
            ExportRecord.status.in_(
                [
                    ExportStatus.PENDING,
                    ExportStatus.ANALYZING,
                    ExportStatus.GENERATING,
                    ExportStatus.RENDERING,
                ]
            ),
        )
        .order_by(ExportRecord.created_at.desc())
    )
    if existing is not None:
        raise ConflictError(
            "export_already_running",
            "该会议已有正在进行的导出任务，请等待完成后再发起新导出",
        )


@router.get(
    "/meetings/{meeting_id}/exports",
    response_model=ExportRecordListRead,
    summary="查看导出历史",
)
async def list_exports(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ExportRecordListRead:
    await _meeting(session, meeting_id, current.organization_id)
    items, total = await list_export_records(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
        page=page,
        page_size=page_size,
    )
    return ExportRecordListRead(
        items=[_serialize(record) for record in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/exports/{export_id}", response_model=ExportRecordRead, summary="查询导出任务")
async def get_export(
    export_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> ExportRecordRead:
    record = await get_export_record(session, export_id)
    if record.organization_id != current.organization_id:
        raise NotFoundError("导出记录", "export_not_found")
    return _serialize(record)


@router.post(
    "/meetings/{meeting_id}/exports/text",
    response_model=ExportRecordRead,
    summary="发起文字版会议纪要导出",
)
async def create_text_export(
    meeting_id: UUID,
    payload: TextExportCreate,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> ExportRecordRead:
    meeting = await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    await _active_export_check(session, meeting_id=meeting_id, export_type=ExportType.TEXT)
    fmt = ExportFileFormat(payload.format)
    file_name = payload.file_name or default_file_name(bundle, payload.format)
    record = await create_export_record(
        session,
        organization_id=current.organization_id,
        meeting_id=meeting_id,
        analysis_version=bundle.analysis_version,
        export_type=ExportType.TEXT,
        file_format=fmt,
        config=payload.model_dump(mode="json"),
        created_by=current.user_id,
        file_name=file_name,
    )
    return _serialize(record, include_download=False)


@router.get(
    "/meetings/{meeting_id}/exports/text/preview",
    response_model=TextPreviewRead,
    summary="文字版导出预览（与导出内容一致）",
)
async def preview_text_export(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
    selected: str | None = Query(default=None),
    show_attendee_names: bool = True,
    template: str = "formal",
    include_cover: bool = True,
) -> TextPreviewRead:
    await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    section_keys = (
        [part for part in selected.split(",") if part] if selected else None
    )
    return build_text_preview(
        bundle,
        selected=section_keys,
        show_attendee_names=show_attendee_names,
        template=template,
        include_cover=include_cover,
    )


@router.post(
    "/meetings/{meeting_id}/exports/ppt/outline",
    response_model=ExportRecordRead,
    summary="生成 PPT 大纲（异步）",
)
async def create_ppt_outline(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> ExportRecordRead:
    await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    existing = await session.scalar(
        select(PptOutline).where(
            PptOutline.meeting_id == meeting_id,
            PptOutline.analysis_version == bundle.analysis_version,
        )
    )
    if existing is not None:
        raise ConflictError("ppt_outline_exists", "当前版本 PPT 大纲已存在，可直接预览编辑")
    record = await create_export_record(
        session,
        organization_id=current.organization_id,
        meeting_id=meeting_id,
        analysis_version=bundle.analysis_version,
        export_type=ExportType.PPT,
        file_format=ExportFileFormat.PPTX,
        config={"mode": "outline", "theme": "formal"},
        created_by=current.user_id,
    )
    return _serialize(record, include_download=False)


@router.get(
    "/meetings/{meeting_id}/exports/ppt/outline",
    response_model=PptOutlineRead,
    summary="获取 PPT 大纲",
)
async def get_ppt_outline(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> PptOutlineRead:
    await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    outline = await session.scalar(
        select(PptOutline).where(
            PptOutline.meeting_id == meeting_id,
            PptOutline.analysis_version == bundle.analysis_version,
        )
    )
    if outline is None:
        raise NotFoundError("PPT 大纲", "ppt_outline_not_found")
    return PptOutlineRead(
        id=outline.id,
        meeting_id=outline.meeting_id,
        analysis_version=outline.analysis_version,
        spec=PptDeckSpec.model_validate(
            {
                "title": outline.title,
                "subtitle": outline.subtitle,
                "theme": outline.theme,
                "slides": outline.slides,
            }
        ),
        generated_at=outline.updated_at,
    )


@router.put(
    "/meetings/{meeting_id}/exports/ppt/outline",
    response_model=PptOutlineRead,
    summary="保存编辑后的 PPT 大纲",
)
async def update_ppt_outline(
    meeting_id: UUID,
    payload: PptDeckSpec,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> PptOutlineRead:
    await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    outline = await session.scalar(
        select(PptOutline)
        .where(
            PptOutline.meeting_id == meeting_id,
            PptOutline.analysis_version == bundle.analysis_version,
        )
        .with_for_update()
    )
    if outline is None:
        raise NotFoundError("PPT 大纲", "ppt_outline_not_found")
    outline.title = payload.title
    outline.subtitle = payload.subtitle
    outline.theme = payload.theme
    outline.slides = [slide.model_dump(mode="json") for slide in payload.slides]
    await session.commit()
    return PptOutlineRead(
        id=outline.id,
        meeting_id=outline.meeting_id,
        analysis_version=outline.analysis_version,
        spec=payload,
        generated_at=outline.updated_at,
    )


@router.post(
    "/meetings/{meeting_id}/exports/ppt/outline/regenerate-page",
    response_model=PptOutlineRead,
    summary="重新生成单页大纲",
)
async def regenerate_ppt_page(
    meeting_id: UUID,
    payload: dict[str, Any],
    session: SessionDependency,
    current: CurrentUserDependency,
) -> PptOutlineRead:
    from app.schemas.export import PptDeckSpec as _PptDeckSpec

    page_number = int(payload.get("page_number", 0))
    instruction = payload.get("instruction")
    await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    outline = await session.scalar(
        select(PptOutline).where(
            PptOutline.meeting_id == meeting_id,
            PptOutline.analysis_version == bundle.analysis_version,
        )
    )
    if outline is None:
        raise NotFoundError("PPT 大纲", "ppt_outline_not_found")
    spec = _PptDeckSpec.model_validate(
        {
            "title": outline.title,
            "subtitle": outline.subtitle,
            "theme": outline.theme,
            "slides": outline.slides,
        }
    )
    updated = await PptOutlineModelClient().regenerate_page(
        spec=spec,
        page_number=page_number,
        instruction=str(instruction) if instruction else None,
    )
    outline.title = updated.title
    outline.subtitle = updated.subtitle
    outline.slides = [slide.model_dump(mode="json") for slide in updated.slides]
    await session.commit()
    return PptOutlineRead(
        id=outline.id,
        meeting_id=outline.meeting_id,
        analysis_version=outline.analysis_version,
        spec=updated,
        generated_at=outline.updated_at,
    )


@router.post(
    "/meetings/{meeting_id}/exports/ppt",
    response_model=ExportRecordRead,
    summary="发起 PPT 导出",
)
async def create_ppt_export(
    meeting_id: UUID,
    payload: PptExportCreate,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> ExportRecordRead:
    meeting = await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    outline = await session.scalar(
        select(PptOutline).where(
            PptOutline.meeting_id == meeting_id,
            PptOutline.analysis_version == bundle.analysis_version,
        )
    )
    if outline is None:
        raise ConflictError("ppt_outline_missing", "请先生成 PPT 大纲")
    await _active_export_check(session, meeting_id=meeting_id, export_type=ExportType.PPT)
    slides = payload.slides or [PptSlideOut.model_validate(item) for item in outline.slides]
    config = payload.model_dump(mode="json")
    config["slides"] = [slide.model_dump(mode="json") for slide in slides]
    record = await create_export_record(
        session,
        organization_id=current.organization_id,
        meeting_id=meeting_id,
        analysis_version=bundle.analysis_version,
        export_type=ExportType.PPT,
        file_format=ExportFileFormat.PPTX,
        config=config,
        created_by=current.user_id,
        file_name=payload.file_name or f"{meeting.title}-会议汇报.pptx",
    )
    return _serialize(record, include_download=False)


@router.post(
    "/meetings/{meeting_id}/charts/plan",
    response_model=ExportRecordRead,
    summary="发起图表分析（LLM 分类 + 程序统计）",
)
async def plan_chart_export(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
    chart_type: str = "bar",
    target_question_id: UUID | None = None,
    metric: str = "independent_speakers",
) -> ExportRecordRead:
    await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    await _active_export_check(session, meeting_id=meeting_id, export_type=ExportType.CHART)
    record = await create_export_record(
        session,
        organization_id=current.organization_id,
        meeting_id=meeting_id,
        analysis_version=bundle.analysis_version,
        export_type=ExportType.CHART,
        file_format=ExportFileFormat.PNG,
        config={
            "chart_type": chart_type,
            "target_question_id": str(target_question_id) if target_question_id else None,
            "metric": metric,
        },
        created_by=current.user_id,
    )
    return _serialize(record, include_download=False)


@router.get(
    "/meetings/{meeting_id}/charts",
    response_model=list[ChartSpecRead],
    summary="获取已验证图表数据",
)
async def list_charts(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> list[ChartSpecRead]:
    await _meeting(session, meeting_id, current.organization_id)
    bundle = await load_analysis_bundle(
        session,
        meeting_id=meeting_id,
        organization_id=current.organization_id,
    )
    specs = await list_chart_specs(
        session,
        meeting_id=meeting_id,
        analysis_version=bundle.analysis_version,
    )
    return [chart_spec_to_read(spec) for spec in specs]


@router.get("/meetings/{meeting_id}/charts/{chart_id}/image")
async def chart_image(
    meeting_id: UUID,
    chart_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
    fmt: str = "png",
) -> Response:
    from app.models.export import ChartSpec

    await _meeting(session, meeting_id, current.organization_id)
    spec = await session.scalar(
        select(ChartSpec).where(
            ChartSpec.id == chart_id,
            ChartSpec.meeting_id == meeting_id,
        )
    )
    if spec is None or not spec.valid:
        raise NotFoundError("图表", "chart_not_found")
    data = dict(spec.spec or {})
    if fmt == "svg":
        content = render_chart_svg(data)
        return Response(content=content, media_type="image/svg+xml")
    content = render_chart_png(data)
    return Response(content=content, media_type="image/png")


@router.post("/exports/{export_id}/retry", response_model=ExportRecordRead, summary="重试导出")
async def retry_export(
    export_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> ExportRecordRead:
    record = await retry_export_record(session, export_id)
    if record.organization_id != current.organization_id:
        raise NotFoundError("导出记录", "export_not_found")
    return _serialize(record, include_download=False)


@router.post("/exports/{export_id}/cancel", response_model=ExportRecordRead, summary="取消导出")
async def cancel_export(
    export_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> ExportRecordRead:
    record = await cancel_export_record(session, export_id)
    if record.organization_id != current.organization_id:
        raise NotFoundError("导出记录", "export_not_found")
    return _serialize(record, include_download=False)


@router.get("/exports/{export_id}/download", summary="下载导出文件")
async def download_export(
    export_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> dict[str, str]:
    record = await get_export_record(session, export_id)
    if record.organization_id != current.organization_id:
        raise NotFoundError("导出记录", "export_not_found")
    if record.status is not ExportStatus.COMPLETED or not record.storage_key:
        raise ConflictError("export_not_ready", "导出文件尚未生成")
    storage = ObjectStorage()
    try:
        url = storage.presigned_url(record.storage_key)
    except Exception:
        raise ConflictError("download_link_expired", "下载链接已过期，请重新获取") from None
    return {"url": url, "file_name": record.file_name or "download"}


@router.get(
    "/exports/{export_id}/download/file",
    summary="下载导出文件（API 流式代理）",
)
async def download_export_file(
    export_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> Response:
    record = await get_export_record(session, export_id)
    if record.organization_id != current.organization_id:
        raise NotFoundError("导出记录", "export_not_found")
    if record.status is not ExportStatus.COMPLETED or not record.storage_key:
        raise ConflictError("export_not_ready", "导出文件尚未生成")
    storage = ObjectStorage()
    try:
        content = await storage.get(record.storage_key)
    except Exception:
        raise ConflictError("download_file_missing", "导出文件暂不可用，请重新导出") from None
    file_name = record.file_name or "download"
    return Response(
        content=content,
        media_type=record.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(file_name)}"
            )
        },
    )
