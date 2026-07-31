#!/usr/bin/env python3
"""一次性迁移：profile 目录 7555a4(USER_GROUP+group) → cfd2a9(USER+user) 重命名。

背景：INDEPENDENT 改动后 profile_name hash 从 7555a4 变 cfd2a9，旧 7555a4 目录里
有用户会话数据（state.db/sessions/memories）不能删。此脚本把旧目录按 user_uuid
算新 hash 重命名，保留全部数据；DB 的 agent_profiles/internal_port_map 已被污染，
直接清空让新版 ensure 重建（--clone 对已存在目录报错被忽略，不覆盖 state.db）。

用法（云服务器 ~/union_agent 下）：
  python3 scripts/migrate_profile_hash.py --dry-run   # 先看计划
  python3 scripts/migrate_profile_hash.py             # 执行
"""
from __future__ import annotations
import argparse
import hashlib
import os
import subprocess
import sys
import json

CI_DIR = os.path.join(os.path.dirname(__file__), "..", "deploy", "ci")
ENV_LOCAL = os.path.join(CI_DIR, ".env.local")
NS = os.environ.get("K8S_NAMESPACE", "unionagents")


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


def new_hash(user_uuid: str) -> str:
    return hashlib.sha256(f"USER:{user_uuid}".encode()).hexdigest()[:6]


def psql(env: dict, sql: str) -> str:
    cmd = ["psql", "-h", env["DB_HOST"], "-U", env["DB_USER"], "-d", env["DB_NAME"], "-t", "-A", "-F", "|", "-c", sql]
    e = dict(os.environ, PGPASSWORD=env["DB_PASSWORD"])
    return subprocess.run(cmd, env=e, capture_output=True, text=True, check=True).stdout


def kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", "-n", NS, *args], capture_output=True, text=True).stdout


def list_engine_pods() -> list[str]:
    out = subprocess.run(["kubectl", "-n", NS, "get", "pods", "-o", "name"],
                         capture_output=True, text=True, check=True).stdout
    return [l.replace("pod/", "") for l in out.splitlines() if "engine-hermes" in l]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    env = load_env(ENV_LOCAL)
    for k in ("DB_HOST", "DB_USER", "DB_NAME", "DB_PASSWORD"):
        if k not in env:
            sys.exit(f"missing {k} in {ENV_LOCAL}")

    # 1. user_short(8) -> new_hash
    rows = psql(env, "SELECT id::text FROM users;")
    user_map: dict[str, str] = {}  # short -> new_hash
    for line in rows.splitlines():
        uid = line.strip()
        if uid:
            user_map[uid.replace("-", "")[:8]] = new_hash(uid)
    print(f"[users] {len(user_map)} users -> hash map")
    for s, h in user_map.items():
        print(f"    {s} -> {h}")

    # 2. 各 pod 重命名 7555a4 目录 -> 新 hash 目录
    pods = list_engine_pods()
    print(f"\n[pods] {len(pods)} engine pods")
    rename_plan: list[tuple[str, str, str, str]] = []  # pod, old, new, stale_to_delete
    for pod in pods:
        dirs = kubectl("exec", pod, "--", "sh", "-c",
                       "ls /opt/data/profiles/ 2>/dev/null").split()
        for d in dirs:
            if d == "base" or "-" not in d:
                continue
            parts = d.split("-")
            if len(parts) != 3:
                continue
            agent, middle, user_short = parts
            if middle == "7555a4" and user_short in user_map:
                new_middle = user_map[user_short]
                new_name = f"{agent}-{new_middle}-{user_short}"
                stale = new_name if new_name in dirs and new_name != d else None
                rename_plan.append((pod, d, new_name, stale))

    print(f"\n[rename] {len(rename_plan)} dirs to migrate:")
    for pod, old, new, stale in rename_plan:
        extra = f" (delete stale {stale} first)" if stale else ""
        print(f"    {pod}: {old} -> {new}{extra}")

    if args.dry_run:
        print("\n[DRY RUN] DB: would DELETE agent_profiles + reset internal_port_map, then restart engine pods.")
        print("[DRY RUN] no changes made.")
        return

    # 3. 执行重命名
    for pod, old, new, stale in rename_plan:
        if stale:
            print(f"  rm stale {pod}:{stale}")
            subprocess.run(["kubectl", "-n", NS, "exec", pod, "--", "rm", "-rf",
                            f"/opt/data/profiles/{stale}"], check=False)
        print(f"  mv {pod}:{old} -> {new}")
        subprocess.run(["kubectl", "-n", NS, "exec", pod, "--", "mv",
                        f"/opt/data/profiles/{old}", f"/opt/data/profiles/{new}"], check=True)

    # 4. DB 清空（port_map 已污染，让 ensure 重建）
    print("\n[DB] DELETE agent_profiles + reset internal_port_map ...")
    sql = (
        "BEGIN; "
        "DELETE FROM agent_profiles; "
        "UPDATE agent_deployments SET internal_port_map = '{\"profiles\":{},\"next_port\":8644}'::jsonb; "
        "COMMIT;"
    )
    print(psql(env, sql))

    # 5. 重启 engine pod（清旧 gateway 进程 + nginx 干净重建）
    print("\n[restart] deleting engine pods to rebuild ...")
    for pod in pods:
        print(f"  kubectl delete pod {pod}")
        subprocess.run(["kubectl", "-n", NS, "delete", "pod", pod, "--ignore-not-found"], check=False)

    print("\n[done] 首条消息会触发 ensure 重建 cfd2a9 profile（state.db 保留在重命名后的目录）。")


if __name__ == "__main__":
    main()
