// Independent title operations: never reload messages or switch the selected conversation.
(() => {
    const dialog = document.createElement('dialog');
    dialog.id = 'conversation-title-dialog';
    dialog.className = 'conversation-title-dialog';
    dialog.setAttribute('aria-labelledby', 'conversation-title-heading');
    dialog.innerHTML = `<form id="conversation-title-form">
        <div class="modal-header"><h2 id="conversation-title-heading">对话标题</h2>
            <button type="button" id="conversation-title-close" class="close-modal-btn" aria-label="关闭标题编辑">×</button></div>
        <div class="modal-body">
            <label for="conversation-title-input">标题</label>
            <input id="conversation-title-input" type="text" maxlength="100" autocomplete="off" required>
            <p class="conversation-title-help">AI 根据对话内容生成标题，使用此对话的模型，每次生成调用一次模型。</p>
            <p id="conversation-title-model" class="conversation-title-help"></p>
            <p id="conversation-title-notice" role="status" aria-live="polite"></p>
        </div>
        <div class="modal-footer">
            <button type="button" id="conversation-title-generate" class="btn btn-secondary">AI 重新生成</button>
            <button type="button" id="conversation-title-cancel" class="btn btn-secondary">取消</button>
            <button type="submit" id="conversation-title-save" class="btn btn-primary">保存标题</button>
        </div></form>`;
    document.body.appendChild(dialog);
    const input = dialog.querySelector('#conversation-title-input');
    const notice = dialog.querySelector('#conversation-title-notice');
    const generateButton = dialog.querySelector('#conversation-title-generate');
    const saveButton = dialog.querySelector('#conversation-title-save');
    const modelLabel = dialog.querySelector('#conversation-title-model');
    const pending = new Set();
    const known = new Map();
    const reportedErrors = new Map();
    let targetId = null, opener = null, timer = null, polling = false, saving = false, dirty = false, session = 0;

    function noticeText(text, error = false) {
        notice.textContent = text;
        notice.classList.toggle('title-error', error);
    }

    function schedule() {
        if (!timer && pending.size) timer = setTimeout(poll, 900);
    }

    function updateDialog(result) {
        if (!dialog.open || String(result.id) !== String(targetId)) return;
        generateButton.disabled = result.status === 'generating';
        generateButton.textContent = result.status === 'generating' ? '正在生成…' : 'AI 重新生成';
        modelLabel.textContent = result.model ? `模型：${result.model}` : '';
        if (!dirty && !saving) input.value = result.title || '新对话';
        if (result.status === 'generating') noticeText('正在生成标题，你仍可手动编辑并保存。');
        else if (result.error) noticeText(result.error, true);
        else noticeText('');
    }

    function apply(result) {
        if (!result?.success || !result.id) return;
        const id = String(result.id);
        const previous = known.get(id);
        if (previous && Number(previous.title_version) > Number(result.title_version)) return;
        known.set(id, result);
        const summary = conversations.find(row => String(row.id) === id);
        if (summary) Object.assign(summary, {
            title: result.title, title_source: result.title_source, title_version: result.title_version,
            title_status: result.status, title_error: result.error
        });
        if (String(currentConvId) === id && currentConv) Object.assign(currentConv, {
            title: result.title, title_source: result.title_source, title_version: result.title_version
        });
        if (result.status === 'generating') pending.add(id);
        else pending.delete(id);
        if (result.status === 'error' && reportedErrors.get(id) !== result.title_version) {
            reportedErrors.set(id, result.title_version);
            statusLabel.textContent = `标题生成失败：${result.error}`;
        }
        updateDialog(result);
        renderConversations();
        schedule();
    }

    async function refresh(id) {
        const result = await apiBridge.conversation_title_operation(id);
        if (result?.success) apply(result);
        else {
            pending.delete(String(id));
            if (dialog.open && String(targetId) === String(id)) noticeText(result?.error || '无法读取标题', true);
        }
        return result;
    }

    async function poll() {
        timer = null;
        if (polling) return schedule();
        polling = true;
        try {
            await Promise.all([...pending].map(async id => {
                try { await refresh(id); }
                catch (_) { /* Keep the job visible and retry a transient transport failure. */ }
            }));
        } finally { polling = false; schedule(); }
    }

    function observe(rows) {
        for (const row of rows) {
            const id = String(row.id);
            const cached = known.get(id);
            if (cached && Number(cached.title_version) >= Number(row.title_version || 0)) {
                Object.assign(row, {
                    title: cached.title, title_version: cached.title_version, title_source: cached.title_source,
                    title_status: cached.status, title_error: cached.error
                });
            }
            if (row.title_status === 'generating') pending.add(id);
        }
        schedule();
    }

    async function open(id, source) {
        if (!id) return;
        const row = conversations.find(c => String(c.id) === String(id)) || (currentConv?.id === id ? currentConv : {});
        const thisSession = ++session;
        targetId = id; opener = source || document.activeElement; dirty = false; saving = false;
        const shell = document.querySelector('.app-container');
        if (shell?.classList.contains('sidebar-open')) {
            shell.classList.remove('sidebar-open');
            opener = document.getElementById('sidebar-toggle-btn');
        }
        saveButton.disabled = false; generateButton.disabled = true; generateButton.textContent = 'AI 重新生成';
        input.value = row.title || '新对话'; modelLabel.textContent = ''; noticeText('');
        if (!dialog.open) dialog.showModal();
        input.focus(); input.select();
        try {
            const result = await refresh(id);
            if (session !== thisSession) return;
            generateButton.disabled = !result?.success || result.status === 'generating';
        } catch (error) {
            if (session === thisSession) noticeText(error.message || '无法读取标题，请重试', true);
        }
    }

    function close() { if (dialog.open) dialog.close(); }
    dialog.addEventListener('close', () => {
        const id = targetId;
        session++; targetId = null;
        const replacement = Array.from(convList.querySelectorAll('.conv-item'))
            .find(row => row.dataset.convId === String(id))?.querySelector('.conversation-title-menu-btn');
        const focusTarget = opener?.isConnected ? opener : replacement || inputBox;
        focusTarget?.focus();
    });
    dialog.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.stopPropagation(); }
    });
    dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
    dialog.querySelector('#conversation-title-close').onclick = close;
    dialog.querySelector('#conversation-title-cancel').onclick = close;
    input.addEventListener('input', () => { dirty = true; });
    dialog.querySelector('form').addEventListener('submit', async event => {
        event.preventDefault();
        if (!targetId || saving) return;
        const id = targetId, thisSession = session;
        const title = input.value.trim();
        if (!title) return noticeText('标题不能为空', true);
        saving = true; saveButton.disabled = true;
        try {
            const result = await apiBridge.conversation_title_operation(id, 'edit', title);
            if (!result?.success) throw new Error(result?.error || '标题保存失败，请重试');
            apply(result);
            if (session === thisSession) { close(); statusLabel.textContent = '标题已保存'; }
        } catch (error) {
            if (session === thisSession) noticeText(error.message || '标题保存失败，请重试', true);
        } finally {
            if (session === thisSession) { saving = false; saveButton.disabled = false; }
        }
    });
    generateButton.onclick = async () => {
        if (!targetId || generateButton.disabled) return;
        const id = targetId, thisSession = session;
        generateButton.disabled = true; noticeText('正在启动标题生成…');
        try {
            const result = await apiBridge.conversation_title_operation(id, 'generate');
            if (!result?.success) throw new Error(result?.error || '标题生成失败，请重试');
            apply(result);
        } catch (error) {
            if (session === thisSession) { noticeText(error.message || '标题生成失败，请重试', true); generateButton.disabled = false; }
        }
    };
    currentConversationTitle?.addEventListener('click', event => open(currentConvId, event.currentTarget));
    document.getElementById('conversation-title-toolbar-btn')?.addEventListener('click', event => open(currentConvId, event.currentTarget));
    window.ChatTitles = {open, observe, track: id => refresh(id).catch(() => {})};
})();
