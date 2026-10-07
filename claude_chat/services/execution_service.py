import json
import logging
import subprocess
import threading

from claude_chat.sandbox import (
    check_sandbox_environment,
    cleanup_sandbox_process,
    install_sandbox_components,
    normalize_execution_timeout,
    normalize_language,
    prepare_linux_docker_launch,
    prepare_windows_appcontainer_launch,
    terminate_sandbox_process,
)
from claude_chat.services.base import AppService

logger = logging.getLogger("claude_chat")


class ExecutionService(AppService):
    """Sandbox environment checks and isolated process lifecycle operations."""

    def check_code_sandbox_environment(self):
        """返回当前平台的安全代码执行环境状态，并将失败原因写入日志。"""
        return check_sandbox_environment()

    def install_code_sandbox_environment(self):
        """下载 Linux Docker 沙盒缺失的 Python/Node.js 镜像。"""
        return install_sandbox_components()

    def start_code_execution(self, code, lang):
        """在平台安全后端中异步执行 Python/JavaScript；绝不回退到宿主进程。"""
        import codecs

        if not self._app.config.get("enable_code_sandbox", False):
            return {"error": "安全代码执行功能尚未启用，请先在设置中完成环境检测并开启。"}

        norm_lang = normalize_language(lang)
        if not norm_lang:
            return {"error": f"不支持的代码执行语言: {lang}"}

        status = check_sandbox_environment()
        if not status.get("ready"):
            reasons = "；".join(status.get("reasons") or ["当前平台没有可用的安全执行后端"])
            logger.error("拒绝不安全的代码执行回退：%s", reasons)
            return {"error": f"安全代码沙盒不可用：{reasons}"}

        timeout_seconds = normalize_execution_timeout(
            self._app.config.get("code_sandbox_timeout", 30)
        )

        launch = None
        windows_run_dir = None
        windows_lock_acquired = False
        try:
            if status.get("platform") == "linux":
                launch = prepare_linux_docker_launch(code, norm_lang)
                logger.info("正在 Docker 沙盒中启动代码，语言: %s，容器: %s", norm_lang, launch.container_name)
                proc = subprocess.Popen(
                    launch.command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=launch.environment,
                    shell=False,
                    bufsize=0,
                )
                proc_id = launch.process_id
                process_info = {
                    "proc": proc,
                    "temp_dir": str(launch.temp_dir),
                    "container_name": launch.container_name,
                    "backend": "docker",
                }
            elif status.get("platform") == "windows":
                windows_sandbox_lock = getattr(self._app, "windows_sandbox_lock", None)
                if windows_sandbox_lock is None:
                    windows_sandbox_lock = threading.Lock()
                    self._app.windows_sandbox_lock = windows_sandbox_lock
                windows_lock_acquired = windows_sandbox_lock.acquire(blocking=False)
                if not windows_lock_acquired:
                    raise RuntimeError("Windows AppContainer 当前已有任务运行，请等待其结束后重试")
                proc, proc_id, windows_run_dir = prepare_windows_appcontainer_launch(
                    code,
                    norm_lang,
                    timeout_seconds=timeout_seconds,
                )
                logger.info("正在 AppContainer + Job Object 中启动代码，语言: %s，任务: %s", norm_lang, proc_id)
                process_info = {
                    "proc": proc,
                    "temp_dir": str(windows_run_dir),
                    "backend": "appcontainer",
                }
            else:
                raise RuntimeError("当前平台没有安全代码执行后端")
            with self._app.process_lock:
                self._app.active_processes[proc_id] = process_info

            output_lock = threading.Lock()
            output_bytes = {"total": 0, "terminated": False}

            def publish(stream_type, text):
                if self._app.window:
                    js_code = f"if (window.onConsoleOutput) window.onConsoleOutput({json.dumps(proc_id)}, {json.dumps(stream_type)}, {json.dumps(text)});"
                    self._app.window.evaluate_js(js_code)
                event = {"proc_id": proc_id, "stream": stream_type, "text": text}
                for cb in list(self._app.console_listeners):
                    try:
                        cb(event)
                    except Exception:
                        pass

            def read_stream(stream, stream_type):
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while True:
                    try:
                        chunk = stream.read(1024)
                        if not chunk:
                            break
                        with output_lock:
                            output_bytes["total"] += len(chunk)
                            over_limit = output_bytes["total"] > 1024 * 1024
                            if over_limit and not output_bytes["terminated"]:
                                output_bytes["terminated"] = True
                                logger.warning("沙盒输出超过 1 MiB，终止进程 %s", proc_id)
                                terminate_sandbox_process(process_info)
                        if over_limit:
                            break
                        text = decoder.decode(chunk)
                        if text:
                            publish(stream_type, text)
                    except Exception as exc:
                        logger.error("读取沙盒 %s 管道失败: %s", stream_type, exc)
                        break
                try:
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        publish(stream_type, tail)
                except Exception:
                    pass

            def monitor_process(t_out, t_err):
                try:
                    exit_code = proc.wait(timeout=float(timeout_seconds))
                    t_out.join(timeout=2.0)
                    t_err.join(timeout=2.0)
                except subprocess.TimeoutExpired:
                    logger.warning("代码沙盒执行超时（%s 秒），终止 %s", timeout_seconds, proc_id)
                    terminate_sandbox_process(process_info)
                    exit_code = -1
                except Exception as exc:
                    logger.error("等待沙盒进程返回异常: %s", exc)
                    terminate_sandbox_process(process_info)
                    exit_code = -1
                finally:
                    with self._app.process_lock:
                        saved_info = self._app.active_processes.pop(proc_id, process_info)
                    cleanup_sandbox_process(saved_info)
                    if windows_lock_acquired:
                        self._app.windows_sandbox_lock.release()
                    if self._app.window:
                        js_code = f"if (window.onConsoleExit) window.onConsoleExit({json.dumps(proc_id)}, {exit_code});"
                        self._app.window.evaluate_js(js_code)
                    event = {"proc_id": proc_id, "stream": "exit", "exit_code": exit_code}
                    for cb in list(self._app.console_listeners):
                        try:
                            cb(event)
                        except Exception:
                            pass

            t_stdout = threading.Thread(target=read_stream, args=(proc.stdout, "stdout"), daemon=True)
            t_stderr = threading.Thread(target=read_stream, args=(proc.stderr, "stderr"), daemon=True)
            t_monitor = threading.Thread(target=monitor_process, args=(t_stdout, t_stderr), daemon=True)
            t_stdout.start()
            t_stderr.start()
            t_monitor.start()
            return {
                "process_id": proc_id,
                "backend": process_info["backend"],
                "timeout_seconds": timeout_seconds,
            }
        except Exception as exc:
            logger.exception("启动安全代码执行模块失败: %s", exc)
            if launch is not None:
                cleanup_sandbox_process({"temp_dir": str(launch.temp_dir)})
            if windows_run_dir is not None:
                cleanup_sandbox_process({"temp_dir": str(windows_run_dir)})
            if windows_lock_acquired:
                self._app.windows_sandbox_lock.release()
            return {"error": str(exc)}

    def send_console_input(self, process_id, text):
        """
        向正在后台执行的代码进程输入区写入回车文本（向其 stdin 发送信息）
        """
        logger.info(f"正在向子进程发送输入 {process_id}: {text}")
        with self._app.process_lock:
            p_info = self._app.active_processes.get(process_id)
            if not p_info:
                return False

            try:
                proc = p_info["proc"]
                if proc.poll() is None:
                    proc.stdin.write((text + "\n").encode("utf-8"))
                    proc.stdin.flush()
                    return True
            except Exception as e:
                logger.error(f"写入子进程 stdin 管道错误 {process_id}: {e}")
            return False

    def kill_console_process(self, process_id):
        """
        强制杀掉后台代码进程
        """
        logger.info(f"强制中止代码进程: {process_id}")
        with self._app.process_lock:
            p_info = self._app.active_processes.get(process_id)
            if not p_info:
                return False

            try:
                terminate_sandbox_process(p_info)
                return True
            except Exception as e:
                logger.error(f"杀死后台进程 {process_id} 失败: {e}")
            return False
