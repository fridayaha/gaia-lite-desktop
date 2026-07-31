#!/usr/bin/env python3
"""一次性清理：删除所有 profile_type='SHARED' 的 AgentProfile 及其运行时资源。

背景：组共享 Profile（SHARED）已下线，全部收敛为 INDEPENDENT（USER 级独占）。本脚本
清除历史遗留的 SHARED profile 行 + 其运行时资源（gateway 进程 / profile 目录 / port_map /
nginx / browser Pod），并把 agent_instance_channels 里残留的 SHARED 渠道配置归一为
INDEPENDENT/USER。

清理路径：复用 manager 的 ``DELETE /api/controller/profiles/{profile_id}`` 端点 → 走完整
``delete_profile`` / ``teardown_profile`` 链路。``teardown_profile`` 按
``deployment.scope_type`` / ``scope_target_id`` + ``profile.profile_name`` 派发 k8s exec，
**不读 ``profile.user_id``** → 对组共享（``user_id=None``）的 SHARED profile 无需适配。

幂等：所有步骤可重跑。DELETE 对已删 profile 返回 404 跳过；UPDATE 仅作用于 SHARED 行。

用法（云服务器 ~/union_agent 下，kubectl 已连集群）：
  python3 scripts/cleanup_shared_profiles.py --dry-run        # 仅列出，不删
  python3 scripts/cleanup_shared_profiles.py                  # 执行（manager svc port-forward）
  python3 scripts/cleanup_shared_profiles.py --manager http://localhost:18000 --token XXX
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CI_DIR = os.path.join(os.path.dirname(__file__), "..", "deploy", "ci")
ENV_LOCAL = os.path.join(CI_DIR, ".env.local")
NS = os.environ.get("K8S_NAMESPACE", "unionagents")
MANAGER_PORT_FWD = 18000  # 本地 port-forward 端口


def load_env(path: str) -> dict:
    env = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def psql(env: dict, sql: str) -> str:
    cmd = [
        "psql", "-h", env["DB_HOST"], "-U", env["DB_USER"], "-d", env["DB_NAME"],
        "-t", "-A", "-F", "|", "-c", sql,
    ]
    e = dict(os.environ, PGPASSWORD=env["DB_PASSWORD"])
    return subprocess.run(cmd, env=e, capture_output=True, text=True, check=True).stdout


def query_shared_profiles(env: dict) -> list[dict]:
    out = psql(
        env,
        "SELECT id::text, instance_id::text, profile_name, "
        "COALESCE(user_id::text, ''), COALESCE(group_id::text, ''), "
        "COALESCE(deployment_id::text, '') "
        "FROM agent_profiles WHERE profile_type = 'SHARED' ORDER BY created_at;",
    )
    rows = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 6:
            rows.append({
                "id": parts[0], "instance_id": parts[1], "profile_name": parts[2],
                "user_id": parts[3] or None, "group_id": parts[4] or None,
                "deployment_id": parts[5] or None,
            })
    return rows


def start_port_forward() -> subprocess.Popen | None:
    """port-forward svc/manager 到本地 MANAGER_PORT_FWD。返回进程或 None。"""
    pf = subprocess.Popen(
        ["kubectl", "-n", NS, "port-forward", f"svc/manager", f"{MANAGER_PORT_FWD}:8002"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    # 等 port-forward 就绪
    for _ in range(30):
        if pf.poll() is not None:
            return None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{MANAGER_PORT_FWD}/healthz", timeout=1) as r:
                if r.status == 200:
                    return pf
        except Exception:
            time.sleep(0.5)
    return pf  # 即便 healthz 没通也返回，由调用方 DELETE 时暴露错误


def delete_profile(manager_url: str, token: str | None, profile_id: str) -> tuple[int, str]:
    url = f"{manager_url.rstrip('/')}/api/controller/profiles/{profile_id}"
    req = urllib.request.Request(url, method="DELETE")
    if token:
        req.add_header("X-Internal-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode(errors="replace")[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]
    except Exception as e:
        return -1, str(e)


def normalize_channels(env: dict) -> int:
    """归一 agent_instance_channels: SHARED → INDEPENDENT/USER。返回受影响行数。"""
    out = psql(
        env,
        "UPDATE agent_instance_channels "
        "SET profile_type = 'INDEPENDENT', scope_type = 'USER', scope_target_id = NULL "
        "WHERE profile_type = 'SHARED' RETURNING id::text;",
    )
    return len([l for l in out.splitlines() if l.strip()])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅列出，不执行删除")
    ap.add_argument("--manager", default=None, help="manager base URL（不给则 port-forward svc/manager）")
    ap.add_argument("--token", default=None, help="X-Internal-Token（默认读 .env.local 的 UA_INTERNAL_TOKEN）")
    args = ap.parse_args()

    env = load_env(ENV_LOCAL)
    for k in ("DB_HOST", "DB_USER", "DB_NAME", "DB_PASSWORD"):
        if k not in env:
            sys.exit(f"missing {k} in {ENV_LOCAL}")
    token = args.token or env.get("UA_INTERNAL_TOKEN") or None

    rows = query_shared_profiles(env)
    total = len(rows)
    null_user = sum(1 for r in rows if not r["user_id"])
    by_inst: dict[str, int] = {}
    for r in rows:
        by_inst[r["instance_id"][:8]] = by_inst.get(r["instance_id"][:8], 0) + 1

    print(f"[shared profiles] 共 {total} 条（user_id IS NULL 真·组共享 {null_user} / 带 user_id {total - null_user}）")
    for inst, n in by_inst.items():
        print(f"    instance {inst}: {n} 条")
    for r in rows[:20]:
        print(f"    - {r['id']}  profile={r['profile_name'][:24]}  user={r['user_id'] or '-'}  inst={r['instance_id'][:8]}")
    if total > 20:
        print(f"    ... 其余 {total - 20} 条略")

    # 归一渠道配置（即便 dry-run 也展示，但 dry-run 不执行）
    ch_shared = psql(env, "SELECT count(*) FROM agent_instance_channels WHERE profile_type = 'SHARED';").strip()
    print(f"[shared channels] {ch_shared} 条 SHARED 渠道配置待归一")

    if args.dry_run:
        print("\n[dry-run] 未执行删除/归一。去掉 --dry-run 执行。")
        return

    if total == 0:
        print("\n[skip] 无 SHARED profile 需删除。")

    # 1. 删除 SHARED profile（复用 manager DELETE 端点 → teardown_profile 全链路清理）
    manager_url = args.manager or f"http://127.0.0.1:{MANAGER_PORT_FWD}"
    pf = None
    if not args.manager:
        print(f"\n[port-forward] kubectl -n {NS} port-forward svc/manager {MANAGER_PORT_FWD}:8002 ...")
        pf = start_port_forward()
        if pf is None or pf.poll() is not None:
            sys.exit("port-forward 启动失败，请用 --manager 指定已可达的 manager URL")

    failures: list[tuple[str, int, str]] = []
    for r in rows:
        code, body = delete_profile(manager_url, token, r["id"])
        if code == 404:
            print(f"  [skip] {r['id']} (404 已删)")
        elif 200 <= code < 300:
            print(f"  [ok]   {r['id']}  {r['profile_name'][:24]}")
        else:
            print(f"  [fail] {r['id']}  HTTP {code}  {body}")
            failures.append((r["id"], code, body))

    if pf is not None:
        pf.terminate()
        try:
            pf.wait(timeout=5)
        except Exception:
            pf.kill()

    # 2. 归一渠道配置
    n = normalize_channels(env)
    print(f"\n[normalize] agent_instance_channels SHARED→INDEPENDENT: {n} 行")

    # 3. 复查
    residual = psql(env, "SELECT count(*) FROM agent_profiles WHERE profile_type = 'SHARED';").strip()
    print(f"\n[verify] 残留 SHARED profile: {residual}")
    if int(residual or 0) > 0:
        print("[warn] 仍有残留，建议重跑本脚本（DELETE 失败的行见上方 [fail]）")
    if failures:
        print(f"\n[failures] {len(failures)} 条删除失败，详见上方日志")
        sys.exit(1)


if __name__ == "__main__":
    main()
