"""回归测试：LLM 定价识别 + 视觉降级链 + 外部直连计费回填。

覆盖 322b04d 提交引入的 3 类逻辑（此前无测试覆盖）：
1. _pricing_key_from_model 豆包两档判定（pro/turbo）
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
# 1. _pricing_key_from_model 豆包两档判定
# ============================================================

def test_pricing_key_doubao_two_tiers(monkeypatch):
    import infra.llm.gateway as gw_mod

    assert gw_mod._pricing_key_from_model("doubao-seed-2-1-pro-260628") == "doubao-pro"
    assert gw_mod._pricing_key_from_model("doubao-seed-2-1-turbo-260628") == "doubao-turbo"


def test_pricing_key_doubao_ep_prefix_and_unknown(monkeypatch):
    import infra.llm.gateway as gw_mod

    # ep- 前缀（ARK 接入点模型）同样走豆包档位
    assert gw_mod._pricing_key_from_model("ep-20250101-abcde") == "doubao-pro"

    # 无法识别档位的豆包模型 → 保守归入 pro
    assert gw_mod._pricing_key_from_model("doubao-some-future-model") == "doubao-pro"


def test_pricing_key_doubao_tier_priority_turbo_over_pro(monkeypatch):
    """名字同时含 turbo 和 pro 时按 turbo > pro 优先级判定。"""
    import infra.llm.gateway as gw_mod

    # 名字含 pro 和 turbo → turbo 优先
    assert gw_mod._pricing_key_from_model("doubao-turbo-pro-x") == "doubao-turbo"


def test_pricing_key_deepseek_flash_and_pro(monkeypatch):
    import infra.llm.gateway as gw_mod

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
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-1-pro-260628")

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


def test_call_multimodal_env_misconfigured_pro_fallback_normalized(monkeypatch, tmp_path):
    """v9.9.64 回归：.env 误配成豆包 Pro 的兜底模型，也必须被拦成 Turbo。

    改动前 call_multimodal 只归一化了主模型，兜底直接读 env 原样使用（旧注释
    甚至写着「.env 若显式配 Pro 仍尊重 env」）。一旦服务器 .env 配了
    LLM_VISION_MODEL_DOUBAO=doubao-seed-2-1-pro-260628，OCR / 票据识别在
    DeepSeek vision 失败走豆包降级时就会产生**真实豆包 Pro 调用** —— 这条路径
    平时不触发，属于极难发现的隐性漏钱口子。

    这里刻意保留 setenv(..., "doubao-seed-2-1-pro-260628") 作为对抗输入：
    它测的正是「env 被误配」这个场景，不能被改成 turbo 而失去意义。
    """
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-1-pro-260628")

    calls = []

    def dispatch(model):
        calls.append(model)
        if model == "deepseek-v4-flash-vision-exp":
            return 500, {"error": "vision model down"}
        if model == "doubao-seed-2-1-turbo-260628":
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
    assert result["model"] == "doubao-seed-2-1-turbo-260628"
    assert result["fallback_used"] is True
    assert "888.88" in result["content"]
    assert calls == ["deepseek-v4-flash-vision-exp", "doubao-seed-2-1-turbo-260628"]


def test_call_multimodal_no_key_returns_no_key(monkeypatch, tmp_path):
    import infra.llm.gateway as gw_mod

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
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "ds")
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-1-pro-260628")

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


def test_call_multimodal_cheap_primary_never_reaches_pro_fallback(monkeypatch, tmp_path):
    """v9.9.64 回归：主模型已是便宜档时，env 误配的 Pro 兜底绝不能被触达。

    这是 vision 兜底未归一化时**最隐蔽**的一条漏钱路径：主模型传的是便宜的
    doubao turbo，兜底 env 却配了 Pro。改动前两者不同值 → 候选链去重失效 →
    主模型一失败就真的发一次豆包 Pro。改动后兜底被归一化成与主模型同值、
    去重生效，候选链只剩 1 个，Pro 根本进不来。

    断言的是「任何一次请求都不许带 pro 字样」，而不是只看最终 result。
    """
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    # 对抗输入：兜底被误配成 Pro
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-1-pro-260628")

    calls = []

    def dispatch(model):
        calls.append(model)
        # 主模型（便宜档）失败，逼出降级链
        return 500, {"error": "doubao turbo down"}

    monkeypatch.setitem(sys.modules, "httpx", _make_fake_httpx(dispatch))

    gw = gw_mod.LLMGateway()
    result = gw.call_multimodal(
        [{"role": "user", "content": [{"type": "text", "text": "识别"}]}],
        model="doubao-seed-2-1-turbo-260628",
        user_id="LeiJiang",
        module="ocr",
    )

    assert result["source"] == "api_error"
    assert calls == ["doubao-seed-2-1-turbo-260628"], (
        "归一化后兜底与主模型同值，去重生效，候选链应只剩 1 个；"
        "实际 %r —— 说明 env 误配的 Pro 又被放进了候选链" % calls
    )
    assert all("pro" not in m for m in calls)


def test_call_multimodal_doubao_as_primary_dedup(monkeypatch, tmp_path):
    """主模型本身就是豆包时，降级链去重，不重复请求。

    注：本用例的 setenv 刻意保留 Pro —— 它同时覆盖了「显式传 Pro 的主模型被
    归一化」和「env 误配 Pro 的兜底被归一化」两条，正是 v9.9.64 要守的场景。
    """
    import infra.llm.gateway as gw_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DOUBAO_API_KEY", "db")
    monkeypatch.setenv("LLM_VISION_MODEL_DOUBAO", "doubao-seed-2-1-pro-260628")

    calls = []

    def dispatch(model):
        calls.append(model)
        return 200, {
            "choices": [{"message": {"content": "豆包直接识别"}}],
            "usage": {"total_tokens": 5, "prompt_tokens": 4, "completion_tokens": 1},
        }

    monkeypatch.setitem(sys.modules, "httpx", _make_fake_httpx(dispatch))

    gw = gw_mod.LLMGateway()
    # v9.9.63：豆包 Pro 已下架，显式 doubao-*-pro 会被归一化为 Turbo
    result = gw.call_multimodal(
        [{"role": "user", "content": [{"type": "text", "text": "识别"}]}],
        model="doubao-seed-2-1-pro-260628",
        user_id="LeiJiang",
        module="ocr",
    )

    assert result["source"] == "ai"
    assert result["model"] == "doubao-seed-2-1-turbo-260628"
    assert result["fallback_used"] is False
    # 去重后只请求一次
    assert calls == ["doubao-seed-2-1-turbo-260628"]


# ============================================================
# 3. record_external_call 计费回填
# ============================================================

def test_record_external_call_records_usage_and_cost(monkeypatch, tmp_path):
    import infra.llm.gateway as gw_mod

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
    """豆包价目存在（两档已配置），应正常记账而非跳过。验证 doubao 档位命中价表。"""
    import infra.llm.gateway as gw_mod
    from config import PROVIDER_PRICING

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    # 两档价表都应存在
    assert "doubao-pro" in PROVIDER_PRICING
    assert "doubao-turbo" in PROVIDER_PRICING

    gw = gw_mod.LLMGateway()
    # doubao pro 有价表 → record_external_call 不抛异常，usage 记录正常
    gw.record_external_call(
        user_id="LeiJiang",
        module="multi_model_score",
        model="doubao-seed-2-1-pro-260628",
        input_tokens=10,
        output_tokens=5,
    )

    usage = gw._usage.get("LeiJiang", {}).get("multi_model_score")
    assert usage["calls"] == 1
    assert usage["tokens"] == 15


def test_record_external_call_anonymous_user_and_empty_module(monkeypatch, tmp_path):
    import infra.llm.gateway as gw_mod

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
