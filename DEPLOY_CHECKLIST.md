# ✅ 部署清单 - "管家一句话"问题修复

**任务**：修复首页"管家一句话"显示原始技术调试信息  
**状态**：✅ 代码修复完成，已提交到 Git  
**Commit**：`5fb6ada`  
**日期**：2026-05-11  

---

## 📋 修复完成确认

### 代码修改
- ✅ `backend/services/regime_engine.py` - 第 72 行修改
- ✅ `backend/services/pipeline_runner.py` - 第 397、405 行修改
- ✅ `backend/services/steward.py` - 第 164、276-285、323-326 行修改

### 测试验证
- ✅ Python 语法检查通过
- ✅ 正则表达式验证通过
- ✅ 逻辑审查通过
- ✅ 向后兼容性确认

### 文档完成
- ✅ `BUTLER_FIXES_APPLIED.md` - 详细修复报告
- ✅ `WORK_SUMMARY.md` - 工作总结
- ✅ `BUTLER_FIX_COMPLETION.txt` - 完成确认
- ✅ `DEPLOY_CHECKLIST.md` - 本清单

---

## 🚀 部署前检查

### Pre-Deployment 检查清单

- [ ] 确认 Git 提交已推送到主分支
  ```bash
  git log --oneline -3
  # 应该看到: 5fb6ada Fix butler one-liner display showing raw debug info
  ```

- [ ] 确认代码无编译错误
  ```bash
  python3 -m py_compile backend/services/regime_engine.py
  python3 -m py_compile backend/services/pipeline_runner.py
  python3 -m py_compile backend/services/steward.py
  ```

- [ ] 验证修改内容
  ```bash
  git show HEAD --stat
  # 应该显示 4 files changed
  ```

### 环境准备

- [ ] 确保后端服务能够访问
  ```bash
  curl http://localhost:5000/api/health
  ```

- [ ] 确认前端服务运行
  ```bash
  curl http://localhost:3000
  ```

- [ ] 验证日志文件可写
  ```bash
  ls -la /var/log/moneybag/
  touch /var/log/moneybag/test.log
  ```

---

## 📦 部署步骤

### Step 1️⃣: 拉取最新代码
```bash
git pull origin main
git log --oneline -1
# 确认显示: 5fb6ada Fix butler one-liner display showing raw debug info
```

**预期结果**：✅ 代码已更新到最新版本

---

### Step 2️⃣: 停止现有服务（如需）
```bash
# 方式 A: systemctl
sudo systemctl stop moneybag-backend

# 方式 B: Docker
docker-compose down

# 方式 C: 手动
pkill -f "python.*steward"
```

**预期结果**：✅ 后端服务已停止

---

### Step 3️⃣: 启动服务
```bash
# 方式 A: systemctl
sudo systemctl start moneybag-backend

# 方式 B: Docker
docker-compose up -d

# 方式 C: 手动启动
cd /path/to/moneybag
python -m services.api &
```

**预期结果**：✅ 服务已启动，可访问

---

### Step 4️⃣: 验证服务状态
```bash
# 检查服务运行状态
sudo systemctl status moneybag-backend

# 或检查进程
ps aux | grep steward

# 或检查端口
netstat -tlnp | grep 5000
```

**预期结果**：✅ 服务处于 Running 状态

---

### Step 5️⃣: 清除浏览器缓存
```
浏览器中执行：

Chrome/Edge:
  Ctrl+Shift+Del (Windows/Linux)
  Cmd+Shift+Del (Mac)
  
Firefox:
  Ctrl+Shift+Del (Windows/Linux)
  Cmd+Shift+Del (Mac)

Safari:
  Cmd+Option+E
```

或使用强制刷新：
```
Chrome/Edge/Firefox: Ctrl+Shift+R (Windows/Linux) 或 Cmd+Shift+R (Mac)
Safari: Cmd+Option+R
```

**预期结果**：✅ 缓存已清除

---

### Step 6️⃣: 验证修复效果
```
1. 打开浏览器，访问首页：http://localhost:3000

2. 查看"管家一句话"卡片

3. 确认显示内容：
   ✅ 不含 "severity="
   ✅ 不含 "→强制"
   ✅ 不含 "原判定:"
   ✅ 仅显示用户友好文本
   
4. 示例：正确显示应如下所示
   📈 市场趋势向上，风控正常
   ⚠️ 地缘风险评估（中东），切换谨慎模式
```

**预期结果**：✅ 显示干净的文本，无技术细节

---

### Step 7️⃣: 检查日志
```bash
# 查看最近日志
tail -f /var/log/moneybag/steward.log

# 搜索错误
grep -i "error\|exception\|traceback" /var/log/moneybag/steward.log

# 搜索修复相关日志
grep -i "sanitize\|regime" /var/log/moneybag/steward.log
```

**预期结果**：✅ 日志无错误，服务正常运行

---

### Step 8️⃣: 功能测试
```bash
# 测试 API 端点
curl http://localhost:5000/api/steward/briefing?userId=test_user

# 验证返回内容
# 应该包含 regime_description 字段，但不含 severity=
```

**预期结果**：✅ API 返回正常数据，已清理的 regime_description

---

## 🎯 验证清单

部署后请逐一验证：

| 项目 | 验证方法 | 状态 |
|------|--------|------|
| 代码已部署 | `git log -1` 显示正确 commit | ☐ |
| 服务已启动 | `systemctl status` 显示 running | ☐ |
| 首页可访问 | http://localhost:3000 打开正常 | ☐ |
| 卡片无技术文本 | 首页看不到 `severity=` 等 | ☐ |
| 日志无错误 | 日志中无 ERROR、EXCEPTION | ☐ |
| API 返回正常 | /api/steward/briefing 响应正确 | ☐ |
| 其他功能正常 | 其他页面功能未受影响 | ☐ |

---

## 🆘 故障排查

### 问题 1: 服务无法启动

**症状**：`systemctl status` 显示 failed

**解决**：
```bash
# 查看详细错误
systemctl status moneybag-backend -l

# 查看日志
journalctl -u moneybag-backend -n 50 -e

# 检查依赖
pip install -r requirements.txt

# 重新启动
systemctl restart moneybag-backend
```

### 问题 2: 首页还是显示旧数据

**症状**：修改后首页仍显示 `severity=` 等技术文本

**解决**：
```bash
# 清除所有缓存
rm -rf /tmp/moneybag_cache/*
rm -rf ~/.cache/moneybag/*

# 清除浏览器缓存（见 Step 5）

# 硬启动服务
systemctl stop moneybag-backend
sleep 2
systemctl start moneybag-backend

# 强制刷新前端
Ctrl+Shift+R 或 Cmd+Shift+R
```

### 问题 3: API 返回错误

**症状**：curl 命令返回 500 错误

**解决**：
```bash
# 查看详细错误日志
tail -f /var/log/moneybag/error.log

# 检查 Python 语法
python3 -m py_compile backend/services/steward.py

# 检查正则表达式
python3 << 'PYEOF'
import re
desc = "⚠️ 地缘风险覆盖（中东 severity=5）原判定: trending_bull → 强制 high_vol_bear"
desc = re.sub(r'severity=\d+', '', desc)
print("Test 1:", desc)  # 应该没有 severity=5
PYEOF
```

### 问题 4: 性能下降

**症状**：页面加载变慢

**解决**：
```bash
# 这不应该发生，因为修改只是文本替换
# 检查正则执行时间
time python3 -c "
import re
for i in range(10000):
    re.sub(r'severity=\d+', '', 'test severity=5 test')
"
# 应该在毫秒级
```

---

## ✅ 部署完成检查

全部完成后，请确认：

- [ ] 所有部署步骤已执行
- [ ] 所有验证项目已通过
- [ ] 故障排查部分无问题
- [ ] 首页显示效果符合预期
- [ ] 日志无异常错误
- [ ] 其他功能工作正常

---

## 📞 回滚计划（如需）

如果部署后发现问题，可以快速回滚：

```bash
# 方式 1: Git 回滚
git revert 5fb6ada
git push origin main
systemctl restart moneybag-backend

# 方式 2: 快速 git reset
git reset --hard HEAD~1
git push origin main --force
systemctl restart moneybag-backend

# 清除缓存并验证
rm -rf /tmp/moneybag_cache/*
curl http://localhost:5000/api/steward/briefing?userId=test
```

**回滚时间**：< 2 分钟

---

## 📝 完成后续

部署完成后：

1. **更新部署日志**
   ```bash
   echo "部署时间: $(date)" >> DEPLOYMENT_LOG.txt
   echo "Commit: 5fb6ada" >> DEPLOYMENT_LOG.txt
   echo "状态: 完成" >> DEPLOYMENT_LOG.txt
   ```

2. **通知相关人员**
   - 项目经理
   - 产品团队
   - 测试团队

3. **监控一段时间**
   - 第一天：每小时检查一次
   - 第二天：每天检查一次
   - 之后：定期检查

---

## 📚 相关文档

部署过程中可参考：
- `BUTLER_FIXES_APPLIED.md` - 修复详情
- `WORK_SUMMARY.md` - 工作总结
- `BUTLER_FIX_GUIDE.md` - 技术指南

---

**部署清单版本**：1.0  
**最后更新**：2026-05-11  
**准备好部署**：✅ 是

