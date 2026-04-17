---
name: moneybag-dev
version: 1.0.0
description: >
  Load when: (1) developing or modifying MoneyBag (钱袋子) backend/frontend code,
  (2) deploying to Tencent Cloud server 150.158.47.189,
  (3) working with DeepSeek V3/R1 LLM integration,
  (4) modifying app.js or styles.css frontend files,
  (5) any task touching MoneyBag source files.
  NOT needed for: pure design document discussion, financial data queries, non-MoneyBag projects.
  Prerequisite: hermanlei-conventions should be loaded first for general coding standards.
globs:
  - "**/moneybag/**"
  - "**/钱袋子/**"
---

# MoneyBag (钱袋子) 开发规范

## 项目概况

- **定位**：家庭资产管理工具（2 用户：厉害了哥 + 部落格里）
- **技术栈**：FastAPI + 原生 JS SPA + JSON 文件存储 + DeepSeek V3/R1
- **GitHub**：https://github.com/lj860117/moneybag.git
- **服务器**：腾讯云 150.158.47.189:8000 / Ubuntu 22.04 / systemd / uvicorn ×1
- **设计文档**：`docs/MoneyBag-全景设计文档.md`（7000+ 行，按 Part 行号裁剪读取）

## 🔴 MoneyBag 铁律

### M1: 前端改动必须用脚本打补丁

app.js 是压缩的单行格式（~3000 行），**禁止用 replace_in_file 插入多行代码**。

正确做法：
1. `scp` 拉服务器上能跑的版本到 `/tmp/app_base.js`
2. 用 Node.js 脚本精确替换/追加
3. 每个补丁后 `node --check` 验证
4. 检查花括号平衡
5. 部署后让用户刷新验证

```javascript
// 补丁脚本模板
const fs = require('fs');
let code = fs.readFileSync('/tmp/app_base.js', 'utf8');
// Patch: 精确匹配旧代码 → 替换为新代码
const old = "完整的旧代码片段";
const new_ = "完整的新代码片段";
if(!code.includes(old)){console.log('❌ target not found');process.exit(1)}
code = code.replace(old, new_);
fs.writeFileSync('/tmp/app_patched.js', code);
```

来源：2026-04-17 白屏 P0 事故（replace 截断 doAddStock → 白屏）

### M2: FastAPI 路由顺序

具名路径必须在通配路径之前：
```python
# ✅ 正确
@app.get("/api/user/preference")  # 先定义具名
@app.get("/api/user/{user_id}")   # 后定义通配

# ❌ 错误
@app.get("/api/user/{user_id}")   # 通配在前会拦截 preference
@app.get("/api/user/preference")  # 永远匹配不到
```

### M3: 部署前必须备份

```bash
sudo cp /opt/moneybag/app.js /opt/moneybag/app.js.bak.$(date +%Y%m%d)
sudo cp /opt/moneybag/backend/main.py /opt/moneybag/backend/main.py.bak.$(date +%Y%m%d)
```

### M4: JSON 写入必须用原子写

所有 `json.dump` 必须走 `persistence.py` 的 `atomic_write_json()`，不能直接 `open().write()`。

### M5: LLM 返回必须用 safe_parse_json

DeepSeek R1 偶尔在 JSON 前后加解释文字，直接 `json.loads()` 会炸。

### M6: 每个后端改动必须验证前后端同步

**新增/修改后端 API 后，按顺序做 3 件事**：

1. **后端验证**：`curl` 调 API 确认返回正确
2. **前端调用检查**：`grep` 前端代码确认有调用这个 API
   ```bash
   grep "新增的API路径" /opt/moneybag/app.js || echo "❌ 前端未接入"
   ```
3. **端到端验证**：在浏览器中实际触发这个功能，确认数据从后端→前端完整展示

**如果后端做了前端没接** → 必须告知用户，标注到设计文档的待补清单中。
来源：铁律 #18 + 2026-04-17 Phase 0（13 个后端 API 验证通过，但 6 项前端未接入）

### M7: 部署后必须让用户手机验证

代码部署到服务器后，**不能只靠 curl 验证就说"完成了"**。必须：
1. 先自己用 curl 验证所有 API
2. 然后让用户在手机上刷新看效果
3. 用户说"OK"才算部署成功

**如果用户说白屏/报错** → 立即回滚到备份版本，再排查。
来源：2026-04-17 白屏事故（curl 全通过但手机白屏）

### M8: 硬编码 vs 动态判断原则

MoneyBag 是 AI 分析应用，核心价值在于**分析逻辑可调可进化**。判断标准：

**✅ 可以硬编码（2 用户场景，改了要重启也能接受）**：
- 用户列表（FAMILY_MEMBERS = ["LeiJiang", "BuLuoGeLi"]）
- 邀请码
- 服务器地址、API Key 环境变量名
- CSS 颜色变量

**⚠️ 应该放 config.py 或用户 JSON（不重启可调、或 V8 复盘需要回溯）**：
- 风控阈值（止损/止盈/再平衡偏离度）→ 已在 USER_OVERRIDES + config.py ✅
- Token 预算（日/月上限、预警阈值）→ 已在 config.py ✅
- 因子权重（宏观/技术/资金面权重配比）→ 在 config.py ✅
- 用户偏好（模式/推送/盯盘）→ 在用户 JSON ✅

**❌ 绝不能硬编码（AI 分析的核心，必须动态 + 可追溯）**：
- **Prompt 模板**：必须放独立文件或数据库，不能写死在代码里。V8 复盘需要回溯"用的哪个版本 prompt 得出的结论"
- **模型选择（R1/V3）**：必须按任务类型动态选择，不能写死。凌晨分析用 R1，盘中快速判断用 V3
- **数据源路由**：AKShare/Tushare 降级策略必须可配置，不能 if/else 写死
- **买卖建议阈值**：如"PE > 30 算高估"这种数字，必须可配置且 V8 能修改
- **行业/个股评分权重**：V8 自主复盘的核心就是调这些权重

**简单判断法**：
> 问自己：**V8 AI 复盘时需要改这个值吗？**
> - 需要 → 必须动态（配置文件/数据库/AI 可写入）
> - 不需要 → 可以硬编码

**当前代码中需要 V6 修正的硬编码**：
- `temperature: 0.7` 在 main.py 中出现 4 次 → 应提取到 config.py
- 智能定投倍率表（`< 20% → 1.5x` 等）→ 应提取到 config.py，V8 可调
- Prompt 文本（system_prompt 构建）→ V6 应拆到 `prompts/` 目录

### M9: 每个新功能必须过多账号检查

MoneyBag 有 2 个用户，所有功能**从设计到验证**都要考虑多账号。新增任何 API / 前端页面 / 缓存 / 推送时，逐项检查：

**后端 4 问**：
1. **API 有 userId 参数吗？** → 没有就是全局数据（宏观/行情），有才是用户私有数据
2. **数据读写隔离了吗？** → 用户 JSON 文件必须按 SHA256(userId) 隔离，不能读到别人的持仓/记忆/偏好
3. **缓存按谁的 key？** → 用户私有数据的缓存 key 必须带 userId；全局数据（宏观/指数）共享缓存
4. **推送发给谁？** → 盯盘预警只推给持仓所有者，不推给另一个人；早安简报按各自偏好生成

**前端 4 问**：
5. **localStorage key 带 userId 了吗？** → 偏好/模式/主题 的 key 必须是 `moneybag_{功能}_{userId}`，否则切用户会串数据
6. **切用户时清了旧数据吗？** → 如果 A 用户的持仓还显示在页面上，B 用户登录后会看到 A 的数据
7. **API 调用传了 userId 吗？** → `getProfileId()` 必须传给每个 API，不能漏
8. **家庭汇总 vs 个人视图分清了吗？** → household API 返回全家数据，个人 API 只返回自己的

**验证方法**：每个新功能做完后，**分别用两个 userId 调一次 API**，确认返回不同数据：
```bash
# 验证模板
curl "http://localhost:8000/api/新功能?userId=LeiJiang" | python3 -m json.tool
curl "http://localhost:8000/api/新功能?userId=BuLuoGeLi" | python3 -m json.tool
# 两次结果必须不同（除非是全局数据）
```

**当前已知的多账号问题**：
- `_alert_cooldown` 字典的 key 已带 userId ✅
- `decision_log` 按 userId 过滤 ✅
- 前端 localStorage 的 `moneybag_ui_mode` 没带 userId ⚠️（两人共用一个手机时会串）
- 聊天记忆 `_loadChatHistory` 需确认是否按 userId 隔离 ⚠️

### M10: 新增数据源/因子必须注册到分析链路

**核心原则**：任何新增的数据源或因子，如果 AI 分析时"不知道它存在"，就等于白加。

**新增数据源时必须做 3 件事**：

1. **注册到数据源清单**（让系统知道它存在）
   ```python
   # config.py — DATA_SOURCES 注册表
   DATA_SOURCES = {
       "akshare_fund_nav":  {"name": "基金净值", "source": "akshare", "refresh": "daily", "critical": False},
       "tushare_daily":     {"name": "A股日线", "source": "tushare", "refresh": "daily", "critical": True},
       "tushare_report_rc": {"name": "盈利预测", "source": "tushare", "refresh": "weekly", "critical": True},
       # 新增数据源在这里注册 ↓
   }
   ```

2. **接入到 DecisionContextBuilder**（让 AI 分析时能拿到这个数据）
   ```python
   # V6: DecisionContextBuilder.build() 应该自动收集所有注册的数据源
   # 而不是手动一个个写 if/else
   def build(self, user_id):
       ctx = {}
       for key, meta in DATA_SOURCES.items():
           data = self._fetch(key)
           if data:
               ctx[key] = {"data": data, "meta": meta}
           else:
               ctx[key] = {"data": None, "meta": meta, "status": "missing"}
       return ctx
   ```

3. **写进 Prompt 模板的数据清单**（让 LLM 知道这次分析有哪些数据可用）
   ```
   ## 本次分析可用数据
   ✅ A股日线（2026-04-17）
   ✅ 基金净值（2026-04-17）
   ✅ 盈利预测（3家机构）
   ❌ 北向资金（数据源异常，本次不可用）
   
   请基于以上可用数据进行分析，对缺失数据标注"数据不足"。
   ```

**新增因子/权重时必须做 2 件事**：

4. **注册到因子清单**（V8 复盘可追溯"当时用了哪些因子"）
   ```python
   FACTOR_REGISTRY = {
       "valuation_pe": {"weight": 0.15, "source": "tushare_daily", "version": "1.0"},
       "northbound_flow": {"weight": 0.10, "source": "akshare", "version": "1.0"},
       # 新增因子在这里注册 ↓
   }
   ```

5. **确保 V8 复盘能读到这个因子的历史值**
   - 因子值必须随决策日志一起存档
   - V8 回溯时能看到"当时这个因子是多少 → AI 给了什么建议 → 结果对不对"

**判断法**：

> 新加了一个数据源/因子后，问自己 3 个问题：
> 1. **AI 凌晨分析时能自动拿到它吗？** → 不能 = 白加
> 2. **Prompt 里会提到它吗？** → 不提 = AI 不知道它存在
> 3. **V8 复盘时能回溯它的历史值吗？** → 不能 = 无法验证这个因子有没有用

**当前代码的问题**：
- 数据源分散在各个 service 文件里，没有统一注册表 → V6 建 DATA_SOURCES
- Prompt 是手动拼接的，新加数据源不会自动出现在 prompt 里 → V6 建 Prompt 模板引擎
- 因子权重硬编码在 config.py → V7 建 FACTOR_REGISTRY + V8 可调

## 部署流程

```bash
# 1. 本地：上传文件
scp backend/main.py ubuntu@150.158.47.189:/tmp/moneybag_deploy/
scp app.js ubuntu@150.158.47.189:/tmp/moneybag_deploy/

# 2. 服务器：备份 + 覆盖 + 重启
ssh ubuntu@150.158.47.189
sudo cp /opt/moneybag/backend/main.py /opt/moneybag/backend/main.py.bak
sudo cp /tmp/moneybag_deploy/main.py /opt/moneybag/backend/
sudo cp /tmp/moneybag_deploy/app.js /opt/moneybag/
sudo systemctl restart moneybag

# 3. 验证
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

**注意**：
- SSH 用户名是 `ubuntu`（不是 root）
- 前端是静态文件，不需要重启 uvicorn（但可能需要改 sw.js 的 CACHE_NAME 强制刷缓存）
- 后端改动必须 `systemctl restart moneybag`

## 用户体系

| userId | 昵称 | 模式 | 风险偏好 |
|--------|------|------|---------|
| LeiJiang | 厉害了哥 | Pro | growth |
| BuLuoGeLi | 部落格里 | Simple | balanced |

- 认证：邀请码制 + SHA256(userId) 路径隔离
- 数据文件：`data/users/{sha256_prefix}.json`

## Token 预算

- 日上限 ¥3，月上限 ¥30
- 70% 预警，90% 降级
- DeepSeek V3（轻量）/ R1（重型推理）
- chat 路由目前直接用 httpx，没走 LLMGateway 记账（V6 修）

## 设计文档导航

读文档时按行号裁剪，不要全塞进上下文：

| Part | 内容 | 行号范围 |
|------|------|----------|
| 全局 | 实施总表+数据源+AI排班+工程底座 | 行 32-1196 |
| Part 1 | Phase 0 前端整合 | 行 1197-2870 |
| Part 2 | V6 七大模块 | 行 2871-4081 |
| Part 3 | V6.5 盈利预测 | 行 4082-4963 |
| Part 4 | V7 推荐+决策 | 行 4964-5410 |
| Part 5 | V8 AI复盘 | 行 5411-6304 |
| Part 7 | V10+ 远期 | 行 6305-6596 |
| Part 6 | V9 模拟交易 | 行 6597-7044 |

## Phase 0 完成状态（2026-04-17）

**后端 ✅ 全部完成**（版本 6.0.0-phase0）

**前端 ⚠️ 6 项待 V6 补**：
- [ ] 空仓首页改造
- [ ] 资讯页 deepimpact / riskassess tab
- [ ] 持仓页 AI 分析按钮
- [ ] 信号页 AI 解读
- [ ] 首页入场时机卡片
- [ ] 家庭汇总展示

建议 V6 先格式化 app.js（prettier）或拆模块再做前端接入。

## Skill 路由表

做 MoneyBag 开发时，按以下路由主动加载对应 Skill：

| 触发场景 | 加载 Skill | 用途 |
|---------|-----------|------|
| **任何 MoneyBag 编码** | `hermanlei-conventions` | 通用铁律+编码规范 |
| **任何 MoneyBag 编码** | `moneybag-dev`（本 Skill） | MoneyBag 专属铁律 M1-M10 |
| **V6 重构 Prompt 模板** | `prompt-engineering-expert` | Prompt 优化、结构化输出设计、few-shot 模板 |
| **V6 凌晨宏观数据采集** | `macro-monitor` | 宏观数据源采集流程（Trading Economics/FRED/国家统计局） |
| **后端 async/路由/中间件** | `fastapi-async-patterns` | FastAPI 异步模式、shield、后台任务、错误处理 |
| **前端 PWA/离线/SW/推送** | `pwa-development` | Service Worker、缓存策略、离线、添加到桌面、推送通知 |
| **查金融数据** | `neodata-financial-search`（系统插件） | 自然语言查 A 股/港股/基金/宏观/外汇 |
| **查金融数据（补充）** | `finance-data-retrieval`（系统插件） | 209 个结构化 API 精确查询 |
| **前端 UI/UX 改动** | `frontend-design` | 配色/动画/排版/响应式 |
| **代码重构/简化** | `code-simplifier` | 代码可读性优化 |
| **深度调研（地缘/行业/个股）** | `deep-research` | 结构化调研：大纲→并行搜索→生成报告 |
| **分析经验沉淀** | `llm-wiki` | V8 复盘知识库：把历史分析经验增量编入 Wiki |
| **多 Agent 协作** | `agent-team-orchestration` | V9+ 多 Agent 编排：角色定义、任务流转、质量门禁 |
| **GitHub PR/Issue** | `github` | gh CLI 操作 |

### 各版本重点 Skill

```
V6（地缘/原油/北向/行业）：
  必加载：hermanlei-conventions + moneybag-dev
  按需：macro-monitor（宏观数据采集）
       prompt-engineering-expert（重构 prompt 模板）
       fastapi-async-patterns（后端 async 重构、shield、错误处理）
       pwa-development（6 项前端接入 + SW 缓存更新）
       frontend-design（UI/UX 改动）
       deep-research（地缘/原油/行业深度调研）

V6.5（盈利预测/估值）：
  必加载：hermanlei-conventions + moneybag-dev
  按需：finance-data-retrieval（Tushare report_rc 接入）
       prompt-engineering-expert（盈利预测 prompt）
       deep-research（个股/行业深度调研）

V7（推荐/DCF/买卖决策）：
  必加载：hermanlei-conventions + moneybag-dev
  按需：prompt-engineering-expert（决策 prompt + 结构化输出 schema）
       fastapi-async-patterns（决策引擎 async 流程）

V8（AI 自主复盘）：
  必加载：hermanlei-conventions + moneybag-dev
  按需：prompt-engineering-expert（归因分析 prompt）
       llm-wiki（把复盘经验增量编入知识库，越用越聪明）

V9（模拟交易 + 自主学习）：
  必加载：hermanlei-conventions + moneybag-dev
  按需：agent-team-orchestration（多 Agent 编排：分析Agent/交易Agent/风控Agent）
       llm-wiki（模拟交易经验沉淀）
       prompt-engineering-expert（策略进化 prompt）

V10+（远期）：
  按需：agent-team-orchestration（完整多 Agent 投研团队）
```
