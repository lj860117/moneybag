"""
MoneyBag Infrastructure Layer
==============================
Adapters to external systems: cache, persistence, LLM, data sources.

Dependency rule: infra/ depends on domain/ (for Protocols and models).
                 infra/ NEVER imports from api/ or use_cases/.

Design doc: docs/design/12-framework-refactor.md
"""
