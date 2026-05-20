"""
钱袋子 — AI 凌晨自主工作链（01:00-08:30）
设计文档: Part 0 AI 24小时排班表

完整链路:
01:00 数据源健康巡检
01:30 数据预热（Tushare+AKShare 凌晨拉取）
02:00 R1 Phase 1: 宏观环境+地缘政治+行业轮动
02:30 R1 Phase 2: 持仓诊断（逐用户）+ 盈利预测解读
03:00 R1 Phase 3: 买入候选+卖出检查+三情景
04:00 生成分析产物（综合简报+决策清单+风险预警）
05:00 研报存档
06:00 维护（清理过期文件+日志）
07:00 外盘+事件检查
07:30 生成早安简报（Pro/Simple 两版）
08:30 推送早安简报

Token 预算: ¥0.45/天（R1×7 + V3×6）
"""

import sys
import os
import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, date

# 确保 import 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR, LLM_API_URL, LLM_API_KEY

NIGHT_LOG_DIR = DATA_DIR / "night_worker"
NIGHT_LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    # 追加到日志文件
    logfile = NIGHT_LOG_DIR / f"{date.today()}.log"
    with open(logfile, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_profiles():
    """加载所有用户"""
    try:
        from services.stock_monitor import _load_profiles as lp
        return lp()
    except Exception:
        # 2026-04-19 V7.7: id 统一为 name，废弃 u_xxx
        return [{"id": "LeiJiang", "name": "LeiJiang", "wxworkUserId": "LeiJiang"},
                {"id": "BuLuoGeLi", "name": "BuLuoGeLi", "wxworkUserId": "BuLuoGeLi"}]


def _call_v3(prompt, max_tokens=500, system=""):
    """调用 DeepSeek V3（通过 gateway 统一管理）

    Args:
        prompt: 用户 prompt
        max_tokens: 最大输出 token 数
        system: system prompt（用于注入角色设定和硬性约束）
    """
    if not LLM_API_KEY:
        return ""
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()
        result = gw.call_sync(
            prompt,
            system=system,
            model_tier="llm_light",
            user_id="",
            module="night_worker",
            max_tokens=max_tokens,
        )
        if result.get("fallback") or not result.get("content"):
            log(f"  V3 gateway fallback: {result.get('source')}")
            return ""
        return _clean_llm_output(result["content"])
    except Exception as e:
        log(f"  V3 调用失败: {e}")
    return ""


def _clean_llm_output(text: str) -> str:
    """清洗 DeepSeek 输出，去除偶尔泄露的推理过程/元文本

    DeepSeek V3 偶尔会在正文中输出思考过程（"我们基于..."、"注意：这里..."），
    这些不应出现在面向用户的简报中。
    """
    import re
    if not text:
        return text

    # 1. 去除 <think>...</think> 标签包裹的内容（DeepSeek 格式）
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    # 2. 去除开头的推理前缀（常见模式：模型自述分析过程）
    reasoning_prefixes = [
        r'^我们基于.*?进行分析。.*?(?=\n\n|\n[一二三四五六七八九十\d【\[])',
        r'^(?:数据包括|数据分析|根据提供的数据)[:：].*?(?=\n\n|\n[一二三四五六七八九十\d【\[])',
        r'^(?:要求|注意|说明)[:：].*?(?=\n\n|\n[一二三四五六七八九十\d【\[])',
    ]
    for pattern in reasoning_prefixes:
        text = re.sub(pattern, '', text, flags=re.DOTALL).strip()

    # 3. 去除行内的元注释（括号内的自言自语）
    #    例如："极高（注意：这里严重度是0/5但标题说极高，可能矛盾？...）"
    text = re.sub(r'（[^）]*?(?:可能矛盾|按原文|我们按|注意：)[^）]*?）', '', text)

    return text.strip()


# ============================================================
# 01:00 数据源健康巡检
# ============================================================

def step_health_check():
    log("🔍 01:00 数据源健康巡检")
    try:
        from scripts.datasource_health_check import run_health_check
        result = run_health_check()
        failed = [r for r in result if not r.get("ok")]
        if failed:
            log(f"  ⚠️ {len(failed)} 个数据源异常: {[r['name'] for r in failed]}")
            # 推企微给厉害了哥（不推老婆）
            try:
                from services.wxwork_push import is_configured, send_daily_report_to
                if is_configured():
                    msg = f"⚠️ 数据源巡检异常 ({len(failed)}个)\n"
                    msg += "\n".join(f"❌ {r['name']}: {r.get('error', '未知')}" for r in failed)
                    send_daily_report_to("LeiJiang", msg)
            except Exception:
                pass
        else:
            log(f"  ✅ 全部正常")
        return result
    except Exception as e:
        log(f"  ❌ 巡检失败: {e}")
        return []


# ============================================================
# 01:15 月度净资产快照（每月 1 号自动触发）
# ============================================================

def step_monthly_snapshot():
    """每月 1 号保存所有用户的净资产快照"""
    from datetime import date
    if date.today().day != 1:
        return  # 非月初不执行
    log("📸 01:15 月度净资产快照")
    try:
        from services.monthly_snapshot import save_all_users_snapshot
        count = save_all_users_snapshot()
        log(f"  ✅ 快照完成: {count} 个用户")
    except Exception as e:
        log(f"  ❌ 快照失败: {e}")


# ============================================================
# 01:30 数据预热
# ============================================================

def step_data_warm():
    log("📦 01:30 数据预热")
    warmed = []

    # Tushare 数据
    try:
        from services.tushare_data import (is_configured, get_northbound_flow,
                                           get_shibor_rate, get_margin_data, get_research_reports)
        if is_configured():
            get_northbound_flow()
            warmed.append("北向资金")
            time.sleep(0.3)
            get_shibor_rate()
            warmed.append("SHIBOR")
            time.sleep(0.3)
            get_margin_data()
            warmed.append("融资融券")
            time.sleep(0.3)
            get_research_reports(limit=30)
            warmed.append("研报")
    except Exception as e:
        log(f"  Tushare 预热失败: {e}")

    # AKShare 数据
    try:
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        get_fear_greed_index()
        warmed.append("恐贪指数")
        get_valuation_percentile()
        warmed.append("估值百分位")
    except Exception as e:
        log(f"  AKShare 预热失败: {e}")

    try:
        from services.sector_rotation import get_sector_ranking
        get_sector_ranking()
        warmed.append("行业轮动")
    except Exception as e:
        pass

    try:
        from services.geopolitical import get_geopolitical_risk_score
        get_geopolitical_risk_score()
        warmed.append("地缘风险")
    except Exception as e:
        pass

    log(f"  ✅ 预热完成: {', '.join(warmed)} ({len(warmed)}项)")
    return warmed


# ============================================================
# 02:00 R1 Phase 1: 宏观+地缘+行业
# ============================================================

def step_r1_phase1():
    log("🧠 02:00 R1 Phase 1: 全局市场分析")
    results = {}

    # 逐个收集数据（单个失败不影响其他）
    fgi = {"score": 50, "level": "中性"}
    val = {"percentile": 50, "level": ""}
    north = {"net_flow_5d": 0, "trend": "中性"}
    geo = {"level": "低", "max_severity": 0}
    sr = {"available": False}
    br = {"consensus": "未知"}
    margin = {"balance": 0, "change_5d_pct": 0}
    shibor = {"overnight": 0, "trend": ""}

    try:
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        fgi = get_fear_greed_index() or fgi
        val = get_valuation_percentile() or val
        log(f"  恐贪={fgi.get('score')}, 估值百分位={val.get('percentile')}%")
    except Exception as e:
        log(f"  ⚠️ 恐贪/估值失败: {e}")

    try:
        from services.factor_data import get_northbound_flow, get_shibor, get_margin_trading
        north = get_northbound_flow() or north
        shibor = get_shibor() or shibor
        margin = get_margin_trading() or margin
        log(f"  北向5日={north.get('net_flow_5d', 0):.1f}亿, SHIBOR={shibor.get('overnight', 0)}")
    except Exception as e:
        log(f"  ⚠️ 因子数据失败: {e}")

    try:
        from services.geopolitical import get_geopolitical_risk_score
        geo = get_geopolitical_risk_score() or geo
    except Exception as e:
        log(f"  ⚠️ 地缘风险失败: {e}")

    try:
        from services.sector_rotation import get_sector_ranking
        sr = get_sector_ranking() or sr
    except Exception as e:
        log(f"  ⚠️ 行业轮动失败: {e}")

    try:
        from services.broker_research import get_broker_consensus
        br = get_broker_consensus() or br
    except Exception as e:
        log(f"  ⚠️ 研报共识失败: {e}")

    # 保存原始数据供 step_generate_products 使用
    results["raw"] = {
        "fgi": fgi, "val": val, "north": north,
        "geo": geo, "sr": sr, "br": br,
        "margin": margin, "shibor": shibor,
    }

    # 收集新闻
    news_titles = []
    try:
        from services.news_data import get_market_news
        news = get_market_news(limit=5)
        news_titles = [n.get("title", "") for n in news[:5]]
        results["news"] = news_titles
        log(f"  新闻: {len(news_titles)}条")
    except Exception as e:
        log(f"  ⚠️ 新闻获取失败: {e}")

    # 拼接数据文本用于 LLM 研判（全中文，防止 LLM 照搬英文术语）
    sector_text = ','.join([s.get('name', '') for s in sr.get('top_gainers', [])[:5]]) if sr.get('available') else '暂无'
    _geo_level_map = {"low": "低", "normal": "低", "moderate": "中等",
                      "elevated": "偏高", "high": "高", "extreme": "极高", "critical": "极高"}
    geo_cn_for_llm = _geo_level_map.get(geo.get('level', ''), geo.get('level', '低'))
    data_text = f"""宏观数据快照:
- 恐贪指数: {fgi.get('score', 50)} ({fgi.get('level', '中性')})
- 估值百分位: {val.get('percentile', 50)}% ({val.get('level', '')})
- 北向资金: 5日{north.get('net_flow_5d', 0):.1f}亿 ({north.get('trend', '中性')})
- 资金面(银行间利率): {shibor.get('overnight', 0)}% ({shibor.get('trend', '')})
- 融资余额5日变化: {margin.get('change_5d_pct', 0):.1f}%
- 地缘风险: {geo_cn_for_llm}（严重度{geo.get('max_severity', 0)}/5）
- 行业热点: {sector_text}
- 机构共识: {br.get('consensus', '未知')}
- 今日要闻: {'; '.join(news_titles[:3]) if news_titles else '暂无'}"""

    # LLM 研判（可选，失败也不影响简报生成）
    # 严格约束：只基于已提供数据分析，禁止编造任何未提供的数字或事件
    ANTI_HALLUCINATION_SYSTEM = """你是家庭理财管家，帮普通人看懂市场。你有铁律：
1. 只能基于用户提供的数据进行分析，禁止编造任何数据点
2. 禁止提及以下未提供的信息：MLF操作、OMO规模、逆回购、出口数据、新增贷款、PMI、CPI
3. 禁止输出任何精确数字（百分比/亿元），除非数据中已明确给出
4. 如果要做推测，必须用"可能""或许"等不确定性词语
5. 用最简单的中文，像朋友聊天一样，不要金融术语和英文
6. 不要输出你的分析过程/思考过程，直接给结论
7. 违反以上规则等于失职"""

    prompt = f"""请基于以下数据，用200字以内给我一段市场小结。
要求像朋友帮你看盘后的微信消息，通俗易懂。

{data_text}

格式（不用 markdown，纯文本即可）：
一句话：<今天/近期市场怎么样，一句话说清楚>
看点：
- <第1个要点，说清楚对普通人意味着什么>
- <第2个要点>
- <第3个要点>
建议：<一句话，当前该怎么做>

示例风格（内容不要照抄，根据实际数据写）：
一句话：今天盘面偏弱但没大问题，外资在卖但国内资金还稳。
看点：
- 外资连续5天卖出，总量接近470亿，情绪偏谨慎
- 地缘局势让避险资金往银行、黄金跑
- 好消息是市场整体不贵，跌不深
建议：不用慌，持有为主。如果情绪很恐慌了再考虑捡便宜。

重要: 上述数据就是全部信息。如果某个维度数据缺失，直接跳过不提。
禁止: 不要写"我来分析"之类的前缀，直接从"一句话："开始。"""

    analysis = _call_v3(prompt, 400, system=ANTI_HALLUCINATION_SYSTEM)
    if analysis:
        results["macro_analysis"] = analysis
        log(f"  ✅ 宏观研判: {len(analysis)}字")
    else:
        # LLM 不可用，生成纯数据版研判
        results["macro_analysis"] = f"市场情绪{fgi.get('level', '中性')}({fgi.get('score', 50)}分), 外资5日{north.get('net_flow_5d', 0):.0f}亿, 热点:{sector_text}"
        log(f"  ⚠️ LLM 不可用，使用纯数据版")

    return results


# ============================================================
# 02:30 R1 Phase 2: 逐用户持仓诊断
# ============================================================

def step_r1_phase2():
    log("🧠 02:30 R1 Phase 2: 持仓诊断")
    results = {}
    profiles = _load_profiles()

    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        try:
            from services.stock_monitor import load_stock_holdings, scan_all_holdings
            from services.fund_monitor import load_fund_holdings

            stocks = load_stock_holdings(uid) or []
            funds = load_fund_holdings(uid) or []

            if not stocks and not funds:
                log(f"  {name}: 空仓，跳过")
                continue

            # 扫描
            scan = scan_all_holdings(uid) if stocks else {}
            holdings_text = ""
            for h in scan.get("holdings", [])[:10]:
                holdings_text += f"  {h.get('name', '')}({h.get('code', '')}) 盈亏{h.get('pnlPct', 0):+.1f}%\n"
            for f in funds[:5]:
                holdings_text += f"  {f.get('name', '')}({f.get('code', '')})\n"

            prompt = f"""你是投资组合诊断师。请对以下持仓给出简短诊断（150字以内）。

持仓:
{holdings_text}

要求: 1句话总评 + 最大风险 + 操作建议
重要: 只基于上面列出的持仓做判断，不要引用任何你自己知道的市场新闻或数据。"""

            diagnosis = _call_v3(prompt, 300,
                                 system="你是持仓诊断师，只基于用户提供的持仓列表做分析，禁止编造任何市场新闻、政策或未提供的数据。")
            results[uid] = {"diagnosis": diagnosis, "stock_count": len(stocks), "fund_count": len(funds)}
            log(f"  ✅ {name}: {len(diagnosis)}字")

        except Exception as e:
            log(f"  ❌ {name}: {e}")

    return results


# ============================================================
# 03:00 R1 Phase 3: 推荐+决策
# ============================================================

def step_r1_phase3():
    log("🧠 03:00 R1 Phase 3: 推荐+决策")
    results = {}

    # 推荐引擎
    try:
        from services.recommend_engine import get_stock_recommendations
        rec = get_stock_recommendations("", top_n=10, pool="hot")
        results["recommendations"] = rec.get("recommendations", [])[:10]
        log(f"  ✅ 推荐: Top {len(results['recommendations'])} 只")
    except Exception as e:
        log(f"  ❌ 推荐失败: {e}")

    # 逐用户决策
    profiles = _load_profiles()
    for p in profiles:
        uid = p["id"]
        try:
            # V7.7: 空仓用户跳过决策生成（generate_decisions 依赖持仓）
            from services.stock_monitor import load_stock_holdings
            from services.fund_monitor import load_fund_holdings
            if not load_stock_holdings(uid) and not load_fund_holdings(uid):
                log(f"  跳过 {p.get('name', uid)}: 空仓无需决策")
                continue
            # M5 W4: decision_maker v1 已废弃删除，凌晨决策生成暂跳过
            # TODO: 接入 decision_maker_v2 或新决策复盘系统
            log(f"  跳过 {p.get('name', uid)}: 决策生成待迁移到新系统")
        except Exception as e:
            log(f"  ❌ {p.get('name', uid)} 决策失败: {e}")

    return results


# ============================================================
# 04:00 生成分析产物
# ============================================================

def _get_fund_recommendations(top_n=5, category="stock"):
    """V7.7: 基金推荐 — 直接读 fund_rank_ts.json 的 ranks.{category} 已排好序列表

    category: stock / hybrid / bond / index / qdii / etf
    默认 stock（股票型基金），给空仓小白用户一个中高收益的起步选项

    过滤规则：
    - 近1年涨幅 >100% 的极端品种不推荐（可能是行业 ETF 暴涨，风险极高）
    - 近1年涨幅 <5% 的不推荐（收益太低没意义）
    """
    try:
        import json as _json
        rank_file = Path("/opt/moneybag/backend/data/fund_rank_ts.json")
        if not rank_file.exists():
            rank_file = Path("./data/fund_rank_ts.json")
        if not rank_file.exists():
            return []
        data = _json.loads(rank_file.read_text(encoding="utf-8"))
        ranks = data.get("ranks", {})
        if not isinstance(ranks, dict):
            return []
        category_list = ranks.get(category) or ranks.get("all") or []
        if not isinstance(category_list, list):
            return []
        # 过滤极端涨幅：只推荐 5%-100% 区间的基金
        filtered = [f for f in category_list
                    if 5 <= (f.get("return_1y") or 0) <= 100]
        return filtered[:top_n]
    except Exception as e:
        log(f"  基金推荐失败: {e}")
        return []


def _fix_stock_names(recs):
    """批量补全推荐列表中缺失的股票名称"""
    need_fix = [r for r in recs if not r.get('name') or r.get('name') == r.get('code', '')]
    if not need_fix:
        return recs
    try:
        from services.tushare_data import _call_tushare, is_configured
        if is_configured():
            codes = [f"{r.get('code', '')}.SH" if r.get('code', '').startswith('6')
                     else f"{r.get('code', '')}.SZ" for r in need_fix]
            name_rows = _call_tushare("stock_basic",
                                      {"ts_code": ",".join(codes), "list_status": "L"},
                                      "ts_code,name")
            name_map = {nr["ts_code"].split(".")[0]: nr["name"]
                        for nr in (name_rows or []) if nr.get("name")}
            for r in recs:
                code = r.get("code", "")
                if code in name_map:
                    r["name"] = name_map[code]
    except Exception as e:
        log(f"  补名称失败: {e}")
    return recs


def step_generate_products(phase1, phase2, phase3):
    log("📝 04:00 生成分析产物")

    products = {}
    today = date.today().isoformat()

    # ---- 从 Phase1 提取原始数据 ----
    raw = phase1.get("raw", {})
    fgi = raw.get("fgi", {})
    north = raw.get("north", {})
    margin = raw.get("margin", {})
    shibor = raw.get("shibor", {})
    sr = raw.get("sr", {})
    geo = raw.get("geo", {})
    news_titles = phase1.get("news", [])
    macro = phase1.get("macro_analysis", "")

    # ---- 股票推荐（补名称）----
    recs = phase3.get("recommendations", [])
    recs = _fix_stock_names(recs)
    rec_text = "\n".join(
        f"  {i+1}. {r.get('name', r.get('code', '?'))}({r.get('code', '')}) 综合评分{r.get('total_score', 0)}(自研多因子)"
        for i, r in enumerate(recs[:3])
    ) if recs else "  暂无推荐"

    # ---- 基金推荐 ----
    fund_recs = _get_fund_recommendations(top_n=3, category="stock")
    fund_rec_text = "\n".join(
        f"  {i+1}. {f.get('name', '')}（{f.get('code', '')}）近1年+{f.get('return_1y', '?')}%"
        for i, f in enumerate(fund_recs[:3])
    ) if fund_recs else "  暂无基金推荐"

    # ---- 市场温度（普通人看得懂的版本）----
    temp_parts = []
    temp_parts.append(f"市场情绪: {fgi.get('level', '中性')}({fgi.get('score', '?')}分)")
    if north.get("net_flow_5d"):
        flow = north['net_flow_5d']
        if flow < 0:
            temp_parts.append(f"外资动向: 5天净卖出{abs(flow):.0f}亿")
        else:
            temp_parts.append(f"外资动向: 5天净买入{flow:.0f}亿")
    if margin.get("change_5d_pct"):
        pct = margin['change_5d_pct']
        if pct > 1:
            margin_desc = "明显增加"
        elif pct > 0:
            margin_desc = "小幅增加"
        elif pct < -1:
            margin_desc = "明显减少"
        else:
            margin_desc = "小幅减少"
        temp_parts.append(f"杠杆资金: {margin_desc}")
    if shibor.get("overnight"):
        # SHIBOR = 银行间隔夜拆借利率，翻译为「资金面」
        rate = shibor['overnight']
        if rate > 2.5:
            shibor_desc = "偏紧"
        elif rate < 1.5:
            shibor_desc = "宽松"
        else:
            shibor_desc = "正常"
        temp_parts.append(f"资金面: {shibor_desc}")
    temp_text = " | ".join(temp_parts) if temp_parts else "数据获取中"

    # ---- 行业热点 ----
    if sr.get("available") and sr.get("top_gainers"):
        sector_items = sr["top_gainers"][:3]
        sector_text = " | ".join(
            f"{s.get('name', '?')} {s.get('change_pct', 0):+.1f}%"
            for s in sector_items
        )
    else:
        sector_text = "今日暂无明显热点板块"

    # ---- 新闻 ----
    news_text = "\n".join(f"  • {t[:40]}" for t in news_titles[:3]) if news_titles else "  • 暂无新闻"

    # ---- 地缘风险 ----
    geo_level_map = {"low": "低", "normal": "低", "moderate": "中等",
                     "elevated": "偏高", "high": "高", "extreme": "极高", "critical": "极高"}
    geo_cn = geo_level_map.get(geo.get('level', ''), geo.get('level', '低'))
    geo_text = f"地缘风险: {geo_cn}"
    if geo.get('max_severity', 0) >= 3:
        geo_text += " ⚠️"
    # 附上具体事件摘要（有的话）
    top_events = geo.get("top_events", [])
    if top_events:
        event_titles = [e.get("title", "")[:30] for e in top_events[:2] if e.get("title")]
        if event_titles:
            geo_text += f"\n  关注: {'、'.join(event_titles)}"

    # ---- 组装核心简报 ----
    briefing = f"""📊 {today} 钱袋子晨报

📊 【市场温度】（数据源: Tushare/AKShare实时）
{temp_text}

🏭 【行业热点】
{sector_text}

📰 【今日要闻】
{news_text}

🌍 【风险提示】
{geo_text}

{'📝 【AI研判】' + chr(10) + macro + chr(10) if macro else ''}
📈 【股票推荐 Top 3】
{rec_text}

💰 【基金推荐 Top 3】
{fund_rec_text}

⚠️ AI建议仅供参考，不构成投资建议"""

    # ---- 逐用户简报 ----
    profiles = _load_profiles()
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)

        user_phase2 = phase2.get(uid, {})
        is_empty = (
            not user_phase2 or
            (user_phase2.get("stock_count", 0) == 0 and user_phase2.get("fund_count", 0) == 0)
        )

        if is_empty:
            user_briefing = briefing
        else:
            diag = user_phase2.get("diagnosis", "暂无诊断")
            dec = phase3.get(f"decisions_{uid}", {})
            dec_text = ""
            for d in dec.get("decisions", [])[:5]:
                action_label = {"buy": "买入", "sell": "卖出", "hold": "持有",
                                "reduce": "减仓", "add": "加仓"}.get(d.get("action", ""),
                                                                       d.get("action", ""))
                dec_text += f"  {d.get('name', '')} → {action_label}: {d.get('reason', '')}\n"

            user_briefing = f"""{briefing}

📋 【{name} 持仓诊断】
{diag}

{'📌 【操作建议】' + chr(10) + dec_text if dec_text else ''}
⚠️ AI建议仅供参考，不构成投资建议"""

        products[uid] = user_briefing

        # 存档
        try:
            from services.analysis_history import save_analysis
            save_analysis(uid, "night_worker", "AI凌晨自动分析", "full",
                         user_briefing, direction="unknown", confidence=0)
        except Exception:
            pass

    # 保存产物文件
    product_file = NIGHT_LOG_DIR / f"products_{today}.json"
    product_file.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  ✅ 分析产物已生成: {len(products)} 份")

    return products


# ============================================================
# 05:00 研报存档
# ============================================================

def step_archive_reports():
    log("📦 05:00 研报存档")
    try:
        from services.broker_research import get_broker_consensus, get_latest_reports
        consensus = get_broker_consensus()
        reports = get_latest_reports(limit=30)

        archive = {
            "date": date.today().isoformat(),
            "consensus": consensus,
            "report_count": len(reports),
            "archived_at": datetime.now().isoformat(),
        }
        archive_file = NIGHT_LOG_DIR / f"reports_{date.today()}.json"
        archive_file.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  ✅ {len(reports)} 篇研报已存档")
    except Exception as e:
        log(f"  ❌ 研报存档失败: {e}")


# ============================================================
# 06:00 维护
# ============================================================

def step_maintenance():
    log("🧹 06:00 维护任务")
    # 清理过期分析历史
    try:
        from services.analysis_history import cleanup_old_records
        result = cleanup_old_records(max_days=90)
        log(f"  清理过期记录: {result.get('deleted', 0)} 条")
    except Exception:
        pass

    # 清理旧的 night_worker 日志
    try:
        cutoff = datetime.now().timestamp() - 30 * 86400
        for f in NIGHT_LOG_DIR.glob("*.log"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
        for f in NIGHT_LOG_DIR.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
    except Exception:
        pass

    log("  ✅ 维护完成")


# ============================================================
# 07:00 外盘+事件
# ============================================================

def step_overnight_check():
    """外盘速览 — 纯数据驱动，禁止 LLM 凭空编造

    数据源优先级:
    1. get_global_futures_snapshot() — A50/标普/道指/纳指/原油/黄金 期货实时
    2. get_us_indices() — 美股三大指数收盘
    3. get_forex_data() — 汇率

    如果数据全部获取失败 → 返回明确提示"暂不可用"，不让 LLM 编故事
    """
    log("🌍 07:00 外盘+事件检查")
    parts = []

    # ── 数据源 1: 全球期货快照（A50/美指/大宗） ──
    futures = {}
    try:
        from infra.data_source.macro.indicators import get_global_futures_snapshot
        futures = get_global_futures_snapshot()
        if futures.get("available"):
            # A50 期货
            a50 = futures.get("a50")
            if a50 and a50.get("change_pct") is not None:
                emoji = "📈" if a50["change_pct"] > 0 else "📉"
                parts.append(f"{emoji} A50期指: {a50['price']:,.0f} ({a50['change_pct']:+.2f}%)")

            # 美股三大指数期货
            for key, name in [("sp500", "标普500"), ("dji", "道琼斯"), ("nasdaq", "纳斯达克")]:
                d = futures.get(key)
                if d and d.get("change_pct") is not None:
                    emoji = "📈" if d["change_pct"] > 0 else "📉"
                    parts.append(f"{emoji} {name}期货: {d['change_pct']:+.2f}%")

            # 大宗商品
            oil = futures.get("oil")
            if oil and oil.get("change_pct") is not None:
                parts.append(f"🛢️ 原油: ${oil['price']:.1f} ({oil['change_pct']:+.2f}%)")

            gold = futures.get("gold")
            if gold and gold.get("price") is not None:
                parts.append(f"🥇 黄金: ${gold['price']:.1f}/盎司 ({gold.get('change_pct', 0):+.2f}%)")
    except Exception as e:
        log(f"  期货快照获取失败: {e}")

    # ── 数据源 2: 美股三大指数收盘（如果期货没拿到） ──
    if not any(k in (futures or {}) for k in ["sp500", "dji", "nasdaq"]) or not futures.get("available"):
        try:
            from services.global_market import get_us_indices
            us = get_us_indices()
            if us.get("available"):
                for key, name in [("dji", "道琼斯"), ("spx", "标普500"), ("ixic", "纳斯达克")]:
                    d = us.get(key)
                    if d:
                        emoji = "📈" if d["change_pct"] > 0 else "📉"
                        parts.append(f"{emoji} {name}: {d['close']:,.0f} ({d['change_pct']:+.2f}%)")
        except Exception as e:
            log(f"  美股数据失败: {e}")

    # ── 数据源 3: 汇率 ──
    try:
        from services.global_market import get_forex_data
        fx = get_forex_data()
        if fx.get("available") and fx.get("usdcny"):
            parts.append(f"💱 美元/人民币: {fx['usdcny']['rate']:.4f}")
    except Exception as e:
        log(f"  汇率数据失败: {e}")

    # ── 数据源 4: 恒生指数（新浪源，比东方财富稳定） ──
    try:
        from infra.data_source.macro.indicators import get_hsi_latest
        hsi = get_hsi_latest()
        if hsi and hsi.get("change_pct") is not None:
            emoji = "📈" if hsi["change_pct"] > 0 else "📉"
            parts.append(f"{emoji} 恒生指数: {hsi['price']:,.0f} ({hsi['change_pct']:+.2f}%)")
    except Exception as e:
        log(f"  恒指数据失败: {e}")

    # ── 组装结果 ──
    if parts:
        # 有真实数据，用 LLM 生成简短总结（严格限制只用提供的数据）
        data_summary = "\n".join(parts)
        prompt = f"""以下是今日开盘前的外盘数据，请用2-3句话总结对A股的影响：

{data_summary}

要求：
1. 只基于上面的数据做判断，禁止补充任何未提供的信息
2. 不要提及 MLF/OMO/央行操作/出口数据等你不知道的东西
3. 简洁说明利好还是利空，以及主要影响哪些板块"""

        system = "你是市场数据播报员，只转述已有数据，严禁编造任何未提供的数据点。"
        llm_summary = _call_v3(prompt, 150, system=system)

        if llm_summary:
            result = f"{data_summary}\n\n💡 {llm_summary}"
        else:
            # LLM 不可用也没关系，纯数据已经够用
            result = data_summary
    else:
        # 所有数据源都失败 → 明确告知，不编故事
        result = "外盘数据暂不可用（数据源连接异常），请稍后查看东方财富全球行情"

    log(f"  ✅ 外盘检查: {len(result)}字, 数据点{len(parts)}个")
    return result


# ============================================================
# 07:30 生成早安简报
# ============================================================

def step_morning_briefing(products, overnight):
    log("📋 07:30 生成早安简报")
    briefings = {}
    profiles = _load_profiles()

    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        # 默认全部 Pro 版；用户 profile 里 display_mode="simple" 时才切简洁版
        display_mode = p.get("display_mode", "pro")
        full_product = products.get(uid, "暂无分析")

        if display_mode != "simple":
            # Pro 版（默认）：完整内容 + 外盘
            briefing = f"☀️ 早安，{name}！\n\n{full_product}"
            if overnight:
                briefing += f"\n\n🌍 【外盘速览】\n{overnight}"
        else:
            # Simple 版：精简要点（用户主动切换时才走这里）
            simple = f"☀️ 早安，{name}！\n\n"
            prompt = f"""把以下投资报告改写成大白话，给不懂金融的人看，150字以内，亲切友好：

{full_product[:400]}"""
            llm_simple = _call_v3(prompt, 250)
            if llm_simple:
                simple += llm_simple
            else:
                simple += full_product[:500]
            briefing = simple

        briefings[uid] = briefing
        log(f"  ✅ {name}: {'Pro' if display_mode != 'simple' else 'Simple'} 简报 {len(briefing)}字")

    return briefings


# ============================================================
# 08:30 推送早安简报
# ============================================================

def step_push_briefing(briefings):
    log("📤 08:30 推送早安简报")
    try:
        from services.wxwork_push import is_configured, send_daily_report_to
        if not is_configured():
            log("  ⚠️ 企微未配置，跳过推送")
            return

        profiles = _load_profiles()
        for p in profiles:
            uid = p["id"]
            wxid = p.get("wxworkUserId", "")
            if not wxid or uid not in briefings:
                continue
            # 企微限制字数，截断
            msg = briefings[uid][:2000]
            # wxworkUserId 无效时降级为 @all
            result = send_daily_report_to(wxid, msg)
            if result.get("ok"):
                log(f"  ✅ {p.get('name', uid)}: 已推企微")
            else:
                # 推送失败（如 userId 不存在），尝试 @all
                err = result.get("data", {}).get("errcode", 0)
                if err == 81013:
                    log(f"  ⚠️ {p.get('name', uid)}: userId无效，改用@all")
                    send_daily_report_to("@all", msg)
                else:
                    log(f"  ❌ {p.get('name', uid)}: 推送失败 {result}")
    except Exception as e:
        log(f"  ❌ 推送失败: {e}")


# ============================================================
# 主函数：按时间顺序执行全链路
# ============================================================

def run_night_worker():
    """AI 凌晨自主工作主函数（01:00-08:30）"""
    log("🌙 ========================================")
    log(f"🌙 AI 凌晨工作启动 {date.today()}")
    log("🌙 ========================================")

    start = time.time()

    # 01:00 健康巡检
    step_health_check()

    # 01:15 月度快照（每月1号执行）
    step_monthly_snapshot()

    # 01:30 数据预热
    step_data_warm()

    # ★ 预热后立即保存因子缓存
    try:
        from services.precomputed_cache import save_precomputed
        from services.factor_data import get_northbound_flow, get_shibor, get_margin_trading
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        from services.sector_rotation import get_sector_ranking
        from services.broker_research import get_broker_consensus

        save_precomputed("factors", {
            "northbound": get_northbound_flow(),
            "shibor": get_shibor(),
            "margin": get_margin_trading(),
        })
        save_precomputed("fear_greed", get_fear_greed_index())
        save_precomputed("valuation", get_valuation_percentile())

        sr = get_sector_ranking()
        if sr.get("available"):
            save_precomputed("sector_rotation", sr)

        br = get_broker_consensus()
        if br.get("available"):
            save_precomputed("broker_consensus", br)

        log("  ★ 因子+指标 预计算缓存已保存")
    except Exception as e:
        log(f"  预计算缓存保存失败: {e}")

    # 02:00 R1 Phase 1: 全局市场
    phase1 = step_r1_phase1()

    # ★ 保存 13 维信号预计算
    try:
        from services.precomputed_cache import save_precomputed
        from services.signal import generate_daily_signal
        signal = generate_daily_signal()
        save_precomputed("daily_signal", signal)
        log("  ★ 13维信号预计算缓存已保存")
    except Exception as e:
        log(f"  信号预计算失败: {e}")

    # 02:30 R1 Phase 2: 持仓诊断
    phase2 = step_r1_phase2()

    # 03:00 R1 Phase 3: 推荐+决策
    phase3 = step_r1_phase3()

    # ★ 保存推荐和决策到预计算缓存
    try:
        from services.precomputed_cache import save_precomputed
        if phase3.get("recommendations"):
            save_precomputed("recommendations", {"recommendations": phase3["recommendations"]})
        for p in _load_profiles():
            uid = p["id"]
            dec_key = f"decisions_{uid}"
            if dec_key in phase3:
                save_precomputed("decisions", phase3[dec_key], user_id=uid)
        # 预计算4个预设情景
        try:
            from services.scenario_engine import analyze_scenario, PRESET_SCENARIOS
            for sid in PRESET_SCENARIOS:
                result = analyze_scenario(scenario_id=sid)
                if result.get("available"):
                    save_precomputed(f"scenario_{sid}", result)
            log("  ★ 4个预设情景已预计算")
        except Exception as e:
            log(f"  预设情景预计算失败: {e}")
        log("  ★ 推荐+决策+情景 预计算缓存已保存")
    except Exception as e:
        log(f"  预计算缓存保存失败: {e}")

    # 04:00 生成分析产物
    products = step_generate_products(phase1, phase2, phase3)

    # 05:00 研报存档
    step_archive_reports()

    # 06:00 维护
    step_maintenance()

    # 07:00 外盘+事件
    overnight = step_overnight_check()

    # 07:30 早安简报
    briefings = step_morning_briefing(products, overnight)

    # 保存简报文件（08:30 由 --push-only 推送）
    briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
    briefing_file.write_text(json.dumps(briefings, ensure_ascii=False, indent=2), encoding="utf-8")

    elapsed = time.time() - start
    log(f"✅ AI 凌晨工作完成，耗时 {elapsed:.0f}秒，简报已就绪等待 08:30 推送")

    return briefings


def push_morning():
    """08:30 推送早安简报（独立 cron 调用）

    由 crontab '30 8 * * 1-5' 触发，读取凌晨生成的简报文件并推送。
    """
    log("📤 08:30 推送早安简报")
    briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
    if briefing_file.exists():
        briefings = json.loads(briefing_file.read_text(encoding="utf-8"))
        step_push_briefing(briefings)
    else:
        log("  ⚠️ 无简报文件，凌晨流程可能未执行")
        # 推送异常通知给 LeiJiang
        try:
            from services.wxwork_push import is_configured, send_daily_report_to
            if is_configured():
                send_daily_report_to("LeiJiang", "⚠️ 今日晨报未生成，凌晨流程可能失败，请检查 night.log")
        except Exception:
            pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI 凌晨自主工作链")
    parser.add_argument("--push-only", action="store_true", help="只推送简报(08:30)")
    parser.add_argument("--step", type=str, help="只执行某一步(health/warm/phase1/phase2/phase3/products/reports/maintain/overnight/briefing)")
    args = parser.parse_args()

    if args.push_only:
        push_morning()
    elif args.step:
        steps = {
            "health": step_health_check,
            "warm": step_data_warm,
            "phase1": step_r1_phase1,
            "phase2": step_r1_phase2,
            "phase3": step_r1_phase3,
            "reports": step_archive_reports,
            "maintain": step_maintenance,
            "overnight": step_overnight_check,
        }
        if args.step in steps:
            steps[args.step]()
        else:
            print(f"未知步骤: {args.step}, 可选: {list(steps.keys())}")
    else:
        briefings = run_night_worker()
