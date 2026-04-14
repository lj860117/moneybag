"""
钱袋子 — AI 预测引擎 V1
轻量 MLP/梯度提升 混合预测模型

架构：
  1. 特征工程：从历史价量数据提取 ~40 个技术/基本面/动量特征
  2. 双模型集成：MLPRegressor（非线性） + GradientBoosting（树模型）
  3. 滑动窗口训练：用最近 500 天数据训练，预测未来 5 天涨跌概率
  4. 自带特征重要性排名 + 预测置信度

设计决策：
  - 不用 PyTorch/TensorFlow（服务器 2C2G 跑不动）
  - 用 scikit-learn MLPRegressor 替代 LSTM（同为神经网络，轻量级）
  - GBM 作为对照/集成模型
  - 纯 CPU 推理，单次预测 < 3 秒

参考：
  - 微软 Qlib Alpha158 特征集
  - 幻方量化因子体系（简化版）
  - "Empirical Asset Pricing via Machine Learning" (Gu, Kelly, Xiu, 2020)
"""
import time
import math
import traceback
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import STOCK_CACHE_TTL

_pred_cache = {}
_PRED_CACHE_TTL = 3600  # 1 小时


# ============================================================
# 特征工程
# ============================================================

def _extract_features(prices: list, volumes: list = None) -> list:
    """
    从价格序列提取 ~40 个特征向量序列
    每个交易日 → 一行特征
    返回 (features_2d, feature_names)
    """
    n = len(prices)
    if n < 60:
        return [], []

    prices_arr = np.array(prices, dtype=np.float64)
    returns = np.diff(np.log(prices_arr))  # 对数收益率

    vol_arr = np.array(volumes, dtype=np.float64) if volumes and len(volumes) == n else None

    feature_names = []
    all_features = {}

    # ---- 收益率特征 ----
    for w in [5, 10, 20, 60]:
        name = f"ret_{w}d"
        feat = np.full(n, np.nan)
        for i in range(w, n):
            feat[i] = (prices_arr[i] / prices_arr[i - w]) - 1.0
        all_features[name] = feat
        feature_names.append(name)

    # ---- 波动率特征 ----
    for w in [5, 10, 20, 60]:
        name = f"vol_{w}d"
        feat = np.full(n, np.nan)
        for i in range(w, n):
            window_ret = returns[i - w:i]
            feat[i] = np.std(window_ret) * math.sqrt(252) if len(window_ret) > 1 else 0
        all_features[name] = feat
        feature_names.append(name)

    # ---- 均线偏离度 ----
    for w in [5, 10, 20, 60]:
        name = f"ma_bias_{w}d"
        feat = np.full(n, np.nan)
        for i in range(w, n):
            ma = np.mean(prices_arr[i - w + 1:i + 1])
            feat[i] = (prices_arr[i] / ma - 1.0) if ma > 0 else 0
        all_features[name] = feat
        feature_names.append(name)

    # ---- RSI ----
    for w in [6, 14]:
        name = f"rsi_{w}"
        feat = np.full(n, np.nan)
        for i in range(w + 1, n):
            changes = np.diff(prices_arr[i - w:i + 1])
            gains = changes[changes > 0]
            losses = -changes[changes < 0]
            avg_gain = np.mean(gains) if len(gains) > 0 else 0
            avg_loss = np.mean(losses) if len(losses) > 0 else 1e-9
            rs = avg_gain / avg_loss
            feat[i] = 100 - (100 / (1 + rs))
        all_features[name] = feat
        feature_names.append(name)

    # ---- MACD 相关 ----
    def _ema(arr, span):
        """指数移动平均"""
        alpha = 2 / (span + 1)
        out = np.full(len(arr), np.nan)
        out[0] = arr[0]
        for i in range(1, len(arr)):
            if np.isnan(out[i - 1]):
                out[i] = arr[i]
            else:
                out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
        return out

    ema12 = _ema(prices_arr, 12)
    ema26 = _ema(prices_arr, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    macd_hist = 2 * (dif - dea)

    all_features["macd_dif"] = dif
    all_features["macd_dea"] = dea
    all_features["macd_hist"] = macd_hist
    feature_names.extend(["macd_dif", "macd_dea", "macd_hist"])

    # ---- 布林带位置 ----
    for w in [20]:
        name = f"boll_pos_{w}"
        feat = np.full(n, np.nan)
        for i in range(w, n):
            window = prices_arr[i - w + 1:i + 1]
            ma = np.mean(window)
            std = np.std(window)
            if std > 0:
                feat[i] = (prices_arr[i] - ma) / (2 * std)  # -1 ~ +1
            else:
                feat[i] = 0
        all_features[name] = feat
        feature_names.append(name)

    # ---- 成交量特征 ----
    if vol_arr is not None:
        for w in [5, 10, 20]:
            name = f"vol_ratio_{w}d"
            feat = np.full(n, np.nan)
            for i in range(w, n):
                avg_vol = np.mean(vol_arr[i - w + 1:i + 1])
                feat[i] = (vol_arr[i] / avg_vol - 1.0) if avg_vol > 0 else 0
            all_features[name] = feat
            feature_names.append(name)

        # 量价相关性
        name = "vol_price_corr_20d"
        feat = np.full(n, np.nan)
        for i in range(20, n):
            p_win = prices_arr[i - 19:i + 1]
            v_win = vol_arr[i - 19:i + 1]
            if np.std(p_win) > 0 and np.std(v_win) > 0:
                feat[i] = np.corrcoef(p_win, v_win)[0, 1]
            else:
                feat[i] = 0
        all_features[name] = feat
        feature_names.append(name)

    # ---- 动量/反转特征 ----
    # 加速度（收益率变化率）
    name = "ret_accel_10d"
    feat = np.full(n, np.nan)
    for i in range(20, n):
        r1 = (prices_arr[i] / prices_arr[i - 10]) - 1
        r2 = (prices_arr[i - 10] / prices_arr[i - 20]) - 1
        feat[i] = r1 - r2
    all_features[name] = feat
    feature_names.append(name)

    # 最高/最低价位置
    for w in [20, 60]:
        name = f"high_pos_{w}d"
        feat = np.full(n, np.nan)
        for i in range(w, n):
            window = prices_arr[i - w + 1:i + 1]
            hi = np.max(window)
            lo = np.min(window)
            feat[i] = (prices_arr[i] - lo) / (hi - lo) if hi > lo else 0.5
        all_features[name] = feat
        feature_names.append(name)

    # ---- 偏度/峰度 ----
    name = "skew_20d"
    feat = np.full(n, np.nan)
    for i in range(20, n):
        win = returns[i - 20:i]
        m = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            feat[i] = np.mean(((win - m) / s) ** 3)
        else:
            feat[i] = 0
    all_features[name] = feat
    feature_names.append(name)

    name = "kurt_20d"
    feat = np.full(n, np.nan)
    for i in range(20, n):
        win = returns[i - 20:i]
        m = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            feat[i] = np.mean(((win - m) / s) ** 4) - 3  # 超额峰度
        else:
            feat[i] = 0
    all_features[name] = feat
    feature_names.append(name)

    # ---- 组装为矩阵 ----
    start_idx = 60  # 前60天数据不完整，跳过
    rows = []
    for i in range(start_idx, n):
        row = []
        valid = True
        for fname in feature_names:
            v = all_features[fname][i]
            if np.isnan(v) or np.isinf(v):
                valid = False
                break
            row.append(float(v))
        if valid:
            rows.append(row)

    return rows, feature_names


def _make_labels(prices: list, start_idx: int, forward_days: int = 5) -> list:
    """生成标签：未来 forward_days 天的收益率"""
    prices_arr = np.array(prices, dtype=np.float64)
    labels = []
    for i in range(start_idx, len(prices_arr)):
        if i + forward_days < len(prices_arr):
            fwd_ret = (prices_arr[i + forward_days] / prices_arr[i]) - 1.0
            labels.append(float(fwd_ret))
        else:
            labels.append(None)
    return labels


# ============================================================
# 模型训练 + 预测
# ============================================================

def _train_and_predict(features: list, labels: list, feature_names: list) -> dict:
    """
    双模型集成：MLP + GradientBoosting
    返回预测结果 + 特征重要性 + 置信度
    """
    from sklearn.neural_network import MLPRegressor
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import TimeSeriesSplit

    # 过滤无效标签
    valid_idx = [i for i, l in enumerate(labels) if l is not None]
    if len(valid_idx) < 100:
        return {"error": "数据不足，至少需要100个有效样本"}

    X = np.array([features[i] for i in valid_idx])
    y = np.array([labels[i] for i in valid_idx])

    # 时序分割：80% 训练 / 20% 测试
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # 标准化
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    # ---- 模型 1: MLP（轻量神经网络）----
    mlp = MLPRegressor(
        hidden_layer_sizes=(64, 32, 16),
        activation="relu",
        solver="adam",
        max_iter=300,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=42,
        learning_rate_init=0.001,
    )
    mlp.fit(X_train_s, y_train)
    mlp_pred = mlp.predict(X_test_s)
    mlp_latest = float(mlp.predict(scaler.transform(X[-1:]))[0])

    # ---- 模型 2: GradientBoosting（树模型）----
    gbm = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
    )
    gbm.fit(X_train, y_train)
    gbm_pred = gbm.predict(X_test)
    gbm_latest = float(gbm.predict(X[-1:])[0])

    # ---- 集成：简单加权平均 ----
    # 根据测试集表现动态调权
    mlp_mse = float(np.mean((mlp_pred - y_test) ** 2))
    gbm_mse = float(np.mean((gbm_pred - y_test) ** 2))
    total_inv_mse = (1 / (mlp_mse + 1e-10)) + (1 / (gbm_mse + 1e-10))
    mlp_weight = (1 / (mlp_mse + 1e-10)) / total_inv_mse
    gbm_weight = (1 / (gbm_mse + 1e-10)) / total_inv_mse

    ensemble_pred = mlp_weight * mlp_latest + gbm_weight * gbm_latest

    # ---- 特征重要性（来自 GBM）----
    importances = gbm.feature_importances_
    feat_imp = sorted(
        [(feature_names[i], float(importances[i])) for i in range(len(feature_names))],
        key=lambda x: -x[1]
    )

    # ---- 测试集统计 ----
    ensemble_test = mlp_weight * mlp_pred + gbm_weight * gbm_pred
    # 方向准确率
    correct_dir = sum(
        1 for p, a in zip(ensemble_test, y_test)
        if (p > 0 and a > 0) or (p < 0 and a < 0) or (p == 0 and a == 0)
    )
    direction_accuracy = correct_dir / len(y_test) if len(y_test) > 0 else 0

    # 相关系数
    if np.std(ensemble_test) > 0 and np.std(y_test) > 0:
        correlation = float(np.corrcoef(ensemble_test, y_test)[0, 1])
    else:
        correlation = 0

    # 置信度：基于预测分布
    all_latest_X = scaler.transform(X[-20:]) if len(X) >= 20 else scaler.transform(X[-len(X):])
    mlp_recent = mlp.predict(all_latest_X)
    gbm_recent = gbm.predict(X[-20:] if len(X) >= 20 else X[-len(X):])
    recent_ensemble = mlp_weight * mlp_recent + gbm_weight * gbm_recent
    pred_std = float(np.std(recent_ensemble))
    confidence = max(0, min(1, 1 - pred_std / (abs(ensemble_pred) + 1e-6)))

    return {
        "prediction": round(float(ensemble_pred) * 100, 2),  # 百分比
        "direction": "看涨" if ensemble_pred > 0.005 else ("看跌" if ensemble_pred < -0.005 else "中性"),
        "confidence": round(float(confidence) * 100, 1),
        "models": {
            "mlp": {
                "prediction": round(mlp_latest * 100, 2),
                "weight": round(mlp_weight * 100, 1),
                "mse": round(mlp_mse * 10000, 4),
            },
            "gbm": {
                "prediction": round(gbm_latest * 100, 2),
                "weight": round(gbm_weight * 100, 1),
                "mse": round(gbm_mse * 10000, 4),
            },
        },
        "backtest": {
            "direction_accuracy": round(direction_accuracy * 100, 1),
            "correlation": round(correlation, 4),
            "test_samples": len(y_test),
            "train_samples": len(y_train),
        },
        "feature_importance": feat_imp[:15],  # Top 15
        "total_features": len(feature_names),
    }


# ============================================================
# 数据获取
# ============================================================

def _get_price_volume(code: str, days: int = 800) -> tuple:
    """获取价格和成交量序列"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if df is not None and len(df) > 0:
            df = df.tail(days)
            prices = df["收盘"].tolist()
            volumes = df["成交量"].tolist() if "成交量" in df.columns else None
            return prices, volumes
    except Exception:
        pass

    # Tushare 降级
    try:
        from services.tushare_data import get_tushare_client
        ts = get_tushare_client()
        if ts:
            ts_code = code + ".SZ" if code.startswith(("0", "3")) else code + ".SH"
            df = ts.daily(ts_code=ts_code, fields="close,vol")
            if df is not None and len(df) > 0:
                df = df.sort_values("trade_date").tail(days)
                return df["close"].tolist(), df["vol"].tolist()
    except Exception:
        pass

    return [], None


# ============================================================
# 公开 API
# ============================================================

def predict_stock(code: str, forward_days: int = 5) -> dict:
    """
    预测单只股票未来 N 天涨跌概率
    输入: 股票代码 (如 "000001")
    输出: 预测收益率 + 方向 + 置信度 + 特征重要性
    """
    cache_key = f"pred_{code}_{forward_days}"
    now = time.time()
    if cache_key in _pred_cache and now - _pred_cache[cache_key]["ts"] < _PRED_CACHE_TTL:
        return _pred_cache[cache_key]["data"]

    try:
        prices, volumes = _get_price_volume(code)
        if len(prices) < 200:
            return {"error": f"数据不足：需要200+天，只有{len(prices)}天"}

        features, feature_names = _extract_features(prices, volumes)
        if len(features) < 100:
            return {"error": f"特征提取后样本不足：{len(features)}"}

        labels = _make_labels(prices, start_idx=60, forward_days=forward_days)

        # 对齐
        min_len = min(len(features), len(labels))
        features = features[:min_len]
        labels = labels[:min_len]

        result = _train_and_predict(features, labels, feature_names)
        result["code"] = code
        result["forward_days"] = forward_days
        result["data_points"] = len(prices)
        result["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        _pred_cache[cache_key] = {"data": result, "ts": now}
        return result

    except Exception as e:
        traceback.print_exc()
        return {"error": f"预测失败: {str(e)}"}


def predict_portfolio(user_id: str, forward_days: int = 5) -> dict:
    """
    预测用户持仓组合的未来 N 天表现
    对每只持仓分别预测，按权重加权得到组合预测
    """
    cache_key = f"pred_portfolio_{user_id}_{forward_days}"
    now = time.time()
    if cache_key in _pred_cache and now - _pred_cache[cache_key]["ts"] < _PRED_CACHE_TTL:
        return _pred_cache[cache_key]["data"]

    try:
        from services.stock_monitor import get_stock_holdings
        holdings = get_stock_holdings(user_id)

        if not holdings or len(holdings) == 0:
            return {"error": "没有持仓数据"}

        # 计算权重
        total_value = sum(h.get("market_value", h.get("cost", 0)) for h in holdings)
        if total_value <= 0:
            return {"error": "持仓市值为0"}

        results = []
        weighted_pred = 0.0
        errors = []

        def _predict_one(h):
            code = h.get("code", "")
            weight = h.get("market_value", h.get("cost", 0)) / total_value
            return code, weight, predict_stock(code, forward_days)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_predict_one, h) for h in holdings[:10]]  # 最多10只
            for f in as_completed(futures):
                code, weight, pred = f.result()
                if "error" in pred:
                    errors.append({"code": code, "error": pred["error"]})
                else:
                    weighted_pred += weight * pred["prediction"] / 100.0
                    results.append({
                        "code": code,
                        "name": next((h.get("name", "") for h in holdings if h.get("code") == code), ""),
                        "weight": round(weight * 100, 1),
                        "prediction": pred["prediction"],
                        "direction": pred["direction"],
                        "confidence": pred["confidence"],
                    })

        # 按预测收益排序
        results.sort(key=lambda x: -x["prediction"])

        portfolio_result = {
            "portfolio_prediction": round(weighted_pred * 100, 2),
            "portfolio_direction": "看涨" if weighted_pred > 0.005 else ("看跌" if weighted_pred < -0.005 else "中性"),
            "stock_count": len(results),
            "stocks": results,
            "errors": errors,
            "forward_days": forward_days,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        _pred_cache[cache_key] = {"data": portfolio_result, "ts": now}
        return portfolio_result

    except Exception as e:
        traceback.print_exc()
        return {"error": f"组合预测失败: {str(e)}"}


def batch_predict(codes: list, forward_days: int = 5) -> dict:
    """批量预测多只股票"""
    results = []
    errors = []

    def _do_one(code):
        return code, predict_stock(code, forward_days)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_do_one, c) for c in codes[:20]]  # 最多20只
        for f in as_completed(futures):
            code, pred = f.result()
            if "error" in pred:
                errors.append({"code": code, "error": pred["error"]})
            else:
                results.append(pred)

    results.sort(key=lambda x: -x["prediction"])

    return {
        "predictions": results,
        "errors": errors,
        "count": len(results),
        "forward_days": forward_days,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
