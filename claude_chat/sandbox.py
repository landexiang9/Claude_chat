"""Cross-platform code-sandbox discovery and launch helpers.

Linux execution uses short-lived, hardened Docker containers.  Windows support
is deliberately fail-closed until the native AppContainer launcher is present;
the probe below performs a real AppContainer process launch so the settings UI
can report the exact platform failure instead of guessing from the OS version.
"""

from __future__ import annotations

import ctypes
import csv
import hashlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger("claude_chat")

PYTHON_IMAGE = "python:3.12-slim"
NODE_IMAGE = "node:22-slim"
DOCKER_INSTALL_URL = "https://docs.docker.com/engine/install/"
MAX_INSTALL_SECONDS = 15 * 60
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 30
MIN_EXECUTION_TIMEOUT_SECONDS = 1
MAX_EXECUTION_TIMEOUT_SECONDS = 600
MAX_WINDOWS_SANDBOX_PROCESSES = 4


def normalize_language(language: str) -> str | None:
    value = str(language or "").strip().lower()
    if value == "python":
        return "python"
    if value in {"javascript", "js"}:
        return "javascript"
    return None


def normalize_execution_timeout(value: Any) -> int:
    """Return a bounded per-run sandbox timeout in seconds."""
    if isinstance(value, bool):
        return DEFAULT_EXECUTION_TIMEOUT_SECONDS
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_EXECUTION_TIMEOUT_SECONDS
    return max(MIN_EXECUTION_TIMEOUT_SECONDS, min(MAX_EXECUTION_TIMEOUT_SECONDS, seconds))


def _run_checked(command: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        check=False,
    )


def _short_error(result: subprocess.CompletedProcess[str]) -> str:
    message = (result.stderr or result.stdout or "").strip()
    if not message:
        return f"进程退出码 {result.returncode}"
    return message.splitlines()[-1][:500]


def _docker_image_exists(docker: str, image: str) -> tuple[bool, str, str]:
    result = _run_checked([docker, "image", "inspect", "--format", "{{.Id}}", image], timeout=10.0)
    if result.returncode == 0:
        image_id = (result.stdout or "").strip()
        return bool(image_id), "", image_id
    return False, _short_error(result), ""


def _check_linux() -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    reasons: list[str] = []
    docker = shutil.which("docker")
    if not docker:
        reason = "未找到 Docker CLI；请先安装 Docker Engine。"
        logger.warning("Linux 代码沙盒不可用：%s", reason)
        return {
            "platform": "linux",
            "backend": "docker",
            "ready": False,
            "components": [
                {"id": "docker", "name": "Docker Engine", "ready": False, "reason": reason},
                {"id": "python", "name": f"Python 环境（{PYTHON_IMAGE}）", "ready": False, "reason": "等待 Docker 安装"},
                {"id": "node", "name": f"Node.js 环境（{NODE_IMAGE}）", "ready": False, "reason": "等待 Docker 安装"},
            ],
            "reasons": [reason],
            "can_install": False,
            "download_url": DOCKER_INSTALL_URL,
        }

    try:
        daemon = _run_checked([docker, "version", "--format", "{{.Server.Version}}"], timeout=10.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = f"Docker 服务检测失败：{exc}"
        logger.warning("Linux 代码沙盒不可用：%s", reason)
        return {
            "platform": "linux",
            "backend": "docker",
            "ready": False,
            "components": [{"id": "docker", "name": "Docker Engine", "ready": False, "reason": reason}],
            "reasons": [reason],
            "can_install": False,
            "download_url": DOCKER_INSTALL_URL,
        }

    docker_ready = daemon.returncode == 0 and bool((daemon.stdout or "").strip())
    docker_reason = "" if docker_ready else f"Docker daemon 不可用：{_short_error(daemon)}"
    components.append({"id": "docker", "name": "Docker Engine", "ready": docker_ready, "reason": docker_reason})
    if not docker_ready:
        reasons.append(docker_reason)
        logger.warning("Linux 代码沙盒不可用：%s", docker_reason)
        return {
            "platform": "linux",
            "backend": "docker",
            "ready": False,
            "components": components,
            "reasons": reasons,
            "can_install": False,
            "download_url": DOCKER_INSTALL_URL,
        }

    for component_id, name, image in (
        ("python", "Python", PYTHON_IMAGE),
        ("node", "Node.js", NODE_IMAGE),
    ):
        try:
            exists, detail, runtime_ref = _docker_image_exists(docker, image)
        except (OSError, subprocess.TimeoutExpired) as exc:
            exists, detail, runtime_ref = False, str(exc), ""
        reason = "" if exists else f"缺少镜像 {image}"
        components.append(
            {
                "id": component_id,
                "name": f"{name} 环境（{image}）",
                "ready": exists,
                "reason": reason,
                "detail": detail if not exists else "",
                "runtime_ref": runtime_ref,
            }
        )
        if reason:
            reasons.append(reason)

    ready = docker_ready and all(item["ready"] for item in components)
    if ready:
        logger.info("Linux 代码沙盒检测通过：Docker、Python 与 Node.js 镜像均可用。")
    else:
        logger.warning("Linux 代码沙盒环境不完整：%s", "；".join(reasons))
    return {
        "platform": "linux",
        "backend": "docker",
        "ready": ready,
        "components": components,
        "reasons": reasons,
        "can_install": docker_ready and not ready,
        "download_url": "" if docker_ready else DOCKER_INSTALL_URL,
    }


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.c_void_p),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _format_windows_error(prefix: str, code: int | None = None) -> str:
    error_code = code if code is not None else ctypes.get_last_error()
    try:
        detail = ctypes.FormatError(error_code).strip()
    except Exception:
        detail = "未知错误"
    return f"{prefix}（Win32 {error_code}: {detail}）"


def _probe_windows_appcontainer() -> tuple[bool, str]:
    """Create a disposable profile and launch a real process inside it."""
    if os.name != "nt":
        return False, "当前系统不是 Windows"

    required = (
        ("userenv.dll", "CreateAppContainerProfile"),
        ("userenv.dll", "DeleteAppContainerProfile"),
        ("kernel32.dll", "InitializeProcThreadAttributeList"),
        ("kernel32.dll", "UpdateProcThreadAttribute"),
        ("kernel32.dll", "CreateProcessW"),
    )
    for dll_name, symbol in required:
        try:
            dll = ctypes.WinDLL(dll_name, use_last_error=True)
            getattr(dll, symbol)
        except (OSError, AttributeError) as exc:
            return False, f"缺少 Windows AppContainer API {symbol}：{exc}"

    userenv = ctypes.WinDLL("userenv.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    profile_name = f"ClaudeChat.SandboxProbe.{uuid.uuid4().hex}"
    sid = ctypes.c_void_p()
    attr_buffer = None
    process_info = _PROCESS_INFORMATION()
    profile_created = False

    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long

    try:
        hr = userenv.CreateAppContainerProfile(
            profile_name,
            "Claude Chat Sandbox Probe",
            "Temporary AppContainer capability probe",
            None,
            0,
            ctypes.byref(sid),
        )
        if hr != 0:
            return False, f"创建 AppContainer profile 失败（HRESULT 0x{hr & 0xFFFFFFFF:08X}）"
        profile_created = True

        attribute_size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
        if not attribute_size.value:
            return False, _format_windows_error("计算 AppContainer 启动属性大小失败")
        attr_buffer = ctypes.create_string_buffer(attribute_size.value)
        attr_pointer = ctypes.cast(attr_buffer, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(attr_pointer, 1, 0, ctypes.byref(attribute_size)):
            return False, _format_windows_error("初始化 AppContainer 启动属性失败")

        security = _SECURITY_CAPABILITIES(sid.value, None, 0, 0)
        proc_thread_attribute_security_capabilities = 0x00020009
        if not kernel32.UpdateProcThreadAttribute(
            attr_pointer,
            0,
            proc_thread_attribute_security_capabilities,
            ctypes.byref(security),
            ctypes.sizeof(security),
            None,
            None,
        ):
            return False, _format_windows_error("写入 AppContainer 安全属性失败")

        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = attr_pointer
        comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
        command = ctypes.create_unicode_buffer(f'"{comspec}" /d /c exit 0')
        flags = 0x00080000 | 0x08000000  # EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW
        created = kernel32.CreateProcessW(
            None,
            command,
            None,
            None,
            False,
            flags,
            None,
            None,
            ctypes.byref(startup),
            ctypes.byref(process_info),
        )
        if not created:
            return False, _format_windows_error("在 AppContainer 中创建测试进程失败")

        wait_result = kernel32.WaitForSingleObject(process_info.hProcess, 5000)
        if wait_result == 0x00000102:
            kernel32.TerminateProcess(process_info.hProcess, 1)
            return False, "AppContainer 测试进程在 5 秒内未退出"
        if wait_result != 0:
            return False, _format_windows_error("等待 AppContainer 测试进程失败")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)):
            return False, _format_windows_error("读取 AppContainer 测试进程退出码失败")
        if exit_code.value != 0:
            return False, f"AppContainer 测试进程退出码为 {exit_code.value}"
        return True, "AppContainer profile、受限令牌和进程启动均正常"
    except Exception as exc:
        return False, f"AppContainer 自检异常：{exc}"
    finally:
        if process_info.hThread:
            kernel32.CloseHandle(process_info.hThread)
        if process_info.hProcess:
            kernel32.CloseHandle(process_info.hProcess)
        if attr_buffer is not None:
            try:
                kernel32.DeleteProcThreadAttributeList(ctypes.cast(attr_buffer, ctypes.c_void_p))
            except Exception:
                pass
        if sid.value:
            try:
                ctypes.windll.advapi32.FreeSid(sid)
            except Exception:
                pass
        if profile_created:
            try:
                userenv.DeleteAppContainerProfile(profile_name)
            except Exception:
                pass


APP_CONTAINER_NAME = "ClaudeChat.CodeSandbox"


def _get_windows_appcontainer_profile() -> tuple[ctypes.c_void_p, Path]:
    """Return the persistent sandbox SID and its private profile directory."""
    userenv = ctypes.WinDLL("userenv.dll", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32.dll", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32.dll", use_last_error=True)
    sid = ctypes.c_void_p()

    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long

    hr = userenv.CreateAppContainerProfile(
        APP_CONTAINER_NAME,
        "Claude Chat Code Sandbox",
        "Restricted Python and Node.js execution",
        None,
        0,
        ctypes.byref(sid),
    )
    if (hr & 0xFFFFFFFF) == 0x800700B7:  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
        hr = userenv.DeriveAppContainerSidFromAppContainerName(APP_CONTAINER_NAME, ctypes.byref(sid))
    if hr != 0 or not sid.value:
        raise RuntimeError(f"创建或读取 AppContainer profile 失败（HRESULT 0x{hr & 0xFFFFFFFF:08X}）")

    sid_string = wintypes.LPWSTR()
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
        advapi32.FreeSid(sid)
        raise RuntimeError(_format_windows_error("转换 AppContainer SID 失败"))

    folder = wintypes.LPWSTR()
    userenv.GetAppContainerFolderPath.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR)]
    userenv.GetAppContainerFolderPath.restype = ctypes.c_long
    try:
        hr = userenv.GetAppContainerFolderPath(sid_string.value, ctypes.byref(folder))
        if hr != 0 or not folder.value:
            raise RuntimeError(f"读取 AppContainer profile 路径失败（HRESULT 0x{hr & 0xFFFFFFFF:08X}）")
        profile_path = Path(folder.value)
    finally:
        if folder:
            ole32.CoTaskMemFree(folder)
        ctypes.windll.kernel32.LocalFree(sid_string)
    profile_path.mkdir(parents=True, exist_ok=True)
    return sid, profile_path


def _copy_if_changed(source: Path, destination: Path) -> None:
    def digest(path: Path) -> bytes:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.digest()

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        try:
            if destination.stat().st_size == source.stat().st_size:
                source_hash = digest(source)
                destination_hash = digest(destination)
                if source_hash == destination_hash:
                    return
        except OSError:
            pass
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _windows_sid_string(sid: ctypes.c_void_p) -> str:
    advapi32 = ctypes.WinDLL("advapi32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    value = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(value)):
        raise RuntimeError(_format_windows_error("转换 Windows SID 失败"))
    try:
        return value.value
    finally:
        kernel32.LocalFree(value)


def _current_windows_user_sid() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    whoami = os.path.join(system_root, "System32", "whoami.exe")
    result = _run_checked([whoami, "/user", "/fo", "csv", "/nh"], timeout=5.0)
    if result.returncode != 0:
        raise RuntimeError(f"读取当前用户 SID 失败：{_short_error(result)}")
    try:
        row = next(csv.reader([result.stdout.strip()]))
        if len(row) < 2 or not row[1].startswith("S-"):
            raise ValueError("无效 SID")
        return row[1]
    except Exception as exc:
        raise RuntimeError(f"解析当前用户 SID 失败：{exc}") from exc


def _protect_windows_runtime(runtime_dir: Path, appcontainer_sid: ctypes.c_void_p) -> None:
    """Make copied runtimes executable but immutable to sandboxed code."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    icacls = os.path.join(system_root, "System32", "icacls.exe")
    app_sid = _windows_sid_string(appcontainer_sid)
    user_sid = _current_windows_user_sid()
    targets = [runtime_dir, *(path for path in runtime_dir.rglob("*") if path.is_file() or path.is_dir())]
    for target in targets:
        inheritance = "(OI)(CI)" if target.is_dir() else ""
        result = _run_checked(
            [
                icacls,
                str(target),
                "/inheritance:r",
                "/grant:r",
                f"*{user_sid}:{inheritance}F",
                f"*S-1-5-18:{inheritance}F",
                f"*{app_sid}:{inheritance}RX",
                "/Q",
            ],
            timeout=15.0,
        )
        if result.returncode != 0:
            raise RuntimeError(f"锁定 AppContainer 运行时失败（{target.name}）：{_short_error(result)}")


def _resolve_windows_runtime(
    language: str, profile_path: Path, run_dir: Path
) -> tuple[Path, list[str], dict[str, str]]:
    runtime_dir = profile_path / "ClaudeChatRuntime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    environment_root = run_dir / "Environment"
    profile_roaming = environment_root / "Roaming"
    profile_local = environment_root / "Local"
    profile_temp = environment_root / "Temp"
    for directory in (profile_roaming, profile_local, profile_temp):
        directory.mkdir(parents=True, exist_ok=True)
    environment = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "COMSPEC": os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
        "PATH": str(runtime_dir) + os.pathsep + os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "TEMP": str(profile_temp),
        "TMP": str(profile_temp),
        "HOME": str(environment_root),
        "USERPROFILE": str(environment_root),
        "LOCALAPPDATA": str(profile_local),
        "APPDATA": str(profile_roaming),
        "PROGRAMDATA": os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
        "PROGRAMFILES": os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        "COMMONPROGRAMFILES": os.environ.get("COMMONPROGRAMFILES", r"C:\Program Files\Common Files"),
        "OS": "Windows_NT",
    }
    if language == "python":
        if getattr(sys, "frozen", False):
            executable = runtime_dir / "ClaudeChatSandboxPython.exe"
            _copy_if_changed(Path(sys.executable), executable)
            return executable, ["--sandbox-worker"], environment
        executable = Path(sys.executable).resolve()
        if not executable.is_file():
            raise RuntimeError(f"Python 解释器不存在：{executable}")
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        return executable, ["-I", "-B"], environment

    node = shutil.which("node")
    if not node:
        raise RuntimeError("未找到 Node.js；请安装 Node.js 后重新检测")
    executable = runtime_dir / "node.exe"
    _copy_if_changed(Path(node).resolve(), executable)
    # AppContainer cannot enumerate C:\ while Node's default main-module
    # canonicalization walks every parent.  Preserve the already absolute,
    # private profile path instead of resolving it through the host root.
    return executable, ["--preserve-symlinks", "--preserve-symlinks-main"], environment


def _environment_block(environment: dict[str, str]):
    items = [f"{key}={value}" for key, value in sorted(environment.items(), key=lambda item: item[0].upper())]
    return ctypes.create_unicode_buffer("\0".join(items) + "\0\0")


class WindowsSandboxProcess:
    """Small Popen-compatible wrapper for an AppContainer process in a Job Object."""

    def __init__(
        self,
        args: list[str],
        cwd: Path,
        environment: dict[str, str],
        appcontainer_sid: ctypes.c_void_p,
        timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    ):
        if os.name != "nt":
            raise RuntimeError("AppContainer 仅支持 Windows")
        import msvcrt

        self.args = args
        self.returncode = None
        self._kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        self._process_handle = wintypes.HANDLE()
        self._job_handle = wintypes.HANDLE()
        self._closed = False
        child_handles: list[wintypes.HANDLE] = []
        parent_handles: list[wintypes.HANDLE] = []
        attr_buffer = None

        security_attributes = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)

        def create_pipe(parent_reads: bool):
            read_handle = wintypes.HANDLE()
            write_handle = wintypes.HANDLE()
            if not self._kernel32.CreatePipe(
                ctypes.byref(read_handle),
                ctypes.byref(write_handle),
                ctypes.byref(security_attributes),
                0,
            ):
                raise RuntimeError(_format_windows_error("创建沙盒管道失败"))
            parent_handle = read_handle if parent_reads else write_handle
            child_handle = write_handle if parent_reads else read_handle
            if not self._kernel32.SetHandleInformation(parent_handle, 1, 0):
                self._kernel32.CloseHandle(read_handle)
                self._kernel32.CloseHandle(write_handle)
                raise RuntimeError(_format_windows_error("设置沙盒管道继承属性失败"))
            parent_handles.append(parent_handle)
            child_handles.append(child_handle)
            return parent_handle, child_handle

        stdin_parent, stdin_child = create_pipe(False)
        stdout_parent, stdout_child = create_pipe(True)
        stderr_parent, stderr_child = create_pipe(True)

        process_info = _PROCESS_INFORMATION()
        try:
            self._job_handle = self._kernel32.CreateJobObjectW(None, None)
            if not self._job_handle:
                raise RuntimeError(_format_windows_error("创建 Job Object 失败"))
            limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = 0x00000008 | 0x00000004 | 0x00000200 | 0x00002000
            limits.BasicLimitInformation.ActiveProcessLimit = MAX_WINDOWS_SANDBOX_PROCESSES
            # Job time is aggregate CPU time across every child. Scale it by the process cap so
            # the user-facing wall-clock timeout remains authoritative for multi-process code.
            limits.BasicLimitInformation.PerJobUserTimeLimit = (
                normalize_execution_timeout(timeout_seconds)
                * MAX_WINDOWS_SANDBOX_PROCESSES
                * 10_000_000
            )
            limits.JobMemoryLimit = 256 * 1024 * 1024
            if not self._kernel32.SetInformationJobObject(
                self._job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise RuntimeError(_format_windows_error("设置 Job Object 资源限制失败"))

            attribute_size = ctypes.c_size_t()
            self._kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(attribute_size))
            if not attribute_size.value:
                raise RuntimeError(_format_windows_error("计算 AppContainer 属性大小失败"))
            attr_buffer = ctypes.create_string_buffer(attribute_size.value)
            attr_pointer = ctypes.cast(attr_buffer, ctypes.c_void_p)
            if not self._kernel32.InitializeProcThreadAttributeList(attr_pointer, 2, 0, ctypes.byref(attribute_size)):
                raise RuntimeError(_format_windows_error("初始化 AppContainer 属性失败"))

            security = _SECURITY_CAPABILITIES(appcontainer_sid.value, None, 0, 0)
            if not self._kernel32.UpdateProcThreadAttribute(
                attr_pointer, 0, 0x00020009, ctypes.byref(security), ctypes.sizeof(security), None, None
            ):
                raise RuntimeError(_format_windows_error("设置 AppContainer 安全属性失败"))
            handle_array = (wintypes.HANDLE * 3)(stdin_child, stdout_child, stderr_child)
            if not self._kernel32.UpdateProcThreadAttribute(
                attr_pointer, 0, 0x00020002, handle_array, ctypes.sizeof(handle_array), None, None
            ):
                raise RuntimeError(_format_windows_error("限制沙盒继承句柄失败"))

            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x00000100
            startup.StartupInfo.hStdInput = stdin_child
            startup.StartupInfo.hStdOutput = stdout_child
            startup.StartupInfo.hStdError = stderr_child
            startup.lpAttributeList = attr_pointer
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
            environment_block = _environment_block(environment)
            flags = 0x00080000 | 0x08000000 | 0x00000400 | 0x00000004
            created = self._kernel32.CreateProcessW(
                str(args[0]),
                command_line,
                None,
                None,
                True,
                flags,
                environment_block,
                str(cwd),
                ctypes.byref(startup),
                ctypes.byref(process_info),
            )
            if not created:
                raise RuntimeError(_format_windows_error("在 AppContainer 中启动代码失败"))
            self._process_handle = process_info.hProcess
            if not self._kernel32.AssignProcessToJobObject(self._job_handle, self._process_handle):
                self._kernel32.TerminateProcess(self._process_handle, 1)
                raise RuntimeError(_format_windows_error("将 AppContainer 进程加入 Job Object 失败"))
            if self._kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
                self._kernel32.TerminateJobObject(self._job_handle, 1)
                raise RuntimeError(_format_windows_error("恢复 AppContainer 进程失败"))

            for handle in child_handles:
                self._kernel32.CloseHandle(handle)
            child_handles.clear()
            self._kernel32.CloseHandle(process_info.hThread)
            process_info.hThread = None

            stdin_fd = msvcrt.open_osfhandle(int(stdin_parent.value), os.O_WRONLY | os.O_BINARY)
            stdout_fd = msvcrt.open_osfhandle(int(stdout_parent.value), os.O_RDONLY | os.O_BINARY)
            stderr_fd = msvcrt.open_osfhandle(int(stderr_parent.value), os.O_RDONLY | os.O_BINARY)
            self.stdin = os.fdopen(stdin_fd, "wb", buffering=0)
            self.stdout = os.fdopen(stdout_fd, "rb", buffering=0)
            self.stderr = os.fdopen(stderr_fd, "rb", buffering=0)
            parent_handles.clear()
        except Exception:
            if process_info.hThread:
                self._kernel32.CloseHandle(process_info.hThread)
            if process_info.hProcess and not self._process_handle:
                self._kernel32.CloseHandle(process_info.hProcess)
            for handle in child_handles + parent_handles:
                if handle:
                    self._kernel32.CloseHandle(handle)
            if self._job_handle:
                self._kernel32.CloseHandle(self._job_handle)
                self._job_handle = None
            raise
        finally:
            if attr_buffer is not None:
                self._kernel32.DeleteProcThreadAttributeList(ctypes.cast(attr_buffer, ctypes.c_void_p))

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        exit_code = wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(self._process_handle, ctypes.byref(exit_code)):
            raise RuntimeError(_format_windows_error("读取 AppContainer 退出码失败"))
        if exit_code.value == 259:
            return None
        self.returncode = int(exit_code.value)
        return self.returncode

    def wait(self, timeout=None):
        milliseconds = 0xFFFFFFFF if timeout is None else max(0, int(float(timeout) * 1000))
        result = self._kernel32.WaitForSingleObject(self._process_handle, milliseconds)
        if result == 0x00000102:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != 0:
            raise RuntimeError(_format_windows_error("等待 AppContainer 进程失败"))
        return self.poll()

    def terminate(self):
        if self.poll() is None:
            self._kernel32.TerminateJobObject(self._job_handle, 1)

    kill = terminate

    def close(self):
        if self._closed:
            return
        self._closed = True
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(self, stream_name, None)
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass
        if self._process_handle:
            self._kernel32.CloseHandle(self._process_handle)
            self._process_handle = None
        if self._job_handle:
            self._kernel32.CloseHandle(self._job_handle)
            self._job_handle = None


def prepare_windows_appcontainer_launch(
    code: str,
    language: str,
    timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
) -> tuple[WindowsSandboxProcess, str, Path]:
    normalized = normalize_language(language)
    if not normalized:
        raise ValueError(f"不支持的代码执行语言：{language}")
    sid, profile_path = _get_windows_appcontainer_profile()
    run_id = uuid.uuid4().hex
    run_dir = profile_path / "ClaudeChatRuns" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    source = run_dir / ("main.py" if normalized == "python" else "main.js")
    source.write_text(str(code or ""), encoding="utf-8")
    try:
        executable, prefix, environment = _resolve_windows_runtime(normalized, profile_path, run_dir)
        _protect_windows_runtime(profile_path / "ClaudeChatRuntime", sid)
        process = WindowsSandboxProcess(
            [str(executable), *prefix, str(source)],
            run_dir,
            environment,
            sid,
            timeout_seconds=timeout_seconds,
        )
        return process, run_id, run_dir
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    finally:
        ctypes.windll.advapi32.FreeSid(sid)


def _check_windows() -> dict[str, Any]:
    appcontainer_ready, detail = _probe_windows_appcontainer()
    components = [
        {
            "id": "appcontainer",
            "name": "Windows AppContainer",
            "ready": appcontainer_ready,
            "reason": "" if appcontainer_ready else detail,
            "detail": detail if appcontainer_ready else "",
        }
    ]

    if appcontainer_ready:
        for component_id, name, language, code in (
            ("python", "Python AppContainer 环境", "python", "print('claude-chat-sandbox-ok')"),
            ("node", "Node.js AppContainer 环境", "javascript", "console.log('claude-chat-sandbox-ok')"),
        ):
            process = None
            run_dir = None
            try:
                process, _, run_dir = prepare_windows_appcontainer_launch(code, language)
                exit_code = process.wait(timeout=10.0)
                output = process.stdout.read(256).decode("utf-8", errors="replace")
                runtime_ready = exit_code == 0 and "claude-chat-sandbox-ok" in output
                reason = "" if runtime_ready else f"测试进程失败（退出码 {exit_code}）"
                detail_text = "已在 AppContainer + Job Object 中完成真实执行" if runtime_ready else output.strip()[:300]
            except Exception as exc:
                runtime_ready = False
                reason = str(exc)
                detail_text = ""
            finally:
                if process is not None:
                    process.close()
                if run_dir is not None:
                    shutil.rmtree(run_dir, ignore_errors=True)
            components.append(
                {
                    "id": component_id,
                    "name": name,
                    "ready": runtime_ready,
                    "reason": reason,
                    "detail": detail_text,
                }
            )
    reasons = [item["reason"] for item in components if not item["ready"] and item.get("reason")]
    ready = appcontainer_ready and all(item["ready"] for item in components)
    if ready:
        logger.info("Windows 代码沙盒检测通过：AppContainer 执行环境可用。")
    else:
        logger.warning("Windows 代码沙盒不可用：%s", "；".join(reasons))
    return {
        "platform": "windows",
        "backend": "appcontainer",
        "ready": ready,
        "components": components,
        "reasons": reasons,
        "can_install": False,
        "download_url": "",
    }


def check_sandbox_environment() -> dict[str, Any]:
    system = platform.system().lower()
    if system == "linux":
        return _check_linux()
    if system == "windows":
        return _check_windows()
    reason = f"暂不支持在 {platform.system() or '未知系统'} 上执行不可信代码"
    logger.warning("代码沙盒不可用：%s", reason)
    return {
        "platform": system or "unknown",
        "backend": "unsupported",
        "ready": False,
        "components": [],
        "reasons": [reason],
        "can_install": False,
        "download_url": "",
    }


def install_sandbox_components() -> dict[str, Any]:
    status = check_sandbox_environment()
    if status["platform"] != "linux":
        return {"success": False, "error": "当前平台没有可自动下载的沙盒组件", "status": status}
    if status.get("download_url"):
        return {
            "success": False,
            "error": "需要先安装并启动 Docker Engine",
            "download_url": status["download_url"],
            "status": status,
        }
    if not status.get("can_install"):
        return {"success": bool(status.get("ready")), "status": status}

    docker = shutil.which("docker")
    if not docker:
        return {"success": False, "error": "未找到 Docker CLI", "download_url": DOCKER_INSTALL_URL, "status": status}

    missing = {item["id"] for item in status.get("components", []) if not item.get("ready")}
    errors: list[str] = []
    for component_id, image in (("python", PYTHON_IMAGE), ("node", NODE_IMAGE)):
        if component_id not in missing:
            continue
        logger.info("开始下载代码沙盒镜像：%s", image)
        try:
            result = _run_checked([docker, "pull", image], timeout=MAX_INSTALL_SECONDS)
        except subprocess.TimeoutExpired:
            errors.append(f"下载 {image} 超时")
            logger.error("下载代码沙盒镜像超时：%s", image)
            continue
        except OSError as exc:
            errors.append(f"下载 {image} 失败：{exc}")
            logger.exception("启动 Docker pull 失败：%s", image)
            continue
        if result.returncode != 0:
            error = f"下载 {image} 失败：{_short_error(result)}"
            errors.append(error)
            logger.error("%s", error)
        else:
            logger.info("代码沙盒镜像下载完成：%s", image)

    refreshed = check_sandbox_environment()
    return {
        "success": refreshed.get("ready", False),
        "error": "；".join(errors),
        "status": refreshed,
    }


@dataclass
class SandboxLaunch:
    command: list[str]
    process_id: str
    temp_dir: Path
    container_name: str
    environment: dict[str, str]


def prepare_linux_docker_launch(code: str, language: str) -> SandboxLaunch:
    if platform.system().lower() != "linux":
        raise RuntimeError("Docker 沙盒启动器仅用于 Linux")
    normalized = normalize_language(language)
    if not normalized:
        raise ValueError(f"不支持的代码执行语言：{language}")

    status = check_sandbox_environment()
    component_id = "python" if normalized == "python" else "node"
    component = next(
        (item for item in status.get("components", []) if item.get("id") == component_id),
        None,
    )
    docker_component = next((item for item in status.get("components", []) if item.get("id") == "docker"), None)
    if not docker_component or not docker_component.get("ready"):
        raise RuntimeError("Docker Engine 不可用，请在设置中查看检测原因")
    if not component or not component.get("ready"):
        raise RuntimeError(f"{normalized} 沙盒环境未安装，请在设置中点击下载")

    process_id = uuid.uuid4().hex
    container_name = f"claude-chat-sandbox-{process_id}"
    temp_dir = Path(tempfile.mkdtemp(prefix="claude_chat_sandbox_"))
    input_dir = temp_dir / "input"
    input_dir.mkdir(mode=0o755)
    suffix = ".py" if normalized == "python" else ".js"
    source_path = input_dir / f"main{suffix}"
    source_path.write_text(str(code or ""), encoding="utf-8")
    try:
        temp_dir.chmod(0o700)
        input_dir.chmod(0o755)
        source_path.chmod(0o444)
    except OSError:
        pass

    docker = shutil.which("docker")
    if not docker:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("Docker CLI 在环境检测后消失")
    image = component.get("runtime_ref") or (PYTHON_IMAGE if normalized == "python" else NODE_IMAGE)
    if normalized == "python":
        runtime_command = ["python", "-I", "-B", f"/input/{source_path.name}"]
    else:
        runtime_command = ["node", f"/input/{source_path.name}"]
    command = [
        docker,
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--pids-limit",
        "16",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--cpus",
        "0.5",
        "--user",
        "65534:65534",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
        "--tmpfs",
        "/work:rw,noexec,nosuid,size=64m",
        "--mount",
        f"type=bind,source={input_dir.resolve()},target=/input,readonly",
        "--workdir",
        "/work",
        "--env",
        "HOME=/tmp",
        image,
        *runtime_command,
    ]
    # Only a tiny, explicit host environment reaches the Docker CLI.  No API
    # keys or proxy variables are inherited by the user program in the container.
    cli_environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "XDG_RUNTIME_DIR"}
    }
    return SandboxLaunch(command, process_id, temp_dir, container_name, cli_environment)


def terminate_sandbox_process(info: dict[str, Any]) -> None:
    container_name = info.get("container_name")
    if container_name and platform.system().lower() == "linux":
        docker = shutil.which("docker")
        if docker:
            try:
                _run_checked([docker, "kill", container_name], timeout=5.0)
            except Exception as exc:
                logger.warning("终止 Docker 沙盒容器 %s 失败：%s", container_name, exc)
    process = info.get("proc")
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()


def cleanup_sandbox_process(info: dict[str, Any]) -> None:
    process = info.get("proc")
    if process is not None and hasattr(process, "close"):
        try:
            process.close()
        except Exception:
            pass
    temp_dir = info.get("temp_dir")
    if temp_dir:
        shutil.rmtree(Path(temp_dir), ignore_errors=True)
