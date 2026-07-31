"""敏感字符串掩码工具。"""
from __future__ import annotations


def mask_secret(value: str | None, prefix: int = 4, suffix: int = 4) -> str:
    """掩码敏感字符串：保留前 prefix + 后 suffix 字符，中间 **** 替代。

    长度不足 prefix+suffix 时全返回 ****。None/空返回 "—"。
    用于详情页只读展示 api_key/token 等字段，明文编辑走定义表单页。
    """
    if not value:
        return "—"
    if len(value) <= prefix + suffix:
        return "****"
    return f"{value[:prefix]}****{value[-suffix:]}"
