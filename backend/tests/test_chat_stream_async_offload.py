"""SSE 流式后端「异步根治」回归守卫
=====================================
背景：``api/chat.py`` 有三处（+FC 一处）在 async generator 里直接 ``for`` 同步
生成器（``LLMGateway.stream_sync`` / ``run_fc_agent_stream``，内部走同步
``httpx.Client``）。同步 ``next()`` 在事件循环线程里执行 → 整个 LLM 请求期间
（首字节等待 + 每个 chunk 的 read）堵死 event loop，同进程其它请求全部被卡。

修复：新增 ``api.chat._aiter_blocking``，把同步生成器的 ``next()`` 丢进线程池
（``asyncio.to_thread``）。本文件用**可控的慢速同步生成器**复现「阻塞 vs 不阻塞」，
做行为级断言（对比心跳协程在消费期间的推进次数），并附一个控制组反证：
不桥接的裸 ``for`` 确实会被堵死 —— 证明断言能区分两种情况，不是恒真。
"""
import asyncio
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.chat import _aiter_blocking  # noqa: E402

_GAP = 0.15     # 每个 chunk 前的同步阻塞时长（模拟 httpx read 等待）
_N = 5          # chunk 数，总阻塞 ≈ 0.75s


def _slow_sync_gen(n: int, gap: float):
    """同步生成器：每次产出前阻塞 gap 秒（模拟 stream_sync 的同步 read）。"""
    for i in range(n):
        time.sleep(gap)
        yield i


def _consume_with_heartbeat(bridged: bool):
    """消费一个慢速同步生成器，期间并发跑心跳；返回 (产出列表, 心跳次数)。

    bridged=True  → async for + _aiter_blocking（修复后路径）
    bridged=False → async generator 里裸 for（修复前路径，用作控制组）

    两者都留出 ``await asyncio.sleep(0)`` 模拟 ASGI 层每发一个 chunk 后的调度点，
    与被测的真实 StreamingResponse 驱动方式一致。
    """
    ticks = []

    async def _run():
        async def heartbeat():
            while True:
                ticks.append(1)
                await asyncio.sleep(0.01)

        async def agen():
            if bridged:
                async for x in _aiter_blocking(_slow_sync_gen(_N, _GAP)):
                    yield x
            else:
                for x in _slow_sync_gen(_N, _GAP):
                    yield x

        hb = asyncio.create_task(heartbeat())
        out = []
        try:
            async for x in agen():
                out.append(x)
                await asyncio.sleep(0)
        finally:
            hb.cancel()
        return out

    out = asyncio.run(_run())
    return out, len(ticks)


def test_aiter_blocking_matches_sync_iteration():
    """语义等价：桥接迭代的顺序/内容与直接迭代逐一致。"""

    async def _run():
        return [x async for x in _aiter_blocking(iter([1, 2, 3]))]

    assert asyncio.run(_run()) == [1, 2, 3]


def test_aiter_blocking_empty_generator():
    """空生成器：立即结束，不产任何元素。"""

    async def _run():
        return [x async for x in _aiter_blocking(iter([]))]

    assert asyncio.run(_run()) == []


def test_aiter_blocking_propagates_exception():
    """生成器内抛出的异常必须按原样透传（否则流式错误降级会失效）。"""

    def _boom():
        yield 1
        raise ValueError("boom")

    async def _run():
        got = []
        try:
            async for x in _aiter_blocking(_boom()):
                got.append(x)
        except ValueError:
            got.append("raised")
        return got

    assert asyncio.run(_run()) == [1, "raised"]


def test_aiter_blocking_keeps_event_loop_responsive():
    """核心断言：慢速同步生成期间，event loop 仍能调度心跳协程。"""
    out, ticks = _consume_with_heartbeat(bridged=True)
    assert out == list(range(_N))
    # 理论心跳上限 ≈ 总时长/10ms ≈ 75；被堵死则 ≈ 个位数。
    # 取 20 作阈值：远低于理论值（留调度抖动余量），又足以与"被堵死"区分。
    assert ticks >= 20, f"event loop 疑似被同步生成器堵死，心跳仅推进 {ticks} 次"


def test_unbridged_iteration_blocks_loop_control():
    """反证（控制组）：不桥接、async gen 里裸 for 同步生成器 → 心跳被堵死。

    这条证明上一条行为断言确实能区分「阻塞」与「不阻塞」，不是恒真假绿。
    裸 for 期间 loop 无法推进心跳，只在每个 chunk 的 yield 间隙跑一两次。
    """
    out, ticks = _consume_with_heartbeat(bridged=False)
    assert out == list(range(_N))
    assert ticks <= 8, f"控制组本应被堵死，却推进了 {ticks} 次（断言失效）"


def test_chat_stream_paths_use_async_bridge():
    """回归绊线：chat.py 不得再把同步流式生成器直接 for 进 async 上下文。

    精确匹配修复前的两种调用形状。命中即说明有人把 async for + 桥接改回了
    裸 for —— 本 bug 复发。仅作补充，行为正确性由上面的运行时断言保证。
    """
    src = (BACKEND_DIR / "api" / "chat.py").read_text(encoding="utf-8")
    assert "for chunk in gw.stream_sync(" not in src, (
        "chat.py 中仍有裸 for 消费 gw.stream_sync（同步生成器）→ 会堵死 event loop"
    )
    assert "for chunk in run_fc_agent_stream(" not in src, (
        "chat.py 中仍有裸 for 消费 run_fc_agent_stream（同步生成器）→ 会堵死 event loop"
    )
