(() => {
    'use strict';

    const statusMap = {
        online: { key: 'online', label: 'Online', color: '#22c55e', className: 'bg-success' },
        streaming: { key: 'online', label: 'Online', color: '#22c55e', className: 'bg-success' },
        sleep: { key: 'sleep', label: 'Sleep', color: '#06b6d4', className: 'bg-info' },
        offline: { key: 'offline', label: 'Offline', color: '#ef4444', className: 'bg-danger' },
        locked: { key: 'locked', label: 'Locked', color: '#94a3b8', className: 'bg-secondary' },
        unprovisioned: { key: 'locked', label: 'Unprovisioned', color: '#94a3b8', className: 'bg-secondary' },
        starting: { key: 'sleep', label: 'Starting', color: '#f59e0b', className: 'bg-warning text-dark' },
        configuring: { key: 'sleep', label: 'Configuring', color: '#0ea5e9', className: 'bg-primary' },
        rover: { key: 'rover', label: 'Rover', color: '#0ea5e9', className: 'bg-primary' }
    };

    function getStatusMeta(status) {
        const key = String(status || 'offline').toLowerCase();
        return statusMap[key] || { key, label: key ? key.replace(/_/g, ' ') : 'Unknown', color: '#64748b', className: 'bg-secondary' };
    }

    function formatDistance(meters) {
        const value = Number(meters || 0);
        if (!Number.isFinite(value)) return '--';
        if (value < 1000) return `${Math.round(value)} m`;
        return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} km`;
    }

    function formatSpeed(bitsPerSecond) {
        const value = Number(bitsPerSecond || 0);
        if (!Number.isFinite(value) || value <= 0) return '0 bps';
        if (value < 1000) return `${Math.round(value)} bps`;
        if (value < 1000000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} kbps`;
        return `${(value / 1000000).toFixed(value < 10000000 ? 2 : 1)} Mbps`;
    }

    function formatTime(value) {
        if (!value) return '--';
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString();
    }

    function getToastContainer() {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container position-fixed top-0 end-0 p-3';
            container.style.zIndex = '1100';
            document.body.appendChild(container);
        }
        return container;
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[char]);
    }

    function showToast(type, message) {
        const normalizedType = ['success', 'danger', 'warning', 'info'].includes(type) ? type : 'info';
        const container = getToastContainer();
        const toastEl = document.createElement('div');
        toastEl.className = `toast ops-toast ${normalizedType}`;
        toastEl.setAttribute('role', 'alert');
        toastEl.innerHTML = `
            <div class="d-flex align-items-center">
                <div class="toast-body">${escapeHtml(message)}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        `;
        container.appendChild(toastEl);
        const toast = new bootstrap.Toast(toastEl, { delay: 4500 });
        toast.show();
        toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
    }

    function confirmAction(options = {}) {
        const title = options.title || 'Confirm action';
        const message = options.message || 'Continue?';
        const confirmText = options.confirmText || 'Confirm';
        const cancelText = options.cancelText || 'Cancel';
        const tone = options.tone === 'danger' ? 'danger' : 'primary';

        return new Promise((resolve) => {
            const backdrop = document.createElement('div');
            backdrop.className = 'ops-confirm-backdrop';
            backdrop.innerHTML = `
                <div class="ops-confirm" role="dialog" aria-modal="true">
                    <div class="ops-confirm-head">${escapeHtml(title)}</div>
                    <div class="ops-confirm-body">${escapeHtml(message)}</div>
                    <div class="ops-confirm-actions">
                        <button type="button" class="btn btn-outline-secondary ops-confirm-cancel">${escapeHtml(cancelText)}</button>
                        <button type="button" class="btn btn-${tone} ops-confirm-ok">${escapeHtml(confirmText)}</button>
                    </div>
                </div>
            `;
            const done = (value) => {
                backdrop.remove();
                resolve(value);
            };
            backdrop.addEventListener('click', (event) => {
                if (event.target === backdrop || event.target.closest('.ops-confirm-cancel')) done(false);
                if (event.target.closest('.ops-confirm-ok')) done(true);
            });
            document.addEventListener('keydown', function onKey(event) {
                if (!document.body.contains(backdrop)) {
                    document.removeEventListener('keydown', onKey);
                    return;
                }
                if (event.key === 'Escape') {
                    document.removeEventListener('keydown', onKey);
                    done(false);
                }
            });
            document.body.appendChild(backdrop);
            backdrop.querySelector('.ops-confirm-ok')?.focus();
        });
    }

    window.CorsUI = {
        getStatusMeta,
        formatDistance,
        formatSpeed,
        formatTime,
        showToast,
        confirmAction
    };
})();
