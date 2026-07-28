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
    const webgl = supportsWebGL();
    const storedTier = sessionStorage.getItem('cors:quality-mode');
    const validStoredTier = storedTier && ['high', 'balanced', 'lite'].includes(storedTier) ? storedTier : null;
    let tier = validStoredTier || ((reducedMotion || !webgl) ? 'lite' : 'high');

    const profiles = {
        high: { particleCount: 95, threePixelRatio: 1.8, frameInterval: 0, effectIntensity: 1 },
        balanced: { particleCount: 48, threePixelRatio: 1.15, frameInterval: 24, effectIntensity: 0.72 },
        lite: { particleCount: 18, threePixelRatio: 0.78, frameInterval: 50, effectIntensity: 0.48 }
    };
    const tierRank = { lite: 0, balanced: 1, high: 2 };
    let maxTier = tier;
    const settings = {
        tier,
        reducedMotion,
        webgl,
        fps: 60,
        ...profiles[tier]
    };

    function applyTier(nextTier, reason = 'runtime') {
        if (!profiles[nextTier] || tierRank[nextTier] > tierRank[maxTier]) return;
        const changed = settings.tier !== nextTier;
        Object.assign(settings, profiles[nextTier], { tier: nextTier });
        document.documentElement.dataset.quality = nextTier;
        document.documentElement.style.setProperty('--effect-intensity', String(settings.effectIntensity));
        if (reason === 'low-fps' || reason === 'manual') {
            maxTier = nextTier;
            try {
                sessionStorage.setItem('cors:quality-mode', nextTier);
            } catch (_) {}
        }
        if (changed) {
            window.dispatchEvent(new CustomEvent('cors:qualitychange', {
                detail: { tier: nextTier, reason, fps: settings.fps }
            }));
        }
    }

    applyTier(tier, 'hardware');
    document.documentElement.classList.toggle('reduced-motion', reducedMotion);

    const lagBannerState = {
        element: null,
        lastShownAt: 0
    };

    function showLagBanner(fps, nextTier, reason = 'low-fps') {
        if (document.hidden) return;
        const now = Date.now();
        if (now - lagBannerState.lastShownAt < 20000) return;
        lagBannerState.lastShownAt = now;

        const existing = document.getElementById('cors-lag-banner');
        if (existing) existing.remove();

        const banner = document.createElement('div');
        banner.id = 'cors-lag-banner';
        banner.style.position = 'fixed';
        banner.style.left = '50%';
        banner.style.bottom = 'calc(16px + env(safe-area-inset-bottom, 0px))';
        banner.style.transform = 'translateX(-50%)';
        banner.style.zIndex = '99999';
        banner.style.width = 'min(94vw, 560px)';
        banner.style.padding = '14px 16px';
        banner.style.borderRadius = '18px';
        banner.style.background = 'rgba(6, 12, 22, 0.92)';
        banner.style.border = '1px solid rgba(56, 189, 248, 0.28)';
        banner.style.boxShadow = '0 20px 55px rgba(0, 0, 0, 0.45)';
        banner.style.color = '#e2f3ff';
        banner.style.backdropFilter = 'blur(14px)';
        banner.innerHTML = `
            <div style="display:flex; gap:12px; align-items:flex-start;">
                <div style="width:42px; height:42px; border-radius:14px; display:grid; place-items:center; background:rgba(14,165,233,0.15); color:#7dd3fc; flex:0 0 auto;">
                    <i class="bi bi-speedometer2"></i>
                </div>
                <div style="flex:1 1 auto; min-width:0;">
                    <div style="font-weight:800; font-size:0.98rem; margin-bottom:4px;">Thiết bị đang hơi lag</div>
                    <div style="font-size:0.9rem; color:#b5c7d8; line-height:1.45;">
                        FPS trung bình ${fps}. Hệ thống đã tự hạ chất lượng sang ${nextTier.toUpperCase()} để giữ mượt hơn.
                        Bạn có thể tải lại trang để áp dụng sạch hơn.
                    </div>
                    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:12px;">
                        <button type="button" data-action="reload" style="min-height:36px; padding:8px 12px; border:0; border-radius:999px; background:#38bdf8; color:#00111f; font-weight:800;">Tải lại trang</button>
                        <button type="button" data-action="dismiss" style="min-height:36px; padding:8px 12px; border:1px solid rgba(148,163,184,0.35); border-radius:999px; background:transparent; color:#e2f3ff; font-weight:700;">Đóng</button>
                    </div>
                </div>
            </div>
        `;

        const dismiss = () => {
            banner.remove();
            if (lagBannerState.element === banner) lagBannerState.element = null;
        };
        banner.addEventListener('click', (event) => {
            const action = event.target?.closest?.('[data-action]')?.dataset?.action;
            if (!action) return;
            if (action === 'reload') {
                sessionStorage.setItem('cors:quality-mode', nextTier);
                window.location.reload();
                return;
            }
            if (action === 'dismiss') dismiss();
        });

        document.body.appendChild(banner);
        lagBannerState.element = banner;
    }

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
            if (canChange && fps < 38) {
                const nextTier = settings.tier === 'high' ? 'balanced' : 'lite';
                applyTier(nextTier, 'low-fps');
                showLagBanner(fps, nextTier, 'low-fps');
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
