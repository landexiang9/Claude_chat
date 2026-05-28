# Windows PowerShell 虚拟环境一键配置脚本
# 用于自动创建虚拟环境、升级 pip 并安装所有依赖项

$OutputEncoding = [System.Text.Encoding]::UTF8
Host -Id 0 | Out-Null # Ensure console uses UTF8 if possible

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  开始配置 Claude Chat 运行与开发虚拟环境 (.venv)" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

# 1. 检测 Python 是否安装
try {
    $pythonVersion = & python --version 2>&1
    Write-Host "检测到 Python: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[错误] 未检测到 Python，请先安装 Python 3.10+ 并将其加入环境变量 PATH。" -ForegroundColor Red
    Read-Host "按下回车键退出..."
    exit 1
}

# 2. 创建虚拟环境 (若不存在)
if (-not (Test-Path ".venv")) {
    Write-Host "正在创建虚拟环境 .venv ..." -ForegroundColor Yellow
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 创建虚拟环境失败，请检查 Python 安装。" -ForegroundColor Red
        Read-Host "按下回车键退出..."
        exit 1
    }
    Write-Host "虚拟环境创建成功。" -ForegroundColor Green
} else {
    Write-Host "虚拟环境 .venv 已存在，将直接更新依赖。" -ForegroundColor Green
}

# 3. 激活虚拟环境并升级 pip
Write-Host "正在激活虚拟环境并升级 pip ..." -ForegroundColor Yellow
# Run in the scope of the caller or in this script session
. .venv\Scripts\Activate.ps1
& python -m pip install --upgrade pip

# 4. 安装依赖库
Write-Host "正在安装项目依赖项 (requirements.txt) ..." -ForegroundColor Yellow
& pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[警告] 依赖项安装中出现错误，请检查网络或代理设置。" -ForegroundColor Yellow
}

# 5. 安装开发与打包依赖项
Write-Host "正在安装打包/代码格式化工具 (pyinstaller, ruff) ..." -ForegroundColor Yellow
& pip install pyinstaller ruff
if ($LASTEXITCODE -ne 0) {
    Write-Host "[警告] 开发工具安装失败。" -ForegroundColor Yellow
}

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  配置完成！" -ForegroundColor Green
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  您现在可以通过以下命令运行项目：" -ForegroundColor White
Write-Host "    .venv\Scripts\python.exe claude_chat.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "  或者双击运行主程序，或执行打包脚本：" -ForegroundColor White
Write-Host "    .venv\Scripts\python.exe build_executable.py" -ForegroundColor Yellow
Write-Host "===================================================" -ForegroundColor Cyan

Read-Host "按下回车键退出..."
