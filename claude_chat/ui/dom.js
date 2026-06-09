// 绑定 DOM 界面元素
const convList = document.getElementById("conv-list");
const newChatBtn = document.getElementById("new-chat-btn");
const modelSelect = document.getElementById("model-select");
const apiStatusLed = document.getElementById("api-status-led");
const tokenLabel = document.getElementById("token-label");
const statusLabel = document.getElementById("status-label");
const messageList = document.getElementById("message-list");
const chatViewport = document.getElementById("chat-viewport");
const scrollAnchor = document.getElementById("scroll-anchor");

const attachBtn = document.getElementById("attach-btn");
const attachmentsArea = document.getElementById("attachments-area");
const inputBox = document.getElementById("input-box");
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
const tempSlider = document.getElementById("temp-slider");
const tempLabelTitle = document.getElementById("temp-label-title");
const maxTokensInput = document.getElementById("max-tokens-input");
const budgetGroup = document.getElementById("budget-group");
const budgetTokensInput = document.getElementById("budget-tokens-input");
const thinkingLevelGroup = document.getElementById("thinking-level-group");
const thinkingLevelSelect = document.getElementById("thinking-level-select");
const autoRunCodeInput = document.getElementById("auto-run-code-input");
const enableCodeSandboxInput = document.getElementById("enable-code-sandbox-input");
const fontModeSelect = document.getElementById("font-mode-select");
const enableServerInput = document.getElementById("enable-server-input");
const syncConfigInput = document.getElementById("sync-config-input");
const onlyServerInput = document.getElementById("only-server-input");
const enableSslInput = document.getElementById("enable-ssl-input");
const serverPortInput = document.getElementById("server-port-input");
const webSearchBtn = document.getElementById("web-search-btn");
const searchEngineSelect = document.getElementById("search-engine-select");
const tavilyKeyGroup = document.getElementById("tavily-key-group");
const tavilyKeyInput = document.getElementById("tavily-key-input");
const tavilyKeyInputContainer = document.getElementById("tavily-key-input-container");
const tavilyKeyStatusContainer = document.getElementById("tavily-key-status-container");
const toggleTavilyVisibility = document.getElementById("toggle-tavily-visibility");
const changeTavilyBtn = document.getElementById("change-tavily-btn");
const disconnectTavilyBtn = document.getElementById("disconnect-tavily-btn");

const jinaKeyGroup = document.getElementById("jina-key-group");
const jinaKeyInput = document.getElementById("jina-key-input");
const jinaKeyInputContainer = document.getElementById("jina-key-input-container");
const jinaKeyStatusContainer = document.getElementById("jina-key-status-container");
const toggleJinaVisibility = document.getElementById("toggle-jina-visibility");
const changeJinaBtn = document.getElementById("change-jina-btn");
const disconnectJinaBtn = document.getElementById("disconnect-jina-btn");
const webPageParserSelect = document.getElementById("web-page-parser-select");

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

