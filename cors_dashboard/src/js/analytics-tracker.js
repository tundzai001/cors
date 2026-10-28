(() => {
    'use strict';
    let visitor, viewId, currentPath, pending = false, recorded = false;
    try {
        visitor = localStorage.getItem('cors_analytics_visitor');
        if (!/^[0-9a-f-]{36}$/i.test(visitor || '')) {
            visitor = crypto.randomUUID();
            localStorage.setItem('cors_analytics_visitor', visitor);
        }
    } catch (_) { return; }

    function pagePath() {
        return location.pathname.replace(/\.html$/, '').replace(/^\/index$/, '/') +
            (location.pathname === '/' && new URLSearchParams(location.search).get('view') === 'map' ? '?view=map' : '');
    }
    async function signal() {
        if (document.hidden || pending || !window.ApiClient) return;
        const path = pagePath();
        if (path !== currentPath) {
            currentPath = path;
            viewId = crypto.randomUUID();
            recorded = false;
        }
        pending = true;
        const sentView = viewId;
        try {
            const meta = await window.ApiClient.getClientMeta();
            const response = await fetch('/api/public/analytics/page', {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({view_id: viewId, visitor_id: visitor, path,
                    device_name: meta.device_name, touch: navigator.maxTouchPoints > 1, heartbeat: recorded}),
                signal: AbortSignal.timeout(8000)
            });
            if (response.ok && sentView === viewId) recorded = true;
        } catch (_) { /* Analytics never interrupts page use. Retry on the next signal. */ }
        finally { pending = false; if (pagePath() !== path) signal(); }
    }
    for (const method of ['pushState', 'replaceState']) {
        const original = history[method];
        history[method] = function (...args) {
            const result = original.apply(this, args);
            signal();
            return result;
        };
    }
    window.addEventListener('popstate', signal);
    window.addEventListener('pageshow', signal);
    window.addEventListener('online', signal);
    document.addEventListener('visibilitychange', signal);
    setInterval(signal, 30000);
    signal();
})();
