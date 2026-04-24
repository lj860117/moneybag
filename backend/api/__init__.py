"""
MoneyBag API Layer
==================
FastAPI routers, parameter validation, response serialization.
Each file corresponds to a business domain, < 400 lines.

Dependency rule: api/ -> use_cases/ -> domain/ -> infra/ (never backward).

Design doc: docs/design/12-framework-refactor.md
"""
