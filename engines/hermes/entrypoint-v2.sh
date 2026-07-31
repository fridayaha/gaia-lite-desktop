#!/bin/bash
# UnionAgents V2 Engine Entrypoint
# Hermes official image + nginx Profile routing
set -e

HERMES_DATA="${HERMES_HOME:-/opt/data}"
API_KEY="${API_SERVER_KEY:-change-me}"

echo "[v2] Starting..."

# ── Step 1: Setup base profile ──
mkdir -p "$HERMES_DATA/profiles/base"

cat > "$HERMES_DATA/profiles/base/.env" <<EOF
API_SERVER_ENABLED=true
API_SERVER_KEY=$API_KEY
API_SERVER_HOST=0.0.0.0
API_SERVER_PORT=8643
GATEWAY_ALLOW_ALL_USERS=true
EOF

# LiteLLM 模型网关配置 —— 引擎只走 LiteLLM 的 OpenAI 兼容端点
if [ -n "$LITELLM_API_KEY" ]; then
    python3 -c "
import os
env_path = '$HERMES_DATA/profiles/base/.env'
cfg_path = '$HERMES_DATA/profiles/base/config.yaml'
base = os.environ.get('LITELLM_BASE_URL','').rstrip('/')
model = os.environ.get('LITELLM_MODEL','')
key = os.environ.get('LITELLM_API_KEY','')
# Hermes 的 OpenAI 兼容 provider 为 'openai-api'，
# base_url 从 OPENAI_BASE_URL 读、key 从 OPENAI_API_KEY 读（不在 config.yaml）
with open(cfg_path, 'w') as f:
    f.write('model:\n')
    f.write('  provider: openai-api\n')
    f.write(f'  default: {model}\n')
    f.write('security:\n  tirith_enabled: false\napprovals:\n  mode: off\nplatform_toolsets:\n  api_server:\n    - hermes-api-server\n    - terminal\n')
with open(env_path, 'a') as f:
    f.write(f'OPENAI_API_KEY={key}\n')
    f.write(f'OPENAI_BASE_URL={base}\n')
" 2>/dev/null || true
fi

[ ! -f "$HERMES_DATA/profiles/base/config.yaml" ] && cat > "$HERMES_DATA/profiles/base/config.yaml" <<YAML
security:
  tirith_enabled: false
approvals:
  mode: off
platform_toolsets:
  api_server:
    - hermes-api-server
    - terminal
YAML

if ! hermes profile list 2>/dev/null | grep -q "^base$"; then
    hermes profile create base 2>/dev/null || true
fi

# 修复权限：entrypoint 以 root 运行，base 由 hermes 用户运行（gateway 进程）。
# 仅 chown base——用户 profile 目录由 profile_isolation.py 按各自 uid 隔离（0700），
# 递归 chown 会清掉 per-profile uid 属主，破坏跨重启隔离。
# 父目录 755（o+x）让 profile uid 可穿越进入自己的目录。
chown -R hermes:hermes "$HERMES_DATA/profiles/base" 2>/dev/null || true

# ── Step 1.5: Langfuse 插件 env 注入（双写模式） ──
# Hermes 内置 langfuse 观测插件（plugins/observability/langfuse）opt-in，
# 在 pre_llm_call / post_llm_call hook 点写 trace。
#
# Gateway 与 Hermes 双写 trace，通过 session_id + last_user_message_hash 软关联：
# - Gateway 写外层 trace（metadata 含 agent_id/session_id/enduser_id/channel_type/
#   last_user_message_hash/gateway_request_time），admin 监控中心按 agent_id 过滤
# - Hermes 插件写内层 trace（含 Hermes 内部的多轮 LLM 调用 / tool 调用 / reasoning）
# - admin trace 详情页按 session_id + 哈希 + 时间窗口关联 Hermes 内层 trace，
#   把 Hermes 的 observations 挂到 Gateway trace 详情下方展示
#
# 插件 env 由 deploy/k8s/engines/hermes-template.yaml 通过 Secret 注入：
# HERMES_LANGFUSE_PUBLIC_KEY / HERMES_LANGFUSE_SECRET_KEY / HERMES_LANGFUSE_BASE_URL
# 未注入时插件 fail open（hooks 静默 no-op），不影响生产。
# 详见 engines/hermes/CLAUDE.md「Langfuse trace 归属」节。

# 显式 enable 插件——plugins/observability/langfuse 是 opt-in，必须显式 enable
# 才会加载 hooks（pre_llm_call/post_llm_call/pre_tool_call/post_tool_call）。
# 仅当 LANGFUSE env 任一存在时才 enable（避免空凭据 Pod 也加载插件）；缺 env
# 时插件 fail open 静默 no-op，不影响生产。
if [ -n "$HERMES_LANGFUSE_PUBLIC_KEY" ] || [ -n "$HERMES_LANGFUSE_SECRET_KEY" ]; then
    hermes plugins enable observability/langfuse 2>/dev/null || true
fi

# plugins disable 会 chmod HERMES_HOME root（/opt/data）0700（secure_parent_dir 保护 creds），
# 即便不再 disable 也要保留 chmod 755 恢复——若 entrypoint 前序步骤（profile create 等）
# 把 /opt/data 改成 0700，per-profile UID（20000+）是 other，0700 进不了 /opt/data →
# profile gateway 崩（.env PermissionError）。
# 父目录 755（o+x）让 profile uid 可穿越进入自己的目录（profile_dir 0700 owner uid）。
# skills/ 同理：external_dirs 模型下 profile gateway 经 skill 补充组读 /opt/data/skills/{defid}/，
# 父目录 skills/ 必须可穿越（o+x），否则 Permission denied。
chmod 755 "$HERMES_DATA" "$HERMES_DATA/profiles" "$HERMES_DATA/skills" 2>/dev/null || true

# ── Step 2: Reconcile port_map.json from PVC (profile→port 唯一真相) ──
# port_map.py reconcile 扫描 profiles/*/ 目录：孤儿目录分配新端口、stale 条目删除。
# 缺失/损坏的 port_map.json 也从目录重建（不读 .env 端口，杜绝多 profile 塌缩 8644）。
PORT_MAP_ALL=$(python3 /opt/scripts/port_map.py reconcile)
echo "[v2] port_map reconciled: $PORT_MAP_ALL"

# 收集 PVC 上已有 profile 名（含刚 reconcile 的），用于合并 PROFILES_JSON 入参
EXISTING_NAMES_JSON=$(python3 -c "
import json, os
base = '$HERMES_DATA/profiles'
names = [d for d in os.listdir(base) if d != 'base' and os.path.isdir(os.path.join(base, d))]
print(json.dumps(names))
" 2>/dev/null || echo "[]")

# PROFILES_JSON 入参中目录还不存在的新 profile（动态创建路径下通常为 []）
NEW_NAMES_JSON="[]"
if [ -n "$PROFILES_JSON" ] && [ "$PROFILES_JSON" != "[]" ]; then
    NEW_NAMES_JSON=$(PROFILES_JSON="$PROFILES_JSON" EXISTING="$EXISTING_NAMES_JSON" python3 -c "
import json, os
incoming = json.loads(os.environ['PROFILES_JSON'])
existing = set(json.loads(os.environ['EXISTING']))
print(json.dumps([p['name'] for p in incoming if p.get('name') and p['name'] not in existing]))
" 2>/dev/null || echo "[]")
fi

# ── Step 3: Create new user profiles (dir not yet on PVC) ──
# 端口由 port_map.py alloc 分配（唯一真相），.env 端口由 port_map 派生写入。
if [ "$NEW_NAMES_JSON" != "[]" ]; then
    echo "$NEW_NAMES_JSON" | python3 -c "
import json, os, subprocess, sys
api_key = os.environ.get('API_SERVER_KEY','change-me')
for name in json.loads(sys.stdin.read()):
    subprocess.run(['hermes','profile','create',name,'--clone','--clone-from','base'], check=False)
    port = subprocess.run(['python3','/opt/scripts/port_map.py','alloc',name],
                          capture_output=True, text=True).stdout.strip()
    if not port:
        print(f'[v2] WARN: alloc port failed for {name}, skip .env', file=sys.stderr); continue
    with open(f'/opt/data/profiles/{name}/.env','w') as f:
        f.write(f'API_SERVER_ENABLED=true\nAPI_SERVER_KEY={api_key}\nAPI_SERVER_HOST=0.0.0.0\nAPI_SERVER_PORT={port}\nGATEWAY_ALLOW_ALL_USERS=true\n')
    print(f'[v2] Profile {name} created (port {port})')
" 2>/dev/null || true
    PORT_MAP_ALL=$(python3 /opt/scripts/port_map.py all)
fi

# ── Step 4: Generate nginx config (从 port_map.json 唯一真相) ──
echo "[v2] Generating nginx config..."

NGINX_UPSTREAMS="upstream base_profile { server 127.0.0.1:8643; }"
NGINX_MAP='    default "127.0.0.1:8643";'

# PORT_MAP_ALL: {"name": port, ...}；无端口的 profile 不生成条目（流量走 default base，绝不串到别的 profile）
echo "$PORT_MAP_ALL" | python3 -c "
import json, sys
m = json.loads(sys.stdin.read() or '{}')
for name, port in sorted(m.items(), key=lambda x: x[1]):
    safe = name.replace('-','_').replace('.','_')
    print(f'upstream profile_{safe} {{ server 127.0.0.1:{port}; }}')
" > /tmp/nginx-upstreams.txt 2>/dev/null || true

echo "$PORT_MAP_ALL" | python3 -c "
import json, sys
m = json.loads(sys.stdin.read() or '{}')
for name, port in sorted(m.items(), key=lambda x: x[1]):
    print(f'    \"{name}\" \"127.0.0.1:{port}\";')
" > /tmp/nginx-map.txt 2>/dev/null || true

if [ -s /tmp/nginx-upstreams.txt ]; then
    NGINX_UPSTREAMS="$NGINX_UPSTREAMS
$(cat /tmp/nginx-upstreams.txt)"
    NGINX_MAP="$NGINX_MAP
$(cat /tmp/nginx-map.txt)"
fi

cat > /etc/nginx/conf.d/hermes-profiles.conf <<NGINXEOF
map \$http_x_hermes_profile \$backend {
$NGINX_MAP
}

$NGINX_UPSTREAMS

server {
    listen 8642;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_set_header Origin "";
    proxy_set_header Referer "";

    location / {
        proxy_pass http://\$backend;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Hermes-Profile \$http_x_hermes_profile;
    }

    location /health {
        return 200 '{"status":"ok","service":"engine-nginx"}';
        add_header Content-Type application/json;
    }
}
NGINXEOF

echo "[v2] nginx config generated"

# ── Step 5: Start nginx ──
mkdir -p /run/nginx
nginx -g "daemon off;" &
NGINX_PID=$!
echo "[v2] nginx started (PID $NGINX_PID)"

# ── Step 6: Start profile gateways (端口从 port_map.json 取) ──
echo "[v2] Starting gateways..."

HERMES_HOME="$HERMES_DATA/profiles/base" hermes gateway run &>/tmp/gateway-base.log &
echo "  base: PID $! (8643)"

# 6.1 重建共享 skill 目录的补充组（PVC 持久，/etc/group 容器重启丢失）
# external_dirs 模型：/opt/data/skills/{definition_id}/ chown root:{gid} chmod 2750，
# GID 落在目录 stat 上。重启后按 st_gid 重建同名组，profile_isolation launch 时
# 据此把 profile UID 加组 → 降权后仍可读共享 skill。
if [ -d "$HERMES_DATA/skills" ]; then
    find "$HERMES_DATA/skills" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | while IFS= read -r _skdir; do
        _gid=$(stat -c '%g' "$_skdir" 2>/dev/null || stat -f '%g' "$_skdir" 2>/dev/null || echo 0)
        [ "$_gid" = "0" ] && continue
        _defid=$(basename "$_skdir")
        _gname="skills-$(python3 -c "import sys;d='$_defid';s=''.join(c if c.isalnum() else '-' for c in d).strip('-');s=s if s and s[0].isalpha() else 'd'+s;print(s[:24])")"
        groupadd -g "$_gid" -f "$_gname" 2>/dev/null || true
    done
fi

# 每个 profile 经 profile_isolation.py 启动：分配/恢复 uid + chown 0700 + 降权 gateway
# 端口取自 port_map.json（唯一真相），取不到则跳过（不串号）
# definition_id 取自 profile 目录的 .definition_id（manager 创建 profile 时写），
# 传给 profile_isolation 用于加入共享 skill 补充组（external_dirs 读权限）。
python3 /opt/scripts/port_map.py all | python3 -c "
import json, sys
m = json.loads(sys.stdin.read() or '{}')
for name, port in sorted(m.items(), key=lambda x: x[1]):
    print(f'{name}\t{port}')
" | while IFS=$'\t' read -r _name _port; do
    [ -n "$_name" ] && [ -n "$_port" ] || continue
    _defid=""
    if [ -f "/opt/data/profiles/$_name/.definition_id" ]; then
        _defid=$(cat "/opt/data/profiles/$_name/.definition_id" 2>/dev/null | tr -d '[:space:]')
    fi
    if [ -n "$_defid" ]; then
        python3 /opt/scripts/profile_isolation.py launch "$_name" "/opt/data/profiles/$_name" "$_port" "$_defid"
    else
        python3 /opt/scripts/profile_isolation.py launch "$_name" "/opt/data/profiles/$_name" "$_port"
    fi
    sleep 1
done

# ── Step 7: Register profiles to Controller (reconcile stale DB records) ──
if [ -n "$CONTROLLER_URL" ] && [ -n "$AGENT_ID" ]; then
    _profiles=$(ls "$HERMES_DATA/profiles/" 2>/dev/null | grep -v "^base$" | python3 -c "import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))" 2>/dev/null || echo "[]")
    curl -s -X POST "$CONTROLLER_URL/api/controller/profiles/register" \
        -H "Content-Type: application/json" \
        -d "{\"agent_id\":\"$AGENT_ID\",\"profiles\":$_profiles}" 2>/dev/null && echo "[v2] Registered profiles to controller" || true
    # Pod 启动时 reconcile skill secrets.enc（兜底 install/rebind/删 PVC 后缺失 → sidecar 404 → skill auth_fail）
    curl -s -X POST "$CONTROLLER_URL/api/controller/agents/$AGENT_ID/skills/secrets/reconcile" >/dev/null 2>&1 && echo "[v2] Reconciled skill secrets" || true
fi

# ── Step 8: Keep alive ──
echo "[v2] Container ready"
while kill -0 $NGINX_PID 2>/dev/null; do sleep 10; done
echo "[v2] nginx died, exiting"
exit 1
