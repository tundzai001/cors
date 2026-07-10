// ======================
// react-components.js 
// ======================

const { useState, useEffect, memo, useRef } = React;

function getLeaflet() {
    const leaflet = window.Leaflet || window.L;
    return leaflet && typeof leaflet.map === 'function' ? leaflet : null;
}

// === PREMIUM UI COMPONENTS ===
const MetricLineChart = memo(function MetricLineChart({ label, data = [], color, unit = "" }) {
    const max = Math.max(...data, 1);
    const min = Math.min(...data, 0);
    const range = max - min || 1;

    const points = data.map((d, i) => {
        const x = (i / Math.max(data.length - 1, 1)) * 100;
        const y = 30 - ((d - min) / range) * 30;
        return `${x},${y}`;
    }).join(' ');

    return (
        <div style={{ padding: '0 8px' }}>
            <div className="d-flex justify-content-between align-items-center mb-1">
                <span className="small text-muted">{label}</span>
                <span className="fw-bold" style={{ color }}>
                    {data.length > 0 ? data[data.length - 1] : 0}{unit}
                </span>
            </div>
            <svg width="100%" height="30" viewBox="0 0 100 30" preserveAspectRatio="none">
                <polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
        </div>
    );
});
window.MetricLineChart = MetricLineChart;

const MetricCard = memo(function MetricCard({ title, value, unit, trend, sparklineData = [], color, icon }) {
    const max = Math.max(...sparklineData, 1);
    const min = Math.min(...sparklineData, 0);
    const range = max - min || 1;

    const points = sparklineData.map((d, i) => {
        const x = (i / Math.max(sparklineData.length - 1, 1)) * 100;
        const y = 20 - ((d - min) / range) * 20;
        return `${x},${y}`;
    }).join(' ');

    return (
        <div className="card bg-dark border-secondary-subtle">
            <div className="card-body p-2 d-flex flex-column">
                <div className="d-flex justify-content-between mb-2">
                    <div className="text-muted small fw-bold">
                        <span className="me-1">{icon}</span>{title}
                    </div>
                </div>
                <div className="d-flex justify-content-between align-items-end">
                    <div className="fs-4 fw-bold" style={{ color, lineHeight: 1 }}>
                        {value}<span className="fs-6 ms-1 text-muted">{unit}</span>
                    </div>
                    {sparklineData.length > 0 && (
                        <svg width="40" height="20" viewBox="0 0 100 20" preserveAspectRatio="none">
                            <polyline points={points} fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                    )}
                </div>
            </div>
        </div>
    );
});
window.MetricCard = MetricCard;

const SatelliteBars = memo(function SatelliteBars({ satellites }) {
    if (!satellites) return null;
    const sats = Object.values(satellites);
    if (sats.length === 0) return null;

    const counts = {};
    const snrs = {};
    sats.forEach(sat => {
        const systemMap = { 
            us: "US", ru: "RU", eu: "EU", cn: "CN", jp: "JP", un: "UN",
            G: "US", R: "RU", E: "EU", C: "CN", J: "JP" 
        };
        let sys = systemMap[sat.system] || "UN";
        if (sys === "UN") {
            const prnStr = String(sat.prn || "");
            if (prnStr.startsWith('G')) sys = "US";
            else if (prnStr.startsWith('R')) sys = "RU";
            else if (prnStr.startsWith('E')) sys = "EU";
            else if (prnStr.startsWith('B')) sys = "CN";
            else if (prnStr.startsWith('Q')) sys = "JP";
        }

        counts[sys] = (counts[sys] || 0) + 1;
        snrs[sys] = (snrs[sys] || 0) + (Number(sat.snr) || 0);
    });

    const systems = Object.keys(counts).map(sys => ({
        sys: sys, count: counts[sys], avgSnr: snrs[sys] / counts[sys]
    }));

    return (
        <div className="d-flex justify-content-around small text-muted">
            {systems.map(s => (
                <div key={s.sys} className="text-center">
                    <div className="fw-bold" style={{ color: s.avgSnr > 35 ? '#00ff88' : '#ffc107' }}>
                        {s.avgSnr.toFixed(0)} dB
                    </div>
                    <div style={{ fontSize: '0.7rem' }}>{s.sys} ({s.count})</div>
                </div>
            ))}
        </div>
    );
});
window.SatelliteBars = SatelliteBars;

// === CONSTANTS ===
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

const getStatusColor = (status) => {
    switch (status) {
        case 'online': return '#198754'; // success
        case 'offline': return '#dc3545'; // danger
        case 'sleep': return '#0dcaf0';   // info
        case 'streaming': return '#198754'; // success
        case 'starting': return '#ffc107';  // warning
        case 'unprovisioned': return '#ffc107';
        case 'configuring': return '#0d6efd';
        case 'locked': return '#dc3545';
        default:
            if (status && status.startsWith('auto_setup')) return '#0dcaf0';
            return '#6c757d'; // secondary
    }
};

const parseBoolCompat = (value, defaultValue = false) => {
    if (value === null || value === undefined) return defaultValue;
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const v = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(v)) return true;
        if (['false', '0', 'no', 'off'].includes(v)) return false;
    }
    return Boolean(value);
};

const isServerConfiguredCompat = (cfg, idx) => {
    const host = String(cfg[`serverhost${idx}`] || '').trim();
    const port = String(cfg[`port${idx}`] || '').trim();
    const mountpoint = String(cfg[`mountpoint${idx}`] || '').trim();
    return Boolean(host && port && mountpoint);
};

const PREFERRED_DOMAINS = ['aitogy.com.vn', 'aitogy.click'];
const isPreferredHost = (hostValue) => {
    const host = String(hostValue || '').trim().toLowerCase();
    if (!host) return false;
    return PREFERRED_DOMAINS.some((d) => host.includes(d));
};

const getMapEffectiveStatus = (device) => {
    const rawStatus = String(device?.status || '').toLowerCase();
    const stats = device?.ntrip_stats || {};
    const rtcmIn = Number(stats.rtcm_input_bps || stats.rtcm_in_bps || 0);
    const isVirtual = String(device?.detected_chip_type || '').toUpperCase() === 'VIRTUAL';

    if (rawStatus === 'streaming') return 'streaming';
    if (rawStatus === 'starting') return 'starting';

    if (!['online', 'streaming', 'sleep', 'starting'].includes(rawStatus)) {
        return (isVirtual && rtcmIn > 0) ? 'online' : 'offline';
    }

    const cfg = device?.service_config || {};
    const s1Enabled = parseBoolCompat(cfg.server1_enabled, isServerConfiguredCompat(cfg, 1));
    const s2Enabled = parseBoolCompat(cfg.server2_enabled, isServerConfiguredCompat(cfg, 2));

    if (!s1Enabled && !s2Enabled) {
        return (isVirtual && rtcmIn > 0) ? 'online' : 'offline';
    }

    const s1Host = String(cfg.serverhost1 || '').trim().toLowerCase();
    const s2Host = String(cfg.serverhost2 || '').trim().toLowerCase();
    const preferred = 'aitogy.com.vn';

    let primarySid = 1;
    if (s1Host.includes(preferred)) primarySid = 1;
    else if (s2Host.includes(preferred)) primarySid = 2;
    else primarySid = 1;

    const ntripStatusMap = device?.ntrip_status || {};
    const primaryBps = Number(ntripStatusMap[`server${primarySid}`]?.bps || 0);
    if (primaryBps > 0) {
        return 'online';
    }

    const onDemand = parseBoolCompat(cfg[`server${primarySid}_stream_on_demand`], parseBoolCompat(cfg.stream_on_demand, false));
    const active = onDemand ? parseBoolCompat(cfg[`server${primarySid}_stream_active`], parseBoolCompat(cfg.stream_active, false)) : true;
    if (rawStatus === 'sleep' && (!onDemand || active)) {
        return 'online';
    }
    return (!onDemand || active) ? 'online' : 'sleep';
};


// ===============================================================================
// === FORM COMPONENTS
// ===============================================================================

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
            const res = await window.apiFetch('/api/admin/caster-config');
            if (res.ok) setConfig(await res.json());
        } catch (e) { console.error(e); }
    };

    const handleSave = async () => {
        try {
            await window.apiFetch('/api/admin/caster-config', {
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
            const res = await window.apiFetch('/api/admin/caster-sources');
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
                        <h5 className="modal-title">📡 Tất cả Mountpoints trên Caster (🔒 Admin Only)</h5>
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
                                            📊 Tất cả ({sources.total})
                                        </label>

                                        {domains.map(domain => (
                                            <span key={domain}>
                                                <input type="radio" className="btn-check" name="domain" id={`domain-${domain}`}
                                                    value={domain} checked={selectedDomain === domain}
                                                    onChange={() => setSelectedDomain(domain)} />
                                                <label className="btn btn-outline-info" htmlFor={`domain-${domain}`}>
                                                    🌐 {domain} ({(sources.by_domain[domain] || []).length})
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
                                                                📋
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
                                                                {src.auth_required === 'B' ? '🔒 Auth' : '🟢 Public'}
                                                            </span>
                                                        </td>
                                                        <td>
                                                            <span className={`badge ${src.gga_required ? 'bg-danger' : 'bg-info'}`}>
                                                                {src.gga_required ? '✅ Yes' : '❌ No'}
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
                                    <strong>💡 Tips:</strong>
                                    <ul className="mb-0 ms-3">
                                        <li>Click 📋 để copy mountpoint name</li>
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
                            🔄 Tải lại
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
});
window.CasterSourcesModal = CasterSourcesModal;

// ===============================================================================


// ===============================================================================
// === ALL STATIONS MAP COMPONENT
// ===============================================================================
const createColoredIcon = (color) => {
    const leaflet = getLeaflet();
    if (!leaflet) return null;
    return leaflet.divIcon({
        className: 'custom-div-icon',
        html: `<div style="background-color: ${color}; width: 12px; height: 12px; border-radius: 50%; border: 2px solid white; box-shadow: 0 0 4px black;"></div>`,
        iconSize: [12, 12],
        iconAnchor: [6, 6]
    });
};

const getRoverStatusMeta = (status) => {
    if (status === 4) return { label: 'Fixed', color: '#22c55e' }; // Green
    if (status === 5) return { label: 'Float', color: '#38bdf8' }; // Light blue
    if (status === 0) return { label: '3D', color: '#ef4444' };    // Red
    return { label: 'Unknown', color: '#a3a3a3' };
};

const createRoverIcon = (color) => {
    const leaflet = getLeaflet();
    if (!leaflet) return null;
    const svg = `
        <svg fill="${color}" stroke="white" stroke-width="2" viewBox="0 0 24 24" width="24" height="24" style="filter: drop-shadow(0px 2px 3px rgba(0,0,0,0.8));">
            <path d="M12 2 L22 20 L2 20 Z" />
        </svg>
    `;
    return leaflet.divIcon({
        className: 'custom-div-icon',
        html: svg,
        iconSize: [24, 24],
        iconAnchor: [12, 12]
    });
};

const createRoverMarker = (lat, lon, color) => {
    const leaflet = getLeaflet();
    if (!leaflet) return null;
    return leaflet.marker([lat, lon], {
        icon: createRoverIcon(color),
        riseOnHover: true,
        keyboard: false
    });
};

const animateMarkerLatLng = (marker, lat, lon, durationMs = 650) => {
    if (!marker) return;

    if (marker.__animFrame) {
        cancelAnimationFrame(marker.__animFrame);
        marker.__animFrame = null;
    }

    const from = marker.getLatLng();
    const fromLat = Number(from.lat);
    const fromLon = Number(from.lng);
    const toLat = Number(lat);
    const toLon = Number(lon);

    if (!Number.isFinite(fromLat) || !Number.isFinite(fromLon) || !Number.isFinite(toLat) || !Number.isFinite(toLon)) {
        marker.setLatLng([lat, lon]);
        return;
    }

    const dLat = toLat - fromLat;
    const dLon = toLon - fromLon;
    if (Math.abs(dLat) < 0.0000001 && Math.abs(dLon) < 0.0000001) {
        return;
    }

    const start = performance.now();
    const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

    const step = (now) => {
        const t = Math.min(1, (now - start) / durationMs);
        const eased = easeOutCubic(t);
        marker.setLatLng([fromLat + dLat * eased, fromLon + dLon * eased]);
        if (t < 1) {
            marker.__animFrame = requestAnimationFrame(step);
        } else {
            marker.__animFrame = null;
        }
    };

    marker.__animFrame = requestAnimationFrame(step);
};

const getDistanceMeters = (lat1, lon1, lat2, lon2) => {
    const toRad = (deg) => (deg * Math.PI) / 180;
    const R = 6371000;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
        Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
};

const AllStationsMap = memo(function AllStationsMap({ devices, currentUser }) {
    const mapContainerRef = useRef(null);
    const mapInstanceRef = useRef(null);
    const roverClusterRef = useRef(null);
    const corsMarkersRef = useRef({});
    const roverMarkersRef = useRef({});
    const roverHashRef = useRef('');
    const [roverClients, setRoverClients] = useState([]);
    const [showCors, setShowCors] = useState(true);
    const [showRovers, setShowRovers] = useState(true);
    const [switchOrientation, setSwitchOrientation] = useState('horizontal');
    const [selectedMountpoints, setSelectedMountpoints] = useState([]);
    const [roverSearch, setRoverSearch] = useState('');
    const [mapViewportVersion, setMapViewportVersion] = useState(0);
    const [mapType, setMapType] = useState('m'); // 'y' = Hybrid, 'm' = Roadmap
    const tileLayerRef = useRef(null);
    const [deadbandMeters, setDeadbandMeters] = useState(1.5);
    const [isFilterOpen, setIsFilterOpen] = useState(false);
    const roverProcessFrameRef = useRef(null);
    const roverChunkSizeRef = useRef(24);

    // Nearest Station Feature States
    const [findingNearest, setFindingNearest] = useState(false);
    const [searchLocationQuery, setSearchLocationQuery] = useState('');
    const [isSearchingLoc, setIsSearchingLoc] = useState(false);
    const userMarkerRef = useRef(null);
    const polylinesRef = useRef([]); // Mảng chứa nhiều đường nối (online + offline)

    const canViewRovers = React.useMemo(() => {
        const role = String(currentUser?.role || '').toLowerCase();
        return role === 'admin' || role === 'viewer' || role === 'coordinator';
    }, [currentUser?.role]);

    // Các trạm mà coordinator có thể xem sẽ bị giới hạn bởi mountpoint
    const allowedMountpoints = React.useMemo(() => {
        const role = String(currentUser?.role || '').toLowerCase();
        if (role === 'admin') return null; // Admin sees all

        const mps = new Set();
        (devices || []).forEach(d => {
            const mp1 = d.service_config?.mountpoint1;
            const mp2 = d.service_config?.mountpoint2;
            if (mp1) mps.add(mp1);
            if (mp2) mps.add(mp2);
        });
        return mps;
    }, [devices, currentUser?.role]);

    const mountpointOptions = React.useMemo(() => {
        const mps = new Set();
        roverClients.forEach((rover) => {
            const mp = (rover.mountpoint || '').trim();
            if (mp) {
                if (allowedMountpoints === null || allowedMountpoints.has(mp)) {
                    mps.add(mp);
                }
            }
        });
        return Array.from(mps).sort((a, b) => a.localeCompare(b));
    }, [roverClients, allowedMountpoints]);

    const filteredRoverClients = React.useMemo(() => {
        const search = roverSearch.trim().toLowerCase();
        return roverClients.filter((rover) => {
            const mp = (rover.mountpoint || '').trim();

            // Block if not authorized to view this mountpoint
            if (allowedMountpoints !== null && !allowedMountpoints.has(mp)) {
                return false;
            }

            if (selectedMountpoints.length > 0 && !selectedMountpoints.includes(mp)) {
                return false;
            }
            if (!search) {
                return true;
            }
            const username = String(rover.username || '').toLowerCase();
            const receiverId = String(rover.receiver_id || '').toLowerCase();
            return username.includes(search) || receiverId.includes(search);
        });
    }, [roverClients, selectedMountpoints, roverSearch, allowedMountpoints]);

    useEffect(() => {
        let isMounted = true;

        const fetchRoverClients = async () => {
            if (!canViewRovers) {
                if (isMounted) {
                    setRoverClients([]);
                }
                roverHashRef.current = '';
                return;
            }

            try {
                const apiCaller = window.apiFetch || fetch;
                const response = await apiCaller('/api/caster/clienttable');
                if (!response || !response.ok) return;

                const payload = await response.json();
                const items = Array.isArray(payload.items) ? payload.items : [];

                const nextHash = items
                    .map((r) => `${r.username}|${r.receiver_id}|${r.mountpoint}|${r.latitude}|${r.longitude}|${r.status}|${r.nsv}`)
                    .join('#');

                if (nextHash === roverHashRef.current) {
                    return;
                }

                roverHashRef.current = nextHash;
                if (isMounted) {
                    setRoverClients(items);
                }
            } catch (error) {
                console.warn('Failed to fetch rover clienttable:', error);
            }
        };

        fetchRoverClients();
        const timer = setInterval(fetchRoverClients, 10000);

        return () => {
            isMounted = false;
            clearInterval(timer);
        };
    }, [canViewRovers]);

    useEffect(() => {
        const leaflet = getLeaflet();
        if (!leaflet || !mapContainerRef.current) {
            console.error('Leaflet namespace is not available or has been replaced');
            return;
        }

        if (!mapInstanceRef.current) {
            const map = leaflet.map(mapContainerRef.current).setView([21.0285, 105.8542], 6);

            tileLayerRef.current = leaflet.tileLayer(`https://{s}.google.com/vt/lyrs=${mapType}&x={x}&y={y}&z={z}`, {
                maxZoom: 20,
                subdomains: ['mt0', 'mt1', 'mt2', 'mt3']
            }).addTo(map);

            let canUseMarkerCluster = typeof leaflet.markerClusterGroup === 'function' &&
                typeof leaflet.MarkerClusterGroup === 'function' &&
                leaflet.MarkerClusterGroup.prototype;
            if (canUseMarkerCluster && typeof Reflect !== 'undefined') {
                try {
                    Reflect.construct(function () {}, [], leaflet.MarkerClusterGroup);
                } catch (error) {
                    canUseMarkerCluster = false;
                }
            }

            if (canUseMarkerCluster) {
                try {
                    roverClusterRef.current = leaflet.markerClusterGroup({
                        chunkedLoading: true,
                        chunkInterval: 40,
                        chunkDelay: 40,
                        removeOutsideVisibleBounds: true,
                        showCoverageOnHover: false,
                        spiderfyOnMaxZoom: false,
                        maxClusterRadius: 45,
                        disableClusteringAtZoom: 14
                    });
                    map.addLayer(roverClusterRef.current);
                } catch (error) {
                    console.info('Leaflet marker cluster unavailable, using plain rover markers.');
                    roverClusterRef.current = null;
                }
            }

            const handleViewportChanged = () => {
                setMapViewportVersion((v) => v + 1);
            };
            map.on('moveend zoomend', handleViewportChanged);
            map.__viewportHandler = handleViewportChanged;

            mapInstanceRef.current = map;
        }

        return () => {
            const map = mapInstanceRef.current;
            if (!map) return;

            Object.values(roverMarkersRef.current).forEach((marker) => {
                if (marker && marker.__animFrame) {
                    cancelAnimationFrame(marker.__animFrame);
                    marker.__animFrame = null;
                }
            });

            if (roverClusterRef.current) {
                roverClusterRef.current.clearLayers();
                roverClusterRef.current = null;
            }

            if (userMarkerRef.current) userMarkerRef.current = null;
            if (polylinesRef.current) polylinesRef.current = [];

            if (map.__viewportHandler) {
                map.off('moveend zoomend', map.__viewportHandler);
                delete map.__viewportHandler;
            }

            map.remove();
            mapInstanceRef.current = null;
            corsMarkersRef.current = {};
            roverMarkersRef.current = {};
        };
    }, []);

    // Cập nhật lớp bản đồ khi mapType thay đổi (Giao thông <-> Vệ tinh)
    useEffect(() => {
        if (tileLayerRef.current) {
            tileLayerRef.current.setUrl(`https://mt1.google.com/vt/lyrs=${mapType}&x={x}&y={y}&z={z}`);
        }
    }, [mapType]);

    useEffect(() => {
        const leaflet = getLeaflet();
        if (!leaflet) return;

        const map = mapInstanceRef.current;
        if (!map) return;

        const corsMarkers = corsMarkersRef.current;
        const validCorsKeys = new Set();
        const bounds = [];

        if (showCors) {
            devices.forEach(device => {
                const coords = device.base_config?.coords || (
                    device.base_config?.lat !== undefined && device.base_config?.lon !== undefined
                        ? { lat: device.base_config.lat, lon: device.base_config.lon, alt: device.base_config.alt }
                        : null
                );
                if (coords && coords.lat && coords.lon) {
                    validCorsKeys.add(device.serial);
                    const lat = parseFloat(coords.lat);
                    const lon = parseFloat(coords.lon);
                    bounds.push([lat, lon]);

                    const displayName = device.name || 'Unknown Device';
                    const mapEffectiveStatus = getMapEffectiveStatus(device);
                    const isAdmin = currentUser?.role === 'admin';
                    const serialHtml = isAdmin ? `<small>${device.serial}</small>` : '';

                    const popupContent = `
                        <div style="color: #333; min-width: 180px;">
                            <strong>Trạm CORS: ${displayName}</strong> <br>
                            <span class="badge ${STATUS_CLASS_MAP[mapEffectiveStatus] || 'bg-secondary'}">${STATUS_TEXT_MAP[mapEffectiveStatus] || mapEffectiveStatus}</span><br>
                            ${serialHtml}
                        </div>
                    `;

                    if (corsMarkers[device.serial]) {
                        corsMarkers[device.serial].setLatLng([lat, lon]);
                        corsMarkers[device.serial].setIcon(createColoredIcon(getStatusColor(mapEffectiveStatus)));
                        corsMarkers[device.serial].setPopupContent(popupContent);
                    } else {
                        const marker = leaflet.marker([lat, lon], {
                            icon: createColoredIcon(getStatusColor(mapEffectiveStatus))
                        }).addTo(map).bindPopup(popupContent);

                        marker.on('mouseover', function () {
                            this.openPopup();
                        });

                        corsMarkers[device.serial] = marker;
                    }
                }
            });
        }

        Object.keys(corsMarkers).forEach((serial) => {
            if (!showCors || !validCorsKeys.has(serial)) {
                map.removeLayer(corsMarkers[serial]);
                delete corsMarkers[serial];
            }
        });

        if (bounds.length > 0 && !map.fitted) {
            map.fitBounds(bounds, { padding: [50, 50] });
            map.fitted = true;
        }
    }, [devices, currentUser, showCors]);

    useEffect(() => {
        const leaflet = getLeaflet();
        if (!leaflet) return;

        const map = mapInstanceRef.current;
        if (!map) return;

        if (roverProcessFrameRef.current) {
            cancelAnimationFrame(roverProcessFrameRef.current);
            roverProcessFrameRef.current = null;
        }

        const roverMarkers = roverMarkersRef.current;
        const roverCluster = roverClusterRef.current;
        const validRoverKeys = new Set();
        const bounds = [];
        const viewBounds = map.getBounds().pad(0.25);
        const MIN_MOVE_METERS = Number.isFinite(deadbandMeters) ? Math.max(0, deadbandMeters) : 1.5;
        const MAX_SMOOTH_MOVE_METERS = 100;
        const MIN_CHUNK = 8;
        const MAX_CHUNK = 56;

        const ensureMarkerVisible = (marker) => {
            if (!marker) return;
            if (roverCluster) {
                if (!roverCluster.hasLayer(marker)) {
                    roverCluster.addLayer(marker);
                }
            } else if (!map.hasLayer(marker)) {
                marker.addTo(map);
            }
        };

        const ensureMarkerHidden = (marker) => {
            if (!marker) return;
            if (roverCluster) {
                if (roverCluster.hasLayer(marker)) {
                    roverCluster.removeLayer(marker);
                }
            } else if (map.hasLayer(marker)) {
                map.removeLayer(marker);
            }
        };

        const visibleRovers = showRovers && canViewRovers
            ? filteredRoverClients.filter((rover) => {
                const lat = Number(rover.latitude);
                const lon = Number(rover.longitude);
                const isVisible = rover.show_position !== false; // Default to true if undefined
                return Number.isFinite(lat) && Number.isFinite(lon) && viewBounds.contains([lat, lon]) && isVisible;
            })
            : [];

        let cursor = 0;
        const processChunk = () => {
            const currentChunkSize = roverChunkSizeRef.current;
            const chunkStartTs = performance.now();
            const end = Math.min(cursor + currentChunkSize, visibleRovers.length);

            for (let i = cursor; i < end; i++) {
                const rover = visibleRovers[i];
                const lat = Number(rover.latitude);
                const lon = Number(rover.longitude);
                const key = `${rover.username || 'unknown'}_${rover.receiver_id || '0'}`;
                validRoverKeys.add(key);
                bounds.push([lat, lon]);

                const statusMeta = getRoverStatusMeta(rover.status);
                const popupContent = `
                    <div style="color: #333; min-width: 180px;">
                        <strong>Rover: ${rover.username || 'N/A'}</strong><br>
                        <span class="badge text-bg-dark">RX: ${rover.receiver_id || 'N/A'}</span>
                        <span class="badge text-bg-primary ms-1">${rover.mountpoint || 'N/A'}</span><br>
                        Status: <b>${statusMeta.label}</b> | nSV: <b>${rover.nsv ?? 'N/A'}</b>
                    </div>
                `;

                if (roverMarkers[key]) {
                    const marker = roverMarkers[key];
                    const current = marker.getLatLng();
                    const distance = getDistanceMeters(current.lat, current.lng, lat, lon);

                    if (distance >= MIN_MOVE_METERS) {
                        if (distance <= MAX_SMOOTH_MOVE_METERS) {
                            animateMarkerLatLng(marker, lat, lon, 550);
                        } else {
                            marker.setLatLng([lat, lon]);
                        }
                    }

                    if (marker.__statusColor !== statusMeta.color) {
                        marker.setIcon(createRoverIcon(statusMeta.color));
                        marker.__statusColor = statusMeta.color;
                    }
                    if (marker.__popupHtml !== popupContent) {
                        marker.setPopupContent(popupContent);
                        marker.__popupHtml = popupContent;
                    }
                    ensureMarkerVisible(marker);
                } else {
                    const marker = createRoverMarker(lat, lon, statusMeta.color).bindPopup(popupContent);
                    marker.__statusColor = statusMeta.color;
                    marker.__popupHtml = popupContent;
                    roverMarkers[key] = marker;
                    ensureMarkerVisible(marker);
                }
            }

            cursor = end;

            // Adaptive FPS guard:
            // - If a chunk takes too long, reduce work per frame.
            // - If chunks are consistently fast, increase gradually.
            const elapsed = performance.now() - chunkStartTs;
            if (elapsed > 14 && roverChunkSizeRef.current > MIN_CHUNK) {
                roverChunkSizeRef.current = Math.max(MIN_CHUNK, roverChunkSizeRef.current - 4);
            } else if (elapsed < 7 && roverChunkSizeRef.current < MAX_CHUNK) {
                roverChunkSizeRef.current = Math.min(MAX_CHUNK, roverChunkSizeRef.current + 2);
            }

            if (cursor < visibleRovers.length) {
                roverProcessFrameRef.current = requestAnimationFrame(processChunk);
                return;
            }

            roverProcessFrameRef.current = null;

            Object.keys(roverMarkers).forEach((key) => {
                if (!showRovers || !validRoverKeys.has(key)) {
                    if (roverMarkers[key].__animFrame) {
                        cancelAnimationFrame(roverMarkers[key].__animFrame);
                        roverMarkers[key].__animFrame = null;
                    }
                    ensureMarkerHidden(roverMarkers[key]);
                    delete roverMarkers[key];
                }
            });

            if (bounds.length > 0 && !map.fitted) {
                map.fitBounds(bounds, { padding: [50, 50] });
                map.fitted = true;
            }
        };

        processChunk();

        return () => {
            if (roverProcessFrameRef.current) {
                cancelAnimationFrame(roverProcessFrameRef.current);
                roverProcessFrameRef.current = null;
            }
        };

    }, [filteredRoverClients, showRovers, mapViewportVersion, canViewRovers, deadbandMeters]);

    useEffect(() => {
        if (selectedMountpoints.length === 0) return;
        const valid = new Set(mountpointOptions);
        const next = selectedMountpoints.filter((mp) => valid.has(mp));
        if (next.length !== selectedMountpoints.length) {
            setSelectedMountpoints(next);
        }
    }, [mountpointOptions, selectedMountpoints]);

    const toggleMountpoint = (mp) => {
        setSelectedMountpoints((prev) => {
            if (prev.includes(mp)) {
                return prev.filter((item) => item !== mp);
            }
            return [...prev, mp];
        });
    };

    const findNearestFromCoords = (lat, lon, locationName = "Vị trí của bạn") => {
        // Helper: extract coords from device consistently
        const getDeviceCoords = (d) => {
            const bc = d?.base_config;
            if (!bc) return null;
            if (bc.coords && bc.coords.lat && bc.coords.lon) return bc.coords;
            if (bc.lat !== undefined && bc.lon !== undefined) return { lat: bc.lat, lon: bc.lon, alt: bc.alt };
            return null;
        };
        // Lấy tất cả các trạm có tọa độ
        const validDevices = devices.filter(d => getDeviceCoords(d) !== null);

        if (validDevices.length === 0) {
            if (window.showError) window.showError("Không có trạm CORS nào có tọa độ hợp lệ trên hệ thống.");
            return;
        }

        // Tách riêng trạm kết nối (online hoặc có BPS > 0) và offline
        // BPS > 0 có nghĩa là trạm vẫn truyền dữ liệu (kể cả khi sleep)
        const connectedDevices = validDevices.filter(d =>
            d.status === 'online' || (d.bps && d.bps > 0)
        );
        const offlineDevices = validDevices.filter(d =>
            d.status !== 'online' && (!d.bps || d.bps === 0)
        );

        // Hàm tính khoảng cách
        const calcDist = (d) => {
            const coords = getDeviceCoords(d);
            return window.getDistanceMeters
                ? window.getDistanceMeters(lat, lon, parseFloat(coords.lat), parseFloat(coords.lon))
                : getDistanceMeters(lat, lon, parseFloat(coords.lat), parseFloat(coords.lon));
        };

        // Tìm trạm gần nhất trong 1 danh sách
        const findNearest = (list) => {
            let best = null, bestDist = Infinity;
            list.forEach(d => {
                const dist = calcDist(d);
                if (dist < bestDist) { bestDist = dist; best = d; }
            });
            return best ? { device: best, dist: bestDist } : null;
        };

        const nearestConnected = findNearest(connectedDevices);
        const nearestOffline = findNearest(offlineDevices);

        if (!nearestConnected && !nearestOffline) {
            if (window.showError) window.showError("Không tìm thấy trạm CORS nào phù hợp.");
            return;
        }

        // Thông báo kết quả
        const textPrefix = locationName === "Vị trí của bạn" ? "Trạm gần nhất" : `Gần ${locationName.split(',')[0]} nhất`;
        let msg = [];
        if (nearestConnected) msg.push(`🟢 CONNECTED: ${nearestConnected.device.name || nearestConnected.device.serial} (${(nearestConnected.dist / 1000).toFixed(2)} km)`);
        if (nearestOffline) msg.push(`🔴 OFFLINE: ${nearestOffline.device.name || nearestOffline.device.serial} (${(nearestOffline.dist / 1000).toFixed(2)} km)`);
        if (window.showSuccess) window.showSuccess(`${textPrefix} — ${msg.join(' | ')}`);

        const leaflet = getLeaflet();
        const map = mapInstanceRef.current;
        if (leaflet && map) {
            // Xóa các đường cũ
            if (userMarkerRef.current) map.removeLayer(userMarkerRef.current);
            polylinesRef.current.forEach(item => {
                map.removeLayer(item.line);
                if (item.tooltip) map.removeLayer(item.tooltip);
            });
            polylinesRef.current = [];

            // Marker vị trí người dùng
            const iconHtml = locationName === "Vị trí của bạn"
                ? `<div style="background-color: #007bff; width: 16px; height: 16px; border-radius: 50%; border: 3px solid white; box-shadow: 0 0 6px rgba(0,0,0,0.5);"></div>`
                : `<div style="background-color: #ffc107; width: 16px; height: 16px; border-radius: 50%; border: 3px solid white; box-shadow: 0 0 6px rgba(0,0,0,0.5);"></div>`;
            const userIcon = leaflet.divIcon({ className: 'custom-div-icon', html: iconHtml, iconSize: [16, 16], iconAnchor: [8, 8] });
            userMarkerRef.current = leaflet.marker([lat, lon], { icon: userIcon }).addTo(map)
                .bindPopup(`<b>${locationName.split('(')[0]}</b>`).openPopup();

            // Hàm vẽ 1 đường nối
            const drawLine = (target, color, label) => {
                if (!target) return null;
                const tCoords = getDeviceCoords(target.device);
                const tLat = parseFloat(tCoords.lat);
                const tLon = parseFloat(tCoords.lon);
                const distKm = (target.dist / 1000).toFixed(2);
                const stationName = target.device.name || target.device.serial;

                const line = leaflet.polyline([[lat, lon], [tLat, tLon]], {
                    color: color,
                    weight: 5,
                    opacity: 1.0,
                    dashArray: '12, 12',
                    lineCap: 'round',
                    lineJoin: 'round'
                }).addTo(map);

                line.bindTooltip(
                    `<div style="font-size: 13px; font-weight: bold; color: ${color}; text-shadow: 1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000; white-space: nowrap;">
                        ${label} ${stationName}: ${distKm} km
                    </div>`,
                    { permanent: true, direction: 'center', className: 'bg-transparent border-0 shadow-none' }
                ).openTooltip();

                return { line };
            };

            // Vẽ đường xanh lá đến trạm kết nối gần nhất (online hoặc có BPS)
            const connectedLine = drawLine(nearestConnected, '#00ff00', '🟢');
            if (connectedLine) polylinesRef.current.push(connectedLine);

            // Vẽ đường đỏ đến trạm offline gần nhất
            const offlineLine = drawLine(nearestOffline, '#ff3333', '🔴');
            if (offlineLine) polylinesRef.current.push(offlineLine);

            // Zoom bao trọn tất cả đường nối
            const allBounds = leaflet.latLngBounds([[lat, lon]]);
            if (nearestConnected) {
                const cCoords = getDeviceCoords(nearestConnected.device);
                allBounds.extend([parseFloat(cCoords.lat), parseFloat(cCoords.lon)]);
            }
            if (nearestOffline) {
                const oCoords = getDeviceCoords(nearestOffline.device);
                allBounds.extend([parseFloat(oCoords.lat), parseFloat(oCoords.lon)]);
            }
            map.flyToBounds(allBounds, { padding: [60, 60], maxZoom: 14, duration: 1.5 });
        }
    };

    const handleFindNearest = () => {
        if (!navigator.geolocation) {
            if (window.showError) window.showError("Trình duyệt không hỗ trợ Geolocation.");
            return;
        }

        setFindingNearest(true);
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const lat = pos.coords.latitude;
                const lon = pos.coords.longitude;
                findNearestFromCoords(lat, lon, "Vị trí của bạn");
                setFindingNearest(false);
            },
            (err) => {
                setFindingNearest(false);
                if (window.showError) window.showError("Không thể lấy vị trí hiện tại.");
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    };

    const handleSearchLocation = async () => {
        const query = searchLocationQuery.trim();
        if (!query) return;

        setIsSearchingLoc(true);
        try {

            const response = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&countrycodes=vn&limit=1`);
            const data = await response.json();

            if (data && data.length > 0) {
                const lat = parseFloat(data[0].lat);
                const lon = parseFloat(data[0].lon);
                findNearestFromCoords(lat, lon, data[0].display_name);
            } else {
                if (window.showError) window.showError(`Không tìm thấy địa danh: "${query}". Vui lòng thử nhập kèm Tên Huyện, Tỉnh.`);
            }
        } catch (error) {
            console.error(error);
            if (window.showError) window.showError("Lỗi kết nối khi tìm kiếm địa điểm.");
        } finally {
            setIsSearchingLoc(false);
        }
    };

    return (
        <div style={{ position: 'relative' }}>
            <style>{`
                @keyframes mapFilterFadeIn {
                    from { opacity: 0; transform: translateY(-5px); }
                    to { opacity: 1; transform: translateY(0); }
                }
            `}</style>
            <div
                style={{
                    position: 'absolute',
                    top: '12px',
                    left: '55px', // Tránh nút +/- của Leaflet
                    zIndex: 1000,
                    maxWidth: 'calc(100% - 70px)' // Đảm bảo không bị tràn trên màn hình nhỏ
                }}
            >
                <div className="d-flex flex-wrap gap-2 align-items-center">
                    {/* Nút bật/tắt Popup Filter */}
                    <button
                        className="btn text-light fw-bold flex-shrink-0"
                        onClick={() => setIsFilterOpen(!isFilterOpen)}
                        style={{
                            background: 'rgba(8, 12, 20, 0.85)',
                            border: '1px solid rgba(255, 255, 255, 0.3)',
                            borderRadius: '8px',
                            padding: '8px 16px',
                            boxShadow: '0 4px 6px rgba(0,0,0,0.6)',
                            whiteSpace: 'nowrap'
                        }}
                    >
                        <i className={`bi ${isFilterOpen ? 'bi-x-circle-fill text-warning' : 'bi-funnel-fill'} me-2`}></i>
                        Bộ lọc
                    </button>

                    {/* Nút Tìm trạm gần nhất */}
                    {currentUser && (
                        <button
                            className="btn text-light fw-bold btn-primary border-0 flex-shrink-0"
                            onClick={handleFindNearest}
                            disabled={findingNearest}
                            style={{
                                background: findingNearest ? '#6c757d' : '#0d6efd',
                                borderRadius: '8px',
                                padding: '8px 16px',
                                boxShadow: '0 4px 6px rgba(0,0,0,0.6)',
                                transition: 'background-color 0.2s',
                                whiteSpace: 'nowrap'
                            }}
                            title="Lấy vị trí của bạn và tìm trạm gần nhất"
                        >
                            {findingNearest ? (
                                <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Đang tìm...</>
                            ) : (
                                <><i className="bi bi-geo-alt-fill me-2"></i>Tìm trạm gần nhất</>
                            )}
                        </button>
                    )}

                    {/* Ô Tìm kiếm địa điểm */}
                    {currentUser && (
                        <div className="input-group" style={{ width: 'auto', minWidth: '220px', flex: '1 1 auto', boxShadow: '0 4px 6px rgba(0,0,0,0.6)' }}>
                            <input
                                type="text"
                                className="form-control bg-dark text-light border-secondary"
                                placeholder="Tên Xã, Huyện, Tỉnh..."
                                value={searchLocationQuery}
                                onChange={(e) => setSearchLocationQuery(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleSearchLocation();
                                }}
                                style={{ borderRadius: '8px 0 0 8px' }}
                            />
                            <button
                                className="btn btn-primary border-primary flex-shrink-0"
                                onClick={handleSearchLocation}
                                disabled={isSearchingLoc || !searchLocationQuery.trim()}
                                style={{ borderRadius: '0 8px 8px 0' }}
                            >
                                {isSearchingLoc
                                    ? <span className="spinner-border spinner-border-sm"></span>
                                    : <i className="bi bi-search"></i>}
                            </button>
                        </div>
                    )}
                </div>

                {/* Khung chứa các Filter, chỉ hiện khi isFilterOpen = true */}
                {isFilterOpen && (
                    <div
                        className="d-flex align-items-center gap-2 mt-2"
                        style={{
                            background: 'rgba(8, 12, 20, 0.95)',
                            border: '1px solid rgba(255, 255, 255, 0.2)',
                            borderRadius: '10px',
                            padding: '12px',
                            flexDirection: switchOrientation === 'vertical' ? 'column' : 'row',
                            boxShadow: '0 8px 16px rgba(0,0,0,0.7)',
                            animation: 'mapFilterFadeIn 0.2s ease-out forwards'
                        }}
                    >
                        <button
                            className="btn btn-sm btn-outline-light"
                            onClick={() => setSwitchOrientation((prev) => prev === 'horizontal' ? 'vertical' : 'horizontal')}
                            title="Đổi nút gạt ngang/dọc"
                        >
                            <i className={`bi ${switchOrientation === 'horizontal' ? 'bi-distribute-horizontal' : 'bi-distribute-vertical'}`}></i>
                        </button>

                        <div className="form-check form-switch m-0 d-flex align-items-center" style={{ gap: '0.5rem' }}>
                            <input
                                className="form-check-input m-0 mt-1"
                                type="checkbox"
                                role="switch"
                                id="toggle-cors-layer"
                                checked={showCors}
                                onChange={(e) => setShowCors(e.target.checked)}
                            />
                            <label className="form-check-label text-light small fw-bold m-0" htmlFor="toggle-cors-layer">
                                Trạm CORS
                            </label>
                        </div>

                        {canViewRovers && (
                            <>
                                <div className="form-check form-switch m-0 d-flex align-items-center" style={{ gap: '0.5rem' }}>
                                    <input
                                        className="form-check-input m-0 mt-1"
                                        type="checkbox"
                                        role="switch"
                                        id="toggle-rover-layer"
                                        checked={showRovers}
                                        onChange={(e) => setShowRovers(e.target.checked)}
                                    />
                                    <label className="form-check-label text-light small fw-bold m-0" htmlFor="toggle-rover-layer">
                                        Rover
                                    </label>
                                </div>

                                <div className="form-check form-switch m-0 d-flex align-items-center" style={{ gap: '0.5rem' }}>
                                    <input
                                        className="form-check-input m-0 mt-1"
                                        type="checkbox"
                                        role="switch"
                                        id="toggle-map-satellite"
                                        checked={mapType === 'y'}
                                        onChange={(e) => setMapType(e.target.checked ? 'y' : 'm')}
                                    />
                                    <label className="form-check-label text-light small fw-bold m-0" htmlFor="toggle-map-satellite">
                                        Vệ tinh (Google)
                                    </label>
                                </div>

                                <div className="dropdown" data-bs-auto-close="outside">
                                    <button
                                        className="btn btn-sm btn-outline-light dropdown-toggle"
                                        type="button"
                                        data-bs-toggle="dropdown"
                                        aria-expanded="false"
                                        title="Chọn nhiều mountpoint bằng cách bấm trực tiếp"
                                    >
                                        {selectedMountpoints.length > 0
                                            ? `MP (${selectedMountpoints.length})`
                                            : 'MP: ALL'}
                                    </button>
                                    <ul className="dropdown-menu dropdown-menu-dark p-2" style={{ minWidth: '240px', maxHeight: '260px', overflowY: 'auto' }}>
                                        <li className="mb-2">
                                            <button
                                                type="button"
                                                className="btn btn-sm btn-outline-info w-100"
                                                onClick={() => setSelectedMountpoints([])}
                                            >
                                                Chọn tất cả Mountpoint
                                            </button>
                                        </li>
                                        {mountpointOptions.length === 0 && (
                                            <li className="dropdown-item-text text-muted small">Không có mountpoint</li>
                                        )}
                                        {mountpointOptions.map((mp) => {
                                            const checked = selectedMountpoints.includes(mp);
                                            return (
                                                <li key={mp} className="mb-1">
                                                    <button
                                                        type="button"
                                                        className={`btn btn-sm w-100 text-start ${checked ? 'btn-primary' : 'btn-outline-secondary'}`}
                                                        onClick={() => toggleMountpoint(mp)}
                                                    >
                                                        {checked ? '✓ ' : ''}{mp}
                                                    </button>
                                                </li>
                                            );
                                        })}
                                    </ul>
                                </div>

                                <input
                                    type="text"
                                    className="form-control form-control-sm bg-dark text-light border-secondary"
                                    style={{ minWidth: '180px' }}
                                    placeholder="Lọc rover (user/RX)"
                                    value={roverSearch}
                                    onChange={(e) => setRoverSearch(e.target.value)}
                                />

                            </>
                        )}
                    </div>
                )}
            </div>

            <div
                ref={mapContainerRef}
                style={{
                    width: '100%',
                    height: 'calc(100vh - 150px)',
                    borderRadius: '8px',
                    border: '2px solid #444',
                    background: '#222'
                }}
            />
        </div>
    );
});
window.AllStationsMap = AllStationsMap;

console.log('✓ React Components loaded and assigned to window object.');
