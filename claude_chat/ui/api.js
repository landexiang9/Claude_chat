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
    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done) {
                // 处理遗留在 buffer 中没有以换行符分割的最后一条数据，防止断流或末行数据被截断丢弃
                if (buffer.trim()) {
                    try {
                        const parsed = JSON.parse(buffer);
                        if (callback) {
                            callback(parsed.type, parsed.data);
                        }
                    } catch (e) {
                        console.error("Failed to parse remaining stream buffer:", buffer, e);
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
                        if (callback) {
                            callback(parsed.type, parsed.data);
                        }
                    } catch (e) {
                        console.error("Failed to parse stream line:", line, e);
                    }
                }
            }
        }
    } catch (e) {
        // M-fix#11: 中途 reader.read() 失败时显式通知前端出错,避免助手占位"思考中..."永久卡死
        console.error("readHttpStream 读取流失败:", e);
        if (callback) {
            try { callback('error', e?.message || String(e)); } catch (_) {}
        }
        // 读流出错时由调用方/catch 处理 UI 复位,这里不让 finally 提前复位 isStreaming。
        throw e;
    } finally {
        // M-fix#27: 不在此提前重置 isStreaming。done/aborted/error 处理器(chat.js)在完成
        // UI 重载后再复位,提前复位会让用户在重载期间触发的 sendMessage/selectConversation
        // 覆盖刚写入的 messageList DOM。复位动作移至调用方 catch / done 处理器统一处理。
        try { reader.releaseLock(); } catch (_) {}
        if (sendBtn) {
            sendBtn.classList.remove("stop-active");
            sendBtn.title = "发送 (Ctrl+Enter)";
            const sendIcon = sendBtn.querySelector(".send-icon");
            if (sendIcon) sendIcon.innerHTML = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>`;
        }
    }
}

// M-fix#17: 串行化所有 save_config 调用,避免多个 await 句柄交错导致后到达的旧
// config 快照覆盖新值(丢失用户设置)。所有路径共享此 promise 链,逐条执行。
let _saveConfigChain = Promise.resolve();
function serializeSaveConfig(cfg) {
    const run = () => (checkIsNative()
        ? window.pywebview.api.save_config(cfg)
        : fetchJson('/api/save_config', 'POST', cfg));
    const next = _saveConfigChain.then(run, run);
    // 不让单次失败阻断整条串行队列
    _saveConfigChain = next.catch(() => {});
    return next;
}

const apiBridge = {
    get_config: () => checkIsNative() ? window.pywebview.api.get_config() : fetchJson('/api/config'),
    save_config: (cfg) => serializeSaveConfig(cfg),
    fetch_models: () => checkIsNative() ? window.pywebview.api.fetch_models() : fetchJson('/api/models'),
    check_parsers: () => {
        if (checkIsNative() && typeof window.pywebview.api.check_parsers === 'function') {
            return window.pywebview.api.check_parsers();
        } else {
            return fetchJson('/api/check_parsers');
        }
    },
    load_conversations: () => checkIsNative() ? window.pywebview.api.load_conversations() : fetchJson('/api/conversations'),
    load_conversation: (id) => checkIsNative() ? window.pywebview.api.load_conversation(id) : fetchJson(`/api/conversation/${id}`),
    new_conversation: () => checkIsNative() ? window.pywebview.api.new_conversation() : fetchJson('/api/new_conversation', 'POST'),
    delete_conversation: (id) => checkIsNative() ? window.pywebview.api.delete_conversation(id) : fetchJson(`/api/conversation/${id}`, 'DELETE'),
    get_message_packet: (id, idx) => checkIsNative() ? window.pywebview.api.get_message_packet(id, idx) : fetchJson(`/api/message_packet/${id}/${idx}`),
    paste_from_clipboard: () => checkIsNative() ? window.pywebview.api.paste_from_clipboard() : fetchJson('/api/paste_from_clipboard', 'POST'),
    upload_dropped_file: (name, size, data) => checkIsNative() ? window.pywebview.api.upload_dropped_file(name, size, data) : fetchJson('/api/upload_dropped_file', 'POST', { name, size, base64_data: data }),
    branch_conversation: (id, idx) => checkIsNative() ? window.pywebview.api.branch_conversation(id, idx) : fetchJson('/api/branch_conversation', 'POST', { conv_id: id, msg_index: idx }),
    send_console_input: (id, txt) => checkIsNative() ? window.pywebview.api.send_console_input(id, txt) : fetchJson('/api/send_console_input', 'POST', { process_id: id, text: txt }),
    kill_console_process: (id) => checkIsNative() ? window.pywebview.api.kill_console_process(id) : fetchJson('/api/kill_console_process', 'POST', { process_id: id }),
    abort_generation: () => checkIsNative() ? window.pywebview.api.abort_generation() : fetchJson('/api/abort_generation', 'POST'),
    get_logs: () => checkIsNative() ? window.pywebview.api.get_logs() : fetchJson('/api/get_logs'),
    clear_logs: () => checkIsNative() ? window.pywebview.api.clear_logs() : fetchJson('/api/clear_logs', 'POST'),

    // 自定义模型提供商管理 (保存到服务器 config.json)
    list_custom_providers: () => checkIsNative() ? window.pywebview.api.list_custom_providers() : fetchJson('/api/custom_providers'),
    add_custom_provider: (data) => checkIsNative() ? window.pywebview.api.add_custom_provider(data.name, data.api_url, data.api_key, data.models, data.temperature, data.max_tokens, data.models_api_url) : fetchJson('/api/add_custom_provider', 'POST', data),
    update_custom_provider: (data) => checkIsNative() ? window.pywebview.api.update_custom_provider(data.id, data.name, data.api_url, data.api_key, data.models, data.temperature, data.max_tokens, data.models_api_url, data.clear_api_key) : fetchJson('/api/update_custom_provider', 'POST', data),
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
            input.onchange = async () => {
                // M-fix#4: 必须捕获 reject,否则 readFileAsBase64 拒绝(>20MB 或 FileReader 错误)
                // 时 resolve 永不执行,外层 attachBtn.onclick 的 await 永久挂起,附件框失活。
                const files = Array.from(input.files);
                const uploaded = [];
                try {
                    for (const f of files) {
                        const base64 = await readFileAsBase64(f);
                        const result = await apiBridge.upload_dropped_file(f.name, f.size, base64);
                        if (result) uploaded.push(result);
                    }
                    resolve(uploaded);
                } catch (e) {
                    console.error("select_attachments 处理失败:", e);
                    reject(e);
                }
            };
            input.click();
        });
        }
    },
    send_message: async (convId, text, attachments) => {
        if (checkIsNative()) {
            return window.pywebview.api.send_message(convId, text, attachments);
        } else {
            try {
                const response = await fetch('/api/send_message', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ conv_id: convId, text, attachments })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                await readHttpStream(response, window.onStreamMessage);
                return true;
            } catch (e) {
                console.error("send_message HTTP 请求失败:", e);
                if (window.onStreamMessage) {
                    window.onStreamMessage('error', e.message || String(e));
                }
                throw e;
            }
        }
    },
    edit_and_resend: async (convId, msgIdx, newContent) => {
        if (checkIsNative()) {
            return window.pywebview.api.edit_and_resend(convId, msgIdx, newContent);
        } else {
            try {
                const response = await fetch('/api/edit_and_resend', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ conv_id: convId, msg_index: msgIdx, new_content: newContent })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                await readHttpStream(response, window.onStreamMessage);
                return true;
            } catch (e) {
                console.error("edit_and_resend HTTP 请求失败:", e);
                if (window.onStreamMessage) {
                    window.onStreamMessage('error', e.message || String(e));
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
                if (window.onStreamMessage) {
                    window.onStreamMessage('error', e.message || String(e));
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
