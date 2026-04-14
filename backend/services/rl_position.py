"""
钱袋子 — 强化学习仓位管理 V1
用 Q-Learning 动态调整持仓比例

架构（轻量级，CPU 友好）：
  State  = 离散化的 [仓位水平, 收益率区间, 波动率区间, RSI区间, 趋势方向]
  Action = [大幅加仓(+20%), 小幅加仓(+10%), 持仓不动, 小幅减仓(-10%), 大幅减仓(-20%), 清仓]
  Reward = 风险调整收益（类似夏普比率的增量形式）

为什么用 Q-Learning 而不是 PPO/A3C：
  - 状态空间离散化后维度低（~5000种状态）
  - 不需要 GPU 训练
  - Q-table 可直接序列化保存
  - 可解释性强（能看到每个状态对应的最优动作）

参考：
  - 幻方量化 PPO 策略调参框架（简化版）
  - FinRL 仓位管理模块
  - Sutton & Barto "Reinforcement Learning: An Introduction"
"""
import time
import math
import random
import traceback
import json
import numpy as np
from pathlib import Path

_rl_cache = {}
_RL_CACHE_TTL = 3600


# ============================================================
# 状态空间离散化
# ============================================================

def _discretize_state(position_pct: float, return_pct: float, volatility: float,
                       rsi: float, trend: float) -> tuple:
    """
    将连续状态离散化为有限状态空间

    position_pct: 当前仓位比例 (0~1)
    return_pct: 近期收益率
    volatility: 近期波动率（年化）
    rsi: RSI 指标 (0~100)
    trend: 趋势方向 (-1, 0, +1)
    """
    # 仓位水平: 5 档
    if position_pct <= 0.05:
        pos_level = 0  # 空仓
    elif position_pct <= 0.3:
        pos_level = 1  # 轻仓
    elif position_pct <= 0.6:
        pos_level = 2  # 半仓
    elif position_pct <= 0.85:
        pos_level = 3  # 重仓
    else:
        pos_level = 4  # 满仓

    # 收益率区间: 5 档
    if return_pct < -0.10:
        ret_level = 0  # 大亏
    elif return_pct < -0.03:
        ret_level = 1  # 小亏
    elif return_pct < 0.03:
        ret_level = 2  # 持平
    elif return_pct < 0.10:
        ret_level = 3  # 小赚
    else:
        ret_level = 4  # 大赚

    # 波动率区间: 4 档
    if volatility < 0.15:
        vol_level = 0  # 低波
    elif volatility < 0.25:
        vol_level = 1  # 中波
    elif volatility < 0.40:
        vol_level = 2  # 高波
    else:
        vol_level = 3  # 极高波

    # RSI 区间: 4 档
    if rsi < 30:
        rsi_level = 0  # 超卖
    elif rsi < 50:
        rsi_level = 1  # 偏低
    elif rsi < 70:
        rsi_level = 2  # 偏高
    else:
        rsi_level = 3  # 超买

    # 趋势: 3 档
    trend_level = 0 if trend < -0.3 else (2 if trend > 0.3 else 1)

    return (pos_level, ret_level, vol_level, rsi_level, trend_level)


# ============================================================
# 动作空间
# ============================================================

ACTIONS = {
    0: {"name": "大幅加仓", "delta": +0.20, "emoji": "🔥"},
    1: {"name": "小幅加仓", "delta": +0.10, "emoji": "📈"},
    2: {"name": "持仓不动", "delta": 0.00, "emoji": "⏸️"},
    3: {"name": "小幅减仓", "delta": -0.10, "emoji": "📉"},
    4: {"name": "大幅减仓", "delta": -0.20, "emoji": "🛑"},
    5: {"name": "清仓", "delta": -1.00, "emoji": "🚨"},
}

NUM_ACTIONS = len(ACTIONS)


# ============================================================
# Q-Learning Agent
# ============================================================

class QLearningAgent:
    """Q-Learning 仓位管理 Agent"""

    def __init__(self, alpha=0.1, gamma=0.95, epsilon=0.1):
        self.alpha = alpha      # 学习率
        self.gamma = gamma      # 折扣因子
        self.epsilon = epsilon  # 探索率
        self.q_table = {}       # Q 值表
        self.training_episodes = 0

    def _get_q(self, state: tuple, action: int) -> float:
        return self.q_table.get((state, action), 0.0)

    def _set_q(self, state: tuple, action: int, value: float):
        self.q_table[(state, action)] = value

    def choose_action(self, state: tuple, explore=True) -> int:
        """ε-贪心策略选择动作"""
        if explore and random.random() < self.epsilon:
            return random.randint(0, NUM_ACTIONS - 1)

        q_values = [self._get_q(state, a) for a in range(NUM_ACTIONS)]
        max_q = max(q_values)
        best_actions = [a for a in range(NUM_ACTIONS) if q_values[a] == max_q]
        return random.choice(best_actions)

    def learn(self, state: tuple, action: int, reward: float, next_state: tuple):
        """Q-Learning 更新"""
        current_q = self._get_q(state, action)
        max_next_q = max(self._get_q(next_state, a) for a in range(NUM_ACTIONS))
        new_q = current_q + self.alpha * (reward + self.gamma * max_next_q - current_q)
        self._set_q(state, action, new_q)

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "q_table": {f"{k[0]}_{k[1]}": v for k, v in self.q_table.items()},
            "training_episodes": self.training_episodes,
            "alpha": self.alpha,
            "gamma": self.gamma,
            "epsilon": self.epsilon,
        }


# ============================================================
# 环境模拟
# ============================================================

def _calc_reward(daily_return: float, position: float, volatility: float) -> float:
    """
    风险调整奖励函数
    = position * daily_return - 0.5 * position^2 * volatility^2
    鼓励在低波动时加仓、高波动时减仓
    """
    reward = position * daily_return - 0.5 * (position ** 2) * (volatility ** 2)

    # 额外惩罚：满仓遇大跌
    if position > 0.8 and daily_return < -0.03:
        reward -= 0.02

    # 额外奖励：低仓位躲过大跌
    if position < 0.3 and daily_return < -0.03:
        reward += 0.01

    return reward


def _calc_rsi(prices, period=14):
    """计算 RSI"""
    if len(prices) < period + 1:
        return 50.0
    changes = np.diff(prices[-(period + 1):])
    gains = changes[changes > 0]
    losses = -changes[changes < 0]
    avg_gain = np.mean(gains) if len(gains) > 0 else 0
    avg_loss = np.mean(losses) if len(losses) > 0 else 1e-9
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_trend(prices, short=5, long=20):
    """计算趋势方向"""
    if len(prices) < long:
        return 0
    ma_short = np.mean(prices[-short:])
    ma_long = np.mean(prices[-long:])
    return (ma_short / ma_long - 1) * 10  # 放大差异


def train_on_history(code: str, days: int = 750) -> dict:
    """在历史数据上训练 Q-Learning Agent"""
    try:
        from services.backtest_engine import _get_stock_hist
        prices = _get_stock_hist(code, days=days)
        if len(prices) < 200:
            return {"error": f"数据不足: {len(prices)}天"}

        prices_arr = np.array(prices, dtype=np.float64)
        returns = np.diff(np.log(prices_arr))

        agent = QLearningAgent(alpha=0.1, gamma=0.95, epsilon=0.15)

        # 多轮训练
        total_episodes = 5
        results = []

        for episode in range(total_episodes):
            position = 0.5  # 初始半仓
            portfolio_value = 1.0
            episode_reward = 0

            for i in range(60, len(returns)):
                # 构造状态
                ret_20d = float((prices_arr[i] / prices_arr[i - 20]) - 1)
                vol_20d = float(np.std(returns[i - 20:i]) * math.sqrt(252))
                rsi = _calc_rsi(prices_arr[:i + 1])
                trend = _calc_trend(prices_arr[:i + 1])

                state = _discretize_state(position, ret_20d, vol_20d, rsi, trend)

                # 选择动作
                action = agent.choose_action(state, explore=True)
                delta = ACTIONS[action]["delta"]

                # 执行动作
                new_position = max(0, min(1, position + delta))
                if action == 5:
                    new_position = 0

                # 下一天收益
                daily_ret = float(returns[i]) if i < len(returns) else 0
                reward = _calc_reward(daily_ret, new_position, vol_20d)

                # 更新组合价值
                portfolio_value *= (1 + new_position * daily_ret)

                # 下一个状态
                if i + 1 < len(returns):
                    next_ret = float((prices_arr[i + 1] / prices_arr[i + 1 - 20]) - 1) if i + 1 >= 20 else 0
                    next_vol = float(np.std(returns[max(0, i - 19):i + 1]) * math.sqrt(252))
                    next_rsi = _calc_rsi(prices_arr[:i + 2])
                    next_trend = _calc_trend(prices_arr[:i + 2])
                    next_state = _discretize_state(new_position, next_ret, next_vol, next_rsi, next_trend)

                    agent.learn(state, action, reward, next_state)

                position = new_position
                episode_reward += reward

            agent.training_episodes += 1
            # 逐步降低探索率
            agent.epsilon = max(0.05, agent.epsilon * 0.9)

            buy_hold_value = prices_arr[-1] / prices_arr[60]

            results.append({
                "episode": episode + 1,
                "rl_return": round((portfolio_value - 1) * 100, 2),
                "buy_hold_return": round((buy_hold_value - 1) * 100, 2),
                "total_reward": round(episode_reward, 4),
            })

        return {
            "code": code,
            "training_results": results,
            "q_table_size": len(agent.q_table),
            "agent": agent,
        }

    except Exception as e:
        traceback.print_exc()
        return {"error": f"训练失败: {str(e)}"}


# ============================================================
# 公开 API
# ============================================================

def get_rl_recommendation(code: str) -> dict:
    """获取 RL 仓位建议"""
    cache_key = f"rl_rec_{code}"
    now = time.time()
    if cache_key in _rl_cache and now - _rl_cache[cache_key]["ts"] < _RL_CACHE_TTL:
        return _rl_cache[cache_key]["data"]

    try:
        # 训练
        train_result = train_on_history(code)
        if "error" in train_result:
            return train_result

        agent = train_result["agent"]

        # 用当前市场状态获取建议
        from services.backtest_engine import _get_stock_hist
        prices = _get_stock_hist(code, days=60)
        if len(prices) < 30:
            return {"error": "当前数据不足"}

        prices_arr = np.array(prices, dtype=np.float64)
        returns = np.diff(np.log(prices_arr))

        ret_20d = float((prices_arr[-1] / prices_arr[-20]) - 1) if len(prices_arr) >= 20 else 0
        vol_20d = float(np.std(returns[-20:]) * math.sqrt(252)) if len(returns) >= 20 else 0.2
        rsi = _calc_rsi(prices_arr)
        trend = _calc_trend(prices_arr)

        # 为不同仓位水平获取建议
        recommendations = []
        for pos_pct in [0.0, 0.2, 0.5, 0.8, 1.0]:
            state = _discretize_state(pos_pct, ret_20d, vol_20d, rsi, trend)
            action = agent.choose_action(state, explore=False)
            act_info = ACTIONS[action]
            q_values = [agent._get_q(state, a) for a in range(NUM_ACTIONS)]

            recommendations.append({
                "current_position": f"{int(pos_pct * 100)}%",
                "action": f"{act_info['emoji']} {act_info['name']}",
                "target_position": f"{max(0, min(100, int((pos_pct + act_info['delta']) * 100)))}%",
                "confidence": round(max(q_values) - np.mean(q_values), 4) if max(q_values) != min(q_values) else 0,
            })

        result = {
            "code": code,
            "market_state": {
                "return_20d": round(ret_20d * 100, 2),
                "volatility": round(vol_20d * 100, 2),
                "rsi": round(rsi, 1),
                "trend": "上涨" if trend > 0.3 else ("下跌" if trend < -0.3 else "震荡"),
            },
            "recommendations": recommendations,
            "training_summary": {
                "episodes": len(train_result["training_results"]),
                "final_rl_return": train_result["training_results"][-1]["rl_return"],
                "buy_hold_return": train_result["training_results"][-1]["buy_hold_return"],
                "outperformance": round(
                    train_result["training_results"][-1]["rl_return"] -
                    train_result["training_results"][-1]["buy_hold_return"], 2
                ),
                "q_table_size": train_result["q_table_size"],
            },
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        _rl_cache[cache_key] = {"data": result, "ts": now}
        return result

    except Exception as e:
        traceback.print_exc()
        return {"error": f"RL 建议失败: {str(e)}"}


def get_rl_portfolio_advice(user_id: str) -> dict:
    """对用户全部持仓给出 RL 仓位建议"""
    try:
        from services.stock_monitor import get_stock_holdings
        holdings = get_stock_holdings(user_id)

        if not holdings:
            return {"error": "没有持仓数据"}

        results = []
        errors = []

        def _do_one(h):
            code = h.get("code", "")
            return code, h.get("name", ""), get_rl_recommendation(code)

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_do_one, h) for h in holdings[:8]]
            for f in as_completed(futures):
                code, name, rec = f.result()
                if "error" in rec:
                    errors.append({"code": code, "name": name, "error": rec["error"]})
                else:
                    rec["name"] = name
                    results.append(rec)

        return {
            "stocks": results,
            "errors": errors,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    except Exception as e:
        traceback.print_exc()
        return {"error": f"组合RL建议失败: {str(e)}"}
