#!/usr/bin/env python3
"""
晨报缓存清理脚本
==================
用途：清理过期的晨报缓存文件（日期早于当前日期的缓存）

使用：
  python3 cleanup_morning_report_cache.py
  
或指定 DATA_DIR：
  DATA_DIR=/path/to/data python3 cleanup_morning_report_cache.py
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

def main():
    # 获取 DATA_DIR
    data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
    brief_dir = data_dir / "briefings"
    
    if not brief_dir.exists():
        print(f"✅ 缓存目录不存在，无需清理: {brief_dir}")
        return 0
    
    print(f"🔍 扫描缓存目录: {brief_dir}")
    print(f"📅 当前日期: {datetime.now().strftime('%Y-%m-%d')}")
    print("-" * 60)
    
    today_str = datetime.now().strftime("%Y%m%d")
    cutoff_str = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    
    cache_files = list(brief_dir.glob("*.json"))
    if not cache_files:
        print("✅ 未找到缓存文件")
        return 0
    
    # 分类缓存文件
    expired_files = []
    future_files = []
    valid_files = []
    invalid_files = []
    
    for fp in sorted(cache_files):
        filename = fp.stem  # e.g., "LeiJiang_20250714"
        
        # 尝试提取日期
        parts = filename.split('_')
        if len(parts) < 2:
            invalid_files.append(fp)
            continue
        
        date_str = parts[-1]
        if len(date_str) != 8 or not date_str.isdigit():
            invalid_files.append(fp)
            continue
        
        # 分类
        if date_str > today_str:
            future_files.append((fp, date_str))
        elif date_str < cutoff_str:
            expired_files.append((fp, date_str))
        else:
            valid_files.append((fp, date_str))
    
    # 显示统计
    print(f"📊 统计:")
    print(f"  有效缓存（最近7天）: {len(valid_files)}")
    print(f"  过期缓存（>7天前）: {len(expired_files)}")
    print(f"  未来缓存（未来日期）: {len(future_files)}")
    print(f"  无效缓存（格式错误）: {len(invalid_files)}")
    print("-" * 60)
    
    # 显示详情
    if valid_files:
        print("✅ 有效缓存:")
        for fp, date_str in sorted(valid_files, key=lambda x: x[1], reverse=True):
            size_kb = fp.stat().st_size / 1024
            print(f"  {fp.name:40s} ({date_str}) {size_kb:6.1f} KB")
    
    if expired_files:
        print("\n⏰ 过期缓存（待删除）:")
        for fp, date_str in sorted(expired_files, key=lambda x: x[1], reverse=True):
            size_kb = fp.stat().st_size / 1024
            print(f"  {fp.name:40s} ({date_str}) {size_kb:6.1f} KB")
    
    if future_files:
        print("\n⚠️  未来缓存（待删除）:")
        for fp, date_str in sorted(future_files, key=lambda x: x[1]):
            size_kb = fp.stat().st_size / 1024
            print(f"  {fp.name:40s} ({date_str}) {size_kb:6.1f} KB")
    
    if invalid_files:
        print("\n❓ 无效缓存（待删除）:")
        for fp in sorted(invalid_files):
            size_kb = fp.stat().st_size / 1024
            print(f"  {fp.name:40s} {size_kb:6.1f} KB")
    
    print("-" * 60)
    
    # 询问是否删除
    to_delete = expired_files + future_files + invalid_files
    if not to_delete:
        print("✅ 无需清理")
        return 0
    
    total_size = sum(fp.stat().st_size for fp, _ in to_delete if isinstance(fp, Path)) / 1024
    print(f"\n🗑️  待删除: {len(to_delete)} 个文件，共 {total_size:.1f} KB")
    
    response = input("确认删除? (yes/no): ").strip().lower()
    if response not in ('yes', 'y'):
        print("❌ 已取消")
        return 1
    
    # 执行删除
    deleted_count = 0
    for fp, date_str in expired_files + future_files + invalid_files:
        try:
            if isinstance(fp, tuple):
                fp = fp[0]
            fp.unlink()
            print(f"🗑️  已删除: {fp.name}")
            deleted_count += 1
        except Exception as e:
            print(f"❌ 删除失败 {fp.name}: {e}")
    
    print("-" * 60)
    print(f"✅ 完成: 删除了 {deleted_count} 个过期缓存文件")
    return 0

if __name__ == "__main__":
    sys.exit(main())
