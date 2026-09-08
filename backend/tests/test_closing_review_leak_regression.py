#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收盘复盘推送「脏内容」回归测试 —— 2026-09-02 线上事故锁定。

事故现场（企微推送原文，用户贴出）::

    📉 "direction": "bearish", "confidence": 58, "conclusion": "高波震荡偏弱…", "reasoning": "
    🔍 怎么回事：
    • 资金在往防御板块（银行/公用事业）躲
    • 我们需要输出严格 JSON
    • 输入：用户问题收盘复盘，市场状态 high_vol_bear，多个模块
    📊 本日异动明细（11 条）
    • 浦银安盛全球智能科技(QDII)A(006555)：
    • 财通科技创新混合C(008984)：

三类根因（已定位，本文件只负责把它钉死在测试里）：

    A. JSON 泄漏
       pipeline_runner.py 解析失败后回退 content[:200] 把原文当结论；
       decision_context.py 的清理只删花括号，反而造出无括号裸片段；
       下游三道检测正则一律要求字面 "{"，全部失效；
       整条 stock_monitor_cron.py 链路从未接入 LLMOutputGuard。

    B. Prompt / 思维链泄漏
       _extract_human_points() 兜底分支把 reasoning 原文按标点切片直接当 bullet，
       上游 _sanitize_reasoning_for_extraction() 的黑名单不覆盖
       「输入：」「我们需要输出」「市场状态」和 regime 枚举值。

    C. 异动明细空内容
       基金侧产出键是 message，股票侧是 msg，而 cron 六处只读 msg，
       导致基金侧 alert 全部渲染成「名字(代码)：」空壳。

    D. 防误杀（与 A/B/C 同等重要）
       修复不得误伤正常中文引号冒号，不得把 1.406% / 0.856% 这类小数
       截取成 406% / 856% 触发「异常涨幅」告警（本项目历史坑）。

覆盖范围与函数全部取自事故前（git HEAD）已存在的实现，不在本文件里
依赖修复过程中新增的任何符号 —— 这样同一份文件既能跑修复前的红色基线，
也能跑修复后的绿色回归。

运行方式（本地，务必用这条）::

    cd backend && env -u PYTHONPATH \
        /Users/leijiang/.workbuddy/binaries/python/envs/default/bin/python \
        -m pytest tests/test_closing_review_leak_regression.py -v -rfEX

为什么必须 `env -u PYTHONPATH`：托管 python 的 PYTHONPATH 指向 WorkBuddy 的
sitecustomize.py shim，它会拦截 config.py 模块级的 USERS_DIR.mkdir()，把大量用例
打成 ERROR，看起来像代码坏了其实是跑法不对。

【不要】用 `DATA_DIR=/tmp/xxx` 去绕那个 mkdir 报错 —— 那是本文件早期版本里写过的
错误做法，它只是把 ERROR 变成别的状态，并没有真正解决问题，还会污染跑出来的数字。
（2026-09-04 踩过，全量跑因此虚低 44 个 passed、虚高 40 个 errors。）

CI / 服务器（venv 依赖齐全，没有 shim）直接跑即可::

    cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python \
        -m pytest tests/test_closing_review_leak_regression.py -q -rfEX

`-rfEX` 里的 X 不能省：E2 是 xfail，将来若被修好会变成 XPASS，不加 -rX 只会看到
计数变化、不会点名是哪一条。

本地全量跑会有约 134 个 failed，全部集中在 test_task8_akshare_timeout.py /
test_market_stocks_timeout.py 等 5 个 akshare / tushare 相关文件，是本地 venv 缺
这两个包所致，与代码质量无关 —— 本地绝对数不可信，权威结论以服务器为准。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from unittest import mock

import pytest

from services.decision_context import DecisionContext
from infra.llm.gateway import LLMGateway
from services.llm_output_guard import LLMOutputGuard
from services.pipeline_runner import step_llm_arbitration
from services.steward import _sanitize_reasoning_for_user
from scripts import stock_monitor_cron as cron
from scripts.night_worker import _inject_hallucination_label

_FORMAT_REVIEW = cron._format_review_for_push
_SANITIZE_FOR_EXTRACTION = cron._sanitize_reasoning_for_extraction
_EXTRACT_HUMAN_POINTS = cron._extract_human_points


# ============================================================
# 共用断言工具
# ============================================================

# 「"字段名":」形态的键值对片段。这是 JSON 泄漏最本质的特征 ——
# 无论有没有花括号，只要它出现在给用户的文本里就是脏内容。
# 不依赖 "{" 的存在，因为 decision_context 的清理恰恰会把花括号删掉。
JSON_KV_RE = re.compile(r'"[A-Za-z_][A-Za-z0-9_]*"\s*:\s*')

# 元话术：属于 prompt 指令 / 系统内部状态，绝不该出现在给用户的 bullet 里
META_MARKERS = (
    "输入：",
    "输入:",
    "high_vol_bear",
    "trending_bull",
    "oscillating",
    "rotation",
    "我们需要输出",
)

# _format_review_for_push 的降级兜底文案
REVIEW_FALLBACK = "📊 收盘复盘完成，请打开钱袋子查看详情"


def assert_no_json_kv(text: str, where: str = "") -> None:
    """断言文本里没有 "字段名": 形态的键值对片段"""
    hit = JSON_KV_RE.search(text or "")
    assert hit is None, (
        f"{where} 仍存在 JSON 键值对片段 {hit.group(0)!r}\n"
        f"--- 实际产出 ---\n{text}"
    )


def assert_no_meta(text: str, where: str = "") -> None:
    """断言文本里没有 prompt 元话术残留"""
    for marker in META_MARKERS:
        assert marker not in (text or ""), (
            f"{where} 仍残留元话术 {marker!r}\n--- 实际产出 ---\n{text}"
        )
    assert "严格json" not in (text or "").replace(" ", "").lower(), (
        f"{where} 仍残留「严格 JSON」话术\n--- 实际产出 ---\n{text}"
    )


# ============================================================
# 事故原样素材
# ============================================================

# 用户贴出的那段脏推送（去掉行号，原样保留脏内容）
DIRTY_PUSH_TEXT = (
    '📉 "direction": "bearish", "confidence": 58, '
    '"conclusion": "高波震荡偏弱，防御为上，控制仓位。", "reasoning": "\n'
    "🔍 怎么回事：\n"
    "• 资金在往防御板块（银行/公用事业）躲\n"
    "• 我们需要输出严格 JSON\n"
    "• 输入：用户问题收盘复盘，市场状态 high_vol_bear，多个模块\n"
    "📊 本日异动明细（11 条）\n"
    "• 浦银安盛全球智能科技(QDII)A(006555)：\n"
    "• 财通科技创新混合C(008984)：\n"
)

# 未闭合 JSON：模拟 max_tokens 截断（供应商未上报 finish_reason=length 的情况，
# 这是线上真正踩中的路径）。长度刻意 >200，让 content[:200] 真的截断。
_TRUNCATED_BODY = (
    "资金在往防御板块（银行/公用事业）躲，市场广度偏弱，动量指标转负，"
    "风控模块提示组合回撤扩大，市场状态 high_vol_bear，多个模块结论一致偏空，"
    "建议控制仓位，等波动率回落之后再考虑分批加仓，不要急着抄底，"
    "持仓里的成长方向暂时没有看到明确的资金回流迹象，先按防守处理更稳妥，"
    "等两市成交额重新放大、涨跌家数回到均衡之后再评估要不要把仓位加回去。"
)
TRUNCATED_JSON = (
    '{"direction": "bearish", "confidence": 58, '
    '"conclusion": "高波震荡偏弱，防御为上，控制仓位。", '
    f'"reasoning": "{_TRUNCATED_BODY}"'
)  # 注意：结尾故意没有右花括号

# 合法闭合 JSON（对照组，验证修复没有把正常解析一起打掉）
CLOSED_JSON = json.dumps(
    {
        "direction": "bearish",
        "confidence": 58,
        "conclusion": "高波震荡偏弱，防御为上，控制仓位。",
        "reasoning": "资金在往防御板块躲，市场广度偏弱。",
    },
    ensure_ascii=False,
)

# 混进了元话术的 reasoning（B 类素材）
META_REASONING = (
    "今日两市缩量整理，成交比昨天少了一成。"
    "输入：用户问题收盘复盘，市场状态 high_vol_bear，多个模块。"
    "我们需要输出严格 JSON。"
)


# ============================================================
# A 类：JSON 泄漏
# ============================================================

class TestAJsonLeak:
    """JSON 泄漏 —— 脏内容不得原样穿过守卫送达用户"""

    def test_a1_filter_analysis_strips_json_kv(self):
        """A1-a：用户贴出的那段脏文本，filter_analysis 后不得保留 "direction" 字样"""
        cleaned = LLMOutputGuard.filter_analysis(
            DIRTY_PUSH_TEXT, fallback="（分析暂时不可用）"
        )
        assert "direction" not in cleaned, (
            f"filter_analysis 放行了 JSON 字段名\n--- 实际产出 ---\n{cleaned}"
        )
        assert_no_json_kv(cleaned, "LLMOutputGuard.filter_analysis")

    def test_a1_filter_diagnosis_must_not_pass_through(self):
        """A1-b：严格模式下不得被原样放过"""
        cleaned = LLMOutputGuard.filter_diagnosis(DIRTY_PUSH_TEXT)
        assert cleaned != DIRTY_PUSH_TEXT, (
            "filter_diagnosis 原样放行了脏文本，严格模式形同虚设"
        )
        assert "direction" not in cleaned, (
            f"filter_diagnosis 放行了 JSON 字段名\n--- 实际产出 ---\n{cleaned}"
        )
        assert_no_json_kv(cleaned, "LLMOutputGuard.filter_diagnosis")

    def test_a2_truncated_json_full_chain(self):
        """A2：未闭合 JSON 走完整链路（解析 → sanitize → 格式化）后不得有键值对片段"""
        ctx = _run_arbitration(TRUNCATED_JSON)
        resp = ctx.to_user_response()          # sanitize: 剥花括号
        push = _FORMAT_REVIEW(resp)            # 格式化: 组推送文本

        assert push.strip(), "格式化产出为空，等于把整条推送吃掉了"
        assert_no_json_kv(push, "未闭合 JSON 全链路")

    def test_a3_closed_json_still_parses(self):
        """A3-a：已闭合的合法 JSON 必须能正常解析出 conclusion，不被误判为泄漏"""
        ctx = _run_arbitration(CLOSED_JSON)
        assert ctx.conclusion == "高波震荡偏弱，防御为上，控制仓位。", (
            f"合法 JSON 未被正确解析，conclusion={ctx.conclusion!r}"
        )

        push = _FORMAT_REVIEW(ctx.to_user_response())
        assert push.strip() != REVIEW_FALLBACK, (
            "合法 JSON 被误判成泄漏，整条复盘降级成了兜底文案"
        )
        assert "高波震荡偏弱" in push, (
            f"合法 JSON 的 conclusion 丢失\n--- 实际产出 ---\n{push}"
        )
        assert_no_json_kv(push, "合法 JSON 全链路")

    def test_a3_closed_json_string_input(self):
        """A3-b：JSON 字符串形态直接喂给格式化函数，同样要被解析而不是被当泄漏"""
        push = _FORMAT_REVIEW(CLOSED_JSON)
        assert push.strip() != REVIEW_FALLBACK
        assert "高波震荡偏弱" in push
        assert_no_json_kv(push, "JSON 字符串输入")


# ============================================================
# B 类：Prompt / 思维链泄漏
# ============================================================

class TestBPromptLeak:
    """prompt 元话术不得作为「怎么回事」要点推给用户"""

    def test_b1_sanitize_removes_input_prefix_and_regime_enum(self):
        """B1：「输入：…」「high_vol_bear」必须被清掉"""
        out = _SANITIZE_FOR_EXTRACTION(META_REASONING)
        assert "输入：" not in out, (
            f"「输入：」残留\n--- 实际产出 ---\n{out}"
        )
        assert "high_vol_bear" not in out, (
            f"regime 枚举值残留\n--- 实际产出 ---\n{out}"
        )

    def test_b2_sanitize_removes_we_need_to_output_json(self):
        """B2：「我们需要输出严格 JSON」必须被清掉"""
        out = _SANITIZE_FOR_EXTRACTION(META_REASONING)
        assert "我们需要输出" not in out, (
            f"「我们需要输出」残留\n--- 实际产出 ---\n{out}"
        )
        assert "严格json" not in out.replace(" ", "").lower(), (
            f"「严格 JSON」残留\n--- 实际产出 ---\n{out}"
        )

    def test_b3_extract_human_points_after_sanitize(self):
        """B3-a：sanitize 之后再提取，兜底切片不得产出元话术 bullet"""
        cleaned = _SANITIZE_FOR_EXTRACTION(META_REASONING)
        points = _EXTRACT_HUMAN_POINTS(cleaned, {"direction": "neutral"})
        for p in points:
            assert_no_meta(p, "_extract_human_points（sanitize 后）")

    def test_b3b_extract_human_points_defensive_on_raw_input(self):
        """B3-b：即使上游漏了 sanitize，_extract_human_points 自身也要挡住"""
        points = _EXTRACT_HUMAN_POINTS(META_REASONING, {"direction": "neutral"})
        for p in points:
            assert_no_meta(p, "_extract_human_points（原始输入）")

    def test_b4_steward_sanitizer_same_contract(self):
        """B4：services/steward.py 的同名清理函数必须符合同一套契约"""
        out = _sanitize_reasoning_for_user(META_REASONING)
        assert "输入：" not in out
        assert "high_vol_bear" not in out
        assert "我们需要输出" not in out


# ============================================================
# C 类：异动明细渲染
# ============================================================

def _render_daily_summary(alerts: list) -> str:
    """用给定 alert 列表跑一遍 build_daily_summary_text()"""
    pool = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "users": {"qa_leak": {f"k{i}": a for i, a in enumerate(alerts)}},
    }
    with mock.patch.object(cron, "_load_daily_pool", lambda: pool):
        return cron.build_daily_summary_text()["qa_leak"]


def _line_for(text: str, hint: str):
    """取渲染文本里包含 hint 的第一行（找不到返回 None）"""
    for line in (text or "").splitlines():
        if hint in line:
            return line
    return None


class TestCAlertRender:
    """异动明细不得渲染成「名字(代码)：」空壳"""

    def test_c1_fund_alert_message_key(self):
        """C1：只有 message 键的基金 alert，冒号后不得为空"""
        text = _render_daily_summary(
            [
                {
                    "type": "drawdown",
                    "code": "006555",
                    "fund": "浦银安盛全球智能科技(QDII)A(006555)",
                    "level": "warning",
                    "message": "🔻 60日最大回撤 8.3%（07-15→08-20），当前仍在低位（距高点 -6.2%）",
                }
            ]
        )
        line = _line_for(text, "006555")
        assert line is not None, f"基金 alert 未被渲染\n--- 实际产出 ---\n{text}"
        tail = line.split("：", 1)[1].strip() if "：" in line else ""
        assert tail != "", f"基金 alert 渲染成空壳：{line!r}"

    def test_c2_stock_alert_msg_key(self):
        """C2：只有 msg 键的股票 alert，冒号后同样不得为空（防误杀）"""
        text = _render_daily_summary(
            [
                {
                    "type": "drop",
                    "code": "600519",
                    "name": "贵州茅台",
                    "level": "warning",
                    "msg": "📉 贵州茅台(600519) 跌幅 -5.3%，关注是否需要止损",
                }
            ]
        )
        line = _line_for(text, "600519")
        assert line is not None, f"股票 alert 未被渲染\n--- 实际产出 ---\n{text}"
        tail = line.split("：", 1)[1].strip() if "：" in line else ""
        assert tail != "", f"股票 alert 渲染成空壳：{line!r}"

    def test_c3_missing_both_keys_renders_fallback(self):
        """C3：两个键都缺失时，应渲染兜底文案而不是裸冒号"""
        text = _render_daily_summary(
            [
                {
                    "type": "hot",
                    "code": "008984",
                    "fund": "财通科技创新混合C(008984)",
                    "level": "info",
                }
            ]
        )
        line = _line_for(text, "008984")
        assert line is not None, f"缺内容的 alert 未被渲染\n--- 实际产出 ---\n{text}"
        if "：" in line:
            tail = line.split("：", 1)[1].strip()
            assert tail != "", f"渲染出裸冒号：{line!r}"
        else:
            bare = "• 财通科技创新混合C(008984)"
            assert len(line.strip()) > len(bare), f"缺兜底文案：{line!r}"


# ============================================================
# D 类：防误杀
# ============================================================

# 正常中文：含引号 + 冒号，是「JSON 泄漏」正则最容易误伤的形态
NORMAL_QUOTE_TEXT = (
    '监管层强调"房住不炒"：政策延续，地产链短期仍是托而不举的格局。\n'
    "银行与公用事业等防御方向今天更受资金青睐。"
)
NORMAL_QUOTE_PHRASE = '监管层强调"房住不炒"：政策延续'


class TestDNoFalsePositive:
    """修复不得过度 —— 正常文本必须原样保留"""

    @pytest.mark.parametrize(
        "name,fn",
        [
            ("cron._sanitize_reasoning_for_extraction", _SANITIZE_FOR_EXTRACTION),
            ("steward._sanitize_reasoning_for_user", _sanitize_reasoning_for_user),
        ],
    )
    def test_d1_sanitizers_keep_normal_quote_colon(self, name, fn):
        """D1-a：正常中文引号冒号不得被 sanitizer 改动"""
        out = fn(NORMAL_QUOTE_TEXT)
        assert NORMAL_QUOTE_PHRASE in out, (
            f"{name} 误伤了正常中文\n--- 实际产出 ---\n{out}"
        )

    def test_d1_guard_keeps_normal_quote_colon(self):
        """D1-b：LLMOutputGuard 不得改动正常中文引号冒号"""
        out = LLMOutputGuard.filter_analysis(NORMAL_QUOTE_TEXT)
        assert NORMAL_QUOTE_PHRASE in out, (
            f"filter_analysis 误伤了正常中文\n--- 实际产出 ---\n{out}"
        )
        out = LLMOutputGuard.filter_diagnosis(NORMAL_QUOTE_TEXT)
        assert NORMAL_QUOTE_PHRASE in out, (
            f"filter_diagnosis 误伤了正常中文\n--- 实际产出 ---\n{out}"
        )

    def test_d1_decision_context_keeps_normal_quote_colon(self):
        """D1-c：DecisionContext 的输出清理不得改动正常中文引号冒号"""
        ctx = DecisionContext(conclusion=NORMAL_QUOTE_TEXT)
        out = ctx.to_user_response()["conclusion"]
        assert NORMAL_QUOTE_PHRASE in out, (
            f"DecisionContext 误伤了正常中文\n--- 实际产出 ---\n{out}"
        )

    def test_d2_decimal_percentage_not_flagged(self):
        """D2：1.406% / 0.856% 这类小数不得被截取成 406% / 856% 触发告警"""
        text = (
            "☀️ 早安！\n\n"
            "资金面(银行间利率): 1.406% (平稳)\n"
            "估算偏差 0.856%\n"
            "建议：先别乱动"
        )
        with mock.patch("services.stock_monitor.load_stock_holdings", return_value=[]), \
             mock.patch("services.fund_monitor.load_fund_holdings", return_value=[]), \
             mock.patch("urllib.request.urlopen", side_effect=RuntimeError("no network")):
            out = _inject_hallucination_label({"qa_leak": text})["qa_leak"]

        assert "异常涨幅数字" not in out, (
            f"小数百分比被误判成异常涨幅\n--- 实际产出 ---\n{out}"
        )
        assert "1.406%" in out and "0.856%" in out, (
            f"小数百分比被改动\n--- 实际产出 ---\n{out}"
        )

    def test_d2b_real_exaggeration_still_flagged(self):
        """D2 对照：真正的夸大数字（320%）仍必须被抓出来

        2026-09-09 修正用例文本：原文是「该基金近1年涨幅 320%」，但「近1年」
        本身就在长周期限定语词表里 —— Bug4 修复（EXAG_TIME_QUAL_RE 去掉 `$`
        锚定）之后，这类表述属**合规**长周期涨幅，不该再被标记。

        也就是说旧文本把「Bug4 的误报行为」当成了对照组期望，与修复后的语义
        直接冲突。这里把限定语换成非长周期的「今日」，保留本用例的原始意图
        ——「没有长周期限定语的孤立夸大数字必须被抓出来」。
        """
        text = "该基金今日涨幅 320%，非常可观"
        with mock.patch("services.stock_monitor.load_stock_holdings", return_value=[]), \
             mock.patch("services.fund_monitor.load_fund_holdings", return_value=[]), \
             mock.patch("urllib.request.urlopen", side_effect=RuntimeError("no network")):
            out = _inject_hallucination_label({"qa_leak": text})["qa_leak"]
        assert "异常涨幅数字" in out, (
            f"异常涨幅检测被削弱，320% 没被抓到\n--- 实际产出 ---\n{out}"
        )

    def test_d3_decimal_percentage_survives_sanitize(self):
        """D3：小数百分比不得被 sanitizer 改动"""
        raw = "估算偏差 0.856%，银行间利率 1.406%，组合今日下跌 1.2%"
        out = _SANITIZE_FOR_EXTRACTION(raw)
        assert "0.856%" in out and "1.406%" in out and "1.2%" in out, (
            f"小数百分比被改动\n--- 实际产出 ---\n{out}"
        )


# ============================================================
# E 类：误杀面加固（与 D 类同源，但盯的是"过度拦截"这一类错误）
# ============================================================
# D 类盯的是"该拦的没拦住"，E 类盯的是反方向："不该拦的被拦了"。
# 两者同等重要——把正常内容整段降级成"分析暂时不可用"，对用户来说
# 和看到一段 JSON 一样是坏的，只是坏的方向相反。

# E1 历史上是真实缺陷：filter_analysis 默认 min_len=20，会把 20 字以下的
# 合法短结论整段降级成兜底文案。其中 `高波震荡偏弱，防御为上，控制仓位。`
# （17 字）正是 2026-09-02 事故 JSON 里 conclusion 字段的原文——也就是说
# 这个阈值和"LLM 正常输出的合法结论长度"是直接冲突的，不是边缘 case。
#
# v9.9.10 已把默认 min_len 由 20 下调到 10，并且删掉了所有调用点的显式
# min_len 传参（阈值只留一处，不再分叉到调用点）。本组用例因此从 xfail
# 升格为正式回归保护：谁再把默认阈值调回 17 以上，这几条立刻变红。
#
# 注释里标了每条的长度，改阈值时能一眼看出会撞到哪条。
SHORT_OK_CONCLUSIONS = [
    "资金在往防御板块（银行/公用事业）躲",      # 17 字
    "今天市场偏弱，小心一点",                    # 11 字
    "高波震荡偏弱，防御为上，控制仓位。",        # 17 字 ← 事故 JSON 的 conclusion 原文
    "成交缩量，观望为主。",                      # 10 字 ← 正好压在 min_len=10 的边界上
]

# 白名单正则只认带引号的键（"direction": "bearish"），无引号键（direction: bearish）抓不到。
UNQUOTED_KEY_LEAK = (
    "📉 direction: bearish, confidence: 58, "
    "conclusion: 高波震荡偏弱，防御为上，控制仓位。"
)


@pytest.mark.parametrize("text", SHORT_OK_CONCLUSIONS)
def test_e1_filter_analysis_min_len_over_blocks(text):
    """E1：短但完全合法的结论，不应被 min_len 判死"""
    out = LLMOutputGuard.filter_analysis(text, fallback="（分析暂时不可用）")
    assert out == text, f"合法短结论被 min_len 误杀\n--- 实际产出 ---\n{out}"


# E2 是本次回归确认后**仍未修**的已知缺口，不属于 2026-09-02 事故范围
# （线上泄漏形态是带引号的键，本次够用）。用 xfail(strict=False) 而不是跳过
# 或直接不写，是为了让缺口留在代码里可被机器发现：
#   - 现在：xfailed（不红，不阻塞发版）
#   - 将来被修好：XPASS（不红，但需要 -rX 才点名，见文件头运行说明）
# 谁修好了请顺手摘掉这个 marker，让它升格成正式用例。
@pytest.mark.xfail(
    strict=False,
    reason="已知缺口：泄漏检测只认带引号的键，无引号键 direction: bearish 抓不到",
)
def test_e2_unquoted_json_keys_are_caught():
    """E2：无引号键的 JSON 泄漏同样应该被挡住"""
    out = LLMOutputGuard.filter_analysis(
        UNQUOTED_KEY_LEAK, fallback="（分析暂时不可用）"
    )
    assert "direction" not in out, (
        f"无引号键的 JSON 泄漏被放行\n--- 实际产出 ---\n{out}"
    )


# ============================================================
# 辅助：跑一遍真实的 LLM 仲裁解析链路（网关用假实现替换）
# ============================================================

class _FakeGateway:
    """假 LLM 网关：只返回预置 content，不发任何网络请求"""

    def __init__(self, content: str):
        self._content = content

    def call_sync(self, prompt: str, **kwargs) -> dict:
        return {
            "content": self._content,
            "reasoning": "",
            "model": "mock-model",
            "fallback": False,
            "usage": {},
        }


def _run_arbitration(content: str) -> DecisionContext:
    """让 step_llm_arbitration 真实跑一遍（含它自己的三段式 JSON 解析）"""
    ctx = DecisionContext(user_id="qa_leak", question="收盘复盘", trigger="cron")
    ctx.regime = "high_vol_bear"
    ctx.regime_description = "高波动偏弱"
    ctx.gate_decision = "llm_arbitration"
    ctx.gate_reason = "qa 强制走仲裁"
    ctx.confidence_score = 0.45
    ctx.divergence = 0.6
    ctx.modules_results = {
        "breadth": {
            "available": True,
            "direction": "bearish",
            "confidence": 60,
            "detail": "市场广度偏弱",
        }
    }

    fake = _FakeGateway(content)
    with mock.patch.object(LLMGateway, "instance", staticmethod(lambda: fake)):
        step_llm_arbitration(ctx)
    return ctx


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
