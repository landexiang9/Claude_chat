// Hybrid-memory controls. All supplied content is rendered as text, never HTML.
window.MemoryAdvanced = function ({overlay, call, run, notice, refresh}) {
    const el = id => document.getElementById(id);
    const settings = document.createElement('details');
    settings.id = 'memory-advanced-settings';
    settings.innerHTML = `<summary>语义检索、排序与上下文设置</summary>
      <p class="memory-description">已保存事实与历史索引保存在本地。配置远程 Embedding 后，允许参考的记忆及历史用户陈述会发送给该供应商建立索引；每次查询只嵌入问题并选取 Top-K。未配置或失败时使用关键词检索。</p>
      <div class="memory-grid">
        <label>Embedding 供应商<select id="memory-embedding-provider"></select></label>
        <div class="memory-embedding-model-field">
          <label>Embedding 模型 ID<input id="memory-embedding-model" maxlength="200" placeholder="获取后选择，也可手动填写"></label>
          <div class="memory-embedding-picker"><select id="memory-embedding-model-list" aria-label="远程 Embedding 模型"><option value="">手动填写模型 ID</option></select><button type="button" class="btn btn-secondary" id="memory-embedding-fetch">获取模型</button></div>
          <small id="memory-embedding-model-status" role="status"></small>
        </div>
        <label>向量维度（0 使用模型默认）<input id="memory-embedding-dimensions" type="number" min="0" max="8192" value="0"></label>
        <label>本地模型目录（可选）<input id="memory-embedding-path" maxlength="200" placeholder="需要可选 sentence-transformers 依赖"></label>
        <label>最多召回 Top-K<input id="memory-top-k" type="number" min="1" max="20" value="8"></label>
        <label>记忆提示词字符预算<input id="memory-budget" type="number" min="1000" max="12000" value="6000"></label>
        <label>衰减半衰期（天）<input id="memory-half-life" type="number" min="1" max="3650" value="180"></label>
        <label>语义相似度阈值<input id="memory-threshold" type="number" min="0" max="1" step="0.05" value="0.45"></label>
        <label>近期完整消息数<input id="memory-recent" type="number" min="4" max="100" value="12"></label>
      </div>
      <div class="memory-options"><label><input id="memory-summary" type="checkbox"> 长对话使用早期摘录摘要</label><label><input id="memory-router-model" type="checkbox"> 复杂问题使用模型辅助路由</label></div>
      <div class="memory-model-settings"><label>当前会话作用域<input id="memory-session-scope" maxlength="120" value="global" placeholder="global 或与记忆相同的项目标识"></label><button type="button" class="btn btn-secondary" id="memory-scope-save">保存会话作用域</button></div>
      <p class="memory-description">排序综合语义/关键词相关度（50%）、重要度（20%）、时间衰减（20%）、频次（10%）；置顶另加权。衰减只降低召回优先级。摘要保留用户与助手归属；含代码、工具或附件的早期上下文保留完整。模型路由默认关闭，开启后复杂查询可能额外调用记忆模型。</p>
      <div class="memory-card-actions"><button type="button" class="btn btn-primary" id="memory-advanced-save">保存检索设置</button><button type="button" class="btn btn-secondary" id="memory-index-start">建立 / 继续索引</button><button type="button" class="btn btn-secondary" id="memory-index-pause">暂停索引</button><button type="button" class="btn btn-secondary" id="memory-index-rebuild">重建索引</button><button type="button" class="btn btn-secondary" id="memory-index-refresh">刷新状态</button></div>
      <p id="memory-index-status" class="memory-description" role="status"></p>`;
    el('memory-context-details').after(settings);
    const profile = document.createElement('details');
    profile.id = 'memory-profile-details';
    profile.innerHTML = '<summary>结构化用户档案</summary><div id="memory-profile-list" class="memory-list"></div>';
    settings.after(profile);
    const conflicts = document.createElement('details');
    conflicts.id = 'memory-conflicts-details';
    conflicts.innerHTML = '<summary>待确认的记忆冲突 <span id="memory-conflict-count"></span></summary><div id="memory-conflicts-list" class="memory-list"></div>';
    profile.after(conflicts);
    const audit = document.createElement('details');
    audit.innerHTML = '<summary>记忆处理记录</summary><div id="memory-audit-list" class="memory-list"></div>';
    conflicts.after(audit);
    const fields = document.createElement('div'); fields.className = 'memory-grid';
    fields.innerHTML = `<label>结构字段（可留空）<input id="memory-subject" maxlength="120" placeholder="例如 user.os"></label>
      <label>作用域<input id="memory-scope" maxlength="120" value="global" placeholder="global 或项目标识"></label>
      <label>结构化值（JSON，可留空）<input id="memory-value" maxlength="2000" placeholder='例如 "Windows 11"'></label>
      <label>置信度<input id="memory-confidence" type="number" min="0" max="1" step="0.05" value="1"></label>
      <label>重要度<input id="memory-importance" type="number" min="0" max="1" step="0.05" value="0.7"></label>`;
    el('memory-content').after(fields);
    const number = id => Number(el(id).value);
    let modelRequest = 0, modelsLoaded = false, modelsLoading = false;
    function resetModels() {
        modelRequest++; modelsLoaded = false; modelsLoading = false;
        el('memory-embedding-model-list').replaceChildren(new Option('手动填写模型 ID', ''));
        const remote = !!el('memory-embedding-provider').value && el('memory-embedding-provider').value !== 'local';
        el('memory-embedding-fetch').disabled = !remote;
        el('memory-embedding-model-list').disabled = !remote;
        el('memory-embedding-model-status').textContent = remote ? '可远程获取模型；获取失败时仍可手动填写。' : '本地模型手动填写标识与目录；关键词检索无需模型。';
    }
    function syncModelSelection() {
        const value = el('memory-embedding-model').value.trim();
        el('memory-embedding-model-list').value = Array.from(el('memory-embedding-model-list').options).some(option=>option.value===value) ? value : '';
    }
    async function fetchModels() {
        const platform = el('memory-embedding-provider').value;
        if (!platform || platform === 'local' || modelsLoading) return;
        const request = ++modelRequest; modelsLoading = true;
        el('memory-embedding-fetch').disabled = true;
        el('memory-embedding-model-status').textContent = '正在从供应商获取 Embedding 模型…';
        try {
            const result = await call('embedding_models', {platform});
            if (request !== modelRequest || el('memory-embedding-provider').value !== platform) return;
            if (result.platform !== platform || !Array.isArray(result.models)) throw new Error('模型列表响应无效，仍可手动填写');
            const list = el('memory-embedding-model-list');
            list.replaceChildren(new Option('手动填写模型 ID', ''));
            const seen = new Set();
            for (const model of result.models) {
                if (!model || typeof model.id !== 'string' || !model.id || seen.has(model.id)) continue;
                seen.add(model.id);
                list.append(new Option(model.display_name && model.display_name !== model.id ? `${model.display_name} · ${model.id}` : model.id, model.id));
            }
            modelsLoaded = true; syncModelSelection();
            el('memory-embedding-model-status').textContent = seen.size ? `已获取 ${seen.size} 个模型，选择后保存检索设置。兼容服务的无能力标记列表按模型名称筛选。` : '供应商未返回可识别的 Embedding 模型，仍可手动填写。';
        } catch (error) {
            if (request !== modelRequest || el('memory-embedding-provider').value !== platform) return;
            el('memory-embedding-model-status').textContent = error.message || '获取失败，仍可手动填写模型 ID。';
        } finally {
            if (request === modelRequest) { modelsLoading = false; el('memory-embedding-fetch').disabled = false; }
        }
    }
    el('memory-embedding-fetch').onclick = fetchModels;
    el('memory-embedding-provider').onchange = ()=>{ resetModels(); fetchModels(); };
    el('memory-embedding-model-list').onchange = ()=>{
        if(el('memory-embedding-model-list').value) el('memory-embedding-model').value = el('memory-embedding-model-list').value;
    };
    el('memory-embedding-model').addEventListener('input', syncModelSelection);
    function renderIndex(index={}) {
        const status = index.running ? '后台处理中' : ({ready:'已就绪',error:'需重试',paused:'已暂停',disabled:'记忆已关闭',needs_rebuild:'模型配置已变更，需建立索引'}[index.status] || '未建立');
        el('memory-index-status').textContent = `${index.configured || index.status==='disabled' ? status : '未配置，使用关键词检索'} · ${index.indexed || 0}/${index.documents || 0} 条向量${index.dimensions ? ` · ${index.dimensions} 维` : ''}${index.error ? ` · ${index.error}` : ''}`;
    }
    async function status() { renderIndex((await call('index_status')).index); }
    function fill(options) {
        const previous = el('memory-embedding-provider').value;
        el('memory-embedding-provider').replaceChildren();
        for (const [value,label] of [['','关键词检索（不调用 Embedding）'],['local','本地模型'], ...Array.from(platformSelect.options).filter(o=>o.value==='gemini'||o.value.startsWith('custom:')).map(o=>[o.value,o.textContent])]) {
            const option=document.createElement('option');option.value=value;option.textContent=label;el('memory-embedding-provider').append(option);
        }
        el('memory-embedding-provider').value=options.embedding_platform || '';
        for(const [id,key,fallback] of [['memory-embedding-model','embedding_model',''],['memory-embedding-path','embedding_local_path',''],['memory-embedding-dimensions','embedding_dimensions',0],['memory-top-k','top_k',8],['memory-budget','budget_chars',6000],['memory-half-life','half_life_days',180],['memory-threshold','semantic_threshold',0.45],['memory-recent','recent_messages',12]])el(id).value=options[key] ?? fallback;
        if(previous !== el('memory-embedding-provider').value || !modelsLoaded && !modelsLoading) resetModels();
        syncModelSelection();
        el('memory-summary').checked=options.summary_enabled !== false;el('memory-router-model').checked=!!options.router_model_enabled;
    }
    function fillEditor(row={}) {
        el('memory-subject').value=row.subject || '';el('memory-scope').value=row.scope || 'global';
        el('memory-value').value=row.value_json && row.value_json !== 'null' ? row.value_json : '';
        el('memory-confidence').value=row.confidence ?? 1;el('memory-importance').value=row.importance ?? 0.7;
    }
    function editorData() {
        const data={subject:el('memory-subject').value.trim(),scope:el('memory-scope').value.trim() || 'global',confidence:number('memory-confidence'),importance:number('memory-importance')};
        if(el('memory-value').value.trim())data.value=JSON.parse(el('memory-value').value);
        return data;
    }
    function text(parent, value, tag='p') { const element=document.createElement(tag);element.textContent=value;parent.append(element);return element; }
    function button(parent, label, fn) { const b=document.createElement('button');b.type='button';b.className='btn btn-secondary btn-sm';b.textContent=label;b.onclick=()=>run(fn);parent.append(b); }
    async function loadPanels(rows=[]) {
        const results=await Promise.all([call('profile'),call('conflicts'),call('audit'),call('index_status')]);
        for(const id of ['memory-profile-list','memory-conflicts-list','memory-audit-list'])el(id).replaceChildren();
        for(const fact of results[0].profile || [])text(el('memory-profile-list'),`${fact.subject} [${fact.scope}] = ${fact.value_json} · v${fact.version} · 置信度 ${fact.confidence}`);
        if(!(results[0].profile || []).length)text(el('memory-profile-list'),'暂无结构化事实。');
        const pending=results[1].conflicts || [];el('memory-conflict-count').textContent=`(${pending.length})`;
        for(const conflict of pending){
            const card=document.createElement('article');card.className='memory-card';
            text(card, `${conflict.relation} · ${conflict.candidate.operation || 'UPDATE'} [${conflict.candidate.scope || 'global'}]：${conflict.candidate.content}`);
            const previous=rows.find(row=>row.id===conflict.memory_id);
            if(previous)text(card, `当前保存：${previous.content} · v${previous.version}`);
            text(card, `来源：${conflict.candidate.source_quote || '未提供'}`, 'blockquote');
            const actions=document.createElement('div');actions.className='memory-card-actions';
            button(actions,'接受',async()=>{await call('resolve',{id:conflict.id,accept:true});await refresh();notice('已接受更新');});
            button(actions,'拒绝',async()=>{await call('resolve',{id:conflict.id,accept:false});await refresh();notice('已拒绝，该候选不会自动再次写入');});
            card.append(actions);el('memory-conflicts-list').append(card);
        }
        if(!pending.length)text(el('memory-conflicts-list'),'没有待确认事项。');
        for(const item of results[2].audit || [])text(el('memory-audit-list'),`${item.created_at.slice(0,16)} · ${item.operation}/${item.relation} · ${item.reason}`);
        renderIndex(results[3].index);
    }
    function decorate(card,row,actions) {
        if(row.subject)text(card, `${row.subject} [${row.scope}] = ${row.value_json} · v${row.version} · 重要度 ${row.importance}`, 'small');
        button(actions,'时间版本',async()=>{
            const result=await call('versions',{id:row.id});
            let details=card.querySelector('.memory-versions');if(!details){details=document.createElement('div');details.className='memory-versions';card.append(details);}details.replaceChildren();
            for(const version of result.versions || [])text(details, `v${version.version} · ${version.valid_from.slice(0,10)} → ${version.valid_until.slice(0,10)}：${version.content} (${version.value_json})`);
            if(!(result.versions || []).length)text(details,'暂无历史版本。');
        });
    }
    function contextInfo(context={}) {
        if(!context.router)return;
        text(el('memory-context-list'),`${context.retrieval} · 路由 ${context.router.method} · ${context.candidates} 个相关候选 · ${context.chars} 字符${context.fallback ? ` · ${context.fallback}` : ''}`);
        for(const item of [...(context.memories || []),...(context.history || [])])if(item.score_components)text(el('memory-context-list'),`${item.content || item.title}：得分 ${item.score}；${JSON.stringify(item.score_components)}`);
    }
    el('memory-advanced-save').onclick=()=>run(async()=>{
        const data={embedding_platform:el('memory-embedding-provider').value,embedding_model:el('memory-embedding-model').value.trim(),embedding_dimensions:number('memory-embedding-dimensions'),embedding_local_path:el('memory-embedding-path').value.trim(),top_k:number('memory-top-k'),budget_chars:number('memory-budget'),half_life_days:number('memory-half-life'),semantic_threshold:number('memory-threshold'),recent_messages:number('memory-recent'),summary_enabled:el('memory-summary').checked,router_model_enabled:el('memory-router-model').checked};
        if(data.embedding_platform && !data.embedding_model)throw new Error('请填写 Embedding 模型 ID');
        await call('options',data);await refresh();notice('设置已保存；更换 Embedding 模型、维度或地址后请重建索引');
    });
    for(const [id,action] of [['memory-index-start','index_start'],['memory-index-pause','index_pause'],['memory-index-rebuild','index_rebuild']])el(id).onclick=()=>run(async()=>{const result=await call(action);await status();notice(action==='index_pause'?'已暂停，新批次不再发起':result.started===false?'已有索引任务正在运行':'后台索引已开始，可刷新查看进度');});
    el('memory-index-refresh').onclick=()=>run(status);
    el('memory-scope-save').onclick=()=>run(async()=>{
        if(!currentConvId)throw new Error('请先新建会话');
        await call('privacy',{conv_id:currentConvId,changes:{scope:el('memory-session-scope').value.trim() || 'global'}});
        await refresh();notice('会话作用域已保存');
    });
    let timer;
    settings.addEventListener('toggle',()=>{
        clearInterval(timer);
        if(settings.open && !modelsLoaded) fetchModels();
        if(settings.open)timer=setInterval(()=>{if(!overlay.classList.contains('hidden') && !el('memory-index-status').textContent.includes('未配置'))status().catch(()=>{});},5000);
    });
    return {fill,fillEditor,editorData,loadPanels,decorate,contextInfo,sessionPrivacy:privacy=>{el('memory-session-scope').value=privacy.scope || 'global';}};
};
