// 初始化 Marked Markdown 解析配置
if (typeof marked !== 'undefined') {
    marked.setOptions({
        breaks: true,
        gfm: true
    });
}

function parseMarkdown(text) {
    // M-fix#16: 必须同时具备 marked + DOMPurify 才能安全返回 HTML。
    // 当 marked 已加载而 DOMPurify 因 CDN 失败/广告拦截缺失时,直接返回 marked.parse 会
    // 把内联原始 HTML 注入 innerHTML 造成 XSS,因此降级走转义路径而非返回原始解析结果。
    if ((typeof marked !== 'undefined') && (typeof DOMPurify !== 'undefined')) {
        const renderer = new marked.Renderer();
        // Markdown 语法照常渲染，但消息内嵌的原始 HTML 永远只作为文字展示。
        renderer.html = token => escapeHtml(typeof token === "string" ? token : (token?.text || ""));
        return DOMPurify.sanitize(marked.parse(String(text), { renderer }));
    }
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;")
        .replace(/\n/g, "<br>");
}

function renderMessageBody(body, text, useMarkdown) {
    if (useMarkdown) {
        body.innerHTML = parseMarkdown(text);
        return;
    }
    body.classList.add("plain-text");
    body.textContent = String(text);
}

function applyFontMode() {
    if (config && config.font_mode === "system") {
        document.body.classList.add("font-mode-system");
    } else {
        document.body.classList.remove("font-mode-system");
    }
}

function getActiveSearchPreference() {
    const platform = config.active_platform || "claude";
    if (platform === "claude") return { enabledKey: "enable_web_search", engineKey: "web_search_engine", label: "Claude" };
    if (platform === "deepseek") return { enabledKey: "deepseek_enable_web_search", engineKey: "deepseek_web_search_engine", label: "DeepSeek" };
    if (platform === "gemini") return { enabledKey: "gemini_enable_web_search", engineKey: null, label: "Gemini" };
    return null;
}

let disclosureControlCounter = 0;
function makeDisclosureHeaderAccessible(header, card) {
    if (!header || !card) return;
    const body = card.querySelector(".search-card-body");
    header.setAttribute("role", "button");
    header.setAttribute("tabindex", "0");
    header.setAttribute("aria-expanded", card.classList.contains("collapsed") ? "false" : "true");
    if (body) {
        if (!body.id) body.id = `disclosure-panel-${++disclosureControlCounter}`;
        header.setAttribute("aria-controls", body.id);
    }
    header.addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        header.click();
    });
    header.addEventListener("click", () => {
        requestAnimationFrame(() => {
            header.setAttribute("aria-expanded", card.classList.contains("collapsed") ? "false" : "true");
        });
    });
}

function updateSearchBtnUI() {
    if (!webSearchBtn) return;
    const preference = getActiveSearchPreference();
    if (!preference) {
        webSearchBtn.classList.remove("active");
        webSearchBtn.disabled = true;
        webSearchBtn.title = "当前自定义提供商不支持内置联网搜索开关";
        webSearchBtn.setAttribute("aria-pressed", "false");
        webSearchBtn.setAttribute("aria-label", webSearchBtn.title);
        return;
    }

    webSearchBtn.disabled = false;
    const enabled = !!config[preference.enabledKey];
    if (enabled) {
        webSearchBtn.classList.add("active");
        const engine = preference.engineKey ? (config[preference.engineKey] || "google") : "Google Search";
        webSearchBtn.title = `${preference.label} 联网搜索：开启 (${engine})`;
        webSearchBtn.setAttribute("aria-pressed", "true");
    } else {
        webSearchBtn.classList.remove("active");
        webSearchBtn.title = `${preference.label} 联网搜索：关闭`;
        webSearchBtn.setAttribute("aria-pressed", "false");
    }
    webSearchBtn.setAttribute("aria-label", webSearchBtn.title);
}

if (webSearchBtn) {
    webSearchBtn.onclick = async () => {
        if (isStreaming) {
            statusLabel.textContent = "正在生成中，无法切换联网搜索状态";
            return;
        }
        const preference = getActiveSearchPreference();
        if (!preference) {
            statusLabel.textContent = "当前提供商未提供内置联网搜索";
            return;
        }
        config[preference.enabledKey] = !config[preference.enabledKey];
        updateSearchBtnUI();
        await apiBridge.save_config({
            [preference.enabledKey]: config[preference.enabledKey]
        });
        statusLabel.textContent = config[preference.enabledKey] ? `${preference.label} 联网搜索已开启` : `${preference.label} 联网搜索已关闭`;
        setTimeout(() => { if (statusLabel.textContent.includes("联网搜索")) statusLabel.textContent = "就绪"; }, 1500);
    };
}
function updateLedStatus() {
    window.SelectPicker?.refreshAll();
    let hasKey = false;
    const platform = config.active_platform || "claude";
    if (platform === "claude") {
        hasKey = !!(config.has_api_key || (config.api_key && config.api_key.trim()));
    } else if (platform === "deepseek") {
        hasKey = !!(config.has_deepseek_api_key || (config.deepseek_api_key && config.deepseek_api_key.trim()));
    } else if (platform === "gemini") {
        hasKey = !!(config.has_gemini_api_key || (config.gemini_api_key && config.gemini_api_key.trim()));
    } else if (platform.startsWith("custom:")) {
        // 自定义提供商：后端在 get_config 时已注入 has_custom_<id>_api_key
        const pid = platform.split(":")[1];
        hasKey = !!(config[`has_custom_${pid}_api_key`] || config[`custom_${pid}_api_key`]);
    }
    
    if (hasKey) {
        apiStatusLed.className = "status-led online";
        apiStatusLed.title = `API 已连接 (${platform})`;
    } else {
        apiStatusLed.className = "status-led";
        apiStatusLed.title = `API 未连接 (${platform})`;
    }
    apiStatusLed.setAttribute("role", "status");
    apiStatusLed.setAttribute("aria-label", apiStatusLed.title);
}

function setSendButtonState(isGenerating) {
    if (!sendBtn) return;
    sendBtn.classList.toggle("stop-active", isGenerating);
    sendBtn.title = isGenerating ? "停止生成" : "发送 (Ctrl+Enter)";
    sendBtn.setAttribute("aria-label", isGenerating ? "停止生成" : "发送消息");
    sendBtn.setAttribute("aria-pressed", isGenerating ? "true" : "false");
    const icon = sendBtn.querySelector(".send-icon");
    if (icon) icon.textContent = isGenerating ? "■" : "↑";
}

function createSearchableModelPicker() {
    const requiredElements = [
        modelPickerTrigger, modelPickerValue, modelPickerPanel,
        modelSearchInput, modelPickerResults, modelPickerEmpty
    ];
    if (window.ModelPicker && requiredElements.every(Boolean)) {
        return window.ModelPicker.create({
            select: modelSelect,
            trigger: modelPickerTrigger,
            valueLabel: modelPickerValue,
            panel: modelPickerPanel,
            searchInput: modelSearchInput,
            results: modelPickerResults,
            emptyState: modelPickerEmpty
        });
    }
    // 渐进增强回退：搜索组件加载失败时仍保留原生模型下拉框。
    modelSelect.classList.remove("model-select-native");
    modelSelect.classList.add("select-menu");
    modelSelect.removeAttribute("tabindex");
    modelSelect.removeAttribute("aria-hidden");
    if (modelPickerTrigger) modelPickerTrigger.classList.add("hidden");
    return { setModels() {}, setSelected() {}, setStatus() {} };
}

const searchableModelPicker = createSearchableModelPicker();

function setModelListStatus(message) {
    modelSelect.innerHTML = "";
    const option = document.createElement("option");
    option.value = "";
    option.textContent = message;
    modelSelect.appendChild(option);
    searchableModelPicker.setStatus(message);
}

// 渲染下拉菜单中的模型选项列表
function updateModelList(models, selectedModelId = config.model, preserveSelected = false) {
    modelSelect.innerHTML = "";
    const displayModels = Array.isArray(models) ? [...models] : [];
    const containsSelected = displayModels.some(m => (typeof m === 'string' ? m : m.id) === selectedModelId);
    if (preserveSelected && selectedModelId && !containsSelected) {
        displayModels.unshift({ id: selectedModelId, display_name: `${selectedModelId}（历史会话）` });
    }
    if (displayModels.length === 0) {
        setModelListStatus("模型加载失败或无可用模型，请在设置中检查配置");
        return;
    }
    let hasSelected = false;
    displayModels.forEach(m => {
        const mId = typeof m === 'string' ? m : m.id;
        const mName = typeof m === 'string' ? m : (m.display_name || m.id);
        const option = document.createElement("option");
        option.value = mId;
        option.textContent = mName;
        if (mId === selectedModelId) {
            option.selected = true;
            hasSelected = true;
        }
        modelSelect.appendChild(option);
    });
    if (!hasSelected && displayModels.length > 0) {
        const firstId = typeof displayModels[0] === 'string' ? displayModels[0] : displayModels[0].id;
        config.model = firstId;
        modelSelect.value = firstId;
        apiBridge.save_config({ model: firstId });
        if (currentConvId) {
            const conv = conversations.find(c => c.id === currentConvId);
            if (conv) {
                conv.model = firstId;
            }
            if (currentConv && currentConv.id === currentConvId) {
                currentConv.model = firstId;
            }
        }
        if (window.updateModelSettingsUI) window.updateModelSettingsUI();
    }
    searchableModelPicker.setModels(displayModels, modelSelect.value);
}

// 绑定模型列表更新的全局回调函数
window.onModelsUpdated = (models, platform = null) => {
    const sourcePlatform = platform || config.active_platform || "claude";
    if (Array.isArray(models)) {
        modelCache.set(sourcePlatform, models);
    }
    if (sourcePlatform !== (config.active_platform || "claude")) return;
    availableModels = models;
    const selectedModel = currentConv && currentConv.model ? currentConv.model : config.model;
    updateModelList(models, selectedModel, Boolean(currentConv));
};

// 监听模型切换事件
modelSelect.addEventListener("change", async (e) => {
    const previousModel = config.model;
    config.model = e.target.value;
    searchableModelPicker.setSelected(config.model);
    
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
        if (currentConv && currentConv.id === currentConvId) {
            currentConv.model = config.model;
            if (modelObj && !modelObj.thinking_supported) currentConv.thinking = null;
        }
    }
    const saved = await apiBridge.save_config(config);
    if (!saved) {
        statusLabel.textContent = "模型切换保存失败";
        config.model = previousModel;
        modelSelect.value = previousModel;
        searchableModelPicker.setSelected(previousModel);
        if (currentConvId) {
            const conv = conversations.find(c => c.id === currentConvId);
            if (conv) conv.model = previousModel;
            if (currentConv && currentConv.id === currentConvId) currentConv.model = previousModel;
        }
        return;
    }
    if (window.updateModelSettingsUI) window.updateModelSettingsUI();
    statusLabel.textContent = `模型切换为: ${config.model}`;
});

let platformSwitchVersion = 0;
let platformSaveQueue = Promise.resolve();
let pendingPlatformSaves = 0;
let confirmedPlatform = null;

async function switchActivePlatform(nextPlatform) {
    const switchVersion = ++platformSwitchVersion;
    const previousPlatform = config.active_platform;
    if (pendingPlatformSaves === 0) confirmedPlatform = previousPlatform;
    pendingPlatformSaves += 1;
    config.active_platform = nextPlatform;
    if (platformSelect) platformSelect.value = nextPlatform;
    setModelListStatus("正在加载模型...");
    if (nextPlatform === "deepseek") {
        statusLabel.textContent = "已切换至 DeepSeek (本地文档解析与 OCR 提取生效)";
    } else if (nextPlatform.startsWith("custom:")) {
        const opt = platformSelect && platformSelect.selectedOptions && platformSelect.selectedOptions[0];
        const dispName = opt ? opt.textContent : nextPlatform;
        statusLabel.textContent = `已切换至自定义提供商: ${dispName}`;
    } else {
        statusLabel.textContent = `已切换至平台: ${nextPlatform}`;
    }
    const saveTask = platformSaveQueue.then(async () => {
        try {
            const result = await apiBridge.save_config({ active_platform: nextPlatform });
            if (result) confirmedPlatform = nextPlatform;
            return result;
        } finally {
            pendingPlatformSaves -= 1;
        }
    });
    platformSaveQueue = saveTask.catch(() => false);
    let saved = false;
    try {
        saved = await saveTask;
    } catch (error) {
        console.error("Platform switch save failed", error);
    }
    if (switchVersion !== platformSwitchVersion || config.active_platform !== nextPlatform) return false;
    if (!saved) {
        statusLabel.textContent = "平台切换保存失败";
        const restoredPlatform = confirmedPlatform || previousPlatform;
        config.active_platform = restoredPlatform;
        if (platformSelect) platformSelect.value = restoredPlatform;
        if (currentConv && currentConv.id === currentConvId) currentConv.platform = restoredPlatform;
        const summary = conversations.find(c => c.id === currentConvId);
        if (summary) summary.platform = restoredPlatform;
        updateLedStatus();
        updateSearchBtnUI();
        try {
            const restoredModels = modelCache.get(restoredPlatform) || await apiBridge.fetch_models(restoredPlatform);
            if (switchVersion !== platformSwitchVersion || config.active_platform !== restoredPlatform) return false;
            availableModels = Array.isArray(restoredModels) ? restoredModels : [];
            modelCache.set(restoredPlatform, availableModels);
            updateModelList(availableModels, config.model, true);
        } catch (error) {
            if (switchVersion === platformSwitchVersion && config.active_platform === restoredPlatform) {
                setModelListStatus("模型加载失败，请重新选择平台");
            }
        }
        return false;
    }
    if (currentConv && currentConv.id === currentConvId) {
        currentConv.platform = nextPlatform;
    }
    if (currentConvId) {
        const conv = conversations.find(c => c.id === currentConvId);
        if (conv) conv.platform = nextPlatform;
    }
    updateLedStatus();
    updateSearchBtnUI();
    let models;
    try {
        models = await apiBridge.fetch_models(nextPlatform);
    } catch (error) {
        if (switchVersion === platformSwitchVersion && config.active_platform === nextPlatform) {
            setModelListStatus("模型加载失败，请重新选择平台");
        }
        return false;
    }
    if (switchVersion !== platformSwitchVersion || config.active_platform !== nextPlatform) return false;
    if (Array.isArray(models)) {
        modelCache.set(nextPlatform, models);
        availableModels = models;
        updateModelList(models, config.model, false);
    } else {
        setModelListStatus("模型加载失败，请重新选择平台");
    }
    return true;
}

if (platformSelect) {
    platformSelect.addEventListener("change", async (e) => {
        await switchActivePlatform(e.target.value);
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
    const normalizedQuery = (conversationSearchInput?.value || "").trim().toLocaleLowerCase();
    const visibleConversations = conversations.filter(c => {
        if (!normalizedQuery) return true;
        return String(c.title || "新对话").toLocaleLowerCase().includes(normalizedQuery);
    });

    if (visibleConversations.length === 0) {
        const emptyState = document.createElement("div");
        emptyState.className = "conversation-empty";
        emptyState.textContent = normalizedQuery ? "没有找到匹配的对话" : "还没有历史对话";
        convList.appendChild(emptyState);
    }

    visibleConversations.forEach(c => {
        const item = document.createElement("div");
        const isActiveConversation = String(c.id) === String(currentConvId);
        item.className = `conv-item ${isActiveConversation ? "active" : ""}`;
        item.dataset.convId = String(c.id);
        item.setAttribute("aria-current", isActiveConversation ? "true" : "false");

        const selectBtn = document.createElement("button");
        selectBtn.className = "conv-select-btn";
        selectBtn.type = "button";
        selectBtn.setAttribute("aria-current", isActiveConversation ? "page" : "false");
        selectBtn.setAttribute("aria-label", `打开对话：${c.title || "新对话"}`);
        selectBtn.onclick = () => selectConversation(c.id);
        
        const title = document.createElement("span");
        title.className = "conv-title";
        title.textContent = c.title || "新对话";
        selectBtn.appendChild(title);
        item.appendChild(selectBtn);
        
        const delBtn = document.createElement("button");
        delBtn.className = "delete-conv-btn";
        delBtn.type = "button";
        delBtn.innerHTML = "&times;";
        delBtn.title = `删除对话：${c.title || "新对话"}`;
        delBtn.setAttribute("aria-label", delBtn.title);
        delBtn.onclick = (e) => {
            e.stopPropagation();
            showDeleteConfirm(c.id);
        };
        item.appendChild(delBtn);
        
        convList.appendChild(item);
    });

    const activeConversation = conversations.find(c => String(c.id) === String(currentConvId));
    if (currentConversationTitle) {
        currentConversationTitle.textContent = activeConversation?.title || currentConv?.title || "新对话";
    }
    document.title = activeConversation?.title
        ? `${activeConversation.title} · Claude Chat`
        : "Claude Chat · AI 工作台";
}

conversationSearchInput?.addEventListener("input", renderConversations);

function updateChatEmptyState() {
    if (!chatEmptyState || !messageList) return;
    chatEmptyState.classList.toggle("hidden", Boolean(messageList.querySelector(".message-row")));
}

if (messageList && chatEmptyState) {
    new MutationObserver(updateChatEmptyState).observe(messageList, { childList: true });
    updateChatEmptyState();
    chatEmptyState.querySelectorAll(".prompt-suggestion").forEach(button => {
        button.addEventListener("click", () => {
            inputBox.value = button.dataset.prompt || "";
            inputBox.dispatchEvent(new Event("input", { bubbles: true }));
            inputBox.focus();
        });
    });
}

function getAssistantDisplayName() {
    const platform = currentConv?.platform || config.active_platform || "claude";
    if (platform === "deepseek") return "DeepSeek";
    if (platform === "gemini") return "Gemini";
    if (platform === "claude") return "Claude";
    if (platform.startsWith("custom:")) {
        const selectedOption = Array.from(platformSelect?.options || []).find(option => option.value === platform);
        return selectedOption?.textContent || "Assistant";
    }
    return "Assistant";
}

function appendMessage(
    role,
    content,
    thinking,
    isStreamingPlaceholder = false,
    msgIndex = -1,
    toolCalls = null,
    targetContainer = messageList,
    renderMarkdown = true,
    isError = false
) {
    const displayContent = normalizeMessageDisplayContent(content, {
        extractAttachments: role === "user"
    });
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
    sender.dataset.role = role;
    sender.textContent = role === "user" ? "You" : getAssistantDisplayName();
    header.appendChild(sender);
    
    const meta = document.createElement("div");
    meta.className = "meta-info";
    
    const time = document.createElement("span");
    time.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    meta.appendChild(time);
    
    if (!isStreamingPlaceholder) {
        const copy = document.createElement("button");
        copy.className = "copy-btn";
        copy.type = "button";
        copy.textContent = "⧉";
        copy.title = "复制消息内容";
        copy.setAttribute("aria-label", copy.title);
        copy.onclick = () => copyText(messageContentForCopy(content, role));
        meta.appendChild(copy);

        if (msgIndex !== -1) {
            const packet = document.createElement("button");
            packet.className = "copy-btn";
            packet.style.marginLeft = "6px";
            packet.type = "button";
            packet.textContent = "{}";
            packet.title = "查看原始数据包";
            packet.setAttribute("aria-label", packet.title);
            packet.onclick = () => showPacketModal(currentConvId, msgIndex);
            meta.appendChild(packet);
            
            const branch = document.createElement("button");
            branch.className = "copy-btn";
            branch.style.marginLeft = "6px";
            branch.type = "button";
            branch.textContent = "⑂";
            branch.title = "从此消息创建分支对话";
            branch.setAttribute("aria-label", branch.title);
            branch.onclick = () => branchConversation(msgIndex);
            meta.appendChild(branch);
            
            if (role === "user") {
                const edit = document.createElement("button");
                edit.className = "copy-btn";
                edit.style.marginLeft = "6px";
                edit.type = "button";
                edit.textContent = "✎";
                edit.title = "编辑并重新发送";
                edit.setAttribute("aria-label", edit.title);
                edit.onclick = () => editUserMessage(msgIndex);
                meta.appendChild(edit);
            }
            
            if (role === "assistant" && currentConv && msgIndex === currentConv.messages.length - 1) {
                const retry = document.createElement("button");
                retry.className = "copy-btn";
                retry.style.marginLeft = "6px";
                retry.type = "button";
                retry.textContent = "↻";
                retry.title = "不满意，重新生成";
                retry.setAttribute("aria-label", retry.title);
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
        thinkToggle.type = "button";
        thinkToggle.setAttribute("aria-expanded", "false");
        const tokenEstimate = thinking ? Math.floor(thinking.length / 2) : 0;
        thinkToggle.textContent = `▶ 思考过程 (~${tokenEstimate} tokens)`;
        
        const thinkBox = document.createElement("div");
        thinkBox.className = "thinking-box hidden";
        thinkBox.textContent = thinking || "";
        
        thinkToggle.onclick = () => {
            const isHidden = thinkBox.classList.toggle("hidden");
            thinkToggle.setAttribute("aria-expanded", isHidden ? "false" : "true");
            thinkToggle.textContent = `${isHidden ? "▶" : "▼"} 思考过程 (~${Math.floor(thinkBox.textContent.length / 2)} tokens)`;
        };
        
        thinkContainer.appendChild(thinkToggle);
        thinkContainer.appendChild(thinkBox);
        card.appendChild(thinkContainer);
    }

    // 用户可逐条选择 Markdown 或纯文本；两种模式都不会执行原始 HTML。
    const body = document.createElement("div");
    body.className = "message-body";
    body.id = isStreamingPlaceholder ? "streaming-message-body" : "";
    
    if (isError) {
        body.classList.add("message-error", "plain-text");
        body.textContent = displayContent.text;
    } else if (displayContent.text) {
        renderMessageBody(body, displayContent.text, role !== "user" || renderMarkdown);
    } else {
        body.classList.add("hidden");
    }

    const attachmentCards = displayContent.attachments.length > 0
        ? renderMessageAttachmentCards(displayContent.attachments, {
            onPreview: (attachment, triggerElement) => {
                const isPendingPreview = attachment.previewScope === "pending";
                const previewContext = isPendingPreview
                    ? { scope: "pending" }
                    : {
                        scope: "message",
                        convId: currentConvId,
                        messageIndex: msgIndex,
                        blockIndex: attachment.blockIndex
                    };
                openAttachmentPreview(attachment, previewContext, triggerElement);
            }
        })
        : null;
    
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
                makeDisclosureHeaderAccessible(sHeader, searchCard);
                
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
                                <a href="${sUrl}" target="_blank" rel="noopener noreferrer">${sTitle}</a>
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
                makeDisclosureHeaderAccessible(sHeader, fetchCard);
                
                sBody.innerHTML = `
                    <div class="search-engine-info" style="margin-top: 0px; border-top: none; padding-top: 0px;">
                        <span>读取工具：</span>
                        <span class="search-engine-badge" style="background-color: var(--overlay0);">${parserName}</span>
                        <span style="font-size: 11.5px; color: var(--subtext0); margin-left: 8px;">${usageStr}</span>
                        <div style="font-size: 11px; color: var(--subtext0); word-break: break-all; margin-top: 4px;">URL: <a href="${safeUrl(tc.url)}" target="_blank" rel="noopener noreferrer" style="color: var(--blue); text-decoration: underline;">${escapeHtml(tc.url)}</a></div>  <!-- M-fix#15: 对齐流式卡,safeUrl 过滤 + escapeHtml 转义,防 javascript:/onerror XSS -->
                    </div>
                `;
                
                card.appendChild(fetchCard);
            }
        });
    }
    
    card.appendChild(body);
    if (attachmentCards) card.appendChild(attachmentCards);
    row.appendChild(card);
    targetContainer.appendChild(row);
    
    // 对代码块进行语法高亮并注入复制与保存操作头部
    highlightCodeBlocks(card);
    return row;
}
// 语法高亮代码块；对话默认附带操作栏，附件预览可只复用静态高亮。
function highlightCodeBlocks(container, options = {}) {
    const includeActions = options.includeActions !== false;
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
        
        if (includeActions && !pre.querySelector('.code-header')) {
            let lang = 'code';
            block.classList.forEach(cls => {
                if (cls.startsWith('language-')) {
                    lang = cls.replace('language-', '');
                }
            });
            
            let runBtnHtml = "";
            const normLang = lang.toLowerCase();
            if (normLang === 'python' || normLang === 'javascript' || normLang === 'js') {
                runBtnHtml = `<button type="button" class="code-run-btn" style="background-color: var(--green) !important; color: var(--crust) !important; border: none; border-radius: 4px; padding: 2px 8px; font-size: 11.5px; cursor: pointer; font-weight: 600; margin-right: 6px;">运行</button>`;
            }

            // 安全修复:语言标记来自模型可控的 markdown fence 信息串,必须先转义再入 innerHTML,
            // 否则 ```lang"><img src=x onerror=...> 会在净化后被拼入 DOM 造成存储型 XSS。
            const langLabel = escapeHtml(String(lang).toUpperCase()) || "CODE";
            const header = document.createElement('div');
            header.className = 'code-header';
            header.innerHTML = `
                <span>${langLabel}</span>
                <div class="code-header-actions">
                    ${runBtnHtml}
                    <button type="button" class="code-save-btn">保存为文件</button>
                    <button type="button" class="code-copy-btn">复制</button>
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
                showBtn.type = 'button';
                showBtn.textContent = '预览 Artifact';
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
let artifactRenderVersion = 0;

if (closeArtifactsBtn) {
    closeArtifactsBtn.onclick = () => {
        artifactsPanel.classList.add("collapsed");
        artifactRenderVersion += 1;
        // Removing the iframe also stops timers, media and scripts owned by the preview.
        clearArtifactPreview();
    };
}

if (artifactsFullscreenBtn) {
    artifactsFullscreenBtn.onclick = () => {
        const isFullscreen = artifactsPanel.classList.toggle("fullscreen");
        artifactsFullscreenBtn.textContent = isFullscreen ? "↙" : "⛶";
        artifactsFullscreenBtn.title = isFullscreen ? "还原" : "全屏";
        artifactsFullscreenBtn.setAttribute("aria-label", artifactsFullscreenBtn.title);
    };
}

artifactsTabBtns.forEach((btn, index) => {
    btn.onclick = () => {
        const tabName = btn.getAttribute("data-tab");
        artifactsTabBtns.forEach(b => {
            const isActive = b === btn;
            b.classList.toggle("active", isActive);
            b.setAttribute("aria-selected", isActive ? "true" : "false");
            b.setAttribute("tabindex", isActive ? "0" : "-1");
        });
        
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
    btn.addEventListener("keydown", event => {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        const direction = event.key === "ArrowRight" ? 1 : -1;
        const target = artifactsTabBtns[(index + direction + artifactsTabBtns.length) % artifactsTabBtns.length];
        target.focus();
        target.click();
    });
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

const ARTIFACT_IFRAME_PERMISSIONS = [
    "camera 'none'",
    "microphone 'none'",
    "geolocation 'none'",
    "payment 'none'",
    "usb 'none'",
    "serial 'none'",
    "clipboard-read 'none'",
    "clipboard-write 'none'",
    "display-capture 'none'"
].join("; ");

function clearArtifactPreview() {
    if (!artifactsPreviewContainer) return;
    artifactsPreviewContainer.replaceChildren();
    artifactsPreviewContainer.classList.remove("svg-mode", "mermaid-mode");
}

function createArtifactIframe(content, { interactive = false, title = "Artifact preview" } = {}) {
    const iframe = document.createElement("iframe");
    iframe.title = title;
    iframe.style.width = "100%";
    iframe.style.height = "100%";
    iframe.style.border = "none";
    iframe.style.backgroundColor = "#ffffff";
    iframe.referrerPolicy = "no-referrer";
    iframe.setAttribute("allow", ARTIFACT_IFRAME_PERMISSIONS);

    // Never grant allow-same-origin: srcdoc would otherwise inherit the application origin and
    // could read the parent page's storage. Interactive HTML keeps its existing client-side
    // capabilities, while SVG is rendered as a script-free document.
    iframe.setAttribute(
        "sandbox",
        interactive ? "allow-scripts allow-forms allow-modals allow-popups" : ""
    );
    iframe.srcdoc = content;
    return iframe;
}

function showArtifactPreviewMessage(message, color = "var(--red)") {
    clearArtifactPreview();
    const messageEl = document.createElement("span");
    messageEl.style.color = color;
    messageEl.textContent = message;
    artifactsPreviewContainer.appendChild(messageEl);
}

function renderArtifactPreview(content, type) {
    const renderVersion = ++artifactRenderVersion;
    clearArtifactPreview();
    
    if (type === "html" || type === "xml") {
        artifactsPreviewContainer.appendChild(createArtifactIframe(content, {
            interactive: true,
            title: `${type.toUpperCase()} Artifact preview`
        }));
        
    } else if (type === "svg") {
        if (typeof DOMPurify === "undefined") {
            showArtifactPreviewMessage("安全净化组件未加载，已拒绝预览 SVG。");
            return;
        }

        const sanitizedSvg = DOMPurify.sanitize(content, {
            USE_PROFILES: { svg: true, svgFilters: true },
            FORBID_TAGS: ["script", "foreignobject"]
        });
        const svgDocument = `<!doctype html>
<html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">
<style>html,body{margin:0;min-height:100%;display:grid;place-items:center;background:#fff}svg{max-width:100%;height:auto}</style>
</head><body>${sanitizedSvg}</body></html>`;
        artifactsPreviewContainer.classList.add("svg-mode");
        artifactsPreviewContainer.appendChild(createArtifactIframe(svgDocument, {
            interactive: false,
            title: "SVG Artifact preview"
        }));
        
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
                const renderResult = mermaid.init(undefined, `#${uniqueId}`);
                if (renderResult && typeof renderResult.catch === "function") {
                    renderResult.catch((err) => {
                        if (renderVersion === artifactRenderVersion) {
                            showArtifactPreviewMessage(`Mermaid 渲染错误: ${err.message || String(err)}`);
                        }
                    });
                }
            } catch (err) {
                showArtifactPreviewMessage(`Mermaid 渲染错误: ${err.message || String(err)}`);
            }
        } else {
            showArtifactPreviewMessage("Mermaid 库未加载，无法预览图表", "var(--yellow)");
        }
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
                showModal(imageModal);
            }
        }
    });
}

function closeImagePreview() {
    hideModal(imageModal);
    previewImageEl.src = "";
}

if (closeImageModalBtn) {
    closeImageModalBtn.addEventListener("click", closeImagePreview);
}

if (imageModal) {
    imageModal.addEventListener("click", (e) => {
        // Close if clicking outside the image content
        if (e.target === imageModal) closeImagePreview();
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
