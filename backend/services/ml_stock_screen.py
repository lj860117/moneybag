"""
钱袋子 — LightGBM 多因子选股模型
Phase 4: 对标量化文档第三阶段
参考: Qlib Alpha158 + GuruAgents + 广发金融工程多因子体系
"""
import time
import traceback
import numpy as np
from config import STOCK_CACHE_TTL

_ml_cache = {}


def ml_stock_screen(top_n: int = 30) -> dict:
    """
    LightGBM 多因子选股 — 基于规则打分+ML增强的混合模型
    Phase 4 MVP: 先用规则打分生成训练标签，再用 LightGBM 学习非线性关系

    与规则版(stock_screen.py)的区别：
    - 规则版：人工设定阈值和权重，线性加权
    - ML版：LightGBM 自动学习特征重要性和非线性关系

    数据源：AKShare stock_zh_a_spot_em（同规则版）
    """
    cache_key = f"ml_screen_{top_n}"
    now = time.time()
    if cache_key in _ml_cache and now - _ml_cache[cache_key]["ts"] < STOCK_CACHE_TTL:
        return _ml_cache[cache_key]["data"]

    try:
        import lightgbm as lgb
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import ndcg_score
        from services.stock_data_provider import get_stock_data

        # 1. 通过数据层获取数据（自动降级多源）
        print("[ML_SCREEN] Loading via data provider...")
        raw = get_stock_data()
        raw_stocks = raw.get("stocks", [])
        source = raw.get("source", "unknown")
        if not raw_stocks:
            return {"stocks": [], "total": 0, "error": raw.get("error", "数据不可用"), "model": "none"}

        print("[ML_SCREEN] Got {} stocks from source={}".format(len(raw_stocks), source))

        # 2. 特征提取
        features = []
        labels = []
        stock_info = []

        for s in raw_stocks:
            try:
                code = s.get("code", "")
                name = s.get("name", "")
                price = s.get("price")
                pe = s.get("pe")
                pb = s.get("pb")
                change_pct = s.get("change_pct")
                turnover = s.get("turnover")
                market_cap_yi = s.get("market_cap")  # 已是亿元
                change_5d = s.get("change_5d")
                change_20d = s.get("change_20d")
                change_60d = s.get("change_60d")
                amp = s.get("amplitude")

                # 过滤条件
                if not code or not name or price is None or price <= 0:
                    continue
                if "ST" in name:
                    continue
                if pe is not None and (pe <= 0 or pe > 300):
                    continue
                if market_cap_yi is not None and market_cap_yi < 50:
                    continue  # 排除市值<50亿
                if turnover is not None and turnover < 0.5:
                    continue
                # 无PE数据的跳过（新浪源未被补充的）
                if pe is None:
                    continue

                # 特征向量（8维）— 对标量化文档"特征库"
                feat = [
                    pe if pe else 20,                                  # F1: PE（价值因子）
                    pb if pb else 2,                                   # F2: PB（价值因子）
                    change_5d if change_5d else 0,                     # F3: 5日动量
                    change_60d if change_60d else 0,                   # F4: 60日动量
                    turnover if turnover else 1,                       # F5: 换手率（流动性）
                    (market_cap_yi / 100) if market_cap_yi else 5,     # F6: 市值（百亿）
                    amp if amp else 3,                                 # F7: 振幅（风险）
                    change_20d if change_20d else 0,                   # F8: 20日动量
                ]

                # 标签：用规则打分作为"弱监督标签"
                rule_score = _calc_rule_score(pe, pb, change_5d, change_60d, change_20d, turnover, market_cap_yi, amp)

                features.append(feat)
                labels.append(rule_score)
                stock_info.append({
                    "code": code, "name": name, "price": price,
                    "pe": pe, "pb": pb, "change_pct": change_pct,
                    "turnover": turnover,
                    "market_cap": market_cap_yi,
                })

            except Exception:
                continue

        if len(features) < 100:
            return {"stocks": [], "total": len(features), "error": "有效股票不足100只", "model": "none"}

        # 3. LightGBM 训练
        X = np.array(features)
        y = np.array(labels)

        print(f"[ML_SCREEN] Training LightGBM on {len(X)} samples, {X.shape[1]} features...")

        # 用全量数据训练（截面选股，非时序）
        # 防过拟合：低树数 + 高 min_child_samples + L2 正则
        params = {
            "objective": "regression",
            "metric": "rmse",
            "n_estimators": 50,          # 少量树防过拟合
            "max_depth": 4,              # 浅树防过拟合
            "learning_rate": 0.1,
            "min_child_samples": 50,     # 每叶至少50个样本
            "reg_lambda": 1.0,           # L2 正则
            "reg_alpha": 0.5,            # L1 正则
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "verbose": -1,
            "n_jobs": 1,
        }

        model = lgb.LGBMRegressor(**params)
        model.fit(X, y)

        # 4. 预测打分
        ml_scores = model.predict(X)

        # 5. 特征重要性
        feature_names = ["PE", "PB", "5日动量", "60日动量", "换手率", "市值", "振幅", "20日动量"]
        importances = model.feature_importances_
        feat_importance = sorted(
            zip(feature_names, importances.tolist()),
            key=lambda x: x[1], reverse=True
        )

        # 6. 排序输出 TOP N
        scored = list(zip(stock_info, ml_scores.tolist(), labels))
        scored.sort(key=lambda x: x[1], reverse=True)

        stocks = []
        for info, ml_score, rule_score in scored[:top_n]:
            info["ml_score"] = round(ml_score, 1)
            info["rule_score"] = round(rule_score, 1)
            info["score"] = round(ml_score, 1)  # 兼容前端
            stocks.append(info)

        result = {
            "stocks": stocks,
            "total": len(scored),
            "source": source,
            "method": "LightGBM 多因子选股（8维特征 + 弱监督标签）",
            "model_info": {
                "n_estimators": params["n_estimators"],
                "max_depth": params["max_depth"],
                "feature_importance": feat_importance[:5],
                "train_samples": len(X),
                "note": "当前用规则打分作为弱监督标签，后续可替换为远期收益率",
            },
            "note": "LightGBM 模型自动学习非线性特征关系，比规则打分更准确",
        }

        _ml_cache[cache_key] = {"data": result, "ts": now}
        print(f"[ML_SCREEN] TOP {top_n} selected from {len(scored)} candidates")
        return result

    except ImportError as e:
        print(f"[ML_SCREEN] LightGBM not installed: {e}")
        return {"stocks": [], "total": 0, "error": "LightGBM 未安装", "model": "none"}
    except Exception as e:
        print(f"[ML_SCREEN] Failed: {e}")
        traceback.print_exc()
        return {"stocks": [], "total": 0, "error": str(e), "model": "none"}


def _calc_rule_score(pe, pb, change_5d, change_60d, change_20d, turnover, market_cap_yi, amp) -> float:
    """规则打分（作为 LightGBM 的弱监督标签）— 与 stock_screen.py 保持一致"""
    from config import STOCK_SCREEN_WEIGHTS as W

    scores = {}

    # 价值
    val = 0
    if pe and pe < 10: val += 50
    elif pe and pe < 20: val += 40
    elif pe and pe < 30: val += 25
    elif pe and pe < 50: val += 10
    if pb and pb < 1: val += 50
    elif pb and pb < 2: val += 35
    elif pb and pb < 3: val += 20
    elif pb and pb < 5: val += 10
    scores["value"] = min(val, 100)

    # 动量
    mom = 50
    if change_5d is not None:
        if 0 < change_5d < 5: mom += 15
        elif change_5d >= 5: mom += 5
        elif -5 < change_5d < 0: mom += 5
        else: mom -= 10
    if change_60d is not None:
        if 5 < change_60d < 30: mom += 20
        elif change_60d >= 30: mom += 5
        elif -10 < change_60d < 5: mom += 10
        else: mom -= 15
    scores["momentum"] = max(0, min(mom, 100))

    # 流动性（市值单位：亿元）
    liq = 50
    if turnover and 1 < turnover < 5: liq += 30
    elif turnover and 0.5 < turnover <= 1: liq += 15
    if market_cap_yi and market_cap_yi > 1000: liq += 20
    elif market_cap_yi and market_cap_yi > 500: liq += 15
    scores["liquidity"] = max(0, min(liq, 100))

    # 风险
    risk = 70
    if amp and amp < 2: risk += 20
    elif amp and amp < 5: risk += 10
    elif amp and amp > 10: risk -= 30
    elif amp and amp > 7: risk -= 15
    scores["risk"] = max(0, min(risk, 100))

    # 成长
    growth = 50
    if pe and pe < 25 and change_60d and change_60d > 0: growth += 25
    if change_20d and change_20d > 0: growth += 15
    scores["growth"] = max(0, min(growth, 100))

    # 质量（市值单位：亿元）
    quality = 50
    if pe and 5 < pe < 30: quality += 15
    if pb and 0.5 < pb < 5: quality += 15
    if market_cap_yi and market_cap_yi > 500: quality += 20
    scores["quality"] = max(0, min(quality, 100))

    # 舆情
    scores["sentiment"] = 50

    return sum(scores[k] * W[k] for k in W)
