/*

# This file is part of LazyFarmers.
# Copyright (c) 2025-Present Routo
#
# LazyFarmers is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with LazyFarmers. If not, see <https://www.gnu.org/licenses/>.



*/

// `let` at top level is not a window property, and init.js needs to read this
// after applySessionRole() resolves, so keep the session on window explicitly
window.sessionInfo = { is_admin: false };
let activationKeys = [];
let dashboardUsers = [];

// the sidebar and the account-management block only make sense for the admin,
// so ask the server who we are and drop everything marked data-admin-only
window.applySessionRole = async function() {
    try {
        const res = await fetch('/api/session');
        if (!res.ok) return;
        window.sessionInfo = await res.json();
    } catch (e) {
        return;
    }
    if (window.sessionInfo.is_admin) return;
    document.querySelectorAll('[data-admin-only]').forEach(el => el.remove());
    const nameEl = document.getElementById('currentAccountName');
    if (nameEl && window.sessionInfo.days_left !== null && window.sessionInfo.days_left !== undefined) {
        nameEl.title = `${window.sessionInfo.email} · ${window.sessionInfo.days_left} days left`;
    }
};

window.loadUsersView = async function() {
    await Promise.all([loadActivationKeys(), loadDashboardUsers()]);
};

async function loadActivationKeys() {
    try {
        const res = await fetch('/api/users/keys');
        const data = await res.json();
        activationKeys = data.keys || [];
        renderActivationKeys();
    } catch (e) {
        const el = document.getElementById('keys-list');
        if (el) el.innerHTML = '<div class="no-data error">Could not load keys</div>';
    }
}

async function loadDashboardUsers() {
    try {
        const res = await fetch('/api/users');
        const data = await res.json();
        dashboardUsers = data.users || [];
        renderDashboardUsers();
    } catch (e) {
        const el = document.getElementById('users-list');
        if (el) el.innerHTML = '<div class="no-data error">Could not load users</div>';
    }
}

function activationLink(key) {
    return `${window.location.origin}/activate?key=${encodeURIComponent(key)}`;
}

function renderActivationKeys() {
    const el = document.getElementById('keys-list');
    if (!el) return;
    if (!activationKeys.length) {
        el.innerHTML = '<div class="no-data">No activation keys yet. Generate one above.</div>';
        return;
    }
    el.innerHTML = activationKeys.map(k => {
        const used = !!k.used_by;
        const badge = used
            ? `<span class="users-badge used">USED</span>`
            : `<span class="users-badge fresh">UNUSED</span>`;
        const note = k.note ? `<span class="dim">${escHtml(k.note)}</span>` : '';
        const usedLine = used
            ? `<span class="dim">Redeemed by ${escHtml(k.used_by)} · ${fmtDate(k.used_at)}</span>`
            : `<span class="users-link mono" title="${escAttr(activationLink(k.key))}">${escHtml(activationLink(k.key))}</span>`;
        const copyBtn = used ? '' : `<button class="btn-proxy-sm" onclick="copyActivationLink('${escAttr(k.key)}')"><span class="icon-svg" style="--icon: url('/static/assets/neura_icons/link.svg');"></span> Copy link</button>`;
        return `
            <div class="users-card ${used ? 'off' : ''}">
                <div class="users-card-info">
                    <strong class="mono">${escHtml(k.key)} ${badge} <span class="dim">${k.days}d</span></strong>
                    ${usedLine}
                    ${note}
                </div>
                <div class="users-card-actions">
                    ${copyBtn}
                    <button class="btn-proxy-sm danger" onclick="deleteActivationKey('${escAttr(k.key)}')">Delete</button>
                </div>
            </div>
        `;
    }).join('');
}

function renderDashboardUsers() {
    const el = document.getElementById('users-list');
    if (!el) return;
    if (!dashboardUsers.length) {
        el.innerHTML = '<div class="no-data">Nobody has redeemed a key yet.</div>';
        return;
    }
    el.innerHTML = dashboardUsers.map(u => {
        let badge;
        if (u.revoked) badge = '<span class="users-badge revoked">REVOKED</span>';
        else if (u.expired) badge = '<span class="users-badge revoked">EXPIRED</span>';
        else badge = `<span class="users-badge fresh">${u.days_left} DAYS LEFT</span>`;
        const revokeBtn = u.revoked
            ? `<button class="btn-proxy-sm" onclick="setUserRevoked('${escAttr(u.id)}', false)">Restore</button>`
            : `<button class="btn-proxy-sm danger" onclick="setUserRevoked('${escAttr(u.id)}', true)">Revoke</button>`;
        return `
            <div class="users-card ${u.revoked || u.expired ? 'off' : ''}">
                <div class="users-card-info">
                    <strong>${escHtml(u.email)} ${badge}</strong>
                    <span class="mono users-secret">Password: <span class="users-pw" data-pw="${escAttr(u.password)}">••••••••</span>
                        <button class="btn-proxy-sm" onclick="togglePassword(this)">Show</button></span>
                    <span class="dim">Key ${escHtml(u.key || '—')} · joined ${fmtDate(u.created_at)} · expires ${fmtDate(u.expires_at)} · last login ${fmtDate(u.last_login)}</span>
                </div>
                <div class="users-card-actions">
                    <button class="btn-proxy-sm" onclick="extendUser('${escAttr(u.id)}')">+ Days</button>
                    <button class="btn-proxy-sm" onclick="changeUserPassword('${escAttr(u.id)}')">Password</button>
                    ${revokeBtn}
                    <button class="btn-proxy-sm danger" onclick="deleteUser('${escAttr(u.id)}', '${escAttr(u.email)}')">Delete</button>
                </div>
            </div>
        `;
    }).join('');
}

window.togglePassword = function(btn) {
    const span = btn.parentElement.querySelector('.users-pw');
    if (!span) return;
    const showing = span.dataset.showing === '1';
    span.textContent = showing ? '••••••••' : span.dataset.pw;
    span.dataset.showing = showing ? '0' : '1';
    btn.textContent = showing ? 'Show' : 'Hide';
};

window.copyActivationLink = function(key) {
    const link = activationLink(key);
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(link)
            .then(() => showToast('Activation link copied', 'success'))
            .catch(() => showToast(link, 'info'));
    } else {
        showToast(link, 'info');
    }
};

window.generateKeys = async function() {
    const days = parseInt(document.getElementById('key-days').value, 10);
    const count = parseInt(document.getElementById('key-count').value, 10) || 1;
    const note = document.getElementById('key-note').value.trim();
    if (!days || days < 1) {
        showToast('Set at least 1 day', 'error');
        return;
    }
    const data = await usersRequest('/api/users/keys', 'POST', { days, count, note });
    if (!data.success) return;
    document.getElementById('key-note').value = '';
    showToast(`Generated ${data.keys.length} key(s)`, 'success');
    await loadActivationKeys();
    if (data.keys.length === 1) copyActivationLink(data.keys[0].key);
};

window.deleteActivationKey = async function(key) {
    if (!confirm(`Delete key ${key}? Anyone holding the link will no longer be able to use it.`)) return;
    const data = await usersRequest(`/api/users/keys/${encodeURIComponent(key)}`, 'DELETE');
    if (data.success) {
        showToast('Key deleted', 'success');
        await loadActivationKeys();
    }
};

window.setUserRevoked = async function(userId, revoked) {
    const data = await usersRequest(`/api/users/${encodeURIComponent(userId)}`, 'PATCH', {
        action: revoked ? 'revoke' : 'restore',
    });
    if (data.success) {
        showToast(revoked ? 'Access removed' : 'Access restored', 'success');
        await loadDashboardUsers();
    }
};

window.extendUser = async function(userId) {
    const raw = prompt('Add how many days? (use a negative number to take days away)', '7');
    if (raw === null) return;
    const days = parseFloat(raw);
    if (!days) {
        showToast('Enter a number of days', 'error');
        return;
    }
    const data = await usersRequest(`/api/users/${encodeURIComponent(userId)}`, 'PATCH', { action: 'extend', days });
    if (data.success) {
        showToast(`Now ${data.user.days_left} days left`, 'success');
        await loadDashboardUsers();
    }
};

window.changeUserPassword = async function(userId) {
    const password = prompt('New password (at least 6 characters)');
    if (password === null) return;
    const data = await usersRequest(`/api/users/${encodeURIComponent(userId)}`, 'PATCH', { action: 'password', password });
    if (data.success) {
        showToast('Password changed', 'success');
        await loadDashboardUsers();
    }
};

window.deleteUser = async function(userId, email) {
    if (!confirm(`Delete ${email}? Their login stops working right away.`)) return;
    const data = await usersRequest(`/api/users/${encodeURIComponent(userId)}`, 'DELETE');
    if (data.success) {
        showToast('User deleted', 'success');
        await loadDashboardUsers();
    }
};

async function usersRequest(url, method, body) {
    try {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (body) opts.body = JSON.stringify(body);
        const res = await fetch(url, opts);
        const data = await res.json();
        if (!data.success) showToast(data.error || 'Request failed', 'error');
        return data;
    } catch (e) {
        showToast('Request failed', 'error');
        return { success: false };
    }
}

function fmtDate(ts) {
    if (!ts) return 'never';
    return new Date(ts * 1000).toLocaleString();
}

function escHtml(s) {
    return String(s === null || s === undefined ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escAttr(s) {
    return escHtml(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
