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

绝大多数测试是**纯 pytest 单元测试**（不需要后端运行）。只有 `online_only` /
`MB_TEST_HOST` 相关用例才需要后端在跑。

```bash
cd backend

# 跑全部测试（推荐写法：不要设 DATA_DIR，见下方「数据隔离」）
python3 -m pytest tests/ -q

# 跑单个文件 / 单个用例
python3 -m pytest tests/test_fund_signal_render_dca.py -v
python3 -m pytest tests/test_fund_signal_combo_caliber.py::test_xxx -v

# 跳过需要 LLM 的慢测试
python3 -m pytest tests/ -m "not llm_heavy" -q

# 针对线上服务跑（需要后端在 8000 端口运行）
MB_TEST_HOST=http://150.158.47.189:8000 python3 -m pytest tests/ -q
```

#### 数据隔离（机制强制，不是人肉纪律）

`backend/tests/conftest.py` 在**模块顶层**（早于任何 `test_*.py` 被 import）执行：

1. 若未设 `DATA_DIR` → 自动 `tempfile.mkdtemp(prefix="moneybag_pytest_data_")` 并写入环境变量
2. `pytest_sessionfinish` 在整个会话结束后 `shutil.rmtree` 清理
3. autouse fixture `_clear_secret_env_pollution` 清空 `TUSHARE_TOKEN` 等 13 个密钥类环境变量

⚠️ **不要手动设 `DATA_DIR` 指向生产路径** —— 显式设置会绕过兜底，测试就会读写真实数据。
（2026-09-01 事故：`test_phase3_services.py` 未隔离，13 个用例真实写入生产 `data/users/`。）

#### 本地 vs 服务器：哪里才是权威

本地缺 `akshare` / `tushare` / `pandas` / `httpx` 等依赖，全量跑会有约 140 条
`ModuleNotFoundError` / `PermissionError` 失败，**与改动无关**。判断回归与否请：

```bash
# 服务器才是权威（557 passed / 0 failed @ v9.9.7）
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189
cd /opt/moneybag/backend && ../venv/bin/python -m pytest tests/ -q
```

本地要和基线对比时，用 `git archive` 导出干净基线到临时目录同法跑，**不要用 `git stash`**
（会打断正在编辑的工作区）：

```bash
git archive <baseline-sha> | tar -x -C /tmp/mb_baseline
cd /tmp/mb_baseline/backend && python3 -m pytest tests/ -q
```

#### 沙箱环境已知坑

沙箱 shim 会把「对已存在目录调用 `mkdir(exist_ok=True)`」误判为越权，报
`PermissionError: EEXIST`。每次跑测试都要用**全新**临时目录：

```bash
DD=$(mktemp -d); TD=$(mktemp -d); DATA_DIR=$DD TMPDIR=$TD python3 -m pytest tests/ -q
```

（这是沙箱假象，服务器上不会出现；复用上次的目录会再次触发。）

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
tests/             # 测试（主体为纯 pytest 单测；少量集成用例 httpx 直连后端不 mock）
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

- **主体是纯 pytest 单元测试**（557 条 @ v9.9.7），**不需要后端运行**。
  只有少量集成用例才通过 httpx 直连后端（不 mock）
- `conftest.py` 提供 `client` fixture（自动探活，服务未起则 `pytest.skip`）
- 测试账号：`qa_test_20260419`（环境变量 `MB_TEST_USER` 可覆盖）
- `llm_heavy` 标记的测试会调 LLM，耗时且消耗 token，CI 可跳过
- `online_only` 标记的测试仅在线上 host 有意义
- 数据隔离机制见「跑测试 → 数据隔离」——**不要手动把 `DATA_DIR` 指向生产路径**
- ⚠️ `test_user` 是**代码中在用的测试 user id**（`app.js` 黑名单 + 几十个测试用例）。
  服务器上若出现 `data/test_user` 这类残留目录，是历史测试未隔离时写入的数据垃圾，
  可删；但删前先确认当前代码没有依赖它

---

## 部署

- 生产：腾讯云 `150.158.47.189:8000`，systemd + uvicorn，服务路径 `/opt/moneybag/`
- 前端静态文件由后端 FastAPI 一体服务（`app.mount("/static", ...)` + 兜底路由）
- 备选：Railway（`railway.toml` + `Procfile` 已配置）
- 关键环境变量：`DATA_DIR`（持久化目录）、`LLM_API_KEY`、`LLM_API_URL`、`LLM_MODEL`

---

## ⚠️ 代码同步铁律（2026-08-09 教训：曾积压53 个文件的本地/git/服务器三方漂移）

**根因**：`/opt/moneybag` 服务器上很长一段时间没有版本控制，靠 SSH 直接改代码 + 手动 cp 备份维护，任何改动都"隐身"，直到手动 diff 才被发现。已修复：服务器 `/opt/moneybag` 现在自己也是一个 git 仓库（`git log` 可查），本地仓库配置了 `server` remote 可直接 `git fetch server` 拉取服务器当前状态对比。

**任何 AI 或人类在这个项目上工作，改代码必须走这个顺序，不能跳步：**

1. 本地改代码 → 语法检查（`python -m py_compile` / `ast.parse`）
2. `git add + git commit`（本地commit，说明改了什么）
3. `git push origin main`（推到 GitHub，这是"权威版本"落脚点）
4. 部署到服务器：先给要改的文件在服务器上打时间戳备份（`cp -p file file.bak_$(date +%Y%m%d_%H%M%S)`）→ `rsync` 覆盖 → 服务器端语法/import 检查 → `sudo systemctl restart moneybag` → 功能验证（`curl /api/models` 等）
5. **在服务器自己的 git 仓库里也 `git add -A && git commit`**——这一步最容易被遗漏。忘了的话，服务器 `git status` 会一直显示一堆 `M`，看起来像"又漂移了"，其实只是"部署了但没在服务器本地落地 commit"
6. 定期（大改动后，或每周）跑一次巡检：`cd ~/WorkBuddy/moneybag-for-claudecode && git fetch server && bash scripts/check-drift.sh`，几秒钟就能看出本地/服务器是否一致

**绝对禁止**：
- ❌ SSH 到服务器直接改代码"救急"，改完不同步回本地仓库——这是过去漂移的唯一根因
- ❌ 部署后忘记在服务器 git 里 commit
- ❌ 看到 diff 就无脑用一方覆盖另一方——漂移方向不固定（有时本地领先，有时服务器上有本地没有的独有逻辑），**必须先 diff 判断方向再决定，覆盖前一定先备份**

**如果必须紧急热改服务器**（比如深夜故障来不及走完整流程）：改完立刻在服务器 git 里 commit（哪怕写"emergency hotfix, TODO sync back"），第二天第一件事 `git fetch server` 把这个diff 拉回本地看一遍，决定要不要合并回主仓库。

详细排查方法见本地技能 `~/.workbuddy/skills/moneybag-server-drift-debug/SKILL.md`（含`check-drift.sh` 用法、常见坑）。
