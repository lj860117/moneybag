"""
钱袋子 — 数据层门面（Facade）
统一导出所有数据获取函数，保持向后兼容
"""
from services.market_data import (
    get_fund_nav, get_fear_greed_index, get_valuation_percentile,
    _get_nav_on_date, _nav_cache,
)
from services.technical import (
    calc_rsi, calc_macd, calc_bollinger, get_technical_indicators,
)
from services.news_data import (
    get_fund_news, get_market_news,
    get_policy_news, analyze_news_impact,
    _news_cache,
)
from services.macro_data import (
    get_macro_calendar, _macro_cache,
)
from services.factor_data import (
    get_northbound_flow, get_margin_trading, get_treasury_yield,
    get_shibor, get_dividend_yield, get_news_sentiment_score,
)
from services.fund_rank import (
    _load_fund_rank_data, get_fund_dynamic_info, _fund_rank_cache,
)
