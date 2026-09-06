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
from collections import Counter
from typing import Callable, Optional
from services.decision_context import DecisionContext

# v9.9.10: LLM 仲裁失败（解析失败 / 输出被截断 / 调用异常）时的安全占位文案。
# 绝不回退成 LLM 原文 —— 那正是线上把裸 JSON 片段推给用户的根因。
_SAFE_ARBITRATION_FALLBACK = "模块综合判断，详情请打开钱袋子查看"


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
    """Layer2: Registry 发现模块 → 执行所有有 enrich() 的模块（单模块 5s 超时）"""
    import signal

    class _ModuleTimeout(Exception):
        pass

    try:
        from services.module_registry import ModuleRegistry
        registry = ModuleRegistry.instance()
        registry.ensure_discovered()

        skip_names = {"regime_engine", "judgment_tracker", "weekly_report", "portfolio_doctor"}

        for name, entry in registry._modules.items():
            if name in skip_names:
                continue
            enrich_fn = entry.get("enrich")
            if not enrich_fn:
                continue

            meta = entry.get("meta", {})
            scope = meta.get("scope", "public")
            if scope == "private" and not ctx.user_id:
                ctx.modules_skipped.append(f"{name}:no_user_id")
                continue

            if name not in ctx.modules_called:
                ctx.modules_called.append(name)

            try:
                # 使用 threading 超时（兼容 uvicorn worker 线程）
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(enrich_fn, ctx)
                    try:
                        ctx = future.result(timeout=15)  # FIX: 8s→15s，stock_screen 等模块数据量大时 8s 不够
                    except concurrent.futures.TimeoutError:
                        raise _ModuleTimeout("timeout 15s")
            except _ModuleTimeout:
                err_msg = "timeout 15s"
                print(f"[PIPELINE] module {name}.enrich() timeout 15s, skipped")
                ctx.modules_results[name] = {"available": False, "error": err_msg}
                ctx.modules_errors[name] = err_msg
            except Exception as e:
                err_msg = str(e)[:200]
                print(f"[PIPELINE] module {name}.enrich() failed: {err_msg}")
                ctx.modules_results[name] = {"available": False, "error": err_msg}
                ctx.modules_errors[name] = err_msg
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
    # FIX 2026-04-19 V7.2: 阈值从 config 读
    from config import PIPELINE_GATE
    _conf_thr = PIPELINE_GATE["confidence_threshold"]
    _div_thr  = PIPELINE_GATE["divergence_threshold"]
    if ctx.confidence_score >= _conf_thr and ctx.divergence < _div_thr:
        ctx.gate_decision = "direct_output"
        ctx.gate_reason = f"一致分{ctx.confidence_score:.2f}≥{_conf_thr} 且 分歧{ctx.divergence:.2f}<{_div_thr}"
    else:
        ctx.gate_decision = "llm_arbitration"
        ctx.gate_reason = f"一致分{ctx.confidence_score:.2f} 或 分歧{ctx.divergence:.2f} 未达标"

    ctx.pipeline_steps.append("confidence_gate")
    return ctx


def _parse_llm_json(content: str) -> Optional[dict]:
    """从 LLM 输出中提取 JSON dict（复用现有三级解析策略）。

    返回 dict；解析失败返回 None。绝不返回非 dict 类型。
    """
    if not content:
        return None
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
                if content[i] == '{':
                    brace_count += 1
                elif content[i] == '}':
                    brace_count -= 1
                if brace_count == 0:
                    try:
                        parsed = _json.loads(content[start_idx:i + 1])
                    except _json.JSONDecodeError:
                        pass
                    break
    return parsed if isinstance(parsed, dict) else None


def _llm_result_content(result: dict) -> str:
    """提取 LLM 输出正文；若被 max_tokens 截断则置空（复用截断降级逻辑）。"""
    content = result.get("content", "") or ""
    if result.get("finish_reason") == "length":
        print("[PIPELINE] LLM输出被 max_tokens 截断，按解析失败降级")
        return ""
    return content


def _read_prompt(prompts_dir, filename: str, default: str) -> str:
    """读取 prompt 文件；不存在或读取失败时返回默认值（fail-open）。"""
    try:
        path = prompts_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[PIPELINE] 读取 prompt {filename} 失败: {e}")
    return default


def _build_modules_text(ctx: DecisionContext) -> str:
    """组装各模块分析结果文本（首轮/空头/复核共用）。"""
    modules_text = ""
    for name, result in ctx.modules_results.items():
        d = result.get("direction", "neutral")
        c = result.get("confidence", 0)
        detail = str(result.get("detail", ""))[:200]
        modules_text += f"  - {name}: 方向={d}, 置信={c}, 详情={detail}\n"
        # 个股新闻（signal_scout 提供）
        if name == "signal_scout" and result.get("stock_news"):
            news_titles = [n.get("title", "") for n in result["stock_news"][:5]]
            modules_text += f"    📰 个股新闻({len(news_titles)}条): {'; '.join(news_titles)}\n"
            if result.get("stock_news_direction"):
                modules_text += f"    📰 新闻方向: {result['stock_news_direction']}\n"
    return modules_text


def _build_stock_info(ctx: DecisionContext) -> str:
    """组装查询个股信息（首轮/空头/复核共用）。"""
    if getattr(ctx, "question_stock_name", ""):
        return f"\n## 查询个股\n名称: {ctx.question_stock_name}, 代码: {getattr(ctx, 'question_stock_code', '')}\n"
    return ""


def _fallback_module_vote(ctx: DecisionContext, reason: str) -> None:
    """仲裁失败/LLM 不可用时的统一降级：模块多数投票 + 安全占位文案。

    绝不把 LLM 原文当结论（v9.9.10 泄漏根因）。
    """
    dirs = [r.get("direction", "neutral") for r in ctx.modules_results.values()]
    if dirs:
        ctx.direction = Counter(dirs).most_common(1)[0][0]
    ctx.conclusion = _SAFE_ARBITRATION_FALLBACK
    print(f"[PIPELINE] LLM仲裁降级({reason}): 多数投票→{ctx.direction}")


def _apply_arbitration_result(ctx: DecisionContext, parsed: dict) -> None:
    """把仲裁 JSON 写入 ctx 的 direction/confidence/conclusion/reasoning。"""
    ctx.direction = parsed.get("direction", "neutral")
    raw_conf = parsed.get("confidence", 50)
    try:
        raw_conf = int(raw_conf)
    except (TypeError, ValueError):
        raw_conf = 50
    ctx.confidence_score = raw_conf / 100.0
    ctx.confidence = raw_conf  # 0-100 整数给前端
    ctx.conclusion = parsed.get("conclusion", "")
    ctx.llm_reasoning = parsed.get("reasoning", "")


def _is_stock_fund_question(ctx: DecisionContext) -> bool:
    """判断是否为股票/基金类问题（启用空头反驳的前提）。

    question_is_fund 是 steward 运行时动态赋值的字段（不在 dataclass 里），
    question_stock_code/question_stock_name 同样用 getattr 防御。
    """
    return bool(
        getattr(ctx, "question_stock_code", "") or
        getattr(ctx, "question_stock_name", "") or
        getattr(ctx, "question_is_fund", False)
    )


def step_llm_arbitration(ctx: DecisionContext) -> DecisionContext:
    """Layer3.5: LLM 仲裁（仅 gate_decision == llm_arbitration 时执行）
    
    把所有模块结果 + Regime + 用户持仓打包给 DeepSeek，让它做多空辩论仲裁。
    T01: 首轮成功后，对股票/基金类问题追加空头反驳三步链
    （首轮仲裁 → 空头研究员 → 最终复核），全程 fail-open。
    """
    if ctx.gate_decision != "llm_arbitration":
        ctx.pipeline_steps.append("llm_arbitration_skipped")
        return ctx

    try:
        from infra.llm.gateway import LLMGateway
        gw = LLMGateway.instance()

        from pathlib import Path
        prompts_dir = Path(__file__).parent.parent / "prompts"
        system = _read_prompt(prompts_dir, "steward_arbitrate.md", "你是投资仲裁官。只输出JSON。")

        modules_text = _build_modules_text(ctx)
        stock_info = _build_stock_info(ctx)

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
            # v9.9.10: 500 → 1000。500 会把仲裁 JSON 截断成半截字符串，
            # 解析必然失败，进而触发"把原文当结论"的泄漏路径。
            max_tokens=1000,
        )

        ctx.llm_called = True
        ctx.llm_model = result.get("model", "deepseek-v4-flash")
        ctx.llm_calls_count += 1
        ctx.llm_reasoning = result.get("reasoning", "") or ""  # LLM 的思考过程

        content = _llm_result_content(result)

        if content and not result.get("fallback"):
            parsed = _parse_llm_json(content)
            if parsed is not None:
                _apply_arbitration_result(ctx, parsed)
                print(f"[PIPELINE] LLM仲裁: {ctx.direction} {ctx.confidence_score*100:.0f}% — {ctx.conclusion}")
                # ━━ T01: 空头反驳三步链（仅股票/基金类问题启用） ━━
                _run_bear_rebuttal_chain(ctx, gw, prompts_dir)
            else:
                _fallback_module_vote(ctx, "无有效JSON")
        else:
            _fallback_module_vote(ctx, "LLM不可用")

    except Exception as e:
        # v9.9.10: 异常原文可能含文件路径/接口报错，同样不能透出给用户
        print(f"[PIPELINE] LLM仲裁异常: {e}")
        ctx.conclusion = _SAFE_ARBITRATION_FALLBACK

    ctx.pipeline_steps.append("llm_arbitration")
    return ctx


def _run_bear_rebuttal_chain(ctx: DecisionContext, gw, prompts_dir) -> None:
    """T01: 空头反驳三步链（首轮仲裁成功后，仅股票/基金类问题启用）。

    步骤：首轮（已完成）→ 空头研究员 → 最终复核。
    降级（fail-open）：空头或复核任一 fallback / 截断 / 解析失败，
    打印日志、保持首轮结果、不阻塞主流程。
    """
    if not _is_stock_fund_question(ctx):
        return

    first_round = {
        "direction": ctx.direction,
        "confidence": ctx.confidence,
        "conclusion": ctx.conclusion,
        "reasoning": ctx.llm_reasoning,
    }

    bear = _run_bear_attack(ctx, gw, prompts_dir, first_round)
    if bear is None:
        return

    _run_final_review(ctx, gw, prompts_dir, first_round, bear)


def _run_bear_attack(ctx: DecisionContext, gw, prompts_dir, first_round: dict) -> Optional[dict]:
    """第2步：空头研究员。任一环节失败返回 None（保持首轮结果，fail-open）。"""
    system = _read_prompt(prompts_dir, "steward_bear_attack.md", "你是空头研究员。只输出JSON。")

    prompt = f"""## 首轮仲裁结论
方向: {first_round.get('direction')}
置信度: {first_round.get('confidence')}
结论: {first_round.get('conclusion')}
推理: {first_round.get('reasoning')}

## 原始模块数据
{_build_modules_text(ctx)}

请以空头研究员视角挑出首轮结论的漏洞，严格按 JSON 格式输出。"""

    try:
        result = gw.call_sync(
            prompt,
            system=system,
            model_tier="llm_light",
            user_id=ctx.user_id,
            module="steward_bear_attack",
            max_tokens=800,
        )
    except Exception as e:
        print(f"[PIPELINE] 空头研究员异常，保持首轮结果: {e}")
        return None

    ctx.llm_calls_count += 1

    content = _llm_result_content(result)
    if not content or result.get("fallback"):
        print("[PIPELINE] 空头研究员降级/无输出，保持首轮结果")
        return None

    parsed = _parse_llm_json(content)
    if parsed is None:
        print("[PIPELINE] 空头研究员无有效JSON，保持首轮结果")
        return None

    bear = {
        "bear_attack_points": parsed.get("bear_attack_points", []) or [],
        "strongest_objection": parsed.get("strongest_objection", "") or "",
        "overlooked_risk": parsed.get("overlooked_risk", "") or "",
        "fatal_risk": bool(parsed.get("fatal_risk", False)),
        "data_sources_referenced": parsed.get("data_sources_referenced", []) or [],
    }
    # 挂到 ctx（运行时扩展字段）供审计/透出，不参与主流程契约
    ctx.bear_attack = bear
    print(f"[PIPELINE] 空头研究员: fatal_risk={bear['fatal_risk']}, objection={bear['strongest_objection'][:40]}")
    return bear


def _run_final_review(ctx: DecisionContext, gw, prompts_dir, first_round: dict, bear: dict) -> None:
    """第3步：最终复核。任一环节失败则保持首轮结果（fail-open）。"""
    system = _read_prompt(prompts_dir, "steward_final_review.md", "你是最终裁决官。只输出JSON。")

    attack_points = "；".join(bear.get("bear_attack_points", []) or [])
    prompt = f"""## 首轮仲裁结论
方向: {first_round.get('direction')}
置信度: {first_round.get('confidence')}
结论: {first_round.get('conclusion')}
推理: {first_round.get('reasoning')}

## 空头研究员反驳
致命风险(fatal_risk): {bear.get('fatal_risk')}
最强反驳: {bear.get('strongest_objection')}
被忽略的风险: {bear.get('overlooked_risk')}
攻击点: {attack_points}

请综合双方给出最终裁决，严格按 JSON 格式输出。"""

    try:
        result = gw.call_sync(
            prompt,
            system=system,
            model_tier="llm_light",
            user_id=ctx.user_id,
            module="steward_final_review",
            max_tokens=1000,
        )
    except Exception as e:
        print(f"[PIPELINE] 最终复核异常，保持首轮结果: {e}")
        return

    ctx.llm_calls_count += 1

    content = _llm_result_content(result)
    if not content or result.get("fallback"):
        print("[PIPELINE] 最终复核降级/无输出，保持首轮结果")
        return

    parsed = _parse_llm_json(content)
    if parsed is None:
        print("[PIPELINE] 最终复核无有效JSON，保持首轮结果")
        return

    prev_direction = ctx.direction
    prev_conf = ctx.confidence

    new_direction = parsed.get("direction", prev_direction)
    raw_conf = parsed.get("confidence", prev_conf)
    try:
        new_conf = int(raw_conf)
    except (TypeError, ValueError):
        new_conf = prev_conf
    new_conf = max(0, min(100, new_conf))

    # ━━ 强影响机制 · 代码层兜底 ━━
    # LLM 层已由 steward_final_review.md 要求在 fatal_risk=true 时下调 confidence
    # （腰斩或 0-29）或翻转 direction。这里做代码层兜底：若复核结果未下调，
    # 打印 warning + 追加 risk_alert 透出 strongest_objection；不做代码层强制翻转。
    if bear.get("fatal_risk"):
        downgraded = (
            new_direction != prev_direction
            or new_conf <= 29
            or new_conf <= prev_conf / 2
        )
        if not downgraded:
            print(f"[PIPELINE] ⚠️ fatal_risk=true 但复核未下调(confidence {prev_conf}→{new_conf}, "
                  f"direction {prev_direction}→{new_direction})，追加 risk_alert")
            ctx.risk_alerts.append({
                "source": "bear_attack",
                "level": "warning",
                "msg": f"空头反驳提出致命风险：{bear.get('strongest_objection', '')[:120]}",
            })

    # 覆盖 ctx（复核结果）
    ctx.direction = new_direction
    ctx.confidence_score = new_conf / 100.0
    ctx.confidence = new_conf
    ctx.conclusion = parsed.get("conclusion", ctx.conclusion)
    ctx.llm_reasoning = parsed.get("reasoning", ctx.llm_reasoning)

    print(f"[PIPELINE] 最终复核: {ctx.direction} {ctx.confidence}% — {ctx.conclusion}")


def step_payoff_ev(ctx: DecisionContext) -> DecisionContext:
    """Layer4: 不对称赔率 + EV 计算
    
    公式（老师方案+朋友D）：
    E = (winrate × (gain - cost)) - (lossrate × (loss + cost))
    cost = 0.23%（佣金+印花+滑点）
    E ≤ 0 → 拦截不出手
    """
    # FIX 2026-04-19 V7.2: 常量从 config 读
    from config import PIPELINE_GATE
    TRADING_COST = PIPELINE_GATE["trading_cost"]  # 0.0023

    # 从门控结果推算胜率和赔率
    confidence = ctx.confidence_score or 0.5
    winrate = max(PIPELINE_GATE["winrate_min"],
                  min(PIPELINE_GATE["winrate_max"], confidence))  # 30%-90%
    lossrate = 1 - winrate

    # 预期收益/亏损（简化：用 Regime 参数估算）
    regime_vol = ctx.regime_params.get("volatility_20d", 20) / 100 if ctx.regime_params else 0.2
    expected_gain = regime_vol * PIPELINE_GATE["expected_gain_factor"]  # 0.8
    expected_loss = regime_vol * PIPELINE_GATE["expected_loss_factor"]  # 0.5 (ATR止损)
    
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
    V6 Phase 2: + 地缘风险一票否决 (severity=5 → blocked)
    """
    # V6: 地缘风险一票否决
    geo_severity = 0
    if ctx.regime_params:
        geo_severity = ctx.regime_params.get("geo_severity", 0)
    
    # 也从 modules_results 取（双保险）
    geo_module = ctx.modules_results.get("geopolitical", {})
    geo_severity = max(geo_severity, geo_module.get("max_severity", 0))
    
    if geo_severity >= 5:
        # severity=5: 极端地缘风险 → 一票否决（如全面战争、核危机）
        ctx.risk_alerts.append({
            "source": "geo_risk_firewall",
            "level": "danger",
            "msg": "🔴 地缘极端风险，所有操作建议已拦截",
        })
        print(f"[RISK] 地缘一票否决: severity={geo_severity}")
    elif geo_severity >= 4:
        # severity=4: 高风险 → 强制预警（已由 regime_engine 切到 cautious）
        ctx.risk_alerts.append({
            "source": "geo_risk_firewall",
            "level": "warning",
            "msg": "⚠️ 地缘高风险，已切换谨慎管线，建议减少操作",
        })

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

    # v9.5.44 修复：检查市场估值，如 >= 85% 则升级 risk_level
    # FIX 2026-08-09: 从服务器版本合并回本地（发现本地/服务器代码漂移时，
    # 服务器上这段逻辑正在生产环境生效，本地版本一直缺失）
    _check_valuation_risk(ctx)

    ctx.pipeline_steps.append("risk_firewall")
    return ctx


def _check_valuation_risk(ctx: DecisionContext) -> None:
    """
    检查市场估值风险
    如估值百分位 >= 85%，升级 risk_level 并添加 alert
    """
    # 从 ctx 中提取估值百分位
    val_pct = _extract_valuation_pct(ctx)

    if val_pct is None:
        return

    # 估值 >= 85% 属于高位
    if val_pct >= 85:
        # 升级 risk_level
        if ctx.risk_level == "normal":
            ctx.risk_level = "warning"
            ctx.risk_alerts.append({
                "source": "valuation_check",
                "level": "warning",
                "msg": f"市场估值偏高（分位{val_pct:.0f}%），建议谨慎",
            })
        elif ctx.risk_level == "warning" and not getattr(ctx, 'risk_override', False):
            # 已经是 warning，但添加估值 alert
            ctx.risk_alerts.append({
                "source": "valuation_check",
                "level": "warning",
                "msg": f"市场估值偏高（分位{val_pct:.0f}%），注意风险",
            })


def _extract_valuation_pct(ctx: DecisionContext) -> float:
    """从 ctx 中提取估值百分位"""
    # 尝试从 modules_results 中获取
    if hasattr(ctx, 'modules_results') and isinstance(ctx.modules_results, dict):
        # 检查 market_valuation 模块
        if 'market_valuation' in ctx.modules_results:
            val_data = ctx.modules_results['market_valuation']
            if isinstance(val_data, dict):
                return val_data.get('pct', None)

        # 检查 unified 数据
        if 'unified' in ctx.modules_results:
            unified = ctx.modules_results['unified']
            if isinstance(unified, dict):
                return unified.get('val_pct', unified.get('valuation_pct', None))

    # 尝试从 ctx 直接获取
    if hasattr(ctx, 'val_pct'):
        return ctx.val_pct
    if hasattr(ctx, 'valuation_pct'):
        return ctx.valuation_pct

    return None


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
    "default": {"name": "日常决策", "steps": 9, "llm_max": 3, "description": "门控60%直出+仲裁(股票/基金最多3次LLM)"},
    "fast": {"name": "紧急快速", "steps": 5, "llm_max": 0, "description": "零LLM，纯模块+风控"},
    "cautious": {"name": "谨慎深度", "steps": 10, "llm_max": 3, "description": "含持仓体检+空头反驳三步链，熊市专用"},
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
