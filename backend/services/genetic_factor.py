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

════════════════════════════════════════════════════════════════
P1-9 统计防护（2026-09）：单票约 800 天时序上做约 6000 次表达式搜索，
      再取同段历史 |IC| 最高者当"最好因子" —— 这是典型的多重检验过拟合，
      而且没有任何东西告诉使用者「6000 次搜索下纯噪声能刷出多高的 |IC|」。
      本次补三件事：
        1. zscore 由「全样本均值/方差」改为**滚动窗口** —— 原实现用到了
           包含未来数据的均值与方差，是明确的前视偏差（look-ahead bias）；
        2. **样本外切分**：前 70% 定因子（选择只用样本内 IC），后 30% 只做
           验证，样本外 IC 一并输出；样本内末尾按前瞻期 purge，避免标签重叠；
        3. **置换检验**：把样本外标签随机重排 n_perm 次，对**同一批被搜索过的
           候选因子**计算 max|IC|，得到「6000 次搜索的随机基准」分布，据此给出
           经多重检验校正的 p 值（而不是裸 |IC| 排名）。
      随机基准是真实算出来的数字（固定随机种子，可复算核对），不是拍的阈值。
      取不到数据/样本不足一律降级并标明 available=False，绝不造数。
════════════════════════════════════════════════════════════════
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
    "description": "表达式树+遗传进化200×30代，样本内定因子/样本外验证+置换检验多重校正",
    "layer": "analysis",
    "priority": 5,
}
import random
import traceback
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from infra.cache import MemoryCache

_GF_CACHE_TTL = 86400  # 24 小时
_gf_cache = MemoryCache(default_ttl=_GF_CACHE_TTL)

# ── P1-9 统计防护参数 ──
_FORWARD_HORIZON = 5        # 前瞻收益天数（与 _prepare_data 的 fwd_ret 定义一致）
_OOS_SPLIT_RATIO = 0.7      # 前 70% 样本内（定因子）/ 后 30% 样本外（验证）
_ZS_WINDOWS = (20, 60, 120)  # zscore 滚动窗口候选
_ZS_DEFAULT_WINDOW = 60      # 无 param 时的滚动窗口（向后兼容手写节点）
_PERM_N_PERMUTATIONS = 200   # 置换次数（p 值分辨率 1/201≈0.005）
_PERM_MAX_CANDIDATES = 8000  # 进入置换检验的候选因子上限（默认搜索 6000 < 上限）
_PERM_SEED = 20260913        # 固定种子 —— 随机基准必须可复算核对
_PERM_MIN_CANDIDATES = 30    # 少于该数量无法构成多重检验基准，直接降级
_PERM_MIN_OOS_POINTS = 60    # 样本外有效点数下限


# ============================================================
# 安全数学函数
# ============================================================

def _safe_div(a, b):
    """安全除法，避免除以零（2026-09-13 改为 np.divide(where=...)）。

    旧写法 `np.where(np.abs(b) < 1e-10, 0.0, a / b)` 会**先把 a/b 整体算出来**
    再挑，于是 b=0 的元素照样触发
        RuntimeWarning: divide by zero / invalid value encountered in divide
    —— 数值结果完全一样，但每次跑测试都刷屏，会把真警告淹掉（噪音即遮蔽）。
    `where=` 是"只在该位置计算"，警告随之消失，且**数值逐位不变**。
    等价性由 test_genetic_factor_statistics.py 里的 _safe_div 对照用例钉住。
    """
    a_arr = np.asarray(a, dtype=float)
    b_arr = np.asarray(b, dtype=float)
    out = np.zeros(np.broadcast(a_arr, b_arr).shape, dtype=float)
    np.divide(a_arr, b_arr, out=out, where=np.abs(b_arr) >= 1e-10)
    return out

def _safe_log(x):
    """安全对数"""
    return np.log(np.maximum(np.abs(x), 1e-10))

def _safe_sqrt(x):
    """安全平方根"""
    return np.sqrt(np.maximum(np.abs(x), 0))


def _rolling_zscore(x: np.ndarray, window: int) -> np.ndarray:
    """因果滚动 zscore：第 i 个点只用 x[i-w+1 .. i] 的均值/方差。

    与 `(x - mean(全样本)) / std(全样本)` 的区别在于**不含未来信息**，
    因此同一表达式在样本外的取值也是"当时真能算出来"的，可用于样本外验证。

    向量化实现（cumsum 前缀和），O(n)，不引入 Python 循环。
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return x
    w = max(1, min(int(window), n))
    c1 = np.cumsum(np.concatenate(([0.0], x)))
    c2 = np.cumsum(np.concatenate(([0.0], x * x)))
    idx = np.arange(n)
    lo = np.maximum(0, idx - w + 1)
    cnt = (idx - lo + 1).astype(np.float64)
    mean = (c1[idx + 1] - c1[lo]) / cnt
    mean_sq = (c2[idx + 1] - c2[lo]) / cnt
    var = np.maximum(mean_sq - mean * mean, 0.0)   # 防浮点误差导致的负方差
    return (x - mean) / (np.sqrt(var) + 1e-10)


def _full_sample_zscore(x: np.ndarray) -> np.ndarray:
    """旧实现（全样本均值/方差）。**仅供测试对照**，生产路径不再使用。

    保留它是为了让「前视偏差」这件事可以被测试证伪：test 里用同一份数据
    分别跑滚动版和全样本版，断言全样本版会被"未来段污染"而滚动版不会。
    """
    x = np.asarray(x, dtype=np.float64)
    m = np.mean(x)
    s = np.std(x)
    return (x - m) / (s + 1e-10)


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
                # P1-9：滚动窗口 zscore（因果）。原实现用**全样本**均值/方差，
                # 即第 i 个点的取值依赖了 i 之后的数据 —— 前视偏差，等价于偷看答案；
                # 在 6000 次搜索里它会系统性地把"运气好的表达式"刷成高 IC。
                # 现在第 i 个点只用 [i-w+1, i] 的样本（含 i 本身）估计均值/方差。
                return _rolling_zscore(x, int(self.param or _ZS_DEFAULT_WINDOW))

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
        if self.op in ("abs", "neg", "log", "sqrt", "rank"):
            return f"{self.op}({self.children[0].to_string()})"
        if self.op == "zscore":
            return f"zscore({self.children[0].to_string()}, {self.param or _ZS_DEFAULT_WINDOW})"
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
        # zscore 必须带滚动窗口（P1-9：无窗口=全样本，即前视偏差）
        param = random.choice(_ZS_WINDOWS) if op == "zscore" else None
        return Node(op, children=[child], param=param)
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
    elif r < 0.6 and (target.op in TS_OPS or target.op == "zscore"):
        # 换窗口（zscore 的滚动窗口同样在候选集里变异）
        target.param = random.choice(TS_WINDOWS if target.op in TS_OPS else _ZS_WINDOWS)
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

def _average_ranks_rows(M: np.ndarray) -> np.ndarray:
    """按行计算平均秩（并列取平均），纯 numpy，O(m·k·log k)。

    并列行的修正只对真正含并列的行执行（连续因子值几乎不会命中），
    因此置换检验里 5000 行 × 235 点的秩转换只需一次 argsort 量级的时间。
    """
    M = np.asarray(M, dtype=np.float64)
    if M.ndim == 1:
        M = M[None, :]
    m, k = M.shape
    order = np.argsort(M, axis=1, kind="stable")
    ranks = np.empty((m, k), dtype=np.float64)
    ranks[np.arange(m)[:, None], order] = np.arange(1, k + 1, dtype=np.float64)[None, :]
    if k > 1:
        s = np.take_along_axis(M, order, axis=1)
        has_tie = np.any(s[:, 1:] == s[:, :-1], axis=1)
        for r in np.nonzero(has_tie)[0]:
            sr = s[r]
            starts = np.nonzero(np.concatenate(([True], sr[1:] != sr[:-1])))[0]
            ends = np.concatenate((starts[1:], [k]))
            for st, en in zip(starts, ends):
                if en - st > 1:
                    ranks[r, order[r, st:en]] = (st + 1 + en) / 2.0
    return ranks


def _average_ranks(a: np.ndarray) -> np.ndarray:
    """1 维平均秩（并列取平均）。与 scipy.stats.rankdata 同口径。

    为什么不用 scipy：requirements 里确有 scipy，但本地/CI 解释器可能没装，
    而 `from scipy.stats import spearmanr` 一旦 ImportError 会被上层
    try/except 吞成「IC=0」—— 那会让整套因子评分静默变成 0 分（看起来在跑、
    其实全是假绿）。本模块改为自带实现，消除这个静默失败面。
    口径与 services/factor_ic._spearman_rank_corr 一致。
    """
    return _average_ranks_rows(np.asarray(a, dtype=np.float64))[0]


def _spearman_corr(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman 秩相关 = 秩上的 Pearson 相关（纯 numpy）"""
    rx = _average_ranks(x)
    ry = _average_ranks(y)
    k = len(rx)
    if k < 5:
        return 0.0
    mx = rx.mean()
    my = ry.mean()
    dx = rx - mx
    dy = ry - my
    denom = math.sqrt(float(dx @ dx) * float(dy @ dy))
    if denom <= 0:
        return 0.0
    return float(dx @ dy) / denom


def _calc_ic(factor_values: np.ndarray, forward_returns: np.ndarray) -> float:
    """Spearman IC（秩相关系数）"""
    factor_values = np.asarray(factor_values, dtype=np.float64)
    forward_returns = np.asarray(forward_returns, dtype=np.float64)
    valid = np.isfinite(factor_values) & np.isfinite(forward_returns)
    fv = factor_values[valid]
    fr = forward_returns[valid]

    if len(fv) < 30:
        return 0.0
    return _spearman_corr(fv, fr)


def _evaluate_fitness(tree: Node, data: dict, forward_returns: np.ndarray,
                      lo: int = 0, hi: int = None) -> float:
    """评估因子的 IC 适应度（只在 [lo, hi) 区间上算 IC）。

    P1-9：进化/选择阶段必须传样本内区间，避免用样本外数据挑因子。
    """
    values = _eval_values(tree, data, len(forward_returns))
    if values is None:
        return 0.0
    return _fitness_from_values(tree, values, forward_returns, lo, hi)


def _eval_values(tree: Node, data: dict, n: int):
    """表达式求值 + 有效性检查；无效（全 NaN / 常数）返回 None（不造数）。"""
    try:
        values = tree.evaluate(data, n)
    except Exception:
        return None
    finite = values[np.isfinite(values)]
    if finite.size == 0 or float(np.std(finite)) < 1e-10:
        return None
    return values


def _fitness_from_values(tree: Node, values: np.ndarray,
                         forward_returns: np.ndarray,
                         lo: int = 0, hi: int = None) -> float:
    """由已求值的因子序列算适应度 = |IC| - 深度惩罚。"""
    hi = len(forward_returns) if hi is None else hi
    ic = _calc_ic(values[lo:hi], forward_returns[lo:hi])
    depth_penalty = max(0, tree.depth() - 3) * 0.005
    return abs(ic) - depth_penalty


# ============================================================
# P1-9 置换检验（多重检验校正）
# ============================================================

def _permutation_significance(candidate_vectors: list,
                              forward_returns: np.ndarray,
                              oos_lo: int, oos_hi: int,
                              selected_vector=None,
                              n_perm: int = _PERM_N_PERMUTATIONS,
                              seed: int = _PERM_SEED) -> dict:
    """对「被搜索过的候选因子集合」做标签置换检验，算出多重检验校正 p 值。

    为什么必须这样做：进化过程做了 population_size × generations 次尝试，
    再从里面挑 |IC| 最高者 —— 即使因子毫无预测力，6000 次抽样也能刷出一个
    很高的 |IC|。只报裸 |IC| 等于把「搜索次数」这份运气算成了 alpha。

    做法（每一步都是真实计算，固定种子，可复算）：
      1. 取样本外区间内**所有**候选因子的取值序列（同一批被搜索过的表达式）；
      2. 把样本外前瞻收益标签随机重排 n_perm 次；
      3. 每次重排都对全部候选算 |IC|，取 max —— 得到「N 次搜索的随机基准」分布；
      4. 校正后 p = P(随机基准 max >= 被选中因子的样本外 |IC|)，
         用 (k+1)/(n_perm+1) 修偏；同时给出**未校正**的单因子随机基准作对照，
         让"裸 |IC| 看起来显著、校正后不显著"这件事在数字上可见。

    Args:
        selected_vector: 最终被选中/上报的那个因子的取值序列（含样本外区间）。
            校正 p 值以它为准 —— 因为我们上报的是它的 |IC|，而不是搜索面上的
            最大值；用搜索面最大值当观测值会低估 p（偏乐观）。
            为 None 时退化为用候选集合的样本外 max|IC| 作观测值，并在
            observed_statistic 里标明口径。

    Returns:
        {available, reason?, n_candidates, n_permutations, seed, oos_points,
         observed_statistic, observed_abs_ic, null_single_trial:{mean,p95,p99},
         p_value_naive, null_max_distribution:{mean,p50,p95,p99,max},
         observed_max_abs_ic_searched, p_value_searched_max,
         p_value_corrected, significant, verdict}
        available=False 时只返回 available/reason，绝不编造基准数字。
    """
    nan_result = {"available": False, "n_candidates": len(candidate_vectors),
                  "n_permutations": 0, "seed": seed}
    try:
        n_oos = oos_hi - oos_lo
        if n_oos < _PERM_MIN_OOS_POINTS:
            return {**nan_result, "reason": f"样本外区间过短({n_oos}<{_PERM_MIN_OOS_POINTS})"}

        labels = np.asarray(forward_returns[oos_lo:oos_hi], dtype=np.float64)
        mask = np.isfinite(labels)
        k_eff = int(mask.sum())
        if k_eff < _PERM_MIN_OOS_POINTS:
            return {**nan_result, "reason": "样本外标签有效点不足"}

        def _seg(v):
            seg = np.asarray(v, dtype=np.float64)[oos_lo:oos_hi][mask]
            if seg.size != k_eff or not bool(np.all(np.isfinite(seg))):
                return None
            return seg

        rows = []
        for v in candidate_vectors:
            try:
                seg = _seg(v)
            except Exception:
                seg = None
            if seg is not None:
                rows.append(seg)
        if len(rows) < _PERM_MIN_CANDIDATES:
            return {**nan_result, "n_candidates": len(rows),
                    "reason": f"可用候选因子过少({len(rows)}<{_PERM_MIN_CANDIDATES})"}
        truncation_note = len(rows) > _PERM_MAX_CANDIDATES
        if truncation_note:
            rows = rows[:_PERM_MAX_CANDIDATES]

        sel_seg = None
        if selected_vector is not None:
            try:
                sel_seg = _seg(selected_vector)
            except Exception:
                sel_seg = None

        M = np.vstack(rows)                       # (m, k)
        k = M.shape[1]
        # 逐行平均秩（纯 numpy，口径与 _calc_ic 一致）
        R = _average_ranks_rows(M)
        R -= R.mean(axis=1, keepdims=True)
        R /= (R.std(axis=1, keepdims=True) + 1e-12)   # 行标准化 → Pearson=IC

        lab = labels[mask].astype(np.float64)
        lab_r = _average_ranks(lab)
        lab_obs = lab_r - lab_r.mean()
        lab_obs /= (lab_obs.std() + 1e-12)
        ic_obs = (R @ lab_obs) / k
        searched_max = float(np.max(np.abs(ic_obs)))
        if sel_seg is not None:
            sr = _average_ranks(sel_seg)
            sr = sr - sr.mean()
            sr /= (sr.std() + 1e-12)
            observed = float(abs(sr @ lab_obs) / k)
            observed_stat = "selected_factor_oos_abs_ic"
        else:
            observed = searched_max
            observed_stat = "searched_max_oos_abs_ic"

        rng = np.random.default_rng(seed)
        # 标签无并列时，重排取值后再取秩 == 直接重排秩（IC 只依赖秩），
        # 因此可一次性生成 n_perm 个独立排列，避免 200 次重排+排序的开销。
        if k == int(np.unique(lab_r).size):
            perm_idx = np.argsort(rng.random((n_perm, k)), axis=1)   # (n_perm, k)
            Lz = lab_r[perm_idx]
            Lz -= Lz.mean(axis=1, keepdims=True)
            Lz /= (Lz.std(axis=1, keepdims=True) + 1e-12)
            L = Lz.T                                                 # (k, n_perm)
        else:  # 有并列：退回"重排取值再取秩"，与 IC 定义严格一致
            L = np.empty((k, n_perm), dtype=np.float64)
            for j in range(n_perm):
                pz = _average_ranks(rng.permutation(lab))
                pz = pz - pz.mean()
                L[:, j] = pz / (pz.std() + 1e-12)
        IC = (R @ L) / k                           # (m, n_perm)

        abs_ic = np.abs(IC)
        null_max = abs_ic.max(axis=0)              # 每次置换下 N 次搜索的 max|IC|
        pooled = abs_ic.ravel()                    # 单次尝试的随机基准
        p_corr = float((1 + int(np.sum(null_max >= observed))) / (1 + n_perm))
        p_naive = float((1 + int(np.sum(pooled >= observed))) / (1 + pooled.size))
        p_searched = float((1 + int(np.sum(null_max >= searched_max))) / (1 + n_perm))

        return {
            "available": True,
            "reason": "",
            "observed_statistic": observed_stat,
            "n_candidates": int(M.shape[0]),
            "n_permutations": int(n_perm),
            "seed": int(seed),
            "oos_points": int(k),
            "candidates_truncated": truncation_note,
            "observed_abs_ic": round(observed, 4),
            # 「裸 |IC| 排名」的随机基准：单个因子在纯噪声下的 |IC| 分布
            "null_single_trial": {
                "mean": round(float(pooled.mean()), 4),
                "p95": round(float(np.percentile(pooled, 95)), 4),
                "p99": round(float(np.percentile(pooled, 99)), 4),
            },
            # 「N 次搜索」的随机基准：N 次尝试里最好那个的 |IC| 分布
            "null_max_distribution": {
                "mean": round(float(null_max.mean()), 4),
                "p50": round(float(np.percentile(null_max, 50)), 4),
                "p95": round(float(np.percentile(null_max, 95)), 4),
                "p99": round(float(np.percentile(null_max, 99)), 4),
                "max": round(float(null_max.max()), 4),
            },
            # 参考：整个搜索面上的最大样本外 |IC| 及其校正 p（口径与上面不同）
            "observed_max_abs_ic_searched": round(searched_max, 4),
            "p_value_searched_max": round(p_searched, 6),
            "p_value_naive": round(p_naive, 6),
            "p_value_corrected": round(p_corr, 6),
            "significant": bool(p_corr < 0.05),
            "verdict": ("通过多重检验校正（p<0.05）：样本外 |IC| 高于 "
                        f"{n_perm} 次搜索的随机基准" if p_corr < 0.05 else
                        "未通过多重检验校正：|IC| 落在多次搜索的随机波动范围内，"
                        "不能当作 alpha"),
        }
    except Exception as e:
        return {**nan_result, "reason": f"置换检验失败: {e}"}


# ============================================================
# 数据准备
# ============================================================

def _prepare_data(code: str, days: int = 800) -> tuple:
    """获取股票数据，返回 (data_dict, forward_returns)"""
    try:
        from infra.data_source.market.stocks import get_stock_daily_hist
        df = get_stock_daily_hist(code=code, period="daily", adjust="qfq")
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

        # 未来 N 天收益率作为标签（N = _FORWARD_HORIZON）
        fwd_ret = np.full(n, np.nan)
        for i in range(n - _FORWARD_HORIZON):
            fwd_ret[i] = (close[i + _FORWARD_HORIZON] / close[i]) - 1.0

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
    cached = _gf_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        data, fwd_ret = _prepare_data(code)
        if data is None:
            return {"error": "数据获取失败"}

        n = len(fwd_ret)
        # P1-9：样本内(前70%) / 样本外(后30%) 切分。
        # 样本内末尾按前瞻期 purge —— 样本内最后 N 天的标签会用到样本外首段的
        # 收盘价，不切掉的话"样本外"与"样本内"的标签重叠，验证就名不副实。
        split = int(n * _OOS_SPLIT_RATIO)
        is_lo, is_hi = 0, max(1, split - _FORWARD_HORIZON)
        oos_lo, oos_hi = split, n - _FORWARD_HORIZON
        if oos_hi - oos_lo < _PERM_MIN_OOS_POINTS or is_hi - is_lo < 120:
            return {"error": f"历史数据不足，无法做 {_OOS_SPLIT_RATIO:.0%} 样本外切分（n={n}）"}

        # 初始化种群
        population = [_random_tree() for _ in range(population_size)]
        best_ever = []
        evolution_log = []
        # P1-9：保存「被搜索过的候选因子」在样本外的取值，供置换检验构造
        # 「N 次搜索的随机基准」。按表达式去重 + 设上限（防大参数爆内存）。
        candidate_oos: list = []
        candidate_exprs: set = set()

        for gen in range(generations):
            # 评估适应度（**只用样本内** 区间，样本外绝不参与选择）
            fitness_scores = []
            for tree in population:
                values = _eval_values(tree, data, n)
                if values is None:
                    fitness_scores.append((tree, 0.0))
                    continue
                fitness_scores.append(
                    (tree, _fitness_from_values(tree, values, fwd_ret, is_lo, is_hi)))
                expr = tree.to_string()
                if expr not in candidate_exprs and len(candidate_oos) < _PERM_MAX_CANDIDATES:
                    candidate_exprs.add(expr)
                    # 存**整段**取值（float32 控内存），由置换检验按样本外区间切片
                    candidate_oos.append(np.asarray(values, dtype=np.float32))

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
        selected_values = None   # 上报的 top-1 因子取值，供置换检验做被选中口径的校正

        for b in best_ever:
            expr = b["expression"]
            if expr in seen_expr:
                continue
            seen_expr.add(expr)

            vals = _eval_values(b["tree"], data, n)
            if vals is None:
                continue
            ic_is_signed = _calc_ic(vals[is_lo:is_hi], fwd_ret[is_lo:is_hi])
            ic_is = abs(ic_is_signed)
            ic_oos = _calc_ic(vals[oos_lo:oos_hi], fwd_ret[oos_lo:oos_hi])
            # 样本外"复现"判定：既要达到 |IC|>=0.03 的有效门槛，**方向也必须一致**。
            # 只看 |IC| 会把"样本内 +0.35 / 样本外 -0.27"这种方向翻转也算作复现，
            # 而方向翻转的因子根本不可用 —— 这是本项目最忌讳的假绿。
            same_dir = (ic_is_signed * ic_oos) > 0
            oos_ok = same_dir and abs(ic_oos) >= 0.03
            if selected_values is None:
                selected_values = vals

            rating = "🏆 优秀" if ic_is > 0.05 else ("✅ 有效" if ic_is > 0.03 else ("⚠️ 弱" if ic_is > 0.02 else "❌ 无效"))
            if not same_dir and abs(ic_oos) > 1e-9:
                rating += " · 样本外方向反转"
            elif not oos_ok:
                rating += " · 样本外未复现"

            top_factors.append({
                "rank": len(top_factors) + 1,
                "expression": expr,
                "ic": round(ic_is, 5),         # 样本内 IC（选择阶段所用，含选择偏差）
                "ic_is": round(ic_is, 5),
                "ic_oos": round(ic_oos, 5),    # 样本外 IC（从未参与选择）
                "oos_same_direction": bool(same_dir),
                "oos_confirmed": bool(oos_ok),
                "rating": rating,
                "depth": b["depth"],
            })

            if len(top_factors) >= top_k:
                break

        # P1-9：多重检验校正（以**上报的 top-1 因子**为观测口径）
        significance = _permutation_significance(
            candidate_oos, fwd_ret, oos_lo, oos_hi, selected_vector=selected_values)

        result = {
            "code": code,
            "top_factors": top_factors,
            "evolution_log": evolution_log[-10:],  # 最后10代
            "config": {
                "population_size": population_size,
                "generations": generations,
                "data_points": len(fwd_ret),
                # P1-9：让「多重检验」的规模可见 —— 就是搜索尝试次数
                "search_trials": population_size * generations,
                "in_sample_points": is_hi - is_lo,
                "out_of_sample_points": oos_hi - oos_lo,
                "purged_points": _FORWARD_HORIZON,
            },
            "summary": {
                "excellent": sum(1 for f in top_factors if f["ic"] > 0.05),
                "effective": sum(1 for f in top_factors if 0.03 < f["ic"] <= 0.05),
                "weak": sum(1 for f in top_factors if f["ic"] <= 0.03),
                # P1-9 新增：上面三个计数都是**样本内**口径（含选择偏差），
                # 样本外是否复现（且方向一致）必须单独计数。
                "oos_confirmed": sum(1 for f in top_factors if f.get("oos_confirmed")),
                "is_overfit_suspected": bool(top_factors) and
                    all(not f.get("oos_confirmed") for f in top_factors),
            },
            # P1-9：样本外验证口径声明
            "oos_validation": {
                "available": True,
                "split_ratio": _OOS_SPLIT_RATIO,
                "in_sample_range": [is_lo, is_hi],
                "out_of_sample_range": [oos_lo, oos_hi],
                "purged_points": _FORWARD_HORIZON,
                "selection_uses": "in_sample_only",
                "note": ("因子选择只用样本内 IC；样本外 IC 仅用于验证，"
                         "从未进入适应度函数。样本内末尾按前瞻期 purge，"
                         "避免与样本外标签重叠。"),
            },
            # P1-9：多重检验校正（随机基准 + 校正后 p 值）
            "significance": significance,
            "method": "genetic_expression_search_with_oos_and_permutation",
            "limitations": [
                f"搜索空间为单票 {len(fwd_ret)} 天时序、"
                f"{population_size * generations} 次表达式尝试，|IC| 排名受多重检验影响，"
                "请以 significance.p_value_corrected 为准，不要只看 |IC|。",
                "样本外区间只有一段（后 30%），未做滚动前推/多段验证，"
                "结论对这一段行情有依赖。",
                "rank 与 ts_* 操作符是时序口径（单票时序），不是横截面 IC；"
                "因子是否在全市场横截面上有效需另用 services/factor_ic 的面板 IC 验证。",
            ],
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        _gf_cache.set(cache_key, result)
        return result

    except Exception as e:
        traceback.print_exc()
        return {"error": f"因子挖掘失败: {str(e)}"}
