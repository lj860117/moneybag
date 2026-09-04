"""
P0-2 基金经理变更（快照 diff + 离任/接任配对 + 30 天冷却）。

设计依据：docs/design/signal-scout-fund-account.md §3.4
依赖 B1：get_fund_manager() 返回 all_managers（含离任记录），否则 8 只基金
「在任经理」恒为 0，本采集器不可用。

diff 语义（关键）：
  * 记录键 = f"{name}|{begin_date}"（字符串化）—— 不含 end_date。这样
    「end_date 从空 → 非空」才能被正确识别为【离任】，而不是被误判成一条
    「新任」记录（若把 end_date 放进键里，同一个人离任会同时产生一条消失
    + 一条新增，配对会错）。
  * 键【必须】是字符串：状态要经 state.save() 走 json.dumps，而 JSON 不支持
    tuple key —— 用 tuple 会抛 TypeError 并被静默吞掉，快照永远落不了盘，
    冷启动恒为真，P0-2 永远推不出来。详见 _records() 注释。
  * 离任：end_date 从空变非空，或记录整体消失（罕见，数据回撤）。
  * 新任：出现全新记录且仍在任（end_date 为空）。
  * 冷却：只推 ann_date 在最近 30 天内的记录（防历史回填误报）。
  * 配对：同基金「离任 end_date」与「新任 begin_date」相差 ≤7 天 → 合成「离任→接任」。
  * 冷启动：快照不存在 → 只写不推。
"""
from datetime import datetime, timedelta

from services.fund_signal import state
from services.fund_signal.config import (
    MANAGER_COOLDOWN_DAYS,
    MANAGER_PAIRING_GAP_DAYS,
)


def _norm_date(value) -> str:
    """YYYYMMDD / YYYY-MM-DD → YYYY-MM-DD；脏值返回 ""。"""
    s = str(value or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    return ""


def _parse_date(s: str):
    """解析 YYYY-MM-DD → date；失败返回 None。"""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _days_apart(a: str, b: str):
    """两日期相差的绝对天数；任一解析失败返回 None。"""
    da, db = _parse_date(a), _parse_date(b)
    if da is None or db is None:
        return None
    return abs((da - db).days)


def _fetch(code: str) -> dict:
    """拉取单只基金的经理全量记录；任何异常返回空 dict（旁路，不阻断）。"""
    from services.tushare_data import get_fund_manager
    try:
        return get_fund_manager(code) or {}
    except Exception as e:
        print(f"[FUND_SIGNAL] get_fund_manager({code}) failed: {e}")
        return {}


def _records(rows) -> dict:
    """把 fund_manager 行转成 {"name|begin_date": record}。

    ⚠️ 键必须字符串化，不能用 tuple：state.save() 内部走 json.dumps，
    而 JSON 不支持 tuple key —— 用 tuple 会抛 TypeError 并被 save() 的
    except 静默吞掉（只 print 一行），结果是快照永远落不了盘、collect()
    每次都是冷启动、P0-2 基金经理变更永远推不出来。
    分隔符用 `|`：经理姓名与 YYYY-MM-DD 均不含该字符，不会产生歧义键。

    仅保留能定位身份的行（name 非空）；日期统一成 YYYY-MM-DD。
    """
    rec: dict = {}
    for r in rows or []:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        begin = _norm_date(r.get("begin_date"))
        key = f"{name}|{begin}"
        rec[key] = {
            "name": name,
            "begin_date": begin,
            "end_date": _norm_date(r.get("end_date")),
            "ann_date": _norm_date(r.get("ann_date")),
        }
    return rec


def _tenure_years(rec: dict) -> str:
    """离任经理任期年数（end_date - begin_date），脏数据返回 "-"。"""
    b, e = _parse_date(rec.get("begin_date", "")), _parse_date(rec.get("end_date", ""))
    if b is None or e is None:
        return "-"
    return f"{(e - b).days / 365:.1f}"


def collect(user_id: str, positions: list) -> list:
    """P0-2 采集。冷启动只写快照不推；否则 diff + 配对 + 冷却后出文案。"""
    snap = state.load(user_id, state.MANAGER_SNAPSHOT)
    prev_funds = (snap.get("funds") or {}) if isinstance(snap, dict) else {}
    is_cold = not prev_funds

    new_funds: dict = {}
    departures: list = []   # {"code","fund_name","rec"}
    arrivals: list = []     # {"code","fund_name","rec"}

    for p in positions:
        rows = _fetch(p.code).get("all_managers") or []
        curr = _records(rows)
        prev = prev_funds.get(p.code, {})

        for key, prev_rec in prev.items():
            curr_rec = curr.get(key)
            if curr_rec is None or (not prev_rec.get("end_date") and curr_rec.get("end_date")):
                # 记录整体消失，或 end_date 从空 → 非空：均为离任
                departures.append({"code": p.code, "fund_name": p.name,
                                   "rec": curr_rec or prev_rec})
        for key, curr_rec in curr.items():
            if key not in prev and not curr_rec.get("end_date"):
                # 全新记录且仍在任：新任
                arrivals.append({"code": p.code, "fund_name": p.name, "rec": curr_rec})

        new_funds[p.code] = curr

    # 先落新快照（无论是否冷启动），保证下一次 diff 基线正确。
    state.save(user_id, state.MANAGER_SNAPSHOT, {"funds": new_funds})

    if is_cold:
        return []  # 冷启动静默：只写不推

    # ---- 30 天冷却：只保留 ann_date 在最近 30 天内的记录 ----
    cutoff = datetime.now().date() - timedelta(days=MANAGER_COOLDOWN_DAYS)

    def _fresh(rec: dict) -> bool:
        ann = _parse_date(rec.get("ann_date", ""))
        return ann is not None and ann >= cutoff

    departures = [d for d in departures if _fresh(d["rec"])]
    arrivals = [a for a in arrivals if _fresh(a["rec"])]
    if not departures and not arrivals:
        return []

    # ---- 配对：离任 end_date ↔ 接任 begin_date 相差 ≤7 天 ----
    used_arrivals: set = set()
    changes: list = []
    for d in departures:
        mate = None
        for i, a in enumerate(arrivals):
            if i in used_arrivals or a["code"] != d["code"]:
                continue
            gap = _days_apart(d["rec"].get("end_date", ""), a["rec"].get("begin_date", ""))
            if gap is not None and gap <= MANAGER_PAIRING_GAP_DAYS:
                mate = (i, a)
                break
        if mate is not None:
            changes.append({"code": d["code"], "fund_name": d["fund_name"],
                            "departed": d["rec"], "joined": mate[1]["rec"]})
            used_arrivals.add(mate[0])
        else:
            changes.append({"code": d["code"], "fund_name": d["fund_name"],
                            "departed": d["rec"], "joined": None})
    for i, a in enumerate(arrivals):
        if i not in used_arrivals:
            changes.append({"code": a["code"], "fund_name": a["fund_name"],
                            "departed": None, "joined": a["rec"]})

    from services.fund_signal.render import render_manager
    sig = render_manager(changes, positions)
    return [sig] if sig else []
