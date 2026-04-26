# Claude Code 启动指令 — M1 W4：CI 门禁 + providers 实现

> 复制以下内容，粘贴到新的 Claude Code 会话中执行。
> 通用模板：先读 ANCHOR → 读进度 → 执行任务 → 更新 PROGRESS.md

---

## 第一步：先读 ANCHOR（永远不变）

```
先读 docs/design/00-ANCHOR.md，确认你理解了 12 条不变式
```

---

## 第二步：读取进度上下文

```
docs/PROGRESS.md
docs/REFACTOR_STATUS.md
docs/design/12-framework-refactor.md
docs/design/M1-W2-api-migration.md
docs/design/ 目录下所有与 M1 相关的设计文档
```

读完后用一句话告诉我：**当前阶段、已完成项、下一步计划**。

---

## 第三步：M1 W4 任务清单（按优先级执行）

### 任务 A：import-linter + mypy CI（方案 C 落地）

1. **检查当前 .importlinter 配置**
   - 读取 `.importlinter`，确认 4 个 contract 是否仍全过
   - 如有新增 api/*.py 导致 contract 破坏，修复或补充豁免

2. **配置 mypy strict**
   - 在 `pyproject.toml`（或新建 `mypy.ini`）配置 mypy strict 模式
   - 初始范围：`domain/` + `infra/` + `api/` 新拆出的模块
   - `services/` 老模块先加入 `--ignore-missing-imports` 豁免，逐步收紧
   - 配置项参考：
     ```toml
     [tool.mypy]
     strict = true
     ignore_missing_imports = true
     exclude = ["services/legacy_.*"]
     ```

3. **更新 CI 流水线**
   - 读取 `.github/workflows/ci.yml`
   - 在现有 `import-linter` job 后新增 `mypy` job
   - 确保失败时阻塞 merge

### 任务 B：main.py 行数上限 linter

1. **创建自定义 linter 脚本**
   - 路径：`scripts/lint_main_py.py`
   - 功能：统计 `backend/main.py` 行数，>200 行则 exit 1 + 打印错误
   - 同时检查 `backend/api/*.py` 单行数 >500 则 warning（不阻塞）

2. **集成到 CI**
   - 在 `.github/workflows/ci.yml` 新增 `main-py-size` job
   - 运行 `python scripts/lint_main_py.py`

### 任务 C：infra/data_source/providers/ 实现（如有时间）

1. **读取现有 facade 和 fallback**
   - `infra/data_source/__init__.py`：当前导出 `get_stock_news`、`search_funds`
   - `infra/data_source/fallback.py`：三级降级链占位

2. **创建 Provider 适配器**
   - `infra/data_source/providers/tushare_provider.py`：Tushare API 适配
   - `infra/data_source/providers/akshare_provider.py`：AKShare 适配
   - `infra/data_source/providers/baostock_provider.py`：Baostock 适配
   - 每个 Provider 实现 `DataSourceProtocol` 接口（先 stub，逐步填充）

3. **实现三级降级链**
   - 主源 → 备选源 → 本地缓存 → 空值/异常
   - 在 `fallback.py` 中实现 `FallbackDataSource` 包装器

---

## 第四步：输出格式

请用以下格式输出结果：

```markdown
## M1 W4 执行报告

### 已完成
- [x] 任务 A：mypy strict 配置 + CI 集成
- [x] 任务 B：main.py 行数 linter
- [ ] 任务 C：providers/ 实现（部分/全部）

### 关键变更
| 文件 | 变更 | 说明 |
|------|------|------|
| pyproject.toml | +12 行 | mypy strict 配置 |
| .github/workflows/ci.yml | +25 行 | mypy + main-py-size jobs |
| scripts/lint_main_py.py | 新建 | 200 行红线检查 |
| ... | ... | ... |

### 验证结果
- import-linter：X/4 contract 通过
- mypy：X errors（新模块 / 遗留模块）
- main.py 行数：XXX 行（<200 ✅）
- 骨架测试：X/X 通过

### 阻塞项
- 无 / XXX（说明）

### 下次计划
- XXX
```

---

## 第五步：会话结束前必须执行（进度追踪）

**更新 `docs/PROGRESS.md`**：

1. 在「已完成」追加今日任务
2. 更新「当前阶段」「进行中」「阻塞项」
3. 追加到「历史记录」，格式参考现有模板

**更新 `docs/REFACTOR_STATUS.md`**：
- 更新「当前阶段」「方案 C 状态」「变更日志」

---

## 约束（必须遵守）

- 遵守 ANCHOR 12 条不变式
- 修改 CI 配置前先读取现有 `.github/workflows/ci.yml`
- 新增脚本先确认 Python 路径（`which python3`）
- 所有变更必须有测试验证（骨架测试全绿）
- 不确定就问，不要猜
