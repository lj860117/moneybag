"""成绩单「口径诚实性」前端回归测试。

背景：``pages/analysis.js`` 的成绩单用大号红/绿数字展示 ``card.accuracy``，
但该口径已被实测确认存在缺陷（取数窗口与预测日无关 + 中性类几乎不可能判对），
实测「永远喊多」的基线反而更高。于是这个数字在**没有基线对照**时是一个误导性 KPI。

本文件锁定三条：
  1. 字段缺失（后端尚未修复）时：必须常驻一条诚实横幅，写明「口径有缺陷、数字暂不可用」
     并给出两条原因，而不是只把颜色从红改成灰；
  2. 新字段存在时：优先展示 directional_accuracy，且必须与 baseline_always_bullish
     **并排对照**（没有基线的命中率无法解释），并处理 no_view_rate / sample_adequate；
  3. 不得再按 70 / 50 阈值把旧口径数字染成红/绿 KPI。

摘掉横幅或摘掉新字段判断，本文件立刻转红（故障注入）。
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_ANALYSIS = _REPO / "pages" / "analysis.js"

BANNER_TITLE = "当前评分口径存在已知缺陷，数字暂不可用"
CAUSE_WINDOW = "取数窗口与预测日无关"
CAUSE_NEUTRAL = "中性类几乎无法判对"


def _src() -> str:
    assert _ANALYSIS.exists(), f"找不到 {_ANALYSIS}"
    return _ANALYSIS.read_text(encoding="utf-8")


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
    """命中率必须与「永远看多」基线并排 —— 没有基线的命中率无法解释。"""
    src = _src()
    assert "有观点时命中率" in src, "未展示 directional_accuracy"
    assert "永远看多的基线" in src, "未把 baseline_always_bullish 并排展示"
    assert "相对基线" in src, "未给出「命中率 - 基线」的对照差值"


def test_shows_warning_when_below_baseline():
    """跑不赢基线时必须明说「不构成优势」，不许把持平/落后渲染成正面。"""
    src = _src()
    assert "未跑赢「永远看多」基线" in src, "低于基线时缺少如实警示"
    assert "命中率无法解释" in src, "缺少基线时缺少「无法解释」提示"


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
