/* ==================== 参数方案编辑器 ====================
 * 作为独立面板（非模态弹窗），左侧方案列表 + 右侧编辑表单。
 * 支持载入、保存、另存为、删除、校验提示。
 */
window.App = window.App || {};

App.ProfileState = {
    selectedName: '',
    loaded: false,
};

// -------------------- 面板初始化（切换到面板时调用）--------------------
App.initProfilesPanel = async function() {
    if (!App.ProfileState.loaded) {
        await App.refreshProfileList();
        App.ProfileState.loaded = true;
    }
};

App.refreshProfileList = async function() {
    const profiles = await App.apiGet('/api/profiles');
    const list = document.getElementById('profileEditList');
    if (!list) return;
    list.innerHTML = '';
    const names = profiles.names || [];
    if (!names.length) {
        list.innerHTML = '<div class="text-center">暂无方案</div>';
        return;
    }
    // 读取当前方案以高亮
    const config = await App.apiGet('/api/config');
    const current = config.current_profile || '';
    names.forEach(n => {
        const div = document.createElement('div');
        div.textContent = n === current ? `★ ${n}` : n;
        div.dataset.name = n;
        if (n === App.ProfileState.selectedName) div.classList.add('selected');
        list.appendChild(div);
    });
};

App.onProfileListClick = function(event) {
    const target = event.target;
    if (!target.dataset.name) return;
    App.loadProfileForEdit(target.dataset.name);
};

// -------------------- 载入方案到表单 --------------------
App.loadProfileForEdit = async function(name) {
    name = name || document.getElementById('editProfileName').value.trim();
    if (!name) return;
    const r = await App.apiGet('/api/profiles/get/' + encodeURIComponent(name));
    if (r.error) { alert(r.error); return; }
    const p = r.profile || {};
    App.ProfileState.selectedName = name;
    document.getElementById('editProfileName').value = name;
    document.getElementById('f_context_size').value = p.context_size ?? 0;
    document.getElementById('f_parallel').value = p.parallel ?? 1;
    document.getElementById('f_batch_size').value = p.batch_size ?? 512;
    document.getElementById('f_ubatch_size').value = p.ubatch_size ?? 512;
    document.getElementById('f_gpu_layers').value = p.gpu_layers ?? 0;
    document.getElementById('f_threads').value = p.threads ?? 0;
    document.getElementById('f_flash_attn').checked = !!p.flash_attn;
    document.getElementById('f_cont_batching').checked = !!p.cont_batching;
    document.getElementById('f_mlock').checked = !!p.mlock;
    document.getElementById('f_no_mmap').checked = !!p.no_mmap;
    document.getElementById('f_embedding').checked = !!p.embedding;
    document.getElementById('f_reranking').checked = !!p.reranking;
    document.getElementById('f_jinja').checked = !!p.jinja;
    document.getElementById('f_verbose').checked = !!p.verbose;
    document.getElementById('f_pooling').value = p.pooling || '';
    document.getElementById('f_chat_template').value = p.chat_template || '';
    document.getElementById('f_draft_model').value = p.draft_model || '';
    document.getElementById('f_grammar_file').value = p.grammar_file || '';
    document.getElementById('f_extra_args').value = p.extra_args || '';
    document.getElementById('deleteProfileBtn').disabled = false;
    // 刷新列表高亮
    App.refreshProfileList();
};

// -------------------- 新建方案（清空表单）--------------------
App.newProfile = function() {
    App.ProfileState.selectedName = '';
    document.getElementById('editProfileName').value = '';
    document.getElementById('f_context_size').value = 0;
    document.getElementById('f_parallel').value = 1;
    document.getElementById('f_batch_size').value = 512;
    document.getElementById('f_ubatch_size').value = 512;
    document.getElementById('f_gpu_layers').value = 0;
    document.getElementById('f_threads').value = 0;
    document.getElementById('f_flash_attn').checked = false;
    document.getElementById('f_cont_batching').checked = false;
    document.getElementById('f_mlock').checked = false;
    document.getElementById('f_no_mmap').checked = false;
    document.getElementById('f_embedding').checked = false;
    document.getElementById('f_reranking').checked = false;
    document.getElementById('f_jinja').checked = false;
    document.getElementById('f_verbose').checked = false;
    document.getElementById('f_pooling').value = '';
    document.getElementById('f_chat_template').value = '';
    document.getElementById('f_draft_model').value = '';
    document.getElementById('f_grammar_file').value = '';
    document.getElementById('f_extra_args').value = '';
    document.getElementById('deleteProfileBtn').disabled = true;
    document.getElementById('profileWarnings').style.display = 'none';
    App.refreshProfileList();
};

// -------------------- 保存方案 --------------------
App.saveProfileFromForm = async function() {
    const name = document.getElementById('editProfileName').value.trim();
    if (!name) { alert('请输入方案名'); return; }
    const profile = {
        context_size: parseInt(document.getElementById('f_context_size').value) || 0,
        parallel: parseInt(document.getElementById('f_parallel').value) || 0,
        batch_size: parseInt(document.getElementById('f_batch_size').value) || 0,
        ubatch_size: parseInt(document.getElementById('f_ubatch_size').value) || 0,
        gpu_layers: parseInt(document.getElementById('f_gpu_layers').value) || 0,
        threads: parseInt(document.getElementById('f_threads').value) || 0,
        flash_attn: document.getElementById('f_flash_attn').checked,
        cont_batching: document.getElementById('f_cont_batching').checked,
        mlock: document.getElementById('f_mlock').checked,
        no_mmap: document.getElementById('f_no_mmap').checked,
        embedding: document.getElementById('f_embedding').checked,
        reranking: document.getElementById('f_reranking').checked,
        jinja: document.getElementById('f_jinja').checked,
        verbose: document.getElementById('f_verbose').checked,
        pooling: document.getElementById('f_pooling').value,
        chat_template: document.getElementById('f_chat_template').value,
        draft_model: document.getElementById('f_draft_model').value,
        grammar_file: document.getElementById('f_grammar_file').value,
        extra_args: document.getElementById('f_extra_args').value,
    };
    const r = await App.apiPost('/api/profiles/save', { name, profile, set_current: true });
    const warnBox = document.getElementById('profileWarnings');
    if (r.error) { alert(r.error); return; }
    if (r.warnings && r.warnings.length) {
        warnBox.style.display = 'block';
        warnBox.innerHTML = r.warnings.map(w => `<div class="hc-warn">⚠ ${w}</div>`).join('');
    } else {
        warnBox.style.display = 'none';
    }
    App.ProfileState.selectedName = name;
    await App.loadProfiles();
    App.selectOption('profileSelect', name);
    await App.refreshProfileList();
    alert('方案已保存');
};

// -------------------- 删除方案 --------------------
App.deleteProfileFromUI = async function() {
    const name = document.getElementById('editProfileName').value.trim();
    if (!name) return;
    if (!confirm(`删除方案 "${name}"？`)) return;
    const r = await App.apiPost('/api/profiles/delete', { name });
    if (r.error) { alert(r.error); return; }
    await App.loadProfiles();
    App.newProfile();
    await App.refreshProfileList();
    alert('已删除');
};

// -------------------- grammar 文件选择器（原生）--------------------
App.pickGrammarFile = function() {
    App.pickPath('f_grammar_file', 'file', 'gbnf', '选择 GBNF 语法文件');
};

// -------------------- draft 模型选择器（原生）--------------------
App.pickDraftModel = function() {
    App.pickPath('f_draft_model', 'file', 'gguf', '选择草稿模型');
};
