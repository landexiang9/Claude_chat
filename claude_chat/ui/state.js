// 全局状态管理
let config = {};
let conversations = [];
let currentConvId = null;
let currentConv = null;
let attachments = [];
let isStreaming = false;
let isSending = false;
let streamingText = "";
let streamingThinking = "";
let availableModels = [];
let clearApiKeyPending = false; // 标记是否需要断开并清除 API Key
let clearDeepseekKeyPending = false;
let clearGeminiKeyPending = false;
let clearTavilyKeyPending = false;
let clearJinaKeyPending = false;
let initialEnableServer = false;
let initialServerPort = 8000;
let initialEnableSsl = false;

