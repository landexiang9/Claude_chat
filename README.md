# Claude Chat

Desktop GUI client for Anthropic Claude API, built with **customtkinter**. Supports model switching, file uploads, conversation history, token counting, proxy settings, Markdown rendering, and extended thinking.

## Features

- **Modern dark UI** — customtkinter with dark theme
- **Model switching** — auto-fetches available models via `client.models.list()`, supports all Claude models
- **Extended thinking** — configurable thinking mode (adaptive / enabled / disabled) with budget tokens; thinking content displayed in a collapsible panel
- **File upload** — images (base64), PDFs (document block), and text files injected into context
- **Conversation management** — create, rename, delete, auto-clean (keeps last 50)
- **Token counting** — displays input/output tokens after each response
- **Proxy support** — three modes: system proxy (env vars), no proxy, custom proxy URL
- **Markdown rendering** — headings, bold/italic, inline code, code blocks, blockquotes, lists, horizontal rules, **tables**
- **Streaming response** — real-time text display, final render with full Markdown formatting
- **Auto-resize** — message textboxes auto-adjust height to fit content

## Requirements

- Python 3.10+
- Anthropic API key (set in-app or via bottom bar)

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run
python claude_chat.py
```

On first launch, you'll be prompted for your API key. Alternatively, paste it into the bottom bar and click Save.

## Dependencies

| Package | Version |
|---------|---------|
| customtkinter | >=5.2.2 |
| anthropic | >=0.103.0 |
| Pillow | >=10.0.0 |
| httpx | (via anthropic) |

## Project Structure

```
Claude_chat/
├── claude_chat.py     # Main application (~1285 lines)
├── requirements.txt   # Python dependencies
├── config.json        # Runtime config (auto-created)
└── conversations/     # Per-conversation JSON store (auto-created)
    ├── abc123.json
    └── ...
```

### config.json

```json
{
  "api_key": "sk-ant-...",
  "model": "claude-sonnet-4-20250514",
  "temperature": 0.7,
  "max_tokens": 4096,
  "thinking_enabled": false,
  "thinking_type": "adaptive",
  "thinking_budget": 16000,
  "proxy_mode": "system",
  "proxy_url": ""
}
```

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Enter` | Send message |
| `Shift+Enter` | Newline (default) |

## Proxy Modes

1. **System proxy** — reads `HTTP_PROXY`/`HTTPS_PROXY` environment variables
2. **No proxy** — `httpx.Client(trust_env=False)`, ignores all proxy settings
3. **Custom proxy** — specify a proxy URL (e.g. `http://127.0.0.1:10808`)

The active mode is shown in the top bar next to the model selector.
