"""
services/stock_monitor.py 降级链参数名守门测试（P1 追加发现）
================================================================
背景：排查 P1（infra/data_source 层裸调用超时保护）时，实测
get_stock_realtime('600519') 发现 _fallback_hist_close() 里"源1：
东方财富"这一分支调用 get_stock_daily_hist(symbol=code, ...)，但该
函数签名的第一个参数名是 code（不是 symbol），关键字传参直接
TypeError。这个 TypeError 被外层 try/except 吞掉，只在日志打一行
不起眼的报错，于是这个降级分支从未真正跑起来过，一直静默 fall
through 到"源2：新浪"（数据字段覆盖率更差：volume/amount/high/low/
open 全部返回 None，data_date 还输出了错误的行数索引而不是日期）。

同一个 bug 出现在两处：_fallback_hist_close()（get_stock_realtime 的
降级路径）和 calc_stock_indicators()（技术指标计算），两处症状相同、
修法相同。

这个文件测什么：
  - 用真实的（未 mock 的）get_stock_daily_hist 签名做参数名契约测试，
    确保 stock_monitor.py 里的调用点用的关键字参数是它接受的
  - 验证降级链在"雪球失败"场景下能真正走到东财这一层（不是每次都
    fall through 到新浪），用 mock 断言 get_stock_daily_hist 被调用过
  - 回归锁定：不允许再出现 get_stock_daily_hist(symbol=...) 这种
    错误的关键字传参
"""
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.data_source.market.stocks import get_stock_daily_hist


def test_get_stock_daily_hist_first_param_is_code_not_symbol():
    """契约锁定：get_stock_daily_hist 的第一个参数名是 code。

    这条测试本身很简单，但存在的意义是防止未来有人在不知道调用方
    约定的情况下把参数名改成 symbol（跟同文件里其他几个函数的命名
    风格看齐），那样会让本次修复的 stock_monitor.py 两处调用再次
    静默失效。
    """
    sig = inspect.signature(get_stock_daily_hist)
    params = list(sig.parameters.keys())
    assert params[0] == "code", (
        f"get_stock_daily_hist 第一个参数应为 'code'，实际是 '{params[0]}'。"
        "stock_monitor.py 里有多处用 code=code 关键字传参，"
        "改名会导致那些调用全部 TypeError。"
    )


def test_no_bare_symbol_kwarg_calls_to_get_stock_daily_hist():
    """回归锁定：全仓库不应再出现 get_stock_daily_hist(symbol=...) 这种
    错误的关键字传参（真实历史 bug，2026-09-01 修复）。"""
    import re

    backend_dir = Path(__file__).parent.parent
    bad_calls = []
    for py_file in backend_dir.rglob("*.py"):
        if "venv" in py_file.parts or "__pycache__" in py_file.parts:
            continue
        if py_file.name == __name__.split(".")[-1] + ".py":
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if re.search(r"get_stock_daily_hist\(\s*symbol\s*=", line):
                bad_calls.append(f"{py_file.relative_to(backend_dir)}:{i}: {line.strip()}")

    assert bad_calls == [], (
        "发现 get_stock_daily_hist(symbol=...) 错误调用（应为 code=...）: "
        + "; ".join(bad_calls)
    )


def test_fallback_hist_close_actually_calls_eastmoney_layer():
    """验证 _fallback_hist_close 在雪球失败后，东财这一层的
    get_stock_daily_hist 真的被调用了（不是因为参数名错误而被跳过）。

    用 mock 让"雪球"路径不涉及（这个函数本身就是雪球失败后的兜底），
    直接验证 get_stock_daily_hist 被调用时用的是正确的关键字参数
    （code=，不是 symbol=），且调用会成功返回而不抛 TypeError。
    """
    from services import stock_monitor

    fake_df_columns_data = {
        "日期": ["2026-08-29", "2026-08-30"],
        "收盘": [100.0, 102.0],
        "成交量": [1000, 1100],
        "成交额": [100000, 112200],
        "最高": [103.0, 104.0],
        "最低": [99.0, 100.0],
        "开盘": [100.5, 101.0],
    }

    class _FakeDF:
        """极简 DataFrame 替身，只支持本函数用到的接口。"""

        def __init__(self, data):
            self._data = data
            self._n = len(next(iter(data.values())))

        def __len__(self):
            return self._n

        @property
        def iloc(self):
            rows = self
            class _Iloc:
                def __getitem__(self, idx):
                    return {k: v[idx] for k, v in rows._data.items()}
            return _Iloc()

    fake_df = _FakeDF(fake_df_columns_data)

    # 注意：get_stock_daily_hist 在 _fallback_hist_close 内部是局部
    # `from infra.data_source.market.stocks import get_stock_daily_hist`，
    # 不是 stock_monitor 模块级绑定，所以要 patch 数据源模块本身，
    # 不能 patch stock_monitor.get_stock_daily_hist（那个属性不存在）。
    with patch(
        "infra.data_source.market.stocks.get_stock_daily_hist", return_value=fake_df
    ) as mock_hist:
        result = stock_monitor._fallback_hist_close("600519")

    assert mock_hist.called, "东财降级层 get_stock_daily_hist 应该被调用"
    _, kwargs = mock_hist.call_args
    assert "code" in kwargs, (
        f"get_stock_daily_hist 应以 code= 关键字调用，实际传入的关键字参数是 {list(kwargs.keys())}"
    )
    assert kwargs["code"] == "600519"
    # 修复前这里会因为 TypeError 被吞掉而 result == {}
    assert result.get("price") == 102.0
