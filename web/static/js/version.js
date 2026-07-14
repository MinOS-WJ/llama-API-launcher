/* ==================== 版本管理 + 回滚 ====================
 * 从 GitHub 获取发布列表，本地按 os_group 筛选资产，
 * 下载安装（进度轮询），回滚到 _backup 备份。
 */
window.App = window.App || {};

App.VersionState = {
    releases: [],
    currentAssets: [],
    selectedReleaseIndex: -1,
    selectedAssetIndex: -1,
    loaded: false,
    attempted: false,
    loading: false,
};

// -------------------- 初始化版本管理面板（切换到面板时调用）--------------------
App.initVersionPanel = async function() {
    await App.refreshCurrent();
    const platform = await App.apiGet('/api/platform');
    const osFilter = document.getElementById('osFilter');
    if (osFilter) osFilter.value = platform.os || '全部';
    await App.loadGithubToken();
    if (!App.VersionState.loaded && !App.VersionState.attempted) {
        await App.checkUpdates(false);
    } else {
        await App.refreshAssets();
    }
    await App.refreshBackupStatus();
};

// -------------------- GitHub Token 管理 --------------------
App.loadGithubToken = async function() {
    const config = await App.apiGet('/api/config');
    const input = document.getElementById('githubTokenInput');
    const status = document.getElementById('githubTokenStatus');
    if (input) input.value = config.github_token || '';
    if (status) {
        status.textContent = config.github_token_set ? '✓ Token 已设置' : '';
        status.style.color = config.github_token_set ? 'var(--ok)' : '';
    }
};

App.saveGithubToken = async function() {
    const input = document.getElementById('githubTokenInput');
    const status = document.getElementById('githubTokenStatus');
    const token = input ? input.value.trim() : '';
    const result = await App.apiPost('/api/config', { github_token: token });
    if (result.error) {
        alert('保存失败：' + result.error);
        return;
    }
    if (status) {
        status.textContent = token ? '✓ Token 已保存' : 'Token 已清除';
        status.style.color = token ? 'var(--ok)' : 'var(--muted)';
    }
};

// -------------------- 当前状态 --------------------
App.refreshCurrent = async function() {
    const config = await App.apiGet('/api/config');
    const label = document.getElementById('currentDirLabel');
    if (label) label.textContent =
        `当前目录：${config.llamacpp_dir || '（未设置）'}  —  ${config.detect_msg}`;
};

App.refreshBackupStatus = async function() {
    try {
        const r = await App.apiGet('/api/update/backup_status');
        const info = document.getElementById('backupInfo');
        const rb = document.getElementById('rollbackBtn');
        if (r.has_backup) {
            info.textContent = '✓ 存在 _backup 备份，可回滚到上个版本';
            info.style.color = 'var(--ok)';
            rb.disabled = r.running;
        } else {
            info.textContent = '✗ 无 _backup 备份（更新后才会生成）';
            info.style.color = 'var(--muted)';
            rb.disabled = true;
        }
    } catch (e) {}
};

// -------------------- 获取发布列表 --------------------
App.checkUpdates = async function(force) {
    if (App.VersionState.loading) return;
    App.VersionState.loading = true;
    const status = document.getElementById('updateStatus');
    status.textContent = '正在从 GitHub 获取发布列表…';
    document.getElementById('installBtn').disabled = true;
    try {
        App.VersionState.attempted = true;
        const url = '/api/releases?os=全部' + (force ? '&force=true' : '');
        const result = await App.apiGet(url);
        if (result.error) throw new Error(result.error);
        App.VersionState.releases = Array.isArray(result) ? result : [];
        App.VersionState.loaded = true;
        const list = document.getElementById('releaseList');
        list.innerHTML = '';
        if (!App.VersionState.releases.length) {
            list.innerHTML = '<div class="text-center">未获取到版本</div>';
            status.textContent = '未获取到版本';
            return;
        }
        App.VersionState.releases.forEach((r, i) => {
            const div = document.createElement('div');
            div.textContent = `${r.tag}  (${r.published})`;
            div.dataset.index = i;
            list.appendChild(div);
        });
        list.firstChild.classList.add('selected');
        App.VersionState.selectedReleaseIndex = 0;
        await App.refreshAssets();
        status.textContent = `共 ${App.VersionState.releases.length} 个版本`;
    } catch (e) {
        status.textContent = '获取失败';
        alert('获取失败：' + e);
    } finally {
        App.VersionState.loading = false;
    }
};

App.refreshAssets = async function() {
    const list = document.getElementById('assetList');
    list.innerHTML = '';
    App.VersionState.currentAssets = [];
    App.VersionState.selectedAssetIndex = -1;
    document.getElementById('installBtn').disabled = true;
    if (App.VersionState.selectedReleaseIndex < 0 ||
        !App.VersionState.releases[App.VersionState.selectedReleaseIndex]) return;

    const osFilter = document.getElementById('osFilter').value;
    const groupMap = { "Windows": "windows", "Linux": "linux",
                        "macOS": "macos", "其他": "others" };
    const targetGroup = osFilter === '全部' ? '' : (groupMap[osFilter] || '');

    const release = App.VersionState.releases[App.VersionState.selectedReleaseIndex];
    for (const a of release.assets) {
        if (targetGroup && a.os_group !== targetGroup) continue;
        const div = document.createElement('div');
        div.textContent = `${a.name}  [${a.variant}]  ${(a.size / 1024).toFixed(0)}KB`;
        div.dataset.index = App.VersionState.currentAssets.length;
        App.VersionState.currentAssets.push(a);
        list.appendChild(div);
    }
    if (App.VersionState.currentAssets.length > 0) {
        list.firstChild.classList.add('selected');
        App.VersionState.selectedAssetIndex = 0;
        document.getElementById('installBtn').disabled = false;
    } else {
        const empty = document.createElement('div');
        empty.className = 'text-center';
        empty.textContent = osFilter === '全部' ? '无资产' : `该版本无 ${osFilter} 资产`;
        list.appendChild(empty);
    }
};

App.selectRelease = function(event) {
    const target = event.target;
    if (!target.dataset.index) return;
    App.VersionState.selectedReleaseIndex = parseInt(target.dataset.index);
    document.querySelectorAll('#releaseList div').forEach(d => d.classList.remove('selected'));
    target.classList.add('selected');
    App.refreshAssets();
};

App.selectAsset = function(event) {
    const target = event.target;
    if (!target.dataset.index) return;
    App.VersionState.selectedAssetIndex = parseInt(target.dataset.index);
    document.querySelectorAll('#assetList div').forEach(d => d.classList.remove('selected'));
    target.classList.add('selected');
    document.getElementById('installBtn').disabled = false;
};

// -------------------- 下载并安装 --------------------
App.installAsset = async function() {
    if (App.VersionState.selectedAssetIndex < 0 ||
        !App.VersionState.currentAssets[App.VersionState.selectedAssetIndex]) return;
    const asset = App.VersionState.currentAssets[App.VersionState.selectedAssetIndex];
    const config = await App.apiGet('/api/config');
    if (!config.llamacpp_dir) { alert('未设置 llama.cpp 目录'); return; }

    const installBtn = document.getElementById('installBtn');
    const progressFill = document.getElementById('updateProgress');
    const status = document.getElementById('updateStatus');
    installBtn.disabled = true;
    document.getElementById('rollbackBtn').disabled = true;
    progressFill.style.width = '0%';
    status.textContent = '正在下载…';

    const result = await App.apiPost('/api/update', { url: asset.url, filename: asset.name });
    if (result.error) {
        alert(result.error);
        installBtn.disabled = false;
        status.textContent = '就绪';
        return;
    }

    const updateId = result.update_id;
    const checkProgress = async () => {
        const progress = await App.apiGet('/api/update/progress/' + updateId);
        if (progress.status === 'not_found') {
            status.textContent = '更新任务未找到';
            installBtn.disabled = false;
            return;
        }
        progressFill.style.width = progress.progress + '%';
        if (progress.message) status.textContent = progress.message;
        if (progress.status === 'done') {
            if (progress.success) {
                progressFill.style.width = '100%';
                status.textContent = '更新完成';
                alert('更新完成');
                await App.refreshCurrent();
                await App.refreshBackupStatus();
            } else {
                alert('更新失败：' + progress.message);
                status.textContent = '更新失败';
            }
            installBtn.disabled = false;
            return;
        }
        setTimeout(checkProgress, 500);
    };
    checkProgress();
};

// -------------------- 回滚 --------------------
App.rollbackVersion = async function() {
    if (!confirm('回滚到 _backup 中的上个版本？需先停止 llama-server。')) return;
    const progressFill = document.getElementById('updateProgress');
    const status = document.getElementById('updateStatus');
    const rb = document.getElementById('rollbackBtn');
    rb.disabled = true;
    progressFill.style.width = '0%';
    status.textContent = '准备回滚…';
    const result = await App.apiPost('/api/update/rollback', {});
    if (result.error) {
        alert(result.error);
        status.textContent = '就绪';
        rb.disabled = false;
        return;
    }
    const updateId = result.update_id;
    const checkProgress = async () => {
        const progress = await App.apiGet('/api/update/progress/' + updateId);
        if (progress.status === 'not_found') {
            status.textContent = '回滚任务未找到';
            rb.disabled = false;
            return;
        }
        progressFill.style.width = progress.progress + '%';
        if (progress.message) status.textContent = progress.message;
        if (progress.status === 'done') {
            if (progress.success) {
                progressFill.style.width = '100%';
                status.textContent = '回滚完成';
                alert('回滚完成：' + progress.message);
                await App.refreshCurrent();
                await App.refreshBackupStatus();
            } else {
                alert('回滚失败：' + progress.message);
                status.textContent = '回滚失败';
            }
            rb.disabled = false;
            return;
        }
        setTimeout(checkProgress, 500);
    };
    checkProgress();
};
