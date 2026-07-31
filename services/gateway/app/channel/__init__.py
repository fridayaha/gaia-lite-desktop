"""IM Channel integration for AgentGateway.

Importing concrete adapters triggers @register decorators.
"""
# 导入适配器模块触发注册（装饰器在 import 时执行）
from . import wecom
from . import wecom_bot_callback
from . import feishu
from . import dingtalk
