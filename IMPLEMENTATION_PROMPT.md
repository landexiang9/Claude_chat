# Chatudex — 从零实现提示词（保留扩展性版）

> 用途：把下面【主提示词】整段复制给一个具备文件读写与命令执行能力的 AI 编码代理，让它从空目录开始完整实现本项目。
>
> 使用要点：
> 1. **不要让 AI 一次做完。** 主提示词末尾的"分阶段交付"是硬性要求，按里程碑逐轮推进，每轮结束跑验收。
> 2. 第一轮只让它产出 `docs/ARCHITECTURE.md` + 骨架 + 一个平台（Claude），确认架构对了再放行后续阶段。
> 3. 项目路径、Python 版本等已知信息已写在提示词里；如与实际不符，让 AI 先确认再动手。

---

## 主提示词（整段复制）

````text
你是一名资深 Python 桌面应用架构师。请从一个空目录开始，完整实现一个名为
「Chatudex」的多平台 AI 聊天客户端，包括后端、前端、测试与打包配置。

这不是一次性脚本任务：代码会被长期维护，后续要不断新增模型平台、新增请求协议、
新增 UI 面板。因此「可扩展性」和「功能完整」是同等优先级的验收项，而不是加分项。

════════════════════════════════════════
一、产品目标
════════════════════════════════════════

一个可在本机图形界面运行、也可作为无界面 HTTP 服务运行的多平台 AI 聊天客户端。

必须支持的能力（全部要实现，不许用 TODO 占位）：

1. 多平台对话
   - Claude（Anthropic）、DeepSeek、Google Gemini 三个内置平台。
   - 用户可自行添加任意数量的「自定义供应商」，指定名称、API 地址、密钥、模型列表。
   - 运行时切换平台与模型，切换后当前会话的上下文不丢失。
   - 平台感知校验：不允许把 Claude 的模型名发给 DeepSeek 之类的跨平台错误。

2. 流式输出
   - 逐字流式渲染回答正文。
   - 推理/思维链单独成通道，可在 UI 折叠展开。
   - 用户随时中断生成；已生成的部分必须持久化，并标记为「已中断」。

3. 联网搜索与网页读取
   - 会话级开关，可按平台独立配置。
   - 搜索引擎：Google / Bing / DuckDuckGo / Tavily(API) / Jina(API)。
   - 搜索过程实时可视化（搜索中卡片 → 展开显示标题/URL/摘要），并写入会话历史。
   - 网页正文读取：本地解析 或 Jina Reader，二者可切换。
   - 平台原生搜索优先：Claude 用官方 web_search 工具、Gemini 用 Google Search grounding、
     DeepSeek 用 tool-call 循环自己实现。三者行为要对用户一致。

4. 附件与多模态
   - 图片（png/jpg/jpeg/gif/webp）、PDF、Office（docx/xlsx/pptx）、30+ 种纯文本格式。
   - 支持选择文件、拖拽上传、剪贴板粘贴。
   - 多模态平台走原生内容块；纯文本平台自动降级为 OCR/本地解析后的文本。
   - OCR 模式：auto / 原生视觉 / 本地 EasyOCR / 云端 Gemini OCR。
   - 附件必须落盘到受管目录，前端只看预览 ID，不允许前端持有服务器真实路径。

5. 会话管理
   - 会话与消息持久化（SQLite + WAL）。
   - 重命名、删除、自动清理旧会话。
   - 重新生成上一条回答、编辑任意历史用户消息并重发、从任意消息处分支出新会话。
   - 每条消息可查看原始数据包（DB 记录 + 实际 API 请求载荷），且预览里不得泄露真实路径与密钥。

6. 生成参数控制
   - 全局默认 + 每模型覆盖（max_tokens、temperature、thinking 配置）。
   - 每模型可编辑「最终请求参数 JSON」，带实时预览与严格校验。
   - 每模型可附加自定义参数（custom params），但与程序保留字段冲突时必须报错。

7. UI
   - 单页应用，Catppuccin Mocha 深色主题，玻璃拟态面板，动效克制。
   - Markdown 渲染 + 代码高亮 + Mermaid + SVG/HTML 产物侧栏（全部沙箱化渲染）。
   - 系统提示词管理器（保存/编辑/切换预设）。
   - 全局设置弹窗，按平台分标签页。
   - 完全离线：marked / highlight.js / mermaid / DOMPurify 必须是本地文件，禁止 CDN。

8. 代码沙箱（默认关闭，需用户在设置里显式开启）
   - 可执行回答中的 Python / JavaScript 代码块。
   - 每个代码块下方有交互式终端，支持 stdin 输入、输出流式回传、随时终止。
   - Windows 用 AppContainer + Job Object 限额；Linux 用 Docker。
   - 环境检测必须 fail-closed：检测不到安全能力时拒绝执行，而不是降级为裸跑。

9. 双运行形态
   - GUI 模式：pywebview 窗口 + WebView2。
   - 服务端模式：纯 HTTP 服务，供局域网/移动端浏览器访问。
   - 无论哪种模式，内置 HTTP 服务都必须启动（GUI 模式也启动）。
   - 静态资源、NDJSON 流式传输、SSL、Token 鉴权齐全。

════════════════════════════════════════
二、技术栈约束（不可替换）
════════════════════════════════════════

- Python 3.10+（可用 3.10 的类型语法，如 `str | None`）。
- 桌面外壳：pywebview（Windows 走 WebView2）。
- 前端：原生 HTML/CSS/JavaScript（ES2020），**无框架、无构建步骤、无 npm 依赖**。
- 持久化：SQLite（WAL 模式）。
- 依赖清单（严格限定，不要引入额外第三方库）：
  pywebview>=5.0, anthropic>=1.2.0, openai>=2.0.0, google-genai>=1.0.0, Pillow>=10.0.0,
  keyring>=24.0.0, httpx>=0.24.0, httpx2>=2.0.0,<3, cryptography>=42.0.0,
  beautifulsoup4>=4.12.0, python-docx>=1.1.0, openpyxl>=3.1.0, python-pptx>=0.6.23,
  pypdf>=4.0.0
  开发依赖（单独说明，不写进运行时依赖）：pytest、ruff、pyinstaller。
- 代码风格：行宽 120，ruff check + ruff format 必须零告警。
- 所有面向用户的文案与日志用简体中文；代码标识符用英文。

════════════════════════════════════════
三、架构分层与依赖方向（最重要的一节）
════════════════════════════════════════

严格五层，依赖只能自上而下，禁止反向 import、禁止跨层抄近路：

  ┌ UI 层        ui/*.js, index.html, style.css —— 只管渲染与交互
  ├ 桥接层       server.py（HTTP 传输）/ pywebview js_api —— 只管传输
  ├ 路由层       http_router.py —— 只管参数校验与分发，禁止业务逻辑
  ├ 服务层       services/*.py —— 领域编排：会话、配置、文件、执行、模型
  ├ 客户端层     clients/*.py —— 只与某个供应商 SDK/HTTP 打交道
  └ 基础设施     config.py, db.py, search.py, sandbox.py, attachment_parser.py, stream_protocol.py

硬性规则：

R1. 唯一入口 facade：`WebAPI` 必须是一个**只做多重继承组合、不含任何业务逻辑**的类，
    由 5 个领域服务混入组成：ConfigService、ConversationService、FileService、
    ExecutionService、ModelService。它的公开方法名同时就是 pywebview 的
    `window.pywebview.api.*` 与 HTTP 端点所调用的名字，必须保持稳定。
    该文件的体积上限 5KB，超出即视为架构违规。

R2. HTTP 路由零业务：server.py 里不得出现 `handle_api_get` / `handle_api_post` 这类方法；
    所有 `/api/*` 路由必须由 HttpApiRouter 统一 dispatch（GET/POST/DELETE 各一个入口），
    负责参数类型校验、把非法参数转成 400，然后把调用转给 `WebAPI`。

R3. 客户端层不许知道 UI：clients/ 不得 import services/ 或 server.py。
    客户端统一签名：接收 `streaming_queue`、`abort_event`、`on_stream_created`，
    把事件塞进队列，函数本身不返回渲染结果。

R4. 单一 SSE/流式入口：所有流式请求都走 `clients/dispatcher.py` 的
    `stream_claude_response(...)`（名字保持兼容即可，语义是「按 active_platform 与
    provider_adapter 路由」）。UI 与 HTTP 层永远不直接调用具体客户端函数。

════════════════════════════════════════
四、扩展性契约（本项目的核心，必须按此实现）
════════════════════════════════════════

「保留拓展性」不是口号，而是下面四条可测试的接入协议。请把每条都做成注册表/适配器，
而不是散落的 if-else。

────────────────────────────────
契约 A：新增一个模型平台 = 新增文件 + 注册，不改公共文件
────────────────────────────────
- 每个平台一个独立模块 `clients/<platform>.py`。
- 提供平台注册表，把「平台 ID」映射到「客户端函数 + 参数映射 + 能力声明」。
- 新增平台时必须能够做到：只新建 `clients/newplatform.py`、写一份
  `ui/settings_newplatform.js`、在注册表和 index.html 中各加一行，而**不需要**
  修改 dispatcher 的路由逻辑、conversation_service 的发送逻辑、ui.js 的渲染逻辑。
- 如果你发现要改公共文件，说明抽象错了，请回去改注册表设计，而不是加 if。

────────────────────────────────
契约 B：新增一个请求协议/传输形态 = 新增一个 adapter
────────────────────────────────
- 同一个供应商可能说不同协议：Chat Completions、OpenAI Responses、Anthropic Messages、
  Gemini generateContent；同一份用户配置要能切换协议而不改供应商身份。
- 实现 adapter 注册表，至少包含：local、openai_files、responses、openrouter、
  inline_images、anthropic、gemini。
- 必须提供 `normalize_custom_provider_adapter(value, legacy_bool)`：
  接受别名（openai→openai_files、openai_responses→responses、inline→inline_images …）、
  把旧的布尔开关迁移成字符串 adapter、遇到不认识的值回落默认值而不是抛异常。
- 必须提供 3 个小判定函数供上层使用，不要在上层写死平台名：
  `adapter_accepts_attachment(adapter, extension)`
  `adapter_uses_remote_files_api(adapter)`
  `adapter_expiry_limit(adapter)`
- 每平台的配置归一化集中在一个 `PlatformParamMapper.map_params(platform, config, model_id)`
  里，返回**统一形状的参数字典**（api_key / max_tokens / temperature / thinking_config /
  request_params / provider_adapter / file_upload_* 等），客户端只认这个形状。

────────────────────────────────
契约 C：生成参数的构造与校验必须与 UI 预览共用同一份代码
────────────────────────────────
- 提供 `generation_params(platform, model, ...)`：由统一参数形状构造真正发给 SDK 的参数。
- 提供 `validate_request_params(value, platform)`：强校验，含
  「Claude 开启 thinking 时 temperature 只能为 1」这类平台规则，
  并把 Responses 协议的历史字段自动迁移（max_tokens→max_output_tokens、
  reasoning_effort→reasoning.effort）。
- 提供 `preview_generation_params(...)` 给设置面板做「实际会发什么」的预览。
- 三者必须共用同一实现；**禁止**前端用另一套 JS 逻辑猜参数。
- 自定义参数校验：键名必须匹配 `[A-Za-z_][A-Za-z0-9_]*`，值必须是合法有限 JSON，
  且不得覆盖保留字段（model / messages / tools / stream / api_key / input / instructions /
  previous_response_id / __proto__ / constructor / prototype 等）。

────────────────────────────────
契约 D：前端平台差异 = 一个注册组件，不改设置编排器
────────────────────────────────
- 提供全局注册表 `PlatformSettings`（register / loadAll / saveAll / bindAll）。
- 每个平台一个 `ui/settings_<platform>.js`，调用
  `PlatformSettings.register("<platform>", { bind(), load(config), save(config) })`，
  自己负责本平台面板的 DOM 读写与联动显隐。
- `settings.js` 只调用 `PlatformSettings.loadAll(config)` / `saveAll(config)`，
  **不得**出现单平台专属的更新函数（例如 `updateThinkingSettingsUI()` 这种）。
- 脚本加载顺序必须保证：`settings_components.js` → 各 `settings_<platform>.js` → `settings.js`。
  请写一个测试断言这个顺序，防止后续被改坏。
- 同理，模型选择器、平台选择器、参数编辑器都做成独立模块并挂到 `window` 命名空间
  （如 `window.ModelPicker`），由编排层探测存在性后使用，而不是直接内联调用。

════════════════════════════════════════
五、流式事件协议（版本化，类型化）
════════════════════════════════════════

- 事件类型枚举（字符串值固定，不许改名）：
  text, thinking, search_start, search_done, fetch_start, fetch_done, done, aborted, error
- 任务状态机：starting → running → cancelling → completed / aborted / failed。
  只允许合法转换，非法转换必须抛异常而不是静默忽略。
- 事件结构（wire 格式，同时用于 HTTP NDJSON 与 GUI evaluate_js）：
  { version, task_id, conversation_id, sequence, type, task_state, data }
- `STREAM_PROTOCOL_VERSION = 1`；前后端都要校验版本，不匹配时前端拒收并报错。
- 队列适配器必须**同时接受**类型化事件对象与历史遗留的 `(type, data)` 二元组
  （二元组自动升级为类型化事件），历史脚本用 `.get()` 仍能解包二元组。
- 事件必须带自增 `sequence` 与 `task_id`；前端必须丢弃（a）属于旧任务的事件、
  （b）序号小于等于已处理序号的事件。
- 中止语义：`request_abort()` 设置 abort_event 并关闭当前流；若关闭流发生在
  `bind_stream()` 之前（取消赢得竞态），绑定那一刻必须立刻关闭该流，不能泄漏。
- 平台专用事件数据不得污染通用字段：例如 Responses 协议的续传元数据放在文本块的
  `_responses` 子对象里，最终回答持久化时要保留它，否则多轮上下文会断。

════════════════════════════════════════
六、目录与文件结构（请照此创建，职责见括号）
════════════════════════════════════════

chatudex.py                     （薄入口：解析参数、启动 App；含隐藏的
                                    --sandbox-worker 模式，供打包后的 exe 以
                                    AppContainer 运行时身份执行脚本，该模式
                                    不得初始化 GUI/DB/配置/API 客户端）
claude_chat/
  app.py                           （ClaudeChatApp：窗口、流任务生命周期、
                                    GUI 读取线程 _process_sending_stream、模型后台刷新）
  api_bridge.py                    （WebAPI facade，<5KB，纯组合）
  server.py                        （ThreadingHTTPServer + Handler：静态文件、
                                    NDJSON 流、控制台流、SSL、Token 鉴权）
  http_router.py                   （HttpApiRouter：校验 + 分发，零业务）
  config.py                        （ConfigManager、BASE_DIR、密钥三层存储、
                                    FALLBACK_MODELS、附件大小/扩展名限制、日志）
  db.py                            （DatabaseManager：SQLite/WAL、增量写、
                                    启动时自动迁移 conversations/*.json 并归档）
  stream_protocol.py               （StreamEvent / StreamEventQueue / StreamTask）
  search.py                        （多引擎搜索 + 网页抓取，SSRF 防护）
  sandbox.py                       （Windows AppContainer / Linux Docker 沙箱，fail-closed）
  attachment_parser.py             （OCR / PDF / Office 解析）
  platform_params.py               （PlatformParamMapper，契约 B）
  provider_adapters.py             （adapter 注册表与迁移，契约 B）
  request_params.py                （generation_params / validate / preview，契约 C）
  custom_params.py                 （自定义参数校验，契约 C）
  clients/
    base.py                        （代理客户端构造、错误脱敏、消息结构提取）
    dispatcher.py                  （唯一流式入口，按 platform + adapter 路由）
    claude.py                      （Anthropic 流式 + 原生搜索/抓取工具循环 + Files API）
    deepseek.py                    （OpenAI 兼容流式 + tool-call 搜索 + reasoning_content）
    gemini.py                      （google-genai 流式 + Google Search grounding + 代码沙箱）
    responses.py                   （Responses 协议流式，含原生搜索与续传元数据）
    models.py                      （模型发现 + 能力元数据 + models.dev 注册表缓存）
    file_uploads.py                （Anthropic/OpenAI/Gemini 文件上传与缓存命名空间）
    thinking_tag_parser.py         （聚合模型的 <thought> 标签流式切分器）
  services/
    base.py                        （AppService，持有 _app 引用）
    config_service.py              （配置读写、解析器自检、日志查看/清理、参数预览）
    conversation_service.py        （会话/消息/发送/重发/分支/中断编排）
    file_service.py                （剪贴板、附件选择/上传/预览/丢弃、导出）
    execution_service.py           （沙箱环境检测/安装/执行/终止/stdin）
    model_service.py               （模型发现、自定义供应商 CRUD、注册表）
    attachment_store.py            （受管附件存储 + 有界预览生成）
  ui/
    index.html, style.css, fonts.css, fonts/（woff2 字体）
    state.js, stream_protocol.js, dom.js, utils.js,
    api.js, model_picker.js, select_picker.js, attachment_display.js,
    attachment_preview.js, ui.js, chat.js, conversation_render.js,
    sandbox_settings.js, settings_components.js,
    settings_claude.js, settings_deepseek.js, settings_gemini.js,
    custom_params.js, model_config_editor.js, settings.js, events.js, main.js
    libs/                          （marked / highlight.js / mermaid / DOMPurify 本地包）
test/
  conftest.py                      （隔离：把配置/DB/附件路径重定向到临时目录，
                                    并 mock 凭据存储；绝不读写用户真实数据）
  test_*.py, test_*.js             （见第八节的验收要求）
build_executable.py, chatudex.spec, requirements.txt, pyproject.toml,
setup_venv.bat, setup_venv.ps1, README.md, README_ZH.md, AGENTS.md, .gitignore

文件体积纪律：单个源文件不得超过 1200 行。超过就说明该拆了，请主动拆分并更新文档。

════════════════════════════════════════
七、安全与持久化契约（逐条实现，不许简化）
════════════════════════════════════════

S1. BASE_DIR 解析：
    打包态（sys.frozen）取 `Path(sys.executable).parent`，开发态取项目根。
    config.json、claude_chat.db、claude_chat.log、models_registry.json、SSL 证书都放这里，
    全部 git-ignore，首次运行自动创建。

S2. API 密钥三层存储，**永不写入明文**：
    OS keyring（Windows 凭据管理器 / macOS Keychain / Linux libsecret）
    → PBKDF2（机器指纹派生）+ AES-256-GCM 本地加密
    → XOR 混淆兜底。
    配置里字段写空字符串，另存 `<key>_storage` 与 `<key>_obfuscated`；
    对外 `get_config()` 只返回 `has_<key>` 布尔标志，绝不返回密钥本身。

S3. 安全令牌：启动时生成 32 位 hex 的 security_token 存入 config.json，
    **打印到 stdout（绝不写日志文件）**。所有 `/api/*` 必须校验，
    支持 `X-Security-Token` 头、`Authorization: Bearer`、`?token=` 查询参数三种方式。
    前端存 localStorage 并从地址栏抹掉。
    Token 缺失/错误时，静态文件与 API 都不得泄露内容。

S4. 附件存储：只允许「受管目录 + 随机 preview_id」寻址；
    校验 preview_id 格式、拒绝符号链接、限制元数据体积、限制预览文本/图片字节数与像素数，
    图片预览要按最长边缩放。前端永远拿不到真实文件路径。

S5. 错误脱敏：任何返回给用户或写进消息历史的错误文本，
    必须先脱敏 `sk-ant-*`、`sk-*`、`AIzaSy*` 等密钥样式，并截断到合理长度，
    完整堆栈只进本地日志。

S6. SSRF 防护：网页抓取与搜索重定向必须校验目标 URL，
    拒绝环回、私网、链路本地地址与非 http(s) 协议，限制重定向次数。

S7. 前端渲染安全：所有模型输出经 DOMPurify 清洗；
    Mermaid、SVG、HTML 产物一律在隔离 iframe 中渲染，禁止直接 innerHTML 注入未经清洗的内容。

S8. 沙箱 fail-closed：检测不到 AppContainer/Docker 能力时，执行入口直接报错并提示用户
    去设置里安装/启用，绝不「退化为直接执行」。

S9. 测试隔离：pytest 收集期就重定向配置/DB/附件路径并 mock keyring，
    保证测试永远不会碰到用户的真实数据。

════════════════════════════════════════
八、兼容性陷阱（这些是踩过的坑，必须按结论实现）
════════════════════════════════════════

P1. pywebview 常量名是 `webview.SAVE_DIALOG` / `OPEN_DIALOG`，
    没有 `SAVE_FILE_DIALOG`；`create_file_dialog` 的 `directory` 参数必须是字符串
    （默认传 `''`，不能传 None，新版会无条件 `os.path.exists(directory)`）。
P2. GUI 模式下 Python 推数据给前端唯一通道是 `window.evaluate_js(...)`。
    需暴露的全局回调：`onStreamEvent`（类型化事件）、`onStreamMessage`（兼容旧二元组）、
    `onModelsUpdated`、`onConsoleOutput`、`onConsoleExit`。
P3. WebView2 屏蔽 `navigator.clipboard`，剪贴板必须走 ctypes 调 user32。
P4. Anthropic SDK 1.x 依赖 `httpx2`：所有 `Anthropic(http_client=...)`
    （含模型发现、云端 OCR）必须用基于 httpx2 的客户端工厂；
    其他集成用通用 httpx 客户端工厂。`httpx2` 要同时写进 requirements.txt 与 pyproject.toml
    以及 PyInstaller spec。
P5. 图片内容块的预处理是**平台相关**的：只有 Claude 走 base64 图片块预处理；
    若对 DeepSeek/Custom 也做转换，Anthropic 格式的 image block 会被 OpenAI 转换器
    替换成 `[图片]`，同时丢失图片数据与原始 markdown。请在 dispatcher 层显式区分。
P6. Responses 协议必须保留 `_responses` 续传元数据，并在持久化最终回答时一并保存，
    否则下一轮会丢失上下文。
P7. 每协议的请求参数 JSON 必须分开保存（`request_params_by_protocol`）：
    在 Chat Completions 与 Responses 之间切换时，不得把 A 协议的字段带进 B 协议。
P8. `manual_model_ids` 按供应商保存 ID 列表；合并进模型发现结果时，
    只合并当前激活供应商的 ID，且不得重复；发现结果为空与供应商切换都要保持正确。
    请为这三种情况各写一个测试（含 JS 测试）。
P9. 聚合模型（如把 reasoning 混在正文里的供应商）需要流式 `<thought>` 标签切分器，
    必须能跨 chunk 处理被切断的标签，并能处理只有开标签没有闭标签的收尾情况。
P10. 用户删除或更换平台后，settings 面板的联动显隐（如 Tavily/Jina 密钥组）
     必须由各自平台组件负责，不能写进通用设置编排器。

════════════════════════════════════════
九、反模式（出现即视为未完成，必须重做）
════────────────────────────────────────
X1. 用 if-else 堆平台差异。任何形如 `if active_platform == "xxx"` 的分支，
    如果它决定的是「用哪个客户端 / 用哪个协议 / 用哪组配置键」，都必须改成注册表或适配器查询。
    唯一允许保留平台名的位置：注册表声明处、各平台自己的 settings 组件、
    FALLBACK_MODELS 常量、以及确有平台语义的参数字典构造。
X2. WebAPI 里塞业务逻辑；server.py 里写路由分支；路由层做数据库操作。
X3. 前端猜参数：JS 里再实现一遍「实际会发什么请求」的逻辑。
X4. 任何形式的明文密钥落盘、密钥进日志、密钥进错误消息。
X5. 阻塞主线程：模型发现、搜索、抓取、沙箱执行全部必须异步或走线程 + 队列。
X6. 假实现：用 `pass`、`TODO`、`raise NotImplementedError`、写死假数据来凑功能。
    宁可明确告诉我某能力做不到，也不要留空壳。
X7. 单个文件超过 1200 行；或者把不同平台逻辑揉进同一个函数靠参数分叉。
X8. 引入 CDN 或运行时 npm 依赖，破坏离线可用性。

════════════════════════════════════════
十、测试与验收（我会按这些条目逐条检查）
════════════════════════════════════════

必须提供 `python -m pytest test/ -q` 一套可离线全绿的测试（不需要真实 API Key），
外加若干 `node test/*.js` 的前端模块测试。至少覆盖：

T1. WebAPI 仍是 5 个服务的多重继承组合，且 api_bridge.py < 5KB（用 ast 静态断言）。
T2. 每个领域服务确实拥有其预期的方法集合（用 ast 静态断言，防止逻辑回流到 facade）。
T3. server.py 中不存在 `handle_api_get/post`，且确实调用
    `HttpApiRouter(self).dispatch_get/post/delete`；路由层有 GET/POST/DELETE 三个入口。
T4. 脚本加载顺序：三个 `settings_<platform>.js` 都在 `settings.js` 之前；
    settings.js 里有 `PlatformSettings.loadAll/saveAll`，且不含单平台专属函数。
T5. 路由派发正确：`/api/models?platform=gemini` 会把 platform 传给服务层；
    `/api/message_packet/<id>/not-a-number` 返回 400；DELETE 路由不改写业务数据。
T6. 流协议：合法转换通过、非法转换抛错；二元组与类型化事件混用不炸；
    序号/任务 ID 去重生效；取消后绑定流会被立即关闭。
T7. 适配器迁移：别名归一、旧布尔开关迁移、未知值回落。
T8. 参数校验：保留字段被拒；thinking + temperature≠1 被拒；
    Responses 字段迁移正确；每协议参数隔离。
T9. 附件：路径穿越/符号链接/超大体积被拒；预览有界；纯文本平台降级为文本块。
T10. 密钥：保存后 config.json 中不含明文；`get_config()` 只回 has_* 标志；
     错误消息中的密钥被脱敏。
T11. 模型合并：manual_model_ids 在「发现为空 / 切换供应商 / 正常合并」三种情况下的行为
     （Python + JS 各一份）。
T12. 沙箱：未启用时执行被拒绝（fail-closed），且失败原因可读。
T13. Anthropic HTTP：模型发现与云端 OCR 都使用 httpx2 客户端工厂（mock API 方法，不联网）。

以及两条**扩展性验收**（我会亲自验证，请务必让它们成立）：

E1. 我在 `clients/` 下新建一个 `demo.py`、在注册表加一行、在 index.html 加一行
    并写一个 `settings_demo.js`，就能完成一个新平台的接入，且
    dispatcher.py / conversation_service.py / ui.js / settings.js **一行都不用改**。
    请把这条实际演练一遍，并在 `docs/ARCHITECTURE.md` 里写下你新增平台时到底改了哪些文件
    （逐文件列出，用来证明契约 A 成立）。
E2. 我新增一个请求协议 adapter（例如 `myproto`），只需在适配器注册表加一项
    并在 `generation_params` 里补该协议的分支，就能让任意供应商切过去。
    同样在文档里列出改动文件清单。

════════════════════════════════════════
十一、分阶段交付（必须按顺序，禁止跳阶段）
════════════════════════════════════════

每个阶段结束后：跑 `ruff check .`、`ruff format --check .`、`python -m pytest test/ -q`，
全绿才进入下一阶段；并在回复里用一段话汇报「本阶段做了什么 / 哪些验收项已验证 / 遗留什么」。

阶段 0 — 设计与骨架
  · 先输出 `docs/ARCHITECTURE.md`：分层图、依赖方向、四条扩展性契约的具体接口签名、
    事件协议表、目录职责表。
  · 建立目录、requirements.txt / pyproject.toml、setup 脚本、.gitignore、README 骨架。
  · 此时不要写业务逻辑。
  · 输出后停下来等我确认架构，再继续。

阶段 1 — 基础设施
  config.py（三层密钥存储 + 令牌 + BASE_DIR）、db.py（含 JSON 迁移）、
  stream_protocol.py、search.py、attachment_parser.py、clients/base.py、
  services/base.py + config_service.py。此阶段要能通过 T6/T8/T10/T13 中已可实现的部分。

阶段 2 — 服务层与桥接层
  五个领域服务、api_bridge.py、http_router.py、server.py（静态资源 + NDJSON + 令牌 + SSL）、
  chatudex.py 入口（含 --sandbox-worker）。此阶段要能通过 T1/T2/T3/T5。

阶段 3 — 单个平台打通（Claude）
  clients/claude.py、clients/dispatcher.py、clients/models.py、platform_params.py、
  provider_adapters.py、request_params.py、custom_params.py。
  目标是「端到端可用的单平台」：流式、思考、搜索、附件、中断全部真实可用。

阶段 4 — UI
  全部 ui/*.js 模块 + index.html + style.css + 本地 libs。
  必须通过 T4 与 T11（JS 部分），并实现契约 D。

阶段 5 — 其余平台
  deepseek.py、gemini.py、responses.py、file_uploads.py、thinking_tag_parser.py，
  以及各自的 settings 组件。此阶段要**顺手完成 E1 演练**：证明新增平台不改公共文件。

阶段 6 — 沙箱
  sandbox.py（Windows AppContainer + Job Object；Linux Docker）、execution_service.py、
  前端终端 UI、sandbox_settings.js。通过 T12。

阶段 7 — 打包与收尾
  build_executable.py、chatudex.spec、完整测试套件、README（中英）、AGENTS.md，
  跑通全部 T1–T13 与 E1–E2，最后给我一份「验收对照表」。

════════════════════════════════════════
十二、工作方式要求
════════════════════════════════════════

W1. 先思考再写码。每个阶段开始前，先用几句话说明你打算怎么实现、关键权衡是什么。
W2. 不确定就问，但只问真正阻塞的问题（最多一个），不要为了确认而打断节奏。
    能靠合理默认推进的就推进，并在文档里记下你选的默认值。
W3. 严禁假实现。做不到的能力要显式说明「本阶段未实现，原因 X，影响 Y」。
W4. 改动源码前先读文件。不要凭记忆改代码。
W5. 每完成一个阶段，更新 `docs/ARCHITECTURE.md` 与 `AGENTS.md`，
    写明：模块职责、扩展点在哪、新增平台/协议的步骤清单、已知陷阱。
    文档与代码不一致时，以代码为准并立刻修正文档。
W6. 绝不执行会破坏用户数据的操作（例如重写真实 config.json、删真实数据库）。
    测试一律走临时目录。
W7. 如果实现过程中你发现我给的某条约束（例如某常量值、某个固定签名）会导致更差的架构，
    停下来告诉我，给出替代方案与理由，而不是沉默地照做或沉默地违背。

现在开始：先做阶段 0，输出 `docs/ARCHITECTURE.md` 与骨架，然后停下等我确认。
````

---

## 精简版（上下文有限时用这个）

```text
从零实现「Chatudex」多平台 AI 聊天客户端（Python 3.10 + pywebview + 原生 JS + SQLite，
无前端框架/无构建步骤/全离线）。功能：Claude/DeepSeek/Gemini 三平台 + 自定义供应商、
流式输出与可中断、思维链通道、多引擎联网搜索与网页读取、图片/PDF/Office/文本附件与 OCR、
会话持久化与重试/编辑/分支、每模型生成参数与自定义参数、Catppuccin Mocha 深色 UI、
默认关闭的代码沙箱（Windows AppContainer / Linux Docker，fail-closed）、
GUI 与 HTTP 服务双形态（含 NDJSON 流与 Token 鉴权）。

架构硬约束（这是验收重点）：
1. 五层依赖单向：UI → 桥接(server.py) → 路由(http_router.py，零业务) →
   服务(services/*) → 客户端(clients/*) → 基础设施(config/db/search/sandbox)。
2. WebAPI 必须是 5 个领域服务(Config/Conversation/File/Execution/Model)的多重继承 facade，
   自身 <5KB、零业务逻辑。
3. 流式只有一个入口 clients/dispatcher.py，按「平台 + 协议 adapter」路由；
   事件协议版本化：{version, task_id, conversation_id, sequence, type, task_state, data}，
   type ∈ {text, thinking, search_start, search_done, fetch_start, fetch_done, done, aborted, error}；
   队列同时兼容类型化事件与旧 (type, data) 二元组。
4. 扩展性四契约，必须做成注册表/适配器，禁止 if-else 堆平台差异：
   A 新增平台 = 新增 clients/<p>.py + ui/settings_<p>.js + 注册表各一行，
     不改 dispatcher/conversation_service/ui.js/settings.js；
   B 新增协议 = 新增 adapter（local/openai_files/responses/openrouter/inline_images/
     anthropic/gemini），配置归一化集中在 PlatformParamMapper.map_params；
   C 参数构造 generation_params 与校验 validate_request_params 必须与 UI 预览共用同一实现；
   D 前端平台差异由 PlatformSettings.register(platform, {bind,load,save}) 承担，
     settings.js 只调 loadAll/saveAll。
5. 安全：密钥三层存储(keyring → PBKDF2+AES-GCM → XOR)且永不落明文、get_config 只回 has_* 标志；
   启动生成 32hex security_token 打印到 stdout 不入日志，所有 /api/* 校验；
   附件走受管目录 + preview_id，前端拿不到真实路径；错误文本脱敏；抓取做 SSRF 防护；
   渲染经 DOMPurify + 沙箱 iframe。

必须避开的坑：pywebview 用 SAVE_DIALOG/OPEN_DIALOG 且 directory 传 ''；
GUI 回推前端只能靠 window.evaluate_js；WebView2 剪贴板走 ctypes user32；
Anthropic SDK 1.x 必须用 httpx2 客户端工厂；base64 图片块预处理只对 Claude 生效；
Responses 的 _responses 续传元数据要持久化；每协议请求参数 JSON 分开保存；
manual_model_ids 合并只在当前供应商内去重；聚合模型需跨 chunk 的 <thought> 切分器。

交付方式：分 8 个阶段（骨架与文档 → 基础设施 → 服务与桥接 → 单平台打通 → UI →
其余平台 → 沙箱 → 打包收尾），每阶段跑 ruff + pytest 全绿再继续。
测试须离线可跑（pytest + node），其中必须包含：facade 体积/继承静态断言、
服务方法归属断言、路由零业务断言、设置脚本加载顺序断言、stream 状态机合法/非法转换、
adapter 迁移、参数保留字段与 thinking-temperature 规则、附件越权、密钥不落明文、
manual_model_ids 三态、沙箱 fail-closed、Anthropic httpx2。
另外必须真实演练一次「新增平台只改 4 个文件」并列出改动清单，作为扩展性证明。
禁止假实现（不许 pass/TODO/NotImplementedError 凑数），单文件 ≤1200 行，不允许 CDN。
先输出 docs/ARCHITECTURE.md 与骨架，停下等我确认，再继续。
```

---

## 为什么这样写（设计说明）

这份提示词里，真正决定成败的不是功能清单，而是三处：

1. **第五节「扩展性契约」把抽象写成了可测试的接口。** 只说"要有扩展性"，AI 会给你一个
   `if platform == ...` 的 switch；只有写明"新增平台时哪些文件必须不用改"，它才会去做注册表。
2. **第九节反模式 + 第十节 E1/E2 演练。** 让 AI 自己新增一个假平台并列出改动文件清单，
   这是唯一能证伪"扩展性"的方式——它会立刻暴露隐藏耦合。
3. **明确列出现有代码里真实存在的耦合点作为待重做的对象。** 值得知道的是：当前实现中
   `ui/model_config_editor.js`(24 处)、`ui/ui.js`(15 处)、`ui/chat.js`(10 处)、
   `services/conversation_service.py`(9 处) 都存在硬编码平台分支判断。如果你是在现有仓库上
   继续演进而不是从零重写，这些位置就是应该优先按契约 A/D 收口的地方。

另外提醒一点：这份提示词假设了若干具体常量与签名（如 `DEFAULT_MAX_TOKENS = 16384`、
`request_params` 等参数名）。它们与当前实现一致；如果你希望 AI 自由发挥，把第十节之后的
具体数值改成"由你决定并在文档中记录"，否则 AI 可能会自作主张改动导致接不上现有前端。
