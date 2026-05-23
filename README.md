# Claude Chat

Desktop GUI client for Anthropic Claude API, built with **pywebview** and modern web technologies. Features a premium **Catppuccin Mocha** dark theme, real-time streaming, collapsible reasoning/thinking steps, offline-first assets, text selection with custom context menus, secure credential storage, SQLite database architecture, custom system prompts preset manager, artifacts side panel preview, and proxy support.

## Key Features

- **Premium Dark UI** — Powered by a modern web layout using the HSL-based Catppuccin Mocha color palette, featuring glassmorphism elements, custom scrollbars, and micro-animations.
- **Model Switching & Persistence** — Auto-fetches available models via the API in the background. Initially loads from a cached local model list for a near-instant startup. Switched model settings persist across application restarts.
- **Extended Thinking** — Configurable reasoning/thinking mode (adaptive / enabled / disabled) with budget token controls. Reasoning paths are visualized in an expandable toggle panel with estimated token sizes.
- **Stop Generating Button** — Interrupt active streaming with a visual control, terminating the connection and displaying an `🚫 已中止` (Aborted) badge while saving partial responses.
- **Artifacts Side Panel** — A collapsible right-hand side panel that renders SVGs natively, runs HTML snippets in a sandboxed `<iframe>` container, and processes Markdown or structural graphs using local `mermaid.js`, providing rich, isolated previews.
- **System Prompts Preset Manager** — Save, edit, and delete system prompt presets via the Settings (⚙️) modal. Easily switch active presets directly from the navigation bar dropdown.
- **Secure Storage** — Encrypts and stores Anthropic API keys directly inside the Windows Credential Manager using `keyring`. Falls back to an obfuscated Base64 + XOR salt scheme when a keyring isn't available.
- **SQLite Database Architecture** — Shifts the storage layer from individual files to a structured SQLite database (`claude_chat.db`). Legacy chat history JSON files inside `conversations/` are automatically migrated on boot and archived in `conversations_backup/`.
- **System Log Viewer** — Logs system behavior directly to `claude_chat.log`. View, refresh, copy, and clear logs in real time from the Settings (⚙️) modal.
- **Raw Packet Viewer** — An API payload inspector. Click the package icon (`📦`) next to any message to view the database record and the Anthropic API request/response JSON payload (with massive base64 content sanitized).
- **Text Selection & Custom Context Menu** — Full mouse selection enabled natively. Right-clicking inside input fields brings up standard Cut/Copy/Paste/Select All options, while right-clicking inside message cards offers "Copy Selection", "Copy Message", and "Select All" actions.
- **Windows Ctypes Clipboard Bridge** — Utilizes a ctypes clipboard hook on Windows to bypass WebView2's clipboard restrictions, ensuring reliable copy/paste functions.
- **Offline-First Libraries** — Markdown (`marked.js`), syntax highlighting (`highlight.js`), and graphing (`mermaid.js`) are fully localized. The application operates entirely without external CDN network requests.
- **Proxy Support** — Choose between system proxy (environment variables), no proxy, or custom proxy URL configs directly via a graphical modal.

## Requirements

- Python 3.10+
- Anthropic API key (configured inside the app settings)
- Internet connection (for API calls)

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the application
python claude_chat.py
```

On first launch, click the settings gear icon (⚙️) to configure your Anthropic API Key.

## Dependencies

| Package | Version | Description |
|---------|---------|-------------|
| pywebview | >=5.0 | Desktop GUI container |
| anthropic | >=0.103.0 | Anthropic SDK |
| Pillow | >=10.0.0 | Image processing |
| keyring | >=24.0.0 | Secure OS credential storage |

## Project Structure

```text
Claude_chat/
├── claude_chat.py      # Application launcher entry point
├── requirements.txt    # Python dependencies
├── claude_chat/        # Core application source
│   ├── __init__.py     # Module initialization
│   ├── app.py          # PyWebView GUI & JS bridge API
│   ├── client.py       # Anthropic Client wrapper
│   ├── config.py       # Configuration manager & Secure Storage
│   ├── conversation.py # Conversation JSON manager (Legacy)
│   ├── db.py           # SQLite database layer & JSON migrator
│   └── ui/             # Web interface files
│       ├── index.html  # Application HTML layout
│       ├── style.css   # HSL Catppuccin Mocha styles
│       ├── app.js      # Frontend interaction logic & stream callbacks
│       └── libs/       # Localized libraries (marked, highlight, mermaid)
├── config.json         # Runtime configuration file (git-ignored)
├── claude_chat.db      # SQLite database file (git-ignored)
├── conversations/      # Legacy chat history directory (git-ignored)
└── conversations_backup/# Legacy chat history archives after migration (git-ignored)
```

### config.json

```json
{
  "api_key": "sk-ant-...",
  "model": "claude-3-7-sonnet-20250219",
  "temperature": 1.0,
  "max_tokens": 4096,
  "thinking_enabled": true,
  "thinking_type": "enabled",
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
