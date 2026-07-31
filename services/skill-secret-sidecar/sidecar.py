"""skill-secret-sidecar — 解密 skill secrets.enc，返回明文给 execute_code。

hermes execute_code 沙箱隔离 Pod env（读不到业务环境变量），但能 open Pod 文件 + 调 localhost。
故 secret 密文落 Pod 文件（secrets.enc），本 sidecar 持 credential_encryption_key 解密，
execute_code 调 localhost:8004 拿明文，用明文直接调外部 API（不经出口代理）。

key 派生复刻 app/core/crypto.py:_load_fernet（sha256 + urlsafe_b64encode），dev 兜底同
_DEV_KEY_MATERIAL。key 来源优先 projected Secret 文件（/etc/ua/credential-key/...，
kubelet ~60s 刷新 projected volume，_load_fernet 每请求重读 → key 轮换无需重启 sidecar），
回退 env CREDENTIAL_ENCRYPTION_KEY，回退 dev material。支持多 key 轮换（值换行分隔，
newest 在前；MultiFernet.decrypt 依次尝试 → 旧密文仍可解，轮换零 500）。

secrets.enc 路径：/opt/data/skills/{definition_id}/{skill}/secrets.enc（external_dirs 共享模型，
与 manager router.py 落盘路径一致）。本 sidecar glob {UA_SKILLS_ROOT}/*/{skill}/secrets.enc。
"""

import base64
import glob
import hashlib
import json
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

app = FastAPI(title="skill-secret-sidecar")

# dev 兜底 key material（与 app/core/crypto.py:_DEV_KEY_MATERIAL 一致）
_DEV_KEY_MATERIAL = b"ua-credential-dev-key-do-not-use-in-prod"

# key 文件挂载点：projected Secret volume（unionagents-secret/credential-encryption-key），
# 由 k8s_manager 注入到 sidecar 容器。kubelet ~60s 刷新 projected volume；_load_fernet 每请求
# 重读文件 → key 轮换无需重启 sidecar。UA_CREDENTIAL_KEY_FILE 供测试覆盖路径。
_KEY_FILE = os.environ.get(
    "UA_CREDENTIAL_KEY_FILE", "/etc/ua/credential-key/credential-encryption-key"
)


def _derive_fernet(material: bytes) -> Fernet:
    """raw material → Fernet（sha256 → urlsafe_b64encode，复刻 crypto.py）。"""
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def _read_key_materials() -> list[bytes]:
    """key material 优先级：① projected Secret 文件（轮换生效）② env CREDENTIAL_ENCRYPTION_KEY
    （rollout 期回退）③ dev material。值按换行分割为多 key（newest 在前，轮换时前置新 key
    不删旧 → 旧密文仍可解）。单行 = list-of-one。
    """
    raw = None
    try:
        with open(_KEY_FILE, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        raw = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if not raw:
        return [_DEV_KEY_MATERIAL]
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    return [ln.encode("utf-8") for ln in lines] if lines else [_DEV_KEY_MATERIAL]


def _load_fernet() -> MultiFernet:
    """每请求重读 key（不缓存）→ kubelet 刷新 projected volume 后即生效，无需重启。"""
    return MultiFernet([_derive_fernet(m) for m in _read_key_materials()])

# 共享 skill 目录根：secrets.enc 落在 {root}/{definition_id}/{skill}/secrets.enc
_SKILLS_ROOT = os.environ.get("UA_SKILLS_ROOT", "/opt/data/skills")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/secret")
async def get_secret(
    skill: str = Query(..., description="skill name"),
    key: str = Query(..., description="secret 参数名"),
):
    """返回指定 skill 的指定 secret 参数明文（解密 secrets.enc）。"""
    candidates = glob.glob(f"{_SKILLS_ROOT}/*/{skill}/secrets.enc")
    if not candidates:
        raise HTTPException(status_code=404, detail=f"no secrets for skill {skill}")
    try:
        with open(candidates[0], "rb") as f:
            token = f.read()
        creds = json.loads(_load_fernet().decrypt(token))
    except InvalidToken:
        raise HTTPException(
            status_code=500,
            detail="decrypt failed: invalid token or CREDENTIAL_ENCRYPTION_KEY mismatch",
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"decrypt failed: {e}")
    if key not in creds:
        raise HTTPException(
            status_code=404, detail=f"secret {key} not configured for skill {skill}"
        )
    return JSONResponse({"value": creds[key]})
