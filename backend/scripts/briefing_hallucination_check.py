#!/usr/bin/env python3
"""
v9.5.44 A1 晨报幻觉自检脚本（E6 升级版）

用途：
- 连续 3 次拉 /api/steward/briefing
- 校验 regime/regime_description/one_line 中的关键数字（百分位/涨跌幅/波动率）
- 是否能在原始数据源（market timing / news）中找到对应或反推合理
- 输出幻觉嫌疑列表
- E6 新增：检测结果写 logs/hallucination_check.log，有幻觉发企微告警

运行：
  python backend/scripts/briefing_hallucination_check.py
  python backend/scripts/briefing_hallucination_check.py --rounds 5
  python backend/scripts/briefing_hallucination_check.py --alert  # 有问题时发企微

退出码：
  0 = 全部通过
  1 = 检测到 1+ 处幻觉（详见输出）
"""
import argparse
import json
import re
import sys
import time
import os
from datetime import datetime
from urllib.request import urlopen
from urllib.parse import urlencode

DEFAULT_BASE = "http://localhost:8000"  # v9.5.71: 改 localhost 避免走外网回环导致超时

# v9.5.44: 已知合法的 regime 值（含新增的高波动/地缘风险场景）
VALID_REGIMES = {
    "trending_bull", "trending_bear", "volatile", "neutral",
    "recovery", "overheated", "panic",
    "oscillating",  # v9.5.44 漏补：regime_engine 的 4 种基础状态之一（震荡市默认态）
    "牛市", "熊市", "震荡", "中性", "轮动",
    "rotation", "bull", "bear",
    # v9.5.44 新增：高波动场景
    "high_vol_bear", "high_vol_bull", "high_vol_neutral",
    "geo_risk", "risk_off", "crisis",
}


def fetch(url: str, timeout: int = 60, retries: int = 2):
    """v9.5.71: 加重试 + 60s 超时，应对冷启动场景

    07:30 night_worker 刚生成完晨报，07:50 检查时如果 cache 失效需要重新跑一次完整链路（30+ 秒）。
    原 30s 超时不够，且无重试时偶发超时就误报。
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = 5 * (attempt + 1)  # 5s, 10s 退避
                print(f"  ⚠️ fetch attempt {attempt+1} failed: {e}, retry in {wait}s")
                time.sleep(wait)
    raise last_err


def extract_numbers(text: str):
    """从中文晨报文本里抽取所有数字（百分比/分位/具体数）"""
    if not text:
        return []
    pat = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|分位|亿|万|倍)?")
    out = []
    for m in pat.finditer(text):
        val = float(m.group(1))
        unit = m.group(2) or ""
        out.append({"value": val, "unit": unit, "raw": m.group(0)})
    return out


def cross_check_briefing(b: dict, market_timing: dict) -> list:
    """对比 briefing 中的数字 vs market_timing/估值原数据"""
    issues = []
    desc = b.get("regime_description", "")
    one_line = b.get("one_line", "")

    # 抽取 desc 里的关键数字（应能在 market_timing 中找到）
    nums = extract_numbers(desc) + extract_numbers(one_line)

    # market_timing 真实值
    mt_pct = market_timing.get("valuation_pct")
    mt_fgi = market_timing.get("fgi")

    # 数字范围合理性检查
    for n in nums:
        v = n["value"]
        # 百分比 > 100% 或 < -100% 一律可疑
        if n["unit"] == "%" and (v > 100 or v < -100):
            issues.append(f"⚠️ 不合理百分比：{n['raw']}（应在 -100~100 之间）")
        # 分位 > 100 必假
        if n["unit"] == "分位" and (v > 100 or v < 0):
            issues.append(f"❌ 不合理分位值：{n['raw']}（应在 0~100 之间）")

    # 关键术语可信度
    for keyword, exp_pct_range in [
        ("低估", (0, 40)),
        ("高估", (60, 100)),
        ("过热", (80, 100)),
        ("恐慌", (0, 30)),
    ]:
        if keyword in desc and mt_pct is not None:
            lo, hi = exp_pct_range
            if not (lo <= mt_pct <= hi):
                issues.append(
                    f"❌ 语义不匹配：晨报含「{keyword}」但估值百分位={mt_pct:.0f}（期望 {lo}~{hi}）"
                )

    # regime 必须在已知列表
    rg = b.get("regime", "")
    if rg and rg not in VALID_REGIMES:
        issues.append(f"⚠️ 未知 regime 值：{rg}（可能需要更新白名单）")

    # ⚠️ regime vs 估值矛盾检查
    # v9.5.43 修复：如 one_line 已包含估值警示词，则不再报矛盾
    has_valuation_warn = any(kw in one_line for kw in ["估值", "高位", "便宜", "分位"])

    if mt_pct is not None:
        bullish_regimes = {"trending_bull", "牛市", "bull", "high_vol_bull"}
        if rg in bullish_regimes and mt_pct >= 85 and not has_valuation_warn:
            issues.append(
                f"⚠️ 矛盾：regime={rg}（看多） 但估值百分位={mt_pct:.0f}（>85% 高位）— "
                "晨报应同时提示「估值偏高，警惕回调」"
            )
        bearish_regimes = {"trending_bear", "熊市", "bear", "panic"}
        if rg in bearish_regimes and mt_pct <= 25 and not has_valuation_warn:
            issues.append(
                f"⚠️ 矛盾：regime={rg}（看空） 但估值百分位={mt_pct:.0f}（<25% 低位）— "
                "晨报应同时提示「估值已便宜，可分批布局」"
            )

    # 「风控正常」+ 高估值百分位 = 可能漏报（除非 one_line 已含估值警示）
    if "风控正常" in one_line and mt_pct is not None and mt_pct >= 85 and not has_valuation_warn:
        issues.append(
            f"⚠️ 漏报：one_line=「{one_line}」称风控正常，但估值={mt_pct:.0f}% 已超 85% 高位 — "
            "应至少提示「高估值警示」"
        )

    return issues


def send_wecom_alert(issues: list, base: str):
    """E6: 检测到幻觉时发企微告警"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from services.wxwork_push import is_configured, send_text
        if not is_configured():
            print("  [告警] 企微未配置，跳过推送")
            return
        ts = datetime.now().strftime("%m-%d %H:%M")
        msg = f"⚠️ 钱袋子晨报幻觉自检告警 [{ts}]\n\n"
        msg += f"检测到 {len(issues)} 项问题：\n"
        for iss in issues[:5]:  # 最多显示 5 条
            msg += f"• {iss}\n"
        if len(issues) > 5:
            msg += f"...以及 {len(issues)-5} 项更多问题\n"
        msg += f"\n请检查 night_worker 日志（02:30 前生效的改动）"
        result = send_text(msg)
        print(f"  [告警] 企微推送结果: {result}")
    except Exception as e:
        print(f"  [告警] 企微推送失败: {e}")


def write_log(log_path: str, all_issues: list, briefings: list, mt: dict):
    """E6: 写检测日志"""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "PASS" if not all_issues else "FAIL"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] {status} — {len(all_issues)} issues, "
                    f"rounds={len(briefings)}, "
                    f"pct={mt.get('valuation_pct','N/A')}, "
                    f"regime={briefings[0].get('regime','N/A') if briefings else 'N/A'}\n")
            for iss in all_issues:
                f.write(f"  • {iss}\n")
        print(f"  [日志] 写入 {log_path}")
    except Exception as e:
        print(f"  [日志] 写入失败: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--user", default="leijiang")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--interval", type=int, default=2)
    ap.add_argument("--alert", action="store_true", help="有幻觉时发企微告警")
    ap.add_argument("--log", default="", help="日志路径（空=不写日志）")
    args = ap.parse_args()

    print(f"[A1] 晨报幻觉自检 — base={args.base} user={args.user} rounds={args.rounds}")
    print("=" * 60)

    all_issues = []
    briefings = []

    for i in range(args.rounds):
        try:
            url = f"{args.base}/api/steward/briefing?userId={args.user}"
            b = fetch(url)
            briefings.append(b)
            print(f"\n[Round {i+1}] briefing fetched (cache={b.get('from_cache')}, "
                  f"elapsed={b.get('elapsed', 0):.1f}s)")
            print(f"  regime: {b.get('regime')}")
            print(f"  desc:   {b.get('regime_description', '')[:80]}")
            print(f"  one:    {b.get('one_line', '')}")
        except Exception as e:
            print(f"  ❌ fetch failed: {e}")
            all_issues.append(f"Round {i+1}: fetch failed - {e}")
            continue
        time.sleep(args.interval)

    # 拉一次 market_timing 真实数据做交叉验证
    mt = {}
    try:
        # 复用 stock-screen 里的 market_timing 摘要
        ss = fetch(f"{args.base}/api/stock-screen?top_n=1")
        mt = ss.get("market_timing", {})
        print(f"\n[基准] market_timing: pct={mt.get('valuation_pct')} fgi={mt.get('fgi')} "
              f"verdict={mt.get('verdict')}")
    except Exception as e:
        print(f"\n⚠️ market_timing 拉取失败: {e}")

    # 检查每条 briefing
    print("\n" + "=" * 60)
    print("[幻觉检测]")
    for i, b in enumerate(briefings):
        issues = cross_check_briefing(b, mt)
        if issues:
            print(f"\n  Round {i+1}:")
            for iss in issues:
                print(f"    {iss}")
                all_issues.append(f"R{i+1}: {iss}")
        else:
            print(f"  Round {i+1}: ✅ 无幻觉")

    # 一致性检查（多轮 briefing 应该一致或合理变化）
    if len(briefings) >= 2:
        regimes = {b.get("regime") for b in briefings}
        if len(regimes) > 1:
            all_issues.append(f"⚠️ 多轮 regime 不一致：{regimes}（缓存或决策不稳定）")
            print(f"\n⚠️ 多轮 regime 不一致：{regimes}")

    print("\n" + "=" * 60)

    # E6: 写日志
    log_path = args.log or os.path.join(
        os.path.dirname(__file__), "..", "logs", "hallucination_check.log"
    )
    write_log(log_path, all_issues, briefings, mt)

    if all_issues:
        print(f"❌ 检测到 {len(all_issues)} 项问题：")
        for iss in all_issues:
            print(f"  • {iss}")

        # E6: 有幻觉时发企微告警
        if args.alert:
            print("\n[告警] 正在发送企微告警...")
            send_wecom_alert(all_issues, args.base)

        sys.exit(1)
    else:
        print("✅ 所有检查通过，未发现幻觉")
        sys.exit(0)


if __name__ == "__main__":
    main()
