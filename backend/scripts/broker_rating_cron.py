#!/usr/bin/env python3
"""
机构评级周报 cron
每周日 20:00 执行一次
职责：
  1. 读取所有用户的持仓（基金+股票）
  2. 用 Tushare report_rc 拉近期机构研报评级
  3. 用 DeepSeek 汇总为一段简明的评级摘要
  4. 写入 analysis_history source='broker'
  
设计原则：
  - 纯数据+AI总结，不预测价格，不给操作建议
  - 一周一次即可（机构研报更新频率月频为主）
  - 失败不阻断，每只资产独立 try/except
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from config import DATA_DIR


def _load_profiles() -> list:
    f = DATA_DIR / "profiles.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _get_user_holdings(user_id: str) -> list:
    """获取用户所有持仓的代码列表"""
    holdings = []
    try:
        from services.fund_monitor import load_fund_holdings
        funds = load_fund_holdings(user_id) or []
        for h in funds:
            code = h.get("code", "")
            name = h.get("name", code)
            if code:
                holdings.append({"code": code, "name": name, "type": "fund"})
    except Exception as e:
        print(f"  [BROKER] 拉基金持仓失败: {e}")
    try:
        from services.stock_monitor import load_stock_holdings
        stocks = load_stock_holdings(user_id) or []
        for h in stocks:
            code = h.get("code", "")
            name = h.get("name", code)
            if code:
                holdings.append({"code": code, "name": name, "type": "stock"})
    except Exception as e:
        print(f"  [BROKER] 拉股票持仓失败: {e}")
    return holdings


def _fetch_broker_ratings(code: str, asset_type: str) -> list:
    """拉单只资产的近期机构评级
    
    股票：用 tushare report_rc（需要积分）
    基金：akshare 基金评级/研报接口
    返回 [{"org": "机构", "rating": "买入", "date": "YYYY-MM-DD", "title": "..."}]
    """
    ratings = []
    try:
        if asset_type == "stock":
            from services.tushare_data import _call_tushare, _code_to_ts
            ts_code = _code_to_ts(code)
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            rows = _call_tushare("report_rc", {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
            }, "ts_code,report_date,org_name,rating,report_title")
            for row in (rows or [])[:5]:  # 最近5条
                if row.get("rating"):
                    ratings.append({
                        "org": row.get("org_name", ""),
                        "rating": row.get("rating", ""),
                        "date": row.get("report_date", ""),
                        "title": row.get("report_title", "")[:60],
                    })
        elif asset_type == "fund":
            # 基金没有标准机构评级接口，用 akshare 基金评级
            try:
                import akshare as ak
                # 天天基金评级数据
                df = ak.fund_rating_all()
                if df is not None and not df.empty:
                    # 尝试匹配基金代码
                    matched = df[df.apply(lambda r: code in str(r.values), axis=1)]
                    for _, row in matched.head(3).iterrows():
                        rating_val = ""
                        for col in row.index:
                            if "评级" in str(col) or "rating" in str(col).lower():
                                rating_val = str(row[col])
                                break
                        if rating_val:
                            ratings.append({
                                "org": "天天基金",
                                "rating": rating_val,
                                "date": datetime.now().strftime("%Y-%m-%d"),
                                "title": f"{code} 综合评级",
                            })
            except Exception:
                pass
    except Exception as e:
        print(f"  [BROKER] 拉评级失败 {code}: {e}")
    return ratings


def _summarize_with_deepseek(user_id: str, holdings_ratings: list) -> str:
    """用 DeepSeek 汇总机构评级为简明摘要"""
    if not holdings_ratings:
        return ""
    
    lines = []
    for item in holdings_ratings:
        h = item["holding"]
        rs = item["ratings"]
        if rs:
            rating_desc = "；".join([f"{r['org']}:{r['rating']}({r['date'][:7]})" for r in rs[:3]])
            lines.append(f"- {h['name']}({h['code']}): {rating_desc}")
        else:
            lines.append(f"- {h['name']}({h['code']}): 近期暂无机构研报")
    
    holdings_text = "\n".join(lines)
    
    prompt = f"""以下是用户持仓的机构评级汇总（来自券商研报/天天基金评级）：

{holdings_text}

请用3-5句话，客观总结：
1. 机构整体对这批持仓的评价倾向（看多/中性/看空）
2. 哪些持仓获得更多机构认可
3. 需要关注的评级变化

要求：
- 只描述机构观点，不给自己的操作建议
- 不预测价格
- 简洁客观，不超过150字"""

    try:
        from services.ds_enhance import _call_deepseek
        result = _call_deepseek(
            prompt,
            system="你是客观的机构观点汇总助手，只描述机构评级事实，不给投资建议。",
            max_tokens=200,
            cache_key=f"broker_summary_{user_id}_{datetime.now().strftime('%Y%W')}",
        )
        return result or ""
    except Exception as e:
        print(f"  [BROKER] DeepSeek汇总失败: {e}")
        return ""


def run_broker_rating(user_id: str):
    """为一个用户生成机构评级周报"""
    print(f"[BROKER] 开始处理用户: {user_id}")
    
    holdings = _get_user_holdings(user_id)
    if not holdings:
        print(f"  [BROKER] 用户 {user_id} 无持仓，跳过")
        return
    
    print(f"  [BROKER] 持仓数: {len(holdings)}")
    
    # 拉每只资产的机构评级
    holdings_ratings = []
    for h in holdings[:8]:  # 最多处理8只（接口限速）
        ratings = _fetch_broker_ratings(h["code"], h["type"])
        holdings_ratings.append({"holding": h, "ratings": ratings})
        has_ratings = len([r for r in ratings if r])
        print(f"  [BROKER] {h['name']}({h['code']}): {len(ratings)} 条评级")
    
    # 汇总
    rated_count = sum(1 for item in holdings_ratings if item["ratings"])
    if rated_count == 0:
        # 即使没有机构数据，也写入一条"暂无评级"的记录，避免每周重复拉取
        summary = f"本周查询了 {len(holdings)} 只持仓的机构评级，均暂无近期研报数据（可能是基金类资产，机构评级覆盖较少）。"
    else:
        summary = _summarize_with_deepseek(user_id, holdings_ratings)
    
    if not summary:
        summary = f"本周机构评级查询完成，{rated_count}/{len(holdings)} 只持仓有近期研报。"
    
    # 构建详细内容
    detail_lines = [f"## 机构评级周报 {datetime.now().strftime('%Y年第%W周')}\n"]
    for item in holdings_ratings:
        h = item["holding"]
        rs = item["ratings"]
        type_label = "基金" if h["type"] == "fund" else "股票"
        detail_lines.append(f"### {h['name']}（{h['code']}）[{type_label}]")
        if rs:
            for r in rs[:3]:
                detail_lines.append(f"- {r['date'][:7]} {r['org']} 评级：**{r['rating']}**")
                if r.get("title"):
                    detail_lines.append(f"  研报：{r['title']}")
        else:
            detail_lines.append("- 近3个月暂无机构研报覆盖")
        detail_lines.append("")
    
    detail_lines.append(f"\n**AI摘要**：{summary}")
    full_text = "\n".join(detail_lines)
    
    # 写入 analysis_history
    try:
        from services.analysis_history import save_analysis
        result = save_analysis(
            user_id=user_id,
            source="broker",
            source_label="机构评级",
            analysis_type="fund",
            analysis_text=full_text,
            direction="neutral",
            confidence=0,
            metadata={
                "holdings_count": len(holdings),
                "rated_count": rated_count,
                "week": datetime.now().strftime("%Y-W%W"),
            }
        )
        if result.get("ok"):
            print(f"  [BROKER] 写入成功: {result['id']}")
        else:
            print(f"  [BROKER] 写入失败: {result.get('error')}")
    except Exception as e:
        print(f"  [BROKER] 写入异常: {e}")


def main():
    print(f"[BROKER] 机构评级周报 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    profiles = _load_profiles()
    if not profiles:
        print("[BROKER] 无用户 Profile，退出")
        return
    
    for p in profiles:
        uid = p["id"]
        name = p.get("name", uid)
        if uid == "Guest":
            continue  # 跳过访客
        try:
            run_broker_rating(uid)
        except Exception as e:
            print(f"[BROKER] 用户 {name} 异常: {e}")
    
    print("[BROKER] 完成")


if __name__ == "__main__":
    main()
