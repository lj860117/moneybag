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


def test_resolve_default_model_peak_prefers_doubao_for_interactive(monkeypatch):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 5, 9, 30),
    )

    assert model == "doubao-seed-2-0-lite-260215"


def test_resolve_default_model_peak_falls_back_to_qwen_when_doubao_missing(monkeypatch):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 5, 14, 1),
    )

    assert model == "qwen3.6-flash"


def test_resolve_default_model_offpeak_prefers_deepseek(monkeypatch):
    import services.llm_gateway as gw_mod

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
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 5, 9, 35),
    )

    assert model == "deepseek-v4-flash"


def test_resolve_model_candidates_switches_fallback_order_by_peak_window(monkeypatch):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    peak_candidates = gw_mod.resolve_model_candidates(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 5, 9, 35),
    )
    offpeak_candidates = gw_mod.resolve_model_candidates(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 5, 20, 5),
    )

    assert peak_candidates == [
        "doubao-seed-2-0-lite-260215",
        "qwen3.6-flash",
        "deepseek-v4-flash",
    ]
    assert offpeak_candidates == [
        "deepseek-v4-flash",
        "doubao-seed-2-0-mini-260215",
        "qwen3.6-flash",
    ]


def test_resolve_default_model_noninteractive_keeps_deepseek_during_peak(monkeypatch):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    model = gw_mod.resolve_default_model(
        "llm_heavy",
        module="night_worker",
        now=datetime(2026, 7, 5, 10, 15),
    )

    assert model == "deepseek-v4-pro"


def test_llm_cache_key_includes_model_to_avoid_cross_model_reuse(tmp_path, monkeypatch):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    gateway = gw_mod.LLMGateway()

    deepseek_key = gateway._cache_key("LeiJiang", "chat", "现在市场怎么样", "sys", "deepseek-v4-flash")
    doubao_key = gateway._cache_key("LeiJiang", "chat", "现在市场怎么样", "sys", "doubao-seed-2-0-lite-260215")

    assert deepseek_key != doubao_key


def test_call_sync_peak_falls_back_to_deepseek_when_alt_providers_exhausted(monkeypatch, tmp_path):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")
    monkeypatch.setattr(gw_mod, "_is_deepseek_peak_window", lambda now=None: True)

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
            if model == "doubao-seed-2-0-lite-260215":
                return _FakeResponse(402, {"error": "doubao quota exceeded"})
            if model == "qwen3.6-flash":
                return _FakeResponse(402, {"error": "qwen quota exceeded"})
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
    fake_llm_gateway = types.ModuleType("services.llm_gateway")
    fake_llm_gateway.resolve_default_model = lambda model_tier="llm_light", module="": "doubao-seed-2-0-lite-260215"
    monkeypatch.setitem(sys.modules, "services.llm_gateway", fake_llm_gateway)

    import api.chat as chat

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    data = chat.list_models()

    assert data["default"] == "doubao-seed-2-0-lite-260215"
    assert {item["provider"] for item in data["models"]} == {"deepseek", "doubao", "qwen"}


def test_chat_analysis_passes_explicit_model_to_gateway(monkeypatch):
    captured = {}
    fake_llm_gateway = types.ModuleType("services.llm_gateway")

    class _FakeGateway:
        @staticmethod
        def instance():
            return _FakeGateway()

        def get_api_config(self, model_tier="llm_light", module=""):
            return {
                "api_key": "ds",
                "api_base": "https://api.deepseek.com/v1",
                "model": "doubao-seed-2-0-lite-260215",
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
    fake_llm_gateway.resolve_default_model = lambda model_tier="llm_light", module="": "doubao-seed-2-0-lite-260215"
    monkeypatch.setitem(sys.modules, "services.llm_gateway", fake_llm_gateway)

    import api.chat as chat
    from models.schemas import ChatRequest

    monkeypatch.setattr(chat, "_build_market_context", lambda: "")
    monkeypatch.setattr(chat, "_build_portfolio_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat, "_build_system_prompt", lambda *args, **kwargs: "sys")
    monkeypatch.setattr(chat, "classify_chat_intent", lambda *_args, **_kwargs: {"intent": "general"})
    monkeypatch.setattr(chat, "_check_preset_answer", lambda *args, **kwargs: None)
    monkeypatch.setenv("LLM_API_KEY", "ds")

    result = asyncio.run(chat.chat_analysis(ChatRequest(message="现在市场怎么样", model="qwen3.6-flash")))

    assert captured["explicit_model"] == "qwen3.6-flash"
    assert captured["module"] == "chat"
    assert result["source"] == "ai"


def test_chat_stream_fc_uses_peak_aware_default_model(monkeypatch):
    captured = {}
    fake_llm_gateway = types.ModuleType("services.llm_gateway")

    class _FakeGateway:
        @staticmethod
        def instance():
            return _FakeGateway()

        def get_api_config(self, model_tier="llm_light", module=""):
            return {
                "api_key": "db",
                "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "doubao-seed-2-0-lite-260215",
            }

        def pre_check(self):
            return True

    fake_llm_gateway.LLMGateway = _FakeGateway
    fake_llm_gateway.resolve_default_model = lambda model_tier="llm_light", module="": "doubao-seed-2-0-lite-260215"
    monkeypatch.setitem(sys.modules, "services.llm_gateway", fake_llm_gateway)

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

    assert captured["model"] == "doubao-seed-2-0-lite-260215"
    assert "done" in payload


def test_chat_stream_done_event_preserves_model_and_fallback(monkeypatch):
    fake_llm_gateway = types.ModuleType("services.llm_gateway")

    class _FakeGateway:
        @staticmethod
        def instance():
            return _FakeGateway()

        def get_api_config(self, model_tier="llm_light", module=""):
            return {
                "api_key": "qw",
                "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen3.6-flash",
            }

        def pre_check(self):
            return True

        def stream_sync(self, prompt, **kwargs):
            yield {"delta": "前端标签修复完成", "phase": "answering", "done": False}
            yield {"delta": "", "done": True, "model": "qwen3.6-flash", "fallback_used": False}

    fake_llm_gateway.LLMGateway = _FakeGateway
    fake_llm_gateway.resolve_default_model = lambda model_tier="llm_light", module="": "qwen3.6-flash"
    monkeypatch.setitem(sys.modules, "services.llm_gateway", fake_llm_gateway)

    import api.chat as chat
    from models.schemas import ChatRequest

    monkeypatch.setattr(chat, "_build_market_context", lambda: "")
    monkeypatch.setattr(chat, "_build_portfolio_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat, "_build_system_prompt", lambda *args, **kwargs: "sys")
    monkeypatch.setattr(chat, "classify_chat_intent", lambda *_args, **_kwargs: {"intent": "general"})
    monkeypatch.setattr(chat, "_rule_based_reply", lambda *args, **kwargs: "rule")

    async def _collect_body():
        response = await chat.chat_analysis_stream(ChatRequest(message="请只回答八个字", userId="LeiJiang", model="qwen3.6-flash", history=[]))
        body = []
        async for chunk in response.body_iterator:
            body.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        return "".join(body)

    payload = asyncio.run(_collect_body())

    assert '"model": "qwen3.6-flash"' in payload
    assert '"fallback_used": false' in payload
    assert '"served_by": "llm"' in payload
