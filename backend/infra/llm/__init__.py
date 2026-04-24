"""
LLM infrastructure -- gateway implementations.
Invariant #3: All LLM calls through infra/llm/gateway.
"""
from infra.llm.gateway import LLMClient

__all__ = ["LLMClient"]
