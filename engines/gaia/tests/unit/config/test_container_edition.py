"""Unit tests for edition-conditional Layer assembly (A3).

Verifies that under ``edition == "lite"`` the Container's Layer properties
raise ``EditionUnavailableError`` *without importing* the heavy full-edition
dependencies (trino / pyiceberg), and that ``edition == "full"`` keeps the
original lazy-assembly path.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from ontology.config.container import Container
from ontology.config.settings import settings
from ontology.core.exceptions import EditionUnavailableError


@pytest.fixture
def lite_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "edition", "lite")


@pytest.fixture
def full_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "edition", "full")


# 6 个 Layer property 在 lite 下访问即抛 EditionUnavailableError，不 import 重依赖。
# engine 不在此列——B2 起 lite 版 engine 返回 DuckDBEngine（见 TestLiteEngineAssembly）。
_LITE_UNAVAILABLE_PROPS = [
    "catalog",
    "dataset",
    "index",
    "pipeline",
    "graph_store",
    "geotime_store",
]


class TestLiteLayerAssembly:
    @pytest.mark.parametrize("prop", _LITE_UNAVAILABLE_PROPS)
    def test_layer_properties_raise_under_lite(self, lite_edition: None, prop: str) -> None:
        c = Container()
        with pytest.raises(EditionUnavailableError):
            getattr(c, prop)

    def test_engine_returns_duckdb_under_lite(
        self, lite_edition: None, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        # B2: lite 版 engine 返回 DuckDBEngine（不抛、不 import trino）。
        # 指向临时文件避免污染 ~/.gaia-lite/warehouse.duckdb。
        monkeypatch.setattr(settings, "lite_warehouse_path", str(tmp_path / "w.duckdb"))
        c = Container()
        from ontology.layers.engine.duckdb_engine import DuckDBEngine

        eng = c.engine
        assert isinstance(eng, DuckDBEngine)
        assert c.engine is eng  # 缓存单例
        eng.close()

    def test_service_overrides_still_honored_under_lite(self, lite_edition: None) -> None:
        # service_overrides 优先于 edition 短路（测试/DI 注入不受 edition 影响）。
        c = Container()
        fake_graph = MagicMock()
        c.service_overrides["graph_store"] = fake_graph
        assert c.graph_store is fake_graph
        fake_geo = MagicMock()
        c.service_overrides["geotime_store"] = fake_geo
        assert c.geotime_store is fake_geo


class TestLiteServiceAssemblyNoneLayers:
    """lite 版 4 个核心 Service property 构造期不触达 lite-抛错 Layer。

    B 阶段遗留 gap 修复：ontology_service/object_query_service/action_service/
    datasource_service 构造期原本无条件访问 self.catalog/self.index/self.dataset/
    self.pipeline（lite 抛 EditionUnavailableError），导致 lite 创建本体即炸。
    修复后 lite 分支传 None（Service 内部 lite 路径不访问这些 Layer）。
    本测试验证 lite 下访问这 4 个 Service property 不抛 EditionUnavailableError。
    """

    @pytest.mark.parametrize(
        "service_prop",
        ["ontology_service", "object_query_service", "action_service", "datasource_service"],
    )
    def test_core_service_property_assembles_under_lite(self, lite_edition: None, service_prop: str) -> None:
        # 直接断言：lite 下访问 4 个核心 Service property 不抛 EditionUnavailableError
        # （构造期传 None，不访问 Layer 方法）。metadata/engine 走 lite SQLite/DuckDB
        # 真实路径（可用），session 泄漏由测试进程回收，不影响断言。
        c = Container()
        svc = getattr(c, service_prop)
        assert svc is not None


class TestFullLayerAssembly:
    def test_cached_instance_returned_under_full(self, full_edition: None) -> None:
        # full 模式下 edition 短路不触发；预设缓存实例直接返回（不构造真 Layer）。
        c = Container()
        fake_catalog = MagicMock()
        c._catalog = fake_catalog
        assert c.catalog is fake_catalog
        fake_engine = MagicMock()
        c._engine = fake_engine
        assert c.engine is fake_engine


class TestLiteImportGuarantee:
    """Subprocess test: a fresh interpreter with EDITION=lite must import the
    Container + all heavy-Service modules without pulling trino/pyiceberg.

    In-process ``sys.modules`` checks are unreliable because earlier full-mode
    tests may have already loaded the heavy deps; a fresh subprocess is the
    definitive proof that A3's edition short-circuit + lazy Service imports work.
    """

    def test_lite_does_not_import_heavy_deps(self) -> None:
        script = (
            "import sys\n"
            "from ontology.config.container import Container\n"
            "from ontology.core.exceptions import EditionUnavailableError\n"
            "import ontology.services.object_query_service\n"
            "import ontology.services.datasource_service\n"
            "import ontology.services.action_service\n"
            "import ontology.services.sync_flush_scheduler\n"
            "import ontology.services.time_travel_service\n"
            "import ontology.services.textql.embedding\n"
            "c = Container()\n"
            # B2: lite 版 engine 返回 DuckDBEngine（不抛、不 import trino）。
            # duckdb 本身是惰性 import（在 connection 属性内），仅访问 c.engine 不会触发。
            "assert type(c.engine).__name__ == 'DuckDBEngine', 'engine should be DuckDBEngine under lite'\n"
            "assert 'trino' not in sys.modules, 'trino was imported under lite'\n"
            # dataset 仍抛（Iceberg 层 lite 不可用）。
            "try:\n"
            "    c.dataset\n"
            "except EditionUnavailableError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('dataset did not raise under lite')\n"
            "assert 'trino' not in sys.modules, 'trino was imported under lite'\n"
            "assert 'pyiceberg' not in sys.modules, 'pyiceberg was imported under lite'\n"
            "print('OK')\n"
        )
        env = {"EDITION": "lite"}
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, **env},
            timeout=60,
        )
        assert result.returncode == 0, f"lite subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        assert "OK" in result.stdout
