"""
钱袋子 v9.8.8 — IPO观察列表自动验证 + 自动发现新热门IPO
=========================================================
每周日自动运行：
1. 验证已知观察列表公司的 IPO 状态（用真实新闻搜索，不再依赖 LLM 知识）
2. 自动发现新热门 IPO（A股/港股/美股）

数据来源：
- A股：AKShare stock_ipo_ths（新股申购日历）
- 港股：AKShare stock_ipo_hk_ths（港股新股日历）
- 美股：web_search 搜索近期 "IPO filing" "S-1" 新闻

用法:
  python3 scripts/ipo_verify.py          # 验证已知 + 发现新热门
  python3 scripts/ipo_verify.py --verify-only  # 只验证已知
  python3 scripts/ipo_verify.py --discover-only # 只发现新热门

由 dca_scheduler.py --weekly 调用(每周日20:00)
"""

import sys
import os
import json
import time
import re
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DATA_DIR = Path(os.environ.get("DATA_DIR", "data")).resolve()  # 转成绝对路径
CACHE_FP = DATA_DIR / "_cache" / "ipo_status.json"
DISCOVER_FP = DATA_DIR / "_cache" / "ipo_discovered.json"
WATCHLIST_FP = DATA_DIR / "ipo_watchlist.json"  # 权威配置文件（替代硬编码）

# 状态关键词（按可信度排序，前面的优先匹配）
STATUS_KEYWORDS = {
    "✅ 已上市": [
        "完成IPO", "正式上市", "成功上市", "挂牌交易", "股票代码", "纳斯达克上市",
        "港交所上市", "科创板上市", "begins trading", "lists on", "IPO priced", "went public",
        "started trading", "debuts on", "上市交易", "正式挂牌",
    ],
    "已提交招股书": [
        "提交招股书", "提交S-1", "提交F-1", "S-1 filing", "prospectus filed",
        "IPO申请", "IPO prospectus", "filed for IPO",
    ],
    "进行中": [
        "IPO进程", "上市辅导", "辅导备案", "IPO辅导", "过会", "IPO审核",
        "approved IPO", "IPO approved",
    ],
    "已取消": [
        "取消IPO", "终止上市", "推迟IPO", "withdraws IPO", "IPO cancelled",
        "IPO postponed", "终止IPO",
    ],
}

# 观察列表（ fallback：当 ipo_watchlist.json 不存在时使用）
DEFAULT_WATCHLIST = [
    {"name": "长鑫科技", "market": "A股科创板", "status": "进行中"},
    {"name": "长江存储", "market": "A股科创板", "status": "传闻中"},
    {"name": "xAI", "market": "美股", "status": "已取消"},
    {"name": "SpaceX", "market": "美股纳斯达克", "status": "✅ 已上市"},
    {"name": "字节跳动", "market": "港股/美股", "status": "传闻中"},
    {"name": "英伟达", "market": "美股纳斯达克", "status": "✅ 已上市"},
    {"name": "宁德时代", "market": "A股", "status": "✅ 已上市"},
]


def load_watchlist() -> list:
    """加载观察列表（优先读 ipo_watchlist.json，不存在则用硬编码）"""
    if WATCHLIST_FP.exists():
        try:
            data = json.loads(WATCHLIST_FP.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) > 0:
                return data
        except Exception:
            pass
    return DEFAULT_WATCHLIST


def save_watchlist(watchlist: list):
    """保存观察列表到 JSON 文件"""
    WATCHLIST_FP.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_FP.write_text(
        json.dumps(watchlist, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[IPO] 观察列表已保存到 {WATCHLIST_FP}")


def _search_news_keywords(name: str, market: str) -> str:
    """用新闻搜索 + 关键词匹配判断 IPO 状态（不依赖 LLM 知识）"""
    # 构建搜索关键词
    queries = []
    if market.startswith("美股"):
        queries = [
            f"{name} IPO 上市 2026",
            f"{name} Nasdaq listing 2026",
            f"{name} IPO priced",
        ]
    elif market.startswith("港股"):
        queries = [
            f"{name} 港股上市 2026",
            f"{name} 港交所 IPO",
        ]
    else:  # A股
        queries = [
            f"{name} IPO 上市 2026",
            f"{name} 科创板 上市",
        ]

    all_titles = []
    try:
        from services.web_search import web_search
        for q in queries[:2]:  # 最多搜2个关键词
            try:
                results = web_search(q, max_results=5)
                for r in results:
                    title = r.get("title", "") or ""
                    snippet = r.get("snippet", "") or ""
                    all_titles.append(title)
                    all_titles.append(snippet)
            except Exception:
                pass
            time.sleep(0.5)
    except Exception:
        pass

    # 如果 web_search 不可用，尝试 AKShare 新闻
    if not all_titles:
        try:
            import akshare as ak
            # 尝试获取该公司相关新闻（A股）
            if not market.startswith("美股"):
                df = ak.stock_news_em(symbol=name)
                if df is not None and len(df) > 0:
                    for _, row in df.head(10).iterrows():
                        all_titles.append(str(row.get("新闻标题", "")))
        except Exception:
            pass

    if not all_titles:
        return ""  # 无数据，不更新状态

    # 关键词匹配（按状态优先级）
    text = " ".join(all_titles).lower()

    for status, keywords in STATUS_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                return status

    return ""  # 无匹配，保持原状态


def verify_known_ipos() -> dict:
    """验证已知观察列表公司的 IPO 状态"""
    print("[IPO验证] 开始检查观察列表...")
    watchlist = load_watchlist()
    results = []
    updated = []

    # 读取上次验证结果
    prev_status = {}
    if CACHE_FP.exists():
        try:
            prev_status = json.loads(CACHE_FP.read_text(encoding="utf-8"))
        except Exception:
            pass

    for item in watchlist:
        name = item["name"]
        market = item.get("market", "")
        print(f"  检查: {name} ({market})...")

        new_status = _search_news_keywords(name, market)

        old = prev_status.get(name, {}).get("status", item.get("status", "未知"))
        if new_status and new_status != old:
            print(f"    ⚠️ 状态变化: {old} → {new_status}")
            updated.append({"name": name, "old": old, "new": new_status})
            item["status"] = new_status  # 更新内存中的状态
        elif new_status:
            item["status"] = new_status

        results.append({
            "name": name,
            "market": market,
            "status": new_status or old,
            "verified_at": datetime.now().isoformat(),
        })
        time.sleep(1)  # 限流

    # 保存验证结果
    output = {
        r["name"]: {"status": r["status"], "verified_at": r["verified_at"]}
        for r in results
    }
    CACHE_FP.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FP.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 保存更新后的观察列表
    save_watchlist(watchlist)

    if updated:
        print(f"  🔔 {len(updated)}个公司状态有变化!")
    else:
        print("  ✅ 所有状态无变化")

    return {"verified": len(results), "updated": updated}


def discover_hot_ipos() -> list:
    """自动发现新热门 IPO（A股/港股/美股）"""
    print("[IPO发现] 开始搜索新热门IPO...")
    discovered = []
    now_str = datetime.now().strftime("%Y-%m-%d")

    # ---- A股：从 AKShare 新股申购日历获取 ----
    try:
        import akshare as ak
        print("  [A股] 获取新股申购日历...")
        df = ak.stock_ipo_ths(symbol="全部A股")
        if df is not None and len(df) > 0:
            # 取近30天的新股或即将上市的新股
            recent = df.tail(30)
            for _, row in recent.iterrows():
                name = str(row.get("股票名称", "") or row.iloc[1] if len(row) > 1 else "")
                code = str(row.get("申购代码", "") or row.iloc[0] if len(row) > 0 else "")
                ipo_date = str(row.get("申购日期", "") or "")
                if name and name not in [d["name"] for d in discovered]:
                    discovered.append({
                        "name": name,
                        "code": code,
                        "market": "A股",
                        "status": "进行中",
                        "ipo_date": ipo_date,
                        "source": "akshare_ipo_cn",
                        "discovered_at": now_str,
                    })
            print(f"    ✅ 发现 {len(discovered)} 只A股新股")
    except Exception as e:
        print(f"    ❌ A股新股获取失败: {e}")

    # ---- 港股：从 AKShare 港股新股获取 ----
    try:
        import akshare as ak
        print("  [港股] 获取港股新股...")
        df_hk = ak.stock_ipo_hk_ths()
        if df_hk is not None and len(df_hk) > 0:
            for _, row in df_hk.head(20).iterrows():
                name = str(row.get("股票名称", "") or "")
                if name and name not in [d["name"] for d in discovered]:
                    discovered.append({
                        "name": name,
                        "market": "港股",
                        "status": "进行中",
                        "source": "akshare_ipo_hk",
                        "discovered_at": now_str,
                    })
            print(f"    ✅ 发现 {sum(1 for d in discovered if d['market']=='港股')} 只港股新股")
    except Exception as e:
        print(f"    ❌ 港股新股获取失败: {e}")

    # ---- 美股：用 web_search 搜索近期热门 IPO ----
    try:
        from services.web_search import web_search
        print("  [美股] 搜索近期热门IPO...")
        queries = [
            "upcoming IPO 2026 Nasdaq billion dollar valuation",
            "hot IPO filing 2026 unicorn",
            "most anticipated IPO 2026 US",
        ]
        seen_names = set(d["name"] for d in discovered)
        for q in queries[:2]:
            try:
                results = web_search(q, max_results=10)
                for r in results:
                    title = r.get("title", "") or ""
                    # 从标题中提取可能的公司名（简单启发式）
                    # 匹配大写开头的英文单词（可能是公司名）
                    names = re.findall(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2})\b", title)
                    for n in names[:3]:
                        if n not in seen_names and len(n) > 3 and n not in ("IPO", "Nasdaq", "NYSE", "SEC"):
                            discovered.append({
                                "name": n,
                                "market": "美股",
                                "status": "传闻中",
                                "note": title[:100],
                                "source": "web_search",
                                "discovered_at": now_str,
                            })
                            seen_names.add(n)
                time.sleep(1)
            except Exception:
                pass
        print(f"    ✅ 发现 {sum(1 for d in discovered if d['market']=='美股')} 只美股新股")
    except Exception as e:
        print(f"    ❌ 美股新股搜索失败: {e}")

    # 过滤：只保留"热门"的（有估值/知名度的）
    # 简单规则：已有备注信息或来自 A股/港股确定数据的
    hot_discovered = []
    for d in discovered:
        # A股/港股的直接保留
        if d["market"] in ("A股", "港股"):
            hot_discovered.append(d)
        # 美股需要有备注信息（说明搜索结果里有相关内容）
        elif d.get("note") and len(d.get("note", "")) > 20:
            hot_discovered.append(d)

    # 保存发现结果
    DISCOVER_FP.parent.mkdir(parents=True, exist_ok=True)
    # 合并历史发现（避免重复）
    history = []
    if DISCOVER_FP.exists():
        try:
            history = json.loads(DISCOVER_FP.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 去重（按 name + market）
    seen = set()
    merged = []
    for d in (history + hot_discovered):
        key = f"{d['name']}_{d['market']}"
        if key not in seen:
            seen.add(key)
            merged.append(d)
    DISCOVER_FP.write_text(
        json.dumps(merged[-50:], ensure_ascii=False, indent=2),  # 最多保留50条
        encoding="utf-8"
    )
    print(f"[IPO发现] 共发现 {len(hot_discovered)} 只新热门IPO（总计 {len(merged)} 条历史记录）")
    return hot_discovered


def sync_watchlist_to_api():
    """将 ipo_watchlist.json 同步到 API 读取的格式（供 fund_detail.py 使用）"""
    watchlist = load_watchlist()
    # 补充默认字段（index/funds/note/flag/fundType）
    DEFAULTS = {
        "长鑫科技": {"index": ["科创50", "中证1000"], "funds": ["华夏科创50ETF联接A", "国泰中证1000ETF联接A"], "note": "国内DRAM存储芯片龙头", "flag": "🇨🇳", "fundType": "index"},
        "长江存储": {"index": ["科创50", "中证半导体"], "funds": ["华夏科创50ETF联接A", "国联安中证半导体ETF联接"], "note": "国内NAND Flash龙头", "flag": "🇨🇳", "fundType": "index"},
        "xAI": {"index": ["纳斯达克100", "标普500"], "funds": ["博时纳斯达克100ETF联接C"], "note": "2025年被SpaceX收购，不再独立IPO", "flag": "🇺🇸", "fundType": "qdii"},
        "SpaceX": {"index": ["纳斯达克100"], "funds": ["博时纳斯达克100ETF联接C"], "note": "2026-06-12 纳斯达克上市，代码SPCX", "flag": "🇺🇸", "fundType": "qdii"},
        "字节跳动": {"index": ["恒生科技", "纳斯达克100"], "funds": ["华夏恒生科技ETF联接A"], "note": "TikTok/抖音母公司，上市预期持续", "flag": "🌐", "fundType": "qdii"},
        "英伟达": {"index": ["纳斯达克100", "标普500"], "funds": ["博时纳斯达克100ETF联接C"], "note": "AI算力核心标的", "flag": "🇺🇸", "fundType": "qdii"},
        "宁德时代": {"index": ["科创50", "沪深300"], "funds": ["华夏科创50ETF联接A"], "note": "新能源龙头，2018年上市", "flag": "🇨🇳", "fundType": "index"},
    }
    full_list = []
    for item in watchlist:
        name = item["name"]
        entry = {"name": name, "market": item.get("market", ""), "status": item.get("status", "传闻中")}
        if name in DEFAULTS:
            entry.update(DEFAULTS[name])
        full_list.append(entry)

    # 写入 fund_detail.py 读取的缓存文件（让 API 直接读 JSON）
    api_cache_fp = DATA_DIR / "_cache" / "ipo_watchlist_api.json"
    api_cache_fp.write_text(
        json.dumps(full_list, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[IPO] API缓存已更新: {api_cache_fp}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true", help="只验证已知IPO状态")
    parser.add_argument("--discover-only", action="store_true", help="只发现新热门IPO")
    args = parser.parse_args()

    if args.discover_only:
        discover_hot_ipos()
    elif args.verify_only:
        verify_known_ipos()
    else:
        # 默认：先验证已知，再发现新热门
        verify_known_ipos()
        discover_hot_ipos()
        sync_watchlist_to_api()
    print("[IPO] 完成!")
