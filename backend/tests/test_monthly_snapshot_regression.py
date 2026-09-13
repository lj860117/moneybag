"""
回归测试：月度快照的「净资产来源」必须指向真实存在的符号
========================================================
事故（2026-09-13 定位）：
  `services/monthly_snapshot.py` 里曾写
      from services.portfolio_overview import get_unified_networth
  但 `get_unified_networth` 在**任何模块都不存在**（真实现是
  `services/unified_networth.py:66 calc_unified_networth`）。

  因为这条 import 写在 try/except 里，ImportError 被静默吞掉 →
  `save_monthly_snapshot()` 永远返回 None → 月度快照连续数月一个都没落盘；
  而 `scripts/night_worker.py:step_monthly_snapshot()` 仍打印
  "✅ 快照完成: 0 个用户" —— 步骤跑了、绿勾、实际全失败。

本文件的两道闸门都必须在「把导入改回错误名字」时**转红**
（实现时已做故障注入前后对照，见提交信息）：
  1) 源码级：SUT 里不得再出现 `get_unified_networth` 这个死名字，
     且必须真的从 `services.unified_networth` 导入 `calc_unified_networth`。
  2) 行为级：把真实 provider mock 掉后跑通 `save_monthly_snapshot`，
     断言快照真的落盘且金额一致 —— 若导入名回归，mock 打不中，
     真实 provider 对空用户返回 netWorth 0 → 函数返回 None → 断言失败。

⚠️ 不能只写 `import monthly_snapshot` 就算完 —— 那条 import 就在
try/except 里，import 失败不会抛，只会静默返回 None。必须是可证伪的断言。
"""
import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import monthly_snapshot as ms
from services import unified_networth as unw


def _module_imports(module) -> set:
    """解析模块真实 import 语句 → {(module_path, imported_name), ...}

    用 AST 而不是字符串匹配：注释/文档里提到旧名字（比如解释这个 bug 的
    注释本身）不算违规，只有**真的还写着那条 import** 才算。
    """
    tree = ast.parse(inspect.getsource(module))
    pairs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                pairs.add((node.module, alias.name))
    return pairs


def test_unified_networth_provider_exists():
    """provider 符号本身必须真实存在且可调用"""
    assert hasattr(unw, "calc_unified_networth"), (
        "services.unified_networth 里没有 calc_unified_networth —— "
        "monthly_snapshot 依赖的导入目标不存在"
    )
    assert callable(unw.calc_unified_networth)


def test_monthly_snapshot_imports_real_provider():
    """源码级闸门：导入语句必须指向真实符号，不得残留死名字"""
    imports = _module_imports(ms)

    assert ("services.unified_networth", "calc_unified_networth") in imports, (
        "monthly_snapshot 必须 `from services.unified_networth import calc_unified_networth`"
    )
    assert not any(name == "get_unified_networth" for _, name in imports), (
        "monthly_snapshot 仍在 import 不存在的 get_unified_networth。"
        "该 import 位于 try/except 内，ImportError 会被静默吞掉，"
        "导致 save_monthly_snapshot() 永远返回 None、快照永不落盘。"
    )


def test_save_monthly_snapshot_persists_snapshot(monkeypatch):
    """行为级闸门：mock 掉真实 provider 后，快照必须真的落盘"""
    user_id = "test_snapshot_import_regression"
    fake_nw = {
        "netWorth": 1234567.0,
        "breakdown": {"investment": {"total": 1234567.0}},
    }

    def _fake_calc(uid, force=False):
        assert uid == user_id, f"provider 收到的 user_id 不对: {uid}"
        return fake_nw

    # 打在 SUT 真实查找的模块属性上（SUT 在函数内做 from ... import，
    # 调用时才会取这个属性，所以这里能生效）
    monkeypatch.setattr(unw, "calc_unified_networth", _fake_calc)

    snapshot = ms.save_monthly_snapshot(user_id)
    assert snapshot is not None, (
        "save_monthly_snapshot 返回 None —— 净资产 provider 没被调用到。"
        "导入名写错时正是这个症状（import 被 try/except 静默吞掉）。"
    )
    assert snapshot["net_worth"] == 1234567.0

    latest = ms.get_snapshot_latest(user_id)
    assert latest is not None
    assert latest["net_worth"] == 1234567.0
