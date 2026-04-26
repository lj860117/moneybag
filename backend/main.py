"""
钱袋子 — FastAPI 后端 V5.0（模块化重构）
========================================
M1 W2 完成：199 路由全部拆到 api/*.py，本文件只保留：
  - FastAPI 实例初始化
  - 中间件（CORS / GZip）
  - 21 个 include_router（P1 × 10 + P2 × 5 + P3 × 6）
  - 企业微信 / Profiles 旧 router 挂载
  - 静态文件服务（前后端一体部署）
"""
import os
import sys
from pathlib import Path

# 确保能导入同级模块
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from config import APP_VERSION as _APP_VERSION
from api.shared_helpers import _cached_file_response

# ---- FastAPI 应用 ----
app = FastAPI(title="钱袋子 API", version=_APP_VERSION)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 旧 Router（企业微信 / 多用户 Profile）----
from routers.wxwork import router as wxwork_router
from routers.profiles import router as profiles_router
app.include_router(wxwork_router)
app.include_router(profiles_router)

# ---- M1 W2: P1 独立路由（10 文件，64 路由）----
from api.factors import router as factors_router
from api.macro import router as macro_router
from api.global_market import router as global_market_router
from api.policy import router as policy_router
from api.market_factors import router as market_factors_router
from api.alt_data import router as alt_data_router
from api.quant import router as quant_router
from api.broker import router as broker_router
from api.analysis import router as analysis_router
from api.scenario import router as scenario_router
app.include_router(factors_router)
app.include_router(macro_router)
app.include_router(global_market_router)
app.include_router(policy_router)
app.include_router(market_factors_router)
app.include_router(alt_data_router)
app.include_router(quant_router)
app.include_router(broker_router)
app.include_router(analysis_router)
app.include_router(scenario_router)

# ---- M1 W2: P2 中等耦合路由（5 文件，78 路由）----
from api.holdings import router as holdings_router
from api.portfolio import router as portfolio_router
from api.signals import router as signals_router
from api.news import router as news_router
from api.user import router as user_router
app.include_router(holdings_router)
app.include_router(portfolio_router)
app.include_router(signals_router)
app.include_router(news_router)
app.include_router(user_router)

# ---- M1 W2: P3 高耦合路由（6 文件，57 路由）----
from api.chat import router as chat_router
from api.dashboard import router as dashboard_router
from api.agent import router as agent_router
from api.steward import router as steward_router
from api.enhance import router as enhance_router
from api.misc import router as misc_router
app.include_router(chat_router)
app.include_router(dashboard_router)
app.include_router(agent_router)
app.include_router(steward_router)
app.include_router(enhance_router)
app.include_router(misc_router)

# ---- 静态文件服务（部署时前后端一体）----
FRONTEND_DIR = Path(__file__).resolve().parent.parent  # moneybag/

@app.get("/")
def serve_index():
    return _cached_file_response(FRONTEND_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

# 兜底：让 /app.js 等直接路径也能访问
@app.get("/{filename:path}")
def serve_frontend_file(filename: str):
    fp = FRONTEND_DIR / filename
    if fp.is_file():
        return _cached_file_response(fp)
    return _cached_file_response(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
