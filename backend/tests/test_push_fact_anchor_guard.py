#!/usr/bin/env python3
"""v9.9.24 推送出口防幻觉守卫回归测试。

盯的是两件事，方向相反，同等重要：
  - 该拦的必须拦住（编造数字 / prompt 泄漏 / 内部枚举，不能只加 ⚠️ 就放行）
  - 不该拦的必须放行（正常中文、小数百分比、整数百分比建议）

历史教训：9/10 早安简报出现过「异常涨幅数字 229.7%」，质检规则抓到了，
但只是加了一行 ⚠️，可疑数字照样推给了用户 —— 标注不是拦截。
"""

import unittest
from unittest import mock

from services.fact_anchor import check_fact_anchors, guard_fact_anchors
from services.llm_output_guard import LLMOutputGuard

# 模拟"喂给 LLM 的数据包"
DATA_PACKET = """## 持仓数据
- 贵州茅台 600519: 现价1520.30, 涨跌 +1.20%, 成本 1480.00
- 沪深300ETF 510300: 现价3.856, 涨跌 -0.42%
## 盈亏锚点
组合总浮盈 3701 元, 收益率 +2.35%
## 市场
融资余额 18200 亿
"""


class TestFactAnchorBlocksFabricatedNumbers(unittest.TestCase):
    def test_r1_exaggerated_pct_is_blocked_whole_segment(self):
        """R1：>200% 的涨幅属 critical，整段降级，一个字都不许出"""
        text = "今天市场不错。\n贵州茅台涨了 229.7%，已接近止盈线。\n继续持有。"

        out, findings = guard_fact_anchors(text, DATA_PACKET, fallback="（已拦截）")

        self.assertEqual(out, "（已拦截）")
        self.assertTrue(any(f.rule == "R1_EXAGGERATED_PCT" for f in findings))

    def test_r3_unsourced_money_sentence_is_removed(self):
        """R3：数据包里没有的金额（净流入 487 亿）必须被删句"""
        text = "今天市场不错。\n北向资金净流入 487 亿，外资大举加仓。\n继续持有。"

        out, findings = guard_fact_anchors(text, DATA_PACKET)

        self.assertNotIn("487", out)
        self.assertIn("今天市场不错", out)
        self.assertTrue(any(f.rule == "R3_UNSOURCED_MONEY" for f in findings))

    def test_r2_unsourced_decimal_pct_sentence_is_removed(self):
        """R2：带小数点、数据包里找不到的百分比必须被删句"""
        text = "组合今天跌了 5.42%，需要留意。\n其他都没问题，继续持有。"

        out, findings = guard_fact_anchors(text, DATA_PACKET)

        self.assertNotIn("5.42%", out)
        self.assertIn("其他都没问题", out)
        self.assertTrue(any(f.rule == "R2_UNSOURCED_PCT" for f in findings))


class TestFactAnchorNoFalsePositive(unittest.TestCase):
    def test_numbers_from_packet_pass_through(self):
        """正文数字都能在数据包里找到出处 → 一个字都不许改"""
        text = (
            "组合今天涨了 1.20%。\n"
            "沪深300ETF 微跌 0.42%，不用在意。\n"
            "整体收益率 2.35%，继续持有。\n"
            "融资余额 18200 亿，杠杆还在。\n"
        )
        out, findings = guard_fact_anchors(text, DATA_PACKET)
        self.assertEqual(findings, [])
        self.assertEqual(out, text)

    def test_decimal_pct_not_truncated_into_false_alarm(self):
        """1.406% / 0.856% 不得被误判（历史上被截成 406% 过）"""
        text = "资金面(银行间利率): 1.406% (平稳)\n估算偏差 0.856%"
        packet = "银行间利率 1.406, 估算偏差 0.856"
        out, findings = guard_fact_anchors(text, packet)
        self.assertEqual(findings, [])
        self.assertIn("1.406%", out)
        self.assertIn("0.856%", out)

    def test_integer_pct_advice_is_never_checked(self):
        """整数百分比是建议性表述（"留 30% 仓位"），不查出出处，避免误杀"""
        text = "建议留 30% 仓位，跌破 5% 就止损。"
        out, findings = guard_fact_anchors(text, DATA_PACKET)
        self.assertEqual(findings, [])
        self.assertEqual(out, text)

    def test_empty_packet_does_not_block(self):
        """拿不到数据包时放行（校验器不掌握证据就不该自作主张删内容）"""
        text = "组合今天涨了 1.20%。"
        # 空包 → 无锚点 → 视为无法判定，放行
        out, findings = guard_fact_anchors(text, "")
        self.assertEqual(findings, [])

    def test_derived_number_is_flagged_because_untraceable(self):
        """刻意严格：LLM 自己推导出的数字（成本 1.5 → 现价 1.6 → 浮盈 6.7%）
        在数据包里找不到原文，也会被标记删句。

        这是有意的选择 —— 放开到"允许一步推导"就得给足容差，而容差一大，
        数据包里的净值 3.856 会把编造的"跌了 3.87%"也判成有出处，检测失明。
        误判代价只是少一句（且会打日志），漏判代价是把编造数字推给用户。
        """
        packet = "成本 1.5, 现价 1.6"
        text = "这只浮盈 6.7%，可以考虑止盈。\n其余仓位都不用动，继续持有就好。"
        out, findings = guard_fact_anchors(text, packet)
        self.assertNotIn("6.7%", out)
        self.assertIn("其余仓位都不用动", out)


class TestHardLeakInterception(unittest.TestCase):
    def test_internal_enum_is_hard_leak(self):
        self.assertTrue(LLMOutputGuard.has_hard_leak("当前市场状态 high_vol_bear，建议防御。"))

    def test_truncated_json_kv_is_hard_leak(self):
        self.assertTrue(
            LLMOutputGuard.has_hard_leak('{"direction": "bearish", "confidence": 58, "conclusion": "'))

    def test_normal_text_is_not_hard_leak(self):
        self.assertFalse(LLMOutputGuard.has_hard_leak("今天市场不错，继续持有为主。"))


class TestBriefingHallucinationLabelNowBlocks(unittest.TestCase):
    """_inject_hallucination_label 从「只标注」升级为「事实型问题删句」。"""

    def _run(self, text):
        with mock.patch("services.stock_monitor.load_stock_holdings", return_value=[]), \
             mock.patch("services.fund_monitor.load_fund_holdings", return_value=[]), \
             mock.patch("urllib.request.urlopen", side_effect=RuntimeError("no network")):
            from scripts.night_worker import _inject_hallucination_label
            return _inject_hallucination_label({"u": text})["u"]

    def test_exaggerated_pct_sentence_is_dropped(self):
        text = "☀️ 早安！\n今天市场不错。\n该基金今日涨幅 229.7%，非常可观。\n建议：先别乱动。"
        out = self._run(text)
        body = out.split("\n\n", 1)[-1]
        self.assertNotIn("229.7%", body)
        self.assertIn("今天市场不错", body)
        # 标注保留，方便复盘
        self.assertIn("异常涨幅数字", out)

    def test_prompt_leak_sentence_is_dropped(self):
        text = "☀️ 早安！\n今天市场不错。\n用户提供了宏观数据快照，需要我按格式输出。\n建议：先别乱动。"
        out = self._run(text)
        body = out.split("\n\n", 1)[-1]
        self.assertNotIn("用户提供了", body)
        self.assertIn("今天市场不错", body)

    def test_normal_briefing_untouched(self):
        text = "☀️ 早安！\n\n资金面(银行间利率): 1.406% (平稳)\n建议：先别乱动。"
        out = self._run(text)
        self.assertEqual(out, text)


if __name__ == "__main__":
    unittest.main()
