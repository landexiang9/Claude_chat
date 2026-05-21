# Claude Chat

Desktop GUI client for Anthropic Claude API, built with **pywebview** and modern web technologies. Features a premium **Catppuccin Mocha** dark theme, real-time streaming, collapsible reasoning/thinking steps, offline-first assets, text selection with custom context menus, and proxy support.

## Features

- **Premium Dark UI** — Powered by a modern web layout using the HSL-based Catppuccin Mocha color palette, featuring glassmorphism elements, custom scrollbars, and micro-animations.
- **Model Switching** — Auto-fetches available models via the API in the background. Initially loads from a cached local model list for a near-instant startup.
- **Extended Thinking** — Configurable reasoning/thinking mode (adaptive / enabled / disabled) with budget token controls. Reasoning paths are visualised in an expandable toggle panel with estimated token sizes.
- **Text Selection & Custom Context Menu** — Full mouse selection enabled natively. Right-clicking inside input fields brings up standard Cut/Copy/Paste/Select All options, while right-clicking inside message cards offers "Copy Selection", "Copy Message", and "Select All" actions.
- **Windows Ctypes Clipboard Bridge** — Utilises a ctypes clipboard hook on Windows to bypass WebView2's clipboard restrictions, ensuring reliable paste functions.
- **Offline-First Libraries** — Markdown (`marked.js`) and syntax highlighting (`highlight.js` with `github-dark` theme) are fully localized inside the project. The application does not rely on external CDN dependencies, preventing GFW blockages or slow loading times.
- **File Upload** — Supports attaching text documents, images (base64 encoded), and PDFs directly.
- **Conversation Management** — Easily create, delete, and switch between conversations. Conversations are automatically pruned (keeping the last 50 files) to save disk space.
- **Token Counting** — Displays input and output token counts for each conversation interaction.
- **Proxy Support** — Choose between system proxy (environment variables), no proxy, or custom proxy URL configs directly via a graphical modal.

## Requirements

- Python 3.10+
- Anthropic API key (set inside the app settings)
- Internet connection (for API calls)

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run
python claude_chat.py
```

On first launch, click the settings gear icon (⚙️) to configure your Anthropic API Key.

## Dependencies

| Package | Version |
|---------|---------|
| pywebview | >=5.0 |
| anthropic | >=0.103.0 |
| Pillow | >=10.0.0 |

## Project Structure

```text
Claude_chat/
├── claude_chat.py      # Application launcher entry point
├── requirements.txt    # Python dependencies
├── claude_chat/        # Core application source
│   ├── __init__.py     # Module initialization
│   ├── app.py          # PyWebView GUI & JS bridge API
│   ├── client.py       # Anthropic Client wrapper
│   ├── config.py       # Configuration manager
│   ├── conversation.py # Conversation JSON manager
│   └── ui/             # Web interface files
│       ├── index.html  # Application HTML layout
│       ├── style.css   # HSL Catppuccin Mocha styles
│       ├── app.js      # Frontend interaction logic & stream callbacks
│       └── libs/       # Offline-first localized libraries (marked, highlight)
├── config.json         # Runtime configuration file (git-ignored)
└── conversations/      # Chat history directory (git-ignored)
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
| `Shift+Enter` | Newline (default behavior) |

## Proxy Modes

1. **System proxy** — reads `HTTP_PROXY`/`HTTPS_PROXY` environment variables.
2. **No proxy** — ignores all proxy settings.
3. **Custom proxy** — specify a proxy URL (e.g. `http://127.0.0.1:10808`).
