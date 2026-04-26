"""
用户 API（用户数据 / 偏好 / 家庭汇总 / OCR 记账 / 记账 / 收入源 / 数据审计）
============================================================================
从 main.py 提取的 P2 路由。

Design doc: docs/design/12-framework-refactor.md §四
"""
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

router = APIRouter(tags=["用户与记账"])

from config import DATA_DIR, RECEIPTS_DIR
from models.schemas import (
    UserData, LedgerEntry, IncomeSourceCreate, IncomeSourceRecord,
)
from services.persistence import load_user, save_user, _user_file
from services.portfolio_calc import calc_holdings_from_transactions, ensure_v4_portfolio
from services.data_layer import (
    get_fund_nav, get_valuation_percentile, get_fear_greed_index,
    get_macro_calendar, get_market_news, get_policy_news,
)

from api.shared_helpers import (
    _do_ocr, USER_DEFAULTS, USER_OVERRIDES,
    FAMILY_MEMBERS, NICKNAMES,
)


# ---- 用户数据持久化 ----

@router.post("/api/user/save")
def save_user_data(data: UserData):
    """保存用户数据到服务端（兼容V3和V4）"""
    user = load_user(data.userId)
    if data.portfolio:
        if isinstance(data.portfolio, dict):
            user["portfolio"] = data.portfolio
        else:
            user["portfolio"] = data.portfolio
    if data.ledger:
        user["ledger"] = data.ledger
    if not user.get("createdAt"):
        user["createdAt"] = datetime.now().isoformat()
    save_user(user)
    return {"status": "ok", "userId": data.userId}


# ---- 用户偏好 ----
# ⚠️ 必须在 /api/user/{user_id} 之前定义，否则 FastAPI 会把 "preference" 当 user_id

@router.get("/api/user/preference")
def get_user_preference(userId: str):
    """获取用户偏好（Simple/Pro模式、推送、盯盘阈值）"""
    user = load_user(userId)
    defaults = USER_DEFAULTS.copy()
    overrides = USER_OVERRIDES.get(userId, {})

    return {
        "display_mode": user.get("display_mode", overrides.get("display_mode", defaults["display_mode"])),
        "risk_profile": user.get("risk_profile", overrides.get("risk_profile", defaults["risk_profile"])),
        "push_preferences": user.get("push_preferences", overrides.get("push_preferences", defaults["push_preferences"])),
        "watchlist_config": user.get("watchlist_config", overrides.get("watchlist_config", defaults["watchlist_config"])),
    }


@router.put("/api/user/preference")
def update_user_preference(userId: str, body: dict):
    """更新用户偏好"""
    from services.audit_log import audit_log
    user = load_user(userId)

    changed = {}
    for key in ["display_mode", "risk_profile", "push_preferences", "watchlist_config"]:
        if key in body:
            old_val = user.get(key)
            user[key] = body[key]
            changed[key] = {"old": old_val, "new": body[key]}

    save_user(user)
    audit_log("preference_update", user_id=userId, detail=changed)
    return {"success": True, "changed": list(changed.keys())}


@router.get("/api/user/{user_id}")
def get_user_data(user_id: str):
    """读取用户数据"""
    user = load_user(user_id)
    return user


@router.delete("/api/user/{user_id}")
def delete_user_data(user_id: str):
    """删除用户数据"""
    f = _user_file(user_id)
    if f.exists():
        f.unlink()
    return {"status": "ok"}


# ---- 家庭资产汇总 ----

@router.get("/api/household/summary")
def household_summary():
    """家庭资产汇总 — 两人持仓合计 + 各自明细"""
    members = []
    total_value = 0
    total_change = 0

    for uid in FAMILY_MEMBERS:
        user = load_user(uid)
        portfolio = ensure_v4_portfolio(user.get("portfolio") or {})
        holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
        assets = user.get("assets") or portfolio.get("assets") or []

        try:
            from services.unified_networth import calc_net_worth
            nw = calc_net_worth(holdings, assets)
        except Exception:
            nw = {"total": 0, "daily_change": 0}

        member_value = nw.get("total", 0)
        member_change = nw.get("daily_change", 0)
        members.append({
            "userId": uid,
            "nickname": NICKNAMES.get(uid, uid),
            "value": member_value,
            "change": member_change,
        })
        total_value += member_value
        total_change += member_change

    yesterday = total_value - total_change
    change_pct = (total_change / yesterday) if yesterday > 0 else 0.0

    return {
        "total_value": total_value,
        "daily_change": total_change,
        "daily_change_pct": change_pct,
        "members": members,
    }


# ---- OCR 记账 ----

@router.post("/api/receipt/ocr")
async def ocr_receipt(file: UploadFile = File(...), userId: str = Form("")):
    """拍照识别小票 → 自动提取金额和商品"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "请上传图片文件")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")

    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    receipt_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    receipt_path = RECEIPTS_DIR / f"{receipt_id}.{ext}"
    receipt_path.write_bytes(content)

    ocr_result = await _do_ocr(receipt_path, content)

    if userId and ocr_result.get("amount", 0) > 0:
        user = load_user(userId)
        screenshot_type = ocr_result.get("screenshot_type", "consumption")

        if screenshot_type in ("fund_buy", "fund_sell"):
            user = ensure_v4_portfolio(user)
            p = user["portfolio"]
            tx_id = f"tx_ocr_{receipt_id}"
            tx = {
                "id": tx_id,
                "type": "BUY" if screenshot_type == "fund_buy" else "SELL",
                "code": ocr_result.get("fund_code", ""),
                "name": ocr_result.get("fund_name", ""),
                "amount": ocr_result["amount"],
                "shares": ocr_result.get("shares", 0),
                "nav": ocr_result.get("nav", 0),
                "fee": 0,
                "date": ocr_result.get("date", datetime.now().isoformat()),
                "source": "ocr",
                "note": f"OCR识别 - {ocr_result.get('fund_name', '')}",
            }
            p["transactions"].append(tx)
            save_user(user)
            ocr_result["saved"] = True
            ocr_result["savedAs"] = "transaction"
            ocr_result["transaction"] = tx

        elif screenshot_type == "bank_tx" and ocr_result.get("bank_balance", 0) > 0:
            user = ensure_v4_portfolio(user)
            p = user["portfolio"]
            bank_name = ocr_result.get("merchant", "银行卡")
            existing = None
            for a in p.get("assets", []):
                if a.get("type") == "cash" and bank_name in a.get("name", ""):
                    existing = a
                    break
            if existing:
                existing["balance"] = ocr_result["bank_balance"]
                existing["updated"] = datetime.now().strftime("%Y-%m-%d")
            else:
                p.setdefault("assets", []).append({
                    "id": f"a_ocr_{receipt_id}",
                    "type": "cash",
                    "name": bank_name,
                    "balance": ocr_result["bank_balance"],
                    "updated": datetime.now().strftime("%Y-%m-%d"),
                })
            save_user(user)
            ocr_result["saved"] = True
            ocr_result["savedAs"] = "asset"

        else:
            # 消费/收入 → 写入记账
            entry = {
                "id": receipt_id,
                "date": ocr_result.get("date", datetime.now().isoformat()),
                "amount": ocr_result["amount"],
                "category": ocr_result.get("category", "其他"),
                "note": ocr_result.get("merchant", "") or ocr_result.get("note", ""),
                "direction": "income" if screenshot_type == "income" else "expense",
                "source": "ocr",
            }
            user.setdefault("ledger", []).append(entry)
            save_user(user)
            ocr_result["saved"] = True
            ocr_result["savedAs"] = "ledger"
            ocr_result["entryId"] = receipt_id

    return ocr_result


# ---- 记账 CRUD ----

@router.post("/api/ledger/add")
def add_ledger_entry(entry: LedgerEntry):
    """手动添加记账条目（支持收入/支出）"""
    user = load_user(entry.userId)
    item = {
        "id": f"{int(time.time())}_{uuid.uuid4().hex[:8]}",
        "date": entry.date or datetime.now().isoformat(),
        "amount": entry.amount,
        "category": entry.category,
        "note": entry.note,
        "direction": entry.direction,
        "source": "manual",
    }
    user.setdefault("ledger", []).append(item)
    save_user(user)
    return {"status": "ok", "entry": item}


@router.get("/api/ledger/{user_id}")
def get_ledger(user_id: str):
    """获取用户记账列表"""
    user = load_user(user_id)
    return {"ledger": user.get("ledger", [])}


@router.get("/api/ledger/{user_id}/summary")
def get_ledger_summary(user_id: str, days: int = 30):
    """获取记账统计摘要（区分收入/支出）"""
    user = load_user(user_id)
    ledger = user.get("ledger", [])

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [e for e in ledger if e.get("date", "") >= cutoff]

    expense_by_cat = {}
    income_by_cat = {}
    total_expense = 0
    total_income = 0
    for e in recent:
        cat = e.get("category", "其他")
        amt = e.get("amount", 0)
        direction = e.get("direction", "expense")
        if direction == "income":
            income_by_cat[cat] = income_by_cat.get(cat, 0) + amt
            total_income += amt
        else:
            expense_by_cat[cat] = expense_by_cat.get(cat, 0) + amt
            total_expense += amt

    return {
        "period": f"近{days}天",
        "totalExpense": round(total_expense, 2),
        "totalIncome": round(total_income, 2),
        "netCashFlow": round(total_income - total_expense, 2),
        "count": len(recent),
        "expenseByCategory": expense_by_cat,
        "incomeByCategory": income_by_cat,
        "totalSpent": round(total_expense, 2),
        "byCategory": expense_by_cat,
    }


# ---- 收入源管理 ----

@router.post("/api/income-sources/add")
def add_income_source(src: IncomeSourceCreate):
    """登记新收入源"""
    user = load_user(src.userId)
    sources = user.setdefault("income_sources", [])
    new_src = {
        "id": f"src_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        "name": src.name,
        "type": src.type,
        "expectedAmt": src.expectedAmt,
        "note": src.note,
        "createdAt": datetime.now().isoformat(),
        "lastRecordAt": None,
        "totalRecorded": 0,
        "recordCount": 0,
    }
    sources.append(new_src)
    save_user(user)
    return {"ok": True, "source": new_src}


@router.get("/api/income-sources/{user_id}")
def get_income_sources(user_id: str):
    """获取用户所有收入源"""
    user = load_user(user_id)
    return {"sources": user.get("income_sources", [])}


@router.delete("/api/income-sources/{user_id}/{source_id}")
def delete_income_source(user_id: str, source_id: str):
    """删除收入源"""
    user = load_user(user_id)
    sources = user.get("income_sources", [])
    user["income_sources"] = [s for s in sources if s.get("id") != source_id]
    save_user(user)
    return {"ok": True}


@router.post("/api/income-sources/record")
def record_from_source(req: IncomeSourceRecord):
    """从收入源快速入账"""
    user = load_user(req.userId)
    sources = user.get("income_sources", [])
    src = next((s for s in sources if s.get("id") == req.sourceId), None)
    if not src:
        raise HTTPException(status_code=404, detail="收入源不存在")

    ledger = user.setdefault("ledger", [])
    entry = {
        "id": f"{int(time.time())}_{uuid.uuid4().hex[:8]}",
        "date": datetime.now().isoformat(),
        "amount": req.amount,
        "category": src.get("type", "其他"),
        "note": src.get("name", ""),
        "direction": "income",
        "source": "income_source",
        "sourceId": req.sourceId,
    }
    ledger.append(entry)

    src["lastRecordAt"] = datetime.now().isoformat()
    src["totalRecorded"] = src.get("totalRecorded", 0) + req.amount
    src["recordCount"] = src.get("recordCount", 0) + 1

    save_user(user)
    return {"ok": True, "entry": entry, "source": src}


# ---- 数据健康检查 & 自动审计 ----

@router.get("/api/health/data-audit")
def data_audit():
    """自动审计所有关键数据源的新鲜度和准确性"""
    checks = []
    overall_ok = True

    # 1. 宏观数据新鲜度检查
    try:
        macro = get_macro_calendar()
        macro_dict = {}
        if isinstance(macro, list):
            for item in macro:
                if isinstance(item, dict):
                    macro_dict[item.get("key", "")] = item
        else:
            macro_dict = macro if isinstance(macro, dict) else {}
        for key, label in [("cpi", "CPI"), ("pmi", "PMI"), ("m2", "M2"), ("ppi", "PPI")]:
            item = macro_dict.get(key, {})
            val = item.get("value")
            period = item.get("period", "")
            if val is None or val == "N/A":
                checks.append({"name": f"宏观·{label}", "status": "error", "msg": "数据缺失", "value": None})
                overall_ok = False
            else:
                fresh = True
                if period:
                    try:
                        p = period.replace("年", "-").replace("月", "").strip()
                        data_date = datetime.strptime(p, "%Y-%m")
                        days_old = (datetime.now() - data_date).days
                        if days_old > 90:
                            fresh = False
                    except Exception:
                        pass
                status = "ok" if fresh else "warn"
                if not fresh:
                    overall_ok = False
                checks.append({"name": f"宏观·{label}", "status": status, "msg": f"{period} {val}", "value": val})
    except Exception as e:
        checks.append({"name": "宏观数据", "status": "error", "msg": f"获取失败: {e}", "value": None})
        overall_ok = False

    # 2. 估值数据检查
    try:
        val = get_valuation_percentile()
        pe = val.get("pe_ttm")
        pct = val.get("percentile")
        source = val.get("source", "未知")
        if pe and 5 < pe < 50 and pct is not None:
            checks.append({"name": "估值·PE-TTM", "status": "ok", "msg": f"PE={pe} 百分位={pct}% ({source})", "value": pe})
        else:
            checks.append({"name": "估值·PE-TTM", "status": "warn", "msg": f"数据可能异常: PE={pe} ({source})", "value": pe})
            overall_ok = False
    except Exception as e:
        checks.append({"name": "估值数据", "status": "error", "msg": f"获取失败: {e}", "value": None})
        overall_ok = False

    # 3. 基金净值新鲜度检查
    fund_codes = ["110020", "050025", "217022", "000216", "008114"]
    for code in fund_codes:
        try:
            nav = get_fund_nav(code)
            if nav["nav"] == "N/A":
                checks.append({"name": f"基金净值·{code}", "status": "error", "msg": "获取失败", "value": None})
                overall_ok = False
            else:
                nav_date = nav.get("date", "")
                is_fresh = True
                if nav_date:
                    try:
                        nd = datetime.strptime(nav_date, "%Y-%m-%d")
                        if (datetime.now() - nd).days > 5:
                            is_fresh = False
                    except Exception:
                        pass
                status = "ok" if is_fresh else "warn"
                if not is_fresh:
                    overall_ok = False
                checks.append({"name": f"基金净值·{code}", "status": status, "msg": f"净值={nav['nav']} ({nav_date})", "value": nav["nav"]})
        except Exception as e:
            checks.append({"name": f"基金净值·{code}", "status": "error", "msg": str(e), "value": None})
            overall_ok = False

    # 4. 新闻内容相关性检查
    try:
        news = get_market_news(10)
        foreign_keywords = ["伦敦", "荷兰", "法兰克福", "多伦多", "澳洲", "欧洲央行", "英镑"]
        foreign_count = sum(1 for n in news if any(k in n.get("title", "") for k in foreign_keywords))
        if foreign_count > 3:
            checks.append({"name": "新闻相关性", "status": "warn", "msg": f"10条新闻中{foreign_count}条疑似海外无关内容", "value": foreign_count})
            overall_ok = False
        else:
            checks.append({"name": "新闻相关性", "status": "ok", "msg": f"10条新闻中{foreign_count}条海外内容（正常范围）", "value": foreign_count})
    except Exception as e:
        checks.append({"name": "新闻数据", "status": "error", "msg": f"获取失败: {e}", "value": None})
        overall_ok = False

    # 5. API 响应时间检查
    try:
        t0 = time.time()
        get_fear_greed_index()
        elapsed = round((time.time() - t0) * 1000)
        status = "ok" if elapsed < 5000 else ("warn" if elapsed < 15000 else "error")
        if status != "ok":
            overall_ok = False
        checks.append({"name": "API响应·恐贪指数", "status": status, "msg": f"{elapsed}ms", "value": elapsed})
    except Exception as e:
        checks.append({"name": "API响应·恐贪指数", "status": "error", "msg": str(e), "value": None})
        overall_ok = False

    error_count = sum(1 for c in checks if c["status"] == "error")
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    ok_count = sum(1 for c in checks if c["status"] == "ok")

    return {
        "overall": "healthy" if overall_ok else ("degraded" if error_count == 0 else "unhealthy"),
        "summary": f"✅{ok_count} ⚠️{warn_count} ❌{error_count}",
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    }
