"""腾讯云 SES 探活 + 发码 — tencentcloud-sdk-python-ses。

- probe: 调只读 list API ListEmailAddress 验证 AK/SK + region
- send: 调 SendEmail API 发邮件
成功 return None；失败 raise Exception。
"""


def probe(cfg, secrets: dict) -> None:
    """腾讯云 SES 探活。secrets = {"access_key_id": "...", "access_key_secret": "..."}"""
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.ses.v20201002 import models, ses_client

    cred = credential.Credential(
        secrets["access_key_id"], secrets["access_key_secret"]
    )
    http_profile = HttpProfile()
    http_profile.endpoint = "ses.tencentcloudapi.com"
    http_profile.req_timeout = 15
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = ses_client.SesClient(cred, cfg.region or "ap-hongkong", client_profile)

    req = models.ListEmailAddressRequest()
    client.ListEmailAddress(req)


def send(cfg, secrets: dict, to: str, subject: str, html_body: str) -> None:
    """腾讯云 SES 发邮件。secrets = {"access_key_id", "access_key_secret"}。

    cfg.from_email 是发信地址；Destination 是 list。成功 return None；失败 raise Exception。
    """
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.ses.v20201002 import models, ses_client

    cred = credential.Credential(
        secrets["access_key_id"], secrets["access_key_secret"]
    )
    http_profile = HttpProfile()
    http_profile.endpoint = "ses.tencentcloudapi.com"
    http_profile.req_timeout = 15
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = ses_client.SesClient(cred, cfg.region or "ap-hongkong", client_profile)

    req = models.SendEmailRequest()
    req.FromEmailAddress = cfg.from_email
    req.Destination = [to]
    req.Subject = subject
    req.HtmlBody = html_body
    #腾讯云 Tencent SES 不要求 from_name 单独字段，发件人显示名在 from_email 里组合
    resp = client.SendEmail(req)
    if not resp.Response or not resp.Response.MessageId:
        raise Exception(f"tencent send_email failed: {resp.Response.Error.Message if resp.Response.Error else 'no message id'}")
