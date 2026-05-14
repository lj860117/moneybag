# 晨报日期 Bug - 完整代码流程图

## 执行流程时序图

```
时间线: 2026-05-14 (当前日期)

┌─ 01:00 ─────────────────────────────────────────────────────┐
│  系统启动 night_worker.py                                     │
│  run_night_worker()                                          │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─ 01:00-03:00 ───────────────────────────────────────────────┐
│  各项数据采集 + R1 Phase 1/2/3                               │
│  ├─ phase1 = step_r1_phase1()                               │
│  ├─ phase2 = step_r1_phase2()                               │
│  └─ phase3 = step_r1_phase3()                               │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─ 04:00 ──── 📊 晨报生成 ────────────────────────────────────┐
│  step_generate_products(phase1, phase2, phase3)              │
│  ┌────────────────────────────────────────────┐             │
│  │ 第 415 行:                                  │             │
│  │ today = date.today().isoformat()            │ ← 系统日期  │
│  │ today = "2026-05-14"                        │             │
│  └────────────────────────────────────────────┘             │
│                                                               │
│  ┌────────────────────────────────────────────┐             │
│  │ 第 475 行:                                  │             │
│  │ briefing = f"""📊 {today} 钱袋子晨报"""     │ ← 生成内容  │
│  │ briefing = "📊 2026-05-14 钱袋子晨报"      │             │
│  └────────────────────────────────────────────┘             │
│                                                               │
│  ┌────────────────────────────────────────────┐             │
│  │ 第 540-543 行: 保存产物                      │             │
│  │ products[uid] = user_briefing               │ ← 含有日期  │
│  │                                             │             │
│  │ 第 541 行:                                  │             │
│  │ product_file = NIGHT_LOG_DIR / f"products_{today}.json" │
│  │ 保存到: data/night_worker/products_2026-05-14.json      │
│  └────────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─ 07:30 ──── 📋 简报组装 ────────────────────────────────────┐
│  step_morning_briefing(products, overnight)                  │
│  ├─ 读取 products 中的用户简报                              │
│  ├─ 为 Pro 用户添加外盘信息                                  │
│  ├─ 为 Simple 用户精简内容 (LLM)                            │
│  └─ 组装最终的 briefings[uid]                               │
│                                                               │
│  ┌────────────────────────────────────────────┐             │
│  │ 第 843-844 行: 保存简报文件                  │             │
│  │ briefing_file = NIGHT_LOG_DIR /                          │
│  │   f"briefings_{date.today()}.json"                       │
│  │ 保存到: data/night_worker/briefings_2026-05-14.json    │
│  └────────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─ 08:30 ──── 📤 推送简报 ────────────────────────────────────┐
│  push_morning() 或 step_push_briefing(briefings)             │
│  ├─ 读取 briefing_file                                      │
│  ├─ 逐用户推送 send_daily_report_to(wxid, msg)             │
│  └─ 推送内容含有日期信息                                    │
└─────────────────────────────────────────────────────────────┘
                           ↓
        (保存在文件系统中，数据已固化)
                           ↓
┌─ 任意时间 ──── 用户查询 ────────────────────────────────────┐
│  前端调用: /api/steward/briefing?userId=LeiJiang             │
│                      ↓                                       │
│  ┌─ backend/api/steward.py:L36 ──┐                         │
│  │ @router.get("/api/steward/briefing")                     │
│  │ steward.briefing(userId)                                 │
│  └────────────────────────────────────────────┘             │
│                      ↓                                       │
│  ┌─ backend/services/steward.py:L130-180 ────┐             │
│  │ def briefing(self, user_id: str):                        │
│  │     today = datetime.now().strftime("%Y%m%d")            │
│  │     # 第 138 行:                            │             │
│  │     cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"   │
│  │     cache_fp = "data/briefings/LeiJiang_20260514.json"  │
│  │                                             │             │
│  │     # 第 139 行: ⭐ 关键逻辑 ⭐              │             │
│  │     if cache_fp.exists():                  │             │
│  │         cached = json.loads(cache_fp.read_text())       │
│  │         cached["from_cache"] = True                      │
│  │         return cached                      │  ← 返回缓存  │
│  │                                             │             │
│  │         ⚠️ 如果今天没有生成新缓存              │             │
│  │            就从磁盘读取旧的                │             │
│  └────────────────────────────────────────────┘             │
│                      ↓                                       │
│  API 返回缓存数据 (含 date 字段)                            │
│                      ↓                                       │
│  前端: pages/analysis.js:L626-636 runBriefingHistory()     │
│  展示日期给用户看                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 缓存查询流程 (历史简报)

```
用户点击「往期晨报」
        ↓
前端调用: /api/steward/briefing-history?userId=LeiJiang&days=7
        ↓
┌────────────────────────────────────────────────────────┐
│ backend/api/steward.py:L45                             │
│ @router.get("/api/steward/briefing-history")           │
│ return steward.briefing_history(userId, days)         │
└────────────────────────────────────────────────────────┘
        ↓
┌────────────────────────────────────────────────────────┐
│ backend/services/steward.py:L254-271                   │
│ def briefing_history(self, user_id: str, days: int = 7):│
│     if not _BRIEF_DIR.exists():                       │
│         return []                                      │
│                                                        │
│     # 第 260 行: ⭐ 扫描目录                            │
│     files = sorted(                                   │
│         _BRIEF_DIR.glob(f"{user_id}_*.json"),        │
│         reverse=True                                  │
│     )                                                 │
│     # files = [                                       │
│     #   'LeiJiang_20260514.json',  ← 最新           │
│     #   'LeiJiang_20260513.json',                    │
│     #   'LeiJiang_20260512.json',                    │
│     #   ...                                          │
│     #   'LeiJiang_20250714.json',  ← 🔴 旧数据       │
│     # ]                                              │
│                                                        │
│     # 第 262-271 行: 逐个读取                          │
│     result = []                                       │
│     for fp in files[:days]:  # 只取前 7 个            │
│         try:                                         │
│             date_str = fp.stem.replace(f"{user_id}_", "")│
│             # date_str = "20250714"                  │
│             data = json.loads(fp.read_text())        │
│             data["date"] = date_str                  │
│             result.append(data)                      │
│         except Exception as e:                       │
│             pass                                     │
│                                                        │
│     return result                                    │
│     # ⚠️ 返回包含过期数据的列表                        │
└────────────────────────────────────────────────────────┘
        ↓
前端接收 result (包含旧数据)
        ↓
┌────────────────────────────────────────────────────────┐
│ pages/analysis.js:L626-636                             │
│ async function runBriefingHistory(){                   │
│     const items = d.history || [];                    │
│     items.forEach(b=>{                               │
│         const dateStr = b.date || '';  # "20250714"  │
│         const dateLabel = dateStr.length === 8 ?     │
│             `${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}` :│
│             dateStr;  # "2025-07-14"                 │
│         html += `<span>${dateLabel}</span>`          │
│     });                                              │
│ }                                                     │
└────────────────────────────────────────────────────────┘
        ↓
用户看到 "2025-07-14" 🔴 BUG!
```

---

## 数据流向图

```
文件系统
┌──────────────────────────────────────────┐
│ data/briefings/                          │
├──────────────────────────────────────────┤
│ LeiJiang_20250714.json  ← 旧缓存        │
│ LeiJiang_20250715.json  ← 旧缓存        │
│ LeiJiang_20260512.json  ← 正常缓存      │
│ LeiJiang_20260513.json  ← 正常缓存      │
│ LeiJiang_20260514.json  ← 最新缓存 ✓   │
└──────────────────────────────────────────┘

文件系统
┌──────────────────────────────────────────┐
│ data/night_worker/                       │
├──────────────────────────────────────────┤
│ products_2026-05-14.json  ← 产物        │
│ briefings_2026-05-14.json ← 简报 ✓     │
│ 2026-05-14.log            ← 日志        │
└──────────────────────────────────────────┘

内存
┌──────────────────────────────────────────┐
│ Python date/datetime objects             │
├──────────────────────────────────────────┤
│ date.today() = 2026-05-14 ✓             │
│ datetime.now() = 2026-05-14T... ✓       │
└──────────────────────────────────────────┘

API Response
┌──────────────────────────────────────────┐
│ GET /api/steward/briefing?userId=X      │
├──────────────────────────────────────────┤
│ {                                        │
│   "regime": "oscillating",               │
│   "timestamp": "2026-05-14T...",        │
│   "date": "20260514",  ← 内嵌的日期    │
│   "from_cache": true                    │
│ }                                        │
│                                          │
│ 如果缓存来自旧日期...                     │
│ "date": "20250714" 🔴                  │
└──────────────────────────────────────────┘
```

---

## 问题触发场景

### 场景 1: 缓存穿梭 (最常见)
```
T1 (2025-07-14 凌晨 04:00)
  ├─ 生成晨报
  └─ 保存: data/briefings/LeiJiang_20250714.json

T2 (时间调回至 2025-07-13)
  └─ (可能是系统维护、时区调整等)

T3 (时间调进至 2026-05-14)
  ├─ 系统启动 night_worker
  ├─ 生成今天的晨报
  └─ 保存: data/briefings/LeiJiang_20260514.json

T4 (用户查询历史简报)
  ├─ briefing_history() 扫描所有 *.json
  ├─ 返回: [20260514.json, ..., 20250714.json]
  └─ 前端显示最旧的: 2025-07-14 🔴
```

### 场景 2: 缓存污染
```
某个进程/脚本写错时间戳
  ├─ 创建了 LeiJiang_20250714.json
  ├─ 即使系统时间正确也会被读取
  └─ 导致显示错误的日期
```

### 场景 3: 并发 Bug
```
多个进程同时访问 data/briefings/
  ├─ 进程 A 写入新数据
  ├─ 进程 B 读取时读到混乱的数据
  └─ 导致时间戳不匹配
```

---

## 关键代码块分析

### Block 1: 晨报生成 ⭐
**文件**: backend/scripts/night_worker.py:411-546

```python
def step_generate_products(phase1, phase2, phase3):
    products = {}
    today = date.today().isoformat()  # ← 使用系统当前日期
    
    # ... 处理数据 ...
    
    briefing = f"""📊 {today} 钱袋子晨报
    
    📊 【市场温度】
    ...
    """
    
    # 保存产物
    product_file = NIGHT_LOG_DIR / f"products_{today}.json"
    product_file.write_text(json.dumps(products, ...))
    
    return products
```

**问题**: 如果系统时间有问题,这里的 `today` 就会错误

---

### Block 2: 缓存读写 ⭐⭐⭐
**文件**: backend/services/steward.py:130-180

```python
def briefing(self, user_id: str) -> dict:
    # 每日文件缓存
    today = datetime.now().strftime("%Y%m%d")
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    
    if cache_fp.exists():  # ← 🔴 关键：只要文件存在就返回
        try:
            cached = json.loads(cache_fp.read_text(encoding="utf-8"))
            cached["from_cache"] = True
            return cached
        except Exception as e:
            print(f"[STEWARD] 读晨报缓存失败: {e}")
    
    # 缓存不存在，执行 fast 管线重新生成
    # ... 生成新数据 ...
```

**问题**: 
- 没有验证缓存文件的修改时间
- 没有检查缓存的日期有效性
- 即使是 2 天前的缓存也会被返回

---

### Block 3: 历史列表 ⭐⭐⭐
**文件**: backend/services/steward.py:254-271

```python
def briefing_history(self, user_id: str, days: int = 7) -> list:
    if not _BRIEF_DIR.exists():
        return []
    
    files = sorted(_BRIEF_DIR.glob(f"{user_id}_*.json"), reverse=True)
    # ← 🔴 关键：返回所有匹配的文件，包括很久以前的
    
    result = []
    for fp in files[:days]:  # 只取前 N 个文件
        try:
            date_str = fp.stem.replace(f"{user_id}_", "")
            data = json.loads(fp.read_text(encoding="utf-8"))
            data["date"] = date_str  # ← 从文件名提取日期
            result.append(data)
        except Exception as e:
            print(f"[STEWARD] 读往期晨报失败 {fp}: {e}")
    
    return result
```

**问题**:
- 从文件名提取日期，但没有验证文件名的有效性
- 如果文件名是 "LeiJiang_20250714.json" 就会返回该日期
- 不检查该日期是否 > 7 天前

---

### Block 4: 前端显示
**文件**: pages/analysis.js:626-636

```javascript
async function runBriefingHistory(){
    const el = document.getElementById('stewardResult');
    const items = d.history || [];
    
    items.forEach(b => {
        const dateStr = b.date || '';
        // 从后端返回的日期转换为显示格式
        const dateLabel = dateStr.length === 8 
            ? `${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}`
            : dateStr;
        
        html += `<span>${dateLabel}</span>`;
    });
    
    el.innerHTML = html;
}
```

**问题**: 前端只做显示,没有验证日期的合理性

---

## 修复优先级

| 优先级 | 修复位置 | 修改难度 | 预期效果 |
|-------|--------|--------|--------|
| 🔴 P0 | 清理旧缓存文件 | 极简 | 立即解决问题 |
| 🔴 P1 | 添加缓存过期检查 | 简单 | 防止读取过期缓存 |
| 🟠 P2 | 添加日期有效性检查 | 中等 | 防止时间戳错误 |
| 🟡 P3 | 添加日志和监控 | 简单 | 便于排查 |
| 🟡 P4 | 时区统一配置 | 中等 | 根本解决时区问题 |

