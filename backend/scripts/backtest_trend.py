"""
钱袋子 v9.5.123 — 走势预估 + 智能定投 回测验证
=================================================
Sprint 1: 用历史数据验证8维引擎的准确率

回测方法:
1. 拉取代表性基金2年净值数据(~500个交易日)
2. 在每个交易日t模拟8维评分(只用t及之前的数据)
3. 检查t+30天后的实际涨跌是否与预测方向一致
4. 统计: 偏多预测准确率 / 偏空准确率 / 综合准确率

回测标的:
- 110020 (易方达沪深300ETF联接) — 大盘代表
- 163406 (兴全合润混合) — 主动管理代表
- 012414 (华夏中证500) — 中盘代表
- 007994 (广发纳指100ETF联接) — 美股代表

使用: python3 scripts/backtest_trend.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import time
from datetime import datetime, timedelta
from pathlib import Path


# 回测参数
BACKTEST_FUNDS = [
    ("110020", "易方达沪深300ETF联接"),
    ("163406", "兴全合润混合"),
    ("012414", "华夏中证500"),
    ("007994", "广发纳指100ETF联接"),
    ("005827", "易方达蓝筹精选"),
    ("001156", "申万菱信新能源汽车"),
    ("003834", "华夏能源革新"),
    ("161725", "招商中证白酒"),
]
LOOKBACK_DAYS = 730  # 2年
FORWARD_DAYS = 30    # 预测30天后涨跌
MIN_NAV_POINTS = 300  # 至少需要300个净值点才能回测


def fetch_nav_history(code: str, days: int = LOOKBACK_DAYS) -> list:
    """从天天基金API拉取基金净值历史(无需Tushare token)"""
    import requests, re
    
    all_navs = []
    headers = {"Referer": "https://fund.eastmoney.com/", "User-Agent": "Mozilla/5.0"}
    # 每页20条,拉足够多页
    max_pages = days // 20 + 2
    
    for page in range(1, min(max_pages, 40)):  # 最多40页=800条
        try:
            url = "https://api.fund.eastmoney.com/f10/lsjz"
            r = requests.get(url, params={
                "callback": "x", "fundCode": code,
                "pageIndex": page, "pageSize": 20
            }, headers=headers, timeout=10)
            body = re.sub(r"^x\(", "", r.text.strip()).rstrip(")")
            import json as _j
            items = _j.loads(body).get("Data", {}).get("LSJZList", [])
            if not items:
                break
            for item in items:
                nav = item.get("DWJZ")  # 用单位净值(反映真实价格波动,不含分红)
                date = item.get("FSRQ", "")
                if nav and date:
                    try:
                        all_navs.append({"date": date.replace("-", ""), "nav": float(nav)})
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            print(f"  ⚠️ page {page} failed: {e}")
            break
        time.sleep(0.3)  # 限流
    
    all_navs.sort(key=lambda x: x["date"])
    print(f"  {'✅' if len(all_navs) >= 100 else '⚠️'} {code} 获取 {len(all_navs)} 个净值点")
    return all_navs


def simulate_trend_score(navs: list, idx: int) -> dict:
    """在第idx天模拟8维评分(只用idx及之前的数据)
    
    简化版模拟(不依赖外部API):
    - 动量: 用过去60日(~3月)涨跌
    - 技术面: 用MACD简化判断
    - 估值: 用过去300日的百分位
    - 其他维度用简化规则
    """
    if idx < 120:  # 至少需要120天历史
        return {"direction": "flat", "score": 0}
    
    cur_nav = navs[idx]["nav"]
    
    # 动量: 过去60日涨跌
    nav_60d_ago = navs[idx - 60]["nav"]
    r3m = (cur_nav - nav_60d_ago) / nav_60d_ago * 100 if nav_60d_ago > 0 else 0
    
    # 6月动量
    nav_120d_ago = navs[idx - 120]["nav"] if idx >= 120 else navs[0]["nav"]
    r6m = (cur_nav - nav_120d_ago) / nav_120d_ago * 100 if nav_120d_ago > 0 else 0
    
    # 估值百分位(过去250日)
    lookback = min(idx, 250)
    hist_navs = [navs[i]["nav"] for i in range(idx - lookback, idx + 1)]
    nav_pct = sum(1 for v in hist_navs if v <= cur_nav) / len(hist_navs) * 100
    
    # === 模拟8维评分 ===
    score = 0
    
    # 1. 动量趋势 (±25)
    d1 = 0
    if r3m > 15: d1 = 18
    elif r3m > 5: d1 = 10
    elif r3m < -10: d1 = -18
    elif r3m < -5: d1 = -8
    if r6m > 25: d1 += 5
    elif r6m < -15: d1 -= 5
    d1 = max(-25, min(25, d1))
    score += d1
    
    # 2. 技术面 (±20) — 简化MACD
    d2 = 0
    if r3m > 10 and nav_pct < 70:
        d2 = 12
    elif r3m > 5 and nav_pct < 50:
        d2 = 8
    elif r3m < -5 and nav_pct > 70:
        d2 = -12
    elif r3m < -10:
        d2 = -8
    score += d2
    
    # 3. 估值水位 (±15)
    d3 = 0
    if nav_pct >= 90: d3 = -15
    elif nav_pct >= 75: d3 = -8
    elif nav_pct <= 15: d3 = 15
    elif nav_pct <= 30: d3 = 10
    elif nav_pct <= 50: d3 = 4
    score += d3
    
    # 4. 资金面代理 (±15) — 用最近10日动量做代理
    nav_10d_ago = navs[idx - 10]["nav"] if idx >= 10 else navs[0]["nav"]
    r10d = (cur_nav - nav_10d_ago) / nav_10d_ago * 100 if nav_10d_ago > 0 else 0
    d4 = 0
    if r10d > 3 and r3m > 5: d4 = 8
    elif r10d < -3 and r3m < -5: d4 = -8
    score += d4
    
    # 5. 市场环境 (±10) — 用6月趋势做代理
    d5 = 0
    if r6m > 10: d5 = 8
    elif r6m < -10: d5 = -8
    score += d5
    
    # 6-8. 简化为固定小分
    # (真实环境有赛道/波动/情绪, 回测中用固定值)
    
    score = max(-100, min(100, score))
    
    # v9.5.123 优化: 阈值从±20降到±12，让系统更频繁给出方向判断
    THRESHOLD = 12
    if score >= THRESHOLD:
        direction = "up"
    elif score <= -THRESHOLD:
        direction = "down"
    else:
        direction = "flat"
    
    return {"direction": direction, "score": score, "nav_pct": nav_pct, "r3m": r3m}


def run_backtest_one(code: str, name: str) -> dict:
    """对单只基金跑回测"""
    print(f"\n{'='*50}")
    print(f"📊 回测: {name} ({code})")
    print(f"{'='*50}")
    
    navs = fetch_nav_history(code, LOOKBACK_DAYS)
    if len(navs) < MIN_NAV_POINTS:
        print(f"  ⚠️ 数据不足({len(navs)}点 < {MIN_NAV_POINTS}), 跳过")
        return {"code": code, "name": name, "error": "数据不足"}
    
    # 从第150天开始回测(保证有足够lookback), 到倒数第FORWARD_DAYS天
    start_idx = 150
    end_idx = len(navs) - FORWARD_DAYS
    
    results = {"up_correct": 0, "up_total": 0, "down_correct": 0, "down_total": 0,
               "flat_total": 0, "total_predictions": 0}
    
    # 每5天采样一次(减少重叠)
    for idx in range(start_idx, end_idx, 5):
        pred = simulate_trend_score(navs, idx)
        direction = pred["direction"]
        
        # 计算实际30天后涨跌
        future_nav = navs[idx + FORWARD_DAYS]["nav"]
        current_nav = navs[idx]["nav"]
        actual_return = (future_nav - current_nav) / current_nav * 100
        
        results["total_predictions"] += 1
        
        if direction == "up":
            results["up_total"] += 1
            if actual_return > 0:
                results["up_correct"] += 1
        elif direction == "down":
            results["down_total"] += 1
            if actual_return < 0:
                results["down_correct"] += 1
        else:
            results["flat_total"] += 1
    
    # 计算准确率
    up_acc = round(results["up_correct"] / results["up_total"] * 100, 1) if results["up_total"] > 0 else 0
    down_acc = round(results["down_correct"] / results["down_total"] * 100, 1) if results["down_total"] > 0 else 0
    total_correct = results["up_correct"] + results["down_correct"]
    total_directional = results["up_total"] + results["down_total"]
    overall_acc = round(total_correct / total_directional * 100, 1) if total_directional > 0 else 0
    
    print(f"\n📈 结果:")
    print(f"  偏多预测: {results['up_total']}次, 正确{results['up_correct']}次, 准确率 {up_acc}%")
    print(f"  偏空预测: {results['down_total']}次, 正确{results['down_correct']}次, 准确率 {down_acc}%")
    print(f"  震荡预测: {results['flat_total']}次 (不计入准确率)")
    print(f"  综合方向准确率: {overall_acc}% ({total_correct}/{total_directional})")
    
    return {
        "code": code, "name": name,
        "up_accuracy": up_acc, "down_accuracy": down_acc,
        "overall_accuracy": overall_acc,
        "up_predictions": results["up_total"],
        "down_predictions": results["down_total"],
        "flat_predictions": results["flat_total"],
        "total_samples": results["total_predictions"],
        "backtest_days": len(navs),
        "forward_days": FORWARD_DAYS,
    }


def run_dca_backtest(code: str, name: str, navs: list) -> dict:
    """定投策略回测: 双因子 vs 固定金额"""
    if len(navs) < MIN_NAV_POINTS:
        return {"error": "数据不足"}
    
    BASE_AMOUNT = 3000  # 每月定投基准金额
    
    # 模拟每月定投(每20个交易日投一次)
    fixed_shares = 0
    fixed_cost = 0
    smart_shares = 0
    smart_cost = 0
    
    for idx in range(150, len(navs), 20):  # 每20天=约每月
        nav = navs[idx]["nav"]
        if nav <= 0:
            continue
        
        # 固定定投
        fixed_shares += BASE_AMOUNT / nav
        fixed_cost += BASE_AMOUNT
        
        # 双因子定投
        pred = simulate_trend_score(navs, idx)
        direction = pred["direction"]
        nav_pct = pred.get("nav_pct", 50)
        
        # 简化版双因子倍率
        if direction == "up":
            if nav_pct < 30: mult = 2.0
            elif nav_pct < 70: mult = 1.3
            else: mult = 0.8
        elif direction == "down":
            if nav_pct < 30: mult = 0.8
            elif nav_pct < 70: mult = 0.5
            else: mult = 0.2
        else:
            if nav_pct < 30: mult = 1.5
            elif nav_pct < 70: mult = 1.0
            else: mult = 0.7
        
        smart_amount = BASE_AMOUNT * mult
        smart_shares += smart_amount / nav
        smart_cost += smart_amount
    
    # 计算最终收益
    final_nav = navs[-1]["nav"]
    fixed_value = fixed_shares * final_nav
    smart_value = smart_shares * final_nav
    
    fixed_return = round((fixed_value - fixed_cost) / fixed_cost * 100, 2) if fixed_cost > 0 else 0
    smart_return = round((smart_value - smart_cost) / smart_cost * 100, 2) if smart_cost > 0 else 0
    excess = round(smart_return - fixed_return, 2)
    
    print(f"\n💰 定投回测: {name}")
    print(f"  固定定投: 投入¥{fixed_cost:.0f} → 市值¥{fixed_value:.0f}, 收益率 {fixed_return}%")
    print(f"  双因子:   投入¥{smart_cost:.0f} → 市值¥{smart_value:.0f}, 收益率 {smart_return}%")
    print(f"  超额收益: {'+' if excess > 0 else ''}{excess}%")
    
    return {
        "code": code, "name": name,
        "fixed_cost": round(fixed_cost), "fixed_value": round(fixed_value),
        "fixed_return": fixed_return,
        "smart_cost": round(smart_cost), "smart_value": round(smart_value),
        "smart_return": smart_return,
        "excess_return": excess,
        "months": (len(navs) - 150) // 20,
    }


def main():
    print("=" * 60)
    print("🔬 钱袋子 v9.5.123 — 走势预估+定投 历史回测")
    print(f"   回测周期: {LOOKBACK_DAYS}天 | 预测窗口: {FORWARD_DAYS}天")
    print(f"   标的: {len(BACKTEST_FUNDS)}只代表性基金")
    print("=" * 60)
    
    trend_results = []
    dca_results = []
    
    for code, name in BACKTEST_FUNDS:
        # 走势准确率回测
        r = run_backtest_one(code, name)
        trend_results.append(r)
        
        # 定投回测(复用净值数据)
        navs = fetch_nav_history(code, LOOKBACK_DAYS)
        if navs and len(navs) >= MIN_NAV_POINTS:
            dr = run_dca_backtest(code, name, navs)
            dca_results.append(dr)
        
        time.sleep(1)  # Tushare 限流
    
    # 汇总
    print("\n" + "=" * 60)
    print("📋 汇总结果")
    print("=" * 60)
    
    valid_trend = [r for r in trend_results if "error" not in r]
    if valid_trend:
        avg_up = round(sum(r["up_accuracy"] for r in valid_trend) / len(valid_trend), 1)
        avg_down = round(sum(r["down_accuracy"] for r in valid_trend) / len(valid_trend), 1)
        avg_overall = round(sum(r["overall_accuracy"] for r in valid_trend) / len(valid_trend), 1)
        print(f"\n🎯 走势预估平均准确率:")
        print(f"   偏多预测: {avg_up}%")
        print(f"   偏空预测: {avg_down}%")
        print(f"   综合方向: {avg_overall}%")
    
    valid_dca = [r for r in dca_results if "error" not in r]
    if valid_dca:
        avg_excess = round(sum(r["excess_return"] for r in valid_dca) / len(valid_dca), 2)
        print(f"\n💰 双因子定投 vs 固定定投:")
        print(f"   平均超额收益: {'+' if avg_excess > 0 else ''}{avg_excess}%")
        for r in valid_dca:
            print(f"   {r['name']}: 固定{r['fixed_return']}% → 双因子{r['smart_return']}% (超额{'+' if r['excess_return']>0 else ''}{r['excess_return']}%)")
    
    # 保存结果到缓存文件(供前端读取)
    output = {
        "backtest_date": datetime.now().strftime("%Y-%m-%d"),
        "trend_accuracy": {
            "avg_up": avg_up if valid_trend else 0,
            "avg_down": avg_down if valid_trend else 0,
            "avg_overall": avg_overall if valid_trend else 0,
            "details": valid_trend,
        },
        "dca_comparison": {
            "avg_excess": avg_excess if valid_dca else 0,
            "details": valid_dca,
        },
        "params": {
            "lookback_days": LOOKBACK_DAYS,
            "forward_days": FORWARD_DAYS,
            "funds_tested": len(BACKTEST_FUNDS),
        },
    }
    
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = cache_dir / "backtest_results.json"
    fp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 结果已保存: {fp}")
    
    return output


if __name__ == "__main__":
    main()
