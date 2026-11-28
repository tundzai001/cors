function getBaseStartupMessage(baseStartup, receiverOffline) {
    if (receiverOffline) return 'Thiết bị offline; trạng thái bên dưới là lần nhận gần nhất.';
    const messages = {
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
        error: `Chưa thiết lập được Base: ${baseStartup.error || 'Chưa có xác nhận từ receiver.'}`,
    };
    return messages[baseStartup.state];
}

function BaseStartupNotice({ baseStartup, receiverOffline }) {
    if (!baseStartup.state) return null;
    const waiting = !receiverOffline && ['pending', 'awaiting_receiver'].includes(baseStartup.state);
    return <div className={`alert ${baseStartup.state === 'error' ? 'alert-danger' : 'alert-info'} small py-2 mb-3`}
        role="status" aria-atomic="true" aria-busy={waiting}>
        {waiting && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>}
        {getBaseStartupMessage(baseStartup, receiverOffline)}
    </div>;
}

function ReceiverCoordinatesPanel({
    receiverCoordinates, savedMethod, canUseReceiverCoordinates, disabled, isSubmitting,
    handleCopyReceiverCoordinates, handleUseReceiverCoordinates, receiverCopyNotice, formatCoord10,
}) {
    if (!receiverCoordinates) return null;
    return <div className="border rounded p-3 mt-3">
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
        {savedMethod === 'SURVEY_IN' && <div className="small text-muted mb-2">Receiver đã giữ tọa độ Base sau khảo sát.</div>}
        <div className="d-flex flex-wrap gap-2">
            <button type="button" className="btn btn-outline-info btn-sm" style={{minHeight: '44px'}}
                disabled={!canUseReceiverCoordinates} onClick={handleCopyReceiverCoordinates}>Sao chép tọa độ</button>
            <button type="button" className="btn btn-outline-primary btn-sm" style={{minHeight: '44px'}}
                disabled={!canUseReceiverCoordinates || disabled || isSubmitting}
                onClick={handleUseReceiverCoordinates}>Điền vào Fixed LLA</button>
        </div>
        {receiverCopyNotice && <div className="small text-info mt-2" role="status" aria-live="polite">{receiverCopyNotice}</div>}
    </div>;
}

function ReceiverPositionReadback(props) {
    const { receiverCoordinates, receiverInstantCoordinates, receiverPosition } = props;
    if (receiverCoordinates) return <ReceiverCoordinatesPanel {...props} />;
    if (receiverInstantCoordinates) return <div className="small text-warning mt-1">
        Tọa độ ước tính đang hội tụ, chưa chốt Survey-In: <code>{receiverInstantCoordinates.lat}, {receiverInstantCoordinates.lon}</code>
        {' · '}h ellipsoid {receiverInstantCoordinates.alt} m · {receiverPosition.position_type}
    </div>;
    if (receiverPosition.state !== 'ok') return null;
    return <div className="small text-muted mt-1">
        {receiverPosition.message || 'BESTPOS'} hiện tại: <code>{String(receiverPosition.latitude)}, {String(receiverPosition.longitude)}</code>
        {' · '}độ cao chip báo {String(receiverPosition.height_msl)} m
        {receiverPosition.position_type ? ` · ${receiverPosition.position_type}` : ''}
    </div>;
}

function getFixedCoordinateComparison(comparison) {
    if (comparison?.matches) return {
        className: 'text-success-emphasis', text: 'Tọa độ máy khớp Fixed LLA đã lưu.',
    };
    if (comparison) return {
        className: 'text-danger-emphasis',
        text: `Tọa độ máy lệch Fixed LLA đã lưu: ngang ${comparison.horizontalMeters.toFixed(4)} m; độ cao ${comparison.heightMeters.toFixed(4)} m.`,
    };
    return { className: 'text-muted', text: 'Mode BASE khớp; chưa đủ dữ liệu để so sánh tọa độ đã lưu.' };
}

function ReceiverReadbackOk(props) {
    const {
        receiverLabel, receiverReadback, receiverModeMismatch, receiverReadbackIsFresh, expectedReceiverMode,
        receiverCoordinates, receiverInstantCoordinates, receiverPosition, savedMethod, savedMethodLabel,
        fixedCoordinateComparison, ...coordinateProps
    } = props;
    const comparison = getFixedCoordinateComparison(fixedCoordinateComparison);
    const showComparison = receiverReadbackIsFresh && !receiverModeMismatch
        && receiverReadback.mode_kind === 'base' && savedMethod === 'FIXED_LLA';
    return <>
        <div className="small" role="status" aria-live="polite">
            {receiverLabel} báo <code>{receiverReadback.mode_raw}</code>
            {receiverModeMismatch && <span className="badge bg-danger ms-2">LỆCH MODE</span>}
            {receiverReadbackIsFresh && !receiverModeMismatch && expectedReceiverMode && <span className="badge bg-success ms-2">MODE KHỚP</span>}
        </div>
        <ReceiverPositionReadback receiverCoordinates={receiverCoordinates}
            receiverInstantCoordinates={receiverInstantCoordinates} receiverPosition={receiverPosition}
            savedMethod={savedMethod} {...coordinateProps} />
        {receiverModeMismatch && <div className="alert alert-danger small py-2 mt-2 mb-0" role="alert">
            Web đang lưu {savedMethodLabel}, nhưng {receiverLabel} báo {receiverReadback.mode_raw}. Hai mode không khớp.
        </div>}
        {showComparison && <div className={`small mt-2 ${comparison.className}`} role="status" aria-live="polite">{comparison.text}</div>}
        {receiverReadbackIsFresh
            ? <div className="small text-muted mt-1">Đọc cách đây {Number(receiverReadback.age_seconds) || 0} giây.</div>
            : <div className="small text-warning mt-1">Kết quả đã cũ hoặc thiết bị offline; đang chờ bản cập nhật mới.</div>}
    </>;
}

function ReceiverReadbackStatus(props) {
    const { supportsReceiverReadback, receiverReadback, receiverLabel } = props;
    if (!supportsReceiverReadback) return <div className="small text-muted" role="status">Đang chờ nhận diện receiver GNSS.</div>;
    if (receiverReadback.state === 'error') return <div className="small text-danger" role="status" aria-live="polite">
        Không đọc được mode từ {receiverLabel}: {receiverReadback.error || 'Không có phản hồi.'}
    </div>;
    if (receiverReadback.state !== 'ok') return <div className="small text-muted" role="status">Đang chờ thiết bị tự cập nhật mode và tọa độ.</div>;
    return <ReceiverReadbackOk {...props} />;
}

function getSurveyStatusMessage(surveyState, surveyStatus, receiverLabel) {
    const messages = {
        base_ready: surveyStatus.source === 'NAV-SVIN' ? 'u-blox đã xác nhận khảo sát hoàn tất (NAV-SVIN valid).' : 'UM đã giữ tọa độ Base (BESTNAV: FIXEDPOS).',
        waiting_base: `${receiverLabel} đang khảo sát, chưa xác nhận tọa độ Base.`,
        awaiting_gga: 'Đang chờ GGA mới từ UM để xác định trạng thái.',
        awaiting_readback: 'Đang chờ trạng thái Base mới từ receiver.',
        offline: 'Thiết bị offline; cần kết nối lại để đọc trạng thái mới.',
    };
    return messages[surveyState] || 'Đang chờ thiết bị tự xác nhận trạng thái Survey-In.';
}

function getSurveyElapsedMessage(surveyStatus, surveyElapsedSeconds, surveyStartedAt) {
    const started = surveyStartedAt ? `Bắt đầu ước tính (ICT): ${surveyStartedAt}. ` : '';
    let elapsed;
    if (surveyStatus.source === 'NAV-SVIN') {
        elapsed = `Đã khảo sát khoảng ${Math.floor(surveyElapsedSeconds) || 0} giây · ${Number(surveyStatus.observations) || 0} mẫu · Sai số ước tính ${Number(surveyStatus.mean_accuracy_m || 0).toFixed(3)} m`;
    } else if (surveyStatus.elapsed_since_ack_seconds != null) {
        elapsed = `Đã qua ${Math.floor(surveyElapsedSeconds) || 0} giây từ ACK bắt đầu của UM.`;
    } else {
        elapsed = 'Không có mốc bắt đầu khảo sát của phiên này.';
    }
    const target = Number(surveyStatus.target_seconds) > 0
        ? ` Thời gian khảo sát yêu cầu: ${Number(surveyStatus.target_seconds)} giây.` : '';
    return `${started}${elapsed}${target}`;
}

function getSurveySourceMessage(surveyStatus) {
    if (['BESTNAV', 'NAV-SVIN'].includes(surveyStatus.source)) {
        return `Đọc từ receiver cách đây ${Number(surveyStatus.readback_age_seconds) || 0} giây`;
    }
    return `${Number(surveyStatus.satellites) || 0} vệ tinh · HDOP ${Number(surveyStatus.hdop || 0).toFixed(2)} · GGA cách đây ${Number(surveyStatus.gga_age_seconds) || 0} giây`;
}

function SurveyStatusPanel({
    supportsReceiverReadback, savedMethod, surveyStatus, surveyState, surveyStartedAt,
    surveyElapsedSeconds, receiverLabel,
}) {
    const visible = supportsReceiverReadback
        && (savedMethod === 'SURVEY_IN' || (surveyStatus.state && surveyStatus.state !== 'not_monitoring'));
    if (!visible) return null;
    const monitoring = surveyStatus.state && surveyStatus.state !== 'not_monitoring';
    return <div className="border-top border-secondary pt-2 mt-3">
        <div className="fw-bold small">Theo dõi Survey-In</div>
        <div className={`small mt-1 ${surveyState === 'base_ready' ? 'text-success' : 'text-warning'}`} role="status" aria-atomic="true">
            {getSurveyStatusMessage(surveyState, surveyStatus, receiverLabel)}
        </div>
        {monitoring && <>
            <div className="small text-muted mt-1">{getSurveyElapsedMessage(surveyStatus, surveyElapsedSeconds, surveyStartedAt)}</div>
            {['base_ready', 'waiting_base'].includes(surveyState)
                && <div className="small text-muted mt-1">{getSurveySourceMessage(surveyStatus)}</div>}
            <div className="small text-muted mt-1">Chỉ báo hoàn tất khi receiver xác nhận tọa độ Base.</div>
        </>}
    </div>;
}

function BaseReceiverStatusCard(props) {
    const {
        baseStartup, receiverOffline, savedMethodLabel, disabled, isReadingReceiver, supportsReceiverReadback,
        handleReadReceiver, receiverLabel, savedMethod, isSubmitting, device, pendingReadbackRevision,
    } = props;
    const readbackProps = { ...props };
    return <div className="card bg-dark border-secondary mb-3">
        <div className="card-body p-3">
            <BaseStartupNotice baseStartup={baseStartup} receiverOffline={receiverOffline} />
            <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                <div>
                    <div className="fw-bold">Trạng thái Base thực tế</div>
                    <div className="small text-muted">Web lưu: {savedMethodLabel}</div>
                    <div className="small text-muted">Thiết bị tự cập nhật mỗi 30 giây.</div>
                </div>
                <button type="button" className="btn btn-outline-info" style={{minHeight: '44px'}}
                    disabled={disabled || isReadingReceiver || !supportsReceiverReadback}
                    onClick={handleReadReceiver} aria-label="Đọc lại mode và tọa độ hiện tại từ receiver">
                    {isReadingReceiver
                        ? <><span className="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Đang chờ receiver...</>
                        : <><i className="bi bi-arrow-repeat me-2" aria-hidden="true"></i>Cập nhật ngay</>}
                </button>
            </div>
            <ReceiverReadbackStatus {...readbackProps} />
            {['Ublox', 'Unicorecomm'].includes(device.detected_chip_type)
                && <GnssConfigVerification device={device} disabled={disabled || isSubmitting || isReadingReceiver} />}
            {pendingReadbackRevision !== null
                && <div className="small text-info mt-1" role="status" aria-live="polite">Đang chờ firmware trả kết quả đọc mới...</div>}
            <SurveyStatusPanel supportsReceiverReadback={supportsReceiverReadback} savedMethod={savedMethod}
                surveyStatus={surveyStatus} surveyState={surveyState} surveyStartedAt={surveyStartedAt}
                surveyElapsedSeconds={surveyElapsedSeconds} receiverLabel={receiverLabel} />
        </div>
    </div>;
}


export { BaseReceiverStatusCard };
