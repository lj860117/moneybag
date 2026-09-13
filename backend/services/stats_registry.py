"""唯一可信统计白名单注册表（v9.9.26「假统计防复发」）。

## 这个文件存在的理由

本项目有一条铁律（用户反复强调，本轮冲刺的核心原则）：

    算不出 / 没数据的地方必须是 ``None`` + 原因说明，绝不能返回占位数值
    （placeholder）；「没数据」也绝不能被伪装成「判断为中性 / 看多」。

本仓历史上反复出现同一形态的违规：一个**看着像指标的常数**被写死在代码或文案里。
本轮（v9.9.26）刚清理的点包括：

  * ``api/shared_helpers.py`` 规则引擎回答里 21 处 ``"confidence": 0.8~0.95``
    （唯一消费方 ``api/chat.py`` 用它做 ``>= 0.7`` 的快速通道闸门 —— 全部取值
    恒 ≥ 0.7，该闸门永远为真，等于没有闸门）
  * ``holdings.py`` / ``dca_scheduler.py`` 的 ``get("trend_confidence", 55)``
    （默认 55 = 刚过置信度门槛，把「缺失」伪装成「够用」）
  * ``34.6%``（定投准确率）、``3.7%``（超额收益）、``85%``（三年赚钱概率）
  * ``多赚 15-20%`` / ``多赚 2-3%/年``（无出处的收益断言，前后端各有一份副本）
  * ``confidence || 50``（50 = 闸门阈值 → 伪造出「刚好达标」）
  * ``const adv = c.advantage || 0``（缺失 → 渲染成「多赚 0%」）
  * 硬编码的 ``returns {0.25, 0.12, -0.15}``

## 使用规则（硬约束）

1. **凡是在「收益 / 胜率 / 概率 / 回撤 / 覆盖率 / 年化 / 超额」等语境里出现的
   百分比或统计数字，要么来自运行时计算，要么必须在本文件登记来源，禁止裸写。**
2. 登记的意义是「这个数字有一个**可核查的**来源」，不是「这个数字看起来合理」。
   ``STATS_SOURCES`` 只允许放**真正正当**的常量：
     - 日历 / 数学常量（365 天、12 个月、100% 基数…）
     - 由已知配置常量或规则表**定义**出来的分档阈值（规则本身，不是测量值）
     - 清晰的数学定义（如「每承担 1% 回撤」里的 1%）
   **禁止**把编造值塞进来当后门。凡是「说不出出处、只是看起来像行业常识」的数字
   （例如「美股长期年化 10%」「纯债年化 2-4%」），一律不得登记 —— 它们必须在
   代码里改成运行时计算，或按铁律返回 ``None`` + 原因。
3. 原则：**算不出就 ``None`` + reason**。缺数据不是「中性」，更不是「够用」。
4. 本表的完备性由 ``backend/tests/test_no_hardcoded_stats.py`` 的源码扫描守着；
   新增一处合法写死的统计数字，必须同步在这里登记，否则测试会红。

## 为什么不在这里放「负向清单」

「已知无出处、待主理人裁决」的数字清单放在测试文件的 ``UNVERIFIED_REPORTED``
里，**故意不放进本表** —— 本表是「可信来源」白名单，混进待裁决项就等于把门焊开。
"""
from __future__ import annotations

# ============================================================
# 可信来源白名单：字面量 -> 「人类可读的正当理由 + 数据来源」
#
# key 的写法：与源码里出现的字面量一致（``"365"`` 或 ``"20%"`` 都可以）。
# 查询时由 ``_norm()`` 归一：去空白、去掉尾部 ``%``，因此 ``is_registered("20%")``
# 与 ``is_registered("20")`` 等价。
# ============================================================
STATS_SOURCES: dict[str, str] = {
    # ---- 日历常量（非金融统计）----
    "365": "一年 365 天——日历常量，与收益/胜率无关。来源：日历定义",
    "7": "一周 7 天——日历常量，与收益/胜率无关。来源：日历定义",
    "12": "一年 12 个月 / 12 个月份——日历常量。来源：日历定义",
    "252": "A 股一年约 252 个交易日，用于把日频波动率/夏普年化的业界通行约定。"
           "来源：交易日约定（是换算分母，不是收益预测）",
    # ---- 数学定义 ----
    "100": "百分数 / 百分位基数（100% = 满值，百分位满档）。来源：百分数数学定义",
    "1": "归一化单位（如「每承担 1% 回撤」——分母单位，不是统计量）。来源：归一化定义",
}

# ============================================================
# 站点级正当理由：(相对路径, 字面量) -> 理由
#
# 为什么需要这一层：``STATS_SOURCES`` 是**按字面量**全局生效的，而同一个数字在
# 不同地方含义完全不同。例如 ``2%`` 在 ``pages/_components.js`` 是「夏普比率的
# 无风险利率口径」（config 常量，正当），在 ``fund_screen.py`` 却是「现金管理工具
# 年化 2% 左右」（无出处的收益断言，不正当）。把 ``2%`` 全局登记会把后者一起放行
# —— 那正是「把门焊开」。
#
# 本表只登记**能明确说清来源**的那种：规则表 / 公式权重 / 分档口径 / 公开费率。
# 登记不下的（无出处的收益断言）一律留给 ``UNVERIFIED_REPORTED``，不得塞进这里。
# ============================================================
_DCA_TABLE_REASON = (
    "智能定投倍率表的**分档阈值**——它就是规则定义本身，不是测量出来的统计量。"
    "来源：services/signal.py 的估值档位（_valuation_tier）× 倍率矩阵"
    "（DCA_MATRIX / DCA_MULTIPLIERS）"
)
_APP_FEE_REASON = (
    "基金**费率**（管理费 / 托管费 / 申购费），来自基金合同与公开费率表，"
    "不是收益预测。来源：app.js FUND_DETAILS 的产品公开资料"
)

SITE_JUSTIFICATIONS: dict[tuple[str, str], str] = {
    # ---- 智能定投倍率表：后端文案 + 前端副本（同一张表的两个副本）----
    ("backend/api/shared_helpers.py", "20%"): _DCA_TABLE_REASON,
    ("backend/api/shared_helpers.py", "30%"): _DCA_TABLE_REASON,
    ("backend/api/shared_helpers.py", "50%"): _DCA_TABLE_REASON,
    ("backend/api/shared_helpers.py", "70%"): _DCA_TABLE_REASON,
    ("backend/api/shared_helpers.py", "85%"): _DCA_TABLE_REASON,
    ("pages/quiz.js", "20%"): _DCA_TABLE_REASON,
    ("pages/quiz.js", "30%"): _DCA_TABLE_REASON,
    ("pages/quiz.js", "50%"): _DCA_TABLE_REASON,
    ("pages/quiz.js", "70%"): _DCA_TABLE_REASON,
    ("pages/quiz.js", "85%"): _DCA_TABLE_REASON,
    # ---- 长期评分公式权重（3年年化/稳定性/一致性）----
    ("pages/analysis.js", "50%"): "长期评分公式权重：3年年化 50%。来源：services/longterm_screen.py 评分公式",
    ("pages/analysis.js", "30%"): "长期评分公式权重：稳定性 30%。来源：services/longterm_screen.py 评分公式",
    ("pages/analysis.js", "20%"): "长期评分公式权重：一致性 20%。来源：services/longterm_screen.py 评分公式",
    # ---- 估值分档口径 ----
    ("pages/insight.js", "30%"): "估值分档阈值（<30% 便宜）。来源：services/signal.py 估值档位定义",
    ("pages/insight.js", "70%"): "估值分档阈值（>70% 偏贵）。来源：services/signal.py 估值档位定义",
    # ---- 收益率指标的「阅读分档」（展示口径，不是统计断言）----
    ("pages/_components.js", "15%"): "成立以来年化的阅读分档（>15% 很厉害），主观展示口径，不是统计断言",
    ("pages/_components.js", "8%"): "成立以来年化的阅读分档（8%-15% 不错），主观展示口径，不是统计断言",
    ("pages/_components.js", "5%"): "成立以来年化的阅读分档（<5% 不如货基），主观展示口径，不是统计断言",
    # ---- 风险调整指标的口径声明 ----
    ("pages/_components.js", "1%"): "卡玛比率定义说明「每承担 1% 回撤」——分母单位，数学定义",
    # ---- 运维告警阈值（非金融统计）----
    ("backend/infra/llm/gateway.py", "30%"): (
        "LLM 网关缓存命中率告警阈值。来源：同行的判据 `daily_hit_ratio < 0.3`"
        "（运维阈值，与收益/胜率无关）"),
    # ---- 基金费率（公开产品资料）----
    ("app.js", "0.15%"): _APP_FEE_REASON,
    ("app.js", "0.05%"): _APP_FEE_REASON,
    ("app.js", "0.12%"): _APP_FEE_REASON,
    # ---- 与 config 同值、但被硬编码进提示字符串（有漂移风险，见报告）----
    ("backend/services/ds_enhance.py", "0.2%"): (
        "银行活期利率口径。来源：config CASH['bank_rate_current']=0.002"
        "（硬编码在同值字符串里而非读取 config，存在漂移风险 —— 已在报告标注）"),
    ("backend/services/ds_enhance.py", "1%"): (
        "通胀假设口径。来源：config CASH['inflation_rate']=0.01"
        "（硬编码在同值字符串里而非读取 config，存在漂移风险 —— 已在报告标注）"),
}

# ============================================================
# 已知「无出处」、**待主理人裁决**的统计断言：(相对路径, 字面量) -> 说明
#
# 这一层**不是白名单**：这些数字按铁律本应改成运行时计算或 None + 原因，
# 只是本轮不允许由测试作者顺手改生产代码，所以先把它们显式记在这里，交给
# 主理人决定（删除 / 改成实时计算 / 补来源）。
#
# 约束（由测试强制）：
#   * 不得与本文件的 STATS_SOURCES / SITE_JUSTIFICATIONS 重叠（不能既待裁决又当正当）
#   * 每项必须真的还能在源码里被扫到（修好之后必须把本项删掉，清单不许长毛）
#   * 数量有上限，防止有人把它当后门无限扩张
# ============================================================
UNVERIFIED_REPORTED: dict[tuple[str, str], str] = {
    ("app.js", "10%"):
        "无出处：「过去30年标普500年化回报约10%」——历史收益断言，未标注来源与区间口径",
    ("app.js", "22.1%"):
        "无出处：FUND_DETAILS 写死的基金历史收益（y1=+22.1%），没有运行时净值来源",
    ("app.js", "38.7%"):
        "无出处：FUND_DETAILS 写死的基金历史收益（y3=+38.7%），没有运行时净值来源",
    ("app.js", "95.2%"):
        "无出处：FUND_DETAILS 写死的基金历史收益（y5=+95.2%），没有运行时净值来源",
    ("backend/services/industry_templates.py", "4%"):
        "无出处：「纯债……最低风险，年化2-4%」——收益断言无回测/合同来源",
    ("backend/services/ds_enhance.py", "4%"):
        "无出处：「纯债稳健打底，年化2-4%」/「债基(年化 3-4%)」——收益断言无来源",
    ("backend/services/ds_enhance.py", "15%"):
        "无出处：与「债基年化 3-4%」同句的收益断言，无来源",
    ("backend/services/ds_enhance.py", "10%"):
        "无出处：「美股大盘核心，长期年化10%」——收益断言无来源",
    ("backend/services/fund_screen.py", "2%"):
        "无出处：「现金管理工具，年化 2% 左右」——收益断言无来源",
}

_UNVERIFIED_MAX = 12


def _site_key(relpath: str, literal: str) -> tuple[str, str]:
    return (str(relpath), str(literal).strip())


def is_justified(relpath: str, literal: str) -> bool:
    """该处写死的统计数字是否有正当来源（全局白名单或站点级理由）。"""
    if is_registered(literal):
        return True
    return _site_key(relpath, literal) in SITE_JUSTIFICATIONS


def site_reason(relpath: str, literal: str) -> str:
    """返回站点级理由；没有则返回空串。"""
    key = _site_key(relpath, literal)
    if key in SITE_JUSTIFICATIONS:
        return SITE_JUSTIFICATIONS[key]
    return explain(literal)


def unverified_reason(relpath: str, literal: str) -> str:
    """返回「已知无出处、待裁决」的说明；不在清单里则返回空串。"""
    return UNVERIFIED_REPORTED.get(_site_key(relpath, literal), "")


def unverified_max() -> int:
    """待裁决清单允许的最大条目数（防止清单无限扩张）。"""
    return _UNVERIFIED_MAX


def _norm(literal: str) -> str:
    """归一化字面量：去空白、去尾部 ``%``。"""
    return str(literal).strip().rstrip("%").strip()


def is_registered(literal: str) -> bool:
    """该字面量是否已在 ``STATS_SOURCES`` 登记（有可核查来源）。"""
    key = str(literal).strip()
    if key in STATS_SOURCES:
        return True
    return _norm(literal) in {_norm(k) for k in STATS_SOURCES}


def explain(literal: str) -> str:
    """返回登记理由；未登记时返回一句明确的「未登记」说明（不返回空串）。"""
    key = str(literal).strip()
    if key in STATS_SOURCES:
        return STATS_SOURCES[key]
    for k, reason in STATS_SOURCES.items():
        if _norm(k) == _norm(literal):
            return reason
    return (f"未登记：{literal!r} 没有可核查的来源。"
            "请改为运行时计算，或按铁律返回 None + 原因说明；"
            "确属正当常量时才登记进 STATS_SOURCES。")
