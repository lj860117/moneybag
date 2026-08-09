# Peak-time Model Routing and Provider Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让钱袋子交互型 AI 在 DeepSeek 峰价时段默认优先豆包/千问，非峰价时段默认回到 DeepSeek，同时恢复线上多模型可用状态与统一降级链。

**Architecture:** 把“峰谷时间判断 + 默认模型选择 + tier 路由”收敛到 `backend/services/llm_gateway.py`，避免 `chat.py`、`chat_fc.py`、`/api/models` 各自硬编码。API 层只负责区分“显式选模”和“默认选模”；显式选模永远优先，默认选模由 gateway 按时间和已配置 provider 决定。

**Tech Stack:** FastAPI, Pydantic, Python unittest/pytest, SSE, systemd, SSH 精准部署

---

## 已确认前提 / Blockers

- 本地仓库很脏，部署必须继续走**精准文件上传/远程定点 patch**，不能整仓 rsync。
- 本地 `backend/.env` 当前状态：`LLM_API_KEY=SET`，`DOUBAO_API_KEY=EMPTY`，`DASHSCOPE_API_KEY=EMPTY`。
- 因此“恢复线上多模型可用”这一步仍需要用户提供豆包/千问 key，或明确告诉我线上 key 应从哪里取。

### Task 1: 在 gateway 中实现峰谷默认选模与统一路由

**Files:**
- Modify: `backend/services/llm_gateway.py`
- Test: `backend/tests/test_chat_model_routing.py`

- [ ] **Step 1: 先写失败用例，锁定峰时/非峰时默认模型行为**

```python
import os
import unittest
from unittest.mock import patch

from backend.services import llm_gateway as gw_mod


class TestPeakAwareRouting(unittest.TestCase):
    def test_peak_prefers_doubao_for_light_tier(self):
        with patch.dict(os.environ, {
            'LLM_API_KEY': 'ds',
            'DOUBAO_API_KEY': 'db',
            'DASHSCOPE_API_KEY': 'qw',
        }, clear=False):
            with patch.object(gw_mod, '_is_deepseek_peak_window', return_value=True):
                self.assertEqual(
                    gw_mod.resolve_default_model('llm_light'),
                    'doubao-seed-2-0-lite-260215',
                )

    def test_offpeak_prefers_deepseek_for_light_tier(self):
        with patch.dict(os.environ, {
            'LLM_API_KEY': 'ds',
            'DOUBAO_API_KEY': 'db',
            'DASHSCOPE_API_KEY': 'qw',
        }, clear=False):
            with patch.object(gw_mod, '_is_deepseek_peak_window', return_value=False):
                self.assertEqual(
                    gw_mod.resolve_default_model('llm_light'),
                    'deepseek-v4-flash',
                )

    def test_peak_falls_back_to_qwen_when_doubao_missing(self):
        with patch.dict(os.environ, {
            'LLM_API_KEY': 'ds',
            'DOUBAO_API_KEY': '',
            'DASHSCOPE_API_KEY': 'qw',
        }, clear=False):
            with patch.object(gw_mod, '_is_deepseek_peak_window', return_value=True):
                self.assertEqual(
                    gw_mod.resolve_default_model('llm_light'),
                    'qwen3.6-flash',
                )
```

- [ ] **Step 2: 跑测试，确认当前实现先红灯**

Run: `PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" /Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python -m pytest /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_chat_model_routing.py -q`

Expected: FAIL，报 `resolve_default_model` / `_is_deepseek_peak_window` 不存在或返回值不符。

- [ ] **Step 3: 在 gateway 新增峰谷辅助函数，替代静态 light/heavy/reasoning 默认路由**

```python
DEEPSEEK_PEAK_WINDOWS = (
    ((9, 0), (12, 0)),
    ((14, 0), (18, 0)),
)


def _is_deepseek_peak_window(now=None) -> bool:
    from datetime import datetime
    now = now or datetime.now()
    hm = (now.hour, now.minute)
    return ((9, 0) <= hm < (12, 0)) or ((14, 0) <= hm < (18, 0))


def resolve_default_model(model_tier: str = 'llm_light') -> str:
    deepseek_map = {
        'llm_light': 'deepseek-v4-flash',
        'llm_heavy': 'deepseek-v4-pro',
        'llm_reasoning': 'deepseek-reasoner',
    }
    doubao_map = {
        'llm_light': 'doubao-seed-2-0-lite-260215',
        'llm_heavy': 'doubao-seed-2-0-pro-260215',
        'llm_reasoning': 'doubao-seed-2-0-pro-260215',
    }
    qwen_map = {
        'llm_light': 'qwen3.6-flash',
        'llm_heavy': 'qwen3.6-plus',
        'llm_reasoning': 'qwen3.6-plus',
    }

    if _is_deepseek_peak_window():
        if os.environ.get('DOUBAO_API_KEY'):
            return doubao_map.get(model_tier, 'doubao-seed-2-0-lite-260215')
        if os.environ.get('DASHSCOPE_API_KEY'):
            return qwen_map.get(model_tier, 'qwen3.6-flash')

    if os.environ.get('LLM_API_KEY'):
        return deepseek_map.get(model_tier, 'deepseek-v4-flash')
    if os.environ.get('DOUBAO_API_KEY'):
        return doubao_map.get(model_tier, 'doubao-seed-2-0-lite-260215')
    if os.environ.get('DASHSCOPE_API_KEY'):
        return qwen_map.get(model_tier, 'qwen3.6-flash')
    return deepseek_map.get(model_tier, 'deepseek-v4-flash')
```

- [ ] **Step 4: 让 `call_sync()` / `stream_sync()` / `get_api_config()` 全部走统一默认解析**

```python
if explicit_model:
    model = explicit_model
else:
    model = resolve_default_model(model_tier)
```

```python
def get_api_config(self, model_tier: str = 'llm_light') -> dict:
    model = resolve_default_model(model_tier)
    api_key, api_base, _provider = self._route_model(model)
    return {'api_key': api_key, 'api_base': api_base, 'model': model}
```

- [ ] **Step 5: 重跑单测，确认 helper 行为转绿**

Run: `PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" /Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python -m pytest /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_chat_model_routing.py -q`

Expected: PASS

### Task 2: 接入 chat / models / FC，补显式选模透传

**Files:**
- Modify: `backend/api/chat.py`
- Modify: `backend/api/chat_fc.py`
- Test: `backend/tests/test_chat_model_routing.py`

- [ ] **Step 1: 给 `/api/models` 加时间感知 default，并保留只返回已配置模型**

```python
@router.get('/api/models')
def list_models():
    from services.llm_gateway import resolve_default_model

    result = []
    for m in AVAILABLE_MODELS:
        key = os.environ.get(m['env_key'], '')
        if key:
            result.append({'id': m['id'], 'name': m['name'], 'provider': m['provider']})

    return {
        'models': result,
        'default': resolve_default_model('llm_light'),
    }
```

- [ ] **Step 2: 修复非流式 `/api/chat`，显式模型必须真正透传到 gateway**

```python
from services.llm_gateway import LLMGateway, resolve_default_model

gw = LLMGateway.instance()
model = req.model or resolve_default_model('llm_light')

gw_result = gw.call_sync(
    user_msg,
    system=system_prompt,
    model_tier='llm_light',
    user_id=uid,
    module='chat',
    max_tokens=800,
    explicit_model=model,
)
```

- [ ] **Step 3: 修复流式 `/api/chat/stream` 和 FC 入口的默认模型硬编码**

```python
model = req.model or gw.get_api_config(model_tier='llm_light')['model']
```

```python
for chunk in run_fc_agent_stream(
    user_msg,
    system_prompt=system_prompt,
    user_id=uid,
    model=req.model or gw.get_api_config(model_tier='llm_light')['model'],
    history=history_dicts,
):
```

- [ ] **Step 4: 给 `/api/models` 默认值与非流式透传补回归测试**

```python
class TestChatApiModelDefaults(unittest.TestCase):
    def test_list_models_returns_peak_aware_default(self):
        from backend.api.chat import list_models
        with patch.dict(os.environ, {
            'LLM_API_KEY': 'ds',
            'DOUBAO_API_KEY': 'db',
            'DASHSCOPE_API_KEY': 'qw',
        }, clear=False):
            with patch('backend.api.chat.resolve_default_model', return_value='doubao-seed-2-0-lite-260215'):
                data = list_models()
                self.assertEqual(data['default'], 'doubao-seed-2-0-lite-260215')
```

- [ ] **Step 5: 运行语法检查与定向测试**

Run:
- `PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" /Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python -m py_compile /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/api/chat.py /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/api/chat_fc.py /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/services/llm_gateway.py`
- `PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" /Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python -m pytest /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_chat_model_routing.py /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_briefing_push_guard.py -q`

Expected: 全部 PASS

### Task 3: 精准部署、补线上环境变量、做回归验证

**Files:**
- Modify remote: `/opt/moneybag/backend/services/llm_gateway.py`
- Modify remote: `/opt/moneybag/backend/api/chat.py`
- Modify remote: `/opt/moneybag/backend/api/chat_fc.py`
- Optional upload: `backend/tests/test_chat_model_routing.py`（仅远端临时验证时需要）

- [ ] **Step 1: 先备份线上目标文件与 `.env`**

Run:

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 '
cp /opt/moneybag/backend/services/llm_gateway.py /opt/moneybag/backend/services/llm_gateway.py.bak-$(date +%Y%m%d-%H%M%S) &&
cp /opt/moneybag/backend/api/chat.py /opt/moneybag/backend/api/chat.py.bak-$(date +%Y%m%d-%H%M%S) &&
cp /opt/moneybag/backend/api/chat_fc.py /opt/moneybag/backend/api/chat_fc.py.bak-$(date +%Y%m%d-%H%M%S) &&
cp /opt/moneybag/backend/.env /opt/moneybag/backend/.env.bak-$(date +%Y%m%d-%H%M%S)
'
```

- [ ] **Step 2: 只有在拿到用户提供的 key 后，才补齐线上环境变量**

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 '
python3 - <<"PY"
from pathlib import Path
fp = Path("/opt/moneybag/backend/.env")
text = fp.read_text(encoding="utf-8")
updates = {
    "DOUBAO_API_KEY": "<USER_PROVIDED>",
    "DASHSCOPE_API_KEY": "<USER_PROVIDED>",
}
for k, v in updates.items():
    if f"{k}=" in text:
        import re
        text = re.sub(rf"^{k}=.*$", f"{k}={v}", text, flags=re.M)
    else:
        text += f"\n{k}={v}"
fp.write_text(text + "\n", encoding="utf-8")
PY
'
```

- [ ] **Step 3: 精准上传 3 个目标文件，避免把本地脏改动一起发上去**

Run:

```bash
rsync -avz -e "ssh -i ~/.ssh/id_ed25519" \
  /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/services/llm_gateway.py \
  /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/api/chat.py \
  /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/api/chat_fc.py \
  ubuntu@150.158.47.189:/opt/moneybag/tmp-llm-routing/
```

- [ ] **Step 4: 服务器分发、语法检查、重启服务**

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 '
mkdir -p /opt/moneybag/tmp-llm-routing &&
mv /opt/moneybag/tmp-llm-routing/llm_gateway.py /opt/moneybag/backend/services/llm_gateway.py &&
mv /opt/moneybag/tmp-llm-routing/chat.py /opt/moneybag/backend/api/chat.py &&
mv /opt/moneybag/tmp-llm-routing/chat_fc.py /opt/moneybag/backend/api/chat_fc.py &&
python3 -m py_compile /opt/moneybag/backend/services/llm_gateway.py /opt/moneybag/backend/api/chat.py /opt/moneybag/backend/api/chat_fc.py &&
sudo systemctl restart moneybag &&
sleep 3 &&
sudo systemctl is-active moneybag
'
```

Expected: `active`

- [ ] **Step 5: 验证 `/api/models`、默认模型、显式选模、企微网关回退链**

Run:

```bash
curl -s --max-time 10 http://150.158.47.189:8000/api/health
curl -s --max-time 10 http://150.158.47.189:8000/api/models
curl -s --max-time 20 -X POST http://150.158.47.189:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"现在市场怎么样","userId":"LeiJiang","model":"qwen3.6-flash"}'
```

Expected:
- `/api/models.models` 同时出现 DeepSeek / 豆包 / 千问（前提是 key 已补齐）
- `/api/models.default` 在峰价窗口返回豆包或千问，在非峰价窗口返回 DeepSeek
- 非流式 `/api/chat` 在显式指定 `qwen3.6-flash` 时不再偷偷回到 DeepSeek

- [ ] **Step 6: 做晨报/企微降级烟测**

Run:

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 '
cd /opt/moneybag/backend &&
python3 scripts/night_worker.py --dry-run
'
```

Expected: 不报 provider key 缺失；若主模型失败，日志可见继续降级到豆包/千问，而不是直接整段 fallback。

- [ ] **Step 7: 完成后写记忆并记录线上验证结论**

```markdown
## 2026-07-05 峰谷自动选模与多模型恢复
- llm_gateway 新增峰谷默认选模；高峰默认豆包/千问，非高峰默认 DeepSeek。
- 修复非流式 chat 未透传 explicit_model。
- /api/models default 改为时间感知。
- 线上补齐 DOUBAO_API_KEY / DASHSCOPE_API_KEY 后，已验证模型列表与降级链恢复。
```

## 执行建议

1. 先按 Task 1-2 在本地把代码和测试补齐。
2. 拿到豆包/千问 key 之后，再执行 Task 3 上线。
3. 若用户现在不能提供 key，则本轮做到“本地代码 + 本地测试 + 待部署补丁说明”即可，不要假装线上已恢复。
