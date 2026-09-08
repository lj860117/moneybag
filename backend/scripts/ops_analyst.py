#!/usr/bin/env python3
"""
钱袋子 — 二期「AI 运维巡检日报」
=================================
定位：基于一期 `ops_summary.py`（08:03 cron）落盘的结构化快照
     `DATA_DIR/ops/snapshot_{date}.json`，做两层判断：
       1. 确定性规则引擎 `rule_triage`（零 LLM，兜底）
       2. LLM 分析 `call_llm`（llm_heavy + prompts/ops_analyst.md）
     最终 `merge_verdict` 一票否决：final = max(rule, llm)，critical 绝不被降级。
     产物落盘 `DATA_DIR/ops/report_{date}.json`，推送企微（critical 立即推 + 日报）。

用法：
  cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/ops_analyst.py
  cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/ops_analyst.py --critical-only

cron（08:05，紧跟快照 08:03）：
  5 8 * * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env \
    && set +a && /opt/moneybag/venv/bin/python scripts/ops_analyst.py \
    >> /var/log/moneybag/ops_analyst.log 2>&1

工程铁律：
  M4  落盘一律 services.persistence.atomic_write_json，禁止裸 open().write()
  M5  LLM 返回解析一律 services.json_extract.extract_json_object（DeepSeek 会在 JSON 前后加解释文字）
  M8  prompt 模板独立 backend/prompts/ops_analyst.md，读入失败回退内置兜底
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# 确保能 import 项目模块（backend 目录）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

# 加载 .env（兜底；cron 已通过 `set -a && . .env` 注入，这里 setdefault 不覆盖已注入值）
_env_file = _BACKEND_DIR / ".env"
if _env_file.exists():
    try:
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
    except Exception:  # .env 读取失败不应阻断脚本
        pass

from config import (  # noqa: E402
    DATA_DIR,
    OPS_DIR_NAME,
    OPS_REPORT_USER_ID,
    OPS_BASELINE_FILE,
    OPS_CRITICAL_STATE_FILE,
    OPS_WINDOW_7D,
    OPS_WINDOW_30D,
    OPS_DISK_CRITICAL_GB,
    OPS_DISK_WARN_GB,
    OPS_ERROR_CRITICAL_COUNT,
    OPS_ERROR_WARN_COUNT,
    OPS_ROUTED_PROVIDERS,
    OPS_LLM_MODEL_TIER,
    OPS_LLM_MAX_TOKENS,
)
from services.persistence import atomic_write_json  # noqa: E402  (铁律 M4)
from services.json_extract import extract_json_object  # noqa: E402  (铁律 M5)


# ============================================================
# 常量
# ============================================================

# 严重度序：info < warn < critical
SEVERITY_ORDER = {"info": 0, "warn": 1, "critical": 2}
SEVERITY_EMOJI = {"info": "✅", "warn": "🟡", "critical": "🔴"}

DIMENSIONS = ("freshness", "disk", "llm_balance", "error_logs")
DIM_LABELS = {
    "freshness": "巡检新鲜度",
    "disk": "磁盘",
    "llm_balance": "余额·欠费",
    "error_logs": "24h 错误日志",
}

# 磁盘 30 日趋势判定的噪声容忍（GB）：首尾差值在此范围内视为 flat
_DISK_TREND_EPS_GB = 0.5

_PROMPT_PATH = _BACKEND_DIR / "prompts" / "ops_analyst.md"

# 内置兜底 prompt（铁律 M8：读不到模板文件时回退，保证脚本仍可运行）
_FALLBACK_PROMPT = """# 角色
你是钱袋子（MoneyBag）的运维巡检分析官，判断系统是否健康、要不要告警。

# 输出格式（必须严格 JSON，不要输出任何其他内容）
{
  "overall_verdict": "critical|warn|info",
  "summary": "一句话总结（≤60字）",
  "dimensions": [
    {"dimension": "freshness|disk|llm_balance|error_logs",
     "verdict": "critical|warn|info",
     "headline": "≤20字",
     "detail": "数据支撑（≤80字）",
     "trend": "7日/30日趋势（≤40字，数据不足写『数据不足，待积累』）"}
  ],
  "critical_items": [{"title": "≤30字", "action": "建议动作（≤50字）"}],
  "warn_items": [{"title": "≤30字", "action": "建议动作（≤50字）"}],
  "report_text": "给老板 LeiJiang 的日报正文（纯文本，用 🔴/🟡/✅ 标记严重度，不用 markdown 符号）"
}

# 原则
1. 数据说话，禁止编造快照里没有的指标（进程数、内存、DB 连接数、接口错误率等一律不存在）。
2. history_days < 7 时，趋势一律写「数据不足，待积累」。
3. overall_verdict 只能 >= rule.overall（info < warn < critical），不得把 critical 降级。
4. overall_verdict 取四维度最严重者。
5. 只输出 JSON。
"""


# ============================================================
# 纯函数（确定性逻辑，方便单测）
# ============================================================

def _severity_max(a: str, b: str) -> str:
    """返回两档严重度中的更严重者。未知档位按 info 兜底。"""
    a = a if a in SEVERITY_ORDER else "info"
    b = b if b in SEVERITY_ORDER else "info"
    return a if SEVERITY_ORDER[a] >= SEVERITY_ORDER[b] else b


def _safe_float(v: Any) -> float | None:
    """安全转 float；无法转换返回 None（区别于 0.0，避免误判）。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any, default: int = 0) -> int:
    """安全转 int；无法转换返回 default。"""
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _to_daily_point(snapshot: dict, default_date: str = "") -> dict:
    """把一份快照抽成基线滚动单元 DailyPoint（只读，不采集新指标）。"""
    freshness = snapshot.get("freshness") or []
    summary = snapshot.get("summary") or {}
    disk = snapshot.get("disk") or {}
    llm_balance = snapshot.get("llm_balance") or {}
    error_logs = snapshot.get("error_logs_24h") or {}
    return {
        "date": snapshot.get("date") or default_date,
        "stale_count": _safe_int(summary.get("stale_count")),
        "overall_ok": bool(summary.get("overall_ok", False)),
        "disk_free_gb": _safe_float(disk.get("free_gb")),
        "disk_ok": bool(disk.get("ok", False)),
        "arrears": list(llm_balance.get("arrears") or []),
        "error_count_24h": _safe_int(error_logs.get("count_24h")),
        # 老快照无此字段 → 回退到条数，趋势口径与判定口径保持一致
        "error_root_cause_24h": _safe_int(
            error_logs.get("root_cause_count", error_logs.get("count_24h"))
        ),
    }


def _load_prompt() -> str:
    """读取 ops_analyst.md；读不到/为空则回退内置兜底（铁律 M8）。"""
    try:
        if _PROMPT_PATH.exists():
            text = _PROMPT_PATH.read_text(encoding="utf-8")
            if text.strip():
                return text
    except Exception:
        pass
    return _FALLBACK_PROMPT


# ============================================================
# OpsAnalyst
# ============================================================

class OpsAnalyst:
    """二期运维巡检日报：基线引擎 + 规则告警 + LLM 分析 + 合并 + 渲染 + 推送。"""

    def __init__(self, ops_dir: Path | None = None, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self.ops_dir = Path(ops_dir) if ops_dir is not None else self.data_dir / OPS_DIR_NAME
        self.ops_dir.mkdir(parents=True, exist_ok=True)
        # 历史漂移目录（只读，不写入），与一期 ops_summary._candidate_dirs 一致
        self.legacy_ops_dir = _BACKEND_DIR / "data" / OPS_DIR_NAME
        self.baseline_path = self.ops_dir / OPS_BASELINE_FILE
        self.critical_state_path = self.ops_dir / OPS_CRITICAL_STATE_FILE

    # ---- 数据读取 ----

    def _candidate_ops_dirs(self) -> list[Path]:
        """权威 + 历史两处 ops 目录（去重、仅保留存在目录）。"""
        dirs: list[Path] = []
        for d in (self.ops_dir, self.legacy_ops_dir):
            if d is not None and d.exists() and d not in dirs:
                dirs.append(d)
        return dirs

    def _read_snapshot(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as e:
            print(f"[OPS_ANALYST] 快照读取失败 {path}: {e}")
            return None

    def _iter_snapshot_files(self):
        """yield (文件名日期, 路径)，扫描两处 snapshot_*.json。"""
        seen: set[Path] = set()
        for d in self._candidate_ops_dirs():
            for p in sorted(d.glob("snapshot_*.json")):
                if not p.is_file() or p in seen:
                    continue
                seen.add(p)
                date_str = p.name[len("snapshot_"):-len(".json")]
                yield date_str, p

    def load_latest_snapshot(self) -> dict | None:
        """读今日快照；缺失则回退 glob 最新一份；都无则返回 None。"""
        today_str = date.today().isoformat()
        for d in self._candidate_ops_dirs():
            snap = self._read_snapshot(d / f"snapshot_{today_str}.json")
            if snap is not None:
                return snap
        newest: Path | None = None
        for _, p in self._iter_snapshot_files():
            if newest is None or p.stat().st_mtime > newest.stat().st_mtime:
                newest = p
        return self._read_snapshot(newest) if newest is not None else None

    def build_history(self) -> list[dict]:
        """glob 快照自愈重建基线：按 date 升序、去重、最多保留最近 30 天。"""
        by_date: dict[str, dict] = {}
        for filename_date, p in self._iter_snapshot_files():
            snap = self._read_snapshot(p)
            if snap is None:
                continue
            dp = _to_daily_point(snap, default_date=filename_date)
            if not dp["date"]:
                continue
            by_date[dp["date"]] = dp
        days = [by_date[d] for d in sorted(by_date)]
        return days[-OPS_WINDOW_30D:]

    # ---- 基线读写（派生缓存，原子写）----

    def load_baseline(self) -> dict:
        if self.baseline_path.exists():
            try:
                data = json.loads(self.baseline_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("days"), list):
                    return data
            except Exception:
                pass
        return {"updated_at": None, "days": []}

    def save_baseline(self, baseline: dict) -> None:
        baseline["updated_at"] = datetime.now().isoformat()
        atomic_write_json(self.baseline_path, baseline)

    # ---- 派生事实 + 规则告警 ----

    def _window_with_today(self, history: list[dict], today_point: dict, window: int) -> list[dict]:
        """取最近 window 天的 DailyPoint，并确保 today 被并入（按 date 去重）。"""
        base = list(history)[-window:]
        merged: dict[str, dict] = {d.get("date"): d for d in base if d.get("date")}
        merged[today_point["date"]] = today_point
        ordered = [merged[d] for d in sorted(merged)]
        return ordered[-window:]

    def _disk_trend(self, days_30: list[dict], history_days: int) -> str:
        """30 日磁盘趋势：up | down | flat | insufficient。"""
        if history_days < OPS_WINDOW_7D:
            return "insufficient"
        vals = [d for d in days_30 if d.get("disk_free_gb") is not None]
        if len(vals) < 2:
            return "insufficient"
        first = float(vals[0]["disk_free_gb"])
        last = float(vals[-1]["disk_free_gb"])
        if last > first + _DISK_TREND_EPS_GB:
            return "up"
        if last < first - _DISK_TREND_EPS_GB:
            return "down"
        return "flat"

    def compute_context(self, today: dict, history: list[dict]) -> dict:
        """脚本先算派生事实（derived），LLM 只做判断（幻觉治理）。"""
        today_point = _to_daily_point(today, default_date=date.today().isoformat())
        days_7 = self._window_with_today(history, today_point, OPS_WINDOW_7D)
        days_30 = self._window_with_today(history, today_point, OPS_WINDOW_30D)

        disk_now = today_point["disk_free_gb"]
        disk_7d_vals = [d["disk_free_gb"] for d in days_7 if d["disk_free_gb"] is not None]
        disk_7d_min = min(disk_7d_vals) if disk_7d_vals else (disk_now if disk_now is not None else 0.0)
        disk_7d_max = max(disk_7d_vals) if disk_7d_vals else (disk_now if disk_now is not None else 0.0)

        stale_now = today_point["stale_count"]
        stale_7d_vals = [d["stale_count"] for d in days_7]
        stale_7d_avg = round(sum(stale_7d_vals) / len(stale_7d_vals), 1) if stale_7d_vals else float(stale_now)

        stale_names_now = [
            r.get("name", "") for r in (today.get("freshness") or []) if not r.get("ok", False)
        ]

        arrears_now = today_point["arrears"]
        prior = [d for d in history if d.get("date", "") < today_point["date"]]
        prior_arrears = {a for d in prior for a in (d.get("arrears") or [])}
        arrears_new_7d = bool(arrears_now) and any(a not in prior_arrears for a in arrears_now)

        error_now = today_point["error_count_24h"]
        error_7d_total = sum(d["error_count_24h"] for d in days_7)

        derived = {
            "history_days": len(history),
            "disk_free_gb_now": disk_now,
            "disk_free_gb_7d_min": disk_7d_min,
            "disk_free_gb_7d_max": disk_7d_max,
            "disk_trend_30d": self._disk_trend(days_30, len(history)),
            "stale_count_now": stale_now,
            "stale_count_7d_avg": stale_7d_avg,
            "stale_names_now": stale_names_now,
            "arrears_now": arrears_now,
            "arrears_new_7d": arrears_new_7d,
            "error_count_now": error_now,
            "error_count_7d_total": error_7d_total,
        }

        return {
            "today": today,
            "history": {"days_7": days_7, "days_30": days_30},
            "derived": derived,
            "rule": {},
        }

    def rule_triage(self, today: dict, context: dict | None = None) -> dict:
        """确定性规则引擎兜底（零 LLM）。freshness 封顶 warn，是否 critical 交 LLM。"""
        per_dim: dict[str, str] = {}
        reasons: list[str] = []

        # disk：critical ≤5GB / warn ≤10GB
        disk = today.get("disk") or {}
        free_gb = _safe_float(disk.get("free_gb"))
        if free_gb is None:
            disk_v = "info"
        elif free_gb <= OPS_DISK_CRITICAL_GB:
            disk_v = "critical"
            reasons.append(f"磁盘剩余 {free_gb:.1f}GB ≤ {OPS_DISK_CRITICAL_GB}GB 致命阈值")
        elif free_gb <= OPS_DISK_WARN_GB:
            disk_v = "warn"
            reasons.append(f"磁盘剩余 {free_gb:.1f}GB ≤ {OPS_DISK_WARN_GB}GB 警告阈值")
        else:
            disk_v = "info"
        per_dim["disk"] = disk_v

        # llm_balance：主路由模型(deepseek/doubao)欠费=critical；仅 qwen 等=warn
        llm_balance = today.get("llm_balance") or {}
        arrears = list(llm_balance.get("arrears") or [])
        checked = bool(llm_balance.get("checked", False))
        routed = [a for a in arrears if a in OPS_ROUTED_PROVIDERS]
        if routed:
            lb_v = "critical"
            reasons.append(f"主路由模型欠费：{', '.join(routed)}")
        elif arrears:
            lb_v = "warn"
            reasons.append(f"非路由模型欠费：{', '.join(arrears)}")
        elif not checked:
            lb_v = "warn"
            reasons.append("LLM 余额检查未完成（checked=false）")
        else:
            lb_v = "info"
        per_dim["llm_balance"] = lb_v

        # error_logs：critical ≥10 / warn ≥3；含 Traceback/Exception 上浮一档
        # ⚠️ 阈值按**独立根因数**判定，不按独立错误条数：
        # 一个根因（如 ALLOC_PCTS NameError）会在 5 档风险 × 3 类资产上扇出
        # 15 条，按条数判会把它顶成 critical，而真实故障只有 1 个。
        # 两个数字都在日报正文里显示，细节不丢。
        error_logs = today.get("error_logs_24h") or {}
        count = _safe_int(error_logs.get("count_24h"))
        # 老快照没有 root_cause_count 字段 → 回退到条数，避免误判成 0 而假绿
        _rc_raw = error_logs.get("root_cause_count")
        root_cause_count = _safe_int(count if _rc_raw is None else _rc_raw)
        files = error_logs.get("files") or []
        has_traceback = any(
            (f.get("keyword") or "") in ("Traceback", "Exception") for f in files
        )
        if root_cause_count >= OPS_ERROR_CRITICAL_COUNT:
            err_v = "critical"
        elif root_cause_count >= OPS_ERROR_WARN_COUNT:
            err_v = "warn"
        else:
            err_v = "info"
        if has_traceback:
            if err_v == "info":
                err_v = "warn"
            elif err_v == "warn":
                err_v = "critical"
        # 双指标展示：既看得到扇出规模（条数），也不被扇出数字吓到（根因数）
        _err_brief = f"{count} 条独立错误 / {root_cause_count} 个独立根因"
        if err_v == "critical":
            reasons.append(
                f"24h 错误日志 {_err_brief}" + ("（含 Traceback/Exception）" if has_traceback else f" ≥ {OPS_ERROR_CRITICAL_COUNT} 个根因")
            )
        elif err_v == "warn":
            reasons.append(
                f"24h 错误日志 {_err_brief}（阈值 {OPS_ERROR_WARN_COUNT} 个根因）" + ("，含 Traceback/Exception" if has_traceback else "")
            )
        per_dim["error_logs"] = err_v

        # freshness：任一项 ok=false → warn（规则引擎封顶 warn，绝不 critical）
        freshness = today.get("freshness") or []
        stale_items = [r for r in freshness if not r.get("ok", False)]
        if stale_items:
            fresh_v = "warn"
            for r in stale_items:
                name = r.get("name", "未知巡检")
                stale = r.get("stale_days")
                maxd = r.get("max_stale_days")
                stale_txt = "未知" if stale is None else str(stale)
                reasons.append(f"{name}失效 {stale_txt} 天（阈值 {maxd} 天）")
        else:
            fresh_v = "info"
        per_dim["freshness"] = fresh_v

        overall = "info"
        for v in per_dim.values():
            overall = _severity_max(overall, v)

        return {"overall": overall, "per_dim": per_dim, "reasons": reasons}

    # ---- LLM 分析 ----

    def call_llm(self, context: dict) -> dict | None:
        """调 LLM（llm_heavy）+ extract_json_object 解析。失败/熔断/解析失败 → None。"""
        prompt_text = _load_prompt()
        context_json = json.dumps(context, ensure_ascii=False, indent=2)
        try:
            from infra.llm.gateway import LLMGateway
            r = LLMGateway.instance().call_sync(
                prompt=context_json,
                system=prompt_text,
                model_tier=OPS_LLM_MODEL_TIER,
                user_id="ops",
                module="ops_analyst",
                max_tokens=OPS_LLM_MAX_TOKENS,
                # 结构化 JSON 输出：关闭推理，避免 reasoning_content 挤占 content 预算导致 JSON 截断
                force_no_thinking=True,
            )
        except Exception as e:
            print(f"[OPS_ANALYST] LLM 调用异常: {e}")
            return None

        source = r.get("source", "")
        content = (r.get("content") or "").strip()
        if not content or source in ("rate_limited", "api_error", "error", "no_key"):
            print(f"[OPS_ANALYST] LLM 不可用 source={source}，走规则兜底")
            return None

        # 截断的 JSON 是半截字符串，解析必然失败；显式记录 finish_reason 便于定位
        finish_reason = r.get("finish_reason", "")
        if finish_reason == "length":
            print(f"[OPS_ANALYST] LLM 输出被 max_tokens 截断（finish_reason=length, tokens={r.get('tokens')}），走规则兜底")
            return None

        parsed = extract_json_object(content)
        if parsed is None:
            print(f"[OPS_ANALYST] LLM JSON 解析失败（model={r.get('model')}, finish_reason={finish_reason}, content_len={len(content)}），走规则兜底")
            return None
        parsed["_model"] = r.get("model", "")
        return parsed

    # ---- 合并 + 渲染 ----

    def merge_verdict(self, rule: dict, llm: dict) -> dict:
        """一票否决：final.overall = max(rule.overall, llm.overall)，critical 绝不被降级。"""
        final_overall = _severity_max(rule.get("overall", "info"), llm.get("overall_verdict", "info"))
        return {
            "date": date.today().isoformat(),
            "generated_at": datetime.now().isoformat(),
            "source": "llm",
            "model": llm.get("_model", ""),
            "overall_verdict": final_overall,
            "summary": llm.get("summary", ""),
            "dimensions": llm.get("dimensions", []),
            "critical_items": llm.get("critical_items", []),
            "warn_items": llm.get("warn_items", []),
            "report_text": llm.get("report_text", ""),
            "rule": rule,
            "push": {
                "target": OPS_REPORT_USER_ID,
                "critical_pushed": False,
                "daily_pushed": False,
            },
        }

    def fallback_report(self, rule: dict, today: dict) -> dict:
        """LLM 失败时的规则纯文本兜底报告。"""
        date_str = today.get("date") or date.today().isoformat()
        lines = [
            f"🔍 钱袋子运维巡检日报 {date_str}",
            "",
            f"整体判定：{SEVERITY_EMOJI.get(rule.get('overall', 'info'), '✅')} {rule.get('overall', 'info')}",
            "（本次由规则引擎兜底生成，LLM 不可用）",
            "",
            "各维度：",
        ]
        for dim in DIMENSIONS:
            v = rule.get("per_dim", {}).get(dim, "info")
            lines.append(f"  {SEVERITY_EMOJI.get(v, '✅')} {DIM_LABELS.get(dim, dim)}：{v}")
        if rule.get("reasons"):
            lines.append("")
            lines.append("问题明细：")
            for r in rule["reasons"]:
                lines.append(f"  - {r}")
        report_text = "\n".join(lines)

        dimensions = [
            {
                "dimension": dim,
                "verdict": rule.get("per_dim", {}).get(dim, "info"),
                "headline": DIM_LABELS.get(dim, dim),
                "detail": "",
                "trend": "数据不足，待积累",
            }
            for dim in DIMENSIONS
        ]
        return {
            "date": date_str,
            "generated_at": datetime.now().isoformat(),
            "source": "rule_fallback",
            "model": "",
            "overall_verdict": rule.get("overall", "info"),
            "summary": "规则引擎兜底（LLM 不可用）",
            "dimensions": dimensions,
            "critical_items": [],
            "warn_items": [],
            "report_text": report_text,
            "rule": rule,
            "push": {
                "target": OPS_REPORT_USER_ID,
                "critical_pushed": False,
                "daily_pushed": False,
            },
        }

    def render_report(self, report: dict) -> str:
        """渲染最终日报文本（标题 + 正文）；正文缺失时按维度兜底拼装。"""
        title = f"🔍 钱袋子运维巡检日报 {report.get('date') or date.today().isoformat()}"
        body = (report.get("report_text") or "").strip()
        if body:
            return f"{title}\n\n{body}"
        lines = [
            title,
            "",
            f"整体判定：{SEVERITY_EMOJI.get(report.get('overall_verdict', 'info'), '✅')} {report.get('overall_verdict', 'info')}",
        ]
        for d in report.get("dimensions", []):
            v = d.get("verdict", "info")
            lines.append(
                f"  {SEVERITY_EMOJI.get(v, '✅')} {d.get('dimension', '')}：{d.get('headline', '')}"
            )
        return "\n".join(lines)

    # ---- 推送 ----

    def _build_critical_text(self, report: dict) -> str:
        lines = ["🚨 钱袋子致命告警"]
        items = report.get("critical_items") or []
        if items:
            for it in items:
                lines.append(f"🔴 {it.get('title', '')}")
                action = it.get("action", "")
                if action:
                    lines.append(f"   建议：{action}")
        else:
            for r in report.get("rule", {}).get("reasons", []):
                lines.append(f"🔴 {r}")
        return "\n".join(lines)

    def _build_critical_text_from_rule(self, today: dict, rule: dict) -> str:
        lines = ["🚨 钱袋子致命告警"]
        for r in rule.get("reasons", []):
            lines.append(f"🔴 {r}")
        return "\n".join(lines)

    def push_report(self, report: dict) -> dict:
        """critical 立即推 + 日报推。企微未配置则跳过。"""
        try:
            from services import wxwork_push
        except Exception as e:
            print(f"[OPS_ANALYST] 推送服务导入失败: {e}")
            return report.setdefault("push", {})
        push = report.setdefault("push", {})
        push["target"] = OPS_REPORT_USER_ID
        if not wxwork_push.is_configured():
            print("[OPS_ANALYST] 企微未配置，跳过推送")
            return push
        if report.get("overall_verdict") == "critical":
            r = wxwork_push.send_markdown(
                self._build_critical_text(report), user_id=OPS_REPORT_USER_ID
            )
            push["critical_pushed"] = bool(r.get("ok"))
        daily_text = (report.get("report_text") or "").strip() or self.render_report(report)
        r2 = wxwork_push.send_daily_report_to(
            OPS_REPORT_USER_ID, daily_text, title="🔍 钱袋子运维巡检日报"
        )
        push["daily_pushed"] = bool(r2.get("ok"))
        return push

    # ---- critical 去重 ----

    def _critical_signature(self, today: dict, rule: dict) -> str:
        payload = {
            "date": today.get("date", ""),
            "reasons": sorted(rule.get("reasons", [])),
        }
        return hashlib.md5(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _load_critical_state(self) -> dict:
        if self.critical_state_path.exists():
            try:
                data = json.loads(self.critical_state_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                pass
        return {}

    def _save_critical_state(self, state: dict) -> None:
        state["updated_at"] = datetime.now().isoformat()
        atomic_write_json(self.critical_state_path, state)

    # ---- 主流程 ----

    def run_report(self) -> int:
        """日报主流水线：读快照→基线→规则→LLM→合并→落盘→推送。"""
        today = self.load_latest_snapshot()
        if today is None:
            print("[OPS_ANALYST] 未找到任何快照，退出码 2（不推送）")
            return 2

        history = self.build_history()
        self.save_baseline({"updated_at": None, "days": history})

        context = self.compute_context(today, history)
        rule = self.rule_triage(today, context)
        context["rule"] = rule

        llm = self.call_llm(context)
        if llm is not None:
            report = self.merge_verdict(rule, llm)
        else:
            report = self.fallback_report(rule, today)
        report["date"] = today.get("date") or report.get("date") or date.today().isoformat()

        # 先推送、后落盘：确保落盘时 push 字段已含真实推送结果（而非初始 False）
        self.push_report(report)

        report_file = self.ops_dir / f"report_{date.today().isoformat()}.json"
        atomic_write_json(report_file, report)
        print(f"[OPS_ANALYST] 报告已落盘: {report_file}（{report['overall_verdict']}）")
        return 0

    def run_critical_only(self) -> int:
        """--critical-only 模式：仅规则引擎扫快照，critical 且签名未变才推。"""
        today = self.load_latest_snapshot()
        if today is None:
            print("[OPS_ANALYST] 无快照，--critical-only 退出码 2")
            return 2

        history = self.build_history()
        context = self.compute_context(today, history)
        rule = self.rule_triage(today, context)
        if rule.get("overall") != "critical":
            print("[OPS_ANALYST] 非 critical，不推送")
            return 0

        signature = self._critical_signature(today, rule)
        state = self._load_critical_state()
        if state.get("signature") == signature:
            print("[OPS_ANALYST] critical 签名未变，跳过推送")
            return 0

        try:
            from services import wxwork_push
            if wxwork_push.is_configured():
                text = self._build_critical_text_from_rule(today, rule)
                wxwork_push.send_markdown(text, user_id=OPS_REPORT_USER_ID)
        except Exception as e:
            print(f"[OPS_ANALYST] 推送失败: {e}")

        self._save_critical_state({"signature": signature, "date": today.get("date", "")})
        print("[OPS_ANALYST] 已推送 critical 告警并更新签名")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="钱袋子二期 AI 运维巡检日报")
    parser.add_argument(
        "--critical-only",
        action="store_true",
        help="仅规则引擎扫描 critical 告警（不调 LLM、不产日报、不推非 critical）",
    )
    args = parser.parse_args(argv)
    analyst = OpsAnalyst()
    if args.critical_only:
        return analyst.run_critical_only()
    return analyst.run_report()


if __name__ == "__main__":
    sys.exit(main())
