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
            const casterTab = document.getElementById('caster-tab-li');
            if (casterTab) casterTab.classList.remove('d-none');

            const casterRoverTab = document.getElementById('caster-rover-tab-li');
            if (casterRoverTab) casterRoverTab.classList.remove('d-none');

            loadCasterConfig();
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
        await fetchRovers();
        // Load caster users khi tab được click lần đầu
        const casterRoverTab = document.getElementById('caster-rover-tab');
        if (casterRoverTab) {
            casterRoverTab.addEventListener('shown.bs.tab', () => {
                if (casterUsers.length === 0) fetchCasterUsers();
            });
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
            showToast(error.message, 'danger');
        }
    }

    // === CASTER USERS MANAGEMENT ===
    async function fetchCasterUsers() {
        const tbody = document.getElementById('caster-table-body');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center py-5 text-muted">
                <div class="spinner-border spinner-border-sm me-2"></div> Đang tải dữ liệu từ Caster...
            </td></tr>`;
        }

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
            renderCasterTable();
        } catch (error) {
            showToast(error.message, 'danger');
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="6" class="text-center py-5 text-danger">
                    <i class="bi bi-exclamation-triangle me-2"></i>${error.message}
                </td></tr>`;
            }
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
        const tbody = document.getElementById('caster-table-body');
        if (!tbody) return;

        const searchTerm = (document.getElementById('caster-search-box')?.value || '').toLowerCase();
        const statusFilter = document.getElementById('caster-filter-status')?.value || '';

        tbody.innerHTML = '';

        const filtered = casterUsers.filter(u => {
            // Search
            const matchSearch = !searchTerm ||
                u.username.toLowerCase().includes(searchTerm) ||
                u.mountpoints.some(mp => mp.toLowerCase().includes(searchTerm));

            // Status filter
            let matchStatus = true;
            if (statusFilter === 'blocked') matchStatus = u.is_blocked;
            else if (statusFilter === 'active') matchStatus = !u.is_blocked;
            else if (statusFilter === 'unknown') matchStatus = !u.in_db;
            else if (statusFilter === 'indb') matchStatus = u.in_db;

            return matchSearch && matchStatus;
        });

        const countEl = document.getElementById('caster-displayed-count');
        const totalEl = document.getElementById('caster-total-count');
        if (countEl) countEl.textContent = filtered.length;
        if (totalEl) totalEl.textContent = casterUsers.length;

        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center py-5 text-muted">Không tìm thấy user nào.</td></tr>`;
            return;
        }

        filtered.forEach(u => {
            const tr = document.createElement('tr');

            // Source badge
            const sourceBadge = u.in_db
                ? '<span class="badge badge-indb rounded-pill"><i class="bi bi-database-check me-1"></i>IN DB</span>'
                : '<span class="badge badge-unknown rounded-pill"><i class="bi bi-question-circle me-1"></i>UNKNOWN</span>';

            // Status badge  
            let statusBadge;
            if (u.is_blocked) {
                statusBadge = '<span class="badge badge-blocked rounded-pill"><i class="bi bi-shield-x me-1"></i>BỊ CHẶN</span>';
            } else {
                statusBadge = '<span class="badge badge-active rounded-pill"><i class="bi bi-check-circle me-1"></i>ACTIVE</span>';
            }

            // Mountpoints display
            const mpsDisplay = u.mountpoints.length > 0
                ? u.mountpoints.map(mp => `<code class="text-info small">${mp}</code>`).join(' ')
                : '<span class="text-muted small">N/A</span>';

            // Block reason
            const reasonHtml = u.is_blocked && u.blocked_reason
                ? `<div class="block-reason mt-1"><i class="bi bi-info-circle me-1"></i>${u.blocked_reason}</div>`
                : '';

            // Blocked timestamp
            const blockedTimeHtml = u.is_blocked && u.blocked_at
                ? `<div class="text-muted" style="font-size:0.7rem">${new Date(u.blocked_at * 1000).toLocaleString('vi-VN')}</div>`
                : '';

            // Action buttons
            let actionHtml;
            if (u.is_blocked) {
                actionHtml = `
                    <button class="btn btn-sm btn-outline-success border-0 caster-unblock-btn" 
                        data-username="${u.username}" title="Mở khóa user">
                        <i class="bi bi-unlock-fill me-1"></i>Mở khóa
                    </button>`;
            } else {
                actionHtml = `
                    <button class="btn btn-sm btn-outline-danger border-0 caster-block-btn" 
                        data-username="${u.username}" 
                        data-mountpoints="${u.mountpoints.join(',')}"
                        title="Chặn user">
                        <i class="bi bi-shield-lock-fill me-1"></i>Chặn
                    </button>`;
            }

            // Version check tick
            let versionTick = '';
            if (u.nclient > 0) {
                // If the user has clients, they are "Online"
                // Check if we can match the station version (this is a bit theoretical for rovers, 
                // but we follow user's UI request for a tick next to online)
                versionTick = ` <i class="bi bi-patch-check-fill text-info ms-1" title="Version Synced: ${globalAppVersion}"></i>`;
            }

            tr.innerHTML = `
                <td>
                    <div class="d-flex align-items-center">
                        <div class="${u.is_blocked ? 'bg-danger' : 'bg-success'} bg-opacity-10 ${u.is_blocked ? 'text-danger' : 'text-success'} rounded-circle p-2 me-2">
                            <i class="bi bi-person-fill"></i>
                        </div>
                        <div>
                            <span class="fw-bold text-light">${u.username}</span>
                            ${reasonHtml}
                        </div>
                    </div>
                </td>
                <td>${mpsDisplay}</td>
                <td class="text-center text-muted small">${u.server || 'default'}</td>
                <td class="text-center">${sourceBadge}</td>
                <td class="text-center">
                    ${statusBadge}
                    ${versionTick}
                    ${blockedTimeHtml}
                </td>
                <td class="text-center">
                    <span class="text-white fw-bold">${u.nclient}</span>
                    <span class="text-muted">/ ${u.maxclient}</span>
                </td>
                <td class="text-end">${actionHtml}</td>
            `;
            tbody.appendChild(tr);
        });

        // Bind events
        document.querySelectorAll('.caster-block-btn').forEach(btn => {
            btn.addEventListener('click', handleBlockUser);
        });
        document.querySelectorAll('.caster-unblock-btn').forEach(btn => {
            btn.addEventListener('click', handleUnblockUser);
        });
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

    // === RENDER TABLE (CÓ NÚT EDIT) ===
    function renderTable() {
        const tbody = document.getElementById('rovers-table-body');
        const searchTerm = document.getElementById('search-box').value.toLowerCase();
        const stationFilter = document.getElementById('filter-station').value;
        const statusFilter = document.getElementById('filter-status').value;

        tbody.innerHTML = '';

        const filtered = allRovers.filter(r => {
            const matchSearch = r.username.toLowerCase().includes(searchTerm) || 
                                r.base_serial.toLowerCase().includes(searchTerm) ||
                                (r.base_name && r.base_name.toLowerCase().includes(searchTerm));
            
            const matchStation = !stationFilter || r.base_serial.includes(stationFilter);
            const matchStatus = !statusFilter || String(r.is_active) === statusFilter;
            
            return matchSearch && matchStation && matchStatus;
        });

        document.getElementById('displayed-count').textContent = filtered.length;
        document.getElementById('total-rovers').textContent = allRovers.length;

        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center py-5 text-muted">Không tìm thấy dữ liệu.</td></tr>`;
            return;
        }

        filtered.forEach(r => {
            const tr = document.createElement('tr');
            const createdDate = new Date(r.created_at * 1000).toLocaleDateString('vi-VN');
            const statusBadge = r.is_active 
                ? '<span class="badge badge-active rounded-pill">Active</span>' 
                : '<span class="badge badge-expired rounded-pill">Expired</span>';

            tr.innerHTML = `
                <td>
                    <div class="d-flex align-items-center">
                        <div class="bg-primary bg-opacity-10 text-primary rounded-circle p-2 me-2">
                            <i class="bi bi-person-fill"></i>
                        </div>
                        <span class="fw-bold text-light">${r.username}</span>
                    </div>
                </td>
                <td><code class="text-warning bg-dark px-2 py-1 rounded">${r.password}</code></td>
                <td>
                    <div class="d-flex flex-column">
                        <span class="text-info fw-bold">${r.base_name || r.base_serial}</span>
                        <span class="text-muted small mt-1">
                            <i class="bi bi-broadcast me-1"></i>
                            ${r.mountpoints ? r.mountpoints.split(';').map(m => m.trim()).join(',') : 'Chưa xác định'}
                        </span>
                    </div>
                </td>
                <td class="text-center">${statusBadge}</td>
                <td class="text-secondary small">${createdDate}</td>
                <td class="text-end">
                    <!-- NÚT SỬA -->
                    <button class="btn btn-sm btn-outline-warning edit-btn me-1 border-0" 
                        data-id="${r.id}" title="Sửa thông tin">
                        <i class="bi bi-pencil-square"></i>
                    </button>
                    <!-- NÚT XÓA -->
                    <button class="btn btn-sm btn-outline-danger delete-btn border-0" 
                        data-id="${r.id}" data-user="${r.username}" title="Xóa Rover">
                        <i class="bi bi-trash3-fill"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        // Gán sự kiện
        document.querySelectorAll('.delete-btn').forEach(btn => btn.addEventListener('click', handleDeleteRover));
        
        // Gán sự kiện cho nút EDIT mới
        document.querySelectorAll('.edit-btn').forEach(btn => btn.addEventListener('click', handleEditRover));
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
    async function loadCasterConfig() {
        try {
            const res = await fetch('/api/admin/caster-config', { headers: API_HEADERS });
            if (res.ok) {
                const data = await res.json();
                document.getElementById('caster-host').value = data.host;
                document.getElementById('caster-port').value = data.port;
            }
        } catch (e) { console.error("Caster load error", e); }
    }

    document.getElementById('caster-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            host: document.getElementById('caster-host').value,
            port: parseInt(document.getElementById('caster-port').value)
        };
        try {
            const res = await fetch('/api/admin/caster-config', {
                method: 'PUT',
                headers: API_HEADERS,
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error("Lỗi lưu cấu hình");
            showToast('Lưu cấu hình Caster thành công!', 'success');
        } catch (err) {
            showToast(err.message, 'danger');
        }
    });

    // Rover tab events
    document.getElementById('search-box').addEventListener('input', renderTable);
    document.getElementById('filter-station').addEventListener('change', renderTable);
    document.getElementById('filter-status').addEventListener('change', renderTable);
    
    document.getElementById('refresh-btn').addEventListener('click', async () => {
        const btn = document.getElementById('refresh-btn');
        btn.querySelector('i').classList.add('spin-anim');
        await Promise.all([fetchDevices(), fetchRovers()]);
        btn.querySelector('i').classList.remove('spin-anim');
        showToast('Đã cập nhật dữ liệu', 'info');
    });

    // Caster Rover tab events
    const casterSearchBox = document.getElementById('caster-search-box');
    if (casterSearchBox) casterSearchBox.addEventListener('input', renderCasterTable);

    const casterFilterStatus = document.getElementById('caster-filter-status');
    if (casterFilterStatus) casterFilterStatus.addEventListener('change', renderCasterTable);

    const casterRefreshBtn = document.getElementById('caster-refresh-btn');
    if (casterRefreshBtn) {
        casterRefreshBtn.addEventListener('click', async () => {
            casterRefreshBtn.querySelector('i').classList.add('spin-anim');
            await fetchCasterUsers();
            casterRefreshBtn.querySelector('i').classList.remove('spin-anim');
            showToast('Đã tải lại dữ liệu từ Caster', 'info');
        });
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