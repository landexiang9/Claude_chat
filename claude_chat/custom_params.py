"""Validation for per-model custom generation parameters."""
import copy
import json
import re

RESERVED_PARAMS = {
    "model", "messages", "contents", "stream", "stream_options", "system", "system_instruction",
    "tools", "tool_choice", "api_key", "base_url", "api_url", "http_options", "extra_headers",
    "extra_query", "extra_body", "timeout", "__proto__", "constructor", "prototype",
}


def validate_custom_params(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("自定义参数必须是 JSON 对象")
    for key in value:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("自定义参数名只能包含字母、数字和下划线，且不能以数字开头")
        if key in RESERVED_PARAMS:
            raise ValueError(f"自定义参数 {key} 由程序管理，不能覆盖")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("自定义参数必须是有效 JSON，数字必须有限") from exc
    return copy.deepcopy(value)
