"""
AKShare 优化包装器
提供超时控制、缓存、快速健康检查等功能

FIX 2026-08-09: 从服务器同步回本地时，把硬编码的 /opt/moneybag/backend/data/cache
改成用项目统一的 config.DATA_DIR（本地 Mac 上没有 /opt/moneybag 这个路径，
硬编码会导致 mkdir 失败），行为不变，仅路径来源改为跟随环境变量/本地默认目录。
"""
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import akshare as ak
from pathlib import Path
import pickle

from config import DATA_DIR

# 缓存目录
CACHE_DIR = Path(DATA_DIR) / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 缓存有效期（秒）
CACHE_TTL = {
    "stock_zh_a_spot": 60,  # 实时行情缓存1分钟
    "fund_open_fund_info_em": 300,  # 基金信息缓存5分钟
}


def with_timeout(func, timeout=10):
    """为函数添加超时控制"""
    def wrapper(*args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, *args, **kwargs)
            try:
                return future.result(timeout=timeout)
            except FutureTimeoutError:
                raise TimeoutError(f"调用超时（>{timeout}秒）")
    return wrapper


def fast_health_check_stock_spot():
    """
    快速健康检查：带超时控制
    """
    try:
        print("[快速检查] 调用 stock_zh_a_spot（10秒超时）...")
        start = time.time()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(ak.stock_zh_a_spot)
            data = future.result(timeout=10)

        elapsed = time.time() - start
        print(f"[快速检查] 成功获取 {len(data)} 行，耗时 {elapsed:.2f} 秒")
        return data

    except FutureTimeoutError:
        print("[快速检查] 超时（10秒），接口响应过慢")
        return None
    except Exception as e:
        print(f"[快速检查] 失败: {e}")
        return None


def optimized_stock_spot(use_cache=True, timeout=15):
    """
    优化的 stock_zh_a_spot 调用
    - 支持缓存（默认1分钟）
    - 支持超时控制（默认15秒）
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
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(ak.stock_zh_a_spot)
            data = future.result(timeout=timeout)

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

    except FutureTimeoutError:
        print(f"[接口] 超时（{timeout}秒）")
        return None
    except Exception as e:
        print(f"[接口] 失败: {e}")
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
