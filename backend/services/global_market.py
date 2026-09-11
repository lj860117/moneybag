"""
钱袋子 — 全球市场数据层
职责：
  1. 美股指数（道琼斯/标普/纳斯达克）历史+最新
  2. 外汇（美元/人民币、美元指数）
  3. 美联储利率（联邦基金利率历史）
  4. 全球市场估值（美国 PE）
  5. 国际商品（黄金/原油国际价）
  6. 全球→A股影响分析（DeepSeek 联动）

数据源：AKShare（腾讯云实测可用的接口）
缓存：1 小时（全球数据更新频率低于 A 股）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "global_market",
    "scope": "public",
    "input": [],
    "output": "global_snapshot",
    "cost": "cpu",
    "tags": ['全球', '美股', '外汇', '美联储'],
    "description": "全球市场：美股三大指数+外汇+美联储利率+全球PE+影响分析",
    "layer": "data",
    "priority": 2,
}
import os
import re
import time
import json
import math
import traceback
from datetime import datetime, timedelta
from infra.cache import MemoryCache

_global_cache = MemoryCache(default_ttl=3600)
_GLOBAL_TTL = 3600  # 1 小时缓存（成功路径）
# 降级/失败结果的缓存时长。远短于成功路径，目的是让主源一恢复就能自愈，
# 同时**仍保留兜底**（不是「失败不缓存」——那会在上游故障时放大请求压力）。
# 只用于外汇，见 get_forex_data() 末尾。
_GLOBAL_TTL_DEGRADED = 300

# 综合快照（get_global_snapshot）的缓存时长。
# 为什么是 300 而不是更大：快照里含外汇，而外汇的降级结果按
# _GLOBAL_TTL_DEGRADED=300s 缓存（见 get_forex_data 末尾）。若快照 TTL 更长，
# 外汇主源恢复后消费方读到的仍是快照里的旧值，P3-6 做的「降级缩短 TTL 好自愈」
# 会被快照层再压一层、效果打折。对齐成 300，自愈链路才完整。
#
# 为什么降 TTL 不会打爆上游（这点务必保留，否则后人看到 300 会以为要限流）：
# 快照本身不取数，只是把四个子函数的结果组装起来，而四个子函数**各自有自己的
# 缓存**：get_us_indices=3600 / get_forex_data=3600(正常)或300(降级) /
# get_fed_rate=default 3600 / get_global_pe=default 3600。所以快照 TTL 从 600
# 降到 300，多出来的开销只是每 5 分钟重新组装一次字典，绝大多数子调用直接命中
# 自己的缓存，不会新增上游请求。
_SNAPSHOT_TTL = 300


def _safe_num(v, default=0):
    """安全转数字，处理 NaN/Inf"""
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


# ============================================================
# 1. 美股指数
# ============================================================

def get_us_indices() -> dict:
    """获取美股三大指数最新数据（道琼斯/标普500/纳斯达克）

    策略：Tushare 主 + AKShare 降级
    """
    cache_key = "us_indices"
    now = time.time()
    cached = _global_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"dji": None, "spx": None, "ixic": None, "available": False}

    # 策略：Tushare 主
    try:
        from services.tushare_fallback import TusharePrimary
        tp = TusharePrimary.instance()
        us_indices = tp.get_us_indices()
        if us_indices:
            for item in us_indices:
                key = item["key"]
                result[key] = {
                    "close": item["close"],
                    "change_pct": item["change_pct"],
                    "date": item["date"],
                    "trend": item["trend"],
                    "source": "tushare",
                }
            result["available"] = True
            print(f"[GLOBAL] US indices from Tushare: {len(us_indices)} indices")
    except Exception as e:
        print(f"[GLOBAL] US indices Tushare failed: {e}")

    # 降级：AKShare
    if not result["available"]:
        try:
            from infra.data_source.macro.indicators import get_us_index

            indices = {
                "dji": ".DJI",   # 道琼斯
                "spx": ".INX",   # 标普500
                "ixic": ".IXIC", # 纳斯达克
            }

            for key, symbol in indices.items():
                try:
                    df = get_us_index(symbol=symbol)
                    if df is not None and len(df) > 0:
                        last = df.iloc[-1]
                        prev = df.iloc[-2] if len(df) > 1 else last
                        close = _safe_num(last.iloc[1]) if len(last) > 1 else 0
                        prev_close = _safe_num(prev.iloc[1]) if len(prev) > 1 else close
                        change_pct = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0
                        result[key] = {
                            "close": round(close, 2),
                            "change_pct": round(change_pct, 2),
                            "date": str(last.iloc[0]) if len(last) > 0 else "",
                            "trend": "up" if change_pct > 0 else "down" if change_pct < 0 else "flat",
                            "source": "akshare",
                        }
                except Exception as e:
                    print(f"[GLOBAL] {key} AKShare failed: {e}")

            result["available"] = any(result[k] is not None for k in ["dji", "spx", "ixic"])
            if result["available"]:
                print(f"[GLOBAL] US indices from AKShare (Tushare unavailable)")
        except Exception as e:
            print(f"[GLOBAL] US indices AKShare failed: {e}")

    _global_cache.set(cache_key, result, ttl=_GLOBAL_TTL)
    return result


# ============================================================
# 2. 外汇数据
# ============================================================

def _normalize_fx_pair(name) -> str:
    """把货币对名称归一化为大写紧凑代码：'USD/CNY' / 'USDCNY' / '美元人民币' → 'USDCNY'。

    AKShare fx_spot_quote 的「货币对」列历史上返回中文名（如 '美元/人民币'），
    现版本返回 ISO 代码（'USD/CNY'）。FIX 2026-09-11 之前这里用
    `"美元" in name and "人民币" in name` 匹配，而实际数据全是 ISO 代码，
    25 行命中 0 行 → usdcny 恒为 None → 外汇 100% 缺失且无告警。
    这里同时兼容两种形态，避免同类静默失败复发。

    Returns:
        归一化后的代码（如 'USDCNY'），无法识别时返回空字符串。
    """
    raw = str(name if name is not None else "").strip().upper()
    if not raw:
        return ""
    # 中文形态优先：美元/人民币、美元人民币、美元兑人民币 等
    if "美元" in raw and "人民币" in raw:
        return "USDCNY"
    # ISO 形态：去掉所有非字母数字（'USD/CNY' → 'USDCNY'）
    return re.sub(r"[^A-Z0-9]", "", raw)


def _parse_fx_frame(df) -> dict:
    """解析 AKShare 外汇表 → {归一化代码: 买卖中值}。列名不敏感。

    约定：第 1 列为货币对名称，第 2、3 列为买报价/卖报价，取二者均值作中值。

    v9.9.19 P3-5：这里原先写的是 `cells[1:]`（「能转成数字的列都算报价」），
    当时的正确性**完全依赖 akshare 的返回列数**。实测（任务 #7）
    `akshare/fx/fx_quote.py:42` 最后做了
    `temp_df = temp_df[["货币对", "买报价", "卖报价"]]`，
    所以帧固定 3 列、`cells[1:]` 恰好等于 [买报价, 卖报价]，均值即真实中值 ✅。
    但哪天它升版多返回一列（「昨收」「涨跌幅」之类），旧写法会把这些一起平均；
    而混合均值仍落在 `[4.0, 9.0]` 区间内，**能通过 `_sanitize_usdcny` 校验、
    不报错、日志照常打 `USDCNY=6.7xxx`** —— 典型的「没有症状」的静默失败。
    所以改成显式 `cells[1:3]`，把「只认买/卖两列」这件事写死。

    Returns:
        {pair_code: mid_rate}，解析不到任何有效行时返回空 dict。
    """
    out: dict = {}
    if df is None or len(df) == 0:
        return out
    try:
        rows = df.itertuples(index=False)
    except Exception:
        return out
    for row in rows:
        cells = list(row)
        if len(cells) < 2:
            continue
        pair = _normalize_fx_pair(cells[0])
        if not pair:
            continue
        rates = []
        # 显式只取买/卖报价两列（见上面 docstring）。不要改回 cells[1:]：
        # 那会让解析结果随 akshare 的列数悄悄变化，且变化后无症状。
        for cell in cells[1:3]:
            try:
                v = float(str(cell).replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                rates.append(v)
        if not rates:
            continue
        out[pair] = sum(rates) / len(rates)
    return out


# USD/CNY 合理性区间：历史大致 6.0~7.3，放宽到 [4.0, 9.0] 以容纳极端行情，
# 同时仍能拦住明显错误的值（如把分位/百分比/汇率小数位弄错的结果）。
_USDCNY_VALID_RANGE = (4.0, 9.0)

# 美元指数（DXY）合理性区间：真实值约 100，历史区间大致 70~165，
# 放宽到 [50, 200] 以容纳极端行情。关键是它能拦住**量纲错误**——
# 例如把 USDCNY≈6.7 当成美元指数返回（这正是 2026-09-11 返工的原因）。
_DXY_VALID_RANGE = (50.0, 200.0)


def _sanitize_usdcny(value) -> float | None:
    """USD/CNY 准入校验：落在 [4.0, 9.0] 才采纳，越界返回 None 并告警。

    兜底必须配检测：只判「非空」会让任何可疑数值一路静默变成对外数据，
    这正是 2026-09-11 这一整串故障的共同病根。
    """
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        print(f"[GLOBAL] usdcny 非法值（非数字）: {value!r}，置为 None")
        return None
    if not math.isfinite(val):
        print(f"[GLOBAL] usdcny 非法值（非有限数）: {val}，置为 None")
        return None
    low, high = _USDCNY_VALID_RANGE
    if not (low <= val <= high):
        print(f"[GLOBAL] ⚠️ usdcny={val} 超出合理区间 [{low}, {high}]，"
              f"疑似数据源异常，置为 None")
        return None
    return val


def _sanitize_dxy(value) -> float | None:
    """美元指数（DXY）准入校验：落在 [50, 200] 才采纳，越界返回 None 并告警。

    v9.9.20 起 DXY 已由 _compute_dxy_proxy() 真实合成（不再是恒为 None 的
    占位字段），这道校验从「预留防线」变成**实际生效的运行时闸门**：
    任何合成结果都必须先过这里，越界即拦下并告警，绝不静默返回可疑值。
    这是本字段唯一的安全网，不要在调用处绕过它。
    """
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        print(f"[GLOBAL] dxy_proxy 非法值（非数字）: {value!r}，置为 None")
        return None
    if not math.isfinite(val):
        print(f"[GLOBAL] dxy_proxy 非法值（非有限数）: {val}，置为 None")
        return None
    low, high = _DXY_VALID_RANGE
    if not (low <= val <= high):
        print(f"[GLOBAL] ⚠️ dxy_proxy={val} 超出合理区间 [{low}, {high}]，"
              f"疑似量纲错误（DXY 应约 100，切勿拿 USDCNY≈6.7 充当），置为 None")
        return None
    return val


# ---- 美元指数（DXY）合成（v9.9.20 真值化）----
#
# ICE 美元指数的官方定义（1973-03 基期 = 100）：
#   DXY = 50.14348112
#       × (EUR/USD)^(-0.576) × (USD/JPY)^(+0.136) × (GBP/USD)^(-0.119)
#       × (USD/CAD)^(+0.091) × (USD/SEK)^(+0.042) × (USD/CHF)^(+0.036)
#
# 两个最容易写错的地方，错一个就得到荒谬的值：
#   1. 必须是**几何加权**（连乘 + 幂），不是算术加权。算术加权会把 EUR/USD≈1.16
#      和 USD/JPY≈154.4 这种量纲完全不同的数直接相加 —— 2026-09-11 实测
#      算术加权得 22.39，既不在 _DXY_VALID_RANGE 内也毫无意义；几何加权得
#      99.11，与真实美元指数同量级。
#   2. USD 是基准货币：EUR/USD、GBP/USD 里 USD 在**分母**，指数为负；
#      其余四项 USD 在分子，指数为正。别统一成正号。
_DXY_BASE_CONST = 50.14348112
_DXY_W_EUR = -0.576
_DXY_W_JPY = 0.136
_DXY_W_GBP = -0.119
_DXY_W_CAD = 0.091
_DXY_W_SEK = 0.042
_DXY_W_CHF = 0.036


def _compute_dxy_proxy(pairs: dict) -> float | None:
    """用 AKShare 的「外币/CNY」报价交叉出 ICE 六大成分货币对，合成美元指数。

    fx_spot_quote 只给「外币/CNY」和「CNY/外币」两类报价，而 DXY 公式需要的是
    跨币种对（EUR/USD 等），所以先与 USD/CNY 做交叉：
        EUR/USD = (EUR/CNY) ÷ (USD/CNY)          USD 在分母 → 指数取负
        USD/JPY = (USD/CNY) ÷ (JPY/CNY)          USD 在分子 → 指数取正
        CNY/SEK 是「1 人民币 = ? 克朗」，先取倒数才是 SEK/CNY
        100JPY/CNY 是「100 日元 = ? 人民币」，先 ÷100 归一到 1 日元

    交叉精度已实测核对（2026-09-11 10:58）：与 akshare fx_pair_quote 的直盘
    报价逐项比对，六项全部吻合到小数点后 4 位（EUR/USD 1.160731 vs 1.16073、
    USD/JPY 154.4031 vs 154.399、GBP/USD 1.350385 vs 1.35040、
    USD/CAD 1.384119 vs 1.38413、USD/SEK 9.70027 vs 9.6995、
    USD/CHF 0.813669 vs 0.81368），所以不需要额外发一次 fx_pair_quote 请求，
    复用已经取回来的这一帧即可（省一次上游调用，也少一个失败点）。

    局限性（要写清楚，避免被当成官方指数用）：
      - 这是**合成代理值**，不是 ICE 官方发布的 DXY，字段名保留 dxy_proxy 就是
        为了时刻提示这一点；
      - 六个成分都由 CNY 报价推导，因此**继承了在岸盘口的时点特性**——
        非交易时段（CFETS 每日 03:00 重置、09:30 开盘）算出来的是陈旧价；
      - 任一分量为 0/缺失/非有限数时直接放弃返回 None，**不做硬编码兜底**。
        返回错值比返回 None 危险得多（2026-09-11 拿 USDCNY≈6.7 冒充 DXY
        就是这个教训）。

    Args:
        pairs: _parse_fx_frame() 的输出，{归一化货币对代码: 买卖中值}。

    Returns:
        合成后的 DXY（已过 _sanitize_dxy 校验）；缺少/非法成分时返回 None。
    """
    usdcny = _safe_num(pairs.get("USDCNY"), 0)
    if not (math.isfinite(usdcny) and usdcny > 0):
        return None

    needed = ("EURCNY", "100JPYCNY", "GBPCNY", "CADCNY", "CHFCNY", "CNYSEK")
    vals = {k: _safe_num(pairs.get(k), 0) for k in needed}
    bad = [k for k, v in vals.items() if not (math.isfinite(v) and v > 0)]
    if bad:
        print(f"[GLOBAL] dxy_proxy 缺少/非法成分 {bad}，本次不合成（返回 None）")
        return None

    eurusd = vals["EURCNY"] / usdcny
    usdjpy = usdcny / (vals["100JPYCNY"] / 100.0)
    gbpusd = vals["GBPCNY"] / usdcny
    usdcad = usdcny / vals["CADCNY"]
    usdchf = usdcny / vals["CHFCNY"]
    usdsek = usdcny / (1.0 / vals["CNYSEK"])

    comps = (eurusd, usdjpy, gbpusd, usdcad, usdsek, usdchf)
    if any((not math.isfinite(c)) or c <= 0 for c in comps):
        print(f"[GLOBAL] dxy_proxy 交叉结果非法: {comps}，置为 None")
        return None

    dxy = (_DXY_BASE_CONST
           * eurusd ** _DXY_W_EUR
           * usdjpy ** _DXY_W_JPY
           * gbpusd ** _DXY_W_GBP
           * usdcad ** _DXY_W_CAD
           * usdsek ** _DXY_W_SEK
           * usdchf ** _DXY_W_CHF)
    return _sanitize_dxy(dxy)


def _fx_as_of_text(trade_date: str = "") -> str:
    """生成汇率行的「时点」标注文案（三处渲染点共用，见 FX_AS_OF_MARKER）。

    为什么不用报价自带的时间戳：akshare 的 fx_spot_quote 原始 payload 里确实有
    time 字段，但实测（2026-09-11 10:58）返回的是**空字符串**，midprice 也是
    占位符 '---'，拿不到报价自己的时点。所以分两种口径：

      - 在岸价：标**取数时刻**（`截至 MM-DD HH:MM`），语义是「本系统此刻观测到的
        价」。关键是这个时刻在**取数时**写入并随缓存一起返回，不是在渲染时才生成
        —— 否则缓存命中时会把「多久之前取的」说成「现在」，标注反而变成误导。
      - 离岸兜底价：优先用 Tushare 自己的 trade_date（`MM-DD 收盘`）。因为
        fx_daily 是日频收盘价，很可能就是昨天的，标成取数时刻会把「昨收」
        说成「现在」，比不标更糟。

    Args:
        trade_date: Tushare 的 YYYYMMDD 交易日；空串/非法表示没有，退回取数时刻。

    Returns:
        形如 "截至 09-11 10:58" 或 "09-11 收盘" 的文案。
    """
    d = (trade_date or "").strip()
    if len(d) == 8 and d.isdigit():
        try:
            dt = datetime.strptime(d, "%Y%m%d")
            return f"{dt.month:02d}-{dt.day:02d} 收盘"
        except ValueError:
            pass
    now = datetime.now()
    return (f"截至 {now.month:02d}-{now.day:02d} "
            f"{now.hour:02d}:{now.minute:02d}")


def get_forex_data() -> dict:
    """获取主要外汇汇率（美元/人民币）

    数据源顺序（FIX 2026-09-11 重大调整）：
      1. **AKShare** fx_spot_quote —— 在岸 USD/CNY 的真实主源
      2. **Tushare** fx_daily(USDCNH.FXCM) —— 离岸 USD/CNH，仅作兜底/交叉校验

    为什么把 AKShare 提到主源（违背「Tushare 主源」惯例，但这是实测结论）：
      Tushare 上**不存在在岸 USD/CNY 数据**。详见
      services/tushare_fallback.py 的 get_forex_data docstring——
      穷举 27 个候选接口名只有 fx_daily 可用，而 fx_daily 是 FXCM 数据，
      273 个 ts_code 里没有任何 CNY 标的，只有离岸 USDCNH.FXCM；
      代码原先调用的 fx_obtime 则是根本不存在的接口名。
      在岸/离岸价差通常 <0.1%，但**币种不同**，混用必须显式标注，
      不能用 CNH 默默冒充 CNY（这与 dxy_proxy 不能拿 USDCNY 充数是同一原则）。

    Returns:
        {"usdcny": {...}|None, "dxy_proxy": float|None, "available": bool}
        usdcny 里的 "proxy": True 表示当前值是离岸 USD/CNH 兜底，非在岸价。
        usdcny 里的 "as_of" 是该价格的时点文案（见 _fx_as_of_text），
        dxy_proxy 是合成美元指数（见 _compute_dxy_proxy），两者都可能为 None/缺失。
    """
    cache_key = "forex"
    now = time.time()
    cached = _global_cache.get(cache_key)
    if cached is not None:
        return cached

    # degraded / degraded_reason：把「降级」做成接口字段而不只是一行日志。
    # 2026-09-11 的病根就是主源坏了靠降级撑着、只在 stdout 打一行没人看的日志，
    # 调用方（前端/晨报）完全无感。现在降级状态随数据一起返回，可被巡检、
    # 可被前端读取、可被人看见。
    result = {
        "usdcny": None,
        "dxy_proxy": None,
        "available": False,
        "degraded": False,
        "degraded_reason": "",
    }

    # 主源 1：AKShare（在岸 USD/CNY）
    try:
        from infra.data_source.macro.indicators import get_fx_spot_quote
        df = get_fx_spot_quote()
        pairs = _parse_fx_frame(df)
        if pairs:
            # 美元/人民币：兼容 USD/CNY 与 USDCNY 两种写法
            if "USDCNY" in pairs:
                rate = _sanitize_usdcny(pairs["USDCNY"])
                if rate is not None:
                    result["usdcny"] = {
                        "rate": round(rate, 4),
                        "name": "USD/CNY",
                        "source": "akshare",
                        # 时点在**取数时刻**写入（不是渲染时生成），这样缓存命中时
                        # 返回的是真正观测到该价格的时刻，见 _fx_as_of_text。
                        "as_of": _fx_as_of_text(),
                    }

            # 美元指数（DXY）：v9.9.20 起真实合成，不再是恒为 None 的占位字段。
            #
            # 之前这里写着「fx_spot_quote 只提供外币/CNY 报价，不含跨币种对，无法
            # 计算」——这个结论只对了一半：确实没有直盘，但可以用 USD/CNY 做
            # 交叉推导出六大成分（实测与 fx_pair_quote 直盘吻合到 4 位小数）。
            # 合成口径、几何加权的原因、以及「合成值而非官方指数」的局限都写在
            # _compute_dxy_proxy() 的 docstring 里。
            #
            # 两条不能破的规矩：
            #   1. 结果必须过 _sanitize_dxy() —— 它在 _compute_dxy_proxy 内部调用，
            #      不要在外面另算一遍绕过它；
            #   2. 合成不出来就保持 None，**不要硬编码兜底值**。
            #      返回错值比返回 None 危险得多（2026-09-11 拿 USDCNY≈6.7 冒充
            #      DXY 的返工就是这个教训）。
            result["dxy_proxy"] = (
                _compute_dxy_proxy(pairs) if result["usdcny"] is not None else None
            )

            result["available"] = result["usdcny"] is not None
            if result["available"]:
                print(f"[GLOBAL] Forex from AKShare (primary): "
                      f"USDCNY={result['usdcny']['rate']}")
            else:
                # 解析不到就明确报出来，不再静默返回 available=False
                print(f"[GLOBAL] ⚠️ 外汇主源 AKShare 未解析出 USDCNY"
                      f"（解析到 {len(pairs)} 个货币对）")
        else:
            print("[GLOBAL] ⚠️ 外汇主源 AKShare 返回空/无法解析")
    except Exception as e:
        print(f"[GLOBAL] ⚠️ 外汇主源 AKShare 异常: {e}")

    # 兜底/交叉校验：Tushare（离岸 USD/CNH）
    if not result["available"]:
        try:
            from services.tushare_fallback import TusharePrimary
            tp = TusharePrimary.instance()
            fx = tp.get_forex_data()
            if fx and fx.get("usdcnh"):
                rate = _sanitize_usdcny(fx["usdcnh"])
                if rate is not None:
                    # 离岸价兜底：必须显式标注币种，绝不冒充在岸 USD/CNY。
                    # proxy=True 会被数据源巡检识别为降级状态并告警
                    # （见 scripts/datasource_health_check.py 的 _check_forex）。
                    result["usdcny"] = {
                        "rate": round(rate, 4),
                        "name": "USD/CNH(离岸,代理USD/CNY)",
                        "source": "tushare",
                        "proxy": True,
                        # 离岸价走 fx_daily，是日频收盘价，可能就是昨天的。
                        # 这里用数据源自己的 trade_date，不能标取数时刻 ——
                        # 否则会把「昨收」说成「现在」，比不标更误导。
                        "as_of": _fx_as_of_text(str(fx.get("date", "") or "")),
                    }
                    result["available"] = True
                    result["degraded"] = True
                    result["degraded_reason"] = (
                        "在岸 USD/CNY 主源(AKShare)不可用，"
                        "当前值为 Tushare 离岸 USD/CNH 兜底"
                    )
                    print(f"[GLOBAL] ⚠️ 外汇主源不可用，已用 Tushare 离岸 USD/CNH 兜底: "
                          f"{rate}（date={fx.get('date', '')}，注意：离岸价，非在岸 USD/CNY）")
                else:
                    print(f"[GLOBAL] ⚠️ Tushare 离岸 USD/CNH 值不合理: {fx['usdcnh']}")
            else:
                print("[GLOBAL] ⚠️ 外汇兜底源 Tushare 也无数据（无 USD/CNH）")
        except Exception as e:
            print(f"[GLOBAL] ⚠️ Tushare 外汇兜底异常: {e}")

    if not result["available"]:
        result["degraded"] = True
        result["degraded_reason"] = "外汇全部数据源均不可用（AKShare 在岸 + Tushare 离岸）"
        print("[GLOBAL] ⚠️ 外汇全部数据源均不可用，usdcny=None")

    # v9.9.19 P3-6：降级/失败结果不按 1 小时缓存。
    # 2026-09-11 生产证据：08:30 那次落到离岸兜底后被缓存 3600s，一直锁到 09:30
    # —— 而 09:30 正是在岸开盘、本可以恢复成在岸价的时刻（10:20 实测主源完全
    # 正常：source=akshare、degraded=false）。等于一次降级把自己钉死整整一小时，
    # 这一小时里用户看到的都是 CNH。全失败（available=False）同理。
    # 所以这两类结果只缓存 _GLOBAL_TTL_DEGRADED（300s）：主源一恢复就能自愈，
    # 又不会因为「失败不缓存」而在上游故障时放大请求压力。
    # 判据复用 result 里已有的字段（proxy / available），不新造状态标记。
    _fx_proxy = bool((result.get("usdcny") or {}).get("proxy", False))
    if result["available"] and not _fx_proxy:
        _ttl = _GLOBAL_TTL
    else:
        _ttl = _GLOBAL_TTL_DEGRADED
        print(f"[GLOBAL] 外汇为降级/失败结果，缓存缩短为 {_ttl}s"
              f"（{'离岸CNH兜底' if _fx_proxy else '全部数据源不可用'}），"
              f"便于主源恢复后自愈")
    _global_cache.set(cache_key, result, ttl=_ttl)
    return result


# ============================================================
# 3. 美联储利率
# ============================================================

def get_fed_rate() -> dict:
    """获取美联储联邦基金利率历史"""
    cache_key = "fed_rate"
    now = time.time()
    cached = _global_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"current_rate": None, "last_change": None, "trend": "hold", "available": False}

    try:
        from infra.data_source.macro.indicators import get_usa_interest_rate
        df = get_usa_interest_rate()
        if df is not None and len(df) > 0:
            # 列名识别
            cols = list(df.columns)
            rate_col = None
            date_col = None
            for c in cols:
                if "利率" in str(c) or "rate" in str(c).lower() or "今值" in str(c):
                    rate_col = c
                if "日期" in str(c) or "date" in str(c).lower() or "公布" in str(c):
                    date_col = c

            if rate_col:
                # 从后往前找第一个有效值（跳过 NaN/0）
                valid_rows = df[df[rate_col].notna() & (df[rate_col] != 0)]
                if len(valid_rows) >= 2:
                    latest = valid_rows.iloc[-1]
                    prev = valid_rows.iloc[-2]
                    try:
                        current = _safe_num(latest[rate_col])
                        previous = _safe_num(prev[rate_col])
                        result["current_rate"] = current
                        result["previous_rate"] = previous
                        if current > previous:
                            result["trend"] = "hiking"
                            result["impact"] = "加息周期，资金回流美国，利空新兴市场"
                        elif current < previous:
                            result["trend"] = "cutting"
                            result["impact"] = "降息周期，资金流入新兴市场，利好A股"
                        else:
                            result["trend"] = "hold"
                            result["impact"] = "利率不变，市场等待政策信号"
                    except (ValueError, TypeError):
                        pass

                    if date_col:
                        result["last_change"] = str(latest[date_col])

            result["available"] = result["current_rate"] is not None
    except Exception as e:
        print(f"[GLOBAL] Fed rate failed: {e}")

    _global_cache.set(cache_key, result)
    return result


# ============================================================
# 4. 全球 PE 估值对比
# ============================================================

def get_global_pe() -> dict:
    """获取中美 PE 对比"""
    cache_key = "global_pe"
    now = time.time()
    cached = _global_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"us_pe": None, "cn_pe": None, "spread": None, "available": False}

    def _extract_pe(df, label):
        """从 DataFrame 提取 PE 值，带合理性校验"""
        if df is None or len(df) == 0:
            return None
        last = df.iloc[-1]
        # 优先找明确的 PE 列
        pe_col = None
        for c in df.columns:
            c_lower = str(c).lower()
            if "pe" in c_lower or "市盈率" in str(c):
                pe_col = c
                break
        if pe_col:
            val = _safe_num(last[pe_col])
        elif len(df.columns) > 1:
            # 降级：取第二列，但必须通过合理性校验
            val = _safe_num(last.iloc[1])
        else:
            return None

        # 合理性校验：全球主要市场 PE 通常在 5~150 之间
        # 注意：乐咕全市场PE可能到90+（含亏损股拉高）
        if val is not None and 3 < val < 150:
            return round(val, 2)
        else:
            print(f"[GLOBAL_PE] {label} PE={val} 不在合理区间(3-150)，丢弃")
            return None

    try:
        from infra.data_source.macro.indicators import get_global_market_pe
        # 美国 PE
        try:
            df_us = get_global_market_pe(symbol="美国")
            result["us_pe"] = _extract_pe(df_us, "美国")
        except Exception as e:
            print(f"[GLOBAL] US PE failed: {e}")

        # 中国 PE
        try:
            df_cn = get_global_market_pe(symbol="中国")
            result["cn_pe"] = _extract_pe(df_cn, "中国")
        except Exception as e:
            print(f"[GLOBAL] CN PE failed: {e}")

        # 额外校验：中美 PE 不应相同（如果相同大概率是数据源错误）
        if result["us_pe"] and result["cn_pe"] and result["us_pe"] == result["cn_pe"]:
            print(f"[GLOBAL_PE] 中美PE相同({result['us_pe']})，切换到备用数据源")
            # FIX 2026-04-19 F11: 用不同接口重新获取
            # 美国 → 标普 500 指数 PE; 中国 → 沪深 300 PE
            try:
                from services.market_data import get_valuation_percentile
                cn_val = get_valuation_percentile()
                if cn_val.get("current_pe"):
                    result["cn_pe"] = round(float(cn_val["current_pe"]), 2)
                    print(f"[GLOBAL_PE] 中国 PE 改用沪深300 = {result['cn_pe']}")
            except Exception as e:
                print(f"[GLOBAL_PE] 沪深300 fallback failed: {e}")
                result["cn_pe"] = None

            # 美国 PE 无可靠备用，暂时标记不可用（但保留沪深300 PE 给下游）
            if result["cn_pe"] and result["us_pe"] == result["cn_pe"]:
                result["us_pe"] = None
                result["notice"] = "美国 PE 数据源异常，仅保留中国沪深300数据"

        # PE 价差
        if result["us_pe"] and result["cn_pe"]:
            result["spread"] = round(result["us_pe"] - result["cn_pe"], 2)
            if result["spread"] > 10:
                result["assessment"] = "A股估值显著低于美股，性价比较高"
            elif result["spread"] > 0:
                result["assessment"] = "A股估值略低于美股"
            else:
                result["assessment"] = "A股估值高于美股，需谨慎"

        result["available"] = result["us_pe"] is not None or result["cn_pe"] is not None
    except Exception as e:
        print(f"[GLOBAL] Global PE failed: {e}")

    _global_cache.set(cache_key, result)
    return result


# ============================================================
# 5. 全球市场综合快照（一次性调用）
# ============================================================

def get_global_snapshot() -> dict:
    """一次性获取全球市场综合快照"""
    cache_key = "global_snapshot"
    cached = _global_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {
        "us_indices": get_us_indices(),
        "forex": get_forex_data(),
        "fed_rate": get_fed_rate(),
        "global_pe": get_global_pe(),
        "updatedAt": datetime.now().isoformat(),
    }

    # 生成简洁摘要（供 DeepSeek system prompt 注入）
    summary_lines = ["【全球市场快照】"]

    us = result["us_indices"]
    if us.get("available"):
        for key, name in [("dji", "道琼斯"), ("spx", "标普500"), ("ixic", "纳斯达克")]:
            d = us.get(key)
            if d:
                emoji = "📈" if d["change_pct"] > 0 else "📉"
                summary_lines.append(f"  {emoji} {name}: {d['close']:,.0f} ({d['change_pct']:+.2f}%)")

    fx = result["forex"]
    if fx.get("available") and fx.get("usdcny"):
        # v9.9.19：这段 summary 会被 api/shared_helpers.py 原样拼进 LLM 上下文，
        # 模型会照抄里面的措辞写结论。主源（AKShare 在岸）挂掉、落到 Tushare
        # 离岸 USD/CNH 兜底时若不标出币种，模型就会把离岸价当在岸 USD/CNY
        # 转述给用户 —— 与 dxy_proxy 拿 USDCNY 充数是同一类语义错误。
        # 文案与 scripts/night_worker.py 的汇率行保持一致，避免两处漂移
        # （一致性由 tests/test_model_attribution_labels.py 的
        #  test_all_usdcny_render_sites_handle_proxy 扫描三处渲染点守住）。
        # 时点文案来自后端 usdcny.as_of（取数时刻/离岸收盘日），不要在这里现取
        # datetime.now()——那会把缓存里的旧价说成「现在」。
        _as_of = fx["usdcny"].get("as_of") or ""
        _as_of_txt = f"（{_as_of}）" if _as_of else ""
        if fx["usdcny"].get("proxy"):
            summary_lines.append(
                f"  💱 美元/人民币(离岸CNH兜底): {fx['usdcny']['rate']:.4f}{_as_of_txt}"
            )
        else:
            summary_lines.append(
                f"  💱 美元/人民币: {fx['usdcny']['rate']:.4f}{_as_of_txt}"
            )

    fed = result["fed_rate"]
    if fed.get("available"):
        trend_map = {"hiking": "加息周期⬆️", "cutting": "降息周期⬇️", "hold": "按兵不动"}
        summary_lines.append(f"  🏛️ 美联储利率: {fed['current_rate']}% ({trend_map.get(fed['trend'], '')})")

    gpe = result["global_pe"]
    if gpe.get("available"):
        if gpe.get("us_pe") and gpe.get("cn_pe"):
            summary_lines.append(f"  📊 PE对比: 美国{gpe['us_pe']} vs 中国{gpe['cn_pe']} (价差{gpe.get('spread', 0):+.1f})")

    result["summary"] = "\n".join(summary_lines)

    # TTL 见 _SNAPSHOT_TTL 的注释：与外汇降级 TTL 对齐，且子函数各自有 3600s
    # 缓存，降到这里不会新增上游请求。不要改回裸写的 600 —— 那会让外汇的
    # 300s 自愈链路被快照层再压一层。
    _global_cache.set(cache_key, result, ttl=_SNAPSHOT_TTL)
    return result


# ============================================================
# 6. DeepSeek 全球→A股影响分析
# ============================================================

def analyze_global_impact_on_a_shares() -> dict:
    """DeepSeek 分析全球市场对 A 股的影响"""
    cache_key = "global_impact"
    cached = _global_cache.get(cache_key)
    if cached is not None:
        return cached

    snapshot = get_global_snapshot()
    result = {"analysis": "", "source": "none", "snapshot": snapshot}

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        result["analysis"] = snapshot.get("summary", "全球数据暂不可用")
        result["source"] = "data_only"
        _global_cache.set(cache_key, result, ttl=1800)
        return result

    prompt = f"""请分析以下全球市场数据对中国A股的影响，给出简洁的投资建议。

{snapshot.get('summary', '')}

要求：
1. 逐项分析每个全球因素对 A 股的影响（利好/利空/中性）
2. 特别关注：美联储政策→资金流向、美股走势→情绪传染、汇率→北向资金
   ⚠️ 北向资金净流入数据不可得（交易所 2024-08-19 起改季度披露），此处仅做传导机制的定性推演，禁止给出具体金额或流入/流出方向判断。
3. 给出综合判断（一句话）
4. 给出具体操作建议（加仓/减仓/持有+针对哪类资产）
5. 控制在 200 字以内
6. 用 emoji 标注利好/利空"""

    try:
        from infra.llm.gateway import LLMGateway
        gw = LLMGateway.instance()
        llm_result = gw.call_sync(
            prompt,
            system="你是全球宏观分析师，专注分析国际市场对中国A股的传导效应。简洁、有数据支撑。",
            model_tier="llm_light",
            user_id="",
            module="global_impact",
            max_tokens=500,
        )
        if not llm_result.get("fallback") and llm_result.get("content"):
            result["analysis"] = llm_result["content"]
            result["source"] = "ai"
    except Exception as e:
        print(f"[GLOBAL_IMPACT] LLM Gateway failed: {e}")
        result["analysis"] = snapshot.get("summary", "分析暂不可用")
        result["source"] = "data_only"

    _global_cache.set(cache_key, result)
    return result


# ============================================================
# 7. 决策数据包（供 Claude 快速拉取）
# ============================================================

def get_decision_data_pack(user_id: str = "default") -> dict:
    """一次性返回全量决策数据包 — 供 Claude 做投资决策用"""
    from services.stock_monitor import load_stock_holdings, scan_all_holdings
    from services.fund_monitor import load_fund_holdings, scan_all_fund_holdings
    from services.data_layer import (
        get_valuation_percentile, get_fear_greed_index,
        get_technical_indicators, get_market_news,
        get_northbound_flow, get_margin_trading,
    )
    from services.portfolio_overview import get_portfolio_overview

    pack = {
        "timestamp": datetime.now().isoformat(),
        "global": get_global_snapshot(),
        "global_impact": analyze_global_impact_on_a_shares(),
    }

    # A 股核心数据
    try:
        pack["valuation"] = get_valuation_percentile()
    except Exception:
        pack["valuation"] = {"error": "unavailable"}

    try:
        pack["fear_greed"] = get_fear_greed_index()
    except Exception:
        pack["fear_greed"] = {"error": "unavailable"}

    try:
        pack["technical"] = get_technical_indicators()
    except Exception:
        pack["technical"] = {"error": "unavailable"}

    try:
        pack["northbound"] = get_northbound_flow()
    except Exception:
        pack["northbound"] = {"error": "unavailable"}

    try:
        pack["margin"] = get_margin_trading()
    except Exception:
        pack["margin"] = {"error": "unavailable"}

    # 持仓数据
    try:
        pack["stock_holdings"] = scan_all_holdings(user_id)
    except Exception:
        pack["stock_holdings"] = {"error": "unavailable"}

    try:
        pack["fund_holdings"] = scan_all_fund_holdings(user_id)
    except Exception:
        pack["fund_holdings"] = {"error": "unavailable"}

    try:
        pack["portfolio_overview"] = get_portfolio_overview(user_id)
    except Exception:
        pack["portfolio_overview"] = {"error": "unavailable"}

    # 最新新闻
    try:
        pack["news"] = get_market_news(5)
    except Exception:
        pack["news"] = []

    # 国内政策数据
    try:
        from services.policy_data import get_all_policy_topics, get_real_estate_data
        pack["policy_topics"] = get_all_policy_topics()
        pack["real_estate"] = get_real_estate_data()
    except Exception:
        pack["policy_topics"] = {"error": "unavailable"}

    # 市场微观因子（大宗商品+限售解禁+ETF 资金流）
    try:
        from services.market_factors import get_all_market_factors
        pack["market_factors"] = get_all_market_factors()
    except Exception:
        pack["market_factors"] = {"error": "unavailable"}

    # 持仓关联智能（个股新闻+资金流+行业+解禁）
    try:
        from services.holding_intelligence import scan_all_holding_intelligence
        pack["holding_intelligence"] = scan_all_holding_intelligence()
    except Exception:
        pack["holding_intelligence"] = {"error": "unavailable"}

    # V8 扩展宏观（GDP/工业增加值/社零/固投/龙虎榜/管理层增减持）
    try:
        from services.macro_v8 import get_all_v8_macro
        pack["macro_v8"] = get_all_v8_macro()
    except Exception:
        pack["macro_v8"] = {"error": "unavailable"}

    return pack
