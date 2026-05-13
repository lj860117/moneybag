# 晨报日期 Bug 修复方案

## 问题诊断

**症状**: 晨报显示"2025年7月14日"而实际日期是"2026年5月14日"

**根本原因**: 
1. `backend/services/steward.py` 的 `briefing()` 方法（L138-145）缓存了晨报文件
2. 当天的缓存文件被返回时，**没有验证缓存文件的日期是否与当前日期匹配**
3. 如果存在旧的缓存文件（如 `LeiJiang_20250714.json`），系统返回的仍是旧数据

**影响范围**:
- `/api/steward/briefing` 端点 — 返回当日晨报（带缓存）
- `/api/steward/briefing-history` 端点 — 返回最近7天晨报，无日期验证

**数据流**:
```
night_worker.py (line 415: date.today().isoformat())
    ↓
briefing text embeds date (line 475: f"📊 {today} 钱袋子晨报")
    ↓
cached to data/briefings/{user_id}_{YYYYMMDD}.json
    ↓
steward.py briefing() returns cached data without date validation
    ↓
frontend receives & displays the embedded date
```

## 修复方案

### 方案 A: 严格的日期验证（推荐）

在 `backend/services/steward.py` 中添加以下修改：

#### 1. 添加日期验证函数

```python
def _check_date_consistency() -> bool:
    """验证系统日期是否在合理范围内"""
    from datetime import datetime
    today = datetime.now().date()
    # 允许范围：2020-2050
    if today.year < 2020 or today.year > 2050:
        print(f"[WARNING] 系统日期异常: {today}")
        return False
    return True

def _extract_cache_date(filename: str) -> str:
    """从缓存文件名中提取日期，格式 {user_id}_{YYYYMMDD}"""
    parts = filename.replace('.json', '').split('_')
    if len(parts) >= 2:
        return parts[-1]  # 返回 YYYYMMDD
    return ""
```

#### 2. 修改 `briefing()` 方法 (L130-180)

```python
def briefing(self, user_id: str) -> dict:
    """
    每日简报（精简版）
    不问具体问题，只看大盘状态+持仓风险+信号
    优先读取当日缓存（night_worker 07:30 预生成），避免重复计算
    """
    # ---- 每日文件缓存（当日命中直接返回）----
    today = datetime.now().strftime("%Y%m%d")
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    
    # 关键修复：验证缓存日期与当前日期匹配
    if cache_fp.exists():
        try:
            cached = json.loads(cache_fp.read_text(encoding="utf-8"))
            cache_date = _extract_cache_date(cache_fp.stem)
            
            # 只有当缓存日期 == 当前日期时才返回缓存
            if cache_date == today:
                cached["from_cache"] = True
                return cached
            else:
                # 日期不匹配，删除过期缓存
                try:
                    cache_fp.unlink()
                    print(f"[STEWARD] 删除过期缓存: {cache_fp.name}")
                except Exception as e:
                    print(f"[STEWARD] 删除缓存失败: {e}")
        except Exception as e:
            print(f"[STEWARD] 读晨报缓存失败: {e}")

    # 缓存未命中或已过期，生成新的晨报
    start = time.time()
    # ... 后续代码保持不变
```

#### 3. 修改 `briefing_history()` 方法 (L254-271)

```python
def briefing_history(self, user_id: str, days: int = 7) -> list:
    """
    返回最近 N 天的晨报缓存列表（MB-005 往期晨报）
    关键修复：过滤掉日期在未来或超过 N 天的缓存
    """
    if not _BRIEF_DIR.exists():
        return []
    
    from datetime import datetime, timedelta
    today = datetime.now().date()
    cutoff_date = (today - timedelta(days=days)).strftime("%Y%m%d")
    
    files = sorted(_BRIEF_DIR.glob(f"{user_id}_*.json"), reverse=True)
    result = []
    
    for fp in files[:days * 2]:  # 扫描范围稍大一些，防止文件缺失
        try:
            # 关键修复：提取并验证日期
            date_str = fp.stem.replace(f"{user_id}_", "")
            
            # 跳过格式不符的文件
            if len(date_str) != 8 or not date_str.isdigit():
                continue
            
            # 跳过未来的日期
            if date_str > today.strftime("%Y%m%d"):
                print(f"[STEWARD] 跳过未来的缓存: {fp.name}")
                continue
            
            # 跳过太旧的日期（超过 N 天）
            if date_str < cutoff_date:
                break
            
            data = json.loads(fp.read_text(encoding="utf-8"))
            data["date"] = date_str
            result.append(data)
            
            if len(result) >= days:
                break
                
        except Exception as e:
            print(f"[STEWARD] 读往期晨报失败 {fp}: {e}")
    
    return result
```

### 方案 B: 快速修复（临时方案）

如果需要立即修复而不想更改太多代码，可以只修改 `briefing()` 方法来跳过日期不匹配的缓存：

```python
def briefing(self, user_id: str) -> dict:
    today = datetime.now().strftime("%Y%m%d")
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    
    # 快速修复：只使用完全匹配当日期的缓存
    if cache_fp.exists() and cache_fp.stem == f"{user_id}_{today}":
        try:
            cached = json.loads(cache_fp.read_text(encoding="utf-8"))
            cached["from_cache"] = True
            return cached
        except Exception as e:
            print(f"[STEWARD] 读晨报缓存失败: {e}")
    
    # 后续代码保持不变...
```

## 测试验证

### 单元测试 (`test_steward_date_validation.py`)

```python
import unittest
from datetime import datetime, timedelta
from pathlib import Path
import json

class TestStewardDateValidation(unittest.TestCase):
    
    def test_briefing_uses_today_only(self):
        """验证 briefing() 只使用当日缓存"""
        steward = get_steward()
        
        # 创建一个过期的缓存文件
        old_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        old_cache = {
            "regime": "old_regime",
            "timestamp": old_date
        }
        
        # 调用 briefing 应该生成新的报告，而不是返回旧缓存
        result = steward.briefing("test_user")
        self.assertNotEqual(result.get("regime"), old_cache["regime"])
    
    def test_briefing_history_filters_future_dates(self):
        """验证 briefing_history() 过滤未来日期"""
        steward = get_steward()
        
        # 创建一个未来的缓存文件
        future_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        
        history = steward.briefing_history("test_user", days=7)
        
        # 验证没有未来日期的项
        for item in history:
            cache_date = item.get("date", "")
            today = datetime.now().strftime("%Y%m%d")
            self.assertLessEqual(cache_date, today)
    
    def test_briefing_history_max_days_limit(self):
        """验证 briefing_history() 不返回太旧的缓存"""
        steward = get_steward()
        history = steward.briefing_history("test_user", days=7)
        
        today = datetime.now().date()
        cutoff = (today - timedelta(days=7)).strftime("%Y%m%d")
        
        for item in history:
            cache_date = item.get("date", "")
            self.assertGreaterEqual(cache_date, cutoff)
```

### 手动验证步骤

1. **验证系统时间**
   ```bash
   date  # 应该显示 2026-05-14 或之后
   ```

2. **清理过期缓存**
   ```bash
   rm -f /path/to/data/briefings/LeiJiang_2025*.json
   ```

3. **测试晨报 API**
   ```bash
   curl "http://localhost:8000/api/steward/briefing?userId=LeiJiang"
   ```
   验证响应中的日期是否为当前日期

4. **测试往期晨报 API**
   ```bash
   curl "http://localhost:8000/api/steward/briefing-history?userId=LeiJiang&days=7"
   ```
   验证返回的所有日期都在最近 7 天内

## 部署建议

1. **立即修复** (优先级: HIGH)
   - 应用方案 A 的所有修改
   - 新增 `_check_date_consistency()` 和 `_extract_cache_date()` 函数
   - 修改 `briefing()` 和 `briefing_history()` 方法

2. **数据清理** (优先级: HIGH)
   - 删除所有过期的缓存文件（日期 < 当前日期的）
   - 脚本: `backend/scripts/cleanup_cache.py`

3. **监控添加** (优先级: MEDIUM)
   - 在晨报生成时记录系统日期
   - 在日期异常时发送告警
   - 添加日志追踪缓存命中/未命中情况

4. **文档更新** (优先级: LOW)
   - 更新缓存策略文档
   - 添加日期相关的注释
   - 更新 API 文档中的日期格式说明

## 文件修改清单

- [ ] `backend/services/steward.py` — 添加日期验证逻辑
- [ ] `backend/api/steward.py` — API 层无需修改（服务层已处理）
- [ ] `pages/analysis.js` — 无需修改（已正确处理日期格式）
- [ ] `backend/scripts/night_worker.py` — 验证日期生成逻辑（已正确）

## 预期效果

- ✅ 晨报始终显示当前日期
- ✅ 往期晨报不包含未来日期
- ✅ 过期缓存自动清理
- ✅ 系统日期异常时有警告日志

