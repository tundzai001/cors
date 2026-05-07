/**
 * ApiDocs Component
 * Professional developer documentation for Aitogy GNSS Platform.
 */
const ApiDocs = () => {
    const endpoints = [
        {
            method: 'GET',
            path: '/api/public/stations',
            desc: 'Lấy danh sách toàn bộ trạm CORS và trạng thái trực tuyến.',
            params: [
                { name: 'status', type: 'string', desc: 'Lọc theo online/offline' }
            ],
            response: `[
  {
    "serial": "HNI001",
    "name": "Station Ha Noi 01",
    "status": "online",
    "lat": 21.0285,
    "lon": 105.8542
  }
]`
        },
        {
            method: 'GET',
            path: '/api/public/stats',
            desc: 'Lấy số liệu thống kê thời gian thực của mạng lưới.',
            params: [],
            response: `{
  "total_stations": 150,
  "active_nodes": 142,
  "avg_latency": 45,
  "last_update": 1715065000
}`
        }
    ];

    return (
        <div className="container py-5 animate__animated animate__fadeIn">
            <div className="text-center mb-5">
                <h1 className="display-4 fw-bold">Developer Portal</h1>
                <p className="text-muted">Tích hợp dữ liệu CORS RTK vào ứng dụng của bạn một cách dễ dàng.</p>
            </div>

            <div className="row">
                <div className="col-lg-4">
                    <div className="glass-card p-4 sticky-top" style={{top: '100px'}}>
                        <h5 className="mb-4">Hướng dẫn chung</h5>
                        <p className="small text-muted">Mọi yêu cầu API cần được gửi tới <code>https://cors.aitogy.com</code>. Chúng tôi hỗ trợ định dạng JSON cho mọi phản hồi.</p>
                        <hr className="border-secondary opacity-25" />
                        <div className="d-grid gap-2">
                            <button className="btn btn-outline-primary btn-sm rounded-pill">Tải OpenAPI Spec</button>
                            <button className="btn btn-outline-secondary btn-sm rounded-pill">Liên hệ hỗ trợ Dev</button>
                        </div>
                    </div>
                </div>

                <div className="col-lg-8">
                    {endpoints.map((ep, idx) => (
                        <div key={idx} className="glass-card mb-4 p-0 overflow-hidden">
                            <div className="p-4 border-bottom border-secondary border-opacity-10 d-flex align-items-center justify-content-between">
                                <div className="d-flex align-items-center">
                                    <span className={`badge ${ep.method === 'GET' ? 'bg-success' : 'bg-primary'} me-3`}>{ep.method}</span>
                                    <code className="h5 mb-0 text-white">{ep.path}</code>
                                </div>
                                <span className="small text-muted">{ep.desc}</span>
                            </div>
                            <div className="p-4 bg-black bg-opacity-20">
                                <h6 className="text-uppercase small tracking-widest text-muted mb-3">Parameters</h6>
                                {ep.params.length > 0 ? (
                                    <table className="table table-dark table-borderless small">
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
                                ) : <p className="small text-muted">No parameters required.</p>}

                                <h6 className="text-uppercase small tracking-widest text-muted mt-4 mb-3">Example Response</h6>
                                <pre className="p-3 rounded bg-dark border border-secondary border-opacity-25" style={{fontSize: '0.8rem'}}>
                                    <code className="text-info">{ep.response}</code>
                                </pre>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

window.ApiDocs = ApiDocs;
