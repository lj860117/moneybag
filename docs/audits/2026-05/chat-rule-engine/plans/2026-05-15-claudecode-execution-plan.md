# MoneyBag 4 周改造计划（Claude Code 执行版）

> 生成时间：2026-05-15
> 项目：~/WorkBuddy/moneybag-for-claudecode
> 用法：每个 Phase 把"对 Claude Code 说"那段直接粘进 Claude Code，让它执行；执行完按"验收"项核对。
> 不变式：始终遵守 CLAUDE.md 的 12 条不变式 + 改动结束产出"影响面清单"。

---

## 总览

| Phase | 主题 | 时长 | 风险 |
|------|------|------|------|
| P0-1 | 紧急止血：轮换 secret、`.env` 不入库、CORS 收口 | 1-2h | 高 |
| P0-2 | 加最小鉴权（HMAC token） | 半天 | 中 |
| P0-3 | Chat 规则首发（高频问题不打 LLM） | 半天 | 中 |
| P1-4 | 修 PWA 中文乱码 + `/api/api/` 双前缀 | 2h | 低 |
| P1-5 | 仓库根目录 30 份报告归档 | 1h | 低 |
| P1-6 | print → logging + ruff 门禁 | 半天 | 低 |
| P1-7 | api/ 层引入 mypy 渐进式类型 | 半天 | 中 |
| P2-8 | services/ 绞杀（recommend_engine + tushare_data） | 2-3 天 | 中高 |
| P2-9 | requirements pin + CI secrets 扫描 + rate limit | 半天 | 低 |
| P2-10 | 前端 ESM 化（最小可行） | 1 天 | 中 |

---

# Phase 0-1：紧急止血（**今晚就做**）

## 对 Claude Code 说

```
我们要做安全紧急止血。请按以下步骤执行：

【上下文】
- 项目：MoneyBag，FastAPI + 原生 JS SPA。
- 当前 backend/.env 明文存有 LLM_API_KEY、TUSHARE_TOKEN、WXWORK_SECRET、WXWORK_AES_KEY。
- backend/main.py 里 CORSMiddleware allow_origins=["*"] + allow_credentials=True。
- 严格遵守 CLAUDE.md 的 12 条不变式；改动结束输出"影响面清单"。

【任务】
1. 用 `git log --all -p -- backend/.env` 确认 .env 是否曾被提交，把结果原文贴给我（关键 commit 也告诉我）。
2. 检查 .gitignore 是否真覆盖了 backend/.env、data/、.workbuddy/，不全的补上。
3. 把 backend/.env.example 补全，包含所有当前 .env 里的 KEY，但 value 一律写占位符（如 sk-xxx-rotate-me）。
4. 在 backend/config.py 顶部加一段"启动时校验关键 secret 已配置且不是占位符"的代码：缺失时抛 RuntimeError，提示去改 .env。
5. 修改 backend/main.py 的 CORS：
   - 从环境变量 MB_ALLOWED_ORIGINS（逗号分隔）读 origins；
   - 默认 ["http://localhost:8000", "http://127.0.0.1:8000"]；
   - allow_credentials 改成 True 但 origins 不允许 ["*"]；
   - allow_methods/headers 也改成显式白名单（GET POST DELETE OPTIONS / Authorization Content-Type X-User-Token）。
6. 在仓库根新建 docs/security/SECRETS.md，写清楚：
   - 哪些 KEY 必须存在；
   - 部署平台（Railway/腾讯云）应通过 systemd EnvironmentFile / 平台 secret 注入；
   - 本地开发用 backend/.env 且文件权限必须 600；
   - "secret 永远不入 git"。
7. 在 CLAUDE.md 的 12 条不变式末尾追加第 13 条："任何 secret 不入工作目录、不入 git，统一从环境变量读"。

【不要做的事】
- 不要直接 git rm 或重写历史，只做诊断 + 防御加固，是否清理历史等我决定。
- 不要把真实 token 写进任何新文件。

【验收】
- 给我 `git log --all -p -- backend/.env` 的输出。
- 给我修改后的 main.py CORS 段、config.py 启动校验段、SECRETS.md。
- 列影响面清单：哪些文件被改、哪些路由的 CORS 行为变了。
```

## 执行后我自查
- [ ] 我自己手动轮换 LLM_API_KEY（DeepSeek 后台）、TUSHARE_TOKEN、WXWORK_SECRET、WXWORK_AES_KEY；
- [ ] 把新 token 只写进部署机的 EnvironmentFile / 平台 secret，不写回仓库；
- [ ] `chmod 600 backend/.env`；
- [ ] 如 git 历史里曾有 secret，决定是否走 `git filter-repo` 清理（先备份）。

---

# Phase 0-2：加最小鉴权（HMAC token）

## 对 Claude Code 说

```
我们要给 MoneyBag 加最小可用鉴权。

【上下文】
- 现状：/api/user/{user_id}、/api/decisions/* 等接口直接拿 path/body 里的 user_id 操作数据，零鉴权。
- 用户来源：邀请码制（已有）+ 企业微信 OAuth（routers/wxwork.py）。
- 数据落盘：data/users/SHA256(userId).json。
- 不引入数据库。

【任务】
1. 新建 backend/infra/auth/__init__.py 和 backend/infra/auth/token.py：
   - 用环境变量 MB_AUTH_SECRET（HMAC key），如果没设置则启动报错；
   - 提供 issue_token(user_id, ttl_days=30) -> str，格式 base64url(payload).hex(hmac)，payload 含 sub/exp/iat；
   - 提供 verify_token(token) -> dict|None，过期/签名错返回 None。
2. 新建 backend/api/_deps.py：
   - 提供 require_user(token: str = Header(alias="X-User-Token")) -> str，返回 user_id；
   - 失败抛 HTTPException 401。
3. 给"写接口"和"读个人数据"接口加 Depends(require_user)，并强制 token.sub == path/body 里的 user_id：
   - api/user.py: GET/PUT /api/user/{user_id}、DELETE /api/user/{user_id}、PUT /api/user/preference/{userId}、POST /api/feedback、POST /api/ocr/receipt
   - api/decisions.py: 所有 POST + GET /review/{user_id}
   - api/holdings.py: 所有写接口
   - api/portfolio.py: 所有写接口
   - api/family_profile.py: 所有写接口
   - api/balance_sheet.py: 所有写接口
   不动：/api/dashboard、/api/news、/api/macro/* 等公开行情类接口。
4. 邀请码激活流程（routers/profiles.py 或 api/user.py 里现有的）：激活成功时调 issue_token 返回给前端。
5. 前端 app.js 加：
   - 登录/激活成功后把 token 存 localStorage 的 mb_token；
   - 改一个全局 fetch 包装 apiFetch(path, init)：自动注入 Authorization 同站请求头 X-User-Token: <token>；
   - 401 时清除 token 并跳到 landing 页提示"请重新激活"。
6. 写测试 tests/test_auth_token.py：
   - 没 token 调 /api/user/<x> 应该 401；
   - token 有效但 sub != path user_id 应该 403；
   - token 过期应该 401；
   - 正常 token 200。

【约束】
- 全部走 backend/infra/auth/，业务层不直接 import hmac；
- 不破坏 wxwork OAuth 已有逻辑；
- 写接口加 audit_log（services/audit_log.py 已存在）。

【验收】
- 给我 token.py 的实现、_deps.py、修改后的 api/user.py 的关键 diff；
- pytest tests/test_auth_token.py 全过；
- 列影响面清单：哪些接口现在要求 token、哪些前端页面要改 fetch 调用。
```

## 执行后我自查
- [ ] 用 curl 试一下：`curl -X DELETE http://localhost:8000/api/user/qa_test_20260419` 应返回 401；
- [ ] 前端页面登录/激活后能正常拉数据；
- [ ] 看 services/audit_log.py 是否记录到鉴权失败事件。

---

# Phase 0-3：Chat 规则首发

## 对 Claude Code 说

```
我们要修 chat 路径的核心 bug：现在的 chat 注释说"先规则后 LLM"，实际永远先打 LLM（7-10s）。
完整背景在 FINAL_AUDIT_SUMMARY.md / DETAILED_CODE_TRACE.md。

【任务】
1. 在 api/shared_helpers.py 里：
   - 给 _rule_based_reply 增加返回结构 { "text": str, "confidence": float, "intent": str }；
   - 让现有的 15+ 模式（timing/take_profit/dca/sentiment/macro/news/technicals…）在命中时给 confidence=0.85，否则返回 None。
2. 在 api/chat.py 顶部定义：
   FAST_PATH_INTENTS = {"timing", "take_profit", "dca", "sentiment", "macro_summary"}
3. 修改 /api/chat 和 /api/chat/stream：
   - 先 classify_chat_intent → 拿 intent；
   - 若 intent in FAST_PATH_INTENTS：调用 _rule_based_reply：
     - 命中且 confidence>=0.7：直接返回（流式接口要按 SSE 逐字 yield，模拟打字）；
     - 不命中：fall through 到 LLM；
   - 其它 intent：直接走 LLM（保持现状）。
4. 在响应里加一个调试字段 `served_by: "rules"|"llm"`（流式接口用最后一帧 meta event）。
5. 加测试 tests/test_chat_fast_path.py：
   - 输入 "现在能进场吗" → served_by=rules，耗时 < 1s；
   - 输入 "我手上 600519 怎么看" → served_by=llm（复杂股票名超出规则）；
   - 输入 "止盈" → served_by=rules。

【约束】
- 不要新增 services/ 文件，逻辑只在 api/shared_helpers.py + api/chat.py；
- 命中规则也要把当天市场上下文注入回复（_build_market_context 已有）；
- 保留对 LLM 的 fallback：规则不命中时一定要打 LLM。

【验收】
- 给我修改后的 chat.py 关键 diff；
- pytest tests/test_chat_fast_path.py 全过；
- 用 time curl /api/chat 测一次"现在能进场吗"，应 < 1s；
- 列影响面清单。
```

## 执行后我自查
- [ ] 真人在前端聊一次，"该不该加仓 / 现在能买吗 / 该不该止盈"返回明显变快；
- [ ] 看后端日志，规则命中率是否 ≥ 30%。

---

# Phase 1-4：修 PWA 中文乱码 + `/api/api/` 双前缀

## 对 Claude Code 说

```
我们要修两个前端的小但显眼问题。

【任务 A：PWA 文案乱码】
- index.html、manifest.json、sw.js 中的中文都成了 "���"（不是占位符，是真实编码错误）。
- 需要用 UTF-8 重写这三个文件，替换为正确文案：
  - 应用名："钱袋子"
  - 副标题："家庭资产管理教练"
  - description："AI 做管家整理信息，人做 CFO 决策"
  - title："钱袋子 — 家庭资产管理教练"
- 不改任何功能、版本号、CACHE_NAME、JS 引用顺序，只动文案。
- 完成后用 `file -I index.html manifest.json sw.js` 确认 charset=utf-8。

【任务 B：/api/api/ 双前缀】
- 背景：app.js 的 API_BASE 在生产是 '/api'，部分 pages/*.js 又写了 API_BASE+'/api/...'，导致 /api/api/...。
- 详见 DOUBLE_PATH_BUG_REPORT.md。
- 步骤：
  1. 在 app.js 暴露 window.api(path)：
     ```js
     function api(path) {
       if (!path.startsWith('/')) path = '/' + path;
       if (path.startsWith('/api/') && API_BASE.endsWith('/api')) path = path.slice(4);
       return API_BASE + path;
     }
     window.api = api;
     ```
  2. 全局 grep `API_BASE+'/api/`、`API_BASE + '/api/`、`${API_BASE}/api/`，替换成 `api('/`。
  3. 在 pages/stocks.js、pages/chart.js（已知有 6 处 bug）和其它命中文件做替换。
  4. 改完跑 `grep -rn "API_BASE.*'/api/" pages/` 应该返回空。

【任务 C】
- sw.js 改 fetch 策略：
  - HTML（Accept 含 text/html）→ network-only；
  - 同源 *.js?v=* 资源 → stale-while-revalidate；
  - chart.js / lightweight-charts CDN → cache-first；
- 同时把 index.html 的 ?v= 和 sw.js 的 CACHE_NAME 都升一版（v8.0.4），并在 index.html 注释提醒"两处必须同步"。

【验收】
- file -I 三个文件应输出 utf-8；
- grep API_BASE 结果应不再出现 /api/api；
- 重新加载 PWA 看到正确中文 splash；
- 列影响面清单。
```

## 执行后我自查
- [ ] 卸载并重装 PWA（清缓存）后看图标和文案；
- [ ] 浏览器 DevTools → Network 确认请求路径不再是 `/api/api/`。

---

# Phase 1-5：仓库根目录 30 份报告归档

## 对 Claude Code 说

```
仓库根目录现在散落 30+ 份审计/分析 .md 和 .txt（AKSHARE_*、AUDIT_*、DEEPSEEK_*、MORNING_REPORT_*、WEEK1_*、START_HERE.txt、moneybag_audit_report.md…）。
还有一个 diagnose_and_fix_morning_report_bug.py 在根目录。

【任务】
1. 在 docs/audits/2026-05/ 下按主题建子目录：
   - akshare-fallback/
   - chat-rule-engine/
   - morning-report-bug/
   - deepseek-models/
   - weekly-summary/
2. 把对应文件 git mv（非 git tracked 的就普通 mv）进去。
3. 在 docs/audits/INDEX.md 写一份总目录，按"日期 + 主题 + 一句话摘要 + 文件链接"列出。
4. 把根目录的 diagnose_and_fix_morning_report_bug.py 移到 scripts/audits/。
5. 根目录最终只保留：README.md、CLAUDE.md、pyproject.toml、requirements*.txt、.gitignore、.importlinter、Procfile、railway.toml、index.html、app.js、styles.css、sw.js、manifest.json、icons/、logo-wxwork.png。
6. 顺手把 backend/data-backup-pre-m1/ 移到 backend/_archive/data-pre-m1/，并在 backend/_archive/README.md 写清楚是什么。
7. 不删除任何文件，只移动；frontend-patches/ 暂不动（先确认后再清）。

【验收】
- 给我 ls 根目录的输出；
- 给我 docs/audits/INDEX.md 的内容；
- 列影响面清单：哪些 .md 链接需要更新（grep 之前那些文件名在仓库里有没有被引用）。
```

## 执行后我自查
- [ ] CLAUDE.md / README.md 里有没有引用旧路径，更新了；
- [ ] 备份目录 README.md 写清"何时可删"。

---

# Phase 1-6：print → logging + ruff 门禁

## 对 Claude Code 说

```
backend 里有 443 处 print() 散落在 api/services/use_cases，要替换成统一 logging。

【任务】
1. 新建 backend/infra/logging.py：
   - 提供 get_logger(name) -> logging.Logger；
   - 默认 level=INFO，env MB_LOG_LEVEL 可覆盖；
   - 格式 "%(asctime)s [%(levelname)s] %(name)s :: %(message)s"；
   - 不引入 structlog，保持零依赖。
2. 全局替换：
   - `print(f"[CHAT] xxx")` → `logger.info("xxx")`；
   - `print(f"[CHAT] memory inject failed: {e}")` → `logger.warning("memory inject failed: %s", e)`；
   - 文件顶部加 `from infra.logging import get_logger; logger = get_logger(__name__)`。
3. 不动 scripts/ 和 tests/ 里的 print（脚本/测试可以留）。
4. 加 ruff 配置（pyproject.toml 加 [tool.ruff]），启用 T201（禁 print）：
   - 仅对 backend/api、backend/use_cases、backend/domain、backend/infra 启用；
   - backend/services/ 暂时排除（绞杀过程中允许）；
5. 跑 `ruff check backend/api backend/use_cases backend/domain backend/infra` 应 0 错误。

【验收】
- 给我新增的 infra/logging.py；
- 给我 ruff 配置 diff；
- 报告替换了多少处；
- 列影响面清单。
```

---

# Phase 1-7：api/ 层渐进式 mypy

## 对 Claude Code 说

```
现在 pyproject.toml 里 services.* 和 api.* 都是 ignore_errors=true，类型安全空洞。
我们先把 api/ 摘掉 ignore，渐进式启用。

【任务】
1. 修 pyproject.toml：
   - 移除 api.* 的 ignore_errors=true；
   - 给 api.* 单独配：
     ```
     disallow_untyped_defs = false
     check_untyped_defs = true
     warn_unused_ignores = false
     ```
   - 即"已有类型必须对，但允许暂时不写类型"。
2. 跑 `mypy backend/api`，把"轻松能修"的（缺 import、明显类型错）修掉。
3. 不要为了消错误强行加 # type: ignore[xxx]，确实改不了的列在 docs/typing-debt.md。
4. 给 chat.py、shared_helpers.py、user.py、decisions.py 这 4 个核心文件的所有公共函数加上完整类型签名（参数 + 返回值）。

【验收】
- mypy backend/api 通过（或剩余错误列在 typing-debt.md）；
- 4 个核心文件类型完整；
- 列影响面清单。
```

---

# Phase 2-8：services/ 绞杀（recommend_engine + tushare_data）

## 对 Claude Code 说

```
backend/services/ 还有 24K 行遗留代码。本周绞杀两个最大的：
- services/recommend_engine.py（890 行）
- services/tushare_data.py（840 行）

按 CLAUDE.md 的"绞杀者模式 + 4 层架构"做：
- 数据源类逻辑 → backend/infra/data_source/providers/
- 业务规则类逻辑 → backend/domain/services/ 或 backend/domain/rule_engine/
- 编排逻辑 → backend/use_cases/

【任务（先从 recommend_engine 开始）】
1. 读 services/recommend_engine.py，写一份 docs/refactor/recommend_engine_breakdown.md：
   - 列出所有 public 函数；
   - 每个函数标"属于哪一层"（infra / domain / use_cases）；
   - 列出谁调用它（grep 引用方）。
2. 把 docs 给我看一眼，等我说"go"再开始迁移。
3. 收到 go 之后：
   - 新增对应的 domain/use_cases/infra 文件；
   - 老的 services/recommend_engine.py 改成"薄壳子"，全部 re-export 新位置的函数（保持 import 兼容）；
   - 更新调用方，把 import 路径改新的；
   - 跑 lint-imports 应通过；
   - 跑 pytest tests/ -k recommend 应通过。
4. tushare_data.py 同样流程：先 breakdown 文档 → 等 go → 迁移 → 旧文件薄壳。

【约束】
- 不允许在迁移中重写功能或"顺手优化算法"；
- 单次迁移最多动 30 个文件 import；
- 全程不破坏现有 API 行为。

【验收】
- breakdown 文档清晰；
- 迁移后 services/recommend_engine.py < 50 行（只 re-export）；
- pytest 全过；
- 列影响面清单。
```

## 执行后我自查
- [ ] 真接口跑一遍冒烟（/api/recommend、/api/quant 等）；
- [ ] 一个月内观察是否有遗漏调用方报错。

---

# Phase 2-9：requirements pin + CI secrets 扫描 + rate limit

## 对 Claude Code 说

```
做生产硬化三件事。

【任务 A：依赖固定】
1. 用 pip-tools：
   - 给 requirements.txt 加 pyproject.toml 里的版本下限；
   - 生成 requirements.lock.txt（pip-compile 输出）；
   - 同样产 requirements-dev.lock.txt；
2. 在 README 部署章节注明"生产用 lock 安装"。

【任务 B：CI secrets 扫描】
1. 在 .github/workflows/ 加 secrets-scan.yml：
   - 跑 gitleaks 扫整库（include 工作目录中文件，不只 git diff）；
   - 在 PR 触发；
2. 加 .gitleaks.toml 白名单：
   - 允许 .env.example 里的 sk-xxx-rotate-me 占位符；

【任务 C：rate limit】
1. requirements.txt 加 slowapi；
2. backend/main.py 注册 slowapi limiter；
3. 给 /api/chat、/api/chat/stream、/api/ocr/receipt、/api/feedback 限：
   - per-IP 30/min；
   - per-user（拿 token 里的 sub）100/hour；
4. 限流错误统一返回 {"code":"MB-RATE-001","message":"请求太快，稍后再试"}；

【验收】
- pip install -r requirements.lock.txt 干净通过；
- gh actions 能跑 secrets-scan；
- curl 连发 40 次 /api/chat 触发 429 + MB-RATE-001；
- 列影响面清单。
```

---

# Phase 2-10：前端 ESM 化（最小可行）

## 对 Claude Code 说

```
当前 pages/*.js 12 个文件靠 <script> 顺序硬挂全局变量。我们做最小成本 ESM 化，不引入 webpack/vite。

【任务】
1. 把 app.js 拆成：
   - app/state.js   - 全局状态（currentPage、liveNavData、currentUser…）
   - app/router.js  - navigateTo / 历史栈
   - app/api.js     - apiFetch / api(path) 工具 / token 注入
   - app/ui.js      - 通用 UI 工具（toast、modal、loader）
   - app/main.js    - 入口（启动 + 注册 SW + 初始路由）
2. pages/*.js 全部改成 ESM：
   - 顶部 import { state, router, apiFetch, ui } from '../app/main.js';
   - 导出 export function render() { ... } export function teardown() { ... }
3. index.html 改成只加载一个入口：
   ```html
   <script type="module" src="/app/main.js?v=8.1.0"></script>
   ```
   不再单独列 12 个 pages script。
4. main.js 用动态 import 懒加载页面：
   ```js
   const PAGES = {
     landing: () => import('../pages/landing.js'),
     portfolio: () => import('../pages/portfolio.js'),
     ...
   };
   ```
5. 同步更新 sw.js：把 STATIC_ASSETS 改成只 precache app/main.js + app/api.js + 几个核心 css；pages/*.js 走 stale-while-revalidate。
6. 验证：
   - 打开 PWA，5 个核心页面（landing/portfolio/insight/history/chat）切换正常；
   - DevTools Network 确认按需加载；
   - 离线模式下已访问过的页面仍能渲染。

【约束】
- 不要改业务逻辑、UI、API 调用，只改"模块组织方式"；
- 一次性改完后跑一次冒烟，再交给我；
- 保留 app.js 作为兼容入口，里面用 <script> 强制重定向到 module 入口（用户可能缓存了旧版本）。

【验收】
- index.html ≤ 30 行；
- 手测 5 个页面 + 离线模式；
- 列影响面清单。
```

---

# 附：Claude Code 通用 Prompt 模板

每次给 Claude Code 任务都用这个"三段式"，不要省：

```
【上下文】
- 项目：MoneyBag，FastAPI + 原生 JS SPA，根目录 ~/WorkBuddy/moneybag-for-claudecode
- 必读：CLAUDE.md（12 条不变式）、docs/design/00-ANCHOR.md
- 本次相关文件：<列出 3-5 个相关文件>

【任务】
<分步骤，越具体越好>

【约束】
- 不要改无关文件；
- 改完一个文件立即验证；
- 严禁 _v2 / _new / _ext / _helper 命名；
- 严禁裸 print，走 infra/logging；
- 严禁 _cache = {} 模块级缓存，走 infra/cache。

【验收】
- 指定测试 / curl / grep 命令；
- 必须输出"影响面清单"：
  🔴 必须同步评估：<文件>
  🟡 建议评估：<文件>
  🟢 确认无影响：<文件>
- 必须输出"下一步建议"。
```

---

# 执行节奏建议

- **第 1 天晚上**：跑 P0-1（紧急止血），其他不要碰；轮换 secret。
- **第 2-3 天**：P0-2（鉴权）+ P0-3（chat 规则首发），这两个跑完产品就稳了一大截。
- **第 4-5 天**：P1-4（PWA 乱码）+ P1-5（仓库整理），属于"看起来变干净"环节。
- **第 2 周**：P1-6 + P1-7（工程基线）。
- **第 3 周**：P2-8（绞杀），节奏慢点没关系。
- **第 4 周**：P2-9 + P2-10（生产硬化 + 前端 ESM）。

每个 Phase 跑完，让 Claude Code 给"影响面清单"，你回我执行结果，我帮你判断是否进下一个。
