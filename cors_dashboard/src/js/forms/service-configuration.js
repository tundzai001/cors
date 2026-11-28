import { createDefaultServiceFormState, getCasterIds, toBool, useHydrateServiceForm } from './service-form-state.js';
import { ServiceCommonSettings } from './service-form-sections.js';

(() => {
const { useState, memo, useRef } = React;

const getCasterNoDataReason = (sleeping, startup, runningAutoBase, device) => {
    if (sleeping) return 'Đang chờ yêu cầu phát từ rover.';
    if (startup.state === 'error') return startup.error || 'Thiết lập Base thất bại; xem trạng thái GNSS.';
    if (startup.state === 'awaiting_auto_base') return 'Đang chờ bắt đầu Auto Base.';
    if (runningAutoBase) return `Đang chạy Auto Base (${device.auto_base_progress?.phase || 'đang xử lý'}); chưa phát RTCM Base.`;
    if (device.ntrip_stats?.rtcm_input_bps === 0) return 'ESP không nhận được RTCM từ chip GNSS.';
    return 'Chưa xác định được nguyên nhân từ trạng thái thiết bị hiện tại.';
};
const getCasterStatusPresentation = (blocked, lockApplied, enabled, pending, offline, telemetry) => {
    if (blocked) return lockApplied
        ? { label: 'Đã khoá · ngừng gửi', icon: 'bi-lock-fill', tone: 'danger' }
        : { label: 'Khoá · chờ áp dụng', icon: 'bi-lock-fill', tone: 'danger' };
    if (!enabled) return { label: 'Đã tắt', icon: 'bi-power', tone: 'secondary' };
    if (pending) return { label: 'Chưa áp dụng', icon: 'bi-pencil-square', tone: 'warning' };
    if (offline) return { label: 'Trạm mất kết nối', icon: 'bi-cloud-slash', tone: 'danger' };
    if (telemetry.connected) return { label: 'Đã kết nối Caster', icon: 'bi-check-circle-fill', tone: 'success' };
    if (telemetry.hasConnection || telemetry.hasBps) {
        return { label: 'Chưa kết nối Caster', icon: 'bi-exclamation-circle', tone: 'warning' };
    }
    return { label: 'Chờ trạng thái', icon: 'bi-hourglass-split', tone: 'secondary' };
};
const getCasterActivityPresentation = (sleeping, bps) => {
    if (sleeping) return { label: 'Ngủ · chờ rover', tone: 'info' };
    if (bps > 0) return { label: `Đang gửi RTCM · ${bps} B/s`, tone: 'success' };
    return { label: 'Chưa gửi RTCM', tone: 'secondary' };
};
function applyGlobalOnDemandChange(next, name, checked, serverIds) {
    if (name !== 'stream_on_demand') return;
    next.server1_stream_on_demand = !!checked;
    next.server2_stream_on_demand = !!checked;
    next.stream_active = !checked;
    next.server1_stream_active = !checked;
    next.server2_stream_active = !checked;
    for (const sid of serverIds) {
        next[`server${sid}_stream_on_demand`] = !!checked;
        next[`server${sid}_stream_active`] = !checked;
    }
}

function applyServerOnDemandChange(next, name, checked) {
    const streamMatch = name.match(/^server(\d+)_stream_on_demand$/);
    if (streamMatch) next[`server${streamMatch[1]}_stream_active`] = !checked;
    if (name === 'server1_stream_on_demand') next.server1_stream_active = !checked;
    if (name === 'server2_stream_on_demand') next.server2_stream_active = !checked;
}

function buildServiceFormState(previous, name, inputValue, checked, serverIds) {
    const next = { ...previous, [name]: inputValue };
    applyGlobalOnDemandChange(next, name, checked, serverIds);
    applyServerOnDemandChange(next, name, checked);
    return next;
}

function buildServiceSubmissionPayload(formState, serverIds, toBool) {
    const derivedGlobalOnDemand = serverIds.some(sid => toBool(formState[`server${sid}_enabled`]) && toBool(formState[`server${sid}_stream_on_demand`]));
    const derivedGlobalActive = serverIds.some(sid => toBool(formState[`server${sid}_enabled`])
        && (!toBool(formState[`server${sid}_stream_on_demand`]) || toBool(formState[`server${sid}_stream_active`])));
    const payload = {
        ...formState,
        stream_on_demand: derivedGlobalOnDemand,
        stream_active: derivedGlobalActive,
        ntrip_version1: Number.parseInt(formState.ntrip_version1, 10),
        ntrip_version2: Number.parseInt(formState.ntrip_version2, 10),
        port1: formState.port1 ? Number.parseInt(formState.port1, 10) : null,
        port2: formState.port2 ? Number.parseInt(formState.port2, 10) : null,
        command_port: formState.command_port ? Number.parseInt(formState.command_port, 10) : null,
        rtcmntripversion1: Number.parseInt(formState.rtcmntripversion1, 10) === 2 ? 2 : 1,
        rtcmport1: formState.rtcmport1 ? Number.parseInt(formState.rtcmport1, 10) : null,
        reconnectioninterval: Number.parseInt(formState.reconnectioninterval, 10),
    };
    for (const sid of serverIds) {
        payload[`ntrip_version${sid}`] = Number.parseInt(formState[`ntrip_version${sid}`] || 1, 10);
        payload[`port${sid}`] = formState[`port${sid}`] ? Number.parseInt(formState[`port${sid}`], 10) : null;
    }
    return payload;
}

function removeLteUnsupportedServiceFields(payload, isLteStation) {
    if (!isLteStation) return;
    delete payload.command_host;
    delete payload.command_port;
    delete payload.command_pass;
    delete payload.command_mp;
    Object.assign(payload, {
        rtcm_enabled: false,
        rtcmserver1: null,
        rtcmport1: null,
        rtcmmountpoint1: null,
        rtcmusername1: null,
        rtcmpassword1: null,
    });
}

function removeLockedServiceFields(payload, { hostsLocked, isAdmin, hostPolicy, serverIds, blockedServer }) {
    if (!hostsLocked || isAdmin) return;
    for (const key of Object.keys(hostPolicy)) delete payload[key];
    for (const sid of serverIds) {
        delete payload[`server${sid}_stream_on_demand`];
        delete payload[`server${sid}_stream_active`];
        if (blockedServer(sid)) {
            for (const prefix of ['ntrip_version', 'username', 'mountpoint', 'password']) delete payload[`${prefix}${sid}`];
        }
    }
    delete payload.stream_on_demand;
    delete payload.stream_active;
}

function removeUnchangedServiceFields(payload, formState, hydratedConfig, serverIds) {
    const streamSettingsChanged = serverIds.some(sid => ['enabled', 'stream_on_demand', 'stream_active'].some(suffix => {
        const key = `server${sid}_${suffix}`;
        return String(formState[key] ?? '') !== String(hydratedConfig[key] ?? '');
    }));
    if (!streamSettingsChanged) {
        delete payload.stream_on_demand;
        delete payload.stream_active;
    }
    for (const key of Object.keys(payload)) {
        if (String(payload[key] ?? '') === String(hydratedConfig[key] ?? '')) delete payload[key];
    }
}

const ServiceConfigForm = memo(function ServiceConfigForm({ device, currentUser = {}, onConfigureService, disabled }) {
    const [formState, setFormState] = useState(createDefaultServiceFormState);
    const serverIds = getCasterIds(formState);
    const [removedServers, setRemovedServers] = useState([]);
    const addCaster = () => {
        const sid = Math.max(...serverIds, ...removedServers) + 1;
        setIsFormDirty(true);
        setFormState(prev => ({ ...prev, [`server${sid}_enabled`]: false,
            [`server${sid}_stream_on_demand`]: true, [`server${sid}_stream_active`]: false,
            [`ntrip_version${sid}`]: 1, [`serverhost${sid}`]: '', [`port${sid}`]: '',
            [`username${sid}`]: '', [`password${sid}`]: '', [`mountpoint${sid}`]: '' }));
    };
    const [visiblePasswords, setVisiblePasswords] = useState({});
    const isAdmin = currentUser?.role === 'admin';
    const hostPolicy = device.server_config_lock || {};
    const lockedServers = window.CorsDashboardShared.getLockedServers(device);
    const hostsLocked = lockedServers.length > 0;
    const lockApplied = window.CorsDashboardShared.isDeviceLockApplied(device);
    const blockedServer = sid => lockedServers.includes(sid);
    const lockedInput = name => !isAdmin && hostsLocked && Object.prototype.hasOwnProperty.call(hostPolicy, name);

    const [isSubmitting, setIsSubmitting] = useState(false);
    const [isFormDirty, setIsFormDirty] = useState(false);
    const lastHydratedSerialRef = useRef(null);
    const hydratedConfigRef = useRef({});
    const canManageStationMode = !hostsLocked && (
        currentUser?.role === 'admin' ||
        currentUser?.permissions?.includes('manage:station_mode') ||
        currentUser?.permissions?.includes('MANAGE_STATION_MODE')) || isAdmin;
    const isLteStation = !!device?.is_lte || String(device?.connection_type || '').toUpperCase() === 'LTE';
    const removeCaster = async sid => {
        if (sid <= 2 || disabled || isSubmitting || (!isAdmin && hostsLocked)) return;
        const confirmed = await window.CorsUI.confirmAction({
            title: `Xoá Host ${sid}`,
            message: `Xoá cấu hình Host ${sid}? Bấm Áp dụng để lưu và ngừng gửi dữ liệu tới host này.`,
            confirmText: 'Xoá host', tone: 'danger'
        });
        if (!confirmed) return;
        setFormState(prev => window.CorsDashboardShared.removeServiceServers(prev, [sid]));
        setRemovedServers(prev => [...new Set([...prev, sid])]);
        setIsFormDirty(true);
    };

    // Common section should only show stream toggles for servers that are currently enabled.
    const showS1StreamToggle = toBool(formState.server1_enabled, false);
    const showS2StreamToggle = toBool(formState.server2_enabled, false);

    useHydrateServiceForm({
        device, isAdmin, isFormDirty, hostPolicy, lastHydratedSerialRef, hydratedConfigRef,
        setVisiblePasswords, setRemovedServers, setFormState, setIsFormDirty,
    });

    const renderCasterStatus = (serverId) => {
        const key = `server${serverId}`;
        const saved = device.service_config || {};
        const enabled = toBool(formState[`${key}_enabled`]);
        const pending = ['serverhost', 'port', 'mountpoint', 'username'].some(prefix =>
            String(formState[`${prefix}${serverId}`] ?? '') !== String(saved[`${prefix}${serverId}`] ?? '')
        ) || enabled !== toBool(saved[`${key}_enabled`]);
        const telemetry = window.CorsDashboardShared?.getServerTelemetry(device, serverId) || {};
        const offline = !!window.CorsDashboardShared?.getOfflineInfo(device) || device.status === 'offline';
        const blocked = blockedServer(serverId);
        const { label, icon, tone } = getCasterStatusPresentation(blocked, lockApplied, enabled, pending, offline, telemetry);
        const sleeping = telemetry.sleep ?? (
            toBool(telemetry.onDemand, toBool(saved[`${key}_stream_on_demand`], toBool(saved.stream_on_demand))) &&
            !toBool(telemetry.active, toBool(saved[`${key}_stream_active`], toBool(saved.stream_active)))
        );
        const activity = getCasterActivityPresentation(sleeping, telemetry.bps);
        const casterError = device.ntrip_status?.[key]?.error;
        const startup = device.system_info?.base_startup || {};
        const runningAutoBase = String(device.status || '').startsWith('auto_setup');
        const noDataReason = getCasterNoDataReason(sleeping, startup, runningAutoBase, device);
        const showActivity = enabled && !blocked && !pending && !offline;
        const showCasterError = enabled && !pending && !offline && !telemetry.connected && !sleeping && Boolean(casterError);
        const showNoData = enabled && !blocked && !pending && !offline && telemetry.connected && telemetry.bps <= 0;
        return (
            <span className="d-inline-flex flex-wrap align-items-center gap-2 ms-2 small" role="status" aria-live="polite">
                <span className={`badge text-bg-${enabled ? 'primary' : 'secondary'}`}>
                    <i className={`bi ${enabled ? 'bi-toggle-on' : 'bi-toggle-off'} me-1`} aria-hidden="true"></i>
                    {enabled ? 'Đã bật' : 'Đã tắt'}
                </span>
                <span className={`badge text-bg-${tone}`}>
                    <i className={`bi ${icon} me-1`} aria-hidden="true"></i>{label}
                </span>
                {showActivity && (
                    <span className={`badge text-bg-${activity.tone}`}>
                        <i className={`bi ${sleeping ? 'bi-moon-stars' : 'bi-broadcast'} me-1`} aria-hidden="true"></i>
                        {activity.label}
                    </span>
                )}
                {showCasterError && (
                    <span className="text-danger text-break w-100" role="status">Caster: {casterError}</span>
                )}
                {showNoData && (
                    <span className="text-warning text-break w-100">{noDataReason}</span>
                )}
            </span>
        );
    };

    const renderPasswordInput = (name, label) => (
        <div className="input-group">
            <input
                type={visiblePasswords[name] ? 'text' : 'password'}
                className="form-control"
                id={name}
                name={name}
                aria-label={label}
                value={formState[name]}
                onChange={handleChange}
                autoComplete="new-password"
                placeholder="••••••••"
            />
            <button
                type="button"
                className="btn btn-outline-secondary"
                aria-label={`${visiblePasswords[name] ? 'Ẩn' : 'Hiện'} ${label}`}
                aria-pressed={!!visiblePasswords[name]}
                title={visiblePasswords[name] ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
                onClick={() => setVisiblePasswords(prev => ({ ...prev, [name]: !prev[name] }))}
            >
                <i className={`bi ${visiblePasswords[name] ? 'bi-eye-slash' : 'bi-eye'}`} aria-hidden="true"></i>
            </button>
        </div>
    );

    const handleChange = (e) => {
        const { name, value, type, checked } = e.target;
        if (lockedInput(name)) return;
        const inputValue = type === 'checkbox' ? checked : value;
        setIsFormDirty(true);
        setFormState(previous => buildServiceFormState(previous, name, inputValue, checked, serverIds));
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (disabled) return;
        setIsSubmitting(true);

        try {
            const payload = buildServiceSubmissionPayload(formState, serverIds, toBool);
            removeLteUnsupportedServiceFields(payload, isLteStation);

            for (const key of [...serverIds.map(sid => `password${sid}`), 'rtcmpassword1', 'command_pass']) {
                if (!payload[key]) delete payload[key];
            }
            removeLockedServiceFields(payload, { hostsLocked, isAdmin, hostPolicy, serverIds, blockedServer });

            // Send only edited values; telemetry/password placeholders are not changes.
            removeUnchangedServiceFields(payload, formState, hydratedConfigRef.current, serverIds);
            if (removedServers.length) payload.removed_servers = removedServers;
            const result = await onConfigureService(device.serial, payload);
            hydratedConfigRef.current = window.CorsDashboardShared.removeServiceServers(
                { ...hydratedConfigRef.current, ...payload }, removedServers);
            delete hydratedConfigRef.current.removed_servers;
            if (result?.delivered !== false) setRemovedServers([]);
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
                {' '}Để trống mật khẩu để giữ mật khẩu đã lưu; chỉ nhập khi muốn thay đổi.
            </div>
            {hostsLocked && isAdmin && <div className="alert alert-warning small" role="status">
                <i className="bi bi-shield-lock me-2" aria-hidden="true"></i>Admin quản lý khoá server.
                {[1, 2].map(sid => <span key={sid} className="d-block mt-1">Server {sid}: {blockedServer(sid) ? (lockApplied ? 'Đã khoá · không gửi dữ liệu' : 'Khoá · chờ trạm xác nhận') : 'Được phép gửi · người dùng không được tắt'}</span>)}
            </div>}
            {isLteStation && (
                <div className="alert alert-primary small">
                    <div className="fw-bold mb-1">
                        <i className="bi bi-reception-4 me-2"></i>Mục LTE
                    </div>
                    Trạm LTE giữ luồng hoạt động MQTT data -&gt; NTRIP SOURCE. Chỉ cấu hình NTRIP Caster Server 1/2,
                    mountpoint, password và chế độ sleep/always-on. Phần RTCM Client và NMEA vệ tinh không áp dụng cho LTE.
                </div>
            )}

            {/* 🔥 Thông báo chế độ xem */}
            {disabled && (
                <div className="alert alert-warning py-2 mb-3">
                    <i className="bi bi-eye-fill me-2"></i>
                    Chế độ xem: Không thể chỉnh sửa cấu hình dịch vụ.
                </div>
            )}

            <ServiceCommonSettings device={device} formState={formState} handleChange={handleChange}
                disabled={disabled} canManageStationMode={canManageStationMode}
                showS1StreamToggle={showS1StreamToggle} showS2StreamToggle={showS2StreamToggle} />

            <div className="accordion" id="service-config-accordion">
                {isLteStation && (
                    <div className="accordion-item">
                        <h2 className="accordion-header">
                            <button
                                className="accordion-button"
                                type="button"
                                data-bs-toggle="collapse"
                                data-bs-target="#collapseLteCommand"
                            >
                                <i className="bi bi-terminal me-2 text-warning"></i>
                                LTE Command SOURCE
                            </button>
                        </h2>
                        <div id="collapseLteCommand" className="accordion-collapse collapse show" data-bs-parent="#service-config-accordion">
                            <div className="accordion-body">
                                <fieldset disabled>
                                    <div className="alert alert-warning small mb-3">
                                        <i className="bi bi-lightning-charge-fill me-2"></i>
                                        Đây là SOURCE riêng để gửi lệnh cấu hình tọa độ/chip LTE. Giá trị này được khóa cứng theo cấu hình hệ thống,
                                        chỉ hiển thị để kiểm tra và không thể sửa từ màn Service Config.
                                    </div>
                                    <div className="row g-3">
                                        <div className="col-md-8">
                                            <label htmlFor="command_host" className="form-label">Command Host / IP</label>
                                            <input id="command_host"
                                                type="text"
                                                className="form-control"
                                                name="command_host"
                                                value={formState.command_host}
                                                onChange={handleChange}
                                                placeholder="aitogy.com.vn"
                                            />
                                        </div>
                                        <div className="col-md-4">
                                            <label htmlFor="command_port" className="form-label">Command Port</label>
                                            <input id="command_port"
                                                type="number"
                                                className="form-control"
                                                name="command_port"
                                                value={formState.command_port}
                                                onChange={handleChange}
                                                placeholder="2101"
                                            />
                                        </div>
                                        <div className="col-md-6">
                                            <label htmlFor="command_pass" className="form-label">Command Password</label>
                                            {renderPasswordInput('command_pass', 'mật khẩu Command')}
                                        </div>
                                        <div className="col-md-6">
                                            <label htmlFor="command_mp" className="form-label">Command Mountpoint</label>
                                            <input id="command_mp"
                                                type="text"
                                                className="form-control"
                                                name="command_mp"
                                                value={formState.command_mp}
                                                onChange={handleChange}
                                                placeholder="test_lte"
                                            />
                                        </div>
                                    </div>
                                </fieldset>
                            </div>
                        </div>
                    </div>
                )}

                {/* ==================== SERVER 1 ==================== */}
                <div className="accordion-item">
                    <h2 className="accordion-header">
                        <button
                            className={`accordion-button ${isLteStation ? 'collapsed' : ''}`}
                            type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#collapseOne"
                        >
                            <span className="d-flex flex-column flex-sm-row align-items-start align-items-sm-center gap-2 flex-grow-1">
                                <span><i className="bi bi-box-arrow-up me-2 text-primary" aria-hidden="true"></i>NTRIP Caster Server 1</span>
                                {renderCasterStatus(1)}
                            </span>
                        </button>
                    </h2>
                    <div id="collapseOne" className={`accordion-collapse collapse ${isLteStation ? '' : 'show'}`} data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
                            <fieldset disabled={disabled || (!isAdmin && blockedServer(1))}>
                                {isLteStation && (
                                    <div className="alert alert-info small mb-3">
                                        <i className="bi bi-box-arrow-up-right me-2"></i>
                                        Với LTE, Server 1 chỉ là đường Python bridge forward RTCM nhận từ MQTT ra caster.
                                        Các trường Host/Port/Password/Mountpoint ở đây sẽ được lưu đồng thời thành target_host,
                                        target_port, target_pass và target_mp. Nó không gửi lệnh vào chip.
                                    </div>
                                )}
                                {/* Enable Switch */}
                                <div className="form-check form-switch form-switch-lg mb-3">
                                    <input
                                        className="form-check-input"
                                        type="checkbox"
                                        role="switch"
                                        id="server1_enabled"
                                        name="server1_enabled"
                                        disabled={lockedInput('server1_enabled')}
                                        checked={formState.server1_enabled}
                                        onChange={handleChange}
                                    />
                                    <label className="form-check-label" htmlFor="server1_enabled">Kích hoạt Server 1</label>
                                </div>

                                {/* NTRIP Version Selection */}
                                <div className="mb-3 p-3 bg-dark rounded border border-secondary">
                                    <span className="form-label fw-bold text-info">
                                        <i className="bi bi-gear-fill me-2"></i>NTRIP Protocol Version
                                    </span>
                                    <div className="d-flex gap-4">
                                        <div className="form-check">
                                            <input
                                                className="form-check-input"
                                                type="radio"
                                                name="ntrip_version1"
                                                id="ntrip_v1_1"
                                                value="1"
                                                checked={Number.parseInt(formState.ntrip_version1) === 1}
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
                                                checked={Number.parseInt(formState.ntrip_version1) === 2}
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
                                    {isLteStation && (
                                        <div className="alert alert-info small mt-2 mb-0">
                                            <i className="bi bi-reception-4 me-1"></i>
                                            Với LTE, NTRIP version chỉ điều khiển cách bridge Python forward RTCM MQTT lên caster Server 1. Cấu hình tọa độ/chip vẫn đi qua LTE Command SOURCE.
                                        </div>
                                    )}
                                </div>

                                {/* Connection Details */}
                                <div className="row g-3">
                                    <div className="col-md-8">
                                        <label className="form-label d-flex flex-wrap align-items-center">Host / IP {renderCasterStatus(1)}</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="serverhost1"
                                        disabled={lockedInput('serverhost1')}
                                            value={formState.serverhost1}
                                            onChange={handleChange}
                                            placeholder="Ví dụ: rtk2go.com"
                                        />
                                    </div>
                                    <div className="col-md-4">
                                        <label htmlFor="port1" className="form-label">Port</label>
                                        <input id="port1"
                                            type="number"
                                            className="form-control"
                                            name="port1"
                                        disabled={lockedInput('port1')}
                                            value={formState.port1}
                                            onChange={handleChange}
                                            placeholder="2101"
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label htmlFor="username1" className="form-label">
                                            Username
                                            <small className="text-muted ms-1">(NTRIP Source Account)</small>
                                        </label>
                                        <input id="username1"
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
                                        <label htmlFor="password1" className="form-label">Password</label>
                                        {renderPasswordInput('password1', 'mật khẩu Caster 1')}
                                    </div>
                                    <div className="col-12">
                                        <label htmlFor="mountpoint1" className="form-label">Mountpoint</label>
                                        <input id="mountpoint1"
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
                            <span className="d-flex flex-column flex-sm-row align-items-start align-items-sm-center gap-2 flex-grow-1">
                                <span><i className="bi bi-box-arrow-up me-2 text-info" aria-hidden="true"></i>NTRIP Caster Server 2</span>
                                {renderCasterStatus(2)}
                            </span>
                        </button>
                    </h2>
                    <div id="collapseTwo" className="accordion-collapse collapse" data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
                            <fieldset disabled={disabled || (!isAdmin && blockedServer(2))}>
                                {/* Enable Switch */}
                                <div className="form-check form-switch form-switch-lg mb-3">
                                    <input
                                        className="form-check-input"
                                        type="checkbox"
                                        role="switch"
                                        id="server2_enabled"
                                        name="server2_enabled"
                                        disabled={lockedInput('server2_enabled')}
                                        checked={formState.server2_enabled}
                                        onChange={handleChange}
                                    />
                                    <label className="form-check-label" htmlFor="server2_enabled">Kích hoạt Server 2</label>
                                </div>

                                {/* NTRIP Version Selection */}
                                <div className="mb-3 p-3 bg-dark rounded border border-secondary">
                                    <span className="form-label fw-bold text-info">
                                        <i className="bi bi-gear-fill me-2"></i>NTRIP Protocol Version
                                    </span>
                                    <div className="d-flex gap-4">
                                        <div className="form-check">
                                            <input
                                                className="form-check-input"
                                                type="radio"
                                                name="ntrip_version2"
                                                id="ntrip_v2_1"
                                                value="1"
                                                checked={Number.parseInt(formState.ntrip_version2) === 1}
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
                                                checked={Number.parseInt(formState.ntrip_version2) === 2}
                                                onChange={handleChange}
                                            />
                                            <label className="form-check-label" htmlFor="ntrip_v2_2">
                                                <strong>Version 2.0</strong>
                                                <br />
                                                <small className="text-muted">HTTP POST (Modern)</small>
                                            </label>
                                        </div>
                                    </div>
                                    {isLteStation && (
                                        <div className="alert alert-info small mt-2 mb-0">
                                            <i className="bi bi-reception-4 me-1"></i>
                                            Với LTE, NTRIP version chỉ điều khiển cách bridge Python forward RTCM MQTT lên caster Server 2. Cấu hình tọa độ/chip vẫn đi qua LTE Command SOURCE.
                                        </div>
                                    )}
                                    <div className="alert alert-secondary small mt-2 mb-0">
                                        <i className="bi bi-info-circle me-1"></i>
                                        <strong>Ghi chú:</strong> Nếu caster từ chối kết nối với v2.0, hãy thử lại với v1.0
                                    </div>
                                </div>

                                {/* Connection Details */}
                                <div className="row g-3">
                                    <div className="col-md-8">
                                        <label className="form-label d-flex flex-wrap align-items-center">Host / IP {renderCasterStatus(2)}</label>
                                        <input
                                            type="text"
                                            className="form-control"
                                            name="serverhost2"
                                        disabled={lockedInput('serverhost2')}
                                            value={formState.serverhost2}
                                            onChange={handleChange}
                                            placeholder="Ví dụ: backup.caster.com"
                                        />
                                    </div>
                                    <div className="col-md-4">
                                        <label htmlFor="port2" className="form-label">Port</label>
                                        <input id="port2"
                                            type="number"
                                            className="form-control"
                                            name="port2"
                                        disabled={lockedInput('port2')}
                                            value={formState.port2}
                                            onChange={handleChange}
                                            placeholder="2101"
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label htmlFor="username2" className="form-label">
                                            Username
                                            <small className="text-muted ms-1">(NTRIP Source Account)</small>
                                        </label>
                                        <input id="username2"
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
                                        <label htmlFor="password2" className="form-label">Password</label>
                                        {renderPasswordInput('password2', 'mật khẩu Caster 2')}
                                    </div>
                                    <div className="col-12">
                                        <label htmlFor="mountpoint2" className="form-label">Mountpoint</label>
                                        <input id="mountpoint2"
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

                {serverIds.filter(sid => sid > 2).map(sid => (
                    <div className="accordion-item" key={sid}>
                        <h2 className="accordion-header d-flex align-items-center">
                            <button type="button" className="accordion-button flex-grow-1" data-bs-toggle="collapse"
                                data-bs-target={`#caster-${sid}`} aria-controls={`caster-${sid}`} aria-expanded="true">
                                <span className="d-flex flex-column flex-sm-row align-items-start align-items-sm-center gap-2 flex-grow-1">
                                    <span><i className="bi bi-box-arrow-up me-2 text-info" aria-hidden="true"></i>NTRIP Caster Server {sid}</span>
                                    {renderCasterStatus(sid)}
                                </span>
                            </button>
                            <button type="button" className="btn btn-outline-danger m-2 px-3 flex-shrink-0"
                                style={{ minHeight: '44px' }}
                                aria-label={`Xoá Host ${sid}`} title={`Xoá Host ${sid}`}
                                disabled={disabled || isSubmitting || (!isAdmin && hostsLocked)} onClick={() => removeCaster(sid)}>
                                <i className="bi bi-trash me-2" aria-hidden="true"></i>Xoá host
                            </button>
                        </h2>
                        <div id={`caster-${sid}`} className="accordion-collapse collapse show" data-bs-parent="#service-config-accordion">
                            <div className="accordion-body">
                                <fieldset disabled={disabled || isSubmitting || (!isAdmin && blockedServer(sid))}>
                                    <div className="form-check form-switch form-switch-lg mb-3">
                                        <input type="checkbox" className="form-check-input" role="switch" id={`server${sid}_enabled`}
                                            name={`server${sid}_enabled`} disabled={lockedInput(`server${sid}_enabled`)} checked={toBool(formState[`server${sid}_enabled`])} onChange={handleChange} />
                                        <label className="form-check-label" htmlFor={`server${sid}_enabled`}>Kích hoạt Server {sid}</label>
                                    </div>
                                    <div className="mb-3 p-3 bg-dark rounded border border-secondary">
                                        <div className="form-label fw-bold text-info" id={`protocol-label-${sid}`}>
                                            <i className="bi bi-gear-fill me-2" aria-hidden="true"></i>NTRIP Protocol Version
                                        </div>
                                        <div className="d-flex flex-wrap gap-4" role="radiogroup" aria-labelledby={`protocol-label-${sid}`}>
                                            {[1, 2].map(version => <div className="form-check" key={version}>
                                                <input type="radio" className="form-check-input" name={`ntrip_version${sid}`}
                                                    id={`ntrip_v${sid}_${version}`} value={version}
                                                    checked={Number.parseInt(formState[`ntrip_version${sid}`] || 1, 10) === version} onChange={handleChange} />
                                                <label className="form-check-label" htmlFor={`ntrip_v${sid}_${version}`}>
                                                    <strong>Version {version}.0</strong><br />
                                                    <small className="text-muted">{version === 1 ? 'SOURCE command (Legacy)' : 'HTTP POST (Modern)'}</small>
                                                </label>
                                            </div>)}
                                        </div>
                                        <div className="alert alert-secondary small mt-2 mb-0">
                                            <i className="bi bi-info-circle me-1" aria-hidden="true"></i>
                                            <strong>Ghi chú:</strong> Nếu caster từ chối kết nối với v2.0, hãy thử lại với v1.0
                                        </div>
                                    </div>
                                    <div className="row g-3">
                                        <div className="col-md-8">
                                            <label className="form-label d-flex flex-wrap align-items-center" htmlFor={`serverhost${sid}`}>Host / IP {renderCasterStatus(sid)}</label>
                                            <input type="text" className="form-control" id={`serverhost${sid}`} name={`serverhost${sid}`}
                                                disabled={lockedInput(`serverhost${sid}`)} value={formState[`serverhost${sid}`] ?? ''}
                                                onChange={handleChange} placeholder="Ví dụ: backup.caster.com" required={toBool(formState[`server${sid}_enabled`])} />
                                        </div>
                                        <div className="col-md-4">
                                            <label className="form-label" htmlFor={`port${sid}`}>Port</label>
                                            <input type="number" className="form-control" id={`port${sid}`} name={`port${sid}`}
                                                disabled={lockedInput(`port${sid}`)} value={formState[`port${sid}`] ?? ''} onChange={handleChange}
                                                min="1" max="65535" placeholder="2101" required={toBool(formState[`server${sid}_enabled`])} />
                                        </div>
                                        <div className="col-md-6">
                                            <label className="form-label" htmlFor={`username${sid}`}>Username <small className="text-muted ms-1">(NTRIP Source Account)</small></label>
                                            <input type="text" className="form-control" id={`username${sid}`} name={`username${sid}`}
                                                value={formState[`username${sid}`] ?? ''} onChange={handleChange} placeholder="source hoặc username riêng" />
                                            <small className="text-muted">Mặc định: "source" (NTRIP v1). Caster hiện đại có thể yêu cầu username riêng.</small>
                                        </div>
                                        <div className="col-md-6">
                                            <label htmlFor={`password${sid}`} className="form-label">Password</label>{renderPasswordInput(`password${sid}`, `Mật khẩu Caster ${sid}`)}
                                        </div>
                                        <div className="col-12">
                                            <label className="form-label" htmlFor={`mountpoint${sid}`}>Mountpoint</label>
                                            <input type="text" className="form-control" id={`mountpoint${sid}`} name={`mountpoint${sid}`}
                                                value={formState[`mountpoint${sid}`] ?? ''} onChange={handleChange} placeholder={`MY_BASE_0${sid}`}
                                                required={toBool(formState[`server${sid}_enabled`])} />
                                        </div>
                                    </div>
                                    <div className="form-check form-switch mt-3">
                                        <input type="checkbox" className="form-check-input" role="switch" id={`server${sid}_stream_on_demand`}
                                            name={`server${sid}_stream_on_demand`} disabled={!canManageStationMode}
                                            checked={toBool(formState[`server${sid}_stream_on_demand`], true)} onChange={handleChange} />
                                        <label className="form-check-label" htmlFor={`server${sid}_stream_on_demand`}>Ngủ khi không có rover</label>
                                    </div>
                                </fieldset>
                            </div>
                        </div>
                    </div>
                ))}
                {!isLteStation && <div className="p-3">
                    <button type="button" className="btn btn-outline-primary" disabled={disabled || isSubmitting || (!isAdmin && hostsLocked)} onClick={addCaster}>
                        <i className="bi bi-plus-lg me-2" aria-hidden="true"></i>Thêm NTRIP caster
                    </button>
                </div>}

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
                            {isLteStation ? 'RTCM Client / NMEA vệ tinh (không áp dụng cho LTE)' : 'RTCM Client (Nhận tín hiệu hiệu chỉnh)'}
                        </button>
                    </h2>
                    <div id="collapseThree" className="accordion-collapse collapse" data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
                            {isLteStation && (
                                <div className="alert alert-info small mb-3">
                                    <i className="bi bi-info-circle-fill me-2"></i>
                                    LTE không dùng mục này để set NMEA/GSV hoặc bật vệ tinh. Bridge tự giữ luồng MQTT -&gt; SOURCE,
                                    còn cấu hình chip LTE dùng profile RTCM-only cố định (MSM4 + MSM7) trên COM2. Các trường RTCM Client bên dưới sẽ bị bỏ qua.
                                </div>
                            )}
                            <fieldset disabled={disabled || isLteStation}>
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
                                    <span className="form-label d-block">NTRIP Version</span>
                                    <div className="btn-group" role="group" aria-label="RTCM NTRIP Version">
                                        <input
                                            type="radio"
                                            className="btn-check"
                                            id="rtcmntripversion1_v1"
                                            name="rtcmntripversion1"
                                            value="1"
                                            checked={Number.parseInt(formState.rtcmntripversion1, 10) === 1}
                                            onChange={handleChange}
                                        />
                                        <label className="btn btn-outline-secondary" htmlFor="rtcmntripversion1_v1">NTRIP v1</label>
                                        <input
                                            type="radio"
                                            className="btn-check"
                                            id="rtcmntripversion1_v2"
                                            name="rtcmntripversion1"
                                            value="2"
                                            checked={Number.parseInt(formState.rtcmntripversion1, 10) === 2}
                                            onChange={handleChange}
                                        />
                                        <label className="btn btn-outline-secondary" htmlFor="rtcmntripversion1_v2">NTRIP v2</label>
                                    </div>
                                </div>

                                {/* Connection Details */}
                                <div className="row g-3">
                                    <div className="col-md-8">
                                        <label htmlFor="rtcmserver1" className="form-label">Caster Host / IP</label>
                                        <input id="rtcmserver1"
                                            type="text"
                                            className="form-control"
                                            name="rtcmserver1"
                                            value={formState.rtcmserver1}
                                            onChange={handleChange}
                                            placeholder="Ví dụ: rtk2go.com"
                                        />
                                    </div>
                                    <div className="col-md-4">
                                        <label htmlFor="rtcmport1" className="form-label">Caster Port</label>
                                        <input id="rtcmport1"
                                            type="number"
                                            className="form-control"
                                            name="rtcmport1"
                                            value={formState.rtcmport1}
                                            onChange={handleChange}
                                            placeholder="2101"
                                        />
                                    </div>
                                    <div className="col-12">
                                        <label htmlFor="rtcmmountpoint1" className="form-label">Mountpoint</label>
                                        <input id="rtcmmountpoint1"
                                            type="text"
                                            className="form-control"
                                            name="rtcmmountpoint1"
                                            value={formState.rtcmmountpoint1}
                                            onChange={handleChange}
                                            placeholder="BASE_STATION_MP"
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label htmlFor="rtcmusername1" className="form-label">Username</label>
                                        <input id="rtcmusername1"
                                            type="text"
                                            className="form-control"
                                            name="rtcmusername1"
                                            value={formState.rtcmusername1}
                                            onChange={handleChange}
                                            placeholder="username"
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label htmlFor="rtcmpassword1" className="form-label">Password</label>
                                        {renderPasswordInput('rtcmpassword1', 'mật khẩu RTCM')}
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



window.ServiceConfigForm = ServiceConfigForm;
})();
