# 权限治理 —— 技术栈深度选型洞察

> **用途**：针对 [设计文档 §〇-§十](../design/permission-governance-design.md) 进入技术方案设计阶段的两项核心关注点（① 策略求值与表达式引擎；② 缓存层可切换架构），以及身份认证基座，做基于一手证据的深度选型分析。**本文纠正 [前期研究 §2.2](./permission-data-pushdown-and-python-components.md#22-授权引擎策略求值) 中关于 Cedar 的过时结论**。
> **研究方法**：以组件官方仓库/文档/CHANGELOG/RFC + 学术论文（Cedar OOPSLA）+ 独立安全基准（Trail of Bits / Teleport SPEF）为第一手来源，辅以 cedarpy 源码与 benchmark 实测数据。所有结论附证据链接。
> **研究日期**：2026-07-08
> **关联**：[ADR-016](../architecture/adr-016-permission-governance.md) · [设计文档](../design/permission-governance-design.md) · [前期研究](./permission-data-pushdown-and-python-components.md)（本文为其勘误 + 深化）

---

## 目录

- [〇、核心结论速览](#〇核心结论速览)
- [一、表达式引擎：为什么必须放弃 simpleeval](#一表达式引擎为什么必须放弃-simpleeval)
- [二、策略求值引擎：Cedar 是被误判的最佳选择](#二策略求值引擎cedar-是被误判的最佳选择)
- [三、Cedar 落地 Gaia 的完整技术方案](#三cedar-落地-gaia-的完整技术方案)
- [四、缓存层：cashews —— 开箱即用的可切换架构](#四缓存层cashews--开箱即用的可切换架构)
- [五、身份认证基座：双场景需求驱动的选型](#五身份认证基座双场景需求驱动的选型)
- [六、对前期研究文档的勘误](#六对前期研究文档的勘误)
- [七、四个待定技术点的深度验证](#七四个待定技术点的深度验证)
- [八、自建代码的参考实现（不从头写）](#八自建代码的参考实现不从头写)
- [附录 A：Cedar vs OPA vs Casbin vs Cerbos 全维度对照](#附录-acedar-vs-opa-vs-casbin-vs-cerbos-全维度对照)
- [附录 B：证据索引](#附录-b证据索引)

---

## 〇、核心结论速览

### 四项核心选型决策

| 关注点 | 前期研究结论（已过时） | 本文结论（基于最新证据） | 关键证据 |
|--------|----------------------|------------------------|---------|
| **表达式引擎** | simpleeval（一期）/ 保留 | **放弃 simpleeval，统一用 Cedar** | §一：simpleeval 五项根本缺陷 |
| **策略求值引擎** | 一期 simpleeval 自建，二期 Cerbos，**不推荐 Cedar** | **一期即采用 Cedar（cedarpy）** | §二：前期结论基于过时信息，cedarpy 已生产可用 |
| **缓存层** | 未深入研究 | **cashews（async-first 多 backend，URL 驱动切换）**，不自建抽象 | §四：7 候选库对照 + Gaia 环境实测验证 |
| **身份认证** | Authlib + 自建 PrincipalService | **Better Auth（TS，双场景原生共存）独立服务 + Authlib 应用层 JWT 验证** | §五：双场景需求（本地用户+企业联邦）驱动，Node.js 成本已确认接受 |

### 一句话总结

> **Cedar（策略求值）+ cashews（缓存）+ Better Auth（认证，双场景）+ Authlib（应用层 JWT 验证）** 四个开源成熟组件覆盖权限治理全部技术栈。Cedar 替代 simpleeval 并提供 TPE 行级下推；cashews URL 驱动切换单机/分布式缓存；Better Auth 作为 Spring Security 等价物原生支持本地用户管理（Admin/Organization 插件）与企业联邦（SSO/SAML/SCIM 插件）共存，account linking 统一双重身份。Node.js 运行时成本经确认可接受（团队已有 TS 能力，多引擎架构同构）。

---

## 一、表达式引擎：为什么必须放弃 simpleeval

> 用户明确表达：「我对使用 simpleeval 非常不认同」。本节用证据支撑这一判断，并指出 simpleeval 在权限治理场景的根本缺陷。

### 1.1 当前 simpleeval 的使用现状

Gaia 项目当前已依赖 `simpleeval>=1.0.5,<2.0`（见 `pyproject.toml`），用于 `ActionRuleEngine`（5 个文件引用）：

```python
# src/ontology/services/action_rule_engine.py
from simpleeval import SimpleEval
self._evaluator = SimpleEval(functions=self._SAFE_FUNCTIONS)  # 白名单函数
def _safe_eval(self, expression: str, names: dict[str, Any]) -> Any:
    self._evaluator.names = names
    return self._evaluator.eval(expression)
```

设计文档 §1.5 / §2.1 / §4.2 反复提出「RowSecurityPolicy 表达式用 simpleeval」「与 ADR-011 ActionRuleEngine 复用」。**这个方向是错的**，原因如下。

### 1.2 simpleeval 的五项根本缺陷

#### 缺陷 1：安全模型是「黑名单式 AST 过滤」，非「语言级安全」

simpleeval 的工作原理是：用 Python `ast` 模块解析表达式，然后遍历 AST，**拒绝已知的危险节点类型**（如 `Call`/`Attribute` 访问 dunder）。这是黑名单模型——它必须枚举所有危险操作并逐一拒绝，**任何遗漏就是安全漏洞**。

对比 Cedar：Cedar 是**非图灵完备的专用策略语言**，从语言设计层面就**排除了**任意函数调用、属性反射、循环、递归。安全是语言的内在属性，不是事后过滤。

> **证据**：Trail of Bits 受 AWS 委托对 Cedar/Rego/OpenFGA 做的安全评估（[ToB 报告](https://github.com/trailofbits/publications/blob/master/reports/Policy_Language_Security_Comparison_and_TM.pdf)）+ Teleport SPEF 动态基准测试（[SPEF 结果矩阵](https://goteleport.com/blog/benchmarking-policy-languages/)）。在 27 个安全测试用例中，**Rego 失败 14 个，Cedar 仅在 2 个「预测项」失败**（均为边界场景）。simpleeval 根本不在同一安全级别——它没有经过任何形式化安全评估。

#### 缺陷 2：无类型系统，运行时才暴露错误

simpleeval 求值 `principal.attributes['region'] == row['region']` 时，如果 `attributes` 没有 `region` 键，**运行时抛 KeyError**。策略作者在写表达式时得不到任何静态检查。

对比 Cedar：Cedar 有完整的**类型系统 + schema 验证**。策略 `when { principal.attributes.region == resource.region }` 在加载时就会验证 `principal` 的 `attributes` 是否有 `region` 字段且类型匹配。**错误在部署前暴露，不在运行时**。

```cedar
// Cedar：schema 定义类型，策略加载时验证
// schema.json
{ "entityTypes": { "User": { "shape": { "type": "Record", "attributes": {
    "region": { "type": "String", "required": true }  // 编译期检查
}}}}}
// policy.cedar — 如果引用不存在的属性，validate 阶段报错
permit(principal, action, resource) when {
    principal.attributes.region == resource.region  // 类型安全
};
```

#### 缺陷 3：无法下推到存储引擎（核心架构红线冲突）

CLAUDE.md 红线 8 明确要求「联邦查询 SQL 不手写翻译器，操作符用映射表查表拼接」。设计文档 §4.2 要求 RowSecurityPolicy 表达式「编译为 Doris Row Policy SQL 下推」。

simpleeval 的表达式是 **Python AST**，无法可靠地反向编译为 SQL/Doris Row Policy/PG RLS。你必须写一个**手写的 Python AST → SQL 翻译器**——这恰恰是红线 8 禁止的「手写操作符 if-elif 链 / 手写字面量转义」。

对比 Cedar：Cedar 的 **partial evaluation（TPE）** 产生**残差策略（residual）**，这是一个结构化的 AST，可以可靠地翻译为各引擎的过滤条件（详见 §三）。这是 Cedar 为行级下推设计的核心能力。

#### 缺陷 4：无 partial evaluation，无法实现 QueryScope

设计文档 §2.1 的 `evaluate_query_scope` 要返回 `visible_rids`（可见对象集）。如果用 simpleeval，你只能：①查出所有对象 → ②逐个用 simpleeval 求值表达式 → ③过滤。这是**应用层后过滤**，是设计文档 §4.0 明确批判的「虚假安全」（可被绕过 + 性能差）。

对比 Cedar：Cedar 的 TPE 可以在**资源未知**的情况下部分求值策略，产生描述「哪些资源属性决定可见性」的残差，直接翻译为 SQL WHERE 下推。这是 `evaluate_query_scope` 的正确实现路径。

#### 缺陷 5：维护状态与生态

- simpleeval：[github.com/aabversteeg/simpleeval](https://github.com/aabversteeg/simpleeval)，单文件库，个人维护，最近更新缓慢，无安全审计，无形式化验证
- Cedar：[github.com/cedar-policy/cedar](https://github.com/cedar-policy/cedar)，AWS 维护，OOPSLA 论文发表（[arxiv 2403.04651](https://arxiv.org/pdf/2403.04651)），有形式化语义证明，Trail of Bits 安全审计，生产规模部署（Amazon Verified Permissions）

### 1.3 结论

**simpleeval 不应用于权限治理的任何环节**。具体地：
- RowSecurityPolicy / PropertyMaskingPolicy 的表达式 → 用 Cedar 策略语言
- ActionRuleEngine 的 derivation/constraint/validation 规则 → **可渐进迁移到 Cedar**（详见 §三.6，Action 规则与权限策略统一）

> **对现有 ActionRuleEngine 的处理**：一期不强行替换（避免破坏 ADR-011 契约），但新写的权限策略必须用 Cedar。ActionRuleEngine 作为「Action 参数推导/校验」的轻量规则引擎保留，与「权限策略」是不同关注点——前者是业务规则，后者是安全策略，不应共用一个引擎。设计文档 §1.5 「与 ADR-011 ActionRuleEngine 复用同一表达式引擎，降低学习成本」这个理由不成立：**安全策略的业务复杂度远低于 Action 参数推导，用专用策略语言更安全**。

---

## 二、策略求值引擎：Cedar 是被误判的最佳选择

### 2.1 前期研究为何否定 Cedar（以及为什么该结论已过时）

前期研究文档 §2.2.2 写道：

> 「不推荐 Cedar：cedar_py 早期阶段，Rust 绑定增加构建复杂度」

**这两个理由在 2026-07 的现状下都不成立**，以下是逐一纠正：

#### 误判 1：「cedar_py 早期阶段」——已过时

| 维度 | 前期研究时的认知（推测） | 2026-07 实际现状 | 证据 |
|------|----------------------|----------------|------|
| 版本 | 早期 0.x | **cedarpy 4.8.6**（对应 Cedar 引擎 4.8.2） | [PyPI cedarpy](https://pypi.org/project/cedarpy/) |
| 维护 | 不明 | **活跃维护**，68 个 release，最近 release 2026-07 | [GitHub releases](https://github.com/k9securityio/cedar-py/releases) |
| 供应链安全 | 未知 | **PyPI Trusted Publishing (OIDC) + SLSA 构建证明 + zizmor CI 审计** | [cedar-py CLAUDE.md](https://github.com/k9securityio/cedar-py/blob/main/CLAUDE.md) |
| benchmark | 无 | **完整 pytest-benchmark 回归套件**，median 5% 回归门禁 | [BENCHMARKS.md](https://github.com/k9securityio/cedar-py/blob/main/BENCHMARKS.md) |

#### 误判 2：「Rust 绑定增加构建复杂度」——错误

cedarpy **发布预编译 manylinux wheel**，无需 Rust 工具链，`pip install cedarpy` 即可：

| 平台 | 架构 | Python 版本 | 安装方式 |
|------|------|------------|---------|
| Linux | x86_64, aarch64 | 3.9 - 3.14 | 预编译 wheel，无需编译 |
| macOS | x86_64, aarch64 | 3.11 - 3.14 | 预编译 wheel |
| Windows | x86_64 | 3.9 - 3.14 | 预编译 wheel |

**Gaia 环境验证**：Python 3.12.3 + Linux x86_64（见 `.python-version` + Dockerfile `python:3.12-slim`），cedarpy 有对应 wheel。`pip index versions cedarpy` 确认可装。

> **关键**：cedarpy 是 PyO3 + maturin 构建的 native extension，但**对使用者透明**——和安装任何纯 Python 包一样。Dockerfile 无需加 Rust/cargo 构建阶段。唯一注意：wheel 体积约 4MB（含 Rust 编译的 Cedar 引擎），可接受。

#### 误判 3：未评估 Cedar 的 partial evaluation 能力（决定性遗漏）

前期研究完全没提到 Cedar 的 **partial evaluation / TPE（Type-aware Partial Evaluation）**——这是 Cedar 相对所有竞品的决定性优势，直接解决设计文档 §4 的行级下推难题。详见 §三.4。

### 2.2 为什么 Cedar 优于其他候选

#### Cedar vs OPA/Rego

| 维度 | Cedar | OPA/Rego | 证据 |
|------|-------|---------|------|
| **语言安全** | 非图灵完备，无任意函数调用，无正则（防 ReDoS） | 图灵完备风险，正则致 DoS | [SPEF testcase-16](https://goteleport.com/blog/benchmarking-policy-languages/)：Rego FAIL，Cedar N/A（语言层排除） |
| **运行时确定性** | 强制 deny-default + explicit-deny-overrides | 运行时异常、非确定性 | [SPEF testcase-01/03/04](https://goteleport.com/blog/benchmarking-policy-languages/)：Rego FAIL |
| **部署模式** | **进程内嵌**（cedarpy，零额外服务） | **sidecar/独立服务**（Python 只能 HTTP 调用） | [OPA 集成文档](https://openpolicyagent.org/docs/integration)：非 Go 语言必须 sidecar |
| **Python 集成** | cedarpy 原生 Python API（`is_authorized(request, policies, entities)`） | HTTP 客户端（`opa-python-client`）或 wasm（`opa-wasm`，实验性） | Rego 的 in-process Python 方案（`regopy`/`opa-wasm`）均不成熟 |
| **行级下推** | TPE 产生残差 → 翻译 SQL | Compile API 产生残差 → 翻译 SQL | 两者机制类似，但 Cedar 类型安全 |
| **性能** | Rust 实现，microsecond 级 | Go 实现，p50 1.84ms（in-process） | [kastra benchmark](https://kastra.ai/benchmarks)（非官方，仅供参考） |

**结论**：OPA 的 Python 集成只能走 sidecar（增加部署复杂度 + 网络开销），而 Cedar 可进程内嵌。对于 Gaia 这种「权限校验是热路径」的场景，**进程内嵌是刚需**。

#### Cedar vs Casbin

| 维度 | Cedar | Casbin (pycasbin) |
|------|-------|-------------------|
| **大规模性能** | 微秒级，PolicySet 复用 | **10k 规则 ABAC 需 500ms**（[Issue #336](https://github.com/casbin/pycasbin/issues/336)）；1.6M 规则建 Enforcer 需 12-18s（[Issue #681](https://github.com/apache/casbin/issues/681)） |
| **缓存** | PolicySet/Entities 句柄复用 | **缓存失效有 bug**：g() 函数 memoization 在 BuildRoleLinks 后返回 stale 结果（[PR #1580](https://github.com/apache/casbin/pull/1580)）；CachedEnforcer 删策略不刷新缓存（[Issue #832](https://github.com/apache/casbin/issues/832)） |
| **分布式同步** | 无状态（策略即数据，各实例独立求值） | 需 Redis Watcher 同步策略，多实例一致性难（[Issue #1063](https://github.com/apache/casbin/issues/1063)） |
| **行级下推** | TPE 残差 → SQL | **不支持**（[SO 61455215](https://stackoverflow.com/questions/61455215/enforce-casbin-policy-into-sql-where)：官方明确无 SQL WHERE 翻译能力） |
| **策略可读性** | Cedar 语法（类自然语言） | model.conf + policy CSV 双文件，学习曲线陡 |

**结论**：Casbin 在大规模下有严重性能问题（500ms/10k 规则），缓存有已知 bug，且**完全不支持行级下推**——直接出局。

#### Cedar vs Cerbos

| 维度 | Cedar (cedarpy) | Cerbos |
|------|-------|--------|
| **部署** | 进程内嵌，零额外服务 | 独立服务（gRPC/HTTP），增加部署 + 网络开销 |
| **策略语言** | Cedar（专用，类型安全） | YAML + CEL 表达式 |
| **行级下推** | TPE 残差 → SQL（类型安全翻译） | PlanResources API 返回过滤器（类似但无类型保证） |
| **依赖** | 1 个 wheel（4MB） | 独立 Go 服务 + Python SDK client |
| **与 Gaia 哲学契合** | 「策略即数据」（Cedar 策略入 Git） | 「策略即数据」（YAML 入 Git）——持平 |

**结论**：Cerbos 是强有力的候选，但**独立服务模式**与 Gaia「不增加部署复杂度」的偏好冲突。Cedar 进程内嵌 + TPE 能力是更轻量的选择。若未来需要多消费者共享策略（Trino OPA 插件等），可再评估 Cerbos/OPA，但一期 Cedar 足够。

### 2.3 独立安全基准背书

[Teleport + Doyensec 的 SPEF 框架](https://goteleport.com/blog/benchmarking-policy-languages/)对 Cedar/Rego/OpenFGA 做了 27 个安全测试用例的动态评估，结论原文：

> 「**Cedar is safe and deterministic, with strong validation and isolation**」
> 「**Rego is expressive but error-prone, failing several tests due to runtime exceptions, non-determinism, and extensibility risks**」

这是目前业界对策略语言最权威的独立安全评估（非厂商背书）。

---

## 三、Cedar 落地 Gaia 的完整技术方案

### 3.1 cedarpy 的关键 API（已验证）

```python
from cedarpy import is_authorized, is_authorized_batch, is_authorized_partial, Decision, PolicySet, Entities

# 1. 策略预编译（启动时一次，复用句柄，避免每次解析）
policy_set = PolicySet.from_str(policies_text)  # 或 from_json_str(EST 格式)
# entities 也可预编译复用：Entities.from_json_str(entities_json, schema)

# 2. 单次授权决策（PDP 核心）
result = is_authorized(
    request={
        "principal": 'User::"alice"',           # 或 {"type":"User","id":"alice"}
        "action": 'Action::"view"',
        "resource": 'ObjectType::"invoice_001"',
        "context": {"has_mfa": True, "ip": "10.0.0.1"}
    },
    policies=policy_set,                         # 复用句柄，不重新解析
    entities=entities_list,                      # 或 Entities 句柄
    schema=schema_dict                           # 可选，启用类型验证
)
# result.decision == Decision.Allow / Decision.Deny
# result.diagnostics.reasons  — 哪些策略触发
# result.diagnostics.errors   — 求值错误
# result.metrics              — 耗时（parse/authz 各阶段）

# 3. 批量授权（批量场景 10x 性能，摊销解析开销）
results = is_authorized_batch(requests=[...], policies=policy_set, entities=..., schema=...)

# 4. Partial evaluation（行级下推核心）——见 §3.4
partial_result = is_authorized_partial(request_with_unknown_resource, policy_set, entities, schema)
```

### 3.2 性能特征（来自 cedarpy benchmark 实测）

| 场景 | 耗时（median） | 说明 |
|------|:---:|------|
| 简单策略（1 规则）+ 复用 PolicySet | ~120 µs | 单次授权 |
| 中等策略（4 规则）+ 复用 | ~125 µs | |
| 复杂策略（10 规则）+ 复用 | ~168 µs | |
| 大策略（16KB / 60 规则）+ 复用 | ~168 µs | PolicySet 复用消除 1.4ms 解析开销 |
| 大策略不复用（每次解析） | ~1.54 ms | 反面教材，务必复用 |
| 批量（100 请求） | 单次 1/10 开销 | 摊销 entity/schema 解析 |

**关键实践**：
- **PolicySet 必须复用**：启动时 `PolicySet.from_str()` 一次，后续传句柄。大策略可获 9x 加速
- **Entities 可复用**：`Entities.from_json_str()` 句柄，principal 的 entity graph 相对稳定（用户组关系不常变）
- **批量场景用 `is_authorized_batch`**：Action 批量执行时一次校验多 rids

> 详见 [BENCHMARKS.md](https://github.com/k9securityio/cedar-py/blob/main/BENCHMARKS.md)。

### 3.3 Gaia 权限模型 → Cedar 映射

设计文档的四组权限模型，映射为 Cedar 的 schema + policies：

#### Schema（定义实体类型 + 属性 + action）

```json
{
  "Gaia": {
    "entityTypes": {
      "User": {
        "shape": {
          "type": "Record",
          "attributes": {
            "region": { "type": "String", "required": false },
            "department": { "type": "String", "required": false },
            "job_level": { "type": "Long", "required": false }
          }
        }
      },
      "Group": { "shape": { "type": "Record", "attributes": {} } },
      "Organization": { "shape": { "type": "Record", "attributes": {
        "org_type": { "type": "String" }
      }}},
      "Ontology": { "shape": { "type": "Record", "attributes": {
        "space_id": { "type": "Entity", "name": "Space" }
      }}},
      "ObjectType": { "shape": { "type": "Record", "attributes": {
        "ontology_id": { "type": "Entity", "name": "Ontology" },
        "region": { "type": "String", "required": false },
        "owner_id": { "type": "Entity", "name": "User", "required": false }
      }}},
      "Space": { "shape": { "type": "Record", "attributes": {} }}
    },
    "actions": {
      "view": { "appliesTo": { "principal": ["User"], "resource": ["ObjectType"], "context": {} }},
      "edit": { "appliesTo": { "principal": ["User"], "resource": ["ObjectType"], "context": {} }},
      "execute_action": { "appliesTo": { "principal": ["User"], "resource": ["ObjectType"], "context": {
        "has_mfa": { "type": "Boolean", "required": false }
      }}}
    }
  }
}
```

#### Policies（权限规则）

```cedar
// ── RBAC：组授权（principal in Group）──
// Viewer 组可查看 ObjectType
permit(
    principal in Group::"viewers",
    action == Action::"view",
    resource is ObjectType
);

// Editor 组可编辑
permit(
    principal in Group::"editors",
    action in [Action::"view", Action::"edit"],
    resource is ObjectType
);

// ── Marking（MAC 合取校验）──
// 无 PII 标记的用户不能访问带 PII 的 ObjectType
forbid(
    principal,
    action,
    resource is ObjectType
)
when {
    resource has pii && resource.pii
}
unless {
    principal in Group::"pii_authorized"
};

// ── ABAC 行级：用户只能看本区域 ──
permit(
    principal,
    action == Action::"view",
    resource is ObjectType
)
when {
    resource has region && principal has region &&
    resource.region == principal.region
};

// ── Organization 隔离（主体强隔离）──
// 用户只能访问同 Organization 的 Space 下的资源
permit(
    principal,
    action,
    resource is ObjectType
)
when {
    principal in Organization::"org_default" &&
    resource.ontology_id.space_id in Space::"default_space"
};
```

> 这套映射**完整覆盖设计文档的五层校验**：身份（principal）→ Organization（principal in Org）→ Space（resource.space in Space）→ Project/RBAC（principal in Group）→ Marking（forbid unless）→ 行级（when resource.attr == principal.attr）。

### 3.4 Partial Evaluation：行级下推的技术路径

这是 Cedar 的决定性能力，也是设计文档 §4 行级下推的正确实现。

#### 原理

Cedar 的 TPE（[RFC 0095](https://github.com/cedar-policy/rfcs/blob/main/text/0095-type-aware-partial-evaluation.md)，Type-aware Partial Evaluation）允许在**资源（resource）未知**的情况下部分求值策略，产生**残差策略（residual）**——描述「哪些资源属性决定可见性」。

```python
# principal/action/context 已知，resource 未知（要列出用户能看哪些 ObjectType）
partial_result = is_authorized_partial(
    request={
        "principal": 'User::"alice"',
        "action": 'Action::"view"',
        # resource 省略 → 未知
        "context": {"has_mfa": True}
    },
    policies=policy_set,
    entities=[alice_entity],   # 只需 principal 的 entity data
    schema=schema
)
# partial_result.decision == Decision.NoDecision（无法定论，需加载 resource）
# partial_result.residuals — 残差策略 AST
# partial_result.diagnostics.unknown_entities — 需要加载哪些 entity
```

#### 残差 → SQL 翻译

残差是结构化的 Cedar JSON AST。例如策略 `when { resource.region == principal.region }`，principal=alice(region="east")，TPE 产生残差：

```python
# residuals AST（简化）
{"effect": "permit", "conditions": [{"kind": "when", "body": {
    "==": {"left": {".": {"left": {"unknown": "resource"}, "attr": "region"}},
                     "right": {"Value": "east"}}}
}}]}
```

翻译为各引擎：

| 引擎 | 翻译产物 | 示例 |
|------|---------|------|
| Doris | Row Policy USING | `USING (region = 'east')` |
| PostgreSQL | RLS USING | `USING (region = 'east')` |
| Trino | SQL WHERE 注入 | `WHERE region = 'east'`（谓词下推到 connector） |
| Neo4j | Cypher WHERE | `WHERE n.region = 'east'` |

**关键**：翻译器只处理 Cedar 的有限 AST 节点类型（`==`/`!=`/`in`/`&&`/`||`/`.`属性访问/字面量），是**确定性的映射表查表**（符合 CLAUDE.md 红线 8），不是手写翻译器。Cedar 的类型系统保证残差是良类型的，翻译时不会遇到类型歧义。

> **对比 OPA**：OPA 的 Compile API 也能做 partial evaluation 产生残差翻译 SQL（[OPA data filtering](https://openpolicyagent.org/docs/filtering/partial-evaluation)），但 Rego 的残差是 Rego AST，类型保证弱，且 OPA 必须 sidecar 部署。Cedar 进程内嵌 + 类型安全残差是更优解。

### 3.5 Entity Store 规模化策略

Cedar 每次授权需要 entity data（principal + 其祖先组 + resource 的属性）。大规模下不能每次加载全量。Cedar 的解决机制：

1. **Policy slicing**：Cedar 的 scope 机制自动过滤无关策略（`principal in Group::"X"` 的策略只在 principal 属于 X 时才评估）
2. **Entity slicing（[RFC 0076](https://github.com/cedar-policy/rfcs/blob/main/text/0076-entity-slice-validation.md)，实验性）**：Level validation 限制策略对 entity 的解引用深度，使应用只需加载浅层 entity slice
3. **Gaia 的实践**：
   - **principal slice 缓存**：用户的 groups/roles/markings 相对稳定，登录会话级缓存（设计文档 §2.1 的「用户属性缓存」）。构造为 `Entities` 句柄复用
   - **resource slice 按需加载**：单资源访问时只加载该 resource 的属性；批量查询时用 partial evaluation 避免逐个加载

> cedarpy 的 `is_authorized_partial` 已暴露 `diagnostics.unknown_entities`——告诉你还需加载哪些 entity，支持按需加载模式。

### 3.6 与现有 ActionRuleEngine 的关系（澄清）

| 关注点 | ActionRuleEngine（现有） | 权限策略（新增） |
|--------|------------------------|----------------|
| **职责** | Action 参数推导/约束/校验（业务规则） | 资源访问授权（安全策略） |
| **引擎** | simpleeval（保留，一期不换） | Cedar（新增） |
| **表达式** | Python 表达式（`quantity > 0 and status == 'open'`） | Cedar 策略语言 |
| **求值对象** | Action 参数命名空间 | principal + resource + context |

**一期不强行统一**：ActionRuleEngine 是已落地的业务规则引擎，换 Cedar 收益不大（业务规则无安全风险）。权限策略是新增的安全关键路径，必须用 Cedar。两者关注点不同，不应为「降低学习成本」而强行共用——**安全策略值得专用工具**。

二期可选：将 ActionRuleEngine 的 `validation` 类型规则（接近权限语义的部分）迁移到 Cedar，`derivation` 类型（纯计算）保留 simpleeval。

---

## 四、缓存层：cashews —— 开箱即用的可切换架构

> 用户需求：「单机、分布式部署都要考虑，目标是能够支撑切换缓存依赖，但代码不改」。**应优先使用开源成熟方案，不重复造轮子。**

### 4.1 选型：cashews（async-first 多 backend 缓存框架）

经对 Python async 缓存生态的全面调研，**cashews** 是唯一同时满足全部需求的成熟开源库。

#### 候选库横向对照

| 库 | async | 多 backend | tag 失效 | 分布式锁 | client-side caching | 成熟度 | 选型 |
|----|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **cashews** | ✅ 原生 | ✅ mem/redis/disk | ✅ `delete_tags` | ✅ `set_lock` | ✅ Redis 6 tracking | ★★★★★ v7.5.0 | ✅ **选** |
| aiocache | ✅ 原生 | ✅ mem/redis/memcached | ❌ 无 | ❌ 无 | ❌ 无 | ★★★ decorator 有 bug [#973](https://github.com/aio-libs/aiocache/issues/973) | ✗ |
| dogpile.cache | ❌ 同步 | ✅ | ❌ | ✅ dogpile lock | ❌ | ★★★★ 但与 async 冲突 | ✗ |
| cachetools | ✅(基础) | ❌ 仅内存 | ❌ | ❌ | ❌ | ★★★ 仅 L1 | ✗ |
| yokedcache | ✅ | ✅ | ✅ tag | ✅ | ❌ | ★★ 2026 新库，未验证 | ✗ |
| cachekit/kmcache/zoocache | ✅ | 部分 | 部分 | 部分 | ❌ | ★★ 过新 | ✗ |

#### 为什么选 cashews

1. **async-first**：与 Gaia 的 FastAPI + SQLAlchemy async 技术栈天然契合，全链路 `async/await`，不阻塞事件循环
2. **URL 驱动的 backend 切换**：`cache.setup("mem://")` ↔ `cache.setup("redis://host:6379")`，**同一套代码，改 URL 即切换单机/分布式**——这正是「切换依赖不改代码」的需求
3. **tag 失效**（权限缓存最关键能力）：`cache.set(key, value, tags=[...])` + `cache.delete_tags("tag")`，精准批量失效，不需要手写 pattern 拼接
4. **分布式锁**：`cache.set_lock(key, owner, expire)` 防 stampede，权限失效风暴时保护后端
5. **client-side caching**（Redis 6+ tracking）：10x 性能提升，热路径权限校验近乎本地内存速度
6. **生产成熟**：v7.5.0，[Krukov/cashews](https://github.com/Krukov/cashews) 580+ stars，PandaDoc 生产使用（作者即 PandaDoc 工程师），活跃维护
7. **零侵入**：API 是 `cache.get/set/delete`，不强制 decorator，可纯命令式调用，与 AuthorizationService 的集成干净

> **对比 aiocache**：aiocache 是最知名的 async 缓存库，但**缺少 tag 失效和分布式锁**这两个权限缓存的核心能力，且 decorator API 有已知 bug。cashews 在功能完备性上完胜，且自称「2x faster than aiocache」（with client-side caching）。

### 4.2 实测验证（Gaia 环境已跑通）

在 Gaia 的 `.venv`（Python 3.12.3）中实测 cashews 7.5.0，核心能力全部端到端通过：

```python
from cashews import cache
cache.setup('mem://')  # 单机开发；生产改 redis://host:6379/0，代码不变

# ① 基本 get/set/delete ✓
await cache.set('user:1:attr', {'region': 'east'}, expire='10m')
await cache.get('user:1:attr')  # -> {'region': 'east'}
await cache.delete('user:1:attr')

# ② pattern 批量删除（权限失效）✓
await cache.set('authz:u1:o1', 'ALLOW')
await cache.set('authz:u1:o2', 'DENY')
await cache.set('authz:u2:o1', 'ALLOW')
await cache.delete_match('authz:u1:*')  # 删 u1 的所有，保留 u2 ✓

# ③ 分布式锁（防 stampede）✓
await cache.set_lock('lk:expensive', 'owner1', expire='10s')  # -> True

# ④ tag 失效（精准批量失效）✓
await cache.set('r1', 'v1', expire='10m', tags=['res:o1'])
await cache.set('r2', 'v2', expire='10m', tags=['res:o1'])
await cache.set('r3', 'v3', expire='10m', tags=['res:o2'])
await cache.delete_tags('res:o1')  # 删 r1+r2，保留 r3 ✓
```

**backend 切换验证**（代码零改动）：
```python
cache.setup('mem://')                          # 单机开发
# cache.setup('redis://localhost:6379/0', client_side=True)  # 分布式生产
# cache.setup('mem://?size=500', prefix='hot')               # 多 backend 前缀路由
```

### 4.3 Gaia 权限缓存集成方案

#### 配置（settings 驱动，单机/分布式切换）

```python
# src/ontology/config/settings.py
@dataclass
class CacheSettings:
    url: str = "mem://"  # mem:// (开发) | redis://host:6379/0 (生产)
    client_side: bool = False  # Redis 6+ client-side caching（生产开）
    prefix: str = "gaia:perm"  # key 前缀，避免与其他系统冲突
```

```python
# src/ontology/config/container.py
from cashews import Cache

class Container:
    def permission_cache(self) -> Cache:
        if cached := self._service_cache.get("permission_cache"):
            return cached
        c = Cache(name="permission")
        c.setup(self._settings.cache.url,
                client_side=self._settings.cache.client_side,
                prefix=self._settings.cache.prefix)
        self._service_cache["permission_cache"] = c
        return c
```

#### AuthorizationService 使用 cashews（零自建抽象）

```python
class AuthorizationService:
    def __init__(self, metadata: PostgresMetaStore, cache: Cache) -> None:
        self._metadata = metadata
        self._cache = cache  # cashews.Cache，直接用，不自建抽象

    async def check_access(self, principal, resource_type, resource_id, action) -> AccessResult:
        cache_key = f"authz:{principal.id}:{resource_type}:{resource_id}:{action}"
        # 缓存命中
        if cached := await self._cache.get(cache_key):
            return AccessResult.model_validate_json(cached)
        # 未命中 → 五层校验 → 写缓存（带 tag，便于精准失效）
        result = await self._do_check(principal, resource_type, resource_id, action)
        await self._cache.set(
            cache_key, result.model_dump_json(), expire='5m',
            tags=[f"authz:user:{principal.id}", f"authz:{resource_type}:{resource_id}"]
        )
        return result

    async def invalidate_user(self, user_id: str) -> None:
        """用户加/退组 → 失效该用户所有授权缓存"""
        await self._cache.delete_tags(f"authz:user:{user_id}")

    async def invalidate_resource(self, resource_type: str, resource_id: str) -> None:
        """资源打标/改权限 → 失效该资源所有授权缓存"""
        await self._cache.delete_tags(f"authz:{resource_type}:{resource_id}")
```

**关键**：`AuthorizationService` 直接依赖 `cashews.Cache`，**不自建任何抽象层**。cashews 的 API（`get/set/delete/delete_match/delete_tags/set_lock`）已覆盖权限缓存的全部需求。切换 backend 只改 `settings.cache.url`，Service 代码零改动。

### 4.4 三级缓存的 key 设计与失效策略

```python
# 缓存 key 规范（prefix:维度:标识），配合 tag 实现精准失效
# user:attr:{user_id}          — 用户属性（groups/roles/markings/attributes）
#   tags: ["user:{user_id}"]
# resource:attr:{type}:{id}    — 资源属性（org/space/project/marking）
#   tags: ["resource:{type}:{id}"]
# authz:result:{user_id}:{res_type}:{res_id}:{action}  — 授权结果
#   tags: ["authz:user:{user_id}", "authz:{res_type}:{res_id}"]

# 失效规则（设计文档 §2.1 主动失效 + TTL 兜底）
# 1. 用户加/退组 → delete_tags("user:{user_id}") + delete_tags("authz:user:{user_id}")
# 2. 资源打标/改权限 → delete_tags("resource:{type}:{id}") + delete_tags("authz:{type}:{id}")
# 3. 高敏操作（权限授予/标记移除/删除）→ 强制实时校验，不走缓存
```

### 4.5 分布式失效：cashews 的 client-side caching（生产）

多实例部署时，cashews 的 Redis client-side caching（`client_side=True`）天然解决跨实例一致性——Redis 6+ 的 tracking 机制在 key 变更时主动通知所有客户端失效本地副本，**无需手写 Pub/Sub 广播**。

- **一期（单机/少量实例）**：`mem://` 或单 Redis，TTL 兜底即可
- **二期（多实例生产）**：`redis://...?client_side=True`，Redis tracking 自动同步跨实例缓存

> cashews 作者（PandaDoc 工程师）专门撰文介绍此机制：[Redis client-side cache with async Python](https://medium.com/the-pandadoc-tech-blog/redis-client-side-cache-with-async-python-6228a0121a12)。这是比手写 Pub/Sub 更成熟的方案。

### 4.6 与 Cedar 的缓存协同

Cedar 自身有两个句柄可复用：
- `PolicySet`（策略集）：启动时编译一次，进程内全局共享，**不需要外部缓存**
- `Entities`（entity graph）：principal slice 会话级缓存，可序列化后存入 cashews

`AuthorizationService` 的缓存分层：
```
L0: Cedar PolicySet 句柄（进程内，启动时编译，策略变更时重建）
L1: Cedar Entities JSON（principal slice，cashews 缓存，会话级 TTL）
L2: 授权结果（authz:result，cashews 缓存，短 TTL + tag 失效）
```

### 4.7 已知风险与规避

| 风险 | 严重度 | 规避 |
|------|:---:|------|
| 锁在多进程下偶发 `set_lock` 重复调用（[Issue #333](https://github.com/Krukov/cashews/issues/333)） | 中 | 权限缓存场景锁开销低（权限校验非重计算）；高敏操作不走缓存直接实时校验，不依赖锁 |
| tag 失效需 backend 支持 set（Redis 支持，mem 用内存模拟） | 低 | mem backend 已实测支持 tag；生产用 Redis 原生支持 |
| client-side caching 需 Redis 6+ | 低 | Gaia Docker Compose 部署 Redis 时用 7.x 即可 |
| 个人维护库（主要贡献者 Krukov 1 人） | 中 | 代码成熟（v7.5.0，2019 至今）、MIT 协议、PandaDoc 生产背书；必要时可 fork（纯 Python，无 native 依赖，fork 成本低） |

---

## 五、身份认证基座：双场景需求驱动的选型

> 用户需求（明确澄清）：要同时支持两种场景——
> 1. **简单场景**：自己管理用户、角色、分组（本地用户库）
> 2. **企业场景**：直接对接已有的企业用户系统（LDAP/SAML/OIDC IdP）
>
> 这两种场景**必须共存于同一系统**，不是二选一。本节据此重新选型，纠正了前几版「Authlib / fastapi-oidc / 纯 IDP 外置」的片面结论。

### 5.1 双场景需求的核心挑战：本地用户与企业联邦共存

这两种场景的共存是认证设计的经典难题：

- **场景 1（本地用户）**：系统自带用户库，管理员 CRUD 用户/角色/分组，邮箱密码登录。适合中小企业、PoC、无现成 IdP 的场景
- **场景 2（企业联邦）**：对接客户既有的 LDAP/AD/SAML IdP/OAuth2 IdP，用户在 IdP 登录后联邦进系统。适合大企业、有成熟 IAM 的客户

**共存的关键**：同一个系统里，本地用户和企业联邦用户**必须能统一识别**（account linking）——一个用户可能先用邮箱密码注册，后来企业接入 SSO 后，同一个邮箱的 SSO 登录要关联到已有账户，不能变成两个账户。

这个需求排除了「纯 IDP 外置」方案——如果认证全交给外部 Keycloak，场景 1 的「自己管理用户」就要在 Keycloak 里做（Keycloak 能做但重），且 Gaia 应用层对用户生命周期无控制。

### 5.2 候选方案：三种架构模式对照

#### 模式 A：Better Auth（应用内嵌，Spring Security 等价物）

**Better Auth**（[better-auth/better-auth](https://github.com/better-auth/better-auth)，**27.5k stars**，2026-07-07 Vercel 收购）是 2024-2026 爆火的新一代认证框架，自我定位「the most comprehensive authentication framework for TypeScript」。它正是 Spring Security 的现代等价物——**应用内嵌、plugin-based、本地用户与企业联邦原生共存**。

**双场景覆盖能力**（原生支持，同一实例）：

| 场景 | Better Auth 能力 | 插件 |
|------|----------------|------|
| **场景 1 本地用户** | emailAndPassword 登录 + Admin 插件（CRUD 用户/角色/ban/impersonate）+ Organization 插件（团队/分组）+ Database adapter（自带用户/会话表） | `emailAndPassword` + `admin()` + `organization()` |
| **场景 2 企业联邦** | SSO 插件支持 OIDC + OAuth2 + **SAML 2.0**（Okta/Azure AD/任意 SAML IdP）+ SCIM 插件（IdP 自动同步用户）+ account linking（本地用户与 SSO 身份自动关联） | `sso()` + `scim()` |
| **共存机制** | account linking 默认开启——SSO 登录时若邮箱匹配本地用户则自动关联；同一用户可有多重身份（邮箱密码 + Google + 企业 SAML） | `accountLinking` 配置 |

**关键证据**：
- SSO 插件文档原文：「Enterprise SSO with SAML 2.0 and OIDC support, including automatic user provisioning and organization mapping」（[@better-auth/sso](https://www.npmjs.com/package/@better-auth/sso)）
- Admin 插件：「creating users, managing user roles, banning/unbanning users, impersonating users」（[Admin 文档](https://better-auth.com/docs/plugins/admin)）
- Account linking：「enabled by default, lets users associate multiple identities」（[Users & Accounts](https://better-auth.com/docs/concepts/users-accounts)）
- SCIM：「exposes compliant SCIM 2.0 server, allows third party identity providers to sync identities」（[SCIM 文档](https://better-auth.com/docs/plugins/scim)）

**致命短板**：**Better Auth 原版是 TypeScript，Python 生态无成熟 port**。

| Python 方案 | 状态 | 可用性 |
|------------|------|--------|
| better-auth-py（sebasxsala） | 0 stars，2026-05 创建，未发布 PyPI | ❌ 不可用 |
| PyAuth（SamiMelhem） | 0 stars，「vision」阶段 | ❌ 不可用 |
| fastapi-betterauth（lukonik） | v0.2.6 Alpha，**只验证 JWT，不做认证** | 🟡 仅 Resource Server 侧 |

→ 若用 Better Auth，必须接受**认证服务用 TypeScript（Node.js 容器）**，Gaia FastAPI 只做 JWT 验证。这引入 Node.js 运行时。

#### 模式 B：Keycloak（IDP 外置，Java）

**Keycloak**（Red Hat 支持，最成熟的开源 IAM）也能覆盖双场景：

| 场景 | Keycloak 能力 |
|------|--------------|
| **场景 1 本地用户** | Keycloak 内置用户库，Admin Console CRUD 用户/角色/分组。但 UI 粗糙，Gaia 应用层无控制权 |
| **场景 2 企业联邦** | User Federation（LDAP/AD 同步）+ Identity Provider 联邦（SAML/OIDC broker）+ 账号链接（同邮箱自动关联） |
| **共存** | 支持——本地用户与联邦用户在同一 realm，通过邮箱/用户名关联 |

**优势**：Java 栈成熟（Red Hat 支持）、全协议覆盖、企业实战最多
**短板**：
- 场景 1 的「自己管理用户」要 Keycloak Admin Console 或其 Admin REST API 做，**Gaia 应用层无原生用户管理能力**——与「自己管理用户」的诉求有距离（用户管理变成 Keycloak 的事，不是 Gaia 的事）
- 资源占用重（JVM）、配置复杂、UI 粗糙
- 引入 Java 运行时

#### 模式 C：Gaia 自建用户库 + 多认证源联邦（Python 原生）

这是「应用内嵌」的 Python 原生实现：Gaia 自己管用户/角色/分组（场景 1），同时对接外部 IdP 做联邦（场景 2）。

| 场景 | 实现 |
|------|------|
| **场景 1 本地用户** | Gaia PostgreSQL 存用户/角色/分组（设计文档 §1.2 的 UserModel/GroupModel），邮箱密码登录（bcrypt） |
| **场景 2 企业联邦** | Authlib 做 OAuth2/OIDC 客户端 + python3-saml 做 SAML SP + ldap3 做 LDAP bind，联邦登录后通过 account linking 关联本地用户 |
| **共存** | Gaia 自建 account linking 逻辑（邮箱匹配 + 手动绑定） |

**优势**：纯 Python、应用层完全控制用户生命周期、无额外运行时
**短板**：
- **多协议联邦要 Gaia 自己集成**（SAML/LDAP 各接各的，不是统一框架）——这正是前面 §5.1 说的「Python 无 Spring Security 等价物」的体现
- SAML 在 Python 里解析复杂（python3-saml 维护一般）
- account linking 逻辑要自写（但这是业务逻辑，不算造轮子）

### 5.3 三种模式对比与推荐

| 维度 | 模式 A：Better Auth（TS） | 模式 B：Keycloak（Java） | 模式 C：Gaia 自建（Python） |
|------|:---:|:---:|:---:|
| 场景 1 自管理用户 | ✅ Admin 插件，应用层控制 | 🟡 Keycloak 管用户，应用层无控制 | ✅ Gaia 完全控制 |
| 场景 2 企业联邦 | ✅ SSO 插件统一 OIDC/SAML + SCIM | ✅ User Federation + IdP Broker | 🟡 多协议各接各的 |
| 双场景共存 | ✅ account linking 原生 | ✅ realm 内关联 | 🟡 自建 linking |
| 架构定位 | Spring Security 等价（应用内嵌） | IDP 外置 | 应用内嵌 |
| 运行时 | **引入 Node.js** | 引入 Java（JVM） | 纯 Python |
| 成熟度 | TS 原版 27.5k stars（Vercel 收购）；Python 无 port | ★★★★★ 最成熟 | ★★★ 自建 |
| 协议完整度 | OIDC/SAML/OAuth2/SCIM/2FA/Passkey 全插件 | OIDC/SAML/LDAP/OAuth2/Kerberos | 取决于集成哪些库 |
| 与 Gaia 哲学契合 | ⭐⭐⭐⭐⭐ 现代框架设计 | ⭐⭐⭐ 重但稳 | ⭐⭐⭐⭐ 轻但手写多 |

#### 决策：采用模式 A（Better Auth）——已确认

**采用 Better Auth 的理由**：
1. **唯一原生满足双场景共存**的应用内嵌框架——本地用户（Admin 插件）与企业联邦（SSO 插件）通过 account linking 统一，正是需求
2. **Spring Security 的现代等价物**——plugin-based、composable、Vercel 收购背书，是 2026 年最先进的认证框架
3. **企业联邦最完整**——OIDC/SAML/SCIM 全覆盖，SCIM 让企业客户的 IdP 自动同步用户到 Gaia（场景 2 的最佳体验）
4. **应用层控制用户**——场景 1 的「自己管理用户」由 Admin 插件 API 提供，Gaia 前端直接调用，不像 Keycloak 要跳出去管理

**Node.js 运行时成本（已确认可接受）**：
- Gaia 已是多引擎容器架构（PostgreSQL/Gravitino/Doris/Trino/Neo4j/SeaTunnel/Kafka），再加一个 Node.js 认证服务同构
- Better Auth Server 是轻量 Node 服务（Hono，非 JVM 那种重），单 Bun 二进制可部署
- Gaia 前端已是 TypeScript（web-ui Vite+React），团队有 TS 能力
- 不用从头写：有多个开源生产级 starter 模板（见 §5.9）

**模式 C（纯 Python 自建）作为文档保留的备选**：若未来需要纯 Python 栈，用 Authlib（OIDC/OAuth2）+ python3-saml（SAML）+ ldap3（LDAP）在 Gaia 内自建联邦，account linking 自写。但一期不采用。

### 5.4 Better Auth 落地 Gaia 的架构（模式 A 详细方案）

```
场景 1：本地用户                      场景 2：企业联邦
  邮箱密码登录                          SAML/OIDC IdP（Okta/Azure AD）
  Admin 插件 CRUD                         ↓ SSO 插件联邦
  Organization 插件（分组）               ↓ SCIM 自动同步
        \                               /
         \                             /
    ┌─────────────────────────────────────────┐
    │  Better Auth Server（TypeScript/Node.js）│  ← 独立容器
    │  emailAndPassword + admin() + sso()      │     account linking 统一
    │  + organization() + scim() + jwt()       │     签发 OIDC JWT
    └──────────────────┬──────────────────────┘
                       │ OIDC JWT（标准）
                       ↓
    ┌─────────────────────────────────────────┐
    │  Gaia FastAPI 后端                        │  ← fastapi-betterauth 验证 JWT
    │  PrincipalService: claims → Gaia Principal │     + claims→User/attributes 映射
    │  AuthorizationService: Cedar 五层校验     │
    └─────────────────────────────────────────┘
```

**关键设计**：
- **Better Auth Server 独立部署**（Docker 容器，Node.js），管用户/会话/认证/联邦
- **Gaia FastAPI 只做 JWT 验证**（fastapi-betterauth 或 Authlib）+ claims→Principal 映射
- **用户/角色/分组数据在 Better Auth 的 DB**（可共享 Gaia 的 PostgreSQL，不同 schema）
- **Gaia 的权限模型**（Organization/Space/Project/Marking，设计文档 §1）在 Gaia 自己的 DB，通过 user_id 关联 Better Auth 用户

**Docker Compose 增加**：
```yaml
better-auth:
  build: ./auth-server  # Better Auth TS 服务
  environment:
    BETTER_AUTH_SECRET: ${BETTER_AUTH_SECRET}
    DATABASE_URL: postgresql://...  # 共享 Gaia PG
  ports: ["3000:3000"]
```

### 5.5 Gaia 应用层：JWT 验证 + PrincipalService

无论 Better Auth 还是 Keycloak 做 IDP，Gaia 应用层都只需 OIDC JWT 验证 + claims→Principal 映射。用 Authlib（async 原生、无 python-jose 技术债）自补 ~80 行 FastAPI 适配（见 §5.3 旧版方案 A 代码），或用 fastapi-betterauth（若选 Better Auth，它专门验证 Better Auth 签发的 JWT）。

```python
class PrincipalService:
    """薄映射层：OIDC claims → Gaia Principal。业务逻辑，无通用库可替代。"""
    async def resolve_principal(self, request: Request) -> Principal:
        token = self._extract_bearer(request)
        if not token:
            return self._anonymous_principal()
        claims = await self._verify_jwt(token)  # Authlib/fastapi-betterauth 验证
        user = await self._user_service.get_or_create_by_subject(claims["sub"])
        await self._sync_attributes(user, claims)  # claims → user.attributes（行级安全用）
        return await self._build_principal(user)  # 加载 Gaia 侧 groups/roles/markings
```

> **Gaia 侧权限与认证的边界**：认证（你是谁）由 Better Auth/Keycloak 管，授权（你能做什么）由 Gaia 的 Cedar 权限体系管。PrincipalService 是两者的桥梁——把认证结果（claims）转成 Gaia 的 Principal，供 AuthorizationService 五层校验。

### 5.6 开发模式 fallback

一期开发可不部署 Better Auth/Keycloak，用 `X-User-Id` 请求头 fallback：
```python
async def resolve_principal(self, request: Request) -> Principal:
    if self._settings.dev_mode:
        if user_id := request.headers.get("X-User-Id"):
            return await self._load_principal(user_id)
        return self._anonymous_principal()
    # 生产：验证 Better Auth/Keycloak 签发的 JWT
    ...
```

### 5.7 对比 Spring Security 的能力差距与补偿

| Spring Security 能力 | Gaia 方案（Better Auth） | 是否等价 |
|---------------------|--------------------------|:---:|
| 多 AuthenticationProvider（OAuth2/LDAP/SAML） | Better Auth SSO 插件（OIDC/SAML）+ SCIM | ✅ 等价 |
| 本地用户管理 | Admin 插件 + emailAndPassword | ✅ 等价 |
| SecurityContext + Principal | request.state.principal + PrincipalService | ✅ 等价 |
| FilterChain | Better Auth 中间件 + FastAPI Middleware | ✅ 等价 |
| Method-level Authorization | ToolExecutor + Cedar | ✅ 等价 |
| LDAP 直接集成 | 🟡 Better Auth 无原生 LDAP（靠 SSO 联邦 LDAP IdP） | 🟡 需 LDAP IdP 桥接 |
| 进程内统一 | Better Auth 独立服务 + Gaia 薄验证层 | 🟡 架构不同 |

**唯一差距**：Better Auth 无原生 LDAP 插件（不像 Keycloak 有 User Federation LDAP）。但企业 LDAP 通常通过 LDAP IdP（如 Keycloak/Authentik 做 LDAP 联邦）转 OIDC 接入 Better Auth SSO，或直接用支持 LDAP 的 SAML IdP。纯 LDAP 直连场景若必需，需模式 C 补 ldap3。

### 5.8 联邦配置机制：运行时 API 动态注册（场景 2 核心）

Better Auth 的企业联邦（SSO 插件）支持两种配置方式，满足不同部署场景：

#### 方式 1：运行时动态注册（API 驱动，多租户/多客户推荐）

通过 `registerSSOProvider` API 在运行时注册企业 IdP，配置存数据库，**不用改代码重启**：

```typescript
// 注册 OIDC 企业 IdP（如 Okta）——自动 discovery，只需 issuer + clientId/secret
await auth.api.registerSSOProvider({
    body: {
        providerId: "acme-corp-oidc",
        issuer: "https://acme.okta.com",
        domain: "acmecorp.com",
        organizationId: "org_acme_id",   // 关联到组织
        oidcConfig: { clientId: "...", clientSecret: "..." }
        // 其余字段自动从 IdP .well-known/openid-configuration 获取
    }
});

// 注册 SAML 企业 IdP
await auth.api.registerSSOProvider({
    body: {
        providerId: "acme-corp-saml",
        issuer: "https://acme.okta.com",
        domain: "acmecorp.com",
        samlConfig: {
            entryPoint: "https://idp.example.com/sso",
            cert: "-----BEGIN CERTIFICATE-----...",
        }
    }
});
```

**特点**：配置存数据库，运行时可增删；OIDC 自动 discovery；支持邮箱域名匹配自动路由（`user@acmecorp.com` → 自动用 acme 的 provider）；可关联 organization（企业用户自动加入对应组织）。Gaia 前端可做「企业 SSO 配置」管理页调此 API。

#### 方式 2：代码内静态配置（defaultSSO，单一固定 IdP）

```typescript
sso({
    defaultSSO: [{
        providerId: "default-saml",
        domain: "your-app.com",
        samlConfig: { issuer: "...", entryPoint: "...", cert: "..." }
    }]
})
```

适合 PoC 或单租户固定 IdP，改了要重启。

#### 联邦登录后的属性同步（对接 Gaia 行级安全）

```typescript
sso({
    provisionUser: async ({ user, userInfo, provider }) => {
        // userInfo.attributes 来自 SAML/OIDC IdP（department/region/manager...）
        await updateUserAttributes(user.id, {
            department: userInfo.attributes?.department,
            region: userInfo.attributes?.region,  // ← Gaia Cedar RowSecurityPolicy 引用
        });
    },
    provisionUserOnEveryLogin: true,  // 转岗时属性自动更新
})
```

Gaia 侧 PrincipalService 从 JWT claims 读这些属性，传给 Cedar 做行级过滤。

#### 内置企业级 SAML 安全防护（开箱即用，无需自实现）

- **InResponseTo 验证**：防 unsolicited response / replay / cross-provider injection
- **Assertion replay protection**：Assertion ID 去重，防重放（数据库存储，多实例安全）
- **Timestamp 验证**：NotBefore/NotOnOrAfter + clockSkew 容差
- **Signed AuthnRequests**：Okta/Azure AD/ADFS 要求的签名请求
- **Assertion 加密**：支持加密 assertion + 解密私钥

> 这些用模式 C（Python 自建）都得自己用 python3-saml 实现，Better Auth 开箱即用。

### 5.9 开源 starter 模板（不用从头写）

Better Auth + Hono 独立认证服务有多个开源生产级模板可直接 fork：

| 仓库 | 特点 |
|------|------|
| [savioruz/oil-auth](https://github.com/savioruz/oil-auth) | 完整生产级：Hono + Better Auth + PG + Redis + 2FA + 邮件 + 单 Bun 二进制部署 |
| [mrmovas/Express-BetterAuth-Boilerplate](https://github.com/mrmovas/Express-BetterAuth-Boilerplate) | Express + TS + Kysely + PG，clean architecture，v1.3.6 |
| [alwaysnomads/better-hono](https://github.com/alwaysnomads/better-hono) | 极简：Hono + Better Auth + Drizzle + Docker |
| [kadumedim/better-auth-starter](https://github.com/kadumedim/better-auth-starter) | Hono + Better Auth + Redis + OpenAPI，单 Bun 二进制 |

核心代码量极小（官方 Hono 集成文档）：

```typescript
// auth.ts — Gaia 定制配置（约 20 行）
import { betterAuth } from "better-auth";
import { sso } from "@better-auth/sso";
import { admin, organization, scim } from "better-auth/plugins";

export const auth = betterAuth({
  database: { dialect: "postgres", type: "postgres" },  // 共享 Gaia PG
  secret: process.env.BETTER_AUTH_SECRET,
  baseURL: process.env.BETTER_AUTH_URL,
  emailAndPassword: { enabled: true },                   // 场景 1
  plugins: [
    admin(),                                             // 用户管理
    organization(),                                      // 分组
    sso({                                                // 场景 2 企业联邦
      provisionUser: async ({ user, userInfo }) => { /* 同步属性 */ },
    }),
    scim(),                                              // 企业 IdP 自动同步
  ],
});

// server.ts — 启动（就这几行）
import { Hono } from "hono";
import { serve } from "@hono/node-server";
import { auth } from "auth";
const app = new Hono();
app.on(["POST", "GET"], "/api/auth/*", (c) => auth.handler(c.req.raw));
serve(app);
```

**Gaia 真正需定制写的**：① `auth.ts` 插件配置（~20 行）② `provisionUser` 属性映射（~10 行）③ Dockerfile + compose 加服务。登录/注册/SSO/SAML/SCIM/session/JWT 签发/密码哈希/邮箱验证/2FA 全由 Better Auth 内置。

**推荐路径**：fork [savioruz/oil-auth](https://github.com/savioruz/oil-auth)（最完整），改三处（插件配置 + provisionUser + 数据库指向 Gaia PG 独立 schema）。认证服务部分是「配置 + 薄定制」，不是「开发认证系统」。

### 5.10 Node.js 运行时成本确认（已接受）

经讨论确认，引入 Node.js 运行时的成本可接受：

- **能力门槛不存在**：Gaia web-ui 已是 TypeScript（Vite + React），团队有 TS 能力
- **运维同构**：Gaia 已是多引擎容器架构（PG/Gravitino/Doris/Trino/Neo4j/SeaTunnel/Kafka），再加一个 Node 认证服务同构
- **资源轻量**：Better Auth Server 是轻量 Node 服务（Hono，非 JVM 那种重），单 Bun 二进制可部署
- **职责清晰**：认证（你是谁）归 Better Auth，授权（你能做什么）归 Gaia Cedar，通过 JWT 解耦

> 注意：web-ui 的 Node.js 是构建时工具（产出静态文件，运行时无 Node 进程）；Better Auth Server 是常驻 Node 进程（运行时服务）。两者本质不同，但都证明团队具备 TS/Node 能力。

---

Spring Security 提供的是一个**框架内嵌的完整安全栈**：FilterChain + AuthenticationManager（可插拔 AuthenticationProvider：OAuth2/OIDC/LDAP/SAML/HTTP Basic）+ SecurityContext + Method-level Authorization。这一切在 Java 应用进程内完成，多协议在框架内统一。

**Python 生态没有等价物**。多个独立来源证实：

- [SSOJet 2026 博文](https://ssojet.com/blog/enterprise-sso-in-fastapi-how-to-add-saml-and-oidc-auth-to-python-apis-in-2026)：「Python's enterprise SSO ecosystem is **thinner** than Java or .NET。Rolling your own SAML parser in Python means owning `python3-saml`, managing per-tenant XML signature validation, and debugging encoding edge cases」
- [WorkOS 2026 Java 认证指南](https://workos.com/blog/java-authentication-guide-2026)：「Spring Security alone provides a **comprehensive filter chain**, password encoding, CSRF protection, session management, OAuth2 client and resource server support, and method-level authorization」——Python 没有任何库提供这种完整度

> 这正是选择 Better Auth（TypeScript）而非 Python 原生方案的根本原因——Python 生态确实没有 Spring Security 等价物，而 Better Auth 是。详见 §5.2-5.3。

---

## 六、对前期研究文档的勘误

[前期研究](./permission-data-pushdown-and-python-components.md) §2.2.2 的结论需更正：

| 原结论 | 勘误 | 依据 |
|--------|------|------|
| 「cedar_py 早期阶段」 | **cedarpy 4.8.6，生产级，活跃维护，SLSA 构建证明** | §2.1 误判 1 |
| 「Rust 绑定增加构建复杂度」 | **预编译 manylinux wheel，pip install 即可，无需 Rust 工具链** | §2.1 误判 2 |
| 「不推荐 Cedar」 | **推荐 Cedar 作为一期策略引擎** | §二 全文 |
| 「一期 simpleeval 够用」 | **simpleeval 有五项根本缺陷，不应用于安全策略** | §一 全文 |
| 「二期评估 Cerbos」 | **Cedar 已能覆盖 Cerbos 的核心能力（PlanResources ≈ TPE），且进程内嵌更轻** | §2.2 |
| 「OIDC 选 Authlib + 自建 PrincipalService」 | **Better Auth（TS，双场景原生共存）独立服务 + Authlib 应用层 JWT 验证；Node.js 成本已确认接受** | §五/§5.3 |

前期研究未评估的 Cedar 关键能力：
- **Partial Evaluation / TPE**（行级下推的正确路径）——§3.4
- **类型系统 + schema 验证**（部署前暴露错误）——§1.2 缺陷 2
- **PolicySet/Entities 句柄复用**（性能优化）——§3.2
- **cedarpy 的供应链安全**（Trusted Publishing + SLSA）——§2.1 误判 1

前期研究正确的部分（保持不变）：
- §2.3 SCIM 选 scim2-models ✓
- 第一部分 各引擎下推机制 ✓（但表达式引擎从 simpleeval 改为 Cedar TPE）

---

## 七、四个待定技术点的深度验证

> 针对设计文档中停留在「方案描述」层的 4 个技术点，做开源生态调研，确认是否有成熟方案可复用。

### 7.1 Doris 下推：放弃原生 Row Policy，统一 SqlGlot AST 注入

#### 问题

Cedar TPE 产生行级过滤残差后，如何下推到 Doris Row Policy？设计文档假设了 `current_user_region()` 函数，但未验证 Doris 是否支持类似 PG `current_setting` 的运行时用户上下文。

#### 验证结论：Doris Row Policy 机制与 PG RLS 有本质差异

**Doris Row Policy 是静态绑定 user/role 的，不支持运行时动态用户上下文**（[官方文档](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-governance/CREATE-ROW-POLICY/)）：

```sql
-- Doris Row Policy：绑定到具体 user/role，USING 是静态条件
CREATE ROW POLICY policy_region ON idx_ont__type
AS RESTRICTIVE TO ROLE gaia_group_xxx
USING (region = 'east');  -- 值写死，不能引用 current_setting
```

对比 PG RLS 可用 `current_setting('app.principal_region')` 动态取值，Doris 的 USING **不能引用 session 变量或 UDF 运行时求值**——它只是一个静态谓词，自动追加到查询 WHERE。

**Doris 的用户变量 `SET @var`**（[文档](https://doris.apache.org/docs/4.x/sql-manual/basic-element/variables/)）虽然存在，但**不能在 Row Policy USING 中引用**（USING 只接受列字面量条件，不解析 @var）。这是 Doris 与 PG 的关键差异。

#### 架构决策：放弃 Doris 原生 Row Policy，统一走 SqlGlot AST 注入

深入分析后发现，Doris Row Policy 不仅「静态」这一个约束，还有两个更深的架构问题使它不适合 Gaia。最终决策是**放弃 Doris 原生 Row Policy，与 Trino 降级统一走 SqlGlot AST 注入**。

##### 问题 2：Gaia 当前 Doris 连接模型是单用户连接池，与 Row Policy 不兼容

Gaia 现状（`doris_index_store.py`）：所有请求共用一个 Doris 用户连接池（`settings.doris_user`，通常是 root/admin）。而 Doris Row Policy 的官方约束是：

> **root/admin 用户不受 Row Policy 约束**

这意味着即使创建了 Row Policy，只要 Gaia 用 root/admin 连接，策略**完全不生效**。要让 Row Policy 生效，必须改成「每请求用对应用户身份的 Doris 账号连接」，引出三种连接模型：

| 模型 | 机制 | 致命问题 |
|------|------|--------|
| **A：每用户独立 Doris 账号** | user_id → Doris 账号 → 连接池 | 连接池爆炸（N 用户×5 连接）；Doris 账号生命周期管理；违反组授权铁律（per-user 非 per-group） |
| **B：每 Group 一个 Doris Role** | 选 Role 对应账号连接 | 一用户多 Group 时一个连接只能一套 Role；要选「最严」或合成 Role，语义复杂 |
| **C：维持单用户连接池 + 应用层注入** | 不依赖 Doris 身份，谓词注入 SQL | ✅ 连接模型不变；✅ 动态性自由；✅ 无 DDL 同步 |

##### 问题 3：SqlGlot AST 注入与 Doris Row Policy 安全等价（非「后过滤」）

设计文档 §4.0 批判「应用层后过滤是虚假安全」。需厘清 SqlGlot AST 注入不是后过滤：

| | 应用层**后**过滤（虚假安全） | 应用层**前**注入（SqlGlot AST） |
|---|---|---|
| 时机 | 查完数据后 Python 过滤 | 查询前把条件注入 SQL WHERE |
| 引擎是否执行过滤 | ❌ 引擎返回全量，应用丢弃 | ✅ 引擎在 scan 时就过滤 |
| 能否绕过 | ✅ 抓包改参数/直接调 API 可绕过 | ❌ SQL 由服务端构造，客户端无法篡改 |
| 数据是否到应用层 | ✅ 无权数据先到应用再丢弃 | ❌ 无权数据不离开 Doris |

SqlGlot 注入是「应用层构造谓词，引擎执行过滤」——谓词在 SQL 发给 Doris 前注入，Doris 在 scan 节点执行 WHERE 时过滤无权行，无权数据不返回应用层。与「下推到存储层」要求一致，非后过滤。唯一区别是策略持有方（Doris 自持 vs Gaia 构造），但安全本质（引擎层过滤）等价。

##### 最终方案：SqlGlot AST 注入统一下推

```python
class DorisIndexStore:
    async def query(self, query: IndexQuery, scope: QueryScope) -> IndexResult:
        # 基础查询 SQL（现有逻辑不变）
        base_sql = f"SELECT {pk} FROM {table} WHERE {business_filters}"
        # Cedar TPE 残差 → SQL 谓词（已求值 principal 部分，只剩 resource 属性条件）
        permission_predicates = self._residual_to_sql(scope.residual)
        # SqlGlot AST 注入（递归处理子查询/CTE/UNION，AskTable 成熟方案）
        final_sql = self._sql_injector.inject(base_sql, permission_predicates, dialect="doris")
        await cursor.execute(final_sql)  # 仍用单一连接池，无 Doris 身份管理
```

**决策理由**：
1. **Doris Row Policy 动态性不足**——静态谓词不能引用运行时用户上下文
2. **单用户连接池模型不兼容**——Row Policy 对 root/admin 不生效，改 per-user/per-group 连接池代价过大
3. **SqlGlot 注入安全等价**——引擎层过滤，数据不离开 Doris，非后过滤
4. **架构统一**——Doris/Trino/任何 SQL 引擎同一套下推机制（§7.2），方言切换即可
5. **避免 Doris DDL 同步开销**——不用 CREATE ROLE/ROW POLICY/GRANT，不用 outbox，不用处理 Group 生命周期与 Doris 一致性（嵌套 Group 展开、改名/删除级联）

**Doris 原生 Row Policy 保留为二期可选纵深防御层**：若未来需要「即使应用层被绕过（如直连 Doris），策略仍生效」，再评估 per-group Doris Role + Row Policy。但一期不引入——Gaia 的 Doris 不对外暴露直连（只通过 ObjectQueryService 访问），应用层注入已足够。

> **对设计文档 §4.2 的修正**：删除「Gaia Group → Doris Role 映射」方案（CREATE ROW POLICY ... TO ROLE gaia_group_xxx），改为 SqlGlot AST 注入。设计文档 §4.2 的「列脱敏用原生 MASK 函数 + VIEW」方案保留（那是存储层脱敏，与行级下推是两个独立机制，不受此决策影响）。

### 7.2 Trino 下推：SqlGlot AST 注入（有成熟开源实现）

#### 问题

设计文档说「一期 SQL 注入 filter，二期 OPA 插件」，但未定具体实现。手拼 WHERE 有注入风险 + 无法处理子查询/CTE/UNION。

#### 验证结论：SqlGlot AST 重写是成熟方案，有生产级参考实现

**Gaia 已依赖 sqlglot>=30.0**（pyproject.toml），且 sqlglot 原生提供：
- `sqlglot.parse_one(sql, read=dialect)` 解析成 AST
- `sqlglot.optimizer.build_scope(ast)` 构建 Scope 树（识别子查询/CTE/UNION/JOIN 的层级）
- `exp.Where` / `exp.And` AST 节点操作注入条件
- 多方言输出（Trino/Doris/PG 等 31 种方言）

**生产级参考：AskTable SQL Permission Guard**（[博文](https://www.asktable.com/en-US/blog/2026-03-05/asktable-sql-permission-guard-sqlglot)）：
- 递归处理 Scope 树，子查询/CTE/UNION/JOIN 每层独立注入权限条件
- Jinja2 动态变量（`{{user_id}}`）+ 通配符规则（`*.*.status = 0`）
- 性能 <10ms（复杂 SQL）
- 计划开源

**Apache Superset 也有 sqlglot RLS 实现**（[PR #33524](https://github.com/apache/superset/pull/33524)，已合并 6.0.0）。

#### Gaia 方案：Cedar 残差→SqlGlot AST 注入

```python
# Gaia 行级下推统一走 SqlGlot AST 注入（适用 Trino 降级 + Doris 降级 + 任何 SQL 引擎）
class SqlPermissionInjector:
    def inject(self, sql: str, residual: CedarResidual, dialect: str) -> str:
        ast = sqlglot.parse_one(sql, read=dialect).copy()
        scope = build_scope(ast)
        # Cedar 残差 → SQL 谓词（残差已求值掉 principal，只剩 resource 属性条件）
        predicates = self._residual_to_predicates(residual)  # region = 'east' AND dept = 'sales'
        self._inject_into_scope(scope, predicates)  # 递归注入所有子 Scope
        return ast.sql(dialect=dialect)
```

**关键决策**：
- **一期用 SqlGlot AST 注入**（不用手拼 WHERE，不用 OPA sidecar）。Cedar TPE 残差翻译为 SQL 谓词后，用 sqlglot 递归注入所有 Scope——这是 AskTable 验证过的成熟模式
- **残差→SQL 谓词翻译器**复用 §3.4 的 Cedar AST→SQL 映射表（`==`→`=`、`in`→`IN`、`&&`→`AND`），确定性查表，符合红线 8
- **二期 OPA 插件仅作为 Trino 原生计划改写的可选增强**，非必需。SqlGlot 注入已能下推到 connector（谓词下推是 Trino 优化器原生能力）

> **比设计文档更优**：设计文档把 SqlGlot 注入当「次选」，但实测它比 OPA sidecar 更轻（无额外服务）+ 比 OPA 计划改写更可控（应用层决定注入什么）。**一期即采用 SqlGlot AST 注入作为统一的 SQL 下推机制**（Trino 降级 + Doris 降级 + 任何 SQL 引擎通用）。

### 7.3 工具层权限声明 + Principal 注入（pydantic-ai 原生支持）

#### 问题

设计文档说「静态配置每个工具声明所需权限」，未定声明格式；工具参数动态决定 resource_id 的情况未处理；AG-UI Agent 的 Principal 透传机制未定。

#### 验证结论：pydantic-ai RunContext 原生支持依赖注入

Gaia 的 AG-UI Agent 基于 pydantic-ai（`pydantic-ai-slim[ag-ui]==2.0.0`），pydantic-ai 的 **`RunContext[Deps]` 类型化依赖注入**天然解决 Principal 透传（[文档](https://pydantic.dev/docs/ai/core-concepts/dependencies/)）：

```python
from pydantic_ai import Agent, RunContext

class GaiaDeps(BaseModel):
    principal: Principal          # 认证后的 Principal
    authz: AuthorizationService   # 权限服务
    # ... 其他依赖

agent = Agent(model, deps_type=GaiaDeps, ...)

@agent.tool  # 需要访问 context 的工具用 @agent.tool（自动注入 RunContext）
async def query_with_dataframe(ctx: RunContext[GaiaDeps], object_type_id: str, ...) -> ...:
    # 工具权限校验：动态 resource_id（从参数来，不是静态）
    result = await ctx.deps.authz.check_access(
        ctx.deps.principal, "OBJECT_TYPE", object_type_id, "VIEW"
    )
    if not result.allowed:
        return ToolResult(error="FORBIDDEN", reason=result.reason)
    return await _do_query(object_type_id, ...)

@agent.tool_plain  # 不需要 context 的纯函数工具用 @agent.tool_plain
async def list_ontologies() -> ...: ...
```

#### Gaia 方案：声明 + 运行时校验结合

**工具权限声明**用**代码内类型化声明**（不用 YAML/JSON 配置文件，避免漂移）：

```python
@dataclass
class ToolPermission:
    resource_type: str          # ONTOLOGY / OBJECT_TYPE / DATASET ...
    action: str                 # VIEW / EDIT / EXECUTE
    resource_id_param: str | None  # 运行时参数名（如 "object_type_id"），None=静态

# 工具注册时声明权限（装饰器或注册表）
TOOL_PERMISSIONS: dict[str, ToolPermission] = {
    "define_object_type": ToolPermission("ONTOLOGY", "EDIT", None),  # 静态
    "query_with_dataframe": ToolPermission("OBJECT_TYPE", "VIEW", "object_type_id"),  # 动态
}

class ToolExecutor:
    async def execute_gated(self, tool_name, params, principal) -> ToolResult:
        perm = TOOL_PERMISSIONS[tool_name]
        resource_id = params[perm.resource_id_param] if perm.resource_id_param else "*"
        result = await self._authz.check_access(principal, perm.resource_type, resource_id, perm.action)
        if not result.allowed:
            return ToolResult(error="FORBIDDEN", reason=result.reason)
        return await self._execute(tool_name, params)
```

**关键决策**：
- **声明格式**：代码内 `ToolPermission` dataclass + 注册表（类型安全 + IDE 可追溯，不用 YAML）
- **动态 resource_id**：声明里记 `resource_id_param`（参数名），运行时从工具参数取值校验
- **AG-UI Principal 透传**：pydantic-ai `RunContext[GaiaDeps]`，Principal 作为 Dep 注入，`@agent.tool` 自动拿到 context。Agent 以人类用户 Principal 身份执行（继承权限）
- **MCP 工具**：用 Service User Principal（scoped 限制），同样走 ToolExecutor 校验

### 7.4 Cedar 策略 LLM 辅助生成（生态完整，非空白）

#### 问题

设计文档 §8.3 说「二期，LLM 转成结构化 expression」，但表达式引擎已从 simpleeval 换成 Cedar，需重新设计自然语言→Cedar 策略的生成 + 校验 + 编辑器。

#### 验证结论：Cedar 的 AI/工具生态已成熟

**学术 + 开源**：
- **AutoCedar**（[arxiv 2607.03656](https://arxiv.org/html/2607.03656)）：verifier-guided 自然语言→Cedar 策略合成框架，人类在环（HITL），用 CVC5 SMT 验证策略满足意图。开源 [neselab/cedar-synthesis-engine](https://github.com/neselab/cedar-synthesis-engine)
- **Autoformalization**（[arxiv 2606.26649](https://arxiv.org/html/2606.26649)）：把 agent 提示词/MCP 工具描述/自然语言策略文档自动形式化为 Cedar 策略

**生产级**：
- **AWS Bedrock AgentCore 用 Cedar 保护 agentic workflows**（[AWS 博文](https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/)）：LLM 把自然语言转 Cedar 策略，两阶段验证（Cedar 引擎 + 形式化验证）

**开发工具**：
- **VS Code Cedar 扩展**（[cedar-policy/vscode-cedar](https://github.com/cedar-policy/vscode-cedar)，官方）：语法高亮 + 格式化 + 校验（基于 schema）+ 实体类型 IntelliSense。前端策略编辑器可参考其语言服务协议
- **cedarpy `validate_policies`**：策略 + schema 验证，部署前拦截错误（§3.1）
- **AWS CI/CD Cedar 策略验证**（[AWS 博文](https://aws.amazon.com/blogs/security/automate-cedar-policy-validation-with-aws-developer-tools/)）：构建流水线自动验证 Cedar 策略

#### Gaia 方案（二期）

- **生成**：复用 Gaia 已有 `/ai/generate`（pydantic-ai structured output），prompt 里给 Cedar 语法 + schema + 用户自然语言，生成 Cedar 策略草稿
- **校验**：cedarpy `validate_policies(policy, schema)` 部署前验证类型/语法；dry-run 测试用例（输入 principal/resource/action，断言决策）
- **编辑器**：前端用 CodeMirror/Monaco + Cedar 语法规则（参考 vscode-cedar 的 TextMate 语法）；后端 dry-run 返回校验结果。二期可评估集成 AutoCedar 的 verifier-guided 合成

### 7.5 四点验证总结

| 技术点 | 原状态 | 验证后结论 | 是否有成熟方案 |
|--------|--------|-----------|:---:|
| Doris Group→Role 同步 | 假设 current_user_region() | **放弃 Doris 原生 Row Policy**，统一走 SqlGlot AST 注入（与 Trino 同机制） | ✅ sqlglot 原生（见 §7.2） |
| Trino/SQL 下推 | 未定，备选 OPA | **SqlGlot AST 注入**（Gaia 已依赖），AskTable 生产级参考，<10ms | ✅ sqlglot 原生 + AskTable 参考 |
| 工具层权限声明 | 未定格式 | pydantic-ai RunContext 原生 DI + 代码内 ToolPermission 注册表 | ✅ pydantic-ai 原生 |
| Cedar LLM 辅助 | 二期待设计 | AutoCedar（学术+开源）+ AWS Bedrock 生产实践 + vscode-cedar 工具链 | ✅ 生态完整 |

**关键修正**：
1. Doris 下推放弃原生 Row Policy（静态谓词不支持运行时上下文 + root/admin 不受约束 + 单用户连接池不兼容），统一走 SqlGlot AST 注入
2. Trino/SQL 下推从「备选 SqlGlot」升为「一期统一方案」（比 OPA sidecar 更轻更可控）
3. 工具层用 pydantic-ai 原生 DI（不用额外框架）
4. Cedar LLM 辅助有完整生态（AutoCedar + AWS + vscode-cedar），二期实现路径清晰

---

## 八、自建代码的参考实现（不从头写）

> 针对 Gaia 自己要写的代码（非外部依赖），调研开源参考实现与最佳实践，避免从零造轮子。

### 8.1 Cedar 集成层（AuthorizationService + schema/entities 映射）

Gaia 要写的 Cedar 集成代码：schema 生成、entities 构建、PolicySet 生命周期、五层校验求值。以下参考实现可直接借鉴：

| 参考实现 | 覆盖的 Gaia 代码 | 价值 |
|---------|----------------|------|
| [k9securityio/cedarpy-example-hello-photos](https://github.com/k9securityio/cedarpy-example-hello-photos) | cedarpy 基本用法（is_authorized + entities JSON + schema） | Lambda Authorizer 完整示例，Python 调 cedarpy 的模式 |
| [sondera-ai/sondera-harness-python](https://github.com/sondera-ai/sondera-harness-python/blob/main/examples/cedar/coding_agent.py) | schema 生成 + CedarPolicyHarness + agent_to_cedar_schema | Python 应用层如何把业务模型转成 Cedar schema/entities |
| [Cedar 官方授权模式指南](https://docs.cedarpolicy.com/bestpractices/bp-authorization-patterns.html) | entity slice 构建（principal/resource/context 各取什么） | 每个 API endpoint 对应的 Cedar 请求构造模式 |
| [atlas9: Building an access framework using Cedar](https://atlas9.dev/blog/access-with-cedar.html) | 用户/组嵌套、对象级权限、引用资源校验、list 查询、partial eval 转 SQL、policy templates | **最完整的实战参考**——覆盖 Cedar 落地所有难点 |

**atlas9 博文的关键经验（Gaia 直接借鉴）**：
- **组嵌套**：用 Cedar 的 `in` 运算符 + entities 的 `parents` 字段，无需改策略即可支持嵌套组（Gaia 的 parent_group_id 直接映射）
- **引用资源校验**：atlas9 指出 Cedar 难以表达「viewReport 须同时 viewDataset」的跨资源依赖，需应用层循环校验。**Gaia 的 Action 跨 ObjectType Reference 二次校验**正是此场景，参考其应用层模式
- **list 查询效率**：暴力法（查全量→逐个 Cedar 校验）性能差，用 partial evaluation 残差转 SQL 才高效——这正是 Gaia SqlGlot 注入的路径
- **policy templates**：用数据库存权限授予 + 模板填充，对应 Gaia 的 RoleAssignment → Cedar policy 动态生成

### 8.2 PG RLS 策略生成 + SET LOCAL 上下文注入

Gaia 要写的 PG RLS 代码：CREATE POLICY DDL 生成、AuthMiddleware 的 SET LOCAL 上下文注入、SQLAlchemy 事件监听器。有多个成熟参考：

| 参考实现 | 覆盖的 Gaia 代码 | 关键模式 |
|---------|----------------|---------|
| [FastAPI + PostgreSQL RLS 多租户](https://fernandocode.pages.dev/en/blog/fastapi-rls-multitenant-en/) | AuthMiddleware SET LOCAL + RLS policy | `before_cursor_execute` 事件监听器注入 `SET LOCAL app.current_tenant`，RLS 自动过滤 |
| [ulfblk-multitenant](https://pypi.org/project/ulfblk-multitenant/) | TenantMiddleware + TenantContext(contextvar) + SQLAlchemy 事件 | FastAPI + SQLAlchemy async 的完整 RLS 集成库，contextvar 传 tenant_id |
| [CommonTrace: RLS with SQLAlchemy](https://www.commontrace.org/trace/postgresql-row-level-security-rls-with-sqlalchemy/) | RLS policy 生成 + FORCE RLS + current_setting | ENABLE/FORCE RLS + `USING(current_setting(...))` 完整模式 |
| [pgrls](https://pypi.org/project/pgrls/0.38.0/) | RLS 策略生成 + lint | `pgrls generate` 自动生成 RLS，`pgrls fix` 修复已有 RLS，可借鉴生成器设计 |
| [django-rls-tenants](https://dvoraj75.github.io/django-rls-tenants/advanced/architecture/) | GUC 变量管理 + RLSConstraint 生成 CREATE POLICY | `rls/guc.py`(set/get/clear GUC) + `rls/constraints.py`(生成 CREATE POLICY SQL) 模块化设计 |

**Gaia 借鉴模式**：
- **SET LOCAL 上下文注入**：用 SQLAlchemy 的 `before_cursor_execute` 事件监听器（非中间件），每事务开头 `SET LOCAL app.principal_organization = ...`。ulfblk-multitenant 是 FastAPI async 的现成参考
- **RLS policy 生成**：参考 django-rls-tenants 的 `RLSConstraint` 类——把 RLS policy 封装成可声明式定义的对象，自动生成 CREATE POLICY SQL
- **contextvar vs request.state**：[FastAPI 实践](https://dev.to/akarshan/fastapi-requeststate-vs-context-variables-when-to-use-what-2c07)推荐 request.state 传 Principal（显式可测），contextvar 用于中间件/事件监听器等无法拿 request 的场景。Gaia 两者结合：request.state.principal 给路由，contextvar 给 SQLAlchemy 事件监听器

### 8.3 SqlGlot AST 注入器（行级下推）

Gaia 要写的 SqlGlot 注入代码：Cedar 残差→SQL 谓词翻译 + SqlGlot AST 递归注入 WHERE。有生产级参考：

| 参考实现 | 覆盖的 Gaia 代码 | 关键模式 |
|---------|----------------|---------|
| [AskTable SQL Permission Guard](https://www.asktable.com/en-US/blog/2026-03-05/asktable-sql-permission-guard-sqlglot) | **完整实现**：Scope 递归 + 条件注入 + 别名处理 + 去重 | 生产级，<10ms，子查询/CTE/UNION/JOIN 全覆盖。**Gaia 可直接借鉴架构** |
| [Query-farm/python-sql-manipulation](https://github.com/Query-farm/python-sql-manipulation) | SqlGlot 谓词操作（添加/移除 WHERE 条件） | sqlglot AST 操作的工具库 |
| [Apache Superset RLS PR #33524](https://github.com/apache/superset/pull/33524) | Superset 用 sqlglot 实现 RLS | 826 行实现，企业级 BI 的 RLS 集成参考 |
| [sqlglot AST primer](https://github.com/tobymao/sqlglot/blob/main/posts/ast_primer.md) | AST 节点操作（exp.Where/exp.And/condition()） | 官方 AST 操作教程 |

**AskTable 的关键模式（Gaia 直接借鉴）**：
- `build_scope(ast)` 构建 Scope 树 → 递归 `_process_scope` 遍历所有子 Scope（derived_table/cte/subquery/union）→ 每个 Scope 独立注入条件
- 别名处理：`source.alias != source.name` 区分真别名与表名，权限条件用别名引用列
- 条件去重：基于 SQL 字符串去重，避免同表多次 JOIN 重复注入
- Jinja2 动态变量：`{{user_id}}` 渲染（Gaia 用 Cedar 残差求值替代，但模式可借鉴）

### 8.4 FastAPI 认证中间件 + Principal 注入

Gaia 要写的 AuthMiddleware 代码：JWT 验证 → Principal 注入 → PG RLS 上下文。有最佳实践参考：

| 参考实现 | 覆盖的 Gaia 代码 | 关键模式 |
|---------|----------------|---------|
| [FastAPI Authentication Best Practices 2026](https://safeguard.sh/resources/blog/fastapi-authentication-best-practices-2026) | 分层依赖链（token 验证→user 加载→权限校验） | 反对「god-dependency」，推荐三层分层各司其职 |
| [FastAPI Get Current User 官方教程](https://fastapi.tiangolo.com/tutorial/security/get-current-user/) | oauth2_scheme + get_current_user 依赖 | 官方推荐的 token→user 依赖模式 |
| [FastAPI middleware ordering](https://uguraslim.com/blog/fastapi-middleware-ordering-why-your-cors-auth-and-tenant-co/) | 中间件执行顺序（CORS→Auth→Tenant） | 中间件顺序对 RLS 上下文的影响 |
| [fastapi-principal](https://pypi.org/project/fastapi-principal/) | Principal 注入 + contextvar + Depends(permission.require) | flask-principal 的 FastAPI 适配，可借鉴 Principal 抽象 |

**Gaia 借鉴模式**：
- **分层依赖链**：`verify_jwt`（Authlib 验证）→ `get_current_user`（claims→User）→ `require_permission(action)`（Cedar 校验），每层单一职责
- **request.state.principal**：中间件注入，路由层 `Depends` 取。不用 contextvar 传 Principal（[显式可测优于全局状态](https://dev.to/uaslimcreate/fastapi-dependency-injection-for-multi-tenant-request-context-avoiding-the-global-state-trap-484a)）
- **中间件顺序**：CORS → AuthMiddleware（JWT 验证 + Principal 注入）→ PG RLS 上下文（before_cursor_execute 事件）

### 8.5 审计日志（追加写入 + 防篡改）

Gaia 要写的 AuditLog 代码：追加写入、不可篡改、可能加哈希链。有成熟参考：

| 参考实现 | 覆盖的 Gaia 代码 | 关键模式 |
|---------|----------------|---------|
| [An immutable audit trail for AI agent actions (FastAPI + async SQLAlchemy)](https://dev.to/codemalasartes/an-immutable-audit-trail-for-ai-agent-actions-fastapi-async-sqlalchemy-4m4c) | **完整实现**：append-only AuditLog + FastAPI 中间件 + async SQLAlchemy | 与 Gaia 技术栈完全一致（FastAPI + async SQLAlchemy），直接借鉴 |
| [postgresql-audit](https://postgresql-audit.readthedocs.io/en/stable/sqlalchemy.html) | SQLAlchemy 集成 + after_create DDL 触发器自动建版本表 | DB 触发器级审计，比应用层更难绕过 |
| [Thalian Audit Log Chain](https://docs.thalian.ai/audit-log-chain-of-custody/) | SHA-256 哈希链防篡改 | `content_hash` + `previous_hash` Merkle 链，SOC 2 合规 |
| [JSON Audit Trail guide](https://jsonic.io/guides/json-audit-trail) | 审计日志 schema + 不可篡变设计 + GDPR | 4 必需字段（actorId/action/resourceId/timestamp）+ 哈希链 + 保留期 |

**Gaia 借鉴模式**：
- **append-only 强制**：审计模块只暴露 `append()` 方法，不暴露 UPDATE/DELETE（[Rule zero: the audit log is append-only](https://dev.to/codemalasartes/an-immutable-audit-trail-for-ai-agent-actions-fastapi-async-sqlalchemy-4m4c)）
- **DB 角色权限**：audit_logs 表只授予 INSERT + SELECT，不授 UPDATE/DELETE（应用层 + DB 层双重保障）
- **哈希链（二期）**：`content_hash = SHA256(canonical_json(event) + previous_hash)`，防篡改，SOC 2 审计需要
- **字段设计**：actorId/action/resourceId/timestamp（ISO 8601 UTC）/result(ALLOW|DENY)/layer/reason，对齐设计文档 §1.6

### 8.6 权限缓存失效（cashews tag 失效）

Gaia 要写的缓存代码：三级缓存 key 设计 + tag 失效 + 高敏操作绕过。cashews 已提供原语，参考其文档：

| 参考实现 | 覆盖的 Gaia 代码 | 关键模式 |
|---------|----------------|---------|
| [cashews 文档](https://github.com/Krukov/cashews) | tag 失效 + set_lock + client_side caching | 原语齐全，Gaia 直接用 |
| [cashews 作者博文：Redis client-side cache with async Python](https://medium.com/the-pandadoc-tech-blog/redis-client-side-cache-with-async-python-6228a0121a12) | 多实例缓存一致性 | client_side=True 自动跨实例同步 |

**Gaia 借鉴模式**：已在 §4.3-4.4 详述（key 前缀 + tag 失效 + 三级缓存），不赘述。

### 8.7 参考实现总结

| Gaia 自建模块 | 参考实现 | 借鉴程度 |
|-------------|---------|:---:|
| Cedar 集成层 | cedarpy-example + sondera-harness + atlas9 博文 | 架构借鉴 |
| PG RLS 生成 + SET LOCAL | ulfblk-multitenant + CommonTrace + django-rls-tenants | 模式直接借鉴 |
| SqlGlot AST 注入器 | **AskTable SQL Permission Guard** | **架构直接借鉴** |
| FastAPI 认证中间件 | FastAPI 官方 + safeguard 最佳实践 | 模式借鉴 |
| 审计日志 | **immutable audit trail (FastAPI+async SA)** | **技术栈一致，直接借鉴** |
| 权限缓存失效 | cashews 文档 + 作者博文 | 原语直接用 |

**结论**：Gaia 自建代码均有开源参考实现，不需要从零造轮子。最值得直接借鉴的是 **AskTable SQL Permission Guard**（SqlGlot 注入器架构）和 **immutable audit trail**（审计日志，技术栈完全一致）。Cedar 集成层参考 atlas9 博文的实战经验（组嵌套/引用资源校验/list 查询/partial eval）。

---

## 附录 A：Cedar vs OPA vs Casbin vs Cerbos 全维度对照

| 维度 | Cedar (cedarpy) | OPA (Rego) | Casbin (pycasbin) | Cerbos |
|------|:---:|:---:|:---:|:---:|
| **语言安全** | ★★★★★ 非图灵完备，类型安全 | ★★★ 图灵完备，有 ReDoS 风险 | ★★★ 模型文件，无类型 | ★★★★ YAML+CEL |
| **Python 集成** | ★★★★★ 进程内嵌，原生 API | ★★ 仅 sidecar/HTTP | ★★★★ 嵌入式 | ★★★ 需独立服务+SDK |
| **部署复杂度** | ★★★★★ 零额外服务 | ★★ 需 OPA sidecar | ★★★★★ 嵌入式 | ★★ 需 Cerbos 服务 |
| **行级下推** | ★★★★★ TPE 残差→SQL | ★★★★ Compile API→SQL | ✗ 不支持 | ★★★★ PlanResources |
| **大规模性能** | ★★★★★ µs 级，句柄复用 | ★★★ ms 级 | ★★ 10k规则500ms | ★★★★ 独立服务开销 |
| **缓存** | ★★★★★ 无状态+句柄复用 | ★★★ 需自管 | ★★ 有已知 bug | ★★★★ 内置 |
| **分布式** | ★★★★★ 策略即数据，无状态 | ★★★ 需策略同步 | ★★ 需 Redis Watcher | ★★★★ 独立服务共享 |
| **策略可读性** | ★★★★ Cedar 语法 | ★★★ Rego（陡峭） | ★★ 双文件 | ★★★★★ YAML |
| **类型验证** | ★★★★★ schema 验证 | ★★★ 弱类型 | ✗ 无 | ★★★ CEL 类型 |
| **安全审计** | ★★★★★ ToB+SPEF 背书 | ★★★ ToB 评估 | ✗ 无 | ★★★★ |
| **维护方** | AWS + k9securityio | Styra/CNCF | Apache (Incubating) | Cerbos Labs |
| **Gaia 契合度** | ★★★★★ | ★★ | ★★ | ★★★ |

**综合推荐排序**（Gaia 场景）：**Cedar > Cerbos > OPA > Casbin**

---

## 附录 B：证据索引

### Cedar / cedarpy
- Cedar 官方：https://github.com/cedar-policy/cedar
- cedarpy（Python 绑定）：https://github.com/k9securityio/cedar-py
- cedarpy PyPI：https://pypi.org/project/cedarpy/
- Cedar 语法：https://docs.cedarpolicy.com/policies/syntax-policy.html
- Cedar schema：https://docs.cedarpolicy.com/schema/json-schema.html
- Cedar 授权决策：https://docs.cedarpolicy.com/auth/authorization.html
- Cedar TPE RFC：https://github.com/cedar-policy/rfcs/blob/main/text/0095-type-aware-partial-evaluation.md
- Cedar entity slicing RFC：https://github.com/cedar-policy/rfcs/blob/main/text/0076-entity-slice-validation.md
- Cedar OOPSLA 论文：https://arxiv.org/pdf/2403.04651
- cedarpy BENCHMARKS.md：https://github.com/k9securityio/cedar-py/blob/main/BENCHMARKS.md
- cedarpy partial auth guide：https://github.com/k9securityio/cedar-py/blob/main/docs/guides/partial-authorization-guide.md
- cedarpy CLAUDE.md（维护质量）：https://github.com/k9securityio/cedar-py/blob/main/CLAUDE.md

### 安全评估
- Trail of Bits 策略语言安全对比：https://github.com/trailofbits/publications/blob/master/reports/Policy_Language_Security_Comparison_and_TM.pdf
- Teleport SPEF 动态基准：https://goteleport.com/blog/benchmarking-policy-languages/
- SPEF 框架仓库：https://github.com/gravitational/policy-languages-framework

### 对比方案
- OPA data filtering（partial evaluation）：https://openpolicyagent.org/docs/filtering/partial-evaluation
- OPA 集成文档（非 Go 须 sidecar）：https://openpolicyagent.org/docs/integration
- Casbin 性能问题 #336（10k 规则 500ms）：https://github.com/casbin/pycasbin/issues/336
- Casbin 性能问题 #681（1.6M 规则 12-18s）：https://github.com/apache/casbin/issues/681
- Casbin 缓存 bug #832：https://github.com/apache/casbin/issues/832
- Casbin 缓存 stale #1580：https://github.com/apache/casbin/pull/1580
- Casbin 无 SQL 下推：https://stackoverflow.com/questions/61455215/enforce-casbin-policy-into-sql-where

### 缓存
- cashews（选型）：https://github.com/Krukov/cashews
- cashews PyPI（v7.5.0）：https://pypi.org/project/cashews/
- cashews 文档（LLMs 镜像）：https://context7.com/krukov/cashews/llms.txt
- cashews client-side caching 文章（作者）：https://medium.com/the-pandadoc-tech-blog/redis-client-side-cache-with-async-python-6228a0121a12
- cashews 锁问题 #333：https://github.com/Krukov/cashews/issues/333
- aiocache（对比，未选）：https://github.com/aio-libs/aiocache
- aiocache decorator bug #973：https://github.com/aio-libs/aiocache/issues/973
- dogpile.cache（对比，同步不选）：https://github.com/sqlalchemy/dogpile.cache

### 身份认证
- Better Auth（选型，TS 认证框架）：https://github.com/better-auth/better-auth
- Better Auth 官网：https://better-auth.com/
- Better Auth SSO 插件文档（联邦配置）：https://better-auth.com/docs/plugins/sso
- @better-auth/sso npm：https://www.npmjs.com/package/@better-auth/sso
- Better Auth Admin 插件：https://better-auth.com/docs/plugins/admin
- Better Auth Organization 插件：https://better-auth.com/docs/plugins/organization
- Better Auth SCIM 插件：https://better-auth.com/docs/plugins/scim
- Better Auth JWT 插件：https://better-auth.com/docs/plugins/jwt
- Better Auth Hono 集成：https://better-auth.com/docs/integrations/hono
- Better Auth Database 概念：https://better-auth.com/docs/concepts/database
- Better Auth Installation：https://better-auth.com/docs/installation
- Vercel 收购 Better Auth（2026-07-07）：https://vercel.com/blog/vercel-acquires-better-auth
- oil-auth（生产级 starter）：https://github.com/savioruz/oil-auth
- Express-BetterAuth-Boilerplate：https://github.com/mrmovas/Express-BetterAuth-Boilerplate
- better-hono（极简 starter）：https://github.com/alwaysnomads/better-hono
- better-auth-starter（Hono+Redis）：https://github.com/kadumedim/better-auth-starter
- fastapi-betterauth（Python JWT 验证，Alpha）：https://github.com/lukonik/fastapi-betterauth
- Authlib（应用层 JWT 验证，底层库）：https://github.com/authlib/authlib
- Authlib FastAPI 集成：https://docs.authlib.org/en/latest/oauth2/client/web/fastapi.html
- FastAPI 社区讨论 python-jose 停摆：https://github.com/fastapi/fastapi/discussions/9587
- fastapi-oidc（对比，依赖 python-jose 不选）：https://github.com/HarryMWinters/fastapi-oidc
- fastapi-azure-auth（对比，绑 Azure 不选）：https://github.com/intility/fastapi-azure-auth
- AuthX（对比，协议覆盖不足）：https://github.com/yezz123/authx
- python-social-auth（对比，无 async 无 FastAPI adapter）：https://github.com/python-social-auth/social-core
- Keycloak（IDP Broker 备选）：https://www.keycloak.org/
- Authentik（IDP Broker 备选）：https://goauthentik.io/
- SSOJet：Python 企业 SSO 生态薄于 Java/.NET：https://ssojet.com/blog/enterprise-sso-in-fastapi-how-to-add-saml-and-oidc-auth-to-python-apis-in-2026
- WorkOS：Java Spring Security 完整度 vs Python：https://workos.com/blog/java-authentication-guide-2026
- Spring Security 架构（参照）：https://docs.spring.io/spring-security/reference/servlet/architecture.html

### §八 自建代码参考实现
- cedarpy-example-hello-photos：https://github.com/k9securityio/cedarpy-example-hello-photos
- sondera-harness-python（Cedar schema 生成）：https://github.com/sondera-ai/sondera-harness-python/blob/main/examples/cedar/coding_agent.py
- Cedar 授权模式指南：https://docs.cedarpolicy.com/bestpractices/bp-authorization-patterns.html
- atlas9: Building an access framework using Cedar：https://atlas9.dev/blog/access-with-cedar.html
- FastAPI + PostgreSQL RLS 多租户：https://fernandocode.pages.dev/en/blog/fastapi-rls-multitenant-en/
- ulfblk-multitenant（FastAPI RLS 库）：https://pypi.org/project/ulfblk-multitenant/
- CommonTrace RLS with SQLAlchemy：https://www.commontrace.org/trace/postgresql-row-level-security-rls-with-sqlalchemy/
- pgrls（RLS 生成 + lint）：https://pypi.org/project/pgrls/0.38.0/
- django-rls-tenants 架构：https://dvoraj75.github.io/django-rls-tenants/advanced/architecture/
- Query-farm/python-sql-manipulation：https://github.com/Query-farm/python-sql-manipulation
- sqlglot AST primer：https://github.com/tobymao/sqlglot/blob/main/posts/ast_primer.md
- FastAPI Authentication Best Practices 2026：https://safeguard.sh/resources/blog/fastapi-authentication-best-practices-2026
- FastAPI middleware ordering：https://uguraslim.com/blog/fastapi-middleware-ordering-why-your-cors-auth-and-tenant-co/
- fastapi-principal：https://pypi.org/project/fastapi-principal/
- immutable audit trail (FastAPI+async SA)：https://dev.to/codemalasartes/an-immutable-audit-trail-for-ai-agent-actions-fastapi-async-sqlalchemy-4m4c
- postgresql-audit：https://postgresql-audit.readthedocs.io/en/stable/sqlalchemy.html
- Thalian Audit Log Chain：https://docs.thalian.ai/audit-log-chain-of-custody/
- JSON Audit Trail guide：https://jsonic.io/guides/json-audit-trail

### 行级下推参考实现
- pgrest-lambda（Cedar→SQL WHERE）：https://github.com/yoshuacas/pgrest-lambda/blob/main/docs/authorization.md
- cedar-rag-authz-demo（Cedar TPE→向量库 filter）：https://github.com/windley/cedar-rag-authz-demo
- Cedarling PG RLS 扩展：https://github.com/JanssenProject/jans/wiki/Cedarling-PostgreSQL-Extension

### 前端权限控制
- Ship the policy, not the code（allowedActions 模式源头）：https://www.jayfreestone.com/writing/share-the-policy-not-the-code/
- 三道闸门（Render/Data/Backend Gate）：https://dev.to/nwosaemeka/hiding-the-button-isnt-authorization-why-you-must-gate-the-request-156k
- CASL React Can passThrough（PermissionGate 参考）：https://www.npmjs.com/package/@casl/react
- Backstage usePermission / PermissionedRoute（路由保护参考）：https://github.com/backstage/backstage/blob/master/plugins/permission-react/src/hooks/usePermission.ts
- Backstage 前端授权文档：https://backstage.io/docs/permissions/plugin-authors/05-frontend-authorization/
- Oso 前端授权最佳实践：https://www.osohq.com/docs/learn/guides/ui
- Better Auth React client：https://better-auth.com/docs/concepts/client
- Better Auth useSession：https://github.com/better-auth/better-auth/blob/main/docs/content/docs/concepts/session-management.mdx
- Better Auth organization 切换：https://better-auth-ui.com/docs/react/queries/active-organization
- Better Auth SSO 登录流程：https://github.com/better-auth/better-auth/blob/9fed16b6/docs/content/docs/plugins/sso.mdx
- Better Auth SSO 组织 scope 修复（PR #9024）：https://github.com/better-auth/better-auth/pull/9024
- React Aria Filterable CRUD Table（权限页面参考）：https://react-aria.adobe.com/examples/crud
- React Aria Table 文档：https://react-aria.adobe.com/Table
- React Aria Forms 文档：https://react-aria.adobe.com/forms
- pro-admin-template（React Aria + Tailwind v4 admin 模板）：https://github.com/dangbt/pro-admin-template

### §七 补充证据
- Doris Row Policy 语法：https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-governance/CREATE-ROW-POLICY/
- Doris Data Access Control：https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/
- Doris 用户变量（SET @var）：https://doris.apache.org/docs/4.x/sql-manual/basic-element/variables/
- Doris Ranger 集成：https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/ranger/
- sqlglot pushdown_predicates：https://github.com/tobymao/sqlglot/blob/main/sqlglot/optimizer/pushdown_predicates.py
- sqlglot 优化器文档：https://deepwiki.com/tobymao/sqlglot/6.5-predicate-pushdown-and-subquery-optimization
- AskTable SQL Permission Guard（生产级参考）：https://www.asktable.com/en-US/blog/2026-03-05/asktable-sql-permission-guard-sqlglot
- AskTable AST 权限控制：https://www.asktable.com/en-US/blog/2026-03-04/sql-guard-ast-permission-control
- Apache Superset sqlglot RLS PR #33524：https://github.com/apache/superset/pull/33524
- pydantic-ai Dependencies 文档：https://pydantic.dev/docs/ai/core-concepts/dependencies/
- pydantic-ai Function Tools：https://pydantic.dev/docs/ai/tools-toolsets/tools/
- AutoCedar（学术+开源）：https://arxiv.org/html/2607.03656
- AutoCedar 开源引擎：https://github.com/neselab/cedar-synthesis-engine
- Autoformalization 论文：https://arxiv.org/html/2606.26649
- AWS Bedrock AgentCore 用 Cedar：https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/
- VS Code Cedar 扩展（官方）：https://github.com/cedar-policy/vscode-cedar
- AWS CI/CD Cedar 策略验证：https://aws.amazon.com/blogs/security/automate-cedar-policy-validation-with-aws-developer-tools/

---

