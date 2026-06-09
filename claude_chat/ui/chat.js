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
