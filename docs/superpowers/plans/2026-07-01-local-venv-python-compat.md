# 本地 .venv Python 兼容性修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让钱袋子项目本地 `.venv` 与项目声明/线上运行时对齐，避免完整回归因 Python 3.9 不支持 `X | None` 注解而失败。

**Architecture:** 这次不去全仓库倒退兼容 Python 3.9，而是把本地虚拟环境升级到项目已声明的 Python 3.11。代码层只补最小校验与验证，不引入无意义的大面积注解改写。这样本地、mypy 配置、线上环境三者更一致。

**Tech Stack:** Python 3.11 venv、pytest、requirements.txt、MoneyBag backend

---

### Task 1: 复现并锁定根因

**Files:**
- Modify: `docs/superpowers/plans/2026-07-01-local-venv-python-compat.md`
- Read: `pyproject.toml`
- Read: `backend/.python-version`
- Read: `backend/services/fund_rank.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 运行本地 .venv 与完整回归**

Run: `"/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -V && "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -m pytest "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_regression_signal_and_cache.py" -q`
Expected: `Python 3.9.x`，并在 `backend/services/fund_rank.py` 的 `Path | None` 注解处失败。

- [ ] **Step 2: 对照项目声明版本**

Read and verify:
```toml
# pyproject.toml
[tool.mypy]
python_version = "3.11"
```
```text
# backend/.python-version
3.11
```
Expected: 项目本来就声明本地应跑 3.11，不是代码偶发坏掉。

### Task 2: 重建本地虚拟环境到 Python 3.11

**Files:**
- Modify: `.venv/`（本地虚拟环境）
- Read: `requirements.txt`
- Read: `requirements-dev.txt`

- [ ] **Step 1: 备份旧环境目录名**

Run: `mv .venv .venv-py39-backup-20260701`
Expected: 保留旧环境可回退，不直接硬删。

- [ ] **Step 2: 用 Python 3.11.9 重建 `.venv`**

Run: `"/Users/leijiang/.workbuddy/binaries/python/versions/3.11.9/bin/python3" -m venv .venv`
Expected: `.venv/bin/python -V` 输出 `Python 3.11.9` 或同级 3.11.x。

- [ ] **Step 3: 安装依赖**

Run: `"/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/pip" install -r "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/requirements.txt" -r "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/requirements-dev.txt"`
Expected: pytest 与项目依赖可用。

### Task 3: 验证并沉淀规范

**Files:**
- Modify: `/Users/leijiang/WorkBuddy/2026-06-16-08-51-23/.workbuddy/memory/2026-07-01.md`
- Modify: `/Users/leijiang/WorkBuddy/2026-06-16-08-51-23/.workbuddy/memory/MEMORY.md`
- Modify: `/Users/leijiang/.workbuddy/skills/moneybag-feature-deploy/SKILL.md`

- [ ] **Step 1: 重新运行完整回归**

Run: `"/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -m pytest "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_regression_signal_and_cache.py" -q`
Expected: 所有测试通过，不再因 `Path | None` 崩在导入阶段。

- [ ] **Step 2: 记录环境对齐规则**

Add note:
```markdown
- MoneyBag 本地 `.venv` 必须跟 `backend/.python-version` / `pyproject.toml` 对齐到 Python 3.11；如果 `.venv/bin/python -V` 仍是 3.9，优先重建环境，不要为了兼容旧环境去倒改大量 `X | None` 注解。
```

- [ ] **Step 3: 最终验证**

Run: `"/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -V`
Expected: 输出 3.11.x，说明本地执行基线已与项目一致。
