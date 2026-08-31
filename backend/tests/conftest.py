"""
backend/tests/ 共享 pytest 配置

背景（FIX 2026-09-01）：`scripts/cache_warmer.py` 模块级代码会在 import
时手动解析 `.env` 并用 `os.environ.setdefault(k, v)` 写入真实密钥——这是
生产环境的正常兜底行为（有些 crontab 条目不走 `set -a && . .env`，脚本
自己兜底加载一次），不应该改。

但这段代码是**模块级副作用**：只要任何测试 `import scripts.cache_warmer`
（哪怕只是为了测别的函数），Python 的模块缓存机制就会让这次 import 只执行
一次，然后**永久污染当前 pytest 进程的 os.environ**——不是通过
`monkeypatch.setenv` 设置的，`monkeypatch` 的自动回滚机制根本管不到它。

真实故障链：本地开发没有 `.env` 文件（在 .gitignore 里）测不出这个问题，
只有服务器上真实 `.env` 存在时才会触发；`test_save_cache_can_replace_
readonly_existing_file` import 了 cache_warmer 后，`test_health_does_not_
flag_missing_cfo_cache_as_degraded` 断言"deepseek key 应为 missing"就会
失败——因为它读到的是这次污染写进去的真实 LLM_API_KEY，不是测试自己设置
的值。单独跑这个测试永远通过（污染源没被 import 过），混在全量套件里跑
才会暴露——这正是为什么必须在服务器隔离环境跑全量套件核对，本地单测
"看起来通过"是不可信的。

修法：在每个测试函数运行前，把 .env 里出现的全部密钥类环境变量强制清空
（autouse fixture，无需每个测试文件单独引用）。选择"清空"而不是"记录
原值再还原"，是因为测试进程本来就不应该依赖真实密钥——如果某个测试确实
需要模拟"已配置"的状态，应该用 monkeypatch.setenv 显式设置，而不是依赖
残留的真实值。
"""
import os
import pytest

# 与 backend/.env 里出现的 key 名保持一致（脱敏后的清单，不含真实值）。
# 新增密钥类环境变量时记得同步补充这里。
_SECRET_ENV_KEYS = (
    "LLM_API_KEY", "LLM_API_BASE", "LLM_MODEL",
    "WXWORK_CORP_ID", "WXWORK_AGENT_ID", "WXWORK_SECRET",
    "WXWORK_TOUSER", "WXWORK_CALLBACK_TOKEN", "WXWORK_CALLBACK_AES_KEY",
    "TUSHARE_TOKEN",
    "DOUBAO_API_KEY", "DASHSCOPE_API_KEY",
    "DOUBAO_API_BASE", "DASHSCOPE_API_BASE",
)


@pytest.fixture(autouse=True)
def _clear_secret_env_pollution(monkeypatch):
    """每个测试运行前清空密钥类环境变量，防止 cache_warmer 等模块的
    import 时副作用泄漏真实密钥到其他测试。

    用 monkeypatch.delenv 而不是直接 del os.environ[...]：这样测试结束
    monkeypatch 会自动恢复（虽然我们期望恢复后也是"未设置"，但这样写法
    统一、且不会影响测试进程之外的环境）。
    """
    for key in _SECRET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
