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
VERIFY_DAYS = 15          # 判断后第15个交易日验证（5天太短，市场方向需更长周期）
# 2026-09-13: 10 → 30。n=10 时准确率的 95% 置信区间宽到 ≈±30pp，等于在证明不了
# 任何事的样本量上永久改权重。n=30 时约 ±18pp，勉强可作门槛。
MIN_RECORDS_FOR_EMA = 30
# 2026-09-13: 新增。历史上单个模块只要 3 条样本就参与调权（n=3 的置信区间约 [21%, 94%]）。
MIN_MODULE_RECORDS_FOR_EMA = 30
# 区分 60% 命中率与 50% 抛硬币（α=0.05 双侧 / power=80%）约需 194 条已验证的方向性样本；
# 若同时比较 8 个模块（Bonferroni 校正）约需 357 条。仅用于「样本够不够」的诚实展示，
# 不作为校准门槛 —— 到不了这个数时应当如实说「尚不能判定」，而不是给一个好看的数字。
MIN_DIRECTIONAL_FOR_SIGNIFICANCE = 194
# 中性带半宽（%）= NEUTRAL_BAND_SIGMA × 沪深300近20日日收益标准差。
# 旧的固定 ±0.5% 口径在实测样本里让 neutral 命中 0/70 —— 等于把「中性」这个类别禁用。
NEUTRAL_BAND_SIGMA = 0.5
NEUTRAL_BAND_FLOOR = 0.3       # 半宽下限（%）防止低波动期把带宽压到 0
NEUTRAL_BAND_FALLBACK = 0.5    # 取不到波动率时的兜底半宽（%），与旧口径一致
RECORD_DEDUP_MINUTES = 30  # 同用户同regime/direction 30分钟内不重复写入

# 默认模块权重 (sum=1.0)
# M5 W4: ai_predictor 权重重分配到其他模块（旧版已删除）
DEFAULT_WEIGHTS = {
    "stock_screen": 0.25,
    "signal": 0.20,
    "risk": 0.20,
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
    
    防重复：同用户同regime+direction，30分钟内不重复写入
    
    judgment_data 来自 ctx.to_judgment_record()，包含：
    - regime, direction, confidence, weighted_score, divergence
    - module_results (各模块的结论+置信度)
    - gate_decision, ev_result, risk_result
    - llm_arbitration (如有)
    - timestamp
    """
    # ---- 防重复写入（同 regime+direction 30分钟内去重）----
    records_now = _load_month(user_id)
    new_regime = judgment_data.get("regime", "unknown")
    new_direction = judgment_data.get("direction", "neutral")
    cutoff = datetime.now() - timedelta(minutes=RECORD_DEDUP_MINUTES)
    for existing in records_now[-5:]:  # 只检查最近5条，效率优先
        try:
            rec_time = datetime.fromisoformat(existing.get("recorded_at", "2000-01-01"))
        except Exception:
            continue
        if (rec_time >= cutoff
                and existing.get("regime") == new_regime
                and existing.get("direction") == new_direction):
            print(f"[JUDGMENT] 防重复：{new_regime}/{new_direction} 在 {RECORD_DEDUP_MINUTES}min 内已有记录，跳过")
            return existing  # 返回已有记录，不写入新记录

    record_entry = {
        "id": f"j_{int(time.time())}_{user_id[:8]}",
        "user_id": user_id,
        "recorded_at": datetime.now().isoformat(),
        "verify_at": (datetime.now() + timedelta(days=VERIFY_DAYS)).strftime("%Y-%m-%d"),
        "verified": False,
        "verdict": None,  # 验证后填: "correct" / "wrong" / "partial"
        "actual_return": None,
        # 决策快照
        "direction": new_direction,
        "confidence": judgment_data.get("confidence", 0),
        "regime": new_regime,
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
    records_now.append(record_entry)
    _save_month(user_id, records_now)
    
    return record_entry


# ============================================================
# 2. 验证判断 (cron 每日调用)
# ============================================================

def _month_files(user_id: str) -> list:
    """用户判断目录下所有 YYYY-MM.json（排除 weights.json），按月份升序。

    2026-09-13: 旧实现写死 `for month_offset in [0, -1]` 只扫两个月，
    实测后果是 2026-06 的 10 条记录全部 verified=False 且 verify_at 早已过期，
    永久丢失（日期越往后越扫不到）。改为遍历全部月份文件。
    """
    return sorted(
        p for p in _judgments_dir(user_id).glob("*.json") if p.name != "weights.json"
    )


def neutral_band() -> float:
    """中性带半宽（%）：市场波动小于它就算「没动」。

    用沪深300近20个交易日日收益标准差 × NEUTRAL_BAND_SIGMA，下限 NEUTRAL_BAND_FLOOR。
    取不到数据时返回 NEUTRAL_BAND_FALLBACK —— 这是显式降级，不是造数。
    """
    try:
        from services.tushare_data import get_index_daily
        data = get_index_daily("000300.SH", days=40)
        closes = [float(r["close"]) for r in data if r.get("close") is not None]
        if len(closes) < 21:
            return NEUTRAL_BAND_FALLBACK
        rets = [
            (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
            for i in range(1, len(closes))
        ][-20:]
        if len(rets) < 2:
            return NEUTRAL_BAND_FALLBACK
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return max(NEUTRAL_BAND_FLOOR, round(NEUTRAL_BAND_SIGMA * (var ** 0.5), 3))
    except Exception as e:
        print(f"[JUDGMENT] 中性带取波动率失败，降级为 {NEUTRAL_BAND_FALLBACK}%: {e}")
        return NEUTRAL_BAND_FALLBACK


def judge_verdict(predicted_dir: str, actual: float, band: float) -> str:
    """判决口径（2026-09-13 重做）。

    旧口径的三个问题：
      1. 固定 ±0.5% 阈值 —— 实测样本里 neutral 命中 0/70，等于禁用了「中性」这个类别
      2. 没有基线对照 —— 样本期 56% 的窗口本来就在涨，「永远喊多」天然 56% 命中率，
         脱离基线看命中率无法解释
      3. neutral / blocked 被算进方向命中率分母 —— blocked 表示「风控拦截、根本没做预测」，
         把它判错是类别错误

    新口径：
      - neutral / blocked → "no_view"，不计入方向命中率分母（blocked 更严格：根本没预测）
      - 市场波动落在中性带内 → "partial"（方向对错都算不上，不奖励也不惩罚）
      - 否则按方向符号判 correct / wrong
    """
    if predicted_dir in ("neutral", "blocked"):
        return "no_view"
    if abs(actual) <= band:
        return "partial"
    if (actual > 0 and predicted_dir == "bullish") or \
       (actual < 0 and predicted_dir == "bearish"):
        return "correct"
    return "wrong"


def verify_pending(user_id: str) -> list:
    """验证到期的判断记录。

    - 遍历该用户**所有**月份文件（不再只扫最近两个月）
    - 找到 verify_at <= 今天 且 verified == False 的记录
    - 用 recorded_at 锚定的真实窗口取收益，判定方向
    - 未到期的记录不动（不是「取不到」而是「还没到期」）

    返回: 本次验证的记录列表
    """
    today = datetime.now().strftime("%Y-%m-%d")
    verified_list = []
    band = None  # 惰性计算：只有真的有条目待验证时才去取波动率

    for path in _month_files(user_id):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(records, list):
            continue
        changed = False

        for rec in records:
            if rec.get("verified") or not rec.get("verify_at"):
                continue
            if rec["verify_at"] > today:
                continue

            actual = _get_actual_return(user_id, rec)
            if actual is None:
                continue  # 观察期未走完 / 数据不可得，跳过（不算错，也不算对）

            if band is None:
                band = neutral_band()

            rec["verified"] = True
            rec["verified_at"] = today
            rec["actual_return"] = actual
            rec["neutral_band"] = band
            rec["verdict"] = judge_verdict(rec.get("direction", "neutral"), actual, band)

            verified_list.append(rec)
            changed = True

        if changed:
            path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    return verified_list


def _get_actual_return(user_id: str, record: dict) -> Optional[float]:
    """
    获取判断记录对应的实际收益率。

    口径：以记录的 recorded_at 为**起点**，取其后的 VERIFY_DAYS 个交易日，
    用沪深300涨跌幅作为市场方向的实际结果。

    ⚠️ 2026-09-13 修复：旧实现写的是
        recent = data[0]; older = data[VERIFY_DAYS]
    并配了一句错误的注释「data 是按日期降序排列，[-1] 是最早，[0] 是最新」。
    实际 tushare_data.get_index_daily 的 docstring 与 sort 都是**升序**，
    于是 data[0] 是最早、data[15] 是更晚 —— 算出来的是「今天往前 34~55 天」
    这段窗口，与预测日完全无关。
    线上实测证据：71 条已验记录只有 23 个不同的 actual_return 值（同日批量验证
    必然得到同一个数），前 12 条全是 -7.28。

    ⚠️ 已知局限：用沪深300指数代替用户持仓加权收益（原 TODO 仍未做）。
    对「判断大盘方向」类记录成立；对个股判断只是粗略代理，需在展示层披露。
    """
    try:
        return _index_window_return(record.get("recorded_at"))
    except AssertionError:
        # 取数口径被改坏了必须响亮地失败，不能静默跳过（静默跳过会伪装成「数据不可用」）
        raise
    except Exception as e:
        print(f"[JUDGMENT] 获取实际收益失败: {e}")
        return None


def _index_window_return(recorded_at: Optional[str]) -> Optional[float]:
    """沪深300 在 [recorded_at, recorded_at + VERIFY_DAYS 个交易日] 的涨跌幅（%）。

    返回 None 表示：记录日期缺失 / 观察期尚未走完 / 数据点不足。
    """
    if not recorded_at:
        return None
    base_day = str(recorded_at)[:10]                # YYYY-MM-DD
    base_compact = base_day.replace("-", "")        # YYYYMMDD
    try:
        base_dt = datetime.fromisoformat(base_day)
    except Exception:
        return None

    elapsed = (datetime.now() - base_dt).days
    # days 参数在 tushare_data 内部会额外带 30 天缓冲，这里只需覆盖到记录日之后
    need = max(elapsed, VERIFY_DAYS * 2) + 10

    from services.tushare_data import get_index_daily
    data = get_index_daily("000300.SH", days=need)
    if not data or len(data) < 2:
        return None

    first_d, last_d = str(data[0].get("trade_date", "")), str(data[-1].get("trade_date", ""))
    assert first_d < last_d, (
        f"指数数据不再是升序（first={first_d} last={last_d}）—— "
        "取数窗口逻辑依赖升序假设，请重新核对本函数的索引方向"
    )

    i0 = next(
        (i for i, r in enumerate(data) if str(r.get("trade_date", "")) >= base_compact),
        None,
    )
    if i0 is None:
        return None
    i1 = i0 + VERIFY_DAYS
    if i1 >= len(data):
        return None  # 观察期还没走完

    try:
        c0 = float(data[i0]["close"])
        c1 = float(data[i1]["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if c0 <= 0:
        return None
    return round((c1 - c0) / c0 * 100, 2)


# ============================================================
# 3. 成绩单 (前端/API 调用)
# ============================================================

def _wilson_interval(successes: int, total: int) -> tuple:
    """Wilson 95% 置信区间（%）。n=0 时返回 (0.0, 100.0)。

    用途：把「命中率 60%」旁边放上区间，让人一眼看出样本够不够。
    n=5、命中 3 时区间约 [23%, 88%] —— 这种样本量下任何结论都是噪音。
    """
    if total <= 0:
        return (0.0, 100.0)
    z = 1.96
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = (z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5)) / denom
    lo = max(0.0, center - half) * 100
    hi = min(1.0, center + half) * 100
    return (round(lo, 1), round(hi, 1))


def scorecard(user_id: str, months: int = 3) -> dict:
    """
    生成判断成绩单
    
    自动触发 verify_pending：有到期未验证的记录时，先验证再出成绩单
    
    返回:
    - total: 总判断数
    - verified: 已验证数
    - correct/wrong/partial: 正确/错误/部分
    - accuracy: 准确率
    - avg_confidence: 平均置信度
    - module_accuracy: 各模块准确率
    - recent: 最近10条记录
    """
    # 先触发 verify，补验到期记录（不依赖 cron）
    try:
        newly_verified = verify_pending(user_id)
        if newly_verified:
            print(f"[SCORECARD] 自动补验 {len(newly_verified)} 条到期记录")
    except Exception as e:
        print(f"[SCORECARD] 自动补验失败: {e}")

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
    no_view = [r for r in verified if r.get("verdict") == "no_view"]
    pending = [r for r in all_records if not r.get("verified")]

    # ── 方向命中率：分母只含「真的做了方向预测」的记录 ──
    # 2026-09-13: 旧口径把 neutral / blocked 也算进分母，而 blocked 意味着
    # 「风控拦截、根本没做预测」—— 把它判错是类别错误。现在单独统计「无观点率」。
    directional = [r for r in verified if r.get("verdict") in ("correct", "wrong")]
    directional_correct = [r for r in directional if r.get("verdict") == "correct"]
    directional_accuracy = (
        round(len(directional_correct) / len(directional) * 100, 1) if directional else None
    )
    # accuracy 字段保留（前端在用），现在含义 = 有观点时的方向命中率。
    # 样本不足时为 None —— 前端必须显示「样本不足」而不是把这个 None 渲染成数字。
    accuracy = directional_accuracy if directional_accuracy is not None else 0

    # ── 基线对照：同期「永远喊多」的命中率 ──
    # 没有这个数，8.5% 这种命中率无法解释（样本期 56% 的窗口本来就在涨，
    # 「永远喊多」天然 56%）。只有高于基线的部分才算真本事。
    band = neutral_band()
    base_up = sum(1 for r in verified if (r.get("actual_return") or 0.0) > band)
    baseline_always_bullish = round(base_up / len(verified) * 100, 1) if verified else None
    baseline_always_bearish = (
        round(100 - baseline_always_bullish, 1) if verified else None
    )

    # ── 无观点率（neutral + blocked）──
    no_view_rate = round(len(no_view) / len(verified) * 100, 1) if verified else None

    # ── neutral 单独统计：市场是否真的落在中性带内 ──
    neutral_recs = [r for r in verified if r.get("direction") == "neutral"]
    neutral_in_band = sum(
        1 for r in neutral_recs
        if abs(r.get("actual_return") or 9e9) <= (r.get("neutral_band") or band)
    )
    neutral_hit_rate = (
        round(neutral_in_band / len(neutral_recs) * 100, 1) if neutral_recs else None
    )

    # ── 样本充足性 + 置信区间 ──
    # 到不了 194 条就该如实说「尚不能判定优于基线」，而不是给一个好看的数字。
    sample_adequate = len(directional) >= MIN_DIRECTIONAL_FOR_SIGNIFICANCE
    ci_low, ci_high = _wilson_interval(len(directional_correct), len(directional))

    avg_conf = round(sum(r.get("confidence", 0) for r in all_records) / total, 1) if total else 0
    
    # 各模块准确率 —— 用与主口径相同的 judge_verdict，并单列「有观点」分母
    # 2026-09-13: 旧实现用固定 ±0.5 且 dir 必须等于 "neutral" 才算对，
    # 而模块的方向取值里 neutral 占比极高，导致 accuracy 恒为 0；
    # 现在用同一套判据，"total" 只数模块真的表达了方向（非 no_view）的记录。
    module_stats = {}
    for rec in verified:
        actual = rec.get("actual_return")
        if actual is None:
            continue
        rec_band = rec.get("neutral_band") or band
        for mod_name, snap in rec.get("module_snapshots", {}).items():
            mod_dir = snap.get("direction", "neutral")
            verdict = judge_verdict(mod_dir, actual, rec_band)
            st = module_stats.setdefault(
                mod_name, {"total": 0, "correct": 0, "no_view": 0}
            )
            st[f"verdict_{verdict}"] = st.get(f"verdict_{verdict}", 0) + 1
            if verdict == "no_view":
                st["no_view"] += 1
                continue
            st["total"] += 1
            if verdict == "correct":
                st["correct"] += 1

    module_accuracy = {}
    for mod, stats in module_stats.items():
        n = stats["total"]
        module_accuracy[mod] = {
            "total": n,
            "correct": stats["correct"],
            # 样本不足时返回 None，让调用方能区分「命中率 0%」和「样本不够」
            "accuracy": round(stats["correct"] / n * 100, 1) if n else None,
            "no_view": stats["no_view"],
            "sample_adequate": n >= MIN_MODULE_RECORDS_FOR_EMA,
        }

    # 统计最近7天内有多少重复记录（用于前端提示）
    cutoff_7d = (datetime.now() - timedelta(days=7)).isoformat()
    recent_all = sorted(all_records, key=lambda r: r.get("recorded_at", ""), reverse=True)
    recent_10 = recent_all[:10]
    
    return {
        "total": total,
        "verified": len(verified),
        "pending": len(pending),
        "correct": len(correct),
        "wrong": len(wrong),
        "partial": len(partial),
        "no_view": len(no_view),
        # accuracy 现含义 = 有观点时的方向命中率；分母不含 neutral / blocked
        "accuracy": accuracy,
        "directional_total": len(directional),
        "directional_correct": len(directional_correct),
        "directional_accuracy": directional_accuracy,
        # 基线对照：没有它，上面的命中率无法解释
        "baseline_always_bullish": baseline_always_bullish,
        "baseline_always_bearish": baseline_always_bearish,
        "no_view_rate": no_view_rate,
        "neutral_count": len(neutral_recs),
        "neutral_in_band": neutral_in_band,
        "neutral_hit_rate": neutral_hit_rate,
        "neutral_band_pct": band,
        # 样本充足性 —— 到不了门槛时前端必须显示「尚不能判定」，不得渲染成有效结论
        "sample_adequate": sample_adequate,
        "required_samples": MIN_DIRECTIONAL_FOR_SIGNIFICANCE,
        "accuracy_ci95": [ci_low, ci_high],
        "avg_confidence": avg_conf,
        # 历史遗留：2026-09-13 前的记录里 confidence 曾被算成 >100（最高 1041）。
        # 那是决策时刻的真实快照，不改写历史，但必须让调用方知道这批数字不可用。
        "confidence_out_of_range": sum(
            1 for r in all_records
            if isinstance(r.get("confidence"), (int, float))
            and not (0 <= r["confidence"] <= 100)
        ),
        "metric_revision": "2026-09-13",
        "module_accuracy": module_accuracy,
        "recent": recent_10,
        "verify_days": VERIFY_DAYS,  # 告诉前端验证周期是几个交易日
        "can_calibrate": len(verified) >= MIN_RECORDS_FOR_EMA,
        "calibrate_needed": MIN_RECORDS_FOR_EMA,
        "module_calibrate_needed": MIN_MODULE_RECORDS_FOR_EMA,
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


def _verified_records(user_id: str, months: int = 12) -> list:
    """该用户全部已验证记录，按 recorded_at 升序 —— 供样本外切分使用。"""
    out = []
    for path in _month_files(user_id):
        try:
            recs = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(recs, list):
            out.extend(r for r in recs if r.get("verified"))
    out.sort(key=lambda r: r.get("recorded_at", ""))
    return out


def _weighted_vote_hit(records: list, weights: dict) -> tuple:
    """用 weights 对模块方向加权投票，与市场实际方向比对，返回 (hit, total)。

    - 中性带内的记录跳过（市场没动，方向对错都算不上）
    - 权重表覆盖不到的模块按「表中非零权重的均值」计入。
      ⚠️ 这不是细节：DEFAULT_WEIGHTS 的 8 个键里，monte_carlo / rl_position /
      portfolio_optimizer / genetic_factor / alt_data 在线上判断记录里**从未出现过**，
      而实际出场的 broker_research / factor_data / geopolitical / market_factors /
      news_data / sector_rotation / signal_scout 在权重表里**没有键**。
      若按「查不到就是 0」处理，7/10 个模块会被静默忽略。
    """
    known = [float(v) for v in weights.values()
             if isinstance(v, (int, float)) and v > 0]
    fallback_w = (sum(known) / len(known)) if known else 0.0

    hit = total = 0
    for r in records:
        actual = r.get("actual_return")
        snaps = r.get("module_snapshots") or {}
        if actual is None or not snaps:
            continue
        band = r.get("neutral_band") or NEUTRAL_BAND_FALLBACK
        if abs(actual) <= band:
            continue
        bull = bear = 0.0
        for mod, snap in snaps.items():
            d = (snap or {}).get("direction")
            w = weights.get(mod)
            w = float(w) if isinstance(w, (int, float)) else fallback_w
            if d == "bullish":
                bull += w
            elif d == "bearish":
                bear += w
        if bull == bear:
            continue          # 加权后打平 → 没有方向观点
        total += 1
        if (bull > bear) == (actual > 0):
            hit += 1
    return hit, total


def _walk_forward_check(user_id: str, candidate_weights: dict) -> dict:
    """前 70% 样本定权重、后 30% 验证；样本外不得劣于等权。

    2026-09-13 新增。没有这道闸门，EMA 校准就是在训练集上给自己发奖状：
    它把「在历史上表现好的模块」加权，但没有任何样本证明这能外推。

    拒绝条件（任一成立即拒绝，且**不写入** weights.json）：
      1. 已验证样本 < MIN_RECORDS_FOR_EMA
      2. 样本外「真的能判方向」的样本 < 10（测不出任何东西）
      3. 样本外加权命中率 < 等权命中率（调权没带来好处，只有过拟合风险）
    """
    recs = _verified_records(user_id)
    n = len(recs)
    if n < MIN_RECORDS_FOR_EMA:
        return {"passed": False, "samples_total": n, "reason":
                f"已验证样本 {n} < {MIN_RECORDS_FOR_EMA}"}

    split = int(n * 0.7)
    train, test = recs[:split], recs[split:]

    cand_hit, cand_n = _weighted_vote_hit(test, candidate_weights)
    eq_hit, eq_n = _weighted_vote_hit(test, DEFAULT_WEIGHTS)

    detail = {
        "passed": False,
        "samples_total": n,
        "train": len(train),
        "test": len(test),
        "oos_directional": cand_n,
        "oos_weighted_accuracy": round(cand_hit / cand_n * 100, 1) if cand_n else None,
        "oos_equal_weight_accuracy": round(eq_hit / eq_n * 100, 1) if eq_n else None,
    }

    if cand_n < 10:
        detail["reason"] = f"样本外可判方向样本仅 {cand_n} < 10，测不出结论"
        return detail
    if eq_n and cand_hit / cand_n < eq_hit / eq_n:
        detail["reason"] = (
            f"样本外加权命中率 {detail['oos_weighted_accuracy']}% "
            f"低于等权 {detail['oos_equal_weight_accuracy']}% —— 调权只有过拟合风险"
        )
        return detail

    detail["passed"] = True
    detail["reason"] = "样本外不劣于等权"
    return detail


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
        mod_row = mod_acc.get(mod_name) or {}
        mod_n = mod_row.get("total") or 0
        mod_accuracy = mod_row.get("accuracy")

        # 2026-09-13: 门槛 3 → MIN_MODULE_RECORDS_FOR_EMA(30)。
        # n=3 的 95% 置信区间约 [21%, 94%] —— 在证明不了任何事的样本量上永久改权重。
        # accuracy 为 None 表示该模块分母为 0（从未表达过方向），同样不参与调权。
        if mod_accuracy is not None and mod_n >= MIN_MODULE_RECORDS_FOR_EMA:
            acc = mod_accuracy / 100.0  # 0~1
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

    # ── 样本外闸门（2026-09-13 新增）──
    # 在训练集上算出来的权重必须在样本外也不比等权差，否则就是自我加冕。
    wf = _walk_forward_check(user_id, new_weights)
    if not wf["passed"]:
        return {
            "status": "rejected_oos",
            "message": "样本外检验未通过，权重未更新：" + wf["reason"],
            "records_used": card["verified"],
            "overall_accuracy": card["accuracy"],
            "walk_forward": wf,
            "calibrated_at": datetime.now().isoformat(),
        }

    # 保存
    weight_data = {
        "weights": new_weights,
        "calibrated_at": datetime.now().isoformat(),
        "records_used": card["verified"],
        "overall_accuracy": card["accuracy"],
        "walk_forward": wf,
        # ⚠️ 如实标注：这两个字段目前**没有消费方**。
        # pipeline_runner.step_confidence_gate 在 step_ema_calibration 之前执行，
        # 门控用的是未加权的一致分；ctx.module_weights 只被赋值、全仓无读取点。
        # 也就是说这份权重当前只影响展示，不影响任何决策。要让它真正生效必须显式改门控。
        "consumers": [],
        "consumer_note": "module_weights 当前无读取方，权重仅用于展示",
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
        "walk_forward": wf,
        # 如实标注：当前没有任何代码读取这份权重（见 weights.json 的 consumer_note）
        "consumers": [],
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
