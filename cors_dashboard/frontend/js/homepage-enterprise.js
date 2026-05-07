/**
 * HomePageEnterprise Component
 * The professional public landing page for unauthenticated visitors.
 */
const HomePageEnterprise = ({ devices }) => {
    const onlineCount = devices.filter(d => d.status === 'online').length;
    const totalSats = devices.reduce((acc, d) => acc + (d.system_info?.satellites || 0), 0);
    const avgLatency = devices.length > 0 ? Math.round(devices.reduce((acc, d) => acc + (d.system_info?.latency || 0), 0) / devices.length) : 0;

    const stats = [
        { label: 'Total Stations', value: devices.length, sub: 'Network Coverage', icon: 'bi-broadcast-pin' },
        { label: 'Active Nodes', value: onlineCount, sub: 'Realtime Online', icon: 'bi-check-all', color: 'var(--primary-cyan)' },
        { label: 'Satellites Tracked', value: totalSats, sub: 'Global Constellations', icon: 'bi-satellite' },
        { label: 'Avg Latency', value: `${avgLatency}ms`, sub: 'Ultra-low response', icon: 'bi-lightning-charge' }
    ];

    return (
        <div className="enterprise-homepage">
            <header className="position-absolute w-100 p-4 d-flex justify-content-end" style={{zIndex: 100}}>
                <button className="btn btn-outline-light border-opacity-25 rounded-pill px-4" onClick={() => window.location.href = '/login.html'}>
                    <i className="bi bi-box-arrow-in-right me-2"></i>Đăng nhập
                </button>
            </header>
            <section className="hero-section text-center">
                <div className="tech-grid"></div>
                <div className="container position-relative">
                    <div className="animate__animated animate__fadeInUp">
                        <div className="badge bg-primary bg-opacity-10 text-primary border border-primary border-opacity-25 px-3 py-2 rounded-pill mb-4">
                            <i className="bi bi-shield-check me-2"></i> NEXT-GEN GNSS INFRASTRUCTURE
                        </div>
                        <h1 className="hero-title mb-4">
                            Aitogy Network<br/>
                            <span style={{opacity: 0.8}}>CORS Control Center</span>
                        </h1>
                        <p className="hero-subtitle mx-auto mb-5">
                            Hệ thống quản lý và giám sát mạng lưới trạm định vị vệ tinh độ chính xác cao. 
                            Cung cấp hạ tầng RTK/NTRIP chuyên nghiệp cho khảo sát, đo đạc và tự động hóa.
                        </p>
                        <div className="d-flex justify-content-center gap-3">
                            <button className="btn-enterprise" onClick={() => window.location.href = '/login.html'}>
                                Access Dashboard <i className="bi bi-arrow-right ms-2"></i>
                            </button>
                            <button className="btn btn-outline-light border-opacity-25 px-4 rounded-pill" onClick={() => window.openPublicMap()}>
                                View Public Map
                            </button>
                        </div>
                    </div>
                </div>
            </section>

            <section className="stats-section container py-5">
                <div className="row g-4">
                    {stats.map((stat, idx) => (
                        <div key={idx} className="col-lg-3 col-md-6 animate__animated animate__fadeInUp" style={{animationDelay: `${idx * 0.1}s`}}>
                            <div className="status-card h-100 glow-border">
                                <div className="d-flex align-items-center mb-3">
                                    <div className="p-2 rounded bg-primary bg-opacity-10 text-primary me-3">
                                        <i className={`bi ${stat.icon} fs-4`}></i>
                                    </div>
                                    <div className="stat-label">{stat.label}</div>
                                </div>
                                <div className="stat-value mb-1" style={{color: stat.color || 'white'}}>{stat.value}</div>
                                <div className="small text-muted">{stat.sub}</div>
                            </div>
                        </div>
                    ))}
                </div>
            </section>

            <section className="features-section container py-5">
                <div className="glass-card p-5 border-0 bg-opacity-10">
                    <div className="row align-items-center">
                        <div className="col-lg-6">
                            <h2 className="display-6 fw-bold mb-4">Enterprise Features</h2>
                            <ul className="list-unstyled">
                                <li className="mb-4 d-flex align-items-start">
                                    <i className="bi bi-cpu text-primary fs-4 me-3"></i>
                                    <div>
                                        <h5 className="mb-1">Multi-Constellation Support</h5>
                                        <p className="text-muted small">Tracking GPS, GLONASS, Galileo, BeiDou for maximum accuracy.</p>
                                    </div>
                                </li>
                                <li className="mb-4 d-flex align-items-start">
                                    <i className="bi bi-wifi text-primary fs-4 me-3"></i>
                                    <div>
                                        <h5 className="mb-1">High-Speed NTRIP Caster</h5>
                                        <p className="text-muted small">Optimized data transmission with ultra-low latency protocol.</p>
                                    </div>
                                </li>
                                <li className="d-flex align-items-start">
                                    <i className="bi bi-graph-up-arrow text-primary fs-4 me-3"></i>
                                    <div>
                                        <h5 className="mb-1">24/7 Health Monitoring</h5>
                                        <p className="text-muted small">Automatic alerts and recovery for maximum network uptime.</p>
                                    </div>
                                </li>
                            </ul>
                        </div>
                        <div className="col-lg-6">
                             {/* Map Preview or High-end Graphic */}
                             <div className="rounded-4 overflow-hidden shadow-lg border border-secondary border-opacity-25 bg-dark" style={{height: '400px', position: 'relative'}}>
                                <div className="tech-grid"></div>
                                <div className="p-4">
                                    <div className="d-flex justify-content-between align-items-center mb-4">
                                        <span className="badge bg-success bg-opacity-25 text-success rounded-pill">LIVE NETWORK MAP</span>
                                        <span className="text-muted small">Realtime Sync</span>
                                    </div>
                                    <div className="text-center py-5">
                                        <i className="bi bi-globe-asia-australia display-1 text-primary opacity-25"></i>
                                    </div>
                                </div>
                             </div>
                        </div>
                    </div>
                </div>
            </section>

            <footer className="container py-5 text-center border-top border-secondary border-opacity-25 mt-5">
                <div className="text-muted small mb-3">© 2025 AITOGY INFRASTRUCTURE TECHNOLOGY JSC</div>
                <div className="d-flex justify-content-center gap-4 text-muted">
                    <a href="#" className="text-decoration-none hover-white">Documentation</a>
                    <a href="#" className="text-decoration-none hover-white">Terms of Service</a>
                    <a href="#" className="text-decoration-none hover-white">Privacy Policy</a>
                </div>
            </footer>
        </div>
    );
};

window.HomePageEnterprise = HomePageEnterprise;
