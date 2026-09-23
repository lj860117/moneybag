#!/usr/bin/env python3
"""
钱袋子 — 数据源健康巡检
Phase 0 任务 1.1 | 设计文档：全景设计文档 §数据源保障体系

用法:
  cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/datasource_health_check.py

cron:
  # FIX 2026-09-22：这里原先写 `0 1 * * *`，与服务器真实排班不符 —— 服务器上
  # 这条是 **01:20**（`20 1 * * *`），夜里还有一份 01:00 的重复巡检跑在
  # night_worker 里（已本轮移除）。文档写错会让排障时按 01:00 去找日志、
  # 白查一遍。已按服务器口径改为 01:20。
  20 1 * * * cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/datasource_health_check.py

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
from datetime import date, datetime, timedelta
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
    # FIX 2026-09-22: 加 trading_hours_only 标记。此前跳过逻辑靠
    # `_TRADING_HOURS_ONLY = {"实时行情"}` 这个**名称字符串集合**匹配，
    # 只要改名或新增同类检查项就会静默漏掉（下面的降级项就是这么漏的）。
    # 布尔标记比名称匹配稳，调度改为读该字段（见 run_health_check）。
    {"name": "实时行情", "source": "akshare", "func": "stock_zh_a_spot_em",
     "args": {}, "expect": "rows > 100", "trading_hours_only": True},
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
    # FIX 2026-09-22，两处：
    #   1) timeout 30 → 60。ak.stock_zh_a_spot 是 70 页分页全量拉取（5564 行），
    #      实测 19.42s / 21.97s，30 秒只留 8 秒余量，一次网络抖动就误判超时
    #      （2026-09-22 日报里的「调用超时（>30秒）」就是这么来的）。
    #      放宽到 60 秒是安全的：同日修复的真超时（akshare_optimized.
    #      _run_with_timeout）保证到点一定返回，不会再拖死进程。
    #   2) 本项**故意不打** trading_hours_only，与上面的「实时行情」策略不同，
    #      这是权衡后的决定，不要"顺手统一"：
    #        - 「实时行情」= 盘中快照，非交易时段取不到当日数据，跳过是省一次
    #          无意义请求；
    #        - 本项 = **生产降级链源 2（ak.stock_zh_a_spot / 新浪）的前哨**
    #          （见 services/stock_data_provider.py 的 _try_sina_xq_source），
    #          critical=True、注释明确写了「不可静音」。东财主源反爬时降级链
    #          会连坐到这个源，凌晨跑一次是为白天开盘提前发现它已经不可用。
    #      前哨价值 > 省一次 70 页拉取的成本 —— 何况现在 timeout 已放宽到 60
    #      秒、缓存 TTL 300 秒、且超时是真超时不会挂死进程，凌晨跑一次成本可控
    #      （2026-09-22 主理人拍板：保留前哨，撤销 trading_hours_only）。
    #   3) ⚠️ 千万别给本项补 trading_hours_only —— 那等于**永久删除这个前哨**。
    #      原因：巡检只在 01:20 跑一次（见本文件顶部 cron 示例），而
    #      `_is_trading_hours()` 判定的是 A 股 9:15-15:30 工作日 —— **01:20 对
    #      A 股永远是非交易时段**，所以一旦打上标记，本项在自动链里就永不执行，
    #      不是「非交易时段省一次、交易时段还跑」。降级链源 2 失效将不再有任何
    #      自动告警（2026-09-23 复核确认，主理人据此维持保留前哨的决定）。
    {"name": "[降级]实时行情(优化)", "source": "akshare_optimized", "func": "optimized_stock_spot",
     "args": {"use_cache": True, "timeout": 60}, "expect": "rows > 100",
     "critical": True,
     "note": "带缓存和超时控制；生产降级链源2前哨，不可静音（非交易时段也照跑）"},

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
#
# FIX 2026-09-22: 优先读 HEALTH_CHECKS 里的 `trading_hours_only` 布尔字段
# （见 run_health_check），本集合退化为**兼容兜底**——保留它是因为项目里
# 可能有其他地方/历史分支按名称引用它，且它能兜住没打标记的老条目。
# 名称匹配是脆的：「[降级]实时行情(优化)」与「实时行情」只差几个字，
# 结果一个被跳过、一个照跑（2026-09-22 实证），所以新条目一律用布尔字段。
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


# 银行间人民币外汇即期（CFETS）交易时段。
# 来源：中国货币网（chinamoney.org.cn，即 akshare fx_spot_quote 的上游）
# 「人民币外汇即期」产品页原文：交易时间 北京时间 9:30 - 次日 3:00，
# 周六、周日及法定节假日不开市。
#
# 所以「非交易时段」= 每天 03:00-09:30 的盘面重置空档 + 整个周六周日。
# 这个区间拿不到在岸报价是**预期行为**，不是故障，不该天天告警。
#
# 实测佐证（2026-09-11）：
#   08:30（空档内）→ 在岸主源不可用，落到离岸 USD/CNH 兜底 6.7138
#   10:58（开盘后）→ 在岸主源正常，USD/CNY=6.7109
#   02:30（夜盘内）→ 巡检 ✅ USD/CNY=6.712（来源 akshare）
_FX_SESSION_OPEN_HHMM = 930     # 09:30 开盘
_FX_SESSION_CLOSE_HOUR = 3      # 次日 03:00 收市（跨零点）
# 交易时段文案，告警/跳过详情里复用，避免几处说法漂移
_FX_HOURS_TEXT = "CFETS 人民币外汇即期 9:30-次日3:00，周六日及法定节假日休市"

# FIX 2026-09-22：独立的「降级」态 ⚠️，与 ✅ 正常 / ❌ 失败 三分，不要合并。
#
# 为什么需要第三种态：外汇主源（AKShare 在岸）挂了、正在用 Tushare 离岸
# USD/CNH 兜底时，服务是「有值但不可信」——判 ✅ 会让故障隐形（2026-09-22
# 实测：cron.log 里 2 次降级，health JSON 里却写着 ✅），判 ❌ 又把一个
# 「还有兜底价可用」的状态说成彻底失败，两者都在撒谎。
#
# ⚠️ 两条硬约束（改动前先读，别"顺手统一"）：
#   1. **必须区分交易时段**（见 `_check_forex`）：非交易时段（03:00-09:30
#      空档 + 周末）在岸主源本来就没报价，走离岸兜底是**预期路径**，一律
#      降级成 ⏭️ 跳过、不告警；只有交易时段的降级才算降级态。否则每天
#      08:30 都会误报一次 —— 那正好是本次要修的「把预期变成告警」。
#   2. **不得计入 error_logs_24h 错误计数**：那是「错误」计数，不是「降级」
#      计数。告警文案里降级项一律用 ⚠️ 前缀、绝不写 ❌（❌ 会被
#      ops_summary.collect_error_logs 的关键字表命中），否则会污染刚修干净的
#      日报数字。
_DEGRADED_STATUS = "⚠️"


def _is_fx_trading_hours(now: datetime | None = None) -> bool:
    """判断当前是否处于银行间人民币外汇即期交易时段（9:30 - 次日 3:00）。

    ⚠️ 不是「9:30-23:30」。CFETS 已将人民币外汇市场延长到次日 03:00 收市，
    所以**凌晨 01:20 的巡检落在交易时段内**，这时拿不到数据是真故障、要告警。
    （团队最初的判断是「01:20 属非交易时段」，与官方时段和实测都不符：
      2026-09-11 02:30 的巡检是 ✅ 通过。已按实测口径实现。）

    跨零点的处理：03:00 之前的时刻属于**前一交易日**的夜盘，所以要先回退一天
    再判周末，否则周六 01:00（属于周五夜盘、本应开市）会被误判成休市。

    Args:
        now: 本地时间，便于测试注入；None 表示取当前时间。

    Returns:
        True 表示处于交易时段。
    """
    now = now or datetime.now()
    # 03:00 之前 → 归到前一交易日的场次
    anchor = now - timedelta(days=1) if now.hour < _FX_SESSION_CLOSE_HOUR else now
    if anchor.weekday() >= 5:  # 5=周六 6=周日
        return False
    hhmm = now.hour * 100 + now.minute
    if _FX_SESSION_CLOSE_HOUR * 100 <= hhmm < _FX_SESSION_OPEN_HHMM:
        return False  # 每日 03:00-09:30 盘面重置空档
    return True


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
            # 非交易时段（03:00-09:30 盘面重置空档 / 周末）拿不到在岸报价是预期
            # 行为，不是故障——见 _is_fx_trading_hours 的时段说明与实测佐证。
            # 只在非交易时段降级为「跳过」，交易时段仍然照常判失败并告警。
            if not _is_fx_trading_hours():
                return {"ok": True, "status": "⏭️",
                        "detail": f"非交易时段（{_FX_HOURS_TEXT}），"
                                  f"无在岸报价属预期，不告警"}
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
        # 说明主源已故障、正在用离岸价兜底——不能因为「还有个值」就当没事
        # （这正是 2026-09-11 的病根：主源坏了靠降级撑着，全靠一行没人看的日志）。
        # 例外：非交易时段在岸主源本来就没有报价，落到离岸兜底是**预期路径**，
        # 这时降级成「跳过」而不是告警，否则每天 08:30 都会误报一次。
        if usd.get("proxy"):
            if not _is_fx_trading_hours():
                return {"ok": True, "status": "⏭️",
                        "detail": (f"非交易时段（{_FX_HOURS_TEXT}）：在岸主源无报价属预期，"
                                   f"当前为 Tushare 离岸 USD/CNH 兜底 = {rate_val}，不告警")}
            # FIX 2026-09-22: 交易时段的降级 —— 改成独立的 ⚠️ 降级态，不再判 ❌。
            #
            # 判 ❌ 有两个问题：① 把「还有兜底价可用」说成彻底失败；
            # ② 告警文案会带 ❌ 关键字，被 ops_summary 的 error_logs_24h 当成
            #   真错误计入（降级不是错误，混进去会污染刚修干净的日报数字）。
            # 判 ✅ 更不行：主源已挂，却在巡检里显示健康，等于隐形（本次要修的就是它）。
            #
            # ok=True 表示「没到失败」，`degraded=True` 表示「值不可信」，
            # 两者合起来由 main() 走独立的降级告警通道（⚠️ 前缀，不计错误数）。
            return {
                "ok": True,
                "status": _DEGRADED_STATUS,
                "degraded": True,
                "detail": (f"主源降级：当前 USD/CNY 由 Tushare 离岸 USD/CNH 兜底 "
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
        #
        # FIX 2026-09-22: 判定改为优先读 check["trading_hours_only"] 布尔字段，
        # 名称集合 _TRADING_HOURS_ONLY 只作兼容兜底。此前只按名称匹配，
        # 导致「[降级]实时行情(优化)」在非交易时段仍执行 70 页全量拉取。
        if (check.get("trading_hours_only") or name in _TRADING_HOURS_ONLY) and not trading_hours:
            results.append({
                "name": name, "source": source, "ok": True,
                "status": "⏭️", "detail": "非交易时段，跳过",
                "degraded": False,
                "critical": bool(check.get("critical", True)),
                "timestamp": datetime.now().isoformat(),
            })
            print(f"  ⏭️ [{source}] {name}: 非交易时段，跳过")
            continue
        if name in _TRADING_DAY_ONLY and not trading_day:
            results.append({
                "name": name, "source": source, "ok": True,
                "status": "⏭️", "detail": "非交易日，跳过",
                "degraded": False,
                "critical": bool(check.get("critical", True)),
                "timestamp": datetime.now().isoformat(),
            })
            print(f"  ⏭️ [{source}] {name}: 非交易日，跳过")
            continue
        if name in _TRADING_DAY_ONLY and not trading_hours:
            results.append({
                "name": name, "source": source, "ok": True,
                "status": "⏭️", "detail": "收盘前无数据，跳过",
                "degraded": False,
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

        # 允许检查函数自带 status（如外汇在非交易时段返回 ⏭️「跳过」），
        # 否则按 ok 推断 ✅/❌。
        status = result.get("status") or ("✅" if result["ok"] else "❌")
        results.append({
            "name": name,
            "source": source,
            "ok": result["ok"],
            "status": status,
            "detail": result["detail"],
            # FIX 2026-09-22: 降级态 ⚠️ 透传（目前只有外汇会置位，见 `_check_forex`）。
            # 它是「有值但不可信」，既不是 ✅ 也不是 ❌，落盘与告警都要能区分。
            "degraded": bool(result.get("degraded", False)),
            # FIX 2026-09-11: 透传 critical。此前该字段只在 HEALTH_CHECKS
            # 里声明、从未进入 results，导致 main() 无从区分关键/非关键失败，
            # 所有失败一律推企微（"失败不影响整体健康结论"的设计意图落空）。
            "critical": bool(check.get("critical", True)),
            "timestamp": datetime.now().isoformat(),
        })
        print(f"  {status} [{source}] {name}: {result['detail']}")

    return results


def _push_alert(failures: list, total: int, degraded: list | None = None):
    """有异常/降级时推企微告警（去重：跟上次一样则不重复推）

    FIX 2026-09-22: 增加 `degraded` 参数，走**独立的降级通道**：
      - 文案一律用 ⚠️ 前缀，**绝不写 ❌**（❌ 会被 ops_summary 的
        `collect_error_logs` 关键字表命中，把「降级」数进「错误」，污染刚修
        干净的 24h 错误计数）。降级不是错误，只是「值不可信」。
      - 与 failures 分开列出，让人一眼看出哪些是彻底挂了、哪些是撑着。

    Args:
        failures: ❌ 失败且需要告警的条目。
        total: 巡检总条目数。
        degraded: ⚠️ 降级条目（有值但来自兜底源），可为 None。
    """
    degraded = degraded or []
    # 去重：对比上次推送的失败项与降级项，完全相同则跳过
    alert_state_file = DATA_DIR / "health" / "_last_alert.json"
    current_names = sorted(r["name"] for r in failures)
    current_degraded = sorted(r["name"] for r in degraded)
    try:
        if alert_state_file.exists():
            last = json.loads(alert_state_file.read_text(encoding="utf-8"))
            if (last.get("failures") == current_names
                    and last.get("degraded", []) == current_degraded):
                print(f"  ⏭️ 告警与上次相同，不重复推送")
                return
    except Exception:
        pass

    try:
        from services.wxwork_push import send_text
        parts = []
        if failures:
            parts.append(f"{len(failures)} 个异常")
        if degraded:
            parts.append(f"{len(degraded)} 个降级")
        msg = f"⚠️ 数据源巡检（{' / '.join(parts)}）\n\n"
        for r in failures:
            msg += f"{r['status']} [{r['source']}] {r['name']}: {r['detail']}\n"
        for r in degraded:
            # ⚠️ 不是 ❌：降级不是错误，不计入 error_logs_24h
            msg += f"⚠️ 降级 [{r['source']}] {r['name']}: {r['detail']}\n"
        msg += f"\n✅ 正常：{total - len(failures) - len(degraded)} 个"
        msg += f"\n\n降级方案已自动激活，AI 分析不受影响"
        send_text(msg)
        print(f"  📤 企微告警已推送")

        # 记录本次推送内容
        (DATA_DIR / "health").mkdir(parents=True, exist_ok=True)
        alert_state_file.write_text(json.dumps({
            "failures": current_names,
            "degraded": current_degraded,
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
            # FIX 2026-09-22: 降级数单独落盘。此前降级项落在 ok 里，
            # 于是「主源已挂、走兜底价」在巡检 JSON 里显示为健康 —— 隐形故障。
            # 三者互斥且之和 = total：ok + failed + degraded + ⏭️跳过 = total。
            "degraded": sum(1 for r in results if r.get("degraded")),
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
    # FIX 2026-09-22: 降级项单独统计（⏭️ 跳过不算、❌ 失败不算，只算 ⚠️）。
    # 它们此前被混进 ✅ 里 —— 主源已经挂了，巡检却显示健康，等于隐形。
    degraded = [r for r in results if r.get("degraded")]
    # ⚠️ 口径说明（已拍板不改，改动前务必读完）：
    #   1. 这里的「正常」语义是**非异常**，而不是「status == ✅」。
    #      它**包含 ⏭️ 跳过项**（如外汇在非交易时段无报价、交易时段专属项
    #      在非盘中跳过）—— 跳过不是故障，把它算进「正常」是对的。
    #   2. 计入跳过项才能让「正常 + 异常 + 降级 = 总数」这个等式闭合；
    #      改成只数 `status == "✅"` 会让总数对不上，且非交易时段的巡检
    #      会出现「14 项里只有 3 项正常」这种吓人又无意义的数字。
    #   3. 下一行的打印文案**不许改**：`ops_summary` 靠「✅ 正常: N」这行
    #      解析巡检结果（并据此做汇总行回声抑制），改文案会直接打破解析。
    ok_count = len(results) - len(all_failures) - len(degraded)
    print(f"\n{'='*40}")
    print(f"  ✅ 正常: {ok_count}    ❌ 异常: {len(all_failures)}（其中需告警: {len(failures)}）")
    if degraded:
        # 单独一行，不混进 ✅ 正常里
        print(f"  ⚠️ 降级: {len(degraded)}（有值但来自兜底源，主源已不可用，见 _DEGRADED_STATUS）")
        for r in degraded:
            print(f"     - [{r['source']}] {r['name']}: {r['detail']}")
    if len(failures) < len(all_failures):
        print("  ℹ️ 以下非关键项失败（critical=False，不推送告警）：")
        for r in all_failures:
            if not r.get("critical", True):
                print(f"     - [{r['source']}] {r['name']}: {r['detail']}")

    # 有异常或降级 → 推企微（去重；降级走 ⚠️ 通道，不写 ❌，不计入错误数）
    if failures or degraded:
        _push_alert(failures, len(results), degraded)
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
