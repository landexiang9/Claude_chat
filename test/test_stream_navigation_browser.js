// Real HTTP transport and isolated SQLite; all model output is generated offline.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {spawn} = require('child_process');
const assert = require('assert/strict');
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

finish = threading.Event()
class SlowClose:
    def close(self): time.sleep(2)

def mock_stream(*args, **kwargs):
    events, abort, created = args[8:11]
    created(SlowClose())
    prompt = args[3][-1]['content']
    if prompt == 'stall':
        abort.wait(10)  # No first chunk; stop must wake the transport consumer.
    else:
        i = 0
        while not abort.is_set() and not finish.is_set() and i < 400:
            events.put(('thinking', f'Thought {i}. '))
            events.put(('text', f'\n\nOutput {i}: ' + 'offline output ' * 12))
            i += 1
            time.sleep(.04)
    if abort.is_set():
        time.sleep(.15)
        events.put(('text', 'LATE OUTPUT MUST BE DROPPED'))
        events.put(('error', 'transport closed'))
    events.put(('done', {'input_tokens':5, 'output_tokens':9}))

class Handler(ClaudeChatHTTPHandler):
    def do_POST(self):
        if self.path == '/test/finish':
            finish.set()
            self.send_json_response({'success':True})
        elif self.path == '/test/reset':
            finish.clear()
            self.send_json_response({'success':True})
        else: super().do_POST()

with tempfile.TemporaryDirectory(prefix='stream-navigation-qa-') as directory, ExitStack() as stack:
    root = Path(directory)
    for module, names in [(settings, ['CONFIG_PATH','LOG_PATH','CONVERSATIONS_DIR','ATTACHMENT_STORE_DIR']),
                          (database, ['DB_PATH','CONVERSATIONS_DIR','BACKUP_DIR'])]:
        for name in names: stack.enter_context(patch.object(module, name, root / name.lower()))
    for name in ['get_password','set_password','delete_password']:
        stack.enter_context(patch.object(keyring, name, return_value=None))
    stack.enter_context(patch('claude_chat.services.model_service.fetch_available_models', return_value=[{'id':'claude-test'}]))
    stack.enter_context(patch('claude_chat.services.conversation_service.stream_claude_response', side_effect=mock_stream))
    stack.enter_context(patch('claude_chat.services.conversation_service.ConversationService.schedule_conversation_title'))
    app = ClaudeChatApp(force_server=True)
    app.config.data.update({'security_token':'stream-qa-token','model':'claude-test','api_key':'offline-key'})
    api = WebAPI(app)
    api.memory_operation('options', {'auto_extract':False})
    source = api.new_conversation()['id']
    for i in range(20):
        app.conv_manager.add_message(source, 'user', f'History {i}: ' + 'read earlier messages ' * 30)
        app.conv_manager.add_assistant_message_and_update_tokens(source, f'Reply {i}: ' + 'old answer ' * 30)
    other = api.new_conversation()['id']
    app.conv_manager.add_message(other, 'user', 'Other conversation only')
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler, app, api)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    print(json.dumps({'port':server.server_port,'source':source,'other':other}), flush=True)
    try: sys.stdin.read()
    finally: server.shutdown(); server.server_close(); worker.join(timeout=3)
`;

(async () => {
    const server = spawn(process.env.PYTHON_EXECUTABLE || '.venv/Scripts/python.exe', ['-u','-c',fixture], {
        cwd:process.cwd(), stdio:['pipe','pipe','pipe'], env:{...process.env,PYTHONIOENCODING:'utf-8'}
    });
    let diagnostics = '', browser;
    server.stderr.on('data', chunk => diagnostics += chunk);
    try {
        const setup = await new Promise((resolve,reject) => {
            let output = '';
            const timer = setTimeout(() => reject(new Error('Fixture timeout: '+diagnostics)), 20000);
            server.stdout.on('data', chunk => {
                output += chunk;
                const line = output.split('\n').find(row => row.startsWith('{"port"'));
                if (line) {clearTimeout(timer); resolve(JSON.parse(line));}
            });
            server.on('exit', code => {clearTimeout(timer); reject(new Error('Fixture exit '+code+': '+diagnostics));});
        });
        browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
        const page = await browser.newPage({viewport:{width:1440,height:900}});
        const errors = []; page.on('pageerror', e => errors.push(e.message));
        const base = `http://127.0.0.1:${setup.port}`;
        await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
        await page.goto(base+'/?token=stream-qa-token');
        await page.waitForFunction(() => isInitialized);
        await page.evaluate(id => selectConversation(id), setup.source);
        await page.locator('#input-box').fill('stream'); await page.locator('#send-btn').click();
        await page.waitForFunction(() => streamingText.includes('Output 3'));
        // Interrupt following even while an animation frame is pending.
        await page.evaluate(() => {
            scrollChatBottom();
            chatViewport.dispatchEvent(new WheelEvent('wheel', {deltaY:-100}));
            chatViewport.scrollTop = 400;
        });
        await page.waitForTimeout(500);
        assert(Math.abs(await page.evaluate(() => chatViewport.scrollTop) - 400) < 3, 'Output must preserve reading position');
        assert.equal(await page.evaluate(() => chatShouldFollowLatest), false);
        await page.evaluate(() => scrollChatBottom(true,true));
        await page.waitForTimeout(150);
        assert(await page.evaluate(() => chatViewport.scrollHeight-chatViewport.clientHeight-chatViewport.scrollTop < 5));
        // Thinking/text continue in the original view after switching away.
        await page.evaluate(id => selectConversation(id), setup.other);
        const otherView = await page.locator('#message-list').textContent();
        const otherTokens = await page.locator('#token-label').textContent();
        await page.waitForTimeout(200);
        assert.equal(await page.locator('#message-list').textContent(),otherView);
        await page.evaluate(id => selectConversation(id), setup.source);
        assert((await page.locator('#streaming-message-body').textContent()).includes('Output'));
        assert((await page.locator('#streaming-thinking-container').textContent()).includes('Thought'));
        await page.evaluate(id => selectConversation(id), setup.other);
        await page.request.post(base+'/test/finish');
        await page.waitForFunction(() => !isStreaming && !isSending);
        assert.equal(await page.locator('#message-list').textContent(),otherView);
        assert.equal(await page.locator('#token-label').textContent(),otherTokens);
        assert.equal(await page.evaluate(() => currentConvId),setup.other);
        await page.evaluate(id => selectConversation(id), setup.source);
        assert((await page.locator('#message-list').textContent()).includes('Output'));

        // Cancellation before any output also tolerates a blocked SDK close().
        await page.request.post(base+'/test/reset');
        await page.locator('#input-box').fill('stall'); await page.locator('#send-btn').click();
        await page.waitForFunction(() => isStreaming);
        await page.locator('#send-btn').click();
        await page.waitForFunction(() => !isStreaming && !isSending, null, {timeout:1500});
        assert.equal(await page.evaluate(() => currentConv.messages.at(-1).aborted),true);
        await page.waitForTimeout(250);
        assert(!(await page.locator('#message-list').textContent()).includes('LATE OUTPUT'));
        // Partial output is saved once; cancellation also works from a different chat.
        await page.locator('#input-box').fill('stream'); await page.locator('#send-btn').click();
        await page.waitForFunction(() => streamingText.includes('Output 2'));
        await page.evaluate(id => selectConversation(id), setup.other);
        await page.locator('#send-btn').click();
        await page.waitForFunction(() => !isStreaming && !isSending, null, {timeout:1500});
        assert.equal(await page.locator('#message-list').textContent(),otherView);
        await page.evaluate(id => selectConversation(id), setup.source);
        const saved = await page.evaluate(() => currentConv.messages.at(-1));
        assert.equal(saved.aborted,true); assert(saved.content.includes('Output')); assert(saved.thinking.includes('Thought'));
        assert(!saved.content.includes('LATE OUTPUT'));
        // Stop can reach the server before the generation request itself.
        await page.route('**/api/send_message', async route => {
            await new Promise(resolve => setTimeout(resolve,250));
            await route.continue();
        }, {times:1});
        await page.locator('#input-box').fill('stall'); await page.locator('#send-btn').click();
        await page.locator('#send-btn').click();
        await page.waitForFunction(() => !isStreaming && !isSending, null, {timeout:2000});
        assert.equal(await page.evaluate(() => currentConv.messages.at(-1).aborted),true);

        // The desktop API can also acknowledge startup after the first Stop call.
        await page.evaluate(() => {
            const conv = structuredClone(currentConv);
            window.nativeAbortCalls = 0;
            let started = false;
            window.pywebview = {api:{
                send_message: async () => {
                    await new Promise(resolve => setTimeout(resolve,200));
                    started = true;
                    return true;
                },
                abort_generation: async () => {
                    window.nativeAbortCalls++;
                    if (started) window.onStreamEvent({version:1,task_id:'native-qa',conversation_id:conv.id,
                        sequence:1,type:'aborted',data:{}});
                    return true;
                },
                load_conversation: async () => structuredClone(conv),
                load_conversations: async () => structuredClone(conversations),
                memory_operation: async () => ({success:true}),
            }};
        });
        await page.locator('#input-box').fill('native startup'); await page.locator('#send-btn').click();
        await page.locator('#send-btn').click();
        await page.waitForFunction(() => !isStreaming && !isSending);
        assert.equal(await page.evaluate(() => window.nativeAbortCalls),2);
        await page.evaluate(() => {delete window.pywebview;});
        assert.deepEqual(errors,[]);
        console.log('stream navigation browser regressions passed');
    } finally {
        if (browser) await browser.close();
        server.stdin.end();
        await new Promise(resolve => {
            if (server.exitCode !== null) return resolve();
            server.once('exit',resolve);
            setTimeout(() => {server.kill();resolve();},5000).unref();
        });
    }
})().catch(error => {console.error(error);process.exitCode=1;});
