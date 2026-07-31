"""Unit tests for /ai/scaffold (BuildWith: scaffold ObjectType from dataset).

Covers two layers:
1. ``_sanitize_scaffold_result`` — pure-function hallucination guards
   (drop unknown columns, backfill missing, repair dangling key refs).
2. The ``/ai/scaffold`` endpoint SSE stream — uses pydantic-ai's TestModel
   with ``custom_output_args`` to drive the structured Tool Output path
   offline, and asserts the SSE frames + final sanitization.

See docs/design/buildwith-object-scaffolding.md.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from ontology.config.settings import settings
from ontology.main import app
from ontology.routes.ai import (
    ScaffoldColumn,
    ScaffoldProperty,
    ScaffoldResult,
    _build_scaffold_prompt,
    _sanitize_scaffold_result,
)

# ── Fixtures ──


def _columns() -> list[ScaffoldColumn]:
    return [
        ScaffoldColumn(name="customer_id", type="bigint", nullable=False),
        ScaffoldColumn(name="name", type="varchar", nullable=False),
        ScaffoldColumn(name="email", type="varchar", nullable=True),
        ScaffoldColumn(name="created_at", type="timestamp", nullable=True),
    ]


def _result(
    *,
    display_name: str = "客户",
    api_name: str = "Customer",
    description: str = "客户信息",
    primary_key_column: str = "customer_id",
    title_column: str | None = "name",
    properties: list[ScaffoldProperty] | None = None,
) -> ScaffoldResult:
    if properties is None:
        properties = [
            ScaffoldProperty(
                source_column="customer_id",
                display_name="客户ID",
                is_primary_key=True,
            ),
            ScaffoldProperty(
                source_column="name",
                display_name="姓名",
                is_title_property=True,
                searchable=True,
            ),
            ScaffoldProperty(source_column="email", display_name="邮箱", searchable=True),
            ScaffoldProperty(source_column="created_at", display_name="创建时间"),
        ]
    return ScaffoldResult(
        display_name=display_name,
        api_name=api_name,
        description=description,
        primary_key_column=primary_key_column,
        title_column=title_column,
        properties=properties,
    )


@pytest.fixture
def test_model(monkeypatch: pytest.MonkeyPatch) -> TestModel:
    """Patch settings.ai_model to a TestModel (offline, deterministic)."""
    model = TestModel()
    monkeypatch.setattr(settings, "ai_model", model)
    return model


# ── _build_scaffold_prompt ──


class TestBuildPrompt:
    def test_renders_columns_with_nullable(self) -> None:
        req_columns = _columns()
        prompt = _build_scaffold_prompt(
            type(
                "_R",
                (),
                {
                    "dataset_api_name": "customer",
                    "dataset_display_name": "客户表",
                    "storage_type": "MANAGED",
                    "columns": req_columns,
                },
            )()  # type: ignore[arg-type]
        )
        assert "数据集名：customer" in prompt
        assert "数据集展示名：客户表" in prompt
        assert "存储类型：MANAGED" in prompt
        assert "- customer_id | bigint | nullable=false" in prompt
        assert "- email | varchar | nullable=true" in prompt

    def test_falls_back_display_name_to_api_name(self) -> None:
        prompt = _build_scaffold_prompt(
            type(
                "_R",
                (),
                {
                    "dataset_api_name": "orders",
                    "dataset_display_name": "",
                    "storage_type": "MANAGED",
                    "columns": [],
                },
            )()  # type: ignore[arg-type]
        )
        assert "数据集展示名：orders" in prompt


# ── _sanitize_scaffold_result ──


class TestSanitize:
    def test_passes_through_valid_result(self) -> None:
        """A well-formed result is returned with columns preserved."""
        result = _result()
        sanitized = _sanitize_scaffold_result(result, _columns())
        assert sanitized.display_name == "客户"
        assert sanitized.api_name == "Customer"
        assert sanitized.primary_key_column == "customer_id"
        assert sanitized.title_column == "name"
        assert len(sanitized.properties) == 4
        # PK / title flags reconciled
        pk = next(p for p in sanitized.properties if p.source_column == "customer_id")
        assert pk.is_primary_key is True
        title = next(p for p in sanitized.properties if p.source_column == "name")
        assert title.is_title_property is True

    def test_drops_hallucinated_columns(self) -> None:
        """Properties whose source_column isn't in the input are dropped."""
        result = _result(
            properties=[
                ScaffoldProperty(source_column="customer_id", display_name="ID", is_primary_key=True),
                ScaffoldProperty(source_column="name", display_name="姓名", is_title_property=True),
                ScaffoldProperty(source_column="email", display_name="邮箱"),
                ScaffoldProperty(source_column="created_at", display_name="创建时间"),
                # hallucinated — not in dataset
                ScaffoldProperty(source_column="phantom_col", display_name="幽灵列"),
            ]
        )
        sanitized = _sanitize_scaffold_result(result, _columns())
        names = {p.source_column for p in sanitized.properties}
        assert "phantom_col" not in names
        assert names == {"customer_id", "name", "email", "created_at"}

    def test_backfills_missing_columns(self) -> None:
        """Columns present in the input but missing from the LLM output are
        added as deterministic skeletons."""
        result = _result(
            properties=[
                ScaffoldProperty(source_column="customer_id", display_name="ID", is_primary_key=True),
            ]
        )
        sanitized = _sanitize_scaffold_result(result, _columns())
        names = {p.source_column for p in sanitized.properties}
        assert names == {"customer_id", "name", "email", "created_at"}
        # backfilled skeleton keeps column name as display_name, not searchable
        backfilled = next(p for p in sanitized.properties if p.source_column == "email")
        assert backfilled.display_name == "email"
        assert backfilled.searchable is False
        assert backfilled.is_primary_key is False

    def test_clears_dangling_primary_key(self) -> None:
        """primary_key_column not matching any property → cleared, flags reset."""
        result = _result(primary_key_column="nonexistent", title_column=None)
        result.properties = [
            ScaffoldProperty(source_column="customer_id", display_name="ID", is_primary_key=True),
            ScaffoldProperty(source_column="name", display_name="姓名"),
            ScaffoldProperty(source_column="email", display_name="邮箱"),
            ScaffoldProperty(source_column="created_at", display_name="创建时间"),
        ]
        sanitized = _sanitize_scaffold_result(result, _columns())
        assert sanitized.primary_key_column == ""
        assert all(not p.is_primary_key for p in sanitized.properties)

    def test_clears_dangling_title_column(self) -> None:
        """title_column not matching any property → None, frontend uses PK."""
        result = _result(title_column="nonexistent")
        sanitized = _sanitize_scaffold_result(result, _columns())
        assert sanitized.title_column is None
        assert all(not p.is_title_property for p in sanitized.properties)

    def test_preserves_null_title_column(self) -> None:
        """title_column=None (no suitable title) is preserved."""
        result = _result(title_column=None)
        result.properties = [
            ScaffoldProperty(source_column="customer_id", display_name="ID", is_primary_key=True),
            ScaffoldProperty(source_column="name", display_name="姓名"),
            ScaffoldProperty(source_column="email", display_name="邮箱"),
            ScaffoldProperty(source_column="created_at", display_name="创建时间"),
        ]
        sanitized = _sanitize_scaffold_result(result, _columns())
        assert sanitized.title_column is None
        # PK flag still set correctly
        pk = next(p for p in sanitized.properties if p.source_column == "customer_id")
        assert pk.is_primary_key is True

    def test_reconciles_flags_after_repair(self) -> None:
        """When key columns are repaired, per-property flags are re-derived
        from the repaired key column names, not the LLM's original flags."""
        # LLM says customer_id is PK AND name is PK (contradiction) + title dangling
        result = _result(primary_key_column="customer_id", title_column="name")
        result.properties = [
            ScaffoldProperty(source_column="customer_id", display_name="ID", is_primary_key=True),
            ScaffoldProperty(source_column="name", display_name="姓名", is_primary_key=True, is_title_property=True),
            ScaffoldProperty(source_column="email", display_name="邮箱", is_title_property=True),
            ScaffoldProperty(source_column="created_at", display_name="创建时间"),
        ]
        sanitized = _sanitize_scaffold_result(result, _columns())
        pk_props = [p for p in sanitized.properties if p.is_primary_key]
        title_props = [p for p in sanitized.properties if p.is_title_property]
        assert len(pk_props) == 1
        assert pk_props[0].source_column == "customer_id"
        assert len(title_props) == 1
        assert title_props[0].source_column == "name"


# ── /ai/scaffold endpoint (SSE stream) ──


class TestScaffoldEndpoint:
    def test_streams_sanitized_scaffold_result(self, test_model: TestModel) -> None:
        """The endpoint streams SSE frames; the final frame is a sanitized
        ScaffoldResult JSON, terminated by [DONE]."""
        # Drive the structured Tool Output path: custom_output_args becomes
        # the output tool's arguments (the ScaffoldResult payload).
        test_model.custom_output_args = {
            "display_name": "客户",
            "api_name": "Customer",
            "description": "客户信息",
            "primary_key_column": "customer_id",
            "title_column": "name",
            "properties": [
                {"source_column": "customer_id", "display_name": "客户ID", "is_primary_key": True},
                {"source_column": "name", "display_name": "姓名", "is_title_property": True, "searchable": True},
                {"source_column": "email", "display_name": "邮箱", "searchable": True},
                {"source_column": "created_at", "display_name": "创建时间"},
                # hallucinated column — must be dropped by sanitization
                {"source_column": "phantom", "display_name": "幽灵"},
            ],
        }

        client = TestClient(app)
        response = client.post(
            "/ai/scaffold",
            json={
                "dataset_api_name": "customer",
                "dataset_display_name": "客户表",
                "storage_type": "MANAGED",
                "columns": [
                    {"name": "customer_id", "type": "bigint", "nullable": False},
                    {"name": "name", "type": "varchar", "nullable": False},
                    {"name": "email", "type": "varchar", "nullable": True},
                    {"name": "created_at", "type": "timestamp", "nullable": True},
                ],
            },
        )
        assert response.status_code == 200

        # Parse SSE frames: lines starting with "data: "
        frames: list[Any] = []
        for line in response.text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            payload = line[len("data: ") :]
            if payload == "[DONE]":
                continue
            frames.append(json.loads(payload))

        assert len(frames) >= 1
        # The last frame is the complete (sanitized) result.
        last = frames[-1]
        assert last["display_name"] == "客户"
        assert last["api_name"] == "Customer"
        assert last["primary_key_column"] == "customer_id"
        assert last["title_column"] == "name"
        prop_names = {p["source_column"] for p in last["properties"]}
        assert "phantom" not in prop_names  # hallucinated column dropped
        assert prop_names == {"customer_id", "name", "email", "created_at"}

    def test_surfaces_error_in_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM failures are surfaced as an SSE error frame, not a 500.

        We monkeypatch ``stream_structured`` to raise — the endpoint's
        try/except must convert the exception into an in-stream ``error``
        SSE frame (HTTP stays 200 so the client always gets a stream).
        """
        from ontology.routes import ai as ai_routes

        async def _failing(*args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover — unreachable, makes it an async gen

        monkeypatch.setattr(ai_routes, "stream_structured", _failing)

        client = TestClient(app)
        response = client.post(
            "/ai/scaffold",
            json={
                "dataset_api_name": "customer",
                "columns": [{"name": "id", "type": "bigint", "nullable": False}],
            },
        )
        assert response.status_code == 200  # error is in-stream, not HTTP
        assert "error" in response.text
        assert "model unavailable" in response.text
