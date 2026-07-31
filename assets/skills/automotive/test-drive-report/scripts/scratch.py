#!/usr/bin/env python3
"""scratch.py — profile 私有临时目录 helper（多租户隔离 + 留存清理）

本平台多租：每个 profile 独立 Linux UID，hermes gateway 以该 UID 降权启动，
环境变量 HERMES_HOME / HOME 指向 profile 私有目录 /opt/data/profiles/{pn}（0700，
随用户删除 `rm -rf` 自动清理）。skill 脚本的中间文件 / 详情缓存必须落该目录下的
.skill_tmp/，而不是共享 /tmp——否则：

  - /tmp 固定名文件跨租户碰撞（`>` 重定向 Permission denied / 读到别的租户旧数据）
  - /tmp 按 uid 命名也不安全：uid 删除后被新 profile 复用 → 读到旧租户数据
  - /tmp 不随用户删除清理 → 孤儿 PII 留存

skill_scratch() 返回本 profile 的 .skill_tmp/（0700），并在每次调用时 best-effort
清理超 TTL 的文件（bound 增长）。无 HERMES_HOME / HOME（异常上下文）→ 返回 None，
调用方降级不缓存（绝不回退共享 /tmp）。

留存 TTL 与 query_detail 的 10min「新鲜度 TTL」职责不同：后者管缓存可不可用（超时返
cache_missing 触发重取），本 TTL 管删不删（超时物理删除 bound 磁盘）。1h 足够覆盖
多轮深挖的短时停顿。
"""
import os
import time

# 留存 TTL（秒）：超过即物理删除。可用环境变量 UA_SKILL_TMP_TTL 覆盖。
SKILL_TMP_TTL = int(os.environ.get("UA_SKILL_TMP_TTL", "3600"))


def _sweep_expired(d):
    """best-effort 删除目录下 mtime 超 SKILL_TMP_TTL 的普通文件。任何异常吞掉。"""
    ttl = SKILL_TMP_TTL
    try:
        now = time.time()
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                if os.path.isfile(p) and (now - os.path.getmtime(p)) > ttl:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def skill_scratch():
    """返回本 profile 私有 scratch 目录（0700）。无 profile 上下文返回 None。

    每次调用都 best-effort 清理超 TTL 文件——靠脚本高频调用 bound .skill_tmp 增长，
    无需引入额外定时任务。profile / 用户删除时 /opt/data/profiles/{pn} 被 rm -rf，
    .skill_tmp 连带清掉（覆盖挂起 SUSPENDED 不跑脚本的场景）。
    """
    base = os.environ.get("HERMES_HOME") or os.environ.get("HOME")
    if not base:
        return None
    d = os.path.join(base, ".skill_tmp")
    try:
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o700)
    except OSError:
        return None
    _sweep_expired(d)
    return d
