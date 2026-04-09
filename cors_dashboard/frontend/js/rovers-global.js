// rovers-global.js

document.addEventListener('DOMContentLoaded', async () => {
    // 1. Kiểm tra Token
    const token = localStorage.getItem('access_token');
    if (!token) {
        window.location.href = '/login.html';
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

    // === 2. AUTH & PERMISSIONS CHECK ===
    try {
        const userRes = await fetch('/api/auth/me', { headers: API_HEADERS });
        if (!userRes.ok) throw new Error("Unauthorized");
        currentUser = await userRes.json();

        // Cập nhật UI Navbar
        if(document.getElementById('user-info-name')) document.getElementById('user-info-name').textContent = currentUser.username;
        if(document.getElementById('user-info-role')) document.getElementById('user-info-role').textContent = currentUser.role.toUpperCase();
        if(document.getElementById('user-avatar-text')) document.getElementById('user-avatar-text').textContent = currentUser.username.charAt(0).toUpperCase();

        if (currentUser.permissions?.includes('manage:caster')) {
            const casterRoverTab = document.getElementById('caster-rover-tab-li');
            if (casterRoverTab) casterRoverTab.classList.remove('d-none');
        }

        initData();

    } catch (e) {
        console.error("Auth error:", e);
        window.location.href = '/login.html';
    }

    // === 3. DATA LOADING ===
    async function initData() {
        await fetchCasterConfig(); // Load available servers first
        await fetchDevices();
        
        // Parallel load DB and Caster data
        try {
            const results = await Promise.allSettled([
                fetchRovers(),
                fetchCasterUsers()
            ]);
            
            // If admin, show stats and info
            if (currentUser.permissions?.includes('manage:caster')) {
                document.getElementById('admin-stats-container')?.classList.remove('d-none');
                document.getElementById('admin-info-alert')?.classList.remove('d-none');
            }
            
            // Periodic sync (every 30s)
            setInterval(syncData, 30000);
            
        } catch (e) {
            console.error("Init data error", e);
        }
    }

    async function syncData() {
        const syncTimeEl = document.getElementById('caster-sync-time');
        if (syncTimeEl) syncTimeEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Syncing...';
        
        await Promise.allSettled([fetchRovers(), fetchCasterUsers()]);
        
        if (syncTimeEl) {
            const now = new Date();
            syncTimeEl.textContent = `Last sync: ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
        }
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
        const container = document.getElementById('target-caster-container');
        if (!container) return;
        
        container.innerHTML = '';
        if (availableCasters.length === 0) {
            container.innerHTML = '<div class="text-muted small">Không tìm thấy caster nào.</div>';
            return;
        }

        availableCasters.forEach(host => {
            const div = document.createElement('div');
            div.className = 'form-check form-check-inline mb-0';
            div.innerHTML = `
                <input class="form-check-input caster-target-check" type="checkbox" value="${host}" id="target-${host}" checked>
                <label class="form-check-label text-white small" for="target-${host}">${host}</label>
            `;
            container.appendChild(div);
        });
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

    async function fetchRovers() {
        try {
            const res = await fetch('/api/rovers/all', { headers: API_HEADERS });
            if (!res.ok) throw new Error('Không thể tải danh sách Rover');
            allRovers = await res.json();
            renderTable();
        } catch (error) {
            console.error("Rovers fetch error", error);
        }
    }

    // === CASTER USERS MANAGEMENT ===
    async function fetchCasterUsers() {
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
            renderTable(); // Call unified render
        } catch (error) {
            console.error("Caster users fetch error", error);
        }
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
        const allMps = new Set([...currentMps, ...MOUNTPOINT_EXAMPLES]);
        
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
            let mps = [];
            if (sConf.server1_enabled && sConf.mountpoint1) mps.push(sConf.mountpoint1);
            if (sConf.server2_enabled && sConf.mountpoint2) mps.push(sConf.mountpoint2);

            if (mps.length > 0) {
                const div = document.createElement('div');
                div.className = 'form-check';
                div.innerHTML = `
                    <input class="form-check-input station-check" type="checkbox" 
                        value="${d.serial}" 
                        id="check-${d.serial}"
                        data-mps="${mps.join(';')}">
                    <label class="form-check-label text-white small" for="check-${d.serial}">
                        ${d.name} <span class="text-muted">(${mps.join(',')})</span>
                    </label>
                `;
                container.appendChild(div);
            }
        });

        document.querySelectorAll('.station-check').forEach(cb => {
            cb.addEventListener('change', updateMountpointInput);
        });
    }

    function updateMountpointInput() {
        const checkedBoxes = document.querySelectorAll('.station-check:checked');
        let allMps = new Set();
        checkedBoxes.forEach(cb => {
            const mps = cb.dataset.mps.split(';');
            mps.forEach(mp => { if(mp) allMps.add(mp); });
        });
        document.getElementById('new-mountpoints').value = Array.from(allMps).join(';');
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

    // === UNIFIED RENDER TABLE ===
    function renderTable() {
        const tbody = document.getElementById('rovers-table-body');
        if (!tbody) return;

        const searchTerm = document.getElementById('search-box')?.value.toLowerCase() || '';
        const stationFilter = document.getElementById('filter-station')?.value || '';
        const statusFilter = document.getElementById('filter-status')?.value || '';

        tbody.innerHTML = '';

        // 1. Prepare merged data
        // Start with DB rovers
        let mergedData = allRovers.map(r => ({
            ...r,
            type: 'db',
            caster_info: casterUsers.find(cu => cu.username === r.username)
        }));

        // Add Caster Users NOT in DB (Unknowns)
        casterUsers.forEach(cu => {
            if (!allRovers.find(r => r.username === cu.username)) {
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
                                (r.base_name && r.base_name.toLowerCase().includes(searchTerm)) ||
                                (r.caster_info?.server && r.caster_info.server.toLowerCase().includes(searchTerm));
            
            const matchStation = !stationFilter || (r.base_serial && r.base_serial.includes(stationFilter));
            
            // Status filter logic
            let matchStatus = true;
            if (statusFilter === 'active') matchStatus = r.type === 'db' && r.is_active;
            else if (statusFilter === 'blocked') matchStatus = r.caster_info?.is_blocked;
            else if (statusFilter === 'online') matchStatus = r.caster_info?.nclient > 0;
            else if (statusFilter === 'unknown') matchStatus = r.type === 'unknown';

            return matchSearch && matchStation && matchStatus;
        });

        // 3. Update Counts
        const countEl = document.getElementById('displayed-count');
        const totalEl = document.getElementById('total-rovers');
        if (countEl) countEl.textContent = filtered.length;
        if (totalEl) totalEl.textContent = mergedData.length;

        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center py-5 text-muted">Không tìm thấy dữ liệu phù hợp.</td></tr>`;
            return;
        }

        // 4. Render Rows
        filtered.forEach(r => {
            const tr = document.createElement('tr');
            if (r.type === 'unknown') tr.style.backgroundColor = 'rgba(239, 68, 68, 0.03)';

            const cu = r.caster_info;
            
            // Caster Status Badge
            let casterStatusBadge = '<span class="badge border border-secondary text-muted rounded-pill opacity-50">Offline</span>';
            if (cu) {
                if (cu.is_blocked) {
                    casterStatusBadge = '<span class="badge badge-blocked rounded-pill"><i class="bi bi-shield-x me-1"></i>BLOCKED</span>';
                } else if (cu.nclient > 0) {
                    casterStatusBadge = `<span class="badge badge-active rounded-pill shadow-sm"><i class="bi bi-broadcast me-1"></i>ONLINE (${cu.nclient})</span>`;
                } else {
                    casterStatusBadge = '<span class="badge bg-secondary bg-opacity-10 text-secondary border border-secondary border-opacity-25 rounded-pill">Standby</span>';
                }
            }

            // DB Status Indicator
            const dbBadge = r.type === 'db' 
                ? (r.is_active ? '<i class="bi bi-check-circle-fill text-success ms-1" title="Account Active"></i>' : '<i class="bi bi-exclamation-circle-fill text-danger ms-1" title="Account Expired"></i>')
                : '<i class="bi bi-question-diamond-fill text-warning ms-1" title="Unknown User (Not in DB)"></i>';

            // Mountpoints
            const mps = (r.mountpoints || '').split(';').map(m => m.trim()).filter(m => m);
            const mpsDisplay = mps.length > 0
                ? mps.map(m => `<code class="text-info small" style="font-size:0.7rem">${m}</code>`).join(' ')
                : '<span class="text-muted small">N/A</span>';

            // Actions
            let actionHtml = '';
            
            // Admin actions
            if (currentUser.permissions?.includes('manage:caster')) {
                // Block/Unblock toggle
                if (cu?.is_blocked) {
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

            // Standard Edit/Delete (Only for DB users)
            if (r.type === 'db') {
                actionHtml += `
                    <button class="btn btn-sm btn-outline-warning edit-btn me-1 border-0" 
                        data-id="${r.id}" title="Sửa">
                        <i class="bi bi-pencil-square"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger delete-btn border-0" 
                        data-id="${r.id}" data-user="${r.username}" title="Xóa">
                        <i class="bi bi-trash3-fill"></i>
                    </button>
                `;
                // For unknown users, allow direct deletion from Caster
                actionHtml += `
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
                            <span class="fw-bold text-light">${r.username} ${dbBadge}</span>
                            <span class="text-muted" style="font-size:0.7rem">${r.type === 'db' ? 'Database Account' : 'Unknown Caster User'}</span>
                        </div>
                    </div>
                </td>
                <td><code class="text-warning bg-dark px-2 py-1 rounded small">${r.password || '******'}</code></td>
                <td>
                    <div class="d-flex flex-column">
                        <span class="text-info fw-bold small">${r.base_name || 'N/A'}</span>
                        <div class="mt-1">${mpsDisplay}</div>
                    </div>
                </td>
                <td class="text-center">${casterStatusBadge}</td>
                <td class="text-center text-muted small">${cu?.server || '---'}</td>
                <td class="text-end">${actionHtml}</td>
            `;
            tbody.appendChild(tr);
        });

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
        const rover = allRovers.find(r => r.id === id);
        
        if (!rover) return;

        // 1. Điền thông tin cơ bản
        document.getElementById('edit-rover-id').value = rover.id;
        document.getElementById('edit-username').value = rover.username;
        document.getElementById('edit-password').value = ''; 
        document.getElementById('edit-expiry').value = ''; 

        // 2. XỬ LÝ CHECKLIST TRẠM
        const container = document.getElementById('edit-station-checklist');
        container.innerHTML = ''; 
        
        const currentRoverMps = (rover.mountpoints || '').split(';');

        if (allDevices.length === 0) {
            container.innerHTML = '<div class="text-center small text-muted">Không có trạm nào khả dụng.</div>';
        } else {
            allDevices.forEach(d => {
                // Lấy các mountpoint thực tế của trạm này
                const sConf = d.service_config || {};
                let deviceMps = [];
                if (sConf.server1_enabled && sConf.mountpoint1) deviceMps.push(sConf.mountpoint1);
                if (sConf.server2_enabled && sConf.mountpoint2) deviceMps.push(sConf.mountpoint2);

                if (deviceMps.length > 0) {
                    const isChecked = deviceMps.some(mp => currentRoverMps.includes(mp));
                    
                    const div = document.createElement('div');
                    div.className = 'form-check';
                    div.innerHTML = `
                        <input class="form-check-input edit-station-check" type="checkbox" 
                            value="${d.serial}" 
                            id="edit-check-${d.serial}"
                            ${isChecked ? 'checked' : ''} 
                            data-mps="${deviceMps.join(';')}">
                        <label class="form-check-label text-white small" for="edit-check-${d.serial}">
                            ${d.name} <span class="text-muted">(${deviceMps.join(',')})</span>
                        </label>
                    `;
                    container.appendChild(div);
                }
            });
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

            // --- LẤY DỮ LIỆU TỪ CHECKLIST ---
            const checkedBoxes = document.querySelectorAll('.edit-station-check:checked');
            let allMps = new Set(); // Dùng Set để tránh trùng lặp
            
            checkedBoxes.forEach(cb => {
                const mps = cb.dataset.mps.split(';');
                mps.forEach(mp => { if(mp) allMps.add(mp); });
            });
            
            // Chuyển Set thành chuỗi ngăn cách bởi dấu chấm phẩy
            const finalMountpoints = Array.from(allMps).join(';');

            if (!finalMountpoints) {
                showToast('Vui lòng chọn ít nhất một trạm phát!', 'danger');
                return;
            }
            // --------------------------------

            const payload = {
                mountpoints: finalMountpoints
            };
            if (newPass) payload.password = newPass;
            if (newExpiry) payload.expiry_days = parseInt(newExpiry);

            try {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Đang lưu...';

                // GỌI API PUT
                const res = await fetch(`/api/rovers/${roverId}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                    },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.detail || 'Cập nhật thất bại');
                }

                showToast('Đã cập nhật Rover thành công!', 'success');
                
                // Ẩn modal và reload
                const modalEl = document.getElementById('editRoverModal');
                const modalInstance = bootstrap.Modal.getInstance(modalEl);
                modalInstance.hide();
                
                window.location.reload(); 

            } catch (err) {
                showToast(err.message, 'danger');
            } finally {
                const submitBtn = e.target.querySelector('button[type="submit"]');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-save me-2"></i>Lưu Thay Đổi';
            }
        });
    }

    // === HANDLE DELETE ROVER ===
    async function handleDeleteRover(e) {
        const btn = e.currentTarget;
        const id = btn.dataset.id;
        const username = btn.dataset.user;
        
        if (!confirm(`Bạn có chắc muốn xóa Rover "${username}"?`)) return;

        try {
            const res = await fetch(`/api/rovers/${id}`, { method: 'DELETE', headers: API_HEADERS });
            if (!res.ok) throw new Error('Xóa thất bại');
            showToast(`Đã xóa tài khoản ${username}`, 'success');
            fetchRovers();
        } catch (err) {
            showToast(err.message, 'danger');
        }
    }

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
            fetchRovers(); // Refresh data
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

            if (selectedSerials.length === 0) {
                msgEl.textContent = "Vui lòng chọn ít nhất một trạm.";
                msgEl.classList.remove('d-none');
                return;
            }

            const mpOverride = document.getElementById('new-mountpoints').value;
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
                target_casters: targetCasters
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
                fetchRovers();

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

    // Rover Dashboard events
    document.getElementById('search-box').addEventListener('input', renderTable);
    document.getElementById('filter-station').addEventListener('change', renderTable);
    document.getElementById('filter-status').addEventListener('change', renderTable);
    
    document.getElementById('refresh-btn').addEventListener('click', async () => {
        const btn = document.getElementById('refresh-btn');
        const icon = btn.querySelector('i');
        if (icon) icon.classList.add('spin-anim');
        
        await syncData();
        
        if (icon) icon.classList.remove('spin-anim');
        showToast('Đã đồng bộ dữ liệu hệ thống', 'info');
    });

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