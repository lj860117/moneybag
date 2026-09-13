#!/usr/bin/env python3
"""
基金排行榜飞速版（2026-04-19 A++）
===================================
策略：4 次 Tushare 调用 → 全市场 1.7 万基金的 1y/3y 收益率排行榜

原理：
  - fund_basic(E) + fund_basic(O) → 全量名单
  - fund_nav(nav_date=今天)
  - fund_nav(nav_date=1 年前)
  - fund_nav(nav_date=3 年前)
  - 本地计算收益率 → 排序

⚠️ 上面说的"4 次调用"是**逻辑上**的 4 个日期，不是 4 次 HTTP 请求：
  fund_nav 单次调用最多返回 10500 行，而全市场一天有 24338 行净值记录，
  因此每个日期实际要 offset 翻页 3 次才取得全（详见 tushare_data.
  _fetch_fund_nav_rows 的实测注释）。控制调用次数的手段见
  _PROBE_MAX_PAGES 的说明。

产出：
  <DATA_DIR>/fund_rank_ts.json（DATA_DIR 来自 config，单一数据源）
  结构：{
    "generated_at": ISO_timestamp,
    "trade_date": "20260417",
    "ranks": {
      "all": [{code, name, type, nav, return_1y, return_3y, score}, ...],
      "stock": [...],
      "hybrid": [...],
      "bond": [...],
      "index": [...]
    }
  }

运行：
  python backend/scripts/fund_rank_build.py            # 默认构建到本地 data/
  python backend/scripts/fund_rank_build.py --upload   # 构建后 SCP 到线上
"""
import sys
import os
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[2]
# backend/ 必须单独进 sys.path：本脚本 import 的 backend.services.tushare_data 内部
# 用的是以 backend/ 为根的绝对导入（`from infra.cache import MemoryCache`），只插
# 仓库根会在这里抛 ModuleNotFoundError: No module named 'infra'。
# cron 用 `python scripts/fund_rank_build.py` 调用时 sys.path[0] 是 scripts/ 而不是
# cwd（uvicorn 从 backend/ 启动才碰巧能 import，所以 API 侧一直正常、只有 cron 崩），
# 这条路径必须显式补齐，不能依赖 cwd。
_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# 加载 .env
env = ROOT / "backend" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from backend.services.tushare_data import (  # noqa: E402
    get_fund_basic_all, get_fund_nav_by_date, is_configured,
)
# 统一走 config.DATA_DIR（单一数据源），不再硬编码 backend/data/。
# 否则与 fund_rank.py / night_worker.py / fund_screen.py / longterm_screen.py
# 这些读取方（都读 config.DATA_DIR/fund_rank_ts.json）落盘目录不一致，榜单永远读不到。
from backend.config import DATA_DIR  # noqa: E402


OUTPUT_FILE = Path(DATA_DIR) / "fund_rank_ts.json"

# ---- 找交易日时用"单页探测"，避免 Tushare 调用次数爆炸 ----
#
# get_fund_nav_by_date 改成 offset 翻页取全之后，一次调用最多要打 3 次
# Tushare（24338 行 / 每页 10000）。而 find_latest_trade_date 最多往前试
# 10 天、find_nav_date_before 各最多试 15 天 —— 若每次都取全，最坏情况是
#   (10 + 15 + 15) × 3 = 120 次 Tushare 请求，足以触发限流。
#
# 但这两个函数在**判定阶段**只需要知道"这一天有没有数据"（阈值 1000 条），
# 根本不需要全量 —— 第一页（10000 条）就绰绰有余。所以判定阶段统一用
# max_pages=1 只翻一页；确认命中后再取全一次。
#
# 命中后那次"取全"并不会重复消耗第一页的额度：_call_tushare 按
# (api_name, params, fields) 做进程内缓存，探测与取全的第一页 params 完全
# 相同（offset=0, limit=10000），直接命中缓存。于是：
#     现实情况（1~2 次命中）：探测 1~3 次 + 取全 3×3 页（其中 3 页命中缓存）
#                          ≈ 3 + 6 = 9 次请求
#     最坏情况（回退满）  ：探测 40 次 + 取全 6 次 ≈ 46 次请求（而非 120）
_PROBE_MAX_PAGES = 1

# ---- 指数基金的识别口径：看 invest_type，不看 fund_type ----
#
# 修前这里写的是 filter_type(["指数"])，即拿 fund_type 去匹配"指数"二字 ——
# **从建成那天起就永远匹配不到任何东西**，ranks.index 恒为空数组。
# 实测（2026-09-13，fund_basic 全量 17949 只）fund_type 的取值分布：
#     混合型 6417 / 股票型 6280 / 债券型 4758 / 货币型 335 / REITs 104 / 其他 55
# 里面**根本没有**含"指数"的类别。
#
# 真正区分指数基金的是 **invest_type**（投资风格），实测分布：
#     被动指数型 4757 + 增强指数型 837 = 5594 只（占全市场 31%）
# 这两个取值是 Tushare 自己的分类标签，不是我们从名称里猜的。抽样核对
# （各 12 只）100% 是 genuine 指数基金，如"大成中证畜牧养殖产业ETF"、
# "华宝沪深300增强策略ETF"。
#
# 为什么不顺带用名称匹配补全：另有 1337 只基金 invest_type 为 None，其中
# 494 只名称含"指数/ETF/沪深300"等关键词（抽样看绝大多数是 ETF）。它们
# **确实**是指数基金，但用名称猜会把"沪深300自由现金流ETF"这类边缘品种
# 和主动基金里名字带"指数"字样的混进来，准确率无法量化。这里宁可要
# **高准确率的部分覆盖**（5594 只），也不要一个准确率不明的"全量" ——
# 错误的分类比空分类更有害。漏掉的那部分 ETF 由下面已有的 `etf` 分类
# （按 "ETF" in name）兜住。
INDEX_INVEST_TYPES = ("被动指数型", "增强指数型")


def is_index_fund(item: dict) -> bool:
    """指数基金判定：只看 invest_type，不看 fund_type

    fund_type 里没有"指数"这个类别（见 INDEX_INVEST_TYPES 的实测注释），
    按 fund_type 匹配必然恒为空。

    Args:
        item: ranks_all 里的一条（必须带 invest_type 字段）。

    Returns:
        True 表示这是一只被动指数型 / 增强指数型基金。
    """
    return (item.get("invest_type") or "") in INDEX_INVEST_TYPES


def find_latest_trade_date() -> str:
    """往前找最多 10 天，找到有净值数据的日期

    判定阶段只翻一页（_PROBE_MAX_PAGES），命中后再取全 —— 详见该常量注释。
    """
    for i in range(1, 11):
        td = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        navs = get_fund_nav_by_date(td, max_pages=_PROBE_MAX_PAGES)
        if navs and len(navs) > 1000:
            return td, get_fund_nav_by_date(td)
    raise RuntimeError("10 天内都找不到有净值数据的日期")


def find_nav_date_before(days_before: int, latest_td: str) -> tuple:
    """找 N 天前有数据的日期（同样先单页探测，命中后再取全）"""
    base = datetime.strptime(latest_td, "%Y%m%d")
    for offset in range(days_before, days_before + 15):
        td = (base - timedelta(days=offset)).strftime("%Y%m%d")
        navs = get_fund_nav_by_date(td, max_pages=_PROBE_MAX_PAGES)
        if navs and len(navs) > 1000:
            return td, get_fund_nav_by_date(td)
    return "", []


def build_rank():
    if not is_configured():
        print("❌ Tushare 未配置（.env 没有 TUSHARE_TOKEN）")
        return 1

    print("="*60)
    print("🚀 基金排行榜飞速版构建")
    print("="*60)

    # Step 1: 全量名单
    print("\n[1/4] 拉全量基金名单...")
    basics = get_fund_basic_all()
    if not basics:
        print("❌ fund_basic 失败")
        return 2
    basic_map = {b["ts_code"]: b for b in basics}
    print(f"  ✅ {len(basics)} 只基金入库")

    # Step 2: 最新净值
    print("\n[2/4] 拉最新日净值...")
    latest_td, latest_navs = find_latest_trade_date()
    latest_map = {n["ts_code"]: n for n in latest_navs}
    print(f"  ✅ {latest_td}: {len(latest_navs)} 条")

    # Step 3: 1 年前
    print("\n[3/4] 拉 1 年前净值...")
    td_1y, navs_1y = find_nav_date_before(365, latest_td)
    map_1y = {n["ts_code"]: n for n in navs_1y} if navs_1y else {}
    print(f"  ✅ {td_1y or 'N/A'}: {len(navs_1y)} 条")

    # Step 4: 3 年前
    print("\n[4/4] 拉 3 年前净值...")
    td_3y, navs_3y = find_nav_date_before(365 * 3, latest_td)
    map_3y = {n["ts_code"]: n for n in navs_3y} if navs_3y else {}
    print(f"  ✅ {td_3y or 'N/A'}: {len(navs_3y)} 条")

    # 计算
    print("\n[计算] 算收益率 + 打分...")
    ranks_all = []
    for ts_code, nav in latest_map.items():
        basic = basic_map.get(ts_code)
        if not basic:
            continue
        try:
            cur_nav = float(nav.get("accum_nav") or nav.get("unit_nav") or 0)
        except (ValueError, TypeError):
            continue
        if cur_nav <= 0:
            continue

        r1y, r3y = None, None
        if ts_code in map_1y:
            try:
                prev = float(map_1y[ts_code].get("accum_nav") or map_1y[ts_code].get("unit_nav") or 0)
                if prev > 0:
                    r1y = round((cur_nav - prev) / prev * 100, 2)
            except (ValueError, TypeError):
                pass
        if ts_code in map_3y:
            try:
                prev = float(map_3y[ts_code].get("accum_nav") or map_3y[ts_code].get("unit_nav") or 0)
                if prev > 0:
                    r3y = round((cur_nav - prev) / prev * 100, 2)
            except (ValueError, TypeError):
                pass

        # 综合评分：1y 权重 60% + 3y 权重 40%
        score = None
        if r1y is not None and r3y is not None:
            score = round(r1y * 0.6 + r3y * 0.4, 2)
        elif r1y is not None:
            score = r1y

        ranks_all.append({
            "code": ts_code.split(".")[0],
            "ts_code": ts_code,
            "name": basic.get("name", ""),
            "type": basic.get("fund_type", ""),
            "invest_type": basic.get("invest_type", ""),
            "nav": cur_nav,
            "return_1y": r1y,
            "return_3y": r3y,
            "score": score,
            "status": basic.get("status", ""),
            "list_date": basic.get("list_date", ""),
            "issue_amount": basic.get("issue_amount"),
        })

    # 按 score 降序
    ranks_all = [r for r in ranks_all if r["score"] is not None]
    ranks_all.sort(key=lambda r: r["score"], reverse=True)

    # 分类
    def filter_type(keywords):
        return [r for r in ranks_all if any(k in (r["type"] or "") for k in keywords)][:500]

    ranks_by_type = {
        "all": ranks_all[:1000],
        "stock": filter_type(["股票"]),
        "hybrid": filter_type(["混合"]),
        "bond": filter_type(["债券", "定开债"]),
        # ⚠️ 指数基金不能走 filter_type（fund_type 里没有"指数"这个类别，
        # 走它就恒为空数组）；改按 invest_type 判定，见 INDEX_INVEST_TYPES。
        "index": [r for r in ranks_all if is_index_fund(r)][:500],
        "qdii": filter_type(["QDII"]),
        "etf": [r for r in ranks_all if "ETF" in (r["name"] or "")][:200],
    }

    # ⚠️ 空分类必须留痕：这个分类曾经**静默空了很久**没人发现（fund_type 里
    # 根本没有"指数"这个类别）。若 Tushare 哪天把 invest_type 的取值改名，
    # index 会再次静默归零 —— 所有测试断言的都只是我们写死的常量，抓不到
    # 上游改名，只有这条日志能在排障时被 grep 到。
    if not ranks_by_type["index"]:
        print(f"  ⚠️ index 分类为空：fund_basic 的 invest_type 取值可能已变化，"
              f"当前口径 {INDEX_INVEST_TYPES}，请重新核对 invest_type 分布")

    # 落盘
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "trade_date": latest_td,
        "date_1y_ago": td_1y,
        "date_3y_ago": td_3y,
        "total_funds": len(ranks_all),
        "ranks": ranks_by_type,
    }
    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 归档模式：按日期保存一份历史快照（保留最近 12 周）----
    archive_date = datetime.now().strftime("%Y%m%d")
    archive_file = OUTPUT_FILE.parent / f"fund_rank_ts_{archive_date}.json"
    archive_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   归档: {archive_file.name}")

    # 清理超过 12 周（84天）的旧归档
    cutoff = datetime.now() - timedelta(days=84)
    cleaned = 0
    for old_f in OUTPUT_FILE.parent.glob("fund_rank_ts_????????.json"):
        try:
            file_date = datetime.strptime(old_f.stem.split("_")[-1], "%Y%m%d")
            if file_date < cutoff:
                old_f.unlink()
                cleaned += 1
        except Exception:
            pass
    if cleaned > 0:
        print(f"   清理旧归档: {cleaned} 个")

    print()
    print("="*60)
    print(f"✅ 完成！共 {len(ranks_all)} 只有效基金")
    print(f"   输出: {OUTPUT_FILE}")
    print(f"   大小: {OUTPUT_FILE.stat().st_size // 1024} KB")
    print("="*60)

    # TOP 5 抽样
    print("\n🏆 综合 TOP 5:")
    for r in ranks_all[:5]:
        print(f"  {r['code']:<12} {r['name'][:20]:<22} 1y={r['return_1y']}% 3y={r['return_3y']}% score={r['score']}")

    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true", help="构建后 SCP 到线上")
    args = ap.parse_args()
    code = build_rank()
    if code != 0:
        return code
    if args.upload:
        import subprocess
        print("\n📤 上传到线上...")
        r = subprocess.run([
            "scp", str(OUTPUT_FILE),
            "ubuntu@150.158.47.189:/opt/moneybag/data/fund_rank_ts.json",
        ])
        if r.returncode == 0:
            print("✅ 上传成功")
        else:
            print("❌ 上传失败")
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
