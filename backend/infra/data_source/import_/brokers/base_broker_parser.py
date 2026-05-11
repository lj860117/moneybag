"""
券商流水解析基类
================
Strategy 模式：每个券商一个 parser 文件，基类负责：
  - CSV / Excel 自动识别
  - 编码检测（chardet，GBK/GB2312 常见）
  - 列名清洗（中文空格、全角符号 → 半角）
  - 列名别名模糊匹配
  - 金额方向统一（买入正数、卖出负数）

Design doc: docs/design/m7-plus/01-batch-m7-external-sync.md §3
"""
from __future__ import annotations

import io
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Union

import pandas as pd  # type: ignore[import-untyped]

from domain.models.transaction import Transaction


class ParseError(Exception):
    """解析失败（编码/格式问题）"""
    pass


class UnsupportedBrokerError(Exception):
    """不支持的券商"""
    pass


def _normalize_column_name(name: str) -> str:
    """
    列名清洗：
      - 去除首尾空白
      - 全角字符 → 半角
      - 中文空格（　）→ 普通空格
      - 连续空格合并
    """
    if not isinstance(name, str):
        return str(name).strip()
    # 全角 → 半角（ASCII 范围对应的全角字符）
    result = ""
    for ch in name:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result += chr(code - 0xFEE0)
        elif ch == "　":
            result += " "
        else:
            result += ch
    return re.sub(r"\s+", " ", result).strip()


def _detect_encoding(file_bytes: bytes) -> str:
    """用 chardet 检测编码，默认 utf-8"""
    try:
        import chardet  # type: ignore[import-not-found]
        detection = chardet.detect(file_bytes[:10000])
        encoding = detection.get("encoding", "utf-8") or "utf-8"
        # chardet 可能返回 GB2312，统一用 gbk（超集）
        if encoding.lower() in ("gb2312", "gbk", "gb18030"):
            return "gbk"
        return encoding
    except ImportError:
        return "utf-8"


class BaseBrokerParser(ABC):
    """
    券商 CSV/Excel 解析基类。

    子类只需实现:
      1. broker_name: 券商标识（如 "changjiang"）
      2. column_alias_map(): 列名别名映射
      3. parse_rows(df): 解析已清洗的 DataFrame
    """

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """券商唯一标识（如 "changjiang", "huatai"）"""
        ...

    @abstractmethod
    def column_alias_map(self) -> dict[str, list[str]]:
        """
        列名别名映射。
        key = 标准字段名，value = 该券商可能使用的别名列表。
        如 {"成交金额": ["交易金额", "成交额", "金额"]}
        """
        ...

    @abstractmethod
    def parse_rows(self, df: pd.DataFrame) -> list[Transaction]:
        """
        解析已清洗（列名已统一）的 DataFrame 为 Transaction 列表。
        此时 df 的列名已经是标准字段名（column_alias_map 的 key）。
        """
        ...

    @property
    def sheet_name(self) -> Union[str, int]:
        """Excel 的 sheet 名（默认第一个 sheet）。子类可覆盖。"""
        return 0

    def parse_file(self, file_path: Union[str, Path]) -> list[Transaction]:
        """
        解析文件入口：
          1. 自动识别 CSV / Excel
          2. 编码检测 → UTF-8 转换
          3. 列名清洗 + 别名匹配
          4. 调用子类 parse_rows()
        """
        path = Path(file_path)
        if not path.exists():
            raise ParseError(f"文件不存在: {path}")

        df = self._read_file(path)
        df = self._clean_and_map_columns(df)
        return self.parse_rows(df)

    def parse_bytes(self, file_bytes: bytes, filename: str) -> list[Transaction]:
        """
        从字节流解析（用于 API 上传场景）。
        filename 用于判断文件类型（.csv / .xlsx / .xls）。
        """
        df = self._read_bytes(file_bytes, filename)
        df = self._clean_and_map_columns(df)
        return self.parse_rows(df)

    # ---- 内部方法 ----

    def _read_file(self, path: Path) -> pd.DataFrame:
        """根据扩展名读取文件为 DataFrame"""
        suffix = path.suffix.lower()
        if suffix in (".xlsx", ".xls"):
            return self._read_excel(path)
        elif suffix == ".csv":
            return self._read_csv(path)
        else:
            raise ParseError(f"不支持的文件格式: {suffix}（支持 .csv / .xlsx / .xls）")

    def _read_bytes(self, file_bytes: bytes, filename: str) -> pd.DataFrame:
        """从字节流读取"""
        suffix = Path(filename).suffix.lower()
        if suffix in (".xlsx", ".xls"):
            return pd.read_excel(io.BytesIO(file_bytes), sheet_name=self.sheet_name)
        elif suffix == ".csv":
            encoding = _detect_encoding(file_bytes)
            text = file_bytes.decode(encoding, errors="replace")
            return pd.read_csv(io.StringIO(text))
        else:
            raise ParseError(f"不支持的文件格式: {suffix}（支持 .csv / .xlsx / .xls）")

    def _read_excel(self, path: Path) -> pd.DataFrame:
        """读取 Excel 文件"""
        try:
            return pd.read_excel(path, sheet_name=self.sheet_name)
        except Exception as e:
            raise ParseError(f"读取 Excel 失败: {e}") from e

    def _read_csv(self, path: Path) -> pd.DataFrame:
        """读取 CSV（自动检测编码）"""
        try:
            file_bytes = path.read_bytes()
            encoding = _detect_encoding(file_bytes)
            text = file_bytes.decode(encoding, errors="replace")
            return pd.read_csv(io.StringIO(text))
        except Exception as e:
            raise ParseError(f"读取 CSV 失败: {e}") from e

    def _clean_and_map_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        列名清洗 + 别名映射：
          1. 清洗所有列名（全角→半角、去空格）
          2. 按 column_alias_map 把别名映射为标准名
        """
        # 清洗原始列名
        cleaned_cols = {col: _normalize_column_name(col) for col in df.columns}
        df = df.rename(columns=cleaned_cols)

        # 别名映射
        alias_map = self.column_alias_map()
        rename_map: dict[str, str] = {}
        current_cols = set(df.columns)

        for standard_name, aliases in alias_map.items():
            # 如果标准名已经存在，无需映射
            if standard_name in current_cols:
                continue
            # 尝试从别名中找到匹配
            for alias in aliases:
                normalized_alias = _normalize_column_name(alias)
                if normalized_alias in current_cols:
                    rename_map[normalized_alias] = standard_name
                    current_cols.discard(normalized_alias)
                    current_cols.add(standard_name)
                    break

        if rename_map:
            df = df.rename(columns=rename_map)

        return df
