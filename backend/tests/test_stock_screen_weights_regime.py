"""
P1-7 回归测试：选股 7 维权重必须由 regime 固化表查得，不再由 LLM 现编

背景（P1-7 缺陷）：
    `_get_dynamic_weights()` 原实现让 LLM 每次输出 7 个精确的权重数值，
    喂给它的只有 3 个市场指标。后果：
      1. 不可复现 —— 同一天跑两次可能得到两套权重，同一批股票排名就变了；
      2. 无回测证明 —— 没有任何证据表明 LLM 现编的权重优于等权；
      3. 让 LLM 编数字 —— 3 个输入推 7 个精确输出，本质是幻觉温床；
    而这套权重被直接用于全市场 5000+ 只股票的打分排序。

修法：LLM 只做离散分类（regime），权重由 config.STOCK_FACTOR_WEIGHTS_BY_REGIME
查表得到。本测试锁死这条边界。
"""
import inspect

import pytest

from config import (
    STOCK_FACTOR_WEIGHTS_BY_REGIME,
    STOCK_SCREEN_WEIGHTS,
)
import config
from services import stock_screen as ss


DIM_KEYS = ("value", "growth", "quality", "momentum", "risk", "liquidity", "sentiment")


@pytest.fixture(autouse=True)
def _isolated_weight_env(monkeypatch):
    """每个用例：清空权重缓存 + 关掉 LLM key，保证用例互不污染且不走网络。

    需要模拟"LLM 可用"的用例会在自己体内用 monkeypatch 重新设上 key。
    """
    ss._weight_cache.clear()
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    yield
    ss._weight_cache.clear()


# ── 1. 可复现性（本缺陷的核心）────────────────────────────────────────

def test_same_regime_twice_returns_identical_weights():
    """同一 regime 连续调用两次，权重字典必须完全相等"""
    first = ss.get_weights_for_regime("牛市")
    second = ss.get_weights_for_regime("牛市")
    assert first == second


def test_same_regime_is_reproducible_across_cache_cycles():
    """清掉缓存后重算，结果仍必须逐位相等（旧实现每次问 LLM，做不到这点）"""
    first = ss._get_dynamic_weights()
    ss._weight_cache.clear()
    second = ss._get_dynamic_weights()
    assert first == second


def test_two_legit_regimes_give_stable_mapping():
    """不同 regime 各自稳定：牛市权重不等于熊市，且各自两次调用一致"""
    bull_a, bull_b = ss.get_weights_for_regime("牛市"), ss.get_weights_for_regime("牛市")
    bear_a, bear_b = ss.get_weights_for_regime("熊市"), ss.get_weights_for_regime("熊市")
    assert bull_a == bull_b and bear_a == bear_b
    assert bull_a != bear_a, "牛市与熊市的权重必须有区别，否则分状态调权形同虚设"


# ── 2. 归一化 ────────────────────────────────────────────────────────

def test_every_regime_sums_to_one():
    """每个 regime 的 7 维权重之和必须 == 1.0（容差 1e-6）"""
    assert STOCK_FACTOR_WEIGHTS_BY_REGIME, "固化权重表不能为空"
    for regime, weights in STOCK_FACTOR_WEIGHTS_BY_REGIME.items():
        assert set(weights.keys()) == set(DIM_KEYS), f"{regime} 维度集合不符"
        assert abs(sum(weights[k] for k in DIM_KEYS) - 1.0) < 1e-6, \
            f"{regime} 权重之和={sum(weights[k] for k in DIM_KEYS)}"


def test_lookup_result_is_normalized_even_if_table_is_hand_edited(monkeypatch):
    """负面控制：表被手改成不归一化的值，查表结果仍要归一化到 1.0"""
    monkeypatch.setitem(
        STOCK_FACTOR_WEIGHTS_BY_REGIME, "牛市",
        {k: 2.0 for k in DIM_KEYS},  # 和为 14，明显越界
    )
    w = ss.get_weights_for_regime("牛市")
    assert abs(sum(w[k] for k in DIM_KEYS) - 1.0) < 1e-6


def test_default_baseline_is_normalized():
    """STOCK_SCREEN_WEIGHTS 基线本身也必须是归一化的（它是最终兜底）"""
    assert abs(sum(STOCK_SCREEN_WEIGHTS[k] for k in DIM_KEYS) - 1.0) < 1e-6


# ── 3. 非法 regime 回退 ──────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["火星牛市", None, "", "  ", "BULL", 123, ["牛市"]])
def test_invalid_regime_falls_back_to_default(bad):
    """非法 regime 必须回退到默认权重，不抛异常、不返回缺维度"""
    w = ss.get_weights_for_regime(bad)
    assert set(w.keys()) == set(DIM_KEYS)
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert w == ss._normalize_weights(STOCK_SCREEN_WEIGHTS)


def test_invalid_regime_is_reproducible():
    """回退路径本身也要可复现（不能这次回退、下次抛错）"""
    assert ss.get_weights_for_regime("火星牛市") == ss.get_weights_for_regime("火星牛市")


def test_whitespace_regime_is_accepted_after_strip():
    assert ss.get_weights_for_regime(" 牛市 ") == ss.get_weights_for_regime("牛市")


# ── 4. LLM 不可用 / 返回非法 → 回退且带降级标记 ──────────────────────

class _BoomGateway:
    """LLM 直接抛异常的替身网关"""

    @classmethod
    def instance(cls):
        return cls()

    def call_sync(self, *args, **kwargs):
        raise RuntimeError("LLM 不可用")


class _FakeGateway:
    """可控返回内容的替身网关"""

    CONTENT = '{"regime":"牛市","reason":"测试"}'

    @classmethod
    def instance(cls):
        return cls()

    def call_sync(self, *args, **kwargs):
        return {"content": self.CONTENT, "model": "fake", "fallback": False}


def _patch_llm(monkeypatch, gateway_cls, api_key="test-key"):
    from infra.llm import gateway as gateway_mod
    monkeypatch.setattr(gateway_mod, "LLMGateway", gateway_cls)
    monkeypatch.setattr(config, "LLM_API_KEY", api_key)
    monkeypatch.setattr(ss, "_build_market_ctx", lambda: "估值百分位: 60% | 恐贪指数: 55")


def test_llm_exception_falls_back_and_stays_normalized(monkeypatch):
    """LLM 抛异常 → 必须回退，结果仍归一化、仍可复现"""
    _patch_llm(monkeypatch, _BoomGateway)

    w1 = ss._get_dynamic_weights()
    assert w1["_source"] == "fallback"
    assert w1["_regime"] == ""
    dims1 = {k: v for k, v in w1.items() if not k.startswith("_")}
    assert abs(sum(dims1.values()) - 1.0) < 1e-6

    ss._weight_cache.clear()
    w2 = ss._get_dynamic_weights()
    assert {k: v for k, v in w2.items() if not k.startswith("_")} == dims1


def test_no_api_key_falls_back_without_calling_llm(monkeypatch):
    """没配 LLM_API_KEY 时不该调 LLM，直接回退并标记 source"""
    from infra.llm import gateway as gateway_mod

    calls = []

    class _Recorder(_FakeGateway):
        def call_sync(self, *args, **kwargs):
            calls.append(1)
            return {"content": self.CONTENT, "model": "fake", "fallback": False}

    monkeypatch.setattr(gateway_mod, "LLMGateway", _Recorder)
    monkeypatch.setattr(ss, "_build_market_ctx", lambda: "估值百分位: 60%")

    w = ss._get_dynamic_weights()
    assert calls == [], "未配置 key 就不该发起 LLM 调用"
    assert w["_source"] == "fallback"
    assert abs(sum(v for k, v in w.items() if not k.startswith("_")) - 1.0) < 1e-6


def test_llm_regime_uses_table_weights(monkeypatch):
    """LLM 成功判出 regime → 权重取自固化表，并标记 source=llm_regime"""
    _patch_llm(monkeypatch, _FakeGateway)

    w = ss._get_dynamic_weights()
    assert w["_regime"] == "牛市"
    assert w["_source"] == "llm_regime"
    dims = {k: v for k, v in w.items() if not k.startswith("_")}
    assert dims == ss.get_weights_for_regime("牛市")


def test_llm_weight_numbers_are_ignored(monkeypatch):
    """核心：即使 LLM 仍按老格式塞回权重数字，也一律不许进入结果"""
    class _OldStyleGateway(_FakeGateway):
        CONTENT = ('{"value":0.99,"growth":0.001,"quality":0.001,"momentum":0.001,'
                   '"risk":0.001,"liquidity":0.001,"sentiment":0.005,'
                   '"regime":"熊市","reason":"旧格式"}')

    _patch_llm(monkeypatch, _OldStyleGateway)

    w = ss._get_dynamic_weights()
    dims = {k: v for k, v in w.items() if not k.startswith("_")}
    assert dims == ss.get_weights_for_regime("熊市"), "LLM 编的数字泄漏进了权重"
    assert abs(dims["value"] - 0.99) > 1e-6


def test_llm_illegal_regime_falls_back(monkeypatch):
    """LLM 返回枚举外的 regime（含自造词/英文）→ 视为识别失败，回退"""
    class _WeirdGateway(_FakeGateway):
        CONTENT = '{"regime":"结构性慢牛","reason":"自造状态"}'

    _patch_llm(monkeypatch, _WeirdGateway)

    w = ss._get_dynamic_weights()
    assert w["_regime"] == ""
    assert w["_source"] == "fallback"
    assert {k: v for k, v in w.items() if not k.startswith("_")} == \
        ss._normalize_weights(STOCK_SCREEN_WEIGHTS)


def test_llm_broken_json_falls_back(monkeypatch):
    class _JunkGateway(_FakeGateway):
        CONTENT = "我觉得现在大概是牛市吧，权重你自己定"

    _patch_llm(monkeypatch, _JunkGateway)
    w = ss._get_dynamic_weights()
    assert w["_source"] == "fallback"
    assert abs(sum(v for k, v in w.items() if not k.startswith("_")) - 1.0) < 1e-6


def test_llm_gateway_fallback_flag_is_treated_as_unavailable(monkeypatch):
    class _FallbackGateway(_FakeGateway):
        def call_sync(self, *args, **kwargs):
            return {"content": "", "model": "", "fallback": True}

    _patch_llm(monkeypatch, _FallbackGateway)
    assert ss._get_dynamic_weights()["_source"] == "fallback"


# ── 5. 负面控制：权重生成不得掺入随机/时间 ───────────────────────────

def test_weight_generation_has_no_random_or_time():
    """可复现性的反向保证：权重生成路径里不得出现 random / time / uuid"""
    sources = inspect.getsource(ss._normalize_weights) + \
        inspect.getsource(ss.get_weights_for_regime)
    for banned in ("random", "time.", "uuid", "datetime", "os.environ"):
        assert banned not in sources, f"权重生成掺入了不可复现因素: {banned}"


def test_table_weights_are_plain_numbers():
    """固化表必须是纯字面量，不能是函数/表达式（否则谈不上"固化"）"""
    for regime, weights in STOCK_FACTOR_WEIGHTS_BY_REGIME.items():
        for k, v in weights.items():
            assert isinstance(v, (int, float)) and not isinstance(v, bool), \
                f"{regime}.{k}={v!r} 不是数字字面量"
            assert 0 < v < 1, f"{regime}.{k}={v} 越界"


def test_classify_prompt_does_not_ask_for_weight_numbers():
    """旧 prompt 让 LLM "动态调整 7 个维度权重"，新 prompt 必须明令禁止输出数字"""
    src = inspect.getsource(ss._classify_regime_by_llm)
    assert "不要输出任何权重" in src, "分类 prompt 未禁止 LLM 输出权重数字"
    for old_number in ("0.20", "0.15", "0.18", "20%"):
        assert old_number not in src, f"分类 prompt 里仍出现旧权重数字 {old_number}"


def test_dynamic_weights_returns_copy_not_cached_object():
    """调用方会 pop 元信息，返回值必须是拷贝，否则会污染缓存里的那份"""
    w1 = ss._get_dynamic_weights()
    w1.pop("_regime", None)
    w2 = ss._get_dynamic_weights()
    assert "_regime" in w2, "缓存对象被上一调用方改坏了"
