/**
 * Anomaly History page — paginated table with filters.
 */

import { api } from '../api.js';

let _offset = 0;
const LIMIT = 25;

export async function renderAnomalies($el) {
    $el.innerHTML = `
        <div class="page-header">
            <h1>Anomaly History</h1>
            <p>All detected anomalies, persisted across sessions</p>
        </div>

        <div class="toolbar">
            <input type="text" id="an-service" class="form-input" placeholder="Filter by service..." style="min-width: 180px;">
            <select id="an-severity" class="form-input">
                <option value="">All severities</option>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
            </select>
            <select id="an-since" class="form-input">
                <option value="">All time</option>
                <option value="1h">Last 1h</option>
                <option value="6h">Last 6h</option>
                <option value="24h">Last 24h</option>
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
            </select>
            <button id="an-search" class="btn btn-primary">Search</button>
        </div>

        <div class="card">
            <div id="an-results"></div>
            <div id="an-pagination" class="pagination"></div>
        </div>
    `;

    const $search = document.getElementById('an-search');
    $search.addEventListener('click', () => { _offset = 0; loadAnomalies(); });

    // Enter key search
    document.getElementById('an-service').addEventListener('keydown', e => { if (e.key === 'Enter') { _offset = 0; loadAnomalies(); } });

    _offset = 0;
    loadAnomalies();
}

async function loadAnomalies() {
    const $results = document.getElementById('an-results');
    const $pag = document.getElementById('an-pagination');
    $results.innerHTML = '<div class="loading-overlay"><span class="spinner"></span> Loading...</div>';

    const service = document.getElementById('an-service')?.value || '';
    const severity = document.getElementById('an-severity')?.value || '';
    const sinceOption = document.getElementById('an-since')?.value || '';

    let since = '';
    if (sinceOption) {
        const now = new Date();
        const ms = { '1h': 3600e3, '6h': 21600e3, '24h': 86400e3, '7d': 604800e3, '30d': 2592000e3 };
        since = new Date(now - (ms[sinceOption] || 0)).toISOString();
    }

    const params = { limit: LIMIT, offset: _offset };
    if (service) params.service = service;
    if (severity) params.severity = severity;
    if (since) params.since = since;

    try {
        const data = await api.anomalies(params);
        const { anomalies, total } = data;

        if (anomalies.length === 0) {
            $results.innerHTML = '<div class="empty-state"><div class="icon">📭</div>No anomalies found</div>';
            $pag.innerHTML = '';
            return;
        }

        $results.innerHTML = `
            <table>
                <tr>
                    <th>Timestamp</th>
                    <th>Service</th>
                    <th>Severity</th>
                    <th>Type</th>
                    <th>Description</th>
                    <th>Metric</th>
                </tr>
                ${anomalies.map(a => `
                    <tr>
                        <td style="white-space: nowrap; font-size: 0.8rem; color: var(--text-secondary);">${formatTime(a.timestamp)}</td>
                        <td><span class="svc-tag">${esc(a.service || '—')}</span></td>
                        <td><span class="badge badge-${a.severity}">${a.severity.toUpperCase()}</span></td>
                        <td>${esc(a.anomaly_type)}</td>
                        <td style="max-width: 450px; font-size: 0.85rem;">${esc(trunc(a.description, 200))}</td>
                        <td style="font-family: var(--mono); font-size: 0.8rem;">${a.metric_value?.toFixed(1) || '—'}</td>
                    </tr>
                `).join('')}
            </table>
        `;

        const totalPages = Math.ceil(total / LIMIT);
        const currentPage = Math.floor(_offset / LIMIT) + 1;
        $pag.innerHTML = `
            <button class="btn btn-sm btn-secondary" ${_offset === 0 ? 'disabled' : ''} id="an-prev">← Prev</button>
            <span class="info">Page ${currentPage} of ${totalPages} (${total} total)</span>
            <button class="btn btn-sm btn-secondary" ${_offset + LIMIT >= total ? 'disabled' : ''} id="an-next">Next →</button>
        `;

        document.getElementById('an-prev')?.addEventListener('click', () => { _offset = Math.max(0, _offset - LIMIT); loadAnomalies(); });
        document.getElementById('an-next')?.addEventListener('click', () => { _offset += LIMIT; loadAnomalies(); });

    } catch (err) {
        $results.innerHTML = `<p style="color: var(--danger);">⚠️ ${esc(err.message)}</p>`;
    }
}

function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function trunc(s, n) { return (s || '').length > n ? s.slice(0, n) + '…' : s || ''; }
function formatTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) + ' ' +
           d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
