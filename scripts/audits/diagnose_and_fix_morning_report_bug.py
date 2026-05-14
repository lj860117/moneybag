#!/usr/bin/env python3
"""
晨报日期Bug诊断和修复脚本
Diagnosis and Fix Script for Morning Report Date Bug

功能:
1. 检查系统时间和时区
2. 扫描和清理过期缓存
3. 验证代码修复
4. 生成修复报告

使用方法:
  python diagnose_and_fix_morning_report_bug.py [--fix] [--clean-cache]
"""

import os
import sys
import json
import tempfile
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

# 颜色输出
class Color:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_section(title: str):
    """打印分节标题"""
    print(f"\n{Color.BLUE}{'='*60}{Color.END}")
    print(f"{Color.BLUE}{title}{Color.END}")
    print(f"{Color.BLUE}{'='*60}{Color.END}\n")

def print_ok(msg: str):
    """打印成功信息"""
    print(f"{Color.GREEN}✅ {msg}{Color.END}")

def print_warn(msg: str):
    """打印警告信息"""
    print(f"{Color.YELLOW}⚠️  {msg}{Color.END}")

def print_error(msg: str):
    """打印错误信息"""
    print(f"{Color.RED}❌ {msg}{Color.END}")

def check_system_time() -> bool:
    """检查系统时间是否正确"""
    print_section("1. 系统时间检查")
    
    today = date.today()
    now = datetime.now()
    year = today.year
    
    print(f"系统日期: {today} (ISO: {today.isoformat()})")
    print(f"系统时间: {now}")
    print(f"时区: {os.environ.get('TZ', '未设置，使用系统默认')}")
    
    # 检查年份合理性
    if year < 2020 or year > 2050:
        print_error(f"系统日期异常: 年份 {year} 不在合理范围内 (2020-2050)")
        return False
    
    print_ok("系统日期在合理范围内")
    return True

def scan_briefing_cache() -> Tuple[List[Path], int]:
    """扫描晨报缓存目录"""
    print_section("2. 晨报缓存扫描")
    
    backend_dir = Path(__file__).parent / "backend"
    cache_dir = backend_dir / "data" / "briefings"
    
    print(f"缓存目录: {cache_dir}")
    
    if not cache_dir.exists():
        print_warn("缓存目录不存在 (这是正常的，表示尚未生成晨报)")
        return [], 0
    
    cache_files = list(cache_dir.glob("*.json"))
    print_ok(f"找到 {len(cache_files)} 个缓存文件")
    
    today = date.today()
    today_yyyymmdd = today.strftime("%Y%m%d")
    
    old_files = []
    today_files = []
    future_files = []
    
    for fp in cache_files:
        # 提取日期部分: {user_id}_{YYYYMMDD}.json
        parts = fp.stem.split("_")
        if len(parts) >= 2:
            date_part = parts[-1]
            
            if date_part.isdigit() and len(date_part) == 8:
                try:
                    cache_date = datetime.strptime(date_part, "%Y%m%d").date()
                    
                    if cache_date == today:
                        today_files.append(fp)
                    elif cache_date > today:
                        future_files.append((fp, cache_date))
                    elif (today - cache_date).days > 7:
                        old_files.append((fp, cache_date))
                    else:
                        print_ok(f"有效缓存: {fp.name} ({cache_date})")
                        
                except ValueError:
                    pass
    
    # 打印统计
    print_warn(f"今天的缓存: {len(today_files)} 个")
    for fp in today_files:
        print(f"  ✓ {fp.name}")
    
    if future_files:
        print_error(f"未来日期的缓存: {len(future_files)} 个 (BUG信号!)")
        for fp, cache_date in future_files:
            print(f"  ✗ {fp.name} ({cache_date})")
    
    if old_files:
        print_warn(f"过期缓存 (>7天): {len(old_files)} 个")
        for fp, cache_date in old_files:
            print(f"  ✗ {fp.name} ({cache_date})")
    
    return old_files + future_files, len(cache_files)

def scan_night_worker_logs() -> Dict[str, List[Path]]:
    """扫描night_worker日志和产物"""
    print_section("3. Night Worker 日志扫描")
    
    backend_dir = Path(__file__).parent / "backend"
    night_worker_dir = backend_dir / "data" / "night_worker"
    
    print(f"Night Worker 目录: {night_worker_dir}")
    
    if not night_worker_dir.exists():
        print_warn("Night Worker 目录不存在 (可能尚未运行)")
        return {}
    
    logs = list(night_worker_dir.glob("*.log"))
    products = list(night_worker_dir.glob("products_*.json"))
    briefings = list(night_worker_dir.glob("briefings_*.json"))
    
    print_ok(f"日志文件: {len(logs)} 个")
    print_ok(f"产物文件: {len(products)} 个")
    print_ok(f"简报文件: {len(briefings)} 个")
    
    # 检查最新的日志
    if logs:
        latest_log = sorted(logs)[-1]
        print_ok(f"最新日志: {latest_log.name}")
        
        # 读取最后几行
        with open(latest_log, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if lines:
                print("  最后3行:")
                for line in lines[-3:]:
                    print(f"    {line.rstrip()}")
    
    return {
        "logs": logs,
        "products": products,
        "briefings": briefings,
    }

def check_code_issues() -> List[str]:
    """检查代码中是否存在问题"""
    print_section("4. 代码问题检查")
    
    backend_dir = Path(__file__).parent / "backend"
    steward_file = backend_dir / "services" / "steward.py"
    
    if not steward_file.exists():
        print_error(f"找不到 {steward_file}")
        return []
    
    issues = []
    content = steward_file.read_text(encoding='utf-8')
    
    # 检查是否缺少日期验证
    if "_check_date_consistency" not in content:
        issues.append("缺少 _check_date_consistency() 函数")
        print_warn("缺少日期一致性检查函数")
    else:
        print_ok("已包含日期一致性检查函数")
    
    if "cache_date > today" not in content:
        issues.append("briefing_history() 缺少未来日期检查")
        print_warn("缺少未来日期验证")
    else:
        print_ok("已包含未来日期验证")
    
    if "cache_date < cutoff_date" not in content:
        issues.append("briefing_history() 缺少过期缓存检查")
        print_warn("缺少过期缓存验证")
    else:
        print_ok("已包含过期缓存验证")
    
    return issues

def clean_cache(dry_run: bool = True) -> Tuple[int, int]:
    """清理过期缓存"""
    print_section("5. 缓存清理")
    
    backend_dir = Path(__file__).parent / "backend"
    cache_dir = backend_dir / "data" / "briefings"
    
    if not cache_dir.exists():
        print("缓存目录不存在，无需清理")
        return 0, 0
    
    today = date.today()
    cutoff_date = today - timedelta(days=7)
    
    deleted_count = 0
    preserved_count = 0
    
    for fp in cache_dir.glob("*.json"):
        parts = fp.stem.split("_")
        if len(parts) >= 2:
            date_part = parts[-1]
            
            if date_part.isdigit() and len(date_part) == 8:
                try:
                    cache_date = datetime.strptime(date_part, "%Y%m%d").date()
                    
                    # 删除条件: 未来日期或超过7天
                    should_delete = cache_date > today or cache_date < cutoff_date
                    
                    if should_delete:
                        if dry_run:
                            print(f"[DRY RUN] 将删除: {fp.name}")
                        else:
                            fp.unlink()
                            print(f"✓ 已删除: {fp.name}")
                        deleted_count += 1
                    else:
                        preserved_count += 1
                        
                except ValueError:
                    pass
    
    if dry_run:
        print_warn(f"(DRY RUN 模式) 将删除 {deleted_count} 个文件，保留 {preserved_count} 个")
    else:
        print_ok(f"已删除 {deleted_count} 个文件，保留 {preserved_count} 个")
    
    return deleted_count, preserved_count

def generate_report(issues: List[str], cache_files: List[Path]):
    """生成修复报告"""
    print_section("6. 修复报告")
    
    report = {
        "timestamp": datetime.now().isoformat(),
        "system_date": date.today().isoformat(),
        "total_cache_files": len(cache_files),
        "code_issues": issues,
        "status": "HEALTHY" if not issues else "NEEDS_FIX",
    }
    
    if issues:
        print_error("检测到以下问题:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    else:
        print_ok("未检测到代码问题")
    
    # 保存报告
    report_file = Path(__file__).parent / "MORNING_REPORT_DIAGNOSTIC_REPORT.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print_ok(f"诊断报告已保存: {report_file}")
    
    return report

def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="晨报日期Bug诊断和修复")
    parser.add_argument("--fix", action="store_true", help="应用修复")
    parser.add_argument("--clean-cache", action="store_true", help="清理过期缓存")
    
    args = parser.parse_args()
    
    print(f"{Color.BLUE}")
    print("╔════════════════════════════════════════════════════════════╗")
    print("║         晨报日期Bug 诊断和修复工具 v1.0                    ║")
    print("║  Diagnosis and Fix Tool for Morning Report Date Bug        ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(f"{Color.END}")
    
    # 执行诊断
    time_ok = check_system_time()
    cache_files, total_cache = scan_briefing_cache()
    night_worker = scan_night_worker_logs()
    code_issues = check_code_issues()
    
    # 生成报告
    report = generate_report(code_issues, cache_files)
    
    # 清理缓存
    if args.clean_cache:
        print()
        deleted, preserved = clean_cache(dry_run=not args.fix)
    
    # 最终建议
    print_section("7. 后续建议")
    
    if not time_ok:
        print_error("❌ 系统时间异常，请先检查和修正系统时间和时区")
    
    if code_issues and args.fix:
        print_warn("请应用代码修复补丁: git apply FIX_steward_date_validation.patch")
        print("然后重启服务: systemctl restart moneybag-backend")
    
    if cache_files and not args.clean_cache:
        print_warn("建议清理过期缓存: python diagnose_and_fix_morning_report_bug.py --clean-cache --fix")
    
    print_ok("诊断完成！")
    
    return 0 if not code_issues else 1

if __name__ == "__main__":
    sys.exit(main())
