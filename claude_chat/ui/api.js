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
    } finally {
        isStreaming = false;
        if (sendBtn) {
            sendBtn.classList.remove("stop-active");
            sendBtn.title = "发送 (Ctrl+Enter)";
            const sendIcon = sendBtn.querySelector(".send-icon");
            if (sendIcon) sendIcon.innerHTML = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>`;
        }
    }
}

const apiBridge = {
    get_config: () => checkIsNative() ? window.pywebview.api.get_config() : fetchJson('/api/config'),
    save_config: (cfg) => checkIsNative() ? window.pywebview.api.save_config(cfg) : fetchJson('/api/save_config', 'POST', cfg),
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
            return new Promise((resolve) => {
                const input = document.createElement('input');
                input.type = 'file';
                input.multiple = true;
                input.onchange = async () => {
                    const files = Array.from(input.files);
                    const uploaded = [];
                    for (const f of files) {
                        const base64 = await readFileAsBase64(f);
                        const result = await apiBridge.upload_dropped_file(f.name, f.size, base64);
                        if (result) uploaded.push(result);
                    }
                    resolve(uploaded);
                };
                input.click();
            });
        }
    },
    send_message: async (convId, text, attachments) => {
        if (checkIsNative()) {
            return window.pywebview.api.send_message(convId, text, attachments);
        } else {
            const response = await fetch('/api/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ conv_id: convId, text, attachments })
            });
            readHttpStream(response, window.onStreamMessage);
            return true;
        }
    },
    edit_and_resend: async (convId, msgIdx, newContent) => {
        if (checkIsNative()) {
            return window.pywebview.api.edit_and_resend(convId, msgIdx, newContent);
        } else {
            const response = await fetch('/api/edit_and_resend', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ conv_id: convId, msg_index: msgIdx, new_content: newContent })
            });
            readHttpStream(response, window.onStreamMessage);
            return true;
        }
    },
    retry_message: async (convId, msgIdx) => {
        if (checkIsNative()) {
            return window.pywebview.api.retry_message(convId, msgIdx);
        } else {
            const response = await fetch('/api/retry_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ conv_id: convId, msg_index: msgIdx })
            });
            readHttpStream(response, window.onStreamMessage);
            return true;
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
                    const response = await fetch(`/api/console_stream/${procId}`);
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = '';
                    while (true) {
                        const { value, done } = await reader.read();
                        if (done) {
                            activeConsoleProcesses.delete(procId);
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
                })();
            }
            return result;
        }
    }
};
