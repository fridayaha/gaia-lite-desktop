"""profile_isolation.py 单测：per-profile UID 隔离算法（移植自 main orchestrator.py）。

mock subprocess / pwd / os.stat / open，不依赖真实 /etc/passwd 或 root 权限。
验证：用户名 sanitize、目录属主 truth 恢复 uid、空闲 uid 分配、池耗尽、chown/chmod 0700、
secrets.enc 加固、launch 带 preexec_fn 降权。
"""

import os
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

# profile_isolation 是 engines/hermes/ 下的独立脚本（非包内模块），加入 sys.path 导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import profile_isolation  # noqa: E402


def test_profile_username_sanitization():
    assert profile_isolation._profile_username("alice") == "hermes-alice"
    # 含 - 的 profile 名（实际命名 agent_short-scope_hash-user_short）
    assert (
        profile_isolation._profile_username("d38e436e-f664d6-39c9e118")
        == "hermes-d38e436e-f664d6-39c9e118"
    )
    # 数字开头 → 补 p（useradd 要求字母开头）
    assert profile_isolation._profile_username("123abc") == "hermes-p123abc"
    # 超长截断到 25（hermes- 前缀 + 25 = 32，useradd 上限）
    uname = profile_isolation._profile_username("a" * 100)
    assert len(uname) <= 32
    assert uname == "hermes-" + "a" * 25


def test_ensure_profile_user_recovers_from_dir_owner():
    """目录已存在、属主非 root、/etc/passwd 无该 uid → 用原 uid 重建用户。"""
    st = MagicMock()
    st.st_uid = 20005
    with (
        patch("profile_isolation.os.stat", return_value=st),
        patch("profile_isolation.os.path.exists", return_value=True),
        patch("profile_isolation.pwd.getpwuid", side_effect=KeyError),
        patch("profile_isolation.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        uid = profile_isolation._ensure_profile_user("alice", "/opt/data/profiles/alice")
    assert uid == 20005
    args = mock_run.call_args.args[0]
    assert args[:3] == ["useradd", "-r", "-M"]
    assert "-u" in args and "20005" in args


def test_ensure_profile_user_allocates_new_uid_skipping_used():
    """目录属主 root → 扫 /etc/passwd，从 20000 起跳过已用，分配首个空闲 uid。"""
    st = MagicMock()
    st.st_uid = 0
    passwd = (
        "root:x:0:0:root:/root:/bin/sh\n"
        "hermes:x:10000:10000::/opt/hermes:/bin/sh\n"
        "u:x:20000:20000::/tmp:/bin/sh\n"
    )
    with (
        patch("profile_isolation.os.stat", return_value=st),
        patch("profile_isolation.os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=passwd)),
        patch("profile_isolation.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        uid = profile_isolation._ensure_profile_user("bob", "/opt/data/profiles/bob")
    # 20000 已用 → 分配 20001
    assert uid == 20001
    assert "20001" in mock_run.call_args.args[0]


def test_ensure_profile_user_pool_exhausted_raises():
    """池内 uid 全占用且 useradd 恒失败 → RuntimeError。"""
    st = MagicMock()
    st.st_uid = 0
    passwd = "u0:x:20000:20000::\nu1:x:20001:20001::\n"
    with (
        patch("profile_isolation.os.stat", return_value=st),
        patch("profile_isolation.os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=passwd)),
        patch("profile_isolation.subprocess.run") as mock_run,
        patch.object(profile_isolation, "HERMES_UID_MIN", 20000),
        patch.object(profile_isolation, "HERMES_UID_MAX", 20001),
    ):
        mock_run.return_value = MagicMock(returncode=1)  # useradd 恒失败
        with pytest.raises(RuntimeError, match="No available uid"):
            profile_isolation._ensure_profile_user("x", "/opt/data/profiles/x")


def test_chown_profile_dir_chowns_uid_uid_and_chmod_0700():
    """chown -R {uid}:{uid} + chmod 0700（dir + logs/）。"""
    with (
        patch("profile_isolation.os.path.exists", return_value=True),
        patch("profile_isolation.os.makedirs") as mock_makedirs,
        patch("profile_isolation.os.chmod") as mock_chmod,
        patch("profile_isolation.subprocess.run") as mock_run,
    ):
        profile_isolation._chown_profile_dir(20010, "/opt/data/profiles/alice")
    # chown -R 20010:20010
    assert mock_run.call_args.args[0] == ["chown", "-R", "20010:20010", "/opt/data/profiles/alice"]
    # logs/ 被建
    mock_makedirs.assert_called_once_with("/opt/data/profiles/alice/logs", exist_ok=True)
    # dir + logs/ 均 0700
    mock_chmod.assert_any_call("/opt/data/profiles/alice", 0o700)
    mock_chmod.assert_any_call("/opt/data/profiles/alice/logs", 0o700)


def test_harden_secrets_reowns_root_chmod_640():
    """共享 skill 目录下 secrets.enc 收归 root:root 0640。

    gateway 非 root uid 读不到、sidecar（root）可读（external_dirs 模型防御性加固）。
    """
    with (
        patch("profile_isolation.os.path.isdir", return_value=True),
        patch("profile_isolation.subprocess.run") as mock_run,
    ):
        profile_isolation._harden_secrets("defid-alice")
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "find"
    assert "/opt/data/skills/defid-alice" in cmd
    assert "secrets.enc" in cmd
    assert "chown" in cmd and "root:root" in cmd
    assert "chmod" in cmd and "640" in cmd


def test_harden_secrets_skips_when_no_definition_id():
    """definition_id 为 None（无 skill 的 profile）时跳过，不调 find。"""
    with patch("profile_isolation.subprocess.run") as mock_run:
        profile_isolation._harden_secrets(None)
    mock_run.assert_not_called()


def test_harden_secrets_skips_when_dir_missing():
    """共享 skill 目录不存在时跳过（definition 还没装 skill）。"""
    with (
        patch("profile_isolation.os.path.isdir", return_value=False),
        patch("profile_isolation.subprocess.run") as mock_run,
    ):
        profile_isolation._harden_secrets("defid-no-skill")
    mock_run.assert_not_called()


def test_launch_uses_preexec_fn_and_sets_hermes_home():
    """launch：ensure→chown→harden→Popen 带 preexec_fn（降权）+ HERMES_HOME/HOME/PORT。"""
    with (
        patch("profile_isolation._ensure_profile_user", return_value=20010) as m_ensure,
        patch("profile_isolation._chown_profile_dir") as m_chown,
        patch("profile_isolation._harden_secrets") as m_harden,
        patch("profile_isolation.subprocess.Popen") as m_popen,
        patch("builtins.open", mock_open()),
    ):
        uid = profile_isolation.launch("alice", "/opt/data/profiles/alice", 8644)
    assert uid == 20010
    m_ensure.assert_called_once_with("alice", "/opt/data/profiles/alice")
    m_chown.assert_called_once_with(20010, "/opt/data/profiles/alice")
    m_harden.assert_called_once_with(None)
    args, kwargs = m_popen.call_args
    assert args[0] == ["hermes", "gateway", "run", "--replace"]
    assert kwargs["preexec_fn"] is not None  # 降权 preexec_fn
    env = kwargs["env"]
    assert env["HERMES_HOME"] == "/opt/data/profiles/alice"
    assert env["HOME"] == "/opt/data/profiles/alice"
    assert env["API_SERVER_PORT"] == "8644"


def test_cleanup_userdels_profile_username():
    """cleanup：userdel hermes-{name}。"""
    with patch("profile_isolation.subprocess.run") as mock_run:
        profile_isolation.cleanup("alice")
    assert mock_run.call_args.args[0] == ["userdel", "hermes-alice"]


def test_launch_writes_gateway_pid_for_teardown_kill():
    """launch 写 gateway.pid（proc.pid）供 teardown kill，避免 gateway 孤儿占端口。

    不写则 teardown 的 `kill $(cat gateway.pid)` 取不到 PID → gateway 成孤儿，
    已删用户仍可经此孤儿 gateway 请求（profile 漂移 + 越权）。
    """
    m_popen = MagicMock()
    m_popen.return_value.pid = 12345
    m_open = mock_open()
    with (
        patch("profile_isolation._ensure_profile_user", return_value=20010),
        patch("profile_isolation._chown_profile_dir"),
        patch("profile_isolation._harden_secrets"),
        patch("profile_isolation.subprocess.Popen", m_popen),
        patch("builtins.open", m_open),
    ):
        profile_isolation.launch("alice", "/opt/data/profiles/alice", 8644)
    # gateway.pid 文件被打开写入
    pid_opens = [c for c in m_open.call_args_list if "gateway.pid" in str(c.args)]
    assert pid_opens, "gateway.pid 未写入"
    # 写入内容 = Popen 返回的 pid
    m_open().write.assert_any_call("12345")


# ── _sync_config_api_port：堵 port_map↔config.yaml 端口漂移 ──


def _write_cfg(path, content):
    path.write_text(content)


def test_sync_config_api_port_overwrites_stale(tmp_path):
    """stale 旧端口（已删用户 profile 残留）→ 强制覆盖为 port_map 新端口。"""
    import yaml

    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg, "platforms:\n  api_server:\n    port: 8644\nmodel:\n  default: x\n")
    profile_isolation._sync_config_api_port(str(tmp_path), 8645)
    out = yaml.safe_load(cfg.read_text())
    assert out["platforms"]["api_server"]["port"] == 8645  # 8644 → 8645
    assert out["model"]["default"] == "x"  # 其余字段保留


def test_sync_config_api_port_adds_when_empty(tmp_path):
    """platforms.api_server 存在但无 port（新 heal 的 config.yaml）→ 补 port。"""
    import yaml

    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg, "platforms:\n  api_server: {}\n")
    profile_isolation._sync_config_api_port(str(tmp_path), 8646)
    out = yaml.safe_load(cfg.read_text())
    assert out["platforms"]["api_server"]["port"] == 8646


def test_sync_config_api_port_adds_platforms_section(tmp_path):
    """config.yaml 无 platforms 段 → 创建 platforms.api_server.port。"""
    import yaml

    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg, "model:\n  default: x\n")
    profile_isolation._sync_config_api_port(str(tmp_path), 8647)
    out = yaml.safe_load(cfg.read_text())
    assert out["platforms"]["api_server"]["port"] == 8647
    assert out["model"]["default"] == "x"


def test_sync_config_api_port_missing_file_noop(tmp_path):
    """config.yaml 不存在 → 不报错、不创建文件（hermes 回退 .env）。"""
    profile_isolation._sync_config_api_port(str(tmp_path), 8648)
    assert not (tmp_path / "config.yaml").exists()


def test_launch_calls_sync_config_api_port_before_chown():
    """launch 在 _chown_profile_dir 前调 _sync_config_api_port（chown 一并改属主）。"""
    m_popen = MagicMock()
    m_popen.return_value.pid = 12345
    m_open = mock_open()
    call_order = []

    def _spy_sync(d, p):
        call_order.append(("sync", d, p))

    def _spy_chown(uid, d):
        call_order.append(("chown", uid, d))

    with (
        patch("profile_isolation._ensure_profile_user", return_value=20010),
        patch("profile_isolation._sync_config_api_port", side_effect=_spy_sync),
        patch("profile_isolation._chown_profile_dir", side_effect=_spy_chown),
        patch("profile_isolation._harden_secrets"),
        patch("profile_isolation.subprocess.Popen", m_popen),
        patch("builtins.open", m_open),
    ):
        profile_isolation.launch("alice", "/opt/data/profiles/alice", 8644)
    # sync 在 chown 前
    kinds = [c[0] for c in call_order]
    assert "sync" in kinds and "chown" in kinds
    assert kinds.index("sync") < kinds.index("chown")
    # sync 收到 port_map 的端口
    sync_call = next(c for c in call_order if c[0] == "sync")
    assert sync_call[2] == 8644


# ── _clear_stale_gateway_lock：清 PVC 残留 stale 锁，修滚动更新 gateway 起不来 ──

import json as _json


def _write_lock(tmp_path, pid, start_time):
    (tmp_path / "gateway.lock").write_text(_json.dumps({"pid": pid, "start_time": start_time}))


def test_clear_stale_lock_dead_pid(tmp_path):
    """持有者 PID 已死（Pod 重建后旧 gateway 进程没了）→ 清 stale 锁。"""
    _write_lock(tmp_path, 99999, 100)
    with patch("profile_isolation.os.kill", side_effect=ProcessLookupError):
        profile_isolation._clear_stale_gateway_lock(str(tmp_path))
    assert not (tmp_path / "gateway.lock").exists()


def test_clear_stale_lock_no_lock_noop(tmp_path):
    """无 lock 文件 → 不报错。"""
    profile_isolation._clear_stale_gateway_lock(str(tmp_path))
    assert not (tmp_path / "gateway.lock").exists()


def test_clear_stale_lock_malformed_kept(tmp_path):
    """lock 文件损坏（非 JSON）→ best-effort 不动（不崩）。"""
    (tmp_path / "gateway.lock").write_text("not json{")
    profile_isolation._clear_stale_gateway_lock(str(tmp_path))
    assert (tmp_path / "gateway.lock").exists()  # 保守保留


def test_clear_stale_lock_live_matching_kept(tmp_path):
    """PID 存活 + start_time 匹配（真在跑的 gateway）→ 保留锁（不误清）。"""
    _write_lock(tmp_path, 12345, 200)
    # /proc/<pid>/stat 第 22 字段(starttime)=200，与 lock 记录一致
    stat_line = "0 (hermes) S " + "0 " * 18 + "200 "
    import builtins
    real_open = builtins.open

    def _open(path, *a, **k):
        if str(path).startswith("/proc/"):
            m = mock_open(read_data=stat_line)
            return m()
        return real_open(path, *a, **k)

    with patch("profile_isolation.os.kill", return_value=None), patch("builtins.open", side_effect=_open):
        profile_isolation._clear_stale_gateway_lock(str(tmp_path))
    assert (tmp_path / "gateway.lock").exists()  # live → 保留


def test_clear_stale_lock_pid_reuse_cleared(tmp_path):
    """PID 存活但 start_time 不匹配（PID 被复用为别的进程）→ 清 stale 锁。"""
    _write_lock(tmp_path, 12345, 100)  # lock 记录 start_time=100
    stat_line = "0 (hermes) S " + "0 " * 18 + "200 "  # 当前 /proc 读出 start_time=200（复用）
    import builtins
    real_open = builtins.open

    def _open(path, *a, **k):
        if str(path).startswith("/proc/"):
            m = mock_open(read_data=stat_line)
            return m()
        return real_open(path, *a, **k)

    with patch("profile_isolation.os.kill", return_value=None), patch("builtins.open", side_effect=_open):
        profile_isolation._clear_stale_gateway_lock(str(tmp_path))
    assert not (tmp_path / "gateway.lock").exists()  # PID 复用 → 清


def test_launch_clears_stale_lock_before_popen():
    """launch 在 Popen 前调 _clear_stale_gateway_lock。"""
    m_popen = MagicMock()
    m_popen.return_value.pid = 12345
    m_open = mock_open()
    call_order = []

    def _spy_clear(d):
        call_order.append("clear")

    with (
        patch("profile_isolation._ensure_profile_user", return_value=20010),
        patch("profile_isolation._sync_config_api_port"),
        patch("profile_isolation._clear_stale_gateway_lock", side_effect=_spy_clear),
        patch("profile_isolation._chown_profile_dir"),
        patch("profile_isolation._harden_secrets"),
        patch("profile_isolation.subprocess.Popen", m_popen),
        patch("builtins.open", m_open),
    ):
        profile_isolation.launch("alice", "/opt/data/profiles/alice", 8644)
    assert "clear" in call_order


def test_sync_config_api_port_also_syncs_env(tmp_path):
    """.env 的 API_SERVER_PORT 必须与 config.yaml 同步，否则 hermes 启动 hang。"""
    import yaml

    (tmp_path / "config.yaml").write_text("platforms:\n  api_server:\n    port: 8644\n")
    (tmp_path / ".env").write_text(
        "API_SERVER_ENABLED=true\nAPI_SERVER_PORT=8644\nOPENAI_API_KEY=sk-x\n"
    )
    profile_isolation._sync_config_api_port(str(tmp_path), 8647)
    # config.yaml synced
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["platforms"]["api_server"]["port"] == 8647
    # .env API_SERVER_PORT synced to 8647 (was 8644), other keys preserved
    env = (tmp_path / ".env").read_text()
    assert "API_SERVER_PORT=8647\n" in env
    assert "API_SERVER_ENABLED=true\n" in env
    assert "OPENAI_API_KEY=sk-x\n" in env


def test_sync_config_api_port_adds_env_port_if_missing(tmp_path):
    """.env 无 API_SERVER_PORT 行 → 补上（与 config.yaml 一致）。"""
    import yaml

    (tmp_path / "config.yaml").write_text("platforms:\n  api_server: {}\n")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-x\n")
    profile_isolation._sync_config_api_port(str(tmp_path), 8646)
    env = (tmp_path / ".env").read_text()
    assert "API_SERVER_PORT=8646\n" in env
    assert "OPENAI_API_KEY=sk-x\n" in env  # 原 key 保留
