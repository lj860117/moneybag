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
        - 200 → 正常
        - 其余 → 交给 services.llm_quota_alert.classify_llm_error_detail 统一判定

     ⚠️ 2026-09-12 修正：旧版把 402/403 一律当「额度/余额耗尽」，是过度归因。
     实测 ARK 上 404 = ModelNotOpen（模型未开通，充值无用）、429 = 限流、
     403 也可能是 AccessDenied——处置动作完全不同，一律说「余额耗尽」会误导
     用户去充值。且只探测 llm_light 档（turbo）时，turbo 未开通会被静默吞掉，
     看不出「账号其实可用、只是这个模型没开」。现改为两档都探 + 统一分类器。

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
  - 同种告警一天只推一次：**去重与优先级都直接复用 services.llm_quota_alert**，
    与「事后告警」共用同一张优先级表、同一个状态文件，不再各判各的
    （FIX 2026-09-12 QA-2：此前本脚本只有去重、没有优先级判定）。
  - 主动巡检是用户主动设的每日任务，允许绕过免打扰窗口（cron 已从 07:40 挪到
    08:05 落进窗口内），但**不能绕过优先级**：P3（限流/瞬时抖动）永不推送。
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

# ---- 保证 `from services.xxx import ...` 可用（FIX 2026-09-12）----
# cron 的调用形态是 `cd /opt/moneybag/backend && python scripts/llm_balance_monitor.py`，
# 此时 sys.path[0] 是 scripts/，backend/ 不在 path 里 —— 下面的
# `from services.llm_quota_alert import ...` 会 ModuleNotFoundError。
# 那个 import 又包在 try/except 里，结果是**运行期静默降级**：分类器、去重、
# 优先级门禁全部失效，且只在 cron 环境才暴露。这里显式补上 backend/。
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_LOG_DIR = Path(os.environ.get("LOG_DIR", str(_BACKEND_DIR / "logs"))).expanduser()
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "llm_balance_monitor.log"

# ---- 可配置阈值 ----

DEEPSEEK_BALANCE_THRESHOLD = float(os.environ.get("DEEPSEEK_BALANCE_THRESHOLD", "10.0"))

# ---- provider 配置（env 默认值与 services/llm_gateway.py 保持一致）----
DEEPSEEK_API_BASE = os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_API_KEY = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")

DOUBAO_API_BASE = os.environ.get(
    "DOUBAO_API_BASE", os.environ.get("ARK_API_BASE", "https://ark.cn-beijing.volces.com/api/v3")
)
DOUBAO_API_KEY = os.environ.get("DOUBAO_API_KEY", "") or os.environ.get("ARK_API_KEY", "")
# 探测用模型：复用 gateway 的 llm_light 档位（turbo），可用 env 覆盖
DOUBAO_PROBE_MODEL = os.environ.get("DOUBAO_PROBE_MODEL", "doubao-seed-2-1-turbo-260628")
#
# FIX 2026-09-12：只探测一个模型会得出错误结论。
# 实测（2026-09-12 01:30 CST，生产 Key 直连 ARK）：
#   doubao-seed-2-1-turbo-260628 → 404 ModelNotOpen（模型未开通）
#   doubao-seed-2-1-pro-260628   → 200 OK（账户可用）
# 只看 turbo 的旧逻辑分不清「账号欠费」和「这个模型没开通」，
# 于是把 ModelNotOpen 静默记一句就完事；而 402/403 又被一律当成欠费。
# 现在两个路由档位都探，各自独立告警，语义由 llm_quota_alert 统一判定。
DOUBAO_PROBE_MODELS: list[str] = [
    m.strip()
    for m in os.environ.get(
        "DOUBAO_PROBE_MODELS",
        "doubao-seed-2-1-turbo-260628,doubao-seed-2-1-pro-260628",
    ).split(",")
    if m.strip()
] or [DOUBAO_PROBE_MODEL]

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
# 告警去重 + 优先级 —— 统一复用 services.llm_quota_alert
# ============================================================
# FIX 2026-09-12（QA-2）：本脚本过去自带一套去重、且**完全没有优先级判定**，
# 与 llm_quota_alert 的「P3 限流永不推送」口径不一致：同一个限流错误，事后告警
# 会静默、主动巡检却会推出去。现在去重、优先级、文案三件事全部复用
# services.llm_quota_alert，两条链路共用一张表、一个状态文件，不再各判各的。
def _dedupe_already_sent(dedupe_key: str) -> bool:
    """复用 llm_quota_alert 的当日去重；取不到时按「未推送」处理。

    失败方向选「宁可重复推送、不可静默丢告警」——本脚本每天只跑一次，
    最坏情况是多推一条，不会刷屏。
    """
    try:
        from services.llm_quota_alert import was_alert_sent_today
    except Exception as e:  # noqa: BLE001
        LOG.warning("共享去重模块加载失败，按未推送处理: %s", e)
        return False
    try:
        return bool(was_alert_sent_today(dedupe_key))
    except Exception as e:  # noqa: BLE001
        LOG.warning("共享去重读取失败，按未推送处理: %s", e)
        return False


def _dedupe_mark_sent(dedupe_key: str) -> None:
    """复用 llm_quota_alert 的当日去重写入（失败仅告警，不影响主流程）。"""
    try:
        from services.llm_quota_alert import mark_alert_sent_today
    except Exception as e:  # noqa: BLE001
        LOG.warning("共享去重模块加载失败，跳过写入: %s", e)
        return
    try:
        mark_alert_sent_today(dedupe_key)
    except Exception as e:  # noqa: BLE001
        LOG.warning("共享去重写入失败: %s", e)


def _priority_of(dedupe_key: str) -> str:
    """取告警优先级，与 llm_quota_alert 共用同一张 ALERT_PRIORITY 表。

    dedupe_key 形如 "doubao_model_not_open|doubao-seed-2-1-turbo-260628"，
    取 "|" 之前的 alert_type 去查表；未知类型由 alert_priority 保守返回 P2。
    """
    base = (dedupe_key or "").split("|", 1)[0]
    try:
        from services.llm_quota_alert import alert_priority
    except Exception as e:  # noqa: BLE001
        LOG.warning("优先级表加载失败，保守按 P2 处理: %s", e)
        return "P2"
    try:
        return str(alert_priority(base))
    except Exception as e:  # noqa: BLE001
        LOG.warning("优先级判定失败，保守按 P2 处理: %s", e)
        return "P2"


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
    """统一告警出口：醒目日志 + **优先级门禁** +（可选）企微推送 + 当日去重。

    优先级门禁（FIX 2026-09-12 QA-2）：与 llm_quota_alert 共用 ALERT_PRIORITY，
    P3（限流/瞬时抖动）**永不推送**。主动巡检允许绕过免打扰窗口（cron 已挪到
    08:05 落在窗口内，实际也不再需要绕过），但**绝不能绕过优先级**。
    """
    LOG.warning("")
    LOG.warning("================================================================")
    LOG.warning("🚨 [%s] %s", alert_type, title)
    LOG.warning("%s", content)
    LOG.warning("================================================================")
    LOG.warning("")

    priority = _priority_of(alert_type)
    if priority == "P3":
        LOG.info("P3（限流/瞬时抖动）永不推送，仅记录 | %s", alert_type)
        return

    if not do_push:
        return
    if _dedupe_already_sent(alert_type):
        LOG.info("今日已推送过 %s，跳过", alert_type)
        return
    if _push_alert(title, content):
        _dedupe_mark_sent(alert_type)


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
    """豆包的可用性探测入口（按模型粒度探测，语义由统一分类器判定）。

    FIX 2026-09-12：旧逻辑 `402/403 → 额度/余额耗尽` 是过度归因。
    ARK 上 403 也可能是 AccessDenied（权限），404 是 ModelNotOpen（未开通），
    429 是限流——它们的处置动作完全不同，一律说「余额耗尽」会误导人去充值。
    现在复用 services.llm_quota_alert.classify_llm_error_detail 统一判定，
    保证「主动监控」和「事后告警」两处口径一致，不再各判各的。
    """
    if not api_key:
        LOG.warning("[%s] 未配置 API Key，跳过可用性探测", provider)
        return

    try:
        status, body = _probe_availability(provider, api_key, api_base, model)
    except Exception as e:  # noqa: BLE001
        LOG.warning("[%s/%s] 可用性探测网络异常: %s", provider, model, e)
        return

    if status == 200:
        LOG.info("[%s/%s] 可用性探测通过（HTTP 200）", provider, model)
        return

    LOG.warning("[%s/%s] 可用性探测返回 %s: %s", provider, model, status, body)

    try:
        from services.llm_quota_alert import (
            classify_llm_error_detail,
            build_alert_message,
        )
    except Exception as e:  # noqa: BLE001
        LOG.warning("分类器加载失败，按未分类处理: %s", e)
        classify_llm_error_detail = None  # type: ignore[assignment]
        build_alert_message = None  # type: ignore[assignment]

    if classify_llm_error_detail is None or build_alert_message is None:
        LOG.warning("[%s/%s] 无法判定，仅记录不告警", provider, model)
        return

    alert_type, err_code, snippet = classify_llm_error_detail(provider, status, body)
    if not alert_type:
        LOG.warning(
            "[%s/%s] 未识别的失败（HTTP %s · 错误码 %s），仅记录不告警",
            provider, model, status, err_code or "-",
        )
        return

    # 注：限流（P3）不再在这里特判 —— _emit_alert 里的优先级门禁会统一挡掉，
    # 只留一个门禁，避免「两处各判一次，改了一处忘了另一处」。

    title, content = build_alert_message(
        alert_type,
        provider,
        status,
        err_code,
        snippet,
        model=model,
        module="llm_balance_monitor",
    )
    # 按「类型 + 模型」去重：turbo 未开通和 pro 未开通是两件事，都要能报出来
    _emit_alert(f"{alert_type}|{model}", title, content, do_push=do_push)


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
        # 两个路由档位都探：只有同时看到「turbo 404 未开通 + pro 200 正常」
        # 才能断定是模型没开通而不是账号欠费（2026-09-12 误报的根因）
        for probe_model in DOUBAO_PROBE_MODELS:
            try:
                _check_by_probe(
                    "doubao", DOUBAO_API_KEY, DOUBAO_API_BASE, probe_model, do_push=do_push
                )
            except Exception as e:  # noqa: BLE001
                LOG.exception("[doubao/%s] 未捕获异常: %s", probe_model, e)

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
