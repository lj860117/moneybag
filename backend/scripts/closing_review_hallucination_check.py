#!/usr/bin/env python3
"""
v9.8.10 收盘复盘幻觉自检脚本

用途：
- 读取今日收盘复盘结果（steward review + R1 诊断）
- 校验 AI 诊断中的关键数字（基金涨跌幅/板块表现/盈亏数据）
- 对比实际持仓数据（fund_realtime / stock_realtime）
- 输出幻觉嫌疑列表
- 检测到幻觉时发企微告警

运行：
  python backend/scripts/closing_review_hallucination_check.py
  python backend/scripts/closing_review_hallucination_check.py --alert  # 有问题时发企微
  python backend/scripts/closing_review_hallucination_check.py --user leijiang

退出码：
  0 = 全部通过
  1 = 检测到 1+ 处幻觉（详见输出）
"""
import argparse
import json
import re
import sys
import os
import time
from datetime import datetime, date
from pathlib import Path

# 添加 backend 到路径
_BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND))

from config import DATA_DIR

MONITOR_DIR = DATA_DIR / "monitor"

# 合理的涨跌幅范围（单只基金单日最大涨跌）
MAX_FUND_CHANGE = 20.0  # QDII 基金可能因汇率波动超过 10%
MIN_FUND_CHANGE = -20.0

# 合理的板块涨跌幅范围
MAX_SECTOR_CHANGE = 15.0
MIN_SECTOR_CHANGE = -15.0


def load_review_data(user_id: str, target_date: str = None) -> dict:
    """加载指定用户的收盘复盘数据"""
    if not target_date:
        target_date = date.today().isoformat()
    
    review_dir = MONITOR_DIR / user_id / "reviews"
    
    result = {
        "date": target_date,
        "steward_review": {},
        "diagnosis": "",
        "has_data": False,
    }
    
    # 1. 加载 steward review
    review_file = review_dir / f"{target_date}.json"
    if review_file.exists():
        try:
            result["steward_review"] = json.loads(review_file.read_text(encoding="utf-8"))
            result["has_data"] = True
        except Exception as e:
            print(f"  [加载] steward review 失败: {e}")
    
    # 2. 加载 R1 诊断
    diag_file = review_dir / f"diagnosis_{target_date}.json"
    if diag_file.exists():
        try:
            diag_data = json.loads(diag_file.read_text(encoding="utf-8"))
            result["diagnosis"] = diag_data.get("diagnosis", "")
            result["has_data"] = True
        except Exception as e:
            print(f"  [加载] R1 诊断失败: {e}")
    
    return result


def extract_fund_mentions(text: str) -> list:
    """从文本中提取基金提及（名称 + 涨跌幅）"""
    mentions = []
    
    # 模式1：基金名称(+涨跌幅%)
    pattern1 = re.compile(r'([\u4e00-\u9fa5a-zA-Z·（）()]{2,30})[（(]?\+?(-?\d+\.?\d*)%?[）)]?', re.IGNORECASE)
    
    # 模式2：涨跌幅数字
    pattern2 = re.compile(r'(?:涨幅|跌幅|涨跌|收益)[^，。；]*?(-?\d+\.?\d*)%', re.IGNORECASE)
    
    # 模式3：直接提取所有百分比数字
    pattern3 = re.compile(r'(?:^|\s)(-?\d+\.?\d*)%', re.MULTILINE)
    
    # 合并提取
    for m in pattern1.finditer(text):
        fund_name = m.group(1).strip()
        value = float(m.group(2)) if m.group(2) else None
        mentions.append({
            "fund_name": fund_name,
            "mentioned_change": value,
            "raw": m.group(0),
        })
    
    # 如果没有提取到，尝试提取所有百分比数字
    if not mentions:
        for m in pattern3.finditer(text):
            value = float(m.group(1))
            # 过滤掉明显不是涨跌幅的数字（如 2024, 100等）
            if -50 <= value <= 50:
                mentions.append({
                    "fund_name": None,
                    "mentioned_change": value,
                    "raw": m.group(0),
                })
    
    return mentions


def extract_sector_mentions(text: str) -> list:
    """从文本中提取板块提及（板块名称 + 表现描述）"""
    mentions = []
    
    # 常见板块关键词
    sectors = ["科技", "消费", "医药", "金融", "地产", "新能源", "半导体", 
                "白酒", "互联网", "AI", "人工智能", "芯片", "光伏", "锂电",
                "军工", "农业", "煤炭", "钢铁", "化工", "有色金属"]
    
    for sector in sectors:
        if sector in text:
            # 尝试提取涨跌幅
            pattern = re.compile(rf'{sector}[^，。；]*?(-?\d+\.?\d*)%', re.IGNORECASE)
            match = pattern.search(text)
            value = float(match.group(1)) if match else None
            mentions.append({
                "sector": sector,
                "mentioned_change": value,
                "raw": text[text.find(sector):text.find(sector)+50] if text.find(sector) >= 0 else sector,
            })
    
    return mentions


def fetch_real_fund_data(user_id: str) -> dict:
    """获取用户持仓基金的实际数据"""
    try:
        from services.fund_monitor import load_fund_holdings
        from infra.data_source.fund_realtime import get_fund_realtime
        
        holdings = load_fund_holdings(user_id)
        if not holdings:
            return {}
        
        real_data = {}
        for fund in holdings:
            code = fund.get("code", "")
            name = fund.get("name", code)
            if not code:
                continue
            
            try:
                realtime = get_fund_realtime(code)
                if realtime:
                    real_data[code] = {
                        "name": name,
                        "est_change": realtime.get("est_change_pct"),
                        "nav_change": realtime.get("nav_change_pct"),
                        "last_nav": realtime.get("last_nav"),
                    }
            except Exception as e:
                print(f"  [实际数据] {name}({code}) 获取失败: {e}")
                continue
        
        return real_data
    except Exception as e:
        print(f"  [实际数据] 获取失败: {e}")
        return {}


def cross_check_diagnosis(review_data: dict, real_fund_data: dict) -> list:
    """对比 AI 诊断中的数字 vs 实际持仓数据"""
    issues = []
    
    diagnosis_text = review_data.get("diagnosis", "")
    steward_review = review_data.get("steward_review", {})
    
    if not diagnosis_text and not steward_review:
        return issues
    
    # 1. 检查诊断文本中的基金提及
    all_text = diagnosis_text
    if steward_review.get("reasoning"):
        all_text += "\n" + steward_review.get("reasoning", "")
    
    fund_mentions = extract_fund_mentions(all_text)
    sector_mentions = extract_sector_mentions(all_text)
    
    print(f"  [检查] 提取到 {len(fund_mentions)} 处基金提及, {len(sector_mentions)} 处板块提及")
    
    # 2. 对比基金涨跌幅
    for mention in fund_mentions:
        mentioned_change = mention.get("mentioned_change")
        if mentioned_change is None:
            continue
        
        # 检查是否在合理范围
        if mentioned_change > MAX_FUND_CHANGE or mentioned_change < MIN_FUND_CHANGE:
            issues.append(
                f"❌ 不合理涨跌幅：{mention['raw']} "
                f"（应在 {MIN_FUND_CHANGE}~{MAX_FUND_CHANGE}% 之间）"
            )
            continue
        
        # 尝试匹配实际基金数据
        fund_name = mention.get("fund_name")
        if fund_name and real_fund_data:
            # 模糊匹配基金名称
            matched = False
            for code, real in real_fund_data.items():
                if fund_name in real["name"] or real["name"] in fund_name:
                    matched = True
                    real_change = real.get("est_change") or real.get("nav_change")
                    if real_change is not None:
                        diff = abs(mentioned_change - real_change)
                        if diff > 2.0:  # 允许 2% 误差（估算 vs 实际）
                            issues.append(
                                f"⚠️ 涨跌幅不匹配：AI 说 {fund_name} {mentioned_change:+.2f}%，"
                                f"实际 {real['name']} {real_change:+.2f}%（差 {diff:.2f}%）"
                            )
                    break
            
            if not matched and len(real_fund_data) > 0:
                # 可能是幻觉（提到了不存在的基金）
                issues.append(
                    f"⚠️ 无法匹配基金：AI 提到「{fund_name}」，"
                    f"但持仓中未找到（可能是幻觉）"
                )
    
    # 3. 检查板块描述
    for mention in sector_mentions:
        sector = mention.get("sector")
        mentioned_change = mention.get("mentioned_change")
        
        if mentioned_change is None:
            continue
        
        # 检查是否在合理范围
        if mentioned_change > MAX_SECTOR_CHANGE or mentioned_change < MIN_SECTOR_CHANGE:
            issues.append(
                f"❌ 不合理板块涨跌幅：{sector} {mentioned_change:+.2f}% "
                f"（应在 {MIN_SECTOR_CHANGE}~{MAX_SECTOR_CHANGE}% 之间）"
            )
    
    # 4. 检查 steward review 中的关键字段
    if steward_review:
        direction = steward_review.get("direction", "")
        conclusion = steward_review.get("conclusion", "")
        
        # 检查 direction 是否合法
        valid_directions = {"bullish", "bearish", "neutral"}
        if direction and direction not in valid_directions:
            issues.append(f"⚠️ 未知 direction 值：{direction}（可能不是幻觉，但需要检查）")
        
        # 检查 conclusion 中是否有具体数字
        nums = re.findall(r'(-?\d+\.?\d*)%', conclusion)
        for num_str in nums:
            num = float(num_str)
            if num > 20 or num < -20:
                issues.append(
                    f"⚠️ conclusion 中可能的不合理数字：{num_str}% "
                    f"（如果是涨跌幅，应在 -20~20% 之间）"
                )
    
    return issues


def check_truncation(review_data: dict) -> list:
    """检查复盘内容是否截断"""
    issues = []
    
    diagnosis_text = review_data.get("diagnosis", "")
    
    if not diagnosis_text:
        return issues
    
    # 检查是否以不完整的方式结束
    truncation_signals = [
        diagnosis_text.rstrip().endswith("（"),
        diagnosis_text.rstrip().endswith("("),
        diagnosis_text.rstrip().endswith("+"),
        diagnosis_text.rstrip().endswith("-"),
        len(diagnosis_text) > 0 and diagnosis_text[-1] not in ["。", "！", "？", "…", "\n"],
        "..." in diagnosis_text[-50:],  # 末尾有省略号
    ]
    
    if any(truncation_signals):
        issues.append(
            f"⚠️ 诊断内容可能截断：末尾={diagnosis_text[-50:]!r}"
        )
    
    # 检查是否有不完整的括号
    open_parens = diagnosis_text.count("（") + diagnosis_text.count("(")
    close_parens = diagnosis_text.count("）") + diagnosis_text.count(")")
    if open_parens > close_parens:
        issues.append(
            f"⚠️ 诊断内容可能截断：括号不匹配（开 {open_parens} vs 闭 {close_parens}）"
        )
    
    return issues


def send_wecom_alert(issues: list, user_id: str):
    """检测到幻觉时发企微告警"""
    try:
        from services.wxwork_push import is_configured, send_text
        
        if not is_configured():
            print("  [告警] 企微未配置，跳过推送")
            return
        
        ts = datetime.now().strftime("%m-%d %H:%M")
        msg = f"⚠️ 钱袋子收盘复盘幻觉自检告警 [{ts}]\n\n"
        msg += f"用户：{user_id}\n"
        msg += f"检测到 {len(issues)} 项问题：\n"
        for iss in issues[:5]:  # 最多显示 5 条
            msg += f"• {iss}\n"
        if len(issues) > 5:
            msg += f"...以及 {len(issues)-5} 项更多问题\n"
        msg += f"\n请检查 stock_monitor_cron.py 日志"
        
        result = send_text(msg)
        print(f"  [告警] 企微推送结果: {result}")
    except Exception as e:
        print(f"  [告警] 企微推送失败: {e}")


def main():
    ap = argparse.ArgumentParser(description="收盘复盘幻觉自检")
    ap.add_argument("--user", default="leijiang", help="用户 ID")
    ap.add_argument("--date", default=None, help="检查指定日期（默认今天）")
    ap.add_argument("--alert", action="store_true", help="有幻觉时发企微告警")
    ap.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = ap.parse_args()
    
    print(f"[幻觉检查] 收盘复盘自检 — user={args.user} date={args.date or '今天'}")
    print("=" * 60)
    
    # 1. 加载复盘数据
    print("\n[加载] 读取收盘复盘数据...")
    review_data = load_review_data(args.user, args.date)
    
    if not review_data["has_data"]:
        print(f"❌ 未找到 {args.user} 在 {review_data['date']} 的复盘数据")
        print(f"  请确认 stock_monitor_cron.py 已在 15:30 运行")
        sys.exit(1)
    
    print(f"  ✅ steward review: {'有' if review_data['steward_review'] else '无'}")
    print(f"  ✅ R1 诊断: {'有' if review_data['diagnosis'] else '无'} ({len(review_data['diagnosis'])} 字)")
    
    if args.verbose:
        if review_data["diagnosis"]:
            print(f"\n[诊断内容]\n{review_data['diagnosis'][:500]}...")
    
    # 2. 获取实际持仓数据
    print("\n[实际数据] 获取持仓基金实时数据...")
    real_fund_data = fetch_real_fund_data(args.user)
    print(f"  ✅ 获取到 {len(real_fund_data)} 只基金的实际数据")
    
    if args.verbose and real_fund_data:
        for code, data in real_fund_data.items():
            print(f"    {data['name']}({code}): {data.get('est_change', 'N/A'):+.2f}%")
    
    # 3. 交叉验证
    print("\n" + "=" * 60)
    print("[幻觉检测]")
    
    all_issues = []
    
    # 3.1 检查数字准确性
    print("\n  [检查1] 数字准确性（AI诊断 vs 实际数据）...")
    number_issues = cross_check_diagnosis(review_data, real_fund_data)
    all_issues.extend(number_issues)
    if number_issues:
        print(f"    ⚠️ 检测到 {len(number_issues)} 处数字问题")
        for iss in number_issues:
            print(f"      {iss}")
    else:
        print(f"    ✅ 数字准确性检查通过")
    
    # 3.2 检查截断
    print("\n  [检查2] 内容完整性（是否截断）...")
    truncation_issues = check_truncation(review_data)
    all_issues.extend(truncation_issues)
    if truncation_issues:
        print(f"    ⚠️ 检测到 {len(truncation_issues)} 处截断问题")
        for iss in truncation_issues:
            print(f"      {iss}")
    else:
        print(f"    ✅ 内容完整性检查通过")
    
    # 4. 汇总结果
    print("\n" + "=" * 60)
    
    if all_issues:
        print(f"\n❌ 检测到 {len(all_issues)} 项问题：")
        for iss in all_issues:
            print(f"  • {iss}")
        
        # 发企微告警
        if args.alert:
            print("\n[告警] 正在发送企微告警...")
            send_wecom_alert(all_issues, args.user)
        
        sys.exit(1)
    else:
        print("\n✅ 所有检查通过，未发现幻觉")
        sys.exit(0)


if __name__ == "__main__":
    main()
