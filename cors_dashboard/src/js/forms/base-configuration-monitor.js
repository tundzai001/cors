import { STATUS_TEXT_MAP } from './base-configuration-projection.js';

function getGgaFixBadge(ggaFix) {
    if (ggaFix === 'RTK_FIXED') return <span className="badge bg-success">🎯 RTK FIXED</span>;
    if (ggaFix === 'RTK_FLOAT') return <span className="badge bg-warning text-dark">📍 RTK FLOAT</span>;
    if (ggaFix === 'SINGLE' || ggaFix === 'GPS_FIX') return <span className="badge bg-primary">📡 SINGLE</span>;
    return <span className="badge bg-secondary">⏳ {ggaFix}</span>;
}

function copyRawGga(rawGgaSentence) {
    navigator.clipboard.writeText(rawGgaSentence).then(
        () => window.showToast?.('Đã sao chép bản tin GGA!'),
        () => window.showError?.('Không thể sao chép vào bộ nhớ tạm.')
    );
}

function AutoBaseMonitorHeader({
    device, isAutoSetupStatus, isAutoSetupActive, onStopAutoBase, disabled, isStoppingAutoBase,
    stopAutoBase, ggaFix, ggaAge, autoProgressPercent,
}) {
    const statusText = STATUS_TEXT_MAP[device.status] || device.status || 'Giám sát Rover & Base';
    const ggaAgeClass = ggaAge < 3 ? 'bg-success' : ggaAge < 10 ? 'bg-warning text-dark' : 'bg-danger';
    return <div className="card-header bg-info bg-opacity-10 border-info d-flex flex-wrap justify-content-between align-items-center py-2 gap-2">
        <div className="d-flex align-items-center gap-2">
            {isAutoSetupStatus && <span className="spinner-grow spinner-grow-sm text-info" role="status"></span>}
            <strong className="text-info small text-uppercase"><i className="bi bi-broadcast me-1"></i>{statusText}</strong>
        </div>
        <div className="d-flex align-items-center gap-2 flex-wrap">
            {isAutoSetupActive && onStopAutoBase && <button type="button" className="btn btn-outline-danger btn-sm fw-bold"
                style={{minHeight:44}} disabled={disabled || isStoppingAutoBase} onClick={stopAutoBase}>
                {isStoppingAutoBase ? <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" /> : <i className="bi bi-stop-circle-fill me-1" aria-hidden="true" />}
                {isStoppingAutoBase ? 'Đang dừng…' : 'Dừng Auto Base'}
            </button>}
            {getGgaFixBadge(ggaFix)}
            {ggaAge !== null && <span className={`badge ${ggaAgeClass}`}>GGA: {ggaAge}s</span>}
            {autoProgressPercent !== null && <span className="badge bg-primary">{autoProgressPercent}%</span>}
        </div>
    </div>;
}

function AutoBaseSampleProgress({ autoSamplesTarget, autoSamplesCollected, autoProgressPercent }) {
    if (autoSamplesTarget <= 0) return null;
    return <div className="mb-3">
        <div className="d-flex justify-content-between small text-muted mb-1">
            <span>Tiến độ thu thập mẫu tọa độ</span>
            <span>{autoSamplesCollected} / {autoSamplesTarget} mẫu</span>
        </div>
        <div className="progress" style={{height: '8px'}}>
            <div className="progress-bar progress-bar-striped progress-bar-animated bg-info" style={{width: `${autoProgressPercent || 0}%`}}></div>
        </div>
    </div>;
}

function AutoBaseRawGga({ rawGgaSentence }) {
    return <div className="mb-3">
        <div className="d-flex justify-content-between align-items-center mb-1">
            <span className="form-label small fw-bold mb-0 text-info"><i className="bi bi-terminal me-1"></i>Bản tin NMEA GGA trực tiếp từ Chip Rover:</span>
            {rawGgaSentence && <button type="button" className="btn btn-outline-info btn-sm py-0 px-2" style={{fontSize: '0.75rem'}}
                onClick={() => copyRawGga(rawGgaSentence)}>
                <i className="bi bi-clipboard me-1"></i>Copy GGA
            </button>}
        </div>
        <div className="p-2 rounded bg-black border border-info font-monospace text-success small text-break user-select-all shadow-inner"
            style={{minHeight: '38px', lineHeight: '1.4', fontSize: '0.85rem'}}>
            {rawGgaSentence || <span className="text-muted fst-italic">
                <i className="bi bi-arrow-repeat me-1 spin"></i>Đang chờ bản tin GGA từ chip GNSS (Rover mode)...
            </span>}
        </div>
    </div>;
}

function AutoBaseMonitorMetrics({ rtcmBytesReceived, rtcmPktsReceived, fixedStreak, fixedStreakReq, autoProgress, formatCoord10 }) {
    const rtcmAmount = rtcmBytesReceived > 1024 ? `${(rtcmBytesReceived / 1024).toFixed(1)} KB` : `${rtcmBytesReceived} B`;
    const roverCoordinate = autoProgress.current_coord?.lat
        ? formatCoord10(autoProgress.current_coord.lat) : 'Đang đợi Fix';
    return <div className="row g-2 text-center small">
        <div className="col-4"><div className="p-2 rounded bg-dark border border-secondary-subtle">
            <div className="text-muted" style={{fontSize: '0.75rem'}}>RTCM từ Caster</div>
            <div className="fw-bold text-info">{rtcmAmount}<span className="text-muted ms-1" style={{fontSize: '0.7rem'}}>({rtcmPktsReceived} pkts)</span></div>
        </div></div>
        <div className="col-4"><div className="p-2 rounded bg-dark border border-secondary-subtle">
            <div className="text-muted" style={{fontSize: '0.75rem'}}>Giữ RTK Fix</div>
            <div className="fw-bold text-warning">{fixedStreak} / {fixedStreakReq}s</div>
        </div></div>
        <div className="col-4"><div className="p-2 rounded bg-dark border border-secondary-subtle">
            <div className="text-muted" style={{fontSize: '0.75rem'}}>Tọa độ Rover</div>
            <div className="fw-bold text-white text-truncate" title={`${autoProgress.current_coord?.lat || '--'}, ${autoProgress.current_coord?.lon || '--'}`}>{roverCoordinate}</div>
        </div></div>
    </div>;
}

function AutoBaseMonitor(props) {
    const { device, isAutoSetupStatus, isAutoSetupActive, method, autoProgress, autoResultText,
        autoFixedResult, autoSamplesTarget, autoSamplesCollected, rawGgaSentence, rtcmBytesReceived,
        rtcmPktsReceived, fixedStreak, fixedStreakReq, formatCoord10, ...headerProps } = props;
    const showMonitor = isAutoSetupActive || (method === 'AUTO_CORS' && ['failed', 'stopped'].includes(autoProgress.phase));
    if (!showMonitor) return null;
    return <div className="mb-3"><div className="card border-info bg-dark shadow-sm">
        <AutoBaseMonitorHeader device={device} isAutoSetupStatus={isAutoSetupStatus} isAutoSetupActive={isAutoSetupActive} {...headerProps} />
        <div className="card-body p-3">
            {autoResultText && <div className={`alert ${autoFixedResult ? 'alert-success' : 'alert-warning'} py-2`} role="status" aria-atomic="true">{autoResultText}</div>}
            <AutoBaseSampleProgress autoSamplesTarget={autoSamplesTarget} autoSamplesCollected={autoSamplesCollected} autoProgressPercent={props.autoProgressPercent} />
            <AutoBaseRawGga rawGgaSentence={rawGgaSentence} />
            <AutoBaseMonitorMetrics rtcmBytesReceived={rtcmBytesReceived} rtcmPktsReceived={rtcmPktsReceived}
                fixedStreak={fixedStreak} fixedStreakReq={fixedStreakReq} autoProgress={autoProgress} formatCoord10={formatCoord10} />
        </div>
    </div></div>;
}


export { AutoBaseMonitor };
