const fs = require("fs");
const path = require("path");
const vm = require("vm");

function input(value = "") {
    return {
        value,
        checked: false,
        style: {},
        classList: { toggle() {}, add() {}, remove() {} }
    };
}

const elements = {
    claudeEnableSearchInput: input(), claudeSearchGroup: input(), claudeSearchEngineSelect: input(),
    claudeWebPageParserSelect: input(), claudeEnableFetchInput: input(), claudeWebFetchLimitInput: input(),
    claudeTavilyKeyGroup: input(), claudeJinaKeyGroup: input(),
    deepseekApiUrlInput: input(), deepseekEnableSearchInput: input(), deepseekSearchGroup: input(),
    deepseekSearchEngineSelect: input(), deepseekWebPageParserSelect: input(), deepseekEnableFetchInput: input(),
    deepseekWebFetchLimitInput: input(), deepseekTavilyKeyGroup: input(), deepseekJinaKeyGroup: input(),
    geminiApiUrlInput: input(), geminiEnableSearchInput: input(), geminiEnableCodeSandboxInput: input(),
    geminiCodeSandboxTypeSelect: input()
};

const context = vm.createContext({
    console,
    window: {},
    document: { getElementById: () => null },
    ...elements
});

const uiDir = path.join(__dirname, "..", "claude_chat", "ui");
vm.runInContext(fs.readFileSync(path.join(uiDir, "settings_components.js"), "utf8"), context, { filename: "settings_components.js" });
context.PlatformSettings = context.window.PlatformSettings;
for (const file of ["settings_claude.js", "settings_deepseek.js", "settings_gemini.js"]) {
    vm.runInContext(fs.readFileSync(path.join(uiDir, file), "utf8"), context, { filename: file });
}

const registry = context.window.PlatformSettings;
if (registry.registeredPlatforms().join(",") !== "claude,deepseek,gemini") throw new Error("platform registration failed");

const config = {
    enable_web_search: true,
    web_search_engine: "tavily",
    web_page_parser: "jina",
    web_fetch_limit: 1234,
    deepseek_api_url: "https://deepseek.example",
    deepseek_enable_web_search: true,
    deepseek_web_search_engine: "jina",
    deepseek_web_page_parser: "local",
    deepseek_web_fetch_limit: 5678,
    gemini_api_url: "https://gemini.example",
    gemini_enable_web_search: true,
    gemini_enable_code_sandbox: true,
    gemini_code_sandbox_type: "local"
};
registry.bindAll();
registry.loadAll(config);

if (elements.claudeWebFetchLimitInput.value !== 1234) throw new Error("Claude settings did not load");
if (elements.deepseekApiUrlInput.value !== "https://deepseek.example") throw new Error("DeepSeek settings did not load");
if (!elements.geminiEnableCodeSandboxInput.checked) throw new Error("Gemini settings did not load");

elements.deepseekApiUrlInput.value = "https://changed.example";
elements.geminiEnableSearchInput.checked = false;
registry.saveAll(config);
if (config.deepseek_api_url !== "https://changed.example") throw new Error("DeepSeek settings did not save");
if (config.gemini_enable_web_search !== false) throw new Error("Gemini settings did not save");

console.log("platform settings component test passed");
