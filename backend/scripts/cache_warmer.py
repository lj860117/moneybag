#!/usr/bin/env python3
"""
钱袋子 — 数据预缓存 cron 脚本
用法: 加入 crontab，在用户使用前跑好缓存
  # 收盘后预热（最重要，用户晚上打开秒出）
  35 15 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --after-close
  
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
        except Exception:
            pass
        try:
            dashboard["valuation"] = get_valuation_percentile()
        except Exception:
            pass
        try:
            dashboard["technical"] = get_technical_indicators()
        except Exception:
            pass
        
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🟢 周末预热（周六 10:00 跑）
# 低频数据一周刷一次
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def warm_weekend():
    """周末预热 — 低频数据刷新"""
    print(f"[CACHE] 周末预热 {datetime.now().strftime('%H:%M')}")
    
    ttl = 72  # 周六 → 周一 = ~48h，留余量72h
    
    # 1. 宏观数据（月更，但每周检查一次）
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-close", action="store_true", help="收盘后预热")
    parser.add_argument("--morning", action="store_true", help="早盘前预热")
    parser.add_argument("--midday", action="store_true", help="午间预热")
    parser.add_argument("--weekend", action="store_true", help="周末预热")
    parser.add_argument("--all", action="store_true", help="全部预热")
    args = parser.parse_args()
    
    if args.all:
        warm_after_close()
        warm_morning()
        warm_weekend()
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
