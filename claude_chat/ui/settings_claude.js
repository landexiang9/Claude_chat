PlatformSettings.register("claude", {
    bind() {
        const slider = document.getElementById("claude-temp-slider");
        const label = document.getElementById("claude-temp-label-title");
        if (slider && label) {
            slider.oninput = event => {
                label.textContent = `Temperature: ${parseFloat(event.target.value).toFixed(2)}`;
            };
        }
        if (claudeEnableSearchInput) {
            claudeEnableSearchInput.onchange = () => {
                claudeSearchGroup.classList.toggle("hidden", !claudeEnableSearchInput.checked);
            };
        }
        if (claudeSearchEngineSelect) {
            claudeSearchEngineSelect.onchange = () => {
                toggleClaudeSearchKeys();
            };
        }
        if (claudeWebPageParserSelect) {
            claudeWebPageParserSelect.onchange = () => {
                toggleClaudeSearchKeys();
            };
        }
    },

    load(currentConfig) {
        if (typeof claudeFileUploadEnabledInput !== "undefined" && claudeFileUploadEnabledInput) claudeFileUploadEnabledInput.checked = currentConfig.claude_file_upload_enabled !== false;
        if (claudeEnableSearchInput) {
            claudeEnableSearchInput.checked = !!currentConfig.enable_web_search;
            if (claudeSearchGroup) claudeSearchGroup.classList.toggle("hidden", !claudeEnableSearchInput.checked);
        }
        if (claudeEnableFetchInput) claudeEnableFetchInput.checked = currentConfig.enable_web_fetch !== false;
        if (claudeSearchEngineSelect) claudeSearchEngineSelect.value = currentConfig.web_search_engine || "google";
        if (claudeWebPageParserSelect) claudeWebPageParserSelect.value = currentConfig.web_page_parser || "local";
        if (claudeWebFetchLimitInput) claudeWebFetchLimitInput.value = currentConfig.web_fetch_limit || 15000;
        toggleClaudeSearchKeys();
    },

    save(currentConfig) {
        if (typeof claudeFileUploadEnabledInput !== "undefined" && claudeFileUploadEnabledInput) currentConfig.claude_file_upload_enabled = claudeFileUploadEnabledInput.checked;
        if (claudeEnableSearchInput) currentConfig.enable_web_search = claudeEnableSearchInput.checked;
        if (claudeSearchEngineSelect) currentConfig.web_search_engine = claudeSearchEngineSelect.value || "google";
        if (claudeEnableFetchInput) currentConfig.enable_web_fetch = claudeEnableFetchInput.checked;
        if (claudeWebPageParserSelect) currentConfig.web_page_parser = claudeWebPageParserSelect.value || "local";
        if (claudeWebFetchLimitInput) currentConfig.web_fetch_limit = parseInt(claudeWebFetchLimitInput.value) || 15000;
    }
});

function toggleClaudeSearchKeys() {
    const engine = claudeSearchEngineSelect ? claudeSearchEngineSelect.value : "google";
    const parser = claudeWebPageParserSelect ? claudeWebPageParserSelect.value : "local";
    if (claudeTavilyKeyGroup) claudeTavilyKeyGroup.style.display = engine === "tavily" ? "block" : "none";
    if (claudeJinaKeyGroup) claudeJinaKeyGroup.style.display = engine === "jina" || parser === "jina" ? "block" : "none";
}
