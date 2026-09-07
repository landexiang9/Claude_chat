// 打开附件选择对话框
attachBtn.onclick = async () => {
    attachBtn.disabled = true;
    try {
        const selected = await apiBridge.select_attachments();
        if (Array.isArray(selected) && selected.length > 0) {
            attachments = [...attachments, ...selected];
            renderAttachments();
            statusLabel.textContent = `已添加 ${selected.length} 个附件`;
        }
        if (selected?.uploadErrors?.length) {
            statusLabel.textContent = `部分文件添加失败: ${selected.uploadErrors.join("；")}`;
        }
    } catch (error) {
        console.error("选择附件失败:", error);
        statusLabel.textContent = `文件添加失败: ${error.message || String(error)}`;
    } finally {
        attachBtn.disabled = false;
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
        const card = createAttachmentCard(att, {
            mode: "composer",
            onPreview: (attachment, triggerElement) => openAttachmentPreview(
                attachment,
                { scope: "pending" },
                triggerElement
            ),
            onRemove: (attachment) => {
                attachments.splice(index, 1);
                renderAttachments();
                if (attachment.previewId) {
                    void apiBridge.discard_pending_attachment(attachment.previewId).catch(error => {
                        console.warn("清理未发送附件失败:", error);
                    });
                }
            }
        });
        card.setAttribute("role", "listitem");
        attachmentsArea.appendChild(card);
    });
}

// 监听输入，根据文本内容自动拉伸输入框高度
inputBox.addEventListener("input", () => {
    inputBox.style.height = "auto";
    inputBox.style.height = `${inputBox.scrollHeight}px`;
});

function clipboardFiles(clipboardData) {
    if (!clipboardData) return [];
    const files = Array.from(clipboardData.files || []);
    if (files.length > 0) return files;
    return Array.from(clipboardData.items || [])
        .filter(item => item.kind === "file")
        .map(item => item.getAsFile())
        .filter(Boolean);
}

function pastedFileName(file, index) {
    if (typeof file.name === "string" && file.name.trim()) return file.name.trim();
    const extensionByType = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
    };
    const extension = extensionByType[file.type] || "png";
    return `粘贴图片-${Date.now()}-${index + 1}.${extension}`;
}

function insertPastedText(textarea, text) {
    if (!text) return;
    const start = Number.isInteger(textarea.selectionStart) ? textarea.selectionStart : textarea.value.length;
    const end = Number.isInteger(textarea.selectionEnd) ? textarea.selectionEnd : start;
    textarea.value = textarea.value.slice(0, start) + text + textarea.value.slice(end);
    const nextPosition = start + text.length;
    textarea.selectionStart = textarea.selectionEnd = nextPosition;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

async function addNativeClipboardAttachments() {
    if (!checkIsNative()) return 0;
    try {
        const result = await apiBridge.paste_attachments_from_clipboard();
        const added = Array.isArray(result) ? result : (result?.attachments || []);
        const errors = Array.isArray(result?.errors) ? result.errors : [];
        if (added.length > 0) {
            attachments = [...attachments, ...added];
            renderAttachments();
            statusLabel.textContent = `已从剪贴板添加 ${added.length} 个附件`;
        } else if (errors.length > 0) {
            statusLabel.textContent = `剪贴板附件添加失败: ${errors.join("；")}`;
        }
        if (added.length > 0 && errors.length > 0) {
            statusLabel.textContent = `已添加 ${added.length} 个附件，部分失败: ${errors.join("；")}`;
        }
        return added.length;
    } catch (error) {
        console.error("读取原生剪贴板附件失败:", error);
        statusLabel.textContent = `剪贴板附件添加失败: ${error.message || String(error)}`;
        return 0;
    }
}

// 浏览器能提供 File 时直接上传；pywebview 缺失 File 时改由 Python 读取 Windows 剪贴板。
inputBox.addEventListener("paste", async event => {
    const files = clipboardFiles(event.clipboardData);
    const clipboardText = event.clipboardData?.getData("text/plain") || "";
    if (files.length > 0) {
        event.preventDefault();
        insertPastedText(inputBox, clipboardText);
        statusLabel.textContent = `正在粘贴 ${files.length} 个附件...`;
        for (let index = 0; index < files.length; index++) {
            await handleDroppedFile(files[index], pastedFileName(files[index], index));
        }
        inputBox.focus();
        return;
    }

    if (!checkIsNative()) return;
    // 图片/文件剪贴板通常没有文本，立即阻止 WebView2 的空粘贴行为。
    if (!clipboardText) event.preventDefault();
    await addNativeClipboardAttachments();
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
    const preserveReadingPosition = typeof chatShouldFollowLatest !== "undefined" && !chatShouldFollowLatest;
    const previousScrollTop = chatViewport.scrollTop;
    let readingAnchor = null;
    if (preserveReadingPosition) {
        const viewportTop = chatViewport.getBoundingClientRect().top;
        const visibleRow = Array.from(messageList.querySelectorAll(".message-row[data-msg-index]"))
            .find(row => row.getBoundingClientRect().bottom >= viewportTop);
        if (visibleRow) {
            readingAnchor = {
                messageIndex: visibleRow.dataset.msgIndex,
                offset: visibleRow.getBoundingClientRect().top - viewportTop
            };
        }
    }
    currentConv = conv;
    messageList.innerHTML = "";

    const messages = conv.messages || [];
    renderConversationMessages(messages);  // M-fix#28: 与 selectConversation 共用合并逻辑,保证视图一致
    let restoredReadingPosition = false;
    if (readingAnchor) {
        const restoredRow = Array.from(messageList.querySelectorAll(".message-row[data-msg-index]"))
            .find(row => row.dataset.msgIndex === readingAnchor.messageIndex);
        if (restoredRow) {
            const viewportTop = chatViewport.getBoundingClientRect().top;
            chatViewport.scrollTop += restoredRow.getBoundingClientRect().top - viewportTop - readingAnchor.offset;
            updateChatFollowState();
            restoredReadingPosition = true;
        }
    }
    if (!restoredReadingPosition && preserveReadingPosition) {
        chatViewport.scrollTop = Math.min(previousScrollTop, Math.max(0, chatViewport.scrollHeight - chatViewport.clientHeight));
        updateChatFollowState();
    } else if (!preserveReadingPosition) {
        scrollChatBottom();
    }
    
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

    // M-fix#5/#6: 流式生成中拒绝删除/切换,防止 reader 线程响应写入目标被改动导致丢消息/写错对话。
    if (isStreaming) {
        statusLabel.textContent = "生成中无法删除对话";
        hideModal(deleteModal);
        return;
    }

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
    // 移动端/触摸设备通常依靠原生菜单选择和修改文本，自定义菜单容易干扰操作且无法在 HTTP 协议下访问系统剪贴板
    const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || 
                     (navigator.maxTouchPoints > 0);
    if (isMobile) {
        hideContextMenu();
        return;
    }

    const target = e.target;
    
    // Check if target is a text editable field
    const isTextInput = target.tagName === "TEXTAREA" || 
                        (target.tagName === "INPUT" && 
                         ["text", "password", "number", "url"].includes(target.type));
    
    // 浏览器环境下（非 Native 模式），输入框建议使用原生上下文菜单，以便顺畅且安全地使用浏览器原生剪贴板机制
    if (isTextInput && !checkIsNative()) {
        hideContextMenu();
        return;
    }

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
        if (contextMenuTarget === inputBox) {
            await addNativeClipboardAttachments();
        }
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

async function handleDroppedFile(file, displayName = file.name) {
    const maxSize = 20 * 1024 * 1024; // 20MB
    if (file.size > maxSize) {
        statusLabel.textContent = `文件添加失败: ${displayName} (超过 20MB 限制)`;
        return;
    }
    statusLabel.textContent = `正在读取文件: ${displayName}...`;
    
    try {
        const base64Data = await readFileAsBase64(file);
        const uploaded = await apiBridge.upload_dropped_file(displayName, file.size, base64Data);
        if (uploaded && !uploaded.error) {
            attachments.push(uploaded);
            renderAttachments();
            if (config.active_platform === "deepseek") {
                statusLabel.textContent = `附件已添加: ${displayName} (已开启本地文档解析与图像 OCR 提取)`;
            } else {
                statusLabel.textContent = `附件已添加: ${displayName}`;
            }
            setTimeout(() => { if (statusLabel.textContent.startsWith("附件已添加")) statusLabel.textContent = "就绪"; }, 3000);
        } else {
            statusLabel.textContent = `文件添加失败: ${displayName}（${uploaded?.error || "上传失败"}）`;
        }
    } catch (error) {
        console.error("拖入附件失败:", error);
        statusLabel.textContent = `文件读取失败: ${displayName}（${error.message || String(error)}）`;
    }
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
        presetModalTitle.textContent = "编辑系统提示词预设";
        const p = config.system_prompts.find(x => x.id === id);
        presetNameInput.value = p ? p.name : "";
        presetContentInput.value = p ? p.content : "";
    } else {
        presetModalTitle.textContent = "添加系统提示词预设";
        presetNameInput.value = "";
        presetContentInput.value = "";
    }
    showModal(presetModal);
}

function hidePresetEditor() {
    hideModal(presetModal);
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

    const setConsoleStatus = (message, color, detail = "") => {
        outputDiv.replaceChildren();
        const statusSpan = document.createElement("span");
        statusSpan.style.color = color;
        statusSpan.textContent = message;
        outputDiv.appendChild(statusSpan);
        if (detail) outputDiv.appendChild(document.createTextNode(`\n${detail}`));
        outputDiv.appendChild(document.createTextNode("\n"));
    };

    setConsoleStatus("⚙️ 正在启动安全代码沙盒...", "var(--yellow)");
    
    try {
        const result = await apiBridge.start_code_execution(code, lang);
        if (result.error) {
            setConsoleStatus("❌ 启动失败:", "var(--red)", String(result.error));
            if (inputBar) inputBar.style.display = "none";
        } else {
            const procId = result.process_id;
            const timeoutText = result.timeout_seconds ? `，最长 ${result.timeout_seconds} 秒` : "";
            setConsoleStatus(
                `[${result.backend || "安全沙盒"} 已启动${timeoutText}，正在运行...]`,
                "var(--green)"
            );
            
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
        setConsoleStatus("❌ 启动出错:", "var(--red)", String(e));
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
