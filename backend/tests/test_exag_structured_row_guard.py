#!/usr/bin/env python3
"""EXAG 删句不得删掉**程序渲染的结构化行**（2026-09-16 兴全合润明细整行消失事故）。

事故链（生产存档实测）：
  持仓明细行因累计净值口径算出假的 ▲224.6%
  → ``_inject_hallucination_label`` 的「异常涨幅数字 >200%」分支命中
  → 按 ``\\n`` 切句 ⇒ 「句子」= 整行明细 ⇒ 整行被删
  → 明细少一行，而「当前市值」仍含它 ⇒ 用户看到 1333 vs 328 的残缺组合。

铁律：**AI 自由文本里的幻觉数字照删；程序渲染出来的结构化行不删（只标注）。**

本文件每条用例都必须能被故障注入变红 —— 恒绿的守卫 = 空转的绿。
行格式均取自生产真实存档（2026-09-16 ~ 09-18_briefing_BuLuoGeLi.txt），不自造。
"""

import unittest
from unittest.mock import patch

from scripts.night_worker import _inject_hallucination_label


# 温度计汇总行（程序渲染，_build_portfolio_thermometer）
THERMO_HEAD = "📊 组合温度计（截至近日收盘）\n总投入 ¥1000  当前市值 ¥1020  整体浮盈 📈 +2.0%\n"
# 持仓明细行（程序渲染）。▲350% 是**真实且正确**的涨幅，不是幻觉。
ROW_COOL = "  • 华夏先进制造龙头混合A(013107)  买入2.420 → 现2.904  ▲20.0%  ¥120.0"
ROW_HOT = "  • 长期持有的牛基(000001)  买入1.000 → 现4.500  ▲350.0%  ¥900.0"


def _run(text, uid="BuLuoGeLi"):
    """跑真实实现。数据源一律打桩，保证用例不依赖网络与真实持仓文件。"""
    with patch("services.stock_monitor.load_stock_holdings", return_value=[]), \
         patch("services.fund_monitor.load_fund_holdings", return_value=[]), \
         patch("urllib.request.urlopen", side_effect=RuntimeError("skip local api")):
        return _inject_hallucination_label({uid: text})[uid]


class TestStructuredRowSurvivesExagDrop(unittest.TestCase):
    """验收 1：真实的 >200% 明细行必须原样保留（改前红、改后绿）。"""

    def test_position_row_with_real_350pct_is_kept(self):
        text = THERMO_HEAD + "\n持仓明细：\n" + ROW_COOL + "\n" + ROW_HOT + "\n"

        out = _run(text)

        self.assertIn(ROW_HOT, out, "▲350% 的明细行被删了 —— 真实涨幅不该被当幻觉")
        self.assertIn(ROW_COOL, out)
        self.assertNotIn("已自动删除", out)
        # 明细合计必须仍等于总市值（事故里这条被打成 120 vs 1020）
        self.assertIn("¥900.0", out)
        self.assertIn("¥120.0", out)

    def test_thermo_summary_row_with_over_200pct_is_kept(self):
        text = "📊 组合温度计（截至近日收盘）\n总投入 ¥100  当前市值 ¥400  整体浮盈 📈 +300.0%\n"

        out = _run(text)

        self.assertIn("总投入 ¥100", out, "温度计汇总行被删了")
        self.assertIn("整体浮盈", out)
        self.assertNotIn("已自动删除", out)

class TestExagGuardStillWorks(unittest.TestCase):
    """验收 2：守卫不能被改废 —— 自由文本里的幻觉数字照删。"""

    def test_free_text_hallucination_still_dropped(self):
        text = "📝 【AI研判】\n该基金暴涨 350%，建议立刻加仓。\n"

        out = _run(text)

        self.assertNotIn("该基金暴涨", out, "自由文本的幻觉数字没被删 —— 守卫被改废了")
        self.assertIn("已自动删除", out)

    def test_structured_row_hit_does_not_shield_free_text(self):
        """结构化行命中后不能 `break`，否则后面真正的幻觉扫不到。"""
        text = (
            THERMO_HEAD + "\n持仓明细：\n" + ROW_HOT + "\n"
            "📝 【AI研判】\n该基金暴涨 420%，建议立刻加仓。\n"
        )

        out = _run(text)

        self.assertIn(ROW_HOT, out, "结构化行应保留")
        self.assertNotIn("该基金暴涨", out, "结构化行命中后 continue 扫描失败 —— 真幻觉漏网")
        self.assertIn("已自动删除", out)

    def test_structured_row_hit_falls_back_to_label_only(self):
        """没删任何句子时，标注降级为「含 N 处可疑内容」，不说「已自动删除」。"""
        text = THERMO_HEAD + "\n持仓明细：\n" + ROW_HOT + "\n"

        out = _run(text)

        self.assertNotIn("已自动删除", out)
        self.assertIn("[AI质检]", out, "只标注不删，但仍要如实标注")
        self.assertIn("异常涨幅数字", out)


if __name__ == "__main__":
    unittest.main()
