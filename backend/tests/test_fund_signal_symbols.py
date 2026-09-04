"""fund_signal.symbols 归一化（B6 唯一实现）的独立回归。

覆盖港股去前导零 / A 股补后缀 / A 股后缀保留前导零 / 美股防误伤 / 脏值。
全部离线，无任何网络或状态依赖。
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.fund_signal.symbols import normalize_symbol


# ============================================================
# 港股：去前导零 + 补 5 位 + 大写后缀
# ============================================================

def test_hk_strips_leading_zeros_and_pads_to_5():
    assert normalize_symbol("00981.HK") == "00981.HK"
    assert normalize_symbol("0981.HK") == "00981.HK"
    assert normalize_symbol("981.HK") == "00981.HK"


def test_hk_normalizes_case():
    assert normalize_symbol("0981.hk") == "00981.HK"
    assert normalize_symbol("981.HK") == "00981.HK"


def test_hk_over_5_digits_returns_raw_and_warns(capsys):
    # 数字部分 >5 位属异常，原样返回并告警（不截断、不补零）。
    out = normalize_symbol("123456.HK")
    assert out == "123456.HK"
    assert "超过 5 位" in capsys.readouterr().out


# ============================================================
# A 股：6 位纯数字补后缀（前缀决定交易所）
# ============================================================

def test_a_share_bare_gets_suffix_by_head():
    assert normalize_symbol("600519") == "600519.SH"
    assert normalize_symbol("900901") == "900901.SH"
    assert normalize_symbol("000001") == "000001.SZ"
    assert normalize_symbol("002163") == "002163.SZ"
    assert normalize_symbol("300750") == "300750.SZ"
    assert normalize_symbol("430047") == "430047.BJ"
    assert normalize_symbol("830799") == "830799.BJ"


def test_a_share_leading_zeros_are_part_of_code():
    # ⚠️ 铁律：A 股不去前导零 —— '000001.SZ' ≠ '1.SZ'。
    assert normalize_symbol("000001") == "000001.SZ"
    assert normalize_symbol("000001.SZ") == "000001.SZ"


def test_a_share_suffixed_keeps_code_and_uppercases():
    assert normalize_symbol("600519.sh") == "600519.SH"
    assert normalize_symbol("000001.sz") == "000001.SZ"
    assert normalize_symbol("002163.SZ") == "002163.SZ"


# ============================================================
# 美股 / 其它 ticker：原样返回，不做任何补零或截断
# ============================================================

def test_us_ticker_and_other_symbols_untouched():
    assert normalize_symbol("AAPL") == "AAPL"
    assert normalize_symbol("TSLA") == "TSLA"
    assert normalize_symbol("BRK.B") == "BRK.B"
    assert normalize_symbol("0700.HK") == "00700.HK"  # 港股 4 位也补到 5 位


# ============================================================
# 脏值：None / 空串 / 空白
# ============================================================

def test_dirty_values_return_as_is():
    assert normalize_symbol(None) == ""
    assert normalize_symbol("") == ""
    assert normalize_symbol("   ") == ""
