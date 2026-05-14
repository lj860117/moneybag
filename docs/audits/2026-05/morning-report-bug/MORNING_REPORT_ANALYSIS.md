# 钱袋子晨报（晨报）日期 Bug 分析报告

## 问题陈述
- **症状**: 晨报显示日期为 "2025年7月14日"，但当前日期应为 "2026年5月14日"
- **影响范围**: 用户看到的每日晨报标题中的日期错误
- **严重性**: 高（影响用户信息准确性）

---

## 代码架构概览

### 主要组件

#### 1. **后端晨报生成** - `backend/scripts/night_worker.py`
```
职责: AI 凌晨自主工作链（01:00-08:30）
流程:
  01:00 → 数据源健康巡检
  01:30 → 数据预热
  02:00 → R1 Phase 1（宏观分析）
  02:30 → R1 Phase 2（持仓诊断）
  03:00 → R1 Phase 3（推荐+决策）
  04:00 → 生成分析产物（★ 晨报在这一步生成）
  05:00 → 研报存档
  06:00 → 维护
  07:00 → 外盘+事件检查
  07:30 → 生成早安简报
  08:30 → 推送早安简报
```

#### 2. **晨报服务** - `backend/services/steward.py`
```
职责: AI投资管家
入口方法:
  - briefing(user_id) → 每日简报（精简版）
  - briefing_history() → 往期晨报列表
```

#### 3. **前端晨报显示** - `pages/analysis.js`
```
职责: 前端显示往期晨报列表
```

---

## 日期处理流程分析

### 🔴 **关键代码位置 1: 晨报内容生成**
**文件**: `backend/scripts/night_worker.py`
**函数**: `step_generate_products()`
**行号**: 411-546

```python
def step_generate_products(phase1, phase2, phase3):
    log("📝 04:00 生成分析产物")
    products = {}
    today = date.today().isoformat()  # ★ LINE 415：使用 date.today().isoformat()
    
    # ... 数据处理 ...
    
    # 第 475 行：组装晨报
    briefing = f"""📊 {today} 钱袋子晨报
    
    📊 【市场温度】
    ...
    """
    # ★ 这里的 {today} 应该会显示 "2026-05-14" (ISO 格式)
```

**当前实现**:
- `date.today()` 返回Python `date` 对象，当前日期
- `.isoformat()` 返回 ISO 8601 格式字符串: "2026-05-14"

**问题分析**:
- 代码逻辑上是正确的，使用系统当前日期
- 但显示的格式是 ISO 格式 ("2026-05-14")，而不是用户反馈的 "2025年7月14日"
- **推断**: 日期可能在其他地方被硬编码或缓存了


### 🔴 **关键代码位置 2: 晨报缓存和存档**
**文件**: `backend/scripts/night_worker.py`
**行号**: 540-545

```python
# 保存产物文件
product_file = NIGHT_LOG_DIR / f"products_{today}.json"
product_file.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")

# 保存简报文件（08:30 由 --push-only 推送）
briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
briefing_file.write_text(json.dumps(briefings, ensure_ascii=False, indent=2), encoding="utf-8")
```

**存储位置**: `data/night_worker/`
- `products_{date}.json` - 分析产物
- `briefings_{date}.json` - 简报文件


### 🔴 **关键代码位置 3: 晨报服务缓存**
**文件**: `backend/services/steward.py`
**行号**: 34-35, 137-180

```python
# 晨报缓存目录
_BRIEF_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data")) / "briefings"

def briefing(self, user_id: str) -> dict:
    """每日简报（精简版）"""
    # ---- 每日文件缓存（当日命中直接返回）----
    today = datetime.now().strftime("%Y%m%d")  # ★ 格式: "20260514"
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    if cache_fp.exists():
        try:
            cached = json.loads(cache_fp.read_text(encoding="utf-8"))
            cached["from_cache"] = True
            return cached
        except Exception as e:
            print(f"[STEWARD] 读晨报缓存失败: {e}")
```

**缓存文件格式**: `LeiJiang_20260514.json`

**问题推断**:
- 如果缓存中存有旧日期（如 "20250714.json"）的数据
- 即使系统时间更新了，只要缓存文件存在，就会返回旧数据
- 这可能是 **主要原因**


### 🔴 **关键代码位置 4: 前端日期显示**
**文件**: `pages/analysis.js`
**行号**: 626-636

```javascript
async function runBriefingHistory(){
    const items = d.history || [];
    items.forEach(b=>{
        const dateStr = b.date || '';
        // 如果 dateStr 格式是 YYYYMMDD，转换为 YYYY-MM-DD
        const dateLabel = dateStr.length === 8 
            ? `${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}`
            : dateStr;
        html += `<span>${iconMap[b.regime]||'📊'} ${dateLabel}</span>`
    });
}
```

**前端处理**:
- 接收后端返回的 `date` 字段（YYYYMMDD 格式）
- 转换为 "YYYY-MM-DD" 显示
- 不会生成 "年月日" 格式


---

## 日期格式转换链路

```
系统时间 (2026-05-14)
    ↓
step_generate_products()
    ├─ date.today().isoformat() → "2026-05-14"
    ├─ 写入产物: products_2026-05-14.json
    └─ 写入简报: briefings_2026-05-14.json
    ↓
steward.briefing()
    ├─ 检查缓存: _BRIEF_DIR/{user_id}_YYYYMMDD.json
    ├─ datetime.now().strftime("%Y%m%d") → "20260514"
    └─ 缓存键: LeiJiang_20260514.json
    ↓
前端 pages/analysis.js
    ├─ 从 briefing_history API 获取
    ├─ b.date 格式: YYYYMMDD
    └─ 显示格式: YYYY-MM-DD
```


---

## 🎯 根本原因推断

### **假设 1: 缓存未清理** (最可能)
- **症状**: 晨报显示旧日期
- **原因**: `data/briefings/LeiJiang_20250714.json` 等旧文件未删除
- **触发条件**:
  1. 系统时间被回调到 2025-07-14
  2. 生成了晨报缓存
  3. 系统时间又向前推进到 2026-05-14
  4. 但前端查询历史简报时，访问到旧缓存
  5. 或者 `briefing_history()` 返回了所有历史缓存文件

### **假设 2: 日期序列化 Bug**
- **症状**: 某个特定日期的晨报显示错误
- **原因**: 时区转换或日期序列化问题
- **证据**: `datetime.now()` 在 UTC 与本地时间转换时可能出错

### **假设 3: WeChat Bot 集成中的日期问题**
- **相关文件**: `backend/services/wxwork_push.py`
- **症状**: 推送给企业微信的晨报日期错误
- **需要检查**: 企微推送中是否有硬编码日期


---

## 相关文件详细列表

### 后端文件
1. **`backend/scripts/night_worker.py`** - 晨报生成主程序
   - `step_generate_products()` (L411) - 生成晨报内容 ⭐
   - `run_night_worker()` (L742) - 执行全链路
   - `push_morning()` (L852) - 推送晨报

2. **`backend/services/steward.py`** - 管家服务
   - `briefing()` (L130) - 获取每日简报 ⭐
   - `briefing_history()` (L254) - 往期晨报列表 ⭐

3. **`backend/api/steward.py`** - 路由
   - `/api/steward/briefing` (L36) - 获取简报
   - `/api/steward/briefing-history` (L45) - 历史列表 ⭐

4. **`backend/services/wxwork_push.py`** - 企微推送
   - `send_daily_report_to()` - 推送晨报

### 前端文件
1. **`pages/analysis.js`** - 分析页面
   - `runBriefingHistory()` (L617) - 显示往期晨报 ⭐
   - `loadBriefing()` (L595) - 加载今日简报

### 数据文件目录
- **`data/night_worker/`** - 晨报生成日志和产物
  - `products_{date}.json` - 分析产物
  - `briefings_{date}.json` - 简报文件
  - `{date}.log` - 执行日志

- **`data/briefings/`** - 晨报缓存目录
  - `{user_id}_{YYYYMMDD}.json` - 用户的每日缓存


---

## 调度任务配置

### 💻 凌晨执行
**时间**: 每天 01:00-08:30
**触发**: Cron Job（需确认系统配置）
**主程序**: `python backend/scripts/night_worker.py`

### 📤 推送执行
**时间**: 08:30
**触发**: `python backend/scripts/night_worker.py --push-only`
**作用**: 读取凌晨生成的简报并推送


---

## 问题诊断清单

- [ ] 检查 `data/briefings/` 目录中是否有 "20250714" 的缓存文件
- [ ] 检查 `data/night_worker/` 中的日志文件日期
- [ ] 验证系统时间和时区设置是否正确
- [ ] 检查 Cron 任务是否被正确配置和执行
- [ ] 查看企微推送日志中的时间戳
- [ ] 验证数据库/缓存中的时间戳


---

## 修复建议

### **短期修复** (紧急)
1. 清理旧的缓存文件
   ```bash
   rm data/briefings/*_202507*.json  # 删除 2025-07-* 的缓存
   rm data/night_worker/briefings_2025-07-*.json  # 删除产物
   ```

2. 验证当前系统时间
   ```bash
   date  # 检查系统时间是否正确
   timedatectl  # Linux 系统时间同步状态
   ```

3. 强制重新生成简报
   ```bash
   python backend/scripts/night_worker.py  # 执行完整链路
   ```

### **长期修复** (架构)
1. 在 `steward.briefing_history()` 中添加日期过滤
   - 只返回最近 N 天的缓存
   - 自动删除超过保留期的旧缓存

2. 添加日期有效性检查
   ```python
   def briefing(self, user_id: str):
       today = datetime.now().strftime("%Y%m%d")
       cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
       
       # 只使用当日缓存
       if cache_fp.exists() and is_today(cache_fp.stat().st_mtime):
           # 使用缓存
       else:
           # 重新生成
   ```

3. 时区配置
   - 确保所有时间操作使用一致的时区
   - 建议使用 `pytz` 或 `datetime.timezone`

4. 添加日期合理性检查
   ```python
   def step_generate_products():
       today = date.today()
       assert 2020 <= today.year <= 2030, f"Date out of range: {today}"
   ```


---

## 相关功能映射

| 功能 | 代码位置 | 触发时机 | 输出 |
|------|--------|--------|------|
| 晨报生成 | night_worker.py::step_generate_products | 每天 04:00 | products_{date}.json |
| 简报组装 | night_worker.py::step_morning_briefing | 每天 07:30 | briefings_{date}.json |
| 简报推送 | night_worker.py::step_push_briefing | 每天 08:30 | 企微消息 |
| 缓存读取 | steward.py::briefing | API调用 | 缓存数据 |
| 历史查询 | steward.py::briefing_history | API调用 | 最近7天缓存 |
| 前端显示 | pages/analysis.js::runBriefingHistory | 用户点击 | 往期晨报列表 |


---

## 测试验证步骤

1. **检查缓存文件**
   ```bash
   ls -la data/briefings/
   ls -la data/night_worker/
   ```

2. **查看API响应**
   ```bash
   curl "http://localhost:8000/api/steward/briefing?userId=LeiJiang"
   curl "http://localhost:8000/api/steward/briefing-history?userId=LeiJiang&days=7"
   ```

3. **检查日志**
   ```bash
   tail -100 data/night_worker/$(date +%Y-%m-%d).log
   ```

4. **验证时间戳**
   ```python
   from datetime import date, datetime
   print(f"Today: {date.today()}")
   print(f"Now: {datetime.now()}")
   print(f"Formatted: {datetime.now().strftime('%Y%m%d')}")
   ```

---

## 📋 总结

**晨报日期 Bug 的最可能原因**:
1. 缓存中存有旧日期的数据 (70% 概率)
2. 系统时间配置错误 (20% 概率)
3. 时区转换问题 (10% 概率)

**建议立即采取的行动**:
1. 检查并清理旧缓存文件
2. 验证系统时间和时区
3. 手动触发晨报重新生成
4. 在代码中添加日期有效性检查和日志

