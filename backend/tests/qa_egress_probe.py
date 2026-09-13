"""QA 独立出口探针（验证专用，非生产代码）。

用法：
    cd backend && PYTHONPATH=. python3 -m pytest <目标> -p qa_egress_probe

两层探针，互相补位：
  1. httpx 层：patch `httpx.Client.send`，记下 **URL 主机名**（能证明"目标是企微"），
     然后照常往下走 —— 真连接由第 2 层拦。
  2. socket 层：patch `socket.create_connection` / `socket.socket.connect`，
     **一律阻断并记录**（此前对 127.0.0.1 放行是错的：本机有透明代理
     HTTPS_PROXY=127.0.0.1:54245，httpx 连的是代理不是目标主机，
     白名单一开就等于整条链路放行 —— 实测真请求带回了企微 40013）。

收集顺序上，第 1 层能看到"想发给谁"，第 2 层保证"一个字节都发不出去"。
"""

import socket

# socket 层：所有被阻断的出网尝试
EGRESS: list = []
# httpx 层：所有"准备发出的请求"的目标 URL
HTTP_EGRESS: list = []

# 会话级保存的 wxwork_push 原始 HTTP 出口（conftest 打桩前抓下来，
# 供"反向证明"用例摘掉 conftest 那层网，单独验证新守卫）
ORIGINALS: dict = {}

_HTTPX_CLIENT_CLS = None


def reset():
    EGRESS.clear()
    HTTP_EGRESS.clear()


def _install_socket():
    if getattr(socket, "_qa_probe_installed", False):
        return
    socket._qa_probe_installed = True

    _orig_connect = socket.socket.connect
    _orig_create = socket.create_connection

    def _blocked(addr, kind):
        host = str(addr[0]) if isinstance(addr, (tuple, list)) and addr else str(addr)
        port = addr[1] if isinstance(addr, (tuple, list)) and len(addr) > 1 else None
        EGRESS.append({"kind": kind, "host": host, "port": port})
        raise RuntimeError(f"[QA_EGRESS_PROBE] 阻断出网 {kind} -> {host}:{port}")

    def connect(self, addr, *a, **k):
        return _blocked(addr, "socket.connect")

    def create_connection(addr, *a, **k):
        return _blocked(addr, "create_connection")

    socket.socket.connect = connect                     # type: ignore[assignment]
    socket.create_connection = create_connection        # type: ignore[assignment]
    # 保留原函数引用，供需要时还原
    ORIGINALS["_sock_connect"] = _orig_connect
    ORIGINALS["_sock_create"] = _orig_create


def _install_httpx():
    global _HTTPX_CLIENT_CLS
    if _HTTPX_CLIENT_CLS is not None:
        return
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return
    cls = httpx.Client
    if getattr(cls, "_qa_probe_installed", False):
        return
    _HTTPX_CLIENT_CLS = cls

    _orig_send = cls.send

    def send(self, request, *a, **k):
        HTTP_EGRESS.append({
            "method": str(request.method),
            "host": str(request.url.host),
            "path": str(request.url.path),
        })
        return _orig_send(self, request, *a, **k)

    send._qa_probe_installed = True
    cls.send = send                                      # type: ignore[assignment]
    ORIGINALS["_httpx_send"] = _orig_send


def install():
    _install_socket()
    _install_httpx()


# 当前用例 ID（用于定位"是谁想发企微"）
_CURRENT_TEST = ["<session>"]
# 每次"真的调用了 wxwork_push 的发送函数"的记录
SEND_CALLS: list = []


def _install_send_wrappers():
    try:
        from services import wxwork_push as wp
    except Exception:  # noqa: BLE001
        return
    import functools
    for name in dir(wp):
        if not (name.startswith("send_") or name == "archive_push"):
            continue
        fn = getattr(wp, name)
        if not callable(fn) or getattr(fn, "_qa_wrapped", False):
            continue

        @functools.wraps(fn)
        def _wrapped(*a, _fn=fn, _name=name, **k):
            SEND_CALLS.append({"func": _name, "test": _CURRENT_TEST[0]})
            return _fn(*a, **k)

        _wrapped._qa_wrapped = True
        setattr(wp, name, _wrapped)


def pytest_runtest_setup(item):
    _CURRENT_TEST[0] = item.nodeid


# 只有当本文件被当作 **插件** 加载（-p qa_egress_probe）时才为 True。
# 注意：pytest 会把 tests/ 塞进 sys.path，导致 `import qa_egress_probe` 在
# 没加 -p 时也能成功 —— 那会儿钩子一个都没跑，断言会假绿/报错。
ACTIVE = False


def pytest_configure(config):
    global ACTIVE
    ACTIVE = True
    install()


def pytest_sessionstart(session):  # pragma: no cover
    install()
    _install_send_wrappers()
    try:
        from services import wxwork_push as wp
        ORIGINALS["get"] = wp._http_client.get
        ORIGINALS["post"] = wp._http_client.post

        # QA_FORCE_WECOM_CREDS=1：把"企微已配置"状态灌进模块级常量。
        # 本机 WXWORK_* 全空 → is_configured() 恒 False → 根本走不到推送分支，
        # 那种"零调用"是空转的绿。生产机上有这三个变量，必须补上才算复现。
        import os
        if os.environ.get("QA_FORCE_WECOM_CREDS") == "1":
            wp._CORP_ID = "QA-FAKE-CORP"
            wp._SECRET = "QA-FAKE-SECRET"
            wp._AGENT_ID = "QA-FAKE-AGENT"
            print("[QA_EGRESS_PROBE] 已强制企微为「已配置」状态（模拟生产）")
    except Exception as e:  # noqa: BLE001
        ORIGINALS["error"] = str(e)
