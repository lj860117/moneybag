"""
券商 parser 注册表 + 统一入口
==============================
新增券商 = 新增一个 parser 文件 + 在此注册，不改主逻辑。

Design doc: docs/design/m7-plus/01-batch-m7-external-sync.md §3
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

from domain.models.transaction import Transaction
from infra.data_source.import_.brokers.base_broker_parser import (
    BaseBrokerParser,
    UnsupportedBrokerError,
)


# 券商 parser 注册表（懒加载，避免循环导入）
_REGISTRY: dict[str, type[BaseBrokerParser]] = {}


def register_broker(parser_cls: type[BaseBrokerParser]) -> type[BaseBrokerParser]:
    """装饰器：注册一个券商 parser"""
    # 实例化一次以获取 broker_name
    instance = parser_cls()
    _REGISTRY[instance.broker_name] = parser_cls
    return parser_cls


def get_supported_brokers() -> list[str]:
    """返回所有已注册的券商标识列表"""
    _ensure_loaded()
    return list(_REGISTRY.keys())


def get_parser(broker_name: str) -> BaseBrokerParser:
    """获取指定券商的 parser 实例"""
    _ensure_loaded()
    parser_cls = _REGISTRY.get(broker_name)
    if parser_cls is None:
        supported = ", ".join(_REGISTRY.keys()) or "（暂无）"
        raise UnsupportedBrokerError(
            f"不支持的券商: '{broker_name}'。当前支持: {supported}"
        )
    return parser_cls()


def parse_broker_file(
    broker_name: str,
    file_path: Union[str, Path],
) -> list[Transaction]:
    """
    统一入口函数。

    参数:
        broker_name: 券商标识（如 "changjiang", "huatai"）
        file_path: 文件路径（CSV 或 Excel）
    返回:
        List[Transaction] — 解析后的交易记录列表
    异常:
        UnsupportedBrokerError — 不支持的券商
        ParseError — 解析失败（编码/格式问题）
    """
    parser = get_parser(broker_name)
    return parser.parse_file(file_path)


def _ensure_loaded() -> None:
    """确保所有 parser 模块已导入（触发 @register_broker 装饰器）"""
    if _REGISTRY:
        return
    # 导入所有已知的 parser 模块
    try:
        import infra.data_source.import_.brokers.changjiang_parser  # noqa: F401
    except ImportError:
        pass
