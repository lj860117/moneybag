"""
钱袋子 — 企业微信路由（独立 router）
职责：回调验证 + 消息接收 + 快捷指令 + AI 聊天 + 推送状态/测试
"""
import os
import time
import threading

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from services.wxwork_push import (
    is_configured as wxwork_configured,
    send_text as wxwork_send,
    verify_callback as wxwork_verify,
    decrypt_message as wxwork_decrypt,
    encrypt_reply as wxwork_encrypt_reply,
    send_markdown,
)

router = APIRouter(prefix="/api/wxwork", tags=["企业微信"])


# ---- 回调验证 ----

@router.get("/callback")
def callback_verify(msg_signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
    """企业微信 URL 验证回调（GET 请求）"""
    result = wxwork_verify(msg_signature, timestamp, nonce, echostr)
    if result:
        return PlainTextResponse(result)
    return PlainTextResponse("verify failed", status_code=403)


@router.post("/callback")
async def callback_receive(
    request: Request,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
):
    """企业微信消息接收 → DeepSeek 分析 → 异步回复"""
    body = await request.body()
    xml_body = body.decode("utf-8")

    msg = wxwork_decrypt(msg_signature, timestamp, nonce, xml_body)
    if not msg or not msg.get("content"):
        return PlainTextResponse("success")

    from_user = msg["from_user"]
    content = msg["content"]
    print(f"[WXWORK] 收到消息: {from_user} → {content}")

    def async_reply():
        try:
            # 找到用户的 profileId（名字=企微userid）
            from main import _load_profiles, _build_market_context, _build_portfolio_context, _load_prompt_template
            profiles = _load_profiles()
            user_profile = None
            for p in profiles:
                if p.get("wxworkUserId") == from_user or p.get("name") == from_user:
                    user_profile = p
                    break

            user_id = user_profile["id"] if user_profile else ""
            user_name = user_profile["name"] if user_profile else from_user

            # 快捷指令
            cmd = content.strip().lower()
            if cmd in ("持仓", "我的持仓", "持仓情况"):
                _handle_holdings(from_user, user_id, user_name)
                return
            if cmd in ("扫描", "盯盘", "异动"):
                _handle_scan(from_user, user_id, user_name)
                return
            if cmd in ("帮助", "help", "?", "？"):
                _handle_help(from_user)
                return

            # 先发一条"思考中"让用户知道没死机
            send_markdown(f"🧠 收到！正在分析「{content[:20]}{'...' if len(content)>20 else ''}」\n预计 10-20 秒回复...", user_id=from_user)

            # 通用问题 → 调 DeepSeek AI 聊天
            market_ctx = _build_market_context()
            portfolio_ctx = _build_portfolio_context(user_id=user_id) if user_id else "用户尚未建仓。"

            # 注入用户记忆
            if user_id:
                try:
                    from services.agent_memory import get_memory_summary
                    mem = get_memory_summary(user_id)
                    if mem:
                        portfolio_ctx += f"\n\n## 用户记忆\n{mem}"
                except Exception:
                    pass

            system_prompt = _load_prompt_template()
            full_system = f"{system_prompt}\n\n## 实时市场数据\n{market_ctx}\n\n## 用户持仓\n{portfolio_ctx}"

            import httpx
            api_key = os.getenv("LLM_API_KEY", "")
            if not api_key:
                send_markdown("⚠️ AI 暂不可用（未配置 API Key）", user_id=from_user)
                return

            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": full_system},
                            {"role": "user", "content": content},
                        ],
                        "max_tokens": 800,
                        "temperature": 0.7,
                    },
                )
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]

            reply_md = f"**🤖 AI 分析师**\n\n{reply}\n\n> 来自: {user_name} 的提问"
            send_markdown(reply_md, user_id=from_user)

        except Exception as e:
            print(f"[WXWORK] Async reply error: {e}")
            try:
                send_markdown(f"⚠️ 分析出错: {str(e)[:100]}", user_id=from_user)
            except Exception:
                pass

    threading.Thread(target=async_reply, daemon=True).start()
    return PlainTextResponse("success")


# ---- 快捷指令处理 ----

def _handle_holdings(wxwork_uid: str, user_id: str, user_name: str):
    """快捷指令：查看持仓"""
    from services.stock_monitor import load_stock_holdings
    from services.fund_monitor import load_fund_holdings
    stocks = load_stock_holdings(user_id) if user_id else []
    funds = load_fund_holdings(user_id) if user_id else []
    if not stocks and not funds:
        send_markdown(f"📭 {user_name}，你还没有添加持仓。\n\n打开 App 添加：http://150.158.47.189:8000", user_id=wxwork_uid)
        return
    lines = [f"**📈 {user_name} 的持仓**\n"]
    if stocks:
        lines.append(f"**股票（{len(stocks)}只）**")
        for s in stocks:
            lines.append(f"- {s.get('name', '')}({s.get('code', '')})")
    if funds:
        lines.append(f"\n**基金（{len(funds)}只）**")
        for f in funds:
            lines.append(f"- {f.get('name', '')}({f.get('code', '')})")
    send_markdown("\n".join(lines), user_id=wxwork_uid)


def _handle_scan(wxwork_uid: str, user_id: str, user_name: str):
    """快捷指令：触发盯盘扫描"""
    from services.stock_monitor import scan_all_holdings
    from services.fund_monitor import scan_all_fund_holdings
    send_markdown(f"🔍 正在扫描 {user_name} 的持仓...", user_id=wxwork_uid)
    stock_result = scan_all_holdings(user_id) if user_id else {}
    fund_result = scan_all_fund_holdings(user_id) if user_id else {}
    alerts = []
    for s in stock_result.get("signals", []):
        alerts.append(f"📊 {s.get('name', '')}：{s.get('message', s.get('msg', ''))}")
    for f in fund_result.get("signals", []):
        alerts.append(f"💰 {f.get('name', '')}：{f.get('message', f.get('msg', ''))}")
    if alerts:
        send_markdown(f"**🚨 {user_name} 持仓异动**\n\n" + "\n".join(alerts[:10]), user_id=wxwork_uid)
    else:
        send_markdown(f"✅ {user_name} 持仓暂无异动", user_id=wxwork_uid)


def _handle_help(wxwork_uid: str):
    """快捷指令：帮助"""
    help_text = """**🤖 钱袋子 AI 助手**

**快捷指令：**
- 发 **持仓** → 查看你的持仓列表
- 发 **扫描** → 立即扫描持仓异动
- 发 **帮助** → 显示本帮助

**问问题：**
- 直接发任何理财问题，AI 帮你分析
- 例：`茅台还能买吗？`
- 例：`现在适合入场吗？`
- 例：`帮我做一次全面市场分析`

📱 完整功能：http://150.158.47.189:8000"""
    send_markdown(help_text, user_id=wxwork_uid)


# ---- 状态 & 测试 ----

@router.get("/status")
def wxwork_status():
    """企业微信推送配置状态"""
    return {"configured": wxwork_configured()}


@router.post("/test")
def wxwork_test(req: dict = {}):
    """测试企业微信推送"""
    if not wxwork_configured():
        return {"ok": False, "error": "企业微信未配置。需要设置环境变量：WXWORK_CORP_ID, WXWORK_SECRET, WXWORK_AGENT_ID"}
    msg = req.get("message", "🧪 钱袋子推送测试\n如果你能看到这条消息，说明企业微信推送配置成功！")
    touser = req.get("touser", "@all")
    return send_markdown(msg, user_id=touser) if touser != "@all" else wxwork_send(msg)


@router.post("/daily-report")
def wxwork_daily_report():
    """手动触发市场日报推送（给所有绑定企微的用户）"""
    if not wxwork_configured():
        return {"ok": False, "error": "企业微信未配置"}
    try:
        from main import _build_market_context
        ctx = _build_market_context()
        # 精简为日报格式
        lines = ["**📊 钱袋子市场日报**\n"]
        for line in ctx.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if any(k in line for k in ["估值", "恐贪", "GDP", "社零", "沪深", "标普", "道琼", "纳斯达克", "黄金", "铜", "美元", "政策", "降准", "降息"]):
                lines.append(line)
            if len(lines) >= 15:
                break
        if len(lines) <= 1:
            lines.append("暂无市场数据")
        lines.append(f"\n⏰ {time.strftime('%Y-%m-%d %H:%M')}")
        content = "\n".join(lines)
        result = send_markdown(content, user_id="@all")
        return {"ok": result.get("ok", False), "data": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
