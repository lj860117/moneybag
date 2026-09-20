"""v9.9.69：requests / urllib3 出口防火墙的故障注入测试。

背景：httpx 防火墙（v9.9.66~v9.9.68）只 patch 了 httpx.Client/AsyncClient.send，
但业务里存在直接用 `requests` 库发 DeepSeek 的直连路径（httpx 看不到）。
本文件验证：即使调用方用 requests 直连 DeepSeek Pro，防火墙也必须改写成 flash。

判据（参考 skill llm-cost-audit AE/AF）：断言**内容**（改写后的 body 里 model 字段），
而不是只看「没报错」。
"""
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from infra.llm import gateway  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 共享判定函数 _check_model_in_body 单测（无网络、无副作用）
# ---------------------------------------------------------------------------
def test_check_model_in_body_rewrites_pro():
    body, orig = gateway._check_model_in_body(
        json.dumps({"model": "deepseek-v4-pro", "messages": []}).encode()
    )
    assert orig == "deepseek-v4-pro"
    assert json.loads(body)["model"] == "deepseek-v4-flash"


def test_check_model_in_body_keeps_flash():
    body, orig = gateway._check_model_in_body(
        json.dumps({"model": "deepseek-v4-flash", "messages": []}).encode()
    )
    assert orig == ""
    assert body is None


def test_check_model_in_body_handles_edge_cases():
    # str 输入也算
    body, orig = gateway._check_model_in_body('{"model":"deepseek-v4-pro"}')
    assert orig == "deepseek-v4-pro"
    # 非 JSON
    assert gateway._check_model_in_body(b"not json") == (None, "")
    # 非 dict
    assert gateway._check_model_in_body(json.dumps([1, 2]).encode()) == (None, "")
    # 空
    assert gateway._check_model_in_body(b"") == (None, "")
    # 大小写：PRO / Pro 都要命中
    body, orig = gateway._check_model_in_body(
        json.dumps({"model": "DeepSeek-V4-PRO", "messages": []}).encode()
    )
    assert orig == "DeepSeek-V4-PRO"
    assert json.loads(body)["model"] == "deepseek-v4-flash"


# ---------------------------------------------------------------------------
# 2. 集成：requests 直连 DeepSeek Pro 必须被改写成 flash（故障注入）
# ---------------------------------------------------------------------------
def test_requests_adapter_rewrites_pro_to_flash(monkeypatch):
    """直接用 requests.post 打 DeepSeek Pro，经防火墙改写后落地的 body 必须是 flash。

    实现：把 HTTPAdapter.send 顶成捕获 spy（返回假 Response，零网络），
    再手动装防火墙（pytest 下 _auto_install 被跳过，需显式装），
    拦截点拿到的是「已被改写」的 request.body。
    """
    import requests
    from requests.models import Response

    captured = {}

    real_send = requests.adapters.HTTPAdapter.send

    def _spy_send(self, request, **kwargs):
        # 注意：此时 request.body 已被防火墙改写成 flash
        captured["body"] = request.body
        captured["url"] = request.url
        resp = Response()
        resp.status_code = 200
        resp._content = b'{"choices":[{"message":{"content":"hi"}}],"model":"deepseek-flash"}'
        resp.headers = {}
        resp.url = request.url
        return resp

    # 先顶成 spy，再装防火墙 —— 防火墙捕获的 orig 就是 spy
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _spy_send)
    gateway.install_deepseek_pro_firewall()

    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]},
        timeout=5,
    )

    assert resp.status_code == 200
    assert captured, "HTTPAdapter.send 未被调用 —— 请求没真正经过防火墙"
    sent = json.loads(captured["body"])
    assert sent["model"] == "deepseek-v4-flash", (
        "requests 直连 DeepSeek Pro 未被防火墙改写，发出 %r 会真实扣 Pro 费" % sent["model"]
    )
    assert "api.deepseek.com" in captured["url"]


def test_requests_adapter_leaves_non_deepseek_untouched(monkeypatch):
    """非 DeepSeek 的 requests 调用（如 Tushare/AKShare 数据抓取）必须原样放行。"""
    import requests
    from requests.models import Response

    captured = {}
    real_send = requests.adapters.HTTPAdapter.send

    def _spy_send(self, request, **kwargs):
        captured["url"] = request.url
        captured["body"] = request.body
        resp = Response()
        resp.status_code = 200
        resp._content = b'{}'
        resp.headers = {}
        resp.url = request.url
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _spy_send)
    gateway.install_deepseek_pro_firewall()

    requests.post(
        "https://api.tushare.pro/index/index/index_daily",
        json={"api_name": "index_daily", "token": "x"},
        timeout=5,
    )
    assert captured, "HTTPAdapter.send 未被调用"
    assert "api.deepseek.com" not in captured["url"]
    assert captured["body"] is not None  # 原样透传，未被改写
