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
        return new Date(value * 1000).toLocaleString('vi-VN');
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
        el.hidden = false;
        el.textContent = JSON.stringify(data, null, 2);
    }

    function html(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
    }

    function severityLabel(value) {
        return ({ critical: 'Nghiêm trọng', error: 'Lỗi', warning: 'Cảnh báo', success: 'Thành công', info: 'Thông tin' })[value] || 'Thông tin';
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
        $('ops-alert-count').textContent = alerts.length;
        if (!alerts.length) {
            container.innerHTML = '<div class="ops-empty-state"><i class="bi bi-shield-check"></i><span>Không có cảnh báo cần xử lý.</span></div>';
            return;
        }
        container.innerHTML = alerts.map(alert => {
            const severity = ['critical', 'warning', 'info'].includes(alert.severity) ? alert.severity : 'info';
            const target = targetHref(alert);
            return `<article class="ops-alert-item ${severity}">
                <div class="ops-alert-top"><strong>${html(alert.title || alert.type || 'Cảnh báo')}</strong><span class="badge ${badgeClass(severity)}">${severityLabel(severity)}</span></div>
                <p class="ops-alert-message">${html(alert.message)}</p>
                <div class="ops-alert-meta"><span>${html(alert.device_serial || alert.rover_username || alert.caster_host || 'Hệ thống')}</span><time>${fmtTime(alert.created_at)}</time></div>
                <div class="ops-alert-actions">
                    ${target !== '#' ? `<a class="btn btn-sm btn-outline-info" href="${html(target)}">Xem chi tiết</a>` : ''}
                    ${alert.state !== 'acknowledged' ? `<button class="btn btn-sm btn-outline-warning" type="button" data-alert-action="ack" data-alert-id="${html(alert.id)}">Ghi nhận</button>` : ''}
                    <button class="btn btn-sm btn-outline-success" type="button" data-alert-action="resolve" data-alert-id="${html(alert.id)}">Đã xử lý</button>
                </div>
            </article>`;
        }).join('');
    }

    function renderEvents(payload) {
        const list = $('ops-events-list');
        if (!list) return;
        const events = payload.items || [];
        $('ops-event-count').textContent = events.length;
        if (!events.length) {
            list.innerHTML = '<div class="ops-empty-state"><i class="bi bi-clock-history"></i><span>Chưa có sự kiện phù hợp.</span></div>';
            return;
        }
        list.innerHTML = events.map(event => `<article class="ops-event-item">
            <div class="ops-event-marker ${html(['success', 'warning', 'error', 'info'].includes(event.severity) ? event.severity : 'info')}"></div>
            <div class="ops-event-content"><div class="ops-event-top"><span class="badge ${badgeClass(event.severity)}">${severityLabel(event.severity)}</span><time>${fmtTime(event.timestamp)}</time></div>
            <p>${html(event.message)}</p>${event.device_serial ? `<span class="ops-event-device"><i class="bi bi-broadcast-pin" aria-hidden="true"></i>${html(event.device_serial)}</span>` : ''}</div>
        </article>`).join('');
    }

    function renderSummary(payload) {
        const devices = payload.devices || {};
        const rovers = payload.rovers || {};
        const alerts = payload.alerts || {};
        const casters = payload.casters || [];

        $('ops-stat-devices').textContent = devices.total ?? '--';
        $('ops-stat-devices-meta').textContent = `${devices.online || 0} trực tuyến · ${devices.offline || 0} ngoại tuyến · ${devices.unsynced || 0} chưa đồng bộ`;
        $('ops-stat-rovers').textContent = rovers.total ?? '--';
        $('ops-stat-rovers-meta').textContent = `${rovers.active || 0} hoạt động · ${rovers.expiring_14d || 0} sắp hết hạn · ${rovers.blocked || 0} bị khóa`;
        $('ops-stat-alerts').textContent = alerts.total ?? '--';
        $('ops-stat-alerts-meta').textContent = `${alerts.critical || 0} nghiêm trọng · ${alerts.warning || 0} cảnh báo`;
        $('ops-stat-traffic').textContent = fmtBps(devices.total_bps || 0);
        $('ops-stat-casters-meta').textContent = `${casters.length} máy chủ caster`;
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
        window.CorsUI.showToast('success', action === 'resolve' ? 'Đã xử lý cảnh báo' : 'Đã ghi nhận cảnh báo');
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
        const button = $('ops-refresh-btn');
        if (button) button.disabled = true;
        try {
            await Promise.all([loadSummary(), loadAlerts(), loadEvents(), loadTemplates()]);
            $('ops-last-updated').textContent = `Cập nhật ${new Date().toLocaleTimeString('vi-VN')}`;
        } catch (error) {
            $('ops-last-updated').textContent = 'Không thể cập nhật toàn bộ dữ liệu';
            window.CorsUI.showToast('danger', error.message);
        } finally {
            if (button) button.disabled = false;
        }
    }

    async function bulkRenewRovers() {
        const usernames = splitTokens($('ops-rover-usernames')?.value);
        const expiryDays = Number($('ops-rover-days')?.value || 365);
        if (!usernames.length) return window.CorsUI.showToast('warning', 'Nhập ít nhất một tài khoản Rover');
        if (!Number.isInteger(expiryDays) || expiryDays < 1 || expiryDays > 3650) return window.CorsUI.showToast('warning', 'Thời hạn phải từ 1 đến 3650 ngày');
        const ok = await window.CorsUI.confirmAction({
            title: 'Gia hạn Rover',
            message: `Gia hạn ${usernames.length} tài khoản thêm ${expiryDays} ngày?`,
            confirmText: 'Gia hạn'
        });
        if (!ok) return;
        const result = await apiJson(window.ApiRoutes.rovers.bulkRenew, {
            method: 'POST',
            body: JSON.stringify({ usernames, expiry_days: expiryDays })
        });
        setJson($('ops-rover-result'), result);
        window.CorsUI.showToast('success', `Đã gia hạn ${result.updated?.length || 0} Rover`);
        await refreshAll();
    }

    async function validateRover() {
        const username = $('ops-rover-validate-username')?.value?.trim();
        if (!username) return window.CorsUI.showToast('warning', 'Nhập tên tài khoản Rover');
        const params = new URLSearchParams({ username });
        const mps = $('ops-rover-validate-mps')?.value?.trim();
        if (mps) params.set('mountpoints', mps);
        setJson($('ops-rover-result'), await apiJson(`${window.ApiRoutes.rovers.validate}?${params.toString()}`));
    }

    function csvCell(value) {
        const raw = typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '');
        const safe = /^\s*[=+\-@]/.test(raw) ? `'${raw}` : raw;
        return `"${safe.replace(/"/g, '""')}"`;
    }

    function csvRows(items, fields) {
        return [fields.map(([label]) => csvCell(label)).join(','), ...items.map(item =>
            fields.map(([, key]) => csvCell(item[key])).join(',')
        )].join('\r\n');
    }

    async function exportData() {
        const kind = $('ops-export-kind').value;
        const button = $('ops-export-btn');
        button.disabled = true;
        try {
            let csv;
            const serverExports = {
                devices: window.ApiRoutes.devices.exportCsv,
                rovers: window.ApiRoutes.rovers.exportCsv,
                'rover-history': window.ApiRoutes.rovers.historyExportCsv
            };
            if (serverExports[kind]) {
                const route = serverExports[kind];
                const response = await window.ApiClient.apiFetch(route);
                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(error.detail || `Không thể xuất dữ liệu (${response.status})`);
                }
                csv = await response.text();
            } else {
                const alertFields = [
                    ['Mức độ', 'severity'], ['Loại', 'type'], ['Tiêu đề', 'title'],
                    ['Nội dung', 'message'], ['Trạm', 'device_serial'],
                    ['Rover', 'rover_username'], ['Caster', 'caster_host'],
                    ['Trạng thái', 'state'], ['Thời gian Unix', 'created_at']
                ];
                const exports = {
                    'caster-users': [window.ApiRoutes.caster.users, data => data.items, [
                        ['Tài khoản', 'username'], ['Caster', 'server'], ['Mountpoint', 'mountpoints'],
                        ['Kết nối', 'nclient'], ['Giới hạn kết nối', 'maxclient'],
                        ['Trong DB', 'in_db'], ['Bị chặn', 'is_blocked'], ['Lần cập nhật Unix', 'last_update']
                    ]],
                    blocklist: [window.ApiRoutes.caster.blocklist, data => data, [
                        ['Tài khoản', 'username'], ['Mountpoint', 'mountpoint'],
                        ['Thời gian chặn Unix', 'blocked_at'], ['Người chặn', 'blocked_by'], ['Lý do', 'reason']
                    ]],
                    alerts: [window.ApiRoutes.ops.alerts, data => data.items, alertFields],
                    'resolved-alerts': [`${window.ApiRoutes.ops.alerts}?state=resolved`, data => data.items, alertFields],
                    events: [`${window.ApiRoutes.ops.events}?limit=200`, data => data.items, [
                        ['Thời gian Unix', 'timestamp'], ['Mức độ', 'severity'],
                        ['Sự kiện', 'message'], ['Trạm', 'device_serial'],
                        ['Người dùng ID', 'actor_user_id'], ['IP', 'ip_address']
                    ]],
                    casters: [window.ApiRoutes.ops.summary, data => data.casters, [
                        ['Máy chủ', 'host'], ['Cổng', 'port'], ['Tài khoản', 'users'],
                        ['Kết nối', 'clients'], ['Lỗi', 'error'],
                        ['Dữ liệu cũ', 'stale'], ['Lần cập nhật Unix', 'last_update']
                    ]],
                    users: [window.ApiRoutes.users.list, data => data, [
                        ['ID', 'id'], ['Tài khoản', 'username'], ['Họ tên', 'full_name'],
                        ['Vai trò', 'role'], ['Đang hoạt động', 'is_active'],
                        ['Trạm được sửa', 'allowed_bases'], ['Trạm chỉ xem', 'view_only_bases'],
                        ['Được xem Rover', 'can_view_rovers'], ['Được tạo Rover', 'can_create_rovers'],
                        ['Được tạo tài khoản', 'can_create_users'], ['Ngày tạo Unix', 'created_at']
                    ]],
                    templates: [window.ApiRoutes.devices.templates, data => data.items, [
                        ['Tên mẫu', 'name'], ['Mô tả', 'description'], ['Cấu hình Base', 'base_config'],
                        ['Cấu hình dịch vụ', 'service_config'], ['Người tạo', 'created_by'],
                        ['Ngày tạo Unix', 'created_at']
                    ]]
                };
                const selected = exports[kind];
                if (!selected) throw new Error('Loại dữ liệu không hợp lệ');
                const data = await apiJson(selected[0]);
                csv = csvRows(selected[1](data) || [], selected[2]);
            }
            const url = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }));
            const link = document.createElement('a');
            link.href = url;
            link.download = `${kind}-${new Date().toISOString().slice(0, 10)}.csv`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            window.CorsUI.showToast('success', 'Đã tải file CSV');
        } finally {
            button.disabled = false;
        }
    }

    async function runDeviceAction() {
        const serials = splitTokens($('ops-device-serials')?.value);
        const action = $('ops-device-action')?.value || 'lock';
        if (!serials.length) return window.CorsUI.showToast('warning', 'Nhập ít nhất một số sê-ri trạm');
        const ok = await window.CorsUI.confirmAction({
            title: 'Thao tác hàng loạt',
            message: `${({ lock: 'Khóa', unlock: 'Mở khóa', reset: 'Đặt lại' })[action] || action} ${serials.length} trạm?`,
            confirmText: 'Thực hiện',
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
            select.innerHTML = '<option value="">Chưa có mẫu cấu hình</option>';
            return;
        }
        select.innerHTML = templates.map(t => `<option value="${html(t.id)}">${html(t.name)}</option>`).join('');
    }

    async function createTemplate() {
        const name = $('ops-template-name')?.value?.trim();
        if (!name) return window.CorsUI.showToast('warning', 'Nhập tên mẫu cấu hình');
        let serviceConfig = {};
        const rawService = $('ops-template-service')?.value?.trim();
        if (rawService) {
            try {
                serviceConfig = JSON.parse(rawService);
            } catch (error) {
                return window.CorsUI.showToast('danger', 'Cấu hình dịch vụ phải là JSON hợp lệ');
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
        if (!templateId) return window.CorsUI.showToast('warning', 'Chọn hoặc tạo mẫu cấu hình');
        if (!serials.length) return window.CorsUI.showToast('warning', 'Nhập số sê-ri trạm');
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
        $('ops-export-btn')?.addEventListener('click', () => exportData().catch(e => window.CorsUI.showToast('danger', e.message)));
        $('ops-device-action-btn')?.addEventListener('click', () => runDeviceAction().catch(e => window.CorsUI.showToast('danger', e.message)));
        $('ops-template-create-btn')?.addEventListener('click', () => createTemplate().catch(e => window.CorsUI.showToast('danger', e.message)));
        $('ops-template-apply-btn')?.addEventListener('click', () => applyTemplate().catch(e => window.CorsUI.showToast('danger', e.message)));
    }

    const mount = $('operations-root');
    if (mount && window.ReactDOM && window.OperationsView) {
        const root = window.ReactDOM.createRoot(mount);
        window.ReactDOM.flushSync(() => root.render(window.React.createElement(window.OperationsView)));
    }

    document.addEventListener('DOMContentLoaded', () => {
        bindEvents();
        refreshAll();
    });
})();
