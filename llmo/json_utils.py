"""Extract one strict JSON object from an LLM response text."""

from __future__ import annotations

import json
import re
from typing import Any


def dumps_readable(value: Any, indent: int = 2) -> str:
    """Serialize JSON with objects expanded but numeric/vector arrays horizontal.

    The standard ``json.dumps(..., indent=2)`` puts every list element on its own
    line. Runtime records contain 401-point frequency/S11 vectors, so this small
    formatter keeps scalar-only arrays on one line while retaining readable
    indentation for dictionaries and arrays of objects.
    """

    def render(item: Any, level: int) -> str:
        prefix = " " * (indent * level)
        child_prefix = " " * (indent * (level + 1))
        if isinstance(item, dict):
            if not item:
                return "{}"
            entries = [
                f"{child_prefix}{json.dumps(str(key), ensure_ascii=False)}: {render(val, level + 1)}"
                for key, val in item.items()
            ]
            return "{\n" + ",\n".join(entries) + "\n" + prefix + "}"
        if isinstance(item, list):
            if not item:
                return "[]"
            # Numeric/string/bool vectors are intentionally horizontal.
            if all(not isinstance(child, dict) for child in item):
                return "[" + ", ".join(render(child, level + 1) for child in item) + "]"
            entries = [f"{child_prefix}{render(child, level + 1)}" for child in item]
            return "[\n" + ",\n".join(entries) + "\n" + prefix + "]"
        return json.dumps(item, ensure_ascii=False)

    return render(value, 0)


def extract_json_object(text: str) -> dict[str, Any]:
    """从纯 JSON、Markdown JSON 代码块或夹杂少量说明的响应中提取根对象。"""
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        # 这是兼容性后备路径；提示词仍明确要求模型只能返回 JSON，避免依赖宽松解析。
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM response does not contain a JSON object")
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("LLM response root must be a JSON object")
    return value
