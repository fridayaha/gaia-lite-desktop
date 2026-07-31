"""skill-secret-sidecar 单测：glob 路径契约 + /secret 解密 round-trip + 404/500 分支
+ 每请求重读 key 文件（轮换无需重启）+ 多 key Fernet + env 回退。

无外部依赖（不依赖 k8s/DB），用 tmp_path 造 secrets.enc + httpx ASGITransport 直驱 FastAPI app。
固化路径契约 /opt/data/skills/{definition_id}/{skill}/secrets.enc（防 external_dirs 漂移回归）。
"""

import base64
import hashlib
import json
import os
import sys
from unittest.mock import patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

# sidecar 是顶层模块（非包内），加入 sys.path 导入（仿 test_profile_isolation.py）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sidecar  # noqa: E402


def _fernet_for(material: bytes) -> Fernet:
    """raw material → Fernet（复刻 sidecar._derive_fernet）。"""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def _make_enc(fernet, creds: dict) -> bytes:
    return fernet.encrypt(json.dumps(creds, ensure_ascii=False).encode("utf-8"))


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=sidecar.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def fernet(monkeypatch):
    """固定 _load_fernet 返回已知 Fernet（替代旧 sidecar._fernet 模块全局）。"""
    f = _fernet_for(b"test-key")
    monkeypatch.setattr(sidecar, "_load_fernet", lambda: f)
    return f


async def test_glob_path_contract(monkeypatch):
    """glob 路径 = {_SKILLS_ROOT}/*/{skill}/secrets.enc（固化契约，防漂移回归）。"""
    monkeypatch.setattr(sidecar, "_SKILLS_ROOT", "/tmp/fake-skills")
    with patch("sidecar.glob.glob", return_value=[]) as m:
        with pytest.raises(Exception):  # HTTPException(404)
            await sidecar.get_secret(skill="weather", key="api_key")
    m.assert_called_once_with("/tmp/fake-skills/*/weather/secrets.enc")


async def test_secret_roundtrip(tmp_path, client, fernet, monkeypatch):
    """造 secrets.enc（fernet 加密）→ GET /secret 返回明文。"""
    monkeypatch.setattr(sidecar, "_SKILLS_ROOT", str(tmp_path))
    enc = _make_enc(fernet, {"api_key": "sk-x", "api_secret": "yy"})
    skill_dir = tmp_path / "defid-1" / "weather"
    skill_dir.mkdir(parents=True)
    (skill_dir / "secrets.enc").write_bytes(enc)

    r = await client.get("/secret", params={"skill": "weather", "key": "api_key"})
    assert r.status_code == 200
    assert r.json()["value"] == "sk-x"


async def test_secret_404_no_file(tmp_path, client, monkeypatch):
    """无 secrets.enc → 404。"""
    monkeypatch.setattr(sidecar, "_SKILLS_ROOT", str(tmp_path))
    r = await client.get("/secret", params={"skill": "nope", "key": "api_key"})
    assert r.status_code == 404


async def test_secret_404_key_not_configured(tmp_path, client, fernet, monkeypatch):
    """secrets.enc 存在但请求的 key 不在 → 404。"""
    monkeypatch.setattr(sidecar, "_SKILLS_ROOT", str(tmp_path))
    enc = _make_enc(fernet, {"api_key": "sk-x"})
    skill_dir = tmp_path / "defid-1" / "weather"
    skill_dir.mkdir(parents=True)
    (skill_dir / "secrets.enc").write_bytes(enc)
    r = await client.get("/secret", params={"skill": "weather", "key": "api_secret"})
    assert r.status_code == 404


async def test_secret_500_key_mismatch(tmp_path, client, fernet, monkeypatch):
    """secrets.enc 用不同 key 加密 → sidecar 解不开 → 500。"""
    monkeypatch.setattr(sidecar, "_SKILLS_ROOT", str(tmp_path))
    other = _fernet_for(b"different-key")
    enc = other.encrypt(json.dumps({"api_key": "sk-x"}).encode())
    skill_dir = tmp_path / "defid-1" / "weather"
    skill_dir.mkdir(parents=True)
    (skill_dir / "secrets.enc").write_bytes(enc)
    r = await client.get("/secret", params={"skill": "weather", "key": "api_key"})
    assert r.status_code == 500


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── B1: 每请求重读 key 文件（轮换无需重启）+ B2: 多 key ─────────────────


async def test_per_request_key_reread(tmp_path, client, monkeypatch):
    """换 key 文件后不重启 sidecar，下次 /secret 即用新 key（模拟 kubelet 刷新 volume）。"""
    monkeypatch.setattr(sidecar, "_SKILLS_ROOT", str(tmp_path))
    key_file = tmp_path / "key"
    monkeypatch.setattr(sidecar, "_KEY_FILE", str(key_file))
    key_file.write_text("k1")

    enc = _make_enc(_fernet_for(b"k1"), {"api_key": "sk-x"})
    skill_dir = tmp_path / "defid-1" / "weather"
    skill_dir.mkdir(parents=True)
    (skill_dir / "secrets.enc").write_bytes(enc)

    r = await client.get("/secret", params={"skill": "weather", "key": "api_key"})
    assert r.status_code == 200
    assert r.json()["value"] == "sk-x"

    # 轮换：key 文件改 "k2\nk1"（newest 前置），不重启 sidecar，旧密文仍可解（MultiFernet）
    key_file.write_text("k2\nk1")
    r2 = await client.get("/secret", params={"skill": "weather", "key": "api_key"})
    assert r2.status_code == 200
    assert r2.json()["value"] == "sk-x"


async def test_sidecar_multikey_rotation(tmp_path, client, monkeypatch):
    """旧 key 加密的密文，sidecar 配 [new, old] 多 key 仍可解（轮换零 500）。"""
    monkeypatch.setattr(sidecar, "_SKILLS_ROOT", str(tmp_path))
    key_file = tmp_path / "key"
    monkeypatch.setattr(sidecar, "_KEY_FILE", str(key_file))
    key_file.write_text("new-key\nold-key")

    enc = _make_enc(_fernet_for(b"old-key"), {"api_key": "sk-x"})
    skill_dir = tmp_path / "defid-1" / "weather"
    skill_dir.mkdir(parents=True)
    (skill_dir / "secrets.enc").write_bytes(enc)

    r = await client.get("/secret", params={"skill": "weather", "key": "api_key"})
    assert r.status_code == 200
    assert r.json()["value"] == "sk-x"


async def test_sidecar_falls_back_to_env_when_file_absent(tmp_path, client, monkeypatch):
    """key 文件不存在 → 回退 env CREDENTIAL_ENCRYPTION_KEY（rollout 期共存）。"""
    monkeypatch.setattr(sidecar, "_SKILLS_ROOT", str(tmp_path))
    monkeypatch.setattr(sidecar, "_KEY_FILE", str(tmp_path / "no-such-file"))
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-env-key")

    enc = _make_enc(_fernet_for(b"test-env-key"), {"api_key": "sk-x"})
    skill_dir = tmp_path / "defid-1" / "weather"
    skill_dir.mkdir(parents=True)
    (skill_dir / "secrets.enc").write_bytes(enc)

    r = await client.get("/secret", params={"skill": "weather", "key": "api_key"})
    assert r.status_code == 200
    assert r.json()["value"] == "sk-x"
