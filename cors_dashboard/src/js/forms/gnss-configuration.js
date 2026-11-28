const { useState, useEffect, useRef } = React;

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
    const rate = value => {
        if (value === 0) return 'Tắt';
        if (value == null) return 'Chưa đọc được';
        return `Mỗi ${value} chu kỳ`;
    };
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
                            <td>{formatPortEnabledState(port.enabled, ready)}</td>
                            <td>{formatPortBaud(name, port?.baud)}</td>
                            {[0, 1, 2].map(index => <td key={index} className={ready && port.values?.[index] === 1 ? 'text-success-emphasis' : 'text-body-secondary'}>
                                {formatPortValue(port.values?.[index], ready, show)}
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
        {ublox && observed?.state !== 'ok' && <div className="small text-warning">Chưa đọc được danh sách bản tin trên {selected}.</div>}
        {ublox && observed?.state === 'ok' && <>
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
        </>}
        {!ublox && details.logs_state !== 'ok' && <div className="small text-warning">Chưa đọc được danh sách log từ UM.</div>}
        {!ublox && details.logs_state === 'ok' && <div className="table-responsive border rounded">
            <table className="table table-sm align-middle mb-0 small">
                <caption className="visually-hidden">Danh sách log UM trên {selected}</caption>
                <thead><tr><th scope="col">Bản tin</th><th scope="col">Chip trả về</th><th scope="col">Yêu cầu</th></tr></thead>
                <tbody>{logNames.map(name => {
                    const actual = logs.find(log => log.port === selected && log.name === name);
                    const expected = expectedLogs.includes(name);
                    if (enabledOnly && actual?.period == null && !expected) return null;
                    return <tr key={name}><th scope="row" className="fw-normal">{name}</th>
                        <td className={compare && ((!expected && actual?.period > 0) || (expected && actual?.period !== 1)) ? 'text-danger-emphasis' : ''}>{formatLogPeriod(actual)}</td>
                        <td>{formatExpectedLogPeriod(expected, compare)}</td></tr>;
                })}</tbody>
            </table>
            {logNames.length === 0 && <div className="small text-muted p-2">Chip không liệt kê log trên cổng này.</div>}
        </div>}
    </div>;
};

const formatPortEnabledState = (value, ready) => {
    if (!ready || value == null) return 'Chưa đọc được';
    if (value === 1) return 'Bật';
    if (value === 0) return 'Tắt';
    return String(value);
};
const formatPortValue = (value, ready, show) => {
    if (!ready) return 'Chưa đọc được';
    if (value === 1) return 'Bật';
    if (value === 0) return 'Tắt';
    return show(value);
};
const formatPortBaud = (name, baud) => {
    if (!name.startsWith('UART')) return '—';
    return baud == null ? 'Chưa đọc được' : `${baud} bps`;
};
const formatLogPeriod = actual => {
    if (!actual) return 'Không có trong danh sách';
    if (actual.period == null) return 'Một lần';
    return `${actual.period} giây`;
};
const formatExpectedLogPeriod = (expected, compare) => {
    if (expected) return '1 giây';
    if (compare) return 'Không bật';
    return 'Không đối chiếu';
};
const verificationResultText = (offline, expired, state) => {
    if (offline) return 'Thiết bị ngoại tuyến. Kết quả là lần xác minh gần nhất.';
    if (expired) return 'Kết quả là lần đọc trước. Xác minh lại để cập nhật.';
    if (state === 'match') return 'Cấu hình đang chạy khớp.';
    if (state === 'mismatch') return 'Cấu hình chưa khớp.';
    return 'Chưa đọc đủ cấu hình để xác minh.';
};
const verificationResultClass = (offline, expired, state) =>
    offline || expired || state !== 'match' ? 'text-warning' : 'text-success-emphasis';
const verificationAdvice = (supported, fixed) => {
    if (!supported) return 'Cập nhật agent để xác minh cấu hình.';
    if (!fixed) return 'Lưu cấu hình Fixed LLA trước khi xác minh.';
    return 'Đối chiếu cấu hình đang chạy với cấu hình đã lưu. Không thay đổi cấu hình chip.';
};
const verificationGroups = chipType => {
    const isUblox = chipType === 'Ublox';
    return [
        ['fixed', isUblox ? 'Fixed LLA và độ chính xác' : 'Fixed LLA'],
        ...(isUblox ? [['protocols', 'Giao thức đầu ra']] : []),
        ['diagnostics', isUblox ? 'Bản tin NMEA / UBX' : 'Bản tin NMEA'],
        ['rtcm', 'Bản tin RTCM'],
    ];
};
const verificationGroupStatus = group => {
    const unread = !group || Number(group.unread) > 0 || !(Number(group.checked) > 0);
    const mismatch = Number(group?.mismatched) > 0;
    let className = 'text-success-emphasis';
    let label = 'Khớp';
    if (unread) {
        className = 'text-warning';
        label = 'Chưa đọc được';
    } else if (mismatch) {
        className = 'text-danger-emphasis';
        label = `Sai (${group.mismatched}/${group.checked})`;
    }
    return {
        className,
        label,
    };
};
export const GnssConfigVerification = ({ device, disabled }) => {
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
        <div className="small text-muted mt-2">{verificationAdvice(supported, fixed)}</div>
        <div role="status" aria-live="polite" aria-atomic="true">
            {pending && <div className="small text-info mt-2">Đang chờ chip trả cấu hình…</div>}
            {notice && <div className="small text-warning mt-2">{notice}</div>}
            {result && <>
                <div className={`small fw-bold mt-2 ${verificationResultClass(offline, expired, result.state)}`}>
                    {verificationResultText(offline, expired, result.state)}
                </div>
                {result.details?.ports?.some(port => port.state !== 'ok') && <div className="small text-warning mt-2">Một số cổng chưa đọc được; xem trạng thái trong bảng.</div>}
                {result.details ? <GnssReadbackDetails details={result.details} />
                    : <div className="small text-warning mt-2">Cập nhật agent để xem giá trị và từng cổng chip trả về.</div>}
                <details className="mt-3"><summary>Đối chiếu các nhóm cấu hình</summary>
                <dl className="row g-2 small mt-2 mb-0">
                    {verificationGroups(device.detected_chip_type).map(([key, label]) => {
                        const group = result.groups?.[key];
                        const status = verificationGroupStatus(group);
                        return <React.Fragment key={key}>
                            <dt className="col-7 fw-normal">{label}</dt>
                            <dd className={`col-5 text-end mb-0 ${status.className}`}>
                                {status.label}
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

const GNSS_CONSTELLATIONS = [
    ['gps', 'GPS', '1074', '1077', '1019'],
    ['glo', 'GLONASS', '1084', '1087', '1020'],
    ['gal', 'Galileo', '1094', '1097', '1045'],
    ['bds', 'BeiDou', '1124', '1127', '1042'],
    ['qzss', 'QZSS', '1114', '1117', '1044'],
];

function getMsmMessage(msmLevel, msm4, msm7) {
    if (msmLevel === 'both') return `${msm4}+${msm7}`;
    return msmLevel === '7' ? msm7 : msm4;
}

function GnssConstellationOptions({ isLteStation, isAdmin, isSubmitting, constellations, setConstellations }) {
    if (!isLteStation) {
        return <>
            <span className="gnss-output-policy__chip">GPS</span>
            <span className="gnss-output-policy__chip">GLONASS</span>
            <span className="gnss-output-policy__chip">Galileo</span>
            <span className="gnss-output-policy__chip">BeiDou</span>
            <span className="gnss-output-policy__chip">QZSS</span>
            <span className="gnss-output-policy__chip gnss-output-policy__chip--muted">SBAS</span>
        </>;
    }
    return GNSS_CONSTELLATIONS.map(([key, label]) => (
        <label key={key} className="gnss-output-policy__chip"
            style={{minHeight: '44px', cursor: isAdmin ? 'pointer' : 'default'}}>
            <input type="checkbox" className="form-check-input mt-0 me-1"
                aria-label={`Bản tin ${label}`} checked={!!constellations[key]}
                disabled={!isAdmin || isSubmitting}
                onChange={event => setConstellations(previous => ({...previous, [key]: event.target.checked}))} />
            {label}
        </label>
    ));
}

function GnssInterferenceControls({
    activeChip, device, itfmUnsupported, jammingSensitivityHigh, setJammingSensitivityHigh,
    interferenceDetectionEnabled, setInterferenceDetectionEnabled, isAdmin, isSubmitting,
}) {
    if (activeChip !== 'Ublox') return null;
    return <div className="gnss-output-policy__field" style={{gridColumn:'1 / -1'}}>
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
    </div>;
}

function GnssRtcmMessages({ activeChip, isLteStation, msmLevel, constellations }) {
    if (activeChip === 'Ublox') {
        return <span>
            <strong>1005</strong> (Tọa độ Base), <strong>{getMsmMessage(msmLevel, '1074', '1077')}</strong> (GPS), <strong>{getMsmMessage(msmLevel, '1084', '1087')}</strong> (GLONASS), <strong>{getMsmMessage(msmLevel, '1094', '1097')}</strong> (Galileo), <strong>{getMsmMessage(msmLevel, '1124', '1127')}</strong> (BeiDou), <strong>1230</strong> (GLONASS Bias).
        </span>;
    }
    if (activeChip === 'Unicorecomm' && isLteStation) {
        const selectedMessages = GNSS_CONSTELLATIONS.filter(([key]) => constellations[key])
            .map(([, label, msm4, msm7, ephemeris]) => `${getMsmMessage(msmLevel, msm4, msm7)}, ${ephemeris} (${label})`)
            .join('; ');
        return <span><strong>1006</strong> (Tọa độ Base), <strong>1033</strong> (antenna/receiver). {selectedMessages}</span>;
    }
    if (activeChip === 'Unicorecomm') {
        let messages = '1074/1084/1094/1124';
        if (msmLevel === 'both') messages = '1074/1084/1094/1124 + 1077/1087/1097/1127';
        else if (msmLevel === '7') messages = '1077/1087/1097/1127';
        return <span>
            <strong>1006</strong> (Tọa độ Base), <strong>1033</strong> (mô tả antenna/receiver), <strong>{messages}</strong> ({msmLevel === 'both' ? 'MSM4 + MSM7' : `MSM${msmLevel}`}), <strong>1019/1020/1042/1045</strong> (Lịch vệ tinh GPS/GLO/BDS/GAL).
        </span>;
    }
    return <span>Bản tin tọa độ trạm (<strong>1005/1006</strong>), Tên trạm (<strong>1033</strong>) và MSM4 (<strong>1074, 1084, 1094, 1124</strong>) cho các hệ vệ tinh GPS, GLONASS, Galileo, BeiDou.</span>;
}

function GnssOutputPolicySummary({ isLteStation, activeChip, msmLevel, constellations, isAdmin, isSubmitting, setConstellations }) {
    return <div className="gnss-output-policy__summary" style={isLteStation ? {gridColumn: '1 / -1'} : undefined}>
        <span className="gnss-output-policy__label gnss-output-policy__label--accent">
            <i className="bi bi-info-circle me-1"></i>Thông tin Hệ vệ tinh & Bản tin RTCM
        </span>
        <div className="gnss-output-policy__info" style={isLteStation ? {height: 'auto'} : undefined}>
            <div>
                <div className="gnss-output-policy__section-title">
                    {isLteStation ? 'Bản tin hệ vệ tinh đầu ra:' : 'Hệ vệ tinh hỗ trợ (Mặc định bật toàn bộ):'}
                </div>
                <div className="gnss-output-policy__chips">
                    <GnssConstellationOptions isLteStation={isLteStation} isAdmin={isAdmin} isSubmitting={isSubmitting}
                        constellations={constellations} setConstellations={setConstellations} />
                </div>
            </div>
            <div className="gnss-output-policy__divider"></div>
            <div className="gnss-output-policy__rtcm">
                <div className="gnss-output-policy__section-title">Bản tin RTCM đầu ra ({activeChip}):</div>
                <div className="gnss-output-policy__copy">
                    <GnssRtcmMessages activeChip={activeChip} isLteStation={isLteStation} msmLevel={msmLevel} constellations={constellations} />
                </div>
            </div>
        </div>
    </div>;
}

export function GnssOutputPolicyPanel(props) {
    const { device, isAdmin, isSubmitting, isLteStation, outputMode, setOutputMode, msmLevel, setMsmLevel } = props;
    return <div className="gnss-output-policy mt-4">
        <div className="gnss-output-policy__header">
            <div className="gnss-output-policy__title"><i className="bi bi-broadcast-pin me-2"></i>GNSS Output Policy</div>
            <span className="gnss-output-policy__admin">{isAdmin ? 'Admin' : 'Cài đặt bởi Admin'}</span>
        </div>
        {isLteStation && <div className="alert alert-info small mb-3">
            <i className="bi bi-reception-4 me-2"></i>
            <strong>Trạm LTE:</strong> chọn bản tin NMEA/RTCM và mức MSM, rồi bấm Áp dụng cấu hình BASE để lưu xuống chip.
        </div>}
        <div className="gnss-output-policy__grid">
            <div className="gnss-output-policy__field">
                <label htmlFor={`gnss-output-mode-${device.serial}`} className="gnss-output-policy__label">Output mode</label>
                <select id={`gnss-output-mode-${device.serial}`} className="form-select form-select-sm gnss-output-policy__select"
                    value={outputMode} onChange={event => setOutputMode(event.target.value)} disabled={!isAdmin || isSubmitting}>
                    <option value="diagnostics_rtcm">NMEA + RTCM</option>
                    <option value="rtcm_only">RTCM only</option>
                </select>
            </div>
            <div className="gnss-output-policy__field">
                <label htmlFor={`gnss-msm-level-${device.serial}`} className="gnss-output-policy__label">RTCM MSM level</label>
                <select id={`gnss-msm-level-${device.serial}`} className="form-select form-select-sm gnss-output-policy__select"
                    value={msmLevel} onChange={event => setMsmLevel(event.target.value)} disabled={!isAdmin || isSubmitting}>
                    <option value="4">MSM4</option>
                    <option value="7">MSM7</option>
                    <option value="both">MSM4 + MSM7</option>
                </select>
            </div>
            <GnssInterferenceControls activeChip={props.activeChip} device={device}
                itfmUnsupported={props.itfmUnsupported} jammingSensitivityHigh={props.jammingSensitivityHigh}
                setJammingSensitivityHigh={props.setJammingSensitivityHigh}
                interferenceDetectionEnabled={props.interferenceDetectionEnabled}
                setInterferenceDetectionEnabled={props.setInterferenceDetectionEnabled}
                isAdmin={isAdmin} isSubmitting={isSubmitting} />
            <GnssOutputPolicySummary isLteStation={isLteStation} activeChip={props.activeChip} msmLevel={msmLevel}
                constellations={props.constellations} isAdmin={isAdmin} isSubmitting={isSubmitting}
                setConstellations={props.setConstellations} />
        </div>
    </div>;
}
