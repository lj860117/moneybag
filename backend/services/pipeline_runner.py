"""
钱袋子 — PipelineRunner（管线引擎）
v3.0 底座 #3

职责：
  可配置的决策管线。根据 Regime 动态选择 Pipeline，
  按步骤顺序执行，每步读写同一个 DecisionContext。

3 种管线：
  default   = [load_user→regime→modules→gate→llm→payoff→risk→output→ema]  日常9步
  fast      = [load_user→regime→modules→risk→output]                      紧急5步
  cautious  = [load_user→regime→modules→gate→llm→payoff→doctor→risk→output→ema] 熊市10步

设计文档: §四
"""
import time
import asyncio
from typing import Callable, Optional
from services.decision_context import DecisionContext


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pipeline Step 定义（每个 step 是一个函数: ctx → ctx）
# 这里先定义骨架，具体逻辑在 W3-W7 各 Phase 实现
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def step_load_user_data(ctx: DecisionContext) -> DecisionContext:
    """Layer0: 加载用户持仓+记忆+偏好"""
    # W3 实现：从 stock_monitor/fund_monitor/agent_memory 加载
    # 现在先标记为已执行
    ctx.pipeline_steps.append("load_user_data")
    return ctx


def step_regime(ctx: DecisionContext) -> DecisionContext:
    """Layer1: 市场状态分类（4类：趋势牛/震荡/高波熊/轮动）"""
    # W6 实现：regime_engine.py
    # 现在默认 oscillating
    if not ctx.regime:
        ctx.regime = "oscillating"
        ctx.regime_confidence = 0.5
    ctx.pipeline_steps.append("regime")
    return ctx


def step_parallel_modules(ctx: DecisionContext) -> DecisionContext:
    """Layer2: Registry 发现模块 → 并行执行 enrich()"""
    # W2 适配后实现：从 registry 获取所有 analysis 层模块，asyncio.gather 并行
    # 现在先标记
    ctx.pipeline_steps.append("parallel_modules")
    return ctx


def step_confidence_gate(ctx: DecisionContext) -> DecisionContext:
    """Layer3: 置信度门控 — 一致分>0.7+分歧<0.3 → 直出，否则仲裁"""
    if not ctx.modules_results:
        ctx.gate_decision = "direct_output"
        ctx.gate_reason = "无模块结果，跳过门控"
        ctx.pipeline_steps.append("confidence_gate")
        return ctx

    # 计算加权一致分和分歧度
    directions = []
    scores = []
    for name, result in ctx.modules_results.items():
        d = result.get("direction", "neutral")
        s = result.get("score", 0.5)
        directions.append(d)
        scores.append(s)

    if scores:
        ctx.confidence_score = sum(scores) / len(scores)

        # 分歧度：方向不一致的比例
        if len(directions) > 1:
            from collections import Counter
            dir_counts = Counter(directions)
            majority = dir_counts.most_common(1)[0][1]
            ctx.divergence = 1 - (majority / len(directions))
        else:
            ctx.divergence = 0.0

    # 门控决策
    if ctx.confidence_score >= 0.7 and ctx.divergence < 0.3:
        ctx.gate_decision = "direct_output"
        ctx.gate_reason = f"一致分{ctx.confidence_score:.2f}≥0.7 且 分歧{ctx.divergence:.2f}<0.3"
    else:
        ctx.gate_decision = "llm_arbitration"
        ctx.gate_reason = f"一致分{ctx.confidence_score:.2f} 或 分歧{ctx.divergence:.2f} 未达标"

    ctx.pipeline_steps.append("confidence_gate")
    return ctx


def step_llm_arbitration(ctx: DecisionContext) -> DecisionContext:
    """Layer3.5: LLM 仲裁（仅 gate_decision == llm_arbitration 时执行）"""
    if ctx.gate_decision != "llm_arbitration":
        ctx.pipeline_steps.append("llm_arbitration_skipped")
        return ctx

    # W7 实现：调 llm_gateway
    # 现在先标记，不实际调 LLM
    ctx.llm_called = True
    ctx.llm_model = "deepseek-chat"
    ctx.llm_calls_count += 1
    ctx.pipeline_steps.append("llm_arbitration")
    return ctx


def step_payoff_ev(ctx: DecisionContext) -> DecisionContext:
    """Layer4: 不对称赔率 + EV 计算"""
    # W6 实现：ATR止损 + half-Kelly + EV公式
    # 现在骨架
    ctx.pipeline_steps.append("payoff_ev")
    return ctx


def step_portfolio_doctor(ctx: DecisionContext) -> DecisionContext:
    """Layer4.5: 持仓体检（仅 cautious 管线）"""
    # W5 实现：压力测试 + 集中度 + 相关性
    ctx.pipeline_steps.append("portfolio_doctor")
    return ctx


def step_risk_firewall(ctx: DecisionContext) -> DecisionContext:
    """Layer5: 风控防火墙"""
    # 检查风控铁律
    # W3 实现：读 RISK_LIMITS + ctx.risk_overrides
    # 现在骨架：如果有 danger 级别预警，一票否决
    for alert in ctx.risk_alerts:
        if alert.get("level") == "danger":
            ctx.risk_override = True
            break

    if ctx.risk_override:
        ctx.final_direction = "blocked"
        ctx.final_conclusion = "风控一票否决：" + ctx.risk_alerts[0].get("msg", "风险过高")

    ctx.pipeline_steps.append("risk_firewall")
    return ctx


def step_output(ctx: DecisionContext) -> DecisionContext:
    """Layer6: 组装最终输出"""
    if ctx.final_direction == "blocked":
        # 已被风控否决
        ctx.pipeline_steps.append("output")
        return ctx

    # 如果门控直出（未调 LLM）
    if ctx.gate_decision == "direct_output" and not ctx.llm_called:
        # 从模块结果汇总
        if ctx.modules_results:
            from collections import Counter
            dirs = [r.get("direction", "neutral") for r in ctx.modules_results.values()]
            majority = Counter(dirs).most_common(1)[0][0]
            ctx.set_final(
                direction=majority,
                confidence=int(ctx.confidence_score * 100),
                conclusion=f"模块一致看{majority}(门控直出，未调LLM)",
            )
        else:
            ctx.set_final("neutral", 50, "无模块结果")

    # 如果 LLM 仲裁
    elif ctx.llm_result:
        ctx.set_final(
            direction=ctx.llm_result.get("direction", "neutral"),
            confidence=ctx.llm_result.get("confidence", 50),
            conclusion=ctx.llm_result.get("conclusion", ""),
            reasoning=ctx.llm_result.get("reasoning", ""),
        )

    ctx.pipeline_steps.append("output")
    return ctx


def step_ema_calibration(ctx: DecisionContext) -> DecisionContext:
    """Layer7: EMA 权重自校准"""
    # W4 实现：judgment_tracker 读历史准确率，调整模块权重
    ctx.pipeline_steps.append("ema_calibration")
    return ctx


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 管线定义
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PIPELINES: dict[str, list[Callable]] = {
    "default": [  # 日常决策 9 步
        step_load_user_data,
        step_regime,
        step_parallel_modules,
        step_confidence_gate,
        step_llm_arbitration,
        step_payoff_ev,
        step_risk_firewall,
        step_output,
        step_ema_calibration,
    ],
    "fast": [  # 紧急/盘中 5 步（0 次 LLM）
        step_load_user_data,
        step_regime,
        step_parallel_modules,
        step_risk_firewall,
        step_output,
    ],
    "cautious": [  # 熊市/高波 10 步
        step_load_user_data,
        step_regime,
        step_parallel_modules,
        step_confidence_gate,
        step_llm_arbitration,
        step_payoff_ev,
        step_portfolio_doctor,
        step_risk_firewall,
        step_output,
        step_ema_calibration,
    ],
}

# 管线描述（给前端/日志用）
PIPELINE_INFO = {
    "default": {"name": "日常决策", "steps": 9, "llm_max": 1, "description": "门控60%直出+需要时仲裁"},
    "fast": {"name": "紧急快速", "steps": 5, "llm_max": 0, "description": "零LLM，纯模块+风控"},
    "cautious": {"name": "谨慎深度", "steps": 10, "llm_max": 1, "description": "含持仓体检，熊市专用"},
}


class PipelineRunner:
    """管线引擎 — 按步骤顺序执行 Pipeline"""

    def select_pipeline(self, regime: str) -> str:
        """根据 Regime 自动选择管线
        
        Args:
            regime: trending_bull / oscillating / high_vol_bear / rotation
        
        Returns: "default" / "fast" / "cautious"
        """
        if regime == "high_vol_bear":
            return "cautious"
        elif regime == "trending_bull":
            return "default"
        elif regime == "rotation":
            return "default"
        else:  # oscillating 或未知
            return "default"

    def run(self, pipeline_name: str, ctx: DecisionContext) -> DecisionContext:
        """同步执行管线
        
        Args:
            pipeline_name: "default" / "fast" / "cautious"
            ctx: DecisionContext 实例
        
        Returns: 执行完毕的 ctx
        """
        steps = PIPELINES.get(pipeline_name)
        if not steps:
            raise ValueError(f"未知管线: {pipeline_name}")

        ctx.pipeline_name = pipeline_name
        t0 = time.time()

        for step_fn in steps:
            try:
                ctx = step_fn(ctx)
            except Exception as e:
                # step 失败不中断 Pipeline，记录错误继续
                step_name = step_fn.__name__
                ctx.pipeline_steps.append(f"{step_name}_ERROR")
                print(f"[PIPELINE] ⚠️ {step_name} 失败: {e}")

        ctx.total_time_ms = int((time.time() - t0) * 1000)
        return ctx

    def list_pipelines(self) -> dict:
        """列出所有可用管线"""
        return {
            name: {
                **PIPELINE_INFO.get(name, {}),
                "step_names": [s.__name__ for s in steps],
            }
            for name, steps in PIPELINES.items()
        }


# ━━ 全局单例 ━━
runner = PipelineRunner()
