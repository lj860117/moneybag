# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 项目定位

**钱袋子（MoneyBag）** — 家庭资产管理教练。AI 做管家整理信息，人做 CFO 决策。数据采集用 API，指标计算用 Python，综合判断用规则引擎，AI 只负责把结果翻译成人话。DeepSeek 不在白天柜台算命，在凌晨仓库批量翻译。

---

## 常用命令

### 启动后端

```bash
# 开发模式（在仓库根目录执行）
cd backend && uvicorn main:app --reload --port 8000

# 或直接
uvicorn backend.main:app --reload --port 8000
```

### 安装依赖

```bash
pip install -r requirements.txt          # 生产依赖
pip install -r requirements-dev.txt      # 开发+测试依赖
```

### 跑测试

```bash
# 跑所有测试（需要后端在 8000 端口运行）
pytest tests/

# 跑单个测试文件
pytest tests/test_skeleton_m1.py -v

# 跑单个测试函数
pytest tests/test_red_team.py::test_xxx -v

# 并行跑（快）
pytest tests/ -n auto

# 跳过需要 LLM 的慢测试
pytest tests/ -m "not llm_heavy"

# 针对线上服务跑
MB_TEST_HOST=http://150.158.47.189:8000 pytest tests/
```

### 类型检查 & 架构门禁

```bash
# mypy 类型检查（仅对新架构层 strict）
mypy backend/domain backend/infra/cache backend/use_cases

# import-linter 架构依赖检查（四层单向依赖）
lint-imports

# main.py 行数检查（超 200 行 CI 报错）
python scripts/lint_main_py.py
```

### 健康检查

```bash
python backend/scripts/api_health_check.py
python backend/scripts/datasource_health_check.py
```

---

## 架构概览

### 整体结构

```
backend/           # FastAPI 后端
├── main.py        # <200 行，只做 FastAPI init + CORS + include_router
├── config.py      # 所有阈值/权重/TTL/模型配置（单一数据源，禁止魔法数字）
├── api/           # 路由层：按业务域拆，每文件 <400 行，只做路由+参数校验
├── use_cases/     # 用例层：编排 services，一个用户动作/一个凌晨任务 = 一个文件
├── domain/        # 领域层
│   ├── models/    # 业务对象（Pydantic/dataclass）
│   ├── services/  # 单一职责领域服务（服务之间禁止互相 import）
│   ├── rule_engine/ # 规则引擎（配比矩阵/阈值/清单规则）
│   └── protocols/ # 接口定义（Protocol）
├── infra/         # 基础设施层
│   ├── cache/     # 统一缓存（MemoryCache，禁止模块级 _cache = {}）
│   ├── store/     # 持久化（FileStore，禁止裸读 DATA_DIR）
│   ├── llm/       # LLM 网关（所有 LLM 调用必须走这里）
│   ├── data_source/ # 数据源适配（market/fundamental/macro/alt 四分法）
│   │   └── providers/ # Tushare > AKShare > baostock 三级降级链
│   └── knowledge/ # RAG 知识库（ChromaDB）
├── services/      # 遗留服务层（绞杀者模式，逐步迁入 domain/）
└── scripts/       # 运维脚本（定时任务/健康检查/数据预热）

app.js             # 前端 SPA 核心层（全局状态/路由/API客户端，~500行目标）
pages/             # 前端页面模块（M7 拆分后，每个 render*() 独立文件）
styles.css         # 全局样式（不拆，不改框架）
index.html         # 单页入口，按顺序加载 app.js 后加载所有 pages/*.js
sw.js              # Service Worker（PWA 离线缓存）
tests/             # 集成测试（httpx 直连后端，不做 mock）
```

### 四层单向依赖（强制约束，import-linter 门禁）

```
api/ → use_cases/ → domain/ → infra/
```

- 反向依赖禁止
- `domain/services` 之间禁止互相 import，只走 `domain/protocols`
- `infra/` 只能依赖 `domain/protocols`，不能依赖 `domain/services` 或 `domain/rule_engine`
- 新架构层（domain/、infra/cache、infra/store、infra/llm、use_cases/）有 mypy strict 检查；遗留层（services/、api/）暂时忽略类型错误

### 前端架构（原生 JS SPA，无框架无构建）

- 单页应用，路由通过 `navigateTo(page)` 函数切换，每个页面对应一个 `render*()` 函数
- 全局状态（`currentPage`、`liveNavData` 等）在 `app.js` 声明，所有 `pages/*.js` 直接访问全局作用域，无 import/export
- V6 patches（`frontend-patches/v6/`）已拆入对应 `pages/*.js`，`build.js` 已废弃
- 图表用 Chart.js 4（CDN 加载），无其他前端依赖
- 数据存储：用户数据存 localStorage，持仓等核心数据存服务端 JSON 文件

### 数据层

- 用户数据路径：`data/users/SHA256(userId).json`（邀请码制，SHA256 路径隔离）
- 不使用数据库，全部 JSON 文件（`infra/store/file_store.py`）
- 数据源优先级：Tushare（规范/稳定）> AKShare（另类数据）> baostock（纯 A 股日线/免费）

### LLM 集成

- 主模型：DeepSeek V3（`deepseek-chat`），推理模型：DeepSeek R1（`deepseek-reasoner`）
- 所有 LLM 调用必须走 `infra/llm/gateway.py`，禁止业务代码直调 httpx
- 关键约束：**AI 不预测证券价格**，不输出 action / position_pct / 未来收益率

---

## 12 条不变式（每次改动必须遵守）

1. AI 不预测证券价格 — 不输出 action / position_pct / 未来收益率
2. 所有 LLM 调用走 `infra/llm/gateway`
3. 所有缓存走 `infra/cache` — 禁止模块级 `_cache = {}`
4. 所有文件 IO 走 `infra/store` — 禁止裸读 `DATA_DIR`
5. 所有外部数据源走 `infra/data_source` — 不允许 domain/api 层 import tushare
6. 禁止 `_v2` / `_new` / `_ext` / `_helper` 命名
7. `main.py` < 200 行
8. `domain/services` 之间禁止互相 import
9. 单向依赖：`api/` → `use_cases/` → `domain/` → `infra/`
10. 新建跨模块接口必须先写 Protocol（方案 C）
11. 每次改动必须产出"影响面清单"（见下方规范）
12. 后端 API 做了必须验证前端有调用（铁律 #18）

---

## 每次改动结束必须产出

```
✅ 本次改动：<改了什么 + 改在哪>

📢 影响面清单：
🔴 必须同步评估：<文件列表 + 原因>
🟡 建议评估：<文件列表>
🟢 确认无影响：<文件列表>

❓ 需要你决定：<悬而未决的问题>
🗺️ 下一步建议：<下次会话贴哪些文件>
```

---

## 开发约定

- 注释用**中文**
- Git commit 格式：`[home] 类型: 简短描述`
- 禁止模糊文件命名：`记录1`、`想法`、`杂项`、`temp` 一律禁止
- 改完一个文件立即验证，不攒改动
- 前端版本号更新时，同步更新 `index.html` 的 `?v=` 查询参数和 `sw.js` 的 `CACHE_NAME`

---

## 设计文档速查（按任务找必读文件）

- 每次任务开始先读 `docs/design/00-ANCHOR.md`
- 新增功能/改架构 → `docs/design/12-framework-refactor.md`
- 前端拆分/M7 页面 → `docs/design/m7-plus/09-frontend-refactor.md`
- M7+ Batch 开发 → `docs/design/m7-plus/` 对应文件
- 规则引擎/阈值 → `docs/design/03-rule-engine.md`
- AI 调用规范 → `docs/design/04-ai-interface.md`
- 治理/CI 规则 → `docs/design/13-governance.md`

---

## 测试说明

- 测试通过 httpx 直连运行中的后端（不 mock），需要后端先启动
- `conftest.py` 提供 `client` fixture（自动探活，服务未起则 `pytest.skip`）
- 测试账号：`qa_test_20260419`（环境变量 `MB_TEST_USER` 可覆盖）
- `llm_heavy` 标记的测试会调 LLM，耗时且消耗 token，CI 可跳过
- `online_only` 标记的测试仅在线上 host 有意义

---

## 部署

- 生产：腾讯云 `150.158.47.189:8000`，systemd + uvicorn，服务路径 `/opt/moneybag/`
- 前端静态文件由后端 FastAPI 一体服务（`app.mount("/static", ...)` + 兜底路由）
- 备选：Railway（`railway.toml` + `Procfile` 已配置）
- 关键环境变量：`DATA_DIR`（持久化目录）、`LLM_API_KEY`、`LLM_API_URL`、`LLM_MODEL`
