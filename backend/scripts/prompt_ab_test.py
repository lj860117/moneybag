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

# 让脚本能直接 import backend.services.llm_gateway
ROOT = Path(__file__).resolve().parents[2]  # moneybag/
sys.path.insert(0, str(ROOT))

from backend.services.llm_gateway import LLMGateway  # noqa: E402


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


# ==================== 合并判决 ====================

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
    """主流程。返回 exit code: 0=允许合并, 1=拒绝合并, 2=脚本异常"""
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
            print(f"  旧版 {old_ver}: {len(old_out)} 字, 诚信={'✅' if old_s['data_integrity_ok'] else '❌'}, 免责={'✅' if old_s['safety_disclaimer'] else '❌'}, 耗时={time.time()-t0:.1f}s")

            # 跑新版
            t0 = time.time()
            new_out = run_case(new_prompt, case)
            new_s = scorer.score(new_out, case)
            new_scores.append(new_s)
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

        # 聚合
        old_agg = aggregate(old_scores)
        new_agg = aggregate(new_scores)

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

        # 判决
        allow, reasons = judge_merge(old_agg, new_agg, rules)
        print()
        print("="*70)
        if allow:
            print("✅ 判决：允许合并")
        else:
            print("❌ 判决：拒绝合并")
        for r in reasons:
            print(f"  {r}")
        print("="*70)
        print(f"\n📁 详情落盘：{run_dir}\n")

        # 写 summary
        (run_dir / "summary.json").write_text(
            json.dumps({
                "prompt": prompt_name,
                "old_version": old_ver,
                "new_version": new_ver,
                "timestamp": stamp,
                "old_aggregate": old_agg,
                "new_aggregate": new_agg,
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
