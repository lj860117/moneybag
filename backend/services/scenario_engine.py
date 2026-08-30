"""
钱袋子 — V6 Phase 4: 情景分析引擎
"如果中东停火 A 股会怎样？" — 给定假设条件 → 收集当前市场数据 → R1 深度推演 → 输出影响评估

设计原则：
1. 4 个即用型预设情景（中东停火/油价120/美联储降息/芯片禁令升级）
2. 自定义文本情景（用户输入任意 what-if）
3. 推理引擎优先 R1（深度推理），降级 V3（轻量快速）
4. 消费所有已有模块数据（地缘/油价/北向/行业/研报）
5. 结果结构化：概率/市场影响/行业赢家输家/持仓建议/时间窗口
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "scenario_engine",
    "scope": "public",
    "input": ["scenario_type"],
    "output": "scenario_analysis",
    "cost": "llm_heavy",
    "tags": ["情景", "假设", "压力测试", "What-if"],
    "description": "情景分析引擎：给定假设条件→推演A股影响→给配置建议（需R1推理）",
    "layer": "analysis",
    "priority": 5,
}

import os
import time
import json
from datetime import datetime

from services.memory_cache import MemoryCache
from services.llm_gateway import MODEL_ROUTING

_scenario_cache = MemoryCache("scenario_engine", ttl=1800, max_entries=30)


# ============================================================
# 1. 预设情景模板
# ============================================================

PRESET_SCENARIOS = {
    "ceasefire": {
        "id": "ceasefire",
        "name": "中东停火",
        "description": "美以伊达成停火协议，霍尔木兹海峡恢复正常通航",
        "assumptions": [
            "中东主要冲突方达成停火协议",
            "霍尔木兹海峡通航恢复正常",
            "石油供应链恢复，运输风险下降",
        ],
        "affected_vars": {"oil_price": "-20~30%", "gold": "-3~5%", "risk_appetite": "上升"},
        "sector_impact_hint": {
            "能源": "bearish", "航空": "bullish", "旅游": "bullish",
            "消费": "bullish", "军工": "bearish", "黄金": "bearish",
        },
    },
    "oil_120": {
        "id": "oil_120",
        "name": "油价突破120美元",
        "description": "布伦特原油突破120美元/桶，持续1个月以上",
        "assumptions": [
            "OPEC+ 深度减产",
            "中东局势进一步恶化",
            "全球经济衰退预期升温",
        ],
        "affected_vars": {"oil_price": "+20%", "cpi": "+0.3~0.5%", "a_share": "-3~5%"},
        "sector_impact_hint": {
            "能源": "strong_bullish", "化工": "bearish", "航空": "strong_bearish",
            "运输": "bearish", "新能源": "bullish",
        },
    },
    "fed_cut": {
        "id": "fed_cut",
        "name": "美联储意外降息",
        "description": "美联储紧急降息50BP，全球流动性预期逆转",
        "assumptions": [
            "美国经济数据大幅走弱",
            "美联储紧急会议降息50BP",
            "全球央行跟进宽松预期",
        ],
        "affected_vars": {"usd": "-2%", "gold": "+3~5%", "northbound": "大幅流入", "a_share": "+3~5%"},
        "sector_impact_hint": {
            "科技": "bullish", "地产": "bullish", "银行": "neutral",
            "黄金": "bullish", "消费": "bullish",
        },
    },
    "chip_ban": {
        "id": "chip_ban",
        "name": "芯片禁令升级",
        "description": "美国扩大对华芯片出口限制范围，涵盖更多设备和技术",
        "assumptions": [
            "美国将更多中国企业列入实体清单",
            "芯片制造设备出口管制范围扩大",
            "EDA 软件许可收紧",
        ],
        "affected_vars": {"tech_sentiment": "短期大跌", "domestic_sub": "中期利好"},
        "sector_impact_hint": {
            "半导体(外资)": "strong_bearish", "国产替代": "bullish",
            "AI/算力": "short_bearish", "军工": "neutral",
        },
    },
}


# ============================================================
# 2. 收集当前市场上下文（给 R1 用）
# ============================================================

def _collect_market_snapshot() -> dict:
    """收集当前市场关键数据，作为情景分析的输入上下文"""
    snapshot = {}

    # 地缘
    try:
        from services.geopolitical import get_geopolitical_risk_score
        geo = get_geopolitical_risk_score()
        snapshot["geopolitical"] = {
            "score": geo.get("score", 0),
            "level": geo.get("level", "未知"),
            "events": geo.get("events", [])[:3],
        }
    except Exception:
        snapshot["geopolitical"] = {"score": 0, "level": "未知"}

    # 大宗商品（原油/黄金）
    try:
        from services.market_factors import get_crude_oil_price, get_commodity_prices
        oil = get_crude_oil_price()
        snapshot["oil"] = {
            "price": oil.get("sc_price", 0),
            "brent_est": oil.get("brent_est", 0),
            "alert_level": oil.get("alert_level", "normal"),
        }
        comm = get_commodity_prices()
        if comm.get("gold"):
            snapshot["gold"] = comm["gold"]
    except Exception:
        snapshot["oil"] = {"price": 0}

    # 北向资金
    # ⚠️ 净流入自 2024-08-19 起交易所改为按季度披露，日频不可得（口径说明见
    #    tushare_data.get_northbound_flow）。这里只取成交额活跃度维度。
    #    注意不能写 north.get("net_flow_5d", 0) —— 键存在且值为 None 时
    #    .get 返回 None 而不是默认值 0，下游格式化会抛 TypeError。
    try:
        from services.factor_data import get_northbound_flow
        north = get_northbound_flow()
        snapshot["northbound"] = {
            "net_flow_available": bool(north.get("net_flow_available", False)),
            "unavailable_reason": north.get("unavailable_reason", ""),
            "turnover_today": north.get("turnover_today"),
            "turnover_avg_5d": north.get("turnover_avg_5d"),
            "turnover_avg_20d": north.get("turnover_avg_20d"),
            "turnover_trend": north.get("turnover_trend", "数据不可得"),
            "available": bool(north.get("available", False)),
        }
    except Exception:
        snapshot["northbound"] = {
            "net_flow_available": False,
            "unavailable_reason": "北向数据获取失败",
            "turnover_today": None,
            "turnover_avg_5d": None,
            "turnover_avg_20d": None,
            "turnover_trend": "数据不可得",
            "available": False,
        }

    # 行业轮动
    try:
        from services.sector_rotation import get_sector_rotation
        sr = get_sector_rotation()
        if sr.get("available"):
            snapshot["sector_rotation"] = {
                "pattern": sr.get("rotation_signal", "均衡"),
                "top_sectors": [s.get("name", "") for s in sr.get("top_gainers", [])[:5]],
            }
    except Exception:
        pass

    # 研报共识
    try:
        from services.broker_research import get_broker_consensus
        br = get_broker_consensus()
        if br.get("available"):
            snapshot["broker_consensus"] = {
                "consensus": br.get("consensus"),
                "hot_sectors": [s["name"] for s in br.get("hot_sectors", [])[:3]],
            }
    except Exception:
        pass

    # 估值
    try:
        from services.market_data import get_valuation_percentile
        val = get_valuation_percentile()
        snapshot["valuation"] = {
            "percentile": val.get("percentile", 50),
            "level": val.get("level", "适中"),
            "pe": val.get("current_pe", 0),
        }
    except Exception:
        pass

    return snapshot


# ============================================================
# 3. R1 情景推理 Prompt 构建
# ============================================================

def _build_scenario_prompt(scenario: dict, market_snapshot: dict, user_portfolio: list = None) -> str:
    """构建情景分析的 R1 Prompt

    Prompt 设计原则（prompt-engineering-expert Skill）：
    1. 角色设定：A股宏观策略分析师
    2. 结构化输入：假设条件 + 当前市场数据 + 用户持仓
    3. 结构化输出：JSON schema 强约束
    4. 链式推理：假设→传导机制→行业影响→持仓建议
    """

    # 格式化市场快照
    market_lines = []
    geo = market_snapshot.get("geopolitical", {})
    market_lines.append(f"- 地缘风险：{geo.get('level', '未知')}（评分 {geo.get('score', 0)}/100）")

    oil = market_snapshot.get("oil", {})
    if oil.get("brent_est"):
        market_lines.append(f"- 布伦特原油估价：~{oil['brent_est']}美元/桶（预警={oil.get('alert_level', 'normal')}）")

    # 北向资金：净流入维度不可得时，只喂成交额活跃度，并显式给 LLM 划边界，
    # 防止模型把成交额（恒正的换手规模）误读成资金流入方向。
    north = market_snapshot.get("northbound", {})
    if north.get("net_flow_available"):
        nf5 = north.get("net_flow_5d")
        if isinstance(nf5, (int, float)):
            market_lines.append(f"- 北向资金：近5日净流入{nf5:+.1f}亿，趋势={north.get('trend', '')}")
    elif north.get("available") and isinstance(north.get("turnover_today"), (int, float)):
        t_avg5 = north.get("turnover_avg_5d")
        t_avg20 = north.get("turnover_avg_20d")
        line = (f"- 北向资金成交额：今日{north['turnover_today']:.0f}亿"
                f"，活跃度={north.get('turnover_trend', '平稳')}")
        if isinstance(t_avg5, (int, float)) and isinstance(t_avg20, (int, float)):
            line += f"（5日均{t_avg5:.0f}亿 vs 20日均{t_avg20:.0f}亿）"
        market_lines.append(line)
        market_lines.append(
            "  ⚠️ 北向【净买入/净流出】数据不可得：自2024-08-19起沪深交易所停止披露日频北向净买入，"
            "改为按季度公布。上面的成交额是买入额+卖出额的总规模（恒为正），"
            "**不代表资金流入或流出方向**。请勿据此推断外资在买入还是卖出，"
            "也不要在结论中出现「北向流入/流出」「外资加仓/减仓」等方向性表述。"
        )
    else:
        market_lines.append(
            "- 北向资金：数据不可得（净买入自2024-08-19起改为季度披露，且本次未取到成交额）。"
            "请勿对北向资金方向做任何推断。"
        )

    val = market_snapshot.get("valuation", {})
    if val.get("percentile"):
        market_lines.append(f"- 沪深300估值百分位：{val['percentile']}%（{val.get('level', '')}），PE={val.get('pe', '')}")

    sr = market_snapshot.get("sector_rotation", {})
    if sr.get("top_sectors"):
        market_lines.append(f"- 行业热点：{','.join(sr['top_sectors'][:5])}")

    br = market_snapshot.get("broker_consensus", {})
    if br.get("consensus"):
        market_lines.append(f"- 机构共识：{br['consensus']}，关注行业={','.join(br.get('hot_sectors', []))}")

    market_text = "\n".join(market_lines)

    # 格式化假设条件
    assumptions_text = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(scenario.get("assumptions", [])))

    # 格式化持仓（如有）
    portfolio_text = ""
    if user_portfolio:
        portfolio_text = "\n\n## 用户当前持仓\n"
        for p in user_portfolio[:10]:
            portfolio_text += f"- {p.get('name', p.get('code', ''))}: {p.get('pct', '')}%\n"

    prompt = f"""你是一位资深A股宏观策略分析师，擅长情景分析和压力测试。

## 情景假设：{scenario.get('name', '自定义情景')}

{scenario.get('description', '')}

### 具体假设条件：
{assumptions_text}

## 当前市场数据快照

{market_text}{portfolio_text}

## 你的任务

基于以上假设条件和当前市场数据，进行完整的情景推演。请严格按以下 JSON 格式输出，不要有任何其他内容：

```json
{{
  "probability": "该情景发生的概率评估（如 30%/中等/较低）",
  "timeframe": "影响时间窗口（如 1-3个月/3-6个月）",
  "transmission_chain": "传导机制：用2-3句话描述从假设到A股影响的因果链",
  "market_impact": {{
    "a_share": "对A股整体的影响（如 +3~5% / -2~3%）",
    "oil": "对油价的影响（如不涉及写 N/A）",
    "gold": "对黄金的影响",
    "bond": "对债券/利率的影响",
    "rmb": "对人民币汇率的影响"
  }},
  "sector_winners": ["受益行业1", "受益行业2", "受益行业3"],
  "sector_losers": ["受损行业1", "受损行业2"],
  "portfolio_advice": "对投资者的具体配置建议（1-2句话）",
  "key_risk": "该情景下最大的不确定性/风险点",
  "confidence": "你对这个分析的置信度（0-100）"
}}
```

注意：
1. 概率评估要基于当前市场数据，不能凭空想象
2. 行业影响要具体到 A 股板块名称
3. 配置建议要可操作（具体到 ETF 类型或板块方向）"""

    return prompt


# ============================================================
# 4. 调用 LLM 推理
# ============================================================

def _call_llm_for_scenario(prompt: str, use_r1: bool = True) -> dict:
    """调用 DeepSeek R1 或 V3 进行情景推理 — 走 LLMGateway 统一入口

    R1 用于深度推理（主），V3 用于快速降级。
    """
    from services.llm_gateway import llm_call

    model_tier = "llm_heavy" if use_r1 else "llm_light"
    result = llm_call(
        prompt,
        model_tier=model_tier,
        module="scenario_engine",
        max_tokens=1500,
    )

    content = result.get("content", "")
    if not content:
        return {"error": result.get("source", "unknown")}

    # 提取 JSON
    import re
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            parsed["_model"] = MODEL_ROUTING.get(model_tier, "deepseek-v4-pro" if use_r1 else "deepseek-v4-flash")
            parsed["_raw_length"] = len(content)
            return parsed
        except json.JSONDecodeError:
            return {"error": "LLM 返回格式解析失败", "_raw": content[:500]}
    return {"error": "LLM 返回中无 JSON", "_raw": content[:500]}


# ============================================================
# 5. 情景分析主函数
# ============================================================

def analyze_scenario(scenario_id: str = "", custom_text: str = "", user_id: str = "") -> dict:
    """情景分析主入口

    Args:
        scenario_id: 预设情景 ID（ceasefire/oil_120/fed_cut/chip_ban）
        custom_text: 自定义情景描述（与 scenario_id 二选一）
        user_id: 用户 ID（用于获取持仓）

    Returns:
        完整的情景分析结果
    """
    # 确定情景
    if scenario_id and scenario_id in PRESET_SCENARIOS:
        scenario = PRESET_SCENARIOS[scenario_id]
    elif custom_text:
        scenario = {
            "id": "custom",
            "name": "自定义情景",
            "description": custom_text,
            "assumptions": [custom_text],
            "affected_vars": {},
            "sector_impact_hint": {},
        }
    else:
        return {"error": "需要 scenario_id 或 custom_text", "available": False}

    # 缓存检查
    cache_key = f"scenario_{scenario.get('id', 'custom')}_{user_id}"
    cached = _scenario_cache.get(cache_key)
    if cached is not None:
        return cached

    print(f"[SCENARIO] 开始分析: {scenario['name']}")

    # 1. 收集市场快照
    market_snapshot = _collect_market_snapshot()

    # 2. 获取用户持仓（如有）
    user_portfolio = []
    if user_id:
        try:
            from services.user_service import get_user_holdings
            holdings = get_user_holdings(user_id)
            if holdings:
                user_portfolio = holdings[:10]
        except Exception:
            pass

    # 3. 构建 Prompt
    prompt = _build_scenario_prompt(scenario, market_snapshot, user_portfolio)

    # 4. 调用 R1 推理（失败降级 V3）
    llm_result = _call_llm_for_scenario(prompt, use_r1=True)
    if llm_result.get("error"):
        print(f"[SCENARIO] R1 failed: {llm_result['error']}，降级 V3")
        llm_result = _call_llm_for_scenario(prompt, use_r1=False)

    # 5. 组装结果
    result = {
        "available": not bool(llm_result.get("error")),
        "scenario": {
            "id": scenario.get("id", "custom"),
            "name": scenario["name"],
            "description": scenario["description"],
            "assumptions": scenario.get("assumptions", []),
        },
        "analysis": {
            "probability": llm_result.get("probability", "未知"),
            "timeframe": llm_result.get("timeframe", "未知"),
            "transmission_chain": llm_result.get("transmission_chain", ""),
            "market_impact": llm_result.get("market_impact", {}),
            "sector_winners": llm_result.get("sector_winners", []),
            "sector_losers": llm_result.get("sector_losers", []),
            "portfolio_advice": llm_result.get("portfolio_advice", ""),
            "key_risk": llm_result.get("key_risk", ""),
            "confidence": llm_result.get("confidence", 0),
        },
        "market_snapshot": market_snapshot,
        "model": llm_result.get("_model", "unknown"),
        "analyzed_at": datetime.now().isoformat(),
        "error": llm_result.get("error"),
    }

    print(f"[SCENARIO] 分析完成: {scenario['name']}, "
          f"概率={result['analysis']['probability']}, "
          f"模型={result['model']}")

    _scenario_cache.set(cache_key, result)
    return result


def list_scenarios() -> list:
    """列出所有预设情景"""
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],
        }
        for s in PRESET_SCENARIOS.values()
    ]


# ============================================================
# 6. 规则引擎降级（LLM 不可用时的纯规则分析）
# ============================================================

def _rule_based_analysis(scenario: dict, market_snapshot: dict) -> dict:
    """纯规则情景分析（0 token 成本降级方案）"""
    hints = scenario.get("sector_impact_hint", {})
    affected = scenario.get("affected_vars", {})

    winners = [k for k, v in hints.items() if "bullish" in str(v)]
    losers = [k for k, v in hints.items() if "bearish" in str(v)]

    return {
        "probability": "需要 AI 评估",
        "timeframe": "1-3个月",
        "transmission_chain": f"基于 {scenario['name']} 假设的规则推演（未使用 AI）",
        "market_impact": affected,
        "sector_winners": winners,
        "sector_losers": losers,
        "portfolio_advice": f"关注 {','.join(winners[:3])} 机会，规避 {','.join(losers[:2])} 风险",
        "key_risk": "此为规则引擎推演，未考虑复杂传导和交叉影响",
        "confidence": 30,
        "_model": "rule_engine",
    }
