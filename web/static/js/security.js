/* ==================== 安全设置 ====================
 * 两部分：
 *   1. API Key 管理（CRUD + 启停 + 重命名）
 *   2. 管理员密码管理（修改密码，替代旧版"主控 Token"输入框）
 */
window.App = window.App || {};

App.SecurityState = {
    currentKeys: [],
    renameKeyId: '',
};

// -------------------- API Key 列表 --------------------
App.findKey = function(id) {
    return App.SecurityState.currentKeys.find(k => k.id === id) || {};
};

App.loadKeys = async function() {
    const keys = await App.apiGet('/api/keys');
    App.renderKeys(keys || []);
};

App.renderKeys = function(keys) {
    App.SecurityState.currentKeys = keys || [];
    const body = document.getElementById('keysBody');
    if (!body) return;
    if (!App.SecurityState.currentKeys.length) {
        body.innerHTML = '<tr><td colspan="7" class="text-center">暂无 API Key</td></tr>';
        return;
    }
    body.innerHTML = App.SecurityState.currentKeys.map(k => {
        const statusBadge = k.enabled
            ? '<span class="key-badge ok">启用</span>'
            : '<span class="key-badge off">已停用</span>';
        const toggleBtn = k.enabled
            ? `<button class="row-btn" onclick="App.toggleKey('${k.id}', false)">停用</button>`
            : `<button class="row-btn" onclick="App.toggleKey('${k.id}', true)">启用</button>`;
        return `<tr>
            <td>${App.escapeHtml(k.label)}</td>
            <td><code>${App.escapeHtml(k.prefix)}…</code>
                <button class="row-btn" onclick="App.copyKeyPrefix('${k.id}')" title="复制前缀">复制</button></td>
            <td><span class="key-badge">${App.escapeHtml(k.scope)}</span></td>
            <td>${statusBadge}</td>
            <td>${App.formatTime(k.created_at)}</td>
            <td>${App.formatTime(k.last_used_at)}</td>
            <td>
                ${toggleBtn}
                <button class="row-btn" onclick="App.openRenameKey('${k.id}')">重命名</button>
                <button class="row-btn danger" onclick="App.revokeKey('${k.id}')">回收</button>
            </td>
        </tr>`;
    }).join('');
};

// -------------------- 新建 Key --------------------
App.showCreateKeyDialog = function() {
    document.getElementById('newKeyLabel').value = '';
    document.getElementById('newKeyScope').value = 'admin';
    document.getElementById('keyFormView').style.display = '';
    document.getElementById('keyPlaintextView').style.display = 'none';
    document.getElementById('keyModalConfirm').style.display = '';
    document.getElementById('keyModalDone').style.display = 'none';
    document.getElementById('keyModalTitle').textContent = '新建 API Key';
    document.getElementById('keyModal').classList.add('show');
};

App.closeKeyModal = function() {
    document.getElementById('keyModal').classList.remove('show');
};

App.createKeyFromForm = async function() {
    const label = document.getElementById('newKeyLabel').value.trim();
    if (!label) { alert('请填写标签'); return; }
    const scope = document.getElementById('newKeyScope').value;
    const result = await App.apiPost('/api/keys', { label, scope });
    if (result.error) { alert(result.error); return; }
    const created = result.key;
    document.getElementById('newKeyPlaintext').textContent = created.plaintext;
    document.getElementById('newKeyMeta').textContent =
        `前缀：${created.prefix} · 作用域：${created.scope}`;
    document.getElementById('keyFormView').style.display = 'none';
    document.getElementById('keyPlaintextView').style.display = '';
    document.getElementById('keyModalConfirm').style.display = 'none';
    document.getElementById('keyModalDone').style.display = '';
    document.getElementById('keyModalTitle').textContent = 'Key 已创建';
    App.renderKeys(result.keys || []);
};

App.copyNewKey = function() {
    const txt = document.getElementById('newKeyPlaintext').textContent;
    if (txt) App.copyText(txt);
};

App.copyKeyPrefix = function(id) {
    const k = App.findKey(id);
    if (k.prefix) App.copyText(k.prefix);
};

// -------------------- 启停 / 回收 / 重命名 --------------------
App.toggleKey = async function(id, enabled) {
    const result = await App.apiPost('/api/keys/toggle', { id, enabled });
    if (result.error) { alert(result.error); return; }
    App.renderKeys(result.keys || []);
};

App.revokeKey = async function(id) {
    const k = App.findKey(id);
    const label = k.label || id;
    if (!confirm(`确认回收 Key「${label}」？此操作不可撤销，该 Key 将立即失效。`)) return;
    const result = await App.apiDelete('/api/keys/' + encodeURIComponent(id));
    if (result.error) { alert(result.error); return; }
    App.renderKeys(result.keys || []);
};

App.openRenameKey = function(id) {
    App.SecurityState.renameKeyId = id;
    const k = App.findKey(id);
    document.getElementById('renameKeyLabel').value = k.label || '';
    document.getElementById('keyRenameModal').classList.add('show');
};

App.closeKeyRenameModal = function() {
    document.getElementById('keyRenameModal').classList.remove('show');
    App.SecurityState.renameKeyId = '';
};

App.confirmRenameKey = async function() {
    const label = document.getElementById('renameKeyLabel').value.trim();
    if (!label) { alert('请填写新标签'); return; }
    if (!App.SecurityState.renameKeyId) { App.closeKeyRenameModal(); return; }
    const result = await App.apiPost('/api/keys/rename', { id: App.SecurityState.renameKeyId, label });
    if (result.error) { alert(result.error); return; }
    App.closeKeyRenameModal();
    App.renderKeys(result.keys || []);
};
