# Claude Chat

[简体中文](./README_ZH.md) | **English**

A powerful desktop GUI / headless web client for **Claude (Anthropic)**, **DeepSeek**, and **Google Gemini** APIs, built with **pywebview** and modern web technologies. Features a premium **Catppuccin Mocha** dark theme, real-time streaming, multi-platform AI support with seamless model switching, web search integration, file attachments, collapsible reasoning/thinking steps, offline-first assets, secure credential storage, and SQLite database architecture.

## Key Features

### 🤖 Multi-Platform AI Support
- **Claude (Anthropic)** — Full support including Extended Thinking (adaptive/enabled), vision, PDF parsing, and native search grounding.
- **DeepSeek** — Full chat support including DeepSeek-V3 and DeepSeek-R1 (reasoning). Automatic tool-based web search.
- **Google Gemini** — Full support including Gemini 2.0 Flash, Gemini 1.5 Pro, and reasoning models. Native Gemini search grounding.
- **Instant model switching** — Switch platform and model from the top bar. Platform-aware validation prevents cross-platform model errors (e.g. sending a Claude model name to DeepSeek).
- **Dynamic model list** — Fetches available models from each platform's API in the background; falls back to a built-in list if the network is unavailable.

### 🔍 Web Search
- **Integrated web search** — Toggle search on/off per-session. Supports Google, Bing, DuckDuckGo, Tavily (API), and Jina (API).
- **Live search card UI** — A real-time radar animation card appears during search; expands to show result titles, URLs, and snippets. Search cards persist in the conversation history.
- **Deep webpage reading** — The AI can read full page content via local extraction or Jina Reader API.
- **Platform-native search** — Claude uses Anthropic's native web search tool; Gemini uses built-in Google Search grounding; DeepSeek uses tool-call based web search.

### 📎 File Attachments & Vision
- **Claude & Gemini** — Native multimodal: drag-and-drop or upload images (PNG/JPG/GIF/WebP) and PDFs directly as API content blocks.
- **DeepSeek** — Text-only API: images/PDFs are automatically extracted via OCR or local PDF parser and sent as text.
- **OCR Engine** — Auto-mode selects the best OCR: native vision (Claude/Gemini) for multimodal models, EasyOCR (local) or cloud OCR (Gemini Flash) for text-only platforms. Configurable via Settings.
- **Text file support** — `.py`, `.js`, `.md`, `.json`, `.csv`, `.sql`, and 30+ other text formats are read and injected as code blocks.

### 🧠 Extended Thinking / Reasoning
- **Claude** — Supports adaptive and enabled thinking modes, with configurable budget tokens and effort level.
- **DeepSeek-R1 / Gemini 2.0 Flash Thinking** — Reasoning traces are shown in a collapsible toggle panel with estimated token count.
- Thinking config is saved per-conversation.

### 🎨 Premium UI
- **Catppuccin Mocha** dark theme with HSL color system, glassmorphism panels, and micro-animations.
- **Artifacts side panel** — Renders SVG, sandboxed HTML iframes, and Mermaid diagrams in an isolated collapsible panel.
- **System prompts manager** — Save, edit, and switch preset system prompts from the Settings modal.
- **Stop generation** — Interrupt streaming at any time; partial responses are saved with an `🚫 Aborted` badge.
- **Retry / Edit / Branch** — Regenerate the last AI response, edit and resend any user message, or fork a new conversation from any point.
- **Raw packet inspector** — Click `📦` on any message to view the raw DB record and API payload JSON.

### 🛠️ Developer & Power Features
- **Local code sandbox** — Run Python/JavaScript code blocks directly in the app. A real-time interactive terminal appears below each code block with stdin support (must be manually enabled in settings for security).
- **Comprehensive security** — Uses strict DOMPurify sanitization to prevent XSS injections. Renders Mermaid diagrams and SVG graphics inside secure sandboxed environments.
- **Proxy support** — System proxy, no proxy, or custom HTTP proxy URL.
- **Secure credential storage** — API keys are stored in the OS keyring (Windows Credential Manager, macOS Keychain, Linux libsecret). Falls back to PBKDF2/AES-256-GCM encrypted local storage.
- **SQLite architecture** — All conversations and messages are stored in `claude_chat.db`. Auto-migrates legacy JSON conversation files on first boot.
- **Headless server mode** — Runs as a pure HTTP server for LAN/remote access from any browser (Linux, macOS, Android Termux, etc.).
- **Offline-first libraries** — `marked.js`, `highlight.js`, and `mermaid.js` are fully local; no external CDN requests.

---

## Requirements

- Python 3.10+
- API key(s) for one or more platforms:
  - **Anthropic Claude** — [console.anthropic.com](https://console.anthropic.com)
  - **DeepSeek** — [platform.deepseek.com](https://platform.deepseek.com)
  - **Google Gemini** — [aistudio.google.com](https://aistudio.google.com)
- Internet connection (for API calls)

---

## Quick Start

### Windows (Automated Setup)

Setup scripts create a `.venv`, install all dependencies, and configure the environment:

```bash
# Option 1: CMD
setup_venv.bat

# Option 2: PowerShell
.\setup_venv.ps1
```

Then run the app:
```bash
.venv\Scripts\python.exe claude_chat.py
```

### Linux / macOS / Android Termux (Headless Server Mode)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Start server accessible from any device on the LAN
python claude_chat.py --server --host 0.0.0.0 --port 8000
```
Navigate to `http://<device-ip>:8000` from any browser on the network.

### Build Windows Executable

```bash
.venv\Scripts\python.exe build_executable.py
# Output: dist/ClaudeChat.exe
```

---

## Dependencies

| Package | Version | Description |
|---------|---------|-------------|
| pywebview | >=5.0 | Desktop GUI container |
| anthropic | >=1.2.0 | Anthropic Claude SDK |
| openai | >=1.0.0 | DeepSeek (OpenAI-compatible) SDK |
| google-genai | >=1.0.0 | Google Gemini SDK |
| Pillow | >=10.0.0 | Image processing |
| keyring | >=24.0.0 | Secure OS credential storage |
| httpx | >=0.24.0 | HTTP client for search and other integrations |
| httpx2 | >=2.0.0,<3 | HTTP client for Anthropic SDK 1.x |
| cryptography | >=42.0.0 | Encrypted credential storage |
| beautifulsoup4 | >=4.12.0 | Web page parsing |

File parsing dependencies are installed by `requirements.txt`: `beautifulsoup4>=4.12.0`, `pypdf>=4.0.0`, `python-docx>=1.1.0`, `openpyxl>=3.1.0`, and `python-pptx>=0.6.23`.

Optional: `easyocr` for local image OCR on text-only platforms.

---

## Project Structure

```text
Claude_chat/
├── claude_chat.py          # Application launcher entry point
├── requirements.txt        # Python dependencies
├── setup_venv.bat          # Windows CMD setup script
├── setup_venv.ps1          # Windows PowerShell setup script
├── build_executable.py     # PyInstaller packaging script
├── pyproject.toml          # Project metadata & linting config
├── claude_chat/            # Core application source
│   ├── __init__.py         # Module initialization
│   ├── app.py              # GUI and streaming task lifecycle
│   ├── api_bridge.py       # Shared GUI/HTTP service facade
│   ├── services/           # Config, conversations, files, execution, models
│   ├── http_router.py      # HTTP endpoint validation and dispatch
│   ├── config.py           # Configuration & secure storage manager
│   ├── db.py               # SQLite database layer
│   ├── search.py           # Web search engine integration
│   ├── attachment_parser.py# File attachment parsing & OCR
│   ├── server.py           # HTTP server for headless/web mode
│   ├── clients/            # Multi-platform API clients
│   │   ├── __init__.py     # Client module exports
│   │   ├── base.py         # Separate httpx/httpx2 clients and shared helpers
│   │   ├── claude.py       # Anthropic Claude streaming client
│   │   ├── deepseek.py     # DeepSeek (OpenAI-compatible) streaming client
│   │   ├── gemini.py       # Google Gemini streaming client
│   │   ├── dispatcher.py   # Platform router & unified stream entry
│   │   └── models.py       # Dynamic model list fetching
│   └── ui/                 # Web interface
│       ├── index.html      # Application HTML layout
│       ├── style.css       # Catppuccin Mocha styles
│       ├── fonts.css       # Font definitions
│       ├── api.js          # API communication layer
│       ├── chat.js         # Chat message management
│       ├── dom.js          # DOM manipulation utilities
│       ├── events.js       # Event handlers & shortcuts
│       ├── main.js         # App initialization & stream callbacks
│       ├── model_picker.js # Searchable model picker
│       ├── select_picker.js# Searchable select adapter
│       ├── model_config_editor.js # Per-model request settings
│       ├── settings.js     # Settings panel logic
│       ├── state.js        # Application state management
│       ├── ui.js           # UI rendering & components
│       ├── utils.js        # General utility functions
│       └── libs/           # Localized JS libraries
├── config.json             # Runtime configuration (git-ignored)
├── claude_chat.db          # SQLite database (git-ignored)
└── conversations_backup/   # Migrated legacy JSON files (git-ignored)
```

---

## Configuration Reference (`config.json`)

```json
{
  "active_platform": "claude",
  "model": "claude-sonnet-4-6",
  "temperature": 1.0,
  "max_tokens": 16000,
  "thinking_enabled": false,
  "thinking_type": "adaptive",
  "thinking_budget": 16000,
  "thinking_level": "high",
  "enable_web_search": false,
  "web_search_engine": "google",
  "web_page_parser": "local",
  "proxy_mode": "system",
  "proxy_url": "",
  "ocr_mode": "auto",
  "ocr_cloud_model": "gemini"
}
```

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Enter` | Send message |
| `Shift+Enter` | Insert newline |

---

## Proxy Modes

1. **System proxy** — Reads `HTTP_PROXY` / `HTTPS_PROXY` environment variables.
2. **No proxy** — Forces direct connections, ignoring all proxy settings.
3. **Custom proxy** — Specify a proxy URL (e.g. `http://127.0.0.1:10808`).

---

## CLI Arguments

| Flag | Full Form | Description |
|------|-----------|-------------|
| `-s` | `--server` | Force headless web server mode (no GUI window) |
| | `--host <ip>` | Bind IP address (default: `127.0.0.1`; use `0.0.0.0` for LAN access) |
| `-p` | `--port <port>` | HTTP server port (default: `8000`) |


## Custom Model IDs

In Settings, select a provider and click **自定义模型 ID** (Custom model ID) in the model settings area. Enter the exact model ID accepted by that provider and save. The ID is used in API requests and retained when model discovery refreshes or returns no models. IDs are saved separately for each provider in `manual_model_ids`; adding an ID does not grant access to a model. Custom providers appear as a separate group in the provider picker.

## Updating a Linux Deployment

From your existing checkout, with its virtual environment activated:

```bash
git pull --ff-only
python -m pip install -r requirements.txt
python -m pip check
```

Restart the running service through your existing process manager. For a manually launched server, stop the old process and run:

```bash
python claude_chat.py --server --host 0.0.0.0 --port 8000
```

### Claude reports `Invalid http_client` / `httpx2.Client`

Anthropic SDK 1.x requires objects from `httpx2`. Passing `httpx.Client` fails during client construction, before any API request. This is an SDK compatibility issue on any operating system. Claude chat, model discovery, and cloud OCR must use `build_anthropic_http_client()` from `claude_chat/clients/base.py`. Other integrations retain their separate HTTP client builder.

Update both source and dependencies, then restart. Installing `httpx2` alone does not change callers that still construct `httpx.Client`. Check the interpreter used by the actual service:

```bash
python -c "import sys, anthropic, httpx2; print(sys.executable); print('anthropic', anthropic.__version__); print(httpx2.Client)"
```

## Development Checks

Install test and lint tools in the project environment:

```bash
python -m pip install pytest ruff
python -m pytest test/ -q
python -m pytest test/test_anthropic_http_client.py -q
node test/test_model_picker.js
node test/test_platform_select.js
```

The Claude HTTP regression tests use real SDK client validation with mocked API methods; they require no API key or network connection. They cover model discovery and cloud OCR. Node.js is needed only for the JavaScript checks. See `AGENTS.md` for the remaining checks and standalone integration scripts.
