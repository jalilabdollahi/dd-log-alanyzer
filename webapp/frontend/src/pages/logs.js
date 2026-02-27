/**
 * Log Viewer page — search Datadog logs with presets or custom queries.
 */

import { api } from '../api.js';

export async function renderLogs($el) {
    let presets = {};
    try { presets = await api.presets(); } catch (_) {}

    $el.innerHTML = `
        <div class="page-header">
            <h1>Log Viewer</h1>
            <p>Search Datadog logs in real-time</p>
        </div>

        <div class="card">
            <div class="toolbar">
                <select id="log-preset" class="form-input" style="min-width: 180px;">
                    <option value="">Custom query</option>
                    ${Object.entries(presets).map(([name, p]) =>
                        `<option value="${esc(name)}">${esc(name)}</option>`
                    ).join('')}
                </select>
                <input type="text" id="log-query" class="form-input" placeholder="Datadog query (e.g. service:auth-service status:error)" value="*" style="flex: 1; min-width: 300px;">
                <select id="log-time" class="form-input">
                    <option value="last 5m">Last 5m</option>
                    <option value="last 15m">Last 15m</option>
                    <option value="last 1h" selected>Last 1h</option>
                    <option value="last 6h">Last 6h</option>
                    <option value="last 24h">Last 24h</option>
                </select>
                <select id="log-limit" class="form-input" style="min-width: 80px;">
                    <option value="50">50</option>
                    <option value="100" selected>100</option>
                    <option value="200">200</option>
                    <option value="500">500</option>
                </select>
                <button id="log-search" class="btn btn-primary">🔍 Search</button>
            </div>
        </div>

        <div class="card">
            <div id="log-results">
                <div class="empty-state"><div class="icon">🔍</div>Enter a query and click Search</div>
            </div>
        </div>
    `;

    // Preset selection auto-fills query
    document.getElementById('log-preset').addEventListener('change', (e) => {
        const name = e.target.value;
        if (name && presets[name]) {
            document.getElementById('log-query').value = presets[name].query;
        }
    });

    document.getElementById('log-search').addEventListener('click', searchLogs);
    document.getElementById('log-query').addEventListener('keydown', e => { if (e.key === 'Enter') searchLogs(); });
}

async function searchLogs() {
    const $results = document.getElementById('log-results');
    $results.innerHTML = '<div class="loading-overlay"><span class="spinner"></span> Querying Datadog...</div>';

    const preset = document.getElementById('log-preset').value;
    const query = document.getElementById('log-query').value || '*';
    const time_range = document.getElementById('log-time').value;
    const limit = document.getElementById('log-limit').value;

    try {
        const data = await api.logs({ query: preset ? '*' : query, preset: preset || undefined, time_range, limit });
        const { logs, total } = data;

        if (logs.length === 0) {
            $results.innerHTML = '<div class="empty-state"><div class="icon">📭</div>No logs found for this query</div>';
            return;
        }

        // Count by status
        const statusCounts = {};
        logs.forEach(l => { statusCounts[l.status] = (statusCounts[l.status] || 0) + 1; });

        $results.innerHTML = `
            <div class="card-header">
                <h2>${total.toLocaleString()} logs found</h2>
                <div style="display: flex; gap: 0.5rem;">
                    ${Object.entries(statusCounts).map(([s, c]) =>
                        `<span class="badge ${s === 'error' ? 'badge-critical' : s === 'warn' ? 'badge-warning' : 'badge-info'}">${s}: ${c}</span>`
                    ).join('')}
                </div>
            </div>
            <table>
                <tr>
                    <th style="width: 140px;">Timestamp</th>
                    <th style="width: 60px;">Level</th>
                    <th style="width: 140px;">Service</th>
                    <th>Message</th>
                </tr>
                ${logs.map(l => `
                    <tr>
                        <td style="white-space: nowrap; font-size: 0.8rem; color: var(--text-secondary);">${formatTime(l.timestamp)}</td>
                        <td><span class="badge ${l.status === 'error' || l.status === 'critical' ? 'badge-critical' : l.status === 'warn' ? 'badge-warning' : 'badge-info'}">${l.status.toUpperCase()}</span></td>
                        <td><span class="svc-tag">${esc(l.service)}</span></td>
                        <td class="log-msg ${l.status === 'error' ? 'error' : l.status === 'warn' ? 'warn' : ''}">${esc(trunc(l.message, 300))}</td>
                    </tr>
                `).join('')}
            </table>
        `;
    } catch (err) {
        $results.innerHTML = `<p style="color: var(--danger);">⚠️ ${esc(err.message)}</p>`;
    }
}

function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function trunc(s, n) { return (s || '').length > n ? s.slice(0, n) + '…' : s || ''; }
function formatTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts);
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}
