"""Channel adapter registry — lookup adapters by channel type."""
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)

_registry: dict[str, type["BaseChannelAdapter"]] = {}


def register(channel_type: str):
    """Decorator to register a channel adapter class."""
    def decorator(cls):
        cls.channel_type = channel_type
        _registry[channel_type] = cls
        logger.debug("Registered channel adapter: %s", channel_type)
        return cls
    return decorator


def get_adapter(channel_type: str, config: dict) -> "BaseChannelAdapter | None":
    """Create an adapter instance for the given channel type and config."""
    cls = _registry.get(channel_type)
    if not cls:
        logger.warning("No adapter registered for channel type: %s", channel_type)
        return None
    return cls(config)


def list_supported() -> list[str]:
    return list(_registry.keys())
