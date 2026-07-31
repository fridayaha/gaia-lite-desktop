"""用户信息序列化器 + config.yaml memory 段 单元测试（纯函数）。"""

from types import SimpleNamespace

from app.models import BusinessUserBinding, Role, User, UserGroup
from app.services.user_info_renderer import serialize_user_context
from app.worker._common import build_profile_config_yaml as _build_profile_config_yaml

# ═══════════════════════════════════════════════════════════
# serialize_user_context
# ═══════════════════════════════════════════════════════════


def _make_user(**kwargs) -> User:
    u = User(
        username=kwargs.get("username", "alice"),
        real_name=kwargs.get("real_name"),
        email=kwargs.get("email", "alice@example.com"),
        phone=kwargs.get("phone"),
        hashed_password="secret-hash",
        is_active=True,
    )
    if "roles" in kwargs:
        u.roles = kwargs["roles"]
    if "groups" in kwargs:
        u.groups = kwargs["groups"]
    return u


def _make_binding(**kwargs) -> BusinessUserBinding:
    return BusinessUserBinding(
        business_username=kwargs.get("business_username", "LiuWei"),
        business_phone=kwargs.get("business_phone"),
        business_email=kwargs.get("business_email"),
    )


class TestSerialize:
    def test_basic_fields(self):
        out = serialize_user_context(_make_user())
        assert out["fields"]["用户名"] == "alice"
        assert out["fields"]["邮箱"] == "alice@example.com"
        assert out["business"] == {}

    def test_real_name_and_phone_rendered(self):
        u = _make_user(real_name="张三", phone="13800000000")
        out = serialize_user_context(u)
        assert out["fields"]["真实姓名"] == "张三"
        assert out["fields"]["手机号"] == "13800000000"

    def test_optional_fields_skipped_when_none(self):
        u = _make_user()  # real_name/phone 默认 None
        out = serialize_user_context(u)
        assert "真实姓名" not in out["fields"]
        assert "手机号" not in out["fields"]

    def test_excludes_sensitive_columns(self):
        out = serialize_user_context(_make_user())
        fields = out["fields"]
        # 密码哈希、id、is_active、时间戳、登录态、审核状态绝不出现
        assert "secret-hash" not in " ".join(fields.values())
        assert "hashed_password" not in fields
        assert "is_active" not in fields
        assert "created_at" not in fields
        assert "updated_at" not in fields
        assert "email_verified" not in fields
        assert "failed_login_count" not in fields
        assert "locked_until" not in fields
        assert "last_login_at" not in fields
        assert "last_login_ip" not in fields

    def test_roles_and_groups(self):
        role = Role(name="平台管理员")
        group = UserGroup(name="门店A组", code="a")
        u = _make_user(roles=[role], groups=[group])
        out = serialize_user_context(u)
        assert out["fields"]["角色"] == "平台管理员"
        assert out["fields"]["用户组"] == "门店A组"

    def test_multiple_roles_joined(self):
        u = _make_user(roles=[Role(name="平台管理员"), Role(name="运营")])
        out = serialize_user_context(u)
        assert out["fields"]["角色"] == "平台管理员, 运营"

    def test_empty_roles_groups_omitted(self):
        u = _make_user(roles=[], groups=[])
        out = serialize_user_context(u)
        assert "角色" not in out["fields"]
        assert "用户组" not in out["fields"]

    def test_adaptive_new_column(self, monkeypatch):
        """User 表新增列后无需改序列化器即自动带入。"""

        class _FakeCol:
            def __init__(self, name):
                self.name = name

        fake_table = SimpleNamespace(
            columns=[_FakeCol("username"), _FakeCol("phone"), _FakeCol("hashed_password")]
        )
        monkeypatch.setattr(User, "__table__", fake_table)

        u = _make_user()
        u.phone = "13800000000"
        out = serialize_user_context(u)
        assert out["fields"]["用户名"] == "alice"
        assert out["fields"]["手机号"] == "13800000000"
        assert "hashed_password" not in out["fields"]

    def test_none_values_skipped(self):
        u = _make_user(username="bob", email=None)
        out = serialize_user_context(u)
        assert out["fields"]["用户名"] == "bob"
        assert "邮箱" not in out["fields"]

    def test_business_binding_fields(self):
        binding = _make_binding(business_phone="13900000000", business_email="lw@corp.com")
        out = serialize_user_context(_make_user(), binding)
        assert out["business"]["业务用户名"] == "LiuWei"
        assert out["business"]["业务手机号"] == "13900000000"
        assert out["business"]["业务邮箱"] == "lw@corp.com"

    def test_business_binding_none(self):
        out = serialize_user_context(_make_user(), None)
        assert out["business"] == {}

    def test_business_optional_skipped(self):
        binding = _make_binding(business_phone=None, business_email=None)
        out = serialize_user_context(_make_user(), binding)
        assert out["business"] == {"业务用户名": "LiuWei"}


# ═══════════════════════════════════════════════════════════
# config.yaml memory 段（user_profile_enabled 开关仍保留，Hermes 自管 USER.md 画像）
# ═══════════════════════════════════════════════════════════


class TestProfileConfigYaml:
    def test_includes_user_profile_enabled(self):
        yaml = _build_profile_config_yaml({"litellm": {"model": "gpt-4o"}}, {})
        assert "memory:" in yaml
        assert "user_profile_enabled: true" in yaml

    def test_memory_block_present_with_disabled_skills(self):
        yaml = _build_profile_config_yaml({}, {"skills": [{"name": "x", "enabled": False}]})
        assert "memory:" in yaml
        assert "user_profile_enabled: true" in yaml
        assert "- x" in yaml
