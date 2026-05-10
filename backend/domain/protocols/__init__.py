"""
Domain Protocols -- interface contracts between layers
======================================================
Every cross-layer dependency MUST go through a Protocol defined here.
Invariant #11: post-M1, any new cross-module interface needs a Protocol first.

Usage::

    from domain.protocols import CacheProtocol, StoreProtocol, FamilyProfileProtocol

Design doc: docs/design/12-framework-refactor.md
"""
from domain.protocols.cache import CacheProtocol
from domain.protocols.store import StoreProtocol
from domain.protocols.llm_client import LLMClientProtocol
from domain.protocols.data_source import DataSourceProtocol
from domain.protocols.family_profile import FamilyProfileProtocol
from domain.protocols.balance_sheet import BalanceSheetProtocol
from domain.protocols.decision_guard import DecisionGuardProtocol
from domain.protocols.knowledge_retriever import KnowledgeRetrieverProtocol
from domain.protocols.question_bank import QuestionBankProtocol
from domain.protocols.report_generator import ReportGeneratorProtocol
from domain.protocols.multi_view_advisor import MultiViewAdvisorProtocol

__all__ = [
    "CacheProtocol",
    "StoreProtocol",
    "LLMClientProtocol",
    "DataSourceProtocol",
    "FamilyProfileProtocol",
    "BalanceSheetProtocol",
    "DecisionGuardProtocol",
    "KnowledgeRetrieverProtocol",
    "QuestionBankProtocol",
    "ReportGeneratorProtocol",
    "MultiViewAdvisorProtocol",
]
