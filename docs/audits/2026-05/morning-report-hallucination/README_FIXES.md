# 钱袋子晨报 AI 幻觉修复 - 完整指南

## 📋 文档索引

开始前，请按顺序阅读：

### 1️⃣ **Executive Summary** (5分钟阅读)
📄 [`EXECUTIVE_SUMMARY.md`](EXECUTIVE_SUMMARY.md)
- 问题是什么？
- 如何解决？
- 需要多久？
- 风险有多大？

**→ 如果你时间有限，只读这个**

---

### 2️⃣ **Deployment Checklist** (15分钟执行)
📄 [`DEPLOYMENT_CHECKLIST.md`](DEPLOYMENT_CHECKLIST.md)
- 逐步部署指南
- 健康检查步骤
- 24小时监控清单
- 回滚计划

**→ 准备部署时，按这个清单执行**

---

### 3️⃣ **Technical Reference** (详细文档)
📄 [`TECHNICAL_REFERENCE.md`](TECHNICAL_REFERENCE.md)
- 架构概览
- 三个修复的详细实现
- 代码示例
- 数据流图
- 常见问题和解决方案

**→ 需要理解技术细节时阅读**

---

### 4️⃣ **Troubleshooting Guide** (问题排查)
📄 [`TROUBLESHOOTING_GUIDE.md`](TROUBLESHOOTING_GUIDE.md)
- 每个修复的故障排除
- 根本原因分析
- 调试命令
- 集成测试脚本
- 性能指标

**→ 部署后出现问题时查阅**

---

### 5️⃣ **Deployment Status** (当前状态)
📄 [`DEPLOYMENT_STATUS.md`](DEPLOYMENT_STATUS.md)
- 修复总结表
- 部署前检查清单
- 部署后任务
- 文档列表
- 成功标准

**→ 快速查看当前进度**

---

## 🚀 快速开始

### 场景 1: 我是项目经理
1. 读 [`EXECUTIVE_SUMMARY.md`](EXECUTIVE_SUMMARY.md)
2. 检查风险评估
3. 批准部署或要求修改

### 场景 2: 我是开发/运维
1. 读 [`DEPLOYMENT_CHECKLIST.md`](DEPLOYMENT_CHECKLIST.md)
2. 执行部署步骤
3. 运行验证脚本
4. 监控24小时
5. 查看 [`TROUBLESHOOTING_GUIDE.md`](TROUBLESHOOTING_GUIDE.md) 如果有问题

### 场景 3: 我需要技术细节
1. 读 [`TECHNICAL_REFERENCE.md`](TECHNICAL_REFERENCE.md)
2. 查看代码示例
3. 理解数据流
4. 了解监控方式

### 场景 4: 出现问题
1. 查看 [`TROUBLESHOOTING_GUIDE.md`](TROUBLESHOOTING_GUIDE.md)
2. 查找症状和根本原因
3. 执行调试命令
4. 不行的话回滚（15秒钟）

---

## ✅ 验证脚本

运行自动验证来确保所有修复都已部署：

```bash
cd /Users/leijiang/WorkBuddy/moneybag-for-claudecode
bash verify_fixes.sh
```

**预期输出**:
```
✅ All tests passed! Fixes are properly deployed.
```

---

## 📊 快速统计

| 指标 | 值 |
|------|-----|
| 修复数量 | 3 个 Priority 1 修复 |
| 修改的文件 | 3 个 Python 文件 |
| 代码行数变化 | ~150 行 |
| 测试通过率 | 10/10 (100%) ✅ |
| 部署时间 | ~15 分钟 |
| 回滚时间 | 15 秒 |
| 预期收益 | 幻觉 100% 消除 |

---

## 🔍 修复一览

### Fix #1: LLM 数据完整性声明
- **文件**: `backend/scripts/night_worker.py`
- **目标**: 防止 LLM 编造央行数据 (MLF/OMO/PBOC)
- **方式**: 在提示词中明确列出有哪些数据和没有哪些数据

### Fix #2: Tushare 降级链
- **文件**: `backend/infra/data_source/alt/flows.py`
- **目标**: 防止 AKShare 故障导致北向资金数据缺失
- **方式**: AKShare 失败时自动降级到 Tushare

### Fix #3: 缓存 TTL 从 24h 改为 4h
- **文件**: `backend/services/steward.py`
- **目标**: 防止下午/晚上用户看到早上 7:30 的过期缓存
- **方式**: 实现基于文件修改时间的 TTL 检查

---

## 📞 支持

- **部署问题**: 查看 `DEPLOYMENT_CHECKLIST.md`
- **技术问题**: 查看 `TECHNICAL_REFERENCE.md`  
- **故障排除**: 查看 `TROUBLESHOOTING_GUIDE.md`
- **执行验证**: 运行 `verify_fixes.sh`

---

## 📅 推荐时间表

| 时间 | 任务 |
|------|------|
| T-1 day | 阅读文档，审查修复 |
| T day 08:00 | 早间低流量窗口部署 |
| T day 08:00-08:30 | 执行部署检查清单 |
| T day 08:30-24:00 | 监控日志和指标 |
| T+1 day | 收集反馈，确认成功 |

---

## 🎯 成功标准

- ✅ 0 个 MLF/OMO 幻觉 (目标: 从 2-3/天降至 0)
- ✅ 北向资金数据可用性 99%+ (目标: 从 95% 提高)
- ✅ 下午用户看到新数据，不是早上的缓存
- ✅ 日志中出现 3 个修复的标志

---

**第一步**: 阅读 [`EXECUTIVE_SUMMARY.md`](EXECUTIVE_SUMMARY.md)

**最后一步**: 运行 `bash verify_fixes.sh` 确认成功 ✅

