"""
LLM 配额/余额告警
- DeepSeek 余额不足 → 推送企微
- 豆包(火山引擎 ARK) 余额耗尽 / API Key 异常 → 推送企微
- 同种告警一天只推一次（文件去重）
- 没看见第二天会再推（次日重新允许推送）
"""
import config
import os
import json
import time
from pathlib import Path
from datetime import date

DATA_DIR = Path(config.DATA_DIR)
ALERT_STATE_FILE = DATA_DIR / "llm_alert_state.json"


def _load_state() -> dict:
    """读取告警状态：{alert_type: last_sent_date}"""
    if not ALERT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(ALERT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERT_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[QUOTA_ALERT] save state failed: {e}")


def _was_sent_today(alert_type: str) -> bool:
    """检查今天是否已推送过此类告警"""
    state = _load_state()
    return state.get(alert_type) == date.today().isoformat()


def _mark_sent_today(alert_type: str):
    state = _load_state()
    state[alert_type] = date.today().isoformat()
    _save_state(state)


def classify_llm_error(provider: str, status_code: int, error_msg: str) -> str | None:
    """根据错误码/消息识别告警类型

    返回告警类型字符串，或 None（不需要告警的错误）
    """
    err_lower = (error_msg or "").lower()

    # DeepSeek: 402 Insufficient Balance / Payment Required
    if provider == "deepseek":
        if status_code == 402:
            return "deepseek_balance_exhausted"
        if "insufficient" in err_lower and "balance" in err_lower:
            return "deepseek_balance_exhausted"
        if "payment required" in err_lower or "支付" in error_msg:
            return "deepseek_balance_exhausted"

    # 豆包(火山引擎 ARK): 402/403 额度/余额耗尽 / 余额不足 / 鉴权失败
    if provider == "doubao":
        if status_code in (401, 403) and (
            "auth" in err_lower or "invalid" in err_lower or "key" in err_lower
        ):
            return "doubao_auth_failed"
        if status_code == 402:
            return "doubao_balance_exhausted"
        if status_code == 403 and "quota" in err_lower:
            return "doubao_balance_exhausted"
        if "余额不足" in error_msg or "insufficient" in err_lower or "balance" in err_lower:
            return "doubao_balance_exhausted"
        if "arrearage" in err_lower or "arrears" in err_lower or "欠费" in error_msg:
            return "doubao_balance_exhausted"

    return None


def maybe_alert_quota(provider: str, status_code: int, error_msg: str):
    """检测错误并按需推送告警（自动去重）

    安全：异常时静默，不影响主流程
    """
    try:
        alert_type = classify_llm_error(provider, status_code, error_msg)
        if not alert_type:
            return

        # 当日去重
        if _was_sent_today(alert_type):
            return

        # 构造告警消息
        messages = {
            "deepseek_balance_exhausted": (
                "💳 DeepSeek 余额提醒",
                "**❗ DeepSeek API 余额已用尽或不足**\n\n"
                "影响：晨报/选基/AI对话可能降级到豆包\n\n"
                "建议处理：\n"
                "• 前往 https://platform.deepseek.com/ 充值\n"
                "• 或临时把 AI 对话切到「豆包」备用\n\n"
                "_系统已自动切换到豆包继续运行_"
            ),
            "doubao_balance_exhausted": (
                "💳 豆包余额提醒",
                "**❗ 豆包（火山引擎 ARK）账户余额已用尽或不足**\n\n"
                "影响：DeepSeek 降级到豆包时，豆包也欠费，AI 功能可能整体不可用\n\n"
                "建议处理：\n"
                "• 前往火山引擎控制台充值 https://console.volcengine.com/ark\n"
                "• 或尽快为 DeepSeek 充值恢复主路径\n\n"
                "_DeepSeek 若正常则不受影响_"
            ),
            "doubao_auth_failed": (
                "🔑 豆包 API Key 异常",
                "**⚠️ 豆包（火山引擎 ARK）API Key 可能失效或权限不足**\n\n"
                "前往火山引擎控制台检查：https://console.volcengine.com/ark"
            ),
        }
        title, content = messages.get(alert_type, ("LLM 告警", error_msg))

        # 推送给 LeiJiang 和 BuLuoGeLi
        from services.wxwork_push import is_configured, send_daily_report_to
        if not is_configured():
            return

        for uid in ["LeiJiang", "BuLuoGeLi"]:
            try:
                send_daily_report_to(uid, content, title=title)
            except Exception as e:
                print(f"[QUOTA_ALERT] push to {uid} failed: {e}")

        _mark_sent_today(alert_type)
        print(f"[QUOTA_ALERT] ✅ 已推送告警: {alert_type}")

    except Exception as e:
        print(f"[QUOTA_ALERT] err: {e}")
