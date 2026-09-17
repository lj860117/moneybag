"""
基金分类工具 — 统一基金类型识别逻辑
职责：
  1. 按基金名称 + 代码识别基金类型（股票/债券/混合/黄金/货币）
  2. 避免代码重复（portfolio_overview.py + risk.py 共用）
  3. 支持显式类型映射 + 关键字推断两种方式
  4. 混合基金返回配置信息供后续比例分配
"""
from typing import Optional

# ============================================================
# 基金类型关键字（完整版，包含混合/QDII/偏股/偏债）
# ============================================================

MONEY_KEYWORDS = ["货币", "money", "余额", "现金", "宝宝", "理财"]
BOND_KEYWORDS = ["债", "bond", "纯债", "信用", "利率", "可转"]
EQUITY_KEYWORDS = ["股票", "混合", "灵活", "配置", "QDII", "偏股", "偏债", "沪深", "创业", "科创", "医药", "消费", "新能源", "半导体", "ETF", "300", "500", "50", "基金"]
GOLD_KEYWORDS = ["黄金", "金ETF", "贵金属"]

# 已知基金代码映射（手动维护常见基金）
KNOWN_FUND_TYPES = {
    # 股票/指数 ETF
    "110020": "equity", "050025": "equity", "008114": "equity",
    "510300": "equity", "510500": "equity", "510050": "equity",
    "159915": "equity", "159919": "equity",
    # 519736 = 交银新成长混合：证监会分类为「偏股混合型」，不是纯债。
    #   此前误记为 bond（docs/HEALTH-CHECK-2026-04-19.md 更误记为「交银裕隆纯债A」），已按
    #   生产 /api/fund/detail/519736 的 name + top_holdings（全为个股）核实并纠正。
    #   归 equity 而非 mixed，是遵循项目既有惯例 risk.py:52 的 mixed → equity 保守归并。
    # ★ 不要改成 "mixed"：本表在 classify_fund() 的「1. 尝试精确代码查询」分支（:68-75）
    #   中优先短路，返回体只含 {type, keywords, is_mixed}，**不带 allocation**；
    #   而 classify_and_allocate
    #   的判据是 `fund_type == "mixed" and "allocation" in classification`，两者叠加会让
    #   equity/bond/money/gold 四桶全为 0，这笔持仓直接从股债配置分母里蒸发 —— 比错成 bond
    #   更糟。要真支持 mixed 档，必须让本表的短路分支一并吐出 allocation。
    "519736": "equity",
    # 债券
    "217022": "bond", "003376": "bond",
    # 黄金
    "000216": "gold", "518880": "gold",
    # 货币
    "000198": "money", "003474": "money",
}


def classify_fund(code: str = "", name: str = "") -> dict:
    """
    分类基金，返回详细信息
    
    Args:
        code: 基金代码（可选）
        name: 基金名称（必需）
    
    Returns:
        {
            "type": "equity" | "bond" | "money" | "gold" | "mixed" | "unknown",
            "keywords": ["matched", "keywords"],
            "allocation": {"equity": 0.6, "bond": 0.3, "money": 0.1},  # 仅当 type=mixed 时
            "is_mixed": bool,  # 混合基金标记
        }
    
    Examples:
        >>> classify_fund(code="000001", name="华夏成长混合")
        {'type': 'equity', 'keywords': ['混合'], 'allocation': {'equity': 0.7, 'bond': 0.2, 'money': 0.1}}
        
        >>> classify_fund(code="", name="QDII美元现钞")
        {'type': 'equity', 'keywords': ['QDII'], 'is_mixed': False, ...}
    """
    
    # 1. 尝试精确代码查询
    if code and code in KNOWN_FUND_TYPES:
        fund_type = KNOWN_FUND_TYPES[code]
        return {
            "type": fund_type,
            "keywords": [],
            "is_mixed": False,
        }
    
    # 2. 按名称关键字分类
    name_lower = name.lower()
    matched_keywords = []
    
    # 检查货币基金
    for kw in MONEY_KEYWORDS:
        if kw in name_lower:
            matched_keywords.append(kw)
    if matched_keywords:
        return {
            "type": "money",
            "keywords": matched_keywords,
            "is_mixed": False,
        }
    
    # 检查黄金
    for kw in GOLD_KEYWORDS:
        if kw in name_lower:
            matched_keywords.append(kw)
    if matched_keywords:
        return {
            "type": "gold",
            "keywords": matched_keywords,
            "is_mixed": False,
        }
    
    # 检查债券（需要排除"可转债"中的债字被混合基金误匹配）
    has_bond_kw = False
    for kw in BOND_KEYWORDS:
        if kw in name_lower:
            has_bond_kw = True
            matched_keywords.append(kw)
    
    # 检查混合/灵活关键字
    has_mixed_kw = False
    for kw in ["混合", "灵活", "配置", "QDII", "偏股", "偏债"]:
        if kw in name:
            has_mixed_kw = True
            matched_keywords.append(kw)
    
    # 如果有混合关键字，返回 mixed 类型 + 推断的配置
    if has_mixed_kw:
        allocation = _infer_mixed_allocation(name, matched_keywords)
        return {
            "type": "mixed",
            "keywords": matched_keywords,
            "is_mixed": True,
            "allocation": allocation,  # 仅 mixed 类型有此字段
        }
    
    # 如果只有债券关键字
    if has_bond_kw:
        return {
            "type": "bond",
            "keywords": matched_keywords,
            "is_mixed": False,
        }
    
    # 检查其他股票关键字（包括"基金"通用词）
    for kw in EQUITY_KEYWORDS:
        if kw in name_lower:
            matched_keywords.append(kw)
    
    if matched_keywords:
        return {
            "type": "equity",
            "keywords": matched_keywords,
            "is_mixed": False,
        }
    
    # A 股股票基金代码默认为股票型
    if code and code.isdigit() and len(code) == 6:
        if code[0] in ("6", "3") or code.startswith("000") or code.startswith("002"):
            return {
                "type": "equity",
                "keywords": ["code_pattern"],
                "is_mixed": False,
            }
    
    # 默认未知
    return {
        "type": "unknown",
        "keywords": [],
        "is_mixed": False,
    }


def _infer_mixed_allocation(name: str, keywords: list) -> dict:
    """
    对混合基金推断权益/债券/现金配置比例
    
    启发式规则：
    - 名字中有"偏股" → 股票 70%
    - 名字中有"偏债" → 债券占比大
    - 名字中有"灵活配置" → 均衡配置 60/30/10
    - 其他混合 → 保守混合 50/35/15
    """
    name_lower = name.lower()
    
    if "偏股" in name or "股债" in name:
        # 偏股混合：股票占比 70%
        return {"equity": 0.70, "bond": 0.20, "money": 0.10}
    elif "偏债" in name:
        # 偏债混合：债券占比 60%
        return {"equity": 0.25, "bond": 0.60, "money": 0.15}
    elif "灵活配置" in name or "灵活" in name:
        # 灵活配置：均衡配置
        return {"equity": 0.60, "bond": 0.30, "money": 0.10}
    elif "QDII" in name:
        # QDII 通常混合配置
        return {"equity": 0.65, "bond": 0.25, "money": 0.10}
    else:
        # 保守型混合基金
        return {"equity": 0.50, "bond": 0.35, "money": 0.15}


def classify_and_allocate(
    code: str = "",
    name: str = "",
    nav_cost: float = 0,
    shares: float = 0,
    nav_current: Optional[float] = None,
) -> dict:
    """
    一步到位：分类基金 + 计算各类别占比金额

    分配基数（**口径**）说明 —— 2026-09 修订：
      本函数历史上只有一个基数，即「成本口径」`total_cost = nav_cost * shares`。
      当它被 portfolio_overview 用于「资产配置占比」时，会与同一分母里的
      股票市值（实时价 × 股数）口径不一致：资产涨了，基金部分仍按成本计价，
      配置占比就被低估。因此新增可选参数 `nav_current` 支持「市值口径」。

      向后兼容是硬约束：
        - `nav_current` 不传（None）或非正数 → 基数 = nav_cost * shares，
          即**完全维持旧语义**，既有调用点与既有断言不受影响；
        - 显式传入正的 `nav_current` → 基数 = nav_current * shares（市值口径）。
      注意「传了实时净值、但实时净值取不到」的调用点应当回落到
      `nav_current = nav_cost` 再传入，此时两种口径**数值等价**（退化路径连续）。

    Args:
        code: 基金代码
        name: 基金名称
        nav_cost: 基金成本净值（元/份）
        shares: 持仓份额
        nav_current: 基金当前净值（元/份）。可选；不传或不大于 0 时按成本口径分配。

    Returns:
        {
            "code": code,
            "name": name,
            "type": "equity" | "bond" | "money" | "gold" | "mixed" | "unknown",
            "totalCost": float,   # 总成本金额（恒为 nav_cost * shares）
            "totalValue": float,  # 实际参与分配的基数金额（成本或市值口径）
            "navCurrent": float | None,  # 生效的当前净值（未传则为 None）
            "basis": "cost" | "market",  # 本次实际使用的口径
            "equity": float,      # 按类别分配的股票占比金额
            "bond": float,
            "money": float,
            "gold": float,
        }
    """
    classification = classify_fund(code, name)
    total_cost = nav_cost * shares if shares > 0 else 0

    # 口径选择：只有「显式传入正的当前净值 + 有份额」才切到市值口径，
    # 其余情况一律退化到成本口径（含 nav_current=None / 0 / 负数 / shares<=0）。
    use_market = (
        nav_current is not None
        and nav_current > 0
        and shares > 0
    )
    basis = "market" if use_market else "cost"
    total_value = (nav_current * shares) if use_market else total_cost

    result = {
        "code": code,
        "name": name,
        "type": classification["type"],
        "totalCost": round(total_cost, 2),
        "totalValue": round(total_value, 2),
        "navCurrent": nav_current if use_market else None,
        "basis": basis,
        "equity": 0,
        "bond": 0,
        "money": 0,
        "gold": 0,
    }

    fund_type = classification["type"]

    if fund_type == "mixed" and "allocation" in classification:
        # 按推断的比例分配
        alloc = classification["allocation"]
        result["equity"] = round(total_value * alloc.get("equity", 0), 2)
        result["bond"] = round(total_value * alloc.get("bond", 0), 2)
        result["money"] = round(total_value * alloc.get("money", 0), 2)
    elif fund_type == "equity":
        result["equity"] = round(total_value, 2)
    elif fund_type == "bond":
        result["bond"] = round(total_value, 2)
    elif fund_type == "money":
        result["money"] = round(total_value, 2)
    elif fund_type == "gold":
        result["gold"] = round(total_value, 2)
    # unknown 类型所有占比都是 0

    return result
