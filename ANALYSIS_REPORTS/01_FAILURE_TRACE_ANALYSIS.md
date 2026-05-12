# 晨报无法生成的失败链追溯

## 关键发现：七个连锁失败点

### 1. **Night Worker 凌晨工作链中断**
**位置**: `backend/scripts/night_worker.py:326-644`

#### 问题：Phase1/Phase2/Phase3 失败导致 products 字典为空

```python
# Line 326-327: step_generate_products(phase1, phase2, phase3)
# 如果 phase1, phase2, phase3 任何一个是 None 或 {}，则：

# Line 333: macro = phase1.get("macro_analysis", "暂无宏观分析")
#          → 取默认值 "暂无宏观分析"

# Line 334-336: recs = phase3.get("recommendations", [])
#              → 取默认值 []，导致 rec_text = "  暂无推荐"

# Line 508: full_product = products.get(uid, "暂无分析")
#          → 如果 uid 不在 products，返回 "暂无分析"
```

**结果**: 即使所有数据源失败，night_worker 仍然生成简报文件，但内容只有：
- "暂无宏观分析"
- "暂无推荐"
- "暂无基金推荐"

**关键**: 没有异常被抛出，pipeline 继续执行，所以用户看到的是 "暂无分析" 而不是 API 错误

---

### 2. **Phase1 宏观分析失败的无声传播**
**位置**: `backend/scripts/night_worker.py:162-225`

```python
def step_r1_phase1():
    log("🔄 02:00 R1 Phase 1: 宏观环境")
    
    try:
        # 调用 Phase 1 管线
        from services.phase_engine import analyze_phase1
        result = analyze_phase1()
        return result
    except Exception as e:
        log(f"  ❌ Phase 1 失败: {e}")
        return {}  # ← 返回空字典，不抛出异常！
```

**宏观分析包含** (backend/services/macro_analysis.py):
- CPI（通过 `get_china_cpi()` → akshare）
- PMI（通过 `get_china_pmi()` → akshare）
- M2（通过 `get_china_m2()` → akshare）
- PPI（通过 `get_china_ppi()` → akshare）
- GDP增速、失业率等

**关键失败机制**:
```python
# backend/infra/data_source/macro/indicators.py

def get_china_cpi():
    try:
        import akshare
        # 调用 akshare
    except Exception as e:
        # 没有日志，直接返回 None
        return None

def get_china_pmi():
    try:
        import akshare
        # 调用 akshare
    except Exception as e:
        # 没有日志，直接返回 None
        return None
```

**级联效应**:
- CPI/PMI/M2/PPI 返回 None
- Phase1 的宏观分析函数接收到 None 值
- 没有数据 → 分析逻辑被跳过
- Phase1 返回 `{"macro_analysis": "暂无宏观分析"}`
- step_generate_products() 接收到此结果
- 简报内容变成 "暂无宏观分析"

---

### 3. **Regime 分类降级失败**
**位置**: `backend/services/regime_engine.py:37-97`

```python
def classify(force: bool = False) -> dict:
    """分类当前市场状态"""
    cached = _regime_cache.get("regime")
    if not force and cached is not None:
        return cached
    
    try:
        params = _get_market_params()  # 调用数据源
        regime, confidence, desc = _classify_regime(params)
    except Exception as e:
        # 数据获取失败时的降级
        result = {
            "regime": "oscillating",        # ← 默认震荡
            "confidence": 30,               # ← 低置信度
            "params": {},
            "description": f"数据获取失败({e})，默认震荡",
        }
    
    _regime_cache.set("regime", result, ttl=_REGIME_TTL)
    return result
```

**问题**:
- 当 HSI 300 日K 数据无法获取时
- 不会中止，而是降级到 "oscillating" + 30% 置信度
- 这会导致选择 "default" 管线（而不是 "cautious" 管线）
- 日常分析精度下降，但用户不知道

**关键**: 这是"silent graceful degradation"

---

### 4. **Pipeline 模块超时堆积**
**位置**: `backend/services/pipeline_runner.py:75-131`

```python
def step_parallel_modules(ctx: DecisionContext) -> DecisionContext:
    """执行所有有 enrich() 的模块（单模块 5s 超时）"""
    
    for name, entry in registry._modules.items():
        try:
            # 设置 5 秒单模块超时
            signal.alarm(5)
            try:
                ctx = enrich_fn(ctx)
            finally:
                signal.alarm(0)
        except _ModuleTimeout:
            # 模块超时，标记为不可用
            ctx.modules_results[name] = {"available": False, "error": "timeout 5s"}
            # ← 注意：这里没有抛出异常，继续下一个模块
        except Exception as e:
            ctx.modules_results[name] = {"available": False, "error": str(e)[:200]}
            # ← 继续
```

**问题**:
- 如果有 5 个模块，每个都超时 5 秒
- 总耗时 25 秒，但 pipeline 不会中止
- 最终 modules_results 全是 `{"available": False}`
- Pipeline 继续向下游传递

**Fast Pipeline 中** (用于晨报):
```python
fast = [load_user→regime→modules→risk→output]  # 5 步
```

- 即使所有 modules 超时
- fast pipeline 仍然执行 risk 步骤
- 最终输出 "信号不足，建议等待更多数据"

---

### 5. **缓存写入异常被忽略**
**位置**: `backend/services/steward.py:174-179`

```python
def briefing(self, user_id: str) -> dict:
    ctx = DecisionContext(user_id=user_id, question="每日简报")
    
    # 执行 fast pipeline
    ctx = self.runner.run("fast", ctx)
    
    # 写入当日缓存
    try:
        _BRIEF_DIR.mkdir(parents=True, exist_ok=True)
        cache_fp.write_text(json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[STEWARD] 写晨报缓存失败: {e}")
        # ← 只 print，不抛出异常
```

**问题**:
- 如果磁盘满、权限错误、路径无效
- 缓存写入失败
- 但 API 仍然返回成功（因为只有 print，没有 raise）
- 用户看不到错误

**级联**:
- 晨报被生成但缓存失败
- 次日用户再访问时，缓存 miss
- steward.briefing() 重新执行 fast pipeline
- 如果数据源仍然故障，就是"幽灵简报"（没有实质内容）

---

### 6. **Night Worker 保存简报被忽略的异常**
**位置**: `backend/scripts/night_worker.py:658-660`

```python
# 保存简报（等 08:30 推送）
briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
briefing_file.write_text(json.dumps(briefings, ensure_ascii=False, indent=2), encoding="utf-8")
```

**问题**:
- 这里没有 try-except
- 如果磁盘满、权限错误、目录不存在
- 脚本直接异常退出
- 简报生成流程中止，用户完全收不到晨报

**但更微妙的是**:
```python
def push_morning():
    """08:30 推送早安简报"""
    log("📤 08:30 推送早安简报（独立调用）")
    briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
    if briefing_file.exists():
        briefings = json.loads(briefing_file.read_text(encoding="utf-8"))
        step_push_briefing(briefings)
    else:
        log("  ⚠️ 无简报文件，跳过")  # ← 只 log，不发告警
```

- 如果 briefing_file 不存在
- 只是 log "无简报文件，跳过"
- 用户就真的收不到晨报，且没有任何提示

---

### 7. **用户缓存 TTL 过短导致重复失败**
**位置**: `backend/services/steward.py:130-181`

```python
def briefing(self, user_id: str) -> dict:
    today = datetime.now().strftime("%Y%m%d")
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    
    if cache_fp.exists():
        try:
            cached = json.loads(cache_fp.read_text(encoding="utf-8"))
            cached["from_cache"] = True
            return cached
        except Exception as e:
            print(f"[STEWARD] 读晨报缓存失败: {e}")
            # ← 读取失败，继续
    
    # 读取失败或不存在，执行 fast pipeline
    ctx = self.runner.run("fast", ctx)
    # ...
```

**问题**:
- 如果早晨 cache hit，用户得到好的简报
- 如果中午有新数据，用户重新请求
- 因为同一天（YYYYMMDD），缓存命中
- 用户看不到最新数据（中午的新闻、新信号等）

**但如果数据源故障**:
- 凌晨 night_worker 生成简报时，数据源已经故障
- 缓存内容是 "暂无分析"
- 即使后来数据源恢复，用户也看不到（因为同日缓存命中）

---

## 完整失败流程图

```
09:00 用户请求晨报
  ↓
steward.briefing(user_id)
  ↓
检查缓存: _BRIEF_DIR / f"{user_id}_{YYYYMMDD}.json"
  ↓
  ├─ 缓存存在？
  │  ├─ YES → 返回缓存内容（即使是 "暂无分析"）
  │  └─ NO → 继续
  ↓
执行 fast pipeline [load_user→regime→modules→risk→output]
  ↓
  ├─ regime 分类失败 → 降级到 "oscillating" + 30%
  │  └─ 选择 "default" 管线（不是 "cautious"）
  ├─ modules 全部超时 5s
  │  └─ modules_results = {"module1": {available: false}, ...}
  └─ risk 步骤
      └─ 没有模块结果 → risk_actions = []
  ↓
输出步骤: 生成用户响应
  └─ 如果没有足够的信号 → "市场信号不足，请稍候"
  ↓
返回给用户: {"regime": "oscillating", "risk_level": "normal", ...}

用户感受: 空内容或模板内容，没有实际分析
```

---

## 数据源故障时的"无声失败"模式

### 对比：正常情况 vs 故障情况

```python
# 正常情况（数据源可用）
get_china_cpi() → 3.2  # 返回数值
Phase1 宏观分析 → "CPI 环比3.2%，同比+2.1%，压力可控"

# 故障情况（数据源不可用）
get_china_cpi() → None  # 返回 None，没有日志，没有异常
Phase1 宏观分析 → None 值 → 分析逻辑被跳过 → "暂无宏观分析"
```

**关键点**: 系统没有异常，没有崩溃，没有错误消息，只是内容变成了"暂无"

---

## 验证失败的 6 个诊断点

### 1. 检查晨报缓存文件
```bash
ls -la ~/WorkBuddy/moneybag-for-claudecode/data/briefings/
cat ~/WorkBuddy/moneybag-for-claudecode/data/briefings/LeiJiang_20260512.json
```
**预期**: 如果缓存存在但内容为 "暂无分析"，说明 night_worker 生成时数据源故障

### 2. 检查 night_worker 日志
```bash
cat ~/WorkBuddy/moneybag-for-claudecode/data/night_worker/20260512.log
```
**关键词**:
- "❌ Phase 1 失败" → 宏观分析失败
- "❌ Phase 2 失败" → 持仓诊断失败
- "❌ Phase 3 失败" → 推荐失败
- "无简报文件，跳过" → 简报保存失败

### 3. 检查数据源健康状态
```bash
tail -20 ~/WorkBuddy/moneybag-for-claudecode/data/night_worker/20260512.log | grep "数据源"
```

### 4. 检查 regime 分类日志
```bash
grep -i "regime" ~/WorkBuddy/moneybag-for-claudecode/data/night_worker/20260512.log
```
**预期**: 如果显示 "数据获取失败，默认震荡"，说明 HSI 300 数据无法获取

### 5. 测试 API 端点
```bash
curl "http://localhost:8000/api/steward/briefing?userId=LeiJiang"
```
**检查**: 响应中 "from_cache" 字段和 content 字段

### 6. 检查模块超时情况
```bash
grep "timeout 5s" ~/WorkBuddy/moneybag-for-claudecode/data/night_worker/20260512.log | wc -l
```
**如果数值 > 3**, 说明模块普遍超时

