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
    if (useCdnAssetsInput) {
        useCdnAssetsInput.checked = !!config.use_cdn_assets;
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
    if (useCdnAssetsInput) {
        config.use_cdn_assets = useCdnAssetsInput.checked;
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
