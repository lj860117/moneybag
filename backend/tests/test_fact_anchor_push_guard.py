#!/usr/bin/env python3
"""v9.9.24 事实锚点校验 + 推送出口拦截 回归测试。

盯的是两头的错误：
  - 该拦的没拦住（脏内容只加个 ⚠️ 就放行）
  - 不该拦的被拦了（正常推送被降级成兜底文案）
"""
import unittest
from unittest import mock

from services import fact_anchor
from services.fact_anchor import guard_fact_anchors
from services.llm_output_guard import LLMOutputGuard
from scripts.night_worker import _inject_hallucination_label

FALLBACK = "（AI 输出含无法核实的数据，已拦截）"

# 模拟"传给 LLM 的数据包"—— 收盘复盘里就是 scan_data + 温度计 + 持仓新闻
PACKET = """## 持仓数据
- 贵州茅台 600519: 现价1520.30, 涨跌 +1.20%, 成本 1480.00, 浮盈 4021 元
- 沪深300ETF 510300: 现价3.856, 涨跌 -0.42%, 浮亏 -320 元
## 盈亏锚点
组合总浮盈 3701 元, 收益率 +2.35%
## 市场
融资余额 18200 亿, 北向成交 1205 亿
"""

DIRTY_LLM_OUTPUT = """今天你的组合表现不错。
贵州茅台涨了 229.7%，已经接近止盈线了。
北向资金今天净流入 487 亿，外资在大举加仓。
沪深300ETF 微跌 0.42%，不用在意。"""

NORMAL_LLM_OUTPUT = """今天你的组合表现不错。
贵州茅台涨了 1.20%，浮盈 4021 元。
沪深300ETF 微跌 0.42%，不用在意。
组合整体收益率 2.35%，继续持有为主。"""


class TestFactAnchor(unittest.TestCase):
    """事实锚点：编造数字必须拦，真实数字必须放行。"""

    def test_fabricated_pct_and_money_blocked(self):
        """229.7% 与 487 亿在数据包里都无出处 → critical，整段降级"""
        out, findings = guard_fact_anchors(
            DIRTY_LLM_OUTPUT, PACKET, fallback=FALLBACK)

        self.assertEqual(out, FALLBACK)
        rules = {f.rule for f in findings}
        self.assertIn("R1_EXAGGERATED_PCT", rules)
        self.assertIn("R3_UNSOURCED_MONEY", rules)
        self.assertTrue(any(f.severity == "critical" for f in findings))

    def test_normal_output_not_touched(self):
        """正常输出里的数字在数据包里都有出处 → 一字不改"""
        out, findings = guard_fact_anchors(
            NORMAL_LLM_OUTPUT, PACKET, fallback=FALLBACK)

        self.assertEqual(findings, [])
        self.assertEqual(out, NORMAL_LLM_OUTPUT)

    def test_unsourced_pct_removes_only_that_sentence(self):
        """单个无出处的小数百分比：只删命中句，其余正文保留（不整段降级）"""
        text = "组合今天涨了 7.88%，挺猛。\n沪深300ETF 微跌 0.42%，不用在意。\n继续持有。"
        out, findings = guard_fact_anchors(text, PACKET, fallback=FALLBACK)

        self.assertNotIn("7.88%", out)
        self.assertIn("0.42%", out)
        self.assertIn("继续持有", out)
        self.assertEqual([f.rule for f in findings], ["R2_UNSOURCED_PCT"])

    def test_long_period_pct_exempt(self):
        """近3年涨幅 320% 是合规长周期表述，不得被 R1 判 critical"""
        text = "该基金近3年涨幅 320%，长期表现不错。\n继续持有。"
        out, findings = guard_fact_anchors(text, PACKET, fallback=FALLBACK)

        self.assertNotIn("R1_EXAGGERATED_PCT", {f.rule for f in findings})

    def test_empty_packet_does_not_block_everything(self):
        """没有数据包时不得因"查不到出处"把正常文本删空（降级为放行）"""
        out, _ = guard_fact_anchors("今天涨了 1.20%。", "", fallback=FALLBACK)
        self.assertEqual(out, "今天涨了 1.20%。")

    def test_guard_never_raises(self):
        """校验器自身异常绝不能阻断推送链路"""
        with mock.patch.object(fact_anchor, "check_fact_anchors",
                               side_effect=RuntimeError("boom")):
            out, findings = guard_fact_anchors("随便一段文本", PACKET)
        self.assertEqual(out, "随便一段文本")
        self.assertEqual(findings, [])


class TestHardLeakInterception(unittest.TestCase):
    """内部枚举 / JSON 契约词 / 指令复读 → 整段拦截。"""

    def test_internal_enum_flagged(self):
        self.assertTrue(LLMOutputGuard.has_hard_leak(
            "当前 regime 为 high_vol_bear，建议防守。"))

    def test_json_kv_leak_flagged(self):
        self.assertTrue(LLMOutputGuard.has_hard_leak(
            '{"direction": "bearish", "confidence": 58, "conclusion": "跌"'))

    def test_normal_text_not_flagged(self):
        self.assertFalse(LLMOutputGuard.has_hard_leak(NORMAL_LLM_OUTPUT))
        self.assertFalse(LLMOutputGuard.has_hard_leak(
            "资金面(银行间利率): 1.406% (平稳)"))


class TestBriefingLabelNowIntercepts(unittest.TestCase):
    """_inject_hallucination_label：从"只标注"升级为"删命中句"。"""

    def _run(self, text):
        with mock.patch("services.stock_monitor.load_stock_holdings", return_value=[]), \
             mock.patch("services.fund_monitor.load_fund_holdings", return_value=[]), \
             mock.patch("urllib.request.urlopen", side_effect=RuntimeError("no net")):
            return _inject_hallucination_label({"u": text})["u"]

    def test_exaggerated_pct_sentence_removed(self):
        raw = "☀️ 早安！\n该基金今日涨幅 229.7%，非常可观。\n建议：先别乱动。"
        out = self._run(raw)

        body = out.split("\n\n", 1)[-1]  # 去掉 ⚠️ 标注行看正文
        self.assertNotIn("229.7%", body)
        self.assertIn("异常涨幅数字", out)  # 标注仍保留，便于复盘
        self.assertIn("建议：先别乱动。", body)  # 未命中句保留

    def test_prompt_leak_sentence_removed(self):
        raw = "☀️ 早安！\n用户提供了宏观数据快照。\n建议：先别乱动。"
        out = self._run(raw)

        body = out.split("\n\n", 1)[-1]
        self.assertNotIn("用户提供了", body)
        self.assertIn("prompt泄漏", out)
        self.assertIn("建议：先别乱动。", body)

    def test_normal_briefing_untouched(self):
        raw = "☀️ 早安！\n\n资金面(银行间利率): 1.406% (平稳)\n估算偏差 0.856%\n建议：先别乱动。"
        out = self._run(raw)

        self.assertEqual(out, raw)


if __name__ == "__main__":
    unittest.main()
