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

const CAPTCHA_SERVICES = {
    yescaptcha:  { label: 'YesCaptcha',   keyField: 'yescaptcha_api_key',  balanceUnit: 'pts',    color: '#f59e0b', hint: 'Paid service – requires ≥ 30 pts to auto-solve.' },
    nopecha:     { label: 'NopeCHA',      keyField: 'nopecha_api_key',     balanceUnit: 'credits',color: '#a855f7', hint: 'Free 100 credits daily – resets every day.' },
    anticaptcha: { label: 'Anti-Captcha', keyField: 'anticaptcha_api_key', balanceUnit: '$',      color: '#22c55e', hint: 'Paid service – supports hCaptcha Enterprise too.' },
    captchaly:   { label: 'Captchaly',    keyField: 'captchaly_api_key',   balanceUnit: '$',      color: '#3b82f6', hint: 'Paid service – strict 120s solve times.' },
};

let pendingCaptchas = {};
let pendingInterval = null;
let _manualSolvePopup = null;

async function testSecurity(btn) {
    const q = currentAccountId ? `?id=${currentAccountId}` : '';
    const original = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> TESTING...';
    btn.disabled = true;
    try {
        const res = await fetch(`/api/security/test${q}`, { method: 'POST' });
        const d = await res.json();
        if (d.status === 'success') {
            btn.style.borderColor = 'var(--success)';
            btn.innerHTML = '<i class="fa-solid fa-check"></i> SIGNALS SENT';
        } else {
            alert("Test failed: " + d.message);
            btn.innerHTML = original;
        }
    } catch (e) {
        alert("Request failed");
        btn.innerHTML = original;
    } finally {
        setTimeout(() => {
            btn.innerHTML = original;
            btn.disabled = false;
            btn.style.border = '';
        }, 3000);
    }
}

async function fetchSecuritySummary() {
    if (!document.getElementById('security').classList.contains('active-view')) return;
    const container = document.getElementById('security-accounts-grid');
    if (!container) return;
    let html = '';
    for (const acc of accountsList) {
        try {
            const res = await fetch(`/api/stats?id=${acc.id}`);
            const d = await res.json();
            if (!d || !d.security) continue;
            const isActive = acc.id === currentAccountId;
            const statusColor = d.status === "PAUSED" ? "var(--danger)" : "var(--success)";
            html += `
                <div class="sec-account-card ${d.status === "PAUSED" ? 'alert-active' : ''} ${isActive ? 'selected' : ''}">
                    <div class="sec-acc-header">
                        <div class="sec-acc-info">
                            ${acc.avatar ? `<img src="${escAttr(acc.avatar)}" class="account-avatar-lg" alt="">` : '<span class="icon-svg account-avatar-lg account-avatar-fallback" style="--icon: url(\'/static/assets/neura_icons/discord.svg\');"></span>'}
                            <div class="sec-acc-text">
                                <div class="sec-acc-name">${escHtml(acc.username)}</div>
                                <div class="sec-acc-id">User ID · ${escHtml(acc.id)}</div>
                                <div class="sec-acc-status" style="color:${statusColor}">${escHtml(d.status)}</div>
                            </div>
                        </div>
                    </div>
                    <div class="sec-acc-stats">
                        <div class="sec-mini-stat">
                            <span class="icon-svg" style="--icon: url('/static/assets/neura_icons/check-to-slot.svg'); background-color: var(--success);"></span>
                            <div class="val">${d.security.captchas}</div>
                            <div class="lbl">Solved</div>
                        </div>
                        <div class="sec-mini-stat">
                            <span class="icon-svg" style="--icon: url('/static/assets/neura_icons/user-slash.svg'); background-color: var(--danger);"></span>
                            <div class="val">${d.security.bans}</div>
                            <div class="lbl">Bans</div>
                        </div>
                        <div class="sec-mini-stat">
                            <span class="icon-svg" style="--icon: url('/static/assets/neura_icons/warning.svg'); background-color: var(--warning);"></span>
                            <div class="val">${d.security.warnings}</div>
                            <div class="lbl">Warns</div>
                        </div>
                    </div>
                </div>
            `;
        } catch (e) {}
    }
    container.innerHTML = html || '<div class="no-data">Initializing system details...</div>';
}

function renderCaptchaSolverWidget(cfg, basePath, parentEnabled) {
    const enabled    = cfg.enabled !== false;
    const service    = (cfg.service || 'yescaptcha').toLowerCase();
    const svcInfo    = CAPTCHA_SERVICES[service] || CAPTCHA_SERVICES.yescaptcha;
    const apiKey     = cfg[svcInfo.keyField] || '';
    // The key box has to follow the same rule as the Service dropdown below.
    // It used to stay editable while the auto-solver was off, and because the
    // dropdown was frozen at that point, a key for another service (say NopeCHA)
    // landed in yescaptcha_api_key - a key that looked saved but was never read,
    // so every captcha still came back to the operator to solve by hand.
    const live       = enabled && parentEnabled;
    const dis        = live ? '' : ' disabled';
    const serviceOptions = Object.entries(CAPTCHA_SERVICES).map(([id, s]) => `
        <option value="${id}" ${id === service ? 'selected' : ''}>${s.label}</option>
    `).join('');
    return `
        <div class="cfg-row" data-path="${basePath}.enabled">
            <div class="cfg-row-label"><span class="cfg-label-text">Enable Auto-Solver</span></div>
            <div class="cfg-row-control">${renderNeuraToggle(basePath + '.enabled', enabled, parentEnabled, true)}</div>
        </div>
        <div class="cfg-row csw-service-row" data-path="${basePath}.service">
            <div class="cfg-row-label">
                <span class="cfg-label-text">Service</span>
                <span class="csw-service-hint">${svcInfo.hint}</span>
            </div>
            <div class="cfg-row-control">
                <div class="csw-dropdown-wrap">
                    <div class="csw-svc-dot" style="background:${svcInfo.color}"></div>
                    <select id="csw-service-select" class="csw-select" ${live ? '' : 'disabled'}
                        onchange="updateCaptchaService(this.value)">
                        ${serviceOptions}
                    </select>
                </div>
            </div>
        </div>
        <div class="cfg-row csw-key-row" data-path="${basePath}.${svcInfo.keyField}" id="csw-key-row">
            <div class="cfg-row-label">
                <span class="cfg-label-text">${svcInfo.label} API Key</span>
                ${live ? '' : '<span class="csw-service-hint">Turn on Enable Auto-Solver first, then pick your service.</span>'}
            </div>
            <div class="cfg-row-control">
                <div class="cfg-input-wrap">
                    <input type="password" id="csw-api-key-input" class="cfg-input" value="${apiKey}"${dis}
                        placeholder="${live ? `Paste your ${svcInfo.label} API key here…` : 'Enable the auto-solver to set a key'}"
                        onchange="updateDeepVal('${basePath}.${svcInfo.keyField}', this.value)">
                </div>
            </div>
        </div>
        <div class="cfg-row csw-balance-row">
            <div class="cfg-row-label"><span class="cfg-label-text">Live Balance</span></div>
            <div class="cfg-row-control">
                <div class="csw-balance-wrap">
                    <span id="csw-balance-badge" class="csw-balance-badge" onclick="fetchCaptchaBalance()">
                        <span class="csw-balance-dot"></span>
                        <span id="csw-balance-text">Click to check…</span>
                    </span>
                    <button class="cfg-stepper-btn csw-refresh-btn" onclick="fetchCaptchaBalance()" title="Refresh balance">
                        <span class="icon-svg" style="--icon: url('/static/assets/neura_icons/sync.svg');"></span>
                    </button>
                </div>
            </div>
        </div>
        ${renderBrowserSolverRows(cfg.browser_solver || {}, basePath + '.browser_solver', parentEnabled)}
    `;
}

// This widget hand-renders security.captcha_solver, and renderCategoryFlat swallows every
// key of that section (see its `path === 'security.captcha_solver'` branch) - so a nested
// object added to the config is invisible here unless it is rendered explicitly. Without
// these rows the key-free solver could only be turned off by editing settings.json.
//
// Deliberately gated on parentEnabled, not on `live`: the browser solver needs no service
// and no key, so it must stay usable while "Enable Auto-Solver" is off.
function renderBrowserSolverRows(bs, base, parentEnabled) {
    const on  = bs.enabled !== false;
    const sub = parentEnabled && on;
    return `
        <div class="cfg-section cfg-section-nested ${on ? '' : 'cfg-section-disabled'}">
            <div class="cfg-section-head">Key-Free Browser Solver</div>
            <div class="cfg-section-rows">
                <div class="cfg-row" data-search="browser solver key free hcaptcha"
                     data-path="${base}.enabled">
                    <div class="cfg-row-label">
                        <span class="cfg-label-text">Enable Browser Solver</span>
                        <span class="csw-service-hint">Solves in Chrome/Edge on this machine. No service, no API key. Also detects captchas OwO already dropped.</span>
                    </div>
                    <div class="cfg-row-control">${renderNeuraToggle(base + '.enabled', on, parentEnabled, true)}</div>
                </div>
                <div class="cfg-row ${sub ? '' : 'cfg-row-disabled'}" data-path="${base}.headless">
                    <div class="cfg-row-label">
                        <span class="cfg-label-text">Headless</span>
                        <span class="csw-service-hint">Leave off: hCaptcha's image challenges cannot be answered in a window nobody can see.</span>
                    </div>
                    <div class="cfg-row-control">${renderNeuraToggle(base + '.headless', bs.headless === true, sub)}</div>
                </div>
                <div class="cfg-row" data-path="${base}.timeout_s">
                    <div class="cfg-row-label"><span class="cfg-label-text">Timeout</span></div>
                    <div class="cfg-row-control">${renderStepperInner(base + '.timeout_s', '', bs.timeout_s ?? 180, 's', sub)}</div>
                </div>
                <div class="cfg-row" data-path="${base}.passive_window_s">
                    <div class="cfg-row-label">
                        <span class="cfg-label-text">Passive Window</span>
                        <span class="csw-service-hint">A token issued inside this window means hCaptcha passed us without a challenge.</span>
                    </div>
                    <div class="cfg-row-control">${renderStepperInner(base + '.passive_window_s', '', bs.passive_window_s ?? 20, 's', sub)}</div>
                </div>
                <div class="cfg-row" data-path="${base}.widget_wait_s">
                    <div class="cfg-row-label">
                        <span class="cfg-label-text">Widget Wait</span>
                        <span class="csw-service-hint">How long to wait for OwO's page to mount hCaptcha before calling it "nothing to solve".</span>
                    </div>
                    <div class="cfg-row-control">${renderStepperInner(base + '.widget_wait_s', '', bs.widget_wait_s ?? 25, 's', sub)}</div>
                </div>
            </div>
        </div>
    `;
}

window.updateCaptchaService = function(newService) {
    const basePath = 'security.captcha_solver';
    setDeep(currentConfig, `${basePath}.service`.split('.'), newService);
    checkDirty();
    renderSettings(currentConfig);
};

window.fetchCaptchaBalance = async function() {
    const badge   = document.getElementById('csw-balance-badge');
    const balText = document.getElementById('csw-balance-text');
    if (!badge || !balText) return;
    balText.textContent = 'Checking…';
    badge.className = 'csw-balance-badge loading';
    try {
        const q = currentAccountId ? `?id=${currentAccountId}` : '';
        const selectedService = getDeep(currentConfig, 'security.captcha_solver.service'.split('.')) || 'yescaptcha';
        const svcInfo = CAPTCHA_SERVICES[selectedService] || CAPTCHA_SERVICES.yescaptcha;
        const currentKey = getDeep(currentConfig, `security.captcha_solver.${svcInfo.keyField}`.split('.')) || '';
        const res = await fetch(`/api/captcha/balance${q}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service: selectedService, api_key: currentKey })
        });
        const d = await res.json();
        if (d.error || d.balance === null || d.balance === undefined) {
            balText.textContent = d.message || d.error || 'Error – check API key';
            badge.className = 'csw-balance-badge error';
        } else {
            let balance = d.balance;
            let unit = svcInfo.balanceUnit;
            let display;
            if (typeof balance === 'number') {
                if (unit === '$') display = `$${balance.toFixed(2)}`;
                else display = `${Math.round(balance).toLocaleString()} ${unit}`;
            } else {
                display = String(balance);
            }
            balText.textContent = display;
            badge.className = 'csw-balance-badge ok';
        }
    } catch (e) {
        balText.textContent = 'Request failed';
        badge.className = 'csw-balance-badge error';
    }
};

async function updatePendingCaptchas() {
    try {
        const res = await fetch('/api/captcha/pending');
        const data = await res.json();
        const pending = data.pending || [];
        const newPending = {};
        pending.forEach(p => {
            newPending[p.account_id] = {
                accountId: p.account_id,
                accountName: p.account_name || p.account_id,
                createdAt: p.created_at
            };
        });
        Object.keys(pendingCaptchas).forEach(id => {
            if (!newPending[id]) delete pendingCaptchas[id];
        });
        Object.keys(newPending).forEach(id => {
            if (!pendingCaptchas[id]) pendingCaptchas[id] = newPending[id];
        });
        updateNotificationUI();
    } catch (e) {
        console.error('Failed to fetch pending captchas:', e);
    }
}

function updateNotificationUI() {
    const count = Object.keys(pendingCaptchas).length;
    const bell = document.getElementById('notification-bell');
    const badge = document.getElementById('notification-badge');
    if (bell) {
        if (count > 0) {
            bell.classList.add('has-alert');
            badge.textContent = count;
            badge.style.display = 'block';
        } else {
            bell.classList.remove('has-alert');
            badge.style.display = 'none';
        }
    }
    renderPendingDropdown();
    renderSecurityCards();
}

function renderPendingDropdown() {
    const dropdown = document.getElementById('notification-dropdown');
    if (!dropdown) return;
    const count = Object.keys(pendingCaptchas).length;
    if (count === 0) {
        dropdown.innerHTML = '<div class="no-data">No pending captchas</div>';
        return;
    }
    let html = '';
    const now = Date.now() / 1000;
    Object.values(pendingCaptchas).forEach(p => {
        const elapsed = now - p.createdAt;
        const remaining = Math.max(0, 600 - elapsed);
        const urgencyClass = getUrgencyClass(remaining);
        const timeStr = formatTime(remaining);
        html += `
            <div class="pending-item ${urgencyClass}">
                <span class="pending-name">${escHtml(p.accountName)}</span>
                <span class="pending-timer">${timeStr}</span>
                <button class="btn-proxy-sm solve-btn" onclick="triggerManualSolve('${jsArg(p.accountId)}')">Solve</button>
            </div>
        `;
    });
    dropdown.innerHTML = html;
}

function renderSecurityCards() {
    const container = document.getElementById('captcha-cards-container');
    if (!container) return;
    const count = Object.keys(pendingCaptchas).length;
    if (count === 0) {
        container.innerHTML = '<div class="no-data">No pending captchas</div>';
        return;
    }
    let html = '';
    const now = Date.now() / 1000;
    Object.values(pendingCaptchas).forEach(p => {
        const elapsed = now - p.createdAt;
        const remaining = Math.max(0, 600 - elapsed);
        const urgencyClass = getUrgencyClass(remaining);
        const timeStr = formatTime(remaining);
        html += `
            <div class="captcha-card ${urgencyClass}">
                <div class="captcha-card-header">
                    <span class="captcha-account">${escHtml(p.accountName)}</span>
                    <span class="captcha-timer">${timeStr}</span>
                </div>
                <div class="captcha-card-body">
                    <button class="btn-control gold" onclick="triggerManualSolve('${jsArg(p.accountId)}')">Solve</button>
                    <button class="btn-control" onclick="dismissCaptchaCard('${jsArg(p.accountId)}')">Dismiss</button>
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function dismissCaptchaCard(accountId) {
    if (pendingCaptchas[accountId]) {
        delete pendingCaptchas[accountId];
        updateNotificationUI();
    }
}

function getUrgencyClass(seconds) {
    if (seconds > 300) return 'urgency-green';
    if (seconds > 120) return 'urgency-yellow';
    if (seconds > 60) return 'urgency-orange';
    if (seconds > 30) return 'urgency-red';
    return 'urgency-critical';
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}m ${secs}s`;
}

window.triggerManualSolve = async function(accountId) {
    try {
        const res = await fetch('/api/captcha/oauth_url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: accountId })
        });
        const data = await res.json();
        if (!data.success || !data.url) {
            showToast('Failed to get captcha URL', 'error');
            return;
        }
        const popup = window.open(data.url, '_blank', 'width=420,height=600,resizable=yes,scrollbars=yes');
        if (!popup) {
            window.open(data.url, '_blank');
        } else {
            _manualSolvePopup = popup;
        }
        showToast('Captcha page opened in new window', 'info');
    } catch (e) {
        showToast('Error opening captcha', 'error');
    }
};

window.pollForCaptchas = async function() {
    await updatePendingCaptchas();
};

function startPendingTimer() {
    if (pendingInterval) clearInterval(pendingInterval);
    pendingInterval = setInterval(() => {
        if (Object.keys(pendingCaptchas).length > 0) {
            renderPendingDropdown();
            renderSecurityCards();
        }
    }, 1000);
}

window.toggleNotificationDropdown = function() {
    const dropdown = document.getElementById('notification-dropdown');
    if (!dropdown) return;
    if (dropdown.style.display === 'block') {
        dropdown.style.display = 'none';
    } else {
        dropdown.style.display = 'block';
        document.addEventListener('click', function closeDropdown(e) {
            const bell = document.getElementById('notification-bell');
            if (bell && !bell.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.style.display = 'none';
                document.removeEventListener('click', closeDropdown);
            }
        });
    }
};

window.cancelManualSolve = function() {
    if (_manualSolvePopup) {
        try { _manualSolvePopup.close(); } catch (e) {}
        _manualSolvePopup = null;
    }
};

// ---------------------------------------------------------------------------
// Embedded hCaptcha panel (index.html #captcha-solver-section).
// dashboard.js update() calls openEmbeddedCaptcha() whenever an account is
// paused with a captcha message, so these must exist even if the hCaptcha
// script is blocked.
// ---------------------------------------------------------------------------
const OWO_HCAPTCHA_SITEKEY = 'a6a1d5ce-612d-472d-8e37-7601408fbc09';
let _embeddedCaptcha = { accountId: null, accountName: null, widgetId: null };

function _captchaFallbackHtml(accountId) {
    return `
        <div class="no-data" style="text-align:center;">
            hCaptcha widget unavailable here.<br>
            <button class="btn-control gold" style="margin-top:10px;"
                onclick="triggerManualSolve('${jsArg(accountId)}')">Open solve page</button>
        </div>
    `;
}

// hCaptcha serves 127.0.0.1 but answers the literal host "localhost" with
// 403 "Invalid Data", so the embedded widget can never load on http://localhost:8000 -
// it silently renders an empty box. Same machine, same port, different string.
function _localhostBlocked() {
    return location.hostname === 'localhost';
}

function _localhostHtml() {
    const swapped = location.href.replace('//localhost', '//127.0.0.1');
    return `
        <div class="no-data" style="text-align:left; line-height:1.6;">
            hCaptcha refuses the hostname <code>localhost</code> (403 Invalid Data), so the
            widget cannot load on this address. Two ways round it:
            <div style="margin-top:12px;">
                <a class="btn-control green" href="${swapped}">Reopen on 127.0.0.1</a>
                <button class="btn-control gold" onclick="solveInBrowser()">Solve in browser (no key)</button>
            </div>
            <div style="margin-top:10px; opacity:.75; font-size:.9em;">
                Cookies are per-hostname, so 127.0.0.1 will ask you to log in again.
                The browser solve runs on the machine hosting the bot and needs no key.
            </div>
        </div>
    `;
}

function _renderEmbeddedHcaptcha() {
    const container = document.getElementById('hcaptcha-container');
    if (!container) return;
    const accountId = _embeddedCaptcha.accountId;

    if (_localhostBlocked()) {
        container.innerHTML = _localhostHtml();
        return;
    }

    if (typeof hcaptcha === 'undefined' || typeof hcaptcha.render !== 'function') {
        container.innerHTML = _captchaFallbackHtml(accountId);
        return;
    }

    container.innerHTML = '<div id="hcaptcha-widget"></div>';
    try {
        _embeddedCaptcha.widgetId = hcaptcha.render('hcaptcha-widget', {
            sitekey: OWO_HCAPTCHA_SITEKEY,
            theme: 'dark',
            callback: 'submitEmbeddedCaptcha',
            'expired-callback': 'reloadEmbeddedCaptcha',
            'error-callback': 'reloadEmbeddedCaptcha'
        });
    } catch (e) {
        console.error('hCaptcha render failed:', e);
        _embeddedCaptcha.widgetId = null;
        container.innerHTML = _captchaFallbackHtml(accountId);
    }
}

window.openEmbeddedCaptcha = function(accountId, accountName) {
    const section = document.getElementById('captcha-solver-section');
    if (!section) return;
    _embeddedCaptcha.accountId = accountId;
    _embeddedCaptcha.accountName = accountName || accountId;
    section.style.display = 'block';
    const title = section.querySelector('.module-header h3');
    if (title) title.setAttribute('title', `Solving for ${_embeddedCaptcha.accountName}`);
    _renderEmbeddedHcaptcha();
};

window.reloadEmbeddedCaptcha = function() {
    if (!_embeddedCaptcha.accountId) return;
    if (_embeddedCaptcha.widgetId !== null && typeof hcaptcha !== 'undefined') {
        try { hcaptcha.reset(_embeddedCaptcha.widgetId); return; } catch (e) {}
    }
    _renderEmbeddedHcaptcha();
};

window.closeEmbeddedCaptcha = function() {
    const section = document.getElementById('captcha-solver-section');
    if (section) section.style.display = 'none';
    if (_embeddedCaptcha.widgetId !== null && typeof hcaptcha !== 'undefined') {
        try { hcaptcha.reset(_embeddedCaptcha.widgetId); } catch (e) {}
    }
    _embeddedCaptcha = { accountId: null, accountName: null, widgetId: null };
};

window.submitEmbeddedCaptcha = async function(token) {
    const accountId = _embeddedCaptcha.accountId;
    if (!accountId || !token) return;
    try {
        const res = await fetch('/api/captcha_solve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: accountId, token: token })
        });
        const d = await res.json();
        if (d.success) {
            showToast('Captcha verified', 'success');
            dismissCaptchaCard(accountId);
            closeEmbeddedCaptcha();
        } else {
            showToast(d.error || 'Captcha rejected', 'error');
            reloadEmbeddedCaptcha();
        }
    } catch (e) {
        showToast('Failed to submit captcha', 'error');
        reloadEmbeddedCaptcha();
    }
};

window.cancelEmbeddedCaptcha = function() {
    cancelManualSolve();
    closeEmbeddedCaptcha();
};

// Key-free solve: the bot's own machine opens OwO's captcha page in Chrome/Edge with the
// account already authenticated. hCaptcha issues the token itself when its risk score
// allows it; otherwise the challenge appears in that window for one answer.
window.solveInBrowser = async function() {
    const accountId = _embeddedCaptcha.accountId;
    if (!accountId) { showToast('No account selected', 'error'); return; }
    const btn = document.getElementById('browser-solve-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Solving in browser...'; }
    showToast('Opening the captcha in a browser on the bot machine...', 'info');
    try {
        const res = await fetch('/api/captcha/browser_solve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: accountId })
        });
        const d = await res.json();
        if (d.success) {
            const how = d.how === 'passive' ? 'automatically, no key used'
                      : d.how === 'interactive' ? 'answered in the browser'
                      : 'already clear';
            showToast(`Captcha done (${how})`, 'success');
            dismissCaptchaCard(accountId);
            closeEmbeddedCaptcha();
        } else {
            showToast(d.error || 'Browser solve failed', 'error');
        }
    } catch (e) {
        showToast('Browser solve request failed', 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Solve in browser (no key)'; }
    }
};

startPendingTimer();