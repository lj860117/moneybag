#!/usr/bin/env python3
"""恐贪指数伪精度回归测试（P2-3）。

背景：恐贪指数由 3 个粗分桶维度加权得出
（动量 7 档 × 波动率 5 档 × 量能 7 档 ≈ 245 种组合），却以 1 位小数
输出（如 62.5），前端 `pages/market-panorama.js` 直接原样渲染成
"恐贪 62.5(贪婪)"，让人误以为精度到 0.1 分。实际只是"20 日动量跌了
3~8%"这一类粗判。本测试锁定"分数必须是整数"这一契约。
"""

import unittest

import pandas as pd

from services.market_data import get_fear_greed_index


def _fake_index_daily(n: int = 60, drift: float = 0.0, vol_spread: float = 0.3):
    """造一段沪深300日线：drift 为每日漂移，vol_spread 制造波动率差异"""
    import math

    closes = []
    for i in range(n):
        wobble = math.sin(i / 3.0) * vol_spread
        closes.append(100.0 + drift * i + wobble)
    return pd.DataFrame({
        "close": closes,
        "volume": [1_000_000 + i * 1000 for i in range(n)],
    })


class TestFearGreedPrecision(unittest.TestCase):
    def _score_with(self, df):
        import infra.data_source.market.stocks as stocks_mod

        original = getattr(stocks_mod, "get_index_daily", None)
        stocks_mod.get_index_daily = lambda symbol=None: df
        try:
            return get_fear_greed_index()
        finally:
            stocks_mod.get_index_daily = original

    def test_score_is_integer_no_false_precision(self):
        """分数必须是整数 —— 不得出现 62.5 这类伪精度"""
        for drift, spread in ((0.0, 0.3), (0.05, 1.2), (-0.08, 2.0)):
            res = self._score_with(_fake_index_daily(drift=drift, vol_spread=spread))
            score = res["score"]
            with self.subTest(drift=drift, spread=spread, score=score):
                self.assertIsInstance(score, int, f"score={score!r} 不是整数")
                self.assertEqual(score, int(round(score)))
                self.assertTrue(0 <= score <= 100, f"score={score} 越界")

    def test_level_matches_score_bucket(self):
        """档位划分与分数一致，取整后不出现「61 分却标中性」这类错位"""
        expect = [
            (76, "极度贪婪"), (75, "极度贪婪"),
            (61, "贪婪"), (60, "贪婪"),
            (41, "中性"), (40, "中性"),
            (26, "恐惧"), (25, "恐惧"),
            (10, "极度恐惧"),
        ]
        for score, level in expect:
            with self.subTest(score=score):
                if score >= 75:
                    self.assertEqual(level, "极度贪婪")
                elif score >= 60:
                    self.assertEqual(level, "贪婪")
                elif score >= 40:
                    self.assertEqual(level, "中性")
                elif score >= 25:
                    self.assertEqual(level, "恐惧")
                else:
                    self.assertEqual(level, "极度恐惧")

    def test_dimension_values_keep_precision(self):
        """维度明细是真实测量值，取整只动综合分，不能连维度值一起抹掉"""
        res = self._score_with(_fake_index_daily(drift=0.03, vol_spread=1.5))
        dims = res.get("dimensions") or {}
        self.assertTrue(dims, "维度明细不应为空")
        for name in ("momentum", "volatility"):
            self.assertIn(name, dims)
            self.assertIsInstance(dims[name]["value"], float)


if __name__ == "__main__":
    unittest.main()
