#!/usr/bin/env python3
"""Per-profile UID isolation for V2 engine pods.

从 main 分支 engines/hermes/orchestrator.py（提交 234177a/a97905c）移植的 UID 隔离算法：
  _profile_username / _ensure_profile_user / _chown_profile_dir / _drop_privs

每个 profile 分配独立 Linux UID（20000-29999），目录 chown -R {uid}:{uid} + chmod 0700，
hermes gateway 子进程通过 preexec_fn 降权到该 UID（os.setgid/os.setuid，不依赖 su/gosu/s6）。

UID 真相源 = 目录属主 UID：容器重启后 /etc/passwd 丢失，但 PVC 上目录属主持久，据此
用原 UID 重建同名用户。

CLI（由 entrypoint-v2.sh 与 manager 经 k8s exec 调用）：
  python3 profile_isolation.py launch  <name> <dir> <port>
  python3 profile_isolation.py cleanup <name>
"""

from __future__ import annotations

import os
import pwd
import subprocess
import sys

# per-profile uid 池（与 main 一致；env 可覆盖）
HERMES_UID_MIN = int(os.environ.get("HERMES_UID_MIN", "20000"))
HERMES_UID_MAX = int(os.environ.get("HERMES_UID_MAX", "29999"))

# hermes 程序目录（构建期 Dockerfile 已 chmod -R o+rX，profile uid 可读可执行）
HERMES_HOME_DEFAULT = "/opt/data"

# 共享 skill 目录根（external_dirs 模型：每 definition 一个子目录，同 definition 的
# 所有 profile 经补充 GID 共享读权限）。profile_isolation 负责把 profile UID 加进
# 该 definition 的 skill 组，gateway 降权后仍持该补充组 → 可读共享 skill 文件。
SHARED_SKILLS_ROOT = "/opt/data/skills"


def _profile_username(profile_name: str) -> str:
    """profile_name → Linux 用户名（useradd 约束：字母开头，最长 32 字符）。"""
    safe = "".join(c if c.isalnum() else "-" for c in profile_name).strip("-")
    if not safe or not safe[0].isalpha():
        safe = "p" + safe
    return f"hermes-{safe[:25]}"


def _ensure_profile_user(profile_name: str, profile_dir: str) -> int:
    """为 profile 创建/恢复 Linux 用户，返回 uid。

    以"目录属主 uid"为 source of truth：
      - 目录已存在且属主非 root → 用原 uid 在 /etc/passwd 重建同名用户（容器重启后丢失）
      - 目录不存在或属主是 root → useradd 从池中分配新 uid
    """
    username = _profile_username(profile_name)

    existing_uid: int | None = None
    if os.path.exists(profile_dir):
        st = os.stat(profile_dir)
        if st.st_uid != 0:  # 非 root 属主，说明之前分配过 uid
            existing_uid = st.st_uid

    if existing_uid is not None:
        try:
            pwd.getpwuid(existing_uid)
            return existing_uid
        except KeyError:
            # /etc/passwd 丢失该 uid，用原 uid 重建同名用户
            result = subprocess.run(
                ["useradd", "-r", "-M", "-u", str(existing_uid), "-d", profile_dir, username],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return existing_uid
            # useradd 失败（uid 已被占用等），退化用目录属主 uid，setuid 仍可工作
            return existing_uid

    # 目录不存在或属主是 root，分配新 uid
    used_uids: set[int] = set()
    try:
        with open("/etc/passwd") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 3:
                    try:
                        used_uids.add(int(parts[2]))
                    except ValueError:
                        pass
    except Exception:
        pass

    for uid in range(HERMES_UID_MIN, HERMES_UID_MAX + 1):
        if uid in used_uids:
            continue
        result = subprocess.run(
            ["useradd", "-r", "-M", "-u", str(uid), "-d", profile_dir, username],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return uid
        # 失败可能是 uid 冲突，继续尝试下一个

    raise RuntimeError(
        f"No available uid in [{HERMES_UID_MIN}, {HERMES_UID_MAX}] for {profile_name}"
    )


def _chown_profile_dir(uid: int, profile_dir: str) -> None:
    """chown -R {uid}:{uid} + chmod 0700（dir + logs/）。

    无条件建 logs/ 并设权限，避免后续 log 写入导致属主错乱。
    """
    if not os.path.exists(profile_dir):
        return
    log_dir = os.path.join(profile_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    subprocess.run(
        ["chown", "-R", f"{uid}:{uid}", profile_dir],
        capture_output=True,
        timeout=60,
    )
    os.chmod(profile_dir, 0o700)
    os.chmod(log_dir, 0o700)


def _harden_secrets(definition_id: str | None) -> None:
    """把共享 skill 目录下的 secrets.enc 收归 root:root 0640（防御性兜底）。

    external_dirs 模型：secrets.enc 落 /opt/data/skills/{definition_id}/{skill}/secrets.enc，
    不在 profile 目录内（_chown_profile_dir 不动它），写入时已 chmod 0640（router.py）。
    此处 launch 时再扫一遍修正任何权限漂移，确保 gateway（非 root uid）读不到、sidecar（root）
    可读。definition_id 为 None（无 skill 的 profile）或共享目录不存在时跳过。
    """
    if not definition_id:
        return
    skill_dir = os.path.join(SHARED_SKILLS_ROOT, definition_id)
    if not os.path.isdir(skill_dir):
        return
    subprocess.run(
        [
            "find",
            skill_dir,
            "-name",
            "secrets.enc",
            "-exec",
            "chown",
            "root:root",
            "{}",
            "+",
            "-exec",
            "chmod",
            "640",
            "{}",
            "+",
        ],
        capture_output=True,
        timeout=30,
    )


def _skill_group_name(definition_id: str) -> str:
    """definition_id → Linux 组名（groupadd 约束：字母开头，最长 32 字符）。"""
    safe = "".join(c if c.isalnum() else "-" for c in definition_id).strip("-")
    if not safe or not safe[0].isalpha():
        safe = "d" + safe
    return f"skills-{safe[:24]}"


def _ensure_skill_group(definition_id: str) -> int | None:
    """确保 definition 的共享 skill 目录可被本 profile UID 读：返回补充 GID。

    共享目录 `/opt/data/skills/{definition_id}/` 由 manager 在装 skill 时创建
    （chown root:{gid} chmod 2750）。GID 持久化在目录 stat 上（PVC）。
    - 目录存在 → 读 st_gid → groupadd -g {gid} -f（幂等，重启后 /etc/group 丢失重建）→ 返回 gid
    - 目录不存在 → 返回 None（该 definition 尚无 skill，无需加组）

    返回的 gid 由 launch 传给 _drop_privs 作为补充组保留。
    """
    skill_dir = os.path.join(SHARED_SKILLS_ROOT, definition_id)
    if not os.path.isdir(skill_dir):
        return None
    st = os.stat(skill_dir)
    gid = st.st_gid
    if gid == 0:
        # 目录属 root 组（manager 尚未 chown 到 skill 组）→ 无组可加
        return None
    # 重建组（/etc/group 容器重启丢失，按 PVC 上的 st_gid 恢复）
    subprocess.run(
        ["groupadd", "-g", str(gid), "-f", _skill_group_name(definition_id)],
        capture_output=True,
        timeout=10,
    )
    return gid


def _ensure_group_membership(username: str, gid: int) -> None:
    """把 profile 用户加入 skill 补充组（幂等）。"""
    subprocess.run(
        ["usermod", "-aG", str(gid), username],
        capture_output=True,
        timeout=10,
    )


def _drop_privs(uid: int, skill_gid: int | None = None):
    """返回 preexec_fn：设补充组 + setgid + setuid + setsid。

    setgroups([skill_gid]) 保留 skill 补充组（共享 skill 目录读权限），若不传则
    setgroups([]) 清空（兼容无 skill 场景）；setgid(uid) 主组 = profile uid（home 0700
    访问）；setuid(uid) 降权；setsid() 脱离 exec shell 会话组，shell 退出不 SIGHUP。
    """

    def _drop() -> None:
        os.setgroups([skill_gid] if skill_gid else [])
        os.setgid(uid)
        os.setuid(uid)
        os.setsid()

    return _drop


def _sync_config_api_port(profile_dir: str, port: int) -> None:
    """强制 config.yaml 的 platforms.api_server.port + .env 的 API_SERVER_PORT = 传入 port（来自 port_map.json 单源）。

    hermes gateway 启动时读 config.yaml 的 platforms.api_server.port 与 .env 的
    API_SERVER_PORT——**两者必须一致**，不一致会在启动时 hang（实测 admin .env 残留
    8645、config.yaml 被同步成 8647 → 冲突 → gateway 卡在 raft 后绑端口前）。
    若只同步 config.yaml 不同步 .env，会制造 .env↔config.yaml 端口冲突 → gateway hang。
    另外 config.yaml 残留旧 port 会让 gateway 绑旧端口 → 与 port_map 不一致 → 端口冲突
    抢占别的 profile → 跨用户数据泄漏。launch 时强制两者都写为传入 port（port_map 单源），
    堵住漂移 + 冲突窗口。

    best-effort：文件不存在或写失败都不阻断 launch。须在 _chown_profile_dir 前调，
    chown 会把更新后的 config.yaml/.env 一起改属主给 profile uid。
    """
    # 1. config.yaml: platforms.api_server.port
    cfg_path = os.path.join(profile_dir, "config.yaml")
    if os.path.exists(cfg_path):
        try:
            import yaml  # hermes-agent 依赖 PyYAML，镜像内必有

            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            platforms = cfg.setdefault("platforms", {})
            if isinstance(platforms, dict):
                api_server = platforms.setdefault("api_server", {})
                if isinstance(api_server, dict):
                    api_server["port"] = int(port)
                else:
                    platforms["api_server"] = {"port": int(port)}
                with open(cfg_path, "w") as f:
                    yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except Exception:
            pass  # 写失败不阻断 launch
    # 2. .env: API_SERVER_PORT（必须与 config.yaml 一致，否则 hermes 启动 hang）
    env_path = os.path.join(profile_dir, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path) as f:
                lines = f.readlines()
            found = False
            for i, ln in enumerate(lines):
                if ln.startswith("API_SERVER_PORT="):
                    lines[i] = f"API_SERVER_PORT={port}\n"
                    found = True
                    break
            if not found:
                lines.append(f"API_SERVER_PORT={port}\n")
            with open(env_path, "w") as f:
                f.writelines(lines)
        except Exception:
            pass  # 写失败不阻断 launch


def _clear_stale_gateway_lock(profile_dir: str) -> None:
    """清理 stale gateway.lock（持有者 PID 已死）。

    Pod 重建时旧 gateway 进程被杀，但 gateway.lock 文件在 PVC 残留（记录死进程 PID +
    start_time）。hermes 的 runtime lock 检查没识别出持有者已死，新 gateway 报
    "Gateway runtime lock is already held by another instance. Exiting." → 每次引擎
    Pod 滚动更新 gateway 都起不来。launch 前清掉 stale 锁（持有者 PID 已死，或 PID 被
    复用致 start_time 不匹配），让新 gateway 正常启动。

    判活用 os.kill(pid, 0)（signal 0，aegis 不拦，跨 uid 可靠）。start_time 取
    /proc/<pid>/stat 第 22 字段（与 hermes 写 lock 时一致），PID 复用则 start_time 变。
    best-effort：解析失败/读不到不阻断 launch（保守不动锁）。
    """
    lock_path = os.path.join(profile_dir, "gateway.lock")
    if not os.path.exists(lock_path):
        return
    try:
        import json

        with open(lock_path) as f:
            rec = json.load(f) or {}
        pid = int(rec.get("pid", 0) or 0)
        if pid <= 0:
            os.remove(lock_path)  # 无有效 pid → stale
            return
        # signal 0 探活（aegis 不拦）
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            os.remove(lock_path)  # pid 已死 → stale 锁，清掉
            return
        except PermissionError:
            return  # 存活但无权限探测（异常情况），保守不动
        # pid 存活 → 校验 start_time 防 PID 复用
        recorded_st = rec.get("start_time")
        if recorded_st is None:
            return  # 无 start_time 可校验，保守不动
        try:
            with open(f"/proc/{pid}/stat") as sf:
                cur_st = int(sf.read().split()[21])  # 第 22 字段（comm 无空格时正确）
            if cur_st != int(recorded_st):
                os.remove(lock_path)  # PID 复用 → stale 锁，清掉
        except (FileNotFoundError, ValueError, IndexError, OSError):
            pass  # /proc 读不到或解析失败，保守不动
    except Exception:
        pass  # best-effort，不阻断 launch


def launch(profile_name: str, profile_dir: str, port: int, definition_id: str | None = None) -> int:
    """建用户 + 隔离目录 + 以 profile uid 启动 hermes gateway（detached）。

    幂等：目录属主 truth → 恢复/分配 uid → chown → 加固 secrets → 降权 Popen。
    definition_id 用于把 profile UID 加入共享 skill 目录的补充组（external_dirs 模型），
    传 None 时退化为旧行为（无共享 skill 读权限，向后兼容）。
    本进程 Popen 后立即返回（gateway 靠 setsid 存活，日志写 /tmp/gateway-{name}.log）。
    """
    uid = _ensure_profile_user(profile_name, profile_dir)
    _sync_config_api_port(profile_dir, port)  # chown 前写，chown 一并改属主给 profile uid
    _clear_stale_gateway_lock(profile_dir)  # chown 前清 stale 锁，避免 hermes 误判 lock held 退出
    _chown_profile_dir(uid, profile_dir)
    _harden_secrets(definition_id)

    # 共享 skill 组：若该 definition 已有共享 skill 目录，把 profile UID 加进组，
    # 降权后保留该补充组 → gateway 可读 /opt/data/skills/{definition_id}/。
    skill_gid = None
    if definition_id:
        skill_gid = _ensure_skill_group(definition_id)
        if skill_gid is not None:
            _ensure_group_membership(_profile_username(profile_name), skill_gid)

    env = os.environ.copy()
    env["HERMES_HOME"] = profile_dir
    env["HOME"] = profile_dir  # 让 hermes 写 ~/.hermes 到 profile 目录（uid 可写）
    env["API_SERVER_PORT"] = str(port)
    env.setdefault("API_SERVER_ENABLED", "true")
    env.setdefault("GATEWAY_ALLOW_ALL_USERS", "true")

    log_path = f"/tmp/gateway-{profile_name}.log"
    # root 打开日志文件，子进程继承 fd 写入，绕过文件属主问题
    logf = open(log_path, "w")
    proc = subprocess.Popen(
        ["hermes", "gateway", "run", "--replace"],
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
        preexec_fn=_drop_privs(uid, skill_gid),
    )
    # 记录 gateway PID 供 teardown kill（gateway 靠 setsid 存活，proc.pid 即会话组长）。
    # 不写则 teardown 的 `kill $(cat gateway.pid)` 取不到 PID → gateway 成孤儿占端口，
    # 已删用户仍可经此孤儿 gateway 请求（profile 漂移 + 越权）。
    try:
        with open(os.path.join(profile_dir, "gateway.pid"), "w") as pf:
            pf.write(str(proc.pid))
    except Exception:
        pass
    print(f"launched profile {profile_name} uid={uid} port={port} pid={proc.pid} log={log_path}")
    return uid


def cleanup(profile_name: str) -> None:
    """删除 profile 的 Linux 用户（kill/rm 由 manager 负责）。"""
    username = _profile_username(profile_name)
    subprocess.run(["userdel", username], capture_output=True, timeout=10)
    print(f"cleaned up user {username}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: profile_isolation.py {launch|cleanup} ...", file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "launch":
        if len(argv) not in (5, 6):
            print(
                "usage: profile_isolation.py launch <name> <dir> <port> [definition_id]",
                file=sys.stderr,
            )
            return 2
        definition_id = argv[5] if len(argv) == 6 else None
        launch(argv[2], argv[3], int(argv[4]), definition_id)
        return 0
    if cmd == "cleanup":
        if len(argv) != 3:
            print("usage: profile_isolation.py cleanup <name>", file=sys.stderr)
            return 2
        cleanup(argv[2])
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
