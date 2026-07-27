// ====================== Dashboard detail shell and management panels ======================
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
    'auto_setup_rover': 'AUTO: CHẾ ĐỘ ROVER',
    'auto_setup_wait_fix': 'AUTO: CHỜ RTK FIX',
    'auto_setup_averaging': 'AUTO: LẤY MẪU TỌA ĐỘ',
    'auto_setup_base': 'AUTO: CÀI ĐẶT BASE'
};
const STATUS_CLASS_MAP = {
    'online': 'bg-success',
    'offline': 'bg-danger',
    'unprovisioned': 'bg-warning text-dark',
    'awaiting_license': 'bg-info text-dark',
    'configuring': 'bg-primary',
    'rebooting': 'bg-warning text-dark',
    'rebooting_for_reset': 'bg-danger',
    'locked': 'bg-danger',
    'sleep': 'bg-info',
    'streaming': 'bg-success',
    'starting': 'bg-warning text-dark',
    'auto_setup_rover': 'bg-info',
    'auto_setup_wait_fix': 'bg-info',
    'auto_setup_averaging': 'bg-info',
    'auto_setup_base': 'bg-info'
};

function isDeviceStaleOffline(device) {
    const rawStatus = String(device?.status || '').toLowerCase();
    if (rawStatus === 'offline') return true;
    const timestamp = Number(device?.timestamp || device?.system_info?.timestamp || 0);
    if (!timestamp) return false;
    return (Date.now() / 1000) - timestamp > 180;
}
const RoverManagementPanel = memo(function RoverManagementPanel({ device }) {
    const [rovers, setRovers] = useState([]);
    const [isLoading, setIsLoading] = useState(true);

    // Form States
    const [newUsername, setNewUsername] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [selectedMountpoint, setSelectedMountpoint] = useState('');
    const [expiryDays, setExpiryDays] = useState('365'); // Mặc định 1 năm

    // --- 1. LẤY DANH SÁCH MOUNTPOINT KHẢ DỤNG TỪ CẤU HÌNH ---
    const availableMountpoints = [];
    const config = device.service_config || {};

    if (config.server1_enabled && config.mountpoint1) {
        availableMountpoints.push({
            id: 'server1',
            name: config.mountpoint1,
            host: config.serverhost1 || 'Local'
        });
    }
    if (config.server2_enabled && config.mountpoint2) {
        availableMountpoints.push({
            id: 'server2',
            name: config.mountpoint2,
            host: config.serverhost2 || 'Local'
        });
    }

    // Tự động chọn Mountpoint đầu tiên khi load
    useEffect(() => {
        if (availableMountpoints.length > 0 && !selectedMountpoint) {
            setSelectedMountpoint(availableMountpoints[0].name);
        }
    }, [device.service_config]);

    // --- 2. TẢI DANH SÁCH ROVER ---
    const fetchRovers = async () => {
        try {
            setIsLoading(true);
            const response = await window.apiFetch(`/api/bases/${device.serial}/rovers`);
            if (!response.ok) throw new Error("Không thể tải danh sách Rover");
            const data = await response.json();
            setRovers(data);
        } catch (error) {
            window.showError(error.message);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        if (device?.serial) {
            fetchRovers();
        }
    }, [device?.serial]);

    // --- 3. XỬ LÝ TẠO ROVER ---
    const handleCreateRover = async (e) => {
        e.preventDefault();

        // Validation cơ bản
        if (!newUsername.trim() || !newPassword.trim()) {
            return window.showError("Tên đăng nhập và mật khẩu không được để trống.");
        }

        // Validation ký tự đặc biệt (QUAN TRỌNG CHO CASTER)
        // Chỉ cho phép chữ cái (a-z, A-Z), số (0-9) và gạch dưới (_)
        const validUsernameRegex = /^[a-zA-Z0-9_]+$/;
        if (!validUsernameRegex.test(newUsername)) {
            return window.showError("Tên đăng nhập chỉ được chứa chữ cái không dấu, số và dấu gạch dưới (_). Không dùng khoảng trắng hoặc ký tự đặc biệt.");
        }

        // Kiểm tra xem đã chọn mountpoint chưa
        let targetMp = selectedMountpoint;
        if (!targetMp && availableMountpoints.length > 0) {
            targetMp = availableMountpoints[0].name;
        }
        if (!targetMp) {
            // Fallback nếu chưa cấu hình gì cả (ít khi xảy ra nếu UI đúng)
            targetMp = `MP_${device.serial}`;
        }

        try {
            const payload = {
                username: newUsername,
                password: newPassword,
                mountpoint_allow: targetMp,
                expiry_days: parseInt(expiryDays)
            };

            const response = await window.apiFetch(`/api/bases/${device.serial}/rovers`, {
                method: 'POST',
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || "Tạo tài khoản thất bại");
            }

            window.showSuccess(`Đã tạo tài khoản Rover '${newUsername}' thành công!`);

            // Reset form
            setNewUsername('');
            setNewPassword('');
            fetchRovers(); // Reload list

        } catch (error) {
            window.showError(error.message);
        }
    };

    // --- 4. XỬ LÝ XÓA ROVER ---
    const handleDeleteRover = async (roverId, username) => {
        const ok = await window.CorsUI.confirmAction({
            title: 'Delete rover',
            message: `Delete rover account ${username}? It will be disconnected immediately.`,
            confirmText: 'Delete',
            tone: 'danger'
        });
        if (!ok) return;
        try {
            const response = await window.apiFetch(`/api/rovers/${roverId}`, { method: 'DELETE' });
            if (!response.ok) throw new Error("Xóa thất bại");
            window.showSuccess(`Đã xóa tài khoản '${username}'.`);
            fetchRovers();
        } catch (error) {
            window.showError(error.message);
        }
    };

    return (
        <div>
            {/* CẢNH BÁO NẾU CHƯA CẤU HÌNH SERVER NTRIP */}
            {availableMountpoints.length === 0 && (
                <div className="alert alert-warning small mb-3">
                    <i className="bi bi-exclamation-triangle-fill me-2"></i>
                    <strong>Chưa cấu hình dịch vụ phát:</strong> Trạm này chưa bật NTRIP Server nào.
                    Vui lòng sang tab <strong>"Dịch vụ"</strong> để bật Server 1 hoặc Server 2 và đặt tên Mountpoint trước khi tạo tài khoản Rover.
                </div>
            )}

            {/* FORM TẠO MỚI */}
            <div className="p-3 border rounded bg-dark-subtle mb-4 shadow-sm">
                <h5 className="text-primary-emphasis border-bottom pb-2 mb-3">
                    <i className="bi bi-person-plus-fill me-2"></i>Thêm tài khoản Rover (Khách hàng)
                </h5>
                <form onSubmit={handleCreateRover}>
                    <div className="row g-3">
                        <div className="col-md-3">
                            <label className="form-label small fw-bold">Tên đăng nhập</label>
                            <input
                                type="text"
                                className="form-control"
                                value={newUsername}
                                onChange={e => setNewUsername(e.target.value)}
                                placeholder="VD: khach_a_01"
                            />
                        </div>
                        <div className="col-md-3">
                            <label className="form-label small fw-bold">Mật khẩu</label>
                            <input
                                type="text"
                                className="form-control"
                                value={newPassword}
                                onChange={e => setNewPassword(e.target.value)}
                            />
                        </div>
                        <div className="col-md-3">
                            <label className="form-label small fw-bold">Quyền truy cập (Mountpoint)</label>
                            <select
                                className="form-select"
                                value={selectedMountpoint}
                                onChange={(e) => setSelectedMountpoint(e.target.value)}
                                disabled={availableMountpoints.length === 0}
                            >
                                {availableMountpoints.length === 0 && <option>Chưa có MP</option>}
                                {availableMountpoints.map(mp => (
                                    <option key={mp.id} value={mp.name}>
                                        {mp.name} ({mp.id === 'server1' ? 'S1' : 'S2'})
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div className="col-md-3">
                            <label className="form-label small fw-bold">Thời hạn sử dụng</label>
                            <select
                                className="form-select"
                                value={expiryDays}
                                onChange={(e) => setExpiryDays(e.target.value)}
                            >
                                <option value="1">1 Ngày (Test)</option>
                                <option value="7">1 Tuần</option>
                                <option value="30">1 Tháng</option>
                                <option value="90">3 Tháng</option>
                                <option value="180">6 Tháng</option>
                                <option value="365">1 Năm</option>
                                <option value="730">2 Năm</option>
                                <option value="1095">3 Năm</option>
                                <option value="3650">Vĩnh viễn (~10 năm)</option>
                            </select>
                        </div>
                        <div className="col-12 text-end">
                            <button type="submit" className="btn btn-primary px-4" disabled={availableMountpoints.length === 0}>
                                <i className="bi bi-plus-circle me-2"></i>Tạo Tài Khoản
                            </button>
                        </div>
                    </div>
                </form>
            </div>

            {/* DANH SÁCH TÀI KHOẢN */}
            <h5 className="mt-4 mb-3">Danh sách Rover đã tạo</h5>
            {isLoading ? (
                <div className="text-center py-3">
                    <div className="spinner-border text-primary" role="status"></div>
                    <p className="mt-2 text-muted">Đang đồng bộ danh sách...</p>
                </div>
            ) : rovers.length === 0 ? (
                <div className="text-center p-4 bg-dark rounded text-muted border border-secondary">
                    <i className="bi bi-people display-6 d-block mb-3 opacity-50"></i>
                    Chưa có tài khoản Rover nào được tạo cho trạm này.
                </div>
            ) : (
                <div className="table-responsive">
                    <table className="table table-hover align-middle table-bordered border-secondary">
                        <thead className="table-dark text-center">
                            <tr>
                                <th>Tài khoản (Username)</th>
                                <th>Mật khẩu</th>
                                <th>Mountpoint (Luồng dữ liệu)</th>
                                <th>Trạng thái</th>
                                <th>Hành động</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rovers.map(rover => (
                                <tr key={rover.id}>
                                    <td>
                                        <div className="d-flex align-items-center">
                                            <div className="bg-primary rounded-circle p-2 me-2 d-flex align-items-center justify-content-center" style={{ width: '32px', height: '32px' }}>
                                                <i className="bi bi-person-fill text-white"></i>
                                            </div>
                                            <strong>{rover.username}</strong>
                                        </div>
                                    </td>
                                    <td className="font-monospace text-warning">{rover.password}</td>
                                    <td className="text-center">
                                        <span className="badge bg-info text-dark border border-info-subtle">
                                            <i className="bi bi-broadcast me-1"></i>
                                            {/* Logic hiển thị Mountpoint, ưu tiên lấy từ config hiện tại nếu khớp, ko thì hiển thị cái fallback */}
                                            {availableMountpoints.find(mp => mp.name === rover.base_serial)
                                                ? rover.base_serial // Lưu ý: Backend đang lưu base_serial, nên cần sửa backend để lưu mountpoint nếu muốn hiển thị chính xác lịch sử.
                                                : (availableMountpoints.length > 0 ? availableMountpoints[0].name : "Mặc định")
                                            }
                                            {/* Tạm thời hiển thị MP hiện tại của trạm vì logic DB đang lưu base_serial chứ chưa lưu mountpoint cụ thể từng user. 
                                                Nhưng với caster, user đó chỉ login được vào MP đã đăng ký. 
                                                Ở đây hiển thị MP hiện tại của trạm là hợp lý nhất. */}
                                            {selectedMountpoint || (availableMountpoints[0]?.name || "N/A")}
                                        </span>
                                    </td>
                                    <td className="text-center">
                                        <span className={`badge ${rover.is_active ? 'bg-success' : 'bg-secondary'}`}>
                                            {rover.is_active ? 'Active' : 'Expired'}
                                        </span>
                                    </td>
                                    <td className="text-center">
                                        <button
                                            className="btn btn-sm btn-outline-danger"
                                            title="Xóa & Ngắt kết nối Rover này"
                                            onClick={() => handleDeleteRover(rover.id, rover.username)}
                                        >
                                            <i className="bi bi-trash3 me-1"></i> Xóa
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* HƯỚNG DẪN SỬ DỤNG */}
            <div className="alert alert-secondary small mt-4">
                <h6 className="alert-heading fw-bold"><i className="bi bi-info-circle-fill me-2"></i>Hướng dẫn kết nối cho Khách hàng:</h6>
                <hr />
                <ul className="mb-0 ps-3">
                    <li><strong>IP / Host:</strong> {window.location.hostname} (hoặc Domain của bạn)</li>
                    <li><strong>Port:</strong> 2101</li>
                    <li><strong>Mountpoint:</strong> {selectedMountpoint || "Xem ở cột Mountpoint"}</li>
                    <li><strong>Username / Password:</strong> Cấp theo danh sách trên.</li>
                </ul>
            </div>
        </div>
    );
});

// === SYSTEM INFO SIDE PANEL 
// ===============================================================================

const SystemInfoPanel = memo(function SystemInfoPanel({ device, isOpen, onClose }) {
    if (!device || !device.system_info) {
        return null;
    }

    const info = device.system_info;

    // Format uptime
    const formatUptime = (seconds) => {
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const mins = Math.floor((seconds % 3600) / 60);

        if (days > 0) return `${days}d ${hours}h ${mins}m`;
        if (hours > 0) return `${hours}h ${mins}m`;
        return `${mins}m`;
    };

    // Calculate health score
    const calculateHealthScore = () => {
        let score = 100;
        if (info.cpu?.usage_percent > 80) score -= 20;
        else if (info.cpu?.usage_percent > 60) score -= 10;

        if (info.temperature?.celsius > 70) score -= 25;
        else if (info.temperature?.celsius > 60) score -= 15;

        if (info.memory?.percent > 85) score -= 20;
        else if (info.memory?.percent > 70) score -= 10;

        if (device.measured_latency !== undefined) {
            const latency = device.measured_latency;
            if (latency > 5) score -= 30;
            else if (latency > 2) score -= 15;
        }

        return Math.max(score, 0);
    };

    const healthScore = calculateHealthScore();
    const healthColor = healthScore >= 80 ? '#51cf66' : healthScore >= 60 ? '#ffd43b' : '#fa5252';

    return (
        <div className={`system-info-panel ${isOpen ? 'open' : ''}`}>
            {/* Header */}
            <div className="panel-header-sys">
                <div className="d-flex align-items-center gap-2">
                    <i className="bi bi-cpu text-primary"></i>
                    <h5 className="mb-0">System Monitor</h5>
                </div>
                <button className="btn btn-sm btn-ghost" onClick={onClose} title="Đóng">
                    <i className="bi bi-x-lg"></i>
                </button>
            </div>

            {/* Body */}
            <div className="panel-body-sys">

                {/* 1. HEALTH SCORE & DEVICE INFO */}
                <div className="d-flex align-items-center justify-content-between mb-4 p-3 rounded bg-dark border border-secondary">
                    <div>
                        <div className="text-muted small text-uppercase">Health Score</div>
                        <div className="display-6 fw-bold" style={{ color: healthColor }}>{healthScore}%</div>
                        <div className="small text-light">{healthScore >= 80 ? 'Excellent' : 'Warning'}</div>
                    </div>
                    <div className="text-end">
                        <div className="text-muted small">Uptime</div>
                        <div className="fw-bold text-light">{info.uptime_seconds ? formatUptime(info.uptime_seconds) : 'N/A'}</div>
                        <div className="text-muted small mt-1">Chip: {device.detected_chip_type || 'Unknown'}</div>
                    </div>
                </div>

                {/* 2. CIRCULAR METRICS (CPU & RAM) */}
                <div className="row g-3 mb-4">
                    {/* CPU Ring */}
                    {info.cpu && window.ProgressRing && (
                        <div className="col-6 d-flex justify-content-center">
                            <window.ProgressRing
                                value={info.cpu.usage_percent}
                                max={100}
                                size={110}
                                strokeWidth={8}
                                color={info.cpu.usage_percent > 80 ? '#fa5252' : '#0d6efd'}
                                label="CPU Load"
                                subLabel={`${info.cpu.count} Cores`}
                            />
                        </div>
                    )}

                    {/* RAM Ring */}
                    {info.memory && window.ProgressRing && (
                        <div className="col-6 d-flex justify-content-center">
                            <window.ProgressRing
                                value={info.memory.percent}
                                max={100}
                                size={110}
                                strokeWidth={8}
                                color={info.memory.percent > 85 ? '#fa5252' : '#74c0fc'}
                                label="RAM Usage"
                                subLabel={`${Math.round(info.memory.used_mb)}/${Math.round(info.memory.total_mb)}MB`}
                            />
                        </div>
                    )}
                </div>

                {/* 3. TEMPERATURE & DISK (Bar Style for Compactness) */}
                <div className="info-section">
                    <div className="section-title"><i className="bi bi-thermometer-half me-2"></i>Temperature</div>
                    {info.temperature?.celsius ? (
                        <div className="metric-card">
                            <div className="d-flex justify-content-between mb-1">
                                <span className="fw-bold text-light">{info.temperature.celsius}°C</span>
                                <span className="text-muted small">Max: 85°C</span>
                            </div>
                            <div className="progress" style={{ height: '8px' }}>
                                <div
                                    className={`progress-bar ${info.temperature.celsius > 70 ? 'bg-danger' : 'bg-success'}`}
                                    style={{ width: `${Math.min((info.temperature.celsius / 85) * 100, 100)}%` }}
                                ></div>
                            </div>
                        </div>
                    ) : <div className="text-muted small">No sensor data</div>}
                </div>

                <div className="info-section mt-3">
                    <div className="section-title"><i className="bi bi-hdd me-2"></i>Disk Usage</div>
                    {info.disk ? (
                        <div className="metric-card">
                            <div className="d-flex justify-content-between mb-1">
                                <span className="fw-bold text-light">{info.disk.percent}% Used</span>
                                <span className="text-muted small">Free: {(info.disk.total_gb - info.disk.used_gb).toFixed(1)} GB</span>
                            </div>
                            <div className="progress" style={{ height: '8px' }}>
                                <div
                                    className={`progress-bar ${info.disk.percent > 90 ? 'bg-danger' : 'bg-primary'}`}
                                    style={{ width: `${info.disk.percent}%` }}
                                ></div>
                            </div>
                        </div>
                    ) : <div className="text-muted small">No disk data</div>}
                </div>

                <div className="info-section mt-3 mb-4">
                    <div className="section-title"><i className="bi bi-activity me-2"></i>Network Latency (Agent → Server)</div>
                    {(() => {
                        if (device.measured_latency === undefined) {
                            return <div className="text-muted small">Đang chờ dữ liệu...</div>;
                        }
                        
                        let latency = device.measured_latency.toFixed(1);
                        
                        if (latency > 5) {
                            return (
                                <div className="metric-card bg-danger bg-opacity-25 border border-danger">
                                    <div className="d-flex justify-content-between mb-1">
                                        <span className="fw-bold text-danger">Mất kết nối / Trễ cao ({latency}s)</span>
                                    </div>
                                    <div className="small text-light">Cảnh báo: Dữ liệu gửi từ trạm đang bị trễ ({latency} giây). Trạm có thể đang bị rớt mạng (LTE/Wifi) hoặc quá tải.</div>
                                </div>
                            );
                        } else if (latency >= 2) {
                            return (
                                <div className="metric-card bg-warning bg-opacity-25 border border-warning">
                                    <div className="d-flex justify-content-between mb-1">
                                        <span className="fw-bold text-warning">Trễ mạng ({latency}s)</span>
                                    </div>
                                    <div className="small text-light">Mạng chậm/không đều. Chênh lệch thời gian giữa Agent và Server là {latency}s.</div>
                                </div>
                            );
                        } else {
                            return (
                                <div className="metric-card border border-success">
                                    <div className="d-flex justify-content-between mb-1">
                                        <span className="fw-bold text-success">Ổn định ({latency}s)</span>
                                    </div>
                                    <div className="small text-light">Kết nối mạng từ trạm đến máy chủ rất tốt và liên tục.</div>
                                </div>
                            );
                        }
                    })()}
                </div>

                {/* Footer */}
                <div className="text-center text-muted small mt-4 pt-3 border-top border-secondary">
                    <i className="bi bi-clock-history me-1"></i>
                    Updated: {new Date(info.timestamp * 1000).toLocaleTimeString()}
                </div>
            </div>
        </div>
    );
});
window.SystemInfoPanel = SystemInfoPanel;

console.log('✓ SystemInfoPanel component loaded');

const StationOperationsPanel = memo(function StationOperationsPanel({ device, canEditService, canRunDeviceActions, onLock, onUnlock, onReset }) {
    const [alerts, setAlerts] = useState([]);
    const [history, setHistory] = useState([]);
    const [templates, setTemplates] = useState([]);
    const [templateId, setTemplateId] = useState('');
    const [isLoading, setIsLoading] = useState(true);

    const apiJson = async (url, options = {}) => {
        const response = await window.apiFetch(url, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `Request failed: ${response.status}`);
        return data;
    };

    const loadOps = async () => {
        if (!device?.serial) return;
        setIsLoading(true);
        try {
            const [alertsData, historyData, templatesData] = await Promise.all([
                apiJson(`${window.ApiRoutes.devices.alerts}?serial=${encodeURIComponent(device.serial)}`),
                apiJson(window.ApiRoutes.devices.history(device.serial)),
                apiJson(window.ApiRoutes.devices.templates)
            ]);
            setAlerts(alertsData.items || []);
            setHistory(historyData.events || []);
            setTemplates(templatesData.items || []);
        } catch (error) {
            window.showError ? window.showError(error.message) : console.error(error);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        loadOps();
    }, [device?.serial]);

    const applyTemplate = async () => {
        if (!templateId) return window.showError?.('Select a template first');
        await apiJson(window.ApiRoutes.devices.applyTemplate, {
            method: 'POST',
            body: JSON.stringify({ template_id: templateId, serials: [device.serial] })
        });
        window.showSuccess?.('Template applied');
        await loadOps();
    };

    const locked = device?.is_locked || device?.status === 'locked';

    if (isLoading) {
        return <div className="text-center text-muted py-4"><div className="spinner-border spinner-border-sm me-2"></div>Loading station operations...</div>;
    }

    return (
        <div className="station-ops-panel">
            <div className="station-ops-grid">
                <div className="station-ops-card">
                    <div className="station-ops-title"><i className="bi bi-exclamation-triangle me-2"></i>Device alerts</div>
                    {alerts.length ? alerts.map(alert => (
                        <div className={`station-ops-alert ${alert.severity || 'info'}`} key={alert.id}>
                            <strong>{alert.title || alert.type}</strong>
                            <span>{alert.message}</span>
                        </div>
                    )) : <div className="text-muted small">No active device alerts.</div>}
                </div>
                <div className="station-ops-card">
                    <div className="station-ops-title"><i className="bi bi-clock-history me-2"></i>Device history</div>
                    {history.length ? history.slice(0, 8).map((event, idx) => (
                        <div className="station-ops-event" key={`${event.timestamp}-${idx}`}>
                            <span>{event.timestamp ? new Date(event.timestamp * 1000).toLocaleString() : '--'}</span>
                            <strong>{event.message || 'Event'}</strong>
                        </div>
                    )) : <div className="text-muted small">No recent events for this station.</div>}
                </div>
            </div>
            <div className="station-ops-card mt-3">
                <div className="station-ops-title"><i className="bi bi-sliders me-2"></i>Template and quick actions</div>
                <div className="d-flex flex-wrap gap-2 align-items-center">
                    <select className="form-select form-select-sm bg-dark text-light border-secondary" value={templateId} onChange={e => setTemplateId(e.target.value)} style={{ maxWidth: '280px' }}>
                        <option value="">Select config template</option>
                        {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                    <button className="btn btn-sm btn-outline-info" disabled={!canEditService || !templateId} onClick={applyTemplate}>
                        <i className="bi bi-magic me-1"></i>Apply template
                    </button>
                    <button className={`btn btn-sm ${locked ? 'btn-outline-success' : 'btn-outline-warning'}`} disabled={!canRunDeviceActions} onClick={() => locked ? onUnlock(device.serial) : onLock(device.serial)}>
                        <i className={`bi ${locked ? 'bi-unlock-fill' : 'bi-lock-fill'} me-1`}></i>{locked ? 'Unlock' : 'Lock'}
                    </button>
                    <button className="btn btn-sm btn-outline-danger" disabled={!canRunDeviceActions} onClick={() => onReset(device.serial)}>
                        <i className="bi bi-power me-1"></i>Khởi động lại
                    </button>
                    <button className="btn btn-sm btn-outline-light" onClick={loadOps}>
                        <i className="bi bi-arrow-clockwise me-1"></i>Refresh
                    </button>
                </div>
            </div>
        </div>
    );
});

// =========================================
// === COMPONENT CHÍNH: DetailsPanel ===
// =========================================
const DetailsPanel = memo(function DetailsPanel({
    device,
    currentUser,
    nmeaData,
    onReset,
    onProvision,
    onConfigureChip,
    onDeployLicense,
    onConfigureService,
    onLock,
    onUnlock
}) {
    console.log("[DetailsPanel] RENDERING CYCLE! device:", device, "currentUser:", currentUser, "nmeaData:", nmeaData);
    const isAdmin = currentUser?.role === 'admin';
    const [activeTab, setActiveTab] = useState('');

    const [isEditingName, setIsEditingName] = useState(false);
    const [tempName, setTempName] = useState('');
    const [userSelectedTab, setUserSelectedTab] = useState(false);
    const [isSystemPanelOpen, setIsSystemPanelOpen] = useState(false);
    const lastSerialRef = useRef(null);
    const lastGnssConfiguredRef = useRef(false);

    const hasConfig = (config) => !!(config && typeof config === 'object' && Object.keys(config).length > 0);
    const isGnssConfigured = hasConfig(device?.base_config);
    const isServiceConfigured = hasConfig(device?.service_config);
    const baseConfigSignature = isGnssConfigured ? JSON.stringify(device.base_config) : '';
    const serviceConfigSignature = isServiceConfigured ? JSON.stringify(device.service_config) : '';
    const authStatus = device?.system_info?.auth_status || {};
    const licenseBypass = authStatus.license_bypass === true || device?.system_info?.auth_license_bypass === true;
    const serverTokenPresent = Object.prototype.hasOwnProperty.call(authStatus, 'server_token_present')
        ? authStatus.server_token_present
        : authStatus.token_required;
    const tokenCheckAvailable = authStatus.token_check_available !== false;
    const hasAuthTokenState = [
        'server_token_present',
        'token_required',
        'token_synced',
        'agent_token_present',
        'needs_token_deploy'
    ].some(key => Object.prototype.hasOwnProperty.call(authStatus, key));
    const hasAccessTokenGap =
        isDeviceStaleOffline(device)
            ? serverTokenPresent === false
            : (
                authStatus.needs_token_deploy === true ||
                serverTokenPresent === false ||
                (tokenCheckAvailable && authStatus.agent_token_present === false) ||
                (tokenCheckAvailable && authStatus.token_synced === false)
            );
    const rawStatusKey = isDeviceStaleOffline(device) ? 'offline' : String(device?.status || 'offline').toLowerCase();
    const displayStatusKey = rawStatusKey !== 'offline' && (hasAccessTokenGap || rawStatusKey === 'awaiting_license')
        ? 'awaiting_license'
        : rawStatusKey;
    const shouldShowLicenseTab =
        !licenseBypass && hasAccessTokenGap;
    const showLicenseTab = shouldShowLicenseTab || (licenseBypass && hasAuthTokenState && hasAccessTokenGap);

    useEffect(() => {
        console.log("[DetailsPanel] useEffect triggered! device.serial:", device?.serial, "device.status:", device?.status, "activeTab:", activeTab);
        if (!device) return;

        const isDeviceSwitched = lastSerialRef.current !== device.serial;
        const wasGnssConfigured = isDeviceSwitched ? false : lastGnssConfiguredRef.current;
        console.log("[DetailsPanel] isDeviceSwitched:", isDeviceSwitched, "lastSerialRef:", lastSerialRef.current, "device.serial:", device.serial);
        lastSerialRef.current = device.serial;
        lastGnssConfiguredRef.current = isGnssConfigured;

        let newActiveTab = '';

        if (shouldShowLicenseTab) {
            if (!isDeviceSwitched && activeTab) {
                return;
            }
            newActiveTab = 'license';
        } else switch (device.status) {
            case 'unprovisioned':
                newActiveTab = 'chip';
                break;
            case 'auto_setup_rover':
            case 'auto_setup_wait_fix':
            case 'auto_setup_averaging':
            case 'auto_setup_base':
                newActiveTab = 'chip';
                break;
            default:
                if (!isDeviceSwitched && activeTab) {
                    if (activeTab === 'chip' && !wasGnssConfigured && isGnssConfigured) {
                        newActiveTab = 'service';
                    } else {
                        return;
                    }
                }

                if (!isDeviceSwitched && userSelectedTab && activeTab) {
                    return;
                }

                if (!isGnssConfigured) {
                    newActiveTab = 'chip';
                } else if (!isServiceConfigured) {
                    newActiveTab = 'service';
                } else {
                    newActiveTab = 'dashboard';
                }
                break;
        }

        if (newActiveTab) {
            setActiveTab(newActiveTab);
            setUserSelectedTab(false); // Reset flag
        }

        setTempName(device.name || '');
        setIsEditingName(false);

    }, [device?.serial, device?.status, baseConfigSignature, serviceConfigSignature, shouldShowLicenseTab]);

    const handleTabClick = (tabName) => {
        setActiveTab(tabName);
        setUserSelectedTab(true);
    };

    const handleSaveName = () => {
        if (!tempName.trim()) {
            window.showError('Tên trạm không được để trống!');
            return;
        }

        if (tempName !== device.name) {
            window.handleRenameDevice(device.serial, tempName);
        }
        setIsEditingName(false);
    };

    if (!device) {

        return (
            <div className="card dashboard-detail-card bg-dark-tertiary h-100">
                <div className="card-body text-center d-flex flex-column justify-content-center">
                    <i className="bi bi-cursor-fill display-1 text-muted"></i>
                    <h3 className="mt-3 text-muted">Vui lòng chọn một trạm từ danh sách bên trái.</h3>
                </div>
            </div>
        );
    }

    const role = String(currentUser?.role || '').toLowerCase();
    const perms = Array.isArray(currentUser?.permissions) ? currentUser.permissions : [];
    const hasServiceEditPermission =
        perms.includes('edit:service_config') ||
        perms.includes('EDIT_SERVICE_CONFIG') ||
        perms.includes('manage:station_mode') ||
        perms.includes('MANAGE_STATION_MODE');
    const serialUpper = String(device?.serial || '').toUpperCase();
    const chipUpper = String(device?.detected_chip_type || '').toUpperCase();
    const isRelayLikeStation =
        serialUpper.startsWith('MONITOR_');

    const canEdit = role === 'admin' ||
        (role === 'coordinator' && device.user_id === currentUser?.id);
    const canEditService = canEdit || (isRelayLikeStation && hasServiceEditPermission);
    // REBOOTING STATE
    if (device.status === 'rebooting' || device.status === 'rebooting_for_reset') {
        return (
            <div className="card dashboard-detail-card bg-dark-tertiary h-100">
                <div className="card-body text-center d-flex flex-column justify-content-center align-items-center">
                    <div className="spinner-border text-danger" style={{ width: '3rem', height: '3rem' }} role="status">
                        <span className="visually-hidden">Loading...</span>
                    </div>
                    <h3 className="mt-4 text-danger">Đang Khởi động lại</h3>
                    <p className="text-muted mt-2">
                        Thiết bị đang khởi động lại.<br />
                        Vui lòng đợi trong giây lát để thiết bị kết nối lại.
                    </p>
                </div>
            </div>
        );
    }

    const renderTabContent = () => {
        const commonProps = { device, currentUser, disabled: !canEdit };
        const forms = window.CorsDashboardForms || {};
        const LicenseForm = forms.LicenseForm || window.LicenseForm;
        const ProvisionForm = forms.ProvisionForm || window.ProvisionForm;
        const BaseConfigForm = forms.BaseConfigForm || window.BaseConfigForm;
        const ServiceConfigForm = forms.ServiceConfigForm || window.ServiceConfigForm;
        const DashboardPanel = window.DashboardPanel;
        const missingPanel = (name) => (
            <div className="alert alert-warning small mb-0">
                <i className="bi bi-exclamation-triangle-fill me-2"></i>
                Dashboard module <strong>{name}</strong> chưa sẵn sàng. Vui lòng refresh lại trang.
            </div>
        );

        switch (activeTab) {
            case 'license':
                if (!LicenseForm) return missingPanel('LicenseForm');
                return <LicenseForm {...commonProps} onDeployLicense={onDeployLicense} />;
            case 'chip':
                if (device.status === 'unprovisioned') {
                    if (!ProvisionForm) return missingPanel('ProvisionForm');
                    return <ProvisionForm {...commonProps} onProvision={onProvision} />;
                }
                if (!BaseConfigForm) return missingPanel('BaseConfigForm');
                return <BaseConfigForm {...commonProps} onConfigureChip={onConfigureChip} />;
            case 'service':
                if (!ServiceConfigForm) return missingPanel('ServiceConfigForm');
                return <ServiceConfigForm {...commonProps} disabled={!canEditService} onConfigureService={onConfigureService} />;
            case 'dashboard':
                if (!DashboardPanel) return missingPanel('DashboardPanel');
                return <DashboardPanel device={device} nmeaData={nmeaData} />;
            case 'rovers':
                if (!canEdit) {
                    return <div className="alert alert-danger">Bạn không có quyền truy cập mục này.</div>;
                }
                return <RoverManagementPanel device={device} />;
            case 'operations':
                return <StationOperationsPanel device={device} canEditService={canEditService} canRunDeviceActions={isAdmin} onLock={onLock} onUnlock={onUnlock} onReset={onReset} />;
            default:
                return null;
        }
    };

    const canManageRovers = device && (currentUser?.role === 'admin' || (currentUser?.role === 'coordinator' && currentUser?.can_create_rovers));
    const showConfigTabs = !['rebooting', 'rebooting_for_reset'].includes(device.status) && (device.status !== 'awaiting_license' || licenseBypass);
    const showServiceAndDashboardTabs = showConfigTabs && (device.status !== 'unprovisioned' || isRelayLikeStation);
    const showStationOperationsTab = !['rebooting', 'rebooting_for_reset'].includes(device.status) && (isAdmin || canEditService);

    const isDeviceLocked = device.is_locked || device.status === 'locked';

    return (
        <div className="card dashboard-detail-card bg-dark-tertiary h-100 d-flex flex-column" style={{ position: 'relative', overflowX: 'hidden' }}>
            {isAdmin && device && device.system_info && (
                <button
                    className="btn-system-toggle"
                    onClick={() => setIsSystemPanelOpen(!isSystemPanelOpen)}
                    title="System Monitor"
                >
                    <i className="bi bi-cpu"></i>
                </button>
            )}
            {/* CARD-HEADER */}
            <div className="card-header dashboard-detail-header d-flex justify-content-between align-items-center bg-dark flex-shrink-0">
                <div className="d-flex align-items-center flex-grow-1">
                    {/* 🔥 THAY THẾ PHẦN HIỂN THỊ TÊN */}
                    {isEditingName ? (
                        <div className="input-group input-group-sm me-3" style={{ maxWidth: 'min(300px, 100%)' }}>
                            <input
                                type="text"
                                className="form-control"
                                value={tempName}
                                onChange={(e) => setTempName(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleSaveName();
                                    if (e.key === 'Escape') setIsEditingName(false);
                                }}
                                autoFocus
                            />
                            <button
                                className="btn btn-success"
                                onClick={handleSaveName}
                                title="Lưu tên mới"
                            >
                                <i className="bi bi-check"></i>
                            </button>
                            <button
                                className="btn btn-secondary"
                                onClick={() => {
                                    setTempName(device.name);
                                    setIsEditingName(false);
                                }}
                                title="Hủy"
                            >
                                <i className="bi bi-x"></i>
                            </button>
                        </div>
                    ) : (
                        <div className="d-flex align-items-center me-3">
                            <h4 className="mb-0 me-2 text-truncate" style={{ maxWidth: 'min(350px, calc(100vw - 120px))' }} title={device.name}>
                                {device.name}
                            </h4>
                            {canEdit && (
                                <button
                                    className="btn btn-link text-muted p-0 ms-1"
                                    onClick={() => {
                                        setTempName(device.name);
                                        setIsEditingName(true);
                                    }}
                                    title="Đổi tên trạm"
                                    style={{ fontSize: '1.1rem' }}
                                >
                                    <i className="bi bi-pencil-square"></i>
                                </button>
                            )}
                        </div>
                    )}

                    {/* Các Badge trạng thái */}
                    <div className="d-flex align-items-center">
                        {device.is_lte && (
                            <span className="badge bg-primary me-2" title="Trạm kết nối qua 4G/LTE">
                                <i className="bi bi-reception-4 me-1"></i>LTE
                            </span>
                        )}
                        {!device.is_synced && canEdit && (
                            <div className="me-2 position-relative group-hover-target">
                                <button
                                    className="btn btn-warning btn-sm text-dark fw-bold"
                                    onClick={() => {
                                        // Cho phép click để xem alert chi tiết nếu cần
                                        const errText = device.sync_errors ? device.sync_errors.join('\n') : "Đang chờ dữ liệu...";
                                        window.CorsUI.showToast('warning', errText);
                                    }}
                                    title={device.sync_errors ? device.sync_errors.join('\n') : "Click để xem chi tiết"}
                                >
                                    <i className="bi bi-exclamation-triangle-fill me-1"></i>
                                    Không đồng bộ (Click xem)
                                </button>
                            </div>
                        )}

                        {/* Badge trạng thái (luôn hiển thị) */}
                        <div className="d-flex align-items-center gap-1">
                            <span className={`badge ${STATUS_CLASS_MAP[displayStatusKey] || 'bg-secondary'}`}>
                                {STATUS_TEXT_MAP[displayStatusKey] || displayStatusKey}
                            </span>
                            {device.is_synced ? (
                                <i className="bi bi-check-circle-fill text-success" title={`Đã đồng bộ`} style={{ fontSize: '1rem' }}></i>
                            ) : (
                                <i className="bi bi-exclamation-triangle-fill text-warning" title={device.sync_errors && device.sync_errors.length > 0 ? device.sync_errors.join('\n') : "Không đồng bộ"} style={{ fontSize: '1rem' }}></i>
                            )}
                        </div>


                        {/* Badge LOCKED (luôn hiển thị nếu bị khóa) */}
                        {isDeviceLocked && (
                            <span className="badge bg-danger ms-2">
                                <i className="bi bi-lock-fill me-1"></i>LOCKED
                            </span>
                        )}
                    </div>
                </div>

                {/* Nút Reset */}
                {canEdit && !isEditingName && (
                    <button
                        className="btn btn-sm btn-outline-danger ms-2"
                        title="Khởi động lại nguồn trạm"
                        onClick={async () => {
                            const ok = await window.CorsUI.confirmAction({
                                title: 'Khởi động lại trạm',
                                message: `Bạn có chắc muốn khởi động lại nguồn trạm ${device.name || device.serial}?`,
                                confirmText: 'Khởi động lại',
                                tone: 'danger'
                            });
                            if (ok) onReset(device.serial);
                        }}
                    >
                        <i className="bi bi-power"></i> Khởi động lại
                    </button>
                )}

                {/* Nút Hard Reset (Chỉ Admin) */}
                {isAdmin && !isEditingName && (
                    <button
                        className="btn btn-sm btn-outline-danger ms-2"
                        title="Khôi phục cài đặt gốc"
                        onClick={async () => {
                            const ok = await window.CorsUI.confirmAction({
                                title: 'Khôi phục cài đặt gốc',
                                message: `Bạn có chắc muốn khôi phục cài đặt gốc và xóa mọi cấu hình của trạm ${device.name || device.serial} không?`,
                                confirmText: 'Khôi phục',
                                tone: 'danger'
                            });
                            if (ok) {
                                try {
                                    await window.CorsDeviceAPI.hardReset(device.serial);
                                    window.showSuccess(`Đã gửi lệnh khôi phục cài đặt gốc đến thiết bị.`);
                                } catch (error) {
                                    window.showError(`Không thể gửi lệnh khôi phục: ${error.message}`);
                                }
                            }
                        }}
                    >
                        <i className="bi bi-eraser-fill"></i> Reset Mới Cứng
                    </button>
                )}
            </div>
            <div className="details-panel-scroll dashboard-detail-body card-body overflow-auto flex-grow-1 p-3">
                {/* HIỂN THỊ TIẾN ĐỘ CẤU HÌNH (CONFIG STATUS) */}
                {device?.system_info?.config_status && !['done', 'success', 'idle'].includes(device.system_info.config_status.state) && (
                    <div className={`alert ${device.system_info.config_status.state === 'error' ? 'alert-danger' : 'alert-info'} mb-3`}>
                        <div className="d-flex align-items-center">
                            {device.system_info.config_status.state === 'executing' ? (
                                <div className="spinner-border spinner-border-sm me-2" role="status"></div>
                            ) : device.system_info.config_status.state === 'error' ? (
                                <i className="bi bi-x-circle-fill me-2 fs-5"></i>
                            ) : (
                                <i className="bi bi-info-circle-fill me-2 fs-5"></i>
                            )}
                            <div>
                                <strong>Trạng thái cấu hình thiết bị:</strong> {device.system_info.config_status.progress}
                                {device.system_info.config_status.error && (
                                    <div className="small mt-1 text-danger">Lỗi: {device.system_info.config_status.error}</div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
                <div className="dashboard-detail-meta d-flex justify-content-between align-items-center mb-3">
                    {isAdmin ? (
                        <p className="text-white-50 small mb-0 d-flex align-items-center">
                            Serial: {device.serial}
                            {(device.serial.includes('_LTE_') || device.serial.includes('LTE_') || device.serial.startsWith('MQTT_')) && (
                                <button className="btn btn-link text-warning p-0 ms-2" title="Chỉnh sửa Serial" onClick={() => {
                                    const newSerial = prompt('Nhập Serial mới cho trạm LTE này:', device.serial);
                                    if (newSerial && newSerial.trim() && newSerial.trim() !== device.serial) {
                                        window.handleRenameSerial(device.serial, newSerial.trim());
                                    }
                                }}>
                                    <i className="bi bi-pencil-square"></i>
                                </button>
                            )}
                        </p>
                    ) : (
                        <div></div>
                    )}
                    <span className="badge bg-dark-subtle text-light border border-secondary">
                        {device.detected_chip_type || 'UNKNOWN'}
                    </span>
                </div>

                <ul className="nav nav-tabs dashboard-tabs mb-3" role="tablist">
                    {showLicenseTab && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'license' ? 'active' : ''}`} onClick={() => handleTabClick('license')}>
                                <i className="bi bi-key-fill me-1"></i>License
                            </button>
                        </li>
                    )}

                    {showConfigTabs && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'chip' ? 'active' : ''}`} onClick={() => handleTabClick('chip')}>
                                <i className="bi bi-cpu-fill me-1"></i>Kích hoạt & GNSS
                            </button>
                        </li>
                    )}

                    {showServiceAndDashboardTabs && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'service' ? 'active' : ''}`} onClick={() => handleTabClick('service')}>
                                <i className="bi bi-hdd-network-fill me-1"></i>Dịch vụ
                            </button>
                        </li>
                    )}

                    {showServiceAndDashboardTabs && canManageRovers && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'rovers' ? 'active' : ''}`} onClick={() => handleTabClick('rovers')}>
                                <i className="bi bi-person-circle me-1"></i>Quản lý Rover
                            </button>
                        </li>
                    )}

                    {showStationOperationsTab && (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'operations' ? 'active' : ''}`} onClick={() => handleTabClick('operations')}>
                                <i className="bi bi-activity me-1"></i>Operations
                            </button>
                        </li>
                    )}

                    {(['online', 'sleep', 'configuring'].includes(device.status) || (device.status || '').startsWith('auto_setup')) ? (
                        <li className="nav-item">
                            <button className={`nav-link ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => handleTabClick('dashboard')}>
                                <i className="bi bi-speedometer2 me-1"></i>Overview
                            </button>
                        </li>
                    ) : null}
                </ul>

                <div className="tab-content dashboard-tab-content pt-3">
                    {renderTabContent()}
                </div>
            </div>
            {canEdit && (
                <div className="card-footer dashboard-detail-footer bg-dark border-top border-secondary p-3 mt-auto">
                    {/* CHỈ HIỂN THỊ NÚT KHÓA NẾU LÀ ADMIN */}
                    {isAdmin && (
                        <div id="admin-actions-section">
                            <div className="d-flex align-items-center justify-content-between">
                                <span className="text-muted small">
                                    <i className="bi bi-shield-lock-fill me-2 text-danger"></i>
                                    Admin Control (Chỉ Admin)
                                </span>
                                <div style={{ width: '60%' }}>
                                    {isDeviceLocked ? (
                                        <button onClick={() => onUnlock(device.serial)} className="btn btn-success w-100">
                                            <i className="bi bi-unlock-fill me-2"></i>Mở Khóa (Unlock)
                                        </button>
                                    ) : (
                                        <button onClick={() => onLock(device.serial)} className="btn btn-warning w-100 text-dark fw-bold">
                                            <i className="bi bi-lock-fill me-2"></i>Khóa Thiết Bị (Lock)
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Nếu là Coordinator thì không hiện gì hoặc hiện thông báo khác tùy bạn */}
                    {!isAdmin && (
                        <div className="text-center text-muted small">
                            <i className="bi bi-info-circle me-1"></i>
                            Bạn có quyền quản lý trạm này.
                        </div>
                    )}
                </div>
            )}
            {isAdmin && device && (
                <SystemInfoPanel
                    device={device}
                    isOpen={isSystemPanelOpen}
                    onClose={() => setIsSystemPanelOpen(false)}
                />
            )}
        </div>
    );
});
window.DetailsPanel = DetailsPanel;
if (typeof window.requestDashboardRender === 'function') {
    window.requestDashboardRender();
}
})();
