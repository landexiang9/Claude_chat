// 全局状态管理
let config = {};
let conversations = [];
let currentConvId = null;
let currentConv = null;
let attachments = [];
let isStreaming = false;
let isSending = false;
let streamingText = "";
let streamingThinking = "";
let availableModels = [];
let clearApiKeyPending = false; // 标记是否需要断开并清除 API Key
let clearDeepseekKeyPending = false;
let clearGeminiKeyPending = false;
let clearTavilyKeyPending = false;
let clearJinaKeyPending = false;
let initialEnableServer = false;
let initialServerPort = 8000;
let initialEnableSsl = false;

function safeUrl(url) {
    if (!url) return "#";
    const cleaned = url.trim();
    if (cleaned.startsWith("http://") || cleaned.startsWith("https://")) {
        return cleaned;
    }
    return "#";
}

function escapeHtml(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


// 解决移动端输入法/虚拟键盘弹出时，导致顶部导航栏或底部输入框被遮挡/移出可视区的问题。
// 1. 使用 Visual Viewport API 动态监控实际可视区域高度，自动调整主容器高度，使输入框和导航栏都在可视区内。
if (window.visualViewport) {
    const handleViewportChange = () => {
        const height = window.visualViewport.height;
        const appContainer = document.querySelector('.app-container');
        if (appContainer) {
            appContainer.style.height = `${height}px`;
        }
        document.body.style.height = `${height}px`;
        // 如果是输入聚焦引发的视口变化，同时强制窗口滚动复位
        window.scrollTo(0, 0);
    };
    window.visualViewport.addEventListener('resize', handleViewportChange);
    window.visualViewport.addEventListener('scroll', handleViewportChange);
    // 页面加载完毕后初次执行
    window.addEventListener('DOMContentLoaded', handleViewportChange);
    window.addEventListener('load', handleViewportChange);
}

// 2. 拦截一切 window 级别的默认滚动，确保视口永远锁定在 (0, 0)
window.addEventListener('scroll', () => {
    if (window.scrollY !== 0 || window.scrollX !== 0) {
        window.scrollTo(0, 0);
    }
});

// 在任何输入框聚焦时，双重保险强制重置页面视口滚动条到最顶部，解决各平台浏览器默认滚动行为
document.addEventListener('focusin', (e) => {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
        setTimeout(() => {
            window.scrollTo(0, 0);
            if (window.visualViewport) {
                const height = window.visualViewport.height;
                const appContainer = document.querySelector('.app-container');
                if (appContainer) {
                    appContainer.style.height = `${height}px`;
                }
                document.body.style.height = `${height}px`;
            }
        }, 30);
    }
});

// 访问令牌 (Security Token) 安全认证与存储
const urlParams = new URLSearchParams(window.location.search);
let securityToken = urlParams.get('token') || localStorage.getItem('security_token') || '';

if (urlParams.get('token')) {
    localStorage.setItem('security_token', urlParams.get('token'));
    // 移除地址栏中的 token 参数，防止 Referer 泄露
    const url = new URL(window.location);
    url.searchParams.delete('token');
    window.history.replaceState({}, document.title, url.toString());
}

function handleUnauthorized() {
    const existing = document.getElementById('auth-overlay');
    if (existing) {
        const errorMsg = existing.querySelector('#auth-error-msg');
        if (errorMsg) errorMsg.style.display = 'block';
        return;
    }
    
    const overlay = document.createElement('div');
    overlay.id = 'auth-overlay';
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = '9999';
    overlay.style.display = 'flex';
    overlay.style.justifyContent = 'center';
    overlay.style.alignItems = 'center';
    
    overlay.innerHTML = `
        <div class="modal-card" style="width: 380px;">
            <div class="modal-header">
                <h2>🔒 安全访问认证</h2>
            </div>
            <div class="modal-body" style="gap: 12px; padding: 20px;">
                <p style="font-size: 12px; color: var(--subtext1); line-height: 1.5;">
                    本服务已开启安全防护。若您正在从局域网或外部浏览器访问，请输入终端或日志中打印的 <b>Security Token</b>。
                </p>
                <div class="form-group">
                    <label for="auth-token-input">安全验证 Token (Security Token):</label>
                    <input type="password" id="auth-token-input" placeholder="输入 Security Token..." style="padding: 8px 10px; border-radius: 6px; border: 1px solid var(--surface0); background-color: var(--crust); color: var(--text);">
                </div>
                <div id="auth-error-msg" style="color: var(--red); font-size: 11px; display: none;">Token 错误或无效，请重新输入。</div>
            </div>
            <div class="modal-footer" style="padding: 12px 18px;">
                <button id="auth-submit-btn" class="btn btn-primary" style="width: 100%;">验证并连接</button>
            </div>
        </div>
    `;
    
    document.body.appendChild(overlay);
    
    const input = document.getElementById('auth-token-input');
    const submitBtn = document.getElementById('auth-submit-btn');
    const errorMsg = document.getElementById('auth-error-msg');
    
    const submitToken = () => {
        const val = input.value.trim();
        if (val) {
            securityToken = val;
            localStorage.setItem('security_token', val);
            document.body.removeChild(overlay);
            isInitialized = false;
            isInitializing = false;
            initApp();
        } else {
            errorMsg.style.display = 'block';
        }
    };
    
    submitBtn.onclick = submitToken;
    input.onkeydown = (e) => {
        if (e.key === 'Enter') submitToken();
    };
}

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


// 绑定 DOM 界面元素
const convList = document.getElementById("conv-list");
const newChatBtn = document.getElementById("new-chat-btn");
const modelSelect = document.getElementById("model-select");
const apiStatusLed = document.getElementById("api-status-led");
const tokenLabel = document.getElementById("token-label");
const statusLabel = document.getElementById("status-label");
const messageList = document.getElementById("message-list");
const chatViewport = document.getElementById("chat-viewport");
const scrollAnchor = document.getElementById("scroll-anchor");

const attachBtn = document.getElementById("attach-btn");
const attachmentsArea = document.getElementById("attachments-area");
const inputBox = document.getElementById("input-box");
const sendBtn = document.getElementById("send-btn");

const settingsBtn = document.getElementById("settings-btn");
const settingsModal = document.getElementById("settings-modal");
const saveSettingsBtn = document.getElementById("save-settings-btn");
const apiKeyInput = document.getElementById("api-key-input");
const toggleKeyVisibility = document.getElementById("toggle-key-visibility");

const apiKeyInputContainer = document.getElementById("api-key-input-container");
const apiKeyStatusContainer = document.getElementById("api-key-status-container");
const changeKeyBtn = document.getElementById("change-key-btn");
const disconnectKeyBtn = document.getElementById("disconnect-key-btn");

// Platform selection & DeepSeek / Gemini config elements
const platformSelect = document.getElementById("platform-select");
const deepseekApiKeyInput = document.getElementById("deepseek-api-key-input");
const toggleDeepseekKeyVisibility = document.getElementById("toggle-deepseek-key-visibility");
const deepseekApiKeyInputContainer = document.getElementById("deepseek-api-key-input-container");
const deepseekApiKeyStatusContainer = document.getElementById("deepseek-api-key-status-container");
const changeDeepseekKeyBtn = document.getElementById("change-deepseek-key-btn");
const disconnectDeepseekKeyBtn = document.getElementById("disconnect-deepseek-key-btn");
const deepseekApiUrlInput = document.getElementById("deepseek-api-url-input");

const geminiApiKeyInput = document.getElementById("gemini-api-key-input");
const toggleGeminiKeyVisibility = document.getElementById("toggle-gemini-key-visibility");
const geminiApiKeyInputContainer = document.getElementById("gemini-api-key-input-container");
const geminiApiKeyStatusContainer = document.getElementById("gemini-api-key-status-container");
const changeGeminiKeyBtn = document.getElementById("change-gemini-key-btn");
const disconnectGeminiKeyBtn = document.getElementById("disconnect-gemini-key-btn");
const geminiApiUrlInput = document.getElementById("gemini-api-url-input");

const ocrModeSelect = document.getElementById("ocr-mode-select");
const ocrCloudModelSelect = document.getElementById("ocr-cloud-model-select");
const badgeDocx = document.getElementById("badge-docx");
const badgeOpenpyxl = document.getElementById("badge-openpyxl");
const badgePptx = document.getElementById("badge-pptx");
const badgePypdf = document.getElementById("badge-pypdf");
const badgeEasyocr = document.getElementById("badge-easyocr");

const serverTokenInput = document.getElementById("server-token-input");
const toggleTokenVisibility = document.getElementById("toggle-token-visibility");
const tempSlider = document.getElementById("temp-slider");
const tempLabelTitle = document.getElementById("temp-label-title");
const maxTokensInput = document.getElementById("max-tokens-input");
const budgetGroup = document.getElementById("budget-group");
const budgetTokensInput = document.getElementById("budget-tokens-input");
const thinkingLevelGroup = document.getElementById("thinking-level-group");
const thinkingLevelSelect = document.getElementById("thinking-level-select");
const autoRunCodeInput = document.getElementById("auto-run-code-input");
const enableCodeSandboxInput = document.getElementById("enable-code-sandbox-input");
const fontModeSelect = document.getElementById("font-mode-select");
const enableServerInput = document.getElementById("enable-server-input");
const syncConfigInput = document.getElementById("sync-config-input");
const onlyServerInput = document.getElementById("only-server-input");
const enableSslInput = document.getElementById("enable-ssl-input");
const serverPortInput = document.getElementById("server-port-input");
const webSearchBtn = document.getElementById("web-search-btn");
const searchEngineSelect = document.getElementById("search-engine-select");
const tavilyKeyGroup = document.getElementById("tavily-key-group");
const tavilyKeyInput = document.getElementById("tavily-key-input");
const tavilyKeyInputContainer = document.getElementById("tavily-key-input-container");
const tavilyKeyStatusContainer = document.getElementById("tavily-key-status-container");
const toggleTavilyVisibility = document.getElementById("toggle-tavily-visibility");
const changeTavilyBtn = document.getElementById("change-tavily-btn");
const disconnectTavilyBtn = document.getElementById("disconnect-tavily-btn");

const jinaKeyGroup = document.getElementById("jina-key-group");
const jinaKeyInput = document.getElementById("jina-key-input");
const jinaKeyInputContainer = document.getElementById("jina-key-input-container");
const jinaKeyStatusContainer = document.getElementById("jina-key-status-container");
const toggleJinaVisibility = document.getElementById("toggle-jina-visibility");
const changeJinaBtn = document.getElementById("change-jina-btn");
const disconnectJinaBtn = document.getElementById("disconnect-jina-btn");
const webPageParserSelect = document.getElementById("web-page-parser-select");

const viewLogsBtn = document.getElementById("view-logs-btn");
const logsModal = document.getElementById("logs-modal");
const refreshLogsBtn = document.getElementById("refresh-logs-btn");
const clearLogsBtn = document.getElementById("clear-logs-btn");
const copyLogsBtn = document.getElementById("copy-logs-btn");
const logsContainer = document.getElementById("logs-container");
const logsContent = document.getElementById("logs-content");

const packetModal = document.getElementById("packet-modal");
const packetContent = document.getElementById("packet-content");
const copyPacketBtn = document.getElementById("copy-packet-btn");

const proxyBtn = document.getElementById("proxy-btn");
const proxyModal = document.getElementById("proxy-modal");
const saveProxyBtn = document.getElementById("save-proxy-btn");
const customProxyGroup = document.getElementById("custom-proxy-group");
const proxyUrlInput = document.getElementById("proxy-url-input");

const clearChatBtn = document.getElementById("clear-chat-btn");
const deleteModal = document.getElementById("delete-modal");
const confirmDeleteBtn = document.getElementById("confirm-delete-btn");

// 初始化 Marked Markdown 解析配置
if (typeof marked !== 'undefined') {
    marked.setOptions({
        breaks: true,
        gfm: true
    });
}

function parseMarkdown(text) {
    if (typeof marked !== 'undefined') {
        const parsedHtml = marked.parse(text);
        if (typeof DOMPurify !== 'undefined') {
            return DOMPurify.sanitize(parsedHtml);
        }
        return parsedHtml;
    }
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;")
        .replace(/\n/g, "<br>");
}

function applyFontMode() {
    if (config && config.font_mode === "system") {
        document.body.classList.add("font-mode-system");
    } else {
        document.body.classList.remove("font-mode-system");
    }
}

function updateSearchBtnUI() {
    if (!webSearchBtn) return;
    if (config.enable_web_search) {
        webSearchBtn.classList.add("active");
        webSearchBtn.title = `联网搜索：开启 (引擎: ${config.web_search_engine || "google"})`;
    } else {
        webSearchBtn.classList.remove("active");
        webSearchBtn.title = "联网搜索：关闭";
    }
}

if (webSearchBtn) {
    webSearchBtn.onclick = async () => {
        if (isStreaming) {
            statusLabel.textContent = "正在生成中，无法切换联网搜索状态";
            return;
        }
        config.enable_web_search = !config.enable_web_search;
        updateSearchBtnUI();
        await apiBridge.save_config({
            enable_web_search: config.enable_web_search
        });
        statusLabel.textContent = config.enable_web_search ? "🌐 联网搜索已开启" : "🔍 联网搜索已关闭";
        setTimeout(() => { if (statusLabel.textContent.includes("联网搜索")) statusLabel.textContent = "就绪"; }, 1500);
    };
}

let isInitialized = false;
let isInitializing = false;

async function initApp() {
    if (isInitialized || isInitializing) return;
    isInitializing = true;
    
    try {
        // 1. 拉取系统基础配置
        const fetchedConfig = await apiBridge.get_config();
        if (!fetchedConfig) {
            throw new Error("Config fetch returned null/undefined");
        }
        config = fetchedConfig;
        applyFontMode();
        updateSearchBtnUI();
        updateLedStatus();
        
        // 初始化系统提示词下拉菜单
        renderSystemPromptSelect();
        
        // 初始化平台选择器状态
        if (platformSelect) {
            platformSelect.value = config.active_platform || "claude";
        }
        
        // 2. 拉取并配置可用模型下拉菜单
        const models = await apiBridge.fetch_models();
        if (!models) {
            throw new Error("Models fetch returned null/undefined");
        }
        availableModels = models;
        updateModelList(models);
        
        // 3. 加载历史对话卡片
        await loadConversations();
        
        // 4. 加载初始对话记录
        if (conversations.length > 0) {
            await selectConversation(conversations[0].id);
        } else {
            await startNewChat();
        }
        
        isInitialized = true;
        console.log("App successfully initialized.");
    } catch (e) {
        console.error("Failed to initialize app, retrying in 1000ms...", e);
        isInitialized = false;
        setTimeout(initApp, 1000);
    } finally {
        isInitializing = false;
    }
}

// 等待 WebView2 容器加载或标准的 DOM 构建就绪
window.addEventListener("pywebviewready", () => {
    console.log("pywebview API is ready. Initializing app...");
    initApp();
});
window.addEventListener("DOMContentLoaded", () => {
    setTimeout(() => {
        if (!isInitialized && !isInitializing) {
            console.log("DOMContentLoaded: pywebview API not ready yet, trying fallback...");
            initApp();
        }
    }, 300);
});
if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(() => {
        if (!isInitialized && !isInitializing) {
            initApp();
        }
    }, 300);
}

function updateLedStatus() {
    let hasKey = false;
    const platform = config.active_platform || "claude";
    if (platform === "claude") {
        hasKey = !!(config.has_api_key || (config.api_key && config.api_key.trim()));
    } else if (platform === "deepseek") {
        hasKey = !!(config.has_deepseek_api_key || (config.deepseek_api_key && config.deepseek_api_key.trim()));
    } else if (platform === "gemini") {
        hasKey = !!(config.has_gemini_api_key || (config.gemini_api_key && config.gemini_api_key.trim()));
    }
    
    if (hasKey) {
        apiStatusLed.className = "status-led online";
        apiStatusLed.title = `API 已连接 (${platform.toUpperCase()})`;
    } else {
        apiStatusLed.className = "status-led";
        apiStatusLed.title = `API 未连接 (${platform.toUpperCase()})`;
    }
}

// 渲染下拉菜单中的模型选项列表
function updateModelList(models) {
    modelSelect.innerHTML = "";
    if (!models || models.length === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "无可用模型";
        modelSelect.appendChild(option);
        return;
    }
    let hasSelected = false;
    models.forEach(m => {
        const mId = typeof m === 'string' ? m : m.id;
        const mName = typeof m === 'string' ? m : (m.display_name || m.id);
        const option = document.createElement("option");
        option.value = mId;
        option.textContent = mName;
        if (mId === config.model) {
            option.selected = true;
            hasSelected = true;
        }
        modelSelect.appendChild(option);
    });
    if (!hasSelected && models.length > 0) {
        const firstId = typeof models[0] === 'string' ? models[0] : models[0].id;
        config.model = firstId;
        modelSelect.value = firstId;
        apiBridge.save_config({ model: firstId });
        if (currentConvId) {
            const conv = conversations.find(c => c.id === currentConvId);
            if (conv) {
                conv.model = firstId;
            }
        }
    }
}

// 绑定模型列表更新的全局回调函数
window.onModelsUpdated = (models) => {
    availableModels = models;
    updateModelList(models);
};

// 监听模型切换事件
modelSelect.addEventListener("change", async (e) => {
    config.model = e.target.value;
    
    // 安全防护：检测新切换的模型是否支持 Extended Thinking
    const modelObj = availableModels.find(m => (typeof m === 'object' && m.id === config.model));
    if (modelObj && !modelObj.thinking_supported) {
        config.thinking_enabled = false;
    }
    
    // 同步到当前的对话缓存列表数据中
    if (currentConvId) {
        const conv = conversations.find(c => c.id === currentConvId);
        if (conv) {
            conv.model = config.model;
            if (modelObj && !modelObj.thinking_supported) {
                conv.thinking = null;
            }
        }
    }
    await apiBridge.save_config(config);
    statusLabel.textContent = `模型切换为: ${config.model}`;
});

if (platformSelect) {
    platformSelect.addEventListener("change", async (e) => {
        config.active_platform = e.target.value;
        modelSelect.innerHTML = '<option value="">正在加载模型...</option>';
        if (config.active_platform === "deepseek") {
            statusLabel.textContent = "已切换至 DeepSeek (本地文档解析与 OCR 提取生效)";
        } else {
            statusLabel.textContent = `已切换至平台: ${config.active_platform}`;
        }
        await apiBridge.save_config({ active_platform: config.active_platform });
        updateLedStatus();
        await apiBridge.fetch_models();
    });
}

// 加载历史会话列表数据
async function loadConversations() {
    conversations = await apiBridge.load_conversations();
    renderConversations();
}

// 渲染侧边栏中的对话卡片列表
function renderConversations() {
    convList.innerHTML = "";
    conversations.forEach(c => {
        const item = document.createElement("div");
        item.className = `conv-item ${c.id === currentConvId ? "active" : ""}`;
        item.onclick = () => selectConversation(c.id);
        
        const title = document.createElement("span");
        title.className = "conv-title";
        title.textContent = c.title || "新对话";
        item.appendChild(title);
        
        const delBtn = document.createElement("button");
        delBtn.className = "delete-conv-btn";
        delBtn.innerHTML = "&times;";
        delBtn.onclick = (e) => {
            e.stopPropagation();
            showDeleteConfirm(c.id);
        };
        item.appendChild(delBtn);
        
        convList.appendChild(item);
    });
}

// 提取单个消息内或后续反馈消息内的工具调用及搜索结果
function extractToolCallsFromMsg(msg, nextMsg) {
    let toolCalls = [];
    if (!msg || msg.role !== "assistant") return toolCalls;
    
    // 1. 处理 Claude 官方原生搜索 (在同一个 assistant 消息内部的 content 列表中)
    if (Array.isArray(msg.content)) {
        msg.content.forEach(item => {
            if (item && (item.type === "server_tool_use" || item.type === "tool_use") && item.name === "web_search") {
                const toolId = item.id;
                const query = item.input ? item.input.query : "";
                
                // 查找同消息下的 web_search_tool_result 结果块
                const resultBlock = msg.content.find(r => r && r.type === "web_search_tool_result" && r.tool_use_id === toolId);
                const results = [];
                if (resultBlock && Array.isArray(resultBlock.content)) {
                    resultBlock.content.forEach(r => {
                        if (r && r.type === "web_search_result") {
                            results.push({
                                title: r.title || "网页链接",
                                url: r.url || "",
                                snippet: r.title || ""
                            });
                        }
                    });
                }
                toolCalls.push({ type: "search", query, results, engine: "claude", usage: null });
            }
        });
    }
    
    // 2. 处理 client-side 搜索和网页拉取 (需要配合下一条 user 反馈消息)
    if (nextMsg && nextMsg.role === "user" && isToolResultMsg(nextMsg.content) && Array.isArray(msg.content)) {
        msg.content.forEach(item => {
            if (item && item.type === "tool_use") {
                const toolResult = Array.isArray(nextMsg.content) ? nextMsg.content.find(r => r && r.type === "tool_result" && r.tool_use_id === item.id) : null;
                if (toolResult) {
                    const rawContent = toolResult.content || "";
                    if (item.name === "search_web") {
                        const query = item.input ? item.input.query : "";
                        const engine = extractSearchEngineFromText(rawContent);
                        const usage = extractUsageFromText(rawContent);
                        const cleanedContent = cleanToolResultText(rawContent);
                        const results = parseToolResultText(cleanedContent);
                        toolCalls.push({ type: "search", query, results, engine, usage });
                    } else if (item.name === "fetch_webpage") {
                        const url = item.input ? item.input.url : "";
                        const cleanedContent = cleanToolResultText(rawContent);
                        const contentLen = cleanedContent.length;
                        const parser = extractParserFromText(rawContent);
                        const usage = extractUsageFromText(rawContent);
                        toolCalls.push({ type: "fetch", url, content_len: contentLen, parser, usage });
                    }
                }
            }
        });
    }
    
    return toolCalls;
}

// 选中并加载指定的对话
async function selectConversation(id) {
    if (isStreaming) return;
    currentConvId = id;
    
    // 在移动端选中对话后，自动收起侧边栏
    const appContainer = document.querySelector(".app-container");
    if (appContainer) {
        appContainer.classList.remove("sidebar-open");
    }
    
    // 更新选中卡片的高亮样式
    const items = convList.querySelectorAll(".conv-item");
    items.forEach((item, index) => {
        if (conversations[index] && conversations[index].id === id) {
            item.className = "conv-item active";
        } else {
            item.className = "conv-item";
        }
    });

    messageList.innerHTML = "";
    
    const conv = await apiBridge.load_conversation(id);
    if (!conv) return;
    currentConv = conv;
    
    // 设置 Token 统计标签展示
    if (conv.input_tokens !== undefined && conv.output_tokens !== undefined) {
        tokenLabel.textContent = `Token: ${conv.input_tokens} in / ${conv.output_tokens} out`;
    } else {
        tokenLabel.textContent = "Token: --";
    }

    if (conv.model) {
        let targetPlatform = config.active_platform;
        if (conv.model.includes("claude")) targetPlatform = "claude";
        else if (conv.model.includes("deepseek")) targetPlatform = "deepseek";
        else if (conv.model.includes("gemini") || conv.model.includes("learnlm")) targetPlatform = "gemini";
        
        let needSave = false;
        const configUpdate = {};
        
        if (targetPlatform && targetPlatform !== config.active_platform) {
            config.active_platform = targetPlatform;
            if (typeof platformSelect !== 'undefined' && platformSelect) {
                platformSelect.value = targetPlatform;
            }
            configUpdate.active_platform = targetPlatform;
            needSave = true;
        }
        
        if (conv.model !== config.model) {
            config.model = conv.model;
            configUpdate.model = conv.model;
            needSave = true;
        }
        
        if (needSave) {
            await apiBridge.save_config(configUpdate);
            if (configUpdate.active_platform) {
                const models = await apiBridge.fetch_models();
                if (models) {
                    availableModels = models;
                    updateModelList(models);
                }
            }
        }

        const options = modelSelect.querySelectorAll("option");
        options.forEach(opt => {
            opt.selected = (opt.value === conv.model);
        });
    }

    // 遍历并渲染当前对话的所有消息历史
    const messages = conv.messages || [];
    for (let i = 0; i < messages.length; i++) {
        const msg = messages[i];
        
        // 跳过独立的 tool_result 用户消息气泡
        if (msg.role === "user" && isToolResultMsg(msg.content)) {
            continue;
        }
        
        let toolCalls = extractToolCallsFromMsg(msg, i + 1 < messages.length ? messages[i + 1] : null);
        let mergedThinking = msg.thinking || "";
        let mergedContent = msg.content;
        let currentI = i;

        // 如果是 assistant 消息，贪婪地向后合并由 tool_result 隔开的后续 assistant 消息
        if (msg.role === "assistant") {
            // 深拷贝 content 避免修改源数据
            if (Array.isArray(mergedContent)) {
                mergedContent = [...mergedContent];
            }
            while (i + 2 < messages.length && 
                   messages[i+1].role === "user" && isToolResultMsg(messages[i+1].content) && 
                   messages[i+2].role === "assistant") {
                
                let nextAstMsg = messages[i+2];
                
                // 拼接 thinking
                if (nextAstMsg.thinking) {
                    mergedThinking = (mergedThinking ? mergedThinking + "\n\n" : "") + nextAstMsg.thinking;
                }
                
                // 提取并拼接 nextAstMsg 的纯文本内容
                let extraText = "";
                if (typeof nextAstMsg.content === "string") {
                    extraText = nextAstMsg.content;
                } else if (Array.isArray(nextAstMsg.content)) {
                    extraText = nextAstMsg.content.filter(x => x && x.type === "text").map(x => x.text).join("\n");
                }
                
                if (extraText) {
                    if (typeof mergedContent === "string") {
                        mergedContent += (mergedContent ? "\n\n" : "") + extraText;
                    } else if (Array.isArray(mergedContent)) {
                        mergedContent.push({ type: "text", text: "\n\n" + extraText });
                    }
                }
                
                // 拼接 tool_calls
                let nextToolCalls = extractToolCallsFromMsg(nextAstMsg, i + 3 < messages.length ? messages[i+3] : null);
                if (nextToolCalls) {
                    if (!toolCalls) toolCalls = [];
                    toolCalls = toolCalls.concat(nextToolCalls);
                }
                
                i += 2;
            }
        }
        
        appendMessage(msg.role, mergedContent, mergedThinking, false, currentI, toolCalls);
    }
    
    scrollChatBottom();
}

// 开启全新对话会话
async function startNewChat() {
    if (isStreaming) return;
    const newConv = await apiBridge.new_conversation();
    await loadConversations();
    await selectConversation(newConv.id);
}

newChatBtn.onclick = startNewChat;

// 向对话展示区追加一条消息气泡
function isToolResultMsg(content) {
    if (Array.isArray(content)) {
        return content.some(item => item && item.type === "tool_result");
    }
    return false;
}

function getToolUseFromContent(content) {
    if (Array.isArray(content)) {
        return content.find(item => item && item.type === "tool_use" && item.name === "search_web");
    }
    return null;
}

function getToolResultFromContent(content) {
    if (Array.isArray(content)) {
        const block = content.find(item => item && item.type === "tool_result");
        return block ? block.content : "";
    }
    return "";
}

function extractSearchEngineFromText(text) {
    if (!text) return "";
    const match = text.match(/\[Search\s+Engine:\s*([^\]]+)\]/i);
    return match ? match[1].trim() : "";
}

function extractParserFromText(text) {
    if (!text) return "";
    const match = text.match(/\[Web\s+Reader:\s*([^\]]+)\]/i);
    return match ? match[1].trim() : "";
}

function extractUsageFromText(text) {
    if (!text) return null;
    const match = text.match(/\[Usage:\s*([^\]]+)\]/i);
    if (match) {
        try {
            return JSON.parse(match[1]);
        } catch(e) {}
    }
    return null;
}

function cleanToolResultText(text) {
    if (!text) return "";
    return text.replace(/\[Search\s+Engine:\s*([^\]]+)\]/i, "")
               .replace(/\[Web\s+Reader:\s*([^\]]+)\]/i, "")
               .replace(/\[Usage:\s*([^\]]+)\]/i, "")
               .trim();
}

function parseToolResultText(text) {
    if (!text || text.includes("No results found")) return [];
    const items = [];
    const rawParts = text.split(/\[\d+\]\s+Title:\s+/);
    rawParts.slice(1).forEach(part => {
        const lines = part.split("\n");
        if (lines.length > 0) {
            const title = lines[0].trim();
            let url = "";
            let snippet = "";
            
            const urlLine = lines.find(l => l.startsWith("URL:"));
            if (urlLine) url = urlLine.replace("URL:", "").trim();
            
            const snippetLine = lines.find(l => l.startsWith("Snippet:"));
            if (snippetLine) snippet = snippetLine.replace("Snippet:", "").trim();
            
            if (title) {
                items.push({ title, url, snippet });
            }
        }
    });
    return items;
}

function appendMessage(role, content, thinking, isStreamingPlaceholder = false, msgIndex = -1, toolCalls = null) {
    const row = document.createElement("div");
    row.className = `message-row ${role}`;
    if (msgIndex !== -1) {
        row.setAttribute("data-msg-index", msgIndex);
    }
    if (isStreamingPlaceholder) {
        row.id = "streaming-msg-row";
    }

    const card = document.createElement("div");
    card.className = "message-card";
    
    // 渲染消息卡片头部（角色与操作动作按钮）
    const header = document.createElement("div");
    header.className = "message-header";
    
    const sender = document.createElement("div");
    sender.className = "sender-info";
    sender.innerHTML = role === "user" ? "👤 You" : "🤖 Claude";
    header.appendChild(sender);
    
    const meta = document.createElement("div");
    meta.className = "meta-info";
    
    const time = document.createElement("span");
    time.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    meta.appendChild(time);
    
    if (!isStreamingPlaceholder) {
        const copy = document.createElement("button");
        copy.className = "copy-btn";
        copy.textContent = "📋";
        copy.title = "复制消息内容";
        copy.onclick = () => copyText(typeof content === 'string' ? content : JSON.stringify(content));
        meta.appendChild(copy);

        if (msgIndex !== -1) {
            const packet = document.createElement("button");
            packet.className = "copy-btn";
            packet.style.marginLeft = "6px";
            packet.textContent = "📦";
            packet.title = "查看原始数据包";
            packet.onclick = () => showPacketModal(currentConvId, msgIndex);
            meta.appendChild(packet);
            
            const branch = document.createElement("button");
            branch.className = "copy-btn";
            branch.style.marginLeft = "6px";
            branch.textContent = "🌿";
            branch.title = "从此消息创建分支对话";
            branch.onclick = () => branchConversation(msgIndex);
            meta.appendChild(branch);
            
            if (role === "user") {
                const edit = document.createElement("button");
                edit.className = "copy-btn";
                edit.style.marginLeft = "6px";
                edit.textContent = "✏️";
                edit.title = "编辑并重新发送";
                edit.onclick = () => editUserMessage(msgIndex);
                meta.appendChild(edit);
            }
            
            if (role === "assistant" && currentConv && msgIndex === currentConv.messages.length - 1) {
                const retry = document.createElement("button");
                retry.className = "copy-btn";
                retry.style.marginLeft = "6px";
                retry.textContent = "🔄";
                retry.title = "不满意，重新生成";
                retry.onclick = () => retryAssistantMessage(msgIndex);
                meta.appendChild(retry);
            }
        }
    }
    header.appendChild(meta);
    card.appendChild(header);

    // 如果是 Assistant 推理过程，渲染推理折叠面板
    if (role === "assistant" && (thinking || isStreamingPlaceholder)) {
        const thinkContainer = document.createElement("div");
        thinkContainer.className = `thinking-container ${!thinking ? "hidden" : ""}`;
        thinkContainer.id = isStreamingPlaceholder ? "streaming-thinking-container" : "";
        
        const thinkToggle = document.createElement("button");
        thinkToggle.className = "thinking-toggle";
        const tokenEstimate = thinking ? Math.floor(thinking.length / 2) : 0;
        thinkToggle.textContent = `▶ 思考过程 (~${tokenEstimate} tokens)`;
        
        const thinkBox = document.createElement("div");
        thinkBox.className = "thinking-box hidden";
        thinkBox.textContent = thinking || "";
        
        thinkToggle.onclick = () => {
            const isHidden = thinkBox.classList.toggle("hidden");
            thinkToggle.textContent = `${isHidden ? "▶" : "▼"} 思考过程 (~${Math.floor(thinkBox.textContent.length / 2)} tokens)`;
        };
        
        thinkContainer.appendChild(thinkToggle);
        thinkContainer.appendChild(thinkBox);
        card.appendChild(thinkContainer);
    }

    // 渲染消息正文（Markdown 解析）
    const body = document.createElement("div");
    body.className = "message-body";
    body.id = isStreamingPlaceholder ? "streaming-message-body" : "";
    
    if (typeof content === "string") {
        body.innerHTML = parseMarkdown(content);
    } else if (Array.isArray(content)) {
        // 处理包含多媒体及 PDF 的复杂消息数组结构
        let textContent = "";
        content.forEach(item => {
            if (item.type === "text") {
                textContent += item.text;
            }
        });
        body.innerHTML = parseMarkdown(textContent);
    }
    
    // 渲染静态联网搜索与网页内容卡片
    if (role === "assistant" && Array.isArray(toolCalls) && toolCalls.length > 0) {
        toolCalls.forEach(tc => {
            if (tc.type === "search") {
                const searchCard = document.createElement("div");
                searchCard.className = "search-card collapsed"; // 历史记录默认折叠
                
                const results = tc.results || [];
                
                searchCard.innerHTML = `
                    <div class="search-card-header">
                        <div class="search-status-wrapper">
                            <span class="search-radar-done">🌐</span>
                            <span>已找到 ${results.length} 个关于“${escapeHtml(tc.query)}”的搜索结果</span>
                        </div>
                        <span class="search-card-toggle-icon">▶</span>
                    </div>
                    <div class="search-card-body"></div>
                `;
                
                const sHeader = searchCard.querySelector(".search-card-header");
                const sBody = searchCard.querySelector(".search-card-body");
                
                sHeader.onclick = () => {
                    const collapsed = searchCard.classList.toggle("collapsed");
                    const toggleIcon = sHeader.querySelector(".search-card-toggle-icon");
                    if (toggleIcon) toggleIcon.textContent = collapsed ? "▶" : "▼";
                };
                
                if (results.length === 0) {
                    sBody.innerHTML = `<div style="font-size: 11.5px; color: var(--subtext0); padding: 4px;">未找到相关搜索结果。</div>`;
                } else {
                    results.forEach(res => {
                        const item = document.createElement("div");
                        item.className = "search-result-item";
                        const sUrl = safeUrl(res.url);
                        const sTitle = escapeHtml(res.title);
                        const sSnippet = res.snippet ? escapeHtml(res.snippet) : "";
                        item.innerHTML = `
                            <div class="search-result-title">
                                <a href="${sUrl}" target="_blank">${sTitle}</a>
                                <span class="search-result-link-icon">↗</span>
                            </div>
                            <span class="search-result-url">${sUrl}</span>
                            ${sSnippet ? `<div class="search-result-snippet">${sSnippet}</div>` : ""}
                        `;
                        sBody.appendChild(item);
                    });
                    
                    // 展示使用的搜索引擎及第三方额度
                    if (tc.engine && tc.engine !== "none") {
                        const engineInfo = document.createElement("div");
                        engineInfo.className = "search-engine-info";
                        let engineDisplayName = tc.engine;
                        if (tc.engine === "google") engineDisplayName = "Google";
                        else if (tc.engine === "bing") engineDisplayName = "Bing";
                        else if (tc.engine === "duckduckgo") engineDisplayName = "DuckDuckGo";
                        else if (tc.engine === "tavily") engineDisplayName = "Tavily Search API";
                        else if (tc.engine === "jina") engineDisplayName = "Jina Search API";
                        
                        let usageStr = "";
                        if (tc.usage) {
                            const usage = tc.usage;
                            if (usage.remaining_requests !== undefined) {
                                usageStr = ` | 剩余额度: ${usage.remaining_requests} 请求`;
                                if (usage.remaining_tokens !== undefined) {
                                    usageStr += ` / ${usage.remaining_tokens} Token`;
                                }
                            } else if (usage.used !== undefined && usage.limit !== undefined) {
                                usageStr = ` | 本月已用: ${usage.used} / ${usage.limit} 次`;
                            }
                        }
                        
                        engineInfo.innerHTML = `
                            <span>检索工具：</span>
                            <span class="search-engine-badge">${engineDisplayName}</span>
                            <span style="font-size: 11.5px; color: var(--subtext0); margin-left: 6px;">${usageStr}</span>
                        `;
                        sBody.appendChild(engineInfo);
                    }
                }
                
                card.appendChild(searchCard);
                
            } else if (tc.type === "fetch") {
                const fetchCard = document.createElement("div");
                fetchCard.className = "search-card collapsed"; // 沿用 search-card 样式并折叠
                
                let parserName = tc.parser === "jina" ? "Jina Reader API" : "本地内容提取器";
                
                let usageStr = "";
                if (tc.usage) {
                    const usage = tc.usage;
                    if (usage.remaining_requests !== undefined) {
                        usageStr = ` | Jina 剩余: ${usage.remaining_requests} 请求 / ${usage.remaining_tokens} Token`;
                    }
                }
                
                fetchCard.innerHTML = `
                    <div class="search-card-header">
                        <div class="search-status-wrapper">
                            <span class="search-radar-done">📖</span>
                            <span>已成功读取网页内容 (${tc.content_len} 字符)</span>
                        </div>
                        <span class="search-card-toggle-icon">▶</span>
                    </div>
                    <div class="search-card-body"></div>
                `;
                
                const sHeader = fetchCard.querySelector(".search-card-header");
                const sBody = fetchCard.querySelector(".search-card-body");
                
                sHeader.onclick = () => {
                    const collapsed = fetchCard.classList.toggle("collapsed");
                    const toggleIcon = sHeader.querySelector(".search-card-toggle-icon");
                    if (toggleIcon) toggleIcon.textContent = collapsed ? "▶" : "▼";
                };
                
                sBody.innerHTML = `
                    <div class="search-engine-info" style="margin-top: 0px; border-top: none; padding-top: 0px;">
                        <span>读取工具：</span>
                        <span class="search-engine-badge" style="background-color: var(--overlay0);">${parserName}</span>
                        <span style="font-size: 11.5px; color: var(--subtext0); margin-left: 8px;">${usageStr}</span>
                        <div style="font-size: 11px; color: var(--subtext0); word-break: break-all; margin-top: 4px;">URL: <a href="${tc.url}" target="_blank" style="color: var(--blue); text-decoration: underline;">${tc.url}</a></div>
                    </div>
                `;
                
                card.appendChild(fetchCard);
            }
        });
    }
    
    card.appendChild(body);
    row.appendChild(card);
    messageList.appendChild(row);
    
    // 对代码块进行语法高亮并注入复制与保存操作头部
    highlightCodeBlocks(card);
}

// 工具函数：根据语言名获取对应文件后缀
function getExtensionFromLang(lang) {
    const langMap = {
        'javascript': '.js', 'js': '.js',
        'typescript': '.ts', 'ts': '.ts',
        'python': '.py', 'py': '.py',
        'html': '.html',
        'css': '.css',
        'json': '.json',
        'xml': '.xml',
        'yaml': '.yaml', 'yml': '.yaml',
        'markdown': '.md', 'md': '.md',
        'sql': '.sql',
        'rust': '.rs', 'rs': '.rs',
        'c': '.c',
        'cpp': '.cpp', 'c++': '.cpp',
        'go': '.go',
        'shell': '.sh', 'sh': '.sh', 'bash': '.sh',
        'powershell': '.ps1', 'ps1': '.ps1',
        'java': '.java',
        'php': '.php'
    };
    return langMap[lang.toLowerCase()] || '.txt';
}

// 语法高亮代码块并在 pre 上方注入操作头部
function highlightCodeBlocks(container) {
    container.querySelectorAll('pre code').forEach((block) => {
        const pre = block.parentNode;
        
        // 智能高亮优化：如果当前是流输出状态且最后一个代码块还未闭合，先不进行高亮以免频繁重绘
        const isStreamingBlock = isStreaming && (block.closest('#streaming-message-body') !== null);
        if (isStreamingBlock) {
            const backtickCount = (streamingText.match(/```/g) || []).length;
            const isLastBlockOpen = (backtickCount % 2 === 1);
            if (isLastBlockOpen) {
                // 暂不进行渲染，直接返回
                return;
            }
        }
        
        if (!pre.querySelector('.code-header')) {
            let lang = 'code';
            block.classList.forEach(cls => {
                if (cls.startsWith('language-')) {
                    lang = cls.replace('language-', '');
                }
            });
            
            let runBtnHtml = "";
            const normLang = lang.toLowerCase();
            if (normLang === 'python' || normLang === 'javascript' || normLang === 'js') {
                runBtnHtml = `<button class="code-run-btn" style="background-color: var(--green) !important; color: var(--crust) !important; border: none; border-radius: 4px; padding: 2px 8px; font-size: 11.5px; cursor: pointer; font-weight: 600; margin-right: 6px;">▶️ 运行</button>`;
            }

            const header = document.createElement('div');
            header.className = 'code-header';
            header.innerHTML = `
                <span>${lang.toUpperCase()}</span>
                <div class="code-header-actions">
                    ${runBtnHtml}
                    <button class="code-save-btn">保存为文件</button>
                    <button class="code-copy-btn">复制</button>
                </div>
            `;
            
            if (normLang === 'python' || normLang === 'javascript' || normLang === 'js') {
                header.querySelector('.code-run-btn').onclick = () => {
                    runCodeBlock(block.innerText, normLang, pre);
                };
            }

            // 绑定保存为文件按钮事件
            header.querySelector('.code-save-btn').onclick = async () => {
                const content = block.innerText;
                const extension = getExtensionFromLang(lang);
                const suggestName = `code_${Date.now()}${extension}`;
                const saved = await apiBridge.save_code_block(content, suggestName);
                if (saved) {
                    statusLabel.textContent = "💾 文件保存成功";
                    setTimeout(() => { statusLabel.textContent = "就绪"; }, 2000);
                }
            };
            // Add copy action
            header.querySelector('.code-copy-btn').onclick = () => {
                copyText(block.innerText);
            };
            pre.insertBefore(header, block);
            
            // Phase 2 Artifact: If code is html, svg, xml, or mermaid, add a preview button below pre block
            if (normLang === 'html' || normLang === 'svg' || normLang === 'mermaid' || normLang === 'xml') {
                const showBtn = document.createElement('button');
                showBtn.className = 'show-artifact-btn';
                showBtn.innerHTML = '👁️ 预览 Artifact';
                showBtn.onclick = () => {
                    showArtifact(block.innerText, normLang);
                };
                pre.parentNode.insertBefore(showBtn, pre.nextSibling);
            }
        }
        if (typeof hljs !== 'undefined') {
            hljs.highlightElement(block);
        }
    });
}

// 滚动聊天视口至最底部
function scrollChatBottom() {
    scrollAnchor.scrollIntoView({ behavior: "smooth" });
}

// 复制文本工具函数
function copyText(text) {
    navigator.clipboard.writeText(text);
    statusLabel.textContent = "📋 内容已成功复制到剪贴板";
    setTimeout(() => { statusLabel.textContent = "就绪"; }, 2000);
}

// 发送消息核心逻辑
async function sendMessage() {
    if (isStreaming) {
        statusLabel.textContent = "正在停止生成...";
        await apiBridge.abort_generation();
        return;
    }
    if (isSending) return;
    const text = inputBox.value.trim();
    if (!text && attachments.length === 0) return;
    
    let hasKey = false;
    const platform = config.active_platform || "claude";
    if (platform === "claude") {
        hasKey = !!(config.has_api_key || (config.api_key && config.api_key.trim()));
    } else if (platform === "deepseek") {
        hasKey = !!(config.has_deepseek_api_key || (config.deepseek_api_key && config.deepseek_api_key.trim()));
    } else if (platform === "gemini") {
        hasKey = !!(config.has_gemini_api_key || (config.gemini_api_key && config.gemini_api_key.trim()));
    }
    
    if (!hasKey) {
        statusLabel.textContent = "请先配置 API Key！";
        showSettings();
        return;
    }

    // 清空输入框并重置高度
    inputBox.value = "";
    inputBox.style.height = "auto";
    
    // 1. 在聊天面板展示用户发送的消息气泡
    let displayContent = text;
    if (attachments.length > 0) {
        displayContent += "\n[附件: " + attachments.map(a => a.name).join(", ") + "]";
    }
    const userMsgIndex = currentConv ? currentConv.messages.length : -1;
    appendMessage("user", displayContent, "", false, userMsgIndex);
    scrollChatBottom();

    // 2. 在聊天面板生成一个空的 Assistant 占位气泡准备流式打字机输入
    appendMessage("assistant", "思考中...", "", true);
    
    isStreaming = true;
    streamingText = "";
    streamingThinking = "";
    
    // 激活“停止生成”按钮的视觉样式
    sendBtn.classList.add("stop-active");
    sendBtn.title = "停止生成";
    const sendIcon = sendBtn.querySelector(".send-icon");
    if (sendIcon) sendIcon.textContent = "■";

    // 3. 消息发送后，清除本地已选择的附件列表
    const oldAttachments = [...attachments];
    attachments = [];
    renderAttachments();

    let statusText = "AI 思考中...";
    if (platform === "claude") {
        statusText = "Claude 思考中...";
    } else if (platform === "deepseek") {
        statusText = "DeepSeek 思考中...";
        if (oldAttachments.length > 0) {
            statusText = "DeepSeek (正在进行本地解析/OCR) 思考中...";
        }
    } else if (platform === "gemini") {
        statusText = "Gemini 思考中...";
    }
    statusLabel.textContent = statusText;

    // 4. 调用 API 发起生成请求
    isSending = true;
    try {
        await apiBridge.send_message(currentConvId, text, oldAttachments);
    } finally {
        isSending = false;
    }
}

// Trigger sends on clicks and enter
sendBtn.onclick = sendMessage;
inputBox.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.ctrlKey) {
        e.preventDefault();
        sendMessage();
    }
});

// 由后台 Python 线程实时评估调用的流式输出回调函数
window.onStreamMessage = async (type, data) => {
    const body = document.getElementById("streaming-message-body");
    const thinkContainer = document.getElementById("streaming-thinking-container");
    
    if (type === "text") {
        streamingText += data;
        if (body) {
            if (body.textContent === "思考中...") {
                body.textContent = "";
            }
            body.innerHTML = parseMarkdown(streamingText);
            highlightCodeBlocks(body.parentNode);
        }
        scrollChatBottom();
        
    } else if (type === "search_start") {
        const card = document.querySelector("#streaming-msg-row .message-card");
        if (card) {
            const oldSearchCard = document.querySelector(".search-card[id^='streaming-search-card-']");
            if (oldSearchCard) oldSearchCard.remove();
            
            const searchCard = document.createElement("div");
            searchCard.id = `streaming-search-card-${Date.now()}`;
            searchCard.className = "search-card";
            const queryText = data.query ? `：${data.query}` : "";
            searchCard.innerHTML = `
                <div class="search-card-header">
                    <div class="search-status-wrapper">
                        <span class="search-radar"></span>
                        <span>正在联网搜索${queryText}...</span>
                    </div>
                </div>
                <div class="search-card-body"></div>
            `;
            
            const bodyElement = document.getElementById("streaming-message-body");
            if (bodyElement) {
                card.insertBefore(searchCard, bodyElement);
            } else {
                card.appendChild(searchCard);
            }
            scrollChatBottom();
        }
        
    } else if (type === "search_done") {
        const searchCard = document.querySelector(".search-card[id^='streaming-search-card-']");
        if (searchCard) {
            searchCard.removeAttribute("id");
            
            const results = data.results || [];
            const header = searchCard.querySelector(".search-card-header");
            const sBody = searchCard.querySelector(".search-card-body");
            
            if (header) {
                let statusText = `已找到 ${results.length} 个关于“${escapeHtml(data.query)}”的搜索结果`;
                if (data.engine === "claude") {
                    statusText = `已完成关于“${escapeHtml(data.query)}”的联网检索`;
                }
                header.innerHTML = `
                    <div class="search-status-wrapper">
                        <span class="search-radar-done">🌐</span>
                        <span>${statusText}</span>
                    </div>
                    <span class="search-card-toggle-icon">▼</span>
                `;
                
                header.onclick = () => {
                    const collapsed = searchCard.classList.toggle("collapsed");
                    const toggleIcon = header.querySelector(".search-card-toggle-icon");
                    if (toggleIcon) toggleIcon.textContent = collapsed ? "▶" : "▼";
                };
            }
            
            if (sBody) {
                sBody.innerHTML = "";
                if (results.length === 0 && data.engine !== "claude") {
                    sBody.innerHTML = `<div style="font-size: 11.5px; color: var(--subtext0); padding: 4px;">未找到相关搜索结果。</div>`;
                } else if (results.length > 0) {
                    results.forEach(res => {
                        const item = document.createElement("div");
                        item.className = "search-result-item";
                        const sUrl = safeUrl(res.url);
                        const sTitle = escapeHtml(res.title);
                        const sSnippet = res.snippet ? escapeHtml(res.snippet) : "";
                        item.innerHTML = `
                            <div class="search-result-title">
                                <a href="${sUrl}" target="_blank">${sTitle}</a>
                                <span class="search-result-link-icon">↗</span>
                            </div>
                            <span class="search-result-url">${sUrl}</span>
                            ${sSnippet ? `<div class="search-result-snippet">${sSnippet}</div>` : ""}
                        `;
                        sBody.appendChild(item);
                    });
                }
                
                // 展示使用的搜索引擎及第三方额度
                const engine = data.engine || "";
                if (engine && engine !== "none") {
                    const engineInfo = document.createElement("div");
                    engineInfo.className = "search-engine-info";
                    let engineDisplayName = engine;
                    if (engine === "google") engineDisplayName = "Google";
                    else if (engine === "bing") engineDisplayName = "Bing";
                    else if (engine === "duckduckgo") engineDisplayName = "DuckDuckGo";
                    else if (engine === "tavily") engineDisplayName = "Tavily Search API";
                    else if (engine === "jina") engineDisplayName = "Jina Search API";
                    else if (engine === "claude") engineDisplayName = "Claude 官方原生搜索";
                    
                    let usageStr = "";
                    if (data.usage) {
                        const usage = data.usage;
                        if (usage.remaining_requests !== undefined) {
                            usageStr = ` | 剩余额度: ${usage.remaining_requests} 请求`;
                            if (usage.remaining_tokens !== undefined) {
                                usageStr += ` / ${usage.remaining_tokens} Token`;
                            }
                        } else if (usage.used !== undefined && usage.limit !== undefined) {
                            usageStr = ` | 本月已用: ${usage.used} / ${usage.limit} 次`;
                        }
                    }
                    
                    engineInfo.innerHTML = `
                        <span>检索工具：</span>
                        <span class="search-engine-badge">${engineDisplayName}</span>
                        <span style="font-size: 11.5px; color: var(--subtext0); margin-left: 6px;">${usageStr}</span>
                    `;
                    sBody.appendChild(engineInfo);
                }
            }
            scrollChatBottom();
        }
        
    } else if (type === "fetch_start") {
        const card = document.querySelector("#streaming-msg-row .message-card");
        if (card) {
            const oldFetchCard = document.getElementById("streaming-fetch-card");
            if (oldFetchCard) oldFetchCard.remove();
            
            const fetchCard = document.createElement("div");
            fetchCard.className = "search-card"; // 沿用 search-card 样式
            fetchCard.id = "streaming-fetch-card";
            fetchCard.innerHTML = `
                <div class="search-card-header">
                    <div class="search-status-wrapper">
                        <span class="search-radar"></span>
                        <span>正在深度读取网页内容：${data.url}...</span>
                    </div>
                </div>
                <div class="search-card-body"></div>
            `;
            
            const bodyElement = document.getElementById("streaming-message-body");
            if (bodyElement) {
                card.insertBefore(fetchCard, bodyElement);
            } else {
                card.appendChild(fetchCard);
            }
            scrollChatBottom();
        }
        
    } else if (type === "fetch_done") {
        const fetchCard = document.getElementById("streaming-fetch-card");
        if (fetchCard) {
            fetchCard.removeAttribute("id");
            
            const header = fetchCard.querySelector(".search-card-header");
            const fBody = fetchCard.querySelector(".search-card-body");
            
            const parser = data.parser || "local";
            let parserName = parser === "jina" ? "Jina Reader API" : "本地内容提取器";
            
            let usageStr = "";
            if (data.usage) {
                const usage = data.usage;
                if (usage.remaining_requests !== undefined) {
                    usageStr = ` | Jina 剩余: ${usage.remaining_requests} 请求 / ${usage.remaining_tokens} Token`;
                }
            }
            
            if (header) {
                header.innerHTML = `
                    <div class="search-status-wrapper">
                        <span class="search-radar-done">📖</span>
                        <span>已成功读取网页内容 (${data.content_len} 字符)</span>
                    </div>
                    <span class="search-card-toggle-icon">▼</span>
                `;
                
                header.onclick = () => {
                    const collapsed = fetchCard.classList.toggle("collapsed");
                    const toggleIcon = header.querySelector(".search-card-toggle-icon");
                    if (toggleIcon) toggleIcon.textContent = collapsed ? "▶" : "▼";
                };
            }
            
            if (fBody) {
                const sUrl = safeUrl(data.url);
                const escapedUrl = escapeHtml(data.url);
                fBody.innerHTML = `
                    <div class="search-engine-info" style="margin-top: 0px; border-top: none; padding-top: 0px;">
                        <span>读取工具：</span>
                        <span class="search-engine-badge" style="background-color: var(--overlay0);">${parserName}</span>
                        <span style="font-size: 11.5px; color: var(--subtext0); margin-left: 8px;">${usageStr}</span>
                        <div style="font-size: 11px; color: var(--subtext0); word-break: break-all; margin-top: 4px;">URL: <a href="${sUrl}" target="_blank" style="color: var(--blue); text-decoration: underline;">${escapedUrl}</a></div>
                    </div>
                `;
            }
            scrollChatBottom();
        }
        
    } else if (type === "thinking") {
        streamingThinking += data;
        if (thinkContainer) {
            thinkContainer.classList.remove("hidden");
            const box = thinkContainer.querySelector(".thinking-box");
            box.textContent = streamingThinking;
            
            const toggle = thinkContainer.querySelector(".thinking-toggle");
            toggle.textContent = `▶ 思考过程 (~${Math.floor(streamingThinking.length / 2)} tokens)`;
        }
        scrollChatBottom();
        
    } else if (type === "done") {
        // 结束流式输出，还原发送按钮状态
        
        // 还原发送图标为原本的箭头样式
        sendBtn.classList.remove("stop-active");
        sendBtn.title = "发送 (Ctrl+Enter)";
        const sendIcon = sendBtn.querySelector(".send-icon");
        if (sendIcon) sendIcon.textContent = "↑";
        
        statusLabel.textContent = "就绪";
        
        // 移除临时流式 ID 标识以固定内容
        const row = document.getElementById("streaming-msg-row");
        if (row) row.removeAttribute("id");
        if (body) body.removeAttribute("id");
        if (thinkContainer) thinkContainer.removeAttribute("id");
        
        // 更新显示的 Token 消耗量统计
        tokenLabel.textContent = `Token: ${data.input_tokens} in / ${data.output_tokens} out`;
        
        // 重新加载列表以刷新会话卡片标题
        await loadConversations();
        await reloadCurrentConversation();
        
        isStreaming = false;
        
    } else if (type === "aborted") {
        
        // Restore send button state
        sendBtn.classList.remove("stop-active");
        sendBtn.title = "发送 (Ctrl+Enter)";
        const sendIcon = sendBtn.querySelector(".send-icon");
        if (sendIcon) sendIcon.textContent = "↑";
        
        statusLabel.textContent = "已中止生成";
        setTimeout(() => { if (statusLabel.textContent === "已中止生成") statusLabel.textContent = "就绪"; }, 2000);
        
        const row = document.getElementById("streaming-msg-row");
        if (row) row.removeAttribute("id");
        
        if (body) {
            body.removeAttribute("id");
            body.innerHTML += `<div class="aborted-badge" style="color: var(--peach); font-size: 11px; margin-top: 8px; font-style: italic; display: flex; align-items: center; gap: 4px;">🚫 已中止</div>`;
        }
        
        const thinkContainer = document.getElementById("streaming-thinking-container");
        if (thinkContainer) thinkContainer.removeAttribute("id");
        
        await loadConversations();
        await reloadCurrentConversation();
        
        isStreaming = false;
        
    } else if (type === "error") {
        isStreaming = false;
        
        // Restore send button state
        sendBtn.classList.remove("stop-active");
        sendBtn.title = "发送 (Ctrl+Enter)";
        const sendIcon = sendBtn.querySelector(".send-icon");
        if (sendIcon) sendIcon.textContent = "↑";
        
        const errText = typeof data === 'object' && data !== null ? (data.text || JSON.stringify(data)) : data;
        statusLabel.textContent = `错误: ${errText}`;
        
        if (body) {
            body.innerHTML = `<span style="color: var(--red);">❌ 发生错误: ${escapeHtml(errText)}</span>`;
        }
    }
};

// 打开附件选择对话框
attachBtn.onclick = async () => {
    const selected = await apiBridge.select_attachments();
    if (selected && selected.length > 0) {
        attachments = [...attachments, ...selected];
        renderAttachments();
    }
};

function renderAttachments() {
    if (attachments.length === 0) {
        attachmentsArea.classList.add("hidden");
        return;
    }
    
    attachmentsArea.classList.remove("hidden");
    attachmentsArea.innerHTML = "";
    attachments.forEach((att, index) => {
        const badge = document.createElement("div");
        badge.className = "attachment-badge";
        badge.innerHTML = `
            <span>📎 ${att.name} (${Math.round(att.size / 1024)} KB)</span>
            <button class="remove-att-btn">&times;</button>
        `;
        badge.querySelector(".remove-att-btn").onclick = () => {
            attachments.splice(index, 1);
            renderAttachments();
        };
        attachmentsArea.appendChild(badge);
    });
}

// 监听输入，根据文本内容自动拉伸输入框高度
inputBox.addEventListener("input", () => {
    inputBox.style.height = "auto";
    inputBox.style.height = `${inputBox.scrollHeight}px`;
});

// 弹窗显示/隐藏控制逻辑
function showModal(modal) {
    modal.classList.remove("hidden");
}

function hideModal(modal) {
    modal.classList.add("hidden");
}

document.querySelectorAll(".close-modal-btn, .cancel-modal-btn").forEach(btn => {
    btn.onclick = (e) => {
        const modal = e.target.closest(".modal-overlay");
        hideModal(modal);
    };
});

// 移动端侧边栏切换与遮罩层点击逻辑
const sidebarToggleBtn = document.getElementById("sidebar-toggle-btn");
const sidebarOverlay = document.getElementById("sidebar-overlay");
const appContainerElement = document.querySelector(".app-container");

if (sidebarToggleBtn && appContainerElement) {
    sidebarToggleBtn.onclick = (e) => {
        e.stopPropagation();
        appContainerElement.classList.toggle("sidebar-open");
    };
}

if (sidebarOverlay && appContainerElement) {
    sidebarOverlay.onclick = () => {
        appContainerElement.classList.remove("sidebar-open");
    };
}

// 配置对话框模块
settingsBtn.onclick = showSettings;


function updateOcrNotices(platform) {
    const multiNotice = document.getElementById("ocr-notice-multimodal");
    const textNotice = document.getElementById("ocr-notice-textonly");
    if (!multiNotice || !textNotice) return;
    
    if (platform === "claude" || platform === "gemini") {
        multiNotice.style.display = "block";
        textNotice.style.display = "none";
    } else {
        multiNotice.style.display = "none";
        textNotice.style.display = "block";
    }
}

function showSettings() {
    clearApiKeyPending = false; // 重置清除标记
    clearDeepseekKeyPending = false;
    clearGeminiKeyPending = false;
    
    // 渲染 Claude API Key 已绑定或未绑定状态的面板
    if (config.has_api_key) {
        apiKeyInputContainer.classList.add("hidden");
        apiKeyStatusContainer.classList.remove("hidden");
        apiKeyInput.value = "";
    } else {
        apiKeyInputContainer.classList.remove("hidden");
        apiKeyStatusContainer.classList.add("hidden");
        apiKeyInput.value = "";
    }
    
    // 渲染 DeepSeek API Key 已绑定或未绑定状态的面板
    if (config.has_deepseek_api_key) {
        if (deepseekApiKeyInputContainer) deepseekApiKeyInputContainer.classList.add("hidden");
        if (deepseekApiKeyStatusContainer) deepseekApiKeyStatusContainer.classList.remove("hidden");
        if (deepseekApiKeyInput) deepseekApiKeyInput.value = "";
    } else {
        if (deepseekApiKeyInputContainer) deepseekApiKeyInputContainer.classList.remove("hidden");
        if (deepseekApiKeyStatusContainer) deepseekApiKeyStatusContainer.classList.add("hidden");
        if (deepseekApiKeyInput) deepseekApiKeyInput.value = "";
    }
    if (deepseekApiUrlInput) {
        deepseekApiUrlInput.value = config.deepseek_api_url || "https://api.deepseek.com";
    }

    // 渲染 Gemini API Key 已绑定或未绑定状态的面板
    if (config.has_gemini_api_key) {
        if (geminiApiKeyInputContainer) geminiApiKeyInputContainer.classList.add("hidden");
        if (geminiApiKeyStatusContainer) geminiApiKeyStatusContainer.classList.remove("hidden");
        if (geminiApiKeyInput) geminiApiKeyInput.value = "";
    } else {
        if (geminiApiKeyInputContainer) geminiApiKeyInputContainer.classList.remove("hidden");
        if (geminiApiKeyStatusContainer) geminiApiKeyStatusContainer.classList.add("hidden");
        if (geminiApiKeyInput) geminiApiKeyInput.value = "";
    }
    if (geminiApiUrlInput) {
        geminiApiUrlInput.value = config.gemini_api_url || "";
    }

    // 默认展示当前 active 平台的 Tab 和面板
    // 默认展示当前 active 平台的 Tab 和面板
    const currentPlatform = config.active_platform || "claude";
    updateOcrNotices(currentPlatform);
    const tabButtons = document.querySelectorAll(".platform-tabs .tab-btn");
    const tabPanels = document.querySelectorAll(".platform-panel");
    tabButtons.forEach(btn => {
        if (btn.getAttribute("data-platform-tab") === currentPlatform) {
            btn.classList.add("active");
            btn.style.background = "var(--surface0)";
            btn.style.color = "var(--text)";
        } else {
            btn.classList.remove("active");
            btn.style.background = "transparent";
            btn.style.color = "var(--subtext0)";
        }
    });
    tabPanels.forEach(panel => {
        if (panel.id === `platform-panel-${currentPlatform}`) {
            panel.classList.remove("hidden");
        } else {
            panel.classList.add("hidden");
        }
    });

    // 初始化 OCR 配置项
    if (ocrModeSelect) {
        ocrModeSelect.value = config.ocr_mode || "auto";
    }
    if (ocrCloudModelSelect) {
        ocrCloudModelSelect.value = config.ocr_cloud_model || "gemini";
    }

    // 异步查询并更新本地解析器/OCR 依赖状态徽章
    if (apiBridge.check_parsers) {
        apiBridge.check_parsers().then(status => {
            if (!status) return;
            const updateBadge = (id, name, installed) => {
                const b = document.getElementById(id);
                if (!b) return;
                b.textContent = `${name}: ${installed ? '已安装' : '未安装'}`;
                b.style.background = installed ? 'rgba(166, 227, 161, 0.15)' : 'rgba(243, 139, 168, 0.15)';
                b.style.color = installed ? 'var(--green, #a6e3a1)' : 'var(--red, #f38ba8)';
                b.style.border = `1px solid ${installed ? 'rgba(166, 227, 161, 0.3)' : 'rgba(243, 139, 168, 0.3)'}`;
            };
            updateBadge("badge-docx", "python-docx", status.docx);
            updateBadge("badge-openpyxl", "openpyxl", status.openpyxl);
            updateBadge("badge-pptx", "python-pptx", status.pptx);
            updateBadge("badge-pypdf", "pypdf", status.pypdf);
            updateBadge("badge-easyocr", "easyocr", status.easyocr);
        }).catch(err => console.error("Error checking parsers status:", err));
    }
    
    tempSlider.value = config.temperature;
    tempLabelTitle.textContent = `Temperature: ${parseFloat(config.temperature).toFixed(2)}`;
    maxTokensInput.value = config.max_tokens;
    
    const mode = config.thinking_enabled ? config.thinking_type : "disabled";
    document.querySelectorAll("input[name='thinking-mode']").forEach(radio => {
        radio.checked = (radio.value === mode);
    });
    
    budgetTokensInput.value = config.thinking_budget;
    if (thinkingLevelSelect) {
        thinkingLevelSelect.value = config.thinking_level || "high";
    }
    if (autoRunCodeInput) {
        autoRunCodeInput.checked = !!config.auto_run_code;
        if (enableCodeSandboxInput) enableCodeSandboxInput.checked = !!config.enable_code_sandbox;
    }
    if (fontModeSelect) {
        fontModeSelect.value = config.font_mode || "custom";
    }
    if (enableServerInput) {
        enableServerInput.checked = config.enable_server !== false;
    }
    if (syncConfigInput) {
        syncConfigInput.checked = config.sync_config_to_web !== false;
    }
    if (onlyServerInput) {
        onlyServerInput.checked = !!config.only_server;
    }
    if (enableSslInput) {
        enableSslInput.checked = !!config.enable_ssl;
    }
    if (serverPortInput) {
        serverPortInput.value = config.server_port || 8000;
    }
    
    initialEnableServer = config.enable_server !== false;
    initialServerPort = config.server_port || 8000;
    initialEnableSsl = !!config.enable_ssl;
    if (serverTokenInput) {
        serverTokenInput.value = config.security_token || "";
    }
    clearTavilyKeyPending = false;
    clearJinaKeyPending = false;

    if (searchEngineSelect) {
        searchEngineSelect.value = config.web_search_engine || "google";
    }
    const enableSearchInput = document.getElementById("enable-search-input");
    if (enableSearchInput) {
        enableSearchInput.checked = !!config.enable_web_search;
    }
    const enableFetchInput = document.getElementById("enable-fetch-input");
    if (enableFetchInput) {
        enableFetchInput.checked = config.enable_web_fetch !== false; // 默认开启
    }
    const webFetchLimitInput = document.getElementById("web-fetch-limit-input");
    if (webFetchLimitInput) {
        webFetchLimitInput.value = config.web_fetch_limit || 15000;
    }
    if (webPageParserSelect) {
        webPageParserSelect.value = config.web_page_parser || "local";
    }
    
    // 初始化 Tavily Key 状态与输入显示
    if (config.has_tavily_api_key) {
        if (tavilyKeyInputContainer) tavilyKeyInputContainer.classList.add("hidden");
        if (tavilyKeyStatusContainer) tavilyKeyStatusContainer.classList.remove("hidden");
        if (tavilyKeyInput) tavilyKeyInput.value = "";
    } else {
        if (tavilyKeyInputContainer) tavilyKeyInputContainer.classList.remove("hidden");
        if (tavilyKeyStatusContainer) tavilyKeyStatusContainer.classList.add("hidden");
        if (tavilyKeyInput) tavilyKeyInput.value = "";
    }

    // 初始化 Jina Key 状态与输入显示
    if (config.has_jina_api_key) {
        if (jinaKeyInputContainer) jinaKeyInputContainer.classList.add("hidden");
        if (jinaKeyStatusContainer) jinaKeyStatusContainer.classList.remove("hidden");
        if (jinaKeyInput) jinaKeyInput.value = "";
    } else {
        if (jinaKeyInputContainer) jinaKeyInputContainer.classList.remove("hidden");
        if (jinaKeyStatusContainer) jinaKeyStatusContainer.classList.add("hidden");
        if (jinaKeyInput) jinaKeyInput.value = "";
    }

    toggleSearchKeyGroups(config.web_search_engine || "google", config.web_page_parser || "local");
    
    // Update settings UI components dynamically based on model capabilities
    updateThinkingSettingsUI();
    
    // Render custom system prompts presets inside Settings dialog
    renderPresetsList();
    
    showModal(settingsModal);
}

// 监听 Extended Thinking 推理模式切换以动态控制 UI 显隐
document.querySelectorAll("input[name='thinking-mode']").forEach(radio => {
    radio.onchange = (e) => {
        updateThinkingSettingsUI();
    };
});

function toggleSearchKeyGroups(engine, parser) {
    if (tavilyKeyGroup) {
        tavilyKeyGroup.style.display = (engine === "tavily") ? "block" : "none";
    }
    if (jinaKeyGroup) {
        jinaKeyGroup.style.display = (engine === "jina" || parser === "jina") ? "block" : "none";
    }
}

if (searchEngineSelect) {
    searchEngineSelect.onchange = () => {
        toggleSearchKeyGroups(searchEngineSelect.value, webPageParserSelect ? webPageParserSelect.value : "local");
    };
}
if (webPageParserSelect) {
    webPageParserSelect.onchange = () => {
        toggleSearchKeyGroups(searchEngineSelect ? searchEngineSelect.value : "google", webPageParserSelect.value);
    };
}

function updateThinkingSettingsUI() {
    const selectedModelId = modelSelect.value || config.model;
    const modelObj = availableModels.find(m => (typeof m === 'object' && m.id === selectedModelId));
    
    const caps = modelObj || {
        thinking_supported: false,
        adaptive_supported: false,
        enabled_supported: false,
        effort_levels: []
    };
    
    const thinkingSection = document.querySelector("input[name='thinking-mode']").closest(".form-group");
    if (!caps.thinking_supported) {
        thinkingSection.classList.add("hidden");
        budgetGroup.classList.add("hidden");
        thinkingLevelGroup.classList.add("hidden");
        return;
    }
    
    thinkingSection.classList.remove("hidden");
    
    const adaptiveRadio = document.querySelector("input[name='thinking-mode'][value='adaptive']");
    const enabledRadio = document.querySelector("input[name='thinking-mode'][value='enabled']");
    
    if (adaptiveRadio) {
        adaptiveRadio.disabled = !caps.adaptive_supported;
        adaptiveRadio.closest(".radio-label").style.opacity = caps.adaptive_supported ? "1" : "0.5";
    }
    if (enabledRadio) {
        enabledRadio.disabled = !caps.enabled_supported;
        enabledRadio.closest(".radio-label").style.opacity = caps.enabled_supported ? "1" : "0.5";
    }
    
    let checkedRadio = document.querySelector("input[name='thinking-mode']:checked");
    if (checkedRadio && checkedRadio.disabled) {
        document.querySelector("input[name='thinking-mode'][value='disabled']").checked = true;
        checkedRadio = document.querySelector("input[name='thinking-mode'][value='disabled']");
    }
    
    const mode = checkedRadio ? checkedRadio.value : "disabled";
    
    if (mode === "disabled") {
        budgetGroup.classList.add("hidden");
        thinkingLevelGroup.classList.add("hidden");
    } else if (mode === "adaptive") {
        budgetGroup.classList.add("hidden");
        if (caps.effort_levels && caps.effort_levels.length > 0) {
            thinkingLevelGroup.classList.remove("hidden");
            populateThinkingLevels(caps.effort_levels);
        } else {
            thinkingLevelGroup.classList.add("hidden");
        }
    } else if (mode === "enabled") {
        budgetGroup.classList.remove("hidden");
        thinkingLevelGroup.classList.add("hidden");
    }
}

function populateThinkingLevels(levels) {
    const levelLabels = {
        "low": "Low (低 - 快速且经济)",
        "medium": "Medium (中 - 平衡)",
        "high": "High (高 - 默认推荐)",
        "xhigh": "X-High (极高)",
        "max": "Max (最大级 - 最深思考)"
    };
    
    const currentVal = thinkingLevelSelect.value || config.thinking_level || "high";
    thinkingLevelSelect.innerHTML = "";
    
    levels.forEach(lvl => {
        const option = document.createElement("option");
        option.value = lvl;
        option.textContent = levelLabels[lvl] || lvl.toUpperCase();
        if (lvl === currentVal) option.selected = true;
        thinkingLevelSelect.appendChild(option);
    });
    
    if (!levels.includes(currentVal)) {
        if (levels.includes("high")) {
            thinkingLevelSelect.value = "high";
        } else if (levels.length > 0) {
            thinkingLevelSelect.value = levels[levels.length - 1];
        }
    }
}

tempSlider.addEventListener("input", (e) => {
    tempLabelTitle.textContent = `Temperature: ${parseFloat(e.target.value).toFixed(2)}`;
});

toggleKeyVisibility.onclick = () => {
    if (apiKeyInput.type === "password") {
        apiKeyInput.type = "text";
        toggleKeyVisibility.textContent = "🔒";
    } else {
        apiKeyInput.type = "password";
        toggleKeyVisibility.textContent = "👁";
    }
};

if (changeKeyBtn) {
    changeKeyBtn.onclick = () => {
        apiKeyInputContainer.classList.remove("hidden");
        apiKeyStatusContainer.classList.add("hidden");
    };
}

if (disconnectKeyBtn) {
    disconnectKeyBtn.onclick = () => {
        clearApiKeyPending = true;
        apiKeyInputContainer.classList.remove("hidden");
        apiKeyStatusContainer.classList.add("hidden");
        apiKeyInput.value = "";
    };
}

// DeepSeek Key 控件事件绑定
if (toggleDeepseekKeyVisibility && deepseekApiKeyInput) {
    toggleDeepseekKeyVisibility.onclick = () => {
        if (deepseekApiKeyInput.type === "password") {
            deepseekApiKeyInput.type = "text";
            toggleDeepseekKeyVisibility.textContent = "🔒";
        } else {
            deepseekApiKeyInput.type = "password";
            toggleDeepseekKeyVisibility.textContent = "👁";
        }
    };
}
if (changeDeepseekKeyBtn) {
    changeDeepseekKeyBtn.onclick = () => {
        if (deepseekApiKeyInputContainer) deepseekApiKeyInputContainer.classList.remove("hidden");
        if (deepseekApiKeyStatusContainer) deepseekApiKeyStatusContainer.classList.add("hidden");
    };
}
if (disconnectDeepseekKeyBtn) {
    disconnectDeepseekKeyBtn.onclick = () => {
        clearDeepseekKeyPending = true;
        if (deepseekApiKeyInputContainer) deepseekApiKeyInputContainer.classList.remove("hidden");
        if (deepseekApiKeyStatusContainer) deepseekApiKeyStatusContainer.classList.add("hidden");
        if (deepseekApiKeyInput) deepseekApiKeyInput.value = "";
    };
}

// Gemini Key 控件事件绑定
if (toggleGeminiKeyVisibility && geminiApiKeyInput) {
    toggleGeminiKeyVisibility.onclick = () => {
        if (geminiApiKeyInput.type === "password") {
            geminiApiKeyInput.type = "text";
            toggleGeminiKeyVisibility.textContent = "🔒";
        } else {
            geminiApiKeyInput.type = "password";
            toggleGeminiKeyVisibility.textContent = "👁";
        }
    };
}
if (changeGeminiKeyBtn) {
    changeGeminiKeyBtn.onclick = () => {
        if (geminiApiKeyInputContainer) geminiApiKeyInputContainer.classList.remove("hidden");
        if (geminiApiKeyStatusContainer) geminiApiKeyStatusContainer.classList.add("hidden");
    };
}
if (disconnectGeminiKeyBtn) {
    disconnectGeminiKeyBtn.onclick = () => {
        clearGeminiKeyPending = true;
        if (geminiApiKeyInputContainer) geminiApiKeyInputContainer.classList.remove("hidden");
        if (geminiApiKeyStatusContainer) geminiApiKeyStatusContainer.classList.add("hidden");
        if (geminiApiKeyInput) geminiApiKeyInput.value = "";
    };
}

// 绑定设置弹窗内部的平台标签卡切换逻辑
const settingsTabButtons = document.querySelectorAll(".platform-tabs .tab-btn");
const settingsTabPanels = document.querySelectorAll(".platform-panel");

settingsTabButtons.forEach(btn => {
    btn.onclick = () => {
        settingsTabButtons.forEach(b => {
            b.classList.remove("active");
            b.style.background = "transparent";
            b.style.color = "var(--subtext0)";
        });
        btn.classList.add("active");
        btn.style.background = "var(--surface0)";
        btn.style.color = "var(--text)";
        
        const platform = btn.getAttribute("data-platform-tab");
        if (typeof updateOcrNotices === "function") updateOcrNotices(platform);
        settingsTabPanels.forEach(p => {
            if (p.id === `platform-panel-${platform}`) {
                p.classList.remove("hidden");
            } else {
                p.classList.add("hidden");
            }
        });
    };
});

if (toggleTokenVisibility && serverTokenInput) {
    toggleTokenVisibility.onclick = () => {
        if (serverTokenInput.type === "password") {
            serverTokenInput.type = "text";
            toggleTokenVisibility.textContent = "🔒";
        } else {
            serverTokenInput.type = "password";
            toggleTokenVisibility.textContent = "👁";
        }
    };
}

// Tavily Key 控件事件绑定
if (toggleTavilyVisibility && tavilyKeyInput) {
    toggleTavilyVisibility.onclick = () => {
        if (tavilyKeyInput.type === "password") {
            tavilyKeyInput.type = "text";
            toggleTavilyVisibility.textContent = "🔒";
        } else {
            tavilyKeyInput.type = "password";
            toggleTavilyVisibility.textContent = "👁";
        }
    };
}
if (changeTavilyBtn) {
    changeTavilyBtn.onclick = () => {
        if (tavilyKeyInputContainer) tavilyKeyInputContainer.classList.remove("hidden");
        if (tavilyKeyStatusContainer) tavilyKeyStatusContainer.classList.add("hidden");
    };
}
if (disconnectTavilyBtn) {
    disconnectTavilyBtn.onclick = () => {
        clearTavilyKeyPending = true;
        if (tavilyKeyInputContainer) tavilyKeyInputContainer.classList.remove("hidden");
        if (tavilyKeyStatusContainer) tavilyKeyStatusContainer.classList.add("hidden");
        if (tavilyKeyInput) tavilyKeyInput.value = "";
    };
}

// Jina Key 控件事件绑定
if (toggleJinaVisibility && jinaKeyInput) {
    toggleJinaVisibility.onclick = () => {
        if (jinaKeyInput.type === "password") {
            jinaKeyInput.type = "text";
            toggleJinaVisibility.textContent = "🔒";
        } else {
            jinaKeyInput.type = "password";
            toggleJinaVisibility.textContent = "👁";
        }
    };
}
if (changeJinaBtn) {
    changeJinaBtn.onclick = () => {
        if (jinaKeyInputContainer) jinaKeyInputContainer.classList.remove("hidden");
        if (jinaKeyStatusContainer) jinaKeyStatusContainer.classList.add("hidden");
    };
}
if (disconnectJinaBtn) {
    disconnectJinaBtn.onclick = () => {
        clearJinaKeyPending = true;
        if (jinaKeyInputContainer) jinaKeyInputContainer.classList.remove("hidden");
        if (jinaKeyStatusContainer) jinaKeyStatusContainer.classList.add("hidden");
        if (jinaKeyInput) jinaKeyInput.value = "";
    };
}

saveSettingsBtn.onclick = async () => {
    // 1. 处理 API Key 封包保存与清除逻辑 (写唯一)
    if (clearApiKeyPending) {
        config.clear_api_key = true;
        config.api_key = "";
        clearApiKeyPending = false; // 重置标记
    } else {
        delete config.clear_api_key;
        const newKey = apiKeyInput.value.trim();
        if (newKey) {
            config.api_key = newKey;
        } else {
            // 如果输入框为空且没有清除标记，不传送该字段，由后端保持原状
            delete config.api_key;
        }
    }
    
    // Tavily API Key 保存与清除
    if (clearTavilyKeyPending) {
        config.clear_tavily_api_key = true;
        config.tavily_api_key = "";
        clearTavilyKeyPending = false;
    } else {
        delete config.clear_tavily_api_key;
        const newKey = tavilyKeyInput ? tavilyKeyInput.value.trim() : "";
        if (newKey) {
            config.tavily_api_key = newKey;
        } else {
            delete config.tavily_api_key;
        }
    }

    // Jina API Key 保存与清除
    if (clearJinaKeyPending) {
        config.clear_jina_api_key = true;
        config.jina_api_key = "";
        clearJinaKeyPending = false;
    } else {
        delete config.clear_jina_api_key;
        const newKey = jinaKeyInput ? jinaKeyInput.value.trim() : "";
        if (newKey) {
            config.jina_api_key = newKey;
        } else {
            delete config.jina_api_key;
        }
    }

    // DeepSeek API Key 保存与清除
    if (clearDeepseekKeyPending) {
        config.clear_deepseek_api_key = true;
        config.deepseek_api_key = "";
        clearDeepseekKeyPending = false;
    } else {
        delete config.clear_deepseek_api_key;
        const newKey = deepseekApiKeyInput ? deepseekApiKeyInput.value.trim() : "";
        if (newKey) {
            config.deepseek_api_key = newKey;
        } else {
            delete config.deepseek_api_key;
        }
    }
    if (deepseekApiUrlInput) {
        config.deepseek_api_url = deepseekApiUrlInput.value.trim() || "https://api.deepseek.com";
    }

    // Gemini API Key 保存与清除
    if (clearGeminiKeyPending) {
        config.clear_gemini_api_key = true;
        config.gemini_api_key = "";
        clearGeminiKeyPending = false;
    } else {
        delete config.clear_gemini_api_key;
        const newKey = geminiApiKeyInput ? geminiApiKeyInput.value.trim() : "";
        if (newKey) {
            config.gemini_api_key = newKey;
        } else {
            delete config.gemini_api_key;
        }
    }
    if (geminiApiUrlInput) {
        config.gemini_api_url = geminiApiUrlInput.value.trim() || "";
    }

    // OCR 识别模式保存
    if (ocrModeSelect) {
        config.ocr_mode = ocrModeSelect.value || "auto";
    }
    if (ocrCloudModelSelect) {
        config.ocr_cloud_model = ocrCloudModelSelect.value || "gemini";
    }
    
    // 2. 保存 Token 验证字段
    if (serverTokenInput) {
        config.security_token = serverTokenInput.value.trim();
    }
    
    config.temperature = parseFloat(tempSlider.value);
    config.max_tokens = parseInt(maxTokensInput.value) || 4096;
    
    const thinkingMode = document.querySelector("input[name='thinking-mode']:checked").value;
    config.thinking_enabled = (thinkingMode !== "disabled");
    config.thinking_type = thinkingMode;
    config.thinking_budget = parseInt(budgetTokensInput.value) || 16000;
    
    if (thinkingLevelSelect) {
        config.thinking_level = thinkingLevelSelect.value || "high";
    }
    if (autoRunCodeInput) {
        config.auto_run_code = autoRunCodeInput.checked;
        if (enableCodeSandboxInput) config.enable_code_sandbox = enableCodeSandboxInput.checked;
    }
    if (fontModeSelect) {
        config.font_mode = fontModeSelect.value || "custom";
    }
    const enableSearchCheckbox = document.getElementById("enable-search-input");
    if (enableSearchCheckbox) {
        config.enable_web_search = enableSearchCheckbox.checked;
        if (typeof updateSearchBtnUI === "function") updateSearchBtnUI();
    }
    const enableFetchCheckbox = document.getElementById("enable-fetch-input");
    if (enableFetchCheckbox) {
        config.enable_web_fetch = enableFetchCheckbox.checked;
    }
    const webFetchLimitInput = document.getElementById("web-fetch-limit-input");
    if (webFetchLimitInput) {
        config.web_fetch_limit = parseInt(webFetchLimitInput.value) || 15000;
    }
    if (enableServerInput) {
        config.enable_server = enableServerInput.checked;
    }
    if (syncConfigInput) {
        config.sync_config_to_web = syncConfigInput.checked;
    }
    if (onlyServerInput) {
        config.only_server = onlyServerInput.checked;
    }
    if (enableSslInput) {
        config.enable_ssl = enableSslInput.checked;
    }
    if (serverPortInput) {
        config.server_port = parseInt(serverPortInput.value) || 8000;
    }
    if (searchEngineSelect) {
        config.web_search_engine = searchEngineSelect.value || "google";
    }
    if (webPageParserSelect) {
        config.web_page_parser = webPageParserSelect.value || "local";
    }
    applyFontMode();
    updateSearchBtnUI();
    
    const serverSettingsChanged = (
        (enableServerInput && enableServerInput.checked !== initialEnableServer) ||
        (serverPortInput && (parseInt(serverPortInput.value) || 8000) !== initialServerPort) ||
        (enableSslInput && enableSslInput.checked !== initialEnableSsl)
    );
    
    await apiBridge.save_config(config);
    const fetchedConfig = await apiBridge.get_config();
    config = fetchedConfig;
    updateLedStatus();
    hideModal(settingsModal);
    statusLabel.textContent = "设置已保存";
    
    if (serverSettingsChanged) {
        alert("检测到服务端、端口或 SSL 传输设置已被修改。\n\n请手动关闭本程序并重新启动，新设置才能生效！");
    }
};

// 日志查看器弹窗处理逻辑
async function loadAndShowLogs() {
    const logs = await apiBridge.get_logs();
    if (logsContent) {
        logsContent.textContent = logs;
    }
    if (logsContainer) {
        logsContainer.scrollTop = logsContainer.scrollHeight;
    }
}

viewLogsBtn.onclick = () => {
    hideModal(settingsModal);
    loadAndShowLogs();
    showModal(logsModal);
};

refreshLogsBtn.onclick = async () => {
    await loadAndShowLogs();
    statusLabel.textContent = "日志已刷新";
};

clearLogsBtn.onclick = async () => {
    const success = await apiBridge.clear_logs();
    if (success) {
        await loadAndShowLogs();
        statusLabel.textContent = "日志已清空";
    } else {
        statusLabel.textContent = "清空日志失败";
    }
};

copyLogsBtn.onclick = () => {
    if (logsContent) {
        copyText(logsContent.textContent || "");
        statusLabel.textContent = "📋 日志已成功复制到剪贴板";
    }
};

// 调试抓包数据弹窗处理逻辑
async function showPacketModal(convId, messageIndex) {
    if (!convId || messageIndex === -1) return;
    packetContent.textContent = "正在从数据库加载原始数据包...";
    showModal(packetModal);
    
    try {
        const result = await apiBridge.get_message_packet(convId, messageIndex);
        if (result.error) {
            packetContent.textContent = "加载失败: " + result.error;
        } else {
            const jsonText = JSON.stringify(result, null, 2);
            packetContent.textContent = jsonText;
            if (typeof hljs !== 'undefined') {
                hljs.highlightElement(packetContent);
            }
        }
    } catch (e) {
        packetContent.textContent = "加载失败: " + e;
    }
}

copyPacketBtn.onclick = () => {
    copyText(packetContent.textContent);
    statusLabel.textContent = "📋 数据包 JSON 已成功复制到剪贴板";
};

async function reloadCurrentConversation() {
    if (!currentConvId) return;
    const conv = await apiBridge.load_conversation(currentConvId);
    if (!conv) return;
    currentConv = conv;
    messageList.innerHTML = "";
    
    const messages = conv.messages || [];
    for (let i = 0; i < messages.length; i++) {
        const msg = messages[i];
        
        // 跳过独立的 tool_result 用户消息气泡
        if (msg.role === "user" && isToolResultMsg(msg.content)) {
            continue;
        }
        
        let toolCalls = extractToolCallsFromMsg(msg, i + 1 < messages.length ? messages[i + 1] : null);
        
        appendMessage(msg.role, msg.content, msg.thinking, false, i, toolCalls);
    }
    scrollChatBottom();
    
    if (config.auto_run_code) {
        autoRunLastAssistantCode();
    }
}

// 代理配置弹窗处理逻辑
proxyBtn.onclick = () => {
    document.querySelectorAll("input[name='proxy-mode']").forEach(radio => {
        radio.checked = (radio.value === config.proxy_mode);
    });
    proxyUrlInput.value = config.proxy_url || "";
    toggleProxyGroup(config.proxy_mode);
    showModal(proxyModal);
};

document.querySelectorAll("input[name='proxy-mode']").forEach(radio => {
    radio.onchange = (e) => {
        toggleProxyGroup(e.target.value);
    };
});

function toggleProxyGroup(mode) {
    if (mode === "custom") {
        customProxyGroup.classList.remove("hidden");
    } else {
        customProxyGroup.classList.add("hidden");
    }
}

saveProxyBtn.onclick = async () => {
    config.proxy_mode = document.querySelector("input[name='proxy-mode']:checked").value;
    config.proxy_url = proxyUrlInput.value.trim();
    
    await apiBridge.save_config(config);
    hideModal(proxyModal);
    statusLabel.textContent = "代理设置已更新";
};

// 对话删除确认弹窗
let deleteTargetId = null;

function showDeleteConfirm(id) {
    deleteTargetId = id;
    showModal(deleteModal);
}

clearChatBtn.onclick = () => {
    if (currentConvId) {
        showDeleteConfirm(currentConvId);
    }
};

confirmDeleteBtn.onclick = async () => {
    if (!deleteTargetId) return;
    
    await apiBridge.delete_conversation(deleteTargetId);
    hideModal(deleteModal);
    await loadConversations();
    
    // Switch to first remaining conversation or make new
    if (deleteTargetId === currentConvId) {
        if (conversations.length > 0) {
            await selectConversation(conversations[0].id);
        } else {
            await startNewChat();
        }
    } else {
        // Stay on current conversation
        await selectConversation(currentConvId);
    }
    
    deleteTargetId = null;
    statusLabel.textContent = "对话已删除";
};

// 右键自定义上下文菜单控制
const contextMenu = document.getElementById("custom-context-menu");
const menuCopy = document.getElementById("menu-copy");
const menuCopyMsg = document.getElementById("menu-copy-msg");
const menuCut = document.getElementById("menu-cut");
const menuPaste = document.getElementById("menu-paste");
const menuSelectAll = document.getElementById("menu-selectall");
let contextMenuTarget = null;

window.addEventListener("contextmenu", (e) => {
    const target = e.target;
    
    // Check if target is a text editable field
    const isTextInput = target.tagName === "TEXTAREA" || 
                        (target.tagName === "INPUT" && 
                         ["text", "password", "number", "url"].includes(target.type));
    
    // Check if there is selected text anywhere on the page
    const selection = window.getSelection();
    const selectedText = selection.toString();
    const hasSelection = selectedText.length > 0;
    
    // Check if right click occurs inside a message card
    const messageCard = target.closest(".message-card");
    
    // Only show custom context menu if in text input, text is selected, or inside a message bubble
    if (!isTextInput && !hasSelection && !messageCard) {
        hideContextMenu();
        return;
    }
    
    e.preventDefault();
    contextMenuTarget = target;
    
    // Position menu near cursor
    const menuWidth = 190;
    const menuHeight = 220;
    let x = e.clientX;
    let y = e.clientY;
    
    if (x + menuWidth > window.innerWidth) {
        x = window.innerWidth - menuWidth - 10;
    }
    if (y + menuHeight > window.innerHeight) {
        y = window.innerHeight - menuHeight - 10;
    }
    
    contextMenu.style.left = `${x}px`;
    contextMenu.style.top = `${y}px`;
    contextMenu.classList.remove("hidden");
    
    if (isTextInput) {
        // Cut and paste are allowed inside input fields
        let hasInputSelection = false;
        try {
            hasInputSelection = (target.selectionStart !== target.selectionEnd);
        } catch (err) {
            hasInputSelection = (target.value && target.value.length > 0);
        }
        
        menuCopy.textContent = "📋 复制 (Copy)";
        menuCopy.disabled = !hasInputSelection;
        menuCopy.style.display = "flex";
        
        menuCopyMsg.style.display = "none";
        
        menuCut.disabled = !hasInputSelection;
        menuCut.style.display = "flex";
        
        menuPaste.disabled = false;
        menuPaste.style.display = "flex";
        
        menuSelectAll.textContent = "🔍 全选 (Select All)";
        menuSelectAll.disabled = false;
        menuSelectAll.style.display = "flex";
    } else if (messageCard) {
        // Inside a message card
        if (hasSelection) {
            menuCopy.textContent = "📋 复制选中文字 (Copy Selection)";
            menuCopy.disabled = false;
            menuCopy.style.display = "flex";
        } else {
            menuCopy.style.display = "none";
        }
        
        // Show whole message copy
        menuCopyMsg.disabled = false;
        menuCopyMsg.style.display = "flex";
        
        // Hide inputs specific edit commands
        menuCut.style.display = "none";
        menuPaste.style.display = "none";
        
        // Select message content option
        menuSelectAll.textContent = "🔍 选中整条消息 (Select Message)";
        menuSelectAll.disabled = false;
        menuSelectAll.style.display = "flex";
    } else {
        // General text selection on page (outside message bubble)
        menuCopy.textContent = "📋 复制选中文字 (Copy Selection)";
        menuCopy.disabled = false;
        menuCopy.style.display = "flex";
        
        menuCopyMsg.style.display = "none";
        menuCut.style.display = "none";
        menuPaste.style.display = "none";
        
        menuSelectAll.textContent = "🔍 全选页面内容 (Select All)";
        menuSelectAll.disabled = false;
        menuSelectAll.style.display = "flex";
    }
});

function hideContextMenu() {
    contextMenu.classList.add("hidden");
}

window.addEventListener("click", (e) => {
    hideContextMenu();
});

window.addEventListener("resize", hideContextMenu);

// Copy Selection action
menuCopy.addEventListener("click", () => {
    if (!contextMenuTarget) return;
    
    let textToCopy = "";
    if (contextMenuTarget.tagName === "TEXTAREA" || contextMenuTarget.tagName === "INPUT") {
        try {
            const start = contextMenuTarget.selectionStart;
            const end = contextMenuTarget.selectionEnd;
            textToCopy = contextMenuTarget.value.substring(start, end);
        } catch (err) {
            textToCopy = contextMenuTarget.value;
        }
    } else {
        textToCopy = window.getSelection().toString();
    }
    
    if (textToCopy) {
        copyText(textToCopy);
    }
});

// Copy Entire Message action
menuCopyMsg.addEventListener("click", () => {
    if (!contextMenuTarget) return;
    const messageCard = contextMenuTarget.closest(".message-card");
    if (messageCard) {
        const body = messageCard.querySelector(".message-body");
        if (body) {
            copyText(body.innerText);
        }
    }
});

// Cut action
menuCut.addEventListener("click", () => {
    if (!contextMenuTarget) return;
    
    if (contextMenuTarget.tagName === "TEXTAREA" || contextMenuTarget.tagName === "INPUT") {
        try {
            const start = contextMenuTarget.selectionStart;
            const end = contextMenuTarget.selectionEnd;
            const val = contextMenuTarget.value;
            const textToCut = val.substring(start, end);
            
            if (textToCut) {
                copyText(textToCut);
                contextMenuTarget.value = val.substring(0, start) + val.substring(end);
                contextMenuTarget.selectionStart = contextMenuTarget.selectionEnd = start;
                
                // Trigger input event to resize textarea
                const event = new Event('input', { bubbles: true });
                contextMenuTarget.dispatchEvent(event);
            }
        } catch (err) {
            const val = contextMenuTarget.value;
            copyText(val);
            contextMenuTarget.value = "";
            const event = new Event('input', { bubbles: true });
            contextMenuTarget.dispatchEvent(event);
        }
    }
});

// Paste action
menuPaste.addEventListener("click", async () => {
    if (!contextMenuTarget) return;
    
    if (contextMenuTarget.tagName === "TEXTAREA" || contextMenuTarget.tagName === "INPUT") {
        const clipboardText = await apiBridge.paste_from_clipboard();
        if (clipboardText) {
            try {
                const start = contextMenuTarget.selectionStart;
                const end = contextMenuTarget.selectionEnd;
                const val = contextMenuTarget.value;
                
                contextMenuTarget.value = val.substring(0, start) + clipboardText + val.substring(end);
                
                const newPos = start + clipboardText.length;
                contextMenuTarget.selectionStart = contextMenuTarget.selectionEnd = newPos;
            } catch (err) {
                // Fallback for number inputs
                contextMenuTarget.value = clipboardText;
            }
            contextMenuTarget.focus();
            
            // Trigger input event to resize textarea
            const event = new Event('input', { bubbles: true });
            contextMenuTarget.dispatchEvent(event);
        }
    }
});

// Select All action
menuSelectAll.addEventListener("click", () => {
    if (!contextMenuTarget) return;
    
    if (contextMenuTarget.tagName === "TEXTAREA" || contextMenuTarget.tagName === "INPUT") {
        contextMenuTarget.select();
    } else {
        const messageCard = contextMenuTarget.closest(".message-card");
        if (messageCard && (menuSelectAll.textContent.includes("整条消息") || menuSelectAll.textContent.includes("Message"))) {
            const body = messageCard.querySelector(".message-body");
            if (body) {
                const range = document.createRange();
                range.selectNodeContents(body);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
            }
        } else {
            const range = document.createRange();
            range.selectNodeContents(document.body);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        }
    }
});

// ==========================================
// 全局文件拖拽拖放文件识别模块
// ==========================================
const dragDropOverlay = document.getElementById("drag-drop-overlay");

window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    if (e.dataTransfer.types.includes("Files")) {
        dragDropOverlay.classList.remove("hidden");
    }
});

window.addEventListener("dragover", (e) => {
    e.preventDefault();
});

window.addEventListener("dragleave", (e) => {
    if (e.relatedTarget === null || e.relatedTarget === document.documentElement) {
        dragDropOverlay.classList.add("hidden");
    }
});

window.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragDropOverlay.classList.add("hidden");
    
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        for (let i = 0; i < e.dataTransfer.files.length; i++) {
            const file = e.dataTransfer.files[i];
            await handleDroppedFile(file);
        }
    }
});

async function handleDroppedFile(file) {
    const maxSize = 20 * 1024 * 1024; // 20MB
    if (file.size > maxSize) {
        statusLabel.textContent = `文件添加失败: ${file.name} (超过 20MB 限制)`;
        return;
    }
    statusLabel.textContent = `正在读取文件: ${file.name}...`;
    
    const reader = new FileReader();
    reader.onload = async (event) => {
        const base64Data = event.target.result;
        const uploaded = await apiBridge.upload_dropped_file(file.name, file.size, base64Data);
        if (uploaded) {
            attachments.push(uploaded);
            renderAttachments();
            if (config.active_platform === "deepseek") {
                statusLabel.textContent = `附件已添加: ${file.name} (已开启本地文档解析与图像 OCR 提取)`;
            } else {
                statusLabel.textContent = `附件已添加: ${file.name}`;
            }
            setTimeout(() => { if (statusLabel.textContent.startsWith("附件已添加")) statusLabel.textContent = "就绪"; }, 3000);
        } else {
            statusLabel.textContent = `文件添加失败: ${file.name}`;
        }
    };
    reader.onerror = () => {
        statusLabel.textContent = `文件读取失败: ${file.name}`;
    };
    reader.readAsDataURL(file);
}

// ==========================================
// 自定义系统提示词预设管理器
// ==========================================
const systemPromptSelect = document.getElementById("system-prompt-select");
const presetsList = document.getElementById("presets-list");
const presetModal = document.getElementById("preset-modal");
const presetNameInput = document.getElementById("preset-name-input");
const presetContentInput = document.getElementById("preset-content-input");
const savePresetBtn = document.getElementById("save-preset-btn");
const cancelPresetBtn = document.getElementById("cancel-preset-btn");
const closePresetModalBtn = document.getElementById("close-preset-modal-btn");
const presetModalTitle = document.getElementById("preset-modal-title");
const addPresetBtn = document.getElementById("add-preset-btn");

let editingPresetId = null;

function renderSystemPromptSelect() {
    if (!systemPromptSelect) return;
    systemPromptSelect.innerHTML = '<option value="">无系统提示词 (默认)</option>';
    const presets = config.system_prompts || [];
    presets.forEach(p => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.name;
        if (p.id === config.selected_system_prompt_id) {
            opt.selected = true;
        }
        systemPromptSelect.appendChild(opt);
    });
}

if (systemPromptSelect) {
    systemPromptSelect.addEventListener("change", async (e) => {
        config.selected_system_prompt_id = e.target.value;
        await apiBridge.save_config(config);
        statusLabel.textContent = `系统提示词已更新`;
        setTimeout(() => { if (statusLabel.textContent === "系统提示词已更新") statusLabel.textContent = "就绪"; }, 2000);
    });
}

function renderPresetsList() {
    if (!presetsList) return;
    presetsList.innerHTML = "";
    const presets = config.system_prompts || [];
    if (presets.length === 0) {
        presetsList.innerHTML = '<div style="color: var(--overlay0); text-align: center; padding: 12px; font-size: 12px;">暂无自定义系统提示词</div>';
        return;
    }
    presets.forEach(p => {
        const item = document.createElement("div");
        item.style.display = "flex";
        item.style.justifyContent = "space-between";
        item.style.alignItems = "center";
        item.style.padding = "6px 8px";
        item.style.borderBottom = "1px solid var(--surface0)";
        item.style.fontSize = "12px";
        
        const nameSpan = document.createElement("span");
        nameSpan.textContent = p.name;
        nameSpan.style.fontWeight = "500";
        nameSpan.style.color = "var(--text)";
        item.appendChild(nameSpan);
        
        const actionsDiv = document.createElement("div");
        actionsDiv.style.display = "flex";
        actionsDiv.style.gap = "6px";
        
        const editBtn = document.createElement("button");
        editBtn.className = "btn btn-secondary btn-sm";
        editBtn.textContent = "编辑";
        editBtn.style.padding = "2px 6px";
        editBtn.style.fontSize = "10px";
        editBtn.onclick = (e) => {
            e.preventDefault();
            showPresetEditor(p.id);
        };
        
        const delBtn = document.createElement("button");
        delBtn.className = "btn btn-danger btn-sm";
        delBtn.textContent = "删除";
        delBtn.style.padding = "2px 6px";
        delBtn.style.fontSize = "10px";
        delBtn.onclick = (e) => {
            e.preventDefault();
            deletePreset(p.id);
        };
        
        actionsDiv.appendChild(editBtn);
        actionsDiv.appendChild(delBtn);
        item.appendChild(actionsDiv);
        presetsList.appendChild(item);
    });
}

function showPresetEditor(id = null) {
    editingPresetId = id;
    if (id) {
        presetModalTitle.textContent = "📝 编辑系统提示词预设";
        const p = config.system_prompts.find(x => x.id === id);
        presetNameInput.value = p ? p.name : "";
        presetContentInput.value = p ? p.content : "";
    } else {
        presetModalTitle.textContent = "📝 添加系统提示词预设";
        presetNameInput.value = "";
        presetContentInput.value = "";
    }
    presetModal.classList.remove("hidden");
}

function hidePresetEditor() {
    presetModal.classList.add("hidden");
    editingPresetId = null;
}

if (addPresetBtn) addPresetBtn.onclick = () => showPresetEditor(null);
if (cancelPresetBtn) cancelPresetBtn.onclick = hidePresetEditor;
if (closePresetModalBtn) closePresetModalBtn.onclick = hidePresetEditor;

if (savePresetBtn) {
    savePresetBtn.onclick = async () => {
        const name = presetNameInput.value.trim();
        const content = presetContentInput.value.trim();
        if (!name || !content) {
            alert("名称和内容不能为空！");
            return;
        }
        
        if (!config.system_prompts) config.system_prompts = [];
        
        if (editingPresetId) {
            const p = config.system_prompts.find(x => x.id === editingPresetId);
            if (p) {
                p.name = name;
                p.content = content;
            }
        } else {
            const newId = "preset_" + Date.now();
            config.system_prompts.push({
                id: newId,
                name: name,
                content: content
            });
        }
        
        await apiBridge.save_config(config);
        hidePresetEditor();
        renderPresetsList();
        renderSystemPromptSelect();
    };
}

async function deletePreset(id) {
    if (!confirm("确定要删除该预设吗？")) return;
    config.system_prompts = (config.system_prompts || []).filter(x => x.id !== id);
    if (config.selected_system_prompt_id === id) {
        config.selected_system_prompt_id = "";
    }
    await apiBridge.save_config(config);
    renderPresetsList();
    renderSystemPromptSelect();
}

// ==========================================
// Collapsible Artifacts 渲染沙盒侧边栏
// ==========================================
const artifactsPanel = document.getElementById("artifacts-panel");
const closeArtifactsBtn = document.getElementById("close-artifacts-btn");
const artifactsFullscreenBtn = document.getElementById("artifacts-fullscreen-btn");
const artifactsTabBtns = document.querySelectorAll(".artifacts-tab-btn");
const artifactsTabContents = document.querySelectorAll(".artifacts-tab-content");
const artifactsPreviewContainer = document.getElementById("artifacts-preview-container");
const artifactsCodeView = document.getElementById("artifacts-code-view");

let currentArtifactContent = "";
let currentArtifactType = "";

if (closeArtifactsBtn) {
    closeArtifactsBtn.onclick = () => {
        artifactsPanel.classList.add("collapsed");
    };
}

if (artifactsFullscreenBtn) {
    artifactsFullscreenBtn.onclick = () => {
        const isFullscreen = artifactsPanel.classList.toggle("fullscreen");
        artifactsFullscreenBtn.textContent = isFullscreen ? "🗖" : "🖥️";
        artifactsFullscreenBtn.title = isFullscreen ? "还原" : "全屏";
    };
}

artifactsTabBtns.forEach(btn => {
    btn.onclick = () => {
        const tabName = btn.getAttribute("data-tab");
        artifactsTabBtns.forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        
        artifactsTabContents.forEach(content => {
            if (content.id === `artifacts-${tabName}-tab`) {
                content.classList.add("active");
            } else {
                content.classList.remove("active");
            }
        });
        
        if (tabName === "code") {
            if (typeof hljs !== 'undefined') {
                hljs.highlightElement(artifactsCodeView);
            }
        }
    };
});

function showArtifact(content, type) {
    currentArtifactContent = content;
    currentArtifactType = type;
    
    artifactsPanel.classList.remove("collapsed");
    artifactsCodeView.textContent = content;
    artifactsCodeView.className = "";
    
    if (type === "html" || type === "xml") {
        artifactsCodeView.classList.add("language-xml");
    } else if (type === "svg") {
        artifactsCodeView.classList.add("language-xml");
    } else if (type === "mermaid") {
        artifactsCodeView.classList.add("language-mermaid");
    }
    
    const previewTabBtn = Array.from(artifactsTabBtns).find(b => b.getAttribute("data-tab") === "preview");
    if (previewTabBtn) previewTabBtn.click();
    
    renderArtifactPreview(content, type);
}

function renderArtifactPreview(content, type) {
    artifactsPreviewContainer.innerHTML = "";
    
    if (type === "html" || type === "xml") {
        const iframe = document.createElement("iframe");
        iframe.style.width = "100%";
        iframe.style.height = "100%";
        iframe.style.border = "none";
        iframe.style.backgroundColor = "#ffffff";
        iframe.sandbox = "allow-scripts allow-same-origin allow-forms allow-modals allow-popups";
        iframe.srcdoc = content;
        
        artifactsPreviewContainer.appendChild(iframe);
        
    } else if (type === "svg") {
        artifactsPreviewContainer.innerHTML = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(content) : content;
        const svgEl = artifactsPreviewContainer.querySelector("svg");
        if (svgEl) {
            svgEl.style.maxWidth = "100%";
            svgEl.style.height = "auto";
            svgEl.style.display = "block";
            svgEl.style.margin = "0 auto";
        }
        
    } else if (type === "mermaid") {
        if (typeof mermaid !== 'undefined') {
            const uniqueId = `mermaid-${Date.now()}`;
            const div = document.createElement("div");
            div.className = "mermaid";
            div.id = uniqueId;
            div.textContent = content;
            artifactsPreviewContainer.appendChild(div);
            
            try {
                mermaid.initialize({
                    startOnLoad: false,
                    theme: 'dark',
                    securityLevel: 'strict'
                });
                mermaid.init(undefined, `#${uniqueId}`);
            } catch (err) {
                artifactsPreviewContainer.innerHTML = `<span style="color: var(--red);">Mermaid 渲染错误: ${err.message}</span>`;
            }
        } else {
            artifactsPreviewContainer.innerHTML = '<span style="color: var(--yellow);">Mermaid 库未加载，无法预览图表</span>';
        }
    }
}

// 用户消息历史内嵌快捷二次修改并重新生成
async function editUserMessage(msgIndex) {
    if (isStreaming) return;
    if (isSending) return;
    const msgRow = messageList.querySelector(`.message-row[data-msg-index="${msgIndex}"]`);
    if (!msgRow) return;
    const body = msgRow.querySelector(".message-body");
    if (!body) return;
    
    const rawContent = currentConv.messages[msgIndex].content;
    let textVal = "";
    if (typeof rawContent === 'string') {
        textVal = rawContent;
    } else if (Array.isArray(rawContent)) {
        rawContent.forEach(item => {
            if (item.type === "text") textVal += item.text;
        });
    }
    
    const originalHTML = body.innerHTML;
    body.innerHTML = `
        <div class="edit-msg-container" style="display: flex; flex-direction: column; gap: 8px; width: 100%; margin-top: 4px;">
            <textarea class="edit-msg-textarea" style="width: 100%; min-height: 80px; background-color: var(--crust); border: 1px solid var(--surface0); border-radius: 6px; color: var(--text); padding: 8px; font-family: inherit; font-size: 13px; outline: none; resize: vertical;"></textarea>
            <div style="display: flex; gap: 8px; justify-content: flex-end;">
                <button class="btn btn-secondary btn-sm edit-cancel-btn" style="padding: 4px 10px; font-size: 11px;">取消</button>
                <button class="btn btn-primary btn-sm edit-save-btn" style="padding: 4px 10px; font-size: 11px;">保存并发送</button>
            </div>
        </div>
    `;
    
    const textarea = body.querySelector(".edit-msg-textarea");
    textarea.value = textVal;
    textarea.focus();
    
    textarea.style.height = "auto";
    textarea.style.height = textarea.scrollHeight + "px";
    textarea.oninput = () => {
        textarea.style.height = "auto";
        textarea.style.height = textarea.scrollHeight + "px";
    };
    
    body.querySelector(".edit-cancel-btn").onclick = (e) => {
        e.stopPropagation();
        body.innerHTML = originalHTML;
        highlightCodeBlocks(msgRow);
    };
    
    body.querySelector(".edit-save-btn").onclick = async (e) => {
        e.stopPropagation();
        const newText = textarea.value.trim();
        if (!newText) return;
        
        // Remove msgRow and all subsequent elements from DOM
        let current = messageList.lastChild;
        while (current) {
            const prev = current.previousSibling;
            messageList.removeChild(current);
            if (current === msgRow) break;
            current = prev;
        }
        
        appendMessage("user", newText, "", false, msgIndex);
        scrollChatBottom();
        
        appendMessage("assistant", "思考中...", "", true);
        
        isStreaming = true;
        streamingText = "";
        streamingThinking = "";
        
        sendBtn.classList.add("stop-active");
        sendBtn.title = "停止生成";
        const sendIcon = sendBtn.querySelector(".send-icon");
        if (sendIcon) sendIcon.textContent = "■";
        statusLabel.textContent = "Claude 思考中...";
        
        isSending = true;
        try {
            await apiBridge.edit_and_resend(currentConvId, msgIndex, newText);
        } finally {
            isSending = false;
        }
    };
}

// 创建分支对话
async function branchConversation(msgIndex) {
    if (isStreaming) return;
    statusLabel.textContent = "正在创建分支对话...";
    const newConv = await apiBridge.branch_conversation(currentConvId, msgIndex);
    if (newConv) {
        await loadConversations();
        await selectConversation(newConv.id);
        statusLabel.textContent = `分支创建成功: ${newConv.title}`;
        setTimeout(() => { if (statusLabel.textContent.startsWith("分支创建成功")) statusLabel.textContent = "就绪"; }, 2000);
    } else {
        statusLabel.textContent = "分支创建失败";
    }
}

// 重新生成模型答复
async function retryAssistantMessage(msgIndex) {
    if (isStreaming) return;
    
    messageList.removeChild(messageList.lastChild);
    scrollChatBottom();
    
    appendMessage("assistant", "思考中...", "", true);
    
    isStreaming = true;
    streamingText = "";
    streamingThinking = "";
    
    sendBtn.classList.add("stop-active");
    sendBtn.title = "停止生成";
    const sendIcon = sendBtn.querySelector(".send-icon");
    if (sendIcon) sendIcon.textContent = "■";
    statusLabel.textContent = "Claude 思考中...";
    
    await apiBridge.retry_message(currentConvId, msgIndex);
}

// 本地控制台交互式代码块执行终端
// Active console process registry
const activeConsoleProcesses = new Map();

// Global callbacks for interactive console output & exit
window.onConsoleOutput = (procId, streamType, char) => {
    const info = activeConsoleProcesses.get(procId);
    if (info) {
        const output = info.outputDiv;
        let lastSpan = output.lastElementChild;
        if (!lastSpan || lastSpan.dataset.stream !== streamType) {
            lastSpan = document.createElement("span");
            lastSpan.dataset.stream = streamType;
            if (streamType === "stdout") {
                lastSpan.style.color = "var(--green)";
            } else if (streamType === "stderr") {
                lastSpan.style.color = "var(--red)";
            }
            output.appendChild(lastSpan);
        }
        lastSpan.textContent += char;
        output.scrollTop = output.scrollHeight;
    }
};

window.onConsoleExit = (procId, exitCode) => {
    const info = activeConsoleProcesses.get(procId);
    if (info) {
        const output = info.outputDiv;
        const statusSpan = document.createElement("span");
        if (exitCode === 0) {
            statusSpan.style.color = "var(--overlay0)";
            statusSpan.style.fontStyle = "italic";
            statusSpan.textContent = `\n[程序运行完毕，退出状态码: ${exitCode}]\n`;
        } else {
            statusSpan.style.color = "var(--red)";
            statusSpan.style.fontWeight = "bold";
            statusSpan.textContent = `\n[程序异常退出，退出状态码: ${exitCode}]\n`;
        }
        output.appendChild(statusSpan);
        output.scrollTop = output.scrollHeight;
        
        if (info.inputBar) {
            info.inputBar.style.display = "none";
        }
        activeConsoleProcesses.delete(procId);
    }
};

// Local Code Block Execution
async function runCodeBlock(code, lang, preElement) {
    let consoleBox = preElement.nextSibling;
    if (consoleBox && consoleBox.classList && consoleBox.classList.contains("code-console-box")) {
        // Reuse existing console box
    } else {
        consoleBox = document.createElement("div");
        consoleBox.className = "code-console-box";
        consoleBox.style.backgroundColor = "var(--crust)";
        consoleBox.style.border = "1px solid var(--surface0)";
        consoleBox.style.borderRadius = "0 0 8px 8px";
        consoleBox.style.marginTop = "-8px";
        consoleBox.style.marginBottom = "12px";
        consoleBox.style.padding = "10px 14px";
        consoleBox.style.fontFamily = "Consolas, monospace";
        consoleBox.style.fontSize = "11.5px";
        consoleBox.style.color = "var(--text)";
        consoleBox.style.display = "flex";
        consoleBox.style.flexDirection = "column";
        
        preElement.style.borderRadius = "8px 8px 0 0";
        preElement.parentNode.insertBefore(consoleBox, preElement.nextSibling);
    }
    
    // Render terminal structure
    consoleBox.innerHTML = `
        <div class="console-output-container" style="max-height: 180px; overflow-y: auto; white-space: pre-wrap; font-family: Consolas, monospace; font-size: 11.5px; word-break: break-all; padding-bottom: 4px;"></div>
        <div class="console-input-bar" style="display: flex; align-items: center; border-top: 1px solid var(--surface0); padding-top: 8px; margin-top: 4px; gap: 8px;">
            <span style="color: var(--blue); font-weight: bold; font-family: monospace;">&gt;</span>
            <input type="text" class="console-input" placeholder="输入内容并回车..." style="flex: 1; background: transparent; border: none; outline: none; color: var(--text); font-family: Consolas, monospace; font-size: 11.5px; padding: 0;">
            <button class="console-stop-btn" style="background-color: var(--red) !important; color: var(--crust) !important; border: none; border-radius: 4px; padding: 2px 8px; font-size: 11px; cursor: pointer; font-weight: bold; width: auto; height: auto;">⏹️ 终止</button>
        </div>
    `;
    
    const outputDiv = consoleBox.querySelector(".console-output-container");
    const inputBar = consoleBox.querySelector(".console-input-bar");
    const inputField = consoleBox.querySelector(".console-input");
    const stopBtn = consoleBox.querySelector(".console-stop-btn");
    
    outputDiv.innerHTML = `<span style="color: var(--yellow);">⚙️ 正在利用本地环境启动程序...</span>\n`;
    
    try {
        const result = await apiBridge.start_code_execution(code, lang);
        if (result.error) {
            outputDiv.innerHTML = `<span style="color: var(--red);">❌ 启动失败:</span>\n${result.error}`;
            if (inputBar) inputBar.style.display = "none";
        } else {
            const procId = result.process_id;
            outputDiv.innerHTML = `<span style="color: var(--green);">[程序已启动，正在运行...]</span>\n`;
            
            // Register process details
            activeConsoleProcesses.set(procId, {
                container: consoleBox,
                outputDiv: outputDiv,
                inputBar: inputBar,
                inputField: inputField
            });
            
            // Bind input event
            inputField.onkeydown = async (e) => {
                if (e.key === "Enter") {
                    const text = inputField.value;
                    inputField.value = "";
                    
                    // Echo output
                    const userSpan = document.createElement("span");
                    userSpan.style.color = "var(--blue)";
                    userSpan.style.fontWeight = "bold";
                    userSpan.textContent = `> ${text}\n`;
                    outputDiv.appendChild(userSpan);
                    outputDiv.scrollTop = outputDiv.scrollHeight;
                    
                    await apiBridge.send_console_input(procId, text);
                }
            };
            
            // Bind stop button
            stopBtn.onclick = async () => {
                stopBtn.disabled = true;
                stopBtn.textContent = "正在终止...";
                await apiBridge.kill_console_process(procId);
                // 修复竞态条件: 仅请求后端杀进程，由后端的 exit 事件触发 onConsoleExit 来真正清理 UI 和状态。
                // 为了防止极端情况后端没有发来 exit 事件，设置一个兜底超时。
                setTimeout(() => {
                    if (activeConsoleProcesses.has(procId)) {
                        if (window.onConsoleExit) window.onConsoleExit(procId, -1);
                    }
                }, 1500);
            };
            
            // Focus input field
            inputField.focus();
        }
    } catch (e) {
        outputDiv.innerHTML = `<span style="color: var(--red);">❌ 启动出错:</span>\n${e}`;
        if (inputBar) inputBar.style.display = "none";
    }
    
    // 滚动当前运行的控制台区域到视野内，而不是强制滚动到页面最底部
    if (consoleBox && typeof consoleBox.scrollIntoView === "function") {
        consoleBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
}

// 检查并自动运行模型最后输出的代码块（如果开启了 auto_run_code）
function autoRunLastAssistantCode() {
    const assistantRows = messageList.querySelectorAll(".message-row.assistant");
    if (assistantRows.length > 0) {
        const lastRow = assistantRows[assistantRows.length - 1];
        lastRow.querySelectorAll("pre code").forEach(block => {
            let lang = "";
            block.classList.forEach(cls => {
                if (cls.startsWith('language-')) {
                    lang = cls.replace('language-', '').toLowerCase();
                }
            });
            if (lang === 'python' || lang === 'javascript' || lang === 'js') {
                const pre = block.parentNode;
                runCodeBlock(block.innerText, lang, pre);
            }
        });
    }
}

// --- Image Preview & Save Logic ---
const imageModal = document.getElementById("image-modal");
const previewImageEl = document.getElementById("preview-image-el");
const closeImageModalBtn = document.getElementById("close-image-modal-btn");
const downloadImageBtn = document.getElementById("download-image-btn");

if (messageList) {
    messageList.addEventListener("click", (e) => {
        if (e.target.tagName === "IMG") {
            const src = e.target.src;
            if (src) {
                previewImageEl.src = src;
                imageModal.classList.remove("hidden");
            }
        }
    });
}

if (closeImageModalBtn) {
    closeImageModalBtn.addEventListener("click", () => {
        imageModal.classList.add("hidden");
        previewImageEl.src = "";
    });
}

if (imageModal) {
    imageModal.addEventListener("click", (e) => {
        // Close if clicking outside the image content
        if (e.target === imageModal || e.target.closest(".modal-card") === null || e.target.classList.contains("modal-card")) {
            imageModal.classList.add("hidden");
            previewImageEl.src = "";
        }
    });
}

if (downloadImageBtn) {
    downloadImageBtn.addEventListener("click", () => {
        const src = previewImageEl.src;
        if (src) {
            apiBridge.save_image(src, "generated_image_" + new Date().getTime() + ".png");
        }
    });
}
