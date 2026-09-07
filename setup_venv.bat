@echo off
:: Claude Chat 虚拟环境一键配置脚本 (Windows CMD)
:: 用于自动创建虚拟环境、升级 pip 并安装所有依赖项

chcp 65001 >nul
echo ===================================================
echo   开始配置 Claude Chat 运行与开发虚拟环境 (.venv)
echo ===================================================

:: 1. 检测 Python 是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并将其加入环境变量 PATH。
    pause
    exit /b 1
)

:: 2. 创建虚拟环境 (若不存在)
if not exist ".venv" (
    echo 正在创建虚拟环境 .venv ...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [错误] 创建虚拟环境失败，请检查 Python 安装。
        pause
        exit /b 1
    )
    echo 虚拟环境创建成功。
) else (
    echo 虚拟环境 .venv 已存在，将直接更新依赖。
)

:: 3. 激活虚拟环境并升级 pip
echo 正在激活虚拟环境并升级 pip ...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip

:: 4. 安装依赖库
echo 正在安装项目依赖项 (requirements.txt) ...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [警告] 依赖项安装中出现错误，请检查网络或代理设置。
)

:: 5. 安装开发与打包依赖项
echo 正在安装打包/代码格式化工具 (pyinstaller, ruff) ...
pip install pyinstaller ruff
if %errorlevel% neq 0 (
    echo [警告] 开发工具安装失败。
)

echo ===================================================
echo   配置完成！
echo ===================================================
echo   您现在可以通过以下命令运行项目：
echo     .venv\Scripts\python.exe claude_chat.py
echo.
echo   或者双击运行主程序，或执行打包脚本：
echo     .venv\Scripts\python.exe build_executable.py
echo ===================================================
pause
