# 下一阶段：并发与持久化收尾

**背景：** 2026-08-30 修复 todos 无限膨胀（153,093 条 / 33.9MB）时，连带发现并根治了
用户数据的 **lost update（丢更新）**。已完成部分：`services/persistence.py` 新增
`user_write_lock()`（per-user 线程锁 + `fcntl.flock` 跨进程锁，10s 超时后放弃写入），
并覆盖全项目 **25 处** RMW 临界区（实测 8 线程并发从"只活 1 个"变为 8/8 全存活）。

本文件记录**刻意留到下一阶段**的 4 件事。重点写清"为什么"，判断依据比步骤更重要。

---

## 1. `save_user_data` 的乐观并发控制（version / ETag）

**这是唯一一处锁修不了的丢更新，优先级最高。**

`POST /api/user/save`（`api/user.py::save_user_data`）是**全量覆写**语义：客户端送完整
状态，服务端整体替换。已加 `user_write_lock`，但锁只解决了一半：

- **锁修好的**：字段是**条件覆写**（`if data.portfolio:` / `if data.ledger:`）。
  A 只送 portfolio、B 只送 ledger 时，原实现里 B 会基于自己 load 的旧快照覆写，
  吃掉 A 刚写的 portfolio。实测 1/2 → 2/2 已修复。
- **锁修不了的**：两端都送完整 `ledger` 时 **last-write-wins**。因为那个"读"发生在
  **浏览器端、请求到达服务端之前** —— 服务端锁无从介入，加任何锁都没用。

**已验证的风险场景（真实，不是理论）：** 钱袋子是 PWA，用户手机和电脑会同时开着。
两端各自加了一笔持仓/记账，后同步的那端会**静默覆盖**先同步的。用户看不到任何提示，
只会在某天发现"我明明记过的一笔不见了"。

**为什么必须前后端一起改：** 服务端单方面加版本校验只会让请求失败，用户体验更差。
必须配套前端的冲突处理（重新拉取 + 合并 / 让用户选择），否则就是把静默丢数据换成
静默报错。

**方向（不是照抄步骤）：** `load_user` 返回体带 `updatedAt` 或单调 `version`；
客户端 POST 时回传它；服务端在**锁内**比对，不一致则返回 409 + 服务端最新状态，
前端据此提示或自动合并。注意校验必须在锁内做，否则等于没做。

---

## 2. 规范：`async def` 端点中使用 `user_write_lock` 必须走 `asyncio.to_thread`

在事件循环里直接 `with user_write_lock(...)`，flock 阻塞会**冻住整个事件循环** ——
不是只卡当前请求，而是**所有用户的所有请求一起卡死**，最长 10 秒（超时时间）。
后果比它想保护的那条数据严重一个数量级：从"丢一条记账"变成"全站不可用"。

正确写法：把临界区抽成同步内部函数，再 `await asyncio.to_thread(它)`。
参考实现：`api/user.py::ocr_receipt` → `_persist_ocr_result`。

**当前状态：** 全项目只有 `ocr_receipt` 一个 `async def` 写端点
（`api/portfolio.py` 有 0 个 async 端点，`api/user.py` 只此 1 个），已按此处理。
规范已就近写在 `services/persistence.py::user_write_lock` 的 docstring 里
（**刻意不单独开文档** —— 新增 async 写端点的人第一眼看到的是锁的 docstring，
而不是某个规范文件）。

---

## 3. `scripts/migrate_phase3.py` 刻意**不**加锁 —— 不是遗漏

`migrate_user()`（72→98 行）是全项目唯一一处**未加锁的真实 RMW**，这是有意为之：

- 它是**一次性数据迁移脚本**，跑在停服务窗口，不在任何并发路径上
- 迁移语义本身就要求独占访问，靠流程（停服务）保证比靠锁更合适
- 加锁只会增加噪音，并让人误以为它可以在线上带服务运行

**若将来它变成常规运维脚本（例如被 cron 调用），必须补锁。** 判据是"是否可能与
uvicorn 或其他 cron 同时运行"。

另注：`services/persistence.py::_init_phase3_fields` 在静态扫描里会被标记为 RMW，
那是 **docstring 提到 `load_user()`/`save_user()` 造成的误命中**，函数本身不做 IO，
不需要加锁。

---

## 4. `req: dict = {}` 可变默认参数（`api/holdings.py`，已标 TODO 未改）

`set_financial_goal`（1575 行附近）和 `set_discipline_line`（1640 行附近）的签名是
`def xxx(req: dict = {})` —— **可变对象作默认参数**，Python 经典陷阱：默认值在
**函数定义时**创建并跨所有调用共享，一旦有代码就地修改 `req` 就会污染后续全部请求。

**为什么本次没改：** 改签名（`req: dict | None = None` 或改用 Pydantic model）
可能影响 FastAPI 的请求体解析行为，需要单独验证请求体是否仍能正确注入 ——
属于接口行为变更，不该和一个数据丢失修复混在一起上线。

**当前状态：** 两处函数上方已加 `# TODO(2026-08-30)` 注释说明隐患和不改的原因。
本文件内多处端点是同一写法，建议**统一整改并配一个请求体解析的回归测试**，
而不是零散地改。

> 顺带记录：这两个端点原本还有一个**自上线起每次必 500** 的 bug ——
> `save_user(uid, user)` 传了 2 个参数而 `save_user(data)` 只收 1 个，
> 导致「设定财务目标」和「设定纪律线」从未成功保存过一次。已在 2026-08-30 修复。
> 它能潜伏这么久是因为 `save_user` 是函数内 late import，静态检查扫不出来，
> 且这两个端点没有任何测试覆盖。**新增端点请至少配一个 happy-path 测试。**

---

## 5. AKShare 挂死治理（两位工程师交叉核实后的共识排期）

### 事实依据（生产实测，非推断）
```
2026-04-14 22:07  cache_warmer --after-close 卡死
                  STAT=Sl / futex_wait_queue_me / 19 个打开的 socket
2026-06-14        ak_call() 写成（daemon thread + join(timeout)），81 行
2026-08-30        进程仍在卡 —— 修复存在之后又空转了 77 天，无人发现
```
`ak_call()` 全项目 grep：1 个定义 + 2 行 docstring 示例，**零调用方**。

### 为什么「兜底」必须排在「接线」之前（P0 vs P1）

`ak_call()` 用 daemon thread + `join(timeout)` 实现超时 —— **超时只是放弃等待，
被卡住的请求线程不会死**（AKShare 内部的阻塞 I/O 不可中断），只在进程退出时回收。

于是在长驻进程里：
```
挂死一次 → 泄漏 1 个线程 → 永久占掉 1 个 anyio threadpool worker
→ 累积到 40 个（FastAPI 默认池大小）→ 全站 sync 端点停止服务
```

**关键连带后果**：本次加锁的 25 处写入端点**几乎全是 sync**
（`api/portfolio.py` 0 个 `async def`、`api/user.py` 仅 1 个）——
**线程池耗尽时，锁根本轮不到执行**。也就是说线程泄漏能把整个并发修复成果架空。

所以：**接线只降低概率，兜底才限制后果。** 生产上已存在卡了 137 天的实例，
说明「后果无上限」是当下就存在的风险，必须先把无上限变成有上限。

### P0：进程级兜底（改动小、无前置依赖）
- 长驻 cron（`cache_warmer` / `night_worker` 等）加**最大运行时长自杀**，
  或外部看门狗按 etime 阈值回收
- 顺序：**SIGTERM → 宽限期 → SIGKILL**。SIGKILL 会留孤儿 `.tmp`
  （因为 `atomic_write_json` 的 except 分支不执行），但**不会损坏数据**
  —— 原子写保证要么旧内容完整、要么新内容完整。孤儿 `.tmp` 由
  `housekeeping_cron.py` 兜（>1 天才清，绝不误删正在写入的）
- 兜底必须覆盖 `ak_call` **之外**的挂死路径 —— 只做接线覆盖不到

### P1：`ak_call()` 收口在 infra 层（不是逐点替换）
AKShare 真实调用点分布（用 `ak\.[a-z_0-9]*\(` 数出来的）：
```
infra/data_source/ 内   74 处 / 6 个文件   ← 在此收口即覆盖 74%
绕过 infra 层           26 处
  其中 api/fund_detail.py  12 处，且在 API 请求路径上（P0 级暴露）
```
在 infra 层收口的额外好处：**自动覆盖那个卡死的 cache_warmer**
——它不直连 AKShare，而是走 services → infra，所以只需改 6 个文件而非 100 个点。

⚠️ **核对缺口必须用 `ak\.[a-z_0-9]*\(` 真实调用来数，
不能用 `grep -l "import akshare"`** —— 后者会多出 3 个只 import 未使用的空壳文件，
让接手的人去补三个不存在的洞。（与「本模块不调用 ≠ 死代码」是镜像的两面：
**import 了不等于用了，没调用也不等于没人用**。）

## 6. 13 因子稀释 bug（`services/signal.py`）
8 个因子的「数据不可用」分支都是 `score=0` + 满权重 append，
实测**剔除 30.0 vs 给 0 分 27.0，稀释 10%**。
线上当前 0 个因子降级 → 属偶发问题；北向因**永久降级**已单独改成剔除权重。
统一整改会改变所有历史分数的可比性，需单独立项 + 回测验证，不能搭车。

## 7. `skippedFactors` 前端渲染
`pages/` 下目前**没有承载 13 维信号明细的视图**（`daily_signal`/`details`/`skippedFactors`
全无读取点），所以不是「没做」而是「没有载体」。若做：参照因子 IC 页已有的
「被排除因子」展示模式，**权重合计显示 90%、不要凑成 100%**
——凑成 100% 等于把「剔除了一个因子」这件事又藏起来。

## 8. `housekeeping_cron.py` 的可再生性白名单（阻塞挂 crontab）
DRY-RUN 发现它会删 `data/decision_logs/*.jsonl` 和 `data/audit/*.jsonl`
——**AI 决策记录与审计日志，属不可再生业务数据**，而 V8 复盘正要拿它做归因。
判据缺陷：只看文件名形态（含 `YYYY-MM-DD`）+ mtime，未区分可再生性。
修法：显式两类白名单（可再生=缓存/预计算/日志/临时；不可再生=决策/审计/行为/分析历史），
不可再生但需定期清理的 → **归档而非删除**。
**白名单落地并验证 `decision_logs`/`audit` 不再命中之后，才可挂 crontab。**

### 看门狗的两条实施约束（务必遵守）

**1. 必须 `SIGTERM` → 宽限期 5~10s → `SIGKILL`，不要直接 KILL。**
理由**不是**清垃圾（孤儿 `.tmp` 由 `housekeeping_cron.py` 兜住了），而是
`SIGTERM` 能走到 `finally`，让进程**正常收尾当前那一次写入**。
> 垃圾可以扫，但「这一轮缓存到底写完没有」只有进程自己知道。

`cache_warmer` 单次落盘是毫秒级，几秒宽限期足够。

**2. `_cache/` 里稳定存在 0~1 天的 `.tmp` 是预期行为，不是故障。**
`housekeeping_cron.py` 的 `--tmp-age-days` 默认 1 天，是为了**绝不误删正在写入的文件**。
若看门狗每天触发，运维看到 `_cache` 里常有 `.tmp` **不要当故障排查，更不要把阈值调小**
—— 调小会开始误删活跃写入。

## 9. 🔑 一条贯穿两条线的通用原理（本次最值得记住的）

> **原子写（`tempfile` + `fsync` + `os.replace`）代码本身是对的，但扛不住 `SIGKILL`
> —— 信号会跳过 `except` / `finally`，所以清理逻辑永远不执行。**

`services/persistence.py::atomic_write_json` 与 `scripts/cache_warmer.py` 的落盘用的是
**同一个模式**，本次清掉的 10 个共 33MB 孤儿 `.tmp` 与将来看门狗产生的缓存孤儿**同源**。
两条线只是触发源不同：

| 触发源 | 路径 |
|---|---|
| todos 涨到 49MB → 写入慢 → 进程被 KILL | 本次已修（幂等 + 上限） |
| 网络挂死 → 看门狗 KILL | 下一阶段（P0 看门狗） |

**同一个机制、两个入口** → `housekeeping_cron.py` 这道兜底对两边都有效。

**推论（重要）**：**「修好原子写」不等于「不会再有孤儿文件」。**
- 原子写防的是**数据损坏**（torn write）—— 保证要么旧内容完整、要么新内容完整
- 孤儿文件需要**独立的清理兜底** —— 因为信号路径下清理代码根本不执行

这两件事不能互相替代，也解释了为什么 `housekeeping_cron.py` 不是"可选的运维美化"，
而是原子写模式的**必要配套**。

⚠️ 一个反直觉点（差点导致误判）：`cache_warmer` 用
`tempfile.mkstemp(prefix=f".{name}.")`，孤儿文件名是**点开头**的
`.market_context.a1b2c3.tmp`。**shell glob 的 `*` 不匹配点开头文件**，
但 **pathlib 的 `rglob("*.tmp")` 会匹配** —— 已用 SIGKILL 孤儿的真实形状端到端实测确认
（3 个超龄孤儿全部命中、新鲜 `.tmp` 未命中、正式 `.json` 未命中）。
**这条覆盖是运气好而非当初设计时想到的**，将来若改用 shell 脚本清理必须显式加 `.*` 模式。
