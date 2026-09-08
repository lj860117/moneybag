#!/usr/bin/env python3
"""
钱袋子 — 运行态势快照汇总（元巡检）
=====================================
定位：不是再跑一遍数据源探针（那是 self_audit.py 的职责），而是
     「看守巡检官本身」——把散落在各处的巡检产物，按「新鲜度」汇总
     成一份结构化快照，暴露「哪个巡检自己失效了」。

背景（2026-09-06 调研发现）：
  - data/health/ 停在 2026-06-15（数据源巡检 3 个月未产出）
  - data/audit/latest.json mtime 停在 06-28（但 systemd timer 显示它在跑）
  - qwen 模型欠费（Arrearage）散在 llm_balance_monitor.log 里无人汇总
  → 痛点不是「缺监控」，而是「监控的产物散落、且没人看监控是否还活着」。

职责（纯规则，零 LLM，零联网）：
  1. 汇总各巡检产物/日志的「最近更新时间」→ 算出 stale_days（失效天数）
  2. 汇总各 cron 的「退出码 + 最近运行时间」（从日志 mtime + 内容推断）
  3. 抓取散落的失效证据：LLM 余额告警、qwen 欠费、watchdog 杀进程、磁盘占用
  4. 落盘 data/ops/snapshot_{date}.json（原子写，符合铁律 M4）

用法：
  cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/ops_summary.py

cron（建议 08:03，紧跟 night_worker 之后）：
  3 8 * * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env \
    && set +a && /opt/moneybag/venv/bin/python scripts/ops_summary.py \
    >> /var/log/moneybag/ops_summary.log 2>&1
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional

# 确保能 import 项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR  # noqa: E402

# 复用项目的原子写（铁律 M4）。persistence 在 services 下，脚本可直接 import。
try:
    from services.persistence import atomic_write_json
except Exception:  # pragma: no cover - 兜底，避免 import 失败阻断
    atomic_write_json = None

# 快照落盘目录
OPS_DIR = DATA_DIR / "ops"
OPS_DIR.mkdir(parents=True, exist_ok=True)

# 磁盘告警阈值（GB）
DISK_WARN_GB = 5.0

# ── 数据目录漂移修复 ─────────────────────────────────────────
# 2026-09-06 发现：实际存在两个并行 data 目录——
#   /opt/moneybag/data/          （config.DATA_DIR + systemd 注入，权威）
#   /opt/moneybag/backend/data/  （历史遗留，部分 cron 仍在写入）
# health/audit/llm_usage 等产物被写散在两处。本脚本同时扫描两处取最新。
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_LEGACY_DATA_DIR = _BACKEND_DIR / "data"


def _candidate_dirs(sub: str) -> list[Path]:
    """返回某个子目录在权威 + 历史两处的候选路径（去重、存在才保留）。"""
    dirs: list[Path] = []
    for base in (DATA_DIR, _LEGACY_DATA_DIR):
        p = base / sub
        if p.exists() and p not in dirs:
            dirs.append(p)
    return dirs


# 各巡检产物的「新鲜度」清单：(名称, 子目录, glob, 预期最大 stale 天数)
# 超过 stale 天数即标记 ok=False，表示该巡检链可能已失效。
# path 改为子目录名，由 _candidate_dirs 展开成两处候选。
FRESHNESS_CHECKS: list[dict[str, Any]] = [
    {
        "name": "数据源健康巡检",
        "sub": "health",
        "glob": "*.json",
        "exclude": "_last_alert.json",
        "max_stale_days": 1,
    },
    {
        "name": "周度自检",
        "sub": "audit",
        "glob": "latest.json",
        "exclude": None,
        "max_stale_days": 7,
    },
    {
        "name": "LLM 用量",
        "sub": "llm_usage",
        "glob": f"{date.today().isoformat()}.json",
        "exclude": None,
        "max_stale_days": 1,
    },
    {
        "name": "余额监控",
        "sub": "logs",
        "glob": "llm_balance_monitor.log",
        "exclude": None,
        "max_stale_days": 1,
        # 余额监控日志在 backend/logs/（非 data 子目录），额外追加该目录
        "extra_dirs": [_BACKEND_DIR / "logs"],
    },
]


def _newest_mtime(dirs: list[Path], glob: str, exclude: str | None) -> tuple[str | None, float | None]:
    """返回多个目录下匹配 glob 的最新文件 mtime（ISO 字符串 + epoch 秒）。"""
    candidates: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        candidates += [p for p in d.glob(glob) if p.is_file()]
    if exclude:
        candidates = [p for p in candidates if p.name != exclude]
    if not candidates:
        return None, None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    mtime = newest.stat().st_mtime
    return datetime.fromtimestamp(mtime).isoformat(), mtime


def _stale_days(mtime: float | None) -> int | None:
    if mtime is None:
        return None
    return (datetime.now() - datetime.fromtimestamp(mtime)).days


def collect_freshness() -> list[dict[str, Any]]:
    """汇总各巡检产物的新鲜度（同时扫描权威 + 历史两处 data 目录）。"""
    rows: list[dict[str, Any]] = []
    for chk in FRESHNESS_CHECKS:
        dirs = _candidate_dirs(chk["sub"])
        dirs += chk.get("extra_dirs", [])
        iso, mtime = _newest_mtime(dirs, chk["glob"], chk["exclude"])
        stale = _stale_days(mtime)
        ok = (stale is not None) and (stale <= chk["max_stale_days"])
        rows.append({
            "name": chk["name"],
            "last_updated": iso,
            "stale_days": stale,
            "max_stale_days": chk["max_stale_days"],
            "ok": ok,
        })
    return rows


def collect_disk() -> dict[str, Any]:
    """磁盘占用（根分区）。"""
    total, used, free = shutil.disk_usage("/")
    free_gb = free / (1024 ** 3)
    return {
        "total_gb": round(total / (1024 ** 3), 1),
        "used_gb": round(used / (1024 ** 3), 1),
        "free_gb": round(free_gb, 1),
        "ok": free_gb > DISK_WARN_GB,
    }


def collect_llm_balance() -> dict[str, Any]:
    """抓取 LLM 余额监控日志里的关键信号（余额 + 欠费）。"""
    result: dict[str, Any] = {
        "checked": False,
        "balances": {},   # {provider: 余额字符串}
        "arrears": [],    # 欠费的 provider 列表
    }
    # 余额监控日志在 backend/logs/（相对脚本），需同时扫描两处 data/logs
    candidates = _candidate_dirs("logs") + [_BACKEND_DIR / "logs"]
    log_files = [d / "llm_balance_monitor.log" for d in candidates if (d / "llm_balance_monitor.log").exists()]
    if not log_files:
        return result
    result["checked"] = True
    # 取最新一份日志
    log_file = max(log_files, key=lambda p: p.stat().st_mtime)
    text = log_file.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if "当前余额" in line:
            # 例：[INFO] [deepseek] 当前余额: ¥31.15（阈值 ¥10.00）
            # provider 出现在最后一个 [xxx] 块（紧邻「当前余额」前）
            try:
                prefix = line.split("当前余额:")[0]
                # 取最后一个方括号里的 token
                provider = prefix.rstrip().rsplit("[", 1)[-1].rstrip("]").strip()
                balance = line.split("当前余额:")[1].split("（")[0].strip()
                result["balances"][provider] = balance
            except Exception:
                pass
        if "Arrearage" in line or "overdue-payment" in line:
            # 例：[WARNING] [qwen] 可用性探测返回 400: ... Arrearage ...
            # 欠费 provider 是「Arrearage/探测」所在行的 provider 名（qwen），
            # 从 [provider] 中抓：取所有 [xxx] 块，跳过日志级别关键字。
            try:
                import re
                blocks = re.findall(r"\[([^\]]+)\]", line)
                for b in blocks:
                    if b in ("INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"):
                        continue
                    if b not in result["arrears"]:
                        result["arrears"].append(b)
            except Exception:
                pass
    return result


# 行首方括号前缀：只剥「时间戳/日期」与「日志级别」这两类无业务语义的前缀。
# ⚠️ 不能无差别地剥 `[xxx]`：行首方括号也可能是业务标签（如 `[保守型]` / `[LeiJiang]`），
# 一律剥掉会让不同 profile 的同型错误塌缩成同一个指纹 —— 那是「过度去重」，
# 比虚高更危险：告警会从 critical 直接变绿，而真故障还在。
_BRACKET_PREFIX_RE = re.compile(r"^\[([^\]]*)\]\s*")
# 时间：`HH:MM` / `HH:MM:SS`，可选小数秒与时区后缀
_TS_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?([.,]\d{1,6})?(Z|[+-]\d{2}:?\d{2})?$")
# 日期：`YYYY-MM-DD`，可选分隔符变体与后面的时间部分
_TS_DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}([ T].*)?$")
# ⚠️ 不要用「以数字开头 + 只含数字与分隔符」这种宽松口径判定时间戳：
# 它会把 `[1/4]` 这类**进度计数**误判成时间戳并剥掉，导致
# `[1/4] ❌ 拉取失败` 与 `[2/4] ❌ 拉取失败` 塌缩成同一个指纹 ——
# 静默丢告警（告警变绿而故障还在），是比虚高危险得多的方向。
# 真实 cron.log 里确有 `[1/4]…[4/4]` 进度行，随时可能写出 `[1/4] ❌ xxx`。
# 日志级别白名单（大小写不敏感）
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"})
# 前缀剥离的防御性上界：时间戳 + 日期 + 级别各一层已绰绰有余。
# 说明：真正的终止性来自「正则每次至少消耗 2 个字符（`[]`）」+「剥空即停」
# 这两个条件，这个 6 只是**兜底护栏** —— 防止未来有人把正则改成可匹配空串
# （例如 `^\[[^\]]*?\]`）时退化成无限循环。别把它理解成终止性的唯一保证。
_MAX_PREFIX_BRACKETS = 6


def _is_strippable_bracket(content: str) -> bool:
    """判断行首方括号里的内容是否属于可剥掉的「时间戳 / 日期 / 日志级别」。

    Args:
        content: 方括号内的文本（不含方括号本身）。

    Returns:
        True：属无业务语义的日志前缀，可以剥掉后再取指纹。
        False：可能是业务标签（如 `保守型`、`LeiJiang`），必须原样保留进指纹。
    """
    c = (content or "").strip()
    if not c:
        return False
    if _TS_TIME_RE.match(c) or _TS_DATE_RE.match(c):
        return True
    return c.upper() in _LOG_LEVELS


def _error_fingerprint(line: str) -> str:
    """错误行指纹：剥掉行首「时间戳 / 日志级别」方括号，只留错误本体。

    ⚠️ 为什么必须去重（2026-09-08 事故根因）：
    night_worker 的 stderr 被同时写进 `2026-09-07.log` 和 `cron.log`，两份日志
    内容逐字相同；legacy 目录 `backend/data/night_worker/` 里还躺着同款历史错误。
    旧实现按「文件 × 行」累加，一条真实错误被数成 2~3 条 —— 15 条 ALLOC_PCTS
    被报成 30 条、24h 计数虚高到 35，直接把日报顶到 critical（阈值 ≥10），
    而**真实独立故障只有 4 个**。

    指纹取「去掉时间戳/级别前缀后的错误文本」，因此：
    - 同一批错误出现在多份日志 → 指纹相同 → 只计 1 条
    - 不同 profile / 资产的同型错误（保守型/fund vs 保守型/stock）→ 指纹不同 → 各自计数
    - 行首是业务标签（`[保守型] ❌ X` vs `[激进型] ❌ X`）→ 标签保留 → 指纹不同 → 各自计数

    Args:
        line: 原始日志行，允许为空。

    Returns:
        用于跨文件去重的指纹字符串；空行返回空串。
    """
    s = (line or "").strip()
    for _ in range(_MAX_PREFIX_BRACKETS):
        m = _BRACKET_PREFIX_RE.match(s)
        if not m:
            break  # 没有方括号前缀了
        if not _is_strippable_bracket(m.group(1)):
            break  # 业务标签，保留（防过度去重）
        rest = s[m.end():].strip()
        if not rest:
            break  # 整行只有时间戳，保留原行做指纹
        s = rest
    return s if s else (line or "").strip()


# ── 根因归一口径（「独立根因数」用）────────────────────────────
# 背景：一个根因会扇出成很多条错误 —— 例如 `ALLOC_PCTS` 一个 NameError，
# 在 5 档风险 × 3 类资产上各报一次就是 15 条。去重只砍掉了日志 tee 造成的
# 重复，砍不掉这种 fan-out，所以日报阈值仍会被 15 条顶到 critical。
# 根因指纹在错误指纹基础上再抹掉两类**维度**信息（档位 / 资产），让同源
# fan-out 收敛成 1 个根因；阈值改按根因数判定，日报同时报两个数字。
#
# ⚠️ 严禁抹掉异常类型、变量名、模块标签（DATA_SOURCE/MARKET、TUSHARE、
# STOCK_PROVIDER、CONFIG … 真实日志里有 40+ 种）。抹掉它们就是过度去重 ——
# 不同故障会被洗成同一个根因，告警直接变绿而故障还在。
# 风险档位词（进取型/成长型 两种叫法都收，兼容不同时期的命名）
_PROFILE_WORDS: tuple[str, ...] = ("保守型", "稳健型", "平衡型", "进取型", "成长型", "激进型")
# 资产类型词
_ASSET_WORDS: tuple[str, ...] = ("fund", "stock", "mixed")

_PROFILE_RE = re.compile("|".join(re.escape(w) for w in _PROFILE_WORDS))
# 资产词只匹配**独立成词**的情形（前后不是字母/数字/下划线）：
# 这样才能命中 `保守型/fund:` 里的 fund，又不会把 `get_stock_daily_hist`
# 里的 stock 吃掉 —— 否则 stock/fund 两类数据源的故障会被洗成同一个根因。
_ASSET_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(_ASSET_WORDS) + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_DIGITS_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def _root_cause_fingerprint(error_text: str) -> str:
    """把「错误指纹」再归一成「根因指纹」。

    在 `_error_fingerprint()` 的结果上再抹掉三类**不影响根因身份**的差异：
      1. 风险档位词（保守型 / 稳健型 / 平衡型 / 进取型 / 成长型 / 激进型）
      2. 资产类型（fund / stock / mixed，仅独立成词时）
      3. 行内数字（连续数字 → `#`），避免「重试 3 次」与「重试 5 次」被当两个根因

    **刻意保留**：异常类型、`NameError` 里的变量名、模块标签、函数名、股票代码
    归一化后的结构。这些是区分根因的关键，抹掉就是过度去重。

    Args:
        error_text: `_error_fingerprint()` 的返回值（已剥掉时间戳/级别前缀）。

    Returns:
        根因指纹字符串。
    """
    s = error_text or ""
    s = _PROFILE_RE.sub("", s)
    s = _ASSET_RE.sub("", s)
    s = _DIGITS_RE.sub("#", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


# 零值健康汇总行：`✅ 正常: 13    ❌ 异常: 0` 这类汇总行因为含 `❌` 被误算成错误。
# 只排除「值为 0」这一种形态 —— `❌ 异常: 15` 这类真报警必须照常计入。
# ⚠️ 这是「放宽计数」方向的改动，和过度去重是同一个滑坡，所以口径收到最窄：
# 只认 `❌` + 异常/错误/失败 + 冒号 + 0，不做通用汇总行排除。
_ZERO_SUMMARY_RE = re.compile(r"❌\s*(?:异常|错误|失败)\s*[:：]\s*0(?![0-9.])")


# ── 行内时间戳解析（24h 过滤口径）──────────────────────────────
# ⚠️ 为什么必须按「行内时间戳」而不是「文件 mtime」判 24h（2026-09-08 事故真根因）：
# `data/night_worker/cron.log` 是**按天追加**的：01:00 / 08:30 / 16:00 三个
# cron 都往同一个文件尾部追加。于是文件 mtime 永远是「今天早上 08:30」，
# 而文件里还躺着 09-07 01:00 的 15 条 ALLOC_PCTS、09-07 16:00 的 Traceback ——
# 它们早就该老化了，却因为文件被追加过一行而「永远新鲜」，日报被旧账顶到
# critical。改成行内时间戳后，旧行会自然老化。
#
# 只认两种**行首**形态，刻意不扫描行中间：
#   - `[HH:MM:SS] text` / `[YYYY-MM-DD HH:MM:SS] text`（方括号前缀）
#   - `YYYY-MM-DD HH:MM:SS,mmm - LEVEL - text`（python logging 裸时间戳）
# 行中间的日期（`date=20260904`、`daily_signal_2026-09-07.json`、
# `2026-09-07_briefing_LeiJiang.txt`）语义是**业务日期**不是写入时刻，
# 拿它当时钟会把旧错误洗成新的 —— 宁可解析不到走继承/兜底，也不猜错方向。
#
# 时区后缀直接丢弃：本机日志全是本地时间，硬做时区转换只会把 24h 窗口算错。
_TZ_SUFFIX_RE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)
# 完整时间戳 token：`YYYY-MM-DD` 后可跟 `[ T]HH:MM[:SS][.mmm][Z|±HH:MM]`
_DT_TOKEN_RE = re.compile(
    r"^(?P<date>\d{4}[-/.]\d{1,2}[-/.]\d{1,2})"
    r"(?:[ T](?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?))?$"
)


def _parse_datetime_token(token: str) -> Optional[datetime]:
    """把一个「带日期的时间戳」token 解析成 datetime。

    覆盖 `2026-09-08`、`2026-09-08 01:19:32`、`2026-09-08T01:19:32.123`、
    `2026/09/08 01:19:32+08:00` 四种写法；只有时刻没有日期的（`01:19:32`）
    返回 None —— 日期得由调用方补齐（见 `_line_timestamp`）。

    Args:
        token: 待解析的时间戳文本，允许为空/带前后空白。

    Returns:
        解析成功返回 naive datetime；失败（含非法日期如 2026-02-30）返回 None。
    """
    raw = (token or "").strip().rstrip(",;")
    if not raw:
        return None
    m = _DT_TOKEN_RE.match(raw)
    if not m:
        return None
    date_part = m.group("date").replace("/", "-").replace(".", "-")
    time_part = _TZ_SUFFIX_RE.sub("", (m.group("time") or "").strip())
    time_part = time_part.replace(",", ".")
    if "." in time_part:
        # 微秒对 24h 判定毫无意义，直接截断
        time_part = time_part.split(".", 1)[0]
    parts = time_part.split(":") if time_part else []
    while len(parts) < 3:
        parts.append("0")
    try:
        y, mo, d = (int(x) for x in date_part.split("-"))
        hh, mi, ss = (int(p) for p in parts[:3])
        return datetime(y, mo, d, hh, mi, ss)
    except (ValueError, TypeError):
        return None


def _parse_time_token(token: str) -> Optional[time]:
    """把 `HH:MM[:SS]` 解析成 time；不是时刻返回 None。

    刻意复用 `_TS_TIME_RE`（行首方括号判定的同一个正则），保证「指纹里
    剥得掉的前缀」与「这里解析得出的时刻」永远是同一口径 —— 否则会出现
    「时间戳被剥掉了、时刻却没解析出来、于是走了兜底」这类自相矛盾的行。

    Args:
        token: 方括号内的文本或独立 token。

    Returns:
        解析成功返回 time；失败（如 `[1/4]` 进度计数）返回 None。
    """
    raw = _TZ_SUFFIX_RE.sub("", (token or "").strip()).replace(",", ".")
    if "." in raw:
        raw = raw.split(".", 1)[0]
    if not _TS_TIME_RE.match(raw):
        return None
    parts = raw.split(":")
    while len(parts) < 3:
        parts.append("0")
    try:
        hh, mi, ss = (int(p) for p in parts[:3])
        return time(hh, mi, ss)
    except (ValueError, TypeError):
        return None


def _line_timestamp_ex(line: str, fallback_date: date) -> tuple[Optional[datetime], bool]:
    """解析一行日志的写入时刻，并告诉调用方「这个时刻是否自带日期」。

    优先级（命中即返回，不再往后看）：
      1. 行首方括号里是完整日期时间（`[2026-09-08 01:19:32]`）→ 自带日期
      2. 行首方括号里是纯时间（`[01:19:32]`）→ 用 `fallback_date` 补日期
      3. 行首是裸时间戳（`2026-09-08 01:19:32,123 - ERROR - …`）→ 自带日期
      4. 都没有 → `(None, False)`，交给调用方「继承上一行 / 回退文件 mtime」

    ⚠️ 第 2 条里「补出来的时刻落在未来」要减一天：按天追加的日志里，
    文件 mtime 是今天，行却可能是昨晚 23:50 写的。这个修正只在**未来**
    这一个方向上生效，减一天后必然仍落在 24h 窗口内，所以**不会**把窗口
    放宽、也不会把真错误洗掉。

    Args:
        line: 原始日志行。
        fallback_date: 行内只有时刻、没有日期时用来补齐的日期。

    Returns:
        `(时刻, 是否自带日期)`；解析不到时 `(None, False)`。
    """
    s = (line or "").strip()
    if not s:
        return None, False

    # 1 & 2：行首方括号前缀（时间戳 / 日期 / 日志级别），最多剥 _MAX_PREFIX_BRACKETS 层
    remainder = s
    for _ in range(_MAX_PREFIX_BRACKETS):
        m = _BRACKET_PREFIX_RE.match(remainder)
        if not m:
            break
        content = (m.group(1) or "").strip()
        if _TS_DATE_RE.match(content):
            dt = _parse_datetime_token(content)
            return (dt, True) if dt is not None else (None, False)
        if _TS_TIME_RE.match(content):
            t = _parse_time_token(content)
            if t is None:
                return None, False
            dt = datetime.combine(fallback_date, t)
            if dt > datetime.now():
                dt -= timedelta(days=1)
            return dt, False
        if content.upper() in _LOG_LEVELS:
            rest = remainder[m.end():].strip()
            if not rest:
                break
            remainder = rest
            continue
        # 业务标签（`[保守型]` / `[MACRO]` / `[1/4]`）：不是时间戳，停止剥
        break

    # 3：行首裸时间戳（python logging 风格），先试「日期 + 时刻」再试「只有日期」
    toks = s.split()
    for n in (2, 1):
        if len(toks) < n:
            continue
        cand = " ".join(toks[:n])
        if _TS_DATE_RE.match(cand):
            dt = _parse_datetime_token(cand)
            if dt is not None:
                return dt, True
    return None, False


def _line_timestamp(line: str, fallback_date: date) -> Optional[datetime]:
    """解析一行日志的写入时刻；解析不到返回 None。

    这是 `_line_timestamp_ex()` 的薄封装，保留它是因为「一行 → 一个时刻」
    才是外部（含回归测试）该依赖的口径；「是否自带日期」是推演日期用的
    内部信息，不该外泄。

    Args:
        line: 原始日志行。
        fallback_date: 行内只有时刻、没有日期时用来补齐的日期。

    Returns:
        该行的写入时刻；无法确定时返回 None。
    """
    return _line_timestamp_ex(line, fallback_date)[0]


def _resolve_line_times(lines: list[str], file_mtime: datetime) -> list[datetime]:
    """给文件里每一行定一个写入时刻 —— **倒序锚定推演**。

    为什么必须倒着走：按天追加的日志里时间只有 `HH:MM:SS`，日期信息只藏在
    「这个文件最后写到哪一天」（= mtime）。从文件末尾倒着往回推，锚点初始为
    mtime；遇到「时刻比锚点还晚」的行，说明它属于锚点的**前一天**，减一天
    并更新锚点。这样连 `08:30:30 → 08:30:02` 这种只回退 28 秒的跨天也能认
    出来（真实 cron.log 里 09-07 08:30 与 09-08 08:30 就差这 28 秒），
    09-07 01:00 的旧错误才不会被当成今天写的。

    没有时间戳的行**继承它下面最近一个已知时刻**：Traceback 的后续行
    （`File "…", line 21, in <module>`）本来就没有时刻，取「下一条日志的
    时刻」作为上界是**安全方向** —— 宁可算新一点（报出来），不可算旧一点
    （静默老化掉，告警变绿而故障还在）。

    **整份文件一行时间戳都没有**时，锚点从头到尾都是初始的 `file_mtime`，
    于是每一行都拿到文件 mtime —— 这正好就是「回退到按文件 mtime 整体判定」
    的语义（老文件整体跳过、新文件整体计入，与改动前口径一致），不需要
    额外的分支去实现。

    Args:
        lines: 文件的全部行。
        file_mtime: 文件修改时间，作为倒序推演的初始锚点。

    Returns:
        与 `lines` 等长的时刻列表（不会含 None）。
    """
    resolved: list[datetime] = [file_mtime] * len(lines)
    anchor = file_mtime
    for i in range(len(lines) - 1, -1, -1):
        ts, has_date = _line_timestamp_ex(lines[i], anchor.date())
        if ts is None:
            # 无时间戳：继承下面最近一个已知时刻（安全方向：偏新不偏旧）
            resolved[i] = anchor
            continue
        if ts > anchor and not has_date:
            # 时刻比锚点还晚 → 属于前一天。自带日期的行以行内日期为准，
            # 不参与这个修正（它写的是什么时间就是什么时间）。
            # 减一天后必然 < 锚点（ts 与锚点同日），不会死循环。
            ts -= timedelta(days=1)
        resolved[i] = ts
        anchor = ts
    return resolved


def _candidate_log_dirs() -> list[Path]:
    """待扫描的日志目录候选（系统日志目录 + 新老两处 data 目录）。

    单独抽成函数是为了**可测**：回归测试可以 monkeypatch 掉它，把扫描范围
    限制在 tmp_path 内，免得真实 `/var/log/moneybag` 里的线上日志混进断言。
    `DATA_DIR` / `_LEGACY_DATA_DIR` 是模块级名字，按被测时的值动态读取，
    所以测试用 monkeypatch.setattr 改它们也能生效。
    """
    return [
        Path("/var/log/moneybag"),
        DATA_DIR / "logs",
        _LEGACY_DATA_DIR / "logs",
        DATA_DIR / "night_worker",
        _LEGACY_DATA_DIR / "night_worker",
    ]


def collect_error_logs() -> dict[str, Any]:
    """扫描核心 cron 日志里 24h 内的错误/异常关键字。

    排除「正常容错重试」：`fetch attempt N failed: timed out, retry in Xs` 这类
    网络超时后自动重试是正常容错（重试成功即无碍），不应统计为错误。按行匹配，
    命中 failed/Failed 时若同行还含 retry/timeout/重试/超时 等容错标志则跳过。

    去重：同一条错误被 tee 进多份日志时只计一次（见 `_error_fingerprint`），
    `count_24h` 表示「独立错误条数」而非「错误行数」。

    同时排除「零值健康汇总行」：`✅ 正常: 13  ❌ 异常: 0` 这类汇总行含 `❌`
    但不是错误，仅当值为 0 时跳过（见 `_ZERO_SUMMARY_RE`）。

    24h 窗口按**行内时间戳**判定（见 `_resolve_line_times`），不按文件 mtime：
    cron.log 这类按天追加的文件 mtime 永远新鲜，按 mtime 判会让上周的错误
    永远算进 24h；只有整份文件都解析不到行内时间戳时才回退 mtime。

    返回两个计数，语义不同、缺一不可：
      - `count_24h`：独立错误条数（细节口径，能看出扇出规模）
      - `root_cause_count`：独立根因数（把同源 fan-out 收敛后的口径，
        日报阈值按它判定 —— 一个根因扇出 15 条不该直接顶到 critical）
    """
    log_dirs = _candidate_log_dirs()
    # 目录去重：DATA_DIR 与 _LEGACY_DATA_DIR 可能 resolve 到同一路径，
    # 否则同一份日志会被扫两遍、计数翻倍。
    _seen_dirs: set[str] = set()
    _uniq_dirs: list[Path] = []
    for _d in log_dirs:
        try:
            _key = str(_d.resolve())
        except Exception:
            _key = str(_d)
        if _key in _seen_dirs:
            continue
        _seen_dirs.add(_key)
        _uniq_dirs.append(_d)

    keywords = ("Traceback", "ERROR", "❌", "Exception", "failed", "Failed")
    # 容错重试标志：failed 行若同时含这些词，属正常超时重试，不记为错误
    retry_markers = ("retry", "timed out", "timeout", "重试", "超时")
    cutoff = datetime.now() - timedelta(hours=24)
    findings: list[dict[str, Any]] = []
    # 指纹 → findings 下标，用于跨文件去重（同一错误只占一个条目）
    _fp_index: dict[str, int] = {}
    # 根因指纹集合，用于统计「独立根因数」（同源 fan-out 收敛成 1 个）
    _rc_index: dict[str, int] = {}
    for log_dir in _uniq_dirs:
        if not log_dir.exists():
            continue
        for f in log_dir.rglob("*.log"):
            try:
                st = f.stat()
                file_mtime = datetime.fromtimestamp(st.st_mtime)
                text = f.read_text(encoding="utf-8", errors="ignore")
                # 按行匹配，才能精确排除「failed 但带 retry」的容错行
                lines = text.splitlines()
                line_times = _resolve_line_times(lines, file_mtime)
                for idx, line in enumerate(lines):
                    # 24h 过滤按**行内时间戳**判，不按文件 mtime：
                    # cron.log 这类按天追加的文件 mtime 永远新鲜，按 mtime 判
                    # 会让上周的错误永远算进 24h（2026-09-08 事故真根因）。
                    # 整份文件都解析不到行内时间戳时，`_resolve_line_times`
                    # 会让每行都拿回初始锚点 = 文件 mtime（见其文档），
                    # 与改动前口径一致，不放宽窗口。
                    if line_times[idx] < cutoff:
                        continue
                    # 零值健康汇总行（`✅ 正常: 13    ❌ 异常: 0`）→ 整行跳过，
                    # 只认「值为 0」这一种形态，非零的真报警照常计入
                    if _ZERO_SUMMARY_RE.search(line):
                        continue
                    for kw in keywords:
                        if kw in line:
                            # failed/Failed 且同行含容错重试标志 → 跳过
                            if kw.lower() == "failed" and any(m in line.lower() for m in retry_markers):
                                continue
                            fp = _error_fingerprint(line)
                            if fp in _fp_index:
                                # 同一条错误的另一个出处：只记来源，不重复计数。
                                # also_in 只记「别的文件」——同一文件内重复出现不记，避免噪音
                                _dup = findings[_fp_index[fp]]
                                _also = _dup.setdefault("also_in", [])
                                if str(f) not in _also and str(f) != _dup.get("file"):
                                    _also.append(str(f))
                                break
                            _fp_index[fp] = len(findings)
                            # 同源 fan-out（5 档风险 × 3 类资产）收敛成 1 个根因
                            _rc = _root_cause_fingerprint(fp)
                            if _rc not in _rc_index:
                                _rc_index[_rc] = len(_rc_index)
                            findings.append({
                                "file": str(f),
                                "keyword": kw,
                                # 保留原文（截断 200 字）供日报/人工审计：只报数字
                                # 说不出「是什么错误」，事后也无法验证去重对不对
                                "line": line.strip()[:200],
                            })
                            break
            except Exception:
                continue
    return {
        # 独立错误条数（跨文件去重后）
        "count_24h": len(findings),
        # 独立根因数（再把同源 fan-out 收敛后）—— 日报阈值按这个判
        "root_cause_count": len(_rc_index),
        "files": findings[:20],
    }


def build_snapshot() -> dict[str, Any]:
    """组装完整快照。"""
    freshness = collect_freshness()
    all_ok = all(r["ok"] for r in freshness)
    return {
        "date": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(),
        "freshness": freshness,
        "summary": {
            "checks": len(freshness),
            "stale_count": sum(1 for r in freshness if not r["ok"]),
            "overall_ok": all_ok,
        },
        "disk": collect_disk(),
        "llm_balance": collect_llm_balance(),
        "error_logs_24h": collect_error_logs(),
    }


def main() -> int:
    snapshot = build_snapshot()

    # 打印可读摘要
    print(f"🔍 运行态势快照 {snapshot['date']}")
    for r in snapshot["freshness"]:
        mark = "✅" if r["ok"] else "❌"
        stale = r["stale_days"] if r["stale_days"] is not None else "未知"
        print(f"  {mark} {r['name']}: 距今 {stale} 天（阈值 {r['max_stale_days']} 天）")
    print(f"  💾 磁盘剩余 {snapshot['disk']['free_gb']}GB"
          f"{'' if snapshot['disk']['ok'] else ' ⚠️ 低于阈值'}")
    if snapshot["llm_balance"]["arrears"]:
        print(f"  🚨 欠费 provider: {snapshot['llm_balance']['arrears']}")
    _el = snapshot["error_logs_24h"]
    print(f"  📝 24h 错误: {_el['count_24h']} 条独立错误 / {_el.get('root_cause_count', _el['count_24h'])} 个独立根因")

    # 落盘（原子写，铁律 M4）
    out_file = OPS_DIR / f"snapshot_{snapshot['date']}.json"
    if atomic_write_json is not None:
        atomic_write_json(out_file, snapshot)
    else:  # 兜底
        out_file.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"  📝 快照已写入: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
