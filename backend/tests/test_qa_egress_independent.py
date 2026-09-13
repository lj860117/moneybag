"""QA 独立端到端验证：跑测试不会再往企微发一个字节。

与工程师自带的 test_alert_push_test_mode_guard.py 的区别（刻意不复用其手法）：
  * 断言装在 **httpx.Client.send**（用例级 monkeypatch，默认就会跑，不需要
    `-p` 插件）：既能拿到"想发给谁"，也保证真发不出去。不看 print、
    不看 spy 的 __module__。
  * 强制把 wxwork_push 的模块级常量 _CORP_ID/_SECRET/_AGENT_ID 置为真值 ——
    本地环境这三个是空串，is_configured() 恒 False，会**根本走不到推送分支**。
    不补上这一步，所谓"零调用"是空转的绿（本地本来就连不出事故）。
  * 用 **真实的 402 事故路径**（复刻 test_chat_model_routing.py 的 fake httpx
    402 → gateway 回退 → maybe_alert_quota），而不是直接调 maybe_alert_quota。
  * 钉死推送窗口（见 push_window_open）：事故载荷在 519045b 后从 P0 降为 P2，
    而 P1/P2 在 23:00-08:00 会先于守卫 return —— 不钉死的话这两条用例
    **看时间下菜碟**（白天绿、凌晨红）。

铁律：故障注入必须成对。
  test_egress_blocked_when_guard_active       -> 绿（守卫有效）
  test_egress_really_happens_when_guard_off   -> 必须红（证明探针是活的）
"""

import json
import sys
import types

import pytest

from services import wxwork_push as wp
from services.llm_quota_alert import classify_llm_error_detail

import httpx

# ── 出口探针（本文件自带，不依赖 -p 插件，保证 CI 默认就会跑）────────
# 拦在 httpx.Client.send：既能拿到"想发给谁"（URL 主机），又能保证真发不出去。
# 选 httpx 层而不是 socket 层，是因为 socket 层全局阻断会误伤需要连外网的
# 行情类用例；本探针是**用例级** monkeypatch，跑完即恢复。
HTTP_EGRESS: list = []

# conftest 的 `_block_real_wecom_push` 是函数级 autouse，逐个用例才打桩；
# 模块导入（收集期）拿到的必然是**原始**出口，正是"摘掉 conftest"所需的还原点。
_ORIG_GET = wp._http_client.get
_ORIG_POST = wp._http_client.post


@pytest.fixture
def egress_probe(monkeypatch):
    """记录并阻断所有 httpx 出网请求（只在本用例内生效）。"""
    HTTP_EGRESS.clear()
    _orig_send = httpx.Client.send

    def _send(self, request, *a, **k):
        HTTP_EGRESS.append({
            "method": str(request.method),
            "host": str(request.url.host),
            "path": str(request.url.path),
        })
        raise RuntimeError(
            f"[QA] 已阻断 httpx 出网 {request.method} {request.url.host}")

    monkeypatch.setattr(httpx.Client, "send", _send, raising=True)
    yield HTTP_EGRESS


@pytest.fixture
def prod_like_wecom(monkeypatch):
    """把 wxwork_push 伪装成"生产已配置"状态。

    这是本次事故能成立的第二根支柱：_CORP_ID/_SECRET/_AGENT_ID 是 **import 期
    模块级常量**，conftest 那个"清空密钥环境变量"的 autouse fixture 对它无效。
    这里直接改常量，精确复现生产机上 is_configured() == True 的状态。
    """
    monkeypatch.setattr(wp, "_CORP_ID", "QA-FAKE-CORP", raising=False)
    monkeypatch.setattr(wp, "_SECRET", "QA-FAKE-SECRET", raising=False)
    monkeypatch.setattr(wp, "_AGENT_ID", "QA-FAKE-AGENT", raising=False)
    from infra.cache import MemoryCache
    monkeypatch.setattr(wp, "_token_cache", MemoryCache(default_ttl=7200),
                        raising=False)
    assert wp.is_configured() is True


@pytest.fixture
def conftest_net_block_removed(monkeypatch):
    """摘掉 conftest 那层网，让被测对象**只剩新守卫**这一道防线。

    不摘的话，conftest 会替新守卫挡下请求，测试绿了但证明的是 conftest 有效，
    不是新守卫有效。
    """
    monkeypatch.setattr(wp._http_client, "get", _ORIG_GET, raising=True)
    monkeypatch.setattr(wp._http_client, "post", _ORIG_POST, raising=True)


def _drive_real_402_incident_path(monkeypatch):
    """逐字复刻 2026-09-13 事故路径：fake httpx 造 402 → gateway 回退 → 告警。"""
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    class _FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload, ensure_ascii=False)

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, timeout=60):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            model = (json or {}).get("model")
            if model == "doubao-seed-2-1-turbo-260628":
                return _FakeResponse(402, {"error": "doubao quota exceeded"})
            return _FakeResponse(200, {
                "choices": [{"message": {"content": "deepseek still works"}}],
                "usage": {"total_tokens": 12, "prompt_tokens": 5,
                          "completion_tokens": 7},
            })

    monkeypatch.setitem(sys.modules, "httpx",
                        types.SimpleNamespace(Client=_FakeClient))
    monkeypatch.setattr(gw_mod, "_is_deepseek_peak_window",
                        lambda now=None: True)

    gateway = gw_mod.LLMGateway()
    return gateway.call_sync("现在给我一句结论", system="sys",
                             model_tier="llm_light", module="chat")


@pytest.fixture(params=[True, False], ids=["window_open", "window_closed"])
def push_window(request, monkeypatch):
    """推送窗口开/关两态都跑一遍（守卫必须在**两种**状态下都拦住）。

    历史（519045b 时）：当时短路排在窗口判断**之后**，事故载荷又从 P0 降为 P2，
    于是 23:00-08:00 之间用例是"因为压根没走到推送所以没出网"——空转的绿。
    当时靠钉死窗口为 True 来规避，但那会**掩盖真实行为**：钉死之后旧代码和
    新代码都通过，用例分辨不出短路到底有没有前移。

    54f3a92 把短路前移到窗口判断之前后，正确做法是不钉死、两态都验：
      window_closed 这一态就是**修复本身的回归网** —— 旧顺序下它会打印
      QUIET_HOURS_DEFERRED，断言 TEST_MODE_BLOCKED 必然转红。
    """
    from services import llm_quota_alert as qa
    monkeypatch.setattr(qa, "_in_push_window", lambda: request.param,
                        raising=True)
    return request.param


@pytest.fixture
def push_window_open(monkeypatch):
    """只把窗口打开 —— 供"反向证明"用例用。

    红用例要证明"关掉守卫就真能打出去"，而窗口关闭时本来就不推（生产语义），
    那时零出网说明不了任何问题，所以红用例必须开窗口。
    """
    from services import llm_quota_alert as qa
    monkeypatch.setattr(qa, "_in_push_window", lambda: True, raising=True)


# ── 绿：守卫生效时，真实 402 事故路径一个字节都发不出去 ──────────────
def test_egress_blocked_when_guard_active(monkeypatch, prod_like_wecom,
                                          conftest_net_block_removed,
                                          push_window, egress_probe,
                                          capsys):
    result = _drive_real_402_incident_path(monkeypatch)
    out = capsys.readouterr().out

    assert result["fallback_used"] is True
    assert egress_probe == [], f"守卫失效：仍走到了 HTTP 出口 {egress_probe}"
    # 反空转：必须是"守卫拦下的"，而不是"压根没走到推送"
    assert "[QUOTA_ALERT][TEST_MODE_BLOCKED]" in out, (
        f"没看到守卫拦截日志 —— 这条绿可能是空转的（没走到推送分支）：\n{out}")


# ── 红：关掉守卫，同一条路径必须真的打到出口（证明上面那条不是空转）──
def test_egress_really_happens_when_guard_off(monkeypatch, prod_like_wecom,
                                              conftest_net_block_removed,
                                              push_window_open, egress_probe):
    monkeypatch.setenv("MONEYBAG_TEST_MODE", "0")
    _drive_real_402_incident_path(monkeypatch)

    assert egress_probe, (
        "反向证明失败：关掉守卫后仍未走到 HTTP 出口 —— 上面的绿是空转的，"
        "不是守卫真的挡住了")
    hosts = {e["host"] for e in egress_probe}
    assert any("weixin" in h or "qyapi" in h for h in hosts), \
        f"出网目标不是企微：{hosts}"
    print(f"[QA] 反向证明：HTTP 目标={egress_probe}")


# ── V4：第二条真实出口 scripts/llm_balance_monitor._push_alert ──────
# 它不走 maybe_alert_quota，maybe_alert_quota 那道短路管不到它。
@pytest.mark.parametrize("guard_on", [True, False])
def test_monitor_second_exit(monkeypatch, prod_like_wecom,
                             conftest_net_block_removed, egress_probe,
                             guard_on):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from scripts import llm_balance_monitor as mon

    if not guard_on:
        monkeypatch.setenv("MONEYBAG_TEST_MODE", "0")
    mon._push_alert("QA 标题", "QA 内容")

    if guard_on:
        assert egress_probe == [], f"第二条出口没堵住：{egress_probe}"
    else:
        # 反向证明：关掉守卫，这条出口必须真的打出去
        assert any("weixin" in e["host"] for e in egress_probe), \
            f"第二条出口反向证明失败：{egress_probe}"


# ── V3：生产语义未被改坏 ────────────────────────────────────────────
def test_production_semantics_402_still_balance_exhausted():
    alert_type, code, _snip = classify_llm_error_detail(
        "doubao", 402, '{"error":{"code":"PaymentRequired"}}')
    assert alert_type == "doubao_balance_exhausted", alert_type
    assert code == "PaymentRequired", code
