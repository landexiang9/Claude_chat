function safeUrl(url) {
    if (!url) return "#";
    const cleaned = url.trim();
    if (cleaned.startsWith("http://") || cleaned.startsWith("https://")) {
        return cleaned;
    }
    return "#";
}

function escapeHtml(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


// 解决移动端输入法/虚拟键盘弹出时，导致顶部导航栏或底部输入框被遮挡/移出可视区的问题。
// 1. 使用 Visual Viewport API 动态监控实际可视区域高度，自动调整主容器高度，使输入框和导航栏都在可视区内。
if (window.visualViewport) {
    const handleViewportChange = () => {
        const height = window.visualViewport.height;
        const appContainer = document.querySelector('.app-container');
        if (appContainer) {
            appContainer.style.height = `${height}px`;
        }
        document.body.style.height = `${height}px`;
        // 如果是输入聚焦引发的视口变化，同时强制窗口滚动复位
        window.scrollTo(0, 0);
    };
    window.visualViewport.addEventListener('resize', handleViewportChange);
    window.visualViewport.addEventListener('scroll', handleViewportChange);
    // 页面加载完毕后初次执行
    window.addEventListener('DOMContentLoaded', handleViewportChange);
    window.addEventListener('load', handleViewportChange);
}

// 2. 拦截一切 window 级别的默认滚动，确保视口永远锁定在 (0, 0)
window.addEventListener('scroll', () => {
    if (window.scrollY !== 0 || window.scrollX !== 0) {
        window.scrollTo(0, 0);
    }
});

// 在任何输入框聚焦时，双重保险强制重置页面视口滚动条到最顶部，解决各平台浏览器默认滚动行为
document.addEventListener('focusin', (e) => {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
        setTimeout(() => {
            window.scrollTo(0, 0);
            if (window.visualViewport) {
                const height = window.visualViewport.height;
                const appContainer = document.querySelector('.app-container');
                if (appContainer) {
                    appContainer.style.height = `${height}px`;
                }
                document.body.style.height = `${height}px`;
            }
        }, 30);
    }
});
// 向对话展示区追加一条消息气泡
function isToolResultMsg(content) {
    if (Array.isArray(content)) {
        return content.some(item => item && item.type === "tool_result");
    }
    return false;
}

function getToolUseFromContent(content) {
    if (Array.isArray(content)) {
        return content.find(item => item && item.type === "tool_use" && item.name === "search_web");
    }
    return null;
}

function getToolResultFromContent(content) {
    if (Array.isArray(content)) {
        const block = content.find(item => item && item.type === "tool_result");
        return block ? block.content : "";
    }
    return "";
}

function extractSearchEngineFromText(text) {
    if (!text) return "";
    const match = text.match(/\[Search\s+Engine:\s*([^\]]+)\]/i);
    return match ? match[1].trim() : "";
}

function extractParserFromText(text) {
    if (!text) return "";
    const match = text.match(/\[Web\s+Reader:\s*([^\]]+)\]/i);
    return match ? match[1].trim() : "";
}

function extractUsageFromText(text) {
    if (!text) return null;
    const match = text.match(/\[Usage:\s*([^\]]+)\]/i);
    if (match) {
        try {
            return JSON.parse(match[1]);
        } catch(e) {}
    }
    return null;
}

function cleanToolResultText(text) {
    if (!text) return "";
    return text.replace(/\[Search\s+Engine:\s*([^\]]+)\]/i, "")
               .replace(/\[Web\s+Reader:\s*([^\]]+)\]/i, "")
               .replace(/\[Usage:\s*([^\]]+)\]/i, "")
               .trim();
}

function parseToolResultText(text) {
    if (!text || text.includes("No results found")) return [];
    const items = [];
    const rawParts = text.split(/\[\d+\]\s+Title:\s+/);
    rawParts.slice(1).forEach(part => {
        const lines = part.split("\n");
        if (lines.length > 0) {
            const title = lines[0].trim();
            let url = "";
            let snippet = "";
            
            const urlLine = lines.find(l => l.startsWith("URL:"));
            if (urlLine) url = urlLine.replace("URL:", "").trim();
            
            const snippetLine = lines.find(l => l.startsWith("Snippet:"));
            if (snippetLine) snippet = snippetLine.replace("Snippet:", "").trim();
            
            if (title) {
                items.push({ title, url, snippet });
            }
        }
    });
    return items;
}
// 工具函数：根据语言名获取对应文件后缀
function getExtensionFromLang(lang) {
    const langMap = {
        'javascript': '.js', 'js': '.js',
        'typescript': '.ts', 'ts': '.ts',
        'python': '.py', 'py': '.py',
        'html': '.html',
        'css': '.css',
        'json': '.json',
        'xml': '.xml',
        'yaml': '.yaml', 'yml': '.yaml',
        'markdown': '.md', 'md': '.md',
        'sql': '.sql',
        'rust': '.rs', 'rs': '.rs',
        'c': '.c',
        'cpp': '.cpp', 'c++': '.cpp',
        'go': '.go',
        'shell': '.sh', 'sh': '.sh', 'bash': '.sh',
        'powershell': '.ps1', 'ps1': '.ps1',
        'java': '.java',
        'php': '.php'
    };
    return langMap[lang.toLowerCase()] || '.txt';
}
// 仅在用户停留于最新消息附近时自动跟随流式输出；向上阅读时不再抢夺滚动位置。
let chatShouldFollowLatest = true;
let chatScrollFrame = null;
let chatProgrammaticScrollTimer = null;

function updateChatFollowState() {
    if (!chatViewport) return;
    const distanceFromBottom = chatViewport.scrollHeight - chatViewport.scrollTop - chatViewport.clientHeight;
    chatShouldFollowLatest = distanceFromBottom <= 120;
    if (scrollToBottomBtn) {
        scrollToBottomBtn.classList.toggle("hidden", chatShouldFollowLatest);
    }
}

function scrollChatBottom(force = false, smooth = false) {
    if (!force && !chatShouldFollowLatest) return;
    if (force) chatShouldFollowLatest = true;
    if (chatScrollFrame !== null) cancelAnimationFrame(chatScrollFrame);
    chatScrollFrame = requestAnimationFrame(() => {
        const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
        const useSmoothScroll = smooth && !reduceMotion;
        if (chatProgrammaticScrollTimer !== null) window.clearTimeout(chatProgrammaticScrollTimer);
        chatProgrammaticScrollTimer = window.setTimeout(() => {
            chatProgrammaticScrollTimer = null;
            updateChatFollowState();
        }, useSmoothScroll ? 420 : 0);
        scrollAnchor.scrollIntoView({ behavior: useSmoothScroll ? "smooth" : "auto" });
        chatScrollFrame = null;
        if (scrollToBottomBtn) scrollToBottomBtn.classList.add("hidden");
    });
}

chatViewport?.addEventListener("scroll", () => {
    if (chatProgrammaticScrollTimer === null) updateChatFollowState();
}, { passive: true });
scrollToBottomBtn?.addEventListener("click", () => scrollChatBottom(true, true));

// 复制文本工具函数
function copyText(text) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        navigator.clipboard.writeText(text).then(() => {
            showCopySuccess();
        }).catch(err => {
            console.error('Clipboard write failed, fallback to execCommand:', err);
            fallbackCopyText(text);
        });
    } else {
        fallbackCopyText(text);
    }
}

function fallbackCopyText(text) {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    // Prevent scrolling to bottom in some browsers
    textArea.style.top = "0";
    textArea.style.left = "0";
    textArea.style.position = "fixed";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
        const successful = document.execCommand('copy');
        if (successful) {
            showCopySuccess();
        } else {
            console.error('Fallback copy command failed');
        }
    } catch (err) {
        console.error('Fallback copy failed:', err);
    }
    document.body.removeChild(textArea);
}

function showCopySuccess() {
    statusLabel.textContent = "📋 内容已成功复制到剪贴板";
    setTimeout(() => { statusLabel.textContent = "就绪"; }, 2000);
}

