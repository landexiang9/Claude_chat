// Full application boot and streaming over real authenticated HTTP, isolated SQLite/config.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {spawn}=require('child_process');
const assert=require('assert/strict');
const python=process.env.PYTHON_EXECUTABLE || '.venv/Scripts/python.exe';
const fixture=String.raw`
import json, tempfile, sys, threading
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
import keyring
import claude_chat.config as settings
import claude_chat.db as database
from claude_chat.app import ClaudeChatApp
from claude_chat.api_bridge import WebAPI
from claude_chat.server import ThreadingHTTPServer, ClaudeChatHTTPHandler

def mock_stream(*args, **kwargs):
    args[8].put(('thinking', 'offline reasoning'))
    args[8].put(('text', 'Offline HTTP answer'))
    args[8].put(('done', {'input_tokens':2, 'output_tokens':3}))

retry_probe_used=False
def mock_memory(system,payload,abort,stage='episode'):
    global retry_probe_used
    if stage=='episode':
        if not retry_probe_used and any('QA retry pending' in m['text'] for m in payload['messages']):
            retry_probe_used=True
            abort.wait(20)
        user=next(m for m in payload['messages'] if m['evidence_role']=='user')
        result={'episodes':[{'topic':'离线讨论话题','problem':[{'text':'离线问题摘要',
            'kind':'user_statement','source_ids':[user['source_id']]}]}]}
    else: result={'groups':[]}
    return result,{'input_tokens':12,'output_tokens':3}

with tempfile.TemporaryDirectory(prefix='claude-browser-qa-') as directory, ExitStack() as stack:
    root=Path(directory)
    for module, names in [(settings, ['CONFIG_PATH','LOG_PATH','CONVERSATIONS_DIR','ATTACHMENT_STORE_DIR']),
                          (database, ['DB_PATH','CONVERSATIONS_DIR','BACKUP_DIR'])]:
        for name in names:
            stack.enter_context(patch.object(module, name, root / name.lower()))
    stack.enter_context(patch.object(keyring, 'get_password', return_value=None))
    stack.enter_context(patch.object(keyring, 'set_password'))
    stack.enter_context(patch.object(keyring, 'delete_password'))
    stack.enter_context(patch('claude_chat.services.model_service.fetch_available_models',
                               return_value=[{'id':'claude-test','display_name':'Offline Claude'}]))
    stack.enter_context(patch('claude_chat.services.conversation_service.stream_claude_response', side_effect=mock_stream))
    stack.enter_context(patch('claude_chat.memory_embeddings.fetch_embedding_models', return_value=[
        {'id':'gemini-embedding-2','display_name':'QA Embedding'},
        {'id':'qa-embedding','display_name':'<img src=x onerror=alert(1)> Embedding'}]))
    app=ClaudeChatApp(force_server=True)
    app.config.data.update({'security_token':'qa-token','model':'claude-test','api_key':'offline-key'})
    api=WebAPI(app)
    api.discussion_jobs().request=mock_memory
    api.memory_operation('options', {'auto_extract':False})
    ledger=api.memory_store().usage
    usage_id=ledger.begin('facts','qa-provider','offline-model')
    ledger.finish(usage_id,'success',{'input_tokens':12,'output_tokens':3})
    usage_id=ledger.begin('plan','qa-provider','offline-model')
    ledger.finish(usage_id,'error',{'input_tokens':7})
    server=ThreadingHTTPServer(('127.0.0.1',0),ClaudeChatHTTPHandler,app,api)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    print(json.dumps({'port':server.server_port}),flush=True)
    try: sys.stdin.read()
    finally: server.shutdown();server.server_close();worker.join(timeout=3)
`;
(async()=>{
 const server=spawn(python,['-u','-c',fixture],{cwd:process.cwd(),stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let diagnostics='';server.stderr.on('data',chunk=>diagnostics+=chunk);
 let browser;
 try {
  const port=await new Promise((resolve,reject)=>{
   let text='';const timeout=setTimeout(()=>reject(new Error('Fixture timeout: '+diagnostics)),15000);
   server.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(x=>x.startsWith('{"port"'));if(line){clearTimeout(timeout);resolve(JSON.parse(line).port);}});
   server.on('exit',code=>{clearTimeout(timeout);reject(new Error('Fixture exit '+code+': '+diagnostics));});
  });
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const base=`http://127.0.0.1:${port}`;
  await page.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  await page.goto(base+'/?token=qa-token');await page.waitForFunction(()=>isInitialized);
  assert(!page.url().includes('token='));
  const normalId=await page.evaluate(()=>currentConvId);
  await page.locator('#nav-memory').click();await page.locator('#memory-content').fill('我希望回答使用中文');
  await page.locator('#memory-subject').fill('user.language');await page.locator('#memory-value').fill('"zh-CN"');await page.locator('#memory-save').click();
  await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('已保存'));await page.locator('#memory-close').click();
  await page.locator('#input-box').fill('中文回答的偏好是什么？');await page.locator('#send-btn').click();
  await page.waitForFunction(()=>!isStreaming && !isSending && messageList.textContent.includes('Offline HTTP answer'));
  await page.locator('#memory-status-btn').click();await page.locator('#memory-context-details').click();
  await page.waitForFunction(()=>document.getElementById('memory-context-list').textContent.includes('我希望回答使用中文'));
  assert((await page.locator('#memory-context-list').textContent()).includes('关键词'));
  await page.locator('#memory-profile-details > summary').click();assert((await page.locator('#memory-profile-list').textContent()).includes('zh-CN'));
  await page.locator('#memory-advanced-settings > summary').click();
  const unauthorized=await page.request.post(base+'/api/memory/embedding_models',{data:{platform:'gemini'}});
  assert.equal(unauthorized.status(),401);
  await page.locator('#memory-embedding-model').fill('manual-embedding');
  await page.locator('#memory-embedding-provider').selectOption('gemini');
  await page.waitForFunction(()=>document.getElementById('memory-embedding-model-status').textContent.includes('已获取 2'));
  assert.equal(await page.locator('#memory-embedding-model').inputValue(),'manual-embedding');
  assert.equal(await page.locator('#memory-embedding-model-list img').count(),0);
  await page.locator('#memory-embedding-model-list').selectOption('gemini-embedding-2');
  assert.equal(await page.locator('#memory-embedding-model').inputValue(),'gemini-embedding-2');
  await page.locator('#memory-advanced-save').click();
  await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('设置已保存'));
  assert.equal(await page.evaluate(async()=> (await apiBridge.memory_operation('list')).options.embedding_model),'gemini-embedding-2');
  assert.equal(await page.locator('#memory-embedding-model-list').inputValue(),'gemini-embedding-2');
  // A late response from a previous provider cannot alter the new provider's UI.
  await page.evaluate(()=>{
   window.originalMemoryOperation=apiBridge.memory_operation;
   apiBridge.memory_operation=(action,data)=>action==='embedding_models'?new Promise(resolve=>window.resolveEmbeddingModels=resolve):window.originalMemoryOperation(action,data);
  });
  await page.locator('#memory-embedding-fetch').click();
  await page.waitForFunction(()=>!!window.resolveEmbeddingModels);
  await page.locator('#memory-embedding-provider').selectOption('local');
  await page.evaluate(()=>window.resolveEmbeddingModels({success:true,platform:'gemini',models:[{id:'stale-embedding'}]}));
  await page.waitForFunction(()=>document.getElementById('memory-embedding-model-status').textContent.includes('本地模型'));
  assert.equal(await page.locator('#memory-embedding-model-list option').count(),1);
  assert(await page.locator('#memory-embedding-fetch').isDisabled());
  await page.evaluate(()=>apiBridge.memory_operation=window.originalMemoryOperation);
  await page.route(base+'/api/memory/embedding_models',route=>route.fulfill({status:400,json:{success:false,error:'模拟获取失败，仍可手动填写'}}));
  await page.locator('#memory-embedding-provider').selectOption('gemini');
  await page.waitForFunction(()=>document.getElementById('memory-embedding-model-status').textContent.includes('模拟获取失败'));
  assert.equal(await page.locator('#memory-embedding-model').inputValue(),'gemini-embedding-2');
  assert(!(await page.locator('#memory-embedding-fetch').isDisabled()));
  await page.unroute(base+'/api/memory/embedding_models');
  await page.locator('#memory-embedding-fetch').click();
  await page.waitForFunction(()=>document.getElementById('memory-embedding-model-status').textContent.includes('已获取 2'));
  // Test mobile layout with the actual remote picker populated.
  await page.setViewportSize({width:390,height:844});
  assert(await page.locator('#memory-advanced-settings').evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  await page.locator('#memory-embedding-provider').selectOption('');
  await page.locator('#memory-embedding-model').fill('');
  await page.evaluate(()=>document.getElementById('memory-notice').textContent='');
  await page.locator('#memory-top-k').fill('5');await page.locator('#memory-budget').fill('4000');await page.locator('#memory-advanced-save').click();
  await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('设置已保存'));
  assert.equal(await page.evaluate(async()=> (await apiBridge.memory_operation('list')).options.top_k),5);
  await page.locator('.memory-discussions > details > summary').click();
  await page.locator('#memory-view-overview').click();
  await page.waitForFunction(()=>document.getElementById('memory-discussion-content').textContent.includes('可用事实'));
  assert.equal((await page.evaluate(async()=>apiBridge.memory_operation('jobs'))).jobs.length,0);
  await page.locator('#memory-backfill-preview').click();
  await page.waitForFunction(()=>document.getElementById('memory-backfill-content').textContent.includes('次摘要请求'));
  assert.equal((await page.evaluate(async()=>apiBridge.memory_operation('jobs'))).jobs.length,0,'Preview must not start paid work');
  await page.locator('#memory-backfill-content button').click();
  console.log('QA: explicit backfill started');
  await page.waitForFunction(async()=> (await apiBridge.memory_operation('jobs')).jobs.some(j=>j.kind==='episode'&&j.status==='completed'));
  await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('补整理已启动'));
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.locator('#memory-discussion-refresh').click();
  await page.waitForFunction(()=>document.getElementById('memory-discussion-content').textContent.includes('completed')).catch(async error=>{
   console.log('QA diagnostics',await page.evaluate(()=>({content:document.getElementById('memory-discussion-content').textContent,notice:document.getElementById('memory-notice').textContent})));throw error;
  });
  await page.locator('#memory-jobs-pause').click();
  await page.waitForFunction(async()=>!(await apiBridge.memory_operation('jobs')).running);
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.locator('#memory-close').click();
  await page.locator('#input-box').fill('QA retry pending：继续研究 PHP 的后续问题');await page.locator('#send-btn').click();
  await page.waitForFunction(()=>!isStreaming && !isSending && messageList.textContent.includes('QA retry pending'));
  await page.locator('#memory-status-btn').click();
  await page.locator('#memory-backfill-preview').click();
  await page.getByRole('button',{name:'开始所选会话的补整理'}).click();
  await page.waitForFunction(async()=> (await apiBridge.memory_operation('jobs')).jobs.some(j=>j.kind==='episode'&&j.status==='running'));
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  const retryId=await page.evaluate(async()=> (await apiBridge.memory_operation('jobs')).jobs.find(j=>j.kind==='episode'&&j.status==='running').id);
  await page.locator('#memory-jobs-pause').click();
  await page.waitForFunction(async id=>{const result=await apiBridge.memory_operation('jobs');return !result.running&&result.jobs.some(j=>j.id===id&&j.status==='paused');},retryId);
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.locator('#memory-view-jobs').click();
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.locator('#memory-discussion-content .memory-card').filter({hasText:'会话整理 · paused'}).getByRole('button',{name:'重试此任务',exact:true}).click();
  await page.waitForFunction(async id=> (await apiBridge.memory_operation('jobs')).jobs.some(j=>j.id===id&&j.status==='completed'),retryId);
  console.log('QA: selected paused task retried to completion');
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.locator('#memory-view-topics').click();
  console.log('QA: episode editing');
  const live=page.locator('#memory-discussion-content .memory-card').filter({has:page.locator('h3',{hasText:'离线讨论话题'})});
  await live.getByRole('button',{name:'编辑摘要',exact:true}).click();
  assert(await page.locator('#memory-episode-outcome').isDisabled());
  await page.locator('#memory-episode-problem').fill('人工修订的问题摘要');
  await page.locator('#memory-episode-editor button[type=submit]').click();
  await page.waitForFunction(()=>document.getElementById('memory-discussion-content').textContent.includes('人工修订的问题摘要'));
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await live.getByRole('button',{name:'查看证据与版本',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('memory-discussion-content').textContent.includes('历史版本 1'));
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  assert((await live.textContent()).includes('中文回答的偏好是什么')); // live source read over authenticated HTTP
  await live.getByRole('button',{name:'编辑摘要',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.locator('#memory-episode-problem').fill('尚未提交的旧编辑草稿');
  await page.evaluate(async()=>{
   const result=await apiBridge.memory_operation('episodes',{conv_id:currentConvId});const episode=result.episodes.find(e=>e.topic==='离线讨论话题');
   const saved=await apiBridge.memory_operation('episode_edit',{conv_id:currentConvId,id:episode.id,expected_version:episode.version,changes:{...episode,topic:'后台更新的话题'}});
   if(!saved.success)throw Error(saved.error);
  });
  await page.locator('#memory-episode-editor button[type=submit]').click();
  await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('版本冲突'));
  assert(await page.locator('#memory-episode-editor').isVisible());
  assert.equal(await page.locator('#memory-episode-problem').inputValue(),'尚未提交的旧编辑草稿');
  await page.locator('#memory-episode-cancel').click();
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.evaluate(async()=>{await apiBridge.memory_operation('save',{content:'用户使用 Windows 11',subject:'user.os',value:'Windows 11'});});
  await page.locator('#memory-import-file').setInputFiles({name:'synthetic-memory.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({version:3,memories:[{content:'用户使用 Linux',subject:'user.os',value:'Linux'}]}))});
  await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('预览尚未修改'));
  assert.equal(await page.locator('#memory-list select').inputValue(),'skip');
  assert.equal(await page.evaluate(async()=> (await apiBridge.memory_operation('profile')).profile.find(f=>f.subject==='user.os').value_json),'"Windows 11"');
  await page.locator('#memory-list select').selectOption('independent');
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.getByRole('button',{name:'按选择导入',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('已导入 1 条事实'));
  assert.equal(await page.evaluate(async()=> (await apiBridge.memory_operation('profile')).profile.filter(f=>f.subject==='user.os').length),2);
  console.log('QA: stale editor preserves draft; import preview preserves manual fact and allows independent copy');
  await page.evaluate(async()=>{const exported=await apiBridge.memory_operation('export');const result=await apiBridge.memory_operation('import',exported);if(!result.success)throw Error(result.error);});
  await page.locator('#memory-discussion-refresh').click();
  console.log('QA: external import editing');
  const external=page.locator('#memory-discussion-content .memory-card').filter({hasText:'外部导入 · 未验证'});
  await external.getByRole('button',{name:'编辑摘要',exact:true}).click();
  await page.locator('#memory-episode-topic').fill('外部导入修订');
  await page.locator('#memory-episode-editor button[type=submit]').click();
  await page.waitForFunction(()=>document.getElementById('memory-discussion-content').textContent.includes('外部导入修订'));
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  assert((await external.textContent()).includes('外部导入 · 未验证'));
  await external.getByRole('button',{name:'忘记话题',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await external.getByRole('button',{name:'忘记话题',exact:true}).click();
  await page.waitForFunction(()=>!document.getElementById('memory-discussion-content').textContent.includes('外部导入修订'));
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.getElementById('memory-modal').scrollWidth<=innerWidth));
  assert(await page.locator('#memory-context-list').evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  await page.waitForFunction(()=>document.getElementById('memory-modal').getAttribute('aria-busy')!=='true');
  await page.locator('#memory-view-usage').click();
  await page.waitForFunction(()=>document.getElementById('memory-discussion-content').textContent.includes('聊天内记忆查询'));
  const usageText=await page.locator('#memory-discussion-content').textContent();
  assert(usageText.includes('后台整理')&&usageText.includes('已报告输入 12 / 输出 3')&&usageText.includes('用量未知 1 次'));
  assert(usageText.includes('请勿直接相加')&&usageText.includes('输出 未知'));
  assert(await page.evaluate(()=>document.getElementById('memory-modal').scrollWidth<=innerWidth));
  const usageUnauthorized=await page.request.post(base+'/api/memory/usage',{data:{}});assert.equal(usageUnauthorized.status(),401);
  const usageSummary=await page.evaluate(async()=> (await apiBridge.memory_operation('usage')).usage);
  assert.equal(usageSummary.categories.chat.requests,1);assert.equal(usageSummary.categories.chat.unknown_requests,1);
  assert.equal(usageSummary.categories.background.input_tokens,12);
  console.log('QA: authenticated usage separates background/chat, preserves unknowns and fits mobile');
  await page.locator('#memory-session-scope').fill('qa-project');await page.locator('#memory-scope-save').click();
  await page.waitForFunction(()=>document.getElementById('memory-notice').textContent.includes('作用域已保存'));
  assert.equal(await page.evaluate(async()=> (await apiBridge.memory_operation('context',{conv_id:currentConvId})).privacy.scope),'qa-project');
  await page.locator('#temporary-chat').click();await page.waitForFunction(()=>ChatMemory.privacy.temporary);
  const tempId=await page.evaluate(()=>currentConvId);assert.notEqual(tempId,normalId);
  await page.locator('#input-box').fill('临时任务');await page.locator('#send-btn').click();
  await page.waitForFunction(()=>!isStreaming && !isSending && messageList.textContent.includes('Offline HTTP answer'));
  await page.evaluate(async()=>{const result=await apiBridge.memory_operation('context',{conv_id:currentConvId});if(result.context.memories.length)throw Error('Temporary memory leaked');});
  await page.evaluate(id=>selectConversation(id),normalId);
  assert.equal(await page.evaluate(async id=>apiBridge.load_conversation(id),tempId),null);
  await page.locator('#settings-btn').click();await page.waitForFunction(()=>!document.getElementById('model-request-json').disabled);
  await page.keyboard.press('Escape');assert.deepEqual(errors,[]);
  console.log('Full HTTP browser QA passed: boot, authentication, streaming, memory, embedding model discovery/selection/retry/stale protection/mobile and temporary cleanup');
 } finally {
  if(browser)await browser.close();server.stdin.end();
  await new Promise(resolve=>{if(server.exitCode!==null)return resolve();server.on('exit',resolve);setTimeout(()=>{server.kill();resolve();},5000).unref();});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
