"""
4 类基金信号的中文纯文本文案渲染。

设计依据：docs/design/signal-scout-fund-account.md §3.1 / §4.3

通用硬要求：
  * 第一行让用户认出自己的基金。
  * 标注数据时点与滞后天数。
  * 覆盖率不足时点名未覆盖基金。
  * 不输出 `301563.SZ` 这类原始股票代码（用「中文名(6位代码)」）。
  * 正文 ≤8 行纯文本。
"""
from datetime import datetime

from services.fund_signal.config import (
    DCA_TRIGGER_DAY,
    DRAWDOWN_RUNGS,
    RELEVANCE_PUSH,
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_md(value) -> str:
    """YYYYMMDD / YYYY-MM-DD → MM-DD；脏值返回 ""。"""
    s = str(value or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[4:6]}-{s[6:8]}"
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s[5:]
    return ""


def _signal(sig_type: str, title: str, content: str, codes: list,
            level: str, tags: list, related: str = "") -> dict:
    """组装与 signal_scout 兼容的信号 dict（字段逐一对其，见 §3.1）。"""
    return {
        "type": sig_type,
        "title": title,
        "content": content,
        "codes": codes,
        "source": "fund_signal",
        "time": _now(),
        "level": level,
        "tags": tags,
        "relevance": RELEVANCE_PUSH,
        "related_holding": related,
    }


def _tenure(rec: dict) -> str:
    """离任经理任期年数（end_date - begin_date）；脏数据返回 "-"。"""
    from datetime import date
    try:
        b = date.fromisoformat(rec.get("begin_date", ""))
        e = date.fromisoformat(rec.get("end_date", ""))
    except (ValueError, TypeError):
        return "-"
    return f"{(e - b).days / 365:.1f}"


def _lag(ann: str) -> str:
    """公告日距今的天数；脏数据返回 "-"。"""
    from datetime import date
    try:
        a = date.fromisoformat(ann)
    except (ValueError, TypeError):
        return "-"
    return str((date.today() - a).days)


def render_xray(result, positions: list) -> dict:
    """P0-1 组合穿透体检文案。"""
    if result is None or not positions:
        return None
    cov = result.coverage
    total = len(positions)
    penetrated_funds = total - len(cov.blind_funds)

    top_ind = result.industries[0] if result.industries else None
    top_stock = result.stocks[0] if result.stocks else None

    title = "📊 持仓穿透体检"
    if top_ind:
        title = f"📊 持仓穿透体检｜{top_ind.industry}占你 {top_ind.exposure_pct:.1f}% 净值"
        if top_stock and top_stock.fund_count >= 2:
            title += f"，{top_stock.fund_count} 只基金在买同一只票"

    lines = [
        f"持仓截止 {cov.end_date}（公告 {cov.ann_date}，滞后 {cov.lag_days} 天）",
        f"穿透覆盖：你总净值的 {cov.penetrated_pct:.1f}%（{total} 只基金中的 {penetrated_funds} 只前十大重仓）",
    ]
    if cov.blind_funds:
        names = "、".join(f"{f['name']}({f['code']})" for f in cov.blind_funds)
        lines.append(f"未覆盖 {cov.blind_pct + cov.residual_pct:.1f}%：{names}")
        lines.append(f"  其中 {cov.blind_pct:.1f}% 季报无持仓数据永久无法穿透，其余 {cov.residual_pct:.1f}% 为前十大之外+现金债券")
    else:
        lines.append(f"未覆盖 {cov.residual_pct:.1f}%：为各基金前十大之外的持股、现金与债券")

    if result.industries:
        label = "申万二级" if cov.industry_source == "sw_l2" else "Tushare 行业分类"
        seg = " ｜ ".join(f"{i.industry} {i.exposure_pct:.1f}%" for i in result.industries[:4])
        lines.append(f"行业集中（{label}，占你总净值）：{seg}")

    top3 = [s for s in result.stocks if s.fund_count >= 2][:3]
    if top3:
        seg = " ｜ ".join(
            f"{s.name or s.symbol}({s.symbol[:6]}) {s.exposure_pct:.2f}%←{s.fund_count} 只" for s in top3
        )
        lines.append(f"重复押注：{seg}")

    lines.append(
        f"结论：你持有 {total} 只基金，但穿透后 {penetrated_funds} 只境内基金重仓高度重合，"
        f"实际分散度远低于「{total} 只」的直觉。"
    )

    return _signal(
        "fund_xray_concentration", title, "\n".join(lines),
        [p.code for p in positions], "info", ["持仓穿透", "集中度"], "全部持仓基金",
    )


def render_manager(changes: list, positions: list) -> dict:
    """P0-2 基金经理变更文案（多项变更时详述第一项，其余提示见前端）。"""
    if not changes:
        return None
    ch = changes[0]
    code = ch["code"]
    fname = ch["fund_name"]
    w = next((p.weight_mv for p in positions if p.code == code), 0.0)
    dep, joi = ch.get("departed"), ch.get("joined")

    lines = [f"你的持仓：{fname}({code})，占你总净值 {w:.1f}%"]
    if dep and joi:
        title = f"👔 基金经理变更｜{fname}({code}) 换人了"
        lines.append(f"离任：{dep['name']}（任职 {dep['begin_date']} 至 {dep['end_date']}，共 {_tenure(dep)} 年）")
        lines.append(f"接任：{joi['name']}（自 {joi['begin_date']} 起）")
        ann = dep.get("ann_date") or joi.get("ann_date") or ""
        lag = _lag(ann)
        lines.append(f"公告日：{ann}" + (f"（来源 Tushare fund_manager，滞后 {lag} 天）" if ann else ""))
    elif dep:
        title = f"👔 基金经理离任｜{fname}({code})"
        lines.append(f"离任：{dep['name']}（任职 {dep['begin_date']} 至 {dep['end_date']}，共 {_tenure(dep)} 年）")
        lines.append(f"公告日：{dep.get('ann_date', '')}（来源 Tushare fund_manager）")
    else:
        title = f"👔 基金经理新任｜{fname}({code})"
        lines.append(f"新任：{joi['name']}（自 {joi['begin_date']} 起）")
        lines.append(f"公告日：{joi.get('ann_date', '')}（来源 Tushare fund_manager）")

    if len(changes) > 1:
        lines.append(f"另有 {len(changes) - 1} 项经理变更见前端")
    lines.append("主动管理基金的核心变量是基金经理本人，建议复核新任经理历史业绩与投资风格。")

    return _signal(
        "fund_manager_change", title, "\n".join(lines),
        [code], "warning", ["基金经理", "变更"], fname,
    )


def render_drawdown(items: list, positions: list) -> dict:
    """P0-3 回撤档位文案（单只详述 / 多只按天合并取 Top3）。"""
    if not items:
        return None
    items = sorted(items, key=lambda x: x.get("dd_cost_pct", 0))  # dd 升序（最深在前）
    top3 = items[:3]
    codes = [i["code"] for i in items]
    has_qdii = any(i.get("is_qdii") for i in items)

    def rung_pct(it: dict) -> float:
        r = it.get("rung", -1)
        return abs(DRAWDOWN_RUNGS[r]) if 0 <= r < len(DRAWDOWN_RUNGS) else 0.0

    if len(items) == 1:
        it = items[0]
        title = f"📉 回撤档位｜{it['name']}({it['code']}) 相对成本跌破 -{rung_pct(it):.0f}%"
    else:
        deepest = items[0]
        title = f"📉 回撤档位｜{len(items)} 只基金同日跌破新档位，最深相对成本 {deepest['dd_cost_pct']:.1f}%"

    lag_note = "（QDII 净值滞后 2 天）" if has_qdii else ""
    lines = [f"净值日 {items[0].get('nav_date', '')}{lag_note}"]

    if len(items) == 1:
        it = items[0]
        lines.append(f"{it['name']}({it['code']})｜占你总净值 {it['weight_mv']:.1f}%")
        lines.append(
            f"相对你的成本净值 {it['dd_cost_pct']:.1f}% ← 触发口径｜"
            f"成本净值 {it['cost_nav']} → 单位净值 {it['unit_nav']}"
        )
        if it.get("dd_roll_pct") is not None:
            lines.append(
                f"相对近 60 日高点 {it['dd_roll_pct']:.1f}%"
                f"（{it.get('high_date', '')} 高点 {it.get('high_unit_nav')}）← 参考口径"
            )
        rest = [p for p in positions if p.code not in codes]
        if rest:
            lines.append(f"其余 {len(rest)} 只均在 -20% 档外，明细见前端")
    else:
        for it in top3:
            lines.append(
                f"{it['name']}({it['code']}) 相对成本 {it['dd_cost_pct']:.1f}%"
                f"｜占你总净值 {it['weight_mv']:.1f}%｜跌破 -{rung_pct(it):.0f}% 档"
            )
        if len(items) > 3:
            lines.append(f"（其余 {len(items) - 3} 只见前端）")

    lines.append("抑制规则：每档只推一次，回升 5pct 才重置")
    related = items[0]["name"] if len(items) == 1 else ""
    return _signal("fund_drawdown_rung", title, "\n".join(lines), codes,
                   "warning", ["回撤", "风险"], related)


def render_dca(snap: dict, positions: list) -> dict:
    """P1-1 定投前瞻文案。"""
    if not snap:
        return None
    total = len(positions)
    combo = snap.get("combo_cost_pct")
    combo_s = f"{combo:.1f}%" if combo is not None else "-"
    gainers = snap.get("gainers") or []
    losers = snap.get("losers") or []
    deepest = snap.get("deepest")
    best = snap.get("best")
    xray = snap.get("xray")

    pay_day = DCA_TRIGGER_DAY + 1
    title = (f"🗓️ 定投前瞻｜{pay_day} 号扣款，组合相对成本 {combo_s}，"
             f"{len(gainers)} 浮盈 {len(losers)} 浮亏")

    lines = [f"数据截止 {_fmt_md(snap.get('nav_date', ''))}（QDII 滞后 2 天）",
             f"组合整体：相对成本净值 {combo_s}（{total} 只基金按市值加权）"]

    if deepest:
        d = deepest
        d_s = f"{d['dd_cost_pct']:.1f}%" if d.get("dd_cost_pct") is not None else "-"
        lines.append(f"回撤最深：{d['name']}({d['code']}) {d_s}，占你总净值 {d['weight_mv']:.1f}%")
    if best:
        b = best
        b_s = f"{b['dd_cost_pct']:+.1f}%" if b.get("dd_cost_pct") is not None else "-"
        lines.append(f"表现最好：{b['name']}({b['code']}) {b_s}，占你总净值 {b['weight_mv']:.1f}%")

    if xray is not None:
        cov = xray.coverage
        seg = " ｜ ".join(f"{i.industry} {i.exposure_pct:.1f}%" for i in xray.industries[:2])
        lines.append(f"集中度提醒（穿透覆盖 {cov.penetrated_pct:.1f}% 净值）：{seg}")

    if losers:
        seg = " ｜ ".join(
            f"{r['name']}({r['code']}) {r['cost_nav']}→{r['unit_nav']} {r['dd_cost_pct']:.1f}%"
            for r in losers[:3]
        )
        lines.append(f"浮亏排名：{seg}")

    lines.append("本期定投若仍投向科技成长，集中度将进一步上升")

    return _signal(
        "dca_preflight", title, "\n".join(lines),
        [p.code for p in positions], "info", ["定投", "前瞻"], "全部持仓基金",
    )
