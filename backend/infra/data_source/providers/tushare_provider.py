"""
TushareProvider -- DataSourceProtocol adapter for Tushare Pro.
===============================================================
Primary source for:
  - market:      stock prices, indices, ETF (structured, stable)
  - fundamental: earnings, valuation, financials (strongest coverage)

Requires TUSHARE_TOKEN environment variable.

Status: IMPLEMENTED — provides all 7 supported metrics.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Union

import pandas as pd

from infra.cache import MemoryCache

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = frozenset({
    "stock_price",
    "index_daily",
    "fund_nav",
    "income_statement",
    "balance_sheet",
    "valuation",
    "dividend",
    # v9.9.x FIX 2026-09-01（任务#1，用户显式要求）：个股基本信息（行业/
    # 上市时间/市值），用于替换 AKShare stock_individual_info_em()——
    # 该接口诊断出 push2.eastmoney.com 存在 ~30~70% 量级的间歇性连接
    # 被动 reset（见 infra/data_source/alt/flows.py::get_stock_
    # individual_info 的完整诊断记录）。stock_basic_info 拼接
    # pro.stock_basic()（行业/名称/上市时间）+ pro.daily_basic()（市值），
    # 唯一消费方 services/holding_intelligence.py::get_stock_industry()
    # 只用"行业"字段。
    "stock_basic_info",
    # v9.9.x FIX 2026-09-01（任务#3，用户显式要求）：同花顺题材热门列表
    # + 题材成分股，用于替换 AKShare 已彻底死亡的接口
    # ak.stock_board_concept_name_ths() / ak.stock_board_concept_cons_
    # ths()（任务#8 诊断发现，随库升级被移除，AttributeError 长期被
    # except 吞掉，恒返回空列表）。见
    # infra/data_source/alt/ths_concepts.py 完整改造记录。
    "ths_hot_concepts",
    "ths_concept_members",
})

# Caches by TTL requirement
_quote_cache = MemoryCache(default_ttl=300)     # 5 min for real-time quotes
_kline_cache = MemoryCache(default_ttl=3600)    # 1 hour for daily bars
_financial_cache = MemoryCache(default_ttl=86400)  # 24 hours for financials


class TushareProvider:
    """Tushare Pro data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports tushare only on first fetch() call.
    
    API Notes:
    - pro.daily(ts_code, start_date, end_date) → DataFrame with daily bars
    - pro.index_daily(ts_code, start_date, end_date) → DataFrame with index daily
    - pro.fund_nav(ts_code, start_date, end_date) → DataFrame with fund NAV
    - pro.income(ts_code, start_date, end_date) → DataFrame with income statement
    - pro.balancesheet(ts_code, start_date, end_date) → DataFrame with balance sheet
    - pro.valuation_daily(ts_code, start_date, end_date) → DataFrame with valuation metrics
    - pro.dividend(ts_code, start_date, end_date) → DataFrame with dividend history
    
    Code format: Tushare uses "000001.SZ", "600519.SH" format
    """

    def __init__(self) -> None:
        self._token: str = os.environ.get("TUSHARE_TOKEN", "")
        self._api: Any = None  # lazy-loaded tushare.pro_api instance
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], pd.DataFrame, None]:
        """Fetch a data metric from Tushare Pro.

        Args:
            metric: One of "stock_price", "index_daily", "fund_nav", etc.
            **params:
                - symbol: Code to fetch (e.g. "000001" for stocks, "399001" for indices)
                - start_date: "YYYYMMDD" format
                - end_date: "YYYYMMDD" format
                - ts_code: Alternative parameter name (will use symbol if provided)

        Returns:
            DataFrame or dict with requested data, or None on failure (never raises).
        """
        if metric not in _SUPPORTED_METRICS:
            return None
        if not self.is_available():
            return None

        try:
            if metric == "stock_price":
                return self._fetch_stock_price(**params)
            elif metric == "index_daily":
                return self._fetch_index_daily(**params)
            elif metric == "fund_nav":
                return self._fetch_fund_nav(**params)
            elif metric == "income_statement":
                return self._fetch_income_statement(**params)
            elif metric == "balance_sheet":
                return self._fetch_balance_sheet(**params)
            elif metric == "valuation":
                return self._fetch_valuation(**params)
            elif metric == "dividend":
                return self._fetch_dividend(**params)
            elif metric == "stock_basic_info":
                return self._fetch_stock_basic_info(**params)
            elif metric == "ths_hot_concepts":
                return self._fetch_ths_hot_concepts(**params)
            elif metric == "ths_concept_members":
                return self._fetch_ths_concept_members(**params)
        except Exception as e:
            logger.debug(f"TushareProvider.fetch({metric}) failed: {e}")

        return None

    def _fetch_stock_price(self, **params: Any) -> Any:
        """Fetch daily stock price bars."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_stock_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} stock price rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.daily failed: {e}")

        return None

    def _fetch_index_daily(self, **params: Any) -> Any:
        """Fetch daily index bars."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_index_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        # Index codes: 399001=深证成指, 000001=沪深300, 000300=沪深300, 399006=创业板指
        ts_code = self._normalize_code(symbol, "index")
        api = self._get_api()

        try:
            df = api.index_daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} index rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.index_daily failed: {e}")

        return None

    def _fetch_fund_nav(self, **params: Any) -> Any:
        """Fetch fund NAV.

        v9.9.x FIX 2026-09-01（任务#8/#3 排查发现）：此前直接返回
        pro.fund_nav() 的原始英文列名（ts_code/nav_date/unit_nav/accum_nav），
        但下游唯一消费方 services/fund_monitor.py::get_fund_nav_history()
        是按 AKShare `fund_open_fund_info_em()` 的中文列名读取的
        （"净值日期"/"单位净值"/"累计净值"/"日增长率"）——因为 AkshareProvider
        的 fund_nav 此前一直不在 _SUPPORTED_METRICS 白名单里（见
        akshare_provider.py 对应注释），这条 Tushare 降级路径其实是
        **唯一真正在生产环境跑的路径**，列名不兼容导致净值/日期长期解析
        成 None/空字符串，已影响真实持仓用户的净值展示。

        修复：在这里做列名转换，输出跟 AKShare 格式兼容的 DataFrame，
        无论调用方后续走 AkshareProvider（已重新加入白名单）还是这条
        Tushare 降级路径，下游都能正确解析。
        """
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_fund_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "fund")
        api = self._get_api()

        try:
            df = api.fund_nav(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                # 按 nav_date 升序排列（Tushare 默认可能是降序，AKShare
                # 惯例是升序，下游 df.tail(days) 假定升序取"最近N天"）
                df = df.sort_values("nav_date").reset_index(drop=True)
                # 计算日增长率（Tushare 原始数据没有这个字段，AKShare 有）
                df["日增长率"] = df["unit_nav"].pct_change().round(4) * 100
                df = df.rename(columns={
                    "nav_date": "净值日期",
                    "unit_nav": "单位净值",
                    "accum_nav": "累计净值",
                })
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} fund NAV rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.fund_nav failed: {e}")

        return None

    def _fetch_income_statement(self, **params: Any) -> Any:
        """Fetch income statement (financial metrics)."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_income_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.income(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} income statement rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.income failed: {e}")

        return None

    def _fetch_balance_sheet(self, **params: Any) -> Any:
        """Fetch balance sheet (financial metrics)."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_balance_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.balancesheet(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} balance sheet rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.balancesheet failed: {e}")

        return None

    def _fetch_valuation(self, **params: Any) -> Any:
        """Fetch valuation metrics."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_valuation_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            # Using valuation_daily for daily valuation metrics
            df = api.valuation_daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} valuation rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.valuation_daily failed: {e}")

        return None

    def _fetch_dividend(self, **params: Any) -> Any:
        """Fetch dividend history."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_dividend_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.dividend(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} dividend rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.dividend failed: {e}")

        return None

    def _fetch_stock_basic_info(self, **params: Any) -> Any:
        """Fetch stock basic info (industry/name/listing date/market cap).

        v9.9.x FIX 2026-09-01（任务#1）：用于替换 AKShare
        stock_individual_info_em()。返回 [item, value] 两列 DataFrame
        （与 AKShare 版本格式一致），拼接 pro.stock_basic()（行业/名称/
        上市时间，一次调用即可）+ pro.daily_basic(limit=1)（最新市值，
        可选加分项，失败不影响主字段）。

        Args:
            symbol / ts_code: 股票代码，6位数字或已格式化的 ts_code

        Returns:
            DataFrame with columns [item, value]。None on failure。
        """
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            basic_df = api.stock_basic(
                ts_code=ts_code,
                fields="ts_code,name,industry,list_date,market",
            )
            if basic_df is None or len(basic_df) == 0:
                return None

            row = basic_df.iloc[0]
            items: list = [
                ("股票代码", symbol),
                ("股票简称", row.get("name")),
                ("行业", row.get("industry")),
                ("上市时间", row.get("list_date")),
            ]

            # 市值是可选加分项，daily_basic 偶发失败不应影响主字段（行业等）
            try:
                mv_df = api.daily_basic(
                    ts_code=ts_code, limit=1,
                    fields="ts_code,trade_date,total_mv,circ_mv",
                )
                if mv_df is not None and len(mv_df) > 0:
                    mv_row = mv_df.iloc[0]
                    # Tushare total_mv/circ_mv 单位是万元，AKShare 原始
                    # 单位是元，×10000 换算对齐
                    if mv_row.get("total_mv") is not None:
                        items.append(("总市值", float(mv_row["total_mv"]) * 10000))
                    if mv_row.get("circ_mv") is not None:
                        items.append(("流通市值", float(mv_row["circ_mv"]) * 10000))
            except Exception as mv_e:
                logger.debug(f"Tushare pro.daily_basic(市值) for {symbol} failed "
                             f"(不影响行业等主字段): {mv_e}")

            df = pd.DataFrame(items, columns=["item", "value"])
            logger.debug(f"TushareProvider fetched stock_basic_info for {symbol}: "
                         f"industry={row.get('industry')}")
            return df
        except Exception as e:
            logger.debug(f"Tushare pro.stock_basic failed for {symbol}: {e}")

        return None

    def _get_ths_concept_index_map(self) -> Any:
        """获取"同花顺概念名称 → ts_code"全量映射（30分钟缓存）。

        v9.9.x FIX 2026-09-01（任务#3）：`pro.ths_index(exchange='A',
        type='N')` 返回全部395个同花顺概念指数（type='N'即概念板块，
        区别于 type='I' 行业指数、type='R' 地域指数——2026-09-01 实测
        确认 type='N' 的题材名称如"数据安全"/"人工智能"/"军工" 与
        AKShare 原 stock_board_concept_name_ths() 返回的题材名称体系
        高度一致）。这里缓存整张映射表，避免 get_hot_concepts()/
        get_concept_stocks() 每次调用都全量拉取。

        Returns:
            DataFrame [ts_code, name, count, exchange, list_date, type]。
            None on failure。
        """
        cache_key = "ths_concept_index_map"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        api = self._get_api()
        try:
            df = api.ths_index(exchange="A", type="N")
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df, ttl=1800)  # 30分钟
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.ths_index failed: {e}")

        return None

    def _get_latest_ths_daily(self) -> Any:
        """获取最近一个**有数据**的交易日的全市场同花顺概念指数行情。

        v9.9.x FIX 2026-09-01（任务#3）：`pro.ths_daily(trade_date=今天)`
        存在 T+1 延迟——2026-09-01 实测当天查询 0 行，前一交易日
        （20260831）才有 1879 行数据。直接假设"最新交易日=今天"会导致
        get_hot_concepts() 100%返回空列表，因此这里从最近5个交易日
        倒着试，取第一个有数据的。

        Returns:
            DataFrame [ts_code, trade_date, ..., pct_change, ...]。
            None on failure（连续5个交易日都没数据，视为异常）。
        """
        cache_key = "ths_daily_latest"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        api = self._get_api()
        try:
            import datetime
            end_date = datetime.datetime.now().strftime("%Y%m%d")
            cal = api.trade_cal(exchange="SSE", is_open="1", end_date=end_date)
            trade_dates = sorted(cal["cal_date"].tolist(), reverse=True)[:5]

            for d in trade_dates:
                df = api.ths_daily(trade_date=d)
                if df is not None and len(df) > 0:
                    # 5分钟缓存（比 30分钟的概念映射短，因为盘中数据
                    # 会随交易日推进变化，避免"今天已收盘但仍用昨天
                    # 缓存"的问题——不过 5分钟对日频数据来说仍然足够，
                    # 只是留了更快感知新交易日数据到位的余地）
                    _financial_cache.set(cache_key, df, ttl=300)
                    return df
        except Exception as e:
            logger.debug(f"Tushare pro.ths_daily failed: {e}")

        return None

    def _fetch_ths_hot_concepts(self, **params: Any) -> Any:
        """Fetch THS hot concept boards ranked by pct_change (涨跌幅降序)。

        v9.9.x FIX 2026-09-01（任务#3）：替换 AKShare
        stock_board_concept_name_ths()（该接口本身仍可用，但配套的
        成分股接口 stock_board_concept_cons_ths() 已被库删除，两者
        必须配套才有意义，故整体切换到 Tushare 保持数据源一致）。

        实现：ths_index(type='N') 拿题材名称/成分股数量的静态信息，
        join ths_daily(最近有数据的交易日) 拿涨跌幅，按涨跌幅降序。

        Args:
            limit: 返回条数

        Returns:
            list[dict]，字段名对齐 AKShare 原格式（板块名称/涨跌幅/
            成分股数量），额外带 ts_code（供 _fetch_ths_concept_members
            使用，不影响下游消费逻辑，多出的字段忽略即可）。
            None on failure。
        """
        limit = params.get("limit", 30)

        idx_df = self._get_ths_concept_index_map()
        daily_df = self._get_latest_ths_daily()
        if idx_df is None or daily_df is None:
            return None

        try:
            merged = idx_df.merge(daily_df, on="ts_code", how="inner")
            if merged is None or len(merged) == 0:
                return None
            merged = merged.sort_values("pct_change", ascending=False)
            result = []
            for _, row in merged.head(limit).iterrows():
                result.append({
                    "板块名称": row.get("name"),
                    "涨跌幅": row.get("pct_change"),
                    "成分股数量": row.get("count"),
                    "ts_code": row.get("ts_code"),
                })
            logger.debug(f"TushareProvider fetched {len(result)} ths hot concepts")
            return result
        except Exception as e:
            logger.debug(f"Tushare ths_hot_concepts merge failed: {e}")

        return None

    def _fetch_ths_concept_members(self, **params: Any) -> Any:
        """Fetch constituent stocks of a THS concept board (题材成分股)。

        v9.9.x FIX 2026-09-01（任务#3）：替换已死的 AKShare
        stock_board_concept_cons_ths()。

        Args:
            concept_name: 题材名称（如"数据安全"），需要先在 ths_index
                映射表里查到对应 ts_code，再用 ts_code 查成分股（Tushare
                ths_member 只接受 ts_code，不接受名称）。

        Returns:
            list[str]，6位股票代码列表。None on failure 或题材名称
            未命中映射表（说明不是一个有效的同花顺概念名称）。
        """
        concept_name = params.get("concept_name") or params.get("symbol")
        if not concept_name:
            return None

        idx_df = self._get_ths_concept_index_map()
        if idx_df is None:
            return None

        matched = idx_df[idx_df["name"] == concept_name]
        if len(matched) == 0:
            logger.debug(f"THS concept '{concept_name}' 未在 ths_index 映射表中命中")
            return None

        ts_code = matched.iloc[0]["ts_code"]
        api = self._get_api()
        try:
            df = api.ths_member(ts_code=ts_code)
            if df is None or len(df) == 0:
                return None
            codes = [str(c).split(".")[0].zfill(6) for c in df["con_code"].tolist() if c]
            logger.debug(f"TushareProvider fetched {len(codes)} members for concept "
                         f"'{concept_name}' (ts_code={ts_code})")
            return codes
        except Exception as e:
            logger.debug(f"Tushare pro.ths_member failed for {concept_name}: {e}")

        return None

    def is_available(self) -> bool:
        """Check if Tushare token is configured and API is reachable."""
        if self._available is not None:
            return self._available
        if not self._token:
            self._available = False
            return False
        try:
            api = self._get_api()
            # Test API with a simple call - if token is invalid, this will raise
            # We can verify by checking if we got an api instance
            self._available = api is not None
        except Exception:
            self._available = False
        return self._available

    @property
    def provider_name(self) -> str:
        return "tushare"

    def _get_api(self) -> Any:
        """Lazy-import tushare and initialize pro_api."""
        if self._api is None:
            import tushare as ts  # noqa: delayed import
            ts.set_token(self._token)
            self._api = ts.pro_api()
        return self._api

    def _normalize_code(self, code: str, asset_type: str = "stock") -> str:
        """Convert 6-digit A-share code to Tushare format.

        Examples:
            "000001" + "stock" → "000001.SZ"
            "600519" + "stock" → "600519.SH"
            "399001" + "index" → "399001.SZ"
            "sh000300" + "index" → "000300.SH"
            "sz399001" + "index" → "399001.SZ"

        Args:
            code: 6-digit code, sh/sz prefixed code, or already-formatted code
            asset_type: "stock", "index", or "fund"

        Returns:
            Tushare-formatted code
        """
        # If already formatted (contains dot), return as-is
        if "." in code:
            return code

        # 处理 sh/sz 前缀（如 "sh000300" → "000300"，保留交易所信息）
        exchange_hint = ""
        if code.startswith("sh"):
            exchange_hint = "SH"
            code = code[2:]
        elif code.startswith("sz"):
            exchange_hint = "SZ"
            code = code[2:]

        if len(code) == 6 and code.isdigit():
            if asset_type == "stock":
                if code.startswith(("0", "3")):
                    return f"{code}.SZ"
                elif code.startswith("6"):
                    return f"{code}.SH"
                elif code.startswith(("8", "4")):
                    return f"{code}.BJ"
            elif asset_type == "index":
                # 如果有 exchange_hint，直接用
                if exchange_hint:
                    return f"{code}.{exchange_hint}"
                # Index codes: 399001, 000001, etc. follow different conventions
                if code.startswith("399"):
                    return f"{code}.SZ"  # 深证指数
                elif code.startswith(("000", "999")):
                    return f"{code}.SH"  # 沪市指数
                else:
                    return f"{code}.SZ"  # default
            elif asset_type == "fund":
                # Fund codes typically start with 1 or 5
                if code.startswith("1"):
                    return f"{code}.SZ"
                elif code.startswith("5"):
                    return f"{code}.SH"

        # Already formatted or unknown - use exchange hint if available
        if exchange_hint:
            return f"{code}.{exchange_hint}"
        return code
