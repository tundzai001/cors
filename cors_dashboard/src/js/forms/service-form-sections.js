function ServiceStreamModeToggle({ serverId, formState, canManageStationMode, handleChange }) {
    const name = `server${serverId}_stream_on_demand`;
    const onDemand = !!formState[name];
    return <div className="col-md-6">
        <span className="form-label">Server {serverId}: Chế độ stream</span>
        {canManageStationMode ? (
            <div className="form-check form-switch pt-2">
                <input className="form-check-input" type="checkbox" role="switch" id={name} name={name}
                    checked={onDemand} onChange={handleChange} />
                <label className="form-check-label" htmlFor={name}>
                    {onDemand ? 'Ngủ (On-Demand)' : 'Luôn mở (Always-On)'}
                </label>
            </div>
        ) : (
            <div className="pt-2">
                <span className={`badge ${onDemand ? 'bg-info' : 'bg-success'}`}>
                    <i className={`bi ${onDemand ? 'bi-moon-stars-fill' : 'bi-broadcast-pin'} me-1`}></i>
                    {onDemand ? `S${serverId}: Ngủ` : `S${serverId}: Luôn mở`}
                </span>
            </div>
        )}
    </div>;
}

export function ServiceCommonSettings({
    device, formState, handleChange, disabled, canManageStationMode, showS1StreamToggle, showS2StreamToggle,
}) {
    return <div className="mb-4 p-3 border rounded border-secondary">
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
                    <input type="text" className="form-control" id="ncomport" name="ncomport"
                        value={formState.ncomport} onChange={handleChange} placeholder="Ví dụ: TRAM_HN_01" />
                </div>
                <div className="col-md-6">
                    <label htmlFor="reconnectioninterval" className="form-label">Kết nối lại (giây)</label>
                    <input type="number" className="form-control" id="reconnectioninterval" name="reconnectioninterval"
                        value={formState.reconnectioninterval} onChange={handleChange} />
                </div>
                {showS1StreamToggle && <ServiceStreamModeToggle serverId={1} formState={formState}
                    canManageStationMode={canManageStationMode} handleChange={handleChange} />}
                {showS2StreamToggle && <ServiceStreamModeToggle serverId={2} formState={formState}
                    canManageStationMode={canManageStationMode} handleChange={handleChange} />}
            </div>
        </fieldset>
    </div>;
}
