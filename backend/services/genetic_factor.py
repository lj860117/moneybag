"""
钱袋子 — 遗传编程因子挖掘 V1
自动发现人类难以想到的 Alpha 因子

原理：
  1. 定义操作符集合（数学运算 + 时序函数）
  2. 随机生成因子表达式树
  3. 用 IC（Information Coefficient）作为适应度函数
  4. 遗传算法迭代：选择、交叉、变异
  5. 每轮淘汰低 IC 因子，保留高 IC 因子

操作符：
  - 数学: add, sub, mul, div(安全), abs, neg, log(安全), sqrt(安全)
  - 时序: ts_mean(x,w), ts_std(x,w), ts_rank(x,w), ts_delay(x,d), ts_delta(x,d)
  - 截面: rank(x), zscore(x)

参考：
  - 幻方量化遗传算法因子挖掘
  - WorldQuant 101 Alphas
  - gplearn 设计思路（但我们用纯 Python 实现，不依赖第三方 GP 库）
"""
import time
import math

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "genetic_factor",
    "scope": "public",
    "input": ["stock_code"],
    "output": "evolved_factors",
    "cost": "cpu",
    "tags": ["因子挖掘", "遗传算法", "Alpha"],
    "description": "表达式树+遗传进化200×30代，自动发现Alpha因子",
    "layer": "analysis",
    "priority": 5,
}
import random
import traceback
import numpy as np
from concurrent.futures import ThreadPoolExecutor

_gf_cache = {}
_GF_CACHE_TTL = 86400  # 24 小时


# ============================================================
# 安全数学函数
# ============================================================

def _safe_div(a, b):
    """安全除法，避免除以零"""
    return np.where(np.abs(b) < 1e-10, 0.0, a / b)

def _safe_log(x):
    """安全对数"""
    return np.log(np.maximum(np.abs(x), 1e-10))

def _safe_sqrt(x):
    """安全平方根"""
    return np.sqrt(np.maximum(np.abs(x), 0))


# ============================================================
# 表达式节点
# ============================================================

class Node:
    """表达式树节点"""
    def __init__(self, op, children=None, param=None, field=None):
        self.op = op            # 操作类型
        self.children = children or []
        self.param = param      # 窗口参数（如 ts_mean 的 window）
        self.field = field      # 叶节点字段名

    def evaluate(self, data: dict, n: int) -> np.ndarray:
        """递归求值，data = {field_name: np.array}"""
        if self.op == "field":
            return data.get(self.field, np.zeros(n))

        if self.op == "const":
            return np.full(n, self.param)

        # 一元操作
        if self.op in ("abs", "neg", "log", "sqrt", "rank", "zscore"):
            x = self.children[0].evaluate(data, n)
            if self.op == "abs":
                return np.abs(x)
            elif self.op == "neg":
                return -x
            elif self.op == "log":
                return _safe_log(x)
            elif self.op == "sqrt":
                return _safe_sqrt(x)
            elif self.op == "rank":
                # 截面排名（这里简化为时序排名）
                ranks = np.zeros(n)
                for i in range(n):
                    ranks[i] = np.searchsorted(np.sort(x[:i+1]), x[i]) / max(i, 1)
                return ranks
            elif self.op == "zscore":
                m = np.mean(x)
                s = np.std(x)
                return (x - m) / (s + 1e-10)

        # 二元操作
        if self.op in ("add", "sub", "mul", "div"):
            a = self.children[0].evaluate(data, n)
            b = self.children[1].evaluate(data, n)
            if self.op == "add":
                return a + b
            elif self.op == "sub":
                return a - b
            elif self.op == "mul":
                return a * b
            elif self.op == "div":
                return _safe_div(a, b)

        # 时序操作
        if self.op in ("ts_mean", "ts_std", "ts_rank", "ts_delay", "ts_delta"):
            x = self.children[0].evaluate(data, n)
            w = self.param or 10
            result = np.full(n, np.nan)

            for i in range(w, n):
                window = x[i-w+1:i+1]
                if self.op == "ts_mean":
                    result[i] = np.mean(window)
                elif self.op == "ts_std":
                    result[i] = np.std(window)
                elif self.op == "ts_rank":
                    result[i] = np.searchsorted(np.sort(window), x[i]) / w
                elif self.op == "ts_delay":
                    d = min(w, i)
                    result[i] = x[i - d]
                elif self.op == "ts_delta":
                    d = min(w, i)
                    result[i] = x[i] - x[i - d]

            return np.nan_to_num(result, nan=0.0)

        return np.zeros(n)

    def to_string(self) -> str:
        """表达式字符串"""
        if self.op == "field":
            return self.field
        if self.op == "const":
            return str(round(self.param, 3))
        if self.op in ("abs", "neg", "log", "sqrt", "rank", "zscore"):
            return f"{self.op}({self.children[0].to_string()})"
        if self.op in ("add", "sub", "mul", "div"):
            ops = {"add": "+", "sub": "-", "mul": "*", "div": "/"}
            return f"({self.children[0].to_string()} {ops[self.op]} {self.children[1].to_string()})"
        if self.op in ("ts_mean", "ts_std", "ts_rank", "ts_delay", "ts_delta"):
            return f"{self.op}({self.children[0].to_string()}, {self.param})"
        return "?"

    def depth(self) -> int:
        if not self.children:
            return 1
        return 1 + max(c.depth() for c in self.children)


# ============================================================
# 随机因子生成
# ============================================================

FIELDS = ["close", "open", "high", "low", "volume", "returns"]
UNARY_OPS = ["abs", "neg", "log", "sqrt", "rank", "zscore"]
BINARY_OPS = ["add", "sub", "mul", "div"]
TS_OPS = ["ts_mean", "ts_std", "ts_rank", "ts_delay", "ts_delta"]
TS_WINDOWS = [5, 10, 20, 60]
MAX_DEPTH = 4


def _random_tree(depth=0) -> Node:
    """随机生成表达式树"""
    if depth >= MAX_DEPTH or (depth > 1 and random.random() < 0.3):
        # 叶节点
        if random.random() < 0.85:
            return Node("field", field=random.choice(FIELDS))
        else:
            return Node("const", param=random.uniform(-2, 2))

    r = random.random()
    if r < 0.25:
        # 一元操作
        op = random.choice(UNARY_OPS)
        child = _random_tree(depth + 1)
        return Node(op, children=[child])
    elif r < 0.55:
        # 二元操作
        op = random.choice(BINARY_OPS)
        left = _random_tree(depth + 1)
        right = _random_tree(depth + 1)
        return Node(op, children=[left, right])
    else:
        # 时序操作
        op = random.choice(TS_OPS)
        child = _random_tree(depth + 1)
        window = random.choice(TS_WINDOWS)
        return Node(op, children=[child], param=window)


# ============================================================
# 遗传操作
# ============================================================

def _crossover(parent1: Node, parent2: Node) -> Node:
    """交叉：随机替换子树"""
    import copy
    child = copy.deepcopy(parent1)
    donor = copy.deepcopy(parent2)

    # 找到 child 中的随机可替换节点
    def _find_nodes(node, nodes=None):
        if nodes is None:
            nodes = []
        nodes.append(node)
        for c in node.children:
            _find_nodes(c, nodes)
        return nodes

    child_nodes = _find_nodes(child)
    donor_nodes = _find_nodes(donor)

    if len(child_nodes) > 1 and len(donor_nodes) > 0:
        target = random.choice(child_nodes[1:])  # 不替换根节点
        source = random.choice(donor_nodes)
        target.op = source.op
        target.children = source.children
        target.param = source.param
        target.field = source.field

    return child


def _mutate(node: Node) -> Node:
    """变异：随机修改一个节点"""
    import copy
    mutated = copy.deepcopy(node)

    def _find_nodes(n, nodes=None):
        if nodes is None:
            nodes = []
        nodes.append(n)
        for c in n.children:
            _find_nodes(c, nodes)
        return nodes

    nodes = _find_nodes(mutated)
    target = random.choice(nodes)

    r = random.random()
    if r < 0.3 and target.op == "field":
        # 换字段
        target.field = random.choice(FIELDS)
    elif r < 0.6 and target.op in TS_OPS:
        # 换窗口
        target.param = random.choice(TS_WINDOWS)
    elif r < 0.8 and target.children:
        # 替换一个子节点
        new_subtree = _random_tree(depth=2)
        idx = random.randint(0, len(target.children) - 1)
        target.children[idx] = new_subtree
    else:
        # 用新随机树替换
        new = _random_tree(depth=1)
        target.op = new.op
        target.children = new.children
        target.param = new.param
        target.field = new.field

    return mutated


# ============================================================
# 适应度评估（IC）
# ============================================================

def _calc_ic(factor_values: np.ndarray, forward_returns: np.ndarray) -> float:
    """Spearman IC（秩相关系数）"""
    valid = np.isfinite(factor_values) & np.isfinite(forward_returns)
    fv = factor_values[valid]
    fr = forward_returns[valid]

    if len(fv) < 30:
        return 0.0

    # Spearman 秩相关
    from scipy.stats import spearmanr
    ic, _ = spearmanr(fv, fr)
    return float(ic) if np.isfinite(ic) else 0.0


def _evaluate_fitness(tree: Node, data: dict, forward_returns: np.ndarray) -> float:
    """评估因子的 IC 适应度"""
    try:
        n = len(forward_returns)
        factor_values = tree.evaluate(data, n)

        # 检查有效性
        if np.all(np.isnan(factor_values)) or np.std(factor_values[np.isfinite(factor_values)]) < 1e-10:
            return 0.0

        ic = _calc_ic(factor_values, forward_returns)

        # 惩罚过深的树（复杂度惩罚）
        depth_penalty = max(0, tree.depth() - 3) * 0.005
        return abs(ic) - depth_penalty

    except Exception:
        return 0.0


# ============================================================
# 数据准备
# ============================================================

def _prepare_data(code: str, days: int = 800) -> tuple:
    """获取股票数据，返回 (data_dict, forward_returns)"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if df is None or len(df) < 200:
            return None, None

        df = df.tail(days)
        n = len(df)

        close = df["收盘"].values.astype(np.float64)
        open_ = df["开盘"].values.astype(np.float64)
        high = df["最高"].values.astype(np.float64)
        low = df["最低"].values.astype(np.float64)
        volume = df["成交量"].values.astype(np.float64)
        returns = np.zeros(n)
        returns[1:] = np.diff(np.log(close))

        data = {
            "close": close,
            "open": open_,
            "high": high,
            "low": low,
            "volume": volume,
            "returns": returns,
        }

        # 未来 5 天收益率作为标签
        fwd_ret = np.full(n, np.nan)
        for i in range(n - 5):
            fwd_ret[i] = (close[i + 5] / close[i]) - 1.0

        return data, fwd_ret

    except Exception:
        traceback.print_exc()
        return None, None


# ============================================================
# 主进化函数
# ============================================================

def evolve_factors(
    code: str = "000001",
    population_size: int = 200,
    generations: int = 30,
    top_k: int = 10,
) -> dict:
    """
    对单只股票运行遗传编程，挖掘 Alpha 因子

    参数：
      code: 股票代码（默认平安银行，样本充足）
      population_size: 种群大小
      generations: 进化代数
      top_k: 返回 top-K 因子

    返回：
      top_k 个因子的表达式、IC 值、评级
    """
    cache_key = f"gf_{code}_{generations}"
    now = time.time()
    if cache_key in _gf_cache and now - _gf_cache[cache_key]["ts"] < _GF_CACHE_TTL:
        return _gf_cache[cache_key]["data"]

    try:
        data, fwd_ret = _prepare_data(code)
        if data is None:
            return {"error": "数据获取失败"}

        # 初始化种群
        population = [_random_tree() for _ in range(population_size)]
        best_ever = []
        evolution_log = []

        for gen in range(generations):
            # 评估适应度
            fitness_scores = []
            for tree in population:
                fit = _evaluate_fitness(tree, data, fwd_ret)
                fitness_scores.append((tree, fit))

            # 按适应度排序
            fitness_scores.sort(key=lambda x: -x[1])

            # 记录本代最优
            best_ic = fitness_scores[0][1]
            avg_ic = sum(f[1] for f in fitness_scores) / len(fitness_scores)
            evolution_log.append({
                "generation": gen + 1,
                "best_ic": round(best_ic, 5),
                "avg_ic": round(avg_ic, 5),
            })

            # 保留全局最优
            for tree, fit in fitness_scores[:5]:
                expr = tree.to_string()
                if not any(b["expression"] == expr for b in best_ever):
                    best_ever.append({
                        "expression": expr,
                        "ic": fit,
                        "tree": tree,
                        "depth": tree.depth(),
                    })

            # 选择（锦标赛选择）
            def _tournament(k=3):
                candidates = random.sample(fitness_scores, min(k, len(fitness_scores)))
                return max(candidates, key=lambda x: x[1])[0]

            # 新一代
            new_pop = []
            # 精英保留
            for tree, _ in fitness_scores[:int(population_size * 0.1)]:
                import copy
                new_pop.append(copy.deepcopy(tree))

            while len(new_pop) < population_size:
                r = random.random()
                if r < 0.4:
                    # 交叉
                    p1 = _tournament()
                    p2 = _tournament()
                    child = _crossover(p1, p2)
                    new_pop.append(child)
                elif r < 0.7:
                    # 变异
                    parent = _tournament()
                    child = _mutate(parent)
                    new_pop.append(child)
                else:
                    # 新随机个体（保持多样性）
                    new_pop.append(_random_tree())

            population = new_pop

        # 最终结果
        best_ever.sort(key=lambda x: -x["ic"])
        top_factors = []
        seen_expr = set()

        for b in best_ever:
            expr = b["expression"]
            if expr in seen_expr:
                continue
            seen_expr.add(expr)

            ic_val = b["ic"]
            rating = "🏆 优秀" if ic_val > 0.05 else ("✅ 有效" if ic_val > 0.03 else ("⚠️ 弱" if ic_val > 0.02 else "❌ 无效"))

            top_factors.append({
                "rank": len(top_factors) + 1,
                "expression": expr,
                "ic": round(ic_val, 5),
                "rating": rating,
                "depth": b["depth"],
            })

            if len(top_factors) >= top_k:
                break

        result = {
            "code": code,
            "top_factors": top_factors,
            "evolution_log": evolution_log[-10:],  # 最后10代
            "config": {
                "population_size": population_size,
                "generations": generations,
                "data_points": len(fwd_ret),
            },
            "summary": {
                "excellent": sum(1 for f in top_factors if f["ic"] > 0.05),
                "effective": sum(1 for f in top_factors if 0.03 < f["ic"] <= 0.05),
                "weak": sum(1 for f in top_factors if f["ic"] <= 0.03),
            },
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        _gf_cache[cache_key] = {"data": result, "ts": now}
        return result

    except Exception as e:
        traceback.print_exc()
        return {"error": f"因子挖掘失败: {str(e)}"}
