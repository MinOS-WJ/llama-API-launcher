/* ==================== 路径配置 + 文件选择弹窗 ====================
 * 浏览器按钮统一打开页面内文件/目录选择弹窗，通过 /api/browse 浏览服务端文件系统。
 */
window.App = window.App || {};

// -------------------- 页面内文件/目录选择器 --------------------
App.FilePickerState = {
    inputId: '',
    type: 'file',
    filter: '',
    title: '',
    currentPath: '',
};

App._pickerInitialPath = function(inputId, type) {
    const input = document.getElementById(inputId);
    const value = input ? input.value.trim() : '';
    if (!value) return '';
    if (type === 'dir') return value;
    return App._dirname(value);
};

App._dirname = function(path) {
    const normalized = String(path || '').replace(/\\/g, '/');
    const idx = normalized.lastIndexOf('/');
    if (idx < 0) return '';
    if (idx === 0) return path.slice(0, 1);
    if (idx === 2 && normalized[1] === ':') return path.slice(0, 3);
    return path.slice(0, idx);
};

App._joinPath = function(base, name) {
    if (!base) return name;
    if (base.endsWith('\\') || base.endsWith('/')) return base + name;
    const sep = base.includes('\\') ? '\\' : '/';
    return base + sep + name;
};

App._formatFileSize = function(size) {
    const n = Number(size) || 0;
    if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
    if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
    if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
    return n + ' B';
};

App.pickPath = async function(inputId, type, filter, title) {
    title = title || (type === 'dir' ? '选择目录' : '选择文件');
    App.FilePickerState = {
        inputId,
        type: type || 'file',
        filter: filter || '',
        title,
        currentPath: App._pickerInitialPath(inputId, type || 'file'),
    };
    const titleEl = document.getElementById('filePickerTitle');
    if (titleEl) titleEl.textContent = title;
    const pathInput = document.getElementById('filePickerPath');
    if (pathInput) pathInput.value = App.FilePickerState.currentPath;
    const chooseBtn = document.getElementById('filePickerChooseCurrent');
    if (chooseBtn) chooseBtn.style.display = App.FilePickerState.type === 'dir' ? '' : 'none';
    const useInputBtn = document.getElementById('filePickerUseInput');
    if (useInputBtn) useInputBtn.style.display = App.FilePickerState.type === 'file' ? '' : 'none';
    const modal = document.getElementById('filePickerModal');
    if (modal) modal.classList.add('show');
    await App.refreshFilePicker();
};

App.refreshFilePicker = async function(path) {
    const state = App.FilePickerState;
    if (path !== undefined) state.currentPath = path;
    const pathInput = document.getElementById('filePickerPath');
    if (pathInput) pathInput.value = state.currentPath || '';
    const list = document.getElementById('filePickerList');
    const status = document.getElementById('filePickerStatus');
    const chooseBtn = document.getElementById('filePickerChooseCurrent');
    const useInputBtn = document.getElementById('filePickerUseInput');
    if (!list) return;
    list.innerHTML = '<div class="text-center">加载中…</div>';
    if (status) status.textContent = '';
    if (chooseBtn) chooseBtn.disabled = state.type !== 'dir' || !state.currentPath;
    if (useInputBtn) useInputBtn.disabled = state.type !== 'file';

    let url = `/api/browse?path=${encodeURIComponent(state.currentPath || '')}`;
    if (state.type === 'file' && state.filter) {
        url += `&filter=${encodeURIComponent(state.filter)}`;
    }
    const r = await App.apiGet(url);
    if (r.error) {
        list.innerHTML = '';
        if (status) status.textContent = r.error;
        if (chooseBtn) chooseBtn.disabled = true;
        if (useInputBtn) useInputBtn.disabled = state.type !== 'file';
        return;
    }
    state.currentPath = r.path || '';
    if (pathInput) pathInput.value = state.currentPath;
    if (chooseBtn) chooseBtn.disabled = state.type !== 'dir' || !state.currentPath;
    if (useInputBtn) useInputBtn.disabled = state.type !== 'file';
    App.renderFilePickerEntries(r);
};

App.renderFilePickerEntries = function(result) {
    const state = App.FilePickerState;
    const list = document.getElementById('filePickerList');
    if (!list) return;
    list.innerHTML = '';

    if (result.parent || result.path) {
        const up = document.createElement('div');
        up.textContent = '..';
        up.dataset.path = result.parent || '';
        up.dataset.type = 'dir';
        up.className = 'picker-entry picker-dir';
        list.appendChild(up);
    }

    const entries = (result.entries || []).filter(e => state.type === 'file' || e.type === 'dir');
    if (!entries.length && !list.children.length) {
        list.innerHTML = '<div class="text-center">没有可选择的项目</div>';
        return;
    }

    entries.forEach(e => {
        const div = document.createElement('div');
        const fullPath = App._joinPath(result.path || '', e.name);
        div.dataset.path = fullPath;
        div.dataset.type = e.type;
        div.className = `picker-entry picker-${e.type}`;
        div.textContent = e.type === 'dir'
            ? `[目录] ${e.name}`
            : `[文件] ${e.name}  ${App._formatFileSize(e.size)}`;
        list.appendChild(div);
    });
};

App.onFilePickerClick = function(event) {
    const target = event.target.closest('[data-path]');
    if (!target) return;
    const path = target.dataset.path || '';
    const type = target.dataset.type || '';
    if (type === 'dir') {
        App.refreshFilePicker(path);
        return;
    }
    if (App.FilePickerState.type === 'file') {
        App.confirmFilePickerPath(path);
    }
};

App.confirmFilePickerCurrent = function() {
    const state = App.FilePickerState;
    if (state.type !== 'dir' || !state.currentPath) return;
    App.confirmFilePickerPath(state.currentPath);
};

App.confirmFilePickerManual = function() {
    const pathInput = document.getElementById('filePickerPath');
    const path = pathInput ? pathInput.value.trim() : '';
    App.refreshFilePicker(path);
};

App.confirmFilePickerInput = function() {
    const pathInput = document.getElementById('filePickerPath');
    const path = pathInput ? pathInput.value.trim() : '';
    if (path) App.confirmFilePickerPath(path);
};

App.confirmFilePickerPath = function(path) {
    const input = document.getElementById(App.FilePickerState.inputId);
    if (input) {
        input.value = path;
        input.dispatchEvent(new Event('change'));
    }
    App.closeFilePicker();
};

App.closeFilePicker = function() {
    const modal = document.getElementById('filePickerModal');
    if (modal) modal.classList.remove('show');
};

// -------------------- 配置加载 --------------------
App.loadConfig = async function() {
    const config = await App.apiGet('/api/config');
    const llamacppDir = document.getElementById('llamacppDir');
    const modelDir = document.getElementById('modelDir');
    const profilesPath = document.getElementById('profilesPath');
    const hostInput = document.getElementById('hostInput');
    const portInput = document.getElementById('portInput');
    if (llamacppDir) llamacppDir.value = config.llamacpp_dir || '';
    if (modelDir) modelDir.value = config.model_dir || '';
    if (profilesPath) profilesPath.value = config.profiles_path || '';
    if (hostInput) hostInput.value = config.host || '127.0.0.1';
    if (portInput) portInput.value = config.port || 8080;

    const detectLabel = document.getElementById('detectLabel');
    if (detectLabel) {
        detectLabel.textContent = config.detect_msg;
        detectLabel.className = `detect-label ${config.detect_status}`;
    }

    // 安全状态
    const authState = document.getElementById('authState');
    const remoteWarn = document.getElementById('remoteWarn');
    if (authState) authState.textContent =
        config.auth_enabled ? '已启用' : '未启用（仅本机）';
    if (remoteWarn) remoteWarn.style.display =
        config.remote_access ? 'block' : 'none';

    await App.loadModels();
    await App.loadProfiles();
    App.selectOption('modelSelect', config.last_model);
    App.selectOption('profileSelect', config.current_profile);
};

App.loadModels = async function() {
    const models = await App.apiGet('/api/models');
    const sel = document.getElementById('modelSelect');
    if (!sel) return;
    sel.innerHTML = '';
    models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        sel.appendChild(opt);
    });
};

App.loadProfiles = async function() {
    const profiles = await App.apiGet('/api/profiles');
    const sel = document.getElementById('profileSelect');
    if (!sel) return;
    sel.innerHTML = '';
    (profiles.names || []).forEach(n => {
        const opt = document.createElement('option');
        opt.value = n; opt.textContent = n;
        sel.appendChild(opt);
    });
};

// -------------------- 路径变更 --------------------
App.onPathChanged = async function() {
    const config = {
        llamacpp_dir: document.getElementById('llamacppDir').value,
        model_dir: document.getElementById('modelDir').value,
    };
    const result = await App.apiPost('/api/config', config);
    const detectLabel = document.getElementById('detectLabel');
    if (detectLabel) {
        detectLabel.textContent = result.detect_msg;
        detectLabel.className = `detect-label ${result.detect_status}`;
    }
    await App.loadModels();
};

App.onProfilesPathChanged = async function(path) {
    await App.apiPost('/api/profiles/path', { path });
    await App.loadProfiles();
};

// -------------------- 方案集选择 --------------------
App.showConfigsList = async function() {
    const files = await App.apiGet('/api/configs/list');
    const list = document.getElementById('configsList');
    if (!list) return;
    list.innerHTML = '';
    if (!files.length) {
        list.innerHTML = '<div class="text-center">configs 目录下暂无配置文件</div>';
    } else {
        files.forEach(f => {
            const div = document.createElement('div');
            div.textContent = f;
            div.dataset.filename = f;
            list.appendChild(div);
        });
    }
    document.getElementById('configsModal').classList.add('show');
};

App.closeConfigsList = function() {
    document.getElementById('configsModal').classList.remove('show');
};

App.selectConfigFile = async function(event) {
    const target = event.target;
    if (!target.dataset.filename) return;
    await App.apiPost('/api/configs/load/' + target.dataset.filename);
    await App.loadProfiles();
    await App.loadConfig();
    App.closeConfigsList();
};

// -------------------- 启动前健康检查 --------------------
App.runHealthcheck = async function() {
    const model = document.getElementById('modelSelect').value;
    const profile = document.getElementById('profileSelect').value;
    const host = document.getElementById('hostInput').value;
    const port = document.getElementById('portInput').value;
    const box = document.getElementById('hcBox');
    const summary = document.getElementById('hcSummary');
    if (!box) return;
    box.style.display = 'block';
    box.innerHTML = '<span class="hc-warn">检查中…</span>';
    const r = await App.apiGet(`/api/healthcheck?model=${encodeURIComponent(model)}&profile=${encodeURIComponent(profile)}&host=${encodeURIComponent(host)}&port=${port}`);
    let html = '';
    (r.errors || []).forEach(e => html += `<div class="hc-err">✗ ${e}</div>`);
    (r.warnings || []).forEach(w => html += `<div class="hc-warn">⚠ ${w}</div>`);
    if (r.ok && !r.errors.length) html += '<div class="hc-ok">✓ 全部检查通过</div>';
    box.innerHTML = html;
    if (summary) {
        summary.textContent = r.ok ? '检查通过' : `发现 ${r.errors.length} 个错误`;
        summary.style.color = r.ok ? 'var(--ok)' : 'var(--err)';
    }
};
