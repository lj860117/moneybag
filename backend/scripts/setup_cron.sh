#!/usr/bin/env bash
# setup_cron.sh — 把 MoneyBag 的 crontab 排班对齐到"权威快照"
#
# ============================================================
# 2026-09-01 重写说明（务必读完再改这个文件）
# ============================================================
# 旧版本的问题（已作废，教训写在这里防止未来重犯）：
#   1. 用裸 `python` 起服务——服务器上根本不存在这个命令
#      （`which python` → NOT FOUND），必然 command not found。
#   2. 不加载 `.env`——生产每条 crontab 都有
#      `set -a && . .env && set +a`（或 `source venv/bin/activate` +
#      `export $(cat .env ...)`），旧脚本漏了这步，即使解释器对了
#      也会因为缺 API Key 静默失败。
#   3. 识别关键字用 `moneybag\|night_worker\|...` 这类猜的词——
#      经查生产 crontab 里根本没有 "moneybag" 这个词，这条 grep
#      永远匹配不到任何自己刚装的行，"检测已存在→询问是否替换"这层
#      保护形同虚设。
#   4. 手写维护 23 条命令，跟真实生产的 30 条渐渐漂移（缺 7 类条目、
#      7 处频率不一致），且没有任何机制发现这种漂移。
#
# 本次重写的做法：**不再手写命令**，直接把生产 `crontab -l` 的原始
# 输出整段固化进下面的 heredoc（CRONTAB_AUTHORITATIVE_BLOCK）。
# 好处：
#   - 零手抄误差——什么样就是什么样，连注释、空行、DISABLED 标记
#     都原样保留（process_watchdog.py 的阈值表依赖"三种调用形式并存"
#     这个事实，任何"顺手统一格式"的重构都可能让它的识别正则失效）。
#   - 刷新流程变简单：排班有变动时，重新抓一次
#     `ssh ... 'crontab -l'`，把内容贴进这个 heredoc 就完事，不需要
#     再翻译成 bash 数组。
#
# 排班的权威来源永远是服务器 `crontab -l`；本仓库另有一份**只读快照**
# 供 diff/恢复用：docs/ops/crontab.production.txt
# （两者应该保持同步——这个脚本的 heredoc 变了，那份快照也要重新抓）
#
# ============================================================
# 用法
# ============================================================
#   bash backend/scripts/setup_cron.sh              # dry-run，只打印将要安装的内容
#   bash backend/scripts/setup_cron.sh --apply       # 真正写入 crontab
#
# 幂等性：多次 --apply 不会产生重复条目——脚本会先移除所有已识别的
# MoneyBag 脚本行（按下面 MONEYBAG_SCRIPT_NAMES 匹配），再追加权威
# 内容；不属于 MoneyBag 的其它 crontab 条目（如果有）保持不动。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

APPLY=0
if [ "${1:-}" = "--apply" ]; then
    APPLY=1
fi

# 19 个真实脚本名（2026-09-01 从生产 crontab -l 提取，去重）。
# 用于识别"哪些 crontab 行属于 MoneyBag"，从而安全地做替换而不误删
# 用户/系统的其它 cron 任务。新增脚本时记得把名字加进这个列表。
MONEYBAG_SCRIPT_NAMES='auto_extract_cron|briefing_hallucination_check|broker_rating_cron|cache_warmer|closing_review_hallucination_check|daily_push_quality_check|daily_reflection_cron|dca_scheduler|fund_rank_build|housekeeping_cron|memory_archive_cron|monthly_rebalance_cron|monthly_report|night_worker|process_watchdog|stock_monitor_cron|weekend_push|weekly_plan_cron|weekly_review_cron'

# ============================================================
# 权威排班内容（2026-09-01 从生产 `crontab -l` 原始输出固化）
# ============================================================
# 30 条生效条目 + 2 条 DISABLED 注释 + 说明性注释，共 89 行。
# 三种调用形式并存（务必保留，不要"统一格式化"）：
#   22 条 /opt/moneybag/venv/bin/python scripts/X.py（绝对路径）
#    4 条 -m scripts.X（解释器同样是 venv 绝对路径）
#    4 条 python3 scripts/X.py（裸命令，venv 只在 shell 的 activate 里出现）
CRONTAB_AUTHORITATIVE_BLOCK=$(cat << 'CRONTAB_EOF'

# 
# ============================================================
# ━━━ 01:00-07:30 · AI 凌晨自主工作链 ━━━━━━━━━━━━━━━━━━━━━
0 10 * * 6 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --weekend >> /tmp/cache_warmer.log 2>&1
0 1 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/night_worker.py >> /opt/moneybag/data/night_worker/cron.log 2>&1
0 11 * * 6 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --full-extra >> /opt/moneybag/data/cache_warm.log 2>&1
0 16 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --harvest >> /opt/moneybag/data/night_worker/cron.log 2>&1
0 18 * * 1-5 cd /opt/moneybag/backend && source /opt/moneybag/venv/bin/activate && export $(cat .env | grep -v "^#" | xargs) && python3 scripts/dca_scheduler.py --discipline >> /opt/moneybag/logs/discipline.log 2>&1
0 20 * * 0 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/broker_rating_cron.py >> /opt/moneybag/data/cron/broker_rating.log 2>&1
0 20 * * 0 cd /opt/moneybag/backend && source /opt/moneybag/venv/bin/activate && export $(cat .env | grep -v "^#" | xargs) && python3 scripts/dca_scheduler.py --weekly >> /opt/moneybag/logs/weekly.log 2>&1
0 21 * * 0 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/weekly_plan_cron.py >> /opt/moneybag/data/cron/weekly.log 2>&1
0 22 * * 0 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/fund_rank_build.py >> /opt/moneybag/data/cron/fund_rank.log 2>&1
0 4 1 * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python -m scripts.memory_archive_cron >> /var/log/moneybag/memory_archive.log 2>&1
# ━━━ 08:00-08:30 · 记忆处理 + 简报推送 ━━━━━━━━━━━━━━━━━━━
# 08:00 批量提炼前一天对话 → pending_insights 待审队列
# 08:10 深度复盘（窗口：昨天 06:00 → 今天 06:00）→ 写入 context.last_analysis
# 08:30 推送早安简报到企微（工作日）
0 8 * * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python -m scripts.auto_extract_cron >> /var/log/moneybag/auto_extract.log 2>&1
# 09:00-11:59 每 10 分钟盯盘
# 09:00-14:59 每 30 分钟刷 midday 缓存
# ━━━ 09:00-15:00 · 盘中盯盘 + 缓存预热（工作日）━━━━━━━━━━
# 09:15 盘前缓存预热（morning payload）
0 9 1 * * cd /opt/moneybag/backend && source /opt/moneybag/venv/bin/activate && export $(cat .env | grep -v "^#" | xargs) && python3 scripts/monthly_report.py --all >> /opt/moneybag/logs/monthly.log 2>&1
0 9 25 * * cd /opt/moneybag/backend && source /opt/moneybag/venv/bin/activate && export $(cat .env | grep -v "^#" | xargs) && python3 scripts/dca_scheduler.py --dca >> /opt/moneybag/logs/dca.log 2>&1
10 8 * * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python -m scripts.daily_reflection_cron >> /var/log/moneybag/daily_reflection.log 2>&1
# 13:00-14:59 每 10 分钟盯盘（下午）
# ━━━ 15:30-15:35 · 收盘复盘 ━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 15:30 周五 · 本周复盘
# 15:30 收盘复盘（含地缘预警 + analyze 自动存档）
# 15:30 月末最后一个工作日 · 资产再平衡
# 15:35 收盘后数据缓存
45 8 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --morning >> /tmp/cache_warmer.log 2>&1
# 16:00 收盘后数据收割（存 precomputed 供凌晨 night_worker）
#   1. 凌晨 01:00-07:30 让给 night_worker（内部 10 阶段全链路）
#   2. 08:00-08:30 做对话提炼和复盘（R1 跑完了，不抢带宽）
0 21 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py --close >> /opt/moneybag/data/monitor/cron.log 2>&1
30 15 28-31 * * [ $(date -d "+1day" +\%m) != $(date +\%m) ] && cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/monthly_rebalance_cron.py >> /opt/moneybag/data/cron/monthly.log 2>&1
30 15 * * 5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/weekly_review_cron.py >> /opt/moneybag/data/cron/weekly.log 2>&1
30 16 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --full-extra >> /opt/moneybag/data/cache_warm.log 2>&1
30 20 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --nav-confirmed >> /tmp/cache_warmer.log 2>&1
30 8 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/night_worker.py --push-only >> /opt/moneybag/data/night_worker/cron.log 2>&1
#   3. 08:30 推送早安简报（能带上当天 08:10 刚生成的 handover）
*/30 9-14 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --midday >> /tmp/cache_warmer.log 2>&1
30 9 * * 6 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/weekend_push.py >> /opt/moneybag/data/night_worker/weekend_push.log 2>&1
10 18 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --after-close >> /tmp/cache_warmer.log 2>&1
#   4. 盘中 10 分钟盯盘 + 30 分钟刷缓存
50 7 * * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/briefing_hallucination_check.py --rounds 1 --interval 0 --alert >> /opt/moneybag/data/night_worker/hallucination_check.log 2>&1
#   5. 周末不跑行情类 cron，只跑数据预热和记忆维护
# DISABLED: */10 13-14 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py >> /opt/moneybag/data/monitor/cron.log 2>&1
# DISABLED: */10 9-11 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/stock_monitor_cron.py >> /opt/moneybag/data/monitor/cron.log 2>&1
# night_worker 内部按顺序串跑 10 个阶段（时间是目标节点，实际可能提前完成）：
# Sprint 3: 月度家庭报告
#   Step 10 07:30 生成早安简报（落盘等推送）
#   Step 1  01:00 健康巡检（数据源/Key）
#   Step 2  01:30 数据预热（Tushare/AKShare 因子）
#   Step 3  02:00 🧠 R1 Phase 1 - 全局市场分析
#   Step 4  02:30 🧠 R1 Phase 2 - 持仓诊断
#   Step 5  03:00 🧠 R1 Phase 3 - 推荐+决策+情景预计算
#   Step 6  04:00 生成分析产物（每用户摘要）
#   Step 7  05:00 研报存档
#   Step 8  06:00 维护任务
#   Step 9  07:00 外盘+地缘事件检查
# V7.6 (2026-04-19)：把 LLM 提炼和复盘挪到 night_worker 之后
# v9.5.100 全量补充预热（详情/催化/长持/宏观）
# v9.5.123 Sprint 2: 定投纪律系统
# v9.5.123: 净值确认后全量预热(确认净值重算排行)
#       下游：08:30 推送早安简报时会自动带上 handover 内容
# 周六 10:00 周末 cache 冷启
# 周日 20:00 机构评级周报
# 周日 21:00 下周规划
# 周日 22:00 基金排行榜重建（17560 只基金，14 秒）
# ━━━ 周末/月末维护 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 周末轻量推送（每周六 09:30，纯规则版）
# 每周日20:00 预测复盘
# 每天18:00 检测止盈止损
# 每月 1 号 04:00 记忆归档 + 月摘要（V7.5）
# 每月25号09:00 定投提醒推送
# 设计原则：
# 避免和 R1 Phase 并发抢带宽
# 钱袋子 AI 完整排班表 v7.6（2026-04-19 夜）

# v9.8.2: 晚间预热（覆盖用户下班后打开场景）
0 18 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/cache_warmer.py --evening >> /tmp/cache_warmer.log 2>&1

5 21 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/closing_review_hallucination_check.py --alert >> /opt/moneybag/data/monitor/hallucination_check.log 2>&1
0 22 * * 1-5 cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python scripts/daily_push_quality_check.py --date today --user LeiJiang --alert >> /opt/moneybag/data/logs/push_quality_check.log 2>&1
30 4 * * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python -m scripts.housekeeping_cron --apply >> /var/log/moneybag/housekeeping.log 2>&1
*/5 * * * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env && set +a && /opt/moneybag/venv/bin/python3 scripts/process_watchdog.py --apply >> /var/log/moneybag/watchdog.log 2>&1
CRONTAB_EOF
)

echo "=== MoneyBag Cron 排班对齐 ==="
echo "模式: $([ "$APPLY" = "1" ] && echo 'APPLY（将真正写入 crontab）' || echo 'DRY-RUN（只预览，不写入）')"
echo ""

# 当前 crontab 里，识别为"属于 MoneyBag"的行数（用真实脚本名匹配，
# 而不是旧版那个从不命中的 "moneybag" 关键字）
CURRENT_MB_LINES=$(crontab -l 2>/dev/null | grep -cE "$MONEYBAG_SCRIPT_NAMES") || CURRENT_MB_LINES=0
echo "当前 crontab 中识别到 $CURRENT_MB_LINES 行属于 MoneyBag 的条目"
echo "权威快照将安装 30 条生效条目（含 2 条 DISABLED 说明性注释）"
echo ""

if [ "$APPLY" != "1" ]; then
    echo "--- 以下是 dry-run 预览：将要写入 crontab 的权威内容 ---"
    echo "$CRONTAB_AUTHORITATIVE_BLOCK"
    echo "--- 预览结束 ---"
    echo ""
    echo "确认无误后运行：bash $0 --apply"
    exit 0
fi

# FIX 2026-09-01：第一版在这里试图"逐行过滤保留非 MoneyBag 条目"，
# 结果注释行（既不含脚本名、也不该被当成"其它系统的任务"）被两边
# 各贴了一份，导致 crontab 开头出现一段残缺的注释碎片——这是在服务器
# 隔离测试中被抓到的真实 bug（备份 + 立即恢复，未影响生产）。
#
# 教训：不要用"关键字过滤"去猜"这行是不是该保留"，逻辑含糊必然出错。
# 改成更清晰的语义——这个脚本的唯一职责是让 crontab 与权威快照完全
# 一致：
#   1. 已经一致 → 什么都不做（幂等的正确含义）
#   2. 不一致 → 先检查有没有"真正不属于 MoneyBag 的命令行"（非空、
#      非注释、且不含任何已知脚本名），有就中止并要求人工确认——
#      不猜测、不自动合并陌生内容；确认没有就直接整体覆盖。
CURRENT_CRONTAB=$(crontab -l 2>/dev/null) || CURRENT_CRONTAB=""

if [ "$CURRENT_CRONTAB" = "$CRONTAB_AUTHORITATIVE_BLOCK" ]; then
    echo "✅ 当前 crontab 已与权威内容完全一致，无需改动。"
    exit 0
fi

FOREIGN_LINES=$(printf '%s\n' "$CURRENT_CRONTAB" \
    | grep -v '^[[:space:]]*#' \
    | grep -v '^[[:space:]]*$' \
    | grep -vE "$MONEYBAG_SCRIPT_NAMES" || true)

if [ -n "$FOREIGN_LINES" ]; then
    echo "⛔ 拒绝执行：检测到当前 crontab 里有不属于 MoneyBag 的命令行，"
    echo "   本脚本不会猜测怎么合并陌生内容，以下几行需要你先手动处理："
    echo ""
    echo "$FOREIGN_LINES" | sed 's/^/   /'
    echo ""
    exit 1
fi

printf '%s\n' "$CRONTAB_AUTHORITATIVE_BLOCK" | crontab -

echo "✅ 已对齐。当前 crontab 内容："
echo ""
crontab -l
