"""
钱袋子 — V6.5 模块 J: 业务敞口分析
数据源：Tushare fina_mainbz（按地区/产品分类的收入构成）
功能：分析出口敞口 → 与地缘模块联动 → 识别关税/制裁风险

Tushare fina_mainbz 参数:
  type='D' → 按地区（国内/国外/境内/境外）
  type='P' → 按产品线
"""

MODULE_META = {
    "name": "business_exposure",
    "scope": "public",
    "input": ["fina_mainbz"],
    "output": "exposure_analysis",
    "cost": "api_light",
    "tags": ["业务敞口", "出口", "区域", "地缘脆弱性"],
    "description": "业务敞口分析（出口/区域/产品线）+ 地缘脆弱性评估",
    "layer": "data",
    "priority": 4,
}

import time
from datetime import datetime

_exposure_cache = {}
_EXPOSURE_CACHE_TTL = 86400  # 24小时

# 地缘→出口区域脆弱性映射
_GEO_VULNERABILITY = {
    "美国": ["制裁", "关税", "实体清单", "芯片禁令"],
    "欧洲": ["碳关税", "GDPR", "反补贴"],
    "东南亚": ["供应链转移"],
    "中东": ["能源价格", "运输中断"],
}


def get_business_exposure(code: str) -> dict:
    """获取个股的业务敞口分析"""
    cache_key = f"exposure_{code}"
    now = time.time()
    if cache_key in _exposure_cache and now - _exposure_cache[cache_key]["ts"] < _EXPOSURE_CACHE_TTL:
        return _exposure_cache[cache_key]["data"]

    result = {"available": False, "code": code, "export_exposure": 0,
              "geo_vulnerability": "未知", "vulnerable_to": []}

    try:
        from services.tushare_data import is_configured, _call_tushare, _code_to_ts
        if not is_configured():
            return result

        ts_code = _code_to_ts(code)

        # 1. 按地区的收入分布
        rows = _call_tushare(
            "fina_mainbz",
            {"ts_code": ts_code, "type": "D"},
            "ts_code,end_date,bz_item,bz_sales,bz_profit,bz_cost",
        )

        if not rows:
            _exposure_cache[cache_key] = {"data": result, "ts": now}
            return result

        # 取最新一期
        latest_date = max(r.get("end_date", "") for r in rows)
        latest = [r for r in rows if r.get("end_date") == latest_date]

        domestic_sales = 0
        foreign_sales = 0
        regions = {}

        for r in latest:
            item = r.get("bz_item", "")
            sales = float(r.get("bz_sales", 0) or 0)
            if not item or sales <= 0:
                continue

            # 判断国内/国外
            is_foreign = any(kw in item for kw in ["国外", "境外", "海外", "出口", "外销",
                                                    "美国", "欧洲", "亚洲", "其他地区"])
            is_domestic = any(kw in item for kw in ["国内", "境内", "内销", "中国大陆"])

            if is_foreign:
                foreign_sales += sales
            elif is_domestic:
                domestic_sales += sales
            else:
                # 不明确的归国内
                domestic_sales += sales

            regions[item] = round(sales / 1e8, 2)  # 转亿元

        total_sales = domestic_sales + foreign_sales
        export_pct = round(foreign_sales / max(total_sales, 1) * 100, 1) if total_sales > 0 else 0

        result["available"] = True
        result["export_exposure"] = export_pct
        result["domestic_sales"] = round(domestic_sales / 1e8, 2)
        result["foreign_sales"] = round(foreign_sales / 1e8, 2)
        result["total_sales"] = round(total_sales / 1e8, 2)
        result["regions"] = regions
        result["report_date"] = latest_date

        # 地缘脆弱性评估
        if export_pct > 60:
            result["geo_vulnerability"] = "高"
            result["vulnerable_to"] = ["关税", "制裁", "供应链脱钩"]
        elif export_pct > 30:
            result["geo_vulnerability"] = "中"
            result["vulnerable_to"] = ["关税"]
        elif export_pct > 10:
            result["geo_vulnerability"] = "低"
        else:
            result["geo_vulnerability"] = "极低"
            result["vulnerable_to"] = []

        # 2. 按产品线（补充）
        try:
            prod_rows = _call_tushare(
                "fina_mainbz",
                {"ts_code": ts_code, "type": "P", "start_date": latest_date, "end_date": latest_date},
                "ts_code,end_date,bz_item,bz_sales",
            )
            if prod_rows:
                products = {}
                for r in prod_rows:
                    item = r.get("bz_item", "")
                    sales = float(r.get("bz_sales", 0) or 0)
                    if item and sales > 0:
                        products[item] = round(sales / 1e8, 2)
                result["products"] = dict(sorted(products.items(), key=lambda x: x[1], reverse=True)[:10])
        except Exception:
            pass

        print(f"[EXPOSURE] {code}: 出口{export_pct}%, 地缘脆弱性={result['geo_vulnerability']}")

    except Exception as e:
        print(f"[EXPOSURE] {code} failed: {e}")

    _exposure_cache[cache_key] = {"data": result, "ts": now}
    return result


def enrich(ctx):
    """Pipeline 注入 — 为持仓股票补充业务敞口"""
    try:
        holdings = ctx.modules_results.get("stock_holdings", {}).get("holdings", [])
        if not holdings:
            return ctx

        exposures = {}
        high_risk = []
        for h in holdings[:5]:
            code = h.get("code", "")
            if code:
                exp = get_business_exposure(code)
                if exp.get("available"):
                    exposures[code] = {
                        "export_pct": exp["export_exposure"],
                        "vulnerability": exp["geo_vulnerability"],
                        "vulnerable_to": exp.get("vulnerable_to", []),
                    }
                    if exp["geo_vulnerability"] in ("高", "中"):
                        high_risk.append(f"{h.get('name', code)}(出口{exp['export_exposure']}%)")

        if exposures:
            detail = f"业务敞口覆盖{len(exposures)}只"
            if high_risk:
                detail += f", ⚠️地缘敏感: {','.join(high_risk)}"

            ctx.modules_results["business_exposure"] = {
                "available": True,
                "exposures": exposures,
                "high_risk_stocks": high_risk,
                "detail": detail,
            }

        if "business_exposure" not in ctx.modules_called:
            ctx.modules_called.append("business_exposure")

    except Exception as e:
        print(f"[EXPOSURE] enrich failed: {e}")

    return ctx
