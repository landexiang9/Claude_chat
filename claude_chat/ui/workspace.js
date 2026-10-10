// Shared workspace navigation and inspectable long-term memory controls.
(() => {
    const shell = document.querySelector('.app-container');
    function closeDrawer() { shell?.classList.remove('sidebar-open'); }
    document.getElementById('nav-chat')?.addEventListener('click', () => { closeDrawer(); inputBox.focus(); });
    document.getElementById('nav-settings')?.addEventListener('click', () => { closeDrawer(); showSettings(); });
    document.getElementById('nav-prompts')?.addEventListener('click', async () => { closeDrawer(); await showSettings(); activateSettingsNav('presets'); });
    document.getElementById('mobile-more-btn')?.addEventListener('click', event => {
        const open = document.querySelector('.top-bar').classList.toggle('mobile-tools-open');
        event.currentTarget.setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') { closeDrawer(); document.querySelector('.top-bar')?.classList.remove('mobile-tools-open'); document.getElementById('mobile-more-btn')?.setAttribute('aria-expanded','false'); }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault(); shell?.classList.add('sidebar-open'); conversationSearchInput?.focus();
        }
    });
    const overlay = document.createElement('div');
    overlay.id = 'memory-modal'; overlay.className = 'modal-overlay hidden';
    overlay.setAttribute('role','dialog'); overlay.setAttribute('aria-modal','true'); overlay.setAttribute('aria-labelledby','memory-title');
    overlay.innerHTML = `<div class="modal-card memory-modal-card">
        <div class="modal-header"><div><h2 id="memory-title">记忆中心</h2><p class="memory-description">管理 AI 记住的偏好与长期事实，查看来源及本次调用。</p></div><button type="button" class="icon-btn" id="memory-close" aria-label="关闭记忆中心">×</button></div>
        <div class="modal-body memory-body">
          <div class="memory-options">
            <label><input type="checkbox" id="memory-enabled"> 启用记忆功能</label>
            <label><input type="checkbox" id="memory-history"> 参考相关历史对话</label>
            <label><input type="checkbox" id="memory-auto"> 自动整理长期记忆</label>
          </div><p class="memory-description">自动整理在新增至少 3 条用户消息后调用所选模型，同一会话至少间隔 5 分钟，只提取用户陈述，可能产生额外 API 消耗。记忆保存在本机；启用后相关内容会发送给当前模型。</p>
          <div class="memory-model-settings"><label>记忆供应商<select id="memory-provider" aria-label="记忆供应商"></select></label><label>记忆模型 ID<input id="memory-model" maxlength="200" placeholder="留空使用该供应商的默认模型" aria-label="记忆模型 ID"></label><button type="button" class="btn btn-secondary" id="memory-model-save">保存选择</button></div>
          <div class="memory-session"><button type="button" class="btn btn-secondary" id="memory-session-toggle">当前会话：使用记忆</button><button type="button" class="btn btn-secondary" id="memory-history-toggle">当前会话：可供历史参考</button><button type="button" class="btn btn-secondary" id="temporary-chat">新建临时对话</button></div>
          <p class="memory-description">临时对话不读取或更新记忆，不进入历史参考；离开后删除，意外退出后在下次启动清理。</p>
          <details id="memory-context-details"><summary>本次使用的记忆与历史来源</summary><div id="memory-context-list"></div></details>
          <div class="memory-toolbar"><input type="search" id="memory-search" placeholder="搜索已保存记忆" aria-label="搜索记忆"><span id="memory-count"></span><button type="button" class="btn btn-secondary" id="memory-learn">现在整理</button></div>
          <div id="memory-list" class="memory-list"></div>
          <form id="memory-form" class="memory-editor"><h3 id="memory-editor-title">新增记忆</h3><textarea id="memory-content" rows="3" maxlength="1000" required placeholder="例如：我希望回答优先使用中文，代码示例使用 Python。" aria-label="记忆内容"></textarea>
          <div class="memory-editor-actions"><select id="memory-category" aria-label="记忆分类"><option value="preference">偏好</option><option value="profile">个人背景</option><option value="project">长期项目</option><option value="instruction">回答要求</option><option value="other">其他</option></select><button type="button" id="memory-edit-cancel" class="btn btn-secondary hidden">取消编辑</button><button type="submit" class="btn btn-primary" id="memory-save">保存记忆</button></div></form>
        </div><div class="modal-footer memory-footer"><span id="memory-notice" role="status" aria-live="polite"></span><div><button type="button" class="btn btn-secondary" id="memory-export">导出</button><button type="button" class="btn btn-secondary" id="memory-import">导入</button><input type="file" id="memory-import-file" accept="application/json,.json" hidden><button type="button" class="btn btn-secondary" id="memory-clear">清空记忆</button></div></div>
    </div>`;
    document.body.append(overlay);
    let rows = [], editing = null, manualSource = {}, options = {}, privacy = {}, privacyConvId = null, operationBusy = false;
    const el = id => document.getElementById(id);
    function notice(text, error=false) { el('memory-notice').textContent = text; el('memory-notice').style.color = error ? 'var(--red)' : 'var(--subtext0)'; }
    async function call(action, data={}) {
        const result = await apiBridge.memory_operation(action, data);
        if (!result?.success) throw new Error(result?.error || '记忆服务暂不可用');
        return result;
    }
    async function run(fn) {
        if (operationBusy) return;
        operationBusy = true;
        overlay.setAttribute('aria-busy','true');
        try { await fn(); } catch (error) { notice(error.message,true); }
        finally { operationBusy = false; overlay.setAttribute('aria-busy','false'); }
    }
    const advanced=window.MemoryAdvanced?.({overlay,call,run,notice,refresh});
    const discussions=window.MemoryDiscussions?.({call,run,notice,refresh});
    function reset() { editing=null; manualSource={}; el('memory-form').reset(); advanced?.fillEditor(); el('memory-editor-title').textContent='新增记忆'; el('memory-edit-cancel').classList.add('hidden'); }
    function render() {
        el('memory-list').replaceChildren();
        el('memory-count').textContent=`${rows.length} 条`;
        if (!rows.length) { const empty=document.createElement('p'); empty.className='memory-description'; empty.textContent='还没有匹配的记忆。可以手动添加，或开启自动整理。'; el('memory-list').append(empty); }
        for (const row of rows) {
            const card=document.createElement('article'); card.className='memory-card';
            const text=document.createElement('p'); text.textContent=row.content; card.append(text);
            const meta=document.createElement('div'); meta.className='memory-card-meta';
            meta.textContent=`${{preference:'偏好',profile:'个人背景',project:'长期项目',instruction:'回答要求',other:'其他'}[row.category] || '其他'} · ${row.origin==='automatic'?'自动整理':'手动保存'} · 已调用 ${row.use_count || 0} 次${row.enabled?'':' · 已停用'}`; card.append(meta);
            if(row.provenance==='external_unverified') meta.textContent+=' · 外部导入，未验证';
            if(row.source_valid===false) meta.textContent+=' · 来源不可用，不参与召回';
            if (row.source_quote) { const quote=document.createElement('blockquote'); quote.textContent=`来源陈述：${row.source_quote}`; card.append(quote); }
            const actions=document.createElement('div'); actions.className='memory-card-actions';
            const add=(label,fn)=>{const button=document.createElement('button');button.type='button';button.className='btn btn-secondary btn-sm';button.textContent=label;button.onclick=()=>run(fn);actions.append(button);};
            add('编辑',async()=>{editing=row;advanced?.fillEditor(row);el('memory-content').value=row.content;el('memory-category').value=row.category;el('memory-editor-title').textContent='编辑记忆';el('memory-edit-cancel').classList.remove('hidden');el('memory-content').focus();});
            add('全部来源',async()=>{const result=await call('fact_sources',{id:row.id});const area=document.createElement('div');
                for(const source of result.sources){const p=document.createElement('blockquote');p.textContent=`${source.conv_id}：${source.quote}`;area.append(p);}
                if(!result.sources.length)area.textContent='没有经过验证的本地用户来源；手动保存及外部笔记仍可单独管理。';
                card.append(area);});
            add(row.pinned?'取消置顶':'置顶',async()=>{await call('save',{...row,pinned:!row.pinned,enabled:!!row.enabled});await refresh();});
            add(row.enabled?'停用':'启用',async()=>{await call('save',{...row,pinned:!!row.pinned,enabled:!row.enabled});await refresh();});
            add('修改记录',async()=>{const result=await call('revisions',{id:row.id});notice(result.revisions.length?result.revisions.map(r=>`${r.changed_at.slice(0,10)}：${r.content}`).join('；'):'暂无修改记录');});
            if(row.source_conv_id) add('打开来源',async()=>{const conv=await apiBridge.load_conversation(row.source_conv_id);if(!conv)throw new Error('来源会话已删除');hideModal(overlay);await selectConversation(row.source_conv_id);});
            add('忘记',async()=>{if(!await confirmation('忘记这条记忆？','相关来源会从历史参考中排除，以避免再次提取。'))return;await call('forget',{id:row.id});reset();await refresh();notice('已忘记');});
            advanced?.decorate(card,row,actions);card.append(actions);el('memory-list').append(card);
        }
    }
    // Reuse a small accessible confirmation dialog, not browser confirm (WebView compatibility).
    function confirmation(title, description) {
        return new Promise(resolve=>{
            const dialog=document.createElement('div');dialog.className='modal-overlay';dialog.setAttribute('role','alertdialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label',title);
            const card=document.createElement('div');card.className='modal-card';card.style.maxWidth='420px';
            const body=document.createElement('div');body.className='modal-body';const heading=document.createElement('h3');heading.textContent=title;const desc=document.createElement('p');desc.className='memory-description';desc.textContent=description;body.append(heading,desc);
            const footer=document.createElement('div');footer.className='modal-footer';
            const finish=value=>{hideModal(dialog);dialog.remove();resolve(value);};
            for(const [label,value] of [['取消',false],['确认',true]]){const button=document.createElement('button');button.type='button';button.className='btn '+(value?'btn-primary':'btn-secondary');button.textContent=label;button.onclick=()=>finish(value);footer.append(button);}
            dialog.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();finish(false);}});dialog.addEventListener('click',event=>{if(event.target===dialog)finish(false);});card.append(body,footer);dialog.append(card);document.body.append(dialog);showModal(dialog);
        });
    }
    async function refresh() {
        const result=await call('list',{query:el('memory-search').value}); rows=result.memories;options=result.options;
        el('memory-provider').replaceChildren();
        const follow=document.createElement('option');follow.value='';follow.textContent='跟随当前供应商';el('memory-provider').append(follow);
        for(const source of platformSelect.options){const option=document.createElement('option');option.value=source.value;option.textContent=source.textContent;el('memory-provider').append(option);}
        el('memory-provider').value=options.extraction_platform || '';el('memory-model').value=options.extraction_model || '';
        el('memory-enabled').checked=options.enabled;el('memory-history').checked=options.history_enabled;el('memory-auto').checked=options.auto_extract;render();await updateContext();
        advanced?.fill(options);await advanced?.loadPanels(rows);
        await discussions?.fill(options);
    }
    async function updateContext() {
        el('memory-context-list').replaceChildren();
        const cid = currentConvId;
        if(!cid){privacy={};privacyConvId=null;return;}
        const result=await call('context',{conv_id:cid});
        if(currentConvId !== cid) return;
        privacy=result.privacy;privacyConvId=cid;
        advanced?.sessionPrivacy(privacy);
        el('memory-session-toggle').textContent=`当前会话：${privacy.temporary?'临时对话':privacy.memory_off?'不使用记忆':'使用记忆'}`;
        el('memory-session-toggle').disabled=!!privacy.temporary;el('memory-history-toggle').disabled=!!privacy.temporary;
        el('memory-history-toggle').textContent=`当前会话：${privacy.exclude_history?'不供历史参考':'可供历史参考'}`;
        const context=result.context;
        if(Number.isInteger(result.saved_count)){
            const p=document.createElement('p');
            p.textContent=`已保存记忆共 ${result.saved_count} 条；下面显示本轮召回的记忆和历史片段，并非全部聊天记录。`;
            el('memory-context-list').append(p);
        }
        for(const item of context.memories||[]){const p=document.createElement('p');p.textContent=item.content;el('memory-context-list').append(p);}
        for(const item of context.history||[]){const p=document.createElement('p');p.textContent=`历史「${item.title}」：${item.excerpt}`;el('memory-context-list').append(p);}
        if(!(context.memories||[]).length && !(context.history||[]).length){const p=document.createElement('p');p.textContent='本轮没有调用记忆或历史参考。';el('memory-context-list').append(p);}
        if(result.learning?.error){const p=document.createElement('p');p.textContent=`上次整理：${result.learning.error}`;el('memory-context-list').append(p);}
        if(result.learning && Number.isInteger(result.learning.pending_messages)){
            const p=document.createElement('p');
            p.textContent=`当前会话已整理 ${result.learning.last_count} 个用户消息片段，待整理 ${result.learning.pending_messages} 个。长消息分片处理；整理成功也可能未发现需要长期保存的事实。`;
            el('memory-context-list').append(p);
        }
        advanced?.contextInfo(context);
        discussions?.context(context);
        el('memory-status-btn').textContent=privacy.temporary?'临时对话':privacy.memory_off?'记忆关闭':`记忆 ${(context.memories||[]).length} · 历史 ${(context.history||[]).length}`;
    }
    async function open(content) { closeDrawer();showModal(overlay);notice('');await run(async()=>{await refresh();if(typeof content==='string'){reset();manualSource={source_conv_id:currentConvId,source_quote:content.slice(0,1000)};el('memory-content').value=content.slice(0,1000);el('memory-content').focus();}}); }
    window.ChatMemory={open,refreshContext:()=>updateContext().catch(()=>{}),get privacy(){return privacyConvId === currentConvId ? privacy : {};}};
    el('nav-memory').onclick=()=>open();el('memory-status-btn').onclick=()=>open();el('memory-close').onclick=()=>hideModal(overlay);
    el('memory-edit-cancel').onclick=reset;
    el('memory-form').onsubmit=event=>{event.preventDefault();run(async()=>{await call('save',{...(editing||manualSource),...advanced?.editorData(),content:el('memory-content').value,category:el('memory-category').value,pinned:!!editing?.pinned,enabled:editing?!!editing.enabled:true});reset();await refresh();notice('记忆已保存');});};
    for(const [id,key] of [['memory-enabled','enabled'],['memory-history','history_enabled'],['memory-auto','auto_extract']]) el(id).onchange=()=>run(async()=>{
        if(key==='auto_extract'&&el(id).checked&&!await confirmation('开启自动记忆？','每新增至少 3 条用户消息、同一会话至少间隔 5 分钟，会调用所选模型整理长期事实，可能产生额外 API 消耗。')){el(id).checked=false;return;}
        await call('options',{[key]:el(id).checked});await refresh();notice('设置已保存');
    });
    let searchTimer;el('memory-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>run(refresh),200);};
    el('memory-session-toggle').onclick=()=>run(async()=>{await call('privacy',{conv_id:currentConvId,changes:{memory_off:!privacy.memory_off}});await updateContext();});
    el('memory-history-toggle').onclick=()=>run(async()=>{await call('privacy',{conv_id:currentConvId,changes:{exclude_history:!privacy.exclude_history}});await updateContext();});
    el('memory-model-save').onclick=()=>run(async()=>{await call('options',{extraction_platform:el('memory-provider').value,extraction_model:el('memory-model').value.trim()});await refresh();notice('记忆提取模型已保存');});
    el('temporary-chat').onclick=()=>run(async()=>{if(isStreaming||isSending)throw new Error('请先停止生成');const result=await call('new_temporary');await loadConversations();await selectConversation(result.conversation.id);hideModal(overlay);statusLabel.textContent='临时对话：不读写记忆，离开后删除';});
    el('memory-learn').onclick=()=>run(async()=>{await call('extract',{conv_id:currentConvId});notice('已开始后台整理，稍后刷新查看结果；长会话分批处理，可查看待整理消息数并继续整理');});
    el('memory-clear').onclick=()=>run(async()=>{if(!await confirmation('清空全部记忆？','此操作无法撤销，现有会话也会从历史参考中排除。建议先导出。'))return;await call('clear');reset();await refresh();notice('已清空，已有历史不再参与参考');});
    el('memory-export').onclick=()=>run(async()=>{const result=await call('export');const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='chatudex-memories.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    el('memory-import').onclick=()=>el('memory-import-file').click();el('memory-import-file').onchange=event=>run(async()=>{
        const file=event.target.files[0];if(!file)return;
        if(file.size>32*1024*1024)throw new Error('导入文件不能超过 32 MB');
        const data=JSON.parse(await file.text());event.target.value='';
        const {preview}=await call('import_preview',data);
        async function commit(decisions={}) {
            const result=await call('import',{...data,_import_decisions:decisions});
            await refresh();notice(`已导入 ${result.count} 条事实；冲突按你的选择处理，话题保持外部未验证来源`);
        }
        if(!preview.conflicts.length){await commit();return;}
        const area=el('memory-list');area.replaceChildren();
        const summary=document.createElement('p');summary.textContent=`导入 ${preview.total} 条事实，其中 ${preview.conflicts.length} 条与现有记忆冲突。默认跳过，不会覆盖已有内容。`;area.append(summary);
        const selections=[];
        for(const conflict of preview.conflicts) {
            const row=document.createElement('div');row.className='memory-card';
            const current=document.createElement('p');current.textContent=`现有：${conflict.existing_content}`;
            const incoming=document.createElement('p');incoming.textContent=`导入：${conflict.incoming_content}`;
            const label=document.createElement('label');label.textContent='处理方式 ';
            const select=document.createElement('select');select.className='form-select';
            for(const [value,text] of [['skip','跳过（保留现有）'],['replace','替换（解除旧来源，标记外部未验证）'],['independent','独立保存（保留两条）']]) {
                const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);
            }
            label.append(select);row.append(current,incoming,label);area.append(row);selections.push({conflict,select});
        }
        const apply=document.createElement('button');apply.type='button';apply.className='btn btn-primary';apply.textContent='按选择导入';
        apply.onclick=()=>run(()=>commit(Object.fromEntries(selections.map(({conflict,select})=>[String(conflict.index),{action:select.value,memory_id:conflict.memory_id,expected_version:conflict.expected_version}]))));
        const cancel=document.createElement('button');cancel.type='button';cancel.className='btn btn-secondary';cancel.textContent='取消导入';cancel.onclick=()=>run(refresh);
        area.append(apply,cancel);notice('请核对冲突；预览尚未修改任何记忆');
    });
})();
