"""
券商研报 API
=============
机构共识、最新研报、个股研报查询。

Design doc: docs/design/12-framework-refactor.md §二
"""
from fastapi import APIRouter

router = APIRouter(tags=["券商研报"])


@router.get("/api/broker/consensus")
def api_broker_consensus():
    """机构研报共识（多空比例+热门行业+关键风险）"""
    from services.broker_research import get_broker_consensus
    return get_broker_consensus()


@router.get("/api/broker/latest")
def api_broker_latest(limit: int = 20):
    """最新研报列表"""
    from services.broker_research import get_latest_reports
    return {"reports": get_latest_reports(limit=limit)}


@router.get("/api/broker/stock/{code}")
def api_broker_stock(code: str, limit: int = 5):
    """个股研报查询"""
    from services.broker_research import get_stock_reports
    return {"reports": get_stock_reports(code=code, limit=limit)}
