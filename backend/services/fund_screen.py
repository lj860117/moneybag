"""
钱袋子 — 基金智能筛选
从全量基金排行中多维打分筛选 TOP 推荐
参考：豆包方案（指增基金评分、回撤/规模/费率/超额排序）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "fund_screen",
    "scope": "public",
    "input": ['fund_type', 'sort_by'],
    "output": "screened_funds",
    "cost": "cpu",
    "tags": ['基金筛选', '多维打分'],
    "description": "基金智能筛选：多维打分(收益+稳定+费率)排序TOP推荐",
    "layer": "analysis",
    "priority": 3,
}
import time
import os
import json as _json
from config import FUND_RANK_CACHE_TTL, DATA_DIR
from infra.cache import MemoryCache
from services.fund_rank import _load_fund_rank_data
from services.utils import find_col as _find_col, safe_float as _safe_float, parse_fee as _parse_fee

_fund_screen_cache = MemoryCache(default_ttl=FUND_RANK_CACHE_TTL)

# v9.5.108: fund_screen 文件持久化缓存（跨重启）+ _scale_cache 文件持久化
_FILE_CACHE_DIR = os.path.join(DATA_DIR, "_cache", "fund_screen_persist")
try:
    os.makedirs(_FILE_CACHE_DIR, exist_ok=True)
except Exception:
    pass


def _file_cache_get(cache_key: str):
    """读文件缓存（TTL = FUND_RANK_CACHE_TTL）"""
    try:
        path = os.path.join(_FILE_CACHE_DIR, f"{cache_key}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            rec = _json.load(f)
        if (time.time() - rec.get("t", 0)) > FUND_RANK_CACHE_TTL:
            return None
        return rec.get("v")
    except Exception:
        return None


def _file_cache_set(cache_key: str, value):
    try:
        path = os.path.join(_FILE_CACHE_DIR, f"{cache_key}.json")
        with open(path, "w", encoding="utf-8") as f:
            _json.dump({"v": value, "t": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass


def screen_funds(
    fund_type: str = "all",
    sort_by: str = "score",
    top_n: int = 20,
    user_id: str = "",
) -> dict:
    """
    多维度基金筛选（V2：含质量过滤 + 回撤惩罚 + 用户持仓去重）
    fund_type: all / stock / bond / index / hybrid / qdii
    sort_by: score / 1y / 3y / ytd
    top_n: 返回前N只
    """
    cache_key = f"fund_screen_{fund_type}_{sort_by}_{top_n}_{user_id}"
    now = time.time()
    cached = _fund_screen_cache.get(cache_key)
    if cached is not None:
        return cached
    # v9.5.108: 内存缓存空时尝试文件缓存兜底（跨重启）
    file_cached = _file_cache_get(cache_key)
    if file_cached is not None:
        _fund_screen_cache.set(cache_key, file_cached, ttl=FUND_RANK_CACHE_TTL)
        return file_cached

    rank_data = _load_fund_rank_data()
    if not rank_data:
        return {"funds": [], "total": 0, "error": "基金排行数据暂不可用"}

    # 加载 Tushare fund_rank 数据（含 list_date / issue_amount）
    ts_rank_map = _load_ts_rank_map()

    candidates = []
    excluded_count = 0

    for code, row in rank_data.items():
        try:
            cols = list(row.index) if hasattr(row, "index") else []
            name = str(row.get(_find_col(cols, ["简称", "名称"]) or cols[1], ""))

            # v9.5.123: 类型过滤（补全关键词,避免遗漏）
            if fund_type != "all":
                _INDEX_KW = ["指数", "ETF联接", "ETF", "沪深300", "中证500", "中证1000", "中证A",
                             "创业板指", "增强", "被动", "跟踪", "上证50", "科创50", "恒生",
                             "纳斯达克", "标普", "深证100", "中证红利", "中证800"]
                _is_index = any(k in name for k in _INDEX_KW)
                if fund_type == "stock":
                    # 股票型：主动管理股票基金（排除指数/增强/ETF联接）
                    if _is_index:
                        continue
                    _STOCK_KW = ["股票", "混合", "灵活", "成长", "价值", "量化", "优选", "精选",
                                 "龙头", "先进", "科技", "制造", "消费", "医疗", "新能源",
                                 "红利", "高股息", "质量", "均衡", "核心", "蓝筹", "趋势",
                                 "创新", "产业", "主题", "策略", "优势", "配置"]
                    if not any(k in name for k in _STOCK_KW):
                        continue
                elif fund_type == "bond":
                    # v9.5.123: 去掉"收益"(太泛,会把混合型误收)
                    if not any(k in name for k in ["债", "利率", "信用", "纯债", "固收"]):
                        continue
                    # 排除明显不是债券的
                    if any(k in name for k in ["混合", "股票", "成长", "科技"]):
                        continue
                elif fund_type == "index":
                    if not _is_index:
                        continue
                elif fund_type == "qdii":
                    _QDII_KW = ["QDII", "标普", "纳斯达克", "纳指", "全球", "海外", "美股",
                                "恒生", "港股", "日经", "日本", "越南", "印度", "德国",
                                "法国", "英国", "韩国", "东南亚", "亚太", "欧洲",
                                "S&P", "道琼", "新兴市场", "国际"]
                    if not any(k in name for k in _QDII_KW):
                        continue

            # 提取收益率
            r1y = _safe_float(row.get(_find_col(cols, ["近1年"]), None))
            r3y = _safe_float(row.get(_find_col(cols, ["近3年"]), None))
            r6m = _safe_float(row.get(_find_col(cols, ["近6月"]), None))
            r3m = _safe_float(row.get(_find_col(cols, ["近3月"]), None))
            rytd = _safe_float(row.get(_find_col(cols, ["今年来"]), None))
            fee = str(row.get(_find_col(cols, ["手续费"]), ""))

            # 至少有1年收益率才纳入
            if r1y is None:
                continue

            # ========== 质量硬过滤（V2 新增）==========
            ts_info = ts_rank_map.get(code, {})

            # 过滤1：近1年涨幅 > 80% 的极端品种(v9.5.123: 100%→80%, 一年翻倍追入=接盘)
            if r1y > 80:
                excluded_count += 1
                continue

            # 过滤2：近3月 > 40% 的短期过热品种（追入大概率站岗）
            if r3m is not None and r3m > 40:
                excluded_count += 1
                continue

            # 过滤3：基金成立不足2年（新基金没有足够历史验证）
            list_date = ts_info.get("list_date", "")
            if list_date:
                try:
                    from datetime import datetime as _dt
                    fund_age_days = (now - _dt.strptime(list_date, "%Y%m%d").timestamp()) / 86400
                    if fund_age_days < 730:  # < 2 年
                        excluded_count += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # 过滤4：发行规模 < 2 亿份（小盘容易操纵/清盘风险）
            issue_amount = ts_info.get("issue_amount")
            if issue_amount is not None:
                try:
                    if float(issue_amount) < 2.0:
                        excluded_count += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # 过滤5：从天天基金排行数据里获取当前规模
            # AKShare fund_open_fund_rank_em 列序：序号/代码/简称/日期/单位净值/累计净值/日涨幅/近1周/近1月/近3月/近6月/近1年/近2年/近3年/今年来/成立来/手续费
            # 规模字段在位置7（近1周前面），值单位为亿
            scale_col = _find_col(cols, ["规模", "资产", "净资产", "基金规模"])
            current_scale = None
            if scale_col:
                current_scale = _safe_float(row.get(scale_col))
            else:
                # 降级：按位置推断，位置7通常是规模
                try:
                    val7 = row.iloc[7] if hasattr(row, 'iloc') else None
                    if val7 is not None and isinstance(val7, (int, float)) and 0 < val7 < 50000:
                        current_scale = float(val7)
                except Exception:
                    pass
            # v9.5.94: 规模过滤 — 阈值提到 5 亿（小规模基金清盘风险高），且 None 时降级用 issue_amount 检查
            scale_for_filter = current_scale
            if scale_for_filter is None and issue_amount is not None:
                try:
                    scale_for_filter = float(issue_amount)  # 用发行规模兜底（亿份）
                except (ValueError, TypeError):
                    pass
            if scale_for_filter is not None and scale_for_filter < 5.0:
                excluded_count += 1
                continue
            # 如果 current_scale 和 issue_amount 都拿不到，记录但不过滤（避免误杀）

            # ========== 新评分公式（V2）==========
            score = _compute_quality_score(r1y, r3y, r6m, r3m, fee, list_date, issue_amount)

            # 质量标签
            quality_tags = _compute_quality_tags(r1y, r3m, r6m, list_date, issue_amount)

            # v9.5.31: 一句话理由 + 风险等级
            reason = _compute_reason(name, fund_type, r1y, r3y, r6m, r3m, list_date, issue_amount)
            risk_level = _compute_risk_level(name, fund_type, r1y, r3m, r6m)

            candidates.append({
                "code": code,
                "name": name,
                "score": round(score, 2),
                "returns": {
                    "3m": r3m,
                    "6m": r6m,
                    "1y": r1y,
                    "3y": r3y,
                    "ytd": rytd,
                },
                "fee": fee,
                "quality_tags": quality_tags,
                "reason": reason,           # 一句话理由
                "risk_level": risk_level,   # low / mid / high
                "scale_billion": round(current_scale, 1) if current_scale else None,  # v9.5.89: 规模（亿）
            })
        except Exception:
            continue

    # 用户持仓去重（同类降权）
    if user_id:
        candidates = _apply_user_dedup(candidates, user_id)

    # 排序（v9.5.123: 加 code 作为第二排序键确保稳定性）
    if sort_by == "1y":
        candidates.sort(key=lambda x: (x["returns"].get("1y") or -999, x.get("code", "")), reverse=True)
    elif sort_by == "3y":
        candidates.sort(key=lambda x: (x["returns"].get("3y") or -999, x.get("code", "")), reverse=True)
    elif sort_by == "ytd":
        candidates.sort(key=lambda x: (x["returns"].get("ytd") or -999, x.get("code", "")), reverse=True)
    else:
        candidates.sort(key=lambda x: (x["score"], x.get("code", "")), reverse=True)

    # v9.5.123: A/C份额去重(同名基金只保留排名最高的一只)
    import re as _re_dedup
    _seen_base_names = set()
    _deduped = []
    for c in candidates:
        # 去掉末尾的A/B/C/E/H/I份额标识
        base_name = _re_dedup.sub(r'[A-Z]$', '', c.get("name", "").strip())
        base_name = _re_dedup.sub(r'(（LOF）|（QDII）|（FOF）)', '', base_name).strip()
        if base_name in _seen_base_names:
            continue
        _seen_base_names.add(base_name)
        _deduped.append(c)
    candidates = _deduped

    # v9.5.123: 彻底禁用HTTP二次规模过滤
    # 原因: 天天基金H5页面限流/正则误匹配,导致错误地把大量基金判定为<5亿
    # 规模已在硬过滤阶段通过 issue_amount/scale_col >= 5亿检查,不需要二次验证
    top = candidates[:top_n]
    result = {
        "funds": top,
        "total": len(candidates),
        "excluded_count": excluded_count,
        "filter": fund_type,
        "sort": sort_by,
        "quality_note": f"已过滤 {excluded_count} 只低质量基金（过热/小盘/新基/极端涨幅）",
    }
    _fund_screen_cache.set(cache_key, result, ttl=FUND_RANK_CACHE_TTL)
    _file_cache_set(cache_key, result)  # v9.5.108: 同步写文件，重启不丢
    return result


# v9.5.96: 实时拉规模 + 剔小盘（仅对前 30 只候选执行，避免拖慢全量计算）
# v9.5.108: 启动时从磁盘恢复，避免重启后冷启动 55 秒
_SCALE_CACHE_FILE = os.path.join(_FILE_CACHE_DIR, "_scale_cache.json")
_SCALE_CACHE_TTL = 86400  # 24h


def _load_scale_cache() -> dict:
    """从文件加载规模缓存（启动时调用）"""
    try:
        if os.path.exists(_SCALE_CACHE_FILE):
            with open(_SCALE_CACHE_FILE, "r", encoding="utf-8") as f:
                data = _json.load(f)
            now = time.time()
            return {k: tuple(v) for k, v in data.items() if (now - v[1]) < _SCALE_CACHE_TTL}
    except Exception:
        pass
    return {}


def _save_scale_cache():
    """保存规模缓存到文件（每次更新后调用）"""
    try:
        with open(_SCALE_CACHE_FILE, "w", encoding="utf-8") as f:
            _json.dump({k: list(v) for k, v in _scale_cache.items()}, f, ensure_ascii=False)
    except Exception:
        pass


_scale_cache: dict = _load_scale_cache()  # 启动时即恢复

def _filter_small_scale(candidates: list, min_scale: float = 5.0) -> list:
    """对候选基金调多层降级链拉规模，剔除 < min_scale 亿的小盘基金。

    降级链（v9.5.97）：
      L1: 天天基金 H5 概况页（最快，结构稳定）
      L2: Tushare fund_basic issue_amount（兜底，需要 token）
      L3: 联网搜索"<code> 基金规模"，从 snippet 提取（兜底 of 兜底）

    None（三层都失败）一律保留，避免误杀。同进程缓存 24h。
    """
    import time as _t
    import requests
    import re
    now = _t.time()
    out = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for c in candidates:
        code = c.get("code", "")
        if not code:
            out.append(c)
            continue
        cached = _scale_cache.get(code)
        if cached and (now - cached[1]) < _SCALE_CACHE_TTL:
            scale = cached[0]
        else:
            scale = None
            # L1: 天天基金 H5
            try:
                url = f"http://fundf10.eastmoney.com/jbgk_{code}.html"
                r = requests.get(url, headers=headers, timeout=4)
                m = re.search(r"资产规模[\s\S]{0,300}?([0-9.,]+)\s*亿元", r.text)
                if m:
                    scale = float(m.group(1).replace(",", ""))
            except Exception:
                pass

            # L2: Tushare fund_basic 的 issue_amount（仅参考发行规模）
            if scale is None:
                try:
                    from services.tushare_data import _call_tushare
                    for ts_code in [f"{code}.OF", f"{code}.SZ", f"{code}.SH"]:
                        rows = _call_tushare("fund_basic", {"ts_code": ts_code}, "ts_code,issue_amount")
                        if rows and rows[0].get("issue_amount"):
                            try:
                                scale = float(rows[0]["issue_amount"])
                                break
                            except (ValueError, TypeError):
                                pass
                except Exception:
                    pass

            # L3: 联网搜索（最后兜底）— 用通用工具
            if scale is None:
                try:
                    from services.web_search import lookup_fund_scale_by_search
                    scale = lookup_fund_scale_by_search(code)
                except Exception:
                    pass

            _scale_cache[code] = (scale, now)

        if scale is not None and scale < min_scale:
            continue  # 剔除小盘
        if scale is not None:
            c["scale_billion"] = round(scale, 2)
        out.append(c)
    # v9.5.108: 批量结束后保存到磁盘（一次性，避免每条都写文件）
    _save_scale_cache()
    return out


def _compute_quality_score(r1y, r3y, r6m, r3m, fee, list_date, issue_amount) -> float:
    """V2.1 评分公式：收益(30%)+稳定性(30%)+费率(10%)+成熟度(20%)+回撤惩罚(10%)
    
    基于V2稳定版本 + 3个优化:
    1. 新增回撤惩罚(近3月跌幅作为回撤代理,跌幅越大扣分越重)
    2. C类费率加分从+4降为+1(长持C类更贵)
    3. 过热惩罚阈值从20%降到15%(更早警告)
    """
    score = 0

    # ---- 收益维度 30% ----
    # 近1年贡献收益分，但超过50%后快速封顶（追高风险太大）
    if r1y <= 50:
        score += r1y * 0.20  # 正常范围: 30%=6分, 50%=10分
    else:
        # 超过50%后按递减逻辑加分（不再线性增加，防止暴涨基金霸榜）
        score += 50 * 0.20 + (r1y - 50) * 0.04  # 超出部分只加1/5
    if r3y is not None:
        r3y_ann = r3y / 3  # 年化
        score += min(max(r3y_ann, -20), 40) * 0.12
    if r6m is not None:
        score += min(max(r6m, -20), 40) * 0.08

    # ---- 稳定性维度 30% ----
    periods = [x for x in [r3m, r6m, r1y] if x is not None]
    if len(periods) >= 2:
        # 所有周期都为正 = 稳定上涨
        all_pos = all(x > 0 for x in periods)
        if all_pos:
            score += 6

        # v9.5.123优化1: 回撤惩罚(近3月跌幅作为回撤代理)
        if r3m is not None and r1y > 0 and r3m < -5:
            drawdown_proxy = abs(r3m)
            if drawdown_proxy > 20:
                score -= 10  # 重回撤: -20%以上重罚
            elif drawdown_proxy > 10:
                score -= 6   # 中回撤: -10%~-20%
            else:
                score -= 3   # 轻回撤: -5%~-10%

        # v9.5.123优化3: 过热惩罚阈值从20%降到15%
        if r3m is not None and r3m > 15:
            overheat_penalty = (r3m - 15) * 0.5
            score -= min(overheat_penalty, 12)  # 最多扣12分

        # 1年涨幅过高额外惩罚（>60%显著惩罚）
        if r1y > 60:
            hot_penalty = (r1y - 60) * 0.25
            score -= min(hot_penalty, 10)

        # 波动惩罚：长短期收益差距大
        spread = max(periods) - min(periods)
        if spread > 60:
            score -= 6
        elif spread > 40:
            score -= 3

    # ---- 持续性因子(信息比率代理) 5% ----
    # 如果多个时间段都为正且递增,说明基金持续跑赢不是一波运气
    if r1y is not None and r6m is not None and r3m is not None:
        # 所有3个周期>0 + 短期不弱于长期年化 = 持续优秀
        if r1y > 10 and r6m > 5 and r3m > 0:
            # 近3月/近6月都保持正收益 = 持续性好
            score += 3
            # 额外:近3月还在加速(3m年化 > 1y) = 动能持续
            if r3m * 4 > r1y * 0.8:  # 3月年化 > 1年收益的80%
                score += 2  # 加速奖励
        elif r1y > 0 and r6m > 0 and r3m < -3:
            # 1年/6月正但近3月转负 = 动能衰竭, 不扣分但不加分
            pass

    # ---- 费率维度 10% ----
    # v9.5.123优化2: C类费率从+4降为+1(长持C类管理费更贵)
    fee_pct = _parse_fee(fee)
    if fee_pct is not None:
        if fee_pct < 0.15:
            score += 1  # C类0费率(短期省钱但长持不划算)
        elif fee_pct < 0.5:
            score += 2  # A类低费率反而加更多分(鼓励长持)
        elif fee_pct > 1.5:
            score -= 3

    # ---- 成熟度维度 20% ----
    if list_date:
        try:
            from datetime import datetime as _dt
            age_years = (time.time() - _dt.strptime(list_date, "%Y%m%d").timestamp()) / (365.25 * 86400)
            if age_years >= 5:
                score += 5  # 老基金加分(经历过牛熊验证)
            elif age_years >= 3:
                score += 3
        except (ValueError, TypeError):
            pass

    # 规模适中加分（5-200亿最优区间）
    if issue_amount is not None:
        try:
            amt = float(issue_amount)
            if 5 <= amt <= 200:
                score += 3
        except (ValueError, TypeError):
            pass

    return score


def _compute_quality_tags(r1y, r3m, r6m, list_date, issue_amount) -> list:
    """生成质量标签（前端展示用）"""
    tags = []
    if list_date:
        try:
            from datetime import datetime as _dt
            age_years = (time.time() - _dt.strptime(list_date, "%Y%m%d").timestamp()) / (365.25 * 86400)
            if age_years >= 5:
                tags.append("🏛️ 老牌基金")
        except (ValueError, TypeError):
            pass

    if issue_amount is not None:
        try:
            amt = float(issue_amount)
            if 10 <= amt <= 100:
                tags.append("📐 规模适中")
        except (ValueError, TypeError):
            pass

    # 收益一致性
    periods = [x for x in [r3m, r6m, r1y] if x is not None]
    if len(periods) >= 2 and all(x > 0 for x in periods):
        tags.append("📈 持续盈利")

    return tags


# v9.5.31: 一句话理由生成（纯规则，无 LLM）
def _compute_reason(name: str, fund_type: str, r1y, r3y, r6m, r3m, list_date, issue_amount) -> str:
    """根据基金特征 + 收益数据 拼一句最有信息量的话"""
    nm = name or ""

    # 红利低波 / 价值类 — 防御属性
    if any(k in nm for k in ["红利", "低波", "价值"]):
        return f"🛡️ 防御型品种，近1年{(r1y or 0):+.1f}%稳健跑赢同类"

    # 黄金 / 商品 — 避险
    if "黄金" in nm or "商品" in nm:
        return "🌟 避险资产，对冲不确定性"

    # 货币基金
    if "货币" in nm:
        return "💵 现金管理工具，年化 2% 左右"

    # 海外科技/QDII
    if any(k in nm for k in ["纳斯达克", "纳指", "标普", "美股", "美国"]):
        return f"🌏 美股科技敞口，近1年{(r1y or 0):+.1f}%（人民币计价）"
    if "港股" in nm or "恒生" in nm:
        return f"🇭🇰 港股配置，近1年{(r1y or 0):+.1f}%"
    if "QDII" in nm or "海外" in nm or "全球" in nm:
        return f"🌐 全球配置，分散A股波动（近1年{(r1y or 0):+.1f}%）"

    # 短期暴涨 — 警示
    if r3m is not None and r3m > 40:
        return f"🌬️ 近3月暴涨{r3m:.0f}%，警惕短期回调（拥挤度高）"

    # 高一年涨幅
    if r1y is not None and r1y > 50:
        if r3y is not None and r3y > 30:
            return f"📈 近1年涨{r1y:.0f}% + 3年{r3y:.0f}%，长期亮眼"
        return f"📈 近1年涨{r1y:.0f}%，关注是否短期过热"

    # 三年长跑
    if r3y is not None and r3y > 50 and r1y is not None and r1y > 0:
        return f"🏆 3年涨{r3y:.0f}%长跑型，穿越牛熊"

    # 行业主题
    if any(k in nm for k in ["半导体", "芯片", "光刻"]):
        return f"💎 半导体国产替代主题，近1年{(r1y or 0):+.1f}%"
    if any(k in nm for k in ["人工智能", "AI", "算力", "云计算"]):
        return f"🤖 AI算力主题，近1年{(r1y or 0):+.1f}%（拥挤度需观察）"
    if any(k in nm for k in ["医药", "医疗", "生物"]):
        return f"💊 医药板块，近1年{(r1y or 0):+.1f}%（行业 beta）"
    if any(k in nm for k in ["新能源", "电池", "光伏"]):
        return f"⚡ 新能源板块，近1年{(r1y or 0):+.1f}%"
    if any(k in nm for k in ["军工", "国防"]):
        return f"⚔️ 军工主题，近1年{(r1y or 0):+.1f}%"
    if any(k in nm for k in ["消费", "白酒", "食品"]):
        return f"🍷 大消费板块，近1年{(r1y or 0):+.1f}%"

    # 宽基指数
    if "沪深300" in nm:
        return f"📊 大盘宽基定投首选，近1年{(r1y or 0):+.1f}%"
    if "中证500" in nm or "中证1000" in nm:
        return f"📊 中小盘指数，近1年{(r1y or 0):+.1f}%"
    if "深证100" in nm:
        return f"📊 深市核心宽基，近1年{(r1y or 0):+.1f}%"
    if "创业板" in nm or "科创" in nm:
        return f"🚀 成长风格指数，近1年{(r1y or 0):+.1f}%"

    # 债券类
    if fund_type == "bond" or "债" in nm:
        if r1y is not None and r1y > 5:
            return f"🛡️ 纯债稳健，近1年{r1y:.1f}%（低波底仓）"
        return "🛡️ 债券类品种，平滑波动用"

    # 老牌基金
    if list_date:
        try:
            from datetime import datetime as _dt
            age_years = (time.time() - _dt.strptime(list_date, "%Y%m%d").timestamp()) / (365.25 * 86400)
            if age_years >= 8:
                return f"🏛️ 老牌基金{int(age_years)}年历史，近1年{(r1y or 0):+.1f}%"
        except (ValueError, TypeError):
            pass

    # 默认 fallback
    if r1y is not None:
        return f"📊 综合评分中上，近1年{r1y:+.1f}%"
    return "📊 综合评分中上"


# v9.5.31: 风险等级判定
def _compute_risk_level(name: str, fund_type: str, r1y, r3m, r6m) -> str:
    """返回 low / mid / high"""
    nm = name or ""
    # 低波动：债基/货币/红利低波/纯债
    if fund_type == "bond" or any(k in nm for k in ["货币", "纯债", "红利低波", "短债"]):
        return "low"
    # 高波动：单一行业主题、近3月>30%、QDII科技、杠杆
    if r3m is not None and r3m > 30:
        return "high"
    if any(k in nm for k in ["半导体", "芯片", "AI", "人工智能", "算力", "军工", "新能源", "光伏"]):
        return "high"
    if any(k in nm for k in ["纳斯达克", "纳指", "QDII", "海外科技", "全球科技"]):
        return "high"
    # 中等：宽基指数、价值蓝筹、混合
    if any(k in nm for k in ["沪深300", "中证500", "深证100", "上证50", "红利", "价值", "蓝筹"]):
        return "mid"
    if fund_type == "index":
        return "mid"
    # 默认主动权益按中等
    if r1y is not None and abs(r1y) < 20:
        return "mid"
    return "high"


def _load_ts_rank_map() -> dict:
    """加载 Tushare fund_rank_ts.json 中的质量字段"""
    import json
    from pathlib import Path
    import os

    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    rank_file = data_dir / "fund_rank_ts.json"
    if not rank_file.exists():
        # 尝试相对路径
        rank_file = Path("data") / "fund_rank_ts.json"
    if not rank_file.exists():
        return {}

    try:
        data = json.loads(rank_file.read_text(encoding="utf-8"))
        ranks = data.get("ranks", {})
        result = {}
        for category_list in ranks.values():
            if not isinstance(category_list, list):
                continue
            for item in category_list:
                code = item.get("code", "")
                if code and (item.get("list_date") or item.get("issue_amount")):
                    result[code] = {
                        "list_date": item.get("list_date", ""),
                        "issue_amount": item.get("issue_amount"),
                    }
        return result
    except Exception:
        return {}


def _apply_user_dedup(candidates: list, user_id: str) -> list:
    """用户持仓去重：已持有同类基金的降权50%"""
    try:
        from services.fund_monitor import load_fund_holdings
        user_funds = load_fund_holdings(user_id)
        if not user_funds:
            return candidates

        # v9.5.123: 只用行业/赛道关键词做去重(去掉"混合""指数"等太泛的词)
        user_keywords = set()
        _SECTOR_KEYWORDS = ["科技", "半导体", "新能源", "医药", "消费", "金融", "军工",
                            "白酒", "芯片", "光伏", "锂电", "AI", "机器人", "汽车",
                            "黄金", "煤炭", "银行", "地产", "农业"]
        for f in user_funds:
            name = f.get("name", "")
            for kw in _SECTOR_KEYWORDS:
                if kw in name:
                    user_keywords.add(kw)

        if not user_keywords:
            return candidates

        # 同赛道基金降权(只降30%,不要太激进)
        for c in candidates:
            name = c.get("name", "")
            overlap = sum(1 for kw in user_keywords if kw in name)
            if overlap >= 2:
                # 多个关键词重叠=高度重复
                c["score"] = c["score"] * 0.6
                if "quality_tags" not in c:
                    c["quality_tags"] = []
                c["quality_tags"].append("⚠️ 与持仓高度重复")
            elif overlap == 1:
                c["score"] = c["score"] * 0.8  # 轻度降权
                if "quality_tags" not in c:
                    c["quality_tags"] = []
                c["quality_tags"].append("📎 与持仓同赛道")

        return candidates
    except Exception:
        return candidates


# v9.8.7: 单只基金详情快速查询（用于 codes 参数精确查询场景）
def _enrich_single_fund(code: str, user_id: str = "") -> dict:
    """根据基金代码查询单只基金的完整信息（与 screen_funds 返回格式兼容）

    复用已有的 fund_rank 数据源，跳过全量筛选/排序/评分逻辑。
    """
    # 1. 从 fund_rank 数据中查找
    rank_data = _load_fund_rank_data()
    if not rank_data or code not in rank_data:
        # fallback: 尝试用 Tushare 直接查
        return _enrich_from_tushare(code)

    row = rank_data[code]
    cols = list(row.index) if hasattr(row, "index") else []
    name = str(row.get(_find_col(cols, ["简称", "名称"]) or cols[1], ""))

    # 提取收益数据
    def _val(key):
        v = row.get(key, None)
        return _safe_float(v)

    r3m = _val("近3月") or _val("r3m") or 0
    r6m = _val("近6月") or _val("r6m") or 0
    r1y = _val("近1年") or _val("r1y") or 0
    r3y = _val("近3年") or _val("r3y") or 0
    rytd = _val("今年") or _val("ytd") or 0

    fee_str = str(row.get(_find_col(cols, ["费率", "管理费"]) or "", ""))
    fee = _parse_fee(fee_str)

    scale_val = _safe_float(row.get(_find_col(cols, ["规模", "资产净值"]) or None))
    current_scale = scale_val / 1e8 if scale_val and scale_val > 1e6 else None

    quality_tags = _compute_quality_tags(r1y, r3m, r6m, None, None)
    reason = _compute_reason(name, "", r1y, r3y, r6m, r3m, None, None)
    risk_level = _compute_risk_level(name, "", r1y, r3y, r6m)

    fund = {
        "code": code,
        "name": name,
        "score": 50.0,  # 快速路径不做评分（不参与排名）
        "returns": {
            "3m": round(r3m, 2),
            "6m": round(r6m, 2),
            "1y": round(r1y, 2),
            "3y": round(r3y, 2),
            "ytd": round(rytd, 2),
        },
        "fee": fee,
        "quality_tags": quality_tags,
        "reason": reason,
        "risk_level": risk_level,
        "scale_billion": round(current_scale, 1) if current_scale else None,
    }

    # 用户持仓增强（如果有 userId）
    if user_id:
        fund = _apply_single_user_enrich(fund, user_id)

    return fund


def _enrich_from_tushare(code: str) -> dict:
    """Tushare fallback：当 fund_rank 数据中找不到时直接查询"""
    try:
        from services.tushare_data import is_configured, _call_tushare, _code_to_ts
        if not is_configured():
            return {"code": code, "name": f"未知({code})", "score": 0}

        ts_code = _code_to_ts(code)
        rows = _call_tushare("fund_basic", {"ts_code": ts_code}, "ts_code,name,found_date,invest_type,management_fee")
        if not rows:
            return {"code": code, "name": f"未找到({code})", "score": 0}
        r = rows[0]
        return {
            "code": code,
            "name": str(r.get("name", code)),
            "score": 0,
            "returns": {"3m": 0, "6m": 0, "1y": 0, "3y": 0, "ytd": 0},
            "fee": str(r.get("management_fee", "-")) if r.get("management_fee") else "-",
            "quality_tags": [],
            "reason": "",
            "risk_level": "mid",
            "scale_billion": None,
        }
    except Exception as e:
        print(f"[FUND_SCREEN] tushare fallback error for {code}: {e}")
        return {"code": code, "name": f"错误({code})", "error": str(e), "score": 0}


def _apply_single_user_enrich(fund: dict, user_id: str) -> dict:
    """对单只基金做用户级增强（holding_relation / nav_percentile / industry_tag）"""
    try:
        # 检查是否持仓
        user_file = DATA_DIR / f"users/{user_id}.json"
        if user_file.exists():
            user_data = json.loads(user_file.read_text(encoding="utf-8"))
            holdings = user_data.get("holdings", [])
            codes_held = {h.get("code", "") for h in holdings}
            if fund["code"] in codes_held:
                fund["holding_relation"] = "🔵 已持仓"
                # 找到对应持仓的盈亏等
                for h in holdings:
                    if h.get("code") == fund["code"]:
                        if h.get("shares"):
                            fund["my_shares"] = h["shares"]
                        break
    except Exception as e:
        print(f"[FUND_SCREEN] user enrich error: {e}")
    return fund

