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

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "wxwork_push",
    "scope": "public",
    "input": [],
    "output": "push_result",
    "cost": "cpu",
    "tags": ['推送', '企微', 'AES'],
    "description": "企业微信推送：AES加解密+access_token管理+文本消息",
    "layer": "output",
    "priority": 8,
}
import os
import time
import httpx
from infra.cache import MemoryCache

# 配置从环境变量读取
_CORP_ID = os.getenv("WXWORK_CORP_ID", "")
_SECRET = os.getenv("WXWORK_SECRET", "")
_AGENT_ID = os.getenv("WXWORK_AGENT_ID", "")
_USER_ID = os.getenv("WXWORK_USER_ID", "@all")

# access_token 有效期 2 小时（企微规范），提前 5 分钟刷新
_TOKEN_CACHE_TTL = 7200

# access_token 缓存（2 小时有效）
_token_cache = MemoryCache(default_ttl=_TOKEN_CACHE_TTL)  # {"wxwork_token": {"data": {"token": str, "expires": int}, "ts": float}}

# FIX: 全局 HTTP 连接池复用（避免每次 send 都新建 TCP 连接）
_http_client = httpx.Client(timeout=15, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))

# FIX: 81013 无效用户缓存（同一用户 81013 只打一次日志，避免刷屏）
_81013_warned = set()


def is_configured() -> bool:
    """检查企业微信是否已配置"""
    return bool(_CORP_ID and _SECRET and _AGENT_ID)


def _get_token() -> str:
    """获取/刷新 access_token"""
    cached = _token_cache.get("token")
    if cached is not None:
        return cached

    if not is_configured():
        return ""

    try:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={_CORP_ID}&corpsecret={_SECRET}"
        resp = _http_client.get(url)
        data = resp.json()
        if data.get("errcode") == 0:
            token = data["access_token"]
            _token_cache.set("token", token, ttl=7000)  # 提前 200 秒刷新
            return token
        else:
            print(f"[WXWORK] Token error: {data}")
    except Exception as e:
        print(f"[WXWORK] Token failed: {e}")
    return ""


def send_text(content: str, user_id: str = "") -> dict:
    """发送文本消息（含统一 81013 处理 + 失败自动重试 1 次）
    
    返回: {"ok": bool, "data": dict, "skipped_81013": bool}
    """
    token = _get_token()
    if not token:
        return {"ok": False, "error": "未配置或获取 token 失败"}

    target = user_id or _USER_ID
    payload = {
        "touser": target,
        "msgtype": "text",
        "agentid": int(_AGENT_ID),
        "text": {"content": content},
    }

    def _do_send():
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        resp = _http_client.post(url, json=payload)
        return resp.json()

    for attempt in range(2):  # FIX: 失败自动重试 1 次
        try:
            data = _do_send()
            errcode = data.get("errcode", -1)

            # FIX: 统一 81013 处理 — 无效用户标记跳过，不再每次报错
            if errcode == 81013:
                if target not in _81013_warned:
                    _81013_warned.add(target)
                    print(f"[WXWORK] ⏭️ 用户 {target} 无效(81013)，后续静默跳过")
                return {"ok": False, "error": "81013_invalid_user", "skipped_81013": True, "data": data}

            # token 过期自动刷新重试
            if errcode == 42001 or errcode == 40014:
                _token_cache.delete("token")
                token = _get_token()
                if token and attempt == 0:
                    payload["agentid"] = int(_AGENT_ID)  # refresh payload
                    continue
                return {"ok": False, "error": "token_expired", "data": data}

            ok = errcode == 0
            if not ok:
                print(f"[WXWORK] Send error: {data}")
            return {"ok": ok, "data": data}
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            if attempt == 0:
                print(f"[WXWORK] 网络错误，2s后重试: {e}")
                time.sleep(2)
                continue
            print(f"[WXWORK] Send failed (重试后): {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            print(f"[WXWORK] Send failed: {e}")
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "max_retries_exceeded"}


def send_markdown(content: str, user_id: str = "") -> dict:
    """发送消息（统一用纯文本，微信不支持 Markdown，完整清理格式符号）
    
    v9.5.123: 超过1800字自动分段推送（不再截断），每段间隔0.5秒
    """
    import re
    plain = content
    # 去粗体/代码/斜体/转义
    plain = re.sub(r'\*\*(.+?)\*\*', r'\1', plain)   # **粗体** → 粗体
    plain = re.sub(r'\*(.+?)\*', r'\1', plain)         # *斜体* → 斜体
    plain = re.sub(r'`{1,3}[^`]*`{1,3}', '', plain)   # `code` / ```block``` → 删除
    # 标题行（## 标题 → 标题 加换行）
    plain = re.sub(r'^#{1,6}\s+', '', plain, flags=re.MULTILINE)
    # 引用块 > 内容 → 换成缩进两格
    plain = re.sub(r'^>\s*', '  ', plain, flags=re.MULTILINE)
    # 链接 [文字](url) → 文字
    plain = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', plain)
    # 分割线 --- 或 *** → 删除
    plain = re.sub(r'^[-*]{3,}\s*$', '', plain, flags=re.MULTILINE)
    # 多余空行压缩
    plain = re.sub(r'\n{3,}', '\n\n', plain)
    plain = plain.strip()
    
    # v9.5.123: 超过1800字分段推送(不截断)
    MAX_CHUNK = 1800
    if len(plain) <= MAX_CHUNK:
        return send_text(plain, user_id=user_id)
    
    # 按段落分割(优先在\n\n处切分,其次\n)
    chunks = _split_message(plain, MAX_CHUNK)
    last_result = {}
    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(0.5)  # 防限流
        tag = f"({i+1}/{len(chunks)})" if len(chunks) > 1 else ""
        last_result = send_text(f"{chunk}\n{tag}" if tag else chunk, user_id=user_id)
    return last_result


def _split_message(text: str, max_len: int = 1800) -> list:
    """智能分割长消息: 优先在段落分隔处切分,保证每段不超max_len"""
    if len(text) <= max_len:
        return [text]
    
    chunks = []
    remaining = text
    
    # 尝试找到好的分割点(优先级: 持仓明细标题 > 双换行 > 单换行)
    SPLIT_MARKERS = ["持仓明细", "📊 组合温度计", "📈 【股票推荐", "💰 【基金推荐", "\n\n"]
    
    while len(remaining) > max_len:
        # 在max_len范围内找最佳切分点
        best_pos = -1
        for marker in SPLIT_MARKERS:
            pos = remaining.rfind(marker, 0, max_len)
            if pos > max_len * 0.3:  # 至少要包含30%内容
                best_pos = pos
                break
        
        if best_pos <= 0:
            # 没找到好的分割点,在最近的换行处切
            pos = remaining.rfind("\n", 0, max_len)
            best_pos = pos if pos > max_len * 0.3 else max_len
        
        chunks.append(remaining[:best_pos].rstrip())
        remaining = remaining[best_pos:].lstrip()
    
    if remaining.strip():
        chunks.append(remaining.strip())
    
    return chunks


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


def send_daily_report(report: str, title: str = "📊 钱袋子每日复盘") -> dict:
    """发送每日复盘报告"""
    content = f"**{title}**\n\n{report}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}"
    return send_markdown(content)


# ---- 按用户推送（cron 多用户场景）----

def send_stock_alert_to(wxwork_userid: str, signals: list) -> dict:
    """发送股票异动预警给指定用户"""
    if not signals:
        return {"ok": True, "msg": "无异动"}

    # 补全基金/股票名称（alert 里 name 可能为空）
    def _get_display_name(sig):
        name = sig.get('name', '')
        code = sig.get('code', '')
        if name:
            return name
        # 尝试从基金名称表补全
        try:
            from services.fund_monitor import _get_fund_name
            n = _get_fund_name(code)
            if n and n != code:
                return n
        except Exception:
            pass
        return code  # 最终降级用代码

    lines = ["**🚨 钱袋子盯盘预警**\n"]
    for sig in signals[:10]:
        emoji = "🔴" if sig.get("level") == "warning" else "🟡"
        display_name = _get_display_name(sig)
        code = sig.get('code', '')
        lines.append(f"{emoji} **{display_name}**（{code}）")
        lines.append(f"> {sig.get('message', sig.get('msg', ''))}\n")
    lines.append(f"⏰ {time.strftime('%H:%M:%S')}")
    content = "\n".join(lines)
    return send_markdown(content, user_id=wxwork_userid)


def send_daily_report_to(wxwork_userid: str, report: str, title: str = "📊 钱袋子每日复盘") -> dict:
    """发送报告给指定用户（title 可自定义，默认每日复盘）
    
    v9.5.123: 不再截断，send_markdown 会自动分段推送长消息
    v9.7.0: 移除多余的 ** 清理（send_markdown 内部已统一处理）
    """
    if title:
        content = f"{title}\n\n{report}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}"
    else:
        content = f"{report}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}"
    # v9.5.123: 不再硬截断,send_markdown会自动分段推送
    return send_markdown(content, user_id=wxwork_userid)


# ============================================================
# 回调验证（企业微信 URL 验证 + 消息接收）
# ============================================================

import hashlib
import base64
import struct
import socket
from Crypto.Cipher import AES

_CALLBACK_TOKEN = os.getenv("WXWORK_CALLBACK_TOKEN", "")
if not _CALLBACK_TOKEN:
    # 安全提醒：回调 Token 未配置，消息验证将失败
    # 生产环境务必设置 WXWORK_CALLBACK_TOKEN 环境变量
    print("[WXWORK] ⚠️ WXWORK_CALLBACK_TOKEN 未配置，回调验证不可用")
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
        return ""  # 修复：签名验证失败必须 return，不能继续执行

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

        # 验签（修复：验证失败则拒绝解密，防止伪造消息触发 LLM）
        if not _verify_signature(_CALLBACK_TOKEN, timestamp, nonce, encrypted, msg_signature):
            print(f"[WXWORK] Message signature verification failed — dropping")
            return {}

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


def archive_push(user_id: str, push_type: str, content: str, timestamp: str = None):
    """
    存档推送内容到本地文件（用于后续质量评估）
    
    Args:
        user_id: 用户ID（如 "LeiJiang"）
        push_type: 推送类型（"briefing"/"closing_review"/"alert"）
        content: 完整推送内容
        timestamp: 时间戳（可选，默认当前时间）
    """
    try:
        from config import PUSH_ARCHIVE_DIR
        import datetime
        
        # 生成文件名：YYYY-MM-DD_type_user.txt
        if timestamp is None:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}_{push_type}_{user_id}.txt"
        filepath = PUSH_ARCHIVE_DIR / filename
        
        # 写入文件（追加模式，同类型多条推送都保存）
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"=== {timestamp} ===\n")
            f.write(content)
            f.write("\n\n")
        
        print(f"  [存档] {push_type} 已存档到 {filename}")
        
    except Exception as e:
        print(f"  [存档] 失败：{e}")
