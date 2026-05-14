# 晨报日期 Bug - 快速参考

## 🔍 问题症状
```
显示: 📊 2025-07-14 钱袋子晨报
应该: 📊 2026-05-14 钱袋子晨报
```

---

## 📍 关键代码位置

### 1️⃣ 晨报生成 (最关键)
- **文件**: `backend/scripts/night_worker.py`
- **函数**: `step_generate_products()` 
- **行号**: 415
```python
today = date.today().isoformat()  # 使用当前系统日期
briefing = f"""📊 {today} 钱袋子晨报"""  # 第 475 行
```

### 2️⃣ 晨报缓存 (最可疑)
- **文件**: `backend/services/steward.py`
- **函数**: `briefing()` 
- **行号**: 137-145
```python
today = datetime.now().strftime("%Y%m%d")  # "20260514"
cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
# 如果缓存存在 → 返回旧数据
```

### 3️⃣ 历史简报查询
- **文件**: `backend/services/steward.py`
- **函数**: `briefing_history()` 
- **行号**: 254-271
```python
files = sorted(_BRIEF_DIR.glob(f"{user_id}_*.json"), reverse=True)
# 返回所有历史缓存（可能包含 20250714 的数据）
```

### 4️⃣ 前端显示
- **文件**: `pages/analysis.js`
- **函数**: `runBriefingHistory()` 
- **行号**: 626-636
```javascript
const dateStr = b.date || '';  // 后端返回的日期
const dateLabel = `${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}`;
```

---

## 📁 数据存储位置

```
project/
├── data/
│   ├── briefings/                    ← 晨报缓存目录
│   │   ├── LeiJiang_20250714.json   ← 🔴 旧数据可能在这里
│   │   ├── LeiJiang_20250715.json
│   │   └── LeiJiang_20260514.json
│   └── night_worker/                 ← 生成日志
│       ├── products_2026-05-14.json
│       ├── briefings_2026-05-14.json
│       └── 2026-05-14.log
└── backend/
    ├── scripts/
    │   └── night_worker.py           ← 📋 主程序
    ├── services/
    │   ├── steward.py               ← 🔴 缓存逻辑
    │   └── wxwork_push.py           ← 推送逻辑
    └── api/
        └── steward.py               ← API 路由
```

---

## 🔄 数据流向

```
系统时间 (2026-05-14)
        ↓
生成晨报 (04:00)
├─ step_generate_products() 
│   └─ date.today() = 2026-05-14 ✓
│
├─ 保存文件
│   ├─ data/night_worker/products_2026-05-14.json
│   └─ data/night_worker/briefings_2026-05-14.json
└─ 保存缓存
    └─ data/briefings/LeiJiang_20260514.json
        
        ↓
推送晨报 (08:30)
├─ push_morning()
└─ send_daily_report_to(wxid, msg)

        ↓
前端查询 (任意时间)
├─ /api/steward/briefing?userId=LeiJiang
│   └─ steward.briefing()
│       ├─ 检查 data/briefings/LeiJiang_20260514.json
│       ├─ 如果存在 → 返回缓存 ✓
│       └─ 如果不存在 → 执行 "fast" 管线重新生成
│
└─ /api/steward/briefing-history?userId=LeiJiang
    └─ steward.briefing_history()
        ├─ 扫描 data/briefings/LeiJiang_*.json
        └─ 返回最近 7 天的文件
            (⚠️ 如果有 LeiJiang_20250714.json → 会被返回！)
```

---

## ⚠️ 问题根源分析

### 最可能原因 (70%)
```
情景1: 时间穿梭
├─ 2025-07-14: 正常生成晨报 → LeiJiang_20250714.json
├─ 时间被回调到 2025-07-13
├─ 时间向前推进到 2026-05-14
└─ 但旧缓存文件仍然存在，API 返回了历史数据

情景2: 缓存未清理
├─ 历史简报查询时，扫描所有 *.json 文件
├─ 返回的列表中包含 20250714 的数据
└─ 前端显示了最旧的数据
```

### 次要原因 (20%)
```
系统时间错误
├─ 服务器时钟同步失败
├─ 时区配置错误
└─ datetime.now() 返回错误的时间
```

### 其他原因 (10%)
```
缓存策略问题
├─ 缓存 TTL 设置过长
├─ 缓存键冲突
└─ 并发读写问题
```

---

## 🛠️ 快速诊断命令

```bash
# 1. 检查系统时间
date
timedatectl status

# 2. 查看缓存文件
ls -lh data/briefings/ | sort
ls -lh data/night_worker/ | sort

# 3. 查看最新的缓存内容
cat data/briefings/LeiJiang_$(date +%Y%m%d).json | jq '.date' 

# 4. 检查旧缓存
find data/briefings -name "*202507*" -o -name "*202506*"

# 5. 查看晨报生成日志
tail -50 data/night_worker/$(date +%Y-%m-%d).log

# 6. 测试 API
curl "http://localhost:8000/api/steward/briefing?userId=LeiJiang" | jq '.timestamp'
curl "http://localhost:8000/api/steward/briefing-history?userId=LeiJiang&days=7" | jq '.history[0].date'
```

---

## 🧹 紧急清理步骤

```bash
# 1. 删除旧缓存
rm -f data/briefings/*_202507*.json
rm -f data/briefings/*_202506*.json

# 2. 删除旧产物
rm -f data/night_worker/products_2025-*.json
rm -f data/night_worker/briefings_2025-*.json

# 3. 验证清理结果
ls -la data/briefings/ | grep -E "202[56]"

# 4. 强制重新生成（可选）
python backend/scripts/night_worker.py

# 5. 或者只推送已有的
python backend/scripts/night_worker.py --push-only
```

---

## 📊 API 响应示例

### 当前简报 (有 Bug)
```json
{
  "regime": "oscillating",
  "timestamp": "2026-05-14T10:30:00",
  "date_in_report": "2025-07-14"  ← 🔴 错误！
}
```

### 历史简报列表 (有 Bug)  
```json
{
  "history": [
    {
      "date": "20250714",  ← 🔴 过期的缓存
      "regime": "...",
      "risk_level": "..."
    },
    {
      "date": "20260514",  ← ✓ 当前的缓存
      "regime": "...",
      "risk_level": "..."
    }
  ]
}
```

---

## ✅ 修复检查清单

- [ ] 确认系统时间正确 (`date` 命令)
- [ ] 清理旧缓存文件 (data/briefings/)
- [ ] 清理旧产物文件 (data/night_worker/)
- [ ] 检查 Cron 任务配置
- [ ] 手动执行晨报生成
- [ ] 验证 API 返回的日期
- [ ] 在前端查看历史简报列表
- [ ] 检查企微推送的消息内容

---

## 📝 代码改进建议

### 短期 (快速修复)
```python
# 在 steward.briefing_history() 中添加过滤
def briefing_history(self, user_id: str, days: int = 7) -> list:
    files = sorted(_BRIEF_DIR.glob(f"{user_id}_*.json"), reverse=True)
    result = []
    today = datetime.now().date()
    
    for fp in files[:days * 2]:  # 多取些以应对非交易日
        try:
            date_str = fp.stem.replace(f"{user_id}_", "")
            file_date = datetime.strptime(date_str, "%Y%m%d").date()
            
            # 只返回最近 N 天的有效缓存
            if (today - file_date).days <= days:
                data = json.loads(fp.read_text(encoding="utf-8"))
                data["date"] = date_str
                result.append(data)
        except Exception as e:
            continue
    
    return result
```

### 长期 (架构改进)
```python
# 添加缓存有效性检查
def is_cache_valid(cache_file_path, max_age_hours=36):
    """检查缓存是否仍然有效"""
    if not cache_file_path.exists():
        return False
    
    file_mtime = datetime.fromtimestamp(cache_file_path.stat().st_mtime)
    age_hours = (datetime.now() - file_mtime).total_seconds() / 3600
    
    return age_hours < max_age_hours

# 在 briefing() 中使用
def briefing(self, user_id: str) -> dict:
    today = datetime.now().strftime("%Y%m%d")
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    
    if cache_fp.exists() and is_cache_valid(cache_fp):
        # 使用缓存
        cached = json.loads(cache_fp.read_text(encoding="utf-8"))
        cached["from_cache"] = True
        return cached
    
    # 重新生成
    ctx = DecisionContext(user_id=user_id, question="每日简报")
    # ... 执行 fast 管线 ...
```

