"""对话页多轮历史窗口 —— 单一口径 + token 预算兜底 的守卫测试。

背景（2026-09-20）：
    三处口径长期不一致 —— 前端 `pages/chat.js` 写死 `slice(-21,-1)` 发 20 条，
    后端 `infra/llm/gateway.py` 实际只取 `history[-10:]`，`models/schemas.py`
    的注释又写着「最近5轮」。前端多发的一倍被静默丢弃，且改一处必漏另一处。

    现在统一为 config.CHAT_HISTORY_WINDOW + config.CHAT_HISTORY_TOKEN_BUDGET，
    唯一实现是 `infra.llm.gateway._select_chat_history()`；前端通过
    `/api/models` 下发的 `chat_history_window` 拿同一个值。

本文件守住三件事：
    1. 窗口行为正确（条数截断 + 预算兜底 + 最新一定保留 + 脏数据过滤）
    2. 窗口**没被写死**在 gateway 里 —— 改 config 常量行为必须跟着变（故障注入）
    3. 前端**不再写死** —— 禁止 `slice(-21` 这类硬编码复活
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import config
from infra.llm.gateway import _select_chat_history

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = REPO_ROOT / "pages" / "chat.js"


class _Msg:
    """模拟非 dict 形态的历史条目（有些调用方传的是对象）。"""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


def _hist(n: int, chars: int = 10) -> list[dict[str, str]]:
    """造 n 条交替 user/assistant 的历史，每条 content 长度 = chars。"""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": ("x" * chars) + str(i)}
        for i in range(n)
    ]


# ── 1. 行为正确性 ───────────────────────────────────────────────────


def test_window_takes_only_last_n():
    """只保留最近 CHAT_HISTORY_WINDOW 条，顺序不能乱。"""
    window = int(config.CHAT_HISTORY_WINDOW)
    out = _select_chat_history(_hist(window + 7))
    assert len(out) == window
    # 保留的是「最新的那批」：最后一条应等于输入的最后一条
    assert out[-1]["content"].endswith(str(window + 6))


def test_token_budget_drops_oldest_and_keeps_newest():
    """超预算时从最旧往回丢，但**最新一条一定在**。

    这是最关键的一条：宁可让 AI 忘了很久以前说了什么，
    也不能让它忘了上一轮刚说过什么。
    """
    window = int(config.CHAT_HISTORY_WINDOW)
    budget = int(config.CHAT_HISTORY_TOKEN_BUDGET)
    # 每条都「单独就超预算」→ 兜底生效后应只剩最新 1 条
    huge = _hist(window, chars=int(budget * 3))
    out = _select_chat_history(huge)
    assert len(out) == 1
    assert out[0]["content"] == huge[-1]["content"]


def test_budget_not_triggered_when_history_is_small():
    """预算兜底只在真的超了才介入，正常短历史不该被砍到 1 条。"""
    out = _select_chat_history(_hist(4, chars=20))
    assert len(out) == 4


def test_filters_invalid_role_and_empty_content():
    out = _select_chat_history([
        {"role": "system", "content": "不该进历史"},
        {"role": "user", "content": ""},
        {"role": "tool", "content": "工具结果也不该进"},
        {"role": "user", "content": "真问题"},
        {"role": "assistant", "content": "真回答"},
    ])
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert [m["content"] for m in out] == ["真问题", "真回答"]


def test_accepts_object_shaped_history():
    """调用方可能传对象而非 dict（历史上有过），不能因此崩掉。"""
    out = _select_chat_history([_Msg("user", "问"), _Msg("assistant", "答")])
    assert out == [{"role": "user", "content": "问"}, {"role": "assistant", "content": "答"}]


def test_empty_and_none_history_is_safe():
    assert _select_chat_history([]) == []
    assert _select_chat_history(None) == []


# ── 2. 窗口没被写死在 gateway 里（故障注入）─────────────────────────


def test_window_follows_config_not_hardcoded(monkeypatch):
    """把 config 的窗口改成别的值，行为必须跟着变。

    这条是**故障注入**：若有人又把 10 写回 gateway，本测试会立刻红。
    """
    monkeypatch.setattr(config, "CHAT_HISTORY_WINDOW", 4, raising=True)
    assert len(_select_chat_history(_hist(12))) == 4

    monkeypatch.setattr(config, "CHAT_HISTORY_WINDOW", 20, raising=True)
    assert len(_select_chat_history(_hist(30))) == 20


def test_budget_follows_config_not_hardcoded(monkeypatch):
    monkeypatch.setattr(config, "CHAT_HISTORY_TOKEN_BUDGET", 10, raising=True)
    # 预算 10 token ≈ 16 字符；每条 100 字符 → 只剩最新 1 条
    out = _select_chat_history(_hist(3, chars=100))
    assert len(out) == 1


def test_window_constant_is_sane():
    """常量本身也要合理：不能是 0/负数，也不能大到把输入顶穿。"""
    assert int(config.CHAT_HISTORY_WINDOW) >= 2
    assert int(config.CHAT_HISTORY_WINDOW) <= 40
    assert int(config.CHAT_HISTORY_TOKEN_BUDGET) > 0
    # 历史预算必须显著小于单次 input 上限，否则历史就能单独把调用顶到拒绝线
    assert int(config.CHAT_HISTORY_TOKEN_BUDGET) < int(config.TOKEN_BUDGET["max_input_per_call"])


# ── 3. 前端不再写死（静态守卫）──────────────────────────────────────


def test_frontend_does_not_hardcode_window():
    """前端必须按后端下发的值取历史，禁止硬编码的数字切片复活。

    判据用正则 `chatMessages.slice(-<数字>` 而不是裸字符串 `slice(-21`：
    后者会把**注释里**提到的历史写法也判成违规（本次就这么误报过一次），
    而真正要防的是「代码里又写死一个数字」。
    """
    assert CHAT_JS.exists(), f"找不到前端文件 {CHAT_JS}"
    src = CHAT_JS.read_text(encoding="utf-8")

    hardcoded = re.findall(r"chatMessages\s*\.\s*slice\(\s*-\s*\d+", src)
    assert not hardcoded, (
        f"pages/chat.js 出现了写死的切片 {hardcoded}：前后端窗口会再次漂移。"
        "请改用 chatHistoryWindow（由 /api/models 从 config.CHAT_HISTORY_WINDOW 下发）。"
    )
    assert "chatHistoryWindow" in src
    assert "slice(-(chatHistoryWindow+1)" in src


def test_api_models_publishes_the_same_window():
    """`/api/models` 下发的窗口值必须就是 config.CHAT_HISTORY_WINDOW。"""
    from api.chat import list_models

    data: Any = list_models()
    assert "chat_history_window" in data
    assert data["chat_history_window"] == int(config.CHAT_HISTORY_WINDOW)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
