"""
钱袋子 — 基金行业分类（统一 taxonomy）

背景（v9.9.24 P0-4）：
选基接口此前存在"行业分类双轨制"——候选基金和持仓基金虽然都走
`industry_templates.get_fund_industry()` 这套**基金名称关键词**映射，但主动管理型
基金的简称里几乎没有行业词（"西部利得新盈混合C" / "易方达竞争优势企业混合A"），
于是 50%+ 的候选全部退化成兜底值 "📈 主动混合"；而用户持仓恰好带行业词
（华夏半导体龙头 / 华夏先进制造龙头），被分成完全不同的标签。两套标签永远
匹配不上，`holding_relation` 就恒等于"🟢 新敞口"，并说出"你目前没有📈 主动混合
方向"这种自相矛盾的话。

修法：**不以名称猜行业，以基金实际持仓（重仓股）反推行业分布**。
数据源 = Tushare `fund_portfolio`（基金重仓股，含 stk_mkv_ratio 占净值比）+ 
Tushare `stock_basic`（个股 industry 字段）。两者都是 Tushare 5000 积分档内接口，
且是定期报告披露的真实数据，比基金简称可靠得多。

统一后的 taxonomy 只有一套：候选基金与持仓基金都走 `classify_fund()`，
输出可直接互相比较的 `industry_mix`（{行业标签: 权重}）。

降级链：重仓股 → 名称行业词（债券/宽基优先）→ 📈 主动混合（兜底）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "fund_industry",
    "scope": "public",
    "input": ['code', 'name'],
    "output": "fund_industry_classification",
    "cost": "network",
    "tags": ['基金行业', '重仓股', 'Tushare', '持仓重叠'],
    "description": "基金行业分类：以重仓股反推行业分布，统一候选/持仓 taxonomy",
    "layer": "analysis",
    "priority": 3,
}

import json
import os
import time
from pathlib import Path

import config

DATA_DIR = Path(config.DATA_DIR)

_CACHE_DIR = DATA_DIR / "_cache" / "fund_industry"
_STOCK_INDUSTRY_CACHE = _CACHE_DIR / "stock_industry_map.json"
_FUND_INDUSTRY_CACHE = _CACHE_DIR / "fund_industry_cache.json"

# 个股行业映射变化极慢（上市/行业重分类），7 天足够
_STOCK_INDUSTRY_TTL = 7 * 86400
# 基金重仓股是季报数据，30 天足够；季报披露有滞后，不必频繁刷新
_FUND_INDUSTRY_TTL = 30 * 86400

FALLBACK_TAG = "📈 主动混合"
FALLBACK_DESC = "主动管理型混合基金，行业分散，由基金经理主导配置"

# 兜底类/风格类标签：**不能**用于"你目前没有X方向"这类重叠判断。
# 它们描述的是投资风格或资产类别，不是真实行业敞口。
GENERIC_TAGS = {
    FALLBACK_TAG,
    "🚀 成长",
    "📐 价值蓝筹",
    "💰 红利低波",
    "📊 宽基指数",
    "🇺🇸 美股科技",
    "🇺🇸 美股大盘",
    "🇭🇰 港股",
    "🇯🇵 日本",
    "🇮🇳 印度",
    "🇪🇺 欧洲",
    "🌐 海外",
}

# 债券类：fund_portfolio 对债基没有意义（债基不披露 A 股重仓），名称反而更准
_BOND_TAGS = {"🛡️ 纯债", "🔄 可转债", "📄 债券"}
# 宽基类：指数基金以跟踪标的为准，重仓股反推会把沪深300误判成"高端制造"
_INDEX_TAGS = {
    "📊 沪深300", "📊 中证500", "📊 中证1000", "📊 深证100",
    "📊 上证50", "📊 北证50", "📊 宽基指数", "🚀 创业板", "🚀 科创板",
}

# ===== Tushare stock_basic.industry → 项目统一行业标签 =====
# 覆盖 stock_basic 现网全部 110 个 industry 取值（2026-09 拉取核对）。
# 说明：Tushare 没有"军工"行业，船舶/航空归入高端制造，避免在重仓股路径上
# 臆造军工标签。
TS_INDUSTRY_TO_TAG = {
    # --- 半导体 / 电子 ---
    "半导体": "💎 半导体",
    "元器件": "💎 半导体",
    # --- AI / 科技 / 通信 ---
    "软件服务": "🤖 AI/科技",
    "IT设备": "🤖 AI/科技",
    "通信设备": "📡 通信5G",
    "电信运营": "📡 通信5G",
    "互联网": "📱 互联网",
    # --- 高端制造 ---
    "专用机械": "🏭 高端制造",
    "工程机械": "🏭 高端制造",
    "机床制造": "🏭 高端制造",
    "机械基件": "🏭 高端制造",
    "化工机械": "🏭 高端制造",
    "纺织机械": "🏭 高端制造",
    "农用机械": "🏭 高端制造",
    "轻工机械": "🏭 高端制造",
    "电气设备": "🏭 高端制造",
    "电器仪表": "🏭 高端制造",
    "运输设备": "🏭 高端制造",
    "船舶": "🏭 高端制造",
    "航空": "🏭 高端制造",
    "矿物制品": "🏭 高端制造",
    "玻璃": "🏭 高端制造",
    # --- 新能源 / 公用 ---
    "新型电力": "🔋 新能源",
    "水力发电": "🔋 新能源",
    "火力发电": "🔋 新能源",
    "供气供热": "🔋 新能源",
    "环境保护": "🔋 新能源",
    # --- 新能源车 ---
    "汽车整车": "🚗 新能源车",
    "汽车配件": "🚗 新能源车",
    "汽车服务": "🚗 新能源车",
    "摩托车": "🚗 新能源车",
    # --- 医药 ---
    "化学制药": "💊 医药",
    "生物制药": "💊 医药",
    "中成药": "💊 医药",
    "医疗保健": "💊 医药",
    "医药商业": "💊 医药",
    # --- 金融 ---
    "银行": "🏦 金融",
    "保险": "🏦 金融",
    "证券": "🏦 金融",
    "多元金融": "🏦 金融",
    # --- 消费 ---
    "白酒": "🍷 消费",
    "啤酒": "🍷 消费",
    "红黄酒": "🍷 消费",
    "软饮料": "🍷 消费",
    "乳制品": "🍷 消费",
    "食品": "🍷 消费",
    "服饰": "🍷 消费",
    "纺织": "🍷 消费",
    "日用化工": "🍷 消费",
    "百货": "🍷 消费",
    "超市连锁": "🍷 消费",
    "商品城": "🍷 消费",
    "商贸代理": "🍷 消费",
    "其他商业": "🍷 消费",
    "批发业": "🍷 消费",
    "电器连锁": "🍷 消费",
    "文教休闲": "🍷 消费",
    "旅游景点": "🍷 消费",
    "旅游服务": "🍷 消费",
    "酒店餐饮": "🍷 消费",
    "广告包装": "🍷 消费",
    "影视音像": "🍷 消费",
    "出版业": "🍷 消费",
    "造纸": "🍷 消费",
    # --- 家电家居 ---
    "家居用品": "🏠 家电家居",
    "家用电器": "🏠 家电家居",
    # --- 农业 ---
    "种植业": "🌾 农业",
    "渔业": "🌾 农业",
    "林业": "🌾 农业",
    "农业综合": "🌾 农业",
    "饲料": "🌾 农业",
    "农药化肥": "🌾 农业",
    # --- 化工材料 ---
    "化工原料": "🧪 化工材料",
    "化纤": "🧪 化工材料",
    "塑料": "🧪 化工材料",
    "橡胶": "🧪 化工材料",
    "染料涂料": "🧪 化工材料",
    # --- 周期资源 ---
    "铜": "⛏️ 周期资源",
    "铝": "⛏️ 周期资源",
    "铅锌": "⛏️ 周期资源",
    "小金属": "⛏️ 周期资源",
    "特种钢": "⛏️ 周期资源",
    "普钢": "⛏️ 周期资源",
    "钢加工": "⛏️ 周期资源",
    "煤炭开采": "⛏️ 周期资源",
    "焦炭加工": "⛏️ 周期资源",
    "石油开采": "⛏️ 周期资源",
    "石油加工": "⛏️ 周期资源",
    "石油贸易": "⛏️ 周期资源",
    # --- 贵金属 ---
    "黄金": "🥇 黄金/商品",
    # --- 基建地产 / 交运 ---
    "建筑工程": "🏗️ 基建地产",
    "全国地产": "🏗️ 基建地产",
    "区域地产": "🏗️ 基建地产",
    "园区开发": "🏗️ 基建地产",
    "房产服务": "🏗️ 基建地产",
    "装修装饰": "🏗️ 基建地产",
    "水泥": "🏗️ 基建地产",
    "其他建材": "🏗️ 基建地产",
    "陶瓷": "🏗️ 基建地产",
    "公路": "🏗️ 基建地产",
    "路桥": "🏗️ 基建地产",
    "铁路": "🏗️ 基建地产",
    "港口": "🏗️ 基建地产",
    "水运": "🏗️ 基建地产",
    "空运": "🏗️ 基建地产",
    "机场": "🏗️ 基建地产",
    "仓储物流": "🏗️ 基建地产",
    "公共交通": "🏗️ 基建地产",
    "水务": "🏗️ 基建地产",
    # "综合类" 无行业信息，故意不映射
}

# 重仓股路径新增的两个标签描述（其余标签的 desc 复用 FUND_INDUSTRY_MAP）
_EXTRA_TAG_DESC = {
    "🧪 化工材料": "化工/材料，产能与产品价格周期驱动，弹性大",
    "⛏️ 周期资源": "煤炭/石油/钢铁/有色等周期资源，跟随大宗商品与宏观景气",
}

# 单只基金被判定为"有明确行业敞口"的最低权重门槛。
# 低于此值说明十大重仓高度分散，诚实标注为主动混合（行业分散本来就是事实）。
_MIN_DOMINANT_WEIGHT = 0.20
# 第一大行业占比低于此值时，标签后追加"行业分散"提示，避免用户误以为是指数化的纯赛道基金
_DISPERSED_WEIGHT = 0.35


def _atomic_write(filepath: Path, data: dict) -> None:
    try:
        from services.persistence import atomic_write_json
        atomic_write_json(filepath, data)
    except Exception:
        pass


def _read_json(filepath: Path) -> dict:
    try:
        if filepath.exists():
            return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _fund_ts_code(code: str) -> str:
    code = str(code or "").strip()
    if not code:
        return ""
    return code if "." in code else f"{code}.OF"


# ------------------------------------------------------------------
# 个股行业映射（Tushare stock_basic）
# ------------------------------------------------------------------
_stock_industry_map: dict = {}
_stock_industry_loaded = False


def _load_stock_industry_map() -> dict:
    """{ts_code: Tushare industry}，磁盘缓存 7 天。"""
    global _stock_industry_map, _stock_industry_loaded
    if _stock_industry_loaded:
        return _stock_industry_map

    payload = _read_json(_STOCK_INDUSTRY_CACHE)
    if payload and (time.time() - payload.get("t", 0)) < _STOCK_INDUSTRY_TTL:
        _stock_industry_map = payload.get("v", {}) or {}
        _stock_industry_loaded = True
        return _stock_industry_map

    try:
        from services.tushare_data import _call_tushare, is_configured
        if is_configured():
            rows = _call_tushare("stock_basic", {"list_status": "L"},
                                 "ts_code,name,industry")
            mapping = {r.get("ts_code"): (r.get("industry") or "")
                       for r in (rows or []) if r.get("ts_code")}
            if mapping:
                _stock_industry_map = mapping
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _atomic_write(_STOCK_INDUSTRY_CACHE,
                              {"t": time.time(), "v": mapping})
    except Exception as e:
        print(f"[FUND_INDUSTRY] stock_basic failed: {e}")

    _stock_industry_loaded = True
    return _stock_industry_map


# ------------------------------------------------------------------
# 名称关键词路径（复用 FUND_INDUSTRY_MAP，保证与既有 taxonomy 同一套标签）
# ------------------------------------------------------------------
def name_based_tag(fund_name: str) -> dict:
    """纯名称关键词分类（旧逻辑），返回 {tag, desc} 或 {}。"""
    try:
        from services.industry_templates import get_fund_industry
        return get_fund_industry(fund_name or "") or {}
    except Exception:
        return {}


def is_generic_tag(tag: str) -> bool:
    return (not tag) or tag in GENERIC_TAGS


# ------------------------------------------------------------------
# 重仓股路径（Tushare fund_portfolio）
# ------------------------------------------------------------------
_fund_industry_cache: dict = {}
_fund_cache_dirty = False


def _load_fund_cache() -> dict:
    global _fund_industry_cache
    if _fund_industry_cache:
        return _fund_industry_cache
    payload = _read_json(_FUND_INDUSTRY_CACHE)
    entries = payload.get("v", {}) or {}
    now = time.time()
    _fund_industry_cache = {
        k: v for k, v in entries.items()
        if (now - v.get("t", 0)) < _FUND_INDUSTRY_TTL
    }
    return _fund_industry_cache


def _persist_fund_cache() -> None:
    global _fund_cache_dirty
    if not _fund_cache_dirty:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        on_disk = _read_json(_FUND_INDUSTRY_CACHE).get("v", {}) or {}
        on_disk.update(_fund_industry_cache)
        _atomic_write(_FUND_INDUSTRY_CACHE, {"t": time.time(), "v": on_disk})
        _fund_cache_dirty = False
    except Exception:
        pass


def _tag_of_symbol(symbol: str, industry_map: dict) -> str:
    """重仓股代码 → 统一行业标签；港股单独归 🇭🇰 港股。"""
    symbol = (symbol or "").strip()
    if not symbol:
        return ""
    if symbol.endswith(".HK"):
        return "🇭🇰 港股"
    industry = industry_map.get(symbol, "")
    return TS_INDUSTRY_TO_TAG.get(industry, "")


def portfolio_industry_mix(code: str) -> dict:
    """用最新一期基金重仓股反推行业分布。

    返回 {"mix": {tag: weight}, "end_date": "20260630", "top": [[symbol, tag, w], ...]}
    权重已归一化（sum ≈ 1，只统计能识别行业的部分）。无数据返回 {}。
    """
    ts_code = _fund_ts_code(code)
    if not ts_code:
        return {}

    cached = _load_fund_cache().get(code)
    if cached is not None:
        return cached.get("v") or {}

    result = {}
    try:
        from services.tushare_data import _call_tushare, is_configured
        if not is_configured():
            return {}
        rows = _call_tushare("fund_portfolio", {"ts_code": ts_code},
                             "ts_code,ann_date,end_date,symbol,mkv,stk_mkv_ratio") or []
        if not rows:
            _store(code, result)
            return result

        end_date = max((r.get("end_date") or "") for r in rows)
        latest = [r for r in rows if (r.get("end_date") or "") == end_date]
        if not latest:
            _store(code, result)
            return result

        def _w(r):
            try:
                return float(r.get("stk_mkv_ratio") or 0)
            except (TypeError, ValueError):
                return 0.0

        # stk_mkv_ratio 单位：%（占净值比）。
        # 注意：新上市/打新标的的 stk_mkv_ratio 会是 0，不能当成"权重 1"参与
        # 归一化，否则会把打新仓位的行业权重严重放大（实测某半导体基金因此把
        # 打新的 5 只科创新股算成 25% 权重，把真实第一大行业挤出头名）。
        # 处理：>0 的按真实比例加权；全部为 0 时才退化为等权。
        latest.sort(key=_w, reverse=True)
        top = latest[:10]

        industry_map = _load_stock_industry_map()
        identified = []
        for r in top:
            sym = (r.get("symbol") or "").strip()
            tag = _tag_of_symbol(sym, industry_map)
            if tag:
                identified.append((sym, tag, _w(r)))

        if not identified:
            _store(code, result)
            return result

        positive = [x for x in identified if x[2] > 0]
        if positive:
            weighted = positive
            unit = "stk_mkv_ratio"
        else:
            weighted = [(s, t, 1.0) for s, t, _wgt in identified]
            unit = "equal"

        raw: dict = {}
        for _sym, tag, w in weighted:
            raw[tag] = raw.get(tag, 0.0) + w
        detail = [[s, t, round(w, 2)] for s, t, w in
                  sorted(identified, key=lambda x: -x[2])[:6]]

        total = sum(raw.values())
        if total <= 0:
            _store(code, result)
            return result

        mix = {t: round(v / total, 3) for t, v in
               sorted(raw.items(), key=lambda kv: -kv[1])}
        result = {
            "mix": mix,
            "end_date": end_date,
            "top": detail,
            "unit": unit,
            "coverage": round(len(identified) / max(len(top), 1), 2),
        }
    except Exception as e:
        print(f"[FUND_INDUSTRY] portfolio mix failed {code}: {e}")

    _store(code, result)
    return result


def _store(code: str, value: dict) -> None:
    global _fund_cache_dirty
    # 空结果同样缓存，避免每次请求都去打一次 Tushare（QDII 基金永远没有 A 股重仓）
    _load_fund_cache()[code] = {"t": time.time(), "v": value}
    _fund_cache_dirty = True
    _persist_fund_cache()


# ------------------------------------------------------------------
# 统一入口
# ------------------------------------------------------------------
def classify_fund(code: str = "", name: str = "") -> dict:
    """统一的基金行业分类（候选基金与持仓基金共用同一套 taxonomy）。

    返回 {
        "tag": "💎 半导体",
        "desc": "...",
        "source": "portfolio" | "name" | "fallback",
        "mix": {"💎 半导体": 0.62, "🏭 高端制造": 0.21},
        "generic": False,     # True 表示是风格/兜底标签，不能用于重叠判断
    }
    """
    nm = name or ""
    name_match = name_based_tag(nm)

    # 1) 债券类：重仓股路径对债基无意义，名称即事实
    if name_match.get("tag") in _BOND_TAGS:
        return _pack(name_match["tag"], name_match.get("desc", ""),
                     "name", {name_match["tag"]: 1.0}, False)

    # 2) 宽基/指数类：以跟踪标的为准，不用重仓股反推（会把沪深300说成高端制造）
    if name_match.get("tag") in _INDEX_TAGS:
        return _pack(name_match["tag"], name_match.get("desc", ""),
                     "name", {name_match["tag"]: 1.0}, False)

    # 3) 重仓股反推（主动管理型基金的唯一可靠依据）
    pf = portfolio_industry_mix(code) if code else {}
    mix = pf.get("mix") or {}
    if mix:
        top_tag, top_w = next(iter(mix.items()))
        if top_w >= _MIN_DOMINANT_WEIGHT:
            desc = _desc_of(top_tag)
            dispersed = top_w < _DISPERSED_WEIGHT
            if dispersed:
                desc = f"{desc}（十大重仓行业分散，第一大行业仅占 {top_w:.0%}）"
            out = _pack(top_tag, desc, "portfolio", mix, False,
                        end_date=pf.get("end_date"), top=pf.get("top"))
            out["dispersed"] = dispersed
            return out
        # 十大重仓高度分散：诚实标注主动混合，但把 mix 一起返回，供重叠度计算使用
        return _pack(FALLBACK_TAG, FALLBACK_DESC, "portfolio", mix, True,
                     end_date=pf.get("end_date"), top=pf.get("top"))

    # 4) 名称行业词（QDII / 债基 / 无重仓披露的基金走这里）
    if name_match.get("tag"):
        return _pack(name_match["tag"], name_match.get("desc", ""), "name",
                     {name_match["tag"]: 1.0}, is_generic_tag(name_match["tag"]))

    # 5) 兜底
    return _pack(FALLBACK_TAG, FALLBACK_DESC, "fallback", {}, True)


def _pack(tag, desc, source, mix, generic, end_date=None, top=None) -> dict:
    out = {
        "tag": tag,
        "desc": desc or _desc_of(tag),
        "source": source,
        "mix": mix or {},
        "generic": bool(generic),
    }
    if end_date:
        out["end_date"] = end_date
    if top:
        out["top"] = top
    return out


_desc_cache: dict = {}


def _desc_of(tag: str) -> str:
    if not tag:
        return ""
    if tag in _EXTRA_TAG_DESC:
        return _EXTRA_TAG_DESC[tag]
    if tag in _desc_cache:
        return _desc_cache[tag]
    desc = ""
    try:
        from services.industry_templates import FUND_INDUSTRY_MAP
        for _kw, t, d in FUND_INDUSTRY_MAP:
            if t == tag:
                desc = d
                break
    except Exception:
        pass
    _desc_cache[tag] = desc
    return desc


def classify_funds(funds: list, max_workers: int = 6) -> None:
    """批量给基金列表写 industry_tag / industry_desc / industry_mix / industry_source。

    网络调用走线程池并全部 try/except：单只失败不影响整体，最坏退回名称分类。
    """
    if not funds:
        return
    targets = []
    for f in funds:
        code = str(f.get("code", "") or "")
        if not code:
            continue
        # 已有 portfolio 来源的分类说明本次已算过，跳过
        if f.get("industry_source") == "portfolio":
            continue
        targets.append(f)

    if not targets:
        return

    def _work(f):
        try:
            return f, classify_fund(str(f.get("code", "")), f.get("name", ""))
        except Exception:
            return f, None

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(_work, targets))
    except Exception:
        results = [_work(f) for f in targets]

    for f, res in results:
        if not res:
            continue
        _apply_to_fund(f, res)


def _apply_to_fund(fund: dict, res: dict) -> None:
    fund["industry_tag"] = res["tag"]
    fund["industry_desc"] = res["desc"]
    fund["industry_source"] = res["source"]
    if res.get("mix"):
        fund["industry_mix"] = res["mix"]
    if res.get("end_date"):
        fund["industry_as_of"] = res["end_date"]
    if res.get("dispersed"):
        fund["industry_dispersed"] = True


# ------------------------------------------------------------------
# 重叠度
# ------------------------------------------------------------------
def normalize_mix(mix: dict) -> dict:
    total = sum(v for v in (mix or {}).values() if v)
    if total <= 0:
        return {}
    return {k: v / total for k, v in mix.items() if v}


def overlap_score(cand_mix: dict, my_mix: dict) -> float:
    """候选基金与用户持仓的行业敞口重叠度（0~1）。

    = Σ min(候选权重, 持仓权重)，两个分布都已归一化。
    例：候选 半导体 0.8，持仓 半导体 0.35 → 重叠 0.35（买入会显著加重集中度）。
    """
    a = normalize_mix(cand_mix)
    b = normalize_mix(my_mix)
    if not a or not b:
        return 0.0
    return round(sum(min(a.get(t, 0.0), b.get(t, 0.0))
                     for t in set(a) | set(b)), 3)


def overlap_breakdown(cand_mix: dict, my_mix: dict, limit: int = 3) -> list:
    """重叠贡献明细 [(tag, contribution), ...]，按贡献降序。

    只看总重叠度会误导：某基金主赛道是金融（40%），但真正和你的持仓撞上的是它
    的 AI/科技 + 通信仓位。提示语要说出**撞在哪**，而不是它最大的那个赛道。
    """
    a = normalize_mix(cand_mix)
    b = normalize_mix(my_mix)
    if not a or not b:
        return []
    items = [(t, min(a.get(t, 0.0), b.get(t, 0.0))) for t in set(a) | set(b)]
    items = [x for x in items if x[1] > 0.005]
    items.sort(key=lambda x: -x[1])
    return items[:limit]


def aggregate_holdings_mix(holdings: list, max_workers: int = 6) -> tuple:
    """把用户持仓聚合成行业敞口分布。

    返回 (my_mix, per_fund: {code: classification})
    每只持仓等权（1/N）；单只基金内部的 mix 按自身权重摊薄。
    """
    if not holdings:
        return {}, {}

    def _work(h):
        try:
            return h, classify_fund(str(h.get("code", "")), h.get("name", ""))
        except Exception:
            return h, None

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(_work, holdings))
    except Exception:
        results = [_work(h) for h in holdings]

    per_fund: dict = {}
    raw: dict = {}
    n = 0
    for h, res in results:
        if not res:
            continue
        code = str(h.get("code", "") or "")
        per_fund[code] = res
        n += 1
        mix = normalize_mix(res.get("mix") or {res["tag"]: 1.0})
        for t, w in mix.items():
            raw[t] = raw.get(t, 0.0) + w

    if n == 0:
        return {}, per_fund
    # 每只基金等权 1/n
    total = sum(raw.values()) or 1.0
    my_mix = {t: round(v / total, 3) for t, v in
              sorted(raw.items(), key=lambda kv: -kv[1])}
    return my_mix, per_fund
