"""
LLMClientProtocol -- LLM gateway contract
===========================================
Abstracts the 520-line LLMGateway singleton in services/llm_gateway.py.

Current gateway provides: model routing, caching, rate limiting, cost tracking.
This protocol exposes the calling interface; implementations handle the rest.

Model tiers (from existing LLMGateway):
  - "llm_light" -> deepseek-v4-flash (V4 Flash: commentary, interpretation, chat)
  - "llm_heavy" -> deepseek-v4-pro (V4 Pro: arbitration, diagnosis, factor gen)
  - "llm_reasoning" -> deepseek-reasoner (R1: scenario analysis only)

Implementations:
  - infra.llm.gateway.LLMClient -- adapter over existing LLMGateway (M1 Day 1)
  - infra.llm.gateway.LLMClient -- standalone replacement (planned M2)

Design doc: docs/design/12-framework-refactor.md
Invariant #3: All LLM calls through infra/llm/gateway.
"""
from __future__ import annotations

from typing import Any, Dict, Generator, List, Protocol, runtime_checkable

from domain.models import LLMResponse


@runtime_checkable
class LLMClientProtocol(Protocol):
    """Structural interface for LLM invocations."""

    def call(
        self,
        prompt: str,
        *,
        system: str = ...,
        model_tier: str = ...,
        user_id: str = ...,
        module: str = ...,
        max_tokens: int = ...,
    ) -> LLMResponse:
        """Send a prompt to the LLM and return a structured response.

        Implementations MUST:
          - Route to the correct model based on model_tier
          - Apply rate limiting (daily + burst)
          - Cache identical prompts (1h TTL)
          - Return a fallback LLMResponse (fallback=True) on failure, never raise
          - Record usage for billing/monitoring
        """
        ...

    def stream(
        self,
        prompt: str,
        *,
        system: str = ...,
        model_tier: str = ...,
        user_id: str = ...,
        module: str = ...,
        max_tokens: int = ...,
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream LLM response as incremental chunks.

        Yields dicts: {"delta": str, "phase": "thinking"|"answering", "done": bool}
        Final chunk: {"delta": "", "done": True, "model": str, "tokens": int}
        Error: {"delta": "", "done": True, "error": str, "fallback": True}

        Does NOT cache (streaming is inherently non-cacheable).
        DOES apply rate limiting and cost recording.
        """
        ...

    def call_multimodal(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str = ...,
        user_id: str = ...,
        module: str = ...,
        max_tokens: int = ...,
    ) -> Dict[str, Any]:
        """Call LLM with pre-assembled multimodal messages (vision/image).

        Messages may contain image_url content blocks.
        Returns same dict format as call_sync: {content, source, model, tokens, fallback}.
        Does NOT cache (image content not hashable).
        DOES apply rate limiting and cost recording.
        """
        ...

    def get_usage(self, user_id: str = ...) -> Dict[str, object]:
        """Return usage stats.

        If user_id is given, filter to that user.
        Returns dict with at minimum: {calls, tokens, cost_rmb}
        """
        ...

    def get_daily_remaining(self) -> int:
        """Return how many LLM calls remain today (out of daily limit)."""
        ...
