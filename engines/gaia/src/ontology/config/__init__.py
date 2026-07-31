"""Configuration management — settings, database, DI container."""

from ontology.config.database import async_session_factory, engine, get_session
from ontology.config.settings import settings

__all__ = [
    "settings",
    "engine",
    "async_session_factory",
    "get_session",
]
