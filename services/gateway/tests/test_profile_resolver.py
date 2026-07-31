"""ProfileResolver 单元测试

UserGroup 隔离改造后验证:
  1. _check_access — 用户必须是 agent 所属组的成员
  2. _derive_scope — 恒为 ("USER", user_id, "INDEPENDENT")（SHARED 已下线）
  3. _get_agent — SQL 命中 agent_instances（含 group_id，无 access_scope）
  4. INDEPENDENT profile 命名
  5. AccessDenied / ProfileNotFound 场景
"""

import hashlib
import pytest
from typing import Optional, Dict, List, Set
from unittest.mock import AsyncMock, patch, MagicMock


# ── Helper: scope_hash ────────────────────────────────

def _scope_hash(scope_type: str, scope_target_id: Optional[str]) -> str:
    raw = f"{scope_type}:{scope_target_id or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:6]


def _build_profile_name(agent_id: str, scope_type: str,
                        scope_target_id: Optional[str], user_id: str) -> str:
    short_agent = agent_id.replace("-", "")[:8]
    shash = _scope_hash(scope_type, scope_target_id)
    short_user = user_id.replace("-", "")[:8]
    return f"{short_agent}-{shash}-{short_user}"


# ── Profile Name Tests ────────────────────────────────

class TestProfileNaming:
    """§6.2 Profile 命名规则"""

    def test_independent_profile_naming(self):
        name = _build_profile_name(
            "a1b2c3d4-e5f6-4789-abcd-ef1234567890",
            "USER_GROUP", "gg-001", "emp001"
        )
        parts = name.split("-")
        assert len(parts) == 3
        assert parts[0] == "a1b2c3d4"  # agent 前8位
        assert len(parts[1]) == 6       # scope hash 6位
        assert parts[2] == "emp001"     # user 前8位

    def test_scope_hash_deterministic(self):
        h1 = _scope_hash("USER_GROUP", "gg-001")
        h2 = _scope_hash("USER_GROUP", "gg-001")
        assert h1 == h2

    def test_scope_hash_different_for_different_scopes(self):
        h1 = _scope_hash("USER_GROUP", "gg-001")
        h2 = _scope_hash("USER_GROUP", "gg-002")
        assert h1 != h2

    def test_scope_hash_different_for_different_types(self):
        h1 = _scope_hash("USER_GROUP", "gg-001")
        h2 = _scope_hash("USER", "gg-001")
        assert h1 != h2


# ── Scope Matching Tests ───────────────────────────────

class TestScopeMatching:
    """Channel scope 匹配逻辑"""

    def test_all_scope_matches_any_user(self):
        """scope_type=ALL: 任意用户都匹配"""
        channels = [
            {"scope_type": "ALL", "scope_target_id": None, "profile_type": "INDEPENDENT"}
        ]
        # 任何 user 都应该匹配 ALL channel
        matched = _match_channel(channels, "user-001", set())
        assert matched is not None
        assert matched["scope_type"] == "ALL"

    def test_user_scope_matches_exact_user(self):
        """scope_type=USER: 只有指定用户匹配"""
        channels = [
            {"scope_type": "USER", "scope_target_id": "user-001", "profile_type": "INDEPENDENT"},
            {"scope_type": "USER", "scope_target_id": "user-002", "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, "user-001", set())
        assert matched["scope_target_id"] == "user-001"

    def test_user_scope_no_match_for_other(self):
        """scope_type=USER: 非指定用户不匹配"""
        channels = [
            {"scope_type": "USER", "scope_target_id": "user-001", "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, "user-999", set())
        assert matched is None

    def test_user_group_scope_matches_group_member(self):
        """scope_type=USER_GROUP: 用户属于目标组才匹配"""
        channels = [
            {"scope_type": "USER_GROUP", "scope_target_id": "gg-001", "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, "any-user", {"gg-001", "gg-002"})
        assert matched is not None
        assert matched["scope_target_id"] == "gg-001"

    def test_user_group_scope_no_match_for_non_member(self):
        """scope_type=USER_GROUP: 非组成员不匹配"""
        channels = [
            {"scope_type": "USER_GROUP", "scope_target_id": "gg-001", "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, "any-user", {"gg-999"})
        assert matched is None

    def test_all_scope_priority(self):
        """ALL scope 优先于 USER_GROUP"""
        channels = [
            {"scope_type": "ALL", "scope_target_id": None, "profile_type": "INDEPENDENT"},
            {"scope_type": "USER_GROUP", "scope_target_id": "gg-001", "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, "any-user", {"gg-001"})
        # ALL 先匹配
        assert matched["scope_type"] == "ALL"


# ── Access (group membership) Tests ───────────────────

class TestAccessScope:
    """组隔离权限验证：用户可访问 ⟺ agent 所属组 ∈ 用户所属组"""

    def test_member_can_access(self):
        """用户在 agent 所属组内 → 可访问"""
        assert _check_access("user-001", "gg-001", {"gg-001", "gg-002"})

    def test_non_member_cannot_access(self):
        """用户不在 agent 所属组内 → 拒绝"""
        assert not _check_access("user-001", "gg-001", {"gg-999"})

    def test_user_with_no_groups_cannot_access(self):
        """用户无任何组 → 拒绝"""
        assert not _check_access("user-001", "gg-001", set())

    def test_agent_with_empty_group_cannot_access(self):
        """agent 无 group_id → 拒绝（group_id 为空）"""
        assert not _check_access("user-001", "", {"gg-001"})
        assert not _check_access("user-001", None, {"gg-001"})


# ── ProfileNotFound Tests ──────────────────────────────

class TestProfileNotFound:
    """异常场景"""

    def test_no_channels(self):
        """无 Channel 配置时返回 None"""
        matched = _match_channel([], "user-001", set())
        assert matched is None

    def test_no_matching_scope(self):
        """所有 Channel 都不匹配用户"""
        channels = [
            {"scope_type": "USER", "scope_target_id": "user-002", "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, "user-001", set())
        assert matched is None


# ── Helpers (same logic as ProfileResolver) ────────────

def _match_channel(channels: List[Dict], user_id: str,
                   user_groups: Set[str]) -> Optional[Dict]:
    """Simulate ProfileResolver._match_channel"""
    for ch in channels:
        st = ch["scope_type"]
        stid = ch.get("scope_target_id")
        if st == "ALL":
            return ch
        elif st == "USER" and stid == user_id:
            return ch
        elif st == "USER_GROUP" and stid and stid in user_groups:
            return ch
    return None


def _check_access(user_id: str, agent_group_id: Optional[str],
                  user_groups: Set[str]) -> bool:
    """Simulate ProfileResolver._check_access（组隔离）

    用户可访问 ⟺ agent 所属组 ∈ 用户所属组。
    agent 无 group_id 时一律拒绝。
    """
    if not agent_group_id:
        return False
    return agent_group_id in user_groups


class TestCheckAccessGate:
    """ProfileResolver.check_access 轻量权限闸门测试"""

    def _resolver(self):
        from app.profile_resolver import ProfileResolver
        return ProfileResolver()

    @pytest.mark.asyncio
    async def test_not_bound_raises_and_neg_cached(self):
        """未绑定 → 抛 NotBound 并写负缓存，二次调用不再查 DB"""
        from app.profile_resolver import NotBound
        r = self._resolver()
        with patch.object(r, "_resolve_im_user", AsyncMock(return_value=None)) as mock_map:
            with pytest.raises(NotBound):
                await r.check_access("im_user_001", "agent-001", "wecom")
            with pytest.raises(NotBound):
                await r.check_access("im_user_001", "agent-001", "wecom")
        assert mock_map.await_count == 1  # 负缓存命中，仅首次查映射

    @pytest.mark.asyncio
    async def test_http_channel_skips_mapping(self):
        """http 渠道跳过 IM 映射"""
        r = self._resolver()
        agent = {"id": "agent-001", "group_id": "gg-001", "resource_pool_id": "pool-1"}
        channel = {"scope_type": "ALL", "scope_target_id": None}
        with patch.object(r, "_resolve_im_user", AsyncMock(return_value="x")) as mock_map, \
             patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=True)), \
             patch.object(r, "_match_channel", AsyncMock(return_value=channel)):
            await r.check_access("u-001", "agent-001", "http")
        mock_map.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_not_found(self):
        """agent 不存在 → ProfileNotFound"""
        from app.profile_resolver import ProfileNotFound
        r = self._resolver()
        with patch.object(r, "_resolve_im_user", AsyncMock(return_value="u-uuid")), \
             patch.object(r, "_get_agent", AsyncMock(return_value=None)):
            with pytest.raises(ProfileNotFound):
                await r.check_access("im_user_001", "agent-001", "wecom")

    @pytest.mark.asyncio
    async def test_access_denied(self):
        """已映射但无权限 → AccessDenied"""
        from app.profile_resolver import AccessDenied
        r = self._resolver()
        agent = {"id": "agent-001", "group_id": "gg-001", "resource_pool_id": "pool-1"}
        with patch.object(r, "_resolve_im_user", AsyncMock(return_value="u-uuid")), \
             patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=False)):
            with pytest.raises(AccessDenied):
                await r.check_access("im_user_001", "agent-001", "wecom")

    @pytest.mark.asyncio
    async def test_no_channel(self):
        """无匹配 channel → ProfileNotFound"""
        from app.profile_resolver import ProfileNotFound
        r = self._resolver()
        agent = {"id": "agent-001", "group_id": "gg-001", "resource_pool_id": "pool-1"}
        with patch.object(r, "_resolve_im_user", AsyncMock(return_value="u-uuid")), \
             patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=True)), \
             patch.object(r, "_match_channel", AsyncMock(return_value=None)):
            with pytest.raises(ProfileNotFound):
                await r.check_access("im_user_001", "agent-001", "wecom")

    @pytest.mark.asyncio
    async def test_pass_no_exception(self):
        """全部通过 → 不抛异常"""
        r = self._resolver()
        agent = {"id": "agent-001", "group_id": "gg-001", "resource_pool_id": "pool-1"}
        channel = {"scope_type": "ALL", "scope_target_id": None}
        with patch.object(r, "_resolve_im_user", AsyncMock(return_value="u-uuid")), \
             patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=True)), \
             patch.object(r, "_match_channel", AsyncMock(return_value=channel)):
            await r.check_access("im_user_001", "agent-001", "wecom")

    @pytest.mark.asyncio
    async def test_resolve_raises_not_bound_when_unmapped(self):
        """resolve() 路径一致性：未映射同样抛 NotBound"""
        from app.profile_resolver import NotBound
        r = self._resolver()
        with patch.object(r, "_resolve_im_user", AsyncMock(return_value=None)):
            with pytest.raises(NotBound):
                await r.resolve("im_user_001", "agent-001", "wecom")


class TestResolvedTarget:
    """ResolvedTarget 数据结构"""

    def test_engine_instance_id_field(self):
        """ResolvedTarget 应包含 engine_instance_id 字段"""
        from app.profile_resolver import ResolvedTarget

        t = ResolvedTarget(
            profile_name="test-profile",
            profile_type="INDEPENDENT",
            deployment_id="dep-001",
            engine_instance_id="ei-001",
            resolved_user_id="user-001",
            pod_name="pod-001",
            engine_url="http://test:8642",
            internal_port=None,
            scope_type="ALL",
            scope_target_id=None,
        )
        assert t.engine_instance_id == "ei-001"
        assert t.profile_name == "test-profile"
        assert t.internal_port is None

    def test_engine_instance_id_in_resolve_result(self):
        """_get_deployment 应返回 engine_instance_id"""
        from app.profile_resolver import ProfileResolver

        # 验证 profile_resolver 的 resolve 路径包含此字段
        # 因为 resolve 依赖 DB，此处只检查 ResolvedTarget 构造
        from app.profile_resolver import ResolvedTarget
        t = ResolvedTarget(
            profile_name="p",
            profile_type="INDEPENDENT",
            deployment_id="d",
            engine_instance_id="ei-999",
            resolved_user_id="u-999",
            pod_name="pod",
            engine_url="http://e:8642",
            internal_port=8644,
            scope_type="ALL",
            scope_target_id=None,
        )
        assert t.engine_instance_id == "ei-999"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── V3 数据源切换测试 ─────────────────────────────────

class _FakeSession:
    """模拟 async_session() 上下文管理器，yield 一个捕获 SQL 的 mock db。"""

    def __init__(self, execute_side_effect):
        self.db = MagicMock()
        self.db.execute = AsyncMock(side_effect=execute_side_effect)
        self.db.commit = AsyncMock()

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class TestV3DataSourceQueries:
    """UserGroup 隔离改造后数据源验证：profile_resolver / models 的 SQL 命中
    agent_instances（含 group_id，无 access_scope）、user_group_members、
    agent_instance_channels，而非已删除的 agent_instance_user_access /
    agent_instance_group_access。"""

    def _resolver(self):
        from app.profile_resolver import ProfileResolver
        return ProfileResolver()

    @staticmethod
    def _mapping_row(mapping):
        m = MagicMock()
        m.mappings = MagicMock(return_value=MagicMock(first=MagicMock(return_value=mapping)))
        return m

    @staticmethod
    def _scalar_row(value):
        m = MagicMock()
        m.scalar = MagicMock(return_value=value)
        return m

    @pytest.mark.asyncio
    async def test_get_agent_queries_agent_instances(self):
        r = self._resolver()
        row = self._mapping_row(
            {"id": "inst-1", "group_id": "gg-1", "resource_pool_id": "pool-1"}
        )
        fake = _FakeSession([row])
        with patch("app.profile_resolver.async_session", return_value=fake):
            agent = await r._get_agent("inst-1")
        assert agent["resource_pool_id"] == "pool-1"
        assert agent["group_id"] == "gg-1"
        sql = str(fake.db.execute.call_args[0][0])
        assert "FROM agent_instances" in sql
        assert "group_id" in sql
        assert "resource_pool_id" in sql
        # access_scope 列已删除
        assert "access_scope" not in sql

    @pytest.mark.asyncio
    async def test_check_access_queries_user_group_members(self):
        """_check_access 命中 user_group_members，命中→True"""
        r = self._resolver()
        agent = {"id": "inst-1", "group_id": "gg-1"}
        fake = _FakeSession([self._scalar_row(1)])  # user_group_members 命中
        with patch("app.profile_resolver.async_session", return_value=fake):
            ok = await r._check_access("u-1", agent, is_admin=False)
        assert ok is True
        sql = str(fake.db.execute.call_args[0][0])
        assert "user_group_members" in sql
        assert "user_id" in sql
        assert "group_id" in sql
        # 已删除的表不应出现在 SQL
        assert "agent_instance_user_access" not in sql
        assert "agent_instance_group_access" not in sql

    @pytest.mark.asyncio
    async def test_check_access_no_membership_returns_false(self):
        """_check_access 未命中 user_group_members → False"""
        r = self._resolver()
        agent = {"id": "inst-1", "group_id": "gg-1"}
        fake = _FakeSession([self._scalar_row(None)])  # 未命中
        with patch("app.profile_resolver.async_session", return_value=fake):
            ok = await r._check_access("u-1", agent, is_admin=False)
        assert ok is False

    @pytest.mark.asyncio
    async def test_check_access_empty_group_returns_false(self):
        """agent 无 group_id → 直接 False，不查 DB"""
        r = self._resolver()
        agent = {"id": "inst-1", "group_id": None}
        fake = _FakeSession([])
        with patch("app.profile_resolver.async_session", return_value=fake):
            ok = await r._check_access("u-1", agent, is_admin=False)
        assert ok is False
        # group_id 为空时不应执行 SQL
        fake.db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_derive_scope_ignores_shared(self):
        """SHARED 已下线：channel 显式 SHARED 也恒返回 USER/INDEPENDENT。"""
        r = self._resolver()
        agent = {"id": "inst-1", "group_id": "gg-1"}
        scope_type, scope_target_id, profile_type = await r._derive_scope(
            agent, "u-1", {"profile_type": "SHARED"}
        )
        assert (scope_type, profile_type) == ("USER", "INDEPENDENT")
        assert scope_target_id == "u-1"

    @pytest.mark.asyncio
    async def test_derive_scope_missing_defaults_independent(self):
        """profile_type 缺失（channel=None 或未存）→ 恒 USER/INDEPENDENT。"""
        r = self._resolver()
        agent = {"id": "inst-1", "group_id": "gg-1"}
        # channel=None
        scope_type, scope_target_id, profile_type = await r._derive_scope(agent, "u-1")
        assert (scope_type, profile_type) == ("USER", "INDEPENDENT")
        assert scope_target_id == "u-1"
        # channel 无 profile_type 字段
        scope_type, _, profile_type = await r._derive_scope(
            agent, "u-1", {"scope_type": "ALL"}
        )
        assert (scope_type, profile_type) == ("USER", "INDEPENDENT")

    @pytest.mark.asyncio
    async def test_match_channel_queries_instance_channels(self):
        r = self._resolver()
        ch = {"id": "ch-1", "scope_type": "ALL", "scope_target_id": None, "profile_type": "INDEPENDENT"}
        fake = _FakeSession([self._mapping_row(ch)])
        with patch("app.profile_resolver.async_session", return_value=fake):
            got = await r._match_channel("inst-1", "wecom")
        assert got["id"] == "ch-1"
        sql = str(fake.db.execute.call_args[0][0])
        assert "agent_instance_channels" in sql
        assert "instance_id" in sql
        assert "agent_channels" not in sql.replace("agent_instance_channels", "")

    @pytest.mark.asyncio
    async def test_get_agent_model_config_reads_instance_litellm(self):
        from app.models import get_agent_model_config
        lc = {"key": "sk-inst-key", "model": "gpt-4o"}
        fake = _FakeSession([self._mapping_row({"litellm_config": lc})])
        with patch("app.models.async_session", return_value=fake):
            mc = await get_agent_model_config("inst-1")
        assert mc == {"litellm": lc}
        sql = str(fake.db.execute.call_args[0][0])
        assert "agent_instances" in sql
        assert "litellm_config" in sql

    @pytest.mark.asyncio
    async def test_get_channel_config_queries_instance_channels(self):
        from app.models import get_channel_config
        cfg = {"webhook_url": "http://x"}
        fake = _FakeSession([self._mapping_row({"config": cfg})])
        with patch("app.models.async_session", return_value=fake):
            got = await get_channel_config("inst-1", "wecom")
        assert got == cfg
        sql = str(fake.db.execute.call_args[0][0])
        assert "agent_instance_channels" in sql
        assert "instance_id" in sql


# ── ensure 失败：已删用户不回退 base profile ──────────────

class _FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """模拟 httpx.AsyncClient 上下文管理器。"""

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        return self._resp


@pytest.mark.asyncio
async def test_call_controller_ensure_404_raises_access_denied():
    """manager 返回 404（用户已删）→ AccessDenied，不回退 None。"""
    from app.profile_resolver import AccessDenied, ProfileResolver

    r = ProfileResolver()
    fake_client = _FakeClient(_FakeResp(404, "user not found"))
    with patch("app.profile_resolver.httpx.AsyncClient", return_value=fake_client):
        with pytest.raises(AccessDenied):
            await r._call_controller_ensure_profile(
                agent_id="a" * 32,
                engine_instance_id="p" * 32,
                user_id="u" * 32,
                profile_type="INDEPENDENT",
                profile_name="pn-x",
            )


@pytest.mark.asyncio
async def test_call_controller_ensure_500_returns_none_fallback():
    """manager 500（基础设施瞬时故障）→ 返回 None 回退 base，不误伤非删用户场景。"""
    from app.profile_resolver import ProfileResolver

    r = ProfileResolver()
    fake_client = _FakeClient(_FakeResp(500, "boom"))
    with patch("app.profile_resolver.httpx.AsyncClient", return_value=fake_client):
        result = await r._call_controller_ensure_profile(
            agent_id="a" * 32,
            engine_instance_id="p" * 32,
            user_id="u" * 32,
            profile_type="INDEPENDENT",
            profile_name="pn-x",
        )
    assert result is None


class TestResolveBrowserTarget:
    """profile_resolver.resolve_browser_target：VNC 接管目标解析（鉴权 + profile_name + browser Pod）。"""

    def _resolver(self):
        from app.profile_resolver import ProfileResolver
        return ProfileResolver()

    @pytest.mark.asyncio
    async def test_happy_path_returns_pod_and_vnc_pw(self):
        from unittest.mock import AsyncMock, patch
        r = self._resolver()
        agent = {"id": "a", "group_id": "gg", "resource_pool_id": "p"}
        with patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=True)), \
             patch.object(r, "_build_profile_name", return_value="pn-test"), \
             patch.object(r, "_get_deployment", AsyncMock(return_value={
                 "internal_port_map": {"browsers": {"pn-test": {"pod": "browser-x", "vnc_pw": "secret"}},
                                       "profiles": {"pn-test": 8644}}
             })):
            pn, pod, pw = await r.resolve_browser_target("u-1", "a-1", is_admin=False)
            assert pn == "pn-test"
            assert pod == "browser-x"
            assert pw == "secret"

    @pytest.mark.asyncio
    async def test_agent_not_found(self):
        from unittest.mock import AsyncMock, patch
        from app.profile_resolver import ProfileNotFound
        r = self._resolver()
        with patch.object(r, "_get_agent", AsyncMock(return_value=None)):
            with pytest.raises(ProfileNotFound):
                await r.resolve_browser_target("u-1", "a-1")

    @pytest.mark.asyncio
    async def test_access_denied(self):
        from unittest.mock import AsyncMock, patch
        from app.profile_resolver import AccessDenied
        r = self._resolver()
        agent = {"id": "a", "group_id": "gg", "resource_pool_id": "p"}
        with patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=False)):
            with pytest.raises(AccessDenied):
                await r.resolve_browser_target("u-1", "a-1")

    @pytest.mark.asyncio
    async def test_no_deployment(self):
        from unittest.mock import AsyncMock, patch
        from app.profile_resolver import ProfileNotFound
        r = self._resolver()
        agent = {"id": "a", "group_id": "gg", "resource_pool_id": "p"}
        with patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=True)), \
             patch.object(r, "_build_profile_name", return_value="pn-test"), \
             patch.object(r, "_get_deployment", AsyncMock(return_value=None)):
            with pytest.raises(ProfileNotFound):
                await r.resolve_browser_target("u-1", "a-1")

    @pytest.mark.asyncio
    async def test_no_browser_pod_sandbox_not_enabled(self):
        """internal_port_map 无 browsers 键（沙箱未启用）→ ProfileNotFound"""
        from unittest.mock import AsyncMock, patch
        from app.profile_resolver import ProfileNotFound
        r = self._resolver()
        agent = {"id": "a", "group_id": "gg", "resource_pool_id": "p"}
        with patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=True)), \
             patch.object(r, "_build_profile_name", return_value="pn-test"), \
             patch.object(r, "_get_deployment", AsyncMock(return_value={
                 "internal_port_map": {"profiles": {"pn-test": 8644}}  # 无 browsers
             })):
            with pytest.raises(ProfileNotFound):
                await r.resolve_browser_target("u-1", "a-1")

    @pytest.mark.asyncio
    async def test_browser_pod_missing_for_this_profile(self):
        """browsers 有别的 profile 但无本 profile → ProfileNotFound"""
        from unittest.mock import AsyncMock, patch
        from app.profile_resolver import ProfileNotFound
        r = self._resolver()
        agent = {"id": "a", "group_id": "gg", "resource_pool_id": "p"}
        with patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=True)), \
             patch.object(r, "_build_profile_name", return_value="pn-mine"), \
             patch.object(r, "_get_deployment", AsyncMock(return_value={
                 "internal_port_map": {"browsers": {"pn-other": {"pod": "b-o", "vnc_pw": "x"}}}
             })):
            with pytest.raises(ProfileNotFound):
                await r.resolve_browser_target("u-1", "a-1")

    @pytest.mark.asyncio
    async def test_profile_name_uses_user_scope(self):
        """profile_name 构造用 USER scope（与 chat 路由一致，INDEPENDENT）"""
        from unittest.mock import AsyncMock, patch
        r = self._resolver()
        agent = {"id": "a", "group_id": "gg", "resource_pool_id": "p"}
        uid = "00000000-1111-2222-3333-444444444444"
        expected_pn = r._build_profile_name("a-1", "USER", uid, uid)
        with patch.object(r, "_get_agent", AsyncMock(return_value=agent)), \
             patch.object(r, "_check_access", AsyncMock(return_value=True)), \
             patch.object(r, "_get_deployment", AsyncMock(return_value={
                 "internal_port_map": {"browsers": {expected_pn: {"pod": "b", "vnc_pw": "p"}}}
             })) as mk_dep:
            pn, _, _ = await r.resolve_browser_target(uid, "a-1")
            assert pn == expected_pn
            # _get_deployment 被调一次（取 internal_port_map）
            mk_dep.assert_awaited_once_with("a-1")


class TestEnsureProfileFallbackSQL:
    """_ensure_profile fallback SQL 必须匹配 uq_user_profile_per_instance 约束。

    历史 bug：SQL 用 `ON CONFLICT (deployment_id, profile_name) DO NOTHING`，
    但 agent_profiles 实际唯一约束是 (instance_id, resource_pool_id, user_id)，
    撞约束时 ON CONFLICT 匹配不上 → PostgreSQL 抛 UniqueViolationError →
    fallback 失效（log warning 刷错）。修复后 fallback SQL 必须用新约束的列。
    """

    def _resolver(self):
        from app.profile_resolver import ProfileResolver
        return ProfileResolver()

    @pytest.mark.asyncio
    async def test_ensure_profile_uses_uq_user_profile_per_instance(self):
        """fallback SQL 的 ON CONFLICT 子句必须用 (instance_id, resource_pool_id, user_id)。"""
        r = self._resolver()
        fake = _FakeSession([MagicMock()])
        with patch("app.profile_resolver.async_session", return_value=fake):
            await r._ensure_profile(
                agent_id="a-1",
                deployment_id="d-1",
                profile_name="pn",
                profile_type="INDEPENDENT",
                user_id="00000000-1111-2222-3333-444444444444",
                scope_target_id=None,
                group_id="gg-1",
            )
        # 拿到 _ensure_profile 实际执行的 SQL 文本
        assert fake.db.execute.await_count == 1
        sql_clause = fake.db.execute.await_args.args[0]
        sql_text = str(sql_clause)
        assert "ON CONFLICT (instance_id, resource_pool_id, user_id) DO NOTHING" in sql_text
        # 不能回退到旧约束（旧约束与 uq_user_profile_per_instance 不匹配会报错）
        assert "ON CONFLICT (deployment_id, profile_name)" not in sql_text

    @pytest.mark.asyncio
    async def test_ensure_profile_failure_is_non_fatal(self):
        """fallback SQL 失败也不抛（non-fatal），只 log warning。"""
        r = self._resolver()
        fake = _FakeSession([RuntimeError("db down")])
        with patch("app.profile_resolver.async_session", return_value=fake):
            # 不抛任何异常
            await r._ensure_profile(
                agent_id="a-1",
                deployment_id="d-1",
                profile_name="pn",
                profile_type="INDEPENDENT",
                user_id="u-1",
                scope_target_id=None,
                group_id=None,
            )
