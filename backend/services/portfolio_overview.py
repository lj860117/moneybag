"""
资产总览引擎 — 独立 service
职责：
  1. 汇总股票+基金+其他资产的净资产
  2. 计算资产配置占比（股/债/现）
  3. 偏离度检测
  4. 为持仓页提供统一 Hero 数据
"""
import json
from pathlib import Path
from datetime import datetime

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "portfolio_overview",
    "scope": "private",
    "input": ["user_id"],
    "output": "overview",
    "cost": "cpu",
    "tags": ["组合总览", "配置占比", "偏离度"],
    "description": "资产总览：股票+基金汇总净资产+配置占比+偏离度检测",
    "layer": "data",
    "priority": 2,
}

# ---- 导入各持仓模块 ----
# FIX 2026-04-19 F3: 改用 holdings_bridge 统一两套存储（独立文件 + V4 transactions）
from services.holdings_bridge import unified_load_stock_holdings, unified_load_fund_holdings
from services.stock_monitor import scan_all_holdings
from services.fund_monitor import scan_all_fund_holdings
# FIX 2026-05-20 MB-008: 导入统一的基金分类器，避免代码重复
from services.fund_classifier import classify_and_allocate
# FIX 2026-09-18（现金漏计）: 复用 unified_networth 的资产金额口径与账户现金分桶，
#   避免在 overview 里再写第三套 value/balance 解析。
from services.unified_networth import asset_amount, load_cash_assets


# ---- 账户现金去重（防"基金内货币份额"与"账户现金"双算）----

# 名称包含匹配时，较短一方必须达到的最小长度。
#   阈值取 3 是权衡后的结果：
#     - 「余额宝」(3) ⊂ 「天弘余额宝货币」 → 命中，正确去重；
#     - 「现金」/「活期」/「存款」(均 2 字) 这类**通用**现金名会被放过 ——
#       否则「现金」⊂「华夏现金增利货币」会把一笔真实存款误判为重复而吞掉。
#   这是一个**名称启发式**，不是精确键：Asset schema 里没有 code 字段
#   （见 models/schemas.py:46 与 pages/assets.js:246），无法按基金代码对齐。
_MIN_DEDUP_NAME_LEN = 3


def _normalize_name(name) -> str:
    """归一化名称用于去重比较：去掉所有空白 + 转小写。"""
    return "".join(str(name or "").split()).lower()


def _is_duplicate_account_cash(asset: dict, money_fund_codes: set,
                               money_fund_names: set) -> bool:
    """判断一笔账户现金是否已被基金持仓的货币类份额统计过（防双算）。

    背景：`fund_money` 是**基金持仓内部**的货币类份额（含纯货基与混合基的
    现金比例）；而账户现金（assets[type=cash]）常常记录的就是同一只货基
    （如余额宝）。两处都录会造成双算。

    去重键（按可靠性降序）：
      1. `code`（精确）：若资产带 code（未来 schema 扩展）直接比对基金代码；
      2. 归一化名称：相等，或一方包含另一方且**较短方 >= _MIN_DEDUP_NAME_LEN**。

    Args:
        asset: 一笔账户现金资产 dict。
        money_fund_codes: 已计入 fund_money 的基金代码集合。
        money_fund_names: 已计入 fund_money 的基金归一化名称集合。

    Returns:
        True 表示该资产视为重复，不应再计入账户现金。
    """
    code = str(asset.get("code") or "").strip()
    if code and code in money_fund_codes:
        return True

    nm = _normalize_name(asset.get("name"))
    if len(nm) < _MIN_DEDUP_NAME_LEN:
        return False
    for mn in money_fund_names:
        if len(mn) < _MIN_DEDUP_NAME_LEN:
            continue
        if nm == mn or nm in mn or mn in nm:
            return True
    return False



def get_portfolio_overview(user_id: str = "default") -> dict:
    """汇总全资产，返回统一概览数据"""
    # 1. 股票持仓 — 用实时价格计算市值
    # FIX 2026-08-09: 排查代码漂移发现本地版本这里退化成"直接用成本价当市值"
    # （注释写"需要scan才有"但从未真正调用scan/批量取价），导致资产总览的
    # 股票市值/配置占比在涨跌幅大时明显失真。合并回服务器版本的实时取价逻辑。
    stock_holdings = unified_load_stock_holdings(user_id)
    stock_total_mv = 0
    stock_total_cost = 0
    stock_count = 0

    # 批量获取实时价格
    stock_rt_map = {}
    try:
        from services.stock_monitor import get_stock_realtime
        for h in stock_holdings:
            code = h.get("code", "")
            if code:
                rt = get_stock_realtime(code)
                if rt and rt.get("price"):
                    stock_rt_map[code] = rt["price"]
    except Exception:
        pass

    for h in stock_holdings:
        cost_price = h.get("costPrice", 0) or 0
        shares = h.get("shares", 0) or 0
        code = h.get("code", "")
        if cost_price and shares:
            stock_total_cost += cost_price * shares
            stock_count += 1
        # 用实时价格，没有就用成本价
        current_price = stock_rt_map.get(code) or cost_price
        stock_total_mv += current_price * shares

    # 2. 基金持仓 — FIX 2026-05-20 MB-008: 使用新的分类器，支持混合/QDII 基金
    fund_holdings = unified_load_fund_holdings(user_id)
    fund_total_mv = 0
    fund_total_cost = 0
    fund_count = 0
    fund_equity = 0      # 基金中的股票类占比
    fund_bond = 0        # 基金中的债券类占比
    fund_money = 0       # 基金中的现金类占比
    fund_gold = 0        # 基金中的黄金占比
    # FIX 2026-09-18（现金漏计）: 记录「已计入 fund_money 的持仓」身份，
    #   用于与账户现金去重（见模块顶部 _is_duplicate_account_cash）。
    money_fund_codes: set = set()
    money_fund_names: set = set()

    for h in fund_holdings:
        cost_nav = h.get("costNav", 0) or 0
        shares = h.get("shares", 0) or 0
        cost = cost_nav * shares
        fund_total_cost += cost
        # 优先用实时净值计算市值，fallback 到成本净值
        current_nav = cost_nav
        code = h.get("code", "")
        if code:
            try:
                from services.market_data import get_fund_nav as _get_nav
                nav_info = _get_nav(code)
                _raw_nav = nav_info.get("nav") if nav_info else None
                # 净值有效性校验：必须能解析成**正数**。
                # FIX 2026-09-18（独立复验反证命中）: 原先只排除 "N/A"/None/""，
                #   于是停牌或异常返回的 "0.0000"/0.0 会**通过**校验，把
                #   current_nav 置 0。后果是双头不一致：
                #     - 本行下方 fund_total_mv += 0 → 基金市值整体归零；
                #     - 而 classify_and_allocate 见 nav_current<=0 会退回成本口径
                #       （use_market=False），配置占比仍按成本算。
                #   两边一个归零一个照旧 → total_for_alloc 与 total_mv 相差
                #   整只基金市值（实测差 709.19）。负数同理。
                #   改为要求 > 0：无效净值一律退回 cost_nav，让「市值」与「分配」
                #   两个口径**同时**退化到成本、保持自洽（这正是本次口径统一
                #   想建立的不变量）。
                # 已知限制（刻意不加上界）: 分红/拆分会造成合法的大幅净值变动，
                #   硬上界会误杀真数据；故若上游返回**偏大但为正**的错值，
                #   配置占比会随之偏 —— 属净值数据质量的暴露面，非本函数可闭合。
                if _raw_nav not in ("N/A", None, ""):
                    _nav_f = float(_raw_nav)
                    if _nav_f > 0:
                        current_nav = _nav_f
            except Exception:
                pass
        fund_total_mv += current_nav * shares
        fund_count += 1

        # 使用新分类器，支持混合/QDII 基金的比例分配
        # FIX 2026-09-18 (口径统一): 传入上面已算好的 current_nav，让分配基数走**市值口径**，
        #   与 stock_total_mv（:67 实时价）对齐，见下方 :128-136 的说明。
        #   注意 current_nav 在拿不到实时净值时已退化为 cost_nav（:86），
        #   故此处即便退化为成本口径也与旧行为完全一致（不重复拉行情）。
        allocation = classify_and_allocate(
            code=h.get("code", ""),
            name=h.get("name", ""),
            nav_cost=h.get("costNav", 0),
            shares=h.get("shares", 0),
            nav_current=current_nav,
        )
        fund_equity += allocation["equity"]
        fund_bond += allocation["bond"]
        fund_money += allocation["money"]
        fund_gold += allocation["gold"]
        # 记录贡献了 fund_money 的持仓身份（纯货基 + 混合基的现金比例），
        # 供账户现金去重时使用。
        if allocation["money"] > 0:
            _mc = str(h.get("code", "") or "").strip()
            if _mc:
                money_fund_codes.add(_mc)
            _mn = _normalize_name(h.get("name"))
            if _mn:
                money_fund_names.add(_mn)

    # 2b. 账户现金（资产管理页录入的存款/活期/余额宝等）
    # FIX 2026-09-18（现金漏计）：闭环此前挂在第 4 节注释里的遗留 TODO ——
    #   此前 `cash = fund_money` 只统计**基金持仓内部**的货币类份额，账户里
    #   真正的现金余额（user.portfolio.assets[] 中 type == "cash"）完全没进
    #   allocation —— 后果是现金被系统性低估、误报「现金欠配」并给出错误的
    #   增持建议。
    #   ⚠️ 口径一致性（本项目刚踩过的坑）: 加了现金进 cash 桶**必须同步计入
    #   分母** total_for_alloc。只改分子不改分母就是又一次「分子分母口径分裂」
    #   （参见 2026-09-18 基金分配成本/市值口径混用那处修复）。
    #   ⚠️ 双算风险: fund_money 可能已含同一只货基（余额宝既是基金持仓又可能
    #   被录成现金资产），故按代码/名称去重（_is_duplicate_account_cash）。
    account_cash = 0.0
    account_cash_deduped = 0.0
    for _a in load_cash_assets(user_id):
        _amt = asset_amount(_a)
        if _is_duplicate_account_cash(_a, money_fund_codes, money_fund_names):
            account_cash_deduped += _amt
            continue
        account_cash += _amt

    # 3. 总资产
    total_mv = stock_total_mv + fund_total_mv
    total_cost = stock_total_cost + fund_total_cost
    total_pnl = total_mv - total_cost if total_cost > 0 else 0
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    # 4. 资产配置占比（股票类/债券类/现金类/黄金）
    #
    # FIX 2026-09-15 (gold 桶): gold 现在是**独立第 4 档**，不再并进 equity。
    #   旧代码是 `equity = stock_total_mv + fund_equity + fund_gold`，即分类侧
    #   （fund_classifier.KNOWN_FUND_TYPES 有 000216/518880 → "gold"）分出来的
    #   第 4 档，在展示层被显式吞进 equity，allocation 输出只有 3 个键。
    #   这与同一次 MB-008 提交里的 risk.py:172
    #       `has_hedge = bond_n > 0 or gold_n > 0`
    #   直接矛盾 —— 风控侧把黄金当权益的**对冲资产**，这里却把它当**权益本身**。
    #   二者不可能同时成立，故判定为 bug：归入 equity 是错的。
    #
    # FIX 2026-09-18（口径统一，闭环原 TODO): 此前 fund_equity/bond/money/gold 走**成本**口径
    #   （fund_classifier.classify_and_allocate 的基数 `total_cost = nav_cost * shares`），
    #   而 stock_total_mv 走的是**市值**口径（本文件上方 :67 用实时价），两者却被加进同一个
    #   total_for_alloc —— 结果是「资产涨了，基金那部分仍按买入成本计价」，配置占比被系统性
    #   低估（未实现浮盈越大的持仓，低估越明显），而 totalMarketValue 又是市值口径，
    #   同一份 overview 里两个口径自相矛盾。
    #
    #   改法：由本文件把 :86-96 已经算好的 `current_nav`（优先实时净值、取不到才 fallback
    #   到 cost_nav）作为可选参数 `nav_current` 传给 classify_and_allocate，使基金分配基数
    #   切到市值口径 `nav_current * shares`。**不新增行情请求**，复用已有结果。
    #
    #   为什么安全：
    #     - `nav_current` 是该函数的新增**可选**参数，不传时基数仍为 nav_cost * shares，
    #       即旧语义原样保留（test_fund_classifier*.py 的既有断言不受影响）。
    #     - current_nav 在实时净值取不到时等于 cost_nav（:86 的初始化 + :92 的条件赋值），
    #       故退化路径下新旧口径**数值等价**，不存在断崖。
    #     - 改后不变量：无现金等其他资产时 total_for_alloc == total_mv；基金四桶之和
    #       == fund_total_mv。新增测试 test_portfolio_overview_caliber.py 守卫这两条。
    #
    #   影响面：仅 portfolio_overview 这一处生产调用点（Grep 确认 classify_and_allocate
    #   的其余命中全在 backend/tests/）。allocation 四档百分比、deviation、rebalance
    #   金额与 direction 会随之变化（分母由成本变为市值）；healthScore 仅在偏离跨过
    #   10/20 阈值时才可能变动。
    #
    #   （原 TODO「cash 只统计基金内货币份额」已于 2026-09-18 闭环：见上方 2b 段
    #    与下方 cash 桶 / total_for_alloc 的 FIX 注释。）
    equity = stock_total_mv + fund_equity
    bond = fund_bond
    # FIX 2026-09-18（现金漏计已闭环）: cash 桶 = 基金内货币类份额 + 账户真实现金。
    #   账户现金已在上方 2b 段按代码/名称与基金持仓去重，避免余额宝双算。
    cash = fund_money + account_cash
    gold = fund_gold
    # 分母口径：
    #   - 2026-09-15（gold 拆桶）时分母不变 —— gold 只是从 equity 挪出来单列，
    #     仍计入总配置口径，故当时的整体市值分母不变。
    #   - FIX 2026-09-18（现金口径统一）: 账户现金并入 cash 后，分母也必须
    #     + 账户现金，否则「分子含现金、分母不含」= 口径分裂。新的不变量
    #     （取代旧的 total_for_alloc == total_mv，仅在无账户现金时两者才相等）：
    #         total_for_alloc == totalMarketValue + 账户现金(accountCash)
    #   语义决策：totalMarketValue **保持**「投资持仓市值（股票+基金）」不变 ——
    #   前端 pages/stocks.js:42 明确把它标注为「总持仓资产 仅股票+基金」，
    #   monthly_rebalance_cron 也按它是投资市值来用。账户现金只通过 cash 桶、
    #   分母、以及新增字段 accountCash 暴露，不污染 totalMarketValue。
    total_for_alloc = equity + bond + cash + gold

    allocation = {
        "equity": round(equity / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "bond": round(bond / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "cash": round(cash / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "gold": round(gold / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
    }

    # 默认目标配置（基于稳健型）+ 黄金独立第 4 档
    # 黄金目标取 domain/rule_engine/glide_path_rules.py:35 GOLD_PCT_DEFAULT = 0.05，
    # 与该表各年龄档 gold 恒为 5 一致。该表 stock_pct 的注释明确写
    # 「股票目标占比（**已扣除黄金**）」，因此从原 equity 50 中扣出 5 给 gold，
    # 四档合计仍为 100。
    target = {"equity": 45, "bond": 30, "cash": 20, "gold": 5}
    deviation = {
        "equity": round(allocation["equity"] - target["equity"], 1),
        "bond": round(allocation["bond"] - target["bond"], 1),
        "cash": round(allocation["cash"] - target["cash"], 1),
        "gold": round(allocation["gold"] - target["gold"], 1),
    }

    # 5. 健康评分（简化版 Seeking Alpha Health Score）
    health_score = 100
    health_issues = []

    # 集中度检查
    if stock_count + fund_count > 0:
        if stock_count + fund_count < 3:
            health_score -= 20
            health_issues.append("持仓过于集中（<3 只），建议分散")
        if stock_count + fund_count > 20:
            health_score -= 10
            health_issues.append("持仓过多（>20 只），难以跟踪")

    # 配置偏离检查（含黄金第 4 档）
    max_dev = max(abs(deviation["equity"]), abs(deviation["bond"]),
                  abs(deviation["cash"]), abs(deviation["gold"]))
    if max_dev > 20:
        health_score -= 25
        health_issues.append(f"资产配置严重偏离目标（最大偏离 {max_dev}%）")
    elif max_dev > 10:
        health_score -= 10
        health_issues.append(f"资产配置偏离目标（{max_dev}%），建议再平衡")

    # 全部是股票类
    if total_for_alloc > 0 and allocation["equity"] > 90:
        health_score -= 15
        health_issues.append("几乎全部是权益类，缺乏防御性配置")

    health_score = max(0, health_score)
    health_grade = "🟢 健康" if health_score >= 80 else "🟡 一般" if health_score >= 60 else "🔴 需调整"

    # 6. 再平衡建议
    rebalance = []
    if total_for_alloc > 0:
        for asset, label in [("equity", "股票类"), ("bond", "债券类"),
                             ("cash", "现金类"), ("gold", "黄金类")]:
            d = deviation[asset]
            if abs(d) > 10:
                direction = "reduce" if d > 0 else "increase"
                emoji = "📉" if d > 0 else "📈"
                amount = abs(d) / 100 * total_for_alloc
                rebalance.append({
                    "asset": asset,
                    "label": label,
                    "direction": direction,
                    "deviation": d,
                    "amount": round(amount, 0),
                    "message": f"{emoji} {label}{'超配' if d > 0 else '欠配'}{abs(d):.0f}%，"
                               f"建议{'减持' if d > 0 else '增持'} ¥{amount:,.0f}",
                })

    return {
        # totalMarketValue 语义 = 投资持仓市值（股票 + 基金），**不含**账户现金与
        # 房产/车辆等其他资产。前端 pages/stocks.js 据此展示「总持仓资产 仅股票+基金」。
        "totalMarketValue": round(total_mv, 2),
        # 配置分母 = totalMarketValue + accountCash（账户现金）。等价于
        # allocation 四桶金额之和，供调用方复用统一口径（如 /api/allocation）时取用。
        "totalForAllocation": round(total_for_alloc, 2),
        # 本次计入 cash 桶的账户真实现金（已与基金内货币份额去重）。
        "accountCash": round(account_cash, 2),
        # 因与基金持仓货币份额重名/同代码而被去重、**未**计入的账户现金（透明度用）。
        "accountCashDeduped": round(account_cash_deduped, 2),
        "totalCost": round(total_cost, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl_pct, 2),
        "stockCount": stock_count,
        "fundCount": fund_count,
        "stockValue": round(stock_total_mv, 2),
        "fundValue": round(fund_total_mv, 2),
        "allocation": allocation,
        "target": target,
        "deviation": deviation,
        "healthScore": health_score,
        "healthGrade": health_grade,
        "healthIssues": health_issues,
        "rebalance": rebalance,
        "updatedAt": datetime.now().isoformat(),
    }
