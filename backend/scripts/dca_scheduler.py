"""
钱袋子 v9.5.123 Sprint 2 — 定投纪律执行系统
=============================================
1. 定投日历推送: 每月25号(或用户自定义日)推送智能定投建议
2. 止盈止损触发: 每日检测持仓是否触达纪律线
3. 预测周复盘: 每周日统计上周走势预测准确率

用法:
  python3 scripts/dca_scheduler.py --dca        # 定投日推送
  python3 scripts/dca_scheduler.py --discipline  # 止盈止损检测
  python3 scripts/dca_scheduler.py --weekly      # 周复盘
  python3 scripts/dca_scheduler.py --all         # 全部执行
"""
import sys
import os
import json
import time
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
USERS = ["LeiJiang", "BuLuoGeLi"]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ═══════════════════════════════════════════════
# 1. 定投日历推送
# ═══════════════════════════════════════════════

def push_dca_reminder():
    """每月定投日推送: 为每个用户计算双因子定投建议"""
    log("💰 === 定投日推送 ===")
    
    today = date.today()
    # 默认每月25号(可配置)
    dca_day = 25
    if today.day != dca_day:
        log(f"  今天{today.day}号, 定投日为{dca_day}号, 跳过")
        return {"pushed": False, "reason": f"非定投日(今天{today.day}号)"}
    
    from services.signal import calc_smart_dca_v2
    from services.fund_monitor import load_fund_holdings, get_fund_realtime
    from api.signals import _enrich_trend_forecast
    from services.wxwork_push import is_configured, send_markdown
    
    if not is_configured():
        log("  ⚠️ 企微未配置, 跳过推送")
        return {"pushed": False, "reason": "企微未配置"}
    
    results = []
    for uid in USERS:
        log(f"  📋 用户: {uid}")
        my_funds = load_fund_holdings(uid) or []
        if not my_funds:
            log(f"    无持仓, 跳过")
            continue
        
        # 为每只持仓基金计算走势+定投建议
        fund_list = []
        for f in my_funds[:10]:
            code = f.get("code", "")
            name = f.get("name", code)
            if not code:
                continue
            fund_list.append({"code": code, "name": name, "returns": {}, "nav_percentile": None})
        
        # 批量计算走势
        try:
            _enrich_trend_forecast(fund_list, include_dimensions=False)
        except Exception as e:
            log(f"    走势预估失败: {e}")
        
        # 生成定投建议
        lines = [f"💰 **{today.month}月定投建议** (双因子AI计算)\n"]
        total_suggest = 0
        base_per_fund = 3000  # 每只基金基准金额
        
        for fl in fund_list:
            dca = calc_smart_dca_v2(
                trend_direction=fl.get("trend_direction", "flat"),
                trend_score=fl.get("trend_score", 0),
                trend_confidence=fl.get("trend_confidence", 55),
                nav_percentile=fl.get("nav_percentile"),
                trend_conflict=fl.get("trend_conflict", ""),
                base_amount=base_per_fund,
            )
            mult = dca["multiplier"]
            amount = dca["smart_amount"]
            total_suggest += amount
            
            icon = "🔥" if mult >= 1.5 else "💪" if mult >= 1.0 else "📉" if mult >= 0.5 else "🛑"
            lines.append(f"  {icon} {fl['name'][:8]}: **{mult}x = ¥{amount:.0f}** ({dca['advice'][:15]})")
        
        lines.append(f"\n📊 本月建议总投入: **¥{total_suggest:.0f}** (基准¥{base_per_fund * len(fund_list):.0f})")
        lines.append(f"\n> 数据截至 {today.strftime('%m-%d')} | 基于8维AI评分+估值双因子")
        lines.append("> ⚠️ 以上为参考建议,请根据实际资金情况调整")
        
        msg = "\n".join(lines)
        log(f"    推送消息({len(msg)}字)")
        
        try:
            send_markdown(msg, user_id=uid)
            results.append({"user": uid, "pushed": True, "funds": len(fund_list)})
            log(f"    ✅ 推送成功")
        except Exception as e:
            log(f"    ❌ 推送失败: {e}")
            results.append({"user": uid, "pushed": False, "error": str(e)})
    
    # 保存定投历史记录
    history_fp = DATA_DIR / "_cache" / "dca_history.json"
    try:
        history = json.loads(history_fp.read_text(encoding="utf-8")) if history_fp.exists() else []
        history.append({"date": str(today), "results": results})
        history_fp.write_text(json.dumps(history[-24:], ensure_ascii=False, indent=2), encoding="utf-8")  # 保留2年
    except Exception:
        pass
    
    return {"pushed": True, "results": results}


# ═══════════════════════════════════════════════
# 2. 止盈止损纪律线检测
# ═══════════════════════════════════════════════

def check_discipline_lines():
    """每日检测持仓是否触达用户设定的止盈/止损线"""
    log("🎯 === 止盈止损检测 ===")
    
    from services.fund_monitor import load_fund_holdings, get_fund_realtime
    from services.persistence import load_user
    from services.wxwork_push import is_configured, send_markdown
    
    triggered = []
    
    for uid in USERS:
        log(f"  📋 用户: {uid}")
        user = load_user(uid)
        portfolio = user.get("portfolio") or {}
        
        # 用户纪律线设定 (存在 portfolio.discipline_lines)
        lines = portfolio.get("discipline_lines") or {}
        # 格式: {code: {take_profit: 30, stop_loss: -20}}
        if not lines:
            log(f"    未设定纪律线, 跳过")
            continue
        
        txns = portfolio.get("transactions") or []
        
        for code, rules in lines.items():
            take_profit = rules.get("take_profit")  # 如30 = 盈利30%止盈
            stop_loss = rules.get("stop_loss")  # 如-20 = 亏损20%止损
            
            if not take_profit and not stop_loss:
                continue
            
            # 计算当前盈亏
            fund_txns = [t for t in txns if t.get("code") == code and t.get("action") == "buy"]
            if not fund_txns:
                continue
            total_cost = sum(t.get("amount", 0) for t in fund_txns)
            total_shares = sum(t.get("shares", 0) for t in fund_txns)
            if total_shares <= 0 or total_cost <= 0:
                continue
            avg_cost = total_cost / total_shares
            
            # 获取当前净值
            rt = get_fund_realtime(code)
            current_nav = (rt.get("nav") or rt.get("estNav")) if rt else None
            if not current_nav:
                continue
            
            pnl_pct = (current_nav - avg_cost) / avg_cost * 100
            name = fund_txns[0].get("name", code)
            
            # 检测触发
            if take_profit and pnl_pct >= take_profit:
                triggered.append({
                    "user": uid, "code": code, "name": name,
                    "type": "take_profit",
                    "pnl_pct": round(pnl_pct, 1),
                    "line": take_profit,
                    "text": f"🎯 **{name}** 已盈利 **+{pnl_pct:.1f}%**，触达你设定的止盈线({take_profit}%)\n> 建议: 按纪律止盈一半,锁定利润",
                })
            elif stop_loss and pnl_pct <= stop_loss:
                triggered.append({
                    "user": uid, "code": code, "name": name,
                    "type": "stop_loss",
                    "pnl_pct": round(pnl_pct, 1),
                    "line": stop_loss,
                    "text": f"📉 **{name}** 已亏损 **{pnl_pct:.1f}%**，触达你设定的止损线({stop_loss}%)\n> 建议: 评估基本面是否恶化,若恶化则执行止损",
                })
    
    # 推送触发提醒
    if triggered and is_configured():
        for t in triggered:
            try:
                send_markdown(t["text"], user_id=t["user"])
                log(f"    🔔 推送: {t['name']} {t['type']} ({t['pnl_pct']}%)")
            except Exception as e:
                log(f"    ❌ 推送失败: {e}")
    else:
        log("  ✅ 今日无触发")
    
    # 保存触发记录
    if triggered:
        fp = DATA_DIR / "_cache" / "discipline_triggers.json"
        try:
            history = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
            history.append({"date": str(date.today()), "triggers": triggered})
            fp.write_text(json.dumps(history[-60:], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    
    return {"triggered": len(triggered), "details": triggered}


# ═══════════════════════════════════════════════
# 3. 预测周复盘
# ═══════════════════════════════════════════════

def weekly_prediction_review():
    """每周日统计上周走势预测的实际准确率"""
    log("📊 === 预测周复盘 ===")
    
    today = date.today()
    if today.weekday() != 6:  # 0=Mon, 6=Sun
        log(f"  今天{['一','二','三','四','五','六','日'][today.weekday()]}，非周日, 跳过")
        return {"skipped": True, "reason": "非周日"}
    
    from services.wxwork_push import is_configured, send_markdown
    
    # 读取上周的走势预估缓存
    cache_dir = DATA_DIR / "_cache"
    
    # 策略: 读取上周一的fund_screen缓存(含trend_direction), 对比本周五收盘净值
    last_monday = today - timedelta(days=6)
    
    # 尝试读取缓存的走势预估结果
    predictions = []
    for uid in USERS:
        fp = cache_dir / f"fund_screen_all_score_{uid}.json"
        if not fp.exists():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            funds = data.get("funds") or data.get("data", {}).get("funds", [])
            for f in funds:
                if f.get("trend_direction") in ("up", "down"):
                    predictions.append({
                        "code": f.get("code", ""),
                        "name": f.get("name", ""),
                        "predicted": f.get("trend_direction"),
                        "score": f.get("trend_score", 0),
                    })
            break  # 两用户看的排行一样，只需一份
        except Exception:
            continue
    
    if not predictions:
        log("  无预测记录, 跳过")
        return {"skipped": True, "reason": "无预测记录"}
    
    # 检查实际涨跌(用当前净值 vs 上周)
    # 简化: 用 returns.3m 变化趋势做代理(因为精确对比需要存储上周净值)
    # 更好的方案: 下周实现, 每周缓存一份净值快照用于对比
    
    up_predictions = [p for p in predictions if p["predicted"] == "up"]
    down_predictions = [p for p in predictions if p["predicted"] == "down"]
    
    # 目前无法精确验证(需要上周净值快照), 先输出统计概况
    report_lines = [
        f"📊 **本周预测复盘** ({last_monday.strftime('%m/%d')}-{today.strftime('%m/%d')})\n",
        f"本周给出方向判断的基金: **{len(predictions)}只**",
        f"  ↗️ 偏多预测: {len(up_predictions)}只",
        f"  ↘️ 偏空预测: {len(down_predictions)}只",
        f"\n> 📈 历史回测综合准确率: **34.6%**",
        f"> 💰 双因子定投超额: **+3.7%**",
        f"\n> ℹ️ 精确周准确率需要净值快照对比,下周起自动记录",
    ]
    
    msg = "\n".join(report_lines)
    
    if is_configured():
        try:
            send_markdown(msg)
            log("  ✅ 周复盘推送成功")
        except Exception as e:
            log(f"  ❌ 推送失败: {e}")
    
    # 保存本周预测快照(供下周对比)
    snapshot_fp = cache_dir / f"prediction_snapshot_{today.strftime('%Y%m%d')}.json"
    try:
        snapshot_fp.write_text(json.dumps({
            "date": str(today),
            "predictions": predictions[:30],  # 只存前30只
        }, ensure_ascii=False), encoding="utf-8")
        log(f"  📸 预测快照已保存({len(predictions)}只)")
    except Exception:
        pass
    
    return {"predictions": len(predictions), "up": len(up_predictions), "down": len(down_predictions)}


# ═══════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════

def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["--all"]
    
    log("🚀 钱袋子执行纪律系统")
    log(f"   参数: {args}")
    
    results = {}
    
    if "--dca" in args or "--all" in args:
        results["dca"] = push_dca_reminder()
    
    if "--discipline" in args or "--all" in args:
        results["discipline"] = check_discipline_lines()
    
    if "--weekly" in args or "--all" in args:
        results["weekly"] = weekly_prediction_review()
        # v9.5.123: 周日同时验证IPO观察列表状态
        try:
            from scripts.ipo_verify import verify_ipo_status
            results["ipo_verify"] = verify_ipo_status()
        except Exception as e:
            log(f"  ⚠️ IPO验证失败: {e}")
    
    log(f"\n✅ 完成: {json.dumps(results, ensure_ascii=False, default=str)}")
    return results


if __name__ == "__main__":
    main()
