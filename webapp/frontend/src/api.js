/**
 * API client — wraps fetch with auth token injection.
 */

const API_BASE = '/api';

let _token = localStorage.getItem('dd_token') || null;

export function setToken(token) {
    _token = token;
    if (token) localStorage.setItem('dd_token', token);
    else localStorage.removeItem('dd_token');
}

export function getToken() { return _token; }

export function isLoggedIn() { return !!_token; }

async function request(method, path, body = null) {
    const headers = { 'Content-Type': 'application/json' };
    if (_token) headers['Authorization'] = `Bearer ${_token}`;

    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(`${API_BASE}${path}`, opts);

    if (res.status === 401) {
        setToken(null);
        window.location.hash = '';
        window.location.reload();
        throw new Error('Session expired');
    }
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

export const api = {
    login: (username, password) => request('POST', '/auth/login', { username, password }),
    me: () => request('GET', '/auth/me'),
    dashboard: () => request('GET', '/dashboard'),
    anomalies: (params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request('GET', `/anomalies?${qs}`);
    },
    analyze: (data) => request('POST', '/analyze', data),
    logs: (params = {}) => {
        const qs = new URLSearchParams(params).toString();
        return request('GET', `/logs?${qs}`);
    },
    config: () => request('GET', '/config'),
    updateConfig: (data) => request('PUT', '/config', data),
    presets: () => request('GET', '/config/presets'),
    upsertPreset: (name, data) => request('POST', `/config/presets/${name}`, data),
    deletePreset: (name) => request('DELETE', `/config/presets/${name}`),
};
