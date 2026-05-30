// ====================== Core dashboard list components ======================
(() => {
const { useState, useEffect, memo, useRef } = React;

const STATUS_TEXT_MAP = {
    'online': 'ONLINE',
    'offline': 'OFFLINE',
    'unprovisioned': 'CHƯA KÍCH HOẠT',
    'awaiting_license': 'CHỜ LICENSE',
    'configuring': 'ĐANG CẤU HÌNH',
    'rebooting': 'KHỞI ĐỘNG LẠI',
    'rebooting_for_reset': 'ĐANG RESET',
    'locked': 'BỊ KHÓA',
    'sleep': 'CHẾ ĐỘ NGỦ',
    'streaming': 'ĐANG PHÁT',
    'starting': 'ĐANG KHỞI CHẠY',
    'auto_setup_rover': 'AUTO: CHẾ ĐỘ ROVER',
    'auto_setup_wait_fix': 'AUTO: CHỜ RTK FIX',
    'auto_setup_averaging': 'AUTO: LẤY MẪU TỌA ĐỘ',
    'auto_setup_base': 'AUTO: CÀI ĐẶT BASE'
};

function getDeviceOpsFlags(device) {
    const config = device?.service_config || {};
    const ntripStatus = device?.ntrip_status || {};
    const status = String(device?.status || 'offline').toLowerCase();
    const enabled1 = Boolean(config.server1_enabled || (config.serverhost1 && config.port1 && config.mountpoint1));
    const enabled2 = Boolean(config.server2_enabled || (config.serverhost2 && config.port2 && config.mountpoint2));
    const bps1 = Number(ntripStatus.server1?.bps || 0);
    const bps2 = Number(ntripStatus.server2?.bps || 0);
    const activeStatus = ['online', 'streaming', 'starting'].includes(status);
    const noStream = activeStatus && ((enabled1 && bps1 <= 0) || (enabled2 && bps2 <= 0));
    const unsynced = device?.is_synced === false;
    const alert = status === 'offline' || status === 'locked' || unsynced || noStream;
    return { alert, unsynced, noStream };
}
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
// ===============================================================================
// === DEVICE LIST COMPONENTS
// ===============================================================================

const DeviceListItem = memo(function DeviceListItem({ device, index = 0, isSelected, onSelect, onDelete, currentUser }) {
    const isAdmin = currentUser?.role === 'admin';
    const canDelete = currentUser?.role === 'admin' ||
        (currentUser?.role === 'coordinator' && device.user_id === currentUser?.id);

    // ============================================================
    // STATE MANAGEMENT
    // ============================================================
    const [displayStatus, setDisplayStatus] = React.useState({
        s1Connected: false,
        s2Connected: false
    });

    const lastUpdateTimeRef = React.useRef({
        s1: 0,
        s2: 0
    });

    // Reset refs on serial change
    React.useEffect(() => {
        lastUpdateTimeRef.current = { s1: 0, s2: 0 };
    }, [device.serial]);

    // Update connection status based on bps data
    React.useEffect(() => {
        const ntripStatus = device.ntrip_status || {};
        const s1bps = ntripStatus.server1?.bps || 0;
        const s2bps = ntripStatus.server2?.bps || 0;

        const now = Date.now();
        const GRACE_PERIOD = 5000;

        if (s1bps > 0) lastUpdateTimeRef.current.s1 = now;
        if (s2bps > 0) lastUpdateTimeRef.current.s2 = now;

        const s1Connected = lastUpdateTimeRef.current.s1 !== 0 && (s1bps > 0 || (now - lastUpdateTimeRef.current.s1 < GRACE_PERIOD));
        const s2Connected = lastUpdateTimeRef.current.s2 !== 0 && (s2bps > 0 || (now - lastUpdateTimeRef.current.s2 < GRACE_PERIOD));

        setDisplayStatus(prev => {
            if (prev.s1Connected === s1Connected && prev.s2Connected === s2Connected) return prev;
            return { s1Connected, s2Connected };
        });
    }, [device.ntrip_status]);

    // ============================================================
    // DERIVED STATE (MEMOIZED)
    // ============================================================
    const metrics = React.useMemo(() => {
        const config = device.service_config || {};
        const ntripStatus = device.ntrip_status || {};
        const ntripStats = device.ntrip_stats || {};
        const mqttStats = device.mqtt_stats || {};
        const streamState = device.ntrip_stream_state || {};
        const rawStatus = String(device?.status || 'offline').toLowerCase();

        const toBool = (value, defaultValue = false) => {
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

        const isServerConfigured = (idx) => {
            const host = String(config[`serverhost${idx}`] || '').trim();
            const port = String(config[`port${idx}`] || '').trim();
            const mountpoint = String(config[`mountpoint${idx}`] || '').trim();
            return Boolean(host && port && mountpoint);
        };

        const formatSpeed = (bytes) => {
            if (!bytes || bytes === 0) return { value: '0', unit: 'B/s' };
            if (bytes < 1024) return { value: Math.round(bytes).toString(), unit: 'B/s' };
            const kbVal = (bytes / 1024);
            return { value: kbVal >= 10 ? Math.round(kbVal).toString() : kbVal.toFixed(1), unit: 'KB/s' };
        };

        const s1 = ntripStatus.server1 || { connected: false, bps: 0 };
        const s2 = ntripStatus.server2 || { connected: false, bps: 0 };

        const s1Enabled = toBool(config.server1_enabled, isServerConfigured(1));
        const s2Enabled = toBool(config.server2_enabled, isServerConfigured(2));

        const rtcmInputBps = Number(ntripStats.rtcm_input_bps || ntripStats.rtcm_in_bps || mqttStats.source_bps || 0);
        const hasRtcmInputMetric = Object.prototype.hasOwnProperty.call(ntripStats, 'rtcm_input_bps')
            || Object.prototype.hasOwnProperty.call(ntripStats, 'rtcm_in_bps')
            || Object.prototype.hasOwnProperty.call(mqttStats, 'source_bps');

        const s1State = streamState.server1 || {};
        const s1OnDemand = toBool(s1State.on_demand, toBool(config.server1_stream_on_demand, toBool(config.stream_on_demand, false)));
        const s1Active = toBool(s1State.active, toBool(config.server1_stream_active, !s1OnDemand));
        const s1SleepMode = s1Enabled && s1OnDemand && !s1Active;
        const s1UseRtcmSleepUi = s1SleepMode && hasRtcmInputMetric;

        const s2State = streamState.server2 || {};
        const s2OnDemand = toBool(s2State.on_demand, toBool(config.server2_stream_on_demand, toBool(config.stream_on_demand, false)));
        const s2Active = toBool(s2State.active, toBool(config.server2_stream_active, !s2OnDemand));
        const s2SleepMode = s2Enabled && s2OnDemand && !s2Active;
        const s2UseRtcmSleepUi = s2SleepMode && hasRtcmInputMetric;

        const hasInputTraffic = hasRtcmInputMetric && rtcmInputBps > 0;
        const hasAnyOutputTraffic = (s1Enabled && s1.bps > 0) || (s2Enabled && s2.bps > 0);
        const hasAnySleepOnly = !hasAnyOutputTraffic && ((s1Enabled && s1SleepMode) || (s2Enabled && s2SleepMode));

        const serialUpper = String(device?.serial || '').toUpperCase();
        const chipUpper = String(device?.detected_chip_type || '').toUpperCase();

        // --- CALCULATE STATUS COLOR WITH STRICT PRIMARY SERVER PRIORITY ---
        let effectiveStatus = 'offline';

        const okStatuses = ['online', 'streaming', 'sleep', 'starting'];
        if (okStatuses.includes(rawStatus)) {
            // 1. TRUST AGENT STATUS FIRST (Especially for 'sleep')
            if (rawStatus === 'sleep') {
                effectiveStatus = 'sleep';
            } else if (rawStatus === 'starting') {
                effectiveStatus = 'starting';
            } else {
                // 2. PRIMARY SERVER LOGIC: aitogy.com.vn (partial match for ports) > S1
                const s1Host = String(config.serverhost1 || '').trim().toLowerCase();
                const s2Host = String(config.serverhost2 || '').trim().toLowerCase();
                const preferred = 'aitogy.com.vn';

                let primarySid = 1;
                if (s1Host.includes(preferred)) primarySid = 1;
                else if (s2Host.includes(preferred)) primarySid = 2;
                else primarySid = 1; // Default to S1

                const pBps = primarySid === 1 ? s1.bps : s2.bps;
                const pSleep = primarySid === 1 ? s1SleepMode : s2SleepMode;
                const pEnabled = primarySid === 1 ? s1Enabled : s2Enabled;

                if (pEnabled) {
                    if (pBps > 0) {
                        effectiveStatus = 'online';
                    } else if (pSleep) {
                        effectiveStatus = 'sleep';
                    } else {
                        effectiveStatus = 'online';
                    }
                } else {
                    // Fallback to ANY enabled server if primary is disabled
                    if (hasAnyOutputTraffic) {
                        effectiveStatus = 'online';
                    } else if (hasAnySleepOnly) {
                        effectiveStatus = 'sleep';
                    } else {
                        effectiveStatus = 'offline';
                    }
                }
            }
        }

        // SAFETY GUARD: If agent reports as Online/Sleep/Starting, use that as fallback if logic fails
        if (effectiveStatus === 'offline' && okStatuses.includes(rawStatus)) {
            effectiveStatus = rawStatus === 'offline' ? 'online' : rawStatus;
        }

        const statusColorClass = effectiveStatus;

        // Use the rawStatus declared above at line 265
        const statusText = STATUS_TEXT_MAP[rawStatus] || String(device?.status || 'UNKNOWN').toUpperCase();
        const statusBadgeClass = STATUS_CLASS_MAP[rawStatus] || 'bg-secondary';
        const hideStatusBadge = ['online', 'sleep', 'offline', 'streaming', 'starting'].includes(rawStatus);

        // Per-server individual color: pushing=green, sleep/input-only=blue, idle=disconnected
        const s1StatusClass = s1.bps > 0 ? 'online' : (s1SleepMode ? 'sleep' : 'disconnected');
        const s2StatusClass = s2.bps > 0 ? 'online' : (s2SleepMode ? 'sleep' : 'disconnected');

        return {
            s1Enabled, s1SleepMode, s1UseRtcmSleepUi, s1Speed: formatSpeed(s1UseRtcmSleepUi ? rtcmInputBps : s1.bps),
            s2Enabled, s2SleepMode, s2UseRtcmSleepUi, s2Speed: formatSpeed(s2UseRtcmSleepUi ? rtcmInputBps : s2.bps),
            s1StatusClass, s2StatusClass,
            statusColorClass, statusText, statusBadgeClass, hideStatusBadge, rtcmInputBps
        };
    }, [device, device.ntrip_status, device.service_config, device.ntrip_stats]);
    const opsFlags = React.useMemo(() => getDeviceOpsFlags(device), [device, device.ntrip_status, device.service_config]);

    const handleDelete = (e) => {
        e.stopPropagation();
        onDelete(device.serial, device.name);
    };

    return (
        <div className={`device-card dashboard-device-card animate-fade-in-up ${isSelected ? 'active' : ''}`} style={{ animationDelay: `${index * 0.05}s` }} onClick={() => onSelect(device.serial)}>
            <div className={`status-bar ${metrics.statusColorClass}`}></div>

            <div className="dashboard-device-card__top d-flex justify-content-between align-items-start mb-1 ps-2">
                <div className="flex-grow-1 pe-2">
                    <div className="device-name" title={device.name || device.serial}>
                        {device.name || device.serial}
                    </div>
                    {!metrics.hideStatusBadge && (
                        <div className="mt-1 d-flex align-items-center gap-1">
                            <span className={`badge ${metrics.statusBadgeClass}`}>
                                {metrics.statusText}
                            </span>
                            {device.version === window.APP_VERSION ? (
                                <i className="bi bi-check-circle-fill text-success" title={`Đã đồng bộ (${device.version})`} style={{ fontSize: '0.9rem' }}></i>
                            ) : (
                                <i className="bi bi-exclamation-triangle-fill text-warning" title={`Không đồng bộ (Agent: ${device.version || '??'} | Server: ${window.APP_VERSION})`} style={{ fontSize: '0.9rem' }}></i>
                            )}
                        </div>
                    )}
                    {isAdmin && <div className="device-meta">{device.serial}</div>}
                </div>

                    {opsFlags.alert && (
                        <span className="device-ops-alert-badge" title={opsFlags.noStream ? 'No stream detected' : (opsFlags.unsynced ? 'Unsynced' : 'Needs attention')}>
                            <i className="bi bi-exclamation-triangle-fill"></i>
                        </span>
                    )}
                    <span className="badge">{device.detected_chip_type || 'UNK'}</span>
                    {device.is_lte && (
                        <span className="badge bg-primary ms-1" title="Kết nối 4G/LTE">
                            <i className="bi bi-reception-4 me-1"></i>4G
                        </span>
                    )}
            </div>

            {(['online', 'sleep'].includes(metrics.statusColorClass)) && (metrics.s1Enabled || metrics.s2Enabled) && (
                <div className="server-indicators">
                    {metrics.s1Enabled && (
                        <div className={`server-status-item ${metrics.s1StatusClass}`}>
                            <div className="server-label">
                                <i className={`bi ${metrics.s1UseRtcmSleepUi ? 'bi-cpu-fill' : 'bi-hdd-network-fill'}`}></i>
                                {metrics.s1UseRtcmSleepUi ? 'S1 RTCM' : 'S1'}
                            </div>
                            <div className="d-flex align-items-baseline gap-1">
                                <span className="server-bps">{metrics.s1Speed.value}</span>
                                <span className="server-bps-unit">{metrics.s1Speed.unit}</span>
                            </div>
                        </div>
                    )}

                    {metrics.s2Enabled && (
                        <div className={`server-status-item ${metrics.s2StatusClass}`}>
                            <div className="server-label">
                                <i className={`bi ${metrics.s2UseRtcmSleepUi ? 'bi-cpu-fill' : 'bi-hdd-network-fill'}`}></i>
                                {metrics.s2UseRtcmSleepUi ? 'S2 RTCM' : 'S2'}
                            </div>
                            <div className="d-flex align-items-baseline gap-1">
                                <span className="server-bps">{metrics.s2Speed.value}</span>
                                <span className="server-bps-unit">{metrics.s2Speed.unit}</span>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {canDelete && (
                <button className="btn btn-delete" onClick={handleDelete} title="Xóa trạm">
                    <i className="bi bi-trash3"></i>
                </button>
            )}
        </div>
    );
});

window.DeviceListItem = DeviceListItem;

const DeviceList = memo(function DeviceList({ devices, selectedDeviceSerial, onSelect, onDelete, searchTerm, onSearch, currentUser }) {
    const [localSearchTerm, setLocalSearchTerm] = useState(searchTerm);
    const [statusFilter, setStatusFilter] = useState('all');
    const searchTimeoutRef = useRef(null);
    const isAdmin = currentUser?.role === 'admin';
    const getEffectiveStatus = window.CorsDashboardShared?.getMapEffectiveStatus || ((device) => String(device?.status || 'offline').toLowerCase());
    const counts = React.useMemo(() => {
        return devices.reduce((acc, device) => {
            const status = getEffectiveStatus(device);
            const bucket = status === 'streaming' ? 'online' : status;
            const flags = getDeviceOpsFlags(device);
            acc[bucket] = (acc[bucket] || 0) + 1;
            if (flags.alert) acc.alerts += 1;
            if (flags.unsynced) acc.unsynced += 1;
            if (flags.noStream) acc.no_stream += 1;
            acc.all += 1;
            return acc;
        }, { all: 0, online: 0, sleep: 0, offline: 0, locked: 0, alerts: 0, unsynced: 0, no_stream: 0 });
    }, [devices]);
    const displayedDevices = React.useMemo(() => {
        if (statusFilter === 'all') return devices;
        return devices.filter(device => {
            const flags = getDeviceOpsFlags(device);
            if (statusFilter === 'alerts') return flags.alert;
            if (statusFilter === 'unsynced') return flags.unsynced;
            if (statusFilter === 'no_stream') return flags.noStream;
            const status = getEffectiveStatus(device);
            return (status === 'streaming' ? 'online' : status) === statusFilter;
        });
    }, [devices, statusFilter]);

    useEffect(() => {
        setLocalSearchTerm(searchTerm);
    }, [searchTerm]);

    const handleSearchChange = (e) => {
        const value = e.target.value;
        setLocalSearchTerm(value);
        if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = setTimeout(() => {
            onSearch(value);
        }, 250);
    };

    return (
        <div className="d-flex flex-column h-100 device-list-container">
            {/* Header: Số lượng & Search */}
            <div className="dashboard-list-toolbar p-3">
                <div className="d-flex justify-content-between align-items-center mb-2">
                    <h6 className="text-white mb-0 fw-bold">Trạm ({devices.length})</h6>
                    <div className="d-flex align-items-center gap-2">
                        <button className="btn btn-sm btn-link text-muted text-decoration-none p-0">
                            <i className="bi bi-filter-right fs-5"></i>
                        </button>
                    </div>
                </div>
                <div className="input-group input-group-sm">
                    <span className="input-group-text bg-dark border-secondary text-secondary">
                        <i className="bi bi-search"></i>
                    </span>
                    <input
                        type="text"
                        className="form-control search-box-modern"
                        placeholder="Tìm kiếm..."
                        value={localSearchTerm}
                        onChange={handleSearchChange}
                    />
                    {localSearchTerm && (
                        <button
                            className="btn btn-dark border-secondary text-muted"
                            type="button"
                            onClick={() => { setLocalSearchTerm(''); onSearch(''); }}
                        >
                            <i className="bi bi-x"></i>
                        </button>
                    )}
                </div>
                <div className="dashboard-status-chips">
                    {[
                        ['all', 'All'],
                        ['online', 'Online'],
                        ['sleep', 'Sleep'],
                        ['offline', 'Offline'],
                        ['locked', 'Locked'],
                        ['alerts', 'Alerts'],
                        ['unsynced', 'Unsynced'],
                        ['no_stream', 'No stream']
                    ].map(([key, label]) => (
                        <button
                            key={key}
                            type="button"
                            className={`dashboard-status-chip ${statusFilter === key ? 'active' : ''}`}
                            onClick={() => setStatusFilter(key)}
                        >
                            <span>{label}</span>
                            <strong>{counts[key] || 0}</strong>
                        </button>
                    ))}
                </div>
            </div>

            {/* List */}
            <div className="device-list-scroll flex-grow-1 overflow-auto custom-scrollbar p-2">
                {displayedDevices.length === 0 ? (
                    localSearchTerm ? (
                        <div className="text-center mt-5 text-muted animate-fade-in-up">
                            <i className="bi bi-search display-4 opacity-25"></i>
                            <p className="mt-2 small">Không tìm thấy trạm nào.</p>
                        </div>
                    ) : (
                        <div className="d-flex flex-column gap-2">
                            {[1, 2, 3, 4, 5].map((n, i) => (
                                <div key={n} className="device-card skeleton p-3 border border-secondary border-opacity-25" style={{ animationDelay: `${i * 0.05}s`, opacity: 0, animation: `fadeInUp 0.4s forwards, shimmer 2s infinite linear forwards` }}>
                                    <div className="d-flex justify-content-between mb-2">
                                        <div className="skeleton-title"></div>
                                        <div className="skeleton-badge"></div>
                                    </div>
                                    <div className="skeleton-text"></div>
                                    <div className="skeleton-text short"></div>
                                </div>
                            ))}
                        </div>
                    )
                ) : (
                    displayedDevices.map((device, index) => (
                        <DeviceListItem
                            key={device.serial}
                            device={device}
                            index={index}
                            isSelected={device.serial === selectedDeviceSerial}
                            onSelect={onSelect}
                            onDelete={onDelete}
                            currentUser={currentUser}
                        />
                    ))
                )}
            </div>
        </div>
    );
});
window.DeviceList = DeviceList;


})();
