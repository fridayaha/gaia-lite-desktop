"""邮件服务商 provider 抽象层 — 每个 provider 实现统一 probe/send 接口。

- probe(cfg, secrets) -> None：探活，调只读 list API
- send(cfg, secrets, to, subject, html_body) -> None：发邮件

成功 return None；失败 raise Exception（被 API 层包成 TestEmailResult.error 或 503）。
所有同步 SDK 调用通过 asyncio.to_thread 跑在线程池，避免阻塞 manager event loop。
"""

from .aliyun_provider import probe as aliyun_probe
from .aliyun_provider import send as aliyun_send
from .huawei_provider import probe as huawei_probe
from .huawei_provider import send as huawei_send
from .smtp_provider import probe as smtp_probe
from .smtp_provider import send as smtp_send
from .tencent_provider import probe as tencent_probe
from .tencent_provider import send as tencent_send

PROBERS = {
    "smtp": smtp_probe,
    "aliyun": aliyun_probe,
    "tencent": tencent_probe,
    "huawei": huawei_probe,
}

SENDERS = {
    "smtp": smtp_send,
    "aliyun": aliyun_send,
    "tencent": tencent_send,
    "huawei": huawei_send,
}


def get_probe(provider: str):
    return PROBERS.get(provider)


def get_sender(provider: str):
    return SENDERS.get(provider)
