"""华为云 Cloud Email 探活 + 发码 — httpx + stdlib HMAC-SHA256 签名（无 SDK 依赖）。

- probe: 调 IAM ListUsers API 验证 AK/SK 是合法华为云凭据（IAM 是华为云所有服务的鉴权基底）
- send: 调 cloudemail 邮件发送 API（RESTful POST）

华为云签名算法 SDK-HMAC-SHA256（不派生签名密钥，直接用 SK 做 HMAC key）：
1. canonical_request = METHOD\nURI\nQUERY\nCANONICAL_HEADERS\nSIGNED_HEADERS\nPAYLOAD_HASH
2. string_to_sign = "SDK-HMAC-SHA256\n" + timestamp + "\n" + sha256_hex(canonical_request)
3. signature = hex(HMAC-SHA256(SK, string_to_sign))
4. Authorization = "SDK-HMAC-SHA256 Access={AK}, SignedHeaders=..., Signature=..."

失败抛异常（被 API 层分类为 auth/timeout/connection/other）。
"""
import datetime
import hashlib
import hmac
import json


def probe(cfg, secrets: dict) -> None:
    """华为云探活。secrets = {"access_key_id": "...", "access_key_secret": "..."}.

    调 IAM ListUsers API（GET https://iam.myhuaweicloud.com/v3/users）验证 AK/SK 可用。
    成功 return None；失败抛异常（401 Unauthorized / 网络错误等）。
    """
    import httpx

    ak = secrets["access_key_id"]
    sk = secrets["access_key_secret"]
    host = "iam.myhuaweicloud.com"
    endpoint = f"https://{host}"
    path = "/v3/users"
    query = "limit=1"

    now = datetime.datetime.utcnow()
    x_sdk_date = now.strftime("%Y%m%dT%H%M%SZ")

    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_headers = (
        f"host:{host}\n"
        f"x-sdk-content-sha256:{payload_hash}\n"
        f"x-sdk-date:{x_sdk_date}\n"
    )
    signed_headers = "host;x-sdk-content-sha256;x-sdk-date"

    canonical_request = (
        "GET\n"
        f"{path}\n"
        f"{query}\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{payload_hash}"
    )

    string_to_sign = (
        "SDK-HMAC-SHA256\n"
        f"{x_sdk_date}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    signature = hmac.new(
        sk.encode(), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"SDK-HMAC-SHA256 Access={ak}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    headers = {
        "Host": host,
        "x-sdk-content-sha256": payload_hash,
        "x-sdk-date": x_sdk_date,
        "Authorization": authorization,
    }

    resp = httpx.get(
        f"{endpoint}{path}?{query}",
        headers=headers,
        timeout=15.0,
    )
    if resp.status_code == 401:
        raise Exception("Unauthorized: AK/SK invalid")
    if resp.status_code >= 400:
        raise Exception(f"Unauthorized: HTTP {resp.status_code}")


def send(cfg, secrets: dict, to: str, subject: str, html_body: str) -> None:
    """华为云 Cloud Email 发邮件。secrets = {"access_key_id", "access_key_secret"}.

    cfg.from_email 是发信地址；cfg.region 用于选择 cloudemail 端点。
    成功 return None；失败 raise Exception。
    """
    import httpx

    ak = secrets["access_key_id"]
    sk = secrets["access_key_secret"]
    region = cfg.region or "cn-north-4"
    # cloudemail 端点: https://cloudemail.{region}.myhuaweicloud.com
    host = f"cloudemail.{region}.myhuaweicloud.com"
    endpoint = f"https://{host}"
    path = "/v1/cloudemail/email-body/xphone/v2/send"

    body = {
        "from_mail": cfg.from_email,
        "to_mail": to,
        "subject": subject,
        "body_html": html_body,
    }
    body_bytes = json.dumps(body, separators=(",", ":")).encode()

    now = datetime.datetime.utcnow()
    x_sdk_date = now.strftime("%Y%m%dT%H%M%SZ")

    payload_hash = hashlib.sha256(body_bytes).hexdigest()

    canonical_headers = (
        f"host:{host}\n"
        f"x-sdk-content-sha256:{payload_hash}\n"
        f"x-sdk-date:{x_sdk_date}\n"
    )
    signed_headers = "host;x-sdk-content-sha256;x-sdk-date"

    canonical_request = (
        "POST\n"
        f"{path}\n"
        "\n"  # 无 query string
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{payload_hash}"
    )

    string_to_sign = (
        "SDK-HMAC-SHA256\n"
        f"{x_sdk_date}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    signature = hmac.new(
        sk.encode(), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"SDK-HMAC-SHA256 Access={ak}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    headers = {
        "Host": host,
        "Content-Type": "application/json",
        "x-sdk-content-sha256": payload_hash,
        "x-sdk-date": x_sdk_date,
        "Authorization": authorization,
    }

    resp = httpx.post(
        f"{endpoint}{path}",
        content=body_bytes,
        headers=headers,
        timeout=15.0,
    )
    if resp.status_code in (401, 403):
        raise Exception(f"Authentication failed (status={resp.status_code})")
    if resp.status_code >= 400:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
    # 成功响应：HTTP 200 + body {"code": 0, ...}（华为云 cloudemail code=0 表示成功）
    try:
        result = resp.json()
        if result.get("code") not in (0, "0"):
            raise Exception(f"huawei send_email failed: {result.get('description', result)}")
    except json.JSONDecodeError:
        raise Exception(f"huawei send_email invalid response: {resp.text[:200]}")
