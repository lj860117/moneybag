"""
持仓加载与穿透数据拉取。

设计依据：docs/design/signal-scout-fund-account.md §3.3 / §3.4

单位约定（铁律，赋值行必须有注释）：
  * shares            = 份额
  * cost_nav / unit_nav / adj_nav = 元/份
  * market_value      = 元（= shares × unit_nav）
  * weight_mv         = 市值占比 %（= market_value / Σ market_value × 100）
  * stk_mkv_ratio     = % 占【该基金】净值（展示需二次加权成「占用户总净值」）

⚠️ B8：`adj_nav` 含分红再投资，与 `cost_nav` 不同尺度，禁止 `adj_nav / cost_nav`。
     `adj_nav` 只用于「定位 60 日高点日期」，取到日期后再用 `unit_nav` 计价。
"""
import math
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor


@dataclass
class FundPosition:
    """单只持仓基金的实时快照。"""
    code: str                       # 6 位基金代码（013107）
    name: str                       # 基金简称
    shares: float                   # 份额
    cost_nav: float                 # 成本净值（用户实际买入价，元/份）
    unit_nav: float                 # 最新单位净值（元/份，回撤触发口径）
    adj_nav: float                  # 最新复权净值（元/份，仅定位高点日期）
    is_qdii: bool                   # 纯展示字段（名称含 QDII），覆盖率计算不依赖它
    market_value: float             # 市值（元）= shares × unit_nav
    weight_mv: float                # 市值占比 %
    nav_date: str = ""              # 最新净值日（YYYYMMDD，额外字段）
    nav_history: list = field(default_factory=list)  # 近期净值列表（额外字段，用于 60 日高点）


def _safe_float(value, default: float = 0.0) -> float:
    """安全转 float，None/空串/非数值/非有限值一律按 default。"""
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _detect_qdii(name: str) -> bool:
    """QDII 判定：仅看名称里的字面 QDII，只用于文案措辞。

    ⚠️ 禁止用 fund_type / invest_type / type 判 QDII（实测三者全部无法标识）。
    覆盖率盲区的主判定不靠本字段，而是运行时 fund_portfolio 是否返回数据。
    """
    return "QDII" in str(name or "").upper()


def _largest_remainder_weights(values: list) -> list:
    """按最大余数法把权重分配成「和为 100.00%」的百分比列表。

    例：values 的占比除不尽时，用最大余数法逐位补偿，保证 Σ == 100.00
    （2 位小数），避免出现 99.99 / 100.01 的累加误差。返回值为百分数本体。
    """
    total = sum(values)
    if total <= 0:
        return [0.0 for _ in values]

    units = 10000  # 2 位小数精度 → 100.00 对应 10000 个「基点」
    exact = [v / total * units for v in values]
    floors = [math.floor(e) for e in exact]
    remainder = units - sum(floors)

    # 余数按「小数部分最大者优先」分配（最大余数法）。
    order = sorted(range(len(values)), key=lambda i: exact[i] - floors[i], reverse=True)
    result = list(floors)
    for i in range(int(remainder)):
        result[order[i]] += 1
    return [r / 100.0 for r in result]


def load_positions(user_id: str) -> list:
    """从 fund_monitor.load_fund_holdings() 读持仓，补最新净值与权重。

    返回 list[FundPosition]；无持仓 / 全部无净值数据时返回 []。
    权重 weight_mv 用最大余数法补到 Σ == 100.00%。
    """
    from services.fund_monitor import load_fund_holdings
    from services.tushare_data import get_fund_nav

    try:
        holdings = load_fund_holdings(user_id) or []
    except Exception as e:
        print(f"[FUND_SIGNAL] load_fund_holdings({user_id}) failed: {e}")
        return []

    positions: list = []
    for h in holdings:
        code = str(h.get("code", "") or "").strip()
        if not code:
            continue

        name = str(h.get("name", "") or code)
        shares = _safe_float(h.get("shares"), 0.0)
        # 成本净值：优先 costNav；缺失时用 totalCost/shares 兜底。
        cost_nav = _safe_float(h.get("costNav"), 0.0)
        if cost_nav <= 0 and shares > 0:
            cost_nav = _safe_float(h.get("totalCost"), 0.0) / shares

        # 取 90 天窗口（get_fund_nav 内部再 +30 天 buffer），保证足够 61 个净值日。
        nav = get_fund_nav(code, days=90) or {}

        unit_nav = 0.0
        adj_nav = 0.0
        nav_date = ""
        nav_history: list = []
        if nav.get("available"):
            unit_nav = _safe_float(nav.get("unit_nav"), 0.0)
            nav_date = str(nav.get("nav_date", "") or "")
            nav_history = nav.get("navs") or []
            if nav_history:
                # adj_nav 顶层 get_fund_nav 未返回，从最新一条 navs 里取。
                adj_nav = _safe_float(nav_history[-1].get("adj_nav"), 0.0)

        if unit_nav <= 0:
            # 无净值数据 → 市值/权重无法计算，跳过该基金并告警（不阻断整体）。
            print(f"[FUND_SIGNAL] {code} 无净值数据，本轮不计入组合")
            continue

        market_value = shares * unit_nav  # 元 = 份额 × 单位净值
        positions.append(
            FundPosition(
                code=code,
                name=name,
                shares=shares,
                cost_nav=cost_nav,
                unit_nav=unit_nav,
                adj_nav=adj_nav,
                is_qdii=_detect_qdii(name),
                market_value=market_value,
                weight_mv=0.0,
                nav_date=nav_date,
                nav_history=nav_history,
            )
        )

    if not positions:
        return []

    # 权重：最大余数法，Σ == 100.00%（百分数本体）。
    weights = _largest_remainder_weights([p.market_value for p in positions])
    for p, w in zip(positions, weights):
        p.weight_mv = w
    return positions


def fetch_portfolios(codes: list, max_workers: int = 8) -> dict:
    """并行拉 fund_portfolio → {code: {"ok","end_date","ann_date","holdings"}}。

    ok=False 即无穿透数据（QDII 盲区 / 新基金无季报 / 接口临时故障），
    不是故障 —— 调用方必须据此归入 blind_pct，不得静默丢弃。
    """
    from services.tushare_data import get_fund_portfolio

    def _one(code: str):
        try:
            p = get_fund_portfolio(code) or {}
            if not p.get("available"):
                return code, {"ok": False}
            holdings = p.get("top_holdings") or []
            return code, {
                "ok": True,
                "end_date": p.get("end_date", ""),
                # 同一报告期内 ann_date 一致，取第一条即可。
                "ann_date": (holdings[0].get("ann_date", "") if holdings else ""),
                "holdings": holdings,
            }
        except Exception as e:
            print(f"[FUND_SIGNAL] fetch_portfolios({code}) failed: {e}")
            return code, {"ok": False}

    result: dict = {}
    if not codes:
        return result
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for code, data in pool.map(_one, codes):
            result[code] = data
    return result
