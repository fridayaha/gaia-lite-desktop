from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator, model_validator
from typing import Annotated, Literal, Optional
from datetime import datetime, timezone
from uuid import UUID
from app.models import (
    AgentStatus, EngineType, DeploymentStatus, ProfileType,
    DefinitionStatus, MarketplaceStatus, DifyEngineMode,
    ArticleStatus,
)
import json


# =========================================
# 密码强度校验（0.8.103+）
# 用 zxcvbn-python 评估 score 0-4，要求 ≥ 3（强）+ 长度 ≥ 8 + 不在常见弱密码黑名单。
# 仅对 UserCreate.password / UserUpdate.password / ChangePasswordRequest.new_password 生效，
# LoginRequest.password 不校验（避免锁出现有弱密码用户）。
# =========================================

WEAK_PASSWORD_BLACKLIST = {
    # top 常见弱密码（不区分大小写匹配）
    "12345678", "123456789", "1234567890", "password", "password1",
    "password12", "password123", "qwerty123", "11111111", "00000000",
    "abc12345", "letmein1", "admin123", "root1234", "passw0rd",
    "passw0rd1", "iloveyou1", "welcome1", "monkey123", "dragon12",
    "sunshine1", "princess1", "football1", "baseball1", "master12",
    "shadow12", "michael1", "jennifer1", "jordan23", "hunter12",
    "killer12", "trustno1", "superman1", "batman12", "spiderman",
    "anthony1", "jessica1", "hannah12", "charlie1", "austin12",
    "jasmine1", "thomas12", "triangle", "samuel12", "stupid12",
    "computer", "whatever", "starwars1", "minecraft", "ninja123",
    "asdf1234", "qwertyui", "1q2w3e4r", "1q2w3e4r5t", "zxcvbn12",
    "qazwsx12", "qweasd12", "abcd1234", "abc12345", "1234abcd",
    "p@ssw0rd", "p@ssword", "p@ss1234", "admin@123", "root@1234",
    "test1234", "guest123", "user1234", "demo1234", "temp1234",
    "changeme", "changeme1", "secret12", "summer12", "winter12",
    "spring12", "autumn12", "january1", "february", "march12",
    "april123", "may12345", "june1234", "july1234", "august12",
    "september", "october", "november", "december",
    "11111111", "22222222", "33333333", "44444444", "55555555",
    "66666666", "77777777", "88888888", "99999999",
}


# zxcvbn-python feedback.warning 英文句子 → 中文映射表
# zxcvbn-python（与前端 @zxcvbn-ts/language-en 不同）返回完整英文句子，不是 key
_ZXCVBN_WARNING_CN = {
    "This is a top-10 common password.": "该密码是 top-10 最常见密码，攻击者会优先试它",
    "This is a top-100 common password.": "该密码是 top-100 常见密码，攻击者会优先试它",
    "This is a common password.": "该密码过于常见",
    "This is similar to a commonly used password.": (
        "密码包含常见弱序列或字典词（如 12345678、qwerty、password），建议打散数字部分或换词"
    ),
    "A word by itself is easy to guess.": "单词本身易被猜中",
    "Names and surnames by themselves are easy to guess.": "单个人名或姓氏易被猜中",
    "Common names and surnames are easy to guess.": "常见人名姓氏易被猜中",
    "Capitalization doesn't help very much.": "仅首字母大写帮助不大",
    "All-uppercase is almost as easy to guess as all-lowercase.": "全大写与全小写一样易猜",
    "Reversed words aren't much harder to guess.": "反转常见词帮助不大",
    "Predictable substitutions like '@' instead of 'a' don't help very much.": (
        "可预测的字符替换（如 @ 替 a）帮助不大"
    ),
    'Sequences like "abc" or "6543" are easy to guess.': "字符序列（如 abc、6543）易被猜中",
    "Recent years are easy to guess.": "近期年份易被猜中",
    "Dates are often easy to guess.": "日期易被猜中",
    "This is a very common password.": "该密码过于常见",
}


def _validate_password_strength(v: str) -> str:
    """zxcvbn score ≥ 3 + 长度 ≥ 8 + 不在黑名单。失败抛 ValueError（pydantic 转 422）。

    所有 error message 均为中文，按 score 分级给出可执行建议。
    失败时优先用 zxcvbn 的 warning 给具体原因，让用户知道为什么弱 + 怎么改。
    """
    if len(v) < 8:
        raise ValueError("密码至少 8 位")
    if v.lower() in WEAK_PASSWORD_BLACKLIST:
        raise ValueError("该密码过于常见，请换一个")
    try:
        from zxcvbn import zxcvbn
    except ImportError:
        # zxcvbn-python 未装时降级为长度 + 黑名单校验（不阻塞功能）
        return v
    result = zxcvbn(v)
    score = result["score"]
    if score < 3:
        # 优先用 zxcvbn 给的具体 warning（翻译成中文），让用户知道为什么弱
        warning_en = result.get("feedback", {}).get("warning") or ""
        cn_warning = _ZXCVBN_WARNING_CN.get(warning_en, "") if warning_en else ""
        if score <= 1:
            base = "密码强度过低"
        else:
            base = "密码强度一般"
        if cn_warning:
            raise ValueError(f"{base}：{cn_warning}")
        # 无具体 warning 时给通用建议
        if score <= 1:
            raise ValueError(f"{base}，请使用大小写字母 + 数字 + 符号的组合，长度 ≥ 8 位")
        raise ValueError(f"{base}，建议增加长度或添加更多字符类型（大小写字母/数字/符号）")
    return v


# =========================================
# Auth
# =========================================

class LoginRequest(BaseModel):
    username: str
    password: str
    # captcha 字段可选：默认登录不要验证码，failed_login_count >= 2 后才要求（前端收到 400 captcha_required 后显示 UI）
    captcha_id: Optional[str] = None
    captcha_answer: Optional[str] = None


class LoginByContactRequest(BaseModel):
    """已认证邮箱/手机 + 密码登录（0.8.116）。
    contact_type=email 时按 email + email_verified=True 查 User；
    contact_type=phone 时按 phone + phone_verified=True 查 User。
    防枚举：用户不存在/密码错/未认证统一 401 invalid_credentials。
    captcha_id + captcha_answer：图形验证码可选，failed_login_count >= 2 后才要求（同 /login 逻辑）。
    """
    contact: str
    contact_type: Literal["email", "phone"]
    password: str
    captcha_id: Optional[str] = None
    captcha_answer: Optional[str] = None


class LoginBySmsCodeRequest(BaseModel):
    """已认证手机号 + 短信验证码登录（0.8.116，无需密码）。
    code 通过 verify_code 验证即证明手机所有权。
    """
    phone: str
    code: str = Field(..., min_length=6, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    refresh_token: str = Field(..., alias="refreshToken")


# =========================================
# User
# =========================================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=128)
    real_name: Optional[str] = Field(None, max_length=128)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=32)
    password: str = Field(..., min_length=8)
    role_ids: list[UUID] = []

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class UserUpdate(BaseModel):
    username: Optional[str] = None
    real_name: Optional[str] = Field(None, max_length=128)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=32)
    password: Optional[Annotated[str, StringConstraints(min_length=8)]] = None
    is_active: Optional[bool] = None
    role_ids: Optional[list[UUID]] = None

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_password_strength(v)


class UserResponse(BaseModel):
    id: UUID
    username: str
    real_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    email_verified: bool = False
    phone_verified: bool = False
    avatar_url: Optional[str] = None
    is_active: bool
    # 0.8.103 登录安全加固：暴露 last_login 供前端用户列表展示
    last_login_at: Optional[datetime] = None
    last_login_ip: Optional[str] = None
    last_login_user_agent: Optional[str] = None
    # 0.8.106 admin 解锁能力：暴露锁定状态字段供前端展示 + 解锁按钮
    failed_login_count: int = 0
    locked_until: Optional[datetime] = None
    is_locked: bool = False
    locked_remaining_seconds: Optional[int] = None
    roles: list[str] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    page_size: int


class UserVerifyCode(BaseModel):
    """admin 认证用户邮箱/手机时输入的验证码 — 仅 code 一个字段，target/purpose 从 path 推。"""
    code: str = Field(..., min_length=6, max_length=6)


# =========================================
# Role
# =========================================

class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    permission_ids: list[UUID] = []


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permission_ids: Optional[list[UUID]] = None


class RoleResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    permission_codes: list[str] = []
    user_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


# =========================================
# Permission
# =========================================

class PermissionResponse(BaseModel):
    id: UUID
    name: str
    code: str
    description: str | None = None
    resource_type: str

    model_config = {"from_attributes": True}


# =========================================
# EngineInstance
# =========================================









# =========================================
# Agent
# =========================================









# =========================================
# UserGroup
# =========================================

class UserGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    member_ids: list[UUID] = []


class UserGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    member_ids: Optional[list[UUID]] = None


class UserGroupResponse(BaseModel):
    id: UUID
    name: str
    code: str
    description: str | None = None
    member_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


# =========================================
# Channel (IM 渠道配置)
# =========================================









# =========================================
# Agent Access (终端用户门户)
# =========================================





class UserGroupDetailResponse(BaseModel):
    id: UUID
    name: str
    code: str
    description: str | None = None
    member_count: int = 0
    created_at: datetime
    members: list[dict] = []

    model_config = {"from_attributes": True}


# =========================================
# IM User Binding
# =========================================

class ImBindingCreate(BaseModel):
    channel_type: str = Field(..., pattern="^(wecom|wecom_bot_callback|feishu|dingtalk)$")
    im_user_id: str = Field(..., min_length=1, max_length=256)
    im_user_name: Optional[str] = None


class ImBindingResponse(BaseModel):
    id: UUID
    user_id: UUID
    channel_type: str
    im_user_id: str
    im_user_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ImBindingListResponse(BaseModel):
    items: list[ImBindingResponse]
    total: int


# =========================================
# Business User Binding（1:1，业务系统用户身份）
# =========================================

class BusinessBindingCreate(BaseModel):
    business_username: str = Field(..., min_length=1, max_length=128)
    business_phone: Optional[str] = None
    business_email: Optional[str] = None


class BusinessBindingResponse(BaseModel):
    id: UUID
    user_id: UUID
    business_username: str
    business_phone: Optional[str] = None
    business_email: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# =========================================
# LiteLLM 模型网关
# =========================================

class LiteLLMModelGroup(BaseModel):
    """模型组（LiteLLM model_name），供 Agent 表单选择"""
    model_group: str
    model: str = ""
    provider: str = ""
    input_cost_per_1m_tokens: Optional[float] = None
    output_cost_per_1m_tokens: Optional[float] = None
    context_length: Optional[int] = None  # 模型上下文窗口（写入引擎 config.yaml，跳过 hermes 探针）


class LiteLLMModelCreate(BaseModel):
    """新增上游供应商 deployment（对应 LiteLLM /model/new）"""
    model_name: str = Field(..., description="模型组名，Agent 选用此名")
    model: str = Field(..., description="上游 litellm model，如 openai/gpt-4o、anthropic/claude-3-5-sonnet")
    api_key: str = Field(..., description="上游供应商 API Key")
    api_base: Optional[str] = None
    custom_llm_provider: Optional[str] = None
    context_length: Optional[int] = Field(None, description="模型上下文窗口大小，写入 LiteLLM model_info")


class LiteLLMModelUpdate(BaseModel):
    """更新上游供应商 deployment（对应 LiteLLM /model/update）。

    model_name(组名)不可改；仅更新上游参数，留空字段=不变。
    """
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    custom_llm_provider: Optional[str] = None
    context_length: Optional[int] = Field(None, description="模型上下文窗口大小，更新 LiteLLM model_info")


class LiteLLMModelPriceUpdate(BaseModel):
    """更新 deployment 的 pricing（对应 PUT /models/{id}/price）。

    单位 USD / 1M tokens（易读），manager 转 per token 写回 LiteLLM。
    留空字段=不变；传 0 表示明确设为免费。
    """
    input_cost_per_1m_tokens: Optional[float] = None
    output_cost_per_1m_tokens: Optional[float] = None


class LiteLLMKeyCreate(BaseModel):
    """生成虚拟 Key（归属 UserGroup 对应 Team）"""
    group_id: UUID
    models: Optional[list[str]] = None
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None  # e.g. "30d"
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    duration: Optional[str] = None  # e.g. "30d"
    key_alias: Optional[str] = None


class LiteLLMKeyUpdate(BaseModel):
    models: Optional[list[str]] = None
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    duration: Optional[str] = None


# =========================================
# V3 三层模型：ResourcePool / AgentDefinition / AgentVersion / AgentInstance
# =========================================

# ---- ResourcePool ----

class ResourcePoolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    group_id: Optional[UUID] = None  # None=平台共享池；指定=组私有池
    min_cpu: str = "100m"
    max_cpu: str = "2"
    min_memory: str = "256Mi"
    max_memory: str = "2Gi"
    min_replicas: int = 1
    max_replicas: int = 5
    max_sessions_per_pod: int = 20
    auto_recycle: bool = True
    idle_suspend_minutes: int = 30
    idle_destroy_hours: int = 24


class ResourcePoolUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    min_cpu: Optional[str] = None
    max_cpu: Optional[str] = None
    min_memory: Optional[str] = None
    max_memory: Optional[str] = None
    min_replicas: Optional[int] = None
    max_replicas: Optional[int] = None
    max_sessions_per_pod: Optional[int] = None
    auto_recycle: Optional[bool] = None
    idle_suspend_minutes: Optional[int] = None
    idle_destroy_hours: Optional[int] = None


class ResourcePoolResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    group_id: Optional[UUID] = None
    group_name: Optional[str] = None
    min_cpu: str
    max_cpu: str
    min_memory: str
    max_memory: str
    min_replicas: int
    max_replicas: int
    max_sessions_per_pod: int
    auto_recycle: bool
    idle_suspend_minutes: int
    idle_destroy_hours: int
    created_by: UUID
    creator_name: str = ""
    instance_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ResourcePoolListResponse(BaseModel):
    items: list[ResourcePoolResponse]
    total: int
    page: int
    page_size: int


# ---- AgentDefinition ----

class AgentDefinitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    group_id: UUID
    avatar_color: str = Field(
        default="#6366f1",
        max_length=7,
        pattern=r"^#[0-9a-fA-F]{6}$",
    )
    engine_type: EngineType = EngineType.HERMES
    persona_config: Optional[dict] = None
    model_settings: Optional[dict] = None    # DB column: model_config
    skill_config: Optional[dict] = None
    memory_config: Optional[dict] = None


class AgentDefinitionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_color: Optional[str] = Field(
        default=None,
        max_length=7,
        pattern=r"^#[0-9a-fA-F]{6}$",
    )
    engine_type: Optional[EngineType] = None
    persona_config: Optional[dict] = None
    model_settings: Optional[dict] = None    # DB column: model_config
    skill_config: Optional[dict] = None
    memory_config: Optional[dict] = None


class AgentDefinitionResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    avatar_color: str = "#6366f1"
    engine_type: EngineType
    status: DefinitionStatus
    group_id: UUID
    group_name: str = ""
    current_version_id: Optional[UUID] = None
    current_version_no: Optional[str] = None
    marketplace_status: MarketplaceStatus = MarketplaceStatus.PRIVATE
    persona_config: dict = {}
    model_settings: dict = {}                # DB column: model_config
    skill_config: dict = {}
    memory_config: dict = {}
    created_by: UUID
    creator_name: str = ""
    instance_count: int = 0
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None
    has_unpublished_changes: bool = False

    model_config = {"from_attributes": True}

    @field_validator("persona_config", "model_settings", "skill_config", "memory_config", mode="before")
    @classmethod
    def _parse_json(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v else {}
        return v or {}


class AgentDefinitionListResponse(BaseModel):
    items: list[AgentDefinitionResponse]
    total: int
    page: int
    page_size: int


# ---- AgentVersion ----

class AgentVersionResponse(BaseModel):
    id: UUID
    definition_id: UUID
    version_no: str
    persona_config: dict = {}
    model_settings: dict = {}                # DB column: model_config
    skill_config: dict = {}
    memory_config: dict = {}
    engine_type: EngineType
    change_log: str = ""
    created_by: UUID
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("persona_config", "model_settings", "skill_config", "memory_config", mode="before")
    @classmethod
    def _parse_json(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v else {}
        return v or {}


class PublishVersionRequest(BaseModel):
    change_log: str = ""


# ---- AgentInstance ----

class AgentInstanceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    definition_id: UUID
    version_id: Optional[UUID] = None      # 不传则用 definition.current_version_id
    resource_pool_id: Optional[UUID] = None  # Dify 外接模式不需要资源池
    group_id: UUID                          # 归属用户组（隔离单元）
    dify_config: Optional[dict] = None      # Dify 应用对接配置（per-instance，仅 DIFY 引擎）
    runtime_config: Optional[dict] = None   # 运行时开关 per-instance，如 {"browser_sandbox": {"enabled": true}}


class AgentInstanceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version_id: Optional[UUID] = None
    resource_pool_id: Optional[UUID] = None
    group_id: Optional[UUID] = None         # 修改归属用户组（连带 definition/resource_pool 需重新校验）
    dify_config: Optional[dict] = None      # 修改 Dify 应用绑定（仅 DIFY 引擎实例）
    runtime_config: Optional[dict] = None   # 修改运行时开关（如浏览器沙箱启用）


class AgentInstanceResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    definition_id: UUID
    definition_name: str = ""
    version_id: Optional[UUID] = None
    version_no: Optional[str] = None
    definition_current_version_id: Optional[UUID] = None
    has_newer_version: bool = False
    resource_pool_id: Optional[UUID] = None
    resource_pool_name: str = ""
    engine_type: Optional[str] = None
    group_id: UUID
    group_name: str = ""
    status: AgentStatus
    litellm_config: dict = {}
    dify_config: dict = {}
    runtime_config: dict = {}
    created_by: UUID
    creator_name: str = ""
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AgentInstanceListResponse(BaseModel):
    items: list[AgentInstanceResponse]
    total: int
    page: int
    page_size: int


class AccessibleInstanceResponse(BaseModel):
    """终端门户可见的实例（accessible 端点）— 对齐 portal AccessibleAgent。"""

    id: UUID
    name: str
    description: str | None = None
    engine_type: str | None = None
    # 浏览器沙箱是否启用：终端门户据此决定是否展示「云桌面」入口（不泄露整个 runtime_config）
    browser_sandbox_enabled: bool = False

    model_config = {"from_attributes": True}


# ---- AgentInstanceChannel ----

# 敏感字段（secret/key/token）回显掩码：有值时统一显示固定长度星号，不泄露真实长度。
# 前端编辑态原样回填到密码框，@focus 时清空以便输入新值；保存时空值或掩码均保留原值。
SENSITIVE_MASK = "********"


class AgentInstanceChannelCreate(BaseModel):
    channel_type: str = Field(..., pattern="^(wecom|wecom_bot_callback|feishu|dingtalk)$")
    scope_type: str = "ALL"
    scope_target_id: Optional[UUID] = None
    profile_type: str = "INDEPENDENT"
    config: dict


class AgentInstanceChannelUpdate(BaseModel):
    config: Optional[dict] = None
    enabled: Optional[bool] = None
    scope_type: Optional[str] = None
    scope_target_id: Optional[UUID] = None
    profile_type: Optional[str] = None


class AgentInstanceChannelResponse(BaseModel):
    id: UUID
    instance_id: UUID
    channel_type: str
    scope_type: str = "ALL"
    scope_target_id: Optional[UUID] = None
    profile_type: str = "INDEPENDENT"
    enabled: bool
    callback_url: Optional[str] = None
    config: dict = {}
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("config", mode="before")
    @classmethod
    def mask_sensitive_config(cls, v):
        if not isinstance(v, dict):
            return {}
        sensitive = ("secret", "key", "token")
        out = {}
        for k, val in v.items():
            if any(s in k.lower() for s in sensitive):
                # 有值 → 固定星号掩码（不泄露长度）；无值 → 空串
                out[k] = SENSITIVE_MASK if val else ""
            else:
                out[k] = val
        return out


class AgentInstanceChannelListResponse(BaseModel):
    items: list[AgentInstanceChannelResponse]
    total: int


# ---- AgentApiKey（OpenAI 兼容 API Key）----

class AgentApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class AgentApiKeyResponse(BaseModel):
    id: UUID
    instance_id: UUID
    name: str
    key_prefix: str  # 前 14 字符，如 sk-abcd1234efgh
    last_used_at: Optional[datetime] = None
    last_used_ip: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentApiKeyListResponse(BaseModel):
    items: list[AgentApiKeyResponse]
    total: int


class AgentApiKeyCreateResponse(BaseModel):
    """创建响应：包含明文 key，仅此一次返回。"""
    id: str
    name: str
    key_prefix: str
    key: str  # 明文，仅创建时返回
    created_at: datetime


# =========================================
# Community（社区技术文章）
# =========================================

class ArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(None, min_length=1, max_length=200, description="留空自动从 title 生成")
    excerpt: Optional[str] = Field(None, max_length=500)
    content: str = Field(..., min_length=1)


class ArticleUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    excerpt: Optional[str] = Field(None, max_length=500)
    content: Optional[str] = Field(None, min_length=1)


class ArticleAuditRequest(BaseModel):
    approve: bool
    reject_reason: Optional[str] = Field(None, max_length=1000, description="驳回时必填")


class ArticleResponse(BaseModel):
    id: str
    author_id: str
    author_name: Optional[str] = None
    title: str
    slug: str
    excerpt: Optional[str] = None
    content: str
    status: ArticleStatus
    reject_reason: Optional[str] = None
    published_at: Optional[datetime] = None
    view_count: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

    @classmethod
    def from_article(cls, article) -> "ArticleResponse":
        """从 Article ORM 对象构造 response，自动从 article.author 取 username。"""
        return cls(
            id=str(article.id),
            author_id=str(article.author_id),
            author_name=getattr(getattr(article, "author", None), "username", None),
            title=article.title,
            slug=article.slug,
            excerpt=article.excerpt,
            content=article.content,
            status=article.status,
            reject_reason=article.reject_reason,
            published_at=article.published_at,
            view_count=article.view_count,
            created_at=article.created_at,
            updated_at=article.updated_at,
        )


class ArticleListItem(BaseModel):
    """列表轻量字段，不含 content（列表加载快）。"""
    id: str
    author_id: str
    author_name: Optional[str] = None
    title: str
    slug: str
    excerpt: Optional[str] = None
    status: ArticleStatus
    published_at: Optional[datetime] = None
    view_count: int
    created_at: datetime
    model_config = {"from_attributes": True}

    @classmethod
    def from_article(cls, article) -> "ArticleListItem":
        return cls(
            id=str(article.id),
            author_id=str(article.author_id),
            author_name=getattr(getattr(article, "author", None), "username", None),
            title=article.title,
            slug=article.slug,
            excerpt=article.excerpt,
            status=article.status,
            published_at=article.published_at,
            view_count=article.view_count,
            created_at=article.created_at,
        )


class ArticleListResponse(BaseModel):
    items: list[ArticleListItem]
    total: int
    page: int
    page_size: int


# =========================================
# EngineConfig（引擎级系统配置）
# =========================================

class EngineConfigUpsert(BaseModel):
    """创建/更新引擎配置。admin_password / langfuse_secret_key 留空表示不修改现有值。"""
    engine_type: EngineType = EngineType.DIFY  # v1 只支持 DIFY
    mode: DifyEngineMode = DifyEngineMode.EXTERNAL
    base_url: Optional[str] = None
    admin_email: Optional[str] = None
    admin_password: Optional[str] = Field(None, description="留空表示不修改")
    # Langfuse 集成配置（Dify 外接模式 per-app 用量反查用）
    langfuse_host: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = Field(None, description="留空表示不修改")

    @field_validator("base_url", "langfuse_host")
    @classmethod
    def validate_urls(cls, v):
        if not v:
            return None
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL 必须是 http(s):// 开头的合法 URL")
        return v.rstrip("/")

    @field_validator("mode")
    @classmethod
    def validate_mode_consistency(cls, v):
        if v not in (DifyEngineMode.MANAGED, DifyEngineMode.EXTERNAL):
            raise ValueError(f"mode 必须为 MANAGED 或 EXTERNAL，got: {v}")
        return v

    @field_validator("admin_email")
    @classmethod
    def validate_admin_email(cls, v):
        # 不用 EmailStr：Dify 自部署常用 .local / 内网域名，Pydantic 默认拒绝保留 TLD。
        # 只做基本的 user@domain 格式校验，实际校验交给 Dify login API。
        if not v:
            return None
        v = v.strip()
        if "@" not in v or len(v) < 5 or v.startswith("@") or v.endswith("@"):
            raise ValueError("admin_email 格式不合法（应为 user@domain）")
        return v


class EngineConfigResponse(BaseModel):
    """引擎配置响应。admin_password / langfuse_secret_key 不返回明文，只返回 *_configured 标记。"""
    id: UUID
    engine_type: EngineType
    mode: DifyEngineMode
    base_url: Optional[str] = None
    admin_email: Optional[str] = None
    admin_password_configured: bool = False  # 是否配了管理员账号密码
    langfuse_host: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key_configured: bool = False  # 是否配了 Langfuse secret_key
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DifyAppOption(BaseModel):
    """Dify 应用列表项（前端下拉用）。"""
    id: str
    name: str
    mode: str  # chat / agent-chat / advanced-chat / workflow
    description: Optional[str] = None


class DifyAppSelectResult(BaseModel):
    """选中 Dify 应用后返回的完整 dify 配置（前端回填到 AgentDefinition.model_config.dify）。"""
    base_url: str
    app_id: str
    app_name: str
    app_type: str  # chat / agent / workflow
    app_api_key: str  # 明文返回（前端拿到后立即提交到 model_config.dify，不入前端持久化）


class TestConnectionResult(BaseModel):
    """测试连接结果。"""
    ok: bool
    apps_count: Optional[int] = None  # 成功时返回拉到的应用数
    error: Optional[str] = None


class TestLangfuseResult(BaseModel):
    """Langfuse 连接测试结果。"""
    ok: bool
    trace_count: Optional[int] = None  # 成功时返回近 30 天 trace 总数（取 meta.totalItems）
    error: Optional[str] = None


# =========================================
# SmsConfig（短信服务商配置）
# =========================================

class SmsConfigBase(BaseModel):
    """短信配置基础字段。按 provider 切换必填字段。"""
    provider: str = Field(..., description="aliyun / tencent / huawei")
    sign_name: Optional[str] = Field(None, max_length=64)
    template_code: Optional[str] = Field(None, max_length=64)
    access_key_id: Optional[str] = Field(None, description="留空表示不修改；首次创建必填")
    access_key_secret: Optional[str] = Field(None, description="留空表示不修改；首次创建必填")
    sdk_app_id: Optional[str] = Field(None, max_length=128, description="tencent 必填")
    region: Optional[str] = Field(None, max_length=64, description="aliyun/huawei 必填，tencent 不用")
    daily_limit: int = Field(1000, ge=1, le=100000)
    interval_seconds: int = Field(60, ge=30, le=3600)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v):
        if v not in ("aliyun", "tencent", "huawei"):
            raise ValueError("provider 必须为 aliyun / tencent / huawei")
        return v


class SmsConfigCreate(SmsConfigBase):
    """POST 创建新配置。按 provider 验证必填字段。"""
    @model_validator(mode="after")
    def validate_provider_fields(self):
        if not (self.sign_name and self.template_code and self.access_key_id and self.access_key_secret):
            raise ValueError("sign_name/template_code/access_key_id/access_key_secret 必填")
        if self.provider == "tencent" and not self.sdk_app_id:
            raise ValueError("tencent 必填 sdk_app_id")
        if self.provider in ("aliyun", "huawei") and not self.region:
            raise ValueError(f"{self.provider} 必填 region")
        return self


class SmsConfigUpdate(SmsConfigBase):
    """PUT 更新配置。access_key_* 留空不修改。"""
    @model_validator(mode="after")
    def validate_provider_fields(self):
        # update 时只校验非空字段（留空 = 不修改）
        if self.sign_name == "" or self.template_code == "":
            raise ValueError("sign_name/template_code 不可为空")
        if self.provider == "tencent" and self.sdk_app_id == "":
            raise ValueError("tencent sdk_app_id 不可为空")
        if self.provider in ("aliyun", "huawei") and self.region == "":
            raise ValueError(f"{self.provider} region 不可为空")
        return self


class SmsConfigResponse(BaseModel):
    """短信配置响应。access_key_* 不返回明文，只返回 *_configured 标记。"""
    id: UUID
    provider: str
    is_active: bool
    sign_name: Optional[str] = None
    template_code: Optional[str] = None
    access_key_id_configured: bool = False
    access_key_secret_configured: bool = False
    sdk_app_id: Optional[str] = None
    region: Optional[str] = None
    daily_limit: int
    interval_seconds: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestSmsResult(BaseModel):
    """短信配置测试结果。按 provider 调对应 SDK 探活。"""
    ok: bool
    error: Optional[str] = None


# =========================================
# EmailConfig（邮件服务商配置 — multi-config）
# =========================================

class EmailConfigBase(BaseModel):
    """邮件配置基础字段。按 provider 切换必填字段。"""
    provider: str = Field(..., description="smtp / aliyun / tencent / huawei")
    # SMTP 特定
    smtp_host: Optional[str] = Field(None, max_length=255)
    smtp_port: Optional[int] = Field(None, ge=1, le=65535)
    encryption: Optional[str] = Field("ssl", description="none / ssl / starttls")
    username: Optional[str] = Field(None, max_length=255)
    password: Optional[str] = Field(None, description="留空表示不修改（update 时）；首次创建必填（smtp）")
    # 云厂商通用
    access_key_id: Optional[str] = Field(None, description="留空表示不修改；首次创建必填（cloud provider）")
    access_key_secret: Optional[str] = Field(None, description="留空表示不修改；首次创建必填（cloud provider）")
    region: Optional[str] = Field(None, max_length=64)
    from_email: Optional[str] = Field(None, max_length=255)
    # 共享
    from_name: Optional[str] = Field(None, max_length=128)
    daily_limit: int = Field(200, ge=1, le=100000)
    interval_seconds: int = Field(60, ge=30, le=3600)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v):
        if v not in ("smtp", "aliyun", "tencent", "huawei"):
            raise ValueError("provider 必须为 smtp / aliyun / tencent / huawei")
        return v

    @field_validator("encryption")
    @classmethod
    def validate_encryption(cls, v):
        if v is not None and v not in ("none", "ssl", "starttls"):
            raise ValueError("encryption 必须为 none / ssl / starttls")
        return v


class EmailConfigCreate(EmailConfigBase):
    """POST 创建新配置。按 provider 验证必填字段。"""
    @model_validator(mode="after")
    def validate_provider_fields(self):
        if self.provider == "smtp":
            if not (self.smtp_host and self.smtp_port and self.encryption and self.username and self.password):
                raise ValueError("smtp 必填 smtp_host/smtp_port/encryption/username/password")
        else:  # aliyun/tencent/huawei
            if not (self.access_key_id and self.access_key_secret and self.region and self.from_email):
                raise ValueError(f"{self.provider} 必填 access_key_id/access_key_secret/region/from_email")
        return self


class EmailConfigUpdate(EmailConfigBase):
    """PUT 更新配置。password/access_key_* 留空不修改。"""
    @model_validator(mode="after")
    def validate_provider_fields(self):
        # update 时只校验非空字段（留空 = 不修改）
        if self.provider == "smtp":
            if self.smtp_host == "" or self.username == "":
                raise ValueError("smtp_host/username 不可为空")
        else:
            if self.region == "" or self.from_email == "":
                raise ValueError("region/from_email 不可为空")
        return self


class EmailConfigResponse(BaseModel):
    """邮件配置响应。password/access_key_* 不返回明文，只返回 *_configured 标记。"""
    id: UUID
    provider: str
    is_active: bool
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    encryption: Optional[str] = None
    username: Optional[str] = None
    password_configured: bool = False
    access_key_id_configured: bool = False
    access_key_secret_configured: bool = False
    region: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    daily_limit: int
    interval_seconds: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestEmailResult(BaseModel):
    """邮件配置测试结果。按 provider 调对应 SDK 真实探活（只读 list API，不实际发邮件）。"""
    ok: bool
    error: Optional[str] = None


# =========================================
# AlertRule 告警规则配置
# =========================================

# 5 大类 × 16 子规则（与 seed_alert_rules 写入的取值保持一致）
ALERT_RULE_CATEGORIES = {
    "tracing",         # 链路追踪
    "resource",        # 资源监控
    "service_health",  # 服务健康
    "usage",           # 用量分析
    "call_analysis",   # 调用分析
}

ALERT_RULE_TYPES = {
    # tracing
    "error_trace", "high_latency", "high_tokens",
    # resource
    "high_cpu", "high_memory", "high_disk", "pod_restart",
    # service_health
    "service_down", "high_p95_latency", "low_uptime",
    # usage
    "high_daily_tokens", "high_monthly_cost", "high_agent_tokens",
    # call_analysis
    "low_success_rate", "high_p95_call_latency", "high_avg_tokens_per_request",
}

# category → 合法 rule_type 集合（配对校验用）
ALERT_CATEGORY_RULE_TYPES: dict[str, set[str]] = {
    "tracing": {"error_trace", "high_latency", "high_tokens"},
    "resource": {"high_cpu", "high_memory", "high_disk", "pod_restart"},
    "service_health": {"service_down", "high_p95_latency", "low_uptime"},
    "usage": {"high_daily_tokens", "high_monthly_cost", "high_agent_tokens"},
    "call_analysis": {"low_success_rate", "high_p95_call_latency", "high_avg_tokens_per_request"},
}

# rule_type → category 反查
ALERT_RULE_TYPE_CATEGORY: dict[str, str] = {
    rt: cat for cat, rts in ALERT_CATEGORY_RULE_TYPES.items() for rt in rts
}

# 无阈值规则（状态命中即触发）
ALERT_RULE_TYPES_NO_THRESHOLD = {"error_trace", "service_down"}

# 反向比较规则（值低于阈值才触发）
ALERT_RULE_TYPES_INVERTED = {"low_uptime", "low_success_rate"}

ALERT_SEVERITIES = {"critical", "warning"}
# 告警通知渠道类型——独立于 AgentInstance 的 IM ChannelType 枚举（场景不同：IM 双向通信 vs 告警单向推送）
ALERT_CHANNEL_TYPES = {"feishu", "dingtalk", "wecom", "email"}


def _validate_channel_config(channel_type: str, config: dict | None) -> dict:
    """校验 AlertChannel.config 结构（按 channel_type 分化）。

    - feishu/dingtalk/wecom: 必须有 webhook_url（http(s):// 开头）
    - email: 必须有非空 to 列表，每项含 @
    """
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("config 必须为对象")
    if channel_type == "email":
        to = config.get("to")
        if not isinstance(to, list) or not to:
            raise ValueError("config.to 必须为非空数组")
        for addr in to:
            if not isinstance(addr, str) or "@" not in addr:
                raise ValueError(f"config.to 中含非法邮箱地址: {addr}")
    else:
        url = config.get("webhook_url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("config.webhook_url 必须为 http(s):// URL")
    return config


class AlertRuleUpdate(BaseModel):
    """全量字段均可省略，仅传需要修改的字段。

    规则集为系统预置，rule_type / category 不可改（PUT 端点会忽略这两个字段）。
    可改字段：name / threshold / enabled / severity / description。
    """
    name: Optional[str] = Field(None, max_length=64)
    threshold: Optional[int] = None
    enabled: Optional[bool] = None
    severity: Optional[str] = None
    description: Optional[str] = None

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALERT_SEVERITIES:
            raise ValueError(f"severity 必须为 {sorted(ALERT_SEVERITIES)} 之一")
        return v


class AlertRuleResponse(BaseModel):
    id: UUID
    name: str
    category: str
    rule_type: str
    threshold: Optional[int]
    enabled: bool
    severity: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


# ── AlertChannel 告警通知渠道（独立实体，订阅规则） ──


class AlertChannelCreate(BaseModel):
    """创建告警渠道。subscribed_all=true 时 subscribed_rule_ids 自动忽略（运行时收所有）。"""
    name: str = Field(..., max_length=100)
    channel_type: str
    config: dict = Field(default_factory=dict)
    subscribed_all: bool = False
    subscribed_rule_ids: list[UUID] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("channel_type")
    @classmethod
    def _validate_channel_type(cls, v: str) -> str:
        if v not in ALERT_CHANNEL_TYPES:
            raise ValueError(f"channel_type 必须为 {sorted(ALERT_CHANNEL_TYPES)} 之一")
        return v

    @field_validator("config")
    @classmethod
    def _validate_config(cls, v: dict | None, info) -> dict:
        # info.data 此时可能还没有 channel_type（按字段顺序，channel_type 在 config 前，正常情况下有）
        ctype = info.data.get("channel_type")
        if ctype is None:
            # channel_type 校验失败时不走这里，直接返回，让 channel_type 的 validator 报错
            return v or {}
        return _validate_channel_config(ctype, v)


class AlertChannelUpdate(BaseModel):
    """全量字段均可省略，仅传需要修改的字段。subscribed_rule_ids 传入时整体替换。"""
    name: Optional[str] = Field(None, max_length=100)
    channel_type: Optional[str] = None
    config: Optional[dict] = None
    subscribed_all: Optional[bool] = None
    subscribed_rule_ids: Optional[list[UUID]] = None
    enabled: Optional[bool] = None

    @field_validator("channel_type")
    @classmethod
    def _validate_channel_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALERT_CHANNEL_TYPES:
            raise ValueError(f"channel_type 必须为 {sorted(ALERT_CHANNEL_TYPES)} 之一")
        return v

    @field_validator("config")
    @classmethod
    def _validate_config(cls, v: Optional[dict], info) -> Optional[dict]:
        if v is None:
            return v
        ctype = info.data.get("channel_type")
        # channel_type 未传时无法校验 config 结构，跳过（让前端保证二者一起传）
        if ctype is None:
            return v
        return _validate_channel_config(ctype, v)


class AlertChannelResponse(BaseModel):
    """渠道响应。subscribed_rule_ids 派生自关联表（subscribed_all=true 时为空数组）。"""
    id: UUID
    name: str
    channel_type: str
    config: dict
    subscribed_all: bool
    subscribed_rule_ids: list[UUID] = []
    enabled: bool
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


# ── AlertEvent 告警事件历史 ──


class AlertEventResponse(BaseModel):
    id: UUID
    rule_id: Optional[UUID]
    rule_name: str
    rule_type: str
    trace_id: Optional[str]
    agent_id: Optional[str]
    severity: str
    message: str
    notified_channels: list[dict]
    created_at: datetime
    model_config = {"from_attributes": True}


# =========================================
# 验证码 + 改绑 schemas（0.8.104+，Phase 1）
# 用于 /auth/captcha / /auth/verification-code/* / /auth/reset-password
# / /auth/unlock-account / /me/change-email|change-phone
# =========================================

VerificationPurpose = Literal["reset_password", "change_phone", "change_email", "account_unlock", "verify_email", "verify_phone", "login"]
VerificationChannel = Literal["sms", "email"]


class CaptchaResponse(BaseModel):
    """图形验证码 — 后端生成 PNG，进程内 dict 存 captcha_id → answer，5min 有效。"""
    captcha_id: str
    image_base64: str  # data:image/png;base64,...


class VerificationCodeSendRequest(BaseModel):
    """发码请求 — 必先过图形验证码 + 限速，再调 active provider 发码。

    用户枚举防御：后端查 user 不存在时也返回 sent=true，但实际不发码。
    """
    channel: VerificationChannel
    target: str = Field(..., description="手机号或邮箱")
    purpose: VerificationPurpose
    captcha_id: str
    captcha_answer: str


class VerificationCodeSendResponse(BaseModel):
    """统一返回 sent=true — 不告诉前端该 target 是否存在。"""
    sent: bool = True
    expires_in: int = 600  # 10 min


class VerificationCodeVerifyRequest(BaseModel):
    channel: VerificationChannel
    target: str
    purpose: VerificationPurpose
    code: str = Field(..., min_length=6, max_length=6)


class VerificationCodeVerifyResponse(BaseModel):
    verified: bool = True
    ticket: str  # UUID，后续 reset-password / unlock-account / change-* 用


class ResetPasswordRequest(BaseModel):
    """用 ticket 重置密码 — 新密码复用 Phase 0 的 zxcvbn 校验。"""
    ticket: str
    new_password: str = Field(..., min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v):
        return _validate_password_strength(v)


class UnlockAccountRequest(BaseModel):
    """用 ticket 解锁账号（清空 failed_login_count + locked_until）。"""
    ticket: str


class ChangeEmailRequest(BaseModel):
    """已登录用户改邮箱 — 先发码到 new_email (purpose=change_email)，再调本 endpoint。"""
    new_email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)


class ChangePhoneRequest(BaseModel):
    """已登录用户改手机 — 先发码到 new_phone (purpose=change_phone)，再调本 endpoint。"""
    new_phone: str = Field(..., pattern=r"^\+\d{6,15}$|^1\d{10}$")
    code: str = Field(..., min_length=6, max_length=6)


# =========================================
# APP 发布（0.8.123+ APP 管理页面）
# admin 上传 base APK → draft 记录；编辑 name/icon/description；publish 时 patch + sign
# =========================================

class AppReleaseUpdateRequest(BaseModel):
    """PATCH 编辑元数据。display_name 必填非空，description 可空字符串。"""
    display_name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)


class AppReleasePublishRequest(BaseModel):
    """POST publish 触发 patch + sign。manager_url / gateway_url 必填 https?:// 前缀。"""
    manager_url: str = Field(..., min_length=8, max_length=512, pattern=r"^https?://.+")
    gateway_url: str = Field(..., min_length=8, max_length=512, pattern=r"^https?://.+")


class AppReleaseResponse(BaseModel):
    """APP 发布记录响应。icon_url 为相对路径（前端经 nginx /avatars/ 反代访问）。"""
    id: UUID
    platform: str  # android / harmony
    version: str
    display_name: str
    description: str
    icon_url: Optional[str] = None
    status: str
    manager_url: Optional[str] = None
    gateway_url: Optional[str] = None
    created_at: datetime
    published_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AppReleaseListResponse(BaseModel):
    """admin 列表分页响应。"""
    items: list[AppReleaseResponse]
    total: int
    page: int
    page_size: int


class AppReleaseLatestResponse(BaseModel):
    """公开端点 /public/app-releases/latest 响应（landing 下载页拉取）。"""
    id: UUID
    platform: str  # android / harmony
    display_name: str
    description: str
    icon_url: Optional[str] = None
    version: str
    size: Optional[int] = None  # 下载包字节数，landing 用于显示「约 X MB」；stat 失败为 null


# ─── 消息级反馈 / 收藏 ─────────────────────────────────────────


class MessageFeedbackUpsertRequest(BaseModel):
    """PUT /message-feedback — value=null 表示取消反馈（删除记录）。

    message_ref 锚点：客户端传 "mid:{引擎消息id}"（历史消息有稳定 id）或
    "hash:{sha256(content)[:16]}"（兜底）。value=down 时 reason 必填。
    """
    agent_id: str = Field(..., min_length=1, max_length=64)
    session_id: str = Field(..., min_length=1, max_length=128)
    message_ref: str = Field(..., min_length=1, max_length=128)
    run_id: Optional[str] = Field(None, max_length=64)
    value: Optional[Literal["up", "down"]] = None
    reason: Optional[Literal["inaccurate", "harmful", "off_topic", "other"]] = None
    comment: Optional[str] = Field(None, max_length=4000)
    content_snapshot: str = Field("", max_length=20000)


class MessageFeedbackItem(BaseModel):
    """GET /message-feedback 列表项 — 进会话时恢复按钮状态。"""
    session_id: str
    message_ref: str
    run_id: Optional[str] = None
    value: str
    reason: Optional[str] = None
    comment: Optional[str] = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageFavoriteUpsertRequest(BaseModel):
    """PUT /message-favorites — 幂等：已存在直接返回。"""
    agent_id: str = Field(..., min_length=1, max_length=64)
    session_id: str = Field(..., min_length=1, max_length=128)
    message_ref: str = Field(..., min_length=1, max_length=128)
    run_id: Optional[str] = Field(None, max_length=64)
    content_snapshot: str = Field("", max_length=20000)


class MessageFavoriteDeleteRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message_ref: str = Field(..., min_length=1, max_length=128)


class MessageFavoriteItem(BaseModel):
    """GET /message-favorites/mine 列表项。agent_name 由 join 实例表带出，实例已删为 None。"""
    id: UUID
    agent_id: str
    agent_name: Optional[str] = None
    session_id: str
    message_ref: str
    content_snapshot: str
    created_at: datetime
