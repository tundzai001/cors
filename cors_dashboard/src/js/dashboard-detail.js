// ====================== Dashboard detail shell and management panels ======================
(() => {
const { useState, useEffect, memo, useRef } = React;

const STATUS_TEXT_MAP = {
    'online': 'ONLINE',
    'offline': 'OFFLINE',
    'unprovisioned': 'CHƯA KÍCH HOẠT',
    'awaiting_license': 'CHỜ LICENSE',
    'awaiting_agent': 'CHỜ AGENT',
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
    'awaiting_agent': 'bg-info text-dark',
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

function isDeviceStaleOffline(device, nowMs = Date.now()) {
    return window.CorsDashboardShared.isDeviceStaleOffline(device, nowMs);
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
    const roverFieldId = React.useId();

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
            void fetchRovers();
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
                expiry_days: Number.parseInt(expiryDays)
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
            void fetchRovers(); // Reload list

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
            void fetchRovers();
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
                            <label className="form-label small fw-bold" htmlFor={`${roverFieldId}-newUsername`}>Tên đăng nhập</label>
                            <input id={`${roverFieldId}-newUsername`}
                                type="text"
                                className="form-control"
                                value={newUsername}
                                onChange={e => setNewUsername(e.target.value)}
                                placeholder="Ví dụ: khach_a_01"
                            />
                        </div>
                        <div className="col-md-3">
                            <label className="form-label small fw-bold" htmlFor={`${roverFieldId}-newPassword`}>Mật khẩu</label>
                            <input id={`${roverFieldId}-newPassword`}
                                type="text"
                                className="form-control"
                                value={newPassword}
                                onChange={e => setNewPassword(e.target.value)}
                            />
                        </div>
                        <div className="col-md-3">
                            <label className="form-label small fw-bold" htmlFor={`${roverFieldId}-selectedMountpoint`}>Quyền truy cập (Mountpoint)</label>
                            <select id={`${roverFieldId}-selectedMountpoint`}
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
                            <label className="form-label small fw-bold" htmlFor={`${roverFieldId}-expiryDays`}>Thời hạn sử dụng</label>
                            <select id={`${roverFieldId}-expiryDays`}
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
            {isLoading && (
                <div className="text-center py-3">
                    <div className="spinner-border text-primary" role="status"></div>
                    <p className="mt-2 text-muted">Đang đồng bộ danh sách...</p>
                </div>
            )}
            {!isLoading && rovers.length === 0 && (
                <div className="text-center p-4 bg-dark rounded text-muted border border-secondary">
                    <i className="bi bi-people display-6 d-block mb-3 opacity-50"></i>
                    Chưa có tài khoản Rover nào được tạo cho trạm này.
                </div>
            )}
            {!isLoading && rovers.length > 0 && (
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
                                            {selectedMountpoint || availableMountpoints[0]?.name || 'Mặc định'}
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

const EspStoragePanel = memo(function EspStoragePanel({ device }) {
    const [pending, setPending] = useState(null);
    const [inventory, setInventory] = useState(null);
    const [inventoryOpen, setInventoryOpen] = useState(false);
    const inventoryDialog = useRef(null);
    const [notice, setNotice] = useState('');
    const [failed, setFailed] = useState(false);
    const info = device.system_info || {};
    const storage = info.nvs;
    const action = storage?.last_action;
    useEffect(() => { setPending(null); setNotice(''); setFailed(false); setInventory(null); setInventoryOpen(false); }, [device.serial]);
    useEffect(() => {
        const dialog = inventoryDialog.current;
        if (!dialog) return;
        if (inventoryOpen && !dialog.open) dialog.showModal();
        else if (!inventoryOpen && dialog.open) dialog.close();
    }, [inventoryOpen]);
    useEffect(() => {
        if (!pending) return;
        if (action?.command_id === pending.id && ['success', 'error', 'rejected'].includes(action.status)) {
            setPending(null);
            const report = storage?.inventory;
            const validInventory = !pending.inspect || report?.command_id === pending.id;
            setFailed(action.status !== 'success' || !validInventory);
            if (action.status === 'success' && pending.inspect && validInventory) setInventory(report);
            let actionNotice;
            if (action.status !== 'success') actionNotice = action.detail || 'Thiết bị từ chối thao tác.';
            else if (!validInventory) actionNotice = 'Chưa nhận được bảng NVS đúng yêu cầu. Hãy kiểm tra lại.';
            else if (pending.inspect) actionNotice = 'Đã đọc dữ liệu NVS từ thiết bị.';
            else actionNotice = 'Thiết bị đã cập nhật bộ nhớ.';
            setNotice(actionNotice);
            return;
        }
        const timeout = setTimeout(() => {
            setPending(null); setFailed(true);
            setNotice('Chưa nhận xác nhận từ thiết bị. Kiểm tra kết nối và số liệu mới trước khi thử lại.');
        }, 45000);
        return () => clearTimeout(timeout);
    }, [pending, action?.command_id, action?.status, storage?.inventory?.command_id]);
    const formatBytes = value => value != null && Number.isFinite(Number(value)) ? `${(Number(value) / 1024).toFixed(1)}\u00a0KB` : 'Chưa có số liệu';
    const offline = isDeviceStaleOffline(device);
    const disabled = !!pending || offline || device.is_locked || storage?.cleanup_supported !== true;
    async function run(group, offset = null) {
        if (disabled) return;
        if (group) {
            const description = group === 'unused_wifi'
                ? 'Xóa danh sách mạng phụ và mật khẩu Wi-Fi cấu hình cũ. Mạng đang dùng được giữ lại.'
                : 'Xóa kết quả Auto Base cũ. Cấu hình Base hiện tại và kết quả Survey-In được giữ lại.';
            const confirmed = window.CorsUI?.confirmAction
                ? await window.CorsUI.confirmAction({ title: 'Xóa dữ liệu đã chọn?', message: description, confirmText: 'Xóa dữ liệu', cancelText: 'Giữ lại', tone: 'danger' })
                : window.confirm(description);
            if (!confirmed) return;
        }
        const inspect = offset !== null;
        if (group) setInventory(null);
        setPending({ id: null, inspect }); setFailed(false); setNotice('Đang gửi yêu cầu tới ESP32…');
        try {
            const command = group ? 'CLEAR_NVS_DATA' : 'CHECK_STORAGE_STATUS';
            let payload = {};
            if (group) payload = { group, confirm: group };
            else if (inspect) payload = { inspect: true, offset };
            const response = await window.DeviceApi.sendCommand(device.serial, command, payload);
            const result = await response.json();
            if (!response.ok || !result.command_id) throw new Error(result.detail || 'Không gửi được yêu cầu.');
            setPending({ id: result.command_id, inspect });
            setNotice('Đã gửi, đang chờ thiết bị xác nhận…');
        } catch (error) { setPending(null); setFailed(true); setNotice(error.message); }
    }
    const firmware = info.disk?.firmware_bytes;
    const capacity = info.disk?.app_capacity_bytes;
    const groups = [
        { id: 'unused_wifi', title: 'Wi-Fi cấu hình cũ', detail: 'Danh sách mạng phụ và mật khẩu AP đã bỏ.' },
        { id: 'auto_base_history', title: 'Lịch sử Auto Base', detail: 'Kết quả cũ; giữ cấu hình Base và Survey-In.' },
    ];
    const savedFieldLabels = {
        WIFI_SSID: 'Tên mạng Wi-Fi', WIFI_PASS: 'Mật khẩu Wi-Fi',
        CONNECTION_TYPE: 'Kiểu kết nối', APN: 'APN mạng di động',
        MQTT_SERVER: 'Máy chủ MQTT', MQTT_PORT: 'Cổng MQTT', MQTT_USER: 'Tài khoản MQTT',
        NTRIP_SERVER: 'Máy chủ NTRIP mặc định', NTRIP_PORT: 'Cổng NTRIP mặc định',
        NTRIP_MPT: 'Mountpoint mặc định', DEVICE_NAME: 'Tên trạm', DEVICE_SERIAL: 'Mã thiết bị',
        BASE_JSON: 'Cấu hình Base', SERVICE_JSON: 'Cấu hình NTRIP',
        PROGRESS_JSON: 'Kết quả Auto Base', SURVEY_RESULT: 'Kết quả Survey-In',
        CMD_RESULT: 'Kết quả lệnh gần nhất', LICENSE_KEY: 'Khóa kích hoạt',
        base_setup_method: 'Chế độ Base', survey_in_duration: 'Thời gian Survey-In (giây)',
        survey_in_accuracy: 'Độ chính xác Survey-In (m)', 'coords.lat': 'Vĩ độ Base',
        'coords.lon': 'Kinh độ Base', 'coords.alt': 'Độ cao Base (m)',
        'gnss_options.output_mode': 'Chế độ bản tin', 'gnss_options.msm_level': 'Mức RTCM MSM',
        serverhost1: 'Máy chủ S1', port1: 'Cổng S1', mountpoint1: 'Mountpoint S1',
        username1: 'Tài khoản S1', password1: 'Mật khẩu S1',
        serverhost2: 'Máy chủ S2', port2: 'Cổng S2', mountpoint2: 'Mountpoint S2',
        username2: 'Tài khoản S2', password2: 'Mật khẩu S2',
    };
    function getNvsEntryValue(entry) {
        if (entry.error) return entry.error;
        if (entry.redacted) {
            if (entry.bytes === 0) return '(trống)';
            return 'Đã lưu · Nội dung được che';
        }
        if (entry.value === '') return '(trống)';
        return entry.value ?? 'Đã lưu';
    }
    return <section className="info-section mt-3" aria-label="Bộ nhớ ESP32" aria-busy={!!pending}>
        <div className="d-flex align-items-center justify-content-between gap-2 mb-2">
            <h6 className="mb-0">Bộ nhớ ESP32</h6>
            <button type="button" className="btn btn-sm btn-outline-primary" style={{ minHeight: 44 }} disabled={disabled} onClick={() => run(null)}>{pending ? 'Đang xử lý…' : 'Cập nhật'}</button>
        </div>
        <div className="metric-card small mb-2">
            <dl className="row mb-0 g-1">
                <dt className="col-7">RAM còn trống</dt><dd className="col-5 text-end">{formatBytes(info.memory?.free_heap_bytes)}</dd>
                <dt className="col-7">RAM thấp nhất từ khi bật</dt><dd className="col-5 text-end">{formatBytes(info.memory?.minimum_free_heap_bytes)}</dd>
                <dt className="col-7">Khối RAM trống lớn nhất</dt><dd className="col-5 text-end">{formatBytes(info.memory?.largest_free_block_bytes)}</dd>
                <dt className="col-7">Flash vật lý</dt><dd className="col-5 text-end">{formatBytes(info.disk?.total_bytes)}</dd>
                <dt className="col-7">Firmware / vùng ứng dụng</dt><dd className="col-5 text-end">{firmware != null && capacity ? `${formatBytes(firmware)} / ${formatBytes(capacity)}` : 'Chưa có số liệu'}</dd>
            </dl>
            <p className="text-muted mb-0 mt-2">Flash chứa riêng firmware, vùng cập nhật và dữ liệu lưu. Dọn dữ liệu NVS không tăng RAM.</p>
        </div>
        {!storage ? <p className="text-muted small">Cập nhật firmware để xem và dọn dữ liệu lưu trên ESP32.</p> : <>
            {(storage.partitions || []).map(partition => <div key={partition.id} className="metric-card small mb-2">
                <div className="fw-semibold">{partition.id === 'nvs' ? 'Bộ nhớ cấu hình kết nối' : 'Bộ nhớ dữ liệu trạm'}</div>
                {partition.available ? <>
                    <div className="d-flex justify-content-between gap-2 mt-1"><span>{formatBytes(partition.size_bytes)}</span><span>{partition.used_entries} / {partition.total_entries} ô đã dùng</span></div>
                    <div className="progress mt-2" role="progressbar" aria-label="Ô NVS đã dùng" aria-valuenow={partition.used_entries} aria-valuemin={0} aria-valuemax={partition.total_entries} style={{ height: 6 }}>
                        <div className="progress-bar" style={{ width: `${Math.min(100, partition.total_entries ? partition.used_entries / partition.total_entries * 100 : 0)}%` }} />
                    </div>
                    <div className="text-muted mt-1">Còn {partition.free_entries} ô lưu trữ.</div>
                </> : <p className="text-muted mb-0">Chưa đọc được bộ nhớ này.</p>}
            </div>)}
            <p className="small mb-2">Giữ lại license, khóa trạm, mạng đang dùng và cấu hình GNSS.</p>
            {groups.map(group => {
                const stored = storage.cleanup_groups?.find(item => item.id === group.id);
                return <div key={group.id} className="d-flex align-items-center justify-content-between gap-2 border-top py-2">
                    <div className="small"><strong>{group.title}</strong><div className="text-muted">{group.detail}</div></div>
                    <button type="button" className="btn btn-sm btn-outline-danger flex-shrink-0" style={{ minHeight: 44 }} disabled={disabled || !(stored?.keys > 0)} onClick={() => run(group.id)} aria-label={`Xóa ${group.title}`}>{stored?.keys > 0 ? 'Xóa' : 'Đã sạch'}</button>
                </div>;
            })}
        </>}
        <button type="button" className="btn btn-outline-primary w-100 mt-3" style={{ minHeight: 44 }} disabled={disabled || storage?.inventory_supported !== true}
            onClick={() => { setInventory(null); setInventoryOpen(true); void run(null, 0); }}>Kiểm tra NVS</button>
        {storage && storage.inventory_supported !== true && <p className="small text-muted mt-1">Cập nhật firmware để xem các mục đang lưu.</p>}
        <dialog ref={inventoryDialog} className="esp-nvs-dialog bg-dark text-light border border-secondary rounded" aria-labelledby="nvs-inventory-title"
            onCancel={() => setInventoryOpen(false)} onClose={() => setInventoryOpen(false)}>
            <div className="d-flex justify-content-between align-items-center gap-3 p-3 border-bottom border-secondary">
                <div><h5 id="nvs-inventory-title" className="mb-1">Dữ liệu NVS đang lưu</h5><div className="small text-muted">{device.name || device.serial} · Chỉ đọc, giá trị nhạy cảm được che.</div></div>
                <button type="button" className="btn btn-outline-secondary" style={{ minHeight: 44 }} onClick={() => setInventoryOpen(false)} aria-label="Đóng bảng NVS">Đóng</button>
            </div>
            <div className="p-3" aria-busy={!!pending}>
                {pending && <p role="status" className="text-info">Đang đọc NVS từ ESP32…</p>}
                {failed && <p role="alert" className="text-danger">{notice}</p>}
                {inventory && <>
                    <p className="small text-muted">{inventory.total} {inventory.fields_expanded ? 'trường đã lưu' : 'mục'} · Hiển thị {inventory.entries.length ? inventory.offset + 1 : 0}–{inventory.offset + inventory.entries.length} · {inventory.fields_expanded ? 'Wi-Fi và cấu hình trạm hiển thị trước. JSON được tách thành từng trường.' : 'Dung lượng là dữ liệu giá trị, chưa gồm phần quản lý NVS.'}</p>
                    {inventory.unavailable_partitions?.length > 0 && <p className="text-warning small">Một vùng nhớ chưa đọc được; bảng hiện các vùng khả dụng.</p>}
                    <p className="small text-muted d-md-none">Vuốt ngang để xem đủ các cột.</p>
                    <div className="table-responsive" tabIndex={0} role="region" aria-label="Danh sách mục NVS">
                        <table className="table table-dark table-striped table-hover align-middle mb-0 esp-nvs-table">
                            <caption className="visually-hidden">Dữ liệu NVS đã đọc từ thiết bị, giá trị bí mật được che</caption>
                            <thead><tr><th scope="col">Vùng lưu</th><th scope="col">Nhóm / Mục</th><th scope="col">Kiểu</th><th scope="col">Dung lượng</th><th scope="col">Giá trị</th></tr></thead>
                            <tbody>{inventory.entries.map((entry, index) => <tr key={`${entry.partition}:${entry.namespace}:${entry.key}:${index}`}>
                                <td>{['myPrefs', 'backend'].includes(entry.namespace) ? 'Cấu hình đã lưu' : 'Dữ liệu hệ thống'}</td>
                                <th scope="row"><div>{savedFieldLabels[entry.field || entry.key] || savedFieldLabels[entry.key] || entry.key}</div><div className="small text-muted">{entry.namespace} · {entry.key}</div>{entry.field && <code>{entry.field}</code>}</th>
                                <td>{entry.type || '—'}</td><td>{entry.bytes == null ? '—' : `${entry.bytes} B`}</td>
                                <td className="esp-nvs-value">{getNvsEntryValue(entry)}{entry.truncated && <span className="small text-muted d-block">Chỉ hiển thị 96 ký tự đầu.</span>}</td>
                            </tr>)}</tbody>
                        </table>
                    </div>
                    {!inventory.entries.length && <p className="text-muted mt-3">Không có mục ở trang này. Bấm kiểm tra lại để đọc từ đầu.</p>}
                </>}
            </div>
            <div className="d-flex flex-wrap justify-content-between gap-2 p-3 border-top border-secondary">
                <button type="button" className="btn btn-outline-primary" style={{ minHeight: 44 }} disabled={disabled} onClick={() => { setInventory(null); void run(null, 0); }}>Kiểm tra lại</button>
                <div className="d-flex gap-2">
                    <button type="button" className="btn btn-outline-secondary" style={{ minHeight: 44 }} disabled={disabled || !inventory || inventory.offset === 0} onClick={() => run(null, Math.max(0, inventory.offset - inventory.page_size))}>Trước</button>
                    <button type="button" className="btn btn-outline-secondary" style={{ minHeight: 44 }} disabled={disabled || !inventory?.has_more} onClick={() => run(null, inventory.offset + inventory.page_size)}>Sau</button>
                </div>
            </div>
        </dialog>
        {offline && <p className="small text-warning mt-2 mb-0">Thiết bị ngoại tuyến. Số liệu là lần nhận gần nhất.</p>}
        {notice && <p className={`small mt-2 mb-0 ${failed ? 'text-danger' : 'text-info'}`} role={failed ? 'alert' : 'status'}>{notice}</p>}
    </section>;
});
window.EspStoragePanel = EspStoragePanel;

function formatSystemUptime(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (days > 0) return `${days}d ${hours}h ${mins}m`;
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins}m`;
}

function getSystemHealthScore(info, device) {
    let score = 100;
    if (info.cpu?.usage_percent > 80) score -= 20;
    else if (info.cpu?.usage_percent > 60) score -= 10;
    if (info.temperature?.celsius > 70) score -= 25;
    else if (info.temperature?.celsius > 60) score -= 15;
    if (info.memory?.percent > 85) score -= 20;
    else if (info.memory?.percent > 70) score -= 10;
    if (device.measured_latency !== undefined) {
        if (device.measured_latency > 5) score -= 30;
        else if (device.measured_latency > 2) score -= 15;
    }
    return Math.max(score, 0);
}

function getMemoryCapacityKb(memory) {
    if (memory.total_kb !== undefined) return Math.round(memory.total_kb);
    if (memory.total_heap_bytes) return Math.round(memory.total_heap_bytes / 1024);
    return Math.round((memory.total_mb || 0) * 1024);
}

function getMemoryUsedKb(memory) {
    if (memory.used_kb !== undefined) return Math.round(memory.used_kb);
    if (memory.total_heap_bytes && memory.free_heap_bytes !== undefined) {
        return Math.round((memory.total_heap_bytes - memory.free_heap_bytes) / 1024);
    }
    return Math.round((memory.used_mb || 0) * 1024);
}

function getHeapMemoryRingLabel(memory) {
    const totalKb = getMemoryCapacityKb(memory);
    return totalKb > 0
        ? `${getMemoryUsedKb(memory)}/${totalKb} KB`
        : `${Math.round(memory.percent || 0)}%`;
}

function getMemoryRingLabel(memory, isEsp) {
    const isHeapMemory = isEsp || memory.total_heap_bytes || (memory.total_mb && memory.total_mb < 5);
    if (isHeapMemory) return getHeapMemoryRingLabel(memory);
    if (memory.total_mb !== undefined && memory.used_mb !== undefined) {
        return `${Math.round(memory.used_mb)}/${Math.round(memory.total_mb)}MB`;
    }
    return `${Math.round(memory.percent || 0)}%`;
}

function SystemHealthSummary({device, info, score, color}) {
    return <div className="d-flex align-items-center justify-content-between mb-4 p-3 rounded bg-dark border border-secondary">
        <div>
            <div className="text-muted small text-uppercase">Health Score</div>
            <div className="display-6 fw-bold" style={{color}}>{score}%</div>
            <div className="small text-light">{score >= 80 ? 'Excellent' : 'Warning'}</div>
        </div>
        <div className="text-end">
            <div className="text-muted small">Uptime</div>
            <div className="fw-bold text-light">{info.uptime_seconds ? formatSystemUptime(info.uptime_seconds) : 'N/A'}</div>
            <div className="text-muted small mt-1">Chip: {device.detected_chip_type || 'Unknown'}</div>
        </div>
    </div>;
}

function SystemResourceRings({info, isEsp}) {
    return <div className="row g-3 mb-4">
        {info.cpu && window.ProgressRing && <div className="col-6 d-flex justify-content-center">
            <window.ProgressRing value={info.cpu.usage_percent} max={100} size={110} strokeWidth={8}
                color={info.cpu.usage_percent > 80 ? '#fa5252' : '#0d6efd'} label="CPU Load" subLabel={`${info.cpu.count} Cores`} />
        </div>}
        {info.memory && window.ProgressRing && <div className="col-6 d-flex justify-content-center">
            <window.ProgressRing value={info.memory.percent} max={100} size={110} strokeWidth={8}
                color={info.memory.percent > 85 ? '#fa5252' : '#74c0fc'} label="RAM Usage"
                subLabel={getMemoryRingLabel(info.memory, isEsp)} />
        </div>}
    </div>;
}

function SystemTemperature({temperature}) {
    return <div className="info-section">
        <div className="section-title"><i className="bi bi-thermometer-half me-2"></i>Temperature</div>
        {temperature?.celsius ? <div className="metric-card">
            <div className="d-flex justify-content-between mb-1">
                <span className="fw-bold text-light">{temperature.celsius}°C</span><span className="text-muted small">Max: 85°C</span>
            </div>
            <div className="progress" style={{height:'8px'}}><div className={`progress-bar ${temperature.celsius > 70 ? 'bg-danger' : 'bg-success'}`} style={{width:`${Math.min((temperature.celsius / 85) * 100, 100)}%`}}></div></div>
        </div> : <div className="text-muted small">No sensor data</div>}
    </div>;
}

function SystemDiskMetric({disk}) {
    let freeSpace;
    if (disk.total_gb >= 1) freeSpace = `Free: ${(disk.total_gb - disk.used_gb).toFixed(1)} GB`;
    else if (disk.total_mb !== undefined) freeSpace = `Free: ${(disk.total_mb - (disk.used_mb || 0)).toFixed(1)} MB (${Math.round(disk.total_mb)}MB Flash)`;
    else freeSpace = `Free: ${(disk.total_gb - disk.used_gb).toFixed(2)} GB`;
    return <div className="metric-card">
        <div className="d-flex justify-content-between mb-1">
            <span className="fw-bold text-light">{disk.percent}% Used</span><span className="text-muted small">{freeSpace}</span>
        </div>
        <div className="progress" style={{height:'8px'}}><div className={`progress-bar ${disk.percent > 90 ? 'bg-danger' : 'bg-primary'}`} style={{width:`${disk.percent}%`}}></div></div>
    </div>;
}

function SystemDiskUsage({device, info, isEsp}) {
    if (isEsp) return <EspStoragePanel key={device.serial} device={device} />;
    return <div className="info-section mt-3">
        <div className="section-title"><i className="bi bi-hdd me-2"></i>Disk Usage</div>
        {info.disk ? <SystemDiskMetric disk={info.disk} /> : <div className="text-muted small">No disk data</div>}
    </div>;
}

function SystemLatency({latency:rawLatency}) {
    if (rawLatency === undefined) return <div className="text-muted small">Đang chờ dữ liệu...</div>;
    const latency = rawLatency.toFixed(1);
    if (latency > 5) return <div className="metric-card bg-danger bg-opacity-25 border border-danger">
        <div className="d-flex justify-content-between mb-1"><span className="fw-bold text-danger">Mất kết nối / Trễ cao ({latency}s)</span></div>
        <div className="small text-light">Cảnh báo: Dữ liệu gửi từ trạm đang bị trễ ({latency} giây). Trạm có thể đang bị rớt mạng (LTE/Wifi) hoặc quá tải.</div>
    </div>;
    if (latency >= 2) return <div className="metric-card bg-warning bg-opacity-25 border border-warning">
        <div className="d-flex justify-content-between mb-1"><span className="fw-bold text-warning">Trễ mạng ({latency}s)</span></div>
        <div className="small text-light">Mạng chậm/không đều. Chênh lệch thời gian giữa Agent và Server là {latency}s.</div>
    </div>;
    return <div className="metric-card border border-success">
        <div className="d-flex justify-content-between mb-1"><span className="fw-bold text-success">Ổn định ({latency}s)</span></div>
        <div className="small text-light">Kết nối mạng từ trạm đến máy chủ rất tốt và liên tục.</div>
    </div>;
}

const SystemInfoPanel = memo(function SystemInfoPanel({device, isOpen, onClose}) {
    if (!device?.system_info) return null;
    const info = device.system_info;
    const isEsp = /^esp_/i.test(device.serial || '');
    const healthScore = getSystemHealthScore(info, device);
    let healthColor = '#fa5252';
    if (healthScore >= 80) healthColor = '#51cf66';
    else if (healthScore >= 60) healthColor = '#ffd43b';
    return <div className={`system-info-panel ${isOpen ? 'open' : ''}`}>
        <div className="panel-header-sys">
            <div className="d-flex align-items-center gap-2"><i className="bi bi-cpu text-primary"></i><h5 className="mb-0">System Monitor</h5></div>
            <button className="btn btn-sm btn-ghost" onClick={onClose} title="Đóng"><i className="bi bi-x-lg"></i></button>
        </div>
        <div className="panel-body-sys">
            <SystemHealthSummary device={device} info={info} score={healthScore} color={healthColor} />
            <SystemResourceRings info={info} isEsp={isEsp} />
            <SystemTemperature temperature={info.temperature} />
            <SystemDiskUsage device={device} info={info} isEsp={isEsp} />
            <div className="info-section mt-3 mb-4">
                <div className="section-title"><i className="bi bi-activity me-2"></i>Network Latency (Agent → Server)</div>
                <SystemLatency latency={device.measured_latency} />
            </div>
            <div className="text-center text-muted small mt-4 pt-3 border-top border-secondary">
                <i className="bi bi-clock-history me-1"></i>Updated: {new Date(info.timestamp * 1000).toLocaleTimeString()}
            </div>
        </div>
    </div>;
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
        void loadOps();
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

    const locked = window.CorsDashboardShared.getLockedServers(device).length > 0 || device?.is_locked || device?.status === 'locked';

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
                    <button className={`btn btn-sm ${locked ? 'btn-outline-success' : 'btn-outline-warning'}`} disabled={!canRunDeviceActions} onClick={() => onLock(device.serial)}>
                        <i className={`bi ${locked ? 'bi-unlock-fill' : 'bi-lock-fill'} me-1`}></i>{locked ? 'Sửa khoá host' : 'Khoá host / server'}
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

const ConfiguredRelayPowerControl = memo(function ConfiguredRelayPowerControl({ device, currentUser }) {
    const serviceConfig = device?.service_config || {};
    const isConfiguredRelay = String(device?.serial || '').toUpperCase().startsWith('RELAY_')
        && serviceConfig.agent_station_power_control === true;
    const role = String(currentUser?.role || '').toLowerCase();
    const permissions = currentUser?.permissions || [];
    const canControl = role === 'admin'
        || permissions.includes('manage:station_mode')
        || permissions.includes('MANAGE_STATION_MODE');
    const [stationEnabled, setStationEnabled] = useState(serviceConfig.agent_station_enabled !== false);
    const [isSubmitting, setIsSubmitting] = useState(false);

    useEffect(() => {
        setStationEnabled(device?.service_config?.agent_station_enabled !== false);
    }, [device?.serial, device?.service_config?.agent_station_enabled]);

    if (!isConfiguredRelay || !canControl) return null;

    const toggleStation = async () => {
        const nextActive = !stationEnabled;
        if (!nextActive) {
            const confirmed = await window.CorsUI.confirmAction({
                title: 'Tắt hẳn trạm Relay',
                message: `Ngắt cả kết nối SOURCE lấy dữ liệu và TARGET đẩy dữ liệu của ${device.name || device.serial}. Trạm sẽ không tự kết nối lại cho đến khi được bật.`,
                confirmText: 'Tắt hẳn trạm',
                tone: 'danger'
            });
            if (!confirmed) return;
        }

        setIsSubmitting(true);
        try {
            const response = await window.apiFetch(window.ApiRoutes.devices.configuredRelayPower(device.serial), {
                method: 'POST',
                body: JSON.stringify({ active: nextActive })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || 'Không gửi được lệnh đến Relay.');
            setStationEnabled(nextActive);
            window.showSuccess(nextActive
                ? 'Đã gửi lệnh bật trạm Relay.'
                : 'Đã gửi lệnh tắt hẳn trạm Relay.');
        } catch (error) {
            window.showError(error.message);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="d-flex align-items-center gap-2 ms-2 flex-shrink-0" aria-label="Điều khiển nguồn trạm Relay">
            <span className={`badge ${stationEnabled ? 'bg-success' : 'bg-secondary'}`}>
                {stationEnabled ? 'RELAY BẬT' : 'RELAY ĐÃ TẮT'}
            </span>
            <button
                type="button"
                className={`btn btn-sm py-2 ${stationEnabled ? 'btn-outline-danger' : 'btn-outline-success'}`}
                style={{ minHeight: '44px' }}
                aria-label={stationEnabled ? 'Tắt hẳn trạm Relay' : 'Bật trạm Relay'}
                aria-pressed={stationEnabled}
                disabled={isSubmitting}
                onClick={toggleStation}
            >
                {isSubmitting ? (
                    <><span className="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Đang gửi</>
                ) : (
                    <><i className={`bi ${stationEnabled ? 'bi-power' : 'bi-play-fill'} me-1`} aria-hidden="true"></i>{stationEnabled ? 'Tắt trạm' : 'Bật trạm'}</>
                )}
            </button>
        </div>
    );
});

// =========================================
// === COMPONENT CHÍNH: DetailsPanel ===
// =========================================
const makeEspSetupStep = (label, status, detail = '') => ({label, status, detail});
const offlineStepStatus = (status, offline) => offline ? 'Ngoại tuyến' : status;

function getEspSetupContext(device) {
    const setup = device.esp_setup || {};
    const source = setup.source || {};
    const rover = setup.rover || {};
    const nearest = setup.auto_nearest || {};
    const progress = device.auto_base_progress || {};
    const startup = device.system_info?.base_startup || {};
    const survey = device.system_info?.survey_in_status || {};
    const auth = device.system_info?.auth_status || {};
    const licenseBypass = auth.license_bypass === true || device.system_info?.auth_license_bypass === true;
    const name = device.name || '';
    const actualSource = startup.state === 'awaiting_auto_base' ? ''
        : progress.source_mountpoint || progress.conversion_report?.source?.mountpoint;
    const useSurvey = progress.mode === 'survey_in'
        || ['survey_started', 'survey_resumed'].includes(startup.state)
        || (survey.state && survey.state !== 'not_monitoring');
    const phase = startup.state === 'awaiting_auto_base' ? '' : progress.phase;
    return {source, rover, nearest, progress, startup, survey, auth, licenseBypass, name, actualSource, useSurvey, phase};
}

function getEspHealthStep(device, offline, waiting, ready) {
    let status = waiting;
    if (offline) status = 'Mất kết nối';
    else if (device.timestamp) status = ready;
    return makeEspSetupStep('Health', status,
        offline ? 'Hiển thị trạng thái lần nhận gần nhất.' : 'Backend nhận trạng thái từ ESP32.');
}

function getEspTokenStep(context, offline, tokenConfirmed, waiting, ready) {
    const {auth, licenseBypass} = context;
    let status = offlineStepStatus(waiting, offline);
    let detail = 'Cần đăng ký hoặc kích hoạt thiết bị.';
    if (tokenConfirmed) {
        status = ready;
        detail = 'ESP32 và backend đã xác nhận token.';
    } else if (licenseBypass) {
        status = 'Đã bỏ qua';
        detail = 'Trạm được miễn kiểm tra token.';
    } else if (offline) detail = 'Chưa xác nhận token trước khi mất kết nối.';
    else if (auth.token_deployment_pending) status = 'Đang đồng bộ';
    return makeEspSetupStep('Token thiết bị', status, detail);
}

function getEspSourceStep(context, offline, waiting, retrying) {
    const {source, name} = context;
    let sourceStatus = waiting;
    if (source.state === 'retrying') sourceStatus = retrying;
    else if (['registering', 'waiting_health'].includes(source.state)) sourceStatus = 'Đang đồng bộ';
    const status = source.mountpoint === name && source.state === 'ready'
        ? 'Đã cấu hình'
        : offlineStepStatus(sourceStatus, offline);
    const detail = source.state === 'waiting_config' ? 'Chưa cấu hình caster phát.' : source.detail || name;
    return makeEspSetupStep('Mountpoint phát', status, detail);
}

function getEspRoverStep(context, offline, waiting, ready, retrying) {
    const {rover, name} = context;
    let roverStatus = waiting;
    if (rover.state === 'partial') roverStatus = 'Đồng bộ một phần';
    else if (rover.state === 'retrying') roverStatus = retrying;
    const status = rover.username === name && rover.state === 'ready'
        ? ready
        : offlineStepStatus(roverStatus, offline);
    return makeEspSetupStep('Tài khoản rover', status, name);
}

function getEspNearestStep(context, offline, waiting, ready, retrying) {
    const {actualSource, nearest, startup} = context;
    const detail = [nearest.caster, nearest.requested_mountpoint && ('MP đầu vào ' + nearest.requested_mountpoint), nearest.detail]
        .filter(Boolean).join(' · ');
    if (actualSource) return makeEspSetupStep('AutoNearest', ready, detail);
    const requested = startup.state === 'awaiting_auto_base' || startup.state === 'auto_base_running'
        || !!nearest.state || !!actualSource;
    if (!requested) return null;
    let nearestStatus = waiting;
    if (nearest.state === 'sent') nearestStatus = 'Đã gửi cấu hình';
    else if (nearest.state === 'ready') nearestStatus = 'Đã nạp cấu hình';
    else if (['retrying', 'delivery_retry'].includes(nearest.state)) nearestStatus = retrying;
    const status = offlineStepStatus(nearestStatus, offline);
    return makeEspSetupStep('AutoNearest', status, detail);
}

function getEspBaseSurveyState(context, ready) {
    const {progress, survey} = context;
    const status = survey.state === 'base_ready' ? ready : 'Đang Survey-In';
    const seconds = survey.elapsed_seconds ?? survey.elapsed_since_ack_seconds;
    const details = [];
    if (progress.reason === 'no_fixed_station_within_20_km') details.push('Ngoài vùng trạm cố định 20 km.');
    if (seconds != null && survey.target_seconds) details.push(seconds + '/' + survey.target_seconds + ' giây');
    return {status, detail: details.join(' ')};
}

function getEspBaseFixedState(context, waiting) {
    const {progress, startup, phase, actualSource} = context;
    const phaseStatuses = {
        set_rover: 'Đang vào Rover', wait_gga: 'Đang chờ GGA', connect_caster: 'Đang kết nối caster',
        wait_fix: 'Đang chờ RTK FIX', averaging: 'Đang lấy mẫu', resolving_location: 'Đang xử lý tọa độ',
        ready_for_review: 'Chờ duyệt tọa độ', completed: 'Đã áp dụng Fixed LLA',
    };
    const status = phaseStatuses[phase] || (startup.state === 'fixed_restored' ? 'Đã khôi phục Fixed LLA' : waiting);
    const details = [];
    if (actualSource) details.push('Nguồn RTCM ' + actualSource);
    if (phase === 'averaging' && progress.samples_target) {
        details.push((progress.samples_collected ?? 0) + '/' + progress.samples_target + ' mẫu');
    }
    return {status, detail: details.join(' · ')};
}

function getEspBaseState(context, waiting, ready) {
    const {progress, startup, phase, useSurvey} = context;
    if (phase === 'failed' || startup.state === 'error') {
        return {status: 'Thất bại', detail: progress.reason || startup.error || 'Chưa thiết lập được Base.'};
    }
    if (phase === 'stopped') return {status: 'Đã dừng', detail: ''};
    if (useSurvey) return getEspBaseSurveyState(context, ready);
    return getEspBaseFixedState(context, waiting);
}

function isTerminalEspBaseStatus(status, ready) {
    return ['Thất bại', 'Đã dừng', ready, 'Chờ duyệt tọa độ', 'Đã áp dụng Fixed LLA', 'Đã khôi phục Fixed LLA'].includes(status);
}

function getEspBaseStep(context, offline, waiting, ready) {
    let {status, detail} = getEspBaseState(context, waiting, ready);
    if (offline && !isTerminalEspBaseStatus(status, ready)) {
        if (status !== waiting) detail = [status, detail].filter(Boolean).join(' · ');
        status = 'Ngoại tuyến';
    }
    return makeEspSetupStep('Thiết lập Base', status, detail);
}

function buildEspSetupSteps(device, offline, tokenConfirmed) {
    const context = getEspSetupContext(device);
    const waiting = 'Đang chờ';
    const ready = 'Đã xác nhận';
    const retrying = 'Đang thử lại';
    const steps = [
        getEspHealthStep(device, offline, waiting, ready),
        getEspTokenStep(context, offline, tokenConfirmed, waiting, ready),
        getEspSourceStep(context, offline, waiting, retrying),
        getEspRoverStep(context, offline, waiting, ready, retrying),
    ];
    const nearestStep = getEspNearestStep(context, offline, waiting, ready, retrying);
    if (nearestStep) steps.push(nearestStep);
    steps.push(getEspBaseStep(context, offline, waiting, ready));
    const rtcmStatus = !offline && device.ntrip_connected ? 'Đang phát' : offlineStepStatus(waiting, offline);
    const rtcmDetail = !offline && device.ntrip_connected && context.source.mountpoint === context.name ? context.name : '';
    steps.push(makeEspSetupStep('Phát RTCM', rtcmStatus, rtcmDetail));
    return steps;
}

function getEspSetupStatusClass(status) {
    if (status === 'Thất bại' || status === 'Mất kết nối') return 'text-danger';
    if (status === 'Ngoại tuyến') return 'text-secondary';
    if (['Đã xác nhận', 'Đã cấu hình', 'Đã áp dụng Fixed LLA', 'Đã khôi phục Fixed LLA', 'Đã bỏ qua'].includes(status)) return 'text-success';
    return 'text-info';
}

function EspSetupProgress({ device, offline, tokenConfirmed }) {
    const steps = buildEspSetupSteps(device, offline, tokenConfirmed);
    return (
        <section className="card bg-dark border-secondary mb-3" aria-label="Luồng hoạt động ESP32">
            <div className="card-body p-3">
                <h3 className="h6 mb-3">Luồng hoạt động ESP32</h3>
                <ol className="list-unstyled mb-0" aria-live="polite" aria-atomic="false">
                    {steps.map(({label, status, detail}, index) => (
                        <li key={label} className="d-flex flex-wrap align-items-baseline gap-2 small py-1">
                            <span className="text-muted" aria-hidden="true">{index + 1}.</span>
                            <strong>{label}</strong>
                            <span className={getEspSetupStatusClass(status)}>{status}</span>
                            {detail && <span className="text-muted text-break">{detail}</span>}
                        </li>
                    ))}
                </ol>
            </div>
        </section>
    );
}

function isDetailsTabVisible(tabName, visibility) {
    if (tabName === 'license') return visibility.showLicenseTab;
    if (tabName === 'chip') return visibility.showConfigTabs;
    if (tabName === 'service' || tabName === 'rovers') return visibility.showServiceAndDashboardTabs;
    if (tabName === 'dashboard') return visibility.showOverviewTab;
    if (tabName === 'operations') return !visibility.isDeviceRebooting;
    return false;
}

function getDetailsSpecialTab({isReadonly4g, shouldShowLicenseTab, isDeviceSwitched, activeTab, lifecycleStatus}) {
    if (isReadonly4g) return {tab:'dashboard'};
    if (shouldShowLicenseTab) {
        return !isDeviceSwitched && activeTab === 'license' ? {skip:true} : {tab:'license'};
    }
    if (lifecycleStatus === 'unprovisioned'
        || ['auto_setup_rover','auto_setup_wait_fix','auto_setup_averaging','auto_setup_base'].includes(lifecycleStatus)) {
        return {tab:'chip'};
    }
    return null;
}

function getDetailsDefaultTab({isGnssConfigured, isServiceConfigured, visibility}) {
    if (!isGnssConfigured) return 'chip';
    if (!isServiceConfigured) return 'service';
    if (visibility.showOverviewTab) return 'dashboard';
    if (visibility.showServiceAndDashboardTabs) return 'service';
    if (visibility.showConfigTabs) return 'chip';
    if (visibility.showLicenseTab) return 'license';
    return 'operations';
}

function chooseDetailsTab(context) {
    const specialTab = getDetailsSpecialTab(context);
    if (specialTab) return specialTab;
    const {isDeviceSwitched, activeTab, wasGnssConfigured, isGnssConfigured, userSelectedTab, visibility} = context;
    if (!isDeviceSwitched && activeTab) {
        if (activeTab === 'chip' && !wasGnssConfigured && isGnssConfigured) return {tab:'service'};
        if (isDetailsTabVisible(activeTab, visibility)) return {skip:true};
    }
    if (!isDeviceSwitched && userSelectedTab && activeTab && isDetailsTabVisible(activeTab, visibility)) {
        return {skip:true};
    }
    return {tab:getDetailsDefaultTab(context)};
}

function missingDetailsPanel(name) {
    return <div className="alert alert-warning small mb-0">
        <i className="bi bi-exclamation-triangle-fill me-2"></i>
        Dashboard module <strong>{name}</strong> chưa sẵn sàng. Vui lòng refresh lại trang.
    </div>;
}

function renderRequiredDetailsPanel(Component, name, props) {
    return Component ? <Component {...props} /> : missingDetailsPanel(name);
}

function OfflineStationPanel({offlineInfo}) {
    if (!offlineInfo?.sinceSeconds) return <div className="small text-white-50">
        The start time for this offline period was not saved. The next transition to offline will be recorded accurately.
    </div>;
    return <>
        <div className="small text-white-50">
            <i className="bi bi-clock-history me-1" aria-hidden="true"></i>
            <span>Offline since</span>{': '}
            <time dateTime={new Date(offlineInfo.sinceSeconds * 1000).toISOString()}>{offlineInfo.sinceText}</time>
        </div>
        <div className="fs-5 fw-semibold mt-1"><span>Duration</span>{': '}{offlineInfo.durationText}</div>
    </>;
}

function OfflineDashboardDetails({offlineInfo}) {
    return <div className="card bg-dark border-danger" role="status">
        <div className="card-body d-flex align-items-center gap-3 p-4">
            <div className="rounded-circle bg-danger bg-opacity-10 text-danger d-flex align-items-center justify-content-center flex-shrink-0" style={{width:56,height:56}}>
                <i className="bi bi-wifi-off fs-3" aria-hidden="true"></i>
            </div>
            <div className="min-w-0">
                <div className="d-flex align-items-center gap-2 mb-2"><span className="badge bg-danger">OFFLINE</span><strong>Station is offline</strong></div>
                <OfflineStationPanel offlineInfo={offlineInfo} />
            </div>
        </div>
    </div>;
}

function DetailsPanelTabContent({device, currentUser, activeTab, isReadonly4g, canEdit, canEditService, canManageRovers, isAdmin, staleOffline, offlineInfo, nmeaData, handlers}) {
    const forms = window.CorsDashboardForms || {};
    const LicenseForm = forms.LicenseForm || window.LicenseForm;
    const ProvisionForm = forms.ProvisionForm || window.ProvisionForm;
    const BaseConfigForm = forms.BaseConfigForm || window.BaseConfigForm;
    const ServiceConfigForm = forms.ServiceConfigForm || window.ServiceConfigForm;
    const commonProps = {device,currentUser,disabled:!canEdit};
    switch (isReadonly4g ? 'dashboard' : activeTab) {
        case 'license':
            return renderRequiredDetailsPanel(LicenseForm,'LicenseForm',{...commonProps,onDeployLicense:handlers.onDeployLicense});
        case 'chip':
            return device.status === 'unprovisioned'
                ? renderRequiredDetailsPanel(ProvisionForm,'ProvisionForm',{...commonProps,onProvision:handlers.onProvision})
                : renderRequiredDetailsPanel(BaseConfigForm,'BaseConfigForm',{...commonProps,onConfigureChip:handlers.onConfigureChip,onStopAutoBase:handlers.onStopAutoBase,onReadBaseStatus:handlers.onReadBaseStatus});
        case 'service':
            return renderRequiredDetailsPanel(ServiceConfigForm,'ServiceConfigForm',{...commonProps,disabled:!canEditService,onConfigureService:handlers.onConfigureService});
        case 'dashboard':
            if (staleOffline) return <OfflineDashboardDetails offlineInfo={offlineInfo} />;
            if (!window.DashboardPanel) return missingDetailsPanel('DashboardPanel');
            return <window.DashboardPanel device={device} nmeaData={nmeaData} />;
        case 'rovers':
            return canManageRovers ? <RoverManagementPanel device={device} /> : <div className="alert alert-danger">Bạn không có quyền truy cập mục này.</div>;
        case 'operations':
            return <StationOperationsPanel device={device} canEditService={canEditService} canRunDeviceActions={isAdmin}
                onLock={handlers.onLock} onUnlock={handlers.onUnlock} onReset={handlers.onReset} />;
        default:
            return null;
    }
}

function DetailsPanelName({device, role, canEdit, isEditingName, setIsEditingName, tempName, setTempName, nameInputRef, onSave}) {
    if (isEditingName) return (
        <div className="input-group input-group-sm me-3" style={{maxWidth: 'min(300px, 100%)'}}>
            <input type="text" className="form-control" value={tempName}
                onChange={event => setTempName(event.target.value)}
                onKeyDown={event => {
                    if (event.key === 'Enter') onSave();
                    if (event.key === 'Escape') setIsEditingName(false);
                }}
                ref={nameInputRef} aria-label="Tên trạm" />
            <button className="btn btn-success" onClick={onSave} title="Lưu tên mới"><i className="bi bi-check"></i></button>
            <button className="btn btn-secondary" onClick={() => { setTempName(device.name); setIsEditingName(false); }} title="Hủy">
                <i className="bi bi-x"></i>
            </button>
        </div>
    );
    return (
        <div className="d-flex align-items-center me-3">
            <h4 className="mb-0 me-2 text-truncate" style={{maxWidth: 'min(350px, calc(100vw - 120px))'}} title={device.name}>{device.name}</h4>
            {role === 'coordinator' && !canEdit && <span className="badge bg-secondary">Chỉ xem</span>}
            {canEdit && <button className="btn btn-link text-muted p-0 ms-1"
                onClick={() => { setTempName(device.name); setIsEditingName(true); }}
                title="Đổi tên trạm" style={{fontSize: '1.1rem'}}>
                <i className="bi bi-pencil-square"></i>
            </button>}
        </div>
    );
}

function DetailsPanelStatusBadges({device, isReadonly4g, canEdit, displayStatusKey, staleOffline, lifecycleStatus, isDeviceLocked, lockedServers, lockApplied}) {
    const syncErrorText = device.sync_errors ? device.sync_errors.join('\n') : 'Đang chờ dữ liệu...';
    const lockAppliedSuffix = lockApplied ? '' : ' · Chờ áp dụng';
    const lockedServerLabel = lockedServers.length
        ? `Khoá ${lockedServers.map(serverId => `S${serverId}`).join(', ')}${lockAppliedSuffix}`
        : 'Khoá toàn trạm';
    return <div className="d-flex align-items-center">
        {(isReadonly4g || device.is_lte) && <span className="badge bg-primary me-2" title="Trạm kết nối qua 4G/LTE">
            <i className="bi bi-reception-4 me-1"></i>{isReadonly4g ? 'Base 4G · Chỉ xem' : 'LTE'}
        </span>}
        {!device.is_synced && canEdit && <div className="me-2 position-relative group-hover-target">
            <button className="btn btn-warning btn-sm text-dark fw-bold"
                onClick={() => window.CorsUI.showToast('warning', syncErrorText)}
                title={device.sync_errors ? device.sync_errors.join('\n') : 'Click để xem chi tiết'}>
                <i className="bi bi-exclamation-triangle-fill me-1"></i>Không đồng bộ (Click xem)
            </button>
        </div>}
        <div className="d-flex align-items-center gap-1">
            <span className={`badge ${STATUS_CLASS_MAP[displayStatusKey] || 'bg-secondary'}`}>
                {STATUS_TEXT_MAP[displayStatusKey] || displayStatusKey}
            </span>
            {staleOffline && lifecycleStatus !== 'offline' && <span className="badge bg-secondary" title="Trạng thái cuối cùng của agent; dữ liệu GNSS hiện không còn trực tiếp.">
                Last: {STATUS_TEXT_MAP[lifecycleStatus] || lifecycleStatus.toUpperCase()}
            </span>}
            {device.is_synced
                ? <i className="bi bi-check-circle-fill text-success" title="Đã đồng bộ" style={{fontSize: '1rem'}}></i>
                : <i className="bi bi-exclamation-triangle-fill text-warning"
                    title={device.sync_errors && device.sync_errors.length > 0 ? device.sync_errors.join('\n') : 'Không đồng bộ'}
                    style={{fontSize: '1rem'}}></i>}
        </div>
        {isDeviceLocked && <span className="badge bg-danger ms-2">
            <i className="bi bi-lock-fill me-1"></i>{lockedServerLabel}
        </span>}
    </div>;
}

async function rebootStation(device, onReset) {
    const confirmed = await window.CorsUI.confirmAction({
        title: 'Khởi động lại trạm',
        message: `Bạn có chắc muốn khởi động lại nguồn trạm ${device.name || device.serial}?`,
        confirmText: 'Khởi động lại',
        tone: 'danger'
    });
    if (confirmed) onReset(device.serial);
}

async function factoryResetStation(device) {
    const confirmed = await window.CorsUI.confirmAction({
        title: 'Khôi phục cài đặt gốc',
        message: `Bạn có chắc muốn khôi phục cài đặt gốc và xóa mọi cấu hình của trạm ${device.name || device.serial} không?`,
        confirmText: 'Khôi phục',
        tone: 'danger'
    });
    if (!confirmed) return;
    try {
        await window.CorsDeviceAPI.hardReset(device.serial);
        window.showSuccess('Đã gửi lệnh khôi phục cài đặt gốc đến thiết bị.');
    } catch (error) {
        window.showError(`Không thể gửi lệnh khôi phục: ${error.message}`);
    }
}

function DetailsPanelActions({device, canEdit, isAdmin, isEditingName, onReset}) {
    return <>
        {canEdit && !isEditingName && <button className="btn btn-sm btn-outline-danger ms-2"
            title="Khởi động lại nguồn trạm" onClick={() => rebootStation(device, onReset)}>
            <i className="bi bi-power"></i> Khởi động lại
        </button>}
        {isAdmin && !isEditingName && <button className="btn btn-sm btn-outline-danger ms-2"
            title="Khôi phục cài đặt gốc" onClick={() => factoryResetStation(device)}>
            <i className="bi bi-eraser-fill"></i> Reset Mới Cứng
        </button>}
    </>;
}

function DetailsPanelHeader(props) {
    const {device, currentUser, isAdmin, isReadonly4g, role, canEdit, isEditingName, setIsEditingName,
        tempName, setTempName, nameInputRef, onSaveName,
        displayStatusKey, staleOffline, lifecycleStatus, isDeviceLocked, lockedServers, lockApplied, onReset} = props;
    return <div className="card-header dashboard-detail-header d-flex justify-content-between align-items-center bg-dark flex-shrink-0">
        <div className="d-flex align-items-center flex-grow-1">
            <DetailsPanelName device={device} role={role} canEdit={canEdit} isEditingName={isEditingName}
                setIsEditingName={setIsEditingName} tempName={tempName} setTempName={setTempName}
                nameInputRef={nameInputRef} onSave={onSaveName} />
            <DetailsPanelStatusBadges device={device} isReadonly4g={isReadonly4g} canEdit={canEdit}
                displayStatusKey={displayStatusKey} staleOffline={staleOffline} lifecycleStatus={lifecycleStatus}
                isDeviceLocked={isDeviceLocked} lockedServers={lockedServers} lockApplied={lockApplied} />
        </div>
        {!isReadonly4g && <ConfiguredRelayPowerControl device={device} currentUser={currentUser} />}
        <DetailsPanelActions device={device} canEdit={canEdit} isAdmin={isAdmin} isEditingName={isEditingName} onReset={onReset} />
    </div>;
}

function DetailsPanelNotices({device, staleOffline, tokenConfirmed, rawStatusKey, activeTab, isReadonly4g}) {
    const configStatus = device.system_info?.config_status;
    let configStatusIcon = <i className="bi bi-info-circle-fill me-2 fs-5"></i>;
    if (configStatus?.state === 'executing') configStatusIcon = <div className="spinner-border spinner-border-sm me-2" role="status"></div>;
    else if (configStatus?.state === 'error') configStatusIcon = <i className="bi bi-x-circle-fill me-2 fs-5"></i>;
    return <>
        {/^esp_/i.test(String(device.serial || '').trim()) && <EspSetupProgress device={device} offline={staleOffline} tokenConfirmed={tokenConfirmed} />}
        {configStatus && !['done', 'success', 'idle'].includes(configStatus.state) && (
            <div className={'alert ' + (configStatus.state === 'error' ? 'alert-danger' : 'alert-info') + ' mb-3'}>
                <div className="d-flex align-items-center">
                    {configStatusIcon}
                    <div>
                        <strong>Trạng thái cấu hình thiết bị:</strong> {configStatus.progress}
                        {configStatus.error && <div className="small mt-1 text-danger">Lỗi: {configStatus.error}</div>}
                    </div>
                </div>
            </div>
        )}
        {rawStatusKey === 'offline' && !(activeTab === 'dashboard' && staleOffline) && (
            <div className="alert alert-warning py-2 px-3 small mb-3" role="status">
                <i className="bi bi-broadcast-pin me-2"></i>
                GNSS/RTCM hiện không có dữ liệu trực tiếp. Độ chính xác chỉ được xác nhận lại khi trạm kết nối trở lại.
            </div>
        )}
        {isReadonly4g && device.system_info?.decoder_status === 'missing_rover_credentials' && (
            <div className="alert alert-warning small" role="status">
                Chưa có tài khoản Rover hợp lệ trùng mountpoint. Kiểm tra tài khoản trong trang Rover.
            </div>
        )}
    </>;
}

function requestStationSerialRename(device) {
    const newSerial = prompt('Nhập Serial mới cho trạm LTE này:', device.serial);
    if (newSerial?.trim() && newSerial.trim() !== device.serial) {
        void window.handleRenameSerial(device.serial, newSerial.trim());
    }
}

function DetailsPanelMetadata({device, isAdmin}) {
    const supportsSerialRename = device.serial.includes('_LTE_') || device.serial.includes('LTE_') || device.serial.startsWith('MQTT_');
    return <div className="dashboard-detail-meta d-flex justify-content-between align-items-center mb-3">
        {isAdmin ? <p className="text-white-50 small mb-0 d-flex align-items-center">
            Serial: {device.serial}
            {supportsSerialRename && <button className="btn btn-link text-warning p-0 ms-2" title="Chỉnh sửa Serial" onClick={() => requestStationSerialRename(device)}>
                <i className="bi bi-pencil-square"></i>
            </button>}
        </p> : <div></div>}
        <div className="d-flex flex-wrap align-items-center gap-2">
            <span className="badge bg-dark-subtle text-light border border-secondary text-nowrap">
                {/^esp_/i.test(device.serial) ? 'Firmware' : 'Agent'}: {device.version || 'Chưa rõ phiên bản'}
            </span>
            <span className="badge bg-dark-subtle text-light border border-secondary">
                {device.detected_chip_type || 'UNKNOWN'}
            </span>
        </div>
    </div>;
}

function DetailsPanelTabs(props) {
    const {device, currentUser, activeTab, isReadonly4g, canEdit, canEditService, canManageRovers,
        isAdmin, staleOffline, offlineInfo, nmeaData, showLicenseTab, showConfigTabs,
        showServiceAndDashboardTabs, showStationOperationsTab, showOverviewTab, onTabClick, handlers} = props;
    const tab = (name, icon, label) => <li className="nav-item">
        <button className={'nav-link ' + (activeTab === name ? 'active' : '')} onClick={() => onTabClick(name)}>
            <i className={'bi ' + icon + ' me-1'}></i>{label}
        </button>
    </li>;
    return <>
        <ul className="nav nav-tabs dashboard-tabs mb-3" role="tablist">
            {showLicenseTab && tab('license', 'bi-key-fill', 'License')}
            {showConfigTabs && tab('chip', 'bi-cpu-fill', 'Kích hoạt & GNSS')}
            {showServiceAndDashboardTabs && tab('service', 'bi-hdd-network-fill', 'Dịch vụ')}
            {showServiceAndDashboardTabs && canManageRovers && tab('rovers', 'bi-person-circle', 'Quản lý Rover')}
            {showStationOperationsTab && tab('operations', 'bi-activity', 'Operations')}
            {showOverviewTab && tab('dashboard', 'bi-speedometer2', 'Overview')}
        </ul>
        <div className="tab-content dashboard-tab-content pt-3">
            <DetailsPanelTabContent device={device} currentUser={currentUser} activeTab={activeTab}
                isReadonly4g={isReadonly4g} canEdit={canEdit} canEditService={canEditService}
                canManageRovers={canManageRovers} isAdmin={isAdmin} staleOffline={staleOffline}
                offlineInfo={offlineInfo} nmeaData={nmeaData} handlers={handlers} />
        </div>
    </>;
}

function DetailsPanelFooter({device, canEdit, isAdmin, isDeviceLocked, onLock}) {
    if (!canEdit) return null;
    return <div className="card-footer dashboard-detail-footer bg-dark border-top border-secondary p-3 mt-auto">
        {isAdmin ? <div id="admin-actions-section">
            <div className="d-flex align-items-center justify-content-between">
                <span className="text-muted small">
                    <i className="bi bi-shield-lock-fill me-2 text-danger"></i>
                    Admin Control (Chỉ Admin)
                </span>
                <div style={{width: '60%'}}>
                    <button onClick={() => onLock(device.serial)} className="btn btn-warning w-100 text-dark fw-bold">
                        <i className="bi bi-shield-lock me-2"></i>{isDeviceLocked ? 'Sửa khoá host / server' : 'Khoá host / server'}
                    </button>
                </div>
            </div>
        </div> : <div className="text-center text-muted small">
            <i className="bi bi-info-circle me-1"></i>
            Bạn có quyền quản lý trạm này.
        </div>}
    </div>;
}

function getDetailsPanelAccess(device, currentUser) {
    const isReadonly4g = window.CorsDashboardShared?.isReadonly4gBase?.(device) === true;
    const authStatus = device?.system_info?.auth_status || {};
    return {
        isReadonly4g,
        isAdmin: !isReadonly4g && currentUser?.role === 'admin',
        tokenConfirmed: window.CorsDashboardShared?.isAccessTokenSynced?.(device) === true,
        authStatus,
        licenseBypass: isReadonly4g || authStatus.license_bypass === true || device?.system_info?.auth_license_bypass === true,
        serverTokenPresent: Object.prototype.hasOwnProperty.call(authStatus, 'server_token_present')
            ? authStatus.server_token_present : authStatus.token_required,
        tokenCheckAvailable: authStatus.token_check_available !== false,
        hasAuthTokenState: [
            'server_token_present', 'token_required', 'token_synced', 'agent_token_present',
            'needs_token_deploy', 'token_deployment_pending',
        ].some(key => Object.prototype.hasOwnProperty.call(authStatus, key)),
    };
}

function getDetailsPanelLifecycle(device, isReadonly4g, tokenConfirmed) {
    const rawLifecycle = String(device?.status || 'offline').toLowerCase();
    const reportedLifecycle = isReadonly4g && ['awaiting_license', 'unprovisioned', 'locked'].includes(rawLifecycle)
        ? 'offline' : rawLifecycle;
    const lifecycleStatus = tokenConfirmed && reportedLifecycle === 'awaiting_license'
        ? (window.CorsDashboardShared?.getMapEffectiveStatus?.(device) || 'awaiting_agent')
        : reportedLifecycle;
    return {
        lifecycleStatus,
        isDeviceRebooting: !isReadonly4g && ['rebooting', 'rebooting_for_reset'].includes(lifecycleStatus),
        isDeviceAwaitingLicense: lifecycleStatus === 'awaiting_license',
        isDeviceUnprovisioned: lifecycleStatus === 'unprovisioned',
        isRelayLikeStation: String(device?.serial || '').toUpperCase().startsWith('MONITOR_'),
    };
}

function hasDetailsPanelAccessTokenGap(access, staleOffline) {
    const { isReadonly4g, tokenConfirmed, authStatus, serverTokenPresent, tokenCheckAvailable } = access;
    if (isReadonly4g || tokenConfirmed) return false;
    if (authStatus.token_deployment_pending === true) return true;
    if (staleOffline) return serverTokenPresent === false;
    return authStatus.needs_token_deploy === true || serverTokenPresent === false
        || (tokenCheckAvailable && authStatus.agent_token_present === false)
        || (tokenCheckAvailable && authStatus.token_synced === false);
}

function getDetailsPanelConfig(device) {
    const isGnssConfigured = !!(device?.base_config && typeof device.base_config === 'object' && Object.keys(device.base_config).length);
    const isServiceConfigured = !!(device?.service_config && typeof device.service_config === 'object' && Object.keys(device.service_config).length);
    const storedCoords = device?.base_config?.coords || device?.base_coords || {};
    return {
        isGnssConfigured,
        isServiceConfigured,
        baseConfigSignature: isGnssConfigured ? JSON.stringify(device.base_config) : '',
        serviceConfigSignature: isServiceConfigured ? JSON.stringify(device.service_config) : '',
        stationHasConfiguration: isGnssConfigured || isServiceConfigured || (
            Number.isFinite(Number(storedCoords.lat)) && Number(storedCoords.lat) !== 0
            && Number.isFinite(Number(storedCoords.lon)) && Number(storedCoords.lon) !== 0
        ),
    };
}

function getDetailsPanelVisibility({
    isReadonly4g, licenseBypass, hasAuthTokenState, hasAccessTokenGap, isDeviceAwaitingLicense, isDeviceRebooting,
    isDeviceUnprovisioned, isRelayLikeStation, stationHasConfiguration,
}) {
    const shouldShowLicenseTab = !licenseBypass && (hasAccessTokenGap || isDeviceAwaitingLicense);
    const showLicenseTab = !isReadonly4g && (shouldShowLicenseTab
        || (licenseBypass && hasAuthTokenState && hasAccessTokenGap));
    const showConfigTabs = !isReadonly4g && !isDeviceRebooting
        && (!isDeviceAwaitingLicense || licenseBypass || stationHasConfiguration);
    const showServiceAndDashboardTabs = showConfigTabs && (!isDeviceUnprovisioned || isRelayLikeStation);
    return {
        shouldShowLicenseTab,
        showLicenseTab,
        showConfigTabs,
        showServiceAndDashboardTabs,
        showOverviewTab: isReadonly4g || showServiceAndDashboardTabs,
    };
}

function getDetailsPanelDisplayStatus(device, lifecycleStatus, staleOffline, hasAccessTokenGap) {
    const rawStatusKey = staleOffline ? 'offline' : lifecycleStatus;
    const operationalStatus = ['online', 'sleep', 'streaming', 'starting'].includes(rawStatusKey)
        ? (window.CorsDashboardShared?.getMapEffectiveStatus?.({ ...device, status: rawStatusKey }) || rawStatusKey)
        : rawStatusKey;
    return {
        rawStatusKey,
        displayStatusKey: rawStatusKey !== 'offline' && (hasAccessTokenGap || rawStatusKey === 'awaiting_license')
            ? 'awaiting_license' : operationalStatus,
    };
}

const DetailsPanel = memo(function DetailsPanel({
    device,
    currentUser,
    nmeaData,
    onReset,
    onProvision,
    onConfigureChip,
    onStopAutoBase,
    onReadBaseStatus,
    onDeployLicense,
    onConfigureService,
    onLock,
    onUnlock
}) {
    const access = getDetailsPanelAccess(device, currentUser);
    const { isReadonly4g, isAdmin, tokenConfirmed, licenseBypass, hasAuthTokenState } = access;
    const { lifecycleStatus, isDeviceRebooting, isDeviceAwaitingLicense, isDeviceUnprovisioned, isRelayLikeStation } =
        getDetailsPanelLifecycle(device, isReadonly4g, tokenConfirmed);
    const [activeTab, setActiveTab] = useState('');
    const [offlineClockNow, setOfflineClockNow] = useState(Date.now());

    const [isEditingName, setIsEditingName] = useState(false);
    const [tempName, setTempName] = useState('');
    const nameInputRef = useRef(null);

    useEffect(() => {
        if (isEditingName) nameInputRef.current?.focus();
    }, [isEditingName]);
    const [userSelectedTab, setUserSelectedTab] = useState(false);
    const [isSystemPanelOpen, setIsSystemPanelOpen] = useState(false);
    const lastSerialRef = useRef(null);
    const lastGnssConfiguredRef = useRef(false);

    const { isGnssConfigured, isServiceConfigured, baseConfigSignature, serviceConfigSignature, stationHasConfiguration } =
        getDetailsPanelConfig(device);
    const staleOffline = isDeviceStaleOffline(device, offlineClockNow);
    const offlineInfo = window.CorsDashboardShared?.getOfflineInfo?.(device, offlineClockNow) || null;
    const hasAccessTokenGap = hasDetailsPanelAccessTokenGap(access, staleOffline);

    useEffect(() => {
        if (!device?.serial) return undefined;
        const interval = setInterval(() => setOfflineClockNow(Date.now()), 30000);
        return () => clearInterval(interval);
    }, [device?.serial]);
    const { rawStatusKey, displayStatusKey } = getDetailsPanelDisplayStatus(
        device, lifecycleStatus, staleOffline, hasAccessTokenGap);
    const {
        shouldShowLicenseTab, showLicenseTab, showConfigTabs, showServiceAndDashboardTabs, showOverviewTab,
    } = getDetailsPanelVisibility({
        isReadonly4g, licenseBypass, hasAuthTokenState, hasAccessTokenGap, isDeviceAwaitingLicense, isDeviceRebooting,
        isDeviceUnprovisioned, isRelayLikeStation, stationHasConfiguration,
    });

    useEffect(() => {
        if (!device) return;

        const isDeviceSwitched = lastSerialRef.current !== device.serial;
        const wasGnssConfigured = isDeviceSwitched ? false : lastGnssConfiguredRef.current;
        lastSerialRef.current = device.serial;
        lastGnssConfiguredRef.current = isGnssConfigured;

        let newActiveTab = '';

        if (isReadonly4g) {
            setActiveTab('dashboard');
            setIsEditingName(false);
            return;
        }
        const selection = chooseDetailsTab({
            isReadonly4g, shouldShowLicenseTab, isDeviceSwitched, activeTab,
            lifecycleStatus, wasGnssConfigured, isGnssConfigured, isServiceConfigured, userSelectedTab,
            visibility:{showLicenseTab,showConfigTabs,showServiceAndDashboardTabs,showOverviewTab,isDeviceRebooting}
        });
        if (selection.skip) return;
        newActiveTab = selection.tab;

        if (newActiveTab) {
            setActiveTab(newActiveTab);
            setUserSelectedTab(false); // Reset flag
        }

        setTempName(device.name || '');
        setIsEditingName(false);

    }, [device?.serial, device?.status, baseConfigSignature, serviceConfigSignature, shouldShowLicenseTab, showLicenseTab, showConfigTabs, showServiceAndDashboardTabs, showOverviewTab]);

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
            void window.handleRenameDevice(device.serial, tempName);
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
    const canEdit = !isReadonly4g && (role === 'admin' ||
        (role === 'coordinator' && device.user_id === currentUser?.id));
    const canEditService = canEdit;
    // REBOOTING STATE
    if (isDeviceRebooting) {
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

    const canManageRovers = device && (currentUser?.role === 'admin' || (currentUser?.role === 'coordinator' && currentUser?.can_view_rovers && currentUser?.can_create_rovers));
    const showStationOperationsTab = !isReadonly4g && !isDeviceRebooting && (isAdmin || canEditService);

    const lockedServers = window.CorsDashboardShared.getLockedServers(device);
    const lockApplied = window.CorsDashboardShared.isDeviceLockApplied(device);
    const isDeviceLocked = window.CorsDashboardShared.getLockedServers(device).length > 0 || device.is_locked || device.status === 'locked';

    return (
        <div className="card dashboard-detail-card bg-dark-tertiary h-100 d-flex flex-column" style={{ position: 'relative', overflowX: 'hidden' }}>
            {isAdmin && device?.system_info && (
                <button
                    className="btn-system-toggle"
                    onClick={() => setIsSystemPanelOpen(!isSystemPanelOpen)}
                    title="System Monitor"
                >
                    <i className="bi bi-cpu"></i>
                </button>
            )}
            {/* CARD-HEADER */}
            <DetailsPanelHeader
                device={device}
                currentUser={currentUser}
                isAdmin={isAdmin}
                isReadonly4g={isReadonly4g}
                role={role}
                canEdit={canEdit}
                isEditingName={isEditingName}
                setIsEditingName={setIsEditingName}
                tempName={tempName}
                setTempName={setTempName}
                nameInputRef={nameInputRef}
                onSaveName={handleSaveName}
                displayStatusKey={displayStatusKey}
                staleOffline={staleOffline}
                lifecycleStatus={lifecycleStatus}
                isDeviceLocked={isDeviceLocked}
                lockedServers={lockedServers}
                lockApplied={lockApplied}
                onReset={onReset}
            />
            <div className="details-panel-scroll dashboard-detail-body card-body overflow-auto flex-grow-1 p-3">
                <DetailsPanelNotices device={device} staleOffline={staleOffline} tokenConfirmed={tokenConfirmed}
                    rawStatusKey={rawStatusKey} activeTab={activeTab} isReadonly4g={isReadonly4g} />
                <DetailsPanelMetadata device={device} isAdmin={isAdmin} />
                <DetailsPanelTabs
                    device={device}
                    currentUser={currentUser}
                    activeTab={activeTab}
                    isReadonly4g={isReadonly4g}
                    canEdit={canEdit}
                    canEditService={canEditService}
                    canManageRovers={canManageRovers}
                    isAdmin={isAdmin}
                    staleOffline={staleOffline}
                    offlineInfo={offlineInfo}
                    nmeaData={nmeaData}
                    showLicenseTab={showLicenseTab}
                    showConfigTabs={showConfigTabs}
                    showServiceAndDashboardTabs={showServiceAndDashboardTabs}
                    showStationOperationsTab={showStationOperationsTab}
                    showOverviewTab={showOverviewTab}
                    onTabClick={handleTabClick}
                    handlers={{onDeployLicense,onProvision,onConfigureChip,onStopAutoBase,onReadBaseStatus,onConfigureService,onLock,onUnlock,onReset}}
                />
            </div>
            <DetailsPanelFooter device={device} canEdit={canEdit} isAdmin={isAdmin}
                isDeviceLocked={isDeviceLocked} onLock={onLock} />
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
