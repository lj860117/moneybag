"""
钱袋子 — FastAPI 后端 V3
实时净值、盈亏计算、AI 对话分析、数据持久化、OCR 记账、买卖信号
"""
import os
import json
import time
import uuid
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="钱袋子 API", version="3.0.0")

# CORS - 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 持久化目录 ----
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR = DATA_DIR / "users"
USERS_DIR.mkdir(exist_ok=True)
RECEIPTS_DIR = DATA_DIR / "receipts"
RECEIPTS_DIR.mkdir(exist_ok=True)

# ---- 缓存 ----
nav_cache = {}
NAV_CACHE_TTL = 3600  # 1小时缓存

# ---- 数据模型 ----
class Holding(BaseModel):
    code: str
    name: str
    category: str
    targetPct: float
    amount: float
    buyDate: str

class Portfolio(BaseModel):
    holdings: list[Holding] = []
    history: list = []
    profile: Optional[str] = None
    amount: float = 0

class UserData(BaseModel):
    userId: str
    portfolio: Optional[Portfolio] = None
    ledger: list = []  # 记账条目
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

class ChatRequest(BaseModel):
    message: str
    portfolio: Optional[Portfolio] = None

class LedgerEntry(BaseModel):
    userId: str
    category: str
    amount: float
    note: str = ""
    date: Optional[str] = None

# ---- AKShare 数据拉取 ----
def get_fund_nav(code: str) -> dict:
    """获取单只基金的最新净值"""
    cache_key = code
    now = time.time()

    if cache_key in nav_cache and now - nav_cache[cache_key]["ts"] < NAV_CACHE_TTL:
        return nav_cache[cache_key]["data"]

    try:
        import akshare as ak
        # 开放式基金净值
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
            nav_val = float(latest["单位净值"])
            prev_val = float(prev["单位净值"])
            change = round((nav_val - prev_val) / prev_val * 100, 2)
            result = {
                "code": code,
                "nav": str(nav_val),
                "date": str(latest["净值日期"]),
                "change": str(change),
            }
            nav_cache[cache_key] = {"data": result, "ts": now}
            return result
    except Exception as e:
        print(f"[NAV] Failed to fetch {code}: {e}")

    # 降级：返回空
    return {"code": code, "nav": "N/A", "date": "N/A", "change": "0"}


def get_fear_greed_index() -> float:
    """简易恐惧贪婪指数（基于沪深300近期涨跌幅）"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is not None and len(df) >= 20:
            recent = df.tail(20)
            close_prices = recent["close"].values
            change_20d = (close_prices[-1] - close_prices[0]) / close_prices[0] * 100
            # 简单映射：-20% → 恐惧(90)，+20% → 贪婪(90)
            if change_20d < -15:
                return 85  # 极度恐惧
            elif change_20d < -8:
                return 70  # 恐惧
            elif change_20d < 0:
                return 50  # 中性偏恐惧
            elif change_20d < 8:
                return 40  # 中性偏贪婪
            elif change_20d < 15:
                return 25  # 贪婪
            else:
                return 10  # 极度贪婪
    except Exception as e:
        print(f"[FGI] Failed: {e}")
    return 50  # 默认中性


# ---- API 路由 ----

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.get("/api/nav/{code}")
def get_nav(code: str):
    """获取单只基金净值"""
    return get_fund_nav(code)


@app.get("/api/nav/all")
def get_all_nav():
    """获取所有推荐基金的净值"""
    codes = ["110020", "050025", "217022", "000216", "070018"]
    result = {}
    for code in codes:
        result[code] = get_fund_nav(code)
    return result


@app.post("/api/signals")
def get_signals(portfolio: Portfolio):
    """根据持仓生成买卖信号"""
    signals = []

    if not portfolio.holdings:
        return signals

    total_amount = sum(h.amount for h in portfolio.holdings)
    if total_amount <= 0:
        return signals

    # 1. 再平衡信号：检查各资产偏离度
    for h in portfolio.holdings:
        current_pct = h.amount / total_amount * 100
        deviation = abs(current_pct - h.targetPct)
        if deviation > 5:
            direction = "偏多" if current_pct > h.targetPct else "偏少"
            signals.append({
                "icon": "⚖️",
                "title": f"{h.category}需要再平衡",
                "message": f"当前占比 {current_pct:.1f}%，目标 {h.targetPct}%，{direction} {deviation:.1f}%。建议调整。",
                "type": "rebalance",
                "severity": "warning",
            })

    # 2. 恐惧贪婪信号
    fgi = get_fear_greed_index()
    if fgi >= 75:
        signals.append({
            "icon": "😱",
            "title": "市场极度恐惧 — 可能是加仓机会",
            "message": f"恐惧指数 {fgi:.0f}/100。历史上极度恐惧时买入，长期收益概率较高。考虑用货币基金的弹药适当加仓。",
            "type": "fear",
            "severity": "opportunity",
        })
    elif fgi <= 25:
        signals.append({
            "icon": "🤑",
            "title": "市场过度贪婪 — 注意风险",
            "message": f"贪婪指数 {100 - fgi:.0f}/100。市场可能过热，建议不要追高，保持定投节奏即可。",
            "type": "greed",
            "severity": "warning",
        })

    # 3. 定投提醒
    now = datetime.now()
    if now.day <= 5:
        monthly_invest = total_amount * 0.1  # 假设月定投为总额的10%
        signals.append({
            "icon": "📅",
            "title": "本月定投提醒",
            "message": f"每月初是定投好时机。按你的配比，本月建议定投 ¥{monthly_invest:,.0f}。",
            "type": "dca",
            "severity": "info",
        })

    # 4. 持仓时间检查
    if portfolio.holdings and portfolio.holdings[0].buyDate:
        try:
            buy_date = datetime.fromisoformat(portfolio.holdings[0].buyDate.replace("Z", "+00:00"))
            days_held = (datetime.now(buy_date.tzinfo) - buy_date).days
            if days_held < 30:
                signals.append({
                    "icon": "⏰",
                    "title": "耐心持有",
                    "message": f"你才持有 {days_held} 天，投资是长跑。至少 3 年才能看到复利效果，别被短期波动影响心态。",
                    "type": "patience",
                    "severity": "info",
                })
        except Exception:
            pass

    return signals


# ---- 持久化工具 ----
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"

def load_user(user_id: str) -> dict:
    f = _user_file(user_id)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"userId": user_id, "portfolio": None, "ledger": [], "createdAt": datetime.now().isoformat(), "updatedAt": datetime.now().isoformat()}

def save_user(data: dict):
    data["updatedAt"] = datetime.now().isoformat()
    f = _user_file(data["userId"])
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- API: 盈亏计算 ----

@app.post("/api/portfolio/pnl")
def calc_portfolio_pnl(portfolio: Portfolio):
    """计算持仓的实时盈亏"""
    if not portfolio.holdings:
        return {"totalCost": 0, "totalMarket": 0, "totalPnl": 0, "totalPnlPct": 0, "holdings": []}

    results = []
    total_cost = 0
    total_market = 0

    for h in portfolio.holdings:
        cost = h.amount
        total_cost += cost

        # 获取最新净值
        nav_info = get_fund_nav(h.code) if h.code != "余额宝" else None
        if nav_info and nav_info["nav"] != "N/A":
            current_nav = float(nav_info["nav"])
            nav_date = nav_info["date"]
            change_pct = float(nav_info.get("change", "0"))
        else:
            # 余额宝或无数据：假设年化 1.8% 按日计算
            if h.buyDate:
                try:
                    buy_dt = datetime.fromisoformat(h.buyDate.replace("Z", "+00:00"))
                    days = max((datetime.now(buy_dt.tzinfo) - buy_dt).days, 0)
                except Exception:
                    days = 0
            else:
                days = 0
            daily_rate = 0.018 / 365
            current_nav = None
            nav_date = None
            change_pct = 0
            market_val = cost * (1 + daily_rate * days)
            results.append({
                "code": h.code,
                "name": h.name,
                "category": h.category,
                "cost": round(cost, 2),
                "marketValue": round(market_val, 2),
                "pnl": round(market_val - cost, 2),
                "pnlPct": round((market_val - cost) / cost * 100, 2) if cost > 0 else 0,
                "nav": "余额宝",
                "navDate": datetime.now().strftime("%Y-%m-%d"),
                "dayChange": 0,
            })
            total_market += market_val
            continue

        # 用净值变化估算市值（简化：假设买入时净值为基准，当前净值反映涨跌）
        # 更精确做法：记录买入净值。当前 MVP 用买入日期到现在的累计涨幅估算
        # 这里先用 AKShare 拉买入日到最新的净值变化
        buy_nav = _get_nav_on_date(h.code, h.buyDate)
        if buy_nav and buy_nav > 0:
            growth = (current_nav - buy_nav) / buy_nav
            market_val = cost * (1 + growth)
        else:
            market_val = cost  # 无法计算则保持原值

        pnl = market_val - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0
        total_market += market_val

        results.append({
            "code": h.code,
            "name": h.name,
            "category": h.category,
            "cost": round(cost, 2),
            "marketValue": round(market_val, 2),
            "pnl": round(pnl, 2),
            "pnlPct": round(pnl_pct, 2),
            "nav": str(current_nav),
            "navDate": nav_date,
            "dayChange": change_pct,
        })

    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    return {
        "totalCost": round(total_cost, 2),
        "totalMarket": round(total_market, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl_pct, 2),
        "holdings": results,
    }


def _get_nav_on_date(code: str, date_str: str) -> Optional[float]:
    """获取基金在指定日期的净值"""
    cache_key = f"hist_{code}"
    now = time.time()

    # 使用缓存的历史数据
    if cache_key in nav_cache and now - nav_cache[cache_key]["ts"] < NAV_CACHE_TTL:
        df = nav_cache[cache_key]["data"]
    else:
        try:
            import akshare as ak
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is not None and len(df) > 0:
                nav_cache[cache_key] = {"data": df, "ts": now}
            else:
                return None
        except Exception as e:
            print(f"[HIST_NAV] Failed for {code}: {e}")
            return None

    try:
        target = datetime.fromisoformat(date_str.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        # 找最接近买入日期的净值
        df["date_str"] = df["净值日期"].astype(str)
        match = df[df["date_str"] >= target].head(1)
        if len(match) > 0:
            return float(match.iloc[0]["单位净值"])
        # 如果买入日期比所有数据都新，取最新
        return float(df.iloc[-1]["单位净值"])
    except Exception as e:
        print(f"[HIST_NAV] Parse error: {e}")
        return None


# ---- API: AI 对话分析 ----

@app.post("/api/chat")
async def chat_analysis(req: ChatRequest):
    """AI 对话分析 — 回答用户的理财问题"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    # 构建市场上下文
    market_ctx = _build_market_context()
    portfolio_ctx = _build_portfolio_context(req.portfolio) if req.portfolio else "用户尚未建仓。"

    # 尝试调用 LLM（支持 OpenAI 兼容 API）
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    if api_key:
        try:
            import httpx
            system_prompt = f"""你是「钱袋子」的 AI 理财分析师。你的职责：
1. 用通俗易懂的中文回答用户的理财问题
2. 基于真实市场数据给出分析（不编造数据）
3. 永远提醒用户"投资有风险"
4. 不推荐具体买卖时点，只分析趋势和逻辑
5. 回答控制在 200 字以内，简洁有力

当前市场数据：
{market_ctx}

用户持仓：
{portfolio_ctx}"""

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg},
                        ],
                        "max_tokens": 500,
                        "temperature": 0.7,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    reply = data["choices"][0]["message"]["content"]
                    return {"reply": reply, "source": "ai"}
        except Exception as e:
            print(f"[CHAT] LLM call failed: {e}")

    # 降级：规则引擎回答
    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
    return {"reply": reply, "source": "rules"}


def _build_market_context() -> str:
    """构建市场数据上下文"""
    lines = []
    try:
        fgi = get_fear_greed_index()
        if fgi >= 75:
            lines.append(f"恐惧贪婪指数：{fgi:.0f}（极度恐惧）")
        elif fgi >= 60:
            lines.append(f"恐惧贪婪指数：{fgi:.0f}（恐惧）")
        elif fgi <= 25:
            lines.append(f"恐惧贪婪指数：{100-fgi:.0f}（极度贪婪）")
        elif fgi <= 40:
            lines.append(f"恐惧贪婪指数：{100-fgi:.0f}（贪婪）")
        else:
            lines.append(f"恐惧贪婪指数：{fgi:.0f}（中性）")
    except Exception:
        lines.append("恐惧贪婪指数：暂无数据")

    codes = {"110020": "沪深300", "050025": "标普500", "000216": "黄金"}
    for code, name in codes.items():
        nav = get_fund_nav(code)
        if nav["nav"] != "N/A":
            lines.append(f"{name}({code})：净值 {nav['nav']}，日涨跌 {nav['change']}%")
    return "\n".join(lines) if lines else "暂无市场数据"


def _build_portfolio_context(p: Portfolio) -> str:
    if not p or not p.holdings:
        return "用户尚未建仓。"
    lines = [f"风险类型：{p.profile}，总投入：¥{p.amount:,.0f}"]
    for h in p.holdings:
        lines.append(f"  - {h.name}({h.code})：¥{h.amount:,.0f}，目标占比 {h.targetPct}%")
    return "\n".join(lines)


def _rule_based_reply(msg: str, market_ctx: str, portfolio_ctx: str) -> str:
    """规则引擎降级回答"""
    msg_lower = msg.lower()

    if any(k in msg_lower for k in ["跌", "亏", "赔", "绿", "下跌"]):
        return f"📉 市场波动是正常现象。\n\n{market_ctx}\n\n长期投资（3年+）能大幅平滑短期波动。如果你的资产配比还在目标范围内，建议保持定投节奏，不要恐慌卖出。记住投资铁律：跌了别卖，越跌越该买。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["涨", "赚", "红", "上涨", "牛"]):
        return f"📈 恭喜！不过也别过于乐观。\n\n{market_ctx}\n\n赚钱时更要冷静，检查一下各资产的占比是否偏离目标太多。如果某类资产涨太多导致占比过高，可以考虑再平衡——卖掉一点涨多的，买入涨少的。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["买", "加仓", "定投", "什么时候"]):
        return f"💰 关于买入时机：\n\n{market_ctx}\n\n定投的精髓就是「不择时」——每月固定日期买入，无论涨跌。这样长期下来会自动实现低买多、高买少。如果恐惧指数很高（市场极度悲观），可以适当多买一点。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["卖", "减仓", "止盈", "止损"]):
        return f"🔔 关于卖出：\n\n建议至少持有 3 年再考虑大的调整。日常操作以再平衡为主——某类资产占比偏离目标 ±5% 时，微调回目标配比即可。不建议因为短期涨跌做大的买卖决策。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["黄金", "gold"]):
        nav = get_fund_nav("000216")
        return f"🪙 黄金近况：净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n黄金是经典避险资产，近年全球央行持续增持。在你的配置中作为分散风险的角色，建议保持目标占比即可，不用频繁操作。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    return f"🤔 关于你的问题：\n\n当前市场概况：\n{market_ctx}\n\n{portfolio_ctx}\n\n如果你有更具体的问题（比如「今天为什么跌了」「该不该加仓」「黄金怎么看」），可以直接问我。\n\n⚠️ 以上仅供参考，不构成投资建议。"


# ---- API: 数据持久化 ----

@app.post("/api/user/save")
def save_user_data(data: UserData):
    """保存用户数据到服务端"""
    user = load_user(data.userId)
    if data.portfolio:
        user["portfolio"] = data.portfolio.dict()
    if data.ledger:
        user["ledger"] = data.ledger
    if not user.get("createdAt"):
        user["createdAt"] = datetime.now().isoformat()
    save_user(user)
    return {"status": "ok", "userId": data.userId}

@app.get("/api/user/{user_id}")
def get_user_data(user_id: str):
    """读取用户数据"""
    user = load_user(user_id)
    return user

@app.delete("/api/user/{user_id}")
def delete_user_data(user_id: str):
    """删除用户数据"""
    f = _user_file(user_id)
    if f.exists():
        f.unlink()
    return {"status": "ok"}


# ---- API: OCR 记账 ----

@app.post("/api/receipt/ocr")
async def ocr_receipt(file: UploadFile = File(...), userId: str = Form("")):
    """拍照识别小票 → 自动提取金额和商品"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "请上传图片文件")

    # 保存图片
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")

    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    receipt_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    receipt_path = RECEIPTS_DIR / f"{receipt_id}.{ext}"
    receipt_path.write_bytes(content)

    # 尝试 OCR
    ocr_result = await _do_ocr(receipt_path, content)

    # 如果有用户 ID，自动入账
    if userId and ocr_result.get("amount"):
        user = load_user(userId)
        entry = {
            "id": receipt_id,
            "date": datetime.now().isoformat(),
            "amount": ocr_result["amount"],
            "category": ocr_result.get("category", "其他"),
            "note": ocr_result.get("note", ""),
            "source": "ocr",
            "receiptFile": str(receipt_path.name),
        }
        user.setdefault("ledger", []).append(entry)
        save_user(user)
        ocr_result["saved"] = True
        ocr_result["entryId"] = receipt_id

    return ocr_result


async def _do_ocr(file_path: Path, content: bytes) -> dict:
    """执行 OCR，优先用 LLM 多模态，降级用本地 OCR"""

    # 方案1：用 LLM 多模态识别（最准）
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
    vision_model = os.environ.get("LLM_VISION_MODEL", "gpt-4o-mini")

    if api_key:
        try:
            import base64
            import httpx
            b64 = base64.b64encode(content).decode()
            mime = "image/jpeg"
            if str(file_path).endswith(".png"):
                mime = "image/png"

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": vision_model,
                        "messages": [
                            {"role": "system", "content": "你是一个小票/发票 OCR 识别助手。请从图片中提取：总金额(amount)、商家名(merchant)、消费类别(category:餐饮/交通/购物/娱乐/医疗/教育/其他)、简要备注(note)。返回 JSON 格式。"},
                            {"role": "user", "content": [
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                                {"type": "text", "text": "请识别这张小票/发票的信息，返回 JSON。"},
                            ]},
                        ],
                        "max_tokens": 300,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    # 提取 JSON
                    import re
                    json_match = re.search(r'\{[^}]+\}', text, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group())
                        return {
                            "amount": float(parsed.get("amount", 0)),
                            "merchant": parsed.get("merchant", ""),
                            "category": parsed.get("category", "其他"),
                            "note": parsed.get("note", ""),
                            "source": "llm_vision",
                            "raw": text,
                        }
        except Exception as e:
            print(f"[OCR] LLM vision failed: {e}")

    # 方案2：本地 OCR（pytesseract）
    try:
        from PIL import Image
        import pytesseract
        import re

        img = Image.open(file_path)
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")

        # 简易提取金额
        amounts = re.findall(r'[\d]+\.[\d]{2}', text)
        amount = max([float(a) for a in amounts]) if amounts else 0

        return {
            "amount": amount,
            "merchant": "",
            "category": "其他",
            "note": text[:100],
            "source": "tesseract",
            "raw": text[:500],
        }
    except Exception as e:
        print(f"[OCR] Tesseract failed: {e}")

    return {
        "amount": 0,
        "merchant": "",
        "category": "其他",
        "note": "OCR 识别失败，请手动输入",
        "source": "none",
        "raw": "",
    }


@app.post("/api/ledger/add")
def add_ledger_entry(entry: LedgerEntry):
    """手动添加记账条目"""
    user = load_user(entry.userId)
    item = {
        "id": f"{int(time.time())}_{uuid.uuid4().hex[:8]}",
        "date": entry.date or datetime.now().isoformat(),
        "amount": entry.amount,
        "category": entry.category,
        "note": entry.note,
        "source": "manual",
    }
    user.setdefault("ledger", []).append(item)
    save_user(user)
    return {"status": "ok", "entry": item}

@app.get("/api/ledger/{user_id}")
def get_ledger(user_id: str):
    """获取用户记账列表"""
    user = load_user(user_id)
    return {"ledger": user.get("ledger", [])}

@app.get("/api/ledger/{user_id}/summary")
def get_ledger_summary(user_id: str, days: int = 30):
    """获取记账统计摘要"""
    user = load_user(user_id)
    ledger = user.get("ledger", [])

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = [e for e in ledger if e.get("date", "") >= cutoff]

    # 按分类汇总
    by_category = {}
    total = 0
    for e in recent:
        cat = e.get("category", "其他")
        amt = e.get("amount", 0)
        by_category[cat] = by_category.get(cat, 0) + amt
        total += amt

    return {
        "period": f"近{days}天",
        "totalSpent": round(total, 2),
        "count": len(recent),
        "byCategory": by_category,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
