(() => {
const { useState, memo } = React;

const getLicenseStatusMessage = (authStatus, serverTokenPresent, agentTokenPresent, tokenSynced) => {
    if (authStatus.token_deployment_pending === true) {
        return 'Token đã tạo trên server; lệnh cấp token đã gửi. Đang chờ agent báo fingerprint khớp để xác nhận đồng bộ.';
    }
    if (serverTokenPresent === false) {
        if (agentTokenPresent) {
            return 'Agent đã báo có Access Token cục bộ, nhưng server chưa có token để đối chiếu. Bấm nút bên dưới để tạo token trên server và gửi lại xuống agent qua MQTT.';
        }
        return 'Thiết bị chưa có Access Token trên server. Bấm nút bên dưới để tạo token và gửi xuống agent qua MQTT.';
    }
    if (tokenSynced) return 'Access Token đã đồng bộ với agent. Bạn có thể gửi lại token nếu cần.';
    if (agentTokenPresent) return 'Access Token trên agent không khớp với server. Bấm nút bên dưới để gửi lại token đúng.';
    return 'Agent chưa có Access Token. Bấm nút bên dưới để gửi token xuống thiết bị qua MQTT.';
};
const LicenseForm = memo(function LicenseForm({ device, currentUser = {}, onDeployLicense, disabled }) {
    const [isDeploying, setIsDeploying] = useState(false);
    const [isUpdatingBypass, setIsUpdatingBypass] = useState(false);
    const authStatus = device?.system_info?.auth_status || {};
    const licenseBypass = authStatus.license_bypass === true || device?.system_info?.auth_license_bypass === true;
    const canManageLicense = currentUser?.role === 'admin';
    const serverTokenPresent = Object.prototype.hasOwnProperty.call(authStatus, 'server_token_present')
        ? authStatus.server_token_present
        : authStatus.token_required;
    const agentTokenPresent = authStatus.agent_token_present === true;
    const tokenSynced = window.CorsDashboardShared?.isAccessTokenSynced?.(device) === true;
    const authMessage = getLicenseStatusMessage(authStatus, serverTokenPresent, agentTokenPresent, tokenSynced);

    const handleAutoDeploy = async (e) => {
        e.preventDefault();
        if (!canManageLicense || disabled) return;
        setIsDeploying(true);
        try {
            const response = await window.apiFetch(window.ApiRoutes.devices.autoDeployToken(device.serial), {
                method: 'POST'
            });
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'Failed to auto-deploy token');
            }
            window.showSuccess('Token đã tạo và lệnh cấp token đã gửi. Đang chờ agent xác nhận đồng bộ.');
        } catch (error) {
            window.showError('Không thể cấp token tự động: ' + error.message);
        } finally {
            setIsDeploying(false);
        }
    };

    const handleToggleBypass = async () => {
        if (!canManageLicense || isUpdatingBypass) return;
        const nextEnabled = !licenseBypass;
        const ok = await window.CorsUI.confirmAction({
            title: nextEnabled ? 'Bypass license' : 'Disable license bypass',
            message: nextEnabled
                ? `Cho phép trạm ${device.name || device.serial} bỏ qua license để rescue/update? Chỉ dùng tạm thời.`
                : `Tắt bypass license cho trạm ${device.name || device.serial}?`,
            confirmText: nextEnabled ? 'Enable bypass' : 'Disable bypass',
            tone: nextEnabled ? 'warning' : 'primary'
        });
        if (!ok) return;
        setIsUpdatingBypass(true);
        try {
            const response = await window.apiFetch(window.ApiRoutes.devices.licenseBypass(device.serial), {
                method: 'POST',
                body: JSON.stringify({ enabled: nextEnabled })
            });
            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to update license bypass');
            }
            window.showSuccess(nextEnabled ? 'Đã bật bypass license cho trạm.' : 'Đã tắt bypass license cho trạm.');
            setTimeout(() => window.location.reload(), 500);
        } catch (error) {
            window.showError('Không thể cập nhật bypass license: ' + error.message);
        } finally {
            setIsUpdatingBypass(false);
        }
    };

    return (
        <form onSubmit={handleAutoDeploy}>
            <div className="alert alert-warning small">
                <i className="bi bi-key-fill me-2"></i>
                {canManageLicense ? authMessage : 'Trạm này đang cần Access Token để mở khóa. Vui lòng liên hệ admin để được cấp token và mở khóa trạm.'}
            </div>
            <div className="mb-3">
                <label htmlFor="pi-license-serial" className="form-label">Serial Number của Pi</label>
                <div className="input-group">
                    <input id="pi-license-serial" type="text" className="form-control" value={device.serial} readOnly />
                    <button
                        className="btn btn-outline-secondary"
                        type="button"
                        title="Sao chép Serial"
                        onClick={() => {
                            navigator.clipboard.writeText(device.serial).then(
                                () => window.showSuccess('Đã sao chép Serial!'),
                                () => window.showError?.("Không thể sao chép vào bộ nhớ tạm.")
                            );
                        }}
                    >
                        <i className="bi bi-clipboard"></i>
                    </button>
                </div>
            </div>
            
            {canManageLicense && (
            <button type="submit" className="btn btn-primary w-100 py-3 mt-2 fw-bold" disabled={isDeploying || disabled}>
                {isDeploying ? (
                    <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang cấp phát tự động...
                    </>
                ) : (
                    <>
                        <i className="bi bi-magic me-2"></i>
                        Tự động cấp & gửi Token
                    </>
                )}
            </button>
            )}
            {canManageLicense && (
                <button
                    type="button"
                    className={`btn ${licenseBypass ? 'btn-outline-success' : 'btn-outline-warning'} w-100 py-2 mt-2 fw-bold`}
                    disabled={isUpdatingBypass}
                    onClick={handleToggleBypass}
                >
                    {isUpdatingBypass && <>
                        <span className="spinner-border spinner-border-sm me-2"></span>
                        Đang cập nhật bypass...
                    </>}
                    {!isUpdatingBypass && licenseBypass && <>
                        <i className="bi bi-shield-check me-2"></i>
                        Tắt bypass license
                    </>}
                    {!isUpdatingBypass && !licenseBypass && <>
                        <i className="bi bi-shield-exclamation me-2"></i>
                        Bỏ qua license cho trạm này
                    </>}
                </button>
            )}
        </form>
    );
});

const ProvisionForm = memo(function ProvisionForm({ device, currentUser = {}, onProvision, disabled }) {
    const [stationName, setStationName] = useState(device.name);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const isAutoSetupStatus = (device?.status || '').startsWith('auto_setup');
    const autoProgress = device?.auto_base_progress || {};
    const autoSamplesCollected = Number(autoProgress.samples_collected || 0);
    const autoSamplesTarget = Number(autoProgress.samples_target || 0);
    const autoProgressPercent = autoSamplesTarget > 0
        ? Math.min(100, Math.round((autoSamplesCollected / autoSamplesTarget) * 100))
        : null;

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (disabled) return;
        if (!stationName.trim()) {
            return window.showError('Vui lòng nhập tên trạm!');
        }
        setIsSubmitting(true);
        try {
            await onProvision(device.serial, stationName);
        } catch {
            // The provisioning handler already reports the failure via toast.
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

            {/* Thông báo chế độ xem */}
            {disabled && (
                <div className="alert alert-info">
                    <i className="bi bi-eye-fill me-2"></i>
                    Chế độ xem: Bạn không có quyền kích hoạt trạm này.
                </div>
            )}

            <form onSubmit={handleSubmit}>
                {/* Fieldset để khóa tất cả input */}
                <fieldset disabled={disabled}>
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

                    {/* Chỉ hiện nút submit khi có quyền */}
                    {!disabled && (
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
                    )}
                </fieldset>
            </form>
        </div>
    );
});


window.LicenseForm = LicenseForm;
window.ProvisionForm = ProvisionForm;
})();
