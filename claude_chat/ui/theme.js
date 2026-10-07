// Loaded in <head> so theme is applied before the first paint.
(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    let mode = 'system';
    try { mode = localStorage.getItem('claude_chat_theme') || mode; } catch (_) { /* restricted storage */ }
    const valid = value => ['light', 'dark', 'system'].includes(value) ? value : 'system';
    function apply(value) {
        mode = valid(value);
        document.documentElement.dataset.theme = mode === 'system' ? (media.matches ? 'dark' : 'light') : mode;
        document.documentElement.style.colorScheme = document.documentElement.dataset.theme;
        document.querySelector('meta[name="theme-color"]')?.setAttribute('content', document.documentElement.dataset.theme === 'dark' ? '#0c0f16' : '#f7f7f9');
        document.querySelectorAll('[data-theme-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === mode)));
        const label = { light: '浅色', dark: '深色', system: '跟随系统' }[mode];
        const button = document.getElementById('theme-toggle-btn');
        if (button) { button.title = `主题：${label}`; button.setAttribute('aria-label', `切换主题，当前${label}`); }
        document.dispatchEvent(new CustomEvent('themechange', { detail: { mode, theme: document.documentElement.dataset.theme } }));
    }
    window.ChatTheme = { get mode() { return mode; }, set(value) { apply(value); try { localStorage.setItem('claude_chat_theme', mode); } catch (_) {} } };
    media.addEventListener('change', () => { if (mode === 'system') apply(mode); });
    apply(mode);
    document.addEventListener('DOMContentLoaded', () => {
        apply(mode);
        document.getElementById('theme-toggle-btn')?.addEventListener('click', () => window.ChatTheme.set(mode === 'light' ? 'dark' : mode === 'dark' ? 'system' : 'light'));
        document.querySelectorAll('[data-theme-choice]').forEach(button => button.addEventListener('click', () => window.ChatTheme.set(button.dataset.themeChoice)));
        const viewport = window.visualViewport;
        const resize = () => document.documentElement.style.setProperty('--app-height', `${viewport?.height || window.innerHeight}px`);
        viewport?.addEventListener('resize', resize); window.addEventListener('resize', resize); resize();
    });
})();
