/**
 * Settings page — manage presets, analysis thresholds, and alert config.
 */

import { api } from '../api.js';
import { toast } from '../main.js';

export async function renderSettings($el) {
    try {
        const [config, presets] = await Promise.all([api.config(), api.presets()]);

        $el.innerHTML = `
            <div class="page-header">
                <h1>Settings</h1>
                <p>Manage presets, analysis thresholds, and alert configuration</p>
            </div>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem;">
                <!-- Presets -->
                <div class="card" style="grid-column: 1 / -1;">
                    <div class="card-header">
                        <h2>📋 Query Presets</h2>
                        <button id="preset-add-btn" class="btn btn-sm btn-primary">+ Add Preset</button>
                    </div>
                    <div id="preset-list">
                        ${Object.keys(presets).length > 0 ? `
                            <table>
                                <tr><th>Name</th><th>Query</th><th>Description</th><th>Services</th><th style="width: 100px;">Actions</th></tr>
                                ${Object.entries(presets).map(([name, p]) => `
                                    <tr id="preset-row-${esc(name)}">
                                        <td><strong>${esc(name)}</strong></td>
                                        <td style="font-family: var(--mono); font-size: 0.8rem;">${esc(p.query)}</td>
                                        <td style="font-size: 0.85rem; color: var(--text-secondary);">${esc(p.description || '—')}</td>
                                        <td>${(p.services || []).map(s => `<span class="svc-tag" style="margin-right:3px;">${esc(s)}</span>`).join('') || '—'}</td>
                                        <td>
                                            <button class="btn btn-sm btn-secondary preset-edit" data-name="${esc(name)}">Edit</button>
                                            <button class="btn btn-sm btn-danger preset-del" data-name="${esc(name)}">✕</button>
                                        </td>
                                    </tr>
                                `).join('')}
                            </table>
                        ` : '<div class="empty-state">No presets configured</div>'}
                    </div>

                    <div id="preset-form" class="hidden" style="margin-top: 1rem; padding: 1rem; background: var(--bg-primary); border-radius: var(--radius); border: 1px solid var(--border);">
                        <h3 style="margin-bottom: 1rem; font-size: 1rem;" id="preset-form-title">Add Preset</h3>
                        <div class="form-row">
                            <div class="form-group">
                                <label>Preset Name</label>
                                <input type="text" id="pf-name" class="form-input" placeholder="e.g. high-latency">
                            </div>
                            <div class="form-group">
                                <label>Query</label>
                                <input type="text" id="pf-query" class="form-input" placeholder="e.g. @duration:>5000 status:error">
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>Description</label>
                                <input type="text" id="pf-desc" class="form-input" placeholder="What this preset monitors...">
                            </div>
                            <div class="form-group">
                                <label>Services (comma-separated)</label>
                                <input type="text" id="pf-services" class="form-input" placeholder="e.g. api-gateway, auth-service">
                            </div>
                        </div>
                        <div style="display: flex; gap: 0.5rem; margin-top: 0.5rem;">
                            <button id="pf-save" class="btn btn-primary">Save</button>
                            <button id="pf-cancel" class="btn btn-secondary">Cancel</button>
                        </div>
                    </div>
                </div>

                <!-- Analysis Config -->
                <div class="card">
                    <div class="card-header">
                        <h2>🔬 Analysis Thresholds</h2>
                    </div>
                    <div class="form-group">
                        <label>Anomaly Z-Score Threshold</label>
                        <input type="number" id="cfg-zscore" class="form-input" step="0.1" value="${config.analysis.anomaly_zscore_threshold}">
                    </div>
                    <div class="form-group">
                        <label>Burst Window (seconds)</label>
                        <input type="number" id="cfg-burst-window" class="form-input" value="${config.analysis.burst_window_seconds}">
                    </div>
                    <div class="form-group">
                        <label>Burst Min Count</label>
                        <input type="number" id="cfg-burst-min" class="form-input" value="${config.analysis.burst_min_count}">
                    </div>
                    <div class="form-group">
                        <label>Sample Size</label>
                        <input type="number" id="cfg-sample" class="form-input" value="${config.analysis.sample_size}">
                    </div>
                    <button id="cfg-analysis-save" class="btn btn-primary">Save Analysis Config</button>
                </div>

                <!-- Alert Config -->
                <div class="card">
                    <div class="card-header">
                        <h2>🔔 Alert Configuration</h2>
                    </div>
                    <div class="form-group">
                        <label>Cooldown (minutes)</label>
                        <input type="number" id="cfg-cooldown" class="form-input" value="${config.alerts.cooldown_minutes}">
                    </div>
                    <div class="form-group">
                        <label>Severity Threshold</label>
                        <select id="cfg-severity" class="form-input">
                            <option value="info" ${config.alerts.severity_threshold === 'info' ? 'selected' : ''}>Info</option>
                            <option value="warning" ${config.alerts.severity_threshold === 'warning' ? 'selected' : ''}>Warning</option>
                            <option value="critical" ${config.alerts.severity_threshold === 'critical' ? 'selected' : ''}>Critical</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Environment</label>
                        <input type="text" id="cfg-env" class="form-input" value="${esc(config.scope.env || '')}">
                    </div>
                    <div class="form-group">
                        <label>Slack Enabled</label>
                        <select id="cfg-slack" class="form-input">
                            <option value="true" ${config.slack.enabled ? 'selected' : ''}>Yes</option>
                            <option value="false" ${!config.slack.enabled ? 'selected' : ''}>No</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Jira Enabled</label>
                        <select id="cfg-jira" class="form-input">
                            <option value="true" ${config.jira.enabled ? 'selected' : ''}>Yes</option>
                            <option value="false" ${!config.jira.enabled ? 'selected' : ''}>No</option>
                        </select>
                    </div>
                    <button id="cfg-alerts-save" class="btn btn-primary">Save Alert Config</button>
                </div>
            </div>
        `;

        // ---- Preset interactions ----
        const $form = document.getElementById('preset-form');

        document.getElementById('preset-add-btn').addEventListener('click', () => {
            document.getElementById('preset-form-title').textContent = 'Add Preset';
            document.getElementById('pf-name').value = '';
            document.getElementById('pf-name').disabled = false;
            document.getElementById('pf-query').value = '';
            document.getElementById('pf-desc').value = '';
            document.getElementById('pf-services').value = '';
            $form.classList.remove('hidden');
        });

        document.getElementById('pf-cancel').addEventListener('click', () => $form.classList.add('hidden'));

        // Edit
        document.querySelectorAll('.preset-edit').forEach(btn => {
            btn.addEventListener('click', () => {
                const name = btn.dataset.name;
                const p = presets[name];
                document.getElementById('preset-form-title').textContent = `Edit: ${name}`;
                document.getElementById('pf-name').value = name;
                document.getElementById('pf-name').disabled = true;
                document.getElementById('pf-query').value = p.query;
                document.getElementById('pf-desc').value = p.description || '';
                document.getElementById('pf-services').value = (p.services || []).join(', ');
                $form.classList.remove('hidden');
            });
        });

        // Delete
        document.querySelectorAll('.preset-del').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = btn.dataset.name;
                if (!confirm(`Delete preset "${name}"?`)) return;
                try {
                    await api.deletePreset(name);
                    toast(`Preset "${name}" deleted`, 'success');
                    renderSettings($el);
                } catch (err) {
                    toast(err.message, 'error');
                }
            });
        });

        // Save preset
        document.getElementById('pf-save').addEventListener('click', async () => {
            const name = document.getElementById('pf-name').value.trim();
            const query = document.getElementById('pf-query').value.trim();
            if (!name || !query) { toast('Name and query are required', 'error'); return; }
            const desc = document.getElementById('pf-desc').value.trim();
            const services = document.getElementById('pf-services').value.split(',').map(s => s.trim()).filter(Boolean);

            try {
                await api.upsertPreset(name, { query, description: desc, services });
                toast(`Preset "${name}" saved`, 'success');
                renderSettings($el);
            } catch (err) {
                toast(err.message, 'error');
            }
        });

        // ---- Save analysis config ----
        document.getElementById('cfg-analysis-save').addEventListener('click', async () => {
            try {
                await api.updateConfig({
                    analysis: {
                        anomaly_zscore_threshold: parseFloat(document.getElementById('cfg-zscore').value),
                        burst_window_seconds: parseInt(document.getElementById('cfg-burst-window').value),
                        burst_min_count: parseInt(document.getElementById('cfg-burst-min').value),
                        sample_size: parseInt(document.getElementById('cfg-sample').value),
                    },
                });
                toast('Analysis config saved', 'success');
            } catch (err) {
                toast(err.message, 'error');
            }
        });

        // ---- Save alert config ----
        document.getElementById('cfg-alerts-save').addEventListener('click', async () => {
            try {
                await api.updateConfig({
                    alerts: {
                        cooldown_minutes: parseInt(document.getElementById('cfg-cooldown').value),
                        severity_threshold: document.getElementById('cfg-severity').value,
                    },
                    scope: {
                        env: document.getElementById('cfg-env').value.trim() || null,
                    },
                });
                toast('Alert config saved', 'success');
            } catch (err) {
                toast(err.message, 'error');
            }
        });

    } catch (err) {
        $el.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div>${esc(err.message)}</div>`;
    }
}

function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
