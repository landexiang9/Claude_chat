// Isolated settings QA: local UI assets, mocked APIs, no personal data or model calls.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs/promises');
const path = require('path');
const assert = require('assert/strict');

(async () => {
    const browser = await chromium.launch({
        executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true
    });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    const output = path.join(process.cwd(), 'scratch', 'settings-review');
    await fs.mkdir(output, { recursive: true });
    const capture = async name => {
        await page.screenshot({ path: path.join(output, `${name}.png`), animations: 'disabled' });
    };
    const visibleSection = () => page.locator('[data-settings-section]:visible');
    try {
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.hostname !== 'qa.local') return route.abort();
            const name = decodeURIComponent(url.pathname).replace(/^\//, '') || 'index.html';
            if (name.startsWith('api/')) return route.fulfill({ json: {} });
            let body = await fs.readFile(path.join(process.cwd(), 'claude_chat/ui', name));
            if (name === 'index.html') body = Buffer.from(body.toString().replace('<script src="main.js"></script>', ''));
            const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2' }[path.extname(name)] || 'application/octet-stream';
            await route.fulfill({ body, contentType });
        });
        await page.goto('http://qa.local/');
        await page.evaluate(() => {
            window.savedSettings = {
                active_platform: 'claude', model: 'qa-model', model_configs: {},
                system_prompts: [{ id: 'qa-preset', name: '写作助手', content: 'Write clearly.' }],
                enable_server: true, server_port: 8000, security_token: 'qa-credential-do-not-index'
            };
            config = structuredClone(window.savedSettings);
            availableModels = [{ id: 'qa-model', display_name: 'QA Model' }];
            apiBridge.get_config = async () => structuredClone(window.savedSettings);
            apiBridge.save_config = async value => {
                if (window.rejectSettingsSave) return false;
                window.savedSettings = structuredClone(value); return true;
            };
            apiBridge.list_custom_providers = async () => [];
            apiBridge.check_parsers = async () => ({ docx: true, pypdf: true });
            apiBridge.check_code_sandbox_environment = async () => ({ ready: false, backend: 'appcontainer' });
            apiBridge.fetch_models = async () => availableModels;
            apiBridge.preview_model_request = async data => ({
                params: data.model_config.request_params || { temperature: 0.7, max_tokens: 2048 }, request_protocol: 'claude'
            });
            updateModelList(availableModels, 'qa-model', true);
            renderSystemPromptSelect(); SelectPicker.refreshAll(); ChatTheme.set('light');
        });
        await page.locator('#settings-btn').click();
        await page.waitForFunction(() => !document.getElementById('model-request-json').disabled);
        assert.equal(await visibleSection().count(), 1);
        assert.equal(await visibleSection().getAttribute('data-settings-section'), 'general');
        assert(await page.locator('#enable-code-sandbox-input').isDisabled());
        await capture('general-light');

        // All categories have one independent page, including hidden model drafts.
        for (const key of ['providers', 'model', 'custom', 'files', 'server', 'appearance', 'presets', 'general']) {
            await page.locator(`[data-settings-nav="${key}"]`).click();
            assert.equal(await visibleSection().count(), 1);
            assert.equal(await visibleSection().getAttribute('data-settings-section'), key);
            assert.equal(await page.locator(`[data-settings-nav="${key}"]`).getAttribute('aria-current'), 'page');
            assert(await page.locator('.settings-content').evaluate(el => el.scrollWidth <= el.clientWidth + 1), `Desktop overflow: ${key}`);
        }
        const search = page.locator('#settings-search-input');
        await search.fill('Token');
        assert.equal(await visibleSection().count(), 0);
        assert.match(await page.locator('#settings-search-results').innerText(), /网络与服务/);
        await capture('search-light');
        await page.locator('.settings-search-result').filter({ hasText: '网络与服务' }).click();
        assert.equal(await visibleSection().getAttribute('data-settings-section'), 'server');
        assert.equal(await search.inputValue(), '');
        await page.locator('#server-port-input').fill('8123');
        await search.fill('qa-credential-do-not-index');
        assert.equal(await page.locator('.settings-search-result').count(), 0);
        await search.press('Escape');
        assert(await page.locator('#settings-modal').isVisible());
        assert.equal(await visibleSection().getAttribute('data-settings-section'), 'server');
        await search.fill('Temperature');
        await search.press('Enter');
        assert.equal(await visibleSection().getAttribute('data-settings-section'), 'model');

        // Saving from another page reveals model validation errors.
        await page.locator('#model-request-json').fill('{broken');
        await page.locator('[data-settings-nav="general"]').click();
        await page.locator('#save-settings-btn').click();
        assert.equal(await visibleSection().getAttribute('data-settings-section'), 'model');
        assert(await page.locator('#model-request-json-error').isVisible());
        assert((await page.locator('#model-request-json-error').innerText()).length > 0);
        await page.locator('#model-request-json').fill('{"temperature":0.4,"max_tokens":2048}');
        await page.locator('[data-settings-nav="appearance"]').click();
        await page.locator('[data-theme-choice="dark"]').click();
        assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark');
        await capture('appearance-dark');
        await page.locator('[data-settings-nav="general"]').click();
        await page.locator('#use-cdn-assets-input').check();
        await page.locator('#code-sandbox-timeout-input').fill('45');
        await capture('general-dark');
        await page.locator('#save-settings-btn').click();
        await page.waitForFunction(() => document.getElementById('settings-modal').classList.contains('hidden'));
        assert.equal(await page.evaluate(() => window.savedSettings.server_port), 8123);
        assert.equal(await page.evaluate(() => window.savedSettings.use_cdn_assets), true);
        assert.equal(await page.evaluate(() => window.savedSettings.code_sandbox_timeout), 45);
        assert.equal(await page.evaluate(() => window.savedSettings.model_configs['qa-model'].request_params.temperature), 0.4);
        assert.equal(await page.locator('#settings-btn').evaluate(el => el === document.activeElement), true);

        await page.locator('#settings-btn').click();
        await page.waitForFunction(() => !document.getElementById('model-request-json').disabled);
        await page.evaluate(() => { window.rejectSettingsSave = true; });
        await page.locator('#save-settings-btn').click();
        assert.match(await page.locator('#settings-save-status').innerText(), /保存失败/);
        assert(await page.locator('#settings-modal').isVisible());
        await page.evaluate(() => { window.rejectSettingsSave = false; ChatTheme.set('light'); });
        await page.setViewportSize({ width: 390, height: 844 });
        for (const key of ['general', 'appearance', 'presets', 'providers', 'model', 'custom', 'files', 'server']) {
            await page.locator(`[data-settings-nav="${key}"]`).click();
            assert(await page.locator('.settings-content').evaluate(el => el.scrollWidth <= el.clientWidth + 1), `Mobile overflow: ${key}`);
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            if (['general', 'appearance', 'providers', 'server'].includes(key)) await capture(`${key}-mobile`);
        }
        await page.locator('[data-settings-nav="general"]').focus();
        await page.keyboard.press('ArrowRight');
        assert.equal(await visibleSection().getAttribute('data-settings-section'), 'appearance');
        await page.keyboard.press('Tab');
        assert(await page.locator('#settings-modal').evaluate(el => el.contains(document.activeElement)));
        await page.keyboard.press('Escape');
        await page.locator('#sidebar-toggle-btn').click();
        await page.locator('#nav-prompts').click();
        assert.equal(await visibleSection().getAttribute('data-settings-section'), 'presets');
        await page.locator('#add-preset-btn').click();
        assert(await page.locator('#preset-modal').isVisible());
        await page.keyboard.press('Escape');
        assert(await page.locator('#settings-modal').isVisible());
        assert.deepEqual(errors, []);
        console.log('Settings navigation, search, drafts, validation, save, themes and mobile checks passed');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
