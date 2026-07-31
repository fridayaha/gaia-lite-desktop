# 带安全凭证的 Skill 配置与使用指南

本指南面向 **skill 开发者**与**平台使用者**，说明如何在 UnionAgents 中开发、打包、配置、使用一个**带安全凭证（secret 参数）**的技能，以及凭证在系统中的流转与安全约束。

> 配套示例技能：[`assets/skills/general/credential-checker/`](../../assets/skills/general/credential-checker/) —— 一个专门用于验证凭证机制的自检技能，可直接安装走通全流程。

---

## 1. 概述

许多 skill 需要调用外部 API（如企业内部服务、第三方 SaaS），因而需要持有 `API Key`、`API Secret`、`Webhook Token` 等敏感凭证。UnionAgents 对这类凭证采用**「加密集中存储 + sidecar 解密」**模型，核心原则：

| 原则 | 实现 |
|------|------|
| 明文绝不进 Pod env / 日志 | 凭证只在 Manager 数据库加密存储；Pod env、日志中均无明文。密文落 Pod `secrets.enc`，解密 key 只在 sidecar 容器 env |
| 不回显明文 | console 重新打开配置时只显示「已配置」，不回填原值 |
| Pod 内本地解密 | sidecar 容器持 key 解密 `secrets.enc`，skill 用 `execute_code` 调 `localhost:8004/secret` 拿明文，直接调外部 API（不经出口代理） |
| 最小信任 | sidecar 按 skill_name + 参数名返回；Pod 隔离（一 Pod 一 agent 实例，sidecar 只读本 Pod `secrets.enc`） |

### 凭证流转

```
console 配置抽屉
   │  填写 secret 参数值
   ▼
Manager  ──Fernet 加密──▶  skill_credentials.credentials_encrypted  （密文落库）
   │  save_skill_credentials 后触发 fan-out
   ▼
controller write_skill_secrets  ──▶  Pod skills/{name}/secrets.enc  （密文落盘，PVC/MinIO 带密文）
   │
   │  skill 运行时（user console 对话触发 execute_code）
   ▼
sidecar 容器  GET localhost:8004/secret?skill=<name>&key=<param>
   │  读 secrets.enc + credential_encryption_key 解密 → 返回 {"value": "<明文>"}
   ▼
execute_code  拿明文直接调外部 API（自己带 Authorization 头，不经出口代理）
```

---

## 2. 凭证机制原理

### 2.1 存储

- 表 `skill_credentials`，列 `credentials_encrypted` 存 **Fernet token**（base64），明文结构由 manifest `config_params` 决定。
- 维度：`definition_id`（智能体定义）+ `skill_name` + `scope_type='ALL'`。当前为定义级全局凭证。
- 代码：`services/manager/app/core/crypto.py`（`encrypt_credentials_dict` / `decrypt_credentials_dict`）。

### 2.2 读写 API

| 操作 | 方法 & 路径 | 行为 |
|------|------------|------|
| 保存凭证 | `PUT /api/manager/agent-definitions/{definition_id}/skills/{skill_id}/credentials` | 仅接受 manifest 中 `secret:true` 的参数；空值表示不修改；多次提交 **merge** 已有值；保存后 fan-out 密文到各实例 Pod |
| 查询状态 | `GET /api/manager/agent-definitions/{definition_id}/skills/{skill_id}/credentials` | 返回 `{configured: [参数名...], target_base_url}`，**不回显明文** |

- 代码：`services/manager/app/api/agent_skills.py`（`save_skill_credentials` / `get_skill_credential_status`）。
- 越权隔离：`_require_definition` 做组隔离，跨组访问返回 404。

### 2.3 sidecar 解密（凭证注入）

skill 在引擎内通过 `execute_code` 调 Pod 内 sidecar 拿明文，自己带凭证调外部 API（不经出口代理）：

- sidecar 镜像：`services/skill-secret-sidecar/sidecar.py`，监听 Pod 内 `:8004`。
- 端点：`GET localhost:8004/secret?skill=<skill_name>&key=<param_name>`，返回 `{"value": "<明文>"}`。
- sidecar 读 `skills/{skill_name}/secrets.enc` + 容器 env `CREDENTIAL_ENCRYPTION_KEY` 解密。
- 密文落盘：`services/manager/app/worker/router.py` 的 `write_skill_secrets` 把 `credentials_encrypted` fan-out 到各 Pod home 的 `skills/{name}/secrets.enc`。
- 解密 key 派生复刻 `crypto.py:_load_fernet`（sha256 + urlsafe_b64encode），sidecar 与 manager 必须用同一 `credential_encryption_key`，否则解密失败。

> hermes `execute_code` 沙箱**隔离 Pod env**（读不到业务环境变量），但能 `open()` Pod 文件 + 调 localhost。故 secret 密文落 Pod 文件、sidecar 本地解密，skill 持明文在 execute_code 进程内调外部 API，明文不进 LLM 上下文（execute_code 不 print 明文）。

---

## 3. 声明 secret 参数（manifest.json）

技能元数据来自 `manifest.json`（优先级高于 `SKILL.md` frontmatter）。secret 参数通过 `config_params` 数组声明。

### 3.1 config_params 字段 schema

| 字段 | 类型 | 必填 | 说明 | 消费方 |
|------|------|------|------|--------|
| `name` | string | ✅ | 参数名，作为凭证 dict 的 key，也是 sidecar `key` 查询参数 | 前端 + 后端 |
| `label` | string | ✅ | console 抽屉里的显示标签 | 前端 |
| `type` | string | ✅ | `string` / `number` / `boolean` / `select` | 前端 |
| `secret` | boolean | — | `true` 表示该参数是安全凭证，渲染为密码框且走加密存储 | 前端 + 后端 |
| `description` | string | — | 输入框 placeholder / 说明 | 前端 |
| `options` | string[] | — | `type=select` 时的可选项 | 前端 |

> ⚠️ **当前未实现的字段**：`required`、`default` 等约束字段**前后端均未消费**，写了不会生效。`type` 仅用于前端渲染，后端存储时不区分类型。

### 3.2 type 与前端渲染对照

| type | secret | console 控件 |
|------|--------|-------------|
| `string` | `false` | 普通输入框 |
| `string` | `true` | 密码框（`show-password`）+ 「已配置」绿标 |
| `number` | — | 数字输入框（min=0） |
| `boolean` | — | 开关 |
| `select` | — | 下拉（`options`） |

> **本期限制**：保存时**只提交 secret 参数**，非 secret 参数的值**不持久化**（`saveSkillConfig` 仅收集 `secret:true` 的参数）。非 secret 参数目前仅用于前端展示，重新打开后为空。若需持久化非 secret 配置，需扩展后端。

### 3.3 manifest.json 示例

```json
{
  "name": "my-api-skill",
  "version": "1.0.0",
  "author": "your-team",
  "description": "调用某外部 API 的技能，持有 API Key 与 Secret",
  "icon": "ri:search-eye-line",
  "engine": ["HERMES"],
  "config_params": [
    {
      "name": "api_key",
      "label": "API Key",
      "type": "string",
      "secret": true,
      "description": "外部 API 密钥，sidecar 解密后以 Authorization: Bearer 调外部 API"
    },
    {
      "name": "api_secret",
      "label": "API Secret",
      "type": "string",
      "secret": true,
      "description": "用于签名/二次验证的 secret"
    },
    {
      "name": "endpoint",
      "label": "Endpoint",
      "type": "string",
      "description": "外部 API 地址（非 secret，本期不持久化）"
    }
  ]
}
```

`icon` 取值需命中前端 `iconMap`（`apps/admin/src/views/agent-definitions/detail/SkillsTab.vue`），可选：`ri:code-s-slash-line`、`ri:bar-chart-2-line`、`ri:file-text-line`、`ri:search-eye-line`、`ri:git-branch-line`；未命中则回退默认图标。

---

## 4. 编写 SKILL.md

`SKILL.md` 是引擎扫描识别技能的依据（`{home}/skills/**/SKILL.md`），frontmatter 提供 `name/description/version/author`，正文是 agent 的使用说明。

### 4.1 frontmatter

```yaml
---
name: my-api-skill
description: 调用某外部 API 的技能
version: 1.0.0
author: your-team
---
```

### 4.2 正文：如何使用凭证

skill 正文应指导 agent **通过 sidecar 拿明文**后调外部 API。关键点：

1. 用 `execute_code` 执行脚本，`GET http://localhost:8004/secret?skill=<本技能name>&key=<secret参数名>` 拿明文。
2. 用明文直接调外部 API（自己带 `Authorization` 头）。
3. **不要** print secret 明文（避免进对话/日志）；回显的 `Authorization` 需脱敏。
4. **不要** 读 Pod env（execute_code 沙箱隔离，读不到业务环境变量）。

示例正文片段：

```markdown
## 调用外部 API

用 execute_code 执行：
1. GET http://localhost:8004/secret?skill=my-api-skill&key=api_key 拿明文 api_key。
2. 用明文调外部 API：
   req = urllib.request.Request("https://api.example.com/v1/data",
       headers={"Authorization": f"Bearer {api_key}"})
3. 报告结果时，Authorization 头一律脱敏为 Bearer ***。
```

> sidecar 的 `key` 查询参数必须是 manifest 中声明的某个 `secret:true` 参数名（如 `api_key`）。

---

## 5. 打包

### 5.1 zip 结构

- 必含 `SKILL.md`（根或单一顶层目录下均可）。
- 可选 `manifest.json`（提供 `config_params` 等元数据）。
- **单一顶层目录会被自动剥离**（`_zip_to_tar_strip_top`），最终落到 `{home}/skills/{skill_name}/`。推荐带一层 `skill_name/` 目录。
- 路径安全：禁止 `..`、绝对路径、反斜杠。

### 5.2 打包命令

仓库根目录执行：

```bash
python3 -c "
import zipfile
src = 'assets/skills/general/my-api-skill/'
with zipfile.ZipFile('assets/skills/general/my-api-skill.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for f in ['SKILL.md', 'manifest.json']:
        z.write(src + f, 'my-api-skill/' + f)
"
```

### 5.3 上传前自检

用 Manager 的 preview 接口（不安装）校验 zip 解析是否正常：

```bash
curl -F "file=@assets/skills/general/my-api-skill.zip" \
  -H "Authorization: Bearer <token>" \
  https://<manager>/api/manager/agent-definitions/<definition_id>/skills/preview
```

返回 `manifest.configParams` 应包含你声明的全部参数；`warnings` 为空。

---

## 6. console 配置凭证

### 6.1 入口

智能体定义列表 → 进入某定义详情页 → **Skills** Tab → 上传 zip 安装 → 点该技能「配置」按钮 → 抽屉。

- 路由：`/agent-definitions/detail/:id`
- 组件：`apps/admin/src/views/agent-definitions/detail/SkillsTab.vue`

### 6.2 配置行为

| 操作 | 预期行为 |
|------|---------|
| 填 secret 值并保存 | Fernet 加密落库 + fan-out 密文到各实例 Pod `secrets.enc`；抽屉关闭 |
| 重新打开 | secret 参数显示「已配置」绿标，**输入框不回填明文** |
| 再填另一个 secret 保存 | 与已有值 **merge**，两个均显示已配置 |
| 对已配置参数提交空值 | **不覆盖**已有值 |
| 填非 secret 参数并保存 | **不持久化**，重新打开为空（本期限制） |

### 6.3 验证加密落库

```sql
SELECT skill_name, credentials_encrypted
FROM skill_credentials
WHERE definition_id = '<definition_id>';
-- credentials_encrypted 为 Fernet token，不得包含任何明文子串
```

---

## 7. 引擎侧使用凭证（sidecar API）

### 7.1 sidecar 部署

- 引擎 Pod spec 含 sidecar 容器（`skill-secret-sidecar`），共享 hermes-data volume（读 `secrets.enc`），env 注入 `CREDENTIAL_ENCRYPTION_KEY`。
- hermes 容器**无** `CREDENTIAL_ENCRYPTION_KEY` env，`execute_code` 沙箱又隔离 Pod env，故 skill 代码读不到解密 key。

### 7.2 取 secret 明文

```
GET http://localhost:8004/secret?skill=<skill_name>&key=<param_name>
→ 200 {"value": "<明文>"}
```

### 7.3 错误码对照

| 状态码 | 含义 | 排查 |
|--------|------|------|
| 404 `no secrets for skill ...` | `secrets.enc` 未落盘 | 凭证保存后 fan-out 未执行，重装 skill 触发 fan-out |
| 500 `decrypt failed` | sidecar 的 `CREDENTIAL_ENCRYPTION_KEY` 与 manager 不一致 | 检查部署配置，两边 key 必须一致 |
| 404 `secret ... not configured` | 该参数未在 console 配置 | 在 console 配置抽屉填写该 secret |
| execute_code 连不上 `localhost:8004` | sidecar 容器未起 | 重建引擎 Pod 让 spec 带 sidecar 生效 |

---

## 8. 安全约束（必须遵守）

1. **明文不进 Pod env / 日志**：凭证只在 Manager 加密存储 + sidecar 内存短暂解密。严禁把 secret 写进 `SKILL.md`、manifest、config.yaml、环境变量、日志。
2. **密文落 Pod**：`secrets.enc` 是 Fernet 密文，PVC/MinIO 带密文可接受；解密 key 只在 sidecar 容器 env（hermes 容器无此 env）。
3. **走 sidecar 拿明文**：skill 调外部 API 前用 `execute_code` 调 `localhost:8004/secret` 取明文，不得硬编码 secret、不得读 Pod env。
4. **回复脱敏**：agent 回复中不得输出 secret 明文；回显的 `Authorization` 头需脱敏（如 `Bearer sk-***1234`）。
5. **示例文件占位符**：任何示例/文档中的凭证只能用占位符（`your-api-key-here`），不得放真实凭据（见 `CLAUDE.md` 安全约束）。

---

## 9. 完整示例

参考自检技能 [`assets/skills/general/credential-checker/`](../../assets/skills/general/credential-checker/)：

- `manifest.json` 声明 3 个 secret（`api_key`/`api_secret`/`webhook_token`）+ 4 个非 secret（覆盖 string/number/boolean/select 全部渲染分支）。
- `SKILL.md` 给出经 sidecar 解密 `api_key` 后调 `httpbin.org/anything` 回显、验证 `Authorization` 注入的完整步骤与错误码解读。
- `README.md` 给出打包命令与两层验证清单。

可直接安装它走通「配置 → 加密存储 → 密文落盘 → sidecar 解密 → 注入」全流程。

---

## 10. 验证清单

### console 入口（无需引擎运行）

- [ ] 安装后「配置」抽屉出现声明的全部参数，控件类型正确
- [ ] secret 参数为密码框，非 secret 为对应控件
- [ ] 填 secret 保存 → 重新打开显示「已配置」且不回显明文
- [ ] 多个 secret 逐个保存 → merge，均显示已配置
- [ ] 提交空值 → 不覆盖已有
- [ ] 查 DB `credentials_encrypted` 不含明文子串
- [ ] 跨组账号调保存接口 → 404

### 引擎端到端（需 Pod 运行 + 外网）

- [ ] `kubectl exec <pod> -- env` 无 secret 明文（key 只在 sidecar 容器 env）
- [ ] `kubectl exec <pod> -- ls /opt/data/profiles/*/skills/<name>/secrets.enc` 存在
- [ ] `execute_code` 调 `localhost:8004/secret?skill=<name>&key=api_key` 返回明文
- [ ] 用明文调外部 API，收到正确 `Authorization` 注入
- [ ] 未配置凭证时 sidecar 返回 404 `secret ... not configured`

---

## 11. 相关代码索引

| 关注点 | 位置 |
|--------|------|
| 凭证存取 API | `services/manager/app/api/agent_skills.py` |
| 加解密 | `services/manager/app/core/crypto.py` |
| 密文 fan-out 落盘 | `services/manager/app/worker/router.py`（`write_skill_secrets`） |
| sidecar 解密服务 | `services/skill-secret-sidecar/sidecar.py`（`GET /secret`） |
| 技能 zip 解析 | `services/manager/app/api/agent_skills.py`（`_parse_zip`） |
| 技能 fan-out 落盘 | `services/manager/app/worker/router.py`（`_fanout_skill_to_homes` / `_zip_to_tar_strip_top`） |
| 前端配置入口 | `apps/admin/src/views/agent-definitions/detail/SkillsTab.vue` |
| 前端 API 封装 | `apps/admin/src/api/manager/skills.ts` |
| 凭证单元测试 | `services/manager/tests/test_skill_credentials.py` |

---

## 12. skill-studio 开发期

skill-studio（skill-engine 服务）自身提供与生产 sidecar 一致的密钥访问契约，让技能代码在开发调试与生产环境**零改动可移植**。

- **声明 + 录入**：在 Skill Studio「编辑配置」弹窗声明 `config_params`（name/label/type/secret/options），并录入值。密钥（`secret:true`）加密存 `workspaces.credentials_encrypted`（AES-256-GCM，key 取自 `UA_CREDENTIAL_ENCRYPTION_KEY`，与 manager 同值）；非密钥明文存 `workspaces.config`。
- **运行时取密钥**：debug 会话的技能脚本调 `http://localhost:8004/secret?skill=<name>&key=<param>` → skill-engine 自身解密返回明文。`/secret` 是裸路径（不进 manager 代理），仅 Pod 内可达，靠 localhost 隔离（对齐生产 sidecar）。多 workspace 同 skill_name 取最近 updated。
- **非密钥引用**：技能 SKILL.md 里用 `${config.param}` 引用非密钥值；debug 会话镜像 SKILL.md 到 `.pi/skills/user-skill/` 时按 `workspaces.config` 替换（对齐生产 fanout 替换，仅替换 body、跳过 frontmatter、bool→`True`/`False`）。
- **代码位置**：加解密 `services/skill-engine/src/utils/crypto.ts`；路由 `services/skill-engine/src/routes/config-routes.ts`（admin `GET/PUT /api/skill-engine/workspaces/:id/config` + 运行时 `/secret`）；替换 `services/skill-engine/src/engine/engine-worker.ts` `syncUserSkill`；DB 列在 `workspaces` 表（`skill_name`/`config`/`credentials_encrypted`）。
- **部署**：skill-engine container 需注入 `UA_CREDENTIAL_ENCRYPTION_KEY`（secretKeyRef `credential-encryption-key`，云端由 deploy.sh envsubst 注入）。

> 与生产的差异：生产密钥存 manager `skill_credentials` 表 + fan-out `secrets.enc` 到引擎 Pod + 独立 sidecar 解密；skill-studio 密钥存 skill-engine `workspaces` 表 + skill-engine 自身解密。两套独立存储，但技能代码访问 URL 一致。
