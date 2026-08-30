"""
钱袋子 — Tushare 主数据源模块

策略：Tushare 作为主数据源（5000积分足够），AKShare 作为降级

为 6 个核心数据源模块提供统一的 Tushare 主数据能力：
- news_data (新闻) - Tushare 无新闻接口，直接降级到 AKShare
- macro_data (宏观指标) - Tushare 主：CPI/PMI/M2/社融等
- global_market (全球市场) - Tushare 主：指数日线/周线/月线
- alt_data (另类数据) - Tushare 主：龙虎榜/大宗交易/融资融券
- policy_data (政策数据) - Tushare 无政策接口，直接降级到 AKShare
- sector_data (板块数据) - Tushare 主：行业板块/概念板块

使用方式（新策略：Tushare 主 + AKShare 降级）：
    from services.tushare_fallback import TusharePrimary
    tp = TusharePrimary()
    result = tp.get_macro_cpi(...)  # Tushare 主
    if result is None:
        # 降级到 AKShare
        ...
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any


class TusharePrimary:
    """Tushare 主数据源类（单例模式）

    策略：Tushare 作为主数据源，AKShare 作为降级
    适用场景：用户有 5000+ Tushare 积分，大多数接口可直接使用
    """

    _instance = None
    _token_loaded = False
    _pro = None

    def __init__(self):
        self._ensure_token()
        self._init_pro()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_token(self):
        """加载 Tushare token（从 .env 或环境变量）"""
        if self._token_loaded:
            return

        # 尝试从 .env 加载
        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k and v:
                        os.environ.setdefault(k, v)

        self._token_loaded = True

    def _init_pro(self):
        """初始化 Tushare Pro API"""
        if self._pro is not None:
            return

        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            print("[TUSHARE_PRIMARY] Token 未配置，降级功能不可用")
            return

        try:
            import tushare as ts
            ts.set_token(token)
            self._pro = ts.pro_api()
            print("[TUSHARE_PRIMARY] Pro API 初始化成功")
        except Exception as e:
            print(f"[TUSHARE_PRIMARY] Pro API 初始化失败: {e}")
            self._pro = None

    def is_available(self) -> bool:
        """检查 Tushare 是否可用"""
        return self._pro is not None

    # ============================================================
    # 1. 新闻数据降级 (news_data.py)
    # ============================================================

    def get_news(self, keyword: str = "", limit: int = 10) -> Optional[List[Dict]]:
        """获取新闻（Tushare 无直接新闻接口，返回 None 表示不可用）

        注意：Tushare 主要提供行情数据，新闻数据需要用 AKShare 或其他源
        这里保留接口以便未来扩展
        """
        return None

    # ============================================================
    # 2. 宏观数据降级 (macro_data.py)
    # ============================================================

    def get_macro_cpi(self, start_date: str = "", end_date: str = "", limit: int = 10) -> Optional[List[Dict]]:
        """获取 CPI 数据（Tushare: cn_cpi）"""
        if not self.is_available():
            return None

        try:
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            if not start_date:
                start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

            df = self._pro.cn_cpi(start_date=start_date, end_date=end_date)
            if df is None or len(df) == 0:
                return None

            result = []
            for _, row in df.iterrows():
                result.append({
                    "date": str(row.get("month", "")),
                    "value": float(row.get("nt_yoy", 0)),  # 全国同比增长
                    "source": "tushare",
                })

            print(f"[TUSHARE_PRIMARY] CPI 数据: {len(result)} 条, latest: {result[0]['value']}% @ {result[0]['date']}")
            return result[:limit]

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] CPI 获取失败: {e}")
            return None

    def get_macro_pmi(self, start_date: str = "", end_date: str = "", limit: int = 10) -> Optional[List[Dict]]:
        """获取 PMI 数据（Tushare: cn_pmi）"""
        if not self.is_available():
            return None

        try:
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            if not start_date:
                start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

            df = self._pro.cn_pmi(start_date=start_date, end_date=end_date)
            if df is None or len(df) == 0:
                return None

            result = []
            for _, row in df.iterrows():
                result.append({
                    "date": str(row.get("MONTH", "")),
                    "value": float(row.get("PMI010000", 0)),  # 制造业PMI
                    "source": "tushare",
                })

            print(f"[TUSHARE_PRIMARY] PMI 数据: {len(result)} 条, latest: {result[0]['value']} @ {result[0]['date']}")
            return result[:limit]

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] PMI 获取失败: {e}")
            return None

    # ============================================================
    # 3. 全球市场降级 (global_market.py)
    # ============================================================

    def get_index_daily(self, ts_code: str = "000001.SH", trade_date: str = "") -> Optional[Dict]:
        """获取指数日线数据（Tushare: index_daily）

        ts_code: 指数代码（000001.SH=上证, 399001.SZ=深证, 000300.SH=沪深300）
        """
        if not self.is_available():
            return None

        try:
            if not trade_date:
                # 找最近的交易日
                for i in range(5):
                    td = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
                    df = self._pro.index_daily(ts_code=ts_code, trade_date=td)
                    if df is not None and len(df) > 0:
                        trade_date = td
                        break

            if not trade_date:
                return None

            df = self._pro.index_daily(ts_code=ts_code, trade_date=trade_date)
            if df is None or len(df) == 0:
                return None

            row = df.iloc[0]
            result = {
                "code": ts_code,
                "date": trade_date,
                "open": float(row.get("open", 0)),
                "close": float(row.get("close", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "pct_chg": float(row.get("pct_chg", 0)),
                "vol": float(row.get("vol", 0)),
                "source": "tushare",
            }

            print(f"[TUSHARE_PRIMARY] 指数数据: {ts_code} @ {trade_date}")
            return result

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] 指数数据获取失败: {e}")
            return None

    # ============================================================
    # 4. 另类数据降级 (alt_data.py)
    # ============================================================

    def get_top_list(self, trade_date: str = "", limit: int = 20) -> Optional[List[Dict]]:
        """获取龙虎榜数据（Tushare: top_list）

        需要 5000 积分
        """
        if not self.is_available():
            return None

        try:
            if not trade_date:
                trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

            df = self._pro.top_list(trade_date=trade_date)
            if df is None or len(df) == 0:
                return None

            result = []
            for _, row in df.head(limit).iterrows():
                result.append({
                    "code": str(row.get("ts_code", "")).split(".")[0],
                    "name": str(row.get("name", "")),
                    "price": float(row.get("price", 0)),
                    "pct_chg": float(row.get("pct_chg", 0)),
                    "reason": str(row.get("reason", "")),
                    "source": "tushare",
                })

            print(f"[TUSHARE_PRIMARY] 龙虎榜数据: {len(result)} 条")
            return result

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] 龙虎榜获取失败: {e}")
            return None

    # ============================================================
    # 5. 政策数据降级 (policy_data.py)
    # ============================================================

    def get_policy_news(self, limit: int = 10) -> Optional[List[Dict]]:
        """获取政策新闻（Tushare 无直接接口，返回 None）

        注意：政策数据建议用 AKShare 的 news_economic_baidu() 等
        """
        return None

    # ============================================================
    # 6. 板块数据降级 (sector_data.py)
    # ============================================================

    def get_sector_daily(self, trade_date: str = "") -> Optional[List[Dict]]:
        """获取申万行业日行情（统一走项目封装的 Tushare 入口）。"""
        try:
            from services.tushare_data import get_sw_sector_daily

            result = get_sw_sector_daily(trade_date=trade_date, level="L1")
            if result:
                print(f"[TUSHARE_PRIMARY] 板块数据: {len(result)} 个行业")
                return result
            return None

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] 板块数据获取失败: {e}")
            return None

    @staticmethod
    def _get_sector_name(ts_code: str) -> str:
        """板块代码转名称"""
        name_map = {
            "000300.SH": "沪深300",
            "000016.SH": "上证50",
            "000905.SH": "中证500",
            "399006.SZ": "创业板指",
        }
        return name_map.get(ts_code, ts_code)


    # ===== 全球市场数据 =====

    def get_us_indices(self, start_date: str = "", end_date: str = "") -> Optional[List[Dict]]:
        """获取美股指数（Tushare: index_global）

        Tushare 的 index_global 接口支持：
        - US_DJI (道琼斯)
        - US_SPX (标普500)
        - US_IXIC (纳斯达克)
        """
        if not self.is_available():
            return None

        try:
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            if not start_date:
                start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

            # 获取美股三大指数
            symbols = ["US_DJI", "US_SPX", "US_IXIC"]
            result = []

            for symbol in symbols:
                df = self._pro.index_global(ts_code=symbol, start_date=start_date, end_date=end_date)
                if df is not None and len(df) > 0:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else latest

                    close = float(latest.get("close", 0))
                    prev_close = float(prev.get("close", close))
                    change_pct = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0

                    key_map = {"US_DJI": "dji", "US_SPX": "spx", "US_IXIC": "ixic"}
                    result.append({
                        "key": key_map[symbol],
                        "close": round(close, 2),
                        "change_pct": round(change_pct, 2),
                        "date": str(latest.get("trade_date", "")),
                        "trend": "up" if change_pct > 0 else "down" if change_pct < 0 else "flat",
                        "source": "tushare",
                    })

            print(f"[TUSHARE_PRIMARY] US indices: {len(result)} indices")
            return result

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] US indices 获取失败: {e}")
            return None

    def get_forex_data(self, start_date: str = "", end_date: str = "") -> Optional[Dict]:
        """获取外汇汇率（Tushare: fx_obtime）

        主要汇率：
        - USDCNY (美元/人民币)
        - USDJPY (美元/日元)
        - EURUSD (欧元/美元)
        """
        if not self.is_available():
            return None

        try:
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            if not start_date:
                start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

            # 美元/人民币
            df = self._pro.fx_obtime(ts_code="USDCNY", start_date=start_date, end_date=end_date)
            if df is not None and len(df) > 0:
                latest = df.iloc[-1]
                rate = float(latest.get("close", 0))
                print(f"[TUSHARE_PRIMARY] FX USDCNY: {rate}")
                return {
                    "usdcny": rate,
                    "date": str(latest.get("trade_date", "")),
                    "source": "tushare",
                }

            return None

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] Forex 获取失败: {e}")
            return None

    def get_global_commodities(self, start_date: str = "", end_date: str = "") -> Optional[Dict]:
        """获取国际商品价格（Tushare: fut_global）

        主要商品：
        - GOLD (黄金)
        - WTI (原油)
        """
        if not self.is_available():
            return None

        try:
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            if not start_date:
                start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

            result = {}

            # 黄金
            df = self._pro.fut_global(ts_code="GOLD", start_date=start_date, end_date=end_date)
            if df is not None and len(df) > 0:
                latest = df.iloc[-1]
                result["gold"] = {
                    "price": float(latest.get("close", 0)),
                    "date": str(latest.get("trade_date", "")),
                    "source": "tushare",
                }

            # 原油
            df = self._pro.fut_global(ts_code="WTI", start_date=start_date, end_date=end_date)
            if df is not None and len(df) > 0:
                latest = df.iloc[-1]
                result["oil"] = {
                    "price": float(latest.get("close", 0)),
                    "date": str(latest.get("trade_date", "")),
                    "source": "tushare",
                }

            print(f"[TUSHARE_PRIMARY] Commodities: {list(result.keys())}")
            return result if result else None

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] Commodities 获取失败: {e}")
            return None


# 便捷函数（单例模式）
def get_tushare_primary() -> TusharePrimary:
    """获取 TusharePrimary 单例"""
    return TusharePrimary.instance()

    # ===== 另类数据（alt_data）=====

    def get_dragon_tiger(self, trade_date: str = "") -> Optional[List[Dict]]:
        """获取龙虎榜数据（Tushare: top10_floats）

        龙虎榜：当日涨幅偏离值±7%、换手率前5等异常交易个股
        Tushare 接口：top10_floats (前十大流通股东，非龙虎榜)
        注意：Tushare 无直接龙虎榜接口，返回 None 触发 AKShare 降级
        """
        return None  # Tushare 无龙虎榜接口，直接降级到 AKShare

    def get_block_trade(self, start_date: str = "", end_date: str = "") -> Optional[List[Dict]]:
        """获取大宗交易数据（Tushare: block_trade）

        大宗交易：大额股票交易，通常涉及机构调仓
        """
        if not self.is_available():
            return None

        try:
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            if not start_date:
                start_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

            df = self._pro.block_trade(start_date=start_date, end_date=end_date)
            if df is None or len(df) == 0:
                return None

            result = []
            for _, row in df.iterrows():
                result.append({
                    "ts_code": str(row.get("ts_code", "")),
                    "trade_date": str(row.get("trade_date", "")),
                    "price": float(row.get("price", 0)),
                    "volume": int(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)),
                    "buyer": str(row.get("buyer", "")),
                    "seller": str(row.get("seller", "")),
                    "source": "tushare",
                })

            print(f"[TUSHARE_PRIMARY] 大宗交易: {len(result)} 条")
            return result[:50]  # 返回最近 50 条

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] 大宗交易获取失败: {e}")
            return None

    def get_margin_detail(self, trade_date: str = "") -> Optional[List[Dict]]:
        """获取融资融券明细（Tushare: margin_detail）

        融资融券：投资者借钱买股（融资）或借股卖出（融券）
        """
        if not self.is_available():
            return None

        try:
            if not trade_date:
                trade_date = datetime.now().strftime("%Y%m%d")

            df = self._pro.margin_detail(trade_date=trade_date)
            if df is None or len(df) == 0:
                return None

            result = []
            for _, row in df.iterrows():
                result.append({
                    "ts_code": str(row.get("ts_code", "")),
                    "trade_date": str(row.get("trade_date", "")),
                    "margin_balance": float(row.get("margin_balance", 0)),  # 融资余额
                    "margin_buy": float(row.get("margin_buy", 0)),  # 融资买入额
                    "short_balance": float(row.get("short_balance", 0)),  # 融券余额
                    "source": "tushare",
                })

            print(f"[TUSHARE_PRIMARY] 融资融券: {len(result)} 条")
            return result[:100]  # 返回前 100 条

        except Exception as e:
            print(f"[TUSHARE_PRIMARY] 融资融券获取失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 已删除：get_northbound_flow()（2026-08 移除，请勿重新添加）
    #
    # 原实现有三个叠加错误，且是死代码（全仓库零调用方），修不如删：
    #   1. 【方向完全相反】用 `ggt_ss_flow + ggt_sz_flow` 求和当作北向。
    #      moneyflow_hsgt 里 ggt_ss / ggt_sz 是**港股通（南向，内地资金买港股）**
    #      字段，北向应看 hgt / sgt / north_money。拿南向当北向不是噪声，
    #      而是**系统性反向** —— 噪声至少随机，反向会稳定地给出错误结论。
    #   2. 【单位错误】日志写"万元"，moneyflow_hsgt 实际单位是**百万元**，差 100 倍。
    #   3. 【前提已不成立】北向日频净买入自 2024-08-19 起沪深交易所停止披露、
    #      改为按季度公布，任何数据源都拿不到日频净流入，
    #      `total_inflow_5d` 这个字段本身已无法诚实计算。
    #
    # 北向数据的唯一正确入口：`services.tushare_data.get_northbound_flow()`
    # （只返回成交额，净流入维度显式标记 net_flow_available=False）。
    # 上层请走 `services.factor_data.get_northbound_flow()`（含 AKShare 降级）。
    # ------------------------------------------------------------------
