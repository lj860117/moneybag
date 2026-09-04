"""
fund_signal 落盘状态读写。

设计依据：docs/design/signal-scout-fund-account.md §7 状态存储

约定：
  * 状态目录：`DATA_DIR/{user_id}/fund_signal/{name}.json`
  * 写：tmp 文件 + `os.replace()` 原子替换；失败只打印不抛（信号侦察是旁路）。
  * 读：文件不存在 / 损坏返回 {} 并告警；损坏文件重命名 `.bak` 保留现场。
  * 每个文件带 `schema_version`，不兼容时自动重置为冷启动（防回滚后残留状态
    让「冷启动静默」失效）。
"""
import os
import json
from pathlib import Path

from config import DATA_DIR

from services.fund_signal.config import STATE_SCHEMA_VERSION

# 合法的状态文件名（供调用方引用，也便于排查时一眼看清有哪些状态）。
DRAWDOWN_STATE = "drawdown_state"
MANAGER_SNAPSHOT = "manager_snapshot"
DCA_STATE = "dca_state"
PUSH_LOG = "push_log"
XRAY_STATE = "xray_state"  # P0-1 基线体检 + 季报新 end_date 跟踪（最小扩展）


def _state_dir(user_id: str) -> Path:
    return Path(DATA_DIR) / str(user_id) / "fund_signal"


def _state_path(user_id: str, name: str) -> Path:
    return _state_dir(user_id) / f"{name}.json"


def load(user_id: str, name: str) -> dict:
    """读取状态。文件不存在 / 损坏 / schema 不兼容 → 返回 {}（等价冷启动）。

    Args:
        user_id: 用户 ID。
        name: 状态名（不含 .json 后缀），见本模块顶部常量。

    Returns:
        状态 dict；异常情况下返回 {}。
    """
    p = _state_path(user_id, name)
    if not p.exists():
        return {}

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        # 损坏文件重命名 .bak 保留现场，便于事后定位。
        print(f"[FUND_SIGNAL] 状态 {name} 读取失败（{e}），已备份为 .bak 并冷启动")
        _backup_corrupt(p)
        return {}

    if not isinstance(data, dict):
        print(f"[FUND_SIGNAL] 状态 {name} 非 dict，冷启动")
        _backup_corrupt(p)
        return {}

    # schema 不兼容 → 重置为冷启动（防回滚后残留状态让「冷启动静默」失效）。
    if data.get("schema_version") != STATE_SCHEMA_VERSION:
        print(
            f"[FUND_SIGNAL] 状态 {name} schema_version="
            f"{data.get('schema_version')!r} 与当前 {STATE_SCHEMA_VERSION} 不兼容，重置为冷启动"
        )
        return {}

    return data


def save(user_id: str, name: str, data: dict) -> None:
    """原子写入状态（tmp + os.replace）。失败只打印不抛。

    Args:
        user_id: 用户 ID。
        name: 状态名。
        data: 状态 dict（会自动注入 schema_version，调用方无需手动加）。
    """
    payload = dict(data or {})
    payload["schema_version"] = STATE_SCHEMA_VERSION

    d = _state_dir(user_id)
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = _state_path(user_id, name)
        tmp = d / f"{name}.json.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        print(f"[FUND_SIGNAL] 状态 {name} 保存失败（{e}）")


def _backup_corrupt(p: Path) -> None:
    """把损坏的状态文件重命名为 .bak（保留现场，不抛异常）。"""
    try:
        if p.exists():
            p.rename(p.with_suffix(".json.bak"))
    except Exception:
        pass
