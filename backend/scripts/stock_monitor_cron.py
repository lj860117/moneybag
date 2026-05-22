#!/usr/bin/env python3
"""
持仓盯盘 cron 脚本（股票 + 基金统一，多用户按人推送）
用法：crontab 每 10 分钟执行一次（交易时段）
  */10 9-11,13-14 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py
  30 15 * * 1-5 cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py --close
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载 .env（cron 环境没有 systemd 的 EnvironmentFile）
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from services.stock_monitor import scan_all_holdings, load_stock_holdings
from services.fund_monitor import scan_all_fund_holdings, load_fund_holdings

MONITOR_DIR = Path(os.environ.get("MONITOR_DIR",
    Path(__file__).parent.parent.parent / "data" / "monitor"))
MONITOR_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = Path(os.environ.get("DATA_DIR",
    Path(__file__).parent.parent.parent / "data"))

PROFILES_FILE = DATA_DIR / "profiles.json"

# MB-002: 集中度预警冷却（24小时/天），文件缓存跨 cron 进程持久化
_COOLDOWN_FILE = DATA_DIR / "_cache" / "alert_cooldown.json"


def _load_cooldown() -> dict:
    """读取预警冷却状态（跨进程持久化）"""
    try:
        if _COOLDOWN_FILE.exists():
            return json.loads(_COOLDOWN_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cooldown(cooldown: dict):
    """保存预警冷却状态"""
    try:
        _COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COOLDOWN_FILE.write_text(json.dumps(cooldown, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _filter_alerts_with_cooldown(user_id: str, alerts: list) -> list:
    """对纪律类告警（集中度/行业集中度）施加 24 小时冷却，其余保持 30 分钟冷却"""
    import time
    now = time.time()
    cooldown = _load_cooldown()
    filtered = []
    for a in alerts:
        alert_type = a.get("type", "unknown")
        # 集中度类型 → 24 小时冷却
        if alert_type in ("concentration", "industry_concentration"):
            cd_sec = 86400
        else:
            cd_sec = 1800  # 其他预警 30 分钟
        key = f"{user_id}_{alert_type}_{a.get('code', alert_type)}"
        last_sent = cooldown.get(key, 0)
        if now - last_sent > cd_sec:
            filtered.append(a)
            cooldown[key] = now
    _save_cooldown(cooldown)
    return filtered


def _load_profiles() -> list:
    if PROFILES_FILE.exists():
        try:
            return json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def scan_user(user_id: str) -> dict:
    """扫描单个用户的全持仓（股票+基金）

    V7.7 (2026-04-19): 空仓时直接跳过，返回 empty 标记，不调 API 不写盘
    """
    all_alerts = []

    # V7.7 空仓预检：股票和基金都空就直接返回
    stock_holdings = load_stock_holdings(user_id)
    fund_holdings = load_fund_holdings(user_id)
    if not stock_holdings and not fund_holdings:
        print(f"  [空仓] {user_id} 无任何持仓，跳过扫描")
        return {"combined": None, "alerts": [], "empty": True}

    # 股票扫描
    stock_result = None
    if stock_holdings:
        print(f"  [股票] {len(stock_holdings)} 只...")
        stock_result = scan_all_holdings(user_id)
        stock_signals = stock_result.get("signals", [])
        all_alerts.extend([s for s in stock_signals if s["level"] in ("danger", "warning")])
        # 纪律类告警也推送（止损/止盈/集中度）
        discipline = stock_result.get("discipline", [])
        all_alerts.extend([d for d in discipline if d["level"] in ("danger", "warning")])

    # 基金扫描
    fund_result = None
    if fund_holdings:
        print(f"  [基金] {len(fund_holdings)} 只...")
        fund_result = scan_all_fund_holdings(user_id)
        fund_alerts = fund_result.get("alerts", [])
        all_alerts.extend([a for a in fund_alerts if a["level"] == "warning"])

    combined = {
        "stock": stock_result,
        "fund": fund_result,
        "totalAlerts": len(all_alerts),
        "scannedAt": datetime.now().isoformat(),
    }

    # 保存用户专属结果
    user_dir = MONITOR_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "latest.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"combined": combined, "alerts": all_alerts, "empty": False}


def push_user_alerts(user_id: str, wxwork_uid: str, alerts: list):
    """按用户推送异动到企微"""
    if not alerts:
        return
    
    print(f"  [推送] {len(alerts)} 条 → {wxwork_uid}")
    try:
        from services.wxwork_push import is_configured, send_stock_alert_to
        if is_configured():
            result = send_stock_alert_to(wxwork_uid, alerts)
            if result.get("ok"):
                print(f"  [推送] 成功")
            else:
                print(f"  [推送] 失败: {result.get('error', '')}")
        else:
            print(f"  [推送] 企微未配置")
    except Exception as e:
        print(f"  [推送] 异常: {e}")


def run_scan():
    """遍历所有用户，分别扫描分别推送"""
    # 休市日静默（v3.0 新增）
    try:
        from services.signal_scout import is_trading_day
        if not is_trading_day():
            print("[CRON] 非交易日，跳过扫描")
            return
    except Exception:
        pass

    profiles = _load_profiles()
    if not profiles:
        print("[CRON] 无用户 Profile，跳过")
        return

    # v3.0: 先跑一次公共信号收集（全市场共享，不按用户重复跑）
    try:
        from services.signal_scout import collect as scout_collect
        all_signals = scout_collect()
        print(f"[CRON] 信号侦察: {len(all_signals)} 条公共信号")
    except Exception as e:
        print(f"[CRON] 信号侦察失败: {e}")

    all_results = {}
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        wxwork_uid = p.get("wxworkUserId", "")
        
        print(f"[CRON] 扫描用户: {name} ({uid})")
        result = scan_user(uid)
        all_results[uid] = result["combined"]

        # V7.7: 空仓用户跳过后续信号推送和 alert 推送
        if result.get("empty"):
            continue

        # v3.0: 信号匹配+推送（取代旧的单纯 alert 推送）
        try:
            from services.signal_scout import deliver as scout_deliver
            push_result = scout_deliver(uid)
            if push_result.get("pushed", 0) > 0:
                print(f"  [信号] 推送 {push_result['pushed']} 条信号 → {wxwork_uid or uid}")
        except Exception as e:
            print(f"  [信号] 推送失败: {e}")

        # 原有 alert 推送（保留，信号和 alert 双通道）
        # MB-002: 施加集中度 24 小时冷却，避免每 10 分钟重复推送
        filtered_alerts = _filter_alerts_with_cooldown(uid, result["alerts"])
        if wxwork_uid and filtered_alerts:
            push_user_alerts(uid, wxwork_uid, filtered_alerts)
        elif filtered_alerts and not wxwork_uid:
            print(f"  [推送] 用户 {name} 未绑定企微，跳过推送")

    # 保存全局最新结果（兼容旧格式）
    latest_file = MONITOR_DIR / "latest.json"
    latest_file.write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 保存历史快照
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    (MONITOR_DIR / f"scan_{ts}.json").write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    cleanup_old_snapshots()


def _sanitize_reasoning_for_extraction(reasoning: str) -> str:
    """清理 reasoning 中的调试信息，防止"我们被问到"等debug文本泄露

    【FIX #2】移除调试信息避免用户看到系统细节
    """
    import re

    if not reasoning:
        return ""

    # 1. 去掉问题上下文（"我们被问到...","用户提问","Pipeline..."）
    reasoning = re.sub(r'我们被问到[^。]*?。', '', reasoning)
    reasoning = re.sub(r'用户提问[^。]*?。', '', reasoning)
    reasoning = re.sub(r'Pipeline[^。]*?。', '', reasoning)

    # 2. 先清括号内的技术指标（整体移除，避免残留 =value）
    reasoning = re.sub(r'[（(]Layer\d+[^）)]*?[）)]', '', reasoning)
    reasoning = re.sub(r'[（(]Step\d+[^）)]*?[）)]', '', reasoning)
    reasoning = re.sub(r'[（(][^）)]*?(?:score|分数|权重|门控|直出)[^）)]*?[）)]', '', reasoning)

    # 3. 去掉关键词到句末（贪婪匹配）
    reasoning = re.sub(r'(?:gate_decision|gate_reason|divergence|confidence_score)[^。；\n]*[。；]?', '', reasoning)

    # 4. 去掉残留的 JSON 片段（如 {"direction": "bear}）
    reasoning = re.sub(r'\{[^}]*?"(?:direction|regime|confidence)[^}]*?\}', '', reasoning)

    # 5. 去掉结构化数据片段（如 score=80, confidence=0.95）
    reasoning = re.sub(r'\b[a-z_]+\s*=\s*[0-9.]+\b', '', reasoning)

    # 6. 压缩多余空白
    reasoning = ' '.join(reasoning.split()).strip()

    return reasoning



def _format_review_for_push(review_input) -> str:
    """把 steward review 结构化结果翻译成普通人能看懂的推送文本

    原则：不暴露系统变量（direction/confidence/score），用大白话说清楚"发生了什么"+"你该怎么办"
    
    【FIX #1】Priority 1 修复：
    - 防御性类型检查（处理 JSON 字符串或完整的 25+ 字段 dict）
    - 清理 reasoning 中的调试信息（防止debug文本泄露）
    - 正确处理缺失字段的回退机制
    - 区分 direction（投资信号）和 regime（市场阶段）
    - 最终检查防止 JSON 泄露
    """
    
    # ---- 防御性输入处理 ----
    review = {}
    
    # 情况1：输入是 JSON 字符串
    if isinstance(review_input, str):
        try:
            review = json.loads(review_input)
        except (json.JSONDecodeError, TypeError, ValueError):
            # JSON 解析失败，返回友好提示
            print(f"[WARN] 无法解析 review 输入: {review_input[:100]}")
            return "📊 收盘复盘完成，请打开钱袋子查看详情"
    
    # 情况2：输入是 dict
    elif isinstance(review_input, dict):
        review = review_input
    
    # 情况3：无效输入
    else:
        print(f"[WARN] 无效的 review 输入类型: {type(review_input)}")
        return "📊 收盘复盘完成，请打开钱袋子查看详情"
    
    # 情况4：空 dict（所有字段都缺失）
    if not review or (not review.get("direction") and not review.get("final_direction") and 
                      not review.get("conclusion") and not review.get("final_conclusion")):
        print(f"[WARN] review 输入为空或所有关键字段缺失")
        return "📊 收盘复盘完成，请打开钱袋子查看详情"
    
    # ---- 提取关键字段 ----
    # 注意：direction 是投资方向（bullish/bearish/neutral），不要与 regime 混淆
    direction = review.get("direction") or review.get("final_direction", "neutral")
    conclusion = review.get("conclusion") or review.get("final_conclusion", "")
    reasoning = review.get("reasoning") or review.get("final_reasoning", "")
    
    # 清理 reasoning 中的调试信息
    if reasoning:
        reasoning = _sanitize_reasoning_for_extraction(reasoning)
    
    # ---- 回退机制：如果关键字段缺失，尝试从其他字段恢复 ----
    if not direction or direction not in ["bullish", "bearish", "neutral"]:
        # 尝试从 confidence 推断
        confidence = review.get("confidence") or review.get("final_confidence", 0)
        regime = review.get("regime", "")
        
        if confidence > 60 and regime in ["trending_bull"]:
            direction = "bullish"
        elif confidence > 60 and regime in ["high_vol_bear", "oscillating"]:
            direction = "bearish"
        else:
            direction = "neutral"
    
    if not conclusion:
        # 尝试从 modules_results 补充
        modules = review.get("modules_results", {})
        if modules:
            # 简单启发式：统计模块的方向
            bullish_count = sum(1 for r in modules.values() 
                               if isinstance(r, dict) and r.get("direction") == "bullish")
            bearish_count = sum(1 for r in modules.values() 
                               if isinstance(r, dict) and r.get("direction") == "bearish")
            if bullish_count > bearish_count:
                direction = "bullish"
            elif bearish_count > bullish_count:
                direction = "bearish"
    
    # ---- 组装推送文本 ----
    # 第一行：emoji + 通俗结论
    emoji = "📈" if direction == "bullish" else "📉" if direction == "bearish" else "⚖️"
    headline = _humanize_conclusion(conclusion, direction)
    
    parts = [f"{emoji} {headline}"]
    
    # 中间段：从 reasoning 提取关键信息，翻译为大白话要点
    bullet_points = _extract_human_points(reasoning, review)
    if bullet_points:
        parts.append("\n🔍 怎么回事：")
        for point in bullet_points[:3]:
            parts.append(f"  • {point}")
    
    # 结尾：基于方向给白话建议
    advice = _direction_to_advice(direction)
    if advice:
        parts.append(f"\n💡 {advice}")
    
    result = "\n".join(parts)

    # 最终检查：防止 JSON 泄露（仅检测明确的 JSON 结构，避免误杀正常文本）
    import re as _re
    if result.strip().startswith("{") or _re.search(r'\{\s*"[a-z_]+":', result):
        # 检测到明确的 JSON 泄露（{"key": 格式）
        print(f"[ALERT] Detected JSON leak in formatted result: {result[:100]}")
        return "📊 收盘复盘完成，请打开钱袋子查看详情"

    return result



def _humanize_conclusion(conclusion: str, direction: str) -> str:
    """把 LLM 仲裁的结论翻译成口语化的一句话"""
    if not conclusion:
        if direction == "bullish":
            return "今天市场偏强，氛围不错"
        elif direction == "bearish":
            return "今天市场偏弱，小心一点"
        return "今天市场没什么大动静"

    # 清理常见术语
    text = _humanize_terms(conclusion)
    # 去掉括号里的系统变量（如 "score=80"、"一票否决"）
    import re
    text = re.sub(r'[（(][^）)]*?(?:score|分数|一票否决|门控|直出)[^）)]*?[）)]', '', text)
    # 去掉残留的英文变量
    text = re.sub(r'\b[a-z_]+\s*=\s*\d+', '', text)
    return text.strip() or "今天市场有变化，看看详情"


def _extract_human_points(reasoning: str, review: dict) -> list:
    """从 reasoning 和模块结果中提取 2-3 条普通人能懂的信息点"""
    points = []
    if not reasoning:
        return points

    text = _humanize_terms(reasoning)
    import re

    # 提取北向资金信息
    north_match = re.search(r'北向资金[^，。；]*?(-?\d+\.?\d*)\s*亿', text)
    if north_match:
        val = float(north_match.group(1))
        if val < -100:
            points.append(f"外资5天卖了{abs(val):.0f}亿，还在撤退")
        elif val < 0:
            points.append(f"外资小幅卖出{abs(val):.0f}亿，情绪一般")
        elif val > 100:
            points.append(f"外资5天买入{val:.0f}亿，在加仓")

    # 提取地缘风险
    if "地缘" in text:
        geo_match = re.search(r'地缘[^，。；]{0,30}', text)
        if geo_match:
            geo_text = geo_match.group(0)
            # 简化表述
            if any(w in geo_text for w in ["极高", "高", "严重", "加剧"]):
                points.append("地缘局势紧张，市场避险情绪重")
            elif "中等" in geo_text:
                points.append("地缘局势有些不确定性")

    # 提取涨跌比/广度
    breadth_match = re.search(r'涨跌比[^，。；]*?(\d+)%', text)
    if breadth_match:
        ratio = int(breadth_match.group(1))
        if ratio < 40:
            points.append(f"全场只有{ratio}%的股票在涨，跌的比涨的多")
        elif ratio > 60:
            points.append(f"有{ratio}%的股票在涨，多数票走强")

    # 提取轮动/防御信息（区分进攻/防御）
    direction = review.get("direction", "neutral")
    if "防御" in text:
        points.append("资金在往防御板块（银行/公用事业）躲")
    elif "进攻" in text or "领涨" in text:
        points.append("资金偏进攻，往成长板块走")
    elif "轮动" in text and direction == "bearish":
        points.append("资金在板块间轮动，缺乏主线")

    # 如果没提取到足够要点，用简化版 reasoning
    if len(points) < 2 and reasoning:
        # 按分号/句号切分，取前2段做简化
        segments = re.split(r'[；;。]', text)
        for seg in segments:
            seg = seg.strip()
            if len(seg) > 8 and seg not in points and len(points) < 3:
                # 去掉括号里的数字噪音
                seg = re.sub(r'[（(][^）)]*?\d[^）)]*?[）)]', '', seg).strip()
                if seg:
                    points.append(seg)

    return points[:3]


def _direction_to_advice(direction: str) -> str:
    """根据方向给出一句白话建议"""
    if direction == "bearish":
        return "观望为主，别急着抄底。等市场情绪稳一稳再看。"
    elif direction == "bullish":
        return "氛围还行，手里有仓位可以继续拿着。但别追高。"
    else:
        return "不上不下的行情，没啥操作的必要，安心等就好。"


# 模块/技术术语 → 中文映射（用于推送文本人话化）
_TERM_MAP = {
    "sector_rotation": "板块轮动",
    "factor_data": "多因子数据",
    "factor_ic": "因子IC",
    "macro_data": "宏观数据",
    "macro_extended": "扩展宏观",
    "macro_v8": "宏观V8",
    "market_data": "大盘数据",
    "market_factors": "市场因子",
    "news_data": "新闻舆情",
    "policy_data": "政策数据",
    "technical": "技术面",
    "signal": "信号模块",
    "signal_scout": "信号侦察",
    "risk": "风控模块",
    "regime_engine": "市场状态",
    "global_market": "全球市场",
    "geopolitical": "地缘政治",
    "northbound": "北向资金",
    "breadth": "市场广度",
    "momentum": "动量",
    "volatility": "波动率",
    "valuation_engine": "估值引擎",
    "earnings_forecast": "盈利预测",
    "broker_research": "券商研报",
    "holding_intelligence": "持仓情报",
    "portfolio_doctor": "持仓体检",
    "alt_data": "另类数据",
    "fund_monitor": "基金监控",
    "stock_monitor": "个股监控",
    "monte_carlo": "蒙特卡洛模拟",
    "genetic_factor": "遗传因子",
    "scenario_engine": "情景分析",
    "business_exposure": "业务敞口",
}


def _humanize_terms(text: str) -> str:
    """把 reasoning 中的英文模块名/技术术语替换为中文"""
    for en, zh in _TERM_MAP.items():
        if en in text:
            text = text.replace(en, zh)
    return text


def _sanitize_push_text(text: str) -> str:
    """检测文本是否为原始 JSON，如果是则提取关键信息格式化"""
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                return _format_review_for_push(data)
        except (json.JSONDecodeError, ValueError):
            pass
    return text


def run_close_review():
    """收盘后复盘（升级版：扫描 + R1深度诊断 + steward review + 企微推送）"""
    print("[CRON] 收盘复盘...")
    run_scan()
    date = datetime.now().strftime("%Y-%m-%d")
    
    profiles = _load_profiles()
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        wxwork_uid = p.get("wxworkUserId", "")
        
        # ---- V4 新增：steward 收盘 review ----
        print(f"  [复盘] {name}: steward review...")
        review_text = ""
        review = {}
        try:
            from services.steward import get_steward
            steward = get_steward()
            review = steward.review(uid)
            # 用格式化函数把结构化结果翻译成人话
            review_text = _format_review_for_push(review)

            # 保存复盘结果（前端 /api/steward/review 可读）
            review_dir = MONITOR_DIR / uid / "reviews"
            review_dir.mkdir(parents=True, exist_ok=True)
            review_file = review_dir / f"{date}.json"
            review_file.write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

            # 也存一份 latest
            (review_dir / "latest.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

            print(f"  [复盘] {name}: 已保存 ({len(review_text)}字)")
        except Exception as e:
            print(f"  [复盘] {name}: steward review 失败: {e}")
            review_text = ""
        
        # ---- R1 深度持仓诊断（只有有持仓的用户才跑）----
        diagnosis_text = ""
        try:
            from services.stock_monitor import load_stock_holdings
            from services.fund_monitor import load_fund_holdings
            has_stocks = bool(load_stock_holdings(uid))
            has_funds = bool(load_fund_holdings(uid))
            
            if has_stocks or has_funds:
                print(f"  [诊断] {name}: R1 深度诊断...")
                from services.llm_gateway import LLMGateway
                from services.news_data import get_holdings_news, format_holdings_news_for_prompt
                gw = LLMGateway()
                
                # 组装持仓数据
                scan_file = MONITOR_DIR / uid / "latest.json"
                scan_data = ""
                if scan_file.exists():
                    scan_data = scan_file.read_text(encoding="utf-8")[:3000]
                
                # 拉持仓新闻
                holdings_news_text = ""
                try:
                    stock_h = load_stock_holdings(uid) or []
                    fund_h = load_fund_holdings(uid) or []
                    h_news = get_holdings_news(stock_h, fund_h, limit_per=3)
                    holdings_news_text = format_holdings_news_for_prompt(h_news)
                    if holdings_news_text:
                        print(f"  [诊断] {name}: 拉取 {h_news['summary']}")
                except Exception as e:
                    print(f"  [诊断] {name}: 持仓新闻拉取失败: {e}")
                
                # 加载 close_review prompt
                from pathlib import Path as _P
                _review_prompt = _P(__file__).parent.parent / "prompts" / "close_review.md"
                system_prompt = _review_prompt.read_text(encoding="utf-8") if _review_prompt.exists() else "你是专业投资组合分析师，给出简洁的收盘复盘。"
                
                if scan_data:
                    prompt = f"""## 持仓数据
{scan_data}

## 持仓相关新闻
{holdings_news_text if holdings_news_text else "暂无个股/基金新闻"}

请按 close_review 格式输出收盘复盘，300 字以内。
重要：用普通人能看懂的大白话，不要输出 JSON，不要英文术语。"""
                    
                    result = gw.call_sync(
                        prompt,
                        system=system_prompt,
                        model_tier="llm_heavy",  # R1 深度推理
                        user_id=uid,
                        module="close_review",
                        max_tokens=600,
                    )
                    diagnosis_text = result.get("content", "")
                    
                    # 保存诊断结果
                    diag_file = MONITOR_DIR / uid / "reviews" / f"diagnosis_{date}.json"
                    diag_file.write_text(json.dumps({
                        "date": date,
                        "diagnosis": diagnosis_text,
                        "source": result.get("source", ""),
                        "model": result.get("model", ""),
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    
                    print(f"  [诊断] {name}: 完成 ({len(diagnosis_text)}字)")
        except Exception as e:
            print(f"  [诊断] {name}: R1 诊断失败: {e}")
        
        # ---- 企微推送（扫描结果 + 复盘 + 诊断） ----
        if wxwork_uid:
            try:
                from services.wxwork_push import is_configured, send_daily_report_to
                if is_configured():
                    # 组装推送内容（确保不推原始 JSON）
                    msg_parts = [f"📊 {date} 收盘复盘"]
                    if review_text:
                        msg_parts.append(f"\n{review_text[:300]}")
                    if diagnosis_text:
                        # 防止 LLM 输出原始 JSON
                        safe_diag = _sanitize_push_text(diagnosis_text)
                        msg_parts.append(f"\n🤖 AI诊断:\n{safe_diag[:300]}")
                    msg_parts.append("\n打开钱袋子查看完整报告")

                    send_daily_report_to(wxwork_uid, "\n".join(msg_parts))
                    print(f"  [推送] {name}: 复盘+诊断已推企微")
            except Exception as e:
                print(f"  [推送] {name}: 失败: {e}")

        # ---- V6 Phase 5: 收盘复盘自动存档到 analysis_history ----
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from services.analysis_history import save_analysis
            full_text = ""
            if review_text:
                full_text += f"【管家复盘】\n{review_text}\n\n"
            if diagnosis_text:
                full_text += f"【R1 深度诊断】\n{diagnosis_text}"
            if full_text.strip():
                save_analysis(uid, "deepseek", "DeepSeek 收盘复盘", "full", full_text.strip(), direction="unknown")
                print(f"  [存档] {name}: 复盘已存 analysis_history")
        except Exception as e:
            print(f"  [存档] {name}: 失败: {e}")

    # ---- V6: 地缘风险预警推送 ----
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from services.geopolitical import get_geopolitical_risk_score
        geo = get_geopolitical_risk_score()
        severity = geo.get("max_severity", 0)
        if severity >= 3:
            from services.wxwork_push import is_configured, send_daily_report_to
            if is_configured():
                _geo_level_map = {"low": "低", "normal": "低", "moderate": "中等",
                                  "elevated": "偏高", "high": "高", "extreme": "极高", "critical": "极高"}
                geo_cn = _geo_level_map.get(geo.get('level', ''), geo.get('level', '未知'))
                geo_msg = f"🚨 地缘风险预警\n\n"
                geo_msg += f"风险等级: {geo_cn}（{geo.get('score', 0)}分）\n"
                events = geo.get('top_events', geo.get('events', []))
                if events:
                    geo_msg += "\n相关事件:\n"
                    for e in events[:3]:
                        if isinstance(e, dict):
                            geo_msg += f"• {e.get('title', str(e))[:40]}\n"
                        else:
                            geo_msg += f"• {str(e)[:40]}\n"
                geo_msg += "\n打开钱袋子查看详情"
                for p in profiles:
                    wxid = p.get("wxworkUserId", "")
                    if wxid:
                        send_daily_report_to(wxid, geo_msg)
                print(f"  [地缘] severity={severity}, 已推送预警")
            else:
                print(f"  [地缘] severity={severity}, 企微未配置")
        else:
            print(f"  [地缘] severity={severity}, 无需预警")
    except Exception as e:
        print(f"  [地缘] 预警检查失败: {e}")

    # V4: 判断追踪 — 验证到期判断 + EMA 权重校准
    try:
        from services.judgment_tracker import verify_pending, calibrate
        for p in profiles:
            uid = p.get("id", "")
            if not uid:
                continue
            verified = verify_pending(uid)
            if verified:
                print(f"  [判断] {uid}: 验证 {len(verified)} 条判断")
                cal = calibrate(uid)
                if cal.get("status") == "calibrated":
                    print(f"  [校准] {uid}: 准确率{cal['overall_accuracy']}%, 权重已更新")
    except Exception as e:
        print(f"  [判断/校准] 失败: {e}")


def cleanup_old_snapshots(max_days: int = 7):
    import time
    now = time.time()
    for f in MONITOR_DIR.glob("scan_*.json"):
        if now - f.stat().st_mtime > max_days * 86400:
            f.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="持仓盯盘 cron（多用户按人推送）")
    parser.add_argument("--close", action="store_true", help="收盘复盘模式")
    args = parser.parse_args()

    if args.close:
        run_close_review()
    else:
        run_scan()
