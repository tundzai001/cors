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
            path: '/api/integrations/stations',
            desc: 'Chỉ trả các trạm và trường dữ liệu được cấp cho API key.',
            params: [],
            response: `[
  {
    "serial": "ST001",
    "name": "HP.HaiAn",
    "status": "sleep",
    "lat": 20.838680,
    "lon": 106.723684
  }
]`
        },
        {
            method: 'POST',
            path: '/api/integrations/stations/{serial}/configure-service',
            desc: 'Sửa cấu hình dịch vụ khi key có quyền và được cấp trạm.',
            params: [{ name: 'serial', type: 'string', desc: 'Serial trạm được cấp quyền sửa' }],
            response: `{ "status": "service_config_sent_and_saved" }`
        }
    ];

    const quickstart = [
        'Admin tạo key tại trang API Keys và chọn thời hạn, trạm, dữ liệu, quyền sửa.',
        'Gửi Authorization: Bearer <key> từ backend tích hợp.',
        'Gọi /api/integrations/stations để nhận dữ liệu theo quyền hiện tại của key.',
        'Key hết hạn theo thời gian thực và có thể bị thu hồi bất cứ lúc nào.'
    ];

    return (
        <div className="container py-5 api-shell">
            <div className="text-center mb-5 api-hero">
                <h1 className="display-4 fw-bold">Developer Portal</h1>
                <p className="text-muted mb-0">API cho tích hợp giám sát Base, trạng thái mạng và dữ liệu vận hành GNSS theo thời gian thực.</p>
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
                        <code>https://base.aitogy.com</code>
                        <h6 className="small text-uppercase text-muted mt-4 mb-2">Auth</h6>
                        <code>Authorization: Bearer &lt;token&gt;</code>
                    </aside>
                </div>

                <div className="col-lg-8">
                    <div className="api-endpoint mb-4 p-4">
                        <h5 className="mb-2">Versioning & Stability</h5>
                        <p className="text-muted mb-0">Quyền và thời hạn được kiểm tra ở backend trên mỗi request. Chỉnh quyền không cần đổi token.</p>
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
