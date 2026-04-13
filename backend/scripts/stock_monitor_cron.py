#!/usr/bin/env python3
"""
持仓盯盘 cron 脚本（股票 + 基金统一）
用法：crontab 每 10 分钟执行一次（交易时段）
  */10 9-11,13-14 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py
  30 15 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py --close

功能：
  1. 扫描全持仓（股票+基金）实时行情 + 计算指标
  2. 检测异动信号
  3. 结果保存到 JSON 文件（供前端/Claude 读取）
  4. 有异动时推送到企业微信（待接入）
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# 确保能导入 services
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.stock_monitor import scan_all_holdings, load_stock_holdings
from services.fund_monitor import scan_all_fund_holdings, load_fund_holdings

# ---- 配置 ----
MONITOR_DIR = Path(os.environ.get("MONITOR_DIR",
    Path(__file__).parent.parent.parent / "data" / "monitor"))
MONITOR_DIR.mkdir(parents=True, exist_ok=True)


def run_scan():
    """执行盯盘扫描（股票 + 基金）"""
    all_alerts = []

    # ---- 股票扫描 ----
    stock_holdings = load_stock_holdings()
    stock_result = None
    if stock_holdings:
        print(f"[CRON] 扫描 {len(stock_holdings)} 只持仓股票...")
        stock_result = scan_all_holdings()
        stock_signals = stock_result.get("signals", [])
        all_alerts.extend([s for s in stock_signals if s["level"] in ("danger", "warning")])
        print(f"[CRON] 股票: {stock_result['holdingCount']} 只, {stock_result['signalCount']} 个信号")
    else:
        print("[CRON] 无持仓股票")

    # ---- 基金扫描 ----
    fund_holdings = load_fund_holdings()
    fund_result = None
    if fund_holdings:
        print(f"[CRON] 扫描 {len(fund_holdings)} 只持仓基金...")
        fund_result = scan_all_fund_holdings()
        fund_alerts = fund_result.get("alerts", [])
        all_alerts.extend([a for a in fund_alerts if a["level"] == "warning"])
        print(f"[CRON] 基金: {len(fund_result['holdings'])} 只, {len(fund_alerts)} 个信号")
    else:
        print("[CRON] 无持仓基金")

    # 保存统一最新结果
    combined = {
        "stock": stock_result,
        "fund": fund_result,
        "totalAlerts": len(all_alerts),
        "scannedAt": datetime.now().isoformat(),
    }
    latest_file = MONITOR_DIR / "latest.json"
    latest_file.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 保存历史快照
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    snapshot_file = MONITOR_DIR / f"scan_{ts}.json"
    snapshot_file.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 推送预警
    if all_alerts:
        push_alerts(all_alerts)

    # 清理旧快照
    cleanup_old_snapshots()

    return combined


def push_alerts(signals: list):
    """推送异动信号到企业微信"""
    # 打印到日志
    print(f"[PUSH] {len(signals)} 条预警信号:")
    for s in signals:
        print(f"  [{s['level']}] {s['msg']}")

    # 企业微信应用消息推送
    try:
        from services.wxwork_push import is_configured, send_stock_alert
        if is_configured():
            result = send_stock_alert(signals)
            if result.get("ok"):
                print("[PUSH] 企业微信推送成功")
            else:
                print(f"[PUSH] 企业微信推送失败: {result.get('error', 'unknown')}")
        else:
            print("[PUSH] 企业微信未配置，跳过推送")
    except Exception as e:
        print(f"[PUSH] 推送异常: {e}")


def run_close_review():
    """收盘后全日复盘（保存完整数据+生成复盘摘要）"""
    print("[CRON] 收盘复盘...")
    result = run_scan()
    if result:
        # 生成每日复盘文件
        date = datetime.now().strftime("%Y-%m-%d")
        review_file = MONITOR_DIR / f"review_{date}.json"
        review_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"[CRON] 收盘复盘已保存: {review_file}")


def cleanup_old_snapshots(max_days: int = 7):
    """清理超过 N 天的历史快照"""
    import time
    now = time.time()
    for f in MONITOR_DIR.glob("scan_*.json"):
        if now - f.stat().st_mtime > max_days * 86400:
            f.unlink()
            print(f"[CLEANUP] 删除旧快照: {f.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="持仓盯盘 cron（股票+基金）")
    parser.add_argument("--close", action="store_true", help="收盘复盘模式")
    parser.add_argument("--review", action="store_true", help="晚间复盘模式")
    args = parser.parse_args()

    if args.close or args.review:
        run_close_review()
    else:
        run_scan()
