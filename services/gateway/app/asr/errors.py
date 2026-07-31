"""ASR provider 异常。"""


class AsrError(Exception):
    """ASR 调用异常。

    由 provider 在初始化失败（凭据缺失）或调用失败（网络/HTTP 错误）时抛出，
    wecom.transcribe 捕获后回兜底提示"语音识别失败，请重试"。
    """
