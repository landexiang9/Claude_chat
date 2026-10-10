PlatformSettings.register("deepseek", {
    bind() {
        const responses = document.getElementById("deepseek-use-responses-input");
        if (responses) responses.onchange = async () => {
            const enabled = responses.checked;
            const save = document.getElementById("save-settings-btn");
            responses.disabled = true;
            if (save) save.disabled = true;
            try {
                const success = await window.ModelConfigEditor?.switchDeepseekProtocol(enabled);
                if (success === false) responses.checked = !enabled;
            } finally {
                responses.disabled = false;
                if (save) save.disabled = false;
                updateDeepSeekProtocolUI();
            }
        };
        const slider = document.getElementById("deepseek-temp-slider");
        const label = document.getElementById("deepseek-temp-label-title");
        if (slider && label) {
            slider.oninput = event => {
                label.textContent = `Temperature: ${parseFloat(event.target.value).toFixed(2)}`;
            };
        }
        if (deepseekEnableSearchInput) {
            deepseekEnableSearchInput.onchange = () => {
                updateDeepSeekProtocolUI();
            };
        }
        if (deepseekSearchEngineSelect) deepseekSearchEngineSelect.onchange = updateDeepSeekProtocolUI;
        if (deepseekWebPageParserSelect) deepseekWebPageParserSelect.onchange = toggleDeepSeekSearchKeys;
    },

    load(currentConfig) {
        const responses = document.getElementById("deepseek-use-responses-input");
        if (responses) responses.checked = !!currentConfig.deepseek_use_responses;
        if (typeof deepseekFileUploadEnabledInput !== "undefined" && deepseekFileUploadEnabledInput) deepseekFileUploadEnabledInput.checked = currentConfig.deepseek_file_upload_enabled !== false;
        if (deepseekApiUrlInput) deepseekApiUrlInput.value = currentConfig.deepseek_api_url || "https://api.deepseek.com";
        if (deepseekEnableSearchInput) {
            deepseekEnableSearchInput.checked = !!currentConfig.deepseek_enable_web_search;
            if (deepseekSearchGroup) deepseekSearchGroup.classList.toggle("hidden", !deepseekEnableSearchInput.checked);
        }
        if (deepseekEnableFetchInput) deepseekEnableFetchInput.checked = currentConfig.deepseek_enable_web_fetch !== false;
        if (deepseekSearchEngineSelect) deepseekSearchEngineSelect.value = currentConfig.deepseek_web_search_engine || "google";
        if (deepseekWebPageParserSelect) deepseekWebPageParserSelect.value = currentConfig.deepseek_web_page_parser || "local";
        if (deepseekWebFetchLimitInput) deepseekWebFetchLimitInput.value = currentConfig.deepseek_web_fetch_limit || 15000;
        toggleDeepSeekSearchKeys();
        updateDeepSeekProtocolUI();
    },

    save(currentConfig) {
        const responses = document.getElementById("deepseek-use-responses-input");
        if (responses) currentConfig.deepseek_use_responses = responses.checked;
        if (typeof deepseekFileUploadEnabledInput !== "undefined" && deepseekFileUploadEnabledInput) currentConfig.deepseek_file_upload_enabled = deepseekFileUploadEnabledInput.checked;
        if (deepseekApiUrlInput) currentConfig.deepseek_api_url = deepseekApiUrlInput.value.trim() || "https://api.deepseek.com";
        if (deepseekEnableSearchInput) currentConfig.deepseek_enable_web_search = deepseekEnableSearchInput.checked;
        if (deepseekSearchEngineSelect) currentConfig.deepseek_web_search_engine = deepseekSearchEngineSelect.value || "google";
        if (deepseekEnableFetchInput) currentConfig.deepseek_enable_web_fetch = deepseekEnableFetchInput.checked;
        if (deepseekWebPageParserSelect) currentConfig.deepseek_web_page_parser = deepseekWebPageParserSelect.value || "local";
        if (deepseekWebFetchLimitInput) currentConfig.deepseek_web_fetch_limit = parseInt(deepseekWebFetchLimitInput.value) || 15000;
    }
});

function updateDeepSeekProtocolUI() {
    if (deepseekEnableSearchInput) deepseekEnableSearchInput.disabled = false;
    const label = document.getElementById("deepseek-search-label");
    if (label) label.textContent = "启用联网搜索";
    if (deepseekSearchGroup) deepseekSearchGroup.classList.toggle("hidden", !deepseekEnableSearchInput?.checked);
    const help = document.getElementById("deepseek-native-search-help");
    if (help) help.classList.toggle("hidden", deepseekSearchEngineSelect?.value !== "deepseek_native");
    toggleDeepSeekSearchKeys();
}

function toggleDeepSeekSearchKeys() {
    const engine = deepseekSearchEngineSelect ? deepseekSearchEngineSelect.value : "google";
    const parser = deepseekWebPageParserSelect ? deepseekWebPageParserSelect.value : "local";
    if (deepseekTavilyKeyGroup) deepseekTavilyKeyGroup.style.display = engine === "tavily" ? "block" : "none";
    if (deepseekJinaKeyGroup) deepseekJinaKeyGroup.style.display = engine === "jina" || parser === "jina" ? "block" : "none";
}
