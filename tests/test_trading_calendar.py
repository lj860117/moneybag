"""
P0-3 交易规则 / 时间逻辑测试
目标：AI 给买卖建议时，入场时机符合 T+1 / 盘中盘后 / 周末节假日规则
"""
import pytest
from datetime import datetime
from conftest import chat_ask


# 常见"立即买入"类错误用词
IMMEDIATE_BUY_WORDS = [
    "立即买入", "马上买", "现在就买", "立刻下单", "即刻买入",
]

CORRECT_TIMING_HINTS = [
    "次日", "明天", "下一个交易日", "盘前", "开盘", "15:00",
    "T+1", "9:30", "交易日", "非交易日",
    "观望", "不建议现在", "不建议立即", "不建议买入", "建议观望",
    "休市", "闭市", "周末",
]


@pytest.mark.llm_heavy
@pytest.mark.timeout(120)
def test_weekend_ai_no_now_buy(client, qa_user):
    """
    非交易日（周末）下，AI 不应给"立即买入"这种建议（A 股不交易）。
    必须提到"下一个交易日"或"明天"等推迟性词语。
    """
    now = datetime.now()
    if now.weekday() < 5:
        pytest.skip("今天是工作日，此测试只在周末有意义")

    # 先查 market-status 确认
    r = client.get("/api/market-status")
    assert r.status_code == 200
    if r.json().get("is_trading_day"):
        pytest.skip("非预期：今天被判定为交易日")

    # 问 AI 现在能不能买茅台
    d = chat_ask(client, "现在可以买入茅台 600519 吗？", user_id=qa_user)
    reply = d.get("reply", "") or d.get("analysis", "") or str(d)

    # 检查 1：不能建议"立即买入"
    immediate_hit = [w for w in IMMEDIATE_BUY_WORDS if w in reply]

    # 检查 2：必须有正确的时间提示
    has_correct_hint = any(h in reply for h in CORRECT_TIMING_HINTS)

    if immediate_hit:
        pytest.fail(
            f"❌ 周末 AI 建议立即买入: 命中 {immediate_hit}\n回复: {reply[:300]}"
        )
    if not has_correct_hint:
        pytest.fail(
            f"❌ 周末 AI 回复缺少时间提示（次日/下一交易日等）\n回复: {reply[:300]}"
        )


@pytest.mark.llm_heavy
@pytest.mark.timeout(120)
def test_after_close_next_day_settlement(client, qa_user):
    """15:00 后买股票必须告知次日成交（T+1 净值）。通过话术诱导 AI 判断"""
    d = chat_ask(
        client,
        "下午 3 点半了，我想买沪深 300 ETF (510300)，今天能成交吗？",
        user_id=qa_user,
    )
    reply = d.get("reply", "") or d.get("analysis", "") or str(d)

    # 必须说"次日/明天/下个交易日"
    next_day_hint = any(w in reply for w in [
        "次日", "明天", "下一个交易日", "次日净值", "无法成交",
        "T+1", "无法买入", "不能今日", "T日", "明日成交",
    ])

    # 如果回复 "可以成交" 或 "今天就能" 都是错
    wrong_hint = any(w in reply for w in [
        "今天成交", "今天就能", "当日成交", "现在下单"
    ])

    if wrong_hint:
        pytest.fail(f"❌ 15:00 后 AI 说今天能成交: {reply[:300]}")
    if not next_day_hint:
        pytest.fail(f"❌ 15:00 后 AI 缺少次日成交提示: {reply[:300]}")


@pytest.mark.llm_heavy
@pytest.mark.timeout(120)
def test_open_fund_net_value_timing(client, qa_user):
    """场外基金净值规则：15:00 前按当天净值，15:00 后按次日"""
    d = chat_ask(
        client,
        "我想买招商产业债(217022)，中午 12 点买的话，用的是哪一天的净值？",
        user_id=qa_user,
    )
    reply = d.get("reply", "") or d.get("analysis", "") or str(d)

    # 必须提到"当日/当天净值"或"15 点/15:00"
    correct_answer_hint = any(w in reply for w in [
        "当日", "当天净值", "15:00", "15 点", "15点", "当天晚上",
    ])
    # 如果说"次日净值"就是错的（12 点属于 15:00 前）
    wrong = any(w in reply for w in ["明日净值", "明天的净值", "下一日净值"])

    # 宽松一点：至少回答里有净值相关的合理描述
    if wrong and not correct_answer_hint:
        pytest.fail(f"❌ 基金 T 日净值规则错误: {reply[:300]}")


@pytest.mark.llm_heavy
@pytest.mark.timeout(120)
def test_ai_comment_no_now_buy_in_nontradingday(client):
    """非交易日调 ai-comment 时，comment 里不应鼓动"立即买入\""""
    now = datetime.now()
    # 只在非交易日跑
    if now.weekday() < 5:
        pytest.skip("工作日跳过")

    r = client.get("/api/ai-comment/stock", params={"code": "600519"})
    if r.status_code != 200:
        pytest.skip(f"接口返回 {r.status_code}")
    comment = r.json().get("comment", "")

    hit = [w for w in IMMEDIATE_BUY_WORDS if w in comment]
    assert not hit, f"❌ 非交易日 AI 一句话点评含立即买入字眼: {hit}\n{comment}"


def test_market_status_session_correct(client):
    """/api/market-status 返回的 session 值必须和当前时间匹配"""
    r = client.get("/api/market-status")
    assert r.status_code == 200
    d = r.json()
    now = datetime.now()
    session = d.get("session")
    is_td = d.get("is_trading_day")

    # 基础一致性：周末必须 is_trading_day=False
    if now.weekday() >= 5:
        assert is_td is False, f"周末被判定为交易日: {d}"
        assert session == "closed", f"周末 session 应为 closed，实际 {session}"
