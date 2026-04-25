// rovers-global.js

document.addEventListener('DOMContentLoaded', async () => {
    // 1. Kiểm tra Token (Hỗ trợ cả Local và Session Storage)
    const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
    if (!token) {
        if (typeof NavigationManager !== 'undefined') {
            NavigationManager.forceLogout();
        } else {
            localStorage.removeItem('access_token');
            sessionStorage.removeItem('access_token');
            window.location.href = '/login.html';
        }
        return;
    }

    const API_HEADERS = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };

    let currentUser = null;
    let allRovers = [];
    let allDevices = [];
    let casterUsers = [];  // Danh sách user từ Caster
    let casterStats = {};  // Stats từ API
    let globalAppVersion = null;
    let availableCasters = [];
    let casterSourcesCache = null;
    let syncInProgress = false;
    let currentRenderedConnectionKeys = new Set();
    const connectionClockCache = new Map();

    // === 2. AUTH & PERMISSIONS CHECK ===
    try {
        const userRes = await fetch('/api/auth/me', { headers: API_HEADERS });
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
        }

        initData();

    } catch (e) {
        console.error("Initialization error:", e);
        if (e.message === "Unauthorized") {
            if (typeof NavigationManager !== 'undefined') {
                NavigationManager.forceLogout();
            } else {
                localStorage.removeItem('access_token');
                sessionStorage.removeItem('access_token');
                window.location.href = '/login.html';
            }
        } else {
            // Hiển thị thông báo lỗi thay vì văng ra login nếu chỉ là lỗi mạng/server
            if (typeof showToast === 'function') {
                showToast("Lỗi kết nối hệ thống. Vui lòng tải lại trang.", "danger");
            } else {
                alert("Lỗi khởi tạo dữ liệu. Vui lòng tải lại trang.");
            }
        }
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

    async function syncData() {
        if (syncInProgress) {
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

            await Promise.allSettled([
                fetchRovers({ render: false }),
                fetchCasterUsers({ render: false })
            ]);
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
            const res = await fetch('/api/caster/config', { headers: API_HEADERS });
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
                    const div = document.createElement('div');
                    div.className = 'form-check form-check-inline mb-0';
                    div.innerHTML = `
                        <input class="form-check-input caster-target-check" type="checkbox" value="${host}" id="target-${host}" checked>
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
            const res = await fetch('/api/devices', { headers: API_HEADERS });
            if (res.ok) {
                allDevices = await res.json();
                populateBaseSelect();
                populateFilterStation();
            }
        } catch (e) { console.error("Device fetch error", e); }
    }

    async function fetchRovers({ render = true } = {}) {
        try {
            const res = await fetch('/api/rovers/all', { headers: API_HEADERS });
            if (!res.ok) throw new Error('Không thể tải danh sách Rover');
            allRovers = await res.json();
            if (render) renderTable();
        } catch (error) {
            console.error("Rovers fetch error", error);
        }
    }

    // === CASTER USERS MANAGEMENT ===
    async function fetchCasterUsers({ render = true } = {}) {
        try {
            const res = await fetch('/api/caster/users', { headers: API_HEADERS });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Không thể tải danh sách user từ Caster');
            }
            const data = await res.json();
            casterUsers = data.items || [];
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
            const res = await fetch('/api/admin/caster-sources', { headers: API_HEADERS });
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

    async function renderAdminLiveMountpoints(prefix = '') {
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

        const uniqueHosts = Array.from(new Set(sources.map(s => String(s.host || '').trim()).filter(Boolean))).sort();
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

    async function renderCoordinatorExtraMountpoints(prefix = '') {
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

        list.querySelectorAll('.live-mp-item').forEach(item => {
            const text = item.dataset.search || '';
            const host = item.dataset.host || '';
            const matchText = !q || text.includes(q);
            const matchHost = h === 'all' || host === h;
            const matchTarget = targetHosts.length === 0 || !host || targetHosts.includes(host);

            item.style.display = (matchText && matchHost && matchTarget) ? '' : 'none';
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
    document.getElementById('selectAllMps').addEventListener('change', (e) => {
        const checked = e.target.checked;
        document.querySelectorAll('.mp-checkbox').forEach(cb => {
            cb.checked = checked;
        });
        const container = document.getElementById('mountpointSelector');
        container.style.opacity = checked ? "0.5" : "1";
        container.style.pointerEvents = checked ? "none" : "auto";
    });

    // Xác nhận chặn
    document.getElementById('confirmBlockBtn').addEventListener('click', async () => {
        const username = document.getElementById('blockModalUsername').value;
        const reason = document.getElementById('blockReason').value;
        const selectAll = document.getElementById('selectAllMps').checked;
        
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
            const res = await fetch('/api/caster/block-user', {
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

    async function handleUnblockUser(e) {
        const username = e.currentTarget.dataset.username;
        if (!confirm(`Bạn có chắc muốn mở khóa user "${username}"?`)) return;

        const btn = e.currentTarget;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

        try {
            const res = await fetch('/api/caster/unblock-user', {
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
            const startedAt = parseInt(el.dataset.startedAt || 0);
            if (startedAt > 0) {
                const currentUptime = Math.max(0, nowSec - startedAt);
                el.textContent = formatDuration(currentUptime);
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
        
        // Caster uptime might be in minutes sometimes, but typically it's seconds or minutes
        // We'll treat it as seconds for precision, if it's > 1 month it might be minutes
        
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;

        if (h > 0) {
            return `${h}h ${m}m ${s}s`;
        } else if (m > 0) {
            return `${m}m ${s}s`;
        } else {
            return `${s}s`;
        }
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

    function createRoverRowElement(r) {
        const tr = document.createElement('tr');
        tr.dataset.usernameKey = normalizeUsername(r.username);
        if (r.type === 'unknown') tr.style.backgroundColor = 'rgba(239, 68, 68, 0.03)';

        const infos = r.caster_infos || [r.caster_info].filter(i => i);
        const isBlocked = infos.some(i => i.is_blocked);
        const isOnline = infos.some(i => i.nclient > 0);
        const totalClients = infos.reduce((sum, i) => sum + (i.nclient || 0), 0);

        let casterStatusBadge = '<span class="badge border border-secondary text-muted rounded-pill opacity-50">Offline</span>';
        if (isBlocked) {
            casterStatusBadge = '<span class="badge badge-blocked rounded-pill"><i class="bi bi-shield-x me-1"></i>BLOCKED</span>';
        } else if (isOnline) {
            casterStatusBadge = `<span class="badge badge-active rounded-pill shadow-sm"><i class="bi bi-broadcast me-1"></i>ONLINE (${totalClients})</span>`;
        } else if (infos.length > 0) {
            casterStatusBadge = '<span class="badge bg-secondary bg-opacity-10 text-secondary border border-secondary border-opacity-25 rounded-pill">Standby</span>';
        }

        const dbBadge = r.type === 'db'
            ? (r.is_active ? '<i class="bi bi-check-circle-fill text-success ms-1" title="Account Active"></i>' : '<i class="bi bi-exclamation-circle-fill text-danger ms-1" title="Account Expired"></i>')
            : '<i class="bi bi-question-diamond-fill text-warning ms-1" title="Unknown User (Not in DB)"></i>';

        const targetHosts = parseCasterHosts(r.target_casters);
        let serverBadges = '';
        if (targetHosts.length > 0) {
            serverBadges = targetHosts.map(h => {
                const isPresent = infos.some(i => i.server === h);
                const color = isPresent ? 'text-success' : 'text-danger fw-bold';
                const icon = isPresent ? 'bi-server' : 'bi-exclamation-triangle';
                return `<span class="badge bg-dark border border-secondary text-muted me-1 mb-1 d-inline-flex align-items-center" style="font-size:0.65rem" title="${h}">
                            <i class="bi ${icon} ${color} me-1"></i>
                            ${h}
                        </span>`;
            }).join('');
        } else if (infos.length > 0) {
            serverBadges = infos.map(i => {
                return `<span class="badge bg-dark border border-secondary text-muted me-1 mb-1 d-inline-flex align-items-center" style="font-size:0.65rem" title="${i.server}">
                            <i class="bi bi-server text-warning me-1"></i>
                            ${i.server}
                        </span>`;
            }).join('');
        }

        const hasLiveShowPos = infos.some(i => typeof i?.show_position !== 'undefined');
        const actualShowPos = hasLiveShowPos
            ? infos.every(i => i?.show_position !== false)
            : r.show_position;
        const showPosIcon = actualShowPos
            ? '<i class="bi bi-eye-fill text-primary ms-1" title="Vị trí đang HIỆN (Live)"></i>'
            : '<i class="bi bi-eye-slash text-muted opacity-50 ms-1" title="Vị trí đang ẨN (Live)"></i>';

        const mps = normalizeMountpointList(r.mountpoints);
        const mpsDisplay = mps.length > 0
            ? mps.map(m => `<code class="text-info small" style="font-size:0.75rem">${m}</code>`).join(', ')
            : '<span class="text-muted small">N/A</span>';

        // Identify "orphan" mountpoints (those not belonging to any physical station)
        const stationMps = new Set();
        allDevices.forEach(d => {
            const s = d.service_config || {};
            if (s.mountpoint1) stationMps.add(s.mountpoint1);
            if (s.mountpoint2) stationMps.add(s.mountpoint2);
        });

        const manualMps = mps.filter(m => !stationMps.has(m));
        
        let displayItems = [];
        if (r.base_name && r.base_name.trim()) {
            displayItems.push(r.base_name);
        }
        manualMps.forEach(m => {
            if (!displayItems.includes(m)) displayItems.push(m);
        });

        const baseDisplay = displayItems.length > 0 
            ? displayItems.join(', ')
            : (mps.length > 0 ? mps[0] : 'N/A');

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
                const serverLabel = allActiveConns.length > 1 ? `<span class="text-muted mx-1" style="font-size:0.6rem">[${conn.server.split('.')[0]}]</span>` : '';
                currentRenderedConnectionKeys.add(getConnectionClockKey(conn));
                return `<div class="d-flex align-items-center mb-1">
                            <span class="badge bg-success bg-opacity-10 text-success border border-success border-opacity-25 me-1" style="font-size: 0.7rem;">${conn.mountpoint}</span>
                            ${serverLabel}
                            <span class="text-muted" style="font-size: 0.75rem;">
                                <i class="bi bi-clock me-1"></i>
                                <span class="live-clock" data-started-at="${stableStartedAt}" title="Bắt đầu: ${startedAtText} | Client: ${clientIdText}">${durText}</span>
                                <span class="ms-1 small text-muted opacity-75" style="font-size: 0.65rem;">(${startedAtText})</span>
                            </span>
                        </div>`;
            }).join('');
        }

        let actionHtml = '';
        if (currentUser.permissions?.includes('manage:caster')) {
            if (isBlocked) {
                actionHtml += `
                    <button class="btn btn-sm btn-outline-success border-0 caster-unblock-btn me-1" 
                        data-username="${r.username}" title="Mở khóa">
                        <i class="bi bi-unlock-fill"></i>
                    </button>`;
            } else {
                actionHtml += `
                    <button class="btn btn-sm btn-outline-danger border-0 caster-block-btn me-1" 
                        data-username="${r.username}" 
                        data-mountpoints="${mps.join(',')}"
                        title="Chặn truy cập">
                        <i class="bi bi-shield-lock-fill"></i>
                    </button>`;
            }
        }

        if (r.type === 'db') {
            actionHtml += `
                <button class="btn btn-sm btn-outline-warning edit-btn me-1 border-0" 
                    data-id="${r.id}" data-username="${r.username}" title="Sửa">
                    <i class="bi bi-pencil-square"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger delete-btn border-0" 
                    data-id="${r.id}" data-user="${r.username}" title="Xóa">
                    <i class="bi bi-trash3-fill"></i>
                </button>
            `;
        } else if (r.type === 'unknown') {
            actionHtml += `
                <button class="btn btn-sm btn-outline-warning edit-btn me-1 border-0" 
                    data-id="0" data-username="${r.username}" title="Chỉnh sửa và Đồng bộ về Web">
                    <i class="bi bi-pencil-square"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger border-0 caster-delete-btn" 
                    data-username="${r.username}" title="Xóa tài khoản khỏi Caster">
                    <i class="bi bi-trash3"></i>
                </button>
            `;
        }

        tr.innerHTML = `
            <td>
                <div class="d-flex align-items-center">
                    <div class="${r.type === 'db' ? 'bg-primary' : 'bg-danger'} bg-opacity-10 ${r.type === 'db' ? 'text-primary' : 'text-danger'} rounded-circle p-2 me-2">
                        <i class="bi bi-person-fill"></i>
                    </div>
                    <div class="d-flex flex-column">
                        <span class="fw-bold text-light">${r.username} ${dbBadge} ${showPosIcon}</span>
                        <span class="text-muted mt-1" style="font-size:0.7rem">${r.type === 'db' ? 'Database Account' : 'Unknown Caster User'}</span>
                    </div>
                </div>
            </td>
            <td><code class="text-warning bg-dark px-2 py-1 rounded small">${r.password || '******'}</code></td>
            <td>
                <div class="d-flex flex-column">
                    <span class="text-info fw-bold small" style="font-size:0.8rem">${baseDisplay}</span>
                    <div class="mt-1 d-flex flex-wrap gap-1">${mpsDisplay}</div>
                </div>
            </td>
            <td>
                <div>${activeConnsHtml}</div>
            </td>
            <td class="text-center">${casterStatusBadge}</td>
            <td class="text-center">${serverBadges || '<span class="badge bg-warning bg-opacity-10 text-warning border border-warning border-opacity-25" style="font-size:0.6rem">Cần gán Server</span>'}</td>
            <td class="text-end">${actionHtml}</td>
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
        // Start with DB rovers
        let mergedData = allRovers.map(r => ({
            ...r,
            type: 'db',
            // Aggregated info from ALL possible casters
            caster_infos: getCasterInfosForUsername(r.username)
        }));

        // Add Caster Users NOT in DB (Unknowns)
        casterUsers.forEach(cu => {
            if (!allRovers.find(r => normalizeUsername(r.username) === normalizeUsername(cu.username))) {
                mergedData.push({
                    username: cu.username,
                    type: 'unknown',
                    caster_info: cu,
                    mountpoints: cu.mountpoints.join(';'),
                    base_name: 'Unknown Caster User',
                    is_active: false
                });
            }
        });

        // 2. Filtering
        const filtered = mergedData.filter(r => {
            const matchSearch = r.username.toLowerCase().includes(searchTerm) || 
                                (r.base_serial && r.base_serial.toLowerCase().includes(searchTerm)) ||
                                (r.base_name && r.base_name.toLowerCase().includes(searchTerm));
            
            const matchStation = !stationFilter || (r.base_serial && r.base_serial.includes(stationFilter));
            
            // Status filter logic
            let matchStatus = true;
            if (statusFilter === 'active') matchStatus = r.type === 'db' && r.is_active;
            else if (statusFilter === 'blocked') matchStatus = r.caster_info?.is_blocked;
            else if (statusFilter === 'online') matchStatus = r.caster_info?.nclient > 0;
            else if (statusFilter === 'unknown') matchStatus = r.type === 'unknown';

            return matchSearch && matchStation && matchStatus;
        });

        // 2.5 Sorting: Online first
        filtered.sort((a, b) => {
            const aOnline = (a.caster_infos || [a.caster_info]).some(ci => ci && ci.nclient > 0);
            const bOnline = (b.caster_infos || [b.caster_info]).some(ci => ci && ci.nclient > 0);
            if (aOnline && !bOnline) return -1;
            if (!aOnline && bOnline) return 1;
            return 0;
        });

        // 3. Update Counts
        const countEl = document.getElementById('displayed-count');
        const totalEl = document.getElementById('total-rovers');
        if (countEl) countEl.textContent = filtered.length;
        if (totalEl) totalEl.textContent = mergedData.length;

        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center py-5 text-muted">Không tìm thấy dữ liệu phù hợp.</td></tr>`;
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
    }

    // === HANDLE EDIT ROVER (LOGIC MỚI) ===
    function handleEditRover(e) {
        const id = parseInt(e.currentTarget.dataset.id);
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
                    max_connections: 1
                };
            }
        }
        
        if (!rover) return;

        // 1. Điền thông tin cơ bản
        document.getElementById('edit-rover-id').value = rover.id;
        document.getElementById('edit-username').value = rover.username;
        document.getElementById('edit-password').value = ''; 
        document.getElementById('edit-expiry').value = ''; 
        document.getElementById('edit-max-connections').value = rover.max_connections || 1;
        
        const showPosToggle = document.getElementById('edit-show-position');
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
            const newExpiry = document.getElementById('edit-expiry').value;

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
                rtcm_disconnect_interval_seconds: parseInt(document.getElementById('edit-rtcm-disconnect-interval')?.value || 300),
                rtcm_disconnect_duration_seconds: parseInt(document.getElementById('edit-rtcm-disconnect-duration')?.value || 10)
            };
            if (newPass) payload.password = newPass;
            if (newExpiry) payload.expiry_days = parseInt(newExpiry);
            const maxConn = document.getElementById('edit-max-connections')?.value;
            if (maxConn) payload.max_connections = parseInt(maxConn);

            try {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang lưu...';

                // GỌI API THÔNG MINH (PUT)
                const url = roverId == "0" 
                    ? `/api/rovers/sync/by-username/${document.getElementById('edit-username').value}`
                    : `/api/rovers/${roverId}`;
                
                const res = await fetch(url, {
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
                fetchCasterUsers();

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
        const id = parseInt(btn.dataset.id);
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
                    <input class="form-check-input delete-host-check" type="checkbox" value="${host}" id="del-check-${host}" checked>
                    <label class="form-check-label small" for="del-check-${host}">${host}</label>
                `;
                serverList.appendChild(div);
            });
        }

        const modal = new bootstrap.Modal(document.getElementById('confirmDeleteModal'));
        modal.show();
    }

    // Nút thực thi xóa trong Modal
    document.getElementById('executeDeleteBtn').onclick = async function() {
        const roverId = document.getElementById('delete-rover-id').value;
        const deleteFromDb = document.getElementById('delete-from-db').checked;
        const checkedHosts = Array.from(document.querySelectorAll('.delete-host-check:checked')).map(cb => cb.value);
        const allHosts = Array.from(document.querySelectorAll('.delete-host-check')).map(cb => cb.value);
        
        const btn = this;
        const originalHtml = btn.innerHTML;

        try {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang xử lý...';

            if (deleteFromDb) {
                // TRƯỜNG HỢP 1: Xóa trắng hoàn toàn
                const res = await fetch(`/api/rovers/${roverId}`, { 
                    method: 'DELETE', 
                    headers: API_HEADERS 
                });
                if (!res.ok) throw new Error(await res.text() || 'Xóa hoàn toàn thất bại');
                showToast(`Đã xóa vĩnh viễn tài khoản khỏi hệ thống`, 'success');
            } else {
                // TRƯỜNG HỢP 2: Chỉ gỡ trên một số server (Partial Cleanup)
                // Những server BỊ TÍCH trong modal delete là những server CHÚNG TA MUỐN XÓA.
                // Vậy danh sách target_casters CÒN LẠI = Tổng - Bị tích.
                const remainingHosts = allHosts.filter(h => !checkedHosts.includes(h));
                
                if (checkedHosts.length === 0) {
                    showToast('Bạn chưa chọn server nào để gỡ bỏ!', 'warning');
                    btn.disabled = false;
                    btn.innerHTML = originalHtml;
                    return;
                }

                const res = await fetch(`/api/rovers/${roverId}`, {
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
            bootstrap.Modal.getInstance(document.getElementById('confirmDeleteModal')).hide();
            await refreshDataInPlace();
        } catch (err) {
            console.error(err);
            showToast(`Lỗi: ${err.message}`, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    };

    // === HANDLE DELETE CASTER USER (Unknown) ===
    async function handleDeleteCasterUser(e) {
        const btn = e.currentTarget;
        const username = btn.dataset.username;
        
        if (!confirm(`CẢNH BÁO: Tài khoản "${username}" không tồn tại trong DB nhưng đang có trên Caster.\n\nBạn có chắc muốn XÓA HOÀN TOÀN tài khoản này khỏi Caster?`)) return;

        try {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

            const res = await fetch(`/api/caster/users/${username}`, { 
                method: 'DELETE', 
                headers: API_HEADERS 
            });
            
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Xóa thất bại');
            }

            showToast(`Đã xóa tài khoản ngoại lai ${username} khỏi Caster`, 'warning');
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
            const msgEl = document.getElementById('create-error-msg');
            msgEl.classList.add('d-none');
            
            const checkedBoxes = document.querySelectorAll('.station-check:checked');
            const selectedSerials = Array.from(checkedBoxes).map(cb => cb.value);

            const mpOverride = document.getElementById('new-mountpoints')?.value || '';
            const isAdmin = String(currentUser?.role || '').toLowerCase() === 'admin';

            if (selectedSerials.length === 0 && !isAdmin && !mpOverride.trim()) {
                msgEl.textContent = "Vui lòng chọn ít nhất một trạm hoặc nhập Mountpoint.";
                msgEl.classList.remove('d-none');
                return;
            }

            if (!mpOverride.trim()) {
                msgEl.textContent = "Mountpoint không được để trống.";
                msgEl.classList.remove('d-none');
                return;
            }

            const targetCasters = Array.from(document.querySelectorAll('.caster-target-check:checked')).map(cb => cb.value);

            const payload = {
                username: document.getElementById('new-username').value,
                password: document.getElementById('new-password').value,
                expiry_days: parseInt(document.getElementById('new-expiry').value),
                selected_serials: selectedSerials,
                mountpoint_override: mpOverride,
                target_casters: targetCasters,
                max_connections: parseInt(document.getElementById('new-max-connections')?.value || 1),
                show_position: document.getElementById('new-show-position').checked,
                rtcm_cycle_enabled: document.getElementById('new-rtcm-cycle-enabled').checked,
                rtcm_disconnect_interval_seconds: parseInt(document.getElementById('new-rtcm-disconnect-interval').value || 300),
                rtcm_disconnect_duration_seconds: parseInt(document.getElementById('new-rtcm-disconnect-duration').value || 10)
            };

            try {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang tạo...';

                const res = await fetch(`/api/rovers/create-multi`, {
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
                document.querySelectorAll('.station-check').forEach(cb => cb.checked = false);
                await refreshDataInPlace();

            } catch (err) {
                msgEl.textContent = err.message;
                msgEl.classList.remove('d-none');
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

    // Rover Dashboard events
    document.getElementById('search-box').addEventListener('input', renderTable);
    document.getElementById('filter-station').addEventListener('change', renderTable);
    document.getElementById('filter-status').addEventListener('change', renderTable);

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
    
    // ✅ ADMIN ONLY: Tab Mountpoints
    const mountpointsTab = document.getElementById('tab-mountpoints');
    if (mountpointsTab) {
        mountpointsTab.addEventListener('shown.bs.tab', async () => {
            await loadAndRenderMountpoints();
        });
    }
    
    document.getElementById('refresh-btn').addEventListener('click', async () => {
        const btn = document.getElementById('refresh-btn');
        const icon = btn.querySelector('i');
        if (icon) icon.classList.add('spin-anim');
        
        await syncData();
        
        if (icon) icon.classList.remove('spin-anim');
        showToast('Đã đồng bộ dữ liệu hệ thống', 'info');
    });

    // ✅ Load and render mountpoints into tab
    async function loadAndRenderMountpoints() {
        const container = document.getElementById('mountpoints-container');
        if (!container) return;

        container.innerHTML = '<div class="text-center py-5"><div class="spinner-border spinner-border-sm me-2"></div><span class="text-muted">Đang tải danh sách mountpoints...</span></div>';

        try {
            const res = await fetch('/api/admin/caster-sources', { headers: API_HEADERS });
            if (!res.ok) throw new Error('Failed to fetch sources');
            
            const data = await res.json();
            const domains = Object.keys(data.by_domain || {}).sort();
            const sources = data.all || [];
            const hostOptions = Object.keys(data.by_host || {}).sort();
            let noSortAsc = true;

            const getRenderedSources = () => {
                if (noSortAsc) return sources;
                return [...sources].reverse();
            };

            if (sources.length === 0) {
                container.innerHTML = '<div class="alert alert-warning text-center mt-3"><i class="bi bi-exclamation-triangle me-2"></i>Không có mountpoint nào</div>';
                return;
            }

            let html = `
                <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
                    <button type="button" class="btn btn-success btn-sm" id="toggle-create-mp-btn">
                        <i class="bi bi-plus-circle me-1"></i>Tạo mountpoint
                    </button>
                    <div class="input-group input-group-sm flex-grow-1" style="max-width: 420px;">
                        <span class="input-group-text bg-dark border-secondary text-secondary"><i class="bi bi-search"></i></span>
                        <input type="text" class="form-control" id="mountpoints-search" placeholder="Tìm mountpoint, identifier, host...">
                    </div>
                </div>

                <div class="card bg-dark-subtle border-secondary mb-3 d-none" id="create-mp-panel">
                    <div class="card-body">
                        <div class="row g-2 align-items-end mb-2">
                            <div class="col-md-3">
                                <label class="form-label small text-muted fw-bold mb-1">Host</label>
                                <select class="form-select form-select-sm" id="create-mp-host">
                                    ${hostOptions.map(h => `<option value="${h}">${h}</option>`).join('')}
                                </select>
                            </div>
                            <div class="col-md-2">
                                <label class="form-label small text-muted fw-bold mb-1">No (ID)</label>
                                <input type="number" class="form-control form-control-sm" id="create-mp-index" min="1" placeholder="Auto">
                            </div>
                            <div class="col-md-3">
                                <label class="form-label small text-muted fw-bold mb-1">Identifier</label>
                                <input type="text" class="form-control form-control-sm" id="create-mp-identifier" placeholder="AitogyGNSS">
                            </div>
                            <div class="col-md-3">
                                <label class="form-label small text-muted fw-bold mb-1">Mountpoint</label>
                                <input type="text" class="form-control form-control-sm" id="create-mp-name" placeholder="ATG.NEW">
                            </div>
                            <div class="col-md-1">
                                <label class="form-label small text-muted fw-bold mb-1">Password</label>
                                <input type="text" class="form-control form-control-sm" id="create-mp-password" value="12345">
                            </div>
                        </div>
                        <div class="row g-2 align-items-end mb-2">
                            <div class="col-md-2">
                                <label class="form-label small text-muted fw-bold mb-1">Status</label>
                                <input type="number" class="form-control form-control-sm" id="create-mp-status" value="0" min="0" step="1">
                            </div>
                            <div class="col-md-3">
                                <label class="form-label small text-muted fw-bold mb-1">Latitude</label>
                                <input type="number" class="form-control form-control-sm" id="create-mp-lat" value="21.0717" step="0.000001">
                            </div>
                            <div class="col-md-3">
                                <label class="form-label small text-muted fw-bold mb-1">Longitude</label>
                                <input type="number" class="form-control form-control-sm" id="create-mp-lon" value="105.7735" step="0.000001">
                            </div>
                            <div class="col-md-4">
                                <div class="d-flex flex-wrap gap-3 small text-muted pt-2">
                                    <label class="form-check-label"><input class="form-check-input me-1" type="checkbox" id="create-mp-auth" checked>AuthRequired</label>
                                    <label class="form-check-label"><input class="form-check-input me-1" type="checkbox" id="create-mp-gga">GGA Required</label>
                                    <label class="form-check-label"><input class="form-check-input me-1" type="checkbox" id="create-mp-showpos">Show Position</label>
                                    <label class="form-check-label"><input class="form-check-input me-1" type="checkbox" id="create-mp-overlap">Overlap Data</label>
                                    <label class="form-check-label"><input class="form-check-input me-1" type="checkbox" id="create-mp-recal">Calculating Position</label>
                                    <label class="form-check-label"><input class="form-check-input me-1" type="checkbox" id="create-mp-store">Storing Data</label>
                                </div>
                            </div>
                        </div>
                        <div class="row g-2 align-items-end">
                            <div class="col-md-1 d-grid">
                                <button type="button" class="btn btn-sm btn-success" id="create-mp-btn">Tạo</button>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="mb-3">
                    <div class="btn-group w-100" role="group">
                        <input type="radio" class="btn-check" name="domain-filter" id="domain-all" value="all" checked>
                        <label class="btn btn-outline-primary" for="domain-all">📊 Tất cả (${sources.length})</label>
                        ${domains.map(d => `
                            <input type="radio" class="btn-check" name="domain-filter" id="domain-${d}" value="${d}">
                            <label class="btn btn-outline-info" for="domain-${d}">🌐 ${d} (${(data.by_domain[d] || []).length})</label>
                        `).join('')}
                    </div>
                </div>

                <div class="table-responsive rounded border border-secondary border-opacity-25 shadow-sm">
                    <table class="table table-dark table-hover table-sm mb-0 align-middle">
                        <thead>
                            <tr>
                                <th style="width: 70px;">
                                    <button type="button" class="btn btn-link btn-sm text-decoration-none p-0" id="sort-no-btn" title="Sắp xếp theo No">
                                        No <span id="sort-no-indicator">↑</span>
                                    </button>
                                </th>
                                <th>Mountpoint</th>
                                <th>Identifier</th>
                                <th>Domain / Host</th>
                                <th>Password</th>
                                <th>Data</th>
                                <th>Position</th>
                                <th>Format</th>
                                <th>Auth</th>
                                <th>GGA</th>
                                <th>ShowPos</th>
                                <th class="text-end">Hành động</th>
                            </tr>
                        </thead>
                        <tbody id="mountpoints-table-body">
                            ${getRenderedSources().map((src, idx) => `
                                <tr class="small" data-domain="${src.domain || src.host || ''}">
                                    <td><span class="text-muted fw-semibold">${idx + 1}</span></td>
                                    <td>
                                        <code class="text-info">${src.mountpoint}</code>
                                        <button type="button" class="btn btn-sm btn-outline-success ms-2 copy-mp" data-mp="${src.mountpoint}" title="Copy">📋</button>
                                    </td>
                                    <td><small class="text-muted">${src.identifier}</small></td>
                                    <td><span class="badge bg-secondary">${src.domain || src.host}</span></td>
                                    <td><small class="text-warning">${src.password || '-'}</small></td>
                                    <td><small>${Number(src.datacount || 0)}</small></td>
                                    <td>${src.latitude && src.longitude ? `<small>${src.latitude.toFixed(4)}, ${src.longitude.toFixed(4)}</small>` : '<span class="text-muted">N/A</span>'}</td>
                                    <td><small>${src.format}</small></td>
                                    <td>
                                        <span class="badge ${(src.auth_required === 'B' || src.auth_required === true || String(src.auth_required).toLowerCase() === 'true') ? 'bg-warning text-dark' : 'bg-success'}">
                                            ${(src.auth_required === 'B' || src.auth_required === true || String(src.auth_required).toLowerCase() === 'true') ? '🔒 Required' : '🟢 Open'}
                                        </span>
                                    </td>
                                    <td>
                                        <span class="badge ${(src.gga_required === true || String(src.gga_required).toLowerCase() === 'true' || src.gga_required === 1 || String(src.gga_required) === '1') ? 'bg-danger' : 'bg-info'}">
                                            ${(src.gga_required === true || String(src.gga_required).toLowerCase() === 'true' || src.gga_required === 1 || String(src.gga_required) === '1') ? '✅ Required' : '❌ No'}
                                        </span>
                                    </td>
                                    <td>
                                        <span class="badge ${(src.show_position === true || String(src.show_position).toLowerCase() === 'true' || src.show_position === 1 || String(src.show_position) === '1') ? 'bg-primary' : 'bg-secondary'}">
                                            ${(src.show_position === true || String(src.show_position).toLowerCase() === 'true' || src.show_position === 1 || String(src.show_position) === '1') ? '👁️ On' : '🚫 Off'}
                                        </span>
                                    </td>
                                    <td class="text-end">
                                        <button type="button" class="btn btn-sm btn-outline-danger delete-mp" data-host="${src.host || ''}" data-identifier="${src.identifier || ''}" data-mountpoint="${src.mountpoint || ''}">Xóa</button>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>

                <div class="mt-3 p-2 bg-dark-subtle rounded small text-muted">
                    <strong>💡 Tips:</strong>
                    <ul class="mb-0 ms-3">
                        <li>Click 📋 để copy mountpoint name</li>
                        <li>Dùng các mountpoint này khi tạo Rover mới</li>
                        <li>Admin có thể tạo/xóa mountpoint ngay tại đây</li>
                    </ul>
                </div>
            `;
            
            container.innerHTML = html;
            
            const toggleCreateMpBtn = container.querySelector('#toggle-create-mp-btn');
            const createMpPanel = container.querySelector('#create-mp-panel');
            if (toggleCreateMpBtn && createMpPanel) {
                toggleCreateMpBtn.addEventListener('click', () => {
                    createMpPanel.classList.toggle('d-none');
                });
            }

            const mountpointsSearch = container.querySelector('#mountpoints-search');
            const getSelectedDomain = () => {
                const checked = container.querySelector('input[name="domain-filter"]:checked');
                return checked ? checked.value : 'all';
            };
            const filterRows = () => {
                const q = String(mountpointsSearch?.value || '').trim().toLowerCase();
                const selectedDomain = getSelectedDomain();
                container.querySelectorAll('#mountpoints-table-body tr').forEach(row => {
                    const text = row.textContent.toLowerCase();
                    const rowDomain = String(row.dataset.domain || '').trim();
                    const matchText = !q || text.includes(q);
                    const matchDomain = selectedDomain === 'all' || rowDomain === selectedDomain;
                    row.style.display = (matchText && matchDomain) ? '' : 'none';
                });
            };
            if (mountpointsSearch) {
                mountpointsSearch.addEventListener('input', filterRows);
            }

            const sortNoBtn = container.querySelector('#sort-no-btn');
            const mountpointsBody = container.querySelector('#mountpoints-table-body');
            const sortNoIndicator = container.querySelector('#sort-no-indicator');
            if (sortNoBtn && mountpointsBody) {
                sortNoBtn.addEventListener('click', () => {
                    noSortAsc = !noSortAsc;
                    const renderedSources = getRenderedSources();
                    mountpointsBody.innerHTML = renderedSources.map((src, idx) => `
                        <tr class="small" data-domain="${src.domain || src.host || ''}">
                            <td><span class="text-muted fw-semibold">${idx + 1}</span></td>
                            <td>
                                <code class="text-info">${src.mountpoint}</code>
                                <button type="button" class="btn btn-sm btn-outline-success ms-2 copy-mp" data-mp="${src.mountpoint}" title="Copy">📋</button>
                            </td>
                            <td><small class="text-muted">${src.identifier}</small></td>
                            <td><span class="badge bg-secondary">${src.domain || src.host}</span></td>
                            <td><small class="text-warning">${src.password || '-'}</small></td>
                            <td><small>${Number(src.datacount || 0)}</small></td>
                            <td>${src.latitude && src.longitude ? `<small>${src.latitude.toFixed(4)}, ${src.longitude.toFixed(4)}</small>` : '<span class="text-muted">N/A</span>'}</td>
                            <td><small>${src.format}</small></td>
                            <td>
                                <span class="badge ${(src.auth_required === 'B' || src.auth_required === true || String(src.auth_required).toLowerCase() === 'true') ? 'bg-warning text-dark' : 'bg-success'}">
                                    ${(src.auth_required === 'B' || src.auth_required === true || String(src.auth_required).toLowerCase() === 'true') ? '🔒 Required' : '🟢 Open'}
                                </span>
                            </td>
                            <td>
                                <span class="badge ${(src.gga_required === true || String(src.gga_required).toLowerCase() === 'true' || src.gga_required === 1 || String(src.gga_required) === '1') ? 'bg-danger' : 'bg-info'}">
                                    ${(src.gga_required === true || String(src.gga_required).toLowerCase() === 'true' || src.gga_required === 1 || String(src.gga_required) === '1') ? '✅ Required' : '❌ No'}
                                </span>
                            </td>
                            <td>
                                <span class="badge ${(src.show_position === true || String(src.show_position).toLowerCase() === 'true' || src.show_position === 1 || String(src.show_position) === '1') ? 'bg-primary' : 'bg-secondary'}">
                                    ${(src.show_position === true || String(src.show_position).toLowerCase() === 'true' || src.show_position === 1 || String(src.show_position) === '1') ? '👁️ On' : '🚫 Off'}
                                </span>
                            </td>
                            <td class="text-end">
                                <button type="button" class="btn btn-sm btn-outline-danger delete-mp" data-host="${src.host || ''}" data-identifier="${src.identifier || ''}" data-mountpoint="${src.mountpoint || ''}">Xóa</button>
                            </td>
                        </tr>
                    `).join('');

                    if (sortNoIndicator) {
                        sortNoIndicator.textContent = noSortAsc ? '↑' : '↓';
                    }

                    filterRows();

                    container.querySelectorAll('.copy-mp').forEach(btn => {
                        btn.addEventListener('click', () => {
                            const mp = btn.dataset.mp;
                            navigator.clipboard.writeText(mp);
                            showToast(`Copied: ${mp}`, 'success');
                        });
                    });

                    container.querySelectorAll('.delete-mp').forEach(btn => {
                        btn.addEventListener('click', async () => {
                            const host = btn.dataset.host;
                            const identifier = btn.dataset.identifier;
                            const mountpoint = btn.dataset.mountpoint;
                            if (!mountpoint) return;
                            if (!confirm(`Xóa mountpoint "${mountpoint}" trên caster?`)) return;

                            try {
                                btn.disabled = true;
                                const res = await fetch('/api/admin/caster-sources', {
                                    method: 'DELETE',
                                    headers: API_HEADERS,
                                    body: JSON.stringify({ host, identifier, mountpoint })
                                });
                                const data = await res.json().catch(() => ({}));
                                if (!res.ok) throw new Error(data.detail || 'Không thể xóa mountpoint');
                                showToast(`Đã xóa mountpoint ${mountpoint}`, 'warning');
                                await fetchCasterSources({ force: true });
                                await loadAndRenderMountpoints();
                                renderAdminLiveMountpoints();
                            } catch (err) {
                                showToast(err.message, 'danger');
                            } finally {
                                btn.disabled = false;
                            }
                        });
                    });
                });
            }

            // Add copy button handlers
            container.querySelectorAll('.copy-mp').forEach(btn => {
                btn.addEventListener('click', () => {
                    const mp = btn.dataset.mp;
                    navigator.clipboard.writeText(mp);
                    showToast(`Copied: ${mp}`, 'success');
                });
            });

            const createMpBtn = container.querySelector('#create-mp-btn');
            if (createMpBtn) {
                createMpBtn.addEventListener('click', async () => {
                    const host = container.querySelector('#create-mp-host')?.value?.trim();
                    const indexRaw = container.querySelector('#create-mp-index')?.value?.trim();
                    const identifier = container.querySelector('#create-mp-identifier')?.value?.trim();
                    const mountpoint = container.querySelector('#create-mp-name')?.value?.trim();
                    const password = container.querySelector('#create-mp-password')?.value?.trim() || '12345';
                    const statusRaw = container.querySelector('#create-mp-status')?.value?.trim();
                    const latRaw = container.querySelector('#create-mp-lat')?.value?.trim();
                    const lonRaw = container.querySelector('#create-mp-lon')?.value?.trim();
                    const authRequired = Boolean(container.querySelector('#create-mp-auth')?.checked);
                    const ggaRequired = Boolean(container.querySelector('#create-mp-gga')?.checked);
                    const showPosition = Boolean(container.querySelector('#create-mp-showpos')?.checked);
                    const overlapData = Boolean(container.querySelector('#create-mp-overlap')?.checked);
                    const rePositioning = Boolean(container.querySelector('#create-mp-recal')?.checked);
                    const storingData = Boolean(container.querySelector('#create-mp-store')?.checked);

                    const index = indexRaw ? parseInt(indexRaw, 10) : null;
                    const counter = statusRaw ? parseInt(statusRaw, 10) : 0;
                    const latitude = latRaw ? parseFloat(latRaw) : null;
                    const longitude = lonRaw ? parseFloat(lonRaw) : null;

                    if (!identifier || !mountpoint) {
                        showToast('Vui lòng nhập identifier và mountpoint', 'warning');
                        return;
                    }

                    if (!Number.isFinite(counter) || counter < 0) {
                        showToast('Status phải là số nguyên >= 0', 'warning');
                        return;
                    }

                    if (index !== null && (!Number.isFinite(index) || index <= 0)) {
                        showToast('No (ID) phải là số nguyên > 0', 'warning');
                        return;
                    }

                    if (latitude !== null && !Number.isFinite(latitude)) {
                        showToast('Latitude không hợp lệ', 'warning');
                        return;
                    }

                    if (longitude !== null && !Number.isFinite(longitude)) {
                        showToast('Longitude không hợp lệ', 'warning');
                        return;
                    }

                    try {
                        createMpBtn.disabled = true;
                        createMpBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
                        const res = await fetch('/api/admin/caster-sources', {
                            method: 'POST',
                            headers: API_HEADERS,
                            body: JSON.stringify({
                                host,
                                identifier,
                                mountpoint,
                                password,
                                counter,
                                index,
                                latitude,
                                longitude,
                                auth_required: authRequired,
                                gga_required: ggaRequired,
                                show_position: showPosition,
                                overlap_data: overlapData,
                                re_positioning: rePositioning,
                                storing_data: storingData,
                            })
                        });
                        const data = await res.json().catch(() => ({}));
                        if (!res.ok) throw new Error(data.detail || 'Không thể tạo mountpoint');
                        showToast(`Đã tạo mountpoint ${mountpoint}`, 'success');
                        await fetchCasterSources({ force: true });
                        await loadAndRenderMountpoints();
                        renderAdminLiveMountpoints();
                    } catch (err) {
                        showToast(err.message, 'danger');
                    } finally {
                        createMpBtn.disabled = false;
                        createMpBtn.textContent = 'Tạo';
                    }
                });
            }

            container.querySelectorAll('.delete-mp').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const host = btn.dataset.host;
                    const identifier = btn.dataset.identifier;
                    const mountpoint = btn.dataset.mountpoint;
                    if (!mountpoint) return;
                    if (!confirm(`Xóa mountpoint "${mountpoint}" trên caster?`)) return;

                    try {
                        btn.disabled = true;
                        const res = await fetch('/api/admin/caster-sources', {
                            method: 'DELETE',
                            headers: API_HEADERS,
                            body: JSON.stringify({ host, identifier, mountpoint })
                        });
                        const data = await res.json().catch(() => ({}));
                        if (!res.ok) throw new Error(data.detail || 'Không thể xóa mountpoint');
                        showToast(`Đã xóa mountpoint ${mountpoint}`, 'warning');
                        await fetchCasterSources({ force: true });
                        await loadAndRenderMountpoints();
                        renderAdminLiveMountpoints();
                    } catch (err) {
                        showToast(err.message, 'danger');
                    } finally {
                        btn.disabled = false;
                    }
                });
            });

            // Add domain filter handlers
            container.querySelectorAll('input[name="domain-filter"]').forEach(radio => {
                radio.addEventListener('change', filterRows);
            });

            // Apply default filter state once after render.
            filterRows();

        } catch (e) {
            console.error("[Mountpoints]", e);
            container.innerHTML = `<div class="alert alert-danger mt-3"><i class="bi bi-exclamation-triangle me-2"></i>Lỗi tải mountpoints: ${e.message}</div>`;
        }
    }

    function showToast(msg, type = 'primary') {
        const container = document.getElementById('toast-container');
        const el = document.createElement('div');
        el.className = `toast align-items-center text-bg-${type} border-0 shadow-lg mb-2`;
        el.role = 'alert';
        el.innerHTML = `
            <div class="d-flex">
                <div class="toast-body fw-medium">${msg}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        container.appendChild(el);
        const t = new bootstrap.Toast(el, { delay: 4000 });
        t.show();
        el.addEventListener('hidden.bs.toast', () => el.remove());
    }
});