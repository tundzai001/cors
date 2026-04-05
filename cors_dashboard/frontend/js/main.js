// main.js 

'use strict';

// === GLOBAL STATE  ===
let globalState = {
    devices: [],
    selectedDeviceSerial: null,
    showVirtualStationForm: false,
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
        deviceListRoot.render(
            React.createElement(window.DeviceList, {
                devices: filteredDevices,
                selectedDeviceSerial: globalState.selectedDeviceSerial,
                onSelect: handleSelectDevice,
                onDelete: handleDeleteDevice,
                onOpenVirtualStationForm: handleOpenVirtualStationForm,
                searchTerm: globalState.searchTerm,
                onSearch: handleSearchChange,
                currentUser: globalState.currentUser 
            })
        );
        deviceListNeedsUpdate = false;
    }

    // Render Details Panel
    if (detailsPanelNeedsUpdate && detailsPanelRoot) {
        const selectedDevice = globalState.devices.find(d => String(d.serial) === String(globalState.selectedDeviceSerial));
        detailsPanelRoot.render(
            React.createElement(window.DetailsPanel, {
                device: selectedDevice,
                nmeaData: currentNmeaData,
                currentUser: globalState.currentUser,
                showVirtualStationForm: globalState.showVirtualStationForm,
                onReset: handleResetDevice,
                onDeployLicense: handleDeployLicense,
                onProvision: handleProvisionDevice,
                onConfigureChip: handleConfigureChip,
                onStopAutoBase: handleStopAutoBase,
                onApplyAutoProvince: handleApplyAutoProvince,
                onConfigureService: handleConfigureService,
                onCreateVirtualStation: handleCreateVirtualStation,
                onCloseVirtualStationForm: handleCloseVirtualStationForm,
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

window.switchView = function(viewName) {
    globalState.currentView = viewName;
    
    const navDashboard = document.getElementById('nav-dashboard');
    const navMap = document.getElementById('nav-map');

    if (navDashboard) {
        navDashboard.classList.toggle('active', viewName === 'dashboard');
    }
    
    if (navMap) {
        navMap.classList.toggle('active', viewName === 'map');
    }

    const dashboardDiv = document.getElementById('view-dashboard');
    const mapDiv = document.getElementById('view-map');

    if (viewName === 'dashboard') {
        if (dashboardDiv) dashboardDiv.classList.remove('d-none');
        if (mapDiv) mapDiv.classList.add('d-none');
        
        deviceListNeedsUpdate = true;
        detailsPanelNeedsUpdate = true;
        renderDashboard();
    } else {
        if (dashboardDiv) dashboardDiv.classList.add('d-none');
        if (mapDiv) mapDiv.classList.remove('d-none');
        
        mapNeedsUpdate = true;
        renderMap();
    }
    
    setTimeout(() => {
        window.dispatchEvent(new Event('resize'));
    }, 100);
};

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
            if (!newDevice.detected_chip_type || newDevice.detected_chip_type === 'UNKNOWN') {
                if (existingDevice.detected_chip_type && existingDevice.detected_chip_type !== 'UNKNOWN') {
                    newDevice.detected_chip_type = existingDevice.detected_chip_type;
                }
            }
            // Only fallback when key is truly missing in partial updates.
            // If backend explicitly sends {}, we must respect it (e.g. reset flow).
            if (!hasOwn(newDevice, 'base_config') && hasData(existingDevice.base_config)) {
                newDevice.base_config = existingDevice.base_config;
            }
            if (!hasOwn(newDevice, 'service_config') && hasData(existingDevice.service_config)) {
                newDevice.service_config = existingDevice.service_config;
            }
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
    globalState.showVirtualStationForm = false;
    currentNmeaData = { gga: null, gsa: null, satellites: {} };
    
    detailsPanelNeedsUpdate = true;
    scheduleRender('high');
}

function handleOpenVirtualStationForm() {
    globalState.selectedDeviceSerial = null;
    globalState.showVirtualStationForm = true;
    currentNmeaData = { gga: null, gsa: null, satellites: {} };
    detailsPanelNeedsUpdate = true;
    scheduleRender('high');
}

function handleCloseVirtualStationForm() {
    globalState.showVirtualStationForm = false;
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
    } catch(e) { 
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
    } catch(e) { 
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

    } catch(e) { 
        showError(`Lỗi cấu hình dịch vụ: ${e.message}`); 
        throw e;
    }
}

async function handleCreateVirtualStation(payload) {
    try {
        const response = await apiFetch('/api/admin/virtual-stations', {
            method: 'POST',
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Không thể tạo trạm ảo');
        }

        const device = await response.json();
        updateGlobalDevices(device);
        globalState.showVirtualStationForm = false;
        globalState.selectedDeviceSerial = device.serial;
        detailsPanelNeedsUpdate = true;
        showSuccess(`Đã tạo trạm ảo: ${device.name}`);
        return device;
    } catch (e) {
        showError(`Lỗi tạo trạm ảo: ${e.message}`);
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
    const wsUrl = `${protocol}//${window.location.host}/ws/updates`;
    
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
        // --- LOGIC CHẶN TRẠM LẠ CHO COORDINATOR ---
        if (globalState.currentUser?.role === 'coordinator') {
            
            // Trường hợp 1: Admin vừa gán/thu hồi quyền (Bản tin có chứa user_id)
            if (data.user_id !== undefined && data.user_id !== null) {
                // Nếu user_id không phải của mình -> Xóa khỏi list (nếu đang hiển thị) và Chặn
                if (data.user_id !== globalState.currentUser.id) {
                    const exists = globalState.devices.some(d => d.serial === statusSerial);
                    if (exists) {
                        console.log(`Removed unassigned device: ${statusSerial}`);
                        removeDeviceLocal(statusSerial);
                    }
                    return; // Dừng lại, không update
                }
            } 
            
            // Trường hợp 2: Trạm gửi Heartbeat/Status (Bản tin KHÔNG có user_id)
            else {
                // Kiểm tra xem trạm này có nằm trong danh sách "của mình" (đã load từ API ban đầu) hay không
                const isMyDevice = globalState.devices.some(d => d.serial === statusSerial);
                
                // Nếu trạm này chưa từng tồn tại trong danh sách của mình -> Đây là trạm của người khác -> Bỏ qua
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
        
        if (nmeaType === 'GGA') {
            currentNmeaData.gga = data;
            requestDetailsPanelUpdate('high');
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
    const token = localStorage.getItem('access_token');
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    try {
        const response = await fetch(url, { ...options, headers, signal: controller.signal });
        clearTimeout(timeoutId);

        if (response.status === 401) {
            localStorage.removeItem('access_token');
            window.location.href = '/login.html';
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

window.logActivity = function(type, message, icon) {
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
        window.location.href = '/login.html';
    }
}

async function loadInitialDevices() {
    try {
        let apiUrl = '/api/devices';
        if (globalState.currentUser?.role === 'coordinator' || globalState.currentUser?.role === 'operator') {
            apiUrl = `/api/devices?user_id=${globalState.currentUser.id}`;
        }
        
        const response = await apiFetch(apiUrl);
        if (!response.ok) throw new Error('Failed to load devices');
        
        const devicesFromApi = await response.json();
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
    if (!localStorage.getItem('access_token')) {
        window.location.href = '/login.html';
        return;
    }

    await loadCurrentUser();
    if (!globalState.currentUser) return;

    setupUIbasedOnPermissions();
    setupEventListeners();

    deviceListRoot = ReactDOM.createRoot(document.getElementById('device-list-root'));
    detailsPanelRoot = ReactDOM.createRoot(document.getElementById('details-panel-root'));
    
    const mapContainer = document.getElementById('map-root');
    if (mapContainer) {
        mapRoot = ReactDOM.createRoot(mapContainer);
    }

    const modalContainer = document.createElement('div');
    document.body.appendChild(modalContainer);
    const modalRoot = ReactDOM.createRoot(modalContainer);
    
    if (window.CasterConfigModal) {
        modalRoot.render(React.createElement(window.CasterConfigModal));
    }
    

    await loadInitialDevices();
    connectWebSocket();
    
    // Nếu có tham số view trong URL, chuyển view tự động (giữ nút Bản đồ luôn active)
    let initialView = 'dashboard';
    if (window.location.search.includes('view=map')) {
        initialView = 'map';
        switchView('map');
    }

    console.log(`✓ App initialized with Performance Optimization (view: ${initialView})`);
});

