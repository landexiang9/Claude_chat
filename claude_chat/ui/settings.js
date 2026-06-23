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
    // 渲染各平台 API Key 已绑定或未绑定状态的面板
    for (const [key, item] of Object.entries(keyConfigs)) {
        item.setPending(false); // 重置 pending
        const hasKey = config[`has_${key}`];
        if (hasKey) {
            if (item.container) item.container.classList.add("hidden");
            if (item.statusContainer) item.statusContainer.classList.remove("hidden");
            if (item.input) item.input.value = "";
        } else {
            if (item.container) item.container.classList.remove("hidden");
            if (item.statusContainer) item.statusContainer.classList.add("hidden");
            if (item.input) item.input.value = "";
        }
    }

    if (deepseekApiUrlInput) {
        deepseekApiUrlInput.value = config.deepseek_api_url || "https://api.deepseek.com";
    }
    if (geminiApiUrlInput) {
        geminiApiUrlInput.value = config.gemini_api_url || "";
    }

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
    
    // 加载各平台参数值
    if (claudeTempSlider) {
        claudeTempSlider.value = config.temperature !== undefined ? config.temperature : 0.7;
        claudeTempLabelTitle.textContent = `Temperature: ${parseFloat(claudeTempSlider.value).toFixed(2)}`;
    }
    if (claudeMaxTokensInput) {
        claudeMaxTokensInput.value = config.max_tokens || 4096;
    }

    if (deepseekTempSlider) {
        deepseekTempSlider.value = config.deepseek_temperature !== undefined ? config.deepseek_temperature : 0.7;
        deepseekTempLabelTitle.textContent = `Temperature: ${parseFloat(deepseekTempSlider.value).toFixed(2)}`;
    }
    if (deepseekMaxTokensInput) {
        deepseekMaxTokensInput.value = config.deepseek_max_tokens || 4096;
    }

    if (geminiTempSlider) {
        geminiTempSlider.value = config.gemini_temperature !== undefined ? config.gemini_temperature : 0.7;
        geminiTempLabelTitle.textContent = `Temperature: ${parseFloat(geminiTempSlider.value).toFixed(2)}`;
    }
    if (geminiMaxTokensInput) {
        geminiMaxTokensInput.value = config.gemini_max_tokens || 4096;
    }

    // Claude 思维模式设置
    const claudeMode = config.thinking_enabled ? config.thinking_type : "disabled";
    document.querySelectorAll("input[name='claude-thinking-mode']").forEach(radio => {
        radio.checked = (radio.value === claudeMode);
    });
    if (claudeBudgetTokensInput) {
        claudeBudgetTokensInput.value = config.thinking_budget || 16000;
    }
    if (claudeThinkingLevelSelect) {
        claudeThinkingLevelSelect.value = config.thinking_level || "high";
    }

    // Gemini 思维模式设置
    if (geminiThinkingEnabledInput) {
        geminiThinkingEnabledInput.checked = !!config.gemini_thinking_enabled;
    }
    if (geminiBudgetTokensInput) {
        geminiBudgetTokensInput.value = config.gemini_thinking_budget || 1024;
    }
    if (geminiThinkingLevelSelect) {
        geminiThinkingLevelSelect.value = config.gemini_thinking_level || "high";
    }

    // Claude 网页搜索
    if (claudeEnableSearchInput) {
        claudeEnableSearchInput.checked = !!config.enable_web_search;
        if (claudeSearchGroup) {
            if (claudeEnableSearchInput.checked) {
                claudeSearchGroup.classList.remove("hidden");
            } else {
                claudeSearchGroup.classList.add("hidden");
            }
        }
    }
    if (claudeEnableFetchInput) {
        claudeEnableFetchInput.checked = config.enable_web_fetch !== false;
    }
    if (claudeSearchEngineSelect) {
        claudeSearchEngineSelect.value = config.web_search_engine || "google";
    }
    if (claudeWebPageParserSelect) {
        claudeWebPageParserSelect.value = config.web_page_parser || "local";
    }
    if (claudeWebFetchLimitInput) {
        claudeWebFetchLimitInput.value = config.web_fetch_limit || 15000;
    }
    toggleSearchKeyGroups("claude", claudeSearchEngineSelect ? claudeSearchEngineSelect.value : "google", claudeWebPageParserSelect ? claudeWebPageParserSelect.value : "local");

    // DeepSeek 网页搜索
    if (deepseekEnableSearchInput) {
        deepseekEnableSearchInput.checked = !!config.deepseek_enable_web_search;
        if (deepseekSearchGroup) {
            if (deepseekEnableSearchInput.checked) {
                deepseekSearchGroup.classList.remove("hidden");
            } else {
                deepseekSearchGroup.classList.add("hidden");
            }
        }
    }
    if (deepseekEnableFetchInput) {
        deepseekEnableFetchInput.checked = config.deepseek_enable_web_fetch !== false;
    }
    if (deepseekSearchEngineSelect) {
        deepseekSearchEngineSelect.value = config.deepseek_web_search_engine || "google";
    }
    if (deepseekWebPageParserSelect) {
        deepseekWebPageParserSelect.value = config.deepseek_web_page_parser || "local";
    }
    if (deepseekWebFetchLimitInput) {
        deepseekWebFetchLimitInput.value = config.deepseek_web_fetch_limit || 15000;
    }
    toggleSearchKeyGroups("deepseek", deepseekSearchEngineSelect ? deepseekSearchEngineSelect.value : "google", deepseekWebPageParserSelect ? deepseekWebPageParserSelect.value : "local");

    // Gemini 联网搜索
    if (geminiEnableSearchInput) {
        geminiEnableSearchInput.checked = !!config.gemini_enable_web_search;
    }

    // Gemini 代码沙盒
    if (geminiEnableCodeSandboxInput) {
        geminiEnableCodeSandboxInput.checked = !!config.gemini_enable_code_sandbox;
    }
    if (geminiCodeSandboxTypeSelect) {
        geminiCodeSandboxTypeSelect.value = config.gemini_code_sandbox_type || "local";
    }

    // 全局与服务器配置
    if (autoRunCodeInput) {
        autoRunCodeInput.checked = !!config.auto_run_code;
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

    updateThinkingSettingsUI();
    renderPresetsList();
    showModal(settingsModal);
}

// 密钥控件映射字典，用于统一进行加载、状态渲染、保存和清除
const keyConfigs = {
    "api_key": {
        input: apiKeyInput,
        toggle: toggleKeyVisibility,
        container: apiKeyInputContainer,
        statusContainer: apiKeyStatusContainer,
        changeBtn: changeKeyBtn,
        disconnectBtn: disconnectKeyBtn,
        getPending: () => clearApiKeyPending,
        setPending: (v) => { clearApiKeyPending = v; }
    },
    "tavily_api_key": {
        input: claudeTavilyKeyInput,
        toggle: toggleClaudeTavilyVisibility,
        container: claudeTavilyKeyInputContainer,
        statusContainer: claudeTavilyKeyStatusContainer,
        changeBtn: changeClaudeTavilyBtn,
        disconnectBtn: disconnectClaudeTavilyBtn,
        getPending: () => clearTavilyKeyPending,
        setPending: (v) => { clearTavilyKeyPending = v; }
    },
    "jina_api_key": {
        input: claudeJinaKeyInput,
        toggle: toggleClaudeJinaVisibility,
        container: claudeJinaKeyInputContainer,
        statusContainer: claudeJinaKeyStatusContainer,
        changeBtn: changeClaudeJinaBtn,
        disconnectBtn: disconnectClaudeJinaBtn,
        getPending: () => config.clear_jina_api_key || clearJinaKeyPending,
        setPending: (v) => { clearJinaKeyPending = v; }
    },
    "deepseek_api_key": {
        input: deepseekApiKeyInput,
        toggle: toggleDeepseekKeyVisibility,
        container: deepseekApiKeyInputContainer,
        statusContainer: deepseekApiKeyStatusContainer,
        changeBtn: changeDeepseekKeyBtn,
        disconnectBtn: disconnectDeepseekKeyBtn,
        getPending: () => clearDeepseekKeyPending,
        setPending: (v) => { clearDeepseekKeyPending = v; }
    },
    "deepseek_tavily_api_key": {
        input: deepseekTavilyKeyInput,
        toggle: toggleDeepseekTavilyVisibility,
        container: deepseekTavilyKeyInputContainer,
        statusContainer: deepseekTavilyKeyStatusContainer,
        changeBtn: changeDeepseekTavilyBtn,
        disconnectBtn: disconnectDeepseekTavilyBtn,
        getPending: () => clearDeepseekTavilyKeyPending,
        setPending: (v) => { clearDeepseekTavilyKeyPending = v; }
    },
    "deepseek_jina_api_key": {
        input: deepseekJinaKeyInput,
        toggle: toggleDeepseekJinaVisibility,
        container: deepseekJinaKeyInputContainer,
        statusContainer: deepseekJinaKeyStatusContainer,
        changeBtn: changeDeepseekJinaBtn,
        disconnectBtn: disconnectDeepseekJinaBtn,
        getPending: () => clearDeepseekJinaKeyPending,
        setPending: (v) => { clearDeepseekJinaKeyPending = v; }
    },
    "gemini_api_key": {
        input: geminiApiKeyInput,
        toggle: toggleGeminiKeyVisibility,
        container: geminiApiKeyInputContainer,
        statusContainer: geminiApiKeyStatusContainer,
        changeBtn: changeGeminiKeyBtn,
        disconnectBtn: disconnectGeminiKeyBtn,
        getPending: () => clearGeminiKeyPending,
        setPending: (v) => { clearGeminiKeyPending = v; }
    }
};

// API Key 状态与操作的局部状态 (已移至 state.js 中统一定义)

// 统一初始化所有密钥控件事件
function initAllKeyControls() {
    for (const [key, item] of Object.entries(keyConfigs)) {
        if (!item.input) continue;
        if (item.toggle) {
            item.toggle.onclick = () => {
                if (item.input.type === "password") {
                    item.input.type = "text";
                    item.toggle.textContent = "🔒";
                } else {
                    item.input.type = "password";
                    item.toggle.textContent = "👁";
                }
            };
        }
        if (item.changeBtn) {
            item.changeBtn.onclick = () => {
                if (item.container) item.container.classList.remove("hidden");
                if (item.statusContainer) item.statusContainer.classList.add("hidden");
            };
        }
        if (item.disconnectBtn) {
            item.disconnectBtn.onclick = () => {
                item.setPending(true);
                if (item.container) item.container.classList.remove("hidden");
                if (item.statusContainer) item.statusContainer.classList.add("hidden");
                item.input.value = "";
            };
        }
    }
}

// 平台温度滑块初始化
function initPlatformSliders() {
    const platforms = ["claude", "deepseek", "gemini"];
    platforms.forEach(plat => {
        const slider = document.getElementById(`${plat}-temp-slider`);
        const label = document.getElementById(`${plat}-temp-label-title`);
        if (slider && label) {
            slider.oninput = (e) => {
                label.textContent = `Temperature: ${parseFloat(e.target.value).toFixed(2)}`;
            };
        }
    });
}

// 网页搜索 Key 显示切换
function toggleSearchKeyGroups(platform, engine, parser) {
    if (platform === "claude") {
        if (claudeTavilyKeyGroup) {
            claudeTavilyKeyGroup.style.display = (engine === "tavily") ? "block" : "none";
        }
        if (claudeJinaKeyGroup) {
            claudeJinaKeyGroup.style.display = (engine === "jina" || parser === "jina") ? "block" : "none";
        }
    } else if (platform === "deepseek") {
        if (deepseekTavilyKeyGroup) {
            deepseekTavilyKeyGroup.style.display = (engine === "tavily") ? "block" : "none";
        }
        if (deepseekJinaKeyGroup) {
            deepseekJinaKeyGroup.style.display = (engine === "jina" || parser === "jina") ? "block" : "none";
        }
    }
}

// 初始化绑定事件监听器
initAllKeyControls();
initPlatformSliders();

// Claude Web Search Events
if (claudeEnableSearchInput) {
    claudeEnableSearchInput.onchange = () => {
        if (claudeEnableSearchInput.checked) {
            claudeSearchGroup.classList.remove("hidden");
        } else {
            claudeSearchGroup.classList.add("hidden");
        }
    };
}
if (claudeSearchEngineSelect) {
    claudeSearchEngineSelect.onchange = () => {
        toggleSearchKeyGroups("claude", claudeSearchEngineSelect.value, claudeWebPageParserSelect ? claudeWebPageParserSelect.value : "local");
    };
}
if (claudeWebPageParserSelect) {
    claudeWebPageParserSelect.onchange = () => {
        toggleSearchKeyGroups("claude", claudeSearchEngineSelect ? claudeSearchEngineSelect.value : "google", claudeWebPageParserSelect.value);
    };
}

// DeepSeek Web Search Events
if (deepseekEnableSearchInput) {
    deepseekEnableSearchInput.onchange = () => {
        if (deepseekEnableSearchInput.checked) {
            deepseekSearchGroup.classList.remove("hidden");
        } else {
            deepseekSearchGroup.classList.add("hidden");
        }
    };
}
if (deepseekSearchEngineSelect) {
    deepseekSearchEngineSelect.onchange = () => {
        toggleSearchKeyGroups("deepseek", deepseekSearchEngineSelect.value, deepseekWebPageParserSelect ? deepseekWebPageParserSelect.value : "local");
    };
}
if (deepseekWebPageParserSelect) {
    deepseekWebPageParserSelect.onchange = () => {
        toggleSearchKeyGroups("deepseek", deepseekSearchEngineSelect ? deepseekSearchEngineSelect.value : "google", deepseekWebPageParserSelect.value);
    };
}

// Claude Thinking Mode Events
document.querySelectorAll("input[name='claude-thinking-mode']").forEach(radio => {
    radio.onchange = () => {
        updateThinkingSettingsUI();
    };
});

// Gemini Thinking Enabled Event
if (geminiThinkingEnabledInput) {
    geminiThinkingEnabledInput.onchange = () => {
        updateThinkingSettingsUI();
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
    
    const activePlatform = config.active_platform || "claude";
    
    // 首先隐藏所有平台思维设置区域
    const claudeThinkingSection = document.querySelector("input[name='claude-thinking-mode']")?.closest(".form-group");
    if (claudeThinkingSection) claudeThinkingSection.classList.add("hidden");
    if (claudeBudgetGroup) claudeBudgetGroup.classList.add("hidden");
    if (claudeThinkingLevelGroup) claudeThinkingLevelGroup.classList.add("hidden");
    
    const geminiThinkingSection = geminiThinkingEnabledInput?.closest(".form-group");
    if (geminiThinkingSection) geminiThinkingSection.classList.add("hidden");
    if (geminiBudgetGroup) geminiBudgetGroup.classList.add("hidden");
    if (geminiThinkingLevelGroup) geminiThinkingLevelGroup.classList.add("hidden");
    
    if (activePlatform === "claude") {
        if (!caps.thinking_supported) return;
        if (claudeThinkingSection) claudeThinkingSection.classList.remove("hidden");
        
        const adaptiveRadio = document.querySelector("input[name='claude-thinking-mode'][value='adaptive']");
        const enabledRadio = document.querySelector("input[name='claude-thinking-mode'][value='enabled']");
        
        if (adaptiveRadio) {
            adaptiveRadio.disabled = !caps.adaptive_supported;
            adaptiveRadio.closest(".radio-label").style.opacity = caps.adaptive_supported ? "1" : "0.5";
        }
        if (enabledRadio) {
            enabledRadio.disabled = !caps.enabled_supported;
            enabledRadio.closest(".radio-label").style.opacity = caps.enabled_supported ? "1" : "0.5";
        }
        
        let checkedRadio = document.querySelector("input[name='claude-thinking-mode']:checked");
        if (checkedRadio && checkedRadio.disabled) {
            const disabledRadio = document.querySelector("input[name='claude-thinking-mode'][value='disabled']");
            if (disabledRadio) disabledRadio.checked = true;
            checkedRadio = disabledRadio;
        }
        
        const mode = checkedRadio ? checkedRadio.value : "disabled";
        
        if (mode === "disabled") {
            if (claudeBudgetGroup) claudeBudgetGroup.classList.add("hidden");
            if (claudeThinkingLevelGroup) claudeThinkingLevelGroup.classList.add("hidden");
        } else if (mode === "adaptive") {
            if (claudeBudgetGroup) claudeBudgetGroup.classList.add("hidden");
            if (caps.effort_levels && caps.effort_levels.length > 0) {
                if (claudeThinkingLevelGroup) claudeThinkingLevelGroup.classList.remove("hidden");
                populateThinkingLevels(claudeThinkingLevelSelect, caps.effort_levels, config.thinking_level);
            } else {
                if (claudeThinkingLevelGroup) claudeThinkingLevelGroup.classList.add("hidden");
            }
        } else if (mode === "enabled") {
            if (claudeBudgetGroup) claudeBudgetGroup.classList.remove("hidden");
            if (claudeThinkingLevelGroup) claudeThinkingLevelGroup.classList.add("hidden");
        }
    } else if (activePlatform === "gemini") {
        if (!caps.thinking_supported) return;
        if (geminiThinkingSection) geminiThinkingSection.classList.remove("hidden");
        
        if (geminiThinkingEnabledInput && geminiThinkingEnabledInput.checked) {
            if (caps.effort_levels && caps.effort_levels.length > 0) {
                if (geminiThinkingLevelGroup) geminiThinkingLevelGroup.classList.remove("hidden");
                if (geminiBudgetGroup) geminiBudgetGroup.classList.add("hidden");
                populateThinkingLevels(geminiThinkingLevelSelect, caps.effort_levels, config.gemini_thinking_level || "high");
            } else {
                if (geminiThinkingLevelGroup) geminiThinkingLevelGroup.classList.add("hidden");
                if (geminiBudgetGroup) geminiBudgetGroup.classList.remove("hidden");
            }
        } else {
            if (geminiBudgetGroup) geminiBudgetGroup.classList.add("hidden");
            if (geminiThinkingLevelGroup) geminiThinkingLevelGroup.classList.add("hidden");
        }
    }
}

function populateThinkingLevels(selectEl, levels, currentVal) {
    if (!selectEl) return;
    const levelLabels = {
        "low": "Low (低 - 快速且经济)",
        "medium": "Medium (中 - 平衡)",
        "high": "High (高 - 默认推荐)",
        "xhigh": "X-High (极高)",
        "max": "Max (最大级 - 最深思考)"
    };
    
    selectEl.innerHTML = "";
    
    levels.forEach(lvl => {
        const option = document.createElement("option");
        option.value = lvl;
        option.textContent = levelLabels[lvl] || lvl.toUpperCase();
        if (lvl === currentVal) option.selected = true;
        selectEl.appendChild(option);
    });
    
    if (!levels.includes(currentVal)) {
        if (levels.includes("high")) {
            selectEl.value = "high";
        } else if (levels.length > 0) {
            selectEl.value = levels[levels.length - 1];
        }
    }
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

saveSettingsBtn.onclick = async () => {
    // 1. 保存/清除各个 API Key 相关的配置（采用数据驱动的自动化机制）
    for (const [key, item] of Object.entries(keyConfigs)) {
        if (item.getPending()) {
            config[`clear_${key}`] = true;
            config[key] = "";
            item.setPending(false);
        } else {
            delete config[`clear_${key}`];
            const val = item.input ? item.input.value.trim() : "";
            if (val) {
                config[key] = val;
            } else {
                delete config[key];
            }
        }
    }

    if (deepseekApiUrlInput) {
        config.deepseek_api_url = deepseekApiUrlInput.value.trim() || "https://api.deepseek.com";
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
    
    // 保存 Token 验证字段
    if (serverTokenInput) {
        config.security_token = serverTokenInput.value.trim();
    }
    
    // 保存各平台温度滑块与 Max Tokens
    if (claudeTempSlider) {
        config.temperature = parseFloat(claudeTempSlider.value);
    }
    if (claudeMaxTokensInput) {
        config.max_tokens = parseInt(claudeMaxTokensInput.value) || 4096;
    }

    if (deepseekTempSlider) {
        config.deepseek_temperature = parseFloat(deepseekTempSlider.value);
    }
    if (deepseekMaxTokensInput) {
        config.deepseek_max_tokens = parseInt(deepseekMaxTokensInput.value) || 4096;
    }

    if (geminiTempSlider) {
        config.gemini_temperature = parseFloat(geminiTempSlider.value);
    }
    if (geminiMaxTokensInput) {
        config.gemini_max_tokens = parseInt(geminiMaxTokensInput.value) || 4096;
    }

    // Claude 思维模式保存
    const claudeThinkingModeRadio = document.querySelector("input[name='claude-thinking-mode']:checked");
    const claudeThinkingMode = claudeThinkingModeRadio ? claudeThinkingModeRadio.value : "disabled";
    config.thinking_enabled = (claudeThinkingMode !== "disabled");
    config.thinking_type = claudeThinkingMode;
    if (claudeBudgetTokensInput) {
        config.thinking_budget = parseInt(claudeBudgetTokensInput.value) || 16000;
    }
    if (claudeThinkingLevelSelect) {
        config.thinking_level = claudeThinkingLevelSelect.value || "high";
    }

    // Gemini 思维模式保存
    if (geminiThinkingEnabledInput) {
        config.gemini_thinking_enabled = geminiThinkingEnabledInput.checked;
    }
    if (geminiBudgetTokensInput) {
        config.gemini_thinking_budget = parseInt(geminiBudgetTokensInput.value) || 1024;
    }
    if (geminiThinkingLevelSelect) {
        config.gemini_thinking_level = geminiThinkingLevelSelect.value || "high";
    }

    // Claude 网页搜索
    if (claudeEnableSearchInput) {
        config.enable_web_search = claudeEnableSearchInput.checked;
    }
    if (claudeSearchEngineSelect) {
        config.web_search_engine = claudeSearchEngineSelect.value || "google";
    }
    if (claudeEnableFetchInput) {
        config.enable_web_fetch = claudeEnableFetchInput.checked;
    }
    if (claudeWebPageParserSelect) {
        config.web_page_parser = claudeWebPageParserSelect.value || "local";
    }
    if (claudeWebFetchLimitInput) {
        config.web_fetch_limit = parseInt(claudeWebFetchLimitInput.value) || 15000;
    }

    // DeepSeek 网页搜索
    if (deepseekEnableSearchInput) {
        config.deepseek_enable_web_search = deepseekEnableSearchInput.checked;
    }
    if (deepseekSearchEngineSelect) {
        config.deepseek_web_search_engine = deepseekSearchEngineSelect.value || "google";
    }
    if (deepseekEnableFetchInput) {
        config.deepseek_enable_web_fetch = deepseekEnableFetchInput.checked;
    }
    if (deepseekWebPageParserSelect) {
        config.deepseek_web_page_parser = deepseekWebPageParserSelect.value || "local";
    }
    if (deepseekWebFetchLimitInput) {
        config.deepseek_web_fetch_limit = parseInt(deepseekWebFetchLimitInput.value) || 15000;
    }

    // Gemini 联网搜索
    if (geminiEnableSearchInput) {
        config.gemini_enable_web_search = geminiEnableSearchInput.checked;
    }

    // Gemini 代码沙盒
    if (geminiEnableCodeSandboxInput) {
        config.gemini_enable_code_sandbox = geminiEnableCodeSandboxInput.checked;
    }
    if (geminiCodeSandboxTypeSelect) {
        config.gemini_code_sandbox_type = geminiCodeSandboxTypeSelect.value || "local";
    }

    // 全局与服务器设置保存
    if (autoRunCodeInput) {
        config.auto_run_code = autoRunCodeInput.checked;
    }
    if (fontModeSelect) {
        config.font_mode = fontModeSelect.value || "custom";
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
