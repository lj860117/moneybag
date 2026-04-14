"""
钱袋子 — LLM 因子生成器 V1 (Alpha-GPT 平替)
让 DeepSeek 自动生成交易因子

流程：
  1. 告诉 LLM 可用的数据字段（不给真实数据，防止幻觉）
  2. LLM 基于金融知识生成因子假设 + Python 代码
  3. 执行代码计算因子值
  4. 用 IC 验证因子有效性
  5. 反馈结果给 LLM → 迭代优化

参考：
  - 幻方量化 Alpha-GPT 概念
  - "Can Large Language Models Mine Gold?" (2024, Chen et al.)
  - "FinAgent: A Multimodal Foundation Agent for Financial Trading" (2024)
"""
import time
import json
import traceback
import numpy as np
import re
from config import LLM_API_URL, LLM_API_KEY, LLM_MODEL

_llm_factor_cache = {}
_LLM_CACHE_TTL = 86400  # 24 小时


# ============================================================
# LLM 调用
# ============================================================

def _call_llm(prompt: str, system: str = "") -> str:
    """调用 DeepSeek LLM"""
    import httpx

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
    }

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000,
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(LLM_API_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"LLM 调用失败: {str(e)}"


# ============================================================
# 因子生成 Prompt
# ============================================================

SYSTEM_PROMPT = """你是一位量化金融研究员，擅长设计 Alpha 因子。

可用数据字段（numpy array，长度 N 天）:
- close: 收盘价
- open: 开盘价
- high: 最高价
- low: 最低价
- volume: 成交量
- returns: 日对数收益率

可用函数:
- np.mean, np.std, np.max, np.min (numpy)
- np.diff, np.log, np.abs, np.sqrt
- np.correlate, np.corrcoef

要求:
1. 输出一个 Python 函数，签名为 def calc_factor(close, open, high, low, volume, returns) -> np.ndarray
2. 返回长度为 N 的 numpy array（因子值序列）
3. 处理边界情况（前几天数据不足时返回 0 或 NaN）
4. 不要导入除 numpy 外的任何库
5. 用 ```python ``` 包裹代码
6. 简要说明因子的金融逻辑（1-2句话）"""


GENERATE_PROMPT = """请生成 {count} 个创新性的 Alpha 因子。

要求:
- 每个因子独立，金融逻辑不同
- 不要生成简单的均线或 RSI（太基础了）
- 尝试捕捉以下之一: 量价背离、波动率结构变化、异常成交量模式、动量反转临界点、价格形态识别
- 每个因子必须包含: 因子名称、金融逻辑、Python 代码

格式:
### 因子1: [名称]
逻辑: [1-2句话说明]
```python
def calc_factor(close, open, high, low, volume, returns):
    ...
```

### 因子2: [名称]
...
"""


ITERATE_PROMPT = """上一轮生成的因子验证结果如下:

{results}

请基于以上结果:
1. 分析哪些因子有效、哪些无效及原因
2. 生成 {count} 个改进版因子（保留有效因子的逻辑，改进无效因子的思路）
3. 尝试组合有效因子的特征

格式同上。"""


# ============================================================
# 因子代码提取 + 安全执行
# ============================================================

def _extract_factors_from_response(response: str) -> list:
    """从 LLM 响应中提取因子名称和代码"""
    factors = []

    # 提取 ### 因子N: 名称
    pattern = r'###\s*因子\d+[:：]\s*(.+?)(?:\n|$)'
    names = re.findall(pattern, response)

    # 提取 python 代码块
    code_pattern = r'```python\s*\n(.*?)```'
    codes = re.findall(code_pattern, response, re.DOTALL)

    # 提取逻辑说明
    logic_pattern = r'逻辑[:：]\s*(.+?)(?:\n|$)'
    logics = re.findall(logic_pattern, response)

    for i in range(min(len(codes), len(names))):
        factors.append({
            "name": names[i].strip() if i < len(names) else f"因子{i+1}",
            "logic": logics[i].strip() if i < len(logics) else "",
            "code": codes[i].strip(),
        })

    return factors


def _safe_execute_factor(code: str, data: dict) -> np.ndarray:
    """安全执行因子代码"""
    try:
        # 安全检查：禁止危险操作
        forbidden = ["import os", "import sys", "subprocess", "exec(", "eval(",
                      "open(", "__import__", "globals(", "locals("]
        for f in forbidden:
            if f in code:
                return None

        # 构造执行环境
        local_ns = {
            "np": np,
            "close": data["close"],
            "open": data["open"],
            "high": data["high"],
            "low": data["low"],
            "volume": data["volume"],
            "returns": data["returns"],
        }

        # 添加 import numpy
        full_code = "import numpy as np\n" + code
        # 添加调用
        full_code += "\nresult = calc_factor(close, open, high, low, volume, returns)"

        exec(full_code, {"np": np, "numpy": np, "math": __import__("math")}, local_ns)

        result = local_ns.get("result")
        if isinstance(result, np.ndarray) and len(result) == len(data["close"]):
            # 清洗
            result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
            return result

        return None
    except Exception as e:
        return None


# ============================================================
# IC 验证
# ============================================================

def _validate_factor(factor_values: np.ndarray, forward_returns: np.ndarray) -> dict:
    """验证因子 IC"""
    valid = np.isfinite(factor_values) & np.isfinite(forward_returns)
    fv = factor_values[valid]
    fr = forward_returns[valid]

    if len(fv) < 30:
        return {"ic": 0, "valid": False, "reason": "样本不足"}

    from scipy.stats import spearmanr
    ic, p_value = spearmanr(fv, fr)
    ic = float(ic) if np.isfinite(ic) else 0

    if abs(ic) > 0.05:
        rating = "🏆 优秀"
    elif abs(ic) > 0.03:
        rating = "✅ 有效"
    elif abs(ic) > 0.02:
        rating = "⚠️ 弱"
    else:
        rating = "❌ 无效"

    return {
        "ic": round(ic, 5),
        "abs_ic": round(abs(ic), 5),
        "p_value": round(float(p_value), 5) if np.isfinite(p_value) else 1.0,
        "rating": rating,
        "valid": abs(ic) > 0.02,
        "samples": len(fv),
    }


# ============================================================
# 公开 API
# ============================================================

def generate_alpha_factors(
    code: str = "000001",
    count: int = 5,
    iterations: int = 2,
) -> dict:
    """
    LLM 驱动的 Alpha 因子生成

    参数:
      code: 用于验证的股票代码
      count: 每轮生成因子数
      iterations: 迭代轮数（生成→验证→改进→验证）
    """
    cache_key = f"llm_factor_{code}_{count}_{iterations}"
    now = time.time()
    if cache_key in _llm_factor_cache and now - _llm_factor_cache[cache_key]["ts"] < _LLM_CACHE_TTL:
        return _llm_factor_cache[cache_key]["data"]

    if not LLM_API_KEY:
        return {"error": "LLM API Key 未配置"}

    try:
        # 获取数据
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if df is None or len(df) < 200:
            return {"error": "数据不足"}

        df = df.tail(800)
        n = len(df)

        close = df["收盘"].values.astype(np.float64)
        open_ = df["开盘"].values.astype(np.float64)
        high = df["最高"].values.astype(np.float64)
        low = df["最低"].values.astype(np.float64)
        volume = df["成交量"].values.astype(np.float64)
        returns = np.zeros(n)
        returns[1:] = np.diff(np.log(close))

        data = {
            "close": close, "open": open_, "high": high,
            "low": low, "volume": volume, "returns": returns,
        }

        # 未来5天收益
        fwd_ret = np.full(n, np.nan)
        for i in range(n - 5):
            fwd_ret[i] = (close[i + 5] / close[i]) - 1.0

        all_results = []
        prev_results_text = ""

        for iteration in range(iterations):
            # 生成因子
            if iteration == 0:
                prompt = GENERATE_PROMPT.format(count=count)
            else:
                prompt = ITERATE_PROMPT.format(results=prev_results_text, count=count)

            response = _call_llm(prompt, system=SYSTEM_PROMPT)
            factors = _extract_factors_from_response(response)

            round_results = []
            for factor in factors:
                # 执行因子代码
                factor_values = _safe_execute_factor(factor["code"], data)

                if factor_values is None:
                    round_results.append({
                        "name": factor["name"],
                        "logic": factor["logic"],
                        "status": "❌ 执行失败",
                        "ic": 0,
                        "iteration": iteration + 1,
                    })
                    continue

                # 验证 IC
                validation = _validate_factor(factor_values, fwd_ret)
                round_results.append({
                    "name": factor["name"],
                    "logic": factor["logic"],
                    "code": factor["code"],
                    "status": validation["rating"],
                    "ic": validation["ic"],
                    "abs_ic": validation["abs_ic"],
                    "p_value": validation["p_value"],
                    "samples": validation["samples"],
                    "valid": validation["valid"],
                    "iteration": iteration + 1,
                })

            all_results.extend(round_results)

            # 构造反馈给 LLM 的结果文本
            prev_results_text = "\n".join([
                f"- {r['name']}: IC={r['ic']}, 评级={r['status']}"
                for r in round_results
            ])

        # 按 |IC| 排序
        all_results.sort(key=lambda x: -abs(x.get("ic", 0)))

        effective = [r for r in all_results if r.get("valid", False)]
        failed = [r for r in all_results if not r.get("valid", False)]

        result = {
            "code": code,
            "all_factors": all_results,
            "effective_factors": effective,
            "failed_factors": failed,
            "summary": {
                "total_generated": len(all_results),
                "effective": len(effective),
                "success_rate": round(len(effective) / max(len(all_results), 1) * 100, 1),
                "best_ic": round(max((abs(r.get("ic", 0)) for r in all_results), default=0), 5),
                "iterations": iterations,
            },
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        _llm_factor_cache[cache_key] = {"data": result, "ts": now}
        return result

    except Exception as e:
        traceback.print_exc()
        return {"error": f"LLM因子生成失败: {str(e)}"}
