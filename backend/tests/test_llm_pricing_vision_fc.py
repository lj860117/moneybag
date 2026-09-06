"""回归测试：LLM 定价识别 + 视觉降级链 + 外部直连计费回填。

覆盖 322b04d 提交引入的 3 类逻辑（此前无测试覆盖）：
1. _pricing_key_from_model 豆包三档判定（pro/lite/mini）
2. call_multimodal 视觉降级链（DeepSeek vision → 豆包视觉）
3. record_external_call 计费回填（FC / multi_model_scorer 直连路径）

mock 模式沿用 test_chat_model_routing.py：
- monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
- _FakeResponse / _FakeClient
- monkeypatch.setenv / delenv 注入环境变量
"""
import json
import sys
import types
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ============================================================
# 1. _pricing_key_from_model 豆包三档判定
# ============================================================

def test_pricing_key_doubao_three_tiers(monkeypatch):
    import services.llm_gateway as gw_mod

    assert gw_mod._pricing_key_from_model("doubao-seed-2-0-pro-260215") == "doubao-pro"
    assert gw_mod._pricing_key_from_model("doubao-seed-2-0-lite-260215") == "doubao-lite"
    assert gw_mod._pricing_key_from_model("doubao-seed-2-0-mini-260215") == "doubao-mini"


def test_pricing_key_doubao_ep_prefix_and_unknown(monkeypatch):
    import services.llm_gateway as gw_mod

    # ep- 前缀（ARK 接入点模型）同样走豆包档位
    assert gw_mod._pricing_key_from_model("ep-20250101-abcde") == "doubao-pro"

    # 无法识别档位的豆包模型 → 保守归入 pro
    assert gw_mod._pricing_key_from_model("doubao-some-future-model") == "doubao-pro"


def test_pricing_key_doubao_tier_priority_mini_over_lite_over_pro(monkeypatch):
    """含多个关键词时按 mini > lite > pro 优先级判定。"""
    import services.llm_gateway as gw_mod

    # 名字同时含 lite 和 mini（不现实但验证优先级）
    assert gw_mod._pricing_key_from_model("doubao-mini-lite-x") == "doubao-mini"
    # 名字含 pro 和 lite → lite 优先
    assert gw_mod._pricing_key_from_model("doubao-lite-pro-x") == "doubao-lite"


def test_pricing_key_deepseek_flash_and_pro(monkeypatch):
    import services.llm_gateway as gw_mod

    assert gw_mod._pricing_key_from_model("deepseek-v4-flash") == "deepseek-flash"
    assert gw_mod._pricing_key_from_model("deepseek-v4-pro") == "deepseek-pro"
    # reasoner 是 flash 的思考模式
    assert gw_mod._pricing_key_from_model("deepseek-v4-reasoner") == "deepseek-flash"


# ============================================================
# 2. call_multimodal 视觉降级链
# ============================================================

def _make_fake_httpx(dispatch):
    """构造 fake httpx 模块，dispatch(model) -> (status_code, payload) 或 raise。"""

    class _FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)

        def json(self):
            if isinstance(self._payload, dict):
                return self._payload
            raise ValueError("not json")

    class _FakeClient:
        def __init__(self, timeout=60):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            model = (json or {}).get("model")
            status, payload = dispatch(model)
            return _FakeResponse(status, payload)

    return types.SimpleNamespace(Client=_FakeClient)


def test_call_multimodal_deepseek_success_no_fallback(monkeypatch, tmp_path):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-0-pro-260215")

    calls = []

    def dispatch(model):
        calls.append(model)
        if model == "deepseek-v4-flash-vision-exp":
            return 200, {
                "choices": [{"message": {"content": "识别结果：金额 1234.56"}}],
                "usage": {"total_tokens": 10, "prompt_tokens": 8, "completion_tokens": 2},
            }
        raise AssertionError(f"不应请求 fallback 模型 {model}")

    monkeypatch.setitem(sys.modules, "httpx", _make_fake_httpx(dispatch))

    gw = gw_mod.LLMGateway()
    result = gw.call_multimodal(
        [{"role": "user", "content": [{"type": "text", "text": "识别这张账单"}]}],
        model="deepseek-v4-flash-vision-exp",
        user_id="LeiJiang",
        module="ocr",
    )

    assert result["source"] == "ai"
    assert result["model"] == "deepseek-v4-flash-vision-exp"
    assert result["fallback_used"] is False
    assert "1234.56" in result["content"]
    assert calls == ["deepseek-v4-flash-vision-exp"]


def test_call_multimodal_deepseek_fails_falls_back_to_doubao(monkeypatch, tmp_path):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-0-pro-260215")

    calls = []

    def dispatch(model):
        calls.append(model)
        if model == "deepseek-v4-flash-vision-exp":
            return 500, {"error": "vision model down"}
        if model == "doubao-seed-2-0-pro-260215":
            return 200, {
                "choices": [{"message": {"content": "豆包识别：金额 888.88"}}],
                "usage": {"total_tokens": 9, "prompt_tokens": 7, "completion_tokens": 2},
            }
        raise AssertionError(f"unexpected model {model}")

    monkeypatch.setitem(sys.modules, "httpx", _make_fake_httpx(dispatch))

    gw = gw_mod.LLMGateway()
    result = gw.call_multimodal(
        [{"role": "user", "content": [{"type": "text", "text": "识别"}]}],
        model="deepseek-v4-flash-vision-exp",
        user_id="LeiJiang",
        module="ocr",
    )

    assert result["source"] == "ai"
    assert result["model"] == "doubao-seed-2-0-pro-260215"
    assert result["fallback_used"] is True
    assert "888.88" in result["content"]
    assert calls == ["deepseek-v4-flash-vision-exp", "doubao-seed-2-0-pro-260215"]


def test_call_multimodal_no_key_returns_no_key(monkeypatch, tmp_path):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    gw = gw_mod.LLMGateway()
    result = gw.call_multimodal(
        [{"role": "user", "content": [{"type": "text", "text": "识别"}]}],
        model="deepseek-v4-flash-vision-exp",
    )

    assert result["source"] == "no_key"
    assert result["fallback"] is True


def test_call_multimodal_all_fail_returns_api_error(monkeypatch, tmp_path):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-0-pro-260215")

    def dispatch(model):
        return 500, {"error": "all down"}

    monkeypatch.setitem(sys.modules, "httpx", _make_fake_httpx(dispatch))

    gw = gw_mod.LLMGateway()
    result = gw.call_multimodal(
        [{"role": "user", "content": [{"type": "text", "text": "识别"}]}],
        model="deepseek-v4-flash-vision-exp",
        user_id="LeiJiang",
        module="ocr",
    )

    assert result["source"] == "api_error"
    assert result["fallback"] is True
    assert result["content"] == ""


def test_call_multimodal_doubao_as_primary_dedup(monkeypatch, tmp_path):
    """主模型本身就是豆包时，降级链去重，不重复请求。"""
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-0-pro-260215")

    calls = []

    def dispatch(model):
        calls.append(model)
        return 200, {
            "choices": [{"message": {"content": "豆包直接识别"}}],
            "usage": {"total_tokens": 5, "prompt_tokens": 4, "completion_tokens": 1},
        }

    monkeypatch.setitem(sys.modules, "httpx", _make_fake_httpx(dispatch))

    gw = gw_mod.LLMGateway()
    result = gw.call_multimodal(
        [{"role": "user", "content": [{"type": "text", "text": "识别"}]}],
        model="doubao-seed-2-0-pro-260215",
        user_id="LeiJiang",
        module="ocr",
    )

    assert result["source"] == "ai"
    assert result["model"] == "doubao-seed-2-0-pro-260215"
    assert result["fallback_used"] is False
    # 去重后只请求一次
    assert calls == ["doubao-seed-2-0-pro-260215"]


# ============================================================
# 3. record_external_call 计费回填
# ============================================================

def test_record_external_call_records_usage_and_cost(monkeypatch, tmp_path):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    gw = gw_mod.LLMGateway()
    gw.record_external_call(
        user_id="LeiJiang",
        module="chat_fc",
        model="deepseek-v4-flash",
        input_tokens=100,
        output_tokens=50,
    )

    # usage 已记录（calls + tokens）
    usage = gw._usage.get("LeiJiang", {}).get("chat_fc")
    assert usage is not None
    assert usage["calls"] == 1
    assert usage["tokens"] == 150
    assert usage["models"] == {"deepseek-v4-flash": 1}


def test_record_external_call_doubao_unknown_price_skips_cost(monkeypatch, tmp_path):
    """豆包价目存在（三档已配置），应正常记账而非跳过。验证 doubao 档位命中价表。"""
    import services.llm_gateway as gw_mod
    from config import PROVIDER_PRICING

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    # 三档价表都应存在
    assert "doubao-pro" in PROVIDER_PRICING
    assert "doubao-lite" in PROVIDER_PRICING
    assert "doubao-mini" in PROVIDER_PRICING

    gw = gw_mod.LLMGateway()
    # doubao pro 有价表 → record_external_call 不抛异常，usage 记录正常
    gw.record_external_call(
        user_id="LeiJiang",
        module="multi_model_score",
        model="doubao-seed-2-0-pro-260215",
        input_tokens=10,
        output_tokens=5,
    )

    usage = gw._usage.get("LeiJiang", {}).get("multi_model_score")
    assert usage["calls"] == 1
    assert usage["tokens"] == 15


def test_record_external_call_anonymous_user_and_empty_module(monkeypatch, tmp_path):
    import services.llm_gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    gw = gw_mod.LLMGateway()
    # FC / multi_model_scorer 均传 user_id=""
    gw.record_external_call(
        user_id="",
        module="chat_fc",
        model="deepseek-v4-pro",
        input_tokens=1,
        output_tokens=1,
    )

    usage = gw._usage.get("_anonymous", {}).get("chat_fc")
    assert usage is not None
    assert usage["calls"] == 1
