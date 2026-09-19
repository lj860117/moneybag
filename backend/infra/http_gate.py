"""
钱袋子 v9.9.64 — 公网访问门禁（HTTP Gate）
==========================================
背景
----
uvicorn 监听 0.0.0.0:8000，PWA 通过 http://150.158.47.189:8000 直接访问
（无域名、无 TLS）。日志里存在大量公网扫描器试探，且 /api/chat/stream 等
LLM 端点此前可被任意人无认证调用（真实扣费）。

为什么不用 AUTH_ENABLED=true
----------------------------
1) 前端只有 apiFetch()（5 处调用）会带 Authorization 头，pages/*.js 里
   172 处裸 fetch()、app.js 里 33 处裸 fetch() 都不带 → 打开即整站 401；
2) AuthMiddleware 只从 query param 取 userId，POST body 里的 userId 取不到，
   一律落回 "default"，token 必然不匹配；
3) /api/auth/login 对任意新 userId 自动注册并返回 token → 攻击者可自助拿
   token，鉴权形同虚设；
4) 25+ 处 cron 脚本直连 http://127.0.0.1:8000/api/...，一旦开启全部 401，
   凌晨 AI 工作链 / 缓存预热 / 早报会全挂。

因此采用"应用前置门禁"：在路由匹配之前统一校验一个共享密钥，
来源为 loopback 的请求直接放行（保护全部 cron 与本地脚本），
企微回调按路径放行（自带 msg_signature 验签）。

放行条件（满足任一即放行）
--------------------------
  0. GATE_ENABLED=false 或 GATE_SECRET 未配置 → 门禁自动失效（fail-open，
     保证配置失误时不会把用户锁在门外）
  1. TCP 对端是 loopback（127.0.0.1 / ::1）
  2. 路径属于 /api/wxwork/*（企微回调，自带签名）
  3. 路径是 /gate（密钥换 cookie 的入口，入口本身必须在门禁之外）
  4. Cookie mb_gate == GATE_SECRET
  5. query ?k= == GATE_SECRET
  6. 请求头 X-Moneybag-Gate == GATE_SECRET
  7. Authorization: Basic 且密码 == GATE_SECRET（curl -u moneybag:<secret>）

未放行时：浏览器类请求返回 401 说明页（不返回 WWW-Authenticate，避免弹出
浏览器原生对话框），API 类请求返回 401 JSON。

用法
----
首次进入（手机/电脑浏览器各一次）：
    http://150.158.47.189:8000/gate?k=<GATE_SECRET>
成功后下发 180 天 cookie，之后正常访问 http://150.158.47.189:8000 即可。
"""
import base64
import hmac
import os
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

# ---- 配置（全部来自环境变量，由 systemd unit 注入）----
GATE_ENABLED = os.environ.get("GATE_ENABLED", "true").strip().lower() == "true"
GATE_SECRET = os.environ.get("GATE_SECRET", "").strip()
GATE_USER = os.environ.get("GATE_USER", "moneybag").strip() or "moneybag"

GATE_COOKIE_NAME = "mb_gate"
GATE_COOKIE_MAX_AGE = 180 * 24 * 3600  # 180 天

# loopback 来源：全部 cron / 本地脚本都走 127.0.0.1:8000，必须放行
LOOPBACK_HOSTS = frozenset((
    "127.0.0.1",
    "::1",
    "::ffff:127.0.0.1",
    "localhost",
))

# 门禁之外必须可达的路径（企微回调自带验签；/gate 是换 cookie 的入口）
OPEN_PATHS = ("/gate",)
OPEN_PREFIXES = ("/api/wxwork",)


def is_active() -> bool:
    """门禁是否真正生效。未配置 GATE_SECRET 时自动失效（fail-open）。"""
    return GATE_ENABLED and bool(GATE_SECRET)


def _cookie_value(cookie_header: str, name: str) -> str:
    """从 Cookie 头里取出指定字段的值。"""
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"')
    return ""


def _basic_password(auth_header: str) -> str:
    """从 Authorization: Basic 头里取出密码部分（用户名不校验）。"""
    if not auth_header:
        return ""
    lowered = auth_header.strip()
    if lowered[:6].lower() != "basic ":
        return ""
    try:
        raw = base64.b64decode(lowered[6:].strip()).decode("utf-8", "ignore")
    except Exception:
        return ""
    if ":" not in raw:
        return ""
    return raw.split(":", 1)[1]


def _secret_matches(candidates) -> bool:
    """常量时间比对，任一候选值等于密钥即通过。"""
    if not GATE_SECRET:
        return False
    for candidate in candidates:
        if candidate and hmac.compare_digest(str(candidate), GATE_SECRET):
            return True
    return False


def _should_bypass(path: str, client_host: str) -> bool:
    """判断该请求是否根本不需要过门禁。"""
    if client_host in LOOPBACK_HOSTS:
        return True
    if path in OPEN_PATHS:
        return True
    for prefix in OPEN_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _gate_page_html() -> str:
    """未带密钥时返回的说明页（含密钥输入框，可直接粘贴通行）。"""
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>钱袋子 · 访问验证</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#0f172a;color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;padding:24px}
  .card{width:100%;max-width:340px;background:#1e293b;border:1px solid #334155;border-radius:16px;padding:28px 22px;text-align:center}
  .icon{font-size:40px;margin-bottom:10px}
  h1{margin:0 0 8px;font-size:18px}
  p{margin:0 0 18px;font-size:13px;color:#94a3b8;line-height:1.6}
  input{width:100%;padding:11px 12px;border-radius:8px;border:1px solid #334155;background:#0f172a;
        color:#f1f5f9;font-size:14px;text-align:center;margin-bottom:12px;outline:none}
  input:focus{border-color:#F59E0B}
  button{width:100%;padding:11px;border-radius:8px;border:none;background:#F59E0B;color:#0f172a;
         font-size:15px;font-weight:700;cursor:pointer}
</style></head>
<body>
  <div class="card">
    <div class="icon">🔐</div>
    <h1>钱袋子 · 访问验证</h1>
    <p>请输入访问密钥，验证一次即可记住 180 天。</p>
    <form action="/gate" method="get">
      <input type="password" name="k" placeholder="访问密钥" autocomplete="off" autofocus>
      <button type="submit">进 入</button>
    </form>
  </div>
</body></html>"""


class HttpGateMiddleware:
    """纯 ASGI 中间件（不用 BaseHTTPMiddleware，避免缓冲 SSE 流式响应）。

    注册位置：必须在 main.py 里最后 add_middleware()，Starlette 中最后注册的
    中间件位于最外层，会先于 CORS / AuthMiddleware 执行。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not is_active():
            await self.app(scope, receive, send)
            return

        headers = {}
        for raw_key, raw_value in scope.get("headers", []) or []:
            try:
                headers[raw_key.decode("latin-1").lower()] = raw_value.decode("latin-1")
            except Exception:
                continue

        path = scope.get("path", "") or "/"
        client = scope.get("client")
        client_host = client[0] if client else ""

        if _should_bypass(path, client_host):
            await self.app(scope, receive, send)
            return

        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        candidates = (
            _cookie_value(headers.get("cookie", ""), GATE_COOKIE_NAME),
            (query.get("k") or [""])[0],
            headers.get("x-moneybag-gate", ""),
            _basic_password(headers.get("authorization", "")),
        )

        if _secret_matches(candidates):
            await self.app(scope, receive, send)
            return

        await self._deny(scope, receive, send, headers)

    @staticmethod
    async def _deny(scope, receive, send, headers) -> None:
        """未通过门禁：浏览器返回说明页，其余返回 JSON。"""
        accept = headers.get("accept", "")
        wants_html = "text/html" in accept
        if wants_html:
            response = HTMLResponse(
                content=_gate_page_html(),
                status_code=401,
                headers={
                    "Cache-Control": "no-store",
                    "X-Robots-Tag": "noindex, nofollow",
                },
            )
        else:
            response = JSONResponse(
                status_code=401,
                content={
                    "code": 401,
                    "message": "未授权：缺少访问密钥",
                    "data": None,
                },
                headers={"Cache-Control": "no-store"},
            )
        await response(scope, receive, send)


# ---- /gate：用密钥换取长期 cookie ----
gate_router = APIRouter()


@gate_router.get("/gate", include_in_schema=False)
def gate_entry(request: Request, k: str = ""):
    """访问密钥 → 下发 cookie → 跳回首页。密钥错误则回到说明页。"""
    if not is_active() or not _secret_matches([k or ""]):
        return HTMLResponse(
            content=_gate_page_html(),
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        GATE_COOKIE_NAME,
        GATE_SECRET,
        max_age=GATE_COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return response
