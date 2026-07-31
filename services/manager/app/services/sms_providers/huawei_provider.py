"""华为云 SMS 探活 + 发码 — 无官方 SDK，用 httpx + stdlib HMAC-SHA256 签名直接调 RESTful API。

复用 email_providers/huawei_provider.py 的签名实现 pattern。
- 探活调 ListSmsTemplate 只读 API（limit=1）
- 发码调 SendSms API（POST /v1/sms/messages）
"""

import hashlib
import hmac
import json
import httpx
from datetime import datetime, timezone


def probe(cfg, secrets: dict) -> None:
    """华为云 SMS 探活。secrets = {"access_key_id": "...", "access_key_secret": "..."}

    调用只读 list 模板 API（limit=1，offset=0），成功 = AK/SK + region 可用；
    失败抛异常由调用方分类转中文错误信息。
    """
    ak = secrets["access_key_id"]
    sk = secrets["access_key_secret"]
    region = cfg.region or "cn-north-4"
    # MSGSMS 服务端点: https://sms.{region}.myhuaweicloud.com
    host = f"sms.{region}.myhuaweicloud.com"
    path = "/v1/sms/tmpl-sms/templates"
    query = "limit=1&offset=0"

    # SDK-HMAC-SHA256 签名（同 email huawei_provider pattern，直接用 sk 不做 KDF 派生）
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_headers = (
        f"host: {host}\n"
        f"x-sdk-content-sha256: {payload_hash}\n"
        f"x-sdk-date: {timestamp}\n"
    )
    signed_headers = "host;x-sdk-content-sha256;x-sdk-date"
    canonical_request = (
        f"GET\n{path}\n{query}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    string_to_sign = (
        f"SDK-HMAC-SHA256\n{timestamp}\n"
        + hashlib.sha256(canonical_request.encode()).hexdigest()
    )
    signature = hmac.new(sk.encode(), string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_header = (
        f"SDK-HMAC-SHA256 Access={ak}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    resp = httpx.get(
        f"https://{host}{path}?{query}",
        headers={
            "Host": host,
            "x-sdk-content-sha256": payload_hash,
            "x-sdk-date": timestamp,
            "Authorization": auth_header,
        },
        timeout=15.0,
    )
    if resp.status_code in (401, 403):
        raise Exception(f"Authentication failed (status={resp.status_code})")
    if resp.status_code >= 400:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")


def send(cfg, secrets: dict, phone: str, template_param: dict) -> None:
    """华为云 SMS 发码。secrets = {"access_key_id", "access_key_secret"}。

    template_param = {"code": "123456"} → 转 list 后传 template_paras 字段。
    成功 return None；失败 raise Exception。
    """
    ak = secrets["access_key_id"]
    sk = secrets["access_key_secret"]
    region = cfg.region or "cn-north-4"
    host = f"sms.{region}.myhuaweicloud.com"
    path = "/v1/sms/messages"

    # 华为云 SMS body 字段：from=签名名称 / to=[手机号] / template_id=模板 ID / template_paras=[参数值]
    body = {
        "from": cfg.sign_name,
        "to": [phone if phone.startswith("+") else f"+86{phone}"],
        "template_id": cfg.template_code,
        "template_paras": list(template_param.values()),
    }
    body_bytes = json.dumps(body, separators=(",", ":")).encode()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload_hash = hashlib.sha256(body_bytes).hexdigest()

    canonical_headers = (
        f"host: {host}\n"
        f"x-sdk-content-sha256: {payload_hash}\n"
        f"x-sdk-date: {timestamp}\n"
    )
    signed_headers = "host;x-sdk-content-sha256;x-sdk-date"
    canonical_request = (
        f"POST\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    string_to_sign = (
        f"SDK-HMAC-SHA256\n{timestamp}\n"
        + hashlib.sha256(canonical_request.encode()).hexdigest()
    )
    signature = hmac.new(sk.encode(), string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth_header = (
        f"SDK-HMAC-SHA256 Access={ak}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    resp = httpx.post(
        f"https://{host}{path}",
        content=body_bytes,
        headers={
            "Host": host,
            "Content-Type": "application/json",
            "x-sdk-content-sha256": payload_hash,
            "x-sdk-date": timestamp,
            "Authorization": auth_header,
        },
        timeout=15.0,
    )
    if resp.status_code in (401, 403):
        raise Exception(f"Authentication failed (status={resp.status_code})")
    if resp.status_code >= 400:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
    # 成功响应 body: {"code": "000000", "description": "Success", ...}
    try:
        result = resp.json()
        if result.get("code") not in ("000000", "0"):
            raise Exception(f"huawei send_sms failed: {result.get('description', result)}")
    except json.JSONDecodeError:
        raise Exception(f"huawei send_sms invalid response: {resp.text[:200]}")
