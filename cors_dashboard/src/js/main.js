// main.js 

'use strict';

// === GLOBAL STATE  ===
let globalState = {
    devices: [],
    publicNetworkSummary: null,
    selectedDeviceSerial: null,
    currentUser: null,
    searchTerm: '',
    currentView: 'dashboard',
    stateVersion: 0
};
window.globalState = globalState;
const MAIN_PUBLIC_ROUTES = ['/', '/station/', '/network-status', '/coverage', '/blog', '/api/docs'];

function escapeHtmlMain(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
}

function isMainPublicRoute(pathname = window.location.pathname) {
    return MAIN_PUBLIC_ROUTES.some(route => route === '/' ? pathname === '/' : pathname.startsWith(route));
}

function softGoHome() {
    try {
        if (window.location.pathname !== '/') {
            window.history.replaceState({}, '', '/');
        }
    } catch (_) {}
    if (typeof window.switchView === 'function') {
        window.switchView('homepage');
    }
}
// === REACT ROOTS ===
let deviceListRoot = null;
let detailsPanelRoot = null;
let mapRoot = null;

function dismissAppPreloader() {
    if (typeof window.hideAppPreloader === 'function') {
        window.hideAppPreloader();
        return;
    }
    const loader = document.getElementById('app-preloader');
    if (loader) loader.remove();
}

// === WEBSOCKET ===
let ws = null;
let wsReconnectAttempts = 0;
let wsReconnectTimerId = null;
const MAX_RECONNECT_ATTEMPTS = 10;

// === DASHBOARD STATE & LOGIC  ===
let currentNmeaData = { gga: null, gsa: null, satellites: {} };
window.getCurrentNmeaData = () => currentNmeaData;

// === RENDER THROTTLING LOGIC ===
let lastRenderTime = 0;
const RENDER_INTERVAL = 220;

let lastMapRenderTime = 0;
const MAP_RENDER_INTERVAL = 2000;

let dashboardRafId = null;
let mapRafId = null;
let dashboardTimerId = null;
let mapTimerId = null;
let detailsRenderTimerId = null;
let lastDetailsRenderAt = 0;
const DETAILS_RENDER_MIN_INTERVAL = 220;


let deviceListNeedsUpdate = false;
let detailsPanelNeedsUpdate = false;
let mapNeedsUpdate = false;

function renderReactComponent(root, Component, props = {}, fallbackMessage = 'Component not available') {
    if (!root) return;

    if (typeof Component !== 'function') {
        console.error(`[Main] Missing React component: ${fallbackMessage}`);
        root.render(React.createElement('div', { className: 'p-4 text-center text-muted' }, fallbackMessage));
        return;
    }

    root.render(React.createElement(Component, props));
}

async function waitForGlobalComponent(componentName, timeoutMs = 5000, intervalMs = 50) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        const component = window[componentName];
        if (typeof component === 'function') {
            return component;
        }
        await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
    return null;
}

function toStationSlug(value) {
    return String(value || '')
        .normalize('NFKD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-zA-Z0-9._-]+/g, '.')
        .replace(/\.{2,}/g, '.')
        .replace(/^\.+|\.+$/g, '')
        .toLowerCase();
}

function resolveDeviceFromStationPath(segment) {
    const token = decodeURIComponent(String(segment || '')).trim();
    if (!token) return null;
    const tokenSlug = toStationSlug(token);
    return globalState.devices.find((d) => {
        const serialMatch = String(d.serial || '') === token;
        const nameSlugMatch = toStationSlug(d.name || '') === tokenSlug;
        return serialMatch || nameSlugMatch;
    }) || null;
}

function buildPublicStationPath(device) {
    const fallback = String(device?.serial || '').trim();
    const slug = toStationSlug(device?.name || fallback);
    return `/station/${encodeURIComponent(slug || fallback)}`;
}

// Batch status updates to prevent UI stalls under high websocket throughput.
const STATUS_FLUSH_INTERVAL = 140;
const CONTROL_STATUS_VALUES = new Set([
    'locked', 'rebooting', 'rebooting_for_reset',
    'unprovisioned', 'awaiting_license'
]);
const LIVE_STATUS_FIELDS = [
    'status', 'timestamp', 'offline_since', 'agent_timestamp', 'server_received_at',
    'status_revision', 'status_source', 'bps', 'ntrip_connected',
    'ntrip_status', 'ntrip_stats', 'transport_status'
];
let pendingStatusUpdates = new Map();
let lastStatusMetaBySerial = new Map();
let statusFlushTimerId = null;
let visitorPulseTimerId = null;
let lastVisitorCount = null;

function flushPendingStatusUpdates() {
    statusFlushTimerId = null;
    if (pendingStatusUpdates.size === 0) return;
    const payload = Array.from(pendingStatusUpdates.values());
    pendingStatusUpdates.clear();
    updateGlobalDevices(payload);
}

function enqueueStatusUpdate(payload) {
    const list = Array.isArray(payload) ? payload : [payload];
    list.forEach((item) => {
        if (!item || !item.serial) return;
        const revision = Number(item.status_revision || 0);
        const receivedAt = Number(item.server_received_at || item.timestamp || 0);
        const previous = lastStatusMetaBySerial.get(item.serial);
        if (previous && revision > 0) {
            // Revisions can restart at 1 after a backend restart/worker change.
            // A newer server receipt remains newer even with a lower revision.
            if (receivedAt < previous.receivedAt) return;
            if (receivedAt === previous.receivedAt && revision <= previous.revision) return;
        }
        if (previous && revision === 0 && receivedAt <= previous.receivedAt) {
            // Some administrative endpoints send a full DB snapshot after changing
            // config/ownership. Preserve its static fields, but never let that
            // older snapshot roll live transport state backwards.
            const existing = globalState.devices.find((device) => device.serial === item.serial);
            if (existing && !CONTROL_STATUS_VALUES.has(String(item.status || '').toLowerCase())) {
                item = { ...item };
                LIVE_STATUS_FIELDS.forEach((field) => {
                    if (Object.prototype.hasOwnProperty.call(existing, field)) item[field] = existing[field];
                });
            }
        }
        if (revision > 0) lastStatusMetaBySerial.set(item.serial, { revision, receivedAt });
        pendingStatusUpdates.set(item.serial, item);
    });

    if (statusFlushTimerId !== null) return;
    statusFlushTimerId = setTimeout(flushPendingStatusUpdates, STATUS_FLUSH_INTERVAL);
}

function requestDetailsPanelUpdate(priority = 'high') {
    detailsPanelNeedsUpdate = true;
    const now = Date.now();
    const waitMs = Math.max(0, DETAILS_RENDER_MIN_INTERVAL - (now - lastDetailsRenderAt));
    if (waitMs === 0) {
        lastDetailsRenderAt = now;
        scheduleRender(priority);
        return;
    }
    if (detailsRenderTimerId) clearTimeout(detailsRenderTimerId);
    detailsRenderTimerId = setTimeout(() => {
        lastDetailsRenderAt = Date.now();
        scheduleRender(priority);
    }, waitMs);
}

function queueDashboardRender() {
    if (dashboardRafId !== null) return;
    dashboardRafId = requestAnimationFrame(() => {
        dashboardRafId = null;
        if (globalState.currentView !== 'dashboard') return;
        if (!deviceListNeedsUpdate && !detailsPanelNeedsUpdate) return;
        renderDashboard();
        lastRenderTime = Date.now();
    });
}

function queueMapRender() {
    if (mapRafId !== null) return;
    mapRafId = requestAnimationFrame(() => {
        mapRafId = null;
        if (!mapNeedsUpdate) return;
        renderMap();
        lastMapRenderTime = Date.now();
    });
}

function scheduleRender(priority = 'high') {
    if (priority === 'high') {
        deviceListNeedsUpdate = true;
        detailsPanelNeedsUpdate = true;

        if (globalState.currentView === 'dashboard') {
            const now = Date.now();
            const waitMs = Math.max(0, RENDER_INTERVAL - (now - lastRenderTime));
            if (waitMs === 0) {
                queueDashboardRender();
            } else {
                if (dashboardTimerId) clearTimeout(dashboardTimerId);
                dashboardTimerId = setTimeout(queueDashboardRender, waitMs);
            }
        }
    }

    // Map render (chậm hơn)
    if (globalState.currentView === 'map') {
        mapNeedsUpdate = true;

        const now = Date.now();
        const baseWait = Math.max(0, MAP_RENDER_INTERVAL - (now - lastMapRenderTime));
        const lowPriorityDelay = priority === 'low' ? 120 : 0;
        const waitMs = baseWait + lowPriorityDelay;

        if (waitMs === 0) {
            queueMapRender();
        } else {
            if (mapTimerId) clearTimeout(mapTimerId);
            mapTimerId = setTimeout(queueMapRender, waitMs);
        }
    }
}

// === CACHED FILTERED DEVICES ===
let cachedFilteredDevices = [];
let lastSearchTerm = null;
let lastStateVersion = -1;

function getFilteredDevices() {
    const currentVersion = globalState.stateVersion;
    if (globalState.searchTerm !== lastSearchTerm || currentVersion !== lastStateVersion) {
        const term = globalState.searchTerm.toLowerCase();

        cachedFilteredDevices = globalState.devices
            .filter(d =>
                (d.name && d.name.toLowerCase().includes(term)) ||
                (d.serial && d.serial.toLowerCase().includes(term))
            );

        // Keep the station list stable while status/license/bps packets arrive.
        // Status is shown by badges; it should not reorder cards on every heartbeat.
        cachedFilteredDevices.sort((a, b) => {
            const nameA = String(a.name || a.serial || '').trim();
            const nameB = String(b.name || b.serial || '').trim();
            return nameA.localeCompare(nameB, 'vi', { sensitivity: 'base', numeric: true });
        });

        lastSearchTerm = globalState.searchTerm;
        lastStateVersion = currentVersion;
    }
    return cachedFilteredDevices;
}

class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null, errorInfo: null };
    }
    static getDerivedStateFromError(error) {
        return { hasError: true, error };
    }
    componentDidCatch(error, errorInfo) {
        console.error("[ErrorBoundary] React render error caught:", error, errorInfo);
        this.setState({ errorInfo });
    }
    render() {
        if (this.state.hasError) {
            return React.createElement('div', {
                className: 'card bg-danger text-white h-100 p-4 border border-warning shadow-lg',
                style: { minHeight: '300px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }
            },
                React.createElement('div', { className: 'd-flex align-items-center gap-3 mb-3' },
                    React.createElement('i', { className: 'bi bi-exclamation-triangle-fill fs-1 text-warning' }),
                    React.createElement('h3', { className: 'mb-0 fw-bold' }, 'Lỗi Hiển Thị Chi Tiết Trạm (Details Panel Error)')
                ),
                React.createElement('p', { className: 'lead' }, 'Đã xảy ra lỗi React khi hiển thị bảng chi tiết trạm. Chi tiết lỗi:'),
                React.createElement('pre', {
                    style: {
                        whiteSpace: 'pre-wrap',
                        background: 'rgba(0,0,0,0.85)',
                        padding: '20px',
                        borderRadius: '8px',
                        color: '#ffc107',
                        fontSize: '0.9rem',
                        fontFamily: 'monospace',
                        overflowX: 'auto',
                        border: '1px solid rgba(255,255,255,0.1)'
                    }
                },
                    this.state.error ? this.state.error.toString() : 'Lỗi không xác định',
                    '\n\nStack Trace:\n',
                    this.state.error ? this.state.error.stack : ''
                )
            );
        }
        return this.props.children;
    }
}
window.ErrorBoundary = ErrorBoundary;

// === SPLIT RENDER FUNCTIONS ===
function renderDashboard() {
    //console.log("[DEBUG] renderDashboard() triggered! deviceListNeedsUpdate:", deviceListNeedsUpdate, "detailsPanelNeedsUpdate:", detailsPanelNeedsUpdate, "selectedDeviceSerial:", globalState.selectedDeviceSerial);
    if (globalState.currentView !== 'dashboard') return;
    if (!deviceListNeedsUpdate && !detailsPanelNeedsUpdate) {
        //console.log("[DEBUG] renderDashboard() returned early because no update flags were set.");
        return;
    }

    const filteredDevices = getFilteredDevices();

    // Render Device List
    if (deviceListNeedsUpdate && deviceListRoot) {
        if (!window.DeviceList) {
            console.warn("[Main] window.DeviceList is undefined. Deferring render.");
            return;
        }
        ////console.log("[DEBUG] Rendering DeviceList sidebar...");
        deviceListRoot.render(
            React.createElement(window.DeviceList, {
                devices: filteredDevices,
                selectedDeviceSerial: globalState.selectedDeviceSerial,
                onSelect: handleSelectDevice,
                onDelete: handleDeleteDevice,
                searchTerm: globalState.searchTerm,
                onSearch: handleSearchChange,
                currentUser: globalState.currentUser
            })
        );
        deviceListNeedsUpdate = false;
    }

    // Render Details Panel
    if (detailsPanelNeedsUpdate && detailsPanelRoot) {
        ////console.log("[DEBUG] detailsPanelNeedsUpdate block entered! detailsPanelRoot exists.");
        if (!window.DetailsPanel) {
            console.warn("[Main] window.DetailsPanel is undefined. Deferring render.");
            return;
        }
        const selectedDevice = globalState.devices.find(d => String(d.serial) === String(globalState.selectedDeviceSerial));
        ////console.log("[DEBUG] selectedDevice resolved for DetailsPanel:", selectedDevice);

        if (!selectedDevice) {
            detailsPanelRoot.render(null);
            detailsPanelNeedsUpdate = false;
            updateStaticUI();
            return;
        }
        
        ////console.log("[DEBUG] Triggering detailsPanelRoot.render()...");
        detailsPanelRoot.render(
            React.createElement(ErrorBoundary, null,
                React.createElement(window.DetailsPanel, {
                    device: selectedDevice,
                    nmeaData: { ...currentNmeaData },
                    currentUser: globalState.currentUser,
                    onReset: handleResetDevice,
                    onDeployLicense: handleDeployLicense,
                    onProvision: handleProvisionDevice,
                    onConfigureChip: handleConfigureChip,
                    onStopAutoBase: handleStopAutoBase,
                    onApplyAutoProvince: handleApplyAutoProvince,
                    onConfigureService: handleConfigureService,
                    onLock: handleLockDevice,
                    onUnlock: handleUnlockDevice
                })
            )
        );
        detailsPanelNeedsUpdate = false;
    }

    updateStaticUI();
}

function renderMap() {
    if (!mapNeedsUpdate || !mapRoot) return;

    if (!window.AllStationsMap) {
        return;
    }

    if (window.publicMapInstance) {
        window.publicMapInstance.setView(VIETNAM_CENTER, VIETNAM_DEFAULT_ZOOM);
    }
    
    mapRoot.render(
        React.createElement(window.AllStationsMap, {
            devices: globalState.devices,
            currentUser: globalState.currentUser
        })
    );
    mapNeedsUpdate = false;
}

function updateStaticUI() {
    const deviceCount = document.getElementById('device-count');
    if (deviceCount) deviceCount.textContent = globalState.devices.length;

    const userInfoEl = document.getElementById('user-info');
    if (userInfoEl && globalState.currentUser) {
        userInfoEl.textContent = `${globalState.currentUser.full_name || globalState.currentUser.username} (${globalState.currentUser.role.toUpperCase()})`;
    } else if (userInfoEl) {
        userInfoEl.textContent = ''; // Clear for guest
    }
}

// === MEMOIZED HANDLERS ===
let searchDebounceTimer = null;

const handleSearchChange = (newTerm) => {
    if (searchDebounceTimer) {
        clearTimeout(searchDebounceTimer);
    }
    globalState.searchTerm = newTerm;

    searchDebounceTimer = setTimeout(() => {
        deviceListNeedsUpdate = true;
        scheduleRender('high');
    }, 200);
};

window.switchView = function (viewName) {
    const previousView = globalState.currentView;
    globalState.currentView = viewName;
    if (viewName !== 'dashboard' || previousView !== 'dashboard') {
        document.body.classList.remove('mobile-detail-open');
    }
    document.body.classList.toggle('landing-view-active', viewName === 'homepage');

    // 1. Dynamic class toggling on body to control page-level scrolling
    if (viewName === 'dashboard' || viewName === 'map') {
        document.body.classList.add('dashboard-fixed-layout');
    } else {
        document.body.classList.remove('dashboard-fixed-layout');
    }

    const navDashboard = document.getElementById('nav-dashboard');
    const navMap = document.getElementById('nav-map');
    const dashboardDiv = document.getElementById('view-dashboard');
    const mapDiv = document.getElementById('view-map');
    const stationDetailDiv = document.getElementById('view-station-detail');

    if (navDashboard) {
        navDashboard.classList.toggle('active', viewName === 'dashboard');
    }

    if (navMap) {
        navMap.classList.toggle('active', viewName === 'map');
    }

    const homepageDiv = document.getElementById('view-homepage');
    const blogDiv = document.getElementById('view-blog');
    const apiDocsDiv = document.getElementById('view-api-docs');

    const navbar = document.querySelector('.navbar');

    if (viewName === 'dashboard') {
        if (dashboardDiv) dashboardDiv.classList.remove('d-none');
        if (mapDiv) mapDiv.classList.add('d-none');
        if (stationDetailDiv) stationDetailDiv.classList.add('d-none');
        if (homepageDiv) homepageDiv.classList.add('d-none');
        if (blogDiv) blogDiv.classList.add('d-none');
        if (apiDocsDiv) apiDocsDiv.classList.add('d-none');
        if (navbar) navbar.classList.remove('d-none');

        deviceListNeedsUpdate = true;
        detailsPanelNeedsUpdate = true;
        renderDashboard();
    } else if (viewName === 'map') {
        if (dashboardDiv) dashboardDiv.classList.add('d-none');
        if (mapDiv) mapDiv.classList.remove('d-none');
        if (stationDetailDiv) stationDetailDiv.classList.add('d-none');
        if (homepageDiv) homepageDiv.classList.add('d-none');
        if (blogDiv) blogDiv.classList.add('d-none');
        if (apiDocsDiv) apiDocsDiv.classList.add('d-none');
        if (navbar) navbar.classList.remove('d-none');

        mapNeedsUpdate = true;
        queueMapRender();
        setTimeout(() => {
            if (window.mapInstance) window.mapInstance.invalidateSize();
        }, 100);
    } else if (viewName === 'station-detail') {
        if (dashboardDiv) dashboardDiv.classList.add('d-none');
        if (mapDiv) mapDiv.classList.add('d-none');
        if (stationDetailDiv) stationDetailDiv.classList.remove('d-none');
        if (homepageDiv) homepageDiv.classList.add('d-none');
        if (blogDiv) blogDiv.classList.add('d-none');
        if (apiDocsDiv) apiDocsDiv.classList.add('d-none');
        if (navbar) navbar.classList.remove('d-none');
    } else if (viewName === 'homepage') {
        if (dashboardDiv) dashboardDiv.classList.add('d-none');
        if (mapDiv) mapDiv.classList.add('d-none');
        if (stationDetailDiv) stationDetailDiv.classList.add('d-none');
        if (homepageDiv) homepageDiv.classList.remove('d-none');
        if (blogDiv) blogDiv.classList.add('d-none');
        if (apiDocsDiv) apiDocsDiv.classList.add('d-none');
        if (navbar) navbar.classList.add('d-none'); 
    } else if (viewName === 'blog') {
        if (dashboardDiv) dashboardDiv.classList.add('d-none');
        if (mapDiv) mapDiv.classList.add('d-none');
        if (stationDetailDiv) stationDetailDiv.classList.add('d-none');
        if (homepageDiv) homepageDiv.classList.add('d-none');
        if (blogDiv) blogDiv.classList.remove('d-none');
        if (apiDocsDiv) apiDocsDiv.classList.add('d-none');
        if (navbar) navbar.classList.remove('d-none');
    } else if (viewName === 'api-docs') {
        if (dashboardDiv) dashboardDiv.classList.add('d-none');
        if (mapDiv) mapDiv.classList.add('d-none');
        if (stationDetailDiv) stationDetailDiv.classList.add('d-none');
        if (homepageDiv) homepageDiv.classList.add('d-none');
        if (blogDiv) blogDiv.classList.add('d-none');
        if (apiDocsDiv) apiDocsDiv.classList.remove('d-none');
        if (navbar) navbar.classList.remove('d-none');
    }

    // 2. Dynamic Mounting and Unmounting logic for public page roots
    
    // --- Homepage ---
    if (window.homepageRoot) {
        if (viewName === 'homepage') {
            waitForGlobalComponent('HomePageEnterprise', 15000).then((homeComp) => {
                if (homeComp && window.homepageRoot && globalState.currentView === 'homepage') {
                    renderReactComponent(window.homepageRoot, homeComp, { devices: globalState.devices, networkSummary: globalState.publicNetworkSummary }, 'Homepage component is not loaded');
                }
            });
        } else {
            window.homepageRoot.render(null);
        }
    }

    // --- Blog ---
    if (window.blogRoot) {
        if (viewName === 'blog') {
            const currentPath = window.location.pathname;
            const slug = currentPath.startsWith('/blog') && currentPath.split('/').pop() !== 'blog' ? currentPath.split('/').pop() : null;
            waitForGlobalComponent('BlogSystem', 15000).then((blogComp) => {
                if (blogComp && window.blogRoot && globalState.currentView === 'blog') {
                    renderReactComponent(window.blogRoot, blogComp, { slug }, 'Blog component is not loaded');
                }
            });
        } else {
            window.blogRoot.render(null);
        }
    }

    // --- Station Detail ---
    if (window.enterpriseDetailRoot) {
        if (viewName === 'station-detail') {
            const device = globalState.devices.find(d => d.serial === globalState.selectedDeviceSerial) || globalState.devices[0];
            if (device) {
                waitForGlobalComponent('EnterpriseStationDetail', 15000).then((stationComp) => {
                    if (stationComp && window.enterpriseDetailRoot && globalState.currentView === 'station-detail') {
                        renderReactComponent(window.enterpriseDetailRoot, stationComp, { device }, 'Station detail component is not loaded');
                    }
                });
            }
        } else {
            window.enterpriseDetailRoot.render(null);
        }
    }

    // --- API Docs ---
    if (window.apiDocsRoot) {
        if (viewName === 'api-docs') {
            waitForGlobalComponent('ApiDocs', 15000).then((apiDocsComp) => {
                if (apiDocsComp && window.apiDocsRoot && globalState.currentView === 'api-docs') {
                    renderReactComponent(window.apiDocsRoot, apiDocsComp, {}, 'API docs component is not loaded');
                }
            });
        } else {
            window.apiDocsRoot.render(null);
        }
    }

    // --- Dashboard & Map Roots (to optimize resource usage when inactive) ---
    if (viewName !== 'dashboard') {
        if (typeof deviceListRoot !== 'undefined' && deviceListRoot) deviceListRoot.render(null);
        if (typeof detailsPanelRoot !== 'undefined' && detailsPanelRoot) detailsPanelRoot.render(null);
    }
    if (viewName !== 'map') {
        if (typeof mapRoot !== 'undefined' && mapRoot) mapRoot.render(null);
    }

    setTimeout(() => {
        window.dispatchEvent(new Event('resize'));
    }, 100);
};

// Toggle auth-related buttons (login / logout / back) based on token and current view
function updateAuthButtons() {
    const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
    const loginBtn = document.getElementById('login-btn');
    const logoutBtn = document.getElementById('logout-btn');
    const backBtn = document.getElementById('back-home-btn');

    if (loginBtn) {
        if (token) loginBtn.classList.add('d-none'); else loginBtn.classList.remove('d-none');
    }
    if (logoutBtn) {
        if (token) logoutBtn.classList.remove('d-none'); else logoutBtn.classList.add('d-none');
    }

    // Back button visible on map and station-detail views for public users
    const isMapView = globalState.currentView === 'map' || window.location.search.includes('view=map');
    if (backBtn) {
        if (isMapView) backBtn.classList.remove('d-none'); else backBtn.classList.add('d-none');
    }
}

// Wire auth buttons if present
document.addEventListener('click', (e) => {
    const target = e.target;
    if (!target) return;
    if (target.id === 'login-btn' || (target.closest && target.closest('#login-btn'))) {
        e.preventDefault();
        softGoHome();
    }
    if (target.id === 'back-home-btn' || (target.closest && target.closest('#back-home-btn'))) {
        e.preventDefault();
        window.switchView('homepage');
    }
});

function updateGlobalDevices(newDeviceData) {
    const deviceMap = new Map(globalState.devices.map(d => [d.serial, d]));
    const devicesToAddOrUpdate = Array.isArray(newDeviceData) ? newDeviceData : [newDeviceData];
    const hasData = (obj) => !!(obj && typeof obj === 'object' && Object.keys(obj).length > 0);
    const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);

    let hasChanges = false;
    let hasStatusChanges = false;
    let hasStructuralChanges = false;

    devicesToAddOrUpdate.forEach(newDevice => {
        if (!newDevice || !newDevice.serial) return;

        const existingDevice = deviceMap.get(newDevice.serial);

        // Giữ chip/config đã biết khi bản tin realtime chỉ là partial update (đặc biệt lúc offline).
        if (existingDevice) {
            if (hasOwn(newDevice, 'status') && !hasOwn(newDevice, 'offline_since')) {
                const nextStatus = String(newDevice.status || '').toLowerCase();
                const previousStatus = String(existingDevice.status || '').toLowerCase();
                if (nextStatus === 'offline') {
                    newDevice.offline_since = previousStatus === 'offline'
                        ? (existingDevice.offline_since ?? null)
                        : (Number(newDevice.server_received_at || newDevice.timestamp || 0) || Math.floor(Date.now() / 1000));
                } else {
                    newDevice.offline_since = null;
                }
            }

            // Chip Type
            if (!newDevice.detected_chip_type || newDevice.detected_chip_type === 'UNKNOWN') {
                if (existingDevice.detected_chip_type && existingDevice.detected_chip_type !== 'UNKNOWN') {
                    newDevice.detected_chip_type = existingDevice.detected_chip_type;
                }
            }

            // Name
            if (!newDevice.name && existingDevice.name) {
                newDevice.name = existingDevice.name;
            }

            // Configs: Fallback if key is missing OR if value is null/empty
            if ((!hasOwn(newDevice, 'base_config') || !hasData(newDevice.base_config)) && hasData(existingDevice.base_config)) {
                newDevice.base_config = existingDevice.base_config;
            }
            const unsyncedAgentConfig = newDevice.status_revision > 0 && newDevice.is_synced === false;
            if ((unsyncedAgentConfig || !hasOwn(newDevice, 'service_config') || !hasData(newDevice.service_config)) && hasData(existingDevice.service_config)) {
                // Keep the backend's desired configuration available for repair.
                // An out-of-sync agent may report only a truncated deployment.
                newDevice.service_config = existingDevice.service_config;
            } else if (hasData(existingDevice.service_config) && hasData(newDevice.service_config)) {
                newDevice.service_config = { ...existingDevice.service_config, ...newDevice.service_config };
            }

            // Provisioned status
            if (typeof newDevice.is_provisioned === 'undefined' && typeof existingDevice.is_provisioned !== 'undefined') {
                newDevice.is_provisioned = existingDevice.is_provisioned;
            }
        }

        // Reset/provisioning states must clear stale configs on UI.
        if (newDevice.status === 'rebooting_for_reset' || newDevice.status === 'unprovisioned') {
            if (!hasOwn(newDevice, 'base_config') || !newDevice.base_config || typeof newDevice.base_config !== 'object') {
                newDevice.base_config = {};
            }
            if (!hasOwn(newDevice, 'service_config') || !newDevice.service_config || typeof newDevice.service_config !== 'object') {
                newDevice.service_config = {};
            }
        }

        // Xử lý offline state
        if (newDevice.status === 'offline') {
            newDevice.bps = 0;
            newDevice.ntrip_connected = false;
            newDevice.ntrip_status = {
                server1: { connected: false, bps: 0 },
                server2: { connected: false, bps: 0 }
            };
        }
        else {
            if (newDevice.ntrip_stats) {
                const stats = newDevice.ntrip_stats && typeof newDevice.ntrip_stats === 'object' ? newDevice.ntrip_stats : {};
                const rawStatus = newDevice.ntrip_status && typeof newDevice.ntrip_status === 'object' ? newDevice.ntrip_status : {};
                const streamState = newDevice.ntrip_stream_state && typeof newDevice.ntrip_stream_state === 'object' ? newDevice.ntrip_stream_state : {};
                const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);
                const normalizedStatus = { ...rawStatus };
                let hasServerBps = false;
                let totalBps = 0;
                let anyServerConnected = false;

                [1, 2].forEach((sid) => {
                    const key = `server${sid}`;
                    const rawServer = rawStatus[key];
                    const serverStatus = rawServer && typeof rawServer === 'object' ? rawServer : {};
                    const hasStatsBps = hasOwn(stats, `${key}_bps`) && stats[`${key}_bps`] != null;
                    const hasStatusBps = hasOwn(serverStatus, 'bps') || hasOwn(rawStatus, `${key}_bps`);
                    const hasConnection = typeof rawServer === 'boolean' || typeof serverStatus.connected === 'boolean';
                    const rawBps = hasStatsBps ? stats[`${key}_bps`] : (serverStatus.bps ?? rawStatus[`${key}_bps`] ?? 0);
                    const parsedBps = Number(rawBps);
                    const bps = Number.isFinite(parsedBps) ? Math.max(0, parsedBps) : 0;

                    if (hasStatsBps || hasStatusBps || hasConnection) {
                        hasServerBps = hasServerBps || hasStatsBps || hasStatusBps;
                        const reportedConnected = typeof rawServer === 'boolean' ? rawServer : serverStatus.connected;
                        const connected = reportedConnected === true || bps > 0;
                        const normalizedServer = { ...serverStatus, connected, bps };
                        const liveStream = streamState[key];
                        if (liveStream && typeof liveStream === 'object') {
                            for (const stateKey of ['enabled', 'on_demand', 'active', 'can_push', 'sleep', 'mountpoint']) {
                                if (hasOwn(liveStream, stateKey)) normalizedServer[stateKey] = liveStream[stateKey];
                            }
                        }
                        normalizedStatus[key] = normalizedServer;
                        totalBps += bps;
                        anyServerConnected = anyServerConnected || connected;
                    }
                });

                if (Object.keys(normalizedStatus).length) newDevice.ntrip_status = normalizedStatus;
                const inputBps = Number(stats.rtcm_input_bps ?? stats.rtcm_in_bps ?? 0);
                newDevice.rtcm_input_bps = Number.isFinite(inputBps) ? Math.max(0, inputBps) : 0;
                if (hasServerBps) newDevice.bps = totalBps;
                if (typeof newDevice.ntrip_connected !== 'boolean' && (hasServerBps || anyServerConnected)) {
                    newDevice.ntrip_connected = anyServerConnected;
                }
            }
            else if (existingDevice && existingDevice.ntrip_status && existingDevice.bps > 0) {
                newDevice.ntrip_stats = existingDevice.ntrip_stats;
                newDevice.ntrip_status = existingDevice.ntrip_status;
                newDevice.bps = existingDevice.bps;
                if (typeof newDevice.ntrip_connected !== 'boolean') {
                    newDevice.ntrip_connected = existingDevice.ntrip_connected;
                }
            }
            else if (!newDevice.ntrip_status) {
                newDevice.ntrip_status = {
                    server1: { connected: false, bps: 0 },
                    server2: { connected: false, bps: 0 }
                };
                newDevice.bps = 0;
                if (typeof newDevice.ntrip_connected !== 'boolean') {
                    newDevice.ntrip_connected = false;
                }
            }
        }

        // ✅ XỬ LÝ is_synced: Mặc định là true nếu backend không gửi
        if (typeof newDevice.is_synced === 'undefined') {
            newDevice.is_synced = true;
        }

        if (existingDevice) {
            // Check if we actually need to create a new object reference.
            // BPS changes are frequent, but many other things are static.
            // For now, let's check if the new device data is actually different.
            let isDifferent = false;

            // Simple heuristic: if any top-level property of newDevice is different from existingDevice
            for (let key in newDevice) {
                if (typeof newDevice[key] === 'object' && newDevice[key] !== null) {
                    // Quick check for nested objects (ntrip_status, etc)
                    if (JSON.stringify(newDevice[key]) !== JSON.stringify(existingDevice[key])) {
                        isDifferent = true;
                        break;
                    }
                } else if (newDevice[key] !== existingDevice[key]) {
                    isDifferent = true;
                    if (key === 'status' || key === 'name') hasStatusChanges = true;
                    break;
                }
            }

            if (isDifferent) {
                const mergedDevice = {
                    ...existingDevice,
                    ...newDevice,
                    last_update_local: Date.now()
                };
                deviceMap.set(newDevice.serial, mergedDevice);
                hasChanges = true;
            }
        } else {
            // New device found
            deviceMap.set(newDevice.serial, {
                ...newDevice,
                last_update_local: Date.now()
            });
            hasChanges = true;
            hasStructuralChanges = true;
        }
    });

    if (hasChanges) {
        globalState.devices = Array.from(deviceMap.values());
        globalState.stateVersion++; // Trigger cache refresh in getFilteredDevices

        if (hasStructuralChanges || hasStatusChanges) {
            //console.log(`[STATE] Structural/Status change version: ${globalState.stateVersion}`);
            lastSearchTerm = null; // Forces re-sort
        }

        // Trigger updates
        deviceListNeedsUpdate = true;
        mapNeedsUpdate = true;

        if (globalState.currentView === 'dashboard') {
            scheduleRender('high');
        } else {
            scheduleRender('low');
        }
    }
}

function handleSelectDevice(serial) {
    if (window.matchMedia?.('(max-width: 575.98px)').matches) {
        document.body.classList.add('mobile-detail-open');
        window.scrollTo({ top: 0, behavior: 'auto' });
    }
    if (globalState.selectedDeviceSerial === serial) return;

    globalState.selectedDeviceSerial = serial;
    currentNmeaData = { gga: null, gsa: null, satellites: {} };

    detailsPanelNeedsUpdate = true;
    scheduleRender('high');
}
window.selectDashboardDevice = handleSelectDevice;

function selectDeviceFromDashboardQuery() {
    const params = new URLSearchParams(window.location.search);
    const requestedSerial = params.get('station') || params.get('serial') || params.get('device');
    if (!requestedSerial) return false;

    const normalized = requestedSerial.trim().toLowerCase();
    const device = globalState.devices.find(d =>
        String(d.serial || '').toLowerCase() === normalized ||
        String(d.name || '').toLowerCase() === normalized
    );
    if (!device) {
        console.warn('[Routing] Station query did not match a loaded device:', requestedSerial);
        return false;
    }

    globalState.selectedDeviceSerial = device.serial;
    currentNmeaData = { gga: null, gsa: null, satellites: {} };
    deviceListNeedsUpdate = true;
    detailsPanelNeedsUpdate = true;
    switchView('dashboard');
    if (window.matchMedia?.('(max-width: 575.98px)').matches) {
        document.body.classList.add('mobile-detail-open');
    }
    return true;
}

// ===============================================================================
// === EVENT HANDLERS ===
// ===============================================================================

async function handleRenameDevice(serial, newName) {
    if (!newName || !newName.trim()) {
        showError('Tên trạm không được để trống!');
        return;
    }

    try {
        const response = await apiFetch(`/api/devices/${serial}/name`, {
            method: 'PUT',
            body: JSON.stringify({ name: newName })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Đổi tên thất bại');
        }

        updateGlobalDevices({ serial: serial, name: newName });
        showSuccess(`Đã đổi tên thành công: "${newName}"`);

    } catch (e) {
        showError(`Lỗi đổi tên: ${e.message}`);
    }
}
window.handleRenameDevice = handleRenameDevice;

async function handleRenameSerial(oldSerial, newSerial) {
    try {
        const response = await apiFetch(`/api/devices/${encodeURIComponent(oldSerial)}/serial`, {
            method: 'PUT',
            body: JSON.stringify({ new_serial: newSerial })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Đổi serial thất bại');
        }

        globalState.selectedDeviceSerial = newSerial;
        showSuccess(`Đã đổi serial thành công: "${newSerial}". Đang làm mới danh sách...`);
        await loadInitialDevices();
        deviceListNeedsUpdate = true;
        detailsPanelNeedsUpdate = true;
        scheduleRender('high');

    } catch (e) {
        showError(`Lỗi đổi serial: ${e.message}`);
    }
}
window.handleRenameSerial = handleRenameSerial;

async function handleDeleteDevice(serial, name) {
    const confirmMsg = `⚠️ XÓA KHỎI DANH SÁCH\n\nBạn có chắc muốn xóa trạm '${name}' khỏi giao diện quản lý không?\n\nLưu ý: Hành động này không reset Pi.`;
    const ok = await window.CorsUI.confirmAction({
        title: 'Remove station',
        message: `Remove station ${name || serial} from the admin list? This does not reset the Pi.`,
        confirmText: 'Remove',
        tone: 'danger'
    });
    if (!ok) return;

    try {
        const response = await apiFetch(`/api/devices/${serial}`, { method: 'DELETE' });

        if (response.ok) {
            const initialLength = globalState.devices.length;
            globalState.devices = globalState.devices.filter(d => d.serial !== serial);

            if (globalState.selectedDeviceSerial === serial) {
                globalState.selectedDeviceSerial = null;
            }

            if (globalState.devices.length < initialLength) {
                lastSearchTerm = ''; // Invalidate cache
                deviceListNeedsUpdate = true;
                detailsPanelNeedsUpdate = true;
                scheduleRender('high');
                showSuccess(`Đã xóa trạm '${name}' khỏi danh sách`);
            }
        } else {
            const error = await response.json();
            throw new Error(error.detail || 'Xóa thất bại');
        }
    } catch (error) {
        showError(`Không thể xóa: ${error.message}`);
    }
}

async function handleResetDevice(serial) {
    try {
        await apiFetch(`/api/devices/${serial}/reset`, { method: 'POST' });
        showSuccess(`Đã gửi lệnh khởi động lại đến thiết bị. Giao diện sẽ tự cập nhật.`);
    } catch (error) {
        showError(`Không thể gửi lệnh khởi động lại: ${error.message}`);
        throw error;
    }
}

async function handleDeployLicense(serial, licenseKey) {
    try {
        await apiFetch(`/api/devices/${serial}/command`, {
            method: 'POST',
            body: JSON.stringify({ command: 'DEPLOY_LICENSE', payload: { license_key: licenseKey } })
        });
        showSuccess('Đã gửi license! Chờ thiết bị khởi động lại...');
    } catch (e) {
        showError(`Lỗi gửi license: ${e.message}`);
        throw e;
    }
}

async function handleProvisionDevice(serial, newName) {
    try {
        const response = await apiFetch(`/api/devices/${serial}/command`, {
            method: 'POST',
            body: JSON.stringify({ command: 'PROVISION_DEVICE', payload: { name: newName } })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Không lưu được tên trạm.');
        updateGlobalDevices({serial, name: result.name || newName});
        showSuccess('Đã gửi lệnh kích hoạt! Giao diện sẽ tự cập nhật.');
    } catch (e) {
        showError(`Lỗi kích hoạt: ${e.message}`);
        throw e;
    }
}

async function handleConfigureChip(serial, payload) {
    try {
        const response = await apiFetch(`/api/devices/${serial}/configure-chip`, {
            method: 'POST',
            body: JSON.stringify({ command: 'CONFIGURE_CHIP', payload: payload })
        });
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Cau hinh chip that bai');
        }
        const existingDev = (window.devices || []).find(d => d.serial === serial);
        const existingBase = existingDev?.base_config || {};
        updateGlobalDevices({
            serial,
            base_config: {
                ...existingBase,
                ...(payload?.params || {}),
                gnss_options: payload?.params?.gnss_options || existingBase.gnss_options,
                sensor_type: payload?.sensor_type,
                mode: payload?.mode
            },
            ...(payload?.params?.base_setup_method !== 'AUTO_CORS' ? {
                auto_base_progress: {},
                last_raw_gga: null
            } : {})
        });
        showSuccess('Đã gửi cấu hình GNSS! Giao diện sẽ tự cập nhật.');
    } catch (e) {
        showError(`Lỗi cấu hình chip: ${e.message}`);
        throw e;
    }
}

async function handleStopAutoBase(serial) {
    try {
        await apiFetch(`/api/devices/${serial}/command`, {
            method: 'POST',
            body: JSON.stringify({ command: 'STOP_AUTO_BASE', payload: {} })
        });
        showSuccess('Đã dừng quy trình Auto Base.');
    } catch (e) {
        showError(`Lỗi dừng Auto Base: ${e.message}`);
        throw e;
    }
}

async function handleApplyAutoProvince(serial, provinceCode) {
    try {
        await apiFetch(`/api/devices/${serial}/command`, {
            method: 'POST',
            body: JSON.stringify({ command: 'APPLY_VN2000_PROVINCE', payload: { province_code: provinceCode } })
        });
        showSuccess(`Đã áp dụng lại ITRF->VN2000 theo tỉnh ${provinceCode}.`);
    } catch (e) {
        showError(`Lỗi áp dụng tỉnh VN2000: ${e.message}`);
        throw e;
    }
}

async function handleConfigureService(serial, payload) {
    try {
        const response = await apiFetch(`/api/devices/${serial}/configure-service`, {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok && response.status !== 503) {
            throw new Error(result.detail || 'Lỗi không xác định');
        }
        // The endpoint saves before sending. Keep its saved intent in the form,
        // including when delivery fails, so old telemetry cannot undo the edit.
        const current = globalState.devices.find(device => device.serial === serial);
        const savedConfig = result.service_config || { ...(current?.service_config || {}), ...payload };
        const currentPolicy = current?.server_config_lock;
        const serverLock = result.server_config_lock || (currentPolicy && Object.keys(currentPolicy).length
            ? Object.fromEntries(Object.keys(currentPolicy).map(key => [key, key === "locked_servers" ? currentPolicy[key] : savedConfig[key]])) : currentPolicy);
        updateGlobalDevices({ serial, service_config: savedConfig, server_config_lock: serverLock, is_synced: false });
        if (response.status === 503) {
            showError('Đã lưu cấu hình nhưng chưa gửi được tới trạm. Bấm áp dụng lại.');
        } else {
            showSuccess('Đã lưu và gửi cấu hình dịch vụ! Thiết bị sẽ áp dụng sau giây lát.');
        }
        return { service_config: savedConfig, delivered: response.ok };
    } catch (e) {
        showError(`Lỗi cấu hình dịch vụ: ${e.message}`);
        throw e;
    }
}


async function handleLockDevice(serial) {
    const device = globalState.devices.find(item => item.serial === serial);
    if (!device) return;
    const config = device.service_config || {};
    const modal = document.createElement('dialog');
    modal.className = 'station-host-lock-dialog bg-dark text-light border border-secondary rounded-3';
    modal.setAttribute('aria-labelledby', 'host-lock-title');
    modal.innerHTML = `<form><div class="p-4"><h2 id="host-lock-title" class="h5">Khoá host / server</h2>
        <p class="text-secondary small">Bật nút gạt để khoá server: server bị khoá không gửi dữ liệu, dù cấu hình vẫn bật. Tắt nút gạt để mở khoá server đó. Người dùng không được sửa server bị khoá hoặc tắt server còn lại.</p>
        <div class="host-lock-servers"></div><p class="small text-secondary mt-3 mb-0">Khoá độc lập với nút Kích hoạt Server trong Dịch vụ.</p>
        <p class="host-lock-error text-danger mt-3 mb-0" role="alert"></p></div>
        <div class="d-flex justify-content-end gap-2 p-3 border-top border-secondary"><button type="button" class="btn btn-outline-secondary host-lock-cancel">Hủy</button><button type="submit" class="btn btn-warning">Lưu khoá host</button></div></form>`;
    const rows = modal.querySelector('.host-lock-servers');
    for (const sid of [1, 2]) {
        const label = document.createElement('label'); label.className = 'd-flex gap-3 align-items-center border border-secondary rounded p-3 mt-2';
        const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('role', 'switch'); checkbox.setAttribute('aria-label', `Khoá Server ${sid}`); checkbox.className = 'form-check-input flex-shrink-0'; checkbox.value = sid;
        checkbox.checked = window.CorsDashboardShared.getLockedServers(device).includes(sid);
        checkbox.style.margin = '0';
        label.classList.add('form-switch');
        const text = document.createElement('span');
        const title = document.createElement('strong'); title.className = 'd-block'; title.textContent = `Khoá Server ${sid}`;
        const host = document.createElement('small'); host.className = 'text-secondary'; host.textContent = config[`serverhost${sid}`] ? `${config[`serverhost${sid}`]}:${config[`port${sid}`] || '—'}` : 'Chưa cấu hình host/cổng';
        text.append(title, host); label.append(checkbox, text); rows.append(label);
    }
    document.body.append(modal);
    modal.showModal();
    let busy = false;
    return new Promise(resolve => {
        modal.addEventListener('close', () => { modal.remove(); resolve(); });
        modal.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
        const cancel = modal.querySelector('.host-lock-cancel');
        cancel.addEventListener('click', () => modal.close());
        modal.querySelector('form').addEventListener('submit', async event => {
            event.preventDefault();
            if (busy) return;
            busy = true;
            const submit = modal.querySelector('[type="submit"]'); submit.disabled = true; cancel.disabled = true; submit.textContent = 'Đang lưu…';
            try {
                const locked_servers = [...rows.querySelectorAll('input:checked')].map(input => Number(input.value));
                const response = await apiFetch(`/api/devices/${encodeURIComponent(serial)}/lock`, { method: 'POST', body: JSON.stringify({locked_servers}) });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || 'Không lưu được khoá host.');
                updateGlobalDevices({serial, server_config_lock: result.server_config_lock, service_config: result.service_config, is_synced: false});
                modal.close();
                showSuccess(result.delivered ? 'Đã gửi thay đổi khoá. Đang chờ trạm xác nhận.' : 'Đã lưu khoá host. Đang chờ trạm kết nối để áp dụng.');
            } catch (error) { modal.querySelector('.host-lock-error').textContent = error.message; }
            finally { busy = false; submit.disabled = false; cancel.disabled = false; submit.textContent = 'Lưu khoá host'; }
        });
    });
}

async function handleUnlockDevice(serial) {
    try {
        const response = await apiFetch(`/api/devices/${encodeURIComponent(serial)}/unlock`, { method: 'POST' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Không mở được khoá host.');
        updateGlobalDevices({serial, server_config_lock: result.server_config_lock});
        showSuccess('Đã gửi yêu cầu mở khoá. Đang chờ trạm xác nhận.');
    } catch (e) { showError(`Lỗi mở khóa: ${e.message}`); throw e; }
}

// ===============================================================================
// === WEBSOCKET LOGIC                                                         ===
// ===============================================================================
function connectWebSocket() {
    if (window.ApiClient?.isSessionInvalidated?.() || !window.ApiClient?.hasToken?.()) return;
    if (ws && [WebSocket.OPEN, WebSocket.CONNECTING].includes(ws.readyState)) return;
    if (wsReconnectTimerId) {
        clearTimeout(wsReconnectTimerId);
        wsReconnectTimerId = null;
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let host = window.location.host;

    // 🔥 Force correct domain if hitting via legacy/monitoring host
    if (host.includes('monitoring.')) {
        host = 'cors.aitogy.com';
    }

    if (window.ApiClient?.createAuthenticatedWebSocket) {
        ws = window.ApiClient.createAuthenticatedWebSocket('/ws/updates', host);
    } else {
        const wsUrl = `${protocol}//${host}/ws/updates`;
        ws = new WebSocket(wsUrl);
    }
    if (!ws) return;
    const socket = ws;

    ws.onopen = () => {
        if (ws !== socket) return;
        updateConnectionStatus(true);
        wsReconnectAttempts = 0;
        lastStatusMetaBySerial.clear();
        refreshDashboardSnapshot(socket);
        //console.log('✓ WebSocket connected');
    };

    ws.onmessage = (event) => {
        if (ws !== socket) return;
        try {
            const message = JSON.parse(event.data);
            handleWebSocketMessage(message);
        } catch (e) {
            console.error('Error parsing WebSocket message:', e);
        }
    };

    ws.onerror = (error) => {
        if (ws !== socket) return;
        console.error('WebSocket error:', error);
        updateConnectionStatus(false);
    };

    ws.onclose = (event) => {
        if (ws !== socket) return;
        ws = null;
        updateConnectionStatus(false);

        if (window.ApiClient?.isAuthWebSocketClose?.(event)) {
            window.ApiClient.forceLogout('websocket_auth_failed');
            return;
        }
        if (window.ApiClient?.isSessionInvalidated?.() || !window.ApiClient?.hasToken?.()) return;

        const delay = Math.min(1000 * Math.pow(2, wsReconnectAttempts), 30000);
        wsReconnectAttempts = Math.min(wsReconnectAttempts + 1, MAX_RECONNECT_ATTEMPTS);
        wsReconnectTimerId = setTimeout(connectWebSocket, delay);
    };
}

async function refreshDashboardSnapshot(socket) {
    const revisions = new Map(lastStatusMetaBySerial);
    try {
        const response = await apiFetch('/api/devices', { cache: 'no-store' });
        if (!response.ok) throw new Error('Snapshot request failed');
        const devices = await response.json();
        if (ws !== socket || window.ApiClient?.isSessionInvalidated?.()) return;
        // A heartbeat received during the request takes precedence over its snapshot.
        enqueueStatusUpdate(devices.filter(device =>
            lastStatusMetaBySerial.get(device.serial) === revisions.get(device.serial)));
    } catch (error) {
        if (ws === socket && !window.ApiClient?.isSessionInvalidated?.()) {
            console.warn('Could not refresh station snapshot after reconnect:', error);
        }
    }
}

function resumeDashboardConnection() {
    if (document.hidden || window.ApiClient?.isSessionInvalidated?.() || !window.ApiClient?.hasToken?.()) return;
    if (wsReconnectTimerId) clearTimeout(wsReconnectTimerId);
    wsReconnectTimerId = null;
    const previous = ws;
    ws = null;
    if (previous) {
        try { previous.close(1000, 'page resumed'); } catch (_) {}
    }
    wsReconnectAttempts = 0;
    updateConnectionStatus(false);
    connectWebSocket();
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) resumeDashboardConnection();
});
window.addEventListener('online', resumeDashboardConnection);
window.addEventListener('pageshow', event => {
    if (event.persisted) resumeDashboardConnection();
});
let lastDashboardWakeCheck = Date.now();
setInterval(() => {
    const now = Date.now();
    if (now - lastDashboardWakeCheck > 30000) resumeDashboardConnection();
    lastDashboardWakeCheck = now;
}, 5000);

window.addEventListener('cors:session-expired', () => {
    if (wsReconnectTimerId) {
        clearTimeout(wsReconnectTimerId);
        wsReconnectTimerId = null;
    }
    if (ws && ws.readyState !== WebSocket.CLOSED) {
        try { ws.close(1000, 'session expired'); } catch (_) {}
    }
});

let lastSatelliteGcTs = 0;

function handleWebSocketMessage(message) {
    const { type, data, serial, count } = message;

    const now = Date.now();
    if (now - lastSatelliteGcTs > 10000) {
        lastSatelliteGcTs = now;
        if (currentNmeaData.satellites) {
            let changed = false;
            Object.keys(currentNmeaData.satellites).forEach(key => {
                if (now - currentNmeaData.satellites[key].lastSeen > 30000) {
                    delete currentNmeaData.satellites[key];
                    changed = true;
                }
            });
            if (changed && detailsPanelNeedsUpdate === false) {
                requestDetailsPanelUpdate('low');
            }
        }
    }

    if (type === 'status_update') {
        const incomingSerial = serial || data?.serial;
        const knownDevice = globalState.devices.find(d =>
            String(d.serial || '').trim().toUpperCase() === String(incomingSerial || '').trim().toUpperCase()
        );
        const statusSerial = knownDevice?.serial || incomingSerial;
        if (data && statusSerial) data.serial = statusSerial;
        // The backend scopes station updates by the user's visible serials.
        const userRole = String(globalState.currentUser?.role || '').toLowerCase();
        if (userRole === 'coordinator') {
            if (!knownDevice) return;
        } else if (userRole === 'viewer') {
            // The backend already enforces the viewer's allowed serial scope.
            // Keep this client-side guard for defense in depth, but ignore owner user_id.
            if (!knownDevice) return;
        }

        if (data && data.timestamp) {
            const agentTimestamp = Number(data.agent_timestamp);
            const serverReceivedAt = Number(data.server_received_at);
            data.measured_latency = Number.isFinite(agentTimestamp) && agentTimestamp > 0 &&
                Number.isFinite(serverReceivedAt) && serverReceivedAt > 0
                ? Math.max(0, serverReceivedAt - agentTimestamp)
                : Math.max(0, (Date.now() / 1000) - Number(data.timestamp));
        }
        enqueueStatusUpdate(data);

        const statusValue = String(data?.status || '').toLowerCase();
        if (statusSerial === globalState.selectedDeviceSerial && ['offline', 'awaiting_license'].includes(statusValue)) {
            const savedBaseCoords = currentNmeaData.base_coords;
            currentNmeaData = { gga: null, gsa: null, satellites: {} };
            if (savedBaseCoords) currentNmeaData.base_coords = savedBaseCoords;
            requestDetailsPanelUpdate('high');
        }

        if (statusSerial === globalState.selectedDeviceSerial && data.base_coords) {
            currentNmeaData.base_coords = data.base_coords;
            requestDetailsPanelUpdate('high');
        }
        if (statusSerial === globalState.selectedDeviceSerial && data.nmea_data && !['offline', 'awaiting_license'].includes(statusValue)) {
            const nmea = data.nmea_data || {};
            if (nmea.gga) currentNmeaData.gga = nmea.gga;
            if (nmea.gsa) currentNmeaData.gsa = nmea.gsa;
            if (nmea.base_coords) currentNmeaData.base_coords = nmea.base_coords;
            const statusSatellites = nmea.satellites || {};
            Object.values(statusSatellites).forEach(sat => {
                const satKey = sat.id || `${sat.talker || sat.system || 'GN'}:${sat.prn}`;
                const existingSat = currentNmeaData.satellites[satKey];
                const newSnr = Number(sat.snr) || 0;
                const oldSnr = existingSat ? (Number(existingSat.snr) || 0) : 0;
                
                currentNmeaData.satellites[satKey] = {
                    ...existingSat,
                    ...sat,
                    lastSeen: Date.now()
                };
            });
            requestDetailsPanelUpdate('high');
        }
    }

    if (type === 'user_assignment_updated') {
        if (data.user_id === globalState.currentUser?.id) {
            // Reopen the WebSocket with the newly committed station permissions.
            window.location.reload();
        }
    }

    if (type === 'device_deleted') {
        const initialLength = globalState.devices.length;
        globalState.devices = globalState.devices.filter(d => d.serial !== serial);

        if (globalState.selectedDeviceSerial === serial) {
            globalState.selectedDeviceSerial = null;
        }

        if (globalState.devices.length < initialLength) {
            lastSearchTerm = '';
            deviceListNeedsUpdate = true;
            detailsPanelNeedsUpdate = true;
            scheduleRender('high');
            showSuccess('Một trạm đã được xóa khỏi danh sách.');
        }
    }

    if (type === 'visitor_update') {
        const visitorEl = document.getElementById('live-visitors-count');
        if (visitorEl) {
            if (lastVisitorCount === count) return;
            lastVisitorCount = count;
            visitorEl.textContent = count;
            visitorEl.style.color = '#ffffff';
            visitorEl.style.textShadow = '0 0 15px #ffffff';
            if (visitorPulseTimerId) {
                clearTimeout(visitorPulseTimerId);
            }
            visitorPulseTimerId = setTimeout(() => {
                visitorEl.style.color = '#ff00ff';
                visitorEl.style.textShadow = 'none';
                visitorPulseTimerId = null;
            }, 300);
        }
        return;
    }

    if (type === 'nmea_update' && serial === globalState.selectedDeviceSerial) {
        const nmeaType = data.type;

        if (nmeaType === 'GSA') {
            currentNmeaData.gsa = data;
            if (data.active_sats && currentNmeaData.satellites) {
                Object.values(currentNmeaData.satellites).forEach(sat => {
                    sat.isTracking = data.active_sats.includes(sat.prn);
                });
            }
            requestDetailsPanelUpdate('high');
        }
        else if (nmeaType === 'GSV' && data.satellites) {
            data.satellites.forEach(sat => {
                const satKey = sat.id || `${sat.talker || sat.system || 'GN'}:${sat.prn}`;
                const existingSat = currentNmeaData.satellites[satKey];
                const newSnr = Number(sat.snr) || 0;
                const oldSnr = existingSat ? (Number(existingSat.snr) || 0) : 0;
                
                currentNmeaData.satellites[satKey] = {
                    ...existingSat,
                    ...sat,
                    lastSeen: Date.now()
                };
            });
            requestDetailsPanelUpdate('high');
        }
        else if (nmeaType === 'GGA') {
            currentNmeaData.gga = data;
            requestDetailsPanelUpdate('high');
        }
        else if (nmeaType === 'GST') {
            currentNmeaData.gst = data;
            if (typeof data.hacc !== 'undefined') currentNmeaData.hacc = data.hacc;
            requestDetailsPanelUpdate('high');
        }
    }
    if (type === 'rtcm_signal_update' && serial === globalState.selectedDeviceSerial) {
        const data = message.data || {};
        const satellites = Array.isArray(data?.satellites) ? data.satellites : [];
        
        // Keep Agent → Server latency from status_update; RTCM timestamps use the agent's clock.
        
        if (satellites.length) {
            satellites.forEach(sat => {
                const satKey = sat.id || `RTCM:${sat.system || 'un'}:${sat.prn}`;
                const existingSat = currentNmeaData.satellites[satKey];
                const newSnr = Number(sat.snr) || 0;
                const oldSnr = existingSat ? (Number(existingSat.snr) || 0) : 0;
                const newAz = Number(sat.azimuth) || 0;
                const newEl = Number(sat.elevation) || 0;
                const hasNewPosition = (newAz !== 0 || newEl !== 0);
                const hasOldPosition = existingSat && (Number(existingSat.azimuth) !== 0 || Number(existingSat.elevation) !== 0);

                if (existingSat && !hasNewPosition && hasOldPosition) {
                    // Az/El mới = 0 nhưng cũ đã có vị trí → giữ az/el cũ để vệ tinh không nhảy về trung tâm
                    currentNmeaData.satellites[satKey] = {
                        ...existingSat,
                        ...sat,
                        azimuth: existingSat.azimuth,
                        elevation: existingSat.elevation,
                        lastSeen: Date.now()
                    };
                } else {
                    currentNmeaData.satellites[satKey] = {
                        ...existingSat,
                        ...sat,
                        lastSeen: Date.now()
                    };
                }
            });
            currentNmeaData.rtcm = {
                packets: data.packets || [],
                errors: data.errors || 0,
                bufferedBytes: data.bufferedBytes || 0,
                lastSeen: Date.now()
            };
            requestDetailsPanelUpdate('high');
        }
    }
    if (type === 'activity_log_event') {
        window.logActivity(data.type, data.message, data.icon);
    }
}

// ===============================================================================
// === UTILITIES                                                               ===
// ===============================================================================

async function apiFetch(url, options = {}, retries = 3) {
    if (window.ApiClient?.apiFetch) {
        return window.ApiClient.apiFetch(url, options, retries);
    }
    const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    try {
        const response = await fetch(url, { ...options, headers, signal: controller.signal });
        clearTimeout(timeoutId);

        if (response.status === 401) {
            if (typeof NavigationManager !== 'undefined') {
                NavigationManager.forceLogout();
            } else {
                localStorage.removeItem('access_token');
                sessionStorage.removeItem('access_token');
                softGoHome();
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
        updateConnectionStatus(false);
        throw error;
    }
}

function removeDeviceLocal(serial) {
    const initialLength = globalState.devices.length;
    globalState.devices = globalState.devices.filter(d => d.serial !== serial);

    if (globalState.selectedDeviceSerial === serial) {
        globalState.selectedDeviceSerial = null;
    }

    if (globalState.devices.length < initialLength) {
        lastSearchTerm = '';
        deviceListNeedsUpdate = true;
        detailsPanelNeedsUpdate = true;
        scheduleRender('high');
    }
}

function showToast(message, type) {
    window.CorsUI.showToast(type, message);
}

window.logActivity = function (type, message, icon) {
    const event = new CustomEvent('activity-log', {
        detail: { type, message, icon, timestamp: Date.now() }
    });
    window.dispatchEvent(event);
};

function showSuccess(message) {
    showToast(message, 'success');
    window.logActivity('success', message, '✅');
}

function showError(message) {
    const userMessage = (typeof message === 'string' && (message.includes('NetworkError') || message.includes('fetch')))
        ? 'Lỗi kết nối mạng. Vui lòng kiểm tra internet.'
        : message;

    showToast(userMessage, 'danger');
    window.logActivity('error', userMessage, '❌');
}

function updateConnectionStatus(connected) {
    const statusIndicator = document.getElementById('connection-status');
    if (!statusIndicator) return;
    statusIndicator.className = `navbar-text me-3 fs-5 ${connected ? 'text-success' : 'text-danger'}`;
    statusIndicator.innerHTML = connected ? '<i class="bi bi-reception-4"></i>' : '<i class="bi bi-reception-0"></i>';
    statusIndicator.title = connected ? 'Đã kết nối' : 'Mất kết nối';
}

function hasPermission(permission) {
    return globalState.currentUser?.permissions?.includes(permission);
}

async function loadCurrentUser() {
    try {
        const response = await apiFetch('/api/auth/me');
        if (response.ok) {
            globalState.currentUser = await response.json();
            window.APP_VERSION = globalState.currentUser.app_version || 'UNKNOWN';
        } else {
            throw new Error('Failed to load user info');
        }
    } catch (error) {
        if (isMainPublicRoute()) return;
        localStorage.removeItem('access_token');
        sessionStorage.removeItem('access_token');
        softGoHome();
    }
}

async function loadInitialDevices() {
    try {
        const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
        const isPublicVisitor = !token;
        const apiUrl = isPublicVisitor ? '/api/public/map-stations' : '/api/map/stations';

        const response = await apiFetch(apiUrl);
        if (!response.ok) throw new Error('Failed to load devices');

        let devicesFromApi = await response.json();

        // Public endpoint returns lean station data; normalize to the shape expected by UI components.
        if (isPublicVisitor) {
            try {
                const summaryResponse = await apiFetch('/api/public/network-summary');
                if (summaryResponse.ok) {
                    globalState.publicNetworkSummary = await summaryResponse.json();
                }
            } catch (summaryError) {
                console.warn('Could not load public network summary:', summaryError);
            }

            devicesFromApi = (Array.isArray(devicesFromApi) ? devicesFromApi : []).map((d) => ({
                serial: d.serial,
                name: d.name,
                status: d.status,
                timestamp: d.timestamp,
                offline_since: d.offline_since,
                bps: d.bps || 0,
                ntrip_connected: !!d.ntrip_connected,
                ntrip_status: d.ntrip_status || null,
                service_config: d.service_config || {},
                detected_chip_type: d.detected_chip_type,
                is_provisioned: d.is_provisioned,
                base_config: {
                    coords: {
                        lat: d.lat,
                        lon: d.lon
                    }
                },
                system_info: d.system_info || {}
            }));
        } else {
            devicesFromApi = (Array.isArray(devicesFromApi) ? devicesFromApi : []).map((d) => ({
                ...d,
                base_config: { coords: { lat: d.lat, lon: d.lon, alt: d.alt } },
                system_info: d.system_info || {},
            }));
        }

        updateGlobalDevices(devicesFromApi);
    } catch (error) {
        if (error.message !== 'Unauthorized') {
            showError('Không thể tải danh sách trạm');
        }
    }
}

function setupUIbasedOnPermissions() {
    // === HIỂN THỊ WIDGET MONITORING CHO ADMIN ===
    const monitorWidget = document.getElementById('admin-monitoring-widget');
    if (monitorWidget) {
        if (globalState.currentUser?.role === 'admin') {
            monitorWidget.classList.remove('d-none');
        } else {
            monitorWidget.classList.add('d-none');
        }
    }
}

function setupEventListeners() {

    const mobileStationListBtn = document.getElementById('mobile-station-list-btn');
    if (mobileStationListBtn) {
        mobileStationListBtn.addEventListener('click', () => {
            document.body.classList.remove('mobile-detail-open');
            window.scrollTo({ top: 0, behavior: 'auto' });
        });
    }

}

// === ENTRY POINT ===
async function initializeCorsApp() {
    // 1. Auth Check with Public Route Exceptions
    const publicRoutes = ['/', '/station/', '/network-status', '/coverage', '/blog', '/api/docs'];
    const currentPath = window.location.pathname;
    const isPublicRoute = publicRoutes.some(route => 
        route === '/' ? currentPath === '/' : currentPath.startsWith(route)
    );

    if (!isPublicRoute && !localStorage.getItem('access_token') && !sessionStorage.getItem('access_token')) {
        if (typeof NavigationManager !== 'undefined') {
            NavigationManager.forceLogout();
        } else {
            localStorage.removeItem('access_token');
            sessionStorage.removeItem('access_token');
            softGoHome();
        }
        return;
    }

    // SPECIAL HANDLING FOR PUBLIC HOMEPAGE/STATION/BLOG (NO LOGIN REQUIRED)
    if (isPublicRoute && !localStorage.getItem('access_token') && !sessionStorage.getItem('access_token')) {
        //console.log("Serving Public Content...");
        
        const homepageContainer = document.getElementById('homepage-root');
        if (homepageContainer && !window.homepageRoot) {
            window.homepageRoot = ReactDOM.createRoot(homepageContainer);
        }

        const enterpriseDetailContainer = document.getElementById('enterprise-detail-root');
        if (enterpriseDetailContainer && !window.enterpriseDetailRoot) {
            window.enterpriseDetailRoot = ReactDOM.createRoot(enterpriseDetailContainer);
        }

        const blogContainer = document.getElementById('blog-root');
        if (blogContainer && !window.blogRoot) {
            window.blogRoot = ReactDOM.createRoot(blogContainer);
        }

        const apiDocsContainer = document.getElementById('api-docs-root');
        if (apiDocsContainer && !window.apiDocsRoot) {
            window.apiDocsRoot = ReactDOM.createRoot(apiDocsContainer);
        }
        
        if (currentPath === '/' || currentPath === '/network-status' || currentPath === '/coverage') {
            // Paint the hero immediately. Network data updates the same root later and
            // never blocks first content or basic landing interactions.
            switchView('homepage');
            dismissAppPreloader();
            loadInitialDevices().then(() => {
                if (globalState.currentView === 'homepage' && window.homepageRoot && window.HomePageEnterprise) {
                    renderReactComponent(window.homepageRoot, window.HomePageEnterprise, {
                        devices: globalState.devices,
                        networkSummary: globalState.publicNetworkSummary
                    });
                }
            }).catch((error) => console.warn('[Landing] Async station refresh failed:', error));
        } else if (currentPath.startsWith('/blog')) {
            switchView('blog');
        } else if (currentPath.startsWith('/station/')) {
             await loadInitialDevices();
             const token = currentPath.split('/').pop();
             const device = resolveDeviceFromStationPath(token);
             if (device) globalState.selectedDeviceSerial = device.serial;
             switchView('station-detail');
             if (device) {
                 const canonicalPath = buildPublicStationPath(device);
                 if (window.location.pathname !== canonicalPath) {
                     window.history.replaceState({}, '', canonicalPath);
                 }
             }
        } else if (currentPath === '/api/docs') {
            switchView('api-docs');
        }
        dismissAppPreloader();
        return; // CRITICAL: Stop here for public visitors!
    }

    // Parallel load User info and Initial Devices to boost speed
    const [userResponse, devicesResponse] = await Promise.allSettled([
        loadCurrentUser(),
        loadInitialDevices()
    ]);

    if (!globalState.currentUser) {
        console.warn("[Main] Failed to load current user, stopping authenticated init.");
        dismissAppPreloader();
        return;
    }

    setupUIbasedOnPermissions();
    setupEventListeners();

    // Update auth buttons when user loaded
    updateAuthButtons();

    deviceListRoot = ReactDOM.createRoot(document.getElementById('device-list-root'));
    detailsPanelRoot = ReactDOM.createRoot(document.getElementById('details-panel-root'));

    const homepageContainer = document.getElementById('homepage-root');
    if (homepageContainer) {
        window.homepageRoot = ReactDOM.createRoot(homepageContainer);
    }

    const enterpriseDetailContainer = document.getElementById('enterprise-detail-root');
    if (enterpriseDetailContainer) {
        window.enterpriseDetailRoot = ReactDOM.createRoot(enterpriseDetailContainer);
    }

    const blogContainer = document.getElementById('blog-root');
    if (blogContainer) {
        window.blogRoot = ReactDOM.createRoot(blogContainer);
    }

    const apiDocsContainer = document.getElementById('api-docs-root');
    if (apiDocsContainer) {
        window.apiDocsRoot = ReactDOM.createRoot(apiDocsContainer);
    }

    const mapContainer = document.getElementById('map-root');
    if (mapContainer) {
        mapRoot = ReactDOM.createRoot(mapContainer);
    }

    const modalContainer = document.createElement('div');
    document.body.appendChild(modalContainer);
    const modalRoot = ReactDOM.createRoot(modalContainer);

    // Async load - Babel may not have transpiled these yet
    waitForGlobalComponent('CasterConfigModal', 30000).then(comp => {
        if (comp) renderReactComponent(modalRoot, comp, {}, 'Caster config modal is not loaded');
    }).catch(() => {});

    waitForGlobalComponent('CasterSourcesModal', 30000).then(comp => {
        if (comp) {
            const sourcesContainer = document.createElement('div');
            document.body.appendChild(sourcesContainer);
            const sourcesRoot = ReactDOM.createRoot(sourcesContainer);
            renderReactComponent(sourcesRoot, comp, {}, 'Caster sources modal is not loaded');
        }
    }).catch(() => {});


    window.fetchInitialDevices = loadInitialDevices;
    // Note: loadInitialDevices already called in Promise.all above
    connectWebSocket();

    // 2. Dynamic Routing Handler (Clean URLs)
    let initialView = 'dashboard';
    if (currentPath.startsWith('/station/')) {
        const token = currentPath.split('/').pop();
        const device = resolveDeviceFromStationPath(token);
        if (device) {
            //console.log("Routing to Station Detail:", token);
            globalState.selectedDeviceSerial = device.serial;
            switchView('station-detail');
            const canonicalPath = buildPublicStationPath(device);
            if (window.location.pathname !== canonicalPath) {
                window.history.replaceState({}, '', canonicalPath);
            }
        }
    } else if (currentPath === '/network-status') {
        switchView('homepage');
    } else if (currentPath.startsWith('/blog')) {
        switchView('blog');
        dismissAppPreloader();
        return;
    } else if (currentPath === '/api/docs') {
        switchView('api-docs');
        dismissAppPreloader();
        return;
    } else if (selectDeviceFromDashboardQuery()) {
        initialView = 'dashboard';
    } else if (window.location.search.includes('view=map')) {
        initialView = 'map';
        switchView('map');
    } else {
        switchView('dashboard');
    }

    window.renderBlog = (slug = null) => {
    if (window.blogRoot) {
        renderReactComponent(window.blogRoot, window.BlogSystem, { slug }, 'Blog component is not loaded');
    }
};

dismissAppPreloader();
//console.log(`✓ App initialized with Performance Optimization (view: ${initialView})`);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeCorsApp, { once: true });
} else {
    initializeCorsApp();
}

window.openPublicMap = async function() {
    const modalEl = document.getElementById('publicMapModal');
    if (!modalEl) return;

    const leaflet = window.Leaflet || window.L;
    if (!leaflet || typeof leaflet.map !== 'function') {
        console.error('Leaflet namespace is not available or has been replaced');
        return;
    }

    const fitPublicMap = () => {
        if (!window.publicMapInstance) return;
        window.publicMapInstance.invalidateSize();
        if (window.publicMapBounds?.length) {
            window.publicMapInstance.fitBounds(window.publicMapBounds, { padding: [50, 50], maxZoom: 12 });
        } else {
            window.publicMapInstance.setView([16.0471, 108.2068], 6);
        }
    };

    const waitForModalShown = () => new Promise((resolve) => {
        if (modalEl.classList.contains('show')) {
            setTimeout(resolve, 150);
            return;
        }
        modalEl.addEventListener('shown.bs.modal', () => setTimeout(resolve, 150), { once: true });
    });

    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
    await waitForModalShown();

    if (window.publicMapInstance) {
        setTimeout(fitPublicMap, 50);
        setTimeout(fitPublicMap, 300);
        return;
    }

    const STATUS_TEXT_MAP = {
        online: 'ONLINE', offline: 'OFFLINE', unprovisioned: 'CHUA KICH HOAT',
        awaiting_license: 'CHO LICENSE', configuring: 'DANG CAU HINH',
        rebooting: 'KHOI DONG LAI', rebooting_for_reset: 'DANG KHOI PHUC',
        locked: 'BI KHOA', sleep: 'CHE DO NGU', streaming: 'DANG PHAT',
        starting: 'DANG KHOI CHAY'
    };

    const STATUS_CLASS_MAP = {
        online: 'bg-success', offline: 'bg-danger', unprovisioned: 'bg-warning text-dark',
        awaiting_license: 'bg-info text-dark', configuring: 'bg-primary',
        rebooting: 'bg-warning text-dark', rebooting_for_reset: 'bg-danger',
        locked: 'bg-danger', sleep: 'bg-info', streaming: 'bg-success',
        starting: 'bg-warning text-dark'
    };

    const getStatusColor = (status) => {
        switch (status) {
            case 'online': return '#198754';
            case 'offline': return '#dc3545';
            case 'sleep': return '#0dcaf0';
            case 'streaming': return '#198754';
            case 'starting': return '#ffc107';
            case 'unprovisioned': return '#ffc107';
            case 'configuring': return '#0d6efd';
            case 'locked': return '#dc3545';
            default: return '#6c757d';
        }
    };

    const getMapEffectiveStatus = (dev) => (
        window.CorsDashboardShared?.getMapEffectiveStatus?.(dev)
        || String(dev?.status || 'offline').toLowerCase()
    );

    const normalizeVietnamCoords = (rawLat, rawLon) => {
        let lat = Number(rawLat);
        let lon = Number(rawLon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon) || (lat === 0 && lon === 0)) return null;
        const inVietnam = (a, b) => a >= 8 && a <= 24.5 && b >= 102 && b <= 110.5;
        if (inVietnam(lat, lon)) return [lat, lon];
        if (inVietnam(lon, lat)) return [lon, lat];
        return null;
    };

    window.publicMapInstance = leaflet.map('publicMapContainer', {
        center: [16.0471, 108.2068],
        zoom: 6,
        zoomControl: true
    });

    const googleHybrid = leaflet.tileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', {
        maxZoom: 20,
        attribution: 'Google Hybrid'
    });
    const googleTerrain = leaflet.tileLayer('https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}', {
        maxZoom: 20,
        attribution: 'Google Terrain'
    });
    const googleStreets = leaflet.tileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', {
        maxZoom: 20,
        attribution: 'Google Streets'
    });

    googleHybrid.addTo(window.publicMapInstance);
    leaflet.control.layers({
        'Ve tinh': googleHybrid,
        'Dia hinh': googleTerrain,
        'Duong pho': googleStreets
    }, null, { position: 'topright' }).addTo(window.publicMapInstance);

    try {
        const res = await fetch('/api/public/map-stations');
        if (!res.ok) throw new Error('Cannot load station data');
        const stations = await res.json();
        const bounds = [];

        stations.forEach((st) => {
            const coords = normalizeVietnamCoords(st.lat, st.lon);
            if (!coords) return;
            const [stLat, stLon] = coords;
            const effectiveStatus = getMapEffectiveStatus(st);
            const color = getStatusColor(effectiveStatus);
            const label = STATUS_TEXT_MAP[effectiveStatus] || effectiveStatus.toUpperCase();
            const badgeClass = STATUS_CLASS_MAP[effectiveStatus] || 'bg-secondary';
            const isVirtual = String(st.detected_chip_type || '').toUpperCase() === 'VIRTUAL';

            const icon = leaflet.divIcon({
                className: 'custom-div-icon',
                html: `<div style="background:${color};width:14px;height:14px;border-radius:50%;border:2px solid white;box-shadow:0 0 5px rgba(0,0,0,0.5)"></div>`,
                iconSize: [14, 14],
                iconAnchor: [7, 7]
            });

            const popup = `<div style="color:#333;min-width:180px;font-family:sans-serif;">
                <strong style="font-size:1.1em;display:block;margin-bottom:5px;">${escapeHtmlMain(st.name || st.serial || 'Station')}</strong>
                <div class="mb-2"><span class="badge ${badgeClass}">${label}</span>${isVirtual ? '<span class="badge bg-info text-dark ms-1">VIRTUAL</span>' : ''}</div>
                <div class="small text-muted">Lat: ${stLat.toFixed(6)} | Lon: ${stLon.toFixed(6)}</div>
            </div>`;

            leaflet.marker([stLat, stLon], { icon }).addTo(window.publicMapInstance).bindPopup(popup);
            bounds.push([stLat, stLon]);
        });

        window.publicMapBounds = bounds;
        setTimeout(fitPublicMap, 50);
        setTimeout(fitPublicMap, 300);
    } catch (err) {
        console.error('Map error:', err);
        setTimeout(fitPublicMap, 50);
    }
};
