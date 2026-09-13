"""守卫：规则引擎的 take_profit / dca 分支不得被"决策意图"拦截成死代码。

## 事故背景（2026-09-13 发现）

`api/shared_helpers.py` 的 `_rule_based_reply_structured` 里有一段：

    _has_decision_intent = any(k in msg_lower for k in _DECISION_KW)
    if _has_decision_intent:
        return None          # 注释写的是"含有决策意图的【持仓问题】"

但「【持仓问题】」这层限定**只写在注释里，从未实现**。而 `_DECISION_KW` 里含
"定投" / "止盈" / "止损" 这些**通用咨询词**，于是用户问「止盈」「定投多少合适」
也被拦到 LLM：

  · `:1321` 的 take_profit 规则（那张完整止盈止损表）**成了死代码**
  · `:1326` 的 dca 规则（智能定投倍率表）**成了死代码**
  · 代价：毫秒级确定性回答 → 秒级 LLM 调用 + 真实费用

该缺陷 2026-05-24 (`80b67cd`) 引入，**存活 4 个月无人发现** —— 因为覆盖它的
`tests/test_chat_fast_path.py` 是 HTTP e2e：本地因"后端未启动"整文件 skip，
服务器上又从不跑根 tests/。**测试写了，但从来没有执行过。**

## 本文件为什么放在 backend/tests/

`backend/tests/` 是**唯一会在服务器上真正执行**的套件（根 tests/ 已加生产机自锁，
见 Pitfall 20）。把这个守卫放在这里，才能保证它每轮回归都真的跑，而不是又一次
"被 skip 掩盖"。且它是**纯函数调用**（不起 HTTP、不打生产数据），符合本目录定位。

## 断言

1. 通用咨询（不含自我指涉）必须命中规则 —— 这是修复的核心
2. 持仓决策（含"我的 / 我持有"等）必须放行给 LLM —— 保证修复没有矫枉过正
3. 反空转：确认函数真的被调用过且有非 None 返回，否则断言是死的
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def rule_engine():
    """导入规则引擎入口（模块级缓存，避免每个用例重复 import）。"""
    from api.shared_helpers import _rule_based_reply_structured

    return _rule_based_reply_structured


# --- 1. 通用咨询：必须命中规则（修复前这两条会返回 None → 掉到 LLM）---------
_GENERAL_CONSULT = [
    ("止盈", "take_profit"),
    ("定投多少合适", "dca"),
    ("现在能进场吗", "timing"),
    ("市场情绪怎么样", "sentiment"),
]


@pytest.mark.parametrize("msg,expected_intent", _GENERAL_CONSULT)
def test_general_consult_hits_rule_engine(rule_engine, msg, expected_intent):
    """通用咨询（没有提到自己持仓）应走确定性规则，而不是 LLM。"""
    r = rule_engine(msg, "", "")
    assert r is not None, (
        f"「{msg}」未命中规则引擎，被 _DECISION_KW 提前 return None 拦到了 LLM —— "
        f"这正是 2026-05-24 (`80b67cd`) 引入的死代码缺陷：注释写着"
        f"『含有决策意图的**持仓问题**』，实现却漏了『持仓』这层限定。"
        f"检查 `_is_holding_decision` 是否被改回了 `_has_decision_intent`。"
    )
    assert r.get("intent") == expected_intent, (
        f"「{msg}」命中了规则但 intent={r.get('intent')!r}，期望 {expected_intent!r}"
    )
    assert r.get("deterministic") is True, (
        f"「{msg}」命中规则却没带 deterministic=True，调用方会判为不可采用而 fall through 到 LLM"
    )


# --- 2. 持仓决策：必须放行给 LLM（保证修复没有矫枉过正）-------------------
_HOLDING_DECISION = [
    "我持有的基金要止盈吗",
    "我的持仓要不要减仓",
    "我有多少股票该卖掉",
    "我这只基金要不要继续定投",
]


@pytest.mark.parametrize("msg", _HOLDING_DECISION)
def test_holding_decision_goes_to_llm(rule_engine, msg):
    """提到了自己持仓的决策问题，规则引擎只能列持仓、不能给建议，必须交 LLM。"""
    r = rule_engine(msg, "", "")
    assert r is None, (
        f"「{msg}」是**持仓决策**，应当返回 None 交给 LLM 做个性化分析，"
        f"实际却命中了规则 intent={r.get('intent') if r else None!r}。"
        f"说明 `_SELF_HOLDING_KW` 自我指涉判据被放宽过头了。"
    )


# --- 3. 反空转 -----------------------------------------------------------
def test_scan_is_not_vacuous(rule_engine):
    """反空转：确认规则引擎真的返回过结果，否则上面的断言可能全是死的。

    本项目吃过「闸门空转仍显绿」的教训 —— 如果规则引擎因为某个 import 错误
    整体返回 None，那第 2 组断言会全部"通过"，而第 1 组会红，但红的原因会被
    误读成"缺陷复现"。这里显式把"至少有一条通用咨询能命中"钉住。
    """
    hits = [m for m, _ in _GENERAL_CONSULT if rule_engine(m, "", "") is not None]
    assert hits, (
        "所有通用咨询都未命中规则引擎 —— 规则引擎整体失效（可能是 import 错误或"
        "数据源异常），此时本文件的断言失去判别力。请先排查规则引擎本身。"
    )
