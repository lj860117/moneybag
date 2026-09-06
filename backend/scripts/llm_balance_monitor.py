"""
LLM 供应商余额主动监控 cron — 每天定时查询两家 provider 的余额/可用性，低余额时预警。

背景
----
现有 llm_quota_alert.py 是**事后**告警：只有在调用返回 402/403（欠费）时才会触发。
本脚本是**主动**监控：每天定时、主动查询余额，在真正欠费之前提前预警。

两家 provider 的查询能力差异（重要，务必读）：
  1. DeepSeek  —— 官方提供 `GET {base}/user/balance` 余额接口，可直接读真实余额。
     低于阈值（默认 10 元）时告警。
  2. 豆包(ARK)  —— Volcengine ARK 的 OpenAI 兼容接口（/api/v3）**不提供**余额查询端点；
     其计费/余额只存在于火山引擎控制台（需 AccessKey，非本项目的 API Key）。
     因此这里用「最小化 chat 调用（max_tokens=1）」做**可用性探测**兜底：
        - 200        → 正常
        - 401        → API Key 无效（配置问题）
        - 402 / 403  → 额度/余额耗尽（等价于欠费信号）

（千问 DashScope 已于欠费后下线，不再探测。）

每日探测仅额外消耗 ~1 次 1-token 调用（豆包 lite），成本可忽略。

用法
----
  # 只打印 + 写日志（不推送企微）
  python backend/scripts/llm_balance_monitor.py

  # 打印 + 写日志 + 触发告警时推送企微（生产 cron 建议带上 --alert）
  python backend/scripts/llm_balance_monitor.py --alert

  # 跳过豆包的可用性探测（只查 DeepSeek 真实余额）
  python backend/scripts/llm_balance_monitor.py --no-probe

设计约定
--------
  - 与项目其他 cron 脚本一致，放在 backend/scripts/ 下，独立可运行。
  - 两家逐家 try/except 隔离，单家失败绝不影响其他家。
  - 自带 `mkdir -p` 日志目录，日志写入 <backend>/logs/llm_balance_monitor.log，
    同时打到 stdout（cron 再重定向一份）。
  - 同种告警一天只推一次（文件去重，与 llm_quota_alert.py 相同的状态模式，
    但使用独立的 state 文件，互不干扰）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

# ---- 路径与日志目录（必须最先做 mkdir，避免 bash 重定向因目录缺失而不执行）----
_BACKEND_DIR = Path(__file__).resolve().parent.parent      # backend/
_PROJECT_DIR = _BACKEND_DIR.parent                          # /opt/moneybag
_LOG_DIR = Path(os.environ.get("LOG_DIR", str(_BACKEND_DIR / "logs"))).expanduser()
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "llm_balance_monitor.log"

# 告警去重状态文件（与 llm_quota_alert.py 分开，互不干扰）
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(_PROJECT_DIR / "data"))).expanduser()
_ALERT_STATE_FILE = _DATA_DIR / "llm_balance_alert_state.json"

# ---- 可配置阈值 ----
DEEPSEEK_BALANCE_THRESHOLD = float(os.environ.get("DEEPSEEK_BALANCE_THRESHOLD", "10.0"))

# ---- provider 配置（env 默认值与 services/llm_gateway.py 保持一致）----
DEEPSEEK_API_BASE = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_API_KEY = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")

DOUBAO_API_BASE = os.environ.get(
    "DOUBAO_API_BASE", os.environ.get("ARK_API_BASE", "https://ark.cn-beijing.volces.com/api/v3")
)
DOUBAO_API_KEY = os.environ.get("DOUBAO_API_KEY", "") or os.environ.get("ARK_API_KEY", "")
# 探测用模型：复用 gateway 的 llm_light 档位（最便宜），可用 env 覆盖
DOUBAO_PROBE_MODEL = os.environ.get("DOUBAO_PROBE_MODEL", "doubao-seed-2-0-lite-260215")

_HTTP_TIMEOUT = float(os.environ.get("LLM_BALANCE_HTTP_TIMEOUT", "20.0"))

# 企微推送对象（与 llm_quota_alert.py 一致）
_ALERT_RECIPIENTS = ["LeiJiang", "BuLuoGeLi"]


def _setup_logger():
    """构造 logger：同时写文件（自动 mkdir）和 stdout。

    返回 logging.Logger。使用全局单例，避免重复添加 handler。
    """
    import logging

    logger = logging.getLogger("llm_balance_monitor")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


LOG = _setup_logger()


# ============================================================
# 告警去重（与 llm_quota_alert.py 相同模式）
# ============================================================
def _load_state() -> dict:
    if not _ALERT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(_ALERT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ALERT_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        LOG.warning("保存告警状态失败: %s", e)


def _was_sent_today(alert_type: str) -> bool:
    state = _load_state()
    return state.get(alert_type) == date.today().isoformat()


def _mark_sent_today(alert_type: str) -> None:
    state = _load_state()
    state[alert_type] = date.today().isoformat()
    _save_state(state)


# ============================================================
# 企微推送
# ============================================================
def _push_alert(title: str, content: str) -> bool:
    """推送告警到企微（懒加载，避免非生产环境 import 失败影响主流程）。

    返回 True 表示企微已配置并已尝试推送（应计入当日去重）；
    返回 False 表示未配置/模块加载失败（不应消费当日额度）。
    """
    try:
        from services.wxwork_push import is_configured, send_daily_report_to

        if not is_configured():
            LOG.warning("企微未配置，跳过推送（标题: %s）", title)
            return False

        for uid in _ALERT_RECIPIENTS:
            try:
                send_daily_report_to(uid, content, title=title)
            except Exception as e:  # noqa: BLE001
                LOG.warning("推送给 %s 失败: %s", uid, e)
        LOG.info("✅ 已推送企微告警: %s", title)
        return True
    except Exception as e:  # noqa: BLE001
        LOG.warning("企微推送模块加载失败: %s", e)
        return False


def _emit_alert(alert_type: str, title: str, content: str, *, do_push: bool) -> None:
    """统一告警出口：打印醒目日志 + 写日志 +（可选）企微推送 + 当日去重。"""
    LOG.warning("")
    LOG.warning("================================================================")
    LOG.warning("🚨 [%s] %s", alert_type, title)
    LOG.warning("%s", content)
    LOG.warning("================================================================")
    LOG.warning("")

    if not do_push:
        return
    if _was_sent_today(alert_type):
        LOG.info("今日已推送过 %s，跳过", alert_type)
        return
    if _push_alert(title, content):
        _mark_sent_today(alert_type)


# ============================================================
# DeepSeek：真实余额查询
# ============================================================
def _check_deepseek(threshold: float, *, do_push: bool) -> None:
    """查询 DeepSeek 真实余额，低于阈值则告警。"""
    if not DEEPSEEK_API_KEY:
        LOG.warning("[deepseek] 未配置 LLM_API_KEY，跳过余额查询")
        return

    url = f"{DEEPSEEK_API_BASE.rstrip('/')}/user/balance"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            resp = client.get(url, headers=headers)
    except Exception as e:  # noqa: BLE001
        LOG.warning("[deepseek] 余额查询网络异常: %s", e)
        return

    if resp.status_code != 200:
        LOG.warning("[deepseek] 余额接口返回 %s: %s", resp.status_code, resp.text[:200])
        if resp.status_code in (401, 403):
            _emit_alert(
                "deepseek_key_invalid",
                "💳 DeepSeek API Key 异常",
                f"余额接口返回 {resp.status_code}，API Key 可能失效或权限不足。\n"
                "请前往 https://platform.deepseek.com/ 检查 Key。",
                do_push=do_push,
            )
        return

    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        LOG.warning("[deepseek] 余额响应解析失败: %s", e)
        return

    if data.get("is_available") is False:
        _emit_alert(
            "deepseek_key_invalid",
            "💳 DeepSeek API Key 异常",
            "余额接口返回 is_available=false，API Key 可能已失效。\n"
            "请前往 https://platform.deepseek.com/ 检查 Key。",
            do_push=do_push,
        )
        return

    balance = _parse_deepseek_balance(data)
    if balance is None:
        LOG.warning("[deepseek] 余额接口未找到余额字段: %s", json.dumps(data, ensure_ascii=False)[:300])
        return

    LOG.info("[deepseek] 当前余额: ¥%.2f（阈值 ¥%.2f）", balance, threshold)
    if balance < threshold:
        _emit_alert(
            "deepseek_balance_low",
            "💳 DeepSeek 余额不足",
            f"当前余额 ¥{balance:.2f}，已低于阈值 ¥{threshold:.2f}。\n\n"
            "影响：晨报/选基/AI 对话主路径可能中断或降级到豆包。\n\n"
            "建议：前往 https://platform.deepseek.com/ 充值。",
            do_push=do_push,
        )


def _parse_deepseek_balance(data: dict) -> Optional[float]:
    """从 DeepSeek /user/balance 响应里解析 CNY 余额（元）。

    响应形如:
      {"is_available": true, "balance_infos": [
          {"currency": "CNY", "total_balance": "110.00",
           "granted_balance": "4.00", "topped_up_balance": "106.00"}]}
    """
    infos = data.get("balance_infos") or []
    if not infos:
        return None

    # 优先取 CNY；取不到就用第一条
    target = next((i for i in infos if i.get("currency") == "CNY"), infos[0])

    total = target.get("total_balance")
    if total is None or total == "":
        granted = _to_float(target.get("granted_balance"))
        topped = _to_float(target.get("topped_up_balance"))
        if granted is None or topped is None:
            return None
        return granted + topped

    return _to_float(total)


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ============================================================
# 豆包：可用性探测（无真实余额接口时的兜底）
# ============================================================
def _probe_availability(name: str, api_key: str, api_base: str, model: str) -> tuple[int, str]:
    """发送一次最小化 chat 调用，返回 (status_code, 响应文本片段)。

    用 max_tokens=1 + "hi" 把成本压到最低，仅用于探测可用性。
    """
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        resp = client.post(url, json=payload, headers=headers)
    return resp.status_code, (resp.text or "")[:300]


def _check_by_probe(
    provider: str,
    api_key: str,
    api_base: str,
    model: str,
    *,
    do_push: bool,
) -> None:
    """豆包的可用性探测入口（无余额接口，402/403 视为额度/余额耗尽）。"""
    if not api_key:
        LOG.warning("[%s] 未配置 API Key，跳过可用性探测", provider)
        return

    try:
        status, body = _probe_availability(provider, api_key, api_base, model)
    except Exception as e:  # noqa: BLE001
        LOG.warning("[%s] 可用性探测网络异常: %s", provider, e)
        return

    if status == 200:
        LOG.info("[%s] 可用性探测通过（HTTP 200）", provider)
        return

    LOG.warning("[%s] 可用性探测返回 %s: %s", provider, status, body)

    if status == 401:
        _emit_alert(
            f"{provider}_auth_failed",
            f"🔑 {provider} API Key 无效",
            f"可用性探测返回 401，API Key 可能失效或错误。\n{body}",
            do_push=do_push,
        )
        return

    if status in (402, 403):
        _emit_alert(
            f"{provider}_quota_exhausted",
            f"💳 {provider} 额度/余额耗尽",
            f"可用性探测返回 {status}，视为额度或余额耗尽信号。\n{body}",
            do_push=do_push,
        )
        return

    # 其他状态码（5xx 等）只记录，不推送，避免瞬时抖动造成噪音
    LOG.warning("[%s] 返回未预期的状态码 %s，仅记录不告警", provider, status)


# ============================================================
# 主流程
# ============================================================
def run(threshold: float, *, do_push: bool, do_probe: bool) -> None:
    LOG.info("===== LLM 余额监控启动 @ %s =====", date.today().isoformat())

    # 两家逐家隔离：任何一家异常都不影响其它家
    try:
        _check_deepseek(threshold, do_push=do_push)
    except Exception as e:  # noqa: BLE001
        LOG.exception("[deepseek] 未捕获异常: %s", e)

    if do_probe:
        try:
            _check_by_probe(
                "doubao", DOUBAO_API_KEY, DOUBAO_API_BASE, DOUBAO_PROBE_MODEL, do_push=do_push
            )
        except Exception as e:  # noqa: BLE001
            LOG.exception("[doubao] 未捕获异常: %s", e)

    LOG.info("===== LLM 余额监控结束 =====")


def main() -> None:
    parser = argparse.ArgumentParser(description="钱袋子 LLM 供应商余额主动监控 cron")
    parser.add_argument("--alert", action="store_true", help="触发告警时推送企微（默认只打印+写日志）")
    parser.add_argument("--no-probe", action="store_true", help="跳过豆包可用性探测")
    parser.add_argument(
        "--deepseek-threshold",
        type=float,
        default=DEEPSEEK_BALANCE_THRESHOLD,
        help=f"DeepSeek 余额告警阈值（元，默认 {DEEPSEEK_BALANCE_THRESHOLD}）",
    )
    args = parser.parse_args()

    run(args.deepseek_threshold, do_push=args.alert, do_probe=not args.no_probe)


if __name__ == "__main__":
    main()
