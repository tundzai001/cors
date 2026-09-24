/**
 * ApiDocs Component
 * Professional developer documentation for Aitogy GNSS Platform.
 */
const ApiDocs = () => {
    React.useEffect(() => {
        if (typeof gsap === 'undefined') return;
        if (typeof ScrollTrigger !== 'undefined') gsap.registerPlugin(ScrollTrigger);

        const ctx = gsap.context(() => {
            gsap.from('.api-hero', { y: 34, autoAlpha: 0, duration: 0.6, ease: 'power3.out' });
            gsap.from('.api-sidebar', { x: -28, autoAlpha: 0, duration: 0.65, ease: 'power3.out' });
            gsap.utils.toArray('.api-endpoint').forEach((el, i) => {
                gsap.from(el, {
                    y: 34,
                    autoAlpha: 0,
                    duration: 0.55,
                    delay: i * 0.04,
                    ease: 'power3.out',
                    scrollTrigger: { trigger: el, start: 'top 86%', toggleActions: 'play none none none', once: true }
                });
            });
        });

        return () => ctx.revert();
    }, []);

    const endpoints = [
        {
            method: 'GET',
            path: '/api/public/stations',
            desc: 'Danh sách trạm CORS public (không lộ thông tin nhạy cảm).',
            params: [
                { name: 'status', type: 'string', desc: 'online | offline | sleep' },
                { name: 'province', type: 'string', desc: 'Lọc theo tỉnh/thành' }
            ],
            response: `[
  {
    "name": "HP.HaiAn",
    "status": "sleep",
    "lat": 20.838680,
    "lon": 106.723684
  }
]`
        },
        {
            method: 'GET',
            path: '/api/public/stats',
            desc: 'Chỉ số realtime của toàn mạng CORS.',
            params: [],
            response: `{
  "total_stations": 150,
  "active_nodes": 142,
  "avg_latency_ms": 45,
  "uptime_24h": 99.98
}`
        },
        {
            method: 'GET',
            path: '/api/public/network-health',
            desc: 'Tổng hợp chất lượng theo vùng và theo giao thức truyền.',
            params: [
                { name: 'window', type: 'string', desc: '15m | 1h | 24h' }
            ],
            response: `{
  "window": "1h",
  "regions": [
    {"name": "North", "latency_ms": 43, "packet_loss": 0.12},
    {"name": "South", "latency_ms": 48, "packet_loss": 0.18}
  ]
}`
        }
    ];

    const quickstart = [
        'Đăng ký tài khoản và lấy API token theo môi trường (staging/production).',
        'Gọi /api/public/stations để lấy danh sách trạm khả dụng theo khu vực.',
        'Theo dõi /api/public/stats và /api/public/network-health để hiển thị trạng thái realtime.',
        'Cache dữ liệu read-heavy theo chu kỳ 10-30 giây để giảm tải client.'
    ];

    return (
        <div className="container py-5 api-shell">
            <div className="text-center mb-5 api-hero">
                <h1 className="display-4 fw-bold">Developer Portal</h1>
                <p className="text-muted mb-0">API cho tích hợp giám sát CORS, trạng thái mạng và dữ liệu vận hành GNSS theo thời gian thực.</p>
            </div>

            <div className="row g-4">
                <div className="col-lg-4">
                    <aside className="api-sidebar p-4 sticky-top" style={{ top: '100px' }}>
                        <h5 className="mb-3">Quickstart</h5>
                        <ol className="small text-muted ps-3 mb-4">
                            {quickstart.map((item, idx) => <li key={idx} className="mb-2">{item}</li>)}
                        </ol>
                        <hr className="border-secondary opacity-25" />
                        <h6 className="small text-uppercase text-muted mb-2">Base URL</h6>
                        <code>https://cors.aitogy.com</code>
                        <h6 className="small text-uppercase text-muted mt-4 mb-2">Auth</h6>
                        <code>Authorization: Bearer &lt;token&gt;</code>
                        <div className="d-grid gap-2 mt-4">
                            <button className="btn btn-outline-primary btn-sm rounded-pill">Tải OpenAPI Spec</button>
                            <button className="btn btn-outline-secondary btn-sm rounded-pill">Liên hệ hỗ trợ Dev</button>
                        </div>
                    </aside>
                </div>

                <div className="col-lg-8">
                    <div className="api-endpoint mb-4 p-4">
                        <h5 className="mb-2">Versioning & Stability</h5>
                        <p className="text-muted mb-0">Public API ưu tiên tương thích ngược. Thay đổi breaking sẽ được thông báo trước và có giai đoạn song song phiên bản.</p>
                    </div>

                    {endpoints.map((ep, idx) => (
                        <div key={idx} className="api-endpoint mb-4 p-0 overflow-hidden">
                            <div className="p-4 border-bottom border-secondary border-opacity-10 d-flex align-items-start justify-content-between api-endpoint-head">
                                <div className="d-flex align-items-center">
                                    <span className={`badge ${ep.method === 'GET' ? 'bg-success' : 'bg-primary'} me-3`}>{ep.method}</span>
                                    <code className="h5 mb-0 text-white">{ep.path}</code>
                                </div>
                                <span className="small text-muted ms-3">{ep.desc}</span>
                            </div>
                            <div className="p-4 api-endpoint-body">
                                <h6 className="text-uppercase small tracking-widest text-muted mb-3">Parameters</h6>
                                {ep.params.length > 0 ? (
                                    <table className="table table-dark table-borderless small mb-0">
                                        <thead>
                                            <tr className="text-muted border-bottom border-secondary border-opacity-10">
                                                <th>Name</th>
                                                <th>Type</th>
                                                <th>Description</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {ep.params.map((p, pidx) => (
                                                <tr key={pidx}>
                                                    <td><code>{p.name}</code></td>
                                                    <td>{p.type}</td>
                                                    <td>{p.desc}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                ) : <p className="small text-muted mb-0">No parameters required.</p>}

                                <h6 className="text-uppercase small tracking-widest text-muted mt-4 mb-3">Example Response</h6>
                                <pre className="p-3 rounded bg-dark border border-secondary border-opacity-25" style={{ fontSize: '0.8rem' }}>
                                    <code className="text-info">{ep.response}</code>
                                </pre>
                            </div>
                        </div>
                    ))}

                    <div className="api-endpoint p-4">
                        <h5 className="mb-2">Error Handling</h5>
                        <p className="text-muted mb-2">Các mã lỗi phổ biến: <code>401</code>, <code>403</code>, <code>404</code>, <code>429</code>, <code>500</code>.</p>
                        <p className="text-muted mb-0">Client nên implement retry có backoff cho lỗi tạm thời và log request-id để truy vết với đội vận hành.</p>
                    </div>
                </div>
            </div>
        </div>
    );
};

window.ApiDocs = ApiDocs;
