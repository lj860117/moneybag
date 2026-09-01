#!/usr/bin/env python3
"""钱袋子晨报推送防回归测试。"""

import unittest
from unittest.mock import patch

from services.llm_output_guard import LLMOutputGuard
from scripts.night_worker import _inject_hallucination_label


class TestLLMOutputGuard(unittest.TestCase):
    def test_filter_analysis_rejects_prompt_replay_block(self):
        raw = (
            "好的，用户让我基于提供的市场数据写一段小结，要求像朋友帮你看盘后的微信消息。\n\n"
            "用户提供了宏观数据快照，包括A股指数、北向资金和行业热点。\n"
            "现在分析数据：市场偏弱，建议先别追高。"
        )

        cleaned = LLMOutputGuard.filter_analysis(raw, fallback="（分析暂时不可用）")

        self.assertEqual(cleaned, "（分析暂时不可用）")


class TestBriefingHallucinationLabel(unittest.TestCase):
    @patch("services.stock_monitor.load_stock_holdings", return_value=[])
    @patch("services.fund_monitor.load_fund_holdings", return_value=[])
    @patch("urllib.request.urlopen", side_effect=RuntimeError("skip local api"))
    def test_quality_check_does_not_extract_fake_large_pct_from_decimal(self, _mock_urlopen, _mock_funds, _mock_stocks):
        briefings = {
            "LeiJiang": "☀️ 早安，LeiJiang！\n\n资金面(银行间利率): 1.406% (平稳)\n建议：先别乱动"
        }

        cleaned = _inject_hallucination_label(briefings)

        self.assertNotIn("异常涨幅数字", cleaned["LeiJiang"])


if __name__ == "__main__":
    unittest.main()
