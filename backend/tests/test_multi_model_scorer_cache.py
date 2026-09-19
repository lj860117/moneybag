"""多模型 AI 评分：缓存策略 + 调用参数 + 解析兜底 回归测试。

背景（FIX 2026-09-12，用户报告「点进去打分一直刷新不出来」）
------------------------------------------------------------
线上 `data/_cache/multi_model_score/` 下 35 个缓存文件**全部**是
`model_count: 0` 的失败结果，而 `score_fund_multi_model()` 在算出
`model_count=0` 之后仍**无条件**写 12h 缓存。后果：用户第一次点失败后，
12 小时内任何重试都直接命中这份失败缓存秒返回，永远刷不出来。

两条根因（服务器生产 Key 直连实测）：
* DeepSeek：`deepseek-v4-flash` 是思考型模型，**思考 token 计入
  `max_tokens`**。原 `max_tokens=250` 时思考常吃掉全部预算，出现
  `finish_reason=length` + `content` 为空/截断 → "解析失败"。
* 豆包：`doubao-seed-2-1-pro-260628` 默认开思考，实测 38.91s /
  3117 reasoning tokens，必然撞 30s 读超时 → ReadTimeout。
  显式关闭思考后 2.12s。

本文件锁住的是**修复后的契约**，不是为了凑覆盖率：
1. 全失败 → 不写缓存（这是「重试无效」的直接原因）
2. 部分成功 → 写缓存但短 TTL，且结果带 partial 标记
3. 全成功 → 12h 缓存
4. 两家请求都带足够 max_tokens；豆包必须关闭思考
5. 非 JSON / 截断输出的解析兜底与失败原因
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import types

import pytest

import httpx

from services import multi_model_scorer as mms
from services.multi_model_scorer import score_fund_multi_model


# ── 测试夹具 ────────────────────────────────────────────────────────────────

def _completion(content: str, finish_reason: str = "stop") -> dict:
    """构造一个 OpenAI 格式的 chat.completion 响应体。"""
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.fixture(autouse=True)
def _stub_llm_gateway(monkeypatch):
    """桩掉 gateway 计费，避免单测真的去读写计费存储。"""
    mod = types.ModuleType("infra.llm.gateway")

    class _Gateway:
        def __init__(self) -> None:
            self.recorded: list[dict] = []

        @staticmethod
        def instance() -> "_Gateway":
            return _Gateway()

        def record_external_call(self, **kwargs) -> None:
            return None

    mod.LLMGateway = _Gateway
    monkeypatch.setitem(sys.modules, "infra.llm.gateway", mod)


def _install_fake_httpx(monkeypatch, responder) -> list[dict]:
    """把 httpx.Client 换成假实现，返回记录到的请求列表。

    `_call_model()` 在函数体内 `import httpx`，属性查找发生在调用时，
    因此直接 monkeypatch `httpx.Client` 即可生效。
    """
    calls: list[dict] = []

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict:
            return self._payload

        @property
        def text(self) -> str:
            return json.dumps(self._payload, ensure_ascii=False)

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.timeout = kwargs.get("timeout")

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *exc_info) -> bool:
            return False

        def post(self, url: str, headers=None, json=None) -> "_FakeResponse":
            calls.append(
                {
                    "url": url,
                    "headers": headers or {},
                    "json": json or {},
                    "timeout": self.timeout,
                }
            )
            status, payload = responder(url, json or {})
            return _FakeResponse(status, payload)

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    return calls


def _both_ok(url: str, body: dict):
    """两家都返回合法 JSON。"""
    if "api.deepseek.com" in url:
        return 200, _completion('{"score": 7.5, "reason": "动量强", "risk": "波动"}')
    return 200, _completion('{"score": 6.5, "reason": "中性", "risk": "回撤大"}')


def _cache_file(code: str):
    return mms._CACHE_DIR / f"{code}.json"


def _cache_entry(code: str) -> dict:
    return json.loads(_cache_file(code).read_text(encoding="utf-8"))


# ── 1. 全失败不写缓存（用户「一直刷不出来」的直接原因）────────────────────

def test_all_models_failed_writes_no_cache(monkeypatch):
    """两家都失败 → 不写缓存，用户点重试能真正重试。"""
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    _install_fake_httpx(monkeypatch, lambda url, body: (500, {}))

    result = score_fund_multi_model({"code": "000001", "name": "测试基金A"})

    assert result["model_count"] == 0
    assert result["avg_score"] is None
    assert result["partial"] is False
    assert result["model_total"] == 2
    assert not _cache_file("000001").exists(), "全失败时不应写缓存"

    # 再调一次也不应命中任何缓存（否则就是老 bug 回归）
    again = score_fund_multi_model({"code": "000001", "name": "测试基金A"})
    assert "from_cache" not in again


def test_all_models_failed_reports_per_model_reason(monkeypatch):
    """全失败时每家都要带可诊断的原因，供前端如实展示。"""
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    _install_fake_httpx(monkeypatch, lambda url, body: (429, {}))

    result = score_fund_multi_model({"code": "000002", "name": "测试基金B"})

    reasons = {s["id"]: s["reason"] for s in result["scores"]}
    assert reasons["deepseek"] == "HTTP 429"
    assert reasons["doubao"] == "HTTP 429"
    assert all(s["error"] for s in result["scores"])


# ── 2. 部分成功：写缓存但短 TTL ─────────────────────────────────────────────

def test_primary_ok_skips_doubao_entirely(monkeypatch):
    """主模型成功 → 一次都不碰豆包，且按「1/1 家成功」写 12h 缓存。

    2026-09-19 语义变更：豆包从「并发必调」降级为「仅兜底」。
    这条是本变更的核心守卫 —— 一旦有人把并发调用加回来，这里会红。
    同时守住 model_total 必须等于**实际调用家数**：若写死成 2，
    前端会永远显示"仅 1/2 家模型返回"，且缓存退化成 30min 短 TTL。
    """
    calls = []

    def responder(url: str, body: dict):
        calls.append(url)
        if "api.deepseek.com" in url:
            return 200, _completion('{"score": 7.5, "reason": "动量强", "risk": "波动"}')
        return 200, _completion('{"score": 6.5, "reason": "不该被调到", "risk": "不该"}')

    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    _install_fake_httpx(monkeypatch, responder)

    result = score_fund_multi_model({"code": "000003", "name": "测试基金C"})

    # 豆包一次都没被调
    assert not any("volces" in u or "ark." in u for u in calls), f"豆包被被动调用了: {calls}"
    assert len([u for u in calls if "api.deepseek.com" in u]) == 1

    assert result["model_count"] == 1
    assert result["model_total"] == 1
    assert result["partial"] is False, "只调了 1 家且成功，不应标记 partial"
    assert result["avg_score"] == 7.5
    assert len(result["scores"]) == 1

    # 成功 → 12h 长缓存（而不是 30min 短 TTL）
    assert _cache_file("000003").exists()
    entry = _cache_entry("000003")
    assert entry["ttl"] == mms._CACHE_TTL == 43200


def test_primary_failed_falls_back_to_doubao(monkeypatch):
    """主模型失败 → 才降级补调豆包；一家成功 → 30min 短 TTL。"""
    calls = []

    def responder(url: str, body: dict):
        calls.append(url)
        if "api.deepseek.com" in url:
            return 500, {}
        return 200, _completion('{"score": 6.5, "reason": "中性", "risk": "回撤大"}')

    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    _install_fake_httpx(monkeypatch, responder)

    # ⚠️ 换 code：000003 已被上一条用例写了 12h 缓存，复用会直接命中缓存、
    # 模型一次都不调，导致本用例假失败（缓存目录是本文件共享的）。
    # 000013 也已被 test_results_follow_declared_model_order 占用。
    result = score_fund_multi_model({"code": "000016", "name": "测试基金C2"})

    # 主模型失败后才轮到豆包
    assert any("api.deepseek.com" in u for u in calls)
    assert any("volces" in u or "ark." in u for u in calls), f"主模型失败却没降级: {calls}"

    assert result["model_count"] == 1
    assert result["model_total"] == 2
    assert result["partial"] is True
    assert result["avg_score"] == 6.5

    assert _cache_file("000016").exists()
    entry = _cache_entry("000016")
    assert entry["ttl"] == mms._CACHE_TTL_PARTIAL == 1800
    assert entry["ttl"] < mms._CACHE_TTL


def test_expired_partial_cache_is_not_returned(monkeypatch):
    """短 TTL 过期后必须真的失效，不能被 12h 默认值兜住。"""
    mms._set_cache("000004", {"model_count": 1}, ttl=1)

    fp = _cache_file("000004")
    entry = json.loads(fp.read_text(encoding="utf-8"))
    entry["t"] = time.time() - 10  # 10 秒前写入，ttl=1 → 已过期
    fp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")

    assert mms._get_cache("000004") is None


def test_poisoned_zero_count_cache_is_ignored(monkeypatch):
    """线上已存在的 model_count=0 毒缓存必须自愈失效，否则重试仍秒回失败。

    这是用户「一直刷新不出来」的最后一环：即使不再写新的失败缓存，
    磁盘上那 35 个历史毒缓存也还会锁 12 小时。
    """
    mms._set_cache("000014", {"model_count": 0, "avg_score": None}, ttl=43200)

    assert mms._get_cache("000014") is None


def test_poisoned_cache_lets_retry_really_retry(monkeypatch):
    """毒缓存存在时再次评分应真的重新调用模型，而不是返回缓存。"""
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    # 先造一份毒缓存（模拟线上 35 个失败结果）
    mms._set_cache("000015", {"model_count": 0, "avg_score": None}, ttl=43200)

    calls = _install_fake_httpx(monkeypatch, _both_ok)
    result = score_fund_multi_model({"code": "000015", "name": "测试基金L"})

    assert "from_cache" not in result
    # 豆包改降级后：主模型成功即收工，所以是 1 次而不是 2 次
    assert len(calls) == 1, "毒缓存未失效，模型根本没被调用"
    assert result["model_count"] == 1


def test_legacy_cache_entry_without_ttl_defaults_to_12h(monkeypatch):
    """老缓存条目没有 ttl 字段时要向后兼容，按 12h 处理。"""
    mms._CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fp = _cache_file("000005")
    fp.write_text(
        json.dumps({"v": {"model_count": 2}, "t": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert mms._get_cache("000005") == {"model_count": 2}


# ── 3. 全成功：正常 12h 缓存 ────────────────────────────────────────────────

def test_primary_success_cached_with_full_ttl(monkeypatch):
    """主模型成功 → 12h 缓存，且第二次命中缓存。

    （原 test_both_success_cached_with_full_ttl：豆包改降级后正常路径只调
    1 家，平均分就是主模型的分 7.5，不再是两家均值 7.0。）
    """
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    _install_fake_httpx(monkeypatch, _both_ok)

    result = score_fund_multi_model({"code": "000006", "name": "测试基金D"})

    assert result["model_count"] == 1
    assert result["model_total"] == 1
    assert result["partial"] is False
    assert result["avg_score"] == 7.5  # 只有主模型的分，不再取两家均值
    assert _cache_entry("000006")["ttl"] == mms._CACHE_TTL == 43200

    # 第二次调用应命中缓存
    cached = score_fund_multi_model({"code": "000006", "name": "测试基金D"})
    assert cached["from_cache"] is True


# ── 4. 调用参数：max_tokens 与豆包关闭思考 ──────────────────────────────────

def test_requests_use_generous_max_tokens(monkeypatch):
    """max_tokens 必须给足思考预算（只是上限，不是成本）。"""
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    calls = _install_fake_httpx(monkeypatch, _both_ok)

    score_fund_multi_model({"code": "000007", "name": "测试基金E"})

    # 豆包改降级后正常路径只调主模型 1 次
    assert len(calls) == 1
    for c in calls:
        assert c["json"]["max_tokens"] == 2000


def test_doubao_request_disables_thinking(monkeypatch):
    """豆包必须显式关闭思考，否则实测 38.9s 必然超时。

    豆包改成降级后，正常路径已经调不到它了；要验它的请求参数，
    必须先让主模型失败把它"逼"出来。
    """
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")

    def responder(url: str, body: dict):
        if "api.deepseek.com" in url:
            return 500, {}                      # 逼出降级
        return 200, _completion('{"score": 6.5, "reason": "中性", "risk": "回撤大"}')

    calls = _install_fake_httpx(monkeypatch, responder)

    score_fund_multi_model({"code": "000008", "name": "测试基金F"})

    doubao = [c for c in calls if "ark.cn-beijing" in c["url"]]
    deepseek = [c for c in calls if "api.deepseek.com" in c["url"]]
    assert len(doubao) == 1 and len(deepseek) == 1

    assert doubao[0]["json"]["thinking"] == {"type": "disabled"}
    # DeepSeek 侧不加思考开关（其关闭方式不同，靠放大 max_tokens 解决）
    assert "thinking" not in deepseek[0]["json"]


def test_missing_api_key_reports_not_configured(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    _install_fake_httpx(monkeypatch, _both_ok)

    result = score_fund_multi_model({"code": "000009", "name": "测试基金G"})

    assert result["model_count"] == 0
    assert all(s["reason"] == "API key未配置" for s in result["scores"])
    assert not _cache_file("000009").exists()


# ── 5. 解析兜底与失败原因 ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "content, finish_reason, expected_score, expected_reason",
    [
        # 输出被截断在 score 处 → 走正则兜底抢救出分数
        ('{"score": 6.5', "length", 6.5, None),
        # 纯文本，没有任何 JSON → 解析失败
        ("我是一段纯文本", "stop", None, "解析失败"),
        # 思考吃光预算、content 为空 → 必须说清是截断，而不是笼统的"解析失败"
        ("", "length", None, "输出被上限截断，未完成思考"),
        # content 为空但不是截断 → 空内容
        ("", "stop", None, "模型返回内容为空"),
        # markdown 代码块包裹 → 正常解析
        ('```json\n{"score": 8.0, "reason": "优秀", "risk": "无"}\n```',
         "stop", 8.0, None),
    ],
)
def test_content_parsing_variants(monkeypatch, content, finish_reason,
                                  expected_score, expected_reason):
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    _install_fake_httpx(
        monkeypatch,
        lambda url, body: (200, _completion(content, finish_reason)),
    )

    # 每个用例用独立 code：成功分支会写 12h 缓存，共用同一个 code 会让后跑的
    # 用例命中前一个用例的缓存，拿到假结果（实测已踩过一次）。
    code = "900" + hashlib.md5(
        f"{content}|{finish_reason}".encode("utf-8")
    ).hexdigest()[:6]
    result = score_fund_multi_model({"code": code, "name": "测试基金H"})

    # 豆包改降级后：解析成功 → 只调主模型 1 家（model_count=1）；
    # 解析失败 → 主模型拿不到分，降级补调豆包，两家都失败（model_count=0）。
    assert result["model_count"] == (1 if expected_score is not None else 0)
    for s in result["scores"]:
        if expected_score is not None:
            assert s["score"] == expected_score
        else:
            assert s["score"] is None
            assert s["reason"] == expected_reason


def test_score_is_clamped_to_0_10(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    _install_fake_httpx(
        monkeypatch,
        lambda url, body: (200, _completion('{"score": 99, "reason": "越界", "risk": ""}')),
    )

    result = score_fund_multi_model({"code": "000011", "name": "测试基金I"})

    assert all(s["score"] == 10.0 for s in result["scores"])


# ── 6. 总预算超时：不能冒泡成 500，也不能写缓存 ────────────────────────────

def test_total_timeout_marks_models_and_skips_cache(monkeypatch):
    """并发总预算耗尽时补记超时结果，不让异常冒泡到接口层。"""
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")
    monkeypatch.setattr(mms, "_TOTAL_TIMEOUT", 0.1)

    def slow(url: str, body: dict):
        time.sleep(1.0)
        return 200, _completion('{"score": 7.0, "reason": "慢", "risk": ""}')

    _install_fake_httpx(monkeypatch, slow)

    result = score_fund_multi_model({"code": "000012", "name": "测试基金J"})

    assert result["model_count"] == 0
    assert len(result["scores"]) == 2
    assert all("超时" in s["reason"] for s in result["scores"])
    assert not _cache_file("000012").exists()


def test_timeout_budget_is_strictly_below_frontend(monkeypatch):
    """常量之间保持顺序，且都小于前端 45s。

    注意：这条**只锁常量的大小关系，不保证墙钟** ——
    _TOTAL_TIMEOUT 是 as_completed 的等待预算，真实上界由 httpx
    _MODEL_TIMEOUT 决定（详见 multi_model_scorer.py 文件头，QA C9 实测：
    _TOTAL_TIMEOUT=1 + 线程挂 8s → 实际 8.01s 返回）。
    """
    assert mms._MODEL_TIMEOUT < mms._TOTAL_TIMEOUT < 45


def test_results_follow_declared_model_order(monkeypatch):
    """结果顺序要稳定（as_completed 是完成序，不能泄漏给前端）。

    豆包改降级后它只在主模型失败时才出场，所以要凑齐两家进 results，
    必须让主模型"调得到但拿不到分"（返回非 JSON），而不是 HTTP 报错。
    """
    monkeypatch.setenv("LLM_API_KEY", "test-ds-key")
    monkeypatch.setenv("DOUBAO_API_KEY", "test-db-key")

    def responder(url: str, body: dict):
        # 让豆包先返回，验证结果仍按 _MODELS 顺序
        if "ark.cn-beijing" in url:
            return 200, _completion('{"score": 6.5, "reason": "中性", "risk": "回撤大"}')
        time.sleep(0.05)
        # 主模型返回纯文本 → 解析失败 → 触发豆包降级
        return 200, _completion("我是一段纯文本，不是JSON")

    _install_fake_httpx(monkeypatch, responder)

    result = score_fund_multi_model({"code": "000013", "name": "测试基金K"})

    assert [s["id"] for s in result["scores"]] == ["deepseek", "doubao"]
    assert result["scores"][0]["score"] is None   # 主模型解析失败
    assert result["scores"][1]["score"] == 6.5    # 豆包兜底成功
