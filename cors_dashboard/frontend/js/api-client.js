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
        if (typeof window.switchView === 'function') {
            window.switchView('homepage');
        }
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
        if (isPublicRoute()) return;
        goHomeSoft();
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

    function authHeaders(extraHeaders = {}) {
        const headers = { 'Content-Type': 'application/json', ...extraHeaders };
        const token = getToken();
        if (token) headers.Authorization = `Bearer ${token}`;
        return headers;
    }

    async function apiFetch(url, options = {}, retries = 3) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

        try {
            const response = await fetch(url, {
                ...options,
                headers: authHeaders(options.headers),
                signal: controller.signal
            });
            clearTimeout(timeoutId);

            if (response.status === 401) {
                if (!isPublicRoute() && !isPublicApiUrl(url)) {
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
            if (isAuthWebSocketClose(event) && !isPublicRoute()) {
                forceLogout('websocket_auth_failed');
            }
        });

        return ws;
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
        createAuthenticatedWebSocket
    };

    window.apiFetch = apiFetch;
})();
