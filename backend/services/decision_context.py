"""
钱袋子 — DecisionContext（决策上下文）
v3.0 底座 #1

职责：
  全链路唯一数据载体，Pipeline 每层读写同一对象。
  Layer0(用户数据) → Layer1(Regime) → Layer2(模块结果) → Layer3(门控)
  → Layer4(赔率EV) → Layer5(风控) → Layer6(输出) → Layer7(EMA)

三视图：
  to_llm_context()  → 给 DeepSeek 的完整结构化上下文
  to_user_response() → 给前端展示的结果（脱敏）
  to_judgment_record() → 给 judgment_tracker 的审计记录

设计文档: §三
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class DecisionContext:
    """全链路决策上下文 — Pipeline 各层共享的唯一数据对象"""

    # ━━ Layer0: 用户数据 ━━
    user_id: str = ""
    question: str = ""                          # 用户提问 / 触发源
    question_stock_code: str = ""               # 从问题提取的股票代码（如 002624）
    question_stock_name: str = ""               # 从问题提取的股票名（如 完美世界）
    trigger: str = "manual"                     # manual / cron / alert
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # 用户持仓（steward 从 stock_monitor/fund_monitor 加载）
    stock_holdings: list = field(default_factory=list)   # [{code, name, shares, costPrice, ...}]
    fund_holdings: list = field(default_factory=list)     # [{code, name, shares, costNav, ...}]

    # 用户记忆（从 agent_memory 加载）
    user_preferences: dict = field(default_factory=dict)  # risk_profile, focus, exclude
    user_memory: str = ""                                  # build_memory_summary() 输出
    recent_decisions: list = field(default_factory=list)   # 最近3条决策

    # ━━ Layer1: Regime（市场状态） ━━
    regime: str = ""                    # trending_bull / oscillating / high_vol_bear / rotation
    regime_confidence: float = 0.0      # 0-1
    regime_details: dict = field(default_factory=dict)  # {trend, volatility, breadth, ...}

    # ━━ Layer2: 模块并行结果 ━━
    modules_called: list = field(default_factory=list)     # ["signal", "risk", "ai_predictor", ...]
    modules_results: dict = field(default_factory=dict)    # {module_name: {direction, score, detail}}
    modules_errors: dict = field(default_factory=dict)     # {module_name: error_msg}  失败不阻塞
    modules_skipped: list = field(default_factory=list)    # 跳过的模块（缺依赖/scope不匹配等）
    errors: list = field(default_factory=list)             # 全局错误收集

    # ━━ Layer3: 置信度门控 ━━
    confidence_score: float = 0.0       # 加权一致分 0-1
    divergence: float = 0.0             # 分歧度 0-1（越高=模块越不一致）
    gate_decision: str = ""             # "direct_output" / "llm_arbitration"
    gate_reason: str = ""               # 门控决策原因

    # ━━ Layer3.5: LLM 仲裁（仅 gate_decision == llm_arbitration 时有值）━━
    llm_called: bool = False
    llm_model: str = ""                 # deepseek-chat / deepseek-reasoner
    llm_input_tokens: int = 0           # 送给 DS 的 token 数
    llm_output_tokens: int = 0
    llm_result: dict = field(default_factory=dict)  # {conclusion, direction, confidence, reasoning}
    llm_reasoning: str = ""                # LLM 仲裁推理过程

    # ━━ Layer4: 赔率 EV ━━
    payoff: dict = field(default_factory=dict)  # {upside, downside, stop_loss, position_kelly}
    ev: float = 0.0                              # 期望值 = winrate*(gain-cost) - lossrate*(loss+cost)
    ev_decision: str = ""                        # "pass" / "block"  E≤0 → block

    # ━━ Layer5: 风控防火墙 ━━
    risk_alerts: list = field(default_factory=list)     # [{level, msg, rule}]
    risk_override: bool = False                          # 风控一票否决
    risk_position_limit: float = 1.0                     # 风控建议最大仓位比例
    risk_level: str = ""                                 # normal/warning/danger/blocked
    risk_actions: list = field(default_factory=list)     # 风控操作建议

    # ━━ Layer6: 最终输出 ━━
    direction: str = "neutral"          # 简写：bullish/bearish/neutral/blocked
    confidence: int = 0                 # 简写：0-100
    conclusion: str = ""                # 简写：一句话结论
    final_direction: str = "neutral"    # bullish / bearish / neutral / blocked
    final_confidence: int = 0           # 0-100
    final_conclusion: str = ""          # 一句话结论
    final_reasoning: str = ""           # 完整推理过程
    final_actions: list = field(default_factory=list)  # [{action, target, reason}]

    # ━━ Layer7: EMA 权重校准 ━━
    weight_adjustments: dict = field(default_factory=dict)  # {module_name: new_weight}
    module_weights: dict = field(default_factory=dict)       # 当前各模块权重

    # ━━ 元数据 ━━
    pipeline_name: str = ""             # default / fast / cautious
    pipeline_steps: list = field(default_factory=list)  # 实际执行的 step 名称列表
    total_time_ms: int = 0              # 总耗时
    llm_calls_count: int = 0            # LLM 调用次数（门控验证用）
    elapsed_seconds: float = 0.0        # steward 总耗时（秒）

    # ━━ 运行时扩展字段（enrich/step 可以动态添加） ━━
    regime_params: dict = field(default_factory=dict)
    regime_description: str = ""
    memory_summary: str = ""
    ev_params: dict = field(default_factory=dict)
    ev_blocked: bool = False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 三视图
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def to_llm_context(self) -> str:
        """视图1: 给 DeepSeek 的完整结构化上下文
        
        包含所有层的数据，让 DS 在仲裁时看到全貌。
        """
        lines = []

        # 用户信息
        lines.append(f"## 用户: {self.user_id}")
        lines.append(f"问题: {self.question}")
        if self.user_preferences:
            rp = self.user_preferences.get("risk_profile", "未设置")
            lines.append(f"风险偏好: {rp}")
        if self.user_memory:
            lines.append(f"\n## 用户记忆\n{self.user_memory}")

        # 持仓
        if self.stock_holdings:
            lines.append(f"\n## 股票持仓 ({len(self.stock_holdings)}只)")
            for h in self.stock_holdings[:10]:
                lines.append(f"  {h.get('name', h.get('code', '?'))}({h.get('code', '')}) "
                           f"成本¥{h.get('costPrice', '?')} × {h.get('shares', '?')}股")
        if self.fund_holdings:
            lines.append(f"\n## 基金持仓 ({len(self.fund_holdings)}只)")
            for h in self.fund_holdings[:10]:
                lines.append(f"  {h.get('name', h.get('code', '?'))}({h.get('code', '')})")

        # Regime
        if self.regime:
            lines.append(f"\n## 市场状态 (Regime)")
            lines.append(f"类型: {self.regime} (置信度 {self.regime_confidence:.0%})")
            if self.regime_details:
                for k, v in self.regime_details.items():
                    lines.append(f"  {k}: {v}")

        # 模块结果
        if self.modules_results:
            lines.append(f"\n## {len(self.modules_results)} 个模块分析结果")
            for name, result in self.modules_results.items():
                direction = result.get("direction", "?")
                score = result.get("score", "?")
                detail = result.get("detail", "")[:100]
                lines.append(f"  [{name}] 方向={direction} 分数={score} {detail}")

        # 门控
        if self.gate_decision:
            lines.append(f"\n## 门控判断")
            lines.append(f"一致分: {self.confidence_score:.2f} | 分歧度: {self.divergence:.2f}")
            lines.append(f"决策: {self.gate_decision} ({self.gate_reason})")

        # 赔率
        if self.payoff:
            lines.append(f"\n## 赔率分析")
            lines.append(f"EV={self.ev:.4f} | 上行={self.payoff.get('upside', '?')} | "
                        f"下行={self.payoff.get('downside', '?')}")
            if self.ev_decision == "block":
                lines.append(f"⚠️ 负EV，建议阻止")

        # 风控
        if self.risk_alerts:
            lines.append(f"\n## ⚠️ 风控预警 ({len(self.risk_alerts)}条)")
            for a in self.risk_alerts:
                lines.append(f"  [{a.get('level', 'info')}] {a.get('msg', '')}")
            if self.risk_override:
                lines.append(f"🔴 风控一票否决")

        return "\n".join(lines)

    def to_user_response(self) -> dict:
        """视图2: 给前端展示的结果（脱敏、结构化）"""
        return {
            "direction": self.direction or self.final_direction,
            "confidence": self.confidence or self.final_confidence,
            "conclusion": self.conclusion or self.final_conclusion,
            "reasoning": self.final_reasoning,
            "actions": self.final_actions,
            "regime": self.regime,
            "regime_description": self.regime_description,
            "pipeline": self.pipeline_name,
            "pipeline_steps": self.pipeline_steps,
            "modules_called": self.modules_called,
            "modules_count": len(self.modules_called),
            "modules_results": {
                name: {
                    "available": r.get("available", False),
                    "direction": r.get("direction", "neutral"),
                    "confidence": r.get("confidence", r.get("score", 0)),
                }
                for name, r in self.modules_results.items()
                if isinstance(r, dict)
            },
            "gate_decision": self.gate_decision,
            "gate_reason": self.gate_reason,
            "ev": round(self.ev, 4) if self.ev else None,
            "ev_params": self.ev_params if self.ev_params else None,
            "risk_level": self.risk_level or "normal",
            "risk_override": self.risk_override,
            "risk_alerts": self.risk_alerts[:5] if self.risk_alerts else [],
            "llm_called": self.llm_called,
            "llm_model": self.llm_model if self.llm_called else None,
            "llm_calls": self.llm_calls_count,
            "llm_reasoning": self.llm_reasoning if self.llm_called else None,
            "elapsed": self.elapsed_seconds,
            "total_time_ms": self.total_time_ms,
            "timestamp": self.timestamp,
        }

    def to_judgment_record(self) -> dict:
        """视图3: 给 judgment_tracker 的审计记录（完整、可追溯）"""
        return {
            "user_id": self.user_id,
            "timestamp": self.timestamp,
            "question": self.question,
            "trigger": self.trigger,
            "regime": self.regime,
            "pipeline": self.pipeline_name,
            "modules_called": self.modules_called,
            "modules_results": {
                name: {
                    "direction": r.get("direction"),
                    "score": r.get("score"),
                }
                for name, r in self.modules_results.items()
            },
            "confidence_score": self.confidence_score,
            "divergence": self.divergence,
            "gate_decision": self.gate_decision,
            "llm_called": self.llm_called,
            "llm_model": self.llm_model,
            "llm_result": self.llm_result,
            "ev": self.ev,
            "ev_decision": self.ev_decision,
            "risk_override": self.risk_override,
            "risk_alerts": self.risk_alerts,
            "final_direction": self.final_direction,
            "final_confidence": self.final_confidence,
            "final_conclusion": self.final_conclusion,
            "llm_calls_count": self.llm_calls_count,
            "total_time_ms": self.total_time_ms,
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 辅助方法
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def add_module_result(self, name: str, direction: str, score: float, detail: str = ""):
        """Layer2: 添加一个模块的分析结果"""
        self.modules_called.append(name)
        self.modules_results[name] = {
            "direction": direction,
            "score": score,
            "detail": detail,
        }

    def add_module_error(self, name: str, error: str):
        """Layer2: 记录模块执行失败（不阻塞 Pipeline）"""
        self.modules_errors[name] = error

    def add_risk_alert(self, level: str, msg: str, rule: str = ""):
        """Layer5: 添加风控预警"""
        self.risk_alerts.append({"level": level, "msg": msg, "rule": rule})

    def set_final(self, direction: str, confidence: int, conclusion: str,
                  reasoning: str = "", actions: list = None):
        """Layer6: 设置最终输出"""
        self.final_direction = direction
        self.final_confidence = confidence
        self.final_conclusion = conclusion
        self.final_reasoning = reasoning
        self.final_actions = actions or []
        # 同步简写字段
        self.direction = direction
        self.confidence = confidence
        self.conclusion = conclusion

    def validate_before_llm(self) -> list[str]:
        """门控送 LLM 前的完整性校验 — 返回缺失字段列表"""
        issues = []
        if not self.regime:
            issues.append("regime 未设置")
        if not self.modules_results:
            issues.append("无模块分析结果")
        if self.confidence_score == 0 and self.divergence == 0:
            issues.append("门控分数未计算")
        return issues
