import json
import sys
import time
import types
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

def test_save_cache_can_replace_readonly_existing_file(tmp_path, monkeypatch):
    import scripts.cache_warmer as cache_warmer

    monkeypatch.setattr(cache_warmer, "CACHE_DIR", tmp_path)

    target = tmp_path / "stock_screen_50.json"
    target.write_text(json.dumps({"data": {"old": True}}), encoding="utf-8")
    target.chmod(0o444)

    cache_warmer._save_cache("stock_screen_50", {"stocks": [{"code": "600519"}]}, ttl_hours=18)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["data"]["stocks"][0]["code"] == "600519"
    assert payload["ttl_hours"] == 18


def test_get_technical_indicators_uses_memory_cache_interface(monkeypatch):
    import services.technical as technical

    cache_key = "tech_sh000300"
    expected = {
        "rsi": 55.5,
        "macd": {"macd": 0.1, "dif": 0.2, "dea": 0.1, "trend": "多头排列（DIF/DEA均在0轴上方）"},
        "bollinger": {"upper": 1, "middle": 1, "lower": 1, "current": 1, "position": "中轨上方（偏强）"},
        "rsi_signal": "中性",
    }

    technical._tech_cache.clear()
    technical._tech_cache.set(cache_key, expected, ttl=3600)

    result = technical.get_technical_indicators("sh000300")

    assert result == expected


def test_config_data_dir_defaults_to_project_root(monkeypatch):
    import importlib
    import sys

    backend_dir = Path(__file__).resolve().parents[1]
    project_root = backend_dir.parent

    monkeypatch.chdir(backend_dir)
    monkeypatch.delenv("DATA_DIR", raising=False)
    sys.modules.pop("config", None)

    cfg = importlib.import_module("config")

    assert cfg.DATA_DIR.resolve() == (project_root / "data").resolve()


def test_generate_daily_signal_hold_confidence_uses_consistency_floor(monkeypatch):
    import services.signal as signal_module

    monkeypatch.setattr(signal_module, "get_technical_indicators", lambda: {
        "rsi": 58,
        "macd": {"trend": "MACD金叉但仍在0轴下方，反弹信号（非趋势反转）"},
        "bollinger": {"position": "价格在中轨上方，偏强但注意回调"},
    })
    monkeypatch.setattr(signal_module, "get_valuation_percentile", lambda: {"percentile": 19.3, "current_pe": 19.61})
    monkeypatch.setattr(signal_module, "get_dividend_yield", lambda: {"available": True, "percentile": 20, "dividend_yield": 0.72})
    monkeypatch.setattr(signal_module, "get_treasury_yield", lambda: {"available": True, "yield_10y": 1.733, "equity_premium": "股市有吸引力"})
    monkeypatch.setattr(signal_module, "get_northbound_flow", lambda: {"available": False})
    monkeypatch.setattr(signal_module, "get_margin_trading", lambda: {"available": True, "margin_change_5d": 1.03, "margin_balance": 9278.49})
    monkeypatch.setattr(signal_module, "get_shibor", lambda: {"available": True, "overnight": 1.36, "trend": "流动性平稳"})
    monkeypatch.setattr(signal_module, "get_fear_greed_index", lambda: {"score": 50})
    monkeypatch.setattr(signal_module, "get_news_sentiment_score", lambda: {"available": True, "score": 0, "level": "中性", "source": "test"})
    monkeypatch.setattr(signal_module, "get_macro_calendar", lambda: [
        {"name": "制造业PMI", "value": "50.3"},
    ])
    monkeypatch.setattr(signal_module, "get_market_news", lambda: [])

    fake_geo = types.ModuleType("services.geopolitical")
    fake_geo.get_geopolitical_risk_score = lambda: {"available": True, "score": 0, "level": "low", "top_events": []}
    monkeypatch.setitem(sys.modules, "services.geopolitical", fake_geo)

    result = signal_module.generate_daily_signal()

    assert result["overall"] == "HOLD"
    assert 10 < result["score"] < 20
    assert result["confidence"] > 50
    assert "_confidence_degraded" not in result


def test_cfo_summary_force_refresh_rewrites_fresh_file(tmp_path, monkeypatch):
    fake_services_steward = types.ModuleType("services.steward")
    fake_services_steward.get_steward = lambda: None
    monkeypatch.setitem(sys.modules, "services.steward", fake_services_steward)

    fake_regime_engine = types.ModuleType("services.regime_engine")
    fake_regime_engine.classify = lambda: {"regime": "neutral"}
    monkeypatch.setitem(sys.modules, "services.regime_engine", fake_regime_engine)

    fake_llm_gateway = types.ModuleType("services.llm_gateway")
    fake_llm_gateway.llm_usage = lambda user_id="": {}
    monkeypatch.setitem(sys.modules, "services.llm_gateway", fake_llm_gateway)

    fake_weekly_report = types.ModuleType("services.weekly_report")
    fake_weekly_report.generate = lambda *args, **kwargs: {}
    fake_weekly_report.get_history = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "services.weekly_report", fake_weekly_report)

    import api.steward as steward

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cache_dir = tmp_path / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_fp = cache_dir / "cfo_summary_LeiJiang.json"
    cache_fp.write_text(
        json.dumps({"data": {"timestamp": "old"}, "created_at": time.time() - 3600}, ensure_ascii=False),
        encoding="utf-8",
    )

    fake_cfo = types.ModuleType("services.cfo_dashboard")
    fake_cfo.generate_cfo_summary = lambda user_id: {"timestamp": "new", "user": user_id}
    monkeypatch.setitem(sys.modules, "services.cfo_dashboard", fake_cfo)

    result = steward.cfo_summary(userId="LeiJiang", force=True)
    payload = json.loads(cache_fp.read_text(encoding="utf-8"))

    assert result["timestamp"] == "new"
    assert payload["data"]["timestamp"] == "new"
    assert payload["data"]["user"] == "LeiJiang"


def test_health_does_not_flag_missing_cfo_cache_as_degraded(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cache_dir = tmp_path / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "market_context.txt").write_text("fresh", encoding="utf-8")

    fake_cfg = types.ModuleType("config")
    fake_cfg.APP_VERSION = "test"
    monkeypatch.setitem(sys.modules, "config", fake_cfg)

    fake_data_layer = types.ModuleType("services.data_layer")
    for name in [
        "get_fund_nav", "get_fear_greed_index", "get_valuation_percentile",
        "get_technical_indicators", "get_market_news", "get_macro_calendar",
        "get_northbound_flow", "get_margin_trading", "get_treasury_yield",
        "get_shibor", "get_dividend_yield", "get_news_sentiment_score",
    ]:
        setattr(fake_data_layer, name, lambda *args, **kwargs: {})
    monkeypatch.setitem(sys.modules, "services.data_layer", fake_data_layer)

    fake_llm_gateway = types.ModuleType("services.llm_gateway")

    class _FakeGateway:
        @staticmethod
        def instance():
            return _FakeGateway()

        def check_budget(self):
            return {}

    fake_llm_gateway.LLMGateway = _FakeGateway
    monkeypatch.setitem(sys.modules, "services.llm_gateway", fake_llm_gateway)

    sys.modules.pop("api.dashboard", None)
    dashboard = importlib.import_module("api.dashboard")

    result = dashboard.health()

    assert result["keys_status"]["deepseek"] == "missing"
    assert result["keys_status"]["doubao"] == "missing"
    assert result["keys_status"]["qwen"] == "missing"
    assert "CFO摘要(无缓存)" not in result["data_health"]["degraded"]
    assert result["data_health"]["overall"] == "ok"


def test_warm_evening_refreshes_cfo_summary_for_active_users(tmp_path, monkeypatch):
    import scripts.cache_warmer as cache_warmer

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "profiles.json").write_text(
        json.dumps([
            {"id": "LeiJiang"},
            {"id": "BuLuoGeLi"},
            {"id": "Guest"},
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(cache_warmer, "_is_trading_day", lambda: True)
    monkeypatch.setattr(cache_warmer, "_save_cache", lambda *args, **kwargs: None)

    fake_global = types.ModuleType("services.global_market")
    fake_global.get_global_snapshot = lambda: {"ok": True}
    monkeypatch.setitem(sys.modules, "services.global_market", fake_global)

    fake_news = types.ModuleType("services.news_data")
    fake_news.get_market_news = lambda limit=20: []
    monkeypatch.setitem(sys.modules, "services.news_data", fake_news)

    fake_policy = types.ModuleType("services.policy_data")
    fake_policy.get_all_policy_topics = lambda: []
    monkeypatch.setitem(sys.modules, "services.policy_data", fake_policy)

    fake_fund_monitor = types.ModuleType("services.fund_monitor")
    fake_fund_monitor.scan_all_fund_holdings = lambda uid: {"holdings": []}
    monkeypatch.setitem(sys.modules, "services.fund_monitor", fake_fund_monitor)

    stock_scan_calls = []
    fake_stock_monitor = types.ModuleType("services.stock_monitor")

    def _fake_scan_all_holdings(uid):
        stock_scan_calls.append(uid)
        return {"holdings": []}

    fake_stock_monitor.scan_all_holdings = _fake_scan_all_holdings
    monkeypatch.setitem(sys.modules, "services.stock_monitor", fake_stock_monitor)

    calls = []
    fake_requests = types.ModuleType("requests")

    class _Resp:
        ok = True
        status_code = 200

    def _fake_get(url, timeout=0):
        calls.append(url)
        return _Resp()

    fake_requests.get = _fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    cache_warmer.warm_evening()

    assert stock_scan_calls == ["LeiJiang", "BuLuoGeLi"]
    assert "http://127.0.0.1:8000/api/cfo-summary?userId=LeiJiang&force=1" in calls
    assert "http://127.0.0.1:8000/api/cfo-summary?userId=BuLuoGeLi&force=1" in calls
    assert not any("Guest" in url and "cfo-summary" in url for url in calls)


def test_sector_ranking_prefers_actual_change_pct_column(monkeypatch):
    import pandas as pd
    import services.sector_rotation as sector_rotation

    rows = [
        {
            "板块": "医疗器械",
            "涨跌幅": 2.11,
            "总成交额": 120.5,
            "净流入": 8.6,
            "上涨家数": 35,
            "下跌家数": 12,
            "领涨股": "龙头A",
            "领涨股-涨跌幅": 21.17,
        },
        {
            "板块": "化学制药",
            "涨跌幅": 1.35,
            "总成交额": 98.2,
            "净流入": 6.4,
            "上涨家数": 20,
            "下跌家数": 18,
            "领涨股": "龙头B",
            "领涨股-涨跌幅": 30.0,
        },
    ]
    rows.extend(
        {
            "板块": f"填充行业{i}",
            "涨跌幅": round(0.8 - i * 0.05, 2),
            "总成交额": 50 + i,
            "净流入": 1 + i * 0.1,
            "上涨家数": 10 + i,
            "下跌家数": 8,
            "领涨股": f"填充股{i}",
            "领涨股-涨跌幅": 5 + i,
        }
        for i in range(10)
    )
    sample = pd.DataFrame(rows)

    fake_tushare_fallback = types.ModuleType("services.tushare_fallback")

    class _FakePrimary:
        @classmethod
        def instance(cls):
            return cls()

        def get_sector_daily(self):
            return None

    fake_tushare_fallback.TusharePrimary = _FakePrimary
    monkeypatch.setitem(sys.modules, "services.tushare_fallback", fake_tushare_fallback)

    fake_alt_flows = types.ModuleType("infra.data_source.alt.flows")
    fake_alt_flows.get_industry_board_summary = lambda: sample.copy()
    monkeypatch.setitem(sys.modules, "infra.data_source.alt.flows", fake_alt_flows)

    fake_akshare = types.ModuleType("akshare")
    fake_akshare.stock_board_industry_summary_ths = lambda: sample.copy()
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    monkeypatch.setattr(sector_rotation, "_get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_set_cached", lambda *args, **kwargs: None)

    result = sector_rotation.get_sector_ranking()

    assert result["available"] is True
    assert result["top_gainers"][0]["name"] == "医疗器械"
    assert result["top_gainers"][0]["change_pct"] == 2.11
    assert result["top_gainers"][0]["leader_chg"] == 21.17



def test_sector_ranking_filters_out_anomalous_change_pct(monkeypatch):
    import pandas as pd
    import services.sector_rotation as sector_rotation

    rows = [
        {
            "板块": "医疗器械",
            "涨跌幅": 211.7,
            "总成交额": 120.5,
            "净流入": 8.6,
            "上涨家数": 35,
            "下跌家数": 12,
            "领涨股": "龙头A",
            "领涨股-涨跌幅": 21.17,
        },
        {
            "板块": "半导体",
            "涨跌幅": 6.32,
            "总成交额": 6224.73,
            "净流入": 314.27,
            "上涨家数": 158,
            "下跌家数": 21,
            "领涨股": "格科微",
            "领涨股-涨跌幅": 20.02,
        },
        {
            "板块": "光学光电子",
            "涨跌幅": 5.90,
            "总成交额": 1533.38,
            "净流入": 104.95,
            "上涨家数": 96,
            "下跌家数": 12,
            "领涨股": "联建光电",
            "领涨股-涨跌幅": 20.04,
        },
        {
            "板块": "军工电子",
            "涨跌幅": 5.04,
            "总成交额": 448.81,
            "净流入": 40.46,
            "上涨家数": 61,
            "下跌家数": 1,
            "领涨股": "景嘉微",
            "领涨股-涨跌幅": 14.01,
        },
    ]
    rows.extend(
        {
            "板块": f"填充行业{i}",
            "涨跌幅": round(0.8 - i * 0.05, 2),
            "总成交额": 50 + i,
            "净流入": 1 + i * 0.1,
            "上涨家数": 10 + i,
            "下跌家数": 8,
            "领涨股": f"填充股{i}",
            "领涨股-涨跌幅": 5 + i,
        }
        for i in range(10)
    )
    sample = pd.DataFrame(rows)

    fake_tushare_fallback = types.ModuleType("services.tushare_fallback")

    class _FakePrimary:
        @classmethod
        def instance(cls):
            return cls()

        def get_sector_daily(self):
            return None

    fake_tushare_fallback.TusharePrimary = _FakePrimary
    monkeypatch.setitem(sys.modules, "services.tushare_fallback", fake_tushare_fallback)

    fake_alt_flows = types.ModuleType("infra.data_source.alt.flows")
    fake_alt_flows.get_industry_board_summary = lambda: sample.copy()
    monkeypatch.setitem(sys.modules, "infra.data_source.alt.flows", fake_alt_flows)

    fake_akshare = types.ModuleType("akshare")
    fake_akshare.stock_board_industry_summary_ths = lambda: sample.copy()
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    monkeypatch.setattr(sector_rotation, "_get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_set_cached", lambda *args, **kwargs: None)

    result = sector_rotation.get_sector_ranking()

    assert result["available"] is True
    assert result["anomaly_guard_triggered"] is True
    assert result["filtered_anomaly_count"] == 1
    assert [item["name"] for item in result["top_gainers"][:3]] == ["半导体", "光学光电子", "军工电子"]
    assert all(abs(item["change_pct"]) <= 15 for item in result["top_gainers"])



def test_sector_ranking_degrades_when_too_few_valid_sectors(monkeypatch):
    import pandas as pd
    import services.sector_rotation as sector_rotation

    rows = [
        {
            "板块": "半导体",
            "涨跌幅": 6.32,
            "总成交额": 6224.73,
            "净流入": 314.27,
            "上涨家数": 158,
            "下跌家数": 21,
            "领涨股": "格科微",
            "领涨股-涨跌幅": 20.02,
        },
        {
            "板块": "光学光电子",
            "涨跌幅": 5.90,
            "总成交额": 1533.38,
            "净流入": 104.95,
            "上涨家数": 96,
            "下跌家数": 12,
            "领涨股": "联建光电",
            "领涨股-涨跌幅": 20.04,
        },
    ]
    rows.extend(
        {
            "板块": f"异常行业{i}",
            "涨跌幅": 20 + i,
            "总成交额": 80 + i,
            "净流入": 5.2 + i,
            "上涨家数": 10 + i,
            "下跌家数": 10,
            "领涨股": f"异常龙头{i}",
            "领涨股-涨跌幅": 12 + i,
        }
        for i in range(8)
    )
    sample = pd.DataFrame(rows)

    fake_tushare_fallback = types.ModuleType("services.tushare_fallback")

    class _FakePrimary:
        @classmethod
        def instance(cls):
            return cls()

        def get_sector_daily(self):
            return None

    fake_tushare_fallback.TusharePrimary = _FakePrimary
    monkeypatch.setitem(sys.modules, "services.tushare_fallback", fake_tushare_fallback)

    fake_alt_flows = types.ModuleType("infra.data_source.alt.flows")
    fake_alt_flows.get_industry_board_summary = lambda: sample.copy()
    monkeypatch.setitem(sys.modules, "infra.data_source.alt.flows", fake_alt_flows)

    fake_akshare = types.ModuleType("akshare")
    fake_akshare.stock_board_industry_summary_ths = lambda: sample.copy()
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    monkeypatch.setattr(sector_rotation, "_get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_set_cached", lambda *args, **kwargs: None)

    result = sector_rotation.get_sector_ranking()

    assert result["available"] is False
    assert result["top_gainers"] == []
    assert result["error"] == "暂无明显热点板块（行业数据异常已过滤）"
    assert result["filtered_anomaly_count"] == 8



def test_tushare_primary_sector_daily_uses_unified_sw_industry_chain(monkeypatch):
    import services.tushare_fallback as tushare_fallback
    import services.tushare_data as tushare_data

    classifications = [
        {"index_code": f"8010{i:02d}.SI", "industry_name": f"行业{i}"}
        for i in range(12)
    ]
    sample_rows = [
        {
            "ts_code": item["index_code"],
            "trade_date": "20260701",
            "name": item["industry_name"],
            "pct_change": round(6.0 - idx * 0.2, 2),
            "amount": 1000 + idx * 10,
        }
        for idx, item in enumerate(classifications)
    ]

    monkeypatch.setattr(tushare_data, "is_configured", lambda: True)
    monkeypatch.setattr(tushare_data, "get_index_classify", lambda level="L1": classifications)
    monkeypatch.setattr(tushare_data, "_call_tushare", lambda api_name, params, fields="": sample_rows)

    obj = tushare_fallback.TusharePrimary.__new__(tushare_fallback.TusharePrimary)
    obj._pro = types.SimpleNamespace(
        index_daily=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call index_daily"))
    )

    result = obj.get_sector_daily(trade_date="20260701")

    assert len(result) >= 10
    assert result[0]["板块"] == "行业0"
    assert "涨跌幅" in result[0]
    assert result[0]["source"] == "tushare"



def test_sector_ranking_derives_breadth_from_change_pct_when_counts_missing(monkeypatch):
    import services.sector_rotation as sector_rotation

    tushare_rows = [
        {"板块": "通信", "涨跌幅": 4.89, "总成交额": 23407630.0, "代码": "801770.SI", "source": "tushare"},
        {"板块": "电子", "涨跌幅": 4.22, "总成交额": 117703713.0, "代码": "801080.SI", "source": "tushare"},
        {"板块": "国防军工", "涨跌幅": 3.72, "总成交额": 9514165.0, "代码": "801740.SI", "source": "tushare"},
        {"板块": "银行", "涨跌幅": -2.35, "总成交额": 3035740.0, "代码": "801780.SI", "source": "tushare"},
    ]
    tushare_rows.extend(
        {"板块": f"填充行业{i}", "涨跌幅": 0.5 - i * 0.05, "总成交额": 1000 + i, "代码": f"8019{i:02d}.SI", "source": "tushare"}
        for i in range(8)
    )

    fake_tushare_fallback = types.ModuleType("services.tushare_fallback")

    class _FakePrimary:
        @classmethod
        def instance(cls):
            return cls()

        def get_sector_daily(self):
            return list(tushare_rows)

    fake_tushare_fallback.TusharePrimary = _FakePrimary
    monkeypatch.setitem(sys.modules, "services.tushare_fallback", fake_tushare_fallback)

    monkeypatch.setattr(sector_rotation, "_get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_set_cached", lambda *args, **kwargs: None)

    result = sector_rotation.get_sector_ranking()

    assert result["available"] is True
    assert result["source"] == "tushare"
    assert result["market_breadth"]["up"] == 11
    assert result["market_breadth"]["down"] == 1
    assert result["market_breadth"]["up_pct"] == 91.7
    assert result["rotation_signal"]["style"] != "防御"



def test_get_sw_sector_daily_enriches_flow_and_breadth_from_moneyflow_dc(monkeypatch):
    import services.tushare_data as tushare_data

    classifications = [
        {"index_code": f"8010{i:02d}.SI", "industry_name": f"行业{i}"}
        for i in range(12)
    ]
    sw_rows = [
        {
            "ts_code": item["index_code"],
            "trade_date": "20260701",
            "name": item["industry_name"],
            "pct_change": round(3.6 - idx * 0.1, 2),
            "amount": 1000 + idx * 10,
        }
        for idx, item in enumerate(classifications)
    ]

    member_map = {
        "801000.SI": [
            {"l1_code": "801000.SI", "l1_name": "行业0", "ts_code": "000001.SZ", "name": "甲"},
            {"l1_code": "801000.SI", "l1_name": "行业0", "ts_code": "000002.SZ", "name": "乙"},
            {"l1_code": "801000.SI", "l1_name": "行业0", "ts_code": "000003.SZ", "name": "丙"},
        ],
        "801001.SI": [
            {"l1_code": "801001.SI", "l1_name": "行业1", "ts_code": "000004.SZ", "name": "丁"},
            {"l1_code": "801001.SI", "l1_name": "行业1", "ts_code": "000005.SZ", "name": "戊"},
        ],
    }
    for idx, item in enumerate(classifications[2:], start=2):
        member_map[item["index_code"]] = [
            {
                "l1_code": item["index_code"],
                "l1_name": item["industry_name"],
                "ts_code": f"300{idx:03d}.SZ",
                "name": f"填充股{idx}",
            }
        ]

    moneyflow_dc_rows = [
        {"ts_code": "000001.SZ", "pct_change": 1.5, "net_amount": 50.0, "name": "甲"},
        {"ts_code": "000002.SZ", "pct_change": -0.8, "net_amount": -20.0, "name": "乙"},
        {"ts_code": "000003.SZ", "pct_change": 2.3, "net_amount": 90.0, "name": "丙"},
        {"ts_code": "000004.SZ", "pct_change": 0.6, "net_amount": 12.0, "name": "丁"},
        {"ts_code": "000005.SZ", "pct_change": -1.1, "net_amount": -5.0, "name": "戊"},
    ]
    moneyflow_dc_rows.extend(
        {
            "ts_code": f"300{idx:03d}.SZ",
            "pct_change": 0.3 if idx % 2 == 0 else -0.2,
            "net_amount": float(idx),
            "name": f"填充股{idx}",
        }
        for idx in range(2, 12)
    )

    def fake_call(api_name, params, fields=""):
        if api_name == "sw_daily":
            return sw_rows
        if api_name == "index_member_all":
            return member_map.get(params.get("l1_code"), [])
        if api_name == "moneyflow_dc":
            return moneyflow_dc_rows
        return []

    monkeypatch.setattr(tushare_data, "is_configured", lambda: True)
    monkeypatch.setattr(tushare_data, "get_index_classify", lambda level="L1": classifications)
    monkeypatch.setattr(tushare_data, "_call_tushare", fake_call)

    rows = tushare_data.get_sw_sector_daily(trade_date="20260701", level="L1")

    assert len(rows) == 12
    assert rows[0]["板块"] == "行业0"
    assert rows[0]["净流入"] == 120.0
    assert rows[0]["上涨家数"] == 2
    assert rows[0]["下跌家数"] == 1
    assert rows[1]["净流入"] == 7.0
    assert rows[1]["上涨家数"] == 1
    assert rows[1]["下跌家数"] == 1



def test_get_sw_sector_daily_falls_back_to_daily_and_moneyflow_when_moneyflow_dc_unavailable(monkeypatch):
    import services.tushare_data as tushare_data

    classifications = [
        {"index_code": f"8011{i:02d}.SI", "industry_name": f"补强行业{i}"}
        for i in range(12)
    ]
    sw_rows = [
        {
            "ts_code": item["index_code"],
            "trade_date": "20260701",
            "name": item["industry_name"],
            "pct_change": round(2.8 - idx * 0.1, 2),
            "amount": 800 + idx * 10,
        }
        for idx, item in enumerate(classifications)
    ]

    member_map = {}
    daily_rows = []
    moneyflow_rows = []
    for idx, item in enumerate(classifications):
        ts_code = f"600{idx:03d}.SH"
        member_map[item["index_code"]] = [
            {"l1_code": item["index_code"], "l1_name": item["industry_name"], "ts_code": ts_code, "name": f"成员{idx}"}
        ]
        daily_rows.append({"ts_code": ts_code, "pct_chg": 1.0 if idx % 3 != 0 else -0.5})
        moneyflow_rows.append({"ts_code": ts_code, "net_mf_amount": float(10 + idx)})

    def fake_call(api_name, params, fields=""):
        if api_name == "sw_daily":
            return sw_rows
        if api_name == "index_member_all":
            return member_map.get(params.get("l1_code"), [])
        if api_name == "moneyflow_dc":
            return []
        if api_name == "daily":
            return daily_rows
        if api_name == "moneyflow":
            return moneyflow_rows
        return []

    monkeypatch.setattr(tushare_data, "is_configured", lambda: True)
    monkeypatch.setattr(tushare_data, "get_index_classify", lambda level="L1": classifications)
    monkeypatch.setattr(tushare_data, "_call_tushare", fake_call)

    rows = tushare_data.get_sw_sector_daily(trade_date="20260701", level="L1")

    assert len(rows) == 12
    assert rows[0]["板块"] == "补强行业0"
    assert rows[0]["净流入"] == 10.0
    assert rows[0]["上涨家数"] == 0
    assert rows[0]["下跌家数"] == 1
    assert rows[1]["净流入"] == 11.0
    assert rows[1]["上涨家数"] == 1
    assert rows[1]["下跌家数"] == 0



def test_stock_pick_pages_use_stock_chart_modal():
    insight_stock = (BACKEND_DIR.parent / "pages" / "insight-stock.js").read_text(encoding="utf-8")
    analysis_page = (BACKEND_DIR.parent / "pages" / "analysis.js").read_text(encoding="utf-8")

    assert "showFundChart('${cleanCode}')" in insight_stock
    assert "_showFundKlineModal('${cleanCode}'" not in insight_stock
    assert "showFundChart('${cleanCode}')" in analysis_page



def test_fund_detail_component_declares_is_my_holding_guard():
    components = (BACKEND_DIR.parent / "pages" / "_components.js").read_text(encoding="utf-8")

    assert "const isMyHolding = !!d.holding_relation;" in components



def test_my_holdings_diag_prefers_scan_cache_endpoint():
    insight_fund = (BACKEND_DIR.parent / "pages" / "insight-fund.js").read_text(encoding="utf-8")

    assert "'/fund-holdings/scan?'" in insight_fund
    assert "codes.map(c=>fetch(API_BASE+'/fund-holdings/realtime/'+c" not in insight_fund



def test_cache_warmer_preheats_user_scoped_detail_and_chart_endpoints():
    warmer = (BACKEND_DIR / "scripts" / "cache_warmer.py").read_text(encoding="utf-8")

    assert "/api/fund/detail/{code}?userId={uid}" in warmer
    assert "/api/chart/{code}?period=1y&userId={uid}" in warmer
    assert "_warm_longterm_fund_details_for_user" in warmer
    assert "长持基金详情" in warmer



def test_screen_longterm_funds_fallback_keeps_module_datetime(tmp_path, monkeypatch):
    import services.longterm_screen as longterm_screen

    monkeypatch.setattr(longterm_screen, "DATA_DIR", tmp_path)
    monkeypatch.setattr(longterm_screen, "_CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(longterm_screen, "_FUND_CACHE_FILE", tmp_path / "_cache" / "longterm_funds.json")
    longterm_screen._CACHE_DIR.mkdir(parents=True, exist_ok=True)

    rank_file = tmp_path / "fund_rank_ts.json"
    rank_file.write_text(
        json.dumps(
            {
                "ranks": {
                    "all": [
                        {
                            "code": "000001",
                            "ts_code": "000001.OF",
                            "name": "示例成长混合",
                            "return_1y": 12.0,
                            "return_3y": 36.0,
                            "list_date": "",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(longterm_screen, "_cache_valid", lambda *_args, **_kwargs: False)

    fake_tushare = types.ModuleType("services.tushare_data")
    fake_tushare.is_configured = lambda: False
    fake_tushare._call_tushare = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "services.tushare_data", fake_tushare)

    fake_industry = types.ModuleType("services.industry_templates")
    fake_industry.get_fund_industry = lambda _name: {"tag": "混合", "desc": "测试行业描述"}
    monkeypatch.setitem(sys.modules, "services.industry_templates", fake_industry)

    fake_akshare = types.ModuleType("akshare")
    fake_akshare.fund_rating_all = lambda: None
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    result = longterm_screen.screen_longterm_funds(force=False)

    assert result["funds"]
    assert result["generated_at"]
    assert (tmp_path / "_cache" / "longterm_funds.json").exists()



def test_longterm_analysis_page_exposes_force_refresh_error_copy_and_diagnostics():
    analysis_page = (BACKEND_DIR.parent / "pages" / "analysis.js").read_text(encoding="utf-8")

    assert "async function loadLtFunds(force=false)" in analysis_page
    assert "async function loadLtStocks(force=false)" in analysis_page
    assert "force=true" in analysis_page
    assert "长持基金加载失败" in analysis_page
    assert "长持股票加载失败" in analysis_page
    assert "跳过缓存强制拉取最新结果" in analysis_page
    assert "复制错误信息" in analysis_page
    assert "上报诊断" in analysis_page
    assert "window._prefetchLongtermFundDetails" in analysis_page
    assert "funds.slice(0,3)" in analysis_page



def test_longterm_fund_detail_reuses_shared_cache_when_user_not_holding(monkeypatch):
    import api.fund_detail as fund_detail_module

    base_payload = {"code": "013466", "name": "博时智选量化多因子股票C", "nav": 1.29}
    cache_hits = []

    def fake_get_cached(key, allow_stale=False):
        cache_hits.append((key, allow_stale))
        if key == "fund_detail_013466":
            return dict(base_payload)
        return None

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("shared cache hit should skip expensive detail rebuild")

    monkeypatch.setattr(fund_detail_module, "_get_cached", fake_get_cached)
    monkeypatch.setattr(fund_detail_module, "_set_cached", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fund_detail_module, "_enrich_detail_with_holding", lambda detail, *_args, **_kwargs: detail)

    fake_tushare = types.ModuleType("services.tushare_data")
    fake_tushare.get_fund_manager = should_not_run
    fake_tushare.get_fund_portfolio = should_not_run
    fake_tushare.get_fund_share = should_not_run
    monkeypatch.setitem(sys.modules, "services.tushare_data", fake_tushare)

    fake_rank = types.ModuleType("services.fund_rank")
    fake_rank.get_fund_dynamic_info = should_not_run
    monkeypatch.setitem(sys.modules, "services.fund_rank", fake_rank)

    result = fund_detail_module.fund_detail("013466", userId="LeiJiang")

    assert result == base_payload
    assert cache_hits[0] == ("fund_detail_013466_LeiJiang", True)
    assert ("fund_detail_013466", True) in cache_hits



def test_longterm_detail_modals_use_prefetch_and_async_detail_fetches():
    components = (BACKEND_DIR.parent / "pages" / "_components.js").read_text(encoding="utf-8")

    assert "showStockDetailModal = async function" in components
    assert "stockDetailBody" in components
    assert "API_BASE + '/stock-basic/'" in components
    assert "API_BASE + '/stock/financials/'" in components
    assert "Promise.allSettled" in components
    assert "window._prefetchFundDetail" in components
    assert "window._clearFundDetailPrefetchCache" in components
    assert "window.__fundDetailPrefetchCache" in components
    assert "window.__fundDetailInflightCache" in components



def test_fund_screen_weekend_stale_cache_survives_beyond_24_hours(tmp_path, monkeypatch):
    import threading
    from datetime import datetime as real_datetime
    import api.signals as signals

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cache_dir = tmp_path / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    now_ts = 1_000_000.0
    cache_fp = cache_dir / "fund_screen_all_score_LeiJiang.json"
    cache_fp.write_text(
        json.dumps(
            {
                "data": {"funds": [{"code": "000001"}], "query": "all"},
                "created_at": now_ts - 30 * 3600,
                "expires_at": now_ts - 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeWeekendDateTime:
        @classmethod
        def now(cls):
            return real_datetime(2026, 7, 5, 16, 0, 0)

    started = []

    class DummyThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.args, self.daemon))

    monkeypatch.setattr(signals, "datetime", FakeWeekendDateTime)
    monkeypatch.setattr(signals.time, "time", lambda: now_ts)
    monkeypatch.setattr(signals, "_get_market_timing_summary", lambda: {"signal": "latest"})
    monkeypatch.setattr(signals, "_bg_refresh_fund_screen", lambda *args, **kwargs: None)
    monkeypatch.setattr(threading, "Thread", DummyThread)

    def should_not_compute(*_args, **_kwargs):
        raise AssertionError("weekend stale cache should be returned before recompute")

    monkeypatch.setattr(signals, "_compute_fund_screen", should_not_compute)

    result = signals.get_fund_screen(fund_type="all", sort_by="score", top_n=30, userId="LeiJiang")

    assert result["from_cache"] is True
    assert result["stale"] is True
    assert result["market_timing"] == {"signal": "latest"}
    assert started == [(signals._bg_refresh_fund_screen, ("all", "score", 30, "LeiJiang"), True)]
