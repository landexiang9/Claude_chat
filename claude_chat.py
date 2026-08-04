"""
Claude Chat - Desktop GUI for Anthropic Claude API (Entry Point)
Imports and runs the modularized ClaudeChatApp from the claude_chat package.
"""
# 导入系统与路径相关的标准库
import sys
import argparse
import runpy
from pathlib import Path


if len(sys.argv) == 3 and sys.argv[1] == "--sandbox-worker":
    # PyInstaller builds do not expose a separate python.exe.  This narrow
    # entrypoint lets the copied executable act as the AppContainer Python
    # runtime without initializing the GUI, database, config, or API clients.
    runpy.run_path(sys.argv[2], run_name="__main__")
    raise SystemExit(0)

# 确保项目根目录在 Python 模块搜索路径（sys.path）的首位，避免导入子模块时出错
sys.path.insert(0, str(Path(__file__).parent))

from claude_chat.app import ClaudeChatApp

if __name__ == "__main__":
    # 初始化命令行参数解析器，以便在 Linux、Termux 等无 GUI 环境下灵活控制程序启动
    parser = argparse.ArgumentParser(description="Claude Chat - 跨平台的 Anthropic Claude API 客户端")
    
    # 添加 --server / -s 选项：强制开启纯 Web 服务模式（不启动本地 GUI 窗口）
    parser.add_argument("-s", "--server", action="store_true", help="强制以纯 Web 服务模式 (Headless) 运行，不启动 GUI 窗口")
    
    # 添加 --host 选项：指定 Web 服务绑定的 IP。如 0.0.0.0 允许同局域网其他设备访问
    parser.add_argument("--host", type=str, default=None, help="Web 服务器绑定的 IP 地址 (例如: 0.0.0.0)")
    
    # 添加 --port / -p 选项：指定 Web 服务的运行端口，默认 8000
    parser.add_argument("-p", "--port", type=int, default=None, help="Web 服务器运行的端口号 (默认: 8000)")
    
    args = parser.parse_args()
    
    # 实例化主应用，并将解析到的参数传入。支持 CLI 参数覆盖配置文件设置
    app = ClaudeChatApp(
        host=args.host,
        port=args.port,
        force_server=args.server
    )
    
    # 进入应用程序的主事件循环
    app.mainloop()
