"""
LLM Gateway — deprecated 转发壳（实现本体已迁至 infra/llm/gateway.py）
=====================================================================
2026-09-06 strangler-fig 阶段 1：实现本体迁入 infra/llm/gateway.py，
本文件退化为兼容转发壳，保持 50+ 调用点（api/services/scripts/routers/
domain/use_cases）的既有 `from services.llm_gateway import ...` 可用。

⚠️ 请勿在本文件新增实现。新代码一律走 `infra/llm/gateway`（不变式 #3）。

设计文档：docs/design/14-llm-gateway-migration-map.md
"""
from __future__ import annotations

from infra.llm.gateway import (  # noqa: F401
    # 常量
    MODEL_ROUTING,
    DOUBAO_MODEL_ROUTING,
    INTERACTIVE_AUTO_MODULES,
    DAILY_LIMIT,
    BURST_LIMIT,
    BURST_WINDOW,
    CACHE_TTL,
    MODULE_META,
    # 类
    LLMGateway,
    LLMClient,
    # 纯函数
    resolve_default_model,
    resolve_model_candidates,
    # 便捷函数
    llm_call,
    llm_usage,
)

# 显式 re-export（供 `from services.llm_gateway import *` 场景）
__all__ = [
    "MODEL_ROUTING",
    "DOUBAO_MODEL_ROUTING",
    "INTERACTIVE_AUTO_MODULES",
    "DAILY_LIMIT",
    "BURST_LIMIT",
    "BURST_WINDOW",
    "CACHE_TTL",
    "MODULE_META",
    "LLMGateway",
    "LLMClient",
    "resolve_default_model",
    "resolve_model_candidates",
    "llm_call",
    "llm_usage",
]
