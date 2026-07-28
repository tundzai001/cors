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

    const profiles = {
        high: { particleCount: 95, threePixelRatio: 1.8, frameInterval: 0, effectIntensity: 1 },
        balanced: { particleCount: 48, threePixelRatio: 1.15, frameInterval: 24, effectIntensity: 0.72 },
        lite: { particleCount: 18, threePixelRatio: 0.78, frameInterval: 50, effectIntensity: 0.48 }
    };
    const tierRank = { lite: 0, balanced: 1, high: 2 };
    const maxTier = tier;
    const settings = {
        tier,
        reducedMotion,
        webgl,
        coarsePointer,
        fps: 60,
        ...profiles[tier]
    };

    function applyTier(nextTier, reason = 'runtime') {
        if (!profiles[nextTier] || tierRank[nextTier] > tierRank[maxTier]) return;
        const changed = settings.tier !== nextTier;
        Object.assign(settings, profiles[nextTier], { tier: nextTier });
        document.documentElement.dataset.quality = nextTier;
        document.documentElement.style.setProperty('--effect-intensity', String(settings.effectIntensity));
        if (changed) {
            window.dispatchEvent(new CustomEvent('cors:qualitychange', {
                detail: { tier: nextTier, reason, fps: settings.fps }
            }));
        }
    }

    applyTier(tier, 'hardware');
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

    settings.rafThrottle = rafThrottle;
    settings.applyTier = applyTier;
    window.CorsPerformance = settings;

    // Calibrate against real rendered FPS. Quality changes density, pixel ratio
    // and update cadence; it never removes an effect from the composition.
    let sampleStart = 0;
    let sampleFrames = 0;
    let lastChangeAt = 0;
    let strongWindows = 0;
    const sample = (now) => {
        if (document.visibilityState === 'hidden') {
            sampleStart = now;
            sampleFrames = 0;
            requestAnimationFrame(sample);
            return;
        }
        if (!sampleStart) sampleStart = now;
        sampleFrames += 1;
        const elapsed = now - sampleStart;
        if (elapsed >= 4000) {
            const fps = Math.round((sampleFrames * 1000) / elapsed);
            settings.fps = fps;
            const canChange = now - lastChangeAt >= 12000;
            if (canChange && fps < 38 && settings.tier !== 'lite') {
                applyTier(settings.tier === 'high' ? 'balanced' : 'lite', 'low-fps');
                lastChangeAt = now;
                strongWindows = 0;
            } else if (fps >= 56) {
                strongWindows += 1;
                if (canChange && strongWindows >= 3 && tierRank[settings.tier] < tierRank[maxTier]) {
                    applyTier(settings.tier === 'lite' ? 'balanced' : 'high', 'stable-fps');
                    lastChangeAt = now;
                    strongWindows = 0;
                }
            } else {
                strongWindows = 0;
            }
            sampleStart = now;
            sampleFrames = 0;
        }
        requestAnimationFrame(sample);
    };
    window.addEventListener('load', () => requestAnimationFrame(sample), { once: true });
})();
