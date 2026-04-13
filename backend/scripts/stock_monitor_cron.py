#!/usr/bin/env python3
"""
股票盯盘 cron 脚本
用法：crontab 每 10 分钟执行一次（交易时段）
  */10 9-11,13-14 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py
  30 15 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py --close
  0 20 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py --review

功能：
  1. 扫描全持仓实时行情+计算技术指标
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

# ---- 配置 ----
MONITOR_DIR = Path(os.environ.get("MONITOR_DIR",
    Path(__file__).parent.parent.parent / "data" / "monitor"))
MONITOR_DIR.mkdir(parents=True, exist_ok=True)


def run_scan():
    """执行盯盘扫描"""
    holdings = load_stock_holdings()
    if not holdings:
        print("[CRON] 无持仓股票，跳过")
        return

    print(f"[CRON] 扫描 {len(holdings)} 只持仓股票...")
    result = scan_all_holdings()

    # 保存最新结果
    latest_file = MONITOR_DIR / "latest.json"
    latest_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 保存历史快照（按时间戳）
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    snapshot_file = MONITOR_DIR / f"scan_{ts}.json"
    snapshot_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 输出摘要
    signals = result.get("signals", [])
    danger = [s for s in signals if s["level"] in ("danger", "warning")]
    print(f"[CRON] 扫描完成: {result['holdingCount']} 只股票, "
          f"{result['signalCount']} 个信号, {len(danger)} 个预警")

    # 如果有危险/警告信号，推送通知
    if danger:
        push_alerts(danger)

    # 清理超过 7 天的历史快照
    cleanup_old_snapshots()

    return result


def push_alerts(signals: list):
    """推送异动信号到企业微信（待接入）"""
    # TODO: 企业微信推送（Phase 5 用户注册后接入）
    # 目前先打印到日志
    print(f"[PUSH] {len(signals)} 条预警信号:")
    for s in signals:
        print(f"  [{s['level']}] {s['msg']}")

    # 占位：企业微信 Webhook
    webhook_url = os.environ.get("WECHAT_WEBHOOK")
    if webhook_url:
        try:
            import httpx
            content = "\n".join([s["msg"] for s in signals])
            httpx.post(webhook_url, json={
                "msgtype": "text",
                "text": {"content": f"📈 钱袋子盯盘预警\n\n{content}"}
            }, timeout=10)
            print("[PUSH] 企业微信推送成功")
        except Exception as e:
            print(f"[PUSH] 企业微信推送失败: {e}")


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
    parser = argparse.ArgumentParser(description="股票盯盘 cron")
    parser.add_argument("--close", action="store_true", help="收盘复盘模式")
    parser.add_argument("--review", action="store_true", help="晚间复盘模式")
    args = parser.parse_args()

    if args.close or args.review:
        run_close_review()
    else:
        run_scan()
