"""Custom-provider protocol adapter definitions and legacy migration helpers."""

CUSTOM_PROVIDER_ADAPTERS = {
    "local",
    "openai_files",
    "openrouter",
    "inline_images",
    "anthropic",
    "gemini",
}

_ALIASES = {
    "disabled": "local",
    "none": "local",
    "openai": "openai_files",
    "files": "openai_files",
    "openrouter_inline": "openrouter",
    "inline": "inline_images",
    "anthropic_files": "anthropic",
    "gemini_files": "gemini",
}


def normalize_custom_provider_adapter(value=None, legacy_file_upload_enabled=False):
    """Return a supported adapter, migrating the old boolean Files API setting."""
    if isinstance(value, str) and value.strip():
        adapter = _ALIASES.get(value.strip().lower(), value.strip().lower())
        if adapter in CUSTOM_PROVIDER_ADAPTERS:
            return adapter
    return "openai_files" if legacy_file_upload_enabled else "local"


def adapter_accepts_attachment(adapter, extension):
    """Whether an attachment should reach the provider instead of local parsing."""
    adapter = normalize_custom_provider_adapter(adapter)
    if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return adapter != "local"
    if extension == ".pdf":
        return adapter in {"openai_files", "openrouter", "anthropic", "gemini"}
    return False


def adapter_uses_remote_files_api(adapter):
    return normalize_custom_provider_adapter(adapter) in {"openai_files", "anthropic", "gemini"}


def adapter_expiry_limit(adapter):
    return 7776000 if normalize_custom_provider_adapter(adapter) == "anthropic" else 2592000
