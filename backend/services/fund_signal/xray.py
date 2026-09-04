"""
P0-1 组合穿透体检 —— 穿透暴露的唯一计算入口。

设计依据：docs/design/signal-scout-fund-account.md §3.3 / §3.4

⚠️ 二次加权公式（PRD §6.2 的 6 倍偏差坑就在这里）：
    exposure[stock] = Σ_fund ( weight_mv[fund] × stk_mkv_ratio[fund][stock] / 100 )
  - stk_mkv_ratio 单位 = 【% 占该基金净值】，不是占用户总净值
  - 结果 exposure_pct 单位 = 【% 占用户总净值】
  - 分母恒为用户总净值（100%），不是「已覆盖部分」

覆盖率三分解（三者相加恒 = 100%）：
    penetrated_pct = Σ_stocks exposure_pct
    blind_pct      = Σ weight_mv of funds where ok=False   （QDII 盲区）
    residual_pct   = 100 - penetrated_pct - blind_pct       （前十大之外+现金债券）
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, date

from services.fund_signal.config import (
    XRAY_INDUSTRY_MAX_PCT,
    XRAY_STOCK_MAX_PCT,
    XRAY_STOCK_FUND_COUNT_MIN,
)


@dataclass
class StockExposure:
    symbol: str
    name: str               # 中文简称（申万反查）；缺失回退 symbol
    exposure_pct: float     # % 占用户总净值
    via_funds: list         # 重仓该标的的基金代码列表
    fund_count: int         # 重仓该标的的基金数


@dataclass
class IndustryExposure:
    industry: str
    exposure_pct: float     # % 占用户总净值


@dataclass
class Coverage:
    penetrated_pct: float
    blind_pct: float
    residual_pct: float
    blind_funds: list       # [{code, name, weight_mv}]
    end_date: str           # 持仓截止日（YYYY-MM-DD）
    ann_date: str           # 公告日（YYYY-MM-DD，额外字段，供文案标注滞后）
    lag_days: int           # 公告日 - 持仓截止日 的天数
    industry_source: str    # sw_l2 / tushare_industry / none


@dataclass
class XrayResult:
    industries: list        # list[IndustryExposure]
    stocks: list            # list[StockExposure]
    coverage: Coverage
    triggered_rules: list   # ["R1_industry_concentration", ...]


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _norm_date(value) -> str:
    """把 YYYYMMDD / YYYY-MM-DD 统一成 YYYY-MM-DD；脏值返回 ""。"""
    s = str(value or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    return ""


def _stock_name(symbol: str, sw_map: dict) -> str:
    """从申万反查表取中文简称；缺失返回 ""（render 层回退 symbol）。"""
    sw = sw_map.get(symbol) if isinstance(sw_map, dict) else None
    if isinstance(sw, dict):
        return str(sw.get("name", "") or "").strip()
    return ""


def _parse_date(s: str):
    """解析 YYYY-MM-DD → date；失败返回 None。"""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _lag_days(end_date: str, ann_date: str) -> int:
    """公告日 - 持仓截止日 的天数；解析失败返回 0。"""
    e = _parse_date(end_date)
    a = _parse_date(ann_date)
    if e is None or a is None:
        return 0
    return (a - e).days


def compute_exposure(positions, portfolios: dict, sw_map: dict,
                     sw_source: str = "sw_l2") -> XrayResult:
    """穿透暴露计算（唯一入口）。见模块 docstring 的公式与三分解口径。

    Args:
        positions: list[FundPosition]
        portfolios: fetch_portfolios() 返回值 {code: {"ok","end_date","ann_date","holdings"}}
        sw_map: load_sw_l2_map() 的 {股票代码: {"l1","l2","l3"}}
        sw_source: 行业来源（sw_l2 / tushare_industry / none），仅记录进 Coverage。
    """
    stock_buckets: dict = {}    # symbol -> {"exposure_pct": float, "via_funds": set}
    industry_buckets: dict = {}  # l2 行业名 -> exposure_pct
    blind_pct = 0.0
    blind_funds: list = []
    ok_funds: list = []          # [(end_date, ann_date)] 用于取最新报告期

    for p in positions:
        pf = portfolios.get(p.code)
        if not isinstance(pf, dict) or not pf.get("ok"):
            # ok=False：QDII 盲区 / 新基金无季报 / 接口临时故障 → 归入 blind_pct。
            blind_pct += p.weight_mv
            blind_funds.append({
                "code": p.code,
                "name": p.name,
                "weight_mv": round(p.weight_mv, 2),
            })
            continue

        ok_funds.append((_norm_date(pf.get("end_date")), _norm_date(pf.get("ann_date"))))
        for h in pf.get("holdings", []):
            symbol = str(h.get("symbol", "") or "").strip()
            if not symbol:
                continue
            # stk_mkv_ratio = % 占该基金净值（不是占用户总净值）。
            ratio = _safe_float(h.get("stk_mkv_ratio"), 0.0)
            # 二次加权：weight_mv(%) × ratio(%) / 100 → % 占用户总净值。
            contrib = p.weight_mv * ratio / 100.0

            bucket = stock_buckets.setdefault(symbol, {"exposure_pct": 0.0, "via_funds": set()})
            bucket["exposure_pct"] += contrib
            bucket["via_funds"].add(p.code)

            sw = sw_map.get(symbol) if isinstance(sw_map, dict) else None
            if isinstance(sw, dict) and sw.get("l2"):
                l2 = str(sw["l2"])
                industry_buckets[l2] = industry_buckets.get(l2, 0.0) + contrib

    penetrated_raw = sum(b["exposure_pct"] for b in stock_buckets.values())
    penetrated_pct = round(penetrated_raw, 2)
    blind_pct = round(blind_pct, 2)
    # 保证三分解恒 = 100.00：残差用「100 - 已穿透 - 盲区」反推，
    # 与「Σ ok 基金权重 - 已穿透」在权重已用最大余数法补到 100 时完全等价，
    # 且避免了独立取整带来的 0.01 漂移。
    residual_pct = round(100.0 - penetrated_pct - blind_pct, 2)

    stocks = [
        StockExposure(
            symbol=sym,
            name=_stock_name(sym, sw_map),
            exposure_pct=round(b["exposure_pct"], 2),
            via_funds=sorted(b["via_funds"]),
            fund_count=len(b["via_funds"]),
        )
        for sym, b in stock_buckets.items()
    ]
    stocks.sort(key=lambda s: (-s.exposure_pct, s.symbol))

    industries = [
        IndustryExposure(industry=name, exposure_pct=round(pct, 2))
        for name, pct in industry_buckets.items()
    ]
    industries.sort(key=lambda i: -i.exposure_pct)

    triggered: list = []
    if any(i.exposure_pct > XRAY_INDUSTRY_MAX_PCT for i in industries):
        triggered.append("R1_industry_concentration")
    if any(s.exposure_pct > XRAY_STOCK_MAX_PCT for s in stocks):
        triggered.append("R2_stock_concentration")
    if any(s.fund_count >= XRAY_STOCK_FUND_COUNT_MIN for s in stocks):
        triggered.append("R3_overlap")

    # 最新报告期：取 ok 基金中最大的 end_date。
    latest = ("", "")
    for ed, ad in ok_funds:
        if ed > latest[0]:
            latest = (ed, ad)
    end_date, ann_date = latest
    coverage = Coverage(
        penetrated_pct=penetrated_pct,
        blind_pct=blind_pct,
        residual_pct=residual_pct,
        blind_funds=blind_funds,
        end_date=end_date,
        ann_date=ann_date,
        lag_days=_lag_days(end_date, ann_date),
        industry_source=sw_source,
    )
    return XrayResult(industries=industries, stocks=stocks,
                      coverage=coverage, triggered_rules=triggered)


def decide_emit(result: XrayResult, portfolios: dict, state: dict) -> bool:
    """P0-1 是否推送。

    规则（设计 §3.4 R4 + PRD P0-1 冷启动）：
      * 首次运行（无 baseline_sent）→ 无条件推 1 条基线体检（P0-1 唯一不适用静默）。
      * 之后仅当出现新的报告期 end_date 且命中 R1/R2/R3 任一门槛时才推。
    """
    if not isinstance(state, dict) or not state.get("baseline_sent"):
        return True

    last = state.get("last_end_dates", {}) if isinstance(state, dict) else {}
    new_period = False
    for code, pf in portfolios.items():
        if not isinstance(pf, dict) or not pf.get("ok"):
            continue
        ed = _norm_date(pf.get("end_date"))
        # ⚠️ 两边必须都规范化再比：last_end_dates 里存的是调用方【原样写入】的
        # pf["end_date"]（见 fund_signal/__init__.py _collect_fund_signals，
        # Tushare 原始格式是 YYYYMMDD），而 ed 已被 _norm_date 转成 YYYY-MM-DD。
        # 不规范化 last 侧 → "20260630" != "2026-06-30" 恒成立 →
        # new_period 恒为 True → P0-1 每次 match() 都重推，冷启动「只推一次」
        # 的语义彻底失效（2026-09-05 线上实测：已 baseline_sent 仍返回 True）。
        last_ed = _norm_date(last.get(code))
        if ed and last_ed != ed:
            new_period = True
            break
    return bool(new_period and result.triggered_rules)
