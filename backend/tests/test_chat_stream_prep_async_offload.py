"""SSE 准备期 event-loop 阻塞根治 —— 回归守卫
=================================================
背景（2026-09-18，P0 —— 比流式阻塞更严重一个数量级的那处）：
``api/chat.py`` 的 ``chat_analysis_stream``（以及非流式 ``chat_analysis``）在
**async 函数体里直接调用同步的** ``_build_market_context()`` / ``_build_portfolio_context()``
（内部同步网络取数）。这两个函数在「SSE 流式开始之前」就把 event loop 同步堵死
数秒 ~ 数十秒，期间同进程其它请求（``/api/health``、cron 打进 API 的调用）全部饿死。

修复：新增 ``api.chat._build_contexts_offloaded`` —— 用 ``asyncio.to_thread`` 把两次
调用搬进默认线程池，并用 ``asyncio.gather`` 并行（两者互相独立）。

本文件用**可控慢速同步构建函数**复现「阻塞 vs 不阻塞」，断言心跳协程在准备期的
推进次数；并附一个**控制组反证**：不桥接的裸同步调用确实会堵死 event loop ——
证明断言能区分两种情况，不是恒真假绿。

⚠️ 本文件里的时间数字全部是**可控注入延迟**（``time.sleep``），用于稳定复现，
**不是真实上游延迟**。
"""
import asyncio
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api.chat as chat  # noqa: E402
from models.schemas import ChatRequest  # noqa: E402

_SLEEP = 0.25   # 每个构建函数的注入阻塞时长（秒）
_TICK = 0.01    # 心跳协程间隔（秒）


def _run_with_heartbeat(coro_factory) -> int:
    """跑 ``coro_factory()`` 期间并发一个 10ms 心跳协程，返回心跳推进次数。

    心跳次数直接反映 event loop 是否被同步调用堵死：被堵住 → 心跳几乎不推进。
    """
    ticks = []

    async def _run() -> None:
        async def heartbeat() -> None:
            while True:
                ticks.append(1)
                await asyncio.sleep(_TICK)

        hb = asyncio.create_task(heartbeat())
        try:
            await coro_factory()
        finally:
            hb.cancel()

    asyncio.run(_run())
    return len(ticks)


def _install_slow_builders(monkeypatch):
    """把两个构建函数替换成可控 sleep（同步），返回记录 enter/exit 的事件表。

    与独立复验者手法一致：真实上游在开发机不可控，用可控 sleep 是唯一能稳定
    对比「改动前/后」的手段。
    """
    events = []

    def slow_market(*_args, **_kwargs):
        events.append(("market", time.perf_counter(), "start"))
        time.sleep(_SLEEP)
        events.append(("market", time.perf_counter(), "end"))
        return "MARKET_CTX"

    def slow_portfolio(*_args, **_kwargs):
        events.append(("portfolio", time.perf_counter(), "start"))
        time.sleep(_SLEEP)
        events.append(("portfolio", time.perf_counter(), "end"))
        return "PORTFOLIO_CTX"

    monkeypatch.setattr(chat, "_build_market_context", slow_market)
    monkeypatch.setattr(chat, "_build_portfolio_context", slow_portfolio)
    return events


def _seg(events, name):
    start = next(t for n, t, ph in events if n == name and ph == "start")
    end = next(t for n, t, ph in events if n == name and ph == "end")
    return start, end


# ==========================================================================
# 一、offload helper 本体（行为级）
# ==========================================================================
def test_offloaded_prep_keeps_loop_responsive(monkeypatch):
    """★核心断言：准备期（两个慢构建并行）event loop 仍能调度心跳协程。"""
    _install_slow_builders(monkeypatch)
    req = ChatRequest(message="你好")

    ticks = _run_with_heartbeat(
        lambda: chat._build_contexts_offloaded(req, "default", swallow=True, tag="TEST")
    )

    # 准备期 ≈ _SLEEP（并行），心跳 10ms → 理论 ~25；被堵死则个位数。
    # 阈值 10：远低于理论值（留调度抖动余量），又足以与"被堵死"区分。
    assert ticks >= 10, f"准备期 event loop 疑似被堵死，心跳仅推进 {ticks} 次"


def test_offloaded_prep_runs_two_builders_in_parallel(monkeypatch):
    """★核心断言：两次构建必须**并行**（区间重叠），否则准备期没减半。"""
    events = _install_slow_builders(monkeypatch)
    req = ChatRequest(message="你好")

    market_ctx, portfolio_ctx = asyncio.run(
        chat._build_contexts_offloaded(req, "default", swallow=True, tag="TEST")
    )

    assert (market_ctx, portfolio_ctx) == ("MARKET_CTX", "PORTFOLIO_CTX")
    ms, me = _seg(events, "market")
    ps, pe = _seg(events, "portfolio")
    assert ps < me and ms < pe, (
        f"两次构建未并行（疑似串行）：market=({ms:.3f},{me:.3f}) "
        f"portfolio=({ps:.3f},{pe:.3f})"
    )


def test_offloaded_prep_swallows_one_failure(monkeypatch):
    """swallow=True：一个失败只丢它自己那份上下文（保留既有独立 try/except 语义）。"""

    def ok_market():
        return "MARKET_CTX"

    def boom_portfolio(*_a, **_k):
        raise RuntimeError("portfolio boom")

    monkeypatch.setattr(chat, "_build_market_context", ok_market)
    monkeypatch.setattr(chat, "_build_portfolio_context", boom_portfolio)
    req = ChatRequest(message="你好")

    market_ctx, portfolio_ctx = asyncio.run(
        chat._build_contexts_offloaded(req, "default", swallow=True, tag="TEST")
    )
    assert market_ctx == "MARKET_CTX"
    assert portfolio_ctx == "", "portfolio 失败时不应影响 market 那份上下文"


def test_offloaded_prep_propagates_when_not_swallowing(monkeypatch):
    """swallow=False：构建异常原样上抛（保留非流式端点既有行为）。"""

    def boom_market():
        raise RuntimeError("market boom")

    monkeypatch.setattr(chat, "_build_market_context", boom_market)
    monkeypatch.setattr(chat, "_build_portfolio_context", lambda *_a, **_k: "P")
    req = ChatRequest(message="你好")

    try:
        asyncio.run(
            chat._build_contexts_offloaded(req, "default", swallow=False, tag="TEST")
        )
        raised = False
    except RuntimeError:
        raised = True
    assert raised, "swallow=False 时构建异常必须向上抛"


# ==========================================================================
# 二、控制组反证：裸同步调用确实堵死 event loop
# ==========================================================================
def _bare_sync_prep(req: ChatRequest, uid: str):
    """复刻**修复前**的写法：async 体里直接同步调用两个构建函数。"""
    market_ctx = chat._build_market_context()
    portfolio_ctx = chat._build_portfolio_context(user_id=uid)
    return market_ctx, portfolio_ctx


def test_bare_sync_prep_blocks_loop_control(monkeypatch):
    """反证（控制组）：不桥接的裸同步调用 → 心跳被堵死。

    这条证明上面的行为断言确实能区分「阻塞」与「不阻塞」，不是恒真假绿。
    裸同步 2×_SLEEP=0.5s 期间 loop 无法推进心跳。
    """
    _install_slow_builders(monkeypatch)
    req = ChatRequest(message="你好")

    async def _call():
        return _bare_sync_prep(req, "default")

    ticks = _run_with_heartbeat(_call)
    assert ticks <= 5, f"控制组本应被堵死，却推进了 {ticks} 次（断言失效）"


# ==========================================================================
# 三、真实路由集成（准备期走 offload，且不堵 loop）
# ==========================================================================
def test_stream_route_prep_is_offloaded_and_parallel(monkeypatch):
    """★端到端：真实 ``chat_analysis_stream`` 的准备期走线程池 + 并行 + 不堵 loop。"""
    events = _install_slow_builders(monkeypatch)
    # 让准备期之后的流程离线、确定性：不触网、不依赖环境
    monkeypatch.setattr(chat, "classify_chat_intent",
                        lambda *_a, **_k: {"intent": "general"})
    monkeypatch.setattr(chat, "_build_system_prompt", lambda *_a, **_k: "sys")
    import api.chat_fc as chat_fc
    monkeypatch.setattr(chat_fc, "should_use_fc", lambda *_a, **_k: False)

    req = ChatRequest(message="你好")
    ticks = _run_with_heartbeat(lambda: chat.chat_analysis_stream(req))

    names = {n for n, _t, _ph in events}
    assert names == {"market", "portfolio"}, "准备期没有调用两个构建函数"

    ms, me = _seg(events, "market")
    ps, pe = _seg(events, "portfolio")
    assert ps < me and ms < pe, "真实路由的准备期两次构建未并行"

    assert ticks >= 10, f"真实路由准备期 event loop 疑似被堵死，心跳仅推进 {ticks} 次"


# ==========================================================================
# 四、源码级绊线：不得再把裸同步调用写回 async 路由体
# ==========================================================================
def test_chat_py_no_bare_sync_context_build():
    """绊线：``chat.py`` 不得再出现「async 体里裸同步构建」的调用形状。"""
    src = (BACKEND_DIR / "api" / "chat.py").read_text(encoding="utf-8")
    assert "market_ctx = _build_market_context()" not in src, (
        "chat.py 仍有裸同步 `market_ctx = _build_market_context()` → 准备期会堵死 event loop"
    )
    assert "portfolio_ctx = _build_portfolio_context(req.portfolio, user_id=uid)" not in src, (
        "chat.py 仍有裸同步 portfolio 构建 → 准备期会堵死 event loop"
    )
    # 两处端点（流式 + 非流式）都必须走离线 helper
    assert src.count("_build_contexts_offloaded(") >= 3, (
        "离线构建 helper 应在定义之外被流式与非流式两个端点各调用一次"
    )
