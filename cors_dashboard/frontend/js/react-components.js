// ======================
// react-components.js 
// ======================

const { useState, useEffect, memo, useRef } = React;

// === CONSTANTS ===
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
    const rawStatus = String(device?.status || '').toLowerCase();
    
    const ntripStats = device?.ntrip_stats || {};
    const virtualRtcmInBps = Number(ntripStats.rtcm_input_bps || ntripStats.rtcm_in_bps || 0);
    const isVirtualStation = String(device?.detected_chip_type || '').toUpperCase() === 'VIRTUAL';

    // 1. FORCED OFFLINE: Only if status indicates the agent is truly disconnected
    if (!['online', 'streaming', 'sleep', 'starting'].includes(rawStatus)) {
        if (isVirtualStation && virtualRtcmInBps > 0) {
            // Keep going for virtual
        } else {
            return 'offline';
        }
    }

    const cfg = device?.service_config || {};
    const s1Enabled = parseBoolCompat(cfg.server1_enabled, isServerConfiguredCompat(cfg, 1));
    const s2Enabled = parseBoolCompat(cfg.server2_enabled, isServerConfiguredCompat(cfg, 2));

    if (!s1Enabled && !s2Enabled) {
        return (isVirtualStation && virtualRtcmInBps > 0) ? 'online' : 'offline';
    }

    // 2. PRIMARY SERVER LOGIC: Handle aitogy.com.vn with optional ports
    const s1Host = String(cfg.serverhost1 || '').trim().toLowerCase();
    const s2Host = String(cfg.serverhost2 || '').trim().toLowerCase();
    const preferred = 'aitogy.com.vn';
    
    let primarySid = 1;
    if (s1Host.includes(preferred)) primarySid = 1;
    else if (s2Host.includes(preferred)) primarySid = 2;
    else primarySid = 1; 

    // 3. PRIMARY STATUS (Sleep on primary > Traffic on primary > Online)
    const ntripStatusMap = device?.ntrip_status || {};
    const primaryBps = Number(ntripStatusMap[`server${primarySid}`]?.bps || 0);
    const onDemand = parseBoolCompat(cfg[`server${primarySid}_stream_on_demand`], parseBoolCompat(cfg.stream_on_demand, false));
    const active = onDemand ? parseBoolCompat(cfg[`server${primarySid}_stream_active`], parseBoolCompat(cfg.stream_active, false)) : true;
    const isSleep = onDemand && !active;
    
    let result = 'online';
    if (isSleep) {
        result = 'sleep';
    } else if (primaryBps > 0) {
        result = 'online';
    } else {
        result = 'online';
    }


    const okStatuses = ['online', 'streaming', 'sleep', 'starting'];
    // SAFETY GUARD: If agent is online/sleep, NEVER show Red
    if (result === 'offline' && okStatuses.includes(rawStatus)) {
        result = 'online';
    }
    return result;
};


// ===============================================================================
// === DEVICE LIST COMPONENTS
// ===============================================================================

const DeviceListItem = memo(function DeviceListItem({ device, isSelected, onSelect, onDelete, currentUser }) {
    const isAdmin = currentUser?.role === 'admin';
    const canDelete = currentUser?.role === 'admin' ||
        (currentUser?.role === 'coordinator' && device.user_id === currentUser.id);

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
        const streamState = device.ntrip_stream_state || {};
        
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
        
        const rtcmInputBps = Number(ntripStats.rtcm_input_bps || ntripStats.rtcm_in_bps || 0);
        const hasRtcmInputMetric = Object.prototype.hasOwnProperty.call(ntripStats, 'rtcm_input_bps') || Object.prototype.hasOwnProperty.call(ntripStats, 'rtcm_in_bps');

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
        const isVirtualStation = chipUpper === 'VIRTUAL';
        const isVirtualMonitorOnly = isVirtualStation && !s1Enabled && !s2Enabled;

        // --- CALCULATE STATUS COLOR WITH STRICT PRIMARY SERVER PRIORITY ---
        const rawStatus = String(device?.status || 'offline').toLowerCase();
        let effectiveStatus = 'offline';
        
        const okStatuses = ['online', 'streaming', 'sleep', 'starting'];
        if (okStatuses.includes(rawStatus) || (isVirtualStation && hasInputTraffic)) {
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
                    if (pSleep) {
                        effectiveStatus = 'sleep';
                    } else if (pBps > 0) {
                        effectiveStatus = 'online';
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
                        effectiveStatus = (isVirtualStation && hasInputTraffic) ? 'online' : 'offline';
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

        // Per-server individual color: sleep=blue, pushing=green, idle=disconnected
        const s1StatusClass = s1SleepMode ? 'sleep' : (s1.bps > 0 ? 'online' : 'disconnected');
        const s2StatusClass = s2SleepMode ? 'sleep' : (s2.bps > 0 ? 'online' : 'disconnected');

        return {
            s1Enabled, s1SleepMode, s1UseRtcmSleepUi, s1Speed: formatSpeed(s1UseRtcmSleepUi ? rtcmInputBps : s1.bps),
            s2Enabled, s2SleepMode, s2UseRtcmSleepUi, s2Speed: formatSpeed(s2UseRtcmSleepUi ? rtcmInputBps : s2.bps),
            s1StatusClass, s2StatusClass,
            statusColorClass, statusText, statusBadgeClass, hideStatusBadge, isVirtualStation, rtcmInputBps
        };
    }, [device, device.ntrip_status, device.service_config, device.ntrip_stats]);

    const handleDelete = (e) => {
        e.stopPropagation();
        onDelete(device.serial, device.name);
    };

    return (
        <div className={`device-card ${isSelected ? 'active' : ''}`} onClick={() => onSelect(device.serial)}>
            <div className={`status-bar ${metrics.statusColorClass}`}></div>

            <div className="d-flex justify-content-between align-items-start mb-1 ps-2">
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

                {metrics.isVirtualStation ? (
                    <span className="badge bg-info text-dark" title="Trạm ảo - Nhận RTCM từ Cục và phát lại">
                        <i className="bi bi-cloud-check-fill me-1"></i>VIRTUAL
                    </span>
                ) : (
                    <span className="badge">{device.detected_chip_type || 'UNK'}</span>
                )}
            </div>

            {(metrics.statusColorClass === 'online' || metrics.statusColorClass === 'sleep') && (metrics.s1Enabled || metrics.s2Enabled) && (
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

const DeviceList = memo(function DeviceList({ devices, selectedDeviceSerial, onSelect, onDelete, onOpenVirtualStationForm, searchTerm, onSearch, currentUser }) {
    const [localSearchTerm, setLocalSearchTerm] = useState(searchTerm);
    const searchTimeoutRef = useRef(null);
    const isAdmin = currentUser?.role === 'admin';

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
            <div className="p-3 border-bottom border-secondary bg-black bg-opacity-25">
                <div className="d-flex justify-content-between align-items-center mb-2">
                    <h6 className="text-white mb-0 fw-bold">Trạm ({devices.length})</h6>
                    <div className="d-flex align-items-center gap-2">
                        {isAdmin && (
                            <button
                                type="button"
                                className="btn btn-sm btn-outline-info py-0 px-2"
                                onClick={() => onOpenVirtualStationForm && onOpenVirtualStationForm()}
                                title="Thêm trạm ảo"
                            >
                                <i className="bi bi-plus-circle me-1"></i>Virtual
                            </button>
                        )}
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
            </div>

            {/* List */}
            <div className="flex-grow-1 overflow-auto custom-scrollbar p-2">
                {devices.length === 0 ? (
                    <div className="text-center mt-5 text-muted">
                        <i className="bi bi-inbox display-4 opacity-25"></i>
                        <p className="mt-2 small">Không tìm thấy dữ liệu</p>
                    </div>
                ) : (
                    devices.map(device => (
                        <DeviceListItem
                            key={device.serial}
                            device={device}
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

const VirtualStationFormPanel = memo(function VirtualStationFormPanel({ currentUser, onCreateVirtualStation, onCancel }) {
    const [isCreatingVirtual, setIsCreatingVirtual] = useState(false);
    const [formState, setFormState] = useState({
        virtual_mode: 'relay_share',
        name: '',
        lat: '',
        lon: '',
        alt: '0',
        ncomport: '',
        reconnectioninterval: '10',
        stream_on_demand: true,
        stream_active: false,
        server1_enabled: true,
        server1_stream_on_demand: true,
        server1_stream_active: false,
        ntrip_version1: 1,
        serverhost1: '',
        port1: '2101',
        mountpoint1: '',
        username1: 'source',
        password1: '',
        server2_enabled: false,
        server2_stream_on_demand: true,
        server2_stream_active: false,
        ntrip_version2: 1,
        serverhost2: '',
        port2: '2101',
        mountpoint2: '',
        username2: 'source',
        password2: '',
        rtcm_enabled: true,
        rtcmntripversion1: 1,
        rtcmserver1: '',
        rtcmport1: '2101',
        rtcmmountpoint1: '',
        rtcmusername1: '',
        rtcmpassword1: ''
    });

    const isAdmin = currentUser?.role === 'admin';

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

    const handleChange = (e) => {
        const { name, value, type, checked } = e.target;
        const inputValue = type === 'checkbox' ? checked : value;
        setFormState((prev) => {
            const next = { ...prev, [name]: inputValue };

            if (name === 'virtual_mode') {
                if (value === 'monitor_only') {
                    next.server1_enabled = false;
                    next.server2_enabled = false;
                } else if (!toBool(prev.server1_enabled, false) && !toBool(prev.server2_enabled, false)) {
                    next.server1_enabled = true;
                }
            }

            if (name === 'server1_stream_on_demand') {
                next.server1_stream_active = !checked;
            }
            if (name === 'server2_stream_on_demand') {
                next.server2_stream_active = !checked;
            }

            return next;
        });
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!isAdmin || !onCreateVirtualStation) return;

        const lat = parseFloat(formState.lat);
        const lon = parseFloat(formState.lon);
        const alt = parseFloat(formState.alt || '0');

        if (!formState.name.trim()) return window.showError('Nhập tên trạm ảo');
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return window.showError('Tọa độ không hợp lệ');

        const isRelayShareMode = formState.virtual_mode === 'relay_share';

        const s1Enabled = toBool(formState.server1_enabled, false);
        const s2Enabled = toBool(formState.server2_enabled, false);

        const hasS1 = Boolean(
            String(formState.serverhost1 || '').trim() &&
            String(formState.port1 || '').trim() &&
            String(formState.mountpoint1 || '').trim()
        );
        const hasS2 = Boolean(
            String(formState.serverhost2 || '').trim() &&
            String(formState.port2 || '').trim() &&
            String(formState.mountpoint2 || '').trim()
        );
        const hasEnabledOutput = (s1Enabled && hasS1) || (s2Enabled && hasS2);
        const hasRtcm = Boolean(
            formState.rtcm_enabled &&
            String(formState.rtcmserver1 || '').trim() &&
            String(formState.rtcmport1 || '').trim() &&
            String(formState.rtcmmountpoint1 || '').trim() &&
            String(formState.rtcmusername1 || '').trim() &&
            String(formState.rtcmpassword1 || '').trim()
        );

        if (!hasRtcm) {
            return window.showError('Pipeline bắt buộc có RTCM Input (tài khoản rover + mountpoint input).');
        }

        if (isRelayShareMode && !hasEnabledOutput) {
            return window.showError('Chế độ nhận và đẩy lên bắt buộc bật ít nhất 1 output server hợp lệ (Host/Port/Mountpoint).');
        }

        if (s1Enabled && !hasS1) {
            return window.showError('Server 1 đã bật nhưng thiếu cấu hình Host/Port/Mountpoint.');
        }
        if (s2Enabled && !hasS2) {
            return window.showError('Server 2 đã bật nhưng thiếu cấu hình Host/Port/Mountpoint.');
        }

        const s1CanPush = !toBool(formState.server1_stream_on_demand, true) || !!formState.server1_stream_active;
        const s2CanPush = !toBool(formState.server2_stream_on_demand, true) || !!formState.server2_stream_active;

        const hasAnyEnabledServer = s1Enabled || s2Enabled;
        const derivedGlobalOnDemand = hasAnyEnabledServer
            ? ((s1Enabled && !!formState.server1_stream_on_demand) || (s2Enabled && !!formState.server2_stream_on_demand))
            : !!formState.server1_stream_on_demand;
        const derivedGlobalActive = hasAnyEnabledServer
            ? ((s1Enabled && s1CanPush) || (s2Enabled && s2CanPush))
            : !derivedGlobalOnDemand;

        const payload = {
            name: formState.name.trim(),
            lat: String(formState.lat).trim(),
            lon: String(formState.lon).trim(),
            alt: String(formState.alt || '0').trim(),
            service_config: {
                ncomport: formState.ncomport || null,
                reconnectioninterval: formState.reconnectioninterval ? parseInt(formState.reconnectioninterval, 10) : 10,
                stream_on_demand: derivedGlobalOnDemand,
                stream_active: derivedGlobalActive,
                server1_enabled: s1Enabled,
                server1_stream_on_demand: !!formState.server1_stream_on_demand,
                server1_stream_active: !!s1CanPush,
                ntrip_version1: 1,
                serverhost1: formState.serverhost1 || null,
                port1: formState.port1 ? parseInt(formState.port1, 10) : null,
                mountpoint1: formState.mountpoint1 || null,
                username1: formState.username1 || null,
                password1: formState.password1 || null,
                server2_enabled: s2Enabled,
                server2_stream_on_demand: !!formState.server2_stream_on_demand,
                server2_stream_active: !!s2CanPush,
                ntrip_version2: 1,
                serverhost2: formState.serverhost2 || null,
                port2: formState.port2 ? parseInt(formState.port2, 10) : null,
                mountpoint2: formState.mountpoint2 || null,
                username2: formState.username2 || null,
                password2: formState.password2 || null,
                rtcm_enabled: hasRtcm,
                rtcmntripversion1: parseInt(formState.rtcmntripversion1, 10) === 2 ? 2 : 1,
                rtcmserver1: formState.rtcmserver1 || null,
                rtcmport1: formState.rtcmport1 ? parseInt(formState.rtcmport1, 10) : null,
                rtcmmountpoint1: formState.rtcmmountpoint1 || null,
                rtcmusername1: formState.rtcmusername1 || null,
                rtcmpassword1: formState.rtcmpassword1 || null
            }
        };

        setIsCreatingVirtual(true);
        try {
            await onCreateVirtualStation(payload);
        } finally {
            setIsCreatingVirtual(false);
        }
    };

    const isRelayShareMode = formState.virtual_mode === 'relay_share';
    const server1Enabled = !!formState.server1_enabled;
    const server2Enabled = !!formState.server2_enabled;
    const rtcmEnabled = !!formState.rtcm_enabled;

    if (!isAdmin) {
        return <div className="alert alert-warning">Bạn không có quyền tạo trạm ảo.</div>;
    }

    return (
        <form onSubmit={handleSubmit}>
            <div
                className="p-3 p-md-4 rounded-4 mb-3"
                style={{
                    background: 'linear-gradient(120deg, rgba(13,110,253,0.14) 0%, rgba(25,135,84,0.14) 100%)',
                    border: '1px solid rgba(13,110,253,0.25)'
                }}
            >
                <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                    <div>
                        <h5 className="mb-1">Tạo Trạm Ảo CORS</h5>
                        <p className="small text-muted mb-0">Điền input rover trước, sau đó chọn các server output cần đẩy dữ liệu.</p>
                    </div>
                    <span className={`badge ${isRelayShareMode ? 'bg-primary-subtle text-primary-emphasis' : 'bg-secondary-subtle text-secondary-emphasis'}`}>
                        {isRelayShareMode ? 'Mode: Relay + Share' : 'Mode: Monitor Only'}
                    </span>
                </div>
                <div className="d-flex flex-wrap gap-2 small">
                    <span className={`badge rounded-pill ${server1Enabled ? 'text-bg-success' : 'text-bg-secondary'}`}>Server 1 {server1Enabled ? 'ON' : 'OFF'}</span>
                    <span className={`badge rounded-pill ${server2Enabled ? 'text-bg-success' : 'text-bg-secondary'}`}>Server 2 {server2Enabled ? 'ON' : 'OFF'}</span>
                    <span className={`badge rounded-pill ${rtcmEnabled ? 'text-bg-info' : 'text-bg-secondary'}`}>RTCM Input {rtcmEnabled ? 'ON' : 'OFF'}</span>
                </div>
            </div>

            <div className="card border-0 shadow-sm mb-3">
                <div className="card-body">
                    <h6 className="fw-bold mb-3">1) Cài đặt chung</h6>
                    <div className="row g-3">
                        <div className="col-md-6">
                            <label className="form-label">Kiểu trạm ảo</label>
                            <select className="form-select" name="virtual_mode" value={formState.virtual_mode} onChange={handleChange}>
                                <option value="relay_share">Nhận dữ liệu và đẩy lên mountpoint của mình</option>
                                <option value="monitor_only">Chỉ nhận dữ liệu để hiển thị web (không đẩy lên)</option>
                            </select>
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Kết nối lại (giây)</label>
                            <input className="form-control" name="reconnectioninterval" value={formState.reconnectioninterval} onChange={handleChange} />
                        </div>
                        <div className="col-md-8">
                            <label className="form-label">Tên trạm</label>
                            <input className="form-control" name="name" value={formState.name} onChange={handleChange} required />
                        </div>
                        <div className="col-md-4">
                            <label className="form-label">Định danh Trạm (ID)</label>
                            <input className="form-control" name="ncomport" value={formState.ncomport} onChange={handleChange} placeholder="TRAM_HN_01" />
                        </div>
                        <div className="col-md-4">
                            <label className="form-label">Latitude</label>
                            <input className="form-control" name="lat" value={formState.lat} onChange={handleChange} required />
                        </div>
                        <div className="col-md-4">
                            <label className="form-label">Longitude</label>
                            <input className="form-control" name="lon" value={formState.lon} onChange={handleChange} required />
                        </div>
                        <div className="col-md-4">
                            <label className="form-label">Altitude</label>
                            <input className="form-control" name="alt" value={formState.alt} onChange={handleChange} />
                        </div>
                    </div>
                </div>
            </div>

            <div className="card border-0 shadow-sm mb-3">
                <div className="card-body">
                    <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                        <h6 className="fw-bold mb-0">2) NTRIP Caster Output - Server 1</h6>
                        <div className="form-check form-switch mb-0">
                            <input
                                className="form-check-input"
                                type="checkbox"
                                role="switch"
                                id="virtual_server1_enabled"
                                name="server1_enabled"
                                checked={server1Enabled}
                                onChange={handleChange}
                                disabled={!isRelayShareMode}
                            />
                            <label className="form-check-label" htmlFor="virtual_server1_enabled">Kích hoạt Server 1</label>
                        </div>
                    </div>
                    <div className="row g-3">
                        <div className="col-md-6">
                            <label className="form-label">Chế độ stream</label>
                            <div className="form-check form-switch pt-2">
                                <input
                                    className="form-check-input"
                                    type="checkbox"
                                    role="switch"
                                    id="virtual_server1_stream_on_demand"
                                    name="server1_stream_on_demand"
                                    checked={!!formState.server1_stream_on_demand}
                                    onChange={handleChange}
                                    disabled={!server1Enabled || !isRelayShareMode}
                                />
                                <label className="form-check-label" htmlFor="virtual_server1_stream_on_demand">
                                    {formState.server1_stream_on_demand ? 'Ngủ (On-Demand)' : 'Luôn mở (Always-On)'}
                                </label>
                            </div>
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Host / IP</label>
                            <input className="form-control" name="serverhost1" value={formState.serverhost1} onChange={handleChange} disabled={!server1Enabled || !isRelayShareMode} />
                        </div>
                        <div className="col-md-4">
                            <label className="form-label">Port</label>
                            <input className="form-control" name="port1" value={formState.port1} onChange={handleChange} disabled={!server1Enabled || !isRelayShareMode} />
                        </div>
                        <div className="col-md-8">
                            <label className="form-label">Mountpoint</label>
                            <input className="form-control" name="mountpoint1" value={formState.mountpoint1} onChange={handleChange} disabled={!server1Enabled || !isRelayShareMode} />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Username</label>
                            <input className="form-control" name="username1" value={formState.username1} onChange={handleChange} disabled={!server1Enabled || !isRelayShareMode} />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Password</label>
                            <input className="form-control" type="password" name="password1" value={formState.password1} onChange={handleChange} disabled={!server1Enabled || !isRelayShareMode} />
                        </div>
                    </div>
                </div>
            </div>

            <div className="card border-0 shadow-sm mb-3">
                <div className="card-body">
                    <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                        <h6 className="fw-bold mb-0">3) NTRIP Caster Output - Server 2</h6>
                        <div className="form-check form-switch mb-0">
                            <input
                                className="form-check-input"
                                type="checkbox"
                                role="switch"
                                id="virtual_server2_enabled"
                                name="server2_enabled"
                                checked={server2Enabled}
                                onChange={handleChange}
                                disabled={!isRelayShareMode}
                            />
                            <label className="form-check-label" htmlFor="virtual_server2_enabled">Kích hoạt Server 2</label>
                        </div>
                    </div>
                    <div className="row g-3">
                        <div className="col-md-6">
                            <label className="form-label">Chế độ stream</label>
                            <div className="form-check form-switch pt-2">
                                <input
                                    className="form-check-input"
                                    type="checkbox"
                                    role="switch"
                                    id="virtual_server2_stream_on_demand"
                                    name="server2_stream_on_demand"
                                    checked={!!formState.server2_stream_on_demand}
                                    onChange={handleChange}
                                    disabled={!server2Enabled || !isRelayShareMode}
                                />
                                <label className="form-check-label" htmlFor="virtual_server2_stream_on_demand">
                                    {formState.server2_stream_on_demand ? 'Ngủ (On-Demand)' : 'Luôn mở (Always-On)'}
                                </label>
                            </div>
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Host / IP</label>
                            <input className="form-control" name="serverhost2" value={formState.serverhost2} onChange={handleChange} disabled={!server2Enabled || !isRelayShareMode} />
                        </div>
                        <div className="col-md-4">
                            <label className="form-label">Port</label>
                            <input className="form-control" name="port2" value={formState.port2} onChange={handleChange} disabled={!server2Enabled || !isRelayShareMode} />
                        </div>
                        <div className="col-md-8">
                            <label className="form-label">Mountpoint</label>
                            <input className="form-control" name="mountpoint2" value={formState.mountpoint2} onChange={handleChange} disabled={!server2Enabled || !isRelayShareMode} />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Username</label>
                            <input className="form-control" name="username2" value={formState.username2} onChange={handleChange} disabled={!server2Enabled || !isRelayShareMode} />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Password</label>
                            <input className="form-control" type="password" name="password2" value={formState.password2} onChange={handleChange} disabled={!server2Enabled || !isRelayShareMode} />
                        </div>
                    </div>
                </div>
            </div>

            <div className="card border-0 shadow-sm mb-4">
                <div className="card-body">
                    <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                        <h6 className="fw-bold mb-0">4) RTCM Input (Rover)</h6>
                        <div className="form-check form-switch mb-0">
                            <input
                                className="form-check-input"
                                type="checkbox"
                                role="switch"
                                id="virtual_rtcm_enabled"
                                name="rtcm_enabled"
                                checked={rtcmEnabled}
                                onChange={handleChange}
                            />
                            <label className="form-check-label" htmlFor="virtual_rtcm_enabled">Bật luồng input rover</label>
                        </div>
                    </div>
                    <div className="row g-3">
                        <div className="col-12">
                            <label className="form-label d-block">NTRIP Version</label>
                            <div className="btn-group" role="group" aria-label="RTCM NTRIP Version">
                                <input
                                    type="radio"
                                    className="btn-check"
                                    name="rtcmntripversion1"
                                    id="virtual_rtcm_ntrip_v1"
                                    value="1"
                                    checked={parseInt(formState.rtcmntripversion1, 10) === 1}
                                    onChange={handleChange}
                                    disabled={!rtcmEnabled}
                                />
                                <label className="btn btn-outline-secondary" htmlFor="virtual_rtcm_ntrip_v1">NTRIP v1</label>
                                <input
                                    type="radio"
                                    className="btn-check"
                                    name="rtcmntripversion1"
                                    id="virtual_rtcm_ntrip_v2"
                                    value="2"
                                    checked={parseInt(formState.rtcmntripversion1, 10) === 2}
                                    onChange={handleChange}
                                    disabled={!rtcmEnabled}
                                />
                                <label className="btn btn-outline-secondary" htmlFor="virtual_rtcm_ntrip_v2">NTRIP v2</label>
                            </div>
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Input Host / IP</label>
                            <input className="form-control" name="rtcmserver1" value={formState.rtcmserver1} onChange={handleChange} disabled={!rtcmEnabled} />
                        </div>
                        <div className="col-md-2">
                            <label className="form-label">Input Port</label>
                            <input className="form-control" name="rtcmport1" value={formState.rtcmport1} onChange={handleChange} disabled={!rtcmEnabled} />
                        </div>
                        <div className="col-md-4">
                            <label className="form-label">Input Mountpoint</label>
                            <input className="form-control" name="rtcmmountpoint1" value={formState.rtcmmountpoint1} onChange={handleChange} disabled={!rtcmEnabled} />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Input Username</label>
                            <input className="form-control" name="rtcmusername1" value={formState.rtcmusername1} onChange={handleChange} disabled={!rtcmEnabled} />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Input Password</label>
                            <input className="form-control" type="password" name="rtcmpassword1" value={formState.rtcmpassword1} onChange={handleChange} disabled={!rtcmEnabled} />
                        </div>
                    </div>
                </div>
            </div>

            {!isRelayShareMode && (
                <div className="alert alert-secondary small">
                    Mode Monitor Only đang bật: Server Output sẽ không được sử dụng, trạm chỉ nhận dữ liệu để hiển thị.
                </div>
            )}

            <div className="d-flex justify-content-end gap-2">
                <button type="button" className="btn btn-outline-secondary" onClick={onCancel} disabled={isCreatingVirtual}>Hủy</button>
                <button type="submit" className="btn btn-primary" disabled={isCreatingVirtual}>
                    {isCreatingVirtual ? 'Đang tạo...' : 'Tạo trạm ảo'}
                </button>
            </div>
        </form>
    );
});
window.VirtualStationFormPanel = VirtualStationFormPanel;

// ===============================================================================
// === FORM COMPONENTS
// ===============================================================================

const LicenseForm = memo(function LicenseForm({ device, onDeployLicense, disabled }) {
    const [licenseKey, setLicenseKey] = useState('');
    const [isGenerating, setIsGenerating] = useState(false);
    const [isDeploying, setIsDeploying] = useState(false);

    const handleGenerateKey = async () => {
        setIsGenerating(true);
        try {
            const response = await window.apiFetch('/api/license/pi', {
                method: 'POST',
                body: JSON.stringify({ serial: device.serial })
            });
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'Failed to generate license');
            }
            const data = await response.json();
            setLicenseKey(data.license_key);
            window.showSuccess('License key đã được tạo!');
        } catch (error) {
            window.showError('Không thể tạo key: ' + error.message);
        } finally {
            setIsGenerating(false);
        }
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (disabled) return;
        if (!licenseKey) return window.showError('Vui lòng tạo license key trước!');
        setIsDeploying(true);
        try {
            await onDeployLicense(device.serial, licenseKey);
        } finally {
            setIsDeploying(false);
        }
    };

    return (
        <form onSubmit={handleSubmit}>
            <div className="alert alert-warning small">
                <i className="bi bi-key-fill me-2"></i>
                Chức năng này dùng để cấp phép cho các trạm Pi đang ở trạng thái "CHỜ LICENSE".
            </div>
            <div className="mb-3">
                <label className="form-label">Serial Number của Pi</label>
                <div className="input-group">
                    <input type="text" className="form-control" value={device.serial} readOnly />
                    <button
                        className="btn btn-outline-secondary"
                        type="button"
                        title="Sao chép Serial"
                        onClick={() => {
                            navigator.clipboard.writeText(device.serial);
                            window.showSuccess('Đã sao chép Serial!');
                        }}
                    >
                        <i className="bi bi-clipboard"></i>
                    </button>
                </div>
            </div>
            <div className="mb-3">
                <label className="form-label">License Key</label>
                <div className="input-group">
                    <input type="text" className="form-control" value={licenseKey} readOnly placeholder="Nhấn 'Tạo Key' để tạo..." />
                    <button
                        className="btn btn-outline-secondary"
                        type="button"
                        title="Sao chép Key"
                        onClick={() => {
                            if (licenseKey) {
                                navigator.clipboard.writeText(licenseKey);
                                window.showSuccess('Đã sao chép Key!');
                            }
                        }}
                    >
                        <i className="bi bi-clipboard-check"></i>
                    </button>
                </div>
            </div>
            <button type="button" onClick={handleGenerateKey} className="btn btn-info w-100 mb-2" disabled={isGenerating}>
                {isGenerating ? (
                    <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang tạo...
                    </>
                ) : (
                    <>
                        <i className="bi bi-magic me-2"></i>
                        Tạo Key
                    </>
                )}
            </button>
            <button type="submit" className="btn btn-primary w-100" disabled={isDeploying || !licenseKey}>
                {isDeploying ? (
                    <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang gửi...
                    </>
                ) : (
                    <>
                        <i className="bi bi-send-fill me-2"></i>
                        Gửi Key đến Thiết bị
                    </>
                )}
            </button>
        </form>
    );
});

const ProvisionForm = memo(function ProvisionForm({ device, currentUser = {}, onProvision, disabled }) {
    const [stationName, setStationName] = useState(device.name);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const isAutoSetupStatus = (device?.status || '').startsWith('auto_setup');
    const autoProgress = device?.auto_base_progress || {};
    const autoSamplesCollected = Number(autoProgress.samples_collected || 0);
    const autoSamplesTarget = Number(autoProgress.samples_target || 0);
    const autoProgressPercent = autoSamplesTarget > 0
        ? Math.min(100, Math.round((autoSamplesCollected / autoSamplesTarget) * 100))
        : null;

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (disabled) return;
        if (!stationName.trim()) {
            return window.showError('Vui lòng nhập tên trạm!');
        }
        setIsSubmitting(true);
        try {
            await onProvision(device.serial, stationName);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="p-4 border rounded border-primary bg-dark-subtle">
            <h5 className="text-primary">
                <i className="bi bi-1-circle-fill me-2"></i>
                Bước 1: Kích hoạt trạm
            </h5>
            <p className="small text-muted">
                Vui lòng đặt tên cho trạm phát của bạn để tiếp tục. Tên này sẽ được hiển thị trên toàn hệ thống.
            </p>

            {/* Thông báo chế độ xem */}
            {disabled && (
                <div className="alert alert-info">
                    <i className="bi bi-eye-fill me-2"></i>
                    Chế độ xem: Bạn không có quyền kích hoạt trạm này.
                </div>
            )}

            <form onSubmit={handleSubmit}>
                {/* Fieldset để khóa tất cả input */}
                <fieldset disabled={disabled}>
                    <div className="mb-3">
                        <label htmlFor="station-name" className="form-label fw-bold">Tên trạm</label>
                        <input
                            type="text"
                            className="form-control form-control-lg"
                            id="station-name"
                            value={stationName}
                            onChange={(e) => setStationName(e.target.value)}
                            required
                        />
                    </div>

                    {/* Chỉ hiện nút submit khi có quyền */}
                    {!disabled && (
                        <button type="submit" className="btn btn-primary w-100 btn-lg" disabled={isSubmitting}>
                            {isSubmitting ? (
                                <>
                                    <span className="spinner-border spinner-border-sm me-2"></span>
                                    Đang kích hoạt...
                                </>
                            ) : (
                                <>
                                    <i className="bi bi-check-circle-fill me-2"></i>
                                    Lưu tên và Kích hoạt
                                </>
                            )}
                        </button>
                    )}
                </fieldset>
            </form>
        </div>
    );
});

const BaseConfigForm = memo(function BaseConfigForm({ device, currentUser = {}, onConfigureChip, disabled }) {
    const knownChips = ['Ublox', 'Unicorecomm'];
    const needsManualSelection = !knownChips.includes(device.detected_chip_type);

    const [manualChip, setManualChip] = useState(device.detected_chip_type === 'RTCM3_Source' ? 'Unicorecomm' : 'Ublox');
    const [method, setMethod] = useState('FIXED_LLA');

    const isAutoSetupStatus = (device?.status || '').startsWith('auto_setup');
    const autoProgress = device?.auto_base_progress || {};
    const autoSamplesCollected = Number(autoProgress.samples_collected || 0);
    const autoSamplesTarget = Number(autoProgress.samples_target || 0);
    const autoProgressPercent = autoSamplesTarget > 0
        ? Math.min(100, Math.round((autoSamplesCollected / autoSamplesTarget) * 100))
        : null;

    // Tọa độ Fixed
    const [latitude, setLatitude] = useState('');
    const [longitude, setLongitude] = useState('');
    const [altitude, setAltitude] = useState('');
    const [fixedAccuracyMm, setFixedAccuracyMm] = useState('10'); // Đơn vị mm cho Fixed

    // Survey-In Params
    const [svinDuration, setSvinDuration] = useState('300'); // Giây
    const [svinAccuracyM, setSvinAccuracyM] = useState('3.00'); // Đơn vị Mét cho Survey-In (Mặc định 3m)

    const [isSubmitting, setIsSubmitting] = useState(false);

    useEffect(() => {
        // 1. Reset về giá trị mặc định khi đổi thiết bị
        setManualChip(device.detected_chip_type === 'RTCM3_Source' ? 'Unicorecomm' : 'Ublox');
        setMethod('FIXED_LLA');
        setLatitude('');
        setLongitude('');
        setAltitude('');
        setFixedAccuracyMm('10');
        setSvinDuration('300');
        setSvinAccuracyM('3.00');

        // 2. Load config từ DB (nếu có)
        if (device.base_config) {
            const config = device.base_config;

            // Set Method
            if (config.base_setup_method) {
                setMethod(config.base_setup_method);
            }

            // Load Fixed Params
            const coords = config.coords || (
                (config.lat !== undefined && config.lon !== undefined)
                    ? { lat: config.lat, lon: config.lon, alt: config.alt }
                    : null
            );
            if (coords) {
                setLatitude(coords.lat ?? '');
                setLongitude(coords.lon ?? '');
                setAltitude(coords.alt ?? '');
            }
            // Load Accuracy cho Fixed (Lưu trong DB là mét -> Đổi ra mm để hiển thị)
            if (config.accuracy !== undefined) {
                setFixedAccuracyMm(String(Math.round(config.accuracy * 1000)));
            }

            // Load Survey-In Params
            if (config.survey_in_duration !== undefined) {
                setSvinDuration(String(config.survey_in_duration));
            }
            // Load Accuracy cho Survey-In (Lưu trong DB là mét -> Giữ nguyên là mét)
            if (config.survey_in_accuracy !== undefined) {
                setSvinAccuracyM(String(config.survey_in_accuracy));
            }
        }
    }, [device.serial]);

    const handleDecimalInputChange = (setter) => (e) => {
        const value = e.target.value;
        // Cho phép nhập số thập phân âm/dương
        if (/^-?[0-9]*\.?[0-9]*$/.test(value)) {
            setter(value);
        }
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (disabled) return;
        setIsSubmitting(true);

        try {
            const finalChipType = needsManualSelection
                ? manualChip
                : device.detected_chip_type;

            let params = { base_setup_method: method };

            if (method === 'FIXED_LLA') {
                params.coords = {
                    lat: String(latitude).trim(),
                    lon: String(longitude).trim(),
                    alt: String(altitude).trim()
                };
                // Fixed: Input là mm -> Chia 1000 để thành m gửi cho backend
                params.accuracy = parseFloat(fixedAccuracyMm) / 1000.0;
            } else if (method === 'SURVEY_IN') {
                params.survey_in_duration = parseInt(svinDuration);
                // Survey-In: Input là m -> Gửi trực tiếp m cho backend (Backend sẽ tự xử lý x10000 nếu cần cho Ublox)
                params.survey_in_accuracy = parseFloat(svinAccuracyM);
            } else if (method === 'AUTO_CORS') {
                // Auto CORS uses backend config directly, no frontend parameters needed beyond the method
            }

            const payload = {
                sensor_type: finalChipType,
                mode: 'BASE',
                params: params
            };

            await onConfigureChip(device.serial, payload);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <form onSubmit={handleSubmit}>
            <div className="alert alert-info small">
                <i className="bi bi-info-circle me-2"></i>
                Đây là bước cấu hình tọa độ gốc cho trạm phát. Các thay đổi sẽ reset chip GNSS.
            </div>

            {disabled && (
                <div className="alert alert-warning py-2 mb-3">
                    <i className="bi bi-eye-fill me-2"></i>
                    Chế độ xem: Không thể chỉnh sửa cấu hình GNSS.
                </div>
            )}

            {needsManualSelection && (
                <div className="mb-3 p-3 border border-warning rounded">
                    <div className="alert alert-warning small p-2">
                        <i className="bi bi-exclamation-triangle-fill me-2"></i>
                        {device.detected_chip_type === 'RTCM3_Source' ? (
                            <span>Chip nhận dạng là <strong>RTCM3_Source</strong> (thường là chip Unicorecomm/UM982 trả về mã chung). Vui lòng xác nhận loại chip để cấu hình chính xác.</span>
                        ) : (
                            <span>Không thể nhận diện loại chip (hiện tại là: <strong>{device.detected_chip_type}</strong>). Vui lòng chọn thủ công.</span>
                        )}
                    </div>
                    <label className="form-label fw-bold">Xác nhận Loại Chip</label>
                    <select
                        className="form-select"
                        value={manualChip}
                        onChange={(e) => setManualChip(e.target.value)}
                        disabled={disabled}
                    >
                        <option value="Ublox">Ublox</option>
                        <option value="Unicorecomm">Unicorecomm (Mặc định cho UM982 / RTCM3)</option>
                    </select>
                </div>
            )}

            <div className="mb-3">
                <label className="form-label fw-bold text-primary">Phương pháp xác định vị trí</label>
                <select
                    className="form-select border-primary"
                    value={method}
                    onChange={(e) => setMethod(e.target.value)}
                    disabled={disabled}
                >
                    <option value="AUTO_CORS">Tự động cấu hình Base từ CORS liên kết</option>
                    <option value="FIXED_LLA">Nhập tọa độ thủ công (Fixed Mode - Độ chính xác cao)</option>
                    <option value="SURVEY_IN">Tự động khảo sát (Survey-In - Độ chính xác thấp)</option>
                </select>
            </div>

            <fieldset disabled={disabled}>
                {method === 'AUTO_CORS' ? (
                    <div className="row g-3">
                        {isAutoSetupStatus && (
                            <div className="col-12">
                                <div className="alert alert-info small mb-2">
                                    <div className="d-flex justify-content-between align-items-center">
                                        <div>
                                            <i className="bi bi-hourglass-split me-2"></i>
                                            <strong>Đang tự động cấu hình:</strong> {STATUS_TEXT_MAP[device.status] || device.status}
                                        </div>
                                        {autoProgressPercent !== null && (
                                            <span className="badge bg-primary">{autoProgressPercent}%</span>
                                        )}
                                    </div>
                                    {autoSamplesTarget > 0 && (
                                        <div className="mt-2">
                                            <div className="progress" style={{ height: '8px' }}>
                                                <div
                                                    className="progress-bar progress-bar-striped progress-bar-animated"
                                                    style={{ width: `${autoProgressPercent || 0}%` }}
                                                ></div>
                                            </div>
                                            <div className="mt-1 text-muted">
                                                Mẫu tọa độ: {autoSamplesCollected}/{autoSamplesTarget}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                        <div className="col-12">
                            <div className="alert alert-success small mb-2">
                                <i className="bi bi-robot me-2"></i>
                                {latitude && longitude ? (
                                    <span>Hệ thống đã tự động cấu hình tọa độ cho trạm từ CORS liên kết thành công!</span>
                                ) : (
                                    <span>Hệ thống sẽ tự động ghép nối với máy chủ CORS và tính trung bình tọa độ để thiết lập trạm phát Base hoàn toàn tự động!</span>
                                )}
                            </div>
                        </div>
                        {latitude && longitude && (
                            <>
                                <div className="col-md-4">
                                    <label className="form-label small">Lat (Tự động)</label>
                                    <input type="text" className="form-control form-control-sm bg-dark-subtle" value={latitude} readOnly />
                                </div>
                                <div className="col-md-4">
                                    <label className="form-label small">Lon (Tự động)</label>
                                    <input type="text" className="form-control form-control-sm bg-dark-subtle" value={longitude} readOnly />
                                </div>
                                <div className="col-md-4">
                                    <label className="form-label small">Alt (Tự động)</label>
                                    <input type="text" className="form-control form-control-sm bg-dark-subtle" value={altitude} readOnly />
                                </div>
                            </>
                        )}
                    </div>
                ) : method === 'FIXED_LLA' ? (
                    <div className="row g-3">
                        <div className="col-12 text-muted small fst-italic mb-2">
                            * Chế độ này yêu cầu bạn đã có tọa độ chính xác (mốc địa chính).
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Latitude (Vĩ độ)</label>
                            <input
                                type="text" inputMode="decimal" className="form-control"
                                value={latitude} onChange={handleDecimalInputChange(setLatitude)}
                                required placeholder="VD: 21.028511"
                            />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Longitude (Kinh độ)</label>
                            <input
                                type="text" inputMode="decimal" className="form-control"
                                value={longitude} onChange={handleDecimalInputChange(setLongitude)}
                                required placeholder="VD: 105.854167"
                            />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Altitude (Độ cao - mét)</label>
                            <input
                                type="text" inputMode="decimal" className="form-control"
                                value={altitude} onChange={handleDecimalInputChange(setAltitude)}
                                required placeholder="VD: 15.5"
                            />
                        </div>
                        <div className="col-md-6">
                            <label className="form-label fw-bold">Giới hạn sai số (mm)</label>
                            <input
                                type="number" className="form-control"
                                value={fixedAccuracyMm} onChange={(e) => setFixedAccuracyMm(e.target.value)}
                                required placeholder="VD: 10"
                            />
                            <div className="form-text">Đơn vị: Milimet (Thường để 5mm - 20mm)</div>
                        </div>
                    </div>
                ) : (
                    <div className="row g-3">
                        <div className="col-12 text-muted small fst-italic mb-2">
                            * Chế độ này thiết bị sẽ tự đo trung bình cộng vị trí. Phù hợp thiết lập trạm tạm thời.
                        </div>
                        <div className="col-md-6">
                            <label className="form-label">Thời gian khảo sát (giây)</label>
                            <input
                                type="number" className="form-control"
                                value={svinDuration} onChange={(e) => setSvinDuration(e.target.value)}
                                required
                            />
                            <div className="form-text">Tối thiểu: 60s. Khuyên dùng: &gt; 300s.</div>
                        </div>
                        <div className="col-md-6">
                            <label className="form-label fw-bold">Giới hạn sai số (m)</label>
                            <input
                                type="number" step="0.01" className="form-control"
                                value={svinAccuracyM} onChange={(e) => setSvinAccuracyM(e.target.value)}
                                required placeholder="VD: 3.0"
                            />
                            <div className="form-text text-danger">Đơn vị: MÉT (Ví dụ nhập 3.0 là 3 mét)</div>
                        </div>
                    </div>
                )}

                {!disabled && (
                    <button type="submit" className="btn btn-primary mt-4 w-100" disabled={isSubmitting}>
                        {isSubmitting ? (
                            <>
                                <span className="spinner-border spinner-border-sm me-2"></span>
                                Đang gửi...
                            </>
                        ) : (
                            <>
                                <i className="bi bi-lightning-charge-fill me-2"></i>
                                Áp dụng cấu hình BASE
                            </>
                        )}
                    </button>
                )}
            </fieldset>
        </form>
    );
});

const ServiceConfigForm = memo(function ServiceConfigForm({ device, currentUser = {}, onConfigureService, disabled }) {
    const [formState, setFormState] = useState({
        ncomport: '',
        reconnectioninterval: 10,
        stream_on_demand: true,
        stream_active: false,
        server1_enabled: false,
        server1_stream_on_demand: true,
        server1_stream_active: false,
        ntrip_version1: 1,
        serverhost1: '',
        port1: '',
        mountpoint1: '',
        password1: '',
        server2_enabled: false,
        server2_stream_on_demand: true,
        server2_stream_active: false,
        ntrip_version2: 1,
        serverhost2: '',
        port2: '',
        mountpoint2: '',
        password2: '',
        rtcm_enabled: false,
        rtcmntripversion1: 1,
        rtcmserver1: '',
        rtcmport1: '',
        rtcmmountpoint1: '',
        rtcmusername1: '',
        rtcmpassword1: ''
    });
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [isFormDirty, setIsFormDirty] = useState(false);
    const lastHydratedSerialRef = useRef(null);
    const canManageStationMode =
        currentUser?.role === 'admin' ||
        currentUser?.permissions?.includes('manage:station_mode') ||
        currentUser?.permissions?.includes('MANAGE_STATION_MODE');

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

    // Common section should only show stream toggles for servers that are currently enabled.
    const showS1StreamToggle = toBool(formState.server1_enabled, false);
    const showS2StreamToggle = toBool(formState.server2_enabled, false);

    useEffect(() => {
        const isSerialChanged = lastHydratedSerialRef.current !== device.serial;

        // Skip realtime re-hydration while user is editing current device form.
        if (!isSerialChanged && isFormDirty) {
            return;
        }

        // Reset về trạng thái mặc định
        const defaultState = {
            ncomport: '',
            reconnectioninterval: 10,
            stream_on_demand: true,
            stream_active: false,
            server1_enabled: false,
            server1_stream_on_demand: true,
            server1_stream_active: false,
            ntrip_version1: 1,
            username1: '',
            serverhost1: '',
            port1: '',
            mountpoint1: '',
            password1: '',
            server2_enabled: false,
            server2_stream_on_demand: true,
            server2_stream_active: false,
            ntrip_version2: 1,
            username2: '',
            serverhost2: '',
            port2: '',
            mountpoint2: '',
            password2: '',
            rtcm_enabled: false,
            rtcmntripversion1: 1,
            rtcmserver1: '',
            rtcmport1: '',
            rtcmmountpoint1: '',
            rtcmusername1: '',
            rtcmpassword1: ''
        };

        if (device.service_config) {
            const merged = {
                ...defaultState,
                ...device.service_config
            };

            // Backward compatibility: when per-server flags are missing, inherit from global mode.
            if (merged.server1_stream_on_demand === null || merged.server1_stream_on_demand === undefined) {
                merged.server1_stream_on_demand = !!merged.stream_on_demand;
            }
            if (merged.server2_stream_on_demand === null || merged.server2_stream_on_demand === undefined) {
                merged.server2_stream_on_demand = !!merged.stream_on_demand;
            }
            if (merged.server1_stream_active === null || merged.server1_stream_active === undefined) {
                merged.server1_stream_active = !!merged.stream_active;
            }
            if (merged.server2_stream_active === null || merged.server2_stream_active === undefined) {
                merged.server2_stream_active = !!merged.stream_active;
            }

            if (!merged.server1_stream_on_demand) {
                merged.server1_stream_active = true;
            }
            if (!merged.server2_stream_on_demand) {
                merged.server2_stream_active = true;
            }

            setFormState(merged);
            setIsFormDirty(false);
            lastHydratedSerialRef.current = device.serial;
        } else {
            setFormState(defaultState);
            setIsFormDirty(false);
            lastHydratedSerialRef.current = device.serial;
        }
    }, [device.serial, device.service_config, isFormDirty]);

    const handleChange = (e) => {
        const { name, value, type, checked } = e.target;
        const inputValue = type === 'checkbox' ? checked : value;
        setIsFormDirty(true);

        setFormState(prev => {
            const next = {
                ...prev,
                [name]: inputValue
            };

            if (name === 'stream_on_demand') {
                next.server1_stream_on_demand = !!checked;
                next.server2_stream_on_demand = !!checked;
                next.stream_active = !checked;
                next.server1_stream_active = !checked;
                next.server2_stream_active = !checked;
            }

            if (name === 'server1_stream_on_demand') {
                next.server1_stream_active = !checked;
            }
            if (name === 'server2_stream_on_demand') {
                next.server2_stream_active = !checked;
            }

            return next;
        });
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (disabled) return;
        setIsSubmitting(true);

        try {
            const s1Enabled = toBool(formState.server1_enabled, false);
            const s2Enabled = toBool(formState.server2_enabled, false);

            const s1CanPush = !formState.server1_stream_on_demand || !!formState.server1_stream_active;
            const s2CanPush = !formState.server2_stream_on_demand || !!formState.server2_stream_active;

            // Derive global compatibility flags from enabled servers only.
            const derivedGlobalOnDemand =
                (s1Enabled && !!formState.server1_stream_on_demand)
                ||
                (s2Enabled && !!formState.server2_stream_on_demand);

            const derivedGlobalActive =
                (s1Enabled && s1CanPush)
                ||
                (s2Enabled && s2CanPush);

            const payload = {
                ...formState,
                stream_on_demand: derivedGlobalOnDemand,
                stream_active: derivedGlobalActive,
                ntrip_version1: parseInt(formState.ntrip_version1, 10),
                ntrip_version2: parseInt(formState.ntrip_version2, 10),
                port1: formState.port1 ? parseInt(formState.port1, 10) : null,
                port2: formState.port2 ? parseInt(formState.port2, 10) : null,
                rtcmntripversion1: parseInt(formState.rtcmntripversion1, 10) === 2 ? 2 : 1,
                rtcmport1: formState.rtcmport1 ? parseInt(formState.rtcmport1, 10) : null,
                reconnectioninterval: parseInt(formState.reconnectioninterval, 10)
            };

            await onConfigureService(device.serial, payload);
            setIsFormDirty(false);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <form onSubmit={handleSubmit}>
            <div className="alert alert-info small">
                <i className="bi bi-info-circle-fill me-2"></i>
                Cấu hình các dịch vụ đầu ra (NTRIP Caster, RTCM, ...). Các thay đổi sẽ được áp dụng sau khi dịch vụ trên Pi được khởi động lại.
            </div>

            {/* 🔥 Thông báo chế độ xem */}
            {disabled && (
                <div className="alert alert-warning py-2 mb-3">
                    <i className="bi bi-eye-fill me-2"></i>
                    Chế độ xem: Không thể chỉnh sửa cấu hình dịch vụ.
                </div>
            )}

            <div className="mb-4 p-3 border rounded border-secondary">
                <h5 className="mb-3">Cài đặt chung</h5>
                <div className="mb-3 p-2 rounded bg-dark border border-secondary-subtle">
                    <div className="d-flex align-items-center justify-content-between mb-1">
                        <span className="text-muted small">Phiên bản Agent:</span>
                        <span className="fw-bold small text-light">{device.version || 'Không xác định'}</span>
                    </div>
                    <div className="d-flex align-items-center justify-content-between">
                        <span className="text-muted small">Trạng thái đồng bộ:</span>
                        {device.version === window.APP_VERSION ? (
                            <span className="badge bg-success-subtle text-success border border-success-subtle">
                                <i className="bi bi-check-circle-fill me-1"></i>ĐÃ ĐỒNG BỘ (OK)
                            </span>
                        ) : (
                            <span className="badge bg-warning-subtle text-warning border border-warning-subtle">
                                <i className="bi bi-exclamation-triangle-fill me-1"></i>KHÔNG ĐỒNG BỘ
                            </span>
                        )}
                    </div>
                </div>
                <fieldset disabled={disabled}>
                    <div className="row g-3">
                        <div className="col-md-6">
                            <label htmlFor="ncomport" className="form-label">Định danh Trạm (ID)</label>
                            <input
                                type="text"
                                className="form-control"
                                id="ncomport"
                                name="ncomport"
                                value={formState.ncomport}
                                onChange={handleChange}
                                placeholder="Ví dụ: TRAM_HN_01"
                            />
                        </div>
                        <div className="col-md-6">
                            <label htmlFor="reconnectioninterval" className="form-label">Kết nối lại (giây)</label>
                            <input
                                type="number"
                                className="form-control"
                                id="reconnectioninterval"
                                name="reconnectioninterval"
                                value={formState.reconnectioninterval}
                                onChange={handleChange}
                            />
                        </div>
                        {showS1StreamToggle && (
                            <div className="col-md-6">
                                <label className="form-label">Server 1: Chế độ stream</label>
                                {canManageStationMode ? (
                                    <div className="form-check form-switch pt-2">
                                        <input
                                            className="form-check-input"
                                            type="checkbox"
                                            role="switch"
                                            id="server1_stream_on_demand"
                                            name="server1_stream_on_demand"
                                            checked={!!formState.server1_stream_on_demand}
                                            onChange={handleChange}
                                        />
                                        <label className="form-check-label" htmlFor="server1_stream_on_demand">
                                            {formState.server1_stream_on_demand ? "Ngủ (On-Demand)" : "Luôn mở (Always-On)"}
                                        </label>
                                    </div>
                                ) : (
                                    <div className="pt-2">
                                        <span className={`badge ${formState.server1_stream_on_demand ? 'bg-info' : 'bg-success'}`}>
                                            <i className={`bi ${formState.server1_stream_on_demand ? 'bi-moon-stars-fill' : 'bi-broadcast-pin'} me-1`}></i>
                                            {formState.server1_stream_on_demand ? "S1: Ngủ" : "S1: Luôn mở"}
                                        </span>
                                    </div>
                                )}
                            </div>
                        )}

                        {showS2StreamToggle && (
                            <div className="col-md-6">
                                <label className="form-label">Server 2: Chế độ stream</label>
                                {canManageStationMode ? (
                                    <div className="form-check form-switch pt-2">
                                        <input
                                            className="form-check-input"
                                            type="checkbox"
                                            role="switch"
                                            id="server2_stream_on_demand"
                                            name="server2_stream_on_demand"
                                            checked={!!formState.server2_stream_on_demand}
                                            onChange={handleChange}
                                        />
                                        <label className="form-check-label" htmlFor="server2_stream_on_demand">
                                            {formState.server2_stream_on_demand ? "Ngủ (On-Demand)" : "Luôn mở (Always-On)"}
                                        </label>
                                    </div>
                                ) : (
                                    <div className="pt-2">
                                        <span className={`badge ${formState.server2_stream_on_demand ? 'bg-info' : 'bg-success'}`}>
                                            <i className={`bi ${formState.server2_stream_on_demand ? 'bi-moon-stars-fill' : 'bi-broadcast-pin'} me-1`}></i>
                                            {formState.server2_stream_on_demand ? "S2: Ngủ" : "S2: Luôn mở"}
                                        </span>
                                    </div>
                                )}
                            </div>
                        )}

                    </div>
                </fieldset>
            </div>

            <div className="accordion" id="service-config-accordion">
                {/* ==================== SERVER 1 ==================== */}
                <div className="accordion-item">
                    <h2 className="accordion-header">
                        <button
                            className="accordion-button"
                            type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#collapseOne"
                        >
                            <i className="bi bi-box-arrow-up me-2 text-primary"></i>
                            NTRIP Caster Server 1
                        </button>
                    </h2>
                    <div id="collapseOne" className="accordion-collapse collapse show" data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
                            <fieldset disabled={disabled}>
                                {/* Enable Switch */}
                                <div className="form-check form-switch form-switch-lg mb-3">
                                    <input
                                        className="form-check-input"
                                        type="checkbox"
                                        role="switch"
                                        id="server1_enabled"
                                        name="server1_enabled"
                                        checked={formState.server1_enabled}
                                        onChange={handleChange}
                                    />
                                    <label className="form-check-label" htmlFor="server1_enabled">Kích hoạt Server 1</label>
                                </div>

                                {/* NTRIP Version Selection */}
                                <div className="mb-3 p-3 bg-dark rounded border border-secondary">
                                    <label className="form-label fw-bold text-info">
                                        <i className="bi bi-gear-fill me-2"></i>NTRIP Protocol Version
                                    </label>
                                    <div className="d-flex gap-4">
                                        <div className="form-check">
                                            <input
                                                className="form-check-input"
                                                type="radio"
                                                name="ntrip_version1"
                                                id="ntrip_v1_1"
                                                value="1"
                                                checked={parseInt(formState.ntrip_version1) === 1}
                                                onChange={handleChange}
                                            />
                                            <label className="form-check-label" htmlFor="ntrip_v1_1">
                                                <strong>Version 1.0</strong>
                                                <br />
                                                <small className="text-muted">SOURCE command (Legacy)</small>
                                            </label>
                                        </div>
                                        <div className="form-check">
                                            <input
                                                className="form-check-input"
                                                type="radio"
                                                name="ntrip_version1"
                                                id="ntrip_v1_2"
                                                value="2"
                                                checked={parseInt(formState.ntrip_version1) === 2}
                                                onChange={handleChange}
                                            />
                                            <label className="form-check-label" htmlFor="ntrip_v1_2">
                                                <strong>Version 2.0</strong>
                                                <br />
                                                <small className="text-muted">HTTP POST (Modern)</small>
                                            </label>
                                        </div>
                                    </div>
                                    <div className="alert alert-secondary small mt-2 mb-0">
                                        <i className="bi bi-info-circle me-1"></i>
                                        <strong>Ghi chú:</strong> Nếu caster từ chối kết nối với v2.0, hãy thử lại với v1.0
                                    </div>
                                </div>

                                {/* Connection Details */}
                                <div className="row g-3">
                                    <div className="col-md-8">
                                        <label className="form-label">Host / IP</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="serverhost1"
                                            value={formState.serverhost1}
                                            onChange={handleChange}
                                            placeholder="Ví dụ: rtk2go.com"
                                        />
                                    </div>
                                    <div className="col-md-4">
                                        <label className="form-label">Port</label>
                                        <input
                                            type="number"
                                            className="form-control"
                                            name="port1"
                                            value={formState.port1}
                                            onChange={handleChange}
                                            placeholder="2101"
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label className="form-label">
                                            Username
                                            <small className="text-muted ms-1">(NTRIP Source Account)</small>
                                        </label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="username1"
                                            value={formState.username1}
                                            onChange={handleChange}
                                            placeholder="source hoặc username riêng"
                                        />
                                        <small className="text-muted">
                                            Mặc định: "source" (NTRIP v1). Caster hiện đại có thể yêu cầu username riêng.
                                        </small>
                                    </div>
                                    <div className="col-md-6">
                                        <label className="form-label">Password</label>
                                        <input
                                            type="password"
                                            className="form-control"
                                            name="password1"
                                            value={formState.password1}
                                            onChange={handleChange}
                                            placeholder="••••••••"
                                        />
                                    </div>
                                    <div className="col-12">
                                        <label className="form-label">Mountpoint</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="mountpoint1"
                                            value={formState.mountpoint1}
                                            onChange={handleChange}
                                            placeholder="MY_BASE_01"
                                        />
                                    </div>
                                </div>
                            </fieldset>
                        </div>
                    </div>
                </div>

                {/* ==================== SERVER 2 ==================== */}
                <div className="accordion-item">
                    <h2 className="accordion-header">
                        <button
                            className="accordion-button collapsed"
                            type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#collapseTwo"
                        >
                            <i className="bi bi-box-arrow-up me-2 text-info"></i>
                            NTRIP Caster Server 2
                        </button>
                    </h2>
                    <div id="collapseTwo" className="accordion-collapse collapse" data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
                            <fieldset disabled={disabled}>
                                {/* Enable Switch */}
                                <div className="form-check form-switch form-switch-lg mb-3">
                                    <input
                                        className="form-check-input"
                                        type="checkbox"
                                        role="switch"
                                        id="server2_enabled"
                                        name="server2_enabled"
                                        checked={formState.server2_enabled}
                                        onChange={handleChange}
                                    />
                                    <label className="form-check-label" htmlFor="server2_enabled">Kích hoạt Server 2</label>
                                </div>

                                {/* NTRIP Version Selection */}
                                <div className="mb-3 p-3 bg-dark rounded border border-secondary">
                                    <label className="form-label fw-bold text-info">
                                        <i className="bi bi-gear-fill me-2"></i>NTRIP Protocol Version
                                    </label>
                                    <div className="d-flex gap-4">
                                        <div className="form-check">
                                            <input
                                                className="form-check-input"
                                                type="radio"
                                                name="ntrip_version2"
                                                id="ntrip_v2_1"
                                                value="1"
                                                checked={parseInt(formState.ntrip_version2) === 1}
                                                onChange={handleChange}
                                            />
                                            <label className="form-check-label" htmlFor="ntrip_v2_1">
                                                <strong>Version 1.0</strong>
                                                <br />
                                                <small className="text-muted">SOURCE command (Legacy)</small>
                                            </label>
                                        </div>
                                        <div className="form-check">
                                            <input
                                                className="form-check-input"
                                                type="radio"
                                                name="ntrip_version2"
                                                id="ntrip_v2_2"
                                                value="2"
                                                checked={parseInt(formState.ntrip_version2) === 2}
                                                onChange={handleChange}
                                            />
                                            <label className="form-check-label" htmlFor="ntrip_v2_2">
                                                <strong>Version 2.0</strong>
                                                <br />
                                                <small className="text-muted">HTTP POST (Modern)</small>
                                            </label>
                                        </div>
                                    </div>
                                    <div className="alert alert-secondary small mt-2 mb-0">
                                        <i className="bi bi-info-circle me-1"></i>
                                        <strong>Ghi chú:</strong> Nếu caster từ chối kết nối với v2.0, hãy thử lại với v1.0
                                    </div>
                                </div>

                                {/* Connection Details */}
                                <div className="row g-3">
                                    <div className="col-md-8">
                                        <label className="form-label">Host / IP</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="serverhost2"
                                            value={formState.serverhost2}
                                            onChange={handleChange}
                                            placeholder="Ví dụ: backup.caster.com"
                                        />
                                    </div>
                                    <div className="col-md-4">
                                        <label className="form-label">Port</label>
                                        <input
                                            type="number"
                                            className="form-control"
                                            name="port2"
                                            value={formState.port2}
                                            onChange={handleChange}
                                            placeholder="2101"
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label className="form-label">
                                            Username
                                            <small className="text-muted ms-1">(NTRIP Source Account)</small>
                                        </label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="username2"
                                            value={formState.username2}
                                            onChange={handleChange}
                                            placeholder="source hoặc username riêng"
                                        />
                                        <small className="text-muted">
                                            Mặc định: "source" (NTRIP v1). Caster hiện đại có thể yêu cầu username riêng.
                                        </small>
                                    </div>
                                    <div className="col-md-6">
                                        <label className="form-label">Password</label>
                                        <input
                                            type="password"
                                            className="form-control"
                                            name="password2"
                                            value={formState.password2}
                                            onChange={handleChange}
                                            placeholder="••••••••"
                                        />
                                    </div>
                                    <div className="col-12">
                                        <label className="form-label">Mountpoint</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="mountpoint2"
                                            value={formState.mountpoint2}
                                            onChange={handleChange}
                                            placeholder="MY_BASE_02"
                                        />
                                    </div>
                                </div>
                            </fieldset>
                        </div>
                    </div>
                </div>

                {/* ==================== RTCM CLIENT ==================== */}
                <div className="accordion-item">
                    <h2 className="accordion-header">
                        <button
                            className="accordion-button collapsed"
                            type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#collapseThree"
                        >
                            <i className="bi bi-box-arrow-in-down me-2 text-success"></i>
                            RTCM Client (Nhận tín hiệu hiệu chỉnh)
                        </button>
                    </h2>
                    <div id="collapseThree" className="accordion-collapse collapse" data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
                            <fieldset disabled={disabled}>
                                {/* Enable Switch */}
                                <div className="form-check form-switch form-switch-lg mb-3">
                                    <input
                                        className="form-check-input"
                                        type="checkbox"
                                        role="switch"
                                        id="rtcm_enabled"
                                        name="rtcm_enabled"
                                        checked={formState.rtcm_enabled}
                                        onChange={handleChange}
                                    />
                                    <label className="form-check-label" htmlFor="rtcm_enabled">Kích hoạt nhận RTCM</label>
                                </div>

                                <div className="alert alert-warning small mb-3">
                                    <i className="bi bi-exclamation-triangle-fill me-2"></i>
                                    <strong>Lưu ý:</strong> Chức năng này dành cho trạm Rover nhận tín hiệu hiệu chỉnh từ Base Station.
                                    Không bật nếu thiết bị đang được cấu hình làm Base Station.
                                </div>

                                <div className="mb-3">
                                    <label className="form-label d-block">NTRIP Version</label>
                                    <div className="btn-group" role="group" aria-label="RTCM NTRIP Version">
                                        <input
                                            type="radio"
                                            className="btn-check"
                                            id="rtcmntripversion1_v1"
                                            name="rtcmntripversion1"
                                            value="1"
                                            checked={parseInt(formState.rtcmntripversion1, 10) === 1}
                                            onChange={handleChange}
                                        />
                                        <label className="btn btn-outline-secondary" htmlFor="rtcmntripversion1_v1">NTRIP v1</label>
                                        <input
                                            type="radio"
                                            className="btn-check"
                                            id="rtcmntripversion1_v2"
                                            name="rtcmntripversion1"
                                            value="2"
                                            checked={parseInt(formState.rtcmntripversion1, 10) === 2}
                                            onChange={handleChange}
                                        />
                                        <label className="btn btn-outline-secondary" htmlFor="rtcmntripversion1_v2">NTRIP v2</label>
                                    </div>
                                </div>

                                {/* Connection Details */}
                                <div className="row g-3">
                                    <div className="col-md-8">
                                        <label className="form-label">Caster Host / IP</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="rtcmserver1"
                                            value={formState.rtcmserver1}
                                            onChange={handleChange}
                                            placeholder="Ví dụ: rtk2go.com"
                                        />
                                    </div>
                                    <div className="col-md-4">
                                        <label className="form-label">Caster Port</label>
                                        <input
                                            type="number"
                                            className="form-control"
                                            name="rtcmport1"
                                            value={formState.rtcmport1}
                                            onChange={handleChange}
                                            placeholder="2101"
                                        />
                                    </div>
                                    <div className="col-12">
                                        <label className="form-label">Mountpoint</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="rtcmmountpoint1"
                                            value={formState.rtcmmountpoint1}
                                            onChange={handleChange}
                                            placeholder="BASE_STATION_MP"
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label className="form-label">Username</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="rtcmusername1"
                                            value={formState.rtcmusername1}
                                            onChange={handleChange}
                                            placeholder="username"
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label className="form-label">Password</label>
                                        <input
                                            type="password"
                                            className="form-control"
                                            name="rtcmpassword1"
                                            value={formState.rtcmpassword1}
                                            onChange={handleChange}
                                            placeholder="••••••••"
                                        />
                                    </div>
                                </div>
                            </fieldset>
                        </div>
                    </div>
                </div>
            </div>

            {/* 🔥 Chỉ hiện nút submit khi có quyền */}
            {!disabled && (
                <button type="submit" className="btn btn-success mt-4 w-100 btn-lg" disabled={isSubmitting}>
                    {isSubmitting ? (
                        <>
                            <span className="spinner-border spinner-border-sm me-2"></span>
                            Đang lưu...
                        </>
                    ) : (
                        <>
                            <i className="bi bi-save-fill me-2"></i>
                            Lưu và Áp dụng Cấu hình
                        </>
                    )}
                </button>
            )}
        </form>
    );
});

// === MAP COMPONENT ===
// ==================== MAP COMPONENT (FIXED HTTPS) ====================
const BaseLocationMap = memo(function BaseLocationMap({ coords }) {
    const mapContainerRef = useRef(null);
    const mapInstanceRef = useRef(null);
    const markerRef = useRef(null);
    const [mapType, setMapType] = useState('m'); // 'y' = Hybrid, 'm' = Roadmap
    const tileLayerRef = useRef(null);

    useEffect(() => {
        // Kiểm tra tọa độ đầu vào
        if (!coords || !coords.lat || !coords.lon) {
            console.warn('[Map] No coords provided');
            return;
        }
        const lat = parseFloat(coords.lat);
        const lon = parseFloat(coords.lon);

        if (isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) return;

        // Kiểm tra Leaflet đã load chưa
        if (typeof L === 'undefined') {
            console.error('Leaflet chưa được load!');
            return;
        }

        // Khởi tạo Map
        if (!mapInstanceRef.current) {
            mapInstanceRef.current = L.map(mapContainerRef.current).setView([lat, lon], 16);

            tileLayerRef.current = L.tileLayer(`https://mt1.google.com/vt/lyrs=${mapType}&x={x}&y={y}&z={z}`, {
                maxZoom: 20,
                attribution: 'Google Maps'
            }).addTo(mapInstanceRef.current);

            markerRef.current = L.marker([lat, lon]).addTo(mapInstanceRef.current)
                .bindPopup(`<b>Vị trí Base</b><br>Lat: ${lat}<br>Lon: ${lon}`)
                .openPopup();
        } else {
            mapInstanceRef.current.setView([lat, lon], 16);
            if (markerRef.current) markerRef.current.setLatLng([lat, lon]);
        }
        
        // Cập nhật lớp bản đồ nếu thay đổi
        if (tileLayerRef.current) {
            tileLayerRef.current.setUrl(`https://mt1.google.com/vt/lyrs=${mapType}&x={x}&y={y}&z={z}`);
        }

        setTimeout(() => {
            if (mapInstanceRef.current) {
                mapInstanceRef.current.invalidateSize();
            }
        }, 300);

    }, [coords?.lat, coords?.lon, mapType]);

    // Handle Resize
    useEffect(() => {
        const handleResize = () => {
            if (mapInstanceRef.current) mapInstanceRef.current.invalidateSize();
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Hiển thị placeholder nếu chưa có tọa độ
    if (!coords || !coords.lat || (parseFloat(coords.lat) === 0 && parseFloat(coords.lon) === 0)) {
        console.warn('[Map] No valid coords - showing placeholder');
        return (
            <div className="d-flex align-items-center justify-content-center p-4 text-danger border rounded bg-dark"
                style={{ height: '250px', border: '2px solid red' }}>
                <div className="text-center">
                    <i className="bi bi-geo-alt-slash display-4 mb-2"></i>
                    <p className="mb-0">❌ Chưa có tọa độ Base</p>
                    <small>Debug: lat={coords?.lat || 'null'}, lon={coords?.lon || 'null'}</small>
                </div>
            </div>
        );
    }

    if (typeof L === 'undefined') {
        console.error('[Map] ❌ LEAFLET CHƯA ĐƯỢC LOAD!');
        return (
            <div className="alert alert-danger" style={{ height: '250px' }}>
                <h5>⚠️ Lỗi: Leaflet chưa được tải</h5>
                <p>Kiểm tra file HTML có thêm Leaflet CSS/JS chưa.</p>
            </div>
        );
    }
    return (
        <div>
            <div className="d-flex justify-content-between align-items-center mb-2">
                <div className="d-flex align-items-center">
                    <h6 className="mb-0 me-3">Vị trí trạm Base (Lưu cứng)</h6>
                    <div className="btn-group btn-group-sm" role="group">
                        <button 
                            type="button" 
                            className={`btn ${mapType === 'm' ? 'btn-primary' : 'btn-outline-primary'}`}
                            onClick={() => setMapType('m')}
                        >
                            Giao thông
                        </button>
                        <button 
                            type="button" 
                            className={`btn ${mapType === 'y' ? 'btn-primary' : 'btn-outline-primary'}`}
                            onClick={() => setMapType('y')}
                        >
                            Vệ tinh
                        </button>
                    </div>
                </div>
                <a href={`https://www.google.com/maps?q=${coords.lat},${coords.lon}`} target="_blank" className="btn btn-sm btn-outline-primary">
                    <i className="bi bi-box-arrow-up-right me-1"></i>Google Maps
                </a>
            </div>
            <div ref={mapContainerRef} style={{ width: '100%', height: '300px', borderRadius: '8px', border: '2px solid #444', zIndex: 0 }} />
            <div className="mt-2 small text-muted">Lat: {parseFloat(coords.lat).toFixed(8)} | Lon: {parseFloat(coords.lon).toFixed(8)}</div>
        </div>
    );
});

// ===============================================================================
// === DASHBOARD COMPONENT ===
// ===============================================================================

const flags = { us: new Image(), ru: new Image(), eu: new Image(), cn: new Image(), jp: new Image(), un: new Image() };
flags.us.src = '/img/flags/us.jpg';
flags.ru.src = '/img/flags/ru.jpg';
flags.eu.src = '/img/flags/eu.jpg';
flags.cn.src = '/img/flags/cn.jpg';
flags.jp.src = '/img/flags/jp.jpg';
flags.un.src = '/img/flags/un.jpg';

const colors = { us: '#00d47e', ru: '#ff5c5c', eu: '#5c9cff', cn: '#ffb95c', jp: '#ff77a9', un: '#aaaaaa' };
const constellationNames = { us: "GPS (USA)", ru: "GLONASS (Russia)", eu: "Galileo (EU)", cn: "BeiDou (China)", jp: "QZSS (Japan)", un: "Unknown" };

function getSatelliteSystem(sat) {
    if (sat.system && ['us', 'ru', 'eu', 'cn', 'jp'].includes(sat.system)) return sat.system;
    const prn = sat.prn;
    if (prn >= 1 && prn <= 32) return 'us';
    if (prn >= 65 && prn <= 96) return 'ru';
    if (prn >= 301 && prn <= 336) return 'eu';
    if (prn >= 193 && prn <= 200) return 'jp';
    if ((prn >= 33 && prn <= 64) || (prn >= 401 && prn <= 463)) return 'cn';
    return 'un';
}

// ===============================================================================
// === ENHANCED DASHBOARD COMPONENT (REPLACED)
// ===============================================================================

const DashboardPanel = memo(function DashboardPanel({ device, nmeaData }) {
    const rtk_status = device.rtk_fix_status || 'NO_FIX';

    let fixInfo = { label: 'NO FIX', color: '#ff0000', icon: '❌' };

    const isAutoSetupStatus = (device.status || '').startsWith('auto_setup');

    if (isAutoSetupStatus) {
        const AUTO_SETUP_STEPS = {
            'auto_setup_rover': { label: 'Bước 1: Đặt chế độ ROVER', color: '#17a2b8', icon: '🔄', step: 1 },
            'auto_setup_wait_fix': { label: 'Bước 2: Chờ RTK Fix...', color: '#ffc107', icon: '📡', step: 2 },
            'auto_setup_averaging': { label: 'Bước 3: Lấy mẫu tọa độ...', color: '#17a2b8', icon: '📊', step: 3 },
            'auto_setup_base': { label: 'Bước 4: Cài đặt BASE', color: '#17a2b8', icon: '🏯', step: 4 }
        };
        const stepInfo = AUTO_SETUP_STEPS[device.status] || { label: 'Auto Setup...', color: '#17a2b8', icon: '⏳', step: 0 };
        fixInfo = { label: stepInfo.label, color: stepInfo.color, icon: stepInfo.icon };
    } else if (device.base_mode_active) {
        const baseConfig = device.base_config || {};
        if (baseConfig.auto_configured) {
            fixInfo = { label: 'BASE (Auto CORS)', color: '#00ff00', icon: '🏯' };
        } else if (baseConfig.base_setup_method === 'FIXED_LLA') {
            fixInfo = { label: 'BASE FIXED', color: '#00ff00', icon: '🏯' };
        } else {
            if (device.ntrip_connected) {
                fixInfo = { label: 'BASE READY', color: '#00ff00', icon: '📡' };
            } else {
                fixInfo = { label: 'SURVEYING...', color: '#ffaa00', icon: '⏳' };
            }
        }
    } else {
        const FIX_STATUS_MAP = {
            'RTK_FIXED': { label: 'RTK FIXED', color: '#00ff00', icon: '🎯' },
            'RTK_FLOAT': { label: 'RTK FLOAT', color: '#ffaa00', icon: '📍' },
            'DGPS': { label: 'DGPS', color: '#ff9900', icon: '⚠️' },
            'GPS_FIX': { label: 'GPS FIX', color: '#999999', icon: '📡' },
            'NO_FIX': { label: 'NO FIX', color: '#ff0000', icon: '❌' }
        };
        fixInfo = FIX_STATUS_MAP[rtk_status] || FIX_STATUS_MAP['NO_FIX'];
    }
    const bgCanvasRef = useRef(null);
    const satCanvasRef = useRef(null);
    const animationFrameRef = useRef(null);
    const lastSatelliteDataRef = useRef(null);
    const signalBarsContainerRef = useRef(null);

    const [satStats, setSatStats] = useState({ total: 0, tracking: 0, bySystem: {} });

    // Clean up satellite buffers when switching devices
    useEffect(() => {
        if (satelliteBufferRef.current) satelliteBufferRef.current.clear();
        lastSatelliteDataRef.current = null;
        
        // Clear canvases immediately
        if (satCanvasRef.current) {
            const ctx = satCanvasRef.current.getContext('2d');
            ctx.clearRect(0, 0, satCanvasRef.current.width, satCanvasRef.current.height);
        }
        if (signalBarsContainerRef.current) {
            signalBarsContainerRef.current.innerHTML = '';
        }
        setSatStats({ total: 0, tracking: 0, bySystem: {} });
    }, [device?.serial]);

    // 🆕 METRIC TRACKING STATE
    const [metrics, setMetrics] = useState({
        fixQuality: { value: 0, history: [] },
        satellites: { value: 0, history: [] },
        hdop: { value: 0, history: [] },
        signalStrength: { value: 0, history: [] },
        throughput: { value: 0, history: [] }
    });

    const satelliteBufferRef = useRef(new Map());
    const SATELLITE_TIMEOUT = 5000;
    const FADE_DURATION = 2000;
    const safeNmeaData = nmeaData || { gga: null, gsa: null, satellites: {} };
    const fallbackBaseCoords = device?.base_config?.coords || (
        device?.base_config?.lat !== undefined && device?.base_config?.lon !== undefined
            ? {
                lat: device.base_config.lat,
                lon: device.base_config.lon,
                alt: device.base_config.alt
            }
            : null
    );
    const baseCoords = safeNmeaData.base_coords || fallbackBaseCoords;

    const lastStatsUpdate = useRef(0);
    const lastCanvasUpdate = useRef(0);
    const lastBarsUpdate = useRef(0);

    const STATS_INTERVAL = 1000;
    const CANVAS_INTERVAL = 200;
    const BARS_INTERVAL = 500;

    // 🆕 METRICS UPDATE LOGIC
    useEffect(() => {
        if (!device || !nmeaData) return;
        const interval = setInterval(() => {
            const gga = nmeaData.gga || {};
            const gsa = nmeaData.gsa || {};
            const sats = Object.values(nmeaData.satellites || {});

            const avgSnr = sats.length > 0
                ? sats.reduce((sum, sat) => sum + (sat.snr || 0), 0) / sats.length
                : 0;

            setMetrics(prev => ({
                fixQuality: {
                    value: gga.fix_status === 'RTK_FIXED' ? 5 : gga.fix_status === 'DGPS' ? 3 : 1,
                    history: [...prev.fixQuality.history, gga.fix_status === 'RTK_FIXED' ? 5 : gga.fix_status === 'DGPS' ? 3 : 1].slice(-30)
                },
                satellites: {
                    value: gga.satellites || 0,
                    history: [...prev.satellites.history, gga.satellites || 0].slice(-30)
                },
                hdop: {
                    value: gsa.hdop || 0,
                    history: [...prev.hdop.history, gsa.hdop || 0].slice(-30)
                },
                signalStrength: {
                    value: avgSnr.toFixed(1),
                    history: [...prev.signalStrength.history, avgSnr].slice(-30)
                },
                throughput: {
                    value: device.bps || 0,
                    history: [...prev.throughput.history, device.bps || 0].slice(-30)
                }
            }));
        }, 2000);
        return () => clearInterval(interval);
    }, [device?.serial]); // Update logic dependencies

    // SKYPLOT & CANVAS LOGIC
    useEffect(() => {
        if (!bgCanvasRef.current || !satCanvasRef.current) return;

        const bgCtx = bgCanvasRef.current.getContext('2d');
        const satCtx = satCanvasRef.current.getContext('2d');

        const drawSkyplotBase = () => {
            const w = bgCtx.canvas.width, h = bgCtx.canvas.height;
            if (w === 0 || h === 0) return;

            const centerX = w / 2, centerY = h / 2;
            const radius = Math.min(centerX, centerY) * 0.85;

            bgCtx.clearRect(0, 0, w, h);

            // Background Gradient
            const grad = bgCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
            grad.addColorStop(0, '#1a2332');
            grad.addColorStop(0.5, '#0d1520');
            grad.addColorStop(1, '#000');
            bgCtx.fillStyle = grad;
            bgCtx.fillRect(0, 0, w, h);

            // Circles
            bgCtx.strokeStyle = '#3a4a5a';
            bgCtx.lineWidth = 1.5;
            bgCtx.font = '11px Arial';
            bgCtx.fillStyle = '#6a7a8a';
            bgCtx.textAlign = 'left';

            [{ frac: 0.33, label: '60°' }, { frac: 0.66, label: '30°' }, { frac: 1.0, label: '0°' }]
                .forEach(({ frac, label }) => {
                    const r = radius * frac;
                    bgCtx.beginPath();
                    bgCtx.arc(centerX, centerY, r, 0, 2 * Math.PI);
                    bgCtx.stroke();
                    bgCtx.fillText(label, centerX + r + 5, centerY);
                });

            // Cross Lines
            bgCtx.strokeStyle = '#2a3a4a';
            bgCtx.lineWidth = 1;
            bgCtx.beginPath();
            for (let i = 0; i < 12; i++) {
                const angle = i * 30 * Math.PI / 180;
                bgCtx.moveTo(centerX, centerY);
                bgCtx.lineTo(centerX + radius * Math.sin(angle), centerY - radius * Math.cos(angle));
            }
            bgCtx.stroke();

            // Labels
            bgCtx.fillStyle = '#fff';
            bgCtx.font = 'bold 18px Arial';
            bgCtx.textAlign = 'center';
            bgCtx.textBaseline = 'middle';
            const labelDistance = radius + 25;
            [{ t: 'N', a: 0 }, { t: 'E', a: 90 }, { t: 'S', a: 180 }, { t: 'W', a: 270 }].forEach(({ t, a }) => {
                const rad = a * Math.PI / 180;
                bgCtx.fillText(t, centerX + labelDistance * Math.sin(rad), centerY - labelDistance * Math.cos(rad));
            });
        };

        drawSkyplotBase();

        const drawSkyplotSatellites = (satellites) => {
            const now = Date.now();
            const buffer = satelliteBufferRef.current;

            satellites.forEach(sat => {
                buffer.set(sat.prn, { ...sat, lastSeen: now, opacity: 1.0 });
            });

            for (const [prn, satData] of buffer.entries()) {
                const timeSinceLastSeen = now - satData.lastSeen;
                if (timeSinceLastSeen > SATELLITE_TIMEOUT + FADE_DURATION) {
                    buffer.delete(prn);
                } else if (timeSinceLastSeen > SATELLITE_TIMEOUT) {
                    const fadeProgress = (timeSinceLastSeen - SATELLITE_TIMEOUT) / FADE_DURATION;
                    satData.opacity = Math.max(0, 1 - fadeProgress);
                }
            }

            satCtx.clearRect(0, 0, satCtx.canvas.width, satCtx.canvas.height);
            const centerX = satCtx.canvas.width / 2;
            const centerY = satCtx.canvas.height / 2;
            const radius = Math.min(centerX, centerY) * 0.82;

            if (satCtx.canvas.width === 0 || satCtx.canvas.height === 0) return;

            for (const satData of buffer.values()) {
                let az = Number(satData.azimuth);
                let el = Number(satData.elevation);
                let snr = Number(satData.snr);

                if (isNaN(snr) || snr === 0) continue;
                if (isNaN(az)) az = 0;
                if (isNaN(el)) el = 0;

                const az_rad = (az * Math.PI) / 180;
                const dist = radius * (90 - el) / 90;
                const x = centerX + dist * Math.sin(az_rad);
                const y = centerY - dist * Math.cos(az_rad);

                const system = getSatelliteSystem(satData);
                const flag = flags[system];
                const isTracking = satData.isTracking;

                satCtx.globalAlpha = satData.opacity || 1.0;
                const flagSize = isTracking ? 18 : 14;

                if (isTracking) {
                    satCtx.beginPath();
                    satCtx.arc(x, y, flagSize / 2 + 3, 0, 2 * Math.PI);
                    satCtx.fillStyle = (colors[system] || '#fff') + '40';
                    satCtx.fill();
                }

                if (flag && flag.complete) {
                    satCtx.drawImage(flag, x - flagSize / 2, y - flagSize / 2, flagSize, flagSize);
                } else {
                    satCtx.beginPath();
                    satCtx.arc(x, y, 9, 0, 2 * Math.PI);
                    satCtx.fillStyle = colors[system] || '#ccc';
                    satCtx.fill();
                }

                satCtx.fillStyle = 'rgba(0, 0, 0, 0.8)';
                satCtx.fillRect(x - 8, y + flagSize / 2 + 2, 16, 10);
                satCtx.fillStyle = '#fff';
                satCtx.font = 'bold 7px Arial';
                satCtx.textAlign = 'center';
                satCtx.textBaseline = 'middle';
                satCtx.fillText(satData.prn, x, y + flagSize / 2 + 7);
            }
            satCtx.globalAlpha = 1.0;
        };

        const drawSignalBars = (satellites) => {
            const container = signalBarsContainerRef.current;
            if (!container) return;

            const buffer = satelliteBufferRef.current;
            // Chỉ lấy các vệ tinh có thông tin SNR > 0 và còn sống (opacity > 0.3) theo yêu cầu ẩn SNR 0
            const activeSatellites = Array.from(buffer.values()).filter(s => s.snr > 0 && s.opacity > 0.3);

            // Tạo hash cấu trúc (Chỉ phụ thuộc số lượng và loại PRN - Không chứa giá trị SNR để tránh giật hình)
            const structureHash = activeSatellites.map(s => s.prn).sort().join('|');

            // Cập nhật text Stats
            const groupedSats = activeSatellites.reduce((acc, sat) => {
                const system = getSatelliteSystem(sat);
                if (!acc[system]) acc[system] = [];
                acc[system].push(sat);
                return acc;
            }, {});
            setSatStats({ total: activeSatellites.length, tracking: activeSatellites.filter(s => s.isTracking).length, bySystem: groupedSats });

            // 1. Nếu CẤU TRÚC bị đổi (thêm/bớt vệ tinh) -> Xóa toàn bộ và vẽ lại DOM
            if (lastSatelliteDataRef.current !== structureHash) {
                lastSatelliteDataRef.current = structureHash;

                const fragment = document.createDocumentFragment();

                ['us', 'ru', 'eu', 'cn', 'jp', 'un'].forEach(system => {
                    if (groupedSats[system]) {
                        // Bắt chước phần mềm u-center: Phải sắp xếp cố định theo số PRN thay vì SNR để cột không bị đảo lộn vị trí!
                        const sats = groupedSats[system].sort((a, b) => parseFloat(a.prn) - parseFloat(b.prn));
                        const groupDiv = document.createElement('div');
                        groupDiv.className = 'constellation-group';

                        const headerDiv = document.createElement('div');
                        headerDiv.className = 'constellation-header';
                        headerDiv.innerHTML = `<img src="${flags[system].src}" width="24" height="24" /> ${constellationNames[system]} (<span class="group-count">${sats.length}</span>)`;

                        const barsWrapper = document.createElement('div');
                        barsWrapper.className = 'bars-wrapper';

                        sats.forEach(sat => {
                            const barFillHeight = Math.min(100, sat.snr / 55 * 100);
                            let barColor = colors[system];
                            if (sat.snr >= 40) barColor = '#00ff88';
                            else if (sat.snr >= 20) barColor = '#ffc107';
                            else barColor = '#ff5252';

                            const itemDiv = document.createElement('div');
                            itemDiv.className = 'sat-bar-item';
                            // Gắn id bằng PRN để sau này chọc thẳng vào DOM update
                            itemDiv.id = `sat-bar-${String(sat.prn).replace(/\\s+/g, '-')}`;
                            itemDiv.innerHTML = `
                                <div class="sat-bar-snr" style="color: ${barColor}; font-weight: bold;">${sat.snr}</div>
                                <div class="sat-bar"><div class="sat-bar-fill" style="height: ${barFillHeight}%; background: ${barColor};"></div></div>
                                <div class="sat-bar-prn">${sat.prn}</div>
                            `;
                            barsWrapper.appendChild(itemDiv);
                        });

                        groupDiv.appendChild(headerDiv);
                        groupDiv.appendChild(barsWrapper);
                        fragment.appendChild(groupDiv);
                    }
                });

                container.innerHTML = '';
                container.appendChild(fragment);
            }
            // 2. Chế độ NGẦM: Chỉ cập nhật trị SNR + Color (Không tái tạo DOM, giúp thanh Transition mượt mà và hết giật nền)
            else {
                activeSatellites.forEach(sat => {
                    const domId = `sat-bar-${String(sat.prn).replace(/\\s+/g, '-')}`;
                    const itemDiv = document.getElementById(domId);
                    if (itemDiv) {
                        const barFillHeight = Math.min(100, sat.snr / 55 * 100);
                        const system = getSatelliteSystem(sat);
                        let barColor = colors[system];
                        if (sat.snr >= 40) barColor = '#00ff88';
                        else if (sat.snr >= 20) barColor = '#ffc107';
                        else barColor = '#ff5252';

                        const snrText = itemDiv.querySelector('.sat-bar-snr');
                        const fillDiv = itemDiv.querySelector('.sat-bar-fill');

                        if (snrText) {
                            snrText.textContent = sat.snr;
                            snrText.style.color = barColor;
                        }
                        if (fillDiv) {
                            fillDiv.style.height = `${barFillHeight}%`;
                            fillDiv.style.background = barColor;
                        }
                    }
                });
            }
        };

        const animate = () => {
            const now = Date.now();
            const { gga, gsa, satellites } = safeNmeaData;

            // Text Stats Updates
            if (now - lastStatsUpdate.current >= STATS_INTERVAL) {

                const satCountEl = document.getElementById('db-sats');
                if (satCountEl) satCountEl.textContent = gga?.satellites || 0;

                const dopEl = document.getElementById('db-dop');
                if (dopEl) dopEl.textContent = `${gsa?.hdop?.toFixed(2) || '0.0'} / ${gsa?.pdop?.toFixed(2) || '0.0'}`;

                const latEl = document.getElementById('db-lat');
                if (latEl) latEl.textContent = gga?.latitude?.toFixed(8) || '0.0';

                const lonEl = document.getElementById('db-lon');
                if (lonEl) lonEl.textContent = gga?.longitude?.toFixed(8) || '0.0';

                lastStatsUpdate.current = now;
            }

            // Canvas & Bars Updates
            if (now - lastCanvasUpdate.current >= CANVAS_INTERVAL) {
                drawSkyplotSatellites(Object.values(satellites || {}));
                lastCanvasUpdate.current = now;
            }

            if (now - lastBarsUpdate.current >= BARS_INTERVAL) {
                drawSignalBars(Object.values(satellites || {}));
                lastBarsUpdate.current = now;
            }

            animationFrameRef.current = requestAnimationFrame(animate);
        };

        const handleResize = () => {
            if (bgCanvasRef.current && satCanvasRef.current) {
                const container = bgCanvasRef.current.parentElement;
                if (!container) return;
                const w = container.clientWidth;
                const h = container.clientHeight;

                if (w > 0 && h > 0) {
                    bgCanvasRef.current.width = w;
                    bgCanvasRef.current.height = h;
                    satCanvasRef.current.width = w;
                    satCanvasRef.current.height = h;
                    drawSkyplotBase();
                }
            }
        };

        handleResize();
        setTimeout(handleResize, 100);
        setTimeout(handleResize, 500);
        window.addEventListener('resize', handleResize);

        animate();

        return () => {
            window.removeEventListener('resize', handleResize);
            if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
        };
    }, [nmeaData]);
    /*
    const FIX_STATUS_MAP = {
        'RTK_FIXED': { label: 'RTK FIXED', color: '#00ff00', icon: '🎯', priority: 5 },
        'RTK_FLOAT': { label: 'RTK FLOAT', color: '#ffaa00', icon: '📍', priority: 4 },
        'DGPS': { label: 'DGPS', color: '#ff9900', icon: '⚠️', priority: 3 },
        'GPS_FIX': { label: 'GPS FIX', color: '#999999', icon: '📡', priority: 2 },
        'NO_FIX': { label: 'NO FIX', color: '#ff0000', icon: '❌', priority: 1 }
    };
    
    const rtk_status = device.rtk_fix_status || 'NO_FIX';
    const fixInfo = FIX_STATUS_MAP[rtk_status] || FIX_STATUS_MAP['NO_FIX'];
    */

    return (
        <div className="dashboard-master">
            {/* 🆕 METRIC CARDS WITH SPARKLINES (Nếu có component MetricCard) */}
            {window.MetricCard && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px', marginBottom: '16px' }}>
                    <window.MetricCard title="Fix Quality" value={metrics.fixQuality.value} unit="/5" trend={0} sparklineData={metrics.fixQuality.history} color="#51cf66" icon="🎯" />
                    <window.MetricCard title="Satellites" value={metrics.satellites.value} unit="" trend={0} sparklineData={metrics.satellites.history} color="#0d6efd" icon="🛰️" />
                    <window.MetricCard title="HDOP" value={metrics.hdop.value.toFixed(2)} unit="" trend={0} sparklineData={metrics.hdop.history} color="#ffd43b" icon="📡" />
                    <window.MetricCard title="Avg SNR" value={metrics.signalStrength.value} unit="dB" trend={0} sparklineData={metrics.signalStrength.history} color="#fa5252" icon="📶" />
                </div>
            )}

            {/* STATS STRIP */}
            <div className="stats-strip">
                {/* ✅ CẬP NHẬT FIX STATUS CARD */}
                <div className="stat-item">
                    <div className="stat-icon" style={{ fontSize: '2rem' }}>{fixInfo.icon}</div>
                    <div className="stat-content">
                        <div className="stat-label">Fix Status</div>
                        <div
                            className="stat-value"
                            style={{
                                color: fixInfo.color,
                                textShadow: `0 0 10px ${fixInfo.color}`,
                                fontWeight: 'bold'
                            }}
                        >
                            {fixInfo.label}
                        </div>
                    </div>
                </div>
                <div className="stat-item"><div className="stat-icon">🛰️</div><div className="stat-content"><div className="stat-label">Satellites</div><div id="db-sats" className="stat-value">0</div></div></div>
                <div className="stat-item"><div className="stat-icon">📡</div><div className="stat-content"><div className="stat-label">HDOP / PDOP</div><div id="db-dop" className="stat-value">0.0 / 0.0</div></div></div>
                <div className="stat-item"><div className="stat-icon">📍</div><div className="stat-content"><div className="stat-label">Position</div><div className="stat-value stat-small"><span id="db-lat">0.0</span> / <span id="db-lon">0.0</span></div></div></div>
            </div>
            {/* BASE MODE WARNING */}
            {device.base_mode_active && rtk_status !== 'RTK_FIXED' && (
                <div className="alert alert-warning mt-3" role="alert">
                    <i className="bi bi-exclamation-triangle-fill me-2"></i>
                    <strong>Base Station Mode Active</strong> - Waiting for RTK convergence...
                    {rtk_status === 'DGPS' && (
                        <div className="small mt-1">
                            Currently in DGPS mode. RTK Fixed typically requires 5-10 minutes after power-on.
                        </div>
                    )}
                </div>
            )}

            {/* MAP */}
            <div className="map-section">
                <BaseLocationMap coords={baseCoords} />
            </div>

            {/* SKYPLOT & SIGNALS */}
            <div className="sky-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div className="sky-panel skyplot-panel" style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                    <div className="panel-header" style={{ flexShrink: 0 }}>
                        <i className="bi bi-radar"></i><span>Sky View</span>
                        <span className="badge bg-primary-subtle text-primary ms-auto">{Object.keys(safeNmeaData.satellites || {}).length} Sats</span>
                    </div>
                    <div className="skyplot-wrapper" style={{ position: 'relative', width: '100%', height: '100%' }}>
                        <canvas ref={bgCanvasRef} className="skyplot-bg" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 1 }}></canvas>
                        <canvas ref={satCanvasRef} className="skyplot-sat" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 2 }}></canvas>
                    </div>
                </div>

                <div className="sky-panel signals-panel">
                    <div className="panel-header">
                        <i className="bi bi-bar-chart-fill"></i><span>Signal Strength (SNR)</span>
                        <div className="ms-auto d-flex gap-2">
                            <span className="badge bg-success">{satStats.tracking} Tracking</span>
                            <span className="badge bg-secondary">{satStats.total} Total</span>
                        </div>
                    </div>
                    <div ref={signalBarsContainerRef} className="signals-scroll" style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '10px' }}></div>
                    
                    {/* Premium Chart Overlay */}
                    {window.SatelliteBars && (
                        <div className="mt-auto pt-2 border-top border-secondary-subtle">
                            <window.SatelliteBars satellites={safeNmeaData.satellites} />
                        </div>
                    )}
                </div>
            </div>

            {/* REAL-TIME CHARTS SECTION */}
            <div className="mt-4">
                <div className="row g-3">
                    <div className="col-md-4">
                        <div className="card bg-dark border-secondary-subtle h-100">
                            <div className="card-header py-1 bg-transparent border-secondary-subtle d-flex justify-content-between align-items-center">
                                <span className="small text-muted fw-bold"><i className="bi bi-broadcast me-1"></i>SATELLITES</span>
                                <span className="badge bg-primary">{metrics.satellites.value}</span>
                            </div>
                            <div className="card-body p-2">
                                {window.MetricLineChart ? (
                                    <window.MetricLineChart label="Count" data={metrics.satellites.history} color="#0dcaf0" />
                                ) : (
                                    <div className="text-muted small text-center p-3">Component Missing</div>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="col-md-4">
                        <div className="card bg-dark border-secondary-subtle h-100">
                            <div className="card-header py-1 bg-transparent border-secondary-subtle d-flex justify-content-between align-items-center">
                                <span className="small text-muted fw-bold"><i className="bi bi-activity me-1"></i>HDOP ACCURACY</span>
                                <span className="badge bg-warning text-dark">{metrics.hdop.value.toFixed(2)}</span>
                            </div>
                            <div className="card-body p-2">
                                {window.MetricLineChart ? (
                                    <window.MetricLineChart label="HDOP" data={metrics.hdop.history} color="#ffc107" />
                                ) : (
                                    <div className="text-muted small text-center p-3">Component Missing</div>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="col-md-4">
                        <div className="card bg-dark border-secondary-subtle h-100">
                            <div className="card-header py-1 bg-transparent border-secondary-subtle d-flex justify-content-between align-items-center">
                                <span className="small text-muted fw-bold"><i className="bi bi-speedometer2 me-1"></i>THROUGHPUT</span>
                                <span className="badge bg-success">{(metrics.throughput.value / 1024).toFixed(1)} KB/s</span>
                            </div>
                            <div className="card-body p-2">
                                {window.MetricLineChart ? (
                                    <window.MetricLineChart label="BPS" data={metrics.throughput.history} color="#198754" unit=" Bps" />
                                ) : (
                                    <div className="text-muted small text-center p-3">Component Missing</div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
});
window.DashboardPanel = DashboardPanel;

const RoverManagementPanel = memo(function RoverManagementPanel({ device }) {
    const [rovers, setRovers] = useState([]);
    const [isLoading, setIsLoading] = useState(true);

    // Form States
    const [newUsername, setNewUsername] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [selectedMountpoint, setSelectedMountpoint] = useState('');
    const [expiryDays, setExpiryDays] = useState('365'); // Mặc định 1 năm

    // --- 1. LẤY DANH SÁCH MOUNTPOINT KHẢ DỤNG TỪ CẤU HÌNH ---
    const availableMountpoints = [];
    const config = device.service_config || {};

    if (config.server1_enabled && config.mountpoint1) {
        availableMountpoints.push({
            id: 'server1',
            name: config.mountpoint1,
            host: config.serverhost1 || 'Local'
        });
    }
    if (config.server2_enabled && config.mountpoint2) {
        availableMountpoints.push({
            id: 'server2',
            name: config.mountpoint2,
            host: config.serverhost2 || 'Local'
        });
    }

    // Tự động chọn Mountpoint đầu tiên khi load
    useEffect(() => {
        if (availableMountpoints.length > 0 && !selectedMountpoint) {
            setSelectedMountpoint(availableMountpoints[0].name);
        }
    }, [device.service_config]);

    // --- 2. TẢI DANH SÁCH ROVER ---
    const fetchRovers = async () => {
        try {
            setIsLoading(true);
            const response = await window.apiFetch(`/api/bases/${device.serial}/rovers`);
            if (!response.ok) throw new Error("Không thể tải danh sách Rover");
            const data = await response.json();
            setRovers(data);
        } catch (error) {
            window.showError(error.message);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        if (device?.serial) {
            fetchRovers();
        }
    }, [device?.serial]);

    // --- 3. XỬ LÝ TẠO ROVER ---
    const handleCreateRover = async (e) => {
        e.preventDefault();

        // Validation cơ bản
        if (!newUsername.trim() || !newPassword.trim()) {
            return window.showError("Tên đăng nhập và mật khẩu không được để trống.");
        }

        // Validation ký tự đặc biệt (QUAN TRỌNG CHO CASTER)
        // Chỉ cho phép chữ cái (a-z, A-Z), số (0-9) và gạch dưới (_)
        const validUsernameRegex = /^[a-zA-Z0-9_]+$/;
        if (!validUsernameRegex.test(newUsername)) {
            return window.showError("Tên đăng nhập chỉ được chứa chữ cái không dấu, số và dấu gạch dưới (_). Không dùng khoảng trắng hoặc ký tự đặc biệt.");
        }

        // Kiểm tra xem đã chọn mountpoint chưa
        let targetMp = selectedMountpoint;
        if (!targetMp && availableMountpoints.length > 0) {
            targetMp = availableMountpoints[0].name;
        }
        if (!targetMp) {
            // Fallback nếu chưa cấu hình gì cả (ít khi xảy ra nếu UI đúng)
            targetMp = `MP_${device.serial}`;
        }

        try {
            const payload = {
                username: newUsername,
                password: newPassword,
                mountpoint_allow: targetMp,
                expiry_days: parseInt(expiryDays)
            };

            const response = await window.apiFetch(`/api/bases/${device.serial}/rovers`, {
                method: 'POST',
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || "Tạo tài khoản thất bại");
            }

            window.showSuccess(`Đã tạo tài khoản Rover '${newUsername}' thành công!`);

            // Reset form
            setNewUsername('');
            setNewPassword('');
            fetchRovers(); // Reload list

        } catch (error) {
            window.showError(error.message);
        }
    };

    // --- 4. XỬ LÝ XÓA ROVER ---
    const handleDeleteRover = async (roverId, username) => {
        if (!confirm(`Bạn có chắc muốn xóa tài khoản Rover '${username}'? Tài khoản này sẽ bị ngắt kết nối ngay lập tức.`)) return;
        try {
            const response = await window.apiFetch(`/api/rovers/${roverId}`, { method: 'DELETE' });
            if (!response.ok) throw new Error("Xóa thất bại");
            window.showSuccess(`Đã xóa tài khoản '${username}'.`);
            fetchRovers();
        } catch (error) {
            window.showError(error.message);
        }
    };

    return (
        <div>
            {/* CẢNH BÁO NẾU CHƯA CẤU HÌNH SERVER NTRIP */}
            {availableMountpoints.length === 0 && (
                <div className="alert alert-warning small mb-3">
                    <i className="bi bi-exclamation-triangle-fill me-2"></i>
                    <strong>Chưa cấu hình dịch vụ phát:</strong> Trạm này chưa bật NTRIP Server nào.
                    Vui lòng sang tab <strong>"Dịch vụ"</strong> để bật Server 1 hoặc Server 2 và đặt tên Mountpoint trước khi tạo tài khoản Rover.
                </div>
            )}

            {/* FORM TẠO MỚI */}
            <div className="p-3 border rounded bg-dark-subtle mb-4 shadow-sm">
                <h5 className="text-primary-emphasis border-bottom pb-2 mb-3">
                    <i className="bi bi-person-plus-fill me-2"></i>Thêm tài khoản Rover (Khách hàng)
                </h5>
                <form onSubmit={handleCreateRover}>
                    <div className="row g-3">
                        <div className="col-md-3">
                            <label className="form-label small fw-bold">Tên đăng nhập</label>
                            <input
                                type="text"
                                className="form-control"
                                value={newUsername}
                                onChange={e => setNewUsername(e.target.value)}
                                placeholder="VD: khach_a_01"
                            />
                        </div>
                        <div className="col-md-3">
                            <label className="form-label small fw-bold">Mật khẩu</label>
                            <input
                                type="text"
                                className="form-control"
                                value={newPassword}
                                onChange={e => setNewPassword(e.target.value)}
                            />
                        </div>
                        <div className="col-md-3">
                            <label className="form-label small fw-bold">Quyền truy cập (Mountpoint)</label>
                            <select
                                className="form-select"
                                value={selectedMountpoint}
                                onChange={(e) => setSelectedMountpoint(e.target.value)}
                                disabled={availableMountpoints.length === 0}
                            >
                                {availableMountpoints.length === 0 && <option>Chưa có MP</option>}
                                {availableMountpoints.map(mp => (
                                    <option key={mp.id} value={mp.name}>
                                        {mp.name} ({mp.id === 'server1' ? 'S1' : 'S2'})
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div className="col-md-3">
                            <label className="form-label small fw-bold">Thời hạn sử dụng</label>
                            <select
                                className="form-select"
                                value={expiryDays}
                                onChange={(e) => setExpiryDays(e.target.value)}
                            >
                                <option value="1">1 Ngày (Test)</option>
                                <option value="7">1 Tuần</option>
                                <option value="30">1 Tháng</option>
                                <option value="90">3 Tháng</option>
                                <option value="180">6 Tháng</option>
                                <option value="365">1 Năm</option>
                                <option value="730">2 Năm</option>
                                <option value="1095">3 Năm</option>
                                <option value="3650">Vĩnh viễn (~10 năm)</option>
                            </select>
                        </div>
                        <div className="col-12 text-end">
                            <button type="submit" className="btn btn-primary px-4" disabled={availableMountpoints.length === 0}>
                                <i className="bi bi-plus-circle me-2"></i>Tạo Tài Khoản
                            </button>
                        </div>
                    </div>
                </form>
            </div>

            {/* DANH SÁCH TÀI KHOẢN */}
            <h5 className="mt-4 mb-3">Danh sách Rover đã tạo</h5>
            {isLoading ? (
                <div className="text-center py-3">
                    <div className="spinner-border text-primary" role="status"></div>
                    <p className="mt-2 text-muted">Đang đồng bộ danh sách...</p>
                </div>
            ) : rovers.length === 0 ? (
                <div className="text-center p-4 bg-dark rounded text-muted border border-secondary">
                    <i className="bi bi-people display-6 d-block mb-3 opacity-50"></i>
                    Chưa có tài khoản Rover nào được tạo cho trạm này.
                </div>
            ) : (
                <div className="table-responsive">
                    <table className="table table-hover align-middle table-bordered border-secondary">
                        <thead className="table-dark text-center">
                            <tr>
                                <th>Tài khoản (Username)</th>
                                <th>Mật khẩu</th>
                                <th>Mountpoint (Luồng dữ liệu)</th>
                                <th>Trạng thái</th>
                                <th>Hành động</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rovers.map(rover => (
                                <tr key={rover.id}>
                                    <td>
                                        <div className="d-flex align-items-center">
                                            <div className="bg-primary rounded-circle p-2 me-2 d-flex align-items-center justify-content-center" style={{ width: '32px', height: '32px' }}>
                                                <i className="bi bi-person-fill text-white"></i>
                                            </div>
                                            <strong>{rover.username}</strong>
                                        </div>
                                    </td>
                                    <td className="font-monospace text-warning">{rover.password}</td>
                                    <td className="text-center">
                                        <span className="badge bg-info text-dark border border-info-subtle">
                                            <i className="bi bi-broadcast me-1"></i>
                                            {/* Logic hiển thị Mountpoint, ưu tiên lấy từ config hiện tại nếu khớp, ko thì hiển thị cái fallback */}
                                            {availableMountpoints.find(mp => mp.name === rover.base_serial)
                                                ? rover.base_serial // Lưu ý: Backend đang lưu base_serial, nên cần sửa backend để lưu mountpoint nếu muốn hiển thị chính xác lịch sử.
                                                : (availableMountpoints.length > 0 ? availableMountpoints[0].name : "Mặc định")
                                            }
                                            {/* Tạm thời hiển thị MP hiện tại của trạm vì logic DB đang lưu base_serial chứ chưa lưu mountpoint cụ thể từng user. 
                                                Nhưng với caster, user đó chỉ login được vào MP đã đăng ký. 
                                                Ở đây hiển thị MP hiện tại của trạm là hợp lý nhất. */}
                                            {selectedMountpoint || (availableMountpoints[0]?.name || "N/A")}
                                        </span>
                                    </td>
                                    <td className="text-center">
                                        <span className={`badge ${rover.is_active ? 'bg-success' : 'bg-secondary'}`}>
                                            {rover.is_active ? 'Active' : 'Expired'}
                                        </span>
                                    </td>
                                    <td className="text-center">
                                        <button
                                            className="btn btn-sm btn-outline-danger"
                                            title="Xóa & Ngắt kết nối Rover này"
                                            onClick={() => handleDeleteRover(rover.id, rover.username)}
                                        >
                                            <i className="bi bi-trash3 me-1"></i> Xóa
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* HƯỚNG DẪN SỬ DỤNG */}
            <div className="alert alert-secondary small mt-4">
                <h6 className="alert-heading fw-bold"><i className="bi bi-info-circle-fill me-2"></i>Hướng dẫn kết nối cho Khách hàng:</h6>
                <hr />
                <ul className="mb-0 ps-3">
                    <li><strong>IP / Host:</strong> {window.location.hostname} (hoặc Domain của bạn)</li>
                    <li><strong>Port:</strong> 2101</li>
                    <li><strong>Mountpoint:</strong> {selectedMountpoint || "Xem ở cột Mountpoint"}</li>
                    <li><strong>Username / Password:</strong> Cấp theo danh sách trên.</li>
                </ul>
            </div>
        </div>
    );
});

const CasterConfigModal = memo(function CasterConfigModal() {
    const [config, setConfig] = useState({ host: 'aitogy.com.vn', port: 2101 });
    const [isOpen, setIsOpen] = useState(false);

    // Hàm mở modal (gắn vào window để gọi từ console hoặc nút khác)
    useEffect(() => {
        window.openCasterConfig = () => {
            fetchConfig();
            setIsOpen(true);
        };
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await window.apiFetch('/api/admin/caster-config');
            if (res.ok) setConfig(await res.json());
        } catch (e) { console.error(e); }
    };

    const handleSave = async () => {
        try {
            await window.apiFetch('/api/admin/caster-config', {
                method: 'PUT',
                body: JSON.stringify(config)
            });
            window.showSuccess("Đã cập nhật cấu hình NTRIP Caster!");
            setIsOpen(false);
        } catch (e) {
            window.showError(e.message);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="modal d-block" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
            <div className="modal-dialog">
                <div className="modal-content bg-dark text-light border-secondary">
                    <div className="modal-header border-secondary">
                        <h5 className="modal-title">Cấu hình Hệ thống NTRIP Caster</h5>
                        <button type="button" className="btn-close btn-close-white" onClick={() => setIsOpen(false)}></button>
                    </div>
                    <div className="modal-body">
                        <p className="text-muted small">Cấu hình địa chỉ Caster dùng để tạo/xóa Rover qua giao thức TCP Admin.</p>
                        <div className="mb-3">
                            <label className="form-label">Caster Host / IP</label>
                            <input type="text" className="form-control" value={config.host}
                                onChange={e => setConfig({ ...config, host: e.target.value })} />
                        </div>
                        <div className="mb-3">
                            <label className="form-label">Admin Port (TCP)</label>
                            <input type="number" className="form-control" value={config.port}
                                onChange={e => setConfig({ ...config, port: parseInt(e.target.value) })} />
                        </div>
                    </div>
                    <div className="modal-footer border-secondary">
                        <button type="button" className="btn btn-secondary" onClick={() => setIsOpen(false)}>Đóng</button>
                        <button type="button" className="btn btn-primary" onClick={handleSave}>Lưu Cấu hình</button>
                    </div>
                </div>
            </div>
        </div>
    );
});
window.CasterConfigModal = CasterConfigModal;

// ===============================================================================
// === SYSTEM INFO SIDE PANEL 
// ===============================================================================

const SystemInfoPanel = memo(function SystemInfoPanel({ device, isOpen, onClose }) {
    if (!device || !device.system_info) {
        return null;
    }

    const info = device.system_info;

    // Format uptime
    const formatUptime = (seconds) => {
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const mins = Math.floor((seconds % 3600) / 60);

        if (days > 0) return `${days}d ${hours}h ${mins}m`;
        if (hours > 0) return `${hours}h ${mins}m`;
        return `${mins}m`;
    };

    // Calculate health score
    const calculateHealthScore = () => {
        let score = 100;
        if (info.cpu?.usage_percent > 80) score -= 20;
        else if (info.cpu?.usage_percent > 60) score -= 10;

        if (info.temperature?.celsius > 70) score -= 25;
        else if (info.temperature?.celsius > 60) score -= 15;

        if (info.memory?.percent > 85) score -= 20;
        else if (info.memory?.percent > 70) score -= 10;

        return Math.max(score, 0);
    };

    const healthScore = calculateHealthScore();
    const healthColor = healthScore >= 80 ? '#51cf66' : healthScore >= 60 ? '#ffd43b' : '#fa5252';

    return (
        <div className={`system-info-panel ${isOpen ? 'open' : ''}`}>
            {/* Header */}
            <div className="panel-header-sys">
                <div className="d-flex align-items-center gap-2">
                    <i className="bi bi-cpu text-primary"></i>
                    <h5 className="mb-0">System Monitor</h5>
                </div>
                <button className="btn btn-sm btn-ghost" onClick={onClose} title="Đóng">
                    <i className="bi bi-x-lg"></i>
                </button>
            </div>

            {/* Body */}
            <div className="panel-body-sys">

                {/* 1. HEALTH SCORE & DEVICE INFO */}
                <div className="d-flex align-items-center justify-content-between mb-4 p-3 rounded bg-dark border border-secondary">
                    <div>
                        <div className="text-muted small text-uppercase">Health Score</div>
                        <div className="display-6 fw-bold" style={{ color: healthColor }}>{healthScore}%</div>
                        <div className="small text-light">{healthScore >= 80 ? 'Excellent' : 'Warning'}</div>
                    </div>
                    <div className="text-end">
                        <div className="text-muted small">Uptime</div>
                        <div className="fw-bold text-light">{info.uptime_seconds ? formatUptime(info.uptime_seconds) : 'N/A'}</div>
                        <div className="text-muted small mt-1">Chip: {device.detected_chip_type || 'Unknown'}</div>
                    </div>
                </div>

                {/* 2. CIRCULAR METRICS (CPU & RAM) */}
                <div className="row g-3 mb-4">
                    {/* CPU Ring */}
                    {info.cpu && window.ProgressRing && (
                        <div className="col-6 d-flex justify-content-center">
                            <window.ProgressRing
                                value={info.cpu.usage_percent}
                                max={100}
                                size={110}
                                strokeWidth={8}
                                color={info.cpu.usage_percent > 80 ? '#fa5252' : '#0d6efd'}
                                label="CPU Load"
                                subLabel={`${info.cpu.count} Cores`}
                            />
                        </div>
                    )}

                    {/* RAM Ring */}
                    {info.memory && window.ProgressRing && (
                        <div className="col-6 d-flex justify-content-center">
                            <window.ProgressRing
                                value={info.memory.percent}
                                max={100}
                                size={110}
                                strokeWidth={8}
                                color={info.memory.percent > 85 ? '#fa5252' : '#74c0fc'}
                                label="RAM Usage"
                                subLabel={`${Math.round(info.memory.used_mb)}/${Math.round(info.memory.total_mb)}MB`}
                            />
                        </div>
                    )}
                </div>

                {/* 3. TEMPERATURE & DISK (Bar Style for Compactness) */}
                <div className="info-section">
                    <div className="section-title"><i className="bi bi-thermometer-half me-2"></i>Temperature</div>
                    {info.temperature?.celsius ? (
                        <div className="metric-card">
                            <div className="d-flex justify-content-between mb-1">
                                <span className="fw-bold text-light">{info.temperature.celsius}°C</span>
                                <span className="text-muted small">Max: 85°C</span>
                            </div>
                            <div className="progress" style={{ height: '8px' }}>
                                <div
                                    className={`progress-bar ${info.temperature.celsius > 70 ? 'bg-danger' : 'bg-success'}`}
                                    style={{ width: `${Math.min((info.temperature.celsius / 85) * 100, 100)}%` }}
                                ></div>
                            </div>
                        </div>
                    ) : <div className="text-muted small">No sensor data</div>}
                </div>

                <div className="info-section mt-3">
                    <div className="section-title"><i className="bi bi-hdd me-2"></i>Disk Usage</div>
                    {info.disk ? (
                        <div className="metric-card">
                            <div className="d-flex justify-content-between mb-1">
                                <span className="fw-bold text-light">{info.disk.percent}% Used</span>
                                <span className="text-muted small">Free: {(info.disk.total_gb - info.disk.used_gb).toFixed(1)} GB</span>
                            </div>
                            <div className="progress" style={{ height: '8px' }}>
                                <div
                                    className={`progress-bar ${info.disk.percent > 90 ? 'bg-danger' : 'bg-primary'}`}
                                    style={{ width: `${info.disk.percent}%` }}
                                ></div>
                            </div>
                        </div>
                    ) : <div className="text-muted small">No disk data</div>}
                </div>

                {/* Footer */}
                <div className="text-center text-muted small mt-4 pt-3 border-top border-secondary">
                    <i className="bi bi-clock-history me-1"></i>
                    Updated: {new Date(info.timestamp * 1000).toLocaleTimeString()}
                </div>
            </div>
        </div>
    );
});
window.SystemInfoPanel = SystemInfoPanel;

console.log('✓ SystemInfoPanel component loaded');

// =========================================
// === COMPONENT CHÍNH: DetailsPanel ===
// =========================================
const DetailsPanel = memo(function DetailsPanel({
    device,
    currentUser,
    showVirtualStationForm,
    nmeaData,
    onReset,
    onProvision,
    onConfigureChip,
    onDeployLicense,
    onConfigureService,
    onCreateVirtualStation,
    onCloseVirtualStationForm,
    onLock,
    onUnlock
}) {
    const isAdmin = currentUser?.role === 'admin';
    const [activeTab, setActiveTab] = useState('');

    const [isEditingName, setIsEditingName] = useState(false);
    const [tempName, setTempName] = useState('');
    const [userSelectedTab, setUserSelectedTab] = useState(false);
    const [isSystemPanelOpen, setIsSystemPanelOpen] = useState(false);
    useEffect(() => {
        if (!device) return;
        let newActiveTab = '';

        switch (device.status) {
            case 'awaiting_license':
                newActiveTab = 'license';
                break;
            case 'unprovisioned':
                newActiveTab = 'chip';
                break;
            case 'auto_setup_rover':
            case 'auto_setup_wait_fix':
            case 'auto_setup_averaging':
            case 'auto_setup_base':
                newActiveTab = 'chip';
                break;
            default:
                if (userSelectedTab && activeTab) {
                    return;
                }

                const isGnssConfigured = device.base_config && Object.keys(device.base_config).length > 0;
                if (!isGnssConfigured) {
                    newActiveTab = 'chip';
                } else {
                    newActiveTab = 'service';
                }
                break;
        }

        if (newActiveTab) {
            setActiveTab(newActiveTab);
            setUserSelectedTab(false); // Reset flag
        }

        setTempName(device.name || '');
        setIsEditingName(false);

    }, [device?.serial, device?.status]);

    const handleTabClick = (tabName) => {
        setActiveTab(tabName);
        setUserSelectedTab(true);
    };

    const handleSaveName = () => {
        if (!tempName.trim()) {
            window.showError('Tên trạm không được để trống!');
            return;
        }

        if (tempName !== device.name) {
            window.handleRenameDevice(device.serial, tempName);
        }
        setIsEditingName(false);
    };

    if (!device) {
        if (showVirtualStationForm && isAdmin) {
            return (
                <div className="card bg-dark-tertiary h-100 d-flex flex-column" style={{ maxHeight: '100vh', overflowX: 'hidden' }}>
                    <div className="card-header bg-dark d-flex justify-content-between align-items-center">
                        <h4 className="mb-0 text-info"><i className="bi bi-cloud-plus-fill me-2"></i>Tạo Trạm Ảo</h4>
                    </div>
                    <div className="card-body overflow-auto">
                        <VirtualStationFormPanel
                            currentUser={currentUser}
                            onCreateVirtualStation={onCreateVirtualStation}
                            onCancel={onCloseVirtualStationForm}
                        />
                    </div>
                </div>
            );
        }

        return (
            <div className="card bg-dark-tertiary h-100">
                <div className="card-body text-center d-flex flex-column justify-content-center">
                    <i className="bi bi-cursor-fill display-1 text-muted"></i>
                    <h3 className="mt-3 text-muted">Vui lòng chọn một trạm từ danh sách bên trái.</h3>
                </div>
            </div>
        );
    }

    const role = String(currentUser?.role || '').toLowerCase();
    const perms = Array.isArray(currentUser?.permissions) ? currentUser.permissions : [];
    const hasServiceEditPermission =
        perms.includes('edit:service_config') ||
        perms.includes('EDIT_SERVICE_CONFIG') ||
        perms.includes('manage:station_mode') ||
        perms.includes('MANAGE_STATION_MODE');
    const serialUpper = String(device?.serial || '').toUpperCase();
    const chipUpper = String(device?.detected_chip_type || '').toUpperCase();
    const isRelayLikeStation =
        serialUpper.startsWith('RELAY_') ||
        serialUpper.startsWith('MONITOR_') ||
        chipUpper === 'VIRTUAL';

    const canEdit = role === 'admin' ||
        (role === 'coordinator' && device.user_id === currentUser.id);
    const canEditService = canEdit || (isRelayLikeStation && hasServiceEditPermission);

    if (device.status === 'rebooting_for_reset') {
        return (
            <div className="card bg-dark-tertiary h-100">
                <div className="card-body text-center d-flex flex-column justify-content-center align-items-center">
                    <div className="spinner-border text-danger" style={{ width: '3rem', height: '3rem' }} role="status">
                        <span className="visually-hidden">Loading...</span>
                    </div>
                    <h3 className="mt-4 text-danger">Đang Reset Trạm</h3>
                    <p className="text-muted mt-2">
                        Thiết bị đang xóa cấu hình và khởi động lại.<br />
                        Giao diện sẽ tự động chuyển sang bước cấp License sau giây lát.
                    </p>
                </div>
            </div>
        );
    }

    const renderTabContent = () => {
        const commonProps = { device, currentUser, disabled: !canEdit };

        switch (activeTab) {
            case 'license':
                return <LicenseForm {...commonProps} onDeployLicense={onDeployLicense} />;
            case 'chip':
                if (device.status === 'unprovisioned') {
                    return <ProvisionForm {...commonProps} onProvision={onProvision} />;
                }
                return <BaseConfigForm {...commonProps} onConfigureChip={onConfigureChip} />;
            case 'service':
                return <ServiceConfigForm {...commonProps} disabled={!canEditService} onConfigureService={onConfigureService} />;
            case 'dashboard':
                return <DashboardPanel device={device} nmeaData={nmeaData} />;
            case 'rovers':
                if (!canEdit) {
                    return <div className="alert alert-danger">Bạn không có quyền truy cập mục này.</div>;
                }
                return <RoverManagementPanel device={device} />;
            default:
                return null;
        }
    };

    const canManageRovers = device && (currentUser?.role === 'admin' || (currentUser?.role === 'coordinator' && currentUser?.can_create_rovers));
    const showConfigTabs = !['awaiting_license', 'rebooting_for_reset'].includes(device.status);
    const showServiceAndDashboardTabs = showConfigTabs && (device.status !== 'unprovisioned' || isRelayLikeStation);

    const isDeviceLocked = device.is_locked || device.status === 'locked';

    return (
        <div className="card bg-dark-tertiary h-100 d-flex flex-column" style={{ maxHeight: '100vh', position: 'relative', overflowX: 'hidden' }}>
            {isAdmin && device && device.system_info && (
                <button
                    className="btn-system-toggle"
                    onClick={() => setIsSystemPanelOpen(!isSystemPanelOpen)}
                    title="System Monitor"
                >
                    <i className="bi bi-cpu"></i>
                </button>
            )}
            {/* CARD-HEADER */}
            <div className="card-header d-flex justify-content-between align-items-center bg-dark flex-shrink-0">
                <div className="d-flex align-items-center flex-grow-1">
                    {/* 🔥 THAY THẾ PHẦN HIỂN THỊ TÊN */}
                    {isEditingName ? (
                        <div className="input-group input-group-sm me-3" style={{ maxWidth: '300px' }}>
                            <input
                                type="text"
                                className="form-control"
                                value={tempName}
                                onChange={(e) => setTempName(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleSaveName();
                                    if (e.key === 'Escape') setIsEditingName(false);
                                }}
                                autoFocus
                            />
                            <button
                                className="btn btn-success"
                                onClick={handleSaveName}
                                title="Lưu tên mới"
                            >
                                <i className="bi bi-check"></i>
                            </button>
                            <button
                                className="btn btn-secondary"
                                onClick={() => {
                                    setTempName(device.name);
                                    setIsEditingName(false);
                                }}
                                title="Hủy"
                            >
                                <i className="bi bi-x"></i>
                            </button>
                        </div>
                    ) : (
                        <div className="d-flex align-items-center me-3">
                            <h4 className="mb-0 me-2 text-truncate" style={{ maxWidth: '350px' }} title={device.name}>
                                {device.name}
                            </h4>
                            {canEdit && (
                                <button
                                    className="btn btn-link text-muted p-0 ms-1"
                                    onClick={() => {
                                        setTempName(device.name);
                                        setIsEditingName(true);
                                    }}
                                    title="Đổi tên trạm"
                                    style={{ fontSize: '1.1rem' }}
                                >
                                    <i className="bi bi-pencil-square"></i>
                                </button>
                            )}
                        </div>
                    )}

                    {/* Các Badge trạng thái */}
                    <div className="d-flex align-items-center">
                        {!device.is_synced && canEdit && (
                            <div className="me-2 position-relative group-hover-target">
                                <button
                                    className="btn btn-warning btn-sm text-dark fw-bold"
                                    onClick={() => {
                                        // Cho phép click để xem alert chi tiết nếu cần
                                        const errText = device.sync_errors ? device.sync_errors.join('\n') : "Đang chờ dữ liệu...";
                                        alert("CHI TIẾT SAI LỆCH:\n" + errText);
                                    }}
                                    title={device.sync_errors ? device.sync_errors.join('\n') : "Click để xem chi tiết"}
                                >
                                    <i className="bi bi-exclamation-triangle-fill me-1"></i>
                                    Không đồng bộ (Click xem)
                                </button>
                            </div>
                        )}

                         {/* Badge trạng thái (luôn hiển thị) */}
                        <div className="d-flex align-items-center gap-1">
                            <span className={`badge ${STATUS_CLASS_MAP[device.status] || 'bg-secondary'}`}>
                                {STATUS_TEXT_MAP[device.status] || device.status}
                            </span>
                            {device.version === window.APP_VERSION ? (
                                <i className="bi bi-check-circle-fill text-success" title={`Đã đồng bộ (${device.version})`} style={{ fontSize: '1rem' }}></i>
                            ) : (
                                <i className="bi bi-exclamation-triangle-fill text-warning" title={`Không đồng bộ (Agent: ${device.version || '??'} | Server: ${window.APP_VERSION})`} style={{ fontSize: '1rem' }}></i>
                            )}
                        </div>

                        {/* 3. BADGE LOẠI CHÍNH (Hiện VIRTUAL nếu là trạm ảo) */}
                        {device.detected_chip_type === 'VIRTUAL' && (
                            <span className="badge bg-info text-dark">
                                <i className="bi bi-cloud-check-fill me-1"></i>TRẠM ẢO
                            </span>
                        )}

                        {/* Badge LOCKED (luôn hiển thị nếu bị khóa) */}
                        {isDeviceLocked && (
                            <span className="badge bg-danger ms-2">
                                <i className="bi bi-lock-fill me-1"></i>LOCKED
                            </span>
                        )}
                    </div>
                </div>

                {/* Nút Reset */}
                {canEdit && !isEditingName && (
                    <button
                        className="btn btn-sm btn-outline-danger ms-2"
                        title="Reset Trạm về trạng thái ban đầu"
                        onClick={() => {
                            const confirmMsg = `⚠️ XÁC NHẬN RESET\n\nBạn có chắc chắn muốn reset trạm '${device.name}' về trạng thái ban đầu không?`;
                            if (window.confirm(confirmMsg)) {
                                onReset(device.serial);
                            }
                        }}
                    >
                        <i className="bi bi-arrow-counterclockwise"></i> Reset
                    </button>
                )}
            </div>
            <div className="card-body overflow-auto flex-grow-1 p-3">
                <div className="d-flex justify-content-between align-items-center mb-3">
                    {isAdmin ? (
                        <p className="text-white-50 small mb-0">Serial: {device.serial}</p>
                    ) : (
                        <div></div>
                    )}
                    <span className="badge bg-dark-subtle text-light border border-secondary">
                        {device.detected_chip_type || 'UNKNOWN'}
                    </span>
                </div>

                <ul className="nav nav-tabs mb-3" role="tablist">
                    {device.status === 'awaiting_license' && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'license' ? 'active' : ''}`} onClick={() => handleTabClick('license')}>
                                <i className="bi bi-key-fill me-1"></i>License
                            </button>
                        </li>
                    )}

                    {showConfigTabs && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'chip' ? 'active' : ''}`} onClick={() => handleTabClick('chip')}>
                                <i className="bi bi-cpu-fill me-1"></i>Kích hoạt & GNSS
                            </button>
                        </li>
                    )}

                    {showServiceAndDashboardTabs && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'service' ? 'active' : ''}`} onClick={() => handleTabClick('service')}>
                                <i className="bi bi-hdd-network-fill me-1"></i>Dịch vụ
                            </button>
                        </li>
                    )}

                    {showServiceAndDashboardTabs && canManageRovers && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'rovers' ? 'active' : ''}`} onClick={() => handleTabClick('rovers')}>
                                <i className="bi bi-person-circle me-1"></i>Quản lý Rover
                            </button>
                        </li>
                    )}

                    {(['online', 'sleep', 'configuring'].includes(device.status) || (device.status || '').startsWith('auto_setup')) ? (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => handleTabClick('dashboard')}>
                                <i className="bi bi-speedometer2 me-1"></i>Dashboard
                            </button>
                        </li>
                    ) : null}
                </ul>

                <div className="tab-content pt-3">
                    {renderTabContent()}
                </div>
            </div>
            {canEdit && (
                <div className="card-footer bg-dark border-top border-secondary p-3 mt-auto">
                    {/* CHỈ HIỂN THỊ NÚT KHÓA NẾU LÀ ADMIN */}
                    {isAdmin && (
                        <div id="admin-actions-section">
                            <div className="d-flex align-items-center justify-content-between">
                                <span className="text-muted small">
                                    <i className="bi bi-shield-lock-fill me-2 text-danger"></i>
                                    Admin Control (Chỉ Admin)
                                </span>
                                <div style={{ width: '60%' }}>
                                    {isDeviceLocked ? (
                                        <button onClick={() => onUnlock(device.serial)} className="btn btn-success w-100">
                                            <i className="bi bi-unlock-fill me-2"></i>Mở Khóa (Unlock)
                                        </button>
                                    ) : (
                                        <button onClick={() => onLock(device.serial)} className="btn btn-warning w-100 text-dark fw-bold">
                                            <i className="bi bi-lock-fill me-2"></i>Khóa Thiết Bị (Lock)
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Nếu là Coordinator thì không hiện gì hoặc hiện thông báo khác tùy bạn */}
                    {!isAdmin && (
                        <div className="text-center text-muted small">
                            <i class="bi bi-info-circle me-1"></i>
                            Bạn có quyền quản lý trạm này.
                        </div>
                    )}
                </div>
            )}
            {isAdmin && device && (
                <SystemInfoPanel
                    device={device}
                    isOpen={isSystemPanelOpen}
                    onClose={() => setIsSystemPanelOpen(false)}
                />
            )}
        </div>
    );
});
window.DetailsPanel = DetailsPanel;

// ===============================================================================
// === ALL STATIONS MAP COMPONENT
// ===============================================================================
const createColoredIcon = (color) => {
    return L.divIcon({
        className: 'custom-div-icon',
        html: `<div style="background-color: ${color}; width: 12px; height: 12px; border-radius: 50%; border: 2px solid white; box-shadow: 0 0 4px black;"></div>`,
        iconSize: [12, 12],
        iconAnchor: [6, 6]
    });
};

const getRoverStatusMeta = (status) => {
    if (status === 4) return { label: 'Fixed', color: '#22c55e' }; // Green
    if (status === 5) return { label: 'Float', color: '#38bdf8' }; // Light blue
    if (status === 0) return { label: '3D', color: '#ef4444' };    // Red
    return { label: 'Unknown', color: '#a3a3a3' };
};

const createRoverIcon = (color) => {
    const svg = `
        <svg fill="${color}" stroke="white" stroke-width="2" viewBox="0 0 24 24" width="24" height="24" style="filter: drop-shadow(0px 2px 3px rgba(0,0,0,0.8));">
            <path d="M12 2 L22 20 L2 20 Z" />
        </svg>
    `;
    return L.divIcon({
        className: 'custom-div-icon',
        html: svg,
        iconSize: [24, 24],
        iconAnchor: [12, 12]
    });
};

const createRoverMarker = (lat, lon, color) => {
    return L.marker([lat, lon], {
        icon: createRoverIcon(color),
        riseOnHover: true,
        keyboard: false
    });
};

const animateMarkerLatLng = (marker, lat, lon, durationMs = 650) => {
    if (!marker) return;

    if (marker.__animFrame) {
        cancelAnimationFrame(marker.__animFrame);
        marker.__animFrame = null;
    }

    const from = marker.getLatLng();
    const fromLat = Number(from.lat);
    const fromLon = Number(from.lng);
    const toLat = Number(lat);
    const toLon = Number(lon);

    if (!Number.isFinite(fromLat) || !Number.isFinite(fromLon) || !Number.isFinite(toLat) || !Number.isFinite(toLon)) {
        marker.setLatLng([lat, lon]);
        return;
    }

    const dLat = toLat - fromLat;
    const dLon = toLon - fromLon;
    if (Math.abs(dLat) < 0.0000001 && Math.abs(dLon) < 0.0000001) {
        return;
    }

    const start = performance.now();
    const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

    const step = (now) => {
        const t = Math.min(1, (now - start) / durationMs);
        const eased = easeOutCubic(t);
        marker.setLatLng([fromLat + dLat * eased, fromLon + dLon * eased]);
        if (t < 1) {
            marker.__animFrame = requestAnimationFrame(step);
        } else {
            marker.__animFrame = null;
        }
    };

    marker.__animFrame = requestAnimationFrame(step);
};

const getDistanceMeters = (lat1, lon1, lat2, lon2) => {
    const toRad = (deg) => (deg * Math.PI) / 180;
    const R = 6371000;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
        Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
};

const AllStationsMap = memo(function AllStationsMap({ devices, currentUser }) {
    const mapContainerRef = useRef(null);
    const mapInstanceRef = useRef(null);
    const roverClusterRef = useRef(null);
    const corsMarkersRef = useRef({});
    const roverMarkersRef = useRef({});
    const roverHashRef = useRef('');
    const [roverClients, setRoverClients] = useState([]);
    const [showCors, setShowCors] = useState(true);
    const [showRovers, setShowRovers] = useState(true);
    const [switchOrientation, setSwitchOrientation] = useState('horizontal');
    const [selectedMountpoints, setSelectedMountpoints] = useState([]);
    const [roverSearch, setRoverSearch] = useState('');
    const [mapViewportVersion, setMapViewportVersion] = useState(0);
    const [mapType, setMapType] = useState('m'); // 'y' = Hybrid, 'm' = Roadmap
    const tileLayerRef = useRef(null);
    const [deadbandMeters, setDeadbandMeters] = useState(1.5);
    const [isFilterOpen, setIsFilterOpen] = useState(false);
    const roverProcessFrameRef = useRef(null);
    const roverChunkSizeRef = useRef(24);

    // Nearest Station Feature States
    const [findingNearest, setFindingNearest] = useState(false);
    const [searchLocationQuery, setSearchLocationQuery] = useState('');
    const [isSearchingLoc, setIsSearchingLoc] = useState(false);
    const userMarkerRef = useRef(null);
    const polylinesRef = useRef([]); // Mảng chứa nhiều đường nối (online + offline)

    const canViewRovers = React.useMemo(() => {
        const role = String(currentUser?.role || '').toLowerCase();
        return role === 'admin' || role === 'viewer' || role === 'coordinator' || role === 'operator';
    }, [currentUser?.role]);

    // Các trạm mà coordinator có thể xem sẽ bị giới hạn bởi mountpoint
    const allowedMountpoints = React.useMemo(() => {
        const role = String(currentUser?.role || '').toLowerCase();
        if (role === 'admin') return null; // Admin sees all

        const mps = new Set();
        (devices || []).forEach(d => {
            const mp1 = d.service_config?.mountpoint1;
            const mp2 = d.service_config?.mountpoint2;
            if (mp1) mps.add(mp1);
            if (mp2) mps.add(mp2);
        });
        return mps;
    }, [devices, currentUser?.role]);

    const mountpointOptions = React.useMemo(() => {
        const mps = new Set();
        roverClients.forEach((rover) => {
            const mp = (rover.mountpoint || '').trim();
            if (mp) {
                if (allowedMountpoints === null || allowedMountpoints.has(mp)) {
                    mps.add(mp);
                }
            }
        });
        return Array.from(mps).sort((a, b) => a.localeCompare(b));
    }, [roverClients, allowedMountpoints]);

    const filteredRoverClients = React.useMemo(() => {
        const search = roverSearch.trim().toLowerCase();
        return roverClients.filter((rover) => {
            const mp = (rover.mountpoint || '').trim();

            // Block if not authorized to view this mountpoint
            if (allowedMountpoints !== null && !allowedMountpoints.has(mp)) {
                return false;
            }

            if (selectedMountpoints.length > 0 && !selectedMountpoints.includes(mp)) {
                return false;
            }
            if (!search) {
                return true;
            }
            const username = String(rover.username || '').toLowerCase();
            const receiverId = String(rover.receiver_id || '').toLowerCase();
            return username.includes(search) || receiverId.includes(search);
        });
    }, [roverClients, selectedMountpoints, roverSearch, allowedMountpoints]);

    useEffect(() => {
        let isMounted = true;

        const fetchRoverClients = async () => {
            if (!canViewRovers) {
                if (isMounted) {
                    setRoverClients([]);
                }
                roverHashRef.current = '';
                return;
            }

            try {
                const apiCaller = window.apiFetch || fetch;
                const response = await apiCaller('/api/caster/clienttable');
                if (!response || !response.ok) return;

                const payload = await response.json();
                const items = Array.isArray(payload.items) ? payload.items : [];

                const nextHash = items
                    .map((r) => `${r.username}|${r.receiver_id}|${r.mountpoint}|${r.latitude}|${r.longitude}|${r.status}|${r.nsv}`)
                    .join('#');

                if (nextHash === roverHashRef.current) {
                    return;
                }

                roverHashRef.current = nextHash;
                if (isMounted) {
                    setRoverClients(items);
                }
            } catch (error) {
                console.warn('Failed to fetch rover clienttable:', error);
            }
        };

        fetchRoverClients();
        const timer = setInterval(fetchRoverClients, 10000);

        return () => {
            isMounted = false;
            clearInterval(timer);
        };
    }, [canViewRovers]);

    useEffect(() => {
        if (!mapInstanceRef.current) {
            const map = L.map(mapContainerRef.current).setView([21.0285, 105.8542], 6);

            tileLayerRef.current = L.tileLayer(`https://{s}.google.com/vt/lyrs=${mapType}&x={x}&y={y}&z={z}`, {
                maxZoom: 20,
                subdomains: ['mt0', 'mt1', 'mt2', 'mt3']
            }).addTo(map);

            if (L.markerClusterGroup) {
                roverClusterRef.current = L.markerClusterGroup({
                    chunkedLoading: true,
                    chunkInterval: 40,
                    chunkDelay: 40,
                    removeOutsideVisibleBounds: true,
                    showCoverageOnHover: false,
                    spiderfyOnMaxZoom: false,
                    maxClusterRadius: 45,
                    disableClusteringAtZoom: 14
                });
                map.addLayer(roverClusterRef.current);
            }

            const handleViewportChanged = () => {
                setMapViewportVersion((v) => v + 1);
            };
            map.on('moveend zoomend', handleViewportChanged);
            map.__viewportHandler = handleViewportChanged;

            mapInstanceRef.current = map;
        }

        return () => {
            const map = mapInstanceRef.current;
            if (!map) return;

            Object.values(roverMarkersRef.current).forEach((marker) => {
                if (marker && marker.__animFrame) {
                    cancelAnimationFrame(marker.__animFrame);
                    marker.__animFrame = null;
                }
            });

            if (roverClusterRef.current) {
                roverClusterRef.current.clearLayers();
                roverClusterRef.current = null;
            }

            if (userMarkerRef.current) userMarkerRef.current = null;
            if (polylinesRef.current) polylinesRef.current = [];

            if (map.__viewportHandler) {
                map.off('moveend zoomend', map.__viewportHandler);
                delete map.__viewportHandler;
            }

            map.remove();
            mapInstanceRef.current = null;
            corsMarkersRef.current = {};
            roverMarkersRef.current = {};
        };
    }, []);

    // Cập nhật lớp bản đồ khi mapType thay đổi (Giao thông <-> Vệ tinh)
    useEffect(() => {
        if (tileLayerRef.current) {
            tileLayerRef.current.setUrl(`https://mt1.google.com/vt/lyrs=${mapType}&x={x}&y={y}&z={z}`);
        }
    }, [mapType]);

    useEffect(() => {
        const map = mapInstanceRef.current;
        if (!map) return;

        const corsMarkers = corsMarkersRef.current;
        const validCorsKeys = new Set();
        const bounds = [];

        if (showCors) {
            devices.forEach(device => {
                const coords = device.base_config?.coords || (
                    device.base_config?.lat !== undefined && device.base_config?.lon !== undefined
                        ? { lat: device.base_config.lat, lon: device.base_config.lon, alt: device.base_config.alt }
                        : null
                );
                if (coords && coords.lat && coords.lon) {
                    validCorsKeys.add(device.serial);
                    const lat = parseFloat(coords.lat);
                    const lon = parseFloat(coords.lon);
                    bounds.push([lat, lon]);

                    const displayName = device.name || 'Unknown Device';
                    const mapEffectiveStatus = getMapEffectiveStatus(device);
                    const isAdmin = currentUser?.role === 'admin';
                    const serialHtml = isAdmin ? `<small>${device.serial}</small>` : '';

                    const popupContent = `
                        <div style="color: #333; min-width: 180px;">
                            <strong>Trạm CORS: ${displayName}</strong> <br>
                            <span class="badge ${STATUS_CLASS_MAP[mapEffectiveStatus] || 'bg-secondary'}">${STATUS_TEXT_MAP[mapEffectiveStatus] || mapEffectiveStatus}</span><br>
                            ${serialHtml}
                        </div>
                    `;

                    if (corsMarkers[device.serial]) {
                        corsMarkers[device.serial].setLatLng([lat, lon]);
                        corsMarkers[device.serial].setIcon(createColoredIcon(getStatusColor(mapEffectiveStatus)));
                        corsMarkers[device.serial].setPopupContent(popupContent);
                    } else {
                        const marker = L.marker([lat, lon], {
                            icon: createColoredIcon(getStatusColor(mapEffectiveStatus))
                        }).addTo(map).bindPopup(popupContent);

                        marker.on('mouseover', function () {
                            this.openPopup();
                        });

                        corsMarkers[device.serial] = marker;
                    }
                }
            });
        }

        Object.keys(corsMarkers).forEach((serial) => {
            if (!showCors || !validCorsKeys.has(serial)) {
                map.removeLayer(corsMarkers[serial]);
                delete corsMarkers[serial];
            }
        });

        if (bounds.length > 0 && !map.fitted) {
            map.fitBounds(bounds, { padding: [50, 50] });
            map.fitted = true;
        }
    }, [devices, currentUser, showCors]);

    useEffect(() => {
        const map = mapInstanceRef.current;
        if (!map) return;

        if (roverProcessFrameRef.current) {
            cancelAnimationFrame(roverProcessFrameRef.current);
            roverProcessFrameRef.current = null;
        }

        const roverMarkers = roverMarkersRef.current;
        const roverCluster = roverClusterRef.current;
        const validRoverKeys = new Set();
        const bounds = [];
        const viewBounds = map.getBounds().pad(0.25);
        const MIN_MOVE_METERS = Number.isFinite(deadbandMeters) ? Math.max(0, deadbandMeters) : 1.5;
        const MAX_SMOOTH_MOVE_METERS = 100;
        const MIN_CHUNK = 8;
        const MAX_CHUNK = 56;

        const ensureMarkerVisible = (marker) => {
            if (!marker) return;
            if (roverCluster) {
                if (!roverCluster.hasLayer(marker)) {
                    roverCluster.addLayer(marker);
                }
            } else if (!map.hasLayer(marker)) {
                marker.addTo(map);
            }
        };

        const ensureMarkerHidden = (marker) => {
            if (!marker) return;
            if (roverCluster) {
                if (roverCluster.hasLayer(marker)) {
                    roverCluster.removeLayer(marker);
                }
            } else if (map.hasLayer(marker)) {
                map.removeLayer(marker);
            }
        };

        const visibleRovers = showRovers && canViewRovers
            ? filteredRoverClients.filter((rover) => {
                const lat = Number(rover.latitude);
                const lon = Number(rover.longitude);
                const isVisible = rover.show_position !== false; // Default to true if undefined
                return Number.isFinite(lat) && Number.isFinite(lon) && viewBounds.contains([lat, lon]) && isVisible;
            })
            : [];

        let cursor = 0;
        const processChunk = () => {
            const currentChunkSize = roverChunkSizeRef.current;
            const chunkStartTs = performance.now();
            const end = Math.min(cursor + currentChunkSize, visibleRovers.length);

            for (let i = cursor; i < end; i++) {
                const rover = visibleRovers[i];
                const lat = Number(rover.latitude);
                const lon = Number(rover.longitude);
                const key = `${rover.username || 'unknown'}_${rover.receiver_id || '0'}`;
                validRoverKeys.add(key);
                bounds.push([lat, lon]);

                const statusMeta = getRoverStatusMeta(rover.status);
                const popupContent = `
                    <div style="color: #333; min-width: 180px;">
                        <strong>Rover: ${rover.username || 'N/A'}</strong><br>
                        <span class="badge text-bg-dark">RX: ${rover.receiver_id || 'N/A'}</span>
                        <span class="badge text-bg-primary ms-1">${rover.mountpoint || 'N/A'}</span><br>
                        Status: <b>${statusMeta.label}</b> | nSV: <b>${rover.nsv ?? 'N/A'}</b>
                    </div>
                `;

                if (roverMarkers[key]) {
                    const marker = roverMarkers[key];
                    const current = marker.getLatLng();
                    const distance = getDistanceMeters(current.lat, current.lng, lat, lon);

                    if (distance >= MIN_MOVE_METERS) {
                        if (distance <= MAX_SMOOTH_MOVE_METERS) {
                            animateMarkerLatLng(marker, lat, lon, 550);
                        } else {
                            marker.setLatLng([lat, lon]);
                        }
                    }

                    if (marker.__statusColor !== statusMeta.color) {
                        marker.setIcon(createRoverIcon(statusMeta.color));
                        marker.__statusColor = statusMeta.color;
                    }
                    if (marker.__popupHtml !== popupContent) {
                        marker.setPopupContent(popupContent);
                        marker.__popupHtml = popupContent;
                    }
                    ensureMarkerVisible(marker);
                } else {
                    const marker = createRoverMarker(lat, lon, statusMeta.color).bindPopup(popupContent);
                    marker.__statusColor = statusMeta.color;
                    marker.__popupHtml = popupContent;
                    roverMarkers[key] = marker;
                    ensureMarkerVisible(marker);
                }
            }

            cursor = end;

            // Adaptive FPS guard:
            // - If a chunk takes too long, reduce work per frame.
            // - If chunks are consistently fast, increase gradually.
            const elapsed = performance.now() - chunkStartTs;
            if (elapsed > 14 && roverChunkSizeRef.current > MIN_CHUNK) {
                roverChunkSizeRef.current = Math.max(MIN_CHUNK, roverChunkSizeRef.current - 4);
            } else if (elapsed < 7 && roverChunkSizeRef.current < MAX_CHUNK) {
                roverChunkSizeRef.current = Math.min(MAX_CHUNK, roverChunkSizeRef.current + 2);
            }

            if (cursor < visibleRovers.length) {
                roverProcessFrameRef.current = requestAnimationFrame(processChunk);
                return;
            }

            roverProcessFrameRef.current = null;

            Object.keys(roverMarkers).forEach((key) => {
                if (!showRovers || !validRoverKeys.has(key)) {
                    if (roverMarkers[key].__animFrame) {
                        cancelAnimationFrame(roverMarkers[key].__animFrame);
                        roverMarkers[key].__animFrame = null;
                    }
                    ensureMarkerHidden(roverMarkers[key]);
                    delete roverMarkers[key];
                }
            });

            if (bounds.length > 0 && !map.fitted) {
                map.fitBounds(bounds, { padding: [50, 50] });
                map.fitted = true;
            }
        };

        processChunk();

        return () => {
            if (roverProcessFrameRef.current) {
                cancelAnimationFrame(roverProcessFrameRef.current);
                roverProcessFrameRef.current = null;
            }
        };

    }, [filteredRoverClients, showRovers, mapViewportVersion, canViewRovers, deadbandMeters]);

    useEffect(() => {
        if (selectedMountpoints.length === 0) return;
        const valid = new Set(mountpointOptions);
        const next = selectedMountpoints.filter((mp) => valid.has(mp));
        if (next.length !== selectedMountpoints.length) {
            setSelectedMountpoints(next);
        }
    }, [mountpointOptions, selectedMountpoints]);

    const toggleMountpoint = (mp) => {
        setSelectedMountpoints((prev) => {
            if (prev.includes(mp)) {
                return prev.filter((item) => item !== mp);
            }
            return [...prev, mp];
        });
    };

    const findNearestFromCoords = (lat, lon, locationName = "Vị trí của bạn") => {
        // Helper: extract coords from device consistently
        const getDeviceCoords = (d) => {
            const bc = d?.base_config;
            if (!bc) return null;
            if (bc.coords && bc.coords.lat && bc.coords.lon) return bc.coords;
            if (bc.lat !== undefined && bc.lon !== undefined) return { lat: bc.lat, lon: bc.lon, alt: bc.alt };
            return null;
        };
        // Lấy tất cả các trạm có tọa độ
        const validDevices = devices.filter(d => getDeviceCoords(d) !== null);

        if (validDevices.length === 0) {
            if (window.showError) window.showError("Không có trạm CORS nào có tọa độ hợp lệ trên hệ thống.");
            return;
        }

        // Tách riêng trạm kết nối (online hoặc có BPS > 0) và offline
        // BPS > 0 có nghĩa là trạm vẫn truyền dữ liệu (kể cả khi sleep)
        const connectedDevices = validDevices.filter(d => 
            d.status === 'online' || (d.bps && d.bps > 0)
        );
        const offlineDevices = validDevices.filter(d => 
            d.status !== 'online' && (!d.bps || d.bps === 0)
        );

        // Hàm tính khoảng cách
        const calcDist = (d) => {
            const coords = getDeviceCoords(d);
            return window.getDistanceMeters 
                ? window.getDistanceMeters(lat, lon, parseFloat(coords.lat), parseFloat(coords.lon)) 
                : getDistanceMeters(lat, lon, parseFloat(coords.lat), parseFloat(coords.lon));
        };

        // Tìm trạm gần nhất trong 1 danh sách
        const findNearest = (list) => {
            let best = null, bestDist = Infinity;
            list.forEach(d => {
                const dist = calcDist(d);
                if (dist < bestDist) { bestDist = dist; best = d; }
            });
            return best ? { device: best, dist: bestDist } : null;
        };

        const nearestConnected = findNearest(connectedDevices);
        const nearestOffline = findNearest(offlineDevices);

        if (!nearestConnected && !nearestOffline) {
            if (window.showError) window.showError("Không tìm thấy trạm CORS nào phù hợp.");
            return;
        }

        // Thông báo kết quả
        const textPrefix = locationName === "Vị trí của bạn" ? "Trạm gần nhất" : `Gần ${locationName.split(',')[0]} nhất`;
        let msg = [];
        if (nearestConnected) msg.push(`🟢 CONNECTED: ${nearestConnected.device.name || nearestConnected.device.serial} (${(nearestConnected.dist/1000).toFixed(2)} km)`);
        if (nearestOffline) msg.push(`🔴 OFFLINE: ${nearestOffline.device.name || nearestOffline.device.serial} (${(nearestOffline.dist/1000).toFixed(2)} km)`);
        if (window.showSuccess) window.showSuccess(`${textPrefix} — ${msg.join(' | ')}`);

        const map = mapInstanceRef.current;
        if (map) {
            // Xóa các đường cũ
            if (userMarkerRef.current) map.removeLayer(userMarkerRef.current);
            polylinesRef.current.forEach(item => {
                map.removeLayer(item.line);
                if (item.tooltip) map.removeLayer(item.tooltip);
            });
            polylinesRef.current = [];

            // Marker vị trí người dùng
            const iconHtml = locationName === "Vị trí của bạn"
                ? `<div style="background-color: #007bff; width: 16px; height: 16px; border-radius: 50%; border: 3px solid white; box-shadow: 0 0 6px rgba(0,0,0,0.5);"></div>`
                : `<div style="background-color: #ffc107; width: 16px; height: 16px; border-radius: 50%; border: 3px solid white; box-shadow: 0 0 6px rgba(0,0,0,0.5);"></div>`;
            const userIcon = L.divIcon({ className: 'custom-div-icon', html: iconHtml, iconSize: [16, 16], iconAnchor: [8, 8] });
            userMarkerRef.current = L.marker([lat, lon], { icon: userIcon }).addTo(map)
                .bindPopup(`<b>${locationName.split('(')[0]}</b>`).openPopup();

            // Hàm vẽ 1 đường nối
            const drawLine = (target, color, label) => {
                if (!target) return null;
                const tLat = parseFloat(target.device.base_config.coords.lat);
                const tLon = parseFloat(target.device.base_config.coords.lon);
                const distKm = (target.dist / 1000).toFixed(2);
                const stationName = target.device.name || target.device.serial;

                const line = L.polyline([[lat, lon], [tLat, tLon]], {
                    color: color,
                    weight: 5,
                    opacity: 1.0,
                    dashArray: '12, 12',
                    lineCap: 'round',
                    lineJoin: 'round'
                }).addTo(map);

                line.bindTooltip(
                    `<div style="font-size: 13px; font-weight: bold; color: ${color}; text-shadow: 1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000; white-space: nowrap;">
                        ${label} ${stationName}: ${distKm} km
                    </div>`,
                    { permanent: true, direction: 'center', className: 'bg-transparent border-0 shadow-none' }
                ).openTooltip();

                return { line };
            };

            // Vẽ đường xanh lá đến trạm kết nối gần nhất (online hoặc có BPS)
            const connectedLine = drawLine(nearestConnected, '#00ff00', '🟢');
            if (connectedLine) polylinesRef.current.push(connectedLine);

            // Vẽ đường đỏ đến trạm offline gần nhất
            const offlineLine = drawLine(nearestOffline, '#ff3333', '🔴');
            if (offlineLine) polylinesRef.current.push(offlineLine);

            // Zoom bao trọn tất cả đường nối
            const allBounds = L.latLngBounds([[lat, lon]]);
            if (nearestConnected) allBounds.extend([parseFloat(nearestConnected.device.base_config.coords.lat), parseFloat(nearestConnected.device.base_config.coords.lon)]);
            if (nearestOffline) allBounds.extend([parseFloat(nearestOffline.device.base_config.coords.lat), parseFloat(nearestOffline.device.base_config.coords.lon)]);
            map.flyToBounds(allBounds, { padding: [60, 60], maxZoom: 14, duration: 1.5 });
        }
    };

    const handleFindNearest = () => {
        if (!navigator.geolocation) {
            if (window.showError) window.showError("Trình duyệt không hỗ trợ Geolocation.");
            return;
        }

        setFindingNearest(true);
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const lat = pos.coords.latitude;
                const lon = pos.coords.longitude;
                findNearestFromCoords(lat, lon, "Vị trí của bạn");
                setFindingNearest(false);
            },
            (err) => {
                setFindingNearest(false);
                if (window.showError) window.showError("Không thể lấy vị trí hiện tại.");
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    };

    const handleSearchLocation = async () => {
        const query = searchLocationQuery.trim();
        if (!query) return;

        setIsSearchingLoc(true);
        try {

            const response = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&countrycodes=vn&limit=1`);
            const data = await response.json();

            if (data && data.length > 0) {
                const lat = parseFloat(data[0].lat);
                const lon = parseFloat(data[0].lon);
                findNearestFromCoords(lat, lon, data[0].display_name);
            } else {
                if (window.showError) window.showError(`Không tìm thấy địa danh: "${query}". Vui lòng thử nhập kèm Tên Huyện, Tỉnh.`);
            }
        } catch (error) {
            console.error(error);
            if (window.showError) window.showError("Lỗi kết nối khi tìm kiếm địa điểm.");
        } finally {
            setIsSearchingLoc(false);
        }
    };

    return (
        <div style={{ position: 'relative' }}>
            <style>{`
                @keyframes mapFilterFadeIn {
                    from { opacity: 0; transform: translateY(-5px); }
                    to { opacity: 1; transform: translateY(0); }
                }
            `}</style>
            <div
                style={{
                    position: 'absolute',
                    top: '12px',
                    left: '55px', // Tránh nút +/- của Leaflet
                    zIndex: 1000,
                    maxWidth: 'calc(100% - 70px)' // Đảm bảo không bị tràn trên màn hình nhỏ
                }}
            >
                <div className="d-flex flex-wrap gap-2 align-items-center">
                    {/* Nút bật/tắt Popup Filter */}
                    <button
                        className="btn text-light fw-bold flex-shrink-0"
                        onClick={() => setIsFilterOpen(!isFilterOpen)}
                        style={{
                            background: 'rgba(8, 12, 20, 0.85)',
                            border: '1px solid rgba(255, 255, 255, 0.3)',
                            borderRadius: '8px',
                            padding: '8px 16px',
                            boxShadow: '0 4px 6px rgba(0,0,0,0.6)',
                            whiteSpace: 'nowrap'
                        }}
                    >
                        <i className={`bi ${isFilterOpen ? 'bi-x-circle-fill text-warning' : 'bi-funnel-fill'} me-2`}></i>
                        Bộ lọc
                    </button>

                    {/* Nút Tìm trạm gần nhất */}
                    {currentUser && (
                        <button
                            className="btn text-light fw-bold btn-primary border-0 flex-shrink-0"
                            onClick={handleFindNearest}
                            disabled={findingNearest}
                            style={{
                                background: findingNearest ? '#6c757d' : '#0d6efd',
                                borderRadius: '8px',
                                padding: '8px 16px',
                                boxShadow: '0 4px 6px rgba(0,0,0,0.6)',
                                transition: 'background-color 0.2s',
                                whiteSpace: 'nowrap'
                            }}
                            title="Lấy vị trí của bạn và tìm trạm gần nhất"
                        >
                            {findingNearest ? (
                                <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Đang tìm...</>
                            ) : (
                                <><i className="bi bi-geo-alt-fill me-2"></i>Tìm trạm gần nhất</>
                            )}
                        </button>
                    )}

                    {/* Ô Tìm kiếm địa điểm */}
                    {currentUser && (
                        <div className="input-group" style={{ width: 'auto', minWidth: '220px', flex: '1 1 auto', boxShadow: '0 4px 6px rgba(0,0,0,0.6)' }}>
                            <input
                                type="text"
                                className="form-control bg-dark text-light border-secondary"
                                placeholder="Tên Xã, Huyện, Tỉnh..."
                                value={searchLocationQuery}
                                onChange={(e) => setSearchLocationQuery(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleSearchLocation();
                                }}
                                style={{ borderRadius: '8px 0 0 8px' }}
                            />
                            <button
                                className="btn btn-primary border-primary flex-shrink-0"
                                onClick={handleSearchLocation}
                                disabled={isSearchingLoc || !searchLocationQuery.trim()}
                                style={{ borderRadius: '0 8px 8px 0' }}
                            >
                                {isSearchingLoc
                                    ? <span className="spinner-border spinner-border-sm"></span>
                                    : <i className="bi bi-search"></i>}
                            </button>
                        </div>
                    )}
                </div>

                {/* Khung chứa các Filter, chỉ hiện khi isFilterOpen = true */}
                {isFilterOpen && (
                    <div
                        className="d-flex align-items-center gap-2 mt-2"
                        style={{
                            background: 'rgba(8, 12, 20, 0.95)',
                            border: '1px solid rgba(255, 255, 255, 0.2)',
                            borderRadius: '10px',
                            padding: '12px',
                            flexDirection: switchOrientation === 'vertical' ? 'column' : 'row',
                            boxShadow: '0 8px 16px rgba(0,0,0,0.7)',
                            animation: 'mapFilterFadeIn 0.2s ease-out forwards'
                        }}
                    >
                        <button
                            className="btn btn-sm btn-outline-light"
                            onClick={() => setSwitchOrientation((prev) => prev === 'horizontal' ? 'vertical' : 'horizontal')}
                            title="Đổi nút gạt ngang/dọc"
                        >
                            <i className={`bi ${switchOrientation === 'horizontal' ? 'bi-distribute-horizontal' : 'bi-distribute-vertical'}`}></i>
                        </button>

                        <div className="form-check form-switch m-0 d-flex align-items-center" style={{ gap: '0.5rem' }}>
                            <input
                                className="form-check-input m-0 mt-1"
                                type="checkbox"
                                role="switch"
                                id="toggle-cors-layer"
                                checked={showCors}
                                onChange={(e) => setShowCors(e.target.checked)}
                            />
                            <label className="form-check-label text-light small fw-bold m-0" htmlFor="toggle-cors-layer">
                                Trạm CORS
                            </label>
                        </div>

                        {canViewRovers && (
                            <>
                                <div className="form-check form-switch m-0 d-flex align-items-center" style={{ gap: '0.5rem' }}>
                                    <input
                                        className="form-check-input m-0 mt-1"
                                        type="checkbox"
                                        role="switch"
                                        id="toggle-rover-layer"
                                        checked={showRovers}
                                        onChange={(e) => setShowRovers(e.target.checked)}
                                    />
                                    <label className="form-check-label text-light small fw-bold m-0" htmlFor="toggle-rover-layer">
                                        Rover
                                    </label>
                                </div>

                                <div className="form-check form-switch m-0 d-flex align-items-center" style={{ gap: '0.5rem' }}>
                                    <input
                                        className="form-check-input m-0 mt-1"
                                        type="checkbox"
                                        role="switch"
                                        id="toggle-map-satellite"
                                        checked={mapType === 'y'}
                                        onChange={(e) => setMapType(e.target.checked ? 'y' : 'm')}
                                    />
                                    <label className="form-check-label text-light small fw-bold m-0" htmlFor="toggle-map-satellite">
                                        Vệ tinh (Google)
                                    </label>
                                </div>

                                <div className="dropdown" data-bs-auto-close="outside">
                                    <button
                                        className="btn btn-sm btn-outline-light dropdown-toggle"
                                        type="button"
                                        data-bs-toggle="dropdown"
                                        aria-expanded="false"
                                        title="Chọn nhiều mountpoint bằng cách bấm trực tiếp"
                                    >
                                        {selectedMountpoints.length > 0
                                            ? `MP (${selectedMountpoints.length})`
                                            : 'MP: ALL'}
                                    </button>
                                    <ul className="dropdown-menu dropdown-menu-dark p-2" style={{ minWidth: '240px', maxHeight: '260px', overflowY: 'auto' }}>
                                        <li className="mb-2">
                                            <button
                                                type="button"
                                                className="btn btn-sm btn-outline-info w-100"
                                                onClick={() => setSelectedMountpoints([])}
                                            >
                                                Chọn tất cả Mountpoint
                                            </button>
                                        </li>
                                        {mountpointOptions.length === 0 && (
                                            <li className="dropdown-item-text text-muted small">Không có mountpoint</li>
                                        )}
                                        {mountpointOptions.map((mp) => {
                                            const checked = selectedMountpoints.includes(mp);
                                            return (
                                                <li key={mp} className="mb-1">
                                                    <button
                                                        type="button"
                                                        className={`btn btn-sm w-100 text-start ${checked ? 'btn-primary' : 'btn-outline-secondary'}`}
                                                        onClick={() => toggleMountpoint(mp)}
                                                    >
                                                        {checked ? '✓ ' : ''}{mp}
                                                    </button>
                                                </li>
                                            );
                                        })}
                                    </ul>
                                </div>

                                <input
                                    type="text"
                                    className="form-control form-control-sm bg-dark text-light border-secondary"
                                    style={{ minWidth: '180px' }}
                                    placeholder="Lọc rover (user/RX)"
                                    value={roverSearch}
                                    onChange={(e) => setRoverSearch(e.target.value)}
                                />

                            </>
                        )}
                    </div>
                )}
            </div>

            <div
                ref={mapContainerRef}
                style={{
                    width: '100%',
                    height: 'calc(100vh - 150px)',
                    borderRadius: '8px',
                    border: '2px solid #444',
                    background: '#222'
                }}
            />
        </div>
    );
});
window.AllStationsMap = AllStationsMap;

console.log('✓ React Components loaded and assigned to window object.');
