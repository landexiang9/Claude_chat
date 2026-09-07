// 绑定 DOM 界面元素
const convList = document.getElementById("conv-list");
const newChatBtn = document.getElementById("new-chat-btn");
const conversationSearchInput = document.getElementById("conversation-search-input");
const currentConversationTitle = document.getElementById("current-conversation-title");
const modelSelect = document.getElementById("model-select");
const modelPickerTrigger = document.getElementById("model-picker-trigger");
const modelPickerValue = document.getElementById("model-picker-value");
const modelPickerPanel = document.getElementById("model-picker-panel");
const modelSearchInput = document.getElementById("model-search-input");
const modelPickerResults = document.getElementById("model-picker-results");
const modelPickerEmpty = document.getElementById("model-picker-empty");
const apiStatusLed = document.getElementById("api-status-led");
const tokenLabel = document.getElementById("token-label");
const statusLabel = document.getElementById("status-label");
const chatEmptyState = document.getElementById("chat-empty-state");
const messageList = document.getElementById("message-list");
const chatViewport = document.getElementById("chat-viewport");
const scrollAnchor = document.getElementById("scroll-anchor");
const scrollToBottomBtn = document.getElementById("scroll-to-bottom-btn");

const attachBtn = document.getElementById("attach-btn");
const attachmentsArea = document.getElementById("attachments-area");
const inputBox = document.getElementById("input-box");
const markdownToggle = document.getElementById("markdown-toggle");
const sendBtn = document.getElementById("send-btn");

const settingsBtn = document.getElementById("settings-btn");
const settingsModal = document.getElementById("settings-modal");
const saveSettingsBtn = document.getElementById("save-settings-btn");
const apiKeyInput = document.getElementById("api-key-input");
const toggleKeyVisibility = document.getElementById("toggle-key-visibility");

const apiKeyInputContainer = document.getElementById("api-key-input-container");
const apiKeyStatusContainer = document.getElementById("api-key-status-container");
const changeKeyBtn = document.getElementById("change-key-btn");
const disconnectKeyBtn = document.getElementById("disconnect-key-btn");

// Platform selection & DeepSeek / Gemini config elements
const platformSelect = document.getElementById("platform-select");
const deepseekApiKeyInput = document.getElementById("deepseek-api-key-input");
const toggleDeepseekKeyVisibility = document.getElementById("toggle-deepseek-key-visibility");
const deepseekApiKeyInputContainer = document.getElementById("deepseek-api-key-input-container");
const deepseekApiKeyStatusContainer = document.getElementById("deepseek-api-key-status-container");
const changeDeepseekKeyBtn = document.getElementById("change-deepseek-key-btn");
const disconnectDeepseekKeyBtn = document.getElementById("disconnect-deepseek-key-btn");
const deepseekApiUrlInput = document.getElementById("deepseek-api-url-input");

const geminiApiKeyInput = document.getElementById("gemini-api-key-input");
const toggleGeminiKeyVisibility = document.getElementById("toggle-gemini-key-visibility");
const geminiApiKeyInputContainer = document.getElementById("gemini-api-key-input-container");
const geminiApiKeyStatusContainer = document.getElementById("gemini-api-key-status-container");
const changeGeminiKeyBtn = document.getElementById("change-gemini-key-btn");
const disconnectGeminiKeyBtn = document.getElementById("disconnect-gemini-key-btn");
const geminiApiUrlInput = document.getElementById("gemini-api-url-input");

const ocrModeSelect = document.getElementById("ocr-mode-select");
const ocrCloudModelSelect = document.getElementById("ocr-cloud-model-select");
const badgeDocx = document.getElementById("badge-docx");
const badgeOpenpyxl = document.getElementById("badge-openpyxl");
const badgePptx = document.getElementById("badge-pptx");
const badgePypdf = document.getElementById("badge-pypdf");
const badgeEasyocr = document.getElementById("badge-easyocr");

const serverTokenInput = document.getElementById("server-token-input");
const toggleTokenVisibility = document.getElementById("toggle-token-visibility");

// Claude specific parameters
const claudeEnableSearchInput = document.getElementById("claude-enable-search-input");
const claudeFileUploadEnabledInput = document.getElementById("claude-file-upload-enabled-input");
const claudeSearchGroup = document.getElementById("claude-search-group");
const claudeEnableFetchInput = document.getElementById("claude-enable-fetch-input");
const claudeSearchEngineSelect = document.getElementById("claude-search-engine-select");
const claudeTavilyKeyGroup = document.getElementById("claude-tavily-key-group");
const claudeTavilyKeyInput = document.getElementById("claude-tavily-key-input");
const claudeTavilyKeyInputContainer = document.getElementById("claude-tavily-key-input-container");
const claudeTavilyKeyStatusContainer = document.getElementById("claude-tavily-key-status-container");
const toggleClaudeTavilyVisibility = document.getElementById("toggle-claude-tavily-visibility");
const changeClaudeTavilyBtn = document.getElementById("change-claude-tavily-btn");
const disconnectClaudeTavilyBtn = document.getElementById("disconnect-claude-tavily-btn");
const claudeJinaKeyGroup = document.getElementById("claude-jina-key-group");
const claudeJinaKeyInput = document.getElementById("claude-jina-key-input");
const claudeJinaKeyInputContainer = document.getElementById("claude-jina-key-input-container");
const claudeJinaKeyStatusContainer = document.getElementById("claude-jina-key-status-container");
const toggleClaudeJinaVisibility = document.getElementById("toggle-claude-jina-visibility");
const changeClaudeJinaBtn = document.getElementById("change-claude-jina-btn");
const disconnectClaudeJinaBtn = document.getElementById("disconnect-claude-jina-btn");
const claudeWebPageParserSelect = document.getElementById("claude-web-page-parser-select");
const claudeWebFetchLimitInput = document.getElementById("claude-web-fetch-limit-input");

// DeepSeek specific parameters
const deepseekEnableSearchInput = document.getElementById("deepseek-enable-search-input");
const deepseekFileUploadEnabledInput = document.getElementById("deepseek-file-upload-enabled-input");
const deepseekSearchGroup = document.getElementById("deepseek-search-group");
const deepseekEnableFetchInput = document.getElementById("deepseek-enable-fetch-input");
const deepseekSearchEngineSelect = document.getElementById("deepseek-search-engine-select");
const deepseekTavilyKeyGroup = document.getElementById("deepseek-tavily-key-group");
const deepseekTavilyKeyInput = document.getElementById("deepseek-tavily-key-input");
const deepseekTavilyKeyInputContainer = document.getElementById("deepseek-tavily-key-input-container");
const deepseekTavilyKeyStatusContainer = document.getElementById("deepseek-tavily-key-status-container");
const toggleDeepseekTavilyVisibility = document.getElementById("toggle-deepseek-tavily-visibility");
const changeDeepseekTavilyBtn = document.getElementById("change-deepseek-tavily-btn");
const disconnectDeepseekTavilyBtn = document.getElementById("disconnect-deepseek-tavily-btn");
const deepseekJinaKeyGroup = document.getElementById("deepseek-jina-key-group");
const deepseekJinaKeyInput = document.getElementById("deepseek-jina-key-input");
const deepseekJinaKeyInputContainer = document.getElementById("deepseek-jina-key-input-container");
const deepseekJinaKeyStatusContainer = document.getElementById("deepseek-jina-key-status-container");
const toggleDeepseekJinaVisibility = document.getElementById("toggle-deepseek-jina-visibility");
const changeDeepseekJinaBtn = document.getElementById("change-deepseek-jina-btn");
const disconnectDeepseekJinaBtn = document.getElementById("disconnect-deepseek-jina-btn");
const deepseekWebPageParserSelect = document.getElementById("deepseek-web-page-parser-select");
const deepseekWebFetchLimitInput = document.getElementById("deepseek-web-fetch-limit-input");

// Gemini specific parameters
const geminiEnableSearchInput = document.getElementById("gemini-enable-search-input");
const geminiFileUploadEnabledInput = document.getElementById("gemini-file-upload-enabled-input");
const geminiEnableCodeSandboxInput = document.getElementById("gemini-enable-code-sandbox-input");
const geminiCodeSandboxTypeSelect = document.getElementById("gemini-code-sandbox-type-select");

// Global setting elements
const enableCodeSandboxInput = document.getElementById("enable-code-sandbox-input");
const autoRunCodeInput = document.getElementById("auto-run-code-input");
const codeSandboxTimeoutInput = document.getElementById("code-sandbox-timeout-input");
const codeSandboxStatusText = document.getElementById("code-sandbox-status-text");
const codeSandboxComponents = document.getElementById("code-sandbox-components");
const refreshCodeSandboxBtn = document.getElementById("refresh-code-sandbox-btn");
const installCodeSandboxBtn = document.getElementById("install-code-sandbox-btn");
const fontModeSelect = document.getElementById("font-mode-select");
const enableServerInput = document.getElementById("enable-server-input");
const syncConfigInput = document.getElementById("sync-config-input");
const onlyServerInput = document.getElementById("only-server-input");
const useCdnAssetsInput = document.getElementById("use-cdn-assets-input");
const enableSslInput = document.getElementById("enable-ssl-input");
const serverPortInput = document.getElementById("server-port-input");
const webSearchBtn = document.getElementById("web-search-btn");

const viewLogsBtn = document.getElementById("view-logs-btn");
const logsModal = document.getElementById("logs-modal");
const refreshLogsBtn = document.getElementById("refresh-logs-btn");
const clearLogsBtn = document.getElementById("clear-logs-btn");
const copyLogsBtn = document.getElementById("copy-logs-btn");
const logsContainer = document.getElementById("logs-container");
const logsContent = document.getElementById("logs-content");

const packetModal = document.getElementById("packet-modal");
const packetContent = document.getElementById("packet-content");
const copyPacketBtn = document.getElementById("copy-packet-btn");

const proxyBtn = document.getElementById("proxy-btn");
const proxyModal = document.getElementById("proxy-modal");
const saveProxyBtn = document.getElementById("save-proxy-btn");
const customProxyGroup = document.getElementById("custom-proxy-group");
const proxyUrlInput = document.getElementById("proxy-url-input");

const clearChatBtn = document.getElementById("clear-chat-btn");
const deleteModal = document.getElementById("delete-modal");
const confirmDeleteBtn = document.getElementById("confirm-delete-btn");


const modelTempSlider = document.getElementById("model-temp-slider");
const modelTempLabelTitle = document.getElementById("model-temp-label-title");
const modelMaxTokensInput = document.getElementById("model-max-tokens-input");
const modelThinkingContainer = document.getElementById("model-thinking-container");
const modelThinkingEnabledInput = document.getElementById("model-thinking-enabled-input");
const modelThinkingOptions = document.getElementById("model-thinking-options");
const modelThinkingTypeGroup = document.getElementById("model-thinking-type-group");
const modelThinkingBudgetGroup = document.getElementById("model-thinking-budget-group");
const modelThinkingBudgetInput = document.getElementById("model-thinking-budget-input");
const modelThinkingLevelGroup = document.getElementById("model-thinking-level-group");
const modelThinkingLevelSelect = document.getElementById("model-thinking-level-select");
const modelInfoContext = document.getElementById("model-info-context");
const modelInfoOutput = document.getElementById("model-info-output");
const modelInfoReasoning = document.getElementById("model-info-reasoning");
const updateModelRegistryBtn = document.getElementById("update-model-registry-btn");
const currentModelIndicator = document.getElementById("current-model-indicator");
