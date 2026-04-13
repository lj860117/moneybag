"""
钱袋子 — 企业微信推送服务
通过企业微信应用消息推送盯盘信号到个人微信

配置方式（环境变量）：
  WXWORK_CORP_ID    企业 ID
  WXWORK_SECRET     应用 Secret
  WXWORK_AGENT_ID   应用 AgentID
  WXWORK_USER_ID    接收人（@all 或具体 userId）

注册流程（用户操作约 10 分钟）：
  1. 访问 https://work.weixin.qq.com/ → 注册企业微信（个人也行）
  2. 管理后台 → 应用管理 → 创建应用 → 取 AgentID + Secret
  3. 我的企业 → 取 CorpID
  4. 设置信任 IP（腾讯云公网 IP）
  5. 微信插件 → 邀请成员关注 → 消息就会推到微信
"""
import os
import time
import httpx

# 配置从环境变量读取
_CORP_ID = os.getenv("WXWORK_CORP_ID", "")
_SECRET = os.getenv("WXWORK_SECRET", "")
_AGENT_ID = os.getenv("WXWORK_AGENT_ID", "")
_USER_ID = os.getenv("WXWORK_USER_ID", "@all")

# access_token 缓存（2 小时有效）
_token_cache = {"token": "", "expires": 0}


def is_configured() -> bool:
    """检查企业微信是否已配置"""
    return bool(_CORP_ID and _SECRET and _AGENT_ID)


def _get_token() -> str:
    """获取/刷新 access_token"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    if not is_configured():
        return ""

    try:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={_CORP_ID}&corpsecret={_SECRET}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            data = resp.json()
            if data.get("errcode") == 0:
                token = data["access_token"]
                _token_cache["token"] = token
                _token_cache["expires"] = now + 7000  # 提前 200 秒刷新
                return token
            else:
                print(f"[WXWORK] Token error: {data}")
    except Exception as e:
        print(f"[WXWORK] Token failed: {e}")
    return ""


def send_text(content: str, user_id: str = "") -> dict:
    """发送文本消息"""
    token = _get_token()
    if not token:
        return {"ok": False, "error": "未配置或获取 token 失败"}

    try:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        payload = {
            "touser": user_id or _USER_ID,
            "msgtype": "text",
            "agentid": int(_AGENT_ID),
            "text": {"content": content},
        }
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            data = resp.json()
            ok = data.get("errcode") == 0
            if not ok:
                print(f"[WXWORK] Send error: {data}")
            return {"ok": ok, "data": data}
    except Exception as e:
        print(f"[WXWORK] Send failed: {e}")
        return {"ok": False, "error": str(e)}


def send_markdown(content: str, user_id: str = "") -> dict:
    """发送 Markdown 消息（企业微信支持简单 Markdown）"""
    token = _get_token()
    if not token:
        return {"ok": False, "error": "未配置或获取 token 失败"}

    try:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        payload = {
            "touser": user_id or _USER_ID,
            "msgtype": "markdown",
            "agentid": int(_AGENT_ID),
            "markdown": {"content": content},
        }
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            data = resp.json()
            ok = data.get("errcode") == 0
            return {"ok": ok, "data": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_stock_alert(signals: list) -> dict:
    """发送股票异动预警（格式化为 Markdown）"""
    if not signals:
        return {"ok": True, "msg": "无异动"}

    lines = ["**🚨 钱袋子盯盘预警**\n"]
    for sig in signals[:10]:  # 最多 10 条
        emoji = "🔴" if sig.get("level") == "warning" else "🟡"
        lines.append(f"{emoji} **{sig.get('name', '')}**({sig.get('code', '')})")
        lines.append(f"> {sig.get('message', '')}\n")

    lines.append(f"⏰ {time.strftime('%H:%M:%S')}")
    content = "\n".join(lines)
    return send_markdown(content)


def send_daily_report(report: str) -> dict:
    """发送每日复盘报告"""
    content = f"**📊 钱袋子每日复盘**\n\n{report}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}"
    return send_markdown(content)
