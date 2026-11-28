// rovers-global.js

document.addEventListener('DOMContentLoaded', async () => {
    const authenticatedUser = await window.ApiClient.requireAuthenticatedPage();
    if (!authenticatedUser) {
        return;
    }
    // 1. Kiểm tra Token (Hỗ trợ cả Local và Session Storage)
    const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
    if (!token) {
        if (typeof NavigationManager !== 'undefined') {
            NavigationManager.forceLogout();
        } else {
            localStorage.removeItem('access_token');
            sessionStorage.removeItem('access_token');
            window.location.href = '/';
        }
        return;
    }

    const API_HEADERS = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };

    const apiFetchRaw = (url, options = {}) => window.ApiClient.apiFetch(url, options);

    let currentUser = null;
    let allRovers = [];
    let allDevices = [];
    let casterUsers = [];  // Danh sách user từ Caster
    let casterStats = {};  // Stats từ API
    let globalAppVersion = null;
    let availableCasters = [];
    let casterSourcesCache = null;
    let syncInProgress = false;
    let mountpointsRefreshInProgress = false;
    let currentRenderedConnectionKeys = new Set();
    let latestMergedRovers = [];
    let showExpiringSoonOnly = false;
    const selectedRoverKeys = new Set();
    const connectionClockCache = new Map();
    const expiryDeadlineCache = new Map();
    let roverActivityDaily = [];
    let roverActivityMachines = [];
    let roverActivitySelectedDate = '';
    let roverActivitySelectedMachineKey = '';
    let roverActivityAccountOptionIndex = -1;
    let roverActivityMap = null;
    let roverActivityDailyRequest = 0;
    let roverActivityDayRequest = 0;
    let roverActivityTrackRequest = 0;
    const roverHistorySessionMaps = new Map();
    let roverHistoryRequest = 0;
    const roverFixQualityLabels = {
        0: '3D', 1: '3D', 2: '3D', 3: '3D', 4: 'Fixed',
        5: 'Float', 6: 'Ước tính', 7: 'Nhập thủ công', 8: 'Mô phỏng'
    };
    const roverFixQualityLabel = quality => {
        if (quality == null || quality === '') return 'Chưa có dữ liệu';
        const value = Number(quality);
        return Number.isFinite(value) ? roverFixQualityLabels[value] || `Fix ${value}` : 'Chưa có dữ liệu';
    };
    const addMapTiles = (leaflet, map) => leaflet.tileLayer(
        'https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
        { maxZoom: 20, attribution: '&copy; Google' }
    ).addTo(map);

    const hasRoverPageAccess = () => (
        String(currentUser?.role || '').toLowerCase() === 'admin'
        || currentUser?.can_view_rovers === true
        || currentUser?.permissions?.includes('view:rovers')
    );
    const hasRoverEditAccess = () => (
        String(currentUser?.role || '').toLowerCase() === 'admin'
        || currentUser?.permissions?.includes('edit:rovers')
        || (hasRoverPageAccess() && currentUser?.can_create_rovers === true)
    );

    // === 2. AUTH & PERMISSIONS CHECK ===
    try {
        const userRes = await apiFetchRaw('/api/auth/me', { headers: API_HEADERS });
        if (!userRes.ok) {
            if (userRes.status === 401 || userRes.status === 403) {
                throw new Error("Unauthorized");
            }
            throw new Error(`Server error: ${userRes.status}`);
        }
        currentUser = await userRes.json();

        // Cập nhật UI Navbar
        if(document.getElementById('user-info-name')) document.getElementById('user-info-name').textContent = currentUser.username;
        if(document.getElementById('user-info-role')) document.getElementById('user-info-role').textContent = currentUser.role.toUpperCase();
        if(document.getElementById('user-avatar-text')) document.getElementById('user-avatar-text').textContent = currentUser.username.charAt(0).toUpperCase();

        if (currentUser.permissions?.includes('manage:caster')) {
            const casterRoverTab = document.getElementById('caster-rover-tab-li');
            if (casterRoverTab) casterRoverTab.classList.remove('d-none');
        }

        // Mountpoints tab is strictly admin-only.
        if (String(currentUser.role || '').toLowerCase() === 'admin') {
            const mountpointsTab = document.getElementById('tab-mountpoints-li');
            if (mountpointsTab) mountpointsTab.classList.remove('d-none');
            
            document.querySelectorAll('.admin-only-field').forEach(el => el.classList.remove('d-none'));
        }

        // Chỉ tài khoản có quyền tạo/sửa mới thấy các công cụ ghi dữ liệu.
        if (!hasRoverEditAccess()) {
            document.querySelectorAll('[data-bs-target="#createRoverModal"]').forEach(el => el.classList.add('d-none'));
            const bulkToolbar = document.getElementById('rover-bulk-toolbar');
            if (bulkToolbar) bulkToolbar.classList.add('d-none');
            const bulkToolbarMobile = document.getElementById('rover-bulk-toolbar-mobile');
            if (bulkToolbarMobile) bulkToolbarMobile.classList.add('d-none');
        }

        initData().catch(error => console.error('initData failed:', error));

    } catch (e) {
        console.error("Initialization error:", e);
        if (e.message === "Unauthorized") {
            if (typeof NavigationManager !== 'undefined') {
                NavigationManager.forceLogout();
            } else {
                localStorage.removeItem('access_token');
                sessionStorage.removeItem('access_token');
                window.location.href = '/';
            }
        } else {
            // Hiển thị thông báo lỗi thay vì văng ra login nếu chỉ là lỗi mạng/server
            if (typeof showToast === 'function') {
                showToast("Lỗi kết nối hệ thống. Vui lòng tải lại trang.", "danger");
            } else {
                window.CorsUI?.showToast?.('danger', 'Cannot initialize rover data. Reload the page.');
            }
        }
    }

    function syncExpiryFields(prefix) {
        const custom = document.getElementById(`${prefix}-expiry`).value === 'custom';
        document.getElementById(`${prefix}-expiry-custom`).classList.toggle('d-none', !custom);
        document.getElementById(`${prefix}-expiry-count`).disabled = !custom;
        document.getElementById(`${prefix}-expiry-unit`).disabled = !custom;
    }

    function readExpiryDays(prefix) {
        const value = document.getElementById(`${prefix}-expiry`).value;
        if (value !== 'custom') return value === '' ? null : Number(value);
        const countInput = document.getElementById(`${prefix}-expiry-count`);
        const count = Number(countInput.value);
        const unit = Number(document.getElementById(`${prefix}-expiry-unit`).value);
        const days = count * unit;
        if (!Number.isSafeInteger(count) || count < 1 || ![1, 7, 30, 365].includes(unit)
            || !Number.isSafeInteger(days) || !Number.isSafeInteger(days * 86400)) {
            countInput.setCustomValidity('Nhập số nguyên dương hợp lệ cho thời hạn.');
            countInput.reportValidity();
            return undefined;
        }
        return days;
    }

    for (const prefix of ['new', 'edit']) {
        document.getElementById(`${prefix}-expiry`).addEventListener('change', () => syncExpiryFields(prefix));
        document.getElementById(`${prefix}-expiry-count`).addEventListener('input', event => event.target.setCustomValidity(''));
        document.getElementById(`${prefix}-expiry-unit`).addEventListener('change', () => {
            document.getElementById(`${prefix}-expiry-count`).setCustomValidity('');
        });
        syncExpiryFields(prefix);
    }

    // === 3. DATA LOADING ===
    async function initData() {
        await fetchCasterConfig(); // Load available servers first
        await fetchDevices();
        
        // Parallel load DB and Caster data
        try {
            const results = await Promise.allSettled([
                fetchRovers({ render: false }),
                fetchCasterUsers({ render: false })
            ]);
            if (currentUser?.role === 'admin') {
                await fetchCasterSources({ force: true });
            }
            renderTable();
            applyRoverQueryFilter();
            if (String(currentUser?.role || '').toLowerCase() === 'admin') {
                renderAdminLiveMountpoints();
            } else if (currentUser?.extra_mountpoints) {
                 renderCoordinatorExtraMountpoints();
            } else {
                document.getElementById('admin-live-mountpoints-box')?.classList.add('d-none');
            }
            
            // If admin, show stats and info
            if (currentUser.permissions?.includes('manage:caster')) {
                document.getElementById('admin-stats-container')?.classList.remove('d-none');
                document.getElementById('admin-info-alert')?.classList.remove('d-none');
            }
            
            // Periodic sync (every 5s for smoother status updates)
            setInterval(syncData, 5000);
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) syncData().catch(error => console.error('syncData failed:', error));
            });

            // Live-tick for connection durations (every 1s)
            setInterval(updateLiveClocks, 1000);
            
        } catch (e) {
            console.error("Init data error", e);
        }
    }

    function isAnyRoverModalOpen() {
        const modalIds = ['editRoverModal', 'createRoverModal', 'blockUserModal'];
        return modalIds.some(id => document.getElementById(id)?.classList.contains('show'));
    }

    function applyRoverQueryFilter() {
        const params = new URLSearchParams(window.location.search);
        const rover = params.get('rover') || params.get('username');
        if (!rover) return;
        const search = document.getElementById('search-box');
        if (search && search.value !== rover) {
            search.value = rover;
            renderTable();
        }
        const row = document.querySelector(`[data-username-key="${CSS.escape(normalizeUsername(rover))}"]`);
        row?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        row?.classList.add('active');
    }

    async function syncData() {
        if (document.hidden || syncInProgress) {
            return;
        }

        const syncTimeEl = document.getElementById('caster-sync-time');
        if (isAnyRoverModalOpen()) {
            if (syncTimeEl) syncTimeEl.textContent = 'Sync tạm dừng khi đang chỉnh sửa...';
            return;
        }

        syncInProgress = true;
        try {
            if (syncTimeEl) syncTimeEl.textContent = 'Syncing...';

            const refreshes = [fetchRovers({ render: false }), fetchCasterUsers({ render: false })];
            if (currentUser?.role === 'admin' && document.getElementById('tab-mountpoints-content')?.classList.contains('active')) {
                refreshes.push(loadAndRenderMountpoints({ background: true }));
            }
            await Promise.allSettled(refreshes);
            renderTable();

            if (syncTimeEl) {
                const now = new Date();
                syncTimeEl.textContent = `Last sync: ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
            }
        } finally {
            syncInProgress = false;
        }
    }

    async function refreshDataInPlace() {
        const currentScrollY = window.scrollY || 0;
        await Promise.allSettled([
            fetchRovers({ render: false }),
            fetchCasterUsers({ render: false })
        ]);
        renderTable();
        window.requestAnimationFrame(() => {
            window.scrollTo({ top: currentScrollY, behavior: 'auto' });
        });
    }

    async function fetchCasterConfig() {
        try {
            const res = await apiFetchRaw('/api/caster/config', { headers: API_HEADERS });
            if (res.ok) {
                const data = await res.json();
                availableCasters = data.hosts || [];
                globalAppVersion = data.version;
                renderCasterCheckboxes();
            }
        } catch (e) {
            console.error("Failed to fetch caster config", e);
        }
    }

    function renderCasterCheckboxes() {
        const createContainer = document.getElementById('target-caster-container');
        const editContainer = document.getElementById('edit-target-caster-container');
        
        if (createContainer) {
            createContainer.innerHTML = '';
            if (availableCasters.length === 0) {
                createContainer.innerHTML = '<div class="text-muted small">Không tìm thấy caster nào.</div>';
            } else {
                availableCasters.forEach(host => {
                    const isDefault = host.toLowerCase().includes('aitogy.com.vn');
                    const div = document.createElement('div');
                    div.className = 'form-check form-check-inline mb-0';
                    div.innerHTML = `
                        <input class="form-check-input caster-target-check" type="checkbox" value="${host}" id="target-${host}" ${isDefault ? 'checked' : ''}>
                        <label class="form-check-label text-white small" for="target-${host}">${host}</label>
                    `;
                    createContainer.appendChild(div);
                });
            }
            // Add listeners
            document.querySelectorAll('.caster-target-check').forEach(cb => {
                cb.addEventListener('change', () => {
                    applyLiveMpFilter('');
                    updateMountpointInput('');
                });
            });
        }

        if (editContainer) {
            editContainer.innerHTML = '';
            if (availableCasters.length === 0) {
                editContainer.innerHTML = '<div class="text-muted small">Không tìm thấy caster nào.</div>';
            } else {
                availableCasters.forEach(host => {
                    const div = document.createElement('div');
                    div.className = 'form-check form-check-inline mb-0';
                    div.innerHTML = `
                        <input class="form-check-input edit-caster-target-check" type="checkbox" value="${host}" id="edit-target-${host}">
                        <label class="form-check-label text-white small" for="edit-target-${host}">${host}</label>
                    `;
                    editContainer.appendChild(div);
                });
            }
            // Add listeners
            document.querySelectorAll('.edit-caster-target-check').forEach(cb => {
                cb.addEventListener('change', () => {
                    applyLiveMpFilter('edit-');
                    updateMountpointInput('edit-');
                });
            });
        }
    }

    async function fetchDevices() {
        try {
            const res = await apiFetchRaw('/api/devices', { headers: API_HEADERS });
            if (res.ok) {
                allDevices = await res.json();
                populateBaseSelect();
                populateFilterStation();
            }
        } catch (e) { console.error("Device fetch error", e); }
    }

    async function fetchRovers({ render = true } = {}) {
        try {
            const res = await apiFetchRaw('/api/rovers/all', { headers: API_HEADERS });
            if (!res.ok) throw new Error('Không thể tải danh sách Rover');
            allRovers = await res.json();
            const accountInput = document.getElementById('rover-activity-account-filter');
            const accountOptions = document.getElementById('rover-activity-account-options');
            if (accountInput && accountOptions && document.activeElement === accountInput && !accountOptions.hidden) {
                renderRoverActivityAccountOptions();
            }
            if (render) renderTable();
        } catch (error) {
            console.error("Rovers fetch error", error);
        }
    }

    // === CASTER USERS MANAGEMENT ===
    async function fetchCasterUsers({ render = true } = {}) {
        try {
            const res = await apiFetchRaw('/api/caster/users', { headers: API_HEADERS });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Không thể tải danh sách user từ Caster');
            }
            const data = await res.json();
            const serverTime = Number(data.server_time) || Date.now() / 1000;
            const receivedAt = Date.now() / 1000;
            casterUsers = (data.items || []).map(item => {
                const status = Number(item.status);
                if (!Number.isFinite(status) || status <= 0 || status >= 315360000) return item;
                const sampledAt = Number(item.last_update) || serverTime;
                const observedDeadline = receivedAt + status - Math.max(0, serverTime - sampledAt);
                const key = `${normalizeUsername(item.username)}|${String(item.server || '').toLowerCase()}`;
                const previous = expiryDeadlineCache.get(key);
                const sameSnapshot = previous?.sampledAt === sampledAt && previous?.status === status;
                const deadline = sameSnapshot || (previous && Math.abs(previous.deadline - observedDeadline) <= 3)
                    ? previous.deadline : observedDeadline;
                expiryDeadlineCache.set(key, { sampledAt, status, deadline });
                return { ...item, expiry_deadline: deadline };
            }).sort((a, b) => String(a.server || '').localeCompare(String(b.server || '')));
            casterStats = {
                total: data.total || 0,
                in_db_count: data.in_db_count || 0,
                unknown_count: data.unknown_count || 0,
                blocked_count: data.blocked_count || 0,
            };
            updateCasterStats();
            if (render) renderTable(); // Call unified render
        } catch (error) {
            console.error("Caster users fetch error", error);
        }
    }

    async function fetchCasterSources({ force = false } = {}) {
        if (casterSourcesCache && !force) return casterSourcesCache;
        try {
            const res = await apiFetchRaw('/api/admin/caster-sources', { headers: API_HEADERS });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Không thể tải mountpoints từ Caster');
            }
            casterSourcesCache = await res.json();
            return casterSourcesCache;
        } catch (error) {
            console.error('[Caster sources]', error);
            return null;
        }
    }

    function getAllCasterSources() {
        if (!casterSourcesCache) return [];
        return Array.isArray(casterSourcesCache.all) ? casterSourcesCache.all : [];
    }

    function normalizeMountpointList(value) {
        return Array.from(new Set(
            String(value || '')
                .split(/[;,\n]/)
                .map(v => v.trim())
                .filter(Boolean)
        ));
    }

    function renderAdminLiveMountpoints(prefix = '') {
        const box = document.getElementById(`${prefix}admin-live-mountpoints-box`);
        const list = document.getElementById(`${prefix}admin-live-mountpoints-list`);
        const searchInput = document.getElementById(`${prefix}live-mp-search`);
        const hostFilter = document.getElementById(`${prefix}live-mp-host-filter`);
        if (!box || !list) return;

        const isAdmin = String(currentUser?.role || '').toLowerCase() === 'admin';
        if (!isAdmin && prefix === '') return; // For create, only admins see this logic. Edit might be different.
        
        const label = box.querySelector('.form-label');
        if (label) label.textContent = 'Mountpoints Live từ Caster (Admin)';

        const sources = getAllCasterSources();
        if (!sources.length) {
            box.classList.remove('d-none');
            list.innerHTML = '<div class="text-muted small text-center">Không có mountpoint nào từ Caster</div>';
            return;
        }

        const uniqueHosts = Array.from(new Set(sources.map(s => String(s.host || '').trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b));
        if (hostFilter) {
            const prev = hostFilter.value || 'all';
            hostFilter.innerHTML = '<option value="all">Tất cả host</option>'
                + uniqueHosts.map(h => `<option value="${h}">${h}</option>`).join('');
            if (uniqueHosts.includes(prev)) hostFilter.value = prev;
        }

        box.classList.remove('d-none');
        list.innerHTML = sources.map((src, idx) => `
            <div class="form-check mb-1 live-mp-item" data-host="${src.host || ''}" data-search="${String(src.mountpoint || '').toLowerCase()} ${String(src.identifier || '').toLowerCase()} ${String(src.host || '').toLowerCase()}">
                <input class="form-check-input live-mp-check" type="checkbox" id="${prefix}live-mp-${idx}" value="${src.mountpoint || ''}" data-host="${src.host || ''}">
                <label class="form-check-label small text-white" for="${prefix}live-mp-${idx}">
                    <code class="text-info">${src.mountpoint || ''}</code>
                    <span class="text-muted ms-1">(${src.identifier || src.host || ''})</span>
                </label>
            </div>
        `).join('');

        if (searchInput) searchInput.oninput = () => applyLiveMpFilter(prefix);
        if (hostFilter) hostFilter.onchange = () => applyLiveMpFilter(prefix);
        applyLiveMpFilter(prefix);
    }

    function renderCoordinatorExtraMountpoints(prefix = '') {
        const box = document.getElementById(`${prefix}admin-live-mountpoints-box`);
        const list = document.getElementById(`${prefix}admin-live-mountpoints-list`);
        const searchInput = document.getElementById(`${prefix}live-mp-search`);
        const hostFilterLabel = document.getElementById(`${prefix}live-mp-host-filter`);
        if (!box || !list) return;

        const extraMps = (currentUser.extra_mountpoints || "").split(';').map(m => m.trim()).filter(Boolean);
        if (extraMps.length === 0) {
            box.classList.add('d-none');
            return;
        }

        const label = box.querySelector('.form-label');
        if (label) label.textContent = 'MountPoints bổ sung (Được cấp)';
        if (hostFilterLabel) hostFilterLabel.parentElement.classList.add('d-none');

        // Map extra MPs to hosts if possible from cache
        const sources = getAllCasterSources();
        const mpToHost = {};
        sources.forEach(s => {
            if (s.mountpoint) mpToHost[s.mountpoint] = s.host;
        });

        box.classList.remove('d-none');
        list.innerHTML = extraMps.map((mp, idx) => {
            const host = mpToHost[mp] || '';
            return `
                <div class="form-check mb-1 live-mp-item" data-host="${host}" data-search="${mp.toLowerCase()}">
                    <input class="form-check-input live-mp-check" type="checkbox" id="${prefix}extra-mp-${idx}" value="${mp}" data-host="${host}">
                    <label class="form-check-label small text-white" for="${prefix}extra-mp-${idx}">
                        <code class="text-info">${mp}</code>
                        ${host ? `<span class="text-muted ms-1 small">(${host})</span>` : ''}
                    </label>
                </div>
            `;
        }).join('');

        if (searchInput) searchInput.oninput = () => applyLiveMpFilter(prefix);
        applyLiveMpFilter(prefix);
    }

    function applyLiveMpFilter(prefix = '') {
        const list = document.getElementById(`${prefix}admin-live-mountpoints-list`);
        const searchInput = document.getElementById(`${prefix}live-mp-search`);
        const hostFilter = document.getElementById(`${prefix}live-mp-host-filter`);
        if (!list) return;

        const q = String(searchInput?.value || '').trim().toLowerCase();
        const h = String(hostFilter?.value || 'all').trim();
        
        const targetChecks = document.querySelectorAll(`.${prefix === 'edit-' ? 'edit-caster-target-check' : 'caster-target-check'}:checked`);
        const targetHosts = Array.from(targetChecks).map(cb => cb.value);

        // 1. Get current allowed mountpoints to hide if already in list
        const targetInputId = prefix === 'edit-' ? 'edit-mountpoints' : 'new-mountpoints';
        const allowedMpsInput = document.getElementById(targetInputId);
        const currentAllowedMps = normalizeMountpointList(allowedMpsInput?.value || '').map(m => m.toLowerCase());

        // 2. Get device base station mountpoints to hide them
        const deviceMps = [];
        allDevices.forEach(d => {
            const sConf = d.service_config || {};
            if (sConf.server1_enabled && sConf.mountpoint1) {
                deviceMps.push(sConf.mountpoint1.trim().toLowerCase());
            }
            if (sConf.server2_enabled && sConf.mountpoint2) {
                deviceMps.push(sConf.mountpoint2.trim().toLowerCase());
            }
        });

        list.querySelectorAll('.live-mp-item').forEach(item => {
            const checkEl = item.querySelector('.live-mp-check');
            const mpVal = String(checkEl?.value || '').trim().toLowerCase();

            const isAlreadyAllowed = currentAllowedMps.includes(mpVal);
            const isDeviceMp = deviceMps.includes(mpVal);

            const text = item.dataset.search || '';
            const host = item.dataset.host || '';
            const matchText = !q || text.includes(q);
            const matchHost = h === 'all' || host === h;
            const matchTarget = targetHosts.length === 0 || !host || targetHosts.includes(host);

            // Hide if already allowed or matches system Base station mountpoints
            const isDuplicate = isAlreadyAllowed || isDeviceMp;

            item.style.display = (matchText && matchHost && matchTarget && !isDuplicate) ? '' : 'none';
        });
    }

    function applySelectedLiveMountpoints() {
        const isEdit = this.id.startsWith('edit-');
        const prefix = isEdit ? 'edit-' : '';
        
        const checked = Array.from(document.querySelectorAll(`#${prefix}admin-live-mountpoints-list .live-mp-check:checked`))
            .filter(cb => cb.closest('.live-mp-item').style.display !== 'none')
            .map(cb => cb.value);

        if (!checked.length) {
            showToast('Vui lòng chọn ít nhất 1 mountpoint', 'warning');
            return;
        }

        const targetId = isEdit ? 'edit-mountpoints' : 'new-mountpoints';
        const target = document.getElementById(targetId);
        const current = normalizeMountpointList(target?.value || '');
        const merged = Array.from(new Set([...current, ...checked]));
        
        if (target) target.value = merged.join(';');
        showToast('Đã đưa mountpoint live vào Allowed Mountpoints', 'success');
        applyLiveMpFilter(prefix);
    }

    function updateCasterStats() {
        const el = (id, val) => {
            const e = document.getElementById(id);
            if (e) e.textContent = val;
        };
        el('stat-total', casterStats.total || 0);
        el('stat-indb', casterStats.in_db_count || 0);
        el('stat-unknown', casterStats.unknown_count || 0);
        el('stat-blocked', casterStats.blocked_count || 0);
    }

    function renderCasterTable() {
        // Redundant - functionality merged into renderTable
    }

    let blockModal = null;
    const MOUNTPOINT_EXAMPLES = "humga VP.VinhYen HY.MyHao UAV TV869 VP.TamDao VP.LapThach NA.ThanhChuong 7000 8001 Tram_HP LS.DL hhtg BRVT.CD HB.CaoPhong HN.SocSon QB.LeThuy QB.QuangNinh HD.TuMinh PQ01 PQ02 VP.SongLo SL.MocChau HP.AnThang HP.BatTrang hhks QN.DamHa TN.PhuBinh PT.DoanHung PT.TieuSon YB.LucYen ND.NamTruc HN.MinhChau ATGDT TDBD01 TDBD02 TDBD03 TDBD04".split(" ");

    async function handleBlockUser(e) {
        const username = e.currentTarget.dataset.username;
        const currentMps = (e.currentTarget.dataset.mountpoints || "").split(",").map(m => m.trim()).filter(m => m);
        
        // Init modal if not exists
        if (!blockModal) {
            blockModal = new bootstrap.Modal(document.getElementById('blockUserModal'));
        }

        document.getElementById('blockModalUsername').value = username;
        document.getElementById('blockReason').value = "Quá hạn sử dụng";
        document.getElementById('selectAllMps').checked = false;
        
        const container = document.getElementById('mountpointSelector');
        container.innerHTML = ""; // Clear

        // 1. Lấy danh sách mountpoint Rover này đang dùng
        const allMps = new Set([...currentMps]);
        
        allMps.forEach(mp => {
            if (!mp) return;
            const isCurrent = currentMps.includes(mp);
            const chip = document.createElement('div');
            chip.className = 'form-check form-check-inline';
            chip.innerHTML = `
                <input class="form-check-input mp-checkbox" type="checkbox" id="mp_${mp}" value="${mp}" ${isCurrent ? 'checked' : ''}>
                <label class="form-check-label px-2 py-1 rounded ${isCurrent ? 'bg-danger-subtle text-danger border border-danger' : 'bg-secondary-subtle border border-secondary'} small" for="mp_${mp}">
                    ${mp}
                </label>
            `;
            container.appendChild(chip);
        });

        document.getElementById('blockInfoText').innerText = `Chặn ${username} (${currentMps.length} trạm hiện tại)`;
        blockModal.show();
    }

    // Xử lý Select All
    const selectAllMps = document.getElementById('selectAllMps');
    if (selectAllMps) {
        selectAllMps.addEventListener('change', (e) => {
            const checked = e.target.checked;
            document.querySelectorAll('.mp-checkbox').forEach(cb => {
                cb.checked = checked;
            });
            const container = document.getElementById('mountpointSelector');
            if (container) {
                container.style.opacity = checked ? "0.5" : "1";
                container.style.pointerEvents = checked ? "none" : "auto";
            }
        });
    }

    // Xác nhận chặn
    const confirmBlockBtn = document.getElementById('confirmBlockBtn');
    if (confirmBlockBtn) {
        confirmBlockBtn.addEventListener('click', async () => {
        const username = document.getElementById('blockModalUsername').value;
        const reason = document.getElementById('blockReason').value;
        const selectAllInput = document.getElementById('selectAllMps');
        const selectAll = selectAllInput ? selectAllInput.checked : false;
        
        let targetMps = [];
        if (!selectAll) {
            document.querySelectorAll('.mp-checkbox:checked').forEach(cb => {
                targetMps.push(cb.value);
            });
            if (targetMps.length === 0) {
                showToast("Vui lòng chọn ít nhất 1 trạm hoặc tích 'Chặn toàn bộ'", "warning");
                return;
            }
        }

        const btn = document.getElementById('confirmBlockBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Đang chặn...';

        try {
            const res = await apiFetchRaw('/api/caster/block-user', {
                method: 'POST',
                headers: API_HEADERS,
                body: JSON.stringify({
                    username: username,
                    mountpoints: selectAll ? null : targetMps,
                    reason: reason || 'Blocked by admin'
                })
            });

            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Chặn thất bại');

            showToast(`🔒 ${data.message}`, 'success');
            blockModal.hide();
            await fetchCasterUsers(); // Reload table
        } catch (err) {
            showToast(err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-shield-lock-fill me-1"></i>Xác nhận chặn';
        }
        });
    }

    async function handleUnblockUser(e) {
        const username = e.currentTarget.dataset.username;
        const ok = await window.CorsUI.confirmAction({
            title: 'Unblock user',
            message: `Unblock user ${username}?`,
            confirmText: 'Unblock'
        });
        if (!ok) return;

        const btn = e.currentTarget;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

        try {
            const res = await apiFetchRaw('/api/caster/unblock-user', {
                method: 'POST',
                headers: API_HEADERS,
                body: JSON.stringify({ username: username })
            });

            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Mở khóa thất bại');

            showToast(`🔓 ${data.message}`, 'success');
            await fetchCasterUsers(); // Reload
        } catch (err) {
            showToast(err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-unlock-fill me-1"></i>Mở khóa';
        }
    }

    function updateLiveClocks() {
        const nowSec = Math.floor(Date.now() / 1000);
        document.querySelectorAll('.live-clock').forEach(el => {
            const startedAt = Number.parseInt(el.dataset.startedAt || 0);
            if (startedAt > 0) {
                const currentUptime = Math.max(0, nowSec - startedAt);
                el.textContent = formatDuration(currentUptime);
            }
        });
        document.querySelectorAll('.live-countdown').forEach(el => {
            const initialRemaining = Number.parseInt(el.dataset.remainingSeconds || 0);
            const fetchedAt = Number.parseInt(el.dataset.fetchedAt || 0);
            if (initialRemaining > 0 && fetchedAt > 0) {
                const elapsed = nowSec - fetchedAt;
                const newRemaining = Math.max(0, initialRemaining - elapsed);
                if (newRemaining <= 0) {
                    el.className = 'rover-expiry-badge badge bg-danger bg-opacity-10 text-danger border border-danger border-opacity-25 rounded-pill';
                    el.textContent = 'HẾT HẠN';
                } else {
                    el.textContent = formatDuration(newRemaining);
                }
            }
        });
    }

    // === UI POPULATION (FILTER & CHECKLIST) ===
    function populateBaseSelect() {
        const container = document.getElementById('station-checklist');
        if (!container) return;
        
        container.innerHTML = '';
        if (allDevices.length === 0) {
            container.innerHTML = '<div class="text-center small text-muted">Không có trạm nào khả dụng.</div>';
            return;
        }

        allDevices.forEach(d => {
            const sConf = d.service_config || {};
            let mpsWithHosts = []; // Format: "mp:host"
            if (sConf.server1_enabled && sConf.mountpoint1) {
                mpsWithHosts.push(`${sConf.mountpoint1}:${String(sConf.serverhost1 || '').trim()}`);
            }
            if (sConf.server2_enabled && sConf.mountpoint2) {
                mpsWithHosts.push(`${sConf.mountpoint2}:${String(sConf.serverhost2 || '').trim()}`);
            }

            if (mpsWithHosts.length > 0) {
                const div = document.createElement('div');
                div.className = 'form-check';
                div.innerHTML = `
                    <input class="form-check-input station-check" type="checkbox" 
                        value="${d.serial}" 
                        id="check-${d.serial}"
                        data-mps="${mpsWithHosts.join(';')}">
                    <label class="form-check-label text-white small" for="check-${d.serial}">
                        ${d.name} <span class="text-muted">(${mpsWithHosts.map(m => m.split(':')[0]).join(',')})</span>
                    </label>
                `;
                container.appendChild(div);
            }
        });

        document.querySelectorAll('.station-check').forEach(cb => {
            cb.addEventListener('change', () => updateMountpointInput(''));
        });
    }

    function updateMountpointInput(prefix = '') {
        const targetId = prefix === 'edit-' ? 'edit-mountpoints' : 'new-mountpoints';
        const targetInput = document.getElementById(targetId);
        if (!targetInput) return;

        const checkedStations = document.querySelectorAll(`.${prefix === 'edit-' ? 'edit-station-check' : 'station-check'}:checked`);
        const targetChecks = document.querySelectorAll(`.${prefix === 'edit-' ? 'edit-caster-target-check' : 'caster-target-check'}:checked`);
        const targetHosts = Array.from(targetChecks).map(cb => cb.value);

        // 1. Identify all possible station mountpoints to distinguish from manual entries
        const allStationMps = new Set();
        document.querySelectorAll(`.${prefix === 'edit-' ? 'edit-station-check' : 'station-check'}`).forEach(cb => {
            (cb.dataset.mps || '').split(';').forEach(s => {
                const mp = s.split(':')[0];
                if (mp) allStationMps.add(mp);
            });
        });

        // 2. Preserve manual entries (those not found in any station's mp list)
        const currentMps = normalizeMountpointList(targetInput.value);
        const manualMps = currentMps.filter(mp => !allStationMps.has(mp));

        // 3. Get mountpoints from currently checked stations, filtered by target server
        const stationMps = new Set();
        checkedStations.forEach(cb => {
            (cb.dataset.mps || '').split(';').forEach(s => {
                const [mp, host] = s.split(':');
                if (targetHosts.length === 0 || !host || targetHosts.includes(host)) {
                    if (mp) stationMps.add(mp);
                }
            });
        });

        const final = Array.from(new Set([...manualMps, ...stationMps]));
        targetInput.value = final.join(';');
        applyLiveMpFilter(prefix);
    }

    function populateFilterStation() {
        const select = document.getElementById('filter-station');
        select.innerHTML = '<option value="">Tất cả trạm Base</option>';
        allDevices.forEach(d => {
             const opt = document.createElement('option');
             opt.value = d.serial;
             opt.textContent = d.name;
             select.appendChild(opt);
        });
    }

    function formatDuration(seconds) {
        if (!seconds || seconds <= 0) return '0s';
        
        const d = Math.floor(seconds / 86400);
        const h = Math.floor((seconds % 86400) / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;

        let parts = [];
        if (d > 0) parts.push(`${d}d`);
        if (h > 0 || d > 0) parts.push(`${h}h`);
        if (m > 0 || h > 0 || d > 0) parts.push(`${m}m`);
        parts.push(`${s}s`);

        return parts.join(' ');
    }

    function formatDateTime(value) {
        if (!value) return 'N/A';
        const date = new Date(value * 1000);
        if (Number.isNaN(date.getTime())) return 'N/A';
        return date.toLocaleString('vi-VN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        })[char]);
    }

    function normalizeUsername(username) {
        return String(username || '').trim().toLowerCase();
    }

    function parseCasterHosts(rawValue) {
        if (Array.isArray(rawValue)) {
            return Array.from(new Set(rawValue.map(h => String(h || '').trim()).filter(Boolean)));
        }
        return Array.from(
            new Set(
                String(rawValue || '')
                    .split(/[;,]/)
                    .map(h => h.trim())
                    .filter(Boolean)
            )
        );
    }

    function getCasterInfosForUsername(username) {
        const key = normalizeUsername(username);
        if (!key) return [];
        return casterUsers.filter(cu => normalizeUsername(cu.username) === key);
    }

    function getRoverKey(roverOrUsername) {
        return normalizeUsername(typeof roverOrUsername === 'string' ? roverOrUsername : roverOrUsername?.username);
    }

    function getSelectedRovers() {
        return latestMergedRovers.filter(r => selectedRoverKeys.has(getRoverKey(r)));
    }

    function updateRoverBulkToolbar() {
        const toolbar = document.getElementById('rover-bulk-toolbar');
        const countEl = document.getElementById('rover-selected-count');
        const count = selectedRoverKeys.size;
        if (countEl) countEl.textContent = String(count);
        if (toolbar) toolbar.classList.toggle('d-none', count === 0);
        const selectAll = document.getElementById('rover-select-all');
        const visibleChecks = Array.from(document.querySelectorAll('.rover-row-check'));
        if (selectAll) {
            selectAll.checked = visibleChecks.length > 0 && visibleChecks.every(cb => cb.checked);
            selectAll.indeterminate = visibleChecks.some(cb => cb.checked) && !selectAll.checked;
        }
    }

    function formatExpiryCell(r, infos) {
        const live = infos.find(i => i?.status != null && Number.isFinite(Number(i.status)));
        const nowSec = Math.floor(Date.now() / 1000);
        let remainingSeconds = r.expires_at ? Math.floor(Number(r.expires_at) - nowSec) : null;
        if (live) {
            const status = Number(live.status);
            remainingSeconds = status <= 0 || status >= 315360000
                ? status
                : Math.floor((Number(live.expiry_deadline) || nowSec + status) - nowSec);
        }
        if (remainingSeconds === null || !Number.isFinite(remainingSeconds)) return '<span class="text-muted small">--</span>';
        if (remainingSeconds <= 0) return '<span class="rover-expiry-badge badge bg-danger bg-opacity-10 text-danger border border-danger border-opacity-25 rounded-pill">HẾT HẠN</span>';
        if (remainingSeconds >= 315360000) return '<span class="rover-expiry-badge badge bg-info bg-opacity-10 text-info border border-info border-opacity-25 rounded-pill">VĨNH VIỄN</span>';
        return `<span class="rover-expiry-badge badge bg-info bg-opacity-10 text-info border border-info border-opacity-25 rounded-pill live-countdown" data-remaining-seconds="${remainingSeconds}" data-fetched-at="${nowSec}">${formatDuration(remainingSeconds)}</span>`;
    }

    function isRoverExpiringSoon(r) {
        if (r.type !== 'db' || !r.expires_at) return false;
        const remainingDays = Math.ceil((Number(r.expires_at) - Date.now() / 1000) / 86400);
        return remainingDays >= 0 && remainingDays <= 14;
    }

    function createRoverRowElement(r) {
        const tr = document.createElement('tr');
        const escapedUsername = escapeHtml(r.username);
        const escapedUsernameKey = escapeHtml(normalizeUsername(r.username));
        tr.dataset.usernameKey = normalizeUsername(r.username);
        if (r.type === 'unknown') tr.style.backgroundColor = 'rgba(239, 68, 68, 0.03)';

        const infos = r.caster_infos || [r.caster_info].filter(i => i);
        const isBlocked = infos.some(i => i.is_blocked);

        let dbBadge = '';
        if (r.type === 'db') {
            dbBadge = r.is_active ? '<i class="bi bi-check-circle-fill text-success ms-1" title="Account Active"></i>' : '<i class="bi bi-exclamation-circle-fill text-danger ms-1" title="Account Expired"></i>';
        } else {
            dbBadge = '<i class="bi bi-question-diamond-fill text-warning ms-1" title="Unknown User (Not in DB)"></i>';
        }

        const targetHosts = parseCasterHosts(r.target_casters);
        let serverBadges = '';
        if (targetHosts.length > 0) {
            serverBadges = targetHosts.map(h => {
                const isPresent = infos.some(i => i.server === h);
                const color = isPresent ? 'text-success' : 'text-danger fw-bold';
                const icon = isPresent ? 'bi-server' : 'bi-exclamation-triangle';
                return `<span class="rover-server-chip" title="${escapeHtml(h)}">
                            <i class="bi ${icon} ${color}" aria-hidden="true"></i>
                            <span class="rover-server-name">${escapeHtml(h)}</span>
                        </span>`;
            }).join('');
        } else if (infos.length > 0) {
            serverBadges = infos.map(i => {
                return `<span class="rover-server-chip" title="${escapeHtml(i.server)}">
                            <i class="bi bi-server text-warning" aria-hidden="true"></i>
                            <span class="rover-server-name">${escapeHtml(i.server)}</span>
                        </span>`;
            }).join('');
        }

        const hasLiveShowPos = infos.some(i => typeof i?.show_position !== 'undefined');
        const actualShowPos = hasLiveShowPos
            ? infos.every(i => i?.show_position !== false)
            : r.show_position;
        const showPosIcon = actualShowPos
            ? '<i class="bi bi-eye-fill text-primary" title="Vị trí đang HIỆN (Live)"></i>'
            : '<i class="bi bi-eye-slash text-muted opacity-50" title="Vị trí đang ẨN (Live)"></i>';
        const autoSaveFixedPointsEnabled = r.type === 'db' && r.auto_save_fixed_points === true;
        const autoSaveFixedPointsIcon = r.type === 'db'
            ? `<span role="img" aria-label="Tự lưu điểm vị trí đang ${autoSaveFixedPointsEnabled ? 'bật' : 'tắt'}" title="Tự lưu điểm vị trí đang ${autoSaveFixedPointsEnabled ? 'BẬT' : 'TẮT'}"><i class="bi ${autoSaveFixedPointsEnabled ? 'bi-geo-alt-fill text-success' : 'bi-geo-alt text-muted opacity-50'}" aria-hidden="true"></i></span>`
            : '';
        const autoSaveSessionsEnabled = r.type === 'db' && r.auto_save_sessions !== false;
        const autoSaveSessionsIcon = r.type === 'db'
            ? `<span role="img" aria-label="Tự lưu phiên Rover đang ${autoSaveSessionsEnabled ? 'bật' : 'tắt'}" title="Tự lưu phiên Rover đang ${autoSaveSessionsEnabled ? 'BẬT' : 'TẮT'}"><i class="bi bi-clock-history ${autoSaveSessionsEnabled ? 'text-success' : 'text-muted opacity-50'}" aria-hidden="true"></i></span>`
            : '';

        const mps = normalizeMountpointList(r.mountpoints);
        const mpsDisplay = mps.length > 0
            ? mps.map(m => `<code class="rover-mount-chip">${escapeHtml(m)}</code>`).join('')
            : '<span class="text-muted small">Chưa cấp MP</span>';
        const mpsTitle = escapeHtml(mps.join(', '));

        // Connection Limit computation & UI representation.
        // Prefer live caster data when available because caster uses 0 as "unlimited".
        const liveMaxClients = infos
            .map(i => Number(i?.maxclient))
            .filter(v => Number.isFinite(v) && v >= 0);
        const dbMaxConnections = Number(r.max_connections);
        const maxConn = liveMaxClients.length > 0
            ? liveMaxClients[0]
            : (Number.isFinite(dbMaxConnections) && dbMaxConnections >= 0 ? dbMaxConnections : 1);

        let limitHtml = '';
        if (maxConn === 0) {
            limitHtml = `<div class="text-muted mt-1" style="font-size:0.7rem" title="Không giới hạn số lượng kết nối đồng thời"><i class="bi bi-infinity me-1 text-info"></i>Không giới hạn</div>`;
        } else {
            limitHtml = `<div class="text-muted mt-1" style="font-size:0.7rem" title="Số lượng kết nối đồng thời tối đa"><i class="bi bi-people me-1"></i>Tối đa: ${maxConn}</div>`;
        }

        let activeConnsHtml = '<span class="text-muted small">---</span>';
        const allActiveConns = [];
        infos.forEach(i => {
            if (i.active_connections) {
                i.active_connections.forEach(conn => {
                    allActiveConns.push({ ...conn, server: i.server });
                });
            }
        });

        const getConnectionClockKey = (conn) => {
            const server = String(conn?.server || '').trim().toLowerCase();
            const username = String(r.username || '').trim().toLowerCase();
            const mountpoint = String(conn?.mountpoint || '').trim().toLowerCase();
            const clientId = String(conn?.client_id || '').trim().toLowerCase();
            return [server, username, mountpoint, clientId].join('|');
        };

        const getStableStartedAt = (conn) => {
            const key = getConnectionClockKey(conn);
            const snapshotStartedAt = Number(conn?.started_at || 0);
            const cached = connectionClockCache.get(key);

            if (cached && Number.isFinite(cached.startedAt) && cached.startedAt > 0) {
                cached.lastSeenAt = Date.now();
                return cached.startedAt;
            }

            connectionClockCache.set(key, {
                startedAt: snapshotStartedAt > 0 ? snapshotStartedAt : null,
                lastSeenAt: Date.now(),
            });

            return snapshotStartedAt > 0 ? snapshotStartedAt : 0;
        };

        if (allActiveConns.length > 0) {
            const fetchTime = Date.now();
            activeConnsHtml = allActiveConns.map(conn => {
                const stableStartedAt = getStableStartedAt(conn);
                const durSeconds = Number(conn.duration_seconds ?? conn.uptime ?? 0);
                const durText = formatDuration(durSeconds);
                const startedAtText = formatDateTime(conn.started_at);
                const clientIdText = conn.client_id ? String(conn.client_id) : 'N/A';
                const serverLabel = allActiveConns.length > 1 ? `<span class="text-muted mx-1" style="font-size:0.6rem">[${escapeHtml(String(conn.server || '').split('.')[0])}]</span>` : '';
                const requestedMp = String(conn.requested_mountpoint || conn.mountpoint || '');
                const selectedMp = String(conn.selected_mountpoint || '');
                const routed = selectedMp && selectedMp.toLowerCase() !== requestedMp.toLowerCase();
                const receiverId = String(conn.receiver_id || '');
                const deviceLabel = /^esp_/i.test(receiverId)
                    ? `<span class="text-muted small">${escapeHtml(receiverId)} · </span>` : '';
                const sourceLabel = routed
                    ? `<span class="text-info small"> · RTCM từ ${escapeHtml(selectedMp)}</span>` : '';
                currentRenderedConnectionKeys.add(getConnectionClockKey(conn));
                return `<div class="rover-connection-row">
                            <span class="rover-connection-mp">${deviceLabel}${escapeHtml(requestedMp)}${sourceLabel}</span>
                            ${serverLabel}
                            <span class="rover-connection-time">
                                <i class="bi bi-clock me-1"></i>
                                <span class="live-clock" data-started-at="${Number.isFinite(stableStartedAt) ? stableStartedAt : 0}" title="Bắt đầu: ${escapeHtml(startedAtText)} | Client: ${escapeHtml(clientIdText)}">${escapeHtml(durText)}</span>
                                <span class="rover-connection-start">(${escapeHtml(startedAtText)})</span>
                            </span>
                        </div>`;
            }).join('');
        }

        let actionHtml = '';
        if (currentUser.permissions?.includes('manage:caster')) {
            if (isBlocked) {
                actionHtml += `
                    <button class="rover-action-btn caster-unblock-btn" 
                        data-username="${escapedUsername}" title="Mở khóa">
                        <i class="bi bi-unlock-fill"></i>
                    </button>`;
            } else {
                actionHtml += `
                    <button class="rover-action-btn danger caster-block-btn" 
                        data-username="${escapedUsername}"
                        data-mountpoints="${escapeHtml(mps.join(','))}"
                        title="Chặn truy cập">
                        <i class="bi bi-shield-lock-fill"></i>
                    </button>`;
            }
        }

        if (hasRoverEditAccess() || currentUser.role !== 'viewer') {
            if (r.type === 'db') {
                if (hasRoverEditAccess()) actionHtml += `
                    <button class="rover-action-btn edit-btn" 
                        data-id="${Number(r.id) || 0}" data-username="${escapedUsername}" title="Sửa">
                        <i class="bi bi-pencil-square"></i>
                    </button>
                `;
                if (currentUser.role !== 'viewer') actionHtml += `
                    <button class="rover-action-btn danger delete-btn" 
                        data-id="${Number(r.id) || 0}" data-user="${escapedUsername}" title="Xóa">
                        <i class="bi bi-trash3-fill"></i>
                    </button>
                `;
            } else if (r.type === 'unknown') {
                if (hasRoverEditAccess()) actionHtml += `
                    <button class="rover-action-btn edit-btn" 
                        data-id="0" data-username="${escapedUsername}" title="Chỉnh sửa và Đồng bộ về Web">
                        <i class="bi bi-pencil-square"></i>
                    </button>
                `;
                if (currentUser.role !== 'viewer') actionHtml += `
                    <button class="rover-action-btn danger caster-delete-btn" 
                        data-username="${escapedUsername}" title="Xóa tài khoản khỏi Caster">
                        <i class="bi bi-trash3"></i>
                    </button>
                `;
            }
        }

        const roverKey = getRoverKey(r);
        const checkedAttr = selectedRoverKeys.has(roverKey) ? 'checked' : '';
        const expiresHtml = formatExpiryCell(r, infos);
        const syncHtml = infos.length
            ? '<span class="badge bg-success bg-opacity-10 text-success border border-success border-opacity-25">Synced</span>'
            : '<span class="badge bg-secondary bg-opacity-25 text-secondary border border-secondary border-opacity-25">No caster</span>';
        const sessionHtml = allActiveConns.length
            ? `<span class="badge bg-info bg-opacity-10 text-info border border-info border-opacity-25">${allActiveConns.length}</span>`
            : '<span class="text-muted small">0</span>';
        const blockedHtml = isBlocked
            ? '<span class="badge bg-danger bg-opacity-10 text-danger border border-danger border-opacity-25">Blocked</span>'
            : '<span class="badge bg-success bg-opacity-10 text-success border border-success border-opacity-25">OK</span>';
        actionHtml = `
            <button class="rover-action-btn rover-history-btn"
                data-username="${escapedUsername}" title="Rover history">
                <i class="bi bi-clock-history"></i>
            </button>
            ${actionHtml}
        `;

        tr.innerHTML = `
            <td class="text-center">
                <input type="checkbox" class="form-check-input rover-row-check" data-username-key="${escapedUsernameKey}" aria-label="Chọn rover ${escapedUsername}" ${checkedAttr}>
            </td>
            <td>
                <div class="rover-user-cell">
                    <div class="rover-user-avatar ${r.type === 'db' ? '' : 'is-unknown'}">
                        <i class="bi bi-person-fill"></i>
                    </div>
                    <div class="rover-user-copy">
                        <div class="rover-user-heading">
                            <span class="rover-user-name">${escapeHtml(r.username)}</span>
                        </div>
                        <div class="rover-user-indicators">${dbBadge}${showPosIcon}${autoSaveFixedPointsIcon}${autoSaveSessionsIcon}${r.type === 'db' ? '' : '<span class="rover-user-meta">Caster only</span>'}</div>
                    </div>
                </div>
            </td>
            <td data-label="Password"><code class="rover-password-chip">${escapeHtml(r.password || '---')}</code></td>
            <td data-label="Mountpoints">
                <div class="rover-mount-cell">
                    <div class="rover-mountpoint-list" title="${mpsTitle}" aria-label="Mountpoints được cấp">${mpsDisplay}</div>
                </div>
            </td>
            <td data-label="Connection">
                <div>${activeConnsHtml}</div>
                ${limitHtml}
            </td>
            <td class="text-center" data-label="Caster sync">${syncHtml}</td>
            <td class="text-center" data-label="Sessions">${sessionHtml}</td>
            <td class="text-center" data-label="Blocked">${blockedHtml}</td>
            <td class="text-center" data-label="Expired">${expiresHtml}</td>
            <td class="text-center" data-label="Server"><div class="rover-server-list">${serverBadges || '<span class="badge bg-warning bg-opacity-10 text-warning border border-warning border-opacity-25" style="font-size:0.6rem">Cần gán Server</span>'}</div></td>
            <td class="rover-actions-cell" data-label="Actions"><div class="rover-row-actions">${actionHtml}</div></td>
        `;

        return tr;
    }

    function patchEditedRoverRow(updatedRover) {
        if (!updatedRover || !updatedRover.username) return;

        const updatedKey = normalizeUsername(updatedRover.username);
        const existingIndex = allRovers.findIndex(r => r.id === updatedRover.id || normalizeUsername(r.username) === updatedKey);
        if (existingIndex >= 0) {
            allRovers[existingIndex] = { ...allRovers[existingIndex], ...updatedRover };
        } else {
            allRovers.push(updatedRover);
        }

        const tbody = document.getElementById('rovers-table-body');
        if (!tbody) return;

        const oldRow = Array.from(tbody.querySelectorAll('tr')).find(row => row.dataset.usernameKey === updatedKey);
        if (!oldRow) return;

        const merged = {
            ...(allRovers.find(r => normalizeUsername(r.username) === updatedKey) || updatedRover),
            type: 'db',
            caster_infos: getCasterInfosForUsername(updatedRover.username)
        };

        const newRow = createRoverRowElement(merged);
        oldRow.replaceWith(newRow);

        newRow.querySelectorAll('.delete-btn').forEach(btn => btn.onclick = handleDeleteRover);
        newRow.querySelectorAll('.edit-btn').forEach(btn => btn.onclick = handleEditRover);
        newRow.querySelectorAll('.caster-block-btn').forEach(btn => btn.onclick = handleBlockUser);
        newRow.querySelectorAll('.caster-unblock-btn').forEach(btn => btn.onclick = handleUnblockUser);
        newRow.querySelectorAll('.caster-delete-btn').forEach(btn => btn.onclick = handleDeleteCasterUser);
        bindRoverSelectionEvents(newRow);
        newRow.querySelectorAll('.rover-history-btn').forEach(btn => btn.onclick = handleRoverHistory);
    }

    function bindRoverSelectionEvents(scope = document) {
        scope.querySelectorAll('.rover-row-check').forEach(cb => {
            cb.onchange = () => {
                const key = cb.dataset.usernameKey;
                if (!key) return;
                if (cb.checked) selectedRoverKeys.add(key);
                else selectedRoverKeys.delete(key);
                updateRoverBulkToolbar();
            };
        });
    }

    function ensureRoverHistoryModal() {
        const mount = document.getElementById('rover-history-modal-root');
        if (!mount) return null;
        const existing = mount.querySelector('#roverHistoryModal');
        if (existing) return existing;

        mount.innerHTML = `
            <div class="modal fade" id="roverHistoryModal" tabindex="-1" aria-labelledby="rover-history-title" aria-hidden="true">
                <div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable rover-history-dialog">
                    <div class="modal-content bg-dark border-secondary shadow-lg">
                        <div class="modal-header border-secondary">
                            <div class="rover-history-heading">
                                <div class="rover-history-heading-icon"><i class="bi bi-clock-history" aria-hidden="true"></i></div>
                                <div class="rover-history-heading-copy">
                                    <div class="rover-history-heading-title">
                                        <h5 class="modal-title text-white" id="rover-history-title">Lịch sử Rover</h5>
                                        <span class="rover-history-rover-name" id="rover-history-rover-name">Chưa chọn rover</span>
                                    </div>
                                    <p>Phiên kết nối, vị trí cuối và hoạt động gần đây</p>
                                </div>
                            </div>
                            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Đóng"></button>
                        </div>
                        <div class="modal-body p-4 rover-history-body" id="rover-history-content" aria-live="polite" aria-busy="false">
                            <div class="text-muted py-3">Chọn rover để xem lịch sử.</div>
                        </div>
                    </div>
                </div>
            </div>`;
        const modal = mount.querySelector('#roverHistoryModal');
        modal?.addEventListener('hidden.bs.modal', clearRoverHistorySessionMaps);
        return modal;
    }

    function localDateValue(value) {
        const year = value.getFullYear();
        const month = String(value.getMonth() + 1).padStart(2, '0');
        const day = String(value.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function activityDateLabel(value) {
        const parsed = new Date(`${value}T12:00:00`);
        const locale = window.AppI18n?.language === 'en' ? 'en-US' : 'vi-VN';
        return new Intl.DateTimeFormat(locale, { weekday: 'short', day: '2-digit', month: '2-digit' }).format(parsed);
    }

    function setRoverActivityStatus(message, isError = false) {
        const status = document.getElementById('rover-activity-status');
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('is-error', isError);
    }

    function roverActivityMachineKey(machine) {
        return String(machine?.machine_key || JSON.stringify([
            String(machine?.username || '').trim().toLowerCase(),
            String(machine?.receiver_id || '').trim().toLowerCase()
        ]));
    }

    function roverActivityMachineLabel(machine) {
        return `Receiver ${machine?.receiver_id || 'Không rõ'} · ${machine?.username || 'Không rõ tài khoản'}`;
    }

    function renderRoverActivityDays() {
        const container = document.getElementById('rover-activity-days-list');
        if (!container) return;
        if (!roverActivityDaily.length) {
            container.innerHTML = '<div class="rover-activity-empty"><i class="bi bi-calendar-x" aria-hidden="true"></i><span>Không có ngày trong bộ lọc.</span></div>';
            return;
        }
        const today = localDateValue(new Date());
        const yesterdayDate = new Date();
        yesterdayDate.setDate(yesterdayDate.getDate() - 1);
        const yesterday = localDateValue(yesterdayDate);
        container.innerHTML = roverActivityDaily.slice().reverse().map(item => {
            const dateValue = escapeHtml(item.date);
            const label = item.date === today ? 'Hôm nay'
                : item.date === yesterday ? 'Hôm qua'
                    : activityDateLabel(item.date);
            return `<button type="button" class="rover-activity-day-button ${item.date === roverActivitySelectedDate ? 'is-selected' : ''}" data-activity-date="${dateValue}" aria-pressed="${item.date === roverActivitySelectedDate}">
                <span><strong>${label}</strong><br>${dateValue}</span>
                <strong>${Number(item.machine_count) || 0} máy</strong>
            </button>`;
        }).join('');
        container.querySelectorAll('[data-activity-date]').forEach(button => {
            button.addEventListener('click', () => openRoverActivityDay(button.dataset.activityDate));
        });
    }

    async function refreshRoverActivity() {
        const fromInput = document.getElementById('rover-activity-date-from');
        const toInput = document.getElementById('rover-activity-date-to');
        if (!fromInput || !toInput) return;
        const dateFrom = fromInput.value;
        const dateTo = toInput.value;
        if (!dateFrom || !dateTo || dateFrom > dateTo) {
            setRoverActivityStatus('Chọn khoảng ngày hợp lệ để lọc.', true);
            return;
        }
        const requestId = ++roverActivityDailyRequest;
        roverActivityDayRequest += 1;
        clearRoverActivityTrack();
        setRoverActivityStatus('Đang tính số máy theo tài khoản và receiver…');
        const container = document.getElementById('rover-activity-days-list');
        if (container) container.innerHTML = '<div class="rover-activity-empty" role="status"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Đang tải số máy…</span></div>';
        try {
            const query = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
            const response = await apiFetchRaw(`/api/rovers/activity/daily?${query}`, { headers: API_HEADERS });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Không tải được thống kê (${response.status}).`);
            if (requestId !== roverActivityDailyRequest) return;
            roverActivityDaily = data.days || [];
            const hasSelectedDate = roverActivityDaily.some(item => item.date === roverActivitySelectedDate);
            roverActivitySelectedDate = hasSelectedDate ? roverActivitySelectedDate : dateTo;
            renderRoverActivityDays();
            await openRoverActivityDay(roverActivitySelectedDate, { skipRender: true });
            setRoverActivityStatus(`Đã tải ${roverActivityDaily.length} ngày. Mỗi cặp tài khoản và receiver được tính là một máy.`);
        } catch (error) {
            if (requestId !== roverActivityDailyRequest) return;
            setRoverActivityStatus(error.message || 'Không tải được thống kê hoạt động.', true);
            if (container) container.innerHTML = `<div class="rover-activity-empty"><i class="bi bi-exclamation-circle" aria-hidden="true"></i><span>${escapeHtml(error.message || 'Không tải được dữ liệu.')}</span></div>`;
        }
    }

    async function openRoverActivityDay(dateValue, options = {}) {
        if (!dateValue) return;
        roverActivitySelectedDate = dateValue;
        if (!options.skipRender) renderRoverActivityDays();
        const requestId = ++roverActivityDayRequest;
        const machinesContainer = document.getElementById('rover-activity-machines');
        const subtitle = document.getElementById('rover-activity-detail-subtitle');
        const countEl = document.getElementById('rover-activity-machine-count');
        if (subtitle) subtitle.textContent = `Ngày ${dateValue} · đang tải danh sách máy…`;
        if (countEl) countEl.textContent = '… máy';
        if (machinesContainer) machinesContainer.innerHTML = '<div class="rover-activity-empty" role="status"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Đang tải máy đo…</span></div>';
        clearRoverActivityTrack();
        try {
            const query = new URLSearchParams({ date: dateValue });
            const response = await apiFetchRaw(`/api/rovers/activity/day?${query}`, { headers: API_HEADERS });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Không tải được danh sách máy (${response.status}).`);
            if (requestId !== roverActivityDayRequest) return;
            roverActivityMachines = data.machines || [];
            const count = Number(data.machine_count) || 0;
            const countEl = document.getElementById('rover-activity-machine-count');
            if (countEl) countEl.textContent = `${count} máy`;
            if (subtitle) subtitle.textContent = `Ngày ${dateValue} · ${count} máy (tài khoản + receiver)`;
            renderRoverActivityMachines();
            const selected = roverActivityMachines.find(item => roverActivityMachineKey(item) === roverActivitySelectedMachineKey);
            if (selected) await selectRoverActivityMachine(roverActivityMachineKey(selected));
            else if (roverActivityMachines.length) await selectRoverActivityMachine(roverActivityMachineKey(roverActivityMachines[0]));
            else {
                roverActivitySelectedMachineKey = '';
                const title = document.getElementById('rover-activity-track-title');
                const meta = document.getElementById('rover-activity-track-meta');
                if (title) title.textContent = 'Không có máy trong ngày này';
                if (meta) meta.textContent = 'Không ghi nhận phiên nào trong ngày được chọn.';
                if (machinesContainer) machinesContainer.innerHTML = '<div class="rover-activity-empty"><i class="bi bi-router" aria-hidden="true"></i><span>Không có máy đo trong ngày này.</span></div>';
                setRoverActivityStatus(`Ngày ${dateValue}: không có máy hoạt động.`);
            }
        } catch (error) {
            if (requestId !== roverActivityDayRequest) return;
            if (subtitle) subtitle.textContent = 'Không tải được danh sách máy.';
            if (machinesContainer) machinesContainer.innerHTML = `<div class="rover-activity-empty"><i class="bi bi-exclamation-circle" aria-hidden="true"></i><span>${escapeHtml(error.message || 'Không tải được dữ liệu.')}</span></div>`;
            setRoverActivityStatus(error.message || 'Không tải được danh sách máy.', true);
        }
    }

    function renderRoverActivityMachines() {
        const container = document.getElementById('rover-activity-machines');
        if (!container) return;
        if (!roverActivityMachines.length) {
            container.innerHTML = '<div class="rover-activity-empty"><i class="bi bi-router" aria-hidden="true"></i><span>Không có máy đo trong ngày này.</span></div>';
            return;
        }
        container.innerHTML = `${roverActivityMachines.map(item => {
            const receiver = String(item.receiver_id || '');
            const username = String(item.username || item.usernames?.[0] || 'Không rõ tài khoản');
            const machineKey = roverActivityMachineKey(item);
            const selected = machineKey === roverActivitySelectedMachineKey;
            return `<button type="button" class="rover-activity-machine-button ${selected ? 'is-selected' : ''}" aria-pressed="${selected}" data-activity-machine-key="${escapeHtml(machineKey)}" data-activity-receiver="${escapeHtml(receiver)}" data-activity-username="${escapeHtml(username)}">
                <strong>Receiver ${escapeHtml(receiver)}</strong>
                <span>Tài khoản: ${escapeHtml(username)}</span>
                <small>${Number(item.session_count) || 0} phiên · ${Number(item.point_count) || 0} điểm vị trí</small>
            </button>`;
        }).join('')}<div class="rover-activity-empty rover-activity-filter-empty" hidden>Không tìm thấy máy khớp bộ lọc.</div>`;
        container.querySelectorAll('[data-activity-machine-key]').forEach(button => {
            button.addEventListener('click', () => selectRoverActivityMachine(button.dataset.activityMachineKey));
        });
        filterRoverActivityMachines();
    }

    function roverActivityAccountNames() {
        const accounts = new Map();
        allRovers.forEach(rover => {
            const username = String(rover?.username || '').trim();
            const key = normalizeUsername(username);
            if (key && !accounts.has(key)) accounts.set(key, username);
        });
        const locale = window.AppI18n?.language === 'en' ? 'en' : 'vi';
        return [...accounts.values()].sort((left, right) => left.localeCompare(right, locale, { sensitivity: 'base' }));
    }

    function closeRoverActivityAccountOptions() {
        const input = document.getElementById('rover-activity-account-filter');
        const options = document.getElementById('rover-activity-account-options');
        if (!input || !options) return;
        options.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        options.querySelectorAll('.is-active').forEach(option => option.classList.remove('is-active'));
        roverActivityAccountOptionIndex = -1;
    }

    function renderRoverActivityAccountOptions({ showAll = false } = {}) {
        const input = document.getElementById('rover-activity-account-filter');
        const options = document.getElementById('rover-activity-account-options');
        if (!input || !options) return;
        const accounts = roverActivityAccountNames();
        const query = showAll ? '' : normalizeUsername(input.value);
        const matches = accounts.filter(username => normalizeUsername(username).includes(query));
        options.innerHTML = matches.length
            ? matches.map((username, index) => {
                const selected = normalizeUsername(username) === normalizeUsername(input.value);
                return `<button type="button" id="rover-activity-account-option-${index}" class="rover-activity-account-option ${selected ? 'is-selected' : ''}" role="option" aria-selected="${selected}" tabindex="-1" data-rover-activity-account="${escapeHtml(username)}">${escapeHtml(username)}</button>`;
            }).join('')
            : `<div class="rover-activity-account-options-empty" role="option" aria-disabled="true">${accounts.length ? 'Không tìm thấy tài khoản khớp.' : 'Chưa có tài khoản Rover để chọn.'}</div>`;
        options.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        input.removeAttribute('aria-activedescendant');
        roverActivityAccountOptionIndex = -1;
    }

    function selectRoverActivityAccount(username) {
        const input = document.getElementById('rover-activity-account-filter');
        if (!input) return;
        input.value = username;
        closeRoverActivityAccountOptions();
        filterRoverActivityMachines();
    }

    function setRoverActivityAccountOptionActive(index) {
        const input = document.getElementById('rover-activity-account-filter');
        const options = document.getElementById('rover-activity-account-options');
        const choices = [...(options?.querySelectorAll('[data-rover-activity-account]') || [])];
        if (!input || !options || !choices.length) return;
        roverActivityAccountOptionIndex = (index + choices.length) % choices.length;
        choices.forEach((choice, choiceIndex) => choice.classList.toggle('is-active', choiceIndex === roverActivityAccountOptionIndex));
        const activeChoice = choices[roverActivityAccountOptionIndex];
        input.setAttribute('aria-activedescendant', activeChoice.id);
        activeChoice.scrollIntoView({ block: 'nearest' });
    }

    function filterRoverActivityMachines() {
        const receiverQuery = String(document.getElementById('rover-activity-receiver-filter')?.value || '').trim().toLowerCase();
        const accountQuery = String(document.getElementById('rover-activity-account-filter')?.value || '').trim().toLowerCase();
        const buttons = [...document.querySelectorAll('#rover-activity-machines [data-activity-machine-key]')];
        let visibleCount = 0;
        buttons.forEach(button => {
            const matchesReceiver = !receiverQuery || button.dataset.activityReceiver.toLowerCase().includes(receiverQuery);
            const matchesAccount = !accountQuery || button.dataset.activityUsername.toLowerCase().includes(accountQuery);
            button.hidden = !matchesReceiver || !matchesAccount;
            if (!button.hidden) visibleCount += 1;
        });
        const emptyState = document.querySelector('#rover-activity-machines .rover-activity-filter-empty');
        if (emptyState) emptyState.hidden = !buttons.length || visibleCount > 0;
    }

    async function selectRoverActivityMachine(machineKey) {
        const machine = roverActivityMachines.find(item => roverActivityMachineKey(item) === machineKey);
        if (!machine) return;
        roverActivitySelectedMachineKey = machineKey;
        const receiverId = String(machine.receiver_id || '');
        const username = String(machine.username || '');
        document.querySelectorAll('#rover-activity-machines [data-activity-machine-key]').forEach(button => {
            const selected = button.dataset.activityMachineKey === machineKey;
            button.classList.toggle('is-selected', selected);
            button.setAttribute('aria-pressed', String(selected));
        });
        const title = document.getElementById('rover-activity-track-title');
        const meta = document.getElementById('rover-activity-track-meta');
        if (title) title.textContent = roverActivityMachineLabel(machine);
        if (meta && machine) meta.textContent = `${Number(machine.session_count) || 0} phiên · ${Number(machine.point_count) || 0} điểm vị trí · nối theo thời gian (đầu xanh, cuối đỏ) · điểm gần nhau có thể chồng, bấm + để phóng to`;
        clearRoverActivityTrack();
        await loadRoverActivityTrack(receiverId, username);
    }

    function clearRoverActivityTrack() {
        roverActivityTrackRequest += 1;
        if (roverActivityMap) {
            roverActivityMap.remove();
            roverActivityMap = null;
        }
        const map = document.getElementById('rover-activity-map');
        if (map) {
            map.classList.add('is-empty');
            map.innerHTML = '<div class="rover-activity-empty">Chọn máy để xem đường đi của các điểm vị trí.</div>';
        }
        const sessions = document.getElementById('rover-activity-sessions');
        if (sessions) sessions.innerHTML = '';
        const points = document.getElementById('rover-activity-points');
        if (points) points.innerHTML = '';
        const links = document.getElementById('rover-activity-google-links');
        if (links) {
            links.replaceChildren();
            links.hidden = true;
        }
    }

    async function loadRoverActivityTrack(receiverId, username) {
        const requestId = ++roverActivityTrackRequest;
        const map = document.getElementById('rover-activity-map');
        const pointList = document.getElementById('rover-activity-points');
        if (map) {
            map.classList.remove('is-empty');
            map.innerHTML = '<div class="rover-activity-empty" role="status"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Đang vẽ tuyến vị trí…</span></div>';
        }
        if (pointList) pointList.innerHTML = '<div class="rover-activity-empty" role="status"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Đang tải điểm vị trí…</span></div>';
        const query = new URLSearchParams({ date: roverActivitySelectedDate, receiver_id: receiverId, username });
        try {
            const response = await apiFetchRaw(`/api/rovers/activity/track?${query}`, { headers: API_HEADERS });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Không tải được tuyến vị trí (${response.status}).`);
            if (requestId !== roverActivityTrackRequest) return;
            const points = data.points || [];
            const sessions = data.sessions || [];
            if (!points.length && !sessions.length) {
                clearRoverActivityTrack();
                const title = document.getElementById('rover-activity-track-title');
                const meta = document.getElementById('rover-activity-track-meta');
                const selectedMachine = roverActivityMachines.find(item => roverActivityMachineKey(item) === roverActivitySelectedMachineKey);
                if (title) title.textContent = roverActivityMachineLabel(selectedMachine);
                if (meta) meta.textContent = 'Máy có phiên hoạt động nhưng chưa lưu tọa độ hợp lệ trong ngày.';
                if (pointList) pointList.innerHTML = '<div class="rover-activity-empty"><i class="bi bi-geo" aria-hidden="true"></i><span>Chưa có điểm vị trí trong ngày.</span></div>';
                return;
            }
            renderRoverActivityTrack(points, sessions);
        } catch (error) {
            if (requestId !== roverActivityTrackRequest) return;
            if (pointList) pointList.innerHTML = `<div class="rover-activity-empty"><i class="bi bi-exclamation-circle" aria-hidden="true"></i><span>${escapeHtml(error.message || 'Không tải được điểm vị trí.')}</span></div>`;
            setRoverActivityStatus(error.message || 'Không tải được tuyến vị trí.', true);
        }
    }

    function renderRoverActivityTrack(points, sessionRecords = []) {
        const mapElement = document.getElementById('rover-activity-map');
        const pointList = document.getElementById('rover-activity-points');
        const sessionPicker = document.getElementById('rover-activity-sessions');
        if (!mapElement || !pointList || !sessionPicker) return;
        const validPoints = (Array.isArray(points) ? points : []).filter(point => {
            if (point?.lat == null || point?.lon == null || point.lat === '' || point.lon === '') return false;
            const latitude = Number(point.lat);
            const longitude = Number(point.lon);
            return Number.isFinite(latitude) && Number.isFinite(longitude)
                && latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180
                && !(latitude === 0 && longitude === 0);
        });
        const sessionGroups = new Map();
        validPoints.forEach(point => {
            const sessionId = String(point.session_id ?? 'unknown');
            if (!sessionGroups.has(sessionId)) sessionGroups.set(sessionId, []);
            sessionGroups.get(sessionId).push(point);
        });
        const sessionsById = new Map((Array.isArray(sessionRecords) ? sessionRecords : []).map(session => [String(session.id), {
            ...session,
            id: String(session.id),
            points: sessionGroups.get(String(session.id)) || []
        }]));
        sessionGroups.forEach((sessionPoints, id) => {
            if (!sessionsById.has(id)) sessionsById.set(id, { id, points: sessionPoints });
        });
        const sessions = [...sessionsById.values()];
        const sessionColor = index => `hsl(${(index * 137.508 + 188) % 360} 78% 58%)`;
        const initialSessionKey = 'all';
        sessionPicker.innerHTML = `<button type="button" class="rover-activity-session-button is-selected" data-activity-session="all" aria-pressed="true"><strong>Tất cả phiên</strong><small>${validPoints.length} điểm vị trí · ${sessions.length} phiên</small></button>${sessions.map((session, index) => {
            const firstPoint = session.points[0];
            const lastPoint = session.points[session.points.length - 1];
            const selected = session.id === initialSessionKey;
            const sessionStart = session.started_at ?? firstPoint?.recorded_at ?? session.last_seen_at;
            const sessionEnd = session.ended_at ?? session.last_seen_at ?? lastPoint?.recorded_at;
            const sessionStatus = session.ended_at ? 'Đã kết thúc' : 'Đang hoạt động';
            return `<button type="button" class="rover-activity-session-button ${selected ? 'is-selected' : ''}" style="--rover-activity-route-color:${sessionColor(index)}" data-activity-session="${escapeHtml(session.id)}" aria-pressed="${selected}"><strong>Phiên ${index + 1}</strong><small>${session.points.length} điểm · ${formatDateTime(sessionStart)} – ${formatDateTime(sessionEnd)} · ${sessionStatus}</small></button>`;
        }).join('')}`;
        const googlePointUrl = point => `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${point.lat},${point.lon}`)}`;
        const googleLinks = document.getElementById('rover-activity-google-links');
        const title = document.getElementById('rover-activity-track-title');
        const meta = document.getElementById('rover-activity-track-meta');
        const selectSession = sessionKey => {
            const selectedSession = sessions.find(session => session.id === sessionKey);
            const selectedPoints = selectedSession ? selectedSession.points : validPoints;
            const sessionIndex = selectedSession ? sessions.indexOf(selectedSession) + 1 : 0;
            sessionPicker.querySelectorAll('[data-activity-session]').forEach(button => {
                const selected = button.dataset.activitySession === sessionKey;
                button.classList.toggle('is-selected', selected);
                button.setAttribute('aria-pressed', String(selected));
            });
            const selectedMachine = roverActivityMachines.find(item => roverActivityMachineKey(item) === roverActivitySelectedMachineKey);
            if (title) title.textContent = `${roverActivityMachineLabel(selectedMachine)}${selectedSession ? ` · Phiên ${sessionIndex}` : ''}`;
            if (meta) meta.textContent = selectedSession
                ? selectedPoints.length
                    ? `${selectedPoints.length} điểm vị trí · ${formatDateTime(selectedPoints[0].recorded_at)} – ${formatDateTime(selectedPoints[selectedPoints.length - 1].recorded_at)} · nối từ đầu xanh đến cuối đỏ`
                    : `Phiên ${sessionIndex} chưa có điểm vị trí được lưu.`
                : `${validPoints.length} điểm vị trí · ${sessions.length} phiên · mỗi phiên một màu`;

            if (roverActivityMap) roverActivityMap.remove();
            roverActivityMap = null;
            mapElement.replaceChildren();
            const routeSessions = selectedSession
                ? [{ session: selectedSession, index: sessionIndex - 1 }]
                : sessions.map((session, index) => ({ session, index }));
            if (googleLinks) {
                googleLinks.innerHTML = routeSessions.map(({ session, index }) => {
                    const routes = roverHistoryGoogleMapsRoutes(session.points);
                    return routes
                        ? `<div class="rover-activity-route-group" style="--rover-activity-route-color:${sessionColor(index)}"><strong>Phiên ${index + 1}</strong>${routes}</div>`
                        : '';
                }).join('');
                googleLinks.hidden = !googleLinks.childElementCount;
            }
            const emptyPointsMessage = selectedSession
                ? `Phiên ${sessionIndex} chưa có tọa độ hợp lệ.`
                : 'Chưa có điểm vị trí nào được lưu trong ngày này.';
            pointList.innerHTML = selectedPoints.length ? selectedPoints.map((point, index) => `<div class="rover-activity-point-row">
                <div class="rover-activity-point-details">
                    <span><strong>${index + 1}.</strong> ${formatDateTime(point.recorded_at)} · ${escapeHtml(point.username || '')}${point.mountpoint ? ` · ${escapeHtml(point.mountpoint)}` : ''}</span>
                    <span class="rover-activity-point-coordinates">Tọa độ (vĩ độ, kinh độ): ${Number(point.lat).toFixed(7)}, ${Number(point.lon).toFixed(7)}</span>
                    <span>${escapeHtml(roverFixQualityLabel(point.fix_quality))} · ${point.satellites == null ? 'Không rõ số vệ tinh' : `${escapeHtml(point.satellites)} vệ tinh`}</span>
                </div>
                <a href="${googlePointUrl(point)}" target="_blank" rel="noopener noreferrer" aria-label="Mở tọa độ điểm ${index + 1}${selectedSession ? ` phiên ${sessionIndex}` : ''} trên Google Maps">Bản đồ</a>
            </div>`).join('') : '';
            if (!selectedPoints.length) {
                mapElement.classList.add('is-empty');
                mapElement.innerHTML = `<div class="rover-activity-empty"><i class="bi bi-geo" aria-hidden="true"></i><span>${emptyPointsMessage}</span></div>`;
                return;
            }
            mapElement.classList.remove('is-empty');

            const leaflet = window.L;
            if (!leaflet || typeof leaflet.map !== 'function') {
                mapElement.classList.add('is-empty');
                mapElement.innerHTML = '<div class="rover-activity-empty">Bản đồ chưa tải được. Danh sách tọa độ vẫn có thể mở trên Google Maps.</div>';
                return;
            }
            try {
                roverActivityMap = leaflet.map(mapElement, { preferCanvas: true, scrollWheelZoom: false });
                addMapTiles(leaflet, roverActivityMap);
                const visibleSessions = selectedSession ? [selectedSession] : sessions;
                const mapCoordinates = [];
                visibleSessions.forEach(session => {
                    const originalSessionIndex = sessions.indexOf(session);
                    const routeColor = sessionColor(originalSessionIndex);
                    const coordinates = session.points.map(point => [Number(point.lat), Number(point.lon)]);
                    if (coordinates.length > 1) {
                        leaflet.polyline(coordinates, { color: routeColor, weight: 4, opacity: 0.9 }).addTo(roverActivityMap);
                    }
                    coordinates.forEach((coordinate, index) => {
                        const point = session.points[index];
                        const isStart = index === 0;
                        const isEnd = index === session.points.length - 1;
                        mapCoordinates.push(coordinate);
                        leaflet.circleMarker(coordinate, {
                            radius: isStart || isEnd ? 6 : 3,
                            color: isStart ? '#34d399' : isEnd ? '#fb7185' : routeColor,
                            fillColor: isStart ? '#34d399' : isEnd ? '#fb7185' : routeColor,
                            fillOpacity: 0.95,
                            weight: 1
                        }).addTo(roverActivityMap).bindPopup(`<strong>${selectedSession ? `Điểm ${index + 1}` : `Phiên ${originalSessionIndex + 1} · điểm ${index + 1}`}</strong><br>${formatDateTime(point.recorded_at)}<br>${Number(point.lat).toFixed(7)}, ${Number(point.lon).toFixed(7)}<br>${escapeHtml(roverFixQualityLabel(point.fix_quality))} · ${point.satellites == null ? 'Không rõ số vệ tinh' : `${escapeHtml(point.satellites)} vệ tinh`}`);
                    });
                });
                if (mapCoordinates.length > 1) roverActivityMap.fitBounds(leaflet.latLngBounds(mapCoordinates), { padding: [22, 22], maxZoom: 19 });
                else roverActivityMap.setView(mapCoordinates[0], 17);
                requestAnimationFrame(() => roverActivityMap?.invalidateSize());
            } catch (error) {
                mapElement.classList.add('is-empty');
                mapElement.innerHTML = '<div class="rover-activity-empty">Không thể dựng bản đồ. Danh sách tọa độ vẫn có thể mở trên Google Maps.</div>';
                console.warn('Could not render Rover activity map', error);
            }
        };
        sessionPicker.querySelectorAll('[data-activity-session]').forEach(button => {
            button.addEventListener('click', () => selectSession(button.dataset.activitySession));
        });
        selectSession(initialSessionKey);
    }

    function setupRoverActivity() {
        const fromInput = document.getElementById('rover-activity-date-from');
        const toInput = document.getElementById('rover-activity-date-to');
        const form = document.getElementById('rover-activity-date-form');
        if (!fromInput || !toInput || !form) return;
        const today = new Date();
        const from = new Date(today);
        from.setDate(from.getDate() - 6);
        fromInput.value = localDateValue(from);
        toInput.value = localDateValue(today);
        form.addEventListener('submit', event => {
            event.preventDefault();
            refreshRoverActivity().catch(error => console.error('refreshRoverActivity failed:', error));
        });
        document.getElementById('rover-activity-receiver-filter')?.addEventListener('input', filterRoverActivityMachines);
        const accountInput = document.getElementById('rover-activity-account-filter');
        const accountOptions = document.getElementById('rover-activity-account-options');
        accountInput?.addEventListener('focus', () => renderRoverActivityAccountOptions({ showAll: true }));
        accountInput?.addEventListener('input', () => {
            filterRoverActivityMachines();
            renderRoverActivityAccountOptions();
        });
        accountInput?.addEventListener('blur', () => setTimeout(closeRoverActivityAccountOptions, 120));
        accountInput?.addEventListener('keydown', event => {
            if (event.key === 'Escape') {
                closeRoverActivityAccountOptions();
                return;
            }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                if (accountOptions?.hidden) renderRoverActivityAccountOptions({ showAll: true });
                const direction = event.key === 'ArrowDown' ? 1 : -1;
                const count = accountOptions?.querySelectorAll('[data-rover-activity-account]').length || 0;
                if (!count) return;
                event.preventDefault();
                setRoverActivityAccountOptionActive(roverActivityAccountOptionIndex < 0
                    ? (direction > 0 ? 0 : count - 1)
                    : roverActivityAccountOptionIndex + direction);
                return;
            }
            if (event.key === 'Enter' && accountOptions && !accountOptions.hidden) {
                const choice = accountOptions.querySelectorAll('[data-rover-activity-account]')[roverActivityAccountOptionIndex];
                if (choice) {
                    event.preventDefault();
                    selectRoverActivityAccount(choice.dataset.roverActivityAccount);
                }
            }
        });
        accountOptions?.addEventListener('mousedown', event => {
            if (event.target.closest('[data-rover-activity-account]')) event.preventDefault();
        });
        accountOptions?.addEventListener('click', event => {
            const choice = event.target.closest('[data-rover-activity-account]');
            if (choice) selectRoverActivityAccount(choice.dataset.roverActivityAccount);
        });
        document.getElementById('tab-rover-activity')?.addEventListener('shown.bs.tab', () => {
            if (!roverActivityDaily.length) refreshRoverActivity().catch(error => console.error('refreshRoverActivity failed:', error));
            else requestAnimationFrame(() => roverActivityMap?.invalidateSize());
        });
    }

    function clearRoverHistorySessionMaps() {
        roverHistorySessionMaps.forEach(map => map.remove());
        roverHistorySessionMaps.clear();
    }

    function renderRoverHistorySessionMap(sessionId, mapElement, points) {
        const validPoints = (Array.isArray(points) ? points : []).map((point, index) => ({ point, index }))
            .filter(({ point }) => {
                if (point?.lat == null || point?.lon == null || point.lat === '' || point.lon === '') return false;
                const latitude = Number(point.lat);
                const longitude = Number(point.lon);
                return Number.isFinite(latitude) && Number.isFinite(longitude)
                    && latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180
                    && !(latitude === 0 && longitude === 0);
            });
        const leaflet = window.L;
        if (!leaflet || typeof leaflet.map !== 'function') {
            mapElement.innerHTML = '<div class="rover-activity-empty">Bản đồ chưa tải được.</div>';
            return null;
        }
        if (!validPoints.length) {
            mapElement.innerHTML = '<div class="rover-activity-empty">Phiên này không có tọa độ hợp lệ.</div>';
            return null;
        }

        mapElement.replaceChildren();
        try {
            const coordinates = validPoints.map(({ point }) => [Number(point.lat), Number(point.lon)]);
            const map = leaflet.map(mapElement, { preferCanvas: true, scrollWheelZoom: false });
            roverHistorySessionMaps.set(String(sessionId), map);
            addMapTiles(leaflet, map);
            const line = coordinates.length > 1
                ? leaflet.polyline(coordinates, { color: '#22d3ee', weight: 4, opacity: 0.9 }).addTo(map)
                : null;
            coordinates.forEach((coordinate, index) => {
                const { point, index: originalIndex } = validPoints[index];
                const isEndpoint = index === 0 || index === validPoints.length - 1;
                leaflet.circleMarker(coordinate, {
                    radius: isEndpoint ? 7 : 5,
                    color: index === 0 ? '#34d399' : index === validPoints.length - 1 ? '#fb7185' : '#e0f2fe',
                    fillColor: index === 0 ? '#34d399' : index === validPoints.length - 1 ? '#fb7185' : '#0ea5e9',
                    fillOpacity: 0.95,
                    weight: 1
                }).addTo(map).bindPopup(`<strong>Điểm ${originalIndex + 1}</strong><br>${formatDateTime(point.recorded_at)}<br>${Number(point.lat).toFixed(7)}, ${Number(point.lon).toFixed(7)}<br>${escapeHtml(roverFixQualityLabel(point.fix_quality))} · ${point.satellites == null ? 'Không rõ số vệ tinh' : `${escapeHtml(point.satellites)} vệ tinh`}`);
            });
            if (line) map.fitBounds(line.getBounds(), { padding: [28, 28], maxZoom: 19 });
            else map.setView(coordinates[0], 17);
            requestAnimationFrame(() => map.invalidateSize());
            return map;
        } catch (error) {
            mapElement.innerHTML = '<div class="rover-activity-empty">Không thể dựng bản đồ.</div>';
            console.warn('Could not render Rover session map', error);
            return null;
        }
    }

    function roverHistoryGoogleMapsRoutes(points) {
        const coordinates = (Array.isArray(points) ? points : []).flatMap(point => {
            if (point?.lat == null || point?.lon == null || point.lat === '' || point.lon === '') return [];
            const latitude = Number(point.lat);
            const longitude = Number(point.lon);
            if (!Number.isFinite(latitude) || !Number.isFinite(longitude)
                || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180
                || (latitude === 0 && longitude === 0)) return [];
            return [`${latitude.toFixed(7)},${longitude.toFixed(7)}`];
        });
        if (coordinates.length < 2) return '';

        const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || '');
        const pointsPerRoute = (isMobile ? 3 : 9) + 2;
        const segments = [];
        for (let start = 0; start < coordinates.length - 1;) {
            const end = Math.min(start + pointsPerRoute - 1, coordinates.length - 1);
            const segment = coordinates.slice(start, end + 1);
            const query = new URLSearchParams({
                api: '1',
                origin: segment[0],
                destination: segment[segment.length - 1],
                travelmode: 'driving'
            });
            if (segment.length > 2) query.set('waypoints', segment.slice(1, -1).join('|'));
            segments.push({
                href: `https://www.google.com/maps/dir/?${query.toString()}`,
                firstPoint: start + 1,
                lastPoint: end + 1,
            });
            start = end;
        }

        const links = segments.map((segment, index) => {
            const label = segments.length === 1
                ? `Nối ${coordinates.length} điểm trên Google Maps`
                : `Tuyến ${index + 1}/${segments.length} · điểm ${segment.firstPoint}–${segment.lastPoint}`;
            return `<a class="rover-history-map-link rover-history-route-link" href="${segment.href}" target="_blank" rel="noopener noreferrer" aria-label="${escapeHtml(label)}"><i class="bi bi-map" aria-hidden="true"></i>${escapeHtml(label)}</a>`;
        }).join('');
        const note = segments.length > 1
            ? `<span class="rover-history-route-note">Các tuyến nối tiếp nhau tại điểm cuối mỗi chặng.</span>`
            : '';
        return `${links}${note}`;
    }

    function roverHistoryPaginationHtml(totalPages, page, username, dateValue) {
        if (totalPages <= 1) return '';
        const pages = new Set([1, totalPages]);
        for (let value = Math.max(1, page - 2); value <= Math.min(totalPages, page + 2); value += 1) pages.add(value);
        let previousPage = 0;
        const buttons = Array.from(pages).sort((a, b) => a - b).map(value => {
            const gap = value - previousPage > 1 ? '<span aria-hidden="true">…</span>' : '';
            previousPage = value;
            return `${gap}<button type="button" class="rover-history-page-button" data-username="${escapeHtml(username)}" data-date="${escapeHtml(dateValue)}" data-page="${value}" ${value === page ? 'aria-current="page"' : ''} aria-label="Trang ${value}">${value}</button>`;
        }).join('');
        return `<nav class="rover-history-pagination" aria-label="Trang phiên Rover">
            <button type="button" class="rover-history-page-button" data-username="${escapeHtml(username)}" data-date="${escapeHtml(dateValue)}" data-page="${Math.max(1, page - 1)}" ${page <= 1 ? 'disabled' : ''} aria-label="Trang trước"><i class="bi bi-chevron-left" aria-hidden="true"></i></button>
            ${buttons}
            <button type="button" class="rover-history-page-button" data-username="${escapeHtml(username)}" data-date="${escapeHtml(dateValue)}" data-page="${Math.min(totalPages, page + 1)}" ${page >= totalPages ? 'disabled' : ''} aria-label="Trang sau"><i class="bi bi-chevron-right" aria-hidden="true"></i></button>
        </nav>`;
    }

    function renderRoverHistoryPositionHtml(position) {
        const hasPosition = position
            && position.lat !== null && position.lat !== ''
            && position.lon !== null && position.lon !== ''
            && Number.isFinite(Number(position.lat))
            && Number.isFinite(Number(position.lon))
            && !(Number(position.lat) === 0 && Number(position.lon) === 0);
        if (!hasPosition) {
            return '<div class="rover-history-no-position"><i class="bi bi-geo" aria-hidden="true"></i><span>Chưa ghi nhận tọa độ hợp lệ cho phiên này.</span></div>';
        }
        const latitude = String(position.lat);
        const longitude = String(position.lon);
        const positionFixLabel = roverFixQualityLabel(position.fix_quality);
        const satellitesLabel = position.satellites == null ? 'Không rõ số vệ tinh' : `${escapeHtml(position.satellites)} vệ tinh`;
        return `<div class="rover-history-position">
                <div class="rover-history-position-heading">
                    <span><i class="bi bi-crosshair2" aria-hidden="true"></i> Vị trí hợp lệ gần nhất · ${escapeHtml(positionFixLabel)}</span>
                    <a class="rover-history-map-link" href="https://www.google.com/maps/search/?api=1&amp;query=${latitude}%2C${longitude}" target="_blank" rel="noopener noreferrer" aria-label="Mở vị trí ${latitude}, ${longitude} trên Google Maps"><i class="bi bi-map" aria-hidden="true"></i><span>Google Maps</span></a>
                </div>
                <div class="rover-history-coordinate-grid">
                    <div class="rover-history-coordinate">
                        <div class="rover-history-coordinate-heading"><span>Vĩ độ</span><button class="rover-history-copy" type="button" data-copy-value="${latitude}" aria-label="Sao chép vĩ độ" title="Sao chép vĩ độ"><i class="bi bi-copy" aria-hidden="true"></i></button></div>
                        <strong>${latitude}</strong>
                    </div>
                    <div class="rover-history-coordinate">
                        <div class="rover-history-coordinate-heading"><span>Kinh độ</span><button class="rover-history-copy" type="button" data-copy-value="${longitude}" aria-label="Sao chép kinh độ" title="Sao chép kinh độ"><i class="bi bi-copy" aria-hidden="true"></i></button></div>
                        <strong>${longitude}</strong>
                    </div>
                </div>
                <div class="rover-history-recorded"><i class="bi bi-clock" aria-hidden="true"></i> Ghi nhận lúc ${formatDateTime(position.recorded_at)} · ${satellitesLabel}</div>
            </div>`;
    }

    function renderRoverHistoryPointHtml(point, {isAdmin, selectedDate, currentPage, sessionId}) {
        const pointLatitude = String(point.lat);
        const pointLongitude = String(point.lon);
        const pointMapQuery = encodeURIComponent(`${pointLatitude},${pointLongitude}`);
        const deletePointButton = isAdmin
            ? `<button class="rover-history-action-button rover-history-delete-point" type="button" data-point-id="${escapeHtml(point.id)}" data-session-id="${sessionId}" data-date="${escapeHtml(selectedDate)}" data-page="${currentPage}" aria-label="Xóa điểm ${escapeHtml(point.label || 'vị trí')}"><i class="bi bi-trash3" aria-hidden="true"></i><span>Xóa điểm</span></button>`
            : '';
        return `<article class="rover-history-point">
            <div class="rover-history-point-heading">
                <div><strong>${escapeHtml(point.label || 'Điểm đo')}</strong><span class="rover-history-point-fix">${escapeHtml(roverFixQualityLabel(point.fix_quality))}</span></div>
                <div class="rover-history-point-actions">
                    <a class="rover-history-map-link" href="https://www.google.com/maps/search/?api=1&amp;query=${pointMapQuery}" target="_blank" rel="noopener noreferrer" aria-label="Mở điểm ${escapeHtml(point.label || 'vị trí')} trên Google Maps"><i class="bi bi-map" aria-hidden="true"></i><span>Bản đồ</span></a>
                    ${deletePointButton}
                </div>
            </div>
            <div class="rover-history-coordinate-grid">
                <div class="rover-history-coordinate"><div class="rover-history-coordinate-heading"><span>Vĩ độ</span><button class="rover-history-copy" type="button" data-copy-value="${pointLatitude}" aria-label="Sao chép vĩ độ điểm đo" title="Sao chép vĩ độ"><i class="bi bi-copy" aria-hidden="true"></i></button></div><strong>${pointLatitude}</strong></div>
                <div class="rover-history-coordinate"><div class="rover-history-coordinate-heading"><span>Kinh độ</span><button class="rover-history-copy" type="button" data-copy-value="${pointLongitude}" aria-label="Sao chép kinh độ điểm đo" title="Sao chép kinh độ"><i class="bi bi-copy" aria-hidden="true"></i></button></div><strong>${pointLongitude}</strong></div>
            </div>
            <div class="rover-history-point-meta"><span><i class="bi bi-clock" aria-hidden="true"></i> Đo lúc ${formatDateTime(point.recorded_at)}</span><span><i class="bi bi-broadcast" aria-hidden="true"></i> ${point.satellites == null ? 'Không rõ số vệ tinh' : `${escapeHtml(point.satellites)} vệ tinh`}</span><span><i class="bi bi-save" aria-hidden="true"></i> Lưu lúc ${formatDateTime(point.saved_at)}</span><span><i class="bi bi-person" aria-hidden="true"></i> Lưu bởi ${escapeHtml(point.created_by || 'admin')}</span></div>
        </article>`;
    }

    function renderRoverHistoryPointsHtml(points, options) {
        if (!points.length) {
            return '<div class="rover-history-empty rover-history-no-points"><i class="bi bi-geo" aria-hidden="true"></i><span>Chưa lưu điểm đo nào trong phiên này.</span></div>';
        }
        return `<div class="rover-history-point-list">${points.map(point => renderRoverHistoryPointHtml(point, options)).join('')}</div>`;
    }

    function renderRoverHistorySessionMapHtml(session, points, routeHtml) {
        if (!points.length) return '';
        return `<div class="rover-history-session-map" hidden>
                <div class="rover-history-session-map-canvas" role="region" aria-label="Bản đồ nối toàn bộ điểm vị trí trong phiên ${escapeHtml(session.id)}"></div>
                <div class="rover-history-session-map-directions"><span>Đường xanh nối các điểm vị trí theo thứ tự đo. Điểm gần nhau có thể chồng; bấm + để phóng to.</span></div>
                ${routeHtml ? `<div class="rover-history-session-map-directions"><span>Chỉ đường Google Maps:</span>${routeHtml}</div>` : ''}
            </div>`;
    }

    function renderRoverHistorySessionDeleteHtml(session, ended, isAdmin, selectedDate, currentPage) {
        if (!isAdmin) return '';
        if (!ended) return '<span class="rover-history-delete-note">Phiên đang chạy chỉ xóa được sau khi kết thúc.</span>';
        return `<button class="rover-history-action-button rover-history-delete-session" type="button" data-session-id="${session.id}" data-date="${escapeHtml(selectedDate)}" data-page="${currentPage}"><i class="bi bi-trash3" aria-hidden="true"></i><span>Xóa phiên</span></button>`;
    }

    function renderRoverHistorySessionHtml(session, options) {
        const {isAdmin, selectedDate, currentPage, autoSaveFixedPointsEnabled, autoPointIntervalLabel} = options;
        const ended = Boolean(session.ended_at);
        const points = Array.isArray(session.points) ? session.points : [];
        const currentFixQuality = session.current_fix_quality == null ? Number.NaN : Number(session.current_fix_quality);
        const currentFixLabel = Number.isFinite(currentFixQuality) ? roverFixQualityLabel(currentFixQuality) : 'Chưa có dữ liệu';
        const positionHtml = renderRoverHistoryPositionHtml(session.last_position);
        const pointsHtml = renderRoverHistoryPointsHtml(points, {...options, sessionId: session.id});
        const routeHtml = roverHistoryGoogleMapsRoutes(points);
        const showSessionMapHtml = points.length
            ? `<button class="rover-history-action-button rover-history-show-route" type="button" data-session-id="${escapeHtml(session.id)}" aria-expanded="false"><i class="bi bi-map" aria-hidden="true"></i><span>Xem toàn bộ trên bản đồ</span></button>`
            : '';
        const sessionMapHtml = renderRoverHistorySessionMapHtml(session, points, routeHtml);
        const deleteSessionHtml = renderRoverHistorySessionDeleteHtml(session, ended, isAdmin, selectedDate, currentPage);
        const satellitesLabel = session.satellites == null ? 'Không rõ' : escapeHtml(session.satellites);
        const uptimeLabel = session.uptime_seconds == null ? 'Không rõ' : escapeHtml(formatDuration(session.uptime_seconds));
        const updatedLabel = formatDateTime(ended ? session.ended_at : session.last_seen_at);
        return `<article class="rover-history-session ${ended ? 'is-ended' : 'is-active'}">
            <div class="rover-history-session-heading">
                <div class="min-w-0">
                    <span class="rover-history-label">Trạm Base</span>
                    <h6>${escapeHtml(session.base_name || 'Không rõ')}</h6>
                    <div class="small text-muted mt-1">Mountpoint · ${escapeHtml(session.mountpoint || 'Không rõ')}</div>
                </div>
                <span class="rover-history-status ${ended ? 'is-ended' : 'is-active'}"><i aria-hidden="true"></i>${ended ? 'Đã kết thúc' : 'Đang hoạt động'}</span>
            </div>
            <div class="rover-history-meta">
                <div><span>Máy chủ</span><strong>${escapeHtml(session.server || 'Caster')}</strong></div>
                <div><span>Receiver</span><strong>${escapeHtml(session.receiver_id || 'Không rõ')}</strong></div>
                <div><span>Client</span><strong>${escapeHtml(session.client_id || 'Không rõ')}</strong></div>
                <div><span>Bắt đầu</span><strong>${formatDateTime(session.started_at)}</strong></div>
                <div><span>Fix gần nhất</span><strong>${escapeHtml(currentFixLabel)}</strong></div>
                <div><span>Vệ tinh gần nhất</span><strong>${satellitesLabel}</strong></div>
                <div><span>Uptime</span><strong>${uptimeLabel}</strong></div>
                <div><span>${ended ? 'Kết thúc' : 'Cập nhật cuối'}</span><strong>${updatedLabel}</strong></div>
            </div>
            ${positionHtml}
            <section class="rover-history-points" aria-label="Các điểm vị trí trong phiên ${session.id}">
                <div class="rover-history-points-heading"><strong>Điểm đo đã lưu</strong><div class="rover-history-route-tools"><span>${points.length} điểm</span>${showSessionMapHtml}</div></div>
                ${ended ? '' : `<p class="rover-history-auto-save-note">${autoSaveFixedPointsEnabled ? `Tự lưu điểm vị trí đang bật, theo chu kỳ ${autoPointIntervalLabel}.` : 'Tự lưu điểm vị trí đang tắt cho tài khoản này.'}</p>`}
                ${sessionMapHtml}
                ${pointsHtml}
            </section>
            ${deleteSessionHtml ? `<div class="rover-history-session-actions">${deleteSessionHtml}</div>` : ''}
        </article>`;
    }

    async function handleRoverHistory(e) {
        const username = e.currentTarget.dataset.username;
        if (!username) return;
        const selectedDate = e.currentTarget.dataset.date || '';
        const requestedPage = Math.max(1, Number(e.currentTarget.dataset.page) || 1);
        const requestId = ++roverHistoryRequest;
        const modalEl = ensureRoverHistoryModal();
        if (!modalEl) return;
        const roverName = modalEl.querySelector('#rover-history-rover-name');
        const body = modalEl.querySelector('#rover-history-content');
        if (roverName) roverName.textContent = username;
        if (body) {
            clearRoverHistorySessionMaps();
            body.scrollTop = 0;
            body.setAttribute('aria-busy', 'true');
            body.innerHTML = '<div class="rover-history-empty" role="status"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Đang tải lịch sử…</span></div>';
        }
        if (window.bootstrap) bootstrap.Modal.getOrCreateInstance(modalEl).show();
        try {
            const historyQuery = new URLSearchParams({ page: String(requestedPage) });
            if (selectedDate) historyQuery.set('date', selectedDate);
            const response = await apiFetchRaw(`${window.ApiRoutes.rovers.history(username)}?${historyQuery}`, { headers: API_HEADERS });
            const data = await response.json();
            if (requestId !== roverHistoryRequest) return;
            if (!response.ok) throw new Error(data.detail || `Không tải được lịch sử (${response.status}).`);
            if (body) {
                const autoPointIntervalSeconds = Number(data.fixed_point_auto_interval_seconds) || 120;
                const autoPointIntervalLabel = autoPointIntervalSeconds % 60 === 0
                    ? `${autoPointIntervalSeconds / 60} phút`
                    : `${autoPointIntervalSeconds} giây`;
                const autoSaveFixedPointsEnabled = data.auto_save_fixed_points === true;
                const events = data.events || [];
                const statuses = data.caster_status || [];
                const connections = statuses.flatMap(item => item.active_connections || []);
                const sessions = data.sessions || [];
                const historyPointsBySession = new Map(sessions.map(session => [String(session.id), Array.isArray(session.points) ? session.points : []]));
                const totalSessions = Number(data.total_sessions) || 0;
                const receiverCount = Number(data.receiver_count) || 0;
                const totalPages = Number(data.total_pages) || 0;
                const currentPage = Number(data.page) || 1;
                const isAdmin = String(currentUser?.role || '').toLowerCase() === 'admin';
                const accountName = escapeHtml(data.account?.username || data.username || username);
                const sessionHistoryHtml = sessions.length
                    ? sessions.map(session => renderRoverHistorySessionHtml(session, {
                        isAdmin,
                        selectedDate,
                        currentPage,
                        autoSaveFixedPointsEnabled,
                        autoPointIntervalLabel,
                    })).join('')
                    : '<div class="rover-history-empty"><i class="bi bi-clock-history" aria-hidden="true"></i><span>Chưa có phiên nào được ghi nhận.</span></div>';
                const eventHistoryHtml = events.length ? events.map(item => {
                    const level = String(item.severity || item.type || '').toLowerCase();
                    const tone = ['critical', 'error', 'danger'].includes(level) ? 'danger'
                        : level === 'warning' ? 'warning'
                        : level === 'success' ? 'success' : 'info';
                    const labels = { critical: 'Khẩn cấp', error: 'Lỗi', danger: 'Lỗi', warning: 'Cảnh báo', success: 'Thành công', info: 'Thông tin' };
                    return `<article class="rover-history-event is-${tone}">
                        <span class="rover-history-event-time"><i class="bi bi-clock" aria-hidden="true"></i>${formatDateTime(item.timestamp)}</span>
                        <div class="rover-history-event-copy"><strong>${escapeHtml(labels[level] || item.type || 'Thông tin')}</strong><p>${escapeHtml(item.message || '')}</p></div>
                    </article>`;
                }).join('') : '<div class="rover-history-empty"><i class="bi bi-inbox" aria-hidden="true"></i><span>Chưa có sự kiện nào được ghi nhận.</span></div>';

                body.innerHTML = `
                    <div class="row g-3 rover-history-summary" aria-label="Tổng quan Rover">
                        <div class="col-6 col-xl-3"><div class="rover-history-stat"><div><span>Tên tài khoản</span><strong title="${accountName}">${accountName}</strong></div><i class="bi bi-person-vcard" aria-hidden="true"></i></div></div>
                        <div class="col-6 col-xl-3"><div class="rover-history-stat"><div><span>Phiên hoạt động</span><strong>${connections.length}</strong></div><i class="bi bi-activity" aria-hidden="true"></i></div></div>
                        <div class="col-6 col-xl-3"><div class="rover-history-stat"><div><span>Số receiver</span><strong>${receiverCount}</strong></div><i class="bi bi-router" aria-hidden="true"></i></div></div>
                        <div class="col-6 col-xl-3"><div class="rover-history-stat"><div><span>Tổng số phiên</span><strong>${totalSessions}</strong></div><i class="bi bi-clock-history" aria-hidden="true"></i></div></div>
                    </div>
                    <form class="rover-history-filter" id="rover-history-filter">
                        <div class="rover-history-filter-fields">
                            <label for="rover-history-date">Lọc phiên theo ngày<input id="rover-history-date" type="date" value="${escapeHtml(selectedDate)}"></label>
                            <button class="rover-history-action-button" type="submit"><i class="bi bi-funnel" aria-hidden="true"></i><span>Lọc</span></button>
                            <button class="rover-history-action-button" type="button" id="rover-history-clear-date"><i class="bi bi-calendar3" aria-hidden="true"></i><span>Tất cả ngày</span></button>
                        </div>
                        <span class="rover-history-filter-summary">${selectedDate ? `Ngày ${escapeHtml(selectedDate)}` : 'Tất cả ngày'} · ${totalSessions} phiên · ${receiverCount} receiver</span>
                    </form>
                    <section class="rover-history-section" aria-labelledby="rover-history-sessions-title">
                        <div class="rover-history-section-heading">
                            <div>
                                <h6 id="rover-history-sessions-title"><i class="bi bi-geo-alt" aria-hidden="true"></i>Vị trí cuối theo phiên</h6>
                                <p>Tọa độ gần nhất rover gửi trong mỗi phiên kết nối.</p>
                            </div>
                            <span class="rover-history-count">${totalSessions} phiên · ${receiverCount} receiver</span>
                        </div>
                        <div class="rover-history-session-list">${sessionHistoryHtml}</div>
                        ${roverHistoryPaginationHtml(totalPages, currentPage, username, selectedDate)}
                    </section>
                    <section class="rover-history-section" aria-labelledby="rover-history-events-title">
                        <div class="rover-history-section-heading">
                            <div>
                                <h6 id="rover-history-events-title"><i class="bi bi-activity" aria-hidden="true"></i>Sự kiện gần đây</h6>
                                <p>Hoạt động mới nhất được ghi nhận cho rover này.</p>
                            </div>
                        </div>
                        <div class="rover-history-event-list">${eventHistoryHtml}</div>
                    </section>
                `;
                body.scrollTop = 0;
                body.setAttribute('aria-busy', 'false');
                body.querySelector('#rover-history-filter')?.addEventListener('submit', event => {
                    event.preventDefault();
                    handleRoverHistory({ currentTarget: { dataset: { username, date: body.querySelector('#rover-history-date')?.value || '', page: '1' } } }).catch(error => console.error('handleRoverHistory failed:', error));
                });
                body.querySelector('#rover-history-clear-date')?.addEventListener('click', () => {
                    handleRoverHistory({ currentTarget: { dataset: { username, date: '', page: '1' } } }).catch(error => console.error('handleRoverHistory failed:', error));
                });
                body.querySelectorAll('.rover-history-page-button').forEach(button => {
                    button.addEventListener('click', () => handleRoverHistory({ currentTarget: { dataset: button.dataset } }));
                });
                body.querySelectorAll('.rover-history-show-route').forEach(button => {
                    button.addEventListener('click', () => {
                        const pointsSection = button.closest('.rover-history-points');
                        const mapPanel = pointsSection?.querySelector('.rover-history-session-map');
                        const mapCanvas = mapPanel?.querySelector('.rover-history-session-map-canvas');
                        if (!mapPanel || !mapCanvas) return;
                        mapPanel.hidden = !mapPanel.hidden;
                        button.setAttribute('aria-expanded', String(!mapPanel.hidden));
                        if (mapPanel.hidden) return;

                        const sessionId = String(button.dataset.sessionId || '');
                        const map = roverHistorySessionMaps.get(sessionId)
                            || renderRoverHistorySessionMap(sessionId, mapCanvas, historyPointsBySession.get(sessionId) || []);
                        requestAnimationFrame(() => {
                            map?.invalidateSize();
                            mapPanel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                        });
                    });
                });
                body.querySelectorAll('.rover-history-copy').forEach(button => {
                    button.addEventListener('click', async () => {
                        try {
                            await navigator.clipboard.writeText(button.dataset.copyValue || '');
                            showToast('Đã sao chép tọa độ', 'success');
                        } catch {
                            showToast('Không thể sao chép tọa độ trên trình duyệt này.', 'danger');
                        }
                    });
                });
                body.querySelectorAll('.rover-history-delete-session').forEach(button => {
                    button.addEventListener('click', async () => {
                        const sessionId = button.dataset.sessionId;
                        const ok = await window.CorsUI.confirmAction({
                            title: 'Xóa phiên Rover',
                            message: 'Phiên này và toàn bộ điểm đo đã lưu bên trong sẽ bị xóa vĩnh viễn.',
                            confirmText: 'Xóa phiên',
                            tone: 'danger'
                        });
                        if (!ok) return;
                        const buttonContent = button.innerHTML;
                        button.disabled = true;
                        button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Đang xóa…</span>';
                        try {
                            const response = await apiFetchRaw(window.ApiRoutes.rovers.historySession(username, sessionId), {
                                method: 'DELETE',
                                headers: API_HEADERS
                            });
                            const result = await response.json().catch(() => ({}));
                            if (!response.ok) throw new Error(result.detail || `Không xóa được phiên (${response.status}).`);
                            showToast('Đã xóa phiên Rover.', 'success');
                            await handleRoverHistory({ currentTarget: { dataset: { username, date: button.dataset.date, page: button.dataset.page } } });
                        } catch (error) {
                            button.disabled = false;
                            button.innerHTML = buttonContent;
                            showToast(error.message, 'danger');
                        }
                    });
                });
                body.querySelectorAll('.rover-history-delete-point').forEach(button => {
                    button.addEventListener('click', async () => {
                        const pointId = button.dataset.pointId;
                        const sessionId = button.dataset.sessionId;
                        const ok = await window.CorsUI.confirmAction({
                            title: 'Xóa điểm FIX',
                            message: 'Điểm đo này sẽ bị xóa vĩnh viễn khỏi phiên Rover.',
                            confirmText: 'Xóa điểm',
                            tone: 'danger'
                        });
                        if (!ok) return;
                        const buttonContent = button.innerHTML;
                        button.disabled = true;
                        button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Đang xóa…</span>';
                        try {
                            const sessionUrl = window.ApiRoutes.rovers.historySession(username, sessionId);
                            const response = await apiFetchRaw(`${sessionUrl}/points/${encodeURIComponent(pointId)}`, {
                                method: 'DELETE',
                                headers: API_HEADERS
                            });
                            const result = await response.json().catch(() => ({}));
                            if (!response.ok) throw new Error(result.detail || `Không xóa được điểm (${response.status}).`);
                            showToast('Đã xóa điểm FIX.', 'success');
                            await handleRoverHistory({ currentTarget: { dataset: { username, date: button.dataset.date, page: button.dataset.page } } });
                        } catch (error) {
                            button.disabled = false;
                            button.innerHTML = buttonContent;
                            showToast(error.message, 'danger');
                        }
                    });
                });
            }
        } catch (error) {
            if (requestId !== roverHistoryRequest) return;
            if (body) {
                body.setAttribute('aria-busy', 'false');
                body.innerHTML = `<div class="rover-history-empty text-danger" role="alert">${escapeHtml(error.message)}</div>`;
            }
        }
    }

    async function bulkRenewSelectedRovers() {
        const selected = getSelectedRovers().map(r => r.username).filter(Boolean);
        const expiryDays = Number(document.getElementById('bulk-renew-days')?.value || 365);
        if (!selected.length) return;
        const ok = await window.CorsUI.confirmAction({
            title: 'Bulk renew rovers',
            message: `Renew ${selected.length} selected rover(s) for ${expiryDays} day(s)?`,
            confirmText: 'Renew'
        });
        if (!ok) return;
        const response = await apiFetchRaw(window.ApiRoutes.rovers.bulkRenew, {
            method: 'POST',
            headers: API_HEADERS,
            body: JSON.stringify({ usernames: selected, expiry_days: expiryDays })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `Bulk renew failed: ${response.status}`);
        window.CorsUI.showToast('success', `Renewed ${data.updated?.length || 0} rover(s)`);
        await refreshDataInPlace();
    }

    async function syncSelectedRovers() {
        const selected = getSelectedRovers().map(r => r.username).filter(Boolean);
        if (!selected.length) return;
        const response = await apiFetchRaw(window.ApiRoutes.rovers.syncStatus, {
            method: 'POST',
            headers: API_HEADERS,
            body: JSON.stringify({ usernames: selected })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `Sync failed: ${response.status}`);
        window.CorsUI.showToast('success', `Synced ${data.count || 0} rover status record(s)`);
        await refreshDataInPlace();
    }

    function exportSelectedRovers() {
        const selected = getSelectedRovers();
        if (!selected.length) return;
        const headers = ['username', 'password', 'mountpoints', 'base_name', 'expires_at', 'is_active'];
        const rows = selected.map(r => headers.map(h => `"${String(r[h] ?? '').replace(/"/g, '""')}"`).join(','));
        const blob = new Blob([[headers.join(','), ...rows].join('\n')], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `rovers-selected-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    async function validateCreateRoverInputs() {
        const username = document.getElementById('new-username')?.value?.trim();
        const mountpoints = document.getElementById('new-mountpoints')?.value?.trim();
        const box = document.getElementById('new-rover-validation-msg');
        if (!box || !username) {
            if (box) box.classList.add('d-none');
            return;
        }
        const params = new URLSearchParams({ username });
        if (mountpoints) params.set('mountpoints', mountpoints);
        try {
            const response = await apiFetchRaw(`${window.ApiRoutes.rovers.validate}?${params.toString()}`, { headers: API_HEADERS });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Validation failed: ${response.status}`);
            const warnings = [];
            if (data.exists_db) warnings.push('Username already exists in database.');
            if (data.exists_caster) warnings.push('Username already exists on caster.');
            if ((data.mountpoint_conflicts || []).length) warnings.push('Mountpoint overlaps another rover.');
            box.className = warnings.length ? 'alert alert-warning py-2 small' : 'alert alert-success py-2 small';
            box.textContent = warnings.length ? warnings.join(' ') : 'Username and mountpoints look available.';
            box.classList.remove('d-none');
        } catch (error) {
            box.className = 'alert alert-danger py-2 small';
            box.textContent = error.message;
            box.classList.remove('d-none');
        }
    }

    // === UNIFIED RENDER TABLE ===
    function renderTable() {
        const tbody = document.getElementById('rovers-table-body');
        if (!tbody) return;

        currentRenderedConnectionKeys = new Set();

        const searchTerm = document.getElementById('search-box')?.value.toLowerCase() || '';
        const stationFilter = document.getElementById('filter-station')?.value || '';
        const statusFilter = document.getElementById('filter-status')?.value || '';

        tbody.innerHTML = '';

        // 1. Prepare merged data
        let mergedData = allRovers.map(r => ({
            ...r,
            type: 'db',
            caster_infos: getCasterInfosForUsername(r.username)
        }));

        casterUsers.forEach(cu => {
            if (!allRovers.find(r => normalizeUsername(r.username) === normalizeUsername(cu.username))) {
                mergedData.push({
                    username: cu.username,
                    password: cu.password || '',
                    type: 'unknown',
                    caster_info: cu,
                    mountpoints: cu.mountpoints.join(';'),
                    base_name: 'Caster Only User',
                    is_active: false
                });
            }
        });
        latestMergedRovers = mergedData;

        // 2. Filtering
        const filtered = mergedData.filter(r => {
            const matchSearch = r.username.toLowerCase().includes(searchTerm) || 
                                (r.base_serial && r.base_serial.toLowerCase().includes(searchTerm)) ||
                                (r.base_name && r.base_name.toLowerCase().includes(searchTerm));
            
            // 🔥 FIXED: Lọc trạm Base thông minh cho cả DB và Unknown Rover
            let matchStation = true;
            if (stationFilter) {
                const targetDevice = allDevices.find(d => d.serial === stationFilter);
                const hasSerialMatch = r.base_serial && r.base_serial.split(',').map(s => s.trim()).includes(stationFilter);
                
                const allowedMps = (r.mountpoints || '').split(';').map(m => m.trim().toLowerCase());
                const hasMpMatch = targetDevice && targetDevice.mountpoint && allowedMps.includes(targetDevice.mountpoint.toLowerCase());
                
                matchStation = hasSerialMatch || hasMpMatch;
            }
            
            // 🔥 FIXED: Lọc trạng thái (Status) chính xác cho cả DB và Unknown Rover
            const infos = r.caster_infos || (r.caster_info ? [r.caster_info] : []);
            const isBlocked = infos.some(i => i?.is_blocked);
            const isOnline = infos.some(i => i?.nclient > 0);
            
            const liveExpiry = infos.find(i => i?.status != null && Number.isFinite(Number(i.status)));
            const isExpired = liveExpiry
                ? Number(liveExpiry.status) <= 0 || (
                    Number(liveExpiry.status) < 315360000 && Number(liveExpiry.expiry_deadline) <= Date.now() / 1000
                )
                : Boolean(r.expires_at && Number(r.expires_at) <= Date.now() / 1000);
            
            let matchStatus = true;
            if (statusFilter === 'active') matchStatus = isOnline;
            else if (statusFilter === 'blocked') matchStatus = isBlocked;
            else if (statusFilter === 'expired') matchStatus = isExpired;
            else if (statusFilter === 'standby') matchStatus = !isOnline && !isBlocked && !isExpired;
            if (showExpiringSoonOnly) matchStatus = matchStatus && isRoverExpiringSoon(r);

            return matchSearch && matchStation && matchStatus;
        });

        // 3. Update Counts & Stats
        if (document.getElementById('displayed-count')) document.getElementById('displayed-count').textContent = filtered.length;
        if (document.getElementById('total-rovers')) document.getElementById('total-rovers').textContent = mergedData.length;

        // Premium Stats
        if (document.getElementById('stat-total')) document.getElementById('stat-total').textContent = mergedData.length;
        if (document.getElementById('stat-indb')) document.getElementById('stat-indb').textContent = allRovers.length;
        if (document.getElementById('stat-unknown')) document.getElementById('stat-unknown').textContent = mergedData.filter(r => r.type === 'unknown').length;
        if (document.getElementById('stat-blocked')) document.getElementById('stat-blocked').textContent = mergedData.filter(r => (r.caster_infos || [r.caster_info]).some(i => i?.is_blocked)).length;


        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="11" class="text-center py-5 text-muted">Không tìm thấy dữ liệu phù hợp.</td></tr>`;
            updateRoverBulkToolbar();
            return;
        }

        // 4. Render Rows
        filtered.forEach(r => {
            tbody.appendChild(createRoverRowElement(r));
        });

        for (const key of Array.from(connectionClockCache.keys())) {
            if (!currentRenderedConnectionKeys.has(key)) {
                connectionClockCache.delete(key);
            }
        }

        // Re-bind events
        document.querySelectorAll('.delete-btn').forEach(btn => btn.onclick = handleDeleteRover);
        document.querySelectorAll('.edit-btn').forEach(btn => btn.onclick = handleEditRover);
        document.querySelectorAll('.caster-block-btn').forEach(btn => btn.onclick = handleBlockUser);
        document.querySelectorAll('.caster-unblock-btn').forEach(btn => btn.onclick = handleUnblockUser);
        document.querySelectorAll('.caster-delete-btn').forEach(btn => btn.onclick = handleDeleteCasterUser);
        document.querySelectorAll('.rover-history-btn').forEach(btn => btn.onclick = handleRoverHistory);
        bindRoverSelectionEvents();
        updateRoverBulkToolbar();
    }

    // === HANDLE EDIT ROVER (LOGIC MỚI) ===
    function handleEditRover(e) {
        const id = Number.parseInt(e.currentTarget.dataset.id);
        const username = e.currentTarget.dataset.username;
        
        let rover = allRovers.find(r => r.id === id);
        if (!rover && username) {
            rover = allRovers.find(r => normalizeUsername(r.username) === normalizeUsername(username));
        }
        
        // Nếu không có ID trong DB (Unknown), tìm trong danh sách Caster
        if (!rover && username) {
            const cu = casterUsers.find(u => u.username === username);
            if (cu) {
                rover = {
                    id: 0,
                    username: cu.username,
                    password: cu.password,
                    mountpoints: cu.mountpoints.join(';'),
                    show_position: cu.show_position,
                    auto_save_sessions: true,
                    auto_save_fixed_points: false,
                    max_connections: (typeof cu.maxclient !== 'undefined' && cu.maxclient !== null) ? cu.maxclient : 1
                };
            }
        }
        
        if (!rover) return;

        // 1. Điền thông tin cơ bản
        document.getElementById('edit-rover-id').value = rover.id;
        document.getElementById('edit-username').value = rover.username;
        
        // Hiển thị mật khẩu hiện tại trong modal sửa
        document.getElementById('edit-password').value = rover.password || ''; 
        const expirySelect = document.getElementById('edit-expiry');
        
        // Cập nhật text hiển thị của tùy chọn "Giữ nguyên" (options[0])
        const liveExpiry = getCasterInfosForUsername(rover.username).find(i => Number.isFinite(Number(i?.status)));
        const remainingSeconds = liveExpiry
            ? Number(liveExpiry.status)
            : (rover.expires_at ? rover.expires_at - Date.now() / 1000 : null);
        if (remainingSeconds !== null) {
            const remainingDays = Math.ceil(remainingSeconds / 86400);
            if (remainingDays >= 3650) {
                expirySelect.options[0].text = '--- Giữ nguyên (Vĩnh viễn) ---';
            } else if (remainingDays > 0) {
                expirySelect.options[0].text = '--- Giữ nguyên (Còn ' + remainingDays + ' ngày) ---';
            } else {
                expirySelect.options[0].text = '--- Giữ nguyên (Đã hết hạn) ---';
            }
        } else {
            expirySelect.options[0].text = '--- Giữ nguyên (Vĩnh viễn) ---';
        }

        expirySelect.value = '';
        syncExpiryFields('edit');
        const liveMaxClients = getCasterInfosForUsername(rover.username)
            .map(i => Number(i?.maxclient))
            .filter(v => Number.isFinite(v) && v >= 0);
        const roverMaxConnections = Number(rover.max_connections);
        document.getElementById('edit-max-connections').value = liveMaxClients.length > 0
            ? liveMaxClients[0]
            : (Number.isFinite(roverMaxConnections) && roverMaxConnections >= 0 ? roverMaxConnections : 1);
        const showPosToggle = document.getElementById('edit-show-position');
        const isSharedToggle = document.getElementById('edit-is-shared');
        const autoSaveSessionsToggle = document.getElementById('edit-auto-save-sessions');
        const autoSaveFixedPointsToggle = document.getElementById('edit-auto-save-fixed-points');
        if (isSharedToggle) isSharedToggle.checked = rover.is_shared_with_managers || false;
        if (autoSaveSessionsToggle) autoSaveSessionsToggle.checked = rover.auto_save_sessions !== false;
        if (autoSaveFixedPointsToggle) autoSaveFixedPointsToggle.checked = rover.auto_save_fixed_points === true;
        if (showPosToggle) {
            const liveInfos = getCasterInfosForUsername(rover.username);
            const hasLiveShowPos = liveInfos.some(i => typeof i?.show_position !== 'undefined');
            const effectiveShowPos = hasLiveShowPos
                ? liveInfos.every(i => i?.show_position !== false)
                : rover.show_position;
            showPosToggle.checked = effectiveShowPos !== false;
        }

        // Load RTCM fields
        const rtcmCycleToggle = document.getElementById('edit-rtcm-cycle-enabled');
        if (rtcmCycleToggle) {
            rtcmCycleToggle.checked = rover.rtcm_cycle_enabled || false;
            const intervalGroup = document.getElementById('edit-rtcm-cycle-interval-group');
            if (intervalGroup) {
                if (rtcmCycleToggle.checked) {
                    intervalGroup.classList.remove('d-none');
                } else {
                    intervalGroup.classList.add('d-none');
                }
            }
        }
        const rtcmIntervalInput = document.getElementById('edit-rtcm-disconnect-interval');
        if (rtcmIntervalInput) {
            rtcmIntervalInput.value = rover.rtcm_disconnect_interval_seconds || 300;
        }
        const rtcmDurationInput = document.getElementById('edit-rtcm-disconnect-duration');
        if (rtcmDurationInput) {
            rtcmDurationInput.value = rover.rtcm_disconnect_duration_seconds || 10;
        }

        // 1.5 Load Target Casters
        let targetCasters = parseCasterHosts(rover.target_casters);
        if (targetCasters.length === 0) {
            targetCasters = Array.from(
                new Set(
                    getCasterInfosForUsername(rover.username)
                        .map(i => String(i?.server || '').trim())
                        .filter(Boolean)
                )
            );
        }
        document.querySelectorAll('.edit-caster-target-check').forEach(cb => {
            cb.checked = targetCasters.includes(cb.value);
        });

        // 2. XỬ LÝ CHECKLIST TRẠM
        const container = document.getElementById('edit-station-checklist');
        container.innerHTML = ''; 
        
        const currentRoverMps = normalizeMountpointList(rover.mountpoints);
        document.getElementById('edit-mountpoints').value = (rover.mountpoints || '');

        if (allDevices.length === 0) {
            container.innerHTML = '<div class="text-center small text-muted">Không có trạm nào khả dụng.</div>';
        } else {
            allDevices.forEach(d => {
                const sConf = d.service_config || {};
                let mpsWithHosts = [];
                if (sConf.server1_enabled && sConf.mountpoint1) {
                    mpsWithHosts.push(`${sConf.mountpoint1}:${String(sConf.serverhost1 || '').trim()}`);
                }
                if (sConf.server2_enabled && sConf.mountpoint2) {
                    mpsWithHosts.push(`${sConf.mountpoint2}:${String(sConf.serverhost2 || '').trim()}`);
                }

                if (mpsWithHosts.length > 0) {
                    const deviceMpsOnly = mpsWithHosts.map(m => m.split(':')[0]);
                    const isChecked = deviceMpsOnly.some(mp => currentRoverMps.includes(mp));
                    
                    const div = document.createElement('div');
                    div.className = 'form-check';
                    div.innerHTML = `
                        <input class="form-check-input edit-station-check" type="checkbox" 
                            value="${d.serial}" 
                            id="edit-check-${d.serial}"
                            ${isChecked ? 'checked' : ''} 
                            data-mps="${mpsWithHosts.join(';')}">
                        <label class="form-check-label text-white small" for="edit-check-${d.serial}">
                            ${d.name} <span class="text-muted">(${deviceMpsOnly.join(',')})</span>
                        </label>
                    `;
                    container.appendChild(div);
                }
            });
            // Add listeners
            document.querySelectorAll('.edit-station-check').forEach(cb => {
                cb.addEventListener('change', () => updateMountpointInput('edit-'));
            });
        }

        // 2.5 LOAD LIVE MOUNTPOINTS FOR EDIT
        if (String(currentUser?.role || '').toLowerCase() === 'admin') {
            renderAdminLiveMountpoints('edit-');
            fetchCasterSources({ force: true }).then(() => {
                renderAdminLiveMountpoints('edit-');
            }).catch(e => console.error("Error refreshing live mountpoints", e));
        } else if (currentUser?.extra_mountpoints) {
            renderCoordinatorExtraMountpoints('edit-');
        } else {
            document.getElementById('edit-admin-live-mountpoints-box')?.classList.add('d-none');
        }

        // 3. Hiện Modal
        const modal = new bootstrap.Modal(document.getElementById('editRoverModal'));
        modal.show();
    }

    // Xử lý Submit form Edit
    const editForm = document.getElementById('edit-rover-form');
    if (editForm) {
        editForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const roverId = document.getElementById('edit-rover-id').value;
            const newPass = document.getElementById('edit-password').value;
            const newExpiry = readExpiryDays('edit');
            if (newExpiry === undefined) return;

            // --- LẤY DỮ LIỆU TỪ CHECKLIST & TEXT BOX ---
            const checkedStations = document.querySelectorAll('.edit-station-check:checked');
            let selectedSerials = Array.from(checkedStations).map(cb => cb.value);
            
            // finalMountpoints is now taken from the text box (which is updated by checklist and manual input)
            const finalMountpoints = document.getElementById('edit-mountpoints').value.trim();

            if (!finalMountpoints) {
                showToast('Vui lòng chọn ít nhất một trạm phát hoặc nhập mountpoint!', 'danger');
                return;
            }

            const payload = {
                mountpoints: finalMountpoints,
                selected_serials: selectedSerials, // 🔥 FIX: Gửi kèm danh sách serial trạm
                show_position: document.getElementById('edit-show-position').checked,
                target_casters: Array.from(document.querySelectorAll('.edit-caster-target-check:checked')).map(cb => cb.value),
                rtcm_cycle_enabled: document.getElementById('edit-rtcm-cycle-enabled')?.checked || false,
                rtcm_disconnect_interval_seconds: Number.parseInt(document.getElementById('edit-rtcm-disconnect-interval')?.value || 300),
                rtcm_disconnect_duration_seconds: Number.parseInt(document.getElementById('edit-rtcm-disconnect-duration')?.value || 10),
                is_shared_with_managers: document.getElementById('edit-is-shared')?.checked || false
            };
            if (String(currentUser?.role || '').toLowerCase() === 'admin') {
                payload.auto_save_sessions = document.getElementById('edit-auto-save-sessions')?.checked !== false;
                payload.auto_save_fixed_points = document.getElementById('edit-auto-save-fixed-points')?.checked !== false;
            }
            if (newPass) payload.password = newPass;
            if (newExpiry !== null) payload.expiry_days = newExpiry;
            const maxConn = document.getElementById('edit-max-connections')?.value;
            if (maxConn) payload.max_connections = Number.parseInt(maxConn);

            try {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang lưu...';

                // GỌI API THÔNG MINH (PUT)
                const url = roverId == "0" 
                    ? `/api/rovers/sync/by-username/${document.getElementById('edit-username').value}`
                    : `/api/rovers/${roverId}`;
                
                const res = await apiFetchRaw(url, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${localStorage.getItem('access_token') || sessionStorage.getItem('access_token')}`
                    },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.detail || 'Cập nhật thất bại');
                }

                const updatedRover = await res.json();

                showToast('Đã cập nhật Rover thành công!', 'success');
                
                // Ẩn modal và cập nhật data tại chỗ (không reload toàn trang)
                const modalEl = document.getElementById('editRoverModal');
                const modalInstance = bootstrap.Modal.getInstance(modalEl);
                modalInstance.hide();

                patchEditedRoverRow(updatedRover);
                fetchCasterUsers().catch(error => console.error('fetchCasterUsers failed:', error));

            } catch (err) {
                showToast(err.message, 'danger');
            } finally {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-save me-2"></i>Lưu Thay Đổi';
            }
        });
    }

    // === HANDLE DELETE ROVER (MODAL MỚI) ===
    async function handleDeleteRover(e) {
        const btn = e.currentTarget;
        const id = Number.parseInt(btn.dataset.id);
        const username = btn.dataset.username || btn.dataset.user;
        
        let rover = allRovers.find(r => r.id === id);
        if (!rover && username) {
            rover = allRovers.find(r => normalizeUsername(r.username) === normalizeUsername(username));
        }
        // Nếu là Unknown (id=0), tạo đối tượng giả lập để Modal vẫn chạy
        if (!rover && id === 0) {
            rover = {
                id: 0,
                username: username,
                target_casters: "",
                caster_infos: getCasterInfosForUsername(username)
            };
        }
        
        if (!rover) return;

        document.getElementById('delete-rover-id').value = id;
        
        // Mặc định là TÍCH CHỌN xóa khỏi DB (trừ khi là tài khoản ngoại lai id=0)
        const dbCheck = document.getElementById('delete-from-db');
        dbCheck.checked = (id !== 0);
        
        // Ẩn vùng xóa DB nếu là tài khoản ngoại lai
        const dbSection = dbCheck.closest('.mb-2');
        if (id === 0) {
            dbSection.style.display = 'none';
        } else {
            dbSection.style.display = 'block';
        }

        // Populate server list
        const serverList = document.getElementById('delete-server-list');
        serverList.innerHTML = '';
        
        // Lấy danh sách server: Ưu tiên target_casters, nếu trống thì lấy thực tế từ caster_infos
        let hosts = parseCasterHosts(rover.target_casters);
        let relatedCasterInfos = Array.isArray(rover.caster_infos) ? rover.caster_infos : [];
        if (relatedCasterInfos.length === 0) {
            relatedCasterInfos = getCasterInfosForUsername(username || rover.username);
        }
        if (hosts.length === 0 && relatedCasterInfos.length > 0) {
            hosts = Array.from(new Set(relatedCasterInfos.map(i => String(i?.server || '').trim()).filter(Boolean)));
        }
        
        if (hosts.length === 0) {
            serverList.innerHTML = '<div class="text-muted small">Không tìm thấy server nào liên quan.</div>';
        } else {
            hosts.forEach(host => {
                const div = document.createElement('div');
                div.className = 'form-check form-check-inline me-3 mb-2';
                div.innerHTML = `
                    <input class="form-check-input delete-host-check" type="checkbox" value="${host}" id="del-check-${host}">
                    <label class="form-check-label small" for="del-check-${host}">${host}</label>
                `;
                serverList.appendChild(div);
            });
        }

        const modalEl = document.getElementById('deleteRoverModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
            modal.show();
        } else {
            console.error("Modal deleteRoverModal not found!");
        }
    }

    // Nút thực thi xóa trong Modal
    const executeDeleteBtn = document.getElementById('executeDeleteBtn');
    if (executeDeleteBtn) {
        executeDeleteBtn.onclick = async function() {
            const roverIdInput = document.getElementById('delete-rover-id');
            const deleteDbCheck = document.getElementById('delete-from-db');
            if (!roverIdInput || !deleteDbCheck) return;

            const roverId = roverIdInput.value;
            const deleteFromDb = deleteDbCheck.checked;
        const checkedHosts = Array.from(document.querySelectorAll('.delete-host-check:checked')).map(cb => cb.value);
        const allHosts = Array.from(document.querySelectorAll('.delete-host-check')).map(cb => cb.value);
        
        const btn = this;
        const originalHtml = btn.innerHTML;

        try {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang xử lý...';

            if (deleteFromDb) {
                // TRƯỜNG HỢP 1: Xóa trắng hoàn toàn
                const res = await apiFetchRaw(`/api/rovers/${roverId}`, { 
                    method: 'DELETE', 
                    headers: API_HEADERS 
                });
                if (!res.ok) throw new Error(await res.text() || 'Xóa hoàn toàn thất bại');
                showToast(`Đã xóa vĩnh viễn tài khoản khỏi hệ thống`, 'success');
            } else {
                // TRƯỜNG HỢP 2: Chỉ gỡ trên một số server (Partial Cleanup)
                // Những server ĐƯỢC TÍCH trong modal delete là những server MUỐN GỠ BỎ.
                // Danh sách target_casters CÒN LẠI = Tổng - Đã tích gỡ.
                const remainingHosts = allHosts.filter(h => !checkedHosts.includes(h));
                
                if (checkedHosts.length === 0) {
                    showToast('Vui lòng tích chọn ít nhất 1 server để gỡ bỏ (hoặc chọn Xóa khỏi DB)', 'warning');
                    btn.disabled = false;
                    btn.innerHTML = originalHtml;
                    return;
                }

                const res = await apiFetchRaw(`/api/rovers/${roverId}`, {
                    method: 'PUT',
                    headers: API_HEADERS,
                    body: JSON.stringify({
                        target_casters: remainingHosts
                    })
                });

                if (!res.ok) throw new Error(await res.text() || 'Cập nhật gỡ bỏ thất bại');
                showToast(`Đã gỡ bỏ tài khoản khỏi ${checkedHosts.length} server`, 'success');
            }

            // Đóng modal và refresh
            const modalEl = document.getElementById('deleteRoverModal');
            if (modalEl) {
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
            }
            await refreshDataInPlace();
        } catch (err) {
            console.error(err);
            showToast(`Lỗi: ${err.message}`, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    };
}

    // === HANDLE DELETE CASTER USER (Unknown) ===
    async function handleDeleteCasterUser(e) {
        const btn = e.currentTarget;
        const username = btn.dataset.username;
        
        const infos = getCasterInfosForUsername(username);
        const serversList = infos.map(i => i.server).filter(Boolean);
        const serversStr = serversList.length > 0 ? serversList.join(', ') : 'các Caster được cấu hình';

        const ok = await window.CorsUI.confirmAction({
            title: 'Delete caster account',
            message: `Account ${username} is active on caster [${serversStr}] but is not in DB. Delete it from caster?`,
            confirmText: 'Delete',
            tone: 'danger'
        });
        if (!ok) return;

        try {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

            const res = await apiFetchRaw(`/api/caster/users/${username}`, { 
                method: 'DELETE', 
                headers: API_HEADERS 
            });
            
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Xóa thất bại');
            }

            const data = await res.json();
            const deletedFrom = data.casters && data.casters.length > 0 ? data.casters.join(', ') : serversStr;
            showToast(`Đã xóa tài khoản ngoại lai ${username} khỏi Caster: ${deletedFrom}`, 'warning');
            await refreshDataInPlace();
        } catch (err) {
            showToast(err.message, 'danger');
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-trash3"></i>';
        }
    }

    // === HANDLE CREATE ROVER ===
    const createForm = document.getElementById('create-rover-form');
    if (createForm) {
        createForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const checkedBoxes = document.querySelectorAll('.station-check:checked');
            const selectedSerials = Array.from(checkedBoxes).map(cb => cb.value);

            const mpOverride = document.getElementById('new-mountpoints')?.value || '';
            const isAdmin = String(currentUser?.role || '').toLowerCase() === 'admin';

            if (selectedSerials.length === 0 && !isAdmin && !mpOverride.trim()) {
                showToast('Vui lòng chọn ít nhất một trạm hoặc nhập Mountpoint.', 'warning');
                return;
            }

            if (!mpOverride.trim()) {
                showToast('Mountpoint không được để trống.', 'warning');
                return;
            }

            const expiryDays = readExpiryDays('new');
            if (expiryDays === undefined) return;

            const targetCasters = Array.from(document.querySelectorAll('.caster-target-check:checked')).map(cb => cb.value);

            const payload = {
                username: document.getElementById('new-username').value,
                password: document.getElementById('new-password').value,
                expiry_days: expiryDays,
                selected_serials: selectedSerials,
                mountpoint_override: mpOverride,
                target_casters: targetCasters,
                max_connections: Number.parseInt(document.getElementById('new-max-connections')?.value || 1),
                show_position: document.getElementById('new-show-position').checked,
                rtcm_cycle_enabled: document.getElementById('new-rtcm-cycle-enabled').checked,
                rtcm_disconnect_interval_seconds: Number.parseInt(document.getElementById('new-rtcm-disconnect-interval').value || 300),
                rtcm_disconnect_duration_seconds: Number.parseInt(document.getElementById('new-rtcm-disconnect-duration').value || 10),
                is_shared_with_managers: document.getElementById('new-is-shared')?.checked || false
            };

            if (isAdmin) {
                payload.auto_save_sessions = document.getElementById('new-auto-save-sessions')?.checked !== false;
                payload.auto_save_fixed_points = document.getElementById('new-auto-save-fixed-points')?.checked === true;
            }

            try {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang tạo...';

                const res = await apiFetchRaw(`/api/rovers/create-multi`, {
                    method: 'POST',
                    headers: API_HEADERS,
                    body: JSON.stringify(payload)
                });
                
                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.detail || 'Lỗi tạo Rover');
                }

                showToast(`Tạo Rover "${payload.username}" thành công!`, 'success');
                bootstrap.Modal.getInstance(document.getElementById('createRoverModal')).hide();
                createForm.reset();
                syncExpiryFields('new');
                const newAutoSaveSessions = document.getElementById('new-auto-save-sessions');
                if (newAutoSaveSessions) newAutoSaveSessions.checked = true;
                const newAutoSaveFixedPoints = document.getElementById('new-auto-save-fixed-points');
                if (newAutoSaveFixedPoints) newAutoSaveFixedPoints.checked = false;
                document.querySelectorAll('.station-check').forEach(cb => cb.checked = false);
                await refreshDataInPlace();

            } catch (err) {
                showToast(err.message, 'danger');
            } finally {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Tạo Tài Khoản';
            }
        });
    }

    // === UTILS ===

    // Toggle RTCM cycle interval visibility
    const rtcmCycleToggle = document.getElementById('new-rtcm-cycle-enabled');
    if (rtcmCycleToggle) {
        rtcmCycleToggle.addEventListener('change', (e) => {
            const intervalGroup = document.getElementById('rtcm-cycle-interval-group');
            if (intervalGroup) {
                if (e.target.checked) {
                    intervalGroup.classList.remove('d-none');
                } else {
                    intervalGroup.classList.add('d-none');
                }
            }
        });
    }

    const editRtcmCycleToggle = document.getElementById('edit-rtcm-cycle-enabled');
    if (editRtcmCycleToggle) {
        editRtcmCycleToggle.addEventListener('change', (e) => {
            const intervalGroup = document.getElementById('edit-rtcm-cycle-interval-group');
            if (intervalGroup) {
                if (e.target.checked) {
                    intervalGroup.classList.remove('d-none');
                } else {
                    intervalGroup.classList.add('d-none');
                }
            }
        });
    }

    // Toggle dependency between auto_save_sessions and auto_save_fixed_points
    function bindAutoSaveDependency(sessionToggleId, fixedPointsToggleId) {
        const sessionToggle = document.getElementById(sessionToggleId);
        const fixedPointsToggle = document.getElementById(fixedPointsToggleId);
        if (!sessionToggle || !fixedPointsToggle) return;

        sessionToggle.addEventListener('change', () => {
            if (!sessionToggle.checked) {
                fixedPointsToggle.checked = false;
            }
        });
        fixedPointsToggle.addEventListener('change', () => {
            if (fixedPointsToggle.checked) {
                sessionToggle.checked = true;
            }
        });
    }
    bindAutoSaveDependency('new-auto-save-sessions', 'new-auto-save-fixed-points');
    bindAutoSaveDependency('edit-auto-save-sessions', 'edit-auto-save-fixed-points');

    // Rover Dashboard events
    const searchBox = document.getElementById('search-box');
    if (searchBox) searchBox.addEventListener('input', renderTable);
    
    const filterStation = document.getElementById('filter-station');
    if (filterStation) filterStation.addEventListener('change', renderTable);
    
    const filterStatus = document.getElementById('filter-status');
    if (filterStatus) filterStatus.addEventListener('change', renderTable);

    document.getElementById('rover-select-all')?.addEventListener('change', (event) => {
        document.querySelectorAll('.rover-row-check').forEach(cb => {
            cb.checked = event.target.checked;
            const key = cb.dataset.usernameKey;
            if (key && cb.checked) selectedRoverKeys.add(key);
            if (key && !cb.checked) selectedRoverKeys.delete(key);
        });
        updateRoverBulkToolbar();
    });

    document.getElementById('bulk-renew-btn')?.addEventListener('click', () => {
        bulkRenewSelectedRovers().catch(err => showToast(err.message, 'danger'));
    });
    document.getElementById('bulk-sync-btn')?.addEventListener('click', () => {
        syncSelectedRovers().catch(err => showToast(err.message, 'danger'));
    });
    document.getElementById('bulk-export-btn')?.addEventListener('click', exportSelectedRovers);
    document.getElementById('filter-expiring-btn')?.addEventListener('click', () => {
        showExpiringSoonOnly = !showExpiringSoonOnly;
        document.getElementById('filter-expiring-btn')?.classList.toggle('active', showExpiringSoonOnly);
        renderTable();
    });

    const applyLiveMountpointsBtn = document.getElementById('apply-live-mountpoints-btn');
    if (applyLiveMountpointsBtn) {
        applyLiveMountpointsBtn.addEventListener('click', applySelectedLiveMountpoints);
    }

    const editApplyLiveMountpointsBtn = document.getElementById('edit-apply-live-mountpoints-btn');
    if (editApplyLiveMountpointsBtn) {
        editApplyLiveMountpointsBtn.addEventListener('click', applySelectedLiveMountpoints);
    }

    const reloadLiveMountpointsBtn = document.getElementById('reload-live-mountpoints-btn');
    if (reloadLiveMountpointsBtn) {
        reloadLiveMountpointsBtn.addEventListener('click', async () => {
            await fetchCasterSources({ force: true });
            if (String(currentUser?.role || '').toLowerCase() === 'admin') {
                renderAdminLiveMountpoints('');
            } else {
                renderCoordinatorExtraMountpoints('');
            }
            showToast('Đã tải lại mountpoints live', 'info');
        });
    }

    const editReloadLiveMountpointsBtn = document.getElementById('edit-reload-live-mountpoints-btn');
    if (editReloadLiveMountpointsBtn) {
        editReloadLiveMountpointsBtn.addEventListener('click', async () => {
            await fetchCasterSources({ force: true });
            if (String(currentUser?.role || '').toLowerCase() === 'admin') {
                renderAdminLiveMountpoints('edit-');
            } else {
                renderCoordinatorExtraMountpoints('edit-');
            }
            showToast('Đã tải lại mountpoints live', 'info');
        });
    }

    // Input changes listener for real-time live filter updating
    const newMpsInput = document.getElementById('new-mountpoints');
    if (newMpsInput) {
        newMpsInput.addEventListener('input', () => applyLiveMpFilter(''));
        newMpsInput.addEventListener('blur', () => validateCreateRoverInputs());
    }
    document.getElementById('new-username')?.addEventListener('blur', () => validateCreateRoverInputs());
    const editMpsInput = document.getElementById('edit-mountpoints');
    if (editMpsInput) {
        editMpsInput.addEventListener('input', () => applyLiveMpFilter('edit-'));
    }

    // Dynamic fresh fetch for create modal
    const createModalEl = document.getElementById('createRoverModal');
    if (createModalEl) {
        createModalEl.addEventListener('show.bs.modal', () => {
            if (String(currentUser?.role || '').toLowerCase() === 'admin') {
                renderAdminLiveMountpoints('');
                fetchCasterSources({ force: true }).then(() => {
                    renderAdminLiveMountpoints('');
                }).catch(e => console.error("Error refreshing live mountpoints", e));
            } else if (currentUser?.extra_mountpoints) {
                renderCoordinatorExtraMountpoints('');
            }
        });
    }
    
    // ✅ ADMIN ONLY: Tab Mountpoints
    const mountpointsTab = document.getElementById('tab-mountpoints-li');
    setupRoverActivity();
    if (mountpointsTab) {
        mountpointsTab.addEventListener('shown.bs.tab', async () => {
            await loadAndRenderMountpoints();
        });
    }

    // ✅ ADMIN ONLY: Tab Blocklist
    const blocklistTab = document.getElementById('tab-blocklist-tab');
    if (blocklistTab) {
        blocklistTab.addEventListener('shown.bs.tab', async () => {
            if (typeof renderBlocklistMapping === 'function') {
                await renderBlocklistMapping();
            }
        });
    }
    
    document.getElementById('refresh-btn').addEventListener('click', async () => {
        const btn = document.getElementById('refresh-btn');
        if (!btn) return;
        const icon = btn.querySelector('i');
        if (icon) icon.classList.add('spin-anim');
        
        await syncData();
        
        if (icon) icon.classList.remove('spin-anim');
        showToast('Đã đồng bộ dữ liệu hệ thống', 'info');
    });

    // ✅ Load and render blocklist relationships
    async function renderBlocklistMapping() {
        const container = document.getElementById('blocklist-mapping-container');
        if (!container) return;

        container.innerHTML = `
            <div class="text-center py-5">
                <div class="spinner-border spinner-border-sm text-info me-2"></div>
                <span class="text-muted">Đang tải danh sách quan hệ chặn...</span>
            </div>
        `;

        try {
            const res = await apiFetchRaw('/api/caster/blocklist', { headers: API_HEADERS });
            if (!res.ok) throw new Error('Không thể tải blocklist');
            const data = await res.json();

            if (data.length === 0) {
                container.innerHTML = `
                    <div class="premium-alert mb-4">
                        <div class="alert-content">
                            <i class="bi bi-info-circle-fill"></i>
                            <span class="alert-text">Hệ thống đang hiển thị các quy tắc chặn động dựa trên IP và Mountpoint. Các quy tắc này được đồng bộ 5s/lần từ Core Caster.</span>
                        </div>
                    </div>
                    <div class="text-center py-5 opacity-50">
                        <i class="bi bi-shield-lock d-block mb-3 h1"></i>
                        <p class="mb-0">Hiện tại không có quy tắc chặn chủ động nào được thiết lập.</p>
                        <small>Các tài khoản bị Block sẽ hiển thị trạng thái BLOCKED trong danh sách Rover.</small>
                    </div>
                `;
                return;
            }

            let html = `
                <div class="premium-alert mb-4">
                    <div class="alert-content">
                        <i class="bi bi-shield-fill-check text-success"></i>
                        <span class="alert-text">Có <strong>${data.length}</strong> quy tắc chặn đang hoạt động. Các tài khoản này sẽ bị từ chối kết nối ngay lập tức.</span>
                    </div>
                </div>
                <div class="premium-table-card border-0 bg-transparent">
                    <table class="premium-table">
                        <thead>
                            <tr>
                                <th>Tài khoản</th>
                                <th>Mountpoint</th>
                                <th>Ngày chặn</th>
                                <th>Người chặn</th>
                                <th>Lý do</th>
                                <th class="text-end">Thao tác</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${data.map(bl => `
                                <tr>
                                    <td><span class="text-white fw-bold">${escapeHtml(bl.username)}</span></td>
                                    <td><span class="badge bg-danger bg-opacity-10 text-danger border border-danger border-opacity-25 px-2 py-1">${escapeHtml(bl.mountpoint || 'TẤT CẢ')}</span></td>
                                    <td><span class="text-muted small">${new Date(bl.blocked_at * 1000).toLocaleString('vi-VN')}</span></td>
                                    <td><span class="text-secondary small">${escapeHtml(bl.blocked_by || 'System')}</span></td>
                                    <td><span class="text-muted small">${escapeHtml(bl.reason || '--')}</span></td>
                                    <td class="text-end">
                                        <button class="btn btn-sm btn-outline-success unblock-direct-btn py-1 px-3 rounded-pill" 
                                            data-username="${escapeHtml(bl.username)}" data-mountpoint="${escapeHtml(bl.mountpoint || '')}">
                                            <i class="bi bi-unlock-fill me-1"></i> Mở khóa
                                        </button>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
            container.innerHTML = html;

            // Bind events
            container.querySelectorAll('.unblock-direct-btn').forEach(btn => {
                btn.onclick = async () => {
                    const { username, mountpoint } = btn.dataset;
                    const ok = await window.CorsUI.confirmAction({
                        title: 'Unblock account',
                        message: `Unblock account ${username}?`,
                        confirmText: 'Unblock'
                    });
                    if (!ok) return;
                    
                    try {
                        btn.disabled = true;
                        const res = await apiFetchRaw('/api/caster/unblock-user', {
                            method: 'POST',
                            headers: API_HEADERS,
                            body: JSON.stringify({ username, mountpoint: mountpoint || null })
                        });
                        if (!res.ok) throw new Error('Mở khóa thất bại');
                        showToast(`Đã mở khóa tài khoản ${username}`, 'success');
                        renderBlocklistMapping().catch(error => console.error('renderBlocklistMapping failed:', error)); // Refresh this tab
                        syncData().catch(error => console.error('syncData failed:', error)); // Refresh main list
                    } catch (err) {
                        showToast(err.message, 'danger');
                        btn.disabled = false;
                    }
                };
            });

        } catch (e) {
            container.innerHTML = `<div class="alert alert-danger">Lỗi: ${e.message}</div>`;
        }
    }

    // ✅ Load and render mountpoints into tab
    async function loadAndRenderMountpoints({ background = false } = {}) {
        const container = document.getElementById('mountpoints-container');
        if (!container || mountpointsRefreshInProgress) return;
        if (background && (document.querySelector('.modal.show') || document.getElementById('sync-mountpoints-btn')?.disabled)) return;
        mountpointsRefreshInProgress = true;

        if (!background) container.innerHTML = '<div class="text-center py-5"><div class="spinner-border spinner-border-sm text-info me-2"></div><span class="text-muted">Đang tải danh sách mountpoints...</span></div>';

        try {
            const res = await apiFetchRaw('/api/admin/caster-sources', { headers: API_HEADERS, cache: 'no-store' });
            if (!res.ok) throw new Error('Failed to fetch sources');
            
            const data = await res.json();
            casterSourcesCache = data;
            const oldSearch = document.getElementById('mountpoints-search');
            const view = {
                search: oldSearch?.value || '',
                domain: document.querySelector('#domain-filter-pills .nav-link.active')?.getAttribute('data-domain') || 'all',
                focused: Boolean(oldSearch && document.activeElement === oldSearch),
                cursor: oldSearch?.selectionStart,
                selectionEnd: oldSearch?.selectionEnd,
                scrollTop: container.scrollTop,
                scrollLeft: container.querySelector('.table-responsive')?.scrollLeft || 0,
            };
            const domains = Object.keys(data.by_domain || {}).sort((a, b) => a.localeCompare(b));
            const sources = data.all || [];
            const hostOptions = Object.keys(data.by_host || {}).sort((a, b) => a.localeCompare(b));

            let html = `
                <div class="d-flex flex-wrap justify-content-between align-items-center mb-4 gap-3">
                    <div class="d-flex flex-wrap align-items-center gap-2">
                        <button type="button" class="btn btn-premium-primary btn-sm px-3" id="new-mountpoint-btn">
                            <i class="bi bi-plus-circle me-1"></i> Tạo mountpoint
                        </button>
                        <button type="button" class="btn btn-outline-info btn-sm px-3" id="sync-mountpoints-btn">
                            <i class="bi bi-arrow-repeat me-1" aria-hidden="true"></i> Đồng bộ caster
                        </button>
                        <span class="text-muted small" role="status">${data.last_synced_at ? `Cập nhật: ${escapeHtml(new Date(data.last_synced_at * 1000).toLocaleString('vi-VN'))}` : 'Chưa có dữ liệu đồng bộ'}</span>
                    </div>
                    <div class="search-input-wrapper" style="max-width: 400px;">
                        <i class="bi bi-search"></i>
                        <input type="text" class="form-control premium-input" id="mountpoints-search" aria-label="Tìm mountpoint, identifier hoặc host" placeholder="Tìm tên, identifier, host...">
                    </div>
                </div>

                <div class="mb-4">
                    <div class="premium-tabs nav nav-pills" id="domain-filter-pills" role="tablist">
                        <button class="nav-link active" data-domain="all">
                            <i class="bi bi-grid-fill me-1"></i> Tất cả <span class="badge bg-white bg-opacity-25 ms-1">${sources.length}</span>
                        </button>
                        ${domains.map(d => `
                            <button class="nav-link" data-domain="${escapeHtml(d)}">
                                <i class="bi bi-hdd-network me-1"></i> ${escapeHtml(d)} <span class="badge bg-white bg-opacity-25 ms-1">${(data.by_domain[d] || []).length}</span>
                            </button>
                        `).join('')}
                    </div>
                </div>

                <div class="premium-table-card">
                    <div class="table-responsive">
                    <table class="premium-table table-hover">
                        <thead>
                            <tr>
                                <th style="width: 60px;" class="text-center">#</th>
                                <th>Mountpoint</th>
                                <th>Identifier</th>
                                <th>Caster Host</th>
                                <th>Auth / GGA</th>
                                <th>Position</th>
                                <th>Stats</th>
                                <th class="text-end">Actions</th>
                            </tr>
                        </thead>
                        <tbody id="mountpoints-table-body">
                            ${sources.length ? sources.map((src, idx) => {
                                const domain = escapeHtml(src.domain || src.host || '');
                                const mountpoint = escapeHtml(src.mountpoint || '--');
                                const rawMountpoint = escapeHtml(src.mountpoint || '');
                                const identifier = escapeHtml(src.identifier || '--');
                                const host = escapeHtml(src.host || '');
                                const latitude = Number(src.latitude);
                                const longitude = Number(src.longitude);
                                const hasPosition = Number.isFinite(latitude) && Number.isFinite(longitude) && Boolean(latitude || longitude);
                                const dataCount = Number(src.datacount || 0);
                                return `
                                <tr data-domain="${domain}">
                                    <td class="text-center"><span class="text-muted small">${idx + 1}</span></td>
                                    <td>
                                        <div class="d-flex align-items-center gap-2">
                                            <span class="name text-info font-monospace fw-bold">${mountpoint}</span>
                                            <button type="button" class="btn btn-xs btn-link text-secondary p-0 copy-mp" data-mp="${rawMountpoint}" title="Copy">
                                                <i class="bi bi-clipboard"></i>
                                            </button>
                                        </div>
                                    </td>
                                    <td><span class="text-white small">${identifier}</span></td>
                                    <td><span class="badge bg-dark border border-secondary text-secondary fw-normal px-2 py-1">${domain || '--'}</span></td>
                                    <td>
                                        <div class="d-flex gap-2">
                                            <span class="badge-status ${(src.auth_required === 'B' || src.auth_required === true || String(src.auth_required).toLowerCase() === 'true') ? 'blocked' : 'ok'}" style="font-size:10px">
                                                ${(src.auth_required === 'B' || src.auth_required === true || String(src.auth_required).toLowerCase() === 'true') ? 'AUTH' : 'OPEN'}
                                            </span>
                                            <span class="badge-status ${(src.gga_required === true || String(src.gga_required).toLowerCase() === 'true' || src.gga_required === 1 || String(src.gga_required) === '1') ? 'blocked' : 'ok'}" style="font-size:10px">
                                                GGA
                                            </span>
                                        </div>
                                    </td>
                                    <td>
                                        <div class="d-flex flex-column">
                                            <span class="small ${hasPosition ? 'text-white' : 'text-muted'}">
                                                ${hasPosition ? `${latitude.toFixed(4)}, ${longitude.toFixed(4)}` : '--'}
                                            </span>
                                            <span class="badge-status ${src.show_position ? 'ok' : 'blocked'} mt-1" style="width:fit-content; font-size:9px;">
                                                SHOW POS: ${src.show_position ? 'ON' : 'OFF'}
                                            </span>
                                        </div>
                                    </td>
                                    <td>
                                        <div class="small">
                                            <span class="text-secondary">Data:</span> <span class="text-success fw-bold">${Number.isFinite(dataCount) ? dataCount : 0}</span>
                                        </div>
                                    </td>
                                    <td class="text-end">
                                        <div class="d-flex justify-content-end align-items-center gap-2 flex-nowrap">
                                        <button type="button" class="btn btn-sm btn-outline-info edit-mp" style="min-height:44px;white-space:nowrap" data-index="${idx}" aria-label="Sửa mountpoint ${rawMountpoint}"><i class="bi bi-pencil-square me-1" aria-hidden="true"></i>Sửa</button>
                                        ${src.on_caster === false ? `<button type="button" class="btn btn-sm btn-outline-warning restore-mp" style="min-height:44px;white-space:nowrap" data-host="${host}" data-mountpoint="${rawMountpoint}"><i class="bi bi-arrow-counterclockwise me-1" aria-hidden="true"></i>Khôi phục</button>` : ''}
                                        <button type="button" class="action-btn text-danger delete-mp" data-host="${host}" data-identifier="${escapeHtml(src.identifier || '')}" data-mountpoint="${rawMountpoint}" title="Xóa mountpoint" aria-label="Xóa mountpoint ${rawMountpoint}"><i class="bi bi-trash3" aria-hidden="true"></i></button>
                                        </div>
                                    </td>
                                </tr>
                            `;
                            }).join('') : '<tr><td colspan="8" class="text-center text-muted py-4">Chưa có mountpoint đã lưu. Nhấn Đồng bộ caster để lấy danh sách hiện có.</td></tr>'}
                        </tbody>
                    </table>
                    </div>
                </div>

                <div class="mt-4 p-3 bg-dark-subtle rounded border border-secondary border-opacity-25 small">
                    <div class="d-flex align-items-center gap-2 text-info mb-2">
                        <i class="bi bi-lightbulb"></i>
                        <span class="fw-bold text-uppercase" style="letter-spacing: 0.5px">Hướng dẫn nhanh</span>
                    </div>
                    <ul class="text-muted mb-0 ps-3">
                        <li>Sử dụng cột <strong>Mountpoint</strong> khi cấu hình quyền truy cập cho tài khoản Rover.</li>
                        <li>Trạng thái <strong>AUTH</strong> hiển thị các luồng yêu cầu xác thực tài khoản.</li>
                        <li>Nhấn vào biểu tượng 📋 để sao chép nhanh tên Mountpoint.</li>
                    </ul>
                </div>
            `;

            container.innerHTML = html;

            const syncButton = document.getElementById('sync-mountpoints-btn');
            syncButton?.addEventListener('click', async () => {
                syncButton.disabled = true;
                syncButton.setAttribute('aria-busy', 'true');
                const previousLabel = syncButton.innerHTML;
                syncButton.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span> Đang đồng bộ…';
                try {
                    const response = await apiFetchRaw('/api/admin/caster-sources/sync', { method: 'POST', headers: API_HEADERS });
                    const result = await response.json();
                    if (!response.ok) throw new Error(result.detail || 'Không thể đồng bộ caster');
                    const results = result.results || [];
                    const failed = results.filter(item => item.error).map(item => item.host);
                    showToast(failed.length ? `Đã đồng bộ ${result.synced_hosts}/${results.length} caster. Chưa đồng bộ được: ${failed.join(', ')}. Danh sách đã lưu vẫn được giữ.` : 'Đã đồng bộ danh sách mountpoint', failed.length ? 'warning' : 'success');
                    casterSourcesCache = null;
                    await loadAndRenderMountpoints();
                    document.getElementById('sync-mountpoints-btn')?.focus();
                } catch (error) {
                    showToast(error.message, 'danger');
                } finally {
                    syncButton.disabled = false;
                    syncButton.removeAttribute('aria-busy');
                    syncButton.innerHTML = previousLabel;
                }
            });

            // --- Modal Host Population ---
            const modalHostSelect = document.getElementById('create-mp-host');
            if (modalHostSelect) {
                modalHostSelect.innerHTML = hostOptions.map(h => `<option value="${escapeHtml(h)}">${escapeHtml(h)}</option>`).join('');
            }

            const openMountpointForm = (source = null) => {
                const form = document.getElementById('create-mp-form');
                if (!form) return;
                form.reset();
                form.dataset.editing = source ? 'true' : 'false';
                const host = document.getElementById('create-mp-host');
                const name = document.getElementById('create-mp-name');
                const password = document.getElementById('create-mp-password');
                host.disabled = Boolean(source);
                name.readOnly = Boolean(source);
                password.required = !source;
                password.value = '';
                password.placeholder = source ? 'Không đổi' : '';
                document.getElementById('mountpoint-password-help').classList.toggle('d-none', !source);
                document.getElementById('mountpoint-modal-title').textContent = source ? 'Sửa cấu hình Mountpoint' : 'Cấu hình Mountpoint mới';
                document.getElementById('mountpoint-edit-note').classList.toggle('d-none', !source);
                document.getElementById('create-mp-btn').innerHTML = source ? '<i class="bi bi-check-lg me-2" aria-hidden="true"></i>Lưu thay đổi' : '<i class="bi bi-plus-circle me-2" aria-hidden="true"></i>Tạo Mountpoint';
                if (source) {
                    host.value = source.host;
                    name.value = source.mountpoint;
                    document.getElementById('create-mp-identifier').value = source.identifier || '';
                    document.getElementById('create-mp-lat').value = source.latitude ?? '';
                    document.getElementById('create-mp-lon').value = source.longitude ?? '';
                    const enabled = value => [true, 1, '1', 'true', 'B'].includes(value);
                    document.getElementById('create-mp-auth').checked = enabled(source.auth_required);
                    document.getElementById('create-mp-gga').checked = enabled(source.gga_required);
                    document.getElementById('create-mp-showpos').checked = enabled(source.show_position);
                }
                bootstrap.Modal.getOrCreateInstance(document.getElementById('createMountpointModal')).show();
            };
            document.getElementById('new-mountpoint-btn')?.addEventListener('click', () => openMountpointForm());
            container.querySelectorAll('.edit-mp').forEach(btn => {
                btn.onclick = () => openMountpointForm(sources[Number(btn.dataset.index)]);
            });

            // --- LISTENERS ---
            const searchInput = document.getElementById('mountpoints-search');
            const tableBody = document.getElementById('mountpoints-table-body');
            const rows = tableBody.querySelectorAll('tr');

            const filterRows = () => {
                const term = (searchInput?.value || '').toLowerCase().trim();
                const activePill = document.querySelector('#domain-filter-pills .nav-link.active');
                const selectedDomain = activePill?.getAttribute('data-domain') || 'all';

                rows.forEach(row => {
                    const txt = row.textContent.toLowerCase();
                    const rowDomain = row.getAttribute('data-domain');
                    const matchSearch = !term || txt.includes(term);
                    const matchDomain = (selectedDomain === 'all' || rowDomain === selectedDomain);
                    row.classList.toggle('d-none', !(matchSearch && matchDomain));
                });
            };

            if (searchInput) searchInput.value = view.search;
            const pills = Array.from(document.querySelectorAll('#domain-filter-pills .nav-link'));
            const selectedPill = pills.find(pill => pill.getAttribute('data-domain') === view.domain) || pills[0];
            pills.forEach(pill => pill.classList.toggle('active', pill === selectedPill));
            filterRows();
            container.scrollTop = view.scrollTop;
            const scroller = container.querySelector('.table-responsive');
            if (scroller) scroller.scrollLeft = view.scrollLeft;
            if (view.focused && searchInput) {
                searchInput.focus({ preventScroll: true });
                searchInput.setSelectionRange(view.cursor, view.selectionEnd);
            }
            searchInput?.addEventListener('input', filterRows);

            document.querySelectorAll('#domain-filter-pills .nav-link').forEach(pill => {
                pill.addEventListener('click', () => {
                    document.querySelectorAll('#domain-filter-pills .nav-link').forEach(p => p.classList.remove('active'));
                    pill.classList.add('active');
                    filterRows();
                });
            });

            // Action: Copy
            container.querySelectorAll('.copy-mp').forEach(btn => {
                btn.onclick = async () => {
                    const mp = btn.dataset.mp;
                    try {
                        await navigator.clipboard.writeText(mp);
                        showToast(`Đã copy: ${mp}`, 'success');
                    } catch (error) {
                        showToast(`Không thể sao chép mountpoint: ${error.message}`, 'danger');
                    }
                };
            });

            // Restore uses saved credentials on the server, never in table markup.
            container.querySelectorAll('.restore-mp').forEach(btn => {
                btn.onclick = async () => {
                    const { host, mountpoint } = btn.dataset;
                    if (!mountpoint) return;
                    const confirmed = await window.CorsUI.confirmAction({
                        title: 'Khôi phục mountpoint',
                        message: `Khôi phục ${mountpoint} trên ${host} bằng cấu hình đã lưu? Mountpoint đang có trên caster sẽ được giữ nguyên.`,
                        confirmText: 'Khôi phục',
                        tone: 'info'
                    });
                    if (!confirmed) return;
                    const label = btn.innerHTML;
                    btn.disabled = true;
                    btn.setAttribute('aria-busy', 'true');
                    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Đang khôi phục…';
                    try {
                        const res = await apiFetchRaw('/api/admin/caster-sources/restore', {
                            method: 'POST', headers: API_HEADERS,
                            body: JSON.stringify({ host, mountpoint })
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || 'Chưa thể khôi phục mountpoint. Hãy thử lại.');
                        showToast(data.status === 'already_present' ? 'Mountpoint đã có trên caster; giữ nguyên cấu hình.' : 'Caster đã xác nhận khôi phục mountpoint.', 'success');
                    } catch (err) {
                        showToast(err.message, 'danger');
                    } finally {
                        btn.disabled = false;
                        btn.removeAttribute('aria-busy');
                        btn.innerHTML = label;
                    }
                };
            });

            // Action: Delete
            container.querySelectorAll('.delete-mp').forEach(btn => {
                btn.onclick = async () => {
                    const { host, identifier, mountpoint } = btn.dataset;
                    if (!mountpoint) return;
                    const ok = await window.CorsUI.confirmAction({
                        title: 'Delete mountpoint',
                        message: `Delete mountpoint ${mountpoint}?`,
                        confirmText: 'Delete',
                        tone: 'danger'
                    });
                    if (!ok) return;

                    try {
                        btn.disabled = true;
                        const res = await apiFetchRaw('/api/admin/caster-sources', {
                            method: 'DELETE',
                            headers: API_HEADERS,
                            body: JSON.stringify({ host, identifier, mountpoint })
                        });
                        if (!res.ok) throw new Error((await res.json())?.detail || 'Lỗi khi xóa');
                        showToast('Đã xóa Mountpoint thành công', 'warning');
                        await fetchCasterSources({ force: true });
                        loadAndRenderMountpoints().catch(error => console.error('loadAndRenderMountpoints failed:', error));
                    } catch (err) {
                        showToast(err.message, 'danger');
                        btn.disabled = false;
                    }
                };
            });

            // --- Create MP Form Handler (attached once) ---
            const createForm = document.getElementById('create-mp-form');
            if (createForm && !createForm.dataset.listenerAttached) {
                createForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const btn = document.getElementById('create-mp-btn');
                    const editing = createForm.dataset.editing === 'true';
                    const payload = {
                        host: document.getElementById('create-mp-host').value,
                        mountpoint: document.getElementById('create-mp-name').value.trim(),
                        password: document.getElementById('create-mp-password').value.trim(),
                        identifier: document.getElementById('create-mp-identifier').value.trim(),
                        latitude: Number.parseFloat(document.getElementById('create-mp-lat').value) || 0,
                        longitude: Number.parseFloat(document.getElementById('create-mp-lon').value) || 0,
                        auth_required: document.getElementById('create-mp-auth').checked,
                        gga_required: document.getElementById('create-mp-gga').checked,
                        show_position: document.getElementById('create-mp-showpos').checked,
                        ...(!editing ? {storing_data: true, overlap_data: false, re_positioning: false} : {})
                    };

                    const label = btn.innerHTML;
                    try {
                        btn.disabled = true;
                        btn.setAttribute('aria-busy', 'true');
                        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Đang lưu…';
                        const res = await apiFetchRaw('/api/admin/caster-sources', {
                            method: editing ? 'PUT' : 'POST',
                            headers: API_HEADERS,
                            body: JSON.stringify(payload)
                        });
                        if (!res.ok) throw new Error((await res.json())?.detail || 'Lỗi khi tạo');
                        
                        showToast(editing ? 'Đã lưu cấu hình Mountpoint' : 'Đã tạo Mountpoint thành công', 'success');
                        const modalEl = document.getElementById('createMountpointModal');
                        if (modalEl) {
                            const modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
                            modal.hide();
                        }
                        createForm.reset();
                        await fetchCasterSources({ force: true });
                        loadAndRenderMountpoints().catch(error => console.error('loadAndRenderMountpoints failed:', error));
                    } catch (err) {
                        showToast(err.message, 'danger');
                    } finally {
                        btn.disabled = false;
                        btn.removeAttribute('aria-busy');
                        btn.innerHTML = label;
                    }
                });
                createForm.dataset.listenerAttached = "true";
            }

        } catch (e) {
            console.error("[Mountpoints]", e);
            if (!background) container.innerHTML = `<div class="alert alert-danger mt-3"><i class="bi bi-exclamation-triangle me-2"></i>Lỗi tải dữ liệu: ${escapeHtml(e.message)}</div>`;
        } finally {
            mountpointsRefreshInProgress = false;
        }
    }

    function showToast(msg, type = 'primary') {
        window.CorsUI.showToast(type === 'primary' ? 'info' : type, msg);
    }

    // === Premium UI Interaction ===
    document.addEventListener('click', (e) => {
        // Password Toggle
        if (e.target.classList.contains('toggle-password')) {
            const cell = e.target.closest('.password-cell');
            const masked = cell.querySelector('.pw-masked');
            const password = cell.dataset.password;
            const icon = e.target;

            if (masked.textContent === '••••••••') {
                masked.textContent = password || '******';
                icon.classList.replace('bi-eye', 'bi-eye-slash');
            } else {
                masked.textContent = '••••••••';
                icon.classList.replace('bi-eye-slash', 'bi-eye');
            }
        }
    });
});
