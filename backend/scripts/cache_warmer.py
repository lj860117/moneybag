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
    """保存缓存文件"""
    fp = CACHE_DIR / f"{name}.json"
    payload = {
        "data": data,
        "cached_at": datetime.now().isoformat(),
        "ttl_hours": ttl_hours,
        "expires_at": (datetime.now().timestamp() + ttl_hours * 3600),
    }
    fp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
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
        _save_cache("stock_screen_50", result, ttl)
    except Exception as e:
        print(f"  ❌ 选股失败: {e}")

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
                    _save_cache(f"fund_scan_{uid}", fscan, ttl)
                except Exception:
                    pass
    except Exception as e:
        print(f"  ❌ 用户持仓失败: {e}")
    
    print(f"[CACHE] 收盘预热完成 ✅")


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
    
    print(f"[CACHE] 早盘预热完成 ✅")

    # V7: 同步写入 precomputed 缓存
    _write_precomputed_fast()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🟡 午间预热（13:05 跑）
# 上午数据更新一次
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def warm_midday():
    """午间预热 — 上午数据刷新"""
    print(f"[CACHE] 午间预热 {datetime.now().strftime('%H:%M')}")
    
    if not _is_trading_day():
        print("[CACHE] 非交易日，跳过")
        return
    
    ttl = 3  # 13:05 → 15:30 = ~2.5h
    
    # 只刷新变化的：新闻 + 资金流
    print("  📰 午间新闻...")
    try:
        from services.news_data import get_market_news
        news = get_market_news(20)
        _save_cache("market_news", news, ttl)
    except Exception as e:
        print(f"  ❌ {e}")
    
    print("  💰 资金流...")
    try:
        from services.alt_data import get_alt_data_dashboard
        alt = get_alt_data_dashboard()
        _save_cache("alt_data", alt, ttl)
    except Exception as e:
        print(f"  ❌ {e}")
    
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
    print("  📊 precomputed 数据（恐贪/估值/北向）...")
    try:
        from services.precomputed_cache import save_precomputed
        from services.market_data import get_valuation_percentile, get_fear_greed_index
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
        print(f"  ✅ precomputed 完成（fgi={fgi.get('score') if fgi else 'N/A'}, val={val.get('percentile') if val else 'N/A'}）")
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

    # 1. 股票筛选（最慢 30-40 秒，周六上午 10:00 跑，不影响用户）
    print("  🔍 选股（stock_screen，需要 30-40s）...")
    try:
        from services.stock_screen import screen_stocks
        result = screen_stocks(50)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-close", action="store_true", help="收盘后预热")
    parser.add_argument("--harvest", action="store_true", help="收盘后数据收割（存 precomputed 供凌晨 night_worker）")
    parser.add_argument("--morning", action="store_true", help="早盘前预热")
    parser.add_argument("--midday", action="store_true", help="午间预热")
    parser.add_argument("--weekend", action="store_true", help="周末预热")
    parser.add_argument("--all", action="store_true", help="全部预热")
    args = parser.parse_args()

    if args.all:
        warm_after_close()
        warm_morning()
        warm_weekend()
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
    else:
        # 默认：收盘后预热
        warm_after_close()
