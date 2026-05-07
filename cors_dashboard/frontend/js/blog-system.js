/**
 * BlogSystem Component
 * Technical knowledge base for SEO and community authority.
 */
const BlogSystem = ({ slug }) => {
    const posts = [
        {
            slug: 'ntrip-caster-la-gi',
            title: 'NTRIP Caster là gì? Hướng dẫn toàn tập về giao thức RTK',
            excerpt: 'Tìm hiểu về NTRIP Caster, vai trò của nó trong mạng lưới CORS và cách kết nối đầu thu GNSS...',
            date: '2025-05-07',
            category: 'Kỹ thuật GNSS',
            content: `
                <h2>Giao thức NTRIP và vai trò của Caster</h2>
                <p>NTRIP (Networked Transport of RTCM via Internet Protocol) là một giao thức chuẩn để truyền dữ liệu hiệu chỉnh GNSS (RTCM) qua Internet. NTRIP Caster đóng vai trò như một máy chủ trung gian...</p>
                <h3>Tại sao cần trạm CORS chất lượng cao?</h3>
                <p>Một mạng lưới CORS (Continuously Operating Reference Stations) ổn định đảm bảo rằng dữ liệu hiệu chỉnh luôn sẵn sàng 24/7 với độ trễ thấp nhất...</p>
                <div class="blog-cta glass-card mt-5 p-4 text-center border-primary">
                    <h4>Bạn muốn xây dựng mạng lưới CORS riêng?</h4>
                    <p class="text-muted">Aitogy cung cấp giải pháp trạm tham chiếu và phần mềm Caster chuyên nghiệp hàng đầu Việt Nam.</p>
                    <a href="https://aitogy.com" target="_blank" class="btn btn-enterprise">Khám phá giải pháp tại Aitogy.com</a>
                </div>
            `
        },
        {
            slug: 'huong-dan-ket-noi-cors',
            title: 'Cách kết nối trạm CORS Aitogy cho Drone và Máy công trình',
            excerpt: 'Hướng dẫn chi tiết từng bước cấu hình tài khoản NTRIP để nhận tín hiệu hiệu chỉnh RTK...',
            date: '2025-05-06',
            category: 'Hướng dẫn',
            content: `
                <h2>Kết nối RTK cho Drone (UAV)</h2>
                <p>Để đạt độ chính xác centimet trong khảo sát địa hình bằng Drone, việc kết nối với một trạm CORS ổn định là điều bắt buộc. Quy trình bao gồm...</p>
                <div class="blog-cta glass-card mt-5 p-4 text-center border-primary">
                    <h4>Nâng tầm độ chính xác cho công trình của bạn</h4>
                    <p class="text-muted">Sử dụng tài khoản CORS Aitogy để tận hưởng độ phủ sóng rộng và tín hiệu ổn định nhất.</p>
                    <a href="https://aitogy.com" target="_blank" class="btn btn-enterprise">Xem bảng giá dịch vụ tại Aitogy.com</a>
                </div>
            `
        }
    ];

    const currentPost = posts.find(p => p.slug === slug);

    if (slug && currentPost) {
        return (
            <div className="container py-5 animate__animated animate__fadeIn">
                <nav aria-label="breadcrumb" className="mb-4">
                    <ol className="breadcrumb">
                        <li className="breadcrumb-item"><a href="#" onClick={() => window.switchView('blog')} className="text-primary text-decoration-none">Blog</a></li>
                        <li className="breadcrumb-item active text-light">{currentPost.title}</li>
                    </ol>
                </nav>
                <div className="glass-card p-5">
                    <span className="badge bg-primary mb-3">{currentPost.category}</span>
                    <h1 className="display-5 fw-bold mb-4">{currentPost.title}</h1>
                    <div className="text-muted small mb-5">Đăng ngày {currentPost.date} by Aitogy Engineering Team</div>
                    <div className="blog-body" dangerouslySetInnerHTML={{ __html: currentPost.content }}></div>
                </div>
            </div>
        );
    }

    return (
        <div className="container py-5 animate__animated animate__fadeIn">
            <h1 className="display-4 fw-bold mb-5 text-center">Technical Knowledge Base</h1>
            <div className="row g-4">
                {posts.map((post, idx) => (
                    <div key={idx} className="col-lg-6">
                        <div className="glass-card h-100 d-flex flex-column">
                            <span className="badge bg-primary bg-opacity-10 text-primary w-fit mb-3">{post.category}</span>
                            <h3 className="fw-bold mb-3">{post.title}</h3>
                            <p className="text-muted flex-grow-1">{post.excerpt}</p>
                            <div className="mt-4">
                                <a href={`/blog/${post.slug}`} className="text-primary text-decoration-none fw-bold" onClick={(e) => {
                                    e.preventDefault();
                                    window.history.pushState({}, '', `/blog/${post.slug}`);
                                    window.renderBlog(post.slug);
                                }}>
                                    Đọc tiếp <i className="bi bi-arrow-right ms-1"></i>
                                </a>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

window.BlogSystem = BlogSystem;
