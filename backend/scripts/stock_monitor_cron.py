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
    profiles = _load_profiles()
    if not profiles:
        print("[CRON] 无用户 Profile，跳过")
        return

    all_results = {}
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        wxwork_uid = p.get("wxworkUserId", "")
        
        print(f"[CRON] 扫描用户: {name} ({uid})")
        result = scan_user(uid)
        all_results[uid] = result["combined"]

        # 按人推送
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
    """收盘后复盘"""
    print("[CRON] 收盘复盘...")
    run_scan()
    date = datetime.now().strftime("%Y-%m-%d")
    
    # 推送每日复盘给所有有企微的用户
    profiles = _load_profiles()
    for p in profiles:
        wxwork_uid = p.get("wxworkUserId", "")
        if wxwork_uid:
            try:
                from services.wxwork_push import is_configured, send_daily_report_to
                if is_configured():
                    send_daily_report_to(wxwork_uid, f"📊 {date} 收盘复盘已生成\n打开钱袋子查看详情")
            except Exception:
                pass


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
