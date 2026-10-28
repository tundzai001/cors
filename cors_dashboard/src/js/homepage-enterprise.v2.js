/**
 * HomePageEnterprise Component - Master Pro Landing
 * GNSS/Satellite themed with Vietnam S-shape, inline login
 */

/* Vietnam Map - Real Vector SVG (traced from high-detail source) */
const InlineFlag = (props) => React.createElement('svg', {
    width: props.w || '24', height: props.h || '16', viewBox: '0 0 30 20',
    className: props.className || '', style: props.style || {},
    'aria-hidden': 'true'
},
    React.createElement('rect', { width: '30', height: '20', fill: '#da251d' }),
    React.createElement('polygon', {
        points: '15,4 11.47,14.85 20.71,8.15 9.29,8.15 18.53,14.85',
        fill: '#ff0', className: props.starClass || 'flag-star'
    })
);

/* Vietnam Map - Inline SVG with periodic redraw loop */
const VietnamSVG = () => {
    const containerRef = React.useRef(null);

    React.useEffect(() => {
        const container = containerRef.current;
        if (!container) return;
        const abortController = new AbortController();
        let mapTimeline = null;
        let mapNearViewport = true;
        let pageVisible = document.visibilityState === 'visible';
        const syncMapAnimation = () => {
            if (mapTimeline) mapTimeline.paused(!(mapNearViewport && pageVisible));
        };
        const onVisibilityChange = () => {
            pageVisible = document.visibilityState === 'visible';
            syncMapAnimation();
        };
        const observer = new IntersectionObserver(([entry]) => {
            mapNearViewport = entry.isIntersecting;
            syncMapAnimation();
        }, { rootMargin: '240px' });
        observer.observe(container);
        document.addEventListener('visibilitychange', onVisibilityChange);
        // Fetch the SVG and inject inline for GSAP draw-on
        fetch('/assets/img/vietnam-map.svg', { signal: abortController.signal })
            .then(r => r.text())
            .then(svgText => {
                const svgHolder = container.querySelector('.vietnam-map-inline');
                if (!svgHolder) return;
                svgHolder.innerHTML = svgText;
                const svgEl = svgHolder.querySelector('svg');
                if (!svgEl) return;
                svgEl.classList.add('vietnam-map-bg', 'vietnam-map-hero');
                svgEl.setAttribute('aria-hidden', 'true');
                svgEl.style.width = '100%';
                svgEl.style.height = 'auto';
                // The SVG already has a static glow. Prevent the legacy CSS
                // filter animation from repainting the complete map layer.
                svgEl.style.animation = 'none';
                requestAnimationFrame(() => {
                    if (typeof ScrollTrigger !== 'undefined') ScrollTrigger.refresh();
                });
                // GSAP Draw-on with PERIODIC REDRAW LOOP
                const paths = svgEl.querySelectorAll('path');
if (typeof gsap !== 'undefined' && paths.length && window.CorsPerformance?.decorativeMotion && !window.CorsPerformance?.reducedMotion) {
                    // Prepare all paths with dasharray
                    const pathLengths = [];
                    paths.forEach(p => {
                        const len = p.getTotalLength();
                        pathLengths.push(len);
                        p.style.strokeDasharray = len;
                        p.style.strokeDashoffset = len;
                    });

                    // Build a repeating timeline: draw → glow → hold → erase → pause → repeat
                    mapTimeline = gsap.timeline({ repeat: -1, repeatDelay: 0.1 });

                    // Phase 1: Draw on (2.2s)
                    mapTimeline.to(paths, {
                        strokeDashoffset: 0,
                        duration: 2.2,
                        stagger: { each: 0.0035, from: 'start' },
                        ease: 'power2.inOut'
                    }, 0);

                    // Phase 2: Glow flash after draw completes (0.6s). Opacity
                    // stays compositor-only; animating SVG filters forces a
                    // large repaint on mobile Safari.
                    mapTimeline.to(svgEl, {
                        opacity: 1,
                        duration: 0.6,
                        ease: 'power2.out'
                    }, 2.2);

                    // Phase 3: Settle back to idle glow (0.8s)
                    mapTimeline.to(svgEl, {
                        opacity: 0.9,
                        duration: 0.8,
                        ease: 'power2.inOut'
                    }, 2.8);

                    // Phase 4: Erase (fade paths back out) (1.2s)
                    mapTimeline.to(paths, {
                        strokeDashoffset: (i) => pathLengths[i] || 0,
                        duration: 1.2,
                        stagger: { each: 0.002, from: 'end' },
                        ease: 'power1.in'
                    }, 6.3);

                    // Phase 5: Dim the SVG while erased (0.3s)
                    mapTimeline.to(svgEl, {
                        opacity: 0.66,
                        duration: 0.3,
                        ease: 'power2.in'
                    }, 7.5);
                    syncMapAnimation();
                }
            })
            .catch((error) => {
                if (error.name !== 'AbortError') console.warn('[Landing] Vietnam SVG unavailable');
            });
        return () => {
            abortController.abort();
            mapTimeline?.kill();
            observer.disconnect();
            document.removeEventListener('visibilitychange', onVisibilityChange);
        };
    }, []);

    return React.createElement('div', { className: 'hero-map-wrapper', ref: containerRef },
        // Inline SVG container (replaces <img>)
        React.createElement('div', { className: 'vietnam-map-inline', 'aria-hidden': 'true' }),
        // Origin badge with inline flag SVG
        React.createElement('div', { className: 'vietnam-origin-badge' },
            React.createElement(InlineFlag, { className: 'flag-inline-svg' }),
            React.createElement('span', null, 'Made in Vietnam')
        ),
        // Scan rings
        React.createElement('div', { className: 'map-scan-ring map-scan-ring-1' }),
        React.createElement('div', { className: 'map-scan-ring map-scan-ring-2' }),
        // Data pulse rings (continuous idle animation)
        React.createElement('div', { className: 'data-pulse-ring data-pulse-ring-1' }),
        React.createElement('div', { className: 'data-pulse-ring data-pulse-ring-2' }),
        React.createElement('div', { className: 'data-pulse-ring data-pulse-ring-3' })
    );
};

/* Network Map Overlays - rendered separately (not inside hero-map-wrapper) */
const NetworkMapOverlays = () => React.createElement(React.Fragment, null,
    React.createElement('div', { className: 'map-radar-sweep', 'aria-hidden': 'true' }),
    React.createElement('svg', {
        className: 'map-connection-lines',
        viewBox: '0 0 100 100', preserveAspectRatio: 'none', 'aria-hidden': 'true'
    },
        React.createElement('line', { className: 'map-data-line', x1: '43', y1: '24', x2: '52', y2: '53', stroke: 'rgba(0,242,255,0.3)', strokeWidth: '0.4', strokeDasharray: '2 3' }),
        React.createElement('line', { className: 'map-data-line', x1: '52', y1: '53', x2: '58', y2: '78', stroke: 'rgba(0,242,255,0.3)', strokeWidth: '0.4', strokeDasharray: '2 3' }),
        React.createElement('line', { className: 'map-data-line', x1: '43', y1: '24', x2: '50', y2: '22', stroke: 'rgba(0,242,255,0.15)', strokeWidth: '0.3', strokeDasharray: '1 4' }),
        React.createElement('line', { className: 'map-data-line', x1: '58', y1: '78', x2: '52', y2: '84', stroke: 'rgba(0,242,255,0.15)', strokeWidth: '0.3', strokeDasharray: '1 4' })
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
                className: `flying-satellite satellite-index-${idx + 1} ${sat.cls}`,
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
            React.createElement(InlineFlag, { starClass: 'flag-star-fly' }),
            React.createElement('span', null, 'Vietnam GNSS')
        )
    );
};

/* Particle Background - Upgraded to Canvas Plexus Field (VIP Pro Master Level) */
const ParticleField = () => {
    const canvasRef = React.useRef(null);

    React.useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        let animationFrameId;
        let width = canvas.width = canvas.offsetWidth;
        let height = canvas.height = canvas.offsetHeight;

        const performanceSettings = window.CorsPerformance || {};
        const motionEnabled = performanceSettings.decorativeMotion === true;
        const maxParticleCount = 95;
        const particles = [];
        const lineBuckets = [[], [], [], []];
        let spatialGrid = [];
        let gridColumns = 0;
        let gridRows = 0;
        let gridCellSize = 0;
        let active = document.visibilityState === 'visible';
        let lastFrameAt = 0;

        const supportsFinePointer = window.matchMedia?.('(hover: hover) and (pointer: fine)').matches ?? true;

        // Mouse coordinates for interaction
        const mouse = { x: null, y: null, radius: 155 };

        const handleResize = (performanceSettings.rafThrottle || ((fn) => fn))(() => {
            if (!canvas) return;
            width = canvas.width = canvas.offsetWidth;
            height = canvas.height = canvas.offsetHeight;
        });

        const handleMouseMove = (performanceSettings.rafThrottle || ((fn) => fn))((e) => {
            const rect = canvas.getBoundingClientRect();
            mouse.x = e.clientX - rect.left;
            mouse.y = e.clientY - rect.top;
        });

        const handleMouseLeave = () => {
            mouse.x = null;
            mouse.y = null;
        };

        window.addEventListener('resize', handleResize, { passive: true });
        if (supportsFinePointer && motionEnabled) {
            window.addEventListener('mousemove', handleMouseMove, { passive: true });
            document.body.addEventListener('mouseleave', handleMouseLeave);
        }

        const onVisibility = () => {
            active = document.visibilityState === 'visible';
            if (active && !animationFrameId) animate();
        };
        document.addEventListener('visibilitychange', onVisibility);
        const observer = new IntersectionObserver(([entry]) => {
            active = entry.isIntersecting && document.visibilityState === 'visible';
            if (active && !animationFrameId) animate();
        }, { rootMargin: '100px' });
        observer.observe(canvas);

        // Initialize particles
        for (let i = 0; i < maxParticleCount; i++) {
            particles.push({
                x: Math.random() * width,
                y: Math.random() * height,
                vx: (Math.random() - 0.5) * 0.35,
                vy: (Math.random() - 0.5) * 0.35,
                radius: Math.random() * 2 + 1
            });
        }

        const animate = () => {
            if (!active) {
                animationFrameId = null;
                return;
            }
            const now = window.performance?.now?.() || Date.now();
            const frameInterval = Number(performanceSettings.frameInterval || 0);
            if (frameInterval && now - lastFrameAt < frameInterval) {
                animationFrameId = requestAnimationFrame(animate);
                return;
            }
            lastFrameAt = now;
            ctx.clearRect(0, 0, width, height);
            const activeParticleCount = Math.max(
                8,
                Math.min(particles.length, Number(performanceSettings.particleCount || 18))
            );
            const maxDistance = performanceSettings.tier === 'high' ? 140 : performanceSettings.tier === 'balanced' ? 115 : 88;

            // Update and draw particles
            for (let i = 0; i < activeParticleCount; i++) {
                const p = particles[i];

                // Mouse repel effect
                if (mouse.x !== null && mouse.y !== null) {
                    const dx = p.x - mouse.x;
                    const dy = p.y - mouse.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < mouse.radius) {
                        const force = (mouse.radius - dist) / mouse.radius;
                        const angle = Math.atan2(dy, dx);
                        const pushX = Math.cos(angle) * force * 0.55;
                        const pushY = Math.sin(angle) * force * 0.55;
                        p.vx += pushX;
                        p.vy += pushY;
                    }
                }

                // Drag/frictional damping
                p.vx *= 0.98;
                p.vy *= 0.98;

                // Drift noise
                p.vx += (Math.random() - 0.5) * 0.015;
                p.vy += (Math.random() - 0.5) * 0.015;

                // Cap speed
                const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
                const maxSpeed = 0.7;
                if (speed > maxSpeed) {
                    p.vx = (p.vx / speed) * maxSpeed;
                    p.vy = (p.vy / speed) * maxSpeed;
                }

                p.x += p.vx;
                p.y += p.vy;

                // Bounce on boundaries
                if (p.x < 0) { p.x = 0; p.vx *= -1; }
                else if (p.x > width) { p.x = width; p.vx *= -1; }
                if (p.y < 0) { p.y = 0; p.vy *= -1; }
                else if (p.y > height) { p.y = height; p.vy *= -1; }

                // Draw dot
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                ctx.fillStyle = 'rgba(0, 242, 255, 0.75)';
                ctx.fill();
            }

            // Draw connections through a spatial grid. This keeps the same
            // particle density while avoiding a full O(n^2) scan every frame.
            const cellSize = maxDistance;
            const nextColumns = Math.max(1, Math.ceil(width / cellSize));
            const nextRows = Math.max(1, Math.ceil(height / cellSize));
            if (nextColumns !== gridColumns || nextRows !== gridRows || cellSize !== gridCellSize) {
                gridColumns = nextColumns;
                gridRows = nextRows;
                gridCellSize = cellSize;
                spatialGrid = Array.from({ length: gridColumns * gridRows }, () => []);
            } else {
                spatialGrid.forEach(bucket => { bucket.length = 0; });
            }
            for (let i = 0; i < activeParticleCount; i++) {
                const p = particles[i];
                const cellX = Math.min(gridColumns - 1, Math.max(0, Math.floor(p.x / cellSize)));
                const cellY = Math.min(gridRows - 1, Math.max(0, Math.floor(p.y / cellSize)));
                spatialGrid[cellY * gridColumns + cellX].push(i);
            }

            // Four alpha buckets reduce hundreds of canvas stroke calls to at
            // most four without changing the visible distance fade.
            lineBuckets.forEach(bucket => { bucket.length = 0; });
            const maxDistanceSquared = maxDistance * maxDistance;
            for (let i = 0; i < activeParticleCount; i++) {
                const p1 = particles[i];
                const cellX = Math.min(gridColumns - 1, Math.max(0, Math.floor(p1.x / cellSize)));
                const cellY = Math.min(gridRows - 1, Math.max(0, Math.floor(p1.y / cellSize)));
                for (let offsetX = -1; offsetX <= 1; offsetX++) {
                    for (let offsetY = -1; offsetY <= 1; offsetY++) {
                        const nearbyX = cellX + offsetX;
                        const nearbyY = cellY + offsetY;
                        if (nearbyX < 0 || nearbyX >= gridColumns || nearbyY < 0 || nearbyY >= gridRows) continue;
                        const nearby = spatialGrid[nearbyY * gridColumns + nearbyX];
                        for (let nearbyIndex = 0; nearbyIndex < nearby.length; nearbyIndex++) {
                            const j = nearby[nearbyIndex];
                            if (j <= i) continue;
                            const p2 = particles[j];
                            const dx = p1.x - p2.x;
                            const dy = p1.y - p2.y;
                            const distanceSquared = dx * dx + dy * dy;
                            if (distanceSquared >= maxDistanceSquared) continue;
                            const distanceRatio = Math.sqrt(distanceSquared) / maxDistance;
                            const alphaBucket = Math.min(3, Math.floor((1 - distanceRatio) * 4));
                            lineBuckets[alphaBucket].push(p1.x, p1.y, p2.x, p2.y);
                        }
                    }
                }
            }

            ctx.lineWidth = 0.55;
            lineBuckets.forEach((lines, bucketIndex) => {
                if (!lines.length) return;
                ctx.beginPath();
                for (let index = 0; index < lines.length; index += 4) {
                    ctx.moveTo(lines[index], lines[index + 1]);
                    ctx.lineTo(lines[index + 2], lines[index + 3]);
                }
                const alpha = ((bucketIndex + 0.5) / 4) * 0.35;
                ctx.strokeStyle = `rgba(0, 242, 255, ${alpha})`;
                ctx.stroke();
            });

            animationFrameId = !motionEnabled || performanceSettings.reducedMotion ? null : requestAnimationFrame(animate);
        };

        animate();

        return () => {
            cancelAnimationFrame(animationFrameId);
            window.removeEventListener('resize', handleResize);
            if (supportsFinePointer) {
                window.removeEventListener('mousemove', handleMouseMove);
                document.body.removeEventListener('mouseleave', handleMouseLeave);
            }
            document.removeEventListener('visibilitychange', onVisibility);
            observer.disconnect();
        };
    }, []);

    return React.createElement('canvas', {
        ref: canvasRef,
        style: {
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            pointerEvents: 'none',
            zIndex: 1,
            willChange: 'transform'
        }
    });
};

/* GNSS Signal Waveform Visualizer (VIP Pro Master Level) */
const SignalWaveform = () => {
    const canvasRef = React.useRef(null);

    React.useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const reducedMotion = !window.CorsPerformance?.decorativeMotion || !!window.CorsPerformance?.reducedMotion;
        let animationFrameId = null;
        let width = canvas.width = canvas.offsetWidth;
        let height = canvas.height = canvas.offsetHeight || 40;
        let gradient = null;
        let pageVisible = document.visibilityState === 'visible';
        let sectionVisible = true;
        let lastFrameAt = 0;

        const handleResize = () => {
            if (!canvas) return;
            width = canvas.width = canvas.offsetWidth;
            height = canvas.height = canvas.offsetHeight || 40;
            gradient = null;
        };

        const scheduleIfVisible = () => {
            if (pageVisible && sectionVisible && !animationFrameId) {
                animationFrameId = requestAnimationFrame(draw);
            }
        };

        const onVisibilityChange = () => {
            pageVisible = document.visibilityState === 'visible';
            scheduleIfVisible();
        };

        window.addEventListener('resize', handleResize, { passive: true });
        document.addEventListener('visibilitychange', onVisibilityChange);

        let phase1 = 0;
        let phase2 = Math.PI / 2;

        const draw = () => {
            animationFrameId = null;
            if (!pageVisible || !sectionVisible) return;
            const now = performance.now();
            if (!reducedMotion && now - lastFrameAt < (window.CorsPerformance?.frameInterval || 1000 / 30)) {
                scheduleIfVisible();
                return;
            }
            lastFrameAt = now;
            ctx.clearRect(0, 0, width, height);

            if (!gradient) {
                gradient = ctx.createLinearGradient(0, 0, width, 0);
                gradient.addColorStop(0, 'rgba(0, 242, 255, 0)');
                gradient.addColorStop(0.15, 'rgba(0, 242, 255, 0.45)');
                gradient.addColorStop(0.5, 'rgba(0, 136, 255, 0.7)');
                gradient.addColorStop(0.85, 'rgba(0, 242, 255, 0.45)');
                gradient.addColorStop(1, 'rgba(0, 242, 255, 0)');
            }

            // Wave 1
            ctx.beginPath();
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = gradient;
            for (let x = 0; x <= width; x += 2) {
                const y = height / 2 + Math.sin(x * 0.015 + phase1) * 8 * Math.sin(x * 0.002);
                if (x === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();

            // Wave 2 (interfering signal)
            ctx.beginPath();
            ctx.lineWidth = 1.0;
            ctx.strokeStyle = gradient;
            for (let x = 0; x <= width; x += 2) {
                const y = height / 2 + Math.cos(x * 0.025 + phase2) * 6 * Math.sin(x * 0.004);
                if (x === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();

            phase1 += 0.025;
            phase2 += 0.035;

            animationFrameId = reducedMotion ? null : requestAnimationFrame(draw);
        };

        const observer = new IntersectionObserver(([entry]) => {
            sectionVisible = entry.isIntersecting;
            scheduleIfVisible();
        }, { rootMargin: '120px' });
        observer.observe(canvas);
        scheduleIfVisible();

        return () => {
            if (animationFrameId) cancelAnimationFrame(animationFrameId);
            window.removeEventListener('resize', handleResize);
            document.removeEventListener('visibilitychange', onVisibilityChange);
            observer.disconnect();
        };
    }, []);

    return React.createElement('div', {
        style: {
            width: '100%',
            maxWidth: '500px',
            height: '40px',
            margin: '12px auto',
            position: 'relative',
            opacity: 0.85
        }
    },
        React.createElement('canvas', {
            ref: canvasRef,
            style: {
                position: 'absolute',
                top: 0, left: 0,
                width: '100%', height: '100%',
                pointerEvents: 'none'
            }
        })
    );
};

/* Satellite to Map Laser Beams (Laser line pulses) */
const SatelliteBeams = () => {
    return React.createElement('svg', {
        className: 'satellite-beams-svg',
        viewBox: '0 0 100 100',
        preserveAspectRatio: 'none',
        'aria-hidden': 'true'
    },
        React.createElement('defs', null,
            React.createElement('linearGradient', { id: 'beamGrad1', x1: '0%', y1: '0%', x2: '100%', y2: '100%' },
                React.createElement('stop', { offset: '0%', stopColor: 'rgba(0, 242, 255, 0.8)' }),
                React.createElement('stop', { offset: '50%', stopColor: 'rgba(0, 242, 255, 0.2)' }),
                React.createElement('stop', { offset: '100%', stopColor: 'rgba(0, 242, 255, 0)' })
            ),
            React.createElement('linearGradient', { id: 'beamGrad2', x1: '100%', y1: '0%', x2: '0%', y2: '100%' },
                React.createElement('stop', { offset: '0%', stopColor: 'rgba(204, 93, 232, 0.8)' }),
                React.createElement('stop', { offset: '50%', stopColor: 'rgba(204, 93, 232, 0.2)' }),
                React.createElement('stop', { offset: '100%', stopColor: 'rgba(204, 93, 232, 0)' })
            ),
            React.createElement('linearGradient', { id: 'beamGrad3', x1: '100%', y1: '0%', x2: '0%', y2: '100%' },
                React.createElement('stop', { offset: '0%', stopColor: 'rgba(79, 172, 254, 0.8)' }),
                React.createElement('stop', { offset: '50%', stopColor: 'rgba(79, 172, 254, 0.2)' }),
                React.createElement('stop', { offset: '100%', stopColor: 'rgba(79, 172, 254, 0)' })
            )
        ),
        // Beam lines shooting from active satellite coordinates down to centered map
        React.createElement('line', {
            className: 'sat-beam sat-beam-1',
            x1: '9', y1: '14', x2: '48', y2: '48',
            stroke: 'url(#beamGrad1)', strokeWidth: '0.15'
        }),
        React.createElement('line', {
            className: 'sat-beam sat-beam-2',
            x1: '82', y1: '23', x2: '48', y2: '48',
            stroke: 'url(#beamGrad2)', strokeWidth: '0.15'
        }),
        React.createElement('line', {
            className: 'sat-beam sat-beam-3',
            x1: '71', y1: '42', x2: '48', y2: '48',
            stroke: 'url(#beamGrad3)', strokeWidth: '0.15'
        }),
        React.createElement('line', {
            className: 'sat-beam sat-beam-4',
            x1: '16', y1: '60', x2: '48', y2: '48',
            stroke: 'url(#beamGrad1)', strokeWidth: '0.15'
        })
    );
};

/* Three.js GNSS Precision Scene - premium idle hero atmosphere */
const HeroThreeScene = () => {
    const mountRef = React.useRef(null);

    React.useEffect(() => {
        const mount = mountRef.current;
        const qualitySettings = window.CorsPerformance || {};
        if (!mount || !window.THREE || !qualitySettings.decorativeMotion || qualitySettings.canUse3D === false) {
            if (mount) mount.classList.add('three-scene-fallback');
            return;
        }

        const prefersReducedMotion = !!qualitySettings.reducedMotion;
        const THREE = window.THREE;
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
        camera.position.set(0, 1.62, 7.6);

        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'default' });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, qualitySettings.threePixelRatio || 1.35));
        renderer.setClearColor(0x000000, 0);
        mount.appendChild(renderer.domElement);
        renderer.domElement.className = 'hero-three-canvas';
        mount.classList.add('three-scene-ready');

        const group = new THREE.Group();
        scene.add(group);

        const cyan = new THREE.Color('#00f2ff');
        const blue = new THREE.Color('#2478ff');
        const softCyan = new THREE.Color('#6ffbff');

        scene.add(new THREE.AmbientLight(0x9fdfff, 0.32));
        const keyLight = new THREE.PointLight(0x00f2ff, 5.6, 18);
        keyLight.position.set(-3.4, 3.2, 4.4);
        scene.add(keyLight);
        const rimLight = new THREE.PointLight(0x66f7ff, 2.2, 15);
        rimLight.position.set(3.6, -1.6, 3.2);
        scene.add(rimLight);

        const coreGroup = new THREE.Group();
        coreGroup.position.set(3.75, -0.08, -0.6);
        group.add(coreGroup);

        const coreGeometry = new THREE.SphereGeometry(1.02, 48, 32);
        const coreMaterial = new THREE.MeshStandardMaterial({
            color: 0x031522,
            metalness: 0.28,
            roughness: 0.5,
            emissive: 0x00445a,
            emissiveIntensity: 0.08,
            transparent: true,
            opacity: 0.03
        });
        const core = new THREE.Mesh(coreGeometry, coreMaterial);
        coreGroup.add(core);

        const wire = new THREE.LineSegments(
            new THREE.WireframeGeometry(coreGeometry),
            new THREE.LineBasicMaterial({ color: 0x00f2ff, transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending })
        );
        wire.scale.setScalar(1.012);
        coreGroup.add(wire);

        const latitudeLines = [];
        const meridianLines = [];
        const rotationMarkers = [];
        const makeCircleLine = (radius, color, opacity) => {
            const curve = new THREE.EllipseCurve(0, 0, radius, radius, 0, Math.PI * 2);
            const points = curve.getPoints(180).map(p => new THREE.Vector3(p.x, p.y, 0));
            return new THREE.LineLoop(
                new THREE.BufferGeometry().setFromPoints(points),
                new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending })
            );
        };

        [-0.64, -0.32, 0, 0.32, 0.64].forEach((y, index) => {
            const radius = Math.sqrt(Math.max(0.05, 1.02 * 1.02 - y * y));
            const line = makeCircleLine(radius, 0x00f2ff, index === 2 ? 0.22 : 0.12);
            line.position.y = y;
            coreGroup.add(line);
            latitudeLines.push(line);
        });

        for (let i = 0; i < 7; i++) {
            const line = makeCircleLine(1.03, 0x00f2ff, i % 3 === 0 ? 0.14 : 0.08);
            line.rotation.y = Math.PI / 2;
            line.rotation.z = (Math.PI / 9) * i;
            coreGroup.add(line);
            meridianLines.push(line);
        }

        const makeArcLine = (radius, start, end, color, opacity) => {
            const curve = new THREE.EllipseCurve(0, 0, radius, radius, start, end);
            const points = curve.getPoints(80).map(p => new THREE.Vector3(p.x, p.y, 0));
            const line = new THREE.Line(
                new THREE.BufferGeometry().setFromPoints(points),
                new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending })
            );
            return line;
        };
        [
            { y: 0.42, rz: 0.32, ry: 0.22, start: -0.15, end: 2.15, opacity: 0.56 },
            { y: -0.2, rz: -0.44, ry: -0.18, start: 2.75, end: 5.22, opacity: 0.38 }
        ].forEach(cfg => {
            const marker = makeArcLine(1.07, cfg.start, cfg.end, 0x9dffff, cfg.opacity);
            marker.position.y = cfg.y;
            marker.rotation.z = cfg.rz;
            marker.rotation.y = cfg.ry;
            marker.userData.baseOpacity = cfg.opacity;
            coreGroup.add(marker);
            rotationMarkers.push(marker);
        });

        const orbitingSatellites = [];
        const satelliteGeometry = new THREE.OctahedronGeometry(0.075, 0);

        const createRing = (radius, color, opacity, rotation) => {
            const curve = new THREE.EllipseCurve(0, 0, radius, radius, 0, Math.PI * 2);
            const points = curve.getPoints(180).map(p => new THREE.Vector3(p.x, 0, p.y));
            const geometry = new THREE.BufferGeometry().setFromPoints(points);
            const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity });
            const ring = new THREE.LineLoop(geometry, material);
            ring.position.copy(coreGroup.position);
            ring.rotation.set(rotation[0], rotation[1], rotation[2]);
            group.add(ring);
            return ring;
        };

        const rings = [
            createRing(1.48, cyan, 0.2, [0.8, 0.25, 0]),
            createRing(1.9, blue, 0.1, [1.28, -0.25, 0.4])
        ];

        const pulseRingGeometry = new THREE.RingGeometry(1.28, 1.31, 160);
        const pulseRingMaterial = new THREE.MeshBasicMaterial({
            color: 0x00f2ff,
            transparent: true,
            opacity: 0.18,
            side: THREE.DoubleSide,
            blending: THREE.AdditiveBlending,
            depthWrite: false
        });
        const pulseRings = [0, 1].map(index => {
            const ring = new THREE.Mesh(pulseRingGeometry, pulseRingMaterial.clone());
            ring.position.copy(coreGroup.position);
            ring.rotation.set(1.12 + index * 0.32, 0.24 + index * 0.18, index * 0.68);
            ring.userData.phase = index * 1.45;
            group.add(ring);
            return ring;
        });

        const beamMaterials = [
            new THREE.LineBasicMaterial({ color: 0x00f2ff, transparent: true, opacity: 0.12, blending: THREE.AdditiveBlending }),
            new THREE.LineBasicMaterial({ color: 0x00f2ff, transparent: true, opacity: 0.1, blending: THREE.AdditiveBlending }),
            new THREE.LineBasicMaterial({ color: 0x00f2ff, transparent: true, opacity: 0.08, blending: THREE.AdditiveBlending })
        ];
        const satelliteConfigs = [
            { radius: 2.05, speed: 0.58, phase: 0.1, tilt: [0.8, 0.25, 0], y: 0.05 },
            { radius: 2.58, speed: -0.42, phase: 1.7, tilt: [1.28, -0.25, 0.4], y: -0.08 },
            { radius: 3.08, speed: 0.33, phase: 2.6, tilt: [1.05, 0.76, -0.3], y: 0.12 }
        ];
        const satellites = satelliteConfigs.map((cfg, index) => {
            const pivot = new THREE.Group();
            pivot.position.copy(coreGroup.position);
            pivot.rotation.set(cfg.tilt[0], cfg.tilt[1], cfg.tilt[2]);
            group.add(pivot);

            const beamGeometry = new THREE.BufferGeometry();
            beamGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
            const beam = new THREE.Line(beamGeometry, beamMaterials[index]);
            group.add(beam);

            const tracker = new THREE.Object3D();
            tracker.position.set(cfg.radius, cfg.y, 0);
            pivot.add(tracker);

            const satellite = new THREE.Mesh(
                satelliteGeometry,
                new THREE.MeshBasicMaterial({
                    color: 0x9dffff,
                    transparent: true,
                    opacity: index === 0 ? 0.92 : 0.72,
                    blending: THREE.AdditiveBlending
                })
            );
            satellite.position.set(cfg.radius, cfg.y, 0);
            pivot.add(satellite);
            orbitingSatellites.push(satellite);

            return { pivot, tracker, satellite, beam, cfg };
        });

        const nodeGeometry = new THREE.SphereGeometry(0.035, 12, 12);
        const stationMaterial = new THREE.MeshBasicMaterial({ color: 0x00f2ff, transparent: true, opacity: 0.9 });
        const stationNodes = [];
        const stationLines = [];
        const stationPositions = [
            [-2.8, -1.35, -0.8],
            [-2.1, -0.88, 0.2],
            [-1.38, -1.08, -0.35],
            [-0.6, -0.72, 0.22],
            [0.18, -1.18, -0.12],
            [0.72, -0.74, 0.35]
        ];
        stationPositions.forEach((pos, index) => {
            const node = new THREE.Mesh(nodeGeometry, stationMaterial.clone());
            node.position.set(pos[0], pos[1], pos[2]);
            node.userData.phase = index * 0.65;
            group.add(node);
            node.visible = false;
            stationNodes.push(node);

            const lineGeometry = new THREE.BufferGeometry().setFromPoints([
                new THREE.Vector3(pos[0], pos[1], pos[2]),
                new THREE.Vector3(coreGroup.position.x, coreGroup.position.y, coreGroup.position.z)
            ]);
            const line = new THREE.Line(lineGeometry, new THREE.LineBasicMaterial({
                color: 0x00f2ff,
                transparent: true,
                opacity: 0.08 + index * 0.018
            }));
            group.add(line);
            line.visible = false;
            stationLines.push(line);
        });

        const particleCount = 180;
        const particlePositions = new Float32Array(particleCount * 3);
        for (let i = 0; i < particleCount; i++) {
            particlePositions[i * 3] = (Math.random() - 0.5) * 9;
            particlePositions[i * 3 + 1] = (Math.random() - 0.5) * 5.6;
            particlePositions[i * 3 + 2] = (Math.random() - 0.5) * 4.5;
        }
        const particleGeometry = new THREE.BufferGeometry();
        particleGeometry.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));
        const particleMaterial = new THREE.PointsMaterial({
            color: 0x00f2ff,
            size: 0.018,
            transparent: true,
            opacity: 0.42,
            depthWrite: false
        });
        const particles = new THREE.Points(particleGeometry, particleMaterial);
        group.add(particles);

        let rafId = null;
        let width = 0;
        let height = 0;
        let visible = document.visibilityState !== 'hidden';
        let sectionVisible = true;
        let lastFrameAt = 0;

        const resize = () => {
            const rect = mount.getBoundingClientRect();
            width = Math.max(1, Math.floor(rect.width));
            height = Math.max(1, Math.floor(rect.height));
            camera.aspect = width / height;
            camera.updateProjectionMatrix();
            renderer.setSize(width, height, false);
        };
        const handleResize = qualitySettings.rafThrottle ? qualitySettings.rafThrottle(resize) : resize;

        const onQualityChange = () => {
            renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, qualitySettings.threePixelRatio || 1));
            resize();
        };

        const onVisibilityChange = () => {
            visible = document.visibilityState !== 'hidden';
            if (visible && sectionVisible && !rafId) animate();
        };

        const clock = new THREE.Clock();
        const coreWorldPosition = new THREE.Vector3();
        const satelliteWorldPosition = new THREE.Vector3();
        const animate = () => {
            if (!visible || !sectionVisible) {
                rafId = null;
                return;
            }

            const now = window.performance?.now?.() || Date.now();
            const frameInterval = qualitySettings.frameInterval || 0;
            if (frameInterval && now - lastFrameAt < frameInterval) {
                rafId = requestAnimationFrame(animate);
                return;
            }
            lastFrameAt = now;

            const elapsed = clock.getElapsedTime();
            const visibleParticleCount = qualitySettings.tier === 'high' ? 180 : qualitySettings.tier === 'balanced' ? 72 : 28;
            particleGeometry.setDrawRange(0, visibleParticleCount);
            if (!prefersReducedMotion) {
                group.rotation.y = 0;
                group.rotation.x = 0;
                coreGroup.rotation.x = -0.08;
                coreGroup.rotation.z = 0.02;
                coreGroup.rotation.y += 0.014;
                core.rotation.y += 0.02;
                core.scale.setScalar(1);
                coreMaterial.emissiveIntensity = 0.08;
                wire.scale.setScalar(1.012);
                latitudeLines.forEach((line, index) => {
                    line.material.opacity = (index === 2 ? 0.2 : 0.1);
                });
                meridianLines.forEach((line, index) => {
                    line.material.opacity = (index % 3 === 0 ? 0.14 : 0.08);
                });
                rotationMarkers.forEach((marker, index) => {
                    marker.rotation.z += 0.008 + index * 0.003;
                    marker.material.opacity = marker.userData.baseOpacity;
                });
                rings.forEach((ring, index) => {
                    ring.rotation.z += 0.0048 + index * 0.0018;
                    ring.rotation.x += 0.0007 * (index + 1);
                    ring.material.opacity = (index === 2 ? 0.16 : 0.26) + Math.sin(elapsed * 1.2 + index) * 0.075;
                });
                pulseRings.forEach((ring, index) => {
                    const cycle = (elapsed * 0.42 + index / pulseRings.length) % 1;
                    const scale = 0.78 + cycle * 1.15;
                    ring.scale.setScalar(scale);
                    ring.rotation.z += 0.0025 + index * 0.001;
                    ring.material.opacity = Math.max(0, 0.26 * (1 - cycle));
                });
                coreGroup.getWorldPosition(coreWorldPosition);
                satellites.forEach(({ pivot, tracker, satellite, beam, cfg }, index) => {
                    pivot.rotation.y = elapsed * cfg.speed + cfg.phase;
                    satellite.rotation.x += 0.045;
                    satellite.rotation.y += 0.035;
                    tracker.getWorldPosition(satelliteWorldPosition);
                    const positions = beam.geometry.attributes.position.array;
                    positions[0] = satelliteWorldPosition.x;
                    positions[1] = satelliteWorldPosition.y;
                    positions[2] = satelliteWorldPosition.z;
                    positions[3] = coreWorldPosition.x;
                    positions[4] = coreWorldPosition.y;
                    positions[5] = coreWorldPosition.z;
                    beam.geometry.attributes.position.needsUpdate = true;
                    beam.material.opacity = 0.04 + Math.max(0, Math.sin(elapsed * 2.2 + cfg.phase)) * 0.18;
                });
                particles.rotation.y += 0.0008;
                particles.rotation.x = Math.sin(elapsed * 0.08) * 0.05;
                stationNodes.forEach((node, index) => {
                    node.scale.setScalar(1 + Math.sin(elapsed * 3.1 + node.userData.phase) * 0.48);
                    node.material.opacity = 0.54 + Math.max(0, Math.sin(elapsed * 2.5 + index)) * 0.46;
                });
                stationLines.forEach((line, index) => {
                    line.material.opacity = 0.05 + Math.max(0, Math.sin(elapsed * 1.7 + index * 0.7)) * 0.16;
                });
                camera.position.x = 0;
                camera.position.y = 1.62;
                camera.lookAt(0.45, -0.04, 0);
            }

            renderer.render(scene, camera);
            rafId = requestAnimationFrame(animate);
        };

        resize();
        animate();
        window.addEventListener('resize', handleResize, { passive: true });
        window.addEventListener('cors:qualitychange', onQualityChange);
        document.addEventListener('visibilitychange', onVisibilityChange);
        const observer = new IntersectionObserver(([entry]) => {
            sectionVisible = entry.isIntersecting;
            if (sectionVisible && visible && !rafId) animate();
        }, { rootMargin: '160px' });
        observer.observe(mount);

        return () => {
            window.removeEventListener('resize', handleResize);
            window.removeEventListener('cors:qualitychange', onQualityChange);
            document.removeEventListener('visibilitychange', onVisibilityChange);
            observer.disconnect();
            if (rafId) cancelAnimationFrame(rafId);
            renderer.dispose();
            coreGeometry.dispose();
            coreMaterial.dispose();
            nodeGeometry.dispose();
            stationMaterial.dispose();
            pulseRingGeometry.dispose();
            pulseRingMaterial.dispose();
            satelliteGeometry.dispose();
            orbitingSatellites.forEach(satellite => satellite.material.dispose());
            beamMaterials.forEach(material => material.dispose());
            particleGeometry.dispose();
            particleMaterial.dispose();
            rings.forEach(ring => {
                ring.geometry.dispose();
                ring.material.dispose();
            });
            pulseRings.forEach(ring => ring.material.dispose());
            satellites.forEach(({ beam }) => beam.geometry.dispose());
            latitudeLines.forEach(line => {
                line.geometry.dispose();
                line.material.dispose();
            });
            meridianLines.forEach(line => {
                line.geometry.dispose();
                line.material.dispose();
            });
            rotationMarkers.forEach(line => {
                line.geometry.dispose();
                line.material.dispose();
            });
            stationNodes.forEach(node => node.material.dispose());
            stationLines.forEach(line => {
                line.geometry.dispose();
                line.material.dispose();
            });
            if (renderer.domElement && renderer.domElement.parentNode) {
                renderer.domElement.parentNode.removeChild(renderer.domElement);
            }
        };
    }, []);

    return React.createElement('div', {
        ref: mountRef,
        className: 'hero-three-scene',
        'aria-hidden': 'true'
    },
        React.createElement('div', { className: 'hero-three-fallback-core' }),
        React.createElement('div', { className: 'hero-three-fallback-ring hero-three-fallback-ring-1' }),
        React.createElement('div', { className: 'hero-three-fallback-ring hero-three-fallback-ring-2' })
    );
};

/* 3D Tilt Hook for Feature Cards (Interactive Hologram Effect) */
const useCardTilt = () => {
    React.useEffect(() => {
        if (window.CorsPerformance?.reducedMotion) return;
        const cards = document.querySelectorAll('.landing-feature-card-inner');
        if (!cards.length) return;

        const handleMouseMove = (e) => {
            const card = e.currentTarget;
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const w = rect.width;
            const h = rect.height;

            const rotateX = -((y / h) - 0.5) * 14;
            const rotateY = ((x / w) - 0.5) * 14;

            const shine = card.querySelector('.card-light-shine');
            if (shine) {
                const px = (x / w) * 100;
                const py = (y / h) * 100;
                shine.style.background = `radial-gradient(circle at ${px}% ${py}%, rgba(0, 242, 255, 0.18) 0%, transparent 60%)`;
            }

            if (typeof gsap !== 'undefined') {
                gsap.to(card, {
                    rotateX: rotateX,
                    rotateY: rotateY,
                    scale: 1.025,
                    duration: 0.3,
                    ease: 'power1.out',
                    transformPerspective: 800,
                    overwrite: 'auto'
                });
            }
        };

        const handleMouseLeave = (e) => {
            const card = e.currentTarget;
            const shine = card.querySelector('.card-light-shine');
            if (shine) {
                shine.style.background = 'transparent';
            }
            if (typeof gsap !== 'undefined') {
                gsap.to(card, {
                    rotateX: 0,
                    rotateY: 0,
                    scale: 1,
                    duration: 0.5,
                    ease: 'power2.out',
                    overwrite: 'auto'
                });
            }
        };

        cards.forEach(card => {
            if (!card.querySelector('.card-light-shine')) {
                const shine = document.createElement('div');
                shine.className = 'card-light-shine';
                card.appendChild(shine);
            }

            card.style.transformStyle = 'preserve-3d';
            const front = card.querySelector('.landing-feature-card-front');
            if (front) {
                front.style.transform = 'translateZ(25px)';
                front.style.transformStyle = 'preserve-3d';
            }

            card.addEventListener('mousemove', handleMouseMove);
            card.addEventListener('mouseleave', handleMouseLeave);
        });

        return () => {
            cards.forEach(card => {
                card.removeEventListener('mousemove', handleMouseMove);
                card.removeEventListener('mouseleave', handleMouseLeave);
            });
        };
    }, []);
};

/* Magnetic Button Hook for Hero Call-To-Actions */
const useMagneticButtons = () => {
    React.useEffect(() => {
        if (window.CorsPerformance?.reducedMotion) return;
        if (typeof gsap === 'undefined') return;
        const buttons = document.querySelectorAll('.btn-enterprise, .btn-outline-cyan');
        if (!buttons.length) return;

        const handleMouseMove = (e) => {
            const btn = e.currentTarget;
            const rect = btn.getBoundingClientRect();
            const x = e.clientX - (rect.left + rect.width / 2);
            const y = e.clientY - (rect.top + rect.height / 2);
            
            gsap.to(btn, {
                x: x * 0.35,
                y: y * 0.35,
                scale: 1.04,
                duration: 0.3,
                ease: 'power1.out',
                overwrite: 'auto'
            });
        };

        const handleMouseLeave = (e) => {
            const btn = e.currentTarget;
            gsap.to(btn, {
                x: 0,
                y: 0,
                scale: 1,
                duration: 0.6,
                ease: 'elastic.out(1.1, 0.4)',
                overwrite: 'auto'
            });
        };

        buttons.forEach(btn => {
            btn.addEventListener('mousemove', handleMouseMove);
            btn.addEventListener('mouseleave', handleMouseLeave);
        });

        return () => {
            buttons.forEach(btn => {
                btn.removeEventListener('mousemove', handleMouseMove);
                btn.removeEventListener('mouseleave', handleMouseLeave);
            });
        };
    }, []);
};

/* Why Choose Us Section */
const WhyChooseSection = () => {
    const items = [
        { number: '10+', label: 'Năm kinh nghiệm', desc: 'Triển khai và vận hành hạ tầng GNSS/Base quy mô quốc gia với đội ngũ kỹ sư chuyên sâu.', icon: 'bi-award' },
        { number: '50+', label: 'Đối tác tin dùng', desc: 'Doanh nghiệp khảo sát, xây dựng và viễn thám trên toàn quốc đã lựa chọn nền tảng Aitogy.', icon: 'bi-people' },
        { number: '24/7', label: 'Hỗ trợ kỹ thuật', desc: 'Đội ngũ vận hành trực 24 giờ, xử lý sự cố trong vòng 15 phút qua Telegram và hotline.', icon: 'bi-headset' }
    ];
    return React.createElement('section', { className: 'why-choose-section container' },
        React.createElement('div', { className: 'text-center mb-5' },
            React.createElement('div', { className: 'why-choose-kicker' },
                React.createElement(InlineFlag, { w: '20', h: '14' }),
                'Đồng hành cùng Việt Nam'
            ),
            React.createElement('h2', { className: 'neon-title', style: { fontSize: '2.2rem', fontWeight: 900, color: '#fff' } }, 'Tại sao chọn Aitogy?'),
            React.createElement('p', { style: { color: '#6a8aaa', maxWidth: '600px', margin: '0 auto' } },
                'Nền tảng Base duy nhất được thiết kế và vận hành bởi đội ngũ kỹ sư Việt Nam, tối ưu cho điều kiện thực tế trong nước.'
            )
        ),
        React.createElement('div', { className: 'why-choose-grid' },
            items.map((item, idx) =>
                React.createElement('div', { key: idx, className: 'why-choose-card' },
                    React.createElement('div', { className: 'why-choose-icon' },
                        React.createElement('i', { className: `bi ${item.icon}` })
                    ),
                    React.createElement('div', { className: 'why-choose-number' }, item.number),
                    React.createElement('div', { className: 'why-choose-label' }, item.label),
                    React.createElement('div', { className: 'why-choose-desc' }, item.desc)
                )
            )
        )
    );
};

/* Scroll Progress Bar */
const ScrollProgressBar = () => React.createElement('div', { className: 'scroll-progress-wrap', 'aria-hidden': 'true' },
    React.createElement('div', { className: 'scroll-progress-fill' })
);

/* Typing Headline - cycles through phrases with typing/erasing effect */
const TypingHeadline = () => {
    const phrases = [
        'Trung tâm điều hành Base',
        'Hạ tầng GNSS thế hệ mới',
        'Định vị chính xác centimet'
    ];
    const reducedMotion = !window.CorsPerformance?.decorativeMotion || !!window.CorsPerformance?.reducedMotion;
    const [displayed, setDisplayed] = React.useState(reducedMotion ? phrases[0] : '');
    const [phraseIdx, setPhraseIdx] = React.useState(0);
    const [isDeleting, setIsDeleting] = React.useState(false);

    React.useEffect(() => {
        if (reducedMotion) return;
        const current = phrases[phraseIdx];
        let timeout;

        if (!isDeleting && displayed.length < current.length) {
            // Typing
            timeout = setTimeout(() => setDisplayed(current.slice(0, displayed.length + 1)), 60);
        } else if (!isDeleting && displayed.length === current.length) {
            // Hold for 4s then start deleting
            timeout = setTimeout(() => setIsDeleting(true), 4000);
        } else if (isDeleting && displayed.length > 0) {
            // Erasing
            timeout = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 30);
        } else if (isDeleting && displayed.length === 0) {
            // Move to next phrase
            setIsDeleting(false);
            setPhraseIdx((phraseIdx + 1) % phrases.length);
        }

        return () => clearTimeout(timeout);
    }, [displayed, isDeleting, phraseIdx]);

    return React.createElement('div', { className: 'typing-headline-wrap' },
        React.createElement('span', null, displayed),
        React.createElement('span', { className: 'typing-cursor' })
    );
};

/* Live Data Bars Continuous Animation Hook */
const useLiveDataAnimation = () => {
    React.useEffect(() => {
        if (!window.CorsPerformance?.decorativeMotion || window.CorsPerformance?.reducedMotion) return;
        const section = document.querySelector('.livedata-section');
        if (!section) return;
        const bars = section.querySelectorAll('.livedata-bar');
        let intervalId = null;
        let ready = false;
        let sectionVisible = false;
        let pageVisible = document.visibilityState === 'visible';

        const updateBars = () => {
            bars.forEach(bar => {
                const newHeight = 20 + Math.random() * 60;
                bar.style.height = newHeight + '%';
            });
        };
        const syncAnimation = () => {
            const shouldRun = ready && sectionVisible && pageVisible;
            if (shouldRun && !intervalId) {
                updateBars();
                intervalId = setInterval(updateBars, 2000);
            } else if (!shouldRun && intervalId) {
                clearInterval(intervalId);
                intervalId = null;
            }
        };
        const onVisibilityChange = () => {
            pageVisible = document.visibilityState === 'visible';
            syncAnimation();
        };
        const observer = new IntersectionObserver(([entry]) => {
            sectionVisible = entry.isIntersecting;
            syncAnimation();
        }, { rootMargin: '180px' });
        observer.observe(section);

        // Start after initial scroll animations have had time to play
        const delay = setTimeout(() => {
            ready = true;
            syncAnimation();
        }, 3000);
        document.addEventListener('visibilitychange', onVisibilityChange);
        return () => {
            clearTimeout(delay);
            if (intervalId) clearInterval(intervalId);
            observer.disconnect();
            document.removeEventListener('visibilitychange', onVisibilityChange);
        };
    }, []);
};

/* Pause CSS-only motion for sections that are well outside the viewport. The
   complete animation resumes before a section becomes visible. */
const useSectionAnimationVisibility = () => {
    React.useEffect(() => {
        const root = document.querySelector('.enterprise-homepage');
        if (!root) return;
        const sections = Array.from(root.querySelectorAll('section, footer'));
        const margin = Math.max(180, window.innerHeight * 0.2);
        sections.forEach(section => {
            section.classList.add('landing-perf-section');
            const rect = section.getBoundingClientRect();
            section.classList.toggle('is-near-viewport', rect.bottom >= -margin && rect.top <= window.innerHeight + margin);
        });

        const observer = new IntersectionObserver(entries => {
            entries.forEach(entry => entry.target.classList.toggle('is-near-viewport', entry.isIntersecting));
        }, { rootMargin: '20% 0px' });
        sections.forEach(section => observer.observe(section));

        const onVisibilityChange = () => {
            root.classList.toggle('landing-page-hidden', document.visibilityState === 'hidden');
        };
        document.addEventListener('visibilitychange', onVisibilityChange);
        onVisibilityChange();

        return () => {
            observer.disconnect();
            document.removeEventListener('visibilitychange', onVisibilityChange);
            sections.forEach(section => section.classList.remove('landing-perf-section', 'is-near-viewport'));
            root.classList.remove('landing-page-hidden');
        };
    }, []);
};

/* Mouse Parallax Hook - shifts map and orbs based on cursor position */
const useMouseParallax = () => {
    React.useEffect(() => {
        if (!window.CorsPerformance?.decorativeMotion || window.CorsPerformance?.reducedMotion) return;
        if (typeof gsap === 'undefined' || !window.matchMedia?.('(hover: hover) and (pointer: fine)').matches) return;
        const hero = document.querySelector('.hero-section');
        if (!hero) return;

        const mapQuickX = gsap.quickTo('.hero-map-wrapper', 'x', { duration: 0.8, ease: 'power2.out' });
        const mapQuickY = gsap.quickTo('.hero-map-wrapper', 'y', { duration: 0.8, ease: 'power2.out' });

        const handleMouseMove = (e) => {
            const rect = hero.getBoundingClientRect();
            const cx = (e.clientX - rect.left) / rect.width - 0.5; // -0.5 to 0.5
            const cy = (e.clientY - rect.top) / rect.height - 0.5;
            mapQuickX(cx * 20);  // ±10px shift
            mapQuickY(cy * 15);
        };

        hero.addEventListener('mousemove', handleMouseMove);
        return () => hero.removeEventListener('mousemove', handleMouseMove);
    }, []);
};

/* Animated Scramble Counter Hook (VIP Pro Master Level) */
const useCounter = (targetStr, duration = 1600) => {
    const [displayValue, setDisplayValue] = React.useState('');

    React.useEffect(() => {
        if (targetStr === undefined || targetStr === null || targetStr === '') return;
        const target = String(targetStr);
        if (!window.CorsPerformance?.decorativeMotion || window.CorsPerformance?.reducedMotion) {
            setDisplayValue(target);
            return;
        }
        
        // Extract digits and symbols
        const chars = target.split('');
        const length = chars.length;
        
        let isCancelled = false;
        let elapsed = 0;
        const intervalTime = 30; // ~33 FPS
        
        const timer = setInterval(() => {
            elapsed += intervalTime;
            const progress = Math.min(elapsed / duration, 1);
            
            // Generate scramble string
            const currentScramble = chars.map((char, index) => {
                if (/[^0-9]/.test(char)) return char; // Preserve dots, plus, percentages, etc.
                
                // Digits settle from left to right
                const threshold = (index / length) * 0.7;
                if (progress > threshold + 0.25 || progress === 1) {
                    return char;
                }
                
                return Math.floor(Math.random() * 10);
            }).join('');
            
            if (!isCancelled) {
                setDisplayValue(currentScramble);
            }
            
            if (progress === 1) {
                clearInterval(timer);
            }
        }, intervalTime);
        
        return () => {
            isCancelled = true;
            clearInterval(timer);
        };
    }, [targetStr, duration]);
    
    return displayValue;
};

/* ============================================================
   CINEMATIC SCROLL ENGINE (VIP PRO) - FULL EDITION v2
   ============================================================ */
const useCinematicScroll = () => {
    React.useEffect(() => {
        const performanceProfile = window.CorsPerformance || {};
        const liteMode = performanceProfile.tier === 'lite';
        if (!performanceProfile.decorativeMotion || performanceProfile.reducedMotion) return;
        if (typeof gsap === 'undefined' || typeof ScrollTrigger === 'undefined') {
            console.warn('[Cinematic] GSAP not loaded');
            return;
        }
        gsap.registerPlugin(ScrollTrigger);
        let lenis = null;
        let lenisTicker = null;
        const supportsSmoothWheel = window.matchMedia?.('(hover: hover) and (pointer: fine)').matches ?? true;
        try {
            if (!liteMode && supportsSmoothWheel && typeof Lenis !== 'undefined') {
                lenis = new Lenis({ duration: 1.2, easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)), smoothWheel: true });
                lenis.on('scroll', ScrollTrigger.update);
                lenisTicker = (time) => lenis.raf(time * 1000);
                gsap.ticker.add(lenisTicker);
            }
        } catch(e) {}

        let scrollContext = null;
        const initTimer = setTimeout(() => {
            const landingScope = document.querySelector('.enterprise-homepage') || document.body;
            scrollContext = gsap.context(() => {
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
                y: -24, opacity: 0.42, ease: 'none'
            });
            if (mapWrapper) {
                const compactMap = window.innerWidth <= 991;
                const getMapTarget = (axis) => {
                    const slot = document.querySelector('.network-map-slot');
                    if (!slot || !mapWrapper) return 0;
                    const slotRect = slot.getBoundingClientRect();
                    const mapRect = mapWrapper.getBoundingClientRect();
                    const slotCenterX = slotRect.left + window.scrollX + slotRect.width / 2;
                    const slotCenterY = slotRect.top + window.scrollY + slotRect.height / 2;
                    const mapCenterX = mapRect.left + window.scrollX + mapRect.width / 2;
                    const mapCenterY = mapRect.top + window.scrollY + mapRect.height / 2;
                    return axis === 'x' ? slotCenterX - mapCenterX : slotCenterY - mapCenterY;
                };

                gsap.fromTo(mapWrapper,
                    { x: 0, y: 0, scale: 1, opacity: compactMap ? 0.42 : 1 },
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
                        scale: 0.78,
                        opacity: compactMap ? 0.92 : 0.96,
                        ease: 'power1.inOut',
                        immediateRender: false
                    }
                );

                // Fade out subtly after leaving network section
                gsap.to(mapWrapper, {
                    scrollTrigger: {
                        trigger: '.network-map-section',
                        start: 'bottom center',
                        end: 'bottom top',
                        scrub: 1,
                        invalidateOnRefresh: true
                    },
                    opacity: 0,
                    scale: 0.7,
                    ease: 'none'
                });
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
                const featureHeader = document.querySelector('.landing-features .text-center');
                const featureInners = gsap.utils.toArray('.landing-feature-card-inner');
                const featureIcons = gsap.utils.toArray('.landing-feature-card-front .landing-feature-icon');
                const featureScans = gsap.utils.toArray('.feature-card-scan');
                const featureLines = gsap.utils.toArray('.feature-signal-line');

                gsap.set(featureCards, { autoAlpha: 1, transformPerspective: 1200, transformOrigin: 'center bottom' });
                gsap.set(featureInners, { transformPerspective: 1200, transformOrigin: 'center center' });
                gsap.set(featureScans, { xPercent: -150, opacity: 0 });
                gsap.set(featureLines, { scaleX: 0, transformOrigin: 'left center' });

                const featureTimeline = gsap.timeline({
                    scrollTrigger: {
                        trigger: '.landing-features',
                        start: 'top 88%',
                        toggleActions: 'play none none none',
                        once: true
                    }
                });

                if (featureHeader) {
                    featureTimeline.from(featureHeader, {
                        y: 80,
                        opacity: 0,
                        duration: 0.8,
                        ease: 'power3.out'
                    }, 0);
                }

                featureTimeline
                    .fromTo(featureCards, {
                        y: 120,
                        autoAlpha: 0,
                        scale: 0.84,
                        rotateX: 18,
                        rotateZ: (i) => i % 2 === 0 ? -2.5 : 2.5
                    }, {
                        autoAlpha: 1,
                        y: 0,
                        scale: 1,
                        rotateX: 0,
                        rotateZ: 0,
                        stagger: 0.18,
                        duration: 1.2,
                        ease: 'power4.out'
                    }, 0.18)
                    .from(featureInners, {
                        y: 28,
                        stagger: 0.18,
                        duration: 1,
                        ease: 'power3.out'
                    }, 0.28)
                    .from(featureIcons, {
                        scale: 0.55,
                        rotate: -18,
                        opacity: 0,
                        stagger: 0.18,
                        duration: 0.7,
                        ease: 'back.out(1.8)'
                    }, 0.52)
                    .to(featureLines, {
                        scaleX: 1,
                        stagger: 0.18,
                        duration: 0.7,
                        ease: 'power2.inOut'
                    }, 0.62)
                    .to(featureScans, {
                        xPercent: 260,
                        opacity: 1,
                        stagger: 0.18,
                        duration: 0.65,
                        ease: 'power2.inOut'
                    }, 0.68)
                    .to(featureScans, {
                        opacity: 0,
                        stagger: 0.18,
                        duration: 0.25,
                        ease: 'power1.out'
                    }, 1.12);

                featureTimeline.set(featureInners, { clearProps: 'filter' });

                gsap.to('.feature-orbit-left', {
                    y: -70,
                    rotation: 22,
                    ease: 'none',
                    scrollTrigger: {
                        trigger: '.landing-features',
                        start: 'top bottom',
                        end: 'bottom top',
                        scrub: 1.2
                    }
                });
                gsap.to('.feature-orbit-right', {
                    y: -110,
                    rotation: -28,
                    ease: 'none',
                    scrollTrigger: {
                        trigger: '.landing-features',
                        start: 'top bottom',
                        end: 'bottom top',
                        scrub: 1.2
                    }
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
            gsap.from('.support-tech-title', {
                scrollTrigger: { trigger: '.support-tech-section', start: 'top 88%', toggleActions: 'play none none none', once: true },
                opacity: 0, y: 24, duration: 0.65, ease: 'power3.out'
            });
            gsap.from('.support-tech-marquee', {
                scrollTrigger: { trigger: '.support-tech-section', start: 'top 84%', toggleActions: 'play none none none', once: true },
                opacity: 0, duration: 0.5, ease: 'power2.out'
            });
            const techTrack = document.querySelector('.support-tech-track');
            if (techTrack) {
                gsap.killTweensOf(techTrack);
                gsap.fromTo(techTrack,
                    { x: () => -(techTrack.scrollWidth / 2) },
                    {
                        x: 0,
                        duration: 24,
                        repeat: -1,
                        ease: 'none',
                        repeatRefresh: true
                    }
                );
            }
            gsap.from('.support-tech-panel', {
                scrollTrigger: { trigger: '.support-tech-section', start: 'top 82%', toggleActions: 'play none none none', once: true },
                opacity: 0, y: 26, duration: 0.65, stagger: 0.1, ease: 'power3.out'
            });
            gsap.from('.support-tech-line', {
                scrollTrigger: { trigger: '.support-tech-section', start: 'top 82%', toggleActions: 'play none none none', once: true },
                scaleX: 0,
                transformOrigin: 'left center',
                duration: 0.8,
                ease: 'power2.out'
            });
            // Horizontal scan line: sweep from left to right continuously
            const scanEl = document.querySelector('.support-tech-scan');
            const lineWrapEl = document.querySelector('.support-tech-line-wrap');
            if (scanEl && lineWrapEl) {
                gsap.killTweensOf(scanEl);
                gsap.fromTo(scanEl,
                    { x: -220, autoAlpha: 0.95 },
                    {
                        x: () => lineWrapEl.offsetWidth + 220,
                        duration: 1.9,
                        repeat: -1,
                        ease: 'none'
                    }
                );
            }

            // ========== CTA ==========
            const ctaBox = document.querySelector('.cta-box');
            if (ctaBox) {
                gsap.from(ctaBox, {
                    scrollTrigger: { trigger: ctaBox, start: 'top 85%', toggleActions: 'play none none none', once: true },
                    y: 70, opacity: 0, scale: 0.94, duration: 0.8, ease: 'power3.out'
                });
                gsap.from('.cta-title', {
                    scrollTrigger: { trigger: ctaBox, start: 'top 82%', toggleActions: 'play none none none', once: true },
                    y: 28, opacity: 0, duration: 0.6, delay: 0.08, ease: 'power3.out'
                });
                gsap.from('.cta-desc', {
                    scrollTrigger: { trigger: ctaBox, start: 'top 82%', toggleActions: 'play none none none', once: true },
                    y: 22, opacity: 0, duration: 0.6, delay: 0.14, ease: 'power3.out'
                });
                gsap.from('.cta-actions .btn-enterprise, .cta-actions .btn-outline-cyan', {
                    scrollTrigger: { trigger: ctaBox, start: 'top 82%', toggleActions: 'play none none none', once: true },
                    y: 18, opacity: 0, duration: 0.55, delay: 0.2, stagger: 0.08, ease: 'power2.out'
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

            // ========== SCROLL PROGRESS BAR ==========
            const progressFill = document.querySelector('.scroll-progress-fill');
            if (progressFill) {
                gsap.to(progressFill, {
                    height: '100%',
                    ease: 'none',
                    scrollTrigger: { trigger: document.body, start: 'top top', end: 'bottom bottom', scrub: 0.3 }
                });
            }

            // ========== WHY CHOOSE SECTION ==========
            const whyCards = gsap.utils.toArray('.why-choose-card');
            if (whyCards.length) {
                const whyKicker = document.querySelector('.why-choose-kicker');
                const whyTitle = document.querySelector('.why-choose-section .neon-title');
                if (whyKicker) gsap.from(whyKicker, {
                    scrollTrigger: { trigger: '.why-choose-section', start: 'top 82%', toggleActions: 'play none none reverse' },
                    y: 30, opacity: 0, duration: 0.6, ease: 'power3.out'
                });
                if (whyTitle) gsap.from(whyTitle, {
                    scrollTrigger: { trigger: '.why-choose-section', start: 'top 80%', toggleActions: 'play none none reverse' },
                    y: 50, opacity: 0, duration: 0.8, delay: 0.1, ease: 'power3.out'
                });
                whyCards.forEach((card, i) => {
                    gsap.from(card, {
                        scrollTrigger: { trigger: card, start: 'top 88%', toggleActions: 'play none none reverse' },
                        y: 80, opacity: 0, scale: 0.88, rotateY: i === 1 ? 0 : (i === 0 ? 12 : -12),
                        duration: 1, delay: i * 0.15, ease: 'power3.out'
                    });
                });
                gsap.utils.toArray('.why-choose-number').forEach(num => {
                    gsap.from(num, {
                        scrollTrigger: { trigger: num, start: 'top 85%', toggleActions: 'play none none reverse' },
                        scale: 0.4, opacity: 0, duration: 0.9, ease: 'back.out(1.7)'
                    });
                });
                gsap.utils.toArray('.why-choose-icon').forEach((icon, i) => {
                    gsap.from(icon, {
                        scrollTrigger: { trigger: icon, start: 'top 88%', toggleActions: 'play none none reverse' },
                        scale: 0, rotation: -180, opacity: 0, duration: 0.7, delay: i * 0.12, ease: 'back.out(2)'
                    });
                });
            }

            // ========== FLAG STAR ROTATION ==========
            gsap.to('.flag-star', {
                rotation: 360, transformOrigin: '50% 50%',
                duration: 20, repeat: -1, ease: 'none'
            });
            gsap.to('.flag-star-fly', {
                rotation: -360, transformOrigin: '50% 50%',
                duration: 16, repeat: -1, ease: 'none'
            });

            // ========== MAP CONNECTION LINES REVEAL ==========
            gsap.utils.toArray('.map-data-line').forEach((line, i) => {
                gsap.fromTo(line,
                    { opacity: 0 },
                    {
                        opacity: 1,
                        duration: 1.2,
                        delay: 1 + i * 0.3,
                        ease: 'power2.out',
                        scrollTrigger: { trigger: '.network-map-section', start: 'top 70%', toggleActions: 'play none none reverse' }
                    }
                );
            });

            // ========== MAP RADAR SWEEP REVEAL ==========
            const radar = document.querySelector('.map-radar-sweep');
            if (radar) {
                gsap.fromTo(radar,
                    { opacity: 0, scale: 0.3 },
                    {
                        opacity: 0.5, scale: 1,
                        duration: 1.5,
                        ease: 'power2.out',
                        scrollTrigger: { trigger: '.network-map-section', start: 'top 75%', toggleActions: 'play none none reverse' }
                    }
                );
            }
            }, landingScope);
        }, 500);

        const hideIndicator = () => {
            const el = document.querySelector('.scroll-indicator');
            if (el) el.classList.toggle('hidden', window.scrollY > 200);
        };
        window.addEventListener('scroll', hideIndicator, { passive: true });

        return () => {
            clearTimeout(initTimer);
            window.removeEventListener('scroll', hideIndicator);
            if (scrollContext) scrollContext.revert();
            if (lenisTicker) gsap.ticker.remove(lenisTicker);
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
            const clientMeta = window.ApiClient && typeof window.ApiClient.getClientMeta === 'function'
                ? await window.ApiClient.getClientMeta()
                : { device_id: null, device_name: null, local_ip: null };

            const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: username.trim(),
                    password,
                    remember_me: remember,
                    device_id: clientMeta.device_id,
                    device_name: clientMeta.device_name,
                    local_ip: clientMeta.local_ip
                })
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
            }, '×'),
            React.createElement('div', { className: 'login-panel-header' },
                React.createElement('img', {
                    src: '/assets/img/aitogy.png', alt: 'Aitogy',
                    className: 'login-panel-logo'
                }),
                React.createElement('div', { className: 'login-panel-title' }, 'TRUNG TÂM ĐIỀU HÀNH Base')
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
                        }, 'Thiết bị và hạ tầng Base chính xác cao')
                    )
                ),
                React.createElement('div', { className: 'login-company-info' },
                    React.createElement('strong', null, 'AITOGY .,JSC'),
                    React.createElement('br'),
                    'Hệ thống trạm tham chiếu hoạt động liên tục toàn quốc'
                )
            )
        )
    );
};



/* ============================================================
   CINEMATIC FEATURE CARDS v6 - GSAP SCROLL SEQUENCE
   ============================================================ */
const CinematicFeatures = ({ features }) => {
    return React.createElement('section', { className: 'landing-features cinematic-feature-section container', style: { position: 'relative' } },
        React.createElement('div', { className: 'feature-orbit feature-orbit-left', 'aria-hidden': 'true' }),
        React.createElement('div', { className: 'feature-orbit feature-orbit-right', 'aria-hidden': 'true' }),

        React.createElement('div', { className: 'text-center mb-5' },
            React.createElement('h2', { style: { fontSize: '2.2rem', fontWeight: 800, color: '#fff' } }, 'Tính năng nổi bật'),
            React.createElement('p', { style: { color: '#6a8aaa' } }, 'Nền tảng Base thế hệ mới cho hạ tầng đo đạc Việt Nam')
        ),

        React.createElement('div', { className: 'row g-4 feature-card-grid' },
            features.map((f, idx) =>
                React.createElement('div', { key: idx, className: 'col-lg-3 col-md-6' },
                    React.createElement('div', { className: 'landing-feature-card-wrapper' },
                        React.createElement('div', { className: 'landing-feature-card-inner' },
                            React.createElement('div', { className: 'feature-card-scan', 'aria-hidden': 'true' }),
                            React.createElement('div', { className: 'feature-card-scan-continuous', 'aria-hidden': 'true' }),
                            React.createElement('div', { className: 'landing-feature-card-front' },
                                React.createElement('div', { className: 'feature-card-index' }, `0${idx + 1}`),
                                React.createElement('div', { className: `landing-feature-icon ${f.iconClass}` },
                                    React.createElement('i', { className: `bi ${f.icon}` })
                                ),
                                React.createElement('div', { className: 'landing-feature-title' }, f.title),
                                React.createElement('div', { className: 'landing-feature-desc' }, f.desc),
                                React.createElement('div', { className: 'feature-signal-line', 'aria-hidden': 'true' }),
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
const HomePageEnterprise = ({ devices = [], networkSummary = null }) => {
    const isLoggedIn = !!(localStorage.getItem('access_token') || sessionStorage.getItem('access_token'));

    const [loginOpen, setLoginOpen] = React.useState(false);
    useCinematicScroll();
    useLiveDataAnimation();
    useSectionAnimationVisibility();
    useMouseParallax();
    useCardTilt();
    useMagneticButtons();
    React.useEffect(() => {
        const updateNavbar = (window.CorsPerformance?.rafThrottle || ((fn) => fn))(() => {
            document.getElementById('landing-navbar')?.classList.toggle('scrolled', window.scrollY > 50);
        });
        window.addEventListener('scroll', updateNavbar, { passive: true });
        updateNavbar();
        return () => window.removeEventListener('scroll', updateNavbar);
    }, []);

    // Stats Animation Logic
    const stationTotal = Number(networkSummary?.total_stations || devices.length || 0);
    const visibleStationTotal = devices.length || 0;
    const animStations = useCounter(stationTotal);
    const activeStatuses = ['online', 'sleep', 'streaming', 'starting'];
    const onlineCount = Number(networkSummary?.active_stations || devices.filter(d => activeStatuses.includes(d.status)).length);
    const animOnline = useCounter(onlineCount);
    
    // Unique provinces/cities
    const provinces = new Set(devices.filter(d => d.location).map(d => d.location.split(',').pop().trim()));
    const animProvinces = useCounter(Number(networkSummary?.coverage_areas || provinces.size || 24));
    
    // Uptime calculation (simulated or real)
    const animUptime = useCounter('99.98%');
    const toStationSlug = (value) => String(value || '')
        .normalize('NFKD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-zA-Z0-9._-]+/g, '.')
        .replace(/\.{2,}/g, '.')
        .replace(/^\.+|\.+$/g, '')
        .toLowerCase();
    const openStationMap = () => {
        if (typeof window.openPublicMap === 'function') {
            window.openPublicMap();
            return;
        }

        const modalEl = document.getElementById('publicMapModal');
        if (modalEl && window.bootstrap?.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
            return;
        }

        document.querySelector('.network-map-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    const openTechnicalConsult = () => {
        const subject = encodeURIComponent('Tu van ky thuat Base Aitogy');
        const body = encodeURIComponent('Chao Aitogy,\n\nToi can tu van ve giai phap Base/GNSS/RTK.');
        window.location.href = `mailto:aitogyaitogy@gmail.com?subject=${subject}&body=${body}`;
    };

    const stats = [
        { label: 'Tổng số trạm', value: animStations, sub: 'Mạng lưới Base Aitogy', icon: 'bi-broadcast-pin', color: '#4facfe' },
        { label: 'Đang hoạt động', value: animOnline, sub: 'Online · Sleep · Streaming', icon: 'bi-check-circle-fill', color: '#51cf66' },
        { label: 'Khu vực phủ sóng', value: animProvinces, sub: 'Tỉnh/TP trên toàn quốc', icon: 'bi-geo-alt-fill', color: '#cc5de8' },
        { label: 'Uptime', value: animUptime, sub: 'Tỷ lệ hoạt động', icon: 'bi-lightning-charge-fill', color: '#00f2ff' }
    ];

    const features = [
        { 
            icon: 'bi-cpu', 
            iconClass: 'feature-icon-blue', 
            title: 'Đa chòm sao vệ tinh',
            desc: 'Theo dõi GPS, GLONASS, Galileo, BeiDou để tối ưu độ chính xác định vị.',
            longDesc: 'Hệ thống tích hợp toàn diện 4 hệ thống vệ tinh GPS, GLONASS, Galileo và BeiDou. Khả năng thu nhận tín hiệu đa tần số giúp loại bỏ sai số tầng điện ly, đảm bảo độ chính xác mức centimet ngay cả trong môi trường đô thị dày đặc hoặc tán cây.'
        },
        { 
            icon: 'bi-wifi', 
            iconClass: 'feature-icon-green', 
            title: 'NTRIP Caster tốc độ cao',
            desc: 'Truyền dữ liệu RTK/RTCM tối ưu với độ trễ cực thấp.',
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
            longDesc: 'Toàn bộ luồng dữ liệu được mã hóa TLS/SSL. Cơ chế phân quyền chi tiết cho phép quản lý từng tài khoản Rover, giới hạn khu vực hoạt động (Geofencing) và thời gian truy cập, bảo vệ tuyệt đối dữ liệu hạ tầng.'
        }
    ];

    return React.createElement('div', { className: 'enterprise-homepage', style: { position: 'relative', minHeight: '100vh', color: '#e6f1ff', fontFamily: "'Inter', 'Noto Sans', 'Segoe UI', sans-serif" } },
        React.createElement('div', { className: 'noise-overlay', 'aria-hidden': 'true' }),

        // === ANIMATED BACKGROUND ===
        React.createElement('div', { className: 'landing-bg' },
            React.createElement('div', { className: 'aurora-container', 'aria-hidden': 'true' },
                React.createElement('div', { className: 'aurora-band aurora-band-1' }),
                React.createElement('div', { className: 'aurora-band aurora-band-2' }),
                React.createElement('div', { className: 'aurora-band aurora-band-3' })
            ),
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
                React.createElement('img', { src: '/assets/img/aitogy.png', alt: 'Aitogy' }),
                React.createElement('div', null,
                    'Aitogy ',
                    React.createElement('span', null, 'Mạng Base')
                )
            ),
            React.createElement('div', { className: 'landing-nav-actions' },
                React.createElement('a', { href: '/network', className: 'landing-nav-link' }, 'Mạng lưới'),
                React.createElement('a', { href: '/blog', className: 'landing-nav-link' }, 'Bài viết'),
                React.createElement('a', { href: '/api/docs', className: 'landing-nav-link' }, 'Tài liệu API'),
                React.createElement('a', { href: '/coordinates', className: 'landing-nav-link' }, 'Công cụ'),
                React.createElement('button', {
                    className: 'btn-login-nav',
                    onClick: () => {
                        if (isLoggedIn) {
                            if (typeof window.switchView === 'function') window.switchView('dashboard');
                        } else {
                            setLoginOpen(true);
                        }
                    }
                },
                    React.createElement('i', { className: `bi ${isLoggedIn ? 'bi-speedometer2' : 'bi-person-circle'} me-2` }),
                    isLoggedIn ? 'Bảng điều khiển' : 'Đăng nhập'
                )
            )
        ),

        // === VIETNAM MAP (one floating map: hero -> network slot) ===
        React.createElement(VietnamSVG),

        // === HERO SECTION ===
        React.createElement('section', { className: 'hero-section' },
            React.createElement(HeroThreeScene),
            React.createElement('div', { className: 'hero-premium-hud', 'aria-hidden': 'true' },
                React.createElement('span', null, 'RTK FIX'),
                React.createElement('span', null, 'NTRIP LIVE'),
                React.createElement('span', null, 'GNSS 4 CONSTELLATIONS')
            ),
            React.createElement('div', { className: 'container position-relative', style: { zIndex: 2 } },
                React.createElement('div', { className: 'hero-badge' },
                    React.createElement('i', { className: 'bi bi-satellite' }),
                    ' Hạ tầng GNSS thế hệ mới'
                ),
                React.createElement('h1', { className: 'hero-title', style: { position: 'relative' } },
                    React.createElement('span', {
                        className: 'glitch-hud-title',
                        'data-text': 'Hệ thống Base'
                    }, 'Hệ thống Base'),
                    React.createElement('div', { className: 'glitch-hud-scanline' }),
                    React.createElement('br'),
                    React.createElement('span', { className: 'hero-title-gradient' }, 'Aitogy')
                ),
                React.createElement('p', { className: 'hero-subtitle' },
                    'Hệ thống quản lý và giám sát mạng lưới trạm định vị vệ tinh Base trên toàn lãnh thổ Việt Nam. ',
                    'Cung cấp hạ tầng RTK/NTRIP chuyên nghiệp cho khảo sát, đo đạc và tự động hóa.'
                ),
                React.createElement(SignalWaveform),
                React.createElement(TypingHeadline),
                React.createElement('div', { className: 'hero-actions' },
                    React.createElement('button', {
                        className: 'btn-enterprise',
                        onClick: () => {
                            if (isLoggedIn) {
                                if (typeof window.switchView === 'function') window.switchView('dashboard');
                            } else {
                                setLoginOpen(true);
                            }
                        }
                    },
                        isLoggedIn ? 'Vào bảng điều khiển ' : 'Truy cập hệ thống ',
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
                'Mạng lưới Base Việt Nam'
            ),
            React.createElement('p', { style: { color: '#6a8aaa', textAlign: 'center', marginBottom: '48px' } },
                `${stationTotal} trạm toàn mạng - ${visibleStationTotal} trạm có tọa độ bản đồ, băng thông lớn và kết nối ổn định toàn quốc`
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
                                    className: isActive ? 'station-heartbeat' : '',
                                    style: { width: '10px', height: '10px', borderRadius: '50%', background: dotColor, boxShadow: `0 0 8px ${dotColor}`, flexShrink: 0, color: dotColor }
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
                                        React.createElement('div', { style: { color: '#4a6a8a', fontSize: '0.65rem' } }, 'Vệ tinh')
                                    ),
                                    React.createElement('div', { style: { textAlign: 'center' } },
                                        React.createElement('div', { style: { color: '#00f2ff', fontWeight: 700 } }, lat > 0 ? `${lat}ms` : '—'),
                                        React.createElement('div', { style: { color: '#4a6a8a', fontSize: '0.65rem' } }, 'Độ trễ')
                                    )
                                )
                            );
                        }),
                        devices.length > 15 && React.createElement('div', {
                            style: { textAlign: 'center', padding: '12px', color: '#4a6a8a', fontSize: '0.8rem' }
                        }, `... và ${devices.length - 15} trạm có tọa độ khác`)
                    )
                ),
                React.createElement('div', { className: 'col-lg-7', style: { position: 'relative', height: '450px' } },
                    React.createElement('img', {
                        className: 'network-mobile-map',
                        src: '/assets/img/vietnam-map.svg',
                        alt: 'Bản đồ mạng lưới Base Việt Nam',
                        width: 634,
                        height: 875,
                        loading: 'lazy',
                        decoding: 'async'
                    }),
                    React.createElement('div', { className: 'network-map-slot' }),
                    React.createElement(NetworkMapOverlays),
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

        // === WHY CHOOSE AITOGY ===
        React.createElement(WhyChooseSection),

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),

        // === HOW IT WORKS ===
        React.createElement('section', { className: 'hiw-section container' },
            React.createElement('h2', { className: 'section-title' }, 'Quy trình vận hành'),
            React.createElement('p', { className: 'section-sub' }, 'Từ vệ tinh đến rover - chỉ trong vài mili giây'),
            React.createElement('div', { className: 'hiw-timeline' },
                React.createElement('div', { className: 'hiw-step' },
                    React.createElement('div', { className: 'hiw-step-content' },
                        React.createElement('div', { className: 'hiw-step-icon' }, React.createElement('i', { className: 'bi bi-satellite' })),
                        React.createElement('div', { className: 'hiw-step-title' }, 'Thu tín hiệu vệ tinh'),
                        React.createElement('div', { className: 'hiw-step-desc' }, 'Trạm Base thu tín hiệu từ 4 hệ thống vệ tinh: GPS, GLONASS, Galileo, BeiDou với anten đa tần số chuyên dụng.')
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
                        React.createElement('div', { className: 'hiw-step-title' }, 'Định vị centimet'),
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
                            React.createElement('div', null, React.createElement('span', { className: 'pulse-online' }), 'Lưu lượng mạng toàn hệ thống'),
                            React.createElement('div', { className: 'text-muted' }, 'Trực tiếp: RTCM 3.2 MSM7')
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
                        React.createElement('div', { className: 'livedata-header' }, 'Độ ổn định Caster'),
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

                // === TECHNOLOGY SUPPORT ===
        React.createElement('section', { className: 'support-tech-section container' },
            React.createElement('div', { className: 'support-tech-head text-center' },
                React.createElement('div', { className: 'support-tech-kicker' }, 'Công nghệ & giao thức hỗ trợ'),
                React.createElement('h2', { className: 'support-tech-title' }, 'Chuỗi xử lý GNSS tối ưu cho vận hành thực tế'),
                React.createElement('p', { className: 'support-tech-sub' }, 'Từ thu tín hiệu vệ tinh đến hiệu chỉnh RTCM và phân phối NTRIP, toàn bộ hệ thống được tinh chỉnh cho độ trễ thấp và tính sẵn sàng cao.')
            ),
            React.createElement('div', { className: 'support-tech-line-wrap', 'aria-hidden': 'true' },
                React.createElement('div', { className: 'support-tech-line' }),
                React.createElement('div', { className: 'support-tech-scan' })
            ),
            React.createElement('div', { className: 'support-tech-marquee' },
                React.createElement('div', { className: 'support-tech-track' },
                    [
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
                    ].concat([
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
                    ]).map((t, i) =>
                        React.createElement('div', { key: i, className: 'support-tech-chip' },
                            React.createElement('i', { className: `bi ${t.icon}` }),
                            React.createElement('span', null, t.text)
                        )
                    )
                )
            ),
                React.createElement('div', { className: 'row g-4 mt-2' },
                    React.createElement('div', { className: 'col-lg-4' },
                        React.createElement('article', { className: 'support-tech-panel' },
                        React.createElement('h4', null, 'Chuỗi dữ liệu'),
                        React.createElement('p', null, 'Dữ liệu trạm Base được lọc, chuẩn hóa sang RTCM và phân phối song song tới nhiều mountpoint.'),
                        React.createElement('ul', { className: 'support-tech-list' },
                            React.createElement('li', null, 'Bộ phân tích RTCM MSM thời gian thực'),
                            React.createElement('li', null, 'Mountpoint dự phòng khi caster quá tải'),
                            React.createElement('li', null, 'Ưu tiên định tuyến mạng ít dao động')
                        )
                    )
                ),
                React.createElement('div', { className: 'col-lg-4' },
                    React.createElement('article', { className: 'support-tech-panel' },
                        React.createElement('h4', null, 'Khả năng mở rộng'),
                        React.createElement('p', null, 'Kiến trúc mở rộng ngang theo khu vực để giảm nghẽn cổ chai trong các đợt vận hành cao điểm.'),
                        React.createElement('ul', { className: 'support-tech-list' },
                            React.createElement('li', null, 'Mở rộng ngang theo cụm tỉnh'),
                            React.createElement('li', null, 'Cân bằng phiên rover đồng thời'),
                            React.createElement('li', null, 'Giám sát SNR và mất gói liên tục')
                        )
                    )
                ),
                React.createElement('div', { className: 'col-lg-4' },
                    React.createElement('article', { className: 'support-tech-panel' },
                        React.createElement('h4', null, 'Độ tin cậy vận hành'),
                        React.createElement('p', null, 'Mỗi trạm được kiểm tra sức khỏe theo chu kỳ ngắn để cảnh báo sớm trước khi rover mất FIX.'),
                        React.createElement('ul', { className: 'support-tech-list' },
                            React.createElement('li', null, 'Mục tiêu uptime 99.9%'),
                            React.createElement('li', null, 'Cảnh báo đa kênh cho đội vận hành'),
                            React.createElement('li', null, 'Nhật ký kiểm tra cho điều tra sự cố')
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
                React.createElement('h2', { className: 'cta-title' }, 'Sẵn sàng nâng cấp hạ tầng trắc địa?'),
                React.createElement('p', { className: 'cta-desc' }, 'Liên hệ đội ngũ Aitogy để được tư vấn kiến trúc, hướng dẫn triển khai và hỗ trợ vận hành thực tế.'),
                React.createElement('div', { className: 'cta-actions d-flex gap-3 justify-content-center flex-wrap' },
                    React.createElement('button', { className: 'btn-enterprise', onClick: openStationMap }, 'Bắt đầu ngay'),
                    React.createElement('button', { className: 'btn-outline-cyan', onClick: openTechnicalConsult }, 'Tư vấn kỹ thuật')
                ),
                React.createElement('div', { className: 'cta-contact-links' },
                    React.createElement('a', { href: 'tel:+84912844919', className: 'cta-contact-link' },
                        React.createElement('i', { className: 'bi bi-telephone-fill' }),
                        React.createElement('span', null, '+84 912 844 919')
                    ),
                    React.createElement('a', { href: 'mailto:aitogyaitogy@gmail.com', className: 'cta-contact-link' },
                        React.createElement('i', { className: 'bi bi-envelope-fill' }),
                        React.createElement('span', null, 'aitogyaitogy@gmail.com')
                    ),
                    React.createElement('a', {
                        href: 'https://www.facebook.com/profile.php?id=100062949955358',
                        target: '_blank',
                        rel: 'noopener noreferrer',
                        className: 'cta-contact-link'
                    },
                        React.createElement('img', { src: '/assets/img/Facebook_icon.svg', alt: '', className: 'cta-contact-icon', 'aria-hidden': 'true' }),
                        React.createElement('span', null, 'Facebook')
                    ),
                    React.createElement('a', {
                        href: 'https://www.google.com/maps/search/?api=1&query=S%E1%BB%91%2016B%20Ng%C3%B5%20187%20ph%E1%BB%91%20Mai%20D%E1%BB%8Bch%2C%20ph%C6%B0%E1%BB%9Dng%20Mai%20D%E1%BB%8Bch%2C%20qu%E1%BA%ADn%20C%E1%BA%A7u%20Gi%E1%BA%A5y%2C%20H%C3%A0%20N%E1%BB%99i',
                        target: '_blank',
                        rel: 'noopener noreferrer',
                        className: 'cta-contact-link cta-contact-address'
                    },
                        React.createElement('i', { className: 'bi bi-geo-alt-fill' }),
                        React.createElement('span', null, 'Số 16B Ngõ 187 phố Mai Dịch, Phường Phú Diễn, Hà Nội')
                    )
                )
            )
        ),

        // === DIVIDER ===
        React.createElement('div', { className: 'section-divider' }),

        // === STATION DIRECTORY (SEO) ===
        devices.length > 0 && React.createElement('section', { className: 'station-directory-section container mb-5' },
            React.createElement('div', { className: 'text-center mb-4' },
                React.createElement('h2', { className: 'station-directory-title' }, 'Danh mục trạm Base'),
                React.createElement('p', { className: 'station-directory-sub' }, `${visibleStationTotal} trạm có tọa độ trên toàn Việt Nam`)
            ),
            React.createElement('div', { className: 'station-directory-grid row g-2', style: { maxHeight: '320px', overflowY: 'auto' } },
                devices.map((d, idx) => {
                    const st = d.status || 'offline';
                    const isActive = activeStatuses.includes(st);
                    const dotColor = isActive ? '#51cf66' : '#dc3545';
                    return React.createElement('div', { key: d.serial || idx, className: 'col-lg-3 col-md-4 col-6' },
                        React.createElement('a', { href: `/station/${encodeURIComponent(toStationSlug(d.name || d.serial))}`, className: 'station-dir-item' },
                            React.createElement('span', { className: 'station-dir-dot', style: { background: dotColor, boxShadow: `0 0 6px ${dotColor}` } }),
                            React.createElement('span', { className: 'station-dir-name' }, d.name || d.serial)
                        )
                    );
                })
            )
        ),

        // === FOOTER ===
        React.createElement('footer', { className: 'landing-footer container' },
            React.createElement('div', { className: 'footer-surface row g-4' },
                React.createElement('div', { className: 'col-lg-5' },
                    React.createElement('div', { className: 'fw-bold mb-2 footer-brand' }, 'AITOGY .,JSC'),
                    React.createElement('p', { className: 'footer-copy mb-0' }, 'Hạ tầng Base quy mô quốc gia cho khảo sát, xây dựng và giám sát với độ chính xác cao, vận hành ổn định.')
                ),
                React.createElement('div', { className: 'col-lg-3 col-6' },
                    React.createElement('div', { className: 'footer-heading' }, 'Nền tảng'),
                    React.createElement('a', { href: '/network-status', className: 'landing-footer-link d-block mb-2' }, 'Trạng thái mạng'),
                    React.createElement('a', { href: '/blog', className: 'landing-footer-link d-block mb-2' }, 'Kiến thức'),
                    React.createElement('a', { href: '/api/docs', className: 'landing-footer-link d-block' }, 'Tài liệu API')
                ),
                React.createElement('div', { className: 'col-lg-4 col-6' },
                    React.createElement('div', { className: 'footer-heading' }, 'Liên hệ'),
                    React.createElement('a', { href: 'tel:+84912844919', className: 'landing-footer-link d-block mb-2' }, '+84 912 844 919'),
                    React.createElement('a', { href: 'mailto:aitogyaitogy@gmail.com', className: 'landing-footer-link d-block mb-2' }, 'aitogyaitogy@gmail.com'),
                    React.createElement('a', {
                        href: 'https://www.facebook.com/profile.php?id=100062949955358',
                        target: '_blank',
                        rel: 'noopener noreferrer',
                        className: 'landing-footer-link d-flex align-items-center gap-2 mb-2'
                    },
                        React.createElement('img', { src: '/assets/img/Facebook_icon.svg', alt: '', className: 'cta-contact-icon', 'aria-hidden': 'true' }),
                        React.createElement('span', null, 'Facebook')
                    ),
                    React.createElement('a', {
                        href: 'https://www.google.com/maps/search/?api=1&query=S%E1%BB%91%2016B%20Ng%C3%B5%20187%20ph%E1%BB%91%20Mai%20D%E1%BB%8Bch%2C%20ph%C6%B0%E1%BB%9Dng%20Mai%20D%E1%BB%8Bch%2C%20qu%E1%BA%ADn%20C%E1%BA%A7u%20Gi%E1%BA%A5y%2C%20H%C3%A0%20N%E1%BB%99i',
                        target: '_blank',
                        rel: 'noopener noreferrer',
                        className: 'landing-footer-link d-block mb-2'
                    }, 'Số 16B Ngõ 187 phố Mai Dịch, Phường Phú Diễn, Hà Nội'),
                    React.createElement('span', { className: 'footer-copy small' }, '© 2025 Aitogy Network. Bảo lưu mọi quyền.')
                )
            )
        ),
// === LOGIN PANEL ===
        React.createElement(LoginPanel, {
            isOpen: loginOpen,
            onClose: () => setLoginOpen(false)
        }),

        // === SCROLL PROGRESS BAR ===
        React.createElement(ScrollProgressBar),

        // === SCROLL INDICATOR ===
        React.createElement('div', { className: 'scroll-indicator' },
            'Scroll',
            React.createElement('div', { className: 'scroll-indicator-line' })
        )
    );
};

window.HomePageEnterprise = HomePageEnterprise;
