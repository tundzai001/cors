(() => {
    'use strict';

    const $ = (id) => document.getElementById(id);

    function splitTokens(value) {
        return String(value || '')
            .split(/[;,\s\n]+/)
            .map(item => item.trim())
            .filter(Boolean);
    }

    function fmtTime(ts) {
        const value = Number(ts || 0);
        if (!value) return '--';
        return new Date(value * 1000).toLocaleString();
    }

    function fmtBps(value) {
        const n = Number(value || 0);
        if (!Number.isFinite(n) || n <= 0) return '0 B/s';
        if (n < 1024) return `${Math.round(n)} B/s`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB/s`;
        return `${(n / 1024 / 1024).toFixed(2)} MB/s`;
    }

    async function apiJson(url, options = {}) {
        const response = await window.ApiClient.apiFetch(url, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || data.error || `Request failed: ${response.status}`);
        }
        return data;
    }

    function setJson(el, data) {
        if (!el) return;
        el.textContent = JSON.stringify(data, null, 2);
    }

    function badgeClass(severity) {
        if (severity === 'critical' || severity === 'error') return 'bg-danger';
        if (severity === 'warning') return 'bg-warning text-dark';
        if (severity === 'success') return 'bg-success';
        return 'bg-info text-dark';
    }

    function targetHref(alert) {
        if (alert.rover_username) return `/rovers-global.html?rover=${encodeURIComponent(alert.rover_username)}`;
        if (alert.device_serial) return `/?view=dashboard&station=${encodeURIComponent(alert.device_serial)}`;
        return '#';
    }

    function renderAlerts(payload) {
        const container = $('ops-alerts-list');
        if (!container) return;
        const alerts = payload.items || [];
        if (!alerts.length) {
            container.innerHTML = '<div class="ops-empty-state"><i class="bi bi-shield-check"></i><span>No active alerts</span></div>';
            return;
        }
        container.innerHTML = alerts.map(alert => `
            <div class="ops-alert-item ${alert.severity || 'info'}">
                <div class="d-flex justify-content-between gap-2">
                    <strong>${alert.title || alert.type || 'Alert'}</strong>
                    <div class="d-flex gap-1 align-items-center">
                        <span class="badge bg-secondary">${alert.state || 'open'}</span>
                        <span class="badge ${badgeClass(alert.severity)}">${alert.severity || 'info'}</span>
                    </div>
                </div>
                <div class="ops-alert-message">${alert.message || ''}</div>
                <div class="ops-alert-meta">
                    ${alert.device_serial || alert.rover_username || alert.caster_host || ''}
                    <span>${fmtTime(alert.created_at)}</span>
                </div>
                <div class="ops-alert-actions">
                    <a class="btn btn-sm btn-outline-info" href="${targetHref(alert)}">Open</a>
                    ${alert.state !== 'acknowledged' ? `<button class="btn btn-sm btn-outline-warning" data-alert-action="ack" data-alert-id="${alert.id}">Ack</button>` : ''}
                    <button class="btn btn-sm btn-outline-success" data-alert-action="resolve" data-alert-id="${alert.id}">Resolve</button>
                </div>
            </div>
        `).join('');
    }

    function renderEvents(payload) {
        const tbody = $('ops-events-body');
        if (!tbody) return;
        const events = payload.items || [];
        if (!events.length) {
            tbody.innerHTML = '<tr><td colspan="4"><div class="ops-empty-state"><span>No events</span></div></td></tr>';
            return;
        }
        tbody.innerHTML = events.map(event => `
            <tr>
                <td class="text-muted small">${fmtTime(event.timestamp)}</td>
                <td><span class="badge ${badgeClass(event.severity)}">${event.severity || 'info'}</span></td>
                <td>${event.message || ''}</td>
                <td class="text-muted small">${event.device_serial || '--'}</td>
            </tr>
        `).join('');
    }

    function renderSummary(payload) {
        const devices = payload.devices || {};
        const rovers = payload.rovers || {};
        const alerts = payload.alerts || {};
        const casters = payload.casters || [];

        $('ops-stat-devices').textContent = devices.total ?? '--';
        $('ops-stat-devices-meta').textContent = `${devices.online || 0} online, ${devices.offline || 0} offline, ${devices.unsynced || 0} pending sync`;
        $('ops-stat-rovers').textContent = rovers.total ?? '--';
        $('ops-stat-rovers-meta').textContent = `${rovers.active || 0} active, ${rovers.expiring_14d || 0} expiring, ${rovers.blocked || 0} blocked`;
        $('ops-stat-alerts').textContent = alerts.total ?? '--';
        $('ops-stat-alerts-meta').textContent = `${alerts.critical || 0} critical, ${alerts.warning || 0} warning`;
        $('ops-stat-traffic').textContent = fmtBps(devices.total_bps || 0);
        $('ops-stat-casters-meta').textContent = `${casters.length} caster host(s)`;
    }

    async function loadSummary() {
        renderSummary(await apiJson(window.ApiRoutes.ops.summary));
    }

    async function loadAlerts() {
        const severity = $('ops-alert-severity')?.value || '';
        const url = severity ? `${window.ApiRoutes.ops.alerts}?severity=${encodeURIComponent(severity)}` : window.ApiRoutes.ops.alerts;
        renderAlerts(await apiJson(url));
    }

    async function updateAlertState(alertId, action) {
        const route = action === 'resolve' ? window.ApiRoutes.ops.resolveAlert(alertId) : window.ApiRoutes.ops.ackAlert(alertId);
        await apiJson(route, { method: 'POST', body: JSON.stringify({}) });
        window.CorsUI.showToast('success', action === 'resolve' ? 'Alert resolved' : 'Alert acknowledged');
        await Promise.all([loadSummary(), loadAlerts(), loadEvents()]);
    }

    async function loadEvents() {
        const params = new URLSearchParams({ limit: '50' });
        const severity = $('ops-event-severity')?.value || '';
        const q = $('ops-event-search')?.value || '';
        if (severity) params.set('severity', severity);
        if (q.trim()) params.set('q', q.trim());
        renderEvents(await apiJson(`${window.ApiRoutes.ops.events}?${params.toString()}`));
    }

    async function refreshAll() {
        try {
            await Promise.all([loadSummary(), loadAlerts(), loadEvents(), loadTemplates()]);
        } catch (error) {
            window.CorsUI.showToast('danger', error.message);
        }
    }

    async function bulkRenewRovers() {
        const usernames = splitTokens($('ops-rover-usernames')?.value);
        const expiryDays = Number($('ops-rover-days')?.value || 365);
        if (!usernames.length) return window.CorsUI.showToast('warning', 'Enter at least one rover username');
        const ok = await window.CorsUI.confirmAction({
            title: 'Bulk renew rovers',
            message: `Renew ${usernames.length} account(s) for ${expiryDays} day(s)?`,
            confirmText: 'Renew'
        });
        if (!ok) return;
        const result = await apiJson(window.ApiRoutes.rovers.bulkRenew, {
            method: 'POST',
            body: JSON.stringify({ usernames, expiry_days: expiryDays })
        });
        setJson($('ops-rover-result'), result);
        window.CorsUI.showToast('success', `Renewed ${result.updated?.length || 0} rover(s)`);
        await refreshAll();
    }

    async function validateRover() {
        const username = $('ops-rover-validate-username')?.value?.trim();
        if (!username) return window.CorsUI.showToast('warning', 'Enter a username');
        const params = new URLSearchParams({ username });
        const mps = $('ops-rover-validate-mps')?.value?.trim();
        if (mps) params.set('mountpoints', mps);
        setJson($('ops-rover-result'), await apiJson(`${window.ApiRoutes.rovers.validate}?${params.toString()}`));
    }

    async function exportRoversCsv() {
        const response = await window.ApiClient.apiFetch(window.ApiRoutes.rovers.exportCsv);
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Export failed: ${response.status}`);
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `rovers-export-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    async function runDeviceAction() {
        const serials = splitTokens($('ops-device-serials')?.value);
        const action = $('ops-device-action')?.value || 'lock';
        if (!serials.length) return window.CorsUI.showToast('warning', 'Enter at least one station serial');
        const ok = await window.CorsUI.confirmAction({
            title: `Bulk ${action}`,
            message: `Run ${action} on ${serials.length} station(s)?`,
            confirmText: 'Run',
            tone: action === 'reset' ? 'danger' : 'primary'
        });
        if (!ok) return;
        const result = await apiJson(window.ApiRoutes.devices.bulkAction, {
            method: 'POST',
            body: JSON.stringify({ serials, action })
        });
        setJson($('ops-device-result'), result);
        await refreshAll();
    }

    async function loadTemplates() {
        const select = $('ops-template-select');
        if (!select) return;
        const data = await apiJson(window.ApiRoutes.devices.templates);
        const templates = data.items || [];
        if (!templates.length) {
            select.innerHTML = '<option value="">No templates</option>';
            return;
        }
        select.innerHTML = templates.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
    }

    async function createTemplate() {
        const name = $('ops-template-name')?.value?.trim();
        if (!name) return window.CorsUI.showToast('warning', 'Enter a template name');
        let serviceConfig = {};
        const rawService = $('ops-template-service')?.value?.trim();
        if (rawService) {
            try {
                serviceConfig = JSON.parse(rawService);
            } catch (error) {
                return window.CorsUI.showToast('danger', 'Service config must be valid JSON');
            }
        }
        const template = await apiJson(window.ApiRoutes.devices.templates, {
            method: 'POST',
            body: JSON.stringify({ name, service_config: serviceConfig })
        });
        setJson($('ops-device-result'), template);
        $('ops-template-name').value = '';
        $('ops-template-service').value = '';
        await loadTemplates();
    }

    async function applyTemplate() {
        const templateId = $('ops-template-select')?.value;
        const serials = splitTokens($('ops-device-serials')?.value);
        if (!templateId) return window.CorsUI.showToast('warning', 'Create or select a template');
        if (!serials.length) return window.CorsUI.showToast('warning', 'Enter station serials');
        const result = await apiJson(window.ApiRoutes.devices.applyTemplate, {
            method: 'POST',
            body: JSON.stringify({ template_id: templateId, serials })
        });
        setJson($('ops-device-result'), result);
        await refreshAll();
    }

    function bindEvents() {
        $('ops-refresh-btn')?.addEventListener('click', refreshAll);
        $('ops-alert-severity')?.addEventListener('change', loadAlerts);
        $('ops-alerts-list')?.addEventListener('click', (event) => {
            const btn = event.target.closest('[data-alert-action]');
            if (!btn) return;
            updateAlertState(btn.dataset.alertId, btn.dataset.alertAction).catch(e => window.CorsUI.showToast('danger', e.message));
        });
        $('ops-event-severity')?.addEventListener('change', loadEvents);
        $('ops-event-search')?.addEventListener('input', () => {
            clearTimeout(window.__opsEventSearchTimer);
            window.__opsEventSearchTimer = setTimeout(loadEvents, 250);
        });
        $('ops-rover-renew-btn')?.addEventListener('click', () => bulkRenewRovers().catch(e => window.CorsUI.showToast('danger', e.message)));
        $('ops-rover-validate-btn')?.addEventListener('click', () => validateRover().catch(e => window.CorsUI.showToast('danger', e.message)));
        $('ops-rover-export-btn')?.addEventListener('click', () => exportRoversCsv().catch(e => window.CorsUI.showToast('danger', e.message)));
        $('ops-device-action-btn')?.addEventListener('click', () => runDeviceAction().catch(e => window.CorsUI.showToast('danger', e.message)));
        $('ops-template-create-btn')?.addEventListener('click', () => createTemplate().catch(e => window.CorsUI.showToast('danger', e.message)));
        $('ops-template-apply-btn')?.addEventListener('click', () => applyTemplate().catch(e => window.CorsUI.showToast('danger', e.message)));
    }

    document.addEventListener('DOMContentLoaded', () => {
        bindEvents();
        refreshAll();
    });
})();
