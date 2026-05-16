"""
钱袋子 — 月度关闭脚本 (Phase 3 Batch 4)
=====================================
定时任务：每月 1 日凌晨 1 点执行
功能：
1. 为所有用户保存月度资产快照
2. 清理过期数据

执行方式：
- 本地测试: python -m backend.scripts.monthly_close
- Cron 配置: 0 1 1 * * cd /path/to/project && python -m backend.scripts.monthly_close
"""

import sys
import json
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from config import DATA_DIR
from backend.services.monthly_snapshot import save_all_users_snapshots
from backend.services.behavior_recorder import clear_old_events
from backend.services.todo_manager import clear_old_todos


def run_monthly_close():
    """
    执行月度关闭任务。
    
    返回:
        dict: {success: bool, message: str, stats: dict}
    """
    print(f"[MONTHLY_CLOSE] 开始月度关闭任务 {datetime.now().isoformat()}")
    
    result = {
        "success": False,
        "message": "",
        "stats": {
            "snapshots_saved": 0,
            "events_cleaned": 0,
            "todos_cleaned": 0,
            "users_processed": 0,
            "errors": []
        }
    }
    
    try:
        # 步骤 1: 为所有用户保存月度快照
        print("[MONTHLY_CLOSE] Step 1: 保存月度快照...")
        try:
            snapshots_count = save_all_users_snapshots()
            result["stats"]["snapshots_saved"] = snapshots_count
            print(f"[MONTHLY_CLOSE] ✓ 为 {snapshots_count} 个用户保存了快照")
        except Exception as e:
            msg = f"保存快照失败: {str(e)}"
            print(f"[MONTHLY_CLOSE] ✗ {msg}")
            result["stats"]["errors"].append(msg)
        
        # 步骤 2: 清理过期数据（所有用户）
        print("[MONTHLY_CLOSE] Step 2: 清理过期数据...")
        try:
            users_dir = DATA_DIR / "users"
            if users_dir.exists():
                user_count = 0
                for user_file in users_dir.glob("*.json"):
                    try:
                        data = json.loads(user_file.read_text(encoding="utf-8"))
                        user_id = data.get("userId")
                        if user_id:
                            # 清理 90 天以前的行为事件（保留最近 90 天）
                            try:
                                clear_old_events(user_id, keep_days=90)
                            except Exception as e:
                                print(f"[MONTHLY_CLOSE] 清理事件失败 ({user_id}): {e}")
                            
                            # 清理 30 天以前的完成/跳过任务（保留最近 30 天）
                            try:
                                clear_old_todos(user_id, keep_days=30)
                            except Exception as e:
                                print(f"[MONTHLY_CLOSE] 清理任务失败 ({user_id}): {e}")
                            
                            user_count += 1
                    except Exception as e:
                        print(f"[MONTHLY_CLOSE] 处理用户文件失败 ({user_file}): {e}")
                        result["stats"]["errors"].append(str(e))
                        continue
                
                result["stats"]["users_processed"] = user_count
                print(f"[MONTHLY_CLOSE] ✓ 清理了 {user_count} 个用户的过期数据")
        except Exception as e:
            msg = f"清理过期数据失败: {str(e)}"
            print(f"[MONTHLY_CLOSE] ✗ {msg}")
            result["stats"]["errors"].append(msg)
        
        # 步骤 3: 记录完成
        result["success"] = len(result["stats"]["errors"]) == 0
        result["message"] = f"月度关闭完成" if result["success"] else "月度关闭完成，但存在错误"
        
        print(f"[MONTHLY_CLOSE] {result['message']}")
        print(f"[MONTHLY_CLOSE] 统计: 快照 {result['stats']['snapshots_saved']}个, 处理用户 {result['stats']['users_processed']}个")
        
        if result["stats"]["errors"]:
            print(f"[MONTHLY_CLOSE] ⚠️ 错误: {result['stats']['errors']}")
        
        return result
        
    except Exception as e:
        result["success"] = False
        result["message"] = f"月度关闭任务失败: {str(e)}"
        print(f"[MONTHLY_CLOSE] ✗ {result['message']}")
        return result


if __name__ == "__main__":
    # 执行月度关闭任务
    result = run_monthly_close()
    
    # 返回状态码（0=成功, 1=失败）
    sys.exit(0 if result["success"] else 1)
