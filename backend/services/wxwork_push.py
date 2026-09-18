"""
钱袋子 — 企业微信推送服务
通过企业微信应用消息推送盯盘信号到个人微信

配置方式（环境变量）：
  WXWORK_CORP_ID    企业 ID
  WXWORK_SECRET     应用 Secret
  WXWORK_AGENT_ID   应用 AgentID
  WXWORK_USER_ID    接收人（@all 或具体 userId）

注册流程（用户操作约 10 分钟）：
  1. 访问 https://work.weixin.qq.com/ → 注册企业微信（个人也行）
  2. 管理后台 → 应用管理 → 创建应用 → 取 AgentID + Secret
  3. 我的企业 → 取 CorpID
  4. 设置信任 IP（腾讯云公网 IP）
  5. 微信插件 → 邀请成员关注 → 消息就会推到微信
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "wxwork_push",
    "scope": "public",
    "input": [],
    "output": "push_result",
    "cost": "cpu",
    "tags": ['推送', '企微', 'AES'],
    "description": "企业微信推送：AES加解密+access_token管理+文本消息",
    "layer": "output",
    "priority": 8,
}
import os
import re
import json
import math
import time
import bisect
import datetime
import httpx
from pathlib import Path

from infra.cache import MemoryCache

# 配置从环境变量读取
#
# ⚠️ FIX 2026-09-13：配置必须**延迟读取**（调用时才 os.getenv），不能在
#    import 期抓死。旧实现是四行 `_X = os.getenv(...)`，在 import 时求值一次
#    就永久固定 —— conftest 的 autouse fixture `monkeypatch.delenv(
#    "WXWORK_CORP_ID")` 对它们**完全无效**。只要模块曾在密钥还在的环境里被
#    import 过一次，is_configured() 就恒为 True：2026-09-13 test_chat_model_
#    routing.py 用 fake httpx 造的 HTTP 402，就是顺着这条恒 True 的通道真推
#    到了用户企微（事故 519045b）。
#
# 但这四个**模块级名字必须保留**，不能改成纯函数或删掉：
#   • backend/tests/test_qa_egress_independent.py::prod_like_wecom
#       monkeypatch.setattr(wp, "_CORP_ID", "QA-FAKE-CORP", raising=False)
#   • backend/tests/qa_egress_probe.py（QA_FORCE_WECOM_CREDS=1）
#       wp._CORP_ID = "QA-FAKE-CORP"
#   两者都靠**覆盖模块级常量**来伪造「生产已配置」状态。删掉或改成同名函数
#   会让这些用例**静默退化** —— raising=False 的 setattr 不报错，但
#   is_configured() 恒 False → 根本走不到推送分支 → 那是一道空转的绿，
#   比红更危险。所以：名字保留、语义升级为「外部覆盖优先，否则实时读」
#   （见 _read_config）。
_DEFAULT_USER_ID = "@all"

# import 期快照：**唯一用途**是识别「模块级名字是否被外部覆盖过」，
# 不直接当作配置值使用（配置值一律走 _read_config 实时解析）。
_IMPORT_TIME_ENV: dict = {
    "WXWORK_CORP_ID": os.getenv("WXWORK_CORP_ID", ""),
    "WXWORK_SECRET": os.getenv("WXWORK_SECRET", ""),
    "WXWORK_AGENT_ID": os.getenv("WXWORK_AGENT_ID", ""),
    "WXWORK_USER_ID": os.getenv("WXWORK_USER_ID", _DEFAULT_USER_ID),
}

_CORP_ID = _IMPORT_TIME_ENV["WXWORK_CORP_ID"]
_SECRET = _IMPORT_TIME_ENV["WXWORK_SECRET"]
_AGENT_ID = _IMPORT_TIME_ENV["WXWORK_AGENT_ID"]
_USER_ID = _IMPORT_TIME_ENV["WXWORK_USER_ID"]


def _read_config(attr_name: str, env_key: str, default: str = "") -> str:
    """延迟读取单项企微配置。

    解析顺序（**不可调换**）：
      1. 模块级同名属性被外部显式覆盖（值与 import 期快照不同）→ 用覆盖值。
         这是给上面两个 QA 场景留的兼容通道，必须继续有效。
      2. 否则**调用时**实时 os.getenv → 这样 conftest 的 delenv / setenv
         才真正能控制住 is_configured()（本次修复的核心）。

    生产行为零变化：环境变量没被改动时，os.getenv 返回值与 import 期快照
    完全一致，生产进程观察到的行为与旧实现一字不差。

    Args:
        attr_name: 模块级兼容属性名，如 "_CORP_ID"
        env_key: 对应的环境变量名，如 "WXWORK_CORP_ID"
        default: 环境变量缺失时使用的默认值

    Returns:
        str: 当前生效的配置值（可能为空串，表示未配置）
    """
    current = globals().get(attr_name)
    if isinstance(current, str) and current != _IMPORT_TIME_ENV[env_key]:
        return current
    return os.getenv(env_key, default)


def _corp_id() -> str:
    """当前生效的企业 ID（延迟读取）。"""
    return _read_config("_CORP_ID", "WXWORK_CORP_ID", "")


def _secret() -> str:
    """当前生效的应用 Secret（延迟读取）。"""
    return _read_config("_SECRET", "WXWORK_SECRET", "")


def _agent_id() -> str:
    """当前生效的应用 AgentID（延迟读取）。"""
    return _read_config("_AGENT_ID", "WXWORK_AGENT_ID", "")


def _default_user_id() -> str:
    """当前生效的默认接收人（延迟读取）。"""
    return _read_config("_USER_ID", "WXWORK_USER_ID", _DEFAULT_USER_ID)


# access_token 有效期 2 小时（企微规范），提前 5 分钟刷新
_TOKEN_CACHE_TTL = 7200

# access_token 缓存（2 小时有效）
_token_cache = MemoryCache(default_ttl=_TOKEN_CACHE_TTL)  # {"wxwork_token": {"data": {"token": str, "expires": int}, "ts": float}}

# FIX: 全局 HTTP 连接池复用（避免每次 send 都新建 TCP 连接）
_http_client = httpx.Client(timeout=15, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))

# FIX: 81013 无效用户缓存（同一用户 81013 只打一次日志，避免刷屏）
_81013_warned = set()


# ============================================================
# 推送长度常量（单位统一为【字节】）
# ============================================================
# ⚠️ 教训（v9.9.20 B 系列修复）：历史实现用「字符数」跟企微上限比较
#    （MAX_CHUNK = 1800），但企微按「字节」限流，中文 1 字 = 3 字节。
#     实测晨报 2.38~2.43 字节/字符 → 字符判断会把真实体积系统性低估约 2.4 倍：
#     2026-09-11 晨报 912 字符 / 2194 字节（含信封）永远打不破 1800「字符」的
#     分片阈值 → 不分片、单条直发 → 被企微在 2048 字节处硬截断，
#     丢掉【操作建议】整段 + 免责声明。
#     规矩：任何与通道上限比较的地方，必须先 .encode("utf-8") 再 len()。
# ============================================================

# 企微通道硬上限（字节）
WECOM_TEXT_LIMIT = 2048        # text     消息 content 上限
WECOM_MARKDOWN_LIMIT = 4096    # markdown 消息 content 上限

# 分段预算 = 通道上限 − 预留（预留给分片标记 "(i/N)" 和换行）
TEXT_CHUNK_BUDGET = 1800       # 2048 − 248
MARKDOWN_CHUNK_BUDGET = 3900   # 4096 − 196

# B5 长度护栏告警线。选 3600 而不是贴着 4096 上限，理由：
#   ① 给 4096 留 ~496 字节余量，避免时间戳/分片标记等边角开销把消息顶出上限；
#   ② 晨报长度在单调增长（09-04 1528B → 09-11 2172B，约 +92B/天），
#      3600 相对当前 2194B 给出约 15 天的提前量，足够在真正撞线前被人看到并处理；
#   ③ >=3600 只告警不裁剪 —— 分片本身是无损的，砍内容才是纯损失。
LENGTH_ALERT_BYTES = 3600

# send_daily_report_to 拼装的信封开销（实测拆解）：
#   "☀️ 钱袋子早安简报" + "\n\n"      = 30 字节
#   "\n\n" + "⏰ 2026-09-11 08:30"     = 22 字节
#                                    合计 52 字节
# ⚠️ archive_push 只存 body、不存信封，所以质量检查必须把这 52 字节补回来，
#    否则会系统性少算。这正是 2026-09-11 BuLuoGeLi 晨报 body=2035B「通过检查」
#    但实际发送 2087B 被截断、监控却一直绿的原因。
PUSH_ENVELOPE_OVERHEAD_BYTES = 52

# 长度/截断事件日志（JSONL），供 daily_push_quality_check 读取
_LENGTH_EVENT_FILE = "push_length_events.jsonl"

# 分段优先切分点：(标记, 是否归入上一段)
#   内容类标记（"持仓明细" 等）必须成为新一段的开头 → keep_with_prev=False
#   空白分隔符（"\n\n"）被上一段吃掉，避免下一段顶部出现空行 → keep_with_prev=True
SPLIT_MARKERS = [
    ("持仓明细", False),
    ("📊 组合温度计", False),
    ("📈 【股票推荐", False),
    ("💰 【基金推荐", False),
    ("\n\n", True),
]


def byte_len(text: str) -> int:
    """UTF-8 字节长度。所有与通道上限的比较都必须走这个函数。"""
    return len((text or "").encode("utf-8"))


def is_configured() -> bool:
    """检查企业微信是否已配置（**延迟读取**，受环境变量/monkeypatch 控制）"""
    return bool(_corp_id() and _secret() and _agent_id())


def _get_token() -> str:
    """获取/刷新 access_token"""
    cached = _token_cache.get("token")
    if cached is not None:
        return cached

    if not is_configured():
        return ""

    try:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={_corp_id()}&corpsecret={_secret()}"
        resp = _http_client.get(url)
        data = resp.json()
        if data.get("errcode") == 0:
            token = data["access_token"]
            _token_cache.set("token", token, ttl=7000)  # 提前 200 秒刷新
            return token
        else:
            print(f"[WXWORK] Token error: {data}")
    except Exception as e:
        print(f"[WXWORK] Token failed: {e}")
    return ""


def send_text(content: str, user_id: str = "") -> dict:
    """发送文本消息（单条直发，企微 text 上限 2048 字节；超了会被硬截断）

    ⚠️ 这个函数【不做】任何长度保护——超长内容会被企微静默截断。
       需要长度保护的长文本请改用 send_markdown（按字节无损分段）；
       证据链类、宁可截断也不能改内容的请改用 send_text_capped。

    返回: {"ok": bool, "data": dict, "skipped_81013": bool}
    """
    return _send_raw(content, user_id=user_id, markdown=False)


def _send_raw(content: str, user_id: str = "", markdown: bool = False) -> dict:
    """底层单条发送（含统一 81013 处理 + 失败自动重试 1 次）

    Args:
        content: 消息正文
        user_id: 目标用户
        markdown: True → msgtype="markdown"（上限 4096 字节）；
                  False → msgtype="text"（上限 2048 字节）

    返回: {"ok": bool, "data": dict, "skipped_81013": bool}
    """
    token = _get_token()
    if not token:
        return {"ok": False, "error": "未配置或获取 token 失败"}

    target = user_id or _default_user_id()
    if markdown:
        payload = {
            "touser": target,
            "msgtype": "markdown",
            "agentid": int(_agent_id()),
            "markdown": {"content": content},
        }
    else:
        payload = {
            "touser": target,
            "msgtype": "text",
            "agentid": int(_agent_id()),
            "text": {"content": content},
        }

    def _do_send():
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        resp = _http_client.post(url, json=payload)
        return resp.json()

    for attempt in range(2):  # FIX: 失败自动重试 1 次
        try:
            data = _do_send()
            errcode = data.get("errcode", -1)

            # FIX: 统一 81013 处理 — 无效用户标记跳过，不再每次报错
            if errcode == 81013:
                if target not in _81013_warned:
                    _81013_warned.add(target)
                    print(f"[WXWORK] ⏭️ 用户 {target} 无效(81013)，后续静默跳过")
                return {"ok": False, "error": "81013_invalid_user", "skipped_81013": True, "data": data}

            # token 过期自动刷新重试
            if errcode == 42001 or errcode == 40014:
                _token_cache.delete("token")
                token = _get_token()
                if token and attempt == 0:
                    payload["agentid"] = int(_agent_id())  # refresh payload
                    continue
                return {"ok": False, "error": "token_expired", "data": data}

            ok = errcode == 0
            if not ok:
                print(f"[WXWORK] Send error: {data}")
            return {"ok": ok, "data": data}
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            if attempt == 0:
                print(f"[WXWORK] 网络错误，2s后重试: {e}")
                time.sleep(2)
                continue
            print(f"[WXWORK] Send failed (重试后): {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            print(f"[WXWORK] Send failed: {e}")
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "max_retries_exceeded"}


def _to_wecom_markdown(text: str) -> str:
    """整理成企微 markdown 支持的子集。

    原则：**不确定的语法宁可不转**。只做两件有把握的事，其余全部原样透传：
      1) 删掉代码围栏行和反引号、保留其中文字 —— 企微 markdown 没有代码块，
         反引号会原样显示成噪音（旧实现直接删掉整段 code，那是内容丢失）；
      2) 删掉落单的 '**' —— 企微 markdown 遇到未闭合的粗体标记会吞掉后面整段，
         这是唯一已知会「静默吞内容」的语法风险，必须消除。
    不做的事：不转表格、不转图片、不加粗、不生成标题 —— 都不确定，保持原样。
    """
    out = text or ""
    # 1) 代码围栏行 ```lang ... ``` → 去掉围栏行，保留代码正文
    out = re.sub(r"(?m)^[ \t]*```.*$", "", out)
    # 行内反引号 → 保留内容，去掉符号
    out = out.replace("`", "")
    # 2) 落单的 ** ：只删最后一个前面再也找不到配对的 **
    if out.count("**") % 2:
        out = re.sub(r"\*\*(?![\s\S]*\*\*)", "", out, count=1)
    return out


def send_markdown(content: str, user_id: str = "") -> dict:
    """发送消息（**默认走 text 通道**，超长按字节无损分段）

    历史沿革，务必知悉：
      - 旧实现：把 markdown 剥成纯文本走 text 通道（上限 2048 字节），
        2026-09-11 晨报 2224 字节单条直发被硬截断，丢掉【操作建议】和免责声明。
      - v9.9.20 (B4)：改走真正的 markdown 通道，上限翻倍到 4096。
        但 2026-09-12 实测用户收到「暂不支持此消息类型，请在企业微信中查看」
        —— markdown 在部分接收端不渲染，用户什么都读不到。**比截断更糟。**
      - v9.9.20 (B4 回滚)：**默认改回 text 通道**。长度问题交给 B1~B3 的
        按字节无损分段（预算 1800 字节 < 2048 限制，超长自动切多条），
        不依赖 markdown 通道。想用 markdown 需显式设 WXWORK_FORCE_MARKDOWN=1。

    ⛔ 2026-09-17 第二次实测：markdown 通道**依然不可用，已死心。**
      这次不是历史传闻，是带着完整证据链做的真机验证：
        - 方式：生产服务器单次注入 WXWORK_FORCE_MARKDOWN=1，
          用生产真实路径 send_daily_report_to() 发真实晨报内容（3727 字节）；
        - 企微 API 返回 **errcode=0 / errmsg=ok**（服务端完全接受，msgid 正常下发）；
        - 但用户手机收到的仍是「暂不支持此消息类型，请在企业微信中查看」。
      **关键教训：API 返回 ok ≠ 用户能看到。** 服务端接受只说明消息格式合法，
      不代表接收端支持渲染。下次想验证 markdown 能不能用，唯一的判据是
      **人在手机上看一眼**，任何服务端返回值都不能作为依据。
      距 2026-09-12 首次失败已 5 个月，接收端行为毫无变化 —— 别再试第三次，
      把精力放在砍内容或接受多条上。

    返回: {"ok": bool, "data": dict, "skipped_81013": bool}
    """
    body = _to_wecom_markdown(content).strip()
    if not body:
        return {"ok": False, "error": "empty_content"}

    total = byte_len(body)
    _length_guard(total, source="send_markdown", user_id=user_id)

    # 运维逃生开关：markdown 渲染出问题时可一键退回旧的纯文本通道，无需改代码
    if _force_text():
        # 默认路径（2026-09-12 起）：text 通道 + 按字节分段，预算 1800 < 2048 上限。
        print(f"[WXWORK] 走纯文本通道（{total} 字节，预算 {TEXT_CHUNK_BUDGET}）")
        return _send_chunked(body, user_id=user_id,
                             budget=TEXT_CHUNK_BUDGET, markdown=False)

    if total <= MARKDOWN_CHUNK_BUDGET:
        return _send_raw(body, user_id=user_id, markdown=True)

    chunks = _split_message(body, MARKDOWN_CHUNK_BUDGET)
    print(f"[WXWORK] 内容 {total} 字节 > {MARKDOWN_CHUNK_BUDGET}，"
          f"按字节无损分成 {len(chunks)} 段推送")
    return _send_chunked(body, user_id=user_id,
                         budget=MARKDOWN_CHUNK_BUDGET, markdown=True)


def _send_chunked(body: str, user_id: str = "", budget: int = TEXT_CHUNK_BUDGET,
                  markdown: bool = False) -> dict:
    """按字节分段逐条发送，段间间隔 0.5 秒防限流。

    分段本身是无损的（见 _split_message），所以这里不丢任何内容。
    """
    chunks = _split_message(body, budget)
    last_result: dict = {}
    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(0.5)  # 防限流
        tag = f"({i+1}/{len(chunks)})" if len(chunks) > 1 else ""
        last_result = _send_raw(f"{chunk}\n{tag}" if tag else chunk,
                                user_id=user_id, markdown=markdown)
    return last_result


def _byte_prefix(text: str) -> list:
    """返回 cum[i] = text[:i] 的 UTF-8 字节数，长度 len(text)+1，单调不减。

    有了它就能用 bisect 把「字节预算」换算成「字符下标」，且切点必然落在
    字符边界上 —— 绝不会把一个多字节汉字/emoji 切成两半。
    """
    cum = [0] * (len(text) + 1)
    total = 0
    cache: dict = {}
    for i, ch in enumerate(text):
        n = cache.get(ch)
        if n is None:
            n = len(ch.encode("utf-8"))
            cache[ch] = n
        total += n
        cum[i + 1] = total
    return cum


def _find_cut(text: str, cum: list, start: int, limit_bytes: int, budget: int) -> int:
    """在 (start, hi] 内挑最佳切分点，返回绝对字符下标（下标左侧归入上一段）。

    limit_bytes 是「绝对」字节上限（= cum[start] + budget）。
    """
    # 最大 i 使 cum[i] <= limit_bytes
    hi = bisect.bisect_right(cum, limit_bytes) - 1
    if hi <= start:
        return start + 1  # 兜底：至少推进一个字符，防死循环

    # ── 最少条数约束（2026-09-17 修复）─────────────────────────────
    # 旧逻辑只要求「本段至少装 30% 预算」，于是一个落在 1520B 的「整齐」标记
    # （持仓明细 / 组合温度计）会被优先采用 —— 但切完之后剩余 2XXXB 仍 > 预算，
    # 被迫再切一刀，**为了切得整齐反而多切出一条**。
    #
    # 教训（2026-09-17 LeiJiang 晨报 body 3580B）：本该 ceil(3580/1800)=2 条，
    # 却因标记优先切在 1302B，硬生生变成 3 条，第 3 条还把「持仓速览」一分为二。
    #
    # 现在把「最少条数」写进门槛：若本段只装 L 字节，剩余要切 ceil((R-L)/budget)
    # 段，总数 = 1 + 那一段数。要让它不超过理论最小 k = ceil(R/budget)，本段
    # 至少要装 floor_bytes = R - (k-1)*budget。低于此值的标记一律跳过，切点自动
    # 落到预算内最后一个空行（既整齐又不牺牲条数）。
    remaining = cum[-1] - cum[start]           # 从本段起点算起的剩余字节
    k = math.ceil(remaining / budget)          # 理论最少条数
    floor_bytes = remaining - (k - 1) * budget  # 保证「条数最少」时第 1 段的最小长度
    min_bytes = cum[start] + max(int(budget * 0.3), floor_bytes)

    for marker, keep_with_prev in SPLIT_MARKERS:
        pos = text.rfind(marker, start, hi)
        if pos <= start or cum[pos] < min_bytes:
            continue
        cut = pos + len(marker) if keep_with_prev else pos
        cut = min(cut, hi)
        if cut > start:
            return cut

    # 退而求其次：最近的换行（换行归入上一段，下一段顶部不留空行）
    pos = text.rfind("\n", start, hi)
    if pos > start and cum[pos] >= min_bytes:
        return min(pos + 1, hi)

    return hi


def _split_message(text: str, max_bytes: int = TEXT_CHUNK_BUDGET) -> list:
    """按 UTF-8 字节预算智能分割长消息。

    与旧的按字符分割相比有两个硬保证：
      ① 每段真实字节数 <= max_bytes（企微按字节限流，按字符会低估约 2.4 倍）；
      ② **无损**："".join(chunks) == text，一个字符都不会丢。
         实现方式是「只在切点挪位置、绝不在切点 rstrip/lstrip」，
         旧实现的 rstrip()+lstrip() 会吃掉段落间的换行。

    Args:
        text: 待分割文本
        max_bytes: 单段 UTF-8 字节预算

    Returns:
        list[str]: 分段列表（顺序拼接后与原文完全相同）
    """
    if max_bytes <= 0:
        raise ValueError(f"max_bytes 必须为正数，实际 {max_bytes}")

    total = byte_len(text)
    if total <= max_bytes:
        return [text]

    cum = _byte_prefix(text)
    chunks: list = []
    start = 0
    while total - cum[start] > max_bytes:
        cut = _find_cut(text, cum, start, cum[start] + max_bytes, max_bytes)
        if cut <= start:  # 双保险，任何情况下都不得死循环
            cut = start + 1
        chunks.append(text[start:cut])
        start = cut
    if start < len(text):
        chunks.append(text[start:])
    return chunks


def _truncate_bytes(text: str, limit: int) -> str:
    """按字节安全截断，绝不切在 UTF-8 多字节字符中间。"""
    if byte_len(text) <= limit:
        return text
    cum = _byte_prefix(text)
    return text[:max(bisect.bisect_right(cum, limit) - 1, 0)]


def _force_text() -> bool:
    """是否走纯文本通道（**默认 True**，2026-09-12 反转）。

    B4 曾经把 send_markdown 改成走真正的 markdown 通道，图的是上限从 2048
    翻倍到 4096 字节。但 2026-09-12 01:0x 用户实测收到「暂不支持此消息类型，
    请在企业微信中查看」——markdown 通道在部分接收端（微信侧转发 / 旧版客户端）
    根本渲染不出来，用户等于什么都读不到。**读不到比被截断严重得多。**

    而长度问题 B1~B3 已经用「按字节无损分段」解决了（text 通道单条 2048 字节，
    分段预算 1800，超长自动切多条），根本不需要靠 markdown 通道的 4096 上限。
    所以默认改回 text：稳妥压倒一切。

    显式设置 WXWORK_FORCE_MARKDOWN=1 才会走 markdown（保留能力，不删代码）。

    ⛔ 警告：这个开关**当前不可用**，设了会让用户什么都收不到。
    2026-09-12 首次实测失败、2026-09-17 带着完整证据链二次实测仍失败
    （详见 send_markdown 的 docstring）。两次间隔 5 个月，接收端行为未变。
    保留它只是为了「将来企微支持了能立刻打开」，**不是让你现在去设**。
    """
    return os.getenv("WXWORK_FORCE_MARKDOWN", "").strip().lower() not in ("1", "true", "yes")


def effective_channel() -> tuple:
    """当前**实际生效**的通道：(channel_name, channel_limit, chunk_budget)。

    告警文案 / 落盘事件都必须用这里的数字，绝不能写死 markdown 的 4096：
    生产默认走 text 通道（上限 2048、分段预算 1800），写 4096 等于把风险基准
    抬高整整一倍 —— 实测 3729 字节会被报成「距通道上限 4096 还剩 367 字节」，
    可对 text 通道来说它早已超上限、必然切成 3 条，运维照这个数判断会完全错。
    """
    if _force_text():
        return ("text", WECOM_TEXT_LIMIT, TEXT_CHUNK_BUDGET)
    return ("markdown", WECOM_MARKDOWN_LIMIT, MARKDOWN_CHUNK_BUDGET)


def _record_event(event: dict) -> None:
    """把长度/截断事件追加写入 JSONL，供 daily_push_quality_check 复盘。

    写入失败绝不影响推送主流程（静默降级为只打日志）。
    """
    try:
        from config import PUSH_ARCHIVE_DIR
        record = dict(event)
        record.setdefault("ts", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        path = Path(PUSH_ARCHIVE_DIR) / _LENGTH_EVENT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[WXWORK] 长度事件记录失败（不影响推送）：{e}")


def _length_guard(total_bytes: int, source: str = "", user_id: str = "") -> str:
    """B5 长度护栏：**只告警 + 留记录，绝不静默砍内容**。

    分级：
      ok    <= 3600 字节            —— 正常
      warn  3600 ~ 3900 字节        —— 单条仍装得下，但已进预警区，落一条事件
      split >  3900 字节            —— 会走无损分段；分段不丢内容，所以不裁剪

    ⚠️ 这里刻意**不接**自动精简：分片是无损的，砍内容才是纯损失。
       护栏的职责是「让人知道快撞线了」，不是「替人决定删什么」。

    Returns:
        str: "ok" | "warn" | "split"
    """
    if total_bytes <= LENGTH_ALERT_BYTES:
        return "ok"

    level = "warn" if total_bytes <= MARKDOWN_CHUNK_BUDGET else "split"

    # 基准必须是**实际生效**的通道，不能写死 markdown 的 4096：
    # 生产默认 text（上限 2048），3729 字节早就超上限了，报「还剩 367」纯属误导。
    channel, channel_limit, chunk_budget = effective_channel()
    if total_bytes <= channel_limit:
        headroom = (f"距 {channel} 通道上限 {channel_limit} 还剩 "
                    f"{channel_limit - total_bytes} 字节")
    else:
        headroom = (f"已超 {channel} 通道上限 {channel_limit} 字节 "
                    f"{total_bytes - channel_limit} 字节")

    # text 通道下「会分成几条」才是运维真正需要的信号：内容不丢，但用户收到多条。
    # ceil 是下界（断点优先 + "(i/N)" 标记还会再占字节），所以写 "≥"。
    split_note = ""
    if channel == "text":
        parts = math.ceil(total_bytes / chunk_budget)
        split_note = (f"将按 {chunk_budget} 字节预算无损拆分为 ≥{parts} 条发送"
                      f"（内容不丢，但用户会收到多条）；")

    print(f"[WXWORK] ⚠️ 推送长度告警（{source}）：{total_bytes} 字节 > 告警线 "
          f"{LENGTH_ALERT_BYTES} 字节，处理={level}；{headroom}；"
          f"{split_note}内容不做任何裁剪")
    _record_event({
        "kind": "length_alert",
        "level": level,
        "source": source,
        "user_id": user_id,
        "bytes": total_bytes,
        "alert_line": LENGTH_ALERT_BYTES,
        "channel_limit": channel_limit,
    })
    return level


def send_text_capped(content: str, user_id: str = "", source: str = "") -> dict:
    """B3：证据链类推送 —— **宁可被企微截断，也绝不改写内容**，但必须留下截断记录。

    适用：幻觉自检告警等「原文即证据」的场景。内容一旦被自动精简/分段改写，
    就无法复现问题现场，所以这里唯一的让步是：截断时落一条事件，
    让人明确知道这条消息是断的、断了什么。

    普通业务推送不要用这个，用 send_markdown（无损分段）。
    """
    body = content or ""
    total = byte_len(body)
    if total <= WECOM_TEXT_LIMIT:
        return send_text(body, user_id=user_id)

    kept = _truncate_bytes(body, WECOM_TEXT_LIMIT)
    print(f"[WXWORK] ✂️ 证据链推送被截断（{source}）：{total} → {WECOM_TEXT_LIMIT} 字节，"
          f"丢弃 {total - WECOM_TEXT_LIMIT} 字节；内容未做任何改写，仅做字节安全截断")
    _record_event({
        "kind": "truncated",
        "source": source,
        "user_id": user_id,
        "orig_bytes": total,
        "kept_bytes": byte_len(kept),
        "limit_bytes": WECOM_TEXT_LIMIT,
        "dropped_bytes": total - WECOM_TEXT_LIMIT,
        "lost_tail": body[len(kept):][:200],
    })
    return send_text(kept, user_id=user_id)


def send_stock_alert(signals: list) -> dict:
    """发送股票异动预警（格式化为 Markdown）"""
    if not signals:
        return {"ok": True, "msg": "无异动"}

    lines = ["**🚨 钱袋子盯盘预警**\n"]
    for sig in signals[:10]:  # 最多 10 条
        emoji = "🔴" if sig.get("level") == "warning" else "🟡"
        lines.append(f"{emoji} **{sig.get('name', '')}**({sig.get('code', '')})")
        # v9.9.10: 兼容 message / msg 两种契约。基金侧 alert 用 message，
        # 股票侧用 msg；旧实现只读 message，会让股票侧异动渲染成空行。
        lines.append(f"> {sig.get('message') or sig.get('msg') or '（无详情）'}\n")

    lines.append(f"⏰ {time.strftime('%H:%M:%S')}")
    content = "\n".join(lines)
    return send_markdown(content)


def send_daily_report(report: str, title: str = "📊 钱袋子每日复盘") -> dict:
    """发送每日复盘报告"""
    content = f"**{title}**\n\n{report}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}"
    return send_markdown(content)


# ---- 按用户推送（cron 多用户场景）----

def send_stock_alert_to(wxwork_userid: str, signals: list) -> dict:
    """发送股票异动预警给指定用户"""
    if not signals:
        return {"ok": True, "msg": "无异动"}

    # 补全基金/股票名称（alert 里 name 可能为空）
    def _get_display_name(sig):
        name = sig.get('name', '')
        code = sig.get('code', '')
        if name:
            return name
        # 尝试从基金名称表补全
        try:
            from services.fund_monitor import _get_fund_name
            n = _get_fund_name(code)
            if n and n != code:
                return n
        except Exception:
            pass
        return code  # 最终降级用代码

    lines = ["**🚨 钱袋子盯盘预警**\n"]
    for sig in signals[:10]:
        emoji = "🔴" if sig.get("level") == "warning" else "🟡"
        display_name = _get_display_name(sig)
        code = sig.get('code', '')
        lines.append(f"{emoji} **{display_name}**（{code}）")
        lines.append(f"> {sig.get('message', sig.get('msg', ''))}\n")
    lines.append(f"⏰ {time.strftime('%H:%M:%S')}")
    content = "\n".join(lines)
    return send_markdown(content, user_id=wxwork_userid)


def send_daily_report_to(wxwork_userid: str, report: str, title: str = "📊 钱袋子每日复盘") -> dict:
    """发送报告给指定用户（title 可自定义，默认每日复盘）
    
    v9.5.123: 不再截断，send_markdown 会自动分段推送长消息
    v9.7.0: 移除多余的 ** 清理（send_markdown 内部已统一处理）
    """
    if title:
        content = f"{title}\n\n{report}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}"
    else:
        content = f"{report}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}"
    # v9.5.123: 不再硬截断,send_markdown会自动分段推送
    return send_markdown(content, user_id=wxwork_userid)


# ============================================================
# 回调验证（企业微信 URL 验证 + 消息接收）
# ============================================================

import hashlib
import base64
import struct
import socket
from Crypto.Cipher import AES

_CALLBACK_TOKEN = os.getenv("WXWORK_CALLBACK_TOKEN", "")
if not _CALLBACK_TOKEN:
    # 安全提醒：回调 Token 未配置，消息验证将失败
    # 生产环境务必设置 WXWORK_CALLBACK_TOKEN 环境变量
    print("[WXWORK] ⚠️ WXWORK_CALLBACK_TOKEN 未配置，回调验证不可用")
_CALLBACK_AES_KEY = os.getenv("WXWORK_CALLBACK_AES_KEY", "")


def _decode_aes_key(encoding_aes_key: str) -> bytes:
    return base64.b64decode(encoding_aes_key + "=")


def _verify_signature(token: str, timestamp: str, nonce: str, echostr: str, signature: str) -> bool:
    """验证企微回调签名"""
    sort_list = sorted([token, timestamp, nonce, echostr])
    sha1 = hashlib.sha1("".join(sort_list).encode()).hexdigest()
    return sha1 == signature


def _decrypt_echostr(aes_key: bytes, echostr: str) -> str:
    """AES 解密 echostr 并返回明文"""
    try:
        cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
        decrypted = cipher.decrypt(base64.b64decode(echostr))
        # 去 PKCS7 padding
        pad = decrypted[-1]
        content = decrypted[:-pad]
        # 格式: 16字节随机 + 4字节内容长度 + 内容 + corpid
        xml_len = struct.unpack("!I", content[16:20])[0]
        xml_content = content[20:20 + xml_len].decode("utf-8")
        return xml_content
    except Exception as e:
        print(f"[WXWORK] Decrypt error: {e}")
        return ""


def verify_callback(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """处理企微 URL 验证回调，返回解密后的 echostr（明文）"""
    if not _CALLBACK_AES_KEY:
        print("[WXWORK] No AES key configured")
        return ""

    if not _verify_signature(_CALLBACK_TOKEN, timestamp, nonce, echostr, msg_signature):
        print(f"[WXWORK] Signature verification failed")
        return ""  # 修复：签名验证失败必须 return，不能继续执行

    aes_key = _decode_aes_key(_CALLBACK_AES_KEY)
    result = _decrypt_echostr(aes_key, echostr)
    if result:
        print(f"[WXWORK] Callback verify OK")
    return result


def decrypt_message(msg_signature: str, timestamp: str, nonce: str, xml_body: str) -> dict:
    """解密企微推送的消息，返回 {from_user, content, msg_type}"""
    import xml.etree.ElementTree as ET
    if not _CALLBACK_AES_KEY:
        return {}
    try:
        root = ET.fromstring(xml_body)
        encrypt_node = root.find("Encrypt")
        if encrypt_node is None:
            return {}
        encrypted = encrypt_node.text

        # 验签（修复：验证失败则拒绝解密，防止伪造消息触发 LLM）
        if not _verify_signature(_CALLBACK_TOKEN, timestamp, nonce, encrypted, msg_signature):
            print(f"[WXWORK] Message signature verification failed — dropping")
            return {}

        # AES 解密
        aes_key = _decode_aes_key(_CALLBACK_AES_KEY)
        decrypted = _decrypt_echostr(aes_key, encrypted)
        if not decrypted:
            return {}

        # 解析明文 XML
        msg_root = ET.fromstring(decrypted)
        return {
            "from_user": msg_root.findtext("FromUserName", ""),
            "content": msg_root.findtext("Content", "").strip(),
            "msg_type": msg_root.findtext("MsgType", "text"),
            "msg_id": msg_root.findtext("MsgId", ""),
            "create_time": msg_root.findtext("CreateTime", ""),
        }
    except Exception as e:
        print(f"[WXWORK] Decrypt message error: {e}")
        return {}


def encrypt_reply(reply_text: str, to_user: str, nonce: str) -> str:
    """加密回复消息为企微要求的 XML 格式"""
    import xml.etree.ElementTree as ET
    import random
    import string

    if not _CALLBACK_AES_KEY:
        return ""
    try:
        aes_key = _decode_aes_key(_CALLBACK_AES_KEY)
        corp_id = _corp_id() or ""

        # 构造明文: 16字节随机 + 4字节长度 + 内容 + corpid
        reply_bytes = reply_text.encode("utf-8")
        random_bytes = ''.join(random.choices(string.ascii_letters + string.digits, k=16)).encode()
        content = random_bytes + struct.pack("!I", len(reply_bytes)) + reply_bytes + corp_id.encode()

        # PKCS7 padding
        pad_len = 32 - (len(content) % 32)
        content += bytes([pad_len] * pad_len)

        # AES CBC 加密
        cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
        encrypted = base64.b64encode(cipher.encrypt(content)).decode()

        # 生成签名
        timestamp = str(int(time.time()))
        sign_list = sorted([_CALLBACK_TOKEN, timestamp, nonce, encrypted])
        signature = hashlib.sha1("".join(sign_list).encode()).hexdigest()

        # 构造 XML
        xml = f"""<xml>
<Encrypt><![CDATA[{encrypted}]]></Encrypt>
<MsgSignature><![CDATA[{signature}]]></MsgSignature>
<TimeStamp>{timestamp}</TimeStamp>
<Nonce><![CDATA[{nonce}]]></Nonce>
</xml>"""
        return xml
    except Exception as e:
        print(f"[WXWORK] Encrypt reply error: {e}")
        return ""


def archive_push(user_id: str, push_type: str, content: str, timestamp: str = None):
    """
    存档推送内容到本地文件（用于后续质量评估）
    
    Args:
        user_id: 用户ID（如 "LeiJiang"）
        push_type: 推送类型（"briefing"/"closing_review"/"alert"）
        content: 完整推送内容
        timestamp: 时间戳（可选，默认当前时间）
    """
    try:
        from config import PUSH_ARCHIVE_DIR
        import datetime
        
        # 生成文件名：YYYY-MM-DD_type_user.txt
        if timestamp is None:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}_{push_type}_{user_id}.txt"
        filepath = PUSH_ARCHIVE_DIR / filename
        
        # 写入文件（追加模式，同类型多条推送都保存）
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"=== {timestamp} ===\n")
            f.write(content)
            f.write("\n\n")
        
        print(f"  [存档] {push_type} 已存档到 {filename}")
        
    except Exception as e:
        print(f"  [存档] 失败：{e}")
