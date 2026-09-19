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


def test_resolve_default_model_peak_prefers_deepseek_for_interactive(monkeypatch):
    """峰段 chat 走 auto：主模型必须是 DeepSeek flash，不能是豆包 turbo。

    ⚠️ 业务规则变更（2026-09-19 全面 Flash 化）：
    本用例原名 `..._peak_prefers_doubao_for_interactive`，断言峰段把交互对话
    导去豆包。那是 Pro 时代的遗留规则 —— 当年 DeepSeek Pro 高峰 output
    ¥27/百万，导去豆包 Pro（¥30/百万）勉强说得通。

    Flash 化后算不过账了：
      - 高峰：DeepSeek flash ¥9/百万   vs 豆包 turbo ¥15/百万 → 导豆包贵 67%
      - 低谷：DeepSeek flash ¥4.5/百万 vs 豆包 turbo ¥15/百万 → 贵 233%
    即任何时段 DeepSeek flash 都比豆包 turbo 便宜，高峰期导豆包是纯亏。
    故改为断言峰段同样优先 deepseek，豆包只作降级兜底。
    """
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qw")

    model = gw_mod.resolve_default_model(
        "llm_light",
        module="chat",
        now=datetime(2026, 7, 6, 9, 30),
    )

    assert model == "deepseek-v4-flash"


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


def test_resolve_model_candidates_keeps_deepseek_first_in_any_window(monkeypatch):
    """峰段/谷段候选顺序必须一致：都是 deepseek 在前、豆包在后。

    ⚠️ 业务规则变更（2026-09-19 全面 Flash 化）：
    本用例原名 `..._switches_fallback_order_by_peak_window`，断言候选顺序
    **随峰谷窗口对调**（峰段豆包在前）。新规则下不再对调 —— DeepSeek flash
    在峰段 ¥9/百万、谷段 ¥4.5/百万，都低于豆包 turbo 的 ¥15/百万，
    任何时段导去豆包都更贵。豆包退化为纯粹的降级兜底。

    这里同时验峰段和谷段，正是为了钉死「顺序不再随窗口切换」这件事：
    只验一个时段的话，旧的对调逻辑有 50% 概率照样能绿。
    """
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
        "deepseek-v4-flash",
        "doubao-seed-2-1-turbo-260628",
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


def test_explicit_pro_normalized_to_flash(monkeypatch):
    """v9.9.62 全局 Flash 化：显式选 Pro 一律归一化为 flash，降级也是便宜档。

    旧语义（显式选 Pro → 豆包 Pro 兜底）已被推翻：对话页 sticky localStorage
    选过 Pro 后会永久按 Pro 计费，用户要求所有调用一律 Flash。
    """
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
        "deepseek-v4-flash",
        "doubao-seed-2-1-turbo-260628",
    ]


def test_normalize_explicit_model_rules():
    """normalize_explicit_model 归一化规则：两家的 pro 档各归便宜档，其余不动。"""
    import infra.llm.gateway as gw_mod

    assert gw_mod.normalize_explicit_model("deepseek-v4-pro") == "deepseek-v4-flash"
    assert gw_mod.normalize_explicit_model("DeepSeek-V4-Pro") == "deepseek-v4-flash"
    assert gw_mod.normalize_explicit_model("deepseek-v4-flash") == "deepseek-v4-flash"
    # v9.9.63：豆包 Pro 一并下架，归一化到 Turbo
    assert gw_mod.normalize_explicit_model("doubao-seed-2-1-pro-260628") == "doubao-seed-2-1-turbo-260628"
    assert gw_mod.normalize_explicit_model("doubao-seed-2-1-turbo-260628") == "doubao-seed-2-1-turbo-260628"
    assert gw_mod.normalize_explicit_model("") == ""
    assert gw_mod.normalize_explicit_model("auto") == "auto"


def test_doubao_heavy_fallback_is_cheap_tier(monkeypatch):
    """v9.9.63：豆包重档降级也必须是 Turbo，不得再落 Pro。"""
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    candidates = gw_mod.resolve_model_candidates(
        "llm_heavy",
        module="morning_brief",
        explicit_model="doubao-seed-2-1-pro-260628",
        now=datetime(2026, 7, 6, 10, 15),
    )

    assert candidates[0] == "doubao-seed-2-1-turbo-260628"
    assert all("pro" not in m for m in candidates)


def test_peak_chat_auto_stays_cheap_and_deepseek_first(monkeypatch):
    """峰谷窗口下 chat 走 auto：主模型仍须便宜档，且候选顺序 deepseek 优先。

    ⚠️ 业务规则变更（2026-09-19 全面 Flash 化）：
    原名 `..._and_doubao_first`，断言峰段豆包优先。现改为 deepseek 优先 ——
    DeepSeek flash 峰段 ¥9/百万 < 豆包 turbo ¥15/百万，峰段导豆包反而更贵。
    「主模型与降级档都必须是便宜档（不含 pro）」这条约束不变，继续保留。
    """
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

    assert candidates[0] == "deepseek-v4-flash"              # 峰段 deepseek 优先
    assert "pro" not in candidates[0]                        # 主模型是便宜档
    assert "pro" not in candidates[1]                        # 降级也是便宜档
    assert default_model == "deepseek-v4-flash"


def test_llm_cache_key_includes_model_to_avoid_cross_model_reuse(tmp_path, monkeypatch):
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    gateway = gw_mod.LLMGateway()

    deepseek_key = gateway._cache_key("LeiJiang", "chat", "现在市场怎么样", "sys", "deepseek-v4-flash")
    doubao_key = gateway._cache_key("LeiJiang", "chat", "现在市场怎么样", "sys", "doubao-seed-2-1-turbo-260628")

    assert deepseek_key != doubao_key


def test_call_sync_falls_back_to_doubao_when_deepseek_exhausted(monkeypatch, tmp_path):
    """主 provider（deepseek）配额打满时，必须降级到豆包并标记 fallback_used。

    ⚠️ 业务规则变更（2026-09-19 全面 Flash 化）：
    原名 `..._peak_falls_back_to_deepseek_when_alt_providers_exhausted`。
    旧版的前提是「峰段豆包优先」，于是造豆包 402、deepseek 200，验证
    「豆包挂了能退回 deepseek」。新规则下 deepseek 在任何时段都是主 provider，
    那个前提不成立了 —— 若夹具不改，deepseek 首次即成功，fallback_used 会是
    False，用例就从「验降级链」退化成「验 happy path」，等于把回归网拆了。

    所以这里把 402 挪到新的主 provider（deepseek）上，豆包返回 200：
    仍然是在验「主 provider 配额打满 → 降级到备 provider → fallback_used=True」，
    断言强度一点没降，只是跟随新的 provider 顺序。

    `_is_deepseek_peak_window` 仍被钉死为 True —— 现在它是**故意的 no-op**：
    峰段也必须走 deepseek 优先，钉死峰段正好能证明峰谷窗口不再翻转顺序。
    """
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
            # 402 挪到新的主 provider（deepseek）上 —— 见上方 docstring
            if model == "deepseek-v4-flash":
                return _FakeResponse(402, {"error": "deepseek quota exceeded"})
            if model == "doubao-seed-2-1-turbo-260628":
                return _FakeResponse(200, {
                    "choices": [{"message": {"content": "doubao still works"}}],
                    "usage": {"total_tokens": 12, "prompt_tokens": 5, "completion_tokens": 7},
                })
            raise AssertionError(f"unexpected model {model}")

    fake_httpx = types.SimpleNamespace(Client=_FakeClient)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    gateway = gw_mod.LLMGateway()
    result = gateway.call_sync("现在给我一句结论", system="sys", model_tier="llm_light", module="chat")

    assert result["model"] == "doubao-seed-2-1-turbo-260628"
    assert result["source"] == "ai"
    assert result["fallback_used"] is True
    assert result["content"] == "doubao still works"


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

        def pre_check(self, user_id=""):
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

        def pre_check(self, user_id=""):
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

        def pre_check(self, user_id=""):
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


def test_chat_explicit_pro_normalized_to_flash_thinking_disabled(monkeypatch):
    """v9.9.62 全局 Flash 化：对话页显式传 Pro 也按 flash 执行，thinking 关闭。"""
    body = _capture_request_body(
        monkeypatch,
        prompt="对话页显式 Pro 归一化 thinking 断言",
        model_tier="llm_light",
        module="chat",
        explicit_model="deepseek-v4-pro",
    )

    assert body["model"] == "deepseek-v4-flash"
    assert body.get("thinking") == {"type": "disabled"}


def test_force_no_thinking_with_explicit_pro_also_flash(monkeypatch):
    """短输出点显式传 Pro：同样归一化为 flash 并关闭 thinking。"""
    body = _capture_request_body(
        monkeypatch,
        prompt="force_no_thinking 覆盖 Pro 断言",
        model_tier="llm_light",
        module="chat",
        explicit_model="deepseek-v4-pro",
        force_no_thinking=True,
    )

    assert body["model"] == "deepseek-v4-flash"
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


# -----------------------------------------------------------------------------
# P2-4b：stream_sync 的同一份判据（对话页真实路径）
# -----------------------------------------------------------------------------
# api/chat.py:72 / 618 / 835 三个对话入口全走 stream_sync。同步侧改了、流式侧
# 没改，等于「手动选 Pro 保留 thinking」在真实路径上不生效。这里把上面 6 条
# 断言对称地搬到流式路径，两份逻辑必须始终一致。

def _capture_stream_body(monkeypatch, *, prompt, model_tier, module="",
                         explicit_model="", max_tokens=1200,
                         force_no_thinking=False):
    """跑一次 stream_sync，拦截 httpx 返回真正发出去的 request body。"""
    import httpx

    import infra.llm.gateway as gw_mod

    bodies = []

    class _FakeStreamResp:
        status_code = 200

        def read(self):
            return b""

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield ('data: {"choices":[{"delta":{}}],'
                   '"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}')
            yield "data: [DONE]"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, headers=None, json=None, **kwargs):
            bodies.append(json)
            return _FakeStreamResp()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")

    gw = gw_mod.LLMGateway()
    list(gw.stream_sync(
        prompt,
        system="",
        model_tier=model_tier,
        module=module,
        max_tokens=max_tokens,
        explicit_model=explicit_model,
        force_no_thinking=force_no_thinking,
    ))

    assert len(bodies) == 1, "期望发出 1 次流式请求，实际 %d 次" % len(bodies)
    return bodies[0]


def test_stream_llm_heavy_disables_thinking(monkeypatch):
    """P2-4b 核心：流式侧 llm_heavy 解析成 flash，必须关 thinking。"""
    body = _capture_stream_body(
        monkeypatch,
        prompt="流式 llm_heavy thinking 断言",
        model_tier="llm_heavy",
        module="morning_brief",
    )

    assert body["model"] == "deepseek-v4-flash"
    assert body.get("thinking") == {"type": "disabled"}, (
        "流式侧 model_tier=llm_heavy 解析出来的是 flash，却仍保留 thinking；"
        "同步侧已修，流式侧必须与之一致"
    )
    # 守住 gateway.py 流式侧的 max_tokens 下限未被本次改动波及
    assert body["max_tokens"] == 3000


def test_stream_llm_light_disables_thinking(monkeypatch):
    """流式 llm_light：与改动前一致，仍要关。"""
    body = _capture_stream_body(
        monkeypatch,
        prompt="流式 llm_light thinking 断言",
        model_tier="llm_light",
        module="self_audit",
    )

    assert body["model"] == "deepseek-v4-flash"
    assert body.get("thinking") == {"type": "disabled"}


def test_stream_chat_explicit_pro_normalized_to_flash(monkeypatch):
    """v9.9.62 全局 Flash 化（流式侧）：显式传 Pro 也归一化为 flash。

    改动前这条断言要求 explicit_model=deepseek-v4-pro 透传并保留 thinking；
    2026-09-19 起全局统一 Flash，Pro 在 gateway 入口即被归一化。
    """
    body = _capture_stream_body(
        monkeypatch,
        prompt="流式对话页显式 Pro 归一化断言",
        model_tier="llm_light",
        module="chat",
        explicit_model="deepseek-v4-pro",
    )

    assert body["model"] == "deepseek-v4-flash"
    assert body.get("thinking") == {"type": "disabled"}, (
        "流式侧显式 Pro 归一化为 flash 后必须关 thinking；流式判据必须与同步侧一致"
    )


def test_stream_force_no_thinking_with_explicit_pro_also_flash(monkeypatch):
    """流式短输出点显式传 Pro：同样归一化为 flash 并关闭 thinking。"""
    body = _capture_stream_body(
        monkeypatch,
        prompt="流式 force_no_thinking 覆盖 Pro 断言",
        model_tier="llm_light",
        module="chat",
        explicit_model="deepseek-v4-pro",
        force_no_thinking=True,
    )

    assert body["model"] == "deepseek-v4-flash"
    assert body.get("thinking") == {"type": "disabled"}


def test_stream_doubao_turbo_under_llm_heavy_still_disables_thinking(monkeypatch):
    """流式豆包保持 v9.5.130 既有逻辑：llm_heavy + doubao turbo 仍要关 thinking。

    与同步侧同一条防线：挡住「把豆包分支也换成 _fallback_tier_for(use_model)」
    的偷懒改法 —— doubao-seed-2-1-turbo 不含 "pro"，会被判成轻档而漏关。
    """
    body = _capture_stream_body(
        monkeypatch,
        prompt="流式豆包 turbo 重档 thinking 断言",
        model_tier="llm_heavy",
        module="morning_brief",
        explicit_model="doubao-seed-2-1-turbo-260628",
    )

    assert body["model"] == "doubao-seed-2-1-turbo-260628"
    assert body.get("thinking") == {"type": "disabled"}


def test_stream_doubao_turbo_under_llm_light_keeps_default_thinking(monkeypatch):
    """流式豆包轻档：既有逻辑就是不设 thinking，不得被本次改动带偏。"""
    body = _capture_stream_body(
        monkeypatch,
        prompt="流式豆包 turbo 轻档 thinking 断言",
        model_tier="llm_light",
        module="chat",
        explicit_model="doubao-seed-2-1-turbo-260628",
    )

    assert body["model"] == "doubao-seed-2-1-turbo-260628"
    assert "thinking" not in body


# -----------------------------------------------------------------------------
# v9.9.64：堵死 FC 直连路径的 Pro 泄漏
# -----------------------------------------------------------------------------
# 背景
# ----
# api/chat_fc.py 的 Function Calling 是**直连 httpx** 的（见 _fc_call_with_fallback
# 里的 _do），完全不经过 gateway，所以 gateway 入口的 normalize_explicit_model
# 根本管不到它。前端 sticky localStorage / 旧客户端缓存 / API 直传都可能带来
# 'deepseek-v4-pro'，一旦进来就会以 Pro 身份真实发出去（max_rounds=4，每轮
# max_tokens=3000）—— 这就是用户在 DeepSeek 账单里看到 Pro 扣费的来源。
#
# 纵深防御两层：api/chat.py 的 _normalize_explicit_model（出口统一归一化）
#              + api/chat_fc.py 的 _fc_call_with_fallback（入口自保）。
# 下面两组用例分别守住这两层，且 FC 那组断言的是**真正发出去的 request body**。

def test_api_chat_normalize_explicit_model_rules():
    """api.chat._normalize_explicit_model：auto 哨兵语义不变，Pro 一律归一化。"""
    import api.chat as chat

    # auto / 空值 → ''（空串是「交给 gateway 峰谷调度」的哨兵，语义不能变）
    assert chat._normalize_explicit_model(None) == ""
    assert chat._normalize_explicit_model("") == ""
    assert chat._normalize_explicit_model("auto") == ""

    # 两家 Pro 档 → 各自便宜档
    assert chat._normalize_explicit_model("deepseek-v4-pro") == "deepseek-v4-flash"
    assert chat._normalize_explicit_model("doubao-seed-2-1-pro-260628") == "doubao-seed-2-1-turbo-260628"

    # 已是便宜档 → 原样透传
    assert chat._normalize_explicit_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert chat._normalize_explicit_model("doubao-seed-2-1-turbo-260628") == "doubao-seed-2-1-turbo-260628"


def _capture_fc_request(monkeypatch, tmp_path, model, messages=None):
    """跑一次 _fc_call_with_fallback，拦截 httpx 拿到真正发出去的请求。

    _do 是 _fc_call_with_fallback 的内嵌函数，无法直接 monkeypatch，因此把假
    实现装在 httpx.Client 这一层（FC 唯一的出口）。
    """
    import httpx

    import api.chat_fc as chat_fc

    captured = []

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
            captured.append({"url": url, "headers": headers or {}, "json": json})
            return _FakeResponse({
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("LLM_API_BASE", "https://api.deepseek.com/v1")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("DOUBAO_API_BASE", "https://ark.cn-beijing.volces.com/api/v3")

    payload, actual_model, fallback_used = chat_fc._fc_call_with_fallback(
        model,
        messages if messages is not None else [{"role": "user", "content": "hi"}],
        max_tokens=3000,
    )

    assert len(captured) == 1, "期望发出 1 次 FC 请求，实际 %d 次" % len(captured)
    return captured[0], actual_model, fallback_used


def test_fc_direct_path_normalizes_deepseek_pro_to_flash(monkeypatch, tmp_path):
    """v9.9.64 核心回归：FC 直连路径收到 Pro 也必须发出 flash。

    改动前 _fc_call_with_fallback 原样透传 model，request body 里就是
    'deepseek-v4-pro' —— 一次对话最多 4 次 Pro 调用，每次 max_tokens=3000。
    """
    req, actual_model, fallback_used = _capture_fc_request(
        monkeypatch, tmp_path, "deepseek-v4-pro",
    )

    assert req["json"]["model"] == "deepseek-v4-flash", (
        "FC 直连 httpx 绕过了 gateway，必须自己归一化；"
        "发出 %r 会产生真实 Pro 扣费" % req["json"]["model"]
    )
    assert req["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert actual_model == "deepseek-v4-flash"
    assert fallback_used is False


def test_fc_direct_path_normalizes_doubao_pro_to_turbo(monkeypatch, tmp_path):
    """豆包侧同理：Pro 归一化成 Turbo，且 _route 仍识别为 doubao provider。

    归一化必须发生在 _route(model) **之前** —— _route 靠模型名前缀判断
    provider，顺序反了会把 doubao 请求发到 deepseek 的 base 上。
    """
    req, actual_model, fallback_used = _capture_fc_request(
        monkeypatch, tmp_path, "doubao-seed-2-1-pro-260628",
    )

    assert req["json"]["model"] == "doubao-seed-2-1-turbo-260628"
    assert req["url"] == "https://ark.cn-beijing.volces.com/api/v3/chat/completions", (
        "归一化后 _route 仍须识别为 doubao provider（实际发往 %r）" % req["url"]
    )
    assert actual_model == "doubao-seed-2-1-turbo-260628"
    assert fallback_used is False


def test_fc_direct_path_keeps_deepseek_flash_untouched(monkeypatch, tmp_path):
    """已是便宜档（deepseek flash）时不得被归一化带偏（防「无脑改名」）。"""
    req, actual_model, _ = _capture_fc_request(monkeypatch, tmp_path, "deepseek-v4-flash")
    assert req["json"]["model"] == "deepseek-v4-flash"
    assert actual_model == "deepseek-v4-flash"


def test_fc_direct_path_keeps_doubao_turbo_untouched(monkeypatch, tmp_path):
    """豆包侧负向：已是便宜档（doubao turbo）时必须原样发出。

    与上面 deepseek flash 那条成对存在 —— 只覆盖 flash 会漏掉「有人把归一化
    写成无条件替换成 flash」这种改法，那样豆包的便宜档也会被改坏。
    """
    req, actual_model, _ = _capture_fc_request(monkeypatch, tmp_path, "doubao-seed-2-1-turbo-260628")

    assert req["json"]["model"] == "doubao-seed-2-1-turbo-260628"
    assert req["url"] == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    assert actual_model == "doubao-seed-2-1-turbo-260628"


def test_stream_and_sync_thinking_policy_are_identical(monkeypatch):
    """同步/流式两份 thinking 判据必须逐例一致，防止将来只改一边。

    直接比对 gateway 源码里两段判据的「有效判断骨架」：把变量名
    （body / stream_body、_is_deepseek_v4 / _is_deepseek_v4_stream）归一化后
    应当完全相同。
    """
    import re

    from infra.llm import gateway as gw_mod

    src = Path(gw_mod.__file__).read_text(encoding="utf-8", errors="ignore")

    def _skeleton(text, marker):
        start = text.index(marker)
        segment = text[start:text.index("with httpx.Client", start)]
        lines = []
        for line in segment.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(stripped)
        blob = "\n".join(lines)
        blob = re.sub(r"_is_deepseek_v4_stream", "_is_deepseek_v4", blob)
        blob = re.sub(r"stream_body", "body", blob)
        # 两段截取的起点落在各自的 body 字典内部，多带一个字段名
        # （同步 "temperature": 0.7 / 流式 "stream": True），归一化掉
        blob = re.sub(r'^"[a-z_]+": [^,]+,$', "<BODY_FIELD>", blob, flags=re.M)
        return blob

    sync_block = _skeleton(src, '"temperature": 0.7,')
    stream_block = _skeleton(src, '"stream": True,')

    assert sync_block == stream_block, (
        "同步与流式的 thinking 判据不一致，说明只改了一边：\n"
        "--- sync ---\n%s\n--- stream ---\n%s" % (sync_block, stream_block)
    )
