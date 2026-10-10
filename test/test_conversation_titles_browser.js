// Isolated real HTTP/SQLite with offline title/chat providers, including mobile and races.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {spawn} = require('child_process');
const fs = require('fs/promises');
const path = require('path');
const assert = require('assert/strict');
const python = process.env.PYTHON_EXECUTABLE || '.venv/Scripts/python.exe';
const fixture = String.raw`
import json, tempfile, sys, threading, time
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
import keyring
import claude_chat.config as settings
import claude_chat.db as database
from claude_chat.app import ClaudeChatApp
from claude_chat.api_bridge import WebAPI
from claude_chat.server import ThreadingHTTPServer, ClaudeChatHTTPHandler
from claude_chat.services.conversation_titles import TITLE_PROMPT

counter = 0
def mock_stream(*args, **kwargs):
    global counter
    if kwargs.get('system') == TITLE_PROMPT:
        counter += 1
        number = counter
        time.sleep(.7)
        assert not kwargs['enable_search'] and not kwargs['gemini_enable_code_sandbox']
        args[8].put(('text', f'AI {args[4]} 标题 {number}'))
    else:
        args[8].put(('text', 'Offline answer'))
    args[8].put(('done', {'input_tokens':2, 'output_tokens':3}))

with tempfile.TemporaryDirectory(prefix='title-browser-qa-') as directory, ExitStack() as stack:
    root = Path(directory)
    for module, names in [(settings, ['CONFIG_PATH','LOG_PATH','CONVERSATIONS_DIR','ATTACHMENT_STORE_DIR']),
                          (database, ['DB_PATH','CONVERSATIONS_DIR','BACKUP_DIR'])]:
        for name in names:
            stack.enter_context(patch.object(module, name, root / name.lower()))
    stack.enter_context(patch.object(keyring, 'get_password', return_value=None))
    stack.enter_context(patch.object(keyring, 'set_password'))
    stack.enter_context(patch.object(keyring, 'delete_password'))
    stack.enter_context(patch('claude_chat.services.model_service.fetch_available_models',
                             return_value=[{'id':'claude-test'},{'id':'claude-new'}]))
    stack.enter_context(patch('claude_chat.services.conversation_service.stream_claude_response', side_effect=mock_stream))
    app = ClaudeChatApp(force_server=True)
    app.config.data.update({'security_token':'title-qa-token','model':'claude-test','api_key':'offline-key'})
    api = WebAPI(app)
    api.memory_operation('options', {'auto_extract':False})
    server = ThreadingHTTPServer(('127.0.0.1',0), ClaudeChatHTTPHandler, app, api)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    print(json.dumps({'port':server.server_port}), flush=True)
    try: sys.stdin.read()
    finally: server.shutdown(); server.server_close(); worker.join(timeout=3)
`;

(async () => {
    const server = spawn(python, ['-u','-c',fixture], {
        cwd:process.cwd(), stdio:['pipe','pipe','pipe'], env:{...process.env,PYTHONIOENCODING:'utf-8'}
    });
    let diagnostics = '', browser;
    server.stderr.on('data', chunk => diagnostics += chunk);
    try {
        const port = await new Promise((resolve,reject) => {
            let text = '';
            const timer = setTimeout(() => reject(new Error('Fixture timeout: '+diagnostics)), 20000);
            server.stdout.on('data', chunk => {
                text += chunk;
                const line = text.split('\n').find(row => row.startsWith('{"port"'));
                if (line) { clearTimeout(timer); resolve(JSON.parse(line).port); }
            });
            server.on('exit', code => { clearTimeout(timer); reject(new Error('Fixture exit '+code+': '+diagnostics)); });
        });
        browser = await chromium.launch({
            executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true
        });
        const page = await browser.newPage({viewport:{width:1440,height:900}});
        const errors = []; page.on('pageerror', e => errors.push(e.message));
        const base = `http://127.0.0.1:${port}`;
        await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
        const output = path.join(process.cwd(),'scratch','title-review'); await fs.mkdir(output,{recursive:true});
        const capture = name => page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});
        await page.goto(base+'/?token=title-qa-token'); await page.waitForFunction(() => isInitialized);
        const id = await page.evaluate(() => currentConvId);
        const unauthorized = await page.request.post(base+'/api/conversation_title', {data:{conv_id:id,action:'generate'}});
        assert.equal(unauthorized.status(),401);
        await page.locator('#current-conversation-title').click();
        await page.waitForFunction(() => !document.getElementById('conversation-title-generate').disabled);
        await page.locator('#conversation-title-generate').click();
        await page.waitForFunction(() => document.getElementById('conversation-title-notice').textContent.includes('暂无'));
        assert(!(await page.locator('#conversation-title-generate').isDisabled()));
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#conversation-title-dialog').evaluate(e => e.open),false);
        await page.locator('#input-box').fill('为聊天工具增加对话标题'); await page.locator('#send-btn').click();
        await page.waitForFunction(() => !isStreaming && !isSending && messageList.textContent.includes('Offline answer'));
        await page.waitForFunction(() => document.getElementById('current-conversation-title').textContent === 'AI claude-test 标题 1');
        assert.equal(await page.title(),'AI claude-test 标题 1 · Chatudex');
        const originalMessages = await page.evaluate(() => JSON.stringify(currentConv.messages));
        await page.locator('#current-conversation-title').click();
        await page.locator('#conversation-title-input').fill('<img src=x onerror=alert(1)> 手动标题');
        await page.keyboard.press('Enter');
        await page.waitForFunction(() => !document.getElementById('conversation-title-dialog').open);
        assert.equal(await page.locator('#current-conversation-title').textContent(),'<img src=x onerror=alert(1)> 手动标题');
        assert.equal(await page.locator('.conv-title img').count(),0);
        assert.equal(await page.evaluate(() => JSON.stringify(currentConv.messages)),originalMessages);
        assert.equal(await page.evaluate(() => currentConvId),id);
        await page.reload(); await page.waitForFunction(() => isInitialized);
        assert.equal(await page.locator('#current-conversation-title').textContent(),'<img src=x onerror=alert(1)> 手动标题');
        await page.locator('#current-conversation-title').click();
        await page.locator('#conversation-title-input').fill('取消的草稿'); await page.keyboard.press('Escape');
        assert.equal(await page.locator('#current-conversation-title').textContent(),'<img src=x onerror=alert(1)> 手动标题');
        // Saving while a title job runs invalidates the late model result.
        await page.locator('#current-conversation-title').click();
        await page.waitForFunction(() => !document.getElementById('conversation-title-generate').disabled);
        await page.locator('#conversation-title-generate').click();
        await page.waitForFunction(() => document.getElementById('conversation-title-generate').textContent.includes('正在生成'));
        await page.locator('#conversation-title-input').fill('生成期间手动保存'); await page.keyboard.press('Enter');
        await page.waitForFunction(() => !document.getElementById('conversation-title-dialog').open);
        await page.waitForTimeout(1100);
        assert.equal(await page.locator('#current-conversation-title').textContent(),'生成期间手动保存');
        // Regeneration reads the conversation's current model, independent of the old title job.
        await page.evaluate(async () => {
            config.model = 'claude-new'; await apiBridge.save_config(config);
        });
        await page.locator('#current-conversation-title').click();
        await page.waitForFunction(() => !document.getElementById('conversation-title-generate').disabled);
        await page.locator('#conversation-title-generate').click();
        await page.waitForFunction(() => document.getElementById('conversation-title-input').value === 'AI claude-new 标题 3');
        assert.equal(await page.locator('#conversation-title-model').textContent(),'模型：claude-new');
        await page.evaluate(() => ChatTheme.set('light')); await capture('desktop-light');
        await page.keyboard.press('Escape');
        // A backend error retains the draft and allows a retry.
        await page.route(base+'/api/conversation_title', route => {
            const data = route.request().postDataJSON();
            return data.action === 'edit'
                ? route.fulfill({status:400,json:{success:false,error:'模拟保存失败'}})
                : route.continue();
        });
        await page.locator('#current-conversation-title').click();
        await page.locator('#conversation-title-input').fill('失败后保留草稿'); await page.keyboard.press('Enter');
        await page.waitForFunction(() => document.getElementById('conversation-title-notice').textContent === '模拟保存失败');
        assert.equal(await page.locator('#conversation-title-input').inputValue(),'失败后保留草稿');
        await page.unroute(base+'/api/conversation_title');
        await page.keyboard.press('Enter'); await page.waitForFunction(() => !document.getElementById('conversation-title-dialog').open);
        // Editing another conversation must leave the selected chat/messages unchanged.
        const other = await page.evaluate(async () => {
            const row = await apiBridge.new_conversation(); await loadConversations(); return row.id;
        });
        const selectedBefore = await page.evaluate(() => currentConvId);
        const messagesBefore = await page.locator('#message-list').textContent();
        await page.locator(`.conv-item[data-conv-id="${other}"] .conversation-title-menu-btn`).click();
        await page.locator('#conversation-title-input').fill('另一个对话标题'); await page.keyboard.press('Enter');
        await page.waitForFunction(() => !document.getElementById('conversation-title-dialog').open);
        assert.equal(await page.evaluate(() => currentConvId),selectedBefore);
        assert.equal(await page.locator('#message-list').textContent(),messagesBefore);
        await page.locator('#conversation-search-input').fill('另一个对话');
        assert.equal(await page.locator('.conv-item').count(),1);
        await page.locator('#conversation-search-input').fill('');
        // Mobile: sidebar operations remain reachable and the native dialog fits the viewport.
        await page.setViewportSize({width:390,height:844}); await page.locator('#sidebar-toggle-btn').click();
        await page.locator(`.conv-item[data-conv-id="${id}"] .conversation-title-menu-btn`).click();
        await page.waitForFunction(() => !document.getElementById('conversation-title-generate').disabled);
        assert(await page.locator('#conversation-title-dialog').evaluate(e => e.scrollWidth <= e.clientWidth + 1));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await capture('mobile-light'); await page.evaluate(() => ChatTheme.set('dark')); await capture('mobile-dark');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.getElementById('conversation-title-dialog').open);
        assert(await page.evaluate(() => !document.querySelector('.app-container').classList.contains('sidebar-open')));
        await page.locator('#mobile-more-btn').click();
        const header = await page.locator('.top-bar').boundingBox();
        const titleButton = await page.locator('#conversation-title-toolbar-btn').boundingBox();
        assert(titleButton.y + titleButton.height <= header.y + header.height + 2);
        await page.locator('#conversation-title-toolbar-btn').click();
        assert.equal(await page.locator('#conversation-title-dialog').evaluate(e => e.open),true);
        await page.keyboard.press('Escape');
        // Native bridge selects the same title operation without an HTTP request.
        const native = await page.evaluate(async () => {
            window.pywebview = {api:{conversation_title_operation:async (...args) => ({success:true,args})}};
            const result = await apiBridge.conversation_title_operation('native-id','edit','桌面标题');
            delete window.pywebview; return result.args;
        });
        assert.deepEqual(native,['native-id','edit','桌面标题']);
        assert.deepEqual(errors,[]);
        console.log('Title browser QA passed: auto/model/edit/regenerate/race/persistence/errors/search/mobile/native/authentication');
    } finally {
        if (browser) await browser.close(); server.stdin.end();
        await new Promise(resolve => {
            if (server.exitCode !== null) return resolve(); server.on('exit',resolve);
            setTimeout(() => { server.kill(); resolve(); },5000).unref();
        });
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
