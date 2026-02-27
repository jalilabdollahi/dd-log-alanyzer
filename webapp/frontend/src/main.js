/**
 * Main entry — SPA router, auth state, page mounting.
 */

import { api, setToken, isLoggedIn } from './api.js';
import { renderDashboard } from './pages/dashboard.js';
import { renderAnomalies } from './pages/anomalies.js';
import { renderLogs } from './pages/logs.js';
import { renderSettings } from './pages/settings.js';
import { renderServiceDetail } from './pages/service.js';

const pages = {
    dashboard: renderDashboard,
    anomalies: renderAnomalies,
    logs: renderLogs,
    settings: renderSettings,
};

const $login = document.getElementById('login-screen');
const $shell = document.getElementById('app-shell');
const $page = document.getElementById('page-content');
const $loginForm = document.getElementById('login-form');
const $loginErr = document.getElementById('login-error');
const $logoutBtn = document.getElementById('logout-btn');

// ---- Auth ----
function showLogin() {
    $login.classList.remove('hidden');
    $shell.classList.add('hidden');
}

function showApp() {
    $login.classList.add('hidden');
    $shell.classList.remove('hidden');
    route();
}

$loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    $loginErr.textContent = '';
    const user = document.getElementById('login-user').value;
    const pass = document.getElementById('login-pass').value;
    try {
        const { token } = await api.login(user, pass);
        setToken(token);
        showApp();
    } catch (err) {
        $loginErr.textContent = err.message || 'Login failed';
    }
});

$logoutBtn.addEventListener('click', () => {
    setToken(null);
    showLogin();
});

// ---- Router ----
function route() {
    const hash = window.location.hash.replace('#', '') || 'dashboard';

    // Handle service/:name route
    if (hash.startsWith('service/')) {
        const serviceName = decodeURIComponent(hash.replace('service/', ''));
        document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
        $page.innerHTML = '<div class="loading-overlay"><span class="spinner"></span> Loading...</div>';
        renderServiceDetail($page, serviceName);
        return;
    }

    const render = pages[hash] || pages.dashboard;

    // Update active nav link
    document.querySelectorAll('.nav-link').forEach((el) => {
        el.classList.toggle('active', el.dataset.page === hash);
    });

    $page.innerHTML = '<div class="loading-overlay"><span class="spinner"></span> Loading...</div>';
    render($page);
}

window.addEventListener('hashchange', route);

// ---- Toast ----
let $toastContainer = document.querySelector('.toast-container');
if (!$toastContainer) {
    $toastContainer = document.createElement('div');
    $toastContainer.className = 'toast-container';
    document.body.appendChild($toastContainer);
}

export function toast(msg, type = 'success') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    $toastContainer.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

// ---- Init ----
if (isLoggedIn()) {
    api.me().then(() => showApp()).catch(() => showLogin());
} else {
    showLogin();
}
