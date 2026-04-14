"""
钱袋子 — AI 投资管家 (Steward)
职责：接收用户问题 → 创建 DecisionContext → 调 PipelineRunner → 返回结果
设计哲学：steward 只做"创建+调度+返回"，不做业务逻辑

3 个入口：
  ask(user_id, question)   — 完整决策（Pipeline全流程）
  briefing(user_id)        — 每日简报（精简版）
  review(user_id)          — 收盘复盘（完整+判断记录）
"""
import time
from datetime import datetime

from services.decision_context import DecisionContext
from services.pipeline_runner import PipelineRunner, PIPELINES
from services.module_registry import ModuleRegistry
from services.regime_engine import classify as classify_regime, get_pipeline_for_regime

MODULE_META = {
    "name": "steward",
    "scope": "private",
    "input": ["user_id", "question"],
    "output": "decision",
    "cost": "cpu",  # steward 本身不调 LLM，LLM 在 Pipeline step 里
    "tags": ["steward", "orchestrator"],
    "description": "AI投资管家入口，创建Context→选Pipeline→执行→返回结果",
    "layer": "output",
    "priority": 0,
}


class Steward:
    """AI 投资管家"""
    
    def __init__(self):
        self.runner = PipelineRunner()
        self.registry = ModuleRegistry.instance()
    
    def ask(self, user_id: str, question: str, 
            pipeline_override: str = None) -> dict:
        """
        完整决策流程
        
        Args:
            user_id: 用户ID
            question: 用户问题
            pipeline_override: 强制指定管线（调试用），不传则自动选
            
        Returns:
            ctx.to_user_response() — 用户可读的完整结果
        """
        start = time.time()
        
        # 1. 创建 DecisionContext
        ctx = DecisionContext(user_id=user_id, question=question)
        
        # 1.5 从问题中提取股票/基金名→代码（供各模块分析）
        extracted_code = _extract_stock_code(question)
        extracted_name, name_to_code = _extract_stock_name(question)
        fund_name, fund_code = _extract_fund_name(question)
        ctx.question_stock_code = extracted_code or name_to_code or fund_code
        ctx.question_stock_name = extracted_name or fund_name
        ctx.question_is_fund = bool(fund_code and not name_to_code)
        
        # 2. Regime 分类
        regime_result = classify_regime()
        ctx.regime = regime_result["regime"]
        ctx.regime_confidence = regime_result["confidence"]
        ctx.regime_params = regime_result.get("params", {})
        ctx.regime_description = regime_result.get("description", "")
        
        # 3. 选择管线
        if pipeline_override and pipeline_override in PIPELINES:
            pipeline_name = pipeline_override
        else:
            pipeline_name = get_pipeline_for_regime(ctx.regime)
        
        # 4. 执行 Pipeline
        ctx = self.runner.run(pipeline_name, ctx)
        
        # 5. 记录执行时间
        ctx.elapsed_seconds = round(time.time() - start, 1)
        
        # 6. 保存上下文接力（修复 B2）
        try:
            from services.agent_memory import save_context
            save_context(user_id, {
                "last_analysis": ctx.conclusion[:200] if ctx.conclusion else "",
                "market_phase": ctx.regime,
                "regime_description": ctx.regime_description,
                "direction": ctx.direction,
                "confidence": ctx.final_confidence,
            })
        except Exception as e:
            print(f"[STEWARD] save_context failed: {e}")
        
        # 7. 记录判断（供 judgment_tracker 后续验证）
        try:
            from services.judgment_tracker import record as jt_record
            jt_record(user_id, {
                "direction": ctx.direction,
                "confidence": ctx.final_confidence,
                "regime": ctx.regime,
                "weighted_score": ctx.confidence_score,
                "divergence": ctx.divergence,
                "gate_decision": ctx.gate_decision,
                "modules_results": {
                    name: {
                        "available": r.get("available", False),
                        "direction": r.get("direction", "neutral"),
                        "confidence": r.get("confidence", 0),
                    }
                    for name, r in ctx.modules_results.items()
                    if isinstance(r, dict)
                },
                "question": question[:100],
                "pipeline": pipeline_name,
            })
        except Exception as e:
            print(f"[STEWARD] judgment record failed: {e}")
        
        return ctx.to_user_response()
    
    def briefing(self, user_id: str) -> dict:
        """
        每日简报（精简版）
        不问具体问题，只看大盘状态+持仓风险+信号
        """
        start = time.time()
        
        ctx = DecisionContext(user_id=user_id, question="每日简报")
        
        # Regime
        regime_result = classify_regime()
        ctx.regime = regime_result["regime"]
        ctx.regime_confidence = regime_result["confidence"]
        ctx.regime_description = regime_result.get("description", "")
        
        # 用 fast 管线（不调 LLM）
        ctx = self.runner.run("fast", ctx)
        ctx.elapsed_seconds = round(time.time() - start, 1)
        
        # 组装简报
        briefing = {
            "regime": ctx.regime,
            "regime_description": ctx.regime_description,
            "risk_level": ctx.risk_level or "normal",
            "risk_actions": ctx.risk_actions[:3] if ctx.risk_actions else [],
            "signals_count": len(ctx.modules_results.get("signal_scout", {}).get("signals", [])) if isinstance(ctx.modules_results.get("signal_scout"), dict) else 0,
            "top_signal": _get_top_signal(ctx),
            "one_line": _generate_one_line(ctx),
            "elapsed": ctx.elapsed_seconds,
            "timestamp": datetime.now().isoformat(),
        }
        
        return briefing
    
    def review(self, user_id: str, force_llm: bool = True) -> dict:
        """
        收盘复盘（完整版，强制调 LLM）
        """
        start = time.time()
        
        ctx = DecisionContext(user_id=user_id, question="收盘复盘")
        
        # Regime
        regime_result = classify_regime()
        ctx.regime = regime_result["regime"]
        ctx.regime_confidence = regime_result["confidence"]
        ctx.regime_description = regime_result.get("description", "")
        
        # 用 cautious 管线（最完整，含体检）
        ctx = self.runner.run("cautious", ctx)
        ctx.elapsed_seconds = round(time.time() - start, 1)
        
        # 保存信号文件（供 Claude 查看）
        try:
            from services.agent_engine import save_signal_file
            save_signal_file(user_id, {
                "analysis": ctx.conclusion or "复盘完成",
                "source": "steward_review",
                "direction": ctx.direction,
                "confidence": ctx.final_confidence,
                "regime": ctx.regime,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as e:
            print(f"[STEWARD] save_signal_file failed: {e}")
        
        # 保存上下文接力
        try:
            from services.agent_memory import save_context
            save_context(user_id, {
                "last_analysis": ctx.conclusion[:200] if ctx.conclusion else "收盘复盘",
                "market_phase": ctx.regime,
                "direction": ctx.direction,
                "confidence": ctx.final_confidence,
            })
        except Exception as e:
            print(f"[STEWARD] save_context failed: {e}")
        
        # 记录判断
        try:
            from services.judgment_tracker import record as jt_record
            jt_record(user_id, {
                "direction": ctx.direction,
                "confidence": ctx.final_confidence,
                "regime": ctx.regime,
                "weighted_score": ctx.confidence_score,
                "divergence": ctx.divergence,
                "gate_decision": ctx.gate_decision,
                "modules_results": {
                    name: {
                        "available": r.get("available", False),
                        "direction": r.get("direction", "neutral"),
                        "confidence": r.get("confidence", 0),
                    }
                    for name, r in ctx.modules_results.items()
                    if isinstance(r, dict)
                },
                "question": "收盘复盘",
                "pipeline": "cautious",
            })
        except Exception as e:
            print(f"[STEWARD] judgment record failed: {e}")
        
        return ctx.to_user_response()


# ---- 辅助函数 ----

def _get_top_signal(ctx: DecisionContext) -> str:
    """从 ctx 中提取最重要的一条信号"""
    scout = ctx.modules_results.get("signal_scout", {})
    if isinstance(scout, dict):
        signals = scout.get("signals", [])
        if signals and isinstance(signals, list):
            # 优先 danger > warning > info
            for level in ["danger", "warning", "info"]:
                for s in signals:
                    if isinstance(s, dict) and s.get("level") == level:
                        return s.get("msg", "")[:80]
            if isinstance(signals[0], dict):
                return signals[0].get("msg", "")[:80]
    return ""


def _generate_one_line(ctx: DecisionContext) -> str:
    """生成管家一句话总结"""
    regime_map = {
        "trending_bull": "📈 市场趋势向上",
        "oscillating": "📊 市场震荡整理",
        "high_vol_bear": "📉 市场波动加大",
        "rotation": "🔄 板块轮动中",
    }
    
    regime_text = regime_map.get(ctx.regime, "📊 市场状态未知")
    
    risk = ctx.risk_level or "normal"
    risk_map = {
        "normal": "风控正常",
        "warning": "⚠️ 有风险提示",
        "danger": "🔴 风控红灯",
        "blocked": "🚫 操作被拦截",
    }
    risk_text = risk_map.get(risk, "")
    
    return f"{regime_text}，{risk_text}"


def _extract_stock_code(question: str) -> str:
    """从问题中提取股票代码（6位数字）"""
    import re
    m = re.search(r'\b(\d{6})\b', question)
    return m.group(1) if m else ""


def _extract_stock_name(question: str) -> tuple:
    """从问题中提取股票名 → 返回 (name, code)"""
    STOCK_NAME_MAP = {
        "茅台": "600519", "贵州茅台": "600519",
        "五粮液": "000858", "比亚迪": "002594",
        "宁德时代": "300750", "中国平安": "601318",
        "招商银行": "600036", "腾讯": "00700",
        "阿里": "BABA", "阿里巴巴": "BABA",
        "万科": "000002", "万科A": "000002",
        "美的": "000333", "美的集团": "000333",
        "格力": "000651", "格力电器": "000651",
        "完美世界": "002624", "三七互娱": "002555",
        "网易": "NTES", "米哈游": "",
        "中芯国际": "688981", "华为": "",
        "小米": "01810", "京东": "JD",
        "工商银行": "601398", "建设银行": "601939",
        "中国银行": "601988", "农业银行": "601288",
        "中信证券": "600030", "海天味业": "603288",
        "恒瑞医药": "600276", "药明康德": "603259",
        "隆基绿能": "601012", "宁德": "300750",
        "紫金矿业": "601899", "中远海控": "601919",
    }
    for name, code in STOCK_NAME_MAP.items():
        if name in question:
            return (name, code)
    return ("", "")


def _extract_fund_name(question: str) -> tuple:
    """从问题中提取基金名 → 返回 (name, code)"""
    FUND_NAME_MAP = {
        "沪深300": "110020", "沪深三百": "110020",
        "标普500": "050025", "标普": "050025",
        "招商产业债": "217022", "产业债": "217022",
        "黄金ETF": "000216", "黄金基金": "000216", "华安黄金": "000216",
        "红利低波": "008114", "红利": "008114",
        "余额宝": "余额宝",
        "科创50": "588000", "科创板": "588000",
        "创业板": "159915", "创业板ETF": "159915",
        "中证500": "510500", "中证500ETF": "510500",
        "纳斯达克": "513100", "纳指": "513100",
        "恒生科技": "513130", "恒生": "513130",
    }
    for name, code in FUND_NAME_MAP.items():
        if name in question:
            return (name, code)
    return ("", "")


# ---- 全局单例 ----
_steward_instance = None

def get_steward() -> Steward:
    """获取全局 Steward 单例"""
    global _steward_instance
    if _steward_instance is None:
        _steward_instance = Steward()
    return _steward_instance
