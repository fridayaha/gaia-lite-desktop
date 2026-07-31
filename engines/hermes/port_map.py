#!/usr/bin/env python3
"""Pod 内 profile→port 唯一真相管理（port_map.json）。

设计动机：原本 nginx 的 profile→port 映射有两个独立写入源（manager 从 DB
internal_port_map 生成 / entrypoint 从每个 profile .env 读 API_SERVER_PORT），
两者漂移 + entrypoint 读不到端口默认 8644 → 多 profile 塌缩同一端口 → 用户串号。
现以 PVC 持久化的 port_map.json 为唯一真相，entrypoint 与 manager 经此模块原子读写，
保证每个 profile 拥有唯一端口（gateway 启动失败仅 502，绝不串到别的 profile）。

端口分配用扫描法（[MIN,MAX] 中第一个未占用端口），无 next_port 计数器——
天然回收已删 profile 释放的端口，避免计数器漂移 / 端口泄漏爬向 MAX。

并发：flock(port_map.json.lock) 排他锁序列化跨进程读写（entrypoint + 多个 k8s exec
共享同一锁文件）。原子写：tmp 文件 + fsync + os.replace。

CLI（由 entrypoint-v2.sh 与 manager 经 k8s exec 调用）：
  python3 port_map.py alloc <name>            分配（幂等），打印端口
  python3 port_map.py get <name>              查询，打印端口或空
  python3 port_map.py set <name> <port>       显式设置（不推进，迁移/对齐用）
  python3 port_map.py remove <name>           删除
  python3 port_map.py all                     打印 {name: port} JSON
  python3 port_map.py reconcile               扫描 PVC 目录对齐，打印最终 JSON
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys

HERMES_DATA = os.environ.get("HERMES_HOME", "/opt/data")
PORT_MAP_PATH = os.environ.get("PORT_MAP_PATH", os.path.join(HERMES_DATA, "port_map.json"))
LOCK_PATH = PORT_MAP_PATH + ".lock"
PORT_MIN = int(os.environ.get("HERMES_PORT_MIN", "8644"))
PORT_MAX = int(os.environ.get("HERMES_PORT_MAX", "8699"))
PROFILES_DIR = os.path.join(HERMES_DATA, "profiles")


class PortExhausted(Exception):
    """[PORT_MIN, PORT_MAX] 区间端口已耗尽。"""


def _empty() -> dict:
    return {"version": 1, "profiles": {}}


def _load() -> dict:
    """读 port_map.json；缺失/损坏返回空骨架（不抛，调用方决定是否 reconcile）。"""
    try:
        with open(PORT_MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        return _empty()
    # 仅保留合法的 name->int 映射，丢弃脏数据
    profiles = {str(k): int(v) for k, v in data["profiles"].items() if isinstance(v, (int, str))}
    return {"version": 1, "profiles": profiles}


def _save_atomic(data: dict) -> None:
    """原子写：同目录 tmp + fsync + os.replace（同文件系统原子 rename）。"""
    os.makedirs(os.path.dirname(PORT_MAP_PATH) or ".", exist_ok=True)
    tmp = f"{PORT_MAP_PATH}.tmp.{os.getpid()}"
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, PORT_MAP_PATH)


@contextlib.contextmanager
def _flock_write():
    """排他锁，序列化跨进程读写。锁文件持久化在同目录。"""
    os.makedirs(os.path.dirname(LOCK_PATH) or ".", exist_ok=True)
    with open(LOCK_PATH, "a", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _pick_free_port(profiles: dict) -> int:
    used = set(profiles.values())
    for p in range(PORT_MIN, PORT_MAX + 1):
        if p not in used:
            return p
    raise PortExhausted(f"no free port in [{PORT_MIN},{PORT_MAX}]")


def alloc(name: str) -> int:
    """幂等分配：已存在返回原端口；否则扫描取第一个未用端口写入。"""
    with _flock_write():
        data = _load()
        profiles = data["profiles"]
        if name in profiles:
            return profiles[name]
        port = _pick_free_port(profiles)
        profiles[name] = port
        _save_atomic({"version": 1, "profiles": profiles})
        return port


def get(name: str) -> int | None:
    data = _load()
    return data["profiles"].get(name)


def set_port(name: str, port: int) -> None:
    """显式设置端口（不扫描，迁移/对齐用）。"""
    with _flock_write():
        data = _load()
        profiles = data["profiles"]
        profiles[name] = int(port)
        _save_atomic({"version": 1, "profiles": profiles})


def remove(name: str) -> None:
    with _flock_write():
        data = _load()
        profiles = data["profiles"]
        if name in profiles:
            del profiles[name]
            _save_atomic({"version": 1, "profiles": profiles})


def all_profiles() -> dict[str, int]:
    return _load()["profiles"]


def reconcile_from_disk() -> dict[str, int]:
    """扫描 $HERMES_DATA/profiles/*/（排除 base）对齐 port_map.json：
    - 孤儿目录（目录在但不在 map）→ alloc 新端口
    - map 有但目录不存在 → remove 条目
    缺失/损坏的 port_map.json 也走此路径（_load 返回空→所有目录分配新端口）。
    返回最终 {name: port}。
    """
    with _flock_write():
        data = _load()
        profiles = data["profiles"]
        # 收集 PVC 上实际存在的 profile 目录
        on_disk = set()
        if os.path.isdir(PROFILES_DIR):
            for entry in os.listdir(PROFILES_DIR):
                if entry == "base":
                    continue
                if os.path.isdir(os.path.join(PROFILES_DIR, entry)):
                    on_disk.add(entry)
        # 删 stale 条目（map 有但目录没了）
        for name in list(profiles.keys()):
            if name not in on_disk:
                del profiles[name]
        # 补孤儿目录
        for name in on_disk:
            if name not in profiles:
                profiles[name] = _pick_free_port(profiles)
        _save_atomic({"version": 1, "profiles": profiles})
        return profiles


def _cli(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: port_map.py {alloc|get|set|remove|all|reconcile} ...", file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "alloc":
        if len(argv) < 3:
            print("usage: port_map.py alloc <name>", file=sys.stderr)
            return 2
        print(alloc(argv[2]))
    elif cmd == "get":
        if len(argv) < 3:
            print("usage: port_map.py get <name>", file=sys.stderr)
            return 2
        p = get(argv[2])
        if p is not None:
            print(p)
    elif cmd == "set":
        if len(argv) < 4:
            print("usage: port_map.py set <name> <port>", file=sys.stderr)
            return 2
        set_port(argv[2], int(argv[3]))
    elif cmd == "remove":
        if len(argv) < 3:
            print("usage: port_map.py remove <name>", file=sys.stderr)
            return 2
        remove(argv[2])
    elif cmd == "all":
        print(json.dumps(all_profiles(), ensure_ascii=False))
    elif cmd == "reconcile":
        print(json.dumps(reconcile_from_disk(), ensure_ascii=False))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
