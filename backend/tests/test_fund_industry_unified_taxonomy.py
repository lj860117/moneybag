"""
P0-4 回归测试：选基行业分类必须统一 taxonomy，禁止退化成兜底标签

背景（P0-4 缺陷）：
    持仓侧用重仓股反推出「半导体 / AI科技」这类具体行业，
    候选侧却走名称关键词匹配，匹配不上就退化成兜底值「📈 主动混合」。
    两套 taxonomy 永不交叠 → overlap 恒为 0 → 候选永远被判成「🟢 新敞口」，
    并输出「你目前没有📈 主动混合方向」这种自相矛盾的话
    （实际 77% 的候选就是主动混合）。

本测试锁死三件事：
    1. 两侧使用同一 taxonomy 时，重叠度必须能真实算出（而非恒 0）
    2. 兜底/风格类标签必须被 is_generic_tag 识别，不得参与「你没有 X 方向」判断
    3. 重叠明细要说出「撞在哪」，而不是基金最大的那个赛道
"""
import pytest

from services import fund_industry as fi


FALLBACK = fi.FALLBACK_TAG  # "📈 主动混合"


# ── 1. 原 bug 场景：taxonomy 统一前后对比 ────────────────────────────

def test_original_bug_scenario_overlap_becomes_computable():
    """核心回归：统一 taxonomy 后重叠度必须 > 0，而不是恒 0 判成新敞口"""
    my_mix = {"半导体": 0.6, "AI科技": 0.4}

    cand_before = {FALLBACK: 1.0}          # 修复前：候选退化成兜底
    cand_after = {"半导体": 0.8, "通信": 0.2}  # 修复后：重仓股反推，同一 taxonomy

    # 旧行为：集合不相交 → 恒 0 → 恒判「新敞口」，这是 bug 的根源
    assert fi.overlap_score(cand_before, my_mix) == 0.0
    # 新行为：真实重叠 = min(0.6, 0.8) = 0.6
    assert fi.overlap_score(cand_after, my_mix) == pytest.approx(0.6, abs=1e-3)


def test_fallback_tag_must_not_drive_direction_judgement():
    """兜底/风格类标签不得参与「你目前没有 X 方向」这类判断"""
    assert fi.is_generic_tag(FALLBACK) is True
    assert fi.is_generic_tag("🚀 成长") is True
    assert fi.is_generic_tag("📊 宽基指数") is True
    # 具体行业必须能参与判断
    assert fi.is_generic_tag("半导体") is False
    assert fi.is_generic_tag("AI科技") is False


def test_concrete_industry_is_not_generic():
    """具体行业标签必须可用于方向判断，否则「没有 X 方向」会误触发"""
    assert fi.is_generic_tag("医药生物") is False
    assert fi.is_generic_tag("新能源") is False


# ── 2. overlap_score 数学正确性 ──────────────────────────────────────

def test_overlap_score_equals_sum_of_min():
    a = {"半导体": 0.5, "医药": 0.3, "金融": 0.2}
    b = {"半导体": 0.2, "医药": 0.6, "消费": 0.2}
    expect = min(0.5, 0.2) + min(0.3, 0.6) + min(0.2, 0.0) + min(0.0, 0.2)
    assert fi.overlap_score(a, b) == pytest.approx(expect, abs=1e-3)


def test_overlap_partial_scales_with_smaller_side():
    """候选半导体 0.8 vs 持仓半导体 0.35 → 重叠 0.35（买入会显著加重集中度）

    注意两侧都会先归一化，因此必须写成完整分布，
    单元素 mix 归一后恒为 1.0，测不出「取小」语义。
    """
    cand = {"半导体": 0.8, "医药": 0.2}
    mine = {"半导体": 0.35, "消费": 0.65}
    assert fi.overlap_score(cand, mine) == pytest.approx(0.35, abs=1e-3)


def test_single_tag_mix_overlap_is_full_when_both_pure():
    """负面控制：两侧都是纯半导体 → 归一化后重叠 100%，不是 80%"""
    assert fi.overlap_score({"半导体": 0.8}, {"半导体": 0.35}) == pytest.approx(1.0, abs=1e-3)


def test_overlap_zero_when_truly_disjoint():
    """负面控制：真正无交集时必须为 0，不能为了「不判新敞口」而虚高"""
    assert fi.overlap_score({"医药": 1.0}, {"半导体": 1.0}) == 0.0


def test_overlap_one_when_identical():
    assert fi.overlap_score({"半导体": 0.7, "通信": 0.3},
                            {"半导体": 0.7, "通信": 0.3}) == pytest.approx(1.0, abs=1e-3)


def test_overlap_safe_on_empty_input():
    """负面控制：空输入不得抛异常，也不得返回非零"""
    assert fi.overlap_score({}, {"半导体": 1.0}) == 0.0
    assert fi.overlap_score({"半导体": 1.0}, {}) == 0.0
    assert fi.overlap_score(None, None) == 0.0


# ── 3. 重叠明细：说出「撞在哪」而非最大赛道 ──────────────────────────

def test_breakdown_reports_where_they_collide_not_biggest_track():
    """基金主赛道是金融(40%)，但真正撞上持仓的是 AI科技，明细必须说 AI科技"""
    cand = {"金融": 0.4, "AI科技": 0.35, "通信": 0.25}
    mine = {"AI科技": 0.6, "半导体": 0.4}

    top_tag, top_val = fi.overlap_breakdown(cand, mine, limit=1)[0]
    assert top_tag == "AI科技", f"应报撞上的赛道 AI科技，实际报了 {top_tag}"
    assert top_val == pytest.approx(0.35, abs=1e-3)


def test_breakdown_sorted_desc_and_respects_limit():
    cand = {"金融": 0.4, "AI科技": 0.35, "通信": 0.25}
    mine = {"AI科技": 0.6, "半导体": 0.4}
    items = fi.overlap_breakdown(cand, mine, limit=3)
    vals = [v for _, v in items]
    assert vals == sorted(vals, reverse=True)
    assert len(items) <= 3


def test_breakdown_empty_on_no_overlap():
    assert fi.overlap_breakdown({"医药": 1.0}, {"半导体": 1.0}) == []


# ── 4. normalize_mix ─────────────────────────────────────────────────

def test_normalize_mix_scales_to_one():
    m = fi.normalize_mix({"a": 3, "b": 1})
    assert sum(m.values()) == pytest.approx(1.0)
    assert m["a"] == pytest.approx(0.75)


def test_normalize_mix_drops_zero_and_negative():
    assert fi.normalize_mix({"a": 1, "b": 0}) == {"a": 1.0}


def test_normalize_mix_safe_on_empty():
    assert fi.normalize_mix({}) == {}
    assert fi.normalize_mix(None) == {}
    assert fi.normalize_mix({"a": 0}) == {}


# ── 5. 分类降级必须诚实（不得编造行业） ──────────────────────────────

def test_classify_fund_returns_fallback_instead_of_fabricating(monkeypatch):
    """拿不到重仓股数据时必须退回兜底，而不是编一个行业出来"""
    monkeypatch.setattr(fi, "portfolio_industry_mix", lambda code: {})
    monkeypatch.setattr(fi, "name_based_tag", lambda name: {})

    res = fi.classify_fund(code="000000", name="某某混合A")
    assert res["tag"] == FALLBACK
    # 关键：兜底必须被标记，下游据此不参与方向判断
    assert fi.is_generic_tag(res["tag"]) is True
    assert res.get("generic") is True


def test_classify_fund_uses_holdings_not_name_when_available(monkeypatch):
    """有重仓股数据时必须走重仓股反推，返回具体行业而非兜底。

    这是防「候选退化成 📈 主动混合」的关键断言：若有人把重仓股路径短路掉，
    候选又会全部退化成兜底、与持仓 taxonomy 脱节、重叠恒为 0，P0-4 复发。
    """
    monkeypatch.setattr(fi, "name_based_tag", lambda name: {})
    monkeypatch.setattr(
        fi, "portfolio_industry_mix",
        lambda code: {"mix": {"💎 半导体": 0.62, "🏭 高端制造": 0.21},
                      "end_date": "2026-06-30", "top": []},
    )

    res = fi.classify_fund(code="001593", name="某某成长混合")
    assert res["tag"] == "💎 半导体"
    assert res["source"] == "portfolio"
    assert res["generic"] is False          # 具体行业，可参与方向判断
    assert fi.is_generic_tag(res["tag"]) is False
    # mix 必须完整保留：下游用它算重叠度
    assert res["mix"]["💎 半导体"] == pytest.approx(0.62, abs=1e-6)


def test_dispersed_fund_still_keeps_mix_for_overlap(monkeypatch):
    """高度分散的基金标签虽为「主动混合」，但 mix 必须保留供重叠计算。

    P0-4 的病根正在于此：旧逻辑一旦退化成兜底就丢掉行业分布，
    导致持仓与候选集合永不相交、重叠恒为 0、永远判成「新敞口」。
    """
    monkeypatch.setattr(fi, "name_based_tag", lambda name: {})
    monkeypatch.setattr(
        fi, "portfolio_industry_mix",
        lambda code: {"mix": {"💎 半导体": 0.15, "🏭 高端制造": 0.12,
                              "🧪 医药生物": 0.11},
                      "end_date": "2026-06-30", "top": []},
    )

    res = fi.classify_fund(code="001593", name="某某混合A")
    # 第一大行业 0.15 < _MIN_DOMINANT_WEIGHT(0.20) → 诚实标为主动混合
    assert res["tag"] == FALLBACK
    assert res["generic"] is True
    # 但行业分布必须还在，否则下游算不出重叠
    assert res["mix"], "分散基金也必须保留 mix，否则重叠度恒为 0（P0-4 病根）"
    assert fi.overlap_score(res["mix"], {"💎 半导体": 0.6, "AI科技": 0.4}) > 0
