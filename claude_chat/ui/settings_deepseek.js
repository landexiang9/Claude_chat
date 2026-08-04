PlatformSettings.register("deepseek", {
    bind() {
        const slider = document.getElementById("deepseek-temp-slider");
        const label = document.getElementById("deepseek-temp-label-title");
        if (slider && label) {
            slider.oninput = event => {
                label.textContent = `Temperature: ${parseFloat(event.target.value).toFixed(2)}`;
            };
        }
        if (deepseekEnableSearchInput) {
            deepseekEnableSearchInput.onchange = () => {
                deepseekSearchGroup.classList.toggle("hidden", !deepseekEnableSearchInput.checked);
            };
        }
        if (deepseekSearchEngineSelect) deepseekSearchEngineSelect.onchange = toggleDeepSeekSearchKeys;
        if (deepseekWebPageParserSelect) deepseekWebPageParserSelect.onchange = toggleDeepSeekSearchKeys;
    },

    load(currentConfig) {
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
    },

    save(currentConfig) {
        if (deepseekApiUrlInput) currentConfig.deepseek_api_url = deepseekApiUrlInput.value.trim() || "https://api.deepseek.com";
        if (deepseekEnableSearchInput) currentConfig.deepseek_enable_web_search = deepseekEnableSearchInput.checked;
        if (deepseekSearchEngineSelect) currentConfig.deepseek_web_search_engine = deepseekSearchEngineSelect.value || "google";
        if (deepseekEnableFetchInput) currentConfig.deepseek_enable_web_fetch = deepseekEnableFetchInput.checked;
        if (deepseekWebPageParserSelect) currentConfig.deepseek_web_page_parser = deepseekWebPageParserSelect.value || "local";
        if (deepseekWebFetchLimitInput) currentConfig.deepseek_web_fetch_limit = parseInt(deepseekWebFetchLimitInput.value) || 15000;
    }
});

function toggleDeepSeekSearchKeys() {
    const engine = deepseekSearchEngineSelect ? deepseekSearchEngineSelect.value : "google";
    const parser = deepseekWebPageParserSelect ? deepseekWebPageParserSelect.value : "local";
    if (deepseekTavilyKeyGroup) deepseekTavilyKeyGroup.style.display = engine === "tavily" ? "block" : "none";
    if (deepseekJinaKeyGroup) deepseekJinaKeyGroup.style.display = engine === "jina" || parser === "jina" ? "block" : "none";
}
