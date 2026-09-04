"""
申万二级行业反查表（index_member_all 分页 2 次 + stock_basic 降级）。

设计依据：docs/design/signal-scout-fund-account.md §3.4 / §8.1

实测结论（生产服务器 2026-09-04）：
  * index_member_all 不带 ts_code + offset 翻页：offset=0 → 3000 行，offset=3000 → 2902 行。
  * 单页硬上限 3000 行；offset 有效、limit 无效。
  * 全量约 5902 行，两页取完，耗时约 0.5s。
  * 参数陷阱：`{"ts_code": 指数代码}`（如 801081.SI）会静默返回 0 行，禁用。
  * 降级：stock_basic(list_status=L) 一次 5555 行，industry 字段 110 个分类。
"""
import json
import time
from pathlib import Path

from config import DATA_DIR

from services.fund_signal.config import (
    SW_CACHE_TTL_SECONDS,
    SW_INDEX_MEMBER_PAGE_SIZE,
    SW_INDEX_MEMBER_OFFSETS,
)

_SW_CACHE_FILE = Path(DATA_DIR) / "_cache" / "sw_l2_member.json"

# index_member_all 需要的字段（实测返回列）。
_INDEX_MEMBER_FIELDS = "l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name"
_STOCK_BASIC_FIELDS = "ts_code,name,industry"


def _call(api_name: str, params: dict, fields: str) -> list:
    from services.tushare_data import _call_tushare
    return _call_tushare(api_name, params, fields) or []


def _load_cache() -> dict:
    """读落盘缓存；命中且未过期返回 {map, source}，否则 None。"""
    try:
        if not _SW_CACHE_FILE.exists():
            return None
        data = json.loads(_SW_CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("map"), dict):
            return None
        ts = float(data.get("ts", 0) or 0)
        if time.time() - ts > SW_CACHE_TTL_SECONDS:
            return None
        return {"map": data["map"], "source": str(data.get("source", "sw_l2"))}
    except Exception:
        return None


def _save_cache(result: dict) -> None:
    """写落盘缓存（失败只打印，不阻断）。"""
    try:
        _SW_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(),
            "source": result.get("source", "sw_l2"),
            "map": result.get("map", {}),
        }
        _SW_CACHE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"[FUND_SIGNAL] sw_l2 缓存写入失败: {e}")


def load_sw_l2() -> dict:
    """返回 {"map": {股票代码: {"l1","l2","l3"}}, "source": str}。

    source 取值：
      * "sw_l2"            — 主路径 index_member_all（申万二级/三级）
      * "tushare_industry" — 降级 stock_basic.industry（口径不同，文案需改标）
      * "none"             — 两级都失败，只报个股暴露、不报行业（不得整体失败）
    """
    cached = _load_cache()
    if cached is not None:
        return cached

    # ---- 主路径：index_member_all 分页（offset 0 / 3000）----
    rows: list = []
    for off in SW_INDEX_MEMBER_OFFSETS:
        page = _call("index_member_all", {"offset": off}, _INDEX_MEMBER_FIELDS)
        if not page:
            break
        rows.extend(page)
        if len(page) < SW_INDEX_MEMBER_PAGE_SIZE:
            break  # 未满页即到末尾

    if rows:
        sw_map = {
            r["ts_code"]: {
                "l1": str(r.get("l1_name", "") or ""),
                "l2": str(r.get("l2_name", "") or ""),
                "l3": str(r.get("l3_name", "") or ""),
                "name": str(r.get("name", "") or ""),   # 供 render 输出「中文名(6位代码)」
            }
            for r in rows
            if r.get("ts_code")
        }
        result = {"map": sw_map, "source": "sw_l2"}
        _save_cache(result)
        return result

    # ---- 降级：stock_basic.industry（110 个分类，口径不同）----
    rows = _call("stock_basic", {"list_status": "L"}, _STOCK_BASIC_FIELDS)
    if rows:
        sw_map = {
            r["ts_code"]: {
                "l1": str(r.get("industry", "") or ""),
                "l2": str(r.get("industry", "") or ""),
                "l3": "",
                "name": str(r.get("name", "") or ""),
            }
            for r in rows
            if r.get("ts_code")
        }
        result = {"map": sw_map, "source": "tushare_industry"}
        _save_cache(result)
        return result

    print("[FUND_SIGNAL] 申万行业映射两级数据源均不可用，仅报个股暴露")
    return {"map": {}, "source": "none"}


def load_sw_l2_map() -> dict:
    """返回 {股票代码: {"l1","l2","l3"}}（仅 map，供测试与单点复用）。"""
    return load_sw_l2()["map"]
