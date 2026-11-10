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
    'auto_setup_averaging': 'Bước 3/4: Thu thập mẫu RTK Fixed & Tính trung bình',
    'auto_setup_base': 'Bước 4/4: Hoàn thiện kết quả Auto Base'
};
const projectionLabel = report => Math.abs(Number(report?.k0) - 0.9999) <= 1e-9 ? 'múi 3°' : isSixDegreeReport(report) ? 'múi 6°' : 'phép chiếu tùy chọn';
const isSixDegreeReport = report => Number.isFinite(Number(report?.k0)) && Math.abs(Number(report.k0) - 0.9996) <= 1e-9;
// Show the nominal VN M6 zone constants when RTCM's quantized values decode a few nanodegrees off.
const displayMeridian = (value, report) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'Chưa có dữ liệu';
    const raw = Number(value);
    if (isSixDegreeReport(report)) {
        const nominal = [105, 111].find(center => Math.abs(raw - center) <= 5.6e-9);
        if (nominal !== undefined) return String(nominal);
    }
    return String(value);
};
const displayProjectionScale = report => isSixDegreeReport(report) ? '0.9996' : report?.k0 ?? 'Chưa có dữ liệu';
const formatReportCoordinate = (value, key) => value == null || !Number.isFinite(Number(value))
    ? 'Chưa có dữ liệu' : Number(value).toFixed(key === 'alt' ? 4 : 10);
const Vn2000Result = ({ report, onReviewChange }) => {
    const sourceKey = JSON.stringify([report?.projected_coord, report?.central_meridian_deg, report?.k0, report?.rtcm1025]);
    const [preview, setPreview] = useState(null);
    const [meridian, setMeridian] = useState('');
    const [scale, setScale] = useState('0.9999');
    const [calculating, setCalculating] = useState(false);
    const [error, setError] = useState('');
    const requestRef = useRef(0);
    useEffect(() => {
        ++requestRef.current;
        setPreview(null); setError(''); setCalculating(false);
        setMeridian(report?.central_meridian_deg == null ? '' : displayMeridian(report.central_meridian_deg, report));
        setScale(isSixDegreeReport(report) ? '0.9996' : '0.9999');
        return () => { ++requestRef.current; };
    }, [sourceKey]);
    if (!report?.transform_applied) return null;
    const displayed = preview?.sourceKey === sourceKey ? preview : report;
    const neh = displayed.projected_coord;
    const hasNeh = neh && ['north', 'east', 'h'].every(key =>
        neh[key] != null && neh[key] !== '' && Number.isFinite(Number(neh[key])));
    const decoded = report.rtcm1025;
    const canRecalculate = !onReviewChange || (report.input_coord && report.parameters && report.parameter_source === 'rtcm1021');
    const hasDecoded = decoded && decoded.raw_k0 != null && decoded.raw_l0 != null;
    const projectionSource = {rtcm1025:'CORS · RTCM 1025', manual:'Nhập L₀/k₀ thủ công', server_catalog:'Danh mục tỉnh trên máy chủ', not_used:'Không chuyển đổi'}[report.projection_source] || 'Báo cáo chưa lưu nguồn phép chiếu';
    async function reproject() {
        if (!canRecalculate || calculating || !meridian.trim() || !Number.isFinite(Number(meridian)) || Number(meridian) < 100 || Number(meridian) > 112) return;
        const request = ++requestRef.current;
        setCalculating(true); setError('');
        onReviewChange?.({ pending: true });
        const offsets = { false_easting: Number(decoded?.false_easting ?? 500000),
            false_northing: Number(decoded?.false_northing ?? 0) };
        try {
            const response = await window.apiFetch(onReviewChange ? '/api/coordinates/recalculate-auto-base' : '/api/coordinates/reproject-display', {
                method: 'POST', body: JSON.stringify(onReviewChange ? {
                    input_coord: report.input_coord, parameters: report.parameters,
                    apply_residual: report.residual_source === 'rtcm1023',
                    projection: { l0_deg: Number(meridian), k0: Number(scale), ...offsets }
                } : {
                    ...report.projected_coord,
                    source: { l0_deg: Number(report.central_meridian_deg), k0: Number(report.k0), ...offsets },
                    target_l0_deg: Number(meridian), target_k0: Number(scale)
                })
            });
            const data = await response.json();
            if (request !== requestRef.current) return;
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Không tính được múi hiển thị. Kiểm tra L₀ và dữ liệu gốc.');
            setPreview({ ...data, sourceKey });
            onReviewChange?.({ result: data });
        } catch (error) {
            if (request === requestRef.current) setError(error.message);
        } finally {
            if (request === requestRef.current) setCalculating(false);
        }
    }
    function changeProjection(setter, value) {
        setter(value);
        if (onReviewChange) setPreview(null);
        onReviewChange?.({ pending: true });
    }
    function resetProjection() {
        setPreview(null); setError('');
        setMeridian(displayMeridian(report.central_meridian_deg, report));
        setScale(isSixDegreeReport(report) ? '0.9996' : '0.9999');
        onReviewChange?.(null);
    }
    return <section className="border rounded p-3 mb-3" aria-label="Kết quả VN-2000 NEh" aria-busy={calculating}>
        <h3 className="h6 mb-2">VN-2000 — NEh · {projectionLabel(displayed)}</h3>
        {hasNeh ? <dl className="row g-2 small mb-2">
            {[['north', 'N — Northing (m)'], ['east', 'E — Easting (m)'], ['h', 'h — Độ cao ellipsoid (m)']].map(([key, label]) => (
                <div className="col-sm-4" key={key}><dt className="text-muted fw-normal">{label}</dt>
                    <dd className="font-monospace text-break mb-0">{Number(neh[key]).toFixed(4)}</dd></div>
            ))}
        </dl> : <p className="small text-muted mb-2">Phiên cũ chưa lưu NEh. Cần lấy lại kết quả Auto Base để hiển thị đủ N, E, h.</p>}
        <div className="small text-muted" role="status" aria-live="polite">Kinh tuyến trục: {displayMeridian(displayed.central_meridian_deg, displayed)}° · k₀: {displayProjectionScale(displayed)}.{preview?.sourceKey === sourceKey && (onReviewChange ? ' Đã tính lại NEh và LLH.' : ' Đã đổi múi N/E.')}</div>
        {hasNeh && <div className="border-top mt-3 pt-3">
            <div className="d-flex flex-wrap align-items-end gap-2">
                <label className="small">{onReviewChange ? 'Múi tính tọa độ' : 'Múi hiển thị'}
                    <select className="form-select mt-1" style={{minHeight:44}} value={scale} disabled={calculating}
                        onChange={event => changeProjection(setScale, event.target.value)}>
                        <option value="0.9999">3° · k₀ = 0.9999</option>
                        <option value="0.9996">6° · k₀ = 0.9996</option>
                    </select>
                </label>
                <label className="small">Kinh tuyến trục L₀ (°)
                    <input type="number" min="100" max="112" step="any" className="form-control mt-1" style={{minHeight:44,maxWidth:220}}
                        value={meridian} disabled={calculating} onChange={event => changeProjection(setMeridian, event.target.value)} />
                </label>
                <button type="button" className="btn btn-outline-info" style={{minHeight:44}}
                    disabled={!canRecalculate || calculating || !meridian.trim() || !Number.isFinite(Number(meridian)) || Number(meridian) < 100 || Number(meridian) > 112} onClick={reproject}>
                    {calculating && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />}
                    {calculating ? 'Đang tính…' : onReviewChange ? 'Tính lại NEh và LLH' : 'Đổi múi N/E'}
                </button>
                {(onReviewChange || preview?.sourceKey === sourceKey) && <button type="button" className="btn btn-outline-secondary" style={{minHeight:44}} disabled={calculating}
                    onClick={resetProjection}>Dùng kết quả gốc</button>}
            </div>
            <p className="small text-muted mt-2 mb-0">{!canRecalculate ? 'Phiên này thiếu tham số RTCM để tính lại.' : onReviewChange
                ? 'Dùng lại mẫu RTK Fixed của phiên này. Kiểm tra LLH mới trước khi áp dụng vào Base.'
                : 'Đổi múi để xem N/E của tọa độ đã lưu.'}</p>
            {error && <p className="small text-danger mt-2 mb-0" role="alert">{error}</p>}
        </div>}
        <p className="small mt-2 mb-1">Nguồn phép chiếu gốc: <strong>{projectionSource}</strong>.</p>
        <p className="small text-muted mb-0">Nhãn múi 3°/6° suy theo k₀ (0.9999/0.9996), không phải tỉnh được nhận diện. Ưu tiên RTCM 1025; nếu thiếu thì dùng nhập tay, sau đó danh mục tỉnh.</p>
        {(hasDecoded || report.projection_source === 'rtcm1025') && <details className="small mt-2">
            <summary className="py-2" style={{minHeight:44}}>Đối chiếu giải mã RTCM 1025</summary>
            {hasDecoded ? <>
                <p className="mb-2">Bộ giải mã: {report.processor === 'esp32' ? 'ESP32' : report.processor === 'python_agent' ? 'Agent Python trên Pi' : 'Chưa ghi nhận'} · {decoded.frame_bytes ?? '—'} byte.</p>
                <dl className="row g-2 mb-2">
                    {[
                        ['Giá trị LoNO thô', decoded.raw_l0], ['Giá trị SNO thô', decoded.raw_k0],
                        ['L₀ giải mã (°)', decoded.l0_deg], ['k₀ giải mã', decoded.k0],
                        ['L₀ kết quả gốc (°)', report.central_meridian_deg], ['k₀ kết quả gốc', report.k0],
                        ['Mã loại phép chiếu trong 1025', decoded.projection_type],
                        ['False Easting (m)', decoded.false_easting], ['False Northing (m)', decoded.false_northing],
                    ].map(([label,value]) => <div className="col-sm-6" key={label}><dt className="text-muted fw-normal">{label}</dt><dd className="font-monospace text-break mb-0">{value == null ? 'Chưa ghi nhận' : String(value)}</dd></div>)}
                </dl>
                <p className="text-break mb-2">L₀ = LoNO × 1.1e−8 độ; k₀ = (SNO × 0.00001 + 993000) × 0.000001.</p>
                {decoded.frame_hex && <><div>Khung RTCM 1025 gốc (hex){decoded.frame_truncated ? ' · chỉ lưu 32 byte đầu' : ''}:</div><code className="d-block text-break" data-no-translate>{decoded.frame_hex}</code></>}
            </> : <p className="text-warning mb-0" role="status">Báo cáo cũ chưa lưu trường thô và khung 1025 gốc. Cần lấy lại Auto Base bằng firmware/agent mới để đối chiếu; chưa thể xác nhận decoder chỉ từ nhãn múi chiếu.</p>}
        </details>}
    </section>;
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
                <label htmlFor="pi-license-serial" className="form-label">Serial Number của Pi</label>
                <div className="input-group">
                    <input id="pi-license-serial" type="text" className="form-control" value={device.serial} readOnly />
                    <button
                        className="btn btn-outline-secondary"
                        type="button"
                        title="Sao chép Serial"
                        onClick={() => {
                            navigator.clipboard.writeText(device.serial).then(
                                () => window.showSuccess('Đã sao chép Serial!'),
                                () => window.showError?.("Không thể sao chép vào bộ nhớ tạm.")
                            );
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

const GnssReadbackDetails = ({ details }) => {
    const [portName, setPortName] = useState('');
    const [enabledOnly, setEnabledOnly] = useState(true);
    const ublox = details.kind === 'Ublox';
    const names = ublox ? ['I2C', 'UART1', 'UART2', 'USB', 'SPI'] : ['COM1', 'COM2', 'COM3'];
    const selected = names.includes(portName) ? portName : details.compare_ports?.find(name => names.includes(name)) || names[0];
    const actualFixed = details.fixed?.actual || {};
    const expectedFixed = details.fixed?.expected || {};
    const fixedRows = [
        ['Mode', actualFixed.mode, expectedFixed.mode],
        ['Kiểu tọa độ', actualFixed.coordinate_type, expectedFixed.coordinate_type],
        ['Vĩ độ', actualFixed.self_position?.lat, expectedFixed.self_position?.lat],
        ['Kinh độ', actualFixed.self_position?.lon, expectedFixed.self_position?.lon],
        ['Độ cao (m)', actualFixed.self_position?.alt, expectedFixed.self_position?.alt],
        ['Độ chính xác khai báo (m)', actualFixed.accuracy_m, expectedFixed.accuracy_m],
        ['Độ cao chip báo (m)', actualFixed.height_msl, expectedFixed.height_msl],
        ['Undulation (m)', actualFixed.undulation, expectedFixed.undulation],
        ['Datum', actualFixed.datum, expectedFixed.datum],
        ['Trạng thái vị trí', actualFixed.position_type, expectedFixed.position_type],
    ].filter(([, actual, expected]) => actual != null || expected != null);
    const fields = details.fields || [];
    const ports = details.ports || [];
    const observed = ports.find(port => port.name === selected);
    const compare = details.compare_ports?.includes(selected);
    const messageIndexes = fields.map((_, index) => index).filter(index => index >= 3 && fields[index] !== 'PORT_ENABLED'
        && (!enabledOnly || observed?.values?.[index] !== 0 || (compare && details.expected?.[index] > 0)));
    const logs = details.logs || [];
    const expectedLogs = compare ? details.expected_logs || [] : [];
    const rate = value => value === 0 ? 'Tắt' : value == null ? 'Chưa đọc được' : `Mỗi ${value} chu kỳ`;
    const show = value => value == null ? 'Chưa đọc được' : String(value);
    const logNames = [...new Set([...logs.filter(log => log.port === selected).map(log => log.name), ...expectedLogs])].sort((a, b) => a.localeCompare(b))
        .filter(name => !enabledOnly || expectedLogs.includes(name) || logs.some(log => log.port === selected && log.name === name && log.period > 0));
    return <div className="mt-3">
        <h6>Cấu hình chip trả về</h6>
        <div className="table-responsive border rounded mb-3" tabIndex={0} role="region" aria-label="Mode và tọa độ; cuộn ngang để xem đủ các cột">
            <table className="table table-sm align-middle mb-0 small" style={{ minWidth: 520 }}>
                <caption className="visually-hidden">Mode và tọa độ đọc từ chip, so với cấu hình yêu cầu</caption>
                <thead><tr><th scope="col">Thông số</th><th scope="col">Chip trả về</th><th scope="col">Yêu cầu</th></tr></thead>
                <tbody>{fixedRows.map(([label, actual, expected]) => <tr key={label}>
                    <th scope="row" className="fw-normal">{label}</th>
                    <td className="font-monospace text-nowrap">{show(actual)}</td>
                    <td className="font-monospace text-nowrap">{expected == null ? 'Không đặt' : show(expected)}</td>
                </tr>)}</tbody>
            </table>
        </div>
        <h6>Cổng và bản tin đầu ra</h6>
        <p className="small text-muted">Đang xem {selected}. Cổng đối chiếu: {details.compare_ports?.join(', ') || 'Chưa chọn'}.</p>
        <div className="table-responsive border rounded">
            <table className="table table-sm align-middle mb-0 small">
                <caption className="visually-hidden">Các cổng đầu ra được đọc trực tiếp từ chip</caption>
                <thead><tr><th scope="col">Cổng</th>{ublox ? <><th scope="col">Cổng bật</th><th scope="col">Baud</th><th scope="col">UBX</th><th scope="col">NMEA</th><th scope="col">RTCM</th></> : <th scope="col">Log định kỳ</th>}</tr></thead>
                <tbody>{names.map(name => {
                    const port = ports.find(item => item.name === name);
                    const ready = ublox ? port?.state === 'ok' : details.logs_state === 'ok';
                    return <tr key={name} className={selected === name ? 'table-active' : ''}>
                        <th scope="row"><button type="button" className={`btn btn-sm ${selected === name ? 'btn-info' : 'btn-outline-info'}`}
                            style={{ minHeight: 44 }} aria-pressed={selected === name} onClick={() => setPortName(name)}>{name}</button></th>
                        {ublox ? <>
                            <td>{!ready || port.enabled == null ? 'Chưa đọc được' : port.enabled === 1 ? 'Bật' : port.enabled === 0 ? 'Tắt' : String(port.enabled)}</td>
                            <td>{name.startsWith('UART') ? port?.baud != null ? `${port.baud} bps` : 'Chưa đọc được' : '—'}</td>
                            {[0, 1, 2].map(index => <td key={index} className={ready && port.values?.[index] === 1 ? 'text-success-emphasis' : 'text-body-secondary'}>
                                {!ready ? 'Chưa đọc được' : port.values?.[index] === 1 ? 'Bật' : port.values?.[index] === 0 ? 'Tắt' : show(port.values?.[index])}
                            </td>)}
                        </> : <td>{ready ? logs.filter(log => log.port === name && log.period > 0).length : 'Chưa đọc được'}</td>}
                    </tr>;
                })}</tbody>
            </table>
        </div>
        <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mt-3 mb-2">
            <div className="fw-semibold">Bản tin trên {selected}</div>
            <label className="form-check-label d-flex align-items-center gap-2 small" style={{ minHeight: 44 }}>
                <input className="form-check-input m-0" type="checkbox" checked={enabledOnly} onChange={event => setEnabledOnly(event.target.checked)} />Chỉ bản tin đang bật
            </label>
        </div>
        {ublox ? observed?.state !== 'ok' ? <div className="small text-warning">Chưa đọc được danh sách bản tin trên {selected}.</div> : <>
            <div className="table-responsive border rounded">
                <table className="table table-sm align-middle mb-0 small">
                    <caption className="visually-hidden">Tốc độ bản tin đọc từ chip trên {selected}</caption>
                    <thead><tr><th scope="col">Bản tin</th><th scope="col">Chip trả về</th><th scope="col">Yêu cầu</th></tr></thead>
                    <tbody>{messageIndexes.map(index => {
                        const field = fields[index];
                        const actual = observed.values?.[index];
                        const expected = compare ? details.expected?.[index] : null;
                        if (enabledOnly && actual === 0 && !(expected > 0)) return null;
                        return <tr key={field}>
                            <th scope="row" className="fw-normal">{field}</th>
                            <td className={expected != null && actual !== expected ? 'text-danger-emphasis' : ''}>{rate(actual)}</td>
                            <td>{expected == null ? 'Không đặt' : rate(expected)}</td>
                        </tr>;
                    })}{messageIndexes.length === 0 && <tr><td colSpan={3} className="text-muted">Không có bản tin định kỳ đang bật trong danh sách đã đọc.</td></tr>}</tbody>
                </table>
            </div>
            <p className="small text-muted mt-2 mb-0">Tốc độ tính theo chu kỳ đo của chip. Bảng liệt kê các bản tin trong bộ cấu hình Base. Cổng và giao thức tương ứng phải bật để xuất bản tin.</p>
        </> : details.logs_state !== 'ok' ? <div className="small text-warning">Chưa đọc được danh sách log từ UM.</div> : <div className="table-responsive border rounded">
            <table className="table table-sm align-middle mb-0 small">
                <caption className="visually-hidden">Danh sách log UM trên {selected}</caption>
                <thead><tr><th scope="col">Bản tin</th><th scope="col">Chip trả về</th><th scope="col">Yêu cầu</th></tr></thead>
                <tbody>{logNames.map(name => {
                    const actual = logs.find(log => log.port === selected && log.name === name);
                    const expected = expectedLogs.includes(name);
                    if (enabledOnly && actual?.period == null && !expected) return null;
                    return <tr key={name}><th scope="row" className="fw-normal">{name}</th>
                        <td className={compare && ((!expected && actual?.period > 0) || (expected && actual?.period !== 1)) ? 'text-danger-emphasis' : ''}>{!actual ? 'Không có trong danh sách' : actual.period == null ? 'Một lần' : `${actual.period} giây`}</td>
                        <td>{expected ? '1 giây' : compare ? 'Không bật' : 'Không đối chiếu'}</td></tr>;
                })}</tbody>
            </table>
            {logNames.length === 0 && <div className="small text-muted p-2">Chip không liệt kê log trên cổng này.</div>}
        </div>}
    </div>;
};

const GnssConfigVerification = ({ device, disabled }) => {
    const [pending, setPending] = useState(null);
    const [result, setResult] = useState(null);
    const [expired, setExpired] = useState(false);
    const [notice, setNotice] = useState('');
    const [comparisonPort, setComparisonPort] = useState('COM1');
    const requestRef = useRef(0);
    const info = device.system_info || {};
    const report = info.gnss_verification;
    const selectComparisonPort = device.detected_chip_type === 'Unicorecomm' && info.gnss_verification_uart1 === true;
    const action = device.last_command_result;
    const configSnapshot = JSON.stringify(device.base_config || {});
    const offline = device.status === 'offline' || !!window.CorsDashboardShared?.isDeviceStaleOffline?.(device);
    const supported = info.gnss_verification_supported === true;
    const fixed = device.base_config?.base_setup_method === 'FIXED_LLA';
    useEffect(() => {
        ++requestRef.current;
        setPending(null); setResult(null); setExpired(false); setNotice('');
        return () => { ++requestRef.current; };
    }, [device.serial, configSnapshot, device.status_boot_id]);
    useEffect(() => {
        if (!pending) return;
        if (pending.id && report?.command_id === pending.id && Number(report.age_seconds) <= 60) {
            setResult(report); setPending(null); setNotice('');
            return;
        }
        if (pending.id && action?.command_id === pending.id && ['error', 'rejected'].includes(action.status)) {
            setPending(null); setNotice('Thiết bị chưa xác minh được cấu hình. Hãy thử lại khi GNSS sẵn sàng.');
            return;
        }
        const timer = setTimeout(() => {
            ++requestRef.current;
            setPending(null); setNotice('Chưa nhận được kết quả từ thiết bị. Hãy kiểm tra kết nối rồi thử lại.');
        }, 45000);
        return () => clearTimeout(timer);
    }, [pending, report, action]);
    useEffect(() => {
        if (!result) return;
        const timer = setTimeout(() => {
            setExpired(true);
        }, Math.max(0, 60 - Number(result.age_seconds || 0)) * 1000);
        return () => clearTimeout(timer);
    }, [result]);
    const blocked = disabled || offline || device.is_locked || !supported || !fixed || !!pending;
    async function verify() {
        if (blocked) return;
        const request = ++requestRef.current;
        setResult(null); setExpired(false); setNotice(''); setPending({ id: null });
        try {
            const response = await window.DeviceApi.sendCommand(device.serial, 'VERIFY_GNSS_CONFIG', selectComparisonPort ? { port: comparisonPort } : {});
            const data = await response.json();
            if (request !== requestRef.current) return;
            if (!response.ok || !data.command_id) throw new Error(data.detail || 'Không gửi được yêu cầu xác minh.');
            setPending({ id: data.command_id });
        } catch (error) {
            if (request !== requestRef.current) return;
            setPending(null); setNotice(error.message);
        }
    }
    return <section className="border-top border-secondary mt-3 pt-3" aria-label="Xác minh cấu hình" aria-busy={!!pending}>
        {selectComparisonPort && <label className="d-inline-flex align-items-center gap-2 me-2 mb-2 small">
            Cổng đối chiếu
            <select className="form-select form-select-sm w-auto" style={{ minHeight: 44 }} value={comparisonPort} disabled={!!pending}
                onChange={event => { setComparisonPort(event.target.value); setResult(null); setNotice(''); }}>
                {['COM1', 'COM2', 'COM3'].map(port => <option key={port} value={port}>{port}</option>)}
            </select>
        </label>}
        <button type="button" className="btn btn-outline-info" style={{ minHeight: 44 }} disabled={blocked} onClick={verify}>
            {pending && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />}
            {pending ? 'Đang xác minh…' : 'Xác minh cấu hình'}
        </button>
        {!supported ? <div className="small text-muted mt-2">Cập nhật agent để xác minh cấu hình.</div>
            : !fixed ? <div className="small text-muted mt-2">Lưu cấu hình Fixed LLA trước khi xác minh.</div>
            : <div className="small text-muted mt-2">Đối chiếu cấu hình đang chạy với cấu hình đã lưu. Không thay đổi cấu hình chip.</div>}
        <div role="status" aria-live="polite" aria-atomic="true">
            {pending && <div className="small text-info mt-2">Đang chờ chip trả cấu hình…</div>}
            {notice && <div className="small text-warning mt-2">{notice}</div>}
            {result && <>
                <div className={`small fw-bold mt-2 ${offline || expired || result.state !== 'match' ? 'text-warning' : 'text-success-emphasis'}`}>
                    {offline ? 'Thiết bị ngoại tuyến. Kết quả là lần xác minh gần nhất.'
                        : expired ? 'Kết quả là lần đọc trước. Xác minh lại để cập nhật.'
                        : result.state === 'match' ? 'Cấu hình đang chạy khớp.'
                        : result.state === 'mismatch' ? 'Cấu hình chưa khớp.' : 'Chưa đọc đủ cấu hình để xác minh.'}
                </div>
                {result.details?.ports?.some(port => port.state !== 'ok') && <div className="small text-warning mt-2">Một số cổng chưa đọc được; xem trạng thái trong bảng.</div>}
                {result.details ? <GnssReadbackDetails details={result.details} />
                    : <div className="small text-warning mt-2">Cập nhật agent để xem giá trị và từng cổng chip trả về.</div>}
                <details className="mt-3"><summary>Đối chiếu các nhóm cấu hình</summary>
                <dl className="row g-2 small mt-2 mb-0">
                    {[[ 'fixed', device.detected_chip_type === 'Ublox' ? 'Fixed LLA và độ chính xác' : 'Fixed LLA'],
                        ...(device.detected_chip_type === 'Ublox' ? [['protocols', 'Giao thức đầu ra']] : []),
                        ['diagnostics', device.detected_chip_type === 'Ublox' ? 'Bản tin NMEA / UBX' : 'Bản tin NMEA'],
                        ['rtcm', 'Bản tin RTCM']].map(([key, label]) => {
                        const group = result.groups?.[key];
                        const unread = !group || Number(group.unread) > 0 || !(Number(group.checked) > 0);
                        const mismatch = Number(group?.mismatched) > 0;
                        return <React.Fragment key={key}>
                            <dt className="col-7 fw-normal">{label}</dt>
                            <dd className={`col-5 text-end mb-0 ${unread ? 'text-warning' : mismatch ? 'text-danger-emphasis' : 'text-success-emphasis'}`}>
                                {unread ? 'Chưa đọc được' : mismatch ? `Sai (${group.mismatched}/${group.checked})` : 'Khớp'}
                            </dd>
                        </React.Fragment>;
                    })}
                </dl>
                </details>
                {result.note && <div className="small text-warning mt-2">{result.note}</div>}
            </>}
        </div>
    </section>;
};

// Keep per-station form drafts across navigation in this tab; credentials stay in memory.
const autoBaseDrafts = new Map();
const BaseConfigForm = memo(function BaseConfigForm({ device, currentUser = {}, onConfigureChip, onStopAutoBase, onReadBaseStatus, disabled }) {
    const knownChips = ['Ublox', 'Unicorecomm'];
    const needsManualSelection = !knownChips.includes(device.detected_chip_type);

    const [manualChip, setManualChip] = useState(device.detected_chip_type === 'RTCM3_Source' ? 'Unicorecomm' : 'Ublox');
    const [method, setMethod] = useState('AUTO_CORS');
    const activeChip = needsManualSelection ? manualChip : device.detected_chip_type;
    const isLteStation = !!device?.is_lte || String(device?.connection_type || '').toUpperCase() === 'LTE';

    const ACTIVE_AUTO_BASE_PHASES = ['queued', 'awaiting_source', 'starting', 'set_rover', 'wait_fix', 'averaging', 'resolving_location', 'set_base'];
    const isAutoSetupStatus = (device?.status || '').startsWith('auto_setup');
    const autoProgress = device?.auto_base_progress || {};
    const isAutoSetupActive = isAutoSetupStatus || ACTIVE_AUTO_BASE_PHASES.includes(autoProgress.phase);
    const [isStoppingAutoBase, setIsStoppingAutoBase] = useState(false);
    useEffect(() => {
        if (!isAutoSetupActive) setIsStoppingAutoBase(false);
    }, [isAutoSetupActive]);
    useEffect(() => { setIsStoppingAutoBase(false); }, [device.serial]);
    useEffect(() => {
        if (!isStoppingAutoBase) return;
        const timer = setTimeout(() => {
            setIsStoppingAutoBase(false);
            window.showError?.('Chưa nhận được xác nhận dừng Auto Base. Kiểm tra kết nối rồi thử lại.');
        }, 45000);
        return () => clearTimeout(timer);
    }, [isStoppingAutoBase]);
    async function stopAutoBase() {
        if (disabled || isStoppingAutoBase || !onStopAutoBase) return;
        const ok = await window.CorsUI?.confirmAction?.({
            title: 'Dừng Auto Base',
            message: 'Dừng lấy mẫu và khôi phục cấu hình Base trước phiên này?',
            confirmText: 'Dừng Auto Base', confirmVariant: 'danger'
        });
        if (!ok) return;
        setIsStoppingAutoBase(true);
        try { await onStopAutoBase(device.serial); }
        catch { setIsStoppingAutoBase(false); }
    }

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
    const autoFixedResult = ['ready_for_review', 'completed'].includes(autoProgress.phase) && autoProgress.fixed_samples_only === true;
    const reportedConversion = autoProgress.conversion_report || device.base_config?.conversion_report || {};
    const locationInput = reportedConversion.input_coord || autoProgress.raw_coord;
    const locationKey = `${device.serial}:${['lat','lon','alt'].map(key => locationInput?.[key] ?? '').join(':')}`;
    const [provinceLookup, setProvinceLookup] = useState(null);
    const reportedProvince = reportedConversion.location?.province_name || reportedConversion.province_name;
    useEffect(() => {
        if (!autoFixedResult || reportedProvince) return;
        const keys = ['lat','lon','alt'];
        if (!keys.every(key => locationInput?.[key] != null && typeof locationInput[key] !== 'boolean'
            && String(locationInput[key]).trim() !== '' && Number.isFinite(Number(locationInput[key])))) return;
        if (typeof window.apiFetch !== 'function') return;
        let active = true;
        const point = Object.fromEntries(keys.map(key => [key, Number(locationInput[key])]));
        setProvinceLookup({key:locationKey, state:'loading'});
        void (async () => {
            try {
                const response = await window.apiFetch('/api/coordinates/auto-base-location', {
                    method:'POST', body:JSON.stringify(point)
                });
                if (!response.ok) throw new Error('Máy chủ chưa trả được tỉnh/thành.');
                const location = await response.json();
                if (!keys.every(key => Number(location.input_coord?.[key]) === point[key]))
                    throw new Error('Phản hồi vị trí không khớp tọa độ của phiên này.');
                if (active) setProvinceLookup({key:locationKey, state:'done', location});
            } catch (error) {
                if (active) setProvinceLookup({key:locationKey, state:'error', error:error.message});
            }
        })();
        return () => { active = false; };
    }, [locationKey, autoFixedResult, reportedProvince]);
    const resolvedLocation = provinceLookup?.key === locationKey ? provinceLookup.location : null;
    // Province identification is independent of the selected CORS/manual projection.
    const conversionReport = resolvedLocation ? {...reportedConversion, location:resolvedLocation,
        province_code:resolvedLocation.province_code, province_name:resolvedLocation.province_name} : reportedConversion;
    const reviewKey = JSON.stringify([device.serial, autoProgress.phase, autoProgress.updated_at,
        autoProgress.averaged_coord, reportedConversion]);
    const [projectionReview, setProjectionReview] = useState(null);
    const currentReview = projectionReview?.key === reviewKey ? projectionReview : null;
    const recalculated = currentReview?.result;
    const reviewCoords = recalculated?.calculated_coord || autoProgress.averaged_coord;
    const reviewReport = recalculated ? {
        ...conversionReport,
        calculated_coord: Object.fromEntries(Object.entries(recalculated.calculated_coord).map(([key, value]) => [key, String(value)])),
        projected_coord: Object.fromEntries(Object.entries(recalculated.projected_coord).map(([key, value]) => [key, String(value)])),
        central_meridian_deg: String(recalculated.central_meridian_deg),
        k0: String(recalculated.k0),
        projection_source: 'manual',
        rtcm1025_used: false,
        zone_width_deg: recalculated.k0 === 0.9999 ? 3 : 6
    } : conversionReport;
    const provinceLabel = conversionReport.location?.province_name || conversionReport.province_name
        || (provinceLookup?.key === locationKey && provinceLookup.state === 'loading' ? 'Đang tra tỉnh/thành từ tọa độ…'
            : provinceLookup?.key === locationKey && provinceLookup.error ? provinceLookup.error
            : resolvedLocation?.state === 'outside_boundaries' ? 'Tọa độ nằm ngoài vùng dữ liệu tỉnh/thành'
            : 'Chưa có kết quả tra tỉnh/thành');
    const supportsAutoBaseReview = device.system_info?.auto_base_review_supported === true;
    const supportsAutoBaseNearest = device.system_info?.auto_base_nearest_supported === true;
    const autoResultText = autoFixedResult
        ? autoProgress.auto_applied === true
            ? `Đã tự áp dụng Fixed LLA sau ${autoProgress.sample_count || 0} mẫu RTK Fixed.`
            : `Đã lấy ${autoProgress.sample_count || 0} mẫu RTK Fixed. Xem tọa độ trước khi áp dụng Fixed LLA.`
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

    // CORS connection for Auto Base (per-station tab memory; never read from .env)
    const [corsHost, setCorsHost] = useState('');
    const [corsSourceMode, setCorsSourceMode] = useState('nearest');
    const [projectionMeridian, setProjectionMeridian] = useState('');
    const [projectionScale, setProjectionScale] = useState('');
    const [corsPort, setCorsPort] = useState('2101');
    const [corsUsername, setCorsUsername] = useState('');
    const [corsPassword, setCorsPassword] = useState('');
    const [corsSources, setCorsSources] = useState([]);
    const [selectedMountpoint, setSelectedMountpoint] = useState('');
    const [isLoadingCorsSources, setIsLoadingCorsSources] = useState(false);
    const [corsLookupError, setCorsLookupError] = useState('');
    const draftOwnerRef = useRef({serial:null, savedMethod:null});
    const skipDraftSaveRef = useRef(false);
    const corsRequestRef = useRef(0);

    // Survey-In Params
    const [svinDuration, setSvinDuration] = useState('300'); // Giây
    const [svinAccuracyM, setSvinAccuracyM] = useState('3.00'); // Đơn vị Mét cho Survey-In (Mặc định 3m)

    const [interferenceDetectionEnabled, setInterferenceDetectionEnabled] = useState(true);
    const [jammingSensitivityHigh, setJammingSensitivityHigh] = useState(true);
    const [outputMode, setOutputMode] = useState(isLteStation ? 'rtcm_only' : 'diagnostics_rtcm');
    const [msmLevel, setMsmLevel] = useState(isLteStation ? 'both' : '4');
    const [constellations, setConstellations] = useState({
        gps: true,
        glo: true,
        gal: true,
        bds: true,
        qzss: true,
        sbas: true
    });
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [isReadingReceiver, setIsReadingReceiver] = useState(false);
    const [pendingReadbackRevision, setPendingReadbackRevision] = useState(null);
    const readbackTimeoutRef = useRef(null);
    const pendingReadbackRevisionRef = useRef(null);
    const isAdmin = String(currentUser?.role || '').toLowerCase() === 'admin';
    const itfmUnsupported = device.gnss_receiver?.itfm_supported === false;
    const receiverReadback = device.system_info?.receiver_base_readback || {};
    const receiverPosition = receiverReadback.bestpos || {};
    const baseStartup = device.system_info?.base_startup || {};
    const receiverLabel = device.detected_chip_type === 'Ublox' ? 'u-blox' : 'UM';
    const supportsReceiverReadback = ['Ublox', 'Unicorecomm'].includes(device.detected_chip_type);
    const receiverOffline = device.status === 'offline'
        || !!window.CorsDashboardShared?.isDeviceStaleOffline?.(device);
    const surveyStatus = device.system_info?.survey_in_status || {};
    const surveyState = receiverOffline ? 'offline' : surveyStatus.state;
    const surveyElapsedSeconds = surveyStatus.source === 'NAV-SVIN'
        ? Number(surveyStatus.elapsed_seconds) + Number(surveyStatus.readback_age_seconds ?? receiverReadback.age_seconds ?? 0)
        : Number(surveyStatus.elapsed_since_ack_seconds);
    const statusReceivedAt = Number(device.timestamp);
    const surveyStartedAt = Number.isFinite(statusReceivedAt) && Number.isFinite(surveyElapsedSeconds)
        && statusReceivedAt > surveyElapsedSeconds
        ? new Date((statusReceivedAt - surveyElapsedSeconds) * 1000).toLocaleString('vi-VN', {
            timeZone: 'Asia/Ho_Chi_Minh', hour12: false
        })
        : '';
    const savedMethod = String(device.base_config?.base_setup_method || '').toUpperCase();
    const expectedReceiverMode = savedMethod === 'SURVEY_IN'
        ? 'survey_in'
        : ['FIXED_LLA', 'AUTO_CORS'].includes(savedMethod) ? 'base' : '';
    const receiverReadbackIsFresh = receiverReadback.state === 'ok'
        && Number(receiverReadback.age_seconds) <= 60
        && !receiverOffline;
    const receiverCoordinates = window.CorsReceiverCoordinates?.fromReceiver(receiverPosition);
    const fixedCoordinateComparison = receiverReadbackIsFresh && receiverReadback.mode_kind === 'base'
        ? window.CorsReceiverCoordinates?.compareFixed(receiverCoordinates, device.base_config, device.detected_chip_type)
        : null;
    const receiverInstantCoordinates = window.CorsReceiverCoordinates?.fromReceiver(receiverPosition, true);
    const canUseReceiverCoordinates = !!receiverCoordinates && receiverReadbackIsFresh
        && !isReadingReceiver;
    const [receiverCopyNotice, setReceiverCopyNotice] = useState('');
    useEffect(() => setReceiverCopyNotice(''), [device.serial, receiverReadback.revision]);
    const receiverModeMismatch = receiverReadbackIsFresh && expectedReceiverMode
        && ['survey_in', 'base', 'rover'].includes(receiverReadback.mode_kind)
        && receiverReadback.mode_kind !== expectedReceiverMode;
    const savedMethodLabel = ({
        FIXED_LLA: 'Fixed LLA', SURVEY_IN: 'Survey-In', AUTO_CORS: 'Auto Base'
    })[savedMethod] || 'Chưa có cấu hình Base đã lưu';

    const handleReadReceiver = async () => {
        if (!onReadBaseStatus || isReadingReceiver) return;
        const startingRevision = Number(receiverReadback.revision || 0);
        setIsReadingReceiver(true);
        pendingReadbackRevisionRef.current = startingRevision;
        setPendingReadbackRevision(startingRevision);
        try {
            await onReadBaseStatus(device.serial);
            if (readbackTimeoutRef.current) window.clearTimeout(readbackTimeoutRef.current);
            readbackTimeoutRef.current = window.setTimeout(() => {
                if (pendingReadbackRevisionRef.current !== startingRevision) return;
                pendingReadbackRevisionRef.current = null;
                setPendingReadbackRevision(null);
                setIsReadingReceiver(false);
            }, 15000);
        } catch (error) {
            pendingReadbackRevisionRef.current = null;
            setPendingReadbackRevision(null);
            setIsReadingReceiver(false);
            window.showError(`Không đọc được trạng thái ${receiverLabel}: ${error.message}`);
        }
    };

    useEffect(() => {
        if (pendingReadbackRevisionRef.current === null
            || Number(receiverReadback.revision || 0) === pendingReadbackRevisionRef.current) return;
        pendingReadbackRevisionRef.current = null;
        if (readbackTimeoutRef.current) window.clearTimeout(readbackTimeoutRef.current);
        setPendingReadbackRevision(null);
        setIsReadingReceiver(false);
    }, [receiverReadback.revision]);

    useEffect(() => () => {
        pendingReadbackRevisionRef.current = null;
        if (readbackTimeoutRef.current) window.clearTimeout(readbackTimeoutRef.current);
    }, []);


    useEffect(() => {
        const switched = draftOwnerRef.current.serial !== device.serial;
        const savedMethod = device.base_config?.base_setup_method;
        const methodChanged = draftOwnerRef.current.savedMethod !== savedMethod;
        if (switched) {
            skipDraftSaveRef.current = true;
            ++corsRequestRef.current;
            const draft = autoBaseDrafts.get(device.serial);
            setCorsHost(draft?.host || '');
            setCorsSourceMode(draft?.sourceMode || device.base_config?.auto_cors_source_mode || 'nearest');
            setCorsPort(draft?.port || '2101');
            setCorsUsername(draft?.username || '');
            setCorsPassword(draft?.password || '');
            setCorsSources(draft?.sources || []);
            setSelectedMountpoint(draft?.mountpoint || '');
            setProjectionMeridian(draft?.meridian || '');
            setProjectionScale(draft?.scale || '');
            setCorsLookupError('');
            setIsLoadingCorsSources(false);
            setMethod(isAutoSetupActive ? 'AUTO_CORS' : draft?.method || savedMethod || 'AUTO_CORS');
        } else if (isAutoSetupActive) setMethod('AUTO_CORS');
        else if (methodChanged && savedMethod) setMethod(savedMethod);
        draftOwnerRef.current = {serial:device.serial, savedMethod};
        // Reload receiver configuration without clearing this station's CORS draft.

        setManualChip(device.detected_chip_type === 'RTCM3_Source' ? 'Unicorecomm' : 'Ublox');
        setLatitude('');
        setLongitude('');
        setAltitude('');
        setCoordinatePasteNotice(null);
        setAltitudeReference('ELLIPSOID');
        setFixedAccuracyM('0.01');
        setSvinDuration('300');
        setSvinAccuracyM('3.00');
        setInterferenceDetectionEnabled(true);
        setJammingSensitivityHigh(true);
        setOutputMode(isLteStation ? 'rtcm_only' : 'diagnostics_rtcm');
        setMsmLevel(isLteStation ? 'both' : '4');
        setConstellations({ gps: true, glo: true, gal: true, bds: true, qzss: true, sbas: true });

        // 2. Load config từ DB (nếu có)
        if (device.base_config) {
            const config = device.base_config;

            // Load Fixed Params
            const coords = config.coords || (
                (config.lat !== undefined && config.lon !== undefined)
                    ? { lat: config.lat, lon: config.lon, alt: config.alt }
                    : null
            );
            if (coords) {
                setLatitude(coords.lat != null ? chipCoordinate(coords.lat, activeChip === 'Ublox' ? 9 : 10) : coords.lat ?? '');
                setLongitude(coords.lon != null ? chipCoordinate(coords.lon, activeChip === 'Ublox' ? 9 : 10) : coords.lon ?? '');
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
                setInterferenceDetectionEnabled(options.interference_detection_enabled !== false);
                setJammingSensitivityHigh(options.jamming_sensitivity_high !== false);
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
    }, [device.serial, device.detected_chip_type, device.base_config?.base_setup_method, device.system_info?.base_startup?.state, isAutoSetupActive]);

    useEffect(() => {
        if (skipDraftSaveRef.current) { skipDraftSaveRef.current = false; return; }
        autoBaseDrafts.set(device.serial, {method, sourceMode:corsSourceMode, host:corsHost, port:corsPort,
            username:corsUsername, password:corsPassword, sources:corsSources,
            mountpoint:selectedMountpoint, meridian:projectionMeridian, scale:projectionScale});
    }, [device.serial, method, corsSourceMode, corsHost, corsPort, corsUsername, corsPassword,
        corsSources, selectedMountpoint, projectionMeridian, projectionScale]);
    useEffect(() => () => { ++corsRequestRef.current; }, [device.serial]);

    const handleDecimalInputChange = (setter, maxDecimals) => (e) => {
        const value = e.target.value.replace(',', '.');
        if (maxDecimals !== undefined && (value.split('.')[1] || '').length > maxDecimals) return;
        // Cho phép nhập số thập phân âm/dương
        if (/^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]*)?$/.test(value)) {
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
        return chipCoordinate(val, activeChip === 'Ublox' ? 9 : 10);
    };

    const formatApprovedAltitude = (value, chip) => chip === 'Ublox'
        ? chipCoordinate(value, 4) : Number(value).toFixed(4);

    const formatAlt10 = (val) => {
        if (val === null || val === undefined || val === '') return '';
        if (activeChip === 'Ublox') return chipCoordinate(val, 4);
        return String(val);
    };

    const handleCoordinatePaste = (event) => {
        if (disabled || isSubmitting) return;
        try {
            const coords = window.CorsCoordinateClipboard?.parse(event.clipboardData?.getData('text/plain') || '');
            if (!coords) return; // A normal numeric paste still belongs to the focused field.
            event.preventDefault();
            setLatitude(chipCoordinate(coords.lat, activeChip === 'Ublox' ? 9 : 10));
            setLongitude(chipCoordinate(coords.lon, activeChip === 'Ublox' ? 9 : 10));
            setAltitude(activeChip === 'Ublox' ? chipCoordinate(coords.alt, 4) : coords.alt);
            setAltitudeReference('ELLIPSOID');
            setCoordinatePasteNotice({ error: false, message: 'Đã điền vĩ độ, kinh độ WGS 84 và độ cao h. Kiểm tra tọa độ rồi bấm áp dụng.' });
        } catch (error) {
            event.preventDefault();
            setCoordinatePasteNotice({ error: true, message: error.message });
        }
    };

    const handleUseReceiverCoordinates = () => {
        if (!canUseReceiverCoordinates || disabled || isSubmitting) return;
        setMethod('FIXED_LLA');
        setLatitude(chipCoordinate(receiverCoordinates.lat, activeChip === 'Ublox' ? 9 : 10));
        setLongitude(chipCoordinate(receiverCoordinates.lon, activeChip === 'Ublox' ? 9 : 10));
        setAltitude(receiverCoordinates.alt);
        setAltitudeReference('ELLIPSOID');
        setCoordinatePasteNotice({ error: false,
            message: `Đã điền tọa độ Base từ ${receiverLabel}. Kiểm tra tọa độ và sai số trước khi bấm áp dụng.` });
        window.requestAnimationFrame(() => document.getElementById(`base-latitude-${device.serial}`)?.focus());
    };

    const handleCopyReceiverCoordinates = async () => {
        if (!canUseReceiverCoordinates) return;
        setReceiverCopyNotice('');
        try {
            await navigator.clipboard.writeText(window.CorsCoordinateClipboard.encode(receiverCoordinates));
            setReceiverCopyNotice('Đã sao chép WGS 84 và độ cao h; có thể dán vào công cụ kiểm tra hoặc Fixed LLA.');
        } catch (_) {
            setReceiverCopyNotice('Không sao chép được. Hãy chọn và sao chép các giá trị bên trên.');
        }
    };

    const handleCorsFieldChange = (setter) => (e) => {
        ++corsRequestRef.current;
        setIsLoadingCorsSources(false);
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

        const request = ++corsRequestRef.current;
        setIsLoadingCorsSources(true);
        setCorsLookupError('');
        try {
            const response = await window.apiFetch(window.ApiRoutes.devices.corsMountpoints(device.serial), {
                method: 'POST',
                body: JSON.stringify({ host, port, username, password: corsPassword })
            });
            const data = await response.json().catch(() => ({}));
            if (request !== corsRequestRef.current) return;
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
            if (request !== corsRequestRef.current) return;
            setCorsSources([]);
            setSelectedMountpoint('');
            setCorsLookupError(error.message);
        } finally {
            if (request === corsRequestRef.current) setIsLoadingCorsSources(false);
        }
    };

    const handleSubmit = async (e, approvedCoords = null) => {
        e.preventDefault();
        if (disabled || (approvedCoords && (isAutoSetupActive || !autoFixedResult || currentReview?.pending))) return;
        if (approvedCoords && !['lat', 'lon', 'alt'].every(key =>
            approvedCoords[key] != null && String(approvedCoords[key]).trim() !== '' && Number.isFinite(Number(approvedCoords[key])))) {
            window.showError('Kết quả Auto Base thiếu LLH hợp lệ. Cần lấy lại tọa độ.');
            return;
        }
        const submittedMethod = approvedCoords ? 'FIXED_LLA' : method;
        setIsSubmitting(true);

        try {
            const finalChipType = needsManualSelection
                ? manualChip
                : device.detected_chip_type;

            let params = { base_setup_method: submittedMethod };

            if (submittedMethod === 'FIXED_LLA') {
                const latStr = String(approvedCoords ? approvedCoords.lat : latitude).trim();
                const lonStr = String(approvedCoords ? approvedCoords.lon : longitude).trim();
                const latNum = Number.parseFloat(latStr);
                const lonNum = Number.parseFloat(lonStr);
                if (!latStr || !lonStr || Number.isNaN(latNum) || Number.isNaN(lonNum)) {
                    window.showError('Vui lòng nhập vĩ độ và kinh độ hợp lệ.');
                    setIsSubmitting(false);
                    return;
                }
                params.coords = {
                    lat: chipCoordinate(latStr, finalChipType === 'Ublox' ? 9 : 10),
                    lon: chipCoordinate(lonStr, finalChipType === 'Ublox' ? 9 : 10),
                    alt: approvedCoords ? formatApprovedAltitude(approvedCoords.alt, finalChipType)
                        : finalChipType === 'Ublox' ? chipCoordinate(String(altitude).trim() || '0', 4) : String(altitude).trim() || '0'
                };
                if (finalChipType === 'Ublox') {
                    setLatitude(params.coords.lat);
                    setLongitude(params.coords.lon);
                    setAltitude(params.coords.alt);
                }
                params.altitude_reference = approvedCoords || finalChipType === 'Ublox' ? 'ELLIPSOID' : altitudeReference;
                const candidate = autoProgress.averaged_coord;
                if (approvedCoords || (autoFixedResult && candidate && ['lat', 'lon', 'alt'].every(key =>
                    Math.abs(Number(params.coords[key]) - Number(candidate[key])) <= (key === 'alt' ? 0.00011 : 1.01e-9)))) {
                    params.conversion_report = { ...(approvedCoords ? reviewReport : conversionReport), output_coord: { ...params.coords } };
                    params.coordinate_source = 'AUTO_CORS';
                }
                // Preserve decimal meters through the API and UBX encoding.
                params.accuracy = String(fixedAccuracyM).trim().replace(',', '.') || '0.01';
            } else if (submittedMethod === 'SURVEY_IN') {
                params.survey_in_duration = Number.parseInt(svinDuration);
                // Survey-In: Input là m -> Gửi trực tiếp m cho backend (Backend sẽ tự xử lý x10000 nếu cần cho Ublox)
                params.survey_in_accuracy = Number.parseFloat(svinAccuracyM);
            } else if (submittedMethod === 'AUTO_CORS') {
                if (corsSourceMode === 'nearest') {
                    if (!supportsAutoBaseNearest) {
                        window.showError('Cần cập nhật agent ESP32 để tự chọn CORS gần nhất.');
                        setIsSubmitting(false);
                        return;
                    }
                } else {
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
                if ((projectionMeridian.trim() && !projectionScale) || (!projectionMeridian.trim() && projectionScale)) {
                    window.showError('Nhập cả kinh tuyến trục và hệ số k₀, hoặc để trống cả hai.');
                    setIsSubmitting(false);
                    return;
                }
            }
            if (isLteStation && !['gps', 'glo', 'gal', 'bds', 'qzss'].some(key => constellations[key])) {
                window.showError('Chọn ít nhất một hệ vệ tinh đầu ra.');
                return;
            }
            const existingGnss = (device.base_config && typeof device.base_config.gnss_options === 'object')
                ? device.base_config.gnss_options
                : null;

            if (isAdmin) {
                params.gnss_options = {
                    ...(activeChip === 'Ublox' ? { interference_detection_enabled: interferenceDetectionEnabled, jamming_sensitivity_high: jammingSensitivityHigh } : {}),
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
                    ...(!isLteStation ? { ports: ['USB'] } : {})
                };
            } else if (existingGnss) {
                params.gnss_options = { ...existingGnss };
            } else {
                params.gnss_options = {
                    ...(activeChip === 'Ublox' ? { interference_detection_enabled: true, jamming_sensitivity_high: jammingSensitivityHigh } : {}),
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
                    ...(!isLteStation ? { ports: ['USB'] } : {})
                };
            }

            const payload = {
                sensor_type: finalChipType,
                mode: 'BASE',
                params: params
            };

            if (submittedMethod === 'AUTO_CORS') {
                payload.auto_cors = corsSourceMode === 'nearest' ? { source_mode: 'nearest' } : {
                    source_mode: 'manual',
                    ip: corsHost.trim(),
                    port: Number(corsPort),
                    user: corsUsername.trim(),
                    password: corsPassword,
                    mountpoint: selectedMountpoint
                };
                if (projectionMeridian.trim() || projectionScale) {
                    const l0 = Number(projectionMeridian);
                    const k0 = Number(projectionScale);
                    if (!projectionMeridian.trim() || !projectionScale || !Number.isFinite(l0) || l0 < 100 || l0 > 112) {
                        window.showError('Nhập đủ kinh tuyến trục và hệ số k₀ hợp lệ.');
                        return;
                    }
                    payload.auto_cors.l0_deg = l0;
                    payload.auto_cors.k0 = k0;
                }
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
                    <label htmlFor="base-manual-chip" className="form-label fw-bold">Xác nhận Loại Chip</label>
                    <select id="base-manual-chip"
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
            {(isAutoSetupActive || (method === 'AUTO_CORS' && ['failed', 'stopped'].includes(autoProgress.phase))) && (
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
                                        className="btn btn-outline-danger btn-sm fw-bold"
                                        style={{ minHeight:44 }} disabled={disabled || isStoppingAutoBase} onClick={stopAutoBase}
                                    >
                                        {isStoppingAutoBase ? <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" /> : <i className="bi bi-stop-circle-fill me-1" aria-hidden="true" />}
                                        {isStoppingAutoBase ? 'Đang dừng…' : 'Dừng Auto Base'}
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
                                    <span className="form-label small fw-bold mb-0 text-info">
                                        <i className="bi bi-terminal me-1"></i>
                                        Bản tin NMEA GGA trực tiếp từ Chip Rover:
                                    </span>
                                    {rawGgaSentence && (
                                        <button
                                            type="button"
                                            className="btn btn-outline-info btn-sm py-0 px-2"
                                            style={{ fontSize: '0.75rem' }}
                                            onClick={() => {
                                                navigator.clipboard.writeText(rawGgaSentence).then(
                                                    () => window.showToast?.('Đã sao chép bản tin GGA!'),
                                                    () => window.showError?.("Không thể sao chép vào bộ nhớ tạm.")
                                                );
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

            <div className="card bg-dark border-secondary mb-3">
                <div className="card-body p-3">
                    {baseStartup.state && (
                        <div className={`alert ${baseStartup.state === 'error' ? 'alert-danger' : 'alert-info'} small py-2 mb-3`}
                             role="status" aria-atomic="true" aria-busy={!receiverOffline && ['pending', 'awaiting_receiver'].includes(baseStartup.state)}>
                            {!receiverOffline && ['pending', 'awaiting_receiver'].includes(baseStartup.state)
                                && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>}
                            {receiverOffline ? 'Thiết bị offline; trạng thái bên dưới là lần nhận gần nhất.' : ({
                                pending: 'Thiết bị đang tự thiết lập Base sau khi bật nguồn…',
                                awaiting_receiver: 'Đang chờ nhận diện GNSS để tự thiết lập Base…',
                                awaiting_auto_base: 'Đang chờ backend gửi cấu hình AutoNearest. ESP32 sẽ gửi GGA trực tiếp cho caster.',
                                auto_base_running: 'ESP32 đang kết nối caster và thiết lập Base.',
                                survey_started: 'Đang Survey-In 120 giây theo lựa chọn đã lưu. Cấu hình từ UI dùng thời lượng đã chọn.',
                                survey_resumed: 'Đã tiếp tục phiên Survey-In đang có trên receiver, không bắt đầu lại.',
                                fixed_restored: 'Đã khôi phục Fixed LLA đã lưu; không chạy Survey-In.',
                                configured: 'Đã áp dụng cấu hình Base từ người dùng.',
                                preserved: 'Đã giữ cấu hình Auto Base đã lưu.',
                                locked: 'Thiết bị đang khóa; chưa tự thay đổi cấu hình Base.',
                                error: `Chưa thiết lập được Base: ${baseStartup.error || 'Chưa có xác nhận từ receiver.'}`
                            })[baseStartup.state]}
                        </div>
                    )}
                    <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                        <div>
                            <div className="fw-bold">Trạng thái Base thực tế</div>
                            <div className="small text-muted">Web lưu: {savedMethodLabel}</div>
                            <div className="small text-muted">Thiết bị tự cập nhật mỗi 30 giây.</div>
                        </div>
                        <button
                            type="button"
                            className="btn btn-outline-info"
                            style={{ minHeight: '44px' }}
                            disabled={disabled || isReadingReceiver || !supportsReceiverReadback}
                            onClick={handleReadReceiver}
                            aria-label="Đọc lại mode và tọa độ hiện tại từ receiver"
                        >
                            {isReadingReceiver ? (
                                <><span className="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Đang chờ receiver...</>
                            ) : (
                                <><i className="bi bi-arrow-repeat me-2" aria-hidden="true"></i>Cập nhật ngay</>
                            )}
                        </button>
                    </div>
                    {!supportsReceiverReadback ? (
                        <div className="small text-muted" role="status">Đang chờ nhận diện receiver GNSS.</div>
                    ) : receiverReadback.state === 'ok' ? (
                        <>
                            <div className="small" role="status" aria-live="polite">
                                {receiverLabel} báo <code>{receiverReadback.mode_raw}</code>
                                {receiverModeMismatch && <span className="badge bg-danger ms-2">LỆCH MODE</span>}
                                {receiverReadbackIsFresh && !receiverModeMismatch && expectedReceiverMode && (
                                    <span className="badge bg-success ms-2">MODE KHỚP</span>
                                )}
                            </div>
                            {receiverCoordinates ? (
                                <div className="border rounded p-3 mt-3">
                                    <div className="fw-bold small mb-2">{savedMethod === 'SURVEY_IN' ? 'Tọa độ Base sau Survey-In' : 'Tọa độ Base đang giữ'}</div>
                                    <dl className="row g-2 small mb-2">
                                        <div className="col-sm-6"><dt className="text-muted fw-normal">Vĩ độ WGS 84</dt><dd className="font-monospace text-break mb-0">{formatCoord10(receiverCoordinates.lat)}</dd></div>
                                        <div className="col-sm-6"><dt className="text-muted fw-normal">Kinh độ WGS 84</dt><dd className="font-monospace text-break mb-0">{formatCoord10(receiverCoordinates.lon)}</dd></div>
                                        <div className="col-sm-6"><dt className="text-muted fw-normal">Độ cao h ellipsoid (m)</dt><dd className="font-monospace mb-0">{receiverCoordinates.alt}</dd></div>
                                        <div className="col-sm-6"><dt className="text-muted fw-normal">Độ cao chip báo (m)</dt><dd className="font-monospace mb-0">{receiverCoordinates.heightMsl}</dd></div>
                                    </dl>
                                    {receiverCoordinates.source === 'UBX'
                                        ? <div className="small text-muted mb-2">Độ cao ellipsoid WGS 84 từ tọa độ receiver đã xác nhận.</div>
                                        : <div className="small text-muted mb-2">Undulation chip báo: {receiverCoordinates.undulation} m. h = độ cao chip báo + undulation; undulation = 0 thì giữ nguyên độ cao. Đây không phải geoid địa phương đã hiệu chỉnh.</div>}
                                    {savedMethod === 'SURVEY_IN' && (
                                        <div className="small text-muted mb-2">Receiver đã giữ tọa độ Base sau khảo sát.</div>
                                    )}
                                    <div className="d-flex flex-wrap gap-2">
                                        <button type="button" className="btn btn-outline-info btn-sm" style={{ minHeight: '44px' }}
                                            disabled={!canUseReceiverCoordinates} onClick={handleCopyReceiverCoordinates}>Sao chép tọa độ</button>
                                        <button type="button" className="btn btn-outline-primary btn-sm" style={{ minHeight: '44px' }}
                                            disabled={!canUseReceiverCoordinates || disabled || isSubmitting}
                                            onClick={handleUseReceiverCoordinates}>Điền vào Fixed LLA</button>
                                    </div>
                                    {receiverCopyNotice && <div className="small text-info mt-2" role="status" aria-live="polite">{receiverCopyNotice}</div>}
                                </div>
                            ) : receiverInstantCoordinates ? (
                                <div className="small text-warning mt-1">
                                    Tọa độ ước tính đang hội tụ, chưa chốt Survey-In: <code>{receiverInstantCoordinates.lat}, {receiverInstantCoordinates.lon}</code>
                                    {' · '}h ellipsoid {receiverInstantCoordinates.alt} m · {receiverPosition.position_type}
                                </div>
                            ) : receiverPosition.state === 'ok' && (
                                <div className="small text-muted mt-1">
                                    {receiverPosition.message || 'BESTPOS'} hiện tại: <code>{String(receiverPosition.latitude)}, {String(receiverPosition.longitude)}</code>
                                    {' · '}độ cao chip báo {String(receiverPosition.height_msl)} m
                                    {receiverPosition.position_type ? ` · ${receiverPosition.position_type}` : ''}
                                </div>
                            )}
                            {receiverModeMismatch && (
                                <div className="alert alert-danger small py-2 mt-2 mb-0" role="alert">
                                    Web đang lưu {savedMethodLabel}, nhưng {receiverLabel} báo {receiverReadback.mode_raw}. Hai mode không khớp.
                                </div>
                            )}
                            {receiverReadbackIsFresh && !receiverModeMismatch && receiverReadback.mode_kind === 'base' && savedMethod === 'FIXED_LLA' && (
                                <div className={`small mt-2 ${fixedCoordinateComparison?.matches ? 'text-success-emphasis' : fixedCoordinateComparison ? 'text-danger-emphasis' : 'text-muted'}`}
                                    role="status" aria-live="polite">
                                    {fixedCoordinateComparison?.matches
                                        ? 'Tọa độ máy khớp Fixed LLA đã lưu.'
                                        : fixedCoordinateComparison
                                            ? `Tọa độ máy lệch Fixed LLA đã lưu: ngang ${fixedCoordinateComparison.horizontalMeters.toFixed(4)} m; độ cao ${fixedCoordinateComparison.heightMeters.toFixed(4)} m.`
                                            : 'Mode BASE khớp; chưa đủ dữ liệu để so sánh tọa độ đã lưu.'}
                                </div>
                            )}
                            {receiverReadbackIsFresh ? (
                                <div className="small text-muted mt-1">Đọc cách đây {Number(receiverReadback.age_seconds) || 0} giây.</div>
                            ) : (
                                <div className="small text-warning mt-1">Kết quả đã cũ hoặc thiết bị offline; đang chờ bản cập nhật mới.</div>
                            )}
                        </>
                    ) : receiverReadback.state === 'error' ? (
                        <div className="small text-danger" role="status" aria-live="polite">
                            Không đọc được mode từ {receiverLabel}: {receiverReadback.error || 'Không có phản hồi.'}
                        </div>
                    ) : (
                        <div className="small text-muted" role="status">Đang chờ thiết bị tự cập nhật mode và tọa độ.</div>
                    )}
                    {['Ublox', 'Unicorecomm'].includes(device.detected_chip_type) && (
                        <GnssConfigVerification device={device} disabled={disabled || isSubmitting || isReadingReceiver} />
                    )}
                    {pendingReadbackRevision !== null && (
                        <div className="small text-info mt-1" role="status" aria-live="polite">Đang chờ firmware trả kết quả đọc mới...</div>
                    )}
                    {supportsReceiverReadback
                        && (savedMethod === 'SURVEY_IN' || (surveyStatus.state && surveyStatus.state !== 'not_monitoring')) && (
                        <div className="border-top border-secondary pt-2 mt-3">
                            <div className="fw-bold small">Theo dõi Survey-In</div>
                            <div className={`small mt-1 ${surveyState === 'base_ready' ? 'text-success' : 'text-warning'}`}
                                 role="status" aria-atomic="true">
                                {({
                                    base_ready: surveyStatus.source === 'NAV-SVIN' ? 'u-blox đã xác nhận khảo sát hoàn tất (NAV-SVIN valid).' : 'UM đã giữ tọa độ Base (BESTNAV: FIXEDPOS).',
                                    waiting_base: `${receiverLabel} đang khảo sát, chưa xác nhận tọa độ Base.`,
                                    awaiting_gga: 'Đang chờ GGA mới từ UM để xác định trạng thái.',
                                    awaiting_readback: 'Đang chờ trạng thái Base mới từ receiver.',
                                    offline: 'Thiết bị offline; cần kết nối lại để đọc trạng thái mới.'
                                })[surveyState] || 'Đang chờ thiết bị tự xác nhận trạng thái Survey-In.'}
                            </div>
                            {surveyStatus.state && surveyStatus.state !== 'not_monitoring' && (
                                <>
                                    <div className="small text-muted mt-1">
                                        {surveyStartedAt ? `Bắt đầu ước tính (ICT): ${surveyStartedAt}. ` : ''}
                                        {surveyStatus.source === 'NAV-SVIN'
                                            ? `Đã khảo sát khoảng ${Math.floor(surveyElapsedSeconds) || 0} giây · ${Number(surveyStatus.observations) || 0} mẫu · Sai số ước tính ${Number(surveyStatus.mean_accuracy_m || 0).toFixed(3)} m`
                                            : surveyStatus.elapsed_since_ack_seconds != null
                                            ? `Đã qua ${Math.floor(surveyElapsedSeconds) || 0} giây từ ACK bắt đầu của UM.`
                                            : 'Không có mốc bắt đầu khảo sát của phiên này.'}
                                        {Number(surveyStatus.target_seconds) > 0
                                            ? ` Thời gian khảo sát yêu cầu: ${Number(surveyStatus.target_seconds)} giây.` : ''}
                                    </div>
                                    {['base_ready', 'waiting_base'].includes(surveyState) && (
                                        <div className="small text-muted mt-1">
                                            {['BESTNAV', 'NAV-SVIN'].includes(surveyStatus.source)
                                                ? `Đọc từ receiver cách đây ${Number(surveyStatus.readback_age_seconds) || 0} giây`
                                                : `${Number(surveyStatus.satellites) || 0} vệ tinh · HDOP ${Number(surveyStatus.hdop || 0).toFixed(2)} · GGA cách đây ${Number(surveyStatus.gga_age_seconds) || 0} giây`}
                                        </div>
                                    )}
                                    <div className="small text-muted mt-1">Chỉ báo hoàn tất khi receiver xác nhận tọa độ Base.</div>
                                </>
                            )}
                        </div>
                    )}
                </div>
            </div>

                        {Boolean(autoFixedResult && autoProgress.averaged_coord) && (
                            <>
                                <div className="col-12" role="status" aria-live="polite">
                                    <div className="border rounded p-3">
                                        <h3 className="h6">Kết quả Auto Base</h3>
                                        <p className="small mb-2">{autoProgress.auto_applied === true
                                            ? 'Fixed LLA đã được tự áp dụng sau khi receiver xác nhận tọa độ.'
                                            : autoProgress.phase === 'ready_for_review'
                                                ? 'Chưa áp dụng tọa độ này vào máy.'
                                                : 'Kết quả đang chờ duyệt trên UI.'}</p>
                                        <div className="small text-break mb-2">Base cấp cải chính: {conversionReport.source?.station_name || conversionReport.source?.host || 'Chưa có dữ liệu'}
                                            {conversionReport.source?.mountpoint ? ` / ${conversionReport.source.mountpoint}` : ''}</div>
                                        {conversionReport.source?.distance_m != null && <div className="small text-muted mb-2">Cách thiết bị khoảng {(Number(conversionReport.source.distance_m) / 1000).toFixed(1)} km.</div>}
                                        <div className="small mb-2" role="status">Tỉnh/thành: {provinceLabel}
                                            {conversionReport.location?.state === 'projection_selection_required' && ' · Có nhiều vùng kinh tuyến trục cũ'}</div>
                                        <div className="table-responsive">
                                            <table className="table table-sm small mb-2" style={{minWidth: '640px'}}>
                                                <caption className="visually-hidden">Đối chiếu LLH gốc và kết quả Auto Base</caption>
                                                <thead><tr><th scope="col">Tọa độ</th><th scope="col">LLH gốc</th><th scope="col">LLH sau tính</th><th scope="col">Gửi máy</th></tr></thead>
                                                <tbody>{[['lat', 'Vĩ độ (°)'], ['lon', 'Kinh độ (°)'], ['alt', 'h ellipsoid (m)']].map(([key, label]) => (
                                                    <tr key={key}><th scope="row">{label}</th>
                                                        <td className="font-monospace">{formatReportCoordinate(conversionReport.input_coord?.[key], key)}</td>
                                                        <td className="font-monospace text-break">{formatReportCoordinate((reviewReport.calculated_coord || autoProgress.calculated_coord)?.[key], key)}</td>
                                                        <td className="font-monospace">{currentReview?.pending ? 'Chờ tính lại' : reviewCoords?.[key] == null ? 'Chưa có dữ liệu' : key === 'alt' ? formatApprovedAltitude(reviewCoords[key], activeChip) : formatCoord10(reviewCoords[key])}</td></tr>
                                                ))}</tbody>
                                            </table>
                                        </div>
                                        <Vn2000Result key={reviewKey} report={conversionReport}
                                            onReviewChange={value => setProjectionReview(value && { ...value, key: reviewKey })} />
                                        <div className="small">{conversionReport.transform_applied
                                            ? `LLH địa phương qua VN-2000 ${projectionLabel(reviewReport)} · L₀ ${displayMeridian(reviewReport.central_meridian_deg, reviewReport)}° · k₀ ${displayProjectionScale(reviewReport)}.`
                                            : 'Giữ LLH GNSS gốc; không chuyển hệ tọa độ.'}</div>
                                        {conversionReport.received_messages && <div className="small text-muted mt-1">RTCM nhận trong phiên: {Object.keys(conversionReport.received_messages).join(', ') || 'Chưa có bản tin'}</div>}
                                        {conversionReport.transform_applied && <details className="small mt-2"><summary>Tham số chuyển đổi</summary>
                                            <p className="mb-1">Nguồn 7 tham số: {conversionReport.parameter_source === 'rtcm1021' ? 'RTCM 1021' : 'Chưa xác nhận'};
                                                {' '}hiệu chỉnh: {conversionReport.residual_source === 'rtcm1023' ? 'RTCM 1023' : 'Không có'};
                                                {' '}phép chiếu: {conversionReport.projection_source === 'rtcm1025' ? 'RTCM 1025' : conversionReport.projection_source === 'manual' ? 'Nhập thủ công' : 'Danh mục máy chủ'}.</p>
                                            <dl className="mb-0">{Object.entries(conversionReport.parameters || {}).map(([key, value]) => <div key={key} className="d-flex gap-2"><dt>{key}</dt><dd className="font-monospace mb-0 text-break">{String(value)}</dd></div>)}</dl>
                                        </details>}
                                    </div>
                                </div>
                                {autoProgress.auto_applied !== true && <div className="col-12">
                                    <button type="button" className="btn btn-primary w-100" style={{minHeight:44}} disabled={disabled || isSubmitting || isAutoSetupActive || Boolean(currentReview?.pending)}
                                        onClick={event => handleSubmit(event, reviewCoords)}>
                                        {isSubmitting ? 'Đang gửi cấu hình Fixed LLA…' : 'Duyệt và áp dụng LLH vào Base (Fixed LLA)'}
                                    </button>
                                </div>}
                            </>
                        )}

            {method !== 'AUTO_CORS' && !autoFixedResult && <Vn2000Result report={conversionReport} />}
            <div className="mb-3">
                {device.system_info?.survey_in_result?.bestpos && savedMethod !== 'SURVEY_IN' && (() => {
                    const saved = window.CorsReceiverCoordinates?.fromReceiver(device.system_info.survey_in_result.bestpos);
                    if (!saved) return null;
                    return <details className="border rounded p-3 mb-3">
                        <summary className="small fw-bold py-2">Kết quả Survey-In đã lưu</summary>
                        <div className="small text-muted mt-1">Kết quả trước khi chuyển cấu hình, dùng để đối chiếu với Fixed LLA hiện tại.</div>
                        <div className="small font-monospace text-break mt-2">WGS 84: {saved.lat}, {saved.lon} · h {saved.alt} m</div>
                    </details>;
                })()}
                <label htmlFor={`base-method-${device.serial}`} className="form-label fw-bold text-primary">Phương pháp xác định vị trí</label>
                <select
                    id={`base-method-${device.serial}`}
                    className="form-select border-primary"
                    value={method}
                    onChange={(e) => setMethod(e.target.value)}
                    disabled={disabled}
                >
                    <option value="AUTO_CORS">Tự lấy tọa độ từ CORS gần nhất</option>
                    <option value="SURVEY_IN">Tự động khảo sát (Survey-In - Độ chính xác thấp)</option>
                    <option value="FIXED_LLA">Nhập tọa độ thủ công (Fixed Mode - Độ chính xác cao)</option>
                </select>
            </div>

            <fieldset disabled={disabled}>
                {method === 'AUTO_CORS' ? (
                    <div className="row g-3 auto-cors-config">
                        {!supportsAutoBaseReview && <div className="col-12"><div className="alert alert-warning small mb-0" role="status">
                            Cần cập nhật agent ESP32 để hỗ trợ đầy đủ luồng Auto Base.
                        </div></div>}
                        {!supportsAutoBaseNearest && <div className="col-12"><div className="alert alert-warning small mb-0" role="status">
                            Agent này chưa hỗ trợ tự chuyển Rover và chọn trạm CORS gần nhất.
                        </div></div>}
                        <div className="col-12">
                            <label htmlFor={`cors-source-mode-${device.serial}`} className="form-label fw-bold">Nguồn cải chính</label>
                            <select id={`cors-source-mode-${device.serial}`} className="form-select"
                                value={corsSourceMode} disabled={disabled || isAutoSetupActive}
                                onChange={event => setCorsSourceMode(event.target.value)}>
                                <option value="nearest">Tự chọn trạm CORS gần nhất trong danh sách của tôi</option>
                                <option value="manual">Tự nhập IP, tài khoản và mountpoint</option>
                            </select>
                        </div>
                        <div className="col-12">
                            <div className="auto-cors-config__panel p-3 rounded border border-info-subtle bg-dark-subtle">
                                {corsSourceMode === 'manual' ? <>
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
                                                ? `${corsSources.length} mountpoint đã sẵn sàng. Chọn mountpoint, kiểm tra đầu ra rồi bấm Lấy tọa độ Auto Base bên dưới.`
                                                : 'Bấm nút Bước 1 để tải danh sách mountpoint.'}
                                        </div>
                                    </div>
                                    <div className="col-12 mt-2">
                                        <div className="form-text text-muted mt-2">
                                            <i className="bi bi-info-circle me-1"></i>
                                            Quy trình: Chuyển chip sang Rover &rarr; Gửi NMEA GGA lên Caster &rarr; Nhận RTCM &rarr; Đạt RTK Fix &rarr; Lấy tọa độ trung bình &rarr; Xem kết quả &rarr; Xác nhận áp dụng Fixed LLA.
                                        </div>
                                    </div>
                                </div>
                                </> : <div className="alert alert-info small mb-0" role="status">
                                    <div className="fw-bold mb-1">ESP32 sẽ vào Rover trước</div>
                                    <div>Thiết bị gửi vị trí GNSS lên backend. Backend chọn trạm CORS gần nhất trong danh sách base bạn có quyền dùng, rồi gửi kết nối cải chính xuống ESP32. Khi đủ RTK_FIXED và mẫu hợp lệ, thiết bị tự áp dụng tọa độ Base.</div>
                                </div>}
                            </div>
                        </div>
                        <div className="col-12">
                            <details className="mb-2">
                                <summary>Phép chiếu khi nguồn không gửi RTCM 1025</summary>
                                <div className="row g-3 mt-1">
                                    <div className="col-sm-6">
                                        <label className="form-label" htmlFor={`auto-l0-${device.serial}`}>Kinh tuyến trục L₀ (độ)</label>
                                        <input id={`auto-l0-${device.serial}`} type="number" step="any" min="100" max="112" className="form-control" value={projectionMeridian} onChange={e => setProjectionMeridian(e.target.value)} disabled={disabled || isAutoSetupStatus} />
                                    </div>
                                    <div className="col-sm-6">
                                        <label className="form-label" htmlFor={`auto-k0-${device.serial}`}>Hệ số k₀</label>
                                        <select id={`auto-k0-${device.serial}`} className="form-select" value={projectionScale} onChange={e => setProjectionScale(e.target.value)} disabled={disabled || isAutoSetupStatus}>
                                            <option value="">Theo nguồn / tỉnh</option>
                                            <option value="0.9999">0.9999 — múi 3°</option>
                                            <option value="0.9996">0.9996 — múi 6°</option>
                                        </select>
                                    </div>
                                    <div className="col-12 small text-muted">Có RTCM 1025 sẽ ưu tiên tham số của nguồn. Để trống cả hai để tra theo tỉnh khi thiếu bản tin này.</div>
                                </div>
                            </details>
                        </div>
                        <div className="col-12 small text-info" role="status">
                            LLH trung bình → VN-2000 NEh → LLH địa phương. Dùng tham số RTCM 1021/1023 và phép chiếu RTCM 1025 hoặc theo tỉnh.
                        </div>
                        <div className="col-12">
                                <div className={`alert ${autoFixedResult ? 'alert-success' : 'alert-info'} small mb-2`}>
                                <i className="bi bi-robot me-2"></i>
                                {corsSourceMode === 'nearest' ? (
                                    <span>ESP32 vào Rover, gửi vị trí lên backend, nhận cải chính từ trạm gần nhất, lấy mẫu RTK_FIXED rồi tự chuyển sang Fixed LLA.</span>
                                ) : autoFixedResult ? (
                                    <span>Đã có kết quả. Kiểm tra tọa độ rồi xác nhận áp dụng Fixed LLA.</span>
                                ) : (
                                    <span>Lấy mẫu RTK Fixed từ Base bạn nhập. Sau khi có kết quả, bạn kiểm tra và xác nhận áp dụng Fixed LLA.</span>
                                )}
                            </div>
                        </div>

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
                                value={latitude} onChange={handleDecimalInputChange(setLatitude, activeChip === 'Ublox' ? 9 : 10)}
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
                                value={longitude} onChange={handleDecimalInputChange(setLongitude, activeChip === 'Ublox' ? 9 : 10)}
                                onPaste={handleCoordinatePaste} aria-describedby={`base-coordinate-paste-help-${device.serial}${activeChip === 'Ublox' ? ` base-ublox-precision-${device.serial}` : activeChip === 'Unicorecomm' ? ` base-unicore-precision-${device.serial}` : ''}`}
                                disabled={disabled || isSubmitting}
                                required placeholder="Ví dụ: 105.854167"
                            />
                        </div>
                        {activeChip === 'Ublox' && <div id={`base-ublox-precision-${device.serial}`} className="col-12 form-text mt-1">
                            Ublox: vĩ độ và kinh độ tối đa 9 chữ số sau dấu . hoặc , — không nhận chữ số thứ 10.
                        </div>}
                        {activeChip === 'Unicorecomm' && <div id={`base-unicore-precision-${device.serial}`} className="col-12 form-text mt-1">
                            Unicorecomm: vĩ độ và kinh độ gửi xuống chip có 10 chữ số thập phân; cắt phần dư, không làm tròn. Phép tính giữ đủ độ chính xác.
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
                            <label htmlFor="base-survey-duration" className="form-label">Thời gian khảo sát (giây)</label>
                            <input id="base-survey-duration"
                                type="number" className="form-control"
                                value={svinDuration} onChange={(e) => setSvinDuration(e.target.value)}
                                required
                            />
                            <div className="form-text">Tối thiểu: 60s. Khuyên dùng: &gt; 300s.</div>
                        </div>
                        <div className="col-md-6">
                            {activeChip === 'Unicorecomm' ? (
                                <div className="form-text mt-4">UM tự lấy trung bình theo thời gian. Kết quả cần được kiểm tra trước khi dùng làm tọa độ cố định.</div>
                            ) : <>
                            <label htmlFor="base-survey-accuracy" className="form-label fw-bold">Giới hạn sai số (m)</label>
                            <input id="base-survey-accuracy"
                                type="number" step="0.01" className="form-control"
                                value={svinAccuracyM} onChange={(e) => setSvinAccuracyM(e.target.value)}
                                required placeholder="Ví dụ: 3.0"
                            />
                            <div className="form-text text-danger">Đơn vị: MÉT (Ví dụ nhập 3.0 là 3 mét)</div>
                            </>}
                        </div>
                    </div>
                )}

                {(
                    <div className="gnss-output-policy mt-4">
                        <div className="gnss-output-policy__header">
                            <div className="gnss-output-policy__title">
                                <i className="bi bi-broadcast-pin me-2"></i>GNSS Output Policy
                            </div>
                            <span className="gnss-output-policy__admin">{isAdmin ? 'Admin' : 'Cài đặt bởi Admin'}</span>
                        </div>
                        {isLteStation && (
                            <div className="alert alert-info small mb-3">
                                <i className="bi bi-reception-4 me-2"></i>
                                <strong>Trạm LTE:</strong> chọn bản tin NMEA/RTCM và mức MSM, rồi bấm Áp dụng cấu hình BASE để lưu xuống chip.
                            </div>
                        )}
                        <div className="gnss-output-policy__grid">
                            <div className="gnss-output-policy__field">
                                <label htmlFor={`gnss-output-mode-${device.serial}`} className="gnss-output-policy__label">Output mode</label>
                                <select
                                    id={`gnss-output-mode-${device.serial}`}
                                    className="form-select form-select-sm gnss-output-policy__select"
                                    value={outputMode}
                                    onChange={(e) => {
                                        const nextMode = e.target.value;
                                        setOutputMode(nextMode);
                                    }}
                                    disabled={!isAdmin || isSubmitting}
                                >
                                    <option value="diagnostics_rtcm">NMEA + RTCM</option>
                                    <option value="rtcm_only">RTCM only</option>
                                </select>
                            </div>
                            <div className="gnss-output-policy__field">
                                <label htmlFor={`gnss-msm-level-${device.serial}`} className="gnss-output-policy__label">RTCM MSM level</label>
                                <select
                                    id={`gnss-msm-level-${device.serial}`}
                                    className="form-select form-select-sm gnss-output-policy__select"
                                    value={msmLevel}
                                    onChange={(e) => setMsmLevel(e.target.value)}
                                    disabled={!isAdmin || isSubmitting}
                                >
                                    <option value="4">MSM4</option>
                                    <option value="7">MSM7</option>
                                    <option value="both">MSM4 + MSM7</option>
                                </select>
                            </div>
                            {activeChip === 'Ublox' && <div className="gnss-output-policy__field" style={{gridColumn:'1 / -1'}}>
                                {itfmUnsupported ? <>
                                    <label htmlFor={`gnss-sensitivity-${device.serial}`} className="gnss-output-policy__label">Độ nhạy phát hiện nhiễu</label>
                                    <select id={`gnss-sensitivity-${device.serial}`} className="form-select form-select-sm" value={jammingSensitivityHigh ? 'high' : 'normal'}
                                        disabled={!isAdmin || isSubmitting} aria-describedby={`gnss-sensitivity-help-${device.serial}`}
                                        onChange={event=>setJammingSensitivityHigh(event.target.value === 'high')}>
                                        <option value="high">Cao — khuyến nghị, mặc định u-blox</option>
                                        <option value="normal">Thường — ít cảnh báo nhầm hơn</option>
                                    </select>
                                    <p id={`gnss-sensitivity-help-${device.serial}`} className="form-text mb-0">Mức cao nhạy hơn nhưng có thể báo nhầm nhiều hơn. Mức thường vẫn phát hiện nhiễu. Lưu xuống chip khi bấm Áp dụng cấu hình BASE.</p>
                                </> : <>
                                <label className="form-check form-switch d-flex align-items-center gap-2 mb-1" style={{minHeight:44}}>
                                    <input id={`gnss-interference-${device.serial}`} className="form-check-input mt-0" type="checkbox" role="switch"
                                        checked={interferenceDetectionEnabled} disabled={!isAdmin || isSubmitting}
                                        aria-describedby={`gnss-interference-help-${device.serial}`}
                                        onChange={event=>setInterferenceDetectionEnabled(event.target.checked)} />
                                    <span>Phát hiện nhiễu GNSS</span>
                                </label>
                                <p className="form-text mb-0" id={`gnss-interference-help-${device.serial}`}>Lưu xuống chip khi bấm Áp dụng cấu hình BASE.</p>
                                </>}
                            </div>}
                            <div className="gnss-output-policy__summary" style={isLteStation ? {gridColumn: '1 / -1'} : undefined}>
                                <span className="gnss-output-policy__label gnss-output-policy__label--accent">
                                    <i className="bi bi-info-circle me-1"></i>Thông tin Hệ vệ tinh & Bản tin RTCM
                                </span>
                                <div className="gnss-output-policy__info" style={isLteStation ? {height: 'auto'} : undefined}>
                                    <div>
                                        <div className="gnss-output-policy__section-title">
                                            {isLteStation ? 'Bản tin hệ vệ tinh đầu ra:' : 'Hệ vệ tinh hỗ trợ (Mặc định bật toàn bộ):'}
                                        </div>
                                        <div className="gnss-output-policy__chips">
                                            {isLteStation ? [['gps', 'GPS'], ['glo', 'GLONASS'], ['gal', 'Galileo'], ['bds', 'BeiDou'], ['qzss', 'QZSS']].map(([key, label]) => (
                                                <label key={key} className="gnss-output-policy__chip"
                                                    style={{minHeight: '44px', cursor: isAdmin ? 'pointer' : 'default'}}>
                                                    <input type="checkbox" className="form-check-input mt-0 me-1"
                                                        aria-label={`Bản tin ${label}`} checked={!!constellations[key]}
                                                        disabled={!isAdmin || isSubmitting}
                                                        onChange={event => setConstellations(previous => ({...previous, [key]: event.target.checked}))} />
                                                    {label}
                                                </label>
                                            )) : <>
                                                <span className="gnss-output-policy__chip">GPS</span>
                                                <span className="gnss-output-policy__chip">GLONASS</span>
                                                <span className="gnss-output-policy__chip">Galileo</span>
                                                <span className="gnss-output-policy__chip">BeiDou</span>
                                                <span className="gnss-output-policy__chip">QZSS</span>
                                                <span className="gnss-output-policy__chip gnss-output-policy__chip--muted">SBAS</span>
                                            </>}
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
                                            ) : activeChip === 'Unicorecomm' && isLteStation ? (
                                                <span>
                                                    <strong>1006</strong> (Tọa độ Base), <strong>1033</strong> (antenna/receiver).{' '}
                                                    {[
                                                        ['gps', 'GPS', '1074', '1077', '1019'],
                                                        ['glo', 'GLONASS', '1084', '1087', '1020'],
                                                        ['gal', 'Galileo', '1094', '1097', '1045'],
                                                        ['bds', 'BeiDou', '1124', '1127', '1042'],
                                                        ['qzss', 'QZSS', '1114', '1117', '1044']
                                                    ].filter(([key]) => constellations[key]).map(([, label, msm4, msm7, ephemeris]) =>
                                                        `${msmLevel === 'both' ? `${msm4}+${msm7}` : msmLevel === '7' ? msm7 : msm4}, ${ephemeris} (${label})`
                                                    ).join('; ')}.
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

                {!disabled && isAutoSetupActive && onStopAutoBase && (
                    <button type="button" className="btn btn-outline-danger mt-4 w-100" style={{minHeight:44}}
                        disabled={isStoppingAutoBase} onClick={stopAutoBase}>
                        {isStoppingAutoBase ? <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" /> : <i className="bi bi-stop-circle-fill me-2" aria-hidden="true" />}
                        {isStoppingAutoBase ? 'Đang dừng…' : 'Dừng Auto Base'}
                    </button>
                )}
                {!disabled && (
                    <button
                        type="submit"
                        className="btn btn-primary mt-4 w-100 base-config-submit"
                        disabled={isSubmitting || isAutoSetupActive || isLoadingCorsSources || (method === 'AUTO_CORS' && (!supportsAutoBaseReview || (corsSourceMode === 'nearest' ? !supportsAutoBaseNearest : !selectedMountpoint)))}
                    >
                        {isSubmitting ? (
                            <>
                                <span className="spinner-border spinner-border-sm me-2"></span>
                                {method === 'AUTO_CORS' ? 'Đang gửi yêu cầu lấy tọa độ…' : 'Đang gửi...'}
                            </>
                        ) : (
                            <>
                                <i className="bi bi-lightning-charge-fill me-2"></i>
                                {method === 'AUTO_CORS'
                                    ? (isAutoSetupActive ? 'Đang lấy tọa độ…' : corsSourceMode === 'nearest' ? 'Chuyển Rover & tìm trạm gần nhất' : autoFixedResult ? 'Lấy lại tọa độ Auto Base' : 'Lấy tọa độ Auto Base')
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
        const casterError = device.ntrip_status?.[key]?.error;
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
                {enabled && !pending && !offline && !telemetry.connected && !sleeping && casterError && (
                    <span className="text-danger text-break w-100" role="status">Caster: {casterError}</span>
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
                ntrip_version1: Number.parseInt(formState.ntrip_version1, 10),
                ntrip_version2: Number.parseInt(formState.ntrip_version2, 10),
                port1: formState.port1 ? Number.parseInt(formState.port1, 10) : null,
                port2: formState.port2 ? Number.parseInt(formState.port2, 10) : null,
                command_port: formState.command_port ? Number.parseInt(formState.command_port, 10) : null,
                rtcmntripversion1: Number.parseInt(formState.rtcmntripversion1, 10) === 2 ? 2 : 1,
                rtcmport1: formState.rtcmport1 ? Number.parseInt(formState.rtcmport1, 10) : null,
                reconnectioninterval: Number.parseInt(formState.reconnectioninterval, 10)
            };
            for (const sid of serverIds) {
                payload[`ntrip_version${sid}`] = Number.parseInt(formState[`ntrip_version${sid}`] || 1, 10);
                payload[`port${sid}`] = formState[`port${sid}`] ? Number.parseInt(formState[`port${sid}`], 10) : null;
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
                                <span className="form-label">Server 1: Chế độ stream</span>
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
                                <span className="form-label">Server 2: Chế độ stream</span>
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
