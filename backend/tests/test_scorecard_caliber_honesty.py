"""成绩单「口径诚实性」前端回归测试。

背景：``pages/analysis.js`` 的成绩单曾用大号红/绿数字展示 ``card.accuracy``，
但该口径已被实测确认存在缺陷（取数窗口与预测日无关 + 中性类几乎不可能判对），
实测「永远喊多」的基线反而更高。于是这个数字在**没有基线对照**时是一个误导性 KPI。

后续线上真机验收又抓到三处「把异常值当正常值展示」：
  A. **选择性基线假绿**：只跟 ``baseline_always_bullish``(15.8%) 比，就打出
     「具备增量信息」；而 ``baseline_always_bearish``(84.2%) 更高、**整个前端 0 处引用**。
     线上实测 68.8% 跑不赢「永远看空」。
  B. **置信度越界直出**：历史遗留记录 confidence 曾被算成 >100（线上实测最高 1039、
     均值 226.9），前端原样印成「置信1039%」。后端已不再用它参与评分。
  C. **null% 直出**：后端在「该模块 0 条记录」时故意返回 ``accuracy: null``，
     前端渲染成 ``null%``，且 ``null>=70/50`` 均为 false → 误染红色。

本文件锁定这些不变量。每条判据摘掉后本文件必须转红（故障注入）。
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_ANALYSIS = _REPO / "pages" / "analysis.js"
_HISTORY = _REPO / "pages" / "history.js"
_COMPONENTS = _REPO / "pages" / "_components.js"

BANNER_TITLE = "当前评分口径存在已知缺陷，数字暂不可用"
CAUSE_WINDOW = "取数窗口与预测日无关"
CAUSE_NEUTRAL = "中性类几乎无法判对"

_NODE = shutil.which("node")


def _read(p: Path) -> str:
    assert p.exists(), f"找不到 {p}"
    return p.read_text(encoding="utf-8")


def _src() -> str:
    return _read(_ANALYSIS)


def _run_node(script: str) -> str:
    if not _NODE:
        pytest.skip("环境无 node，跳过 JS 行为级断言")
    r = subprocess.run([_NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"node 执行失败:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def _render_card(card: dict) -> str:
    """把成绩单「KPI + 基线对照 + 坏数据告示」整段真实代码放到 node 里跑，取回渲染结果。

    只有行为级断言才能挡住「逻辑被改坏但字符串还在」这种假通过。
    """
    src = _src()
    i = src.index("const hasDirectional = ")
    j = src.index("// 待验证说明")
    block = src[i:j]
    script = (
        "global.MB={confidenceHtml:v=>String(v)};"
        "const card=" + json.dumps(card) + ";"
        + block
        + "console.log(JSON.stringify(html));"
    )
    return json.loads(_run_node(script).strip().splitlines()[-1])


# 线上真机实测（LeiJiang）的权威数字 —— 这一组数字曾被渲染成假绿
_ONLINE = {
    "directional_total": 16, "directional_correct": 11, "directional_accuracy": 68.8,
    "no_view": 18, "no_view_rate": 47.4, "baseline_always_bullish": 15.8,
    "baseline_always_bearish": 84.2, "sample_adequate": False, "required_samples": 194,
    "accuracy_ci95": [44.4, 85.8], "avg_confidence": 226.9,
    "confidence_out_of_range": 11, "verify_days": 15,
}


# ─────────────────── 1. 未修复时：常驻诚实横幅 ───────────────────

def test_honest_banner_is_present():
    src = _src()
    assert BANNER_TITLE in src, "成绩单缺少「口径有缺陷、数字暂不可用」横幅"


def test_banner_states_both_root_causes():
    src = _src()
    assert CAUSE_WINDOW in src, "横幅未说明「取数窗口与预测日无关」"
    assert CAUSE_NEUTRAL in src, "横幅未说明「中性类几乎无法判对」"


def test_old_accuracy_is_labeled_unusable():
    """旧数字照实保留（不隐藏），但必须标注口径待修 —— 不是无声地留着。"""
    src = _src()
    assert "旧口径准确率" in src, "旧口径数字未标注为暂不可用"
    assert "口径待修" in src, "旧口径数字未标注「口径待修」"


def test_banner_covers_module_accuracy_too():
    """各模块准确率用的是同一个坏口径，横幅必须说明覆盖范围，不能只罩主数字。"""
    src = _src()
    assert "本页所有准确率/命中率数字" in src, "横幅未声明它同时覆盖模块级数字"


def test_no_red_green_kpi_coloring_of_broken_accuracy():
    """故障注入：旧的红/绿 KPI 染色逻辑必须已经不存在。

    如果谁把颜色逻辑加回来（哪怕横幅还在），本断言转红。
    """
    src = _src()
    assert "const accColor=card.accuracy>=70" not in src, "旧口径数字又按 70/50 阈值染色了"


# ─────────────────── 2. 新字段存在时：优先展示新口径 ───────────────────

def test_prefers_new_fields_with_typeof_guards():
    """不得假设后端一定返回新字段：必须用 typeof/undefined 判断。"""
    src = _src()
    assert "typeof card.directional_accuracy === 'number'" in src, \
        "未用 typeof 判断 directional_accuracy，字段缺失时会渲染出 undefined"
    assert "typeof card.baseline_always_bullish === 'number'" in src, \
        "未用 typeof 判断 baseline_always_bullish"
    assert "typeof card.no_view_rate === 'number'" in src, \
        "未处理 no_view_rate（无观点率）"


def test_directional_accuracy_is_shown_side_by_side_with_baseline():
    """命中率必须与两条基线并排 —— 没有基线的命中率无法解释。"""
    src = _src()
    assert "有观点时命中率" in src, "未展示 directional_accuracy"
    assert "永远看多基线" in src, "未把 baseline_always_bullish 并排展示"
    assert "永远看空基线" in src, "未把 baseline_always_bearish 并排展示"


def test_comparison_uses_the_strongest_baseline():
    """故障注入：判定必须跟最强基线比。

    只跟 baseline_always_bullish 比 = 选择性对照 = 假绿（line 上曾如此）。
    摘掉 baseline_always_bearish 的读取 / 摘掉 blMax 取最大值的逻辑，本断言转红。
    """
    src = _src()
    assert "typeof card.baseline_always_bearish === 'number'" in src, \
        "未读取 baseline_always_bearish（选择性基线 = 假绿）"
    assert "blMax" in src, "未计算最强基线 blMax"
    # blMax 必须是「取最大」而不是「取第一条」
    assert "b.v>blMax" in src.replace(" ", ""), \
        "blMax 不是按最大值求解，可能仍在挑对自己有利的基线"


def test_conflict_between_the_two_baselines_is_stated():
    """两条基线结论不一致时必须明说，不能只报对自己有利的那半句。"""
    src = _src()
    assert "跑不赢「永远看空」" in src, "跑赢看多、跑不赢看空时未如实说明冲突"
    assert "未跑赢最强基线" in src, "跑不赢最强基线时缺少如实警示"


def test_significance_uses_ci95_and_never_flips_to_valid():
    """最强基线落在 95% 置信区间内时，必须说「统计上不可区分」。

    且**不得**因此改口说有效 —— 不许把「区分不出来」包装成「已跑赢」。
    """
    src = _src()
    assert "card.accuracy_ci95" in src, "未使用 accuracy_ci95 做显著性判断"
    assert "统计上不可区分" in src, "最强基线落在置信区间内时未如实说明"
    assert "尚不能判定具备增量信息" in src, \
        "样本不足时仍宣称有效（把不可区分包装成跑赢）"


def test_required_samples_is_quantified_not_just_said_insufficient():
    """故障注入：样本不足必须量化（当前 N 条 / 门槛 M 条），不能只说「不足」。"""
    src = _src()
    assert "card.required_samples" in src, "未引用 required_samples（样本不足没量化）"
    assert "card.directional_total" in src, "未引用 directional_total（当前样本数没显示）"


def test_sample_adequate_is_honored():
    src = _src()
    assert "card.sample_adequate===false" in src, "未处理 sample_adequate（样本是否足够）"
    assert "样本量不足" in src, "样本不足时缺少提示"


def test_calibration_threshold_is_not_hardcoded():
    """校准门槛由后端返回（会从 10 提到 30），前端不得写死旧默认值。

    写死 10 的后果：后端把门槛提到 30 后，前端仍告诉用户「需要至少 10 条」，
    又是一个口径说谎的数字。缺字段时只能说「更多」，不许假装知道。
    """
    src = _src()
    assert "card.calibrate_needed||10" not in src, "仍把校准门槛写死为 10"
    assert "typeof card.calibrate_needed==='number'" in src, \
        "未对 calibrate_needed 做类型判断"


# ─────────────────── 3. 置信度越界：不显示数字 ───────────────────

def test_shared_confidence_formatter_exists():
    """全站共用格式化函数必须存在，否则各页面又会各写各的（再犯一次）。"""
    src = _read(_COMPONENTS)
    assert "window.MB.fmtConfidence" in src, "缺少共用置信度格式化函数"
    assert "window.MB.confidenceHtml" in src, "缺少可直接插入 HTML 的封装"
    # 越界必须落到「—」且不 clamp 成 100
    assert "n < 0 || n > 100" in src, "未对 [0,100] 做区间校验"


def test_confidence_formatter_rejects_out_of_range_values():
    """行为级断言：越界值不显示数字、不 clamp。

    故障注入：把区间校验改成 clamp 成 100，本断言立刻转红。
    """
    src = _read(_COMPONENTS)
    i = src.index("window.MB.fmtConfidence")
    j = src.index("// v9.9.x T04")
    body = src[i:j]
    script = (
        "global.window=global; global.MB={}; global.MB.components={};"
        + body
        + "console.log(JSON.stringify([1039,887,541,388,1038,226.9,null,NaN,-5,101,0,100,68.8]"
          ".map(v=>MB.confidenceHtml(v))));"
    )
    out = _run_node(script).strip().splitlines()[-1]
    got = json.loads(out)
    # 前 10 个（含 null / NaN / -5 / 101）一律不显示数字；只有 0/100/68.8 是合法置信度
    expected = ["—（数据异常）"] * 10 + ["0%", "100%", "68.8%"]
    assert got == expected, f"置信度格式化输出不符:\n{got}\n期望:\n{expected}"


@pytest.mark.parametrize("path", [_ANALYSIS, _HISTORY])
def test_out_of_range_confidence_never_prints_raw_number(path):
    """故障注入：把 ``置信${conf}%`` 之类的原样渲染加回来，本断言转红。"""
    src = _read(path)
    for bad in ("置信${conf}%", "置信度 ${d.confidence}%",
                "+d.confidence+'%'", "+rec.confidence+'%'", "+a.confidence||0)+'%'"):
        assert bad not in src, f"{path.name} 仍在原样渲染置信度: {bad}"
    assert "MB.confidenceHtml" in src, f"{path.name} 未使用共用置信度格式化"


def test_confidence_out_of_range_is_disclosed():
    """坏数据要可见：必须显示条数并说明「已排除出评分」，不能悄悄藏起来。

    行为级：有 11 条越界记录时必须出现告示；0 条时不得凭空出现。
    故障注入：把条件改成 ``if(false)`` 或把阈值判定删掉，本断言转红。
    """
    src = _src()
    assert "card.confidence_out_of_range" in src, "未展示置信度越界的记录条数"
    assert "排除出评分" in src, "未说明越界记录已排除出评分"
    with_bad = _render_card(_ONLINE)
    assert "排除出评分" in with_bad, "有越界记录时未渲染告示"
    assert "11" in with_bad, "未显示越界记录条数 11"
    clean = dict(_ONLINE, confidence_out_of_range=0)
    assert "排除出评分" not in _render_card(clean), "没有越界记录时凭空渲染了告示"


# ─────────────────── 2b. 行为级：判决文案矩阵 ───────────────────

def test_online_case_never_claims_advantage():
    """线上真值：跑赢看多(15.8) 但跑不赢看空(84.2) → 绝不能自称有增量信息。"""
    out = _render_card(_ONLINE)
    assert "跑不赢「永远看空」" in out, "未如实说明跑不赢「永远看空」"
    assert "不构成优势" in out, "未给出「不构成优势」结论"
    assert "具备增量信息" not in out, "出现明显假绿：声称具备增量信息"
    assert "统计上不可区分" in out, "最强基线落在 CI 内却未说明统计上不可区分"
    assert "194" in out and "16" in out, "样本不足未量化（当前 16 条 / 门槛 194 条）"


def test_ci_covering_strongest_baseline_never_flips_to_valid():
    """跑赢最强基线但 CI 覆盖它 → 只能说「尚不能判定」，不许改口说有效。"""
    card = dict(_ONLINE, directional_accuracy=95, accuracy_ci95=[70, 99],
                directional_total=16, sample_adequate=False)
    out = _render_card(card)
    assert "尚不能判定具备增量信息" in out, "样本不足时仍宣称具备增量信息"
    assert "方向判断具备增量信息。" not in out, "把「不可区分」包装成了「已跑赢」"


def test_claiming_advantage_requires_clearing_both_baselines_and_ci():
    """只有同时跑赢两条基线、且 CI 不含最强基线，才允许说「具备增量信息」。"""
    card = dict(_ONLINE, directional_accuracy=95, accuracy_ci95=[92, 97],
                directional_total=400, sample_adequate=True)
    out = _render_card(card)
    assert "方向判断具备增量信息。" in out, "真正跑赢时未给出正面结论"


def test_baseline_cells_show_both_values():
    """两条基线的数值都必须渲染出来，让人一眼看到真话。"""
    out = _render_card(_ONLINE)
    assert "永远看多基线" in out and "15.8%" in out, "未渲染「永远看多基线」数值"
    assert "永远看空基线" in out and "84.2%" in out, "未渲染「永远看空基线」数值"


# ─────────────────── 4. 模块准确率：null 不是 0，也不是「差」 ───────────────────

def test_module_accuracy_null_is_not_rendered_as_null_pct():
    """行为级断言：0 样本模块渲染成「—」+「无样本」，不得出现 ``null%``。"""
    src = _src()
    i = src.index("// 模块准确率")
    j = src.index("// 最近判断")
    block = src[i:j]
    card = {
        "module_accuracy": {
            "market_factors": {"total": 0, "correct": 0, "accuracy": None, "no_view": 3},
            "news_data": {"total": 0, "correct": 0, "accuracy": None, "no_view": 5},
            "tech": {"total": 20, "correct": 17, "accuracy": 85.0, "no_view": 0},
        }
    }
    script = (
        "global.MB={confidenceHtml:v=>String(v)};"
        "let html='';"
        "const card=" + json.dumps(card) + ";"
        + block
        + "console.log(JSON.stringify(html));"
    )
    out = _run_node(script).strip().splitlines()[-1]
    html = json.loads(out)
    assert "null%" not in html, "仍渲染出 null%"
    assert "width:null%" not in html, "仍渲染出非法 CSS width:null%"
    assert "无样本" in html, "空样本未标注「无样本」"
    assert "—" in html, "空样本未显示为「—」"
    # 空样本不得染红：红色含义是「差」，空样本是「没有数据」
    rows = html.split("</div>")
    for name in ("market_factors", "news_data"):
        row = next(r for r in rows if name in r)
        assert "var(--red)" not in row, f"{name} 空样本被误染红色"


def test_null_module_accuracy_not_colored_red_in_source():
    """源码级兜底：排序与配色都要显式处理 accuracy 为 null。"""
    src = _src()
    assert "_accVal" in src, "未显式处理 accuracy 为 null（排序/配色会出问题）"
    assert "'var(--text2)':s.accuracy>=70" in src.replace(" ", ""), \
        "空样本未使用中性色（可能仍落到 var(--red)）"
    assert "'无样本'" in src or "无样本" in src, "空样本未标注「无样本」"
