"""
共享性价比缓存 + 后台预热队列 单元测试（T01/T03）

覆盖：
  - set→get 往返（正缓存 + 负缓存）
  - 文件回退（清内存后仍命中，并回填内存）
  - TTL 过期返回 None
  - 原子写不残留 .tmp、文件为合法 JSON
  - 入队去重 + 跳过已缓存（正/负缓存）
  - 入队携带 name（dict 形式）供 worker 类型识别兜底
  - worker 把 name 传给 compute_risk_adjusted_metrics（核心 bug 回归）
  - invalidate 删除内存+文件缓存（自愈坏负缓存的原语）
  - 并发守卫（_WARMUP_RUNNING 阻止重复起 worker）
  - worker 跳过已缓存、不可计算基金落负缓存
  - 单只计算异常被吞掉不拖垮整批、超时不抛
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import fund_risk_adjusted as fra


@pytest.fixture(autouse=True)
def _reset_ra_cache():
    """每个测试前后重置模块级共享状态，避免串扰。"""
    fra._RA_CACHE.clear()
    fra._PENDING_WARMUP.clear()
    fra._WARMUP_RUNNING = False
    yield
    fra._RA_CACHE.clear()
    fra._PENDING_WARMUP.clear()
    fra._WARMUP_RUNNING = False


# ──────────────────────────────────────────────────────────
# 缓存读写 / TTL / 文件回退 / 原子写
# ──────────────────────────────────────────────────────────

def test_set_get_roundtrip_positive_and_negative():
    pos = {"code": "A", "available": True, "sharpe_ratio": 1.8, "sortino_ratio": 2.0}
    fra.set_risk_adjusted_cache("A", pos)
    assert fra.get_risk_adjusted_cache("A") == pos

    # 负缓存（available=False）同样往返，供列表注入区分「算过但不可用」
    neg = {"code": "B", "available": False, "reason": "仅股票型/混合型基金支持性价比计算"}
    fra.set_risk_adjusted_cache("B", neg)
    got = fra.get_risk_adjusted_cache("B")
    assert got is not None
    assert got["available"] is False
    assert got["code"] == "B"


def test_file_fallback_after_memory_clear():
    metrics = {"code": "C", "available": True, "sharpe_ratio": 1.2, "sortino_ratio": 1.4}
    fra.set_risk_adjusted_cache("C", metrics)

    fra._RA_CACHE.clear()  # 模拟进程重启 / 内存丢失
    assert fra.get_risk_adjusted_cache("C") == metrics
    assert "C" in fra._RA_CACHE  # 文件命中后回填内存


def test_negative_cache_file_fallback_after_memory_clear():
    """负缓存（available=False）也必须真的落盘：清内存后能从文件读回。"""
    neg = {"code": "NEG1", "available": False, "reason": "仅股票型/混合型基金支持性价比计算"}
    fra.set_risk_adjusted_cache("NEG1", neg)

    # 文件确实存在且内容合法（证明负缓存落盘了，而非只在内存）
    path = fra._risk_adjusted_cache_path("NEG1")
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["v"]["available"] is False
    assert data["v"]["code"] == "NEG1"

    fra._RA_CACHE.clear()  # 模拟进程重启 / 内存丢失
    got = fra.get_risk_adjusted_cache("NEG1")
    assert got is not None
    assert got["available"] is False
    assert got["code"] == "NEG1"
    assert "NEG1" in fra._RA_CACHE  # 文件命中后回填内存


def test_ttl_expiry_returns_none(monkeypatch):
    fra.set_risk_adjusted_cache("D", {"code": "D", "available": True, "sharpe_ratio": 1.0})
    monkeypatch.setattr(fra, "RISK_ADJUSTED_CACHE_TTL", 0)
    assert fra.get_risk_adjusted_cache("D") is None


def test_atomic_write_no_tmp_leftover_and_valid_json():
    fra.set_risk_adjusted_cache("E", {"code": "E", "available": True, "sharpe_ratio": 1.1})

    path = fra._risk_adjusted_cache_path("E")
    assert path.exists()
    # 不残留 .tmp 半截文件
    assert list(fra._RA_CACHE_DIR.glob("E.json.tmp")) == []
    # 文件是合法 JSON，且信封结构为 {v, t}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["v"]["code"] == "E"
    assert data["v"]["sharpe_ratio"] == 1.1
    assert isinstance(data["t"], (int, float))


def test_concurrent_get_set_no_race_corruption():
    """多线程并发 get/set 同一 code：_RA_CACHE_LOCK 保护下无异常、无 torn read。

    确定性断言：所有读到的 metrics 契约字段完整、sharpe 落在 writer 写入区间内，
    最终内存值与文件值完全一致（同一 code 的「内存+文件」在锁内原子更新）。
    """
    code = "CONC1"
    fra.set_risk_adjusted_cache(code, {"code": code, "available": True, "sharpe_ratio": 1.0})

    errors: list = []

    def writer():
        for i in range(50):
            try:
                fra.set_risk_adjusted_cache(
                    code,
                    {"code": code, "available": True, "sharpe_ratio": 1.0 + i / 1000.0},
                )
            except Exception as e:  # pragma: no cover - 锁/IO 异常兜底
                errors.append(e)

    def reader():
        for _ in range(50):
            try:
                got = fra.get_risk_adjusted_cache(code)
                if got is None:
                    continue
                # 无 torn read：要么是完整合法 dict，要么读到旧完整值
                assert got.get("code") == code
                assert isinstance(got.get("available"), bool)
                assert isinstance(got.get("sharpe_ratio"), (int, float))
                assert 1.0 <= got["sharpe_ratio"] < 1.05  # 只可能是 writer 写过的值
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(3)] + [
        threading.Thread(target=reader) for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # 最终内存与文件一致（last-write-wins 且锁内同步写内存+文件）
    got = fra.get_risk_adjusted_cache(code)
    assert got is not None
    assert got["code"] == code
    assert got["available"] is True
    assert 1.0 <= got["sharpe_ratio"] < 1.05
    data = json.loads(fra._risk_adjusted_cache_path(code).read_text(encoding="utf-8"))
    assert data["v"] == got


def test_atomic_write_concurrent_read_never_sees_partial_file():
    """一个线程反复 set（.tmp→os.replace），另一线程直接读文件：永远合法 JSON。

    直接读文件（不经过 get 的锁）验证 os.replace 原子性——读者在任何时刻
    只会看到完整旧文件或完整新文件，绝不出现空/半截文件导致的 JSONDecodeError。
    """
    code = "ATOM1"
    fra.set_risk_adjusted_cache(code, {"code": code, "available": True, "sharpe_ratio": 1.0})
    path = fra._risk_adjusted_cache_path(code)

    stop = threading.Event()
    errors: list = []

    def writer():
        i = 0
        while not stop.is_set():
            i += 1
            try:
                fra.set_risk_adjusted_cache(
                    code,
                    {"code": code, "available": True, "sharpe_ratio": 1.0 + i / 1000.0},
                )
            except Exception as e:  # pragma: no cover
                errors.append(e)
                break

    def reader():
        while not stop.is_set():
            if not path.exists():
                continue
            try:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw)  # 半截/空文件会在此抛 JSONDecodeError
                assert "v" in data and "t" in data
                assert data["v"]["code"] == code
                assert isinstance(data["v"]["sharpe_ratio"], (int, float))
            except Exception as e:
                errors.append(e)
                break

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    w.start()
    r.start()
    time.sleep(0.3)  # 让并发跑足一段时间
    stop.set()
    w.join()
    r.join()

    assert errors == []


def test_invalidate_removes_memory_and_file():
    """自愈原语：删除指定 code 的内存 + 文件缓存，使 get 回到「未命中」。"""
    fra.set_risk_adjusted_cache("DEL1", {"code": "DEL1", "available": False, "fund_type": "unknown"})
    assert fra.get_risk_adjusted_cache("DEL1") is not None
    path = fra._risk_adjusted_cache_path("DEL1")
    assert path.exists()

    fra.invalidate_risk_adjusted_cache("DEL1")

    assert fra.get_risk_adjusted_cache("DEL1") is None
    assert "DEL1" not in fra._RA_CACHE
    assert not path.exists()


# ──────────────────────────────────────────────────────────
# 入队去重 / 跳过已缓存 / 并发守卫
# ──────────────────────────────────────────────────────────

def test_enqueue_warmup_dedup_and_skip_cached():
    fra._WARMUP_RUNNING = True  # 假装已有 worker，enqueue 只入队不起线程

    # 正缓存 + 负缓存都应跳过（算过即不入队）
    fra.set_risk_adjusted_cache("H", {"code": "H", "available": True, "sharpe_ratio": 1.0})
    fra.set_risk_adjusted_cache("I", {"code": "I", "available": False})

    fra.enqueue_risk_adjusted_warmup(["H", "I", "J", "J", "K"])

    assert fra._PENDING_WARMUP == {"J": "", "K": ""}  # H/I 跳过，J 去重（list 向后兼容，name 空）

    fra._WARMUP_RUNNING = False


def test_enqueue_warmup_carries_name():
    fra._WARMUP_RUNNING = True  # 假装已有 worker，enqueue 只入队不起线程

    fra.enqueue_risk_adjusted_warmup({"017849": "东方红先进制造混合C", "001112": "某债基"})

    assert fra._PENDING_WARMUP == {
        "017849": "东方红先进制造混合C",
        "001112": "某债基",
    }  # name 随 code 一起入队，供 worker 类型识别兜底

    fra._WARMUP_RUNNING = False


def test_enqueue_does_not_start_second_worker(monkeypatch):
    started = []
    monkeypatch.setattr(fra, "_warm_risk_adjusted_worker", lambda: started.append(1))

    fra._WARMUP_RUNNING = True  # 已有 worker 在跑
    fra.enqueue_risk_adjusted_warmup(["L"])
    assert started == []  # 不再起新 worker

    fra._WARMUP_RUNNING = False


# ──────────────────────────────────────────────────────────
# 后台 worker：跳过已缓存 / 落负缓存 / 异常与超时吞掉
# ──────────────────────────────────────────────────────────

def test_worker_skips_already_cached(monkeypatch):
    fra.set_risk_adjusted_cache("P", {"code": "P", "available": True, "sharpe_ratio": 1.0})
    computed = []
    monkeypatch.setattr(
        fra,
        "compute_risk_adjusted_metrics",
        lambda code, name="", fund_type="": computed.append(code) or {"code": code, "available": True},
    )

    fra._PENDING_WARMUP["P"] = ""
    fra._warm_risk_adjusted_worker()

    assert computed == []  # 已缓存（TTL 内）→ 弹出后精确过滤跳过
    assert fra._WARMUP_RUNNING is False


def test_worker_writes_negative_cache_for_noncomputable(monkeypatch):
    monkeypatch.setattr(
        fra,
        "compute_risk_adjusted_metrics",
        lambda code, name="", fund_type="": {"code": code, "available": False, "reason": "债券型"},
    )

    fra._PENDING_WARMUP["M"] = ""
    fra._warm_risk_adjusted_worker()

    got = fra.get_risk_adjusted_cache("M")
    assert got is not None
    assert got["available"] is False  # 负缓存落盘
    assert fra._WARMUP_RUNNING is False


def test_worker_passes_name_to_compute(monkeypatch):
    """核心回归：worker 弹出 (code, name) 后必须把 name 传给 compute_risk_adjusted_metrics。

    修复「只传 code 不传 name」导致新基金被 classify_fund 误判 unknown → 落错误负缓存。
    """
    received = {}

    def _fake(code, name="", fund_type=""):
        received["code"] = code
        received["name"] = name
        return {"code": code, "name_provided": bool(name), "available": True, "sharpe_ratio": 1.5}

    monkeypatch.setattr(fra, "compute_risk_adjusted_metrics", _fake)

    fra._PENDING_WARMUP["017849"] = "东方红先进制造混合C"
    fra._warm_risk_adjusted_worker()

    assert received == {"code": "017849", "name": "东方红先进制造混合C"}
    got = fra.get_risk_adjusted_cache("017849")
    assert got is not None
    assert got["available"] is True
    assert fra._WARMUP_RUNNING is False


def test_compute_batch_swallows_compute_errors(monkeypatch):
    def _boom(code, name=""):
        raise RuntimeError("network down")

    monkeypatch.setattr(fra, "compute_risk_adjusted_metrics", _boom)
    # 单只失败不应向上抛，不拖垮整批
    fra._compute_batch(["O"])


def test_compute_batch_timeout_does_not_raise(monkeypatch):
    monkeypatch.setattr(fra, "_WARMUP_SINGLE_TIMEOUT", 0.05)

    def _slow(code, name=""):
        time.sleep(0.2)
        return {"code": code, "available": True, "sharpe_ratio": 1.0}

    monkeypatch.setattr(fra, "compute_risk_adjusted_metrics", _slow)
    # 超时只代表「不等」，_compute_batch 自身不抛异常
    fra._compute_batch(["N"])
    time.sleep(0.3)  # 等底层线程自行结束后落缓存，避免残留线程影响后续测试


if __name__ == "__main__":
    import traceback

    tests = [
        test_set_get_roundtrip_positive_and_negative,
        test_file_fallback_after_memory_clear,
        test_negative_cache_file_fallback_after_memory_clear,
        test_ttl_expiry_returns_none,
        test_atomic_write_no_tmp_leftover_and_valid_json,
        test_concurrent_get_set_no_race_corruption,
        test_atomic_write_concurrent_read_never_sees_partial_file,
        test_invalidate_removes_memory_and_file,
        test_enqueue_warmup_dedup_and_skip_cached,
        test_enqueue_warmup_carries_name,
        test_enqueue_does_not_start_second_worker,
        test_worker_skips_already_cached,
        test_worker_writes_negative_cache_for_noncomputable,
        test_worker_passes_name_to_compute,
        test_compute_batch_swallows_compute_errors,
        test_compute_batch_timeout_does_not_raise,
    ]
    passed = 0
    for t in tests:
        try:
            # 手动跑时用 pytest 的 monkeypatch 不方便，跳过需要 monkeypatch 的用例
            if t.__name__ in ("test_ttl_expiry_returns_none", "test_enqueue_does_not_start_second_worker",
                              "test_worker_skips_already_cached", "test_worker_writes_negative_cache_for_noncomputable",
                              "test_worker_passes_name_to_compute",
                              "test_compute_batch_swallows_compute_errors", "test_compute_batch_timeout_does_not_raise"):
                continue
            fra._RA_CACHE.clear()
            fra._PENDING_WARMUP.clear()
            fra._WARMUP_RUNNING = False
            t()
            passed += 1
            print(f"✓ {t.__name__}")
        except Exception:
            print(f"✗ {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} 通过")
