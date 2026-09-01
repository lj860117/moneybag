"""
save_user_data 乐观并发控制守门测试（方案A，2026-09-01）
================================================================
背景：`services/persistence.py::user_write_lock` 只保护服务端内部一次
请求的读-改-写原子性，管不到"客户端在提交前读取旧数据"这一步——那一步
发生在浏览器端、请求送达服务端之前。真实风险场景：手机在 T1 读到旧
portfolio，PC 在 T2 写入新 portfolio，手机在 T3 用 T1 时的旧快照 + 自己
的改动整体写回，会静默抹掉 PC 在 T2 写入的内容，用户毫无察觉——这是
本项目"唯一一处锁修不了的丢更新"（docs/superpowers/plans/
2026-08-30-next-phase-concurrency.md）。

本次修复：POST /api/user/save 新增 expectedUpdatedAt 字段（客户端上次
从 GET .../portfolio 读到的 updatedAt），服务端在 user_write_lock 内
比对，不一致则拒绝写入并返回 409 + 服务端当前最新状态。

这个文件测什么：
  - 正常场景（不传 expectedUpdatedAt，旧客户端/首次同步）：跳过检测，
    直接覆盖，行为与修复前完全一致——渐进式升级不应破坏旧客户端。
  - 冲突场景：expectedUpdatedAt 与服务端当前 updatedAt 不一致时，
    必须返回 409，且**不能改动任何数据**（拒绝的请求不应有副作用）。
  - 无冲突场景：expectedUpdatedAt 与服务端当前 updatedAt 一致时，
    正常写入成功，返回体带上新的 updatedAt 供客户端更新本地缓存。
  - 校验必须在锁内（不是锁外先查再进锁）——用并发场景验证：两个请求
    都拿着同一个 expectedUpdatedAt 同时提交，只能有一个成功，另一个
    必须收到 409（如果校验在锁外，两个都会通过校验一起写入，后写的
    覆盖先写的，等于没做保护）。

不测什么：
  - user_write_lock 本身的线程锁/flock 机制（那是既有基础设施，
    这里只测"乐观锁比对逻辑是否正确接入"这层新增的契约）。
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def user_module(tmp_path, monkeypatch):
    """每个测试用独立的 DATA_DIR，避免测试间通过文件互相污染。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import config
    importlib.reload(config)
    import services.persistence as persistence
    importlib.reload(persistence)
    import api.user as user_api
    importlib.reload(user_api)
    import models.schemas as schemas
    importlib.reload(schemas)
    yield user_api, persistence, schemas


def test_save_without_expected_updated_at_skips_conflict_check(user_module):
    """不传 expectedUpdatedAt（旧客户端/首次同步）时，跳过冲突检测，
    行为与修复前一致——不能因为升级了这个功能就让旧客户端全部失败。
    """
    user_api, persistence, schemas = user_module

    data1 = schemas.UserData(userId="test_user", portfolio={"holdings": [{"code": "000001"}]})
    result1 = user_api.save_user_data(data1)
    assert result1["status"] == "ok"

    # 第二次保存，仍不传 expectedUpdatedAt，即使数据已经变了也应该直接覆盖成功
    data2 = schemas.UserData(userId="test_user", portfolio={"holdings": [{"code": "000002"}]})
    result2 = user_api.save_user_data(data2)
    assert result2["status"] == "ok"

    saved = persistence.load_user("test_user")
    assert saved["portfolio"]["holdings"][0]["code"] == "000002"


def test_save_with_matching_expected_updated_at_succeeds(user_module):
    """expectedUpdatedAt 与服务端当前一致时，正常写入成功。"""
    user_api, persistence, schemas = user_module

    data1 = schemas.UserData(userId="test_user", portfolio={"holdings": [{"code": "000001"}]})
    result1 = user_api.save_user_data(data1)
    current_updated_at = result1["updatedAt"]

    data2 = schemas.UserData(
        userId="test_user",
        portfolio={"holdings": [{"code": "000001"}, {"code": "000002"}]},
        expectedUpdatedAt=current_updated_at,
    )
    result2 = user_api.save_user_data(data2)
    assert result2["status"] == "ok"

    saved = persistence.load_user("test_user")
    assert len(saved["portfolio"]["holdings"]) == 2


def test_save_with_stale_expected_updated_at_returns_409(user_module):
    """核心场景：expectedUpdatedAt 是过期版本时（模拟"手机在 T1 读到旧数据，
    PC 在 T2 已经写入新数据，手机在 T3 用 T1 的旧版本号提交"），必须拒绝写入
    并返回 409，不能静默覆盖 PC 的改动。
    """
    from fastapi import HTTPException
    user_api, persistence, schemas = user_module

    # T1：手机读到初始状态（这个动作在真实场景里发生在 GET .../portfolio）
    data_initial = schemas.UserData(userId="test_user", portfolio={"holdings": [{"code": "A"}]})
    result_initial = user_api.save_user_data(data_initial)
    phone_expected_updated_at = result_initial["updatedAt"]

    # 确保 updatedAt 时间戳会变化（同一毫秒内两次 isoformat 可能相同）
    time.sleep(0.01)

    # T2：PC 端写入了新持仓 B（不知道手机的存在，也不传 expectedUpdatedAt——
    # 模拟"PC 用的是尚未升级的旧版本客户端"这个真实场景，验证新旧客户端能共存）
    data_pc = schemas.UserData(userId="test_user", portfolio={"holdings": [{"code": "A"}, {"code": "B"}]})
    result_pc = user_api.save_user_data(data_pc)
    assert result_pc["status"] == "ok"
    server_updated_at_after_pc = result_pc["updatedAt"]
    assert server_updated_at_after_pc != phone_expected_updated_at, (
        "测试前提不成立：PC 写入后 updatedAt 应该变化，否则测的不是真实场景"
    )

    # T3：手机用 T1 时的旧 expectedUpdatedAt 提交，追加持仓 D
    data_phone = schemas.UserData(
        userId="test_user",
        portfolio={"holdings": [{"code": "A"}, {"code": "D"}]},
        expectedUpdatedAt=phone_expected_updated_at,
    )
    with pytest.raises(HTTPException) as exc_info:
        user_api.save_user_data(data_phone)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "conflict"
    # 服务端返回的应该是 PC 刚写入的最新状态，不是手机自己那份过期快照
    assert exc_info.value.detail["serverUpdatedAt"] == server_updated_at_after_pc

    # 关键断言：PC 写入的持仓 B 必须还在——手机的过期提交不能造成任何数据改动
    saved = persistence.load_user("test_user")
    codes = [h["code"] for h in saved["portfolio"]["holdings"]]
    assert "B" in codes, "409 拒绝后，服务端数据不应被手机的过期提交污染"
    assert "D" not in codes, "409 拒绝的写入不应有任何副作用"


def test_conflict_check_happens_inside_lock_not_before(user_module):
    """校验必须在锁内做，不能在锁外先查再进锁——用真实并发验证：
    两个线程拿着同一个 expectedUpdatedAt 同时提交，只能有一个成功。
    如果校验在锁外，两个请求都会先通过校验（此时服务端状态还没变），
    然后都进锁写入，后写的覆盖先写的——等于没做保护。
    """
    user_api, persistence, schemas = user_module

    data_initial = schemas.UserData(userId="test_user", portfolio={"holdings": [{"code": "A"}]})
    result_initial = user_api.save_user_data(data_initial)
    shared_expected_updated_at = result_initial["updatedAt"]

    results = []
    errors = []

    def _try_save(code):
        try:
            data = schemas.UserData(
                userId="test_user",
                portfolio={"holdings": [{"code": "A"}, {"code": code}]},
                expectedUpdatedAt=shared_expected_updated_at,
            )
            r = user_api.save_user_data(data)
            results.append(r)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_try_save, args=(f"concurrent_{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 5 个线程用同一个过期版本号并发提交，必须恰好 1 个成功、其余全部 409。
    # 如果校验在锁外，可能出现 >1 个成功（说明校验被绕过）。
    assert len(results) == 1, f"应该恰好 1 个成功，实际 {len(results)} 个: {results}"
    assert len(errors) == 4, f"应该恰好 4 个被拒绝，实际 {len(errors)} 个"
    for e in errors:
        assert e.status_code == 409
