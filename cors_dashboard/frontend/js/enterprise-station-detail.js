/**
 * EnterpriseStationDetail Component
 * A high-end, professional dashboard for individual GNSS stations.
 */
const EnterpriseStationDetail = ({ device }) => {
    if (!device) return null;

    const stats = [
        { label: 'Satellites', value: device.system_info?.satellites || 0, icon: 'bi-satellite' },
        { label: 'Latency', value: `${device.system_info?.latency || 0}ms`, icon: 'bi-speedometer' },
        { label: 'Uptime 24h', value: '99.9%', icon: 'bi-clock-history' },
        { label: 'RTK Status', value: device.rtk_fix_status || 'FIX', icon: 'bi-check-circle-fill' }
    ];

    const isOffline = device.status === 'offline';
    const [snrData, setSnrData] = React.useState(Array(24).fill(0));

    React.useEffect(() => {
        if (isOffline) {
            setSnrData(Array(24).fill(0));
            return;
        }
        
        // Initialize with random base values
        setSnrData(Array(24).fill(0).map(() => Math.floor(Math.random() * 20) + 35));
        
        // Simulate real-time SNR fluctuation
        const interval = setInterval(() => {
            setSnrData(prev => prev.map(val => {
                let next = val + (Math.random() * 4 - 2);
                if (next < 30) next = 30;
                if (next > 60) next = 60;
                return next;
            }));
        }, 1000);
        
        return () => clearInterval(interval);
    }, [isOffline]);

    return (
        <div className="enterprise-station-detail animate__animated animate__fadeIn">
            {/* Header Section */}
            <div className="mb-4">
                <a href="/" className="btn btn-sm btn-outline-cyan rounded-pill px-3" style={{ border: '1px solid var(--glass-border)', background: 'rgba(0, 242, 255, 0.05)' }}>
                    <i className="bi bi-arrow-left me-2"></i> Trở về trang chủ
                </a>
            </div>
            <div className="d-flex justify-content-between align-items-center mb-5">
                <div>
                    <h1 className="display-4 fw-bold mb-0" style={{ color: 'var(--primary-cyan)' }}>
                        {device.name || device.serial}
                    </h1>
                    <div className="d-flex align-items-center mt-2">
                        <span className="pulse-online"></span>
                        <span className="text-uppercase tracking-widest small opacity-75">
                            Station Status: {device.status}
                        </span>
                    </div>
                </div>
                <div className="text-end">
                    <div className="text-muted small">MOUNTPOINT</div>
                    <div className="h4 fw-bold text-white">{device.name || 'N/A'}</div>
                    <div className="text-muted small mt-2">DEVICE SERIAL</div>
                    <div className="h6 fw-mono text-white opacity-50 mb-0">{device.serial}</div>
                </div>
            </div>

            <div className="row g-4">
                {/* Main Stats Card */}
                <div className="col-lg-8">
                    <div className="row g-4 mb-4">
                        {stats.map((stat, idx) => (
                            <div key={idx} className="col-md-3">
                                <div className="glass-card text-center">
                                    <div className="stat-label mb-2"><i className={`bi ${stat.icon} me-2`}></i>{stat.label}</div>
                                    <div className="stat-value">{stat.value}</div>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Signal Quality Chart / Skyplot Placeholder */}
                    <div className="glass-card mb-4">
                        <div className="d-flex justify-content-between align-items-center mb-4">
                            <h5 className="mb-0">Satellite Signal Strength (SNR)</h5>
                            <div className="d-flex gap-2">
                                <span className="badge-gnss badge-gps">GPS</span>
                                <span className="badge-gnss badge-glo">GLO</span>
                                <span className="badge-gnss badge-bds">BDS</span>
                                <span className="badge-gnss badge-gal">GAL</span>
                            </div>
                        </div>
                        <div style={{ height: '300px', display: 'flex', alignItems: 'flex-end', gap: '8px' }}>
                            {snrData.map((height, i) => (
                                <div key={i} className="flex-grow-1" style={{ 
                                    height: `${isOffline ? 5 : height}%`, 
                                    background: isOffline ? 'rgba(255,255,255,0.05)' : 'linear-gradient(to top, rgba(0, 242, 255, 0.1), var(--primary-cyan))',
                                    borderRadius: '4px 4px 0 0',
                                    opacity: isOffline ? 0.3 : 0.8,
                                    transition: 'height 0.5s ease-in-out'
                                }}></div>
                            ))}
                        </div>
                    </div>
                </div>

                {/* Sidebar Info */}
                <div className="col-lg-4">
                    <div className="glass-card h-100">
                        <h5 className="mb-4">Geodetic Location</h5>
                        <div className="mb-4 p-3 rounded" style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)' }}>
                            <div className="small text-muted mb-1">LATITUDE</div>
                            <div className="h5 fw-mono text-white mb-3">{device.base_config?.coords?.lat || device.base_config?.lat || 'N/A'}</div>
                            
                            <div className="small text-muted mb-1">LONGITUDE</div>
                            <div className="h5 fw-mono text-white mb-3">{device.base_config?.coords?.lon || device.base_config?.lon || 'N/A'}</div>
                            
                            <div className="small text-muted mb-1">ALTITUDE</div>
                            <div className="h5 fw-mono text-white">{device.base_config?.coords?.alt || device.base_config?.alt || '0.000'} m</div>
                        </div>

                        <div className="mt-auto">
                            <button className="btn btn-outline-cyan w-100 mb-3 py-3 rounded-pill" onClick={() => navigator.clipboard.writeText(`${device.base_config?.coords?.lat},${device.base_config?.coords?.lon}`)}>
                                <i className="bi bi-clipboard-data me-2"></i> Copy Coordinates
                            </button>
                            <button className="btn btn-cyan w-100 py-3 rounded-pill">
                                <i className="bi bi-download me-2"></i> Download RINEX
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

window.EnterpriseStationDetail = EnterpriseStationDetail;
