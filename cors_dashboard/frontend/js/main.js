// main.js 

'use strict';

// === GLOBAL STATE  ===
let globalState = {
    devices: [],
    selectedDeviceSerial: null,
    currentUser: null,
    searchTerm: '',
    currentView: 'dashboard'
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
const RENDER_INTERVAL = 500; 

let lastMapRenderTime = 0;
const MAP_RENDER_INTERVAL = 2000; 

let dashboardRafId = null;
let mapRafId = null;
let dashboardTimerId = null;
let mapTimerId = null;


let deviceListNeedsUpdate = false;
let detailsPanelNeedsUpdate = false;
let mapNeedsUpdate = false;

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

function getFilteredDevices() {
    if (globalState.searchTerm !== lastSearchTerm) {
        const term = globalState.searchTerm.toLowerCase();

        const statusPriority = {
            'awaiting_license': 1,
            'configuring': 2,
            'unprovisioned': 3,
            'rebooting_for_reset': 4,
            'rebooting': 5,
            'locked': 20,
            'online': 100,
            'offline': 200
        };
        
        cachedFilteredDevices = globalState.devices
            .filter(d => 
                (d.name && d.name.toLowerCase().includes(term)) || 
                (d.serial && d.serial.toLowerCase().includes(term))
            )
            .sort((a, b) => {
                const priorityA = statusPriority[a.status] || 50;
                const priorityB = statusPriority[b.status] || 50;
                if (priorityA !== priorityB) {
                    return priorityA - priorityB;
                }
                const nameA = (a.name || "").toLowerCase();
                const nameB = (b.name || "").toLowerCase();
                return nameA.localeCompare(nameB);
            });
            
        lastSearchTerm = globalState.searchTerm;
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
                onReset: handleResetDevice,
                onDeployLicense: handleDeployLicense,
                onProvision: handleProvisionDevice,
                onConfigureChip: handleConfigureChip,
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
    
    let hasChanges = false;

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
            if (!hasData(newDevice.base_config) && hasData(existingDevice.base_config)) {
                newDevice.base_config = existingDevice.base_config;
            }
            if (!hasData(newDevice.service_config) && hasData(existingDevice.service_config)) {
                newDevice.service_config = existingDevice.service_config;
            }
            if (typeof newDevice.is_provisioned === 'undefined' && typeof existingDevice.is_provisioned !== 'undefined') {
                newDevice.is_provisioned = existingDevice.is_provisioned;
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
                newDevice.ntrip_connected = newDevice.bps > 0;
            }
            else if (existingDevice && existingDevice.ntrip_status && existingDevice.bps > 0) {
                newDevice.ntrip_stats = existingDevice.ntrip_stats;
                newDevice.ntrip_status = existingDevice.ntrip_status;
                newDevice.bps = existingDevice.bps;
                newDevice.ntrip_connected = existingDevice.ntrip_connected;
            }
            else if (!newDevice.ntrip_status) {
                newDevice.ntrip_status = {
                    server1: { connected: false, bps: 0 },
                    server2: { connected: false, bps: 0 }
                };
                newDevice.bps = 0;
            }
        }
        
        // ✅ XỬ LÝ is_synced: Mặc định là true nếu backend không gửi
        if (typeof newDevice.is_synced === 'undefined') {
            newDevice.is_synced = true;
        }

        if (existingDevice) {
            const mergedDevice = {
                ...existingDevice,
                ...newDevice,
                ntrip_status: newDevice.ntrip_status || existingDevice.ntrip_status,
                last_update: Date.now() 
            };
            
            if (JSON.stringify(existingDevice) !== JSON.stringify(mergedDevice)) {
                deviceMap.set(newDevice.serial, mergedDevice);
                hasChanges = true;
            }
        } else {
            deviceMap.set(newDevice.serial, newDevice);
            hasChanges = true;
        }
    });

    if (hasChanges) {
        globalState.devices = Array.from(deviceMap.values());
        lastSearchTerm = null;
        
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
        // --- LOGIC CHẶN TRẠM LẠ CHO COORDINATOR ---
        if (globalState.currentUser?.role === 'coordinator') {
            
            // Trường hợp 1: Admin vừa gán/thu hồi quyền (Bản tin có chứa user_id)
            if (data.user_id !== undefined && data.user_id !== null) {
                // Nếu user_id không phải của mình -> Xóa khỏi list (nếu đang hiển thị) và Chặn
                if (data.user_id !== globalState.currentUser.id) {
                    const exists = globalState.devices.some(d => d.serial === data.serial);
                    if (exists) {
                        console.log(`Removed unassigned device: ${data.serial}`);
                        removeDeviceLocal(data.serial);
                    }
                    return; // Dừng lại, không update
                }
            } 
            
            // Trường hợp 2: Trạm gửi Heartbeat/Status (Bản tin KHÔNG có user_id)
            else {
                // Kiểm tra xem trạm này có nằm trong danh sách "của mình" (đã load từ API ban đầu) hay không
                const isMyDevice = globalState.devices.some(d => d.serial === data.serial);
                
                // Nếu trạm này chưa từng tồn tại trong danh sách của mình -> Đây là trạm của người khác -> Bỏ qua
                if (!isMyDevice) {
                    return;
                }
            }
        }
        
        updateGlobalDevices(data);
        
        if (serial === globalState.selectedDeviceSerial && data.base_coords) {
            currentNmeaData.base_coords = data.base_coords;
            detailsPanelNeedsUpdate = true;
            scheduleRender('high');
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
            visitorEl.textContent = count;
            visitorEl.style.color = '#ffffff';
            visitorEl.style.textShadow = '0 0 15px #ffffff';
            setTimeout(() => {
                visitorEl.style.color = '#ff00ff';
                visitorEl.style.textShadow = 'none';
            }, 300);
        }
        return;
    }
    
    if (type === 'nmea_update' && serial === globalState.selectedDeviceSerial) {
        const nmeaType = data.type;
        
        if (nmeaType === 'GGA') {
            currentNmeaData.gga = data;
            detailsPanelNeedsUpdate = true;
            scheduleRender('high');
        } 
        else if (nmeaType === 'GSA') {
            currentNmeaData.gsa = data;
            if (data.active_sats && currentNmeaData.satellites) {
                Object.values(currentNmeaData.satellites).forEach(sat => {
                    sat.isTracking = data.active_sats.includes(sat.prn);
                });
            }
            detailsPanelNeedsUpdate = true;
            scheduleRender('high');
        } 
        else if (nmeaType === 'GSV' && data.satellites) {
            data.satellites.forEach(sat => {
                currentNmeaData.satellites[sat.prn] = {
                    ...currentNmeaData.satellites[sat.prn],
                    ...sat,
                    lastSeen: Date.now()
                };
            });
            detailsPanelNeedsUpdate = true;
            scheduleRender('high');
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
    

    connectWebSocket();
    await loadInitialDevices();

    console.log('✓ App initialized with Performance Optimization');
});