import asyncio
import importlib
import json
import sys
import types
from datetime import datetime
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# 每次对话结束后，api/chat.py:127 会额外调一次 call_sync 做「记忆提取」。
# 桩类里不实现 call_sync 就会抛 AttributeError，而被 api/chat.py:143 的
# `except Exception` 静默吞掉 —— 用例照过，但这意味着**桩缺方法这件事本身
# 不会让任何人失败**。将来真实代码出同类错误，同样会被这句静默吞掉。
# 所以三个桩统一实现 call_sync，返回「无」：走 api/chat.py:135 的跳过分支，
# 不沉淀任何记忆，保持测试无副作用。
_STUB_MEMORY_RESULT = {
    "content": "无",
    "source": "ai",
    "fallback": False,
    "model": "",
    "tokens": 0,
}


def _stub_call_sync(self, prompt, **kwargs) -> dict:
    """桩的 call_sync：只服务记忆提取，返回「无」让它跳过沉淀。"""
    return dict(_STUB_MEMORY_RESULT)


def test_resolve_default_model_peak_prefers_doubao_for_interactive(monkeypatch):
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 6, 9, 30),
    )

    assert model == "doubao-seed-2-1-turbo-260628"


def test_resolve_default_model_peak_falls_back_to_deepseek_when_doubao_missing(monkeypatch):
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 6, 14, 1),
    )

    assert model == "deepseek-v4-flash"


def test_resolve_default_model_offpeak_prefers_deepseek(monkeypatch):
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 5, 20, 5),
    )

    assert model == "deepseek-v4-flash"


def test_resolve_default_model_peak_keeps_deepseek_when_alt_providers_missing(monkeypatch):
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 6, 9, 35),
    )

    assert model == "deepseek-v4-flash"


def test_resolve_model_candidates_switches_fallback_order_by_peak_window(monkeypatch):
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    peak_candidates = gw_mod.resolve_model_candidates(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 6, 9, 35),
    )
    offpeak_candidates = gw_mod.resolve_model_candidates(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 6, 20, 5),
    )

    assert peak_candidates == [
        "doubao-seed-2-1-turbo-260628",
        "deepseek-v4-flash",
    ]
    assert offpeak_candidates == [
        "deepseek-v4-flash",
        "doubao-seed-2-1-turbo-260628",
    ]


def test_resolve_default_model_noninteractive_keeps_deepseek_during_peak(monkeypatch):
    """非交互模块（晨报/运维等）峰段仍以 DeepSeek 为主，且 2026-09-11 起是 Flash 而非 Pro。

    这条断言的语义被「全面 Flash 化」推翻：旧断言要求 llm_heavy → deepseek-v4-pro。
    现在 llm_heavy 不再代表 Pro，只代表「更大输出预算 + 重档降级档位」。
    """
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    model = gw_mod.resolve_default_model(
        "llm_heavy",
        module="night_worker",
        now=datetime(2026, 7, 6, 10, 15),
    )

    assert model == "deepseek-v4-flash"


def test_heavy_tier_resolves_to_flash_with_cheap_fallback(monkeypatch):
    """全面 Flash 化：llm_heavy 主模型必须是 flash，降级必须是便宜的豆包 Turbo。

    覆盖晨报(night_worker)、运维分析(OPS_LLM_MODEL_TIER)、个股监控(close_review)、
    AI 选基(ai_pick_funds)、持仓诊断、self_audit、scenario_engine 等全部重档调用。
    """
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    candidates = gw_mod.resolve_model_candidates(
        "llm_heavy",
        module="night_worker",
        now=datetime(2026, 7, 6, 10, 15),   # 峰段但非交互模块 → deepseek 优先
    )

    assert candidates == [
        "deepseek-v4-flash",
        "doubao-seed-2-1-turbo-260628",
    ]


def test_explicit_pro_keeps_quality_fallback(monkeypatch):
    """用户在对话页显式选 Pro 时，降级必须保质量（豆包 Pro），不能降成 Turbo。"""
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    candidates = gw_mod.resolve_model_candidates(
        "llm_light",
        module="chat",
        explicit_model="deepseek-v4-pro",
        now=datetime(2026, 7, 6, 10, 15),
    )

    assert candidates == [
        "deepseek-v4-pro",
        "doubao-seed-2-1-pro-260628",
    ]


def test_peak_chat_auto_stays_cheap_and_doubao_first(monkeypatch):
    """峰谷窗口下 chat 走 auto：主模型仍须便宜档，且候选顺序豆包优先。"""
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    # 2026-07-06 是周一，10:15 落在 9~12 点峰段
    candidates = gw_mod.resolve_model_candidates(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 6, 10, 15),
    )
    default_model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 6, 10, 15),
    )

    assert candidates[0] == "doubao-seed-2-1-turbo-260628"   # 峰段豆包优先
    assert "pro" not in candidates[0]                        # 主模型是便宜档
    assert "pro" not in candidates[1]                        # 降级也是便宜档
    assert default_model == "doubao-seed-2-1-turbo-260628"


def test_llm_cache_key_includes_model_to_avoid_cross_model_reuse(tmp_path, monkeypatch):
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    gateway = gw_mod.LLMGateway()

    deepseek_key = gateway._cache_key("LeiJiang", "chat", "现在市场怎么样", "sys", "deepseek-v4-flash")
    doubao_key = gateway._cache_key("LeiJiang", "chat", "现在市场怎么样", "sys", "doubao-seed-2-1-turbo-260628")

    assert deepseek_key != doubao_key


def test_call_sync_peak_falls_back_to_deepseek_when_alt_providers_exhausted(monkeypatch, tmp_path):
    import infra.llm.gateway as infra_gw
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    # 阶段 1 迁移后，实现本体在 infra/llm/gateway.py；services 壳只转发公共符号，
    # 私有函数 `_is_deepseek_peak_window` 需在实现本体模块上 monkeypatch 才会生效。
    monkeypatch.setattr(infra_gw, "_is_deepseek_peak_window", lambda now=None: True)

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
            if model == "deepseek-v4-flash":
                return _FakeResponse(200, {
                    "choices": [{"message": {"content": "deepseek still works"}}],
                    "usage": {"total_tokens": 12, "prompt_tokens": 5, "completion_tokens": 7},
                })
            raise AssertionError(f"unexpected model {model}")

    fake_httpx = types.SimpleNamespace(Client=_FakeClient)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    gateway = gw_mod.LLMGateway()
    result = gateway.call_sync("现在给我一句结论", system="sys", model_tier="llm_light", module="chat")

    assert result["model"] == "deepseek-v4-flash"
    assert result["source"] == "ai"
    assert result["fallback_used"] is True
    assert result["content"] == "deepseek still works"


def test_list_models_returns_peak_aware_default(monkeypatch):
    fake_llm_gateway = types.ModuleType("infra.llm.gateway")
    fake_llm_gateway.resolve_default_model = lambda model_tier="llm_light", module="": "doubao-seed-2-1-turbo-260628"
    monkeypatch.setitem(sys.modules, "infra.llm.gateway", fake_llm_gateway)

    import api.chat as chat

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    data = chat.list_models()

    assert data["default"] == "auto"
    assert {item["provider"] for item in data["models"]} == {"deepseek", "doubao", "auto"}


def test_chat_analysis_passes_explicit_model_to_gateway(monkeypatch):
    captured = {}
    fake_llm_gateway = types.ModuleType("infra.llm.gateway")

    class _FakeGateway:
        @staticmethod
        def instance():
            return _FakeGateway()

        def get_api_config(self, model_tier="llm_light", module=""):
            return {
                "api_key": "ds",
                "api_base": "https://api.deepseek.com/v1",
                "model": "doubao-seed-2-1-turbo-260628",
            }

        def call_sync(self, prompt, **kwargs):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return {
                "content": "ok",
                "fallback": False,
                "model": kwargs.get("explicit_model", ""),
                "source": "ai",
            }

    fake_llm_gateway.LLMGateway = _FakeGateway
    fake_llm_gateway.resolve_default_model = lambda model_tier="llm_light", module="": "doubao-seed-2-1-turbo-260628"
    monkeypatch.setitem(sys.modules, "infra.llm.gateway", fake_llm_gateway)

    import api.chat as chat
    from models.schemas import ChatRequest

    monkeypatch.setattr(chat, "_build_market_context", lambda: "")
    monkeypatch.setattr(chat, "_build_portfolio_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat, "_build_system_prompt", lambda *args, **kwargs: "sys")
    monkeypatch.setattr(chat, "classify_chat_intent", lambda *_args, **_kwargs: {"intent": "general"})
    monkeypatch.setattr(chat, "_check_preset_answer", lambda *args, **kwargs: None)
    monkeypatch.setenv("LLM_API_KEY", "ds")

    result = asyncio.run(chat.chat_analysis(ChatRequest(message="现在市场怎么样", model="doubao-seed-2-1-turbo-260628")))

    assert captured["explicit_model"] == "doubao-seed-2-1-turbo-260628"
    assert captured["module"] == "chat"
    assert result["source"] == "ai"


def test_chat_stream_fc_uses_peak_aware_default_model(monkeypatch):
    captured = {}
    fake_llm_gateway = types.ModuleType("infra.llm.gateway")

    class _FakeGateway:
        @staticmethod
        def instance():
            return _FakeGateway()

        def get_api_config(self, model_tier="llm_light", module=""):
            return {
                "api_key": "db",
                "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "doubao-seed-2-1-turbo-260628",
            }

        def pre_check(self):
            return True

        call_sync = _stub_call_sync

    fake_llm_gateway.LLMGateway = _FakeGateway
    fake_llm_gateway.resolve_default_model = lambda model_tier="llm_light", module="": "doubao-seed-2-1-turbo-260628"
    monkeypatch.setitem(sys.modules, "infra.llm.gateway", fake_llm_gateway)

    import api.chat as chat
    import api.chat_fc as chat_fc
    from models.schemas import ChatRequest

    monkeypatch.setattr(chat, "_build_market_context", lambda: "")
    monkeypatch.setattr(chat, "_build_portfolio_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat, "classify_chat_intent", lambda *_args, **_kwargs: {"intent": "general"})
    monkeypatch.setattr(chat_fc, "should_use_fc", lambda *_args, **_kwargs: True)

    def _fake_fc_stream(user_msg, system_prompt, user_id, model="", history=None, max_rounds=4):
        captured["model"] = model
        yield {"delta": "", "done": True}

    monkeypatch.setattr(chat_fc, "run_fc_agent_stream", _fake_fc_stream)

    async def _collect_first_chunk():
        response = await chat.chat_analysis_stream(ChatRequest(message="帮我比较沪深300和中证1000", userId="LeiJiang"))
        body = []
        async for chunk in response.body_iterator:
            body.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
            break
        return "".join(body)

    payload = asyncio.run(_collect_first_chunk())

    # auto 哨兵归一化后传给 FC 的是空串，由 chat_fc 内部按峰谷解析默认模型
    assert captured["model"] == ""
    assert "done" in payload


def test_chat_stream_done_event_preserves_model_and_fallback(monkeypatch):
    fake_llm_gateway = types.ModuleType("infra.llm.gateway")

    class _FakeGateway:
        @staticmethod
        def instance():
            return _FakeGateway()

        def get_api_config(self, model_tier="llm_light", module=""):
            return {
                "api_key": "db",
                "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "doubao-seed-2-1-turbo-260628",
            }

        def pre_check(self):
            return True

        def stream_sync(self, prompt, **kwargs):
            yield {"delta": "前端标签修复完成", "phase": "answering", "done": False}
            yield {"delta": "", "done": True, "model": "doubao-seed-2-1-turbo-260628", "fallback_used": False}

        call_sync = _stub_call_sync

    fake_llm_gateway.LLMGateway = _FakeGateway
    fake_llm_gateway.resolve_default_model = lambda model_tier="llm_light", module="": "doubao-seed-2-1-turbo-260628"
    monkeypatch.setitem(sys.modules, "infra.llm.gateway", fake_llm_gateway)

    import api.chat as chat
    from models.schemas import ChatRequest

    monkeypatch.setattr(chat, "_build_market_context", lambda: "")
    monkeypatch.setattr(chat, "_build_portfolio_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat, "_build_system_prompt", lambda *args, **kwargs: "sys")
    monkeypatch.setattr(chat, "classify_chat_intent", lambda *_args, **_kwargs: {"intent": "general"})
    monkeypatch.setattr(chat, "_rule_based_reply", lambda *args, **kwargs: "rule")

    async def _collect_body():
        response = await chat.chat_analysis_stream(ChatRequest(message="请只回答八个字", userId="LeiJiang", model="doubao-seed-2-1-turbo-260628", history=[]))
        body = []
        async for chunk in response.body_iterator:
            body.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        return "".join(body)

    payload = asyncio.run(_collect_body())

    assert '"model": "doubao-seed-2-1-turbo-260628"' in payload
    assert '"fallback_used": false' in payload
    assert '"served_by": "llm"' in payload


def test_chat_stream_fc_hard_failure_falls_back_to_normal_chat(monkeypatch):
    fake_llm_gateway = types.ModuleType("infra.llm.gateway")

    class _FakeGateway:
        @staticmethod
        def instance():
            return _FakeGateway()

        def get_api_config(self, model_tier="llm_light", module=""):
            return {
                "api_key": "ds",
                "api_base": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
            }

        def pre_check(self):
            return True

        def stream_sync(self, prompt, **kwargs):
            yield {"delta": "回退成功", "phase": "answering", "done": False}
            yield {"delta": "", "done": True, "model": "deepseek-v4-flash", "fallback_used": False}

        call_sync = _stub_call_sync

    fake_llm_gateway.LLMGateway = _FakeGateway
    monkeypatch.setitem(sys.modules, "infra.llm.gateway", fake_llm_gateway)

    import api.chat as chat
    import api.chat_fc as chat_fc
    from models.schemas import ChatRequest

    monkeypatch.setattr(chat, "_build_market_context", lambda: "")
    monkeypatch.setattr(chat, "_build_portfolio_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat, "_build_system_prompt", lambda *args, **kwargs: "sys")
    monkeypatch.setattr(chat, "classify_chat_intent", lambda *_args, **_kwargs: {"intent": "general"})
    monkeypatch.setattr(chat, "_rule_based_reply", lambda *args, **kwargs: "rule")
    monkeypatch.setattr(chat_fc, "should_use_fc", lambda *_args, **_kwargs: True)

    def _fake_fc_hard_fail(user_msg, system_prompt, user_id, model="", history=None, max_rounds=4):
        yield {"delta": "AI 暂时不可用（所有模型都失败）", "done": True, "source": "error"}

    monkeypatch.setattr(chat_fc, "run_fc_agent_stream", _fake_fc_hard_fail)

    async def _collect_body():
        response = await chat.chat_analysis_stream(
            ChatRequest(message="帮我比较沪深300和中证1000", userId="LeiJiang", model="auto")
        )
        body = []
        async for chunk in response.body_iterator:
            body.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        return "".join(body)

    payload = asyncio.run(_collect_body())

    assert '"served_by": "llm"' in payload
    assert "所有模型都失败" not in payload


# =============================================================================
# P2-4：thinking 开关按「实际解析出的模型」判定，不按 model_tier 标签
# =============================================================================
# 背景
# ----
# 全面 Flash 化后 MODEL_ROUTING["llm_heavy"] 也解析成 deepseek-v4-flash，但
# gateway 里「要不要关 thinking」仍按 model_tier 判断：llm_light 才关。
# 结果晨报/监控/诊断/self_audit/scenario_engine 这些后台跑批（全部走 llm_heavy）
# 全都保留了 thinking。2026-09-11 实测：flash 带 thinking 的 completion token
# 是关闭状态的 6.9~8.0 倍 —— Flash 化省下的钱基本被吃回去。
#
# 收敛规则：DeepSeek 按实际模型判（含 pro 才保留 thinking），豆包保持既有逻辑。
# 判据复用 _fallback_tier_for()，与降级档位共用同一个「是不是 pro 档」的定义，
# 不新造平行判断函数。
#
# 这些用例断言的是**真正发出去的 request body**，不是推断。

def _capture_request_body(monkeypatch, *, prompt, model_tier, module="",
                          explicit_model="", max_tokens=800,
                          force_no_thinking=False):
    """跑一次 call_sync，拦截 httpx 返回真正发出去的 request body。"""
    import httpx

    import infra.llm.gateway as gw_mod

    bodies = []

    class _FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None, **kwargs):
            bodies.append(json)
            return _FakeResponse({
                "choices": [{"message": {"content": "ok"}}],
                "model": (json or {}).get("model", ""),
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    # 每次新建实例，避免单例的响应缓存让第二次调用直接命中而不发请求
    gw = gw_mod.LLMGateway()
    gw.call_sync(
        prompt,
        system="",
        model_tier=model_tier,
        module=module,
        max_tokens=max_tokens,
        explicit_model=explicit_model,
        force_no_thinking=force_no_thinking,
    )

    assert len(bodies) == 1, "期望发出 1 次请求，实际 %d 次（桩或缓存异常）" % len(bodies)
    return bodies[0]


def test_llm_heavy_backend_batch_disables_thinking(monkeypatch):
    """P2-4 核心回归：llm_heavy 已解析成 flash，必须关 thinking。

    这条在改动前是漏的 —— 按 model_tier 判，llm_heavy 会被当成「重档」而保留
    thinking，所有后台跑批都中招。
    """
    body = _capture_request_body(
        monkeypatch,
        prompt="llm_heavy 后台跑批 thinking 断言",
        model_tier="llm_heavy",
        module="morning_brief",
    )

    assert body["model"] == "deepseek-v4-flash"
    assert body.get("thinking") == {"type": "disabled"}, (
        "model_tier=llm_heavy 解析出来的是 flash，却仍保留 thinking；"
        "实测 flash 带 thinking 的 completion token 是关闭状态的 6.9~8.0 倍"
    )
    # 顺带守住 gateway.py 顶部那条「llm_heavy 抬高 max_tokens 下限」未被本次改动波及
    assert body["max_tokens"] == 3000


def test_llm_light_backend_disables_thinking(monkeypatch):
    """llm_light 后台调用：行为与改动前一致，仍要关。"""
    body = _capture_request_body(
        monkeypatch,
        prompt="llm_light 后台调用 thinking 断言",
        model_tier="llm_light",
        module="self_audit",
    )

    assert body["model"] == "deepseek-v4-flash"
    assert body.get("thinking") == {"type": "disabled"}


def test_chat_explicit_pro_keeps_thinking(monkeypatch):
    """对话页手动选 Pro：用户显式为质量付费，thinking 必须保留。"""
    body = _capture_request_body(
        monkeypatch,
        prompt="对话页手动选 Pro thinking 断言",
        model_tier="llm_light",
        module="chat",
        explicit_model="deepseek-v4-pro",
    )

    assert body["model"] == "deepseek-v4-pro"
    assert "thinking" not in body, (
        "用户显式选了 Pro 却被关掉 thinking；判据要按实际解析出的模型，不是 tier 标签"
    )


def test_force_no_thinking_overrides_explicit_pro(monkeypatch):
    """短输出点显式要求关推理时，即便实际模型是 Pro 也要关掉。"""
    body = _capture_request_body(
        monkeypatch,
        prompt="force_no_thinking 覆盖 Pro 断言",
        model_tier="llm_light",
        module="chat",
        explicit_model="deepseek-v4-pro",
        force_no_thinking=True,
    )

    assert body["model"] == "deepseek-v4-pro"
    assert body.get("thinking") == {"type": "disabled"}


def test_doubao_turbo_under_llm_heavy_still_disables_thinking(monkeypatch):
    """豆包保持 v9.5.130 既有逻辑：llm_heavy + doubao turbo 仍要关 thinking。

    这条专门挡「把整个判据换成 _fallback_tier_for(use_model)」的偷懒改法：
    doubao-seed-2-1-turbo 不含 "pro"，会被判成轻档而漏关。
    """
    body = _capture_request_body(
        monkeypatch,
        prompt="豆包 turbo 重档 thinking 断言",
        model_tier="llm_heavy",
        module="morning_brief",
        explicit_model="doubao-seed-2-1-turbo-260628",
    )

    assert body["model"] == "doubao-seed-2-1-turbo-260628"
    assert body.get("thinking") == {"type": "disabled"}


def test_doubao_turbo_under_llm_light_keeps_default_thinking(monkeypatch):
    """豆包轻档：既有逻辑就是不设 thinking，不得被本次改动带偏。"""
    body = _capture_request_body(
        monkeypatch,
        prompt="豆包 turbo 轻档 thinking 断言",
        model_tier="llm_light",
        module="chat",
        explicit_model="doubao-seed-2-1-turbo-260628",
    )

    assert body["model"] == "doubao-seed-2-1-turbo-260628"
    assert "thinking" not in body
