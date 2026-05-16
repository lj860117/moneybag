"""
钱袋子 — Phase 3 数据迁移脚本 (Phase 3 Batch 4)
============================================
为所有现有用户补全 Phase 3 数据字段。

功能：
1. 为用户补全 behavior_events, todos, monthly_snapshots 字段
2. 保留现有数据完整性
3. 提供回滚能力

执行方式：
- 本地测试: python -m backend.scripts.migrate_phase3 --dry-run
- 实际迁移: python -m backend.scripts.migrate_phase3
- 回滚: python -m backend.scripts.migrate_phase3 --rollback
"""

import sys
import json
import shutil
from datetime import datetime
from pathlib import Path
import argparse

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from config import DATA_DIR
from backend.services.persistence import load_user, save_user


def backup_user_data():
    """
    备份所有用户数据到时间戳目录。
    
    返回:
        str: 备份目录路径
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = DATA_DIR / "backups" / f"phase3_migration_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    users_dir = DATA_DIR / "users"
    if users_dir.exists():
        for user_file in users_dir.glob("*.json"):
            shutil.copy2(user_file, backup_dir / user_file.name)
    
    print(f"[MIGRATE] ✓ 备份完成: {backup_dir}")
    return backup_dir


def migrate_user(user_id: str, dry_run: bool = False) -> dict:
    """
    为单个用户迁移 Phase 3 字段。
    
    Args:
        user_id: 用户 ID
        dry_run: 是否仅测试不实际修改
    
    返回:
        dict: {success: bool, added: list, skipped: bool}
    """
    result = {
        "user_id": user_id,
        "success": False,
        "added": [],
        "skipped": False,
        "error": None
    }
    
    try:
        user_data = load_user(user_id)
        
        # 检查是否已有 Phase 3 字段
        has_behavior = "behavior_events" in user_data
        has_todos = "todos" in user_data
        has_snapshots = "monthly_snapshots" in user_data
        
        if has_behavior and has_todos and has_snapshots:
            result["skipped"] = True
            return result
        
        # 补全缺失字段
        if not has_behavior:
            user_data["behavior_events"] = []
            result["added"].append("behavior_events")
        
        if not has_todos:
            user_data["todos"] = []
            result["added"].append("todos")
        
        if not has_snapshots:
            user_data["monthly_snapshots"] = {}
            result["added"].append("monthly_snapshots")
        
        # 保存（如果不是 dry-run）
        if not dry_run and result["added"]:
            save_user(user_data)
        
        result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
        print(f"[MIGRATE] ✗ 迁移失败 ({user_id}): {e}")
    
    return result


def run_migration(dry_run: bool = False):
    """
    执行迁移任务。
    
    Args:
        dry_run: 是否仅测试
    
    返回:
        dict: {success: bool, total: int, migrated: int, skipped: int, errors: list}
    """
    print(f"[MIGRATE] 开始 Phase 3 数据迁移 {datetime.now().isoformat()}")
    if dry_run:
        print("[MIGRATE] ⚠️ 运行在 DRY-RUN 模式，不会实际修改数据")
    
    result = {
        "success": False,
        "total": 0,
        "migrated": 0,
        "skipped": 0,
        "errors": []
    }
    
    try:
        # 步骤 1: 备份数据
        if not dry_run:
            backup_user_data()
        
        # 步骤 2: 遍历所有用户并迁移
        users_dir = DATA_DIR / "users"
        if not users_dir.exists():
            print("[MIGRATE] ⚠️ 未找到用户数据目录")
            result["success"] = True
            return result
        
        user_files = list(users_dir.glob("*.json"))
        print(f"[MIGRATE] 找到 {len(user_files)} 个用户文件")
        
        for idx, user_file in enumerate(user_files, 1):
            try:
                data = json.loads(user_file.read_text(encoding="utf-8"))
                user_id = data.get("userId", user_file.stem)
                
                migration_result = migrate_user(user_id, dry_run=dry_run)
                result["total"] += 1
                
                if migration_result["skipped"]:
                    result["skipped"] += 1
                    status = "跳过"
                elif migration_result["success"]:
                    result["migrated"] += 1
                    status = f"迁移 (添加: {', '.join(migration_result['added'])})"
                else:
                    result["errors"].append(migration_result["error"])
                    status = "失败"
                
                if idx % 10 == 0 or migration_result["error"]:
                    print(f"[MIGRATE] [{idx}/{len(user_files)}] {user_id}: {status}")
                    
            except Exception as e:
                result["total"] += 1
                result["errors"].append(str(e))
                print(f"[MIGRATE] ✗ 处理文件失败 ({user_file}): {e}")
                continue
        
        # 步骤 3: 总结
        result["success"] = len(result["errors"]) == 0
        
        print(f"\n[MIGRATE] ========== 迁移完成 ==========")
        print(f"[MIGRATE] 总计: {result['total']} 个用户")
        print(f"[MIGRATE] 迁移: {result['migrated']} 个用户")
        print(f"[MIGRATE] 跳过: {result['skipped']} 个用户")
        if result["errors"]:
            print(f"[MIGRATE] 错误: {len(result['errors'])} 个")
            for err in result["errors"][:5]:  # 只显示前 5 个
                print(f"  - {err}")
        
        if dry_run:
            print("\n[MIGRATE] ✓ DRY-RUN 完成，如无问题请移除 --dry-run 标志重新执行")
        else:
            print(f"\n[MIGRATE] ✓ 迁移成功完成")
        
        return result
        
    except Exception as e:
        result["success"] = False
        result["errors"].append(str(e))
        print(f"[MIGRATE] ✗ 迁移任务失败: {e}")
        return result


def rollback_migration(backup_dir: str):
    """
    回滚迁移。
    
    Args:
        backup_dir: 备份目录路径
    """
    backup_path = Path(backup_dir)
    if not backup_path.exists():
        print(f"[MIGRATE] ✗ 备份目录不存在: {backup_path}")
        return False
    
    try:
        print(f"[MIGRATE] 开始回滚到备份: {backup_path}")
        users_dir = DATA_DIR / "users"
        
        # 恢复所有备份文件
        for backup_file in backup_path.glob("*.json"):
            restore_path = users_dir / backup_file.name
            shutil.copy2(backup_file, restore_path)
        
        print(f"[MIGRATE] ✓ 回滚完成")
        return True
        
    except Exception as e:
        print(f"[MIGRATE] ✗ 回滚失败: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3 数据迁移脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅测试，不实际修改")
    parser.add_argument("--rollback", type=str, help="回滚到指定备份目录")
    args = parser.parse_args()
    
    if args.rollback:
        # 回滚模式
        success = rollback_migration(args.rollback)
        sys.exit(0 if success else 1)
    else:
        # 迁移模式
        result = run_migration(dry_run=args.dry_run)
        sys.exit(0 if result["success"] else 1)
