/* ==================== API 工具 + Session 管理 ====================
 * 全局命名空间 App：所有模块挂载于此。
 * apiGet/apiPost/apiDelete 自动注入 session token（sess-），
 * /v1/* 调用走 v1Fetch/v1Get/v1Post（使用 sk- API Key）。
 * 403 + auth_expired=true 时触发 onAuthExpired 回调（由 auth.js 设置）。
 */
window.App = window.App || {};

// -------------------- Session 存储 --------------------
App.SESSION_KEY = 'admin_session';
App.V1_KEY = 'v1_api_key';

App.getSession = function() {
    return localStorage.getItem(App.SESSION_KEY) || '';
};
App.setSession = function(token) {
    localStorage.setItem(App.SESSION_KEY, token);
};
App.clearSession = function() {
    localStorage.removeItem(App.SESSION_KEY);
};

// -------------------- auth expired 钩子（auth.js 覆写）--------------------
App.onAuthExpired = function() {
    // 默认行为：跳转登录页
    window.location.href = '/index.html';
};

// -------------------- /api/* 调用（注入 session）--------------------
App._apiHeaders = function(extra) {
    const h = { 'Content-Type': 'application/json' };
    const sess = App.getSession();
    if (sess) h['Authorization'] = 'Bearer ' + sess;
    return Object.assign(h, extra || {});
};

App._apiCheck = async function(resp) {
    // 403 + auth_expired → 触发登录过期
    if (resp.status === 403) {
        try {
            const body = await resp.json();
            if (body && body.auth_expired) {
                App.clearSession();
                App.onAuthExpired();
            }
            return body;
        } catch (e) {
            return { error: 'HTTP ' + resp.status };
        }
    }
    return null;
};

async function apiGet(url) {
    const resp = await fetch(url, { headers: App._apiHeaders() });
    const expired = await App._apiCheck(resp);
    if (expired) return expired;
    return await resp.json();
}

async function apiPost(url, data) {
    const resp = await fetch(url, {
        method: 'POST',
        headers: App._apiHeaders(),
        body: JSON.stringify(data || {}),
    });
    const expired = await App._apiCheck(resp);
    if (expired) return expired;
    return await resp.json();
}

async function apiDelete(url) {
    const resp = await fetch(url, {
        method: 'DELETE',
        headers: App._apiHeaders(),
    });
    const expired = await App._apiCheck(resp);
    if (expired) return expired;
    return await resp.json();
}

// 挂到命名空间
App.apiGet = apiGet;
App.apiPost = apiPost;
App.apiDelete = apiDelete;

// -------------------- /v1/* 调用（OpenAI 兼容，需 sk- Key）--------------------
App.getV1Key = function() {
    return localStorage.getItem(App.V1_KEY) || '';
};
App.saveV1Key = function(v) {
    localStorage.setItem(App.V1_KEY, v);
};
App.requireV1Key = function() {
    const k = App.getV1Key();
    if (!k) { alert('请先在 API 工作台填入 sk- 开头的 API Key'); return null; }
    return k;
};

async function v1Fetch(path, options) {
    const key = App.requireV1Key();
    if (!key) return null;
    const headers = Object.assign({
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + key,
    }, (options || {}).headers || {});
    return await fetch(path, Object.assign({}, options || {}, { headers }));
}

async function v1Get(path) {
    const resp = await v1Fetch(path);
    if (!resp) return { error: '无 API Key' };
    return await resp.json();
}

async function v1Post(path, data) {
    const resp = await v1Fetch(path, {
        method: 'POST',
        body: JSON.stringify(data || {}),
    });
    if (!resp) return { error: '无 API Key' };
    return await resp.json();
}

App.v1Fetch = v1Fetch;
App.v1Get = v1Get;
App.v1Post = v1Post;

// -------------------- 通用工具 --------------------
App.extractErrorMsg = function(r) {
    if (!r || !r.error) return '';
    if (typeof r.error === 'string') return r.error;
    if (typeof r.error === 'object') return r.error.message || JSON.stringify(r.error);
    return String(r.error);
};

App.copyText = function(text) {
    navigator.clipboard.writeText(text).then(() => {
    }).catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); } catch (e) {}
        document.body.removeChild(ta);
    });
};

App.escapeHtml = function(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
};

App.formatTime = function(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
};

App.selectOption = function(id, value) {
    const sel = document.getElementById(id);
    if (!sel) return;
    for (let i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === value) {
            sel.selectedIndex = i;
            return;
        }
    }
};
