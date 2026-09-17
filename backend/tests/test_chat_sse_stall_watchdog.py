"""对话页 SSE 超时回归测试：流式读取阶段必须有「不活跃」看门狗。

## 缺陷（pages/chat.js，`sendChat` 里的 SSE 请求）

旧实现::

    // SSE 流式请求不能整体 timeout（会掐断长输出），只用 AbortController 限制「连接/首字节」阶段：
    // 20s 内没收到响应头就 abort；一旦拿到响应头即清除定时器，进入流式读取阶段不再超时。
    const _ctl = new AbortController();
    const _tmo = setTimeout(() => _ctl.abort(), 20000);
    const r = await fetch(API_BASE + '/chat/stream', {..., signal: _ctl.signal});
    clearTimeout(_tmo);          // ← 超时保护到此为止

一个**一次性**定时器被绑在了错误的相位边界上，同时犯了两个方向的错：

1. **该断不断（主，且是「SSE 超时」这条线索的真身）**
   `clearTimeout(_tmo)` 之后，流式读取是裸奔的::

       while (true) { const {done, value} = await reader.read(); if (done) break; ... }

   `reader.read()` 只要后端不 close 连接就永不 settle。后端中途卡死时
   （LLM provider 挂起 / FC agent 同步阻塞 event loop / TCP 半开 / 反向代理静默丢流），
   `sendChat` 永远不返回，于是：

   * 界面一直在转圈（`▊` 光标常驻）；
   * 输入框被 `_setChatLock(true)` **永久锁定**，用户连重发都做不到；
   * 代码块里那套 `_streamTimeout → '请求超时，请重试。'` 反馈分支，
     因为 `_ctl.abort()` 再也不会被调用，成了**死代码** —— 用户拿不到任何解释。

2. **误判断开（次）**
   20s 是**到响应头**的预算，而 `StreamingResponse` 的响应头要等路由函数
   `chat_analysis_stream` 把同步阻塞的活干完才发得出去（`_build_market_context` /
   `_build_portfolio_context` / `classify_chat_intent` / 联网搜索 / 新闻注入 / 记忆注入）。
   慢但健康的请求会在 20s 被 abort 成「请求超时」—— 而同一个文件自己的
   思考进度提示写着「R1 深度思考需要 **15-30 秒**」，预算比承诺还短。

## 修法（v9.9.50）：把「一次性定时器」换成「不活跃看门狗」

`_createStallWatchdog(abortFn, firstByteMs, chunkGapMs)` —— 整条请求生命周期共用一把
定时器，**每收到一个网络 chunk 就 kick 一下重新计时**，首字节前后用不同预算：

* 首字节前 `_CHAT_FIRST_BYTE_MS`（60s）：容得下后端预处理 + LLM 首 token，
  长思考不再被误杀；
* 首字节后 `_CHAT_CHUNK_GAP_MS`（45s）：只要后端还在吐数据就永不超时，
  一静默超过预算就 abort —— 卡死终于有界了。

副作用修复：中断时保留用户已经看到的那部分正文（旧实现整段丢掉，只留一句
「请求超时」），并清掉残留的 `▊` 光标。

## 断言类型纪律

* **源码级结构断言**：在源码文本里匹配结构，不执行 JS —— 读作「代码文本如此」。
* **行为级断言**：用 node + vm **真加载并执行** `pages/chat.js`
  （最小 DOM / fetch 桩），断言真实运行结果。环境无 node 时 `pytest.skip`
  （不静默变绿）。
* **未覆盖**：真机/浏览器视觉复验、真实 LLM provider 挂起。本文件不跑浏览器。
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
CHAT_JS = BACKEND_DIR.parent / "pages" / "chat.js"

_NODE = shutil.which("node")
_API_BASE = "http://api.local/api"


def _src() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


# ==========================================================================
# 源码级结构断言（低成本护栏）
# ==========================================================================
def test_old_one_shot_header_timeout_is_gone():
    """旧的「20s 一次性、拿到响应头就撤销」模式必须已经消失。（源码级结构断言）

    这是本轮缺陷的**根因表达**：`_tmo` 这个一次性定时器就是「流式阶段裸奔」的载体。
    """
    src = _src()
    assert "_tmo" not in src, "一次性首字节定时器 _tmo 回来了 —— 流式阶段又裸奔了"
    assert "abort(),20000" not in src, "20s 硬编码超时回来了"


def test_watchdog_helper_exists_and_is_used():
    """`_createStallWatchdog` 必须存在，且被 sendChat 真正创建+kick。（源码级结构断言）"""
    src = _src()
    assert "function _createStallWatchdog(" in src, "找不到 _createStallWatchdog"
    assert "_createStallWatchdog(" in src.split("function _createStallWatchdog(")[1], (
        "_createStallWatchdog 定义了但没人调用（死代码）"
    )
    assert re.search(r"_wd\s*=\s*_createStallWatchdog\(", src), "sendChat 没有创建看门狗"
    assert "_wd.kick(false)" in src, "首字节阶段没有挂看门狗"


def test_streaming_read_loop_kicks_watchdog():
    """★核心断言：流式读取循环里每收到一个 chunk 都必须 kick。（源码级结构断言）

    旧实现恰恰是在这里没有保护。锚定到 `reader.read()` 之后，防止只在别处
    kick 一次来骗过 `'kick' in src`。
    """
    src = _src()
    m = re.search(
        r"while\(true\)\{const\{done,value\}=await reader\.read\(\);if\(done\)break;", src
    )
    assert m, "找不到 SSE 读取循环（结构变了，本断言需要同步更新）"
    tail = src[m.end(): m.end() + 400]
    assert "_wd.kick(true)" in tail, (
        "流式读取循环收到 chunk 后没有 kick 看门狗 —— 后端卡死时 reader.read() "
        "永不 settle，界面会永久转圈且输入永久锁定"
    )


def test_watchdog_is_disarmed_on_all_exit_paths():
    """请求正常结束（成功 / 失败）都要撤销看门狗，否则会误 abort 下一次请求。"""
    src = _src()
    assert "if(_wd)_wd.stop(); // 请求已完整结束" in src, "try 正常结束路径没撤销看门狗"
    assert src.count("_wd.stop()") >= 4, (
        f"_wd.stop() 只出现 {src.count('_wd.stop()')} 次 —— 存在未撤销的退出路径"
    )


def test_first_byte_budget_is_generous_enough_for_deep_thinking():
    """首字节预算必须容得下后端预处理 + 深度思考。（源码级结构断言）

    聊天页自己的提示语写着「R1 深度思考需要 15-30 秒」，预算比它短就是自相矛盾。
    """
    src = _src()
    m = re.search(r"_CHAT_FIRST_BYTE_MS\s*=\s*(\d+)", src)
    assert m, "找不到 _CHAT_FIRST_BYTE_MS 预算常量"
    assert int(m.group(1)) >= 30000, (
        f"首字节预算 {m.group(1)}ms 短于深度思考（15-30s），会把健康请求误杀"
    )
    m2 = re.search(r"_CHAT_CHUNK_GAP_MS\s*=\s*(\d+)", src)
    assert m2, "找不到 _CHAT_CHUNK_GAP_MS 预算常量"
    assert int(m2.group(1)) > 0, "流式阶段 chunk 间隔预算为 0 —— 等于没有看门狗"


def test_stalled_stream_preserves_partial_output():
    """中断前已吐出的正文不能被整段丢掉。（源码级结构断言）"""
    src = _src()
    assert "_partialText" in src, "没有保留中断前的部分正文"
    assert "⚠️ 响应中断，以上为已生成内容" in src, "中断时没有给用户「以上为已生成内容」的标记"
    assert "stream-cursor" in src and "c.remove()" in src, "失败后没有清掉残留的流式光标"


# ==========================================================================
# Node 行为级执行
# ==========================================================================
_MAKE_EL = (
    "function __mkEl(){return{style:{},classList:{add(){},remove(){}},"
    "set onclick(f){},set innerHTML(v){this._h=v;},get innerHTML(){return this._h||'';},"
    "appendChild(){},remove(){},querySelectorAll:()=>[],querySelector:()=>null,"
    "getAttribute:()=>null,setAttribute(){},value:'',disabled:false,"
    "scrollTop:0,scrollHeight:0};}"
)
_DOC = (
    "const __el=__mkEl();"
    "const __document={createElement:()=>__mkEl(),body:{appendChild(){}},"
    "getElementById:()=>__el,querySelector:()=>null,querySelectorAll:()=>[]};"
)


def _load_chat_js(first_byte_ms: int, chunk_gap_ms: int) -> str:
    """返回加载 chat.js 的前置脚本（预算常量被替换成可调的小值）。

    只替换**两个调参常量**本身，看门狗逻辑一行不动 —— 断言的仍是真实实现。
    """
    src = _src()
    patched = src.replace(
        "_CHAT_FIRST_BYTE_MS=60000", f"_CHAT_FIRST_BYTE_MS={first_byte_ms}"
    ).replace(
        "_CHAT_CHUNK_GAP_MS=45000", f"_CHAT_CHUNK_GAP_MS={chunk_gap_ms}"
    )
    assert f"_CHAT_FIRST_BYTE_MS={first_byte_ms}" in patched, "预算常量替换失败（源码结构变了）"
    assert f"_CHAT_CHUNK_GAP_MS={chunk_gap_ms}" in patched, "预算常量替换失败（源码结构变了）"
    return (
        "const fs=require('fs');const vm=require('vm');"
        "const SRC=" + json.dumps(patched) + ";"
        + _MAKE_EL + _DOC
        + "const __sandbox={"
        "API_BASE:" + json.dumps(_API_BASE) + ","
        # ---- 以下 4 项在真实运行时由 app.js 提供（chat.js 只消费）----
        "API_AVAILABLE:true,"
        "chatMessages:[],"
        "_saveChatHistory:()=>{},"
        "_uk:(b)=>b,"
        "getProfileId:()=>'LeiJiang',getUserId:()=>'LeiJiang',"
        "AbortController,AbortSignal,TextDecoder,JSON,Math,String,Array,Object,RegExp,"
        "Error,isNaN,parseInt,parseFloat,encodeURIComponent,decodeURIComponent,Date,"
        "console,setTimeout,clearTimeout,setInterval,clearInterval,"
        "localStorage:{getItem:()=>null,setItem(){},removeItem(){}},"
        "fetch:(u,o)=>__fetchImpl(u,o),"
        "document:__document"
        "};"
        "__sandbox.window=__sandbox;__sandbox.globalThis=__sandbox;"
        "vm.createContext(__sandbox);"
        "vm.runInContext(SRC,__sandbox,{filename:'chat.js'});"
    )


def _run_node(script: str, timeout: int = 60) -> str:
    """执行一段 JS，返回 stdout。

    走**临时文件**而不是 `node -e`：chat.js 全文内联进 `-e` 参数时会撞上
    Node 22 对 eval 代码的 TypeScript 试探性解析（ERR_INTERNAL_ASSERTION: unreachable），
    与本测试意图无关。落盘成 `.js` 后就是普通模块执行。
    """
    if not _NODE:
        pytest.skip("环境无 node，跳过 JS 行为级断言")
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([_NODE, path], capture_output=True, text=True, timeout=timeout)
    finally:
        os.unlink(path)
    assert r.returncode == 0, f"node 执行失败:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def _abort_aware_read(signal_getter: str) -> str:
    """生成一段「会响应 abort 的 read()」实现。

    真实 fetch 的 response body 在被 abort 时会让挂起的 `reader.read()` 以
    AbortError 拒绝。桩如果不模拟这一点，看门狗就无从生效 —— 测试会假绿。
    """
    # 注意：返回的是**语句序列**，会被内联到 `new Promise((res,rej)=>{ ... })` 的
    # executor 里；不要在这里再包一层箭头函数（那样只是声明一个永不调用的闭包，
    # abort 永远传不进来，测试会假绿）。
    return (
        "const sig=" + signal_getter + ";"
        "if(sig&&sig.aborted){const e=new Error('aborted');e.name='AbortError';return rej(e);}"
        "if(sig)sig.addEventListener('abort',()=>{const e=new Error('aborted');"
        "e.name='AbortError';rej(e);});"
    )


# 读取器永不 settle（除非被 abort）—— 模拟「后端卡死、连接不关」
_STALLED_FETCH = (
    "(u,o)=>Promise.resolve({ok:true,status:200,"
    "body:{getReader:()=>({read:()=>new Promise((res,rej)=>{"
    + _abort_aware_read("(o&&o.signal)")
    + "}),cancel:()=>Promise.resolve()})},"
    "json:async()=>({}),text:async()=>''})"
)

# 先吐两个 chunk，然后永久静默（除非被 abort）—— 模拟「流式到一半卡死」
_HALF_STREAM_CHUNKS = [
    'data: {"delta":"你好世界","source":"ai","done":false,"phase":"answering"}\n\n',
    'data: {"delta":"，我是 AI","source":"ai","done":false,"phase":"answering"}\n\n',
]
_HALF_STREAM_FETCH = (
    "(u,o)=>{let n=0;const enc=new TextEncoder();"
    "const CHUNKS=" + json.dumps(_HALF_STREAM_CHUNKS) + ";"
    "return Promise.resolve({ok:true,status:200,"
    "body:{getReader:()=>({read:()=>new Promise((res,rej)=>{"
    "if(n<CHUNKS.length){n++;return res({done:false,value:enc.encode(CHUNKS[n-1])});}"
    + _abort_aware_read("(o&&o.signal)")
    + "}),cancel:()=>Promise.resolve()})},"
    "json:async()=>({}),text:async()=>''});}"
)


def _run_sendchat(fetch_js: str, first_byte_ms: int = 400, chunk_gap_ms: int = 400) -> dict:
    """真跑 sendChat，返回 {finished, unlocked, n_bot, last_bot, last_src}。

    `finished` 为 False 表示 sendChat 在 8s 内没返回（= 永久转圈，本轮缺陷）。
    """
    out = _run_node(
        _load_chat_js(first_byte_ms, chunk_gap_ms)
        + "const __fetchImpl=" + fetch_js + ";"
        "(async()=>{"
        "const raced=await Promise.race(["
        "__sandbox.sendChat('测试').then(()=>'ok').catch(e=>'err:'+(e&&e.message||e)),"
        "new Promise(res=>setTimeout(()=>res('HUNG'),8000))"
        "]);"
        # `_chatSending` 是 chat.js 顶层 let 绑定，不挂在 global 对象上，
        # 只能在同一个 vm context 里求值。
        "const probe=JSON.parse(vm.runInContext("
        "'JSON.stringify({unlocked:_chatSending===false,"
        "bots:chatMessages.filter(m=>m.role===\"bot\")})',__sandbox));"
        "const bots=probe.bots;"
        "console.log(JSON.stringify({finished:raced!=='HUNG',result:raced,"
        "unlocked:probe.unlocked,n_bot:bots.length,"
        "last_bot:bots.length?bots[bots.length-1].text:'',"
        "last_src:bots.length?bots[bots.length-1].src:''}));"
        "process.exit(0);})();",
        timeout=60,
    )
    return json.loads(out.strip().splitlines()[-1])


# ==========================================================================
# 一、看门狗本体（纯逻辑，node + vm）
# ==========================================================================
def test_behavior_watchdog_fires_when_no_first_byte():
    """首字节阶段静默 → 到点必须 abort 且 isStalled=true。（行为级断言）"""
    out = _run_node(
        _load_chat_js(300, 300)
        + "let aborted=false;"
        "const wd=__sandbox._createStallWatchdog(()=>{aborted=true;},150,150);"
        "wd.kick(false);"
        "setTimeout(()=>{console.log(JSON.stringify({"
        "aborted:aborted,stalled:wd.isStalled()}));process.exit(0);},600);"
    )
    got = json.loads(out.strip().splitlines()[-1])
    assert got["aborted"] is True, "首字节静默没有触发 abort"
    assert got["stalled"] is True, "isStalled 没置位（超时反馈分支会再次变成死代码）"


def test_behavior_watchdog_never_fires_while_chunks_keep_coming():
    """★长思考/长输出不被误杀：只要持续 kick 就永不 abort。（行为级断言）

    这是「首字节后固定超时」会踩的坑 —— 本实现靠每 chunk 重新计时避开。
    10 次 kick × 60ms = 600ms 总时长，远大于 200ms 的间隔预算。
    """
    out = _run_node(
        _load_chat_js(300, 300)
        + "let aborted=false;"
        "const wd=__sandbox._createStallWatchdog(()=>{aborted=true;},200,200);"
        "wd.kick(true);"
        "let n=0;const iv=setInterval(()=>{n++;wd.kick(true);"
        "if(n>=10){clearInterval(iv);"
        "setTimeout(()=>{console.log(JSON.stringify({"
        "aborted:aborted,stalled:wd.isStalled(),elapsed_ms:n*60}));process.exit(0);},50);}"
        "},60);"
    )
    got = json.loads(out.strip().splitlines()[-1])
    assert got["aborted"] is False, (
        "总时长 600ms（> 200ms 间隔预算）里持续有 chunk，却被判超时 —— 长输出会被掐断"
    )
    assert got["stalled"] is False


def test_behavior_watchdog_fires_when_stream_goes_silent_midway():
    """★流式阶段卡死必须能被抓到（旧实现在这里裸奔）。（行为级断言）"""
    out = _run_node(
        _load_chat_js(300, 300)
        + "let aborted=false;"
        "const wd=__sandbox._createStallWatchdog(()=>{aborted=true;},150,150);"
        "wd.kick(false);"
        "setTimeout(()=>{wd.kick(true);},50);"  # 首字节到 → 切 chunk 间隔预算
        "setTimeout(()=>{console.log(JSON.stringify({"
        "aborted:aborted,stalled:wd.isStalled()}));process.exit(0);},700);"
    )
    got = json.loads(out.strip().splitlines()[-1])
    assert got["aborted"] is True, "流式阶段静默没有触发 abort —— 界面会永久转圈"
    assert got["stalled"] is True


def test_behavior_watchdog_stop_disarms_it():
    """stop() 之后不得再 abort（否则会误伤下一次请求）。（行为级断言）"""
    out = _run_node(
        _load_chat_js(300, 300)
        + "let aborted=false;"
        "const wd=__sandbox._createStallWatchdog(()=>{aborted=true;},100,100);"
        "wd.kick(false);wd.stop();"
        "setTimeout(()=>{console.log(JSON.stringify({"
        "aborted:aborted,stalled:wd.isStalled()}));process.exit(0);},500);"
    )
    got = json.loads(out.strip().splitlines()[-1])
    assert got["aborted"] is False, "stop() 之后仍然 abort 了"
    assert got["stalled"] is False


# ==========================================================================
# 二、sendChat 端到端（真加载 chat.js + 卡死的流）
# ==========================================================================
def test_behavior_sendchat_returns_on_stalled_stream():
    """★本轮缺陷的核心行为断言：流中途卡死时 sendChat 必须返回，不能永久挂起。"""
    got = _run_sendchat(_STALLED_FETCH)
    assert got["finished"] is True, (
        "sendChat 在 8s 内没有返回 —— 后端卡死时界面永久转圈（本轮缺陷）"
    )
    assert got["unlocked"] is True, "卡死后输入框没解锁（_setChatLock(false) 没走到）"
    assert got["n_bot"] >= 1, "卡死后没有任何反馈消息 —— 用户不知道发生了什么"
    assert "超时" in got["last_bot"], f"卡死后没有给出「请求超时」提示，实际：{got['last_bot']!r}"
    assert got["last_src"] == "timeout", f"来源标记应为 timeout，实际 {got['last_src']!r}"


def test_behavior_sendchat_keeps_partial_text_when_stream_dies_halfway():
    """流到一半卡死：用户已经看到的内容必须保留，不能整段丢掉。（行为级断言）"""
    got = _run_sendchat(_HALF_STREAM_FETCH)
    assert got["finished"] is True, "半途卡死时 sendChat 没有返回"
    assert got["unlocked"] is True, "半途卡死后输入框没解锁"
    assert "你好世界" in got["last_bot"], (
        f"中断前已吐出的正文被丢掉了，实际落库：{got['last_bot']!r}"
    )
    assert "请求超时" not in got["last_bot"], (
        "明明已经吐出内容，却只留一句「请求超时」—— 用户看到的东西被抹掉了"
    )
