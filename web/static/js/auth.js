/* ==================== 管理员认证（登录 / 登出 / 首屏判断）====================
 * 首屏流程（index.html）：
 *   1. 调 /api/auth/status 获取状态
 *   2. 若 session_valid → 已登录，跳转 app.html
 *   3. 若 !password_set && is_local → 首次设置，显示 setup 表单
 *   4. 若 !password_set && !is_local → 提示需本机设置
 *   5. 若 password_set → 显示登录表单
 * 登出流程（app.html）：
 *   调 /api/auth/logout → 清 session → 跳 index.html
 */
window.App = window.App || {};

// -------------------- 首屏判断（index.html 用）--------------------
App.handleAuthRedirect = async function() {
    const status = await App.apiGet('/api/auth/status');

    // 已有有效 session → 直接进主控台
    if (status.session_valid) {
        window.location.href = '/app.html';
        return;
    }

    // 密码未初始化
    if (!status.password_set) {
        if (status.is_local) {
            // 本机 → 显示首次设置表单
            App._showSetupView();
        } else {
            // 远程 → 无法设置，提示
            App._showRemoteNoPasswordView();
        }
        return;
    }

    // 密码已设但 session 无效 → 显示登录表单
    App._showLoginView();
};

// -------------------- 视图切换 --------------------
App._showLoginView = function() {
    document.getElementById('viewLogin').style.display = '';
    document.getElementById('viewSetup').style.display = 'none';
    document.getElementById('viewRemote').style.display = 'none';
    document.getElementById('loginTitle').textContent = '管理员登录';
};

App._showSetupView = function() {
    document.getElementById('viewLogin').style.display = 'none';
    document.getElementById('viewSetup').style.display = '';
    document.getElementById('viewRemote').style.display = 'none';
    document.getElementById('setupTitle').textContent = '首次设置管理员密码';
};

App._showRemoteNoPasswordView = function() {
    document.getElementById('viewLogin').style.display = 'none';
    document.getElementById('viewSetup').style.display = 'none';
    document.getElementById('viewRemote').style.display = '';
};

// -------------------- 登录 --------------------
App.doLogin = async function() {
    const pwd = document.getElementById('loginPassword').value;
    const errBox = document.getElementById('loginError');
    const btn = document.getElementById('loginBtn');
    errBox.classList.remove('show');
    if (!pwd) { errBox.textContent = '请输入密码'; errBox.classList.add('show'); return; }

    btn.disabled = true; btn.textContent = '登录中…';
    try {
        const resp = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd }),
        });
        const r = await resp.json();
        if (r.error) {
            errBox.textContent = r.error;
            errBox.classList.add('show');
            return;
        }
        App.setSession(r.session_token);
        window.location.href = '/app.html';
    } catch (e) {
        errBox.textContent = '网络错误：' + e;
        errBox.classList.add('show');
    } finally {
        btn.disabled = false; btn.textContent = '登录';
    }
};

// -------------------- 首次设置（本机）--------------------
App.doSetup = async function() {
    const pwd = document.getElementById('setupPassword').value;
    const confirm = document.getElementById('setupConfirm').value;
    const errBox = document.getElementById('setupError');
    const btn = document.getElementById('setupBtn');
    errBox.classList.remove('show');
    if (!pwd) { errBox.textContent = '请输入密码'; errBox.classList.add('show'); return; }
    if (pwd.length < 6) { errBox.textContent = '密码至少 6 位'; errBox.classList.add('show'); return; }
    if (pwd !== confirm) { errBox.textContent = '两次输入的密码不一致'; errBox.classList.add('show'); return; }

    btn.disabled = true; btn.textContent = '设置中…';
    try {
        const resp = await fetch('/api/auth/setup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd, confirm: confirm }),
        });
        const r = await resp.json();
        if (r.error) {
            errBox.textContent = r.error;
            errBox.classList.add('show');
            return;
        }
        // 设置成功 → 自动登录
        const loginResp = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd }),
        });
        const lr = await loginResp.json();
        if (lr.session_token) {
            App.setSession(lr.session_token);
            window.location.href = '/app.html';
        } else {
            // 登录失败 → 跳到登录视图
            App._showLoginView();
        }
    } catch (e) {
        errBox.textContent = '网络错误：' + e;
        errBox.classList.add('show');
    } finally {
        btn.disabled = false; btn.textContent = '设置并登录';
    }
};

// -------------------- 登出（app.html 用）--------------------
App.doLogout = async function() {
    try {
        await App.apiPost('/api/auth/logout', {});
    } catch (e) { /* 忽略网络错误 */ }
    App.clearSession();
    window.location.href = '/index.html';
};

// -------------------- 修改密码（app.html 安全设置面板用）--------------------
App.doChangePassword = async function() {
    const oldPwd = document.getElementById('changeOldPassword').value;
    const newPwd = document.getElementById('changeNewPassword').value;
    const confirmPwd = document.getElementById('changeConfirmPassword').value;
    const errBox = document.getElementById('changePwdError');
    const okBox = document.getElementById('changePwdOk');
    const btn = document.getElementById('changePwdBtn');
    errBox.classList.remove('show'); okBox.style.display = 'none';
    if (!oldPwd || !newPwd) { errBox.textContent = '请填写旧密码与新密码'; errBox.classList.add('show'); return; }
    if (newPwd.length < 6) { errBox.textContent = '新密码至少 6 位'; errBox.classList.add('show'); return; }
    if (newPwd !== confirmPwd) { errBox.textContent = '两次新密码不一致'; errBox.classList.add('show'); return; }

    btn.disabled = true; btn.textContent = '修改中…';
    try {
        const r = await App.apiPost('/api/auth/change-password', {
            old_password: oldPwd, new_password: newPwd,
        });
        if (r.error) {
            errBox.textContent = r.error;
            errBox.classList.add('show');
            return;
        }
        // 改密成功 → 所有 session 被吊销 → 需重新登录
        okBox.style.display = 'block';
        okBox.textContent = '密码已修改，所有登录已失效。即将跳转登录页…';
        App.clearSession();
        setTimeout(() => { window.location.href = '/index.html'; }, 2000);
    } catch (e) {
        errBox.textContent = '网络错误：' + e;
        errBox.classList.add('show');
    } finally {
        btn.disabled = false; btn.textContent = '修改密码';
    }
};

// -------------------- app.html 首屏守卫 --------------------
App.requireAuth = async function() {
    const status = await App.apiGet('/api/auth/status');
    if (!status.session_valid) {
        App.clearSession();
        window.location.href = '/index.html';
        return false;
    }
    return true;
};
