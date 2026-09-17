"""
晨报缓存文件名路径穿越 —— 行为级守卫
=====================================

背景（2026-09-17 代码审计发现，属既存问题，非线上事故）：
    晨报缓存文件名是 f"{user_id}_{date}.json" 直接拼出来的，而 user_id 来自
    查询参数（GET /api/steward/briefing?userId=...），**完全外部可控**。于是：

        ?userId=../../tmp/x   →  写：data/briefings/../../tmp/x_20260917.json
                                     即把文件写到缓存目录外面去
        ?userId=../..         →  读：briefing_history() 的 glob pattern 变成
                                     "../../.._*.json"，会去列上级目录

    大小写归一化那一版（33d7bc9）只做了 strip+lower，**没有解决**这一点。

修法（两层）：
    1. 白名单消毒 `_safe_key_fragment()`：只放行 \\w 和连字符，其余压成 "_"。
       结果里不可能出现 "." "/" "\\" ":"，因此拼进文件名后无法穿越目录。
    2. 纵深兜底 `_safe_brief_path()`：拼完再验一次 resolve() 必须仍在
       _BRIEF_DIR 内，越界直接 ValueError。

本文件的断言刻意用「文件到底落在哪个目录」来证明，而不是只看返回的 key 长什么样：
    — 只看 key 长得安全，证明不了拼出来之后真的没跑出去。

故障注入（两轮，缺一不可）：
    A. 把 _safe_key_fragment() 改成恒等函数 → 行为类用例必须变红
       （此时由第 2 层的 ValueError 拦下，说明兜底真的在工作）
    B. 把 _safe_brief_path() 去掉越界校验 → test_safe_brief_path_rejects_escape
       必须变红（说明第 1 层不是唯一防线，第 2 层也不是死代码）
"""
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

import services.steward as steward_module


# ------------------------------------------------------------------
# fixtures / 工具
# ------------------------------------------------------------------

@pytest.fixture
def brief_root(tmp_path, monkeypatch):
    """把 steward 的缓存目录指向 <tmp_path>/briefings，返回 (briefings, tmp_path)。

    刻意让缓存目录**有上级目录**（不是 tmp_path 本身），这样 "向上穿越" 才有
    地方可穿，也才有地方能检查「外面有没有被写脏」。
    """
    d = tmp_path / "data" / "briefings"
    d.mkdir(parents=True)
    monkeypatch.setattr(steward_module, "_BRIEF_DIR", d)
    return d, tmp_path


class _FakeRunner:
    """记录 run() 被调用次数的假 PipelineRunner。"""

    def __init__(self):
        self.calls = []

    def run(self, pipeline_name, ctx):
        self.calls.append(pipeline_name)
        return ctx


def _make_steward(monkeypatch):
    """构造一个不拉起真实依赖的 Steward（只挂假 runner）。"""
    steward = steward_module.Steward.__new__(steward_module.Steward)
    runner = _FakeRunner()
    steward.runner = runner
    monkeypatch.setattr(
        steward_module,
        "classify_regime",
        lambda: {"regime": "oscillating", "confidence": 50, "description": "", "params": {}},
    )
    monkeypatch.setattr(steward_module, "_generate_one_line", lambda ctx: "stub one line")
    monkeypatch.setitem(
        sys.modules,
        "services.geopolitical",
        types.SimpleNamespace(get_geopolitical_events=lambda: {"available": False}),
    )
    return steward, runner


# 一批真实世界的穿越/越权载荷。前四个是经典路径穿越，后面几个是边界情况。
_TRAVERSAL_PAYLOADS = [
    "../../tmp/x",
    "../..",
    "..",
    "/etc/passwd",
    "..%2f..%2ftmp",
    "....//....//etc",
    "a/../../b",
    "..\\..\\windows\\system32",
    "",
    "   ",
]


# ------------------------------------------------------------------
# 1. 消毒后的键本身不含任何危险字符
# ------------------------------------------------------------------

@pytest.mark.parametrize("payload", _TRAVERSAL_PAYLOADS)
def test_sanitized_key_has_no_dangerous_chars(payload):
    """消毒后的键里不许出现路径分隔符、冒号、点号、空白、控制字符。"""
    key = steward_module.brief_cache_key(payload)
    assert key, f"消毒后不应为空串: {payload!r} -> {key!r}"
    for bad in ("/", "\\", ":", ".", " ", "\t", "\n", "\x00", ".."):
        assert bad not in key, f"键 {key!r} 仍含有危险字符 {bad!r}（载荷 {payload!r}）"


@pytest.mark.parametrize("payload", _TRAVERSAL_PAYLOADS)
def test_sanitized_key_variants_have_no_dangerous_chars(payload):
    """briefing_history() 的 glob 键同样要消毒——glob pattern 里的 ".." 会真的
    去列上级目录，所以「原样键」也不能把原始 user_id 直接交出去。"""
    for key in steward_module.brief_cache_key_variants(payload):
        assert key, f"变体不应为空串: {payload!r}"
        for bad in ("/", "\\", ":", ".", " "):
            assert bad not in key, f"glob 键 {key!r} 仍含危险字符 {bad!r}（载荷 {payload!r}）"


def test_unicode_user_id_is_not_destroyed():
    """白名单要留 Unicode 字母：安全修复不能顺手把中文 userId 打成 invalid。

    （这是本次修法的一个具体取舍：白名单用 `[^\\w-]` 而不是 `[^A-Za-z0-9_-]`，
    后者会让「厉害了哥」这类 userId 全部塌成 invalid，属于附带的功能回退。）
    """
    assert steward_module.brief_cache_key("厉害了哥") == "厉害了哥"
    assert steward_module.brief_cache_key("LeiJiang") == "leijiang"
    assert steward_module.brief_cache_key("BuLuoGeLi") == "buluogeli"
    assert steward_module.brief_cache_key("004fee697b1cc2ad") == "004fee697b1cc2ad"


# ------------------------------------------------------------------
# 2. 拼出来的路径确实还在缓存目录里
# ------------------------------------------------------------------

@pytest.mark.parametrize("payload", _TRAVERSAL_PAYLOADS)
def test_cache_path_stays_inside_brief_dir(brief_root, payload):
    """写路径：resolve() 之后必须仍位于缓存目录内。"""
    brief_dir, _ = brief_root
    fp = steward_module.brief_cache_path(payload, "20260917")
    assert fp.resolve().is_relative_to(brief_dir.resolve()), (
        f"写路径逃出了缓存目录: {payload!r} -> {fp}"
    )


@pytest.mark.parametrize("payload", _TRAVERSAL_PAYLOADS)
def test_cache_candidates_stay_inside_brief_dir(brief_root, payload):
    """读路径（含存量兼容的原样键）：每个候选都必须在缓存目录内。"""
    brief_dir, _ = brief_root
    for fp in steward_module.brief_cache_candidates(payload, "20260917"):
        assert fp.resolve().is_relative_to(brief_dir.resolve()), (
            f"读路径逃出了缓存目录: {payload!r} -> {fp}"
        )


def test_safe_brief_path_rejects_escape(brief_root):
    """第 2 层兜底本身要能拦 —— 直接喂一个越界文件名，必须 ValueError。

    这条用例是故障注入 B 的靶子：如果把 _safe_brief_path() 的 is_relative_to
    校验去掉，这条会变红，而其它行为类用例仍然绿（说明第 1 层消毒是主防线）。
    """
    with pytest.raises(ValueError, match="越界"):
        steward_module._safe_brief_path("../../evil.json")


# ------------------------------------------------------------------
# 3. 行为级：真的调一次 briefing()，看文件落在哪
# ------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "../../tmp/x",
    "../../evil",
    "/etc/passwd",
])
def test_briefing_never_writes_outside_brief_dir(brief_root, monkeypatch, payload):
    """核心用例：拿穿越载荷真的跑一次 briefing()，缓存目录外不许出现任何新文件。

    这是「文件到底落在哪」的证据，不是「key 看起来安全」的推断。
    """
    brief_dir, tmp_path = brief_root
    before = {p for p in tmp_path.rglob("*")}

    steward, runner = _make_steward(monkeypatch)
    result = steward.briefing(payload)

    assert runner.calls == ["fast"], "应当走一次现算（缓存必然是空的）"
    assert result.get("regime") == "oscillating"

    after = {p for p in tmp_path.rglob("*")}
    added = after - before
    assert added, "应当至少写出了一个缓存文件，否则这条用例等于没验证写入路径"

    for p in added:
        if p.is_dir():
            continue
        assert p.resolve().is_relative_to(brief_dir.resolve()), (
            f"缓存写到了目录外面: {p}（载荷 {payload!r}）"
        )


def test_briefing_with_traversal_payload_still_returns_200_payload(brief_root, monkeypatch):
    """穿越载荷不应该让接口 500 —— 消毒后当普通用户键处理，正常返回晨报。"""
    brief_dir, _ = brief_root
    steward, _ = _make_steward(monkeypatch)
    result = steward.briefing("../../tmp/x")
    assert result["regime"] == "oscillating"
    # 落盘文件用消毒后的键，仍然在缓存目录内
    written = list(brief_dir.glob("*.json"))
    assert len(written) == 1
    assert written[0].name == "tmp_x_%s.json" % datetime.now().strftime("%Y%m%d")


def test_legacy_camel_case_still_readable_after_sanitize(brief_root, monkeypatch):
    """消毒不能顺手砸掉 33d7bc9 修的存量兼容：LeiJiang 的原样键必须还在候选里。

    LeiJiang 全部字符都在白名单内，所以原样键应当**原封不动**保留（只消毒不 lower）。
    """
    brief_dir, _ = brief_root
    assert steward_module.brief_cache_key_variants("LeiJiang") == ["leijiang", "LeiJiang"]
    assert steward_module.brief_cache_key_variants("BuLuoGeLi") == ["buluogeli", "BuLuoGeLi"]
    names = [p.name for p in steward_module.brief_cache_candidates("LeiJiang", "20260917")]
    assert names == ["leijiang_20260917.json", "LeiJiang_20260917.json"]


# ------------------------------------------------------------------
# 4. glob 读穿越：上级目录的文件不许被列出来
# ------------------------------------------------------------------

def test_glob_variants_do_not_list_parent_dir(brief_root):
    """把恶意 key 交给和 briefing_history() 一模一样的 glob，不许命中上级目录文件。

    上级目录里放一个精心命名的 *_20260917.json，如果 variant 没消毒，
    pattern 会变成 "../../xxx_*.json"，真的把它 glob 出来。
    """
    brief_dir, tmp_path = brief_root
    decoy = tmp_path / "data" / "decoy_20260917.json"
    decoy.write_text('{"regime":"LEAKED"}', encoding="utf-8")

    for payload in ["../../", "../", "..", "../../data"]:
        keys = steward_module.brief_cache_key_variants(payload)
        matched = []
        for key in keys:
            matched.extend(brief_dir.glob(f"{key}_*.json"))
        for p in matched:
            assert p.resolve().is_relative_to(brief_dir.resolve()), (
                f"glob 越界命中了 {p}（载荷 {payload!r}，键 {keys}）"
            )
        assert "LEAKED" not in "".join(
            p.read_text(encoding="utf-8") for p in matched if p.is_file()
        ), f"读到了上级目录的诱饵文件（载荷 {payload!r}）"


def test_briefing_history_with_traversal_payload_is_empty(brief_root):
    """拿穿越载荷调 briefing_history()，不许返回上级目录里的内容。"""
    brief_dir, tmp_path = brief_root
    decoy = tmp_path / "data" / "decoy_20260917.json"
    decoy.write_text('{"regime":"LEAKED"}', encoding="utf-8")

    steward = steward_module.Steward.__new__(steward_module.Steward)
    out = steward.briefing_history("../../", days=7)
    assert isinstance(out, list)
    assert all(item.get("regime") != "LEAKED" for item in out)


# ------------------------------------------------------------------
# 5. 源码护栏：不许有人绕过这两个函数自己拼路径
# ------------------------------------------------------------------

def test_no_raw_briefing_path_concatenation():
    """backend/ 下不许再出现把 user_id 直接拼进 briefings 路径的写法。"""
    import re

    backend_dir = Path(steward_module.__file__).resolve().parent.parent
    patterns = [
        re.compile(r'"briefings"\s*/\s*f"'),
        re.compile(r'_BRIEF_DIR\s*/\s*f"'),
        re.compile(r'brief_dir\s*/\s*f"'),
    ]
    offenders = []
    for py in backend_dir.rglob("*.py"):
        if "tests" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if any(p.search(line) for p in patterns):
                offenders.append(f"{py.relative_to(backend_dir)}:{i}: {line.strip()}")

    assert not offenders, (
        "以下位置绕开了 services.steward.brief_cache_path()/brief_cache_candidates()，"
        "自己拼了 briefings 路径（路径穿越就是这么来的）：\n" + "\n".join(offenders)
    )
