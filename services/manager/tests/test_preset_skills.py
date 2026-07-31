"""出厂预制 skill（platform preset）单测 — 真 DB 断言写入 + fan-out 调用验证。

覆盖：
- prefill_skill_config：空配置注入 7 个 preset；已有同名不重复
- create_definition：空 skill_config → DB 写入 7 preset + MinIO 存 7 zip
- create_definition：显式 skill_config → 不注入 preset（尊重调用方意图）
- _seed_skills：从 MinIO 取 zip 解压到新 profile home（修原 no-op）
- catalog 过滤：godmode/obliteratus 不在 list_skills 返回
"""

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from app.models import AgentDefinition, User, UserGroup
from app.schemas import AgentDefinitionCreate
from app.services import definition_service
from app.services.preset_skills import (
    BANNED_SKILL_NAMES,
    build_preset_zip,
    prefill_skill_config,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings

V3_TABLES = [
    "agent_instance_channels",
    "agent_instances",
    "agent_versions",
    "agent_definitions",
    "resource_pools",
    "agent_deployments",
    "user_group_members",
    "user_groups",
]

PRESET_NAMES = [
    "plan",
    "searxng-search",
    "concept-diagrams",
    "fastmcp",
    "one-three-one-rule",
    "im-channel-push",
    "current-user-info",
]


# ── 纯单元：prefill_skill_config / build_preset_zip ──────────────────


def test_prefill_empty_config_injects_7_presets():
    sc = prefill_skill_config(None)
    names = [s["name"] for s in sc["skills"]]
    assert names == PRESET_NAMES
    for s in sc["skills"]:
        assert s["source"] == "preset"
        assert s["builtin"] is True
        assert s["enabled"] is True
        assert s["id"].startswith("preset-")
    # order 含全部 preset id
    assert len(sc["order"]) == len(PRESET_NAMES)


def test_prefill_respects_existing_skills_no_dup():
    existing = {
        "skills": [{"id": "x", "name": "plan", "source": "local", "enabled": True}],
        "order": ["x"],
    }
    sc = prefill_skill_config(existing)
    names = [s["name"] for s in sc["skills"]]
    # plan 已存在 → 不重复注入；其余 6 个 preset 追加
    assert names.count("plan") == 1
    assert set(names) == set(PRESET_NAMES)
    # 原 local plan 记录保留（source=local）
    plan = next(s for s in sc["skills"] if s["name"] == "plan")
    assert plan["source"] == "local"


def test_prefill_disabled_preset_added_to_disabled_list():
    """enabled_default=False 的 preset 应进 disabled 列表（便于审计）。"""
    sc = prefill_skill_config({"skills": [], "order": []})
    # 默认全 enabled → disabled 为空
    assert sc["disabled"] == []


def test_build_preset_zip_contains_skill_md():
    z = build_preset_zip("plan")
    assert z is not None and len(z) > 0
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = zf.namelist()
    assert any(n.endswith("SKILL.md") for n in names)
    assert any(n.startswith("plan/") for n in names)


def test_build_preset_zip_missing_returns_none():
    assert build_preset_zip("nonexistent-skill") is None


def test_banned_set_contains_safety_risks():
    assert "godmode" in BANNED_SKILL_NAMES
    assert "obliteratus" in BANNED_SKILL_NAMES


def test_ordered_skills_filters_banned():
    """list_skills 的 engineDeployed=False 路径（_ordered_skills）剔除禁用 skill。"""
    from app.api.agent_skills import _ordered_skills

    sc = {
        "skills": [
            {"id": "a", "name": "plan", "source": "preset", "enabled": True},
            {"id": "b", "name": "godmode", "source": "local", "enabled": True},
            {"id": "c", "name": "obliteratus", "source": "local", "enabled": True},
        ],
        "order": ["a", "b", "c"],
    }
    views = _ordered_skills(sc)
    names = [v["name"] for v in views]
    assert "plan" in names
    assert "godmode" not in names
    assert "obliteratus" not in names


def test_skill_view_exposes_source_field():
    """_skill_view 暴露 source，供前端渲染「预制」标签。"""
    from app.api.agent_skills import _skill_view

    v = _skill_view({"id": "preset-plan", "name": "plan", "source": "preset", "builtin": True})
    assert v["source"] == "preset"
    assert v["builtin"] is True


# ── 真 DB：create_definition 预填 ──────────────────────────────────


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    user = User(
        username=f"ps_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    g = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    session.add(g)
    await session.flush()
    g.litellm_team_id = str(g.id)
    await session.commit()
    await session.refresh(g)
    yield session, user, g
    await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
    for t in V3_TABLES:
        await session.execute(text(f"DELETE FROM {t}"))
    await session.delete(user)
    await session.delete(g)
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest.fixture
def mock_archiver_save(monkeypatch):
    """mock archiver.save_skill_zip 记录调用，不真实连 MinIO。"""
    from app.worker.minio_archiver import archiver

    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        archiver,
        "save_skill_zip",
        lambda did, name, z: saved.append((did, name)) or None,
    )
    return saved


async def test_create_definition_prefills_presets_and_saves_zips(db, mock_archiver_save):
    session, user, g = db
    # 空 skill_config → 预填 preset
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"d_{uuid.uuid4().hex[:8]}",
            group_id=g.id,
            engine_type="HERMES",
        ),
        user.id,
    )
    # DB 真实写入断言：skill_config 含全部 preset
    row = (
        await session.execute(select(AgentDefinition).where(AgentDefinition.id == d.id))
    ).scalar_one()
    sc = row.skill_config
    names = [s["name"] for s in sc["skills"]]
    assert set(names) == set(PRESET_NAMES)
    assert all(s["source"] == "preset" for s in sc["skills"])
    # MinIO 存了全部 preset zip（按 definition_id 隔离）
    assert len(mock_archiver_save) == len(PRESET_NAMES)
    saved_names = {n for _, n in mock_archiver_save}
    assert saved_names == set(PRESET_NAMES)
    assert all(did == str(d.id) for did, _ in mock_archiver_save)


async def test_create_definition_with_explicit_skills_no_prefill(db, mock_archiver_save):
    session, user, g = db
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"d_{uuid.uuid4().hex[:8]}",
            group_id=g.id,
            engine_type="HERMES",
            skill_config={
                "skills": [{"id": "x", "name": "custom-skill", "enabled": True, "source": "local"}],
                "order": ["x"],
            },
        ),
        user.id,
    )
    row = (
        await session.execute(select(AgentDefinition).where(AgentDefinition.id == d.id))
    ).scalar_one()
    names = [s["name"] for s in row.skill_config["skills"]]
    # 显式传入 → 不注入 preset，只保留 custom-skill
    assert names == ["custom-skill"]
    # 也不存 preset zip
    assert mock_archiver_save == []


# ── _render_skills_block：external_dirs + disabled ───────────────────


def test_render_skills_block_with_external_dirs_and_disabled():
    from app.worker._common import render_skills_block as _render_skills_block

    block = _render_skills_block(["godmode"], "abc12345-6789-def")
    assert "external_dirs:" in block
    assert "/opt/data/skills/abc12345-6789-def" in block
    assert "disabled:" in block
    assert "- godmode" in block


def test_render_skills_block_no_definition_id_omits_external_dirs():
    from app.worker._common import render_skills_block as _render_skills_block

    block = _render_skills_block([], None)
    assert "external_dirs" not in block
    assert "disabled: []" in block


def test_build_profile_config_yaml_emits_external_dirs():
    from app.worker._common import build_profile_config_yaml as _build_profile_config_yaml

    yaml = _build_profile_config_yaml(
        {"litellm": {"model": "gpt-4o"}},
        {"skills": [{"name": "plan", "enabled": False}], "disabled": ["plan"]},
        "def-xyz-123",
    )
    assert "external_dirs:" in yaml
    assert "/opt/data/skills/def-xyz-123" in yaml
    assert "- plan" in yaml  # disabled


def test_build_profile_config_yaml_context_length():
    """context_length 显式写入可让 hermes 跳过 model_metadata 探针；非正整数省略。"""
    from app.worker._common import build_profile_config_yaml as _build_profile_config_yaml

    def _body(yaml_text: str) -> str:
        # 去掉模板注释行（注释里提到 "context_length"，会干扰 "not in" 断言）
        return "\n".join(l for l in yaml_text.splitlines() if not l.lstrip().startswith("#"))

    # 1) 正整数 → 写入 model 块
    yaml = _build_profile_config_yaml(
        {"litellm": {"model": "deepseek-v4-flash-260425", "context_length": 1000000}},
        {},
    )
    assert "  context_length: 1000000" in yaml
    # 仍位于 model: 块内（2 空格缩进），不破坏后续 security: 顶层键
    expected = (
        "model:\n  provider: openai-api\n  default: deepseek-v4-flash-260425\n"
        "  context_length: 1000000\nsecurity:"
    )
    assert expected in yaml

    # 2) 未设 → 不出现 context_length 行，default 后直接 security:
    yaml2 = _build_profile_config_yaml({"litellm": {"model": "gpt-4o"}}, {})
    assert "context_length" not in _body(yaml2)
    assert "  default: gpt-4o\nsecurity:" in yaml2

    # 3) 非法值（字符串/bool/负数）→ 省略
    for bad in ("abc", True, -1, 0):
        yaml3 = _build_profile_config_yaml(
            {"litellm": {"model": "gpt-4o", "context_length": bad}}, {}
        )
        assert "context_length" not in _body(yaml3)

    # 4) 数字字符串 → 容错为 int
    yaml4 = _build_profile_config_yaml(
        {"litellm": {"model": "gpt-4o", "context_length": "200000"}}, {}
    )
    assert "  context_length: 200000" in yaml4


def test_build_profile_config_yaml_browser_sandbox_disabled_by_default():
    """未启用 browser_sandbox → 不出现 browser toolset / cdp_url"""
    from app.worker._common import build_profile_config_yaml as _b

    yaml = _b({"litellm": {"model": "gpt-4o"}}, {})
    assert "browser:" not in yaml
    assert "cdp_url" not in yaml
    assert "- browser" not in yaml


def test_build_profile_config_yaml_browser_sandbox_enabled():
    """启用 browser_sandbox → api_server 追加 browser + browser.cdp_url 指向 browser Pod 代理"""
    from app.worker._common import build_profile_config_yaml as _b
    from app.worker.k8s_manager import _browser_name

    from pkg.common.config import settings

    agent_id = "550e8400-e29b-41d4-a716-446655440000"
    profile_name = "alice"
    yaml = _b(
        {"litellm": {"model": "gpt-4o"}},
        {},
        agent_id=agent_id,
        profile_name=profile_name,
        browser_sandbox=True,
    )
    # platform_toolsets.api_server 含 browser 工具集
    assert "- hermes-api-server\n    - terminal\n    - browser\n" in yaml
    # browser.cdp_url 指向 per-profile browser Pod 的 CDP 代理端口（命名确定性）
    expected_dns = (
        f"{_browser_name(agent_id, profile_name)}.{settings.k8s_namespace}.svc.cluster.local"
    )
    expected_cdp = f"http://{expected_dns}:{settings.browser_cdp_proxy_port}"
    assert f'cdp_url: "{expected_cdp}"' in yaml


def test_build_profile_config_yaml_browser_sandbox_needs_agent_and_profile():
    """browser_sandbox=True 但缺 agent_id/profile_name → 不渲染（无法算 DNS）"""
    from app.worker._common import build_profile_config_yaml as _b

    yaml = _b({"litellm": {"model": "gpt-4o"}}, {}, browser_sandbox=True)
    assert "cdp_url" not in yaml
    assert "- browser" not in yaml


# ── _regen_homes_config：skill sync 不丢 browser 段（per-profile） ─────────────


@pytest.fixture
def regen_env(monkeypatch):
    """mock _regen_homes_config 外部依赖，记录每个 home 写入的 config.yaml 内容。"""
    from app.worker import config_skills as router_mod

    agent_id = "550e8400-e29b-41d4-a716-446655440000"

    def _inst_cfg(browser_enabled: bool) -> dict:
        return {
            "model_config": {"litellm": {"model": "gpt-4o"}},
            "skill_config": {},
            "definition_id": "def-123",
            "runtime_config": {"browser_sandbox": {"enabled": browser_enabled}},
        }

    written: list[tuple[str, str, str]] = []  # (pod, path, config_yaml)

    async def _write(pod, path, content):
        written.append((pod, path, content))

    monkeypatch.setattr(router_mod.k8s_manager, "exec_write_file_in_pod", _write)
    return {"agent_id": agent_id, "inst_cfg": _inst_cfg, "written": written}


async def test_regen_homes_config_keeps_browser_per_profile(regen_env, monkeypatch):
    """browser_sandbox 开启：regen 各 home 仍按 per-profile 注 browser 段，base 除外。

    回归 bug：旧 _regen_homes_config 只渲染一份无 browser 的 config 写到所有 home，
    skill sync 会覆盖 heal 写入的 browser toolset + cdp_url。
    """
    from app.worker import config_skills as router_mod
    from app.worker.k8s_manager import _browser_name

    from pkg.common.config import settings

    agent_id = regen_env["agent_id"]
    monkeypatch.setattr(
        router_mod,
        "_load_instance_config",
        AsyncMock(return_value=regen_env["inst_cfg"](True)),
    )
    homes = [
        "/opt/data/profiles/550e8400-aaaa-1111",
        "/opt/data/profiles/550e8400-bbbb-2222",
        "/opt/data/profiles/base",
    ]
    await router_mod._regen_homes_config("pod-a", agent_id, homes, db=None)

    assert len(regen_env["written"]) == 3
    by_home = {path: yaml for _pod, path, yaml in regen_env["written"]}
    # 真实 profile：各含 browser toolset + per-profile cdp_url（命名确定性）
    for pn in ["550e8400-aaaa-1111", "550e8400-bbbb-2222"]:
        y = by_home[f"/opt/data/profiles/{pn}/config.yaml"]
        assert "- browser\n" in y, pn
        dns = f"{_browser_name(agent_id, pn)}.{settings.k8s_namespace}.svc.cluster.local"
        assert f'cdp_url: "http://{dns}:{settings.browser_cdp_proxy_port}"' in y, pn
    # base：无 browser Pod，不注 browser 段
    ybase = by_home["/opt/data/profiles/base/config.yaml"]
    assert "cdp_url" not in ybase
    assert "- browser" not in ybase


async def test_regen_homes_config_no_browser_when_sandbox_disabled(regen_env, monkeypatch):
    """browser_sandbox 关闭：regen 各 home 均无 browser 段。"""
    from app.worker import config_skills as router_mod

    monkeypatch.setattr(
        router_mod,
        "_load_instance_config",
        AsyncMock(return_value=regen_env["inst_cfg"](False)),
    )
    homes = ["/opt/data/profiles/p1", "/opt/data/profiles/base"]
    await router_mod._regen_homes_config("pod-a", regen_env["agent_id"], homes, db=None)
    assert len(regen_env["written"]) == 2
    for _pod, _path, yaml in regen_env["written"]:
        assert "cdp_url" not in yaml
        assert "- browser" not in yaml


# ── _fanout_skill_to_pods：per-Pod 共享目录写一次 ────────────────────


@pytest.fixture
def fanout_pods_env(monkeypatch):
    """mock _fanout_skill_to_pods 的外部依赖，记录 k8s exec 调用。"""
    from app.worker import config_skills as router_mod

    monkeypatch.setattr(
        router_mod,
        "_load_agent_configs",
        AsyncMock(return_value=({}, {}, "def-123")),
    )
    monkeypatch.setattr(
        router_mod,
        "_iter_agent_target_pods",
        AsyncMock(
            return_value=[
                {
                    "pod_name": "pod-a",
                    "homes": ["/opt/data/profiles/p1", "/opt/data/profiles/base"],
                },
                {"pod_name": "pod-b", "homes": ["/opt/data/profiles/p2"]},
            ]
        ),
    )
    # _regen_homes_config 走 _load_instance_config（已 mock）+ exec_write_file_in_pod
    monkeypatch.setattr(router_mod, "_regen_homes_config", AsyncMock())
    monkeypatch.setattr(router_mod, "_ensure_shared_skill_dir", AsyncMock())
    # db=None 场景下不触真 DB：definition 层 skill record 读库单独 mock
    monkeypatch.setattr(router_mod, "_load_definition_skill_record", AsyncMock(return_value=None))

    import io as _io
    import zipfile as _zf

    def _mkzip(name):
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr(f"{name}/SKILL.md", f"---\nname: {name}\n---\n# {name}\n")
        return buf.getvalue()

    exec_calls: list[tuple[str, list[str]]] = []
    untar_calls: list[tuple[str, str, bytes]] = []

    async def _exec(pod, cmds):
        exec_calls.append((pod, cmds))
        return ""

    async def _untar(pod, path, data):
        untar_calls.append((pod, path, data))

    monkeypatch.setattr(router_mod.k8s_manager, "exec_command_in_pod", _exec)
    monkeypatch.setattr(router_mod.k8s_manager, "exec_untar_to_in_pod", _untar)
    return {"exec": exec_calls, "untar": untar_calls}


async def test_fanout_skill_to_pods_writes_shared_dir_once_per_pod(fanout_pods_env):
    """external_dirs 模型：每 Pod 写一次共享目录，不再 per-home 复制。"""
    import io
    import zipfile

    from app.worker.config_skills import _fanout_skill_to_pods

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plan/SKILL.md", "---\nname: plan\n---\n# plan\n")

    written = await _fanout_skill_to_pods("agent-1", "plan", buf.getvalue(), db=None)
    assert written == 2  # 2 个 Pod 各写一次
    # 原子换入：rm -rf {dest} && mv {dest_new} {dest}（替代旧 rm -rf; mkdir -p {dest}）
    all_cmds = [c for pod, cmds in fanout_pods_env["exec"] for c in cmds]
    assert any(
        c.startswith("rm -rf /opt/data/skills/def-123/plan && mv ")
        and "/opt/data/skills/def-123/plan.new." in c
        for c in all_cmds
    ), all_cmds
    # 不应出现 per-home skills 目录的 mkdir
    assert all(
        "/opt/data/profiles/" not in c or "skills" not in c
        for c in all_cmds
        if c.startswith("mkdir")
    )
    # untar 每 Pod 一次（共 2 次，不是按 home 数）
    assert len(fanout_pods_env["untar"]) == 2


async def test_fanout_skill_to_pods_no_definition_id_returns_zero(fanout_pods_env, monkeypatch):
    from app.worker import config_skills as router_mod

    monkeypatch.setattr(router_mod, "_load_agent_configs", AsyncMock(return_value=({}, {}, None)))
    written = await router_mod._fanout_skill_to_pods("agent-1", "plan", b"zip", db=None)
    assert written == 0
    assert fanout_pods_env["untar"] == []


def test_skill_group_name_matches_profile_isolation_algorithm():
    """manager 与 profile_isolation 的 _skill_group_name 必须一致（建组 + 加组两边对齐）。"""
    from app.worker.config_skills import _skill_group_name

    # UUID 形式 definition_id（alnum 保留、连字符转 -，截前 24 字符）
    assert (
        _skill_group_name("abc12345-6789-4def-abcd-ef0123456789")
        == "skills-abc12345-6789-4def-abcd-"
    )
    # 非 UUID / 数字开头 → 加 d 前缀（与 profile_isolation 一致）
    assert _skill_group_name("12345678") == "skills-d12345678"


def test_ensure_shared_skill_dir_shell_contains_groupadd_and_chmod():
    """生成的 shell 命令含 groupadd + chown root:{gid} + chmod 2750。"""
    from app.worker.config_skills import _ensure_shared_skill_dir_shell

    sh = _ensure_shared_skill_dir_shell("def-abc-123")
    assert "groupadd" in sh
    assert "chmod 2750" in sh
    assert "/opt/data/skills/def-abc-123" in sh
    assert "skills-def-abc-123" in sh  # group name
