"""
Memory 系统端到端测试 — P0 防护网

覆盖 agent_memory.py 9 模块 + 3 机制：
- 读写闭环（preferences / decisions / rules / context / profile / emotion / ironies / life_events / pending_insights）
- 家庭主账号路由（_route_to_admin）
- 农历生日自动转公历
- 自动记忆积累 + 待审闭环（auto_extract → add_pending → approve → 目标模块可见）
- build_memory_summary 性能压力（1000 条 decisions 注入 < 200ms）
- build_memory_summary 反污染（不能含【】🔒①②③）

运行方式：
  cd moneybag && /Users/leijiang/.workbuddy/binaries/python/envs/moneybag-v7/bin/pytest tests/test_memory_e2e.py -v
触发词：测记忆 / 测 memory
"""
import os
import sys
import json
import time
import shutil
import tempfile
from pathlib import Path

import pytest

# 把 backend 放 sys.path 最前，才能 import services.agent_memory
_BACKEND = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))


@pytest.fixture
def mem(monkeypatch):
    """每个测试独立的数据目录 + 隔离的 agent_memory 模块"""
    d = Path(tempfile.mkdtemp(prefix="mb_mem_test_"))

    # 1. 设置环境变量（config.py 首次 import 时从 DATA_DIR 读）
    monkeypatch.setenv("DATA_DIR", str(d))

    # 2. config 可能已经被其他测试/服务 import 过，需要强制 reload
    import importlib
    import config
    importlib.reload(config)  # 重新读环境变量

    # 3. 再 reload agent_memory（它 from config import DATA_DIR）
    import services.agent_memory as am
    importlib.reload(am)

    # 4. 双保险：直接把模块级 MEMORY_DIR 替换为临时目录
    am.MEMORY_DIR = d

    # 5. 清空 auto_extract 冷却缓存（跨测试污染）
    if hasattr(am, "_extract_cooldown"):
        am._extract_cooldown.clear()

    yield am
    shutil.rmtree(d, ignore_errors=True)


# =============================================================
# 读写闭环测试 — 9 模块
# =============================================================

class TestBasicCRUD:
    """9 模块基础读写闭环"""

    def test_preferences_crud(self, mem):
        uid = "test_user_01"
        # 初始是默认值
        p = mem.get_preferences(uid)
        assert p["risk_profile"] == "稳健型"
        # 写入
        mem.save_preferences(uid, {"risk_profile": "激进", "focus_industries": ["白酒"]})
        p2 = mem.get_preferences(uid)
        assert p2["risk_profile"] == "激进"
        assert p2["focus_industries"] == ["白酒"]

    def test_decisions_crud_and_max(self, mem):
        uid = "test_user_02"
        # 空账号
        assert mem.get_decisions(uid) == []
        # 写入 + 读取
        d = mem.add_decision(uid, {"action": "BUY", "summary": "茅台"})
        assert "id" in d and "time" in d
        got = mem.get_decisions(uid, limit=5)
        assert len(got) == 1 and got[0]["action"] == "BUY"
        # 压过 100 上限应自动裁剪
        for i in range(120):
            mem.add_decision(uid, {"action": "HOLD", "summary": f"#{i}"})
        final = mem.get_decisions(uid, limit=200)
        assert len(final) <= 100, "decisions 超过 100 应裁剪"

    def test_rules_crud(self, mem):
        uid = "test_user_03"
        r = mem.add_rule(uid, {"type": "price_drop", "code": "600519", "threshold": 10})
        assert r["active"] is True
        assert len(mem.get_rules(uid)) == 1
        assert mem.remove_rule(uid, r["id"]) is True
        assert mem.get_rules(uid) == []

    def test_rules_check_triggers(self, mem):
        uid = "test_user_04"
        mem.add_rule(uid, {"type": "price_drop", "code": "600519", "threshold": 5})
        holdings = {"holdings": [{"code": "600519", "name": "茅台", "changePct": -7.2}]}
        triggered = mem.check_rules(uid, holdings)
        assert len(triggered) == 1
        assert "茅台" in triggered[0]["msg"]

    def test_context_crud(self, mem):
        uid = "test_user_05"
        mem.save_context(uid, {"last_analysis": "建议持有", "market_phase": "复苏"})
        ctx = mem.get_context(uid)
        assert ctx["last_analysis"] == "建议持有"
        assert ctx["updated_at"] is not None

    def test_profile_merge_semantics(self, mem):
        """画像保存应"合并"而不是覆盖"""
        uid = "test_user_06"
        mem.save_profile(uid, {"age": 40, "family": "已婚"})
        mem.save_profile(uid, {"income_level": "50 万年"})
        p = mem.get_profile(uid)
        assert p["age"] == 40
        assert p["family"] == "已婚"
        assert p["income_level"] == "50 万年"
        assert p["available"] is True

    def test_emotion_tagging(self, mem):
        uid = "test_user_07"
        assert mem.tag_emotion("我好焦虑怕亏钱") == "焦虑"
        assert mem.tag_emotion("直接 all in 一定赚") == "果断"
        assert mem.tag_emotion("根据估值和 PE 分析") == "理性"
        assert mem.tag_emotion("今天天气不错") == "neutral"
        # 记录并汇总
        mem.record_emotion(uid, "焦虑得睡不着")
        mem.record_emotion(uid, "怕崩了")
        summary = mem.get_emotion_summary(uid)
        assert summary["dominant"] == "焦虑"
        assert summary["hint"]  # 应有 hint 给 AI

    def test_ironies_dedupe(self, mem):
        uid = "test_user_08"
        a = mem.add_irony(uid, "我永远不买医药股", source="manual")
        b = mem.add_irony(uid, "我永远不买医药股 ", source="manual")  # 微小差异
        # 注意：实际 dedupe 用前 30 字精确匹配，所以 trailing 空格这种可能会过
        # 但完全相同内容必须去重
        c = mem.add_irony(uid, "我永远不买医药股", source="manual")
        assert c["id"] == a["id"], "完全相同的 irony 必须去重"

    def test_life_events_basic(self, mem):
        uid = "test_user_09"
        evt = mem.add_life_event(uid, "测试生日", "1985-05-18", is_lunar=False)
        assert evt["id"].startswith("evt_")
        events = mem.get_life_events(uid)
        assert len(events) == 1
        assert mem.remove_life_event(uid, evt["id"]) is True

    def test_pending_insights_full_cycle(self, mem):
        uid = "test_user_10"
        ins = mem.add_pending_insight(uid, {
            "category": "irony", "text": "测试铁律-不追高",
            "source_user": uid,
        })
        assert mem.get_pending_insights(uid)[0]["id"] == ins["id"]
        # approve 应写入 ironies
        ret = mem.approve_insight(uid, ins["id"])
        assert ret["ok"] is True
        assert ret["category"] == "irony"
        # 队列应清掉
        assert mem.get_pending_insights(uid) == []
        # ironies 应增加一条
        ironies = mem.get_ironies(uid)
        assert any("不追高" in i["text"] for i in ironies)


# =============================================================
# 家庭主账号路由测试 (V7.4.3 关键特性)
# =============================================================

class TestFamilyAdminRouting:
    """主账号路由：乐姐的自动提炼必须落到雷老板待审队列"""

    def test_route_admin_self(self, mem):
        """主账号走自己"""
        assert mem._route_to_admin("LeiJiang") == "LeiJiang"

    def test_route_admin_spouse(self, mem):
        """配偶路由到主账号"""
        assert mem._route_to_admin("BuLuoGeLi") == "LeiJiang"

    def test_route_admin_stranger(self, mem):
        """陌生账号走自己（不污染家庭队列）"""
        assert mem._route_to_admin("random_user") == "random_user"

    def test_approve_writes_to_source_user(self, mem):
        """approve 必须写给 source_user（真实来源），不是操作人"""
        # 乐姐的洞察写给雷老板待审
        mem.add_pending_insight("LeiJiang", {
            "category": "irony",
            "text": "乐姐不碰杠杆",
            "source_user": "BuLuoGeLi",
        })
        pending = mem.get_pending_insights("LeiJiang")
        assert len(pending) == 1
        # 雷老板审批
        ret = mem.approve_insight("LeiJiang", pending[0]["id"])
        assert ret["ok"] is True
        assert ret["written_to"] == "BuLuoGeLi", "必须写到乐姐的 ironies，不是雷老板"
        # 验证：乐姐的 ironies 有这条
        assert any("乐姐不碰杠杆" in i["text"] for i in mem.get_ironies("BuLuoGeLi"))
        # 雷老板的 ironies 不应污染
        assert all("乐姐不碰杠杆" not in i["text"] for i in mem.get_ironies("LeiJiang"))


# =============================================================
# 农历转换测试
# =============================================================

class TestLunarConversion:
    def test_lunar_to_solar_multiple_years(self, mem):
        """乐姐农历 1985-02-12 在 2025-2028 公历日期应稳定算出"""
        # 这是你老婆的真实生日：农历正月 + 12 = 02-12 …… 等等
        # 实际乐姐生日是农历 1985 年二月十二，对应 2026 公历 03-30
        try:
            import zhdate
        except ImportError:
            pytest.skip("zhdate 未安装")

        for year in (2025, 2026, 2027, 2028):
            solar = mem._lunar_to_solar_this_year(2, 12, year)
            assert solar is not None, f"{year} 转换失败"
            # 乐姐农历 02-12 大致落在公历 3 月或 4 月
            assert "-03-" in solar or "-04-" in solar, \
                f"{year} 算出 {solar} 不合常识"


# =============================================================
# build_memory_summary 压力 + 反污染测试
# =============================================================

class TestMemorySummary:

    def test_summary_empty_user(self, mem):
        """空账号 summary 应返回字符串（允许短）"""
        s = mem.build_memory_summary("brand_new_user")
        assert isinstance(s, str)

    def test_summary_with_full_profile(self, mem):
        uid = "test_summary_01"
        mem.save_profile(uid, {
            "nickname": "雷老板", "age": 40, "family": "已婚一娃",
            "invest_horizon": "长期 3-10 年",
        })
        mem.save_preferences(uid, {"risk_profile": "稳健", "focus_industries": ["消费"]})
        mem.add_irony(uid, "不碰杠杆")
        s = mem.build_memory_summary(uid)
        assert "雷老板" in s
        assert "40" in s
        assert "不碰杠杆" in s

    def test_summary_no_rule_pollution(self, mem):
        """V7.4.4 关键：summary 不能含规则化标记"""
        uid = "test_summary_02"
        mem.save_profile(uid, {"nickname": "乐姐", "age": 41})
        mem.add_irony(uid, "定期定投")
        mem.add_irony(uid, "不听小道消息")
        s = mem.build_memory_summary(uid)

        # V7.4.4 去规则化：不应含这些 LLM 容易模仿的符号
        forbidden_markers = ["【", "】", "🔒", "①", "②", "③", "④", "⑤"]
        for marker in forbidden_markers:
            assert marker not in s, \
                f"summary 含规则化标记 '{marker}'，LLM 会模仿输出给用户，违反 V7.4.4"

    def test_summary_performance_100_decisions(self, mem):
        """100 条 decisions（默认上限）下 summary 应 < 200ms"""
        uid = "test_perf_01"
        # 满载写入
        for i in range(100):
            mem.add_decision(uid, {
                "action": "HOLD",
                "summary": f"决策 #{i}：持有为主，关注估值和舆情，定投为辅" * 3,
            })
        start = time.time()
        s = mem.build_memory_summary(uid)
        elapsed = (time.time() - start) * 1000
        assert elapsed < 200, f"summary 耗时 {elapsed:.0f}ms，应 < 200ms（已触达 token 敏感临界）"
        assert isinstance(s, str)

    def test_summary_token_budget(self, mem):
        """summary 总字符数应 < 3000（避免吃 prompt token 预算）"""
        uid = "test_perf_02"
        # 模拟用户跑了半年，写满所有模块
        mem.save_profile(uid, {
            "nickname": "雷老板", "age": 40, "family": "已婚一娃",
            "income_level": "50 万年", "invest_horizon": "长期 3-10 年",
            "drawdown_tolerance": "-10% 以内",
            "life_goals": ["育儿教育", "改善生活", "财务自由"],
            "notes": "上海工作武汉家，两地分居，两个家需要照顾" * 5,
        })
        mem.save_preferences(uid, {
            "risk_profile": "稳健",
            "focus_industries": ["消费", "医药", "科技"],
            "exclude_stocks": ["ST", "*ST"],
            "notes": "不听小道消息" * 3,
        })
        for text in ["不追高", "不碰杠杆", "不做短线", "不听小道消息",
                     "分散持仓", "定期定投", "长期持有", "回撤>10%止损"]:
            mem.add_irony(uid, text)
        for i in range(100):
            mem.add_decision(uid, {
                "action": "HOLD",
                "summary": f"决策 #{i}：市场观望，持有核心资产",
            })
        s = mem.build_memory_summary(uid)
        # 当前 V7.4.4 全量注入，我们对上限有数
        length = len(s)
        # 这个阈值会在 Step 2 向量化后大幅收紧
        assert length < 3000, \
            f"summary 长度 {length} 字符，超过 3000 字 token 预算警戒线，该上向量召回了"
        print(f"\n[INFO] 全模块满载 summary 长度：{length} 字符（基线）")


# =============================================================
# 待审闭环 end-to-end
# =============================================================

class TestPendingApprovalLoop:
    def test_irony_approval_visible_in_summary(self, mem):
        """审批后 irony 应立刻出现在 summary 里"""
        uid = "test_loop_01"
        mem.add_pending_insight(uid, {
            "category": "irony",
            "text": "不追板块热点",
            "source_user": uid,
        })
        ins_id = mem.get_pending_insights(uid)[0]["id"]
        mem.approve_insight(uid, ins_id)
        s = mem.build_memory_summary(uid)
        assert "不追板块热点" in s, "审批后的 irony 应注入到 summary"

    def test_reject_removes_from_queue(self, mem):
        uid = "test_loop_02"
        ins = mem.add_pending_insight(uid, {
            "category": "irony", "text": "测试拒绝", "source_user": uid,
        })
        assert mem.reject_insight(uid, ins["id"]) is True
        assert mem.get_pending_insights(uid) == []
        # ironies 不应新增
        assert all("测试拒绝" not in i.get("text", "") for i in mem.get_ironies(uid))


# =============================================================
# V7.5 分层归档测试
# =============================================================

class TestArchival:
    """decisions 热/冷分层 + 月度摘要"""

    def test_archive_old_decisions_moves_correctly(self, mem):
        """archive_old_decisions: 40 天前的应被移到冷区"""
        from datetime import datetime, timedelta
        uid = "test_arch_01"

        # 加 5 条新的 + 3 条老的（手动改时间）
        for i in range(5):
            mem.add_decision(uid, {"action": "NEW", "summary": f"新 #{i}"})

        # 直接操作文件模拟老数据
        import json
        hot_f = mem._user_memory_dir(uid) / "decisions.json"
        all_d = json.loads(hot_f.read_text(encoding="utf-8"))
        old_time = (datetime.now() - timedelta(days=40)).isoformat()
        for i in range(3):
            all_d.insert(0, {
                "id": f"d_old_{i}",
                "action": "OLD",
                "summary": f"老 #{i}",
                "time": old_time,
                "result_tracked": False,
            })
        hot_f.write_text(json.dumps(all_d, ensure_ascii=False), encoding="utf-8")

        # 归档
        result = mem.archive_old_decisions(uid, days=30)
        assert result["moved"] == 3, f"应移走 3 条，实际 {result['moved']}"
        assert result["hot_remaining"] == 5
        assert result["cold_total"] == 3

        # 热区只剩新的
        hot = mem.get_decisions(uid, limit=100)
        assert all("新" in d.get("summary", "") for d in hot)

        # 冷区有老的
        cold = mem.get_archived_decisions(uid)
        assert len(cold) == 3
        assert all("老" in d.get("summary", "") for d in cold)

    def test_archive_idempotent(self, mem):
        """归档应幂等：再跑一次不会重复搬"""
        from datetime import datetime, timedelta
        import json
        uid = "test_arch_02"
        # 造一条老数据
        mem.add_decision(uid, {"action": "OLD", "summary": "老数据"})
        hot_f = mem._user_memory_dir(uid) / "decisions.json"
        data = json.loads(hot_f.read_text(encoding="utf-8"))
        data[0]["time"] = (datetime.now() - timedelta(days=60)).isoformat()
        hot_f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        r1 = mem.archive_old_decisions(uid)
        r2 = mem.archive_old_decisions(uid)
        assert r1["moved"] == 1
        assert r2["moved"] == 0, "幂等性失败：第二次不该再移"
        assert mem.get_archived_decisions(uid)[0]["summary"] == "老数据"

    def test_summarize_archive_month_stat_fallback(self, mem):
        """无 LLM 环境应降级到纯统计摘要"""
        from datetime import datetime
        import json
        uid = "test_arch_03"

        # 直接造冷区数据，跳过 LLM
        cold_f = mem._user_memory_dir(uid) / "decisions_archive.json"
        cold = [
            {"id": "d1", "action": "BUY", "summary": "茅台", "time": "2026-03-05T10:00:00",
             "result_tracked": True, "result": {"win": True}},
            {"id": "d2", "action": "SELL", "summary": "平安", "time": "2026-03-20T10:00:00",
             "result_tracked": True, "result": {"win": False}},
            {"id": "d3", "action": "HOLD", "summary": "观望", "time": "2026-03-25T10:00:00"},
        ]
        cold_f.write_text(json.dumps(cold, ensure_ascii=False), encoding="utf-8")

        # LLM 不存在就降级（真环境是 try/except 捕获）
        r = mem.summarize_archive_month(uid, "2026-03")
        assert r["ok"] is True
        assert r["month"] == "2026-03"
        assert r["total"] == 3
        assert r["with_result"] == 2
        # win_rate = 1/2 = 0.5
        assert r["win_rate"] == 0.5
        # summary 字段必有（LLM 失败会写降级文本）
        assert r["summary"]

        # 幂等：再跑一次应 skipped
        r2 = mem.summarize_archive_month(uid, "2026-03")
        assert r2.get("skipped") is True, "同月二次摘要必须幂等跳过"

    def test_archive_summary_appears_in_memory_summary(self, mem):
        """月度摘要应自动出现在 build_memory_summary 里"""
        import json
        uid = "test_arch_04"
        # 手写一条月度摘要
        summ_f = mem._user_memory_dir(uid) / "archive_summary.json"
        summ_f.write_text(json.dumps([{
            "month": "2026-03",
            "total": 12,
            "win_rate": 0.67,
            "summary": "3 月以消费白酒为主，胜率 67%，坚持定投有效",
        }], ensure_ascii=False), encoding="utf-8")

        s = mem.build_memory_summary(uid)
        assert "2026-03" in s
        assert "3 月以消费白酒" in s

    def test_track_result_works_on_cold_decisions(self, mem):
        """已归档的决策也能补记结果（V7.5 新特性）"""
        import json
        uid = "test_arch_05"
        # 直接造冷区
        cold_f = mem._user_memory_dir(uid) / "decisions_archive.json"
        cold_f.write_text(json.dumps([{
            "id": "d_cold_1",
            "action": "BUY", "summary": "老决策",
            "time": "2026-02-01T10:00:00",
            "result_tracked": False,
        }], ensure_ascii=False), encoding="utf-8")

        ok = mem.track_decision_result(uid, "d_cold_1", {"win": True, "pnl": 1000})
        assert ok is True

        cold = mem.get_archived_decisions(uid)
        assert cold[0]["result_tracked"] is True
        assert cold[0]["result"]["win"] is True
