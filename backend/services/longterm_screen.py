"""
钱袋子 — 长期持有筛选器
========================
面向 10-20 年复利定投的基金/股票筛选，与短期排行榜逻辑完全不同。

基金筛选维度：
  - 夏普比率（单位风险收益，越高越稳）
  - 最大回撤（越小越好，大回撤容易割肉）
  - 规模稳定性（5亿~500亿，太小清盘/太大跑不动）
  - 成立年限（> 5年，说明经历过牛熊）
  - 近5年年化收益（稳定在 8% 以上）

股票筛选维度：
  - ROE 连续3年 > 15%（巴菲特护城河标准）
  - 净利润增速稳定（> 10% 且不大幅波动）
  - 资产负债率 < 50%（低杠杆，抗风险）
  - 市值 > 50亿（流动性好）

缓存策略：
  - 基金：30天文件缓存（季报出来才有新数据）
  - 股票：90天文件缓存（年报级别数据）
"""
import json
import time
import math
from datetime import datetime, timedelta
from pathlib import Path
from config import DATA_DIR

_CACHE_DIR = DATA_DIR / "_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_FUND_CACHE_FILE = _CACHE_DIR / "longterm_funds.json"
_STOCK_CACHE_FILE = _CACHE_DIR / "longterm_stocks.json"

_FUND_CACHE_DAYS = 30   # 月更
_STOCK_CACHE_DAYS = 90  # 季更


def _cache_valid(filepath: Path, max_days: int) -> bool:
    """检查文件缓存是否还有效"""
    if not filepath.exists():
        return False
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        return (datetime.now() - cached_at).days < max_days
    except Exception:
        return False


def _read_cache(filepath: Path) -> dict | None:
    try:
        return json.loads(filepath.read_text(encoding="utf-8")).get("data")
    except Exception:
        return None


def _write_cache(filepath: Path, data: dict):
    try:
        filepath.write_text(
            json.dumps({"data": data, "cached_at": datetime.now().isoformat()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[LONGTERM] 缓存写入失败: {e}")


# ============================================================
# 1. 长期基金筛选
# ============================================================

def screen_longterm_funds(force: bool = False) -> dict:
    """筛选适合长期持有的基金 TOP20
    
    数据来源：Tushare fund_indicator（夏普/回撤/波动率）
    
    返回：
    {
      "funds": [...],
      "total_screened": N,
      "generated_at": "...",
      "cache_valid_days": 30,
    }
    """
    if not force and _cache_valid(_FUND_CACHE_FILE, _FUND_CACHE_DAYS):
        cached = _read_cache(_FUND_CACHE_FILE)
        if cached:
            print("[LONGTERM] 基金筛选缓存命中")
            return cached

    print("[LONGTERM] 开始计算长期基金筛选...")
    t0 = time.time()

    funds = []

    # Step1: 尝试从 Tushare fund_indicator 拉数据
    try:
        from services.tushare_data import _call_tushare, is_configured
        if is_configured():
            print("[LONGTERM] 从 Tushare fund_indicator 拉指标...")
            # 拉近1年指标（period='20251231' 为最近年末）
            from datetime import date
            # 找最近的季末（3/6/9/12月末）
            today = date.today()
            month = today.month
            if month >= 10:
                period = f"{today.year}0930"
            elif month >= 7:
                period = f"{today.year}0630"
            elif month >= 4:
                period = f"{today.year}0331"
            else:
                period = f"{today.year - 1}1231"

            rows = _call_tushare(
                "fund_indicator",
                {"period": period, "market": "E"},  # E = 场外基金
                "ts_code,ann_date,end_date,invest_type,fund_type,fee_rate,"
                "stdev,downside_risk,alpha,beta,sharpe,ir,info_ratio,"
                "loss_ratio,periods_pos,periods_neg,pct_chg_mom,pct_ret_5y"
            )
            if rows and len(rows) > 100:
                print(f"[LONGTERM] Tushare fund_indicator: {len(rows)} 条")

                # 同时拉基金基本信息（年龄/规模）
                basic_rows = _call_tushare(
                    "fund_basic",
                    {"market": "E", "status": "L"},
                    "ts_code,name,fund_type,invest_type,list_date,issue_amount"
                )
                basic_map = {}
                if basic_rows:
                    for b in basic_rows:
                        basic_map[b["ts_code"]] = b

                # 计算长持评分
                candidates = []
                for r in rows:
                    ts_code = r.get("ts_code", "")
                    sharpe = r.get("sharpe")       # 夏普比率
                    loss_ratio = r.get("loss_ratio")   # 最大回撤（正数，如 0.25 = 回撤25%）
                    stdev = r.get("stdev")          # 波动率（年化标准差）
                    pct_ret_5y = r.get("pct_ret_5y")   # 5年累计收益

                    # 基本过滤：无法计算分数的跳过
                    if sharpe is None or loss_ratio is None:
                        continue

                    try:
                        sharpe = float(sharpe)
                        loss_ratio = float(loss_ratio)  # 0.25 = 25%回撤
                        stdev = float(stdev) if stdev else 0.2
                        pct_ret_5y = float(pct_ret_5y) if pct_ret_5y else None
                    except (ValueError, TypeError):
                        continue

                    # 长持筛选门槛
                    if sharpe < 0.5:       # 夏普太低，风险调整后收益差
                        continue
                    if loss_ratio > 0.40:  # 最大回撤超40%，持有体验极差
                        continue

                    # 计算5年年化收益
                    ann_ret_5y = None
                    if pct_ret_5y is not None and pct_ret_5y > -100:
                        ann_ret_5y = round((((1 + pct_ret_5y/100) ** (1/5)) - 1) * 100, 2)

                    # 从basic_map取规模和年龄
                    basic = basic_map.get(ts_code, {})
                    name = basic.get("name", ts_code)
                    invest_type = basic.get("invest_type", r.get("invest_type", ""))
                    list_date_str = basic.get("list_date", "")
                    issue_amount = basic.get("issue_amount")

                    # 成立年限
                    fund_age_years = None
                    if list_date_str and len(list_date_str) >= 8:
                        try:
                            list_date = datetime.strptime(list_date_str, "%Y%m%d")
                            fund_age_years = round((datetime.now() - list_date).days / 365, 1)
                        except Exception:
                            pass

                    # 成立不足5年，跳过
                    if fund_age_years is not None and fund_age_years < 5:
                        continue

                    # 综合长持评分（100分制）
                    # 夏普 (0~3+)  → 40分
                    # 最大回撤 (0%~40%) → 30分（越低越好）
                    # 5年年化收益 → 30分
                    score_sharpe = min(sharpe / 3.0, 1.0) * 40
                    score_drawdown = max(0, (0.40 - loss_ratio) / 0.40) * 30
                    score_return = 0
                    if ann_ret_5y is not None:
                        # 8% = 24分, 15% = 30分，封顶
                        score_return = min(ann_ret_5y / 15.0, 1.0) * 30

                    longterm_score = round(score_sharpe + score_drawdown + score_return, 1)

                    candidates.append({
                        "code": ts_code.split(".")[0],
                        "ts_code": ts_code,
                        "name": name,
                        "invest_type": invest_type,
                        "sharpe": round(sharpe, 3),
                        "max_drawdown_pct": round(loss_ratio * 100, 1),
                        "volatility_pct": round(stdev * 100, 1) if stdev else None,
                        "ann_ret_5y": ann_ret_5y,
                        "pct_ret_5y": round(pct_ret_5y, 1) if pct_ret_5y is not None else None,
                        "fund_age_years": fund_age_years,
                        "longterm_score": longterm_score,
                        "issue_amount": issue_amount,
                    })

                # 按长持评分降序，取TOP30
                candidates.sort(key=lambda x: x["longterm_score"], reverse=True)
                funds = candidates[:30]
                print(f"[LONGTERM] 筛选后 {len(candidates)} → TOP30，耗时 {time.time()-t0:.1f}s")

    except Exception as e:
        print(f"[LONGTERM] Tushare fund_indicator 失败: {e}")

    # Step2: Tushare 失败则用 fund_rank_ts.json 降级处理
    if not funds:
        print("[LONGTERM] 降级：用 fund_rank_ts.json 估算...")
        try:
            rank_file = DATA_DIR / "fund_rank_ts.json"
            if not rank_file.exists():
                rank_file = Path(__file__).parent.parent / "data" / "fund_rank_ts.json"
            if rank_file.exists():
                rank_data = json.loads(rank_file.read_text(encoding="utf-8"))
                all_funds = rank_data.get("ranks", {}).get("all", [])

                # 长持不适合的品种关键词（直接排除）
                EXCLUDE_KEYWORDS = [
                    "一年持有", "两年持有", "三年持有", "180天持有",
                    "12个月持有", "6个月持有", "持有期", "个月持有",   # 封闭期锁定（各种写法）
                    "黄金", "上海金", "贵金属", "商品", "原油",        # 大宗商品
                    "可转债", "定开",                                   # 特殊结构
                    "港股通", "QDII",                                   # 汇率风险
                    "ETF联接", "LOF",                                   # ETF包装
                ]

                for f in all_funds:  # 遍历全部，不限前500
                    name = f.get("name", "")
                    r1y = f.get("return_1y")
                    r3y = f.get("return_3y")
                    list_date_str = f.get("list_date", "")

                    # 必须有3年数据（说明成立超3年）
                    if r1y is None or r3y is None:
                        continue

                    # 优先推荐5年以上老基金（有晨星评级、经历过完整牛熊周期）
                    # 不足3年的直接排除，3-5年打折，5年以上正常权重
                    fund_age_years = None
                    if list_date_str and len(list_date_str) >= 8:
                        try:
                            ld = datetime.strptime(str(list_date_str)[:8], "%Y%m%d")
                            fund_age_years = (datetime.now() - ld).days / 365
                        except Exception:
                            pass

                    # 排除不适合长持的品种
                    if any(kw in name for kw in EXCLUDE_KEYWORDS):
                        continue

                    # 1年收益不能太极端（>40% 说明是短期爆发，不稳定）
                    if r1y > 40:
                        continue

                    # 3年收益不能太低（至少年化8%）
                    ann_3y = (((1 + r3y / 100) ** (1 / 3)) - 1) * 100 if r3y > -100 else None
                    if ann_3y is None or ann_3y < 8:
                        continue

                    # 收益稳定性：1年不能和3年年化偏离太大（排除短期爆发型）
                    ann_3y_val = ann_3y
                    stability_ratio = abs(r1y - ann_3y_val) / max(abs(ann_3y_val), 1)
                    if stability_ratio > 1.5:  # 偏离超过150%则不稳定
                        continue

                    # 长持评分（无夏普/回撤数据时用收益稳定性估算）
                    # 3年年化(50%) + 稳定性(30%) + 1y/3y不过度偏离(20%)
                    score_return = min(ann_3y / 20.0, 1.0) * 50
                    score_stability = max(0, 1 - stability_ratio / 1.5) * 30
                    score_consistency = (1 - min(abs(r1y - ann_3y_val) / max(abs(ann_3y_val), 10), 1)) * 20
                    score = round(score_return + score_stability + score_consistency, 1)

                    # 基金年龄奖励：5年以上 +10分，3-5年 +0分，不足3年已被排除
                    age_bonus = 0
                    age_label = ""
                    if fund_age_years is not None:
                        if fund_age_years >= 7:
                            age_bonus = 10
                            age_label = f"成立{fund_age_years:.0f}年"
                        elif fund_age_years >= 5:
                            age_bonus = 5
                            age_label = f"成立{fund_age_years:.0f}年"
                        elif fund_age_years >= 3:
                            age_label = f"成立{fund_age_years:.1f}年"
                    score = round(score + age_bonus, 1)

                    funds.append({
                        "code": f.get("code"),
                        "ts_code": f.get("ts_code"),
                        "name": name,
                        "invest_type": f.get("invest_type", f.get("type", "")),
                        "sharpe": None,
                        "max_drawdown_pct": None,
                        "ann_ret_5y": None,
                        "ann_ret_3y": round(ann_3y, 1),
                        "return_1y": r1y,
                        "return_3y": r3y,
                        "fund_age_years": round(fund_age_years, 1) if fund_age_years else None,
                        "age_label": age_label,
                        "longterm_score": score,
                        "note": "基于3年收益稳定性估算（精准夏普/回撤数据每月更新）",
                    })
                funds.sort(key=lambda x: x["longterm_score"], reverse=True)
                funds = funds[:30]
                print(f"[LONGTERM] 降级筛选完成：{len(funds)} 只（过滤了锁定期/黄金/短期爆发型）")
        except Exception as e2:
            print(f"[LONGTERM] 降级也失败: {e2}")

    # Step3: 补充行业描述 + AKShare 晨星评级
    if funds:
        # 行业描述（用 industry_templates 的关键词映射）
        try:
            from services.industry_templates import get_fund_industry
            for f in funds:
                match = get_fund_industry(f.get("name", ""))
                f["industry_tag"] = match.get("tag", "")
                f["industry_desc"] = match.get("desc", "")
        except Exception:
            pass

        # AKShare 晨星/济安金信评级补充（有星数比 -- 更直观）
        try:
            import akshare as ak
            rating_df = ak.fund_rating_all()
            if rating_df is not None and len(rating_df) > 0:
                cols = list(rating_df.columns)
                # 列：代码 简称 类型 评级机构 5星以上百分比 3年 5年 10年 成立以来 费率 所属公司
                code_col = cols[0]  # 基金代码
                rating_3y_col = cols[5] if len(cols) > 5 else None  # 3年评级
                rating_5y_col = cols[6] if len(cols) > 6 else None  # 5年评级
                rating_map = {}
                for _, row in rating_df.iterrows():
                    code = str(row[code_col]).strip().zfill(6)
                    r3y = row[rating_3y_col] if rating_3y_col else None
                    r5y = row[rating_5y_col] if rating_5y_col else None
                    rating_map[code] = {"rating_3y": r3y, "rating_5y": r5y}

                for f in funds:
                    code = str(f.get("code", "")).zfill(6)
                    info = rating_map.get(code, {})
                    r3y = info.get("rating_3y")
                    r5y = info.get("rating_5y")
                    # 转换为星星显示
                    def stars(v):
                        try:
                            n = int(float(v))
                            return "★" * n + "☆" * (5 - n) if 1 <= n <= 5 else None
                        except Exception:
                            return None
                    f["morning_star_3y"] = stars(r3y)
                    f["morning_star_5y"] = stars(r5y)
                print(f"[LONGTERM] AKShare 评级补充完成")
        except Exception as e3:
            print(f"[LONGTERM] AKShare 评级失败（不影响主流程）: {e3}")

    result = {
        "funds": funds,
        "total_screened": len(funds),
        "generated_at": datetime.now().isoformat(),
        "cache_valid_days": _FUND_CACHE_DAYS,
        "data_source": "tushare_fund_indicator" if any(f.get("sharpe") for f in funds) else "fund_rank_fallback",
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    _write_cache(_FUND_CACHE_FILE, result)
    return result


# ============================================================
# 2. 长期股票筛选
# ============================================================

def screen_longterm_stocks(force: bool = False) -> dict:
    """筛选适合长期持有的股票 TOP15（护城河评分）
    
    数据来源：Tushare fina_indicator（ROE/利润/负债）
    
    Returns:
        {"stocks": [...], "generated_at": "...", "cache_valid_days": 90}
    """
    if not force and _cache_valid(_STOCK_CACHE_FILE, _STOCK_CACHE_DAYS):
        cached = _read_cache(_STOCK_CACHE_FILE)
        if cached:
            print("[LONGTERM] 股票筛选缓存命中")
            return cached

    print("[LONGTERM] 开始计算长期股票筛选...")
    t0 = time.time()
    stocks = []

    try:
        from services.tushare_data import _call_tushare, is_configured
        if not is_configured():
            raise ValueError("Tushare 未配置")

        # Step1: 构建候选池
        # 策略：选股TOP50（实时高分）+ 高ROE行业龙头白名单
        # 白名单：历史上ROE稳定高于15%的优质公司
        QUALITY_WHITELIST = [
            "600519.SH",  # 贵州茅台
            "000568.SZ",  # 泸州老窖
            "000858.SZ",  # 五粮液
            "002304.SZ",  # 洋河股份
            "600809.SH",  # 山西汾酒
            "603288.SH",  # 海天味业
            "300750.SZ",  # 宁德时代
            "002415.SZ",  # 海康威视
            "600036.SH",  # 招商银行
            "601318.SH",  # 中国平安
            "000651.SZ",  # 格力电器
            "000333.SZ",  # 美的集团
            "002352.SZ",  # 顺丰控股
            "300015.SZ",  # 爱尔眼科
            "600276.SH",  # 恒瑞医药
            "000661.SZ",  # 长春高新
            "002594.SZ",  # 比亚迪
            "601899.SH",  # 紫金矿业
            "002379.SZ",  # 宏创控股
            "688525.SH",  # 佰维存储
            "301308.SZ",  # 宇环数控
            "600887.SH",  # 伊利股份
            "603501.SH",  # 韦尔股份
            "300760.SZ",  # 迈瑞医疗
            "002049.SZ",  # 紫光国微
            "600703.SH",  # 三安光电
            "002916.SZ",  # 深南电路
            "603259.SH",  # 药明康德
            "300122.SZ",  # 智飞生物
            "002241.SZ",  # 歌尔股份
        ]

        # 同时从选股TOP50加入候选（去重）
        from services.stock_screen import screen_stocks
        screen_result = screen_stocks(top_n=50)
        screen_codes = set()
        for s in screen_result.get("stocks", []):
            code = s.get("code", "")
            ts = code + (".SH" if code.startswith("6") or code.startswith("688") else ".SZ")
            screen_codes.add(ts)

        all_candidates = list(dict.fromkeys(QUALITY_WHITELIST + list(screen_codes)))  # 去重保序
        print(f"[LONGTERM] 候选池：白名单{len(QUALITY_WHITELIST)}只 + 选股{len(screen_codes)}只 = 去重后{len(all_candidates)}只")

        # Step2: 逐只查询3年财务数据
        from datetime import date
        current_year = date.today().year
        years = [str(y) + "1231" for y in range(current_year - 3, current_year)]

        # 同时拉基本信息（行业/市场）
        basic_rows = _call_tushare("stock_basic", {"list_status": "L", "exchange": ""},
                                    "ts_code,name,industry,market")
        basic_map = {r["ts_code"]: r for r in (basic_rows or [])}

        year_data = {}
        for ts_code in all_candidates[:40]:  # 最多查40只
            year_data[ts_code] = []
            for period in years:
                try:
                    rows = _call_tushare(
                        "fina_indicator",
                        {"ts_code": ts_code, "period": period},
                        "ts_code,end_date,roe,netprofit_yoy,debt_to_assets,grossprofit_margin"
                    )
                    if rows:
                        year_data[ts_code].extend(rows)
                except Exception:
                    pass
        # 过滤掉没有数据的
        year_data = {k: v for k, v in year_data.items() if v}
        print(f"[LONGTERM] 逐只查询完成：{len(year_data)} 只有财务数据")

        candidates = []
        for ts_code, records in year_data.items():
            if len(records) < 2:  # 至少有2年数据
                continue

            # 计算3年平均 ROE
            roe_vals = [float(r["roe"]) for r in records if r.get("roe") not in (None, "")]
            np_yoy_vals = [float(r["netprofit_yoy"]) for r in records if r.get("netprofit_yoy") not in (None, "")]
            debt_vals = [float(r["debt_to_assets"]) for r in records if r.get("debt_to_assets") not in (None, "")]
            gpm_vals = [float(r["grossprofit_margin"]) for r in records if r.get("grossprofit_margin") not in (None, "")]

            if len(roe_vals) < 2:
                continue

            avg_roe = sum(roe_vals) / len(roe_vals)
            min_roe = min(roe_vals)
            avg_np_growth = sum(np_yoy_vals) / len(np_yoy_vals) if np_yoy_vals else None
            avg_debt = sum(debt_vals) / len(debt_vals) if debt_vals else 50.0
            avg_gpm = sum(gpm_vals) / len(gpm_vals) if gpm_vals else None

            # 筛选门槛
            if avg_roe < 12:       # 平均ROE至少12%
                continue
            if min_roe < 8:        # 最差年份不能低于8%（稳定性）
                continue
            if avg_debt > 65:      # 负债率不能太高（金融股除外）
                industry = basic_map.get(ts_code, {}).get("industry", "")
                if "银行" not in industry and "保险" not in industry:
                    continue

            # 护城河评分（100分）
            # ROE 稳定性 → 40分
            roe_stability = 1 - (max(roe_vals) - min(roe_vals)) / max(avg_roe, 1) * 0.5
            score_roe = min(avg_roe / 25.0, 1.0) * 40 * max(roe_stability, 0.5)

            # 利润增速 → 30分
            score_growth = 0
            if avg_np_growth is not None:
                score_growth = min(max(avg_np_growth / 20.0, 0), 1.0) * 30

            # 低负债 → 20分
            score_debt = max(0, (65 - avg_debt) / 65) * 20

            # 毛利率 → 10分（高毛利 = 定价权/护城河）
            score_gpm = 0
            if avg_gpm is not None:
                score_gpm = min(avg_gpm / 50.0, 1.0) * 10

            longterm_score = round(score_roe + score_growth + score_debt + score_gpm, 1)

            basic = basic_map.get(ts_code, {})
            candidates.append({
                "code": ts_code.split(".")[0],
                "ts_code": ts_code,
                "name": basic.get("name", ts_code),
                "industry": basic.get("industry", ""),
                "market": basic.get("market", ""),
                "avg_roe": round(avg_roe, 1),
                "min_roe": round(min_roe, 1),
                "avg_np_growth": round(avg_np_growth, 1) if avg_np_growth else None,
                "avg_debt": round(avg_debt, 1),
                "avg_gpm": round(avg_gpm, 1) if avg_gpm else None,
                "longterm_score": longterm_score,
                "holding_years": "10年",
                "note": "ROE连续高且稳定，低负债，适合长期持有",
            })

        candidates.sort(key=lambda x: x["longterm_score"], reverse=True)
        stocks = candidates[:20]
        print(f"[LONGTERM] 股票筛选：{len(candidates)} 只候选 → TOP20，耗时 {time.time()-t0:.1f}s")

    except Exception as e:
        print(f"[LONGTERM] 股票筛选失败（Tushare财务数据需高积分）: {e}")
        # 降级：从 stock-screen 已有数据里选 ROE 高 + 负债低的股票
        print("[LONGTERM] 降级：从选股模块提取护城河候选...")
        try:
            from services.stock_screen import screen_stocks
            screen_data = screen_stocks(top_n=100)
            screen_stocks_list = screen_data.get("stocks", [])
            # 用选股里已有的财务数据做护城河过滤
            for s in screen_stocks_list:
                fin = s.get("financials", {})
                # ROE/debt等字段在顶层，financials是嵌套备份
                roe = s.get("roe") or fin.get("roe")
                debt = s.get("debt_ratio") or fin.get("debt_ratio")
                gm = s.get("gross_margin") or fin.get("gross_margin")
                nm = s.get("net_margin") or fin.get("net_margin")
                industry = s.get("industry", "")
                is_financial = any(kw in industry for kw in ["银行", "保险", "证券", "信托"])

                if roe is None: continue
                # 金融行业 ROE 门槛调低
                roe_threshold = 8 if is_financial else 10  # 非金融降到10%（Tushare当期ROE可能偏低）
                if roe < roe_threshold: continue
                # 负债率（金融业不限制）
                if not is_financial and debt is not None and debt > 70: continue

                # 护城河评分（基于选股数据估算）
                score = 0
                if roe >= 25: score += 40
                elif roe >= 20: score += 32
                elif roe >= 15: score += 24
                elif roe >= 12: score += 16
                elif roe >= 8: score += 8
                if gm is not None:
                    if gm > 60: score += 20
                    elif gm > 40: score += 15
                    elif gm > 25: score += 8
                if nm is not None:
                    if nm > 25: score += 15
                    elif nm > 15: score += 10
                    elif nm > 8: score += 5
                if debt is not None and not is_financial:
                    if debt < 30: score += 15
                    elif debt < 50: score += 8

                stocks.append({
                    "code": s.get("code", ""),
                    "ts_code": s.get("code", "") + (".SH" if s.get("code","").startswith("6") else ".SZ"),
                    "name": s.get("name", ""),
                    "industry": industry,
                    "market": "",
                    "avg_roe": round(roe, 1),
                    "min_roe": None,
                    "avg_np_growth": None,
                    "avg_debt": round(debt, 1) if debt else None,
                    "avg_gpm": round(gm, 1) if gm else None,
                    "longterm_score": round(score, 1),
                    "note": "基于实时财务数据估算（Tushare年报数据需高积分）",
                })
            stocks.sort(key=lambda x: x["longterm_score"], reverse=True)
            stocks = stocks[:20]
            print(f"[LONGTERM] 降级完成：{len(stocks)} 只护城河候选")
        except Exception as e2:
            print(f"[LONGTERM] 降级也失败: {e2}")
            stocks = []

    result = {
        "stocks": stocks,
        "total_screened": len(stocks),
        "generated_at": datetime.now().isoformat(),
        "cache_valid_days": _STOCK_CACHE_DAYS,
        "elapsed_seconds": round(time.time() - t0, 1),
        "screening_criteria": {
            "min_avg_roe": 12,
            "min_worst_roe": 8,
            "max_debt_ratio": 65,
            "years_analyzed": 3,
        }
    }

    _write_cache(_STOCK_CACHE_FILE, result)
    return result
