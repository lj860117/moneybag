"""
钱袋子 — 周末轻量推送 v9.5.89
=======================================================================
每周六 09:30 由 crontab 触发，给每个注册用户推送一条轻量周末简报：

内容（纯计算，不调 LLM，0 成本）：
  1. 组合温度计（本周浮盈快照 + 各持仓明细）
  2. 再平衡缺口（当前结构 vs 理想配置）
  3. 大盘估值 + 恐惧贪婪指数（当前市场温度）
  4. 定投提醒（下周一是否该定投 + 当前仓位）
  5. 本周市场概况（来自 precomputed cache，工作日已算好）
  6. 一句话行动建议（规则引擎，无 LLM）

可选（通过 --llm 参数开启，默认关闭）：
  用 Flash 级别模型生成一段「本周复盘 + 下周关注」约150字的 AI 点评

用法：
  python scripts/weekend_push.py          # 纯规则版（免费）
  python scripts/weekend_push.py --llm    # 带 AI 点评版（耗少量 token）
  python scripts/weekend_push.py --dry    # 打印内容但不推送（调试用）
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, date
from pathlib import Path

# ---- 路径初始化 ----
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))

# 加载环境变量（服务器上 .env 里有 API Key）
try:
    _env_path = _BACKEND_DIR / ".env"
    if _env_path.exists():
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
except Exception:
    pass

from config import DATA_DIR, LLM_API_URL, LLM_API_KEY

LOG_DIR = DATA_DIR / "night_worker"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [weekend] {msg}"
    print(line)
    logfile = LOG_DIR / f"{date.today()}.log"
    with open(logfile, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ============================================================
# 加载工具函数（复用 night_worker 里已有的）
# ============================================================

def _load_profiles() -> list:
    try:
        # 直接复用 night_worker 的实现（含 fallback 硬编码用户）
        from scripts.night_worker import _load_profiles as nw_lp
        return nw_lp() or []
    except Exception as e:
        log(f"  ⚠️ 加载用户列表失败: {e}")
        # 兜底：默认两个用户
        return [
            {"id": "LeiJiang", "name": "LeiJiang", "wxworkUserId": "LeiJiang"},
            {"id": "BuLuoGeLi", "name": "BuLuoGeLi", "wxworkUserId": "BuLuoGeLi"},
        ]


def _user_has_account(uid: str) -> bool:
    """检查用户是否已注册（user 文件存在）"""
    import hashlib
    from config import USERS_DIR
    safe = hashlib.sha256(uid.encode()).hexdigest()[:16]
    return (USERS_DIR / f"{safe}.json").exists()


def _get_portfolio_thermometer(uid: str) -> str:
    """复用 night_worker 的组合温度计（含再平衡缺口）"""
    try:
        # 直接复用 night_worker 里的函数
        from scripts.night_worker import _build_portfolio_thermometer
        return _build_portfolio_thermometer(uid)
    except Exception as e:
        log(f"  ⚠️ 组合温度计失败: {e}")
        return ""


def _get_market_temperature() -> str:
    """大盘温度：估值百分位 + 恐惧贪婪指数"""
    try:
        from services.market_data import get_valuation_percentile, get_fear_greed_index
        val = get_valuation_percentile() or {}
        fgi = get_fear_greed_index() or {}

        pct = val.get("percentile", 50)
        fgi_score = fgi.get("score", 50)
        fgi_level = fgi.get("level", "")
        val_index = val.get("index", "沪深300")

        # 估值判断
        if pct <= 20:
            val_emoji, val_desc = "🟢🟢", f"极度低估（历史{pct:.0f}%分位），难得布局机会"
        elif pct <= 40:
            val_emoji, val_desc = "🟢", f"偏低估（历史{pct:.0f}%分位），可正常定投"
        elif pct <= 60:
            val_emoji, val_desc = "🟡", f"估值中性（历史{pct:.0f}%分位），正常节奏"
        elif pct <= 80:
            val_emoji, val_desc = "🟠", f"偏贵（历史{pct:.0f}%分位），减少追高"
        else:
            val_emoji, val_desc = "🔴", f"高估区间（历史{pct:.0f}%分位），谨慎入场"

        # 情绪判断
        if fgi_score >= 75:
            fgi_emoji, fgi_desc = "😱", f"贪婪({fgi_score:.0f}) — 历史上此时往往是短期顶部"
        elif fgi_score >= 55:
            fgi_emoji, fgi_desc = "😊", f"偏乐观({fgi_score:.0f}) — 市场情绪较好"
        elif fgi_score >= 45:
            fgi_emoji, fgi_desc = "😐", f"中性({fgi_score:.0f}) — 市场方向待明朗"
        elif fgi_score >= 25:
            fgi_emoji, fgi_desc = "😟", f"偏悲观({fgi_score:.0f}) — 可能是分批买入时机"
        else:
            fgi_emoji, fgi_desc = "😱", f"极度恐惧({fgi_score:.0f}) — 历史上往往是加仓机会"

        # 综合操作建议（规则）
        timing_score = pct * 0.6 + fgi_score * 0.4
        if timing_score < 30:
            action = "💡 建议：本周可适当加大定投，市场低估+恐惧时买入历史胜率高"
        elif timing_score < 50:
            action = "💡 建议：维持正常定投节奏，当前性价比尚可"
        elif timing_score < 70:
            action = "💡 建议：按计划定投，不追高，等待更好买点"
        else:
            action = "💡 建议：暂缓大额买入，高估值+乐观情绪需谨慎，保持定投纪律"

        lines = [
            "🌡️ 本周市场温度",
            f"  {val_emoji} {val_index}估值 — {val_desc}",
            f"  {fgi_emoji} 恐惧贪婪指数 — {fgi_desc}",
            action,
        ]
        return "\n".join(lines)
    except Exception as e:
        log(f"  ⚠️ 市场温度计算失败: {e}")
        return ""


def _get_dca_reminder(uid: str) -> str:
    """定投提醒 — 判断下周一是否该定投（基于估值+月度计划）"""
    try:
        from services.market_data import get_valuation_percentile
        val = get_valuation_percentile() or {}
        pct = val.get("percentile", 50)

        # 下周一日期
        today = date.today()
        days_to_monday = (7 - today.weekday()) % 7 or 7
        from datetime import timedelta
        next_monday = today + timedelta(days=days_to_monday)

        # 估值分位决定建议倍数
        if pct <= 30:
            mult, advice = 1.5, "低估区间，建议本次加倍定投"
        elif pct <= 50:
            mult, advice = 1.0, "合理区间，正常定投"
        elif pct <= 70:
            mult, advice = 0.8, "偏贵，可小幅减量定投"
        else:
            mult, advice = 0.5, "高估区间，建议半量定投，剩余留作更低点备用"

        lines = [
            f"📅 下周定投提醒（{next_monday.strftime('%m/%d')} 周一）",
            f"  当前估值 {pct:.0f}%分位 → {advice}",
        ]
        if mult != 1.0:
            lines.append(f"  参考倍数：基准金额 × {mult}（基准 = 你设定的月定投额 / 4）")

        return "\n".join(lines)
    except Exception as e:
        log(f"  ⚠️ 定投提醒失败: {e}")
        return ""


def _get_week_summary() -> str:
    """本周市场简要：从 precomputed cache 读上周已算好的数据"""
    try:
        from services.precomputed_cache import get_precomputed
        daily = get_precomputed("daily_signal") or {}
        val = get_precomputed("valuation") or {}
        fgi = get_precomputed("fear_greed") or {}

        lines = []
        if daily:
            regime = daily.get("regime", "")
            one_line = daily.get("one_line", "")
            if regime:
                lines.append(f"  市场状态：{regime}")
            if one_line:
                lines.append(f"  本周一句话：{one_line}")

        if not lines:
            lines.append("  （上周市场数据暂不可用，工作日凌晨会自动补充）")

        return "📰 本周市场概况\n" + "\n".join(lines)
    except Exception as e:
        return ""


def _call_flash_llm(prompt: str, max_tokens: int = 200) -> str:
    """调用轻量 Flash 模型生成 AI 点评（周末版专用，成本极低）"""
    if not LLM_API_KEY:
        return ""
    try:
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()
        result = gw.call_sync(
            prompt,
            system="你是简洁的投资助手。用中文，不超过150字，不预测价格，只做客观分析和行动建议。",
            model_tier="llm_light",   # Flash 级别，最便宜
            user_id="",
            module="weekend_push",
            max_tokens=max_tokens,
        )
        content = result.get("content", "")
        if content:
            return content.strip()
    except Exception as e:
        log(f"  ⚠️ AI 点评失败: {e}")
    return ""


def _build_ai_comment(thermometer: str, market: str) -> str:
    """用 Flash 模型生成本周复盘 + 下周关注（约150字，可选）"""
    if not thermometer and not market:
        return ""
    prompt = f"""请基于以下数据，写一段本周投资复盘和下周关注点，150字以内：

{market}

{thermometer[:500] if thermometer else ''}

要求：
- 直接说结论，不废话
- 不预测价格涨跌
- 结合持仓浮盈情况给出1个具体行动建议
- 语气轻松友好"""
    return _call_flash_llm(prompt, 200)


# ============================================================
# 主推送逻辑
# ============================================================

def build_weekend_briefing(uid: str, name: str, use_llm: bool = False) -> str:
    """为单个用户生成周末简报"""
    today_str = date.today().strftime("%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today_cn = weekday_cn[date.today().weekday()]

    sections = []

    # 标题
    sections.append(f"🌈 周末好，{name}！\n{today_str} {today_cn} · 钱袋子周末简报")

    # 1. 市场温度
    market = _get_market_temperature()
    if market:
        sections.append(market)

    # 2. 组合温度计（含再平衡缺口）
    thermometer = _get_portfolio_thermometer(uid)
    if thermometer:
        sections.append(thermometer)
    else:
        sections.append("📊 组合温度计\n  （暂无持仓记录，在[持仓]页添加交易后会显示）")

    # 3. 定投提醒
    dca = _get_dca_reminder(uid)
    if dca:
        sections.append(dca)

    # 4. 本周市场概况
    week_summary = _get_week_summary()
    if week_summary:
        sections.append(week_summary)

    # 5. AI 点评（可选，默认关闭）
    if use_llm:
        ai_comment = _build_ai_comment(thermometer, market)
        if ai_comment:
            sections.append(f"🤖 AI 本周复盘\n{ai_comment}")

    # 结尾
    sections.append("─────────────────\n钱袋子 · 陪你做家庭CFO\n祝周末愉快 ☀️")

    return "\n\n".join(sections)


def run(dry_run: bool = False, use_llm: bool = False):
    log("=" * 50)
    log(f"🌈 周末轻量推送启动 {date.today()}")
    log("=" * 50)

    # FIX: 错峰延迟 0-60 秒，避免与其他定时任务并发
    import random, time as _t
    jitter = random.randint(0, 60)
    log(f"  ⏳ 错峰延迟 {jitter}s")
    _t.sleep(jitter)

    # 只在周六/周日运行
    today_weekday = date.today().weekday()  # 0=周一 ... 6=周日
    if today_weekday not in (5, 6):
        log(f"⏭️ 今天不是周末（weekday={today_weekday}），跳过")
        return

    profiles = _load_profiles()
    if not profiles:
        log("⚠️ 没有找到用户，退出")
        return

    try:
        from services.wxwork_push import is_configured, send_daily_report_to
        wx_ok = is_configured()
    except Exception:
        wx_ok = False

    if not wx_ok and not dry_run:
        log("⚠️ 企微未配置，退出")
        return

    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        wxid = p.get("wxworkUserId", "")

        # FIX: 过滤无效占位值
        _INVALID_WXID = {"", "guest", "none", "null", "undefined", "n/a"}
        if (not wxid or str(wxid).lower().strip() in _INVALID_WXID) and not dry_run:
            log(f"  ⏭️ {name}: 未配置企微ID，跳过")
            continue

        if not _user_has_account(uid):
            log(f"  ⏭️ {name}: 未注册，跳过")
            continue

        log(f"  📋 生成 {name} 的周末简报...")
        try:
            briefing = build_weekend_briefing(uid, name, use_llm=use_llm)
            log(f"  ✅ 生成完成 ({len(briefing)}字)")

            if dry_run:
                print("\n" + "═" * 50)
                print(f"[DRY RUN] {name} 的周末简报：")
                print("═" * 50)
                print(briefing)
                print("═" * 50 + "\n")
            else:
                # 推送企微
                import re
                msg = re.sub(r'\*\*', '', briefing)  # 清理未闭合 markdown 符号
                msg = msg[:3900]  # 企微上限
                result = send_daily_report_to(wxid, msg, title="🌈 钱袋子周末简报")
                if result.get("ok"):
                    log(f"  ✅ {name}: 周末简报已推送企微")
                else:
                    err = result.get("data", {}).get("errcode", 0)
                    if err == 81013:
                        log(f"  ⏭️ {name}: 企微userId无效(81013)，跳过")
                    else:
                        log(f"  ❌ {name}: 推送失败 {result}")
        except Exception as e:
            log(f"  ❌ {name}: 生成失败 {e}")
            import traceback
            traceback.print_exc()

    log("🌈 周末推送完成")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="钱袋子周末轻量推送")
    parser.add_argument("--dry", action="store_true", help="打印内容但不推送（调试）")
    parser.add_argument("--llm", action="store_true", help="加入 AI 本周复盘点评（耗少量 token）")
    args = parser.parse_args()
    run(dry_run=args.dry, use_llm=args.llm)
