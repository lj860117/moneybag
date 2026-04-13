"""
钱袋子 — AI 多因子选股
7 维打分：价值/成长/质量/动量/风险/舆情/流动性
参考：豆包方案（7维打分 → TOP50-150）
数据源：通过 stock_data_provider 多源自动降级
"""
import time
import traceback
from config import STOCK_CACHE_TTL, STOCK_SCREEN_WEIGHTS

_stock_cache = {}


def screen_stocks(top_n: int = 50) -> dict:
    """
    多因子选股，返回 TOP N 股票
    当前使用规则打分，后续可接 LightGBM
    """
    cache_key = f"stock_screen_{top_n}"
    now = time.time()
    if cache_key in _stock_cache and now - _stock_cache[cache_key]["ts"] < STOCK_CACHE_TTL:
        return _stock_cache[cache_key]["data"]

    try:
        from services.stock_data_provider import get_stock_data

        # 通过数据层获取 A 股行情（自动降级多源）
        print("[STOCK_SCREEN] Loading via data provider...")
        data = get_stock_data()
        raw_stocks = data.get("stocks", [])
        source = data.get("source", "unknown")
        if not raw_stocks:
            return {"stocks": [], "total": 0, "error": data.get("error", "数据不可用")}

        print("[STOCK_SCREEN] Got {} stocks from source={}".format(len(raw_stocks), source))

        candidates = []
        for s in raw_stocks:
            try:
                code = s.get("code", "")
                name = s.get("name", "")
                price = s.get("price")
                pe = s.get("pe")
                pb = s.get("pb")
                change_pct = s.get("change_pct")
                turnover = s.get("turnover")
                volume = s.get("volume")
                market_cap_yi = s.get("market_cap")  # 已是亿元
                change_5d = s.get("change_5d")
                change_20d = s.get("change_20d")
                change_60d = s.get("change_60d")
                amp = s.get("amplitude")

                # 过滤条件
                if not code or not name or price is None or price <= 0:
                    continue
                if "ST" in name:
                    continue  # 排除ST
                if pe is not None and (pe <= 0 or pe > 300):
                    continue  # 排除负PE和极端PE
                if market_cap_yi is not None and market_cap_yi < 50:
                    continue  # 排除市值<50亿
                if turnover is not None and turnover < 0.5:
                    continue  # 排除流动性极差的
                # 注意：雪球接口不稳定，PE 可能为 None
                # 此时用其他因子（动量/流动性/风险）继续选股，不强制跳过

                # --- 7 维打分（每维 0-100）---
                scores = {}

                # 1. 价值（PE + PB，越低越好）
                val_score = 0
                if pe is not None:
                    if pe < 10:
                        val_score += 50
                    elif pe < 20:
                        val_score += 40
                    elif pe < 30:
                        val_score += 25
                    elif pe < 50:
                        val_score += 10
                if pb is not None:
                    if pb < 1:
                        val_score += 50
                    elif pb < 2:
                        val_score += 35
                    elif pb < 3:
                        val_score += 20
                    elif pb < 5:
                        val_score += 10
                scores["value"] = min(val_score, 100)

                # 2. 动量（近期涨跌幅，适度上涨好）
                mom_score = 50  # 中性起步
                if change_5d is not None:
                    if 0 < change_5d < 5:
                        mom_score += 15
                    elif change_5d >= 5:
                        mom_score += 5  # 短期涨太多减分
                    elif -5 < change_5d < 0:
                        mom_score += 5
                    else:
                        mom_score -= 10
                if change_60d is not None:
                    if 5 < change_60d < 30:
                        mom_score += 20
                    elif change_60d >= 30:
                        mom_score += 5
                    elif -10 < change_60d < 5:
                        mom_score += 10
                    else:
                        mom_score -= 15
                scores["momentum"] = max(0, min(mom_score, 100))

                # 3. 流动性（换手率适中好、成交量大好）
                liq_score = 50
                if turnover is not None:
                    if 1 < turnover < 5:
                        liq_score += 30  # 适中换手最好
                    elif 0.5 < turnover <= 1:
                        liq_score += 15
                    elif turnover >= 5:
                        liq_score += 10  # 换手太高可能有炒作
                if market_cap_yi is not None:
                    if market_cap_yi > 1000:
                        liq_score += 20  # 大盘股(>1000亿)流动性好
                    elif market_cap_yi > 500:
                        liq_score += 15
                    elif market_cap_yi > 200:
                        liq_score += 10
                scores["liquidity"] = max(0, min(liq_score, 100))

                # 4. 风险（振幅低好、回撤小好）
                risk_score = 70  # 起步偏正面
                if amp is not None:
                    if amp < 2:
                        risk_score += 20
                    elif amp < 5:
                        risk_score += 10
                    elif amp > 10:
                        risk_score -= 30
                    elif amp > 7:
                        risk_score -= 15
                scores["risk"] = max(0, min(risk_score, 100))

                # 5. 成长（暂用动量+PE组合替代，因为没有ROE/EPS增速）
                growth_score = 50
                if pe is not None and pe < 25 and change_60d is not None and change_60d > 0:
                    growth_score += 25  # 低PE + 上涨 = 成长潜力
                if change_20d is not None and change_20d > 0:
                    growth_score += 15
                scores["growth"] = max(0, min(growth_score, 100))

                # 6. 质量（暂用PE+PB+市值组合替代）
                quality_score = 50
                if pe is not None and 5 < pe < 30:
                    quality_score += 15  # PE在合理范围
                if pb is not None and 0.5 < pb < 5:
                    quality_score += 15
                if market_cap_yi is not None and market_cap_yi > 500:
                    quality_score += 20  # 大市值(>500亿)通常质量更好
                scores["quality"] = max(0, min(quality_score, 100))

                # 7. 舆情（暂无数据，给中性分）
                scores["sentiment"] = 50

                # 加权总分
                total_score = sum(scores[k] * STOCK_SCREEN_WEIGHTS[k] for k in STOCK_SCREEN_WEIGHTS)

                candidates.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "pe": pe,
                    "pb": pb,
                    "change_pct": change_pct,
                    "turnover": turnover,
                    "market_cap": market_cap_yi,
                    "score": round(total_score, 1),
                    "scores": {k: round(v, 0) for k, v in scores.items()},
                })
            except Exception:
                continue

        # 排序取 TOP N
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:top_n]
        result = {
            "stocks": top,
            "total": len(candidates),
            "source": source,
            "method": "7维多因子规则打分（价值20%+成长15%+质量15%+动量15%+风险15%+流动性10%+舆情10%）",
            "note": "数据源: {}".format(source),
        }
        _stock_cache[cache_key] = {"data": result, "ts": time.time()}
        print(f"[STOCK_SCREEN] Screened {len(candidates)} → TOP {len(top)}")
        return result

    except Exception as e:
        print(f"[STOCK_SCREEN] Failed: {e}")
        traceback.print_exc()
        return {"stocks": [], "total": 0, "error": str(e)}

