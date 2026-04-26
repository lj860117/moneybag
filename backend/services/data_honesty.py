"""
钱袋子 — 数据诚信层（DataEnvelope）

目的：所有数据源返回值统一包装，强制标注"数据从哪来、新不新鲜、是真是假"。

为什么存在：
- AI 看不到"降级"标记就会把旧数据当新数据分析 → 自信地错
- 前端看不到"mock"标记就会把演示数据当真实数据显示 → 误导用户
- 下单审计看不到数据来源就无法追溯"为什么当时这么判断"

使用方式（不破坏现有代码）：
    from backend.services.data_honesty import DataEnvelope, wrap_source

    # 方式 1：手动包装
    def get_stock_price(code):
        try:
            raw = tushare.pro_bar(code)
            return DataEnvelope.fresh(raw, source="tushare:pro_bar")
        except Exception as e:
            cached = load_from_cache(code)
            if cached:
                return DataEnvelope.stale(cached, source="tushare:pro_bar", reason=str(e))
            return DataEnvelope.mock(default_value=0.0, reason="all sources down")

    # 方式 2：装饰器（推荐）
    @wrap_source("tushare:pro_bar", fallback=load_from_cache)
    def get_stock_price(code):
        return tushare.pro_bar(code)   # 原函数不动，装饰器处理 envelope

关联文档：
- docs/PRODUCT-PRINCIPLES.md 规则 3（数据真实性）
- docs/prompt-diagnosis-2026-04-23.md（6 个 Prompt 都缺出处追溯，将靠本模块补齐）
- bug-feedback/QA1-plan.md TASK-026（数据因子真实性审计的基础设施）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, Optional

# --- 常量 ---

# 新鲜度阈值：交易日内 1 小时、非交易日 4 小时算 fresh
FRESH_THRESHOLD_MINUTES = 60
STALE_THRESHOLD_HOURS = 24  # 超过此阈值视为过期，必须告警

# 数据级别（对应 docs/PRODUCT-PRINCIPLES.md 数据可信度分级）
LEVEL_LIVE = "live"        # 🟢 实时
LEVEL_CACHED = "cached"    # 🟡 缓存有效期内
LEVEL_FALLBACK = "fallback"  # 🟠 降级（主源挂了用备源）
LEVEL_MOCK = "mock"        # 🔴 Mock 演示数据
LEVEL_STALE = "stale"      # 🔴 过期数据（超过 STALE_THRESHOLD_HOURS）


@dataclass
class DataEnvelope:
    """
    所有数据源返回值的统一信封。

    AI Prompt 里要求标注数据来源时，把 to_ai_tag() 注入即可。
    前端展示数据时，按 level 字段决定是否加红/黄标签。
    """

    value: Any
    source: str              # 例如 "tushare:pro_bar" / "akshare:news_cctv" / "mock:default"
    fetched_at: datetime = field(default_factory=datetime.now)
    level: str = LEVEL_LIVE  # live / cached / fallback / mock / stale
    fallback_reason: Optional[str] = None  # 降级原因（主源异常信息）
    cache_age_seconds: Optional[int] = None  # 缓存了多久
    note: Optional[str] = None  # 额外说明（给 AI 看）

    # --- 便捷构造函数 ---

    @classmethod
    def fresh(cls, value: Any, source: str, note: Optional[str] = None) -> "DataEnvelope":
        """实时真实数据 🟢"""
        return cls(value=value, source=source, level=LEVEL_LIVE, note=note)

    @classmethod
    def cached(cls, value: Any, source: str, cache_age_seconds: int, note: Optional[str] = None) -> "DataEnvelope":
        """缓存数据 🟡"""
        level = LEVEL_STALE if cache_age_seconds > STALE_THRESHOLD_HOURS * 3600 else LEVEL_CACHED
        return cls(
            value=value,
            source=source,
            level=level,
            cache_age_seconds=cache_age_seconds,
            note=note,
        )

    @classmethod
    def stale(cls, value: Any, source: str, reason: str) -> "DataEnvelope":
        """降级（主源挂了用旧数据）🟠"""
        return cls(
            value=value,
            source=source,
            level=LEVEL_FALLBACK,
            fallback_reason=reason,
            note=f"主数据源不可用，使用备用/缓存数据：{reason}",
        )

    @classmethod
    def mock(cls, default_value: Any = None, reason: str = "placeholder") -> "DataEnvelope":
        """Mock 演示数据 🔴"""
        return cls(
            value=default_value,
            source=f"mock:{reason}",
            level=LEVEL_MOCK,
            note=f"⚠️ 演示数据，非真实：{reason}",
        )

    # --- 展示 / 注入 ---

    @property
    def is_reliable(self) -> bool:
        """是否为可信数据（给 AI 判断置信度用）"""
        return self.level in (LEVEL_LIVE, LEVEL_CACHED)

    @property
    def ui_badge(self) -> Optional[str]:
        """前端展示用的标签文字。None 表示不用特殊提示。"""
        if self.level == LEVEL_LIVE:
            return None
        if self.level == LEVEL_CACHED:
            if self.cache_age_seconds and self.cache_age_seconds > 600:
                mins = self.cache_age_seconds // 60
                return f"上次更新 {mins} 分钟前"
            return None
        if self.level == LEVEL_FALLBACK:
            return "⚠️ 备用数据源"
        if self.level == LEVEL_MOCK:
            return "⚠️ Demo 数据，仅演示"
        if self.level == LEVEL_STALE:
            return "⚠️ 数据已过期"
        return None

    def to_ai_tag(self) -> str:
        """
        给 AI Prompt 看的简短标注字符串。
        用在 Prompt 里：`{indicator.name}={indicator.value}（{indicator.envelope.to_ai_tag()}）`
        """
        if self.level == LEVEL_LIVE:
            return f"来源:{self.source}"
        if self.level == LEVEL_CACHED:
            age = self.cache_age_seconds or 0
            return f"来源:{self.source}, 缓存 {age // 60}min"
        if self.level == LEVEL_FALLBACK:
            return f"来源:{self.source}(备用), 原因:{self.fallback_reason}"
        if self.level == LEVEL_MOCK:
            return "⚠️ Mock 演示数据，请降低置信度"
        if self.level == LEVEL_STALE:
            return f"⚠️ 数据过期（>{STALE_THRESHOLD_HOURS}h），请降低置信度"
        return f"来源:{self.source}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fetched_at"] = self.fetched_at.isoformat()
        d["is_reliable"] = self.is_reliable
        d["ui_badge"] = self.ui_badge
        return d


# --- 装饰器：给现有数据源函数加信封而不侵入 ---

def wrap_source(
    source_name: str,
    fallback: Optional[Callable[..., Any]] = None,
    mock_default: Any = None,
):
    """
    把普通数据源函数自动包装为返回 DataEnvelope 的函数。

    参数：
        source_name: 数据源标识，例如 "tushare:pro_bar"
        fallback: 主源失败时调用的降级函数（签名与被装饰函数相同）
        mock_default: 所有降级都失败时的 mock 值

    用法：
        @wrap_source("tushare:news", fallback=_news_from_akshare)
        def get_news(limit: int = 10):
            return tushare.pro.news(limit=limit)

        # 调用时：
        env = get_news(limit=10)
        assert isinstance(env, DataEnvelope)
        if env.is_reliable:
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., DataEnvelope]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> DataEnvelope:
            # 1. 尝试主源
            try:
                value = func(*args, **kwargs)
                return DataEnvelope.fresh(value, source=source_name)
            except Exception as primary_err:  # noqa: BLE001 – 数据源异常种类多
                primary_msg = f"{type(primary_err).__name__}: {primary_err}"

            # 2. 降级到备用函数
            if fallback is not None:
                try:
                    value = fallback(*args, **kwargs)
                    return DataEnvelope.stale(
                        value, source=source_name, reason=primary_msg
                    )
                except Exception as fallback_err:  # noqa: BLE001
                    fallback_msg = f"主源失败：{primary_msg}；备源失败：{fallback_err}"
                    return DataEnvelope.mock(
                        default_value=mock_default, reason=fallback_msg
                    )

            # 3. 无降级函数 → 直接 mock
            return DataEnvelope.mock(
                default_value=mock_default,
                reason=primary_msg,
            )

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 示例：接入两个数据源做样板（不覆盖现有函数，只作演示）
# ---------------------------------------------------------------------------

def _example_tushare_price(code: str) -> float:
    """
    样板：Tushare 股票价格（真实接入时从 tushare_data.py 拿）
    这里只写接口形状，不实际调用 tushare，避免破坏线上
    """
    raise NotImplementedError("接入时替换为真实 tushare 调用")


def _example_akshare_price_fallback(code: str) -> float:
    """样板：AKShare 股票价格降级函数"""
    raise NotImplementedError("接入时替换为真实 akshare 调用")


# 样板函数：展示装饰器用法
@wrap_source(
    source_name="tushare:stock_price",
    fallback=_example_akshare_price_fallback,
    mock_default=0.0,
)
def example_get_stock_price(code: str) -> float:
    """
    样板：用 DataEnvelope 包装后的股票价格查询。

    返回值不再是 float，而是 DataEnvelope：
        env = example_get_stock_price("000408")
        price = env.value
        if env.is_reliable:
            ...
        ui_label = env.ui_badge
        ai_tag = env.to_ai_tag()
    """
    return _example_tushare_price(code)


# ---------------------------------------------------------------------------
# 自检（import 时检查逻辑正确）
# ---------------------------------------------------------------------------

def _self_check():
    """轻量自检，确保核心逻辑未回归。不抛异常即通过。"""
    # 1. fresh
    env = DataEnvelope.fresh(42, source="test")
    assert env.is_reliable
    assert env.level == LEVEL_LIVE
    assert env.to_ai_tag().startswith("来源:test")

    # 2. cached
    env = DataEnvelope.cached(42, source="test", cache_age_seconds=300)
    assert env.is_reliable
    assert env.level == LEVEL_CACHED

    # 3. stale (cached 超过阈值)
    env = DataEnvelope.cached(42, source="test", cache_age_seconds=STALE_THRESHOLD_HOURS * 3600 + 1)
    assert not env.is_reliable
    assert env.level == LEVEL_STALE

    # 4. fallback
    env = DataEnvelope.stale(42, source="test", reason="timeout")
    assert not env.is_reliable
    assert env.level == LEVEL_FALLBACK
    assert env.ui_badge == "⚠️ 备用数据源"

    # 5. mock
    env = DataEnvelope.mock(reason="no data")
    assert not env.is_reliable
    assert env.level == LEVEL_MOCK
    assert "Demo" in (env.ui_badge or "")

    # 6. to_dict 序列化正常
    d = env.to_dict()
    assert "value" in d and "level" in d and "is_reliable" in d

    # 7. 装饰器 —— 主源/降级/mock 路径都要能跑
    @wrap_source("test:primary", fallback=lambda: "backup", mock_default="mock")
    def _ok():
        return "ok"

    @wrap_source("test:primary", fallback=lambda: "backup", mock_default="mock")
    def _fail():
        raise RuntimeError("down")

    @wrap_source("test:primary", mock_default="mock")
    def _no_fallback():
        raise RuntimeError("no backup here")

    assert _ok().value == "ok" and _ok().level == LEVEL_LIVE
    assert _fail().value == "backup" and _fail().level == LEVEL_FALLBACK
    assert _no_fallback().value == "mock" and _no_fallback().level == LEVEL_MOCK


_self_check()
