"""
钱袋子 — judgment_tracker (判断追踪器)
职责：
  1. 记录每次 steward 决策的完整快照 (record)
  2. N日后自动验证决策是否正确 (verify)
  3. 生成成绩单 — 准确率/盈亏/模块贡献 (scorecard)
  4. 维护模块权重 — EMA自校准 (calibrate)
  5. 提供权重给 Pipeline Layer3 门控 (get_weights)

存储: data/judgments/{uid}/{YYYY-MM}.json — 按月归档
权重: data/judgments/{uid}/weights.json — 实时更新

来源: 朋友B方案(EMA权重自校准) + 设计文档§五 Layer7
"""
import json
import time
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta
from config import DATA_DIR

# ---- 常量 ----
EMA_ALPHA = 0.3          # EMA 平滑系数 (越大越重视近期表现)
VERIFY_DAYS = 5           # 判断后第5天验证
MIN_RECORDS_FOR_EMA = 10  # 至少10条记录才开始EMA校准

# 默认模块权重 (sum=1.0)
DEFAULT_WEIGHTS = {
    "stock_screen": 0.20,
    "signal": 0.15,
    "risk": 0.15,
    "ai_predictor": 0.15,
    "monte_carlo": 0.10,
    "rl_position": 0.10,
    "portfolio_optimizer": 0.08,
    "genetic_factor": 0.05,
    "alt_data": 0.02,
}

MODULE_META = {
    "name": "judgment_tracker",
    "scope": "private",
    "input": ["user_id", "judgment_record"],
    "output": "weights",
    "cost": "cpu",
    "tags": ["tracking", "calibration"],
    "description": "判断追踪器：记录决策→N日验证→EMA权重自校准→供门控使用",
    "layer": "output",
    "priority": 90,
}


# ============================================================
# 存储工具
# ============================================================

def _judgments_dir(user_id: str) -> Path:
    """用户判断目录"""
    d = DATA_DIR / "judgments" / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _month_file(user_id: str, dt: datetime = None) -> Path:
    """当月判断文件"""
    dt = dt or datetime.now()
    return _judgments_dir(user_id) / f"{dt.strftime('%Y-%m')}.json"


def _load_month(user_id: str, dt: datetime = None) -> list:
    """读取当月判断记录"""
    f = _month_file(user_id, dt)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_month(user_id: str, records: list, dt: datetime = None):
    """保存当月判断记录"""
    f = _month_file(user_id, dt)
    f.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _weights_file(user_id: str) -> Path:
    """权重文件路径"""
    return _judgments_dir(user_id) / "weights.json"


# ============================================================
# 1. 记录判断 (Pipeline Layer7 调用)
# ============================================================

def record(user_id: str, judgment_data: dict) -> dict:
    """
    记录一次决策判断的完整快照
    
    judgment_data 来自 ctx.to_judgment_record()，包含：
    - regime, direction, confidence, weighted_score, divergence
    - module_results (各模块的结论+置信度)
    - gate_decision, ev_result, risk_result
    - llm_arbitration (如有)
    - timestamp
    """
    record_entry = {
        "id": f"j_{int(time.time())}_{user_id[:8]}",
        "user_id": user_id,
        "recorded_at": datetime.now().isoformat(),
        "verify_at": (datetime.now() + timedelta(days=VERIFY_DAYS)).strftime("%Y-%m-%d"),
        "verified": False,
        "verdict": None,  # 验证后填: "correct" / "wrong" / "partial"
        "actual_return": None,
        # 决策快照
        "direction": judgment_data.get("direction", "neutral"),
        "confidence": judgment_data.get("confidence", 0),
        "regime": judgment_data.get("regime", "unknown"),
        "weighted_score": judgment_data.get("weighted_score", 0),
        "divergence": judgment_data.get("divergence", 0),
        "gate_decision": judgment_data.get("gate_decision", ""),
        "ev_result": judgment_data.get("ev_result"),
        "risk_blocked": judgment_data.get("risk_blocked", False),
        # 各模块结论（验证时对比）
        "module_snapshots": {},
    }
    
    # 提取各模块快照
    for name, result in judgment_data.get("modules_results", {}).items():
        if isinstance(result, dict) and result.get("available"):
            record_entry["module_snapshots"][name] = {
                "direction": result.get("direction", "neutral"),
                "confidence": result.get("confidence", 0),
                "summary": str(result.get("summary", ""))[:200],
            }
    
    # 追加到当月文件
    records = _load_month(user_id)
    records.append(record_entry)
    _save_month(user_id, records)
    
    return record_entry


# ============================================================
# 2. 验证判断 (cron 每日调用)
# ============================================================

def verify_pending(user_id: str) -> list:
    """
    验证到期的判断记录
    
    逻辑：
    - 找到 verify_at <= 今天 且 verified == False 的记录
    - 获取实际收益率
    - 判断方向是否正确
    - 更新记录
    
    返回: 本次验证的记录列表
    """
    today = datetime.now().strftime("%Y-%m-%d")
    verified_list = []
    
    # 扫描最近2个月（跨月兼容）
    for month_offset in [0, -1]:
        dt = datetime.now() + timedelta(days=month_offset * 30)
        records = _load_month(user_id, dt)
        changed = False
        
        for rec in records:
            if rec.get("verified") or not rec.get("verify_at"):
                continue
            if rec["verify_at"] > today:
                continue
            
            # 到期验证
            actual = _get_actual_return(user_id, rec)
            if actual is None:
                continue  # 数据不可用，跳过
            
            rec["verified"] = True
            rec["verified_at"] = today
            rec["actual_return"] = actual
            
            # 判定
            predicted_dir = rec.get("direction", "neutral")
            if actual > 0.5 and predicted_dir == "bullish":
                rec["verdict"] = "correct"
            elif actual < -0.5 and predicted_dir == "bearish":
                rec["verdict"] = "correct"
            elif abs(actual) <= 0.5 and predicted_dir == "neutral":
                rec["verdict"] = "correct"
            elif abs(actual) <= 1.0:
                rec["verdict"] = "partial"  # 变化太小，算部分正确
            else:
                rec["verdict"] = "wrong"
            
            verified_list.append(rec)
            changed = True
        
        if changed:
            _save_month(user_id, records, dt)
    
    return verified_list


def _get_actual_return(user_id: str, record: dict) -> Optional[float]:
    """
    获取判断记录对应的实际收益率
    简化版：用沪深300指数 N 天收益率代替
    TODO: 如果有具体持仓，用持仓加权收益
    """
    try:
        from services.stock_data_provider import get_stock_data
        # 取沪深300（sh000300）的最近数据
        data = get_stock_data("sh000300", days=VERIFY_DAYS + 5)
        if data and len(data) >= 2:
            recent = float(data[-1].get("close", 0))
            older = float(data[-VERIFY_DAYS - 1].get("close", 0)) if len(data) > VERIFY_DAYS else float(data[0].get("close", 0))
            if older > 0:
                return round((recent - older) / older * 100, 2)
    except Exception as e:
        print(f"[JUDGMENT] 获取实际收益失败: {e}")
    return None


# ============================================================
# 3. 成绩单 (前端/API 调用)
# ============================================================

def scorecard(user_id: str, months: int = 3) -> dict:
    """
    生成判断成绩单
    
    返回:
    - total: 总判断数
    - verified: 已验证数
    - correct/wrong/partial: 正确/错误/部分
    - accuracy: 准确率
    - avg_confidence: 平均置信度
    - module_accuracy: 各模块准确率
    - recent: 最近10条记录
    """
    all_records = []
    now = datetime.now()
    
    for i in range(months):
        dt = now - timedelta(days=i * 30)
        all_records.extend(_load_month(user_id, dt))
    
    total = len(all_records)
    verified = [r for r in all_records if r.get("verified")]
    correct = [r for r in verified if r.get("verdict") == "correct"]
    wrong = [r for r in verified if r.get("verdict") == "wrong"]
    partial = [r for r in verified if r.get("verdict") == "partial"]
    
    accuracy = round(len(correct) / len(verified) * 100, 1) if verified else 0
    avg_conf = round(sum(r.get("confidence", 0) for r in all_records) / total, 1) if total else 0
    
    # 各模块准确率
    module_stats = {}
    for rec in verified:
        for mod_name, snap in rec.get("module_snapshots", {}).items():
            if mod_name not in module_stats:
                module_stats[mod_name] = {"total": 0, "correct": 0}
            module_stats[mod_name]["total"] += 1
            
            # 模块方向 vs 实际方向
            actual = rec.get("actual_return", 0)
            mod_dir = snap.get("direction", "neutral")
            if (actual > 0.5 and mod_dir == "bullish") or \
               (actual < -0.5 and mod_dir == "bearish") or \
               (abs(actual) <= 0.5 and mod_dir == "neutral"):
                module_stats[mod_name]["correct"] += 1
    
    module_accuracy = {}
    for mod, stats in module_stats.items():
        module_accuracy[mod] = {
            "total": stats["total"],
            "correct": stats["correct"],
            "accuracy": round(stats["correct"] / stats["total"] * 100, 1) if stats["total"] else 0,
        }
    
    return {
        "total": total,
        "verified": len(verified),
        "pending": total - len(verified),
        "correct": len(correct),
        "wrong": len(wrong),
        "partial": len(partial),
        "accuracy": accuracy,
        "avg_confidence": avg_conf,
        "module_accuracy": module_accuracy,
        "recent": sorted(all_records, key=lambda r: r.get("recorded_at", ""), reverse=True)[:10],
        "generated_at": datetime.now().isoformat(),
    }


# ============================================================
# 4. 权重管理 (Pipeline Layer3 + Layer7 调用)
# ============================================================

def get_weights(user_id: str) -> dict:
    """
    获取当前模块权重 — Pipeline Layer3 门控用
    
    如果有校准过的权重 → 返回校准版
    否则 → 返回默认权重
    """
    f = _weights_file(user_id)
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data.get("weights", DEFAULT_WEIGHTS.copy())
        except Exception:
            pass
    return DEFAULT_WEIGHTS.copy()


def calibrate(user_id: str) -> dict:
    """
    EMA 权重自校准 — Pipeline Layer7 / cron 调用
    
    逻辑：
    1. 统计各模块在已验证判断中的准确率
    2. 用 EMA 平滑更新权重：新权重 = α×准确率 + (1-α)×旧权重
    3. 归一化到 sum=1.0
    4. 保存到 weights.json
    
    返回: {old_weights, new_weights, changes, records_used}
    """
    # 获取成绩单
    card = scorecard(user_id, months=3)
    mod_acc = card.get("module_accuracy", {})
    
    if card["verified"] < MIN_RECORDS_FOR_EMA:
        return {
            "status": "insufficient_data",
            "verified": card["verified"],
            "required": MIN_RECORDS_FOR_EMA,
            "message": f"需要至少 {MIN_RECORDS_FOR_EMA} 条已验证记录才能校准（当前 {card['verified']} 条）",
        }
    
    old_weights = get_weights(user_id)
    new_weights = {}
    
    for mod_name, default_w in DEFAULT_WEIGHTS.items():
        old_w = old_weights.get(mod_name, default_w)
        
        if mod_name in mod_acc and mod_acc[mod_name]["total"] >= 3:
            # 有足够数据 → EMA 更新
            acc = mod_acc[mod_name]["accuracy"] / 100.0  # 0~1
            new_w = EMA_ALPHA * acc + (1 - EMA_ALPHA) * old_w
        else:
            # 数据不足 → 保持原权重
            new_w = old_w
        
        # 最低权重保护（不会被完全清零）
        new_weights[mod_name] = max(new_w, 0.02)
    
    # 归一化
    total = sum(new_weights.values())
    if total > 0:
        new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}
    
    # 保存
    weight_data = {
        "weights": new_weights,
        "calibrated_at": datetime.now().isoformat(),
        "records_used": card["verified"],
        "overall_accuracy": card["accuracy"],
    }
    _weights_file(user_id).write_text(
        json.dumps(weight_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    
    # 计算变化
    changes = {}
    for mod in DEFAULT_WEIGHTS:
        old_v = old_weights.get(mod, 0)
        new_v = new_weights.get(mod, 0)
        diff = new_v - old_v
        if abs(diff) > 0.005:
            changes[mod] = {
                "old": round(old_v, 4),
                "new": round(new_v, 4),
                "diff": round(diff, 4),
                "direction": "↑" if diff > 0 else "↓",
            }
    
    return {
        "status": "calibrated",
        "old_weights": old_weights,
        "new_weights": new_weights,
        "changes": changes,
        "records_used": card["verified"],
        "overall_accuracy": card["accuracy"],
        "calibrated_at": datetime.now().isoformat(),
    }


# ============================================================
# 5. enrich() 适配层 (Pipeline 集成)
# ============================================================

def enrich(ctx) -> None:
    """
    Pipeline Layer7 调用：
    1. 记录本次判断
    2. 尝试验证到期判断
    3. 如果有足够数据，校准权重
    """
    user_id = getattr(ctx, "user_id", "default")
    
    # 1. 记录
    judgment_data = {}
    if hasattr(ctx, "to_judgment_record"):
        judgment_data = ctx.to_judgment_record()
    else:
        # 手动提取
        judgment_data = {
            "direction": getattr(ctx, "final_direction", "neutral"),
            "confidence": getattr(ctx, "final_confidence", 0),
            "regime": getattr(ctx, "regime", "unknown"),
            "weighted_score": getattr(ctx, "weighted_score", 0),
            "divergence": getattr(ctx, "divergence", 0),
            "gate_decision": getattr(ctx, "gate_decision", ""),
            "ev_result": getattr(ctx, "ev_result", None),
            "risk_blocked": getattr(ctx, "risk_blocked", False),
            "modules_results": getattr(ctx, "modules_results", {}),
        }
    
    rec = record(user_id, judgment_data)
    
    # 2. 顺便验证到期的
    try:
        verified = verify_pending(user_id)
        if verified:
            print(f"[JUDGMENT] {user_id}: 验证了 {len(verified)} 条记录")
    except Exception as e:
        print(f"[JUDGMENT] 验证失败: {e}")
    
    # 3. 写回 ctx
    if hasattr(ctx, "judgment_id"):
        ctx.judgment_id = rec["id"]
    if hasattr(ctx, "module_weights"):
        ctx.module_weights = get_weights(user_id)
