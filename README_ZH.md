# Claude Chat 中文文档

[English](./README.md) | **简体中文**

Claude Chat 是一个功能强大的桌面 GUI / Web 客户端，支持 **Claude (Anthropic)**、**DeepSeek** 和 **Google Gemini** API，基于 **pywebview** 与现代网页技术构建。界面采用优雅的 **Catppuccin Mocha** 暗色主题，支持多平台 AI 切换、流式对话响应、联网搜索集成、文件附件上传、可折叠的推理思维轨迹（Reasoning/Thinking Steps）、离线本地静态资源、自定义系统提示词管理器、Artifacts 沙盒预览侧边栏、本地代码沙盒终端以及多平台命令行快速部署。

---

## 核心特性

### 🤖 多平台 AI 支持
- **Claude (Anthropic)** — 完整支持，包括 Extended Thinking 推理思维（自适应/启用模式）、多模态视觉、PDF 解析以及原生联网搜索。
- **DeepSeek** — 完整对话支持，包括 DeepSeek-V3 与 DeepSeek-R1（推理思维）。支持基于 Tool-call 的智能联网搜索。
- **Google Gemini** — 完整支持，包括 Gemini 2.0 Flash、Gemini 1.5 Pro 与 reasoning 推理模型。支持原生 Google Search Grounding 联网搜索。
- **即时模型切换** — 顶栏支持即时切换平台和模型。支持平台感知的安全校验，防止跨平台模型请求错误（例如将 Claude 模型发送给 Gemini 接口）。
- **动态模型列表** — 在后台自动从各平台 API 获取最新的可用模型列表；若网络不可用，则无缝降级到内置的备用模型列表。

### 🔍 联网搜索
- **集成的网页检索** — 可在会话中开启/关闭联网搜索。支持 Google、Bing、DuckDuckGo、Tavily (API) 和 Jina (API) 搜索引擎。
- **实时搜索卡片 UI** — 搜索时会显示精致的雷达扫描动画卡片；展开后可查看搜索结果的网页标题、链接和摘要。搜索卡片会持久化保存至对话历史中。
- **深度网页阅读** — AI 可通过本地提取器或 Jina Reader API 深度抓取并阅读完整的网页正文内容。
- **平台原生搜索** — Claude 使用 Anthropic 原生 search 工具；Gemini 使用内置的 Google Search Grounding；DeepSeek 使用基于 Tool-call 的联网搜索。

### 📎 文件附件与视觉
- **Claude & Gemini** — 原生多模态：支持拖拽或上传图片（PNG/JPG/GIF/WebP）与 PDF 文档，直接作为 API 内容块发送。
- **DeepSeek** — 纯文本 API：自动通过 OCR 或本地 PDF 解析器提取图片和文档内容，并以文本块形式发送。
- **OCR 引擎** — 自动模式下智能选择最佳 OCR 引擎：多模态模型直接使用原生视觉，纯文本模型则自动使用本地 EasyOCR 或云端 OCR (Gemini Flash) 解析。可在设置中配置。
- **纯文本文件读取** — 支持读取 `.py`, `.js`, `.md`, `.json`, `.csv`, `.sql` 等 30 多种文本格式，并自动将其注入为代码块。

### 🧠 推理思维链 (Reasoning)
- **Claude** — 支持自适应（adaptive）和启用（enabled）思维模式，可配置思考 Token 预算及推理强度级别。
- **DeepSeek-R1 / Gemini 2.0 Flash Thinking** — 推理过程将以折叠面板形式展示，支持实时输出和预估 Token 计数。
- 推理设置将按对话会话进行持久化保存。

### 🎨 极奢 UI 与微交互
- **Catppuccin Mocha** 调色板暗色主题，玻璃拟物化面板，流畅的微交互动画。
- **Artifacts 侧边栏沙盒预览** — 独立的右侧侧边栏，支持 SVG 矢量图渲染、在沙盒 `<iframe>` 中隔离执行 HTML 网页、以及利用本地 `mermaid.js` 实时渲染流程图与结构图。
- **系统提示词管理器** — 在设置窗口中轻松保存、编辑和切换预设的系统提示词（System Prompt）。
- **一键中止生成** — 随时点击切断流式输出，并在自动保存已生成内容的同时，在会话中标记 `🚫 已中止` 状态。
- **重试 / 编辑 / 分叉** — 允许重新生成最新回复、编辑并重发任何历史消息，或从任意消息节点分叉出一个全新的对话。
- **原始数据包审查** — 点击消息卡片上的 `📦` 图标，即可查看该消息在 SQLite 数据库中的原始记录和发送/接收的 API Payload JSON。

### 🛠️ 开发与高级特性
- **本地代码沙盒终端** — 支持在客户端本地安全执行模型输出的 Python/Javascript 代码块，并在下方实时生成交互式终端，支持 stdin 输入和手动终止进程（需在设置中手动开启以保证安全性）。
- **全方位安全防护** — 针对 AI 输出的渲染层进行了严格的 DOMPurify 净化，防范 XSS 注入；并采用安全的隔离环境渲染 Mermaid 图表与 SVG 矢量图。
- **代理配置** — 支持系统代理（继承环境变量）、直连无代理、或自定义 HTTP 代理服务器 URL。
- **安全凭证加密存储** — 在 Windows 系统下自动调用系统 `keyring` 将 API Key 安全存入系统保险箱；在 Linux/Termux 等无图形化环境下，自动降级为基于设备指纹和 PBKDF2/AES-GCM-256 的高强度本地加密存储。
- **SQLite 数据库底层** — 所有对话与消息列表均持久化存储于本地的 `claude_chat.db` 数据库中。首次启动时，程序会自动将 `conversations/` 下的老版本 JSON 对话文件迁移至 SQLite 并归档备份。
- **纯 Web 服务器模式 (Headless)** — 自动适配无 GUI 环境，支持在 Linux 服务器、Android Termux (手机端) 等环境下一键作为 Web 聊天室部署，支持局域网多设备访问。
- **离线优先静态资源** — `marked.js`, `highlight.js`, 和 `mermaid.js` 均为本地依赖，不请求外部 CDN。

---

## 环境要求

- **Python 3.10+**
- 至少一个平台的 API Key：
  - **Anthropic Claude** — [console.anthropic.com](https://console.anthropic.com)
  - **DeepSeek** — [platform.deepseek.com](https://platform.deepseek.com)
  - **Google Gemini** — [aistudio.google.com](https://aistudio.google.com)
- 互联网连接

---

## 快速安装与运行

### 方案 A：Windows 系统 (一键自动配置)

我们提供了 Windows 环境下的虚拟环境一键安装脚本，会自动创建本地虚拟环境、升级 pip、安装全部依赖包以及 Ruff、PyInstaller 打包工具：

1. **配置环境**：
   - 双击运行 **`setup_venv.bat`** (CMD 版本) 
   - 或在 PowerShell 中执行 **`setup_venv.ps1`**
2. **运行程序**：
   配置完成后，你可以随时通过下面的命令运行应用（优先启动桌面 GUI 窗口，如果 webview 库加载失败则自动降级为 Web 服务模式）：
   ```bash
   .venv\Scripts\python.exe claude_chat.py
   ```

### 方案 B：Linux / macOS / Android Termux 部署 (作为局域网 Web 聊天室)

1. **创建虚拟环境并安装依赖**：
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
2. **启动移动 Web 服务**：
   ```bash
   # 绑定 0.0.0.0 允许同局域网设备通过浏览器访问，指定运行在 8000 端口
   python claude_chat.py --server --host 0.0.0.0 --port 8000
   ```
   启动后，直接在其他设备的浏览器中输入 `http://<服务器IP>:8000` 即可开始使用。

### 方案 C：Windows 可执行文件一键打包 (无 Python 环境运行)

你可以将程序编译为单个独立的 `.exe` 文件：

1. 激活虚拟环境：
   ```bash
   call .venv\Scripts\activate.bat
   ```
2. 运行打包构建脚本：
   ```bash
   python build_executable.py
   ```
3. 编译完成后，您可以在生成的 **`dist/ClaudeChat.exe`** 找到独立运行包，支持快速拷贝拷贝和快速运行部署。

---

## 依赖说明

| 依赖包 | 版本要求 | 用途描述 |
| :--- | :--- | :--- |
| `pywebview` | >=5.0 | 桌面 GUI 窗口容器 |
| `anthropic` | >=0.103.0 | Anthropic Claude SDK |
| `openai` | >=1.0.0 | DeepSeek (OpenAI 兼容) SDK |
| `google-genai` | >=1.0.0 | Google Gemini SDK |
| `Pillow` | >=10.0.0 | 图像处理支持 |
| `keyring` | >=24.0.0 | 操作系统级安全凭证存储 |
| `httpx` | >=0.24.0 | 联网搜索/网络请求客户端 |

可选依赖（用于增强文件解析与本地 OCR）：
- `easyocr` — 纯文本平台本地图片 OCR
- `pypdf` — PDF 文本提取
- `python-docx` — Word 文档解析
- `openpyxl` — Excel 表格解析
- `python-pptx` — PowerPoint 演示文稿解析

---

## 项目结构

```text
Claude_chat/
├── claude_chat.py          # 应用启动入口文件
├── requirements.txt        # 核心依赖项列表
├── setup_venv.bat          # Windows CMD 一键配置脚本
├── setup_venv.ps1          # Windows PowerShell 一键配置脚本
├── build_executable.py     # PyInstaller 打包脚本
├── pyproject.toml          # 项目元数据与 Ruff 格式化配置
├── claude_chat/            # 核心业务源码目录
│   ├── __init__.py         # 包初始化
│   ├── app.py              # PyWebView GUI 逻辑与 JS 桥接 API
│   ├── api_bridge.py       # Headless 模式的 HTTP API 桥接层
│   ├── config.py           # 配置与凭证安全管理器
│   ├── conversation.py     # 历史 JSON 对话读取适配器
│   ├── db.py               # SQLite 数据库持久化层
│   ├── search.py           # 统一网页搜索引擎接口
│   ├── attachment_parser.py# 附件解析器与 OCR 管道
│   ├── server.py           # 局域网 Web 服务器实现 (Headless)
│   ├── clients/            # 多平台 API 客户端模块
│   │   ├── __init__.py     # 客户端模块导出
│   │   ├── base.py         # 共享工具函数 (HTTP 客户端、错误脱敏)
│   │   ├── claude.py       # Anthropic Claude 流式客户端
│   │   ├── deepseek.py     # DeepSeek (OpenAI 兼容) 流式客户端
│   │   ├── gemini.py       # Google Gemini 流式客户端
│   │   ├── dispatcher.py   # 平台路由与统一流式入口
│   │   └── models.py       # 动态模型列表获取
│   └── ui/                 # Web 前端静态资源
│       ├── index.html      # 主页面 HTML 布局
│       ├── style.css       # Catppuccin Mocha 玻璃拟物主题样式
│       ├── fonts.css       # 字体定义
│       ├── api.js          # API 通信层
│       ├── chat.js         # 聊天消息管理
│       ├── dom.js          # DOM 操作工具
│       ├── events.js       # 事件处理与快捷键
│       ├── main.js         # 应用初始化与流式回调
│       ├── settings.js     # 设置面板逻辑
│       ├── state.js        # 应用状态管理
│       ├── ui.js           # UI 渲染与组件
│       ├── utils.js        # 通用工具函数
│       └── libs/           # 离线打包 JS 依赖库
├── config.json             # 运行时配置 (Git 已忽略)
├── claude_chat.db          # SQLite 数据库 (Git 已忽略)
└── conversations_backup/   # 已迁移的 JSON 对话备份 (Git 已忽略)
```

---

## 配置文件参考 (`config.json`)

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

## 快捷键与操作

| 快捷键 | 触发操作 |
| :--- | :--- |
| `Ctrl+Enter` | 发送当前输入的消息 |
| `Shift+Enter` | 输入框换行 |

---

## 代理配置模式

1. **System proxy (系统代理)** — 自动继承并读取操作系统的环境变量（如 `HTTP_PROXY`, `HTTPS_PROXY`）。
2. **No proxy (无代理)** — 强制直连，忽略所有系统级代理配置。
3. **Custom proxy (自定义代理)** — 手动指定具体的代理网关服务器（如本地代理 `http://127.0.0.1:10809`）。

---

## 命令行参数一览

| 参数选项 | 全称 | 说明 |
| :--- | :--- | :--- |
| `-s` | `--server` | 强制以纯 Web 服务模式 (Headless) 运行，不显示本地图形窗口 |
| | `--host <ip>` | 指定 Web 服务绑定的网卡 IP (如 `0.0.0.0` 代表绑定所有网卡，默认为 `127.0.0.1`) |
| `-p` | `--port <port>` | 指定 Web 服务的监听端口号 (默认: `8000`) |
