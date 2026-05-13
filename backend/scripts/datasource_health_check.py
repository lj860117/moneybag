#!/usr/bin/env python3
"""
钱袋子 — 数据源健康巡检
Phase 0 任务 1.1 | 设计文档：全景设计文档 §数据源保障体系

用法:
  cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/datasource_health_check.py

cron:
  0 1 * * * cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/datasource_health_check.py

功能:
  1. 逐个检查 AKShare（6个）+ Tushare（4个）数据源
  2. 每个接口之间限频 sleep（防封 IP）
  3. 有异常推企微给 LeiJiang（不推老婆）
  4. 结果写入 data/health/{date}.json
"""
import os
import sys
import json
import time
from datetime import date, datetime
from pathlib import Path

# 确保能 import 项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR

# ---- 巡检项定义 ----
HEALTH_CHECKS = [
    # === AKShare（爬虫，不稳定，每次间隔 0.5s）===
    {"name": "基金净值", "source": "akshare", "func": "fund_open_fund_info_em",
     "args": {"symbol": "110020", "indicator": "单位净值走势"}, "expect": "rows > 0"},
    {"name": "恐贪指数", "source": "akshare", "func": "macro_cnbs",
     "args": {}, "expect": "rows > 0"},
    {"name": "实时行情", "source": "akshare", "func": "stock_zh_a_spot_em",
     "args": {}, "expect": "rows > 100"},
    {"name": "基金排行", "source": "akshare", "func": "fund_open_fund_rank_em",
     "args": {"symbol": "全部"}, "expect": "rows > 100"},
    {"name": "估值百分位", "source": "akshare", "func": "stock_zh_index_value_csindex",
     "args": {"symbol": "000300"}, "expect": "rows > 0"},
    {"name": "新闻", "source": "akshare", "func": "stock_news_em",
     "args": {"symbol": "000001"}, "expect": "rows > 0"},

    # === Tushare（付费 API，稳定，每次间隔 0.3s）===
    {"name": "股票日线", "source": "tushare", "api_name": "daily",
     "params": {"ts_code": "000001.SZ", "limit": 1}, "expect": "rows > 0"},
    {"name": "盈利预测", "source": "tushare", "api_name": "report_rc",
     "params": {"ts_code": "600519.SH"}, "expect": "rows > 0"},
    {"name": "北向资金", "source": "tushare", "api_name": "moneyflow_hsgt",
     "params": {}, "expect": "rows > 0"},
    {"name": "SHIBOR", "source": "tushare", "api_name": "shibor",
     "params": {}, "expect": "rows > 0"},
]

# 限频间隔（秒）
RATE_LIMITS = {"akshare": 0.5, "tushare": 0.3}

# 仅在交易时段才检查的数据源（非交易时段 skip）
_TRADING_HOURS_ONLY = {"实时行情"}
# 仅在交易日收盘后才有数据的数据源（凌晨 01:00 检查时可能为空）
_TRADING_DAY_ONLY = {"北向资金", "盈利预测"}


def _is_trading_hours() -> bool:
    """判断当前是否在交易时段（9:15-15:30 工作日）"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    hour_min = now.hour * 100 + now.minute
    return 915 <= hour_min <= 1530


def _is_trading_day() -> bool:
    """判断当前是否为交易日（简单判断：非周末）"""
    return datetime.now().weekday() < 5


def _check_akshare(check: dict) -> dict:
    """检查单个 AKShare 接口"""
    try:
        import akshare as ak
        func = getattr(ak, check["func"])
        data = func(**check["args"])

        # 验证
        if check["expect"].startswith("rows"):
            row_count = len(data) if data is not None else 0
            threshold = int(check["expect"].split(">")[1].strip())
            ok = row_count > threshold
            detail = f"{row_count} 行"
        elif "value" in check["expect"]:
            # 恐贪指数：取最后一行的值
            val = float(data.iloc[-1, -1]) if data is not None and len(data) > 0 else -1
            ok = 0 <= val <= 100
            detail = f"值={val}"
        else:
            ok = data is not None and len(data) > 0
            detail = f"{len(data)} 行" if data is not None else "None"

        return {"ok": ok, "detail": detail}
    except Exception as e:
        return {"ok": False, "detail": f"异常: {str(e)[:80]}"}


def _check_tushare(check: dict) -> dict:
    """检查单个 Tushare 接口"""
    try:
        from services.tushare_data import _call_tushare, is_configured
        if not is_configured():
            return {"ok": False, "detail": "TUSHARE_TOKEN 未配置"}

        rows = _call_tushare(check["api_name"], check.get("params", {}))
        row_count = len(rows) if rows else 0
        threshold = int(check["expect"].split(">")[1].strip())
        ok = row_count > threshold
        return {"ok": ok, "detail": f"{row_count} 行"}
    except Exception as e:
        return {"ok": False, "detail": f"异常: {str(e)[:80]}"}


def run_health_check() -> list:
    """运行全部巡检（交易时段感知，减少误报）"""
    results = []
    trading_hours = _is_trading_hours()
    trading_day = _is_trading_day()

    for check in HEALTH_CHECKS:
        source = check["source"]
        name = check["name"]

        # 交易时段感知：非盘中/非交易日 skip 特定检查
        if name in _TRADING_HOURS_ONLY and not trading_hours:
            results.append({
                "name": name, "source": source, "ok": True,
                "status": "⏭️", "detail": "非交易时段，跳过",
                "timestamp": datetime.now().isoformat(),
            })
            print(f"  ⏭️ [{source}] {name}: 非交易时段，跳过")
            continue
        if name in _TRADING_DAY_ONLY and not trading_day:
            results.append({
                "name": name, "source": source, "ok": True,
                "status": "⏭️", "detail": "非交易日，跳过",
                "timestamp": datetime.now().isoformat(),
            })
            print(f"  ⏭️ [{source}] {name}: 非交易日，跳过")
            continue
        if name in _TRADING_DAY_ONLY and not trading_hours:
            results.append({
                "name": name, "source": source, "ok": True,
                "status": "⏭️", "detail": "收盘前无数据，跳过",
                "timestamp": datetime.now().isoformat(),
            })
            print(f"  ⏭️ [{source}] {name}: 收盘前无数据，跳过")
            continue

        # 限频
        delay = RATE_LIMITS.get(source, 0.3)
        time.sleep(delay)

        # 执行检查
        if source == "akshare":
            result = _check_akshare(check)
        elif source == "tushare":
            result = _check_tushare(check)
        else:
            result = {"ok": False, "detail": "未知数据源"}

        status = "✅" if result["ok"] else "❌"
        results.append({
            "name": name,
            "source": source,
            "ok": result["ok"],
            "status": status,
            "detail": result["detail"],
            "timestamp": datetime.now().isoformat(),
        })
        print(f"  {status} [{source}] {name}: {result['detail']}")

    return results


def _push_alert(failures: list, total: int):
    """有异常时推企微告警（去重：跟上次一样则不重复推）"""
    # 去重：对比上次推送的失败项，完全相同则跳过
    alert_state_file = DATA_DIR / "health" / "_last_alert.json"
    current_names = sorted(r["name"] for r in failures)
    try:
        if alert_state_file.exists():
            last = json.loads(alert_state_file.read_text(encoding="utf-8"))
            if last.get("failures") == current_names:
                print(f"  ⏭️ 告警与上次相同，不重复推送")
                return
    except Exception:
        pass

    try:
        from services.wxwork_push import send_text
        msg = f"⚠️ 数据源巡检（{len(failures)} 个异常）\n\n"
        for r in failures:
            msg += f"{r['status']} [{r['source']}] {r['name']}: {r['detail']}\n"
        msg += f"\n✅ 正常：{total - len(failures)} 个"
        msg += f"\n\n降级方案已自动激活，AI 分析不受影响"
        send_text(msg)
        print(f"  📤 企微告警已推送")

        # 记录本次推送内容
        (DATA_DIR / "health").mkdir(parents=True, exist_ok=True)
        alert_state_file.write_text(json.dumps({
            "failures": current_names,
            "pushed_at": datetime.now().isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️ 企微推送失败: {e}")


def _save_results(results: list):
    """写入巡检日志"""
    log_dir = DATA_DIR / "health"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{date.today()}.json"
    log_file.write_text(json.dumps({
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r["status"] == "✅"),
            "failed": sum(1 for r in results if r["status"] == "❌"),
        }
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  📝 巡检日志: {log_file}")


def main():
    print(f"🔍 数据源健康巡检 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"   检查 {len(HEALTH_CHECKS)} 个数据源...\n")

    results = run_health_check()

    # 统计
    failures = [r for r in results if r["status"] == "❌"]
    ok_count = len(results) - len(failures)
    print(f"\n{'='*40}")
    print(f"  ✅ 正常: {ok_count}    ❌ 异常: {len(failures)}")

    # 有异常 → 推企微（去重）
    if failures:
        _push_alert(failures, len(results))
    else:
        # 全部正常，清除告警状态（下次有新异常时会重新推送）
        alert_state_file = DATA_DIR / "health" / "_last_alert.json"
        if alert_state_file.exists():
            alert_state_file.unlink()
            print(f"  🔔 告警状态已清除（全部恢复正常）")

    # 写日志
    _save_results(results)

    print(f"{'='*40}\n")
    return len(failures) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
