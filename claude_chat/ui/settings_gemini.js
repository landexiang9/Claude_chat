PlatformSettings.register("gemini", {
    bind() {
        const slider = document.getElementById("gemini-temp-slider");
        const label = document.getElementById("gemini-temp-label-title");
        if (slider && label) {
            slider.oninput = event => {
                label.textContent = `Temperature: ${parseFloat(event.target.value).toFixed(2)}`;
            };
        }
    },

    load(currentConfig) {
        if (typeof geminiFileUploadEnabledInput !== "undefined" && geminiFileUploadEnabledInput) geminiFileUploadEnabledInput.checked = currentConfig.gemini_file_upload_enabled !== false;
        if (geminiApiUrlInput) geminiApiUrlInput.value = currentConfig.gemini_api_url || "";
        if (geminiEnableSearchInput) geminiEnableSearchInput.checked = !!currentConfig.gemini_enable_web_search;
        if (geminiEnableCodeSandboxInput) geminiEnableCodeSandboxInput.checked = !!currentConfig.gemini_enable_code_sandbox;
        if (geminiCodeSandboxTypeSelect) geminiCodeSandboxTypeSelect.value = currentConfig.gemini_code_sandbox_type || "local";
    },

    save(currentConfig) {
        if (typeof geminiFileUploadEnabledInput !== "undefined" && geminiFileUploadEnabledInput) currentConfig.gemini_file_upload_enabled = geminiFileUploadEnabledInput.checked;
        if (geminiApiUrlInput) currentConfig.gemini_api_url = geminiApiUrlInput.value.trim() || "";
        if (geminiEnableSearchInput) currentConfig.gemini_enable_web_search = geminiEnableSearchInput.checked;
        if (geminiEnableCodeSandboxInput) currentConfig.gemini_enable_code_sandbox = geminiEnableCodeSandboxInput.checked;
        if (geminiCodeSandboxTypeSelect) currentConfig.gemini_code_sandbox_type = geminiCodeSandboxTypeSelect.value || "local";
    }
});
