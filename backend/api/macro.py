"""
宏观经济 API
=============
M1、社融、LPR、市场涨跌、美林时钟、GDP、龙虎榜 等宏观数据。

Design doc: docs/design/12-framework-refactor.md §二
"""
from fastapi import APIRouter

from services.macro_extended import (
    get_m1_data, get_social_financing, get_lpr_rate,
    get_market_activity, get_merrill_lynch_clock,
)
from services.macro_v8 import (
    get_gdp, get_lhb_summary, get_all_v8_macro,
)

router = APIRouter(tags=["宏观数据"])


@router.get("/api/macro/m1")
def macro_m1():
    """M1 货币供应量 + M1-M2 剪刀差"""
    return get_m1_data()


@router.get("/api/macro/social-financing")
def macro_shrzgm():
    """社会融资规模"""
    return get_social_financing()


@router.get("/api/macro/lpr")
def macro_lpr():
    """LPR 贷款市场报价利率"""
    return get_lpr_rate()


@router.get("/api/market/activity")
def market_activity():
    """市场涨跌家数/赚钱效应"""
    return get_market_activity()


@router.get("/api/macro/clock")
def merrill_clock():
    """美林时钟经济周期判断"""
    return get_merrill_lynch_clock()


@router.get("/api/macro/extended")
def macro_extended_all():
    """一次性获取所有扩展宏观数据"""
    return {
        "m1": get_m1_data(),
        "social_financing": get_social_financing(),
        "lpr": get_lpr_rate(),
        "activity": get_market_activity(),
        "clock": get_merrill_lynch_clock(),
    }


@router.get("/api/macro/v8")
def macro_v8_all():
    """V8 全部扩展宏观因子（GDP/工业增加值/社零/固投/龙虎榜/管理层增减持）"""
    return get_all_v8_macro()


@router.get("/api/macro/gdp")
def macro_gdp():
    return get_gdp()


@router.get("/api/macro/lhb")
def macro_lhb():
    """龙虎榜（机构+游资动向）"""
    return get_lhb_summary()
