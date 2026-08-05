from datetime import datetime, timedelta, timezone

from httpx import AsyncClient


def meeting_payload(**overrides: object) -> dict[str, object]:
    starts_at = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    payload: dict[str, object] = {
        "title": "2026 肿瘤精准诊疗峰会",
        "starts_at": starts_at.isoformat(),
        "ends_at": (starts_at + timedelta(hours=8)).isoformat(),
        "location": "上海医学中心",
        "online_url": "https://example.com/live",
        "organizer": "医学会",
        "topic": "肿瘤精准诊疗",
        "description": "面向临床专家的学术会议",
        "cover_url": "https://example.com/cover.png",
    }
    payload.update(overrides)
    return payload


async def create_meeting(client: AsyncClient, **overrides: object) -> dict[str, object]:
    response = await client.post("/api/v1/meetings", json=meeting_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_meeting_has_initial_statuses(client: AsyncClient) -> None:
    meeting = await create_meeting(client)

    assert meeting["meeting_status"] == "draft"
    assert meeting["analysis_status"] == "not_ready"
    assert meeting["title"] == "2026 肿瘤精准诊疗峰会"
    assert meeting["meeting_info"] == {
        "meeting_purpose": None,
        "discussion_topics": None,
        "meeting_date": None,
        "advisor_selection_criteria": None,
        "advisor_names": None,
        "internal_attendees": None,
        "recorder": None,
    }


async def test_analysis_status_is_read_only(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/meetings",
        json=meeting_payload(analysis_status="ready"),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_valid_and_invalid_status_transitions(client: AsyncClient) -> None:
    meeting = await create_meeting(client)
    meeting_id = meeting["id"]

    invalid = await client.patch(
        f"/api/v1/meetings/{meeting_id}/status",
        json={"meeting_status": "completed"},
    )
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "invalid_state_transition"

    for target in ("published", "in_progress", "completed", "archived"):
        response = await client.patch(
            f"/api/v1/meetings/{meeting_id}/status",
            json={"meeting_status": target},
        )
        assert response.status_code == 200, response.text
        assert response.json()["meeting_status"] == target

    blocked_update = await client.patch(
        f"/api/v1/meetings/{meeting_id}", json={"title": "不可编辑"}
    )
    assert blocked_update.status_code == 409
    assert blocked_update.json()["code"] == "meeting_not_editable"


async def test_list_filters_and_pagination(client: AsyncClient) -> None:
    await create_meeting(client, title="心血管进展论坛", organizer="心血管医学会")
    await create_meeting(
        client,
        title="肿瘤进展论坛",
        starts_at="2026-09-01T09:00:00+00:00",
        ends_at="2026-09-01T17:00:00+00:00",
    )

    response = await client.get(
        "/api/v1/meetings",
        params={"keyword": "肿瘤", "analysis_status": "not_ready", "page_size": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert body["total_pages"] == 1
    assert body["items"][0]["title"] == "肿瘤进展论坛"


async def test_update_delete_and_not_found_error(client: AsyncClient) -> None:
    meeting = await create_meeting(client)
    meeting_id = meeting["id"]

    update = await client.patch(
        f"/api/v1/meetings/{meeting_id}",
        json={"location": "北京医学中心", "online_url": None},
    )
    assert update.status_code == 200
    assert update.json()["location"] == "北京医学中心"
    assert update.json()["online_url"] is None

    deleted = await client.delete(f"/api/v1/meetings/{meeting_id}")
    assert deleted.status_code == 204

    missing = await client.get(f"/api/v1/meetings/{meeting_id}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "meeting_not_found"


async def test_time_validation_and_health_check(client: AsyncClient) -> None:
    invalid_time = await client.post(
        "/api/v1/meetings",
        json=meeting_payload(
            starts_at="2026-08-01T09:00:00",
            ends_at="2026-08-01T08:00:00",
        ),
    )
    assert invalid_time.status_code == 422
    assert invalid_time.json()["code"] == "validation_error"

    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "available"}
