// ==============================================================================
// main.js - v5.1.0 - PHIÊN BẢN REACT HOÀN CHỈNH
// ==============================================================================
// - Đóng vai trò là "Controller": Quản lý state, xử lý sự kiện, giao tiếp API.
// - Không thao tác DOM trực tiếp, chỉ ra lệnh cho React render lại giao diện.

'use strict';

// === GLOBAL STATE - NGUỒN CHÂN LÝ DUY NHẤT ===
let globalState = {
    devices: [],
    selectedDeviceSerial: null,
    currentUser: null,
};

// === REACT ROOTS ===
let deviceListRoot = null;
let detailsPanelRoot = null;

// === WEBSOCKET ===
let ws = null;
let wsReconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 10;

// === DASHBOARD STATE & LOGIC  ===
let currentNmeaData = { gga: null, gsa: null, satellites: {} };
let skyplotBgCanvas, skyplotBgCtx;
let skyplotSatCanvas, skyplotSatCtx;
let animationFrameId = null;
// Dán toàn bộ các hàm vẽ dashboard từ file cũ vào đây nếu cần,
// hoặc gọi chúng từ component React như đã làm trong react-components.js

// === HÀM RENDER TRUNG TÂM - CẦU NỐI VỚI REACT ===
function renderApp() {
    if (!deviceListRoot || !detailsPanelRoot) {
        console.error("React roots not initialized!");
        return;
    }

    // 1. Render danh sách thiết bị
    deviceListRoot.render(
        React.createElement(window.DeviceList, {
            devices: globalState.devices,
            selectedDeviceSerial: globalState.selectedDeviceSerial,
            onSelect: handleSelectDevice,
            onDelete: handleDeleteDevice
        })
    );

    // 2. Render panel chi tiết
    const selectedDevice = globalState.devices.find(d => d.serial === globalState.selectedDeviceSerial);
    detailsPanelRoot.render(
        React.createElement(window.DetailsPanel, {
            device: selectedDevice,
            nmeaData: currentNmeaData,
            onReset: handleResetDevice,
            onDeployLicense: handleDeployLicense,
            onProvision: handleProvisionDevice,
            onConfigureChip: handleConfigureChip,
            onConfigureService: handleConfigureService,
            onLock: handleLockDevice,
            onUnlock: handleUnlockDevice
        })
    );

    // 3. Cập nhật các phần tử HTML bên ngoài React
    const deviceCount = document.getElementById('device-count');
    if (deviceCount) deviceCount.textContent = globalState.devices.length;

    if (globalState.currentUser) {
        const userInfoEl = document.getElementById('user-info');
        if (userInfoEl) {
            userInfoEl.textContent = `${globalState.currentUser.full_name || globalState.currentUser.username} (${globalState.currentUser.role.toUpperCase()})`;
        }
    }
}

/**
 * Hàm duy nhất để cập nhật state thiết bị một cách an toàn và bất biến.
 * Xử lý việc hợp nhất, thêm, sửa, xóa để chống race condition.
 * @param {Array<Object> | Object} newDeviceData - Dữ liệu mới, có thể là một mảng hoặc một đối tượng.
 */
function updateGlobalDevices(newDeviceData) {
    // Sử dụng Map để đảm bảo không bao giờ có serial trùng lặp
    const deviceMap = new Map(globalState.devices.map(d => [d.serial, d]));

    const devicesToAddOrUpdate = Array.isArray(newDeviceData) ? newDeviceData : [newDeviceData];

    devicesToAddOrUpdate.forEach(device => {
        if (device && device.serial) {
            deviceMap.set(device.serial, device);
        }
    });

    // Chuyển Map trở lại thành mảng và cập nhật state
    globalState.devices = Array.from(deviceMap.values());
}
// ===============================================================================
// === EVENT HANDLERS (Được truyền xuống cho React Components qua props)       ===
// ===============================================================================

function handleSelectDevice(serial) {
    globalState.selectedDeviceSerial = serial;
    currentNmeaData = { gga: null, gsa: null, satellites: {} };
    renderApp();
}

async function handleDeleteDevice(serial, name) {
    const confirmMsg = `⚠️ XÓA KHỎI DANH SÁCH\n\nBạn có chắc muốn xóa trạm '${name}' khỏi giao diện quản lý không?\n\nLưu ý: Hành động này không reset Pi.`;
    if (!confirm(confirmMsg)) return;

    try {
        const response = await apiFetch(`/api/devices/${serial}`, { method: 'DELETE' });

        if (response.ok) {
            // Logic này đã đúng, nhưng chúng ta sẽ làm nó tường minh hơn
            const initialLength = globalState.devices.length;
            
            // Dùng .filter() để tạo mảng mới không chứa thiết bị đã xóa
            globalState.devices = globalState.devices.filter(d => d.serial !== serial);
            
            if (globalState.selectedDeviceSerial === serial) {
                globalState.selectedDeviceSerial = null;
            }
            
            // Chỉ render và thông báo nếu thực sự có thay đổi
            if (globalState.devices.length < initialLength) {
                renderApp();
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
        throw error; // Ném lỗi để component React có thể xử lý (vd: bật lại nút)
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
        await apiFetch(`/api/devices/${serial}/command`, { 
            method: 'POST', 
            body: JSON.stringify({ command: 'DEPLOY_SERVICE_CONFIG', payload: payload }) 
        });
        showSuccess('Đã gửi cấu hình dịch vụ! Giao diện sẽ tự cập nhật.');
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

// Tệp: main.js

function connectWebSocket() {
    // Xác định URL của WebSocket dựa trên URL hiện tại của trang web
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/updates`;
    
    // Tạo một đối tượng WebSocket mới
    ws = new WebSocket(wsUrl);
    
    /**
     * Sự kiện được kích hoạt khi kết nối được thiết lập thành công.
     */
    ws.onopen = () => {
        updateConnectionStatus(true); // Cập nhật icon trạng thái kết nối
        wsReconnectAttempts = 0;      // Reset bộ đếm số lần kết nối lại khi đã thành công
        console.log('✓ WebSocket connected');
    };
    
    /**
     * Sự kiện được kích hoạt mỗi khi có một tin nhắn mới từ server.
     */
    ws.onmessage = (event) => {
        try {
            // Phân tích chuỗi JSON nhận được thành đối tượng JavaScript
            const message = JSON.parse(event.data);
            // Gọi hàm trung tâm để xử lý tin nhắn
            handleWebSocketMessage(message);
        } catch (e) {
            console.error('Error parsing WebSocket message:', e, 'Data:', event.data);
        }
    };
    
    /**
     * Sự kiện được kích hoạt khi có lỗi xảy ra với kết nối.
     */
    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        updateConnectionStatus(false); // Cập nhật icon báo lỗi
    };
    
    /**
     * Sự kiện được kích hoạt khi kết nối bị đóng (dù là chủ động hay bị động).
     * Đây là nơi chứa logic tự động kết nối lại.
     */
    ws.onclose = () => {
        updateConnectionStatus(false); // Cập nhật icon báo mất kết nối
        
        // Chỉ cố gắng kết nối lại nếu số lần thử chưa vượt quá giới hạn
        if (wsReconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            // Tính toán thời gian chờ trước khi kết nối lại.
            // Thời gian sẽ tăng dần: 1s, 2s, 4s, 8s, 16s, ... tối đa 30s.
            const delay = Math.min(1000 * Math.pow(2, wsReconnectAttempts), 30000);
            
            wsReconnectAttempts++; // Tăng bộ đếm
            
            console.log(`WebSocket disconnected. Reconnecting in ${delay / 1000}s (attempt ${wsReconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`);
            
            // Lên lịch để gọi lại chính hàm connectWebSocket sau một khoảng thời gian chờ
            setTimeout(connectWebSocket, delay);
        } else {
            // Nếu đã thử quá nhiều lần mà vẫn thất bại, thông báo cho người dùng
            showError('Không thể kết nối đến server. Vui lòng tải lại trang.');
        }
    };
}

function handleWebSocketMessage(message) {
    const { type, data, serial } = message;
    let needsFullRender = false;
    // --- Xử lý cập nhật trạng thái ---
    if (type === 'status_update') {
        updateGlobalDevices(data); // <-- Chỉ cần gọi hàm hợp nhất
        renderApp();
    }
    
    // --- Xử lý xóa thiết bị ---
    if (type === 'device_deleted') {
        const initialLength = globalState.devices.length;
        globalState.devices = globalState.devices.filter(d => d.serial !== serial);
        
        if (globalState.selectedDeviceSerial === serial) {
            globalState.selectedDeviceSerial = null;
        }
        
        if (globalState.devices.length < initialLength) {
            renderApp();
            showSuccess('Một trạm đã được xóa khỏi danh sách.');
        }
    }
    // --- Xử lý dữ liệu NMEA ---
    // Chỉ xử lý nếu tin nhắn NMEA dành cho thiết bị đang được chọn.
    if (type === 'nmea_update' && serial === globalState.selectedDeviceSerial) {
        // Cập nhật dữ liệu NMEA vào biến toàn cục 'currentNmeaData'.
        // Component Dashboard sẽ đọc từ biến này.
        if (data.type === 'GGA') {
            currentNmeaData.gga = data;
        } else if (data.type === 'GSA' && data.active_sats) {
            currentNmeaData.gsa = data;
            Object.values(currentNmeaData.satellites).forEach(sat => {
                sat.isTracking = data.active_sats.includes(sat.prn);
            });
        } else if (data.type === 'GSV' && data.satellites) {
            data.satellites.forEach(sat => {
                const existingSat = currentNmeaData.satellites[sat.prn] || {};
                currentNmeaData.satellites[sat.prn] = { ...existingSat, ...sat, lastSeen: Date.now() };
            });
        }
        
        // Quan trọng: Vì dữ liệu NMEA thay đổi, chúng ta cũng cần render lại
        // để truyền prop 'nmeaData' mới xuống cho React.
        needsFullRender = true;
    }

    // === BƯỚC CUỐI CÙNG: RA LỆNH CHO REACT RENDER LẠI ===
    // Nếu có bất kỳ thay đổi nào ở trên, gọi hàm render trung tâm.
    if (needsFullRender) {
        renderApp();
    }
}

// ===============================================================================
// === INITIALIZATION & UTILITIES                                              ===
// ===============================================================================

/**
 * Hàm giao tiếp API trung tâm, tự động xử lý token, timeout và lỗi 401.
 */
async function apiFetch(url, options = {}, retries = 3) {
    const token = localStorage.getItem('access_token');
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000); // 30s timeout

    try {
        const response = await fetch(url, {
            ...options,
            headers,
            signal: controller.signal
        });

        clearTimeout(timeoutId);

        if (response.status === 401) {
            console.error('Authentication error. Redirecting to login.');
            localStorage.removeItem('access_token');
            window.location.href = '/login.html';
            throw new Error('Unauthorized');
        }

        if (response.status >= 500 && retries > 0) {
            console.warn(`Server error (${response.status}). Retrying... (${retries} attempts left)`);
            await new Promise(resolve => setTimeout(resolve, 1000));
            return apiFetch(url, options, retries - 1);
        }

        return response;
    } catch (error) {
        clearTimeout(timeoutId);

        if (error.name === 'AbortError') {
            console.error('Request timeout');
            if (retries > 0) {
                return apiFetch(url, options, retries - 1);
            }
            throw new Error('Request timeout');
        }

        console.error('API Fetch Error:', error);
        updateConnectionStatus(false);
        throw error;
    }
}

/**
 * Hiển thị thông báo toast của Bootstrap.
 */
function showToast(message, type) {
    const toastContainer = document.getElementById('toast-container') || createToastContainer();
    const toastEl = document.createElement('div');
    toastEl.className = `toast align-items-center bg-${type} text-white border-0`;
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');

    toastEl.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;

    toastContainer.appendChild(toastEl);
    const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

/**
 * Tạo container cho toast nếu chưa tồn tại.
 */
function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container position-fixed top-0 end-0 p-3';
    container.style.zIndex = 1100;
    document.body.appendChild(container);
    return container;
}

// Các hàm showSuccess và showError để tiện sử dụng
function showSuccess(message) { showToast(message, 'success'); }
function showError(message) {
    console.error('Error Displayed:', message);
    const userMessage = (typeof message === 'string' && (message.includes('NetworkError') || message.includes('fetch')))
        ? 'Lỗi kết nối mạng. Vui lòng kiểm tra internet.'
        : message;
    showToast(userMessage, 'danger');
}


/**
 * Cập nhật icon trạng thái kết nối WebSocket trên Navbar.
 */
function updateConnectionStatus(connected) {
    const statusIndicator = document.getElementById('connection-status');
    if (!statusIndicator) return;
    statusIndicator.className = `navbar-text me-3 fs-5 ${connected ? 'text-success' : 'text-danger'}`;
    statusIndicator.innerHTML = connected ? '<i class="bi bi-reception-4"></i>' : '<i class="bi bi-reception-0"></i>';
    statusIndicator.title = connected ? 'Đã kết nối' : 'Mất kết nối';
}

/**
 * Kiểm tra quyền của người dùng hiện tại.
 */
function hasPermission(permission) {
    return globalState.currentUser?.permissions?.includes(permission);
}

/**
 * Lấy thông tin người dùng hiện tại và cập nhật globalState.
 */
async function loadCurrentUser() {
    try {
        const response = await apiFetch('/api/auth/me');
        if (response.ok) {
            globalState.currentUser = await response.json();
        } else {
            throw new Error('Failed to load user info');
        }
    } catch (error) {
        console.error('Error in loadCurrentUser:', error);
        // Chuyển về trang đăng nhập nếu không lấy được thông tin user
        localStorage.removeItem('access_token');
        window.location.href = '/login.html';
    }
}

/**
 * Tải danh sách thiết bị ban đầu, cập nhật globalState và gọi renderApp() lần đầu.
 */
async function loadInitialDevices() {
    try {
        const response = await apiFetch('/api/devices');
        if (!response.ok) throw new Error('Failed to load devices');
        
        const devicesFromApi = await response.json();
        
        // Gọi hàm hợp nhất để cập nhật state một cách an toàn
        updateGlobalDevices(devicesFromApi);
        
        // Render lại giao diện với dữ liệu đã được hợp nhất
        renderApp();
    } catch (error) {
        if (error.message !== 'Unauthorized') {
            showError('Không thể tải danh sách trạm');
        }
    }
}

/**
 * Cài đặt các thành phần UI tĩnh (ngoài React) dựa trên quyền của user.
 */
function setupUIbasedOnPermissions() {
    if (hasPermission('manage:users')) {
        const mainNav = document.getElementById('main-nav');
        if (mainNav) {
            const userManagementLink = document.createElement('li');
            userManagementLink.className = 'nav-item';
            userManagementLink.innerHTML = '<a class="nav-link" href="/users.html">Quản lý User</a>';
            mainNav.appendChild(userManagementLink);
        }
    }
    
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
}

/**
 * Gắn các sự kiện cho các nút tĩnh (ngoài React) như Đăng xuất, Export CSV.
 */
function setupEventListeners() {
    // Nút Đăng xuất
    document.getElementById('logout-btn')?.addEventListener('click', () => {
        localStorage.removeItem('access_token');
        window.location.href = '/login.html';
    });
    
    // Nút Export CSV
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

    // Khởi tạo React Roots
    deviceListRoot = ReactDOM.createRoot(document.getElementById('device-list-root'));
    detailsPanelRoot = ReactDOM.createRoot(document.getElementById('details-panel-root'));

    connectWebSocket();

    await loadInitialDevices(); // Hàm này sẽ gọi renderApp() lần đầu tiên

    console.log('✓ App initialized with React');
});