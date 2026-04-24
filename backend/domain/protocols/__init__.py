"""
Domain Protocols -- interface contracts between layers
======================================================
Every cross-layer dependency MUST go through a Protocol defined here.
Invariant #11: post-M1, any new cross-module interface needs a Protocol first.

Usage::

    from domain.protocols import CacheProtocol, StoreProtocol

Design doc: docs/design/12-framework-refactor.md
"""
from domain.protocols.cache import CacheProtocol
from domain.protocols.store import StoreProtocol
from domain.protocols.llm_client import LLMClientProtocol
from domain.protocols.data_source import DataSourceProtocol

__all__ = [
    "CacheProtocol",
    "StoreProtocol",
    "LLMClientProtocol",
    "DataSourceProtocol",
]
