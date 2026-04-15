# MoneyBag 项目长期记忆

## Tushare 积分状态
- **当前积分**：5000（2026-04-15 升级）
- **解锁接口**：`report_rc`（券商盈利预测）、全量高级接口
- **年费**：约 ¥500/年

## 项目版本规划
- **V6**：地缘政治 + 原油 + 北向修复 + 行业轮动 + 分析历史（8-12天）
- **V6.5**：盈利预测 + 估值定价 + 业务敞口（9-10天）
- **V7**：个股/基金推荐 + DCF估值 + 买卖决策输出（6-8天）
- **总工时**：24-30天
- **微信推送时间**：08:30（开盘前30分钟）

## 确认的技术栈（2026-04-16 扫描确认）
- **后端**：FastAPI 0.110+ / Python 3.11 / Pydantic 2
- **前端**：原生 JS SPA（app.js 309KB）+ Chart.js 4（无框架）
- **数据存储**：JSON 文件（data/users/SHA256.json）
- **缓存**：Python dict 内存缓存
- **AI**：DeepSeek V3（deepseek-chat）+ R1（deepseek-reasoner），通过 llm_gateway.py
- **数据源**：AKShare + Tushare
- **推送**：企业微信（wxwork_push.py）
- **部署**：腾讯云 150.158.47.189:8000 / Ubuntu 22.04 / systemd / uvicorn ×2
- **前端部署**：GitHub Pages（Actions 自动部署）
- **GitHub**：https://github.com/lj860117/moneybag.git
- **服务器代码路径**：/opt/moneybag/
- **后端入口**：backend/main.py（124KB / 3262行）
- **认证**：邀请码制 + SHA256(userId) 路径隔离（无 JWT）

## 关键设计决策
- 采用"大调整"方案，补齐研报核心模块
- Simple/Pro 双模式展示（小白看结论，专业看数据）
- 地缘政治作为贯穿宏观→行业→个股的核心变量
- **V6 Phase 0**：3 天整合前端（2026-04-16 决定）
  - 接入 7 个遗漏 API（P0×4 + P1×3），P2×3 延后到 V6
  - 修复 3 个记忆 BUG
  - 建立 Simple/Pro 模式切换框架
  - **Phase 0 不动底层**：继续原生 JS、JSON 文件、SHA256 隔离、内存缓存
  - 只激活 portfolio_optimizer.py，其余 4 个孤岛模块延后

## 领导反馈要点（2026-04-15）
- 盈利预测是研报重中之重
- 需要：一致预期、估值定价、业务敞口
- 地缘/外贸是核心预测变量，不是独立模块

## 开发工作流（每次编程任务开始前必做，不可跳过）

### 第零步：启动检查
1. 读 `SESSION-STATE.md` → `MEMORY.md` → 今日日志 → 昨日日志
2. 读 `rules-quickref.md`（铁律速查，20 条铁律）
3. 读 `startup-checklist.md`（完整启动流程）
4. 加载对应 Skill（`hermanlei-conventions` 等）

### 第一步：读设计
5. 读 `MoneyBag-全景设计文档.md` 对应 Part（Phase 0 / V6 / V6.5）
6. 读对应的踩坑台账 `moneybag-known-issues.md`

### 第二步：读代码
7. 读实际代码（`main.py`、`app.js`、相关 services/）
8. 对比设计 vs 代码，列出 gap

### 第三步：确认
9. 如果设计有模糊处（Prompt 模板、API 返回格式），先调用实际接口确认
10. 向用户确认改动范围，再动手

### 第四步：编程 + 验证
11. 编程实现，每完成一个任务运行健康检查脚本验证
12. 更新工作日志（memory/YYYY-MM-DD.md）
