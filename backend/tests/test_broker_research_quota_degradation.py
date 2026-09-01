"""
研报共识"限额耗尽降级"归因守护测试
============================================================================
背景（2026-09 周度自检 medium 级问题）：`self_audit.py` 探针"研报共识"结果
`sample_bias="所有研报均无明确评级字段，无法提取多空信号"`，10篇研报
`unrated_count=10`，`bullish_count=0`。

根因链（team-lead 排查确认，本文件测试的正是修复后的行为）：
  1. `services/broker_research.py::get_latest_reports()` 主数据源是 Tushare
     `report_rc`，该接口账号级每日限额仅10次。
  2. 调用入口分散在多个独立进程（cache_warmer 交易时段每30分钟一次、
     night_worker、broker_rating_cron、recommend_engine、api/broker.py
     用户实时请求），各自维护自己的进程内 MemoryCache，互不通信，导致
     同一份"每日10次"硬限额被多个进程分别消耗，交易时段早盘就能打穿限额。
  3. 一旦 Tushare 路径失败（限额超限），`get_latest_reports()` 降级到
     `infra/data_source/macro/indicators.py::get_stock_news()`（AKShare
     东财研报标题），该降级源结构上不带 `rating` 字段，导致全部研报被
     计入 unrated——这不是"数据源没有评级"，是"主数据源限额被打穿后，
     降级源天生缺评级字段"的架构问题。

修复内容：
  1. `services/tushare_data.py` 新增跨进程共享的 report_rc 每日额度计数器
     （落盘 `DATA_DIR/_cache/tushare_quota.json` + fcntl.flock 排他锁），
     `_call_tushare("report_rc", ...)` 调用前先检查额度，耗尽时直接返回
     空列表，不发起真实请求。`get_report_rc_quota_status()` 提供只读查询。
  2. `services/broker_research.py::get_broker_consensus()` 在"全部无评级"
     分支里新增 `degraded_reason` 字段，区分三种情况：
       - "quota_exhausted_fallback_no_rating"：主数据源限额耗尽降级
       - "primary_source_unavailable_fallback_no_rating"：主数据源本次
         未返回数据但额度未耗尽（网络/配置问题）
       - "source_has_no_rating"：主数据源本身就没有评级字段（真实数据
         质量问题）
     并同步改写 `sample_bias` 文案，让"降级原因"和"数据源真的没评级"
     区分清楚。

这个文件测什么：
  - `get_broker_consensus()` 在上述三种"全部无评级"场景下，`degraded_reason`
    和 `sample_bias` 是否被正确区分（用 monkeypatch 替换 `get_latest_reports`
    和 `get_report_rc_quota_status` 模拟不同数据源组合，不发真实网络请求）。
  - `services/tushare_data.py` 的额度计数器：连续调用超过每日限额后，
    `_consume_report_rc_quota()` 必须在第11次开始返回 `ok=False`；
    `get_report_rc_quota_status()` 的只读查询不消耗额度；跨日后额度重置。
  - 正常场景（有评级、评级不为空）不应被误判为"降级"——防止矫枉过正。

不测什么：
  - 真实网络请求 Tushare/AKShare 接口（不适合单测，也不应该消耗真实的
    每日10次限额）。
  - `use_cases/self_audit.py` 的 LLM 审计层 prompt 是否真的让 LLM 输出
    正确结论（那需要真实 LLM 调用，属于集成测试范畴，这里只测数据源头
    是否携带了正确的归因字段）。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ============================================================
# services/tushare_data.py — report_rc 跨进程共享额度计数器
# ============================================================

def _isolate_quota_file(monkeypatch, td_module, tmp_path):
    """把 report_rc 额度计数器文件重定向到本测试专属的 tmp_path。

    NOTE: `config.DATA_DIR` 是模块级常量，首次 import 时就被 conftest.py
    锁定为整个 pytest session 共享的临时目录，之后单个测试再
    `monkeypatch.setenv("DATA_DIR", ...)` 并不会让 `_quota_file_path()`
    里的 `from config import DATA_DIR` 读到新值（模块已被缓存）。因此这里
    直接 monkeypatch `_quota_file_path` 本身，让它返回测试专属路径——
    这样才能保证各测试之间的额度状态互不污染。
    """
    quota_dir = tmp_path / "_cache"
    quota_dir.mkdir(parents=True, exist_ok=True)
    quota_fp = quota_dir / "tushare_quota.json"
    monkeypatch.setattr(td_module, "_quota_file_path", lambda: quota_fp)
    return quota_fp


def test_report_rc_quota_blocks_after_daily_limit_reached(tmp_path, monkeypatch):
    """连续消耗额度超过每日限额（10次）后，第11次起必须被拒绝（ok=False），
    且已消耗次数不再增加——这是"额度耗尽后不再浪费真实请求"的核心保证。"""
    import services.tushare_data as td
    _isolate_quota_file(monkeypatch, td, tmp_path)

    results = [td._consume_report_rc_quota() for _ in range(12)]

    # 前10次应该都成功，used 从1递增到10
    for i in range(10):
        ok, used = results[i]
        assert ok is True, f"第{i+1}次调用应该在限额内被允许"
        assert used == i + 1

    # 第11、12次应该被拒绝，used 保持在10（不继续递增，不重复计数）
    ok_11, used_11 = results[10]
    ok_12, used_12 = results[11]
    assert ok_11 is False
    assert ok_12 is False
    assert used_11 == 10
    assert used_12 == 10


def test_report_rc_quota_status_is_read_only_and_does_not_consume(tmp_path, monkeypatch):
    """get_report_rc_quota_status() 是只读查询，重复调用不应消耗额度——
    否则调用方每次"检查一下还有没有额度"都会白白扣掉一次，额度会被
    查询本身耗尽，这是这个函数存在的核心意义。"""
    import services.tushare_data as td
    _isolate_quota_file(monkeypatch, td, tmp_path)

    # 先消耗3次真实额度
    for _ in range(3):
        td._consume_report_rc_quota()

    # 反复查询状态10次，used 不应该变化
    for _ in range(10):
        status = td.get_report_rc_quota_status()
        assert status["used"] == 3
        assert status["remaining"] == 7
        assert status["exhausted"] is False

    # 再消耗到耗尽
    for _ in range(7):
        td._consume_report_rc_quota()
    status = td.get_report_rc_quota_status()
    assert status["exhausted"] is True
    assert status["used"] == 10
    assert status["remaining"] == 0


def test_report_rc_quota_resets_on_new_day(tmp_path, monkeypatch):
    """额度计数器按日期隔离——昨天写入的耗尽状态，今天读取时应视为0已用
    （不能让"昨天用完了"污染"今天"的额度判断，否则会永久卡死）。"""
    import services.tushare_data as td
    quota_fp = _isolate_quota_file(monkeypatch, td, tmp_path)

    # 手动写入"昨天已用满10次"的状态文件
    quota_fp.write_text(
        json.dumps({"date": "20200101", "used": 10}), encoding="utf-8"
    )

    status = td.get_report_rc_quota_status()
    assert status["used"] == 0, "跨日后应视为今日尚未使用过额度"
    assert status["exhausted"] is False

    ok, used = td._consume_report_rc_quota()
    assert ok is True
    assert used == 1


def test_call_tushare_skips_real_request_when_report_rc_quota_exhausted(tmp_path, monkeypatch):
    """`_call_tushare("report_rc", ...)` 在额度耗尽时应直接返回空列表，
    不发起真实网络请求——用 monkeypatch 断言 urlopen 从未被调用。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-for-test")
    import services.tushare_data as td
    quota_fp = _isolate_quota_file(monkeypatch, td, tmp_path)

    # 提前把今日额度打满
    quota_fp.write_text(
        json.dumps({"date": td._today_str(), "used": td.REPORT_RC_DAILY_LIMIT}),
        encoding="utf-8",
    )

    called = {"count": 0}

    def _should_not_be_called(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("额度耗尽时不应发起真实 urlopen 请求")

    monkeypatch.setattr(td.urllib.request, "urlopen", _should_not_be_called)
    td._ts_cache.clear()  # 避免其他测试写入的进程内缓存命中，掩盖本次真实调用路径

    result = td._call_tushare("report_rc", {"limit": 30}, "ts_code,rating")

    assert result == []
    assert called["count"] == 0


def test_call_tushare_other_apis_not_affected_by_report_rc_quota(tmp_path, monkeypatch):
    """限额只应作用于 report_rc 这一个 api_name，其他 Tushare 接口（如
    daily_basic）即使 report_rc 额度耗尽，也不受影响——避免限流逻辑
    误伤其他没有每日次数上限的高积分接口。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-for-test")
    import services.tushare_data as td
    quota_fp = _isolate_quota_file(monkeypatch, td, tmp_path)
    td._ts_cache.clear()

    quota_fp.write_text(
        json.dumps({"date": td._today_str(), "used": td.REPORT_RC_DAILY_LIMIT}),
        encoding="utf-8",
    )

    class _FakeResp:
        def read(self):
            return json.dumps({
                "data": {
                    "fields": ["ts_code", "pe"],
                    "items": [["600519.SH", 30.5]],
                }
            }).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        td.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResp()
    )

    result = td._call_tushare("daily_basic", {"ts_code": "600519.SH"}, "ts_code,pe")
    assert result == [{"ts_code": "600519.SH", "pe": 30.5}]


# ============================================================
# services/broker_research.py — degraded_reason 归因区分
# ============================================================

def _reload_broker_research(monkeypatch, tmp_path):
    """重新加载 broker_research，隔离 DATA_DIR 并清空进程内缓存。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import services.broker_research as br
    importlib.reload(br)
    br._broker_cache.clear()
    return br


def test_consensus_marks_quota_exhausted_when_all_fallback_and_quota_used_up(tmp_path, monkeypatch):
    """全部研报来自 AKShare 降级源（无 rating），且 report_rc 今日额度已
    耗尽 —— 必须标注 degraded_reason='quota_exhausted_fallback_no_rating'，
    这是本次 bug 修复的核心场景：区分"限额耗尽降级"和"数据源真没评级"。
    """
    br = _reload_broker_research(monkeypatch, tmp_path)

    def _fake_reports(limit=30):
        return [
            {"title": f"研报{i}", "date": "2026-08-30", "source": "akshare_eastmoney"}
            for i in range(10)
        ]

    monkeypatch.setattr(br, "get_latest_reports", _fake_reports)

    import services.tushare_data as td
    monkeypatch.setattr(
        td, "get_report_rc_quota_status",
        lambda: {"exhausted": True, "used": 10, "limit": 10, "remaining": 0, "date": "20260901"},
    )

    result = br.get_broker_consensus()

    assert result["degraded_reason"] == "quota_exhausted_fallback_no_rating"
    assert result["unrated_count"] == 10
    assert result["total_reports"] == 10
    assert "限额已耗尽" in result["sample_bias"]
    assert "10/10" in result["sample_bias"]
    # 明确排除误导性文案：不应让人误以为"数据源天生没有评级字段"是主因，
    # 应该点出"这是限额降级的正常行为"这个真实归因。
    assert "正常的降级行为" in result["sample_bias"] or "正常降级行为" in result["sample_bias"]


def test_consensus_marks_primary_source_unavailable_when_fallback_but_quota_not_exhausted(tmp_path, monkeypatch):
    """全部研报来自 AKShare 降级源，但 report_rc 今日额度还有剩余——说明
    不是限额问题，是主数据源本次请求本身失败（网络/配置），应标注
    'primary_source_unavailable_fallback_no_rating'，不能跟限额耗尽混淆。
    """
    br = _reload_broker_research(monkeypatch, tmp_path)

    def _fake_reports(limit=30):
        return [
            {"title": f"研报{i}", "date": "2026-08-30", "source": "akshare_eastmoney"}
            for i in range(10)
        ]

    monkeypatch.setattr(br, "get_latest_reports", _fake_reports)

    import services.tushare_data as td
    monkeypatch.setattr(
        td, "get_report_rc_quota_status",
        lambda: {"exhausted": False, "used": 2, "limit": 10, "remaining": 8, "date": "20260901"},
    )

    result = br.get_broker_consensus()

    assert result["degraded_reason"] == "primary_source_unavailable_fallback_no_rating"
    assert "非限额耗尽" in result["sample_bias"]


def test_consensus_marks_source_has_no_rating_when_tushare_reports_lack_rating(tmp_path, monkeypatch):
    """研报明确来自 tushare（主数据源本身返回成功），但 rating 字段全部
    为空——这是真实的数据质量问题，不是降级，应标注 'source_has_no_rating'，
    LLM 审计层此时应该真的上报为问题，不能被豁免。"""
    br = _reload_broker_research(monkeypatch, tmp_path)

    def _fake_reports(limit=30):
        return [
            {"title": f"研报{i}", "date": "2026-08-30", "source": "tushare", "rating": ""}
            for i in range(10)
        ]

    monkeypatch.setattr(br, "get_latest_reports", _fake_reports)

    result = br.get_broker_consensus()

    assert result["degraded_reason"] == "source_has_no_rating"
    assert "非限额降级导致" in result["sample_bias"]


def test_consensus_normal_scenario_has_no_degraded_reason(tmp_path, monkeypatch):
    """正常场景：研报带有明确评级，能正常统计多空——degraded_reason 应为
    空字符串，不应误报降级（防止矫枉过正，把正常数据也标成降级）。"""
    br = _reload_broker_research(monkeypatch, tmp_path)

    def _fake_reports(limit=30):
        return [
            {"title": "看好白酒板块", "date": "2026-08-30", "source": "tushare",
             "rating": "买入", "org": "中金公司"},
            {"title": "维持中性评级", "date": "2026-08-30", "source": "tushare",
             "rating": "中性", "org": "中信证券"},
            {"title": "建议减持", "date": "2026-08-30", "source": "tushare",
             "rating": "减持", "org": "国泰君安"},
        ]

    monkeypatch.setattr(br, "get_latest_reports", _fake_reports)

    result = br.get_broker_consensus()

    assert result["degraded_reason"] == ""
    assert result["bullish_count"] == 1
    assert result["neutral_count"] == 1
    assert result["bearish_count"] == 1
    assert result["unrated_count"] == 0
    assert result["consensus"] != "数据不足"


def test_consensus_no_reports_at_all_returns_default_without_degraded_reason_crash(tmp_path, monkeypatch):
    """`get_latest_reports()` 返回空列表（两个数据源都失败）时，
    `get_broker_consensus()` 应该走早退分支返回默认 result，且不应因为
    访问 degraded_reason 相关逻辑而抛异常（默认 result 里 degraded_reason
    已初始化为空字符串）。"""
    br = _reload_broker_research(monkeypatch, tmp_path)
    monkeypatch.setattr(br, "get_latest_reports", lambda limit=30: [])

    result = br.get_broker_consensus()

    assert result["available"] is False
    assert result["degraded_reason"] == ""
    assert result["total_reports"] == 0


# ============================================================
# QA 补充回归（2026-09 独立复核）：额度检查前的缓存短路 + 环境变量覆盖
# 这两条路径此前只被 team-lead/工程师口头验证过，没有落成自动化回归测试。
# ============================================================

def test_call_tushare_cache_hit_does_not_consume_report_rc_quota(tmp_path, monkeypatch):
    """`_call_tushare()` 的进程内缓存命中必须发生在额度检查之前——模块顶部
    注释明确声明"缓存命中不计入消耗"，这是 report_rc 额度器设计的核心前提
    之一（否则同一份数据被重复展示/重试也会白白扣额度）。用预热好的缓存
    连续调用 10 次，断言 get_report_rc_quota_status().used 始终为 0。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-for-test")
    import services.tushare_data as td
    _isolate_quota_file(monkeypatch, td, tmp_path)

    cache_key = "report_rc_" + json.dumps({"limit": 30}, sort_keys=True) + "_ts_code,rating"
    td._ts_cache.set(cache_key, [{"ts_code": "600519.SH", "rating": "买入"}], ttl=3600)

    for _ in range(10):
        result = td._call_tushare("report_rc", {"limit": 30}, "ts_code,rating")
        assert result == [{"ts_code": "600519.SH", "rating": "买入"}]

    status = td.get_report_rc_quota_status()
    assert status["used"] == 0, "缓存命中不应该消耗任何 report_rc 每日额度"


def test_report_rc_daily_limit_env_override_takes_effect_in_fresh_process():
    """`TUSHARE_REPORT_RC_DAILY_LIMIT` 环境变量覆盖必须在【全新进程】首次
    import `services.tushare_data` 时生效——`REPORT_RC_DAILY_LIMIT` 是模块
    级常量，只在 import 那一刻读取一次环境变量，同进程内后续再改环境变量
    不会生效。这条路径不能用同进程 monkeypatch.setenv 验证（因为模块早已
    被其他测试 import 过、常量已固定），必须真的起一个子进程验证，否则这
    个"可通过环境变量覆盖默认10次限额"的说法永远没有自动化回归保护。"""
    backend_dir = str(BACKEND_DIR)
    default_code = (
        f"import sys; sys.path.insert(0, {backend_dir!r}); "
        "import services.tushare_data as td; print(td.REPORT_RC_DAILY_LIMIT)"
    )
    override_code = default_code  # 同样的代码，靠子进程环境变量区分

    default_proc = subprocess.run(
        [sys.executable, "-c", default_code],
        capture_output=True, text=True, timeout=30,
        env={k: v for k, v in os.environ.items() if k != "TUSHARE_REPORT_RC_DAILY_LIMIT"},
    )
    assert default_proc.returncode == 0, default_proc.stderr
    assert default_proc.stdout.strip() == "10", "未设置环境变量时默认限额应为10"

    override_env = dict(os.environ)
    override_env["TUSHARE_REPORT_RC_DAILY_LIMIT"] = "3"
    override_proc = subprocess.run(
        [sys.executable, "-c", override_code],
        capture_output=True, text=True, timeout=30,
        env=override_env,
    )
    assert override_proc.returncode == 0, override_proc.stderr
    assert override_proc.stdout.strip() == "3", "设置环境变量后应覆盖默认限额"
