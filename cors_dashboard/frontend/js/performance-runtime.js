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

    const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false;
    const webgl = supportsWebGL();
    // Every visit starts at the complete Max composition. Runtime monitoring
    // may offer a lower tier, but it never changes quality without consent.
    let tier = 'high';

    const profiles = {
        high: { particleCount: 95, threePixelRatio: 1.8, frameInterval: 0, effectIntensity: 1 },
        balanced: { particleCount: 68, threePixelRatio: 1.3, frameInterval: 16, effectIntensity: 0.82 },
        lite: { particleCount: 42, threePixelRatio: 1, frameInterval: 24, effectIntensity: 0.68 }
    };
    const tierRank = { lite: 0, balanced: 1, high: 2 };
    const maxTier = 'high';
    const settings = {
        tier,
        reducedMotion: false,
        prefersReducedMotion,
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
        if (changed) {
            window.dispatchEvent(new CustomEvent('cors:qualitychange', {
                detail: { tier: nextTier, reason, fps: settings.fps }
            }));
        }
    }

    applyTier(tier, 'initial-high');
    document.documentElement.classList.remove('reduced-motion');

    const lagBannerState = {
        element: null,
        lastShownAt: 0,
        dismissed: false
    };

    function showLagBanner(fps, nextTier, reason = 'low-fps') {
        if (document.hidden || lagBannerState.dismissed || lagBannerState.element) return;
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
                        FPS trung bình ${fps}. Bạn có muốn giảm mật độ hiệu ứng xuống ${nextTier.toUpperCase()} để thao tác mượt hơn không?
                        Bố cục và toàn bộ loại hiệu ứng vẫn được giữ nguyên.
                    </div>
                    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:12px;">
                        <button type="button" data-action="reduce" style="min-height:36px; padding:8px 12px; border:0; border-radius:999px; background:#38bdf8; color:#00111f; font-weight:800;">Giảm nhẹ hiệu ứng</button>
                        <button type="button" data-action="keep" style="min-height:36px; padding:8px 12px; border:1px solid rgba(148,163,184,0.35); border-radius:999px; background:transparent; color:#e2f3ff; font-weight:700;">Giữ chất lượng tối đa</button>
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
            if (action === 'reduce') {
                applyTier(nextTier, 'manual');
                dismiss();
                return;
            }
            if (action === 'keep') {
                lagBannerState.dismissed = true;
                dismiss();
            }
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
    const sampleWindowMs = 3500;
    const warmupMs = 4000;
    const lowFpsThreshold = 42;
    let monitorReadyAt = 0;
    let sampleStart = 0;
    let sampleFrames = 0;
    let lastChangeAt = 0;
    let lowWindows = 0;
    const resetSample = (now) => {
        sampleStart = now;
        sampleFrames = 0;
        lowWindows = 0;
    };
    const sample = (now) => {
        const landingActive = document.body?.classList.contains('landing-view-active');
        if (document.visibilityState === 'hidden' || !landingActive || now < monitorReadyAt) {
            resetSample(now);
            requestAnimationFrame(sample);
            return;
        }
        if (!sampleStart) sampleStart = now;
        sampleFrames += 1;
        const elapsed = now - sampleStart;
        if (elapsed >= sampleWindowMs) {
            const fps = Math.round((sampleFrames * 1000) / elapsed);
            settings.fps = fps;
            const canChange = now - lastChangeAt >= 12000;
            if (fps < lowFpsThreshold) lowWindows += 1;
            else lowWindows = 0;

            // Require two consecutive slow windows. Initial loading, route
            // changes and one isolated long task must not trigger the banner.
            if (canChange && lowWindows >= 2 && settings.tier !== 'lite') {
                const nextTier = settings.tier === 'high' ? 'balanced' : 'lite';
                showLagBanner(fps, nextTier, 'low-fps');
                lastChangeAt = now;
                lowWindows = 0;
            }
            sampleStart = now;
            sampleFrames = 0;
        }
        requestAnimationFrame(sample);
    };
    window.addEventListener('load', () => {
        monitorReadyAt = (window.performance?.now?.() || 0) + warmupMs;
        requestAnimationFrame(sample);
    }, { once: true });
})();
