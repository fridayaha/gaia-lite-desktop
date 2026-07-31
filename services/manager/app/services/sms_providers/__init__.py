"""短信服务商 provider 抽象层 — 按 provider 动态加载对应模块。

每个 provider 文件实现 `probe(cfg, secrets) -> None`（探活）和
`send(cfg, secrets, phone, template_param) -> None`（发码）两个接口。
成功 return None；失败 raise Exception。API 层用 `get_probe` / `get_sender`
取函数后用 asyncio.to_thread 在线程池调用，避免阻塞 event loop。
"""

import importlib

PROBERS = {
    "aliyun": "aliyun_provider",
    "tencent": "tencent_provider",
    "huawei": "huawei_provider",
}

SENDERS = {
    "aliyun": "aliyun_provider",
    "tencent": "tencent_provider",
    "huawei": "huawei_provider",
}


def get_probe(provider: str):
    """按 provider 名取 probe 函数。动态加载便于测试 monkeypatch。"""
    module_name = PROBERS.get(provider)
    if not module_name:
        return None
    module = importlib.import_module(f"app.services.sms_providers.{module_name}")
    return getattr(module, "probe")


def get_sender(provider: str):
    """按 provider 名取 send 函数。动态加载便于测试 monkeypatch。"""
    module_name = SENDERS.get(provider)
    if not module_name:
        return None
    module = importlib.import_module(f"app.services.sms_providers.{module_name}")
    return getattr(module, "send")
