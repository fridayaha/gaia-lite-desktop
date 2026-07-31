"""阿里云 SMS 探活 + 发码 — 探活调只读 QuerySmsTemplateList，发码调 SendSms。"""

import json

from alibabacloud_dysmsapi20170525.client import Client
from alibabacloud_dysmsapi20170525 import models
from alibabacloud_tea_openapi import models as open_api_models


def probe(cfg, secrets: dict) -> None:
    """阿里云 SMS 探活。secrets = {"access_key_id": "...", "access_key_secret": "..."}

    调用只读 list 模板 API（不分页、limit=1），成功 = AK/SK + region 可用；
    失败抛异常由调用方分类转中文错误信息。
    """
    config = open_api_models.Config(
        access_key_id=secrets["access_key_id"],
        access_key_secret=secrets["access_key_secret"],
        endpoint=f"dysmsapi.{cfg.region or 'cn-hangzhou'}.aliyuncs.com",
        connect_timeout=15000,
        read_timeout=15000,
    )
    client = Client(config)
    request = models.QuerySmsTemplateListRequest(
        page_size=1,
        page_index=1,
    )
    client.query_sms_template_list(request)


def send(cfg, secrets: dict, phone: str, template_param: dict) -> None:
    """阿里云 SMS 发码。secrets = {"access_key_id", "access_key_secret"}。

    template_param = {"code": "123456"} → JSON 字符串传 SDK。
    成功 return None；失败 raise Exception（API 层 asyncio.to_thread + try/except 分类）。
    """
    config = open_api_models.Config(
        access_key_id=secrets["access_key_id"],
        access_key_secret=secrets["access_key_secret"],
        endpoint=f"dysmsapi.{cfg.region or 'cn-hangzhou'}.aliyuncs.com",
        connect_timeout=15000,
        read_timeout=15000,
    )
    client = Client(config)
    request = models.SendSmsRequest(
        phone_numbers=phone,
        sign_name=cfg.sign_name,
        template_code=cfg.template_code,
        template_param=json.dumps(template_param, separators=(",", ":")),
    )
    resp = client.send_sms(request)
    if not resp.body or resp.body.code != "OK":
        msg = resp.body.message if resp.body else "no body"
        raise Exception(f"aliyun send_sms failed: {msg}")
