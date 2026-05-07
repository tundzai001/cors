// main.js 

'use strict';

// === GLOBAL STATE  ===
let globalState = {
    devices: [],
    selectedDeviceSerial: null,
    currentUser: null,
    searchTerm: '',
    currentView: 'dashboard',
    stateVersion: 0
};
window.globalState = globalState;
// === REACT ROOTS ===
let deviceListRoot = null;
let detailsPanelRoot = null;
let mapRoot = null;

// === WEBSOCKET ===
let ws = null;
let wsReconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 10;

// === DASHBOARD STATE & LOGIC  ===
let currentNmeaData = { gga: null, gsa: null, satellites: {} };

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

// Batch status updates to prevent UI stalls under high websocket throughput.
const STATUS_FLUSH_INTERVAL = 140;
let pendingStatusUpdates = new Map();
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

function scheduleRender(priority = 'high') {
    const queueDashboardRender = () => {
        if (dashboardRafId !== null) return;
        dashboardRafId = requestAnimationFrame(() => {
            dashboardRafId = null;
            if (!deviceListNeedsUpdate && !detailsPanelNeedsUpdate) return;
            renderDashboard();
            lastRenderTime = Date.now();
        });
    };

    const queueMapRender = () => {
        if (mapRafId !== null) return;
        mapRafId = requestAnimationFrame(() => {
            mapRafId = null;
            if (!mapNeedsUpdate) return;
            renderMap();
            lastMapRenderTime = Date.now();
        });
    };

    if (priority === 'high') {
        deviceListNeedsUpdate = true;
        detailsPanelNeedsUpdate = true;

        const now = Date.now();
        const waitMs = Math.max(0, RENDER_INTERVAL - (now - lastRenderTime));
        if (waitMs === 0) {
            queueDashboardRender();
        } else {
            if (dashboardTimerId) clearTimeout(dashboardTimerId);
            dashboardTimerId = setTimeout(queueDashboardRender, waitMs);
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

        // --- SORT Logic ---
        const needsSort = globalState.searchTerm !== lastSearchTerm ||
            currentVersion === 0 ||
            lastSearchTerm === null;

        if (needsSort) {
            cachedFilteredDevices.sort((a, b) => {
                const isPendingSetup = (d) => {
                    const status = String(d.status || '').toLowerCase();
                    const name = String(d.name || '').trim();
                    if (status === 'awaiting_license' || status === 'unprovisioned') return true;
                    if (!name) return true;
                    return false;
                };

                const aPending = isPendingSetup(a);
                const bPending = isPendingSetup(b);
                if (aPending !== bPending) return aPending ? -1 : 1;

                const nameA = String(a.name || a.serial || '').trim();
                const nameB = String(b.name || b.serial || '').trim();
                return nameA.localeCompare(nameB, 'vi', { sensitivity: 'base', numeric: true });
            });
        }

        lastSearchTerm = globalState.searchTerm;
        lastStateVersion = currentVersion;
    }
    return cachedFilteredDevices;
}

// === SPLIT RENDER FUNCTIONS ===
function renderDashboard() {
    if (!deviceListNeedsUpdate && !detailsPanelNeedsUpdate) return;

    const filteredDevices = getFilteredDevices();

    // Render Device List
    if (deviceListNeedsUpdate && deviceListRoot) {
        if (!window.DeviceList) {
            console.warn("[Main] window.DeviceList is undefined. Deferring render.");
            return;
        }
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
        if (!window.DetailsPanel) {
            console.warn("[Main] window.DetailsPanel is undefined. Deferring render.");
            return;
        }
        const selectedDevice = globalState.devices.find(d => String(d.serial) === String(globalState.selectedDeviceSerial));
        detailsPanelRoot.render(
            React.createElement(window.DetailsPanel, {
                device: selectedDevice,
                nmeaData: currentNmeaData,
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

    if (globalState.currentUser) {
        const userInfoEl = document.getElementById('user-info');
        if (userInfoEl) {
            userInfoEl.textContent = `${globalState.currentUser.full_name || globalState.currentUser.username} (${globalState.currentUser.role.toUpperCase()})`;
        }
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
    globalState.currentView = viewName;

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
    const backBtnLeft = document.getElementById('back-home-btn-left');

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
    if (backBtnLeft) {
        if (isMapView) backBtnLeft.classList.remove('d-none'); else backBtnLeft.classList.add('d-none');
    }
}

// Wire auth buttons if present
document.addEventListener('click', (e) => {
    const target = e.target;
    if (!target) return;
    if (target.id === 'login-btn' || (target.closest && target.closest('#login-btn'))) {
        e.preventDefault();
        window.location.href = '/';
    }
    if (target.id === 'back-home-btn' || (target.closest && target.closest('#back-home-btn'))) {
        e.preventDefault();
        window.switchView('homepage');
    }
    if (target.id === 'back-home-btn-left' || (target.closest && target.closest('#back-home-btn-left'))) {
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
            if ((!hasOwn(newDevice, 'service_config') || !hasData(newDevice.service_config)) && hasData(existingDevice.service_config)) {
                newDevice.service_config = existingDevice.service_config;
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
                const s1_bps = Number(newDevice.ntrip_stats.server1_bps) || 0;
                const s2_bps = Number(newDevice.ntrip_stats.server2_bps) || 0;

                newDevice.ntrip_status = {
                    server1: { connected: s1_bps > 0, bps: s1_bps },
                    server2: { connected: s2_bps > 0, bps: s2_bps }
                };

                newDevice.bps = s1_bps + s2_bps;
                // Prefer backend/agent truth if provided; fallback to bps heuristic.
                if (typeof newDevice.ntrip_connected !== 'boolean') {
                    newDevice.ntrip_connected = newDevice.bps > 0;
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
            console.log(`[STATE] Structural/Status change version: ${globalState.stateVersion}`);
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
    if (globalState.selectedDeviceSerial === serial) return;

    globalState.selectedDeviceSerial = serial;
    currentNmeaData = { gga: null, gsa: null, satellites: {} };

    detailsPanelNeedsUpdate = true;
    scheduleRender('high');
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

async function handleDeleteDevice(serial, name) {
    const confirmMsg = `⚠️ XÓA KHỎI DANH SÁCH\n\nBạn có chắc muốn xóa trạm '${name}' khỏi giao diện quản lý không?\n\nLưu ý: Hành động này không reset Pi.`;
    if (!confirm(confirmMsg)) return;

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
        showSuccess(`Đã gửi lệnh reset đến thiết bị. Giao diện sẽ tự cập nhật.`);
    } catch (error) {
        showError(`Không thể gửi lệnh reset: ${error.message}`);
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
        await apiFetch(`/api/devices/${serial}/command`, {
            method: 'POST',
            body: JSON.stringify({ command: 'PROVISION_DEVICE', payload: { name: newName } })
        });
        showSuccess('Đã gửi lệnh kích hoạt! Giao diện sẽ tự cập nhật.');
    } catch (e) {
        showError(`Lỗi kích hoạt: ${e.message}`);
        throw e;
    }
}

async function handleConfigureChip(serial, payload) {
    try {
        await apiFetch(`/api/devices/${serial}/configure-chip`, {
            method: 'POST',
            body: JSON.stringify({ command: 'CONFIGURE_CHIP', payload: payload })
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

        if (!response.ok) {
            const error = await response.json();
            if (response.status === 503) {
                showSuccess('Đã lưu cấu hình vào DB! Lệnh sẽ được gửi khi thiết bị online.');
                return;
            }
            throw new Error(error.detail || 'Lỗi không xác định');
        }

        showSuccess('Đã lưu và gửi cấu hình dịch vụ! Thiết bị sẽ áp dụng sau giây lát.');

    } catch (e) {
        showError(`Lỗi cấu hình dịch vụ: ${e.message}`);
        throw e;
    }
}


async function handleLockDevice(serial) {
    try {
        await apiFetch(`/api/devices/${serial}/lock`, { method: 'POST' });
        showSuccess('Đã gửi lệnh khóa thiết bị.');
    } catch (e) {
        showError(`Lỗi khóa thiết bị: ${e.message}`);
        throw e;
    }
}

async function handleUnlockDevice(serial) {
    try {
        await apiFetch(`/api/devices/${serial}/unlock`, { method: 'POST' });
        showSuccess('Đã gửi lệnh mở khóa thiết bị.');
    } catch (e) {
        showError(`Lỗi mở khóa: ${e.message}`);
        throw e;
    }
}

// ===============================================================================
// === WEBSOCKET LOGIC                                                         ===
// ===============================================================================
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let host = window.location.host;

    // 🔥 Force correct domain if hitting via legacy/monitoring host
    if (host.includes('monitoring.')) {
        host = 'cors.aitogy.com';
    }

    const wsUrl = `${protocol}//${host}/ws/updates`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        updateConnectionStatus(true);
        wsReconnectAttempts = 0;
        console.log('✓ WebSocket connected');
    };

    ws.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            handleWebSocketMessage(message);
        } catch (e) {
            console.error('Error parsing WebSocket message:', e);
        }
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        updateConnectionStatus(false);
    };

    ws.onclose = () => {
        updateConnectionStatus(false);

        if (wsReconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            const delay = Math.min(1000 * Math.pow(2, wsReconnectAttempts), 30000);
            wsReconnectAttempts++;
            console.log(`WebSocket disconnected. Reconnecting in ${delay / 1000}s...`);
            setTimeout(connectWebSocket, delay);
        } else {
            showError('Không thể kết nối đến server. Vui lòng tải lại trang.');
        }
    };
}

function handleWebSocketMessage(message) {
    const { type, data, serial, count } = message;

    if (type === 'status_update') {
        const statusSerial = serial || data?.serial;
        // --- LOGIC CHẶN TRẠM LẠ CHO ROLES BỊ GIỚI HẠN (COORDINATOR, OPERATOR, VIEWER) ---
        if (globalState.currentUser && ['coordinator', 'operator', 'viewer'].includes(globalState.currentUser.role)) {

            // Trường hợp 1: Admin vừa gán/thu hồi quyền sở hữu (Bản tin có chứa user_id)
            // Lưu ý: Viewers không dựa trên user_id ownership nên sẽ rơi vào Trường hợp 2
            if (data.user_id !== undefined && data.user_id !== null) {
                if (data.user_id !== globalState.currentUser.id) {
                    const exists = globalState.devices.some(d => d.serial === statusSerial);
                    if (exists) {
                        console.log(`Removed unassigned device: ${statusSerial}`);
                        removeDeviceLocal(statusSerial);
                    }
                    return;
                }
            }

            // Trường hợp 2: Trạm gửi Heartbeat/Status hoặc các bản tin khác
            else {
                // Kiểm tra xem trạm này có nằm trong danh sách đang hiển thị hay không
                const isMyDevice = globalState.devices.some(d => d.serial === statusSerial);
                if (!isMyDevice) {
                    return;
                }
            }
        }

        enqueueStatusUpdate(data);

        if (statusSerial === globalState.selectedDeviceSerial && data.base_coords) {
            currentNmeaData.base_coords = data.base_coords;
            requestDetailsPanelUpdate('high');
        }
    }

    if (type === 'user_assignment_updated') {
        // Nếu chính mình là user bị thay đổi gán trạm -> Load lại toàn bộ devices
        if (data.user_id === globalState.currentUser?.id) {
            console.log("Assignment updated for current user. Reloading devices...");
            // Gọi lại hàm lấy danh sách trạm ban đầu
            if (window.fetchInitialDevices) {
                window.fetchInitialDevices();
            } else {
                // Fallback: Reload trang nếu không tìm thấy hàm
                window.location.reload();
            }
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

        // PERFORMANCE: Batch high-frequency updates using requestAnimationFrame
        if (!window.pendingNmeaUpdates) window.pendingNmeaUpdates = [];
        window.pendingNmeaUpdates.push(data);
        
        if (window.pendingNmeaUpdates.length > 5) {
             requestDetailsPanelUpdate('low'); // Reduced priority for massive streams
        }
        else if (nmeaType === 'GSA') {
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
                currentNmeaData.satellites[sat.prn] = {
                    ...currentNmeaData.satellites[sat.prn],
                    ...sat,
                    lastSeen: Date.now()
                };
            });
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
                window.location.href = '/';
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
    const toastContainer = document.getElementById('toast-container') || createToastContainer();
    const toastEl = document.createElement('div');
    toastEl.className = `toast align-items-center bg-${type} text-white border-0`;
    toastEl.setAttribute('role', 'alert');

    toastEl.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    `;

    toastContainer.appendChild(toastEl);
    const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container position-fixed top-0 end-0 p-3';
    container.style.zIndex = 1100;
    document.body.appendChild(container);
    return container;
}

window.logActivity = function (type, message, icon) {
    const event = new CustomEvent('activity-log', {
        detail: { type, message, icon, timestamp: Date.now() }
    });
    window.dispatchEvent(event);
};

function showSuccess(message) {
    if (window.showEnhancedToast) {
        window.showEnhancedToast(message, 'success');
    } else {
        showToast(message, 'success');
    }
    window.logActivity('success', message, '✅');
}

function showError(message) {
    const userMessage = (typeof message === 'string' && (message.includes('NetworkError') || message.includes('fetch')))
        ? 'Lỗi kết nối mạng. Vui lòng kiểm tra internet.'
        : message;

    if (window.showEnhancedToast) {
        window.showEnhancedToast(userMessage, 'danger');
    } else {
        showToast(userMessage, 'danger');
    }
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
        localStorage.removeItem('access_token');
        sessionStorage.removeItem('access_token');
        window.location.href = '/';
    }
}

async function loadInitialDevices() {
    try {
        const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
        const isPublicVisitor = !token;
        const apiUrl = isPublicVisitor ? '/api/public/map-stations' : '/api/devices';

        const response = await apiFetch(apiUrl);
        if (!response.ok) throw new Error('Failed to load devices');

        let devicesFromApi = await response.json();

        // Public endpoint returns lean station data; normalize to the shape expected by UI components.
        if (isPublicVisitor) {
            devicesFromApi = (Array.isArray(devicesFromApi) ? devicesFromApi : []).map((d) => ({
                serial: d.serial,
                name: d.name,
                status: d.status,
                bps: d.bps || 0,
                ntrip_connected: !!d.ntrip_connected,
                ntrip_status: d.ntrip_stats || null,
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
        }

        updateGlobalDevices(devicesFromApi);
    } catch (error) {
        if (error.message !== 'Unauthorized') {
            showError('Không thể tải danh sách trạm');
        }
    }
}

function setupUIbasedOnPermissions() {
    const exportBtn = document.getElementById('export-csv-btn');
    if (exportBtn) {
        if (hasPermission('export:data')) {
            exportBtn.classList.remove('disabled');
            exportBtn.removeAttribute('title');
        } else {
            exportBtn.classList.add('disabled');
            exportBtn.title = 'Bạn không có quyền xuất dữ liệu';
        }
    }

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

    const exportBtn = document.getElementById('export-csv-btn');
    if (exportBtn) {
        exportBtn.addEventListener('click', async (e) => {
            e.preventDefault();
            if (exportBtn.classList.contains('disabled')) return;

            try {
                const response = await apiFetch('/api/devices/export/csv');
                if (!response.ok) throw new Error('Export failed');
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `cors_devices_${new Date().toISOString().split('T')[0]}.csv`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
                showSuccess('File CSV đã được tải xuống!');
            } catch (error) {
                if (error.message !== 'Unauthorized') {
                    showError('Không thể export: ' + error.message);
                }
            }
        });
    }
}

// === ENTRY POINT ===
document.addEventListener('DOMContentLoaded', async () => {
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
            window.location.href = '/';
        }
        return;
    }

    // SPECIAL HANDLING FOR PUBLIC HOMEPAGE/STATION/BLOG (NO LOGIN REQUIRED)
    if (isPublicRoute && !localStorage.getItem('access_token') && !sessionStorage.getItem('access_token')) {
        console.log("Serving Public Content...");
        await loadInitialDevices(); // Needed for stats

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
        
        if (currentPath === '/') {
            switchView('homepage');
            const homeComponent = await waitForGlobalComponent('HomePageEnterprise', 5000);
            if (window.homepageRoot) {
                renderReactComponent(window.homepageRoot, homeComponent, { devices: globalState.devices }, 'Homepage component is not loaded');
            }
        } else if (currentPath === '/network-status') {
            switchView('homepage');
            const homeComponent = await waitForGlobalComponent('HomePageEnterprise', 5000);
            if (window.homepageRoot) {
                renderReactComponent(window.homepageRoot, homeComponent, { devices: globalState.devices }, 'Homepage component is not loaded');
            }
        } else if (currentPath.startsWith('/blog')) {
            switchView('blog');
            const slug = currentPath.split('/').pop() !== 'blog' ? currentPath.split('/').pop() : null;
            if (window.blogRoot) {
                const blogComponent = await waitForGlobalComponent('BlogSystem', 5000);
                renderReactComponent(window.blogRoot, blogComponent, { slug }, 'Blog component is not loaded');
            }

        } else if (currentPath.startsWith('/station/')) {
             const serial = currentPath.split('/').pop();
             switchView('station-detail');
             const device = globalState.devices.find(d => d.serial === serial);
             if (device && window.enterpriseDetailRoot) {
                 const stationComponent = await waitForGlobalComponent('EnterpriseStationDetail', 5000);
                 renderReactComponent(window.enterpriseDetailRoot, stationComponent, { device }, 'Station detail component is not loaded');
             }
        } else if (currentPath === '/api/docs') {
            switchView('api-docs');
            if (window.apiDocsRoot) {
                const apiDocsComponent = await waitForGlobalComponent('ApiDocs', 5000);
                renderReactComponent(window.apiDocsRoot, apiDocsComponent, {}, 'API docs component is not loaded');
            }
        }
        return; // CRITICAL: Stop here for public visitors!
    }

    await loadCurrentUser();
    if (!globalState.currentUser) return;

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
    await loadInitialDevices();
    connectWebSocket();

    // 2. Dynamic Routing Handler (Clean URLs)
    let initialView = 'dashboard';
    if (currentPath.startsWith('/station/')) {
        const serial = currentPath.split('/').pop();
        if (serial) {
            console.log("Routing to Station Detail:", serial);
            globalState.selectedDeviceSerial = serial;
            switchView('station-detail');
            
            // Wait for devices to load, then render
            const checkAndRender = setInterval(() => {
                const device = globalState.devices.find(d => d.serial === serial);
                if (device && window.enterpriseDetailRoot) {
                    waitForGlobalComponent('EnterpriseStationDetail', 5000).then((stationComponent) => {
                        renderReactComponent(window.enterpriseDetailRoot, stationComponent, { device }, 'Station detail component is not loaded');
                        clearInterval(checkAndRender);
                    });
                }
            }, 500);
            
            // Timeout after 10s
            setTimeout(() => clearInterval(checkAndRender), 10000);
        }
    } else if (currentPath === '/network-status') {
        switchView('homepage');
        waitForGlobalComponent('HomePageEnterprise', 5000).then((homeComp) => {
            if (homeComp && window.homepageRoot) {
                renderReactComponent(window.homepageRoot, homeComp, { devices: globalState.devices }, 'Homepage component is not loaded');
            }
        });
    } else if (currentPath.startsWith('/blog')) {
        switchView('blog');
        const slug = currentPath.split('/').pop() !== 'blog' ? currentPath.split('/').pop() : null;
        if (window.blogRoot) {
            waitForGlobalComponent('BlogSystem', 5000).then((blogComponent) => {
                renderReactComponent(window.blogRoot, blogComponent, { slug }, 'Blog component is not loaded');
            });
        }
        return;
    } else if (currentPath === '/api/docs') {
        switchView('api-docs');
        if (window.apiDocsRoot) {
            waitForGlobalComponent('ApiDocs', 5000).then((apiDocsComponent) => {
                renderReactComponent(window.apiDocsRoot, apiDocsComponent, {}, 'API docs component is not loaded');
            });
        }
        return;
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

console.log(`✓ App initialized with Performance Optimization (view: ${initialView})`);
});

window.openPublicMap = async function() {
    const modalEl = document.getElementById('publicMapModal');
    if (!modalEl) return;
    const modal = new bootstrap.Modal(modalEl);
    modal.show();

    if (window.publicMapInstance) {
        setTimeout(() => window.publicMapInstance.invalidateSize(), 100);
        return;
    }

    // === SHARED CONSTANTS (Sync with dashboard) ===
    const STATUS_TEXT_MAP = {
        'online': 'ONLINE', 'offline': 'OFFLINE', 'unprovisioned': 'CHƯA KÍCH HOẠT',
        'awaiting_license': 'CHỜ LICENSE', 'configuring': 'ĐANG CẤU HÌNH',
        'rebooting': 'KHỞI ĐỘNG LẠI', 'rebooting_for_reset': 'ĐANG RESET',
        'locked': 'BỊ KHÓA', 'sleep': 'CHẾ ĐỘ NGỦ', 'streaming': 'ĐANG PHÁT',
        'starting': 'ĐANG KHỞI CHẠY'
    };

    const STATUS_CLASS_MAP = {
        'online': 'bg-success', 'offline': 'bg-danger', 'unprovisioned': 'bg-warning text-dark',
        'awaiting_license': 'bg-info text-dark', 'configuring': 'bg-primary',
        'rebooting': 'bg-warning text-dark', 'rebooting_for_reset': 'bg-danger',
        'locked': 'bg-danger', 'sleep': 'bg-info', 'streaming': 'bg-success',
        'starting': 'bg-warning text-dark'
    };

    const getStatusColor = (status) => {
        switch (status) {
            case 'online': return '#198754'; case 'offline': return '#dc3545';
            case 'sleep': return '#0dcaf0'; case 'streaming': return '#198754';
            case 'starting': return '#ffc107'; case 'unprovisioned': return '#ffc107'; 
            case 'configuring': return '#0d6efd'; case 'locked': return '#dc3545';
            default: return '#6c757d';
        }
    };

    const parseBoolCompat = (v, def = false) => {
        if (v === null || v === undefined) return def;
        if (typeof v === 'boolean') return v;
        if (typeof v === 'string') {
            const s = v.trim().toLowerCase();
            if (['true', '1', 'yes', 'on'].includes(s)) return true;
            if (['false', '0', 'no', 'off'].includes(s)) return false;
        }
        return Boolean(v);
    };

    const isServerConfigured = (cfg, idx) => {
        const host = String(cfg[`serverhost${idx}`] || '').trim();
        const port = String(cfg[`port${idx}`] || '').trim();
        const mp = String(cfg[`mountpoint${idx}`] || '').trim();
        return Boolean(host && port && mp);
    };

    const getMapEffectiveStatus = (dev) => {
        const raw = String(dev?.status || '').toLowerCase();
        const stats = dev?.ntrip_stats || {};
        const rtcmIn = Number(stats.rtcm_input_bps || stats.rtcm_in_bps || 0);
        const isVirtual = String(dev?.detected_chip_type || '').toUpperCase() === 'VIRTUAL';

        if (!['online', 'streaming', 'sleep', 'starting'].includes(raw)) {
            if (isVirtual && rtcmIn > 0) {} else return 'offline';
        }

        const cfg = dev?.service_config || {};
        const s1Enabled = parseBoolCompat(cfg.server1_enabled, isServerConfigured(cfg, 1));
        const s2Enabled = parseBoolCompat(cfg.server2_enabled, isServerConfigured(cfg, 2));

        const enabledServers = []; if (s1Enabled) enabledServers.push(1); if (s2Enabled) enabledServers.push(2);

        if (enabledServers.length === 0) return (isVirtual && rtcmIn > 0) ? 'online' : 'offline';

        const s1h = String(cfg.serverhost1 || '').trim().toLowerCase();
        const s2h = String(cfg.serverhost2 || '').trim().toLowerCase();
        const pref = 'aitogy.com.vn';
        
        let primarySid = 1;
        if (s1h.includes(pref)) primarySid = 1;
        else if (s2h.includes(pref)) primarySid = 2;
        else primarySid = 1;

        const primaryBps = Number(dev?.ntrip_status?.[`server${primarySid}`]?.bps || 0);
        
        let result = 'online';
        if (primaryBps > 0) {
            result = 'online';
        } else {
            const od = parseBoolCompat(cfg[`server${primarySid}_stream_on_demand`], parseBoolCompat(cfg.stream_on_demand, false));
            const ac = od ? parseBoolCompat(cfg[`server${primarySid}_stream_active`], parseBoolCompat(cfg.stream_active, false)) : true;
            result = (!od || ac) ? 'online' : 'sleep';
        }

        if (result === 'offline' && ['online', 'streaming', 'sleep', 'starting'].includes(raw)) {
            result = 'online';
        }
        return result;
    };

    window.publicMapInstance = L.map('publicMapContainer', {
        center: [21.0285, 105.8542],
        zoom: 6,
        zoomControl: true
    });

    const googleHybrid = L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', {
        maxZoom: 20,
        attribution: 'Google Hybrid'
    });
    const googleTerrain = L.tileLayer('https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}', {
        maxZoom: 20,
        attribution: 'Google Terrain'
    });
    const googleStreets = L.tileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', {
        maxZoom: 20,
        attribution: 'Google Streets'
    });

    googleHybrid.addTo(window.publicMapInstance);

    const baseMaps = {
        "Vệ tinh": googleHybrid,
        "Địa hình": googleTerrain,
        "Đường phố": googleStreets
    };

    L.control.layers(baseMaps, null, { position: 'topright' }).addTo(window.publicMapInstance);

    try {
        const res = await fetch('/api/public/map-stations');
        if (!res.ok) throw new Error("Không tải được dữ liệu");
        const stations = await res.json();
        const bounds = [];
        
        stations.forEach(st => {
            const effectiveStatus = getMapEffectiveStatus(st);
            const color = getStatusColor(effectiveStatus);
            const label = STATUS_TEXT_MAP[effectiveStatus] || effectiveStatus.toUpperCase();
            const badgeClass = STATUS_CLASS_MAP[effectiveStatus] || 'bg-secondary';
            const isVirtual = String(st.detected_chip_type || '').toUpperCase() === 'VIRTUAL';

            const stLat = Number(st.lat);
            const stLon = Number(st.lon);

            if (isNaN(stLat) || isNaN(stLon) || stLat === 0 || stLon === 0) return;

            const icon = L.divIcon({
                className: 'custom-div-icon',
                html: `<div style="background:${color};width:14px;height:14px;border-radius:50%;border:2px solid white;box-shadow:0 0 5px rgba(0,0,0,0.5)"></div>`,
                iconSize: [14, 14],
                iconAnchor: [7, 7]
            });

            const popup = `<div style="color:#333;min-width:180px;font-family:sans-serif;">
                <strong style="font-size:1.1em;display:block;margin-bottom:5px;">${st.name}</strong>
                <div class="mb-2">
                    <span class="badge ${badgeClass}">${label}</span>
                    ${isVirtual ? '<span class="badge bg-info text-dark ms-1">VIRTUAL</span>' : ''}
                </div>
            </div>`;

            L.marker([stLat, stLon], { icon }).addTo(window.publicMapInstance).bindPopup(popup);
            bounds.push([stLat, stLon]);
        });

        if (bounds.length > 0) window.publicMapInstance.fitBounds(bounds, { padding: [50, 50] });
    } catch (err) {
        console.error("Map error:", err);
    }
};
