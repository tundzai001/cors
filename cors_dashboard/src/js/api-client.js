(() => {
    'use strict';

    const ACCESS_TOKEN_KEY = 'access_token';
    const DEFAULT_TIMEOUT_MS = 30000;
    const PUBLIC_ROUTES = ['/', '/station/', '/network-status', '/coverage', '/blog', '/api/docs'];
    let sessionInvalidated = false;

    function isPublicRoute(pathname = window.location.pathname) {
        return PUBLIC_ROUTES.some(route => route === '/' ? pathname === '/' : pathname.startsWith(route));
    }

    function isPublicApiUrl(url) {
        try {
            const pathname = new URL(url, window.location.origin).pathname;
            return pathname.startsWith('/api/public/');
        } catch (_) {
            return false;
        }
    }

    function goHomeSoft() {
        try {
            if (window.location.pathname !== '/') {
                window.history.replaceState({}, '', '/');
            }
        } catch (_) {}
        if (typeof window.switchView === 'function' && window.homepageRoot) {
            window.switchView('homepage');
            return true;
        }
        return false;
    }

    function getToken() {
        return localStorage.getItem(ACCESS_TOKEN_KEY) || sessionStorage.getItem(ACCESS_TOKEN_KEY);
    }

    function hasToken() {
        return !!getToken();
    }

    function setToken(token, remember = false) {
        if (!token) return;
        const primaryStore = remember ? localStorage : sessionStorage;
        const secondaryStore = remember ? sessionStorage : localStorage;
        primaryStore.setItem(ACCESS_TOKEN_KEY, token);
        secondaryStore.removeItem(ACCESS_TOKEN_KEY);
    }

    function clearToken() {
        localStorage.removeItem(ACCESS_TOKEN_KEY);
        sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    }

    function forceLogout(reason = 'session_expired') {
        if (sessionInvalidated) return;
        sessionInvalidated = true;
        clearToken();
        window.dispatchEvent(new CustomEvent('cors:session-expired', { detail: { reason } }));
        if (goHomeSoft()) return;

        // During bootstrap there is no mounted homepage root yet. Reload once
        // after clearing the stale token so initializeCorsApp enters guest mode.
        if (isPublicRoute()) {
            window.location.reload();
        } else {
            window.location.replace('/');
        }
    }

    function isSessionInvalidated() {
        return sessionInvalidated;
    }

    function isAuthWebSocketClose(event) {
        if (!event) return false;
        if (event.code === 1008) return true;
        const reason = String(event.reason || '').toLowerCase();
        return reason.includes('auth') || reason.includes('token') || reason.includes('unauthorized');
    }

    async function requireAuthenticatedPage() {
        if (!hasToken()) {
            forceLogout();
            return null;
        }

        try {
            const response = await apiFetch(window.ApiRoutes.auth.me, {}, 0);
            if (!response.ok) {
                forceLogout();
                return null;
            }
            return await response.json();
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                console.warn('Authentication check failed:', error);
            }
            forceLogout();
            return null;
        }
    }

    let cachedLocalIP = null;
    let localIPPromise = null;

    function authHeaders(extraHeaders = {}) {
        const headers = { 'Content-Type': 'application/json', ...extraHeaders };
        const token = getToken();
        if (token) headers.Authorization = `Bearer ${token}`;
        try {
            const devId = getDeviceId();
            if (devId) headers['X-Device-Id'] = devId;
            if (cachedLocalIP) headers['X-Local-IP'] = cachedLocalIP;
            const devName = getDeviceName();
            if (devName) headers['X-Device-Name'] = encodeURIComponent(devName);
        } catch (_) {}
        return headers;
    }

    async function apiFetch(url, options = {}, retries = 3) {
        if (!browserDetectionComplete) await browserDetection;
        const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchOptions } = options;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

        try {
            const response = await fetch(url, {
                ...fetchOptions,
                headers: authHeaders(fetchOptions.headers),
                signal: controller.signal
            });
            clearTimeout(timeoutId);

            if (response.status === 401) {
                if (!isPublicApiUrl(url)) {
                    forceLogout('unauthorized');
                }
                throw new Error('Unauthorized');
            }

            if (response.status >= 500 && retries > 0) {
                await new Promise(resolve => setTimeout(resolve, 1000));
                return apiFetch(url, options, retries - 1);
            }

            return response;
        } catch (error) {
            clearTimeout(timeoutId);
            if (error.name === 'AbortError' && retries > 0) {
                return apiFetch(url, options, retries - 1);
            }
            throw error;
        }
    }

    function createAuthenticatedWebSocket(path = '/ws/updates', hostOverride = null) {
        const token = getToken();
        if (!token) return null;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = hostOverride || window.location.host;
        const ws = new WebSocket(`${protocol}//${host}${path}`);

        ws.addEventListener('open', () => {
            ws.send(JSON.stringify({ type: 'auth', token }));
        }, { once: true });

        ws.addEventListener('close', (event) => {
            if (isAuthWebSocketClose(event)) {
                forceLogout('websocket_auth_failed');
            }
        });

        return ws;
    }

    function getDeviceId() {
        try {
            let did = localStorage.getItem('cors_device_id');
            if (!did) {
                if (window.crypto && window.crypto.getRandomValues) {
                    const arr = new Uint8Array(12);
                    window.crypto.getRandomValues(arr);
                    did = 'dev_' + Array.from(arr, b => b.toString(16).padStart(2, '0')).join('');
                } else {
                    did = 'dev_' + Math.random().toString(36).substring(2, 12) + Date.now().toString(36);
                }
                localStorage.setItem('cors_device_id', did);
            }
            return did;
        } catch (_) {
            return 'dev_unknown';
        }
    }

    let isBrave = false;
    let browserDetectionComplete = false;
    const browserDetection = (async () => {
        try {
            isBrave = navigator.userAgentData?.brands?.some(item => item.brand === 'Brave') ||
                await Promise.race([
                    navigator.brave?.isBrave?.() || Promise.resolve(false),
                    new Promise(resolve => setTimeout(() => resolve(false), 500))
                ]) === true;
        } catch (_) { /* Browser privacy settings can hide the signal. */ }
        finally { browserDetectionComplete = true; }
    })();

    function getDeviceName() {
        try {
            const ua = navigator.userAgent || '';
            const uaLower = ua.toLowerCase();
            let os = 'Máy tính';
            if (uaLower.includes('iphone')) os = 'iPhone · iOS';
            else if (uaLower.includes('ipad')) os = 'iPad · iPadOS';
            else if (uaLower.includes('ipod')) os = 'iPod · iOS';
            else if (uaLower.includes('android')) os = 'Android';
            else if (uaLower.includes('windows nt 10.0') || uaLower.includes('windows nt 11.0')) os = 'Windows 10/11';
            else if (uaLower.includes('windows')) os = 'Windows';
            else if (uaLower.includes('macintosh') && navigator.maxTouchPoints > 1) os = 'iPad · iPadOS';
            else if (uaLower.includes('macintosh') || uaLower.includes('mac os x')) os = 'macOS';
            else if (uaLower.includes('linux')) os = 'Linux';

            let browser = 'Trình duyệt';
            if (isBrave || /brave\//i.test(ua)) browser = 'Brave';
            else if (/edg\/|edgios\/|edga\//i.test(ua)) browser = 'Edge';
            else if (/opr\/|opera\/|opios\//i.test(ua)) browser = 'Opera';
            else if (/chrome\/|crios\//i.test(ua)) browser = 'Chrome';
            else if (/firefox\/|fxios\//i.test(ua)) browser = 'Firefox';
            else if (uaLower.includes('safari/')) browser = 'Safari';

            return `${os} · ${browser}`;
        } catch (_) {
            return 'Thiết bị Web';
        }
    }

    function detectLocalIP(timeoutMs = 1200) {
        if (cachedLocalIP) return Promise.resolve(cachedLocalIP);
        if (localIPPromise) return localIPPromise;

        localIPPromise = new Promise((resolve) => {
            let finished = false;
            const finish = (ip) => {
                if (!finished) {
                    finished = true;
                    if (ip) cachedLocalIP = ip;
                    resolve(ip || null);
                }
            };

            const timer = setTimeout(() => finish(null), timeoutMs);

            try {
                const RTCPC = window.RTCPeerConnection || window.webkitRTCPeerConnection || window.mozRTCPeerConnection;
                if (!RTCPC) {
                    clearTimeout(timer);
                    return finish(null);
                }
                const pc = new RTCPC({ iceServers: [] });
                pc.createDataChannel('cors_probe');
                pc.createOffer()
                    .then(offer => pc.setLocalDescription(offer))
                    .catch(() => finish(null));

                pc.onicecandidate = (event) => {
                    if (!event || !event.candidate || !event.candidate.candidate) return;
                    const candidate = event.candidate.candidate;
                    const match = candidate.match(/([0-9]{1,3}(?:\.[0-9]{1,3}){3})/);
                    if (match) {
                        const ip = match[1];
                        if (ip !== '0.0.0.0' && !ip.startsWith('127.')) {
                            clearTimeout(timer);
                            try { pc.close(); } catch (_) {}
                            finish(ip);
                        }
                    }
                };
            } catch (_) {
                finish(null);
            }
        });

        return localIPPromise;
    }

    // Trigger local IP probe in background
    setTimeout(() => { try { detectLocalIP(1500); } catch (_) {} }, 100);

    async function getClientMeta() {
        if (!browserDetectionComplete) await browserDetection;
        let localIp = cachedLocalIP;
        if (!localIp) {
            try {
                localIp = await Promise.race([
                    detectLocalIP(500),
                    new Promise(res => setTimeout(() => res(null), 500))
                ]);
            } catch (_) {}
        }
        return {
            device_id: getDeviceId(),
            device_name: getDeviceName(),
            local_ip: localIp || null
        };
    }

    window.ApiClient = {
        getToken,
        hasToken,
        setToken,
        clearToken,
        forceLogout,
        isSessionInvalidated,
        isAuthWebSocketClose,
        requireAuthenticatedPage,
        authHeaders,
        apiFetch,
        createAuthenticatedWebSocket,
        getDeviceId,
        getDeviceName,
        detectLocalIP,
        getClientMeta
    };

    window.apiFetch = apiFetch;

    // Only a visible, authenticated page reports presence. Expired signals age
    // out on the server; closing a tab never revokes another tab's login.
    let heartbeatPending = false;
    async function reportSessionPresence() {
        if (heartbeatPending || sessionInvalidated || !hasToken() || document.hidden) return;
        heartbeatPending = true;
        try {
            const response = await apiFetch('/api/auth/heartbeat', { method: 'POST', timeoutMs: 8000 }, 0);
            if (response.status === 403) forceLogout('device_blocked');
        } catch (_) {
            // No successful signal means no online status; retry next interval.
        } finally {
            heartbeatPending = false;
        }
    }
    setInterval(reportSessionPresence, 15000);
    document.addEventListener('visibilitychange', reportSessionPresence);
    window.addEventListener('online', reportSessionPresence);
    window.addEventListener('pageshow', reportSessionPresence);
    reportSessionPresence();
})();
