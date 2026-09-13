#!/usr/bin/env python3
"""v9.9.26 P1-8 推送单一裁决出口 + 建议可执行性闸门 回归测试。

盯的是两类**真实事故**（来自服务器推送存档，不是假设）：
  1. 同一条推送里「推送管家」说持有、「AI 诊断」说减仓，系统不裁决，
     两条矛盾结论并列给用户，让用户自己判断。
  2. 用户总市值 ¥754，却收到「分批止盈三分之一」—— 没有任何资金规模
     闸门判断这个建议是否可执行。

本文件全程离线：不联网、不调 LLM、不写生产目录（tests/conftest.py 已强制
把 DATA_DIR 隔离到 tmp）。
"""
import ast
import pathlib
import unittest

from scripts.night_worker import (
    _advice_priority,
    apply_executability_gate,
    arbitrate_advice,
    collapse_conflicting_decisions,
    dedup_push_items,
    executability_verdict,
    gate_trade_decisions,
    normalize_advice_action,
    render_conflict_conclusion,
    strip_trade_action_lines,
)
import config

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ============================================================
# 1. 方向归一
# ============================================================

class TestNormalizeAdviceAction(unittest.TestCase):
    """各种写法都要落到 add / reduce / hold，认不出来不许瞎猜。"""

    def test_english_and_canonical(self):
        self.assertEqual(normalize_advice_action("reduce"), "reduce")
        self.assertEqual(normalize_advice_action("add"), "add")
        self.assertEqual(normalize_advice_action("hold"), "hold")

    def test_steward_directions(self):
        """管家返回的 direction 字段（bullish / bearish / neutral）。"""
        self.assertEqual(normalize_advice_action("bullish"), "add")
        self.assertEqual(normalize_advice_action("bearish"), "reduce")
        self.assertEqual(normalize_advice_action("neutral"), "hold")
        self.assertEqual(normalize_advice_action("BULLISH"), "add")

    def test_chinese_action_words(self):
        self.assertEqual(normalize_advice_action("止盈"), "reduce")
        self.assertEqual(normalize_advice_action("减仓"), "reduce")
        self.assertEqual(normalize_advice_action("止损"), "reduce")
        self.assertEqual(normalize_advice_action("加仓"), "add")
        self.assertEqual(normalize_advice_action("补仓"), "add")
        self.assertEqual(normalize_advice_action("持有"), "hold")

    def test_sentence_with_action_word(self):
        """长句里带动作词也要能认出来（推送里的真实写法）。"""
        self.assertEqual(normalize_advice_action("建议分批止盈三分之一"), "reduce")
        self.assertEqual(normalize_advice_action("可以考虑减仓一部分"), "reduce")
        self.assertEqual(normalize_advice_action("不妨加仓摊薄成本"), "add")

    def test_negated_sentence_is_not_misread(self):
        """否定句方向是反的 —— 宁可返回空串，也不能判成"加仓"。"""
        for text in ("不宜加仓", "不建议减仓", "不要赎回", "无需止损",
                     "不必清仓", "切忌补仓", "先别卖出"):
            self.assertEqual(
                normalize_advice_action(text), "",
                f"{text!r} 含否定词，不能判成动作方向")

    def test_unknown_and_empty(self):
        for bad in (None, "", "   ", "今天市场没什么大动静", "继续观察一下", 123):
            self.assertEqual(normalize_advice_action(bad), "")

    def test_hold_aliases_survive_negation_guard(self):
        """"暂不操作" 是"持有观望"的正规写法，不能被否定词守卫误杀成空串。"""
        self.assertEqual(normalize_advice_action("暂不操作"), "hold")
        self.assertEqual(normalize_advice_action("不操作"), "hold")


# ============================================================
# 2. 来源优先级
# ============================================================

class TestAdvicePriority(unittest.TestCase):
    def test_risk_control_is_top(self):
        self.assertGreater(_advice_priority("risk_control"),
                           _advice_priority("steward"))
        self.assertGreater(_advice_priority("steward"),
                           _advice_priority("rule_engine"))
        self.assertGreater(_advice_priority("rule_engine"),
                           _advice_priority("llm_diagnosis"))

    def test_unknown_source_is_lowest(self):
        """没登记的来源不能意外压过已登记的来源。"""
        for src in ("", None, "brand_new_source", "   "):
            self.assertLess(_advice_priority(src), _advice_priority("llm_diagnosis"))

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(_advice_priority(" Steward "), _advice_priority("steward"))


# ============================================================
# 3. 单一裁决出口
# ============================================================

class TestArbitrateAdvice(unittest.TestCase):
    """核心：有冲突必须给唯一结论，不许和稀泥。"""

    def test_conflict_resolves_to_one_conclusion(self):
        """真实事故场景：管家说持有、AI 说减仓 → 只能出一个结论。"""
        arb = arbitrate_advice([
            {"source": "steward", "action": "neutral",
             "reason": "不上不下的行情，没啥操作的必要"},
            {"source": "llm_diagnosis", "action": "止盈",
             "reason": "先按纪律止盈一部分"},
        ])

        self.assertTrue(arb["conflict"], "两个相反方向必须被识别为冲突")
        self.assertTrue(arb["resolved"], "优先级不同，应该裁得出结果")
        self.assertEqual(arb["action"], "hold", "管家(70) 高于 AI诊断(20)")
        self.assertEqual(arb["source"], "steward")

        # text 是给用户看的那一句：只能有一个方向，且不能复读被否决的动作词
        self.assertIn("持有观望", arb["text"])
        self.assertNotIn("止盈", arb["text"],
                         "被否决的动作词不能在结论句里复读一遍")

    def test_conflict_reason_is_explainable(self):
        """必须能说清"为什么选 A 不选 B"。"""
        arb = arbitrate_advice([
            {"source": "steward", "action": "hold", "reason": ""},
            {"source": "llm_diagnosis", "action": "reduce", "reason": ""},
        ])
        reason = arb["reason"]
        self.assertIn("推送管家", reason)
        self.assertIn("AI诊断", reason)
        self.assertIn("70", reason, "要给出胜出来源的优先级数值")
        self.assertIn("20", reason, "要给出落败来源的优先级数值")

    def test_suppressed_is_preserved(self):
        """被否决的结论不能凭空消失，要留在 suppressed 里便于复盘。"""
        arb = arbitrate_advice([
            {"source": "steward", "action": "hold", "reason": "没啥必要"},
            {"source": "llm_diagnosis", "action": "reduce", "reason": "止盈一部分"},
        ])
        self.assertEqual(len(arb["suppressed"]), 1)
        lost = arb["suppressed"][0]
        self.assertEqual(lost["source"], "llm_diagnosis")
        self.assertEqual(lost["action"], "reduce")
        self.assertIn("已否决", lost["why"])

    def test_tie_priority_refuses_to_arbitrate(self):
        """同优先级 → 明说"暂不给出方向"，并点名是哪两个源冲突。"""
        arb = arbitrate_advice([
            {"source": "steward", "action": "hold", "reason": ""},
            {"source": "steward", "action": "reduce", "reason": ""},
        ])
        self.assertTrue(arb["conflict"])
        self.assertFalse(arb["resolved"])
        self.assertIsNone(arb["action"], "裁不出结果时不能硬给一个方向")
        self.assertIn("暂不给出方向", arb["text"])
        self.assertIn("推送管家", arb["text"], "必须点名冲突的是哪个源")

    def test_same_direction_is_not_a_conflict(self):
        arb = arbitrate_advice([
            {"source": "rule_engine", "action": "reduce", "reason": "估值高"},
            {"source": "llm_diagnosis", "action": "止盈", "reason": "落袋"},
        ])
        self.assertFalse(arb["conflict"])
        self.assertTrue(arb["resolved"])
        self.assertEqual(arb["action"], "reduce")
        self.assertEqual(len(arb["suppressed"]), 1, "同向的重复结论并入 suppressed")

    def test_single_source(self):
        arb = arbitrate_advice([{"source": "steward", "action": "bearish"}])
        self.assertEqual(arb["action"], "reduce")
        self.assertFalse(arb["conflict"])
        self.assertEqual(arb["suppressed"], [])

    def test_empty_and_garbage_inputs_never_raise(self):
        cases = [
            [],
            None,
            [None, "x", 42],          # 非 dict 元素直接跳过
            [{}],                     # 没有 action
            [{"source": "steward"}],  # 缺 action
            [{"action": "reduce"}],   # 缺 source → 未知来源，优先级最低
            [{"source": "steward", "action": "今天没什么动静"}],  # 认不出的动作
        ]
        for arg in cases:
            with self.subTest(arg=arg):
                arb = arbitrate_advice(arg)
                for key in ("action", "text", "reason", "source",
                            "conflict", "resolved", "suppressed"):
                    self.assertIn(key, arb)

        # 完全无输入 → 明确说"暂不给出方向"，不是静默通过
        empty = arbitrate_advice([])
        self.assertIsNone(empty["action"])
        self.assertIn("暂不给出方向", empty["text"])
        self.assertFalse(empty["conflict"])

    def test_log_fn_is_used(self):
        """裁决过程必须留痕（被否决的结论要进日志）。"""
        seen = []
        arbitrate_advice([
            {"source": "steward", "action": "hold"},
            {"source": "llm_diagnosis", "action": "reduce"},
        ], log_fn=seen.append)
        self.assertTrue(seen, "冲突裁决必须写日志")
        self.assertTrue(any("已裁决" in m for m in seen))


class TestCollapseConflictingDecisions(unittest.TestCase):
    """step_r1_phase3 用的收口函数：估值说减仓 + 恐贪说加仓，不能两条都留。"""

    def test_conflicting_list_collapses_to_one(self):
        decisions = [
            {"action": "reduce", "source": "rule_engine", "reason": "估值过高，减仓"},
            {"action": "add", "source": "rule_engine", "reason": "情绪恐慌，加仓"},
        ]
        out, arb = collapse_conflicting_decisions(decisions)

        self.assertTrue(arb["conflict"])
        self.assertFalse(arb["resolved"], "同一来源(rule_engine)同优先级，裁不出来")
        self.assertEqual(len(out), 1, "必须只剩一条出口")
        self.assertIn("暂不给出方向", out[0]["reason"])
        actions = {d.get("action") for d in out}
        self.assertNotIn("reduce", actions)
        self.assertNotIn("add", actions)

    def test_cross_source_conflict_keeps_winner_only(self):
        decisions = [
            {"action": "reduce", "source": "steward", "reason": "管家建议减仓"},
            {"action": "add", "source": "llm_diagnosis", "reason": "AI 建议加仓"},
        ]
        out, arb = collapse_conflicting_decisions(decisions)
        self.assertTrue(arb["resolved"])
        self.assertEqual(out[0]["action"], "reduce")
        self.assertEqual(out[0]["source"], "steward")
        self.assertIn("[口径裁决]", out[0]["reason"])
        self.assertTrue(any("已否决" in s.get("why", "") for s in out[0]["suppressed"]))

    def test_no_conflict_is_passthrough(self):
        decisions = [
            {"action": "reduce", "source": "rule_engine", "reason": "a"},
            {"action": "reduce", "source": "steward", "reason": "b"},
        ]
        out, arb = collapse_conflicting_decisions(decisions)
        self.assertFalse(arb["conflict"])
        self.assertEqual(out, decisions, "同向不冲突时行为不得改变")

    def test_empty(self):
        out, arb = collapse_conflicting_decisions([])
        self.assertEqual(out, [])


class TestRenderConflictConclusion(unittest.TestCase):
    def test_resolved_keeps_body(self):
        arb = arbitrate_advice([
            {"source": "steward", "action": "hold"},
            {"source": "llm_diagnosis", "action": "reduce"},
        ])
        line, body = render_conflict_conclusion(arb, "今天组合微跌。\n建议止盈一部分")
        self.assertIn("口径裁决", line)
        self.assertEqual(body, "今天组合微跌。\n建议止盈一部分",
                         "裁出结论后正文方向已唯一，不需要删句")

    def test_unresolved_strips_direction(self):
        arb = arbitrate_advice([
            {"source": "steward", "action": "hold"},
            {"source": "steward", "action": "reduce"},
        ])
        line, body = render_conflict_conclusion(
            arb, "今天组合微跌。\n建议止盈一部分\n继续观察")
        self.assertIn("暂不给出方向", line)
        self.assertNotIn("止盈", body, "裁不出方向时不能把动作句留在正文")
        self.assertIn("继续观察", body, "非方向性描述要保留")


# ============================================================
# 4. 可执行性闸门
# ============================================================

class TestExecutabilityVerdict(unittest.TestCase):
    MIN_AMT = float(config.MIN_AMOUNT_FOR_TRADE_ADVICE)
    MIN_PCT = float(config.MIN_POSITION_PCT_FOR_TRADE_ADVICE)

    def test_thresholds_are_explicit_constants(self):
        """闸门阈值必须是显式常量（有出处/标注经验值），不能是散落的魔法数字。"""
        self.assertGreater(self.MIN_AMT, 0)
        self.assertGreater(self.MIN_PCT, 0)
        cfg_src = (_BACKEND_ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("MIN_AMOUNT_FOR_TRADE_ADVICE", cfg_src)
        self.assertIn("MIN_POSITION_PCT_FOR_TRADE_ADVICE", cfg_src)
        # 铁律：阈值必须标注来源或"经验值，未校准"
        self.assertIn("经验值", cfg_src)
        self.assertIn("未校准", cfg_src)

    def test_754_real_incident(self):
        """真实事故：总市值 ¥754 → 拦。"""
        v = executability_verdict(total_value=754.0)
        self.assertTrue(v["blocked"])
        self.assertIn("金额较小", v["reason"])

    def test_boundary_around_min_amount(self):
        """阈值上下都要覆盖：严格小于才拦。"""
        self.assertTrue(executability_verdict(
            total_value=self.MIN_AMT - 0.01)["blocked"])
        self.assertFalse(executability_verdict(
            total_value=self.MIN_AMT)["blocked"], "恰好等于门槛不拦")
        self.assertFalse(executability_verdict(
            total_value=self.MIN_AMT + 0.01)["blocked"])

    def test_position_pct_path(self):
        """仓位占比过小也拦 —— 即使总市值远高于金额门槛。"""
        big = self.MIN_AMT * 100  # 金额路径不会触发
        self.assertTrue(executability_verdict(
            total_value=big, position_pct=self.MIN_PCT - 0.1)["blocked"])
        self.assertFalse(executability_verdict(
            total_value=big, position_pct=self.MIN_PCT)["blocked"])

    def test_position_value_takes_precedence(self):
        """传了标的价值就优先用它，不再用总市值兜底。"""
        big = self.MIN_AMT * 100
        v = executability_verdict(total_value=big, position_value=100.0)
        self.assertTrue(v["blocked"])
        self.assertEqual(v["amount"], 100.0)

    def test_unknown_amount_does_not_block(self):
        """拿不到金额时不能凭空断言"金额较小"（那等于造数据）。"""
        v = executability_verdict()
        self.assertFalse(v["blocked"])
        self.assertIn("未知", v["reason"])

    def test_garbage_input_never_raises(self):
        for kw in ({"total_value": "abc"}, {"total_value": None, "position_pct": "x"},
                   {"position_value": object()}, {"total_value": ""}):
            v = executability_verdict(**kw)
            self.assertIn("blocked", v)


class TestApplyExecutabilityGate(unittest.TestCase):
    MIN_AMT = float(config.MIN_AMOUNT_FOR_TRADE_ADVICE)

    def test_754_downgrades_trade_advice(self):
        """核心验收：¥754 组合的「分批止盈三分之一」必须降级为观察项。"""
        text = "💡 建议分批止盈三分之一，锁定收益"
        out, report = apply_executability_gate(text, total_value=754.0)

        self.assertTrue(report["gated"])
        self.assertEqual(report["count"], 1)
        self.assertIn("观察项", out)
        self.assertIn("暂不给出交易建议", out)
        # 动作词必须被撤掉，不能只是加个前缀标签
        for verb in ("止盈", "减仓", "加仓", "止损", "清仓", "赎回"):
            self.assertNotIn(verb, out, f"降级后不应再出现动作词 {verb}")

    def test_all_trade_verbs_are_neutralised(self):
        for verb in ("止盈", "止损", "减仓", "加仓", "补仓", "清仓", "卖出",
                     "买入", "赎回", "割肉", "离场"):
            out, report = apply_executability_gate(
                f"建议{verb}一部分", total_value=100.0)
            self.assertTrue(report["gated"], f"{verb} 应被闸门拦下")
            self.assertNotIn(verb, out, f"{verb} 降级后不应残留")

    def test_non_trade_info_is_not_touched(self):
        """负面控制：涨跌提醒 / 新闻 / 预警不能被误伤。"""
        text = ("📈 沪深300 今日 +1.2%\n"
                "📰 央行宣布降准 0.5 个百分点\n"
                "🔔 某基金近 30 日回撤 12%，注意风险\n"
                "💰 今日盈亏 +¥3.2")
        out, report = apply_executability_gate(text, total_value=50.0)
        self.assertFalse(report["gated"])
        self.assertEqual(out, text, "非交易类信息必须一字不改")

    def test_large_portfolio_behaviour_unchanged(self):
        """负面控制：资金充足时交易建议原样输出，行为不回退。"""
        text = "💡 建议分批止盈三分之一，锁定收益"
        out, report = apply_executability_gate(
            text, total_value=self.MIN_AMT * 100)
        self.assertFalse(report["gated"])
        self.assertEqual(out, text)

    def test_mixed_lines_only_trade_line_downgraded(self):
        text = "📊 今日组合 +0.8%\n💡 建议减仓一半\n🔔 某基金回撤预警"
        out, report = apply_executability_gate(text, total_value=200.0)
        self.assertTrue(report["gated"])
        self.assertEqual(report["count"], 1)
        self.assertIn("📊 今日组合 +0.8%", out)
        self.assertIn("🔔 某基金回撤预警", out)
        self.assertNotIn("减仓", out)

    def test_empty_and_none_text(self):
        for text in ("", None):
            out, report = apply_executability_gate(text, total_value=10.0)
            self.assertFalse(report["gated"])
            self.assertEqual(report["count"], 0)

    def test_no_amount_no_gating(self):
        text = "建议减仓一半"
        out, report = apply_executability_gate(text)
        self.assertFalse(report["gated"])
        self.assertEqual(out, text)

    def test_long_sentence_keeps_descriptive_clause(self):
        """撤动作子句时，同一行里的描述性子句要留着（别把整行删成空）。"""
        out, report = apply_executability_gate(
            "今天组合微跌，建议分批止盈三分之一。", total_value=300.0)
        self.assertTrue(report["gated"])
        self.assertIn("今天组合微跌", out)
        self.assertNotIn("止盈", out)


class TestGateTradeDecisions(unittest.TestCase):
    def test_754_real_incident_structured(self):
        decisions = [
            {"action": "reduce", "source": "rule_engine",
             "reason": "市场估值过高（90% 分位），建议减仓避险"},
            {"action": "hold", "source": "rule_engine",
             "reason": "市场中性，维持现有仓位"},
        ]
        out, report = gate_trade_decisions(decisions, total_value=754.0)

        self.assertTrue(report["gated"])
        self.assertEqual(out[0]["action"], "observe")
        self.assertIn("暂不给出交易建议", out[0]["reason"])
        # 原判断保留便于复盘
        self.assertIn("市场估值过高", out[0]["reason"])
        # hold 不是交易类建议，不动
        self.assertEqual(out[1]["action"], "hold")

    def test_large_portfolio_unchanged(self):
        decisions = [{"action": "reduce", "source": "rule_engine", "reason": "减仓"}]
        out, report = gate_trade_decisions(decisions, total_value=500000.0)
        self.assertFalse(report["gated"])
        self.assertEqual(out, decisions)

    def test_empty(self):
        out, report = gate_trade_decisions([], total_value=10.0)
        self.assertEqual(out, [])
        self.assertFalse(report["gated"])

    def test_non_dict_entries_pass_through(self):
        out, _ = gate_trade_decisions(["junk", None], total_value=10.0)
        self.assertEqual(out, ["junk", None])


class TestStripTradeActionLines(unittest.TestCase):
    def test_removes_only_action_lines(self):
        text = "今天组合微跌 0.3%。\n建议止盈一部分。\n继续观察即可。"
        out, removed = strip_trade_action_lines(text)
        self.assertEqual(len(removed), 1)
        self.assertNotIn("止盈", out)
        self.assertIn("今天组合微跌 0.3%。", out)
        self.assertIn("继续观察即可。", out)

    def test_empty(self):
        for text in ("", None):
            out, removed = strip_trade_action_lines(text)
            self.assertEqual(removed, [])

    def test_no_action_words(self):
        text = "今天市场平淡。\n继续观察。"
        out, removed = strip_trade_action_lines(text)
        self.assertEqual(removed, [])
        self.assertEqual(out, text)


# ============================================================
# 5. 推送去重（持仓预警 vs 异动明细）
# ============================================================

class TestDedupPushItems(unittest.TestCase):
    ALERTS = ("\n🔔 持仓预警:\n"
              "🔴 易方达蓝筹精选混合\n  近 30 日回撤 12%，注意风险\n"
              "🟡 华夏中证500ETF联接\n  估值分位偏高")

    def test_same_fund_by_name_is_deduped_once(self):
        moves = ("📊 今日持仓异动\n"
                 "• 易方达蓝筹精选混合 -2.3%\n"
                 "• 招商中证白酒指数 +1.1%")
        _p, out, removed = dedup_push_items(self.ALERTS, moves)

        self.assertEqual(len(removed), 1)
        self.assertNotIn("易方达蓝筹精选混合", out, "预警已报过的基金不该再出现在异动里")
        self.assertIn("招商中证白酒指数", out, "不重复的异动要保留")

    def test_same_fund_by_code_is_deduped(self):
        alerts = "🔔 持仓预警:\n🔴 110011 近 30 日回撤 12%"
        moves = "📊 今日异动\n• 110011 -2.3%\n• 161725 +1.1%"
        _p, out, removed = dedup_push_items(alerts, moves)

        self.assertEqual(len(removed), 1)
        self.assertNotIn("110011", out)
        self.assertIn("161725", out)

    def test_different_entities_all_kept(self):
        moves = "📊 今日异动\n• 招商中证白酒指数 +1.1%\n• 国泰纳斯达克100 +0.9%"
        _p, out, removed = dedup_push_items(self.ALERTS, moves)

        self.assertEqual(removed, [])
        self.assertEqual(out, moves)

    def test_combined_message_shows_it_once(self):
        """整条推送里同一只基金只应该出现一次。"""
        moves = "📊 今日异动\n• 易方达蓝筹精选混合 -2.3%"
        primary, secondary, _ = dedup_push_items(self.ALERTS, moves)
        combined = primary + "\n" + secondary
        self.assertEqual(combined.count("易方达蓝筹精选混合"), 1)

    def test_empty_secondary(self):
        for sec in ("", None):
            p, s, removed = dedup_push_items(self.ALERTS, sec)
            self.assertEqual(p, self.ALERTS)
            self.assertEqual(removed, [])

    def test_empty_primary_keeps_secondary(self):
        moves = "📊 今日异动\n• 招商中证白酒指数 +1.1%"
        _p, out, removed = dedup_push_items("", moves)
        self.assertEqual(removed, [])
        self.assertEqual(out, moves)

    def test_generic_words_do_not_trigger_false_dedup(self):
        """通用词（持仓/预警/异动）不能造成误删。"""
        primary = "🔔 持仓预警:\n🟡 某基金 估值偏高"
        secondary = "📊 今日异动\n• 半导体板块整体走强\n• 银行板块资金流入"
        _p, out, removed = dedup_push_items(primary, secondary)
        self.assertEqual(removed, [], "板块级描述不该被误判成持仓实体")
        self.assertEqual(out, secondary)


# ============================================================
# 6. 负面控制：旧的「和稀泥」写法必须已下线
# ============================================================

class TestOldHedgeRemoved(unittest.TestCase):
    """P1-8 的洞是"插一句提示让用户自己猜"，这个写法不能再回来。"""

    #: 旧的和稀泥文案。它们只能出现在注释（讲历史）里，
    #: 绝不能出现在任何**字符串字面量**里 —— 进了字面量就意味着还能被推出去。
    OLD_HEDGE_PHRASES = (
        "两者口径不同，请结合自身情况判断",
        "管家今日判断为观望，而下方 AI 诊断含操作建议",
    )

    def test_hedge_phrase_gone_from_cron(self):
        cron_path = _BACKEND_ROOT / "scripts" / "stock_monitor_cron.py"
        src = cron_path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(cron_path))

        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for phrase in self.OLD_HEDGE_PHRASES:
            for lit in literals:
                self.assertNotIn(
                    phrase, lit,
                    f"旧的和稀泥兜底文案仍作为字符串存在于代码里：{lit[:60]!r}")

    def test_conflict_guard_still_wired_to_arbitration(self):
        """AST 断言**使用点**：冲突判断命中后必须往 msg_parts 追加裁决结论。"""
        cron_path = _BACKEND_ROOT / "scripts" / "stock_monitor_cron.py"
        src = cron_path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(cron_path))

        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test_src = ast.get_source_segment(src, node.test) or ""
            if ("_steward_says_hold(" in test_src
                    and "_ACTION_WORD_RE.search(" in test_src):
                found = True
                body_src = "".join(ast.get_source_segment(src, b) or ""
                                   for b in node.body)
                self.assertIn("msg_parts.append", body_src)
                self.assertIn("render_conflict_conclusion", body_src,
                              "冲突命中后必须走统一裁决出口，不能再自己拼提示语")
                break
        self.assertTrue(found, "冲突检测的接线点丢了")

    def test_cron_uses_shared_arbitration_module(self):
        """裁决 / 闸门 / 去重都复用 night_worker，不能各处各写一份。"""
        src = (_BACKEND_ROOT / "scripts" / "stock_monitor_cron.py").read_text(
            encoding="utf-8")
        for fn in ("arbitrate_advice", "apply_executability_gate",
                   "dedup_push_items", "render_conflict_conclusion"):
            self.assertIn(fn, src, f"run_close_review 未接入 {fn}")


# ============================================================
# 7. 不破坏防幻觉链路
# ============================================================

class TestHallucinationChainUnaffected(unittest.TestCase):
    """闸门产出的新文案不能反过来触发幻觉扫描（等于自己造了脏数据）。"""

    def test_gated_text_passes_hallucination_scanner(self):
        from unittest import mock
        from scripts.night_worker import _inject_hallucination_label

        out, _ = apply_executability_gate(
            "💡 建议分批止盈三分之一", total_value=754.0)
        with mock.patch("services.stock_monitor.load_stock_holdings",
                        return_value=[]), \
             mock.patch("services.fund_monitor.load_fund_holdings",
                        return_value=[]), \
             mock.patch("urllib.request.urlopen",
                        side_effect=RuntimeError("no net")):
            scanned = _inject_hallucination_label({"u": out})["u"]

        self.assertEqual(scanned, out, "闸门文案被幻觉扫描改写了")
        self.assertNotIn("AI质检", scanned)

    def test_arbitration_text_passes_hallucination_scanner(self):
        from unittest import mock
        from scripts.night_worker import _inject_hallucination_label

        arb = arbitrate_advice([
            {"source": "steward", "action": "hold", "reason": "没啥操作的必要"},
            {"source": "llm_diagnosis", "action": "reduce", "reason": "止盈一部分"},
        ])
        with mock.patch("services.stock_monitor.load_stock_holdings",
                        return_value=[]), \
             mock.patch("services.fund_monitor.load_fund_holdings",
                        return_value=[]), \
             mock.patch("urllib.request.urlopen",
                        side_effect=RuntimeError("no net")):
            scanned = _inject_hallucination_label({"u": arb["reason"]})["u"]

        self.assertNotIn("AI质检", scanned)


if __name__ == "__main__":
    unittest.main()
