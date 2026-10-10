/* Conversation memories, explicit historical backfill and durable task controls. */
window.MemoryDiscussions = function ({call, run, notice, refresh}) {
    const el = id => document.getElementById(id);
    const root = document.createElement('section');
    root.className = 'memory-discussions';
    root.innerHTML = `<details><summary>话题、项目进展与概览</summary>
      <p class="memory-description">保留讨论过程，区分助手建议和确认结论。历史补整理仅在预览并选择会话后启动，会调用你配置的模型。</p>
      <div class="memory-options">
        <label><input id="memory-episodes-enabled" type="checkbox">会话记忆</label>
        <label><input id="memory-episodes-auto" type="checkbox">闲置后自动整理新讨论</label>
        <label><input id="memory-tools-enabled" type="checkbox">允许模型按需查询</label>
      </div>
      <div class="memory-model-settings"><label>概览合并供应商<select id="memory-merge-provider"></select></label>
        <label>合并模型 ID<input id="memory-merge-model" maxlength="200" placeholder="留空跟随记忆提取模型"></label>
        <button id="memory-merge-save" class="btn btn-secondary" type="button">保存合并模型</button></div>
      <div class="memory-toolbar" role="group" aria-label="讨论记忆视图">
        <button id="memory-view-topics" type="button" class="btn btn-secondary">话题</button>
        <button id="memory-view-projects" type="button" class="btn btn-secondary">项目进展</button>
        <button id="memory-view-overview" type="button" class="btn btn-secondary">概览</button>
        <button id="memory-view-jobs" type="button" class="btn btn-secondary">后台任务</button>
        <button id="memory-view-usage" type="button" class="btn btn-secondary">记忆用量</button>
        <button id="memory-backfill-preview" type="button" class="btn btn-secondary">预览历史补整理</button>
      </div><div id="memory-discussion-content" class="memory-list" aria-live="polite"></div>
      <div id="memory-backfill-content"></div>
      <form id="memory-episode-editor" hidden>
        <label>话题<input id="memory-episode-topic" maxlength="160" required></label>
        <label>问题<textarea id="memory-episode-problem" rows="3"></textarea></label>
        <label>发现<textarea id="memory-episode-findings" rows="3"></textarea></label>
        <label>提出的方案<textarea id="memory-episode-proposed_solutions" rows="3"></textarea></label>
        <label>待解决事项<textarea id="memory-episode-open_questions" rows="3"></textarea></label>
        <label>已确认结论<textarea id="memory-episode-outcome" rows="3"></textarea></label>
        <p id="memory-episode-outcome-hint" class="memory-description"></p>
        <details><summary>高级：证据引用结构</summary><textarea id="memory-episode-json" rows="12" aria-label="编辑话题摘要"></textarea>
        <label><input id="memory-episode-use-json" type="checkbox">使用此结构保存</label></details>
        <button type="submit" class="btn btn-primary">保存修改</button>
        <button type="button" id="memory-episode-cancel" class="btn btn-secondary">取消</button></form>
      <div class="memory-toolbar"><button type="button" id="memory-jobs-pause" class="btn btn-secondary">暂停整理</button>
        <button type="button" id="memory-jobs-resume" class="btn btn-secondary">继续整理</button>
        <button type="button" id="memory-discussion-refresh" class="btn btn-secondary">刷新讨论状态</button></div>
    </details>`;
    el('memory-context-details').after(root);
    let view = 'topics', identity = '', generation = 0, editorEpisode = null;
    const scope = () => currentConvId ? {conv_id: currentConvId} : {};
    const paragraph = text => { const p = document.createElement('p'); p.textContent = text; return p; };
    function button(label, action) {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'btn btn-secondary';
        b.textContent = label; b.onclick = () => run(action); return b;
    }
    function card(title) {
        const article = document.createElement('article'); article.className = 'memory-card';
        const h = document.createElement('h3'); h.textContent = title; article.append(h); return article;
    }
    function claimText(item) {
        return [item.problem, item.findings, item.proposed_solutions, item.open_questions].flat().filter(Boolean)
            .map(c => `${c.kind || '未验证'}：${c.text}`).join('\n');
    }
    async function load() {
        const ticket = ++generation, cid = currentConvId, params = scope();
        const response = await call(view === 'usage' ? 'usage' : view === 'jobs' ? 'jobs' : view === 'topics' ? 'episodes' : 'overview', params);
        if (ticket !== generation || cid !== currentConvId) return;
        const container = el('memory-discussion-content'); container.replaceChildren();
        if (view === 'topics') {
            for (const item of response.episodes || []) {
                const article = card(item.topic);
                article.append(paragraph(`${item.origin === 'external' ? '外部导入 · 未验证' : item.status === 'resolved' ? '已确认结果' : '未解决 / 待验证'} · ${item.updated_at} · v${item.version}`));
                article.append(paragraph(claimText(item)));
                if (item.confirmed_outcome) article.append(paragraph(`结论：${item.confirmed_outcome.text}`));
                if (item.outcome_update?.operation === 'RETRACT') article.append(paragraph('旧成功结论已撤回，当前仍待解决。'));
                for (const warning of item.validation_warnings || []) article.append(paragraph(warning));
                if (item.origin !== 'external') article.append(button('查看原始会话', async () => { if (item.conv_id) {hideModal(el('memory-modal')); await selectConversation(item.conv_id);} }));
                const evidence = document.createElement('div'); article.append(evidence);
                article.append(button('查看证据与版本', async () => {
                    const params={...scope(),id:item.id};
                    const sources=await call('episode_sources',params), versions=await call('episode_versions',params);
                    evidence.replaceChildren();
                    for (const source of sources.sources) for (const row of source.excerpts)
                        evidence.append(paragraph(`${row.role} · ${row.source_id}\n${row.text}`));
                    for (const version of versions.versions) evidence.append(paragraph(
                        `历史版本 ${version.version} · ${version.updated_at}\n${claimText(version.payload)}\n${version.payload.confirmed_outcome?.text || ''}`));
                    if (!evidence.childNodes.length) evidence.append(paragraph('外部导入内容没有经过验证的本地来源。'));
                }));
                article.append(button('编辑摘要', async () => {
                    identity = item.id;
                    editorEpisode = item;
                    const fields = Object.fromEntries(['topic','problem','findings','proposed_solutions','open_questions','confirmed_outcome'].map(k => [k,item[k]]));
                    el('memory-episode-json').value = JSON.stringify(fields,null,2);
                    el('memory-episode-topic').value = item.topic;
                    for (const name of ['problem','findings','proposed_solutions','open_questions'])
                        el('memory-episode-'+name).value=(item[name]||[]).map(c=>c.text).join('\n');
                    el('memory-episode-outcome').value=item.confirmed_outcome?.text||'';
                    el('memory-episode-outcome').disabled=!item.confirmed_outcome;
                    el('memory-episode-outcome-hint').textContent=item.confirmed_outcome?
                        '修改结论描述时保留原始确认依据；清空后将标为未解决。':
                        '尚无用户确认或工具验证来源。可编辑未验证方案；确认结果后在原会话重新整理。';
                    el('memory-episode-use-json').checked=false;
                    el('memory-episode-editor').hidden = false;
                }));
                article.append(button('忘记话题', async () => {
                    // A second click on the concrete action confirms forgetting this topic.
                    if (article.dataset.forget !== item.id) {article.dataset.forget=item.id;notice('再次点击“忘记话题”确认；它引用的原始消息将停止参与历史参考，其他有独立来源的话题保留。');return;}
                    await call('episode_forget',{...scope(),id:item.id}); await load(); notice('已忘记话题');
                }));
                container.append(article);
            }
        } else if (view === 'usage') {
            const usage=response.usage, labels={facts:'用户事实提取',episode:'会话整理',merge:'概览合并',router:'检索路由',plan:'追加检索规划'};
            container.append(paragraph('工作区记忆请求用量（供应商已报告值）。未返回用量的请求标记未知，不代表免费；仅统计升级后的请求，旧消费无法补算。聊天内的部分规划用量已含在回答总数中，请勿直接相加。Embedding 和主聊天请求不属于本账本。'));
            for (const [category,label] of [['background','后台整理'],['chat','聊天内记忆查询']]) {
                const item=usage.categories[category], article=card(label);
                article.append(paragraph(`请求 ${item.requests} · 已报告输入 ${item.input_tokens} / 输出 ${item.output_tokens} tokens · 用量未知 ${item.unknown_requests} 次 · 未结束 ${item.running_requests} · 失败或中止 ${item.failed_requests}`));container.append(article);
            }
            for(const item of usage.stages||[]) container.append(paragraph(`${labels[item.stage]||item.stage} · 请求 ${item.requests} · 已报告输入 ${item.input_tokens} / 输出 ${item.output_tokens} · 用量未知 ${item.unknown_requests}`));
            for(const item of usage.recent_requests||[]) {
                const article=card(`${labels[item.stage]||item.stage} · ${item.status}`);
                article.append(paragraph(`${item.platform} / ${item.model} · ${item.started_at}\n输入 ${item.input_tokens===null?'未知':item.input_tokens} / 输出 ${item.output_tokens===null?'未知':item.output_tokens} tokens`));container.append(article);
            }
        } else if (view === 'jobs') {
            container.append(paragraph('任务数字仅表示会话整理/合并的累计值。全部记忆模型请求及未知用量请查看“记忆用量”，分别统计后台和聊天内查询。'));
            for (const job of response.jobs || []) {
                const row = document.createElement('article');row.className='memory-card';
                row.append(paragraph(`${job.kind === 'merge' ? '合并概览' : '会话整理'} · ${job.status} · ${job.cursor}/${job.total}${job.excluded_prefix_units ? ' · 此任务未处理较早的 '+job.excluded_prefix_units+' 个片段' : ''} · 输入 ${job.input_tokens} / 输出 ${job.output_tokens} tokens${job.error ? ' · '+job.error : ''}`));
                if (['error','paused'].includes(job.status)) row.append(button('重试此任务',async()=>{
                    const result=await call('job_resume',{id:job.id});await load();
                    notice(result.started?'此任务已继续，保留成功进度':'已有任务正在运行，请稍后再试');
                }));
                container.append(row);
            }
        } else {
            const overview = response.overview;
            container.append(paragraph(`可用事实 ${overview.coverage.saved_fact_count} · 话题 ${overview.coverage.available_topic_count} · ${overview.method === 'model' ? '模型已合并' : '目录回退'}`));
            if (view === 'overview') for (const fact of overview.profile) container.append(paragraph(fact.content));
            for (const group of overview.groups) {
                if (view === 'projects' && group.kind !== 'project') continue;
                const article = card(group.label);
                for (const id of group.episode_ids) {
                    const topic = overview.topics.find(t => t.id === id);
                    if (topic) article.append(button(`${topic.topic} · ${topic.status === 'resolved' ? '已确认' : '待解决'}`,async()=>{view='topics';await load();}));
                }
                container.append(article);
            }
        }
        if (!container.childNodes.length) container.append(paragraph('此范围还没有可用内容。可预览历史并选择需要补整理的会话。'));
    }
    async function preview() {
        const result = await call('job_preview'), area = el('memory-backfill-content'); area.replaceChildren();
        area.append(paragraph(`可选 ${result.preview.conversations.length} 个会话，总计约 ${result.preview.input_chars} 字 / ${result.preview.estimated_requests} 次摘要请求，另有低频概览合并。仅选中的范围会发送给记忆提取模型。`));
        const selections = [];
        for (const item of result.preview.conversations) {
            const label = document.createElement('label'), input = document.createElement('input');input.type='checkbox';
            input.checked = item.conv_id === currentConvId;
            label.append(input,document.createTextNode(` ${item.title} · ${item.input_chars} 字 · 约 ${item.estimated_requests} 次请求`));
            const row = paragraph('');row.append(label);area.append(row);selections.push({input,item});
        }
        area.append(button('开始所选会话的补整理',async()=>{
            const chosen = selections.filter(s=>s.input.checked).map(s=>s.item);
            if (!chosen.length) throw new Error('请先选择需要整理的会话');
            await call('job_start',{conv_ids:chosen.map(i=>i.conv_id),expected:Object.fromEntries(chosen.map(i=>[i.conv_id,i.source_hash]))});
            view='jobs';await load();notice('补整理已启动，可暂停或继续；模型用量显示在后台任务中');
        }));
    }
    for (const name of ['topics','projects','overview','jobs','usage']) el('memory-view-'+name).onclick=()=>run(async()=>{view=name;await load();});
    for (const [id,key] of [['memory-episodes-enabled','episodes_enabled'],['memory-episodes-auto','episode_auto_extract'],['memory-tools-enabled','memory_tools_enabled']])
        el(id).onchange=()=>run(async()=>{await call('options',{[key]:el(id).checked});await refresh();notice('讨论记忆设置已保存');});
    el('memory-merge-save').onclick=()=>run(async()=>{await call('options',{merge_platform:el('memory-merge-provider').value,merge_model:el('memory-merge-model').value.trim()});notice('概览合并模型已保存');});
    el('memory-jobs-pause').onclick=()=>run(async()=>{await call('job_pause');view='jobs';await load();});
    el('memory-jobs-resume').onclick=()=>run(async()=>{await call('job_resume');view='jobs';await load();});
    el('memory-backfill-preview').onclick=()=>run(preview);
    el('memory-discussion-refresh').onclick=()=>run(load);
    el('memory-episode-cancel').onclick=()=>{el('memory-episode-editor').hidden=true;};
    el('memory-episode-editor').onsubmit=event=>{event.preventDefault();run(async()=>{
        let changes;
        if(el('memory-episode-use-json').checked) changes=JSON.parse(el('memory-episode-json').value);
        else {
            changes={topic:el('memory-episode-topic').value.trim()};
            for(const name of ['problem','findings','proposed_solutions','open_questions']) {
                const old=editorEpisode[name]||[];
                changes[name]=el('memory-episode-'+name).value.split('\n').map(t=>t.trim()).filter(Boolean).map((text,index)=>{
                    const previous=old[index]||old.at(-1);
                    if(!previous && editorEpisode.origin!=='external') throw new Error('新增摘要段落需要对应原始证据；请在原会话补充后整理，或在高级结构中选择已有证据。');
                    return {...(previous||{kind:'external_unverified',source_ids:[]}),text};
                });
            }
            const outcome=el('memory-episode-outcome').value.trim();
            changes.confirmed_outcome=outcome && editorEpisode.confirmed_outcome ? {...editorEpisode.confirmed_outcome,text:outcome}:null;
        }
        await call('episode_edit',{...scope(),id:identity,expected_version:editorEpisode.version,changes});el('memory-episode-editor').hidden=true;await load();notice('摘要已保存，自动整理将保留手动修改');
    });};
    return {
        async fill(options) {
            for (const [id,key] of [['memory-episodes-enabled','episodes_enabled'],['memory-episodes-auto','episode_auto_extract'],['memory-tools-enabled','memory_tools_enabled']]) el(id).checked=!!options[key];
            const select=el('memory-merge-provider');select.replaceChildren();
            const follow=document.createElement('option');follow.value='';follow.textContent='跟随记忆提取模型';select.append(follow);
            for (const source of platformSelect.options) {const o=document.createElement('option');o.value=source.value;o.textContent=source.textContent;select.append(o);}
            select.value=options.merge_platform||'';el('memory-merge-model').value=options.merge_model||'';
            await load();
        },
        context(context) {
            for (const item of context.episodes||[]) el('memory-context-list').append(paragraph(`讨论话题「${item.topic}」：${item.confirmed_outcome?.text || '结论尚未确认'}`));
            for (const query of context.queries||[]) el('memory-context-list').append(paragraph(`模型追加查询 ${query.name} · ${query.success?'完成':'失败'} · ${query.chars} 字`));
        }
    };
};
