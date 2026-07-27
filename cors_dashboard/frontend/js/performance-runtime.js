(function () {
    'use strict';

    function supportsWebGL() {
        try {
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl2', { failIfMajorPerformanceCaveat: true }) ||
                canvas.getContext('webgl', { failIfMajorPerformanceCaveat: true });
            if (!gl) return false;
            const lose = gl.getExtension('WEBGL_lose_context');
            if (lose) lose.loseContext();
            return true;
        } catch (_) {
            return false;
        }
    }

    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false;
    const coarsePointer = window.matchMedia?.('(pointer: coarse)').matches || false;
    const memory = Number(navigator.deviceMemory || 4);
    const cores = Number(navigator.hardwareConcurrency || 4);
    const narrow = Math.min(window.innerWidth, window.screen?.width || window.innerWidth) < 768;
    const webgl = supportsWebGL();
    let tier = 'balanced';
    if (reducedMotion || !webgl || memory <= 2 || cores <= 2) tier = 'lite';
    else if (!narrow && !coarsePointer && memory >= 8 && cores >= 8) tier = 'high';

    const settings = {
        tier,
        reducedMotion,
        webgl,
        coarsePointer,
        particleCount: tier === 'high' ? 95 : tier === 'balanced' ? 38 : 0,
        threePixelRatio: tier === 'high' ? 1.8 : tier === 'balanced' ? 1.15 : 1,
        frameInterval: tier === 'high' ? 0 : tier === 'balanced' ? 32 : 80
    };
    document.documentElement.dataset.quality = tier;
    document.documentElement.classList.toggle('reduced-motion', reducedMotion);

    function rafThrottle(callback) {
        let frame = 0;
        let lastArgs;
        return function throttled(...args) {
            lastArgs = args;
            if (frame) return;
            frame = requestAnimationFrame(() => {
                frame = 0;
                callback.apply(this, lastArgs);
            });
        };
    }

    window.CorsPerformance = Object.freeze({ ...settings, rafThrottle });
})();
