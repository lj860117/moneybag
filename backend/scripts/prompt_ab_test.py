#!/usr/bin/env python3
"""
钱袋子 Prompt A/B 测试脚本
=======================================
用法：
  cd moneybag
  python backend/scripts/prompt_ab_test.py --prompt system_prompt --old v1 --new v2

作用：
  1. 读固定场景集（prompt_ab_cases.json）
  2. 分别用 old 和 new 两个版本的 prompt 跑同一组输入
  3. 多维度评分（数据诚信/免责声明/非交易日铁律/字数/结论明确度）
  4. 输出对比表格 + 明确的"允许/拒绝合并"建议

设计原则：
  - 不调 pytest，脚本独立可跑
  - 使用项目自己的 LLMGateway（复用缓存/计费/熔断）
  - 失败场景落盘到 data/ab_test_runs/{timestamp}/ 方便复盘
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# 让脚本能直接 import backend.infra.llm.gateway。
# 两个路径缺一不可：backend/infra/llm/__init__.py 内部用的是以 backend/ 为根的
# 绝对导入（`from infra.llm.gateway import LLMClient`），只插仓库根会在那里
# 报 ModuleNotFoundError: No module named 'infra'；只插 backend/ 又会找不到
# `backend.…` 这个包前缀。因此凭脚本自身路径补齐，免外部 PYTHONPATH。
ROOT = Path(__file__).resolve().parents[2]          # 仓库根 moneybag/
BACKEND_DIR = Path(__file__).resolve().parents[1]   # backend/
for _p in (str(ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.infra.llm.gateway import LLMGateway  # noqa: E402


# ==================== 配置 ====================

PROMPTS_DIR = ROOT / "backend" / "prompts"
VERSIONS_DIR = PROMPTS_DIR / "versions"
CASES_FILE = ROOT / "backend" / "scripts" / "prompt_ab_cases.json"
OUTPUT_DIR = ROOT / "data" / "ab_test_runs"


# ==================== 评分器 ====================

class Scorer:
    """对单次回答做多维度打分"""

    def __init__(self, rules: dict):
        self.forbidden = rules["forbidden_phrases_strict"]
        self.safety = rules["safety_phrases_expected"]
        self.holiday_violation_kw = rules["trading_day_violation_keywords"]
        self.thresholds = rules["thresholds"]

    def score(self, text: str, case: dict) -> dict:
        """给一条回答打分，返回 dict"""
        text = text or ""
        low = text.lower()

        # 1. 数据诚信：是否命中禁用词
        forbidden_hits = [w for w in self.forbidden if w in text or w.lower() in low]

        # 2. 免责声明是否出现
        has_safety = any(s in text for s in self.safety)

        # 3. 非交易日场景下是否违反铁律（编造涨跌）
        is_holiday_case = not case.get("system_vars", {}).get("is_trading_day", True)
        holiday_violation = False
        if is_holiday_case:
            holiday_violation = any(kw in text for kw in self.holiday_violation_kw)

        # 4. 字数
        length = len(text)
        length_ok = (
            self.thresholds["avg_length_min"]
            <= length
            <= self.thresholds["avg_length_max"]
        )

        # 5. 结论明确度（简单启发式）
        conclusion_keywords = [
            "买入", "持有", "卖出", "减仓", "加仓", "观望", "不建议",
            "建议", "方向：", "结论：", "评级", "倾向", "优先", "暂停",
        ]
        has_conclusion = any(k in text for k in conclusion_keywords)

        return {
            "forbidden_hits": forbidden_hits,
            "data_integrity_ok": len(forbidden_hits) == 0,
            "safety_disclaimer": has_safety,
            "holiday_rule_ok": (not is_holiday_case) or (not holiday_violation),
            "holiday_violation": holiday_violation,
            "length": length,
            "length_ok": length_ok,
            "has_conclusion": has_conclusion,
        }


def aggregate(rows: list[dict]) -> dict:
    """按场景批量聚合分数"""
    n = len(rows)
    if n == 0:
        return {}
    di = sum(1 for r in rows if r["data_integrity_ok"]) / n
    sd = sum(1 for r in rows if r["safety_disclaimer"]) / n
    hv = sum(1 for r in rows if r.get("holiday_violation", False))
    conc = sum(1 for r in rows if r["has_conclusion"]) / n
    avg_len = sum(r["length"] for r in rows) / n
    return {
        "data_integrity_rate": round(di, 3),
        "safety_disclaimer_rate": round(sd, 3),
        "holiday_violation_count": hv,
        "conclusion_rate": round(conc, 3),
        "avg_length": round(avg_len, 1),
    }


# ==================== 证据有效性 ====================

# 短于此长度的正文不可能是有效的复盘/诊断回答
MIN_VALID_ANSWER_CHARS = 5

# run_case 拿不到真实模型回答时写入的占位串前缀
FALLBACK_MARKER = "[FALLBACK:"


def is_valid_answer(text: str) -> bool:
    """这条回答是不是"真实模型回答"。

    无效 = 占位串（`[FALLBACK: …]`）/ 空串 / 纯空白 / 短于 MIN_VALID_ANSWER_CHARS。
    这类回答不含任何模型判断，却天然不含禁用词，拿去打分只会得到
    "数据诚信率 100%" 的**假绿** —— 必须单独统计，不能混进评分。
    """
    body = (text or "").strip()
    if not body:
        return False
    if FALLBACK_MARKER in body:
        return False
    return len(body) >= MIN_VALID_ANSWER_CHARS


def answer_validity(answers: list[str]) -> dict:
    """汇总一组回答的有效性，供指标表与判决闸门使用。"""
    invalid = sum(1 for a in answers if not is_valid_answer(a))
    return {"total_answers": len(answers), "invalid_answers": invalid}


# ==================== 合并判决 ====================

VERDICT_ALLOW = "allow"
VERDICT_REJECT = "reject"
VERDICT_INVALID = "invalid"


def evidence_gate(old_agg: dict, new_agg: dict) -> list[str]:
    """证据有效性闸门：返回阻断原因（空列表 = 证据有效，可继续判决）。

    只要**存在任何**无效回答（fallback 占位串 / 空 / 过短），本次 A/B 就没有证据价值：
    指标表里的比例（尤其"数据诚信率 100%"）只是占位串不含禁用词的结果，
    绝不能当成通过依据。缺统计字段时同样拒绝给肯定判决（fail-closed）。
    """
    stats = []
    for label, agg in (("旧版", old_agg), ("新版", new_agg)):
        total = agg.get("total_answers")
        invalid = agg.get("invalid_answers")
        if total is None or invalid is None:
            return [
                "⛔ 判决无效：缺少证据有效性统计（total_answers / invalid_answers 未提供），"
                "无法确认回答来自真实模型，拒绝给出肯定判决",
            ]
        if invalid:
            stats.append(f"{label} {invalid}/{total}")

    if not stats:
        return []

    total_all = (old_agg.get("total_answers", 0) + new_agg.get("total_answers", 0))
    invalid_all = (old_agg.get("invalid_answers", 0) + new_agg.get("invalid_answers", 0))
    lines = [
        f"⛔ 判决无效：未取得真实模型回答（{invalid_all}/{total_all} 为 fallback/空），"
        "本次 A/B 无证据价值",
        "   分版本：" + "，".join(stats),
        "   说明：指标表里的比例（含「数据诚信率 100%」）由占位串得出，不能作为通过依据；",
        "   请在有 LLM key 的环境重跑后再据此判断是否合并。",
    ]
    return lines


def decide(old_agg: dict, new_agg: dict, rules: dict) -> tuple[str, list[str]]:
    """A/B 判决的**唯一入口**：先过证据有效性闸门，再走原有评分/阈值判决。

    返回 (verdict, reasons)，verdict ∈ {allow, reject, invalid}。
    回答全部真实时，结果与改动前完全一致（阈值与比较逻辑未动）。
    """
    blocked = evidence_gate(old_agg, new_agg)
    if blocked:
        return VERDICT_INVALID, blocked
    allow, reasons = judge_merge(old_agg, new_agg, rules)
    return (VERDICT_ALLOW if allow else VERDICT_REJECT), reasons


def judge_merge(old_agg: dict, new_agg: dict, rules: dict) -> tuple[bool, list[str]]:
    """决定新版能否合并到线上，返回 (allow, reasons)"""
    th = rules["thresholds"]
    reasons = []

    # 🔴 红线 1：数据诚信率必须 = 100%
    if new_agg["data_integrity_rate"] < th["data_integrity_rate_min"]:
        reasons.append(
            f"❌ 数据诚信率 {new_agg['data_integrity_rate']:.1%} < 红线 {th['data_integrity_rate_min']:.0%}"
        )

    # 🔴 红线 2：非交易日违规次数必须 = 0
    if new_agg["holiday_violation_count"] > th["holiday_rule_violation_max"]:
        reasons.append(
            f"❌ 非交易日铁律违反 {new_agg['holiday_violation_count']} 次 > 允许 {th['holiday_rule_violation_max']}"
        )

    # 🟡 警戒 1：免责声明率
    if new_agg["safety_disclaimer_rate"] < th["safety_disclaimer_rate_min"]:
        reasons.append(
            f"⚠️ 免责声明率 {new_agg['safety_disclaimer_rate']:.1%} < 目标 {th['safety_disclaimer_rate_min']:.0%}"
        )

    # 🟡 警戒 2：新版不应该比旧版明显退步
    for key, label in [
        ("data_integrity_rate", "数据诚信率"),
        ("safety_disclaimer_rate", "免责声明率"),
        ("conclusion_rate", "结论明确度"),
    ]:
        if new_agg[key] < old_agg[key] - 0.05:
            reasons.append(
                f"⚠️ {label}退步: {old_agg[key]:.1%} → {new_agg[key]:.1%}"
            )

    # 字数过长过短
    if new_agg["avg_length"] > th["avg_length_max"]:
        reasons.append(f"⚠️ 平均字数 {new_agg['avg_length']:.0f} 超出上限 {th['avg_length_max']}")
    if new_agg["avg_length"] < th["avg_length_min"]:
        reasons.append(f"⚠️ 平均字数 {new_agg['avg_length']:.0f} 低于下限 {th['avg_length_min']}")

    has_red = any(r.startswith("❌") for r in reasons)
    return (not has_red), reasons


# ==================== 核心流程 ====================

def load_prompt(name: str, version: str | None) -> str:
    """读一份 prompt。version=None 读线上版，否则读 versions/{name}.{version}.md"""
    if version is None:
        path = PROMPTS_DIR / f"{name}.md"
    else:
        path = VERSIONS_DIR / f"{name}.{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def run_case(system_prompt: str, case: dict) -> str:
    """跑单个场景，返回 AI 回答文本"""
    user_msg = case.get("user_message", "")
    # diagnose 类场景可能没有 user_message，把 holdings 拼进去
    if not user_msg and "holdings_mock" in case:
        user_msg = f"请诊断这组持仓：{json.dumps(case['holdings_mock'], ensure_ascii=False)}"

    result = LLMGateway.instance().call_sync(
        prompt=user_msg,
        system=system_prompt,
        model_tier="llm_light",
        user_id="prompt_ab_test",
        module="ab_test",
        max_tokens=1000,
    )

    if result.get("fallback"):
        return f"[FALLBACK: {result.get('source')}]"
    return result.get("content", "")


def ab_compare(prompt_name: str, old_ver: str, new_ver: str) -> int:
    """主流程。返回 exit code: 0=允许合并, 1=拒绝合并/判决无效, 2=脚本异常

    注意：判决无效（回答里有 fallback/空，本次无证据价值）也返回 1 ——
    「没拿到真实回答」绝不能被 CI/调用方读成通过。
    """
    try:
        cases_json = json.loads(CASES_FILE.read_text(encoding="utf-8"))
        cases_block = cases_json.get(prompt_name)
        if not cases_block:
            print(f"❌ 场景集中没有 {prompt_name}，已知: {[k for k in cases_json if not k.startswith('_') and k != 'scoring_rules']}")
            return 2

        rules = cases_json["scoring_rules"]
        scorer = Scorer(rules)

        old_prompt = load_prompt(prompt_name, old_ver)
        new_prompt = load_prompt(prompt_name, new_ver)
        cases = cases_block["cases"]

        print(f"\n{'='*70}")
        print(f"🧪 Prompt A/B: {prompt_name}  {old_ver} vs {new_ver}")
        print(f"📋 场景数: {len(cases)}")
        print(f"{'='*70}\n")

        old_scores, new_scores = [], []
        old_answers, new_answers = [], []
        detail_rows = []
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = OUTPUT_DIR / f"{prompt_name}_{old_ver}_vs_{new_ver}_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)

        for i, case in enumerate(cases, 1):
            cid = case["id"]
            cat = case["category"]
            print(f"[{i}/{len(cases)}] {cid} ({cat})")

            # 跑旧版
            t0 = time.time()
            old_out = run_case(old_prompt, case)
            old_s = scorer.score(old_out, case)
            old_scores.append(old_s)
            old_answers.append(old_out)
            print(f"  旧版 {old_ver}: {len(old_out)} 字, 诚信={'✅' if old_s['data_integrity_ok'] else '❌'}, 免责={'✅' if old_s['safety_disclaimer'] else '❌'}, 耗时={time.time()-t0:.1f}s")

            # 跑新版
            t0 = time.time()
            new_out = run_case(new_prompt, case)
            new_s = scorer.score(new_out, case)
            new_scores.append(new_s)
            new_answers.append(new_out)
            print(f"  新版 {new_ver}: {len(new_out)} 字, 诚信={'✅' if new_s['data_integrity_ok'] else '❌'}, 免责={'✅' if new_s['safety_disclaimer'] else '❌'}, 耗时={time.time()-t0:.1f}s")

            # 落盘详情
            (run_dir / f"{cid}.json").write_text(
                json.dumps({
                    "case": case,
                    "old": {"text": old_out, "score": old_s},
                    "new": {"text": new_out, "score": new_s},
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            detail_rows.append({"case": cid, "category": cat, "old": old_s, "new": new_s})
            print()

        # 聚合（有效性统计与评分分开：无效回答不能进评分口径）
        old_agg = aggregate(old_scores)
        new_agg = aggregate(new_scores)
        old_agg.update(answer_validity(old_answers))
        new_agg.update(answer_validity(new_answers))

        print("="*70)
        print("📊 聚合对比")
        print("="*70)
        print(f"{'指标':<25}{'旧版 '+old_ver:<20}{'新版 '+new_ver:<20}{'Δ':>10}")
        print("-"*70)
        for key, label in [
            ("data_integrity_rate", "数据诚信率"),
            ("safety_disclaimer_rate", "免责声明率"),
            ("conclusion_rate", "结论明确度"),
            ("avg_length", "平均字数"),
        ]:
            old_v = old_agg[key]
            new_v = new_agg[key]
            delta = new_v - old_v
            sign = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
            if "rate" in key:
                print(f"{label:<23}{old_v:.1%}{'':<14}{new_v:.1%}{'':<14}{sign}{abs(delta):.1%}")
            else:
                print(f"{label:<23}{old_v:<20.1f}{new_v:<20.1f}{sign}{abs(delta):.1f}")
        print(f"{'非交易日违规次数':<23}{old_agg['holiday_violation_count']:<20}{new_agg['holiday_violation_count']:<20}")
        old_invalid_txt = f"{old_agg['invalid_answers']}/{old_agg['total_answers']}"
        new_invalid_txt = f"{new_agg['invalid_answers']}/{new_agg['total_answers']}"
        print(f"{'无效回答数/总回答数':<20}{old_invalid_txt:<20}{new_invalid_txt:<20}")

        # 判决（唯一入口 decide：先过证据有效性闸门，再走原评分/阈值判决）
        verdict, reasons = decide(old_agg, new_agg, rules)
        print()
        print("="*70)
        if verdict == VERDICT_ALLOW:
            print("✅ 判决：允许合并")
        elif verdict == VERDICT_REJECT:
            print("❌ 判决：拒绝合并")
        else:
            print("⛔ 判决无效：本次 A/B 无证据价值")
        for r in reasons:
            print(f"  {r}")
        print("="*70)
        print(f"\n📁 详情落盘：{run_dir}\n")

        allow = (verdict == VERDICT_ALLOW)

        # 写 summary
        (run_dir / "summary.json").write_text(
            json.dumps({
                "prompt": prompt_name,
                "old_version": old_ver,
                "new_version": new_ver,
                "timestamp": stamp,
                "old_aggregate": old_agg,
                "new_aggregate": new_agg,
                "verdict": verdict,
                "allow_merge": allow,
                "reasons": reasons,
                "cases_total": len(cases),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return 0 if allow else 1

    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 2
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ 脚本异常: {e}")
        return 2


def main():
    ap = argparse.ArgumentParser(description="钱袋子 Prompt A/B 测试")
    ap.add_argument("--prompt", required=True, help="prompt 名，如 system_prompt")
    ap.add_argument("--old", default="v1", help="旧版本，如 v1")
    ap.add_argument("--new", required=True, help="新版本，如 v2")
    args = ap.parse_args()
    sys.exit(ab_compare(args.prompt, args.old, args.new))


if __name__ == "__main__":
    main()
