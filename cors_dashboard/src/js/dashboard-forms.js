// ====================== Dashboard configuration forms ======================
(() => {
const { useState, useEffect, memo, useRef } = React;

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
    'auto_setup_rover': 'Bước 1/4: Đặt chip sang ROVER (mở NMEA GGA)',
    'auto_setup_wait_fix': 'Bước 2/4: Gửi GGA lên Caster & Chờ RTK Fix',
    'auto_setup_averaging': 'Bước 3/4: Thu thập mẫu & Tính trung bình (10 số sau dấu phẩy)',
    'auto_setup_base': 'Bước 4/4: Đặt chip về BASE với tọa độ chuẩn'
};
const LicenseForm = memo(function LicenseForm({ device, currentUser = {}, onDeployLicense, disabled }) {
    const [isDeploying, setIsDeploying] = useState(false);
    const [isUpdatingBypass, setIsUpdatingBypass] = useState(false);
    const authStatus = device?.system_info?.auth_status || {};
    const licenseBypass = authStatus.license_bypass === true || device?.system_info?.auth_license_bypass === true;
    const canManageLicense = currentUser?.role === 'admin';
    const serverTokenPresent = Object.prototype.hasOwnProperty.call(authStatus, 'server_token_present')
        ? authStatus.server_token_present
        : authStatus.token_required;
    const agentTokenPresent = authStatus.agent_token_present === true;
    const tokenSynced = window.CorsDashboardShared?.isAccessTokenSynced?.(device) === true;
    const authMessage = authStatus.token_deployment_pending === true
        ? 'Token đã tạo trên server; lệnh cấp token đã gửi. Đang chờ agent báo fingerprint khớp để xác nhận đồng bộ.'
        : serverTokenPresent === false
        ? agentTokenPresent
            ? 'Agent đã báo có Access Token cục bộ, nhưng server chưa có token để đối chiếu. Bấm nút bên dưới để tạo token trên server và gửi lại xuống agent qua MQTT.'
            : 'Thiết bị chưa có Access Token trên server. Bấm nút bên dưới để tạo token và gửi xuống agent qua MQTT.'
        : tokenSynced
            ? 'Access Token đã đồng bộ với agent. Bạn có thể gửi lại token nếu cần.'
            : agentTokenPresent
                ? 'Access Token trên agent không khớp với server. Bấm nút bên dưới để gửi lại token đúng.'
                : 'Agent chưa có Access Token. Bấm nút bên dưới để gửi token xuống thiết bị qua MQTT.';

    const handleAutoDeploy = async (e) => {
        e.preventDefault();
        if (!canManageLicense || disabled) return;
        setIsDeploying(true);
        try {
            const response = await window.apiFetch(window.ApiRoutes.devices.autoDeployToken(device.serial), {
                method: 'POST'
            });
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'Failed to auto-deploy token');
            }
            window.showSuccess('Token đã tạo và lệnh cấp token đã gửi. Đang chờ agent xác nhận đồng bộ.');
        } catch (error) {
            window.showError('Không thể cấp token tự động: ' + error.message);
        } finally {
            setIsDeploying(false);
        }
    };

    const handleToggleBypass = async () => {
        if (!canManageLicense || isUpdatingBypass) return;
        const nextEnabled = !licenseBypass;
        const ok = await window.CorsUI.confirmAction({
            title: nextEnabled ? 'Bypass license' : 'Disable license bypass',
            message: nextEnabled
                ? `Cho phép trạm ${device.name || device.serial} bỏ qua license để rescue/update? Chỉ dùng tạm thời.`
                : `Tắt bypass license cho trạm ${device.name || device.serial}?`,
            confirmText: nextEnabled ? 'Enable bypass' : 'Disable bypass',
            tone: nextEnabled ? 'warning' : 'primary'
        });
        if (!ok) return;
        setIsUpdatingBypass(true);
        try {
            const response = await window.apiFetch(window.ApiRoutes.devices.licenseBypass(device.serial), {
                method: 'POST',
                body: JSON.stringify({ enabled: nextEnabled })
            });
            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to update license bypass');
            }
            window.showSuccess(nextEnabled ? 'Đã bật bypass license cho trạm.' : 'Đã tắt bypass license cho trạm.');
            setTimeout(() => window.location.reload(), 500);
        } catch (error) {
            window.showError('Không thể cập nhật bypass license: ' + error.message);
        } finally {
            setIsUpdatingBypass(false);
        }
    };

    return (
        <form onSubmit={handleAutoDeploy}>
            <div className="alert alert-warning small">
                <i className="bi bi-key-fill me-2"></i>
                {canManageLicense ? authMessage : 'Trạm này đang cần Access Token để mở khóa. Vui lòng liên hệ admin để được cấp token và mở khóa trạm.'}
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
            
            {canManageLicense && (
            <button type="submit" className="btn btn-primary w-100 py-3 mt-2 fw-bold" disabled={isDeploying || disabled}>
                {isDeploying ? (
                    <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang cấp phát tự động...
                    </>
                ) : (
                    <>
                        <i className="bi bi-magic me-2"></i>
                        Tự động cấp & gửi Token
                    </>
                )}
            </button>
            )}
            {canManageLicense && (
                <button
                    type="button"
                    className={`btn ${licenseBypass ? 'btn-outline-success' : 'btn-outline-warning'} w-100 py-2 mt-2 fw-bold`}
                    disabled={isUpdatingBypass}
                    onClick={handleToggleBypass}
                >
                    {isUpdatingBypass ? (
                        <>
                            <span className="spinner-border spinner-border-sm me-2"></span>
                            Đang cập nhật bypass...
                        </>
                    ) : licenseBypass ? (
                        <>
                            <i className="bi bi-shield-check me-2"></i>
                            Tắt bypass license
                        </>
                    ) : (
                        <>
                            <i className="bi bi-shield-exclamation me-2"></i>
                            Bỏ qua license cho trạm này
                        </>
                    )}
                </button>
            )}
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
        } catch {
            // The provisioning handler already reports the failure via toast.
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

const BaseConfigForm = memo(function BaseConfigForm({ device, currentUser = {}, onConfigureChip, onStopAutoBase, disabled }) {
    const knownChips = ['Ublox', 'Unicorecomm'];
    const needsManualSelection = !knownChips.includes(device.detected_chip_type);

    const [manualChip, setManualChip] = useState(device.detected_chip_type === 'RTCM3_Source' ? 'Unicorecomm' : 'Ublox');
    const [method, setMethod] = useState('FIXED_LLA');
    const activeChip = needsManualSelection ? manualChip : device.detected_chip_type;
    const isLteStation = !!device?.is_lte || String(device?.connection_type || '').toUpperCase() === 'LTE';

    const ACTIVE_AUTO_BASE_PHASES = ['queued', 'starting', 'set_rover', 'wait_fix', 'averaging', 'set_base'];
    const isAutoSetupStatus = (device?.status || '').startsWith('auto_setup');
    const autoProgress = device?.auto_base_progress || {};
    const isAutoSetupActive = isAutoSetupStatus || ACTIVE_AUTO_BASE_PHASES.includes(autoProgress.phase);
    const autoSamplesCollected = Number(autoProgress.samples_collected || 0);
    const autoSamplesTarget = Number(autoProgress.samples_target || 0);
    const autoProgressPercent = autoSamplesTarget > 0
        ? Math.min(100, Math.round((autoSamplesCollected / autoSamplesTarget) * 100))
        : null;
    const rawGgaSentence = autoProgress.last_raw_gga || device?.last_raw_gga || '';
    const ggaAge = autoProgress.gga_age_seconds !== undefined && autoProgress.gga_age_seconds !== null
        ? autoProgress.gga_age_seconds
        : (device?.last_raw_gga_age !== undefined ? device.last_raw_gga_age : null);
    const ggaFix = autoProgress.gga_fix_status || device?.last_gga_fix_status || device?.rtk_fix_status || 'NO_FIX';
    const rtcmBytesReceived = autoProgress.rtcm_bytes || 0;
    const rtcmPktsReceived = autoProgress.rtcm_pkts || 0;
    const fixedStreak = autoProgress.fixed_streak || 0;
    const fixedStreakReq = autoProgress.fixed_streak_required || 5;
    const autoFixedResult = autoProgress.phase === 'completed' && autoProgress.fixed_samples_only === true;
    const autoResultText = autoFixedResult
        ? `Auto Base hoàn tất: ${autoProgress.sample_count || 0} mẫu RTK Fixed.`
        : ['completed', 'unverified'].includes(autoProgress.phase)
        ? 'Kết quả cũ chưa được xác nhận RTK Fixed. Cần chạy lại Auto Base.'
        : autoProgress.phase === 'failed'
        ? `Auto Base thất bại: ${autoProgress.reason || 'Không đủ dữ liệu RTK Fixed.'}`
        : autoProgress.phase === 'stopped' ? 'Auto Base đã dừng.' : '';

    // Tọa độ Fixed
    const [latitude, setLatitude] = useState('');
    const [longitude, setLongitude] = useState('');
    const [altitude, setAltitude] = useState('');
    const [coordinatePasteNotice, setCoordinatePasteNotice] = useState(null);
    const [altitudeReference, setAltitudeReference] = useState('ELLIPSOID');
    const [fixedAccuracyM, setFixedAccuracyM] = useState('0.01'); // Meters, matching the API and receiver configuration.

    // CORS connection for Auto Base (kept in this form only; never read from .env)
    const [corsHost, setCorsHost] = useState('');
    const [corsPort, setCorsPort] = useState('2101');
    const [corsUsername, setCorsUsername] = useState('');
    const [corsPassword, setCorsPassword] = useState('');
    const [corsSources, setCorsSources] = useState([]);
    const [selectedMountpoint, setSelectedMountpoint] = useState('');
    const [isLoadingCorsSources, setIsLoadingCorsSources] = useState(false);
    const [corsLookupError, setCorsLookupError] = useState('');

    // Survey-In Params
    const [svinDuration, setSvinDuration] = useState('300'); // Giây
    const [svinAccuracyM, setSvinAccuracyM] = useState('3.00'); // Đơn vị Mét cho Survey-In (Mặc định 3m)

    const [outputMode, setOutputMode] = useState('diagnostics_rtcm');
    const [msmLevel, setMsmLevel] = useState('4');
    const [constellations, setConstellations] = useState({
        gps: true,
        glo: true,
        gal: true,
        bds: true,
        qzss: true,
        sbas: true
    });
    const [isSubmitting, setIsSubmitting] = useState(false);
    const isAdmin = String(currentUser?.role || '').toLowerCase() === 'admin';

    useEffect(() => {
        if (!autoFixedResult) return;
        const coords = autoProgress.averaged_coord || device.base_config?.coords;
        if (!coords) return;
        setLatitude(coords.lat ?? '');
        setLongitude(coords.lon ?? '');
        setAltitude(coords.alt ?? '');
    }, [device.serial, autoProgress.updated_at, autoFixedResult]);

    useEffect(() => {
        // 1. Reset về giá trị mặc định khi đổi thiết bị
        setManualChip(device.detected_chip_type === 'RTCM3_Source' ? 'Unicorecomm' : 'Ublox');
        setMethod('FIXED_LLA');
        setLatitude('');
        setLongitude('');
        setAltitude('');
        setCoordinatePasteNotice(null);
        setAltitudeReference('ELLIPSOID');
        setFixedAccuracyM('0.01');
        setCorsHost('');
        setCorsPort('2101');
        setCorsUsername('');
        setCorsPassword('');
        setCorsSources([]);
        setSelectedMountpoint('');
        setCorsLookupError('');
        setSvinDuration('300');
        setSvinAccuracyM('3.00');
        setOutputMode('diagnostics_rtcm');
        setMsmLevel('4');
        setConstellations({ gps: true, glo: true, gal: true, bds: true, qzss: true, sbas: true });

        // 2. Load config từ DB (nếu có)
        if (device.base_config) {
            const config = device.base_config;

            // Set Method: Ưu tiên cấu hình đã lưu trong DB. Chỉ dùng AUTO_CORS nếu chưa có cấu hình và đang thực sự chạy Auto Base.
            if (config.base_setup_method) {
                setMethod(config.base_setup_method);
            } else if (isAutoSetupActive) {
                setMethod('AUTO_CORS');
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
                setAltitudeReference(config.altitude_reference === 'ELLIPSOID' || device.detected_chip_type === 'Ublox'
                    ? 'ELLIPSOID' : 'LEGACY');
            }
            // Display stored meters directly without unit conversion or rounding.
            if (config.accuracy !== undefined) {
                setFixedAccuracyM(String(config.accuracy));
            }

            // Load Survey-In Params
            if (config.survey_in_duration !== undefined) {
                setSvinDuration(String(config.survey_in_duration));
            }
            // Load Accuracy cho Survey-In (Lưu trong DB là mét -> Giữ nguyên là mét)
            if (config.survey_in_accuracy !== undefined) {
                setSvinAccuracyM(String(config.survey_in_accuracy));
            }
            if (config.gnss_options && typeof config.gnss_options === 'object') {
                const options = config.gnss_options;
                if (options.output_mode) setOutputMode(String(options.output_mode));
                if ([4, 7].includes(Number(options.msm_level)) || options.msm_level === 'both') setMsmLevel(String(options.msm_level));
                if (options.constellations && typeof options.constellations === 'object') {
                    setConstellations(prev => ({
                        ...prev,
                        ...Object.fromEntries(
                            Object.entries(options.constellations)
                                .filter(([key]) => Object.prototype.hasOwnProperty.call(prev, key))
                                .map(([key, value]) => [key, !!value])
                        )
                    }));
                }
            }
        }
    }, [device.serial]);

    const handleDecimalInputChange = (setter, maxDecimals) => (e) => {
        const value = e.target.value.replace(',', '.');
        if (maxDecimals !== undefined && (value.split('.')[1] || '').length > maxDecimals) return;
        // Cho phép nhập số thập phân âm/dương
        if (/^-?[0-9]*\.?[0-9]*$/.test(value)) {
            setter(value);
            setCoordinatePasteNotice(null);
        }
    };

    const chipCoordinate = (value, digits) => {
        const text = String(value).trim().replace(',', '.');
        if (!/^-?\d+(?:\.\d+)?$/.test(text)) return text;
        const [whole, fraction = ''] = text.split('.');
        return `${whole}.${fraction.padEnd(digits, '0').slice(0, digits)}`;
    };

    const formatCoord10 = (val) => {
        if (val === null || val === undefined || val === '') return '';
        if (activeChip === 'Ublox') return chipCoordinate(val, 9);
        const num = parseFloat(val);
        return Number.isFinite(num) ? num.toFixed(10) : String(val);
    };

    const formatAlt10 = (val) => {
        if (val === null || val === undefined || val === '') return '';
        if (activeChip === 'Ublox') return chipCoordinate(val, 4);
        const num = parseFloat(val);
        return Number.isFinite(num) ? num.toFixed(10) : String(val);
    };

    const handleCoordinatePaste = (event) => {
        if (disabled || isSubmitting) return;
        try {
            const coords = window.CorsCoordinateClipboard?.parse(event.clipboardData?.getData('text/plain') || '');
            if (!coords) return; // A normal numeric paste still belongs to the focused field.
            event.preventDefault();
            setLatitude(activeChip === 'Ublox' ? chipCoordinate(coords.lat, 9) : coords.lat);
            setLongitude(activeChip === 'Ublox' ? chipCoordinate(coords.lon, 9) : coords.lon);
            setAltitude(activeChip === 'Ublox' ? chipCoordinate(coords.alt, 4) : coords.alt);
            setAltitudeReference('ELLIPSOID');
            setCoordinatePasteNotice({ error: false, message: 'Đã điền vĩ độ, kinh độ WGS 84 và độ cao h. Kiểm tra tọa độ rồi bấm áp dụng.' });
        } catch (error) {
            event.preventDefault();
            setCoordinatePasteNotice({ error: true, message: error.message });
        }
    };

    const handleCorsFieldChange = (setter) => (e) => {
        setter(e.target.value);
        setCorsSources([]);
        setSelectedMountpoint('');
        setCorsLookupError('');
    };

    const handleLoadCorsMountpoints = async () => {
        const host = corsHost.trim();
        const port = Number(corsPort);
        const username = corsUsername.trim();
        if (!host || !Number.isInteger(port) || port < 1 || port > 65535 || !username || !corsPassword) {
            setCorsLookupError('Vui lòng nhập đủ IP/Host, Port, Username và Password của Base.');
            return;
        }

        setIsLoadingCorsSources(true);
        setCorsLookupError('');
        try {
            const response = await window.apiFetch(window.ApiRoutes.devices.corsMountpoints(device.serial), {
                method: 'POST',
                body: JSON.stringify({ host, port, username, password: corsPassword })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.detail || 'Không thể đọc danh sách mountpoint từ Base.');
            }

            const sources = Array.isArray(data.sources)
                ? data.sources.filter(source => source && String(source.mountpoint || '').trim())
                : [];
            setCorsSources(sources);
            setSelectedMountpoint(current => (
                sources.some(source => source.mountpoint === current)
                    ? current
                    : (sources[0]?.mountpoint || '')
            ));
            if (!sources.length) {
                setCorsLookupError('Base đã kết nối nhưng chưa trả về mountpoint nào.');
            } else {
                window.showSuccess(`Đã tải ${sources.length} mountpoint từ Base.`);
            }
        } catch (error) {
            setCorsSources([]);
            setSelectedMountpoint('');
            setCorsLookupError(error.message);
        } finally {
            setIsLoadingCorsSources(false);
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
                const latStr = String(latitude).trim();
                const lonStr = String(longitude).trim();
                const latNum = parseFloat(latStr);
                const lonNum = parseFloat(lonStr);
                if (!latStr || !lonStr || isNaN(latNum) || isNaN(lonNum)) {
                    window.showError('Vui lòng nhập vĩ độ và kinh độ hợp lệ.');
                    setIsSubmitting(false);
                    return;
                }
                params.coords = {
                    lat: finalChipType === 'Ublox' ? chipCoordinate(latStr, 9) : latStr,
                    lon: finalChipType === 'Ublox' ? chipCoordinate(lonStr, 9) : lonStr,
                    alt: finalChipType === 'Ublox' ? chipCoordinate(String(altitude).trim() || '0', 4) : String(altitude).trim() || '0'
                };
                if (finalChipType === 'Ublox') {
                    setLatitude(params.coords.lat);
                    setLongitude(params.coords.lon);
                    setAltitude(params.coords.alt);
                }
                params.altitude_reference = finalChipType === 'Ublox' ? 'ELLIPSOID' : altitudeReference;
                // Preserve decimal meters through the API and UBX encoding.
                params.accuracy = String(fixedAccuracyM).trim().replace(',', '.') || '0.01';
            } else if (method === 'SURVEY_IN') {
                params.survey_in_duration = parseInt(svinDuration);
                // Survey-In: Input là m -> Gửi trực tiếp m cho backend (Backend sẽ tự xử lý x10000 nếu cần cho Ublox)
                params.survey_in_accuracy = parseFloat(svinAccuracyM);
            } else if (method === 'AUTO_CORS') {
                const port = Number(corsPort);
                if (!corsHost.trim() || !Number.isInteger(port) || port < 1 || port > 65535 || !corsUsername.trim() || !corsPassword) {
                    window.showError('Vui lòng nhập đủ thông tin kết nối Base.');
                    setIsSubmitting(false);
                    return;
                }
                if (!selectedMountpoint) {
                    window.showError('Vui lòng bấm "Kết nối Base & lấy mountpoint" rồi chọn một mountpoint.');
                    setIsSubmitting(false);
                    return;
                }
            }
            if (!isLteStation) {
                const existingGnss = (device.base_config && typeof device.base_config.gnss_options === 'object')
                    ? device.base_config.gnss_options
                    : null;

                if (isAdmin) {
                    params.gnss_options = {
                        output_mode: outputMode,
                        msm_level: msmLevel === 'both' ? 'both' : Number(msmLevel),
                        constellations: constellations || {
                            gps: true,
                            glo: true,
                            gal: true,
                            bds: true,
                            qzss: true,
                            sbas: true
                        },
                        ports: ['USB']
                    };
                } else if (existingGnss) {
                    params.gnss_options = { ...existingGnss };
                } else {
                    params.gnss_options = {
                        output_mode: outputMode || 'diagnostics_rtcm',
                        msm_level: msmLevel === 'both' ? 'both' : Number(msmLevel || 4),
                        constellations: constellations || {
                            gps: true,
                            glo: true,
                            gal: true,
                            bds: true,
                            qzss: true,
                            sbas: true
                        },
                        ports: ['USB']
                    };
                }
            }

            const payload = {
                sensor_type: finalChipType,
                mode: 'BASE',
                params: params
            };

            if (method === 'AUTO_CORS') {
                payload.auto_cors = {
                    ip: corsHost.trim(),
                    port: Number(corsPort),
                    user: corsUsername.trim(),
                    password: corsPassword,
                    mountpoint: selectedMountpoint
                };
                delete params.auto_cors;
            }

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

            {/* Realtime NMEA GGA & Auto Base Monitor (Rover Mode) - Chỉ hiện khi method là AUTO_CORS hoặc khi trạm đang thực sự chạy Auto Base */}
            {((method === 'AUTO_CORS' && (isAutoSetupActive || rawGgaSentence || autoProgress.phase)) || isAutoSetupActive) && (
                <div className="mb-3">
                    <div className="card border-info bg-dark shadow-sm">
                        <div className="card-header bg-info bg-opacity-10 border-info d-flex flex-wrap justify-content-between align-items-center py-2 gap-2">
                            <div className="d-flex align-items-center gap-2">
                                {isAutoSetupStatus && (
                                    <span className="spinner-grow spinner-grow-sm text-info" role="status"></span>
                                )}
                                <strong className="text-info small text-uppercase">
                                    <i className="bi bi-broadcast me-1"></i>
                                    {STATUS_TEXT_MAP[device.status] || device.status || 'Giám sát Rover & Base'}
                                </strong>
                            </div>
                            <div className="d-flex align-items-center gap-2 flex-wrap">
                                {isAutoSetupActive && onStopAutoBase && (
                                    <button
                                        type="button"
                                        className="btn btn-outline-danger btn-sm py-0 px-2 fw-bold"
                                        style={{ fontSize: '0.75rem' }}
                                        onClick={async () => {
                                            const ok = await window.CorsUI?.confirmAction?.({
                                                title: 'Dừng Auto Base',
                                                message: 'Bạn có chắc chắn muốn dừng quy trình Auto Base và đưa trạm về trạng thái bình thường?',
                                                confirmText: 'Dừng Auto Base',
                                                confirmVariant: 'danger'
                                            });
                                            if (ok) {
                                                await onStopAutoBase(device.serial);
                                            }
                                        }}
                                    >
                                        <i className="bi bi-stop-circle-fill me-1"></i>
                                        Dừng Auto Base
                                    </button>
                                )}
                                {/* Fix Quality Badge */}
                                {ggaFix === 'RTK_FIXED' ? (
                                    <span className="badge bg-success">🎯 RTK FIXED</span>
                                ) : ggaFix === 'RTK_FLOAT' ? (
                                    <span className="badge bg-warning text-dark">📍 RTK FLOAT</span>
                                ) : ggaFix === 'SINGLE' || ggaFix === 'GPS_FIX' ? (
                                    <span className="badge bg-primary">📡 SINGLE</span>
                                ) : (
                                    <span className="badge bg-secondary">⏳ {ggaFix}</span>
                                )}
                                {/* GGA Age Badge */}
                                {ggaAge !== null && (
                                    <span className={`badge ${ggaAge < 3 ? 'bg-success' : ggaAge < 10 ? 'bg-warning text-dark' : 'bg-danger'}`}>
                                        GGA: {ggaAge}s
                                    </span>
                                )}
                                {autoProgressPercent !== null && (
                                    <span className="badge bg-primary">{autoProgressPercent}%</span>
                                )}
                            </div>
                        </div>
                        <div className="card-body p-3">
                            {autoResultText && (
                                <div className={`alert ${autoFixedResult ? 'alert-success' : 'alert-warning'} py-2`} role="status" aria-atomic="true">
                                    {autoResultText}
                                </div>
                            )}
                            {/* Sample Collection Progress Bar */}
                            {autoSamplesTarget > 0 && (
                                <div className="mb-3">
                                    <div className="d-flex justify-content-between small text-muted mb-1">
                                        <span>Tiến độ thu thập mẫu tọa độ</span>
                                        <span>{autoSamplesCollected} / {autoSamplesTarget} mẫu</span>
                                    </div>
                                    <div className="progress" style={{ height: '8px' }}>
                                        <div
                                            className="progress-bar progress-bar-striped progress-bar-animated bg-info"
                                            style={{ width: `${autoProgressPercent || 0}%` }}
                                        ></div>
                                    </div>
                                </div>
                            )}

                            {/* Realtime Raw GGA Box */}
                            <div className="mb-3">
                                <div className="d-flex justify-content-between align-items-center mb-1">
                                    <label className="form-label small fw-bold mb-0 text-info">
                                        <i className="bi bi-terminal me-1"></i>
                                        Bản tin NMEA GGA trực tiếp từ Chip Rover:
                                    </label>
                                    {rawGgaSentence && (
                                        <button
                                            type="button"
                                            className="btn btn-outline-info btn-sm py-0 px-2"
                                            style={{ fontSize: '0.75rem' }}
                                            onClick={() => {
                                                navigator.clipboard.writeText(rawGgaSentence);
                                                window.showToast?.('Đã sao chép bản tin GGA!');
                                            }}
                                        >
                                            <i className="bi bi-clipboard me-1"></i>Copy GGA
                                        </button>
                                    )}
                                </div>
                                <div
                                    className="p-2 rounded bg-black border border-info font-monospace text-success small text-break user-select-all shadow-inner"
                                    style={{ minHeight: '38px', lineHeight: '1.4', fontSize: '0.85rem' }}
                                >
                                    {rawGgaSentence || (
                                        <span className="text-muted fst-italic">
                                            <i className="bi bi-arrow-repeat me-1 spin"></i>
                                            Đang chờ bản tin GGA từ chip GNSS (Rover mode)...
                                        </span>
                                    )}
                                </div>
                            </div>

                            {/* Metric Grid: RTCM stream & RTK streak & Coords */}
                            <div className="row g-2 text-center small">
                                <div className="col-4">
                                    <div className="p-2 rounded bg-dark border border-secondary-subtle">
                                        <div className="text-muted" style={{ fontSize: '0.75rem' }}>RTCM từ Caster</div>
                                        <div className="fw-bold text-info">
                                            {rtcmBytesReceived > 1024
                                                ? `${(rtcmBytesReceived / 1024).toFixed(1)} KB`
                                                : `${rtcmBytesReceived} B`}
                                            <span className="text-muted ms-1" style={{ fontSize: '0.7rem' }}>({rtcmPktsReceived} pkts)</span>
                                        </div>
                                    </div>
                                </div>
                                <div className="col-4">
                                    <div className="p-2 rounded bg-dark border border-secondary-subtle">
                                        <div className="text-muted" style={{ fontSize: '0.75rem' }}>Giữ RTK Fix</div>
                                        <div className="fw-bold text-warning">
                                            {fixedStreak} / {fixedStreakReq}s
                                        </div>
                                    </div>
                                </div>
                                <div className="col-4">
                                    <div className="p-2 rounded bg-dark border border-secondary-subtle">
                                        <div className="text-muted" style={{ fontSize: '0.75rem' }}>Tọa độ Rover</div>
                                        <div className="fw-bold text-white text-truncate" title={`${autoProgress.current_coord?.lat || '--'}, ${autoProgress.current_coord?.lon || '--'}`}>
                                            {autoProgress.current_coord?.lat
                                                ? `${formatCoord10(autoProgress.current_coord.lat)}`
                                                : 'Đang đợi Fix'}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {method !== 'AUTO_CORS' && isAutoSetupActive && (
                <div className="alert alert-warning small py-2 mb-3">
                    <i className="bi bi-exclamation-triangle-fill me-2"></i>
                    Trạm đang trong tiến trình Auto Base (Rover). Bạn có thể bấm <strong>"Dừng Auto Base"</strong> ở trên hoặc điền tọa độ và bấm <strong>"Áp dụng cấu hình BASE"</strong> bên dưới để dừng Auto Base và lưu tọa độ cố định.
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
                    <option value="AUTO_CORS">Tự động cấu hình Base từ Base liên kết</option>
                    <option value="FIXED_LLA">Nhập tọa độ thủ công (Fixed Mode - Độ chính xác cao)</option>
                    <option value="SURVEY_IN">Tự động khảo sát (Survey-In - Độ chính xác thấp)</option>
                </select>
            </div>

            <fieldset disabled={disabled}>
                {method === 'AUTO_CORS' ? (
                    <div className="row g-3 auto-cors-config">
                        <div className="col-12">
                            <div className="auto-cors-config__panel p-3 rounded border border-info-subtle bg-dark-subtle">
                                <div className="d-flex align-items-start gap-2 mb-3">
                                    <i className="bi bi-broadcast-pin text-info fs-5" aria-hidden="true"></i>
                                    <div>
                                        <div className="fw-bold text-info">Thông tin Base liên kết</div>
                                        <div className="form-text mt-1">
                                            Nhập đúng tài khoản Base tại đây. Các giá trị này chỉ dùng cho lần Auto Base này và được gửi trực tiếp xuống agent.
                                        </div>
                                    </div>
                                </div>
                                <div className="row g-3">
                                    <div className="col-md-8">
                                        <label htmlFor={`cors-host-${device.serial}`} className="form-label small fw-bold">IP / Host Base</label>
                                        <input
                                            id={`cors-host-${device.serial}`}
                                            type="text"
                                            className="form-control"
                                            value={corsHost}
                                            onChange={handleCorsFieldChange(setCorsHost)}
                                            placeholder="Ví dụ: 14.238.1.125"
                                            autoComplete="off"
                                            required
                                        />
                                    </div>
                                    <div className="col-md-4">
                                        <label htmlFor={`cors-port-${device.serial}`} className="form-label small fw-bold">Port</label>
                                        <input
                                            id={`cors-port-${device.serial}`}
                                            type="number"
                                            min="1"
                                            max="65535"
                                            className="form-control"
                                            value={corsPort}
                                            onChange={handleCorsFieldChange(setCorsPort)}
                                            placeholder="2101"
                                            required
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label htmlFor={`cors-user-${device.serial}`} className="form-label small fw-bold">Username Base</label>
                                        <input
                                            id={`cors-user-${device.serial}`}
                                            type="text"
                                            className="form-control"
                                            value={corsUsername}
                                            onChange={handleCorsFieldChange(setCorsUsername)}
                                            placeholder="Tài khoản NTRIP"
                                            autoComplete="username"
                                            required
                                        />
                                    </div>
                                    <div className="col-md-6">
                                        <label htmlFor={`cors-password-${device.serial}`} className="form-label small fw-bold">Password Base</label>
                                        <input
                                            id={`cors-password-${device.serial}`}
                                            type="password"
                                            className="form-control"
                                            value={corsPassword}
                                            onChange={handleCorsFieldChange(setCorsPassword)}
                                            placeholder="Mật khẩu NTRIP"
                                            autoComplete="current-password"
                                            required
                                        />
                                    </div>
                                    <div className="col-12">
                                        <button
                                            type="button"
                                            className="btn btn-outline-info w-100"
                                            onClick={handleLoadCorsMountpoints}
                                            disabled={isLoadingCorsSources || isSubmitting}
                                        >
                                            {isLoadingCorsSources ? (
                                                <>
                                                    <span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                                                    Đang kết nối Base...
                                                </>
                                            ) : (
                                                <>
                                                    <i className="bi bi-search me-2" aria-hidden="true"></i>
                                                    Bước 1: Kết nối Base & Lấy danh sách Mountpoint
                                                </>
                                            )}
                                        </button>
                                    </div>
                                    {corsLookupError && (
                                        <div className="col-12">
                                            <div className="alert alert-warning small mb-0" role="alert">
                                                <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>
                                                {corsLookupError}
                                            </div>
                                        </div>
                                    )}
                                    <div className="col-12">
                                        <label htmlFor={`cors-mountpoint-${device.serial}`} className="form-label small fw-bold">Bước 2: Chọn Mountpoint</label>
                                        <select
                                            id={`cors-mountpoint-${device.serial}`}
                                            className="form-select"
                                            value={selectedMountpoint}
                                            onChange={(e) => setSelectedMountpoint(e.target.value)}
                                            disabled={!corsSources.length || isSubmitting}
                                            required
                                        >
                                            <option value="">-- Chọn mountpoint từ Base --</option>
                                            {corsSources.map((source) => (
                                                <option key={source.mountpoint} value={source.mountpoint}>
                                                    {source.mountpoint}{source.identifier ? ` · ${source.identifier}` : ''}
                                                </option>
                                            ))}
                                        </select>
                                        <div className="form-text">
                                            {corsSources.length
                                                ? `${corsSources.length} mountpoint đã sẵn sàng. Chọn MP và bấm Bước 3 bên dưới để bắt đầu.`
                                                : 'Bấm nút Bước 1 để tải danh sách mountpoint.'}
                                        </div>
                                    </div>
                                    <div className="col-12 mt-2">
                                        <button
                                            type="submit"
                                            className="btn btn-primary w-100 py-2 fw-semibold shadow-sm"
                                            disabled={isSubmitting || isLoadingCorsSources || !selectedMountpoint}
                                        >
                                            {isSubmitting ? (
                                                <>
                                                    <span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                                                    Đang chạy quy trình Auto Base từ Base...
                                                </>
                                            ) : (
                                                <>
                                                    <i className="bi bi-play-circle-fill me-2" aria-hidden="true"></i>
                                                    Bước 3: Bắt đầu chạy Auto Base từ Base {selectedMountpoint ? `(${selectedMountpoint})` : ''}
                                                </>
                                            )}
                                        </button>
                                        <div className="form-text text-muted mt-2">
                                            <i className="bi bi-info-circle me-1"></i>
                                            Quy trình: Chuyển chip sang Rover &rarr; Gửi NMEA GGA lên Caster &rarr; Nhận RTCM &rarr; Đạt RTK Fix &rarr; Thu thập tọa độ TB (10 số sau dấu phẩy) &rarr; Đặt Base.
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div className="col-12">
                            <div className={`alert ${autoFixedResult ? 'alert-success' : 'alert-info'} small mb-2`}>
                                <i className="bi bi-robot me-2"></i>
                                {autoFixedResult ? (
                                    <span>Chip đã tự chuyển về BASE và áp dụng tọa độ trung bình RTK Fixed. Không cần bấm áp dụng lần nữa.</span>
                                ) : (
                                    <span>Hệ thống sẽ tự động ghép nối với máy chủ Base và tính trung bình tọa độ (10 số thập phân) để thiết lập trạm phát Base hoàn toàn tự động!</span>
                                )}
                            </div>
                        </div>
                        {Boolean(autoFixedResult && latitude !== '' && longitude !== '') && (
                            <>
                                <div className="col-md-4">
                                    <label className="form-label small fw-bold">Lat (Tự động - 10 số thập phân)</label>
                                    <input type="text" className="form-control form-control-sm bg-dark-subtle fw-semibold font-monospace" value={formatCoord10(latitude)} readOnly />
                                </div>
                                <div className="col-md-4">
                                    <label className="form-label small fw-bold">Lon (Tự động - 10 số thập phân)</label>
                                    <input type="text" className="form-control form-control-sm bg-dark-subtle fw-semibold font-monospace" value={formatCoord10(longitude)} readOnly />
                                </div>
                                <div className="col-md-4">
                                    <label className="form-label small fw-bold">Alt h ellipsoid (Tự động)</label>
                                    <input type="text" className="form-control form-control-sm bg-dark-subtle fw-semibold font-monospace" value={formatAlt10(altitude)} readOnly />
                                </div>
                            </>
                        )}
                    </div>
                ) : method === 'FIXED_LLA' ? (
                    <div className="row g-3">
                        <div className="col-12 text-muted small fst-italic mb-2">
                            * Chế độ này yêu cầu bạn đã có tọa độ chính xác (mốc địa chính).
                        </div>
                        <div id={`base-coordinate-paste-help-${device.serial}`} className="col-12 small text-info">
                            Dùng “Sao chép cho GNSS” trong <a href="/coordinates" target="_blank" rel="noopener">công cụ chuyển tọa độ</a>, rồi dán vào một trong ba ô bên dưới để tự điền đủ tọa độ.
                        </div>
                        {coordinatePasteNotice && <div className={`col-12 small ${coordinatePasteNotice.error ? 'text-danger' : 'text-success'}`} role="status" aria-live="polite">{coordinatePasteNotice.message}</div>}
                        <div className="col-md-6">
                            <label htmlFor={`base-latitude-${device.serial}`} className="form-label">Vĩ độ (WGS 84)</label>
                            <input
                                id={`base-latitude-${device.serial}`}
                                type="text" inputMode="decimal" className="form-control"
                                value={latitude} onChange={handleDecimalInputChange(setLatitude, activeChip === 'Ublox' ? 9 : undefined)}
                                onPaste={handleCoordinatePaste} aria-describedby={`base-coordinate-paste-help-${device.serial}${activeChip === 'Ublox' ? ` base-ublox-precision-${device.serial}` : activeChip === 'Unicorecomm' ? ` base-unicore-precision-${device.serial}` : ''}`}
                                disabled={disabled || isSubmitting}
                                required placeholder="Ví dụ: 21.028511"
                            />
                        </div>
                        <div className="col-md-6">
                            <label htmlFor={`base-longitude-${device.serial}`} className="form-label">Kinh độ (WGS 84)</label>
                            <input
                                id={`base-longitude-${device.serial}`}
                                type="text" inputMode="decimal" className="form-control"
                                value={longitude} onChange={handleDecimalInputChange(setLongitude, activeChip === 'Ublox' ? 9 : undefined)}
                                onPaste={handleCoordinatePaste} aria-describedby={`base-coordinate-paste-help-${device.serial}${activeChip === 'Ublox' ? ` base-ublox-precision-${device.serial}` : activeChip === 'Unicorecomm' ? ` base-unicore-precision-${device.serial}` : ''}`}
                                disabled={disabled || isSubmitting}
                                required placeholder="Ví dụ: 105.854167"
                            />
                        </div>
                        {activeChip === 'Ublox' && <div id={`base-ublox-precision-${device.serial}`} className="col-12 form-text mt-1">
                            Ublox: vĩ độ và kinh độ tối đa 9 chữ số sau dấu . hoặc , — không nhận chữ số thứ 10.
                        </div>}
                        {activeChip === 'Unicorecomm' && <div id={`base-unicore-precision-${device.serial}`} className="col-12 form-text mt-1">
                            Unicorecomm: dùng 10 chữ số sau dấu . hoặc , cho vĩ độ và kinh độ; giữ nguyên chữ số khi gửi xuống chip.
                        </div>}
                        <div className="col-md-6">
                            <label htmlFor={`base-altitude-${device.serial}`} className="form-label">{activeChip === 'Ublox' || altitudeReference === 'ELLIPSOID' ? 'Độ cao h ellipsoid (mét)' : 'Độ cao theo cấu hình chip (mét)'}</label>
                            <input
                                id={`base-altitude-${device.serial}`}
                                type="text" inputMode="decimal" className="form-control"
                                value={altitude} onChange={handleDecimalInputChange(setAltitude)}
                                onPaste={handleCoordinatePaste} aria-describedby={`base-coordinate-paste-help-${device.serial}`}
                                disabled={disabled || isSubmitting}
                                required placeholder="Ví dụ: 15.5"
                            />
                            {activeChip !== 'Ublox' && <>
                                <label htmlFor={`base-altitude-reference-${device.serial}`} className="form-label small mt-2">Cách chip hiểu độ cao</label>
                                <select id={`base-altitude-reference-${device.serial}`} className="form-select"
                                    value={altitudeReference} disabled={disabled || isSubmitting}
                                    aria-describedby={`base-altitude-reference-help-${device.serial}`}
                                    onChange={event => { setAltitudeReference(event.target.value); setCoordinatePasteNotice(null); }}>
                                    <option value="ELLIPSOID">h ellipsoid WGS 84</option>
                                    <option value="LEGACY">Giữ theo cấu hình geoid hiện tại của chip</option>
                                </select>
                                <div id={`base-altitude-reference-help-${device.serial}`} className="form-text">Dán từ công cụ sẽ chọn h ellipsoid. Cấu hình cũ giữ cách hiểu hiện tại của chip.</div>
                            </>}
                        </div>
                        <div className="col-md-6">
                            <label htmlFor={`base-accuracy-${device.serial}`} className="form-label fw-bold">Độ chính xác tọa độ Base (3D)</label>
                            <div className="input-group">
                            <input
                                id={`base-accuracy-${device.serial}`}
                                type="number" className="form-control" min="0.0001" max="10000" step="0.0001"
                                aria-describedby={`base-accuracy-help-${device.serial}`}
                                value={fixedAccuracyM} onChange={(e) => setFixedAccuracyM(e.target.value)}
                                required placeholder="Ví dụ: 0.05"
                            />
                            <span className="input-group-text">m</span>
                            </div>
                            <div id={`base-accuracy-help-${device.serial}`} className="form-text">Chỉ nhập số: 5 = 5 m. Sai số 3D khai báo của tọa độ gốc; không phải độ chính xác đo được từ RTCM.</div>
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
                                required placeholder="Ví dụ: 3.0"
                            />
                            <div className="form-text text-danger">Đơn vị: MÉT (Ví dụ nhập 3.0 là 3 mét)</div>
                        </div>
                    </div>
                )}

                {(isAdmin || !isLteStation) && (
                    <div className="gnss-output-policy mt-4">
                        <div className="gnss-output-policy__header">
                            <div className="gnss-output-policy__title">
                                <i className="bi bi-broadcast-pin me-2"></i>GNSS Output Policy
                            </div>
                            <span className="gnss-output-policy__admin">{isLteStation ? 'LTE bỏ qua' : (isAdmin ? 'Admin' : 'Cài đặt bởi Admin')}</span>
                        </div>
                        {isLteStation && (
                            <div className="alert alert-info small mb-3">
                                <i className="bi bi-reception-4 me-2"></i>
                                <strong>Trạm LTE:</strong> mục NMEA + RTCM và bật/tắt hệ vệ tinh không được set từ UI.
                                Bridge LTE luôn giữ profile ổn định: lấy dữ liệu từ MQTT, mở SOURCE command khi cần cấu hình,
                                chỉ phát RTCM (MSM4 + MSM7) trên COM2. Các lựa chọn bên dưới chỉ để tham khảo và sẽ không được gửi xuống trạm LTE.
                            </div>
                        )}
                        <div className="gnss-output-policy__grid">
                            <div className="gnss-output-policy__field">
                                <label className="gnss-output-policy__label">Output mode</label>
                                <select
                                    className="form-select form-select-sm gnss-output-policy__select"
                                    value={outputMode}
                                    onChange={(e) => {
                                        const nextMode = e.target.value;
                                        setOutputMode(nextMode);
                                        if (nextMode !== 'rtcm_only' && msmLevel === 'both') setMsmLevel('4');
                                    }}
                                    disabled={isLteStation || !isAdmin}
                                >
                                    <option value="diagnostics_rtcm">NMEA + RTCM</option>
                                    <option value="rtcm_only">RTCM only</option>
                                </select>
                            </div>
                            <div className="gnss-output-policy__field">
                                <label className="gnss-output-policy__label">RTCM MSM level</label>
                                <select
                                    className="form-select form-select-sm gnss-output-policy__select"
                                    value={msmLevel}
                                    onChange={(e) => setMsmLevel(e.target.value)}
                                    disabled={isLteStation || !isAdmin}
                                >
                                    <option value="4">MSM4</option>
                                    <option value="7">MSM7</option>
                                    <option value="both" disabled={outputMode !== 'rtcm_only'}>MSM4 + MSM7 (RTCM only)</option>
                                </select>
                            </div>
                            <div className="gnss-output-policy__summary">
                                <label className="gnss-output-policy__label gnss-output-policy__label--accent">
                                    <i className="bi bi-info-circle me-1"></i>Thông tin Hệ vệ tinh & Bản tin RTCM
                                </label>
                                <div className="gnss-output-policy__info">
                                    <div>
                                        <div className="gnss-output-policy__section-title">
                                            Hệ vệ tinh hỗ trợ (Mặc định bật toàn bộ):
                                        </div>
                                        <div className="gnss-output-policy__chips">
                                            <span className="gnss-output-policy__chip">GPS</span>
                                            <span className="gnss-output-policy__chip">GLONASS</span>
                                            <span className="gnss-output-policy__chip">Galileo</span>
                                            <span className="gnss-output-policy__chip">BeiDou</span>
                                            <span className="gnss-output-policy__chip">QZSS</span>
                                            <span className="gnss-output-policy__chip gnss-output-policy__chip--muted">SBAS</span>
                                        </div>
                                    </div>
                                    <div className="gnss-output-policy__divider"></div>
                                    <div className="gnss-output-policy__rtcm">
                                        <div className="gnss-output-policy__section-title">
                                            Bản tin RTCM đầu ra ({activeChip}):
                                        </div>
                                        <div className="gnss-output-policy__copy">
                                            {activeChip === 'Ublox' ? (
                                                <span>
                                                    <strong>1005</strong> (Tọa độ Base), <strong>{msmLevel === 'both' ? '1074+1077' : (msmLevel === '7' ? '1077' : '1074')}</strong> (GPS), <strong>{msmLevel === 'both' ? '1084+1087' : (msmLevel === '7' ? '1087' : '1084')}</strong> (GLONASS), <strong>{msmLevel === 'both' ? '1094+1097' : (msmLevel === '7' ? '1097' : '1094')}</strong> (Galileo), <strong>{msmLevel === 'both' ? '1124+1127' : (msmLevel === '7' ? '1127' : '1124')}</strong> (BeiDou), <strong>1230</strong> (GLONASS Bias).
                                                </span>
                                            ) : activeChip === 'Unicorecomm' ? (
                                                <span>
                                                    <strong>1006</strong> (Tọa độ Base), <strong>1033</strong> (mô tả antenna/receiver), <strong>{msmLevel === 'both' ? '1074/1084/1094/1124 + 1077/1087/1097/1127' : (msmLevel === '7' ? '1077/1087/1097/1127' : '1074/1084/1094/1124')}</strong> ({msmLevel === 'both' ? 'MSM4 + MSM7' : `MSM${msmLevel}`}), <strong>1019/1020/1042/1045</strong> (Lịch vệ tinh GPS/GLO/BDS/GAL).
                                                </span>
                                            ) : (
                                                <span>
                                                    Bản tin tọa độ trạm (<strong>1005/1006</strong>), Tên trạm (<strong>1033</strong>) và MSM4 (<strong>1074, 1084, 1094, 1124</strong>) cho các hệ vệ tinh GPS, GLONASS, Galileo, BeiDou.
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                )}

                {!disabled && (
                    <button
                        type="submit"
                        className="btn btn-primary mt-4 w-100 base-config-submit"
                        disabled={isSubmitting || isLoadingCorsSources || (method === 'AUTO_CORS' && !selectedMountpoint)}
                    >
                        {isSubmitting ? (
                            <>
                                <span className="spinner-border spinner-border-sm me-2"></span>
                                {method === 'AUTO_CORS' ? 'Đang chạy quy trình Auto Base từ Base...' : 'Đang gửi...'}
                            </>
                        ) : (
                            <>
                                <i className="bi bi-lightning-charge-fill me-2"></i>
                                {method === 'AUTO_CORS'
                                    ? (autoFixedResult ? 'Chạy lại Auto Base từ Base' : 'Bắt đầu chạy Auto Base từ Base')
                                    : 'Áp dụng cấu hình BASE'}
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
        username1: '',
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
        rtcmpassword1: '',
        command_host: '',
        command_port: '',
        command_pass: '',
        command_mp: ''
    });
    const casterIds = config => [...new Set([1, 2, ...Object.keys(config).map(key => {
        const match = key.match(/^server(\d+)_enabled$|^serverhost(\d+)$/);
        return match ? Number(match[1] || match[2]) : 0;
    }).filter(id => Number.isSafeInteger(id) && id > 0)])].sort((a, b) => a - b);
    const serverIds = casterIds(formState);
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
        if (isSerialChanged) {
            setVisiblePasswords({});
            setRemovedServers([]);
        }

        // Hydrate only when the source config changes. Clearing dirty after save
        // must not reload the pre-save props and flip the mode back.
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
            rtcmpassword1: '',
            command_host: '',
            command_port: '',
            command_pass: '',
            command_mp: ''
        };

        if (device.service_config) {
            const merged = {
                ...defaultState,
                ...device.service_config,
                password1: isAdmin ? (device.service_config.password1 ?? (isSerialChanged ? '' : hydratedConfigRef.current.password1) ?? '') : '',
                password2: isAdmin ? (device.service_config.password2 ?? (isSerialChanged ? '' : hydratedConfigRef.current.password2) ?? '') : '',
                rtcmpassword1: isAdmin ? (device.service_config.rtcmpassword1 ?? (isSerialChanged ? '' : hydratedConfigRef.current.rtcmpassword1) ?? '') : '',
                command_pass: isAdmin ? (device.service_config.command_pass ?? (isSerialChanged ? '' : hydratedConfigRef.current.command_pass) ?? '') : ''
            };

            // Read legacy fallbacks from the received config, before defaults.
            for (const server of casterIds(merged)) {
                const key = `server${server}`;
                merged[`password${server}`] = isAdmin ? (device.service_config[`password${server}`] ?? (isSerialChanged ? '' : hydratedConfigRef.current[`password${server}`]) ?? '') : '';

                merged[`${key}_enabled`] = toBool(device.service_config[`${key}_enabled`], false);
                merged[`${key}_stream_on_demand`] = toBool(device.service_config[`${key}_stream_on_demand`], toBool(device.service_config.stream_on_demand, true));
                merged[`${key}_stream_active`] = toBool(device.service_config[`${key}_stream_active`], toBool(device.service_config.stream_active, false));
                if (!merged[`${key}_stream_on_demand`]) merged[`${key}_stream_active`] = true;
            }

            Object.assign(merged, Object.fromEntries(Object.entries(hostPolicy).filter(([key]) => key !== "locked_servers")));
            hydratedConfigRef.current = merged;
            setFormState(merged);
            setIsFormDirty(false);
            lastHydratedSerialRef.current = device.serial;
        } else {
            hydratedConfigRef.current = defaultState;
            setFormState(defaultState);
            setIsFormDirty(false);
            lastHydratedSerialRef.current = device.serial;
        }
    }, [device.serial, device.service_config, device.server_config_lock, isAdmin]);

    const renderCasterStatus = (serverId) => {
        const key = `server${serverId}`;
        const saved = device.service_config || {};
        const enabled = toBool(formState[`${key}_enabled`]);
        const pending = ['serverhost', 'port', 'mountpoint', 'username'].some(prefix =>
            String(formState[`${prefix}${serverId}`] ?? '') !== String(saved[`${prefix}${serverId}`] ?? '')
        ) || enabled !== toBool(saved[`${key}_enabled`]);
        const telemetry = window.CorsDashboardShared?.getServerTelemetry(device, serverId) || {};
        const offline = !!window.CorsDashboardShared?.getOfflineInfo(device) || device.status === 'offline';
        let label = 'Chờ trạng thái';
        let icon = 'bi-hourglass-split';
        let tone = 'secondary';
        if (blockedServer(serverId)) {
            label = lockApplied ? 'Đã khoá · ngừng gửi' : 'Khoá · chờ áp dụng'; icon = 'bi-lock-fill'; tone = 'danger';
        } else if (!enabled) {
            label = 'Đã tắt'; icon = 'bi-power'; tone = 'secondary';
        } else if (pending) {
            label = 'Chưa áp dụng'; icon = 'bi-pencil-square'; tone = 'warning';
        } else if (offline) {
            label = 'Trạm mất kết nối'; icon = 'bi-cloud-slash'; tone = 'danger';
        } else if (telemetry.connected) {
            label = 'Đã kết nối Caster'; icon = 'bi-check-circle-fill'; tone = 'success';
        } else if (telemetry.hasConnection || telemetry.hasBps) {
            label = 'Chưa kết nối Caster'; icon = 'bi-exclamation-circle'; tone = 'warning';
        }
        const sleeping = telemetry.sleep ?? (
            toBool(telemetry.onDemand, toBool(saved[`${key}_stream_on_demand`], toBool(saved.stream_on_demand))) &&
            !toBool(telemetry.active, toBool(saved[`${key}_stream_active`], toBool(saved.stream_active)))
        );
        return (
            <span className="d-inline-flex flex-wrap align-items-center gap-2 ms-2 small" role="status" aria-live="polite">
                <span className={`badge text-bg-${enabled ? 'primary' : 'secondary'}`}>
                    <i className={`bi ${enabled ? 'bi-toggle-on' : 'bi-toggle-off'} me-1`} aria-hidden="true"></i>
                    {enabled ? 'Đã bật' : 'Đã tắt'}
                </span>
                <span className={`badge text-bg-${tone}`}>
                    <i className={`bi ${icon} me-1`} aria-hidden="true"></i>{label}
                </span>
                {enabled && !blockedServer(serverId) && !pending && !offline && (
                    <span className={`badge text-bg-${sleeping ? 'info' : telemetry.bps > 0 ? 'success' : 'secondary'}`}>
                        <i className={`bi ${sleeping ? 'bi-moon-stars' : 'bi-broadcast'} me-1`} aria-hidden="true"></i>
                        {sleeping ? 'Ngủ · chờ rover' : telemetry.bps > 0 ? `Đang gửi RTCM · ${telemetry.bps} B/s` : 'Chưa gửi RTCM'}
                    </span>
                )}
            </span>
        );
    };

    const renderPasswordInput = (name, label) => (
        <div className="input-group">
            <input
                type={visiblePasswords[name] ? 'text' : 'password'}
                className="form-control"
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

            const streamMatch = name.match(/^server(\d+)_stream_on_demand$/);
            if (streamMatch) next[`server${streamMatch[1]}_stream_active`] = !checked;
            if (name === 'stream_on_demand') {
                for (const sid of serverIds) {
                    next[`server${sid}_stream_on_demand`] = !!checked;
                    next[`server${sid}_stream_active`] = !checked;
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
        if (disabled) return;
        setIsSubmitting(true);

        try {
            const derivedGlobalOnDemand = serverIds.some(sid => toBool(formState[`server${sid}_enabled`]) && toBool(formState[`server${sid}_stream_on_demand`]));
            const derivedGlobalActive = serverIds.some(sid => toBool(formState[`server${sid}_enabled`]) &&
                (!toBool(formState[`server${sid}_stream_on_demand`]) || toBool(formState[`server${sid}_stream_active`])));

            const payload = {
                ...formState,
                stream_on_demand: derivedGlobalOnDemand,
                stream_active: derivedGlobalActive,
                ntrip_version1: parseInt(formState.ntrip_version1, 10),
                ntrip_version2: parseInt(formState.ntrip_version2, 10),
                port1: formState.port1 ? parseInt(formState.port1, 10) : null,
                port2: formState.port2 ? parseInt(formState.port2, 10) : null,
                command_port: formState.command_port ? parseInt(formState.command_port, 10) : null,
                rtcmntripversion1: parseInt(formState.rtcmntripversion1, 10) === 2 ? 2 : 1,
                rtcmport1: formState.rtcmport1 ? parseInt(formState.rtcmport1, 10) : null,
                reconnectioninterval: parseInt(formState.reconnectioninterval, 10)
            };
            for (const sid of serverIds) {
                payload[`ntrip_version${sid}`] = parseInt(formState[`ntrip_version${sid}`] || 1, 10);
                payload[`port${sid}`] = formState[`port${sid}`] ? parseInt(formState[`port${sid}`], 10) : null;
            }
            if (isLteStation) {
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
                    rtcmpassword1: null
                });
            }

            for (const key of [...serverIds.map(sid => `password${sid}`), 'rtcmpassword1', 'command_pass']) {
                if (!payload[key]) delete payload[key];
            }

            if (hostsLocked && !isAdmin) {
                for (const key of Object.keys(hostPolicy)) delete payload[key];
                for (const sid of serverIds) {
                    delete payload[`server${sid}_stream_on_demand`];
                    delete payload[`server${sid}_stream_active`];
                    if (blockedServer(sid)) {
                        for (const prefix of ['ntrip_version', 'username', 'mountpoint', 'password']) delete payload[`${prefix}${sid}`];
                    }
                }
                delete payload.stream_on_demand; delete payload.stream_active;
            }

            const streamSettingsChanged = serverIds.some(sid => ['enabled', 'stream_on_demand', 'stream_active'].some(suffix => {
                const key = `server${sid}_${suffix}`;
                return String(formState[key] ?? '') !== String(hydratedConfigRef.current[key] ?? '');
            }));
            if (!streamSettingsChanged) {
                delete payload.stream_on_demand;
                delete payload.stream_active;
            }

            // Send only edited values; telemetry/password placeholders are not changes.
            for (const key of Object.keys(payload)) {
                if (String(payload[key] ?? '') === String(hydratedConfigRef.current[key] ?? '')) {
                    delete payload[key];
                }
            }
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

            <div className="mb-4 p-3 border rounded border-secondary">
                <h5 className="mb-3">Cài đặt chung</h5>
                <div className="mb-3 p-2 rounded bg-dark border border-secondary-subtle">
                    <div className="d-flex align-items-center justify-content-between mb-1">
                        <span className="text-muted small">Phiên bản Agent:</span>
                        <span className="fw-bold small text-light">{device.version || 'Không xác định'}</span>
                    </div>
                    <div className="d-flex align-items-center justify-content-between">
                        <span className="text-muted small">Trạng thái đồng bộ:</span>
                        {device.is_synced ? (
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
                                            <label className="form-label">Command Host / IP</label>
                                            <input
                                                type="text"
                                                className="form-control"
                                                name="command_host"
                                                value={formState.command_host}
                                                onChange={handleChange}
                                                placeholder="aitogy.com.vn"
                                            />
                                        </div>
                                        <div className="col-md-4">
                                            <label className="form-label">Command Port</label>
                                            <input
                                                type="number"
                                                className="form-control"
                                                name="command_port"
                                                value={formState.command_port}
                                                onChange={handleChange}
                                                placeholder="2101"
                                            />
                                        </div>
                                        <div className="col-md-6">
                                            <label className="form-label">Command Password</label>
                                            {renderPasswordInput('command_pass', 'mật khẩu Command')}
                                        </div>
                                        <div className="col-md-6">
                                            <label className="form-label">Command Mountpoint</label>
                                            <input
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
                                        <label className="form-label">Port</label>
                                        <input
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
                                        {renderPasswordInput('password1', 'mật khẩu Caster 1')}
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
                                        <label className="form-label">Port</label>
                                        <input
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
                                        {renderPasswordInput('password2', 'mật khẩu Caster 2')}
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
                                                    checked={parseInt(formState[`ntrip_version${sid}`] || 1, 10) === version} onChange={handleChange} />
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
                                            <label className="form-label">Password</label>{renderPasswordInput(`password${sid}`, `Mật khẩu Caster ${sid}`)}
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


window.CorsDashboardForms = {
    LicenseForm,
    ProvisionForm,
    BaseConfigForm,
    ServiceConfigForm
};
window.LicenseForm = LicenseForm;
window.ProvisionForm = ProvisionForm;
window.BaseConfigForm = BaseConfigForm;
window.ServiceConfigForm = ServiceConfigForm;
})();
