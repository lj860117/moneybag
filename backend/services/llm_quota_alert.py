"""
LLM 配额/余额告警
- DeepSeek 余额不足 → 推送企微
- 通义千问免费额度用完 → 推送企微
- 同种告警一天只推一次（文件去重）
- 没看见第二天会再推（次日重新允许推送）
"""
import os
import json
import time
from pathlib import Path
from datetime import date

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
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

    # 千问: 403 AllocationQuota.FreeTierOnly / 余额不足
    if provider == "qwen":
        if "freetieronly" in err_lower or "allocationquota" in err_lower:
            return "qwen_free_quota_exhausted"
        if status_code == 403 and "quota" in err_lower:
            return "qwen_free_quota_exhausted"
        if "余额不足" in error_msg or "balance" in err_lower and "insufficient" in err_lower:
            return "qwen_balance_exhausted"

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
                "影响：晨报/选基/AI对话可能降级到通义千问免费额度\n\n"
                "建议处理：\n"
                "• 前往 https://platform.deepseek.com/ 充值\n"
                "• 或临时把 AI 对话切到「通义千问」备用\n\n"
                "_系统已自动切换到千问继续运行_"
            ),
            "qwen_free_quota_exhausted": (
                "🆓 通义千问免费额度告罄",
                "**⚠️ 通义千问免费额度（100万 token）已用完**\n\n"
                "影响：截图识别 / DeepSeek 降级备份暂不可用\n\n"
                "建议处理：\n"
                "• 检查百炼控制台 https://bailian.console.aliyun.com/\n"
                "• 如需继续使用，可少量充值（个人用一年 5 元够用）\n\n"
                "_DeepSeek 主路径正常工作中_"
            ),
            "qwen_balance_exhausted": (
                "💳 通义千问余额告警",
                "**❗ 通义千问账户余额不足**\n\n"
                "前往百炼控制台充值：https://bailian.console.aliyun.com/"
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
