"""
FallbackRunner 超时保护守门测试（P1）
=====================================
背景：`infra/data_source/fallback.py` 的 `FallbackRunner.__init__` 接收
`timeout_per_provider: float = 5.0` 构造参数，但 `_try_provider()` 此前
从未使用它——直接同步调用 `provider_instance.fetch(...)`，没有任何超时
机制。这是"看起来有保护参数、实际完全没接上"的典型 bug，与 P0-c 里
`services/utils.py::ak_call()` 写了两个月零调用方是同一种模式。

这个文件测什么：
  - `timeout_per_provider` 真的会在 provider 挂死时生效，主线程按时返回
    而不是无限等待
  - 超时后 `_try_provider` 返回 `success=False` 且 `error` 里写明超时，
    而不是让异常冒泡到上层
  - 超时不影响正常成功路径（provider 正常返回时行为不变）
  - `timeout_per_provider<=0` 时保留"不限时"的旧行为（显式禁用超时）
  - fallback 链在某个 provider 超时后能继续尝试下一个 provider
    （超时和"provider 返回 None"应该有同样的降级效果）

不测什么：
  - 真实 AKShare/Tushare/Baostock 的网络调用本身（那是各 provider 内部
    实现的职责），这里只测 FallbackRunner 的超时编排逻辑，provider 用
    可控的 fake 对象注入。
"""
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.data_source.fallback import FallbackRunner


class _FakeProvider:
    """可控的 fake provider，用于模拟正常/挂死/异常/返回None四种场景。"""

    def __init__(self, behavior: str, sleep_seconds: float = 0, return_value=None):
        self.behavior = behavior
        self.sleep_seconds = sleep_seconds
        self.return_value = return_value
        self.fetch_call_count = 0

    def is_available(self) -> bool:
        return True

    def fetch(self, metric: str, **params):
        self.fetch_call_count += 1
        if self.behavior == "hang":
            # 模拟挂死：sleep 远超过测试设的 timeout_per_provider
            time.sleep(self.sleep_seconds)
            return self.return_value
        elif self.behavior == "raise":
            raise RuntimeError("模拟 provider 抛异常")
        elif self.behavior == "instant":
            return self.return_value
        raise ValueError(f"未知 behavior: {self.behavior}")


def _make_runner_with_fake_provider(fake_provider, timeout_per_provider=1.0, chain=None):
    """构造一个只含单个 fake provider 的 FallbackRunner。"""
    runner = FallbackRunner(
        metric="stock_price",
        chain=chain or ["fake"],
        timeout_per_provider=timeout_per_provider,
    )
    # monkeypatch _get_provider_instance 直接返回 fake，不走真实 import 逻辑
    runner._get_provider_instance = lambda name: fake_provider if name == "fake" else None
    return runner


def test_timeout_actually_bounds_wait_time():
    """核心验证：provider 挂死 10s，timeout_per_provider=0.5s 时必须在 ~0.5s 内返回，
    不能等 10s。这是本次修复要解决的核心问题。"""
    fake = _FakeProvider("hang", sleep_seconds=10, return_value="should_not_see_this")
    runner = _make_runner_with_fake_provider(fake, timeout_per_provider=0.5)

    t0 = time.time()
    data, meta = runner.fetch()
    elapsed = time.time() - t0

    assert elapsed < 2.0, f"应在 timeout_per_provider(0.5s) 附近返回，实际等了 {elapsed:.2f}s"
    assert data is None
    assert meta["source"] == "none"


def test_timeout_reports_as_failure_not_exception():
    """超时后 _try_provider 必须返回 success=False + error 说明，不能让异常冒泡。"""
    fake = _FakeProvider("hang", sleep_seconds=5)
    runner = _make_runner_with_fake_provider(fake, timeout_per_provider=0.3)

    # 不应抛异常
    result = runner._try_provider("fake")
    assert result["success"] is False
    assert result["data"] is None
    assert result["error"] is not None


def test_normal_success_path_unaffected_by_timeout_wiring():
    """正常返回时行为不变：能拿到数据、耗时远小于 timeout_per_provider。"""
    fake = _FakeProvider("instant", return_value={"rows": [1, 2, 3]})
    runner = _make_runner_with_fake_provider(fake, timeout_per_provider=5.0)

    data, meta = runner.fetch()

    assert data == {"rows": [1, 2, 3]}
    assert meta["source"] == "fake"
    assert fake.fetch_call_count == 1


def test_provider_exception_still_propagates_as_failure():
    """provider 抛异常（不是挂死）时，仍应被捕获为 success=False，行为与超时一致。"""
    fake = _FakeProvider("raise")
    runner = _make_runner_with_fake_provider(fake, timeout_per_provider=2.0)

    result = runner._try_provider("fake")
    assert result["success"] is False
    assert "模拟 provider 抛异常" in result["error"]


def test_timeout_disabled_when_non_positive():
    """timeout_per_provider<=0 应保留旧行为：不限时直接同步调用（显式禁用超时的转义阀）。

    FIX（F3 故障注入发现）：原实现只用 fake="instant"（立即返回）验证，
    即使 timeout<=0 时被错误地强制套上超时包装，instant provider 也不会
    触发超时、测试照样通过——这是一个"看起来测了但抓不住回归"的死测试，
    和 P0-c 里 test_filters_future_dates 是同一种问题（自证而非验证）。
    改为直接断言"timeout<=0 时压根不创建 daemon 线程"，不依赖计时。
    """
    fake = _FakeProvider("instant", return_value="ok")
    runner = _make_runner_with_fake_provider(fake, timeout_per_provider=0)

    with patch("infra.data_source.fallback.threading.Thread") as mock_thread:
        data, meta = runner.fetch()

    assert data == "ok"
    assert not mock_thread.called, (
        "timeout_per_provider<=0 时不应创建任何 daemon 线程，"
        "应直接同步调用 provider_instance.fetch()"
    )


def test_fallback_chain_continues_after_timeout():
    """核心场景：第一个 provider 挂死超时后，链条必须继续尝试下一个 provider，
    不能因为第一个超时就整体失败——超时和"返回 None"应该有同样的降级效果。
    """
    hanging = _FakeProvider("hang", sleep_seconds=10)
    healthy = _FakeProvider("instant", return_value={"source": "backup"})

    runner = FallbackRunner(
        metric="stock_price",
        chain=["primary", "backup"],
        timeout_per_provider=0.3,
    )

    def _get_instance(name):
        return {"primary": hanging, "backup": healthy}.get(name)

    runner._get_provider_instance = _get_instance

    t0 = time.time()
    data, meta = runner.fetch()
    elapsed = time.time() - t0

    assert elapsed < 2.0, f"两级链条总耗时应远小于挂死的10s，实际 {elapsed:.2f}s"
    assert data == {"source": "backup"}
    assert meta["source"] == "backup"
    assert meta["attempts"] == 2
    # 确认第一个确实被尝试过（挂死记录在 attempt_log 里），不是被跳过
    assert healthy.fetch_call_count == 1


def test_default_timeout_is_five_seconds():
    """回归锁定：FallbackRunner 默认超时应保持 5.0s（与既有文档/注释一致），
    防止有人不小心改动默认值影响所有未显式传参的调用方。"""
    runner = FallbackRunner(metric="stock_price")
    assert runner.timeout_per_provider == 5.0


# ============================================================
# call_with_timeout() 纯函数测试（FIX 2026-09-01 追加）
# ============================================================
# 背景：把 FallbackRunner._fetch_with_timeout 的 thread+join 逻辑抽成
# 模块级纯函数 call_with_timeout()，供 market/stocks.py 里未经过
# FallbackRunner 编排的裸调用（get_stock_spot_xq/get_stock_daily_legacy/
# get_fund_name_list/get_fund_estimated_nav）复用，避免同一个超时模式
# 在 infra/data_source 里散落多份实现。
# 之所以不直接复用 services/utils.py::ak_call()：本仓 .importlinter
# 定义 infra 层不能反向依赖 services（四层架构 api>use_cases>domain>infra），
# 且 ak_call() 带的 _AKSHARE_LOCK 是 AKShare 专属并发限制，不该被
# infra 层所有裸调用都套上。

from infra.data_source.fallback import call_with_timeout


def _hang(seconds: float):
    time.sleep(seconds)
    return "should_not_see_this"


def _instant(value="ok"):
    return value


def _raise():
    raise RuntimeError("模拟调用抛异常")


def test_call_with_timeout_bounds_wait_time():
    """核心验证：函数挂死 5s，timeout=0.3s 时必须在 ~0.3s 内返回 None，
    不能等 5s。"""
    t0 = time.time()
    result = call_with_timeout(_hang, 0.3, 5)
    elapsed = time.time() - t0

    assert elapsed < 2.0, f"应在 timeout(0.3s) 附近返回，实际等了 {elapsed:.2f}s"
    assert result is None


def test_call_with_timeout_normal_path_unaffected():
    """正常返回时行为不变。"""
    result = call_with_timeout(_instant, 5.0, value="real_data")
    assert result == "real_data"


def test_call_with_timeout_propagates_exception_when_not_hanging():
    """函数抛异常（不是挂死）时，异常应正常冒泡，不能被超时机制吞掉。"""
    with pytest.raises(RuntimeError, match="模拟调用抛异常"):
        call_with_timeout(_raise, 2.0)


def test_call_with_timeout_disabled_when_non_positive():
    """timeout<=0 时应直接同步调用，不创建线程（显式禁用超时的转义阀）。"""
    with patch("infra.data_source.fallback.threading.Thread") as mock_thread:
        result = call_with_timeout(_instant, 0, value="ok")

    assert result == "ok"
    assert not mock_thread.called, "timeout<=0 时不应创建任何 daemon 线程"


def test_fallback_runner_still_works_after_refactor_to_shared_function():
    """回归验证：FallbackRunner._fetch_with_timeout 委托给 call_with_timeout
    后，原有的挂死场景仍然正确处理（防止重构引入行为差异）。"""
    fake = _FakeProvider("hang", sleep_seconds=5)
    runner = _make_runner_with_fake_provider(fake, timeout_per_provider=0.3)

    t0 = time.time()
    result = runner._try_provider("fake")
    elapsed = time.time() - t0

    assert elapsed < 2.0
    assert result["success"] is False

