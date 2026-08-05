from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api.v1.meeting_verification import _ensure_meeting_access, router
from app.core.auth import AuthContext, OptionalCurrentUserDependency
from app.core.exceptions import ForbiddenError
from app.models.meeting import (
    AnalysisStatus,
    Meeting,
    MeetingQuestion,
    MeetingQuestionType,
    VerificationStatus,
)
from app.schemas.kb import Role
from app.schemas.meeting_review import MeetingQuestionCreate
from app.services.meeting import MeetingService
from app.services.meeting_review import MeetingReviewService


def _meeting(**kwargs: object) -> Meeting:
    starts_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "id": uuid4(),
        "title": "会议",
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(hours=1),
        "verification_status": VerificationStatus.IN_PROGRESS,
        "verification_version": 3,
        "analysis_status": AnalysisStatus.NOT_READY,
    }
    values.update(kwargs)
    return Meeting(**values)


def _question(meeting_id, question_type: MeetingQuestionType) -> MeetingQuestion:
    return MeetingQuestion(
        id=uuid4(),
        meeting_id=meeting_id,
        question_type=question_type,
        content="问题",
        version=1,
    )


def test_question_schema_trims_content_and_rejects_extra() -> None:
    question = MeetingQuestionCreate(question_type="cut_point", content="  问题  ")
    assert question.content == "问题"
    with pytest.raises(ValueError):
        MeetingQuestionCreate(question_type="cut_point", content="问题", extra="x")


def test_eligibility_requires_both_question_types_and_confirmation() -> None:
    meeting = _meeting()
    service = MeetingReviewService(AsyncMock())
    eligibility = service.eligibility(meeting, [], [])
    assert eligibility.can_confirm is False
    assert eligibility.can_submit_analysis is False
    assert "切点问题" in eligibility.missing_conditions[0]
    assert "开放性问题" in eligibility.missing_conditions[1]
    assert "请先确认核验完成" in eligibility.missing_conditions

    meeting.verification_status = VerificationStatus.CONFIRMED
    meeting.analysis_status = AnalysisStatus.READY
    cut = _question(meeting.id, MeetingQuestionType.CUT_POINT)
    opened = _question(meeting.id, MeetingQuestionType.OPEN_ENDED)
    eligibility = service.eligibility(meeting, [cut], [opened])
    assert eligibility.can_submit_analysis is True


def test_mark_changed_resets_confirmation_and_analysis_state() -> None:
    meeting = _meeting(
        verification_status=VerificationStatus.CONFIRMED,
        analysis_status=AnalysisStatus.READY,
        verification_confirmed_at=datetime.now(timezone.utc),
        verification_confirmed_by=uuid4(),
    )
    previous_version = meeting.verification_version
    MeetingReviewService._mark_changed(meeting)
    assert meeting.verification_version == previous_version + 1
    assert meeting.verification_status is VerificationStatus.IN_PROGRESS
    assert meeting.analysis_status is AnalysisStatus.NOT_READY
    assert meeting.verification_confirmed_at is None


def test_confirm_validation_reports_title_time_and_question_gates() -> None:
    service = MeetingReviewService(AsyncMock())
    meeting = _meeting(title="", ends_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    missing = service._confirm_missing_conditions(meeting, [], [])
    assert "会议标题不能为空" in missing
    assert "会议结束时间必须晚于开始时间" in missing
    assert "切点问题" in " ".join(missing)


@pytest.mark.asyncio
async def test_queued_analysis_submission_is_idempotent() -> None:
    class StubService(MeetingReviewService):
        def __init__(self, meeting: Meeting) -> None:
            super().__init__(AsyncMock())
            self.meeting = meeting

        async def get_meeting(self, meeting_id, *, lock=False):
            return self.meeting

        async def list_questions(self, meeting_id):
            return ([], [])

    meeting = _meeting(
        verification_status=VerificationStatus.CONFIRMED,
        analysis_status=AnalysisStatus.QUEUED,
    )
    service = StubService(meeting)
    result = await service.submit_analysis(meeting.id, expected_version=999)
    assert result is meeting
    service.session.commit.assert_not_awaited()


def test_verification_routes_and_delete_version_query_are_stable() -> None:
    paths = {route.path: route.methods for route in router.routes}
    assert "/meetings/{meeting_id}/verification" in paths
    assert "/meetings/{meeting_id}/questions" in paths
    delete_route = next(
        route
        for route in router.routes
        if route.path.endswith("/questions/{question_id}") and "DELETE" in route.methods
    )
    assert any(param.name == "expected_version" for param in delete_route.dependant.query_params)


@pytest.mark.asyncio
async def test_optional_auth_dependency_is_available_and_org_null_is_rejected() -> None:
    assert OptionalCurrentUserDependency is not None
    meeting = _meeting(organization_id=None)
    assert meeting.organization_id is None
    current = AuthContext(uuid4(), uuid4(), "x@example.com", "X", Role.VIEWER, 0)
    with pytest.raises(ForbiddenError):
        await _ensure_meeting_access(AsyncMock(), meeting, current)


@pytest.mark.asyncio
async def test_meeting_service_forwards_organization_filter() -> None:
    service = MeetingService(AsyncMock())
    service.repository.list_active = AsyncMock(return_value=([], 0))
    organization_id = uuid4()
    await service.list(
        page=1,
        page_size=20,
        meeting_status=None,
        analysis_status=None,
        keyword=None,
        starts_at_from=None,
        starts_at_to=None,
        organization_id=organization_id,
    )
    assert service.repository.list_active.await_args.kwargs["organization_id"] == organization_id
