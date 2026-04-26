# MoneyBag 重构进度追踪

> 由 Claude Code 每次会话结束时更新。
> 格式：日期 + 完成的任务 + 当前阶段 + 阻塞项

---

## 当前阶段
M1 W1 — 四层骨架搭建 ✅ 已完成

## 已完成
- [x] 2026-04-25: 四层目录树（api/ use_cases/ domain/ infra/）
- [x] 2026-04-25: 4 个核心 Protocol（Cache/Store/LLM/DataSource）
- [x] 2026-04-25: 3 个 infra 最小实现（cache/store/llm.gateway）
- [x] 2026-04-25: 28 个 smoke test 全绿
- [x] 2026-04-25: Git commit + tag m1-w1-skeleton
- [x] 2026-04-25: REFACTOR_STATUS.md 创建

## 进行中
- [ ] M1 W2 — 拆 main.py（4044 行 → <150 行）
  - [x] 分析路由依赖（199 路由，按 35 组前缀分类，P1/P2/P3 三级）
  - [ ] 第一批拆分：61 个独立路由 → 10 个 Router 文件
  - [ ] 第二批拆分：P2 中等耦合路由（提取公共辅助模块后拆）
  - [ ] 第三批拆分：P3 高耦合路由（chat/analyze/ocr/dashboard）

## 阻塞项
- 无

## 下次会话计划
- 执行第一批拆分：创建 10 个 api/*.py Router 文件
- 拆 factors / macro / global / policy / market-factors / alt-data / quant / broker / analysis / scenario（共 61 路由）
- 跑 route_snapshot.py 对比验证

---

## 历史记录

### 2026-04-25
**会话 1**（M1 W1）
- 任务：搭四层骨架 + 4 Protocol + 3 infra
- 产出：23 文件，1049 行新增
- 状态：✅ 完成
- 影响面：零改动现有文件

### 2026-04-26
**会话 2**（M1 W2 准备）
- 任务：分析 main.py 199 个路由依赖关系
- 产出：完整路由依赖分析报告
  - 199 路由按 35 组前缀分类
  - 每路由标注依赖模块、全局变量、跨路由调用
  - P1 独立路由 120+、P2 中等耦合 50+、P3 高耦合 7 个
  - 第一批拆分目标：10 个 Router 文件，61 路由，main.py 立减 ~500 行
  - 发现 4 类不变式违反（直调 httpx ×3、散装缓存 ×3、akshare 越界 ×2、内嵌逻辑 ×5+）
- 状态：✅ 完成（纯分析，零改动代码）
- 影响面：无（仅更新 PROGRESS.md）
