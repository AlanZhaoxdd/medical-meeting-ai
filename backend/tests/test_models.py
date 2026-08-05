import runpy
from pathlib import Path

import sqlalchemy as sa
from pytest import MonkeyPatch
from sqlalchemy.dialects import postgresql

from app.models.meeting import AnalysisStatus, Meeting, MeetingStatus


def test_postgres_enums_persist_status_values() -> None:
    meeting_status_type = Meeting.__table__.c.meeting_status.type
    analysis_status_type = Meeting.__table__.c.analysis_status.type

    assert meeting_status_type.bind_processor(postgresql.dialect())(MeetingStatus.DRAFT) == "draft"
    assert (
        analysis_status_type.bind_processor(postgresql.dialect())(AnalysisStatus.NOT_READY)
        == "not_ready"
    )


def test_meeting_info_is_non_nullable_jsonb_with_python_default() -> None:
    column = Meeting.__table__.c.meeting_info

    assert isinstance(column.type, postgresql.JSONB)
    assert column.nullable is False
    assert column.default is not None


def test_meeting_info_migration_backfills_historical_rows(monkeypatch: MonkeyPatch) -> None:
    migration = runpy.run_path(
        str(
            Path(__file__).parents[1]
            / "alembic/versions/20260803_0007_meeting_info.py"
        )
    )
    added: list[tuple[str, sa.Column[object]]] = []

    def capture_add_column(table_name: str, column: sa.Column[object]) -> None:
        added.append((table_name, column))

    monkeypatch.setattr(migration["op"], "add_column", capture_add_column)
    migration["upgrade"]()

    assert len(added) == 1
    table_name, column = added[0]
    assert table_name == "meetings"
    assert column.name == "meeting_info"
    assert isinstance(column.type, postgresql.JSONB)
    assert column.nullable is False
    assert str(column.server_default.arg) == "'{}'::jsonb"
