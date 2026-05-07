/**
 * HomePageEnterprise Component - Master Pro Landing
 * GNSS/Satellite themed with Vietnam S-shape, inline login
 */

/* Vietnam Map - Real geographic outline as subtle background */
const VietnamSVG = () => (
    React.createElement('img', {
        src: '/img/vietnam-outline.svg',
        alt: '',
        className: 'vietnam-map-bg',
        'aria-hidden': 'true'
    })
);

/* Particle Background */
const ParticleField = () => {
    const particles = Array.from({ length: 30 }, (_, i) => ({
        left: `${Math.random() * 100}%`,
        delay: `${Math.random() * 14}s`,
        duration: `${Math.random() * 8 + 12}s`,
        size: Math.random() * 2 + 1
    }));
    return React.createElement('div', { className: 'landing-particles' },
        particles.map((p, i) =>
            React.createElement('div', {
                key: i, className: 'landing-particle',
                style: {
                    left: p.left,
                    animationDelay: p.delay,
                    animationDuration: p.duration,
                    width: p.size + 'px',
                    height: p.size + 'px'
                }
            })
        )
    );
};

/* Animated Counter Hook */
const useCounter = (target, duration = 1500) => {
    const [count, setCount] = React.useState(0);
    React.useEffect(() => {
        if (target === 0) return;
        const numTarget = parseInt(target) || 0;
        const step = Math.max(1, Math.ceil(numTarget / (duration / 30)));
        let current = 0;
        const timer = setInterval(() => {
            current += step;
            if (current >= numTarget) {
                setCount(numTarget);
                clearInterval(timer);
            } else {
                setCount(current);
            }
        }, 30);
        return () => clearInterval(timer);
    }, [target]);
    return count;
};

/* Login Panel Component */
const LoginPanel = ({ isOpen, onClose }) => {
    const [username, setUsername] = React.useState('');
    const [password, setPassword] = React.useState('');
    const [remember, setRemember] = React.useState(false);
    const [error, setError] = React.useState('');
    const [loading, setLoading] = React.useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!username.trim() || !password) {
            setError('Vui lòng nhập đầy đủ thông tin!');
            return;
        }
        setLoading(true);
        setError('');
        try {
            const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: username.trim(), password, remember_me: remember })
            });
            const data = await res.json();
            if (!res.ok) {
                let msg = data.detail;
                if (Array.isArray(msg)) msg = msg.map(e => `${(e.loc||[]).join('.')}: ${e.msg}`).join('\n');
                else if (typeof msg === 'object') msg = JSON.stringify(msg);
                throw new Error(msg || 'Đăng nhập thất bại');
            }
            if (remember) {
                localStorage.setItem('access_token', data.access_token);
                sessionStorage.removeItem('access_token');
            } else {
                sessionStorage.setItem('access_token', data.access_token);
                localStorage.removeItem('access_token');
            }
            window.location.href = '/';
        } catch (err) {
            setError(err.message);
            setLoading(false);
        }
    };

    return React.createElement(React.Fragment, null,
        React.createElement('div', {
            className: `login-overlay ${isOpen ? 'active' : ''}`,
            onClick: onClose
        }),
        React.createElement('div', { className: `login-panel ${isOpen ? 'active' : ''}` },
            React.createElement('button', {
                className: 'login-panel-close', onClick: onClose,
                type: 'button'
            }, '✕'),
            React.createElement('div', { className: 'login-panel-header' },
                React.createElement('img', {
                    src: '/img/aitogy.png', alt: 'Aitogy',
                    className: 'login-panel-logo'
                }),
                React.createElement('div', { className: 'login-panel-title' }, 'CORS CONTROL CENTER')
            ),
            React.createElement('div', { className: 'login-panel-body' },
                error && React.createElement('div', { className: 'login-error' },
                    React.createElement('i', { className: 'bi bi-exclamation-triangle-fill me-2' }),
                    error
                ),
                React.createElement('form', { onSubmit: handleSubmit },
                    React.createElement('div', { className: 'login-input-group' },
                        React.createElement('i', { className: 'bi bi-person-fill login-input-icon' }),
                        React.createElement('input', {
                            type: 'text', className: 'login-input',
                            placeholder: 'Tên đăng nhập',
                            value: username, onChange: e => setUsername(e.target.value),
                            autoComplete: 'username'
                        })
                    ),
                    React.createElement('div', { className: 'login-input-group' },
                        React.createElement('i', { className: 'bi bi-lock-fill login-input-icon' }),
                        React.createElement('input', {
                            type: 'password', className: 'login-input',
                            placeholder: 'Mật khẩu',
                            value: password, onChange: e => setPassword(e.target.value),
                            autoComplete: 'current-password'
                        })
                    ),
                    React.createElement('div', {
                        className: 'd-flex align-items-center mb-3',
                        style: { gap: '8px' }
                    },
                        React.createElement('input', {
                            type: 'checkbox', id: 'rememberMeInline',
                            checked: remember, onChange: e => setRemember(e.target.checked),
                            style: { accentColor: '#00f2ff' }
                        }),
                        React.createElement('label', {
                            htmlFor: 'rememberMeInline',
                            style: { color: '#8892b0', fontSize: '0.85rem', cursor: 'pointer' }
                        }, 'Ghi nhớ đăng nhập')
                    ),
                    React.createElement('button', {
                        type: 'submit', className: 'login-submit-btn',
                        disabled: loading
                    }, loading
                        ? React.createElement('span', null,
                            React.createElement('span', { className: 'spinner-border spinner-border-sm me-2' }),
                            'Đang xử lý...'
                        )
                        : React.createElement('span', null,
                            React.createElement('i', { className: 'bi bi-box-arrow-in-right me-2' }),
                            'Đăng nhập'
                        )
                    )
                )
            ),
            React.createElement('div', { className: 'login-panel-footer' },
                React.createElement('a', {
                    href: 'https://aitogy.com', target: '_blank',
                    className: 'login-promo'
                },
                    React.createElement('div', {
                        style: {
                            width: '36px', height: '36px', borderRadius: '10px',
                            background: 'rgba(0, 242, 255, 0.1)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            color: '#00f2ff', flexShrink: 0
                        }
                    }, React.createElement('i', { className: 'bi bi-megaphone-fill' })),
                    React.createElement('div', null,
                        React.createElement('div', {
                            style: { color: '#fff', fontWeight: 600, fontSize: '0.82rem' }
                        }, 'Giải pháp GNSS toàn diện'),
                        React.createElement('div', {
                            style: { color: '#6a8aaa', fontSize: '0.72rem' }
                        }, 'Thiết bị và hạ tầng CORS chính xác cao')
                    )
                ),
                React.createElement('div', { className: 'login-company-info' },
                    React.createElement('strong', null, '© 2025 Aitogy., JSC'),
                    React.createElement('br'),
                    'Công ty CP Công nghệ Hạ tầng Cơ sở AITOGY',
                    React.createElement('br'),
                    'Địa chỉ: Số 16B Ngõ 187 Mai Dịch, Cầu Giấy, Hà Nội'
                )
            )
        )
    );
};

/* Main Homepage Component */
const HomePageEnterprise = ({ devices }) => {
    const [loginOpen, setLoginOpen] = React.useState(false);

    // Count active = online + sleep + streaming + starting (all non-offline)
    const activeStatuses = ['online', 'sleep', 'streaming', 'starting'];
    const onlineCount = devices.filter(d => activeStatuses.includes(d.status)).length;
    const totalSats = devices.reduce((acc, d) => acc + (d.system_info?.satellites || 0), 0);
    const avgLatency = devices.length > 0
        ? Math.round(devices.reduce((acc, d) => acc + (d.system_info?.latency || 0), 0) / devices.length)
        : 0;

    const animStations = useCounter(devices.length);
    const animOnline = useCounter(onlineCount);
    const animSats = useCounter(totalSats);

    const stats = [
        { label: 'Tổng số trạm', value: animStations, sub: 'Phủ sóng toàn quốc', icon: 'bi-broadcast-pin', color: '#4facfe' },
        { label: 'Đang hoạt động', value: animOnline, sub: 'Online · Sleep · Streaming', icon: 'bi-check-circle-fill', color: '#51cf66' },
        { label: 'Vệ tinh theo dõi', value: animSats, sub: 'GPS · GLO · BDS · GAL', icon: 'bi-globe', color: '#cc5de8' },
        { label: 'Độ trễ TB', value: `${avgLatency}ms`, sub: 'Ultra-low latency', icon: 'bi-lightning-charge-fill', color: '#00f2ff' }
    ];

    const features = [
        { icon: 'bi-cpu', iconClass: 'feature-icon-blue', title: 'Đa chòm sao vệ tinh',
            desc: 'Theo dõi GPS, GLONASS, Galileo, BeiDou — tối đa hóa độ chính xác định vị trên toàn lãnh thổ Việt Nam.' },
        { icon: 'bi-wifi', iconClass: 'feature-icon-green', title: 'NTRIP Caster tốc độ cao',
            desc: 'Truyền dữ liệu RTK/RTCM tối ưu với giao thức độ trễ cực thấp, hỗ trợ hàng trăm rover đồng thời.' },
        { icon: 'bi-graph-up-arrow', iconClass: 'feature-icon-purple', title: 'Giám sát 24/7',
            desc: 'Tự động cảnh báo và phục hồi khi phát hiện sự cố, đảm bảo uptime tối đa cho mạng lưới trạm CORS.' },
        { icon: 'bi-shield-check', iconClass: 'feature-icon-cyan', title: 'Bảo mật cấp doanh nghiệp',
            desc: 'Quản lý phân quyền đa cấp, mã hóa dữ liệu end-to-end, bảo vệ hạ tầng đo đạc quốc gia.' }
    ];

    return React.createElement('div', { className: 'enterprise-homepage', style: { position: 'relative', minHeight: '100vh', color: '#e6f1ff', fontFamily: "'Inter', sans-serif" } },
        // === ANIMATED BACKGROUND ===
        React.createElement('div', { className: 'landing-bg' },
            React.createElement('div', { className: 'landing-grid' }),
            React.createElement('div', { className: 'landing-orb landing-orb-1' }),
            React.createElement('div', { className: 'landing-orb landing-orb-2' }),
            React.createElement('div', { className: 'landing-orb landing-orb-3' }),
            React.createElement(ParticleField),
            // Satellite orbit rings
            React.createElement('div', { className: 'satellite-orbit-ring orbit-ring-1' },
                React.createElement('div', { className: 'orbit-dot' })
            ),
            React.createElement('div', { className: 'satellite-orbit-ring orbit-ring-2' },
                React.createElement('div', { className: 'orbit-dot' })
            )
        ),

        // === NAVBAR ===
        React.createElement('nav', { className: 'landing-navbar', id: 'landing-navbar' },
            React.createElement('a', { href: '/', className: 'landing-nav-brand' },
                React.createElement('img', { src: '/img/aitogy.png', alt: 'Aitogy' }),
                React.createElement('div', null,
                    'Aitogy ',
                    React.createElement('span', null, 'CORS Network')
                )
            ),
            React.createElement('div', { className: 'landing-nav-actions' },
                React.createElement('a', {
                    href: '/network-status',
                    className: 'landing-nav-link'
                }, 'Network'),
                React.createElement('a', {
                    href: '/blog',
                    className: 'landing-nav-link'
                }, 'Blog'),
                React.createElement('a', {
                    href: '/api/docs',
                    className: 'landing-nav-link'
                }, 'API Docs'),
                React.createElement('button', {
                    className: 'btn-login-nav',
                    onClick: () => setLoginOpen(true)
                },
                    React.createElement('i', { className: 'bi bi-person-circle me-2' }),
                    'Đăng nhập'
                )
            )
        ),

        // === HERO SECTION ===
        React.createElement('section', { className: 'hero-section' },
            React.createElement(VietnamSVG),
            React.createElement('div', { className: 'container position-relative', style: { zIndex: 2 } },
                React.createElement('div', { className: 'hero-badge' },
                    React.createElement('i', { className: 'bi bi-satellite' }),
                    ' NEXT-GEN GNSS INFRASTRUCTURE'
                ),
                React.createElement('h1', { className: 'hero-title' },
                    'Aitogy Network',
                    React.createElement('br'),
                    React.createElement('span', { className: 'hero-title-gradient' }, 'CORS Control Center')
                ),
                React.createElement('p', { className: 'hero-subtitle' },
                    'Hệ thống quản lý và giám sát mạng lưới trạm định vị vệ tinh CORS trên toàn lãnh thổ Việt Nam. ',
                    'Cung cấp hạ tầng RTK/NTRIP chuyên nghiệp cho khảo sát, đo đạc và tự động hóa.'
                ),
                React.createElement('div', { className: 'hero-actions' },
                    React.createElement('button', {
                        className: 'btn-enterprise',
                        onClick: () => setLoginOpen(true)
                    },
                        'Truy cập hệ thống ',
                        React.createElement('i', { className: 'bi bi-arrow-right ms-2' })
                    ),
                    React.createElement('button', {
                        className: 'btn-outline-cyan',
                        onClick: () => window.openPublicMap && window.openPublicMap()
                    },
                        React.createElement('i', { className: 'bi bi-globe-asia-australia me-2' }),
                        'Xem bản đồ trạm'
                    )
                )
            )
        ),

        // === STATS ===
        React.createElement('section', { className: 'landing-stats container' },
            React.createElement('div', { className: 'row g-3' },
                stats.map((stat, idx) =>
                    React.createElement('div', { key: idx, className: 'col-lg-3 col-md-6 col-6' },
                        React.createElement('div', { className: 'landing-stat-card' },
                            React.createElement('div', {
                                className: 'landing-stat-icon',
                                style: { color: stat.color, background: `${stat.color}15`, borderColor: `${stat.color}30` }
                            },
                                React.createElement('i', { className: `bi ${stat.icon}` })
                            ),
                            React.createElement('div', { className: 'landing-stat-label' }, stat.label),
                            React.createElement('div', { className: 'landing-stat-value' }, stat.value),
                            React.createElement('div', { className: 'landing-stat-sub' }, stat.sub)
                        )
                    )
                )
            )
        ),

        // === FEATURES ===
        React.createElement('section', { className: 'landing-features container' },
            React.createElement('div', { className: 'text-center mb-5' },
                React.createElement('h2', {
                    style: { fontSize: '2rem', fontWeight: 800, color: '#fff', letterSpacing: '-1px' }
                }, 'Tính năng nổi bật'),
                React.createElement('p', {
                    style: { color: '#6a8aaa', maxWidth: '500px', margin: '8px auto 0' }
                }, 'Nền tảng CORS thế hệ mới cho hạ tầng đo đạc Việt Nam')
            ),
            React.createElement('div', { className: 'row g-3' },
                features.map((f, idx) =>
                    React.createElement('div', { key: idx, className: 'col-lg-3 col-md-6' },
                        React.createElement('div', { className: 'landing-feature-card' },
                            React.createElement('div', { className: `landing-feature-icon ${f.iconClass}` },
                                React.createElement('i', { className: `bi ${f.icon}` })
                            ),
                            React.createElement('div', { className: 'landing-feature-title' }, f.title),
                            React.createElement('div', { className: 'landing-feature-desc' }, f.desc)
                        )
                    )
                )
            )
        ),

        // === STATION DIRECTORY (SEO) ===
        devices.length > 0 && React.createElement('section', {
            className: 'container',
            style: { position: 'relative', zIndex: 2, padding: '40px 0' }
        },
            React.createElement('div', { className: 'text-center mb-4' },
                React.createElement('h2', {
                    style: { fontSize: '1.6rem', fontWeight: 800, color: '#fff', letterSpacing: '-0.5px' }
                }, 'Mạng lưới trạm CORS'),
                React.createElement('p', {
                    style: { color: '#6a8aaa', fontSize: '0.9rem' }
                }, `${devices.length} trạm phủ sóng trên toàn lãnh thổ Việt Nam`)
            ),
            React.createElement('div', {
                className: 'row g-2',
                style: { maxHeight: '320px', overflowY: 'auto', padding: '4px' }
            },
                devices.map((d, idx) => {
                    const st = d.status || 'offline';
                    const isActive = ['online', 'sleep', 'streaming', 'starting'].includes(st);
                    const dotColor = isActive ? '#51cf66' : '#dc3545';
                    return React.createElement('div', { key: d.serial || idx, className: 'col-lg-3 col-md-4 col-6' },
                        React.createElement('a', {
                            href: `/station/${d.serial}`,
                            className: 'station-dir-item',
                            title: `Trạm ${d.name || d.serial} - ${st}`
                        },
                            React.createElement('span', {
                                className: 'station-dir-dot',
                                style: { background: dotColor, boxShadow: `0 0 6px ${dotColor}` }
                            }),
                            React.createElement('span', { className: 'station-dir-name' }, d.name || d.serial)
                        )
                    );
                })
            )
        ),

        // === FOOTER ===
        React.createElement('footer', { className: 'landing-footer container' },
            React.createElement('div', { className: 'row align-items-center' },
                React.createElement('div', { className: 'col-md-4 text-start mb-3 mb-md-0' },
                    React.createElement('div', { style: { color: '#b0c4de', fontWeight: 700, fontSize: '0.95rem' } },
                        'Aitogy ',
                        React.createElement('span', { style: { color: '#4a6a8a', fontWeight: 400 } }, 'CORS Network')
                    ),
                    React.createElement('div', { style: { color: '#4a6a8a', fontSize: '0.72rem', marginTop: '4px' } },
                        '© 2025 Aitogy Infrastructure Technology JSC'
                    )
                ),
                React.createElement('div', { className: 'col-md-4 text-center mb-3 mb-md-0' },
                    React.createElement('div', { className: 'd-flex justify-content-center gap-3', style: { fontSize: '0.8rem' } },
                        React.createElement('a', { href: '/network-status', className: 'landing-footer-link' }, 'Network Status'),
                        React.createElement('a', { href: '/blog', className: 'landing-footer-link' }, 'Knowledge Base'),
                        React.createElement('a', { href: '/api/docs', className: 'landing-footer-link' }, 'API Docs')
                    )
                ),
                React.createElement('div', { className: 'col-md-4 text-end' },
                    React.createElement('div', { className: 'd-flex justify-content-md-end justify-content-center gap-3', style: { fontSize: '0.8rem' } },
                        React.createElement('a', { href: 'https://aitogy.com', target: '_blank', className: 'landing-footer-link' }, 'Aitogy.com'),
                        React.createElement('a', { href: 'mailto:support@aitogy.com', className: 'landing-footer-link' }, 'Liên hệ')
                    )
                )
            )
        ),

        // === LOGIN PANEL ===
        React.createElement(LoginPanel, {
            isOpen: loginOpen,
            onClose: () => setLoginOpen(false)
        })
    );
};

// Navbar scroll effect
if (typeof window !== 'undefined') {
    window.addEventListener('scroll', () => {
        const nav = document.getElementById('landing-navbar');
        if (nav) {
            nav.classList.toggle('scrolled', window.scrollY > 50);
        }
    });
}

window.HomePageEnterprise = HomePageEnterprise;
