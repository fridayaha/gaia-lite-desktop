# Skill 安全凭证机制改造方案 —— 路径 B（密文 + sidecar 解密）

> 状态：已实现。2026-07-25 更新：key 改 projected Secret 文件挂载（每请求重读，轮换无需重启 sidecar）+ 多 key Fernet（MultiFernet，旧密文仍可解）+ 全链技能 reconcile 原语（DB↔COS↔Pod↔secrets.enc 自愈）+ fan-out 原子换入 + install 先存 COS 后 fan-out。详见第 12 节。
> 日期：2026-06-30（初版），2026-07-25（更新）
> 关联：[skill-credentials.md](skill-credentials.md)（当前 egress 模式）、节点 101.96.214.49 部署记忆

## 1. 背景与问题

### 1.1 旧实现（egress 代理模式，已废弃移除）

> ⚠️ 该模式已废弃并从代码库移除：`services/manager/app/api/skill_proxy.py`、`agent_skills.py` 的 `skill_credential_self_test` 端点、`router.py` 的 `CREDENTIAL_PROXY_URL` 注入与 env.json 写入均已删除。以下仅作历史背景，当前实现见第 4 节路径 B（sidecar 解密）。

- secret 经 console 配置 → Fernet 加密存 `skill_credentials.credentials_encrypted`（DB）
- skill 调外部 API 时走 manager 出口代理（egress，`/api/engine-proxy/*`）
- egress 代理解密凭证 → 注入 `Authorization` 头 → 转发外部 API
- secret 明文**不进 Pod**（只在 manager 内存短暂解密）

### 1.2 hermes 引擎的限制（实测确认）

在节点 101.96.214.49 的 engine-hermes Pod 上验证：

| 项 | 结论 | 证据 |
|----|------|------|
| hermes 工具能发 HTTP 的 | **只有 `execute_code`**（无 terminal/web_fetch/http 工具） | agent 自述工具列表 |
| `execute_code` 沙箱读 Pod env | ❌ **隔离**（读不到 `CREDENTIAL_PROXY_URL`/`AGENT_ID`/`API_SERVER_KEY`） | credential-checker v1.1.0 实测 `os.environ.get` 返回 None |
| `execute_code` 沙箱读 Pod 文件 | ✅ **不隔离**（能 `open()` + `listdir` `/opt/data/profiles/.../skills/`） | user console 实测 open SKILL.md/env.json 成功 |
| `execute_code` 沙箱网络 | ✅ 可达 `manager:8002`（返回 404，服务在跑） | user console 实测 |
| hermes 把 config 注入 agent 上下文 | `_inject_skill_config` 把 `metadata.hermes.config` 值注入 **LLM 上下文**（message parts） | `skill_commands.py:206` |
| hermes 是否把 Pod env 注入 agent 上下文 | ❌ 不注入业务 env（只 `HERMES_*` 内部变量） | grep `/opt/hermes` 源码 |

### 1.3 egress 模式在 hermes 上的死结

egress（`skill_proxy.py` `_verify_engine_caller`）要求 `Authorization: Bearer <API_SERVER_KEY>` 鉴权防越权。但：

- `API_SERVER_KEY` 在 Pod env → `execute_code` 沙箱**读不到**
- `API_SERVER_KEY` 是 secret → **不能写文件**（env.json，安全策略拒绝 secret 落盘）
- hermes 引擎层不代调 egress（不认识）

→ **hermes 上 skill 代码无法安全直接调 egress**（要调就得拿 API_SERVER_KEY，那是 secret 泄露）。当前验证改走 manager self-test 端点（manager 代调 egress），但那不是 skill 运行时使用。

### 1.4 新需求

用户要求改造为：skill **本地持有明文凭证**直接调外部 API（不经 egress），保留 `config_params` 声明 secret 参数，secret 加密存储，skill 引用参数时自动解密，不调服务。

### 1.5 key 死结（核心障碍）

"secret 密文存 Pod 文件 + skill 自己解密"要求 skill 拿到 `credential_encryption_key`：

| key 通道 | 可行 | 原因 |
|---------|------|------|
| Pod env | ❌ | execute_code 沙箱隔离 env |
| env.json 文件 | ❌ | key 是 secret，写文件泄露（安全策略拒绝） |
| hermes 引擎层解密 | ❌ | hermes 开源不改，不认识 secret |
| 不调服务 | ❌ | 不能调 manager 解密 |

→ **密文 + skill 自解密 + 不调服务**三者不可同时满足。

## 2. 方案目标

- 保留 `config_params` 声明 secret 参数（console 配置入口不变）
- secret 加密存 DB（`skill_credentials`，已有）
- skill 运行时能拿到 secret 明文调外部 API（不经 egress）
- secret 明文**不进 LLM 上下文**（不在对话/日志泄露）
- secret **不落 Pod 明文**（PVC/MinIO 不带明文）
- 不依赖 hermes 改造（开源不侵入）

## 3. 方案选型

三条让 skill 拿明文的路对比：

| 维度 | 路径 A（明文落 Pod） | **路径 B（密文+sidecar）** | egress+sidecar（旧方案A） |
|------|------------------|------------------------|------------------------|
| Pod 文件存 | 明文 secret | **密文** | 不落 |
| PVC/MinIO 存档 | 带明文 ❌ | 带密文 ✅ | 不带 ✅ |
| skill 调服务 | 不调 ✅ | 调 sidecar（Pod内localhost） | 调 sidecar→egress |
| skill 互读越权 | 有（需隔离） | 无（sidecar 按 skill 校验） | 无 |
| key 安全 | 不需 key | key 在 sidecar ✅ | 不需 key（egress 解密） |
| skill 持明文 | 持 | 持 | 不持 |
| 复杂度 | 低 | 中 | 中 |
| 符合"本地使用" | ✅ | ✅ | ❌（egress 代调） |

**选路径 B**：密文落 Pod（安全于 A）+ sidecar 本地解密（Pod 内 localhost，非外部服务）+ skill 持明文本地调外部 API。

## 4. 路径 B 详细设计

### 4.1 架构

```
console 配置 secret → manager 加密存 DB(skill_credentials.credentials_encrypted)
                        │ save_skill_credentials 后触发 fan-out
                        ▼
controller write_skill_secrets → 写密文 secrets.enc 到 Pod skills/{name}/
                        │
引擎 Pod ┌──────────────┴───────────────────┐
        │ hermes 容器(execute_code)           │ sidecar 容器(skill-secret-sidecar)
        │   │                                 │   env: credential_encryption_key
        │   │ execute_code 调 localhost:8004   │   监听 :8004
        │   ▼                                 │   ▲
        │ GET localhost:8004/secret?          │   │ 读 skills/{name}/secrets.enc
        │   skill=xxx&key=api_key             │   │ + credential_encryption_key 解密
        │   │                                 │   │ 返回 {"value": "<明文 api_key>"}
        │   ▼ 拿到明文 api_key                │
        │ execute_code 直接调外部 API         │
        │   （自己带 Authorization 头）        │
        └─────────────────────────────────────┘
```

### 4.2 组件

1. **manager `save_skill_credentials`**：保存 DB 后调 controller `write_skill_secrets`，把密文 fan-out 到 Pod
2. **controller `write_skill_secrets` 端点**：写 `skills/{name}/secrets.enc`（密文）到各 Pod home
3. **controller `install_skill`**：install 时若该 skill 已有凭证，也写密文
4. **sidecar 镜像**（`skill-secret-sidecar`，新）：python FastAPI，监听 :8004，读 `secrets.enc` + key 解密 + 返回指定参数明文
5. **controller `_deploy_body`**：引擎 Pod spec 加 sidecar 容器（env `credential_encryption_key`，共享 `/opt/data` volume）
6. **SKILL.md 模板**：execute_code 调 sidecar 拿明文 → 调外部 API

### 4.3 流程

```
1. console 配置 secret（api_key=xxx）
2. manager save_skill_credentials：Fernet 加密存 DB
3. manager 调 controller write_skill_secrets(agent_id, skill_name, credentials_encrypted)
4. controller 写密文到 Pod 各 home：{home}/skills/{skill_name}/secrets.enc
5. skill 运行时（user console 对话触发）：
   a. agent 用 execute_code 执行脚本
   b. 脚本 GET localhost:8004/secret?skill=credential-checker&key=api_key
   c. sidecar 读 secrets.enc + credential_encryption_key 解密 → 返回 {"value":"xxx"}
   d. 脚本用 xxx 调外部 API（带 Authorization: Bearer xxx）
```

## 5. 改动点（详细）

### 5.1 `services/manager/app/api/agent_skills.py`

`save_skill_credentials` 保存 DB 后，增加 fan-out 密文逻辑：

```python
# 保存 DB 后，fan-out 密文到各实例 Pod
instance_ids = await _definition_instance_ids(db, definition_id)
for iid in instance_ids:
    try:
        await controller_client.write_skill_secrets(iid, skill_name, row.credentials_encrypted)
    except controller_client.ControllerError as e:
        logger.warning("fan-out skill secrets to %s failed: %s", iid[:8], e)
```

### 5.2 `services/manager/app/worker/router.py`

加 `write_skill_secrets` 端点 + client 方法：

```python
@router.post("/api/controller/agents/{agent_id}/skills/{skill_name}/secrets")
async def write_skill_secrets(agent_id: str, skill_name: str, body: dict, db=Depends(get_manager_db)):
    """把加密的 secret 写到各 Pod home 的 skills/{name}/secrets.enc（密文落盘）。"""
    credentials_encrypted = body["credentials_encrypted"]
    pods = await _iter_agent_target_pods(agent_id, db)
    for p in pods:
        for home in p["homes"]:
            dest = f"{home}/skills/{skill_name}/secrets.enc"
            await k8s_manager.exec_write_file_in_pod(p["pod_name"], dest, credentials_encrypted)
    return {"ok": True}
```

`install_skill` / `_fanout_skill_to_homes` 时若已有凭证，也写 secrets.enc（从 DB 取密文）。

### 5.3 sidecar 镜像（新）

`services/skill-secret-sidecar/`：

```dockerfile
FROM python:3.11-slim
RUN pip install fastapi uvicorn cryptography
WORKDIR /app
COPY sidecar.py .
CMD ["uvicorn", "sidecar:app", "--host", "0.0.0.0", "--port", "8004"]
```

`sidecar.py`（实现见 `services/skill-secret-sidecar/sidecar.py`）：

```python
import os, json, glob, base64, hashlib
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

app = FastAPI()
_DEV_KEY_MATERIAL = b"ua-credential-dev-key-do-not-use-in-prod"
# projected Secret volume（unionagents-secret/credential-encryption-key），kubelet ~60s 刷新
_KEY_FILE = os.environ.get("UA_CREDENTIAL_KEY_FILE", "/etc/ua/credential-key/credential-encryption-key")
_SKILLS_ROOT = os.environ.get("UA_SKILLS_ROOT", "/opt/data/skills")  # external_dirs 共享模型

def _derive(material: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))

def _load_fernet() -> MultiFernet:
    """每请求重读 key 文件（不缓存）→ kubelet 刷新 projected volume 后即生效，无需重启。
    多 key：换行分隔，newest 在前（轮换时前置新 key 不删旧 → 旧密文仍可解）。"""
    raw = None
    try:
        with open(_KEY_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        raw = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")  # rollout 期回退
    materials = [ln.strip().encode() for ln in (raw or "").split("\n") if ln.strip()] or [_DEV_KEY_MATERIAL]
    return MultiFernet([_derive(m) for m in materials])

@app.get("/secret")
async def get_secret(skill: str = Query(...), key: str = Query(...)):
    # external_dirs 共享模型：{defid}/{skill}/secrets.enc
    candidates = glob.glob(f"{_SKILLS_ROOT}/*/{skill}/secrets.enc")
    if not candidates:
        raise HTTPException(404, f"no secrets for skill {skill}")
    try:
        with open(candidates[0], "rb") as f:
            creds = json.loads(_load_fernet().decrypt(f.read()))
    except InvalidToken:
        raise HTTPException(500, "decrypt failed: invalid token or key mismatch")
    if key not in creds:
        raise HTTPException(404, f"secret {key} not configured for skill {skill}")
    return JSONResponse({"value": creds[key]})
```

### 5.4 `services/manager/app/worker/router.py` `_deploy_body`

引擎 Pod spec 加 sidecar 容器：

```python
# Pod spec volumes 加 projected Secret（key 轮换无需重建 Pod，kubelet ~60s 刷新）
volumes.append(V1Volume(
    name="credential-key",
    projected=V1ProjectedVolumeSource(sources=[
        V1SecretProjection(
            name="unionagents-secret",
            items=[V1KeyToPath(key="credential-encryption-key", path="credential-encryption-key")],
            optional=True,  # 本地 dev secret.yaml 无此 key 时空目录，sidecar 回退 env/dev
        ),
    ]),
))
# sidecar 容器：挂载 credential-key（只读）+ 共享 hermes-data PVC（读 secrets.enc）
containers.append(V1Container(
    name="skill-secret-sidecar",
    image="unionagents/skill-secret-sidecar:latest",
    ports=[V1ContainerPort(container_port=8004)],
    env=[V1EnvVar(name="UA_SKILLS_ROOT", value="/opt/data/skills")],  # key 走文件挂载，env 仅 rollout 期回退
    volume_mounts=[
        V1VolumeMount(name="hermes-data", mount_path="/opt/data"),
        V1VolumeMount(name="credential-key", mount_path="/etc/ua/credential-key", read_only=True),
    ],
    resources=V1ResourceRequirements(
        requests={"cpu": "50m", "memory": "64Mi"}, limits={"cpu": "200m", "memory": "128Mi"}
    ),
))
```

注意：sidecar 读 projected Secret volume（key 轮换无需重启）+ 共享 `hermes-data` PVC（读 secrets.enc）。hermes 容器无 `credential-key` 挂载（execute_code 沙箱读不到 key）。

### 5.5 SKILL.md 模板

```markdown
## 使用 secret 参数

本技能的 secret 参数（manifest 声明 secret:true）已由平台加密存到 Pod。
用 execute_code 调本地 sidecar 获取解密后的明文（不经 egress，不读 env）：

\```python
import urllib.request, json
# 从 sidecar 拿解密后的 secret
r = urllib.request.urlopen(
    "http://localhost:8004/secret?skill=<本技能name>&key=<secret参数名>", timeout=5)
api_key = json.loads(r.read())["value"]
# 直接调外部 API（自己带凭证）
req = urllib.request.Request("https://api.example.com/data",
    headers={"Authorization": f"Bearer {api_key}"})
print(urllib.request.urlopen(req).read().decode())
\```

注意：
- 不要 print secret 明文（避免进对话日志）
- secret 参数名取自 manifest config_params 的 name
```

### 5.6 部署

- build sidecar 镜像 + manager（含 5.1/5.2/5.4 改动）
- `docker save | k3s ctr import` + `kubectl rollout restart`
- 重装 credential-checker（触发 fan-out 写 secrets.enc）
- 验证

## 6. 安全分析

| 项 | 情况 |
|----|------|
| secret 落 Pod | **密文**（secrets.enc），PVC/MinIO 带密文 |
| credential_encryption_key | 只在 sidecar 容器 env（Pod env），hermes 容器无此 env，execute_code 读不到 env |
| secret 明文去向 | sidecar→execute_code 进程内，**不进 LLM 上下文**（execute_code 不 print） |
| 越权防护 | Pod = 一个 agent instance，sidecar 只读本 Pod secrets.enc（k8s Pod 隔离）；同 Pod 多 skill 互读属同 agent 可信域 |
| config_params 声明 | 保留（sidecar 按 skill_name + 参数名返回） |
| 不改 hermes | sidecar 是 Pod 内独立容器，不侵入 hermes |

## 7. skill 代码用法

见 5.5。skill 开发者：
1. manifest `config_params` 声明 secret 参数（name + secret:true）
2. console 配置 secret 值
3. skill 代码 `execute_code` 调 `localhost:8004/secret?skill=<name>&key=<参数名>` 拿明文
4. 用明文调外部 API

## 8. 部署与验证

### 部署步骤
1. build sidecar 镜像（`services/skill-secret-sidecar/`）
2. build manager（含 5.1/5.2/5.4 改动）
3. `docker save | k3s ctr import` 两个镜像
4. `kubectl rollout restart deploy manager -n unionagents`（manager 改动）
5. **重建引擎 Pod**（让 _deploy_body 加 sidecar 生效）：通过 manager API 触发引擎重建（suspend→resume 或 destroy→redeploy），或直接删 Pod 让 controller 重建
6. 重装 credential-checker（触发 fan-out 写 secrets.enc）

### 验证
- `kubectl get pods` 引擎 Pod 2/2 或 3/3（hermes + asr-sidecar + skill-secret-sidecar）Running
- `kubectl exec <pod> -c skill-secret-sidecar -- cat /etc/ua/credential-key/credential-encryption-key` 有值（key 走文件挂载，非 env）
- `kubectl exec <pod> -- ls /opt/data/skills/*/credential-checker/secrets.enc` 存在（external_dirs 共享模型）
- user console execute_code 调 `localhost:8004/secret?skill=credential-checker&key=api_key` 返回明文
- execute_code 用明文调 httpbin，回显 Authorization 注入

## 9. 风险与待确认

### 风险
- 改 `_deploy_body` 加 sidecar 影响引擎 Pod 部署，需验证 Pod 正常起 + sidecar 健康
- sidecar 容器资源开销（小，~64Mi）
- 凭证保存后 fan-out 密文到 Pod（save_skill_credentials 加 fan-out，凭证保存延迟略增）
- ssh 限流导致部署慢（节点 fail2ban）
- 重建引擎 Pod 才能让 sidecar 生效（_deploy_body 改动需新 Pod）

### 待确认
1. **credential_encryption_key 注入 sidecar**：key 走 projected Secret volume（`/etc/ua/credential-key/...`），每请求重读，轮换无需重启。hermes 容器无此挂载，execute_code 沙箱读不到。env `CREDENTIAL_ENCRYPTION_KEY` 仅 rollout 期回退。
2. **sidecar 共享 hermes-data PVC**：sidecar 读 `/opt/data/skills/{defid}/{skill}/secrets.enc`（external_dirs 共享模型，与 manager `write_skill_secrets` 落盘路径一致）。
3. **同 Pod 多 skill 互读**：sidecar 按 skill_name 返回，同 Pod 内 execute_code 能传任意 skill_name 拿同 agent 其他 skill 的 secret。当前接受同 agent 可信域。
4. **多 profile 路径**：external_dirs 共享模型下 secrets.enc 在共享目录（非 per-home），所有 profile 经 config.yaml external_dirs 共享读同一份。

## 12. 更新日志（2026-07-25）

### 12.1 key 轮换零 500（B1 + B2）

旧实现：sidecar 在 import 期一次性构造 `_fernet = Fernet(...)`，key 来自 manager 在 Pod 创建时注入的**字面量 env**（`k8s_manager.py` `value=settings.credential_encryption_key`）。轮换 `unionagents-secret/credential-encryption-key` 后运行中 sidecar 仍持旧 key → `InvalidToken` → HTTP 500 → 所有带凭证技能 auth_fail，唯有完整 redeploy 才能恢复。

修复：
- **B1（文件挂载 + 每请求重读）**：sidecar 的 key 改读 projected Secret volume（`/etc/ua/credential-key/credential-encryption-key`），`_load_fernet()` 每请求重读（不缓存）。kubelet ~60s 刷新 projected volume 内容 → key 轮换无需重启 sidecar。env `CREDENTIAL_ENCRYPTION_KEY` 保留为 rollout 期回退。
- **B2（多 key Fernet）**：`app/core/crypto.py:_load_fernet` 与 sidecar 都返回 `MultiFernet`，key 值按换行分割为多 key（newest 在前）。`MultiFernet.encrypt` 用首 key（新写入用新 key），`MultiFernet.decrypt` 依次尝试（旧密文仍可解）→ 轮换零 500。

**Key 轮换流程**：
1. `kubectl edit secret unionagents-secret -n unionagents`，把 `credential-encryption-key` 改为 `"新key\n旧key"`（换行分隔，新 key 在前）。
2. 等 ~60s（kubelet 刷新 projected volume）。
3. 验证：`kubectl exec <pod> -c skill-secret-sidecar -- cat /etc/ua/credential-key/credential-encryption-key` 显示两行。
4. 旧 secrets.enc（旧 key 加密）仍可解（MultiFernet 试到旧 key）；新存凭证用新 key。
5. 观察期后删旧 key：需先把存量 `SkillCredential` 重存（用新 key 重加密），否则删旧 key 后旧密文不可解。（重存 admin 端点为后续工作。）

### 12.2 全链技能 reconcile 原语

旧实现：只有 `deploy_agent` 重放技能+凭证；`resume`/`restart` 不重放；Pod 启动只补 secrets.enc（`reconcile_skill_secrets`），不重解压技能文件；无持续 drift 检测。

修复（`services/manager/app/worker/config_skills.py:reconcile_skills`）：4 链对账 + 自愈——
- **COS zip**（`list_skill_zips`，重放真相源）↔ **DB skill_config** ↔ **Pod 文件**（`test -d`，每 Pod 单次 exec 批量探活）↔ **secrets.enc**（`test -f`）。
- drift 自愈：Pod 缺文件 + COS 有 → `_fanout_skill_to_pods` 重放；Pod 有文件 + 缺 secrets.enc + 有 `SkillCredential` → `write_skill_secrets`；DB 有 + COS 无 → 仅上报（需运维重传）。
- 触发点：entrypoint Pod 启动（`/skills/secrets/reconcile` 端点，已升级为全链，保留旧 URL 无需引擎镜像 bump）；`resume_agent`（等引擎就绪后调，兜底 SUSPEND 期间装的技能）；background 30min 周期循环（`_skill_reconcile_loop`，长跑 Pod 自愈，每 Pod 单次 exec 控规模负载 ≈33 pod/min @1000）。
- 规范端点 `POST /api/controller/agents/{id}/skills/reconcile`（resume/循环/manual 调用）。

### 12.3 fan-out 原子换入 + install 顺序

- **原子换入**（`_fanout_skill_to_pods`）：先解压到 `{dest}.new.{uuid}`，再 `rm -rf {dest} && mv {dest_new} {dest}`（同 PVC rename 原子）。中途失败只清理 temp，旧版保留，不留空目录（修旧 `rm -rf` 先删后 tar 失败留空目录的坑）。
- **install 顺序**（`agent_skills.py:_install_skill_bytes`）：`save_skill_zip`（COS）移到 `db.commit` 前，失败 raise 503 → session 回滚（skill_config + 审计一起回滚），不留"DB 有元数据但 COS 无 zip"的孤儿。fan-out 失败可自愈（DB + COS 已落库，reconcile 重放）。

## 10. 附录：验证结论（hermes 限制，方案依据）

- hermes 工具：`browser_*, cronjob, delegate_task, execute_code, image_generate, memory, patch, read_file, search_files, session_search, skill_manage, skill_view, skills_list, todo, vision_analyze, write_file`（无 terminal/web_fetch/http）
- execute_code 沙箱：env 隔离、文件可 open、网络可达 manager:8002
- hermes `_inject_skill_config`（skill_commands.py:206）：把 config 值注入 LLM 上下文（不适合 secret）
- egress `skill_proxy.py` `_verify_engine_caller`：要求 `Authorization: Bearer <API_SERVER_KEY>`
- 当前 manager self-test 端点（`agent_skills.py`）：manager 代调 egress 验证注入（已通，authorization_injected=true）

## 11. 相关代码

| 模块 | 位置 |
|------|------|
| 凭证存储 API | `services/manager/app/api/agent_skills.py`（save_skill_credentials / self-test） |
| 出口代理（旧） | `services/manager/app/api/skill_proxy.py` |
| 加解密 | `services/manager/app/core/crypto.py` |
| controller fan-out | `services/manager/app/worker/router.py`（_fanout_skill_to_homes / _deploy_body） |
| 前端配置入口 | `apps/admin/src/views/agent-definitions/detail/SkillsTab.vue` |
| 示例 skill | `assets/skills/general/credential-checker/` |
