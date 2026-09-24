// ======================
// caster-modals.js
// Caster configuration and source management modals.
// ======================
(() => {
'use strict';
const { useState, useEffect, memo } = React;
const CasterConfigModal = memo(function CasterConfigModal() {
    const [config, setConfig] = useState({ host: 'aitogy.com.vn', port: 2101 });
    const [isOpen, setIsOpen] = useState(false);

    // Hàm mở modal (gắn vào window để gọi từ console hoặc nút khác)
    useEffect(() => {
        window.openCasterConfig = () => {
            fetchConfig();
            setIsOpen(true);
        };
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await window.apiFetch(window.ApiRoutes.admin.casterConfig);
            if (res.ok) setConfig(await res.json());
        } catch (e) { console.error(e); }
    };

    const handleSave = async () => {
        try {
            await window.apiFetch(window.ApiRoutes.admin.casterConfig, {
                method: 'PUT',
                body: JSON.stringify(config)
            });
            window.showSuccess("Đã cập nhật cấu hình NTRIP Caster!");
            setIsOpen(false);
        } catch (e) {
            window.showError(e.message);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="modal d-block" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
            <div className="modal-dialog">
                <div className="modal-content bg-dark text-light border-secondary">
                    <div className="modal-header border-secondary">
                        <h5 className="modal-title">Cấu hình Hệ thống NTRIP Caster</h5>
                        <button type="button" className="btn-close btn-close-white" onClick={() => setIsOpen(false)}></button>
                    </div>
                    <div className="modal-body">
                        <p className="text-muted small">Cấu hình địa chỉ Caster dùng để tạo/xóa Rover qua giao thức TCP Admin.</p>
                        <div className="mb-3">
                            <label className="form-label">Caster Host / IP</label>
                            <input type="text" className="form-control" value={config.host}
                                onChange={e => setConfig({ ...config, host: e.target.value })} />
                        </div>
                        <div className="mb-3">
                            <label className="form-label">Admin Port (TCP)</label>
                            <input type="number" className="form-control" value={config.port}
                                onChange={e => setConfig({ ...config, port: parseInt(e.target.value) })} />
                        </div>
                    </div>
                    <div className="modal-footer border-secondary">
                        <button type="button" className="btn btn-secondary" onClick={() => setIsOpen(false)}>Đóng</button>
                        <button type="button" className="btn btn-primary" onClick={handleSave}>Lưu Cấu hình</button>
                    </div>
                </div>
            </div>
        </div>
    );
});
window.CasterConfigModal = CasterConfigModal;

// ===============================================================================
// === CASTER SOURCES MODAL (ADMIN: View all available mountpoints separated by domain)
// ===============================================================================

const CasterSourcesModal = memo(function CasterSourcesModal() {
    const [isOpen, setIsOpen] = useState(false);
    const [sources, setSources] = useState(null);
    const [loading, setLoading] = useState(false);
    const [selectedDomain, setSelectedDomain] = useState("all");

    useEffect(() => {
        window.openCasterSources = async () => {
            setIsOpen(true);
            await fetchSources();
        };
    }, []);

    const fetchSources = async () => {
        setLoading(true);
        try {
            const res = await window.apiFetch(window.ApiRoutes.admin.casterSources);
            if (res.ok) {
                const data = await res.json();
                setSources(data);
                setSelectedDomain("all");
            } else {
                window.showError("Không thể tải danh sách mountpoint từ Caster");
            }
        } catch (e) {
            console.error("[CasterSourcesModal]", e);
            window.showError("Lỗi kết nối Caster: " + e.message);
        } finally {
            setLoading(false);
        }
    };

    if (!isOpen || !sources) return null;

    // Get domains from by_domain
    const domains = Object.keys(sources.by_domain || {}).sort();

    // Filter sources based on selected domain
    let displayedSources = sources.all || [];
    if (selectedDomain !== "all") {
        displayedSources = sources.by_domain[selectedDomain] || [];
    }

    return (
        <div className="modal d-block" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
            <div className="modal-dialog modal-xl">
                <div className="modal-content bg-dark text-light border-secondary">
                    <div className="modal-header border-secondary">
                        <h5 className="modal-title">ðŸ“¡ Tất cả Mountpoints trên Caster (ðŸ”’ Admin Only)</h5>
                        <button type="button" className="btn-close btn-close-white" onClick={() => setIsOpen(false)}></button>
                    </div>
                    <div className="modal-body" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
                        {loading && <div className="text-center"><span className="spinner-border spinner-border-sm me-2"></span>Đang tải...</div>}

                        {!loading && sources && (
                            <>
                                {/* Domain Filter Tabs */}
                                <div className="mb-3">
                                    <div className="btn-group w-100" role="group">
                                        <input type="radio" className="btn-check" name="domain" id="domain-all"
                                            value="all" checked={selectedDomain === "all"}
                                            onChange={() => setSelectedDomain("all")} />
                                        <label className="btn btn-outline-primary" htmlFor="domain-all">
                                            ðŸ“Š Tất cả ({sources.total})
                                        </label>

                                        {domains.map(domain => (
                                            <span key={domain}>
                                                <input type="radio" className="btn-check" name="domain" id={`domain-${domain}`}
                                                    value={domain} checked={selectedDomain === domain}
                                                    onChange={() => setSelectedDomain(domain)} />
                                                <label className="btn btn-outline-info" htmlFor={`domain-${domain}`}>
                                                    ðŸŒ {domain} ({(sources.by_domain[domain] || []).length})
                                                </label>
                                            </span>
                                        ))}
                                    </div>
                                </div>

                                {/* Sources Table */}
                                <div className="table-responsive">
                                    <table className="table table-sm table-dark table-striped align-middle">
                                        <thead className="table-secondary">
                                            <tr>
                                                <th>Mountpoint</th>
                                                <th>Identifier</th>
                                                <th>Domain / Host</th>
                                                <th>Position</th>
                                                <th>Format</th>
                                                <th>Auth</th>
                                                <th>GGA Req</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {displayedSources.length > 0 ? (
                                                displayedSources.map((src, idx) => (
                                                    <tr key={idx} className="small">
                                                        <td>
                                                            <code className="text-info">{src.mountpoint}</code>
                                                            <button
                                                                type="button"
                                                                className="btn btn-sm btn-outline-success ms-2 copy-mp-btn"
                                                                onClick={() => {
                                                                    navigator.clipboard.writeText(src.mountpoint);
                                                                    window.showSuccess(`Copied: ${src.mountpoint}`);
                                                                }}
                                                                title="Copy mountpoint">
                                                                ðŸ“‹
                                                            </button>
                                                        </td>
                                                        <td><small className="text-muted">{src.identifier}</small></td>
                                                        <td>
                                                            <span className="badge bg-secondary">{src.domain || src.host}</span>
                                                        </td>
                                                        <td>
                                                            {src.latitude && src.longitude ? (
                                                                <small>{src.latitude.toFixed(4)}, {src.longitude.toFixed(4)}</small>
                                                            ) : (
                                                                <span className="text-muted">N/A</span>
                                                            )}
                                                        </td>
                                                        <td><small>{src.format}</small></td>
                                                        <td>
                                                            <span className={`badge ${src.auth_required === 'B' ? 'bg-warning' : 'bg-success'}`}>
                                                                {src.auth_required === 'B' ? 'ðŸ”’ Auth' : 'ðŸŸ¢ Public'}
                                                            </span>
                                                        </td>
                                                        <td>
                                                            <span className={`badge ${src.gga_required ? 'bg-danger' : 'bg-info'}`}>
                                                                {src.gga_required ? 'âœ… Yes' : 'âŒ No'}
                                                            </span>
                                                        </td>
                                                    </tr>
                                                ))
                                            ) : (
                                                <tr>
                                                    <td colSpan="7" className="text-center text-muted">Không có mountpoint nào</td>
                                                </tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>

                                <div className="mt-3 p-2 bg-dark-subtle rounded small text-muted">
                                    <strong>ðŸ’¡ Tips:</strong>
                                    <ul className="mb-0 ms-3">
                                        <li>Click ðŸ“‹ để copy mountpoint name</li>
                                        <li>Hãy tạo Rover với các mountpoint này mà không cần phải đăng nhập Caster Web</li>
                                        <li>Format: <code>GET /genuser HTTP/1.0/creator/username/password/MOUNTPOINT/...</code></li>
                                    </ul>
                                </div>
                            </>
                        )}
                    </div>
                    <div className="modal-footer border-secondary">
                        <button type="button" className="btn btn-secondary" onClick={() => setIsOpen(false)}>Đóng</button>
                        <button type="button" className="btn btn-primary" onClick={fetchSources} disabled={loading}>
                            ðŸ”„ Tải lại
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
});
window.CasterSourcesModal = CasterSourcesModal;
})();

