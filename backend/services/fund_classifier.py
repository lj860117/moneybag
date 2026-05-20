"""
基金分类工具 — 统一基金类型识别逻辑
职责：
  1. 按基金名称 + 代码识别基金类型（股票/债券/混合/黄金/货币）
  2. 避免代码重复（portfolio_overview.py + risk.py 共用）
  3. 支持显式类型映射 + 关键字推断两种方式
  4. 混合基金返回配置信息供后续比例分配
"""

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
    # 债券
    "217022": "bond", "519736": "bond", "003376": "bond",
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


def classify_and_allocate(code: str = "", name: str = "", nav_cost: float = 0, shares: float = 0) -> dict:
    """
    一步到位：分类基金 + 计算各类别占比金额
    
    Args:
        code: 基金代码
        name: 基金名称
        nav_cost: 基金成本净值
        shares: 持仓份额
    
    Returns:
        {
            "code": code,
            "name": name,
            "type": "equity" | "bond" | "money" | "gold" | "mixed" | "unknown",
            "totalCost": float,  # 总成本金额
            "equity": float,     # 按类别分配的股票占比成本
            "bond": float,
            "money": float,
            "gold": float,
        }
    """
    classification = classify_fund(code, name)
    total_cost = nav_cost * shares if shares > 0 else 0
    
    result = {
        "code": code,
        "name": name,
        "type": classification["type"],
        "totalCost": round(total_cost, 2),
        "equity": 0,
        "bond": 0,
        "money": 0,
        "gold": 0,
    }
    
    fund_type = classification["type"]
    
    if fund_type == "mixed" and "allocation" in classification:
        # 按推断的比例分配
        alloc = classification["allocation"]
        result["equity"] = round(total_cost * alloc.get("equity", 0), 2)
        result["bond"] = round(total_cost * alloc.get("bond", 0), 2)
        result["money"] = round(total_cost * alloc.get("money", 0), 2)
    elif fund_type == "equity":
        result["equity"] = round(total_cost, 2)
    elif fund_type == "bond":
        result["bond"] = round(total_cost, 2)
    elif fund_type == "money":
        result["money"] = round(total_cost, 2)
    elif fund_type == "gold":
        result["gold"] = round(total_cost, 2)
    # unknown 类型所有占比都是 0
    
    return result
