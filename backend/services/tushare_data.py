"""
钱袋子 — Tushare Pro 数据层
独立 service，提供稳定的 PE/PB/财务/北向资金/SHIBOR 数据

数据源：Tushare Pro API（5000 积分）
接口：daily_basic + fina_indicator + daily + moneyflow_hsgt + shibor 等
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "tushare_data",
    "scope": "public",
    "input": [],
    "output": "tushare_data",
    "cost": "cpu",
    "tags": ['Tushare', 'PE', 'PB', '财务', '北向资金', 'SHIBOR'],
    "description": "Tushare Pro数据层(5000积分)：PE/PB/财务/北向资金/SHIBOR",
    "layer": "data",
    "priority": 1,
}
import os
import math
import time
import json
import urllib.request
from datetime import datetime
from pathlib import Path
from infra.cache import MemoryCache

try:
    import fcntl  # POSIX 专有；Linux/macOS 均可用（生产是 Linux）
    _FCNTL_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows 兜底，不应在生产出现
    fcntl = None
    _FCNTL_AVAILABLE = False


def _get_token() -> str:
    """实时读取 Token（避免 import 时缓存空值）"""
    return os.getenv("TUSHARE_TOKEN", "")


def is_configured() -> bool:
    return bool(_get_token())


_TUSHARE_URL = "http://api.tushare.pro"

# 缓存
_TS_CACHE_TTL = 3600  # 1 小时
_ts_cache = MemoryCache(default_ttl=_TS_CACHE_TTL)


# ============================================================
# report_rc 跨进程共享每日额度计数器
# ============================================================
# FIX 2026-09: report_rc 是 Tushare 账号级硬限额（10次/天），但调用入口分散在
# 多个独立进程：主 FastAPI 进程（api/broker.py 用户实时请求）、
# scripts/cache_warmer.py（交易时段 crontab 每30分钟一次）、
# scripts/night_worker.py（多处调用）、scripts/broker_rating_cron.py（周日）、
# services/recommend_engine.py（自己的SQLite日缓存）。这些进程各自维护自己的
# 进程内 MemoryCache，互不通信，导致同一份"每日10次"硬限额被多个进程分别
# 消耗——交易时段 cache_warmer 每30分钟一次的任务就足以在早盘把限额打穿，
# 之后所有调用方都会撞上 Tushare 的"频率超限"报错，被动降级到 AKShare
# （无评级字段的数据源），却在展示层被误读为"数据源天生没有评级"。
#
# 修复思路：在真正发起 report_rc 请求前，先用一个落盘、跨进程共享的计数器
# 文件（DATA_DIR/_cache/tushare_quota.json）检查今日已用次数，额度耗尽直接
# 返回空列表走降级路径，而不是浪费一次真实请求、等 Tushare 报错了才降级。
# 用 fcntl.flock 排他锁保证多进程并发自增时不发生"读-改-写"竞态（与
# services/persistence.py::user_write_lock 用的是同一种跨进程锁模式）。
#
# 只对 report_rc 这一个 api_name 做这个限流，其余 Tushare 接口（5000积分档，
# 没有这个每日10次的硬限额）不受影响。
REPORT_RC_DAILY_LIMIT = int(os.getenv("TUSHARE_REPORT_RC_DAILY_LIMIT", "10"))
_QUOTA_FILE_NAME = "tushare_quota.json"


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _quota_file_path() -> Path:
    """今日额度计数器落盘路径：DATA_DIR/_cache/tushare_quota.json"""
    from config import DATA_DIR
    quota_dir = Path(DATA_DIR) / "_cache"
    quota_dir.mkdir(parents=True, exist_ok=True)
    return quota_dir / _QUOTA_FILE_NAME


def _parse_quota_state(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def get_report_rc_quota_status() -> dict:
    """只读查询今日 report_rc 已用/剩余额度，不消耗额度。

    供上层（如 services/broker_research.py）判断"当前展示的是否为
    限额耗尽后的降级数据"，而不是"数据源本身没有评级字段"。
    """
    today = _today_str()
    fp = _quota_file_path()
    try:
        raw = fp.read_bytes() if fp.exists() else b""
    except Exception:
        raw = b""
    state = _parse_quota_state(raw)
    used = state.get("used", 0) if state.get("date") == today else 0
    return {
        "date": today,
        "used": used,
        "limit": REPORT_RC_DAILY_LIMIT,
        "remaining": max(0, REPORT_RC_DAILY_LIMIT - used),
        "exhausted": used >= REPORT_RC_DAILY_LIMIT,
    }


def _consume_report_rc_quota() -> tuple:
    """跨进程原子地尝试消耗一次 report_rc 今日额度。

    Returns:
        (ok, used_after)。ok=True 表示本次调用被允许发起真实请求；
        ok=False 表示今日额度已耗尽，调用方应直接走降级路径。
    """
    today = _today_str()
    fp = _quota_file_path()

    if not _FCNTL_AVAILABLE:  # pragma: no cover - Windows 兜底
        state = _parse_quota_state(fp.read_bytes() if fp.exists() else b"")
        used = state.get("used", 0) if state.get("date") == today else 0
        if used >= REPORT_RC_DAILY_LIMIT:
            return False, used
        used += 1
        fp.write_text(json.dumps({"date": today, "used": used}), encoding="utf-8")
        return True, used

    fd = os.open(str(fp), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # 排他锁：跨进程串行化"读-判断-写"
        try:
            raw = os.read(fd, 65536)
            state = _parse_quota_state(raw)
            used = state.get("used", 0) if state.get("date") == today else 0
            if used >= REPORT_RC_DAILY_LIMIT:
                return False, used
            used += 1
            new_raw = json.dumps({"date": today, "used": used}).encode("utf-8")
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, new_raw)
            return True, used
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _call_tushare(api_name: str, params: dict, fields: str = "") -> list:
    """统一 Tushare API 调用"""
    token = _get_token()
    if not token:
        return []

    cache_key = f"{api_name}_{json.dumps(params, sort_keys=True)}_{fields}"
    now = time.time()
    cached = _ts_cache.get(cache_key)
    if cached is not None:
        return cached

    # report_rc 跨进程共享限额检查（见模块级注释）——只有真正需要发起
    # 新请求时（未命中上面的进程内缓存）才消耗额度，缓存命中不计入消耗。
    if api_name == "report_rc":
        ok, used = _consume_report_rc_quota()
        if not ok:
            print(f"[TUSHARE] report_rc 今日额度已耗尽({used}/{REPORT_RC_DAILY_LIMIT})，跳过真实请求直接降级")
            return []

    try:
        payload = json.dumps({
            "api_name": api_name,
            "token": token,
            "params": params,
            "fields": fields,
        }).encode("utf-8")
        req = urllib.request.Request(
            _TUSHARE_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())

        if resp.get("data") and resp["data"].get("items"):
            # 转换为 dict 列表
            columns = resp["data"]["fields"]
            items = resp["data"]["items"]
            result = [dict(zip(columns, row)) for row in items]
            _ts_cache.set(cache_key, result, ttl=_TS_CACHE_TTL)
            return result
        return []
    except Exception as e:
        print(f"[TUSHARE] {api_name} failed: {e}")
        return []


def _code_to_ts(code: str) -> str:
    """股票代码转 Tushare 格式（600519 → 600519.SH）"""
    code = code.strip()
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


# ============================================================
# 1. 估值数据（PE/PB/总市值/股息率）
# ============================================================

def get_valuation(code: str) -> dict:
    """获取最新估值数据"""
    ts_code = _code_to_ts(code)
    rows = _call_tushare(
        "daily_basic",
        {"ts_code": ts_code, "limit": 1},
        "ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv,turnover_rate",
    )
    if not rows:
        return {"available": False}

    r = rows[0]
    return {
        "available": True,
        "code": code,
        "pe_ttm": r.get("pe_ttm"),
        "pb": r.get("pb"),
        "ps_ttm": r.get("ps_ttm"),
        "dividend_yield": r.get("dv_ttm"),
        "total_mv": round(r["total_mv"] / 10000, 2) if r.get("total_mv") else None,  # 万元→亿元
        "circ_mv": round(r["circ_mv"] / 10000, 2) if r.get("circ_mv") else None,
        "turnover_rate": r.get("turnover_rate"),
        "trade_date": r.get("trade_date"),
        "source": "tushare",
    }


# ============================================================
# 2. 财务指标（ROE/毛利率/净利率/负债率/现金流/EPS/营收增速）
# ============================================================

def get_financials(code: str) -> dict:
    """获取核心财务指标（最近一期）"""
    ts_code = _code_to_ts(code)
    rows = _call_tushare(
        "fina_indicator",
        {"ts_code": ts_code, "limit": 1},
        "ts_code,ann_date,end_date,roe,roe_waa,grossprofit_margin,netprofit_margin,"
        "debt_to_assets,ocfps,eps,revenue_ps,profit_to_gr,"
        "netprofit_yoy,or_yoy,equity_yoy,currentratio",
    )
    if not rows:
        return {"available": False, "source": "tushare"}

    r = rows[0]
    return {
        "available": True,
        "code": code,
        "roe": r.get("roe") or r.get("roe_waa"),
        "eps": r.get("eps"),
        "gross_margin": r.get("grossprofit_margin"),
        "net_margin": r.get("netprofit_margin"),
        "debt_ratio": r.get("debt_to_assets"),
        "cash_flow_per_share": r.get("ocfps"),
        "netprofit_yoy": r.get("netprofit_yoy"),
        "revenue_yoy": r.get("or_yoy"),
        "current_ratio": r.get("currentratio"),
        "profit_to_revenue": r.get("profit_to_gr"),
        "ann_date": r.get("ann_date"),
        "end_date": r.get("end_date"),
        "source": "tushare",
    }


# ============================================================
# 3. 批量估值（选股用，一次拉多只）
# ============================================================

def get_valuation_batch(trade_date: str = "") -> list:
    """批量获取全市场估值数据（选股用）
    trade_date: YYYYMMDD 格式，空则取最近交易日
    """
    params = {"limit": 5000}
    if trade_date:
        params["trade_date"] = trade_date

    rows = _call_tushare(
        "daily_basic",
        params,
        "ts_code,trade_date,pe_ttm,pb,dv_ttm,total_mv,turnover_rate",
    )
    return rows


# ============================================================
# 4. 历史估值（回测用）
# ============================================================

def get_valuation_history(code: str, start_date: str = "", end_date: str = "") -> list:
    """获取历史估值序列（PE/PB 时序数据）"""
    ts_code = _code_to_ts(code)
    params = {"ts_code": ts_code}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    rows = _call_tushare(
        "daily_basic",
        params,
        "ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate",
    )
    return rows


# ============================================================
# 5. 大股东增减持（signal_scout P0 数据源）
# ============================================================

def get_holder_trades(start_date: str = "", end_date: str = "") -> list:
    """获取近期大股东增减持记录

    ⚠️ 字段口径（2026-09-04 服务器实测：30 天窗口 1333 行、356 只票，
    不带 fields 参数调用以拿到接口真实返回的字段）：
        真实返回 = ['ts_code', 'ann_date', 'holder_name', 'holder_type',
                    'in_de', 'change_vol', 'change_ratio', 'after_share',
                    'after_ratio', 'avg_price', 'total_share']

    三条必须记住的事实（违反任意一条都会复现历史 bug）：

      1. **方向字段是 `in_de`**，取值 'IN'（增持）/ 'DE'（减持）。
         全窗口实测分布：**DE = 1056 行、IN = 277 行**（约 79% : 21%），
         空值 0 行。

      2. **没有 `change_type`，也没有 `change_amount`**。这两个字段曾被写进
         请求列表，但接口不返回。更危险的是：**请求不存在的字段会被静默
         丢弃** —— 实测请求 9 个字段只回来 7 个，不报错、不告警，于是消费方
         `.get('change_type')` 恒拿到 None。原代码
         `"增持" if t.get("change_type") == "增持" else "减持"`
         因此把 **277 条真实增持（20.8%）全部报成减持**，且 level 跟着
         action 走，这批信号的 level 也一起错了。
         这也是本函数曾经把 `change_amount` 写进请求列表却从未察觉的原因。

      3. `change_vol` 单位是【股】，不是万股（实测 4016100.0 = 401.61 万股）。

    ⚠️ 本函数只取数，不做方向/单位翻译 —— 翻译在 signal_scout.
    _collect_holder_changes()，那里的 docstring 记了消费侧口径。改动字段
    列表时必须同步改那边，否则又会静默拿不到值。
    """
    from datetime import datetime, timedelta
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    if not start_date:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    rows = _call_tushare(
        "stk_holdertrade",
        {"start_date": start_date, "end_date": end_date},
        "ts_code,ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,after_share,after_ratio",
    )
    return rows[:50]


# ============================================================
# 6. 股权质押统计（风控因子）
# ============================================================

def get_pledge_stat(code: str = "") -> list:
    """获取股权质押统计
    code: 空=全市场最新, 有值=单只个股
    """
    params = {}
    if code:
        params["ts_code"] = _code_to_ts(code)

    rows = _call_tushare(
        "pledge_stat",
        params,
        "ts_code,end_date,pledge_count,unrest_pledge,rest_pledge,total_share,pledge_ratio",
    )
    return rows[:100]


# ============================================================
# 7. 限售股解禁（signal_scout P0 数据源）
# ============================================================

# ---- share_float 分页取数的硬上限 ----
#
# _SHARE_FLOAT_PAGE_SIZE：Tushare **单次返回行数硬上限**，实测传
#   limit=8000 / 20000 都只返回 6000 行，所以按 6000 分页即可（传更大的
#   limit 没有意义，反而让人误以为一次就能取完）。
# _SHARE_FLOAT_MAX_PAGES / _SHARE_FLOAT_MAX_ROWS：防死循环的双保险。
#   offset 翻页只有在"单页取不满"时才能确认取完，万一上游数据异常（或
#   Tushare 改版让 offset 失效、每页都返回满页）就会无限翻下去 —— 用这两个
#   上限把它钉死。实测 30 天窗口 41840 行 = 7 页，6 万行（10 页）留了约
#   1.4 倍余量，20 页是行数上限之上的又一道保险；任一命中都会打印告警
#   并把 complete 置为 False，绝不假装数据完整。
_SHARE_FLOAT_PAGE_SIZE = 6000
_SHARE_FLOAT_MAX_PAGES = 20
_SHARE_FLOAT_MAX_ROWS = 60000

# ---- share_float 请求字段 / 去重键 ----
#
# _SHARE_FLOAT_FIELDS 比 _SHARE_FLOAT_DEDUPE_FIELDS **多一个 ann_date**，
# 这不是笔误，是刻意的，详见 _dedupe_share_float_rows() 的说明。这里先给结论：
# 接口实际返回 7 个字段，早期版本只请求 6 个（漏了 ann_date），于是把
# "同一笔解禁的两次公告"误判成"两笔解禁"，合计值被放大约 2 倍。
_SHARE_FLOAT_FIELDS = "ts_code,ann_date,float_date,float_share,float_ratio,holder_name,share_type"
_SHARE_FLOAT_DEDUPE_FIELDS = (
    "ts_code", "float_date", "float_share", "float_ratio", "holder_name", "share_type",
)


def _dedupe_share_float_rows(rows: list) -> list:
    """按"解禁事件"去重：同一笔解禁的多次公告只算一次

    ⚠️ 背景（2026-09-04 全窗口实测，41840 行）：share_float 会把**同一笔解禁
    登记两次** —— 一次是原始公告（IPO / 股权激励授予时），一次是解禁前的
    **提示性公告**。两行的 ts_code / float_date / float_share / float_ratio /
    holder_name / share_type **六个字段完全相同**，只有 `ann_date`（公告日期）
    不同。实例：

        301507.SZ 杭州民生药业 238000000 股 float_date=20260907 首发原始股
            ann_date=20230904   ← IPO 时的原始公告
            ann_date=20260831   ← 解禁前 7 天的提示性公告
        600298.SH 高路         30000 股 float_date=20260909 股权激励限售流通
            ann_date=20240911   ← 授予时的原始公告
            ann_date=20260904   ← 解禁前 5 天的提示性公告

    不去重的后果：合计 float_share / float_ratio 被放大约 2 倍，出现
    "解禁 138% 总股本"这种物理上不可能的数字（301507.SZ 138.00% → 69.00%），
    并且会改变"按影响排序"的 Top-N 结果。

    ⚠️ 为什么去重键**不包含 ann_date**（重要，改这里前先读完）：
    ann_date 是"这条公告记录是哪天发的"，不是"这笔解禁"的组成部分 —— 它区分
    的是**公告**，不是**解禁事件**。把它加进去重键会让去重完全失效（实测
    9004 组重复行里，9004 组的 ann_date 都不同，0 组相同），合计值重新变回
    2 倍。所以 _SHARE_FLOAT_DEDUPE_FIELDS 刻意比 _SHARE_FLOAT_FIELDS 少一个
    ann_date；ann_date 仍然请求下来，是为了让这个判断在代码里可核对。

    ⚠️ 误伤风险已量化评估（2026-09-04）：
      * 全窗口 41840 行中，7 字段（含 ann_date）唯一数 = 41840，**0 条真重复**
        → 不存在"完全一样的记录被登记多次"的情况，去重删掉的每一行都确实
        是"另一次公告"，不是独立事件。
      * 重复组大小**恒为 2**（9004 组全部是 2，没有 3 及以上）→ 与"原始公告
        + 提示性公告 = 2 次"完全吻合，没有把不同事件错误合并。
      * 最大的一只票 001257.SZ（9559 行）中，6 字段重复 **0 条**，连
        "同名同股数"的碰撞都是 0 → 去重对它零影响。

    Args:
        rows: 翻页拼合后的原始行。

    Returns:
        去重后的行（保持原顺序，保留每组第一次出现的那行）。
    """
    seen: set = set()
    deduped: list = []
    for row in rows:
        key = tuple(str(row.get(field, "") or "") for field in _SHARE_FLOAT_DEDUPE_FIELDS)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def get_upcoming_unlocks(days: int = 30, limit: int = 30) -> list:
    """获取未来N天的限售股解禁计划（已按 (ts_code, float_date) 聚合）

    ⚠️ 口径修正 1（聚合）：Tushare share_float 是**按股东逐行**返回的 —— 同一
    只股票同一天的解禁会拆成多行，每行一个股东。原实现直接取单行 float_ratio，
    把"某个股东的一笔"当成了"这只票当天的解禁总量"，后果有两个：
      1. 严重低估真实冲击（实例：301563.SZ 在 2026-09-30 当天 32 行合计
         float_ratio = 41.09%，最大的单行只有 7.68%）；
      2. 同一只股票同一天解禁会产生多条重复信号。

    ⚠️ 口径修正 2（float_ratio 的分母 = 总股本，不是流通股本）：
    Tushare 的 float_ratio 分母是【总股本】。想要"占流通盘"必须自己拿
    daily_basic.float_share 再除一次 —— 两个数都真实，但差很多：
        301563.SZ（2026-09-30，实测）：
            占总股本 = float_share / daily_basic.total_share
                     = 34,782,667.4 / 84,650,928 = 41.09%   ← 本函数返回的值
            占流通盘 = float_share / daily_basic.float_share
                     = 34,782,667.4 / 19,135,696 = 181.77%  ← 需要另外算
        920222.BJ（2026-09-30，实测）：
            占总股本 = 10,581,305 / 85,647,100 = 12.35%     ← 本函数返回的值
            占流通盘 = 10,581,305 / 11,562,590 = 91.51%
    本函数返回的是**占总股本**（Tushare 原口径，可直接与 Tushare 的 float_ratio
    对账），不做换算 —— 换算交给展示层，并要在文案里写清是哪个口径。

    ⚠️ 口径修正 3（float_share 的单位 = 股，不是万股）：
    上面两个算式里的 34,782,667.4 就是 float_share 的**原始值**，单位是【股】
    （= 3,478.27 万股）。展示层要换算成万股必须自己除 10000。

    现在改为：先按 (ts_code, float_date) 聚合（float_share / float_ratio
    求和，记录股东数 holder_count 与股东名 holder_names），**再**按合计
    比例降序排序，**最后**才截断 Top limit —— 顺序不能反，否则排序算法
    看到的是残缺分母，且同一只票的多笔解禁会被截断拆散。

    ⚠️ 取数修正（单次 6000 行截断，见 _fetch_share_float_rows）：窗口内行数
    常常远超 6000 行（实测 30 天窗口共 4 万余行，其中 001257.SZ 一只票就占
    5964 行），单次调用只能拿回前 6000 行 → 聚合后只剩 4 只票，其余 160 只
    被静默丢掉，其中不乏解禁比例远高于前者的标的。实测对比（同一窗口）：
        单次调用（6000 行）Top1 = 301563.SZ 41.09%
        翻页取全 + 去重    Top5 = 600925.SH 76.69% / 920100.BJ 73.49%
                                 301558.SZ 72.14% / 920015.BJ 71.22%
                                 301507.SZ 69.00%
    即：单页 Top1 的 41.09% 在完整数据里连前五都进不去 —— 按"影响最大优先"
    排序却先丢了影响最大的，这个信号就失去意义了。现在改成 offset 翻页取全。
    取不全时本函数会打印告警，调用方可用 get_upcoming_unlocks_with_meta()
    拿到 complete 标记，不要假装数据完整。

    去重：Tushare 把同一笔解禁登记两次（原始公告 + 解禁前提示性公告），
    会在翻页取全的基础上再按"解禁事件"去重一次，否则合计值放大约 2 倍
    （301507.SZ 不去重是 138.00%，去重后 69.00%）。详见
    _dedupe_share_float_rows()。这两个问题是**独立的**：翻页解决覆盖度，
    去重解决绝对值精度，任何一层缺失都会得到错的数字。

    容错：Tushare 返回值类型不稳定（None / "" / "1.23" / 123 都出现过），
    聚合统一走 _finite_float() 安全转换，脏值（含 inf / nan）按 0 累加，
    绝不抛异常。

    Args:
        days: 向前看多少天，默认 30。
        limit: 聚合排序后返回的条数上限，默认 30。

    Returns:
        list[dict]，每条:
        {
            "ts_code": "301563.SZ",
            "float_date": "20260930",
            "float_share": 34782667.4, # 当日合计解禁股数（单位【股】！
                                       #  = 3,478.27 万股，展示层自行换算）
            "float_ratio": 41.09,      # 当日合计占【总股本】比例（%）
                                       #  （占流通盘 = 181.77%，需另算）
            "holder_count": 32,        # 去重后的股东数 = len(holder_names)
            "holder_names": ["A", "B"], # 去重后的股东名称
        }

        注：holder_count 与 holder_names **同口径**（都是去重后的名字数），
        不按行数计数 —— 同一股东持有多笔不同类型的限售股会占多行。
        全部行都没有股东名时 holder_count = 0，调用方应降级为不显示该字段。
    """
    items, _meta = get_upcoming_unlocks_with_meta(days=days, limit=limit)
    return items


def _fetch_share_float_rows(start_date: str, end_date: str) -> tuple:
    """分页拉全 share_float 在 [start_date, end_date] 窗口内的所有行

    ⚠️ 为什么必须翻页（实测，2026-09-04 ~ 2026-10-04 窗口）：
      * Tushare 单次调用**硬上限 6000 行**：limit 传 8000 / 20000 实测仍只
        返回 6000 行，响应里的 has_more=True、count=0（count 恒为 0，不可用）。
      * 该窗口真实共 41840 行、164 只票；单次调用只拿回 6000 行 → 聚合只剩
        4 只票（301563 / 920222 / 001257 / 603683），**其余 160 只被静默丢掉**。
        001257.SZ 一只票就独占 5964 行（5964 个基金股东），几乎吃满一整页。
      * 更糟的是丢掉的恰恰是更重要的：单页 Top1 = 301563.SZ 41.09%，
        翻页取全 + 去重后的 Top1 = 600925.SH 76.69%（41.09% 连前五都进不去，
        见 get_upcoming_unlocks() 里的完整对比）。按"影响最大优先"排序却先
        丢了影响最大的，这个信号就失去意义了。
      * 注：翻页取全**未去重**时的 Top1 是 301507.SZ 138.00%（超过总股本，
        物理上不可能），去重后回到 69.00% —— 两者是独立的两层修正，见下。

    为什么用 offset 而不是"按日切分"：按日切分**解决不了** —— 实测
    20260907 / 20260928 / 20260930 三天各自的行数都 ≥ 6000（同样被截断），
    所以日级窗口也必须翻页。

    offset 翻页的稳定性已实测验证（同一窗口、同一秒内）：
      * call(offset=24000, limit=6000) 与 call(offset=24500, limit=6000) 的
        B[:5500] == A[500:] 完全一致 → 服务端分页游标稳定，不会跳行/重行；
      * 同一 offset 重复调用结果完全一致 → 翻页**不会**制造重复行。

    翻页取全后还会再做一层**按解禁事件去重**（_dedupe_share_float_rows）：
    Tushare 把同一笔解禁登记两次（原始公告 + 解禁前提示性公告），不去重会让
    合计值放大约 2 倍。去重键刻意**不含 ann_date**，原因见那个函数的 docstring。

    防死循环：三重硬上限，任一命中即停止并回报 complete=False
      * 最多 _SHARE_FLOAT_MAX_PAGES 页；
      * 累计行数不超过 _SHARE_FLOAT_MAX_ROWS；
      * 单页返回行数 < page_size ⇒ 已经是最后一页，正常结束（complete=True）。

    Args:
        start_date: 起始日期 YYYYMMDD。
        end_date: 结束日期 YYYYMMDD。

    Returns:
        (rows, meta)，rows 已去重；meta = {
            "complete": bool,          # False = 被上限截断，数据不完整
            "pages": int,              # 实际发起的调用次数
            "rows": int,               # 翻页取回的原始行数（去重前）
            "duplicate_rows": int,     # 被去重删掉的行（同一事件的多次公告）
            "rows_used": int,          # 去重后实际参与聚合的行数
            "truncated_reason": str,   # 不完整时的原因，完整时为 ""
        }
    """
    fields = _SHARE_FLOAT_FIELDS

    rows: list = []
    complete = False
    truncated_reason = ""
    pages = 0

    for page_index in range(_SHARE_FLOAT_MAX_PAGES):
        page = _call_tushare(
            "share_float",
            {
                "start_date": start_date,
                "end_date": end_date,
                "offset": page_index * _SHARE_FLOAT_PAGE_SIZE,
                "limit": _SHARE_FLOAT_PAGE_SIZE,
            },
            fields,
        )
        pages = page_index + 1
        rows.extend(page or [])

        # 取不满一页 ⇒ 这就是最后一页（唯一"真的取完了"的判定条件）
        if len(page or []) < _SHARE_FLOAT_PAGE_SIZE:
            complete = True
            break
        if len(rows) >= _SHARE_FLOAT_MAX_ROWS:
            truncated_reason = f"累计行数达到上限 {_SHARE_FLOAT_MAX_ROWS}"
            break
    else:
        truncated_reason = f"翻页次数达到上限 {_SHARE_FLOAT_MAX_PAGES}"

    # 去重必须在翻页拼合**之后**做：同一笔解禁的两次公告可能被分页边界切开，
    # 逐页去重会漏掉跨页的那一半。
    deduped = _dedupe_share_float_rows(rows)
    duplicate_rows = len(rows) - len(deduped)

    meta = {
        "complete": complete,
        "pages": pages,
        "rows": len(rows),
        "duplicate_rows": duplicate_rows,
        "rows_used": len(deduped),
        "truncated_reason": truncated_reason,
    }
    if not complete:
        print(
            f"[TUSHARE] share_float 未取全：{truncated_reason}"
            f"（{start_date}~{end_date}，已取 {len(rows)} 行 / {pages} 页）"
            f" → 解禁聚合结果不完整，勿当作全市场快照使用"
        )
    return deduped, meta


def get_upcoming_unlocks_with_meta(days: int = 30, limit: int = 30) -> tuple:
    """get_upcoming_unlocks() 的带元信息版本：额外回报取数是否完整

    返回 (items, meta)。items 与 get_upcoming_unlocks() 的返回值完全一致；
    meta 见 _fetch_share_float_rows()，另加 "groups"（聚合后的 (ts_code,
    float_date) 组数，即截断前的候选条数）。complete=False 表示被 6000 行
    / 翻页上限截断，排序结果的"Top N"不再可信。
    另见 meta["duplicate_rows"] —— 同一笔解禁被多次公告而删掉的行数，
    正常量级是总行数的 20% 上下；若骤降到 0，说明上游数据结构变了，
    去重可能失效，需要重新核对 _dedupe_share_float_rows() 的取证结论。
    """
    from datetime import datetime, timedelta
    start = datetime.now().strftime("%Y%m%d")
    end = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")

    rows, meta = _fetch_share_float_rows(start, end)
    if not rows:
        meta["groups"] = 0  # 与下面的非空路径保持同样的 key 集合，避免调用方 KeyError
        return [], meta

    # 1) 先聚合（顺序关键：聚合 → 排序 → 截断）
    grouped: dict = {}
    for row in rows:
        ts_code = str(row.get("ts_code") or "").strip()
        float_date = str(row.get("float_date") or "").strip()
        key = (ts_code, float_date)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "ts_code": ts_code,
                "float_date": float_date,
                "float_share": 0.0,
                "float_ratio": 0.0,
                "holder_count": 0,
                "holder_names": [],
            }
            grouped[key] = bucket

        # 用 _finite_float 而非 _to_float：inf 一旦进入求和会污染整个合计值
        # （x + inf = inf），且后续 int(inf) 会抛 OverflowError。非有限值
        # 与 "abc" 一样属于脏值，按 0 累加。
        bucket["float_share"] += _finite_float(row.get("float_share"), 0.0)
        bucket["float_ratio"] += _finite_float(row.get("float_ratio"), 0.0)
        holder_name = str(row.get("holder_name") or "").strip()
        if holder_name and holder_name not in bucket["holder_names"]:
            bucket["holder_names"].append(holder_name)

    # holder_count 与 holder_names 必须同口径 = **去重后的股东名数量**。
    # 不能按行数累加：同一股东持有多笔不同类型的限售股时会占多行，
    # 于是算出"涉及 2 个股东"但只列得出 1 个名字，文案自相矛盾。
    # 全部行都没有股东名时 = 0，由调用方降级为不显示该字段。
    for bucket in grouped.values():
        bucket["holder_count"] = len(bucket["holder_names"])

    # 2) 排序 3) 截断
    merged = sorted(grouped.values(), key=lambda x: x["float_ratio"], reverse=True)
    meta["groups"] = len(merged)
    return merged[:limit], meta


# ============================================================
# 8. 分红送转（价值因子增强）
# ============================================================

def get_dividend(code: str) -> list:
    """获取个股分红送转记录"""
    ts_code = _code_to_ts(code)
    rows = _call_tushare(
        "dividend",
        {"ts_code": ts_code},
        "ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,record_date,ex_date,pay_date",
    )
    return rows[:10]


# ============================================================
# 9. ST/*ST 标记（风控排除 + 选股过滤）
# ============================================================

def get_st_stocks() -> list:
    """获取当前所有 ST/*ST 股票列表"""
    cache_key = "st_stocks"
    cached = _ts_cache.get(cache_key)
    if cached is not None:
        return cached

    rows = _call_tushare(
        "namechange",
        {"limit": 200},
        "ts_code,name,start_date,end_date,change_reason",
    )
    # 筛选当前仍为 ST 的
    st_list = [r for r in rows if r.get("name", "").startswith(("ST", "*ST")) and not r.get("end_date")]
    _ts_cache.set(cache_key, st_list, ttl=86400)
    return st_list


def is_st(code: str) -> bool:
    """判断个股是否为 ST"""
    ts_code = _code_to_ts(code)
    return any(s.get("ts_code") == ts_code for s in get_st_stocks())


# ============================================================
# 10. 公告全文摘要（signal_scout 信号源）
# ============================================================

def get_announcements(code: str = "", start_date: str = "", end_date: str = "", limit: int = 20) -> list:
    """获取公告列表"""
    from datetime import datetime, timedelta
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    params = {"start_date": start_date, "end_date": end_date}
    if code:
        params["ts_code"] = _code_to_ts(code)

    rows = _call_tushare(
        "anns",
        params,
        "ts_code,ann_date,title,content,url",
    )
    return rows[:limit]


# ============================================================
# 11. 研报摘要（signal_scout 信号源）
# ============================================================

def get_research_reports(code: str = "", limit: int = 10) -> list:
    """获取最新研报"""
    params = {"limit": limit}
    if code:
        params["ts_code"] = _code_to_ts(code)

    rows = _call_tushare(
        "report_rc",
        params,
        "ts_code,report_date,report_title,author,org_name,rating,abstract",
    )
    return rows


# ============================================================
# 12. 北向资金流向（moneyflow_hsgt — 替代 AKShare 断层数据）
# ============================================================

NORTH_NET_FLOW_UNAVAILABLE_REASON = (
    "2024-08-19 起沪深交易所停止披露北向日频净买入，改为按季度公布；"
    "Tushare moneyflow_hsgt 的 north_money/hgt/sgt 现为当日成交额"
)


def _north_unavailable_result(source: str = "tushare") -> dict:
    """构造「北向数据完全不可得」的返回骨架（成交额也没拿到）

    净流入维度永久不可得（口径变更），所以 net_flow_available 恒为 False；
    available=False 表示连成交额都没拿到 → 下游应报「数据源故障」。
    """
    return {
        # ── 净流入：数据源已不提供 ──
        "net_flow_today": None,
        "net_flow_5d": None,
        "net_flow_20d": None,
        "net_flow_available": False,
        "unavailable_reason": NORTH_NET_FLOW_UNAVAILABLE_REASON,
        "trend": "数据不可得",
        # ── 成交额：本次也没拿到 ──
        "turnover_today": None,
        "turnover_avg_5d": None,
        "turnover_avg_20d": None,
        "turnover_ratio_5d_vs_20d": None,
        "turnover_trend": "数据不可得",
        "daily_turnover": [],
        # ── 元信息 ──
        "available": False,
        "source": source,
        "data_date": "",
        "turnover_5d_range": "",
    }


def _turnover_trend_label(ratio) -> str:
    """依据 5日均量 vs 20日均量 的相对变化给出放量/缩量标签"""
    if ratio is None:
        return "数据不可得"
    if ratio > 0.20:
        return "显著放量"
    if ratio > 0.08:
        return "温和放量"
    if ratio < -0.20:
        return "显著缩量"
    if ratio < -0.08:
        return "温和缩量"
    return "平稳"


def get_northbound_flow(days: int = 30) -> dict:
    """获取北向资金【成交额】（Tushare moneyflow_hsgt）

    ⚠️ 口径关键事实（2026-08 修正，请勿再改回差分逻辑）：
    自 **2024-08-19** 起，沪深交易所停止公布北向资金的每日净买入额，只保留
    每日**成交总额**（买入额+卖出额），净买入改为**按季度**公布。
    因此 Tushare moneyflow_hsgt 的 `north_money` / `hgt` / `sgt` 三个字段在
    该日之后填的是**当日成交额**（单位：百万元），而 **不是**历史累计净买入。

    实测佐证：north_money == hgt + sgt 精确成立，且数值恒为正、稳定在
    2500~3300 亿/日量级 —— 这是成交额的量级，净买入不可能长期单边如此。

    历史 Bug：旧实现把成交额当作「累计净买入」，对相邻两日做差分再求和。
    而「连续差分之和 = 末值 − 首值」，N 日累计会退化成首尾两天成交额之差
    （望远镜求和 telescoping sum）。5 日与 20 日窗口基准日不同，符号可以
    任意相反，导出的今日/5日/20日「净流入」以及 ±600 亿的日波动全是噪声。

    因此本函数**只返回成交额**（不做任何差分），净流入三个字段一律为 None，
    并用 net_flow_available=False 显式告知下游。

    Returns:
        dict: {
            # 净流入维度（数据源已不提供，恒为 None）
            "net_flow_today": None,
            "net_flow_5d": None,
            "net_flow_20d": None,
            "net_flow_available": False,   # 净流入维度是否可得
            "unavailable_reason": str,     # 净流入不可得的原因
            "trend": "数据不可得",         # 保留字段，固定值，不再出现「流入/流出」

            # 成交额维度（真实可得，单位：亿元）
            "turnover_today": float,            # 最新交易日成交额
            "turnover_avg_5d": float,           # 近5日【平均】成交额（非累计）
            "turnover_avg_20d": float,          # 近20日【平均】成交额
            "turnover_ratio_5d_vs_20d": float,  # (avg5d-avg20d)/avg20d
            "turnover_trend": str,              # 显著放量/温和放量/平稳/温和缩量/显著缩量
            "daily_turnover": [{"date": str, "turnover": float}],  # 最多30天

            # 元信息
            "available": bool,            # 整体可用性：成交额拿到即 True
            "source": "tushare",
            "data_date": str,
            "turnover_5d_range": str,     # 如 "8/24-8/28"
        }

    Note:
        `available` 与 `net_flow_available` 语义不同，下游必须区分：
        - `available=True`  表示这个数据源整体成功拿到了数据（成交额可用）；
        - `net_flow_available=False` 单独表示「净流入」这一个维度不可得。
        下游据此决定是**跳过净流入因子**（net_flow_available=False，属正常）
        还是**上报数据源故障**（available=False，属异常）。
    """
    from datetime import datetime, timedelta

    result = _north_unavailable_result("tushare")

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")  # 多取几天防节假日

    rows = _call_tushare(
        "moneyflow_hsgt",
        {"start_date": start_date, "end_date": end_date},
        "trade_date,ggt_ss,ggt_sz,hgt,sgt,north_money,south_money",
    )

    if not rows:
        print("[TUSHARE-NORTH] moneyflow_hsgt 返回空")
        return result

    # 按日期升序排列
    rows = sorted(rows, key=lambda x: x.get("trade_date", ""))

    # 逐日成交额：north_money 即当日北向成交总额（百万元）→ /100 得亿元
    # 缺失时降级为 hgt + sgt（两者之和恒等于 north_money，已实测核实）
    daily_turnover = []
    for row in rows:
        trade_date = row.get("trade_date", "")
        if not trade_date:
            continue

        nm = row.get("north_money")
        try:
            turnover_million = float(nm) if nm is not None else 0.0
        except (TypeError, ValueError):
            turnover_million = 0.0

        if turnover_million <= 0:
            # 降级：hgt + sgt
            try:
                hgt = float(row.get("hgt") or 0)
            except (TypeError, ValueError):
                hgt = 0.0
            try:
                sgt = float(row.get("sgt") or 0)
            except (TypeError, ValueError):
                sgt = 0.0
            turnover_million = hgt + sgt

        if turnover_million <= 0:
            continue  # 该日无有效成交额，跳过（不臆造 0）

        daily_turnover.append({
            "date": trade_date,
            "turnover": round(turnover_million / 100, 2),  # 百万元 → 亿元
        })

    if len(daily_turnover) < 5:
        # 数据不足：保持原有早退行为，但结构完整（含 net_flow_available/unavailable_reason）
        print(f"[TUSHARE-NORTH] 数据不足: {len(daily_turnover)}天")
        return result

    turnovers = [d["turnover"] for d in daily_turnover]
    avg_5d = round(sum(turnovers[-5:]) / len(turnovers[-5:]), 2)
    window_20 = turnovers[-20:]
    avg_20d = round(sum(window_20) / len(window_20), 2)

    result["turnover_today"] = turnovers[-1]
    result["turnover_avg_5d"] = avg_5d
    result["turnover_avg_20d"] = avg_20d
    result["turnover_ratio_5d_vs_20d"] = round((avg_5d - avg_20d) / avg_20d, 4) if avg_20d else None
    result["turnover_trend"] = _turnover_trend_label(result["turnover_ratio_5d_vs_20d"])
    result["daily_turnover"] = daily_turnover[-30:]  # 最多返回30天
    result["data_date"] = daily_turnover[-1]["date"]
    result["available"] = True  # 成交额真实可得 → 数据源整体可用

    # v9.5.130: 记录5日区间起止日期，供展示时标注（如"8/24-8/28"），
    # 避免用户误以为是连续5个日历日。语义已从净流入改为成交额，故改名 turnover_5d_range。
    def _short(d):
        return f"{int(d[4:6])}/{int(d[6:8])}" if d and len(d) == 8 else d

    result["turnover_5d_range"] = (
        f"{_short(daily_turnover[-5]['date'])}-{_short(daily_turnover[-1]['date'])}"
    )

    print(f"[TUSHARE-NORTH] date={result['data_date']}, "
          f"成交额 today={result['turnover_today']}亿, "
          f"avg5d={avg_5d}亿, avg20d={avg_20d}亿, "
          f"turnover_trend={result['turnover_trend']}（净流入维度不可得）")

    result["note"] = "口径:仅成交额可得；净买入自2024-08-19起交易所改为季度披露"

    return result


# ============================================================
# 13. SHIBOR 利率（替代 AKShare rate_interbank）
# ============================================================

def get_shibor_rate(days: int = 30) -> dict:
    """获取 SHIBOR 利率（Tushare shibor 接口）

    相比 AKShare rate_interbank（东财接口不稳定），Tushare 的 shibor 数据更稳定。

    Returns:
        dict: {
            "overnight": float,   # 隔夜利率 (%)
            "one_week": float,    # 1周利率 (%)
            "one_month": float,   # 1月利率 (%)
            "trend": str,         # 流动性收紧/平稳/宽松
            "available": bool,
            "source": "tushare",
            "data_date": str,
        }
    """
    from datetime import datetime, timedelta

    result = {
        "overnight": 0, "one_week": 0, "one_month": 0,
        "trend": "中性", "available": False, "source": "tushare",
    }

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

    rows = _call_tushare(
        "shibor",
        {"start_date": start_date, "end_date": end_date},
        "date,on,1w,2w,1m,3m,6m,9m,1y",
    )

    if not rows:
        print("[TUSHARE-SHIBOR] shibor 返回空")
        return result

    # 按日期升序
    rows = sorted(rows, key=lambda x: x.get("date", ""))

    latest = rows[-1]
    result["overnight"] = round(float(latest.get("on", 0) or 0), 4)
    result["one_week"] = round(float(latest.get("1w", 0) or 0), 4)
    result["one_month"] = round(float(latest.get("1m", 0) or 0), 4)
    result["data_date"] = latest.get("date", "")
    result["available"] = True

    # 趋势判断：对比近5日均值
    if len(rows) >= 5:
        recent_on = [float(r.get("on", 0) or 0) for r in rows[-5:]]
        avg_5d = sum(recent_on) / 5
        current = result["overnight"]
        if current > avg_5d * 1.2:
            result["trend"] = "流动性收紧"
        elif current < avg_5d * 0.8:
            result["trend"] = "流动性宽松"
        else:
            result["trend"] = "流动性平稳"

    print(f"[TUSHARE-SHIBOR] date={result['data_date']}, "
          f"ON={result['overnight']}%, 1W={result['one_week']}%, "
          f"trend={result['trend']}")

    return result


# ============================================================
# 15. 财经日历（eco_cal — 5000积分免费调用）
# ============================================================

def get_economic_calendar(start_date: str = "", end_date: str = "", 
                          country: str = "", event: str = "") -> list:
    """
    获取财经日历（Tushare eco_cal 接口）
    
    参数：
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD
        country: 国家筛选（"中国"、"美国"）
        event: 事件关键词（支持模糊匹配，如 "非农"、"CPI"）
    
    返回：
        list of dict: [
            {
                "date": "20260609",
                "time": "10:00:00",
                "country": "中国",
                "event": "CPI同比(%)",
                "value": "0.5%",
                "pre_value": "0.3%",
                "fore_value": "0.5%",
            },
            ...
        ]
    """
    from datetime import datetime, timedelta
    
    # 默认：未来7天
    if not end_date:
        end_dt = datetime.now() + timedelta(days=7)
        end_date = end_dt.strftime("%Y%m%d")
    if not start_date:
        start_dt = datetime.now() - timedelta(days=1)
        start_date = start_dt.strftime("%Y%m%d")
    
    params = {
        "start_date": start_date,
        "end_date": end_date,
    }
    if country:
        params["country"] = country
    if event:
        params["event"] = event
    
    rows = _call_tushare(
        "eco_cal", params,
        "date,time,country,event,value,pre_value,fore_value"
    )
    
    if not rows:
        print(f"[TUSHARE-CAL] eco_cal 返回空 ({start_date}~{end_date})")
        return []
    
    # 标准化输出
    events = []
    for row in rows:
        # 处理时间字段（可能为None）
        event_time = row.get("time", "") or ""
        
        events.append({
            "date": row.get("date", ""),
            "time": event_time,
            "country": row.get("country", ""),
            "event": row.get("event", ""),
            "value": row.get("value", ""),
            "previous": row.get("pre_value", ""),
            "forecast": row.get("fore_value", ""),
        })
    
    print(f"[TUSHARE-CAL] 获取 {len(events)} 个事件 ({start_date}~{end_date})")
    return events


def get_upcoming_events(days: int = 7, countries: list = None) -> list:
    """
    获取未来N天的重要事件（包装函数，供 financial_calendar.py 调用）
    
    参数：
        days: 未来天数
        countries: 国家列表（["中国", "美国"]），None=全部
    
    返回：
        list of event dicts（已标准化）
    """
    from datetime import datetime, timedelta
    
    start_dt = datetime.now() - timedelta(days=1)
    end_dt = datetime.now() + timedelta(days=days)
    
    start_date = start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")
    
    all_events = []
    
    if countries:
        # 按国家分别查询
        for country in countries:
            events = get_economic_calendar(start_date, end_date, country=country)
            all_events.extend(events)
    else:
        # 查询全部
        all_events = get_economic_calendar(start_date, end_date)
    
    # 去重
    seen = set()
    unique_events = []
    for event in all_events:
        key = f"{event.get('date')}_{event.get('event')}"
        if key not in seen:
            seen.add(key)
            unique_events.append(event)
    
    # 按日期排序
    unique_events.sort(key=lambda x: x.get("date", "99999999"))
    
    return unique_events


# ============================================================
# 14. 融资融券（margin — 替代 AKShare 只有上交所的问题）
# ============================================================

def get_margin_data(days: int = 30) -> dict:
    """获取融资融券数据（Tushare margin 接口）

    相比 AKShare stock_margin_sse（只有上交所 ≈ 60%），Tushare 有沪+深+北全部数据。

    Returns:
        dict: {
            "margin_balance": float,    # 融资余额（亿元）
            "margin_change_5d": float,  # 5日变化百分比
            "rzmre": float,             # 融资买入额（亿元/日）
            "rqye": float,              # 融券余额（亿元）
            "trend": str,               # 杠杆快速上升/温和上升/温和下降/快速下降
            "available": bool,
            "source": "tushare",
            "data_date": str,
        }
    """
    from datetime import datetime, timedelta

    result = {
        "margin_balance": 0, "margin_change_5d": 0, "rzmre": 0, "rqye": 0,
        "trend": "中性", "available": False, "source": "tushare",
    }

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

    rows = _call_tushare(
        "margin",
        {"start_date": start_date, "end_date": end_date},
        "trade_date,exchange_id,rzye,rzmre,rzche,rqye,rzrqye",
    )

    if not rows:
        print("[TUSHARE-MARGIN] margin 返回空")
        return result

    # 按日期聚合（沪+深+北 合计）
    from collections import defaultdict
    daily_totals = defaultdict(lambda: {"rzye": 0, "rzmre": 0, "rqye": 0, "rzrqye": 0})
    for row in rows:
        d = row.get("trade_date", "")
        if not d:
            continue
        daily_totals[d]["rzye"] += float(row.get("rzye", 0) or 0)
        daily_totals[d]["rzmre"] += float(row.get("rzmre", 0) or 0)
        daily_totals[d]["rqye"] += float(row.get("rqye", 0) or 0)
        daily_totals[d]["rzrqye"] += float(row.get("rzrqye", 0) or 0)

    if not daily_totals:
        return result

    # 按日期排序
    sorted_dates = sorted(daily_totals.keys())
    if len(sorted_dates) < 6:
        return result

    latest = daily_totals[sorted_dates[-1]]
    prev_5d = daily_totals[sorted_dates[-6]] if len(sorted_dates) >= 6 else daily_totals[sorted_dates[0]]

    # 数据完整性检查：如果最新一天余额比前一天骤降 >30%，说明部分交易所数据未到
    # 此时用前一天数据代替，避免误判
    if len(sorted_dates) >= 2:
        prev_day = daily_totals[sorted_dates[-2]]
        if prev_day["rzye"] > 0 and latest["rzye"] / prev_day["rzye"] < 0.7:
            print(f"[TUSHARE-MARGIN] ⚠️ {sorted_dates[-1]} 数据不完整"
                  f"（{latest['rzye']/1e8:.0f}亿 vs 前日{prev_day['rzye']/1e8:.0f}亿），用前日数据")
            latest = prev_day
            sorted_dates[-1] = sorted_dates[-2]

    # 万元 → 亿元
    def to_yi(v):
        return round(v / 1e8, 2)

    current_balance = latest["rzye"]
    prev_balance = prev_5d["rzye"]
    change_pct = round((current_balance - prev_balance) / max(prev_balance, 1) * 100, 2)

    result["margin_balance"] = to_yi(current_balance)
    result["margin_change_5d"] = change_pct
    result["rzmre"] = to_yi(latest["rzmre"])
    result["rqye"] = to_yi(latest["rqye"])
    result["data_date"] = sorted_dates[-1]
    result["available"] = True

    # 趋势判断
    if change_pct > 3:
        result["trend"] = "杠杆快速上升"
    elif change_pct > 1:
        result["trend"] = "杠杆温和上升"
    elif change_pct < -3:
        result["trend"] = "杠杆快速下降"
    elif change_pct < -1:
        result["trend"] = "杠杆温和下降"
    else:
        result["trend"] = "杠杆平稳"

    print(f"[TUSHARE-MARGIN] date={result['data_date']}, "
          f"balance={result['margin_balance']}亿, 5d_change={change_pct:+.2f}%, "
          f"trend={result['trend']}")

    return result


# ============================================================
# 15. 基金份额数据（fund_share — 2000积分门槛）
# ============================================================

def get_fund_share(ts_code: str, days: int = 30) -> dict:
    """获取基金/ETF 每日份额变化"""
    from datetime import datetime, timedelta
    result = {"available": False, "source": "tushare"}
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
    rows = _call_tushare("fund_share", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                         "ts_code,trade_date,fd_share,total_share,float_share")
    if not rows:
        return result
    rows = sorted(rows, key=lambda x: x.get("trade_date", ""))
    if len(rows) < 2:
        return result
    latest = rows[-1]
    prev_5d = rows[-6] if len(rows) >= 6 else rows[0]
    current_share = float(latest.get("fd_share", 0) or latest.get("total_share", 0) or 0)
    prev_share = float(prev_5d.get("fd_share", 0) or prev_5d.get("total_share", 0) or 0)
    if current_share <= 0:
        return result
    change_pct = round((current_share - prev_share) / max(prev_share, 1) * 100, 2)
    result.update({"shares_latest": round(current_share / 1e8, 2), "shares_change_5d": round((current_share - prev_share) / 1e8, 2),
                   "shares_change_pct": change_pct, "data_date": latest.get("trade_date", ""), "available": True})
    result["trend"] = "份额大增" if change_pct > 5 else "温和增长" if change_pct > 1 else "大减" if change_pct < -5 else "温和减少" if change_pct < -1 else "稳定"
    print(f"[TUSHARE-SHARE] {ts_code}: {result['shares_latest']}亿份, 5d{change_pct:+.2f}%")
    return result


# =====================================================================
# 2026-04-19 A 阶段新增：股票日线 + 估值批量 + 基金全套
# =====================================================================

def get_daily_price(code: str, days: int = 120, adj: str = "qfq") -> list:
    """
    股票日线数据（Tushare pro_bar，自带前复权）
    code: 可以是 000001 也可以是 000001.SZ
    返回: [{trade_date, open, high, low, close, vol, pct_chg}]，按日期升序
    """
    from datetime import datetime, timedelta
    ts_code = _code_to_ts(code)
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 60)).strftime("%Y%m%d")
    rows = _call_tushare(
        "daily",
        {"ts_code": ts_code, "start_date": start, "end_date": end},
        "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg",
    )
    if not rows:
        return []
    # 升序
    rows = sorted(rows, key=lambda x: x.get("trade_date", ""))
    return rows[-days:] if len(rows) > days else rows


def get_valuation_batch_map(trade_date: str = "") -> dict:
    """
    按日期拉全市场 PE/PB/市值/换手率（一次返回几千条）
    返回 {code(纯数字): {pe, pb, total_mv_亿, turnover, trade_date}}
    trade_date: YYYYMMDD，空则自动找最近交易日
    """
    from datetime import datetime, timedelta
    if not trade_date:
        # 往前找最多 7 天，确保拿到交易日
        for i in range(7):
            td = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            rows = _call_tushare(
                "daily_basic",
                {"trade_date": td},
                "ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate_f",
            )
            if rows and len(rows) > 100:
                trade_date = td
                break
        else:
            return {}
    else:
        rows = _call_tushare(
            "daily_basic",
            {"trade_date": trade_date},
            "ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate_f",
        )
        if not rows:
            return {}

    result = {}
    for r in rows:
        ts_code = r.get("ts_code", "")
        if not ts_code:
            continue
        code = ts_code.split(".")[0]
        try:
            pe = r.get("pe_ttm")
            pb = r.get("pb")
            mv = r.get("total_mv")
            tr = r.get("turnover_rate_f")
            result[code] = {
                "pe": round(float(pe), 2) if pe is not None and 0 < float(pe) < 10000 else None,
                "pb": round(float(pb), 2) if pb is not None and 0 < float(pb) < 1000 else None,
                "total_mv": round(float(mv) / 10000, 1) if mv is not None else None,  # 转亿
                "turnover": round(float(tr), 2) if tr is not None else None,
                "trade_date": trade_date,
            }
        except (ValueError, TypeError):
            continue
    print(f"[TUSHARE-BATCH] daily_basic {trade_date}: {len(result)} 只股票")
    return result


# -------- 基金全套（5000 积分解锁）--------

def get_fund_nav(code: str, days: int = 60) -> dict:
    """
    基金净值（历史）
    code: 006547 或 006547.OF
    返回: {available, source, navs: [...], latest, unit_nav, accum_nav, change_pct}
    """
    from datetime import datetime, timedelta
    ts_code = code if "." in code else f"{code}.OF"
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
    rows = _call_tushare(
        "fund_nav",
        {"ts_code": ts_code, "start_date": start, "end_date": end},
        "ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav",
    )
    if not rows:
        return {"available": False, "source": "tushare", "code": code}
    rows = sorted(rows, key=lambda x: x.get("nav_date", ""))
    latest = rows[-1]
    first = rows[0]
    try:
        chg = round((float(latest["unit_nav"]) - float(first["unit_nav"])) / float(first["unit_nav"]) * 100, 2)
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        chg = None
    return {
        "available": True,
        "source": "tushare",
        "code": code,
        "unit_nav": float(latest["unit_nav"]) if latest.get("unit_nav") else None,
        "accum_nav": float(latest["accum_nav"]) if latest.get("accum_nav") else None,
        "nav_date": latest.get("nav_date", ""),
        "change_pct": chg,
        "navs": rows,
    }


def get_fund_manager(code: str) -> dict:
    """基金经理信息 — Tushare 为主，AKShare 为降级"""
    ts_code = code if "." in code else f"{code}.OF"
    rows = _call_tushare(
        "fund_manager",
        {"ts_code": ts_code},
        "ts_code,ann_date,name,gender,birth_year,edu,nationality,begin_date,end_date,resume",
    )
    if not rows:
        # Tushare 无数据 → AKShare fund_manager_info 降级
        try:
            import akshare as ak
            # 先获取基金经理名字
            basic_df = ak.fund_open_fund_info_em(symbol=code, indicator="基金经理")
            if basic_df is not None and len(basic_df) > 0:
                manager_name = str(basic_df.iloc[-1].get("基金经理", "")).strip()
                if manager_name:
                    mgr_df = ak.fund_manager_info(manager=manager_name)
                    if mgr_df is not None and len(mgr_df) > 0:
                        from datetime import datetime
                        row = mgr_df.iloc[0]
                        begin_str = str(row.get("起始日期", "")).replace("-", "")
                        tenure_years = 0
                        if begin_str and len(begin_str) == 8:
                            try:
                                begin_dt = datetime.strptime(begin_str, "%Y%m%d")
                                tenure_years = round((datetime.now() - begin_dt).days / 365, 1)
                            except Exception:
                                pass
                        return {
                            "available": True,
                            "source": "akshare",
                            "managers": [{
                                "name": manager_name,
                                "begin_date": begin_str,
                                "end_date": "",
                                "resume": str(row.get("基金经理简介", "")),
                                "tenure_years": tenure_years,
                            }]
                        }
        except Exception:
            pass
        return {"available": False, "source": "tushare"}
    # 取当前在任的（end_date 为空的）
    active = [r for r in rows if not r.get("end_date")]
    if not active:
        active = sorted(rows, key=lambda r: r.get("begin_date", ""))[-1:]
    # 计算任期年数
    from datetime import datetime
    for mgr in active:
        begin_str = (mgr.get("begin_date") or "").replace("-", "")
        if begin_str and len(begin_str) == 8:
            try:
                begin_dt = datetime.strptime(begin_str, "%Y%m%d")
                mgr["tenure_years"] = round((datetime.now() - begin_dt).days / 365, 1)
            except Exception:
                mgr["tenure_years"] = 0
        else:
            mgr["tenure_years"] = 0
    return {
        "available": True,
        "source": "tushare",
        "managers": active[:5],
    }


def get_fund_portfolio(code: str, period: str = "") -> dict:
    """
    基金持仓明细
    period: YYYYMMDD 格式的报告期；空则取最近一期
    """
    from datetime import datetime
    ts_code = code if "." in code else f"{code}.OF"
    params = {"ts_code": ts_code}
    if period:
        params["period"] = period
    rows = _call_tushare(
        "fund_portfolio",
        params,
        "ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio",
    )
    if not rows:
        return {"available": False, "source": "tushare"}
    # 同一个 end_date 内按 mkv 降序
    latest_date = max((r.get("end_date", "") for r in rows), default="")
    top_holdings = sorted(
        [r for r in rows if r.get("end_date") == latest_date],
        key=lambda r: float(r.get("mkv", 0) or 0),
        reverse=True,
    )[:10]
    return {
        "available": True,
        "source": "tushare",
        "end_date": latest_date,
        "top_holdings": top_holdings,
    }


def get_fund_nav_by_date(nav_date: str) -> list:
    """
    按日期批量拉全市场基金净值（A++ 基金排行榜飞速版核心）
    nav_date: YYYYMMDD
    返回全部基金当天净值（一次调用可返回 1 万+ 条）
    """
    rows = _call_tushare(
        "fund_nav",
        {"nav_date": nav_date},
        "ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav",
    )
    print(f"[TUSHARE-FUND-BATCH] nav_date={nav_date}: {len(rows)} 条基金净值")
    return rows


def get_fund_basic_all() -> list:
    """全量基金名单（场内 E + 场外 O）"""
    rows_e = _call_tushare(
        "fund_basic",
        {"market": "E"},
        "ts_code,name,fund_type,invest_type,status,list_date,due_date,issue_amount",
    )
    rows_o = _call_tushare(
        "fund_basic",
        {"market": "O"},
        "ts_code,name,fund_type,invest_type,status,list_date,due_date,issue_amount",
    )
    all_rows = (rows_e or []) + (rows_o or [])
    print(f"[TUSHARE-FUND-BASIC] 场内 {len(rows_e or [])} + 场外 {len(rows_o or [])} = {len(all_rows)}")
    return all_rows


# ============================================================
# 国债收益率（yc_cb 中债国债收益率曲线）
# ============================================================

def get_treasury_yield(days: int = 30) -> dict:
    """获取中国10年期国债收益率（Tushare yc_cb）

    返回:
        {
            "yield_10y": 1.77,
            "yield_change_5d": -0.01,
            "yield_1y": 1.21,
            "available": True,
            "data_date": "20260515",
        }
    """
    from datetime import datetime, timedelta

    result = {"yield_10y": 0, "yield_change_5d": 0, "available": False}

    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")

    rows = _call_tushare(
        "yc_cb",
        {"curve_type": "0", "curve_term": "10", "start_date": start, "end_date": end},
        "trade_date,curve_term,yield",
    )

    if not rows or len(rows) < 2:
        return result

    # 按日期排序（最新在前）
    rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)

    current = float(rows[0].get("yield", 0))
    prev_5d = float(rows[min(4, len(rows) - 1)].get("yield", current))

    result["yield_10y"] = round(current, 4)
    result["yield_change_5d"] = round(current - prev_5d, 4)
    result["available"] = True
    result["data_date"] = rows[0].get("trade_date", "")
    result["source"] = "tushare_yc_cb"

    # 额外获取1年期收益率
    rows_1y = _call_tushare(
        "yc_cb",
        {"curve_type": "0", "curve_term": "1", "start_date": end, "end_date": end},
        "trade_date,curve_term,yield",
    )
    if rows_1y:
        result["yield_1y"] = round(float(rows_1y[0].get("yield", 0)), 4)

    print(f"[TUSHARE] 国债收益率 10Y={current}%, 5d变化={result['yield_change_5d']}%")
    return result


# ============================================================
# 指数日线（index_daily）
# ============================================================

def get_index_daily(ts_code: str = "000300.SH", days: int = 120) -> list:
    """获取指数日线数据（用于技术指标计算）

    Args:
        ts_code: 指数代码（000300.SH=沪深300, 000001.SH=上证指数, 399001.SZ=深证成指）
        days: 获取天数

    Returns:
        list of dict: [{"trade_date": ..., "close": ..., "open": ..., "high": ..., "low": ..., "vol": ...}, ...]
        按日期升序排列
    """
    from datetime import datetime, timedelta

    start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")  # 多取30天缓冲
    end = datetime.now().strftime("%Y%m%d")

    rows = _call_tushare(
        "index_daily",
        {"ts_code": ts_code, "start_date": start, "end_date": end},
        "trade_date,close,open,high,low,vol,amount",
    )

    if not rows:
        return []

    # 按日期升序
    rows.sort(key=lambda x: x.get("trade_date", ""))
    print(f"[TUSHARE] index_daily {ts_code}: {len(rows)} 条")
    return rows


# ============================================================
# 主力资金流排名（moneyflow + stock_basic 名称映射）
# ============================================================

_stock_name_cache = MemoryCache(default_ttl=86400)  # 股票名称缓存24小时


def _get_stock_names() -> dict:
    """获取全A股代码→名称映射（缓存24小时）"""
    cached = _stock_name_cache.get("all_names")
    if cached is not None:
        return cached

    rows = _call_tushare(
        "stock_basic",
        {"exchange": "", "list_status": "L"},
        "ts_code,name",
    )
    if not rows:
        return {}

    mapping = {r["ts_code"]: r["name"] for r in rows}
    _stock_name_cache.set("all_names", mapping, ttl=86400)
    print(f"[TUSHARE] stock_basic 名称映射: {len(mapping)} 只")
    return mapping


def validate_stock_code(code: str) -> dict:
    """校验股票代码是否为当前上市的A股

    Returns:
        {"valid": True, "name": "贵州茅台", "ts_code": "600519.SH"}
        {"valid": False, "reason": "该代码不在A股上市列表中"}
        {"valid": None, "reason": "无法连接数据源校验"}  # 降级放行
    """
    ts_code = _code_to_ts(code)
    names = _get_stock_names()
    if not names:
        # 数据源不可用时降级放行（不阻塞用户操作）
        return {"valid": None, "reason": "无法连接数据源校验"}
    if ts_code in names:
        return {"valid": True, "name": names[ts_code], "ts_code": ts_code}
    return {"valid": False, "reason": f"代码 {code} 不在A股上市列表中"}


def validate_fund_code(code: str) -> dict:
    """校验基金代码是否存在（直接查询 Tushare fund_basic）

    不依赖批量缓存（Tushare 分页限制 15000 条会遗漏基金），
    而是直接查询目标基金代码。结果缓存 24 小时。

    Returns:
        {"valid": True, "name": "易方达沪深300ETF联接A"}
        {"valid": False, "reason": "该代码不在公募基金列表中"}
        {"valid": None, "reason": "无法连接数据源校验"}
    """
    cache_key = f"fund_valid_{code}"
    cached = _stock_name_cache.get(cache_key)
    if cached is not None:
        return cached

    # 尝试 .OF（场外）和 .SH/.SZ（场内 ETF）
    ts_candidates = [f"{code}.OF"]
    if code.startswith("5"):
        ts_candidates.append(f"{code}.SH")
    elif code.startswith("1"):
        ts_candidates.append(f"{code}.SZ")

    for ts_code in ts_candidates:
        rows = _call_tushare("fund_basic", {"ts_code": ts_code}, "ts_code,name")
        if rows:
            result = {"valid": True, "name": rows[0].get("name", "")}
            _stock_name_cache.set(cache_key, result, ttl=86400)
            return result

    # 所有候选都查不到
    result = {"valid": False, "reason": f"代码 {code} 不在公募基金列表中"}
    _stock_name_cache.set(cache_key, result, ttl=3600)  # 失败缓存1小时（防误伤新基金）
    return result


def get_main_money_flow(trade_date: str = "") -> dict:
    """获取全市场主力资金流排名

    使用 Tushare moneyflow 接口，按 net_mf_amount 排序。

    Returns:
        {
            "available": True,
            "net_flow_total": -12.5 (亿),
            "top_inflow": [{"code": ..., "name": ..., "net_flow": 万元}, ...],
            "top_outflow": [{"code": ..., "name": ..., "net_flow": 万元}, ...],
            "data_date": "20260515",
        }
    """
    from datetime import datetime, timedelta

    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")

    result = {"available": False, "net_flow_total": 0, "top_inflow": [], "top_outflow": []}

    # 获取全市场资金流（Tushare 每次最多 5000+ 条）
    rows = _call_tushare(
        "moneyflow",
        {"trade_date": trade_date},
        "ts_code,trade_date,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount",
    )

    if not rows:
        # 当天可能还没收盘/非交易日，试前一天
        prev_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        rows = _call_tushare(
            "moneyflow",
            {"trade_date": prev_date},
            "ts_code,trade_date,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount",
        )
        if rows:
            trade_date = prev_date

    if not rows:
        return result

    # 获取股票名称映射
    name_map = _get_stock_names()

    # 计算主力净流入 = (buy_lg + buy_elg) - (sell_lg + sell_elg)
    # net_mf_amount 已经是净主力资金（万元）
    for r in rows:
        r["_net"] = float(r.get("net_mf_amount", 0) or 0)
        r["_name"] = name_map.get(r.get("ts_code", ""), "")

    # 排序
    rows.sort(key=lambda x: x["_net"], reverse=True)

    # TOP5 流入
    top_in = []
    for r in rows[:10]:
        if r["_net"] > 0:
            top_in.append({
                "code": r["ts_code"].split(".")[0],
                "name": r["_name"],
                "net_flow": round(r["_net"] / 10000, 2),  # 万→亿
            })
        if len(top_in) >= 5:
            break

    # TOP5 流出
    top_out = []
    for r in rows[-10:][::-1]:
        if r["_net"] < 0:
            top_out.append({
                "code": r["ts_code"].split(".")[0],
                "name": r["_name"],
                "net_flow": round(r["_net"] / 10000, 2),  # 万→亿
            })
        if len(top_out) >= 5:
            break

    # 全市场净流入
    total_net = sum(r["_net"] for r in rows) / 10000  # 万→亿

    result["available"] = True
    result["top_inflow"] = top_in
    result["top_outflow"] = top_out
    result["net_flow_total"] = round(total_net, 2)
    result["data_date"] = trade_date
    result["source"] = "tushare_moneyflow"
    result["total_stocks"] = len(rows)

    print(f"[TUSHARE] moneyflow {trade_date}: 全市场净流{total_net:.1f}亿, "
          f"TOP流入={top_in[0]['name'] if top_in else 'N/A'}")
    return result


def get_fund_extra_info_ak(code: str) -> dict:
    """AKShare 兜底：获取基金规模/经理/类型等补充信息

    返回 dict 格式与 AKShare fund_individual_basic_info_xq 的 item→value 映射一致。
    如果 AKShare 不可用或失败，返回空 dict。
    """
    try:
        import akshare as ak
        df = ak.fund_individual_basic_info_xq(symbol=code)
        if df is not None and len(df) > 0:
            return dict(zip(df['item'], df['value']))
    except Exception:
        pass
    return {}


# ============================================================
# v9.5.98: 5000 积分新增高价值接口接入
# ============================================================

def get_earning_forecast(code: str = "", end_date: str = "") -> list:
    """业绩预告 forecast — 提前1-3个月知道盈利情况"""
    from datetime import datetime
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    params = {"end_date": end_date}
    if code:
        params["ts_code"] = _code_to_ts(code) if "." not in code else code
    rows = _call_tushare("forecast", params,
                        "ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,summary")
    return rows or []


def get_top_list(trade_date: str = "", code: str = "") -> list:
    """龙虎榜 top_list — 当日上榜个股（游资/机构席位识别）"""
    from datetime import datetime
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    params = {"trade_date": trade_date}
    if code:
        params["ts_code"] = _code_to_ts(code) if "." not in code else code
    rows = _call_tushare("top_list", params,
                        "ts_code,trade_date,name,close,pct_change,turnover_rate,amount,reason,net_amount")
    return rows or []


def get_block_trade(trade_date: str = "", code: str = "") -> list:
    """大宗交易 block_trade — 折价/溢价交易"""
    from datetime import datetime
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    params = {"trade_date": trade_date}
    if code:
        params["ts_code"] = _code_to_ts(code) if "." not in code else code
    rows = _call_tushare("block_trade", params,
                        "ts_code,trade_date,price,vol,amount,buyer,seller")
    return rows or []


def get_cb_basic_list() -> list:
    """可转债基础信息 cb_basic — 在转 + 待转清单"""
    rows = _call_tushare("cb_basic", {},
                        "ts_code,bond_short_name,stk_code,stk_short_name,maturity_date,issue_size,remain_size,coupon_rate,bond_type")
    return rows or []


def get_cb_daily_quote(ts_code: str, days: int = 30) -> list:
    """可转债日线 cb_daily"""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
    rows = _call_tushare("cb_daily", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                        "ts_code,trade_date,close,pct_chg,vol,amount")
    return rows or []


def get_fund_company_list() -> list:
    """基金公司信息 fund_company — 用于识别大厂出品"""
    rows = _call_tushare("fund_company", {},
                        "name,shortname,setup_date,employees,main_business")
    return rows or []


def get_stk_limit(trade_date: str = "") -> list:
    """每日涨跌停价 stk_limit"""
    from datetime import datetime
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    rows = _call_tushare("stk_limit", {"trade_date": trade_date},
                        "ts_code,trade_date,pre_close,up_limit,down_limit")
    return rows or []


# ============================================================
# v9.5.99: 中ROI 8 个 Tushare 接口
# ============================================================

def get_express_report(code: str = "", end_date: str = "") -> list:
    """业绩快报 express — 比正式财报早披露1-2个月"""
    from datetime import datetime
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    params = {"end_date": end_date}
    if code:
        params["ts_code"] = _code_to_ts(code) if "." not in code else code
    rows = _call_tushare("express", params,
                        "ts_code,ann_date,end_date,revenue,operate_profit,total_profit,n_income,total_assets,yoy_net_profit,yoy_sales,bps,perf_summary")
    return rows or []


def get_share_repurchase(code: str = "", days: int = 90) -> list:
    """股票回购 repurchase — 公司回购自家股票（看好信号）"""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    params = {"start_date": start_date, "end_date": end_date}
    if code:
        params["ts_code"] = _code_to_ts(code) if "." not in code else code
    rows = _call_tushare("repurchase", params,
                        "ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit")
    return rows or []


def get_suspend_d(trade_date: str = "") -> list:
    """每日停复牌 suspend_d"""
    from datetime import datetime
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    rows = _call_tushare("suspend_d", {"trade_date": trade_date, "suspend_type": "S"},
                        "ts_code,trade_date,suspend_timing,suspend_type")
    return rows or []


def get_hsgt_top10(trade_date: str = "", market_type: str = "1") -> list:
    """沪深股通十大成交股 hsgt_top10
    market_type: 1=沪股通, 3=深股通"""
    from datetime import datetime
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    rows = _call_tushare("hsgt_top10", {"trade_date": trade_date, "market_type": market_type},
                        "ts_code,name,trade_date,close,change,rank,market_type,amount,net_amount,buy,sell")
    return rows or []


_top_inst_cache: dict = {}  # v9.5.101 进程内缓存（key=code|date, ttl=4h）

def get_top_inst(trade_date: str = "", code: str = "") -> list:
    """龙虎榜机构席位明细 top_inst — 区分游资/机构"""
    from datetime import datetime
    import time as _t
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    cache_key = f"{code or 'all'}|{trade_date}"
    cached = _top_inst_cache.get(cache_key)
    if cached and (_t.time() - cached[1]) < 14400:
        return cached[0]
    params = {"trade_date": trade_date}
    if code:
        params["ts_code"] = _code_to_ts(code) if "." not in code else code
    rows = _call_tushare("top_inst", params,
                        "ts_code,trade_date,exalter,buy,buy_rate,sell,sell_rate,net_buy,reason")
    result = rows or []
    _top_inst_cache[cache_key] = (result, _t.time())
    return result


def get_holder_number(code: str) -> list:
    """股东户数 stk_holdernumber — 户数减少=筹码集中（看涨）"""
    ts_code = _code_to_ts(code) if "." not in code else code
    rows = _call_tushare("stk_holdernumber", {"ts_code": ts_code},
                        "ts_code,ann_date,end_date,holder_num")
    return rows or []


def get_index_dailybasic(ts_code: str = "000300.SH", days: int = 30) -> list:
    """指数每日指标 index_dailybasic — PE/PB/股息率"""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
    rows = _call_tushare("index_dailybasic",
                        {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                        "ts_code,trade_date,total_mv,float_mv,total_share,float_share,pe,pe_ttm,pb,turnover_rate,dv_ratio,dv_ttm")
    return rows or []


def get_macro_cpi(months: int = 12) -> list:
    """中国 CPI cn_cpi — 通胀指标"""
    from datetime import datetime
    cur_year = datetime.now().year
    rows = _call_tushare("cn_cpi", {"start_m": f"{cur_year-1}01", "end_m": datetime.now().strftime("%Y%m")},
                        "month,nt_val,nt_yoy,nt_mom,nt_accu,town_val,town_yoy,town_mom,cnt_val,cnt_yoy,cnt_mom")
    return (rows or [])[:months]


def get_macro_pmi(months: int = 12) -> list:
    """中国 PMI cn_pmi — 制造业景气度"""
    from datetime import datetime
    cur_year = datetime.now().year
    rows = _call_tushare("cn_pmi", {"start_m": f"{cur_year-1}01", "end_m": datetime.now().strftime("%Y%m")},
                        "month,pmi010000,pmi010100,pmi010200,pmi010300,pmi010400,pmi010500,pmi010600,pmi010700,pmi010800,pmi010900,pmi011000")
    return (rows or [])[:months]


def get_macro_gdp(quarters: int = 8) -> list:
    """中国 GDP cn_gdp — 季度经济数据"""
    rows = _call_tushare("cn_gdp", {},
                        "quarter,gdp,gdp_yoy,pi,pi_yoy,si,si_yoy,ti,ti_yoy")
    return (rows or [])[:quarters]


# ============================================================
# v9.5.99: 低ROI 4 个接口（港股/美股/行业分类）
# ============================================================

def get_hk_basic_list() -> list:
    """港股基础信息 hk_basic"""
    rows = _call_tushare("hk_basic", {"list_status": "L"},
                        "ts_code,name,fullname,enname,cn_spell,market,list_status,list_date,delist_date,trade_unit,isin,curr_type")
    return rows or []


def get_hk_daily(ts_code: str, days: int = 30) -> list:
    """港股日线 hk_daily"""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
    rows = _call_tushare("hk_daily",
                        {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                        "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount")
    return rows or []


def get_us_basic_list() -> list:
    """美股基础信息 us_basic"""
    rows = _call_tushare("us_basic", {},
                        "ts_code,name,classify,list_date,delist_date")
    return rows or []


def get_us_daily(ts_code: str, days: int = 30) -> list:
    """美股日线 us_daily"""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
    rows = _call_tushare("us_daily",
                        {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                        "ts_code,trade_date,close,open,high,low,pre_close,change,pct_change,vol,amount")
    return rows or []


def get_index_classify(level: str = "L1") -> list:
    """申万行业分类 index_classify
    level: L1=一级, L2=二级, L3=三级"""
    rows = _call_tushare("index_classify", {"level": level, "src": "SW2021"},
                        "index_code,industry_name,level,industry_code,is_pub,parent_code")
    return rows or []



def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _finite_float(value, default=0.0):
    """转 float 且剔除 inf / nan（非有限值按 default 返回）

    与 _to_float() 的区别：_to_float 是**通用转换**，允许 inf 通过（调用方
    可能就是要原样透传）；本函数用于**求和/聚合**场景，inf 一旦进入累加会
    污染整个合计值（x + inf = inf），且下游 int(inf) 会抛 OverflowError。
    Tushare 偶发返回 "inf"/"nan"/"1e400" 一类脏值，聚合层必须在这里拦掉。
    """
    parsed = _to_float(value, default)
    return parsed if math.isfinite(parsed) else default



def _get_sw_sector_constituents(level: str = "L1", classifications: list | None = None) -> dict:
    """获取申万行业成分映射。"""
    classifications = classifications or get_index_classify(level) or []
    if not classifications:
        return {}

    level_code_field = {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}.get(level, "l1_code")
    result = {}
    for row in classifications:
        sector_code = str(row.get("index_code") or row.get("industry_code") or "").strip()
        sector_name = str(row.get("industry_name") or "").strip()
        if not sector_code or not sector_name:
            continue
        member_rows = _call_tushare(
            "index_member_all",
            {level_code_field: sector_code, "is_new": "Y"},
            "ts_code,name",
        )
        members = []
        for member in member_rows or []:
            ts_code = str(member.get("ts_code") or "").strip()
            name = str(member.get("name") or "").strip()
            if ts_code:
                members.append({"ts_code": ts_code, "name": name})
        result[sector_code] = {"name": sector_name, "members": members}
    return result



def _get_stock_snapshot_for_sector_enrichment(trade_date: str) -> dict:
    """获取用于行业聚合的个股快照。

    优先：moneyflow_dc（同时带 pct_change + net_amount）
    降级：daily + moneyflow
    """
    snapshot = {}

    moneyflow_dc_rows = _call_tushare(
        "moneyflow_dc",
        {"trade_date": trade_date},
        "ts_code,name,pct_change,net_amount",
    )
    for row in moneyflow_dc_rows or []:
        ts_code = str(row.get("ts_code") or "").strip()
        if not ts_code:
            continue
        snapshot[ts_code] = {
            "name": str(row.get("name") or "").strip(),
            "pct_change": _to_float(row.get("pct_change")),
            "net_inflow": _to_float(row.get("net_amount")),
        }
    if snapshot:
        return snapshot

    daily_rows = _call_tushare(
        "daily",
        {"trade_date": trade_date},
        "ts_code,pct_chg",
    )
    moneyflow_rows = _call_tushare(
        "moneyflow",
        {"trade_date": trade_date},
        "ts_code,net_mf_amount",
    )
    flow_map = {
        str(row.get("ts_code") or "").strip(): _to_float(row.get("net_mf_amount"))
        for row in (moneyflow_rows or [])
        if str(row.get("ts_code") or "").strip()
    }
    for row in daily_rows or []:
        ts_code = str(row.get("ts_code") or "").strip()
        if not ts_code:
            continue
        snapshot[ts_code] = {
            "name": "",
            "pct_change": _to_float(row.get("pct_chg")),
            "net_inflow": flow_map.get(ts_code, 0.0),
        }
    return snapshot



def _enrich_sw_sector_rows(rows: list, level: str = "L1", classifications: list | None = None) -> list:
    """给 sw_daily 行业行补齐行业资金流与上涨/下跌家数。"""
    if not rows:
        return rows

    trade_date = str(rows[0].get("trade_date") or "").strip()
    if not trade_date:
        return rows

    sector_members = _get_sw_sector_constituents(level=level, classifications=classifications)
    if not sector_members:
        return rows

    stock_snapshot = _get_stock_snapshot_for_sector_enrichment(trade_date)
    if not stock_snapshot:
        return rows

    enriched_rows = []
    for row in rows:
        sector_code = str(row.get("代码") or row.get("ts_code") or "").strip()
        members = (sector_members.get(sector_code) or {}).get("members") or []
        if not members:
            enriched_rows.append(row)
            continue

        up_count = 0
        down_count = 0
        net_inflow = 0.0
        matched = 0
        leader_name = str(row.get("领涨股") or "").strip()
        leader_change = None

        for member in members:
            ts_code = str(member.get("ts_code") or "").strip()
            if not ts_code:
                continue
            snap = stock_snapshot.get(ts_code)
            if not snap:
                continue
            matched += 1
            pct_change = _to_float(snap.get("pct_change"))
            net_inflow += _to_float(snap.get("net_inflow"))
            if pct_change > 0:
                up_count += 1
            elif pct_change < 0:
                down_count += 1
            if leader_change is None or pct_change > leader_change:
                leader_change = pct_change
                leader_name = str(snap.get("name") or member.get("name") or leader_name).strip()

        if not matched:
            enriched_rows.append(row)
            continue

        new_row = dict(row)
        new_row["净流入"] = round(net_inflow, 2)
        new_row["上涨家数"] = up_count
        new_row["下跌家数"] = down_count
        if leader_name and not new_row.get("领涨股"):
            new_row["领涨股"] = leader_name
        if leader_change is not None and not new_row.get("领涨股-涨跌幅"):
            new_row["领涨股-涨跌幅"] = round(leader_change, 2)
        enriched_rows.append(new_row)

    return enriched_rows



def get_sw_sector_daily(trade_date: str = "", level: str = "L1", lookback_days: int = 5) -> list:
    """统一获取申万行业日行情。

    统一走项目封装的 Tushare 入口：
    1. `index_classify` 获取申万行业列表
    2. `sw_daily` 获取最近可用交易日行情
    3. `index_member_all + moneyflow_dc` 聚合补齐行业资金流/上涨下跌家数
    4. `daily + moneyflow` 作为个股级聚合降级

    返回统一兼容 `sector_rotation.get_sector_ranking()` 的字段格式。
    """
    from datetime import datetime, timedelta

    if not is_configured():
        return []

    classifications = get_index_classify(level) or []
    code_name_map = {}
    for row in classifications:
        code = str(row.get("index_code") or row.get("industry_code") or "").strip()
        name = str(row.get("industry_name") or "").strip()
        if code and name:
            code_name_map[code] = name

    candidate_dates = []
    if trade_date:
        candidate_dates.append(trade_date)
    for i in range(max(lookback_days, 1)):
        td = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        if td not in candidate_dates:
            candidate_dates.append(td)

    for td in candidate_dates:
        rows = _call_tushare(
            "sw_daily",
            {"trade_date": td},
            "ts_code,trade_date,name,close,pct_change,amount",
        )
        if not rows:
            continue

        result = []
        for row in rows:
            ts_code = str(row.get("ts_code") or "").strip()
            if code_name_map and ts_code not in code_name_map:
                continue
            name = code_name_map.get(ts_code) or str(row.get("name") or "").strip()
            if not ts_code or not name:
                continue
            result.append({
                "代码": ts_code,
                "板块": name,
                "涨跌幅": _to_float(row.get("pct_change")),
                "总成交额": _to_float(row.get("amount")),
                "收盘价": _to_float(row.get("close")),
                "trade_date": str(row.get("trade_date") or td),
                "source": "tushare",
            })

        if len(result) >= 10:
            return _enrich_sw_sector_rows(result, level=level, classifications=classifications)

    return []
