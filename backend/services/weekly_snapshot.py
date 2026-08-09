"""
钱袋子 — 周度净资产快照
每周五收盘后存储净资产快照，用于计算周环比收益

存储内容：
  - 日期（ISO周）
  - 净资产总额
  - 投资市值
  - 现金
  - 持仓明细市值（每只基金/股票的市值）
  - 成本基准（用于计算收益）

快照文件：DATA_DIR / user_id / snapshots / weekly_YYYYMMDD.json
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from config import DATA_DIR

MODULE_META = {
    "name": "weekly_snapshot",
    "scope": "private",
    "input": ["user_id"],
    "output": "snapshot",
    "cost": "cpu",
    "tags": ["snapshot", "weekly", "networth"],
    "description": "周度净资产快照 - 存储每周净资产用于计算周环比",
    "layer": "data",
    "priority": 3,
}


def take_snapshot(user_id: str, force: bool = False) -> dict:
    """
    拍摄当前净资产快照（如果本周还没拍过）
    
    Args:
        user_id: 用户ID
        force: 是否强制重新拍摄
    
    Returns:
        快照数据字典
    """
    snapshot_dir = DATA_DIR / user_id / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    
    # 计算本周五的日期（作为快照标识）
    now = datetime.now()
    days_to_friday = (4 - now.weekday()) % 7
    if days_to_friday == 0 and now.hour < 15:  # 周五但未收盘
        days_to_friday = -7  # 用上周五的标识
    friday = now + timedelta(days=days_to_friday)
    friday = friday.replace(hour=0, minute=0, second=0, microsecond=0)
    snapshot_key = f"weekly_{friday.strftime('%Y%m%d')}"
    snapshot_file = snapshot_dir / f"{snapshot_key}.json"
    
    # 如果已存在且不强更，直接返回
    if not force and snapshot_file.exists():
        try:
            return json.loads(snapshot_file.read_text(encoding="utf-8"))
        except Exception:
            pass  # 文件损坏，重新生成
    
    # 获取当前净资产数据
    from services.unified_networth import calc_unified_networth
    nw = calc_unified_networth(user_id, force=True)
    
    # 获取持仓明细市值
    holdings_detail = _get_holdings_market_value(user_id)
    
    # 构建快照
    snapshot = {
        "user_id": user_id,
        "snapshot_type": "weekly",
        "snapshot_date": friday.strftime("%Y-%m-%d"),
        "snapshot_key": snapshot_key,
        "generated_at": now.isoformat(),
        "net_worth": nw.get("netWorth", 0),
        "breakdown": nw.get("breakdown", {}),
        "holdings_detail": holdings_detail,
        "cost_basis": _get_cost_basis(user_id),  # 成本基准（从交易记录计算）
    }
    
    # 保存
    snapshot_file.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    print(f"[SNAPSHOT] 已保存快照: {snapshot_file}")
    
    return snapshot


def _get_holdings_market_value(user_id: str) -> list:
    """获取每个持仓的当前市值"""
    details = []
    
    try:
        from services.fund_monitor import load_fund_holdings
        from services.tushare_data import get_fund_nav
        
        funds = load_fund_holdings(user_id) or []
        for fund in funds:
            code = fund.get("code", "")
            shares = fund.get("shares", 0)
            name = fund.get("name", code)
            
            # 获取最新净值
            nav_data = get_fund_nav(code, days=1)
            if nav_data and len(nav_data) > 0:
                nav = nav_data[-1].get("nav", 0)
                market_value = shares * nav
                
                details.append({
                    "code": code,
                    "name": name,
                    "shares": shares,
                    "nav": nav,
                    "market_value": market_value,
                    "type": "fund"
                })
    except Exception as e:
        print(f"[SNAPSHOT] 基金市值计算失败: {e}")
    
    try:
        from services.stock_monitor import load_stock_holdings
        # 获取最新股价 - 使用 tushare API
        import tushare as ts
        from config import TUSHARE_TOKEN
        
        stocks = load_stock_holdings(user_id) or []
        for stock in stocks:
            code = stock.get("code", "")
            shares = stock.get("shares", 0)
            name = stock.get("name", code)
            
            # 获取最新价格（简化：使用持仓中的成本价估算）
            cost = stock.get("cost", 0)
            # TODO: 实现 get_stock_realtime_price
            market_value = shares * cost  # 近似值
            
            details.append({
                "code": code,
                "name": name,
                "shares": shares,
                "price": cost,
                "market_value": market_value,
                "type": "stock"
            })
    except Exception as e:
        print(f"[SNAPSHOT] 股票市值计算失败: {e}")
    
    return details


def _get_cost_basis(user_id: str) -> float:
    """
    获取成本基准（总投资成本）
    从交易记录中汇总
    """
    try:
        from services.persistence import load_user
        user = load_user(user_id)
        txns = (user.get("portfolio") or {}).get("transactions", [])
        
        # 汇总所有买入金额（简化版）
        total_cost = 0
        for txn in txns:
            if txn.get("type") == "BUY":
                total_cost += txn.get("amount", 0)
        
        return total_cost
    except Exception as e:
        print(f"[SNAPSHOT] 成本基准计算失败: {e}")
        return 0


def get_weekly_change(user_id: str) -> dict:
    """
    计算周环比变化
    
    Returns:
        {
            "current_nw": 802,
            "last_week_nw": 776,
            "change_amount": +26,
            "change_pct": +3.4%,
            "holdings_change": [...]
        }
    """
    try:
        # 获取本周快照
        now = datetime.now()
        days_to_friday = (4 - now.weekday()) % 7
        friday = now + timedelta(days=days_to_friday)
        friday_str = friday.strftime("%Y%m%d")
        
        snapshot_dir = DATA_DIR / user_id / "snapshots"
        current_file = snapshot_dir / f"weekly_{friday_str}.json"
        
        if not current_file.exists():
            # 本周还没拍快照，先拍一个
            take_snapshot(user_id, force=True)
        
        current = json.loads(current_file.read_text(encoding="utf-8"))
        
        # 获取上周快照
        last_friday = friday - timedelta(days=7)
        last_file = snapshot_dir / f"weekly_{last_friday.strftime('%Y%m%d')}.json"
        
        if not last_file.exists():
            return {
                "current_nw": current.get("net_worth", 0),
                "last_week_nw": None,
                "change_amount": None,
                "change_pct": None,
                "holdings_change": [],
                "note": "上周无快照数据，无法计算周环比"
            }
        
        last = json.loads(last_file.read_text(encoding="utf-8"))
        
        # 计算变化
        current_nw = current.get("net_worth", 0)
        last_nw = last.get("net_worth", 0)
        change_amount = current_nw - last_nw
        change_pct = (change_amount / last_nw * 100) if last_nw > 0 else 0
        
        # 计算每只持仓的变化
        holdings_change = _calc_holdings_change(
            last.get("holdings_detail", []),
            current.get("holdings_detail", [])
        )
        
        return {
            "current_nw": current_nw,
            "last_week_nw": last_nw,
            "change_amount": round(change_amount, 2),
            "change_pct": round(change_pct, 2),
            "holdings_change": holdings_change,
            "note": None
        }
        
    except Exception as e:
        print(f"[SNAPSHOT] 周环比计算失败: {e}")
        return {
            "current_nw": 0,
            "last_week_nw": None,
            "change_amount": None,
            "change_pct": None,
            "holdings_change": [],
            "note": f"计算失败: {e}"
        }


def _calc_holdings_change(last_details: list, current_details: list) -> list:
    """计算每只持仓的周变化"""
    changes = []
    
    # 建立当前持仓的查找表
    current_map = {h.get("code"): h for h in current_details}
    
    for last_h in last_details:
        code = last_h.get("code")
        current_h = current_map.get(code)
        
        if current_h:
            last_mv = last_h.get("market_value", 0)
            current_mv = current_h.get("market_value", 0)
            change = current_mv - last_mv
            change_pct = (change / last_mv * 100) if last_mv > 0 else 0
            
            changes.append({
                "code": code,
                "name": last_h.get("name", code),
                "last_market_value": last_mv,
                "current_market_value": current_mv,
                "change_amount": round(change, 2),
                "change_pct": round(change_pct, 2),
                "type": last_h.get("type", "fund")
            })
    
    # 按变化金额排序（赢家在前）
    changes.sort(key=lambda x: x.get("change_amount", 0), reverse=True)
    
    return changes


def get_history(user_id: str, weeks: int = 4) -> list:
    """
    获取历史快照列表
    
    Args:
        user_id: 用户ID
        weeks: 返回最近几周
    
    Returns:
        快照列表（按日期降序）
    """
    snapshot_dir = DATA_DIR / user_id / "snapshots"
    if not snapshot_dir.exists():
        return []
    
    files = sorted(snapshot_dir.glob("weekly_*.json"), reverse=True)
    snapshots = []
    
    for fp in files[:weeks]:
        try:
            snapshot = json.loads(fp.read_text(encoding="utf-8"))
            snapshots.append(snapshot)
        except Exception:
            continue
    
    return snapshots


if __name__ == "__main__":
    # 测试
    print("测试周度快照功能...")
    
    test_user = "LeiJiang"
    
    # 拍摄快照
    print("\n1. 拍摄快照...")
    snapshot = take_snapshot(test_user)
    print(f"   净资产: ¥{snapshot.get('net_worth', 0):,.0f}")
    
    # 计算周环比
    print("\n2. 计算周环比...")
    change = get_weekly_change(test_user)
    print(f"   当前: ¥{change.get('current_nw', 0):,.0f}")
    if change.get("last_week_nw"):
        print(f"   上周: ¥{change.get('last_week_nw'):,.0f}")
        print(f"   变化: ¥{change.get('change_amount'):,.0f} ({change.get('change_pct')}%)")
    
    # 查看历史
    print("\n3. 历史快照...")
    history = get_history(test_user, weeks=4)
    print(f"   共 {len(history)} 个快照")
