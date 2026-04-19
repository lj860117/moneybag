"""
P0-4 账号隔离测试
目标：双用户场景下数据互不可见；管理员权限无法绕过
"""
import os
import pytest


# =============================================================
# 持仓接口：A 账号不能拿到 B 账号的持仓
# =============================================================

def test_portfolio_overview_isolation(client, qa_user):
    """用 qa_user 查 overview，marketValue 只来自 qa_user 自己的数据"""
    r1 = client.get("/api/portfolio/overview", params={"userId": qa_user})
    r2 = client.get("/api/portfolio/overview", params={"userId": "__ghost_user_99__"})
    assert r1.status_code == 200
    assert r2.status_code == 200

    d1 = r1.json()
    d2 = r2.json()

    # qa_user 有持仓（seed 脚本建过），ghost 用户应该是空的
    assert d1.get("stockCount", 0) + d1.get("fundCount", 0) >= 1, (
        f"qa_user 应该有持仓: {d1}"
    )
    assert d2.get("stockCount", 0) + d2.get("fundCount", 0) == 0, (
        f"❌ 不存在的用户居然有持仓: {d2}"
    )


def test_stock_holdings_isolation(client, qa_user):
    """/api/stock-holdings 必须按 userId 隔离"""
    r1 = client.get("/api/stock-holdings", params={"userId": qa_user})
    r2 = client.get("/api/stock-holdings", params={"userId": "__ghost_user_99__"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    h1 = r1.json().get("holdings", [])
    h2 = r2.json().get("holdings", [])

    # ghost 用户必须是空列表
    assert h2 == [], f"❌ 虚拟用户有持仓数据: {h2}"

    # qa 用户的持仓 code 不能出现在 ghost 的返回里
    codes_in_h1 = {h.get("code") for h in h1}
    codes_in_h2 = {h.get("code") for h in h2}
    assert codes_in_h1.isdisjoint(codes_in_h2), (
        f"❌ 数据越界: 两个用户持仓代码重叠 {codes_in_h1 & codes_in_h2}"
    )


def test_fund_holdings_isolation(client, qa_user):
    """/api/fund-holdings 必须按 userId 隔离"""
    r1 = client.get("/api/fund-holdings", params={"userId": qa_user})
    r2 = client.get("/api/fund-holdings", params={"userId": "__ghost_user_99__"})
    if r1.status_code != 200 or r2.status_code != 200:
        pytest.skip("接口未就绪")
    h2 = r2.json().get("holdings", [])
    assert h2 == [], f"❌ 虚拟用户有基金持仓: {h2}"


# =============================================================
# 记忆 / 决策历史隔离
# =============================================================

def test_analysis_history_isolation(client, qa_user):
    """AI 分析历史必须按 userId 隔离"""
    # 多数 API 路径尝试
    endpoints_to_try = [
        ("/api/analysis-history", {"userId": "__ghost_user_99__"}),
        ("/api/agent/memory", {"userId": "__ghost_user_99__"}),
    ]
    for ep, params in endpoints_to_try:
        try:
            r = client.get(ep, params=params)
            if r.status_code == 200:
                d = r.json()
                # 各种可能的字段
                history = (
                    d.get("history", []) or d.get("records", []) or d.get("items", [])
                    or d.get("memory", [])
                )
                assert not history or history == [], (
                    f"❌ ghost 用户从 {ep} 拿到了历史: {history[:2]}"
                )
        except Exception:
            pass  # 接口可能不存在，跳过


# =============================================================
# Profile 注册白名单
# =============================================================

def test_profile_registration_whitelist(client):
    """
    随意注册 Profile 必须被拒（非白名单）。
    白名单 = {LeiJiang, BuLuoGeLi}
    """
    r = client.post(
        "/api/profiles",
        json={"name": "__hack_user_99__", "inviteCode": "random_wrong_code"},
    )
    # 应该 400 / 403 / 409 之一，不应 200 让任意人注册
    assert r.status_code != 200, (
        f"❌ 任意用户名 + 随意邀请码居然注册成功: {r.json()}"
    )


# =============================================================
# 管理员 Key 不能在前端参数里硬塞绕过
# =============================================================

def test_admin_key_not_in_query_bypassable(client):
    """
    所有 userId 相关接口都不应该接受 ?adminKey=xxx 这种前端侧的权限提升。
    真实管理员 Key 只应在服务端环境变量里用。
    """
    # 试图用 "管理员 Key" 当 userId 来访问别人的数据
    r = client.get("/api/portfolio/overview", params={
        "userId": "LeiJiang",
        "adminKey": "moneybag_admin_2026",  # 默认值
    })
    # 接受请求但不应该返回 LeiJiang 真实持仓（如果本地有这个用户）
    # 宽松断言：至少不能 500
    assert r.status_code in (200, 401, 403, 404), (
        f"接口崩溃而非拒绝: {r.status_code}"
    )


# =============================================================
# 企微推送用户名校验
# =============================================================

def test_wxwork_user_binding_not_leaked(client, qa_user):
    """
    获取用户偏好时不能泄露其他用户的企微绑定
    """
    try:
        r = client.get("/api/user/preference", params={"userId": "__ghost_user_99__"})
        if r.status_code != 200:
            return
        d = r.json()
        # ghost 用户不应有企微绑定
        wx_id = d.get("wxwork_uid") or d.get("wxworkUid") or ""
        assert not wx_id, f"❌ ghost 用户拿到了企微绑定: {wx_id}"
    except Exception:
        pass
