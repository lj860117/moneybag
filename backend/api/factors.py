"""
资金面 / 利率因子 API
=====================
北向资金、融资融券、国债收益率、SHIBOR、股息率、情绪、主力资金流。

Design doc: docs/design/12-framework-refactor.md §二
"""
from datetime import datetime
from fastapi import APIRouter

from services.data_layer import (
    get_northbound_flow, get_margin_trading,
    get_treasury_yield, get_shibor, get_dividend_yield,
    get_news_sentiment_score, get_main_money_flow,
)

router = APIRouter(tags=["因子数据"])


@router.get("/api/factors/northbound")
def get_northbound_api():
    """北向资金数据"""
    return get_northbound_flow()


@router.get("/api/factors/margin")
def get_margin_api():
    """融资融券数据"""
    return get_margin_trading()


@router.get("/api/factors/treasury")
def get_treasury_api():
    """国债收益率 / 股债性价比"""
    return get_treasury_yield()


@router.get("/api/factors/shibor")
def get_shibor_api():
    """SHIBOR 银行间利率"""
    return get_shibor()


@router.get("/api/factors/dividend")
def get_dividend_api():
    """股息率数据"""
    return get_dividend_yield()


@router.get("/api/factors/sentiment")
def get_sentiment_api():
    """LLM 新闻情绪评分"""
    return get_news_sentiment_score()


@router.get("/api/factors/all")
def get_all_factors():
    """一次性获取全部新因子数据"""
    return {
        "northbound": get_northbound_flow(),
        "margin": get_margin_trading(),
        "treasury": get_treasury_yield(),
        "shibor": get_shibor(),
        "dividend": get_dividend_yield(),
        "sentiment": get_news_sentiment_score(),
        "mainFlow": get_main_money_flow(),
        "updatedAt": datetime.now().isoformat(),
    }


@router.get("/api/factors/main-flow")
def get_main_flow_api():
    """主力资金流向（今日全市场主力净流入TOP5/流出TOP5）"""
    return get_main_money_flow()
