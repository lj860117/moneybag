"""
LLMClient -- adapter over existing LLMGateway
===============================================
Delegates to services.llm_gateway.LLMGateway.instance() for ALL real work.
This is a thin strangler-fig adapter:

  - New code imports from infra.llm and gets LLMClientProtocol-shaped objects
  - Under the hood, the 520-line LLMGateway does the actual work
  - In M2, the real implementation moves here and LLMGateway is retired

IMPORTANT: All imports of services.llm_gateway are lazy (inside methods, not
at module level) to avoid circular imports and load-order issues.

Design doc: docs/design/12-framework-refactor.md
Satisfies: domain.protocols.LLMClientProtocol (structural subtyping)
Invariant #3: All LLM calls through infra/llm/gateway.
"""
from __future__ import annotations

from typing import Dict

from domain.models import LLMResponse


class LLMClient:
    """Adapter satisfying LLMClientProtocol by delegating to legacy LLMGateway.

    All imports of services.llm_gateway are deferred to method calls (not module
    level) to avoid circular imports and to allow this module to be importable
    even if services.llm_gateway hasn't been loaded yet.
    """

    def call(
        self,
        prompt: str,
        *,
        system: str = "",
        model_tier: str = "llm_light",
        user_id: str = "",
        module: str = "",
        max_tokens: int = 800,
    ) -> LLMResponse:
        """Delegate to LLMGateway.call_sync(), return typed LLMResponse."""
        gw = self._gateway()
        raw = gw.call_sync(
            prompt,
            system=system,
            model_tier=model_tier,
            user_id=user_id,
            module=module,
            max_tokens=max_tokens,
        )  # type: dict
        return LLMResponse.from_dict(raw)

    def get_usage(self, user_id: str = "") -> Dict:
        """Delegate to LLMGateway usage tracking."""
        gw = self._gateway()
        return gw.get_usage(user_id)

    def get_daily_remaining(self) -> int:
        """Return remaining daily LLM calls."""
        gw = self._gateway()
        return gw.get_daily_remaining()

    @staticmethod
    def _gateway():
        """Lazy import to avoid circular deps and load-order issues."""
        from services.llm_gateway import LLMGateway  # noqa: delay import
        return LLMGateway.instance()
