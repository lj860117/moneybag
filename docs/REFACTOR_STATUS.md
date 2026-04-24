# MoneyBag 重构状态追踪

> 最后更新：2026-04-25
> 对应设计文档：`docs/design/12-framework-refactor.md`
> Git tag 基线：`m1-w1-skeleton`

---

## 当前阶段：M1 W1 — 框架改造

### 绞杀者模式 5 步进度

| 步骤 | 内容 | 状态 | Git Tag / Commit |
|------|------|------|-----------------|
| **1. 搭空骨架** | 四层目录 + 4 Protocol + 3 infra 最小实现 | ✅ 完成 | `m1-w1-skeleton` (29c4d18) |
| 2. 拆 main.py | 199 路由按业务域切到 api/*.py | ⬜ 未开始 | — |
| 3. 新模块走新架构 | M2 起家庭画像/资产负债表/7 点清单直接四层写 | ⬜ 未开始 | — |
| 4. 老服务按需迁移 | 改哪个顺手迁到 domain/services/ | ⬜ 未开始 | — |
| 5. 配额管理 | main.py < 200 行 linter + import 门禁 | ⬜ 未开始 | — |

---

## 四层骨架文件清单

### domain/ — 领域层

```
domain/
├── __init__.py
├── models/
│   └── __init__.py          # LLMResponse dataclass
├── protocols/
│   ├── __init__.py          # 重导出 4 Protocol
│   ├── cache.py             # CacheProtocol
│   ├── store.py             # StoreProtocol
│   ├── llm_client.py        # LLMClientProtocol
│   └── data_source.py       # DataSourceProtocol
├── services/
│   └── __init__.py          # 占位（不变式 #9：禁止互 import）
└── rule_engine/
    └── __init__.py          # 占位
```

### infra/ — 基础设施层

```
infra/
├── __init__.py
├── cache/
│   ├── __init__.py          # 导出 MemoryCache
│   └── memory_cache.py      # CacheProtocol 实现（内存 + TTL）
├── store/
│   ├── __init__.py          # 导出 FileStore
│   └── file_store.py        # StoreProtocol 实现（原子写 + .bak 恢复）
├── llm/
│   ├── __init__.py          # 导出 LLMClient
│   └── gateway.py           # LLMClientProtocol 适配器（代理到老 LLMGateway）
├── data_source/
│   └── __init__.py          # 占位（M1 W4 实现）
├── knowledge/
│   └── __init__.py          # 占位
├── events/
│   └── __init__.py          # 占位
└── config/
    └── __init__.py          # 占位
```

### api/ + use_cases/ — 上层占位

```
api/
└── __init__.py              # 占位（M1 W1-W2 拆 main.py 时填充）

use_cases/
└── __init__.py              # 占位（M2 起填充）
```

### 测试

```
tests/
└── test_skeleton_m1.py      # 28 条冒烟测试（全绿）
```

---

## Protocol → 实现 对照表

| Protocol | 定义位置 | 当前实现 | 计划实现 |
|----------|---------|---------|---------|
| `CacheProtocol` | `domain/protocols/cache.py` | `infra/cache/memory_cache.py` (MemoryCache) | DiskCache, PrecomputedCache (M1 W2) |
| `StoreProtocol` | `domain/protocols/store.py` | `infra/store/file_store.py` (FileStore) | SqliteStore (post-M2) |
| `LLMClientProtocol` | `domain/protocols/llm_client.py` | `infra/llm/gateway.py` (LLMClient adapter) | 独立实现替代老 LLMGateway (M2) |
| `DataSourceProtocol` | `domain/protocols/data_source.py` | — | TushareProvider, AkshareProvider, BaostockProvider (M1 W4) |

---

## 关键共存注意事项

1. **FileStore 与 persistence.py 共享 DATA_DIR** — 两者用相同的 SHA256[:16] 哈希，可同时操作 `data/users/`。改其中一个的哈希算法必须同步改另一个。
2. **LLMClient 通过 lazy import 代理到 services/llm_gateway.py** — 老 gateway 520 行代码不动，新代码 import `infra.llm.LLMClient`。M2 时 gateway 实现搬到 infra/。
3. **46 个 `_cache = {}` 尚未迁移** — 步骤 4 按需迁移，碰到哪个改哪个。
4. **16 个直读 DATA_DIR 的文件尚未迁移** — 同上。

---

## 方案 C 状态

| 检查项 | 状态 |
|--------|------|
| 4 个核心 Protocol 定义 | ✅ |
| import-linter 配置 | ⬜ 待配 |
| mypy strict 配置 | ⬜ 待配 |
| CI 集成 | ⬜ 待配 |

---

## 变更日志

| 日期 | 内容 | Commit |
|------|------|--------|
| 2026-04-25 | M1 W1 骨架搭建完成：4 层目录 + 4 Protocol + MemoryCache + FileStore + LLMClient adapter + 28 测试全绿 | `29c4d18` |
