// 访问令牌 (Security Token) 安全认证与存储
const urlParams = new URLSearchParams(window.location.search);
let securityToken = urlParams.get('token') || localStorage.getItem('security_token') || '';

if (urlParams.get('token')) {
    localStorage.setItem('security_token', urlParams.get('token'));
    // 移除地址栏中的 token 参数，防止 Referer 泄露
    const url = new URL(window.location);
    url.searchParams.delete('token');
    window.history.replaceState({}, document.title, url.toString());
}

function handleUnauthorized() {
    const existing = document.getElementById('auth-overlay');
    if (existing) {
        const errorMsg = existing.querySelector('#auth-error-msg');
        if (errorMsg) errorMsg.style.display = 'block';
        return;
    }
    
    const overlay = document.createElement('div');
    overlay.id = 'auth-overlay';
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = '9999';
    overlay.style.display = 'flex';
    overlay.style.justifyContent = 'center';
    overlay.style.alignItems = 'center';
    
    overlay.innerHTML = `
        <div class="modal-card" style="width: 380px;">
            <div class="modal-header">
                <h2>🔒 安全访问认证</h2>
            </div>
            <div class="modal-body" style="gap: 12px; padding: 20px;">
                <p style="font-size: 12px; color: var(--subtext1); line-height: 1.5;">
                    本服务已开启安全防护。若您正在从局域网或外部浏览器访问，请输入终端或日志中打印的 <b>Security Token</b>。
                </p>
                <div class="form-group">
                    <label for="auth-token-input">安全验证 Token (Security Token):</label>
                    <input type="password" id="auth-token-input" placeholder="输入 Security Token..." style="padding: 8px 10px; border-radius: 6px; border: 1px solid var(--surface0); background-color: var(--crust); color: var(--text);">
                </div>
                <div id="auth-error-msg" style="color: var(--red); font-size: 11px; display: none;">Token 错误或无效，请重新输入。</div>
            </div>
            <div class="modal-footer" style="padding: 12px 18px;">
                <button id="auth-submit-btn" class="btn btn-primary" style="width: 100%;">验证并连接</button>
            </div>
        </div>
    `;
    
    document.body.appendChild(overlay);
    
    const input = document.getElementById('auth-token-input');
    const submitBtn = document.getElementById('auth-submit-btn');
    const errorMsg = document.getElementById('auth-error-msg');
    
    const submitToken = () => {
        const val = input.value.trim();
        if (val) {
            securityToken = val;
            localStorage.setItem('security_token', val);
            document.body.removeChild(overlay);
            isInitialized = false;
            isInitializing = false;
            initApp();
        } else {
            errorMsg.style.display = 'block';
        }
    };
    
    submitBtn.onclick = submitToken;
    input.onkeydown = (e) => {
        if (e.key === 'Enter') submitToken();
    };
}
let isInitialized = false;
let isInitializing = false;

async function initApp() {
    if (isInitialized || isInitializing) return;
    isInitializing = true;
    
    try {
        // 1. 拉取系统基础配置
        const fetchedConfig = await apiBridge.get_config();
        if (!fetchedConfig) {
            throw new Error("Config fetch returned null/undefined");
        }
        config = fetchedConfig;
        applyFontMode();
        updateSearchBtnUI();
        updateLedStatus();
        
        // 初始化系统提示词下拉菜单
        renderSystemPromptSelect();
        
        // 初始化平台选择器状态
        if (platformSelect) {
            platformSelect.value = config.active_platform || "claude";
        }
        
        // 2. 拉取并配置可用模型下拉菜单
        const models = await apiBridge.fetch_models();
        if (!models) {
            throw new Error("Models fetch returned null/undefined");
        }
        availableModels = models;
        updateModelList(models);
        
        // 3. 加载历史对话卡片
        await loadConversations();
        
        // 4. 加载初始对话记录
        if (conversations.length > 0) {
            await selectConversation(conversations[0].id);
        } else {
            await startNewChat();
        }
        
        isInitialized = true;
        console.log("App successfully initialized.");
    } catch (e) {
        console.error("Failed to initialize app, retrying in 1000ms...", e);
        isInitialized = false;
        setTimeout(initApp, 1000);
    } finally {
        isInitializing = false;
    }
}

// 等待 WebView2 容器加载或标准的 DOM 构建就绪
window.addEventListener("pywebviewready", () => {
    console.log("pywebview API is ready. Initializing app...");
    initApp();
});
window.addEventListener("DOMContentLoaded", () => {
    setTimeout(() => {
        if (!isInitialized && !isInitializing) {
            console.log("DOMContentLoaded: pywebview API not ready yet, trying fallback...");
            initApp();
        }
    }, 300);
});
if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(() => {
        if (!isInitialized && !isInitializing) {
            initApp();
        }
    }, 300);
}
