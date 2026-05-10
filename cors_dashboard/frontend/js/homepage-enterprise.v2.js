/**
 * HomePageEnterprise Component - Master Pro Landing
 * GNSS/Satellite themed with Vietnam S-shape, inline login
 */

/* Vietnam Map - Real Vector SVG (traced from high-detail source) */
const VietnamSVG = () => (
    React.createElement('div', { className: 'hero-map-wrapper' },
        React.createElement('img', {
            src: '/img/vietnam-map.svg',
            alt: '',
            className: 'vietnam-map-bg vietnam-map-hero',
            'aria-hidden': 'true'
        }),
        React.createElement('div', { className: 'vietnam-origin-badge' },
            React.createElement('img', {
                src: '/img/Flag_of_Vietnam.svg',
                alt: '',
                'aria-hidden': 'true'
            }),
            React.createElement('span', null, 'Made in Vietnam')
        ),
        React.createElement('div', { className: 'map-scan-ring map-scan-ring-1' }),
        React.createElement('div', { className: 'map-scan-ring map-scan-ring-2' }),
        React.createElement('div', { className: 'map-node map-node-hn' }),
        React.createElement('div', { className: 'map-node map-node-dn' }),
        React.createElement('div', { className: 'map-node map-node-hcm' })
    )
);

const SatelliteField = () => {
    const satellites = [
        { icon: 'bi-broadcast-pin', cls: 'sat-cyan', top: '14%', left: '9%', delay: '0s', speed: '15s' },
        { icon: 'bi-satellite', cls: 'sat-blue', top: '23%', left: '82%', delay: '-4s', speed: '18s' },
        { icon: 'bi-globe-asia-australia', cls: 'sat-purple', top: '42%', left: '71%', delay: '-7s', speed: '20s' },
        { icon: 'bi-radar', cls: 'sat-green', top: '60%', left: '16%', delay: '-2s', speed: '16s' },
        { icon: 'bi-broadcast', cls: 'sat-red', top: '73%', left: '88%', delay: '-9s', speed: '22s' },
        { icon: 'bi-crosshair', cls: 'sat-cyan', top: '32%', left: '43%', delay: '-11s', speed: '19s' },
        { icon: 'bi-satellite', cls: 'sat-blue', top: '83%', left: '38%', delay: '-6s', speed: '17s' },
        { icon: 'bi-broadcast-pin', cls: 'sat-green', top: '52%', left: '93%', delay: '-13s', speed: '21s' }
    ];

    return React.createElement(React.Fragment, null,
        satellites.map((sat, idx) =>
            React.createElement('div', {
                key: idx,
                className: `flying-satellite ${sat.cls}`,
                style: {
                    top: sat.top,
                    left: sat.left,
                    animationDelay: sat.delay,
                    animationDuration: sat.speed
                }
            },
                React.createElement('i', { className: `bi ${sat.icon}` })
            )
        ),
        React.createElement('div', { className: 'flying-flag' },
            React.createElement('img', {
                src: '/img/Flag_of_Vietnam.svg',
                alt: '',
                'aria-hidden': 'true'
            }),
            React.createElement('span', null, 'Vietnam GNSS')
        )
    );
};

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

/* ============================================================
   CINEMATIC SCROLL ENGINE (VIP PRO) - FULL EDITION v2
   ============================================================ */
const useCinematicScroll = () => {
    React.useEffect(() => {
        if (typeof gsap === 'undefined' || typeof ScrollTrigger === 'undefined') {
            console.warn('[Cinematic] GSAP not loaded');
            return;
        }
        gsap.registerPlugin(ScrollTrigger);
        let lenis = null;
        try {
            if (typeof Lenis !== 'undefined') {
                lenis = new Lenis({ duration: 1.2, easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)), smoothWheel: true });
                lenis.on('scroll', ScrollTrigger.update);
                gsap.ticker.add((time) => lenis.raf(time * 1000));
                gsap.ticker.lagSmoothing(0);
            }
        } catch(e) {}

        const initTimer = setTimeout(() => {
            const hero = document.querySelector('.hero-section');
            const mapImg = document.querySelector('.vietnam-map-bg');
            const mapWrapper = document.querySelector('.hero-map-wrapper');
            if (!hero) return;

            // ========== HERO ENTRANCE (one-time on load) ==========
            gsap.fromTo('.hero-badge', { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: 0.8, ease: 'power3.out' });
            gsap.fromTo('.hero-title', { y: 50, opacity: 0 }, { y: 0, opacity: 1, duration: 1, delay: 0.15, ease: 'power4.out' });
            gsap.fromTo('.hero-subtitle', { y: 40, opacity: 0 }, { y: 0, opacity: 1, duration: 0.8, delay: 0.3, ease: 'power3.out' });
            gsap.fromTo('.hero-actions', { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: 0.7, delay: 0.45, ease: 'power3.out' });
            if (mapImg) {
                mapImg.style.animation = 'none';
                gsap.fromTo(mapImg, 
                    { scale: 0.5, opacity: 0 }, 
                    { scale: 1, opacity: 0.88, duration: 1.4, delay: 0.3, ease: 'power2.out' }
                );
            }

            // ========== HERO SCROLL-OUT (no pin, just fade) ==========
            gsap.to('.hero-section .container', {
                scrollTrigger: { trigger: '.hero-section', start: 'top top', end: 'bottom top', scrub: true },
                y: -100, opacity: 0, ease: 'none'
            });
            if (mapWrapper) {
                const getMapTarget = (axis) => {
                    const slot = document.querySelector('.network-map-slot');
                    if (!slot || !mapWrapper) return 0;
                    const slotRect = slot.getBoundingClientRect();
                    const mapRect = mapWrapper.getBoundingClientRect();
                    const slotCenterX = slotRect.left + window.scrollX + slotRect.width / 2;
                    const slotCenterY = slotRect.top + window.scrollY + slotRect.height / 2 + 62;
                    const mapCenterX = mapRect.left + window.scrollX + mapRect.width / 2;
                    const mapCenterY = mapRect.top + window.scrollY + mapRect.height / 2;
                    return axis === 'x' ? slotCenterX - mapCenterX : slotCenterY - mapCenterY;
                };

                gsap.fromTo(mapWrapper,
                    { x: 0, y: 0, scale: 1, opacity: 1 },
                    {
                        scrollTrigger: { 
                            trigger: '.hero-section', 
                            start: 'top top', 
                            endTrigger: '.network-map-section',
                            end: 'center center',
                            scrub: 1,
                            invalidateOnRefresh: true
                        },
                        x: () => getMapTarget('x'),
                        y: () => getMapTarget('y'),
                        scale: 0.82,
                        opacity: 1,
                        ease: 'power1.inOut',
                        immediateRender: false
                    }
                );
            }

            // ========== FLYING SATELLITES (orbit around network-map section) ==========
            const sats = gsap.utils.toArray('.flying-satellite');
            sats.forEach((sat, i) => {
                gsap.fromTo(sat,
                    { x: i % 2 === 0 ? '-12vw' : '12vw', y: i % 3 === 0 ? '-8vh' : '8vh', rotation: -12, opacity: 0.45 },
                    {
                        x: i % 2 === 0 ? '18vw' : '-18vw',
                        y: i % 3 === 0 ? '12vh' : '-12vh',
                        rotation: i % 2 === 0 ? 24 : -24,
                        opacity: 0.95,
                        ease: 'none',
                        scrollTrigger: { trigger: '.network-map-section', start: 'top bottom', end: 'bottom top', scrub: 1 }
                    }
                );
            });

            // ========== VIETNAM FLAG PARALLAX ==========
            const flagEl = document.querySelector('.flying-flag');
            if (flagEl) {
                gsap.fromTo(flagEl,
                    { y: '30px', opacity: 0, scale: 0.6 },
                    {
                        y: '-10px', opacity: 1, scale: 1,
                        ease: 'none',
                        scrollTrigger: { trigger: '.network-map-section', start: 'top bottom', end: 'center center', scrub: 1 }
                    }
                );
            }

            // ========== NETWORK MAP SECTION ==========
            gsap.from('.network-map-section .section-title', {
                scrollTrigger: { trigger: '.network-map-section', start: 'top 80%', toggleActions: 'play none none reverse' },
                y: 50, opacity: 0, duration: 0.8
            });
            gsap.utils.toArray('.network-map-section .station-info-card').forEach((card, i) => {
                gsap.from(card, {
                    scrollTrigger: { trigger: card, start: 'top 90%', toggleActions: 'play none none reverse' },
                    x: -60, opacity: 0, duration: 0.7, delay: i * 0.1, ease: 'power3.out'
                });
            });

            // ========== STATS ==========
            const statCards = gsap.utils.toArray('.landing-stat-card');
            statCards.forEach((card, i) => {
                const fromX = i % 2 === 0 ? -80 : 80;
                gsap.from(card, {
                    scrollTrigger: { trigger: card, start: 'top 88%', toggleActions: 'play none none reverse' },
                    x: fromX, y: 40, opacity: 0, scale: 0.85, rotateY: i % 2 === 0 ? 10 : -10,
                    duration: 0.9, delay: i * 0.12, ease: 'power3.out'
                });
            });

            // ========== FEATURES ==========
            const featureCards = gsap.utils.toArray('.landing-feature-card-wrapper');
            if (featureCards.length) {
                gsap.from('.landing-features .text-center', {
                    scrollTrigger: { trigger: '.landing-features', start: 'top 80%', toggleActions: 'play none none reverse' },
                    y: 60, opacity: 0, duration: 0.9
                });
                featureCards.forEach((card, i) => {
                    gsap.from(card, {
                        scrollTrigger: { trigger: card, start: 'top 90%', toggleActions: 'play none none reverse' },
                        y: 100, opacity: 0, rotateX: 20, scale: 0.88,
                        duration: 0.8, delay: i * 0.15, ease: 'back.out(1.4)',
                        transformOrigin: 'center bottom'
                    });
                });
            }

            // ========== HOW IT WORKS ==========
            const hiwSteps = gsap.utils.toArray('.hiw-step');
            if (hiwSteps.length) {
                gsap.from('.hiw-section .section-title', {
                    scrollTrigger: { trigger: '.hiw-section', start: 'top 80%', toggleActions: 'play none none reverse' },
                    y: 50, opacity: 0, duration: 0.8
                });
                gsap.from('.hiw-section .section-sub', {
                    scrollTrigger: { trigger: '.hiw-section', start: 'top 78%', toggleActions: 'play none none reverse' },
                    y: 30, opacity: 0, duration: 0.7, delay: 0.15
                });
                hiwSteps.forEach((step, i) => {
                    const content = step.querySelector('.hiw-step-content');
                    const num = step.querySelector('.hiw-step-number');
                    const fromX = i % 2 === 0 ? -120 : 120;
                    if (content) gsap.from(content, {
                        scrollTrigger: { trigger: step, start: 'top 82%', toggleActions: 'play none none reverse' },
                        x: fromX, opacity: 0, duration: 1, ease: 'power3.out'
                    });
                    if (num) gsap.from(num, {
                        scrollTrigger: { trigger: step, start: 'top 85%', toggleActions: 'play none none reverse' },
                        scale: 0, rotation: 180, opacity: 0, duration: 0.7, ease: 'back.out(2)'
                    });
                });
            }

            // ========== LIVE DATA ==========
            const liveBars = gsap.utils.toArray('.livedata-bar');
            if (liveBars.length) {
                gsap.from('.livedata-section .section-title', {
                    scrollTrigger: { trigger: '.livedata-section', start: 'top 80%', toggleActions: 'play none none reverse' },
                    y: 50, opacity: 0, duration: 0.8
                });
                gsap.from(liveBars, {
                    scrollTrigger: { trigger: '.livedata-bars', start: 'top 90%', toggleActions: 'play none none reverse' },
                    scaleY: 0, opacity: 0, stagger: 0.03, duration: 0.5, ease: 'power2.out'
                });
                gsap.utils.toArray('.livedata-card').forEach((card, i) => {
                    gsap.from(card, {
                        scrollTrigger: { trigger: card, start: 'top 88%', toggleActions: 'play none none reverse' },
                        y: 60, opacity: 0, scale: 0.9, duration: 0.8, delay: i * 0.15, ease: 'power3.out'
                    });
                });
            }

            // ========== TECH MARQUEE ==========
            gsap.from('.tech-marquee-section', {
                scrollTrigger: { trigger: '.tech-marquee-section', start: 'top 90%', toggleActions: 'play none none reverse' },
                opacity: 0, y: 30, duration: 0.8
            });

            // ========== CTA ==========
            const ctaBox = document.querySelector('.cta-box');
            if (ctaBox) {
                gsap.from(ctaBox, {
                    scrollTrigger: { trigger: ctaBox, start: 'top 85%', toggleActions: 'play none none reverse' },
                    y: 80, opacity: 0, scale: 0.9, duration: 1, ease: 'power3.out'
                });
            }

            // ========== STATION DIRECTORY ==========
            const stationItems = gsap.utils.toArray('.station-dir-item');
            if (stationItems.length) {
                gsap.from(stationItems, {
                    scrollTrigger: { trigger: '.station-dir-item', start: 'top 90%', toggleActions: 'play none none reverse' },
                    x: -30, opacity: 0, stagger: 0.03, duration: 0.4, ease: 'power2.out'
                });
            }

            // ========== FOOTER ==========
            gsap.from('.landing-footer', {
                scrollTrigger: { trigger: '.landing-footer', start: 'top 95%', toggleActions: 'play none none reverse' },
                y: 40, opacity: 0, duration: 0.8
            });

            // ========== SECTION DIVIDERS ==========
            gsap.utils.toArray('.section-divider').forEach(div => {
                gsap.from(div, {
                    scrollTrigger: { trigger: div, start: 'top 95%', toggleActions: 'play none none reverse' },
                    scaleX: 0, duration: 1.2, ease: 'power2.inOut'
                });
            });

            // ========== PARALLAX BACKGROUND ==========
            gsap.to('.landing-orb-1', { scrollTrigger: { scrub: true }, y: -200, ease: 'none' });
            gsap.to('.landing-orb-2', { scrollTrigger: { scrub: true }, y: -120, ease: 'none' });
            gsap.to('.landing-orb-3', { scrollTrigger: { scrub: true }, y: -280, ease: 'none' });
            gsap.to('.landing-grid', { scrollTrigger: { scrub: true }, y: -80, ease: 'none' });

        }, 500);

        const hideIndicator = () => {
            const el = document.querySelector('.scroll-indicator');
            if (el) el.classList.toggle('hidden', window.scrollY > 200);
        };
        window.addEventListener('scroll', hideIndicator);

        return () => {
            clearTimeout(initTimer);
            window.removeEventListener('scroll', hideIndicator);
            ScrollTrigger.getAll().forEach(t => t.kill());
            if (lenis) lenis.destroy();
        };
    }, []);
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
                    React.createElement('strong', null, 'Aitogy Technology JSC'),
                    React.createElement('br'),
                    'Hệ thống trạm tham chiếu hoạt động liên tục toàn quốc'
                )
            )
        )
    );
};

/* ============================================================
   CINEMATIC FEATURE CARDS v5 — GSAP "FLY-TO-CENTER"
   Smooth like a video. No delay. Reliable collapse.
   ============================================================ */
const CinematicFeatures = ({ features }) => {
    const cardRefs = React.useRef([]);
    const overlayRef = React.useRef(null);
    const [activeIdx, setActiveIdx] = React.useState(null);
    const timelineRef = React.useRef(null);
    const lockRef = React.useRef(false);

    const handleMouseEnter = (idx) => {
        if (lockRef.current || activeIdx === idx) return;
        
        // If another card is active, reset it instantly
        if (activeIdx !== null) handleMouseLeave(activeIdx, true);

        setActiveIdx(idx);
        const wrapper = cardRefs.current[idx];
        const inner = wrapper.querySelector('.landing-feature-card-inner');
        
        const rect = wrapper.getBoundingClientRect();
        const centerX = window.innerWidth / 2;
        const centerY = window.innerHeight / 2;
        
        const targetX = centerX - (rect.left + rect.width / 2);
        const targetY = centerY - (rect.top + rect.height / 2);
        
        // Lift wrapper
        wrapper.style.zIndex = '9500';
        if (overlayRef.current) overlayRef.current.classList.add('active');

        if (timelineRef.current) timelineRef.current.kill();
        
        timelineRef.current = gsap.timeline({
            onStart: () => { lockRef.current = true; },
            onComplete: () => { lockRef.current = false; }
        });

        // ONE SMOOTH MOTION: fly, scale, and rotate
        timelineRef.current.to(inner, {
            x: targetX,
            y: targetY,
            scale: 1.6,
            rotateY: 180,
            duration: 0.8,
            ease: 'expo.out',
            overwrite: 'auto'
        });
    };

    const handleMouseLeave = (idx, instant = false) => {
        if (activeIdx !== idx) return;

        const wrapper = cardRefs.current[idx];
        const inner = wrapper.querySelector('.landing-feature-card-inner');

        if (overlayRef.current) overlayRef.current.classList.remove('active');
        
        if (timelineRef.current) timelineRef.current.kill();

        if (instant) {
            gsap.set(inner, { x: 0, y: 0, scale: 1, rotateY: 0 });
            wrapper.style.zIndex = '';
            setActiveIdx(null);
            lockRef.current = false;
        } else {
            timelineRef.current = gsap.to(inner, {
                x: 0, y: 0, scale: 1, rotateY: 0,
                duration: 0.6,
                ease: 'power2.inOut',
                onComplete: () => {
                    wrapper.style.zIndex = '';
                    setActiveIdx(null);
                }
            });
        }
    };

    return React.createElement('section', { className: 'landing-features container', style: { position: 'relative' } },
        // Cinematic Overlay
        React.createElement('div', { 
            ref: overlayRef, 
            className: 'feature-cinematic-overlay',
            onMouseEnter: () => activeIdx !== null && handleMouseLeave(activeIdx)
        }),

        // Header
        React.createElement('div', { className: 'text-center mb-5' },
            React.createElement('h2', { style: { fontSize: '2.2rem', fontWeight: 800, color: '#fff' } }, 'Tính năng nổi bật'),
            React.createElement('p', { style: { color: '#6a8aaa' } }, 'Nền tảng CORS thế hệ mới cho hạ tầng đo đạc Việt Nam')
        ),

        // Grid
        React.createElement('div', { className: 'row g-4' },
            features.map((f, idx) =>
                React.createElement('div', { key: idx, className: 'col-lg-3 col-md-6' },
                    React.createElement('div', {
                        ref: el => cardRefs.current[idx] = el,
                        className: 'landing-feature-card-wrapper',
                        onMouseEnter: () => handleMouseEnter(idx)
                    },
                        React.createElement('div', { className: 'landing-feature-card-inner' },
                            // FRONT
                            React.createElement('div', { className: 'landing-feature-card-front' },
                                React.createElement('div', { className: `landing-feature-icon ${f.iconClass}` },
                                    React.createElement('i', { className: `bi ${f.icon}` })
                                ),
                                React.createElement('div', { className: 'landing-feature-title' }, f.title),
                                React.createElement('div', { className: 'landing-feature-desc' }, f.desc)
                            ),
                            // BACK
                            React.createElement('div', { className: 'landing-feature-card-back' },
                                React.createElement('div', { className: 'back-badge' }, 'Chi tiết'),
                                React.createElement('div', { className: `landing-feature-icon ${f.iconClass}` },
                                    React.createElement('i', { className: `bi ${f.icon}` })
                                ),
                                React.createElement('div', { className: 'landing-feature-title' }, f.title),
                                React.createElement('div', { className: 'landing-feature-long-desc' }, f.longDesc)
                            )
                        )
                    )
                )
            )
        )
    );
};


/* ============================================================
   MAIN HOMEPAGE COMPONENT
   ============================================================ */
const HomePageEnterprise = ({ devices = [] }) => {

    const [loginOpen, setLoginOpen] = React.useState(false);
    useCinematicScroll();

    // Stats Animation Logic
    const animStations = useCounter(devices.length || 0);
    const activeStatuses = ['online', 'sleep', 'streaming', 'starting'];
    const onlineCount = devices.filter(d => activeStatuses.includes(d.status)).length;
    const animOnline = useCounter(onlineCount);
    
    // Unique provinces/cities
    const provinces = new Set(devices.filter(d => d.location).map(d => d.location.split(',').pop().trim()));
    const animProvinces = useCounter(provinces.size || 24);
    
    // Uptime calculation (simulated or real)
    const uptimePct = 99.98;

    const stats = [
        { label: 'Tổng số trạm', value: animStations, sub: 'Mạng lưới CORS Aitogy', icon: 'bi-broadcast-pin', color: '#4facfe' },
        { label: 'Đang hoạt động', value: animOnline, sub: 'Online · Sleep · Streaming', icon: 'bi-check-circle-fill', color: '#51cf66' },
        { label: 'Khu vực phủ sóng', value: animProvinces, sub: 'Tỉnh/TP trên toàn quốc', icon: 'bi-geo-alt-fill', color: '#cc5de8' },
        { label: 'Uptime', value: `${uptimePct}%`, sub: 'Tỷ lệ hoạt động', icon: 'bi-lightning-charge-fill', color: '#00f2ff' }
    ];

    const features = [
        { 
            icon: 'bi-cpu', 
            iconClass: 'feature-icon-blue', 
            title: 'Đa chòm sao vệ tinh',
            desc: 'Theo dõi GPS, GLONASS, Galileo, BeiDou — tối đa hóa độ chính xác định vị.',
            longDesc: 'Hệ thống tích hợp toàn diện 4 hệ thống vệ tinh GPS, GLONASS, Galileo và BeiDou. Khả năng thu nhận tín hiệu đa tần số giúp loại bỏ sai số tầng điện ly, đảm bảo độ chính xác mức centimet ngay cả trong môi trường đô thị dày đặc hoặc tán cây.'
        },
        { 
            icon: 'bi-wifi', 
            iconClass: 'feature-icon-green', 
            title: 'NTRIP Caster tốc độ cao',
            desc: 'Truyền dữ liệu RTK/RTCM tối ưu với giao thức độ trễ cực thấp.',
            longDesc: 'Hạ tầng truyền dẫn RTCM 3.x thời gian thực với băng thông cực lớn. Hệ thống cân bằng tải thông minh đảm bảo độ trễ luôn dưới 200ms trên toàn quốc, hỗ trợ kết nối đồng thời hàng ngàn thiết bị Rover mà không gây tắc nghẽn.'
        },
        { 
            icon: 'bi-graph-up-arrow', 
            iconClass: 'feature-icon-purple', 
            title: 'Giám sát 24/7',
            desc: 'Tự động cảnh báo và phục hồi khi phát hiện sự cố, đảm bảo uptime tối đa.',
            longDesc: 'Hệ thống AI tự động phân tích sức khỏe trạm Base theo giây. Tự động chuyển đổi sang trạm dự phòng (Failover) khi phát hiện sự cố. Hệ thống cảnh báo qua Telegram giúp kỹ thuật viên xử lý kịp thời mọi tình huống.'
        },
        { 
            icon: 'bi-shield-check', 
            iconClass: 'feature-icon-cyan', 
            title: 'Bảo mật cấp doanh nghiệp',
            desc: 'Quản lý phân quyền đa cấp, mã hóa dữ liệu end-to-end.',
            longDesc: 'Toàn bộ luồng dữ liệu được mã hóa TLS/SSL. Cơ chế phân quyền chi tiết cho phép quản lý từng tài khoản Rover, hạn chế khu vực hoạt động (Geofencing) và thời gian truy cập, bảo vệ tuyệt đối dữ liệu hạ tầng.'
        }
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
            ),
            React.createElement(SatelliteField)
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
                React.createElement('a', { href: '/network', className: 'landing-nav-link' }, 'Network'),
                React.createElement('a', { href: '/blog', className: 'landing-nav-link' }, 'Blog'),
                React.createElement('a', { href: '/api/docs', className: 'landing-nav-link' }, 'API Docs'),
                React.createElement('button', {
                    className: 'btn-login-nav',
                    onClick: () => setLoginOpen(true)
                },
                    React.createElement('i', { className: 'bi bi-person-circle me-2' }),
                    'Đăng nhập'
                )
            )
        ),

        // === VIETNAM MAP (one floating map: hero -> network slot) ===
        React.createElement(VietnamSVG),

        // === HERO SECTION ===
        React.createElement('section', { className: 'hero-section' },
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

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),

        // === NETWORK MAP SHOWCASE ===
        React.createElement('section', { className: 'network-map-section container' },
            React.createElement('h2', { className: 'section-title', style: { fontSize: '2.2rem', fontWeight: 900, color: '#fff', letterSpacing: '-1px', textAlign: 'center', marginBottom: '8px' } },
                'Mạng lưới CORS Việt Nam'
            ),
            React.createElement('p', { style: { color: '#6a8aaa', textAlign: 'center', marginBottom: '48px' } },
                `${devices.length} trạm bao phủ — Băng thông lớn, kết nối ổn định toàn quốc`
            ),
            React.createElement('div', { className: 'row g-4 align-items-center' },
                React.createElement('div', { className: 'col-lg-5' },
                    React.createElement('div', { 
                        'data-lenis-prevent': 'true',
                        style: { display: 'flex', flexDirection: 'column', gap: '12px', maxHeight: '500px', overflowY: 'auto', paddingRight: '8px', scrollbarWidth: 'thin', scrollbarColor: '#4facfe transparent' } 
                    },
                        devices.slice(0, 15).map((d, idx) => {
                            const st = d.status || 'offline';
                            const isActive = activeStatuses.includes(st);
                            const sats = d.system_info?.satellites || 0;
                            const lat = d.system_info?.latency || 0;
                            const dotColor = isActive ? '#51cf66' : '#dc3545';
                            return React.createElement('div', {
                                key: d.serial || idx,
                                className: 'station-info-card',
                                style: {
                                    background: 'rgba(10,25,47,0.6)', backdropFilter: 'blur(8px)',
                                    border: `1px solid ${isActive ? 'rgba(0,242,255,0.15)' : 'rgba(220,53,69,0.15)'}`,
                                    borderRadius: '14px', padding: '16px 20px',
                                    display: 'flex', alignItems: 'center', gap: '16px', transition: 'all 0.3s'
                                }
                            },
                                React.createElement('span', {
                                    style: { width: '10px', height: '10px', borderRadius: '50%', background: dotColor, boxShadow: `0 0 8px ${dotColor}`, flexShrink: 0 }
                                }),
                                React.createElement('div', { style: { flex: 1, minWidth: 0 } },
                                    React.createElement('div', { style: { fontWeight: 700, fontSize: '0.9rem', color: '#fff' } }, d.name || d.serial),
                                    React.createElement('div', { style: { fontSize: '0.75rem', color: '#6a8aaa', marginTop: '2px' } },
                                        st.charAt(0).toUpperCase() + st.slice(1),
                                        d.location ? ` · ${d.location}` : ''
                                    )
                                ),
                                React.createElement('div', { style: { display: 'flex', gap: '16px', fontSize: '0.75rem', flexShrink: 0 } },
                                    React.createElement('div', { style: { textAlign: 'center' } },
                                        React.createElement('div', { style: { color: '#cc5de8', fontWeight: 700 } }, sats > 0 ? sats : '—'),
                                        React.createElement('div', { style: { color: '#4a6a8a', fontSize: '0.65rem' } }, 'SATs')
                                    ),
                                    React.createElement('div', { style: { textAlign: 'center' } },
                                        React.createElement('div', { style: { color: '#00f2ff', fontWeight: 700 } }, lat > 0 ? `${lat}ms` : '—'),
                                        React.createElement('div', { style: { color: '#4a6a8a', fontSize: '0.65rem' } }, 'Latency')
                                    )
                                )
                            );
                        }),
                        devices.length > 15 && React.createElement('div', {
                            style: { textAlign: 'center', padding: '12px', color: '#4a6a8a', fontSize: '0.8rem' }
                        }, `... và ${devices.length - 15} trạm khác`)
                    )
                ),
                React.createElement('div', { className: 'col-lg-7', style: { position: 'relative', height: '450px' } },
                    React.createElement('div', { className: 'network-map-slot' }),
                    React.createElement('div', {
                        style: { position: 'absolute', top: '10%', right: '5%', display: 'flex', flexDirection: 'column', gap: '8px', zIndex: 2 }
                    },
                        ['GPS', 'GLONASS', 'Galileo', 'BeiDou'].map((sys, i) =>
                            React.createElement('div', {
                                key: sys,
                                style: {
                                    fontSize: '0.7rem', fontWeight: 600, padding: '4px 12px',
                                    borderRadius: '20px', background: 'rgba(0,242,255,0.08)',
                                    border: '1px solid rgba(0,242,255,0.15)', color: ['#4facfe', '#51cf66', '#cc5de8', '#ff6b6b'][i]
                                }
                            }, React.createElement('i', { className: 'bi bi-broadcast-pin me-1' }), sys)
                        )
                    )
                )
            )
        ),

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),

        // === FEATURES (Cinematic Fly-to-Center + 3D Flip) ===
        React.createElement(CinematicFeatures, { features: features }),

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),


        // === HOW IT WORKS ===
        React.createElement('section', { className: 'hiw-section container' },
            React.createElement('h2', { className: 'section-title' }, 'Quy trình vận hành'),
            React.createElement('p', { className: 'section-sub' }, 'Từ vệ tinh đến rover — chỉ trong vài mili giây'),
            React.createElement('div', { className: 'hiw-timeline' },
                React.createElement('div', { className: 'hiw-step' },
                    React.createElement('div', { className: 'hiw-step-content' },
                        React.createElement('div', { className: 'hiw-step-icon' }, React.createElement('i', { className: 'bi bi-satellite' })),
                        React.createElement('div', { className: 'hiw-step-title' }, 'Thu tín hiệu vệ tinh'),
                        React.createElement('div', { className: 'hiw-step-desc' }, 'Trạm CORS thu tín hiệu từ 4 hệ thống vệ tinh: GPS, GLONASS, Galileo, BeiDou với anten đa tần số chuyên dụng.')
                    ),
                    React.createElement('div', { className: 'hiw-step-number' }, '1')
                ),
                React.createElement('div', { className: 'hiw-step' },
                    React.createElement('div', { className: 'hiw-step-content' },
                        React.createElement('div', { className: 'hiw-step-icon' }, React.createElement('i', { className: 'bi bi-cpu' })),
                        React.createElement('div', { className: 'hiw-step-title' }, 'Xử lý & hiệu chỉnh'),
                        React.createElement('div', { className: 'hiw-step-desc' }, 'Dữ liệu được xử lý real-time, tạo ra bản tin RTCM chứa thông số hiệu chỉnh sai số khí quyển và quỹ đạo.')
                    ),
                    React.createElement('div', { className: 'hiw-step-number' }, '2')
                ),
                React.createElement('div', { className: 'hiw-step' },
                    React.createElement('div', { className: 'hiw-step-content' },
                        React.createElement('div', { className: 'hiw-step-icon' }, React.createElement('i', { className: 'bi bi-broadcast' })),
                        React.createElement('div', { className: 'hiw-step-title' }, 'Truyền dẫn NTRIP'),
                        React.createElement('div', { className: 'hiw-step-desc' }, 'Bản tin hiệu chỉnh được gửi tới Caster và phân phối tới Rover qua mạng di động với độ trễ cực thấp.')
                    ),
                    React.createElement('div', { className: 'hiw-step-number' }, '3')
                ),
                React.createElement('div', { className: 'hiw-step' },
                    React.createElement('div', { className: 'hiw-step-content' },
                        React.createElement('div', { className: 'hiw-step-icon' }, React.createElement('i', { className: 'bi bi-crosshair' })),
                        React.createElement('div', { className: 'hiw-step-title' }, 'Định vị Centimet'),
                        React.createElement('div', { className: 'hiw-step-desc' }, 'Rover kết hợp dữ liệu vệ tinh và hiệu chỉnh để giải bài toán RTK, đạt độ chính xác mức mm-cm.')
                    ),
                    React.createElement('div', { className: 'hiw-step-number' }, '4')
                )
            )
        ),

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),

        // === LIVE DATA PREVIEW ===
        React.createElement('section', { className: 'livedata-section container' },
            React.createElement('h2', { className: 'section-title' }, 'Dữ liệu thời gian thực'),
            React.createElement('div', { className: 'row g-4' },
                React.createElement('div', { className: 'col-lg-8' },
                    React.createElement('div', { className: 'livedata-card' },
                        React.createElement('div', { className: 'livedata-header' },
                            React.createElement('div', null, React.createElement('span', { className: 'pulse-online' }), 'Global Network Traffic'),
                            React.createElement('div', { className: 'text-muted' }, 'Live: RTCM 3.2 MSM7')
                        ),
                        React.createElement('div', { className: 'livedata-bars' },
                            Array.from({ length: 40 }).map((_, i) =>
                                React.createElement('div', {
                                    key: i, className: 'livedata-bar',
                                    style: { height: `${20 + Math.random() * 60}%`, animationDelay: `${i * 0.05}s` }
                                })
                            )
                        )
                    )
                ),
                React.createElement('div', { className: 'col-lg-4' },
                    React.createElement('div', { className: 'livedata-card' },
                        React.createElement('div', { className: 'livedata-header' }, 'Caster Stability'),
                        React.createElement('div', { className: 'p-4 text-center' },
                            React.createElement('div', { style: { fontSize: '3rem', fontWeight: 900, color: '#00f2ff' } }, '99.98%'),
                            React.createElement('div', { className: 'text-muted' }, 'Độ ổn định hệ thống')
                        )
                    )
                )
            )
        ),

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),

        // === TECHNOLOGY MARQUEE ===
        React.createElement('section', { className: 'tech-marquee-section' },
            React.createElement('div', { style: { textAlign: 'center', marginBottom: '32px' } },
                React.createElement('div', { style: { color: '#4a6a8a', fontSize: '0.75rem', letterSpacing: '3px', textTransform: 'uppercase', fontWeight: 600 } }, 'Công nghệ & Giao thức hỗ trợ')
            ),
            React.createElement('div', { style: { overflow: 'hidden' } },
                React.createElement('div', { className: 'tech-marquee-track' },
                    ...[
                        { icon: 'bi-broadcast-pin', text: 'NTRIP v2.0' },
                        { icon: 'bi-hdd-network', text: 'RTCM 3.3' },
                        { icon: 'bi-globe', text: 'GPS L1/L2/L5' },
                        { icon: 'bi-radar', text: 'GLONASS' },
                        { icon: 'bi-stars', text: 'Galileo' },
                        { icon: 'bi-compass', text: 'BeiDou B1/B2/B3' },
                        { icon: 'bi-shield-lock', text: 'TLS/SSL' },
                        { icon: 'bi-lightning', text: 'WebSocket' },
                        { icon: 'bi-database', text: 'MQTT' },
                        { icon: 'bi-cpu', text: 'RINEX 3.x' },
                        // Duplicate for seamless loop
                        { icon: 'bi-broadcast-pin', text: 'NTRIP v2.0' },
                        { icon: 'bi-hdd-network', text: 'RTCM 3.3' },
                        { icon: 'bi-globe', text: 'GPS L1/L2/L5' },
                        { icon: 'bi-radar', text: 'GLONASS' },
                        { icon: 'bi-stars', text: 'Galileo' },
                        { icon: 'bi-compass', text: 'BeiDou B1/B2/B3' },
                        { icon: 'bi-shield-lock', text: 'TLS/SSL' },
                        { icon: 'bi-lightning', text: 'WebSocket' },
                        { icon: 'bi-database', text: 'MQTT' },
                        { icon: 'bi-cpu', text: 'RINEX 3.x' }
                    ].map((t, i) =>
                        React.createElement('div', { key: i, className: 'tech-marquee-item' },
                            React.createElement('i', { className: `bi ${t.icon}` }),
                            t.text
                        )
                    )
                )
            )
        ),

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),

        // === CTA SECTION ===
        React.createElement('section', { className: 'cta-section container' },
            React.createElement('div', { className: 'cta-box' },
                React.createElement('h2', null, 'Sẵn sàng nâng cấp hạ tầng đo đạc?'),
                React.createElement('p', null, 'Liên hệ đội ngũ Aitogy để được tư vấn giải pháp hạ tầng CORS phù hợp.'),
                React.createElement('div', { className: 'd-flex gap-3 justify-content-center flex-wrap' },
                    React.createElement('button', { className: 'btn-enterprise', onClick: () => setLoginOpen(true) }, 'Bắt đầu ngay'),
                    React.createElement('button', { className: 'btn-outline-cyan' }, 'Tư vấn kỹ thuật')
                )
            )
        ),

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),

        // === STATION DIRECTORY (SEO) ===
        devices.length > 0 && React.createElement('section', { className: 'container mb-5' },
            React.createElement('div', { className: 'text-center mb-4' },
                React.createElement('h2', { style: { fontSize: '1.5rem', fontWeight: 800, color: '#fff' } }, 'Mạng lưới trạm CORS'),
                React.createElement('p', { style: { color: '#6a8aaa' } }, `${devices.length} trạm phủ sóng toàn quốc`)
            ),
            React.createElement('div', { className: 'row g-2', style: { maxHeight: '300px', overflowY: 'auto' } },
                devices.map((d, idx) => {
                    const st = d.status || 'offline';
                    const isActive = activeStatuses.includes(st);
                    const dotColor = isActive ? '#51cf66' : '#dc3545';
                    return React.createElement('div', { key: d.serial || idx, className: 'col-lg-3 col-md-4 col-6' },
                        React.createElement('a', { href: `/station/${d.serial}`, className: 'station-dir-item' },
                            React.createElement('span', { className: 'station-dir-dot', style: { background: dotColor, boxShadow: `0 0 6px ${dotColor}` } }),
                            React.createElement('span', { className: 'station-dir-name' }, d.name || d.serial)
                        )
                    );
                })
            )
        ),

        // === FOOTER ===
        React.createElement('footer', { className: 'landing-footer container' },
            React.createElement('div', { className: 'row align-items-center' },
                React.createElement('div', { className: 'col-md-6 text-start mb-3 mb-md-0' },
                    React.createElement('div', { className: 'fw-bold mb-1', style: { color: '#fff' } }, 'AITOGY TECHNOLOGY JSC'),
                    React.createElement('div', { className: 'text-muted small' }, '© 2025 Aitogy Network. All rights reserved.')
                ),
                React.createElement('div', { className: 'col-md-6 text-end' },
                    React.createElement('div', { className: 'd-flex gap-4 justify-content-md-end justify-content-center small' },
                        React.createElement('a', { href: 'https://aitogy.com', className: 'landing-footer-link' }, 'Aitogy.com'),
                        React.createElement('a', { href: 'mailto:support@aitogy.com', className: 'landing-footer-link' }, 'Support')
                    )
                )
            )
        ),

        // === LOGIN PANEL ===
        React.createElement(LoginPanel, {
            isOpen: loginOpen,
            onClose: () => setLoginOpen(false)
        }),

        // === SCROLL INDICATOR ===
        React.createElement('div', { className: 'scroll-indicator' },
            'Scroll',
            React.createElement('div', { className: 'scroll-indicator-line' })
        )
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
