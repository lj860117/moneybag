"""晨报「低信息量句式」裁剪回归测试（v9.9.45）
============================================

背景
----
09-17 LeiJiang 晨报 body=3610 字节 + 信封 52 = 3662 字节，超过
``LENGTH_ALERT_BYTES = 3600`` 告警线 62 字节，质检判 FAIL（score 95）。
体积是硬约束（企业微信单条推送有上限），所以只能靠**砍掉低信息量的固定
句式**来腾空间，而不是压缩有信息量的内容。

本轮砍两处：

1. 隔夜市场的「对你的QDII影响有限」
   它是**恒真句** —— 不管纳指 +3% 还是 -3%，"影响有限"都说得通，对读者
   零决策价值，却每天固定占 ~58 字节。三个有实质信息的分支
   （跟涨 / 承压 / 港股走弱）**必须保留**，只砍 else。

2. ``_build_rebalance_gap()`` 末尾的「当前持仓以主动混合基金为主…」
   三重重复 + 会说错话（详见下面该用例的 docstring），占 132 字节。

反向约束
--------
本文件**不**碰免责声明「⚠️ AI建议仅供参考，不构成投资建议」。它在晨报里
出现两次看着重复，但：
  - ``test_briefing_thermometer_wiring.py:60`` 与
    ``test_briefing_qdii_delay_note.py:257`` 都断言输出**结尾必须是**这句；
  - ``prompts/versions/CHANGELOG.md:152`` 把"免责声明率 ≥ 80%"列为质检项。
砍它会同时踩测试红线和合规红线。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import scripts.night_worker as nw                        # noqa: E402

NIGHT_WORKER_SRC = (BACKEND_DIR / "scripts" / "night_worker.py").read_text(
    encoding="utf-8")

# v9.9.45 之前 _build_rebalance_gap() 末尾无条件 append 的这句。
# 保留在测试里是为了：(a) 断言它真的不再出现；(b) 量化改动前后的字节差。
_STRUCTURAL_WARNING = (
    "⚠️ 当前持仓以主动混合基金为主，"
    "与目标指数基金配置存在结构性差异，建议逐步向目标靠拢。"
)
# v9.9.45 之前隔夜市场 else 分支的恒真废话
_TAUTOLOGY_IMPACT = "对你的QDII影响有限"

# ---- 真实持仓数据（2026-09-17 LeiJiang 晨报「持仓明细」原文摘录）----
# 用真实数字而不是随手编的，是因为 _build_rebalance_gap 的输出是百分比 +
# 金额，编造的持仓会算出别的百分比，字节差就没有参考价值。
# 这组数字可以自检：合计 ¥765.3 ≈ 晨报里的「当前总市值 ¥765」，
# 其他(主动混合)占比 594.5/765.3 = 77.7% ≈ 晨报里的 78%。
_REAL_HOLDINGS_0917 = [
    {"code": "002163", "name": "东方惠新灵活配置混合C", "cur_val": 160.5},
    {"code": "013107", "name": "华夏先进制造龙头混合A", "cur_val": 120.2},
    {"code": "016501", "name": "华夏半导体龙头混合C", "cur_val": 107.5},
    {"code": "005851", "name": "财通新视野灵活配置混合A", "cur_val": 101.7},
    {"code": "006555", "name": "浦银安盛全球智能科技", "cur_val": 97.7},
    {"code": "008984", "name": "财通科技创新混合C", "cur_val": 95.7},
    {"code": "007356", "name": "汇添富科技创新混合C", "cur_val": 8.9},
    {"code": "005698", "name": "华夏全球科技先锋混合", "cur_val": 73.1},
]


# ============================================================
# 改动 1：隔夜市场 QDII 影响判语
# ============================================================

@pytest.mark.parametrize("nq_pct, hsi_pct, expected", [
    # 09-17 实测就是 nq=+0.7 / hsi 无数据，走的正是被砍掉的 else 分支
    (1.5, 0.0, "你的QDII科技基金今天大概率跟涨"),
    (1.01, 0.0, "你的QDII科技基金今天大概率跟涨"),
    (-1.5, 0.0, "你的QDII科技基金今天可能承压"),
    (-1.01, 0.0, "你的QDII科技基金今天可能承压"),
    (0.0, -1.5, "港股走弱,留意港股相关持仓"),
    (0.0, -1.01, "港股走弱,留意港股相关持仓"),
])
def test_overnight_qdii_impact_keeps_substantive_branches(nq_pct, hsi_pct, expected):
    """三个有实质信息的分支必须照旧输出 —— 别把有用的也砍了。

    裁量的目的是砍恒真废话，不是砍信息。这三个分支（跟涨 / 承压 /
    港股走弱）会随行情变化，是读者真正会看的判断，砍掉等于把晨报做成
    只有天气没有预报。
    """
    assert nw._overnight_qdii_impact(nq_pct, hsi_pct) == expected


@pytest.mark.parametrize("nq_pct, hsi_pct", [
    (0.0, 0.0),        # 全平
    (0.7, 0.0),        # 09-17 实测值：纳指 +0.7%，恒生无数据
    (0.2, 0.3),        # 微涨
    (-0.5, -0.2),      # 微跌但未破阈值
    (1.0, 0.0),        # 边界：严格大于才判跟涨
    (-1.0, 0.0),       # 边界：严格小于才判承压
    (0.0, -1.0),       # 边界：严格小于才判港股走弱
    (0.5, -0.9),       # 港股接近但没破 -1.0
])
def test_overnight_qdii_impact_drops_tautology(nq_pct, hsi_pct):
    """核心用例：无实质信息时返回空串（原来是恒真的「影响有限」）。

    空串的语义是"这一行不该输出"，调用方据此整行跳过 —— 包括不输出
    ``_reason``，因为「波动不大,方向不明 →」这种只有前半句的残句比
    整句还难读。
    """
    assert nw._overnight_qdii_impact(nq_pct, hsi_pct) == ""


def test_overnight_impact_line_not_appended_when_empty():
    """空 impact 时整行不追加（含 _reason 一起跳过）。

    复刻 step_generate_products 里的拼接契约：``if _impact:`` 才 append。
    """
    overnight = "标普500 +0.2% | 纳指 +0.7%"
    impact = nw._overnight_qdii_impact(0.7, 0.0)
    if impact:
        overnight += f"\n  {'波动不大,方向不明'} → {impact}"

    assert overnight == "标普500 +0.2% | 纳指 +0.7%"
    # 不留残缺的箭头，也不留尾随换行
    assert "→" not in overnight
    assert not overnight.endswith("\n")


def _night_worker_string_constants() -> list:
    """收集 night_worker.py 里的所有**字符串常量**（AST 层，不含注释/文档串）。

    用 AST 而不是源码文本做回潮守卫，是因为 night_worker.py 的注释里需要
    留一句"这里原来写过 X、为什么删掉"的说明 —— 纯文本扫描会被自己的
    注释误伤，AST 只看真正会被求值的字面量。
    """
    import ast
    tree = ast.parse(NIGHT_WORKER_SRC)

    # 文档串也是 Constant，但它们不是"会被输出的内容"，先排除掉
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_tautology_impact_string_is_gone_from_source():
    """回潮守卫：恒真句不得再作为**字符串常量**出现在 night_worker 源码里。

    只要有人把 else 分支加回来（哪怕换了个变量名），这条就红。
    """
    leaked = [s for s in _night_worker_string_constants()
              if _TAUTOLOGY_IMPACT in s]
    assert not leaked, (
        f"night_worker 又出现了恒真的「{_TAUTOLOGY_IMPACT}」（{leaked[:3]}）—— "
        "它在任何行情下都成立，对读者零决策价值，且每天固定 ~58 字节")


# ============================================================
# 改动 2：_build_rebalance_gap() 末尾的结构性差异提示
# ============================================================

def test_rebalance_gap_drops_structural_warning():
    """被删的那句不再出现在输出里（真实持仓数据）。"""
    out = nw._build_rebalance_gap("LeiJiang", _REAL_HOLDINGS_0917)

    assert _STRUCTURAL_WARNING not in out
    assert "当前持仓以主动混合基金为主" not in out

    leaked = [s for s in _night_worker_string_constants()
              if "当前持仓以主动混合基金为主" in s]
    assert not leaked, (
        f"night_worker 源码里又出现了这句无条件输出的结构性差异提示: {leaked[:3]}")


def test_rebalance_gap_keeps_conditional_other_bucket_line():
    """删的是**无条件**那句，条件性的「⚠ 其他(主动混合)」必须还在。

    923 行 ``if other_pct > 5:`` 才输出 —— 它带真实百分比，有信息量，
    且条件成立时才说"以主动混合为主"，不会说错话。
    """
    out = nw._build_rebalance_gap("LeiJiang", _REAL_HOLDINGS_0917)

    assert "⚠ 其他(主动混合)" in out, (
        f"条件性的其他桶提示被误删了:\n{out}")
    assert "78%" in out, f"真实占比应仍是 78%（594.5/765.3）:\n{out}"


def test_rebalance_gap_has_no_trailing_blank_line():
    """删掉 928-929 后输出不得留下孤零零的尾随空行。"""
    out = nw._build_rebalance_gap("LeiJiang", _REAL_HOLDINGS_0917)

    assert out, "真实持仓下不应返回空串"
    assert not out.endswith("\n"), f"输出末尾有多余换行: {out!r}"
    assert not out.endswith("\n\n"), f"输出末尾有多余空行: {out!r}"
    assert out.splitlines()[-1].strip(), (
        f"最后一行是空行，读者会看到一段空白: {out!r}")


def test_rebalance_gap_structural_warning_was_unconditional():
    """记录被删那句的**会说错话**缺陷，防止有人按原样加回来。

    原实现里 929 行无条件输出，但「⚠ 其他(主动混合)」只在 other_pct > 5
    时输出。构造一个以指数基金为主的持仓（other_pct ≤ 5），旧实现仍会
    宣称"当前持仓以主动混合基金为主"—— 那是假的。新实现不再有这句，
    所以不会说错话。
    """
    index_heavy = [
        {"code": "100038", "name": "富国沪深300指数增强A", "cur_val": 700.0},
        {"code": "006555", "name": "浦银安盛全球智能科技", "cur_val": 300.0},
        {"code": "005851", "name": "财通新视野灵活配置混合A", "cur_val": 20.0},
    ]
    out = nw._build_rebalance_gap("LeiJiang", index_heavy)

    # other_pct = 20/1020 ≈ 2%，旧实现会在这里谎称"以主动混合基金为主"
    assert "其他(主动混合)" not in out, (
        f"other_pct≈2% 不应输出其他桶提示:\n{out}")
    assert "当前持仓以主动混合基金为主" not in out


# ============================================================
# 字节账（加分项）：用真实持仓量化省了多少
# ============================================================

def test_byte_saving_on_real_holdings_clears_alert_line():
    """用 09-17 真实持仓算字节差：改动 2 省 132 字节。

    改动 1 在 09-17 那天（nq=+0.7 走 else）再省 58 字节，合计 190 字节，
    足够把 3662 拉回 3600 以下。这里只断言改动 2 的部分（它可离线复现）；
    改动 1 的 58 字节由恒真句长度决定，属固定值。
    """
    after = nw._build_rebalance_gap("LeiJiang", _REAL_HOLDINGS_0917)
    before = after + "\n\n" + _STRUCTURAL_WARNING

    saved = len(before.encode("utf-8")) - len(after.encode("utf-8"))
    assert saved == 132, f"改动 2 预期省 132 字节，实测 {saved}"

    # 告警线差 62 字节，改动 2 单独就已经够；两处合计 190 更宽裕
    assert saved >= 62, f"单独一处不足以把 3662 压回 3600 以下，实测只省 {saved}"
