import { Vn2000Result, projectionLabel, displayMeridian, displayProjectionScale, formatReportCoordinate } from './base-configuration-projection.js';
import { getAutoBaseReviewText } from './base-configuration-state.js';

function formatReviewedCoordinate(key, reviewCoords, currentReview, activeChip, formatCoord10, formatApprovedAltitude) {
    if (currentReview?.pending) return 'Chờ tính lại';
    const value = reviewCoords?.[key];
    if (value == null) return 'Chưa có dữ liệu';
    if (key === 'alt') return formatApprovedAltitude(value, activeChip);
    return formatCoord10(value);
}

function AutoBaseCoordinateComparisonTable({
    conversionReport, reviewReport, autoProgress, reviewCoords, currentReview, activeChip,
    formatCoord10, formatApprovedAltitude,
}) {
    const coordinates = [['lat', 'Vĩ độ (°)'], ['lon', 'Kinh độ (°)'], ['alt', 'h ellipsoid (m)']];
    return <div className="table-responsive">
        <table className="table table-sm small mb-2" style={{minWidth: '640px'}}>
            <caption className="visually-hidden">Đối chiếu LLH gốc và kết quả Auto Base</caption>
            <thead><tr><th scope="col">Tọa độ</th><th scope="col">LLH gốc</th><th scope="col">LLH sau tính</th><th scope="col">Gửi máy</th></tr></thead>
            <tbody>{coordinates.map(([key, label]) => (
                <tr key={key}><th scope="row">{label}</th>
                    <td className="font-monospace">{formatReportCoordinate(conversionReport.input_coord?.[key], key)}</td>
                    <td className="font-monospace text-break">{formatReportCoordinate((reviewReport.calculated_coord || autoProgress.calculated_coord)?.[key], key)}</td>
                    <td className="font-monospace">{formatReviewedCoordinate(key, reviewCoords, currentReview, activeChip, formatCoord10, formatApprovedAltitude)}</td>
                </tr>
            ))}</tbody>
        </table>
    </div>;
}

function AutoBaseTransformDetails({ conversionReport, reviewReport }) {
    const transformSummary = conversionReport.transform_applied
        ? `LLH địa phương qua VN-2000 ${projectionLabel(reviewReport)} · L₀ ${displayMeridian(reviewReport.central_meridian_deg, reviewReport)}° · k₀ ${displayProjectionScale(reviewReport)}.`
        : 'Giữ LLH GNSS gốc; không chuyển hệ tọa độ.';
    const parameterSource = conversionReport.parameter_source === 'rtcm1021' ? 'RTCM 1021' : 'Chưa xác nhận';
    const residualSource = conversionReport.residual_source === 'rtcm1023' ? 'RTCM 1023' : 'Không có';
    let projectionSource = 'Danh mục máy chủ';
    if (conversionReport.projection_source === 'rtcm1025') projectionSource = 'RTCM 1025';
    else if (conversionReport.projection_source === 'manual') projectionSource = 'Nhập thủ công';
    return <>
        <div className="small">{transformSummary}</div>
        {conversionReport.received_messages && <div className="small text-muted mt-1">RTCM nhận trong phiên: {Object.keys(conversionReport.received_messages).join(', ') || 'Chưa có bản tin'}</div>}
        {conversionReport.transform_applied && <details className="small mt-2"><summary>Tham số chuyển đổi</summary>
            <p className="mb-1">Nguồn 7 tham số: {parameterSource};{' '}hiệu chỉnh: {residualSource};{' '}phép chiếu: {projectionSource}.</p>
            <dl className="mb-0">{Object.entries(conversionReport.parameters || {}).map(([key, value]) => (
                <div key={key} className="d-flex gap-2"><dt>{key}</dt><dd className="font-monospace mb-0 text-break">{String(value)}</dd></div>
            ))}</dl>
        </details>}
    </>;
}

function AutoBaseReviewPanel(props) {
    const {
        autoFixedResult, autoProgress, conversionReport, provinceLabel, reviewReport, reviewKey,
        reviewCoords, currentReview, activeChip, formatCoord10, formatApprovedAltitude,
        setProjectionReview, disabled, isSubmitting, isAutoSetupActive, handleSubmit,
    } = props;
    if (!autoFixedResult || !autoProgress.averaged_coord) return null;
    return <>
        <div className="col-12" role="status" aria-live="polite">
            <div className="border rounded p-3">
                <h3 className="h6">Kết quả Auto Base</h3>
                <p className="small mb-2">{getAutoBaseReviewText(autoProgress)}</p>
                <div className="small text-break mb-2">Base cấp cải chính: {conversionReport.source?.station_name || conversionReport.source?.host || 'Chưa có dữ liệu'}
                    {conversionReport.source?.mountpoint ? ` / ${conversionReport.source.mountpoint}` : ''}</div>
                {conversionReport.source?.distance_m != null && <div className="small text-muted mb-2">Cách thiết bị khoảng {(Number(conversionReport.source.distance_m) / 1000).toFixed(1)} km.</div>}
                <div className="small mb-2" role="status">Tỉnh/thành: {provinceLabel}
                    {conversionReport.location?.state === 'projection_selection_required' && ' · Có nhiều vùng kinh tuyến trục cũ'}</div>
                <AutoBaseCoordinateComparisonTable conversionReport={conversionReport} reviewReport={reviewReport}
                    autoProgress={autoProgress} reviewCoords={reviewCoords} currentReview={currentReview}
                    activeChip={activeChip} formatCoord10={formatCoord10} formatApprovedAltitude={formatApprovedAltitude} />
                <Vn2000Result key={reviewKey} report={conversionReport}
                    onReviewChange={value => setProjectionReview(value && { ...value, key: reviewKey })} />
                <AutoBaseTransformDetails conversionReport={conversionReport} reviewReport={reviewReport} />
            </div>
        </div>
        {autoProgress.auto_applied !== true && <div className="col-12">
            <button type="button" className="btn btn-primary w-100" style={{minHeight:44}}
                disabled={disabled || isSubmitting || isAutoSetupActive || Boolean(currentReview?.pending)}
                onClick={event => handleSubmit(event, reviewCoords)}>
                {isSubmitting ? 'Đang gửi cấu hình Fixed LLA…' : 'Duyệt và áp dụng LLH vào Base (Fixed LLA)'}
            </button>
        </div>}
    </>;
}

function FixedLlaCoordinateFields(props) {
    const {
        device, coordinatePasteNotice, latitude, setLatitude, handleDecimalInputChange, activeChip,
        handleCoordinatePaste, disabled, isSubmitting, altitude, setAltitude, altitudeReference,
        setAltitudeReference, setCoordinatePasteNotice, fixedAccuracyM, setFixedAccuracyM,
    } = props;
    const pasteHelpId = `base-coordinate-paste-help-${device.serial}`;
    let precisionHelp = '';
    if (activeChip === 'Ublox') precisionHelp = ` base-ublox-precision-${device.serial}`;
    else if (activeChip === 'Unicorecomm') precisionHelp = ` base-unicore-precision-${device.serial}`;
    const coordinateDigits = activeChip === 'Ublox' ? 9 : 10;
    return <div className="row g-3">
        <div className="col-12 text-muted small fst-italic mb-2">* Chế độ này yêu cầu bạn đã có tọa độ chính xác (mốc địa chính).</div>
        <div id={pasteHelpId} className="col-12 small text-info">
            Dùng “Sao chép cho GNSS” trong <a href="/coordinates" target="_blank" rel="noopener">công cụ chuyển tọa độ</a>, rồi dán vào một trong ba ô bên dưới để tự điền đủ tọa độ.
        </div>
        {coordinatePasteNotice && <div className={`col-12 small ${coordinatePasteNotice.error ? 'text-danger' : 'text-success'}`} role="status" aria-live="polite">{coordinatePasteNotice.message}</div>}
        <div className="col-md-6">
            <label htmlFor={`base-latitude-${device.serial}`} className="form-label">Vĩ độ (WGS 84)</label>
            <input id={`base-latitude-${device.serial}`} type="text" inputMode="decimal" className="form-control"
                value={latitude} onChange={handleDecimalInputChange(setLatitude, coordinateDigits)}
                onPaste={handleCoordinatePaste} aria-describedby={pasteHelpId + precisionHelp}
                disabled={disabled || isSubmitting} required placeholder="Ví dụ: 21.028511" />
        </div>
        <div className="col-md-6">
            <label htmlFor={`base-longitude-${device.serial}`} className="form-label">Kinh độ (WGS 84)</label>
            <input id={`base-longitude-${device.serial}`} type="text" inputMode="decimal" className="form-control"
                value={props.longitude} onChange={handleDecimalInputChange(props.setLongitude, coordinateDigits)}
                onPaste={handleCoordinatePaste} aria-describedby={pasteHelpId + precisionHelp}
                disabled={disabled || isSubmitting} required placeholder="Ví dụ: 105.854167" />
        </div>
        {activeChip === 'Ublox' && <div id={`base-ublox-precision-${device.serial}`} className="col-12 form-text mt-1">
            Ublox: vĩ độ và kinh độ tối đa 9 chữ số sau dấu . hoặc , — không nhận chữ số thứ 10.
        </div>}
        {activeChip === 'Unicorecomm' && <div id={`base-unicore-precision-${device.serial}`} className="col-12 form-text mt-1">
            Unicorecomm: vĩ độ và kinh độ gửi xuống chip có 10 chữ số thập phân; cắt phần dư, không làm tròn. Phép tính giữ đủ độ chính xác.
        </div>}
        <div className="col-md-6">
            <label htmlFor={`base-altitude-${device.serial}`} className="form-label">
                {activeChip === 'Ublox' || altitudeReference === 'ELLIPSOID' ? 'Độ cao h ellipsoid (mét)' : 'Độ cao theo cấu hình chip (mét)'}
            </label>
            <input id={`base-altitude-${device.serial}`} type="text" inputMode="decimal" className="form-control"
                value={altitude} onChange={handleDecimalInputChange(setAltitude)} onPaste={handleCoordinatePaste}
                aria-describedby={pasteHelpId} disabled={disabled || isSubmitting} required placeholder="Ví dụ: 15.5" />
            {activeChip !== 'Ublox' && <>
                <label htmlFor={`base-altitude-reference-${device.serial}`} className="form-label small mt-2">Cách chip hiểu độ cao</label>
                <select id={`base-altitude-reference-${device.serial}`} className="form-select" value={altitudeReference}
                    disabled={disabled || isSubmitting} aria-describedby={`base-altitude-reference-help-${device.serial}`}
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
                <input id={`base-accuracy-${device.serial}`} type="number" className="form-control" min="0.0001" max="10000" step="0.0001"
                    aria-describedby={`base-accuracy-help-${device.serial}`} value={fixedAccuracyM}
                    onChange={event => setFixedAccuracyM(event.target.value)} required placeholder="Ví dụ: 0.05" />
                <span className="input-group-text">m</span>
            </div>
            <div id={`base-accuracy-help-${device.serial}`} className="form-text">Chỉ nhập số: 5 = 5 m. Sai số 3D khai báo của tọa độ gốc; không phải độ chính xác đo được từ RTCM.</div>
        </div>
    </div>;
}


export { AutoBaseReviewPanel, FixedLlaCoordinateFields };
