"""_parse_zip 的 engine 归一测试。

manifest canonical 写法是小写字符串（"hermes"/"openclaw"），但下游 view.engine
期望大写数组。`_normalize_engine` 负责收敛 string/array/缺省 → ["HERMES"-style]。
"""

from app.api.agent_skills import _normalize_engine, _parse_zip


def _make_zip(manifest: dict | None) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: x\ndescription: d\n---\n# x\n")
        if manifest is not None:
            zf.writestr("manifest.json", __import__("json").dumps(manifest))
    return buf.getvalue()


class TestNormalizeEngine:
    def test_string_lower_to_upper_array(self):
        assert _normalize_engine("hermes") == ["HERMES"]
        assert _normalize_engine("openclaw") == ["OPENCLAW"]

    def test_string_upper_passthrough_upper(self):
        assert _normalize_engine("HERMES") == ["HERMES"]

    def test_array_normalized_to_upper(self):
        assert _normalize_engine(["hermes"]) == ["HERMES"]
        assert _normalize_engine(["hermes", "openclaw"]) == ["HERMES", "OPENCLAW"]

    def test_empty_array_defaults(self):
        assert _normalize_engine([]) == ["HERMES"]

    def test_none_or_missing_defaults(self):
        assert _normalize_engine(None) == ["HERMES"]
        assert _normalize_engine("") == ["HERMES"]

    def test_non_str_non_list_defaults(self):
        assert _normalize_engine(123) == ["HERMES"]


class TestParseZipEngine:
    def test_manifest_string_engine_becomes_upper_array(self):
        z = _make_zip({"name": "x", "engine": "hermes", "type": "skill"})
        view, _warnings, _ = _parse_zip(z)
        assert view["engine"] == ["HERMES"]

    def test_manifest_array_engine_normalized_upper(self):
        z = _make_zip({"name": "x", "engine": ["openclaw"]})
        view, _warnings, _ = _parse_zip(z)
        assert view["engine"] == ["OPENCLAW"]

    def test_manifest_missing_engine_defaults_hermes(self):
        z = _make_zip({"name": "x"})
        view, _warnings, _ = _parse_zip(z)
        assert view["engine"] == ["HERMES"]
