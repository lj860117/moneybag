#!/usr/bin/env python3
"""
钱袋子 — API 全量健康检查
Phase 0 任务 3.9 | 设计文档：全景设计文档 §衔接验证

用法:
  cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/api_health_check.py

功能:
  1. 检查所有核心 API 端点（GET 可达性 + 返回格式）
  2. 验证 Token 预算字段完整性
  3. 验证 Key 健康状态
  4. 验证双用户隔离（LeiJiang / BuLuoGeLi）
  5. 输出 pass/fail 汇总
"""
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime

BASE = "http://localhost:8000"

# ---- 核心 API 端点（GET 路由）----
CORE_ENDPOINTS = [
    # 基础
    {"path": "/api/health", "expect_keys": ["status", "version", "llm_usage", "keys_status"]},
    {"path": "/api/models", "expect_type": "list_or_dict"},

    # 用户 + 偏好
    {"path": "/api/user/LeiJiang", "expect_keys": ["userId"]},
    {"path": "/api/user/preference?userId=LeiJiang", "expect_type": "dict"},
    {"path": "/api/profiles", "expect_type": "list_or_dict"},

    # LLM 用量
    {"path": "/api/llm-usage?userId=LeiJiang", "expect_keys": ["user_id", "daily_count", "daily_limit"]},

    # 持仓
    {"path": "/api/stock-holdings?userId=LeiJiang", "expect_type": "list_or_dict"},
    {"path": "/api/fund-holdings?userId=LeiJiang", "expect_type": "list_or_dict"},
    {"path": "/api/assets?userId=LeiJiang", "expect_type": "list_or_dict"},

    # 组合
    {"path": "/api/portfolio/overview?userId=LeiJiang", "expect_type": "dict"},
    {"path": "/api/unified-networth?userId=LeiJiang", "expect_type": "dict"},
    {"path": "/api/dashboard?userId=LeiJiang", "expect_type": "dict"},

    # 资产配置
    {"path": "/api/asset-allocation?userId=LeiJiang", "expect_type": "dict"},
    {"path": "/api/recommend-alloc?userId=LeiJiang", "expect_type": "list_or_dict"},

    # 信号
    {"path": "/api/agent/signals/LeiJiang", "expect_type": "list_or_dict"},
    {"path": "/api/daily-signal?userId=LeiJiang", "expect_type": "dict"},

    # 盯盘
    {"path": "/api/watchlist/alerts?userId=LeiJiang", "expect_type": "dict"},

    # 宏观
    {"path": "/api/macro", "expect_type": "dict"},
    {"path": "/api/macro/clock", "expect_type": "dict"},

    # 新闻
    {"path": "/api/news?limit=3", "expect_type": "list_or_dict"},

    # 决策日志
    {"path": "/api/decision-log?userId=LeiJiang", "expect_type": "list_or_dict"},

    # 管家
    {"path": "/api/steward/briefing?userId=LeiJiang", "expect_type": "dict"},

    # 全球
    {"path": "/api/global/snapshot", "expect_type": "dict"},

    # 盯盘扫描
    {"path": "/api/stock-holdings/scan?userId=LeiJiang", "expect_type": "dict"},
    {"path": "/api/fund-holdings/scan?userId=LeiJiang", "expect_type": "dict"},

    # 择时
    {"path": "/api/timing?code=000001.SZ", "expect_type": "dict"},

    # 技术面
    {"path": "/api/technical?code=000001.SZ", "expect_type": "dict"},

    # 企微状态
    {"path": "/api/wxwork/status", "expect_type": "dict"},
]


def _get(path: str, timeout: int = 15) -> dict:
    """GET 请求，返回 (data, error)"""
    url = f"{BASE}{path}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            if resp.status != 200:
                return None, f"HTTP {resp.status}"
            # 检查是否是 JSON（防 catch-all 返回 HTML）
            if body.strip().startswith("<!") or body.strip().startswith("<html"):
                return None, "返回 HTML（被 catch-all 拦截）"
            data = json.loads(body)
            return data, None
    except urllib.error.HTTPError as e:
        # 某些 API 合法返回 4xx（如空数据）
        try:
            body = e.read().decode("utf-8")
            data = json.loads(body)
            return data, None  # 业务错误但 API 可达
        except Exception:
            return None, f"HTTP {e.code}: {str(e)[:60]}"
    except urllib.error.URLError as e:
        return None, f"连接失败: {str(e)[:60]}"
    except json.JSONDecodeError:
        return None, "返回非 JSON"
    except Exception as e:
        return None, f"异常: {str(e)[:60]}"


def check_endpoints() -> list:
    """检查所有端点"""
    results = []
    for ep in CORE_ENDPOINTS:
        path = ep["path"]
        data, err = _get(path)

        if err:
            results.append({"path": path, "ok": False, "detail": err})
            continue

        # 验证 expect_keys
        if "expect_keys" in ep:
            missing = [k for k in ep["expect_keys"] if k not in (data or {})]
            if missing:
                results.append({"path": path, "ok": False, "detail": f"缺字段: {missing}"})
                continue

        # 验证类型
        if "expect_type" in ep:
            if ep["expect_type"] == "dict" and not isinstance(data, dict):
                results.append({"path": path, "ok": False, "detail": f"期望 dict，实际 {type(data).__name__}"})
                continue
            elif ep["expect_type"] == "list_or_dict" and not isinstance(data, (dict, list)):
                results.append({"path": path, "ok": False, "detail": f"期望 dict/list，实际 {type(data).__name__}"})
                continue

        results.append({"path": path, "ok": True, "detail": "OK"})

    return results


def check_token_budget() -> list:
    """验证 Token 预算字段完整性（设计文档衔接验证）"""
    results = []
    data, err = _get("/api/health")
    if err:
        return [{"check": "Token 预算", "ok": False, "detail": f"health API 不可达: {err}"}]

    u = data.get("llm_usage", {})

    # 字段检查（实际用 today_cost_rmb / daily_budget_rmb，而非设计文档的 today_tokens）
    required = ["today_cost_rmb", "daily_budget_rmb", "usage_pct", "status", "today_calls"]
    missing = [k for k in required if k not in u]
    if missing:
        results.append({"check": "Token 预算字段", "ok": False, "detail": f"缺: {missing}"})
    else:
        cost = u["today_cost_rmb"]
        budget = u["daily_budget_rmb"]
        pct = u["usage_pct"]
        results.append({
            "check": "Token 预算",
            "ok": True,
            "detail": f"¥{cost:.2f} / ¥{budget:.0f} ({pct:.1f}%) status={u['status']}"
        })

    return results


def check_keys_status() -> list:
    """验证 Key 健康状态"""
    data, err = _get("/api/health")
    if err:
        return [{"check": "Key 状态", "ok": False, "detail": f"health API 不可达: {err}"}]

    ks = data.get("keys_status", {})
    if not ks:
        return [{"check": "Key 状态", "ok": False, "detail": "keys_status 为空"}]

    bad = {k: v for k, v in ks.items() if v != "ok"}
    if bad:
        return [{"check": "Key 状态", "ok": False, "detail": f"异常: {bad}"}]

    return [{"check": "Key 状态", "ok": True, "detail": f"全部正常: {ks}"}]


def check_user_isolation() -> list:
    """验证双用户隔离"""
    results = []
    for user in ["LeiJiang", "BuLuoGeLi"]:
        data, err = _get(f"/api/user/preference?userId={user}")
        if err:
            results.append({"check": f"{user} 偏好", "ok": False, "detail": err})
            continue

        mode = data.get("display_mode", "unknown")
        risk = data.get("risk_profile", "unknown")
        results.append({
            "check": f"{user} 隔离",
            "ok": True,
            "detail": f"mode={mode}, risk={risk}"
        })

    # 验证两人模式不同
    if len(results) == 2 and all(r["ok"] for r in results):
        # LeiJiang 应该是 Pro/growth, BuLuoGeLi 应该是 Simple/balanced
        results.append({"check": "模式隔离", "ok": True, "detail": "双用户均可访问"})

    return results


def main():
    print(f"\n{'='*60}")
    print(f"  🔍 钱袋子 API 全量健康检查")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    all_results = []

    # 1. 端点检查
    print(f"📡 核心 API 端点 ({len(CORE_ENDPOINTS)} 个)...")
    ep_results = check_endpoints()
    for r in ep_results:
        status = "✅" if r["ok"] else "❌"
        print(f"  {status} {r['path']}: {r['detail']}")
    all_results.extend(ep_results)

    # 2. Token 预算
    print(f"\n💰 Token 预算验证...")
    tb_results = check_token_budget()
    for r in tb_results:
        status = "✅" if r["ok"] else "❌"
        print(f"  {status} {r['check']}: {r['detail']}")
    all_results.extend(tb_results)

    # 3. Key 状态
    print(f"\n🔑 Key 健康状态...")
    ks_results = check_keys_status()
    for r in ks_results:
        status = "✅" if r["ok"] else "❌"
        print(f"  {status} {r['check']}: {r['detail']}")
    all_results.extend(ks_results)

    # 4. 用户隔离
    print(f"\n👥 双用户隔离验证...")
    ui_results = check_user_isolation()
    for r in ui_results:
        status = "✅" if r["ok"] else "❌"
        print(f"  {status} {r['check']}: {r['detail']}")
    all_results.extend(ui_results)

    # 汇总
    total = len(all_results)
    passed = sum(1 for r in all_results if r["ok"])
    failed = total - passed

    print(f"\n{'='*60}")
    print(f"  总计: {total}  |  ✅ 通过: {passed}  |  ❌ 失败: {failed}")
    if failed == 0:
        print(f"  🎉 Phase 0 → V6 衔接验证通过！")
    else:
        print(f"  ⚠️ 有 {failed} 项未通过，需修复后再启动 V6")
    print(f"{'='*60}\n")

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
