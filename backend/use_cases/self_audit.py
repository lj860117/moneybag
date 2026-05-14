"""
钱袋子 — 每周自检用例 V1
========================
每周日凌晨 2 点由 systemd timer 触发，对：
  1. 各数据源做探针采样（规则检查，不花 token）
  2. 主要 API 端点做冒烟测试（本地调用，不走网络）
  3. 把采样摘要喂给 DeepSeek V4 Pro，让 LLM 找逻辑矛盾和异常
  4. 结果写入 data/audit/latest.json
  5. 企微推送审计摘要

审计分三级：
  🟢 PASS  — 正常
  🟡 WARN  — 数据疑似过期/偏差，建议关注
  🔴 FAIL  — 数据源返回空/异常，或 LLM 发现严重逻辑矛盾

设计约束：
  - 凌晨不开盘 → 不检查实时行情，只检查"上次收盘缓存是否有效"
  - 每周只跑一次 → LLM 审计用 V4 Pro，不心疼 token
  - 结果落盘 → 前端启动时轮询 /api/audit/latest，有新报告显示 banner
"""
from __future__ import annotations
import os
import sys
import json
import time
import traceback
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Callable

# 确保 import 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 加载 .env
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from config import DATA_DIR

AUDIT_DIR = DATA_DIR / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LATEST = AUDIT_DIR / "latest.json"
AUDIT_HISTORY_DIR = AUDIT_DIR / "history"
AUDIT_HISTORY_DIR.mkdir(exist_ok=True)


# ============================================================
# 工具函数
# ============================================================

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[{_ts()}][AUDIT] {msg}")


def _check(
    name: str,
    fn: Callable[[], Any],
    *,
    validators: list[Any] | None = None,
    warn_age_hours: float = 25.0,   # 超过 25h 认为过期（周末不开盘）
) -> dict[str, Any]:
    """
    执行单项探针检查。
    返回 {"name", "status", "value_summary", "issue", "elapsed_ms"}
    """
    start = time.time()
    result: dict[str, Any] = {"name": name, "status": "pass", "value_summary": "", "issue": ""}
    try:
        val = fn()
        elapsed = int((time.time() - start) * 1000)
        result["elapsed_ms"] = elapsed

        # 基础空值检查
        if val is None or val == {} or val == []:
            result["status"] = "fail"
            result["issue"] = "返回空值"
            return result

        # 数据新鲜度检查（如果有 timestamp/updated_at 字段）
        ts_fields = ["timestamp", "updated_at", "cached_at", "date", "trade_date"]
        for field in ts_fields:
            raw = None
            if isinstance(val, dict):
                raw = val.get(field)
            if raw:
                try:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00").split("+")[0])
                    age_h = (datetime.now() - dt).total_seconds() / 3600
                    if age_h > warn_age_hours:
                        result["status"] = "warn"
                        result["issue"] = f"数据可能过期（{age_h:.1f}h前）"
                except Exception:
                    pass
                break

        # 自定义验证器
        if validators:
            for v in validators:
                ok, msg = v(val)
                if not ok:
                    result["status"] = "warn" if result["status"] == "pass" else result["status"]
                    result["issue"] += f" | {msg}"

        # value_summary：截取关键字段做摘要（供 LLM 阅读）
        if isinstance(val, dict):
            keys_to_show = list(val.keys())[:8]
            summary_parts = []
            for k in keys_to_show:
                v2 = val[k]
                if isinstance(v2, (int, float, str, bool)):
                    summary_parts.append(f"{k}={v2}")
            result["value_summary"] = ", ".join(summary_parts[:6])
        elif isinstance(val, list):
            result["value_summary"] = f"list[{len(val)}]"

    except Exception as e:
        result["status"] = "fail"
        result["issue"] = str(e)[:200]
        result["elapsed_ms"] = int((time.time() - start) * 1000)

    return result


# ============================================================
# 1. 数据源探针（规则检查）
# ============================================================

def run_data_probes() -> list[dict[str, Any]]:
    """逐一探测各数据源，返回检查结果列表"""
    _log("数据源探针开始")
    results = []

    # --- 恐贪指数 ---
    def _check_fgi(val: Any) -> tuple[bool, str]:
        score = val.get("score", -1) if isinstance(val, dict) else -1
        if not (0 <= score <= 100):
            return False, f"score={score} 不在 0-100 范围"
        return True, ""
    results.append(_check("恐贪指数", lambda: __import__("services.market_data", fromlist=["get_fear_greed_index"]).get_fear_greed_index(), validators=[_check_fgi]))

    # --- 估值百分位 ---
    def _check_val(val: Any) -> tuple[bool, str]:
        pct = val.get("percentile", -1) if isinstance(val, dict) else -1
        if not (0 <= pct <= 100):
            return False, f"percentile={pct} 超范围"
        return True, ""
    results.append(_check("估值百分位", lambda: __import__("services.market_data", fromlist=["get_valuation_percentile"]).get_valuation_percentile(), validators=[_check_val]))

    # --- 北向资金 ---
    def _check_north(val: Any) -> tuple[bool, str]:
        if not isinstance(val, dict):
            return False, "非 dict"
        flow = val.get("net_flow_5d", None)
        if flow is None:
            return False, "缺少 net_flow_5d"
        if abs(float(flow)) > 5000:   # 单日北向超 5000 亿视为异常
            return False, f"net_flow_5d={flow} 数值异常（>5000亿）"
        return True, ""
    try:
        from services.factor_data import get_northbound_flow
        results.append(_check("北向资金", get_northbound_flow, validators=[_check_north]))
    except Exception as e:
        results.append({"name": "北向资金", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- SHIBOR ---
    try:
        from services.factor_data import get_shibor
        def _check_shibor(val: Any) -> tuple[bool, str]:
            rate = val.get("overnight", -1) if isinstance(val, dict) else -1
            if not (0 < rate < 30):
                return False, f"overnight={rate}% 超范围"
            return True, ""
        results.append(_check("SHIBOR", get_shibor, validators=[_check_shibor]))
    except Exception as e:
        results.append({"name": "SHIBOR", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- 融资融券 ---
    try:
        from services.factor_data import get_margin_trading
        results.append(_check("融资融券", get_margin_trading))
    except Exception as e:
        results.append({"name": "融资融券", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- 行业轮动 ---
    try:
        from services.sector_rotation import get_sector_ranking
        def _check_sr(val: Any) -> tuple[bool, str]:
            if not val:
                return False, "返回空"
            return True, ""
        results.append(_check("行业轮动", get_sector_ranking, validators=[_check_sr]))
    except Exception as e:
        results.append({"name": "行业轮动", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- 研报共识 ---
    try:
        from services.broker_research import get_broker_consensus
        results.append(_check("研报共识", get_broker_consensus))
    except Exception as e:
        results.append({"name": "研报共识", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- 地缘风险 ---
    try:
        from services.geopolitical import get_geopolitical_risk_score
        results.append(_check("地缘风险", get_geopolitical_risk_score))
    except Exception as e:
        results.append({"name": "地缘风险", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- 宏观日历 ---
    try:
        from services.macro_data import get_macro_calendar
        results.append(_check("宏观日历", get_macro_calendar, warn_age_hours=72))
    except Exception as e:
        results.append({"name": "宏观日历", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- 新闻情绪 ---
    try:
        from services.data_layer import get_news_sentiment_score
        results.append(_check("新闻情绪", get_news_sentiment_score))
    except Exception as e:
        results.append({"name": "新闻情绪", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- 全球市场 ---
    try:
        from services.global_market import get_global_snapshot
        results.append(_check("全球市场", get_global_snapshot))
    except Exception as e:
        results.append({"name": "全球市场", "status": "fail", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # --- 选股结果（检查缓存文件是否存在且不过期）---
    try:
        cache_file = DATA_DIR / "_cache" / "stock_screen_50.json"
        def _load_screen() -> dict[str, Any]:
            if not cache_file.exists():
                raise FileNotFoundError("缓存文件不存在")
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            age_h = (time.time() - data.get("expires_at", 0) + data.get("ttl_hours", 18) * 3600) / 3600
            return {"cached_at": data.get("cached_at", ""), "count": len(data.get("data", []))}
        results.append(_check("选股缓存", _load_screen, warn_age_hours=72))
    except Exception as e:
        results.append({"name": "选股缓存", "status": "warn", "issue": str(e), "value_summary": "", "elapsed_ms": 0})

    # 汇总日志
    fails = [r for r in results if r["status"] == "fail"]
    warns = [r for r in results if r["status"] == "warn"]
    _log(f"探针完成: {len(results)}项, {len(fails)}失败, {len(warns)}警告")
    return results


# ============================================================
# 2. API 功能冒烟测试
# ============================================================

def run_smoke_tests() -> list[dict[str, Any]]:
    """对主要业务逻辑做冒烟检查（直接调用函数，不走 HTTP）"""
    _log("功能冒烟测试开始")
    results = []

    # --- 13 维信号生成 ---
    try:
        start = time.time()
        from services.signal import generate_daily_signal
        sig = generate_daily_signal()
        elapsed = int((time.time() - start) * 1000)
        score = sig.get("score", -1) if isinstance(sig, dict) else -1
        if isinstance(sig, dict) and 0 <= score <= 100:
            results.append({"name": "13维信号", "status": "pass", "value_summary": f"score={score}", "issue": "", "elapsed_ms": elapsed})
        else:
            results.append({"name": "13维信号", "status": "warn", "value_summary": str(sig)[:80], "issue": f"score={score} 异常", "elapsed_ms": elapsed})
    except Exception as e:
        results.append({"name": "13维信号", "status": "fail", "value_summary": "", "issue": str(e)[:200], "elapsed_ms": 0})

    # --- 市场 Regime 判断 ---
    try:
        start = time.time()
        from services.regime_engine import classify
        regime = classify()
        elapsed = int((time.time() - start) * 1000)
        label = regime.get("label", "") if isinstance(regime, dict) else ""
        if label:
            results.append({"name": "Regime判断", "status": "pass", "value_summary": f"label={label}", "issue": "", "elapsed_ms": elapsed})
        else:
            results.append({"name": "Regime判断", "status": "warn", "value_summary": str(regime)[:80], "issue": "label 为空", "elapsed_ms": elapsed})
    except Exception as e:
        results.append({"name": "Regime判断", "status": "fail", "value_summary": "", "issue": str(e)[:200], "elapsed_ms": 0})

    # --- 用户记忆系统 ---
    try:
        start = time.time()
        from services.agent_memory import build_memory_summary
        mem = build_memory_summary("qa_test_20260419")
        elapsed = int((time.time() - start) * 1000)
        results.append({"name": "用户记忆", "status": "pass", "value_summary": f"len={len(mem or '')}", "issue": "", "elapsed_ms": elapsed})
    except Exception as e:
        results.append({"name": "用户记忆", "status": "fail", "value_summary": "", "issue": str(e)[:200], "elapsed_ms": 0})

    # --- RAG 检索 ---
    try:
        start = time.time()
        from infra.knowledge import get_retriever
        from domain.protocols.knowledge_retriever import KnowledgeRetrieverProtocol
        from typing import cast as _cast
        retriever: KnowledgeRetrieverProtocol = _cast(KnowledgeRetrieverProtocol, get_retriever())
        total = retriever.total_chunks()
        elapsed = int((time.time() - start) * 1000)
        if total > 0:
            results.append({"name": "RAG知识库", "status": "pass", "value_summary": f"chunks={total}", "issue": "", "elapsed_ms": elapsed})
        else:
            results.append({"name": "RAG知识库", "status": "warn", "value_summary": "chunks=0", "issue": "知识库为空，可能未索引", "elapsed_ms": elapsed})
    except Exception as e:
        results.append({"name": "RAG知识库", "status": "fail", "value_summary": "", "issue": str(e)[:200], "elapsed_ms": 0})

    # --- LLM 网关可用性（只检查配置，不真正调用省 token）---
    try:
        api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        api_base = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
        if api_key and len(api_key) > 10:
            results.append({"name": "LLM配置", "status": "pass", "value_summary": f"key=SET, base={api_base}", "issue": "", "elapsed_ms": 0})
        else:
            results.append({"name": "LLM配置", "status": "fail", "value_summary": "", "issue": "LLM_API_KEY 未配置", "elapsed_ms": 0})
    except Exception as e:
        results.append({"name": "LLM配置", "status": "fail", "value_summary": "", "issue": str(e), "elapsed_ms": 0})

    fails = [r for r in results if r["status"] == "fail"]
    warns = [r for r in results if r["status"] == "warn"]
    _log(f"冒烟测试完成: {len(results)}项, {len(fails)}失败, {len(warns)}警告")
    return results


# ============================================================
# 3. LLM 语义审计（每周精华）
# ============================================================

def run_llm_audit(probe_results: list[dict[str, Any]], smoke_results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    把数据摘要喂给 V4 Pro，让 LLM 找：
    - 数据之间的逻辑矛盾（如北向资金净流入但恐贪极度恐慌）
    - 疑似数据失效（数值长期不变、全部为 0 等）
    - 功能逻辑 bug（信号打分与 Regime 判断方向相反等）
    """
    _log("LLM 语义审计开始")

    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _log("  跳过（无 API Key）")
        return {"available": False, "analysis": "", "issues": [], "score": 100}

    # 组装摘要给 LLM
    probe_text = "\n".join(
        f"  [{r['status'].upper()}] {r['name']}: {r['value_summary']}"
        + (f" ⚠️{r['issue']}" if r["issue"] else "")
        for r in probe_results
    )
    smoke_text = "\n".join(
        f"  [{r['status'].upper()}] {r['name']}: {r['value_summary']}"
        + (f" ⚠️{r['issue']}" if r["issue"] else "")
        for r in smoke_results
    )

    prompt = f"""你是一个金融数据质量审计专家，正在审查一个 A 股家庭资产管理 App 的后端数据健康状况。

## 本次数据源探针结果
{probe_text}

## 功能冒烟测试结果
{smoke_text}

请从以下角度分析：
1. **数据逻辑一致性**：各指标之间是否存在明显矛盾？（例如恐贪指数极度贪婪但估值极低，通常不会同时出现）
2. **数据新鲜度/可信度**：哪些数据看起来可能已经失效或长期未更新？
3. **功能风险**：基于测试结果，哪些功能模块对用户的判断可能产生误导？
4. **综合健康评分**：给本次审计打 0-100 分（100=完全健康），并说明扣分原因。

输出格式（严格 JSON，不要 markdown 代码块）：
{{
  "issues": [
    {{"severity": "high|medium|low", "category": "数据一致性|数据新鲜度|功能风险|配置问题", "description": "问题描述", "suggestion": "建议"}}
  ],
  "health_score": 85,
  "summary": "一句话总结（50字以内）",
  "needs_attention": true
}}"""

    try:
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()
        llm_result = gw.call_sync(
            prompt,
            system="",
            model_tier="llm_heavy",
            user_id="",
            module="self_audit",
            max_tokens=1000,
        )
        if not llm_result.get("fallback") and llm_result.get("content"):
            content = llm_result["content"].strip()
            # 去掉可能的 markdown 代码块包裹
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            parsed = json.loads(content)
            _log(f"  LLM 审计完成: score={parsed.get('health_score')}, issues={len(parsed.get('issues', []))}")
            return {"available": True, **parsed}
        else:
            _log(f"  LLM gateway fallback: {llm_result.get('source')}")
    except json.JSONDecodeError as e:
        _log(f"  LLM 输出 JSON 解析失败: {e}")
    except Exception as e:
        _log(f"  LLM 审计失败: {e}")
        traceback.print_exc()

    # LLM 调用失败时，基于探针/冒烟结果计算兜底分
    all_items = probe_results + smoke_results
    fail_c = sum(1 for r in all_items if r["status"] == "fail")
    warn_c = sum(1 for r in all_items if r["status"] == "warn")
    fallback_score = max(0, 100 - fail_c * 10 - warn_c * 3)
    return {"available": False, "analysis": "", "issues": [], "health_score": fallback_score}


# ============================================================
# 4. 汇总 & 落盘
# ============================================================

def _overall_status(probe: list[dict[str, Any]], smoke: list[dict[str, Any]], llm: dict[str, Any]) -> str:
    all_items = probe + smoke
    fail_count = sum(1 for r in all_items if r["status"] == "fail")
    warn_count = sum(1 for r in all_items if r["status"] == "warn")
    high_issues = sum(1 for i in llm.get("issues", []) if i.get("severity") == "high")

    if fail_count >= 3 or high_issues >= 2:
        return "critical"
    if fail_count >= 1 or warn_count >= 3 or high_issues >= 1:
        return "warning"
    return "healthy"


def save_audit_report(report: dict[str, Any]) -> None:
    """写 latest.json 和带日期的历史文件"""
    AUDIT_LATEST.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    hist_file = AUDIT_HISTORY_DIR / f"{date.today()}.json"
    hist_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _log(f"报告已写入: {AUDIT_LATEST}")


# ============================================================
# 5. 企微推送
# ============================================================

def push_audit_report(report: dict[str, Any]) -> None:
    """把审计摘要推企微（同一天只推一次，防止重复触发）"""
    try:
        from services.wxwork_push import is_configured, send_daily_report_to
        if not is_configured():
            _log("企微未配置，跳过推送")
            return

        # ── 去重：当天已推送过则跳过 ──
        report_date = report.get("date", "")
        if AUDIT_LATEST.exists():
            try:
                existing = json.loads(AUDIT_LATEST.read_text(encoding="utf-8"))
                if existing.get("pushed_date") == report_date:
                    _log(f"今天 ({report_date}) 已推送过，跳过重复推送")
                    return
            except Exception:
                pass

        overall = report["overall_status"]
        score = report["llm_audit"].get("health_score", "N/A")
        summary = report["llm_audit"].get("summary", "")
        issues = report["llm_audit"].get("issues", [])

        emoji = {"healthy": "✅", "warning": "⚠️", "critical": "🔴"}.get(overall, "❓")
        title = f"{emoji} 钱袋子周度自检报告 {report['date']}"

        # 统计
        probe_fail = sum(1 for r in report["probe_results"] if r["status"] == "fail")
        probe_warn = sum(1 for r in report["probe_results"] if r["status"] == "warn")
        smoke_fail = sum(1 for r in report["smoke_results"] if r["status"] == "fail")

        lines = [
            title,
            f"",
            f"健康评分: {score}/100",
            f"数据探针: {len(report['probe_results'])}项，{probe_fail}失败 {probe_warn}警告",
            f"功能测试: {len(report['smoke_results'])}项，{smoke_fail}失败",
            f"",
        ]

        if summary:
            lines += [f"总结: {summary}", ""]

        # 高优先级问题
        high_issues = [i for i in issues if i.get("severity") == "high"]
        if high_issues:
            lines.append("🔴 高优先级问题:")
            for i in high_issues[:3]:
                lines.append(f"  • {i.get('description', '')}")
                lines.append(f"    建议: {i.get('suggestion', '')}")
            lines.append("")

        # 失败的探针
        failed_probes = [r for r in report["probe_results"] if r["status"] == "fail"]
        if failed_probes:
            lines.append("❌ 失败数据源:")
            for r in failed_probes[:5]:
                lines.append(f"  • {r['name']}: {r['issue'][:60]}")
            lines.append("")

        lines.append(f"详情: 打开钱袋子 → 首页 banner 查看完整报告")

        msg = "\n".join(lines)
        send_daily_report_to("LeiJiang", msg)
        # 记录推送日期，防止当天重复推送
        try:
            data = json.loads(AUDIT_LATEST.read_text(encoding="utf-8"))
            data["pushed_date"] = report_date
            AUDIT_LATEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        _log("企微推送完成")
    except Exception as e:
        _log(f"企微推送失败: {e}")


# ============================================================
# 主入口
# ============================================================

def _auto_banner_msg(probe: list[dict[str, Any]], smoke: list[dict[str, Any]]) -> str:
    """LLM 摘要缺失时，自动生成规则摘要作为 banner_message"""
    fails = [r["name"] for r in probe + smoke if r["status"] == "fail"]
    warns = [r["name"] for r in probe + smoke if r["status"] == "warn"]
    parts = []
    if fails:
        parts.append(f"失败: {', '.join(fails[:3])}")
    if warns:
        parts.append(f"警告: {', '.join(warns[:3])}")
    return "；".join(parts) if parts else ""


def run_weekly_audit() -> dict[str, Any]:
    """
    执行完整周度自检，返回审计报告 dict。
    预计耗时：2-5 分钟（含 LLM 调用）。
    """
    _log("=" * 50)
    _log(f"周度自检启动 {date.today()} {datetime.now().strftime('%H:%M')}")
    _log("=" * 50)

    start_time = time.time()

    # Step 1: 数据源探针
    probe_results = run_data_probes()

    # Step 2: 功能冒烟
    smoke_results = run_smoke_tests()

    # Step 3: LLM 语义审计
    llm_result = run_llm_audit(probe_results, smoke_results)

    # Step 4: 组装报告
    overall = _overall_status(probe_results, smoke_results, llm_result)
    elapsed = int(time.time() - start_time)

    report = {
        "date": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(),
        "elapsed_seconds": elapsed,
        "overall_status": overall,         # "healthy" | "warning" | "critical"
        "probe_results": probe_results,
        "smoke_results": smoke_results,
        "llm_audit": llm_result,
        "stats": {
            "probe_pass": sum(1 for r in probe_results if r["status"] == "pass"),
            "probe_warn": sum(1 for r in probe_results if r["status"] == "warn"),
            "probe_fail": sum(1 for r in probe_results if r["status"] == "fail"),
            "smoke_pass": sum(1 for r in smoke_results if r["status"] == "pass"),
            "smoke_warn": sum(1 for r in smoke_results if r["status"] == "warn"),
            "smoke_fail": sum(1 for r in smoke_results if r["status"] == "fail"),
            "health_score": llm_result.get("health_score", -1),
            "llm_issues_high": sum(1 for i in llm_result.get("issues", []) if i.get("severity") == "high"),
            "llm_issues_total": len(llm_result.get("issues", [])),
        },
        # 前端 banner 直接用这两个字段
        "banner_title": {
            "healthy": "✅ 本周自检通过",
            "warning": "⚠️ 自检发现异常",
            "critical": "🔴 自检发现严重问题",
        }.get(overall, "❓ 自检完成"),
        "banner_message": llm_result.get("summary") or _auto_banner_msg(probe_results, smoke_results),
        "read": False,   # 前端读取后置 True（通过 /api/audit/mark-read 接口）
    }

    # Step 5: 落盘
    save_audit_report(report)

    # Step 6: 企微推送
    push_audit_report(report)

    _log(f"周度自检完成: overall={overall}, score={llm_result.get('health_score')}, 耗时{elapsed}s")
    return report


if __name__ == "__main__":
    run_weekly_audit()
