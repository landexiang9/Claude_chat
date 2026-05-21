// Global State
let config = {};
let conversations = [];
let currentConvId = null;
let attachments = [];
let isStreaming = false;
let streamingText = "";
let streamingThinking = "";

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
    
    // 2. Load and configure model dropdown
    const models = await pywebview.api.fetch_models();
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
        const option = document.createElement("option");
        option.value = m;
        option.textContent = m;
        if (m === config.model) option.selected = true;
        modelSelect.appendChild(option);
    });
}

// Bind models updated callback
window.onModelsUpdated = (models) => {
    updateModelList(models);
};

// Model change listener
modelSelect.addEventListener("change", async (e) => {
    config.model = e.target.value;
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
    conv.messages.forEach(msg => {
        appendMessage(msg.role, msg.content, msg.thinking);
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
function appendMessage(role, content, thinking, isStreamingPlaceholder = false) {
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
        copy.onclick = () => copyText(typeof content === 'string' ? content : JSON.stringify(content));
        meta.appendChild(copy);
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

// Highlight code blocks and inject custom header copy button
function highlightCodeBlocks(container) {
    container.querySelectorAll('pre code').forEach((block) => {
        const pre = block.parentNode;
        if (!pre.querySelector('.code-header')) {
            let lang = 'code';
            block.classList.forEach(cls => {
                if (cls.startsWith('language-')) {
                    lang = cls.replace('language-', '');
                }
            });
            
            const header = document.createElement('div');
            header.className = 'code-header';
            header.innerHTML = `
                <span>${lang.toUpperCase()}</span>
                <button class="code-copy-btn">复制</button>
            `;
            // Add copy action
            header.querySelector('.code-copy-btn').onclick = () => {
                copyText(block.innerText);
            };
            pre.insertBefore(header, block);
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
    if (isStreaming) return;
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
    appendMessage("user", displayContent);
    scrollChatBottom();

    // 2. Add empty Assistant placeholder for streaming response
    appendMessage("assistant", "思考中...", "", true);
    
    isStreaming = true;
    streamingText = "";
    streamingThinking = "";
    sendBtn.disabled = true;
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
        sendBtn.disabled = false;
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
        
    } else if (type === "error") {
        isStreaming = false;
        sendBtn.disabled = false;
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
    toggleBudgetGroup(mode);
    
    showModal(settingsModal);
}

// Handle settings budget show/hide
document.querySelectorAll("input[name='thinking-mode']").forEach(radio => {
    radio.onchange = (e) => {
        toggleBudgetGroup(e.target.value);
    };
});

function toggleBudgetGroup(mode) {
    if (mode === "disabled") {
        budgetGroup.classList.add("hidden");
    } else {
        budgetGroup.classList.remove("hidden");
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
    
    await pywebview.api.save_config(config);
    updateLedStatus();
    hideModal(settingsModal);
    statusLabel.textContent = "设置已保存";
};

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

