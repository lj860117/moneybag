"""
券商流水导入 API
================
文件上传 → parser 解析 → 校验 → 返回结果。

Design doc: docs/design/m7-plus/01-batch-m7-external-sync.md §6
"""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from infra.data_source.import_.brokers.registry import get_supported_brokers
from infra.data_source.import_.brokers.base_broker_parser import (
    ParseError,
    UnsupportedBrokerError,
)
from use_cases.sync_broker_statement import SyncBrokerStatementUseCase

router = APIRouter(tags=["券商流水导入"])


@router.get("/api/broker-import/supported")
def api_broker_import_supported():
    """获取支持的券商列表"""
    return {"brokers": get_supported_brokers()}


@router.post("/api/broker-import/upload")
async def api_broker_import_upload(
    file: UploadFile = File(...),
    broker_name: str = Form(...),
    user_id: str = Form(...),
):
    """
    上传券商流水文件并解析。

    参数:
        file: 上传的 CSV/Excel 文件
        broker_name: 券商标识（如 "changjiang"）
        user_id: 用户 ID
    返回:
        解析结果（成功数、失败数、失败详情）
    """
    # 校验文件类型
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")

    suffix = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in ("csv", "xlsx", "xls"):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: .{suffix}（支持 .csv / .xlsx / .xls）",
        )

    # 读取文件内容
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空")

    # 执行导入
    try:
        use_case = SyncBrokerStatementUseCase()
        result = use_case.execute_from_bytes(
            broker_name=broker_name,
            file_bytes=file_bytes,
            filename=file.filename,
            user_id=user_id,
        )
    except UnsupportedBrokerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ParseError as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {e}")

    # 构造响应
    failed_summary = [
        {"reason": f.reason, "rule": f.rule, "code": f.transaction.code, "date": str(f.transaction.trade_date)}
        for f in result.failed_details[:50]  # 最多返回 50 条失败详情
    ]

    return {
        "imported_count": result.imported_count,
        "duplicate_count": result.duplicate_count,
        "failed_count": result.failed_count,
        "warning_count": result.warning_count,
        "failed_details": failed_summary,
    }
