"""current-user-info 预置 skill 的 get_user_info.py 脚本测试。

mock urllib，验证：profile_name 从 HERMES_HOME 解析、请求头 X-Internal-Token、
CONTROLLER_URL 默认值、退出码、stdout JSON 透传。脚本用 importlib 从文件路径加载。
"""

import importlib.util
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent
    / "app"
    / "data"
    / "preset_skills"
    / "current-user-info"
    / "scripts"
    / "get_user_info.py"
)


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("get_user_info", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_resp(body: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_success_returns_json(script, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", "/opt/data/profiles/prof-alice-abc123")
    monkeypatch.setenv("CONTROLLER_URL", "http://manager:8002")
    monkeypatch.setenv("UA_INTERNAL_TOKEN", "secret")
    payload = {"fields": {"用户名": "alice"}, "business": {}}

    with patch.object(script.urllib.request, "urlopen", return_value=_mock_resp(payload)) as m:
        rc = script.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == payload
    # 请求 URL + 头
    req = m.call_args.args[0]
    assert (
        req.full_url == "http://manager:8002/api/controller/profiles/prof-alice-abc123/user-context"
    )
    assert req.get_header("X-internal-token") == "secret"


def test_default_controller_url_when_env_unset(script, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", "/opt/data/profiles/prof-1")
    monkeypatch.delenv("CONTROLLER_URL", raising=False)
    monkeypatch.delenv("UA_INTERNAL_TOKEN", raising=False)

    with patch.object(
        script.urllib.request, "urlopen", return_value=_mock_resp({"fields": {}, "business": {}})
    ) as m:
        rc = script.main()

    assert rc == 0
    req = m.call_args.args[0]
    assert req.full_url == "http://manager:8002/api/controller/profiles/prof-1/user-context"
    # 未配 token 时不带鉴权头
    assert req.get_header("x-internal-token") is None


def test_profile_name_resolved_from_hermes_home(script, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", "/opt/data/profiles/prof-alice-abc123")
    monkeypatch.delenv("CONTROLLER_URL", raising=False)
    monkeypatch.delenv("UA_INTERNAL_TOKEN", raising=False)

    with patch.object(
        script.urllib.request, "urlopen", return_value=_mock_resp({"fields": {}, "business": {}})
    ) as m:
        rc = script.main()

    assert rc == 0
    req = m.call_args.args[0]
    assert (
        req.full_url == "http://manager:8002/api/controller/profiles/prof-alice-abc123/user-context"
    )


def test_hermes_home_unset_falls_back_to_cwd(script, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr("os.getcwd", lambda: "/opt/data/profiles/prof-cwd")
    monkeypatch.delenv("CONTROLLER_URL", raising=False)
    monkeypatch.delenv("UA_INTERNAL_TOKEN", raising=False)

    with patch.object(
        script.urllib.request, "urlopen", return_value=_mock_resp({"fields": {}, "business": {}})
    ) as m:
        rc = script.main()

    assert rc == 0
    assert "/api/controller/profiles/prof-cwd/user-context" in m.call_args.args[0].full_url


def test_manager_http_error_returns_2(script, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", "/opt/data/profiles/prof-1")
    monkeypatch.delenv("CONTROLLER_URL", raising=False)
    monkeypatch.delenv("UA_INTERNAL_TOKEN", raising=False)

    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    err.read = lambda: b'{"detail":"profile not found"}'
    with patch.object(script.urllib.request, "urlopen", side_effect=err):
        rc = script.main()

    assert rc == 2
    assert "404" in capsys.readouterr().err


def test_network_error_returns_3(script, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", "/opt/data/profiles/prof-1")
    monkeypatch.delenv("CONTROLLER_URL", raising=False)
    monkeypatch.delenv("UA_INTERNAL_TOKEN", raising=False)

    with patch.object(script.urllib.request, "urlopen", side_effect=ConnectionError("timeout")):
        rc = script.main()

    assert rc == 3
    assert "timeout" in capsys.readouterr().err
