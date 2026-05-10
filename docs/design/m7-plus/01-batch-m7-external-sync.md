# Batch 1：外部数据同步（M7）

> 来源：`14-m7-plus-enhancement-for-claude.md` §一「外部数据打通」
>
> **本文件为独立批次文档，包含开工所需全部信息。不需要翻阅原文。**

---

## 批次概览

| 属性 | 值 |
|---|---|
| 批次编号 | Batch 1 |
| 里程碑 | M7 |
| 产出文件 | `base_broker_parser.py`、`huatai_parser.py`（首家券商）、`bookkeeping_csv_parser.py`、`sync_broker_statement.py` |
| 需读文档 | 本文件 + `12-framework-refactor.md`（四层架构） |
| 📋 前置依赖 | 无（M7 首批） |
| 可并行 | 与 Batch 2（Glide Path）独立并行 |

---

## 详细设计

### 1. 问题与目标

**问题**：资产负债表、交易记录全靠手动录入 → 维护 friction 高 → 系统吃灰。

**目标**：把"每月维护一次"降到"每季度确认一次"。

### 2. 数据源规划

| 数据源 | 接入方式 | 自动填充字段 | 用户动作 |
|---|---|---|---|
| 券商流水 CSV | 上传文件 → **Strategy 模式解析** | 交易日期、代码、名称、金额、方向 | 上传 + 确认 |
| 记账软件（随手记/钱迹） | CSV 导入或开放 API | 月度收支、现金流 | 授权/导出 |
| 房贷计划 | 用户输入一次 | 剩余负债、月供 | 首次输入 |
| 公积金/社保 | 手动输入（M7 先不上 OCR） | 保障额度 | 按需更新 |

### 3. 券商 CSV 解析（Strategy 模式）

国内券商（华泰、中信、招商等）导出格式差异大，列名、编码、日期格式不统一。

**架构决策**：不写一个通用 parser 硬解析，按券商拆分 Strategy：

- 目录：`infra/data_source/import/brokers/`（`import/` 为 M7+ 新增子目录，与 `12-framework-refactor.md` 的 `market/fundamental/macro/alt/providers` 五分法平行，专用于用户数据导入解析）
- 文件：`huatai_parser.py`、`zhongxin_parser.py`、`zhaoshang_parser.py`...
- 统一接口：`parse_broker_csv(broker_name, file_path) → List[Transaction]`
- 新增券商支持 = 新增一个 parser 文件，不改主逻辑

**编码处理规范（基类职责）**：`base_broker_parser.py` 统一处理：

- 用 `chardet` 检测文件编码（国内券商常见 GBK/GB2312）
- 统一转 UTF-8 后再交给具体 parser 处理列映射
- 列名清洗：去除中文空格、全角符号，统一转半角
- **金额方向统一化**：基类负责矫正买入/卖出金额符号——统一为买入正数、卖出负数。券商导出格式不一致（有的买入为负代表资金流出），基类根据交易方向字段自动矫正，具体 parser 不处理正负
- **列名别名表**：每个 parser 声明列名别名映射（如 `{"成交金额": ["交易金额", "成交额", "金额"]}`），基类按别名表模糊匹配列名

首期支持 3-5 家主流券商，后续按需扩展。

### 4. 过期检测规则

阈值放 `defaults.py`，设计里为参考值：
- 自动同步数据标"自动"来源，手动数据标"手动"
- 自动数据 **AUTO_SYNC_EXPIRE_DAYS**（默认 90）天未更新 → 提醒"请重新同步"
- 手动数据 **MANUAL_SYNC_EXPIRE_DAYS**（默认 30）天未更新 → 标"可能过期"

### 5. 凌晨工厂任务接入

- 外部数据同步作为新步骤接入 `night_worker_pipeline.py`
- **接入方式**：挂在**阶段 1（数据采集）**内，作为数据采集的一个新来源（如阶段 1 从 `[tushare_data]` 扩展为 `[tushare_data, broker_sync]`）
- **前提假设**：M7 开工前需确认 `night_worker_pipeline.py` 的每个阶段内部是否支持"步骤列表"扩展。若不支持则需先重构（见 §九 验证 2）
- **步骤级错误隔离**：阶段内每个步骤独立 try-catch，单步骤失败记录日志并跳过，不阻塞同阶段其他步骤

### 6. 用户可见产出

- 交易录入页增加"导入券商流水"按钮
- 资产负债表增加"上次同步时间"标签

### 7. 数据校验层（解析后、入库前）

解析产出的 `List[Transaction]` 在写入主库前，必须通过统一校验管道。校验不通过的记录**隔离存储**，不污染主库。

**校验规则**：

| 规则 | 判定 | 说明 |
|---|---|---|
| 交易日期 ≤ 当前日期 | 🔴 不通过 | 未来日期必为错误 |
| 交易日期 ≥ 账户开户日（若已知） | 🟡 警告 | 开户日前的记录存疑，标记但不排除 |
| 交易金额 > 0 | 🔴 不通过 | 金额为 0 或负数（方向统一后仍为负）必为错误 |
| 证券代码非空且格式合法 | 🔴 不通过 | 空代码或明显非法格式（如纯中文）排除 |
| 同券商 + 同日期 + 同代码 + 同方向 + 同金额 | 🔴 去重 | 完全相同的记录视为重复导入，只保留一条 |
| 股票数量为正整数（基金可为小数） | 🟡 警告 | 标记但不排除，可能是拆分/合并场景 |

**隔离机制**：

- 校验不通过的记录存入 `failed_transactions` 表（或同结构的隔离存储），保留原始行数据（`raw_row`）供人工核对
- 页面显示："成功导入 X 条，Y 条解析失败（点击查看详情）"
- 隔离记录可由用户手动修正后重新提交

**接口**：

```python
def validate_transactions(
    transactions: list[Transaction],
    account_open_date: Optional[date] = None,
) -> ValidationResult:
    """
    校验交易记录列表。
    返回:
        ValidationResult(
            valid: list[Transaction],       # 通过校验的记录
            failed: list[FailedTransaction], # 未通过的记录（含原因）
            warnings: list[WarningTransaction], # 通过但有警告的记录
        )
    """

@dataclass
class FailedTransaction:
    """校验失败的交易记录"""
    transaction: Transaction     # 原始记录
    reason: str                  # 失败原因（人类可读）
    rule: str                    # 触发的规则标识
```

### 8. 验收标准

- 导入 1 份真实券商流水，解析准确率 >95%（代码/金额/方向）
- **新增**：导入包含错误数据的测试流水（日期错乱、金额为 0、重复行），系统能正确识别并隔离错误数据，主库不受污染

### 9. 依赖 M1-M6

- M2 资产负债表 MVP 先跑通
- M3 规则引擎稳定后接入

---

## 接口契约占位

### `Transaction` 数据结构

```python
@dataclass
class Transaction:
    """券商/记账软件导入的单笔交易记录"""
    trade_date: date           # 交易日期
    code: str                  # 证券代码（如 "510300"）
    name: str                  # 证券名称（如 "沪深300ETF"）
    direction: str             # 交易方向："buy" | "sell"
    amount: Decimal            # 交易金额（正数，方向由 direction 决定）
    quantity: Optional[int]    # 交易数量（份额），可为空
    fee: Optional[Decimal]     # 手续费，可为空
    source: str                # 数据来源标记："auto" | "manual"
    transaction_type: str      # 交易类型："manual" | "auto_invest" | "rebalance" | "dividend"
                               #   - manual: 手动交易（行为归因检测的唯一目标）
                               #   - auto_invest: 定投（不计入行为偏差统计）
                               #   - rebalance: 再平衡（不受任何行为风控限制）
                               #   - dividend: 分红（不计入交易统计）
                               # CSV 导入时默认为 "manual"，用户可在确认页修改
    broker: Optional[str]      # 券商名称（如 "huatai"），手动录入时为 None
    raw_row: Optional[dict]    # 原始行数据（调试用）
```

### `BaseBrokerParser` 基类接口

```python
class BaseBrokerParser(ABC):
    """券商 CSV 解析基类"""

    @abstractmethod
    def column_alias_map(self) -> dict[str, list[str]]:
        """列名别名映射，如 {"成交金额": ["交易金额", "成交额", "金额"]}"""
        ...

    @abstractmethod
    def parse_rows(self, df: pd.DataFrame) -> list[Transaction]:
        """解析已清洗的 DataFrame 为 Transaction 列表"""
        ...

    def parse_file(self, file_path: str) -> list[Transaction]:
        """基类实现：编码检测 → UTF-8 转换 → 列名清洗 → 金额方向统一 → 调用 parse_rows"""
        ...
```

### `parse_broker_csv` 统一入口

```python
def parse_broker_csv(broker_name: str, file_path: str) -> list[Transaction]:
    """
    统一入口函数。
    参数:
        broker_name: 券商标识（如 "huatai", "zhongxin"）
        file_path: CSV 文件路径
    返回:
        List[Transaction] — 解析后的交易记录列表
    异常:
        UnsupportedBrokerError — 不支持的券商
        ParseError — 解析失败（编码/格式问题）
    """
```

### `sync_broker_statement` 用例接口

```python
class SyncBrokerStatementUseCase:
    """同步券商流水到资产负债表"""

    def execute(
        self,
        broker_name: str,
        file_path: str,
        user_id: str,
    ) -> SyncResult:
        """
        执行同步：解析 CSV → 校验 → 去重 → 写入资产负债表。
        返回:
            SyncResult(imported_count, duplicate_count, failed_count, warning_count, failed_details)
        """
```

---

## 文件落位

| 文件路径 | 职责 | 预估行数 |
|---|---|---|
| `infra/data_source/import/brokers/base_broker_parser.py` | Strategy 接口 + 编码检测 + 金额方向统一 | <100 |
| `infra/data_source/import/brokers/huatai_parser.py` | 华泰证券 CSV 解析（首家） | <150 |
| `infra/data_source/import/bookkeeping_csv_parser.py` | 记账软件 CSV 解析 | <150 |
| `use_cases/sync_broker_statement.py` | 同步券商流水用例 | <150 |

---

## 🔗 跨批次耦合

| 被哪个批次引用 | 引用内容 | 说明 |
|---|---|---|
| Batch 5（行为归因检测） | `Transaction` 数据结构（特别是 `transaction_type` 字段） | 行为归因**只检测 `transaction_type="manual"` 的交易**，定投/再平衡/分红不计入偏差统计 |
| Batch 6（行为归因联动） | `Transaction` 数据结构 | 联动版通过交易记录检测追涨/过度交易等，间接依赖 |
| Batch 8（TradingView） | 无直接依赖 | TradingView 读 Tushare 日线数据，不依赖导入流水 |

---

## 📋 前置依赖

无。本批次为 M7 首批，可独立开工。

开工前需确认 §九 检查表中：
- 验证 2：`night_worker_pipeline.py` 阶段内是否支持步骤列表扩展

---

## 🚫 禁止假设

1. **不能假设其他券商 parser 已存在**——本批只实现华泰（首家），后续券商另开会话
2. **不能假设 `night_worker_pipeline.py` 支持步骤扩展**——需先验证（§九 验证 2）
3. **不能假设 `DataSourceProtocol` 已有导入接口**——本批自行定义 `BaseBrokerParser` 接口
4. **不能假设 OCR 能力**——M7 不上 OCR，公积金/社保走手动输入
5. **不能修改 M1-M6 已有文件**——`defaults.py` 仅可新增 `ExternalSyncDefaults` dataclass

---

## 券商 parser 扩展指引（Batch 1 后续）

Batch 1 只实现首家券商 parser（华泰），后续每新增一家券商 = 一个微型会话：

1. 读 `base_broker_parser.py`（接口定义）+ 已有 parser（参考格式）
2. 写一个新 parser 文件（<150 行）
3. 不需要读本文档全文

---

## ⚙️ 全局契约引用

- **凌晨工厂接入**：本批次的 `sync_broker_statement.py` 需实现 `NightWorkerStep` Protocol → 详见 [00-interface-contracts.md](00-interface-contracts.md) §一
- **defaults.py 新增**：本批次的 `ExternalSyncDefaults` dataclass 需遵循命名规范 → 详见 [00-interface-contracts.md](00-interface-contracts.md) §二
- **冲突处理**：实现过程中遇到需要修改 M1-M6 文件、Protocol 不够用等情况时 → **停止编码，按 [README.md](README.md) 冲突处理 SOP 记录冲突，等人工确认**
