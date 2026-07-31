"""Unit tests for AnalysisRecordStore + evidence chain persistence (M6)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.services.analysis_record_store import AnalysisRecordStore


@pytest.fixture
def mock_session() -> MagicMock:
    s = MagicMock()
    s.add = MagicMock()
    s.commit = AsyncMock()
    return s


@pytest.fixture
def store(mock_session) -> AnalysisRecordStore:
    return AnalysisRecordStore(mock_session)


class TestSave:
    async def test_save_persists_record_and_returns_id(self, store, mock_session):
        record_id = await store.save(
            ontology_id="ont-1",
            object_set_ir={"type": "static", "objects": ["v1"]},
            result_summary={"steps": 2, "total_vids": 1},
            evidence_pointers={"matched_vids": ["v1"]},
        )
        assert record_id  # UUID hex
        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()
        # 验证传入的 model 字段。
        model = mock_session.add.call_args.args[0]
        assert model.ontology_id == "ont-1"
        assert model.object_set_ir["type"] == "static"
        assert model.principal == "anonymous"


class TestGet:
    async def test_get_returns_record_when_found(self, store, mock_session):
        from datetime import UTC, datetime

        from ontology.core.models.ontology import AnalysisRecordModel

        ts = datetime(2026, 7, 2, tzinfo=UTC)
        model = AnalysisRecordModel(
            id="rec-1", ontology_id="ont-1", principal="alice",
            object_set_ir={"type": "static"}, result_summary={"steps": 1},
            evidence_pointers={}, created_at=ts,
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=model)
        mock_session.execute = AsyncMock(return_value=result_mock)

        record = await store.get("rec-1")
        assert record is not None
        assert record.id == "rec-1"
        assert record.principal == "alice"

    async def test_get_returns_none_when_not_found(self, store, mock_session):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=result_mock)

        record = await store.get("missing")
        assert record is None
