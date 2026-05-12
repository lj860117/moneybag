# 晨报生成问题：详细修复方案

## 执行摘要

系统有 **10 个待修复问题**，其中 2 个 **必须立即修复**（否则用户完全收不到晨报）。

修复预估：
- 优先级 1（严重）：2-3 小时
- 优先级 2-3（重要）：4-5 小时
- 优先级 4（优化）：2-3 小时
- **总计：8-11 小时**

---

## 快速修复清单（必做）

### 修复 #1：保护简报文件写入（优先级 1）

**位置**：`backend/scripts/night_worker.py:658-665`

**现状**：
```python
# 保存简报（等 08:30 推送）
briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
briefing_file.write_text(json.dumps(briefings, ensure_ascii=False, indent=2), encoding="utf-8")

elapsed = time.time() - start
log(f"✅ AI 凌晨工作完成，耗时 {elapsed:.0f}秒，等待 08:30 推送")

return briefings
```

**问题**：
- 磁盘满、权限错误时脚本直接异常退出
- 没有 try-except
- 用户收不到推送

**修复方案**：
```python
# 保存简报（等 08:30 推送）
briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"

try:
    # 确保目录存在
    briefing_file.parent.mkdir(parents=True, exist_ok=True)
    
    # 写入简报
    briefing_file.write_text(
        json.dumps(briefings, ensure_ascii=False, indent=2), 
        encoding="utf-8"
    )
    
    log(f"✅ 简报已保存: {len(briefings)} 个用户")
    
except IOError as e:
    log(f"❌ 保存简报失败 (IOError): {e}")
    # 发送告警
    try:
        from services.wxwork_push import is_configured, send_daily_report_to
        if is_configured():
            msg = f"⚠️ 晨报保存失败\n错误: {e}\n请检查磁盘空间和文件权限"
            send_daily_report_to("LeiJiang", msg)
    except Exception as alert_e:
        log(f"  告警发送失败: {alert_e}")
    return briefings  # 返回内存中的数据供 push 使用

except Exception as e:
    log(f"❌ 保存简报失败 (其他): {e}")
    try:
        from services.wxwork_push import is_configured, send_daily_report_to
        if is_configured():
            send_daily_report_to("LeiJiang", f"⚠️ 晨报保存异常: {e}")
    except Exception:
        pass
    return briefings

elapsed = time.time() - start
log(f"✅ AI 凌晨工作完成，耗时 {elapsed:.0f}秒，等待 08:30 推送")

return briefings
```

**改动点**：
- [ ] 增加 try-except 围绕文件写入
- [ ] 区分 IOError（磁盘满、权限错误）和其他异常
- [ ] 发送企微告警而不是只 log
- [ ] 确保目录存在

**验证**：
```bash
# 测试磁盘满场景
du -sh /
# 如果还有空间，修改目录权限测试
chmod 000 ~/WorkBuddy/moneybag-for-claudecode/data/night_worker
python scripts/night_worker.py --step briefing 2>&1 | grep "保存简报失败"
chmod 755 ~/WorkBuddy/moneybag-for-claudecode/data/night_worker
```

---

### 修复 #2：保护推送读取（优先级 1）

**位置**：`backend/scripts/night_worker.py:668-676`

**现状**：
```python
def push_morning():
    """08:30 推送早安简报（独立调用）"""
    log("📤 08:30 推送早安简报（独立调用）")
    briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
    if briefing_file.exists():
        briefings = json.loads(briefing_file.read_text(encoding="utf-8"))
        step_push_briefing(briefings)
    else:
        log("  ⚠️ 无简报文件，跳过")
```

**问题**：
- 文件存在但被损坏时 json.loads() 抛异常
- 没有 try-except 围绕 json.loads()
- 推送流程直接崩溃
- 只 log，没有告警

**修复方案**：
```python
def push_morning():
    """08:30 推送早安简报（独立调用）"""
    log("📤 08:30 推送早安简报（独立调用）")
    briefing_file = NIGHT_LOG_DIR / f"briefings_{date.today()}.json"
    
    if not briefing_file.exists():
        log("  ⚠️ 无简报文件，跳过推送")
        # 发送告警（如果是第一次报告此问题）
        try:
            from services.wxwork_push import is_configured, send_daily_report_to
            if is_configured():
                send_daily_report_to(
                    "LeiJiang", 
                    "⚠️ 晨报文件不存在\n"
                    "night_worker 可能运行失败，请检查凌晨日志"
                )
        except Exception:
            pass
        return False
    
    try:
        content = briefing_file.read_text(encoding="utf-8")
        briefings = json.loads(content)
        
        if not briefings:
            log("  ⚠️ 简报内容为空，跳过推送")
            return False
        
        step_push_briefing(briefings)
        log("  ✅ 推送完成")
        return True
        
    except json.JSONDecodeError as e:
        log(f"  ❌ 简报文件损坏: {e}")
        try:
            from services.wxwork_push import is_configured, send_daily_report_to
            if is_configured():
                send_daily_report_to(
                    "LeiJiang", 
                    f"⚠️ 晨报文件损坏\n错误: {e}\n请检查数据完整性"
                )
        except Exception:
            pass
        return False
        
    except Exception as e:
        log(f"  ❌ 推送异常: {e}")
        try:
            from services.wxwork_push import is_configured, send_daily_report_to
            if is_configured():
                send_daily_report_to("LeiJiang", f"⚠️ 晨报推送失败: {e}")
        except Exception:
            pass
        return False
```

**改动点**：
- [ ] 围绕 json.loads() 增加 try-except
- [ ] 区分 JSONDecodeError 和其他异常
- [ ] 检查 briefings 是否为空
- [ ] 所有失败路径都发送告警

**验证**：
```bash
# 测试损坏的 JSON
echo '{invalid json' > ~/WorkBuddy/moneybag-for-claudecode/data/night_worker/briefings_20260512.json
python scripts/night_worker.py --push-only 2>&1 | grep "文件损坏"

# 测试正常的 JSON
echo '{"LeiJiang": "hello"}' > ~/WorkBuddy/moneybag-for-claudecode/data/night_worker/briefings_20260512.json
python scripts/night_worker.py --push-only 2>&1 | grep "推送完成"
```

---

## 重要修复（应该做）

### 修复 #3：错误传播（优先级 2）

**位置**：`backend/services/regime_engine.py:86-94`

**现状**：
```python
try:
    params = _get_market_params()
    regime, confidence, desc = _classify_regime(params)
    # ...
except Exception as e:
    # 静默降级
    result = {
        "regime": "oscillating",
        "confidence": 30,
        "params": {},
        "description": f"数据获取失败({e})，默认震荡",
        "timestamp": datetime.now().isoformat(),
    }
```

**问题**：
- 用户看不到置信度为 30 意味着"我在瞎猜"
- Phase 仍然基于这个不可靠的 regime 进行分析

**修复方案**：
```python
try:
    params = _get_market_params()
    regime, confidence, desc = _classify_regime(params)
    
    result = {
        "regime": regime,
        "confidence": confidence,
        "params": _clean_params(params),
        "description": desc,
        "timestamp": datetime.now().isoformat(),
        "geo_override": geo_override,
        "geo_severity": geo_severity,
        "data_available": True,  # ← 新增
    }
except Exception as e:
    log(f"[REGIME] 数据获取失败: {e}")  # ← 新增 log
    # 返回低可信度结果，标记为数据不可用
    result = {
        "regime": "oscillating",
        "confidence": 0,  # ← 改为 0 表示完全不可信
        "params": {},
        "description": f"数据源暂时不可用，使用默认状态。错误: {str(e)[:100]}",
        "timestamp": datetime.now().isoformat(),
        "data_available": False,  # ← 新增标志
        "error": str(e)[:200],  # ← 记录原始错误
    }
    print(f"[REGIME] 降级到 oscillating + confidence=0，不可用标志已设置")
```

**改动点**：
- [ ] 添加 `data_available` 字段标记数据可用性
- [ ] 故障时置信度改为 0 而不是 30
- [ ] 记录原始错误信息
- [ ] 增加日志输出

**下游影响**：
```python
# 在 Pipeline 中检查
if not ctx.regime_params.get("data_available", True):
    # 数据不可用，考虑是否应该跳过分析或返回降级版本
    log(f"[PIPELINE] 市场数据不可用，分析精度下降")
```

---

### 修复 #4：缓存过期检查（优先级 2）

**位置**：`backend/services/steward.py:130-181`

**现状**：
```python
def briefing(self, user_id: str) -> dict:
    today = datetime.now().strftime("%Y%m%d")
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    if cache_fp.exists():
        try:
            cached = json.loads(cache_fp.read_text(encoding="utf-8"))
            cached["from_cache"] = True
            return cached  # ← 直接返回，不检查质量
        except Exception as e:
            print(f"[STEWARD] 读晨报缓存失败: {e}")
```

**问题**：
- 如果凌晨数据源故障生成了"暂无分析"
- 用户中午访问时直接返回坏数据
- 即使数据源已恢复也看不到好的分析

**修复方案**：
```python
def briefing(self, user_id: str) -> dict:
    today = datetime.now().strftime("%Y%m%d")
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    
    if cache_fp.exists():
        try:
            cached = json.loads(cache_fp.read_text(encoding="utf-8"))
            
            # 检查缓存质量
            quality_score = cached.get("_metadata", {}).get("quality_score", 100)
            
            # 如果质量评分低于 50，说明是故障时生成的坏数据，重新生成
            if quality_score < 50:
                print(f"[STEWARD] 缓存质量低({quality_score}%), 重新生成")
                # 继续执行下面的 on-demand 生成
            else:
                cached["from_cache"] = True
                return cached
                
        except Exception as e:
            print(f"[STEWARD] 读晨报缓存失败: {e}")
            # 继续执行 on-demand 生成

    start = time.time()

    ctx = DecisionContext(user_id=user_id, question="每日简报")

    # Regime（轻量级，有缓存通常 <1s）
    regime_result = classify_regime()
    ctx.regime = regime_result["regime"]
    ctx.regime_confidence = regime_result["confidence"]
    ctx.regime_description = regime_result.get("description", "")
    
    # ★ 检查数据可用性
    data_available = regime_result.get("data_available", True)
    if not data_available:
        print(f"[STEWARD] 市场数据不可用，返回降级版本")

    # 用 fast 管线
    ctx = self.runner.run("fast", ctx)
    ctx.elapsed_seconds = round(time.time() - start, 1)
    
    # 计算质量评分（用于缓存检查）
    has_regime = bool(ctx.regime)
    has_modules = len([r for r in ctx.modules_results.values() 
                       if r.get("available", False)]) > 0
    quality_score = 100 if (has_regime and has_modules) else 50 if has_regime else 20
    
    # 组装简报
    briefing = {
        "regime": ctx.regime,
        "regime_description": _sanitize_regime_description(ctx),
        "risk_level": ctx.risk_level or "normal",
        "risk_actions": ctx.risk_actions[:3] if ctx.risk_actions else [],
        "signals_count": len(ctx.modules_results.get("signal_scout", {}).get("signals", [])) if isinstance(ctx.modules_results.get("signal_scout"), dict) else 0,
        "top_signal": _get_top_signal(ctx),
        "one_line": _generate_one_line(ctx),
        "elapsed": ctx.elapsed_seconds,
        "timestamp": datetime.now().isoformat(),
        "_metadata": {  # ← 新增元数据
            "quality_score": quality_score,
            "data_available": data_available,
            "modules_available": sum(1 for r in ctx.modules_results.values() if r.get("available", False)),
        },
    }

    # 写入当日缓存
    try:
        _BRIEF_DIR.mkdir(parents=True, exist_ok=True)
        cache_fp.write_text(json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[STEWARD] 写晨报缓存失败: {e}")

    return briefing
```

**改动点**：
- [ ] 添加 `_metadata` 字段记录质量评分
- [ ] 检查质量评分，低于 50 则重新生成
- [ ] 记录数据可用性和模块可用数
- [ ] 生成时检查 data_available 并 log

**验证**：
```bash
# 查看缓存中的质量评分
cat ~/WorkBuddy/moneybag-for-claudecode/data/briefings/LeiJiang_20260512.json | jq '._metadata'
```

---

### 修复 #5：宏观指标错误处理（优先级 3）

**位置**：`backend/infra/data_source/macro/indicators.py`

**现状**：所有函数都是：
```python
def get_china_cpi():
    try:
        import akshare
        # 调用 akshare...
    except Exception as e:
        # 没有任何日志或反馈
        return None
```

**修复方案**：
```python
def get_china_cpi():
    try:
        import akshare
        # 调用 akshare...
        result = akshare.macro_china_cpi()
        # ... 处理结果
        return result
    except ImportError:
        print("[MACRO] AKShare 未安装")
        return None
    except Exception as e:
        print(f"[MACRO] 获取 CPI 失败: {e}")
        return None

# 对所有宏观指标函数应用相同模式
```

**改动点**：
- [ ] 区分 ImportError 和其他异常
- [ ] 所有异常都添加日志
- [ ] 日志标准格式：`[MACRO] 获取 XXX 失败: {error}`

---

## 监控和可观测性增强（优先级 4）

### 新增：全局告警函数

**位置**：在 `backend/scripts/night_worker.py` 顶部添加

```python
def _push_alert(level: str, title: str, message: str):
    """
    发送系统告警
    level: "warning" / "error" / "critical"
    """
    try:
        from services.wxwork_push import is_configured, send_daily_report_to
        if not is_configured():
            return
        
        icon_map = {
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨",
        }
        icon = icon_map.get(level, "ℹ️")
        
        msg = f"{icon} {title}\n{message}"
        send_daily_report_to("LeiJiang", msg)
    except Exception as e:
        log(f"[ALERT] 告警发送失败: {e}")
```

### 新增：质量检查函数

```python
def _check_briefing_quality(briefings: dict) -> dict:
    """检查生成的简报质量"""
    issues = []
    
    for uid, briefing in briefings.items():
        # 检查是否全是 "暂无"
        content = briefing.lower()
        if content.count("暂无") > 3:
            issues.append(f"{uid}: 内容过于简略（过多'暂无'）")
        
        # 检查是否为空
        if len(briefing) < 50:
            issues.append(f"{uid}: 简报过短（{len(briefing)}字）")
    
    return {
        "quality_ok": len(issues) == 0,
        "issues": issues,
    }

# 在 step_morning_briefing() 返回前调用
quality_check = _check_briefing_quality(briefings)
if not quality_check["quality_ok"]:
    for issue in quality_check["issues"]:
        log(f"  ⚠️ {issue}")
    # 考虑发送告警给运维
```

---

## 完整修复清单（优先级排序）

| 优先级 | 问题 | 位置 | 工作量 | 紧急性 |
|--------|------|------|--------|--------|
| **P1** | 简报文件写入无保护 | night_worker.py:658 | 30 min | **严重** |
| **P1** | 推送读取无保护 | night_worker.py:668 | 30 min | **严重** |
| **P2** | Regime 错误传播 | regime_engine.py:86 | 20 min | 高 |
| **P2** | 缓存过期检查缺失 | steward.py:139 | 40 min | 高 |
| **P3** | 宏观指标错误处理 | macro/indicators.py | 30 min | 中 |
| **P3** | 模块超时中止 pipeline | pipeline_runner.py:117 | 20 min | 中 |
| **P4** | 全局告警机制 | night_worker.py:top | 30 min | 低 |
| **P4** | 质量检查函数 | night_worker.py:326 | 20 min | 低 |
| **P4** | 监控指标收集 | 全系统 | 60 min | 低 |
| **P4** | 详细日志记录 | 全系统 | 40 min | 低 |

**总预计**：
- P1（必做）：1 小时
- P2（重要）：1 小时
- P3（应做）：1.5 小时
- P4（优化）：2.5 小时
- **合计：6 小时**

