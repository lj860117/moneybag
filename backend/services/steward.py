"""
钱袋子 — AI 投资管家 (Steward)
职责：接收用户问题 → 创建 DecisionContext → 调 PipelineRunner → 返回结果
设计哲学：steward 只做"创建+调度+返回"，不做业务逻辑

3 个入口：
  ask(user_id, question)   — 完整决策（Pipeline全流程）
  briefing(user_id)        — 每日简报（精简版）
  review(user_id)          — 收盘复盘（完整+判断记录）
"""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

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

# 晨报缓存目录
_BRIEF_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data")) / "briefings"


def _check_date_consistency() -> bool:
    """验证系统日期是否在合理范围内"""
    from datetime import datetime
    today = datetime.now().date()
    # 允许范围：2020-2050
    if today.year < 2020 or today.year > 2050:
        print(f"[STEWARD] ⚠️  系统日期异常: {today}")
        return False
    return True


def _extract_cache_date(filename: str) -> str:
    """
    从缓存文件名中提取日期
    格式: {user_id}_{YYYYMMDD}
    返回: YYYYMMDD 或空字符串
    """
    parts = filename.replace('.json', '').split('_')
    if len(parts) >= 2:
        return parts[-1]
    return ""


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
        ctx.regime_confidence = regime_result["confidence"] / 100  # classify 返回 0-100，ctx 期望 0-1
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
                "regime_description": _sanitize_regime_description(ctx),
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
        优先读取当日缓存（night_worker 07:30 预生成），避免重复计算
        
        【FIX #3】缓存策略：4小时TTL而非24小时
        - 07:30 night_worker 预生成缓存
        - 07:30-11:30: 使用缓存（数据相对稳定）
        - 11:30+ 重新计算（北向资金/融资可能发生变化）
        """
        # ---- 每日文件缓存（4小时 TTL）----
        today = datetime.now().strftime("%Y%m%d")
        cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
        CACHE_TTL_HOURS = 4  # 【FIX #3】从24h改为4h
        
        if cache_fp.exists():
            try:
                cached = json.loads(cache_fp.read_text(encoding="utf-8"))
                cache_date = _extract_cache_date(cache_fp.stem)
                
                # 两个条件都需满足：日期匹配 AND 缓存未超过4小时
                if cache_date == today:
                    try:
                        file_mtime = cache_fp.stat().st_mtime
                        cache_age_seconds = time.time() - file_mtime
                        cache_age_hours = cache_age_seconds / 3600
                        
                        if cache_age_hours < CACHE_TTL_HOURS:
                            cached["from_cache"] = True
                            cached["cache_age_minutes"] = round(cache_age_seconds / 60)
                            print(f"[STEWARD] ✅ 使用缓存 (生成于 {cache_age_hours:.1f}h前)")
                            return cached
                        else:
                            # 缓存超过4小时，删除重新计算
                            try:
                                cache_fp.unlink()
                                print(f"[STEWARD] 删除过期缓存 (已{cache_age_hours:.1f}h): {cache_fp.name}")
                            except Exception as e:
                                print(f"[STEWARD] 删除缓存失败: {e}")
                    except Exception as e:
                        print(f"[STEWARD] 检查缓存年龄失败: {e}")
                else:
                    # 日期不匹配，删除过期缓存
                    try:
                        cache_fp.unlink()
                        print(f"[STEWARD] 删除过期缓存（日期不符）: {cache_fp.name}")
                    except Exception as e:
                        print(f"[STEWARD] 删除缓存失败: {e}")
            except Exception as e:
                print(f"[STEWARD] 读晨报缓存失败: {e}")

        start = time.time()

        ctx = DecisionContext(user_id=user_id, question="每日简报")

        # Regime（轻量级，有缓存通常 <1s）
        regime_result = classify_regime()
        ctx.regime = regime_result["regime"]
        ctx.regime_confidence = regime_result["confidence"] / 100  # classify 返回 0-100，ctx 期望 0-1
        ctx.regime_description = regime_result.get("description", "")

        # 用 fast 管线（不调 LLM）
        ctx = self.runner.run("fast", ctx)
        ctx.elapsed_seconds = round(time.time() - start, 1)
        
        # 组装简报
        briefing = {
            "regime": ctx.regime,
            "regime_description": _sanitize_regime_description(ctx),
            "risk_level": ctx.risk_level or "normal",
            "risk_actions": ctx.risk_actions[:3] if ctx.risk_actions else [],
            "signals_count": len(ctx.modules_results.get("signal_scout", {}).get("signals", [])) if isinstance(ctx.modules_results.get("signal_scout"), dict) else 0,
            "top_signal": _get_top_signal(ctx),
            "one_line": _generate_one_line(ctx),
            "elapsed": ctx.elapsed_seconds,
            "timestamp": datetime.now().isoformat(),
        }

        # 写入当日缓存（供同日后续调用直接命中）
        try:
            _BRIEF_DIR.mkdir(parents=True, exist_ok=True)
            cache_fp.write_text(json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[STEWARD] 写晨报缓存失败: {e}")

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
        ctx.regime_confidence = regime_result["confidence"] / 100  # classify 返回 0-100，ctx 期望 0-1
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

    def briefing_history(self, user_id: str, days: int = 7) -> list:
        """
        返回最近 N 天的晨报缓存列表（MB-005 往期晨报）
        关键修复：过滤掉日期在未来或超过 N 天的缓存
        """
        if not _BRIEF_DIR.exists():
            return []
        files = sorted(_BRIEF_DIR.glob(f"{user_id}_*.json"), reverse=True)
        result = []
        
        today_dt = datetime.now().date()
        today_str = today_dt.strftime("%Y%m%d")
        cutoff_date = (today_dt - timedelta(days=days)).strftime("%Y%m%d")
        
        for fp in files[:days * 2]:  # 扫描范围稍大一些，防止文件缺失
            try:
                # 关键修复：提取并验证日期
                data = json.loads(fp.read_text(encoding="utf-8"))
                # fp.stem 格式：{user_id}_{YYYYMMDD}
                date_str = fp.stem.replace(f"{user_id}_", "")
                
                # 跳过格式不符的文件
                if len(date_str) != 8 or not date_str.isdigit():
                    continue
                
                # 跳过未来的日期
                if date_str > today_str:
                    print(f"[STEWARD] 跳过未来的缓存: {fp.name}")
                    continue
                
                # 跳过太旧的日期（超过 N 天）
                if date_str < cutoff_date:
                    break
                
                data["date"] = date_str
                result.append(data)
                
                if len(result) >= days:
                    break
                    
            except Exception as e:
                print(f"[STEWARD] 读往期晨报失败 {fp}: {e}")
        
        return result


# ---- 辅助函数 ----


def _sanitize_regime_description(ctx: DecisionContext) -> str:
    """清理 regime_description 中的技术细节"""
    desc = ctx.regime_description or ""
    import re
    desc = re.sub(r'severity=\d+', '', desc)
    desc = re.sub(r'→\s*强制\s*\w+', '', desc)
    desc = re.sub(r'原判定:\s*\w+', '', desc)
    desc = ' '.join(desc.split()).strip()
    return desc or "📊 市场状态监测中"

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
    
    parts = [regime_text]
    if risk_text:
        parts.append(risk_text)
    return "，".join(parts)


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
