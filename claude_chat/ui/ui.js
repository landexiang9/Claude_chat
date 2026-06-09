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
