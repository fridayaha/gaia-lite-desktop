"""SKILL.md 变量替换引擎单测 — ${config.param} 占位符替换（fan-out 前由 Manager 渲染）。

覆盖：
- _build_substitution_map：secret 排除 / config 优先于 default / 无值不进映射 / bool→True/False
- _substitute_skill_md_body：body-only 替换 / frontmatter 不动 / 未知 token 保留 / 单次不递归
- _zip_to_tar_strip_top 带 skill_md_substitute：SKILL.md 替换 + 其他文件不变
"""
import io
import tarfile
import zipfile

from app.worker.config_skills import (
    _build_substitution_map,
    _substitute_skill_md_body,
    _zip_to_tar_strip_top,
)


def _params():
    return [
        {"name": "api_key", "type": "string", "secret": True},
        {"name": "endpoint", "type": "string", "default": "https://default.example.com"},
        {"name": "timeout", "type": "number", "default": 10},
        {"name": "verbose", "type": "boolean", "default": False},
        {"name": "method", "type": "select", "options": ["GET", "POST"]},  # 无 default 无 config
    ]


# ── _build_substitution_map ──────────────────────────────────


def test_map_excludes_secret():
    m = _build_substitution_map({"config_params": _params(), "config": {"api_key": "sk-x"}})
    assert "api_key" not in m


def test_map_config_overrides_default():
    m = _build_substitution_map(
        {"config_params": _params(), "config": {"endpoint": "https://cfg.example.com"}}
    )
    assert m["endpoint"] == "https://cfg.example.com"


def test_map_falls_back_to_default():
    m = _build_substitution_map({"config_params": _params(), "config": {}})
    assert m["endpoint"] == "https://default.example.com"
    assert m["timeout"] == "10"
    assert m["verbose"] == "False"


def test_map_bool_true():
    m = _build_substitution_map({"config_params": _params(), "config": {"verbose": True}})
    assert m["verbose"] == "True"


def test_map_skips_no_value_no_default():
    m = _build_substitution_map({"config_params": _params(), "config": {}})
    assert "method" not in m  # 无 config 无 default → 不进映射（token 保留原样）


def test_map_empty_record():
    assert _build_substitution_map({}) == {}


# ── _substitute_skill_md_body ────────────────────────────────


def test_substitute_body_only_frontmatter_intact():
    content = "---\nname: my-skill\nversion: 1.0.0\n---\n\nEndpoint: ${config.endpoint}\n"
    out = _substitute_skill_md_body(content, {"endpoint": "https://x"})
    assert "name: my-skill" in out  # frontmatter 不动
    assert "https://x" in out
    assert "${config.endpoint}" not in out


def test_substitute_unknown_token_preserved():
    content = "EP: ${config.endpoint}\nMissing: ${config.missing}\n"
    out = _substitute_skill_md_body(content, {"endpoint": "https://x"})
    assert "${config.missing}" in out  # 未知 token 原样保留
    assert "https://x" in out


def test_substitute_single_pass_no_recursion():
    # 值本身含 ${config.x} 不应被二次替换
    content = "Val: ${config.endpoint}\n"
    out = _substitute_skill_md_body(content, {"endpoint": "${config.timeout}"})
    assert "${config.timeout}" in out  # 替换出的文本不再被扫


def test_substitute_empty_mapping_returns_original():
    content = "---\nname: x\n---\n${config.endpoint}\n"
    assert _substitute_skill_md_body(content, {}) == content


def test_substitute_no_frontmatter_replaces_all():
    content = "EP: ${config.endpoint}\n"
    assert _substitute_skill_md_body(content, {"endpoint": "https://x"}) == "EP: https://x\n"


def test_substitute_secret_token_preserved_when_not_in_map():
    # secret 参数不进 mapping，${config.api_key} 保留字面量
    content = "Key: ${config.api_key}\nEP: ${config.endpoint}\n"
    out = _substitute_skill_md_body(content, {"endpoint": "https://x"})
    assert "${config.api_key}" in out
    assert "https://x" in out


# ── _zip_to_tar_strip_top 带 skill_md_substitute ─────────────


def _make_zip(skill_md: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("my-skill/SKILL.md", skill_md)
        zf.writestr("my-skill/scripts/run.py", "print('hi')\n")
    return buf.getvalue()


def _extract(tar: bytes, suffix: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:gz") as tf:
        name = next(n for n in tf.getnames() if n.endswith(suffix))
        return tf.extractfile(name).read().decode("utf-8")


def test_zip_substitutes_skill_md_only():
    raw = _make_zip("---\nname: my-skill\n---\n\nEP: ${config.endpoint}\n")
    tar = _zip_to_tar_strip_top(raw, "/dest", skill_md_substitute={"endpoint": "https://x"})
    md = _extract(tar, "SKILL.md")
    run = _extract(tar, "run.py")
    assert "https://x" in md
    assert "${config.endpoint}" not in md
    assert run == "print('hi')\n"  # 其他文件不变


def test_zip_no_substitute_when_mapping_none():
    raw = _make_zip("EP: ${config.endpoint}\n")
    tar = _zip_to_tar_strip_top(raw, "/dest")  # 不传 substitute
    md = _extract(tar, "SKILL.md")
    assert "${config.endpoint}" in md  # 原样
