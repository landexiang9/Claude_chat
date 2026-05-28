# Claude Chat 中文文档

[English](./README.md) | **简体中文**

Claude Chat 是一个针对 Anthropic Claude API 的桌面 GUI/Web 客户端，基于 **pywebview** 与现代前端技术构建。界面采用优雅的 **Catppuccin Mocha** 暗色主题，支持流式对话响应、可折叠的推理思维轨迹（Extended Thinking）、离线本地静态资源、自定义系统提示词管理器、Artifacts 沙盒预览侧边栏以及多平台命令行快速部署。

---

## 核心特性

- 🎨 **高级暗色 UI** — 采用优雅的 Catppuccin Mocha 调色板与玻璃拟物化设计，包含自定义平滑滚动条和微交互动画。
- ⚙️ **多平台自动适配** — 支持 **Windows 桌面端 GUI 窗口**运行；在 **Linux 服务器、Android Termux (手机端)** 等无图形化或未安装 webview 依赖的环境下，程序会**自动降级为 Web 服务器模式**运行。
- 🌐 **局域网多设备跨端访问** — 通过命令行参数绑定 IP 后，同局域网内的手机、平板或其他电脑可直接通过浏览器输入 URL 访问与操作该聊天界面，且配置修改与数据库会自动加密保存在服务端运行设备上。
- 🧠 **思维链深度推理** — 支持模型的 Extended Thinking 模式。包含自适应推理和启用推理，支持配置思考 Token 预算及推理深度力度级别，并可在界面折叠查看详细思考流及预估 Token。
- 🚫 **一键中止生成** — 点击即可随时切断正在流式输出的 API 连接，并在自动保存已生成内容的同时，在会话中标记 `已中止` 状态。
- 📦 **Artifacts 侧边栏沙盒预览** — 独立的右侧侧边栏，支持 SVG 矢量图渲染、在沙盒 `<iframe>` 中隔离执行 HTML 网页、以及利用本地 `mermaid.js` 实时渲染流程图与结构图。
- ✏️ **消息便捷回滚与分叉** — 允许直接编辑修改历史发送的消息重新发起生成，或者直接重新生成最新的模型答复，更支持从任意消息节点分叉出一个全新的对话。
- 🔒 **安全凭证加密存储** — 在 Windows 系统下自动调用 `keyring` 将 API Key 安全存入系统保险箱；在 Linux/Termux 等无图形化环境下，自动降级为基于设备指纹和 XOR 的混淆备份存储。
- 🗃️ **SQLite 数据库底层** — 所有对话与消息列表均持久化存储于本地的 `claude_chat.db` 数据库中。首次启动时，程序会自动将 `conversations/` 下的老版本 JSON 对话文件迁移至 SQLite 并归档备份。
- 🛠️ **本地代码沙盒终端** — 支持在客户端本地安全执行模型输出的 Python/Javascript 代码块，并在消息块下实时生成一个交互式终端，支持 stdin 写入和手动终止进程。
- 📶 **代理设置支持** — 可在图形界面下轻松配置：系统代理（跟随环境变量）、无代理、或自定义 HTTP 代理服务器 URL。

---

## 环境要求

- **Python 3.10+**
- 互联网连接（用于请求 Anthropic 接口）
- Anthropic Claude API Key (可在启动后在软件设置 `⚙️` 中输入)

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

---

### 方案 B：Linux 系统 部署 (包含 Headless 服务器/云主机)

在 Linux 环境下，您可以快速将其作为 Web 聊天室部署：

1. **安装 Python 基础依赖项**：
   ```bash
   # Debian/Ubuntu
   sudo apt update
   sudo apt install python3 python3-pip python3-venv

   # CentOS/RHEL
   sudo yum install python3 python3-pip
   ```
2. **创建虚拟环境并安装项目依赖**：
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. **以纯 Web 服务器模式运行**：
   ```bash
   # 绑定 0.0.0.0 允许外部局域网/公网 IP 访问，指定运行在 8000 端口
   python claude_chat.py --server --host 0.0.0.0 --port 8000
   ```
   启动后，在局域网内任意客户端设备的浏览器中输入 `http://<服务器IP>:8000` 即可开始使用。

---

### 方案 C：安卓手机 Termux 部署 (掌上 AI 服务端)

得益于无 GUI 自动降级设计，您可以在安卓手机的 Termux 终端中一键部署，将其作为您的个人移动 Claude 聊天中继：

1. **配置 Termux 基本环境并安装 Python**：
   ```bash
   pkg update && pkg upgrade
   pkg install python python-pip clang make -y
   ```
2. **下载项目并配置运行环境**：
   ```bash
   # 进入 Claude Chat 项目目录
   python -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. **启动移动 Web 服务**：
   ```bash
   python claude_chat.py --server --host 0.0.0.0 --port 8000
   ```
   启动后，直接在手机浏览器或同局域网的电脑浏览器中访问 `http://127.0.0.1:8000` 即可开始对话。

---

## 命令行参数一览

在终端中执行 `python claude_chat.py` 时支持以下配置参数：

| 参数选项 | 全称 | 说明 |
|:---|:---|:---|
| `-s` | `--server` | 强制以纯 Web 服务模式 (Headless) 运行，不显示本地图形窗口 |
| | `--host <ip>` | 指定 Web 服务绑定的网卡 IP (如 `0.0.0.0` 代表绑定所有网卡，默认为 `127.0.0.1`) |
| `-p` | `--port <port>` | 指定 Web 服务的监听端口号 (默认: `8000`) |

---

## Windows 可执行文件一键打包 (无 Python 环境运行)

如果您希望将程序打包，直接分发给没有安装 Python 的 Windows 电脑使用，可以一键编译为单个独立的 `.exe` 文件：

1. 打开控制台并进入虚拟环境：
   ```bash
   call .venv\Scripts\activate.bat
   ```
2. 运行打包构建脚本：
   ```bash
   python build_executable.py
   ```
3. 编译完成后，您可以在生成的 **`dist/ClaudeChat.exe`** 找到独立运行包，支持快速拷贝拷贝和快速运行部署。

---

## 快捷键与操作

| 快捷键 | 触发操作 |
|:---|:---|
| `Ctrl+Enter` | 发送当前输入的消息 |
| `Shift+Enter` | 输入框换行 |

---

## 代理配置模式

1. **System proxy (系统代理)** — 自动继承并读取操作系统的环境变量（如 `HTTP_PROXY`, `HTTPS_PROXY`）。
2. **No proxy (无代理)** — 强制直连，忽略所有系统级代理配置。
3. **Custom proxy (自定义代理)** — 手动指定具体的代理网关服务器（如本地代理 `http://127.0.0.1:10809`）。
