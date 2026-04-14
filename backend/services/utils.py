"""
钱袋子 — 公共工具函数
DataFrame 列名匹配、安全类型转换等
"""



# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "utils",
    "scope": "public",
    "input": [],
    "output": "utility",
    "cost": "cpu",
    "tags": ['工具', '列名匹配', '类型转换'],
    "description": "公共工具函数：DataFrame列名匹配+安全类型转换+NaN清洗",
    "layer": "data",
    "priority": 9,
}
def find_col(cols, keywords):
    """模糊匹配列名"""
    for kw in keywords:
        for c in cols:
            if kw in str(c):
                return c
    return None


def safe_float(val):
    """安全转float，NaN返回None"""
    try:
        v = float(val)
        if v != v:  # NaN
            return None
        return round(v, 2)
    except (ValueError, TypeError):
        return None


def parse_fee(fee_str: str):
    """从费率字符串中提取数值，如 '0.15%' → 0.15"""
    try:
        s = str(fee_str).replace("%", "").strip()
        return float(s)
    except (ValueError, TypeError):
        return None
