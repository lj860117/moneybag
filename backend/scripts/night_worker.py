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
    """调用 DeepSeek V4 Pro（通过 gateway 统一管理）

    晨报场景默认走 V4 Pro：5/23 永久降价后比 Flash 只贵一点，但幻觉更少、质量更好
    如果 DeepSeek 挂掉，gateway 会自动降级到通义千问3.6 Plus

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
            model_tier="llm_heavy",  # 改为 V4 Pro（晨报需要更高质量+低幻觉）
            user_id="",
            module="night_worker",
            max_tokens=max_tokens,
        )
        # v9.5.66: 追踪本次调用用了哪个模型（用于晨报底部显示来源）
        # ⚠️ 网关返回的 key 是 "fallback"，不是 "fallback_used"
        _track_model_usage(result.get("model", ""), result.get("fallback", False))
        # v9.5.127: 只检查 content 是否为空，fallback=True 不影响（降级成功时 content 有内容）
        if not result.get("content"):
            log(f"  V3 gateway 返回空: {result.get('source', 'unknown')}")
            return ""
        return _clean_llm_output(result["content"])
    except Exception as e:
        log(f"  V3 调用失败: {e}")
    return ""


# v9.5.66: 模型使用追踪（晨报/企微推送结尾标注真实模型来源）
_MODEL_USAGE_STATS = {"calls": 0, "models": {}, "any_fallback": False}

def _track_model_usage(model: str, fallback_used: bool):
    """记录一次 LLM 调用用了什么模型"""
    if not model:
        return
    _MODEL_USAGE_STATS["calls"] += 1
    _MODEL_USAGE_STATS["models"][model] = _MODEL_USAGE_STATS["models"].get(model, 0) + 1
    if fallback_used:
        _MODEL_USAGE_STATS["any_fallback"] = True

def _reset_model_stats():
    """新一轮晨报开始前重置"""
    global _MODEL_USAGE_STATS
    _MODEL_USAGE_STATS = {"calls": 0, "models": {}, "any_fallback": False}

def _format_model_display_name(model: str) -> str:
    """模型 ID → 中文展示名"""
    lc = (model or "").lower()
    if "qwen3.6-plus" in lc or "qwen-plus" in lc: return "通义千问 Plus"
    if "qwen3.6-flash" in lc or "qwen-flash" in lc: return "通义千问 Flash"
    if "qwen" in lc: return "通义千问"
    if "seed-2-0-pro" in lc or "seed-2.0-pro" in lc: return "豆包 Seed 2.0 Pro"
    if "seed-2-0-lite" in lc or "seed-2.0-lite" in lc: return "豆包 Seed 2.0 Lite"
    if "seed-2-0-mini" in lc or "seed-2.0-mini" in lc: return "豆包 Seed 2.0 Mini"
    if "doubao-seed-1-6" in lc or "seed-1-6" in lc or "seed-1.6" in lc: return "豆包 Seed 1.6"
    if "doubao-1-5-pro" in lc or "doubao-pro" in lc: return "豆包 Pro"
    if "doubao-1-5-lite" in lc or "doubao-lite" in lc: return "豆包 Lite"
    if "doubao" in lc or lc.startswith("ep-"): return "豆包"
    if "reasoner" in lc: return "DeepSeek R1"
    if "v4-pro" in lc or "deepseek-v4-pro" in lc: return "DeepSeek V4 Pro"
    if "v4-flash" in lc or "deepseek-v4-flash" in lc: return "DeepSeek V4 Flash"
    if "deepseek" in lc: return "DeepSeek"
    return model or "AI"

def _build_model_usage_footer() -> str:
    """构建晨报底部的模型来源行"""
    stats = _MODEL_USAGE_STATS
    if not stats["calls"]:
        return ""
    # 按调用次数排序
    items = sorted(stats["models"].items(), key=lambda x: -x[1])
    parts = [f"{_format_model_display_name(m)}×{cnt}" for m, cnt in items]
    detail = " · ".join(parts)
    if stats["any_fallback"]:
        # v9.5.69: 降级目标可能是豆包或千问，根据实际调用的模型动态展示
        fallback_models = [m for m in stats["models"] if not m.startswith("deepseek")]
        if fallback_models:
            fb_names = [_format_model_display_name(m) for m in fallback_models]
            fb_label = "/".join(sorted(set(fb_names)))
        else:
            fb_label = "降级模型"
        return f"\n\n---\n🔄 **本报告含{fb_label}降级输出（DeepSeek 部分不可用）** · 调用：{detail} · {stats['calls']} 次"
    else:
        # 全部 DeepSeek，简洁标识
        return f"\n\n---\n🤖 模型：{detail}"


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

    # 2.【v9.5.125 激进清理】如果文本以泄露关键词开头 → 直接截取有效内容
    leaky_keywords = (
        '我们需要', '我们被要求', '需要输出', '注意铁律', '注意：',
        '数据：', '资金面', '需要注意的是', '禁止', '不能编造',
        '只能推测', '用最简单', '还要禁止', '每句话必须',
        '输出要求',
    )
    if text.startswith(leaky_keywords):
        # 优先找结构化标记
        for m in ('一句话：', '一句话:', '看点：', '建议：'):
            idx = text.find(m)
            if 0 < idx < 200:
                text = text[idx:]
                break
        else:
            # 降级: 找第一个句号后内容
            idx = text.find('。')
            if 0 < idx < 150 and len(text[idx+1:].strip()) > 20:
                text = text[idx+1:].strip()

    # 3. 正则去除多行推理前缀
    reasoning_prefixes = [
        r'^我们基于.*?进行分析。.*?(?=\n\n|\n[一二三四五六七八九十\d【\[])',
        r'^(?:数据包括|数据分析|根据提供的数据)[:：].*?(?=\n\n|\n[一二三四五六七八九十\d【\[])',
        r'^(?:要求|注意|说明)[:：].*?(?=\n\n|\n[一二三四五六七八九十\d【\[])',
        r'^需要输出.*?(?=\n\n|\n[一二三四五六七八九十\d【\[一句话])',
        r'^注意铁律.*?(?=\n\n|\n[一二三四五六七八九十\d【\[一句话])',
    ]
    for pattern in reasoning_prefixes:
        text = re.sub(pattern, '', text, flags=re.DOTALL).strip()

    # 4. 去除行内的元注释（括号内的自言自语）
    #    例如："极高（注意：这里严重度是0/5但标题说极高，可能矛盾？...）"
    text = re.sub(r'（[^）]*?(?:可能矛盾|按原文|我们按|注意：)[^）]*?）', '', text)

    return text.strip()


def _filter_prompt_leak(text: str) -> str:
    """v9.5.44 过滤 prompt 泄漏 — 委托公共守卫模块

    原 v9.5.43 实现已迁移到 backend/services/llm_output_guard.py。
    此函数保留作向后兼容包装（本文件其他地方调用此名）。
    新代码请直接 from services.llm_output_guard import LLMOutputGuard
    """
    try:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from services.llm_output_guard import LLMOutputGuard
        return LLMOutputGuard.filter_diagnosis(text)
    except Exception as e:
        # 极端情况：公共模块不可用，降级到简单过滤
        print(f"[NIGHT_WORKER] llm_output_guard import failed, using simple filter: {e}")
        if not text:
            return text
        import re
        simple_keywords = ['我们被要求', '持仓列表是', '方括号', '150字以内', '输出要求', '你是投资组合诊断师']
        lines = [l for l in text.split('\n') if not any(kw in l for kw in simple_keywords)]
        result = '\n'.join(lines).strip()
        return result if len(result) >= 30 else "（AI 诊断输出异常，已过滤。建议手动查看持仓页详情）"


def _truncate_at_sentence(text: str, max_chars: int = 3800) -> str:
    """在句子边界截断文本，避免断句

    企微文本消息限制 4096 字符，默认 3800 留余量给 emoji 编码膨胀。
    在最后一个完整句子处截断（。！？\\n），附加省略提示。
    """
    if len(text) <= max_chars:
        return text
    # 在 max_chars 范围内找最后一个句子结束符
    truncated = text[:max_chars]
    # 从末尾向前找句子边界
    for i in range(len(truncated) - 1, max(len(truncated) - 200, 0), -1):
        if truncated[i] in '。！？\n':
            return truncated[:i + 1] + "\n\n...（完整内容请打开钱袋子查看）"
    # 找不到句子边界，至少在空格/逗号处截断
    for i in range(len(truncated) - 1, max(len(truncated) - 100, 0), -1):
        if truncated[i] in '，、 ':
            return truncated[:i] + "...（完整内容请打开钱袋子查看）"
    return truncated + "..."


def _user_has_account(user_id: str) -> bool:
    """检查用户是否已注册/登录（即用户数据是否存在）

    未注册的用户不应收到晨报推送，避免打扰。
    判断标准（兼容新旧格式）：
    - 旧格式：data/users/SHA256(userId)[:16].json 文件存在
    - 新格式：data/users/SHA256(userId)[:16]/ 目录存在
    """
    import hashlib
    from config import USERS_DIR
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    user_file = USERS_DIR / f"{safe_id}.json"
    user_dir = USERS_DIR / safe_id
    # 兼容新旧格式：.json 文件或目录任一存在即认为已注册
    return user_file.exists() or user_dir.exists()


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
        from services.monthly_snapshot import save_all_users_snapshots  # FIX: 函数名是复数 snapshots
        count = save_all_users_snapshots()
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


def _preheat_alloc_cache():
    """v9.5.11: 规则引擎版预热（0 LLM 调用，5档×3偏好=15次，毫秒级完成）
    排行刷新后调用，确保规则结果可立即返回（fund_screen 内部已带缓存）
    """
    log("🔥 预热测评推荐缓存（规则引擎，无 LLM）...")
    from services.portfolio import get_recommend_allocations
    risk_profiles = ["保守型", "稳健型", "平衡型", "进取型", "激进型"]
    preferences = ["fund", "stock", "mixed"]
    success = 0
    for rp in risk_profiles:
        for pref in preferences:
            try:
                result = get_recommend_allocations(rp, with_ai=False, preference=pref)
                if result.get("allocations"):
                    success += 1
                    log(f"  ✅ {rp}/{pref}: 规则引擎选出 {len(result['allocations'])} 项")
            except Exception as e:
                log(f"  ❌ {rp}/{pref}: {e}")
    log(f"  📊 预热结果: {success}/{len(risk_profiles)*len(preferences)} 项完成")


def step_fund_rank_refresh():
    """基金排行榜定期刷新 — 每 3 天自动执行一次

    fund_rank_ts.json 用于晨报基金推荐，过旧会被 72h 时效检查跳过。
    每周一/周四凌晨自动刷新，保证数据不超过 3 天。
    """
    # 仅在周一和周四执行（一周两次，覆盖 72h 时效）
    if date.today().weekday() not in (0, 3):  # 0=周一, 3=周四
        return

    log("📊 01:45 基金排行榜刷新")
    try:
        from scripts.fund_rank_build import build_rank
        code = build_rank()
        if code == 0:
            log("  ✅ 基金排行榜刷新成功")
            # 排行刷新后顺带预热测评推荐缓存（后台异步，不阻塞主流程）
            try:
                _preheat_alloc_cache()
            except Exception as e:
                log(f"  ⚠️ 测评缓存预热失败: {e}")
        else:
            log(f"  ⚠️ 基金排行榜刷新失败 (code={code})")
    except Exception as e:
        log(f"  ❌ 基金排行榜刷新异常: {e}")


# ============================================================
# 北向资金：诚实降级（净流入不可得，只用成交额）
#
# 背景：2024-08-19 起沪深交易所停止披露北向「日频净买入」，改为按
# 季度公布。Tushare moneyflow_hsgt 的 north_money/hgt/sgt 自此填的
# 是【当日成交额】（百万元），不是历史累计净买入。旧代码误按"累计值"
# 对其做相邻两日差分求净流入，N 日累计退化成首尾两天之差，得到的
# 今日/5日/20日净流入全是噪声（20日 -759.8 亿 vs 5日 +100.3 亿，
# 符号相反），却被当作"外资大幅流入"喂给了 LLM。
#
# 因此本文件的处理原则：
#   1. 净流入维度（net_flow_today/5d/20d）一律不进 prompt、不展示
#   2. 只使用真实可得的成交额/活跃度维度（turnover_*）
#   3. 必须向 LLM 显式声明数据边界，并禁止其据成交额推断资金方向
#
# 判断"能不能用净流入"看 net_flow_available（净流入维度），
# 不要看 available（那只代表数据源整体拿到了成交额）。
# ============================================================

NORTH_NET_FLOW_NOTE: str = (
    "日频净流入自2024-08-19起沪深交易所已停止披露（改为按季度公布），本次分析无此数据"
)


def _safe_num(value, digits: int = 0, default: str = "?") -> str:
    """安全格式化数字为字符串。

    net_flow_* 从数字变成 None 后，任何 f"{x:+.1f}" / abs(x) 都会抛
    TypeError，所有北向相关数值一律走这个函数。

    Args:
        value: 待格式化的值，可能是 None / str / int / float。
        digits: 小数位数，默认 0。
        default: 无法格式化时的占位符，默认 "?"。

    Returns:
        格式化后的字符串，None 或非数字返回 default。
    """
    if value is None or isinstance(value, bool):
        return default
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return default


def _north_date_label(north: dict) -> str:
    """把 data_date（YYYYMMDD）转成 M/D 短标签，缺失返回「最新」。"""
    dd = str(north.get("data_date") or "")
    if len(dd) == 8 and dd.isdigit():
        try:
            return f"{int(dd[4:6])}/{int(dd[6:8])}"
        except (TypeError, ValueError):
            return "最新"
    return "最新"


def _north_llm_text(north: dict) -> str:
    """构造喂给 LLM 的北向资金描述（显式声明数据边界，禁止推断方向）。

    Args:
        north: get_northbound_flow() 返回的字典（可能为空/旧结构）。

    Returns:
        一行中文描述，绝不包含伪造的净流入数字与流入/流出方向判断。
    """
    north = north if isinstance(north, dict) else {}

    # 成交额维度是否可用（数据源整体可用且拿到了成交额）
    turnover = north.get("turnover_today")
    has_turnover = turnover is not None and bool(north.get("available")) and not north.get("stale")

    if not has_turnover:
        return f"数据不可得（{NORTH_NET_FLOW_NOTE}；本次也未取到北向成交额）。请勿推断外资流入/流出方向。"

    parts = [f"{NORTH_NET_FLOW_NOTE}"]
    parts.append(
        f"仅提供北向成交额{_safe_num(turnover, 0)}亿元（{_north_date_label(north)}）"
    )

    avg5 = north.get("turnover_avg_5d")
    avg20 = north.get("turnover_avg_20d")
    t_trend = north.get("turnover_trend") or "平稳"
    if avg5 is not None:
        rng = north.get("turnover_5d_range") or ""
        rng_str = f"，{rng}" if rng else ""
        parts.append(f"近5日日均{_safe_num(avg5, 0)}亿元{rng_str}")
    if avg20 is not None:
        parts.append(f"近20日日均{_safe_num(avg20, 0)}亿元")
    parts.append(f"近5日相对近20日为「{t_trend}」")

    return (
        "；".join(parts)
        + "。成交额只反映交易活跃度（买卖双边合计），不含方向信息，"
        + "请勿据此推断外资流入/流出方向，也不要给出任何北向净买入/净卖出的数字或结论。"
    )


def _north_user_text(north: dict) -> str:
    """构造给普通用户看的北向资金描述（早安简报/市场温度用）。"""
    north = north if isinstance(north, dict) else {}
    turnover = north.get("turnover_today")
    has_turnover = turnover is not None and bool(north.get("available")) and not north.get("stale")

    if not has_turnover:
        return "外资动向: 数据暂不可用（交易所已停止披露北向日频净买入）"

    t_trend = north.get("turnover_trend") or "平稳"
    txt = f"外资交投: {_north_date_label(north)}北向成交{_safe_num(turnover, 0)}亿（{t_trend}）"
    avg5 = north.get("turnover_avg_5d")
    if avg5 is not None:
        txt += f"，近5日日均{_safe_num(avg5, 0)}亿"
    txt += "；净买入方向数据交易所已停止披露（改按季度公布）"
    return txt


# ============================================================
# 02:00 R1 Phase 1: 宏观+地缘+行业
# ============================================================

def step_r1_phase1():
    log("🧠 02:00 R1 Phase 1: 全局市场分析")
    results = {}

    # 逐个收集数据（单个失败不影响其他）
    fgi = {"score": 50, "level": "中性"}
    val = {"percentile": 50, "level": ""}
    # 北向：净流入维度已不可得（交易所停止披露），默认值也必须诚实降级
    north = {
        "net_flow_today": None,
        "net_flow_5d": None,
        "net_flow_20d": None,
        "net_flow_available": False,
        "unavailable_reason": NORTH_NET_FLOW_NOTE,
        "trend": "数据不可得",
        "turnover_today": None,
        "turnover_trend": "",
        "available": False,
    }
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
        # 只 log 成交额（净流入已不可得，log 数字会误导排障的人）
        log(f"  北向成交额={_safe_num(north.get('turnover_today'), 0)}亿"
            f"({north.get('turnover_trend') or '—'}), 净流入=不可得, "
            f"SHIBOR={shibor.get('overnight', 0)}")
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

    # 获取前一交易日 A 股三大指数涨跌（防止 LLM 在大盘下跌时误判"亮眼"）
    indices = {}
    try:
        from services.tushare_data import is_configured as ts_ok, get_index_daily
        if ts_ok():
            for code, name in [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"), ("399006.SZ", "创业板指")]:
                rows = get_index_daily(code, days=5)
                if rows and len(rows) >= 2:
                    latest = rows[-1]
                    prev = rows[-2]
                    chg_pct = (latest['close'] - prev['close']) / prev['close'] * 100
                    indices[code] = {"name": name, "close": latest['close'], "change_pct": round(chg_pct, 2)}
                    time.sleep(0.2)  # Tushare 限频
            if indices:
                idx_str = ", ".join(f"{v['name']}{v['change_pct']:+.2f}%" for v in indices.values())
                log(f"  A股指数: {idx_str}")
    except Exception as e:
        log(f"  ⚠️ 指数数据失败: {e}")

    # 保存原始数据供 step_generate_products 使用
    results["raw"] = {
        "fgi": fgi, "val": val, "north": north,
        "geo": geo, "sr": sr, "br": br,
        "margin": margin, "shibor": shibor,
        "indices": indices,
    }

    # 收集新闻
    news_titles = []
    try:
        from services.news_data import get_market_news
        news = get_market_news(limit=10)  # 多拉一些，去重后留5条
        raw_titles = [n.get("title", "") for n in news if n.get("title")]
        # 去重：排除相似度过高的标题（同词颠倒/重复）
        deduped = []
        for t in raw_titles:
            t_chars = set(t.replace('，','').replace('、','').replace(' ',''))
            is_dup = False
            for existing in deduped:
                ex_chars = set(existing.replace('，','').replace('、','').replace(' ',''))
                overlap = len(t_chars & ex_chars) / max(len(t_chars | ex_chars), 1)
                if overlap > 0.7:  # 70%字符重叠则视为重复
                    is_dup = True
                    break
            if not is_dup:
                deduped.append(t)
        news_titles = deduped[:5]
        results["news"] = news_titles
        log(f"  新闻: {len(raw_titles)}条 → 去重后{len(news_titles)}条")
    except Exception as e:
        log(f"  ⚠️ 新闻获取失败: {e}")

    # 拼接数据文本用于 LLM 研判（全中文，防止 LLM 照搬英文术语）
    sector_text = ','.join([s.get('name', '') for s in sr.get('top_gainers', [])[:5]]) if sr.get('available') else '暂无'
    _geo_level_map = {"low": "低", "normal": "低", "moderate": "中等",
                      "elevated": "偏高", "high": "高", "extreme": "极高", "critical": "极高"}
    geo_cn_for_llm = _geo_level_map.get(geo.get('level', ''), geo.get('level', '低'))
    # 北向资金：净流入自 2024-08-19 起交易所停止披露，只喂成交活跃度并声明边界
    north_text = _north_llm_text(north)
    # A 股指数涨跌（关键：让 LLM 知道大盘方向，避免在下跌日误判"亮眼"）
    indices_text = ", ".join(f"{v['name']}{v['change_pct']:+.2f}%" for v in indices.values()) if indices else "暂无"
    data_text = f"""宏观数据快照:
- A股指数(前一交易日): {indices_text}
- 恐贪指数: {fgi.get('score', 50)} ({fgi.get('level', '中性')})
- 估值百分位: {val.get('percentile', 50)}% ({val.get('level', '')})
- 北向资金: {north_text}
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
5. 用最简单的中文，不要金融术语和英文
6. 【关键】禁止输出你的思考链/推理过程/分析过程，包括：
   - 不要输出"我们被要求...""需要检查...""注意铁律..."等自言自语
   - 不要输出你的分析思路、数据解读过程
   - 直接给结论，一句话开头
7. 禁止输出口水话和废话：如"情绪不冷不热""市场有所波动""整体表现平稳"等无信息量表达
8. 每句话必须有具体信息：数字、方向或可执行建议，否则删掉这句
9. 【北向资金铁律】沪深交易所自2024-08-19起已停止披露北向日频净买入（改按季度公布），
   所以你拿不到任何"外资净流入/净流出多少亿"的数据。绝对禁止说外资在买入/卖出/流入/流出/
   加仓/撤退，也禁止给出任何外资净买入金额。数据里若给了北向成交额，那是买卖双边合计的
   交易活跃度，只能说"外资交投活跃/清淡"，不能推断方向。
10. 违反以上规则等于失职

输出格式示例（直接给结论，不要前缀）：
一句话：今天市场涨得不错，但估值偏高
看点：
- 银行间利率偏紧，短期钱不太松
- 好消息是市场整体不贵，跌不深
建议：不用慌，持有为主"""

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
一句话：今天盘面偏弱但没大问题，杠杆资金还稳。
看点：
- 融资余额小幅减少，加杠杆的人在收手
- 地缘局势让避险资金往银行、黄金跑
- 好消息是市场整体不贵，跌不深
建议：不用慌，持有为主。如果情绪很恐慌了再考虑捡便宜。

重要: 上述数据就是全部信息。如果某个维度数据缺失，直接跳过不提。
北向资金：数据里已说明日频净流入不可得，禁止写"外资流入/流出多少亿"，也禁止用成交额推断方向。
禁止: 不要写"我来分析"之类的前缀，直接从"一句话："开始。"""

    fallback_macro = (
        f"市场情绪{fgi.get('level', '中性')}({fgi.get('score', 50)}分), "
        f"北向成交{_safe_num(north.get('turnover_today'), 0)}亿"
        f"({north.get('turnover_trend') or '净买入方向数据已停止披露'}), "
        f"热点:{sector_text}"
    )
    analysis = _call_v3(prompt, 800, system=ANTI_HALLUCINATION_SYSTEM)
    if analysis:
        # v9.5.124: 过滤 prompt 泄漏和思考链
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from services.llm_output_guard import LLMOutputGuard
            analysis = LLMOutputGuard.filter_analysis(analysis, fallback=fallback_macro)
        except Exception as e:
            log(f"  ⚠️ LLMOutputGuard 不可用，降级到 _clean_llm_output: {e}")
            analysis = _clean_llm_output(analysis)
            if any(kw in analysis for kw in ('用户让我', '用户提供了', '现在分析数据', '深层需求', '铁律要求')):
                analysis = fallback_macro
        results["macro_analysis"] = analysis
        log(f"  ✅ 宏观研判: {len(analysis)}字")
    else:
        # LLM 不可用，生成纯数据版研判
        results["macro_analysis"] = fallback_macro
        log(f"  ⚠️ LLM 不可用，使用纯数据版")

    return results


# ============================================================
# v9.5.76: 组合温度计 — 纯计算，不调 LLM
# ============================================================

# v9.5.76: 理想配置（用户定投方案，暂硬编码，后续存 profile）
# S&P500 30% / 中证A500 25% / 纳指100 20% / 红利低波 15% / 沪深300 10%
_IDEAL_ALLOCATION = {
    "美股指数(S&P500/纳指)": 50,   # 合并纳指100到美股桶（30+20）
    "A股宽基指数": 25,              # 中证A500 / 沪深300（25+10）
    "红利低波": 15,
    "主动混合/科技成长": 0,         # 当前持仓全是主动型，不在理想配置里
    "其他": 10,
}

# 基金资产类别映射（LeiJiang + BuLuoGeLi 持仓，可按需追加）
_FUND_CATEGORY_MAP = {
    # LeiJiang 持仓
    "006555": "QDII科技成长",        # 浦银安盛全球智能科技(QDII)
    "002163": "A股主动混合",         # 东方惠新灵活配置混合C
    "005851": "A股主动混合",         # 财通新视野灵活配置混合A
    "008984": "A股主动混合/科技",    # 财通科技创新混合C
    "013107": "A股主动混合/制造",    # 华夏先进制造龙头混合A
    "016501": "A股主动混合/半导体",  # 华夏半导体龙头混合C
    "007356": "A股主动混合/科技",    # 汇添富科技创新混合C
    "005698": "QDII科技成长",        # 华夏全球科技先锋混合(QDII)
    # BuLuoGeLi 持仓
    "100038": "A股宽基指数",         # 富国沪深300指数增强A（主要持仓A股宽基）
    "163406": "A股主动混合",         # 兴全合润混合A（A股灵活配置主动）
    "009708": "A股主动混合/制造",    # 工银新兴制造混合C
}


def _build_rebalance_gap(uid: str, holdings_with_val: list) -> str:
    """v9.5.76: 再平衡缺口 — 当前持仓结构 vs 理想配置

    holdings_with_val: [{"code", "name", "cur_val", "float_pct"}, ...]
    返回格式化文本，供晨报展示
    """
    if not holdings_with_val:
        return ""

    total_val = sum(h.get("cur_val", 0) for h in holdings_with_val)
    if total_val == 0:
        return ""

    # 理想目标（你的定投方案）
    IDEAL = {
        "S&P500/纳指(QDII美股)": 50,   # 30%+20%
        "A股宽基/价值": 35,             # A500 25% + 沪深300 10%
        "红利低波": 15,
    }

    # 当前持仓分类聚合（用 _FUND_CATEGORY_MAP）
    BUCKET_MAP = {
        "QDII科技成长": "S&P500/纳指(QDII美股)",
        "A股宽基指数": "A股宽基/价值",        # 100038 富国沪深300增强
        "A股主动混合": "其他/主动混合",       # 主动型不归入宽基桶
        "A股主动混合/科技": "其他/主动混合",
        "A股主动混合/半导体": "其他/主动混合",
        "A股主动混合/制造": "其他/主动混合",
    }
    actual = {}
    unmatched_val = 0.0
    for h in holdings_with_val:
        code = h.get("code", "")
        cat = _FUND_CATEGORY_MAP.get(code, "其他")
        bucket = BUCKET_MAP.get(cat, "其他/主动混合")
        if bucket not in actual:
            actual[bucket] = 0.0
        actual[bucket] += h.get("cur_val", 0)

    # 计算缺口
    lines = ["⚖️ 再平衡缺口（当前结构 vs 你的定投目标）"]
    lines.append(f"当前总市值 ¥{total_val:.0f}")
    lines.append("")

    has_gap = False
    for bucket, ideal_pct in IDEAL.items():
        cur_val_bucket = actual.get(bucket, 0)
        cur_pct = cur_val_bucket / total_val * 100 if total_val > 0 else 0
        gap_pct = cur_pct - ideal_pct
        gap_val = (cur_pct - ideal_pct) * total_val / 100

        if abs(gap_pct) >= 3:  # 只显示偏差 ≥3% 的
            has_gap = True
            if gap_pct > 0:
                lines.append(f"  ⬆ {bucket}: 超配 +{gap_pct:.0f}%（目标{ideal_pct}% 实际{cur_pct:.0f}%），可减¥{gap_val:.0f}")
            else:
                lines.append(f"  ⬇ {bucket}: 欠配 {gap_pct:.0f}%（目标{ideal_pct}% 实际{cur_pct:.0f}%），需补¥{abs(gap_val):.0f}")
        else:
            lines.append(f"  ✓ {bucket}: 目标{ideal_pct}% 实际{cur_pct:.0f}%（±{abs(gap_pct):.0f}%，在范围内）")

    # 其他（当前主动混合）
    other_val = actual.get("其他/主动混合", 0)
    other_pct = other_val / total_val * 100 if total_val > 0 else 0
    if other_pct > 5:
        lines.append(f"  ⚠ 其他(主动混合): {other_pct:.0f}%（不在理想配置中，可逐步迁移到指数型）")

    if not has_gap:
        lines.append("  配置基本均衡，无需调整")

    lines.append("")
    lines.append("⚠️ 当前持仓以主动混合基金为主，与目标指数基金配置存在结构性差异，建议逐步向目标靠拢。")
    return "\n".join(lines)


def _build_portfolio_thermometer(uid: str) -> str:
    """计算持仓浮盈/仓位快照，返回格式化文本供晨报和诊断使用。

    从 V4 transactions 读成本，从 fund_nav_history 读最新净值，纯算术，无 LLM。
    输出示例：
    📊 组合温度计（截至昨日收盘）
    总投入 ¥709  当前市值 ¥758  整体浮盈 +6.9%

    浮盈明细：
    • 浦银安盛全球智能科技(QDII)  买入 3.48 → 现 3.81  +9.5%  ¥108.6
    • 东方惠新灵活配置混合C        买入 2.59 → 现 2.92  +12.4% ¥112.4
    ...
    """
    import hashlib, json
    from pathlib import Path as _P

    try:
        safe = hashlib.sha256(uid.encode()).hexdigest()[:16]
        _users_dir = os.environ.get("USERS_DIR") or str(_P(os.environ.get("DATA_DIR", "./data")) / "users")
        ufile = _P(_users_dir) / f"{safe}.json"
        if not ufile.exists():
            return ""
        raw = json.loads(ufile.read_text())
        portfolio = raw.get("portfolio") or {}  # FIX: 防止 portfolio=null 时 .get() 报 NoneType
        txns = portfolio.get("transactions") or []  # FIX: transactions=null 时也安全
        if not txns:
            return ""

        # 聚合每只基金的买入成本（多次买入取加权均价）
        from collections import defaultdict
        holdings_map = defaultdict(lambda: {"name": "", "total_amount": 0.0, "total_shares": 0.0, "buy_navs": []})
        for t in txns:
            if t.get("type") != "BUY":
                continue
            code = t.get("code", "")
            if not code:
                continue
            h = holdings_map[code]
            h["name"] = t.get("name", code)
            amount = float(t.get("amount", 0) or 0)
            shares = float(t.get("shares", 0) or 0)
            nav = float(t.get("nav", 0) or 0)
            h["total_amount"] += amount
            h["total_shares"] += shares
            if nav > 0:
                h["buy_navs"].append((nav, shares))

        if not holdings_map:
            return ""

        # 拉最新净值
        from services.fund_monitor import get_fund_nav_history

        total_cost = 0.0
        total_val = 0.0
        rows = []
        for code, h in holdings_map.items():
            cost_amount = h["total_amount"]
            shares = h["total_shares"]
            # 加权平均买入净值
            if h["buy_navs"]:
                wt_nav = sum(n * s for n, s in h["buy_navs"]) / sum(s for _, s in h["buy_navs"])
            else:
                wt_nav = 0.0

            navs = get_fund_nav_history(code, days=3)
            cur_nav = navs[-1]["nav"] if navs and navs[-1].get("nav") else 0.0
            cur_val = cur_nav * shares if cur_nav > 0 else cost_amount
            float_pct = (cur_nav - wt_nav) / wt_nav * 100 if wt_nav > 0 and cur_nav > 0 else 0.0

            total_cost += cost_amount
            total_val += cur_val
            rows.append({
                "code": code, "name": h["name"],
                "wt_nav": wt_nav, "cur_nav": cur_nav,
                "cur_val": cur_val, "float_pct": float_pct,
                "cost": cost_amount,
            })

        if total_cost == 0:
            return ""

        overall_pct = (total_val - total_cost) / total_cost * 100
        overall_flag = "📈" if overall_pct >= 0 else "📉"
        lines = [
            f"📊 组合温度计（截至近日收盘）",
            f"总投入 ¥{total_cost:.0f}  当前市值 ¥{total_val:.0f}  整体浮盈 {overall_flag} {overall_pct:+.1f}%",
            "",
            "持仓明细："
        ]
        # 按浮盈率排序（最好→最差）
        rows.sort(key=lambda x: -x["float_pct"])
        for r in rows:
            arrow = "▲" if r["float_pct"] >= 0 else "▼"
            name_short = r["name"][:12] if r["name"] else r["code"]
            lines.append(
                f"  • {name_short}({r['code']})  "
                f"买入{r['wt_nav']:.3f} → 现{r['cur_nav']:.3f}  "
                f"{arrow}{abs(r['float_pct']):.1f}%  ¥{r['cur_val']:.1f}"
            )

        # v9.5.76: 追加再平衡缺口
        holdings_for_rebalance = [
            {"code": code, "name": h["name"], "cur_val": r["cur_val"], "float_pct": r["float_pct"]}
            for code, h in holdings_map.items()
            for r in [next((rr for rr in rows if rr["code"] == code), {})]
            if r
        ]
        rebalance_text = _build_rebalance_gap(uid, holdings_for_rebalance)
        if rebalance_text:
            lines.append("")
            lines += rebalance_text.split("\n")

        return "\n".join(lines)

    except Exception as e:
        log(f"  [温度计] 计算失败: {e}")
        return ""


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

            # 基金持仓：尝试拿 scan 数据（含盈亏），否则用基础信息
            fund_scan_data = {}
            try:
                from services.fund_monitor import scan_all_fund_holdings
                fund_scan = scan_all_fund_holdings(uid)
                fund_scan_data = {f["code"]: f for f in fund_scan.get("holdings", [])}
            except Exception:
                pass
            for f in funds[:5]:
                code = f.get('code', '')
                name_f = f.get('name', code)
                fd = fund_scan_data.get(code, {})
                pnl_pct = fd.get('pnlPct')
                invest_type = f.get('invest_type', '')
                pnl_str = f" 盈亏{pnl_pct:+.1f}%" if pnl_pct is not None else ""
                # v9.5.43 修复：invest_type 为空时不输出 []，避免晨报里出现「[]」噪点
                type_str = f" [{invest_type}]" if invest_type else ""
                holdings_text += f"  {name_f}({code}){pnl_str}{type_str}\n"

            # v9.5.76: 加组合温度计（浮盈/成本锚点），注入诊断 prompt
            thermometer_text = _build_portfolio_thermometer(uid)

            # v9.5.43 prompt 重构：避免 v4-flash 复读元指令
            # 原版包含「你是...」「请对以下...」「要求：1./2./3.」等结构化指令，模型偶尔会原文复述
            # 改为：纯数据 + 极简指令，禁止复述输入
            prompt = f"""持仓数据：
{holdings_text}
{f"{chr(10)}浮盈数据（真实成本vs当前净值）:{chr(10)}{thermometer_text}" if thermometer_text else ""}

输出要求（共3段，200字内）：
- 总评：组合风格 + 当前整体盈亏状态一句话
- 风险：哪几只基金/股票名称是集中风险，或浮盈较高需要注意止盈
- 建议：基于浮盈数据，针对具体持仓给出可执行操作（持有/加仓/减仓/观望）

只输出诊断结果，不要复述上面的指令或括号说明。"""

            diagnosis = _call_v3(prompt, 800,  # v9.5.123: 600→800(14只基金全名很长,600仍截断)
                                 system=f"""你是持仓诊断师。铁律：
1. 只能提及以下持仓中的基金/股票名称：{', '.join([f.get('name','') or f.get('code','') for f in funds[:10]])}，禁止编造或提及任何其他名称
2. 禁止复述任何指令或括号说明
3. 只输出总评/风险/建议三段，合计200字以内
4. 禁止使用"南嘉""远景""xxx智能"等未在列表中的基金名""")

            # v9.5.43 后处理：过滤 prompt 泄漏（即使 prompt 被复读也兜底）
            diagnosis = _filter_prompt_leak(diagnosis)

            # v9.5.43+ 重试一次：如果首次输出被过滤为降级，再调一次（v4-flash 偶发思考链截断）
            if "异常" in diagnosis and "建议手动查看" in diagnosis:
                log(f"  ⚠️ {name}: 首次输出异常，重试一次")
                retry_prompt = f"""{holdings_text}
直接输出三段诊断，不要思考过程：
总评：（一句话组合风格）
风险：（具体基金/股票名称的集中风险）
建议：（针对持仓的可执行操作）"""
                diagnosis2 = _call_v3(retry_prompt, 300,
                                      system="你是持仓诊断师。直接输出'总评/风险/建议'三段，每段一句话，禁止任何思考过程或前置说明。")
                diagnosis2 = _filter_prompt_leak(diagnosis2)
                if "异常" not in diagnosis2 or len(diagnosis2) > len(diagnosis):
                    diagnosis = diagnosis2
                    log(f"  ✅ {name}: 重试成功 {len(diagnosis2)}字")

            results[uid] = {"diagnosis": diagnosis, "stock_count": len(stocks), "fund_count": len(funds)}
            log(f"  ✅ {name}: {len(diagnosis)}字")

        except Exception as e:
            log(f"  ❌ {name}: {e}")

    # v9.5.124: 保存诊断缓存到文件（供 step_r1_phase3 读取）
    from pathlib import Path
    import json
    diag_cache_dir = Path(os.environ.get("DATA_DIR", "./data")) / "night_worker"
    diag_cache_dir.mkdir(parents=True, exist_ok=True)
    for uid, data in results.items():
        diag_file = diag_cache_dir / f"diagnosis_{uid}.json"
        diag_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  ✅ 诊断缓存已保存: {len(results)} 用户")

    return results


# ============================================================
# 03:00 R1 Phase 3: 推荐+决策
# ============================================================

def generate_trading_decision(diag: str, val_pct: float, fg_index: int) -> list:
    """v9.5.124: 简化版决策生成
    
    基于持仓诊断、市场估值、恐贪指数生成操作建议
    
    Args:
        diag: 持仓诊断文本
        val_pct: 估值百分位（0-100）
        fg_index: 恐贪指数（0-100）
    
    Returns:
        操作建议列表（结构化数据）
    """
    decisions = []
    
    # 1. 基于估值 percentile 的建议
    if val_pct is not None:
        if val_pct >= 85:
            decisions.append({
                "action": "reduce",
                "reason": f"市场估值过高（{val_pct:.0f}% 分位），建议减仓避险，保留 30%-50% 现金"
            })
        elif val_pct >= 70:
            decisions.append({
                "action": "hold",
                "reason": f"市场估值偏高（{val_pct:.0f}% 分位），建议谨慎追高，分批减仓"
            })
        elif val_pct <= 25:
            decisions.append({
                "action": "add",
                "reason": f"市场估值偏低（{val_pct:.0f}% 分位），可以分批加仓优质标的"
            })
    
    # 2. 基于恐贪指数的建议
    if fg_index is not None:
        if fg_index >= 75:
            decisions.append({
                "action": "sell",
                "reason": f"市场情绪过热（恐贪指数 {fg_index}），建议止盈锁定收益"
            })
        elif fg_index <= 25:
            decisions.append({
                "action": "buy",
                "reason": f"市场情绪恐慌（恐贪指数 {fg_index}），可能是左侧布局机会"
            })
    
    # 3. 基于持仓诊断的建议（解析 diag 文本）
    if diag:
        # 提取关键词
        if "集中" in diag or "同质化" in diag:
            decisions.append({
                "action": "reduce",
                "reason": "持仓过于集中，建议分散配置不同行业/风格"
            })
        if "高估值" in diag or "估值敏感" in diag:
            decisions.append({
                "action": "hold",
                "reason": "持仓含高估值标的，建议关注业绩兑现情况"
            })
        if "防御" in diag or "对冲" in diag:
            decisions.append({
                "action": "add",
                "reason": "建议增配防御性资产（债券/红利/沪深300）对冲风险"
            })
        if "回撤" in diag or "波动" in diag:
            decisions.append({
                "action": "reduce",
                "reason": "持仓波动较大，建议设置止损线或降低仓位"
            })
    
    # 4. 如果没有明确建议，添加默认建议
    if not decisions:
        decisions.append({
            "action": "hold",
            "reason": "市场中性，暂无明确操作建议，维持现有仓位"
        })
    
    return decisions


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
            # v9.5.124: 简化版决策生成
            try:
                # 导入市场数据函数
                from services.market_data import get_valuation_percentile, get_fear_greed_index
                
                # 加载持仓诊断（从 step4 生成的缓存）
                from pathlib import Path
                diag_cache_file = Path(os.environ.get("DATA_DIR", "./data")) / "night_worker" / f"diagnosis_{uid}.json"
                if not diag_cache_file.exists():
                    log(f"  ⚠️ {p.get('name', uid)}: 诊断缓存不存在，跳过决策生成")
                    continue
                
                diag_data = json.loads(diag_cache_file.read_text(encoding="utf-8"))
                diag = diag_data.get("diagnosis", "")
                
                # 获取市场数据
                val_data = get_valuation_percentile() or {}
                val_pct = val_data.get("percentile")
                fear_greed = get_fear_greed_index() or {}
                fg_index = fear_greed.get("index")
                
                # 生成操作建议
                decisions = generate_trading_decision(diag, val_pct, fg_index)
                
                # 保存到 phase3（使用 decisions_{uid} 格式）
                results[f"decisions_{uid}"] = {"decisions": decisions}
                log(f"  ✅ {p.get('name', uid)}: 决策生成完成（{len(decisions)} 条建议）")
                
            except Exception as e:
                log(f"  ❌ {p.get('name', uid)} 决策生成失败: {e}")
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
        rank_file = _P(os.environ.get("DATA_DIR", "./data")) / "fund_rank_ts.json"  # FIX: 不再硬编码 /opt/moneybag
        if not rank_file.exists():
            rank_file = _P("./data/fund_rank_ts.json")
        if not rank_file.exists():
            return []

        # 时效校验：文件超过 72 小时不使用（收益率数据已过旧）
        file_age_hours = (time.time() - os.path.getmtime(rank_file)) / 3600
        if file_age_hours > 72:
            log(f"  ⚠️ fund_rank_ts.json 已过期 {file_age_hours:.0f}小时（>72h），跳过基金推荐")
            return []

        data = _json.loads(rank_file.read_text(encoding="utf-8"))
        ranks = data.get("ranks", {})
        if not isinstance(ranks, dict):
            return []
        category_list = ranks.get(category) or ranks.get("all") or []
        if not isinstance(category_list, list):
            return []
        # 过滤极端涨幅：只推荐 8%-60% 区间的基金（更保守，适合定投推荐）
        # 排除高风险行业主题 ETF（名字含行业关键词且涨幅极高）
        EXCLUDE_KEYWORDS = ["有色", "材料", "化工", "能源", "半导体", "芯片", "光伏", "新能源车",
                            "医疗", "军工", "煤炭", "钢铁", "AI人工智能", "工业", "主题", "行业",
                            "ETF平安", "ETF联接", "商品"]
        filtered = []
        for f in category_list:
            r1y = f.get("return_1y") or 0
            name = f.get("name", "")
            if not (8 <= r1y <= 60):
                continue
            # 排除行业主题 ETF
            if any(kw in name for kw in EXCLUDE_KEYWORDS):
                continue
            filtered.append(f)
        # 附带数据更新日期供下游标注
        update_date = data.get("generated_at", data.get("trade_date", ""))
        for f in filtered:
            f["_data_date"] = update_date
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
    if fund_recs:
        fund_data_date = fund_recs[0].get('_data_date', '')
        fund_rec_text = "\n".join(
            f"  {i+1}. {f.get('name', '')}（{f.get('code', '')}）近1年+{f.get('return_1y', '?')}%"
            for i, f in enumerate(fund_recs[:3])
        )
        if fund_data_date:
            fund_rec_text += f"\n  （数据更新: {fund_data_date}，实际收益以基金公司披露为准）"
    else:
        fund_rec_text = "  暂无基金推荐"

    # ---- 市场温度（普通人看得懂的版本）----
    temp_parts = []
    temp_parts.append(f"市场情绪: {fgi.get('level', '中性')}({fgi.get('score', '?')}分)")
    # 北向：净流入维度已不可得，只报成交活跃度（不要再报"5日净买入X亿"）
    temp_parts.append(_north_user_text(north))
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

    # ---- v9.5.123: 隔夜美股/港股 + 原因 + QDII影响 ----
    overnight_markets = ""
    try:
        from infra.data_source.macro.indicators import get_global_futures_snapshot
        futures = get_global_futures_snapshot()
        if futures and futures.get("available"):
            parts_ov = []
            sp_pct = 0
            nq_pct = 0
            hsi_pct = 0
            # v9.5.129: 只有 change_pct 不为 None 且绝对值>0.01 才展示，避免 -0 噪点
            if futures.get("sp500"):
                _sp = futures['sp500'].get('change_pct')
                if _sp is not None:
                    sp_pct = _sp
                    parts_ov.append(f"标普500 {sp_pct:+.1f}%")
            if futures.get("nasdaq"):
                _nq = futures['nasdaq'].get('change_pct')
                if _nq is not None:
                    nq_pct = _nq
                    parts_ov.append(f"纳指 {nq_pct:+.1f}%")
            elif futures.get("a50"):
                _a50 = futures['a50'].get('change_pct')
                if _a50 is not None:
                    parts_ov.append(f"A50 {_a50:+.1f}%")
            if futures.get("hsi"):
                _hsi = futures['hsi'].get('change_pct')
                if _hsi is not None:
                    hsi_pct = _hsi
                    parts_ov.append(f"恒生 {hsi_pct:+.1f}%")
            if parts_ov:
                overnight_markets = " | ".join(parts_ov)
                # v9.5.123: 生成原因+对QDII影响(纯规则,不调LLM)
                _reason = ""
                _impact = ""
                # 原因推断(基于涨跌结构)
                if nq_pct > 1.5 and sp_pct > 0.5:
                    _reason = "科技股领涨,AI/芯片板块强势"
                elif nq_pct > 1.0:
                    _reason = "科技股表现突出"
                elif sp_pct > 0.5 and nq_pct > 0.5:
                    _reason = "风险偏好回升,全面上涨"
                elif sp_pct < -1.0 and nq_pct < -1.0:
                    _reason = "全面下跌,可能受宏观/地缘影响"
                elif nq_pct < -1.0:
                    _reason = "科技股回调"
                elif sp_pct < -0.5:
                    _reason = "市场谨慎"
                else:
                    _reason = "波动不大,方向不明"
                # 对你QDII的影响
                if nq_pct > 1.0:
                    _impact = "你的QDII科技基金今天大概率跟涨"
                elif nq_pct < -1.0:
                    _impact = "你的QDII科技基金今天可能承压"
                elif hsi_pct < -1.0:
                    _impact = "港股走弱,留意港股相关持仓"
                else:
                    _impact = "对你的QDII影响有限"
                overnight_markets += f"\n  {_reason} → {_impact}"
    except Exception as e:
        log(f"  隔夜市场数据获取失败: {e}")
    
    # ---- v9.5.123: 定投速览(当前整体方向) ----
    dca_hint = ""
    try:
        from services.signal import generate_daily_signal
        sig = generate_daily_signal()
        timing = sig.get("timing_label", "") if sig else ""
        if "偏多" in timing or "强势" in timing:
            dca_hint = "整体偏多，标准或加码定投"
        elif "偏空" in timing or "弱势" in timing:
            dca_hint = "整体偏空，缩减定投等待"
        else:
            dca_hint = "中性震荡，标准节奏定投"
    except Exception:
        dca_hint = "标准定投"
    
    # ---- 组装核心简报（v9.5.123重构: 加隔夜+去股票推荐+加定投速览）----
    briefing_parts = [f"📊 {today} 钱袋子晨报"]
    
    # 1. 隔夜市场（QDII用户必看）
    if overnight_markets:
        briefing_parts.append(f"\n🌏 【隔夜市场】\n{overnight_markets}")
    
    # 2. A股温度
    briefing_parts.append(f"\n📊 【A股温度】\n{temp_text}")
    
    # 3. 行业热点(注: 基于前一交易日收盘数据)
    briefing_parts.append(f"\n🏭 【行业热点】(前日)\n{sector_text}")
    
    # 4. AI研判
    if macro:
        briefing_parts.append(f"\n📝 【AI研判】\n{macro}")
    
    # 5. 定投速览
    briefing_parts.append(f"\n💰 【定投参考】\n{dca_hint}\n（每月25号定投日会推详细金额建议）")
    
    # 6. 风险提示（只在高/极高时显示）
    if geo.get('max_severity', 0) >= 3:
        briefing_parts.append(f"\n🌍 【风险提示】\n{geo_text}")
    
    # 7. 要闻（精简为2条）
    if news_titles:
        briefing_parts.append(f"\n📰 【要闻】\n" + "\n".join(f"  • {t[:35]}" for t in news_titles[:2]))
    
    briefing_parts.append("\n⚠️ AI建议仅供参考，不构成投资建议")
    
    briefing = "\n".join(briefing_parts)

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
            # v9.5.125: 操作建议使用 bullet point 格式
            lines = []
            for d in dec.get("decisions", [])[:5]:
                reason = d.get('reason', '').strip()
                if reason:
                    lines.append(f"• {reason}")
            
            # 去重（同一原因只保留一次）
            seen = set()
            unique_lines = []
            for line in lines:
                if line not in seen:
                    seen.add(line)
                    unique_lines.append(line)
            
            dec_text = "\n".join(unique_lines) if unique_lines else ""

            # v9.5.124/125: 恢复操作建议显示
            advice_section = ""
            if dec_text.strip():
                advice_section = f"\n【操作建议】\n{dec_text}"
            else:
                advice_section = f"\n【操作建议】\n  暂无操作建议"
            
            user_briefing = f"""{briefing}

📋 【{name} 持仓速览】
{diag}{advice_section}
⚠️ AI建议仅供参考，不构成投资建议"""

            # v9.5.76: 在持仓诊断前插入组合温度计（纯计算，不依赖 LLM）
            thermometer = _build_portfolio_thermometer(uid)
            if thermometer:
                user_briefing = user_briefing.replace(
                    f"📋 【{name} 持仓诊断】",
                    f"{thermometer}\n\n📋 【{name} 持仓诊断】"
                )

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
# 05:30 持仓基金专项分析（周三/周日执行）
# ============================================================

def step_fund_analysis():
    """对每只持仓基金生成 DeepSeek 专项分析
    
    仅在周三(2)和周日(6)执行，其余日子跳过（周频就够）
    写入 analysis_history source='deepseek' type='fund'
    """
    today_weekday = datetime.now().weekday()  # 0=周一 ... 6=周日
    if today_weekday not in (2, 6):  # 周三=2, 周日=6
        log(f"⏭️ 05:30 持仓基金分析：今日不是周三/周日（weekday={today_weekday}），跳过")
        return

    log("📊 05:30 持仓基金专项分析（周频）")
    profiles = _load_profiles()
    
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        if uid == "Guest":
            continue
        
        try:
            from services.fund_monitor import load_fund_holdings
            funds = load_fund_holdings(uid) or []
        except Exception as e:
            log(f"  ❌ 拉 {name} 基金持仓失败: {e}")
            continue
        
        if not funds:
            log(f"  ⚠️ {name}: 无基金持仓，跳过")
            continue
        
        log(f"  📋 {name}: {len(funds)} 只基金")
        analysis_parts = []
        
        for fund in funds[:6]:  # 最多6只，避免 DeepSeek 调用过多
            code = fund.get("code", "")
            fname = fund.get("name", code)
            cost_nav = fund.get("cost_nav") or fund.get("costNav", 0)
            current_nav = fund.get("current_nav") or fund.get("currentNav", 0)
            shares = fund.get("shares", 0)
            
            if not code:
                continue
            
            # 拉基金近期净值数据
            nav_info = ""
            try:
                from services.market_data import get_fund_nav
                nav_data = get_fund_nav(code)
                nav_val = nav_data.get("nav", "N/A")
                change = nav_data.get("change", 0)
                nav_date = nav_data.get("date", "")
                if nav_val != "N/A":
                    pnl_pct = round((float(nav_val) - float(cost_nav)) / float(cost_nav) * 100, 2) if cost_nav else 0
                    nav_info = f"当前净值 {nav_val}（{nav_date}），日涨跌 {change}%，持仓盈亏 {pnl_pct:+.1f}%"
            except Exception:
                nav_info = f"成本净值 {cost_nav}"
            
            analysis_parts.append(f"- **{fname}**（{code}）：{nav_info}，持有份额 {shares:.2f}")
        
        if not analysis_parts:
            continue
        
        holdings_text = "\n".join(analysis_parts)
        
        prompt = f"""用户当前基金持仓情况：
{holdings_text}

请从以下几个角度做简短分析（总共不超过200字）：
1. 整体持仓配置是否均衡
2. 哪些基金近期表现值得关注（可能需要加仓或减仓观察）
3. 一句话本周持仓操作建议方向

要求：
- 不直接给操作建议，只描述客观状态
- 不预测价格
- 简洁具体"""
        
        try:
            from services.ds_enhance import _call_deepseek
            result = _call_deepseek(
                prompt,
                system="你是客观的基金持仓分析助手，只描述事实和状态，不给确定性操作建议。",
                max_tokens=250,
                cache_key=f"fund_analysis_{uid}_{datetime.now().strftime('%Y%W')}",
            )
            
            if result:
                full_text = f"## 持仓基金专项分析 {datetime.now().strftime('%Y-%m-%d')}\n\n{holdings_text}\n\n---\n\n{result}"
                from services.analysis_history import save_analysis
                save_result = save_analysis(
                    user_id=uid,
                    source="deepseek",
                    source_label="DeepSeek 基金分析",
                    analysis_type="fund",
                    analysis_text=full_text,
                    direction="neutral",
                    confidence=0,
                    metadata={"fund_count": len(funds), "week": datetime.now().strftime("%Y-W%W")},
                )
                if save_result.get("ok"):
                    log(f"  ✅ {name}: 基金分析已存档 {save_result['id']}")
                else:
                    log(f"  ❌ {name}: 存档失败 {save_result.get('error')}")
        except Exception as e:
            log(f"  ❌ {name}: DeepSeek 分析失败 {e}")


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
3. 简洁说明利好还是利空，以及主要影响哪些板块
4. 禁止使用 ** 粗体格式，输出纯文本"""

        system = "你是市场数据播报员，只转述已有数据，严禁编造任何未提供的数据点。"
        llm_summary = _call_v3(prompt, 350, system=system)  # v9.5.75: 200→350 防止截断

        if llm_summary:
            # v9.5.75: 外盘速览也过滤 prompt 泄露（之前漏掉了）
            llm_summary = _filter_prompt_leak(llm_summary)
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
# IPO / 上市事件追踪 + 对应基金推荐
# ============================================================

# 热门 IPO 观察列表（手动维护，自动与 Tushare 新股日历结合）
_HOT_IPO_WATCHLIST = [
    {"name": "长鑫科技", "market": "A股科创板", "status": "进行中", "index": ["科创50", "中证1000"], "funds": ["华夏科创50ETF联接A", "国泰中证1000ETF联接A"], "note": "国内 DRAM 存储芯片龙头，与三星/海力士竞争 HBM"},
    {"name": "长江存储", "market": "A股科创板", "status": "传闻中", "index": ["科创50", "中证半导体"], "funds": ["华夏科创50ETF联接A", "国联安中证半导体ETF联接"], "note": "国内 NAND Flash 龙头，3D NAND 研发实力强，上市预期持续升温"},
    {"name": "xAI", "market": "美股纳斯达克", "status": "传闻中", "index": ["纳斯达克100", "标普500"], "funds": ["博时纳斯达克100ETF联接C", "易方达标普500ETF联接C"], "note": "马斯克 AI 公司，Grok 大模型，估值约1750亿美元"},
    {"name": "SpaceX", "market": "美股纳斯达克", "status": "暂无计划", "index": ["纳斯达克100", "标普500"], "funds": ["博时纳斯达克100ETF联接C"], "note": "上市后大概率进纳指，现阶段可通过纳指基金间接布局"},
    {"name": "字节跳动", "market": "港股/美股待定", "status": "传闻中", "index": ["恒生科技", "纳斯达克100"], "funds": ["华夏恒生科技ETF联接A", "博时纳斯达克100ETF联接C"], "note": "TikTok/抖音母公司，若港股上市影响恒生科技指数"},
]

def _get_ipo_section() -> str:
    """获取近期 IPO 动态 + 对应基金推荐，用于晨报"""
    lines = ["📅 【IPO & 上市追踪】"]

    # 从统一配置API读取观察列表（不再硬编码）
    hot_upcoming = []
    try:
        import requests
        resp = requests.get("http://localhost:8000/api/ipo/watchlist", timeout=5)
        if resp.ok:
            data = resp.json()
            hot_upcoming = [w for w in data.get("watchlist", []) if w.get("status") in ("进行中", "传闻中")]
    except Exception:
        # API不可用时降级到硬编码
        hot_upcoming = [w for w in _HOT_IPO_WATCHLIST if w.get("status") in ("进行中", "传闻中")]

    # A股新股日历（Tushare，近7天内）
    try:
        from services.tushare_data import _call_tushare
        from datetime import date, timedelta
        today = date.today()
        start = (today - timedelta(days=3)).strftime("%Y%m%d")
        end = (today + timedelta(days=7)).strftime("%Y%m%d")
        rows = _call_tushare("new_share", {"start_date": start, "end_date": end},
                              "ts_code,name,ipo_date,issue_date,amount,market")
        if rows:
            for r in rows[:3]:
                name = r.get("name", "")
                ipo_date = r.get("ipo_date", "")
                ts_code = r.get("ts_code", "")
                market = r.get("market", "")
                if not market:
                    if ts_code.startswith("688"): market = "科创板"
                    elif ts_code.startswith("300"): market = "创业板"
                    elif ts_code.endswith(".BJ"): market = "北交所"
                    elif ts_code.endswith(".SH"): market = "上交所"
                    else: market = "深交所"
                watchlist_match = next((w for w in hot_upcoming if w["name"] in name or name in w["name"]), None)
                if watchlist_match:
                    funds_str = "、".join(watchlist_match["funds"][:2])
                    hot_label = watchlist_match.get("hot_label", "")
                    lines.append(f"🔥 {name}（{ipo_date} 上市，{market}{' · '+hot_label if hot_label else ''}）→ 上市后纳入{watchlist_match['index'][0]}，可关注：{funds_str}")
                elif ipo_date:
                    lines.append(f"• {name} 预计 {ipo_date} 上市（{market}）")
    except Exception as e:
        log(f"  ⚠️ 新股日历获取失败: {e}")

    # 热门观察列表
    if hot_upcoming:
        lines.append("🌐 境外热门 IPO 动态：")
        for w in hot_upcoming:
            if w.get("flag") in ("🇺🇸", "🌐") or "港" in w.get("market", "") or "美" in w.get("market", ""):
                funds_str = "、".join(w["funds"][:2])
                hot_label = w.get("hot_label", "")
                status = w['status']
                # v9.5.75: 传闻中的 IPO 在 note 前加明确标注，避免误导用户
                note = w['note']
                if status == "传闻中" and "传闻" not in note:
                    note = f"⚠️传闻未经证实 — {note}"
                lines.append(f"  • {w.get('flag','')} {w['name']}（{w['market']}，{status}{' ' + hot_label if hot_label else ''}）— {note}")
                lines.append(f"    → 上市后纳入{w['index'][0]}，现可布局：{funds_str}")

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


# ============================================================
# 07:30 生成早安简报
# ============================================================

def step_morning_briefing(products, overnight):
    log("📋 07:30 生成早安简报")
    # v9.5.66: 此时还未调用 LLM（IPO/外盘已经在前面，这里只是组装），
    # _MODEL_USAGE_STATS 累计的是 step_r1_phase1/2/3 + products 等所有阶段
    briefings = {}
    profiles = _load_profiles()

    # v9.5.66 模型来源 footer（一次生成，所有用户共用）
    model_footer = _build_model_usage_footer()

    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        # 默认全部 Pro 版；用户 profile 里 display_mode="simple" 时才切简洁版
        display_mode = p.get("display_mode", "pro")
        full_product = products.get(uid, "暂无分析")

        if display_mode != "simple":
            # Pro 版（默认）：完整内容 + IPO追踪 + 外盘
            main_budget = 3200 if overnight else 3800
            truncated_product = _truncate_at_sentence(full_product, main_budget)
            briefing = f"☀️ 早安，{name}！\n\n{truncated_product}"

            # v9.5.123: IPO追踪从晨报移除(资讯页有专栏,避免重复+占篇幅)

            if overnight:
                # 外盘单独追加，不受主体截断影响，保证完整（最多600字）
                overnight_text = overnight[:600]
                # 确保外盘评述是完整句子
                if overnight_text != overnight and '。' in overnight_text:
                    overnight_text = overnight_text[:overnight_text.rfind('。')+1]
                briefing += f"\n\n🌍 【外盘速览】\n{overnight_text}"
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

        # v9.5.66 末尾追加模型来源标识
        if model_footer:
            briefing += model_footer

        briefings[uid] = briefing
        log(f"  ✅ {name}: {'Pro' if display_mode != 'simple' else 'Simple'} 简报 {len(briefing)}字")

    return briefings


# ============================================================
# 08:30 推送早安简报
# ============================================================

def step_push_briefing(briefings):
    # FIX: 推送前随机延迟 0-120 秒，避免多用户集中推送导致流量尖峰
    import random
    jitter = random.randint(0, 120)
    log(f"📤 08:30 推送早安简报（错峰延迟 {jitter}s）")
    time.sleep(jitter)
    try:
        from services.wxwork_push import is_configured, send_daily_report_to
        if not is_configured():
            log("  ⚠️ 企微未配置，跳过推送")
            return

        profiles = _load_profiles()
        for p in profiles:
            uid = p["id"]
            wxid = p.get("wxworkUserId", "")

            # 没有填写 wxworkUserId 或 userId 不在简报里，跳过
            # FIX: 同时过滤无效占位值（"Guest"/"None"/"null"等）
            _INVALID_WXID = {"", "guest", "none", "null", "undefined", "n/a"}
            if not wxid or str(wxid).lower().strip() in _INVALID_WXID or uid not in briefings:
                log(f"  ⏭️ {p.get('name', uid)}: 未配置企微ID或无简报，跳过")
                continue

            # 用户账号存在性检查
            if not _user_has_account(uid):
                log(f"  ⏭️ {p.get('name', uid)}: 未注册/登录，跳过推送")
                continue

            # briefing 已在生成时截断，这里只做最终保险截断（3900字上限）
            # 企微 Markdown 遇到未闭合的 ** 会截断后续内容，推送前统一清理
            import re as _re2
            msg = _re2.sub(r'\*\*', '', briefings[uid])
            msg = _truncate_at_sentence(msg, 3900)
            
            # v9.8.10: 存档推送内容（用于质量评估）
            try:
                from services.wxwork_push import archive_push
                archive_push(
                    user_id=uid,
                    push_type="briefing",
                    content=msg,
                    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            except Exception as e:
                log(f"  ⚠️ 存档失败: {e}")
            
            result = send_daily_report_to(wxid, msg, title="☀️ 钱袋子早安简报")
            if result.get("ok"):
                log(f"  ✅ {p.get('name', uid)}: 已推企微")
            else:
                err = result.get("data", {}).get("errcode", 0)
                if err == 81013:
                    # userId 无效（未绑定企微），不降级为@all，直接跳过
                    log(f"  ⏭️ {p.get('name', uid)}: 企微userId无效(81013)，跳过（请在profiles.json清空wxworkUserId）")
                else:
                    log(f"  ❌ {p.get('name', uid)}: 推送失败 {result}")
    except Exception as e:
        log(f"  ❌ 推送失败: {e}")


# ============================================================
# 主函数：按时间顺序执行全链路
# ============================================================

def run_night_worker():
    """AI 凌晨自主工作主函数（01:00-08:30）
    
    v9.5.122: 周末/节假日跳过（已有 weekend_push.py 覆盖周六推送）
    """
    # v9.5.122: 非交易日跳过完整链路（避免推送和上一个交易日完全相同的重复内容）
    today = date.today()
    if today.weekday() >= 5:  # 周六=5, 周日=6
        log(f"🌙 今天是周末({today.strftime('%A')})，跳过凌晨工作链（由 weekend_push.py 处理周末推送）")
        return []
    
    log("🌙 ========================================")
    log(f"🌙 AI 凌晨工作启动 {today}")
    log("🌙 ========================================")

    start = time.time()
    # v9.5.66: 重置模型使用统计，每次完整运行独立计数
    _reset_model_stats()

    # 01:00 健康巡检
    step_health_check()

    # 01:15 月度快照（每月1号执行）
    step_monthly_snapshot()

    # 01:30 数据预热
    step_data_warm()

    # 01:45 基金排行榜刷新（周一/周四执行，保证 72h 内有效）
    step_fund_rank_refresh()

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
        # v9.5.124: 保存估值缓存前先检查数据有效性，避免保存默认值
        _val_data = get_valuation_percentile()
        if _val_data and _val_data.get("percentile", 50) != 50:  # 50 是默认值，不算有效数据
            save_precomputed("valuation", _val_data)
            log(f"  ★ 估值缓存已更新: pct={_val_data['percentile']}%")
        else:
            log(f"  ⚠️ 估值数据无效（默认值），跳过缓存更新")

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

    # 05:30 持仓基金专项分析（周三/周日）
    step_fund_analysis()

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


def _inject_hallucination_label(briefings: dict) -> dict:
    """E6 v9.5.44 推送前幻觉扫描 — 纯规则，<1s，不调 LLM

    检测晨报文本中的常见幻觉模式：
    1. prompt 泄漏关键词（防御层漏网之鱼）
    2. 估值/regime 矛盾（one_line 无估值警示但简报含"低估/风控正常"+ 实际高估值）
    3. 数字明显异常（百分比 >100% 或 <-100%）

    发现问题 → 在该用户晨报顶部追加 ⚠️ [AI质检] 一行标注，正文不改
    无问题  → 原样返回
    """
    import re as _re_hc

    # prompt 泄漏关键词（与 llm_output_guard 一致）
    LEAK_KW = [
        '我们被要求', '持仓列表是', '方括号', '150字以内',
        '输出要求', '你是投资组合诊断师', '需给出总评',
        '让我分析', '只根据名称', '直接输出',
        '用户让我', '用户提供了', '现在分析数据', '深层需求', '铁律要求',
    ]
    # 夸大数字：正文含 >100% 涨跌（除了"近3年"等明确长周期描述的行）
    # 补数字边界，避免把 1.406% 误截成 406%
    EXAG_PAT = _re_hc.compile(r'(?<![\d.])(?<!近[123三]年)(?<!近五年)([1-9]\d{2,}(?:\.\d+)?%)')

    # 拉市场估值（用于校验"低估/风控正常"等描述）
    mt_pct = None
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/api/stock-screen?top_n=1", timeout=3
        ) as r:
            mt_pct = _json.loads(r.read()).get("market_timing", {}).get("valuation_pct")
    except Exception:
        pass

    # 加载各用户真实持仓名称（用于幻觉检测4）
    _real_holdings_map = {}
    try:
        from services.fund_monitor import load_fund_holdings
        from services.stock_monitor import load_stock_holdings
        for uid in briefings:
            _fh = load_fund_holdings(uid) or []
            _sh = load_stock_holdings(uid) or []
            _names = set()
            for x in _fh + _sh:
                n = x.get('name', '')
                if n:
                    _names.add(n[:4])  # 只取前4字做宽松匹配
            _real_holdings_map[uid] = _names
    except Exception:
        pass

    result = {}
    for uid, text in briefings.items():
        issues = []

        # 1. prompt 泄漏检测
        for kw in LEAK_KW:
            if kw in text:
                issues.append(f"prompt泄漏「{kw}」")
                break  # 一条就够，不刷屏

        # 2. 估值语义矛盾（low/normal 表述 + 实际高估值 ≥85%）
        if mt_pct is not None and mt_pct >= 85:
            suspicious = ['风控正常', '估值合理', '低估', '底部区域', '可以大胆']
            for kw in suspicious:
                if kw in text:
                    issues.append(f"估值矛盾「{kw}」(实际{mt_pct:.0f}%分位)")
                    break

        # 3. 数字夸大（单段涨幅 >200%，大概率幻觉，不含标题行）
        for m in EXAG_PAT.finditer(text):
            val = float(m.group(1).rstrip('%'))
            if val > 200:
                issues.append(f"异常涨幅数字「{m.group(0)}」")
                break

        # 4. v9.5.129: 持仓速览中出现的"X基金"名称是否在真实持仓里
        # 只扫"持仓速览"段落，避免误报新闻里的基金名
        if uid in _real_holdings_map and _real_holdings_map[uid]:
            _real = _real_holdings_map[uid]
            # 从晨报中提取持仓速览段落
            _section_match = _re_hc.search(r'【.{1,6}持仓速览】([\s\S]*?)(?=\n[🌏📝💡⚠️🎯]|\Z)', text)
            if _section_match:
                _section = _section_match.group(1)
                # 匹配 2-8 字基金/股票名（排除常见非实体词）
                _SKIP = {'组合','基金','股票','指数','混合','偏股','债券','主动','高位','低位',
                         '科技','医疗','消费','金融','能源','均衡','成长','价值','中国','全球'}
                _fund_names_in_text = _re_hc.findall(r'[\u4e00-\u9fa5]{2,8}(?:基金|混合|股票|ETF|LOF|债券|指数)?', _section)
                for fn in _fund_names_in_text:
                    fn4 = fn[:4]
                    if fn4 in _SKIP or len(fn4) < 2:
                        continue
                    # 如果这个名字没在任何真实持仓前4字里，就可疑
                    if fn4 not in _real and fn not in _real:
                        issues.append(f"疑似编造名称「{fn}」")
                        break  # 一条就够

        if issues:
            label = "⚠️ [AI质检] 本报告含可疑内容：" + "、".join(issues) + "，数据仅供参考，请核实\n\n"
            result[uid] = label + text
            log(f"  ⚠️ {uid} 晨报标注：{issues}")
        else:
            result[uid] = text

    return result


def push_morning():
    """08:30 推送早安简报（独立 cron 调用）

    由 crontab '30 8 * * 1-5' 触发，读取凌晨生成的简报文件并推送。
    关键修复：推送前重新拉外盘数据（01:00 凌晨美盘还在交易，数据不准）
    外盘速览独立发送（第二条消息），避免混入主简报导致 Markdown 截断
    """
    log("📤 08:30 推送早安简报")
    briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
    if briefing_file.exists():
        briefings = json.loads(briefing_file.read_text(encoding="utf-8"))

        # 08:30 重新拉外盘数据（独立消息，不再拼入主简报）
        overnight_msg = None
        try:
            fresh_overnight = step_overnight_check()
            import re as _re
            has_real_data = fresh_overnight and any(c.isdigit() for c in fresh_overnight) and len(fresh_overnight) > 50
            if has_real_data:
                overnight_clean = _re.sub(r'\*\*', '', fresh_overnight)
                overnight_msg = f"🌍 【外盘速览】\n{overnight_clean}"
                log(f"  ✅ 外盘数据准备完毕: {len(overnight_msg)}字")
            else:
                log("  ⚠️ 外盘数据不足，跳过外盘消息")
        except Exception as e:
            log(f"  ⚠️ 外盘刷新失败: {e}")

        # 主简报：去掉旧的外盘速览部分（避免重复）
        import re as _re2
# v9.5.124: 支持有/无 emoji 的外盘速览标记

        # v9.5.124: 外盘速览标记（支持有/无 emoji）
        OVERNIGHT_MARKER = "🌍 【外盘速览】"
        OVERNIGHT_MARKER_NO_EMOJI = "【外盘速览】"
        for uid in list(briefings.keys()):
            # 从主简报里截掉外盘部分
            # v9.5.124: 同时检查有/无 emoji 的标记
            _marker_found = False
            for _marker in [OVERNIGHT_MARKER, OVERNIGHT_MARKER_NO_EMOJI]:
                if _marker in briefings[uid]:
                    briefings[uid] = briefings[uid].split(_marker)[0].rstrip()
                    _marker_found = True
                    break
            if not _marker_found:
                log(f"  ⚠️ 未找到外盘速览标记，跳过截取")

        # E6 v9.5.44 推送前内联幻觉扫描 + 标注
        # 纯规则，<1s，不调 LLM，有问题在晨报顶部加 ⚠️ 标注
        try:
            briefings = _inject_hallucination_label(briefings)
        except Exception as e:
            log(f"  ⚠️ 幻觉扫描失败（不影响推送）: {e}")

        # 发送主简报
        step_push_briefing(briefings)

        # 发送外盘速览（独立第二条，延迟1秒）
        if overnight_msg:
            import time as _time
            _time.sleep(1)
            try:
                from services.wxwork_push import is_configured, send_daily_report_to
                profiles = _load_profiles()
                if is_configured():
                    for p in profiles:
                        uid = p["id"]
                        wxid = p.get("wxworkUserId", "")
                        if not wxid or uid not in briefings:
                            continue
                        if not _user_has_account(uid):
                            continue
                        result = send_daily_report_to(wxid, overnight_msg, title="")
                        if result.get("ok"):
                            log(f"  ✅ {p.get('name', uid)}: 外盘速览已推")
                        else:
                            log(f"  ⚠️ {p.get('name', uid)}: 外盘速览推送失败")
            except Exception as e:
                log(f"  ⚠️ 外盘速览推送异常: {e}")
    else:
        log("  ⚠️ 无简报文件，凌晨流程可能未执行")
        try:
            from services.wxwork_push import is_configured, send_daily_report_to
            if is_configured():
                for _uid in ["LeiJiang", "BuLuoGeLi"]:
                    send_daily_report_to(_uid, "⚠️ 今日晨报未生成，凌晨流程可能失败，请检查 night.log")
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
        # E5 v9.5.47: 主流程异常捕获 — 失败时发企微告警
        import sys
        try:
            briefings = run_night_worker()
        except Exception as _e:
            import traceback
            err_msg = f"❌ night_worker 主流程崩溃（{date.today()}）\n\n错误：{type(_e).__name__}: {str(_e)[:200]}\n\n请检查 cron.log"
            log(f"[FATAL] night_worker crashed: {_e}")
            traceback.print_exc()
            try:
                from services.wxwork_push import is_configured, send_text
                if is_configured():
                    send_text(err_msg)
                    log("✅ 崩溃告警已发企微")
            except Exception as _we:
                log(f"⚠️ 企微告警发送失败: {_we}")
            sys.exit(1)
