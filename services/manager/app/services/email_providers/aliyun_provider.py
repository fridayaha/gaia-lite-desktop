"""阿里云 DirectMail 探活 + 发码 — alibabacloud_dm20151123 SDK。

- probe: 调只读 list API QueryDomainByParam（page_size=1）验证 AK/SK + region
- send: 调 SingleSendMail API 发邮件
成功 return None；失败 raise Exception。
"""


def probe(cfg, secrets: dict) -> None:
    """阿里云 DirectMail 探活。secrets = {"access_key_id": "...", "access_key_secret": "..."}"""
    from alibabacloud_dm20151123 import models
    from alibabacloud_dm20151123.client import Client
    from alibabacloud_tea_openapi import models as open_api_models

    region_id = cfg.region or "cn-hangzhou"
    config = open_api_models.Config(
        access_key_id=secrets["access_key_id"],
        access_key_secret=secrets["access_key_secret"],
        region_id=region_id,
        endpoint=f"dm.{region_id}.aliyuncs.com",
        connect_timeout=15000,
        read_timeout=15000,
    )
    client = Client(config)
    request = models.QueryDomainByParamRequest(
        page_no=1,
        page_size=1,
    )
    client.query_domain_by_param(request)


def send(cfg, secrets: dict, to: str, subject: str, html_body: str) -> None:
    """阿里云 DirectMail 发邮件。secrets = {"access_key_id", "access_key_secret"}。

    cfg.from_email 是发信地址（AccountName）；AddressType=1 表示外发。
    成功 return None；失败 raise Exception。
    """
    from alibabacloud_dm20151123 import models
    from alibabacloud_dm20151123.client import Client
    from alibabacloud_tea_openapi import models as open_api_models

    region_id = cfg.region or "cn-hangzhou"
    config = open_api_models.Config(
        access_key_id=secrets["access_key_id"],
        access_key_secret=secrets["access_key_secret"],
        region_id=region_id,
        endpoint=f"dm.{region_id}.aliyuncs.com",
        connect_timeout=15000,
        read_timeout=15000,
    )
    client = Client(config)
    request = models.SingleSendMailRequest(
        account_name=cfg.from_email,
        address_type=1,  # 1=外发
        to_address=to,
        subject=subject,
        html_body=html_body,
        from_alias=cfg.from_name or "",
    )
    resp = client.single_send_mail(request)
    if not resp.body or resp.body.env_id is None and resp.body.message_id is None:
        raise Exception(f"aliyun single_send_mail failed: {resp.body}")
