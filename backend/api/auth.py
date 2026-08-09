"""
钱袋子 v9.5.123 — 鉴权接口
============================
POST /api/auth/login  — 用 userId + password 换取 token
GET  /api/auth/verify — 验证当前 token 是否有效
"""
import os
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from infra.auth import generate_token
from config import AUTH_ENABLED

router = APIRouter(prefix="/api/auth", tags=["鉴权"])

# 用户密码存储：data/auth_users.json
# 格式: {"LeiJiang": "password_hash", "BuLuoGeLi": "password_hash"}
_AUTH_FILE = Path(os.environ.get("DATA_DIR", "data")) / "auth_users.json"


def _load_users() -> dict:
    """加载用户密码表"""
    if _AUTH_FILE.exists():
        try:
            return json.loads(_AUTH_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 默认用户（首次部署时自动创建）
    return {}


def _save_users(users: dict):
    """保存用户密码表"""
    _AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _AUTH_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


class LoginRequest(BaseModel):
    userId: str
    password: str


class LoginResponse(BaseModel):
    token: str
    userId: str
    message: str


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    """用户登录：验证密码后返回 token
    
    首次登录（用户不在密码表中）= 自动注册。
    家庭自用工具，不需要复杂注册流程。
    """
    if not AUTH_ENABLED:
        # 鉴权关闭时直接返回 token
        return LoginResponse(
            token=generate_token(req.userId),
            userId=req.userId,
            message="鉴权已关闭，token 仅供格式兼容",
        )
    
    users = _load_users()
    
    # 简单密码哈希（家用级，不需要 bcrypt）
    import hashlib
    pw_hash = hashlib.sha256(req.password.encode("utf-8")).hexdigest()
    
    if req.userId in users:
        # 已注册用户，验证密码
        if users[req.userId] != pw_hash:
            raise HTTPException(status_code=403, detail="密码错误")
    else:
        # 新用户自动注册
        users[req.userId] = pw_hash
        _save_users(users)
    
    token = generate_token(req.userId)
    return LoginResponse(token=token, userId=req.userId, message="登录成功")


@router.get("/verify")
def verify_token(userId: str = "", token: str = ""):
    """验证 token 有效性"""
    if not AUTH_ENABLED:
        return {"valid": True, "message": "鉴权已关闭"}
    
    if not userId or not token:
        return {"valid": False, "message": "缺少参数"}
    
    expected = generate_token(userId)
    import hmac as _hmac
    valid = _hmac.compare_digest(token, expected)
    return {"valid": valid, "message": "有效" if valid else "token无效或已过期"}


@router.get("/status")
def auth_status():
    """返回鉴权系统状态（前端用于决定是否显示登录框）"""
    return {"auth_enabled": AUTH_ENABLED}
