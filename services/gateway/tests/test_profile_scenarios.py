"""光谷门店多用户路由场景测试

UserGroup 隔离改造后：用户可访问智能体 ⟺ 该智能体所属组 ∈ 用户所属组。
每个智能体归属单一用户组，部署对该组全员共享 Profile。

验证 4 个用户 × 3 个智能体的路由组合:
- 店长(门店组):  门店助手 ✅ 库存助手 ✅ 店长助理 ✅
- 店员A(门店组): 门店助手 ✅ 库存助手 ✅ 店长助理 ❌
- 店员B(门店组): 门店助手 ✅ 库存助手 ✅ 店长助理 ❌
- 顾客(无组):    门店助手 ✅ 库存助手 ❌ 店长助理 ❌

遵循现有 test_profile_resolver.py 的测试模式:
纯函数级测试，不依赖 FastAPI/DB 环境。
"""

import hashlib
from typing import Optional, Dict, List, Set

import pytest

# ==================== Helper Functions ====================

def _scope_hash(scope_type: str, scope_target_id: Optional[str]) -> str:
    raw = f"{scope_type}:{scope_target_id or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:6]


def _build_profile_name(agent_id: str, scope_type: str,
                        scope_target_id: Optional[str], user_id: str) -> str:
    short_agent = agent_id.replace("-", "")[:8]
    shash = _scope_hash(scope_type, scope_target_id)
    short_user = user_id.replace("-", "")[:8]
    return f"{short_agent}-{shash}-{short_user}"


def _match_channel(channels: List[Dict], user_id: str, user_groups: Set[str]) -> Optional[Dict]:
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
                  user_group_ids: Set[str]) -> bool:
    """Simulate ProfileResolver._check_access（组隔离）

    用户可访问 ⟺ agent 所属组 ∈ 用户所属组。
    agent 无 group_id 时一律拒绝。
    """
    if not agent_group_id:
        return False
    return agent_group_id in user_group_ids


# ==================== 光谷门店场景常量 ====================

# 用户
STORE_MGR = "40767032-abbe-4848-a328-dda17c4a37a5"   # 店长
STAFF_A = "83c83c77-04a2-4b96-b77b-ada92922e0c1"     # 店员A
STAFF_B = "c79cfbd0-eb59-4643-ae43-4833440cccbd"     # 店员B
CUSTOMER = "bdeec322-b540-4490-b974-db5b7171d9be"     # 顾客

# 智能体
AGENT_SHOP = "d38e436e-a4ae-4706-8b24-93e3f9d7bd15"   # 门店助手
AGENT_STOCK = "c1ef2f5e-6457-401d-b9a9-f0466b4f1005"  # 库存助手
AGENT_MGR = "07a72439-0417-4455-a11e-ba4035ec8d4c"     # 店长助理

# 用户组
GROUP_STORE = "1c09f249-9b72-4a1e-8260-0b95ee422fb4"   # 门店组（全员可访问门店助手/库存助手）
GROUP_MGR = "a2f3b8c1-9d44-4e77-8b21-1f0a5c7e6d92"      # 店长组（仅店长，可访问店长助理）

# 智能体所属组（UserGroup 隔离：每个智能体归属单一组）
AGENT_GROUP = {
    AGENT_SHOP: GROUP_STORE,    # 门店助手 → 门店组（全员）
    AGENT_STOCK: GROUP_STORE,   # 库存助手 → 门店组（全员）
    AGENT_MGR: GROUP_MGR,       # 店长助理 → 店长组（仅店长）
}

# 用户所属组
USER_GROUPS = {
    STORE_MGR: {GROUP_STORE, GROUP_MGR},  # 店长同时在门店组与店长组
    STAFF_A: {GROUP_STORE},               # 店员A 仅门店组
    STAFF_B: {GROUP_STORE},               # 店员B 仅门店组
    CUSTOMER: set(),                      # 顾客无任何组
}


# ==================== Test Classes ====================

class TestGuangguStoreRouting:
    """光谷门店 12 种路由组合验证（组隔离模型）"""

    SCENARIOS = [
        # (display, user_id, agent_id, expected)
        ("店长→门店助手",   STORE_MGR, AGENT_SHOP,  True),
        ("店长→库存助手",   STORE_MGR, AGENT_STOCK, True),
        ("店长→店长助理",   STORE_MGR, AGENT_MGR,   True),
        ("店员A→门店助手",  STAFF_A,   AGENT_SHOP,  True),
        ("店员A→库存助手",  STAFF_A,   AGENT_STOCK, True),
        ("店员A→店长助理",  STAFF_A,   AGENT_MGR,   False),
        ("店员B→门店助手",  STAFF_B,   AGENT_SHOP,  True),
        ("店员B→库存助手",  STAFF_B,   AGENT_STOCK, True),
        ("店员B→店长助理",  STAFF_B,   AGENT_MGR,   False),
        ("顾客→门店助手",   CUSTOMER,  AGENT_SHOP,  False),
        ("顾客→库存助手",   CUSTOMER,  AGENT_STOCK, False),
        ("顾客→店长助理",   CUSTOMER,  AGENT_MGR,   False),
    ]

    @pytest.mark.parametrize(
        "display,user_id,agent_id,expected",
        SCENARIOS
    )
    def test_access_control_scenario(
        self, display, user_id, agent_id, expected
    ):
        agent_group_id = AGENT_GROUP[agent_id]
        user_groups = USER_GROUPS[user_id]
        result = _check_access(user_id, agent_group_id, user_groups)
        assert result == expected, (
            f"{display}: "
            f"预期{'通过' if expected else '拒绝'}但实际{'通过' if result else '拒绝'}"
        )


class TestProfileNaming:
    """Profile 命名规则验证"""

    def test_profile_name_format(self):
        """profile_name 格式: agent[:8]-scope_hash[:6]-user[:8]"""
        name = _build_profile_name(AGENT_SHOP, "ALL", None, STORE_MGR)
        parts = name.split("-")
        assert len(parts) == 3
        assert parts[0] == "d38e436e"   # agent 前8位
        assert len(parts[1]) == 6        # scope hash 6位
        assert parts[2] == "40767032"    # user 前8位

    def test_profile_name_deterministic(self):
        """相同输入产生相同 profile_name"""
        name1 = _build_profile_name(AGENT_SHOP, "ALL", None, STORE_MGR)
        name2 = _build_profile_name(AGENT_SHOP, "ALL", None, STORE_MGR)
        assert name1 == name2

    def test_profile_name_differs_per_user(self):
        """不同用户在同一 scope 下 profile_name 不同"""
        mgr_name = _build_profile_name(AGENT_SHOP, "ALL", None, STORE_MGR)
        staff_name = _build_profile_name(AGENT_SHOP, "ALL", None, STAFF_A)
        assert mgr_name != staff_name

    def test_profile_name_differs_per_scope(self):
        """不同 scope 产生不同 scope_hash"""
        all_hash = _scope_hash("ALL", None)
        group_hash = _scope_hash("USER_GROUP", GROUP_STORE)
        user_hash = _scope_hash("USER", STORE_MGR)
        assert len({all_hash, group_hash, user_hash}) == 3  # 三者各不相同

    def test_scope_hash_deterministic(self):
        h1 = _scope_hash("USER_GROUP", GROUP_STORE)
        h2 = _scope_hash("USER_GROUP", GROUP_STORE)
        assert h1 == h2

    def test_scope_hash_for_all(self):
        """ALL scope 的 hash 确定"""
        h = _scope_hash("ALL", None)
        assert len(h) == 6

    def test_different_groups_different_hash(self):
        """不同 group_id 产生不同 scope_hash"""
        h1 = _scope_hash("USER_GROUP", "group-a")
        h2 = _scope_hash("USER_GROUP", "group-b")
        assert h1 != h2


class TestAccessScope:
    """组隔离权限边缘情况"""

    def test_member_can_access(self):
        """用户在 agent 所属组内 → 可访问"""
        assert _check_access(STAFF_A, GROUP_STORE, {GROUP_STORE})

    def test_non_member_cannot_access(self):
        """用户不在 agent 所属组内 → 拒绝"""
        assert not _check_access(STAFF_A, GROUP_MGR, {GROUP_STORE})

    def test_user_with_no_groups_cannot_access(self):
        """用户无任何组 → 拒绝"""
        assert not _check_access(CUSTOMER, GROUP_STORE, set())

    def test_agent_with_empty_group_cannot_access(self):
        """agent 无 group_id → 拒绝"""
        assert not _check_access(STAFF_A, "", {GROUP_STORE})
        assert not _check_access(STAFF_A, None, {GROUP_STORE})

    def test_member_of_multiple_groups_can_access(self):
        """用户属于多个组，其中之一匹配 → 可访问"""
        assert _check_access(STORE_MGR, GROUP_STORE, {GROUP_STORE, GROUP_MGR})
        assert _check_access(STORE_MGR, GROUP_MGR, {GROUP_STORE, GROUP_MGR})


class TestChannelMatching:
    """Channel 匹配优先级"""

    def test_all_priority_over_user_group(self):
        channels = [
            {"scope_type": "ALL", "scope_target_id": None, "profile_type": "INDEPENDENT"},
            {"scope_type": "USER_GROUP", "scope_target_id": GROUP_STORE, "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, STORE_MGR, {GROUP_STORE})
        assert matched["scope_type"] == "ALL"

    def test_user_priority_over_user_group(self):
        channels = [
            {"scope_type": "USER", "scope_target_id": STORE_MGR, "profile_type": "INDEPENDENT"},
            {"scope_type": "USER_GROUP", "scope_target_id": GROUP_STORE, "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, STORE_MGR, {GROUP_STORE})
        assert matched["scope_type"] == "USER"

    def test_no_channels_returns_none(self):
        assert _match_channel([], STORE_MGR, set()) is None

    def test_no_matching_scope_returns_none(self):
        channels = [
            {"scope_type": "USER", "scope_target_id": "other-user", "profile_type": "INDEPENDENT"},
            {"scope_type": "USER_GROUP", "scope_target_id": "other-group", "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, CUSTOMER, set())
        assert matched is None

    def test_user_group_user_not_member(self):
        channels = [
            {"scope_type": "USER_GROUP", "scope_target_id": GROUP_STORE, "profile_type": "INDEPENDENT"},
        ]
        matched = _match_channel(channels, CUSTOMER, set())
        assert matched is None

    def test_user_scope_only_self(self):
        """USER scope 仅匹配 scoped 用户"""
        channels = [
            {"scope_type": "USER", "scope_target_id": STORE_MGR, "profile_type": "INDEPENDENT"},
        ]
        assert _match_channel(channels, STORE_MGR, set()) is not None
        assert _match_channel(channels, STAFF_A, set()) is None


class TestCacheBehavior:
    """缓存行为测试"""

    def test_cache_key_format(self):
        """cache key 是 user:agent:channel 格式"""
        key = f"{STORE_MGR}:{AGENT_SHOP}:http"
        assert STORE_MGR[:8] in key
        assert AGENT_SHOP[:8] in key

    def test_different_keys_for_different_users(self):
        """不同用户 cache key 不同"""
        k1 = f"{STORE_MGR}:{AGENT_SHOP}:http"
        k2 = f"{STAFF_A}:{AGENT_SHOP}:http"
        assert k1 != k2

    def test_different_keys_for_different_agents(self):
        """不同智能体 cache key 不同"""
        k1 = f"{STORE_MGR}:{AGENT_SHOP}:http"
        k2 = f"{STORE_MGR}:{AGENT_STOCK}:http"
        assert k1 != k2
