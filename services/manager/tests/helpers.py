"""测试共享工具（FakeObj + mock result 构建器）。"""

from unittest.mock import MagicMock
from uuid import UUID
from datetime import datetime


class FakeObj:
    """按属性存取真实值的简单对象，模拟 SQLAlchemy ORM row。"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_mock_result(return_value):
    """
    模拟 SQLAlchemy execute() 返回值链：
    Result.scalars().first() → return_value (单对象)
    Result.scalars().all() → [return_value] 或 return_value (列表)
    Result.unique().scalars().all() → 同上
    Result.scalar() → count
    """
    mock_result = MagicMock()

    # scalar_one_or_none() — 用于 get/update/publish/offline 等单条查询
    mock_result.scalar_one_or_none.return_value = return_value

    # scalars() 子链 — 用于 list 查询
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = return_value
    if isinstance(return_value, list):
        mock_scalars.all.return_value = return_value
    else:
        mock_scalars.all.return_value = [return_value] if return_value else []
    mock_result.scalars.return_value = mock_scalars

    mock_unique = MagicMock()
    mock_unique_scalars = MagicMock()
    if isinstance(return_value, list):
        mock_unique_scalars.all.return_value = return_value
    else:
        mock_unique_scalars.all.return_value = [return_value] if return_value else []
    mock_unique.scalars.return_value = mock_unique_scalars
    mock_result.unique.return_value = mock_unique

    if isinstance(return_value, list):
        mock_result.scalar.return_value = len(return_value)
    else:
        mock_result.scalar.return_value = 1 if return_value else 0

    return mock_result
