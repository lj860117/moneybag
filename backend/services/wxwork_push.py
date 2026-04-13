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


# ---- 按用户推送（cron 多用户场景）----

def send_stock_alert_to(wxwork_userid: str, signals: list) -> dict:
    """发送股票异动预警给指定用户"""
    if not signals:
        return {"ok": True, "msg": "无异动"}

    lines = ["**🚨 钱袋子盯盘预警**\n"]
    for sig in signals[:10]:
        emoji = "🔴" if sig.get("level") == "warning" else "🟡"
        lines.append(f"{emoji} **{sig.get('name', '')}**({sig.get('code', '')})")
        lines.append(f"> {sig.get('message', sig.get('msg', ''))}\n")
    lines.append(f"⏰ {time.strftime('%H:%M:%S')}")
    content = "\n".join(lines)
    return send_markdown(content, user_id=wxwork_userid)


def send_daily_report_to(wxwork_userid: str, report: str) -> dict:
    """发送每日复盘给指定用户"""
    content = f"**📊 钱袋子每日复盘**\n\n{report}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}"
    return send_markdown(content, user_id=wxwork_userid)


# ============================================================
# 回调验证（企业微信 URL 验证 + 消息接收）
# ============================================================

import hashlib
import base64
import struct
import socket
from Crypto.Cipher import AES

_CALLBACK_TOKEN = os.getenv("WXWORK_CALLBACK_TOKEN", "moneybag2026")
_CALLBACK_AES_KEY = os.getenv("WXWORK_CALLBACK_AES_KEY", "")


def _decode_aes_key(encoding_aes_key: str) -> bytes:
    return base64.b64decode(encoding_aes_key + "=")


def _verify_signature(token: str, timestamp: str, nonce: str, echostr: str, signature: str) -> bool:
    """验证企微回调签名"""
    sort_list = sorted([token, timestamp, nonce, echostr])
    sha1 = hashlib.sha1("".join(sort_list).encode()).hexdigest()
    return sha1 == signature


def _decrypt_echostr(aes_key: bytes, echostr: str) -> str:
    """AES 解密 echostr 并返回明文"""
    try:
        cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
        decrypted = cipher.decrypt(base64.b64decode(echostr))
        # 去 PKCS7 padding
        pad = decrypted[-1]
        content = decrypted[:-pad]
        # 格式: 16字节随机 + 4字节内容长度 + 内容 + corpid
        xml_len = struct.unpack("!I", content[16:20])[0]
        xml_content = content[20:20 + xml_len].decode("utf-8")
        return xml_content
    except Exception as e:
        print(f"[WXWORK] Decrypt error: {e}")
        return ""


def verify_callback(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """处理企微 URL 验证回调，返回解密后的 echostr（明文）"""
    if not _CALLBACK_AES_KEY:
        print("[WXWORK] No AES key configured")
        return ""

    if not _verify_signature(_CALLBACK_TOKEN, timestamp, nonce, echostr, msg_signature):
        print(f"[WXWORK] Signature verification failed")
        pass

    aes_key = _decode_aes_key(_CALLBACK_AES_KEY)
    result = _decrypt_echostr(aes_key, echostr)
    if result:
        print(f"[WXWORK] Callback verify OK")
    return result


def decrypt_message(msg_signature: str, timestamp: str, nonce: str, xml_body: str) -> dict:
    """解密企微推送的消息，返回 {from_user, content, msg_type}"""
    import xml.etree.ElementTree as ET
    if not _CALLBACK_AES_KEY:
        return {}
    try:
        root = ET.fromstring(xml_body)
        encrypt_node = root.find("Encrypt")
        if encrypt_node is None:
            return {}
        encrypted = encrypt_node.text

        # 验签
        _verify_signature(_CALLBACK_TOKEN, timestamp, nonce, encrypted, msg_signature)

        # AES 解密
        aes_key = _decode_aes_key(_CALLBACK_AES_KEY)
        decrypted = _decrypt_echostr(aes_key, encrypted)
        if not decrypted:
            return {}

        # 解析明文 XML
        msg_root = ET.fromstring(decrypted)
        return {
            "from_user": msg_root.findtext("FromUserName", ""),
            "content": msg_root.findtext("Content", "").strip(),
            "msg_type": msg_root.findtext("MsgType", "text"),
            "msg_id": msg_root.findtext("MsgId", ""),
            "create_time": msg_root.findtext("CreateTime", ""),
        }
    except Exception as e:
        print(f"[WXWORK] Decrypt message error: {e}")
        return {}


def encrypt_reply(reply_text: str, to_user: str, nonce: str) -> str:
    """加密回复消息为企微要求的 XML 格式"""
    import xml.etree.ElementTree as ET
    import random
    import string

    if not _CALLBACK_AES_KEY:
        return ""
    try:
        aes_key = _decode_aes_key(_CALLBACK_AES_KEY)
        corp_id = _CORP_ID or ""

        # 构造明文: 16字节随机 + 4字节长度 + 内容 + corpid
        reply_bytes = reply_text.encode("utf-8")
        random_bytes = ''.join(random.choices(string.ascii_letters + string.digits, k=16)).encode()
        content = random_bytes + struct.pack("!I", len(reply_bytes)) + reply_bytes + corp_id.encode()

        # PKCS7 padding
        pad_len = 32 - (len(content) % 32)
        content += bytes([pad_len] * pad_len)

        # AES CBC 加密
        cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
        encrypted = base64.b64encode(cipher.encrypt(content)).decode()

        # 生成签名
        timestamp = str(int(time.time()))
        sign_list = sorted([_CALLBACK_TOKEN, timestamp, nonce, encrypted])
        signature = hashlib.sha1("".join(sign_list).encode()).hexdigest()

        # 构造 XML
        xml = f"""<xml>
<Encrypt><![CDATA[{encrypted}]]></Encrypt>
<MsgSignature><![CDATA[{signature}]]></MsgSignature>
<TimeStamp>{timestamp}</TimeStamp>
<Nonce><![CDATA[{nonce}]]></Nonce>
</xml>"""
        return xml
    except Exception as e:
        print(f"[WXWORK] Encrypt reply error: {e}")
        return ""
