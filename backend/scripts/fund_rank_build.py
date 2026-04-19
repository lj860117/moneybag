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

产出：
  moneybag/backend/data/fund_rank_ts.json
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
sys.path.insert(0, str(ROOT))

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


OUTPUT_FILE = ROOT / "backend" / "data" / "fund_rank_ts.json"


def find_latest_trade_date() -> str:
    """往前找最多 10 天，找到有净值数据的日期"""
    for i in range(1, 11):
        td = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        navs = get_fund_nav_by_date(td)
        if navs and len(navs) > 1000:
            return td, navs
    raise RuntimeError("10 天内都找不到有净值数据的日期")


def find_nav_date_before(days_before: int, latest_td: str) -> tuple:
    """找 N 天前有数据的日期"""
    base = datetime.strptime(latest_td, "%Y%m%d")
    for offset in range(days_before, days_before + 15):
        td = (base - timedelta(days=offset)).strftime("%Y%m%d")
        navs = get_fund_nav_by_date(td)
        if navs and len(navs) > 1000:
            return td, navs
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
        "index": filter_type(["指数"]),
        "qdii": filter_type(["QDII"]),
        "etf": [r for r in ranks_all if "ETF" in (r["name"] or "")][:200],
    }

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
            "ubuntu@150.158.47.189:/opt/moneybag/backend/data/fund_rank_ts.json",
        ])
        if r.returncode == 0:
            print("✅ 上传成功")
        else:
            print("❌ 上传失败")
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
