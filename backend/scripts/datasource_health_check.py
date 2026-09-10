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
    {"name": "全球期货(外盘速览)", "source": "akshare", "func": "futures_global_spot_em",
     "args": {}, "expect": "rows > 100"},
    {"name": "恒指日线(新浪)", "source": "akshare", "func": "stock_hk_index_daily_sina",
     "args": {"symbol": "HSI"}, "expect": "rows > 100"},

    # 降级备份：用 stock_zh_a_spot（带缓存+超时控制）替代 stock_zh_a_spot_em，
    # 避开东方财富接口偶发限流/封禁。
    #
    # FIX 2026-09-11：critical 由 False 改为 True。原先注释写着
    # "critical=False，失败不影响整体健康结论"，但 critical 字段从未被
    # run_health_check 读取过（声明未实现）。更重要的是：本项与生产降级链
    # 的源 2 共用同一个上游 ak.stock_zh_a_spot
    # （services/stock_data_provider.py 的 _try_sina_xq_source），
    # 东财主源反爬时会连坐，是有效前哨，不能静音。
    {"name": "[降级]实时行情(优化)", "source": "akshare_optimized", "func": "optimized_stock_spot",
     "args": {"use_cache": True, "timeout": 30}, "expect": "rows > 100",
     "critical": True, "note": "带缓存和超时控制；生产降级链源2前哨，不可静音"},

    # === 外汇（P0，2026-09-11 补入，此前 13 项不含外汇 → 永久盲区）===
    # 注意：不能只测"接口能不能调通"。2026-09-11 的事故正是数据源正常返回
    # 25 行、但解析条件永不命中，导致 usdcny 恒为 None、外汇 100% 缺失却
    # 永不告警。所以这里校验的是「能否解析出有效的 usdcny 数值」。
    {"name": "外汇(USD/CNY)", "source": "forex", "func": "get_forex_data",
     "args": {}, "expect": "usdcny > 0", "critical": True,
     "note": "校验能解析出有效 usdcny，而非仅接口可调通"},

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


def _check_akshare_optimized(check: dict) -> dict:
    """检查 AKShare 接口（使用优化包装器：带缓存+超时控制）

    FIX 2026-08-09: 从服务器版本合并回本地（该检查项配置在服务器
    HEALTH_CHECKS 里一直生效，本地代码库此前缺失这个分支和依赖模块，
    补齐 scripts/akshare_optimized.py 后一并合并这里的调度逻辑，
    保持本地 dict 返回风格（{"ok":..,"detail":..}）与其余分支一致。
    """
    try:
        from scripts.akshare_optimized import optimized_stock_spot

        args = check.get("args", {})
        # FIX 2026-09-11: raise_on_error=True，让真实异常冒出来而不是被
        # optimized_stock_spot 吞成 None。原实现返回固定文案「调用失败或超时」，
        # 把"新浪反爬返回 HTML 导致解码失败"和"真的超时"混为一谈，
        # 严重误导排障（2026-09-11 实际是前者，实耗 21.8s 并未超时）。
        data = optimized_stock_spot(raise_on_error=True, **args)

        if data is None:
            return {"ok": False, "detail": "调用返回空（无数据）"}

        if check["expect"].startswith("rows"):
            row_count = len(data) if data is not None else 0
            threshold = int(check["expect"].split(">")[1].strip())
            ok = row_count > threshold
            detail = f"{row_count} 行"
        else:
            ok = data is not None and len(data) > 0
            detail = "有数据" if ok else "无数据"

        return {"ok": ok, "detail": detail}
    except Exception as e:
        return {"ok": False, "detail": f"异常: {str(e)[:80]}"}


def _check_forex(check: dict) -> dict:
    """检查外汇链路（Tushare 主 + AKShare 降级）

    关键点：不能只验证"接口返回了 DataFrame"。2026-09-11 的事故正是数据源
    正常返回 25 行、但 services/global_market.py 的解析条件用了中文匹配
    （"美元"/"人民币"）而实际数据是 ISO 代码（USD/CNY），命中 0 行 →
    usdcny 恒为 None → 外汇 100% 缺失且无任何告警。

    所以这里直接复用 get_forex_data() 的真实解析路径，以 available 与
    usdcny.rate 作为判定依据——即验证"能解析出有效数值"，而非"接口可调通"。
    """
    try:
        from services.global_market import get_forex_data

        data = get_forex_data()
        if not isinstance(data, dict):
            return {"ok": False, "detail": f"返回值类型异常: {type(data).__name__}"}

        if not data.get("available"):
            return {"ok": False, "detail": "available=False，未解析出 usdcny（数据源有数据但解析失败？）"}

        usd = data.get("usdcny") or {}
        rate = usd.get("rate")
        try:
            rate_val = float(rate)
        except (TypeError, ValueError):
            return {"ok": False, "detail": f"usdcny.rate 非法: {rate!r}"}

        if rate_val <= 0:
            return {"ok": False, "detail": f"usdcny.rate 非正值: {rate_val}"}

        # 在岸 USD/CNY 的主源是 AKShare；若落到 Tushare 的离岸 USD/CNH，
        # 说明主源已故障、正在用离岸价兜底——必须判失败并告警，
        # 不能因为「还有个值」就当没事（这正是 2026-09-11 的病根：
        # 主源坏了靠降级撑着，全靠一行没人看的日志）。
        if usd.get("proxy"):
            return {
                "ok": False,
                "detail": (f"⚠️ 主源降级：当前 USD/CNY 由 Tushare 离岸 USD/CNH 兜底 "
                           f"= {rate_val}（{usd.get('name', '?')}），AKShare 在岸主源已不可用"),
            }

        return {"ok": True, "detail": f"USD/CNY={rate_val}（来源 {usd.get('source', '?')}）"}
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
                "critical": bool(check.get("critical", True)),
                "timestamp": datetime.now().isoformat(),
            })
            print(f"  ⏭️ [{source}] {name}: 非交易时段，跳过")
            continue
        if name in _TRADING_DAY_ONLY and not trading_day:
            results.append({
                "name": name, "source": source, "ok": True,
                "status": "⏭️", "detail": "非交易日，跳过",
                "critical": bool(check.get("critical", True)),
                "timestamp": datetime.now().isoformat(),
            })
            print(f"  ⏭️ [{source}] {name}: 非交易日，跳过")
            continue
        if name in _TRADING_DAY_ONLY and not trading_hours:
            results.append({
                "name": name, "source": source, "ok": True,
                "status": "⏭️", "detail": "收盘前无数据，跳过",
                "critical": bool(check.get("critical", True)),
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
        elif source == "akshare_optimized":
            result = _check_akshare_optimized(check)
        elif source == "forex":
            result = _check_forex(check)
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
            # FIX 2026-09-11: 透传 critical。此前该字段只在 HEALTH_CHECKS
            # 里声明、从未进入 results，导致 main() 无从区分关键/非关键失败，
            # 所有失败一律推企微（"失败不影响整体健康结论"的设计意图落空）。
            "critical": bool(check.get("critical", True)),
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
    """写入巡检日志（供 main() 调用）"""
    save_health_results(results)


def save_health_results(results: list) -> Path:
    """公开落盘入口：供 night_worker.step_health_check 等外部调用方复用。

    night_worker 里 step_health_check 直接调 run_health_check() 拿到结果，
    但 run_health_check() 本身不落盘，导致 data/health/{date}.json 长期不更新，
    元巡检（ops_summary）靠落盘文件 mtime 判新鲜度 → 误判「巡检失效」。
    这里抽成公开函数，让调用方能显式落盘。
    """
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
    return log_file


def main():
    print(f"🔍 数据源健康巡检 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"   检查 {len(HEALTH_CHECKS)} 个数据源...\n")

    results = run_health_check()

    # 统计
    # FIX 2026-09-11: 只有 critical 为真的失败才计入告警 failures；
    # critical=False 的失败降级为日志提示，不再推企微。此前该判断完全缺失，
    # 所有失败一律推送，导致"失败不影响整体健康结论"的设计意图从未生效。
    all_failures = [r for r in results if r["status"] == "❌"]
    failures = [r for r in all_failures if r.get("critical", True)]
    ok_count = len(results) - len(all_failures)
    print(f"\n{'='*40}")
    print(f"  ✅ 正常: {ok_count}    ❌ 异常: {len(all_failures)}（其中需告警: {len(failures)}）")
    if len(failures) < len(all_failures):
        print("  ℹ️ 以下非关键项失败（critical=False，不推送告警）：")
        for r in all_failures:
            if not r.get("critical", True):
                print(f"     - [{r['source']}] {r['name']}: {r['detail']}")

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
