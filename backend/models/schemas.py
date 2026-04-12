"""
钱袋子 — 数据模型定义
所有 Pydantic 模型集中管理
"""
from typing import Optional, Literal
from pydantic import BaseModel, Field


# ---- V3 旧模型（兼容）----

class Holding(BaseModel):
    code: str
    name: str
    category: str
    targetPct: float
    amount: float
    buyDate: str

class Portfolio(BaseModel):
    """V3 兼容 Portfolio — 旧的 holdings 快照模式"""
    holdings: list[Holding] = []
    history: list = []
    profile: Optional[str] = None
    amount: float = 0


# ---- V4 新模型 — 交易流水制 ----

class Transaction(BaseModel):
    id: str = ""
    type: Literal["BUY", "SELL", "DIVIDEND"] = "BUY"
    code: str
    name: str = ""
    amount: float = 0        # 买入金额（BUY 时）
    shares: float = 0        # 份额
    nav: float = 0           # 成交净值
    fee: float = 0           # 手续费
    date: str = ""           # 交易日期
    source: str = "manual"   # recommend|manual|ocr|topup
    note: str = ""

class Asset(BaseModel):
    id: str = ""
    type: Literal["cash", "property", "liability", "other"] = "cash"
    name: str = ""
    balance: float = 0       # cash/liability 用 balance
    value: float = 0         # property/other 用 value
    icon: str = ""
    updated: str = ""

class PortfolioV4(BaseModel):
    """V4 交易流水制 Portfolio"""
    transactions: list[Transaction] = []
    assets: list[Asset] = []
    profile: Optional[str] = None
    history: list = []
    version: int = 4


# ---- 请求模型 ----

class TransactionRequest(BaseModel):
    userId: str
    transaction: Transaction

class AssetRequest(BaseModel):
    userId: str
    asset: Asset

class TopupRequest(BaseModel):
    userId: str
    amount: float
    profile: Optional[str] = None
    allocations: list[dict] = []  # [{code, name, pct, amount}]

class UserData(BaseModel):
    userId: str
    portfolio: Optional[dict] = None  # 兼容 V3 和 V4
    ledger: list = []
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class ChatRequest(BaseModel):
    message: str
    portfolio: Optional[Portfolio] = None
    model: Optional[str] = None  # 前端可指定模型，如 "deepseek-chat"

class LedgerEntry(BaseModel):
    userId: str
    category: str
    amount: float
    note: str = ""
    date: Optional[str] = None
    direction: Literal["expense", "income"] = "expense"

class FundSearchResult(BaseModel):
    code: str
    name: str
    type: str = ""

class IncomeSourceCreate(BaseModel):
    userId: str
    name: str
    type: str = "其他"
    expectedAmt: float = 0
    note: str = ""

class IncomeSourceRecord(BaseModel):
    userId: str
    sourceId: str
    amount: float
