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
        existing.querySelector('#auth-token-input')?.focus();
        return;
    }
    
    const overlay = document.createElement('div');
    overlay.id = 'auth-overlay';
    overlay.className = 'modal-overlay auth-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'auth-dialog-title');
    
    overlay.innerHTML = `
        <div class="modal-card auth-card">
            <div class="modal-header">
                <h2 id="auth-dialog-title">安全访问认证</h2>
            </div>
            <div class="modal-body auth-modal-body">
                <p class="auth-description">
                    本服务已开启安全防护。若您正在从局域网或外部浏览器访问，请输入终端打印的 <b>Security Token</b>。
                </p>
                <div class="form-group">
                    <label for="auth-token-input">安全验证 Token</label>
                    <input type="password" id="auth-token-input" placeholder="输入 Security Token" autocomplete="current-password">
                </div>
                <div id="auth-error-msg" class="auth-error" role="alert">Token 错误或无效，请重新输入。</div>
            </div>
            <div class="modal-footer auth-footer">
                <button type="button" id="auth-submit-btn" class="btn btn-primary">验证并连接</button>
            </div>
        </div>
    `;
    
    document.body.appendChild(overlay);
    
    const input = document.getElementById('auth-token-input');
    const submitBtn = document.getElementById('auth-submit-btn');
    const errorMsg = document.getElementById('auth-error-msg');
    const appShell = document.querySelector('.app-container');
    const previousAppAriaHidden = appShell?.getAttribute('aria-hidden');
    if (appShell) {
        if ('inert' in appShell) appShell.inert = true;
        appShell.setAttribute('aria-hidden', 'true');
    }

    const cleanupAuthDialog = () => {
        if (appShell) {
            if ('inert' in appShell) appShell.inert = false;
            if (previousAppAriaHidden === null) appShell.removeAttribute('aria-hidden');
            else appShell.setAttribute('aria-hidden', previousAppAriaHidden);
        }
        overlay.remove();
    };
    
    const submitToken = () => {
        const val = input.value.trim();
        if (val) {
            securityToken = val;
            localStorage.setItem('security_token', val);
            cleanupAuthDialog();
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
    overlay.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault();
            return;
        }
        if (event.key !== 'Tab') return;
        const focusable = Array.from(overlay.querySelectorAll('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'));
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });
    requestAnimationFrame(() => input.focus({ preventScroll: true }));
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
        
        // 动态填充平台选择器中的自定义提供商选项
        await refreshPlatformSelect();
        
        // 初始化平台选择器状态
        if (platformSelect) {
            platformSelect.value = config.active_platform || "claude";
        }
        
        // 2. Load conversations before any network-bound model discovery.
        await loadConversations();
        
        // 3. The selected conversation renders immediately and refreshes its
        // platform model list in the background on cache miss.
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

// 动态刷新顶部平台选择器，注入自定义提供商选项
async function refreshPlatformSelect(providers = null) {
    if (!platformSelect) return;
    if (!Array.isArray(providers)) {
        try {
            providers = await apiBridge.list_custom_providers();
        } catch (e) {
            console.warn("list_custom_providers failed", e);
            return;
        }
    }

    // 自定义选项属于 optgroup，不能通过 platformSelect.removeChild(option) 删除；
    // 每次直接替换整个动态分组，避免刷新时抛出 NotFoundError 或累积空分组。
    platformSelect.querySelectorAll('optgroup[data-custom-providers="true"]').forEach(group => group.remove());
    if (providers && providers.length > 0) {
        const sep = document.createElement("optgroup");
        sep.dataset.customProviders = "true";
        sep.label = "────────────";
        providers.forEach(p => {
            const opt = document.createElement("option");
            opt.value = p.platform_id || `custom:${p.id}`;
            opt.textContent = p.name || p.id;
            sep.appendChild(opt);
        });
        platformSelect.appendChild(sep);
    }
    // 恢复当前选中值（动态注入可能导致选中丢失）
    if (config && config.active_platform) {
        platformSelect.value = config.active_platform;
    }
}
