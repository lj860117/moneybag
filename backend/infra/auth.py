"""
钱袋子 v9.5.123 — 简单API鉴权
================================
方案：HMAC-SHA256 token
- 每个用户的 token = HMAC-SHA256(AUTH_SECRET, userId)
- 前端 localStorage 存 token，每次请求带 Authorization: Bearer {token}
- 公开接口（健康检查/静态文件）不需要验证
- 可通过 AUTH_ENABLED=false 环境变量关闭（开发模式）

安全等级：家用级（防止随意猜测userId访问他人数据）
"""
import hmac
import hashlib
from fastapi import Request, HTTPException
from config import AUTH_SECRET, AUTH_ENABLED


def generate_token(user_id: str) -> str:
    """为用户生成HMAC token"""
    return hmac.HMAC(
        AUTH_SECRET.encode("utf-8"),
        user_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]  # 取前32字符足够


def verify_request(request: Request, user_id: str = "") -> bool:
    """验证请求的token是否匹配userId
    
    验证逻辑：
    1. AUTH_ENABLED=false → 跳过验证（开发模式）
    2. 从 Authorization header 或 query param `token` 提取 token
    3. 对比 HMAC(SECRET, userId) == provided_token
    """
    if not AUTH_ENABLED:
        return True
    
    # 提取 token
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    else:
        # fallback: query param
        token = request.query_params.get("token", "")
    
    if not token:
        return False
    
    # 确定 userId（如果传入为空，尝试从请求中获取）
    if not user_id:
        user_id = request.query_params.get("userId", "default")
    
    expected = generate_token(user_id)
    return hmac.compare_digest(token, expected)


def require_auth(request: Request, user_id: str = ""):
    """验证鉴权，失败时抛 401"""
    if not AUTH_ENABLED:
        return
    if not verify_request(request, user_id):
        raise HTTPException(status_code=401, detail="未授权访问，请重新登录")
