"""
MoneyBag Use Cases Layer
=========================
Application orchestration. One user action / one cron job = one use case file.
Coordinates domain services and infra adapters. No business rules here.

Dependency rule: use_cases/ -> domain/ -> infra/ (never backward to api/).

Design doc: docs/design/12-framework-refactor.md
"""
