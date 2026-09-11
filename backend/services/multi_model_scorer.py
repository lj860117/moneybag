"""
钱袋子 — 多模型 AI 评分引擎（v9.5.124）

两家大模型各自独立对基金打分（0-10分+理由），综合加权排名。
- DeepSeek V4 Flash：主力分析（2026-09-11 全面 Flash 化，内部默认不再用 Pro）
- 豆包 Seed 2.1 Pro：字节系视角
（千问 Qwen3.6 已于欠费后下线）

复用已有的 API key 和 endpoint，不新建客户端。

缓存策略（v9.9.x 修正：失败结果不再被长期缓存）：
- 两家都成功 → per-fund 文件缓存 12h
- 只有一家成功 → 缓存 30min（不把"半数失败"锁 12 小时）
- 两家全失败 → **不写缓存**，让用户点重试能真正重试

超时预算（口径以实测为准，不要想当然）：
- _MODEL_TIMEOUT(35s)：httpx 单模型读超时。**它才是真实的墙钟上界** ——
  无论 _TOTAL_TIMEOUT 设成多少，请求最多卡到这里就抛 ReadTimeout 返回。
- _TOTAL_TIMEOUT(40s)：as_completed 的**等待预算，不是墙钟上限**。
  超时后 `except FuturesTimeoutError` 只保证"最终能返回结构化结果"，
  **不保证此刻立即返回** —— `with ThreadPoolExecutor` 退出时
  shutdown(wait=True) 仍会阻塞 join 挂起线程。
  实测：_TOTAL_TIMEOUT=1 而线程挂 8s → 函数 8.01s 才返回（QA C9）。
  因 httpx 自带 35s 超时，生产真实最坏约 35s，不会被前端截断。
- 前端 AbortSignal.timeout(45000)：需大于"生产真实最坏值"（≈35s），
  而**不是**大于 _TOTAL_TIMEOUT —— 二者不是同一个量，别拿它当前端上界。
"""
import config
import os
import json
import time
import hashlib
from typing import Optional
from pathlib import Path
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
    TimeoutError as FuturesTimeoutError,
)

# 两家模型配置（复用 llm_gateway 的降级链 endpoint）
#
# 实测结论（2026-09-12，服务器生产 Key 直连）：
# 1) deepseek-v4-flash 不是无效 ID —— 官方 /models 只列 deepseek-flash /
#    deepseek-v4-pro，但传 v4-flash 会被服务端静默归一化为 deepseek-flash，
#    返回 200 且内容正常。因此**不需要改模型 ID**。
# 2) 但它是会输出 reasoning_content 的思考型模型，**思考 token 计入 max_tokens**。
#    原 max_tokens=250 时，思考常吃掉全部预算：finish_reason=length、
#    content 为空或截成 '{"score": 6.5' → "解析失败"。
#    max_tokens 只是上限不是成本（只按实际生成计费），放大是免费保险。
# 3) doubao-seed-2-1-pro-260628 同样是思考模型，默认开思考时实测 38.91s /
#    3117 reasoning tokens，必然撞 30s 读超时 → "The read operation timed out"。
#    显式关闭思考后 2.12s 返回，输出质量不变。
_MODELS = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "model": "deepseek-v4-flash",
        "key_env": "LLM_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "max_tokens": 2000,
        "extra_body": {},
    },
    {
        "id": "doubao",
        "name": "豆包 Seed 2.1",
        "model": "doubao-seed-2-1-pro-260628",
        "key_env": "DOUBAO_API_KEY",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "max_tokens": 2000,
        # 评分只需要一个 JSON，思考过程毫无价值却要 30s+，直接关掉。
        "extra_body": {"thinking": {"type": "disabled"}},
    },
]

_CACHE_DIR = Path(config.DATA_DIR) / "_cache" / "multi_model_score"
_CACHE_TTL = 43200  # 12h：两家都成功
_CACHE_TTL_PARTIAL = 1800  # 30min：只有一家成功
_MODEL_TIMEOUT = 35  # 单模型 HTTP 超时（秒）
_TOTAL_TIMEOUT = 40  # 两家并发总预算（秒）


def _get_cache(code: str) -> Optional[dict]:
    try:
        fp = _CACHE_DIR / f"{code}.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
            # 每个条目自带 ttl（部分成功用短 TTL）；老条目没有 ttl 字段则沿用 12h
            ttl = d.get("ttl", _CACHE_TTL)
            if time.time() - d.get("t", 0) < ttl:
                v = d.get("v")
                # 自愈：历史"全失败"结果（model_count=0）一律视为未缓存。
                # 修复前失败结果被无条件写 12h 缓存，线上已积累 35 个
                # model_count=0 的毒缓存；不在这里挡掉，用户点重试仍会
                # 秒回同一份失败结果——正是"一直刷新不出来"的现象。
                # 这样无需手工清理线上缓存，部署后毒缓存自动失效。
                if isinstance(v, dict) and v.get("model_count", 0) > 0:
                    return v
    except Exception:
        pass
    return None


def _set_cache(code: str, value: dict, ttl: Optional[int] = None):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fp = _CACHE_DIR / f"{code}.json"
        fp.write_text(
            json.dumps(
                {"v": value, "t": time.time(), "ttl": ttl or _CACHE_TTL},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _build_prompt(fund_info: dict) -> str:
    """构建评分 prompt（给所有模型相同的输入）"""
    name = fund_info.get("name", "")
    code = fund_info.get("code", "")
    returns = fund_info.get("returns", {})
    r3m = returns.get("3m")
    r6m = returns.get("6m")
    r1y = returns.get("1y")
    r3y = returns.get("3y")
    fee = fund_info.get("fee", "")
    max_dd = fund_info.get("max_drawdown")
    sharpe = fund_info.get("sharpe_ratio")
    sortino = fund_info.get("sortino_ratio")
    calmar = fund_info.get("calmar_ratio")
    ir = fund_info.get("information_ratio")
    treynor = fund_info.get("treynor_ratio")
    beta = fund_info.get("beta")
    scale = fund_info.get("scale_billion")
    nav_pct = fund_info.get("nav_percentile")
    trend = fund_info.get("trend_label", "")
    trend_score = fund_info.get("trend_score")

    data_lines = [
        f"基金: {name} ({code})",
        f"近3月: {r3m}%" if r3m is not None else "",
        f"近6月: {r6m}%" if r6m is not None else "",
        f"近1年: {r1y}%" if r1y is not None else "",
        f"近3年: {r3y}%" if r3y is not None else "",
        f"费率: {fee}" if fee else "",
        f"规模: {scale}亿" if scale else "",
        f"最大回撤: {max_dd}%" if max_dd else "",
        f"夏普比率: {sharpe}" if sharpe is not None else "",
        f"索提诺比率: {sortino}" if sortino is not None else "",
        f"卡玛比率: {calmar}" if calmar is not None else "",
        f"信息比率: {ir}" if ir is not None else "",
        f"特雷诺比率: {treynor}" if treynor is not None else "",
        f"Beta: {beta}" if beta is not None else "",
        f"净值百分位: {nav_pct}%" if nav_pct is not None else "",
        f"走势预估: {trend} ({trend_score}分)" if trend else "",
    ]
    data_block = "\n".join([l for l in data_lines if l])

    return f"""你是专业基金分析师。根据以下基金数据，给出你的投资推荐评分和理由。

{data_block}

请严格按以下JSON格式回复（不要多余内容）：
{{"score": 7.5, "reason": "一句话理由(20字以内)", "risk": "主要风险(10字以内)"}}

评分标准(0-10分)：
- 9-10: 强烈推荐，低位+强动量+优秀经理
- 7-8: 推荐，收益风险比优秀
- 5-6: 中性，可观察
- 3-4: 谨慎，高位或动量衰减
- 1-2: 不推荐，高风险低回报"""


def _failure_reason(content: str, finish_reason: Optional[str]) -> str:
    """把"解析失败"细化成能定位的失败原因，便于前端如实告诉用户哪一步坏了。"""
    if not content.strip():
        if finish_reason == "length":
            return "输出被上限截断，未完成思考"
        return "模型返回内容为空"
    if finish_reason == "length":
        return "JSON被上限截断，无法解析"
    return "解析失败"


def _call_model(model_cfg: dict, prompt: str) -> dict:
    """调用单个模型打分"""
    import httpx

    key = os.environ.get(model_cfg["key_env"], "")
    if not key:
        return {"id": model_cfg["id"], "name": model_cfg["name"], "score": None, "reason": "API key未配置", "error": True}

    try:
        with httpx.Client(timeout=_MODEL_TIMEOUT) as client:
            resp = client.post(
                f"{model_cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_cfg["model"],
                    "messages": [
                        {"role": "system", "content": "你是基金投资分析专家，只输出JSON，不输出其他内容。"},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": model_cfg.get("max_tokens", 2000),
                    "temperature": 0.3,
                    # 豆包需要显式关闭思考，否则 30s+ 必然超时
                    **model_cfg.get("extra_body", {}),
                },
            )
            if resp.status_code != 200:
                return {"id": model_cfg["id"], "name": model_cfg["name"], "score": None, "reason": f"HTTP {resp.status_code}", "error": True}

            payload = resp.json()
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message", {}) or {}
            content = message.get("content") or ""
            finish_reason = choice.get("finish_reason")
            usage = payload.get("usage", {}) or {}
            # v9.9.11: 复用 gateway 计费（绕过 gateway 直连导致的成本盲区）
            try:
                from infra.llm.gateway import LLMGateway
                gw = LLMGateway.instance()
                gw.record_external_call(
                    user_id="",
                    module="multi_model_score",
                    model=model_cfg["model"],
                    input_tokens=usage.get("prompt_tokens", usage.get("input_tokens", 0)),
                    output_tokens=usage.get("completion_tokens", usage.get("output_tokens", 0)),
                    cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
                    cache_miss_tokens=usage.get("prompt_cache_miss_tokens", 0),
                )
            except Exception as _e:
                print(f"[MULTI_MODEL] 计费失败（不影响打分）: {_e}")
            # 解析 JSON（兼容 markdown code block + 截断修复）
            import re
            from services.json_extract import extract_json_object
            parsed = extract_json_object(content)
            # 截断修复：如果没匹配到完整 }，手动补全
            if parsed is None and '"score"' in content:
                try:
                    # 提取 score 数字
                    score_match = re.search(r'"score"\s*:\s*([0-9.]+)', content)
                    reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"?', content)
                    risk_match = re.search(r'"risk"\s*:\s*"([^"]*)"?', content)
                    if score_match:
                        parsed = {
                            "score": float(score_match.group(1)),
                            "reason": reason_match.group(1) if reason_match else "",
                            "risk": risk_match.group(1) if risk_match else "",
                        }
                except Exception:
                    pass
            if parsed and parsed.get("score") is not None:
                score = float(parsed.get("score", 0))
                score = max(0, min(10, score))  # clamp 0-10
                return {
                    "id": model_cfg["id"],
                    "name": model_cfg["name"],
                    "score": round(score, 1),
                    "reason": str(parsed.get("reason", ""))[:30],
                    "risk": str(parsed.get("risk", ""))[:20],
                }
            return {
                "id": model_cfg["id"],
                "name": model_cfg["name"],
                "score": None,
                "reason": _failure_reason(content, finish_reason),
                "error": True,
            }
    except Exception as e:
        return {"id": model_cfg["id"], "name": model_cfg["name"], "score": None, "reason": str(e)[:30], "error": True}


def score_fund_multi_model(fund_info: dict) -> dict:
    """
    两模型并发评分 → 综合排名（与 _MODELS 保持一致：DeepSeek + 豆包 Seed 2.1）

    返回:
    {
        "scores": [
            {"id": "deepseek", "name": "DeepSeek", "score": 7.5, "reason": "...", "risk": "..."},
            {"id": "doubao", "name": "豆包 Seed 2.1", "score": 8.0, "reason": "...", "risk": "..."},
        ],
        "avg_score": 7.5,
        "consensus": "推荐" / "分歧" / "谨慎",
        "model_count": 2,       # 成功家数
        "model_total": 2,       # 总家数
        "partial": False,       # 只有部分模型成功
        "scored_at": 1717300000,
    }
    """
    code = fund_info.get("code", "")

    # 1. 缓存命中
    cached = _get_cache(code)
    if cached:
        cached["from_cache"] = True
        return cached

    # 2. 构建 prompt
    prompt = _build_prompt(fund_info)

    # 3. 两模型并发调用
    results = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(_call_model, m, prompt): m for m in _MODELS}
        try:
            for future in as_completed(futures, timeout=_TOTAL_TIMEOUT):
                try:
                    results.append(future.result())
                except Exception as e:
                    m = futures[future]
                    results.append({"id": m["id"], "name": m["name"], "score": None, "reason": str(e)[:30] or "调用异常", "error": True})
        except FuturesTimeoutError:
            # 总预算耗尽：未返回的模型补记为超时，不让异常冒泡成 500
            for future, m in futures.items():
                if not future.done():
                    future.cancel()
                    results.append({"id": m["id"], "name": m["name"], "score": None, "reason": f"超时(>{_TOTAL_TIMEOUT}s)", "error": True})

    # as_completed 是"完成序"，排序还原成 _MODELS 定义的稳定顺序
    order = {m["id"]: i for i, m in enumerate(_MODELS)}
    results.sort(key=lambda r: order.get(r.get("id"), len(order)))

    # 4. 综合评分
    valid_scores = [r["score"] for r in results if r.get("score") is not None]
    avg_score = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else None

    # 共识度判断
    consensus = "未知"
    if len(valid_scores) >= 2:
        spread = max(valid_scores) - min(valid_scores)
        if avg_score and avg_score >= 7 and spread <= 2:
            consensus = "共识推荐"
        elif avg_score and avg_score <= 4 and spread <= 2:
            consensus = "共识谨慎"
        elif spread > 3:
            consensus = "分歧较大"
        elif avg_score and avg_score >= 6:
            consensus = "偏向推荐"
        elif avg_score and avg_score <= 5:
            consensus = "偏向观望"
        else:
            consensus = "中性"

    result = {
        "scores": results,
        "avg_score": avg_score,
        "consensus": consensus,
        "model_count": len(valid_scores),
        "model_total": len(_MODELS),
        "partial": 0 < len(valid_scores) < len(_MODELS),
        "scored_at": int(time.time()),
    }

    # 5. 写缓存：全失败时不写，否则用户 12 小时内点重试只会秒回同一份失败结果
    if result["model_count"] >= len(_MODELS):
        _set_cache(code, result, _CACHE_TTL)
    elif result["model_count"] >= 1:
        _set_cache(code, result, _CACHE_TTL_PARTIAL)
    return result
