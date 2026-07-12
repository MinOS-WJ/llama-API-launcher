/* ==================== 路径配置 + 原生文件选择器 ====================
 * 用 /api/pick 端点调起服务端原生 OS 文件/目录选择对话框，
 * 替代旧版 HTML 目录浏览器（用户要求"调用系统的，不需要自己搓"）。
 * 远程无头服务器无显示器时 /api/pick 返回空串，前端回退到手动输入。
 */
window.App = window.App || {};

// -------------------- 原生文件选择器 --------------------
App.pickPath = async function(inputId, type, filter, title) {
    title = title || (type === 'dir' ? '选择目录' : '选择文件');
    filter = filter || '';
    let url = `/api/pick?type=${encodeURIComponent(type)}&title=${encodeURIComponent(title)}`;
    if (filter) url += `&filter=${encodeURIComponent(filter)}`;

    const r = await App.apiGet(url);
    if (r.error) { alert(r.error); return; }
    if (!r.available) {
        alert('当前服务端环境不支持原生文件选择器（无显示器/无 GUI 工具）。\n请手动输入路径。');
        return;
    }
    if (r.path) {
        const input = document.getElementById(inputId);
        if (input) {
            input.value = r.path;
            // 触发 change 事件让监听器响应
            input.dispatchEvent(new Event('change'));
        }
    }
    // 用户取消选择时不做任何操作
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
