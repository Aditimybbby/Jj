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


function initConfigSearch() {
    const input = document.getElementById('config-search');
    if (!input) return;
    input.addEventListener('input', () => filterConfigSearch(input.value));
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') clearConfigSearch();
    });
}

function updateMobileControls() {
    const mobileControls = document.getElementById('mobileControls');
    if (!mobileControls) return;
    const isDashboard = document.getElementById('dash').classList.contains('active-view');
    if (isDashboard && window.innerWidth <= 768) {
        mobileControls.style.display = 'flex';
    } else {
        mobileControls.style.display = 'none';
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    console.log("DOM Content Loaded - Initializing...");
    // resolve the session first so the CSRF token is in place and the Users tab is
    // hidden before anything renders. Accounts and proxies are per-space now, so
    // every role polls them - a plain user just sees their own.
    if (typeof applySessionRole === 'function') {
        await applySessionRole();
    }
    initDashCharts();
    window.fetchAccounts();
    if (typeof fetchProxies === 'function') fetchProxies();
    fetchAccountConfig();
    loadConfig();
    if (typeof loadCustomCommands === 'function') loadCustomCommands();
    initDynamicTilt();
    initConfigSearch();

    // Polling intervals. The bots themselves run in the server's asyncio loop,
    // so they keep farming even when this tab is closed - these polls only
    // refresh what the dashboard *shows*. When the tab is hidden we slow them
    // down (no point fetching a live chart nobody is looking at), and on focus
    // we refresh immediately so a returning user sees the real, current state
    // instead of a stale snapshot that made accounts look "stopped".
    let configTimer = setInterval(fetchAccountConfig, 5000);
    let accountsTimer = setInterval(window.fetchAccounts, 5000);
    let statsTimer = setInterval(update, 1000);
    let captchaTimer = setInterval(window.pollForCaptchas, 2000);

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            // slow down while hidden - keep a heartbeat so the session stays
            // warm but stop refreshing charts every second
            clearInterval(statsTimer);
            statsTimer = setInterval(update, 10000);
        } else {
            // back to active: refresh now and restore the fast cadence
            clearInterval(statsTimer);
            statsTimer = setInterval(update, 1000);
            window.fetchAccounts();
            fetchAccountConfig();
            update();
        }
    });

    // If the browser discarded the page (bfcache) and restored it, re-sync.
    window.addEventListener('pageshow', (event) => {
        if (event.persisted) {
            window.fetchAccounts();
            fetchAccountConfig();
            update();
        }
    });

    updateMobileControls();
    window.addEventListener('resize', updateMobileControls);
});