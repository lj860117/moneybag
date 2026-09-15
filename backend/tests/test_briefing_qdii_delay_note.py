"""
晨报「QDII 净值披露延迟」标注回归测试（v9.9.38）
==============================================

用户报的真 bug（2026-09-14 企业微信质检告警，1 真 2 假中的那 1 条真）::

    ⚠️ QDII 基金未标注 T+1 延迟

含义：晨报正文提到了 QDII 基金（「浦银安盛全球智能科技(QDII) 亏 24.3%」），
但全文没有一处说明 QDII 净值披露有延迟 —— 用户会以为那个涨跌幅是今天的，
实际 QDII 投境外市场，**T+2** 才披露。

两个必须同时修、且很容易修错一半的点
------------------------------------
1. **告警文案本身是错的**：写的是 "T+1"，实际是 **T+2**（境外收盘晚 + 时差
   + 汇率折算）。只按告警文案去正文里塞 "T+1"，等于把事实说反。
2. **判据漏认**：旧判据是 `"T+1" not in content and "延迟" not in content`。
   生成层**现成**的一句文案是「QDII 净值滞后 2 天」
   （services/fund_signal/render.py）——**「滞后」既不含「延迟」也不含
   T+1**，照抄过去质检照样报。本文件专门有一条用例钉死这个坑。

为什么不用改 LLM prompt 来解决
------------------------------
`diag` 是 `_call_v3(prompt, 800)` 的**自由文本**，不是模板拼的。把「记得
标注 T+2」写进 prompt，模型时说时不说 —— 告警就是这么间歇性复发的。所以
修法是：在**持仓速览块**里追加一行**写死的文案**，只要当日持仓含 QDII
就必然出现，不依赖模型心情。

判据为什么走 services.fund_taxonomy
------------------------------------
仓库里曾经并存 4 套互相矛盾的 QDII 判据（fund_type 恒空 / 24 词并集 554 只
误判 / 7 词风格标签 / 前端正则判币种）。`services/fund_taxonomy.is_qdii_fund`
是 v9.9.37 上线的**唯一真源**。本文件用**活断言**
`nw.is_qdii_fund is ft.is_qdii_fund` 钉死接线：哪天有人把 import 换成内联
副本，这条立刻红 —— 否则故障注入会打空、测试恒绿。

全部离线：step_r1_phase2 的 IO（用户表 / 持仓 / LLM）全部 monkeypatch 打桩，
质检只读 tmp_path 下的临时文件。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import scripts.night_worker as nw                        # noqa: E402
from scripts.daily_push_quality_check import check_data_source  # noqa: E402
from services import fund_taxonomy as ft                 # noqa: E402

# 告警文案写死在这里（与 check_data_source 里的一致）。不 import 是为了让
# 有人改了实现文案时这里**变红**，而不是跟着一起漂。
QDII_ISSUE = "⚠️ QDII 基金未标注 T+2 披露延迟"

UID = "LeiJiang"
USER_NAME = "LeiJiang"

# LeiJiang 真实持仓里的两只 QDII（法定名称带 (QDII) 后缀）
# + 160213 国泰纳斯达克100指数：雪球「基金类型=QDII-股票」的真 QDII，但
#   AKShare 简称截掉了 (QDII)，只有关键词分支能抓到 —— 放它是为了证明
#   night_worker 用的是**并集**判据，不是自己写的 `name.contains("QDII")`。
QDII_FUNDS = [
    {"code": "006555", "name": "浦银安盛全球智能科技(QDII)"},
    {"code": "005698", "name": "华夏全球科技先锋混合(QDII)"},
    {"code": "160213", "name": "国泰纳斯达克100指数"},
]

# 纯 A 股持仓：一只都不能被判成 QDII（"沪深300" / "兴全合润" 都不在白名单）
A_SHARE_FUNDS = [
    {"code": "100038", "name": "富国沪深300指数增强A"},
    {"code": "163406", "name": "兴全合润混合A"},
]

DIAG_WITH_QDII = (
    "总评：科技成长为主，整体小幅浮盈。\n"
    "风险：浦银安盛全球智能科技(QDII) 与 华夏全球科技先锋混合(QDII) 风格重合。\n"
    "建议：维持定投，暂不追加。"
)
DIAG_NO_QDII = (
    "总评：A股宽基为主，整体微亏。\n"
    "风险：沪深300 敞口集中。\n"
    "建议：维持定投。"
)
ADVICE_SECTION = "\n【操作建议】\n• 按原节奏定投，不做额外操作"


# ============================================================
# 工具
# ============================================================

def _write(tmp_path, content, name="push.txt") -> str:
    """把正文写成一份推送存档文件，返回路径（质检只吃文件路径）。"""
    p = Path(tmp_path) / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _full_briefing(block: str) -> str:
    """给持仓速览块套一层晨报头，还原 step_generate_products 的拼法。"""
    return f"📊 2026-09-14 钱袋子晨报\n\n📊 【A股温度】\n市场情绪: 中性\n\n{block}"


def _issues(tmp_path, block: str) -> list:
    return check_data_source(_write(tmp_path, _full_briefing(block)))


@pytest.fixture
def phase2_run(monkeypatch):
    """打桩跑真实的 step_r1_phase2()，返回 results。

    只桩掉 IO（用户表 / 持仓加载 / LLM / 温度计），QDII 判定与 results
    组装全部走生产代码 —— 否则测的是测试自己。
    """

    def _run(funds, stocks=None):
        import services.fund_monitor as fm
        import services.stock_monitor as sm

        monkeypatch.setattr(nw, "_load_profiles",
                            lambda: [{"id": UID, "name": USER_NAME}], raising=True)
        monkeypatch.setattr(nw, "_call_v3", lambda *a, **k: DIAG_WITH_QDII,
                            raising=True)
        monkeypatch.setattr(nw, "_build_portfolio_thermometer", lambda uid: "",
                            raising=True)
        monkeypatch.setattr(fm, "load_fund_holdings", lambda uid: list(funds),
                            raising=True)
        monkeypatch.setattr(fm, "scan_all_fund_holdings",
                            lambda uid: {"holdings": []}, raising=True)
        monkeypatch.setattr(sm, "load_stock_holdings",
                            lambda uid: list(stocks or []), raising=True)
        results = nw.step_r1_phase2()
        assert UID in results, (
            f"step_r1_phase2 没产出 {UID} 的结果（多半是内部抛异常被吞了）: {results}")
        return results

    return _run


# ============================================================
# A. 接线：必须走共享判据，禁止内联副本
# ============================================================

def test_night_worker_uses_shared_qdii_criterion():
    """活断言：`is_qdii_fund` 必须是 services.fund_taxonomy 里那个函数对象。

    换成内联副本（哪怕行为一致）这里就红 —— 那种"看起来没坏"的副本正是
    仓库里曾经 4 套口径的来源，也是故障注入会打空的元凶。
    """
    assert nw.is_qdii_fund is ft.is_qdii_fund, (
        "night_worker 没用共享判据 —— 又多了一套 QDII 口径")


def test_night_worker_does_not_define_its_own_qdii_keywords():
    """回潮守卫：night_worker.py 里不许出现自定义 QDII 关键词字面量。

    逐个词都是有全市场实测精度背书的（见 services/fund_taxonomy.py），
    在别处顺手加词等于把 554 只误判的教训忘掉。
    """
    import re
    src = (BACKEND_DIR / "scripts" / "night_worker.py").read_text(encoding="utf-8")
    # 只查代码，不查注释（本文件注释里就在解释这些词为什么不能用）
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "is_qdii_fund" in code, "night_worker.py 里找不到 is_qdii_fund 调用"
    # 只查「QDII 判据型」的赋值：变量名含**大写** QDII + 右侧是列表/元组字面量。
    #
    # 两处刻意的收窄，都是被误报逼出来的：
    #   1. 不按关键词字面量全文件扫 —— 本文件有张**指数代码→中文名**映射表
    #      （("nasdaq", "纳斯达克") / ("spx", "标普500")，供隔夜行情展示），
    #      与 QDII 判据无关，按字面量扫会把它当成"又一套口径"。
    #   2. 大小写敏感 —— 否则 `clean = [... for n in (qdii_names or [])]`
    #      这种普通变量（小写 qdii_names）也会被当成判据定义。
    #
    # 已知局限：变量名完全不含大写 QDII 的（如 KW = [...]）扫不到，由活断言
    # test_night_worker_uses_shared_qdii_criterion 兜底（只认函数对象同一性，
    # 内联副本必红）。
    suspicious = [l.strip() for l in code.splitlines()
                  if "QDII" in l and re.search(r'=\s*[\(\[\{]', l)]
    assert not suspicious, (
        f"night_worker.py 自己定义了 QDII 关键词判据: {suspicious[:2]} —— "
        f"判据必须 import services.fund_taxonomy")


# ============================================================
# B. step_r1_phase2 产出 qdii_names
# ============================================================

def test_phase2_records_qdii_names(phase2_run):
    """持仓含 QDII 时，results 里必须带上它们的名称。"""
    results = phase2_run(QDII_FUNDS)

    assert results[UID]["qdii_names"] == [
        "浦银安盛全球智能科技(QDII)",
        "华夏全球科技先锋混合(QDII)",
        "国泰纳斯达克100指数",
    ], results[UID]["qdii_names"]


def test_phase2_qdii_names_empty_for_a_share_only(phase2_run):
    """纯 A 股持仓 → qdii_names 为空（否则会给无关用户加噪音）。"""
    results = phase2_run(A_SHARE_FUNDS)

    assert results[UID]["qdii_names"] == [], results[UID]["qdii_names"]
    assert results[UID]["fund_count"] == 2


def test_phase2_qdii_names_survive_json_roundtrip(phase2_run, tmp_path):
    """qdii_names 会随 diagnosis_{uid}.json 带去下游 —— 存盘后必须还在。

    step_r1_phase2 已经把 data 写进了 config.DATA_DIR/night_worker（conftest
    已隔离到会话临时目录），这里直接读回来验证，不另造一份序列化逻辑。
    """
    import config

    results = phase2_run(QDII_FUNDS)
    cached = Path(config.DATA_DIR) / "night_worker" / f"diagnosis_{UID}.json"
    assert cached.exists(), f"诊断缓存没落盘: {cached}"

    import json
    on_disk = json.loads(cached.read_text(encoding="utf-8"))
    assert on_disk["qdii_names"] == results[UID]["qdii_names"]
    assert on_disk["qdii_names"], "存盘后 qdii_names 丢了，下游就标不出来"


# ============================================================
# C. 生成的简报：有 QDII 必须标、没 QDII 不许标
# ============================================================

def test_briefing_with_qdii_carries_delay_note(tmp_path, phase2_run):
    """核心用例：持仓含 QDII → 简报含「延迟/T+2」且质检不报 QDII 那条。"""
    results = phase2_run(QDII_FUNDS)
    block = nw._render_holdings_block(
        USER_NAME, DIAG_WITH_QDII, ADVICE_SECTION, results[UID]["qdii_names"])

    assert "延迟" in block, f"标注里没有「延迟」二字，质检判据照样会报:\n{block}"
    assert "T+2" in block, f"标注里没有 T+2:\n{block}"
    assert "浦银安盛全球智能科技(QDII)" in block, "标注没有点名具体基金"

    issues = _issues(tmp_path, block)
    assert QDII_ISSUE not in issues, f"质检仍报 QDII 未标注: {issues}"
    assert issues == [], f"不该有任何质检问题: {issues}"


def test_briefing_without_qdii_has_no_delay_note(tmp_path, phase2_run):
    """持仓不含 QDII → 一个字都不许多说（避免给无关用户加噪音）。"""
    results = phase2_run(A_SHARE_FUNDS)
    block = nw._render_holdings_block(
        USER_NAME, DIAG_NO_QDII, ADVICE_SECTION, results[UID]["qdii_names"])

    assert "T+2" not in block, f"无 QDII 却标了 T+2:\n{block}"
    assert "延迟" not in block, f"无 QDII 却提了延迟:\n{block}"
    assert "QDII" not in block, f"无 QDII 却提了 QDII:\n{block}"
    # 原有结构不能被动过（标题 / 诊断 / 建议 / 免责声明都在）
    assert f"📋 【{USER_NAME} 持仓速览】" in block
    assert DIAG_NO_QDII in block
    assert block.endswith("⚠️ AI建议仅供参考，不构成投资建议")


def test_empty_qdii_names_yields_empty_note():
    """_build_qdii_delay_note 的边界：空 / None 都返回空串，不留空行。"""
    assert nw._build_qdii_delay_note([]) == ""
    assert nw._build_qdii_delay_note(None) == ""
    assert nw._build_qdii_delay_note(["", "  "]) == ""


def test_note_truncates_long_name_lists():
    """QDII 很多时只点名前 3 只 + 「等」，一行不至于塞满十几只基金名。"""
    names = [f"QDII基金{i}号(QDII)" for i in range(1, 6)]
    note = nw._build_qdii_delay_note(names)

    assert note.count("、") == 2, f"应只列 3 只（2 个顿号）: {note}"
    assert note.endswith("等）净值披露延迟约 T+2，文中涨跌幅不是今日实时数据")
    assert "QDII基金4号(QDII)" not in note and "QDII基金5号(QDII)" not in note
    # 恰好 3 只时不加「等」
    assert "等" not in nw._build_qdii_delay_note(names[:3])


# ============================================================
# D. 质检侧：告警文案与判据
# ============================================================

def test_issue_text_says_t_plus_two_not_t_plus_one(tmp_path):
    """钉死文案修正：告警说的是 T+2，不能再把修的人往 T+1 上带。"""
    block = nw._render_holdings_block(USER_NAME, DIAG_WITH_QDII, ADVICE_SECTION, [])

    issues = _issues(tmp_path, block)
    assert QDII_ISSUE in issues, f"没标注时本应告警，实际 {issues}"
    assert "T+2" in QDII_ISSUE
    assert "T+1" not in QDII_ISSUE


def test_lag_wording_does_not_satisfy_the_rule(tmp_path):
    """措辞坑：现有那句「QDII 净值滞后 2 天」**过不了**质检 —— 必须实测确认。

    「滞后」既不含「延迟」也不含 T+1/T+2。这条用例存在的意义就是防止有人
    觉得"已经有滞后文案了"而不再加标注。
    """
    block = nw._render_holdings_block(
        USER_NAME, DIAG_WITH_QDII, ADVICE_SECTION, []) + \
        "\n（QDII 净值滞后 2 天）"

    assert QDII_ISSUE in _issues(tmp_path, block), (
        "「滞后」文案竟然通过了质检 —— 判据被改成认「滞后」了？")


def test_t_plus_two_alone_satisfies_the_rule(tmp_path):
    """判据放宽：只写 T+2、不写「延迟」二字也算合规（新增认的字面量）。"""
    block = nw._render_holdings_block(
        USER_NAME, DIAG_WITH_QDII, ADVICE_SECTION, []) + "\n（QDII 净值 T+2 披露）"

    assert QDII_ISSUE not in _issues(tmp_path, block)


def test_legacy_t_plus_one_archive_still_passes(tmp_path):
    """判据只放宽不收紧：历史存档里标了 "T+1" 的仍判合规。

    收紧会让上百份历史存档一夜之间集体变 FAIL —— 那是一轮新的误报。
    """
    block = nw._render_holdings_block(
        USER_NAME, DIAG_WITH_QDII, ADVICE_SECTION, []) + "\n（QDII 净值 T+1 披露）"

    assert QDII_ISSUE not in _issues(tmp_path, block)


def test_no_qdii_mention_never_triggers_the_rule(tmp_path):
    """正文里没有 (QDII) 提及时，规则不该触发（不是恒真告警）。"""
    block = nw._render_holdings_block(USER_NAME, DIAG_NO_QDII, ADVICE_SECTION, [])

    assert QDII_ISSUE not in _issues(tmp_path, block)


# ============================================================
# E. 故障注入：证明上面这些用例是活的，不是恒绿
# ============================================================

def test_fault_injection_criterion_disabled_kills_the_note(tmp_path, phase2_run,
                                                           monkeypatch):
    """注入 1：把 is_qdii_fund 换成恒 False → 整条链断，质检立刻报回来。

    这是最可能的退化形态（有人"顺手"内联一份判据或改坏判据）。打在
    `nw` 上而不是 `ft` 上：nw 里那个名字就是它实际调用的那个。
    """
    monkeypatch.setattr(nw, "is_qdii_fund", lambda item: False, raising=True)

    results = phase2_run(QDII_FUNDS)
    assert results[UID]["qdii_names"] == [], (
        "故障注入失效：判据恒 False 后 qdii_names 仍非空")

    block = nw._render_holdings_block(
        USER_NAME, DIAG_WITH_QDII, ADVICE_SECTION, results[UID]["qdii_names"])
    assert "延迟" not in block and "T+2" not in block
    assert QDII_ISSUE in _issues(tmp_path, block), (
        "故障注入失效：标注没了质检却没报")


def test_fault_injection_shared_keywords_cleared_loses_truncated_qdii(phase2_run,
                                                                      monkeypatch):
    """注入 2：清空共享关键词白名单 → 简称被截断的真 QDII 立刻漏判。

    打在 **ft** 上还能生效，证明 night_worker 真的是**调用**共享模块、而不是
    import 期把结果冻住了 —— 即 A 节那条活断言的实际含义。
    """
    monkeypatch.setattr(ft, "QDII_NAME_KEYWORDS", (), raising=True)

    results = phase2_run(QDII_FUNDS)
    names = results[UID]["qdii_names"]
    assert "国泰纳斯达克100指数" not in names, (
        f"故障注入失效：关键词清空后截断简称的 QDII 本应漏判，实际 {names}")
    # 名称里带 (QDII) 的两只走 marker 分支，不受影响 —— 两条支路互不顶替
    assert "浦银安盛全球智能科技(QDII)" in names


def test_fault_injection_note_text_loses_delay_word(tmp_path, monkeypatch):
    """注入 3：把文案模板里的「延迟」去掉 → 质检立刻报回来。

    钉死"文案必须含延迟/T+2"这个约束本身：哪天有人把文案润色成
    「QDII 净值 T+2 才更新」（没了"延迟"），还有 T+2 兜底；但连 T+2
    一起润掉（比如改成"净值披露晚两天"），这里就红。
    """
    monkeypatch.setattr(
        nw, "QDII_DELAY_NOTE_TPL",
        "⚠️ QDII 基金（{funds}）净值披露晚两天，注意时点", raising=True)

    names = ["浦银安盛全球智能科技(QDII)"]
    note = nw._build_qdii_delay_note(names)
    assert "延迟" not in note and "T+2" not in note, f"注入没生效: {note}"

    block = nw._render_holdings_block(USER_NAME, DIAG_WITH_QDII, ADVICE_SECTION, names)
    assert QDII_ISSUE in _issues(tmp_path, block), (
        "故障注入失效：文案没了延迟/T+2 质检却没报")
