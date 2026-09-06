"""JSON 提取工具：从 LLM 输出中提取第一个完整 JSON 对象。

三层防御（与 pipeline_runner.py 的 LLM 仲裁解析保持一致）：
1. 直接 json.loads 整个文本
2. 剥离 ```json ... ``` 代码块
3. 花括号计数扫描，取第一个完整 {} 对

返回 dict；失败返回 None。
"""
import json
import re


def extract_json_object(text: str) -> dict | None:
    """从文本中提取第一个完整 JSON 对象。

    Args:
        text: LLM 原始输出文本。

    Returns:
        解析出的 dict；若无法解析出完整 JSON 对象则返回 None。
    """
    if not text:
        return None

    # 方法1: 直接解析整个文本
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # 方法2: 提取 ```json ... ``` 代码块
    code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_match:
        try:
            parsed = json.loads(code_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # 方法3: 花括号计数，取第一个完整 {} 对
    start_idx = text.find('{')
    if start_idx >= 0:
        brace_count = 0
        for i in range(start_idx, len(text)):
            if text[i] == '{':
                brace_count += 1
            elif text[i] == '}':
                brace_count -= 1
            if brace_count == 0:
                try:
                    parsed = json.loads(text[start_idx:i + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    pass
                break

    return None
