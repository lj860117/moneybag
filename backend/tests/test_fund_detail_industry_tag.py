"""
fund_detail.py industry_tag 归属回归测试（v9.9.38 二次修复）
==========================================================
背景（"改对了但没生效"）：
  v9.9.38 修了 `_enrich_detail_with_holding` 里 `get_fund_industry` 调用的
  **三重叠错**（传 2 参给只收 1 参的函数 → TypeError；传 code 不当 name；
  拿 {"tag","desc"} 的 dict 当字符串）。那处代码确实改对了。

  但 QA 实测生产上 `industry_tag` 依然是 `None`。根因是**第二道门**，
  且它在被修的那段**之前**：

      def _enrich_detail_with_holding(detail, code, user_id):
          ...
          user = load_user(user_id)
          if not user: return detail
          holdings = ...portfolio.holdings...
          for h in holdings:
              if h.get("code") == code: holding = h; break
          if not holding: return detail   # ← 第二道门：真实用户在这里就返回了

  真实用户的 `load_user(uid).portfolio.holdings` 是 `[]`（真实持仓存在
  `data/fund_holdings_{uid}.json`），所以 `holding` 恒为 None → 在第二道门
  `return detail`，后面被修好的 industry 段永远执行不到。

正确认知：`industry_tag` **只依赖基金名称，与用户持仓毫无关系**，它根本
不该放在"用户持仓增强"函数里、更不该在两道用户相关早退之后。

修法：把它挪到 `fund_detail()` 里 `result` 构建完之后、`shared_result =
dict(result)` 之前，放进**共享结果**。共享缓存被**所有**调用路径读：
  - 带 userId：`api/fund_detail.py` 的 `if userId:` 分支
  - 不带 userId：`else` 分支直接返回共享缓存 ← 前端 `pages/insight-fund.js:513`
    的 fallback（`fetch('/fund/detail/'+c)` 不带 userId）走的就是这条
所以这一处改动覆盖全部路径，前端一行都不用改。

本文件测什么：
  - 不带 userId（QA 实测暴露问题的路径）→ industry_tag 非空且是字符串
  - 带 userId → 同样有 industry_tag
  - 名称无任何行业关键词 → **不设置** industry_tag（防修过头；"其他" 兜底
    是前端职责，见 pages/insight-fund.js:515）
  - 共享缓存里确实带 industry_tag（证明不带 userId 的缓存读路径也拿得到）
  - 结构守门：industry 增强必须在两道用户早退**之外**，避免有人再挪回去

离线：所有 tushare/akshare 网络调用均被 mock（参考 test_fund_detail_ak_timeout.py）。
"""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

BACKEND_DIR = Path(__file__).parent.parent

# 名字里带明确行业关键词的真实基金 → 命中 FUND_INDUSTRY_MAP 的 "半导体" 桶
CODE_WITH_INDUSTRY = "008888"
NAME_WITH_INDUSTRY = "华夏中证半导体ETF联接A"
EXPECTED_TAG_SUBSTR = "半导体"

# 名字里不含任何行业关键词（见 services/industry_templates.py::FUND_INDUSTRY_MAP）
CODE_NO_INDUSTRY = "009999"
NAME_NO_INDUSTRY = "测试基金无行业关键词"


@pytest.fixture
def fd(tmp_path, monkeypatch):
    """每个测试用独立 DATA_DIR + 重新加载 config/api.fund_detail，
    并 mock 掉全部网络依赖，保证离线且互不污染。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import config
    importlib.reload(config)
    import api.fund_detail as fd
    importlib.reload(fd)
    yield fd


def _install_offline_mocks(monkeypatch, fd, name_lookup):
    """把 fund_detail() 会碰到的一切网络来源都替换成安全的离线桩。

    name_lookup: code -> 基金名（决定 industry 命中与否）。
    """
    # --- services.tushare_data ---
    import services.tushare_data as tushare_data
    monkeypatch.setattr(tushare_data, "get_fund_manager", lambda code: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_portfolio", lambda code: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_share", lambda ts_code, days=10: {"available": False})
    monkeypatch.setattr(tushare_data, "get_fund_extra_info_ak", lambda code: {})
    monkeypatch.setattr(tushare_data, "is_configured", lambda: False)
    monkeypatch.setattr(tushare_data, "get_fund_nav", lambda *a, **k: {})
    monkeypatch.setattr(tushare_data, "_call_tushare", lambda *a, **k: [])

    # --- services.fund_rank ---
    import services.fund_rank as fund_rank
    monkeypatch.setattr(
        fund_rank, "get_fund_dynamic_info",
        lambda code: {"code": code, "name": name_lookup.get(code, code),
                      "nav": 1.234, "returns": {}, "fee": ""},
    )
    monkeypatch.setattr(fund_rank, "_load_fund_rank_data", lambda *a, **k: None)

    # --- services.fund_risk_adjusted（近3年风险调整指标，非本测试关注点）---
    try:
        import services.fund_risk_adjusted as fra
        monkeypatch.setattr(fra, "compute_risk_adjusted_metrics", lambda *a, **k: None)
        monkeypatch.setattr(fra, "set_risk_adjusted_cache", lambda *a, **k: None)
    except Exception:
        pass

    # --- akshare 超时保护：全部走 ak_call，桩成 None 即安全降级 ---
    try:
        import services.utils as utils
        monkeypatch.setattr(utils, "ak_call", lambda *a, **k: None)
    except Exception:
        pass

    # --- fund_detail 模块内函数 ---
    monkeypatch.setattr(fd, "_get_nav_history_cached", lambda *a, **k: [])

    # --- api.signals 的走势预估（另一 worker 正在改 signals.py，若导入失败则
    #     该段本来就在 try/except 内，不会影响本测试）---
    try:
        import api.signals as signals
        monkeypatch.setattr(signals, "_enrich_trend_forecast", lambda *a, **k: None, raising=False)
    except Exception:
        pass


# ============================================================
# 1. 核心用例：不带 userId（QA 实测暴露问题的路径）
# ============================================================

def test_fund_detail_without_userid_exposes_industry_tag(fd, monkeypatch):
    """不带 userId 调 `/fund/detail/{code}`，返回里 industry_tag 必须非空字符串。

    这正是前端 pages/insight-fund.js:513 的 fallback 调用形式，也是 QA
    在生产上看到 industry_tag 为 None 的那条路径。
    """
    _install_offline_mocks(monkeypatch, fd, {CODE_WITH_INDUSTRY: NAME_WITH_INDUSTRY})

    result = fd.fund_detail(CODE_WITH_INDUSTRY)  # 注意：不传 userId

    tag = result.get("industry_tag")
    assert isinstance(tag, str) and tag, f"industry_tag 应为非空字符串，实际 {tag!r}"
    assert EXPECTED_TAG_SUBSTR in tag, f"应命中『半导体』行业桶，实际 {tag!r}"
    assert isinstance(result.get("industry_desc"), str) and result["industry_desc"]


def test_fund_detail_shared_cache_carries_industry_tag(fd, monkeypatch):
    """industry 增强必须落在**共享缓存**里 —— 这是"不带 userId 路径也拿得到"
    的根本原因。直接断言写进共享缓存键的值就带 industry_tag。"""
    _install_offline_mocks(monkeypatch, fd, {CODE_WITH_INDUSTRY: NAME_WITH_INDUSTRY})

    fd.fund_detail(CODE_WITH_INDUSTRY)  # 无 userId，触发全新构建

    shared = fd._get_cached(f"fund_detail_{CODE_WITH_INDUSTRY}", allow_stale=True)
    assert shared is not None, "共享详情缓存应已写入"
    assert shared.get("industry_tag"), "共享缓存里必须带 industry_tag"
    assert EXPECTED_TAG_SUBSTR in shared["industry_tag"]


def test_fund_detail_reads_industry_tag_from_shared_cache_without_userid(fd, monkeypatch):
    """两阶段复现生产路径：先构建落共享缓存，再断网重读。

    第二阶段把所有网络桩换成"一旦被调用就报错"，若仍能拿到 industry_tag，
    说明它确实存在于共享缓存、且不带 userId 的读路径（else 分支）直接命中，
    全程不需要重算、不需要用户持仓。
    """
    _install_offline_mocks(monkeypatch, fd, {CODE_WITH_INDUSTRY: NAME_WITH_INDUSTRY})

    # 阶段 1：全新构建，落共享缓存
    first = fd.fund_detail(CODE_WITH_INDUSTRY)
    assert first.get("industry_tag"), "阶段1应已算出 industry_tag"

    # 阶段 2：网络全断，只能走共享缓存
    import services.tushare_data as tushare_data
    import services.fund_rank as fund_rank

    def _boom(*_a, **_k):
        raise AssertionError("不应重算：共享缓存命中应直接返回")

    monkeypatch.setattr(tushare_data, "get_fund_manager", _boom)
    monkeypatch.setattr(fund_rank, "get_fund_dynamic_info", _boom)

    second = fd.fund_detail(CODE_WITH_INDUSTRY)  # 同样不传 userId

    assert second.get("industry_tag") == first.get("industry_tag")
    assert EXPECTED_TAG_SUBSTR in second["industry_tag"]


# ============================================================
# 2. 带 userId 同样有 industry_tag
# ============================================================

def test_fund_detail_with_userid_exposes_industry_tag(fd, monkeypatch):
    """带 userId 调用同样要拿到 industry_tag（不依赖是否持仓）。"""
    _install_offline_mocks(monkeypatch, fd, {CODE_WITH_INDUSTRY: NAME_WITH_INDUSTRY})

    result = fd.fund_detail(CODE_WITH_INDUSTRY, userId="TesterNoHolding")

    tag = result.get("industry_tag")
    assert isinstance(tag, str) and tag, f"带 userId 时 industry_tag 应为非空字符串，实际 {tag!r}"
    assert EXPECTED_TAG_SUBSTR in tag


# ============================================================
# 3. 防修过头：无行业关键词的基金 → 不设置 industry_tag
# ============================================================

def test_fund_detail_without_keyword_sets_no_industry_tag(fd, monkeypatch):
    """名称无任何行业关键词的基金，不应被强行打上 industry_tag。

    注意：不清空、也不设成 "其他" —— "其他" 兜底是前端职责
    （pages/insight-fund.js:515 的 `dd.industry_tag || '其他'`）。
    """
    from services.industry_templates import get_fund_industry
    # 先验证测试前提：这个名字确实匹配不到任何行业桶
    assert get_fund_industry(NAME_NO_INDUSTRY) == {}, "测试前提失效：该名不应命中任何行业关键词"

    _install_offline_mocks(monkeypatch, fd, {CODE_NO_INDUSTRY: NAME_NO_INDUSTRY})

    result = fd.fund_detail(CODE_NO_INDUSTRY)

    assert "industry_tag" not in result, (
        f"无行业关键词的基金不应设置 industry_tag，实际 {result.get('industry_tag')!r}"
    )


# ============================================================
# 4. 结构守门：industry 增强必须在两道用户早退之外
# ============================================================

def test_industry_enrichment_is_outside_holding_enrichment_function():
    """回归锁定：industry 增强只应出现在 fund_detail() 主体里（共享结果段），
    绝不能再被挪回 _enrich_detail_with_holding（那里有两道 `return detail`
    早退，真实用户永远走不到）。"""
    src = (BACKEND_DIR / "api" / "fund_detail.py").read_text(encoding="utf-8")

    # get_fund_industry 的调用点全文件应只有 1 处
    assert src.count("get_fund_industry(") == 1, "get_fund_industry 调用点应唯一"

    # 该调用必须位于共享结果段（在 shared_result = dict(result) 之前）
    idx_call = src.index("get_fund_industry(")
    idx_shared = src.index("shared_result = dict(result)")
    assert idx_call < idx_shared, "industry 增强必须在 shared_result 构建之前"

    # 且必须早于 _enrich_detail_with_holding 的定义（即不在其函数体内）
    idx_helper = src.index("def _enrich_detail_with_holding(")
    assert idx_call < idx_helper, "industry 增强不得出现在 _enrich_detail_with_holding 内"

    # 反向确认：_enrich_detail_with_holding 函数体里不再有 industry 相关逻辑
    helper_body = src[idx_helper:]
    assert "get_fund_industry" not in helper_body, "持有增强函数内不应再有 industry 逻辑（死代码）"
