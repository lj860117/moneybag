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
#
# 同一个判据还有**反方向**的用法（少算）—— 见 `_distinct_from_other_kind`：
#   前缀判据只是「无类型信息时的兜底猜测」。当名称已经证伪了「是同一支标的」
#   之后，前缀的结论必须让位，否则刚刚证伪出来的那支标的会被前缀再次丢掉
#   （股票文件有 002163 海南发展 + V4 有 002163 东方惠新 时，东方惠新会因
#   `002` 前缀被当成 A 股跳过 → 基金侧空手，整笔持仓消失）。
#   保守取向不变：仅在「双方名称都非空且互不包含」这一种强证据下才推翻前缀。
#
# 为什么**只有基金侧**推翻前缀（两侧不对称）：
#   证伪证据必须来自另一侧的独立持仓文件，而「另一侧文件非空」在两侧含义相反：
#     - 基金侧推翻前缀：证据来自独立**股票**持仓（非空）⇒ 股票侧不会回退 V4
#       ⇒ 这支标的只可能以基金身份出现一次，**不可能双算**；不推翻则整笔消失。
#     - 股票侧推翻前缀：证据来自独立**基金**持仓（非空）⇒ 基金侧不会回退 V4，
#       该代码已作为基金计入总览 ⇒ 再派生一支同名股票就是**双算**
#       （即上一轮刚修掉的 002163 事故换个门进来）。
#   而「同一支基金两处写法不同」在生产里真实存在（BuLuoGeLi 163406：V4 里同时
#   有「兴全合润混合A」与「兴全合润混合(LOF)A」，归一化后互不包含），所以股票
#   侧的「已证伪为不同标的」并不可靠。故股票侧保持只按前缀收股票，遇到已证伪
#   的情况只打告警、不派生（宁可少算，也不双算）。


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


def _distinct_from_other_kind(code: str, name, other_index: Dict[str, Set[str]]) -> bool:
    """判断 (code, name) 是否已**被证伪为**「与另一侧同名代码下的所有标的都不同」。

    与 `_claimed_by_other_kind` 是同一枚硬币的两面（都要求 code 命中索引）：

      - `_claimed_by_other_kind`      = 存在**至少一条**记录无法证伪 → 已被认领；
      - `_distinct_from_other_kind`   = 该 code 下**每一条**记录都与 name
                                        互不包含 → 全部证伪为不同标的。

    为什么要单独要这个信号：
      代码前缀（`002`/`000`/`6`/`3`/`688` → A 股）只是**没有类型信息时的兜底
      猜测**。当名称已经证明「这两个是不同标的」时（股票 002163 海南发展 vs
      基金 002163 东方惠新），前缀的结论就被**推翻**了 —— 若仍照前缀跳过，
      前面那道名称证伪等于白做，持仓直接丢失（少算）。

    为什么仍然保守：
      - 名称缺失（任一侧为空）→ `_looks_like_same_instrument` 返回 True →
        本函数返回 False → 前缀判据照旧生效，**不派生**；
      - code 不在索引里（另一侧没有这条独立持仓）→ 无从证伪 → 返回 False。
      即只有「名称双方都在、且互不包含」这一种强证据才推翻前缀；否则一律
      沿用前缀，宁可少派生一笔，也不凭空造一笔。

    Args:
        code: 待派生标的的代码。
        name: 待派生标的在 V4 流水里的名称（可能为空）。
        other_index: 另一侧独立持仓的 {code: {名称}} 索引。

    Returns:
        True = 已明确证伪为「与另一侧同名代码下的所有标的都不是同一支」。
    """
    names = other_index.get(str(code or "").strip())
    if not names:
        # 另一侧没有这条独立持仓 → 无任何类型证据，交给前缀判据。
        return False
    return not any(_looks_like_same_instrument(name, other_name)
                   for other_name in names)


def _holdings_from_transactions_stock(user_id: str) -> List[Dict]:
    """从 V4 transactions 派生股票持仓（A股代码：6位数字，6/0/3开头）

    ⚠️ 已排除「代码同时存在于独立基金持仓」的条目 —— 详见模块顶部
    「6 位代码命名空间重叠防护」注释，否则 002163 这类重叠代码会被
    既当股票又当基金各算一次（双算）。

    ⚠️ 关于「代码不在 A 股前缀里」的另一类少算：本侧**不放宽**前缀（放宽即
    双算，生产实测 163406 的名称变体会被判成两个标的），改为打告警、不静默
    丢弃。原因见函数内注释与模块顶部「为什么两侧不对称」一节。
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
            is_astock_prefix = (code[0] in ("6", "3") or code.startswith("000")
                                or code.startswith("002") or code.startswith("688"))
            if not is_astock_prefix:
                # ⚠️ 这里**故意不**照基金侧那样用「已证伪为不同标的」去放宽前缀
                # —— 两侧放宽的后果**不对称**：
                #   基金侧放宽 → 该标的只可能被派生成基金；而证伪证据来自独立
                #     股票持仓（非空 ⇒ 股票侧不会回退 V4）→ **不可能双算**；
                #   股票侧放宽 → 证伪证据来自独立基金持仓（非空 ⇒ 基金侧不会
                #     回退 V4，X 已作为基金计入总览）→ 再派生一支同名股票就是
                #     **双算**（这正是上一轮刚修掉的 002163 事故）。
                # 而「同一支基金在两处写法不同」是生产实测存在的：BuLuoGeLi 的
                # 163406 在 V4 流水里同时有「兴全合润混合A」与「兴全合润混合
                # (LOF)A」两种写法，归一化后互不包含 → 会被判成「已证伪为两个
                # 不同标的」。一旦放宽，这 117.99 份基金就会凭空多出一份股票持仓。
                #
                # 所以股票侧维持「只按前缀收股票」，但**不再静默丢弃**：真出现
                # 已证伪为不同标的的情况时打告警交给人工判类型，而不是凭前缀
                # 凭空造一笔持仓。
                if _distinct_from_other_kind(code, h.get("name", ""), known_funds):
                    print(f"[BRIDGE] 疑似少算（已告警、不派生）：V4 的 {code} "
                          f"{h.get('name', '') or '(无名称)'} 已证伪与独立基金持仓里的"
                          "同名标的不同，但代码不在 A 股前缀内；放宽前缀会双算，"
                          "故按保守策略不派生为股票，请人工确认该标的类型。")
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

    ⚠️ 前缀只在「无从证伪」时才生效：若名称已证明它与独立股票持仓里的同名
    代码是**两个不同标的**（股票 002163 海南发展 vs 基金 002163 东方惠新），
    则不再按前缀跳过 —— 否则这支基金会被 `002` 前缀当成 A 股丢掉（少算）。
    详见 `_distinct_from_other_kind`。
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
            # 走到这里说明**没有被认领**，但还分两种：
            #   (a) 另一侧根本没有这个 code → 无类型证据，只能用前缀兜底；
            #   (b) 另一侧有这个 code，但名称已证伪为**另一个标的**
            #       （股票 002163 海南发展 vs 基金 002163 东方惠新）→ 前缀
            #       判据的结论必须让位，否则刚刚证伪出来的基金会被 `002`
            #       前缀当成 A 股跳掉，整笔持仓丢失（少算）。
            # 保守取向不变：名称缺失时 `_distinct_from_other_kind` 返回 False，
            # 前缀照旧生效、不派生。
            distinct_from_stocks = _distinct_from_other_kind(
                code, h.get("name", ""), known_stocks)
            # 跳过 A 股股票
            is_astock = (code.isdigit() and len(code) == 6 and
                         (code[0] in ("6", "3") or code.startswith("000") or
                          code.startswith("002") or code.startswith("688")) and
                         not distinct_from_stocks)
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
