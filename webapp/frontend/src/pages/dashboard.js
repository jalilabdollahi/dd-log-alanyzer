/**
 * Dashboard page — service health grid, anomaly stats, recent anomalies.
 */

import { api } from '../api.js';

export async function renderDashboard($el) {
    try {
        const data = await api.dashboard();
        const { anomalies_24h, critical_24h, warning_24h, service_counts, recent_anomalies, presets, analysis_config } = data;

        // Group service counts
        const svcMap = {};
        for (const row of service_counts) {
            if (!svcMap[row.service]) svcMap[row.service] = { critical: 0, warning: 0 };
            svcMap[row.service][row.severity] = row.count;
        }

        $el.innerHTML = `
            <div class="page-header">
                <h1>Dashboard</h1>
                <p>Last 24 hours overview</p>
            </div>

            <div class="stat-grid">
                <div class="stat-card">
                    <div class="stat-value ${anomalies_24h > 0 ? 'danger' : 'success'}">${anomalies_24h}</div>
                    <div class="stat-label">Total Anomalies (24h)</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value danger">${critical_24h}</div>
                    <div class="stat-label">Critical</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value warning">${warning_24h}</div>
                    <div class="stat-label">Warnings</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value accent">${Object.keys(presets).length}</div>
                    <div class="stat-label">Active Presets</div>
                </div>
            </div>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem;">
                <div class="card">
                    <div class="card-header">
                        <h2>🏢 Services</h2>
                    </div>
                    ${Object.keys(svcMap).length > 0 ? `
                        <table>
                            <tr><th>Service</th><th>Critical</th><th>Warning</th></tr>
                            ${Object.entries(svcMap).map(([svc, counts]) => `
                                <tr>
                                    <td><a href="#service/${encodeURIComponent(svc)}" class="svc-link"><span class="svc-tag">${esc(svc)}</span></a></td>
                                    <td>${counts.critical ? `<span class="badge badge-critical">${counts.critical}</span>` : '—'}</td>
                                    <td>${counts.warning ? `<span class="badge badge-warning">${counts.warning}</span>` : '—'}</td>
                                </tr>
                            `).join('')}
                        </table>
                    ` : `
                        <div class="empty-state"><div class="icon">✅</div>No anomalies detected in the last 24h.
                        <p style="margin-top: 0.5rem; font-size: 0.85rem;">Showing services from your config scope:</p>
                        </div>
                    `}
                    <div id="dash-scope-services" style="padding: 0.5rem;"></div>
                </div>

                <div class="card">
                    <div class="card-header">
                        <h2>📋 Active Presets</h2>
                    </div>
                    <table>
                        <tr><th>Name</th><th>Query</th></tr>
                        ${Object.entries(presets).map(([name, p]) => `
                            <tr>
                                <td><strong>${esc(name)}</strong></td>
                                <td style="font-family: var(--mono); font-size: 0.8rem; color: var(--text-secondary);">${esc(p.query)}</td>
                            </tr>
                        `).join('')}
                    </table>
                </div>
            </div>

            <div class="card" style="margin-top: 1.5rem;">
                <div class="card-header">
                    <h2>🕐 Recent Anomalies</h2>
                    <a href="#anomalies" class="btn btn-sm btn-secondary">View All</a>
                </div>
                ${recent_anomalies.length > 0 ? `
                    <table>
                        <tr><th>Time</th><th>Service</th><th>Severity</th><th>Type</th><th>Description</th></tr>
                        ${recent_anomalies.map(a => `
                            <tr>
                                <td style="white-space: nowrap; font-size: 0.8rem; color: var(--text-secondary);">${formatTime(a.timestamp)}</td>
                                <td><a href="#service/${encodeURIComponent(a.service || '')}"><span class="svc-tag">${esc(a.service || '—')}</span></a></td>
                                <td><span class="badge badge-${a.severity}">${a.severity.toUpperCase()}</span></td>
                                <td>${esc(a.anomaly_type)}</td>
                                <td style="max-width: 400px; font-size: 0.85rem;">${esc(trunc(a.description, 120))}</td>
                            </tr>
                        `).join('')}
                    </table>
                ` : '<div class="empty-state"><div class="icon">📭</div>No anomalies recorded yet. Run an analysis to populate.</div>'}
            </div>

            <div class="card">
                <div class="card-header">
                    <h2>⚡ Quick Analysis</h2>
                </div>
                <div class="toolbar">
                    <select id="quick-preset" class="form-input" style="min-width: 200px;">
                        <option value="">Custom query</option>
                        ${Object.entries(presets).map(([name, p]) =>
                            `<option value="${esc(name)}">${esc(name)}: ${esc(p.description || p.query)}</option>`
                        ).join('')}
                    </select>
                    <input type="text" id="quick-query" class="form-input" placeholder="Datadog query..." value="*" style="min-width: 250px;">
                    <select id="quick-time" class="form-input">
                        <option value="last 15m">Last 15m</option>
                        <option value="last 1h" selected>Last 1h</option>
                        <option value="last 6h">Last 6h</option>
                        <option value="last 24h">Last 24h</option>
                    </select>
                    <button id="quick-run" class="btn btn-primary">▶ Run</button>
                </div>
                <div id="quick-result"></div>
            </div>
        `;

        // Quick analysis
        document.getElementById('quick-run').addEventListener('click', async () => {
            const preset = document.getElementById('quick-preset').value;
            const query = document.getElementById('quick-query').value;
            const time_range = document.getElementById('quick-time').value;
            const $result = document.getElementById('quick-result');
            $result.innerHTML = '<div class="loading-overlay"><span class="spinner"></span> Analyzing...</div>';

            try {
                const res = await api.analyze({ query, preset: preset || undefined, time_range });
                $result.innerHTML = `
                    <div class="stat-grid" style="margin-top: 1rem;">
                        <div class="stat-card">
                            <div class="stat-value accent">${res.total_logs.toLocaleString()}</div>
                            <div class="stat-label">Logs Analyzed</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value ${res.anomalies.length > 0 ? 'danger' : 'success'}">${res.anomalies.length}</div>
                            <div class="stat-label">Anomalies</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value accent">${res.patterns.length}</div>
                            <div class="stat-label">Patterns</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value accent">${res.error_groups.length}</div>
                            <div class="stat-label">Error Groups</div>
                        </div>
                    </div>
                    ${res.anomalies.length > 0 ? `
                        <table style="margin-top: 1rem;">
                            <tr><th>Severity</th><th>Type</th><th>Service</th><th>Description</th></tr>
                            ${res.anomalies.map(a => `
                                <tr>
                                    <td><span class="badge badge-${a.severity}">${a.severity.toUpperCase()}</span></td>
                                    <td>${esc(a.anomaly_type)}</td>
                                    <td><span class="svc-tag">${esc(a.service || '—')}</span></td>
                                    <td style="font-size: 0.85rem;">${esc(trunc(a.description, 200))}</td>
                                </tr>
                            `).join('')}
                        </table>
                    ` : '<p style="text-align:center; color: var(--success); margin-top: 1rem;">✅ No anomalies detected</p>'}
                `;
            } catch (err) {
                $result.innerHTML = `<p style="color: var(--danger); margin-top: 1rem;">⚠️ ${esc(err.message)}</p>`;
            }
        });

        // Show scope services as clickable tags
        const scopeServices = data.scope?.services || [];
        const $scopeSvcs = document.getElementById('dash-scope-services');
        if ($scopeSvcs && scopeServices.length > 0) {
            $scopeSvcs.innerHTML = scopeServices.map(s =>
                `<a href="#service/${encodeURIComponent(s)}" style="margin: 3px; display: inline-block;"><span class="svc-tag">${esc(s)}</span></a>`
            ).join('');
        }

    } catch (err) {
        $el.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div>${esc(err.message)}</div>`;
    }
}

function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function trunc(s, n) { return (s || '').length > n ? s.slice(0, n) + '…' : s || ''; }
function formatTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) + ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}
