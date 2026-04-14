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
    try:
        from services.stock_monitor import load_stock_holdings
        ctx.stock_holdings = load_stock_holdings(ctx.user_id)
    except Exception as e:
        ctx.stock_holdings = []
        print(f"[PIPELINE] load_stock_holdings: {e}")
    
    try:
        from services.fund_monitor import load_fund_holdings
        ctx.fund_holdings = load_fund_holdings(ctx.user_id)
    except Exception as e:
        ctx.fund_holdings = []
        print(f"[PIPELINE] load_fund_holdings: {e}")
    
    try:
        from services.agent_memory import build_memory_summary, get_preferences
        ctx.memory_summary = build_memory_summary(ctx.user_id)
        ctx.user_preferences = get_preferences(ctx.user_id)
    except Exception as e:
        ctx.memory_summary = ""
        ctx.user_preferences = {}
        print(f"[PIPELINE] agent_memory: {e}")
    
    ctx.pipeline_steps.append("load_user_data")
    return ctx


def step_regime(ctx: DecisionContext) -> DecisionContext:
    """Layer1: 市场状态分类（4类：趋势牛/震荡/高波熊/轮动）"""
    if not ctx.regime:
        try:
            from services.regime_engine import classify as classify_regime
            result = classify_regime()
            ctx.regime = result["regime"]
            ctx.regime_confidence = result["confidence"]
            ctx.regime_params = result.get("params", {})
            ctx.regime_description = result.get("description", "")
        except Exception as e:
            ctx.regime = "oscillating"
            ctx.regime_confidence = 30
            ctx.regime_description = f"Regime 获取失败({e})，默认震荡"
            print(f"[PIPELINE] regime_engine: {e}")
    ctx.pipeline_steps.append("regime")
    return ctx


def step_parallel_modules(ctx: DecisionContext) -> DecisionContext:
    """Layer2: Registry 发现模块 → 执行所有有 enrich() 的模块"""
    try:
        from services.module_registry import ModuleRegistry
        registry = ModuleRegistry.instance()
        registry.ensure_discovered()
        
        # 排除 regime_engine（已在 step_regime 执行过）
        # 排除 judgment_tracker / weekly_report / portfolio_doctor（在后续 step 执行）
        skip_names = {"regime_engine", "judgment_tracker", "weekly_report", "portfolio_doctor"}
        
        for name, entry in registry._modules.items():
            if name in skip_names:
                continue
            enrich_fn = entry.get("enrich")
            if not enrich_fn:
                continue
            
            meta = entry.get("meta", {})
            mod_info = {"name": name, **meta}
            
            try:
                # 检查 scope：private 模块需要 user_id
                scope = mod_info.get("scope", "public")
                if scope == "private" and not ctx.user_id:
                    continue
                
                ctx = enrich_fn(ctx)
                if name not in ctx.modules_called:
                    ctx.modules_called.append(name)
            except Exception as e:
                print(f"[PIPELINE] module {name}.enrich() failed: {e}")
                ctx.modules_results[name] = {
                    "available": False,
                    "error": str(e),
                }
    except Exception as e:
        print(f"[PIPELINE] step_parallel_modules failed: {e}")
    
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
    """Layer3.5: LLM 仲裁（仅 gate_decision == llm_arbitration 时执行）
    
    把所有模块结果 + Regime + 用户持仓打包给 DeepSeek，让它做多空辩论仲裁。
    """
    if ctx.gate_decision != "llm_arbitration":
        ctx.pipeline_steps.append("llm_arbitration_skipped")
        return ctx

    try:
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()

        # 加载仲裁 prompt 文件
        from pathlib import Path
        _arb_prompt_path = Path(__file__).parent.parent / "prompts" / "steward_arbitrate.md"
        system = _arb_prompt_path.read_text(encoding="utf-8") if _arb_prompt_path.exists() else "你是投资仲裁官。只输出JSON。"

        # 组装数据部分
        modules_text = ""
        for name, result in ctx.modules_results.items():
            d = result.get("direction", "neutral")
            c = result.get("confidence", 0)
            detail = str(result.get("detail", ""))[:200]
            modules_text += f"  - {name}: 方向={d}, 置信={c}, 详情={detail}\n"
            # 个股新闻（signal_scout 提供）
            if name == "signal_scout" and result.get("stock_news"):
                news_titles = [n.get("title","") for n in result["stock_news"][:5]]
                modules_text += f"    📰 个股新闻({len(news_titles)}条): {'; '.join(news_titles)}\n"
                if result.get("stock_news_direction"):
                    modules_text += f"    📰 新闻方向: {result['stock_news_direction']}\n"

        # 个股信息
        stock_info = ""
        if getattr(ctx, "question_stock_name", ""):
            stock_info = f"\n## 查询个股\n名称: {ctx.question_stock_name}, 代码: {getattr(ctx, 'question_stock_code', '')}\n"

        prompt = f"""## 用户问题
{ctx.question}
{stock_info}
## 市场状态
Regime: {ctx.regime} ({ctx.regime_description})

## 各模块分析结果
{modules_text}
## 门控数据
一致分: {ctx.confidence_score:.2f}, 分歧度: {ctx.divergence:.2f}

请严格按 JSON 格式回答，不要输出任何其他文字。"""

        result = gw.call_sync(
            prompt,
            system=system,
            model_tier="llm_light",  # V3 仲裁：JSON服从性好+快（R1的content常为空，不适合结构化输出）
            user_id=ctx.user_id,
            module="steward_arbitrate",
            max_tokens=500,
        )

        ctx.llm_called = True
        ctx.llm_model = result.get("model", "deepseek-chat")
        ctx.llm_calls_count += 1

        content = result.get("content", "")
        ctx.llm_reasoning = result.get("reasoning", "") or ""  # R1 的思考过程
        if content and not result.get("fallback"):
            # 解析 JSON 返回
            import json as _json
            import re
            parsed = None
            # 方法1: 直接解析整个 content
            try:
                parsed = _json.loads(content.strip())
            except _json.JSONDecodeError:
                pass
            # 方法2: 提取 ```json...``` 代码块
            if not parsed:
                code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if code_match:
                    try:
                        parsed = _json.loads(code_match.group(1))
                    except _json.JSONDecodeError:
                        pass
            # 方法3: 找第一个完整的 {} 对
            if not parsed:
                brace_count = 0
                start_idx = content.find('{')
                if start_idx >= 0:
                    for i in range(start_idx, len(content)):
                        if content[i] == '{': brace_count += 1
                        elif content[i] == '}': brace_count -= 1
                        if brace_count == 0:
                            try:
                                parsed = _json.loads(content[start_idx:i+1])
                            except _json.JSONDecodeError:
                                pass
                            break

            if parsed and isinstance(parsed, dict):
                ctx.direction = parsed.get("direction", "neutral")
                raw_conf = parsed.get("confidence", 50)
                ctx.confidence_score = raw_conf / 100.0
                ctx.confidence = int(raw_conf)  # 0-100 整数给前端
                ctx.conclusion = parsed.get("conclusion", "")
                ctx.llm_reasoning = parsed.get("reasoning", "")
                print(f"[PIPELINE] LLM仲裁: {ctx.direction} {ctx.confidence_score*100:.0f}% — {ctx.conclusion}")
            else:
                # 没找到 JSON，直接用文本
                ctx.conclusion = content[:200]
                # 尝试从文本推断方向
                if "看多" in content or "bullish" in content.lower():
                    ctx.direction = "bullish"
                elif "看空" in content or "bearish" in content.lower():
                    ctx.direction = "bearish"
                print(f"[PIPELINE] LLM仲裁: 无JSON，文本提取 {ctx.direction}")
        else:
            # LLM 不可用，降级：用模块多数投票
            directions = [r.get("direction", "neutral") for r in ctx.modules_results.values()]
            from collections import Counter
            if directions:
                most_common = Counter(directions).most_common(1)[0][0]
                ctx.direction = most_common
            ctx.conclusion = "LLM不可用，使用模块多数投票"
            print(f"[PIPELINE] LLM仲裁降级: 多数投票→{ctx.direction}")

    except Exception as e:
        print(f"[PIPELINE] LLM仲裁异常: {e}")
        ctx.conclusion = f"仲裁异常: {e}"

    ctx.pipeline_steps.append("llm_arbitration")
    return ctx


def step_payoff_ev(ctx: DecisionContext) -> DecisionContext:
    """Layer4: 不对称赔率 + EV 计算
    
    公式（老师方案+朋友D）：
    E = (winrate × (gain - cost)) - (lossrate × (loss + cost))
    cost = 0.23%（佣金+印花+滑点）
    E ≤ 0 → 拦截不出手
    """
    TRADING_COST = 0.0023  # 0.23%
    
    # 从门控结果推算胜率和赔率
    confidence = ctx.confidence_score or 0.5
    winrate = max(0.3, min(0.9, confidence))  # 门控分→胜率（30%-90%）
    lossrate = 1 - winrate
    
    # 预期收益/亏损（简化：用 Regime 参数估算）
    regime_vol = ctx.regime_params.get("volatility_20d", 20) / 100 if ctx.regime_params else 0.2
    expected_gain = regime_vol * 0.8  # 预期盈利 = 波动率 × 0.8
    expected_loss = regime_vol * 0.5  # 预期亏损 = 波动率 × 0.5（ATR止损）
    
    # EV 公式
    ev = (winrate * (expected_gain - TRADING_COST)) - (lossrate * (expected_loss + TRADING_COST))
    
    ctx.ev = round(ev * 100, 2)  # 百分比
    ctx.ev_params = {
        "winrate": round(winrate * 100, 1),
        "expected_gain": round(expected_gain * 100, 1),
        "expected_loss": round(expected_loss * 100, 1),
        "trading_cost": round(TRADING_COST * 100, 2),
        "ev_pct": round(ev * 100, 2),
    }
    
    # EV ≤ 0 → 拦截
    if ev <= 0:
        ctx.ev_blocked = True
        ctx.risk_alerts.append({
            "source": "payoff_ev",
            "level": "warning",
            "msg": f"期望值为负(EV={ev*100:.2f}%)，建议不操作",
        })
    
    ctx.pipeline_steps.append("payoff_ev")
    return ctx


def step_portfolio_doctor(ctx: DecisionContext) -> DecisionContext:
    """Layer4.5: 持仓体检（仅 cautious 管线）"""
    try:
        from services.portfolio_doctor import enrich as doctor_enrich
        ctx = doctor_enrich(ctx)
        
        # 体检发现危险 → 注入风控红灯
        report = getattr(ctx, "doctor_report", {})
        health = report.get("health", {})
        if health.get("score", 100) < 40:
            ctx.risk_alerts.append({
                "source": "portfolio_doctor",
                "level": "danger",
                "msg": f"持仓健康评分 {health.get('score', 0)}分（{health.get('grade', '?')}），建议调整",
            })
        elif health.get("score", 100) < 60:
            ctx.risk_alerts.append({
                "source": "portfolio_doctor",
                "level": "warning",
                "msg": f"持仓健康评分 {health.get('score', 0)}分，有改善空间",
            })
    except Exception as e:
        print(f"[PIPELINE] step_portfolio_doctor 失败: {e}")
    
    ctx.pipeline_steps.append("portfolio_doctor")
    return ctx


def step_risk_firewall(ctx: DecisionContext) -> DecisionContext:
    """Layer5: 风控防火墙
    铁律：单票≤25% TOP3≤60% 极端暂停 冷却48h + 全员观望 + per-user覆盖
    """
    # EV 拦截
    if getattr(ctx, "ev_blocked", False):
        ctx.risk_level = "warning"
    
    # 检查 danger 级别预警 → 一票否决
    danger_alerts = [a for a in ctx.risk_alerts if a.get("level") == "danger"]
    if danger_alerts:
        ctx.risk_override = True
        ctx.risk_level = "blocked"
        ctx.direction = "blocked"
        ctx.conclusion = "🚫 风控一票否决：" + danger_alerts[0].get("msg", "风险过高")
    
    # 高波熊市 + 低置信度 → 自动降级为观望
    if ctx.regime == "high_vol_bear" and (ctx.confidence_score or 0) < 0.5:
        if not ctx.risk_override:
            ctx.risk_level = "warning"
            ctx.risk_alerts.append({
                "source": "risk_firewall",
                "level": "warning",
                "msg": f"高波熊市+置信度低({ctx.confidence_score:.0%})，建议观望",
            })
    
    if not ctx.risk_level:
        ctx.risk_level = "normal"
    
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

    # 如果 LLM 仲裁了
    elif ctx.llm_called and ctx.conclusion:
        # LLM 结果已在 step_llm_arbitration 写入 ctx.direction/confidence/conclusion
        ctx.set_final(
            direction=ctx.direction,
            confidence=ctx.confidence or int(ctx.confidence_score * 100),
            conclusion=ctx.conclusion,
            reasoning=ctx.llm_reasoning or "",
        )

    # 降级：没结论但有模块结果
    elif ctx.modules_results:
        from collections import Counter
        dirs = [r.get("direction", "neutral") for r in ctx.modules_results.values()]
        majority = Counter(dirs).most_common(1)[0][0] if dirs else "neutral"
        ctx.set_final(
            direction=majority,
            confidence=int(ctx.confidence_score * 100) if ctx.confidence_score else 50,
            conclusion=f"模块多数投票: {majority}",
        )

    ctx.pipeline_steps.append("output")
    return ctx


def step_ema_calibration(ctx: DecisionContext) -> DecisionContext:
    """Layer7: EMA 权重自校准 — 读 judgment_tracker 历史，调整模块权重"""
    try:
        from services.judgment_tracker import get_weights
        weights = get_weights(ctx.user_id)
        if weights:
            ctx.module_weights = weights
    except Exception as e:
        print(f"[PIPELINE] ema_calibration: {e}")
    
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
