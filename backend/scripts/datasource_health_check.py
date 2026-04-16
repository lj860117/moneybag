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
     "args": {"fund": "110020", "indicator": "单位净值"}, "expect": "rows > 0"},
    {"name": "恐贪指数", "source": "akshare", "func": "index_fear_greed_funddb",
     "args": {}, "expect": "0 <= value <= 100"},
    {"name": "实时行情", "source": "akshare", "func": "stock_zh_a_spot_em",
     "args": {}, "expect": "rows > 100"},
    {"name": "基金排行", "source": "akshare", "func": "fund_open_fund_rank_em",
     "args": {"symbol": "全部"}, "expect": "rows > 100"},
    {"name": "估值百分位", "source": "akshare", "func": "index_value_hist_funddb",
     "args": {"symbol": "沪深300", "indicator": "市盈率"}, "expect": "rows > 0"},
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
    """运行全部巡检"""
    results = []
    for check in HEALTH_CHECKS:
        source = check["source"]
        name = check["name"]

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
            "status": status,
            "detail": result["detail"],
            "timestamp": datetime.now().isoformat(),
        })
        print(f"  {status} [{source}] {name}: {result['detail']}")

    return results


def _push_alert(failures: list, total: int):
    """有异常时推企微告警（只推给 LeiJiang）"""
    try:
        from services.wxwork_push import send_text
        msg = f"⚠️ 数据源巡检（{len(failures)} 个异常）\n\n"
        for r in failures:
            msg += f"{r['status']} [{r['source']}] {r['name']}: {r['detail']}\n"
        msg += f"\n✅ 正常：{total - len(failures)} 个"
        msg += f"\n\n降级方案已自动激活，AI 分析不受影响"
        send_text(msg)  # 只推给 LeiJiang，不推给 BuLuoGeLi
        print(f"  📤 企微告警已推送")
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

    # 有异常 → 推企微
    if failures:
        _push_alert(failures, len(results))

    # 写日志
    _save_results(results)

    print(f"{'='*40}\n")
    return len(failures) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
