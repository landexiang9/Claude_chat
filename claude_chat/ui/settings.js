// 弹窗显示/隐藏控制逻辑
const DEFAULT_MODEL_MAX_TOKENS = 16384;
const modalReturnFocus = new WeakMap();

function getModalFocusableElements(modal) {
    return Array.from(modal?.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    ) || []).filter(element => element.offsetParent !== null);
}

function showModal(modal) {
    if (!modal) return;
    modalReturnFocus.set(modal, document.activeElement);
    window.SelectPicker?.refreshAll();
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => {
        const preferred = modal.querySelector('[autofocus]') || getModalFocusableElements(modal)[0];
        preferred?.focus({ preventScroll: true });
    });
}

function hideModal(modal) {
    if (!modal) return;
    window.SelectPicker?.closeAll();
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    const returnTarget = modalReturnFocus.get(modal);
    if (returnTarget && document.contains(returnTarget)) {
        returnTarget.focus({ preventScroll: true });
    }
}

document.querySelectorAll(".close-modal-btn, .cancel-modal-btn").forEach(btn => {
    btn.onclick = (e) => {
        const modal = e.target.closest(".modal-overlay");
        hideModal(modal);
    };
});

document.querySelectorAll(".modal-overlay").forEach((modal, index) => {
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-hidden", modal.classList.contains("hidden") ? "true" : "false");
    const heading = modal.querySelector(".modal-header h2");
    if (heading) {
        if (!heading.id) heading.id = `dialog-title-${index + 1}`;
        modal.setAttribute("aria-labelledby", heading.id);
    }
});

document.addEventListener("keydown", event => {
    const authOverlay = document.getElementById("auth-overlay");
    if (authOverlay && document.body.contains(authOverlay)) return;
    if (attachmentPreviewModal && !attachmentPreviewModal.classList.contains("hidden")) return;
    const visibleModals = Array.from(document.querySelectorAll(".modal-overlay:not(.hidden)"))
        .filter(modal => modal.id !== "auth-overlay");
    const modal = visibleModals[visibleModals.length - 1];
    if (!modal) return;

    if (event.key === "Escape") {
        event.preventDefault();
        hideModal(modal);
        return;
    }
    if (event.key !== "Tab") return;
    const focusable = getModalFocusableElements(modal);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!modal.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
});

const settingsNavItems = Array.from(document.querySelectorAll(".settings-nav-item[data-settings-nav]"));
const settingsSections = Array.from(document.querySelectorAll("[data-settings-section]"));
const settingsContent = document.querySelector(".settings-content");

function activateSettingsNav(sectionKey, shouldScroll = true) {
    settingsNavItems.forEach(item => {
        const active = item.dataset.settingsNav === sectionKey;
        item.classList.toggle("active", active);
        item.setAttribute("aria-current", active ? "page" : "false");
    });
    if (shouldScroll) {
        document.querySelector(`[data-settings-section="${sectionKey}"]`)?.scrollIntoView({
            behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth",
            block: "start"
        });
    }
}

settingsNavItems.forEach((item, index) => {
    item.addEventListener("click", () => activateSettingsNav(item.dataset.settingsNav));
    item.addEventListener("keydown", event => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp" && event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        const direction = (event.key === "ArrowDown" || event.key === "ArrowRight") ? 1 : -1;
        const target = settingsNavItems[(index + direction + settingsNavItems.length) % settingsNavItems.length];
        target.focus();
        activateSettingsNav(target.dataset.settingsNav);
    });
});

let settingsScrollFrame = null;
settingsContent?.addEventListener("scroll", () => {
    if (settingsScrollFrame !== null) cancelAnimationFrame(settingsScrollFrame);
    settingsScrollFrame = requestAnimationFrame(() => {
        const contentTop = settingsContent.getBoundingClientRect().top;
        const atBottom = settingsContent.scrollHeight - settingsContent.scrollTop - settingsContent.clientHeight <= 4;
        let activeSection = settingsSections[0];
        if (atBottom) {
            activeSection = settingsSections[settingsSections.length - 1];
        } else {
            const activationLine = contentTop + 36;
            settingsSections.forEach(section => {
                if (section.getBoundingClientRect().top <= activationLine) activeSection = section;
            });
        }
        if (activeSection) activateSettingsNav(activeSection.dataset.settingsSection, false);
        settingsScrollFrame = null;
    });
}, { passive: true });


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
    window.ModelConfigEditor?.reset();
    // 同时检测真实沙盒后端；失败时保持开关禁用，并显示后端写入日志的同一原因。
    refreshCodeSandboxStatus();

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

    // 刷新自定义提供商管理列表
    onSettingsOpenRefreshCustomProviders();

    PlatformSettings.loadAll(config);

    // 默认展示当前 active 平台的 Tab 和面板
    const currentPlatform = config.active_platform || "claude";
    updateOcrNotices(currentPlatform);
    const tabButtons = document.querySelectorAll(".platform-tabs .tab-btn");
    const tabPanels = document.querySelectorAll(".platform-panel");
    tabButtons.forEach(btn => {
        const isActiveTab = btn.getAttribute("data-platform-tab") === currentPlatform;
        btn.setAttribute("aria-selected", isActiveTab ? "true" : "false");
        btn.setAttribute("aria-controls", `platform-panel-${btn.getAttribute("data-platform-tab")}`);
        btn.setAttribute("tabindex", isActiveTab ? "0" : "-1");
        if (isActiveTab) {
            btn.classList.add("active");
            btn.style.background = "var(--surface0)";
            btn.style.color = "var(--text)";
        } else {
            btn.classList.remove("active");
            btn.style.background = "transparent";
            btn.style.color = "var(--subtext0)";
        }
    });
    // 自定义平台没有内置 panel：隐藏全部内置面板，并自动进入该供应商的编辑界面
    const isCustom = currentPlatform.startsWith("custom:");
    tabPanels.forEach(panel => {
        if (!isCustom && panel.id === `platform-panel-${currentPlatform}`) {
            panel.classList.remove("hidden");
        } else {
            panel.classList.add("hidden");
        }
    });
    if (isCustom) {
        // 取消所有内置 tab 的高亮（自定义平台不属于任何内置 tab）
        tabButtons.forEach(btn => {
            btn.classList.remove("active");
            btn.setAttribute("aria-selected", "false");
            btn.setAttribute("tabindex", "-1");
            btn.style.background = "transparent";
            btn.style.color = "var(--subtext0)";
        });
        const customPid = currentPlatform.split(":")[1];
        // 等待供应商列表刷新完成后自动进入编辑
        onSettingsOpenRefreshCustomProviders().then(() => {
            startEditCustomProvider(customPid);
            // 滚动到自定义提供商管理区
            const cpSection = document.querySelector("#custom-providers-list");
            if (cpSection && cpSection.scrollIntoView) {
                cpSection.scrollIntoView({ behavior: "smooth", block: "center" });
            }
        });
    }

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
    
        // 更新当前模型的专属设置UI
    updateModelSettingsUI();
    // 全局与服务器配置
    if (enableCodeSandboxInput) {
        enableCodeSandboxInput.checked = !!config.enable_code_sandbox;
    }
    if (autoRunCodeInput) {
        autoRunCodeInput.checked = !!config.auto_run_code;
    }
    if (codeSandboxTimeoutInput) {
        codeSandboxTimeoutInput.value = normalizeSandboxTimeout(config.code_sandbox_timeout);
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

    renderPresetsList();
    activateSettingsNav(isCustom ? "custom" : "providers", false);
    window.SelectPicker?.refreshAll();
    showModal(settingsModal);
    requestAnimationFrame(() => {
        if (!settingsContent) return;
        if (isCustom) {
            const targetSection = document.querySelector('[data-settings-section="custom"]');
            if (targetSection) {
                const targetTop = settingsContent.scrollTop
                    + targetSection.getBoundingClientRect().top
                    - settingsContent.getBoundingClientRect().top;
                settingsContent.scrollTo({ top: targetTop, behavior: "auto" });
            }
        } else {
            settingsContent.scrollTo({ top: 0, behavior: "auto" });
        }
    });
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

// 初始化绑定事件监听器
initAllKeyControls();
PlatformSettings.bindAll();


// 手动更新模型能力注册表
if (updateModelRegistryBtn) {
    updateModelRegistryBtn.onclick = async () => {
        const originalText = updateModelRegistryBtn.innerHTML;
        updateModelRegistryBtn.innerHTML = "⏳ 更新中...";
        updateModelRegistryBtn.disabled = true;
        try {
            const res = await apiBridge.update_model_registry();
            if (res && res.success) {
                alert(`更新成功！已拉取 ${res.count} 个模型的最新能力数据。`);
                // 刷新 UI
                if (typeof fetchModels === "function") {
                    await fetchModels();
                } else if (apiBridge.fetch_models) {
                    const platform = config.active_platform || "claude";
                    availableModels = await apiBridge.fetch_models(platform);
                    if (availableModels) modelCache.set(platform, availableModels);
                }
                updateModelSettingsUI();
            } else {
                alert(`更新失败: ${res ? res.error : "未知错误"}`);
            }
        } catch (e) {
            console.error(e);
            alert("请求异常，请查看日志。");
        } finally {
            updateModelRegistryBtn.innerHTML = originalText;
            updateModelRegistryBtn.disabled = false;
        }
    };
}

// 当模型改变时，刷新UI (在 ui.js 中 modelSelect.addEventListener("change", ...) 里也会调用此函数)
// 为此我们需要将 updateModelSettingsUI 挂载到 window
window.updateModelSettingsUI = function() {
    const selectedModelId = modelSelect ? modelSelect.value : config.model;
    if (window.ModelConfigEditor?.isEditing(selectedModelId, config.active_platform || "claude")) return;
    window.CustomParams?.load(config.model_configs?.[selectedModelId]?.custom_params || {}, !!selectedModelId);
    if (!selectedModelId) return;

    if (currentModelIndicator) {
        currentModelIndicator.textContent = selectedModelId;
    }

    // 从 config.model_configs 或 config 中提取
    const modelConfigs = config.model_configs || {};
    let mConfig = modelConfigs[selectedModelId];
    if (!mConfig) {
        // Fallback to top level config if no model specific config
        mConfig = {
            temperature: config.temperature,
            max_tokens: config.max_tokens,
            thinking_enabled: config.thinking_enabled,
            thinking_type: config.thinking_type || "adaptive",
            thinking_budget: config.thinking_budget || 1024,
            thinking_level: config.thinking_level || "high"
        };
    }

    // 基础参数
    if (modelTempSlider) {
        modelTempSlider.value = mConfig.temperature !== undefined ? mConfig.temperature : 0.7;
        if (modelTempLabelTitle) modelTempLabelTitle.textContent = `Temperature: ${parseFloat(modelTempSlider.value).toFixed(2)}`;
    }
    if (modelMaxTokensInput) {
        modelMaxTokensInput.value = mConfig.max_tokens || DEFAULT_MODEL_MAX_TOKENS;
    }

    // 查找模型能力
    const modelObj = availableModels ? availableModels.find(m => (typeof m === 'object' && m.id === selectedModelId)) : null;
    const caps = modelObj || {
        thinking_supported: false,
        adaptive_supported: false,
        enabled_supported: false,
        effort_levels: [],
        context_length: 0,
        max_output: 0
    };

    // 更新注册表信息展示
    if (modelInfoContext) modelInfoContext.textContent = caps.context_length ? caps.context_length.toLocaleString() : "未知";
    if (modelInfoOutput) modelInfoOutput.textContent = caps.max_output ? caps.max_output.toLocaleString() : "未知";
    if (modelInfoReasoning) {
        if (caps.thinking_supported) {
            modelInfoReasoning.textContent = "支持";
            modelInfoReasoning.style.color = "var(--green)";
        } else {
            modelInfoReasoning.textContent = "不支持";
            modelInfoReasoning.style.color = "var(--red)";
        }
    }

    // 处理 Thinking UI
    if (!caps.thinking_supported) {
        if (modelThinkingContainer) modelThinkingContainer.classList.add("hidden");
    } else {
        if (modelThinkingContainer) modelThinkingContainer.classList.remove("hidden");
        
        if (modelThinkingEnabledInput) {
            modelThinkingEnabledInput.checked = !!mConfig.thinking_enabled;
        }

        // Toggle visibility of options based on checkbox
        if (modelThinkingOptions) {
            if (mConfig.thinking_enabled) {
                modelThinkingOptions.classList.remove("hidden");
            } else {
                modelThinkingOptions.classList.add("hidden");
            }
        }

        // Setup radio buttons for type
        const adaptiveRadio = document.querySelector("input[name='model-thinking-type'][value='adaptive']");
        const enabledRadio = document.querySelector("input[name='model-thinking-type'][value='enabled']");
        
        if (adaptiveRadio && enabledRadio) {
            adaptiveRadio.disabled = !caps.adaptive_supported;
            adaptiveRadio.closest(".radio-label").style.opacity = caps.adaptive_supported ? "1" : "0.5";
            enabledRadio.disabled = !caps.enabled_supported;
            enabledRadio.closest(".radio-label").style.opacity = caps.enabled_supported ? "1" : "0.5";

            if (mConfig.thinking_type === "adaptive" && caps.adaptive_supported) {
                adaptiveRadio.checked = true;
            } else if (mConfig.thinking_type === "enabled" && caps.enabled_supported) {
                enabledRadio.checked = true;
            } else {
                // Default fallback
                if (caps.adaptive_supported) adaptiveRadio.checked = true;
                else if (caps.enabled_supported) enabledRadio.checked = true;
            }
        }

        // Type Group Visibility (only show if we have choices or required explicitly)
        if (modelThinkingTypeGroup) {
            if (caps.adaptive_supported || caps.enabled_supported) {
                modelThinkingTypeGroup.classList.remove("hidden");
            } else {
                modelThinkingTypeGroup.classList.add("hidden");
            }
        }

        if (modelThinkingBudgetInput) {
            modelThinkingBudgetInput.value = mConfig.thinking_budget || 1024;
        }

        // Populating effort levels
        if (modelThinkingLevelSelect) {
            if (caps.effort_levels && caps.effort_levels.length > 0) {
                if (modelThinkingLevelGroup) modelThinkingLevelGroup.classList.remove("hidden");
                modelThinkingLevelSelect.innerHTML = "";
                caps.effort_levels.forEach(lvl => {
                    const option = document.createElement("option");
                    option.value = lvl;
                    option.textContent = lvl.charAt(0).toUpperCase() + lvl.slice(1);
                    modelThinkingLevelSelect.appendChild(option);
                });
                modelThinkingLevelSelect.value = mConfig.thinking_level || caps.effort_levels[0];
            } else {
                if (modelThinkingLevelGroup) modelThinkingLevelGroup.classList.add("hidden");
            }
        }
    }
    window.ModelConfigEditor?.load(selectedModelId, config.active_platform || "claude", modelConfigs[selectedModelId] || {});
};

// Bind Events
if (modelThinkingEnabledInput) {
    modelThinkingEnabledInput.addEventListener("change", () => {
        if (modelThinkingOptions) {
            if (modelThinkingEnabledInput.checked) {
                modelThinkingOptions.classList.remove("hidden");
            } else {
                modelThinkingOptions.classList.add("hidden");
            }
        }
    });
}
if (modelTempSlider) {
    modelTempSlider.addEventListener("input", (e) => {
        if (modelTempLabelTitle) {
            modelTempLabelTitle.textContent = `Temperature: ${parseFloat(e.target.value).toFixed(2)}`;
        }
    });
}


// 绑定设置弹窗内部的平台标签卡切换逻辑
const settingsTabButtons = document.querySelectorAll(".platform-tabs .tab-btn");
const settingsTabPanels = document.querySelectorAll(".platform-panel");

function activatePlatformSettingsTab(btn) {
    settingsTabButtons.forEach(b => {
        const isActive = b === btn;
        b.classList.toggle("active", isActive);
        b.setAttribute("aria-selected", isActive ? "true" : "false");
        b.setAttribute("tabindex", isActive ? "0" : "-1");
        b.style.background = isActive ? "var(--surface0)" : "transparent";
        b.style.color = isActive ? "var(--text)" : "var(--subtext0)";
    });

    const platform = btn.getAttribute("data-platform-tab");
    if (typeof updateOcrNotices === "function") updateOcrNotices(platform);
    settingsTabPanels.forEach(panel => {
        panel.classList.toggle("hidden", panel.id !== `platform-panel-${platform}`);
    });
}

settingsTabButtons.forEach((btn, index) => {
    const platform = btn.getAttribute("data-platform-tab");
    const panel = document.getElementById(`platform-panel-${platform}`);
    btn.id = btn.id || `platform-tab-${platform}`;
    btn.setAttribute("aria-controls", `platform-panel-${platform}`);
    panel?.setAttribute("role", "tabpanel");
    panel?.setAttribute("aria-labelledby", btn.id);
    btn.onclick = () => activatePlatformSettingsTab(btn);
    btn.addEventListener("keydown", event => {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        const direction = event.key === "ArrowRight" ? 1 : -1;
        const target = settingsTabButtons[(index + direction + settingsTabButtons.length) % settingsTabButtons.length];
        target.focus();
        activatePlatformSettingsTab(target);
    });
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
    const editedModelConfig = window.ModelConfigEditor ? await window.ModelConfigEditor.prepareSave() : null;
    if (window.ModelConfigEditor && !editedModelConfig) return;
    const customParams = window.CustomParams?.read() ?? (window.CustomParams ? null : {});
    if (customParams === null) return;
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

    // OCR 识别模式保存
    if (ocrModeSelect) {
        config.ocr_mode = ocrModeSelect.value || "auto";
    }
    if (ocrCloudModelSelect) {
        config.ocr_cloud_model = ocrCloudModelSelect.value || "gemini";
    }
    
    // 保存 Token 验证字段：留空时保持原值不覆盖，防止保存空 token 导致全部 /api 请求 401 锁死
    if (serverTokenInput) {
        const newToken = serverTokenInput.value.trim();
        if (newToken) {
            config.security_token = newToken;
        } else {
            delete config.security_token;
        }
    }
    
    // 保存当前模型的专属设置
    const selectedModelId = modelSelect ? modelSelect.value : config.model;
    if (selectedModelId) {
        if (!config.model_configs) config.model_configs = {};
        if (!config.model_configs[selectedModelId]) config.model_configs[selectedModelId] = {};
        
        const mConfig = config.model_configs[selectedModelId];
        mConfig.custom_params = customParams;
        
        if (modelTempSlider) mConfig.temperature = parseFloat(modelTempSlider.value);
        if (modelMaxTokensInput) {
            mConfig.max_tokens = parseInt(modelMaxTokensInput.value) || DEFAULT_MODEL_MAX_TOKENS;
        }
        
        if (modelThinkingEnabledInput) mConfig.thinking_enabled = modelThinkingEnabledInput.checked;
        const thinkingModeRadio = document.querySelector("input[name='model-thinking-type']:checked");
        if (thinkingModeRadio) mConfig.thinking_type = thinkingModeRadio.value;
        if (modelThinkingBudgetInput) mConfig.thinking_budget = parseInt(modelThinkingBudgetInput.value) || 1024;
        if (modelThinkingLevelSelect) mConfig.thinking_level = modelThinkingLevelSelect.value || "high";

        if (editedModelConfig) Object.assign(mConfig, editedModelConfig);

        // Backward compatibility for root config fallback
        config.temperature = mConfig.temperature;
        config.max_tokens = mConfig.max_tokens;
    }

    PlatformSettings.saveAll(config);

    // 全局与服务器设置保存
    if (enableCodeSandboxInput) {
        config.enable_code_sandbox = !!(sandboxEnvironmentStatus && sandboxEnvironmentStatus.ready && enableCodeSandboxInput.checked);
    }
    if (autoRunCodeInput) {
        config.auto_run_code = !!config.enable_code_sandbox && autoRunCodeInput.checked;
    }
    if (codeSandboxTimeoutInput) {
        config.code_sandbox_timeout = normalizeSandboxTimeout(codeSandboxTimeoutInput.value);
        codeSandboxTimeoutInput.value = config.code_sandbox_timeout;
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
    
    // 关键：删除 custom_providers，防止前端旧快照回写覆盖后端由 CRUD 端点维护的供应商列表
    delete config.custom_providers;
    
    const saved = await apiBridge.save_config(config);
    if (!saved) {
        statusLabel.textContent = "设置保存失败，请检查日志或磁盘权限";
        return;
    }
    const fetchedConfig = await apiBridge.get_config();
    config = fetchedConfig;
    updateLedStatus();
    hideModal(settingsModal);
    statusLabel.textContent = "设置已保存";
    
    if (serverSettingsChanged) {
        alert("检测到服务端、端口或 SSL 传输设置已被修改。\n\n请手动关闭本程序并重新启动，新设置才能生效！");
    }
};

// ===================== 自定义模型提供商管理 =====================

const cpList = document.getElementById("custom-providers-list");
const cpForm = document.getElementById("custom-provider-form");
const cpNameInput = document.getElementById("cp-name-input");
const cpApiUrlInput = document.getElementById("cp-api-url-input");
const cpModelsApiUrlInput = document.getElementById("cp-models-api-url-input");
const cpApiKeyInput = document.getElementById("cp-api-key-input");
const cpApiKeyInputContainer = document.getElementById("cp-api-key-input-container");
const cpKeyStatusContainer = document.getElementById("cp-api-key-status-container");
const cpChangeKeyBtn = document.getElementById("cp-change-key-btn");
const cpDisconnectKeyBtn = document.getElementById("cp-disconnect-key-btn");
const cpModelsInput = document.getElementById("cp-models-input");
const cpTempInput = document.getElementById("cp-temperature-input");
const cpMaxTokensInput = document.getElementById("cp-max-tokens-input");
const cpProviderAdapterInput = document.getElementById("cp-provider-adapter-input");
const cpProviderAdapterHelp = document.getElementById("cp-provider-adapter-help");
const cpFileUploadOptions = document.getElementById("cp-file-upload-options");
const cpFileUploadPurposeInput = document.getElementById("cp-file-upload-purpose-input");
const cpFileUploadExpiryInput = document.getElementById("cp-file-upload-expiry-input");
const cpSaveBtn = document.getElementById("cp-save-btn");
const cpCancelBtn = document.getElementById("cp-cancel-btn");
const cpEditId = document.getElementById("cp-edit-id");
const addCustomProviderBtn = document.getElementById("add-custom-provider-btn");

let customProvidersCache = [];
let cpClearKeyPending = false;

async function refreshCustomProvidersUI() {
    if (!cpList) return;
    const providers = await apiBridge.list_custom_providers();
    customProvidersCache = providers || [];
    // 同步到全局 config 缓存，避免后续 saveSettings 回写时把后端供应商列表覆盖
    config.custom_providers = customProvidersCache;
    cpList.innerHTML = "";
    if (customProvidersCache.length === 0) {
        cpList.innerHTML = '<div style="font-size: 12px; color: var(--subtext0); padding: 8px;">暂无自定义提供商，点击上方"新增提供商"添加。</div>';
        return customProvidersCache;
    }
    customProvidersCache.forEach(p => {
        const row = document.createElement("div");
        row.style.cssText = "display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background-color: var(--crust); border: 1px solid var(--surface0); border-radius: 6px;";
        const keyBadge = p.has_api_key
            ? '<span style="color: var(--green); font-size: 11px;">🔒 Key 已配置</span>'
            : '<span style="color: var(--red); font-size: 11px;">⚠ 未配置 Key</span>';
        const modelsUrlLine = p.models_api_url
            ? ` · 模型地址: ${escapeHtml(p.models_api_url)}`
            : "";
        const adapter = p.provider_adapter || (p.file_upload_enabled ? "openai_files" : "local");
        const filesBadge = ` · 适配器: ${escapeHtml(adapter)}`;
        row.innerHTML = `
            <div style="display: flex; flex-direction: column; gap: 2px;">
                <span style="font-size: 13px; color: var(--text); font-weight: 500;">${escapeHtml(p.name || p.id)} <span style="color: var(--subtext0); font-size: 11px;">(${escapeHtml(p.platform_id || "")})</span></span>
                <span style="font-size: 11px; color: var(--subtext0);">${escapeHtml(p.api_url || "")}${modelsUrlLine}${filesBadge} · ${keyBadge}</span>
            </div>
            <div style="display: flex; gap: 6px;">
                <button type="button" class="btn btn-secondary btn-sm cp-edit-btn" data-cp-id="${escapeHtml(p.id)}" style="font-size: 11px; padding: 3px 8px;">编辑</button>
                <button type="button" class="btn btn-secondary btn-sm cp-del-btn" data-cp-id="${escapeHtml(p.id)}" style="color: var(--red); font-size: 11px; padding: 3px 8px;">删除</button>
            </div>`;
        cpList.appendChild(row);
    });
    cpList.querySelectorAll(".cp-edit-btn").forEach(b => b.onclick = () => startEditCustomProvider(b.dataset.cpId));
    cpList.querySelectorAll(".cp-del-btn").forEach(b => b.onclick = () => removeCustomProvider(b.dataset.cpId));
    return customProvidersCache;
}

function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function resetCustomProviderForm() {
    cpNameInput.value = "";
    cpApiUrlInput.value = "";
    cpModelsApiUrlInput.value = "";
    cpApiKeyInput.value = "";
    cpApiKeyInput.placeholder = "必填";
    cpModelsInput.value = "";
    cpTempInput.value = "0.7";
    cpMaxTokensInput.value = "4096";
    cpProviderAdapterInput.value = "local";
    cpFileUploadPurposeInput.value = "user_data";
    cpFileUploadExpiryInput.value = "172800";
    updateCustomProviderAdapterUI();
    cpEditId.textContent = "";
    cpClearKeyPending = false;
    // 新增模式：显示输入框，隐藏状态
    if (cpApiKeyInputContainer) cpApiKeyInputContainer.classList.remove("hidden");
    if (cpKeyStatusContainer) cpKeyStatusContainer.classList.add("hidden");
}

function startEditCustomProvider(id) {
    const p = customProvidersCache.find(x => x.id === id);
    if (!p) return;
    cpNameInput.value = p.name || "";
    cpApiUrlInput.value = p.api_url || "";
    cpModelsApiUrlInput.value = p.models_api_url || "";
    cpApiKeyInput.value = "";
    cpApiKeyInput.placeholder = "输入新 Key 以更换";
    cpModelsInput.value = (p.models || []).join(", ");
    cpTempInput.value = p.temperature != null ? p.temperature : 0.7;
    cpMaxTokensInput.value = p.max_tokens != null ? p.max_tokens : 4096;
    cpProviderAdapterInput.value = p.provider_adapter || (p.file_upload_enabled ? "openai_files" : "local");
    cpFileUploadPurposeInput.value = p.file_upload_purpose || "user_data";
    cpFileUploadExpiryInput.value = p.file_upload_expires_in_seconds || 172800;
    updateCustomProviderAdapterUI();
    cpEditId.textContent = id;
    cpClearKeyPending = false;
    // 编辑模式：参考内置 apikey 形式，已配置则显示状态+更换/断开，未配置则显示输入框
    if (p.has_api_key) {
        if (cpApiKeyInputContainer) cpApiKeyInputContainer.classList.add("hidden");
        if (cpKeyStatusContainer) cpKeyStatusContainer.classList.remove("hidden");
    } else {
        if (cpApiKeyInputContainer) cpApiKeyInputContainer.classList.remove("hidden");
        if (cpKeyStatusContainer) cpKeyStatusContainer.classList.add("hidden");
    }
    cpForm.classList.remove("hidden");
}

async function removeCustomProvider(id) {
    if (!confirm(`确认删除自定义提供商 "${id}"？\n其 API Key 也将被清除。`)) return;
    const removedActiveProvider = config.active_platform === `custom:${id}`;
    const res = await apiBridge.remove_custom_provider(id);
    if (res && res.error) { alert(res.error); return; }
    // 后端删除当前厂商时已经持久化回退到 Claude。
    if (removedActiveProvider) config.active_platform = "claude";
    const providers = await refreshCustomProvidersUI();
    await refreshPlatformSelect(providers);
    if (removedActiveProvider) await switchActivePlatform("claude");
    statusLabel.textContent = `已删除提供商: ${id}`;
}

if (addCustomProviderBtn) {
    addCustomProviderBtn.onclick = () => {
        resetCustomProviderForm();
        cpForm.classList.remove("hidden");
    };
}

if (cpCancelBtn) {
    cpCancelBtn.onclick = () => { cpForm.classList.add("hidden"); };
}

function updateCustomProviderAdapterUI() {
    if (!cpProviderAdapterInput) return;
    const adapter = cpProviderAdapterInput.value || "local";
    cpFileUploadOptions.classList.toggle("hidden", !["openai_files", "anthropic"].includes(adapter));
    const purposeVisible = adapter === "openai_files";
    cpFileUploadPurposeInput.previousElementSibling.classList.toggle("hidden", !purposeVisible);
    cpFileUploadPurposeInput.classList.toggle("hidden", !purposeVisible);
    cpFileUploadExpiryInput.max = adapter === "anthropic" ? "7776000" : "2592000";
    const help = {
        local: "图片、PDF 和 Office 文件均在本地提取为文本，不调用远端文件接口。",
        openai_files: "调用 Base URL 下的 /files，随后在 Chat Completions 中引用 file_id。",
        openrouter: "图片使用 image_url，PDF 使用 OpenRouter 官方 file_data 内容块；不会调用 /files。",
        inline_images: "图片以内联 data URL 发送；PDF 和 Office 文件仍在本地解析。",
        anthropic: "聊天改走 Anthropic Messages，图片/PDF 通过兼容的 Anthropic Files API 上传。",
        gemini: "聊天改走 Gemini GenerateContent，图片/PDF 通过兼容的 Gemini Files API 上传。"
    };
    if (cpProviderAdapterHelp) cpProviderAdapterHelp.textContent = help[adapter] || help.local;
}

if (cpProviderAdapterInput) {
    cpProviderAdapterInput.onchange = updateCustomProviderAdapterUI;
    updateCustomProviderAdapterUI();
}

// 自定义提供商 API Key 状态控制：复刻内置 apikey 的"更换/断开"形式
if (cpChangeKeyBtn) {
    cpChangeKeyBtn.onclick = () => {
        if (cpApiKeyInputContainer) cpApiKeyInputContainer.classList.remove("hidden");
        if (cpKeyStatusContainer) cpKeyStatusContainer.classList.add("hidden");
        cpApiKeyInput.focus();
    };
}
if (cpDisconnectKeyBtn) {
    cpDisconnectKeyBtn.onclick = () => {
        cpClearKeyPending = true;
        if (cpApiKeyInputContainer) cpApiKeyInputContainer.classList.remove("hidden");
        if (cpKeyStatusContainer) cpKeyStatusContainer.classList.add("hidden");
        cpApiKeyInput.value = "";
        cpApiKeyInput.placeholder = "已标记清除，保存后生效（可留空）";
    };
}

if (cpSaveBtn) {
    cpSaveBtn.onclick = async () => {
        const name = cpNameInput.value.trim();
        const apiUrl = cpApiUrlInput.value.trim();
        if (!name || !apiUrl) { alert("名称和 API Base URL 不能为空"); return; }
        const modelsApiUrl = cpModelsApiUrlInput.value.trim();
        const models = cpModelsInput.value.split(",").map(s => s.trim()).filter(Boolean);
        const temperature = parseFloat(cpTempInput.value) || 0.7;
        const maxTokens = parseInt(cpMaxTokensInput.value) || 4096;
        const providerAdapter = typeof cpProviderAdapterInput !== "undefined" && cpProviderAdapterInput
            ? cpProviderAdapterInput.value || "local"
            : "local";
        const fileUploadEnabled = providerAdapter !== "local";
        const fileUploadPurpose = typeof cpFileUploadPurposeInput !== "undefined"
            ? cpFileUploadPurposeInput.value.trim() || "user_data"
            : "user_data";
        const fileUploadExpiry = typeof cpFileUploadExpiryInput !== "undefined"
            ? Math.max(3600, Math.min(providerAdapter === "anthropic" ? 7776000 : 2592000, parseInt(cpFileUploadExpiryInput.value) || 172800))
            : 172800;
        const apiKey = cpApiKeyInput.value.trim();
        const editId = cpEditId.textContent.trim();
        let res;
        if (editId) {
            // 编辑模式：参考内置 apikey 保存逻辑
            // - 标记清除 → clear_api_key: true
            // - 输入了新值 → api_key: 新值
            // - 都没有 → 不传 api_key，保持原值
            const updateData = {
                id: editId, name, api_url: apiUrl, models_api_url: modelsApiUrl,
                models, temperature, max_tokens: maxTokens,
                provider_adapter: providerAdapter,
                file_upload_enabled: fileUploadEnabled, file_upload_purpose: fileUploadPurpose,
                file_upload_expires_in_seconds: fileUploadExpiry
            };
            if (cpClearKeyPending) {
                updateData.clear_api_key = true;
            } else if (apiKey) {
                updateData.api_key = apiKey;
            }
            res = await apiBridge.update_custom_provider(updateData);
        } else {
            if (!apiKey) { alert("新增提供商时 API Key 不能为空"); return; }
            res = await apiBridge.add_custom_provider({
                name, api_url: apiUrl, models_api_url: modelsApiUrl, api_key: apiKey, models, temperature, max_tokens: maxTokens,
                provider_adapter: providerAdapter,
                file_upload_enabled: fileUploadEnabled, file_upload_purpose: fileUploadPurpose,
                file_upload_expires_in_seconds: fileUploadExpiry
            });
        }
        if (res && res.error) { alert(res.error); return; }
        cpForm.classList.add("hidden");
        const providers = await refreshCustomProvidersUI();
        await refreshPlatformSelect(providers);
        statusLabel.textContent = editId ? `已更新提供商: ${name}` : `已新增提供商: ${name}`;
    };
}

// 供 main.js 调用：打开设置时刷新自定义提供商列表
function onSettingsOpenRefreshCustomProviders() {
    return refreshCustomProvidersUI();
}
