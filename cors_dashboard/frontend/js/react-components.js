// ===============================================================================
// react-components.js - v2.0.0 - HOÀN CHỈNH TẤT CẢ CÁC FORM
// ===============================================================================

const { useState, useEffect, memo } = React;

// === CONSTANTS ===
const STATUS_TEXT_MAP = {
    'online': 'ONLINE',
    'offline': 'OFFLINE',
    'unprovisioned': 'CHƯA KÍCH HOẠT',
    'awaiting_license': 'CHỜ LICENSE',
    'configuring': 'ĐANG CẤU HÌNH',
    'rebooting': 'KHỞI ĐỘNG LẠI',
    'rebooting_for_reset': 'ĐANG RESET',
    'locked': 'BỊ KHÓA'
};

const STATUS_CLASS_MAP = {
    'online': 'bg-success',
    'offline': 'bg-secondary',
    'unprovisioned': 'bg-warning text-dark',
    'awaiting_license': 'bg-info text-dark',
    'configuring': 'bg-primary',
    'rebooting': 'bg-warning text-dark',
    'rebooting_for_reset': 'bg-danger',
    'locked': 'bg-danger'
};

// ===============================================================================
// === DEVICE LIST COMPONENTS
// ===============================================================================

window.DeviceListItem = memo(function DeviceListItem({ device, isSelected, onSelect, onDelete }) {
    const handleDelete = (e) => {
        e.stopPropagation();
        onDelete(device.serial, device.name);
    };

    return (
        <div 
            className={`list-group-item list-group-item-action p-3 d-flex align-items-center ${isSelected ? 'active' : ''}`}
            data-serial={device.serial}
        >
            <div 
                className="flex-grow-1 device-info-clickable" 
                style={{ cursor: 'pointer' }}
                onClick={() => onSelect(device.serial)}
            >
                <div className="d-flex w-100 justify-content-between">
                    <h5 className="mb-1 device-name fw-bold text-light">{device.name}</h5>
                    <span className={`badge status-badge ${STATUS_CLASS_MAP[device.status] || 'bg-dark'}`}>
                        {STATUS_TEXT_MAP[device.status] || device.status.toUpperCase()}
                    </span>
                </div>
                <p className="mb-1 small text-white-50 device-serial">Serial: {device.serial}</p>
                <div className="d-flex justify-content-between align-items-center mt-2">
                    <small className="device-bps text-info-emphasis fw-medium">
                        {device.status === 'online' && device.ntrip_connected ? (
                            device.bps > 0 ? (
                                `${device.bps} B/s`
                            ) : (
                                <span>
                                    <span className="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Sending...
                                </span>
                            )
                        ) : '0 B/s'}
                    </small>
                    <div>
                        <i className={`bi ${device.ntrip_connected ? 'bi-reception-4 text-success' : 'bi-reception-0 text-secondary'} ntrip-indicator me-2`}></i>
                        <small className="device-chip-type badge bg-dark-subtle text-light border border-secondary">
                            {device.detected_chip_type || 'UNKNOWN'}
                        </small>
                    </div>
                </div>
            </div>
            
            <button 
                className="btn btn-sm btn-outline-danger ms-3 delete-device-btn" 
                title="Xóa trạm khỏi danh sách"
                onClick={handleDelete}
            >
                <i className="bi bi-trash3"></i>
            </button>
        </div>
    );
});

window.DeviceList = memo(function DeviceList({ devices, selectedDeviceSerial, onSelect, onDelete }) {
    if (devices.length === 0) {
        return (
            <div className="text-center p-5 text-muted placeholder-item">
                <div className="spinner-border spinner-border-sm mb-2" role="status"></div>
                <div>Chưa có trạm nào.</div>
            </div>
        );
    }

    return (
        <>
            {devices.map(device => (
                <DeviceListItem 
                    key={device.serial}
                    device={device}
                    isSelected={device.serial === selectedDeviceSerial}
                    onSelect={onSelect}
                    onDelete={onDelete}
                />
            ))}
        </>
    );
});

// ===============================================================================
// === FORM COMPONENTS
// ===============================================================================

// --- Form Cấp License ---
const LicenseForm = memo(function LicenseForm({ device, onDeployLicense }) {
    const [licenseKey, setLicenseKey] = useState('');
    const [isGenerating, setIsGenerating] = useState(false);
    const [isDeploying, setIsDeploying] = useState(false);

    const handleGenerateKey = async () => {
        setIsGenerating(true);
        try {
            const response = await window.apiFetch('/api/license/pi', {
                method: 'POST',
                body: JSON.stringify({ serial: device.serial })
            });
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'Failed to generate license');
            }
            const data = await response.json();
            setLicenseKey(data.license_key);
            window.showSuccess('License key đã được tạo!');
        } catch (error) {
            window.showError('Không thể tạo key: ' + error.message);
        } finally {
            setIsGenerating(false);
        }
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!licenseKey) return window.showError('Vui lòng tạo license key trước!');
        setIsDeploying(true);
        try {
            await onDeployLicense(device.serial, licenseKey);
        } finally {
            setIsDeploying(false);
        }
    };

    return (
        <form onSubmit={handleSubmit}>
            <div className="alert alert-warning small">
                <i className="bi bi-key-fill me-2"></i>
                Chức năng này dùng để cấp phép cho các trạm Pi đang ở trạng thái "CHỜ LICENSE".
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
            <div className="mb-3">
                <label className="form-label">License Key</label>
                <div className="input-group">
                    <input type="text" className="form-control" value={licenseKey} readOnly placeholder="Nhấn 'Tạo Key' để tạo..." />
                    <button 
                        className="btn btn-outline-secondary" 
                        type="button" 
                        title="Sao chép Key"
                        onClick={() => {
                            if (licenseKey) {
                                navigator.clipboard.writeText(licenseKey);
                                window.showSuccess('Đã sao chép Key!');
                            }
                        }}
                    >
                        <i className="bi bi-clipboard-check"></i>
                    </button>
                </div>
            </div>
            <button type="button" onClick={handleGenerateKey} className="btn btn-info w-100 mb-2" disabled={isGenerating}>
                {isGenerating ? (
                    <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang tạo...
                    </>
                ) : (
                    <>
                        <i className="bi bi-magic me-2"></i>
                        Tạo Key
                    </>
                )}
            </button>
            <button type="submit" className="btn btn-primary w-100" disabled={isDeploying || !licenseKey}>
                {isDeploying ? (
                    <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang gửi...
                    </>
                ) : (
                    <>
                        <i className="bi bi-send-fill me-2"></i>
                        Gửi Key đến Thiết bị
                    </>
                )}
            </button>
        </form>
    );
});

// --- Form Kích hoạt trạm ---
const ProvisionForm = memo(function ProvisionForm({ device, onProvision }) {
    const [stationName, setStationName] = useState(device.name);
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!stationName.trim()) {
            return window.showError('Vui lòng nhập tên trạm!');
        }
        setIsSubmitting(true);
        try {
            await onProvision(device.serial, stationName);
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
            <form onSubmit={handleSubmit}>
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
            </form>
        </div>
    );
});

// --- Form Cấu hình GNSS (BASE) ---
const BaseConfigForm = memo(function BaseConfigForm({ device, onConfigureChip }) {
    const [method, setMethod] = useState('FIXED_LLA');
    const [latitude, setLatitude] = useState('');
    const [longitude, setLongitude] = useState('');
    const [altitude, setAltitude] = useState('');
    const [accuracyMm, setAccuracyMm] = useState('10');
    const [svinDuration, setSvinDuration] = useState('300');
    const [isSubmitting, setIsSubmitting] = useState(false);

    useEffect(() => {
        if (device.base_config) {
            const config = device.base_config;
            if (config.base_setup_method) setMethod(config.base_setup_method);
            if (config.coords) {
                setLatitude(config.coords.lat || '');
                setLongitude(config.coords.lon || '');
                setAltitude(config.coords.alt || '');
            }
            if (config.accuracy !== undefined) {
                setAccuracyMm(String(config.accuracy * 1000));
            }
            if (config.survey_in_duration !== undefined) {
                setSvinDuration(String(config.survey_in_duration));
            }
        }
    }, [device.base_config]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setIsSubmitting(true);
        
        try {
            let params = { base_setup_method: method };
            
            if (method === 'FIXED_LLA') {
                params.coords = {
                    lat: parseFloat(latitude),
                    lon: parseFloat(longitude),
                    alt: parseFloat(altitude)
                };
                params.accuracy = parseFloat(accuracyMm) / 1000.0;
            } else {
                params.survey_in_duration = parseInt(svinDuration);
                params.survey_in_accuracy = parseFloat(accuracyMm) / 1000.0;
            }
            
            const payload = {
                sensor_type: device.detected_chip_type,
                mode: 'BASE',
                params: params
            };
            
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
            <div className="mb-3">
                <label className="form-label">Phương pháp xác định vị trí</label>
                <select 
                    className="form-select" 
                    value={method}
                    onChange={(e) => setMethod(e.target.value)}
                >
                    <option value="FIXED_LLA">Nhập tọa độ thủ công (Fixed Mode)</option>
                    <option value="SURVEY_IN">Tự động khảo sát (Survey-In)</option>
                </select>
            </div>
            
            {method === 'FIXED_LLA' ? (
                <div className="row g-3">
                    <div className="col-md-6">
                        <label className="form-label">Latitude</label>
                        <input 
                            type="number" 
                            step="any" 
                            className="form-control"
                            value={latitude}
                            onChange={(e) => setLatitude(e.target.value)}
                            required
                            placeholder="Ví dụ: 21.028511"
                        />
                    </div>
                    <div className="col-md-6">
                        <label className="form-label">Longitude</label>
                        <input 
                            type="number" 
                            step="any" 
                            className="form-control"
                            value={longitude}
                            onChange={(e) => setLongitude(e.target.value)}
                            required
                            placeholder="Ví dụ: 105.854167"
                        />
                    </div>
                    <div className="col-md-6">
                        <label className="form-label">Altitude (m)</label>
                        <input 
                            type="number" 
                            step="any" 
                            className="form-control"
                            value={altitude}
                            onChange={(e) => setAltitude(e.target.value)}
                            required
                            placeholder="Ví dụ: 15.5"
                        />
                    </div>
                    <div className="col-md-6">
                        <label className="form-label">Độ chính xác vị trí (mm)</label>
                        <input 
                            type="number" 
                            className="form-control"
                            value={accuracyMm}
                            onChange={(e) => setAccuracyMm(e.target.value)}
                            required
                            placeholder="Ví dụ: 10"
                        />
                    </div>
                </div>
            ) : (
                <div className="row g-3">
                    <div className="col-md-6">
                        <label className="form-label">Thời gian khảo sát (s)</label>
                        <input 
                            type="number" 
                            className="form-control"
                            value={svinDuration}
                            onChange={(e) => setSvinDuration(e.target.value)}
                        />
                    </div>
                    <div className="col-md-6">
                        <label className="form-label">Độ chính xác vị trí (mm)</label>
                        <input 
                            type="number" 
                            className="form-control"
                            value={accuracyMm}
                            onChange={(e) => setAccuracyMm(e.target.value)}
                            required
                            placeholder="Ví dụ: 10"
                        />
                    </div>
                </div>
            )}
            
            <button type="submit" className="btn btn-primary mt-4 w-100" disabled={isSubmitting}>
                {isSubmitting ? (
                    <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang gửi...
                    </>
                ) : (
                    <>
                        <i className="bi bi-lightning-charge-fill me-2"></i>
                        Áp dụng cấu hình BASE
                    </>
                )}
            </button>
        </form>
    );
});

// --- Form Cấu hình Dịch vụ ---
const ServiceConfigForm = memo(function ServiceConfigForm({ device, onConfigureService }) {
    const [formState, setFormState] = useState({
        ncomport: '',
        reconnectioninterval: 10,
        server1_enabled: false,
        serverhost1: '',
        port1: '',
        mountpoint1: '',
        password1: '',
        server2_enabled: false,
        serverhost2: '',
        port2: '',
        mountpoint2: '',
        password2: '',
        rtcm_enabled: false,
        rtcmserver1: '',
        rtcmport1: '',
        rtcmmountpoint1: '',
        rtcmusername1: '',
        rtcmpassword1: ''
    });
    const [isSubmitting, setIsSubmitting] = useState(false);

     useEffect(() => {
        // Hàm này giờ chỉ chạy MỘT LẦN DUY NHẤT khi người dùng chọn một thiết bị MỚI.
        // Nó sẽ điền dữ liệu từ server vào form.
        // Sau đó, nó sẽ không chạy lại khi có các cập nhật status nhỏ nữa.
        if (device.service_config) {
            setFormState(prevState => ({
                ...prevState, // Giữ lại các giá trị mặc định cho những trường không có trong config
                ...device.service_config
            }));
        }
    }, [device.serial]);

    const handleChange = (e) => {
        const { name, value, type, checked } = e.target;
        setFormState(prev => ({
            ...prev,
            [name]: type === 'checkbox' ? checked : value
        }));
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        setIsSubmitting(true);
        
        try {
            const payload = {
                ...formState,
                port1: formState.port1 ? parseInt(formState.port1, 10) : null,
                port2: formState.port2 ? parseInt(formState.port2, 10) : null,
                rtcmport1: formState.rtcmport1 ? parseInt(formState.rtcmport1, 10) : null,
                reconnectioninterval: parseInt(formState.reconnectioninterval, 10)
            };
            
            await onConfigureService(device.serial, payload);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <form onSubmit={handleSubmit}>
            <div className="alert alert-info small">
                <i className="bi bi-info-circle-fill me-2"></i>
                Cấu hình các dịch vụ đầu ra (NTRIP Caster, RTCM, ...). Các thay đổi sẽ được áp dụng sau khi dịch vụ trên Pi được khởi động lại.
            </div>
            
            <div className="mb-4 p-3 border rounded border-secondary">
                <h5 className="mb-3">Cài đặt chung</h5>
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
                        <label htmlFor="reconnectioninterval" className="form-label">Thời gian kết nối lại (giây)</label>
                        <input 
                            type="number" 
                            className="form-control" 
                            id="reconnectioninterval"
                            name="reconnectioninterval"
                            value={formState.reconnectioninterval}
                            onChange={handleChange}
                        />
                    </div>
                </div>
            </div>

            <div className="accordion" id="service-config-accordion">
                {/* Server 1 */}
                <div className="accordion-item">
                    <h2 className="accordion-header">
                        <button 
                            className="accordion-button" 
                            type="button" 
                            data-bs-toggle="collapse" 
                            data-bs-target="#collapseOne"
                        >
                            <i className="bi bi-box-arrow-up me-2 text-primary"></i>
                            NTRIP Caster Server 1
                        </button>
                    </h2>
                    <div id="collapseOne" className="accordion-collapse collapse show" data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
                            <div className="form-check form-switch form-switch-lg mb-3">
                                <input 
                                    className="form-check-input" 
                                    type="checkbox" 
                                    role="switch" 
                                    id="server1_enabled"
                                    name="server1_enabled"
                                    checked={formState.server1_enabled}
                                    onChange={handleChange}
                                />
                                <label className="form-check-label" htmlFor="server1_enabled">Kích hoạt Server 1</label>
                            </div>
                            <div className="row g-3">
                                <div className="col-md-8">
                                    <label className="form-label">Host / IP</label>
                                    <input 
                                        type="text" 
                                        className="form-control"
                                        name="serverhost1"
                                        value={formState.serverhost1}
                                        onChange={handleChange}
                                    />
                                </div>
                                <div className="col-md-4">
                                    <label className="form-label">Port</label>
                                    <input 
                                        type="number" 
                                        className="form-control"
                                        name="port1"
                                        value={formState.port1}
                                        onChange={handleChange}
                                    />
                                </div>
                                <div className="col-md-6">
                                    <label className="form-label">Mountpoint</label>
                                    <input 
                                        type="text" 
                                        className="form-control"
                                        name="mountpoint1"
                                        value={formState.mountpoint1}
                                        onChange={handleChange}
                                    />
                                </div>
                                <div className="col-md-6">
                                    <label className="form-label">Password</label>
                                    <input 
                                        type="password" 
                                        className="form-control"
                                        name="password1"
                                        value={formState.password1}
                                        onChange={handleChange}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Server 2 */}
                <div className="accordion-item">
                    <h2 className="accordion-header">
                        <button 
                            className="accordion-button collapsed" 
                            type="button" 
                            data-bs-toggle="collapse" 
                            data-bs-target="#collapseTwo"
                        >
                            <i className="bi bi-box-arrow-up me-2 text-info"></i>
                            NTRIP Caster Server 2
                        </button>
                    </h2>
                    <div id="collapseTwo" className="accordion-collapse collapse" data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
                            <div className="form-check form-switch form-switch-lg mb-3">
                                <input 
                                    className="form-check-input" 
                                    type="checkbox" 
                                    role="switch" 
                                    id="server2_enabled"
                                    name="server2_enabled"
                                    checked={formState.server2_enabled}
                                    onChange={handleChange}
                                />
                                <label className="form-check-label" htmlFor="server2_enabled">Kích hoạt Server 2</label>
                            </div>
                            <div className="row g-3">
                                <div className="col-md-8">
                                    <label className="form-label">Host / IP</label>
                                    <input 
                                        type="text" 
                                        className="form-control"
                                        name="serverhost2"
                                        value={formState.serverhost2}
                                        onChange={handleChange}
                                    />
                                </div>
                                <div className="col-md-4">
                                    <label className="form-label">Port</label>
                                    <input 
                                        type="number" 
                                        className="form-control"
                                        name="port2"
                                        value={formState.port2}
                                        onChange={handleChange}
                                    />
                                </div>
                                <div className="col-md-6">
                                    <label className="form-label">Mountpoint</label>
                                    <input 
                                        type="text" 
                                        className="form-control"
                                        name="mountpoint2"
                                        value={formState.mountpoint2}
                                        onChange={handleChange}
                                    />
                                </div>
                                <div className="col-md-6">
                                    <label className="form-label">Password</label>
                                    <input 
                                        type="password" 
                                        className="form-control"
                                        name="password2"
                                        value={formState.password2}
                                        onChange={handleChange}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                {/* RTCM Client */}
                <div className="accordion-item">
                    <h2 className="accordion-header">
                        <button 
                            className="accordion-button collapsed" 
                            type="button" 
                            data-bs-toggle="collapse" 
                            data-bs-target="#collapseThree"
                        >
                            <i className="bi bi-box-arrow-in-down me-2 text-success"></i>
                            RTCM Client
                        </button>
                    </h2>
                    <div id="collapseThree" className="accordion-collapse collapse" data-bs-parent="#service-config-accordion">
                        <div className="accordion-body">
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
                            <div className="row g-3">
                                <div className="col-md-8">
                                    <label className="form-label">Caster Host / IP</label>
                                    <input 
                                        type="text" 
                                        className="form-control"
                                        name="rtcmserver1"
                                        value={formState.rtcmserver1}
                                        onChange={handleChange}
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
                                    />
                                </div>
                                <div className="col-md-6">
                                    <label className="form-label">Password</label>
                                    <input 
                                        type="password" 
                                        className="form-control"
                                        name="rtcmpassword1"
                                        value={formState.rtcmpassword1}
                                        onChange={handleChange}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <button type="submit" className="btn btn-success mt-4 w-100 btn-lg" disabled={isSubmitting}>
                {isSubmitting ? (
                    <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang lưu...
                    </>
                ) : (
                    <>
                        <i className="bi bi-save-fill me-2"></i>
                        Lưu và Áp dụng
                    </>
                )}
            </button>
        </form>
    );
});

// Tệp: react-components.js

// ... (Các component khác như DeviceListItem, LicenseForm, BaseConfigForm... nằm ở trên) ...

// ===============================================================================
// === DASHBOARD COMPONENT (PHIÊN BẢN HOÀN CHỈNH)                              ===
// ===============================================================================
// - Tự quản lý toàn bộ logic vẽ và vòng lặp animation.
// - Nhận dữ liệu NMEA qua props và tự động cập nhật giao diện.
// - Không còn phụ thuộc vào các hàm global trong main.js.

// --- Các hằng số và hàm tiện ích cho Dashboard ---
const flags = { us: new Image(), ru: new Image(), eu: new Image(), cn: new Image(), jp: new Image(), un: new Image() };
flags.us.src = '/img/flags/us.jpg';
flags.ru.src = '/img/flags/ru.jpg';
flags.eu.src = '/img/flags/eu.jpg';
flags.cn.src = '/img/flags/cn.jpg';
flags.jp.src = '/img/flags/jp.jpg';
flags.un.src = '/img/flags/un.jpg';

const colors = { us: '#00d47e', ru: '#ff5c5c', eu: '#5c9cff', cn: '#ffb95c', jp: '#ff77a9', un: '#aaaaaa' };
const constellationNames = { us: "GPS (USA)", ru: "GLONASS (Russia)", eu: "Galileo (EU)", cn: "BeiDou (China)", jp: "QZSS (Japan)", un: "Unknown" };

function getSatelliteSystem(prn) {
    if (prn >= 1 && prn <= 32) return 'us';
    if (prn >= 65 && prn <= 96) return 'ru';
    if (prn >= 201 && prn <= 235) return 'eu';
    if (prn >= 301 && prn <= 336) return 'cn';
    if (prn >= 193 && prn <= 200) return 'jp';
    return 'un';
}

// --- Component Dashboard Chính ---
const DashboardPanel = memo(function DashboardPanel({ nmeaData }) {
    const bgCanvasRef = React.useRef(null);
    const satCanvasRef = React.useRef(null);

    // Sử dụng prop `nmeaData` hoặc một đối tượng rỗng mặc định để tránh lỗi 'undefined'
    const safeNmeaData = nmeaData || { gga: null, gsa: null, satellites: {} };

    useEffect(() => {
        // --- KIỂM TRA ĐIỀU KIỆN CẦN THIẾT ---
        // Nếu canvas chưa sẵn sàng, không làm gì cả.
        if (!bgCanvasRef.current || !satCanvasRef.current) {
            return;
        }

        const bgCtx = bgCanvasRef.current.getContext('2d');
        const satCtx = satCanvasRef.current.getContext('2d');
        const container = bgCanvasRef.current.parentElement;
        let animationFrameId;

        // --- CÁC HÀM VẼ (được định nghĩa bên trong useEffect) ---

        function drawSkyplotBase(ctx) {
            const w = ctx.canvas.width, h = ctx.canvas.height;
            const centerX = w / 2, centerY = h / 2;
            const radius = Math.min(centerX, centerY) * 0.9;
            ctx.clearRect(0, 0, w, h);
            const grad = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
            grad.addColorStop(0, '#101820'); grad.addColorStop(1, '#000');
            ctx.fillStyle = grad; ctx.fillRect(0, 0, w, h);
            ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
            [0.25, 0.5, 0.75, 1].forEach(frac => { ctx.beginPath(); ctx.arc(centerX, centerY, radius * frac, 0, 2 * Math.PI); ctx.stroke(); });
            ctx.beginPath();
            for (let i = 0; i < 12; i++) {
                const angle = i * 30 * Math.PI / 180;
                ctx.moveTo(centerX, centerY);
                ctx.lineTo(centerX + radius * Math.sin(angle), centerY - radius * Math.cos(angle));
            }
            ctx.stroke();
            ctx.fillStyle = '#ccc'; ctx.font = 'bold 14px Arial';
            ctx.textAlign = 'center'; ctx.fillText('N', centerX, centerY - radius - 10);
        }

        function drawSkyplotSatellites(ctx, satellites) {
            ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
            const centerX = ctx.canvas.width / 2, centerY = ctx.canvas.height / 2;
            const radius = Math.min(centerX, centerY) * 0.9, flagSize = 22;

            satellites.forEach(sat => {
                if (sat.azimuth === null || sat.elevation === null || sat.snr === 0) return;
                const az_rad = sat.azimuth * Math.PI / 180, dist = radius * (90 - sat.elevation) / 90;
                const x = centerX + dist * Math.sin(az_rad), y = centerY - dist * Math.cos(az_rad);
                const system = getSatelliteSystem(sat.prn), flag = flags[system], isTracking = sat.isTracking;

                if (isTracking) {
                    ctx.beginPath(); ctx.arc(x, y, flagSize / 2 + 5, 0, 2 * Math.PI);
                    ctx.fillStyle = colors[system] + '60'; ctx.fill();
                }
                
                if (flag && flag.complete && flag.naturalHeight !== 0) {
                    ctx.drawImage(flag, x - flagSize / 2, y - flagSize / 2, flagSize, flagSize);
                } else {
                    ctx.beginPath(); ctx.arc(x, y, 7, 0, 2 * Math.PI);
                    ctx.fillStyle = colors[system]; ctx.fill();
                }
                
                ctx.fillStyle = '#fff'; ctx.font = 'bold 9px Arial';
                ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                ctx.fillText(sat.prn, x, y + flagSize / 2 + 7);
            });
        }

        function drawSignalBars(satellites) {
            const container = document.getElementById('signal-bars-container');
            if (!container) return;

            const groupedSats = satellites.filter(s => s.snr > 0).reduce((acc, sat) => {
                const system = getSatelliteSystem(sat.prn);
                if (!acc[system]) acc[system] = [];
                acc[system].push(sat);
                return acc;
            }, {});

            const systemOrder = ['us', 'ru', 'eu', 'cn', 'jp', 'un'];
            let html = '';
            systemOrder.forEach(system => {
                if (groupedSats[system]) {
                    const sats = groupedSats[system].sort((a, b) => b.snr - a.snr);
                    html += `<div class="constellation-group">
                                <div class="constellation-header">
                                    <img src="${flags[system].src}" width="24" height="24" />
                                    ${constellationNames[system]} (${sats.length})
                                </div>
                                <div class="bars-wrapper">`;
                    sats.forEach(sat => {
                        const barFillHeight = Math.min(100, sat.snr / 55 * 100);
                        html += `<div class="sat-bar-item">
                                    <div class="sat-bar-snr">${sat.snr}</div>
                                    <div class="sat-bar">
                                        <div class="sat-bar-fill" style="height: ${barFillHeight}%; background-color: ${colors[system]};"></div>
                                    </div>
                                    <div class="sat-bar-prn">${sat.prn}</div>
                                 </div>`;
                    });
                    html += `</div></div>`;
                }
            });
            container.innerHTML = html;
        }

        // --- VÒNG LẶP ANIMATION ---
        const animate = () => {
            // Sử dụng dữ liệu đã được "bảo vệ"
            const { gga, gsa, satellites } = safeNmeaData;
            
            // Cập nhật các trường text (thêm kiểm tra null)
            const fixStatusEl = document.getElementById('db-fix-status');
            if (fixStatusEl) {
                fixStatusEl.textContent = gga?.fix_status || 'N/A';
                fixStatusEl.className = 'fw-bold';
                if (gga?.fix_status === 'RTK_FIXED') fixStatusEl.classList.add('text-success');
                else if (gga?.fix_status === 'RTK_FLOAT') fixStatusEl.classList.add('text-warning');
                else fixStatusEl.classList.add('text-info');
            }
            const satsEl = document.getElementById('db-sats');
            if (satsEl) satsEl.textContent = gga?.satellites || 0;
            const dopEl = document.getElementById('db-dop');
            if (dopEl) dopEl.textContent = `${gsa?.hdop?.toFixed(2) || '0.0'} / ${gsa?.pdop?.toFixed(2) || '0.0'}`;
            const latEl = document.getElementById('db-lat');
            if (latEl) latEl.textContent = gga?.latitude?.toFixed(8) || '0.0';
            const lonEl = document.getElementById('db-lon');
            if (lonEl) lonEl.textContent = gga?.longitude?.toFixed(8) || '0.0';

            // Vẽ lại canvas và signal bars
            drawSkyplotSatellites(satCtx, Object.values(satellites));
            drawSignalBars(Object.values(satellites));
            // Chỉ vẽ khi có dữ liệu vệ tinh
            if (satellites) {
                drawSkyplotSatellites(satCtx, Object.values(satellites));
                drawSignalBars(Object.values(satellites));
            }
            animationFrameId = requestAnimationFrame(animate);
        };
        
        // --- QUẢN LÝ VÒNG ĐỜI ---
        const handleResize = () => {
            // Thêm kiểm tra để đảm bảo các phần tử đã tồn tại
            if (container && bgCanvasRef.current && satCanvasRef.current) {
                bgCanvasRef.current.width = container.clientWidth;
                bgCanvasRef.current.height = container.clientHeight;
                satCanvasRef.current.width = container.clientWidth;
                satCanvasRef.current.height = container.clientHeight;
                drawSkyplotBase(bgCtx);
            }
        };

        handleResize(); // Chạy lần đầu
        window.addEventListener('resize', handleResize);
        
        animate(); // Bắt đầu vòng lặp animation

        // Hàm dọn dẹp: sẽ được gọi khi component bị "unmount" (ẩn đi)
        return () => {
            window.removeEventListener('resize', handleResize);
            if (animationFrameId) {
                cancelAnimationFrame(animationFrameId);
            }
        };

    }, [nmeaData]); // QUAN TRỌNG: Effect này sẽ chạy lại mỗi khi prop 'nmeaData' thay đổi.

    // --- JSX ĐỂ RENDER RA HTML ---
    return (
        <div>
            <div className="p-3 rounded bg-dark border border-secondary mb-4">
                <h5 className="text-primary-emphasis">Trạng thái Real-time</h5>
                <div className="row g-3">
                    <div className="col-md-4"><strong>Trạng thái Fix:</strong> <span id="db-fix-status">N/A</span></div>
                    <div className="col-md-4"><strong>Số vệ tinh:</strong> <span id="db-sats">0</span></div>
                    <div className="col-md-4"><strong>HDOP / PDOP:</strong> <span id="db-dop">0.0 / 0.0</span></div>
                    <div className="col-md-6"><strong>Latitude:</strong> <span id="db-lat" className="font-monospace">0.0</span></div>
                    <div className="col-md-6"><strong>Longitude:</strong> <span id="db-lon" className="font-monospace">0.0</span></div>
                </div>
            </div>
            <div className="row g-4">
                <div className="col-lg-5">
                    <h6>Biểu đồ Bầu trời (Skyplot)</h6>
                    <div className="skyplot-container position-relative w-100" style={{ paddingTop: '100%' }}>
                        <canvas ref={bgCanvasRef} id="skyplot-bg-canvas" className="position-absolute top-0 start-0 w-100 h-100"></canvas>
                        <canvas ref={satCanvasRef} id="skyplot-sat-canvas" className="position-absolute top-0 start-0 w-100 h-100"></canvas>
                    </div>
                </div>
                <div className="col-lg-7">
                    <h6>Cường độ tín hiệu (Signal Bars)</h6>
                    <div id="signal-bars-container" className="signal-bars-container">
                        {/* Nội dung được vẽ bởi hàm drawSignalBars */}
                    </div>
                </div>
            </div>
        </div>
    );
});

// ===============================================================================
// === COMPONENT CHÍNH: DetailsPanel                                           ===
// ===============================================================================

window.DetailsPanel = memo(function DetailsPanel({ 
    device, 
    nmeaData,
    onReset, 
    onProvision, 
    onConfigureChip, 
    onDeployLicense, 
    onConfigureService,
    onLock,
    onUnlock 
}) {
    const [activeTab, setActiveTab] = useState('');

    useEffect(() => {
        if (!device) return;
        
        let newActiveTab = '';
        switch (device.status) {
            case 'awaiting_license':
                newActiveTab = 'license';
                break;
            case 'unprovisioned':
                newActiveTab = 'chip';
                break;
            case 'online':
            case 'configuring':
                const isGnssConfigured = device.base_config && Object.keys(device.base_config).length > 0;
                const isServiceConfigured = device.service_config && Object.keys(device.service_config).length > 0;
                if (!isGnssConfigured) newActiveTab = 'chip';
                else if (!isServiceConfigured) newActiveTab = 'service';
                else newActiveTab = 'dashboard';
                break;
        }

        if (newActiveTab && newActiveTab !== activeTab) {
            setActiveTab(newActiveTab);
        }
    }, [device]); // Chạy lại mỗi khi đối tượng device thay đổi

    if (!device) {
        return (
            <div className="card bg-dark-tertiary h-100">
                <div className="card-body text-center d-flex flex-column justify-content-center">
                    <i className="bi bi-cursor-fill display-1 text-muted"></i>
                    <h3 className="mt-3 text-muted">Vui lòng chọn một trạm từ danh sách bên trái.</h3>
                </div>
            </div>
        );
    }

    if (device.status === 'rebooting_for_reset') {
        return (
            <div className="card bg-dark-tertiary h-100">
                <div className="card-body text-center d-flex flex-column justify-content-center align-items-center">
                    <div className="spinner-border text-danger" style={{ width: '3rem', height: '3rem' }} role="status">
                        <span className="visually-hidden">Loading...</span>
                    </div>
                    <h3 className="mt-4 text-danger">Đang Reset Trạm</h3>
                    <p className="text-muted mt-2">
                        Thiết bị đang xóa cấu hình và khởi động lại.<br />
                        Giao diện sẽ tự động chuyển sang bước cấp License sau giây lát.
                    </p>
                </div>
            </div>
        );
    }

    const renderTabContent = () => {
        switch(activeTab) {
            case 'license':
                return <LicenseForm device={device} onDeployLicense={onDeployLicense} />;
            case 'chip':
                if (device.status === 'unprovisioned') {
                    return <ProvisionForm device={device} onProvision={onProvision} />;
                }
                return <BaseConfigForm device={device} onConfigureChip={onConfigureChip} />;
            case 'service':
                return <ServiceConfigForm device={device} onConfigureService={onConfigureService} />; // <-- SỬA LỖI: GỌI COMPONENT THẬT
            case 'dashboard':
                return <DashboardPanel device={device} nmeaData={nmeaData} />; 
            default:
                return null;
        }
    };

    return (
        <div className="card bg-dark-tertiary h-100">
            <div className="card-header d-flex justify-content-between align-items-center bg-dark">
                <div>
                    <h4 className="mb-0 d-inline-block me-2">{device.name}</h4>
                    <span className={`badge ${STATUS_CLASS_MAP[device.status] || 'bg-secondary'}`}>
                        {STATUS_TEXT_MAP[device.status] || device.status}
                    </span>
                    {device.is_locked && (
                        <span className="badge bg-danger ms-2">
                            <i className="bi bi-lock-fill me-1"></i>LOCKED
                        </span>
                    )}
                </div>
                <button 
                    className="btn btn-sm btn-outline-danger" 
                    title="Reset Trạm về trạng thái ban đầu"
                    onClick={() => {
                        const confirmMsg = `⚠️ XÁC NHẬN RESET\n\nBạn có chắc chắn muốn reset trạm '${device.name}' về trạng thái ban đầu không?`;
                        if (window.confirm(confirmMsg)) {
                            onReset(device.serial);
                        }
                    }}
                >
                    <i className="bi bi-arrow-counterclockwise"></i> Reset
                </button>
            </div>
            <div className="card-body overflow-auto vh-80">
                <div className="d-flex justify-content-between align-items-center mb-3">
                    <p className="text-white-50 small mb-0">Serial: {device.serial}</p>
                    <span className="badge bg-dark-subtle text-light border border-secondary">
                        {device.detected_chip_type || 'UNKNOWN'}
                    </span>
                </div>
                
                <ul className="nav nav-tabs mb-3" role="tablist">
                    {['awaiting_license'].includes(device.status) && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'license' ? 'active' : ''}`} onClick={() => setActiveTab('license')}>
                                <i className="bi bi-key-fill me-1"></i>License
                            </button>
                        </li>
                    )}
                    {['unprovisioned', 'online', 'configuring'].includes(device.status) && (
                         <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'chip' ? 'active' : ''}`} onClick={() => setActiveTab('chip')}>
                                <i className="bi bi-cpu-fill me-1"></i>Kích hoạt & GNSS
                            </button>
                        </li>
                    )}
                    {['online', 'configuring'].includes(device.status) && (
                        <>
                            <li className="nav-item">
                                <button className={`nav-link ${activeTab === 'service' ? 'active' : ''}`} onClick={() => setActiveTab('service')}>
                                    <i className="bi bi-hdd-network-fill me-1"></i>Dịch vụ
                                </button>
                            </li>
                            <li className="nav-item">
                                <button className={`nav-link ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveTab('dashboard')}>
                                    <i className="bi bi-speedometer2 me-1"></i>Dashboard
                                </button>
                            </li>
                        </>
                    )}
                </ul>

                <div className="tab-content pt-3">
                    {renderTabContent()}
                </div>
                
                <hr className="my-4" />
                
                <div id="admin-actions-section">
                    <h6 className="text-warning">Admin Actions</h6>
                    <div className="d-grid gap-2">
                        {device.is_locked ? (
                            <button onClick={() => onUnlock(device.serial)} className="btn btn-success">
                                <i className="bi bi-unlock-fill me-2"></i>Unlock Device
                            </button>
                        ) : (
                             <button onClick={() => onLock(device.serial)} className="btn btn-warning">
                                <i className="bi bi-lock-fill me-2"></i>Lock Device
                            </button>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
});

console.log('✓ React Components loaded and assigned to window object.');