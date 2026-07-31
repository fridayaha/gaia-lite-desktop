"""腾讯云 SMS 探活 + 发码 — 探活调 DescribeSmsTemplateList，发码调 SendSms。"""

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.sms.v20210111 import sms_client, models


def probe(cfg, secrets: dict) -> None:
    """腾讯云 SMS 探活。secrets = {"access_key_id": "...", "access_key_secret": "..."}

    调用只读 list 模板 API（limit=1），成功 = AK/SK + region 可用；
    失败抛异常由调用方分类转中文错误信息。
    """
    cred = credential.Credential(
        secrets["access_key_id"], secrets["access_key_secret"]
    )
    http_profile = HttpProfile()
    http_profile.endpoint = "sms.tencentcloudapi.com"
    http_profile.req_timeout = 15
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = sms_client.SmsClient(cred, cfg.region or "ap-guangzhou", client_profile)

    req = models.DescribeSmsTemplateListRequest()
    req.Limit = 1
    req.Offset = 0
    req.International = 0  # 0=国内, 1=海外
    client.DescribeSmsTemplateList(req)


def send(cfg, secrets: dict, phone: str, template_param: dict) -> None:
    """腾讯云 SMS 发码。secrets = {"access_key_id", "access_key_secret"}。

    template_param = {"code": "123456"} → 取 values 转 list 传 TemplateParamSet。
    成功 return None；失败 raise Exception（SDK 抛 TencentCloudSDKException）。
    """
    cred = credential.Credential(
        secrets["access_key_id"], secrets["access_key_secret"]
    )
    http_profile = HttpProfile()
    http_profile.endpoint = "sms.tencentcloudapi.com"
    http_profile.req_timeout = 15
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = sms_client.SmsClient(cred, cfg.region or "ap-guangzhou", client_profile)

    # 腾讯云模板参数是按顺序的 list，从 dict 取 values
    param_set = list(template_param.values())

    req = models.SendSmsRequest()
    req.SmsSdkAppId = cfg.sdk_app_id
    req.SignName = cfg.sign_name
    req.TemplateId = cfg.template_code
    req.PhoneNumberSet = [phone if phone.startswith("+") else f"+86{phone}"]
    req.TemplateParamSet = param_set
    resp = client.SendSms(req)
    if not resp.SendStatusSet or resp.SendStatusSet[0].Code != "Ok":
        item = resp.SendStatusSet[0] if resp.SendStatusSet else None
        msg = item.Message if item else "no status"
        raise Exception(f"tencent send_sms failed: {msg}")
