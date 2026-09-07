"""The generation parameters shared by the editor preview and actual clients.

Messages, system prompts, tools and transport settings are assembled separately.
"""
import re
from claude_chat.custom_params import validate_custom_params


def supports_temperature(model):
    model_id = (model or "").lower()
    if model_id.startswith("claude-sonnet-5"):
        return False
    match = re.search(r"claude-opus-(\d+)(?:-(\d+))?", model_id)
    return not match or (int(match.group(1)), int(match.group(2) or 0)) < (4, 7)


def validate_request_params(value, platform):
    if not isinstance(value, dict):
        raise ValueError("最终参数 JSON 必须是对象")
    params = validate_custom_params(value)
    for key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
        if key in params and (type(params[key]) is not int or params[key] <= 0):
            raise ValueError(f"{key} 必须是正整数")
    if "temperature" in params:
        value = params["temperature"]
        if type(value) not in (int, float) or not 0 <= value <= 2:
            raise ValueError("temperature 必须是 0 到 2 之间的数字")
    for key in ("thinking", "thinking_config", "output_config"):
        if key in params and not isinstance(params[key], dict):
            raise ValueError(f"{key} 必须是对象")
    if (
        platform == "claude"
        and params.get("thinking")
        and "temperature" in params
        and params["temperature"] != 1
    ):
        raise ValueError("Claude 开启 thinking 时 temperature 只能设为 1")
    if "reasoning_effort" in params and not isinstance(params["reasoning_effort"], str):
        raise ValueError("reasoning_effort 必须是文本")
    if platform == "claude" and "max_tokens" not in params:
        raise ValueError("Claude 请求必须包含 max_tokens")
    if platform == "gemini":
        from google.genai import types
        return types.GenerateContentConfig(**params).model_dump(mode="json", exclude_unset=True)
    return params


def generation_params(platform, model, max_tokens, temperature, thinking=None, output=None,
                      custom=None, override=None):
    if override is not None:
        return validate_request_params(override, platform)
    params = {}
    if platform == "claude":
        params["max_tokens"] = max_tokens
        if temperature is not None and supports_temperature(model):
            params["temperature"] = temperature
        if thinking:
            params["thinking"] = thinking
        if output:
            params["output_config"] = output
    elif platform == "gemini":
        params.update(max_output_tokens=max_tokens, temperature=temperature)
        if thinking and thinking.get("enabled"):
            if any(part in model.lower() for part in ("gemini-3", "thinking", "level")):
                params["thinking_config"] = {"thinking_level": thinking.get("effort", "high")}
            else:
                params["thinking_config"] = {"thinking_budget": thinking.get("budget_tokens") or 1024}
        if "image" in model.lower():
            params.update(response_modalities=["IMAGE", "TEXT"], image_config={"image_size": "2K"})
    else:
        params["temperature"] = temperature
        if max_tokens is not None and max_tokens > 0:
            params["max_tokens"] = max_tokens
        if thinking and thinking.get("effort"):
            params["reasoning_effort"] = thinking["effort"]
    params.update(validate_custom_params(custom))
    if platform == "claude" and params.get("thinking") and "temperature" in params:
        if supports_temperature(model):
            # Anthropic rejects every other value while extended thinking is enabled.
            params["temperature"] = 1
        else:
            params.pop("temperature", None)
    # Gemini coerces SDK types and aliases; show those same effective values in the editor.
    if platform == "gemini":
        return validate_request_params(params, platform)
    return params


def preview_generation_params(platform, model, config):
    from claude_chat.platform_params import PlatformParamMapper
    mapped = PlatformParamMapper.map_params(platform, config, model)
    return generation_params(platform, model, mapped["max_tokens"], mapped["temperature"],
                             mapped["thinking_config"], mapped["output_config"],
                             mapped["custom_params"], mapped["request_params"])
