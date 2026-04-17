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
        return [{"id": "LeiJiang", "name": "厉害了哥", "wxworkUserId": ""},
                {"id": "BuLuoGeLi", "name": "部落格里", "wxworkUserId": ""}]


def _call_v3(prompt, max_tokens=500):
    """调用 DeepSeek V3（轻量）"""
    if not LLM_API_KEY:
        return ""
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{LLM_API_URL}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens, "temperature": 0.5},
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log(f"  V3 调用失败: {e}")
    return ""


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
        from services.sector_rotation import get_sector_rotation
        get_sector_rotation()
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

    # 收集数据
    try:
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        from services.factor_data import get_northbound_flow, get_shibor, get_margin_trading
        from services.geopolitical import get_geopolitical_risk_score
        from services.sector_rotation import get_sector_rotation
        from services.broker_research import get_broker_consensus

        fgi = get_fear_greed_index()
        val = get_valuation_percentile()
        north = get_northbound_flow()
        geo = get_geopolitical_risk_score()
        sr = get_sector_rotation()
        br = get_broker_consensus()

        data_text = f"""宏观数据快照:
- 恐贪指数: {fgi.get('score', 50)} ({fgi.get('level', '中性')})
- 估值百分位: {val.get('percentile', 50)}% ({val.get('level', '')})
- 北向资金: 5日{north.get('net_flow_5d', 0):.1f}亿 ({north.get('trend', '中性')})
- 地缘风险: {geo.get('level', '低')} (severity={geo.get('severity', 0)})
- 行业热点: {','.join([s.get('name', '') for s in sr.get('top_gainers', [])[:5]]) if sr.get('available') else '暂无'}
- 机构共识: {br.get('consensus', '未知')}"""

        # R1 宏观研判（用 V3 省钱，凌晨不需要深度推理所有都用 V3 也行）
        prompt = f"""你是 A 股宏观策略分析师，请基于以下数据写一段 200 字以内的市场研判。

{data_text}

要求: 1句话结论 + 3个要点 + 风险提示"""

        analysis = _call_v3(prompt, 400)
        results["macro_analysis"] = analysis
        log(f"  ✅ 宏观研判: {len(analysis)}字")

    except Exception as e:
        log(f"  ❌ Phase 1 失败: {e}")

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

要求: 1句话总评 + 最大风险 + 操作建议"""

            diagnosis = _call_v3(prompt, 300)
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
            from services.decision_maker import generate_decisions
            dec = generate_decisions(uid)
            results[f"decisions_{uid}"] = dec
            log(f"  ✅ {p.get('name', uid)}: {len(dec.get('decisions', []))} 条决策")
        except Exception as e:
            log(f"  ❌ {p.get('name', uid)} 决策失败: {e}")

    return results


# ============================================================
# 04:00 生成分析产物
# ============================================================

def step_generate_products(phase1, phase2, phase3):
    log("📝 04:00 生成分析产物")

    products = {}
    today = date.today().isoformat()

    # 综合简报
    macro = phase1.get("macro_analysis", "暂无宏观分析")
    recs = phase3.get("recommendations", [])
    rec_text = "\n".join(f"  {i+1}. {r.get('name', '')} 评分{r.get('total_score', 0)}"
                         for i, r in enumerate(recs[:5])) if recs else "  暂无推荐"

    briefing = f"""📊 {today} AI 投研日报

【宏观研判】
{macro}

【今日推荐 Top 5】
{rec_text}
"""

    # 逐用户简报
    profiles = _load_profiles()
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        diag = phase2.get(uid, {}).get("diagnosis", "暂无诊断")
        dec = phase3.get(f"decisions_{uid}", {})
        dec_text = ""
        for d in dec.get("decisions", [])[:5]:
            action_label = {"buy": "买入", "sell": "卖出", "hold": "持有", "reduce": "减仓", "add": "加仓"}.get(d.get("action", ""), d.get("action", ""))
            dec_text += f"  {d.get('name', '')} → {action_label}: {d.get('reason', '')}\n"

        scenarios = dec.get("scenarios", {})
        user_briefing = f"""{briefing}
【{name} 持仓诊断】
{diag}

【操作建议】
{dec_text or '  暂无操作建议'}

【三情景】
  🟢 乐观: {scenarios.get('optimistic', '待分析')}
  🟡 中性: {scenarios.get('neutral', '待分析')}
  🔴 悲观: {scenarios.get('pessimistic', '待分析')}

⚠️ AI建议仅供参考，不构成投资建议"""

        products[uid] = user_briefing

        # 存档到 analysis_history
        try:
            from services.analysis_history import save_analysis
            save_analysis(uid, "night_worker", "AI凌晨自动分析", "full",
                         user_briefing, direction="auto", confidence=0)
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
    log("🌍 07:00 外盘+事件检查")
    try:
        prompt = """请用3句话总结今日A股开盘前需要关注的事项（基于常识和近期市场趋势）：
1. 隔夜美股表现对A股的影响
2. 今日有无重要经济数据发布
3. 需要关注的风险/机会"""

        result = _call_v3(prompt, 200)
        log(f"  ✅ 外盘检查: {len(result)}字")
        return result
    except Exception as e:
        log(f"  ❌ 外盘检查失败: {e}")
        return ""


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
        is_pro = (uid == "LeiJiang")
        full_product = products.get(uid, "暂无分析")

        if is_pro:
            # Pro 版：完整分析
            briefing = f"☀️ 早安，{name}！\n\n{full_product}"
            if overnight:
                briefing += f"\n\n【外盘速览】\n{overnight}"
        else:
            # Simple 版：精简大白话
            prompt = f"""把以下投资分析报告改写成大白话版本，给不懂金融的人看，200字以内，用emoji让它更亲切：

{full_product[:500]}"""
            simple = _call_v3(prompt, 300) or f"☀️ 早安！今天市场整体还好，不用太担心~ 😊"
            briefing = simple

        briefings[uid] = briefing
        log(f"  ✅ {name}: {'Pro' if is_pro else 'Simple'} 简报 {len(briefing)}字")

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
            if wxid and uid in briefings:
                # 企微限制字数，截断
                msg = briefings[uid][:2000]
                send_daily_report_to(wxid, msg)
                log(f"  ✅ {p.get('name', uid)}: 已推企微")
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

    # 01:30 数据预热
    step_data_warm()

    # ★ 预热后立即保存因子缓存
    try:
        from services.precomputed_cache import save_precomputed
        from services.factor_data import get_northbound_flow, get_shibor, get_margin_trading
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        from services.sector_rotation import get_sector_rotation
        from services.broker_research import get_broker_consensus

        save_precomputed("factors", {
            "northbound": get_northbound_flow(),
            "shibor": get_shibor(),
            "margin": get_margin_trading(),
        })
        save_precomputed("fear_greed", get_fear_greed_index())
        save_precomputed("valuation", get_valuation_percentile())

        sr = get_sector_rotation()
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
        from services.signal import calculate_daily_signal
        signal = calculate_daily_signal()
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

    # 保存简报（等 08:30 推送）
    briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
    briefing_file.write_text(json.dumps(briefings, ensure_ascii=False, indent=2), encoding="utf-8")

    elapsed = time.time() - start
    log(f"✅ AI 凌晨工作完成，耗时 {elapsed:.0f}秒，等待 08:30 推送")

    return briefings


def push_morning():
    """08:30 推送早安简报（独立调用）"""
    log("📤 08:30 推送早安简报（独立调用）")
    briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
    if briefing_file.exists():
        briefings = json.loads(briefing_file.read_text(encoding="utf-8"))
        step_push_briefing(briefings)
    else:
        log("  ⚠️ 无简报文件，跳过")


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
