"""
国内政策数据 API
=================
房地产、房价指数、政策新闻、政策影响分析。

Design doc: docs/design/12-framework-refactor.md §二
"""
from fastapi import APIRouter

from services.policy_data import (
    get_real_estate_data, get_house_price_index,
    get_policy_news_by_topic, get_all_policy_topics,
    analyze_policy_impact_ds,
)

router = APIRouter(tags=["政策数据"])


@router.get("/api/policy/real-estate")
def policy_real_estate():
    """房地产开发投资/销售数据"""
    return get_real_estate_data()


@router.get("/api/policy/house-price")
def policy_house_price():
    """70城新房价格指数"""
    return get_house_price_index()


@router.get("/api/policy/news")
def policy_news_by_topic(topic: str = "房地产", limit: int = 5):
    """按主题获取政策新闻"""
    return get_policy_news_by_topic(topic, limit)


@router.get("/api/policy/all-topics")
def policy_all_topics():
    """一次性获取全部主题政策新闻"""
    return get_all_policy_topics()


@router.get("/api/policy/impact")
def policy_impact():
    """DeepSeek 分析政策→A股影响"""
    return analyze_policy_impact_ds()
