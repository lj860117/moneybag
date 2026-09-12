#!/usr/bin/env python3
"""
每日推送质量评估脚本
- 检查今日所有推送内容（存档在 /opt/moneybag/data/logs/pushes/）
- 评估：截断、幻觉、数据源、AI分析质量、推送格式
- 有问题发企微告警
"""
import os
import sys
import json
import re
import datetime
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PUSH_ARCHIVE_DIR
from services.persistence import atomic_write_json  # 铁律：JSON 落盘禁止裸 open().write()
from services.wxwork_push import (
    send_markdown,
    byte_len,
    WECOM_MARKDOWN_LIMIT,
    MARKDOWN_CHUNK_BUDGET,
    LENGTH_ALERT_BYTES,
    PUSH_ENVELOPE_OVERHEAD_BYTES,
)


def check_truncation(content: str) -> list:
    """
    检查推送内容是否截断
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    # 检查1：末尾是否不完整（以 "..." 结尾）
    if content.rstrip().endswith("..."):
        issues.append("⚠️ 内容可能截断：末尾有 '...'")
    
    # 检查2：括号是否匹配
    open_parens = content.count("（") + content.count("(")
    close_parens = content.count("）") + content.count(")")
    if open_parens != close_parens:
        issues.append(f"⚠️ 括号不匹配：开放 {open_parens}，闭合 {close_parens}")
    
    # 检查3：引号是否匹配
    quotes = content.count("\"") + content.count("'")
    if quotes % 2 != 0:
        issues.append("⚠️ 引号不匹配：奇数个引号")
    
    # 检查4：是否以不完整的中文字符结尾（如 "+0."）
    if re.search(r'[0-9]\.$', content.rstrip()):
        issues.append("⚠️ 内容可能截断：末尾有不完整数字（如 '+0.'）")
    
    return issues


def check_hallucination(push_file: str, actual_data: dict) -> list:
    """
    检查推送内容是否有幻觉（AI生成的数字 vs 实际数据）
    
    Args:
        push_file: 推送存档文件路径
        actual_data: 实际数据（从API获取）
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：基金涨跌幅是否准确
    fund_mentions = re.findall(r'([^\n\s]+)\((\d{6})\)[^\n]*?([+-]?\d+\.\d+)%', content)
    for fund_name, fund_code, mentioned_pct in fund_mentions:
        actual_pct = actual_data.get("funds", {}).get(fund_code, {}).get("change_pct")
        if actual_pct is not None:
            diff = abs(float(mentioned_pct) - actual_pct)
            if diff > 0.5:  # 误差超过 0.5%
                issues.append(
                    f"⚠️ 涨跌幅不匹配：AI 说 {fund_name} {mentioned_pct}%，"
                    f"实际 {actual_pct:.2f}%（差 {diff:.2f}%）"
                )
    
    # 检查2：板块描述是否准确
    sector_mentions = re.findall(r'(科技|消费|医药|金融|地产|新能源)板块[^\n]*?([+-]?\d+\.\d+)%', content)
    for sector_name, mentioned_pct in sector_mentions:
        actual_pct = actual_data.get("sectors", {}).get(sector_name, {}).get("change_pct")
        if actual_pct is not None:
            diff = abs(float(mentioned_pct) - actual_pct)
            if diff > 1.0:  # 误差超过 1%
                issues.append(
                    f"⚠️ 板块涨跌幅不匹配：AI 说 {sector_name} 板块 {mentioned_pct}%，"
                    f"实际 {actual_pct:.2f}%（差 {diff:.2f}%）"
                )
    
    return issues


def check_data_source(push_file: str) -> list:
    """
    检查数据源是否准确
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：QDII基金是否标注 T+1 延迟
    qdii_mentions = re.findall(r'([^\n\s]+)\(QDII\)', content)
    if qdii_mentions:
        if "T+1" not in content and "延迟" not in content:
            issues.append("⚠️ QDII 基金未标注 T+1 延迟")
    
    # 检查2：估值数据是否最新（不是昨天的）
    if "估算" in content or "估值" in content:
        # 检查是否有时间戳
        if "估值时间" not in content and "数据时间" not in content:
            issues.append("⚠️ 估值数据未标注时间")
    
    return issues


def check_ai_quality(push_file: str) -> list:
    """
    检查 AI 分析质量
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：是否过于模板化（每次都说"科技板块强势"）
    template_phrases = ["科技板块强势", "市场情绪较好", "建议关注"]
    phrase_count = sum(1 for phrase in template_phrases if phrase in content)
    if phrase_count >= 2:
        issues.append(f"⚠️ AI 分析可能模板化：检测到 {phrase_count} 处模板用语")
    
    # 检查2：是否给出具体建议
    if "建议" in content or "推荐" in content:
        # 检查建议是否具体（包含具体基金代码/名称）
        if not re.search(r'\d{6}|[^\n\s]+\([^\n\s]+\)', content):
            issues.append("⚠️ AI 建议不够具体（缺少具体基金/股票）")
    
    # 检查3：盈亏锚点是否准确
    if "浮盈" in content or "浮亏" in content:
        # 检查是否有具体数字
        if not re.search(r'[+-]?\d+\.\d+%', content):
            issues.append("⚠️ 盈亏锚点缺少具体数字")
    
    return issues


def check_push_format(push_file: str) -> list:
    """
    检查推送格式是否正确
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：基金名称是否显示（不是空的 "🔴 ()"）
    if re.search(r'🔴\s*\(\s*\)', content):
        issues.append("❌ 基金名称显示为空（'🔴 ()'）")
    
    if re.search(r'🟡\s*\(\s*\)', content):
        issues.append("❌ 基金名称显示为空（'🟡 ()'）")
    
    # 检查2：消息是否太长
    # v9.9.20 (B2)：原来写的是 `len(content) > 2048`，两个错误叠在一起：
    #   ① 单位错 —— 用「字符数」比「字节上限」。中文 3 字节/字，实测晨报 2.38~2.43
    #      字节/字符，字符判断会把真实体积系统性低估约 2.4 倍；
    #   ② 漏算信封 —— 档案里只有 archive_push 存的 body，不含 send_daily_report_to
    #      拼上的 title + "\n\n" + "\n\n⏰ 时间戳"（实测 52 字节）。
    #   两者叠加的后果：2026-09-11 BuLuoGeLi 晨报 body=2035B「通过检查」，
    #   实际发送 2087B > 2048B 被截断，监控却每晚 22:00 稳定全绿 —— bug 藏了很久。
    body_bytes = byte_len(content)
    sent_bytes = body_bytes + PUSH_ENVELOPE_OVERHEAD_BYTES
    if sent_bytes > WECOM_MARKDOWN_LIMIT:
        issues.append(
            f"❌ 消息超长：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> 企微 markdown 通道上限 "
            f"{WECOM_MARKDOWN_LIMIT} 字节 —— send_markdown 会按字节无损分段，"
            f"若真被截断说明有调用方绕过了分段逻辑，必须排查"
        )
    elif sent_bytes > MARKDOWN_CHUNK_BUDGET:
        issues.append(
            f"⚠️ 消息会分段：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> 分段预算 {MARKDOWN_CHUNK_BUDGET} 字节，"
            f"将拆成多条推送（内容无损，但阅读体验受损）"
        )
    elif sent_bytes > LENGTH_ALERT_BYTES:
        issues.append(
            f"⚠️ 消息接近告警线：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> {LENGTH_ALERT_BYTES} 字节，"
            f"距通道上限 {WECOM_MARKDOWN_LIMIT} 仅剩 "
            f"{WECOM_MARKDOWN_LIMIT - sent_bytes} 字节"
        )
    
    # 检查3：分段是否合理
    if content.count("\n\n") > 10:
        issues.append(f"⚠️ 分段可能不合理：{content.count(chr(10)+chr(10))} 处空行")
    
    return issues


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_date_arg(date_arg) -> str:
    """
    把日期参数解析成 YYYY-MM-DD。

    v9.9.24 (P0-1)：cron 一直传的是字面量 `--date today`（见 setup_cron.sh / 
    docs/ops/crontab.production.txt），而旧代码 `date_str = args.date` 原样透传，
    glob 变成 `today_*_LeiJiang.txt` → 永远匹配不到任何存档 → 走进
    "空结果 = 100 分 = 通过" 分支，这个检查从上线起就没真正跑过一次。

    支持：None / "" / "today" / "yesterday" / "YYYY-MM-DD"。
    其它格式直接抛 ValueError（由 main 转成退出码 2，不静默降级）。
    """
    raw = (date_arg or "").strip().lower()
    today = datetime.date.today()
    if raw in ("", "today", "now"):
        return today.strftime("%Y-%m-%d")
    if raw == "yesterday":
        return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if _DATE_RE.match(raw):
        # 校验是真日期（拦住 2026-13-45 这种）
        datetime.datetime.strptime(raw, "%Y-%m-%d")
        return raw
    raise ValueError(
        f"无法解析的日期参数 {date_arg!r}：只支持 today / yesterday / YYYY-MM-DD"
    )


def evaluate_push_quality(date_str: str, user_id: str = "LeiJiang") -> dict:
    """
    评估指定日期的推送质量
    
    Args:
        date_str: 日期字符串（如 "2026-06-16"）
        user_id: 用户ID
    
    Returns:
        dict: 评估结果
    """
    results = {
        "date": date_str,
        "user_id": user_id,
        "pushes": [],
        "total_issues": 0,
        "score": 100,
        "status": "PASS",
        "issues": [],
        "checks_skipped": [],
    }
    
    # 查找今日的推送存档
    push_dir = Path(PUSH_ARCHIVE_DIR)
    push_files = sorted(push_dir.glob(f"{date_str}_*_{user_id}.txt"))
    
    if not push_files:
        # v9.9.24 (P0-1)：这里原来是 `results["error"] = ...; return`，
        # 而 score 仍保持初始值 100、total_issues 保持 0 → 调用方看到的是
        # "0 问题 / 100 分 / ✅ 通过"。**没有数据 ≠ 通过**，一次推送都没有的
        # 一天（推送挂了 / 存档路径不一致 / 日期没解析）必须判 FAIL。
        results["status"] = "FAIL"
        results["score"] = 0
        results["total_issues"] = 1
        results["archive_dir"] = str(push_dir)
        results["error"] = f"未找到 {date_str} 的推送存档"
        results["issues"].append(
            f"❌ 未找到 {date_str} 的推送存档（目录 {push_dir}，"
            f"匹配 {date_str}_*_{user_id}.txt）：无法验证推送质量，"
            f"可能是推送任务根本没执行 / 存档路径不一致 / 日期解析错误"
        )
        return results
    
    results["archive_dir"] = str(push_dir)
    
    # 评估每个推送
    for push_file in push_files:
        push_type = push_file.stem.split("_")[1]
        
        with open(push_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 运行所有检查
        issues = []
        issues.extend(check_truncation(content))
        
        # 获取实际数据（用于幻觉检查）
        # TODO(v9.9.24 P0-2)：actual_data 恒为空 → check_hallucination 里的
        # `actual_pct is not None` 永远不成立，幻觉检查同样是空跑。这里先如实
        # 记进 checks_skipped（不上报成 issue，避免 P0-2 落地前天天刷告警），
        # 由 P0-2 接真实数据源后消除。
        actual_data = {}
        if not actual_data:
            results["checks_skipped"].append("hallucination")
        issues.extend(check_hallucination(str(push_file), actual_data))
        
        issues.extend(check_data_source(str(push_file)))
        issues.extend(check_ai_quality(str(push_file)))
        issues.extend(check_push_format(str(push_file)))
        
        # 记录结果
        push_result = {
            "file": push_file.name,
            "type": push_type,
            "issues": issues,
            "issue_count": len(issues),
        }
        results["pushes"].append(push_result)
        results["total_issues"] += len(issues)
        results["score"] -= len(issues) * 5  # 每个问题扣 5 分
    
    results["score"] = max(0, results["score"])
    results["checks_skipped"] = sorted(set(results["checks_skipped"]))
    if results["total_issues"] > 0:
        results["status"] = "FAIL"
    
    return results


def send_alert_if_needed(results: dict):
    """
    如果有问题，发企微告警
    """
    # v9.9.24 (P0-1)：原来只判 `total_issues == 0`，而"找不到存档"时
    # total_issues 恒为 0 → 走 ✅ 分支。改为认 status，且 fatal（无存档）
    # 也要告警 —— 「没检查到」本身就是最该被看见的告警。
    if results.get("status") == "PASS" and results.get("total_issues", 0) == 0:
        print("✅ 所有推送质量检查通过")
        return
    
    # 生成告警消息
    alert_msg = f"📊 {results['date']} 推送质量评估\n\n"
    alert_msg += f"结论：{results.get('status', 'FAIL')}\n"
    alert_msg += f"总分：{results['score']}/100\n"
    alert_msg += f"检测到 {results['total_issues']} 处问题：\n\n"
    
    # 无存档 / 其它致命问题（不属于任何单个 push）
    for fatal in results.get("issues", []):
        alert_msg += f"{fatal}\n"
    if results.get("issues"):
        alert_msg += "\n"
    
    for push in results["pushes"]:
        if push["issue_count"] > 0:
            alert_msg += f"❌ {push['type']}（{push['file']}）\n"
            for issue in push["issues"]:
                alert_msg += f"  {issue}\n"
            alert_msg += "\n"
    
    alert_msg += "⚠️ 请及时修复\n"
    
    # 发送告警
    # v9.9.20 (B2)：修参数顺序写反的 bug —— 原来是 send_markdown("LeiJiang", alert_msg)，
    # 而签名是 send_markdown(content, user_id="")，等于把 "LeiJiang" 当正文、
    # 把整段告警文本当 userId 发出去。这个告警其实从来没正常工作过。
    try:
        # v9.9.24 (P0-1)：send_markdown 返回 {"ok": ...}，原来只看「有没有抛异常」，
        # 发送失败也会打 ✅ 告警已发送 —— 又一处"假成功"。
        ret = send_markdown(alert_msg, user_id="LeiJiang")
        if isinstance(ret, dict) and not ret.get("ok", True):
            print(f"❌ 告警发送失败：{ret.get('error') or ret}")
        else:
            print("✅ 告警已发送")
    except Exception as e:
        print(f"❌ 告警发送失败：{e}")


def main():
    """
    主函数
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="每日推送质量评估")
    parser.add_argument(
        "--date", type=str, default=None,
        help="评估日期：today / yesterday / YYYY-MM-DD（默认 today）",
    )
    parser.add_argument("--user", type=str, default="LeiJiang", help="用户ID")
    parser.add_argument("--alert", action="store_true", help="有问题发企微告警")
    parser.add_argument("--out", type=str, default=None, help="结果 JSON 落盘路径（原子写）")
    
    args = parser.parse_args()
    
    # 确定评估日期（v9.9.24 P0-1：cron 传的是字面量 "today"，必须 resolve）
    try:
        date_str = resolve_date_arg(args.date)
    except ValueError as e:
        parser.error(str(e))  # 退出码 2，不静默降级成"通过"
        return
    
    print(f"📊 开始评估 {date_str} 的推送质量...")
    
    # 评估推送质量
    results = evaluate_push_quality(date_str, args.user)
    
    # 打印结果
    print(json.dumps(results, ensure_ascii=False, indent=2))
    
    if results.get("checks_skipped"):
        print(
            f"⚠️ 以下检查被跳过（未取到真实数据，结果不完整）："
            f"{', '.join(results['checks_skipped'])}"
        )
    
    if args.out:
        atomic_write_json(Path(args.out), results)
        print(f"📝 结果已写入 {args.out}")
    
    # 有问题发告警
    if args.alert:
        send_alert_if_needed(results)
    
    # v9.9.24 (P0-1)：退出码必须能反映结论，否则 cron 永远看不到失败
    if results.get("status") == "FAIL" or results.get("total_issues", 0) > 0:
        print(
            f"❌ 推送质量检查未通过：{results['total_issues']} 处问题，"
            f"score={results['score']}/100，date={date_str}"
        )
        sys.exit(1)
    
    print(f"✅ 所有推送质量检查通过（score={results['score']}/100）")
    sys.exit(0)


if __name__ == "__main__":
    main()
