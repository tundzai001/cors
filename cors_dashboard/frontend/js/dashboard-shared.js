// ======================
// dashboard-shared.js
// Shared map/status helpers for dashboard modules.
// ======================
(() => {
'use strict';
function getLeaflet() {
    const leaflet = window.Leaflet || window.L;
    return leaflet && typeof leaflet.map === 'function' ? leaflet : null;
}

const STATUS_TEXT_MAP = {
    'online': 'ONLINE',
    'offline': 'OFFLINE',
    'unprovisioned': 'CHƯA KÍCH HOẠT',
    'awaiting_license': 'CHỜ LICENSE',
    'configuring': 'ĐANG CẤU HÌNH',
    'rebooting': 'KHỞI ĐỘNG LẠI',
    'rebooting_for_reset': 'ĐANG KHÔI PHỤC',
    'locked': 'BỊ KHÓA',
    'sleep': 'CHẾ ĐỘ NGỦ',
    'streaming': 'ĐANG PHÁT',
    'starting': 'ĐANG KHỞI CHẠY',
    'auto_setup_rover': 'AUTO: CHẾ ĐỘ ROVER',
    'auto_setup_wait_fix': 'AUTO: CHỜ RTK FIX',
    'auto_setup_averaging': 'AUTO: LẤY MẪU TỌA ĐỘ',
    'auto_setup_base': 'AUTO: CÀI ĐẶT BASE'
};

const STATUS_CLASS_MAP = {
    'online': 'bg-success',
    'offline': 'bg-danger',
    'unprovisioned': 'bg-warning text-dark',
    'awaiting_license': 'bg-info text-dark',
    'configuring': 'bg-primary',
    'rebooting': 'bg-warning text-dark',
    'rebooting_for_reset': 'bg-danger',
    'locked': 'bg-danger',
    'sleep': 'bg-info',
    'streaming': 'bg-success',
    'starting': 'bg-warning text-dark',
    'auto_setup_rover': 'bg-info',
    'auto_setup_wait_fix': 'bg-info',
    'auto_setup_averaging': 'bg-info',
    'auto_setup_base': 'bg-info'
};

const getStatusColor = (status) => {
    switch (status) {
        case 'online': return '#198754'; // success
        case 'offline': return '#dc3545'; // danger
        case 'sleep': return '#0dcaf0';   // info
        case 'streaming': return '#198754'; // success
        case 'starting': return '#ffc107';  // warning
        case 'unprovisioned': return '#ffc107';
        case 'configuring': return '#0d6efd';
        case 'locked': return '#dc3545';
        default:
            if (status && status.startsWith('auto_setup')) return '#0dcaf0';
            return '#6c757d'; // secondary
    }
};

const parseBoolCompat = (value, defaultValue = false) => {
    if (value === null || value === undefined) return defaultValue;
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const v = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(v)) return true;
        if (['false', '0', 'no', 'off'].includes(v)) return false;
    }
    return Boolean(value);
};

const isServerConfiguredCompat = (cfg, idx) => {
    const host = String(cfg[`serverhost${idx}`] || '').trim();
    const port = String(cfg[`port${idx}`] || '').trim();
    const mountpoint = String(cfg[`mountpoint${idx}`] || '').trim();
    return Boolean(host && port && mountpoint);
};

const PREFERRED_DOMAINS = ['aitogy.com.vn', 'aitogy.click'];
const isPreferredHost = (hostValue) => {
    const host = String(hostValue || '').trim().toLowerCase();
    if (!host) return false;
    return PREFERRED_DOMAINS.some((d) => host.includes(d));
};

const getMapEffectiveStatus = (device) => {
    const authStatus = device?.system_info?.auth_status || {};
    const agentReportedStatus = String(authStatus.agent_reported_status || '').toLowerCase();
    const rawStatus = String(device?.status || '').toLowerCase();
    const statusForOps = rawStatus === 'awaiting_license' && ['online', 'streaming', 'sleep', 'starting'].includes(agentReportedStatus)
        ? agentReportedStatus
        : rawStatus;
    const stats = device?.ntrip_stats || {};
    const rtcmIn = Number(stats.rtcm_input_bps || stats.rtcm_in_bps || 0);
    const streamState = device?.ntrip_stream_state || {};
    const isVirtual = String(device?.detected_chip_type || '').toUpperCase() === 'VIRTUAL';

    if (statusForOps === 'streaming') return 'streaming';
    if (statusForOps === 'starting') return 'starting';

    if (!['online', 'streaming', 'sleep', 'starting'].includes(statusForOps)) {
        const anyServerSleeping = Boolean(streamState.server1?.sleep || streamState.server2?.sleep);
        if (rawStatus === 'awaiting_license' && anyServerSleeping && rtcmIn > 0) return 'sleep';
        return (isVirtual && rtcmIn > 0) ? 'online' : 'offline';
    }

    const cfg = device?.service_config || {};
    const s1Enabled = parseBoolCompat(cfg.server1_enabled, isServerConfiguredCompat(cfg, 1));
    const s2Enabled = parseBoolCompat(cfg.server2_enabled, isServerConfiguredCompat(cfg, 2));

    if (!s1Enabled && !s2Enabled) {
        return (isVirtual && rtcmIn > 0) ? 'online' : 'offline';
    }

    const s1Host = String(cfg.serverhost1 || '').trim().toLowerCase();
    const s2Host = String(cfg.serverhost2 || '').trim().toLowerCase();
    const preferred = 'aitogy.com.vn';

    let primarySid = 1;
    if (s1Host.includes(preferred)) primarySid = 1;
    else if (s2Host.includes(preferred)) primarySid = 2;
    else primarySid = 1;

    const ntripStatusMap = device?.ntrip_status || {};
    const primaryBps = Number(ntripStatusMap[`server${primarySid}`]?.bps || 0);
    if (primaryBps > 0) {
        return 'online';
    }

    const onDemand = parseBoolCompat(cfg[`server${primarySid}_stream_on_demand`], parseBoolCompat(cfg.stream_on_demand, false));
    const active = onDemand ? parseBoolCompat(cfg[`server${primarySid}_stream_active`], parseBoolCompat(cfg.stream_active, false)) : true;
    if (statusForOps === 'sleep' && (!onDemand || active)) {
        return 'online';
    }
    return (!onDemand || active) ? 'online' : 'sleep';
};
window.CorsDashboardShared = { getLeaflet, STATUS_TEXT_MAP, STATUS_CLASS_MAP, getStatusColor, parseBoolCompat, isServerConfiguredCompat, PREFERRED_DOMAINS, isPreferredHost, getMapEffectiveStatus };
})();
