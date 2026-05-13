# 晨报日期 Bug 修复 — 实施指南

## 快速概览

**问题**：晨报显示"2025年7月14日"而当前日期是"2026年5月14日"

**根本原因**：`backend/services/steward.py` 缓存晨报文件时没有验证日期

**解决方案**：在 `briefing()` 和 `briefing_history()` 方法中添加严格的日期验证

**工作量**：30-45 分钟（包括测试和验证）

---

## 实施步骤

### 第 1 步：备份原文件（5 分钟）

```bash
cp backend/services/steward.py backend/services/steward.py.bak
```

### 第 2 步：应用代码修复（10 分钟）

在 `backend/services/steward.py` 中应用以下修改：

#### 2.1 添加导入

在文件顶部（第 14 行）添加：
```python
from datetime import timedelta
```

**完整的导入块应该是**：
```python
from datetime import datetime
from datetime import timedelta
```

#### 2.2 添加两个辅助函数

在 `_BRIEF_DIR` 定义之后（第 37 行）添加：

```python
def _check_date_consistency() -> bool:
    """验证系统日期是否在合理范围内"""
    from datetime import datetime
    today = datetime.now().date()
    # 允许范围：2020-2050
    if today.year < 2020 or today.year > 2050:
        print(f"[STEWARD] ⚠️  系统日期异常: {today}")
        return False
    return True


def _extract_cache_date(filename: str) -> str:
    """
    从缓存文件名中提取日期
    格式: {user_id}_{YYYYMMDD}
    返回: YYYYMMDD 或空字符串
    """
    parts = filename.replace('.json', '').split('_')
    if len(parts) >= 2:
        return parts[-1]
    return ""
```

#### 2.3 修改 `briefing()` 方法

将第 155-180 行：

**原代码**：
```python
    def briefing(self, user_id: str) -> dict:
        # ---- 每日文件缓存（当日命中直接返回）----
        today = datetime.now().strftime("%Y%m%d")
        cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
        if cache_fp.exists():
            try:
                cached = json.loads(cache_fp.read_text(encoding="utf-8"))
                cached["from_cache"] = True
                return cached
            except Exception as e:
                print(f"[STEWARD] 读晨报缓存失败: {e}")
```

**替换为**：
```python
    def briefing(self, user_id: str) -> dict:
        # ---- 每日文件缓存（当日命中直接返回）----
        today = datetime.now().strftime("%Y%m%d")
        cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
        if cache_fp.exists():
            try:
                cached = json.loads(cache_fp.read_text(encoding="utf-8"))
                # 关键修复：验证缓存日期与当前日期匹配
                cache_date = _extract_cache_date(cache_fp.stem)
                
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
```

#### 2.4 修改 `briefing_history()` 方法

将第 295-338 行：

**原代码**：
```python
    def briefing_history(self, user_id: str, days: int = 7) -> list:
        """
        返回最近 N 天的晨报缓存列表（MB-005 往期晨报）
        """
        if not _BRIEF_DIR.exists():
            return []
        files = sorted(_BRIEF_DIR.glob(f"{user_id}_*.json"), reverse=True)
        result = []
        for fp in files[:days]:
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                # fp.stem 格式：{user_id}_{YYYYMMDD}
                date_str = fp.stem.replace(f"{user_id}_", "")
                data["date"] = date_str
                result.append(data)
            except Exception as e:
                print(f"[STEWARD] 读往期晨报失败 {fp}: {e}")
        return result
```

**替换为**：
```python
    def briefing_history(self, user_id: str, days: int = 7) -> list:
        """
        返回最近 N 天的晨报缓存列表（MB-005 往期晨报）
        关键修复：过滤掉日期在未来或超过 N 天的缓存
        """
        if not _BRIEF_DIR.exists():
            return []
        
        files = sorted(_BRIEF_DIR.glob(f"{user_id}_*.json"), reverse=True)
        result = []
        
        today_dt = datetime.now().date()
        today_str = today_dt.strftime("%Y%m%d")
        cutoff_date = (today_dt - timedelta(days=days)).strftime("%Y%m%d")
        
        for fp in files[:days * 2]:  # 扫描范围稍大一些，防止文件缺失
            try:
                # 关键修复：提取并验证日期
                data = json.loads(fp.read_text(encoding="utf-8"))
                # fp.stem 格式：{user_id}_{YYYYMMDD}
                date_str = fp.stem.replace(f"{user_id}_", "")
                
                # 跳过格式不符的文件
                if len(date_str) != 8 or not date_str.isdigit():
                    continue
                
                # 跳过未来的日期
                if date_str > today_str:
                    print(f"[STEWARD] 跳过未来的缓存: {fp.name}")
                    continue
                
                # 跳过太旧的日期（超过 N 天）
                if date_str < cutoff_date:
                    break
                
                data["date"] = date_str
                result.append(data)
                
                if len(result) >= days:
                    break
                    
            except Exception as e:
                print(f"[STEWARD] 读往期晨报失败 {fp}: {e}")
        
        return result
```

### 第 3 步：验证代码语法（5 分钟）

```bash
cd /Users/leijiang/WorkBuddy/moneybag-for-claudecode

# 检查 Python 语法
python3 -m py_compile backend/services/steward.py

# 如果没有报错，说明语法正确
echo "✅ 语法检查通过"
```

### 第 4 步：清理过期缓存（10 分钟）

```bash
# 运行清理脚本
python3 backend/scripts/cleanup_morning_report_cache.py

# 或手动清理
rm -f data/briefings/*_202507*.json  # 清理 2025 年 7 月的缓存
rm -f data/briefings/*_202504*.json  # 清理 2025 年 4 月的缓存
```

### 第 5 步：单元测试（10 分钟）

```bash
# 如果使用 pytest
pytest backend/tests/test_steward_date_validation.py -v

# 或使用 unittest
python3 -m unittest backend.tests.test_steward_date_validation -v
```

### 第 6 步：手动验证（10 分钟）

#### 6.1 启动后端
```bash
cd /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend
python3 main.py
```

#### 6.2 测试晨报 API
```bash
# 获取晨报
curl "http://localhost:8000/api/steward/briefing?userId=LeiJiang" | jq .

# 预期输出应该包含当前日期（2026-05-14）
```

#### 6.3 测试往期晨报 API
```bash
# 获取往期晨报
curl "http://localhost:8000/api/steward/briefing-history?userId=LeiJiang&days=7" | jq '.history[0]'

# 验证返回的日期都在最近 7 天内
```

#### 6.4 打开前端测试
```bash
# 在浏览器中打开前端
# http://localhost:3000 (或相应的前端端口)

# 检查分析页面 → 往期晨报 tab
# 验证所有日期都是有效的、不在未来、不超过 7 天
```

---

## 测试清单

- [ ] 代码语法检查通过
- [ ] 导入语句正确
- [ ] `_check_date_consistency()` 函数可正常调用
- [ ] `_extract_cache_date()` 函数可正常调用
- [ ] `briefing()` 方法返回当日缓存
- [ ] `briefing()` 方法删除过期缓存
- [ ] `briefing_history()` 方法返回最近 7 天的缓存
- [ ] `briefing_history()` 方法过滤未来日期
- [ ] API 端点返回正确日期
- [ ] 前端显示正确的日期格式
- [ ] 没有 JavaScript 控制台错误
- [ ] 缓存命中日志正确显示

---

## 回滚计划

如果出现问题，可以快速回滚：

```bash
# 恢复备份
cp backend/services/steward.py.bak backend/services/steward.py

# 重启后端
# 通过 kill 进程或 Ctrl+C，然后重新启动
```

---

## 部署后的监控

1. **查看日志**
   ```bash
   tail -f backend/logs/steward.log
   ```
   
   查找这些日志输出：
   - `[STEWARD] 删除过期缓存:` — 说明清理逻辑正在工作
   - `[STEWARD] 跳过未来的缓存:` — 说明过滤逻辑正在工作

2. **定期检查**
   ```bash
   # 每周检查一次缓存目录
   ls -la data/briefings/ | wc -l  # 应该 < 100 个文件
   ```

3. **监控告警**
   - 如果日志中出现 `系统日期异常`，立即告警
   - 如果缓存大小持续增长，检查清理逻辑

---

## 常见问题

**Q: 修复后旧缓存还是被显示？**
A: 需要手动清理 `data/briefings/` 目录中的旧文件，或运行清理脚本。

**Q: 怎样验证修复是否有效？**
A: 
1. 清理所有旧缓存
2. 调用 `/api/steward/briefing` API
3. 查看返回的日期是否为当前日期
4. 检查是否有 `from_cache: true` 标记

**Q: 系统日期异常告警如何处理？**
A: 
1. 检查系统时间 `date`
2. 如果时间错误，使用 `timedatectl` 或 NTP 同步
3. 检查系统日志中是否有其他异常

**Q: 需要清理多久的缓存？**
A: 只需清理 2025 年及以前的缓存，或所有日期 < 2026-05-14 的文件。

---

## 预期效果

修复完成后，应该看到：

✅ 晨报始终显示当前日期（2026-05-14 或之后）
✅ 往期晨报不包含 2025 年 7 月 14 日
✅ 过期缓存自动被删除
✅ 系统日期异常时有警告日志
✅ 前端往期晨报 tab 显示正确的日期范围

---

## 后续改进建议

1. **缓存策略优化**
   - 考虑使用 Redis 代替文件系统缓存
   - 设置自动过期时间（TTL）

2. **监控和告警**
   - 添加晨报生成失败告警
   - 添加缓存污染检测

3. **文档更新**
   - 更新 API 文档中的日期格式说明
   - 添加缓存策略文档

4. **性能优化**
   - 考虑只缓存最近 7 天的晨报
   - 使用并行处理生成多用户晨报

