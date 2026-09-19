"""
钱袋子 — 持仓存储统一桥接层（FIX 2026-04-19 F3）

背景：
  MoneyBag 有两套独立的持仓存储互不通信：
    1) V4 transactions 流水制（portfolio.transactions[]）— signal/risk/allocation/backtest 使用
    2) stock_holdings + fund_holdings 独立文件 — overview/monitor 使用

  走 transactions 建仓在 overview 完全看不到（反之亦然）。
  
设计：
  - 不改动底层存储结构（避免破坏性迁移）
  - 提供 unified_load_stock_holdings / unified_load_fund_holdings 两个函数
  - 优先读独立文件；空时回退到 V4 transactions 派生
  - 新 API 调用这里即可获得"两套合一"的视图
"""
from typing import Dict, List, Set


# ---- 6 位代码命名空间重叠防护（002163 双算事故）----
#
# A 股股票代码与场外基金代码**都是 6 位数字且空间重叠**：
#   `002163` 既是深市股票「海南发展」，也是基金「东方惠新灵活配置混合C」。
# 因此 `code` 单独**不构成标的身份**，只有 (资产类型, code) 才是唯一键。
#
# 事故路径（实测于生产用户 LeiJiang）：
#   1. 独立 `fund_holdings` 文件里有基金 002163（真值 112.25）；
#   2. 独立 `stock_holdings` 文件为空 → `unified_load_stock_holdings` 回退到
#      本模块的 V4 派生；
#   3. 旧代码**只按代码前缀**分类（`002` → A 股），于是把同一支基金又派生
#      成一份**股票**持仓（303.00）；
#   4. `portfolio_overview.get_portfolio_overview()` 里
#      `total_mv = stock_total_mv + fund_total_mv`，同一笔被加两次 →
#      `totalMarketValue` 虚高 +303（真值 719.59），equity 占比同步虚高。
#
# 为什么不能只靠前缀修：V4 transaction **没有资产类型字段**（见
# `services/portfolio_calc.calc_holdings_from_transactions`，只有
# code/name/shares/amount/nav），流水本身无法判定股票还是基金；而去掉
# `000`/`002` 前缀又会把真正的深市股票（如 002415 海康威视）误判成基金 ——
# 只是把错误换个方向。可用的**权威类型信息只在独立持仓文件里**，因此派生时
# 必须对照另一侧的独立持仓，把已被认领的代码排除掉。
#
# 判定口径（与 portfolio_overview._is_duplicate_account_cash 同源的保守策略）：
#   代码相同 **且** 无法证伪「是同一支标的」→ 视为已被认领，不派生。
#   「无法证伪」= 名称归一化后相等 / 一方包含另一方 / 任一侧名称缺失。
#   只有两侧名称**都非空且互不包含**时，才认定是代码空间重叠的两个不同标的
#   （股票 002163 海南发展 vs 基金 002163 东方惠新…），正常派生。
#   宁可少派生一笔（不虚增），也不凭前缀凭空造一笔（双算）。


def _norm_name(name) -> str:
    """归一化名称用于同名比对：去掉所有空白 + 转小写。"""
    return "".join(str(name or "").split()).lower()


def _looks_like_same_instrument(name_a, name_b) -> bool:
    """判断两个名称是否**可能**指向同一支标的（无法证伪即视为同一支）。"""
    na = _norm_name(name_a)
    nb = _norm_name(name_b)
    # 任一侧缺名称 → 无从证伪，按同一支处理（宁可不派生，也不双算）。
    if not na or not nb:
        return True
    return na == nb or na in nb or nb in na


def _known_instrument_names(user_id: str, kind: str) -> Dict[str, Set[str]]:
    """读取**独立持仓文件**中已知某类标的：{code: {归一化名称, ...}}。

    这是本模块唯一可信的资产类型来源（V4 流水没有类型字段）。

    Args:
        user_id: 用户 ID。
        kind: "stock" 读 stock_monitor，"fund" 读 fund_monitor。

    Returns:
        code → 归一化名称集合。读不到 / 异常一律返回空 dict（不阻断派生）。
    """
    try:
        if kind == "stock":
            from services.stock_monitor import load_stock_holdings
            rows = load_stock_holdings(user_id) or []
        else:
            from services.fund_monitor import load_fund_holdings
            rows = load_fund_holdings(user_id) or []
    except Exception as e:
        print(f"[BRIDGE] 读取 {kind} 独立持仓失败，跳过重叠防护: {e}")
        return {}

    index: Dict[str, Set[str]] = {}
    for h in rows:
        code = str(h.get("code") or "").strip()
        if not code:
            continue
        index.setdefault(code, set()).add(_norm_name(h.get("name")))
    return index


def _claimed_by_other_kind(code: str, name, other_index: Dict[str, Set[str]]) -> bool:
    """判断 (code, name) 是否已被**另一种资产类型**的独立持仓认领。

    Args:
        code: 待派生标的的 6 位代码。
        name: 待派生标的在 V4 流水里的名称（可能为空）。
        other_index: 另一侧独立持仓的 {code: {名称}} 索引。

    Returns:
        True = 已被另一侧认领，本侧不应再派生（否则双算）。
    """
    names = other_index.get(str(code or "").strip())
    if not names:
        return False
    for other_name in names:
        if _looks_like_same_instrument(name, other_name):
            return True
    return False


def _holdings_from_transactions_stock(user_id: str) -> List[Dict]:
    """从 V4 transactions 派生股票持仓（A股代码：6位数字，6/0/3开头）

    ⚠️ 已排除「代码同时存在于独立基金持仓」的条目 —— 详见模块顶部
    「6 位代码命名空间重叠防护」注释，否则 002163 这类重叠代码会被
    既当股票又当基金各算一次（双算）。
    """
    try:
        from services.persistence import load_user
        from services.portfolio_calc import calc_holdings_from_transactions

        user = load_user(user_id)
        if not user:
            return []
        portfolio = user.get("portfolio", {})
        transactions = portfolio.get("transactions", [])
        if not transactions:
            return []

        calc = calc_holdings_from_transactions(transactions)
        active = calc.get("active", [])
        # 权威「已知基金」索引：只有它能区分 002163 到底是股票还是基金。
        known_funds = _known_instrument_names(user_id, "fund")

        out = []
        for h in active:
            code = str(h.get("code", ""))
            # 只取 A 股股票（6 位数字）
            if not (code.isdigit() and len(code) == 6):
                continue
            if not (code[0] in ("6", "3") or code.startswith("000") or code.startswith("002") or code.startswith("688")):
                continue
            # 该代码已被独立基金持仓认领为**基金** → 不得再派生为股票。
            if _claimed_by_other_kind(code, h.get("name", ""), known_funds):
                continue
            shares = h.get("shares", 0)
            total_cost = h.get("totalCost", 0)
            avg_nav = h.get("avgNav", 0)
            cost_price = avg_nav if avg_nav > 0 else (total_cost / shares if shares > 0 else 0)
            out.append({
                "code": code,
                "name": h.get("name", ""),
                "costPrice": round(cost_price, 3),
                "shares": shares,
                "note": "(from V4 transactions)",
                "industry": h.get("industry", ""),
                "addedAt": h.get("firstBuyDate", ""),
                "_source": "v4_transactions",
            })
        return out
    except Exception as e:
        print(f"[BRIDGE] stock from transactions failed: {e}")
        return []


def _holdings_from_transactions_fund(user_id: str) -> List[Dict]:
    """从 V4 transactions 派生基金持仓（非 A 股代码视为基金）

    ⚠️ 已排除「代码同时存在于独立股票持仓」的条目 —— 详见模块顶部
    「6 位代码命名空间重叠防护」注释。这条对**非 6 位数字**代码同样有效：
    前缀启发式对 "AAPL" / "00700" 这类代码一律判为「非 A 股 → 基金」，
    若独立股票持仓里已经持有它，就会被再派生成一份基金（双算）。
    """
    try:
        from services.persistence import load_user
        from services.portfolio_calc import calc_holdings_from_transactions

        user = load_user(user_id)
        if not user:
            return []
        portfolio = user.get("portfolio", {})
        transactions = portfolio.get("transactions", [])
        if not transactions:
            return []

        calc = calc_holdings_from_transactions(transactions)
        active = calc.get("active", [])
        # 权威「已知股票」索引（同上，V4 流水没有类型字段）。
        known_stocks = _known_instrument_names(user_id, "stock")

        out = []
        for h in active:
            code = str(h.get("code", ""))
            # 该代码已被独立股票持仓认领为**股票** → 不得再派生为基金。
            if _claimed_by_other_kind(code, h.get("name", ""), known_stocks):
                continue
            # 跳过 A 股股票
            is_astock = (code.isdigit() and len(code) == 6 and
                         (code[0] in ("6", "3") or code.startswith("000") or
                          code.startswith("002") or code.startswith("688")))
            if is_astock:
                continue
            shares = h.get("shares", 0)
            total_cost = h.get("totalCost", 0)
            avg_nav = h.get("avgNav", 0)
            if avg_nav <= 0 and shares > 0:
                avg_nav = total_cost / shares
            out.append({
                "code": code,
                "name": h.get("name", ""),
                "costNav": round(avg_nav, 4),
                "shares": shares,
                "note": "(from V4 transactions)",
                "addedAt": h.get("firstBuyDate", ""),
                "_source": "v4_transactions",
            })
        return out
    except Exception as e:
        print(f"[BRIDGE] fund from transactions failed: {e}")
        return []


def unified_load_stock_holdings(user_id: str = "default") -> List[Dict]:
    """统一加载股票持仓：优先独立文件，空则回退到 V4 transactions"""
    from services.stock_monitor import load_stock_holdings
    primary = load_stock_holdings(user_id)
    if primary:
        return primary
    return _holdings_from_transactions_stock(user_id)


def unified_load_fund_holdings(user_id: str = "default") -> List[Dict]:
    """统一加载基金持仓：优先独立文件，空则回退到 V4 transactions"""
    from services.fund_monitor import load_fund_holdings
    primary = load_fund_holdings(user_id)
    if primary:
        return primary
    return _holdings_from_transactions_fund(user_id)


def unified_load_all_holdings(user_id: str = "default") -> Dict:
    """统一加载所有持仓（股票+基金）"""
    return {
        "stocks": unified_load_stock_holdings(user_id),
        "funds": unified_load_fund_holdings(user_id),
    }
