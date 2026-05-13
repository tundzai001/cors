// rovers-global.js

document.addEventListener('DOMContentLoaded', async () => {
    // 1. Kiá»ƒm tra Token (Há»— trá»£ cáº£ Local vÃ  Session Storage)
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

    let currentUser = null;
    let allRovers = [];
    let allDevices = [];
    let casterUsers = [];  // Danh sÃ¡ch user tá»« Caster
    let casterStats = {};  // Stats tá»« API
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

        // Cáº­p nháº­t UI Navbar
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
                window.location.href = '/';
            }
        } else {
            // Hiá»ƒn thá»‹ thÃ´ng bÃ¡o lá»—i thay vÃ¬ vÄƒng ra login náº¿u chá»‰ lÃ  lá»—i máº¡ng/server
            if (typeof showToast === 'function') {
                showToast("Lá»—i káº¿t ná»‘i há»‡ thá»‘ng. Vui lÃ²ng táº£i láº¡i trang.", "danger");
            } else {
                alert("Lá»—i khá»Ÿi táº¡o dá»¯ liá»‡u. Vui lÃ²ng táº£i láº¡i trang.");
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
            if (syncTimeEl) syncTimeEl.textContent = 'Sync táº¡m dá»«ng khi Ä‘ang chá»‰nh sá»­a...';
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
                createContainer.innerHTML = '<div class="text-muted small">KhÃ´ng tÃ¬m tháº¥y caster nÃ o.</div>';
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
                editContainer.innerHTML = '<div class="text-muted small">KhÃ´ng tÃ¬m tháº¥y caster nÃ o.</div>';
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
            if (!res.ok) throw new Error('KhÃ´ng thá»ƒ táº£i danh sÃ¡ch Rover');
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
                throw new Error(err.detail || 'KhÃ´ng thá»ƒ táº£i danh sÃ¡ch user tá»« Caster');
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
                throw new Error(err.detail || 'KhÃ´ng thá»ƒ táº£i mountpoints tá»« Caster');
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
        if (label) label.textContent = 'Mountpoints Live tá»« Caster (Admin)';

        const sources = getAllCasterSources();
        if (!sources.length) {
            box.classList.remove('d-none');
            list.innerHTML = '<div class="text-muted small text-center">KhÃ´ng cÃ³ mountpoint nÃ o tá»« Caster</div>';
            return;
        }

        const uniqueHosts = Array.from(new Set(sources.map(s => String(s.host || '').trim()).filter(Boolean))).sort();
        if (hostFilter) {
            const prev = hostFilter.value || 'all';
            hostFilter.innerHTML = '<option value="all">Táº¥t cáº£ host</option>'
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
        if (label) label.textContent = 'MountPoints bá»• sung (ÄÆ°á»£c cáº¥p)';
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
            showToast('Vui lÃ²ng chá»n Ã­t nháº¥t 1 mountpoint', 'warning');
            return;
        }

        const targetId = isEdit ? 'edit-mountpoints' : 'new-mountpoints';
        const target = document.getElementById(targetId);
        const current = normalizeMountpointList(target?.value || '');
        const merged = Array.from(new Set([...current, ...checked]));
        
        if (target) target.value = merged.join(';');
        showToast('ÄÃ£ Ä‘Æ°a mountpoint live vÃ o Allowed Mountpoints', 'success');
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
        document.getElementById('blockReason').value = "QuÃ¡ háº¡n sá»­ dá»¥ng";
        document.getElementById('selectAllMps').checked = false;
        
        const container = document.getElementById('mountpointSelector');
        container.innerHTML = ""; // Clear

        // 1. Láº¥y danh sÃ¡ch mountpoint Rover nÃ y Ä‘ang dÃ¹ng
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

        document.getElementById('blockInfoText').innerText = `Cháº·n ${username} (${currentMps.length} tráº¡m hiá»‡n táº¡i)`;
        blockModal.show();
    }

    // Xá»­ lÃ½ Select All
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

    // XÃ¡c nháº­n cháº·n
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
                showToast("Vui lÃ²ng chá»n Ã­t nháº¥t 1 tráº¡m hoáº·c tÃ­ch 'Cháº·n toÃ n bá»™'", "warning");
                return;
            }
        }

        const btn = document.getElementById('confirmBlockBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Äang cháº·n...';

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
            if (!res.ok) throw new Error(data.detail || 'Cháº·n tháº¥t báº¡i');

            showToast(`ðŸ”’ ${data.message}`, 'success');
            blockModal.hide();
            await fetchCasterUsers(); // Reload table
        } catch (err) {
            showToast(err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-shield-lock-fill me-1"></i>XÃ¡c nháº­n cháº·n';
        }
        });
    }

    async function handleUnblockUser(e) {
        const username = e.currentTarget.dataset.username;
        if (!confirm(`Báº¡n cÃ³ cháº¯c muá»‘n má»Ÿ khÃ³a user "${username}"?`)) return;

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
            if (!res.ok) throw new Error(data.detail || 'Má»Ÿ khÃ³a tháº¥t báº¡i');

            showToast(`ðŸ”“ ${data.message}`, 'success');
            await fetchCasterUsers(); // Reload
        } catch (err) {
            showToast(err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-unlock-fill me-1"></i>Má»Ÿ khÃ³a';
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
            container.innerHTML = '<div class="text-center small text-muted">KhÃ´ng cÃ³ tráº¡m nÃ o kháº£ dá»¥ng.</div>';
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
        select.innerHTML = '<option value="">Táº¥t cáº£ tráº¡m Base</option>';
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

        let casterStatusBadge = '<span class="badge bg-danger bg-opacity-10 text-danger border border-danger border-opacity-25 rounded-pill opacity-75">OFFLINE</span>';
        if (isBlocked) {
            casterStatusBadge = '<span class="badge badge-blocked rounded-pill"><i class="bi bi-shield-x me-1"></i>BLOCKED</span>';
        } else if (isOnline) {
            casterStatusBadge = `<span class="badge badge-active rounded-pill shadow-sm"><i class="bi bi-broadcast me-1"></i>ONLINE (${totalClients})</span>`;
        } else if (infos.length > 0) {
            casterStatusBadge = '<span class="badge bg-info bg-opacity-10 text-info border border-info border-opacity-25 rounded-pill">STANDBY</span>';
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
            ? '<i class="bi bi-eye-fill text-primary ms-1" title="Vá»‹ trÃ­ Ä‘ang HIá»†N (Live)"></i>'
            : '<i class="bi bi-eye-slash text-muted opacity-50 ms-1" title="Vá»‹ trÃ­ Ä‘ang áº¨N (Live)"></i>';

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
                                <span class="live-clock" data-started-at="${stableStartedAt}" title="Báº¯t Ä‘áº§u: ${startedAtText} | Client: ${clientIdText}">${durText}</span>
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
                        data-username="${r.username}" title="Má»Ÿ khÃ³a">
                        <i class="bi bi-unlock-fill"></i>
                    </button>`;
            } else {
                actionHtml += `
                    <button class="btn btn-sm btn-outline-danger border-0 caster-block-btn me-1" 
                        data-username="${r.username}" 
                        data-mountpoints="${mps.join(',')}"
                        title="Cháº·n truy cáº­p">
                        <i class="bi bi-shield-lock-fill"></i>
                    </button>`;
            }
        }

        if (r.type === 'db') {
            actionHtml += `
                <button class="btn btn-sm btn-outline-warning edit-btn me-1 border-0" 
                    data-id="${r.id}" data-username="${r.username}" title="Sá»­a">
                    <i class="bi bi-pencil-square"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger delete-btn border-0" 
                    data-id="${r.id}" data-user="${r.username}" title="XÃ³a">
                    <i class="bi bi-trash3-fill"></i>
                </button>
            `;
        } else if (r.type === 'unknown') {
            actionHtml += `
                <button class="btn btn-sm btn-outline-warning edit-btn me-1 border-0" 
                    data-id="0" data-username="${r.username}" title="Chá»‰nh sá»­a vÃ  Äá»“ng bá»™ vá» Web">
                    <i class="bi bi-pencil-square"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger border-0 caster-delete-btn" 
                    data-username="${r.username}" title="XÃ³a tÃ i khoáº£n khá»i Caster">
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
            <td class="text-center">${serverBadges || '<span class="badge bg-warning bg-opacity-10 text-warning border border-warning border-opacity-25" style="font-size:0.6rem">Cáº§n gÃ¡n Server</span>'}</td>
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
        let mergedData = allRovers.map(r => ({
            ...r,
            type: 'db',
            caster_infos: getCasterInfosForUsername(r.username)
        }));

        casterUsers.forEach(cu => {
            if (!allRovers.find(r => normalizeUsername(r.username) === normalizeUsername(cu.username))) {
                mergedData.push({
                    username: cu.username,
                    type: 'unknown',
                    caster_info: cu,
                    mountpoints: cu.mountpoints.join(';'),
                    base_name: 'Caster Only User',
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
            
            let matchStatus = true;
            if (statusFilter === 'active') matchStatus = r.type === 'db' && r.is_active;
            else if (statusFilter === 'blocked') matchStatus = (r.caster_infos || [r.caster_info]).some(i => i?.is_blocked);
            else if (statusFilter === 'standby') matchStatus = !(r.caster_infos || [r.caster_info]).some(i => i?.nclient > 0 || i?.is_blocked);

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
            tbody.innerHTML = `<tr><td colspan="6" class="text-center py-5 text-muted">KhÃ´ng tÃ¬m tháº¥y dá»¯ liá»‡u phÃ¹ há»£p.</td></tr>`;
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

    // === HANDLE EDIT ROVER (LOGIC Má»šI) ===
    function handleEditRover(e) {
        const id = parseInt(e.currentTarget.dataset.id);
        const username = e.currentTarget.dataset.username;
        
        let rover = allRovers.find(r => r.id === id);
        if (!rover && username) {
            rover = allRovers.find(r => normalizeUsername(r.username) === normalizeUsername(username));
        }
        
        // Náº¿u khÃ´ng cÃ³ ID trong DB (Unknown), tÃ¬m trong danh sÃ¡ch Caster
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

        // 1. Äiá»n thÃ´ng tin cÆ¡ báº£n
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

        // 2. Xá»¬ LÃ CHECKLIST TRáº M
        const container = document.getElementById('edit-station-checklist');
        container.innerHTML = ''; 
        
        const currentRoverMps = normalizeMountpointList(rover.mountpoints);
        document.getElementById('edit-mountpoints').value = (rover.mountpoints || '');

        if (allDevices.length === 0) {
            container.innerHTML = '<div class="text-center small text-muted">KhÃ´ng cÃ³ tráº¡m nÃ o kháº£ dá»¥ng.</div>';
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

        // 3. Hiá»‡n Modal
        const modal = new bootstrap.Modal(document.getElementById('editRoverModal'));
        modal.show();
    }

    // Xá»­ lÃ½ Submit form Edit
    const editForm = document.getElementById('edit-rover-form');
    if (editForm) {
        editForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const roverId = document.getElementById('edit-rover-id').value;
            const newPass = document.getElementById('edit-password').value;
            const newExpiry = document.getElementById('edit-expiry').value;

            // --- Láº¤Y Dá»® LIá»†U Tá»ª CHECKLIST & TEXT BOX ---
            const checkedStations = document.querySelectorAll('.edit-station-check:checked');
            let selectedSerials = Array.from(checkedStations).map(cb => cb.value);
            
            // finalMountpoints is now taken from the text box (which is updated by checklist and manual input)
            const finalMountpoints = document.getElementById('edit-mountpoints').value.trim();

            if (!finalMountpoints) {
                showToast('Vui lÃ²ng chá»n Ã­t nháº¥t má»™t tráº¡m phÃ¡t hoáº·c nháº­p mountpoint!', 'danger');
                return;
            }

            const payload = {
                mountpoints: finalMountpoints,
                selected_serials: selectedSerials, // ðŸ”¥ FIX: Gá»­i kÃ¨m danh sÃ¡ch serial tráº¡m
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
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Äang lÆ°u...';

                // Gá»ŒI API THÃ”NG MINH (PUT)
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
                    throw new Error(err.detail || 'Cáº­p nháº­t tháº¥t báº¡i');
                }

                const updatedRover = await res.json();

                showToast('ÄÃ£ cáº­p nháº­t Rover thÃ nh cÃ´ng!', 'success');
                
                // áº¨n modal vÃ  cáº­p nháº­t data táº¡i chá»— (khÃ´ng reload toÃ n trang)
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
                submitBtn.innerHTML = '<i class="bi bi-save me-2"></i>LÆ°u Thay Äá»•i';
            }
        });
    }

    // === HANDLE DELETE ROVER (MODAL Má»šI) ===
    async function handleDeleteRover(e) {
        const btn = e.currentTarget;
        const id = parseInt(btn.dataset.id);
        const username = btn.dataset.username || btn.dataset.user;
        
        let rover = allRovers.find(r => r.id === id);
        if (!rover && username) {
            rover = allRovers.find(r => normalizeUsername(r.username) === normalizeUsername(username));
        }
        // Náº¿u lÃ  Unknown (id=0), táº¡o Ä‘á»‘i tÆ°á»£ng giáº£ láº­p Ä‘á»ƒ Modal váº«n cháº¡y
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
        
        // Máº·c Ä‘á»‹nh lÃ  TÃCH CHá»ŒN xÃ³a khá»i DB (trá»« khi lÃ  tÃ i khoáº£n ngoáº¡i lai id=0)
        const dbCheck = document.getElementById('delete-from-db');
        dbCheck.checked = (id !== 0);
        
        // áº¨n vÃ¹ng xÃ³a DB náº¿u lÃ  tÃ i khoáº£n ngoáº¡i lai
        const dbSection = dbCheck.closest('.mb-2');
        if (id === 0) {
            dbSection.style.display = 'none';
        } else {
            dbSection.style.display = 'block';
        }

        // Populate server list
        const serverList = document.getElementById('delete-server-list');
        serverList.innerHTML = '';
        
        // Láº¥y danh sÃ¡ch server: Æ¯u tiÃªn target_casters, náº¿u trá»‘ng thÃ¬ láº¥y thá»±c táº¿ tá»« caster_infos
        let hosts = parseCasterHosts(rover.target_casters);
        let relatedCasterInfos = Array.isArray(rover.caster_infos) ? rover.caster_infos : [];
        if (relatedCasterInfos.length === 0) {
            relatedCasterInfos = getCasterInfosForUsername(username || rover.username);
        }
        if (hosts.length === 0 && relatedCasterInfos.length > 0) {
            hosts = Array.from(new Set(relatedCasterInfos.map(i => String(i?.server || '').trim()).filter(Boolean)));
        }
        
        if (hosts.length === 0) {
            serverList.innerHTML = '<div class="text-muted small">KhÃ´ng tÃ¬m tháº¥y server nÃ o liÃªn quan.</div>';
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

    // NÃºt thá»±c thi xÃ³a trong Modal
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
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Äang xá»­ lÃ½...';

            if (deleteFromDb) {
                // TRÆ¯á»œNG Há»¢P 1: XÃ³a tráº¯ng hoÃ n toÃ n
                const res = await fetch(`/api/rovers/${roverId}`, { 
                    method: 'DELETE', 
                    headers: API_HEADERS 
                });
                if (!res.ok) throw new Error(await res.text() || 'XÃ³a hoÃ n toÃ n tháº¥t báº¡i');
                showToast(`ÄÃ£ xÃ³a vÄ©nh viá»…n tÃ i khoáº£n khá»i há»‡ thá»‘ng`, 'success');
            } else {
                // TRÆ¯á»œNG Há»¢P 2: Chá»‰ gá»¡ trÃªn má»™t sá»‘ server (Partial Cleanup)
                // Nhá»¯ng server ÄÆ¯á»¢C TÃCH trong modal delete lÃ  nhá»¯ng server MUá»N Gá»  Bá»Ž.
                // Danh sÃ¡ch target_casters CÃ’N Láº I = Tá»•ng - ÄÃ£ tÃ­ch gá»¡.
                const remainingHosts = allHosts.filter(h => !checkedHosts.includes(h));
                
                if (checkedHosts.length === 0) {
                    showToast('Vui lÃ²ng tÃ­ch chá»n Ã­t nháº¥t 1 server Ä‘á»ƒ gá»¡ bá» (hoáº·c chá»n XÃ³a khá»i DB)', 'warning');
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

                if (!res.ok) throw new Error(await res.text() || 'Cáº­p nháº­t gá»¡ bá» tháº¥t báº¡i');
                showToast(`ÄÃ£ gá»¡ bá» tÃ i khoáº£n khá»i ${checkedHosts.length} server`, 'success');
            }

            // ÄÃ³ng modal vÃ  refresh
            const modalEl = document.getElementById('deleteRoverModal');
            if (modalEl) {
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
            }
            await refreshDataInPlace();
        } catch (err) {
            console.error(err);
            showToast(`Lá»—i: ${err.message}`, 'danger');
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
        
        if (!confirm(`Cáº¢NH BÃO: TÃ i khoáº£n "${username}" khÃ´ng tá»“n táº¡i trong DB nhÆ°ng Ä‘ang cÃ³ trÃªn Caster.\n\nBáº¡n cÃ³ cháº¯c muá»‘n XÃ“A HOÃ€N TOÃ€N tÃ i khoáº£n nÃ y khá»i Caster?`)) return;

        try {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

            const res = await fetch(`/api/caster/users/${username}`, { 
                method: 'DELETE', 
                headers: API_HEADERS 
            });
            
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'XÃ³a tháº¥t báº¡i');
            }

            showToast(`ÄÃ£ xÃ³a tÃ i khoáº£n ngoáº¡i lai ${username} khá»i Caster`, 'warning');
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
            if (msgEl) msgEl.classList.add('d-none');
            
            const checkedBoxes = document.querySelectorAll('.station-check:checked');
            const selectedSerials = Array.from(checkedBoxes).map(cb => cb.value);

            const mpOverride = document.getElementById('new-mountpoints')?.value || '';
            const isAdmin = String(currentUser?.role || '').toLowerCase() === 'admin';

            if (selectedSerials.length === 0 && !isAdmin && !mpOverride.trim()) {
                if (msgEl) {
                    msgEl.textContent = "Vui lÃ²ng chá»n Ã­t nháº¥t má»™t tráº¡m hoáº·c nháº­p Mountpoint.";
                    msgEl.classList.remove('d-none');
                }
                return;
            }

            if (!mpOverride.trim()) {
                if (msgEl) {
                    msgEl.textContent = "Mountpoint khÃ´ng Ä‘Æ°á»£c Ä‘á»ƒ trá»‘ng.";
                    msgEl.classList.remove('d-none');
                }
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
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Äang táº¡o...';

                const res = await fetch(`/api/rovers/create-multi`, {
                    method: 'POST',
                    headers: API_HEADERS,
                    body: JSON.stringify(payload)
                });
                
                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.detail || 'Lá»—i táº¡o Rover');
                }

                showToast(`Táº¡o Rover "${payload.username}" thÃ nh cÃ´ng!`, 'success');
                bootstrap.Modal.getInstance(document.getElementById('createRoverModal')).hide();
                createForm.reset();
                document.querySelectorAll('.station-check').forEach(cb => cb.checked = false);
                await refreshDataInPlace();

            } catch (err) {
                if (msgEl) {
                    msgEl.textContent = err.message;
                    msgEl.classList.remove('d-none');
                }
            } finally {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Táº¡o TÃ i Khoáº£n';
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
    const searchBox = document.getElementById('search-box');
    if (searchBox) searchBox.addEventListener('input', renderTable);
    
    const filterStation = document.getElementById('filter-station');
    if (filterStation) filterStation.addEventListener('change', renderTable);
    
    const filterStatus = document.getElementById('filter-status');
    if (filterStatus) filterStatus.addEventListener('change', renderTable);

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
            showToast('ÄÃ£ táº£i láº¡i mountpoints live', 'info');
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
            showToast('ÄÃ£ táº£i láº¡i mountpoints live', 'info');
        });
    }
    
    // âœ… ADMIN ONLY: Tab Mountpoints
    const mountpointsTab = document.getElementById('tab-mountpoints-li');
    if (mountpointsTab) {
        mountpointsTab.addEventListener('shown.bs.tab', async () => {
            await loadAndRenderMountpoints();
        });
    }

    // âœ… ADMIN ONLY: Tab Blocklist
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
        showToast('ÄÃ£ Ä‘á»“ng bá»™ dá»¯ liá»‡u há»‡ thá»‘ng', 'info');
    });

    // âœ… Load and render blocklist relationships
    async function renderBlocklistMapping() {
        const container = document.getElementById('blocklist-mapping-container');
        if (!container) return;

        container.innerHTML = `
            <div class="text-center py-5">
                <div class="spinner-border spinner-border-sm text-info me-2"></div>
                <span class="text-muted">Äang táº£i danh sÃ¡ch quan há»‡ cháº·n...</span>
            </div>
        `;

        try {
            const res = await fetch('/api/caster/blocklist', { headers: API_HEADERS });
            if (!res.ok) throw new Error('KhÃ´ng thá»ƒ táº£i blocklist');
            const data = await res.json();

            if (data.length === 0) {
                container.innerHTML = `
                    <div class="premium-alert mb-4">
                        <div class="alert-content">
                            <i class="bi bi-info-circle-fill"></i>
                            <span class="alert-text">Há»‡ thá»‘ng Ä‘ang hiá»ƒn thá»‹ cÃ¡c quy táº¯c cháº·n Ä‘á»™ng dá»±a trÃªn IP vÃ  Mountpoint. CÃ¡c quy táº¯c nÃ y Ä‘Æ°á»£c Ä‘á»“ng bá»™ 5s/láº§n tá»« Core Caster.</span>
                        </div>
                    </div>
                    <div class="text-center py-5 opacity-50">
                        <i class="bi bi-shield-lock d-block mb-3 h1"></i>
                        <p class="mb-0">Hiá»‡n táº¡i khÃ´ng cÃ³ quy táº¯c cháº·n chá»§ Ä‘á»™ng nÃ o Ä‘Æ°á»£c thiáº¿t láº­p.</p>
                        <small>CÃ¡c tÃ i khoáº£n bá»‹ Block sáº½ hiá»ƒn thá»‹ tráº¡ng thÃ¡i BLOCKED trong danh sÃ¡ch Rover.</small>
                    </div>
                `;
                return;
            }

            let html = `
                <div class="premium-alert mb-4">
                    <div class="alert-content">
                        <i class="bi bi-shield-fill-check text-success"></i>
                        <span class="alert-text">CÃ³ <strong>${data.length}</strong> quy táº¯c cháº·n Ä‘ang hoáº¡t Ä‘á»™ng. CÃ¡c tÃ i khoáº£n nÃ y sáº½ bá»‹ tá»« chá»‘i káº¿t ná»‘i ngay láº­p tá»©c.</span>
                    </div>
                </div>
                <div class="premium-table-card border-0 bg-transparent">
                    <table class="premium-table">
                        <thead>
                            <tr>
                                <th>TÃ i khoáº£n</th>
                                <th>Mountpoint</th>
                                <th>NgÃ y cháº·n</th>
                                <th>NgÆ°á»i cháº·n</th>
                                <th>LÃ½ do</th>
                                <th class="text-end">Thao tÃ¡c</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${data.map(bl => `
                                <tr>
                                    <td><span class="text-white fw-bold">${bl.username}</span></td>
                                    <td><span class="badge bg-danger bg-opacity-10 text-danger border border-danger border-opacity-25 px-2 py-1">${bl.mountpoint || 'Táº¤T Cáº¢'}</span></td>
                                    <td><span class="text-muted small">${new Date(bl.blocked_at * 1000).toLocaleString('vi-VN')}</span></td>
                                    <td><span class="text-secondary small">${bl.blocked_by || 'System'}</span></td>
                                    <td><span class="text-muted small">${bl.reason || '--'}</span></td>
                                    <td class="text-end">
                                        <button class="btn btn-sm btn-outline-success unblock-direct-btn py-1 px-3 rounded-pill" 
                                            data-username="${bl.username}" data-mountpoint="${bl.mountpoint || ''}">
                                            <i class="bi bi-unlock-fill me-1"></i> Má»Ÿ khÃ³a
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
                    if (!confirm(`XÃ¡c nháº­n má»Ÿ khÃ³a cho tÃ i khoáº£n "${username}"?`)) return;
                    
                    try {
                        btn.disabled = true;
                        const res = await fetch('/api/caster/unblock-user', {
                            method: 'POST',
                            headers: API_HEADERS,
                            body: JSON.stringify({ username, mountpoint: mountpoint || null })
                        });
                        if (!res.ok) throw new Error('Má»Ÿ khÃ³a tháº¥t báº¡i');
                        showToast(`ÄÃ£ má»Ÿ khÃ³a tÃ i khoáº£n ${username}`, 'success');
                        renderBlocklistMapping(); // Refresh this tab
                        syncData(); // Refresh main list
                    } catch (err) {
                        showToast(err.message, 'danger');
                        btn.disabled = false;
                    }
                };
            });

        } catch (e) {
            container.innerHTML = `<div class="alert alert-danger">Lá»—i: ${e.message}</div>`;
        }
    }

    // âœ… Load and render mountpoints into tab
    async function loadAndRenderMountpoints() {
        const container = document.getElementById('mountpoints-container');
        if (!container) return;

        container.innerHTML = '<div class="text-center py-5"><div class="spinner-border spinner-border-sm text-info me-2"></div><span class="text-muted">Äang táº£i danh sÃ¡ch mountpoints...</span></div>';

        try {
            const res = await fetch('/api/admin/caster-sources', { headers: API_HEADERS });
            if (!res.ok) throw new Error('Failed to fetch sources');
            
            const data = await res.json();
            const domains = Object.keys(data.by_domain || {}).sort();
            const sources = data.all || [];
            const hostOptions = Object.keys(data.by_host || {}).sort();

            if (sources.length === 0) {
                container.innerHTML = '<div class="alert alert-warning text-center mt-3"><i class="bi bi-exclamation-triangle me-2"></i>KhÃ´ng cÃ³ mountpoint nÃ o</div>';
                return;
            }

            let html = `
                <div class="d-flex flex-wrap justify-content-between align-items-center mb-4 gap-3">
                    <div class="d-flex gap-2">
                        <button type="button" class="btn btn-premium-primary btn-sm px-3" data-bs-toggle="modal" data-bs-target="#createMountpointModal">
                            <i class="bi bi-plus-circle me-1"></i> Táº¡o mountpoint
                        </button>
                    </div>
                    <div class="search-input-wrapper" style="max-width: 400px;">
                        <i class="bi bi-search"></i>
                        <input type="text" class="form-control premium-input" id="mountpoints-search" placeholder="TÃ¬m tÃªn, identifier, host...">
                    </div>
                </div>

                <div class="mb-4">
                    <div class="premium-tabs nav nav-pills" id="domain-filter-pills" role="tablist">
                        <button class="nav-link active" data-domain="all">
                            <i class="bi bi-grid-fill me-1"></i> Táº¥t cáº£ <span class="badge bg-white bg-opacity-25 ms-1">${sources.length}</span>
                        </button>
                        ${domains.map(d => `
                            <button class="nav-link" data-domain="${d}">
                                <i class="bi bi-hdd-network me-1"></i> ${d} <span class="badge bg-white bg-opacity-25 ms-1">${(data.by_domain[d] || []).length}</span>
                            </button>
                        `).join('')}
                    </div>
                </div>

                <div class="premium-table-card">
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
                            ${sources.map((src, idx) => `
                                <tr data-domain="${src.domain || src.host || ''}">
                                    <td class="text-center"><span class="text-muted small">${idx + 1}</span></td>
                                    <td>
                                        <div class="d-flex align-items-center gap-2">
                                            <span class="name text-info font-monospace fw-bold">${src.mountpoint || '--'}</span>
                                            <button type="button" class="btn btn-xs btn-link text-secondary p-0 copy-mp" data-mp="${src.mountpoint}" title="Copy">
                                                <i class="bi bi-clipboard"></i>
                                            </button>
                                        </div>
                                    </td>
                                    <td><span class="text-white small">${src.identifier || '--'}</span></td>
                                    <td><span class="badge bg-dark border border-secondary text-secondary fw-normal px-2 py-1">${src.domain || src.host || '--'}</span></td>
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
                                            <span class="small ${src.latitude && src.longitude ? 'text-white' : 'text-muted'}">
                                                ${src.latitude && src.longitude ? `${src.latitude.toFixed(4)}, ${src.longitude.toFixed(4)}` : '--'}
                                            </span>
                                            <span class="badge-status ${src.show_position ? 'ok' : 'blocked'} mt-1" style="width:fit-content; font-size:9px;">
                                                SHOW POS: ${src.show_position ? 'ON' : 'OFF'}
                                            </span>
                                        </div>
                                    </td>
                                    <td>
                                        <div class="small">
                                            <span class="text-secondary">Data:</span> <span class="text-success fw-bold">${Number(src.datacount || 0)}</span>
                                        </div>
                                    </td>
                                    <td class="text-end">
                                        <button type="button" class="action-btn text-danger delete-mp" data-host="${src.host || ''}" data-identifier="${src.identifier || ''}" data-mountpoint="${src.mountpoint || ''}" title="Delete">
                                            <i class="bi bi-trash3"></i>
                                        </button>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>

                <div class="mt-4 p-3 bg-dark-subtle rounded border border-secondary border-opacity-25 small">
                    <div class="d-flex align-items-center gap-2 text-info mb-2">
                        <i class="bi bi-lightbulb"></i>
                        <span class="fw-bold text-uppercase" style="letter-spacing: 0.5px">HÆ°á»›ng dáº«n nhanh</span>
                    </div>
                    <ul class="text-muted mb-0 ps-3">
                        <li>Sá»­ dá»¥ng cá»™t <strong>Mountpoint</strong> khi cáº¥u hÃ¬nh quyá»n truy cáº­p cho tÃ i khoáº£n Rover.</li>
                        <li>Tráº¡ng thÃ¡i <strong>AUTH</strong> hiá»ƒn thá»‹ cÃ¡c luá»“ng yÃªu cáº§u xÃ¡c thá»±c tÃ i khoáº£n.</li>
                        <li>Nháº¥n vÃ o biá»ƒu tÆ°á»£ng ðŸ“‹ Ä‘á»ƒ sao chÃ©p nhanh tÃªn Mountpoint.</li>
                    </ul>
                </div>
            `;

            container.innerHTML = html;

            // --- Modal Host Population ---
            const modalHostSelect = document.getElementById('create-mp-host');
            if (modalHostSelect) {
                modalHostSelect.innerHTML = hostOptions.map(h => `<option value="${h}">${h}</option>`).join('');
            }

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
                btn.onclick = () => {
                    const mp = btn.dataset.mp;
                    navigator.clipboard.writeText(mp);
                    showToast(`ÄÃ£ copy: ${mp}`, 'success');
                };
            });

            // Action: Delete
            container.querySelectorAll('.delete-mp').forEach(btn => {
                btn.onclick = async () => {
                    const { host, identifier, mountpoint } = btn.dataset;
                    if (!mountpoint || !confirm(`Báº¡n cÃ³ cháº¯c cháº¯n muá»‘n xÃ³a Mountpoint "${mountpoint}"?`)) return;

                    try {
                        btn.disabled = true;
                        const res = await fetch('/api/admin/caster-sources', {
                            method: 'DELETE',
                            headers: API_HEADERS,
                            body: JSON.stringify({ host, identifier, mountpoint })
                        });
                        if (!res.ok) throw new Error((await res.json())?.detail || 'Lá»—i khi xÃ³a');
                        showToast('ÄÃ£ xÃ³a Mountpoint thÃ nh cÃ´ng', 'warning');
                        await fetchCasterSources({ force: true });
                        loadAndRenderMountpoints();
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
                    const payload = {
                        host: document.getElementById('create-mp-host').value,
                        mountpoint: document.getElementById('create-mp-name').value.trim(),
                        password: document.getElementById('create-mp-password').value.trim(),
                        identifier: document.getElementById('create-mp-identifier').value.trim(),
                        latitude: parseFloat(document.getElementById('create-mp-lat').value) || 0,
                        longitude: parseFloat(document.getElementById('create-mp-lon').value) || 0,
                        auth_required: document.getElementById('create-mp-auth').checked,
                        gga_required: document.getElementById('create-mp-gga').checked,
                        show_position: document.getElementById('create-mp-showpos').checked,
                        storing_data: true,
                        overlap_data: false,
                        re_positioning: false
                    };

                    try {
                        btn.disabled = true;
                        const res = await fetch('/api/admin/caster-sources', {
                            method: 'POST',
                            headers: API_HEADERS,
                            body: JSON.stringify(payload)
                        });
                        if (!res.ok) throw new Error((await res.json())?.detail || 'Lá»—i khi táº¡o');
                        
                        showToast('ÄÃ£ táº¡o Mountpoint thÃ nh cÃ´ng', 'success');
                        const modalEl = document.getElementById('createMountpointModal');
                        if (modalEl) {
                            const modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
                            modal.hide();
                        }
                        createForm.reset();
                        await fetchCasterSources({ force: true });
                        loadAndRenderMountpoints();
                    } catch (err) {
                        showToast(err.message, 'danger');
                    } finally {
                        btn.disabled = false;
                    }
                });
                createForm.dataset.listenerAttached = "true";
            }

        } catch (e) {
            console.error("[Mountpoints]", e);
            container.innerHTML = `<div class="alert alert-danger mt-3"><i class="bi bi-exclamation-triangle me-2"></i>Lá»—i táº£i dá»¯ liá»‡u: ${e.message}</div>`;
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

    // === Premium UI Interaction ===
    document.addEventListener('click', (e) => {
        // Password Toggle
        if (e.target.classList.contains('toggle-password')) {
            const cell = e.target.closest('.password-cell');
            const masked = cell.querySelector('.pw-masked');
            const password = cell.dataset.password;
            const icon = e.target;

            if (masked.textContent === 'â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢') {
                masked.textContent = password || '******';
                icon.classList.replace('bi-eye', 'bi-eye-slash');
            } else {
                masked.textContent = 'â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢';
                icon.classList.replace('bi-eye-slash', 'bi-eye');
            }
        }
    });
});
