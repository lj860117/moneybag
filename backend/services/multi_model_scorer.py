"""
钱袋子 — 多模型 AI 评分引擎（v9.5.124）

三家大模型各自独立对基金打分（0-10分+理由），综合加权排名。
- DeepSeek V4 Pro：主力深度分析
- 豆包 Seed 2.0 Pro：字节系视角
- 千问 Qwen3.6-Plus：阿里系视角

复用已有的 API key 和 endpoint，不新建客户端。
缓存策略：per-fund 文件缓存 12h（每天只消耗 1 次 LLM）。
"""
import os
import json
import time
import hashlib
from typing import Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 三家模型配置（复用 llm_gateway 的降级链 endpoint）
_MODELS = [
    {
        "id": "deepseek",
        "name": "DeepSeek Pro",
        "model": "deepseek-v4-pro",
        "key_env": "LLM_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
    },
    {
        "id": "doubao",
        "name": "豆包 Seed 2.0",
        "model": "doubao-seed-2-0-pro-260215",
        "key_env": "DOUBAO_API_KEY",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    },
    {
        "id": "qwen",
        "name": "千问 Qwen3.6",
        "model": "qwen3.6-plus",
        "key_env": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
]

_CACHE_DIR = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "multi_model_score"
_CACHE_TTL = 43200  # 12h


def _get_cache(code: str) -> Optional[dict]:
    try:
        fp = _CACHE_DIR / f"{code}.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
            if time.time() - d.get("t", 0) < _CACHE_TTL:
                return d.get("v")
    except Exception:
        pass
    return None


def _set_cache(code: str, value: dict):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fp = _CACHE_DIR / f"{code}.json"
        fp.write_text(json.dumps({"v": value, "t": time.time()}, ensure_ascii=False), encoding="utf-8")
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
        f"夏普比率: {sharpe}" if sharpe else "",
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


def _call_model(model_cfg: dict, prompt: str) -> dict:
    """调用单个模型打分"""
    import httpx

    key = os.environ.get(model_cfg["key_env"], "")
    if not key:
        return {"id": model_cfg["id"], "name": model_cfg["name"], "score": None, "reason": "API key未配置", "error": True}

    try:
        with httpx.Client(timeout=30) as client:
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
                    "max_tokens": 250,
                    "temperature": 0.3,
                },
            )
            if resp.status_code != 200:
                return {"id": model_cfg["id"], "name": model_cfg["name"], "score": None, "reason": f"HTTP {resp.status_code}", "error": True}

            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            # 解析 JSON（兼容 markdown code block + 截断修复）
            import re
            # 先尝试完整 JSON
            json_match = re.search(r'\{[^}]*\}', content)
            parsed = None
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
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
            return {"id": model_cfg["id"], "name": model_cfg["name"], "score": None, "reason": "解析失败", "error": True}
    except Exception as e:
        return {"id": model_cfg["id"], "name": model_cfg["name"], "score": None, "reason": str(e)[:30], "error": True}


def score_fund_multi_model(fund_info: dict) -> dict:
    """
    三模型并发评分 → 综合排名

    返回:
    {
        "scores": [
            {"id": "deepseek", "name": "DeepSeek Pro", "score": 7.5, "reason": "...", "risk": "..."},
            {"id": "doubao", "name": "豆包 Seed 2.0", "score": 8.0, "reason": "...", "risk": "..."},
            {"id": "qwen", "name": "千问 Qwen3.6", "score": 7.0, "reason": "...", "risk": "..."},
        ],
        "avg_score": 7.5,
        "consensus": "推荐" / "分歧" / "谨慎",
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

    # 3. 三模型并发调用
    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_call_model, m, prompt): m for m in _MODELS}
        for future in as_completed(futures, timeout=35):
            try:
                results.append(future.result())
            except Exception:
                m = futures[future]
                results.append({"id": m["id"], "name": m["name"], "score": None, "reason": "超时", "error": True})

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
        "scored_at": int(time.time()),
    }

    # 5. 写缓存
    _set_cache(code, result)
    return result
