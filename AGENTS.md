# AGENTS.md — Chatudex

The product name is Chatudex. `chatudex.py` and `chatudex.spec` are the canonical launch/build entrypoints; `claude_chat.py` remains a compatibility launcher. Keep the internal `claude_chat` package, database filenames, credential service names, encryption salts and sandbox identities stable so existing data and runtime permissions remain usable. Runtime icons live in `claude_chat/ui/icons/`; original artwork lives in `assets/branding/`.

## Run / Build

```bash
# GUI mode (Windows, pywebview + WebView2)
python chatudex.py

# Headless HTTP server mode
python chatudex.py --server --host 0.0.0.0 --port 8000

# Package to Chatudex.exe (uses chatudex.spec)
python build_executable.py

# Lint / format (line-length=120, target py310)
ruff check .
ruff format .
```

Setup scripts (`setup_venv.bat` / `setup_venv.ps1`) create `.venv/` and install `requirements.txt` plus `pyinstaller` and `ruff`. Neither `pyinstaller` nor `ruff` is in `requirements.txt`.

`chatudex.py` also accepts a hidden `--sandbox-worker <script.py>` mode: the PyInstaller-built `Chatudex.exe` re-invokes itself as the AppContainer Python runtime for the Windows code sandbox. It runs the given script via `runpy` and exits — it must not initialize the GUI, DB, config, or API clients.

## Tests

Run the whole suite with **pytest** (works offline — no API key needed for the collected tests):

```bash
python -m pytest test/ -q
```

The suite covers streaming, sandboxing, attachments, service architecture, request parameters, message formatting, and Anthropic HTTP client compatibility. `test_anthropic_http_client.py` exercises model discovery and cloud OCR with real SDK client validation and mocked API methods (no API key or network). `test/conftest.py` redirects configuration, database and attachment paths to a temporary directory and mocks the credential store before collection. It excludes manual integration scripts whose imports modify app state. Keep this isolation enabled when running pytest.

Additional standalone scripts (not pytest-collected):

```bash
# Safe (no side effects, no API key):
python test/test_stream_mock.py        # mock _process_sending_stream queue pump
python test/test_search_event_chain.py # search/fetch event chain simulation
python test/test_genai_msgs.py         # convert_messages_to_gemini unit test
python test/test_genai_types.py        # google.genai.types import smoke test
python test/test_fixed_regressions.py  # historical regression checks (prints "passed")
python test/test_thinking_tag_parser.py# streaming <thought> tag splitter

# Unsafe legacy manual probe; do not run against personal app data:
# python test/test_save_config.py      # Only isolates DB; rewrites real config.json on import

# Safe, uses memory/in-memory app stubs:
python test/test_file_upload.py        # attachment upload / prepare / discard paths
python test/test_attachment_preview.py # managed-attachment preview bounds
python test/test_sandbox.py            # sandbox launch helpers (fail-closed checks)

# JS module checks (run with node, no browser needed):
node test/test_stream_protocol_ui.js
node test/test_attachment_display.js
node test/test_conversation_render.js
node test/test_settings_components.js
node test/test_model_picker.js
node test/test_platform_select.js

# Require a real API key in config.json:
python test/test_ds.py / test_ds2.py / test_ds3.py   # DeepSeek connectivity
python test/test_ds_search.py                        # DeepSeek search tool loop
python test/test_gem_search.py                       # Gemini search integration
```

`scratch/` holds one-off Gemini SDK exploration scripts (development artifacts, not a test suite).

Verify Python syntax after editing:
```bash
python -m py_compile claude_chat/app.py claude_chat/api_bridge.py && echo OK
```

## Architecture

```
chatudex.py          thin entrypoint → ClaudeChatApp (+ --sandbox-worker runner)
claude_chat/
  app.py                ClaudeChatApp (mainloop, pywebview window, stream-task lifecycle,
                        GUI reader thread _process_sending_stream, model refresh)
  api_bridge.py         WebAPI facade: MRO of ConfigService, ConversationService,
                        FileService, ExecutionService, ModelService (shared by
                        pywebview js_api and the HTTP server)
  http_router.py        HttpApiRouter — endpoint param validation + dispatch;
                        transport (responses/streaming) stays in the handler
  server.py             ThreadingHTTPServer + ClaudeChatHTTPHandler (static files,
                        NDJSON stream transport, console stream, SSL, token auth)
  config.py             ConfigManager; BASE_DIR; key obfuscation (keyring/AES-GCM/XOR);
                        FALLBACK_MODELS; attachment size/extension limits
  db.py                 DatabaseManager (SQLite, WAL, incremental writes)
  search.py             web search (Google/Bing/DDG/Tavily/Jina) + webpage fetch,
                        SSRF-guarded redirects
  attachment_parser.py  OCR / PDF / Office parsing helpers
  platform_params.py    PlatformParamMapper — per-platform config → client params
  sandbox.py            code sandbox (Linux Docker / Windows AppContainer), fail-closed
  stream_protocol.py    typed StreamEvent/StreamEventQueue/StreamTask state machine
  clients/
    base.py             shared: sanitize_error_message, build_http_client,
                        build_anthropic_http_client (httpx2 for Claude), extract_api_message
    claude.py           Anthropic SDK streaming + web_search/fetch tool loop
    deepseek.py         OpenAI-compatible streaming + tool-call search + reasoning_content
    gemini.py           google-genai streaming + Google Search grounding + code sandbox
    dispatcher.py       stream_claude_response() — routes by active_platform (sole entrypoint)
    models.py           fetch_available_models + capability metadata + models.dev registry
    thinking_tag_parser.py  streaming <thought> tag splitter (aggregator models)
  services/
    base.py             AppService (holds _app reference)
    config_service.py   ConfigService (get/save config, parser checks, logs)
    conversation_service.py ConversationService (conversations, messages, stream start)
    execution_service.py   ExecutionService (sandbox env check / install / run / kill)
    file_service.py        FileService (clipboard, attachment select/upload/preview, export)
    model_service.py       ModelService (model discovery, custom-provider CRUD, registry)
    attachment_store.py    managed attachment storage + bounded preview generation
  ui/
    index.html          single-page app shell (loads libs/ then the application JS modules)
    state.js, stream_protocol.js, dom.js, utils.js, attachment_display.js, api.js,
    attachment_preview.js, ui.js, chat.js, conversation_render.js, sandbox_settings.js,
    settings_components.js, settings_claude.js, settings_deepseek.js, settings_gemini.js,
    settings.js, events.js, main.js, model_picker.js, select_picker.js,
    custom_params.js, model_config_editor.js    vanilla-JS modules, no framework
    style.css + workspace.css  base components + light/dark/mobile workspace
    settings.css          settings sidebar, category pages, grouped rows and mobile layout
    fonts.css + fonts/  woff2 (Inter, JetBrains Mono, Outfit)
    libs/               offline JS bundles (marked, highlight, mermaid, purify)
```

`WebAPI` is a thin compatibility facade composed from the domain services in `services/` (it no longer implements logic itself). `client.py` was split into the `clients/` package; `ui/app.js` was split into the JS modules above; `conversation.py` was **fully removed** (its `read_text_file` helper now lives in `services/conversation_service.py`). The one-off refactor scripts `split_app.py` / `split_client.py` and pre-split monoliths (`app.backup.py`, `client.backup.py`, `ui/app.backup.js`) have been removed; the later refactor helpers `refactor_js.py`, `refactor_settings.py`, `refactor_ui.py` are still tracked in git (development scripts that rewrote the UI split — do not re-run them on the current tree).

The HTTP server **always starts** even in GUI mode. GUI mode additionally opens a pywebview window with `js_api=WebAPI(...)`.

## BASE_DIR

Both `config.py` and `db.py` resolve `BASE_DIR` via:
```python
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent   # packaged .exe
else:
    BASE_DIR = Path(__file__).parent.parent  # dev: project root
```
`config.json`, `claude_chat.db`, `claude_chat.log`, `models_registry.json`, and SSL certs all live at `BASE_DIR`. These files are git-ignored and auto-created on first run.

## pywebview API — known gotchas

- `create_file_dialog` constant is `webview.SAVE_DIALOG`, **not** `webview.SAVE_FILE_DIALOG` (`webview.OPEN_DIALOG` for open).
- `directory` parameter must be a **string** (`''` for default), not `None` — newer pywebview calls `os.path.exists(directory)` unconditionally.
- `window.evaluate_js(...)` is the only path to push data from Python back to the frontend in GUI mode. Global JS callbacks: `window.onStreamEvent` (typed wire event), `window.onStreamMessage` (legacy tuple), `window.onModelsUpdated`, `window.onConsoleOutput`, `window.onConsoleExit`.
- WebView2 blocks `navigator.clipboard`; clipboard access uses `ctypes.windll.user32` directly (`WebAPI.paste_from_clipboard`, `services/file_service.py`).

## Streaming event protocol

Internal queues carry typed `StreamEvent` objects (`stream_protocol.py`); legacy `(msg_type, msg_data)` tuples are still accepted by `StreamEventQueue.put()` and re-emitted via `to_legacy()`:

| `msg_type` | `msg_data` |
|---|---|
| `"text"` | `str` chunk |
| `"thinking"` | `str` chunk |
| `"search_start"` | `{"query": ..., "id": ...}` |
| `"search_done"` | `{"query", "results", "engine", "usage"}` |
| `"fetch_start"` | `{"url": ...}` |
| `"fetch_done"` | `{"url", "content_len", "parser", "usage"}` |
| `"done"` | `{"input_tokens", "output_tokens", "content_blocks", "thinking"}` |
| `"aborted"` | `{}` |
| `"error"` | `str` |

The HTTP transport serializes each event to one NDJSON line via `event.to_wire()`; the GUI transport calls `window.onStreamEvent(wire)` (falling back to `onStreamMessage(type, data)`).

## Security token

Auto-generated 32-hex `security_token` is stored in `config.json` and **printed to stdout** at startup (never to the log file). All `/api/*` HTTP endpoints require it via `X-Security-Token` header, `Authorization: Bearer`, or `?token=` query param. The frontend stores it in `localStorage` and strips it from the address bar.

## API key storage

Three tiers in `config.py` — machine-fingerprint PBKDF2 key derivation (`config.py:177`) + AES-GCM-256 (`config.py:181`) + XOR fallback (`config.py:209`); save/dispatch logic in `config.py:450+` (keyring → AES-GCM-256 → XOR). API keys are **never written as plaintext** to `config.json`; the field stores `""` plus `<key>_storage` and `<key>_obfuscated` fields. `get_config()` returns `has_<key>` flags to the frontend instead of the values.

## Do NOT run these files

`claude_chat/patch_app.py`, `patch_deepseek_tool.py`, `patch_index.py`, `patch_model_sync.py` were one-off historical migration scripts that **rewrote source files in-place** via `str.replace()`. They have been **removed**; this note is kept so that anyone restoring them from history knows their targets (`ui/app.js`, `client.py`) no longer exist and the scripts contained hardcoded absolute dev-machine paths.

## Other non-obvious files

- `trace_startup.py` — profiling script; opens a pywebview window briefly.
- `test_dialog.py` — manual pywebview dialog probe (dev tool, ignored).
- `refactor_js.py` / `refactor_settings.py` / `refactor_ui.py` — historical UI-split scripts, still tracked; see Architecture note above.
- `app_js_mods*.txt`, `scratch/`, `debug.txt`, `models_registry.json` — developer artifacts / cached registry, not part of the app.
- `dist/Chatudex.exe` — PyInstaller build output; `build/` holds intermediate artifacts.

## Legacy migration

On every startup, `DatabaseManager.__init__()` auto-migrates any `conversations/*.json` files into SQLite and archives originals to `conversations_backup/`. `conversation.py` / `ConversationManager` was **removed** — do not use it for new code.

## No CI

No `.github/` directory, no CI workflows, no pre-commit hooks.

## Anthropic HTTP compatibility

Anthropic SDK 1.x uses `httpx2`. Every `Anthropic(http_client=...)` call must use `build_anthropic_http_client`, including model discovery and cloud OCR. Keep the generic `build_http_client` for other integrations. Declare `httpx2` directly in both `requirements.txt` and `pyproject.toml`; the Windows spec also includes it. When changing these entry points, run `python -m pytest test/test_anthropic_http_client.py -q`.

## Manual model IDs

The settings dialog saves `manual_model_ids` as a provider-to-ID-list mapping. `updateModelList` merges only the active provider's IDs into discovery results without duplicates. Preserve this behavior for empty discovery results and provider switches; `test/test_model_picker.js` covers these cases.


## Settings workspace

`ui/settings.js` owns category navigation and label/description search; `ui/settings.css` is loaded after the workspace styles. Categories are `general`, `appearance`, `presets`, `providers`, `model`, `custom`, `files`, and `server`. Only the selected `[data-settings-section]` is visible via its `hidden` attribute. Preserve control IDs and drafts across navigation: global Save reads every category, including hidden ones. Invalid model JSON/custom parameters must reveal the model page; save failures remain visible in the settings footer. Theme selection applies immediately. The assistant-presets shortcut opens `presets`.

Search must never index input values or credentials. Navigation supports arrow keys/Home/End; Escape clears a nonempty search before closing the dialog. `test/test_settings_navigation_browser.js` covers navigation, search, cross-page drafts/validation/save, themes, mobile overflow and preset dialogs with local assets and mocked APIs. Run it with Node and Playwright/Edge; set `PLAYWRIGHT_MODULE` if needed. Also run `test/test_model_config_editor_browser.js` with `PYTHON_EXECUTABLE` pointing to the project environment for parameter-editor regressions.

## Memory and theme

`memory_store.py` owns SQLite memory, privacy, retrieval, revisions and forgetting hashes. `services/memory_service.py` adds memory operations to WebAPI; HTTP uses authenticated `POST /api/memory/<action>`. Background extraction follows the current or selected configured provider, runs after 3 new user messages with a 5-minute interval, and requires verbatim user evidence. Preserve epoch checks, manual-edit precedence, source existence checks and privacy inheritance when changing these paths. Never reintroduce automatic conversation deletion on listing.

`ui/theme.js` applies persistent light/dark/system mode before paint; `workspace.css` and `workspace.js` add mobile layout and memory controls. New regression coverage: `test/test_memory.py`, `test/test_gemini_http_client.py`, `test/test_workspace_browser.js`, `test/test_workspace_http_browser.js`. Browser checks use Playwright with Edge; set PLAYWRIGHT_MODULE to the available installation and PYTHON_EXECUTABLE to the project virtual environment for full HTTP checks. They isolate config/DB/credentials and do not call real models.

## Hybrid memory

`memory_schema.py` performs additive migrations; `memory_resolver.py` owns typed fields/conflicts/versions; `memory_embeddings.py` handles independent embedding transports; `memory_vectors.py` indexes local float32 vectors; `memory_hybrid.py` merges retrieval and builds bounded context. The UI extension is `ui/memory_advanced.js`. Cloud/keyword modes use existing dependencies; local Sentence Transformers is optional. Do not download models or invoke paid embeddings during automated checks.

Run `python -m pytest test/test_memory.py test/test_memory_hybrid.py -q` for real isolated SQLite regressions. `python test/test_memory_benchmark.py` produces a controlled offline benchmark in `scratch/memory-validation.json`; its recall results verify the pipeline, not real-model quality. Preserve epoch/content/version checks before async write-back, scope inheritance, verbatim live user evidence, atomic import/delete, unchanged-setting index retention and automatic retry backoff. JSON export v2 includes historical versions; imports keep external provenance untrusted.

Embedding model discovery uses `memory_operation("embedding_models", {"platform": ...})`, separate from chat-model discovery. `memory_embeddings.fetch_embedding_models` filters Gemini embedding actions, uses OpenRouter's dedicated catalog, and filters compatible catalogs by capabilities/name. Reuse configured keys/proxies/models URL, keep network I/O outside the app lock, never generate paid vectors to discover models, and retain manual IDs on errors/empty lists. `test/test_embedding_model_discovery.py` and the HTTP browser regression cover discovery, selection, retry and stale-provider responses.
