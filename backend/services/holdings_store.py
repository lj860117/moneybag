"""钱袋子 — 持仓文件读写（原子写 + 损坏可区分 + 损坏备份）

为什么单独一个模块
------------------
`stock_monitor` / `fund_monitor` 两条持仓链此前各自是这套写法::

    def save_x_holdings(holdings, user_id="default"):
        f.write_text(json.dumps(holdings, ensure_ascii=False, indent=2))   # ← 非原子

    def load_x_holdings(user_id="default") -> list:
        if f.exists():
            try:
                return json.loads(f.read_text())
            except Exception:
                return []          # ← 损坏与「真没持仓」返回同一个 []
        return []                  # ← 文件不存在也是 []

三个环节叠起来才致命：

  1. **非原子写**（根因）：写到一半进程被杀 → 磁盘上留下半个 JSON。
     本仓已有铁律与现成实现（`scripts/daily_push_quality_check.py` /
     `scripts/ops_analyst.py` 都写了「JSON 落盘禁止裸 open().write()」，
     `services.persistence.atomic_write_json` 是既有正确实现）。
  2. **损坏与空不可区分**（放大器）：`except: return []` 让调用方以为
     「这个用户没有持仓」，而不是「文件坏了」。
  3. **读空后又覆盖写**（执行者）：`add_x_holding` 拿到 `[]` → append 一只
     → 用这 1 条覆盖原文件 → 原有 N 只持仓**永久消失**。

所以本模块把三件事绑成一份实现，两条链共用，避免修一半又漂移：

  - `save_holdings()`  → 走 `persistence.atomic_write_json`（tmp+fsync+rename）
  - `load_holdings()`  → `return []` 只在**确实没有文件**时发生；解析失败
                         或顶层不是数组 → `load_state="corrupt"`，并把损坏
                         文件**另存为 `<原名>.corrupt-<YYYYmmddHHMMSS>`**（不删原文件）
  - `corrupt_write_refusal()` → 写路径对 corrupt **fail-closed**

返回类型是 list 子类 `HoldingsList`，与 `services.fact_anchor.AnchorFindings`
同一套已验证的做法：旧调用方一行不改照常跑（`isinstance(x, list)` /
`x == []` / `bool(x)` / `len(x)` 语义全不变），新调用方可以用
`.load_state` 把「没有」「损坏」分开。

⚠️ 不要用 `if not holdings:` 判断「真的没持仓」—— 空列表可能是 corrupt。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from services.persistence import atomic_write_json

# ── 加载状态 ──────────────────────────────────────────────
LOAD_STATE_OK = "ok"            # 读取成功（内容可以是空数组 = 真的没有持仓）
LOAD_STATE_MISSING = "missing"  # 文件不存在（全新用户，合法为空）
LOAD_STATE_CORRUPT = "corrupt"  # 文件存在但读不出合法持仓数组（禁止覆盖写）

CORRUPT_BACKUP_SUFFIX = ".corrupt-"


class HoldingsList(list):
    """持仓列表 + 「这份数据是从什么状态读出来的」状态位。

    与 `fact_anchor.AnchorFindings` 同构：做成 list 子类，所有既有调用方
    （`if holdings:` / `len(holdings)` / `holdings == []` / `for h in ...`）
    无需改动，同时新代码可以区分三态。

    Attributes:
        load_state: LOAD_STATE_OK / MISSING / CORRUPT 之一。
        corrupt_backup: 损坏时生成的备份路径（未损坏时为 None）。
    """

    def __init__(self, *args, load_state: str = LOAD_STATE_OK,
                 corrupt_backup: Optional[Path] = None) -> None:
        super().__init__(*args)
        self.load_state = load_state
        self.corrupt_backup = corrupt_backup

    @property
    def corrupt(self) -> bool:
        """True 表示文件损坏、本次读到的空列表**不代表没有持仓**。"""
        return self.load_state == LOAD_STATE_CORRUPT

    @property
    def missing(self) -> bool:
        """True 表示文件不存在（全新用户），空列表是合法结论。"""
        return self.load_state == LOAD_STATE_MISSING

    def __repr__(self) -> str:  # pragma: no cover - 调试可读性
        return (f"HoldingsList({list.__repr__(self)}, "
                f"load_state={self.load_state!r}, corrupt_backup={self.corrupt_backup!r})")


def backup_corrupt_file(path: Path) -> Optional[Path]:
    """把损坏文件另存为 `<原名>.corrupt-<YYYYmmddHHMMSS>`，**不删原文件**。

    同一秒内重复调用且内容相同时复用已有备份，避免每次读都堆一份。
    返回备份路径；备份失败返回 None（调用方仍会 fail-closed，不会覆盖）。
    """
    try:
        raw = path.read_bytes()
    except OSError as e:  # 读不到就没法备份，但绝不能因此允许覆盖
        print(f"[HOLDINGS_STORE] ⚠️ 损坏文件无法读取，跳过备份 {path}: {e}")
        return None

    parent = path.parent
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    candidate = parent / f"{path.name}{CORRUPT_BACKUP_SUFFIX}{stamp}"
    idx = 1
    while candidate.exists():
        try:
            if candidate.read_bytes() == raw:
                return candidate  # 同一份内容已有备份
        except OSError:
            pass
        candidate = parent / f"{path.name}{CORRUPT_BACKUP_SUFFIX}{stamp}-{idx}"
        idx += 1

    try:
        shutil.copyfile(path, candidate)
        return candidate
    except OSError as e:
        print(f"[HOLDINGS_STORE] ⚠️ 损坏文件备份失败 {path}: {e}")
        return None


def load_holdings(path: Path, *, label: str = "持仓") -> HoldingsList:
    """读取持仓 JSON。

    - 文件不存在            → `load_state="missing"`，空列表（合法）
    - JSON 可解析且是数组    → `load_state="ok"`，返回内容（可能是空数组）
    - 解析失败 / 顶层非数组  → `load_state="corrupt"`，空列表 **且已备份原文件**

    读路径永不抛异常（接口不能因为一个坏文件就 500），但损坏必须能被调用方
    识别，并由 `corrupt_write_refusal()` 在写路径上 fail-closed。
    """
    if not path.exists():
        return HoldingsList(load_state=LOAD_STATE_MISSING)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError) as e:
        return _corrupt(path, label, f"解析失败: {e}")

    if not isinstance(data, list):
        # 合法 JSON 但不是数组（例如被写成了 {} 或 None）—— 对持仓文件而言同样是
        # 不可用的坏内容；覆盖写会丢数据，故按 corrupt 处理。
        return _corrupt(path, label, f"顶层不是数组（实际 {type(data).__name__}）")

    return HoldingsList(data, load_state=LOAD_STATE_OK)


def _corrupt(path: Path, label: str, reason: str) -> HoldingsList:
    backup = backup_corrupt_file(path)
    where = f"已备份为 {backup.name}" if backup else "备份失败，请勿覆盖原文件"
    print(f"[HOLDINGS_STORE] 🔴 {label}文件损坏：{path.name} —— {reason}。"
          f"{where}。本次不返回任何持仓，且拒绝一切写入。")
    return HoldingsList(load_state=LOAD_STATE_CORRUPT, corrupt_backup=backup)


def corrupt_write_refusal(holdings, path: Path) -> Optional[dict]:
    """写路径守卫：load 结果是 corrupt 时返回 `{"error": ...}`，否则返回 None。

    必须覆盖「读 → 改 → 写」的**整个**临界区调用点（add / update / remove）。
    只在 save 里判是不够的：`add` 会先 `load` 到 `[]`，`[] + [新]` 是合法输入，
    save 无从得知这次写入的基底其实是个坏文件。
    """
    if getattr(holdings, "load_state", LOAD_STATE_OK) != LOAD_STATE_CORRUPT:
        return None
    backup = getattr(holdings, "corrupt_backup", None)
    backup_hint = (f"损坏内容已备份为 {Path(backup).name}；" if backup
                   else "损坏内容备份失败，请先手工备份原文件；")
    return {
        "error": (
            f"持仓文件已损坏（{path.name}），已拒绝本次写入，未做任何修改。"
            f"{backup_hint}请人工修复该文件（或从 {path.name}{CORRUPT_BACKUP_SUFFIX}* "
            f"备份恢复）后再操作。"
        )
    }


def save_holdings(path: Path, holdings) -> None:
    """原子落盘持仓列表（tmp + fsync + rename）。

    **不是** `path.write_text(json.dumps(...))` —— 后者写到一半进程被杀会在
    磁盘上留下半个 JSON，正是本次事故的根因。

    写失败会向上抛（由 atomic_write_json 负责清理 tmp 并保留原文件）：
    让错误可见，而不是静默吞掉。调用方（持仓增删改）在 corrupt 情形下
    根本不会走到这里（见 `corrupt_write_refusal`）。
    """
    atomic_write_json(path, list(holdings))
