"""
Domain Models -- business value objects
=======================================
All models are either @dataclass (for internal use) or Pydantic BaseModel
(when validation is needed at API boundaries).

Current contents:
  - LLMResponse: structured return type for all LLM calls

Design doc: docs/design/12-framework-refactor.md
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResponse:
    """Structured response from any LLM call.

    Maps 1:1 to the dict returned by LLMGateway.call_sync() in
    services/llm_gateway.py (lines 188-197).

    Fields::

        content          -- generated text (empty on error)
        reasoning        -- chain-of-thought from R1 (empty for V3)
        source           -- "ai" | "cache" | "rate_limited" | "no_key"
                            | "api_error" | "error"
        model            -- "deepseek-chat" | "deepseek-reasoner"
        tokens           -- total tokens consumed
        cache_hit_tokens -- prompt tokens served from provider cache
        cache_miss_tokens-- prompt tokens NOT in provider cache
        fallback         -- True if this is a degraded/error response
        error            -- error message (empty on success)

    frozen=True because LLM responses are immutable facts.
    """

    content: str = ""
    reasoning: str = ""
    source: str = ""
    model: str = ""
    tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    fallback: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        """Backward-compat: old code expects dict.

        Avoids changing 50+ call sites at once during strangler-fig migration.
        """
        return {
            "content": self.content,
            "reasoning": self.reasoning,
            "source": self.source,
            "model": self.model,
            "tokens": self.tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "fallback": self.fallback,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LLMResponse":
        """Construct from legacy gateway dict. Ignores unknown keys."""
        return cls(
            content=d.get("content", ""),
            reasoning=d.get("reasoning", ""),
            source=d.get("source", ""),
            model=d.get("model", ""),
            tokens=d.get("tokens", 0),
            cache_hit_tokens=d.get("cache_hit_tokens", 0),
            cache_miss_tokens=d.get("cache_miss_tokens", 0),
            fallback=d.get("fallback", False),
            error=d.get("error", ""),
        )
