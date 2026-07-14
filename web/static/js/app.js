/* ==================== 主控台初始化 + 菜单 + 状态/启停/SSE ====================
 * app.html 加载后调用 App.initConsole()。
 * 菜单项通过 data-menu 属性绑定，切换面板显示/隐藏。
 */
window.App = window.App || {};

// -------------------- 全局状态 --------------------
App.state = {
    MAX_LOG_LINES: 500,
    SSE_MAX_DELAY: 15000,
    isRunning: false,
    isProcessing: false,
    eventSource: null,
    sseReconnectDelay: 1000,
    statusPollTimer: null,
    apiBaseUrl: '',
    currentMenu: 'dashboard',
};

// -------------------- 初始化 --------------------
App.initConsole = async function() {
    // 首屏守卫：未登录 → 跳登录页
    const authed = await App.requireAuth();
    if (!authed) return;

    // 菜单绑定
    document.querySelectorAll('.sidebar-nav .menu-item').forEach(item => {
        item.addEventListener('click', () => App.switchMenu(item.dataset.menu));
    });
    // 移动端侧边栏
    const toggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebarBackdrop');
    if (toggle) {
        toggle.addEventListener('click', () => {
            sidebar.classList.toggle('open');
            backdrop.classList.toggle('show');
        });
    }
    if (backdrop) backdrop.addEventListener('click', () => {
        sidebar.classList.remove('open'); backdrop.classList.remove('show');
    });
    // 登出按钮
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) logoutBtn.addEventListener('click', App.doLogout);

    // 路径变更监听
    const llamacppDir = document.getElementById('llamacppDir');
    const modelDir = document.getElementById('modelDir');
    const profilesPath = document.getElementById('profilesPath');
    if (llamacppDir) llamacppDir.addEventListener('change', App.onPathChanged);
    if (modelDir) modelDir.addEventListener('change', App.onPathChanged);
    if (profilesPath) profilesPath.addEventListener('change', (e) => App.onProfilesPathChanged(e.target.value));

    // v1 Key 输入
    const v1KeyInput = document.getElementById('v1ApiKey');
    if (v1KeyInput) {
        v1KeyInput.value = App.getV1Key();
        v1KeyInput.addEventListener('input', () => App.saveV1Key(v1KeyInput.value));
    }

    // 初始化各模块
    await App.loadConfig();
    await App.loadEndpoints();
    await App.loadKeys();

    // 服务状态
    const s = await App.apiGet('/api/status');
    App.updateStatus(s.running);
    if (s.running) {
        App.startLogStream();
        App.startStatusPoll();
    }
};

// -------------------- 菜单切换 --------------------
App.switchMenu = function(name) {
    App.state.currentMenu = name;
    document.querySelectorAll('.sidebar-nav .menu-item').forEach(item => {
        item.classList.toggle('active', item.dataset.menu === name);
    });
    document.querySelectorAll('.panel').forEach(p => {
        p.classList.toggle('active', p.id === 'panel-' + name);
    });
    // 模块懒加载：切换到时触发数据刷新
    if (name === 'workbench') {
        // 工作台：确保 endpoints 最新
        App.loadEndpoints();
    } else if (name === 'security') {
        App.loadKeys();
    } else if (name === 'profiles') {
        App.initProfilesPanel();
    } else if (name === 'version') {
        App.initVersionPanel();
    }
    // 移动端：选完菜单收起侧边栏
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebarBackdrop');
    if (sidebar && window.innerWidth <= 768) {
        sidebar.classList.remove('open');
        backdrop.classList.remove('show');
    }
};

// -------------------- 服务控制（启停合一）--------------------
App.toggleServer = async function() {
    if (App.state.isProcessing) return;
    App.state.isProcessing = true;
    try {
        if (App.state.isRunning) await App.stopServer();
        else await App.startServer();
    } finally {
        App.state.isProcessing = false;
    }
};

App.startServer = async function() {
    const model = document.getElementById('modelSelect').value;
    const profile = document.getElementById('profileSelect').value;
    const host = document.getElementById('hostInput').value;
    const port = document.getElementById('portInput').value;
    if (!model) { alert('请选择模型'); return; }
    if (!profile) { alert('请选择参数方案'); return; }

    const result = await App.apiPost('/api/start', { model, profile, host, port });
    if (result.error) { alert(result.error); return; }
    if (result.command) App.addLog(result.command, 'cmd');
    App.updateStatus(true);
    App.startLogStream();
    App.startStatusPoll();
    App.loadEndpoints();
};

App.stopServer = async function() {
    const result = await App.apiPost('/api/stop', { force: true });
    if (result.error) { alert(result.error); return; }
    App.addLog('[已强制停止]', 'rc');
    App.updateStatus(result.running);
    if (!result.running) App.stopStatusPoll();
    App.loadEndpoints();
};

App.updateStatus = function(running) {
    App.state.isRunning = running;
    const statusLabel = document.getElementById('statusLabel');
    const toggleBtn = document.getElementById('toggleBtn');
    const apiBadge = document.getElementById('apiBadge');
    if (running) {
        if (statusLabel) { statusLabel.textContent = '运行中'; statusLabel.className = 'status running'; }
        if (toggleBtn) { toggleBtn.textContent = '■ 停止'; toggleBtn.className = 'topbar-btn stop'; }
        if (apiBadge) apiBadge.disabled = false;
    } else {
        if (statusLabel) { statusLabel.textContent = '未运行'; statusLabel.className = 'status stopped'; }
        if (toggleBtn) { toggleBtn.textContent = '▶ 启动'; toggleBtn.className = 'topbar-btn start'; }
        if (apiBadge) { apiBadge.disabled = true; apiBadge.textContent = 'API: -'; }
    }
    App.loadEndpoints();
};

// -------------------- 状态轮询（仅运行时）--------------------
App.startStatusPoll = function() {
    if (App.state.statusPollTimer) clearInterval(App.state.statusPollTimer);
    App.state.statusPollTimer = setInterval(async () => {
        try {
            const status = await App.apiGet('/api/status');
            if (status.running !== App.state.isRunning) {
                App.updateStatus(status.running);
                if (!status.running) {
                    App.stopStatusPoll();
                    if (status.exit_code !== null && status.exit_code !== undefined) {
                        App.addLog(`[退出码 ${status.exit_code}]`, 'rc');
                    }
                }
            }
        } catch (e) { /* 轮询失败忽略，下次重试 */ }
    }, 3000);
};

App.stopStatusPoll = function() {
    if (App.state.statusPollTimer) {
        clearInterval(App.state.statusPollTimer);
        App.state.statusPollTimer = null;
    }
};

// -------------------- 日志流（SSE + 指数退避重连）--------------------
App.startLogStream = function() {
    if (App.state.eventSource) App.state.eventSource.close();
    // SSE 不走 apiGet（EventSource 不支持自定义 header）；
    // session token 通过 query string 传递
    const sess = App.getSession();
    const url = sess ? '/api/logs?token=' + encodeURIComponent(sess) : '/api/logs';
    App.state.eventSource = new EventSource(url);
    App.state.eventSource.onopen = () => { App.state.sseReconnectDelay = 1000; };
    App.state.eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        if (data.kind === 'rc') {
            App.addLog(`[退出 ${data.data}]`, 'rc');
            App.updateStatus(false);
        } else {
            App.addLog(data.data, data.kind);
        }
    };
    App.state.eventSource.onerror = function() {
        App.state.eventSource.close();
        if (App.state.isRunning) {
            setTimeout(() => {
                App.state.sseReconnectDelay = Math.min(
                    App.state.sseReconnectDelay * 2, App.state.SSE_MAX_DELAY);
                App.startLogStream();
            }, App.state.sseReconnectDelay);
        }
    };
};

App.addLog = function(text, kind) {
    const panel = document.getElementById('logPanel');
    if (!panel) return;
    const line = document.createElement('div');
    line.className = `log-line ${kind}`;
    line.dataset.kind = kind;
    line.textContent = text;
    const filter = document.getElementById('logFilter');
    const filterVal = filter ? filter.value : 'all';
    if (filterVal !== 'all' && filterVal !== kind) line.classList.add('hidden');
    panel.appendChild(line);
    while (panel.childElementCount > App.state.MAX_LOG_LINES) {
        panel.removeChild(panel.firstChild);
    }
    const autoscroll = document.getElementById('autoscroll');
    if (!autoscroll || autoscroll.checked) {
        panel.scrollTop = panel.scrollHeight;
    }
};

App.applyLogFilter = function() {
    const filter = document.getElementById('logFilter').value;
    document.querySelectorAll('#logPanel .log-line').forEach(l => {
        if (filter === 'all' || l.dataset.kind === filter) l.classList.remove('hidden');
        else l.classList.add('hidden');
    });
};

App.clearLogs = function() {
    const panel = document.getElementById('logPanel');
    if (panel) panel.innerHTML = '';
};

App.downloadLogs = function() {
    const lines = [];
    document.querySelectorAll('#logPanel .log-line').forEach(l => {
        lines.push(`[${l.dataset.kind}] ${l.textContent}`);
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `llama-server-${new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')}.log`;
    a.click();
    URL.revokeObjectURL(url);
};

// -------------------- API 端点信息 --------------------
App.loadEndpoints = async function() {
    try {
        const r = await App.apiGet('/api/endpoints');
        const badge = document.getElementById('apiBadge');
        const info = document.getElementById('endpointsInfo');
        const modelLabel = document.getElementById('apiModelLabel');
        App.state.apiBaseUrl = r.base_url || '';
        if (r.running) {
            if (badge) { badge.textContent = 'API: ' + r.base_url; badge.disabled = false; }
            if (info) info.innerHTML = `Base URL: <code>${r.base_url}</code> ` +
                `<button class="copy-btn" onclick="App.copyText('${r.base_url}')">复制</button>` +
                ` | Chat: <code>${r.endpoints.chat_completions}</code>` +
                ` | <button class="copy-btn" onclick="App.copyText('${r.endpoints.chat_completions}')">复制</button>` +
                ` <br><span class="info-line">需 Authorization: Bearer sk-...</span>`;
            if (modelLabel) modelLabel.textContent = r.model ? `模型: ${r.model}` : '';
        } else {
            if (badge) { badge.textContent = 'API: -'; badge.disabled = true; }
            if (info) info.textContent = '未运行（启动服务后显示 API 地址）';
            if (modelLabel) modelLabel.textContent = '';
        }
    } catch (e) { /* 忽略 */ }
};

App.copyApiBase = function() {
    if (App.state.apiBaseUrl) App.copyText(App.state.apiBaseUrl);
};
