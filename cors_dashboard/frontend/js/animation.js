/**
 * animation.js - Cinematic Product Showcase
 * A scroll-scrubbed product film built from still photos.
 */

(function () {
    'use strict';

    function ensureLayer(pin, className) {
        let el = pin.querySelector('.' + className);
        if (!el) {
            el = document.createElement('div');
            el.className = className;
            el.setAttribute('aria-hidden', 'true');
            pin.appendChild(el);
        }
        return el;
    }

    function initProductShowcase() {
        if (typeof gsap === 'undefined' || typeof ScrollTrigger === 'undefined') {
            console.warn('[ProductShowcase] GSAP or ScrollTrigger not loaded');
            return;
        }

        gsap.registerPlugin(ScrollTrigger);

        const pin = document.querySelector('.product-showcase-pin');
        if (!pin || pin.dataset.showcaseReady === 'true') return;
        pin.dataset.showcaseReady = 'true';

        const stage = pin.querySelector('.device-stage');
        const layerSurround = pin.querySelector('.device-layer-surround');
        const layerTop = pin.querySelector('.device-layer-top');
        const layerUnder = pin.querySelector('.device-layer-under');
        const layerInside = pin.querySelector('.device-layer-inside');
        const glowRing = pin.querySelector('.device-glow-ring');
        const glowRing2 = pin.querySelector('.device-glow-ring-2');
        const scanLine = pin.querySelector('.device-scan-line');
        const modelName = pin.querySelector('.device-model-name');
        const scrollHint = pin.querySelector('.showcase-scroll-hint');

        const textSurround = pin.querySelector('.scene-text-surround');
        const textTop = pin.querySelector('.scene-text-top');
        const textUnder = pin.querySelector('.scene-text-under');
        const textInside = pin.querySelector('.scene-text-inside');
        const dots = Array.from(pin.querySelectorAll('.scene-counter-dot'));

        const shutter = ensureLayer(pin, 'showcase-shutter');
        const focusFlare = ensureLayer(pin, 'showcase-focus-flare');

        const shots = [
            { layer: layerSurround, panel: textSurround, side: 'left', dot: 0 },
            { layer: layerTop, panel: textTop, side: 'right', dot: 1 },
            { layer: layerUnder, panel: textUnder, side: 'left', dot: 2 },
            { layer: layerInside, panel: textInside, side: 'right', dot: 3 }
        ];

        if (shots.some((shot) => !shot.layer)) {
            console.warn('[ProductShowcase] Missing device layers');
            return;
        }

        const layers = shots.map((shot) => shot.layer);
        const images = layers.map((layer) => layer.querySelector('img')).filter(Boolean);
        const panels = shots.map((shot) => shot.panel).filter(Boolean);

        function setActiveDot(index) {
            dots.forEach((dot, i) => dot.classList.toggle('active', i === index));
        }

        gsap.set(layers, {
            autoAlpha: 0,
            scale: 0.86,
            x: 0,
            y: 0,
            rotateX: 0,
            rotateY: 0,
            rotation: 0,
            filter: 'blur(0px) brightness(1)',
            transformPerspective: 1200,
            transformOrigin: 'center center'
        });
        gsap.set(images, { x: 0, y: 0, scale: 1, rotation: 0, transformOrigin: 'center center' });
        gsap.set(panels, { autoAlpha: 0, x: 0, y: 24 });
        gsap.set([glowRing, glowRing2], { autoAlpha: 0, scale: 0.72, rotation: 0 });
        gsap.set([shutter, focusFlare], { autoAlpha: 0 });
        gsap.set(shutter, { xPercent: -130 });
        gsap.set(scanLine, { autoAlpha: 0, y: -280, scaleX: 0.55 });
        if (stage) gsap.set(stage, { y: 0, scale: 1, rotateX: 0 });
        if (modelName) gsap.set(modelName, { autoAlpha: 0, y: 20 });
        if (scrollHint) gsap.set(scrollHint, { autoAlpha: 1 });

        const masterTL = gsap.timeline({
            defaults: { ease: 'power2.out' },
            scrollTrigger: {
                trigger: '.product-showcase',
                start: 'top top',
                end: '+=620%',
                pin: '.product-showcase-pin',
                scrub: 0.72,
                anticipatePin: 1,
                invalidateOnRefresh: true,
                onUpdate: (self) => {
                    const index = Math.min(3, Math.floor(self.progress * 4));
                    setActiveDot(index);
                }
            }
        });

        function panelIn(panel, at, side) {
            if (!panel) return;
            const x = side === 'right' ? 46 : -46;
            masterTL.fromTo(panel,
                { autoAlpha: 0, x, y: 22 },
                { autoAlpha: 1, x: 0, y: 0, duration: 0.06, ease: 'power3.out' },
                at
            );
            masterTL.to(panel,
                { autoAlpha: 0, x: -x * 0.35, y: -18, duration: 0.045, ease: 'power2.in' },
                at + 0.185
            );
        }

        function sweep(at) {
            masterTL.fromTo(shutter,
                { autoAlpha: 0, xPercent: -130 },
                { autoAlpha: 1, xPercent: 0, duration: 0.025, ease: 'power2.in' },
                at
            );
            masterTL.to(shutter,
                { xPercent: 130, autoAlpha: 0, duration: 0.055, ease: 'power3.out' },
                at + 0.025
            );
            masterTL.fromTo(focusFlare,
                { autoAlpha: 0, scale: 0.85 },
                { autoAlpha: 0.8, scale: 1.05, duration: 0.03, ease: 'power2.out' },
                at + 0.018
            );
            masterTL.to(focusFlare, { autoAlpha: 0, scale: 1.2, duration: 0.055 }, at + 0.055);
        }

        function showShot(shot, at, opts) {
            const img = shot.layer.querySelector('img');
            masterTL.fromTo(shot.layer,
                {
                    autoAlpha: 0,
                    x: opts.fromX || 0,
                    y: opts.fromY || 26,
                    scale: opts.fromScale || 0.82,
                    rotateX: opts.fromRotateX || 0,
                    rotateY: opts.fromRotateY || 0,
                    rotation: opts.fromRotation || 0,
                    filter: 'blur(12px) brightness(0.72)'
                },
                {
                    autoAlpha: 1,
                    x: opts.x || 0,
                    y: opts.y || 0,
                    scale: opts.scale || 1,
                    rotateX: opts.rotateX || 0,
                    rotateY: opts.rotateY || 0,
                    rotation: opts.rotation || 0,
                    filter: 'blur(0px) brightness(1.04)',
                    duration: 0.08,
                    ease: 'power3.out'
                },
                at
            );
            if (img) {
                masterTL.fromTo(img,
                    {
                        x: opts.imgFromX || 0,
                        y: opts.imgFromY || 0,
                        scale: opts.imgFromScale || 1,
                        rotation: opts.imgFromRotation || 0
                    },
                    {
                        x: opts.imgToX || 0,
                        y: opts.imgToY || 0,
                        scale: opts.imgToScale || 1.08,
                        rotation: opts.imgToRotation || 0,
                        duration: 0.24,
                        ease: 'none'
                    },
                    at
                );
            }
            panelIn(shot.panel, at + 0.045, shot.side);
            masterTL.to(shot.layer,
                {
                    autoAlpha: 0,
                    x: opts.exitX || 0,
                    y: opts.exitY || -24,
                    scale: opts.exitScale || 1.08,
                    rotateX: opts.exitRotateX || 0,
                    rotateY: opts.exitRotateY || 0,
                    rotation: opts.exitRotation || 0,
                    filter: 'blur(10px) brightness(0.76)',
                    duration: 0.06,
                    ease: 'power2.in'
                },
                at + 0.225
            );
        }

        masterTL
            .to(glowRing, { autoAlpha: 0.72, scale: 1, duration: 0.08 }, 0)
            .to(glowRing2, { autoAlpha: 0.32, scale: 1, duration: 0.1 }, 0)
            .to(glowRing2, { rotation: 260, duration: 1, ease: 'none' }, 0)
            .to(modelName, { autoAlpha: 1, y: 0, duration: 0.08 }, 0.02)
            .to(scrollHint, { autoAlpha: 0, y: 14, duration: 0.05 }, 0.075);

        showShot(shots[0], 0.00, {
            fromScale: 0.58,
            fromY: 52,
            fromRotateY: -12,
            imgFromScale: 1.03,
            imgToScale: 1.16,
            imgToY: -10,
            exitScale: 1.14,
            exitY: -36,
            exitRotateY: 16
        });

        sweep(0.235);
        masterTL.to(glowRing, {
            scale: 1.08,
            borderColor: 'rgba(255, 214, 10, 0.22)',
            boxShadow: '0 0 72px rgba(255, 214, 10, 0.12), inset 0 0 60px rgba(0, 242, 255, 0.05)',
            duration: 0.055
        }, 0.245);
        showShot(shots[1], 0.255, {
            fromScale: 0.72,
            fromY: 36,
            fromRotateX: 18,
            fromRotation: -16,
            imgFromScale: 1.02,
            imgToScale: 1.2,
            imgToRotation: 10,
            exitScale: 0.92,
            exitY: -26,
            exitRotateX: -18
        });

        sweep(0.485);
        masterTL.to(glowRing, {
            scale: 1,
            borderColor: 'rgba(204, 93, 232, 0.2)',
            boxShadow: '0 0 70px rgba(204, 93, 232, 0.12), inset 0 0 60px rgba(204, 93, 232, 0.06)',
            duration: 0.055
        }, 0.5);
        showShot(shots[2], 0.505, {
            fromScale: 0.78,
            fromY: 34,
            fromRotateX: -12,
            imgFromScale: 1,
            imgToScale: 1.14,
            imgToY: -24,
            exitScale: 1.16,
            exitY: -28
        });

        sweep(0.72);
        masterTL.to(scanLine, { autoAlpha: 1, y: -190, scaleX: 0.7, duration: 0.035 }, 0.715);
        masterTL.to(scanLine, { y: 330, scaleX: 1.12, duration: 0.125, ease: 'none' }, 0.735);
        masterTL.to(scanLine, { autoAlpha: 0, duration: 0.025 }, 0.85);
        masterTL.to(glowRing, {
            scale: 1.13,
            borderColor: 'rgba(0, 242, 255, 0.3)',
            boxShadow: '0 0 96px rgba(0, 242, 255, 0.2), inset 0 0 84px rgba(0, 242, 255, 0.1)',
            duration: 0.075
        }, 0.76);
        showShot(shots[3], 0.76, {
            fromScale: 0.68,
            fromY: 38,
            imgFromScale: 1.02,
            imgToScale: 1.28,
            imgToY: -18,
            exitScale: 1.04,
            exitY: 0
        });

        const header = document.querySelector('.product-showcase-header');
        if (header) {
            gsap.from(header.querySelectorAll('.product-showcase-kicker, .product-showcase-title, .product-showcase-sub'), {
                scrollTrigger: {
                    trigger: '.product-showcase',
                    start: 'top 82%',
                    toggleActions: 'play none none reverse'
                },
                y: 44,
                opacity: 0,
                stagger: 0.1,
                duration: 0.75,
                ease: 'power3.out'
            });
        }

        pin.querySelectorAll('.showcase-particle').forEach((particle, index) => {
            gsap.to(particle, {
                y: -90 - (index * 24),
                x: index % 2 === 0 ? 34 : -34,
                opacity: 0.12,
                ease: 'none',
                scrollTrigger: {
                    trigger: '.product-showcase',
                    start: 'top bottom',
                    end: 'bottom top',
                    scrub: 1
                }
            });
        });

        const refresh = () => ScrollTrigger.refresh();
        images.forEach((image) => {
            if (!image.complete) image.addEventListener('load', refresh, { once: true });
        });

        console.log('[ProductShowcase] Cinematic animation initialized');
    }

    function boot() {
        if (document.querySelector('.product-showcase-pin')) {
            initProductShowcase();
            return;
        }

        const observer = new MutationObserver((mutations, obs) => {
            if (document.querySelector('.product-showcase-pin')) {
                obs.disconnect();
                window.setTimeout(initProductShowcase, 250);
            }
        });

        observer.observe(document.body, { childList: true, subtree: true });
        window.setTimeout(() => {
            observer.disconnect();
            initProductShowcase();
        }, 5000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        window.setTimeout(boot, 400);
    }
})();
