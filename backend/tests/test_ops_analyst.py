"""ops_analyst.py 单测：规则阈值 / 一票否决 / 兜底报告 / 基线冷启动。

纯函数测试，不调 LLM、不联网、不推送。conftest.py 已把 DATA_DIR 隔离到临时目录。
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR / "scripts"))

import ops_analyst as oa  # noqa: E402


def _snapshot(**overrides):
    """构造一份「全健康」基准快照，按需覆盖字段。"""
    base = {
        "date": "2026-09-06",
        "generated_at": "2026-09-06T08:03:00",
        "freshness": [
            {"name": "数据源健康巡检", "last_updated": "2026-09-06T08:00:00", "stale_days": 0, "max_stale_days": 1, "ok": True},
            {"name": "周度自检", "stale_days": 0, "ok": True},
            {"name": "LLM 用量", "ok": True},
            {"name": "余额监控", "ok": True},
        ],
        "summary": {"checks": 4, "stale_count": 0, "overall_ok": True},
        "disk": {"total_gb": 39.3, "used_gb": 13.3, "free_gb": 24.2, "ok": True},
        "llm_balance": {"checked": True, "balances": {"deepseek": "¥31.15"}, "arrears": []},
        "error_logs_24h": {"count_24h": 0, "files": []},
    }
    base.update(overrides)
    return base


def _write_snapshot(ops_dir: Path, snap: dict) -> Path:
    ops_dir.mkdir(parents=True, exist_ok=True)
    p = ops_dir / f"snapshot_{snap['date']}.json"
    p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return p


def _analyst(tmp_path: Path) -> oa.OpsAnalyst:
    return oa.OpsAnalyst(ops_dir=tmp_path)


# ---- rule_triage：各档阈值 ----

def test_rule_triage_disk_thresholds(tmp_path):
    a = _analyst(tmp_path)
    assert a.rule_triage(_snapshot(disk={"free_gb": 4.0, "ok": False}))["per_dim"]["disk"] == "critical"
    assert a.rule_triage(_snapshot(disk={"free_gb": 7.0, "ok": True}))["per_dim"]["disk"] == "warn"
    assert a.rule_triage(_snapshot(disk={"free_gb": 24.2, "ok": True}))["per_dim"]["disk"] == "info"


def test_rule_triage_error_logs_thresholds_and_escalation(tmp_path):
    a = _analyst(tmp_path)
    assert a.rule_triage(_snapshot(error_logs_24h={"count_24h": 12, "files": []}))["per_dim"]["error_logs"] == "critical"
    assert a.rule_triage(_snapshot(error_logs_24h={"count_24h": 5, "files": []}))["per_dim"]["error_logs"] == "warn"
    assert a.rule_triage(_snapshot(error_logs_24h={"count_24h": 1, "files": []}))["per_dim"]["error_logs"] == "info"
    # 含 Traceback 上浮一档：info -> warn
    r = a.rule_triage(_snapshot(error_logs_24h={"count_24h": 1, "files": [{"file": "x.log", "keyword": "Traceback"}]}))
    assert r["per_dim"]["error_logs"] == "warn"
    # 含 Exception 上浮一档：warn -> critical
    r = a.rule_triage(_snapshot(error_logs_24h={"count_24h": 5, "files": [{"file": "x.log", "keyword": "Exception"}]}))
    assert r["per_dim"]["error_logs"] == "critical"


def test_rule_triage_llm_balance_thresholds(tmp_path):
    a = _analyst(tmp_path)
    assert a.rule_triage(_snapshot(llm_balance={"checked": True, "balances": {}, "arrears": ["deepseek"]}))["per_dim"]["llm_balance"] == "critical"
    assert a.rule_triage(_snapshot(llm_balance={"checked": True, "balances": {}, "arrears": ["doubao"]}))["per_dim"]["llm_balance"] == "critical"
    assert a.rule_triage(_snapshot(llm_balance={"checked": True, "balances": {}, "arrears": ["qwen"]}))["per_dim"]["llm_balance"] == "warn"
    assert a.rule_triage(_snapshot(llm_balance={"checked": False, "balances": {}, "arrears": []}))["per_dim"]["llm_balance"] == "warn"
    assert a.rule_triage(_snapshot(llm_balance={"checked": True, "balances": {}, "arrears": []}))["per_dim"]["llm_balance"] == "info"


def test_rule_triage_freshness_capped_at_warn(tmp_path):
    a = _analyst(tmp_path)
    snap = _snapshot(freshness=[{"name": "数据源健康巡检", "stale_days": 82, "max_stale_days": 1, "ok": False}])
    r = a.rule_triage(snap)
    assert r["per_dim"]["freshness"] == "warn"  # 规则引擎封顶 warn，绝不 critical
    assert r["overall"] == "warn"


def test_rule_triage_overall_is_max(tmp_path):
    a = _analyst(tmp_path)
    # disk critical + freshness warn → overall critical
    snap = _snapshot(
        disk={"free_gb": 4.0, "ok": False},
        freshness=[{"name": "数据源健康巡检", "stale_days": 82, "max_stale_days": 1, "ok": False}],
    )
    assert a.rule_triage(snap)["overall"] == "critical"


# ---- merge_verdict：一票否决 ----

def _llm(overall="info"):
    return {
        "overall_verdict": overall,
        "summary": "",
        "dimensions": [],
        "critical_items": [],
        "warn_items": [],
        "report_text": "",
        "_model": "deepseek-v4-pro",
    }


def test_merge_verdict_critical_never_downgraded(tmp_path):
    a = _analyst(tmp_path)
    rule = {"overall": "critical", "per_dim": {}, "reasons": ["磁盘致命"]}
    assert a.merge_verdict(rule, _llm("info"))["overall_verdict"] == "critical"
    assert a.merge_verdict(rule, _llm("warn"))["overall_verdict"] == "critical"
    # rule warn + llm info → warn（LLM 不能降级 rule 的 warn）
    assert a.merge_verdict({"overall": "warn", "per_dim": {}, "reasons": []}, _llm("info"))["overall_verdict"] == "warn"
    # llm 可升级：rule info + llm warn → warn
    assert a.merge_verdict({"overall": "info", "per_dim": {}, "reasons": []}, _llm("warn"))["overall_verdict"] == "warn"
    # 双 info → info
    assert a.merge_verdict({"overall": "info", "per_dim": {}, "reasons": []}, _llm("info"))["overall_verdict"] == "info"


# ---- fallback_report ----

def test_fallback_report_non_empty(tmp_path):
    a = _analyst(tmp_path)
    rule = {
        "overall": "warn",
        "per_dim": {"freshness": "warn", "disk": "info", "llm_balance": "warn", "error_logs": "info"},
        "reasons": ["qwen 欠费"],
    }
    rep = a.fallback_report(rule, _snapshot())
    assert rep["source"] == "rule_fallback"
    assert rep["model"] == ""
    assert rep["overall_verdict"] == "warn"
    assert rep["report_text"].strip()  # 输出非空


# ---- build_history：冷启动 + 30 天上限 ----

def test_build_history_cold_start_marks_insufficient(tmp_path):
    a = _analyst(tmp_path)
    snap = _snapshot(date="2026-09-06")
    _write_snapshot(tmp_path, snap)
    hist = a.build_history()
    assert len(hist) == 1
    ctx = a.compute_context(snap, hist)
    assert ctx["derived"]["history_days"] == 1
    assert ctx["derived"]["disk_trend_30d"] == "insufficient"


def test_build_history_caps_at_30(tmp_path):
    a = _analyst(tmp_path)
    base = date(2026, 9, 6)
    for i in range(35):
        d = (base - timedelta(days=i)).isoformat()
        _write_snapshot(tmp_path, _snapshot(date=d))
    hist = a.build_history()
    assert len(hist) == 30
    assert hist[0]["date"] == (base - timedelta(days=29)).isoformat()  # 最早保留
    assert hist[-1]["date"] == base.isoformat()  # 最新


def test_compute_context_derived_aggregates(tmp_path):
    a = _analyst(tmp_path)
    # 3 天历史：disk 自由值递减，验证趋势与极值
    snaps = [
        _snapshot(date="2026-09-04", disk={"free_gb": 26.0, "ok": True}),
        _snapshot(date="2026-09-05", disk={"free_gb": 25.0, "ok": True}),
        _snapshot(date="2026-09-06", disk={"free_gb": 24.0, "ok": True}),
    ]
    for s in snaps:
        _write_snapshot(tmp_path, s)
    hist = a.build_history()
    ctx = a.compute_context(snaps[-1], hist)
    assert ctx["derived"]["history_days"] == 3
    assert ctx["derived"]["disk_free_gb_now"] == 24.0
    assert ctx["derived"]["disk_free_gb_7d_min"] == 24.0
    assert ctx["derived"]["disk_free_gb_7d_max"] == 26.0
    # 历史 < 7 天 → 趋势标 insufficient
    assert ctx["derived"]["disk_trend_30d"] == "insufficient"
