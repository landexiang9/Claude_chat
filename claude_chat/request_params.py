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
    for key in ("thinking", "thinking_config", "output_config", "reasoning", "text"):
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
    if platform == "responses":
        # Existing model settings may have been saved using Chat Completions.
        for key in ("max_completion_tokens", "max_tokens"):
            if key in params:
                params.setdefault("max_output_tokens", params.pop(key))
        if "reasoning_effort" in params:
            params.setdefault("reasoning", {}).setdefault("effort", params.pop("reasoning_effort"))
        if "effort" in params.get("reasoning", {}) and not isinstance(params["reasoning"]["effort"], str):
            raise ValueError("reasoning.effort 必须是文本")
        if "include" in params and (
            not isinstance(params["include"], list) or not all(isinstance(item, str) for item in params["include"])
        ):
            raise ValueError("include 必须是文本数组")
        if "store" in params and type(params["store"]) is not bool:
            raise ValueError("store 必须是布尔值")
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
    elif platform == "responses":
        if max_tokens is not None and max_tokens > 0:
            params["max_output_tokens"] = max_tokens
        # Reasoning models commonly reject sampling parameters. Explicit JSON
        # settings are still honored for models/providers that support them.
        model_name = (model or "").lower().rsplit("/", 1)[-1]
        if temperature is not None and not re.match(r"(?:o\d|gpt-[5-9])", model_name):
            params["temperature"] = temperature
        if thinking and thinking.get("effort"):
            params["reasoning"] = {"effort": thinking["effort"], "summary": "auto"}
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
    if platform in {"gemini", "responses"}:
        return validate_request_params(params, platform)
    return params


def preview_generation_params(platform, model, config):
    from claude_chat.platform_params import PlatformParamMapper
    mapped = PlatformParamMapper.map_params(platform, config, model)
    return generation_params(request_protocol(platform, config), model, mapped["max_tokens"], mapped["temperature"],
                             mapped["thinking_config"], mapped["output_config"],
                             mapped["custom_params"], mapped["request_params"])


def request_protocol(platform, config):
    """Resolve the Responses generation schema without changing provider identity."""
    from claude_chat.config import find_custom_provider
    from claude_chat.provider_adapters import normalize_custom_provider_adapter

    if platform == "deepseek" and config.get("deepseek_use_responses", False):
        return "responses"
    if platform.startswith("custom:"):
        provider = find_custom_provider(config, platform) or {}
        if normalize_custom_provider_adapter(provider.get("provider_adapter")) == "responses":
            return "responses"
    return platform
