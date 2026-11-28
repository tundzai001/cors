import { getAutoBaseIntroText } from './base-configuration-state.js';
import { FixedLlaCoordinateFields } from './base-configuration-review.js';

function SurveyInFields({ activeChip, svinDuration, setSvinDuration, svinAccuracyM, setSvinAccuracyM }) {
    return <div className="row g-3">
        <div className="col-12 text-muted small fst-italic mb-2">* Chế độ này thiết bị sẽ tự đo trung bình cộng vị trí. Phù hợp thiết lập trạm tạm thời.</div>
        <div className="col-md-6">
            <label htmlFor="base-survey-duration" className="form-label">Thời gian khảo sát (giây)</label>
            <input id="base-survey-duration" type="number" className="form-control" value={svinDuration}
                onChange={event => setSvinDuration(event.target.value)} required />
            <div className="form-text">Tối thiểu: 60s. Khuyên dùng: &gt; 300s.</div>
        </div>
        <div className="col-md-6">
            {activeChip === 'Unicorecomm' ? <div className="form-text mt-4">UM tự lấy trung bình theo thời gian. Kết quả cần được kiểm tra trước khi dùng làm tọa độ cố định.</div> : <>
                <label htmlFor="base-survey-accuracy" className="form-label fw-bold">Giới hạn sai số (m)</label>
                <input id="base-survey-accuracy" type="number" step="0.01" className="form-control" value={svinAccuracyM}
                    onChange={event => setSvinAccuracyM(event.target.value)} required placeholder="Ví dụ: 3.0" />
                <div className="form-text text-danger">Đơn vị: MÉT (Ví dụ nhập 3.0 là 3 mét)</div>
            </>}
        </div>
    </div>;
}

function BaseCoordinateMethodFields(props) {
    if (props.method === 'FIXED_LLA') return <FixedLlaCoordinateFields {...props} />;
    return <SurveyInFields {...props} />;
}

function CorsManualConnectionFields(props) {
    const {
        device, corsHost, setCorsHost, corsPort, setCorsPort, corsUsername, setCorsUsername,
        corsPassword, setCorsPassword, handleCorsFieldChange, handleLoadCorsMountpoints,
        isLoadingCorsSources, isSubmitting, corsLookupError, corsSources, selectedMountpoint,
        setSelectedMountpoint,
    } = props;
    return <>
        <div className="d-flex align-items-start gap-2 mb-3">
            <i className="bi bi-broadcast-pin text-info fs-5" aria-hidden="true"></i>
            <div>
                <div className="fw-bold text-info">Thông tin Base liên kết</div>
                <div className="form-text mt-1">Nhập đúng tài khoản Base tại đây. Các giá trị này chỉ dùng cho lần Auto Base này và được gửi trực tiếp xuống agent.</div>
            </div>
        </div>
        <div className="row g-3">
            <div className="col-md-8">
                <label htmlFor={`cors-host-${device.serial}`} className="form-label small fw-bold">IP / Host Base</label>
                <input id={`cors-host-${device.serial}`} type="text" className="form-control" value={corsHost}
                    onChange={handleCorsFieldChange(setCorsHost)} placeholder="Ví dụ: 14.238.1.125" autoComplete="off" required />
            </div>
            <div className="col-md-4">
                <label htmlFor={`cors-port-${device.serial}`} className="form-label small fw-bold">Port</label>
                <input id={`cors-port-${device.serial}`} type="number" min="1" max="65535" className="form-control"
                    value={corsPort} onChange={handleCorsFieldChange(setCorsPort)} placeholder="2101" required />
            </div>
            <div className="col-md-6">
                <label htmlFor={`cors-user-${device.serial}`} className="form-label small fw-bold">Username Base</label>
                <input id={`cors-user-${device.serial}`} type="text" className="form-control" value={corsUsername}
                    onChange={handleCorsFieldChange(setCorsUsername)} placeholder="Tài khoản NTRIP" autoComplete="username" required />
            </div>
            <div className="col-md-6">
                <label htmlFor={`cors-password-${device.serial}`} className="form-label small fw-bold">Password Base</label>
                <input id={`cors-password-${device.serial}`} type="password" className="form-control" value={corsPassword}
                    onChange={handleCorsFieldChange(setCorsPassword)} placeholder="Mật khẩu NTRIP" autoComplete="current-password" required />
            </div>
            <div className="col-12">
                <button type="button" className="btn btn-outline-info w-100" onClick={handleLoadCorsMountpoints}
                    disabled={isLoadingCorsSources || isSubmitting}>
                    {isLoadingCorsSources
                        ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Đang kết nối Base...</>
                        : <><i className="bi bi-search me-2" aria-hidden="true"></i>Bước 1: Kết nối Base & Lấy danh sách Mountpoint</>}
                </button>
            </div>
            {corsLookupError && <div className="col-12"><div className="alert alert-warning small mb-0" role="alert">
                <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>{corsLookupError}
            </div></div>}
            <div className="col-12">
                <label htmlFor={`cors-mountpoint-${device.serial}`} className="form-label small fw-bold">Bước 2: Chọn Mountpoint</label>
                <select id={`cors-mountpoint-${device.serial}`} className="form-select" value={selectedMountpoint}
                    onChange={event => setSelectedMountpoint(event.target.value)} disabled={!corsSources.length || isSubmitting} required>
                    <option value="">-- Chọn mountpoint từ Base --</option>
                    {corsSources.map(source => <option key={source.mountpoint} value={source.mountpoint}>
                        {source.mountpoint}{source.identifier ? ` · ${source.identifier}` : ''}
                    </option>)}
                </select>
                <div className="form-text">{corsSources.length
                    ? `${corsSources.length} mountpoint đã sẵn sàng. Chọn mountpoint, kiểm tra đầu ra rồi bấm Lấy tọa độ Auto Base bên dưới.`
                    : 'Bấm nút Bước 1 để tải danh sách mountpoint.'}</div>
            </div>
            <div className="col-12 mt-2"><div className="form-text text-muted mt-2">
                <i className="bi bi-info-circle me-1"></i>
                Quy trình: Chuyển chip sang Rover &rarr; Gửi NMEA GGA lên Caster &rarr; Nhận RTCM &rarr; Đạt RTK Fix &rarr; Lấy tọa độ trung bình &rarr; Xem kết quả &rarr; Xác nhận áp dụng Fixed LLA.
            </div></div>
        </div>
    </>;
}

function AutoCorsProjectionFields({ device, projectionMeridian, setProjectionMeridian, projectionScale, setProjectionScale, disabled, isAutoSetupStatus }) {
    return <div className="col-12"><details className="mb-2">
        <summary>Phép chiếu khi nguồn không gửi RTCM 1025</summary>
        <div className="row g-3 mt-1">
            <div className="col-sm-6">
                <label className="form-label" htmlFor={`auto-l0-${device.serial}`}>Kinh tuyến trục L₀ (độ)</label>
                <input id={`auto-l0-${device.serial}`} type="number" step="any" min="100" max="112" className="form-control"
                    value={projectionMeridian} onChange={event => setProjectionMeridian(event.target.value)} disabled={disabled || isAutoSetupStatus} />
            </div>
            <div className="col-sm-6">
                <label className="form-label" htmlFor={`auto-k0-${device.serial}`}>Hệ số k₀</label>
                <select id={`auto-k0-${device.serial}`} className="form-select" value={projectionScale}
                    onChange={event => setProjectionScale(event.target.value)} disabled={disabled || isAutoSetupStatus}>
                    <option value="">Theo nguồn / tỉnh</option>
                    <option value="0.9999">0.9999 — múi 3°</option>
                    <option value="0.9996">0.9996 — múi 6°</option>
                </select>
            </div>
            <div className="col-12 small text-muted">Có RTCM 1025 sẽ ưu tiên tham số của nguồn. Để trống cả hai để tra theo tỉnh khi thiếu bản tin này.</div>
        </div>
    </details></div>;
}

function AutoCorsConfigFields(props) {
    const {
        device, supportsAutoBaseReview, supportsAutoBaseNearest, corsSourceMode, setCorsSourceMode,
        disabled, isAutoSetupActive, autoFixedResult, ...manualConnectionProps
    } = props;
    return <div className="row g-3 auto-cors-config">
        {!supportsAutoBaseReview && <div className="col-12"><div className="alert alert-warning small mb-0" role="status">Cần cập nhật agent ESP32 để hỗ trợ đầy đủ luồng Auto Base.</div></div>}
        {!supportsAutoBaseNearest && <div className="col-12"><div className="alert alert-warning small mb-0" role="status">Agent này chưa hỗ trợ tự chuyển Rover và chọn trạm CORS gần nhất.</div></div>}
        <div className="col-12">
            <label htmlFor={`cors-source-mode-${device.serial}`} className="form-label fw-bold">Nguồn cải chính</label>
            <select id={`cors-source-mode-${device.serial}`} className="form-select" value={corsSourceMode}
                disabled={disabled || isAutoSetupActive} onChange={event => setCorsSourceMode(event.target.value)}>
                <option value="nearest">Tự chọn trạm CORS gần nhất trong danh sách của tôi</option>
                <option value="manual">Tự nhập IP, tài khoản và mountpoint</option>
            </select>
        </div>
        <div className="col-12"><div className="auto-cors-config__panel p-3 rounded border border-info-subtle bg-dark-subtle">
            {corsSourceMode === 'manual' ? <CorsManualConnectionFields device={device} {...manualConnectionProps} /> : <div className="alert alert-info small mb-0" role="status">
                <div className="fw-bold mb-1">ESP32 sẽ vào Rover trước</div>
                <div>Thiết bị gửi vị trí GNSS lên backend. Backend chọn trạm CORS gần nhất trong danh sách base bạn có quyền dùng, rồi gửi kết nối cải chính xuống ESP32. Khi đủ RTK_FIXED và mẫu hợp lệ, thiết bị tự áp dụng tọa độ Base.</div>
            </div>}
        </div></div>
        <AutoCorsProjectionFields device={device} projectionMeridian={props.projectionMeridian}
            setProjectionMeridian={props.setProjectionMeridian} projectionScale={props.projectionScale}
            setProjectionScale={props.setProjectionScale} disabled={disabled} isAutoSetupStatus={props.isAutoSetupStatus} />
        <div className="col-12 small text-info" role="status">LLH trung bình → VN-2000 NEh → LLH địa phương. Dùng tham số RTCM 1021/1023 và phép chiếu RTCM 1025 hoặc theo tỉnh.</div>
        <div className="col-12"><div className={`alert ${autoFixedResult ? 'alert-success' : 'alert-info'} small mb-2`}>
            <i className="bi bi-robot me-2"></i><span>{getAutoBaseIntroText(corsSourceMode, autoFixedResult)}</span>
        </div></div>
    </div>;
}


export { SurveyInFields, BaseCoordinateMethodFields, AutoCorsConfigFields };
