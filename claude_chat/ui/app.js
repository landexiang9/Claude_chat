// Global State
let config = {};
let conversations = [];
let currentConvId = null;
let currentConv = null;
let attachments = [];
let isStreaming = false;
let streamingText = "";
let streamingThinking = "";
let availableModels = [];

// DOM Elements
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
const tempSlider = document.getElementById("temp-slider");
const tempLabelTitle = document.getElementById("temp-label-title");
const maxTokensInput = document.getElementById("max-tokens-input");
const budgetGroup = document.getElementById("budget-group");
const budgetTokensInput = document.getElementById("budget-tokens-input");
const thinkingLevelGroup = document.getElementById("thinking-level-group");
const thinkingLevelSelect = document.getElementById("thinking-level-select");
const autoRunCodeInput = document.getElementById("auto-run-code-input");

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

// Initialize marked options
if (typeof marked !== 'undefined') {
    marked.setOptions({
        breaks: true,
        gfm: true
    });
}

function parseMarkdown(text) {
    if (typeof marked !== 'undefined') {
        return marked.parse(text);
    }
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;")
        .replace(/\n/g, "<br>");
}

// Wait for WebView2 container to be ready
window.addEventListener("pywebviewready", async () => {
    // 1. Fetch system config
    config = await pywebview.api.get_config();
    updateLedStatus();
    
    // Initialize system prompts dropdown
    renderSystemPromptSelect();
    
    // 2. Load and configure model dropdown
    const models = await pywebview.api.fetch_models();
    availableModels = models;
    updateModelList(models);
    
    // 3. Load conversations
    await loadConversations();
    
    // 4. Load initial conversation
    if (conversations.length > 0) {
        await selectConversation(conversations[0].id);
    } else {
        await startNewChat();
    }
});

// Update connection LED dot
function updateLedStatus() {
    if (config.api_key && config.api_key.trim()) {
        apiStatusLed.className = "status-led online";
    } else {
        apiStatusLed.className = "status-led";
    }
}

// Update model menu options
function updateModelList(models) {
    modelSelect.innerHTML = "";
    models.forEach(m => {
        const mId = typeof m === 'string' ? m : m.id;
        const mName = typeof m === 'string' ? m : (m.display_name || m.id);
        const option = document.createElement("option");
        option.value = mId;
        option.textContent = mName;
        if (mId === config.model) option.selected = true;
        modelSelect.appendChild(option);
    });
}

// Bind models updated callback
window.onModelsUpdated = (models) => {
    availableModels = models;
    updateModelList(models);
};

// Model change listener
modelSelect.addEventListener("change", async (e) => {
    config.model = e.target.value;
    
    // Safeguard: check capabilities of the new model
    const modelObj = availableModels.find(m => (typeof m === 'object' && m.id === config.model));
    if (modelObj && !modelObj.thinking_supported) {
        config.thinking_enabled = false;
    }
    
    // Sync with local conversations list
    if (currentConvId) {
        const conv = conversations.find(c => c.id === currentConvId);
        if (conv) {
            conv.model = config.model;
            if (modelObj && !modelObj.thinking_supported) {
                conv.thinking = null;
            }
        }
    }
    await pywebview.api.save_config(config);
    statusLabel.textContent = `模型切换为: ${config.model}`;
});

// Load conversations list
async function loadConversations() {
    conversations = await pywebview.api.load_conversations();
    renderConversations();
}

// Render conversations sidebar card list
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

// Select specific conversation
async function selectConversation(id) {
    if (isStreaming) return;
    currentConvId = id;
    
    // Update active highlight
    const items = convList.querySelectorAll(".conv-item");
    items.forEach((item, index) => {
        if (conversations[index] && conversations[index].id === id) {
            item.className = "conv-item active";
        } else {
            item.className = "conv-item";
        }
    });

    messageList.innerHTML = "";
    
    const conv = await pywebview.api.load_conversation(id);
    if (!conv) return;
    currentConv = conv;
    
    // Set tokens labels
    if (conv.input_tokens !== undefined && conv.output_tokens !== undefined) {
        tokenLabel.textContent = `Token: ${conv.input_tokens} in / ${conv.output_tokens} out`;
    } else {
        tokenLabel.textContent = "Token: --";
    }

    if (conv.model) {
        config.model = conv.model;
        const options = modelSelect.querySelectorAll("option");
        options.forEach(opt => {
            opt.selected = (opt.value === conv.model);
        });
    }

    // Render messages
    conv.messages.forEach((msg, idx) => {
        appendMessage(msg.role, msg.content, msg.thinking, false, idx);
    });
    
    scrollChatBottom();
}

// Start a new chat
async function startNewChat() {
    if (isStreaming) return;
    const newConv = await pywebview.api.new_conversation();
    await loadConversations();
    await selectConversation(newConv.id);
}

newChatBtn.addEventListener("click", startNewChat);

// Append message block to display area
function appendMessage(role, content, thinking, isStreamingPlaceholder = false, msgIndex = -1) {
    const row = document.createElement("div");
    row.className = `message-row ${role}`;
    if (isStreamingPlaceholder) {
        row.id = "streaming-msg-row";
    }

    const card = document.createElement("div");
    card.className = "message-card";
    
    // Header
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

    // Thinking logs (for assistant)
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

    // Body content
    const body = document.createElement("div");
    body.className = "message-body";
    body.id = isStreamingPlaceholder ? "streaming-message-body" : "";
    
    if (typeof content === "string") {
        body.innerHTML = parseMarkdown(content);
    } else if (Array.isArray(content)) {
        // Handle mixed content with attachments list
        let textContent = "";
        content.forEach(item => {
            if (item.type === "text") {
                textContent += item.text;
            }
        });
        body.innerHTML = parseMarkdown(textContent);
    }
    
    card.appendChild(body);
    row.appendChild(card);
    messageList.appendChild(row);
    
    // Highlight elements and add copy headers
    highlightCodeBlocks(card);
}

// Helper for file extensions
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

// Highlight code blocks and inject custom header copy button
function highlightCodeBlocks(container) {
    container.querySelectorAll('pre code').forEach((block) => {
        const pre = block.parentNode;
        
        // Smart highlight optimization: skip highlighting the very last streaming block if unclosed
        const isStreamingBlock = isStreaming && (block.closest('#streaming-message-body') !== null);
        if (isStreamingBlock) {
            const backtickCount = (streamingText.match(/```/g) || []).length;
            const isLastBlockOpen = (backtickCount % 2 === 1);
            if (isLastBlockOpen) {
                // Return early so we don't style or highlight it yet
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

            // Add save action
            header.querySelector('.code-save-btn').onclick = async () => {
                const content = block.innerText;
                const extension = getExtensionFromLang(lang);
                const suggestName = `code_${Date.now()}${extension}`;
                const saved = await pywebview.api.save_code_block(content, suggestName);
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

// Scroll chat list to bottom
function scrollChatBottom() {
    scrollAnchor.scrollIntoView({ behavior: "smooth" });
}

// Copy helper
function copyText(text) {
    navigator.clipboard.writeText(text);
    statusLabel.textContent = "📋 内容已成功复制到剪贴板";
    setTimeout(() => { statusLabel.textContent = "就绪"; }, 2000);
}

// Send Message action
async function sendMessage() {
    if (isStreaming) {
        statusLabel.textContent = "正在停止生成...";
        await pywebview.api.abort_generation();
        return;
    }
    const text = inputBox.value.trim();
    if (!text && attachments.length === 0) return;
    
    if (!config.api_key || !config.api_key.trim()) {
        statusLabel.textContent = "请先配置 API Key！";
        showSettings();
        return;
    }

    // Clear box
    inputBox.value = "";
    inputBox.style.height = "auto";
    
    // 1. Add User bubble to layout
    let displayContent = text;
    if (attachments.length > 0) {
        displayContent += "\n[附件: " + attachments.map(a => a.name).join(", ") + "]";
    }
    const userMsgIndex = currentConv ? currentConv.messages.length : -1;
    appendMessage("user", displayContent, "", false, userMsgIndex);
    scrollChatBottom();

    // 2. Add empty Assistant placeholder for streaming response
    appendMessage("assistant", "思考中...", "", true);
    
    isStreaming = true;
    streamingText = "";
    streamingThinking = "";
    
    // Stop button active styling
    sendBtn.classList.add("stop-active");
    sendBtn.title = "停止生成";
    const sendIcon = sendBtn.querySelector(".send-icon");
    if (sendIcon) sendIcon.textContent = "■";

    statusLabel.textContent = "Claude 思考中...";

    // 3. Clear local attachments
    const oldAttachments = [...attachments];
    attachments = [];
    renderAttachments();

    // 4. Send API trigger
    await pywebview.api.send_message(currentConvId, text, oldAttachments);
}

// Trigger sends on clicks and enter
sendBtn.onclick = sendMessage;
inputBox.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.ctrlKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Stream callback evaluations evaluated by python thread
window.onStreamMessage = (type, data) => {
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
        // Finalize streaming bubble
        isStreaming = false;
        
        // Restore send button state
        sendBtn.classList.remove("stop-active");
        sendBtn.title = "发送 (Ctrl+Enter)";
        const sendIcon = sendBtn.querySelector(".send-icon");
        if (sendIcon) sendIcon.textContent = "↑";
        
        statusLabel.textContent = "就绪";
        
        // Remove stream identifiers
        const row = document.getElementById("streaming-msg-row");
        if (row) row.removeAttribute("id");
        if (body) body.removeAttribute("id");
        if (thinkContainer) thinkContainer.removeAttribute("id");
        
        // Update token counts labels
        tokenLabel.textContent = `Token: ${data.input_tokens} in / ${data.output_tokens} out`;
        
        // Reload conversations to update title card
        loadConversations();
        reloadCurrentConversation();
        
    } else if (type === "aborted") {
        isStreaming = false;
        
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
        
        loadConversations();
        reloadCurrentConversation();
        
    } else if (type === "error") {
        isStreaming = false;
        
        // Restore send button state
        sendBtn.classList.remove("stop-active");
        sendBtn.title = "发送 (Ctrl+Enter)";
        const sendIcon = sendBtn.querySelector(".send-icon");
        if (sendIcon) sendIcon.textContent = "↑";
        
        statusLabel.textContent = `错误: ${data}`;
        
        if (body) {
            body.innerHTML = `<span style="color: var(--red);">❌ 发生错误: ${data}</span>`;
        }
    }
};

// Select attachments
attachBtn.onclick = async () => {
    const selected = await pywebview.api.select_attachments();
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

// Auto-expand input textbox height
inputBox.addEventListener("input", () => {
    inputBox.style.height = "auto";
    inputBox.style.height = `${inputBox.scrollHeight}px`;
});

// Modal Logic
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

// Settings Modal
settingsBtn.onclick = showSettings;

function showSettings() {
    apiKeyInput.value = config.api_key || "";
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
    }
    
    // Update settings UI components dynamically based on model capabilities
    updateThinkingSettingsUI();
    
    // Render custom system prompts presets inside Settings dialog
    renderPresetsList();
    
    showModal(settingsModal);
}

// Handle settings thinking mode changes
document.querySelectorAll("input[name='thinking-mode']").forEach(radio => {
    radio.onchange = (e) => {
        updateThinkingSettingsUI();
    };
});

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

saveSettingsBtn.onclick = async () => {
    config.api_key = apiKeyInput.value.trim();
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
    }
    
    await pywebview.api.save_config(config);
    updateLedStatus();
    hideModal(settingsModal);
    statusLabel.textContent = "设置已保存";
};

// Log Viewer Modal logic
async function loadAndShowLogs() {
    const logs = await pywebview.api.get_logs();
    logsContent.textContent = logs;
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
    const success = await pywebview.api.clear_logs();
    if (success) {
        await loadAndShowLogs();
        statusLabel.textContent = "日志已清空";
    } else {
        statusLabel.textContent = "清空日志失败";
    }
};

copyLogsBtn.onclick = () => {
    copyText(logsContent.textContent);
    statusLabel.textContent = "📋 日志已成功复制到剪贴板";
};

// Packet Modal Logic
async function showPacketModal(convId, messageIndex) {
    if (!convId || messageIndex === -1) return;
    packetContent.textContent = "正在从数据库加载原始数据包...";
    showModal(packetModal);
    
    try {
        const result = await pywebview.api.get_message_packet(convId, messageIndex);
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
    const conv = await pywebview.api.load_conversation(currentConvId);
    if (!conv) return;
    currentConv = conv;
    messageList.innerHTML = "";
    conv.messages.forEach((msg, idx) => {
        appendMessage(msg.role, msg.content, msg.thinking, false, idx);
    });
    scrollChatBottom();
    
    if (config.auto_run_code) {
        autoRunLastAssistantCode();
    }
}

// Proxy Modal
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
    
    await pywebview.api.save_config(config);
    hideModal(proxyModal);
    statusLabel.textContent = "代理设置已更新";
};

// Delete Modal confirmation dialog
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
    
    await pywebview.api.delete_conversation(deleteTargetId);
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

// Custom Context Menu Elements
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
        const clipboardText = await pywebview.api.paste_from_clipboard();
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
// Drag and Drop Global Overlay Integration
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
    statusLabel.textContent = `正在读取文件: ${file.name}...`;
    
    const reader = new FileReader();
    reader.onload = async (event) => {
        const base64Data = event.target.result;
        const uploaded = await pywebview.api.upload_dropped_file(file.name, file.size, base64Data);
        if (uploaded) {
            attachments.push(uploaded);
            renderAttachments();
            statusLabel.textContent = `附件已添加: ${file.name}`;
            setTimeout(() => { if (statusLabel.textContent.startsWith("附件已添加")) statusLabel.textContent = "就绪"; }, 2000);
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
// System Prompts Presets Management
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
        await pywebview.api.save_config(config);
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
        
        await pywebview.api.save_config(config);
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
    await pywebview.api.save_config(config);
    renderPresetsList();
    renderSystemPromptSelect();
}

// ==========================================
// Artifacts Preview Sidebar Panel
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
        iframe.sandbox = "allow-scripts";
        
        artifactsPreviewContainer.appendChild(iframe);
        
        const doc = iframe.contentDocument || iframe.contentWindow.document;
        doc.open();
        doc.write(content);
        doc.close();
        
    } else if (type === "svg") {
        artifactsPreviewContainer.innerHTML = content;
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
                    securityLevel: 'loose'
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

// In-place User Message Editing
async function editUserMessage(msgIndex) {
    if (isStreaming) return;
    const msgRow = messageList.children[msgIndex];
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
    textarea.addEventListener("input", () => {
        textarea.style.height = "auto";
        textarea.style.height = textarea.scrollHeight + "px";
    });
    
    body.querySelector(".edit-cancel-btn").onclick = (e) => {
        e.stopPropagation();
        body.innerHTML = originalHTML;
        highlightCodeBlocks(msgRow);
    };
    
    body.querySelector(".edit-save-btn").onclick = async (e) => {
        e.stopPropagation();
        const newText = textarea.value.trim();
        if (!newText) return;
        
        while (messageList.children.length > msgIndex) {
            messageList.removeChild(messageList.lastChild);
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
        
        await pywebview.api.edit_and_resend(currentConvId, msgIndex, newText);
    };
}

// Branch Conversation
async function branchConversation(msgIndex) {
    if (isStreaming) return;
    statusLabel.textContent = "正在创建分支对话...";
    const newConv = await pywebview.api.branch_conversation(currentConvId, msgIndex);
    if (newConv) {
        await loadConversations();
        await selectConversation(newConv.id);
        statusLabel.textContent = `分支创建成功: ${newConv.title}`;
        setTimeout(() => { if (statusLabel.textContent.startsWith("分支创建成功")) statusLabel.textContent = "就绪"; }, 2000);
    } else {
        statusLabel.textContent = "分支创建失败";
    }
}

// Retry Assistant Response
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
    
    await pywebview.api.retry_message(currentConvId, msgIndex);
}

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
        consoleBox.style.maxHeight = "200px";
        consoleBox.style.overflowY = "auto";
        consoleBox.style.whiteSpace = "pre-wrap";
        consoleBox.style.wordBreak = "break-all";
        
        preElement.style.borderRadius = "8px 8px 0 0";
        preElement.parentNode.insertBefore(consoleBox, preElement.nextSibling);
    }
    
    consoleBox.innerHTML = `<span style="color: var(--yellow);">⚙️ 正在利用本地环境运行代码...</span>`;
    
    try {
        const result = await pywebview.api.execute_code_locally(code, lang);
        if (result.error) {
            consoleBox.innerHTML = `<span style="color: var(--red);">❌ 运行失败:</span>\n${result.error}`;
        } else {
            let outputHtml = "";
            if (result.stdout) {
                outputHtml += `<span style="color: var(--green);">[标准输出 (stdout)]</span>\n${result.stdout}\n`;
            }
            if (result.stderr) {
                outputHtml += `<span style="color: var(--red);">[错误输出 (stderr)]</span>\n${result.stderr}\n`;
            }
            if (result.exit_code !== 0) {
                outputHtml += `<span style="color: var(--red); font-weight: bold;">[程序退出，状态码: ${result.exit_code}]</span>`;
            } else if (!result.stdout && !result.stderr) {
                outputHtml += `<span style="color: var(--overlay0); font-style: italic;">[程序运行完毕，无输出]</span>`;
            }
            consoleBox.innerHTML = outputHtml;
        }
    } catch (e) {
        consoleBox.innerHTML = `<span style="color: var(--red);">❌ 运行出错:</span>\n${e}`;
    }
    scrollChatBottom();
}

// Auto Run Last Assistant Response Code Blocks
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

