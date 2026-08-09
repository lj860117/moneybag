# 钱袋子项目 - Pending Fixes

**最后更新**: 2026-06-14

---

## P1 - 数据质量 (高优先级)

### P1-1: Tushare 主数据源改造 ✅ **已完成**

**目标**: 将数据源从 AKShare 切换到 Tushare（用户有 5000 积分）

**状态**: ✅ 完成 (2026-06-14)

**修改文件**:
1. **`backend/services/tushare_data.py`** (第 349-460 行)
   - 函数: `get_northbound_flow()`
   - 修改: 增加返回 `daily_flows` 字段（日级别明细，最多30天）
   - 单位: 百万元 → 亿元

2. **`backend/services/alt_data.py`** (第 60-152 行)
   - 函数: `get_northbound_flow_detail()`
   - 修改:
     - 改用 `tushare_data.py` 的 `get_northbound_flow()` (替代 `tushare_fallback.py`)
     - 使用返回的 `daily_flows` 填充 `result["trend"]` (不再粗略估算日均)
     - Top 持股: 优先用 Tushare `hsgt_top10`，失败则降级 AKShare
   - 信号判断阈值调整: 50亿/10亿/-30亿

**部署**: v9.8.6 ✅

---

### P1-2: 基金详情页后端直出 ✅ **已完成（实际优化：统一详情接口）**

**原始描述**: 当前基金详情页数据在前端聚合，改为后端预计算 + 缓存

**调研结论**: 后端已直出 ~85% 数据。实际优化 = **统一为单次 API 调用**

**状态**: ✅ 完成 (2026-06-14, v9.8.7)

**修改文件**:
1. **`backend/api/fund_detail.py`**
   - `/api/fund/detail/{code}` 增加 `userId` 参数
   - 新增 `_enrich_detail_with_holding()` 函数
   - 带 userId 时自动补充持仓决策数据（my_holding/pnl/industry_tag/timing_label）
   - 缓存 key 包含 userId 避免污染

2. **`pages/_components.js`**
   - `showFundDetailModal()` 不再区分持仓/非持仓走不同 API
   - 统一调用 `/api/fund/detail/{code}?userId=xxx`
   - 前端减少分支逻辑

---

## P2 - 前端优化 (中优先级)

### P2-1: 选基页富化字段性能优化 ✅ **已完成（实际优化：心愿单 codes 快速路径）**

**原始描述**: `insight-fund.js` 的富化字段计算较慢

**调研结论**: 富化已是轻量字符串匹配（~0ms）。真正瓶颈 = **心愿单请求 top_n=2000 全量数据**

**状态**: ✅ 完成 (2026-06-14, v9.8.7)

**修改文件**:
1. **`backend/api/signals.py`**
   - `/api/fund-screen` 增加 `codes` 查询参数（逗号分隔代码列表）
   - 新增 `_screen_codes_fast()` 函数 — 跳过全量筛选+缓存

2. **`backend/services/fund_screen.py`**
   - 新增 `_enrich_single_fund()` — 单只基金详情快速查询（复用 fund_rank 数据）
   - 新增 `_enrich_from_tushare()` — Tushare fallback
   - 新增 `_apply_single_user_enrich()` — 用户级增强

3. **`pages/insight-fund.js`**
   - 心愿单 `_showWishlist()` 改用 `codes=xxx` 替代 `top_n=2000`
   - 后端从查 2000 只 → 查 5-20 只

---

## P3 - 数据准确性 (新增)

### P3-1: IPO 观察台自动更新机制 ✅ **已完成**

**问题**: SpaceX 已上市但 App 显示"传闻中"/"暂无计划"（硬编码过时）

**根本原因**:
1. 三个地方硬编码了 IPO 列表（`fund_detail.py` / `night_worker.py` / `ipo_verify.py`），不同步
2. `ipo_verify.py` 用 LLM 判断状态，LLM 不知道最新消息
3. 没有自动发现新热门 IPO 的机制

**解决方案**:
1. **`backend/scripts/ipo_verify.py` 重写**
   - 用新闻搜索 + 关键词匹配替代 LLM 判断（STATUS_KEYWORDS 映射）
   - 增加 `discover_hot_ipos()` — 自动发现新热门 IPO（A股/港股/美股）
   - 观察列表改读 `data/ipo_watchlist.json`（配置文件，不再硬编码）
   - 生成 `data/_cache/ipo_watchlist_api.json`（API 直接读这个）

2. **`backend/api/fund_detail.py` 修改**
   - `/api/ipo/watchlist` 优先读 `ipo_watchlist_api.json`（由 ipo_verify.py 生成）
   - Fallback 到硬编码数据

3. **`pages/insight.js` 修改**
   - IPO_WATCHLIST 前端默认值同步更新（SpaceX=✅已上市, xAI=已取消）

**部署**: v9.8.8 (insight.js + fund_detail.py) + v9.8.9 (ipo_verify.py 重写)

**验证**: ✅ `/api/ipo/watchlist` 返回 `source: ipo_watchlist_api.json (auto)`

---

## 完成标准

- ✅ = 已完成并验证
- 🟡 = 进行中
- 🔴 = 未开始
- ⚠️ 有风险/阻塞

---

## 修改记录

| 日期 | 版本 | 修改内容 | 文件 |
|------|------|---------|------|
| 2026-06-14 | v9.8.6 | P1-1 完成: northbound Tushare 改造 | `tushare_data.py`, `alt_data.py` |
| 2026-06-14 | v9.8.7 | P1-2: detail 接口统一 + P2-1: codes 快速路径 | `signals.py`, `fund_screen.py`, `fund_detail.py`, `_components.js`, `insight-fund.js` |
| 2026-06-14 | v9.8.8 | P3-1: IPO 观察台 SpaceX 数据修正 | `insight.js`, `fund_detail.py` |
| 2026-06-14 | v9.8.9 | P3-1: IPO 自动更新机制（新闻搜索+自动发现） | `ipo_verify.py`, `fund_detail.py` |
| 2026-06-16 | v9.9.0 | 版本号升级 + 每日推送质量评估系统 + 收盘复盘延迟到21:00 + 盘中监控合并到收盘复盘 | `backend/config.py`, `backend/scripts/daily_push_quality_check.py`, `backend/scripts/closing_review_hallucination_check.py`, `backend/scripts/stock_monitor_cron.py`, `backend/services/wxwork_push.py` |
