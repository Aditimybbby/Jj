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

// The Earning tab. One switch that points every account in the space at
// hunt + huntbot + sell, and the ledger those accounts keep while it is on.
//
// Every figure here comes from the server's cash-flow ledger (cogs/earning.py):
// gained minus spent always equals the account's real cowoncy movement, so a
// bucket that guessed wrong shows up as a mislabelled row, never as fake profit.

let earningSettings = null;
let earningTimer = null;

function earnNum(n) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    return Number(n).toLocaleString('en-US');
}

function earnSigned(n) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    const v = Number(n);
    return (v > 0 ? '+' : '') + v.toLocaleString('en-US');
}

function earnColor(n) {
    if (n === null || n === undefined || Number.isNaN(n)) return 'var(--text-dim, #888)';
    if (Number(n) > 0) return 'var(--success, #22c55e)';
    if (Number(n) < 0) return 'var(--rose, #f43f5e)';
    return 'inherit';
}

function earnDuration(hours) {
    const h = Number(hours || 0);
    if (h <= 0) return 'just started';
    if (h < 1) return `${Math.round(h * 60)}m`;
    if (h < 48) return `${Math.floor(h)}h ${Math.round((h % 1) * 60)}m`;
    return `${Math.floor(h / 24)}d ${Math.round(h % 24)}h`;
}

window.loadEarning = async function () {
    try {
        const r = await fetch('/api/earning');
        const d = await r.json();
        if (!d || !d.success) throw new Error((d && d.error) || 'request failed');
        earningSettings = d.settings || {};
        renderEarningControls(d.settings || {}, d.totals || {});
        renderEarningKpis(d.totals || {}, d.accounts || []);
        renderEarningAccounts(d.accounts || []);
    } catch (e) {
        const box = document.getElementById('earning-controls');
        if (box) box.innerHTML = `<div class="no-data">Could not load earning data: ${escHtml(String(e.message || e))}</div>`;
    }
};

function renderEarningControls(s, totals) {
    const box = document.getElementById('earning-controls');
    if (!box) return;
    const on = !!s.enabled;
    box.innerHTML = `
        <div class="neura-config-row" style="align-items:center;">
            <div class="config-label-group">
                <label class="config-label">Earning mode</label>
                <span class="config-hint">Points every account in this space at
                    <b>hunt</b>, <b>huntbot</b> and <b>sell</b>. Overrides each account's own
                    command switches while it is on; turning it off restores them.</span>
            </div>
            <button class="btn-control ${on ? 'green' : ''}" onclick="toggleEarning(${on ? 'false' : 'true'}, this)">
                <span class="btn-text">${on ? 'ON — turn off' : 'OFF — turn on'}</span>
            </button>
        </div>
        <div class="neura-config-row" style="align-items:center;">
            <div class="config-label-group">
                <label class="config-label">Exclusive</label>
                <span class="config-hint">Also switch off what competes for the cowoncy or the
                    time: battle, the gambling three, curse/pray, cookie, rpp, giveaway and coop
                    battles. Daily, gems, team, weapon, quest and shop stay on — they feed the hunt.</span>
            </div>
            <button class="btn-control ${s.exclusive ? 'green' : ''}"
                    onclick="saveEarningField('exclusive', ${s.exclusive ? 'false' : 'true'}, this)">
                <span class="btn-text">${s.exclusive ? 'Yes' : 'No'}</span>
            </button>
        </div>
        ${earnStepper('huntbot_cash', 'Huntbot cowoncy', s.huntbot_cash, 500,
                      'Handed to the huntbot on every dispatch. This is the spend the ledger meters.')}
        ${earnStepper('sell_interval_min', 'Sell every (min)', s.sell_interval_min, 5,
                      'How often to sell animals. Selling is the only income the ledger counts as a sale.')}
        <div class="neura-config-row" style="align-items:center;">
            <div class="config-label-group">
                <label class="config-label">Sell what</label>
                <span class="config-hint">Passed straight to <code>owo sell &lt;type&gt;</code>. Use
                    <code>all</code> for every animal, or a rarity like <code>common</code>.</span>
            </div>
            <input class="input-dark" style="max-width:160px" value="${escAttr(s.sell_type || 'all')}"
                   onchange="saveEarningField('sell_type', this.value, this)">
        </div>
        <div class="neura-config-row" style="align-items:center;">
            <div class="config-label-group">
                <label class="config-label">Ledger</label>
                <span class="config-hint">${totals.on || 0} of ${totals.accounts || 0} running
                    account(s) in earning mode. Reset zeroes the numbers and re-opens the run at the
                    current balance.</span>
            </div>
            <button class="btn-control" onclick="resetEarning(null, this)"><span class="btn-text">RESET ALL</span></button>
        </div>`;
}

function earnStepper(key, label, value, step, hint) {
    const v = Number(value || 0);
    return `
        <div class="neura-config-row" style="align-items:center;">
            <div class="config-label-group">
                <label class="config-label">${escHtml(label)}</label>
                <span class="config-hint">${hint}</span>
            </div>
            <div class="stepper-wrap">
                <button class="stepper-btn" onclick="saveEarningField('${key}', ${Math.max(0, v - step)}, this)">−</button>
                <input class="input-dark stepper-input" type="number" value="${v}"
                       onchange="saveEarningField('${key}', this.value, this)">
                <button class="stepper-btn" onclick="saveEarningField('${key}', ${v + step}, this)">+</button>
            </div>
        </div>`;
}

function earnKpi(icon, title, value, sub, color) {
    return `
        <div class="kpi-card">
            <div class="kpi-icon"><span class="icon-svg"
                    style="--icon: url('/static/assets/neura_icons/${icon}.svg');"></span></div>
            <div class="kpi-data">
                <h3>${escHtml(title)}</h3>
                <p style="color:${color || 'inherit'}">${value}${sub ? `
                    <span style="font-size:0.5em; color:var(--text-dim,#888);">${sub}</span>` : ''}</p>
            </div>
        </div>`;
}

function renderEarningKpis(t, accounts) {
    const box = document.getElementById('earning-kpis');
    if (!box) return;
    const hours = accounts.reduce((m, a) => Math.max(m, a.hours || 0), 0);
    box.innerHTML = [
        earnKpi('coins', 'Gained', earnNum(t.gained), `${earnNum(t.sold_count)} sales`, 'var(--success,#22c55e)'),
        earnKpi('money', 'Spent · autohunt', earnNum(t.spent_autohunt),
                `${earnNum(t.autohunt_runs)} dispatches`, 'var(--rose,#f43f5e)'),
        earnKpi('gun', 'Spent · hunt upkeep', earnNum(t.spent_hunt),
                `${earnNum(t.hunts)} hunts sent`, 'var(--rose,#f43f5e)'),
        earnKpi('bolt', 'Net', earnSigned(t.net), earnDuration(hours), earnColor(t.net)),
        earnKpi('chart-column', 'Per hour', earnSigned(t.per_hour), 'across the farm', earnColor(t.per_hour)),
        earnKpi('layers', 'Unattributed', earnNum(t.spent_other),
                'spend with no command to blame', 'var(--text-dim,#888)'),
    ].join('');
}

function renderEarningAccounts(rows) {
    const box = document.getElementById('earning-accounts');
    if (!box) return;
    if (!rows.length) {
        box.innerHTML = '<div class="no-data">No accounts are running. Start one from the Accounts tab.</div>';
        return;
    }
    box.innerHTML = `
        <div style="overflow-x:auto">
        <table class="neura-table" style="width:100%; border-collapse:collapse;">
            <thead><tr>
                <th style="text-align:left">Account</th>
                <th style="text-align:right">Balance</th>
                <th style="text-align:right">Gained</th>
                <th style="text-align:right">Autohunt</th>
                <th style="text-align:right">Upkeep</th>
                <th style="text-align:right">Net</th>
                <th style="text-align:right">Per hour</th>
                <th style="text-align:left">Run</th>
                <th style="text-align:left">Last event</th>
                <th></th>
            </tr></thead>
            <tbody>${rows.map(earningRow).join('')}</tbody>
        </table></div>`;
}

function earningRow(a) {
    const state = !a.enabled ? '<span style="color:var(--text-dim,#888)">off</span>'
        : a.paused ? '<span style="color:var(--warning,#f59e0b)">paused</span>'
            : '<span style="color:var(--success,#22c55e)">earning</span>';
    return `
        <tr>
            <td style="text-align:left">${escHtml(a.name)}<br><span style="font-size:0.75em; color:var(--text-dim,#888)">${state}</span></td>
            <td style="text-align:right">${earnNum(a.current_cash)}</td>
            <td style="text-align:right; color:var(--success,#22c55e)">${earnNum(a.gained)}</td>
            <td style="text-align:right; color:var(--rose,#f43f5e)">${earnNum(a.spent_autohunt)}</td>
            <td style="text-align:right; color:var(--rose,#f43f5e)">${earnNum(a.spent_hunt)}</td>
            <td style="text-align:right; color:${earnColor(a.net)}">${earnSigned(a.net)}</td>
            <td style="text-align:right; color:${earnColor(a.per_hour)}">${earnSigned(a.per_hour)}</td>
            <td style="text-align:left">${escHtml(earnDuration(a.hours))}</td>
            <td style="text-align:left; font-size:0.8em; color:var(--text-dim,#888)">${escHtml(a.last_event || '—')}</td>
            <td style="text-align:right"><button class="btn-control" style="padding:4px 8px"
                onclick="resetEarning('${escAttr(a.id)}', this)">Reset</button></td>
        </tr>`;
}

async function earningPost(url, body, btn) {
    const label = btn ? btn.innerHTML : null;
    if (btn) { btn.disabled = true; btn.style.opacity = '0.6'; }
    try {
        const r = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        const d = await r.json();
        if (!d || !d.success) throw new Error((d && d.error) || 'request failed');
        return d;
    } catch (e) {
        showToast(String(e.message || e), 'error');
        return null;
    } finally {
        if (btn) { btn.disabled = false; btn.style.opacity = ''; if (label) btn.innerHTML = label; }
    }
}

window.toggleEarning = async function (on, btn) {
    const d = await earningPost('/api/earning/toggle', { enabled: !!on }, btn);
    if (!d) return;
    showToast(on ? 'Earning mode ON — accounts redirected to hunt + huntbot + sell'
        : 'Earning mode OFF — your own command settings are back', 'success');
    // the command gates just changed under the config editor, so re-read it too
    if (typeof loadConfig === 'function') loadConfig();
    loadEarning();
};

window.saveEarningField = async function (key, value, btn) {
    const body = {};
    body[key] = value;
    const d = await earningPost('/api/earning/toggle', body, btn);
    if (!d) return;
    showToast('Saved', 'success');
    loadEarning();
};

window.resetEarning = async function (id, btn) {
    const d = await earningPost('/api/earning/reset', id ? { id: id } : {}, btn);
    if (!d) return;
    showToast(`Ledger reset for ${d.reset} account(s)`, 'success');
    loadEarning();
};

// Polled only while the tab is open: the ledger lives on the server and keeps
// counting regardless, so there is nothing to lose by not fetching it.
window.startEarningPoll = function () {
    if (earningTimer) clearInterval(earningTimer);
    earningTimer = setInterval(() => {
        const view = document.getElementById('earning');
        if (view && view.classList.contains('active-view') && !document.hidden) loadEarning();
    }, 5000);
};
