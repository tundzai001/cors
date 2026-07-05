'use strict';

(function () {
    const ApiRoutes = {
        docs: '/api/docs',
        auth: {
            me: '/api/auth/me',
            login: '/api/auth/login',
            zaloLinkCode: '/api/auth/zalo-link-code',
            zaloMyLinkCode: '/api/auth/zalo-my-link-code',
            zaloGroupLinkCode: '/api/admin/zalo-group-link-code',
            zaloBindings: '/api/auth/zalo-bindings',
            zaloBinding: bindingId => `/api/auth/zalo-bindings/${encodeURIComponent(bindingId)}`,
            revokeZaloBinding: bindingId => `/api/auth/zalo-bindings/${encodeURIComponent(bindingId)}`
        },
        activity: {
            feed: (limit = 50) => `/api/activity/feed?limit=${encodeURIComponent(limit)}`,
            heatmap: '/api/activity/heatmap'
        },
        ops: {
            summary: '/api/ops/summary',
            alerts: '/api/ops/alerts',
            events: '/api/ops/events',
            ackAlert: alertId => `/api/ops/alerts/${encodeURIComponent(alertId)}/ack`,
            resolveAlert: alertId => `/api/ops/alerts/${encodeURIComponent(alertId)}/resolve`
        },
        public: {
            mapStations: '/api/public/map-stations',
            networkSummary: '/api/public/network-summary'
        },
        caster: {
            config: '/api/caster/config',
            users: '/api/caster/users',
            user: username => `/api/caster/users/${encodeURIComponent(username)}`,
            blockUser: '/api/caster/block-user',
            unblockUser: '/api/caster/unblock-user',
            blocklist: '/api/caster/blocklist',
            clientTable: '/api/caster/clienttable'
        },
        bases: {
            rovers: serial => `/api/bases/${encodeURIComponent(serial)}/rovers`
        },
        rovers: {
            all: '/api/rovers/all',
            createMulti: '/api/rovers/create-multi',
            validate: '/api/rovers/validate',
            bulkRenew: '/api/rovers/bulk-renew',
            exportCsv: '/api/rovers/export/csv',
            history: username => `/api/rovers/${encodeURIComponent(username)}/history`,
            syncStatus: '/api/rovers/sync-status',
            detail: roverId => `/api/rovers/${encodeURIComponent(roverId)}`,
            syncByUsername: username => `/api/rovers/sync/by-username/${encodeURIComponent(username)}`
        },
        users: {
            list: '/api/users',
            detail: userId => `/api/users/${encodeURIComponent(userId)}`,
            devices: userId => `/api/devices?user_id=${encodeURIComponent(userId)}`
        },
        devices: {
            list: '/api/devices',
            exportCsv: '/api/devices/export/csv',
            detail: serial => `/api/devices/${encodeURIComponent(serial)}`,
            name: serial => `/api/devices/${encodeURIComponent(serial)}/name`,
            serial: serial => `/api/devices/${encodeURIComponent(serial)}/serial`,
            reset: serial => `/api/devices/${encodeURIComponent(serial)}/reset`,
            command: serial => `/api/devices/${encodeURIComponent(serial)}/command`,
            autoDeployToken: serial => `/api/devices/${encodeURIComponent(serial)}/auto-deploy-token`,
            licenseBypass: serial => `/api/devices/${encodeURIComponent(serial)}/license-bypass`,
            configureChip: serial => `/api/devices/${encodeURIComponent(serial)}/configure-chip`,
            configureService: serial => `/api/devices/${encodeURIComponent(serial)}/configure-service`,
            lock: serial => `/api/devices/${encodeURIComponent(serial)}/lock`,
            unlock: serial => `/api/devices/${encodeURIComponent(serial)}/unlock`,
            alerts: '/api/devices/alerts',
            history: serial => `/api/devices/${encodeURIComponent(serial)}/history`,
            bulkAction: '/api/devices/bulk-action',
            templates: '/api/device-config-templates',
            applyTemplate: '/api/devices/apply-template'
        },
        admin: {
            settings: '/api/admin/settings',
            securityStats: '/api/admin/security/stats',
            blockIp: '/api/admin/security/block-ip',
            unblockIp: '/api/admin/security/unblock-ip',
            casterConfig: '/api/admin/caster-config',
            casterSources: '/api/admin/caster-sources'
        },
        license: {
            pi: '/api/license/pi'
        },
        health: {
            detailed: '/api/health/detailed'
        }
    };

    window.ApiRoutes = ApiRoutes;
})();
