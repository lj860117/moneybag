#!/usr/bin/env python3
"""
持仓盯盘 cron 脚本（股票 + 基金统一，多用户按人推送）
用法：crontab 每 10 分钟执行一次（交易时段）
  */10 9-11,13-14 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py
  30 15 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py --close
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载 .env（cron 环境没有 systemd 的 EnvironmentFile）
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from services.stock_monitor import scan_all_holdings, load_stock_holdings
from services.fund_monitor import scan_all_fund_holdings, load_fund_holdings

MONITOR_DIR = Path(os.environ.get("MONITOR_DIR",
    Path(__file__).parent.parent.parent / "data" / "monitor"))
MONITOR_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = Path(os.environ.get("DATA_DIR",
    Path(__file__).parent.parent.parent / "data"))

PROFILES_FILE = DATA_DIR / "profiles.json"


def _load_profiles() -> list:
    if PROFILES_FILE.exists():
        try:
            return json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def scan_user(user_id: str) -> dict:
    """扫描单个用户的全持仓（股票+基金）"""
    all_alerts = []

    # 股票扫描
    stock_holdings = load_stock_holdings(user_id)
    stock_result = None
    if stock_holdings:
        print(f"  [股票] {len(stock_holdings)} 只...")
        stock_result = scan_all_holdings(user_id)
        stock_signals = stock_result.get("signals", [])
        all_alerts.extend([s for s in stock_signals if s["level"] in ("danger", "warning")])
        # 纪律类告警也推送（止损/止盈/集中度）
        discipline = stock_result.get("discipline", [])
        all_alerts.extend([d for d in discipline if d["level"] in ("danger", "warning")])
    
    # 基金扫描
    fund_holdings = load_fund_holdings(user_id)
    fund_result = None
    if fund_holdings:
        print(f"  [基金] {len(fund_holdings)} 只...")
        fund_result = scan_all_fund_holdings(user_id)
        fund_alerts = fund_result.get("alerts", [])
        all_alerts.extend([a for a in fund_alerts if a["level"] == "warning"])

    combined = {
        "stock": stock_result,
        "fund": fund_result,
        "totalAlerts": len(all_alerts),
        "scannedAt": datetime.now().isoformat(),
    }

    # 保存用户专属结果
    user_dir = MONITOR_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "latest.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"combined": combined, "alerts": all_alerts}


def push_user_alerts(user_id: str, wxwork_uid: str, alerts: list):
    """按用户推送异动到企微"""
    if not alerts:
        return
    
    print(f"  [推送] {len(alerts)} 条 → {wxwork_uid}")
    try:
        from services.wxwork_push import is_configured, send_stock_alert_to
        if is_configured():
            result = send_stock_alert_to(wxwork_uid, alerts)
            if result.get("ok"):
                print(f"  [推送] 成功")
            else:
                print(f"  [推送] 失败: {result.get('error', '')}")
        else:
            print(f"  [推送] 企微未配置")
    except Exception as e:
        print(f"  [推送] 异常: {e}")


def run_scan():
    """遍历所有用户，分别扫描分别推送"""
    # 休市日静默（v3.0 新增）
    try:
        from services.signal_scout import is_trading_day
        if not is_trading_day():
            print("[CRON] 非交易日，跳过扫描")
            return
    except Exception:
        pass

    profiles = _load_profiles()
    if not profiles:
        print("[CRON] 无用户 Profile，跳过")
        return

    # v3.0: 先跑一次公共信号收集（全市场共享，不按用户重复跑）
    try:
        from services.signal_scout import collect as scout_collect
        all_signals = scout_collect()
        print(f"[CRON] 信号侦察: {len(all_signals)} 条公共信号")
    except Exception as e:
        print(f"[CRON] 信号侦察失败: {e}")

    all_results = {}
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        wxwork_uid = p.get("wxworkUserId", "")
        
        print(f"[CRON] 扫描用户: {name} ({uid})")
        result = scan_user(uid)
        all_results[uid] = result["combined"]

        # v3.0: 信号匹配+推送（取代旧的单纯 alert 推送）
        try:
            from services.signal_scout import deliver as scout_deliver
            push_result = scout_deliver(uid)
            if push_result.get("pushed", 0) > 0:
                print(f"  [信号] 推送 {push_result['pushed']} 条信号 → {wxwork_uid or uid}")
        except Exception as e:
            print(f"  [信号] 推送失败: {e}")

        # 原有 alert 推送（保留，信号和 alert 双通道）
        if wxwork_uid and result["alerts"]:
            push_user_alerts(uid, wxwork_uid, result["alerts"])
        elif result["alerts"] and not wxwork_uid:
            print(f"  [推送] 用户 {name} 未绑定企微，跳过推送")

    # 保存全局最新结果（兼容旧格式）
    latest_file = MONITOR_DIR / "latest.json"
    latest_file.write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 保存历史快照
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    (MONITOR_DIR / f"scan_{ts}.json").write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    cleanup_old_snapshots()


def run_close_review():
    """收盘后复盘（升级版：扫描 + R1深度诊断 + steward review + 企微推送）"""
    print("[CRON] 收盘复盘...")
    run_scan()
    date = datetime.now().strftime("%Y-%m-%d")
    
    profiles = _load_profiles()
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        wxwork_uid = p.get("wxworkUserId", "")
        
        # ---- V4 新增：steward 收盘 review ----
        print(f"  [复盘] {name}: steward review...")
        review_text = ""
        try:
            from services.steward import get_steward
            steward = get_steward()
            review = steward.review(uid)
            review_text = review.get("summary", "")
            
            # 保存复盘结果（前端 /api/steward/review 可读）
            review_dir = MONITOR_DIR / uid / "reviews"
            review_dir.mkdir(parents=True, exist_ok=True)
            review_file = review_dir / f"{date}.json"
            review_file.write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            
            # 也存一份 latest
            (review_dir / "latest.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            
            print(f"  [复盘] {name}: 已保存 ({len(review_text)}字)")
        except Exception as e:
            print(f"  [复盘] {name}: steward review 失败: {e}")
            review_text = ""
        
        # ---- R1 深度持仓诊断（只有有持仓的用户才跑）----
        diagnosis_text = ""
        try:
            from services.stock_monitor import load_stock_holdings
            from services.fund_monitor import load_fund_holdings
            has_stocks = bool(load_stock_holdings(uid))
            has_funds = bool(load_fund_holdings(uid))
            
            if has_stocks or has_funds:
                print(f"  [诊断] {name}: R1 深度诊断...")
                from services.llm_gateway import LLMGateway
                from services.news_data import get_holdings_news, format_holdings_news_for_prompt
                gw = LLMGateway()
                
                # 组装持仓数据
                scan_file = MONITOR_DIR / uid / "latest.json"
                scan_data = ""
                if scan_file.exists():
                    scan_data = scan_file.read_text(encoding="utf-8")[:3000]
                
                # 拉持仓新闻
                holdings_news_text = ""
                try:
                    stock_h = load_stock_holdings(uid) or []
                    fund_h = load_fund_holdings(uid) or []
                    h_news = get_holdings_news(stock_h, fund_h, limit_per=3)
                    holdings_news_text = format_holdings_news_for_prompt(h_news)
                    if holdings_news_text:
                        print(f"  [诊断] {name}: 拉取 {h_news['summary']}")
                except Exception as e:
                    print(f"  [诊断] {name}: 持仓新闻拉取失败: {e}")
                
                # 加载 close_review prompt
                from pathlib import Path as _P
                _review_prompt = _P(__file__).parent.parent / "prompts" / "close_review.md"
                system_prompt = _review_prompt.read_text(encoding="utf-8") if _review_prompt.exists() else "你是专业投资组合分析师，给出简洁的收盘复盘。"
                
                if scan_data:
                    prompt = f"""## 持仓数据
{scan_data}

## 持仓相关新闻
{holdings_news_text if holdings_news_text else "暂无个股/基金新闻"}

请按 close_review 格式输出收盘复盘，300 字以内。"""
                    
                    result = gw.call_sync(
                        prompt,
                        system=system_prompt,
                        model_tier="llm_heavy",  # R1 深度推理
                        user_id=uid,
                        module="close_review",
                        max_tokens=600,
                    )
                    diagnosis_text = result.get("content", "")
                    
                    # 保存诊断结果
                    diag_file = MONITOR_DIR / uid / "reviews" / f"diagnosis_{date}.json"
                    diag_file.write_text(json.dumps({
                        "date": date,
                        "diagnosis": diagnosis_text,
                        "source": result.get("source", ""),
                        "model": result.get("model", ""),
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    
                    print(f"  [诊断] {name}: 完成 ({len(diagnosis_text)}字)")
        except Exception as e:
            print(f"  [诊断] {name}: R1 诊断失败: {e}")
        
        # ---- 企微推送（扫描结果 + 复盘 + 诊断） ----
        if wxwork_uid:
            try:
                from services.wxwork_push import is_configured, send_daily_report_to
                if is_configured():
                    # 组装推送内容
                    msg_parts = [f"📊 {date} 收盘复盘"]
                    if review_text:
                        msg_parts.append(f"\n{review_text[:200]}")
                    if diagnosis_text:
                        msg_parts.append(f"\n🤖 AI诊断:\n{diagnosis_text[:300]}")
                    msg_parts.append("\n打开钱袋子查看完整报告")
                    
                    send_daily_report_to(wxwork_uid, "\n".join(msg_parts))
                    print(f"  [推送] {name}: 复盘+诊断已推企微")
            except Exception as e:
                print(f"  [推送] {name}: 失败: {e}")

    # V4: 判断追踪 — 验证到期判断 + EMA 权重校准
    try:
        from services.judgment_tracker import verify_pending, calibrate
        for p in profiles:
            uid = p.get("id", "")
            if not uid:
                continue
            verified = verify_pending(uid)
            if verified:
                print(f"  [判断] {uid}: 验证 {len(verified)} 条判断")
                cal = calibrate(uid)
                if cal.get("status") == "calibrated":
                    print(f"  [校准] {uid}: 准确率{cal['overall_accuracy']}%, 权重已更新")
    except Exception as e:
        print(f"  [判断/校准] 失败: {e}")


def cleanup_old_snapshots(max_days: int = 7):
    import time
    now = time.time()
    for f in MONITOR_DIR.glob("scan_*.json"):
        if now - f.stat().st_mtime > max_days * 86400:
            f.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="持仓盯盘 cron（多用户按人推送）")
    parser.add_argument("--close", action="store_true", help="收盘复盘模式")
    args = parser.parse_args()

    if args.close:
        run_close_review()
    else:
        run_scan()
