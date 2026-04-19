"""
钱袋子 — 用户 Profile + 邀请码管理 Router
"""
import os
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, HTTPException
from config import DATA_DIR

router = APIRouter(prefix="/api", tags=["profiles"])

# ---- Profile 存储 ----
_PROFILES_FILE = DATA_DIR / "profiles.json"


def _load_profiles() -> list:
    if _PROFILES_FILE.exists():
        try:
            return json.loads(_PROFILES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_profiles(profiles: list):
    _PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROFILES_FILE.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- 邀请码存储 ----
_ADMIN_KEY = os.getenv("ADMIN_KEY", "moneybag_admin_2026")  # 生产环境从 .env 读取
_INVITE_FILE = Path(os.getenv("DATA_DIR", "data")) / "invite_codes.json"

# 白名单（名字 = 企微 userid）
VALID_USERS = {"LeiJiang", "BuLuoGeLi"}


def _load_invite_codes() -> list:
    if _INVITE_FILE.exists():
        return json.loads(_INVITE_FILE.read_text())
    return []


def _save_invite_codes(codes: list):
    _INVITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _INVITE_FILE.write_text(json.dumps(codes, ensure_ascii=False, indent=2))


# ---- Profile CRUD ----

@router.get("/profiles")
def get_profiles():
    """获取所有 Profile 列表"""
    return {"profiles": _load_profiles()}


@router.post("/profiles")
def create_profile(req: dict):
    """创建新 Profile（需要邀请码）"""
    name = req.get("name", "").strip()
    invite_code = req.get("inviteCode", "").strip().upper()
    if not name:
        raise HTTPException(400, "名字不能为空")

    profiles = _load_profiles()
    # 2026-04-19 V7.7: id 直接用 name，废弃 u_xxx md5 哈希
    # 老数据自动兼容：下面 73/76 两行会用 name 兜底找到 legacy 记录
    pid = name

    # 已存在用户直接返回（先按 id 找，再按 name/wxworkUserId 兜底兼容老 u_xxx 数据）
    existing = next((p for p in profiles if p["id"] == pid), None)
    # 也按企微ID或名字匹配（清缓存后用不同名字登录的场景 + 老 u_xxx 用户）
    if not existing:
        existing = next((p for p in profiles if p.get("wxworkUserId") == name or p.get("name") == name), None)
    if existing:
        # 已注册用户直接登录
        return {"ok": True, "profile": existing, "exists": True}

    # 新用户必须有邀请码
    if not invite_code:
        raise HTTPException(403, "请输入邀请码。联系管理员获取。")

    # 白名单验证
    if name not in VALID_USERS:
        raise HTTPException(403, f"名字 '{name}' 不在白名单中。请使用管理员分配的名字。")

    # 验证邀请码
    codes = _load_invite_codes()
    valid_code = next((c for c in codes if c["code"] == invite_code and not c["used"]), None)
    if not valid_code:
        raise HTTPException(403, "邀请码无效或已被使用")

    # 标记码已使用
    valid_code["used"] = True
    valid_code["usedBy"] = name
    valid_code["usedAt"] = datetime.now().isoformat()
    _save_invite_codes(codes)

    # 创建或更新 Profile（名字=企微userid，自动绑定）
    if existing:
        existing["verified"] = True
        existing["wxworkUserId"] = name
        _save_profiles(profiles)
        return {"ok": True, "profile": existing, "exists": True}

    profile = {"id": pid, "name": name, "createdAt": datetime.now().isoformat(), "verified": True, "wxworkUserId": name}
    profiles.append(profile)
    _save_profiles(profiles)
    return {"ok": True, "profile": profile, "exists": False}


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: str, req: dict):
    """更新 Profile（绑定企微 userid 等）"""
    profiles = _load_profiles()
    for p in profiles:
        if p["id"] == profile_id:
            if "wxworkUserId" in req:
                wx_id = req["wxworkUserId"].strip()
                if wx_id and wx_id not in VALID_USERS:
                    raise HTTPException(400, f"企微账号 '{wx_id}' 不在白名单中。请联系管理员添加。当前允许：{', '.join(sorted(VALID_USERS))}")
                p["wxworkUserId"] = wx_id
            if "name" in req:
                p["name"] = req["name"].strip()
            _save_profiles(profiles)
            return {"ok": True, "profile": p}
    raise HTTPException(404, "Profile not found")


# ---- 管理员 API ----

@router.post("/admin/invite-codes")
def generate_invite_codes(req: dict):
    """管理员生成邀请码"""
    if req.get("adminKey") != _ADMIN_KEY:
        raise HTTPException(403, "管理员密钥错误")
    count = min(req.get("count", 1), 10)
    codes = _load_invite_codes()
    new_codes = []
    for _ in range(count):
        code = hashlib.md5(f"{time.time()}{len(codes)}".encode()).hexdigest()[:8].upper()
        codes.append({"code": code, "used": False, "usedBy": None, "createdAt": datetime.now().isoformat()})
        new_codes.append(code)
    _save_invite_codes(codes)
    return {"ok": True, "codes": new_codes}


@router.get("/admin/invite-codes")
def list_invite_codes(adminKey: str = ""):
    """查看所有邀请码"""
    if adminKey != _ADMIN_KEY:
        raise HTTPException(403, "管理员密钥错误")
    return {"codes": _load_invite_codes()}


@router.post("/admin/kick")
def kick_user(req: dict):
    """踢出用户"""
    if req.get("adminKey") != _ADMIN_KEY:
        raise HTTPException(403, "管理员密钥错误")
    profile_id = req.get("profileId", "")
    profiles = _load_profiles()
    before = len(profiles)
    profiles = [p for p in profiles if p["id"] != profile_id]
    if len(profiles) == before:
        raise HTTPException(404, f"Profile {profile_id} not found")
    _save_profiles(profiles)
    return {"ok": True, "msg": f"已踢出 {profile_id}，剩余 {len(profiles)} 人"}
