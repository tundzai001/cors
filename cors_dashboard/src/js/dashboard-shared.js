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

function createMarkerClusterGroup(options = {}) {
    const leaflet = getLeaflet();
    if (!leaflet) return null;

    try {
        if (typeof leaflet.MarkerClusterGroup === 'function') {
            return new leaflet.MarkerClusterGroup(options);
        }
        if (typeof leaflet.markerClusterGroup === 'function') {
            return leaflet.markerClusterGroup(options);
        }
    } catch (error) {
        console.warn('MarkerCluster plugin is invalid; using a standard Leaflet layer group.');
    }

    return typeof leaflet.layerGroup === 'function' ? leaflet.layerGroup() : null;
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

const getPreferredOutputServerId = (device) => {
    const cfg = device?.service_config || {};
    const enabled = [1, 2].filter((idx) => (
        parseBoolCompat(cfg[`server${idx}_enabled`], isServerConfiguredCompat(cfg, idx))
    ));
    if (!enabled.length) return null;

    const preferredServer = enabled.find((idx) => (
        String(cfg[`serverhost${idx}`] || '').trim().toLowerCase().includes('aitogy.com.vn')
    ));
    return preferredServer || enabled[0];
};

const getServerTelemetry = (device, serverId) => {
    const key = `server${serverId}`;
    const stats = device?.ntrip_stats || {};
    const statusMap = device?.ntrip_status || {};
    const rawServer = statusMap[key];
    const server = rawServer && typeof rawServer === 'object' ? rawServer : {};
    const statsHasBps = Object.prototype.hasOwnProperty.call(stats, `${key}_bps`) && stats[`${key}_bps`] != null;
    const statusHasBps = Object.prototype.hasOwnProperty.call(server, 'bps') ||
        Object.prototype.hasOwnProperty.call(statusMap, `${key}_bps`);
    const rawBps = statsHasBps ? stats[`${key}_bps`] : (server.bps ?? statusMap[`${key}_bps`] ?? 0);
    const parsedBps = Number(rawBps);
    const bps = Number.isFinite(parsedBps) ? Math.max(0, parsedBps) : 0;
    const stream = device?.ntrip_stream_state?.[key] || server;
    let reportedConnected = null;
    if (typeof rawServer === 'boolean') reportedConnected = rawServer;
    else if (typeof server.connected === 'boolean') reportedConnected = server.connected;

    return {
        bps,
        hasBps: statsHasBps || statusHasBps,
        connected: reportedConnected === true || bps > 0,
        hasConnection: reportedConnected !== null,
        sleep: typeof stream.sleep === 'boolean' ? stream.sleep : null,
        onDemand: typeof stream.on_demand === 'boolean' ? stream.on_demand : null,
        active: typeof stream.active === 'boolean' ? stream.active : null
    };
};

const getOfflineInfo = (device, nowMs = Date.now()) => {
    const status = String(device?.status || '').toLowerCase();
    const nowSeconds = Number(nowMs) / 1000;
    let sinceSeconds;

    if (status === 'offline') {
        const rawOfflineSince = Number(device?.offline_since || 0);
        if (!rawOfflineSince) return { sinceSeconds: null, sinceText: null, elapsedSeconds: null, durationText: null, unknown: true };
        sinceSeconds = rawOfflineSince > 1e12 ? rawOfflineSince / 1000 : rawOfflineSince;
    } else {
        const rawTimestamp = Number(device?.timestamp || device?.system_info?.timestamp || 0);
        const timestamp = rawTimestamp > 1e12 ? rawTimestamp / 1000 : rawTimestamp;
        if (!timestamp || nowSeconds - timestamp <= 180) return null;
        sinceSeconds = timestamp + 180;
    }

    const elapsedSeconds = Math.max(0, Math.floor(nowSeconds - sinceSeconds));
    const date = new Date(sinceSeconds * 1000);
    const pad = value => String(value).padStart(2, '0');
    const sinceText = `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;

    let remaining = elapsedSeconds;
    const days = Math.floor(remaining / 86400);
    remaining %= 86400;
    const hours = Math.floor(remaining / 3600);
    remaining %= 3600;
    const minutes = Math.floor(remaining / 60);
    const durationText = days
        ? `${days} ${days === 1 ? 'day' : 'days'}${hours ? ` ${hours} ${hours === 1 ? 'hour' : 'hours'}` : ''}`
        : hours
            ? `${hours} ${hours === 1 ? 'hour' : 'hours'}${minutes ? ` ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}` : ''}`
            : minutes
                ? `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`
                : 'Less than a minute';

    return { sinceSeconds, sinceText, elapsedSeconds, durationText, unknown: false };
};

const isAccessTokenSynced = (device) => {
    const auth = device?.system_info?.auth_status || {};
    return auth.token_synced === true && auth.token_deployment_pending !== true &&
        auth.needs_token_deploy !== true && auth.server_token_present !== false &&
        auth.agent_token_present !== false;
};

const getMapEffectiveStatus = (device) => {
    const authStatus = device?.system_info?.auth_status || {};
    const agentReportedStatus = String(authStatus.agent_reported_status || '').toLowerCase();
    const rawStatus = String(device?.status || '').toLowerCase();
    if (rawStatus === 'awaiting_license' && isAccessTokenSynced(device) &&
        !['online', 'streaming', 'sleep', 'starting'].includes(agentReportedStatus)) {
        return 'awaiting_agent';
    }
    const statusForOps = rawStatus === 'awaiting_license' && ['online', 'streaming', 'sleep', 'starting'].includes(agentReportedStatus)
        ? agentReportedStatus
        : rawStatus;
    const stats = device?.ntrip_stats || {};
    const rtcmIn = Number(stats.rtcm_input_bps || stats.rtcm_in_bps || 0);
    const streamState = device?.ntrip_stream_state || {};
    const isVirtual = String(device?.detected_chip_type || '').toUpperCase() === 'VIRTUAL';

    if (statusForOps === 'starting') return 'starting';

    if (!['online', 'streaming', 'sleep', 'starting'].includes(statusForOps)) {
        const anyServerSleeping = Boolean(streamState.server1?.sleep || streamState.server2?.sleep);
        if (rawStatus === 'awaiting_license' && anyServerSleeping && rtcmIn > 0) return 'sleep';
        return (isVirtual && rtcmIn > 0) ? 'online' : 'offline';
    }

    const primarySid = getPreferredOutputServerId(device);
    if (!primarySid) {
        if (statusForOps === 'streaming') return 'streaming';
        return (isVirtual && rtcmIn > 0) ? 'online' : 'offline';
    }

    const primary = getServerTelemetry(device, primarySid);
    if (primary.bps > 0) return 'online';
    if (primary.sleep === true || (primary.onDemand === true && primary.active === false)) return 'sleep';
    if (primary.connected) return 'online';
    if (primary.hasBps || primary.hasConnection || statusForOps === 'online' || statusForOps === 'streaming' || statusForOps === 'sleep') {
        return 'sleep';
    }
    return statusForOps;
};
window.CorsDashboardShared = { getLeaflet, createMarkerClusterGroup, STATUS_TEXT_MAP, STATUS_CLASS_MAP, getStatusColor, parseBoolCompat, isServerConfiguredCompat, PREFERRED_DOMAINS, isPreferredHost, getPreferredOutputServerId, getServerTelemetry, getOfflineInfo, getMapEffectiveStatus, isAccessTokenSynced };
})();
