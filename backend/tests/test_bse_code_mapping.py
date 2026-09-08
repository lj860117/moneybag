"""
北交所（BSE）股票代码映射回归测试
=================================

背景（2026-09-09 线上调研）：`920826`（盖世食品）/ `920982`（锦波生物）
是北交所 2024 年起启用的新码段。它们经 Tushare `report_rc` 进入推荐候选池
（`recommend_engine.py` 取 `code.split(".")[0]` 后只剩 6 位裸码），随后在
三级降级链上 **100% 失败**：

  L1 Tushare   `_code_to_ts()`    920826 → 920826.SZ（错交易所）→ 返回空
  L2 AKShare   `stock_price` 不在 `_SUPPORTED_METRICS` → 即时 None
                                  （该级对全市场失效，非 920 特有）
  L3 Baostock  `_normalize_code()` 920826 → 原样返回裸码 → baostock 报
                                   「股票代码应为9位，请检查。格式示例：sh.600000。」

实测：`data/night_worker/cron.log` 里「股票代码应为9位」与
「[STOCK_PROVIDER] … 所有源失败」各 8 次，一一对应，920 成功率 0%。
后果不是报错，而是**静默产出内容错误的推荐卡片**：价格缺失被当成中性分
（technical=50 / risk=45）填进 Top10，08:30 推给用户。

本文件锁定 L1 / L3 两处映射，并守住既有沪深映射不被改坏。

设计原则：**不复制实现里的分支表**，直接调用真实的 `_code_to_ts()` 与
`BaostockProvider._normalize_code()` —— 实现一改、测试立刻能感知，不会
变成「改了实现还绿」的死测试。
"""
from __future__ import annotations

from typing import List, Tuple

import pytest

from infra.data_source.providers.baostock_provider import BaostockProvider
from services.tushare_data import _code_to_ts


# ============================================================
# 用例表
# ============================================================
# 北交所新码段（本次修复目标）
BSE_NEW_SEGMENT: List[str] = ["920826", "920982"]
# 北交所老码段（原新三板精选层平移，修复前已正确，防回归）
BSE_LEGACY_SEGMENT: List[str] = ["830799", "430047"]

# (输入, 期望 Tushare 格式, 期望 Baostock 格式)
MAPPING_CASES: List[Tuple[str, str, str]] = [
    # ---- 北交所新码段：920（本次修的目标）----
    ("920826", "920826.BJ", "bj.920826"),   # 盖世食品
    ("920982", "920982.BJ", "bj.920982"),   # 锦波生物
    # ---- 北交所老码段：8 / 4 ----
    ("830799", "830799.BJ", "bj.830799"),
    ("430047", "430047.BJ", "bj.430047"),
    # ---- 沪市：6 ----
    ("600000", "600000.SH", "sh.600000"),
    ("600519", "600519.SH", "sh.600519"),
    ("688111", "688111.SH", "sh.688111"),   # 科创板
    # ---- 深市：0 / 3 ----
    ("000001", "000001.SZ", "sz.000001"),
    ("300750", "300750.SZ", "sz.300750"),
    ("002594", "002594.SZ", "sz.002594"),
]

# 已带交易所后缀的代码：两个 provider 都必须原样返回，
# 不能被二次加工成 920826.BJ.BJ / bj.bj.920826
DOTTED_CASES: List[str] = [
    "920826.BJ",
    "920982.BJ",
    "600000.SH",
    "000001.SZ",
    "300750.SZ",
]

# baostock 专有的「前缀无点号」写法（8 位）→ 须正常补点
BAOSTOCK_PREFIX_CASES: List[Tuple[str, str]] = [
    ("sh600000", "sh.600000"),
    ("sz000001", "sz.000001"),
]


@pytest.fixture(scope="module")
def baostock_provider() -> BaostockProvider:
    """构造 BaostockProvider 实例。

    `__init__` 只置三个属性、不做任何 I/O（baostock 是 fetch() 时才延迟导入），
    因此在本地/CI 没有安装 baostock 的环境下也能安全实例化。
    """
    return BaostockProvider()


# ============================================================
# 1. 北交所映射（本次修复）
# ============================================================
@pytest.mark.parametrize("code", BSE_NEW_SEGMENT)
def test_bse_new_segment_maps_to_bj_tushare(code: str) -> None:
    """920xxx 必须映射到 .BJ —— 修复前是 .SZ，Tushare 必然返回空。"""
    assert _code_to_ts(code) == f"{code}.BJ", (
        f"{code} 未映射到 .BJ（实际 {_code_to_ts(code)}）—— "
        f"Tushare 会按错误交易所查询，三级降级链 100% 失败"
    )


@pytest.mark.parametrize("code", BSE_NEW_SEGMENT)
def test_bse_new_segment_maps_to_bj_baostock(code: str, baostock_provider) -> None:
    """920xxx 必须映射成 bj. 前缀 —— 修复前原样返回裸码，baostock 直接拒绝。"""
    got = baostock_provider._normalize_code(code)
    assert got == f"bj.{code}", (
        f"{code} 未映射成 bj. 前缀（实际 {got}）—— "
        f"baostock 会报「股票代码应为9位，请检查」，降级链最后一级也断"
    )


@pytest.mark.parametrize("code", BSE_LEGACY_SEGMENT)
def test_bse_legacy_segment_still_maps_to_bj(code: str, baostock_provider) -> None:
    """8/4 开头的北交所老码段不能被 920 分支改坏。"""
    assert _code_to_ts(code) == f"{code}.BJ"
    assert baostock_provider._normalize_code(code) == f"bj.{code}"


# ============================================================
# 2. 回归：沪深映射必须保持原样
# ============================================================
@pytest.mark.parametrize("code,expected_ts,expected_bs", MAPPING_CASES)
def test_tushare_mapping_matches_expectation(
    code: str, expected_ts: str, expected_bs: str
) -> None:
    """Tushare 侧完整映射表（含沪深回归项）。"""
    assert _code_to_ts(code) == expected_ts


@pytest.mark.parametrize("code,expected_ts,expected_bs", MAPPING_CASES)
def test_baostock_mapping_matches_expectation(
    code: str, expected_ts: str, expected_bs: str, baostock_provider
) -> None:
    """Baostock 侧完整映射表（含沪深回归项）。"""
    assert baostock_provider._normalize_code(code) == expected_bs


def test_two_providers_agree_on_exchange(baostock_provider) -> None:
    """两个 provider 对同一个代码判定的交易所必须一致。

    Tushare 用 `.BJ/.SH/.SZ`，baostock 用 `bj./sh./sz.`。若两者不一致，
    说明某一边又漏了分支 —— 比逐条断言更能抓住新增码段的遗漏。
    """
    for code, expected_ts, expected_bs in MAPPING_CASES:
        ts_exchange = _code_to_ts(code).split(".")[-1]
        bs_exchange = baostock_provider._normalize_code(code).split(".")[0]
        assert ts_exchange.lower() == bs_exchange, (
            f"{code}: Tushare 判 {ts_exchange}，baostock 判 {bs_exchange}，两边不一致"
        )


# ============================================================
# 3. 边界：带后缀传入应原样返回，不得二次加工
# ============================================================
@pytest.mark.parametrize("code", DOTTED_CASES)
def test_dotted_code_is_returned_as_is(code: str, baostock_provider) -> None:
    """已带交易所后缀的代码不得被重复加后缀。

    `_code_to_ts()` 顶部有 `if "." in code: return code`，
    `_normalize_code()` 顶部有 `if "." in code and len(code) == 9`，
    两者都必须保持原样返回。
    """
    assert _code_to_ts(code) == code
    assert baostock_provider._normalize_code(code) == code


@pytest.mark.parametrize("code,expected_bs", BAOSTOCK_PREFIX_CASES)
def test_baostock_prefixed_code_gets_a_dot(
    code: str, expected_bs: str, baostock_provider
) -> None:
    """baostock 的「sh600000」写法（前缀无点号，8 位）须补成 sh.600000。"""
    assert baostock_provider._normalize_code(code) == expected_bs


def test_tushare_strips_surrounding_whitespace() -> None:
    """前后空格须先 strip 再判定（候选池里偶发带空格的代码）。"""
    assert _code_to_ts("  920826  ") == "920826.BJ"
    assert _code_to_ts(" 600519 ") == "600519.SH"


# ============================================================
# 4. 防"过度修复"：920 分支不得吃掉别的码段
# ============================================================
@pytest.mark.parametrize(
    "code,expected_ts",
    [
        ("200011", "200011.SZ"),   # 深市 B 股，不是北交所
        ("000905", "000905.SZ"),   # 中证 500（指数代码，走通用兜底）
        ("399006", "399006.SZ"),   # 创业板指
    ],
)
def test_non_bse_codes_are_not_captured_by_bj_branch(
    code: str, expected_ts: str
) -> None:
    """加 920/8/4 分支时不能顺手把别的码段误判成北交所。"""
    assert _code_to_ts(code) == expected_ts


def test_baostock_does_not_map_b_share_prefix_to_bj(baostock_provider) -> None:
    """900xxx 是沪市 B 股，不能被 9 开头的粗放分支误判成北交所。

    这也是 `_normalize_code()` 只匹配 `920`（而不是整段 `9`）的原因：
    宁可让 900xxx 保持"原样返回"（与修复前行为一致），也不要引入一个
    新的错误映射。
    """
    got = baostock_provider._normalize_code("900901")
    assert got == "900901", (
        f"900901（沪市 B 股）被误映射成 {got} —— 应原样返回，保持修复前行为"
    )
