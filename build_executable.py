# -*- coding: utf-8 -*-
"""
Claude Chat - PyInstaller 打包构建脚本
用于自动检测 PyInstaller 环境并根据 spec 配置文件将程序打包为单个独立的 Windows 可执行文件。
"""
import sys
import subprocess
import shutil
from pathlib import Path

def print_banner():
    print("===================================================")
    print("  开始打包 Claude Chat 为独立 Windows 可执行程序")
    print("===================================================")

def check_pyinstaller():
    print("[1/3] 正在检查打包工具 PyInstaller 是否安装...")
    try:
        import PyInstaller
        print(f" -> 检测到 PyInstaller 版本: {PyInstaller.__version__}")
        return True
    except ImportError:
        print(" -> 未检测到 PyInstaller，正在尝试通过 pip 进行安装...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
            print(" -> PyInstaller 安装成功！")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[错误] 安装 PyInstaller 失败: {e}")
            return False

def build():
    print("[2/3] 正在执行 PyInstaller 编译打包流程...")
    spec_path = Path(__file__).parent / "claude_chat.spec"
    
    if not spec_path.exists():
        print(f"[错误] 未找到配置文件: {spec_path}")
        return False
        
    try:
        # 运行 PyInstaller 命令进行打包
        subprocess.run([
            sys.executable, "-m", 
            "PyInstaller", 
            str(spec_path), 
            "--clean"
        ], check=True)
        print(" -> 编译打包程序完成。")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[错误] PyInstaller 编译失败: {e}")
        return False

def verify_output():
    print("[3/3] 正在验证打包结果...")
    dist_dir = Path(__file__).parent / "dist"
    exe_file = dist_dir / "ClaudeChat.exe"
    
    if exe_file.exists():
        size_mb = exe_file.stat().st_size / (1024 * 1024)
        print("===================================================")
        print("[成功] 打包完成！")
        print("===================================================")
        print(f" 可执行文件路径: {exe_file.resolve()}")
        print(f" 文件大小: {size_mb:.2f} MB")
        print(" 说明:")
        print("   1. 您可以直接双击运行此 exe 文件启动图形界面。")
        print("   2. 可以将此文件拷贝到其他 64位 Windows 系统中直接部署和运行，无需安装 Python。")
        print("   3. 运行时会在 exe 所在的同级目录下自动创建数据库和配置文件。")
        print("===================================================")
        return True
    else:
        print("[错误] 未能找到生成的可执行文件，请检查打包日志。")
        return False

if __name__ == "__main__":
    print_banner()
    if not check_pyinstaller():
        sys.exit(1)
        
    if build():
        if verify_output():
            sys.exit(0)
            
    sys.exit(1)
