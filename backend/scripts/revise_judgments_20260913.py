"""
一次性数据修订：把 2026-09-13 前用错误窗口算出的 actual_return / verdict 作废重算。

背景（已线上实测确认）
----------------------
2026-09-13 之前，judgment_tracker._get_actual_return 取的是
    recent = data[0]; older = data[15]
并配了一句错误注释「data 是按日期降序排列」。实际 tushare_data.get_index_daily
返回**升序**，于是算出来的是「今天往前 34~55 天」这段与预测日完全无关的窗口。

实测证据：71 条已验记录只有 23 个不同的 actual_return 值（同日批量验证必然同值），
前 12 条全是 -7.28。判决口径也同时是坏的（固定 ±0.5% 让 neutral 命中 0/70）。

为什么必须重算
--------------
verify_pending 只处理 verified == False 的记录。若不重置，这 71 条的历史脏数据
会永久留在成绩单里 —— 口径修好了，数字还是假的。

做了什么
--------
1. 把每个月份文件备份到 <name>.bak-20260913（只备份一次，不覆盖已有备份）
2. 把 verified == True 且 metric_revision != "2026-09-13" 的记录重置为
   verified = False，并清除 actual_return / verdict / verified_at / neutral_band
   —— 注意：**不改写** confidence / direction / recorded_at 等决策时刻的快照，
   那些是历史事实（哪怕 confidence=1041 是 bug 产物），改写它们等于篡改审计记录。
3. 调用修订后的 verify_pending() 按 recorded_at 锚定的窗口重新验证
4. 打印前后对照

用法
----
    python scripts/revise_judgments_20260913.py                 # 预演，不写盘
    python scripts/revise_judgments_20260913.py --apply         # 真正执行
    python scripts/revise_judgments_20260913.py --apply --user LeiJiang

幂等：已带 metric_revision 标记的记录会跳过，重复执行安全。
"""
import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DATA_DIR  # noqa: E402

REVISION_TAG = "2026-09-13"
BACKUP_SUFFIX = ".bak-20260913"


def _judgments_root() -> Path:
    return DATA_DIR / "judgments"


def _user_dirs(only_user: str = "") -> list:
    root = _judgments_root()
    if not root.exists():
        return []
    dirs = [p for p in sorted(root.iterdir()) if p.is_dir()]
    if only_user:
        dirs = [p for p in dirs if p.name == only_user]
    return dirs


def _inspect(uid: str) -> dict:
    """统计该用户将被重置的记录数（不改盘）。"""
    d = _judgments_root() / uid
    old_verdicts = Counter()
    to_reset = 0
    total = 0
    for f in sorted(d.glob("*.json")):
        if f.name == "weights.json" or f.name.endswith(BACKUP_SUFFIX):
            continue
        try:
            recs = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(recs, list):
            continue
        for r in recs:
            total += 1
            if r.get("metric_revision") == REVISION_TAG:
                continue
            if r.get("verified"):
                to_reset += 1
                old_verdicts[r.get("verdict")] += 1
    return {"total": total, "to_reset": to_reset, "old_verdicts": dict(old_verdicts)}


def _reset_user(uid: str, apply: bool) -> int:
    """备份 + 重置。返回被重置的记录数。"""
    d = _judgments_root() / uid
    touched = 0
    for f in sorted(d.glob("*.json")):
        if f.name == "weights.json" or f.name.endswith(BACKUP_SUFFIX):
            continue
        try:
            recs = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(recs, list):
            continue

        changed = False
        for r in recs:
            if r.get("metric_revision") == REVISION_TAG:
                continue
            if not r.get("verified"):
                continue
            r["verified"] = False
            for k in ("actual_return", "verdict", "verified_at", "neutral_band"):
                r.pop(k, None)
            touched += 1
            changed = True

        if changed and apply:
            backup = f.with_name(f.name + BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy2(f, backup)
            f.write_text(
                json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return touched


def main() -> int:
    ap = argparse.ArgumentParser(description="作废并重算 2026-09-13 前的判断验证结果")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只预演）")
    ap.add_argument("--user", default="", help="只处理某个用户 id")
    ap.add_argument("--skip-verify", action="store_true",
                    help="只重置不重新验证（用于分步排障）")
    args = ap.parse_args()

    users = _user_dirs(args.user)
    if not users:
        print(f"未找到任何用户目录: {_judgments_root()}")
        return 1

    print(f"DATA_DIR = {DATA_DIR}")
    print(f"模式 = {'APPLY（写盘）' if args.apply else 'DRY-RUN（只预演）'}")
    print("=" * 68)

    summary = {}
    for d in users:
        uid = d.name
        before = _inspect(uid)
        summary[uid] = before
        print(f"\n用户 {uid}")
        print(f"  总记录 {before['total']}，待重置 {before['to_reset']}"
              f"，旧判决分布 {before['old_verdicts'] or '{}'}")
        if not before["to_reset"]:
            print("  无需重置")
            continue

        if args.apply:
            n = _reset_user(uid, apply=True)
            print(f"  已重置 {n} 条（月份文件备份后缀 {BACKUP_SUFFIX}）")
        else:
            print(f"  [预演] 将重置 {before['to_reset']} 条并重新验证")

    if not args.apply:
        print("\n" + "=" * 68)
        print("这是预演，没有写盘。加 --apply 真正执行。")
        return 0

    if args.skip_verify:
        print("\n已跳过重新验证（--skip-verify）")
        return 0

    # ── 重新验证 ──
    print("\n" + "=" * 68)
    print("重新验证（按 recorded_at 锚定的窗口）")
    from services.judgment_tracker import scorecard, verify_pending

    for d in users:
        uid = d.name
        try:
            done = verify_pending(uid)
            print(f"\n用户 {uid}: 本轮重新验证 {len(done)} 条")
        except Exception as e:
            print(f"\n用户 {uid}: 重新验证失败 -> {e}")
            continue

        card = scorecard(uid, months=12)
        print(f"  已验证 {card['verified']}（其中带方向 {card['directional_total']}）"
              f" 待验证 {card['pending']}")
        print(f"  方向命中率 {card['directional_accuracy']}%  "
              f"95%CI {card['accuracy_ci95']}")
        print(f"  基线(永远喊多) {card['baseline_always_bullish']}%  "
              f"无观点率 {card['no_view_rate']}%")
        print(f"  中性带 ±{card['neutral_band_pct']}%  "
              f"neutral 落在带内 {card['neutral_in_band']}/{card['neutral_count']}")
        print(f"  样本充足: {card['sample_adequate']}"
              f"（需 {card['required_samples']} 条，当前 {card['directional_total']}）")
        ma = card["module_accuracy"]
        if ma:
            print("  模块明细:")
            for mod, row in sorted(ma.items(), key=lambda kv: -(kv[1]["total"] or 0)):
                print(f"    {mod:18s} 有方向 {row['total']:3d} 命中 {row['correct']:3d} "
                      f"命中率 {row['accuracy']}  sample_adequate={row['sample_adequate']}")

    print("\n" + "=" * 68)
    print(f"完成于 {datetime.now().isoformat()}")
    print(f"如需回滚：把各月份文件的 {BACKUP_SUFFIX} 备份拷回原名。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
