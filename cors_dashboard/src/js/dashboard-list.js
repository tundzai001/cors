// ====================== Core dashboard list components ======================
(() => {
const { useState, useEffect, memo, useRef } = React;

const STATUS_TEXT_MAP = {
    'online': 'ONLINE',
    'offline': 'OFFLINE',
    'unprovisioned': 'CHƯA KÍCH HOẠT',
    'awaiting_license': 'CHỜ LICENSE',
    'awaiting_agent': 'CHỜ AGENT',
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

function getDeviceOpsFlags(device) {
    const status = String(device?.status || 'offline').toLowerCase();
    const config = device?.service_config || {};
    const ntripStatus = device?.ntrip_status || {};
    const ntripStats = device?.ntrip_stats || {};
    const toBool = (value, defaultValue = false) => {
        if (value === null || value === undefined) return defaultValue;
        if (typeof value === 'boolean') return value;
        if (typeof value === 'number') return value !== 0;
        if (typeof value === 'string') {
            const normalized = value.trim().toLowerCase();
            if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
            if (['false', '0', 'no', 'off'].includes(normalized)) return false;
        }
        return Boolean(value);
    };
    const isServerConfigured = (serverId) => Boolean(
        String(config[`serverhost${serverId}`] || '').trim() &&
        String(config[`port${serverId}`] || '').trim() &&
        String(config[`mountpoint${serverId}`] || '').trim()
    );
    const s1Enabled = toBool(config.server1_enabled, isServerConfigured(1));
    const s2Enabled = toBool(config.server2_enabled, isServerConfigured(2));
    const getServerBps = (serverId) => {
        const key = `server${serverId}`;
        const server = ntripStatus[key];
        const rawBps = ntripStats[`${key}_bps`] ?? server?.bps ?? ntripStatus[`${key}_bps`] ?? 0;
        const value = Number(rawBps);
        return Number.isFinite(value) ? Math.max(0, value) : 0;
    };
    const outputBps = getServerBps(1) + getServerBps(2);
    const noStream = !isDeviceStaleOffline(device) &&
        ['online', 'streaming'].includes(status) &&
        (s1Enabled || s2Enabled) && outputBps <= 0;
    const unsynced = device?.is_synced === false;
    const syncErrors = Array.isArray(device?.sync_errors) ? device.sync_errors : [];

    const rtcmInputBps = Number(
        device?.rtcm_input_bps ||
        device?.ntrip_stats?.rtcm_input_bps ||
        device?.ntrip_stats?.rtcm_in_bps ||
        device?.mqtt_stats?.source_bps ||
        0
    );
    const antennaStatus = device?.antenna_status || device?.system_info?.antenna_status;
    const antennaFault = (antennaStatus && antennaStatus.state === "FAULT") ||
        (['online', 'sleep', 'streaming', 'starting'].includes(status) && rtcmInputBps > 0 && rtcmInputBps < 100);

    const alert = status === 'locked' || unsynced || antennaFault || noStream;
    let alertTitle = 'Needs attention';
    if (unsynced) {
        alertTitle = syncErrors.join('\n') || 'Unsynced';
    } else if (status === 'locked') {
        alertTitle = 'Locked';
    } else if (antennaFault) {
        alertTitle = (antennaStatus && antennaStatus.message) || `Lỗi Ăng-ten (Dòng RTCM cực thấp: ${rtcmInputBps} B/s)`;
    }

    if (noStream && alertTitle === 'Needs attention') {
        alertTitle = 'NTRIP đã cấu hình nhưng chưa có dữ liệu correction';
    }
    return { alert, unsynced, noStream, syncErrors, alertTitle, antennaFault };
}

function hasAccessTokenGap(device) {
    if (window.CorsDashboardShared?.isAccessTokenSynced?.(device)) return false;
    const authStatus = device?.system_info?.auth_status || {};
    const serverTokenPresent = Object.prototype.hasOwnProperty.call(authStatus, 'server_token_present')
        ? authStatus.server_token_present
        : authStatus.token_required;
    if (authStatus.token_deployment_pending === true) return true;
    if (isDeviceStaleOffline(device)) {
        return serverTokenPresent === false;
    }
    const tokenCheckAvailable = authStatus.token_check_available !== false;
    return String(device?.status || '').toLowerCase() === 'awaiting_license' ||
        authStatus.needs_token_deploy === true ||
        serverTokenPresent === false ||
        (tokenCheckAvailable && authStatus.agent_token_present === false) ||
        (tokenCheckAvailable && authStatus.token_synced === false);
}

function isDeviceStaleOffline(device, nowMs = Date.now()) {
    const rawStatus = String(device?.status || '').toLowerCase();
    if (rawStatus === 'offline') return true;
    const rawTimestamp = Number(device?.timestamp || device?.system_info?.timestamp || 0);
    const timestamp = rawTimestamp > 1e12 ? rawTimestamp / 1000 : rawTimestamp;
    if (!timestamp) return false;
    return (nowMs / 1000) - timestamp > 180;
}

const STATUS_CLASS_MAP = {
    'online': 'bg-success',
    'offline': 'bg-danger',
    'unprovisioned': 'bg-warning text-dark',
    'awaiting_license': 'bg-info text-dark',
    'awaiting_agent': 'bg-info text-dark',
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

const DeviceListItem = memo(function DeviceListItem({ device, index = 0, isSelected, onSelect, onDelete, currentUser, offlineNow = null }) {
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
        const rawStatus = isDeviceStaleOffline(device, offlineNow || Date.now()) ? 'offline' : String(device?.status || 'offline').toLowerCase();
        const sourceOffline = rawStatus === 'offline';
        const tokenGap = hasAccessTokenGap(device);

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

        const getServerTelemetry = window.CorsDashboardShared?.getServerTelemetry;
        const s1 = getServerTelemetry?.(device, 1) || { connected: false, bps: Number(ntripStats.server1_bps ?? ntripStatus.server1?.bps ?? 0) || 0 };
        const s2 = getServerTelemetry?.(device, 2) || { connected: false, bps: Number(ntripStats.server2_bps ?? ntripStatus.server2?.bps ?? 0) || 0 };

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
        const s1UseRtcmSleepUi = s1.bps <= 0 && s1SleepMode && hasRtcmInputMetric && rtcmInputBps > 0;

        const s2State = streamState.server2 || {};
        const s2OnDemand = toBool(s2State.on_demand, toBool(config.server2_stream_on_demand, toBool(config.stream_on_demand, false)));
        const s2Active = toBool(s2State.active, toBool(config.server2_stream_active, !s2OnDemand));
        const s2SleepMode = s2Enabled && s2OnDemand && !s2Active;
        const s2UseRtcmSleepUi = s2.bps <= 0 && s2SleepMode && hasRtcmInputMetric && rtcmInputBps > 0;

        const statusDevice = { ...device, status: rawStatus };
        const primaryServerId = window.CorsDashboardShared?.getPreferredOutputServerId?.(statusDevice);
        const operationalStatus = window.CorsDashboardShared?.getMapEffectiveStatus?.(statusDevice) || rawStatus;
        const effectiveStatus = sourceOffline ? 'offline' : operationalStatus;
        const displayStatusKey = tokenGap && rawStatus !== 'offline' ? 'awaiting_license' : effectiveStatus;
        const statusColorClass = tokenGap && rawStatus !== 'offline'
            ? (['online', 'streaming', 'sleep', 'starting'].includes(operationalStatus) ? operationalStatus : 'awaiting_license')
            : effectiveStatus;

        const statusText = STATUS_TEXT_MAP[displayStatusKey] || String(displayStatusKey || 'UNKNOWN').toUpperCase();
        const statusBadgeClass = STATUS_CLASS_MAP[displayStatusKey] || 'bg-secondary';
        const hideStatusBadge = !tokenGap && ['online', 'sleep', 'offline', 'streaming', 'starting'].includes(effectiveStatus);

        // Per-server individual color: pushing=green, sleep/input-only=blue, idle=disconnected
        const s1StatusClass = sourceOffline ? 'disconnected' : (s1.bps > 0 ? 'online' : (s1.sleep === true || s1SleepMode ? 'sleep' : (s1.connected ? 'online' : (primaryServerId === 1 && effectiveStatus === 'sleep' ? 'sleep' : 'disconnected'))));
        const s2StatusClass = sourceOffline ? 'disconnected' : (s2.bps > 0 ? 'online' : (s2.sleep === true || s2SleepMode ? 'sleep' : (s2.connected ? 'online' : (primaryServerId === 2 && effectiveStatus === 'sleep' ? 'sleep' : 'disconnected'))));

        return {
            s1Enabled, s1SleepMode, s1UseRtcmSleepUi, s1Speed: formatSpeed(sourceOffline ? 0 : (s1UseRtcmSleepUi ? rtcmInputBps : s1.bps)),
            s2Enabled, s2SleepMode, s2UseRtcmSleepUi, s2Speed: formatSpeed(sourceOffline ? 0 : (s2UseRtcmSleepUi ? rtcmInputBps : s2.bps)),
            s1StatusClass, s2StatusClass,
            statusColorClass, statusText, statusBadgeClass, hideStatusBadge, rtcmInputBps, sourceOffline
        };
    }, [device, device.ntrip_status, device.ntrip_stream_state, device.service_config, device.ntrip_stats, offlineNow]);
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
                            {device.is_synced ? (
                                <i className="bi bi-check-circle-fill text-success" title={`Đã đồng bộ`} style={{ fontSize: '0.9rem' }}></i>
                            ) : (
                                <i className="bi bi-exclamation-triangle-fill text-warning" title={device.sync_errors && device.sync_errors.length > 0 ? device.sync_errors.join('\n') : "Không đồng bộ"} style={{ fontSize: '0.9rem' }}></i>
                            )}
                        </div>
                    )}
                    {isAdmin && <div className="device-meta">{device.serial}</div>}
                </div>

                    {opsFlags.alert && (
                        <span className="device-ops-alert-badge" title={opsFlags.alertTitle}>
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

            {(metrics.s1Enabled || metrics.s2Enabled) && (
                <div className="server-indicators">
                    {metrics.s1Enabled && (
                        <div
                            className={`server-status-item ${metrics.s1StatusClass}`}
                            title={metrics.sourceOffline ? 'S1 đã cấu hình nhưng trạm offline; không có dữ liệu trực tiếp.' : undefined}
                        >
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
                        <div
                            className={`server-status-item ${metrics.s2StatusClass}`}
                            title={metrics.sourceOffline ? 'S2 đã cấu hình nhưng trạm offline; không có dữ liệu trực tiếp.' : undefined}
                        >
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
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [clockNow, setClockNow] = useState(Date.now());
    const searchTimeoutRef = useRef(null);
    const isAdmin = currentUser?.role === 'admin';
    const filterOptions = [
        ['all', 'All'], ['online', 'Online'], ['sleep', 'Sleep'],
        ['offline', 'Offline'], ['locked', 'Locked'], ['alerts', 'Alerts'],
        ['unsynced', 'Unsynced'], ['no_stream', 'No stream']
    ];
    const getEffectiveStatus = window.CorsDashboardShared?.getMapEffectiveStatus || ((device) => String(device?.status || 'offline').toLowerCase());
    const getListStatus = React.useCallback((device) => {
        const rawStatus = isDeviceStaleOffline(device, clockNow) ? 'offline' : String(device?.status || 'offline').toLowerCase();
        const effective = getEffectiveStatus({ ...device, status: rawStatus });
        if (rawStatus === 'offline') return 'offline';
        return hasAccessTokenGap(device) && effective === 'offline' ? 'awaiting_license' : effective;
    }, [getEffectiveStatus, clockNow]);
    const counts = React.useMemo(() => {
        return devices.reduce((acc, device) => {
            const status = getListStatus(device);
            const bucket = status === 'streaming' ? 'online' : status;
            const flags = getDeviceOpsFlags(device);
            acc[bucket] = (acc[bucket] || 0) + 1;
            if (flags.alert) acc.alerts += 1;
            if (flags.unsynced) acc.unsynced += 1;
            if (flags.noStream) acc.no_stream += 1;
            acc.all += 1;
            return acc;
        }, { all: 0, online: 0, sleep: 0, offline: 0, awaiting_license: 0, locked: 0, alerts: 0, unsynced: 0, no_stream: 0 });
    }, [devices, getListStatus]);
    const displayedDevices = React.useMemo(() => {
        if (statusFilter === 'all') return devices;
        return devices.filter(device => {
            const flags = getDeviceOpsFlags(device);
            if (statusFilter === 'alerts') return flags.alert;
            if (statusFilter === 'unsynced') return flags.unsynced;
            if (statusFilter === 'no_stream') return flags.noStream;
            const status = getListStatus(device);
            return (status === 'streaming' ? 'online' : status) === statusFilter;
        });
    }, [devices, statusFilter, getListStatus]);

    useEffect(() => {
        setLocalSearchTerm(searchTerm);
    }, [searchTerm]);

    useEffect(() => {
        const interval = setInterval(() => setClockNow(Date.now()), 30000);
        return () => clearInterval(interval);
    }, []);

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
                    <h6 className="text-white mb-0 fw-bold">Trạm ({devices.length}){statusFilter !== 'all' && <span className="dashboard-active-filter ms-2">{filterOptions.find(([key]) => key === statusFilter)?.[1]}</span>}</h6>
                    <button
                        className="btn btn-sm btn-link text-muted text-decoration-none p-0 dashboard-filter-toggle"
                        type="button"
                        aria-label="Bộ lọc danh sách trạm"
                        aria-expanded={filtersOpen}
                        aria-controls="dashboard-station-filters"
                        onClick={() => setFiltersOpen(open => !open)}
                    >
                        <i className="bi bi-filter-right fs-5" aria-hidden="true"></i>
                    </button>
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
                {filtersOpen && <div className="dashboard-status-chips dashboard-filter-menu" id="dashboard-station-filters" role="group" aria-label="Lọc trạm theo trạng thái">
                    {filterOptions.map(([key, label]) => (
                        <button
                            key={key}
                            type="button"
                            className={`dashboard-status-chip ${statusFilter === key ? 'active' : ''}`}
                            aria-pressed={statusFilter === key}
                            onClick={() => { setStatusFilter(key); setFiltersOpen(false); }}
                        >
                            <span>{label}</span>
                            <strong>{counts[key] || 0}</strong>
                        </button>
                    ))}
                </div>}
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
                            offlineNow={clockNow}
                        />
                    ))
                )}
            </div>
        </div>
    );
});
window.DeviceList = React.memo(DeviceList);


})();
