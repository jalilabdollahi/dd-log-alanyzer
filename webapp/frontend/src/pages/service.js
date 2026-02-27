/**
 * Service Detail page — live Datadog log viewer for a specific service, grouped by severity.
 */

import { api } from '../api.js';

export async function renderServiceDetail($el, serviceName) {
    const timeRanges = ['last 15m', 'last 1h', 'last 6h', 'last 24h'];

    $el.innerHTML = `
        <div class="page-header">
            <div style="display: flex; align-items: center; gap: 0.75rem;">
                <a href="#dashboard" class="btn btn-sm btn-secondary">← Back</a>
                <div>
                    <h1><span class="svc-tag" style="font-size: 1rem; padding: 4px 12px;">${esc(serviceName)}</span> Live Logs</h1>
                    <p>Real-time logs from Datadog for <strong>${esc(serviceName)}</strong></p>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="toolbar">
                <select id="svc-time" class="form-input" style="min-width: 140px;">
                    ${timeRanges.map(t => `<option value="${t}" ${t === 'last 1h' ? 'selected' : ''}>${t}</option>`).join('')}
                </select>
                <select id="svc-limit" class="form-input" style="min-width: 80px;">
                    <option value="100">100</option>
                    <option value="200">200</option>
                    <option value="500" selected>500</option>
                </select>
                <button id="svc-fetch" class="btn btn-primary">🔄 Fetch Logs</button>
                <button id="svc-auto" class="btn btn-secondary">▶ Auto-refresh</button>
                <span id="svc-status" style="color: var(--text-muted); font-size: 0.85rem;"></span>
            </div>
        </div>

        <div id="svc-stats" class="stat-grid" style="margin-bottom: 1rem;"></div>

        <!-- Severity tabs -->
        <div class="card">
            <div class="severity-tabs" id="svc-tabs" style="display: flex; gap: 0; margin-bottom: 1rem;">
                <button class="sev-tab active" data-filter="all">All</button>
                <button class="sev-tab" data-filter="error">🔴 Error</button>
                <button class="sev-tab" data-filter="warn">🟡 Warning</button>
                <button class="sev-tab" data-filter="info">🔵 Info</button>
                <button class="sev-tab" data-filter="debug">⚪ Debug</button>
            </div>
            <div id="svc-logs">
                <div class="empty-state"><div class="icon">🔍</div>Click "Fetch Logs" to load live data from Datadog</div>
            </div>
        </div>
    `;

    let allLogs = [];
    let autoInterval = null;
    let currentFilter = 'all';

    // Severity tabs
    document.getElementById('svc-tabs').addEventListener('click', (e) => {
        const tab = e.target.closest('.sev-tab');
        if (!tab) return;
        document.querySelectorAll('.sev-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        currentFilter = tab.dataset.filter;
        renderLogs();
    });

    document.getElementById('svc-fetch').addEventListener('click', fetchLogs);

    // Auto-refresh toggle
    const $autoBtn = document.getElementById('svc-auto');
    $autoBtn.addEventListener('click', () => {
        if (autoInterval) {
            clearInterval(autoInterval);
            autoInterval = null;
            $autoBtn.textContent = '▶ Auto-refresh';
            $autoBtn.classList.remove('btn-danger');
            $autoBtn.classList.add('btn-secondary');
            document.getElementById('svc-status').textContent = '';
        } else {
            fetchLogs();
            autoInterval = setInterval(fetchLogs, 15000);
            $autoBtn.textContent = '⏸ Stop';
            $autoBtn.classList.remove('btn-secondary');
            $autoBtn.classList.add('btn-danger');
            document.getElementById('svc-status').textContent = 'Auto-refreshing every 15s';
        }
    });

    async function fetchLogs() {
        const time_range = document.getElementById('svc-time').value;
        const limit = document.getElementById('svc-limit').value;
        const $logs = document.getElementById('svc-logs');
        const $status = document.getElementById('svc-status');

        if (!autoInterval) {
            $logs.innerHTML = '<div class="loading-overlay"><span class="spinner"></span> Querying Datadog...</div>';
        }
        $status.textContent = autoInterval ? `Auto-refreshing... (${new Date().toLocaleTimeString()})` : 'Fetching...';

        try {
            const data = await api.logs({ query: `service:${serviceName}`, time_range, limit });
            allLogs = data.logs;
            renderStats();
            renderLogs();
            $status.textContent = autoInterval
                ? `Last updated: ${new Date().toLocaleTimeString()} • Auto-refreshing every 15s`
                : `${allLogs.length} logs fetched`;
        } catch (err) {
            $logs.innerHTML = `<p style="color: var(--danger);">⚠️ ${esc(err.message)}</p>`;
            $status.textContent = '';
        }
    }

    function renderStats() {
        const counts = { error: 0, warn: 0, info: 0, debug: 0, critical: 0 };
        allLogs.forEach(l => { counts[l.status] = (counts[l.status] || 0) + 1; });

        const $stats = document.getElementById('svc-stats');
        $stats.innerHTML = `
            <div class="stat-card">
                <div class="stat-value accent">${allLogs.length}</div>
                <div class="stat-label">Total Logs</div>
            </div>
            <div class="stat-card">
                <div class="stat-value danger">${counts.error + counts.critical}</div>
                <div class="stat-label">Errors</div>
            </div>
            <div class="stat-card">
                <div class="stat-value warning">${counts.warn}</div>
                <div class="stat-label">Warnings</div>
            </div>
            <div class="stat-card">
                <div class="stat-value success">${counts.info}</div>
                <div class="stat-label">Info</div>
            </div>
        `;
    }

    function renderLogs() {
        const $logs = document.getElementById('svc-logs');
        const filtered = currentFilter === 'all'
            ? allLogs
            : allLogs.filter(l => l.status === currentFilter || (currentFilter === 'error' && l.status === 'critical'));

        // Update tab counts
        const counts = { all: allLogs.length, error: 0, warn: 0, info: 0, debug: 0 };
        allLogs.forEach(l => {
            if (l.status === 'error' || l.status === 'critical') counts.error++;
            else if (l.status === 'warn') counts.warn++;
            else if (l.status === 'info') counts.info++;
            else counts.debug++;
        });
        document.querySelectorAll('.sev-tab').forEach(t => {
            const f = t.dataset.filter;
            const c = counts[f] || 0;
            const labels = { all: 'All', error: '🔴 Error', warn: '🟡 Warning', info: '🔵 Info', debug: '⚪ Debug' };
            t.textContent = `${labels[f]} (${c})`;
        });

        if (filtered.length === 0) {
            $logs.innerHTML = `<div class="empty-state"><div class="icon">✅</div>No ${currentFilter === 'all' ? '' : currentFilter + ' '}logs found</div>`;
            return;
        }

        $logs.innerHTML = `
            <table>
                <tr>
                    <th style="width: 160px;">Timestamp</th>
                    <th style="width: 60px;">Level</th>
                    <th style="width: 100px;">Host</th>
                    <th>Message</th>
                </tr>
                ${filtered.map(l => `
                    <tr>
                        <td style="white-space: nowrap; font-size: 0.8rem; color: var(--text-secondary);">${formatTime(l.timestamp)}</td>
                        <td><span class="badge ${statusBadge(l.status)}">${l.status.toUpperCase()}</span></td>
                        <td style="font-size: 0.8rem; color: var(--text-muted);">${esc(l.host || '—')}</td>
                        <td class="log-msg ${l.status === 'error' || l.status === 'critical' ? 'error' : l.status === 'warn' ? 'warn' : ''}" style="max-width: 800px;">${esc(l.message || '—')}</td>
                    </tr>
                `).join('')}
            </table>
        `;
    }
}

function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function statusBadge(s) {
    if (s === 'error' || s === 'critical') return 'badge-critical';
    if (s === 'warn') return 'badge-warning';
    return 'badge-info';
}
function formatTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts);
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}
