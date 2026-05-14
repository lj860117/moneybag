# 晨报无法生成问题的根本原因分析

## 问题陈述

当数据源（AKShare、Tushare 等）失败时，用户无法收到有意义的晨报。系统不会抛出错误，而是生成内容为"暂无分析"的空报告，或者完全不推送。

---

## 根本原因：架构中的三个设计缺陷

### 根本原因 #1：错误吞噬模式（Error Swallowing Pattern）

**问题代码位置**:

1. `backend/infra/data_source/macro/indicators.py` - 所有宏观指标函数
   ```python
   def get_china_cpi():
       try:
           import akshare
           # 调用 akshare...
       except Exception as e:
           # 只返回 None，不 raise，不 log
           return None
   ```

2. `backend/scripts/night_worker.py:162-225` - step_r1_phase1()
   ```python
   try:
       result = analyze_phase1()
       return result
   except Exception as e:
       log(f"  ❌ Phase 1 失败: {e}")
       return {}  # ← 返回空字典继续
   ```

3. `backend/services/regime_engine.py:86-94` - classify()
   ```python
   except Exception as e:
       result = {
           "regime": "oscillating",  # ← 静默降级
           "confidence": 30,
           "description": f"数据获取失败({e})，默认震荡",
       }
   ```

**后果**:
- 数据源故障 → 返回 None/空值，不抛异常
- 上游代码收到 None → 用默认值继续
- 逐层吞噬异常，最终变成"暂无分析"
- 用户完全不知道发生了什么

**关键问题**：这个设计是有意的"graceful degradation"，但对用户不透明

---

### 根本原因 #2：缓存穿透漏洞（Cache Penetration Vulnerability）

**问题代码位置**:

1. `backend/services/steward.py:130-181` - briefing()
   ```python
   today = datetime.now().strftime("%Y%m%d")
   cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
   
   if cache_fp.exists():
       return cached  # ← 直接返回，不检查过期
   
   # 缓存miss或不存在时重新生成
   ctx = self.runner.run("fast", ctx)
   ```

2. `backend/scripts/night_worker.py:658-660` - run_night_worker()
   ```python
   briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
   briefing_file.write_text(...)  # ← 无 try-except，可能写入失败
   ```

**失败流程**:
```
凌晨 02:00 数据源故障
  ↓
night_worker 执行 Phase1 失败 → 返回 {}
  ↓
step_generate_products() 生成 "暂无分析" 内容
  ↓
06:00 保存到 data/briefings/LeiJiang_20260512.json
  ↓
用户 09:00 请求晨报
  ↓
steward.briefing() 检查缓存 → HIT
  ↓
返回旧的 "暂无分析" 内容
  ↓
即使数据源已恢复，用户仍然看到空报告
  ↓
（直到次日凌晨新一轮执行）
```

**关键问题**：同一天内的缓存命中意味着数据源故障期间产生的坏数据被锁定

---

### 根本原因 #3：推送中止点的无提示失败（Silent Push Failure）

**问题代码位置**:

1. `backend/scripts/night_worker.py:668-676` - push_morning()
   ```python
   briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
   if briefing_file.exists():
       briefings = json.loads(briefing_file.read_text(encoding="utf-8"))
       step_push_briefing(briefings)
   else:
       log("  ⚠️ 无简报文件，跳过")  # ← 只 log，不警报
   ```

2. `backend/scripts/night_worker.py:658-660` - run_night_worker()
   ```python
   briefing_file.write_text(json.dumps(briefings, ...), encoding="utf-8")
   # ↑ 这里可能异常（磁盘满、权限错误、目录不存在）
   # ↓ 没有 try-except
   ```

**失败场景**:

**场景 A：磁盘满**
```
凌晨 06:00 写入简报
  ↓
Disk full error
  ↓
脚本异常退出，没有保存简报文件
  ↓
08:30 push_morning() 检查文件不存在
  ↓
只 log "无简报文件，跳过"
  ↓
用户收不到晨报，也没收到告警
```

**场景 B：文件权限错误**
```
脚本运行用户 ≠ 数据目录所有者
  ↓
写入权限被拒绝
  ↓
异常退出，文件不存在
  ↓
推送被跳过
  ↓
用户无声地收不到晨报
```

**场景 C：JSON 反序列化失败**
```
写入了损坏的 JSON（中途被打断）
  ↓
08:30 读取文件
  ↓
json.loads() 抛异常（但没有 try-except）
  ↓
push_morning() 直接崩溃
  ↓
用户收不到晨报
```

**关键问题**：推送流程有 3 个隐藏的单点故障

---

## 数据流中的关键检查点

```
数据源调用
    ↓
    ├─ 返回 None （宏观指标）
    ├─ raise Exception （API 超时）
    └─ 返回空数组 （查询无结果）
    ↓
Phase 分析
    ├─ None 值 → 分析被跳过 → "暂无分析"
    ├─ Exception ↓
    └─ 空数组 → 分析逻辑降级 → "暂无"
    ↓ (没有检查点！)
    ↓
step_generate_products()
    └─ 接收 "暂无分析" → 存入 products dict
    ↓
briefing_file.write_text()
    ├─ 可能失败：磁盘满、权限错误
    └─ 没有 try-except！
    ↓
push_morning()
    ├─ 文件不存在 → 只 log，不推送
    └─ 文件损坏 → json.loads() 崩溃，推送中止
    ↓
用户：无声的收不到晨报
```

---

## 为什么系统不会告诉你有问题

### 1. 没有错误传播
```python
# 宏观数据源
get_china_cpi()  # akshare 失败 → None（没有异常）

# Phase 1 分析  
phase1.get("macro_analysis", "暂无宏观分析")  # None → 使用默认值

# Night Worker
analyze_phase1()  # 返回 {}（没有异常）

# 步骤生成
step_generate_products(phase1={})  # 继续执行，没有异常
```

### 2. 没有告警机制
```python
# 在 push_morning() 中
if briefing_file.exists():
    # 推送
else:
    log("  ⚠️ 无简报文件，跳过")  # ← 只打日志，没有告警
    # 没有：
    # - 发送企微告警
    # - 写入告警日志
    # - 标记为失败状态
```

### 3. 没有监控指标
```
系统缺少：
- 简报质量评分（是否有实际内容 vs "暂无分析"）
- 数据源可用性指标
- 推送成功率统计
- 端到端延迟监控
```

---

## 问题的层级性质

### Level 1：单点故障（某个数据源失败）
```
AKShare 无法访问
  ↓ 自动降级
宏观指标返回 None
  ↓ 上游继续使用默认值
Phase 1 分析精度下降
  ↓ 但不抛异常
用户收到降级版本的晨报
  → ✓ 可接受（用户知道是降级版）
```

**问题**：用户不知道是降级版

---

### Level 2：多重故障堆积（多个模块超时）
```
模块 1 超时 5s
模块 2 超时 5s
模块 3 超时 5s
... × 10 个模块
  ↓ 总耗时 50s
Fast pipeline 仍继续执行
  ↓
最终 modules_results 全是 {available: false}
  ↓
Risk 步骤
  ↓
Output 步骤：无模块可用 → "信号不足"
  ↓
用户收到"信号不足"的晨报
  → ✗ 不可接受（性能差 + 无内容）
```

**问题**：50 秒延迟后收到空报告

---

### Level 3：推送中止（运维故障）
```
磁盘满 或 权限错误
  ↓
briefing_file.write_text() 异常
  ↓
脚本退出（没有 try-except）
  ↓
08:30 push_morning() 找不到文件
  ↓
用户收不到任何推送，也没有告警
  → ✗ 严重失败
```

**问题**：用户完全无法感知

---

## 修复优先级

### 优先级 1（必须修）：推送中止点
**文件**: `backend/scripts/night_worker.py:658-660`
```python
# 现在：无 try-except
briefing_file.write_text(...)

# 应该是：
try:
    briefing_file.write_text(...)
except Exception as e:
    log(f"  ❌ 保存简报失败: {e}")
    # 发送告警
    send_alert(f"晨报保存失败: {e}")
    return False
```

### 优先级 2（应该修）：缓存过期检查
**文件**: `backend/services/steward.py:139-143`
```python
# 现在：没有检查是否过期
if cache_fp.exists():
    cached = json.loads(...)
    return cached

# 应该是：
if cache_fp.exists():
    try:
        cached = json.loads(...)
        # 检查是否来自故障时期
        if cached.get("quality_score", 100) < 50:
            log(f"[STEWARD] 缓存质量低，重新生成")
            # 继续重新生成
        else:
            return cached
    except Exception:
        pass  # 缓存读取失败，继续
```

### 优先级 3（应该修）：错误传播
**文件**: `backend/services/regime_engine.py:86-94`
```python
# 现在：静默降级，用户不知道
except Exception as e:
    result = {
        "regime": "oscillating",
        "confidence": 30,
        "description": f"数据获取失败，默认震荡",
    }

# 应该是：
except Exception as e:
    result = {
        "regime": "oscillating",
        "confidence": 0,  # ← 0 表示不可信
        "description": f"数据获取失败，禁用分析",
        "data_source_error": str(e),  # ← 记录错误
    }
    log(f"[REGIME] 数据获取失败: {e}")  # ← 记录到日志
```

### 优先级 4（优化）：告警机制
```python
# 增加全局告警
def _push_alert(title, message):
    try:
        from services.wxwork_push import send_daily_report_to
        send_daily_report_to("LeiJiang", f"⚠️ {title}\n{message}")
    except Exception:
        pass

# 在推送失败时调用
if not briefing_file.exists():
    _push_alert("晨报推送失败", "简报文件不存在，请检查 night_worker 是否正常运行")
```

---

## 完整问题清单

| 问题 | 位置 | 严重性 | 用户感受 |
|------|------|--------|---------|
| 宏观数据源失败返回 None 而非异常 | macro/indicators.py | 高 | 无法感知数据源故障 |
| Phase 分析失败返回 {} 而非异常 | night_worker.py:162-225 | 高 | 晨报变成"暂无分析" |
| Regime 分类静默降级 | regime_engine.py:86-94 | 中 | 分析精度下降但不知道 |
| 模块超时不中止 pipeline | pipeline_runner.py:117-121 | 中 | 50 秒延迟后收空报告 |
| 缓存命中不检查过期 | steward.py:139-143 | 中 | 坏数据被锁定一整天 |
| 缓存写入无 try-except | steward.py:174-179 | 低 | 缓存失败不知道 |
| 简报文件写入无 try-except | night_worker.py:658-660 | **严重** | **用户收不到推送，无告警** |
| 推送失败无告警 | night_worker.py:668-676 | **严重** | **完全无法感知故障** |
| 没有数据质量评分 | night_worker.py:326-527 | 中 | 无法区分好报告和坏报告 |
| 没有监控指标 | 全系统 | 低 | 无法定位问题根源 |

