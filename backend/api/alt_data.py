"""
另类数据 API
=============
北向资金详情、融资融券详情、龙虎榜、大宗交易、行业资金流。

Design doc: docs/design/12-framework-refactor.md §二
"""
from fastapi import APIRouter

router = APIRouter(tags=["另类数据"])


@router.get("/api/alt-data/dashboard")
def api_alt_data_dashboard():
    """另类数据综合仪表盘"""
    from services.alt_data import get_alt_data_dashboard
    return get_alt_data_dashboard()


@router.get("/api/alt-data/northbound")
def api_alt_northbound():
    """北向资金详情"""
    from services.alt_data import get_northbound_flow_detail
    return get_northbound_flow_detail()


@router.get("/api/alt-data/margin")
def api_alt_margin():
    """融资融券详情"""
    from services.alt_data import get_margin_detail
    return get_margin_detail()


@router.get("/api/alt-data/dragon-tiger")
def api_alt_dragon_tiger():
    """龙虎榜"""
    from services.alt_data import get_dragon_tiger
    return get_dragon_tiger()


@router.get("/api/alt-data/block-trade")
def api_alt_block_trade():
    """大宗交易"""
    from services.alt_data import get_block_trade
    return get_block_trade()


@router.get("/api/alt-data/sector-flow")
def api_alt_sector_flow():
    """行业资金流"""
    from services.alt_data import get_sector_flow
    return get_sector_flow()
