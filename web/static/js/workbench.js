/* ==================== API 工作台 ====================
 * Tab 切换：Chat / Embeddings / Reranking / 模型 / 调用示例 / Prompt 模板
 * /v1/* 调用使用 sk- API Key（App.v1Get / App.v1Post）
 */
window.App = window.App || {};

App.WorkbenchState = {
    chatHistory: [],
    currentExamples: {},
    currentPrompts: [],
    selectedPromptName: '',
};

// -------------------- Tab 切换 --------------------
App.switchTab = function(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + name));
    if (name === 'examples') App.loadExamples();
    if (name === 'prompts') App.loadPrompts();
};

// -------------------- Chat --------------------
App.sendChat = async function() {
    if (!App.state.isRunning) { alert('请先启动 llama-server'); return; }
    const input = document.getElementById('chatInput').value.trim();
    if (!input) return;
    const sys = document.getElementById('chatSystem').value.trim();
    const messages = [];
    if (sys) messages.push({ role: 'system', content: sys });
    App.WorkbenchState.chatHistory.push({ role: 'user', content: input });
    messages.push(...App.WorkbenchState.chatHistory);
    document.getElementById('chatInput').value = '';
    App.renderChat();
    const payload = {
        messages,
        temperature: parseFloat(document.getElementById('chatTemp').value),
        max_tokens: parseInt(document.getElementById('chatMaxTokens').value),
        stream: document.getElementById('chatStream').value === 'true',
    };
    if (payload.stream) {
        const assistantMsg = { role: 'assistant', content: '' };
        App.WorkbenchState.chatHistory.push(assistantMsg);
        App.renderChat();
        try {
            const key = App.requireV1Key();
            if (!key) { assistantMsg.content = '[错误] 无 API Key'; App.renderChat(); return; }
            const resp = await fetch('/v1/chat/completions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + key,
                },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                assistantMsg.content = '[错误] ' + (App.extractErrorMsg(err) || resp.status);
                App.renderChat();
                return;
            }
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const data = line.slice(6);
                    if (data === '[DONE]') { buffer = ''; break; }
                    try {
                        const obj = JSON.parse(data);
                        const delta = obj.choices && obj.choices[0] && obj.choices[0].delta;
                        if (delta && delta.content) {
                            assistantMsg.content += delta.content;
                            App.renderChat();
                        }
                    } catch (e) { /* 忽略解析失败 */ }
                }
            }
        } catch (e) {
            assistantMsg.content = '[请求异常] ' + e;
            App.renderChat();
        }
    } else {
        try {
            const r = await App.v1Post('/v1/chat/completions', payload);
            if (!r) return;
            if (r.error) {
                const msg = App.extractErrorMsg(r);
                App.addLog('Chat 错误: ' + msg, 'err');
                App.WorkbenchState.chatHistory.push({ role: 'assistant', content: '[错误] ' + msg });
            } else {
                const content = (r.choices && r.choices[0] && r.choices[0].message && r.choices[0].message.content) || JSON.stringify(r);
                App.WorkbenchState.chatHistory.push({ role: 'assistant', content });
            }
        } catch (e) {
            App.WorkbenchState.chatHistory.push({ role: 'assistant', content: '[请求异常] ' + e });
        }
        App.renderChat();
    }
};

App.renderChat = function() {
    const win = document.getElementById('chatWindow');
    if (!win) return;
    win.innerHTML = '';
    App.WorkbenchState.chatHistory.forEach(m => {
        const div = document.createElement('div');
        div.className = `chat-msg ${m.role}`;
        div.innerHTML = `<div class="role">${m.role}</div><div class="content"></div>`;
        div.querySelector('.content').textContent = m.content;
        win.appendChild(div);
    });
    win.scrollTop = win.scrollHeight;
};

App.clearChat = function() {
    App.WorkbenchState.chatHistory = [];
    App.renderChat();
};

// -------------------- Embeddings --------------------
App.sendEmbeddings = async function() {
    if (!App.state.isRunning) { alert('请先启动 llama-server'); return; }
    const text = document.getElementById('embInput').value.trim();
    if (!text) { alert('请输入文本'); return; }
    const input = text.split('\n').map(s => s.trim()).filter(s => s);
    const out = document.getElementById('embOutput');
    out.textContent = '请求中…';
    const r = await App.v1Post('/v1/embeddings', { input });
    if (r.error) { out.textContent = '错误: ' + App.extractErrorMsg(r); return; }
    const data = (r.data || []).slice(0, 2).map(d => ({
        index: d.index, embedding_preview: (d.embedding || []).slice(0, 8),
        dims: (d.embedding || []).length
    }));
    out.textContent = JSON.stringify({ model: r.model, count: (r.data||[]).length, preview: data }, null, 2);
};

// -------------------- Reranking --------------------
App.sendRerank = async function() {
    if (!App.state.isRunning) { alert('请先启动 llama-server'); return; }
    const query = document.getElementById('rerankQuery').value.trim();
    const docs = document.getElementById('rerankDocs').value.split('\n').map(s => s.trim()).filter(s => s);
    if (!query || !docs.length) { alert('请输入查询和候选文档'); return; }
    const topN = parseInt(document.getElementById('rerankTopN').value) || 3;
    const out = document.getElementById('rerankOutput');
    out.textContent = '请求中…';
    const r = await App.v1Post('/v1/rerank', { query, documents: docs, top_n: topN });
    if (r.error) { out.textContent = '错误: ' + App.extractErrorMsg(r); return; }
    out.textContent = JSON.stringify(r.results || r, null, 2);
};

// -------------------- 模型 / 探活 --------------------
App.loadProxyModels = async function() {
    if (!App.state.isRunning) { alert('请先启动 llama-server'); return; }
    const out = document.getElementById('modelsOutput');
    out.textContent = '请求中…';
    const r = await App.v1Get('/v1/models');
    if (r.error) { out.textContent = '错误: ' + App.extractErrorMsg(r); return; }
    out.textContent = JSON.stringify(r, null, 2);
};

App.probeHealth = async function() {
    if (!App.state.isRunning) { alert('请先启动 llama-server'); return; }
    const span = document.getElementById('healthProbe');
    span.textContent = '探测中…';
    const r = await App.apiGet('/api/health');
    if (r.error) {
        span.textContent = '✗ 进程运行中，但 API 未就绪';
        span.style.color = 'var(--err)';
    } else {
        span.textContent = r.reachable ? '✓ API 可达' : '✗ API 不可达';
        span.style.color = r.reachable ? 'var(--ok)' : 'var(--err)';
    }
};

// -------------------- 调用示例 --------------------
App.loadExamples = async function() {
    try {
        App.WorkbenchState.currentExamples = await App.apiGet('/api/examples');
        App.showExample();
    } catch (e) {}
};

App.showExample = function() {
    const lang = document.getElementById('exampleLang').value;
    const code = App.WorkbenchState.currentExamples[lang] || '（点击"生成示例代码"）';
    document.getElementById('exampleCode').textContent = code;
};

App.copyExample = function() {
    const code = document.getElementById('exampleCode').textContent;
    if (code) App.copyText(code);
};

// -------------------- v1 Key 填入 --------------------
App.fillV1KeyFromList = function() {
    const k = prompt('粘贴 sk- 开头的 API Key 明文（可在「安全设置」创建后复制）：');
    if (k) {
        document.getElementById('v1ApiKey').value = k.trim();
        App.saveV1Key(k.trim());
    }
};

// -------------------- Prompt 模板管理 --------------------
App.loadPrompts = async function() {
    try {
        App.WorkbenchState.currentPrompts = await App.apiGet('/api/prompts');
        App.renderPromptList();
    } catch (e) {}
};

App.renderPromptList = function() {
    const list = document.getElementById('promptList');
    if (!list) return;
    list.innerHTML = '';
    if (!App.WorkbenchState.currentPrompts.length) {
        list.innerHTML = '<div class="text-center">暂无模板</div>';
        return;
    }
    App.WorkbenchState.currentPrompts.forEach(p => {
        const div = document.createElement('div');
        div.textContent = `[${p.category || '通用'}] ${p.name}`;
        div.dataset.name = p.name;
        if (p.name === App.WorkbenchState.selectedPromptName) div.classList.add('selected');
        list.appendChild(div);
    });
};

App.selectPrompt = function(event) {
    const target = event.target;
    if (!target.dataset.name) return;
    App.WorkbenchState.selectedPromptName = target.dataset.name;
    const p = App.WorkbenchState.currentPrompts.find(x => x.name === App.WorkbenchState.selectedPromptName);
    if (!p) return;
    document.getElementById('promptName').value = p.name;
    document.getElementById('promptCategory').value = p.category || '通用';
    document.getElementById('promptContent').value = p.content || '';
    App.renderPromptList();
};

App.openPromptEditor = function() {
    App.WorkbenchState.selectedPromptName = '';
    document.getElementById('promptName').value = '';
    document.getElementById('promptCategory').value = '通用';
    document.getElementById('promptContent').value = '';
    App.renderPromptList();
};

App.savePrompt = async function() {
    const name = document.getElementById('promptName').value.trim();
    const category = document.getElementById('promptCategory').value.trim() || '通用';
    const content = document.getElementById('promptContent').value;
    if (!name) { alert('请输入模板名称'); return; }
    const body = { name, category, content };
    if (App.WorkbenchState.selectedPromptName && App.WorkbenchState.selectedPromptName !== name) body.old_name = App.WorkbenchState.selectedPromptName;
    const r = await App.apiPost('/api/prompts', body);
    if (r.error) { alert(r.error); return; }
    App.WorkbenchState.currentPrompts = r;
    App.WorkbenchState.selectedPromptName = name;
    App.renderPromptList();
    alert('已保存');
};

App.deletePrompt = async function() {
    const name = App.WorkbenchState.selectedPromptName || document.getElementById('promptName').value.trim();
    if (!name) { alert('请先选择模板'); return; }
    if (!confirm(`删除模板 "${name}"？`)) return;
    const r = await App.apiDelete('/api/prompts/' + encodeURIComponent(name));
    if (r.error) { alert(r.error); return; }
    App.WorkbenchState.currentPrompts = r;
    App.WorkbenchState.selectedPromptName = '';
    App.openPromptEditor();
};

App.applyPromptToChat = function() {
    const content = document.getElementById('promptContent').value;
    if (!content) { alert('模板内容为空'); return; }
    document.getElementById('chatSystem').value = content;
    App.switchTab('chat');
};
