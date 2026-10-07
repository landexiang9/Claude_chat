import re

settings_path = "d:/28021/桌面/Learning_NOW/ai/Claude_chat/claude_chat/ui/settings.js"
with open(settings_path, "r", encoding="utf-8") as f:
    settings = f.read()

# 1. Loading parameters in showSettings()
def remove_between_inclusive(text, start, end):
    return re.sub(re.escape(start) + r'.*?' + re.escape(end), '', text, flags=re.DOTALL)

# Let's target specific blocks to replace.
# In showSettings(), after `// 异步查询并更新本地解析器...`
# We'll just replace the whole section from `// 加载各平台参数值` to `// Claude 网页搜索`
new_load_logic = """    // 更新当前模型的专属设置UI
    updateModelSettingsUI();

    // Claude 网页搜索"""
settings = re.sub(r'// 加载各平台参数值.*?// Claude 网页搜索', new_load_logic, settings, flags=re.DOTALL)

# 2. Events and UI Update Logic for Thinking
new_events_logic = """
// 当模型改变时，刷新UI (在 ui.js 中 modelSelect.addEventListener("change", ...) 里也会调用此函数)
// 为此我们需要将 updateModelSettingsUI 挂载到 window
window.updateModelSettingsUI = function() {
    const selectedModelId = modelSelect ? modelSelect.value : config.model;
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
        modelMaxTokensInput.value = mConfig.max_tokens || 4096;
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
"""
settings = re.sub(r'// Claude Thinking Mode Events.*?function populateThinkingLevels\(selectEl, levels, currentVal\) \{.*?\n\}', new_events_logic, settings, flags=re.DOTALL)

# 3. Saving parameters
new_save_logic = """
    // 保存当前模型的专属设置
    const selectedModelId = modelSelect ? modelSelect.value : config.model;
    if (selectedModelId) {
        if (!config.model_configs) config.model_configs = {};
        if (!config.model_configs[selectedModelId]) config.model_configs[selectedModelId] = {};
        
        let mConfig = config.model_configs[selectedModelId];
        
        if (modelTempSlider) mConfig.temperature = parseFloat(modelTempSlider.value);
        if (modelMaxTokensInput) mConfig.max_tokens = parseInt(modelMaxTokensInput.value) || 4096;
        
        if (modelThinkingEnabledInput) mConfig.thinking_enabled = modelThinkingEnabledInput.checked;
        const typeRadio = document.querySelector("input[name='model-thinking-type']:checked");
        if (typeRadio) mConfig.thinking_type = typeRadio.value;
        if (modelThinkingBudgetInput) mConfig.thinking_budget = parseInt(modelThinkingBudgetInput.value) || 1024;
        if (modelThinkingLevelSelect && !modelThinkingLevelGroup.classList.contains("hidden")) {
            mConfig.thinking_level = modelThinkingLevelSelect.value;
        }

        // Backward compatibility for root config fallback
        config.temperature = mConfig.temperature;
        config.max_tokens = mConfig.max_tokens;
    }

    const ocrModeSelect = document.getElementById("ocr-mode-select");"""
settings = re.sub(r'// 保存各平台温度滑块与 Max Tokens.*?const ocrModeSelect = document.getElementById\("ocr-mode-select"\);', new_save_logic, settings, flags=re.DOTALL)

# 4. Remove slider event listeners
settings = re.sub(r'\["claude", "deepseek", "gemini"\].forEach\(plat => \{.*?\}\);\n', '', settings, flags=re.DOTALL)

with open(settings_path, "w", encoding="utf-8") as f:
    f.write(settings)
print("settings.js updated")
