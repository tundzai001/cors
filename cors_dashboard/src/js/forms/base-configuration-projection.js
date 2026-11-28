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
const projectionLabel = report => {
    if (Math.abs(Number(report?.k0) - 0.9999) <= 1e-9) return 'múi 3°';
    if (isSixDegreeReport(report)) return 'múi 6°';
    return 'phép chiếu tùy chọn';
};
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
const formatReportCoordinate = (value, key) => {
    if (value == null || !Number.isFinite(Number(value))) return 'Chưa có dữ liệu';
    return Number(value).toFixed(key === 'alt' ? 4 : 10);
};
const buildProjectionRequest = (report, decoded, meridian, scale, onReviewChange) => {
    const offsets = {
        false_easting: Number(decoded?.false_easting ?? 500000),
        false_northing: Number(decoded?.false_northing ?? 0),
    };
    const payload = onReviewChange ? {
        input_coord: report.input_coord,
        parameters: report.parameters,
        apply_residual: report.residual_source === 'rtcm1023',
        projection: { l0_deg: Number(meridian), k0: Number(scale), ...offsets },
    } : {
        ...report.projected_coord,
        source: { l0_deg: Number(report.central_meridian_deg), k0: Number(report.k0), ...offsets },
        target_l0_deg: Number(meridian),
        target_k0: Number(scale),
    };
    return {
        path: onReviewChange ? '/api/coordinates/recalculate-auto-base' : '/api/coordinates/reproject-display',
        body: JSON.stringify(payload),
    };
};
async function recalculateVn2000Projection({
    canRecalculate, calculating, meridian, requestRef, setCalculating, setError,
    onReviewChange, report, decoded, scale, setPreview, sourceKey,
}) {
    if (!canRecalculate || calculating || !meridian.trim() || !Number.isFinite(Number(meridian))
        || Number(meridian) < 100 || Number(meridian) > 112) return;
    const request = ++requestRef.current;
    setCalculating(true);
    setError('');
    onReviewChange?.({ pending: true });
    try {
        const requestOptions = buildProjectionRequest(report, decoded, meridian, scale, onReviewChange);
        const response = await window.apiFetch(requestOptions.path, { method: 'POST', body: requestOptions.body });
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
function Vn2000CoordinateSummary({ hasNeh, neh }) {
    if (!hasNeh) {
        return <p className="small text-muted mb-2">Phiên cũ chưa lưu NEh. Cần lấy lại kết quả Auto Base để hiển thị đủ N, E, h.</p>;
    }
    return <dl className="row g-2 small mb-2">
        {[['north', 'N — Northing (m)'], ['east', 'E — Easting (m)'], ['h', 'h — Độ cao ellipsoid (m)']].map(([key, label]) => (
            <div className="col-sm-4" key={key}><dt className="text-muted fw-normal">{label}</dt>
                <dd className="font-monospace text-break mb-0">{Number(neh[key]).toFixed(4)}</dd></div>
        ))}
    </dl>;
}

function Vn2000ProjectionControls({
    onReviewChange, scale, setScale, calculating, meridian, setMeridian,
    canRecalculate, reproject, calculateButtonText, preview, sourceKey,
    report, setPreview, setError, calculateHelpText, error,
}) {
    function changeProjection(setter, value) {
        setter(value);
        if (onReviewChange) setPreview(null);
        onReviewChange?.({ pending: true });
    }
    function resetProjection() {
        setPreview(null);
        setError('');
        setMeridian(displayMeridian(report.central_meridian_deg, report));
        setScale(isSixDegreeReport(report) ? '0.9996' : '0.9999');
        onReviewChange?.(null);
    }
    return <div className="border-top mt-3 pt-3">
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
                {calculateButtonText}
            </button>
            {(onReviewChange || preview?.sourceKey === sourceKey) && <button type="button" className="btn btn-outline-secondary" style={{minHeight:44}} disabled={calculating}
                onClick={resetProjection}>Dùng kết quả gốc</button>}
        </div>
        <p className="small text-muted mt-2 mb-0">{calculateHelpText}</p>
        {error && <p className="small text-danger mt-2 mb-0" role="alert">{error}</p>}
    </div>;
}

function Vn2000DecodeDetails({ report, decoded, hasDecoded, processorLabel }) {
    if (!hasDecoded && report.projection_source !== 'rtcm1025') return null;
    return <details className="small mt-2">
        <summary className="py-2" style={{minHeight:44}}>Đối chiếu giải mã RTCM 1025</summary>
        {hasDecoded ? <>
            <p className="mb-2">Bộ giải mã: {processorLabel} · {decoded.frame_bytes ?? '—'} byte.</p>
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
    </details>;
}

function getVn2000ActionLabels(onReviewChange, canRecalculate, calculating) {
    let button = onReviewChange ? 'Tính lại NEh và LLH' : 'Đổi múi N/E';
    if (calculating) button = 'Đang tính…';
    let help = onReviewChange ? 'Dùng lại mẫu RTK Fixed của phiên này. Kiểm tra LLH mới trước khi áp dụng vào Base.' : 'Đổi múi để xem N/E của tọa độ đã lưu.';
    if (!canRecalculate) help = 'Phiên này thiếu tham số RTCM để tính lại.';
    return {
        button,
        help,
    };
}

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
    const processorLabel = {
        esp32: 'ESP32',
        python_agent: 'Agent Python trên Pi',
    }[report.processor] || 'Chưa ghi nhận';
    const actionLabels = getVn2000ActionLabels(onReviewChange, canRecalculate, calculating);
    const projectionSource = {rtcm1025:'CORS · RTCM 1025', manual:'Nhập L₀/k₀ thủ công', server_catalog:'Danh mục tỉnh trên máy chủ', not_used:'Không chuyển đổi'}[report.projection_source] || 'Báo cáo chưa lưu nguồn phép chiếu';
    const reproject = () => recalculateVn2000Projection({
        canRecalculate, calculating, meridian, requestRef, setCalculating, setError,
        onReviewChange, report, decoded, scale, setPreview, sourceKey,
    });
    return <section className="border rounded p-3 mb-3" aria-label="Kết quả VN-2000 NEh" aria-busy={calculating}>
        <h3 className="h6 mb-2">VN-2000 — NEh · {projectionLabel(displayed)}</h3>
        <Vn2000CoordinateSummary hasNeh={hasNeh} neh={neh} />
        <div className="small text-muted" role="status" aria-live="polite">Kinh tuyến trục: {displayMeridian(displayed.central_meridian_deg, displayed)}° · k₀: {displayProjectionScale(displayed)}.{preview?.sourceKey === sourceKey && (onReviewChange ? ' Đã tính lại NEh và LLH.' : ' Đã đổi múi N/E.')}</div>
        {hasNeh && <Vn2000ProjectionControls
            onReviewChange={onReviewChange} scale={scale} setScale={setScale} calculating={calculating}
            meridian={meridian} setMeridian={setMeridian} canRecalculate={canRecalculate} reproject={reproject}
            calculateButtonText={actionLabels.button} preview={preview} sourceKey={sourceKey} report={report}
            setPreview={setPreview} setError={setError}
            calculateHelpText={actionLabels.help} error={error}
        />}
        <p className="small mt-2 mb-1">Nguồn phép chiếu gốc: <strong>{projectionSource}</strong>.</p>
        <p className="small text-muted mb-0">Nhãn múi 3°/6° suy theo k₀ (0.9999/0.9996), không phải tỉnh được nhận diện. Ưu tiên RTCM 1025; nếu thiếu thì dùng nhập tay, sau đó danh mục tỉnh.</p>
        <Vn2000DecodeDetails report={report} decoded={decoded} hasDecoded={hasDecoded} processorLabel={processorLabel} />
    </section>;
};

export { STATUS_TEXT_MAP, Vn2000Result, projectionLabel, displayMeridian, displayProjectionScale, formatReportCoordinate };
