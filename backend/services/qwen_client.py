"""
通义千问（DashScope/百炼）客户端
两个用途：
1. 视觉理解（qwen-vl-max-latest）— DeepSeek 做不了的截图OCR
2. 文本降级（qwen-turbo）— DeepSeek 失败时的备份
"""
import os
import base64
import json
import re
from typing import Optional

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def is_qwen_available() -> bool:
    return bool(DASHSCOPE_API_KEY)


def call_qwen_text(
    prompt: str,
    max_tokens: int = 500,
    system: Optional[str] = None,
    model: str = "qwen3.6-flash",
    timeout: int = 30,
) -> Optional[str]:
    """通义千问文本模型调用（DeepSeek 降级用）

    默认 qwen3.6-flash：新一代，速度快、价格低
    可选 qwen-turbo（更便宜）、qwen3.7-max（最强）
    """
    if not DASHSCOPE_API_KEY:
        return None
    import httpx
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                DASHSCOPE_BASE_URL,
                headers={
                    "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                },
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            print(f"[QWEN_TEXT] HTTP {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        print(f"[QWEN_TEXT] err: {e}")
        return None


def parse_receipt_image(image_bytes: bytes, image_format: str = "jpeg") -> dict:
    """用 qwen-vl-max-latest 识别基金买入凭证截图

    返回: {ok, fund_name, fund_code, nav, shares, amount, date, raw_text}
    """
    if not DASHSCOPE_API_KEY:
        return {"ok": False, "reason": "未配置 DASHSCOPE_API_KEY"}

    import httpx

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/{image_format};base64,{b64}"

    prompt = """请从这张基金买入凭证截图中提取以下信息，严格按 JSON 格式返回，不要额外说明：
{
  "fund_name": "基金全名（如：华夏半导体龙头混合C）",
  "fund_code": "基金6位代码（如果可见）",
  "nav": 确认净值(数字),
  "shares": 确认份额(数字),
  "amount": 买入金额(数字, 单位元),
  "date": "确认日期(YYYY-MM-DD)"
}
若某字段无法识别，值用 null。只返回JSON，不要任何其他文字。"""

    try:
        with httpx.Client(timeout=45) as client:
            r = client.post(
                DASHSCOPE_BASE_URL,
                headers={
                    "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "qwen3-vl-plus",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                    "max_tokens": 400,
                },
            )
            if r.status_code != 200:
                # 触发配额告警（自动去重）
                try:
                    from services.llm_quota_alert import maybe_alert_quota
                    maybe_alert_quota("qwen", r.status_code, r.text[:500])
                except Exception:
                    pass
                return {"ok": False, "reason": f"HTTP {r.status_code}: {r.text[:200]}"}

            raw = r.json()["choices"][0]["message"]["content"]
            # 提取 JSON
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return {"ok": False, "reason": "未识别到JSON结构", "raw_text": raw}
            try:
                parsed = json.loads(m.group())
            except json.JSONDecodeError as e:
                return {"ok": False, "reason": f"JSON解析失败: {e}", "raw_text": raw}

            return {
                "ok": True,
                "fund_name": parsed.get("fund_name"),
                "fund_code": parsed.get("fund_code"),
                "nav": parsed.get("nav"),
                "shares": parsed.get("shares"),
                "amount": parsed.get("amount"),
                "date": parsed.get("date"),
                "source": "qwen-vl-max",
                "raw_text": raw,
            }
    except Exception as e:
        return {"ok": False, "reason": f"调用异常: {e}"}
