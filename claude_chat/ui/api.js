// 劫持 window.fetch 自动注入安全验证报头并拦截 401 未授权响应
const originalFetch = window.fetch;
window.fetch = function(input, init) {
    init = init || {};
    init.headers = init.headers || {};
    
    if (securityToken) {
        if (init.headers instanceof Headers) {
            if (!init.headers.has('X-Security-Token')) {
                init.headers.append('X-Security-Token', securityToken);
            }
        } else {
            if (!init.headers['X-Security-Token']) {
                init.headers['X-Security-Token'] = securityToken;
            }
        }
    }
    
    return originalFetch.call(this, input, init).then(response => {
        if (response.status === 401) {
            handleUnauthorized();
        }
        return response;
    });
};

// 适配浏览器访问的 API 桥接助手
function checkIsNative() {
    return typeof window.pywebview !== 'undefined' && typeof window.pywebview.api !== 'undefined';
}

async function fetchJson(url, method = 'GET', body = null) {
    const opts = { method };
    if (body) {
        opts.headers = { 'Content-Type': 'application/json' };
        opts.body = JSON.stringify(body);
    }
    try {
        const r = await fetch(url, opts);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await r.json();
    } catch (e) {
        console.error(`API Fetch Error (${url}):`, e);
        return null;
    }
}

function readFileAsBase64(file) {
    const maxSize = 20 * 1024 * 1024; // 20MB
    if (file.size > maxSize) {
        return Promise.reject(new Error("文件大小不能超过 20MB"));
    }
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}


async function readHttpStream(response, callback) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let sawTerminal = false;
    const dispatchLine = (line) => {
        try {
            const parsed = JSON.parse(line);
            if (parsed && (parsed.type === 'done' || parsed.type === 'aborted' || parsed.type === 'error')) {
                sawTerminal = true;
            }
            dispatchStreamEvent(parsed, callback);
        } catch (e) {
            console.error("Failed to parse stream line:", line, e);
        }
    };
    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done) {
                // 处理遗留在 buffer 中没有以换行符分割的最后一条数据，防止断流或末行数据被截断丢弃
                if (buffer.trim()) {
                    dispatchLine(buffer);
                }
                // 服务端静默断流(超时/异常/进程退出)时不会下发 done/aborted/error 终止事件,
                // 此时必须合成一个 error 事件复位 UI 的 isStreaming,否则"思考中..."占位永久卡死。
                if (!sawTerminal) {
                    console.warn("Stream ended without a terminal event; synthesizing error.");
                    dispatchStreamEvent({
                        version: 1,
                        type: "error",
                        data: "生成流意外中断，请重试。",
                        sequence: 0
                    }, callback);
                }
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (line.trim()) {
                    dispatchLine(line);
                }
            }
        }
    } catch (e) {
        // M-fix#11: 中途 reader.read() 失败时显式通知前端出错,避免助手占位"思考中..."永久卡死
        console.error("readHttpStream 读取流失败:", e);
        if (!sawTerminal) {
            dispatchStreamEvent({
                version: 1,
                type: "error",
                data: e?.message || String(e),
                sequence: 0
            }, callback);
        }
        e.streamEventDispatched = true;
        // 读流出错时由调用方/catch 处理 UI 复位,这里不让 finally 提前复位 isStreaming。
        throw e;
    } finally {
        // M-fix#27: 不在此提前重置 isStreaming。done/aborted/error 处理器(chat.js)在完成
        // UI 重载后再复位,提前复位会让用户在重载期间触发的 sendMessage/selectConversation
        // 覆盖刚写入的 messageList DOM。复位动作移至调用方 catch / done 处理器统一处理。
        try { reader.releaseLock(); } catch (_) {}
        if (sendBtn) {
            if (typeof setSendButtonState === "function") {
                setSendButtonState(false);
            } else {
                sendBtn.classList.remove("stop-active");
                sendBtn.title = "发送 (Ctrl+Enter)";
                const sendIcon = sendBtn.querySelector(".send-icon");
                if (sendIcon) sendIcon.textContent = "↑";
            }
        }
    }
}

function dispatchStreamEvent(event, legacyCallback) {
    if (typeof window.onStreamEvent === "function") {
        window.onStreamEvent(event);
    } else if (legacyCallback) {
        legacyCallback(event.type, event.data);
    }
}

// M-fix#17: 串行化所有 save_config 调用,避免多个 await 句柄交错导致后到达的旧
// config 快照覆盖新值(丢失用户设置)。所有路径共享此 promise 链,逐条执行。
let _saveConfigChain = Promise.resolve();
function serializeSaveConfig(cfg) {
    const run = async () => {
        const result = await (checkIsNative()
            ? window.pywebview.api.save_config(cfg)
            : fetchJson('/api/save_config', 'POST', cfg));
        if (result && typeof result === 'object' && Object.prototype.hasOwnProperty.call(result, 'success')) {
            return Boolean(result.success);
        }
        return Boolean(result);
    };
    const next = _saveConfigChain.then(run, run);
    // 不让单次失败阻断整条串行队列
    _saveConfigChain = next.catch(() => {});
    return next;
}

const apiBridge = {
    get_config: () => checkIsNative() ? window.pywebview.api.get_config() : fetchJson('/api/config'),
    save_config: (cfg) => serializeSaveConfig(cfg),
    preview_model_request: (data) => checkIsNative() ? window.pywebview.api.preview_model_request(data) : fetchJson("/api/preview_model_request", "POST", data),
    fetch_models: (platform = null) => {
        if (checkIsNative()) return window.pywebview.api.fetch_models(platform);
        const query = platform ? `?platform=${encodeURIComponent(platform)}` : '';
        return fetchJson(`/api/models${query}`);
    },
    update_model_registry: () => checkIsNative()
        ? window.pywebview.api.update_model_registry()
        : fetchJson('/api/update_model_registry', 'POST', {}),
    check_parsers: () => {
        if (checkIsNative() && typeof window.pywebview.api.check_parsers === 'function') {
            return window.pywebview.api.check_parsers();
        } else {
            return fetchJson('/api/check_parsers');
        }
    },
    check_code_sandbox_environment: () => {
        if (checkIsNative() && typeof window.pywebview.api.check_code_sandbox_environment === 'function') {
            return window.pywebview.api.check_code_sandbox_environment();
        }
        return fetchJson('/api/code_sandbox_environment');
    },
    install_code_sandbox_environment: () => {
        if (checkIsNative() && typeof window.pywebview.api.install_code_sandbox_environment === 'function') {
            return window.pywebview.api.install_code_sandbox_environment();
        }
        return fetchJson('/api/install_code_sandbox_environment', 'POST', {});
    },
    load_conversations: () => checkIsNative() ? window.pywebview.api.load_conversations() : fetchJson('/api/conversations'),
    load_conversation: (id) => checkIsNative() ? window.pywebview.api.load_conversation(id) : fetchJson(`/api/conversation/${id}`),
    new_conversation: () => checkIsNative() ? window.pywebview.api.new_conversation() : fetchJson('/api/new_conversation', 'POST'),
    delete_conversation: (id) => checkIsNative() ? window.pywebview.api.delete_conversation(id) : fetchJson(`/api/conversation/${id}`, 'DELETE'),
    get_message_packet: (id, idx) => checkIsNative() ? window.pywebview.api.get_message_packet(id, idx) : fetchJson(`/api/message_packet/${id}/${idx}`),
    paste_from_clipboard: () => checkIsNative() ? window.pywebview.api.paste_from_clipboard() : fetchJson('/api/paste_from_clipboard', 'POST'),
    paste_attachments_from_clipboard: () => checkIsNative()
        ? window.pywebview.api.paste_attachments_from_clipboard()
        : Promise.resolve({ attachments: [], errors: [] }),
    upload_dropped_file: (name, size, data) => checkIsNative() ? window.pywebview.api.upload_dropped_file(name, size, data) : fetchJson('/api/upload_dropped_file', 'POST', { name, size, base64_data: data }),
    get_attachment_preview: async (request, options = {}) => {
        if (checkIsNative()) {
            return window.pywebview.api.get_attachment_preview(request);
        }
        const response = await fetch('/api/attachment_preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request),
            signal: options.signal
        });
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {}
        if (!response.ok) {
            throw new Error(payload?.error || `附件预览请求失败 (HTTP ${response.status})`);
        }
        return payload;
    },
    discard_pending_attachment: (previewId) => checkIsNative()
        ? window.pywebview.api.discard_pending_attachment(previewId)
        : fetchJson('/api/discard_pending_attachment', 'POST', { preview_id: previewId }),
    branch_conversation: (id, idx) => checkIsNative() ? window.pywebview.api.branch_conversation(id, idx) : fetchJson('/api/branch_conversation', 'POST', { conv_id: id, msg_index: idx }),
    send_console_input: (id, txt) => checkIsNative() ? window.pywebview.api.send_console_input(id, txt) : fetchJson('/api/send_console_input', 'POST', { process_id: id, text: txt }),
    kill_console_process: (id) => checkIsNative() ? window.pywebview.api.kill_console_process(id) : fetchJson('/api/kill_console_process', 'POST', { process_id: id }),
    abort_generation: () => checkIsNative() ? window.pywebview.api.abort_generation() : fetchJson('/api/abort_generation', 'POST'),
    get_logs: () => checkIsNative() ? window.pywebview.api.get_logs() : fetchJson('/api/get_logs'),
    clear_logs: () => checkIsNative() ? window.pywebview.api.clear_logs() : fetchJson('/api/clear_logs', 'POST'),

    // 自定义模型提供商管理 (保存到服务器 config.json)
    list_custom_providers: () => checkIsNative() ? window.pywebview.api.list_custom_providers() : fetchJson('/api/custom_providers'),
    add_custom_provider: (data) => checkIsNative() ? window.pywebview.api.add_custom_provider(data.name, data.api_url, data.api_key, data.models, data.temperature, data.max_tokens, data.models_api_url, data.file_upload_enabled, data.file_upload_purpose, data.file_upload_expires_in_seconds) : fetchJson('/api/add_custom_provider', 'POST', data),
    update_custom_provider: (data) => checkIsNative() ? window.pywebview.api.update_custom_provider(data.id, data.name, data.api_url, data.api_key, data.models, data.temperature, data.max_tokens, data.models_api_url, data.clear_api_key, data.file_upload_enabled, data.file_upload_purpose, data.file_upload_expires_in_seconds) : fetchJson('/api/update_custom_provider', 'POST', data),
    remove_custom_provider: (id) => checkIsNative() ? window.pywebview.api.remove_custom_provider(id) : fetchJson('/api/remove_custom_provider', 'POST', { id }),

    save_code_block: (content, suggest_name) => {
        if (checkIsNative()) {
            return window.pywebview.api.save_code_block(content, suggest_name);
        } else {
            const blob = new Blob([content], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = suggest_name;
            a.click();
            URL.revokeObjectURL(url);
            return true;
        }
    },
    save_image: (image_data, suggest_name) => {
        if (checkIsNative()) {
            return window.pywebview.api.save_image(image_data, suggest_name);
        } else {
            const a = document.createElement('a');
            a.href = image_data;
            a.download = suggest_name || ("image_" + new Date().getTime() + ".png");
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            return true;
        }
    },
    select_attachments: () => {
        if (checkIsNative()) {
            return window.pywebview.api.select_attachments();
        } else {
            return new Promise((resolve, reject) => {
                const input = document.createElement('input');
                input.type = 'file';
                input.multiple = true;
                input.accept = '.png,.jpg,.jpeg,.gif,.webp,.pdf,.docx,.xlsx,.pptx,.txt,.py,.js,.ts,.html,.css,.md,.json,.xml,.yaml,.yml,.toml,.ini,.cfg,.csv,.sql,.c,.cpp,.h,.hpp,.java,.go,.rs,.rb,.php,.sh,.bat,.ps1,.r,.swift,.kt,.scala,.lua';
                input.hidden = true;
                document.body.appendChild(input);

                let settled = false;
                const cleanup = () => {
                    window.removeEventListener('focus', handleWindowFocus);
                    input.remove();
                };
                const finish = (callback, value) => {
                    if (settled) return;
                    settled = true;
                    cleanup();
                    callback(value);
                };
                const handleWindowFocus = () => {
                    // Some WebView versions do not emit "cancel" for a dismissed file picker.
                    setTimeout(() => {
                        if (!settled && (!input.files || input.files.length === 0)) {
                            finish(resolve, []);
                        }
                    }, 300);
                };

                input.oncancel = () => finish(resolve, []);
                input.onchange = async () => {
                    const files = Array.from(input.files || []);
                    const uploaded = [];
                    const errors = [];
                    try {
                        for (const file of files) {
                            try {
                                const base64 = await readFileAsBase64(file);
                                const result = await apiBridge.upload_dropped_file(file.name, file.size, base64);
                                if (result && !result.error) {
                                    uploaded.push(result);
                                } else {
                                    errors.push(`${file.name}: ${result?.error || '上传失败'}`);
                                }
                            } catch (error) {
                                errors.push(`${file.name}: ${error.message || String(error)}`);
                            }
                        }
                        if (errors.length > 0) uploaded.uploadErrors = errors;
                        finish(resolve, uploaded);
                    } catch (error) {
                        console.error("select_attachments 处理失败:", error);
                        finish(reject, error);
                    }
                };
                window.addEventListener('focus', handleWindowFocus);
                input.click();
            });
        }
    },
    send_message: async (convId, text, attachments, renderMarkdown = true) => {
        if (checkIsNative()) {
            const started = await window.pywebview.api.send_message(convId, text, attachments, renderMarkdown);
            if (!started) throw new Error("后端未能启动消息生成");
            return true;
        } else {
            try {
                const response = await fetch('/api/send_message', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ conv_id: convId, text, attachments, render_markdown: renderMarkdown })
                });
                if (!response.ok) {
                    let detail = "";
                    try {
                        const payload = await response.json();
                        detail = payload?.error || payload?.message || "";
                    } catch (_) {}
                    throw new Error(detail || `HTTP ${response.status}`);
                }
                await readHttpStream(response, window.onStreamMessage);
                return true;
            } catch (e) {
                console.error("send_message HTTP 请求失败:", e);
                if (!e.streamEventDispatched) {
                    dispatchStreamEvent({ version: 1, type: "error", data: e.message || String(e), sequence: 0 });
                }
                throw e;
            }
        }
    },
    edit_and_resend: async (convId, msgIdx, newContent, renderMarkdown = true) => {
        if (checkIsNative()) {
            return window.pywebview.api.edit_and_resend(convId, msgIdx, newContent, renderMarkdown);
        } else {
            try {
                const response = await fetch('/api/edit_and_resend', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ conv_id: convId, msg_index: msgIdx, new_content: newContent, render_markdown: renderMarkdown })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                await readHttpStream(response, window.onStreamMessage);
                return true;
            } catch (e) {
                console.error("edit_and_resend HTTP 请求失败:", e);
                if (!e.streamEventDispatched) {
                    dispatchStreamEvent({ version: 1, type: "error", data: e.message || String(e), sequence: 0 });
                }
                throw e;
            }
        }
    },
    retry_message: async (convId, msgIdx) => {
        if (checkIsNative()) {
            return window.pywebview.api.retry_message(convId, msgIdx);
        } else {
            try {
                const response = await fetch('/api/retry_message', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ conv_id: convId, msg_index: msgIdx })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                await readHttpStream(response, window.onStreamMessage);
                return true;
            } catch (e) {
                console.error("retry_message HTTP 请求失败:", e);
                if (!e.streamEventDispatched) {
                    dispatchStreamEvent({ version: 1, type: "error", data: e.message || String(e), sequence: 0 });
                }
                throw e;
            }
        }
    },
    start_code_execution: async (code, lang) => {
        if (checkIsNative()) {
            return window.pywebview.api.start_code_execution(code, lang);
        } else {
            const result = await fetchJson('/api/start_code_execution', 'POST', { code, lang });
            if (result && result.process_id) {
                const procId = result.process_id;
                (async () => {
                    // M-fix#25/#26: 补齐 response.ok 校验与 try/catch/finally。
                    // 非 2xx 响应体会被当成 NDJSON 解析持续报错;fetch/read 抛错时也需要复位 UI。
                    let reader;
                    try {
                        const response = await fetch(`/api/console_stream/${procId}`);
                        if (!response.ok) {
                            throw new Error(`HTTP ${response.status}`);
                        }
                        reader = response.body.getReader();
                        const decoder = new TextDecoder();
                        let buffer = '';
                        while (true) {
                            const { value, done } = await reader.read();
                            if (done) {
                                if (buffer.trim()) {
                                    try {
                                        const parsed = JSON.parse(buffer);
                                        if (parsed.stream === "exit") {
                                            if (window.onConsoleExit) {
                                                window.onConsoleExit(procId, parsed.exit_code);
                                            }
                                        } else {
                                            if (window.onConsoleOutput) {
                                                window.onConsoleOutput(procId, parsed.stream, parsed.text);
                                            }
                                        }
                                    } catch (e) {
                                        console.error("Failed to parse remaining console stream buffer:", buffer, e);
                                    }
                                }
                                break;
                            }
                            buffer += decoder.decode(value, { stream: true });
                            const lines = buffer.split('\n');
                            buffer = lines.pop();
                            for (const line of lines) {
                                if (line.trim()) {
                                    try {
                                        const parsed = JSON.parse(line);
                                        if (parsed.stream === "exit") {
                                            if (window.onConsoleExit) {
                                                window.onConsoleExit(procId, parsed.exit_code);
                                            }
                                        } else {
                                            if (window.onConsoleOutput) {
                                                window.onConsoleOutput(procId, parsed.stream, parsed.text);
                                            }
                                        }
                                    } catch (e) {
                                        console.error("Failed to parse console stream line:", line, e);
                                    }
                                }
                            }
                        }
                    } catch (e) {
                        console.error("读取终端输出流失败:", e);
                        if (window.onConsoleOutput) {
                            window.onConsoleOutput(procId, "error", String(e?.message || e));
                        }
                        if (window.onConsoleExit) {
                            window.onConsoleExit(procId, -1);
                        }
                    } finally {
                        // 取消/断开后清理活跃进程跟踪,避免 UI 卡在启动提示
                        activeConsoleProcesses.delete(procId);
                        if (reader) { try { reader.cancel().catch(() => {}); } catch (_) {} }
                    }
                })();
            }
            return result;
        }
    }
};
