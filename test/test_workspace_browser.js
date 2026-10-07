// Isolated browser checks: no app initialization, personal DB, keys or network.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs/promises');
const path = require('path');
const assert = require('assert/strict');

(async () => {
    const browser = await chromium.launch({executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true});
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    const output=path.join(process.cwd(),'scratch','ui-review'); await fs.mkdir(output,{recursive:true});
    const capture = async options => {
        await page.waitForTimeout(300); // Allow drawer, theme and modal transitions to settle.
        await page.screenshot({...options, animations:'disabled'});
    };
    try {
        await page.route('**/*',async route=>{
            const url=new URL(route.request().url());if(url.hostname!=='qa.local')return route.abort();
            const name=decodeURIComponent(url.pathname).replace(/^\//,'')||'index.html';
            if(name.startsWith('api/'))return route.fulfill({json:{success:true}});
            let body=await fs.readFile(path.join(process.cwd(),'claude_chat/ui',name));
            if(name==='index.html')body=Buffer.from(body.toString().replace('<script src="main.js"></script>',''));
            const type={'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'}[path.extname(name)]||'application/octet-stream';
            await route.fulfill({body,contentType:type});
        });
        await page.goto('http://qa.local/');
        await page.evaluate(()=>{
            config={active_platform:'claude',model:'model-one',model_configs:{},system_prompts:[{id:'writer',name:'写作助手',content:'Write'}]};
            availableModels=[{id:'model-one',display_name:'Claude Sonnet',thinking_supported:true}];
            apiBridge.get_config=async()=>structuredClone(config);apiBridge.save_config=async()=>true;
            apiBridge.list_custom_providers=async()=>[];apiBridge.check_parsers=async()=>({});apiBridge.check_code_sandbox_environment=async()=>({ready:false});
            apiBridge.fetch_models=async()=>availableModels;
            const memories=[{id:'qa',content:'用户偏好中文回答，代码示例使用 Python。',category:'preference',enabled:1,pinned:1,origin:'manual',use_count:3,source_quote:'请优先使用中文'}];
            const options={enabled:true,auto_extract:true,history_enabled:true,extraction_platform:'',extraction_model:''};
            apiBridge.memory_operation=async(action,data)=>{
                if(action==='list')return {success:true,memories,options};
                if(action==='options'){Object.assign(options,data);return {success:true,options};}
                if(action==='context')return {success:true,privacy:{},context:{memories,history:[]}};
                if(action==='save'){memories.push({...data,id:'added'});return {success:true};}
                return {success:true,revisions:[]};
            };
            currentConvId='qa-conversation';currentConv={id:currentConvId,messages:[]};
            conversations=[{id:currentConvId,title:'界面设计与长期记忆'},{id:'other',title:'Python 工具开发'}];
            renderConversations();updateModelList(availableModels,'model-one',true);renderSystemPromptSelect();SelectPicker.refreshAll();
        });
        await page.evaluate(()=>ChatTheme.set('light'));await capture({path:path.join(output,'desktop-light.png')});
        assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');
        await page.locator('#theme-toggle-btn').click();assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark');
        await capture({path:path.join(output,'desktop-dark.png')});
        await page.evaluate(()=>{
            document.getElementById('chat-empty-state').classList.add('hidden');
            appendMessage('user','帮我设计一个支持长期记忆的聊天工具。','',false,0);
            appendMessage('assistant','可以分为三个部分：\n\n- 记忆管理\n- 历史检索\n- 隐私控制\n\n```python\nprint("Hello")\n```','先分析用户需求。',false,1);
        });
        await page.evaluate(()=>ChatTheme.set('light'));await capture({path:path.join(output,'messages-light.png')});
        assert.equal(await page.locator('.message-body').first().evaluate(e=>getComputedStyle(e).color),'rgb(15, 23, 42)');
        await page.locator('#nav-memory').click();await page.locator('#memory-content').fill('我希望答案简洁。');await page.locator('#memory-save').click();
        await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('已保存'));
        assert.equal(await page.locator('.memory-card').count(),2);
        await capture({path:path.join(output,'memory-light.png')});
        await page.locator('#memory-close').click();await page.locator('#settings-btn').click();
        await page.locator('[data-settings-nav="appearance"]').click();await page.waitForTimeout(450);await capture({path:path.join(output,'settings-light.png')});
        await page.keyboard.press('Escape');
        await page.setViewportSize({width:390,height:844});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        await capture({path:path.join(output,'mobile-light.png')});
        await page.locator('#mobile-more-btn').click();
        assert.equal(await page.locator('#mobile-more-btn').getAttribute('aria-expanded'),'true');
        await capture({path:path.join(output,'mobile-expanded.png')});
        const barBox=await page.locator('.top-bar').boundingBox();
        const deleteBox=await page.locator('#clear-chat-btn').boundingBox();
        assert(deleteBox.y+deleteBox.height <= barBox.y+barBox.height+2, 'Expanded mobile controls fit header');
        await page.keyboard.press('Escape');
        await page.locator('#sidebar-toggle-btn').click();await page.locator('#nav-memory').click();
        await capture({path:path.join(output,'memory-mobile.png')});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        await page.keyboard.press('Escape');
        await page.locator('#settings-btn').click();await capture({path:path.join(output,'settings-mobile.png')});await page.keyboard.press('Escape');
        await page.evaluate(()=>ChatTheme.set('dark'));await capture({path:path.join(output,'mobile-dark.png')});
        await page.reload();assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark');
        await page.emulateMedia({colorScheme:'light'});await page.evaluate(()=>ChatTheme.set('system'));assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');
        await page.emulateMedia({colorScheme:'dark'});await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
        await page.setViewportSize({width:390,height:500});
        await page.locator('#input-box').focus();
        const composer=await page.locator('.input-capsule').boundingBox();
        assert(composer.y+composer.height<=500, 'Composer fits reduced keyboard viewport');
        await page.setViewportSize({width:1440,height:900});
        await page.evaluate(()=>showArtifact('graph TD; A[Start] --> B[Done]', 'mermaid'));
        await page.waitForFunction(()=>document.querySelector('#artifacts-preview-container svg'));
        await page.evaluate(()=>ChatTheme.set('light'));
        await page.waitForFunction(()=>document.querySelector('#artifacts-preview-container svg'));
        assert.deepEqual(errors,[]);
        console.log('Workspace desktop/mobile, theme, memory and settings checks passed');
    } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
