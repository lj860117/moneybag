"""
钱袋子 — 公共工具函数
DataFrame 列名匹配、安全类型转换、AKShare超时保护
"""

import functools
import time as _time
import threading

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "utils",
    "scope": "public",
    "input": [],
    "output": "utility",
    "cost": "cpu",
    "tags": ['工具', '列名匹配', '类型转换'],
    "description": "公共工具函数：DataFrame列名匹配+安全类型转换+NaN清洗",
    "layer": "data",
    "priority": 9,
}
def find_col(cols, keywords):
    """模糊匹配列名"""
    for kw in keywords:
        for c in cols:
            if kw in str(c):
                return c
    return None


def safe_float(val):
    """安全转float，NaN返回None"""
    try:
        v = float(val)
        if v != v:  # NaN
            return None
        return round(v, 2)
    except (ValueError, TypeError):
        return None


def parse_fee(fee_str: str):
    """从费率字符串中提取数值，如 '0.15%' → 0.15"""
    try:
        s = str(fee_str).replace("%", "").strip()
        return float(s)
    except (ValueError, TypeError):
        return None


# ============================================================
# AKShare 全局超时保护 + 重试机制
# ============================================================

_AKSHARE_TIMEOUT = 15  # 秒
_AKSHARE_LOCK = threading.Lock()  # 简单的全局串行锁（防并发封IP）


def ak_call(func, *args, timeout=_AKSHARE_TIMEOUT, **kwargs):
    """带超时保护的 AKShare 调用包装器

    用法: result = ak_call(ak.stock_zh_a_spot_em)  # 无参数
         result = ak_call(ak.fund_open_fund_info_em, symbol="SZ")

    超时用守护线程实现：超时后主线程放弃等待并返回 None，
    僵死的请求线程随进程退出而回收（AKShare 内部无法中断，只能放弃）。
    """
    with _AKSHARE_LOCK:  # 防并发过快被封
        try:
            if timeout and timeout > 0:
                # 使用线程实现超时（比进程更轻量）
                result_container = [None]
                error_container = [None]

                def _run():
                    try:
                        result_container[0] = func(*args, **kwargs)
                    except Exception as e:
                        error_container[0] = e

                t = threading.Thread(target=_run, daemon=True)
                t.start()
                t.join(timeout=timeout)

                if t.is_alive():
                    print(f"[AK_TIMEOUT] {func.__name__} 超过 {timeout}s 未返回，强制跳过")
                    return None

                if error_container[0]:
                    raise error_container[0]

                return result_container[0]
            else:
                return func(*args, **kwargs)
        except Exception as e:
            print(f"[AK_ERROR] {func.__name__}: {e}")
            return None


def retry(max_retries=2, backoff_base=1, retry_on=(Exception,), silent=False):
    """通用重试装饰器

    @retry(max_retries=2, backoff_base=1)
    def fetch_data():
        ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*a, **kw)
                except retry_on as e:
                    last_exc = e
                    if attempt < max_retries:
                        wait = backoff_base * (2 ** attempt)
                        if not silent:
                            print(f"[RETRY] {fn.__name__} 第{attempt+1}次失败，{wait:.1f}s后重试: {e}")
                        _time.sleep(wait)
            raise last_exc
        return wrapper
    return decorator
