# credential-checker —— 安全凭证自检技能

仅用于验证 UnionAgents 的安全凭证机制，不具备真实业务能力。覆盖两层验证：

1. **console 配置入口**：secret 参数的加密存储、不回显明文、多次保存合并、空值不覆盖、非 secret 参数仅渲染不持久化。
2. **sidecar 解密**：secret 密文落 Pod（`secrets.enc`），Pod 内 sidecar 容器持 key 解密，skill 用 `execute_code` 调 `localhost:8004` 拿明文，直接调外部 API（不经出口代理）。

## 文件

| 文件 | 作用 |
|------|------|
| `SKILL.md` | 引擎扫描识别的技能说明（frontmatter + agent 触发逻辑） |
| `manifest.json` | 技能元数据与 `config_params` 声明（3 个 secret + 4 个非 secret） |

`config_params` 覆盖前端 `SkillsTab` 全部渲染分支：

| 参数 | type | secret | 验证点 |
|------|------|--------|--------|
| `api_key` | string | ✅ | 主凭证，sidecar 解密后以 `Authorization: Bearer` 调外部 API |
| `api_secret` | string | ✅ | 多凭证 merge |
| `webhook_token` | string | ✅ | 不回显明文 |
| `echo_endpoint` | string | ❌ | string 渲染（本期不持久化） |
| `timeout_seconds` | number | ❌ | number 渲染 |
| `verbose` | boolean | ❌ | switch 渲染 |
| `http_method` | select | ❌ | select 渲染（带 options） |

## 打包

zip 内保留单一顶层目录 `credential-checker/`，Controller 安装时会自动剥离该层，落到 `{home}/skills/credential-checker/`。

```bash
python3 -c "
import zipfile
src = 'assets/skills/general/credential-checker/'
with zipfile.ZipFile('assets/skills/general/credential-checker.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for f in ['SKILL.md', 'manifest.json']:
        z.write(src + f, 'credential-checker/' + f)
"
```

（仓库根目录执行；产物 `assets/skills/general/credential-checker.zip` 为构建产物，不入库。）

## 安装

console → 智能体定义 → 详情页 → **Skills** Tab → 上传 `credential-checker.zip` 安装。

## 验证

### A. console 配置入口（无需引擎运行即可验大半）

1. 安装后点该技能「配置」，抽屉应出现 3 个 secret 密码框 + 4 个非 secret 控件（string/number/switch/select）。
2. 填 `api_key` 保存 → 重新打开：该参数显示「已配置」绿标，**输入框不回显明文**。
3. 再填 `api_secret` 保存 → 重新打开：`api_key`、`api_secret` 均显示已配置（验证 **merge**）。
4. 填 `webhook_token` 后再提交空值 → 仍显示已配置（验证**空值不覆盖**）。
5. 非 secret 参数填值保存 → 重新打开为空（验证**本期非 secret 不持久化**）。
6. 查 DB 验证加密落库：
   ```sql
   SELECT credentials_encrypted FROM skill_credentials
   WHERE skill_name = 'credential-checker';
   ```
   返回的 Fernet token 中**不得**包含任何明文子串。
7. 越权验证：换一个不属该定义所在组的账号调 `PUT .../credentials` → 404（组隔离）。

### B. sidecar 解密端到端（需 Pod 运行 + 外网可达）

1. 在终端门户 Chat 里对 agent 说「credential check」。
2. agent 按 `SKILL.md` 用 `execute_code` 调 `localhost:8004/secret?skill=credential-checker&key=api_key`，sidecar 解密 `secrets.enc` 返回明文。
3. agent 用明文调 `httpbin.org/anything`，回显响应的 `headers.Authorization` 出现 `Bearer ***` → 凭证机制生效。
4. 确认 secret 明文不进 Pod env / 日志：
   ```bash
   kubectl exec <pod> -- env | grep -iE 'api_key|api_secret|webhook_token'
   ```
   应无任何明文 secret（Pod env 只有 `AGENT_ID` 等非敏感变量；解密 key 只在 sidecar 容器 env，hermes 容器无此 env）。
5. 逐项验证多凭证：把 `key=api_key` 改成 `api_secret`、`webhook_token` 各调一次，确认三个 secret 均已配置且能解密。

## 相关代码

- 凭证存取 API：`services/manager/app/api/agent_skills.py`（`save_skill_credentials` / `get_skill_credential_status`）
- 凭证密文 fan-out 落盘：`services/manager/app/worker/router.py`（`write_skill_secrets` 写 `secrets.enc`）
- 加解密：`services/manager/app/core/crypto.py`（`encrypt_credentials_dict` / `decrypt_credentials_dict`）
- sidecar 解密服务：`services/skill-secret-sidecar/sidecar.py`（`GET /secret`）
- 前端入口：`apps/admin/src/views/agent-definitions/detail/SkillsTab.vue`
