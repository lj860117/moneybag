#!/usr/bin/env python3
"""v9.9.30 事实锚点：空锚点必须返回**可区分状态**（回归 + 故障注入）。

背景（审计结论 B）：
    fact_anchor.check_fact_anchors 在"拿不到锚点"时 `return []`，而"校验通过"
    同样返回 `[]`。调用方（stock_monitor_cron / night_worker）只能用
    `if findings:` 判断，于是"这次根本没查"被静默读成"数字全部核对过了"。

    放行本身**是对的**（没有证据时"宁杀错"会误伤正常推送），错的只是
    两种语义长得一模一样。

本文件锁死：
  1. 无锚点 → anchor_state="no_anchors"，与 verified 可区分；
  2. 向后兼容：仍是 list（`findings == []` / `if findings:` / len 语义不变）；
  3. 故障注入：旧实现 `return []` 没有任何状态位 —— 本文件的断言在旧实现下必红。
"""
from __future__ import annotations

import unittest
from unittest import mock

from services import fact_anchor
from services.fact_anchor import (
    ANCHOR_STATE_ERROR,
    ANCHOR_STATE_NO_ANCHORS,
    ANCHOR_STATE_VERIFIED,
    AnchorFindings,
    check_fact_anchors,
    guard_fact_anchors,
)

PACKET = """## 持仓数据
- 贵州茅台 600519: 现价1520.30, 涨跌 +1.20%
## 盈亏锚点
组合收益率 +2.35%
"""
FALLBACK = "（AI 输出含无法核实的数据，已拦截）"


class TestAnchorStateIsDistinguishable(unittest.TestCase):
    def test_no_anchor_state_differs_from_verified_state(self):
        """核心断言：无锚点 与 校验通过 返回的状态必须不同。"""
        text = "今天涨了 1.20%。"

        out, no_anchor = guard_fact_anchors(text, "", fallback=FALLBACK)
        # 行为不变：仍然放行（不误杀正常推送）
        self.assertEqual(out, text)
        # 向后兼容：仍是 list，`== []` 成立
        self.assertEqual(no_anchor, [])
        self.assertEqual(len(no_anchor), 0)

        _, verified = guard_fact_anchors(text, PACKET, fallback=FALLBACK)
        self.assertEqual(verified, [])

        # 可区分：一个"没查"，一个"查过没问题"
        self.assertEqual(no_anchor.anchor_state, ANCHOR_STATE_NO_ANCHORS)
        self.assertEqual(verified.anchor_state, ANCHOR_STATE_VERIFIED)
        self.assertNotEqual(no_anchor.anchor_state, verified.anchor_state)
        self.assertFalse(no_anchor.verified)
        self.assertTrue(verified.verified)
        self.assertTrue(no_anchor.unanchored)
        self.assertFalse(verified.unanchored)

    def test_old_plain_empty_list_cannot_express_the_state(self):
        """故障注入：旧实现 `return []` 不可能表达"没有校验能力"。

        若把 check_fact_anchors 的空锚点分支改回 `return []`，下面这条断言
        立刻变红（plain list 既没有 anchor_state，也无法与"通过"区分）。
        """
        _, findings = guard_fact_anchors("今天涨了 1.20%。", "", fallback=FALLBACK)
        old_return_value = []  # 旧实现的返回形态

        self.assertFalse(hasattr(old_return_value, "anchor_state"),
                         "前提错误：普通 list 不该有状态位")
        self.assertTrue(hasattr(findings, "anchor_state"),
                        "空锚点返回值没有状态位 → 调用方无法区分「没查」与「通过」")
        self.assertEqual(findings.anchor_state, ANCHOR_STATE_NO_ANCHORS)
        # 如果两个语义又退化成同一个可观察值，这条即红
        self.assertNotEqual(findings.anchor_state, ANCHOR_STATE_VERIFIED)

    def test_check_fact_anchors_returns_stateful_list(self):
        empty = check_fact_anchors("今天涨了 1.20%。", "")
        self.assertIsInstance(empty, AnchorFindings)
        self.assertEqual(empty.anchor_state, ANCHOR_STATE_NO_ANCHORS)
        self.assertEqual(empty.anchor_count, 0)

        verified = check_fact_anchors("今天涨了 1.20%。", PACKET)
        self.assertIsInstance(verified, AnchorFindings)
        self.assertEqual(verified.anchor_state, ANCHOR_STATE_VERIFIED)
        self.assertGreater(verified.anchor_count, 0)

    def test_checker_exception_is_a_third_distinguishable_state(self):
        """校验器自身异常（已放行）必须与"通过"、与"无锚点"都不同。"""
        with mock.patch.object(fact_anchor, "check_fact_anchors",
                               side_effect=RuntimeError("boom")):
            out, findings = guard_fact_anchors("随便一段文本", PACKET)

        self.assertEqual(out, "随便一段文本")
        self.assertEqual(findings, [])
        self.assertEqual(findings.anchor_state, ANCHOR_STATE_ERROR)
        self.assertFalse(findings.verified)
        self.assertNotIn(findings.anchor_state,
                         (ANCHOR_STATE_VERIFIED, ANCHOR_STATE_NO_ANCHORS))

    def test_caller_can_now_log_no_verification(self):
        """调用方拿得到状态 → 能如实打日志「本次没做数字校验」。"""
        msgs: list[str] = []
        guard_fact_anchors("今天涨了 1.20%。", "", log=msgs.append, context="u/x")
        self.assertTrue(any("未做数字校验" in m for m in msgs), msgs)
        self.assertTrue(any(ANCHOR_STATE_NO_ANCHORS in m for m in msgs), msgs)

    def test_verified_case_does_not_log_no_verification(self):
        msgs: list[str] = []
        guard_fact_anchors("今天涨了 1.20%。", PACKET, log=msgs.append)
        self.assertFalse(any("未做数字校验" in m for m in msgs), msgs)

    def test_critical_block_keeps_verified_state(self):
        _, findings = guard_fact_anchors("涨了 229.7%。", PACKET, fallback=FALLBACK)
        self.assertTrue(findings)
        self.assertTrue(findings.verified)
        self.assertEqual(findings.anchor_state, ANCHOR_STATE_VERIFIED)


if __name__ == "__main__":
    unittest.main()
