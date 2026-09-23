"""
AKShare 优化包装器
提供超时控制、缓存、快速健康检查等功能

FIX 2026-08-09: 从服务器同步回本地时，把硬编码的 /opt/moneybag/backend/data/cache
改成用项目统一的 config.DATA_DIR（本地 Mac 上没有 /opt/moneybag 这个路径，
硬编码会导致 mkdir 失败），行为不变，仅路径来源改为跟随环境变量/本地默认目录。

FIX 2026-09-22: 超时机制由「假超时」改为「真超时」，详见 _run_with_timeout()。
"""
import functools
import threading
import time
from typing import Any, Callable
import akshare as ak
from pathlib import Path
import pickle
import sys

# 必须在 `import config` 之前：以 `python3 scripts/akshare_optimized.py` 方式调用时
# sys.path[0] 是 scripts/ 而不是 backend/，先 import config 会抛
# ModuleNotFoundError: No module named 'config'（cron 走的就是这条路径）。
_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from config import DATA_DIR

# 缓存目录
CACHE_DIR = Path(DATA_DIR) / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 缓存有效期（秒）
#
# FIX 2026-09-22: stock_zh_a_spot 的 TTL 由 60 秒上调到 300 秒。
# 原因：ak.stock_zh_a_spot 是 70 页分页全量拉取（5564 行），实测耗时
# 19.42s / 21.97s。TTL 只有 60 秒时，相邻两次巡检（如 night_worker 01:00
# 与独立巡检 cron）几乎不可能命中缓存，这个 21 秒的高成本拉取被反复触发，
# 既拖慢巡检又增加被上游限流的概率。放宽到 5 分钟对「实时行情」巡检来说
# 仍是足够新的量级（巡检只判 rows > 100，不判具体价格）。
CACHE_TTL = {
    "stock_zh_a_spot": 300,  # 实时行情缓存5分钟（70页全量拉取成本高，见上）
    "fund_open_fund_info_em": 300,  # 基金信息缓存5分钟
}

# 超时放弃后，后台线程的名字（便于 jstack/py-spy 之类工具定位挂死线程）
_TIMEOUT_WORKER_NAME = "akshare-timeout-guard"


def _run_with_timeout(func: Callable[..., Any], timeout: float, *args: Any, **kwargs: Any) -> Any:
    """在 daemon 线程里执行 func，实现**真超时**（到点立即返回，绝不阻塞等待）。

    FIX 2026-09-22: 原实现是

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(ak.stock_zh_a_spot)
            data = future.result(timeout=timeout)

    这是**假超时**：`future.result(timeout=t)` 到点确实会抛 TimeoutError，
    但 `with` 块退出时要执行 `executor.shutdown(wait=True)`，必须等 worker
    线程把 func 跑完才返回 —— 也就是说超时后仍然被挂死的请求拖住。

    故障注入实测：注入 timeout=5，实际 14.1 秒才抛出（多拖 9.1 秒）。
    本项目上游 push2.eastmoney.com 历史上有 30%~70% 的间歇性 connection
    reset；一旦连接挂起不返回，进程会**永久卡死、永不超时**，巡检 cron
    挂死后既不产出也不退出，后续 cron 全部堆积。

    现在改成 daemon 线程 + join(timeout)：主线程到点就放弃结果并返回/抛异常；
    挂死的请求线程是 daemon，进程退出时直接被回收，不会阻塞解释器退出。
    （AKShare 是 scraper 式接口，内部无法中断，只能「放弃」不能「杀死」，
    这正是必须用 daemon 线程的原因。）

    与 infra/data_source/fallback.py 的 call_with_timeout() 是同一套机制
    （daemon 线程 + join）。这里没有直接复用它，是因为契约不同：
    它在超时时返回 None，而本模块的调用方（datasource_health_check 的
    raise_on_error=True）需要**区分「超时」与「上游返回 HTML 导致解码失败」**
    两种故障，必须显式抛 TimeoutError。

    Args:
        func: 待执行的同步函数。
        timeout: 超时秒数。
        *args / **kwargs: 透传给 func 的参数。

    Returns:
        func 的返回值。

    Raises:
        TimeoutError: 超过 timeout 仍未返回（后台线程仍在跑，但已被放弃）。
        Exception: func 自身抛出的异常，原样透传给调用方。
    """
    result_box: dict[str, Any] = {"value": None, "error": None}

    def _target() -> None:
        try:
            result_box["value"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - worker 里的异常要带回主线程重抛
            result_box["error"] = exc

    worker = threading.Thread(
        target=_target, name=_TIMEOUT_WORKER_NAME, daemon=True
    )
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        # 超时：放弃结果，绝不二次 join（那是老实现卡死的根因）。
        # 线程是 daemon，进程退出时会被直接终止，不会拖住解释器。
        raise TimeoutError(f"调用超时（>{timeout}秒）")

    if result_box["error"] is not None:
        raise result_box["error"]

    return result_box["value"]


def with_timeout(func, timeout=10):
    """为函数添加超时控制（真超时）

    FIX 2026-09-22: 内部由 ThreadPoolExecutor 改为 _run_with_timeout()
    （daemon 线程 + join），超时后立即抛 TimeoutError，不再被挂死的 worker
    拖住。签名与行为对调用方保持不变。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return _run_with_timeout(func, timeout, *args, **kwargs)
    return wrapper


def fast_health_check_stock_spot(timeout: int = 10):
    """
    快速健康检查：带超时控制（真超时，超时后立即返回 None）

    FIX 2026-09-22: 超时实现由 ThreadPoolExecutor 改为 _run_with_timeout()。
    """
    try:
        print(f"[快速检查] 调用 stock_zh_a_spot（{timeout}秒超时）...")
        start = time.time()

        data = _run_with_timeout(ak.stock_zh_a_spot, timeout)

        elapsed = time.time() - start
        print(f"[快速检查] 成功获取 {len(data)} 行，耗时 {elapsed:.2f} 秒")
        return data

    except TimeoutError:
        print(f"[快速检查] 超时（{timeout}秒），接口响应过慢")
        return None
    except Exception as e:
        print(f"[快速检查] 失败: {e}")
        return None


def optimized_stock_spot(use_cache=True, timeout=15, raise_on_error=False):
    """
    优化的 stock_zh_a_spot 调用
    - 支持缓存（默认5分钟，见 CACHE_TTL）
    - 支持超时控制（默认15秒；巡检侧已上调到 60 秒）
    - raise_on_error: 默认 False（失败返回 None，保持既有契约）。
      置 True 时把真实异常抛给调用方，供巡检展示准确的失败原因
      （区分"超时"与"上游返回 HTML 导致解码失败"）。
    """
    cache_key = "stock_zh_a_spot_full"
    cache_file = CACHE_DIR / f"{cache_key}.pkl"

    # 尝试从缓存加载
    if use_cache and cache_file.exists():
        mtime = cache_file.stat().st_mtime
        if time.time() - mtime < CACHE_TTL["stock_zh_a_spot"]:
            try:
                with open(cache_file,'rb') as f:
                    data = pickle.load(f)
                    print(f"[缓存] 加载 stock_zh_a_spot 数据（{len(data)} 行，龄: {int(time.time() - mtime)}秒）")
                    return data
            except Exception as e:
                print(f"[缓存] 加载失败: {e}")

    # 调用接口（带超时）
    print(f"[接口] 调用 stock_zh_a_spot（超时 {timeout} 秒）...")
    start = time.time()

    try:
        # FIX 2026-09-22: 真超时。老实现用 `with ThreadPoolExecutor(...)`，
        # 超时后还要等 worker 跑完才返回（实测 timeout=5 时 14.1s 才抛），
        # 上游连接挂起时会永久卡死。
        data = _run_with_timeout(ak.stock_zh_a_spot, timeout)

        elapsed = time.time() - start
        print(f"[接口] 成功获取 {len(data)} 行，耗时 {elapsed:.2f} 秒")

        # 保存到缓存
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(data, f)
            print(f"[缓存] 已保存 {len(data)} 行数据")
        except Exception as e:
            print(f"[缓存] 保存失败: {e}")

        return data

    except TimeoutError:
        print(f"[接口] 超时（{timeout}秒）")
        if raise_on_error:
            raise TimeoutError(f"调用超时（>{timeout}秒）") from None
        return None
    except Exception as e:
        print(f"[接口] 失败: {e}")
        if raise_on_error:
            raise
        return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("=== 测试优化后的接口 ===\n")

        # 测试1: 快速健康检查
        print("测试1: 快速健康检查（10秒超时）")
        data = fast_health_check_stock_spot()
        if data is not None:
            print(f"✓ 检查通过（{len(data)} 行）\n")
        else:
            print("✗ 检查失败\n")

        # 测试2: 优化调用（带缓存）
        print("测试2: 优化调用（带缓存）")
        data = optimized_stock_spot(use_cache=True, timeout=15)
        if data is not None:
            print(f"✓ 调用成功（{len(data)} 行）\n")
        else:
            print("✗ 调用失败\n")

        # 测试3: 再次调用（应该使用缓存）
        print("测试3: 再次调用（测试缓存）")
        data = optimized_stock_spot(use_cache=True, timeout=15)
        if data is not None:
            print(f"✓ 调用成功（{len(data)} 行）\n")
