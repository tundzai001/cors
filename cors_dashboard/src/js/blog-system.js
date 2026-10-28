/**
 * BlogSystem Component
 * Technical knowledge base for SEO and community authority.
 */
const BlogSystem = ({ slug }) => {
    React.useEffect(() => {
        if (typeof gsap === 'undefined') return;
        if (typeof ScrollTrigger !== 'undefined') gsap.registerPlugin(ScrollTrigger);

        const ctx = gsap.context(() => {
            gsap.from('.blog-hero', { y: 34, autoAlpha: 0, duration: 0.6, ease: 'power3.out' });
            gsap.from('.blog-card', { y: 42, autoAlpha: 0, scale: 0.97, stagger: 0.08, duration: 0.55, ease: 'power3.out' });
            gsap.utils.toArray('.blog-anim-block').forEach((el) => {
                gsap.from(el, {
                    y: 28,
                    autoAlpha: 0,
                    duration: 0.6,
                    ease: 'power3.out',
                    scrollTrigger: { trigger: el, start: 'top 86%', toggleActions: 'play none none none', once: true }
                });
            });
        });

        return () => ctx.revert();
    }, [slug]);

    const posts = [
        {
            slug: 'ntrip-caster-la-gi',
            title: 'NTRIP Caster là gì? Toàn tập về pipeline RTK',
            excerpt: 'Giải thích kiến trúc NTRIP Caster, luồng dữ liệu RTCM, mô hình phân phối mountpoint và các lỗi hay gặp khi triển khai.',
            date: '2025-05-07',
            readTime: '8 phút',
            category: 'Kiến trúc hệ thống',
            content: `
                <h2>Từ Base đến Rover: chuỗi xử lý đầy đủ</h2>
                <p>Dữ liệu GNSS thô tại trạm Base được chuẩn hóa theo RTCM, đẩy lên Caster, sau đó phân phối về Rover theo mountpoint phù hợp khu vực vận hành.</p>
                <h3>Điểm nghẽn thường xuất hiện ở đâu?</h3>
                <p>Trong thực tế, nút thắt thường nằm ở mạng truyền dẫn (jitter cao), cấu hình reconnect chưa tối ưu, hoặc phân bổ mountpoint chưa đúng mật độ rover.</p>
                <h3>Checklist vận hành ổn định</h3>
                <ul>
                    <li>Tách cụm mountpoint theo vùng địa lý</li>
                    <li>Giám sát packet loss, latency và số rover đồng thời</li>
                    <li>Dự phòng Caster để failover nhanh khi tải tăng đột biến</li>
                </ul>
            `
        },
        {
            slug: 'huong-dan-ket-noi-cors',
            title: 'Hướng dẫn kết nối Base cho Drone và máy thi công',
            excerpt: 'Quy trình cấu hình tài khoản NTRIP, kiểm tra FIX, tối ưu độ trễ và xử lý các lỗi mất fix khi làm việc ngoài thực địa.',
            date: '2025-05-06',
            readTime: '6 phút',
            category: 'Hướng dẫn triển khai',
            content: `
                <h2>Thiết lập kết nối theo từng bước</h2>
                <p>Ưu tiên kiểm tra chất lượng mạng 4G/5G trước khi xác nhận cấu hình NTRIP để tránh lỗi FIX giả do truyền dẫn không ổn định.</p>
                <h3>Thông số nên giám sát liên tục</h3>
                <ul>
                    <li>Latency trung bình dưới 200ms</li>
                    <li>Số vệ tinh theo từng chòm GPS/GLO/GAL/BDS</li>
                    <li>Chu kỳ mất FIX và thời gian phục hồi</li>
                </ul>
                <h3>Khi nào cần đổi mountpoint?</h3>
                <p>Khi rover dịch chuyển xa khỏi vùng phủ tối ưu hoặc chất lượng hiệu chỉnh giảm, nên chuyển mountpoint gần cụm trạm Base hơn để ổn định nghiệm.</p>
            `
        },
        {
            slug: 'giam-sat-snr-rtk',
            title: 'Giám sát SNR và trạng thái RTK để giảm downtime',
            excerpt: 'Mô hình cảnh báo sớm dựa trên SNR, latency và packet loss giúp đội vận hành can thiệp trước khi rover mất FIX hàng loạt.',
            date: '2025-05-02',
            readTime: '7 phút',
            category: 'Vận hành & giám sát',
            content: `
                <h2>Vì sao chỉ nhìn trạng thái Online là chưa đủ?</h2>
                <p>Trạm có thể Online nhưng chất lượng hiệu chỉnh đã suy giảm. Cần phối hợp SNR, latency và tỉ lệ reconnect để đánh giá đúng health vận hành.</p>
                <h3>Ngưỡng cảnh báo đề xuất</h3>
                <ul>
                    <li>SNR trung bình giảm liên tục trong 10-15 phút</li>
                    <li>Latency tăng > 250ms theo cụm rover</li>
                    <li>Số lần reconnect tăng bất thường trong 1 giờ</li>
                </ul>
            `
        },
        {
            slug: 'bao-mat-truyen-dan-cors',
            title: 'Bảo mật truyền dẫn Base: TLS, phân quyền và audit',
            excerpt: 'Thiết kế lớp bảo mật cho hạ tầng Base theo hướng thực dụng: mã hóa, tách quyền và theo dõi truy cập có truy vết.',
            date: '2025-04-28',
            readTime: '9 phút',
            category: 'Bảo mật hạ tầng',
            content: `
                <h2>Tối thiểu cần những lớp bảo mật nào?</h2>
                <p>Một hệ Base production cần có TLS đầu cuối, kiểm soát quyền theo vai trò và nhật ký truy cập đủ chi tiết để phục vụ điều tra sự cố.</p>
                <h3>Nguyên tắc phân quyền</h3>
                <ul>
                    <li>Ẩn định danh thiết bị nhạy cảm với người dùng public</li>
                    <li>Tách quyền Viewer / Operator / Admin rõ ràng</li>
                    <li>Luôn có audit trail cho thao tác thay đổi cấu hình</li>
                </ul>
            `
        }
    ];

    const currentPost = posts.find((p) => p.slug === slug);
    const related = currentPost ? posts.filter((p) => p.slug !== currentPost.slug).slice(0, 3) : [];

    if (slug && currentPost) {
        return (
            <div className="container py-5 blog-shell">
                <nav aria-label="breadcrumb" className="mb-4 blog-hero">
                    <ol className="breadcrumb">
                        <li className="breadcrumb-item"><a href="/blog" className="text-primary text-decoration-none">Bài viết</a></li>
                        <li className="breadcrumb-item active text-light">{currentPost.title}</li>
                    </ol>
                </nav>

                <article className="blog-article blog-anim-block">
                    <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                        <span className="badge bg-primary">{currentPost.category}</span>
                        <span className="badge bg-secondary bg-opacity-25 text-light">{currentPost.readTime}</span>
                    </div>
                    <h1 className="display-5 fw-bold mb-3">{currentPost.title}</h1>
                    <div className="text-muted small mb-4">Đăng ngày {currentPost.date} by Aitogy Engineering Team</div>
                    <div className="blog-body" dangerouslySetInnerHTML={{ __html: currentPost.content }} />

                    <div className="blog-cta glass-card mt-5 p-4 text-center border-primary">
                        <h4>Tư vấn kiến trúc Base theo khu vực triển khai</h4>
                        <p className="text-muted">Đội kỹ thuật Aitogy hỗ trợ dựng luồng NTRIP, mountpoint và phương án giám sát theo tải thực tế.</p>
                        <a href="https://aitogy.com" target="_blank" className="btn btn-enterprise">Làm việc với đội triển khai</a>
                    </div>
                </article>

                <div className="row g-4 mt-1">
                    <div className="col-lg-6">
                        <div className="blog-side-card blog-anim-block">
                            <h5 className="mb-2">Checklist trước khi nghiệm thu</h5>
                            <p className="mb-0 text-muted">Xác thực ổn định FIX theo ca làm việc dài, kiểm tra tình huống chuyển vùng, và đánh giá tải đồng thời theo kịch bản cao điểm.</p>
                        </div>
                    </div>
                    <div className="col-lg-6">
                        <div className="blog-side-card blog-anim-block">
                            <h5 className="mb-2">Bài liên quan</h5>
                            {related.map((post) => (
                                <a key={post.slug} href={`/blog/${post.slug}`} className="d-block text-decoration-none mb-2">
                                    <span className="text-primary">{post.title}</span>
                                </a>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="container py-5 blog-shell">
            <div className="text-center mb-5 blog-hero">
                <h1 className="display-4 fw-bold mb-2">Kiến thức Base Aitogy</h1>
                <p className="text-muted mb-0">Kiến thức GNSS/RTK thực chiến cho triển khai Base quy mô lớn.</p>
            </div>

            <div className="row g-4 mb-4">
                {posts.map((post) => (
                    <div key={post.slug} className="col-lg-6">
                        <div className="blog-card h-100 d-flex flex-column">
                            <div className="d-flex align-items-center justify-content-between mb-3">
                                <span className="badge text-bg-primary">{post.category}</span>
                                <span className="text-muted small">{post.readTime}</span>
                            </div>
                            <h2 className="h3 fw-bold mb-3"><a href={`/blog/${post.slug}`} className="link-light link-underline-opacity-0 link-underline-opacity-100-hover">{post.title}</a></h2>
                            <p className="text-muted flex-grow-1">{post.excerpt}</p>
                            <div className="mt-3 text-muted small">{post.date}</div>
                            <div className="mt-3">
                                <a href={`/blog/${post.slug}`} className="text-primary text-decoration-none fw-bold">
                                    Đọc tiếp <i className="bi bi-arrow-right ms-1"></i>
                                </a>
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            <div className="row g-4">
                <div className="col-lg-4">
                    <div className="blog-side-card blog-anim-block">
                        <h5>Series đề xuất</h5>
                        <p className="text-muted mb-0">Từ khảo sát hiện trạng, dựng hạ tầng Caster đến giám sát production cho mạng Base nhiều khu vực.</p>
                    </div>
                </div>
                <div className="col-lg-4">
                    <div className="blog-side-card blog-anim-block">
                        <h5>Case thực tế</h5>
                        <p className="text-muted mb-0">Tối ưu luồng truyền cho dự án có rover di chuyển liên tục, đảm bảo FIX ổn định trong môi trường nhiễu cao.</p>
                    </div>
                </div>
                <div className="col-lg-4">
                    <div className="blog-side-card blog-anim-block">
                        <h5>Ops Notes</h5>
                        <p className="text-muted mb-0">Mẫu dashboard SNR, latency và reconnect để đội vận hành theo dõi health theo ca và xử lý chủ động.</p>
                    </div>
                </div>
            </div>
        </div>
    );
};

window.BlogSystem = BlogSystem;
