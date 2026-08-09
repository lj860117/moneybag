#!/usr/bin/env python3
"""
钱袋子 — 数据预缓存 cron 脚本
用法: 加入 crontab，在用户使用前跑好缓存
  # 收盘后预热（最重要，用户晚上打开秒出）
  35 15 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --after-close

  # 16:00 收盘后数据收割（把只有盘中/收盘后才有的数据存 precomputed，供凌晨 night_worker 使用）
  0 16 * * 1-5 cd /opt/moneybag/backend && set -a && . .env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --harvest

  # 早盘前预热（用户早上看之前跑好）
  15 9 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --morning

  # 午间预热（午休看一眼用）
  5 13 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --midday

  # 周末预热（低频数据刷新）
  0 10 * * 6 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --weekend

设计原则:
  - 收盘后的数据不会变 → 缓存到次日开盘
  - 日更数据每天拉2次就够 → 早盘+收盘
  - 周更数据周末拉一次 → 财报/分红/研报
  - 用户打开时直接读缓存 → 体验从 30-50s → 1-3s
"""
import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载 .env
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

CACHE_DIR = Path(os.environ.get("DATA_DIR",
    Path(__file__).parent.parent.parent / "data")) / "_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _save_cache(name: str, data, ttl_hours: float = 12):
    """保存缓存文件。

    v9.9.2: 使用临时文件 + os.replace 原子替换，避免旧缓存文件属主/权限异常时
    直接 write_text 覆盖失败（例如历史 root 属主文件导致 PermissionError）。
    只要目录可写，replace 就能完成覆盖。
    """
    import tempfile

    fp = CACHE_DIR / f"{name}.json"
    payload = {
        "data": data,
        "cached_at": datetime.now().isoformat(),
        "ttl_hours": ttl_hours,
        "expires_at": (datetime.now().timestamp() + ttl_hours * 3600),
    }
    serialized = json.dumps(payload, ensure_ascii=False, default=str)

    fd, tmp_path = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=str(CACHE_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, fp)
        try:
            fp.chmod(0o664)
        except Exception:
            pass
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    size = fp.stat().st_size / 1024
    print(f"  ✅ {name}: {size:.1f}KB (TTL={ttl_hours}h)")


def _is_trading_day():
    try:
        from services.signal_scout import is_trading_day
        return is_trading_day()
    except Exception:
        # 周一到周五
        return datetime.now().weekday() < 5


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔴 收盘后预热（最重要，35 15 跑）
# 收盘后数据冻结 → 缓存到次日9:30
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def warm_after_close():
    """收盘后预热 — 用户晚上/早上打开秒出"""
    print(f"[CACHE] 收盘后预热 {datetime.now().strftime('%H:%M')}")
    
    if not _is_trading_day():
        print("[CACHE] 非交易日，跳过")
        return
    
    ttl = 18  # 收盘15:35 → 次日9:30 = ~18小时
    
    # 1. 选股结果（最耗时 30-40秒 → 缓存后 0 秒）
    print("  📊 选股...")
    try:
        from services.stock_screen import screen_stocks
        result = screen_stocks(50)
        # v9.5.76: 写缓存前补行业字段
        if result.get("stocks"):
            try:
                import sys as _sys2, os as _os2
                _sys2.path.insert(0, _os2.path.join(_os2.path.dirname(__file__), ".."))
                from api.signals import _enrich_stock_labels, _load_industry_map
                _load_industry_map()
                _enrich_stock_labels(result["stocks"])
            except Exception:
                pass
        _save_cache("stock_screen_50", result, ttl)
    except Exception as e:
        print(f"  ❌ 选股失败: {e}")

    # 1.3. 选基结果（v9.5.123: 通过HTTP请求预热所有按钮组合 per-user）
    # 前端按钮: all/stock/bond/index/qdii × score/1y
    # 必须走HTTP(走API才能写per-user缓存文件,内部screen_funds缓存key不含userId)
    print("  🔍 选基(HTTP预热)...")
    try:
        import requests as _rq_fs
        _fund_types = ["all", "stock", "bond", "index", "qdii"]
        _sort_bys = ["score", "1y", "3y", "ytd"]  # 前端4种排序全覆盖
        _users = ["LeiJiang", "BuLuoGeLi"]
        _fs_ok = 0
        for uid in _users:
            for ft in _fund_types:
                for sb in _sort_bys:
                    try:
                        r = _rq_fs.get(f"http://127.0.0.1:8000/api/fund-screen?fund_type={ft}&sort_by={sb}&top_n=30&userId={uid}", timeout=60)
                        if r.ok:
                            cnt = len(r.json().get("funds", []))
                            _fs_ok += 1
                    except Exception:
                        pass
        print(f"  ✅ 选基预热完成: {_fs_ok}/{len(_users)*len(_fund_types)*len(_sort_bys)} 组合")
    except Exception as e:
        print(f"  ❌ 选基预热失败: {e}")

    # 1.5. 市场全景（1-2秒 → 缓存后 <50ms）
    print("  🌐 市场全景...")
    try:
        from services.market_panorama import generate_market_panorama
        panorama = generate_market_panorama()
        _save_cache("market_panorama", panorama, ttl)
        print("  ✅ market_panorama 完成")
    except Exception as e:
        print(f"  ❌ market_panorama: {e}")

    # 2. Dashboard 11源聚合（15-20秒 → 0秒）
    print("  📊 Dashboard...")
    try:
        # 直接调各数据源
        from services.data_layer import (
            get_fear_greed_index, get_valuation_percentile,
            get_technical_indicators,
        )
        from services.market_data import get_fund_nav
        
        dashboard = {}
        try:
            dashboard["fearGreed"] = get_fear_greed_index()
        except Exception as e:
            print(f"    [fear] {e}")
        try:
            v = get_valuation_percentile()
            dashboard["valuation"] = v
            print(f"    [val] PE={v.get('current_pe')}, pct={v.get('percentile')}%")
        except Exception as e:
            print(f"    [val] FAILED: {e}")
        try:
            dashboard["technical"] = get_technical_indicators()
        except Exception as e:
            print(f"    [tech] {e}")
        
        if dashboard:
            _save_cache("dashboard_core", dashboard, ttl)
    except Exception as e:
        print(f"  ❌ Dashboard失败: {e}")
    
    # 3. Regime 市场状态
    print("  📊 Regime...")
    try:
        from services.regime_engine import classify
        regime = classify(force=True)
        _save_cache("regime", regime, ttl)
    except Exception as e:
        print(f"  ❌ Regime失败: {e}")
    
    # 4. 每日信号
    print("  📊 每日信号...")
    try:
        from services.signal import generate_daily_signal
        signal = generate_daily_signal()
        _save_cache("daily_signal", signal, ttl)
    except Exception as e:
        print(f"  ❌ 信号失败: {e}")
    
    # 5. 信号侦察
    print("  📊 信号侦察...")
    try:
        from services.signal_scout import collect
        signals = collect()
        _save_cache("signal_scout", signals, ttl)
    except Exception as e:
        print(f"  ❌ 侦察失败: {e}")
    
    # 6. 全球市场
    print("  📊 全球市场...")
    try:
        from services.global_market import get_global_snapshot
        global_data = get_global_snapshot()
        _save_cache("global_snapshot", global_data, ttl)
    except Exception as e:
        print(f"  ❌ 全球失败: {e}")
    
    # 7. 另类数据
    print("  📊 另类数据...")
    try:
        from services.alt_data import get_alt_data_dashboard
        alt = get_alt_data_dashboard()
        _save_cache("alt_data", alt, ttl)
    except Exception as e:
        print(f"  ❌ 另类失败: {e}")
    
    # 8. 按用户预热持仓分析
    print("  📊 用户持仓...")
    try:
        profiles_file = CACHE_DIR.parent / "profiles.json"
        if profiles_file.exists():
            profiles = json.loads(profiles_file.read_text(encoding="utf-8"))
            for p in profiles:
                uid = p["id"]
                name = p.get("name", uid)
                print(f"    用户 {name}...")
                try:
                    from services.stock_monitor import scan_all_holdings
                    scan = scan_all_holdings(uid)
                    _save_cache(f"stock_scan_{uid}", scan, ttl)
                except Exception:
                    pass
                try:
                    from services.fund_monitor import scan_all_fund_holdings
                    fscan = scan_all_fund_holdings(uid)
                    # 质量检查：至少有一只基金有有效净值才缓存
                    holdings = fscan.get("holdings", [])
                    valid_count = sum(
                        1 for h in holdings
                        if (h.get("realtime") or {}).get("nav") is not None
                        or (h.get("realtime") or {}).get("estNav") is not None
                    )
                    if valid_count > 0:
                        _save_cache(f"fund_scan_{uid}", fscan, ttl)
                        print(f"    ✅ 基金扫描缓存: {valid_count}/{len(holdings)} 只有净值数据")
                    else:
                        print(f"    ⚠️ 基金净值全为空（可能尚未更新），跳过缓存写入，下次实时拉取")
                except Exception as e:
                    print(f"    ❌ 基金扫描失败: {e}")
    except Exception as e:
        print(f"  ❌ 用户持仓失败: {e}")
    
    # v9.5.81: 收盘后预热 PE 历史百分位（进程内缓存在重启后会清空）
    print("  📊 PE历史百分位预热（after-close）...")
    try:
        import json as _json
        from pathlib import Path as _P
        _cache_fp = _P(os.environ.get("DATA_DIR", "data")) / "_cache" / "stock_screen_50.json"
        if _cache_fp.exists():
            _stocks = _json.loads(_cache_fp.read_text()).get("data", {}).get("stocks", [])
            if _stocks:
                import sys as _sys2
                _sys2.path.insert(0, str(Path(__file__).parent.parent))
                from api.signals import _get_pe_percentile
                hit2 = sum(1 for _s in _stocks[:20]  # after-close 只预热前20只（最常访问的）
                           if _get_pe_percentile(_s.get("code",""), _s.get("pe"), _s.get("pb")))
                print(f"  ✅ PE历史预热(top20): {hit2}只完成")
    except Exception as e:
        print(f"  ⚠️ PE历史预热失败: {e}")

    # v9.5.77: 收盘后也刷新 daily_focus（之前只有早盘9:15刷，收盘后AI日报有8h过期风险）
    print("  📌 daily_focus 刷新...")
    try:
        from services.ds_enhance import generate_daily_focus
        focus = generate_daily_focus("")
        _save_cache("daily_focus", focus, 8)
        print("  ✅ daily_focus 刷新完成")
    except Exception as e:
        print(f"  ⚠️ daily_focus 刷新失败: {e}")

    # v9.8.2: 高优先级新增预热（收盘后）
    print("  🔴 家庭持仓汇总...")
    warm_family_portfolio()
    print("  🔴 持仓盈亏...")
    warm_portfolio_pnl()

    print(f"[CACHE] 收盘预热完成 ✅")


# v9.5.100: 全面缓存预热补全
# - 基金详情（持仓+选基榜TOP30）
# - 个股催化（业绩预告/快报/回购）
# - 长持评分

def _warm_fund_details(codes: list, label: str = ""):
    """预热通用基金详情（避免用户首次点击等 24s）"""
    if not codes:
        return
    print(f"[CACHE] 预热 {label} 基金详情 {len(codes)} 只...")
    import requests as _rq
    success = 0
    for code in codes:
        try:
            # 直接走本地接口（带文件缓存）
            r = _rq.get(f"http://127.0.0.1:8000/api/fund/detail/{code}", timeout=30)
            if r.status_code == 200:
                success += 1
        except Exception as e:
            print(f"  ⚠️ {code}: {str(e)[:40]}")
    print(f"  ✅ {label}: {success}/{len(codes)} 成功")


def _warm_user_scoped_fund_details(uid: str, codes: list, label: str = ""):
    """预热 userId 版基金详情 + 图表，避免真正前端请求时再走冷启动。"""
    if not uid or not codes:
        return
    import requests as _rq
    ok_detail = 0
    ok_chart = 0
    ok_nav = 0
    for code in sorted(set(codes)):
        try:
            r = _rq.get(f"http://127.0.0.1:8000/api/fund/detail/{code}?userId={uid}", timeout=30)
            if r.status_code == 200:
                ok_detail += 1
        except Exception:
            pass
        try:
            r = _rq.get(f"http://127.0.0.1:8000/api/chart/{code}?period=1y&userId={uid}", timeout=30)
            if r.status_code == 200:
                ok_chart += 1
        except Exception:
            pass
        try:
            r = _rq.get(f"http://127.0.0.1:8000/api/fund/nav-history/{code}?days=90", timeout=20)
            if r.status_code == 200:
                ok_nav += 1
        except Exception:
            pass
    print(f"  ✅ {label}{uid}: 详情{ok_detail}/{len(set(codes))} · 图表{ok_chart}/{len(set(codes))} · 净值{ok_nav}/{len(set(codes))}")


def _warm_longterm_fund_details_for_user(uid: str, funds_payload: dict | None, label: str = ""):
    """从长持基金榜提取代码，预热共享基金详情，避免点击非持仓基金时走冷启动慢路径。"""
    if not uid or not isinstance(funds_payload, dict):
        return
    funds = funds_payload.get("funds") or []
    codes = [f.get("code") for f in funds[:12] if isinstance(f, dict) and f.get("code")]
    if not codes:
        return
    _warm_fund_details(codes, f"{label}{uid} 长持基金详情")


def _warm_stock_charts(codes: list, uid: str = "LeiJiang", label: str = ""):
    """预热股票图表接口，保证选股页 K 线首开可用。"""
    if not codes:
        return
    import requests as _rq
    ok_chart = 0
    for code in sorted(set(codes)):
        try:
            r = _rq.get(f"http://127.0.0.1:8000/api/chart/{code}?period=1y&userId={uid}", timeout=30)
            if r.status_code == 200:
                ok_chart += 1
        except Exception:
            pass
    print(f"  ✅ {label}股票图表 {ok_chart}/{len(set(codes))}")


def _warm_stock_catalysts(codes: list, label: str = ""):
    """预热个股催化数据（业绩预告/快报/回购/股东户数）"""
    if not codes:
        return
    print(f"[CACHE] 预热 {label} 个股催化 {len(codes)} 只...")
    try:
        from services.tushare_data import (
            get_earning_forecast, get_express_report,
            get_share_repurchase, get_holder_number, get_top_inst
        )
        cnt = 0
        for code in codes:
            try:
                get_earning_forecast(code=code)
                get_express_report(code=code)
                get_share_repurchase(code=code, days=180)
                get_holder_number(code)
                get_top_inst(code=code)  # v9.5.101: 龙虎榜机构席位
                cnt += 1
            except Exception:
                pass
        print(f"  ✅ {label}: {cnt}/{len(codes)} 完成")
    except Exception as e:
        print(f"  ⚠️ catalyst 预热失败: {e}")


def _collect_all_user_holdings():
    """收集所有用户持仓（基金 + 股票）"""
    fund_codes = set()
    stock_codes = set()
    try:
        from services.fund_monitor import load_fund_holdings
        from services.stock_monitor import load_stock_holdings
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            try:
                for f in (load_fund_holdings(uid) or []):
                    if f.get("code"):
                        fund_codes.add(f["code"])
                for s in (load_stock_holdings(uid) or []):
                    if s.get("code"):
                        stock_codes.add(s["code"])
            except Exception:
                pass
    except Exception:
        pass
    return list(fund_codes), list(stock_codes)


def warm_full_extra():
    """v9.5.100 全量补充预热 — 详情/催化/长持
    
    收盘后追加跑（约 5-10 分钟）
    """
    print(f"[CACHE] 全量补充预热 {datetime.now().strftime('%H:%M')}")
    fund_codes, stock_codes = _collect_all_user_holdings()
    print(f"  持仓基金 {len(fund_codes)} 只，持仓股票 {len(stock_codes)} 只")

    # 1. 持仓基金详情
    _warm_fund_details(fund_codes, "持仓")

    # 2. 选基榜 TOP30 详情
    try:
        from services.fund_screen import screen_funds
        top_funds = screen_funds(fund_type="all", sort_by="score", top_n=30)
        top_codes = [f["code"] for f in (top_funds.get("funds") or []) if f.get("code")]
        _warm_fund_details(top_codes, "选基榜TOP30")
    except Exception as e:
        print(f"  ⚠️ 选基榜预热失败: {e}")

    # 3. 持仓个股催化数据
    _warm_stock_catalysts(stock_codes, "持仓股")

    # 4. 选股榜 TOP30 催化
    top_stock_codes = []
    try:
        from services.stock_screen import screen_stocks
        top_stocks = screen_stocks(top_n=30)
        top_stock_codes = [s["code"] for s in (top_stocks.get("stocks") or [])[:30] if s.get("code")]
        _warm_stock_catalysts(top_stock_codes, "选股榜TOP30")
        _warm_stock_charts(top_stock_codes, uid="LeiJiang", label="选股榜TOP30")
    except Exception as e:
        print(f"  ⚠️ 选股催化预热失败: {e}")

    # 5. v9.5.120: per-user 选基/潜力/选股 缓存预热（后端全权缓存，前端零缓存）
    try:
        import requests as _rq
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            # 选基（per-user per-sort，含 holding_relation + nav_percentile）
            for sort in ["score", "1y", "3y", "ytd"]:
                try:
                    _rq.get(f"http://127.0.0.1:8000/api/fund-screen?fund_type=all&sort_by={sort}&top_n=30&userId={uid}", timeout=120)
                    print(f"  ✅ 选基 {sort} {uid}")
                except Exception:
                    print(f"  ⚠️ 选基 {sort} {uid} 超时")
            # 潜力榜
            try:
                _rq.get(f"http://127.0.0.1:8000/api/fund-potential?userId={uid}&limit=30", timeout=60)
                print(f"  ✅ 潜力榜 {uid}")
            except Exception:
                print(f"  ⚠️ 潜力榜 {uid} 超时")
            # v9.5.121: 持仓诊断（评分/百分位/行业/潜力建议）
            try:
                _rq.get(f"http://127.0.0.1:8000/api/fund-holdings/enrich?userId={uid}", timeout=30)
                print(f"  ✅ 持仓诊断 {uid}")
            except Exception:
                print(f"  ⚠️ 持仓诊断 {uid} 超时")
            # v9.5.121: AI 深度体检（Pro 级 LLM 分析，24h缓存）
            try:
                _rq.get(f"http://127.0.0.1:8000/api/fund-holdings/ai-checkup?userId={uid}", timeout=60)
                print(f"  ✅ AI体检 {uid}")
            except Exception:
                print(f"  ⚠️ AI体检 {uid} 超时")
            # 选股（per-user）
            try:
                _rq.get(f"http://127.0.0.1:8000/api/stock-screen?top_n=50&userId={uid}", timeout=120)
                print(f"  ✅ 选股 {uid}")
            except Exception:
                print(f"  ⚠️ 选股 {uid} 超时")
    except Exception as e:
        print(f"  ⚠️ per-user 预热失败: {e}")

    # 6. 长持评分缓存
    try:
        import requests as _rq
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            for kind in ["funds", "stocks"]:
                try:
                    r = _rq.get(f"http://127.0.0.1:8000/api/longterm/{kind}?userId={uid}", timeout=180)
                    print(f"  ✅ 长持 {kind} {uid}")
                    if kind == "funds" and r.status_code == 200:
                        try:
                            _warm_longterm_fund_details_for_user(uid, r.json(), label="全量预热 ")
                        except Exception as warm_err:
                            print(f"  ⚠️ 长持基金详情预热 {uid} 失败: {warm_err}")
                except Exception:
                    pass
    except Exception as e:
        print(f"  ⚠️ 长持预热失败: {e}")

    # 7. v9.5.121: 基金详情预热（持仓基金 + 榜单 top 基金）
    try:
        import requests as _rq
        # 收集需要预热的基金代码
        detail_codes = set()
        # 持仓基金
        from services.fund_monitor import load_fund_holdings
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            for f in (load_fund_holdings(uid) or []):
                if f.get("code"):
                    detail_codes.add(f["code"])
        # 综合榜 top 基金
        try:
            r = _rq.get("http://127.0.0.1:8000/api/fund-screen?fund_type=all&sort_by=score&top_n=30&userId=LeiJiang", timeout=10)
            if r.ok:
                for f in (r.json().get("funds") or [])[:15]:
                    if f.get("code"):
                        detail_codes.add(f["code"])
        except Exception:
            pass
        print(f"  [详情预热] 共 {len(detail_codes)} 只基金")
        ok_count = 0
        for code in sorted(detail_codes):
            try:
                _rq.get(f"http://127.0.0.1:8000/api/fund/detail/{code}", timeout=20)
                ok_count += 1
            except Exception:
                pass
        print(f"  ✅ 基金详情预热 {ok_count}/{len(detail_codes)}")
    except Exception as e:
        print(f"  ⚠️ 基金详情预热失败: {e}")

    # 8. 宏观数据预热（PMI/CPI/沪深300估值）
    try:
        from services.tushare_data import get_macro_pmi, get_macro_cpi, get_index_dailybasic
        get_macro_pmi(months=2)
        get_macro_cpi(months=2)
        for idx in ["000300.SH", "000016.SH", "399006.SZ", "000852.SH"]:
            get_index_dailybasic(ts_code=idx, days=5)
        print(f"  ✅ 宏观数据预热完成")
    except Exception as e:
        print(f"  ⚠️ 宏观预热失败: {e}")

    print(f"[CACHE] 全量补充预热完成 ✅")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🟡 早盘预热（9:15 跑）
# 开盘前刷新隔夜变化的数据
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def warm_morning():
    """早盘预热 — 开盘前刷新隔夜数据"""
    print(f"[CACHE] 早盘预热 {datetime.now().strftime('%H:%M')}")
    
    if not _is_trading_day():
        print("[CACHE] 非交易日，跳过")
        return
    
    ttl = 4  # 9:15 → 13:00 = ~4小时
    
    # 1. 新闻（隔夜有新的）
    print("  📰 新闻...")
    try:
        from services.news_data import get_market_news
        news = get_market_news(20)
        _save_cache("market_news", news, ttl)
    except Exception as e:
        print(f"  ❌ 新闻失败: {e}")
    
    # 2. 政策新闻
    print("  🏛️ 政策...")
    try:
        from services.policy_data import get_all_policy_topics
        policy = get_all_policy_topics()
        _save_cache("policy_topics", policy, ttl)
    except Exception as e:
        print(f"  ❌ 政策失败: {e}")
    
    # 3. 全球市场（隔夜美股已收盘）
    print("  🌐 全球...")
    try:
        from services.global_market import get_global_snapshot
        global_data = get_global_snapshot()
        _save_cache("global_snapshot", global_data, ttl)
    except Exception as e:
        print(f"  ❌ 全球失败: {e}")
    
    # 4. Regime（开盘前重新判断）
    print("  📊 Regime...")
    try:
        from services.regime_engine import classify
        regime = classify(force=True)
        _save_cache("regime", regime, 8)
    except Exception as e:
        print(f"  ❌ Regime失败: {e}")
    
    # 5. 今日关注
    print("  🎯 今日关注...")
    try:
        from services.ds_enhance import generate_daily_focus
        # 需要 market_ctx
        focus = generate_daily_focus("")
        _save_cache("daily_focus", focus, 8)
    except Exception as e:
        print(f"  ❌ 关注失败: {e}")

    # 6. 用户持仓扫描（开盘前预热，早上打开秒开）
    print("  💼 用户持仓扫描...")
    try:
        profiles = []
        import json as _json
        _profiles_f = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent.parent / "data")) / "profiles.json"
        if _profiles_f.exists():
            profiles = _json.loads(_profiles_f.read_text(encoding="utf-8"))
        for p in profiles:
            uid = p.get("id", "")
            if uid and uid != "Guest":
                try:
                    from services.fund_monitor import scan_all_fund_holdings
                    fscan = scan_all_fund_holdings(uid)
                    # 质量检查
                    f_valid = sum(1 for h in fscan.get("holdings",[])
                                  if (h.get("realtime") or {}).get("nav") is not None
                                  or (h.get("realtime") or {}).get("estNav") is not None)
                    if f_valid > 0:
                        _save_cache(f"fund_scan_{uid}", fscan, ttl)
                    else:
                        print(f"    ⚠️ {uid} 基金净值全为空，跳过缓存")
                    from services.stock_monitor import scan_all_holdings
                    sscan = scan_all_holdings(uid)
                    _save_cache(f"stock_scan_{uid}", sscan, ttl)
                    print(f"    ✅ {uid}: 基金{len(fscan.get('holdings',[]))}只({f_valid}有净值) 股票{len(sscan.get('holdings',[]))}只")
                except Exception as ue:
                    print(f"    ❌ {uid}: {ue}")
    except Exception as e:
        print(f"  ❌ 持仓扫描失败: {e}")

    # 6.5 v9.5.123: 预加载fund_basic(经理稳定性标签需要,17624只约5s)
    # 在选基之前跑,确保选基时经理标签已就绪
    print("  🧬 fund_basic预加载(经理稳定性)...")
    try:
        import requests as _rq_fb
        # 通过一次fund-screen请求触发后台线程,然后等待完成
        _rq_fb.get("http://127.0.0.1:8000/api/fund-screen?fund_type=all&sort_by=score&top_n=1&userId=LeiJiang", timeout=30)
        import time as _tw
        _tw.sleep(15)  # 等后台线程拉完fund_basic(约10-15s)
        print("  ✅ fund_basic应已加载")
    except Exception as e:
        print(f"  ⚠️ fund_basic预加载: {e}")

    # 7. v9.5.123: per-user 选基预热(核心6组,不全量跑避免开盘前20min跑不完)
    # 其他组合走"首次请求实时计算+写缓存"
    print("  🎯 per-user 选基核心预热(6组)...")
    try:
        import requests as _rq
        _morning_combos = [
            ("all", "score"), ("all", "1y"),
            ("stock", "score"), ("index", "score"),
            ("bond", "score"), ("qdii", "score"),
        ]
        _morning_ok = 0
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            for ft, sb in _morning_combos:
                try:
                    _rq.get(f"http://127.0.0.1:8000/api/fund-screen?fund_type={ft}&sort_by={sb}&top_n=30&userId={uid}", timeout=60)
                    _morning_ok += 1
                except Exception:
                    pass
        print(f"  ✅ 选基预热: {_morning_ok}/{len(_morning_combos)*2} 组合")
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            try:
                _rq.get(f"http://127.0.0.1:8000/api/fund-potential?userId={uid}&limit=30", timeout=60)
                print(f"    ✅ 潜力 {uid}")
            except Exception:
                print(f"    ⚠️ 潜力 {uid} 超时")
            try:
                _rq.get(f"http://127.0.0.1:8000/api/stock-screen?top_n=50&userId={uid}", timeout=120)
                print(f"    ✅ 选股 {uid}")
            except Exception:
                print(f"    ⚠️ 选股 {uid} 超时")
            # v9.5.123/v9.9.3: 长持也在 morning 预热，并顺手预热长持基金详情
            for kind in ["funds", "stocks"]:
                try:
                    r = _rq.get(f"http://127.0.0.1:8000/api/longterm/{kind}?userId={uid}", timeout=180)
                    print(f"    ✅ 长持{kind} {uid}")
                    if kind == "funds" and r.status_code == 200:
                        _warm_longterm_fund_details_for_user(uid, r.json(), label="早盘预热 ")
                except Exception:
                    print(f"    ⚠️ 长持{kind} {uid} 超时")
    except Exception as e:
        print(f"  ❌ 选基预热失败: {e}")

    # 8. v9.5.123: TOP基金详情预热(夏普/Sortino/Alpha首次计算慢)
    # 预热选基TOP10的详情,用户点进去秒开
    print("  📊 基金详情预热(TOP10)...")
    try:
        import requests as _rq_det
        # 读选基缓存获取TOP代码
        import json as _j_det, glob as _g_det
        cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
        fs_files = _g_det.glob(str(cache_dir / "fund_screen_all_score_*.json"))
        top_codes = []
        for fp in sorted(fs_files, key=os.path.getmtime, reverse=True)[:1]:
            data = _j_det.loads(open(fp, encoding="utf-8").read())
            inner = data.get("data", data)
            for f in inner.get("funds", [])[:10]:
                code = f.get("code", "")
                if code:
                    top_codes.append(code)
        _det_ok = 0
        for code in top_codes[:10]:
            try:
                _rq_det.get(f"http://127.0.0.1:8000/api/fund/detail/{code}", timeout=30)
                _det_ok += 1
            except Exception:
                pass
        print(f"  ✅ 详情预热: {_det_ok}/{len(top_codes[:10])} 只")
    except Exception as e:
        print(f"  ⚠️ 详情预热: {e}")

    # 8.5 v9.5.124: 多模型AI评分预热(TOP5,缓存12h,每天只跑1次)
    print("  🤖 多模型AI评分预热(TOP5)...")
    try:
        import requests as _rq_ai
        _ai_ok = 0
        for code in top_codes[:5]:
            try:
                _rq_ai.get(f"http://127.0.0.1:8000/api/fund/ai-score/{code}", timeout=40)
                _ai_ok += 1
            except Exception:
                pass
        print(f"  ✅ AI评分预热: {_ai_ok}/5 只")
    except Exception as e:
        print(f"  ⚠️ AI评分预热: {e}")

    # 9. 持仓诊断 + CFO摘要
    try:
        import requests as _rq_misc
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            try:
                _rq_misc.get(f"http://127.0.0.1:8000/api/fund-holdings/enrich?userId={uid}", timeout=30)
                print(f"    ✅ 持仓诊断 {uid}")
            except Exception:
                pass
            # v9.5.122/v9.9.1: 首页 cfo-summary（force=1，确保定时预热真的刷新文件缓存时间戳）
            try:
                _rq_misc.get(f"http://127.0.0.1:8000/api/cfo-summary?userId={uid}&force=1", timeout=20)
                print(f"    ✅ 首页CFO {uid}")
            except Exception:
                pass
        # v9.5.122: AI 对话上下文预热（市场上下文+各用户持仓上下文 → 写文件缓存）
        try:
            from api.shared_helpers import _build_market_context, _build_portfolio_context
            _build_market_context()
            print(f"    ✅ AI市场上下文预热")
            for uid2 in ["LeiJiang", "BuLuoGeLi"]:
                _build_portfolio_context(user_id=uid2)
            print(f"    ✅ AI持仓上下文预热(2用户)")
        except Exception as e:
            print(f"    ⚠️ AI上下文预热失败: {e}")

        # v9.9.2: 当前前端已统一走 /api/fund/detail/{code}?userId=uid + /api/chart/{code}
        # 这里必须预热真实用户态详情与图表，不能再预热旧的 /fund-holdings/detail 分叉入口。
        try:
            from services.fund_monitor import load_fund_holdings
            my_funds = load_fund_holdings(uid) or []
            from services.persistence import load_user
            user = load_user(uid)
            txn_codes = set(t.get("code", "") for t in ((user.get("portfolio") or {}).get("transactions") or []) if t.get("code"))
            all_codes = set(f.get("code", "") for f in my_funds if f.get("code")) | txn_codes
            _warm_user_scoped_fund_details(uid, [fc for fc in all_codes if fc and len(fc) == 6], label="持仓详情预热 ")
        except Exception as e:
            print(f"    ⚠️ 持仓详情预热失败 {uid}: {e}")
    except Exception as e:
        print(f"  ⚠️ per-user 预热失败: {e}")

    # v9.5.122: 预制问题答案预计算
    print("  🧠 预制问题预计算...")
    _warm_preset_answers()

    # v9.8.2: 高优先级新增预热
    print("  🔴 家庭持仓汇总...")
    warm_family_portfolio()
    print("  🔴 持仓盈亏...")
    warm_portfolio_pnl()
    print("  🟡 风控指标...")
    warm_risk_metrics()

    print(f"[CACHE] 早盘预热完成 ✅")

    # V7: 同步写入 precomputed 缓存
    _write_precomputed_fast()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🟡 午间预热（13:05 跑）
# 上午数据更新一次
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def warm_nav_confirmed():
    """v9.5.123: 净值确认后全量预热(20:30触发)
    
    基金净值通常18:00-20:00陆续披露。20:30后用确认净值重算排行,
    确保次日早上打开看到的是最终确认数据(不是估算)。
    """
    print(f"[CACHE] 净值确认预热 {datetime.now().strftime('%H:%M')}")
    
    if not _is_trading_day():
        print("[CACHE] 非交易日，跳过")
        return
    
    # 清除旧的选基缓存(强制用确认净值重算)
    import glob
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
    for f in glob.glob(str(cache_dir / "fund_screen_*.json")):
        try:
            os.remove(f)
        except Exception:
            pass
    
    # 全量预热选基(6核心组合×2用户=12组)
    try:
        import requests as _rq
        _combos = [
            ("all", "score"), ("all", "1y"),
            ("stock", "score"), ("index", "score"),
            ("bond", "score"), ("qdii", "score"),
        ]
        ok = 0
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            for ft, sb in _combos:
                try:
                    _rq.get(f"http://127.0.0.1:8000/api/fund-screen?fund_type={ft}&sort_by={sb}&top_n=30&userId={uid}", timeout=60)
                    ok += 1
                except Exception:
                    pass
        print(f"  ✅ 选基(确认净值): {ok}/{len(_combos)*2} 组合")
    except Exception as e:
        print(f"  ❌ 选基预热失败: {e}")
    
    # 也刷新长持(确认净值后评分更准)
    try:
        import requests as _rq
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            for kind in ["funds", "stocks"]:
                try:
                    r = _rq.get(f"http://127.0.0.1:8000/api/longterm/{kind}?userId={uid}", timeout=180)
                    if kind == "funds" and r.status_code == 200:
                        _warm_longterm_fund_details_for_user(uid, r.json(), label="净值确认预热 ")
                except Exception:
                    pass
        print("  ✅ 长持预热完成")
    except Exception as e:
        print(f"  ❌ 长持: {e}")
    
    # v9.8.2: 基金历史收益率预热（净值确认后）
    print("  📊 基金历史收益率...")
    warm_fund_history_returns()
    
    print(f"[CACHE] 净值确认预热完成 ✅")


def warm_midday():
    """午间预热 — 盘中数据刷新(每30min)"""
    print(f"[CACHE] 午间预热 {datetime.now().strftime('%H:%M')}")
    
    if not _is_trading_day():
        print("[CACHE] 非交易日，跳过")
        return
    
    ttl = 1  # 30min后下一轮会刷新
    
    # 1. 新闻
    print("  📰 午间新闻...")
    try:
        from services.news_data import get_market_news
        news = get_market_news(20)
        _save_cache("market_news", news, ttl)
    except Exception as e:
        print(f"  ❌ {e}")
    
    # 2. 资金流
    print("  💰 资金流...")
    try:
        from services.alt_data import get_alt_data_dashboard
        alt = get_alt_data_dashboard()
        _save_cache("alt_data", alt, ttl)
    except Exception as e:
        print(f"  ❌ {e}")
    
    # 3. v9.5.123: 全市场估值表刷新(供/api/fund-estimate-batch异步API用)
    # 一次HTTP拉全市场估算,前端异步调用时直接读内存/缓存秒回
    print("  📊 全市场估值表...")
    try:
        from services.fund_monitor import _load_estimation_all
        df = _load_estimation_all()
        if df is not None and not df.empty:
            print(f"  ✅ 估值表: {len(df)}只基金")
        else:
            print("  ⚠️ 估值表为空(可能盘前/盘后)")
    except Exception as e:
        print(f"  ❌ 估值表: {e}")
    
    print(f"[CACHE] 午间预热完成 ✅")

    # V7: 同步写入 precomputed 缓存（白天 API 优先读这个）
    _write_precomputed_fast()


def _write_precomputed_fast():
    """快速刷新 precomputed 缓存（给首页秒看用）"""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from services.precomputed_cache import save_precomputed

        # 恐贪指数
        try:
            from services.market_data import get_fear_greed_index
            save_precomputed("fear_greed", get_fear_greed_index())
        except Exception:
            pass

        # 估值百分位
        try:
            from services.market_data import get_valuation_percentile
            save_precomputed("valuation", get_valuation_percentile())
        except Exception:
            pass

        # 北向+融资+SHIBOR
        try:
            from services.factor_data import get_northbound_flow, get_shibor, get_margin_trading
            save_precomputed("factors", {
                "northbound": get_northbound_flow(),
                "shibor": get_shibor(),
                "margin": get_margin_trading(),
            })
        except Exception:
            pass

        # 行业轮动
        try:
            from services.sector_rotation import get_sector_ranking
            sr = get_sector_ranking()
            if sr.get("available"):
                save_precomputed("sector_rotation", sr)
        except Exception:
            pass

        # 研报共识
        try:
            from services.broker_research import get_broker_consensus
            br = get_broker_consensus()
            if br.get("available"):
                save_precomputed("broker_consensus", br)
        except Exception:
            pass

        # 13 维信号
        try:
            from services.signal import calculate_daily_signal
            from services.signal import generate_daily_signal
            signal = generate_daily_signal()
            save_precomputed("daily_signal", signal)
        except Exception:
            pass

        # P2.1: 新增 4 项预计算（扩展凌晨覆盖范围）

        # 全球市场快照
        try:
            from services.global_market import get_global_snapshot
            gs = get_global_snapshot()
            if gs:
                save_precomputed("global_snapshot", gs)
        except Exception:
            pass

        # 新闻情绪打分
        try:
            from services.data_layer import get_news_sentiment_score
            sentiment = get_news_sentiment_score()
            if sentiment.get("available"):
                save_precomputed("news_sentiment", sentiment)
        except Exception:
            pass

        # 大宗商品价格
        try:
            from services.market_factors import get_commodity_impact_assessment
            comm = get_commodity_impact_assessment()
            if comm:
                save_precomputed("commodities", comm)
        except Exception:
            pass

        # 宏观数据（CPI/PMI/M2）
        try:
            from services.macro_data import get_macro_calendar
            macro = get_macro_calendar()
            if macro:
                save_precomputed("macro", {"events": macro})
        except Exception:
            pass

        print(f"  ★ precomputed 缓存已刷新（含P2.1扩展4项）")
    except Exception as e:
        print(f"  precomputed 刷新失败: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🟢 周末预热（周六 10:00 跑）
# 低频数据一周刷一次
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def warm_weekend():
    """周末预热 — 低频数据刷新"""
    print(f"[CACHE] 周末预热 {datetime.now().strftime('%H:%M')}")
    
    ttl = 72  # 周六 → 周一 = ~48h，留余量72h
    
    # 0. precomputed 数据（dashboard 用，工作日盘中更新，周末必须手动生成）
    print("  📊 precomputed 数据（恐贪/估值/北向/技术指标）...")
    try:
        from services.precomputed_cache import save_precomputed
        from services.market_data import get_valuation_percentile, get_fear_greed_index
        from services.technical import get_technical_indicators
        from services.factor_data import get_northbound_flow, get_margin_trading
        fgi = get_fear_greed_index()
        if fgi:
            save_precomputed("fear_greed", fgi)
        val = get_valuation_percentile()
        if val:
            save_precomputed("valuation", val)
        nb = get_northbound_flow() or {}
        margin = get_margin_trading() or {}
        save_precomputed("factors", {"northbound": nb, "margin": margin})
        # ★ 新增 technical 指标（RSI/MACD/布林线）
        tech = get_technical_indicators()
        if tech:
            save_precomputed("technical", tech)
        print(f"  ✅ precomputed 完成（fgi={fgi.get('score') if fgi else 'N/A'}, val={val.get('percentile') if val else 'N/A'}, rsi={tech.get('rsi') if tech else 'N/A'}）")
    except Exception as e:
        print(f"  ❌ precomputed: {e}")

    # 0.5. market-panorama 文件缓存
    print("  🌐 市场全景 market-panorama...")
    try:
        from services.market_panorama import generate_market_panorama
        panorama = generate_market_panorama()
        _save_cache("market_panorama", panorama, ttl)
        print("  ✅ market_panorama 完成")
    except Exception as e:
        print(f"  ❌ market_panorama: {e}")

    # 0.5. v9.5.81: PE 历史百分位预热（选股50只，首次拉需 ~50s，预热后秒读）
    print("  📊 PE历史百分位预热（选股50只Tushare日线数据）...")
    try:
        import json as _json
        from pathlib import Path as _P
        _cache_fp = _P(os.environ.get("DATA_DIR", "data")) / "_cache" / "stock_screen_50.json"
        if _cache_fp.exists():
            _stocks = _json.loads(_cache_fp.read_text()).get("data", {}).get("stocks", [])
            if _stocks:
                # 避免 import 在顶层（warm_weekend 里 signals 还没初始化）
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).parent.parent))
                from api.signals import _get_pe_percentile, _load_industry_map
                _load_industry_map()
                hit, miss = 0, 0
                for _s in _stocks:
                    try:
                        _r = _get_pe_percentile(_s.get("code", ""), _s.get("pe"), _s.get("pb"))
                        if _r:
                            hit += 1
                        else:
                            miss += 1
                    except Exception:
                        miss += 1
                print(f"  ✅ PE历史预热: {hit}只成功, {miss}只失败")
    except Exception as e:
        print(f"  ⚠️ PE历史预热失败: {e}")

    # 1. 股票筛选（最慢 30-40 秒，周六上午 10:00 跑，不影响用户）
    print("  🔍 选股（stock_screen，需要 30-40s）...")
    try:
        from services.stock_screen import screen_stocks
        result = screen_stocks(50)
        # v9.5.76: 写缓存前先补行业字段，否则文件缓存里 industry 永远是空
        if result.get("stocks"):
            try:
                import sys, os
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
                from api.signals import _enrich_stock_labels, _load_industry_map
                _load_industry_map()  # 预热行业映射
                _enrich_stock_labels(result["stocks"])
                print(f"  ✅ 行业补全: {sum(1 for s in result['stocks'] if s.get('industry'))} 只有行业")
            except Exception as e2:
                print(f"  ⚠️ 行业补全失败（不影响写缓存）: {e2}")
        _save_cache("stock_screen_50", result, ttl)
        print(f"  ✅ stock_screen 完成，{len(result.get('stocks', []))} 只")
    except Exception as e:
        print(f"  ❌ stock_screen: {e}")

    # 2. 宏观数据（月更，但每周检查一次）
    print("  🏛️ 宏观...")
    try:
        from services.macro_data import get_macro_calendar
        macro = get_macro_calendar()
        _save_cache("macro", macro, ttl)
    except Exception as e:
        print(f"  ❌ {e}")
    
    # 2. 基金筛选（周更够了）
    print("  🔍 基金筛选...")
    try:
        from services.fund_screen import screen_funds
        for ftype in ["all", "stock", "bond", "index"]:
            result = screen_funds(ftype, "score", 20)
            _save_cache(f"fund_screen_{ftype}", result, ttl)
    except Exception as e:
        print(f"  ❌ {e}")
    
    # 3. 因子IC（周更，计算量大）
    print("  🔬 因子IC...")
    try:
        from services.factor_ic import compute_factor_ic
        ic = compute_factor_ic(forward_days=20, pool_size=200)
        _save_cache("factor_ic", ic, ttl)
    except Exception as e:
        print(f"  ❌ {e}")
    
    # 4. 清理过期缓存
    print("  🧹 清理过期缓存...")
    now = time.time()
    cleaned = 0
    for fp in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if data.get("expires_at", 0) < now:
                fp.unlink()
                cleaned += 1
        except Exception:
            pass
    if cleaned:
        print(f"  🗑️ 清理 {cleaned} 个过期文件")

    # v9.5.77: 清理 precomputed 旧文件（保留最近7天，删除更旧的）
    print("  🧹 清理 precomputed 旧文件...")
    try:
        from datetime import date, timedelta
        precomputed_dir = DATA_DIR.parent / "data" / "precomputed"
        if not precomputed_dir.exists():
            precomputed_dir = Path("/opt/moneybag/data/precomputed")
        if precomputed_dir.exists():
            cutoff = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
            old_files = []
            for fp in precomputed_dir.glob("*_2026-*.json"):
                # 文件名格式: {type}_YYYY-MM-DD.json
                parts = fp.stem.rsplit("_", 3)  # 取最后 3 段 YYYY-MM-DD
                if len(parts) >= 4:
                    file_date = "-".join(parts[-3:])  # YYYY-MM-DD
                else:
                    file_date = fp.stem.split("_")[-1] if "_" in fp.stem else ""
                # 兼容 YYYY-MM-DD 和 YYYYMMDD 两种格式
                if len(file_date) == 10 and file_date < cutoff:
                    old_files.append(fp)
            for fp in old_files:
                try:
                    fp.unlink()
                except Exception:
                    pass
            if old_files:
                print(f"  🗑️ precomputed 清理 {len(old_files)} 个旧文件")
            else:
                print("  ✅ precomputed 无需清理")
    except Exception as e:
        print(f"  ⚠️ precomputed 清理失败: {e}")

    # v9.5.77: 每周逐只预热用户持仓基金的详细指标（避免长持页面首次打开30s等待）
    print("  📦 用户持仓基金逐只预热...")
    try:
        import hashlib
        from services.fund_monitor import get_fund_nav_history, load_fund_holdings
        from services.tushare_data import is_configured as ts_ok

        profiles_file = DATA_DIR.parent / "data" / "profiles.json"
        if not profiles_file.exists():
            profiles_file = Path("/opt/moneybag/data/profiles.json")

        all_codes: set = set()
        if profiles_file.exists():
            profs = json.loads(profiles_file.read_text(encoding="utf-8"))
            for p in profs:
                uid = p.get("id", "")
                fh = load_fund_holdings(uid) or []
                for f in fh:
                    code = f.get("code", "")
                    if code and code.isdigit():
                        all_codes.add(code)

        if all_codes:
            print(f"  📦 逐只预热 {len(all_codes)} 只持仓基金 nav_history...")
            hit = 0
            for code in sorted(all_codes):
                try:
                    navs = get_fund_nav_history(code, days=60)  # 触发缓存写入
                    if navs:
                        hit += 1
                except Exception:
                    pass
            print(f"  ✅ 持仓基金 nav_history 预热: {hit}/{len(all_codes)}")
        else:
            print("  ℹ️ 无持仓基金需预热")
    except Exception as e:
        print(f"  ⚠️ 持仓基金预热失败（不影响主流程）: {e}")

    # v9.5.122: 周末预热 fund_rt 文件缓存（避免周末打开 App 逐只 fundgz fallback 24秒等待）
    print("  🔄 周末 fund_rt 文件缓存预热...")
    try:
        import requests as _rq
        _rt_hit = 0
        for code in sorted(all_codes) if 'all_codes' in dir() else []:
            try:
                _rq.get(f"http://127.0.0.1:8000/api/fund-holdings/realtime/{code}", timeout=5)
                _rt_hit += 1
            except Exception:
                pass
        # 也为活跃用户的 V4 交易中的基金预热
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            try:
                from services.persistence import load_user
                user = load_user(uid)
                for t in ((user.get("portfolio") or {}).get("transactions") or []):
                    c = t.get("code", "")
                    if c and len(c)==6 and c.isdigit() and c not in (all_codes if 'all_codes' in dir() else set()):
                        try:
                            _rq.get(f"http://127.0.0.1:8000/api/fund-holdings/realtime/{c}", timeout=5)
                            _rt_hit += 1
                        except Exception:
                            pass
            except Exception:
                pass
        print(f"  ✅ fund_rt 预热 {_rt_hit} 只")
    except Exception as e:
        print(f"  ⚠️ fund_rt 预热失败: {e}")

    # 月初触发长期筛选预热（每月1-3日的周末才跑，避免每周重复）
    today_day = datetime.now().day
    if today_day <= 7:  # 每月前7天（通常含月初第一个周末）
        print("  📈 月初触发长期持有筛选预热...")
        try:
            from services.longterm_screen import screen_longterm_funds, screen_longterm_stocks
            print("    💼 长期基金筛选...")
            fund_result = screen_longterm_funds(force=False)  # 有缓存不重算
            print(f"    ✅ 长期基金筛选完成：{fund_result.get('total_screened',0)} 只")
        except Exception as e:
            print(f"    ❌ 长期基金筛选: {e}")
        try:
            # 股票筛选90天缓存，只在季初跑
            if datetime.now().month in (1, 4, 7, 10):  # 季初月份
                print("    📊 长期股票筛选（季初触发）...")
                stock_result = screen_longterm_stocks(force=False)
                print(f"    ✅ 长期股票筛选完成：{stock_result.get('total_screened',0)} 只")
        except Exception as e:
            print(f"    ❌ 长期股票筛选: {e}")

    print(f"[CACHE] 周末预热完成 ✅")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def warm_harvest():
    """收盘后数据收割 — 把只有盘中/收盘后才有的数据存到 precomputed_cache

    凌晨 night_worker 直接读 precomputed，不再试图实时抓（凌晨很多数据源没数据）。
    运行时间：16:00（收盘后30分钟，确保数据已更新）

    收割清单：
    1. 北向资金（Tushare, 收盘后才有今日数据）
    2. 融资融券（Tushare, T+1 结算后才有）
    3. SHIBOR（每天更新一次）
    4. 恐贪指数（需要当日收盘价算）
    5. 估值百分位（需要当日 PE）
    6. 行业轮动（AKShare, 盘中才能抓到实时资金流）
    7. 研报共识（Tushare）
    """
    from datetime import datetime
    print(f"[HARVEST] 📦 收盘后数据收割 {datetime.now().strftime('%H:%M')}")

    if not _is_trading_day():
        print("[HARVEST] 非交易日，跳过")
        return

    from services.precomputed_cache import save_precomputed
    harvested = []

    # 1. 因子三件套：北向 + SHIBOR + 融资融券
    print("  📊 因子数据...")
    try:
        from services.factor_data import get_northbound_flow, get_shibor, get_margin_trading
        factors = {
            "northbound": get_northbound_flow(),
            "shibor": get_shibor(),
            "margin": get_margin_trading(),
        }
        save_precomputed("factors", factors)
        harvested.append("因子")
        north = factors.get("northbound", {})
        print(f"    北向: {north.get('net_flow_today', '?')}亿, "
              f"SHIBOR: {factors.get('shibor', {}).get('overnight', '?')}%")
    except Exception as e:
        print(f"  ❌ 因子: {e}")

    # 2. 恐贪指数（需要当日收盘价）
    print("  📊 恐贪指数...")
    try:
        from services.market_data import get_fear_greed_index
        fgi = get_fear_greed_index()
        if fgi.get("score", 50) != 50 or fgi.get("dimensions"):
            save_precomputed("fear_greed", fgi)
            harvested.append(f"恐贪({fgi['score']})")
        else:
            print("    ⚠️ 恐贪=50（可能计算失败），不覆盖缓存")
    except Exception as e:
        print(f"  ❌ 恐贪: {e}")

    # 3. 估值百分位（需要当日 PE）
    print("  📊 估值百分位...")
    try:
        from services.market_data import get_valuation_percentile
        val = get_valuation_percentile()
        if val.get("percentile", 50) != 50 or val.get("current_pe"):
            save_precomputed("valuation", val)
            harvested.append(f"估值({val['percentile']}%)")
        else:
            print("    ⚠️ 估值=50%（可能默认值），不覆盖缓存")
    except Exception as e:
        print(f"  ❌ 估值: {e}")

    # 4. 行业轮动（AKShare 盘中/收盘后抓板块资金流）
    print("  📊 行业轮动...")
    try:
        from services.sector_rotation import get_sector_ranking
        sr = get_sector_ranking()
        if sr.get("available"):
            save_precomputed("sector_rotation", sr)
            top = sr.get("top_gainers", [{}])[:3]
            names = [s.get("name", "?") for s in top]
            harvested.append(f"轮动({','.join(names)})")
        else:
            print(f"    ⚠️ 行业轮动不可用: {sr.get('error', '')}")
    except Exception as e:
        print(f"  ❌ 轮动: {e}")

    # 5. 研报共识
    print("  📊 研报共识...")
    try:
        from services.broker_research import get_broker_consensus
        br = get_broker_consensus()
        if br.get("available"):
            save_precomputed("broker_consensus", br)
            harvested.append(f"研报({br.get('consensus', '?')})")
    except Exception as e:
        print(f"  ❌ 研报: {e}")

    # 6. 13 维每日信号
    print("  📊 每日信号...")
    try:
        from services.signal import generate_daily_signal
        signal = generate_daily_signal()
        save_precomputed("daily_signal", signal)
        harvested.append("信号")
    except Exception as e:
        print(f"  ❌ 信号: {e}")

    print(f"\n[HARVEST] ✅ 收割完成: {', '.join(harvested)} ({len(harvested)} 项)")
    print(f"[HARVEST] 凌晨 night_worker 将直接读取这些 precomputed 数据")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔴 高优先级新增预热（v9.8.2）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def warm_family_portfolio():
    """预热家庭持仓汇总（per-user）
    
    前端 Hero 金额同步依赖这个接口，必须预热！
    预热时机：morning + after-close
    """
    print(f"[CACHE] 家庭持仓汇总预热 {datetime.now().strftime('%H:%M')}")
    
    try:
        import requests as _rq
        users = ["LeiJiang", "BuLuoGeLi"]
        ok = 0
        for uid in users:
            try:
                r = _rq.get(f"http://127.0.0.1:8000/api/family/portfolio-summary?userId={uid}", timeout=30)
                if r.ok:
                    data = r.json()
                    # 验证数据有效性
                    members = data.get("members", [])
                    if members:
                        ok += 1
                        print(f"    ✅ {uid}: {len(members)} 成员")
            except Exception as e:
                print(f"    ⚠️ {uid}: {str(e)[:40]}")
        
        print(f"  ✅ 家庭持仓汇总: {ok}/{len(users)} 用户")
    except Exception as e:
        print(f"  ❌ 家庭持仓汇总预热失败: {e}")


def warm_portfolio_pnl():
    """预热持仓盈亏计算（per-user）
    
    持仓页核心数据，首次打开需 3-8s，预热后秒出。
    预热时机：morning + after-close
    """
    print(f"[CACHE] 持仓盈亏预热 {datetime.now().strftime('%H:%M')}")
    
    try:
        from services.persistence import load_user
        import requests as _rq
        
        users = ["LeiJiang", "BuLuoGeLi"]
        ok = 0
        
        for uid in users:
            try:
                user = load_user(uid)
                holdings = (user.get("portfolio") or {}).get("holdings") or []
                
                if not holdings:
                    print(f"    ℹ️ {uid}: 无持仓")
                    continue
                
                # 构造 PnL 请求
                r = _rq.post(
                    "http://127.0.0.1:8000/api/portfolio/pnl",
                    json={"holdings": holdings, "userId": uid},
                    timeout=30
                )
                
                if r.ok:
                    data = r.json()
                    ok += 1
                    total_market = data.get("totalMarket", 0)
                    total_pnl = data.get("totalPnl", 0)
                    print(f"    ✅ {uid}: 市值 ¥{total_market}, 盈亏 ¥{total_pnl}")
                    
            except Exception as e:
                print(f"    ⚠️ {uid}: {str(e)[:40]}")
        
        print(f"  ✅ 持仓盈亏: {ok}/{len(users)} 用户")
    except Exception as e:
        print(f"  ❌ 持仓盈亏预热失败: {e}")


def warm_fund_history_returns():
    """预热基金历史收益率（持仓基金）
    
    基金详情页历史收益率，首次加载需 5-10s（Tushare 调用），预热后秒出。
    预热时机：morning + nav-confirmed
    """
    print(f"[CACHE] 基金历史收益率预热 {datetime.now().strftime('%H:%M')}")
    
    try:
        from services.fund_monitor import load_fund_holdings
        import requests as _rq
        
        # 收集所有持仓基金
        all_codes = set()
        for uid in ["LeiJiang", "BuLuoGeLi"]:
            holdings = load_fund_holdings(uid) or []
            for f in holdings:
                code = f.get("code", "")
                if code and len(code) == 6:
                    all_codes.add(code)
        
        if not all_codes:
            print("  ℹ️ 无持仓基金")
            return
        
        print(f"  持仓基金 {len(all_codes)} 只")
        
        # 逐只预热（走API，触发后端缓存写入）
        ok = 0
        for code in sorted(all_codes):
            try:
                r = _rq.get(f"http://127.0.0.1:8000/api/fund/info/{code}", timeout=15)
                if r.ok:
                    ok += 1
            except Exception:
                pass
        
        print(f"  ✅ 基金历史收益率: {ok}/{len(all_codes)} 只")
    except Exception as e:
        print(f"  ❌ 基金历史收益率预热失败: {e}")


def warm_risk_metrics():
    """预热风控指标（per-user）
    
    风控页面核心数据，包含 VaR、最大回撤、夏普比率等。
    预热时机：morning
    """
    print(f"[CACHE] 风控指标预热 {datetime.now().strftime('%H:%M')}")
    
    try:
        from services.persistence import load_user
        import requests as _rq
        
        users = ["LeiJiang", "BuLuoGeLi"]
        ok = 0
        
        for uid in users:
            try:
                user = load_user(uid)
                holdings = (user.get("portfolio") or {}).get("holdings") or []
                
                if not holdings:
                    print(f"    ℹ️ {uid}: 无持仓")
                    continue
                
                # 构造风控指标请求（假设有这个 API）
                # 如果没有独立 API，可以在 portfolio_pnl 返回中包含风控指标
                r = _rq.post(
                    "http://127.0.0.1:8000/api/portfolio/risk-metrics",
                    json={"holdings": holdings, "userId": uid},
                    timeout=30
                )
                
                if r.ok:
                    ok += 1
                    print(f"    ✅ {uid}: 风控指标已缓存")
                else:
                    print(f"    ⚠️ {uid}: API 返回 {r.status_code}")
                    
            except Exception as e:
                print(f"    ⚠️ {uid}: {str(e)[:40]}")
        
        print(f"  ✅ 风控指标: {ok}/{len(users)} 用户")
    except Exception as e:
        print(f"  ❌ 风控指标预热失败: {e}")


def warm_evening():
    """晚间预热模式 — 覆盖用户下班后打开 App 的场景
    
    当前 cron 在 20:30 (nav-confirmed) 后没有预热，
    用户 18:00-22:00 打开 App 可能看到早上预热的数据（已经 8h+）。
    
    预热内容：
    - 全球市场（隔夜美股开盘）
    - 新闻（盘后可能有新消息）
    - 基金实时估值（如果还在盘中）
    - CFO 摘要（首页健康检查依赖文件缓存时间戳）
    """
    print(f"[CACHE] 晚间预热 {datetime.now().strftime('%H:%M')}")
    
    if not _is_trading_day():
        print("[CACHE] 非交易日，跳过")
        return
    
    ttl = 12  # 18:00 → 次日 6:00 = ~12小时
    active_users = []
    
    # 1. 全球市场（美股开盘后数据更新）
    print("  🌐 全球市场...")
    try:
        from services.global_market import get_global_snapshot
        global_data = get_global_snapshot()
        _save_cache("global_snapshot", global_data, ttl)
        print("  ✅ 全球市场完成")
    except Exception as e:
        print(f"  ❌ 全球市场: {e}")
    
    # 2. 新闻（盘后可能有新消息）
    print("  📰 新闻...")
    try:
        from services.news_data import get_market_news
        news = get_market_news(20)
        _save_cache("market_news", news, ttl)
        print("  ✅ 新闻完成")
    except Exception as e:
        print(f"  ❌ 新闻: {e}")
    
    # 3. 政策新闻
    print("  🏛️ 政策...")
    try:
        from services.policy_data import get_all_policy_topics
        policy = get_all_policy_topics()
        _save_cache("policy_topics", policy, ttl)
        print("  ✅ 政策完成")
    except Exception as e:
        print(f"  ❌ 政策: {e}")
    
    # 4. 用户持仓 scan（用最新净值更新）
    print("  💼 用户持仓扫描...")
    try:
        profiles = []
        import json as _json
        _profiles_f = Path(os.environ.get("DATA_DIR", 
                              Path(__file__).parent.parent.parent / "data")) / "profiles.json"
        if _profiles_f.exists():
            profiles = _json.loads(_profiles_f.read_text(encoding="utf-8"))
        active_users = [p.get("id", "") for p in profiles if p.get("id") and p.get("id") != "Guest"]
        for uid in active_users:
            try:
                from services.fund_monitor import scan_all_fund_holdings
                fscan = scan_all_fund_holdings(uid)
                _save_cache(f"fund_scan_{uid}", fscan, ttl)
                
                from services.stock_monitor import scan_all_holdings
                sscan = scan_all_holdings(uid)
                _save_cache(f"stock_scan_{uid}", sscan, ttl)
                
                print(f"    ✅ {uid}: 持仓扫描完成")
            except Exception as ue:
                print(f"    ❌ {uid}: {ue}")
    except Exception as e:
        print(f"  ❌ 持仓扫描失败: {e}")

    # 5. 首页 CFO 摘要（force=1，避免命中文件缓存导致 mtime 不更新）
    print("  🧾 首页CFO...")
    try:
        import requests as _rq_cfo
        if not active_users:
            active_users = ["LeiJiang", "BuLuoGeLi"]
        for uid in active_users:
            try:
                _rq_cfo.get(f"http://127.0.0.1:8000/api/cfo-summary?userId={uid}&force=1", timeout=20)
                print(f"    ✅ 首页CFO {uid}")
            except Exception as ue:
                print(f"    ⚠️ 首页CFO {uid}: {ue}")
    except Exception as e:
        print(f"  ❌ 首页CFO预热失败: {e}")
    
    print(f"[CACHE] 晚间预热完成 ✅")


def _warm_preset_answers():
    """v9.5.122: 为每个用户预计算6个快捷问题的回答（前端点击秒回，不调 LLM）"""
    from pathlib import Path
    import time as _time
    
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "preset_answers"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    PRESETS = [
        {"key": "preset_diagnosis", "question": "诊断我的持仓，有没有问题？"},
        {"key": "preset_valuation", "question": "现在估值高吗？适合入场吗？"},
        {"key": "preset_rebalance", "question": "我需要再平衡吗？"},
        {"key": "preset_overlap", "question": "我的持仓有没有重叠，能精简吗？"},
        {"key": "preset_risk", "question": "当前最大风险是什么？"},
    ]
    
    try:
        from api.shared_helpers import _build_market_context, _build_portfolio_context, _build_system_prompt
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()
        market_ctx = _build_market_context()
    except Exception as e:
        print(f"    ⚠️ 预制问题初始化失败: {e}")
        return
    
    for uid in ["LeiJiang", "BuLuoGeLi"]:
        portfolio_ctx = _build_portfolio_context(user_id=uid)
        system = _build_system_prompt(market_ctx, portfolio_ctx)
        
        for preset in PRESETS:
            fp = cache_dir / f"{preset['key']}_{uid}.txt"
            # 跳过24h内已有的
            try:
                if fp.exists() and (_time.time() - fp.stat().st_mtime) < 86400:
                    continue
            except Exception:
                pass
            
            try:
                result = gw.call_sync(
                    preset["question"],
                    system=system,
                    model_tier="llm_light",
                    user_id=uid,
                    module="preset_warm",
                    max_tokens=800,
                )
                content = result.get("content", "")
                if content and len(content) > 20:
                    fp.write_text(content, encoding="utf-8")
                    print(f"    ✅ {preset['key']}_{uid}")
                else:
                    print(f"    ⚠️ {preset['key']}_{uid}: 回答太短")
            except Exception as e:
                print(f"    ⚠️ {preset['key']}_{uid}: {e}")
    
    print(f"    预制问题预计算完成")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-close", action="store_true", help="收盘后预热")
    parser.add_argument("--harvest", action="store_true", help="收盘后数据收割（存 precomputed 供凌晨 night_worker）")
    parser.add_argument("--morning", action="store_true", help="早盘前预热")
    parser.add_argument("--midday", action="store_true", help="午间预热")
    parser.add_argument("--weekend", action="store_true", help="周末预热")
    parser.add_argument("--full-extra", action="store_true", help="全量补充预热（详情/催化/长持/宏观）")
    parser.add_argument("--nav-confirmed", action="store_true", help="v9.5.123: 净值确认后全量预热(20:30)")
    parser.add_argument("--evening", action="store_true", help="v9.8.2: 晚间预热(18:00, 覆盖下班后场景)")
    parser.add_argument("--all", action="store_true", help="全部预热")
    # v9.8.2: 新增独立预热选项
    parser.add_argument("--family-portfolio", action="store_true", help="家庭持仓汇总预热")
    parser.add_argument("--portfolio-pnl", action="store_true", help="持仓盈亏预热")
    parser.add_argument("--fund-history", action="store_true", help="基金历史收益率预热")
    parser.add_argument("--risk-metrics", action="store_true", help="风控指标预热")
    args = parser.parse_args()
    
    if args.all:
        warm_after_close()
        warm_morning()
        warm_weekend()
        warm_full_extra()
    elif args.harvest:
        warm_harvest()
    elif args.after_close:
        warm_after_close()
    elif args.morning:
        warm_morning()
    elif args.midday:
        warm_midday()
    elif args.weekend:
        warm_weekend()
        warm_full_extra()  # v9.5.100 周末也补全预热
    elif args.nav_confirmed:
        warm_nav_confirmed()
    elif args.evening:
        warm_evening()
    elif args.family_portfolio:
        warm_family_portfolio()
    elif args.portfolio_pnl:
        warm_portfolio_pnl()
    elif args.fund_history:
        warm_fund_history_returns()
    elif args.risk_metrics:
        warm_risk_metrics()
    elif args.full_extra:
        warm_full_extra()
    else:
        # 默认：收盘后预热 + 全量补充
        warm_after_close()
        warm_full_extra()
