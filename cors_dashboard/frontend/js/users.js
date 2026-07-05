document.addEventListener('DOMContentLoaded', async () => {
    const currentUser = await window.ApiClient.requireAuthenticatedPage();
    if (!currentUser) return;

    const usersTableBody = document.getElementById('users-table-body');
    const userModal = new bootstrap.Modal(document.getElementById('user-modal'));
    const userForm = document.getElementById('user-form');
    const modalTitle = document.getElementById('user-modal-label');
    const addUserBtn = document.getElementById('add-user-btn');
    const roleSelect = document.getElementById('role');
    const deviceAssignmentSection = document.getElementById('device-assignment-section');
    const roverAssignmentSection = document.getElementById('rover-assignment-section');
    const roverPermissionSection = document.getElementById('rover-permission-section');
    const userPermissionSection = document.getElementById('user-permission-section');
    const deviceChecklist = document.getElementById('device-checklist');
    const roverChecklist = document.getElementById('rover-checklist');
    const extraMountpointsSection = document.getElementById('extra-mountpoints-section');
    const extraMpsChecklist = document.getElementById('extra-mps-checklist');
    const searchExtraMpsInput = document.getElementById('search-extra-mps');
    const userSearch = document.getElementById('user-search');
    const roleFilter = document.getElementById('role-filter');
    const statusFilter = document.getElementById('status-filter');

    const togglePasswordBtn = document.getElementById('togglePassword');
    const togglePasswordIcon = document.getElementById('togglePasswordIcon');
    const passwordInput = document.getElementById('password');
    const passwordReqs = document.getElementById('passwordReqs');
    const reqLength = document.getElementById('reqLength');
    const reqUpper = document.getElementById('reqUpper');
    const reqLower = document.getElementById('reqLower');
    const reqNumber = document.getElementById('reqNumber');
    const reqSpecial = document.getElementById('reqSpecial');
    const reqForbidden = document.getElementById('reqForbidden');

    let allUsers = [];
    let allDevices = [];
    let allRovers = [];
    let allCasterSources = [];

    async function apiJson(url, options = {}) {
        const response = await window.ApiClient.apiFetch(url, options);
        if (!response.ok) {
            let detail = `Error ${response.status}`;
            try {
                const err = await response.json();
                detail = err.detail || detail;
            } catch (_) {}
            throw new Error(detail);
        }
        if (response.status === 204) return null;
        return response.json();
    }

    function roleBadge(role) {
        const r = String(role || '').toLowerCase();
        if (r === 'admin') return 'ops-role-badge ops-role-admin';
        if (r === 'coordinator') return 'ops-role-badge ops-role-coordinator';
        return 'ops-role-badge ops-role-viewer';
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, ch => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[ch]));
    }

    function getSourceMountpoint(source) {
        if (typeof source === 'string') return source.trim();
        if (!source || typeof source !== 'object') return '';
        return String(source.mountpoint || source.name || source.identifier || source.id || '').trim();
    }

    function getSourceMeta(source) {
        if (!source || typeof source !== 'object') return '';
        const parts = [source.identifier, source.host || source.domain]
            .map(v => String(v || '').trim())
            .filter(Boolean);
        return Array.from(new Set(parts)).join(' · ');
    }

    function getSourceSearchText(source) {
        if (typeof source === 'string') return source.toLowerCase();
        if (!source || typeof source !== 'object') return '';
        return [
            source.mountpoint,
            source.name,
            source.identifier,
            source.host,
            source.domain
        ].map(v => String(v || '').toLowerCase()).join(' ');
    }

    function applyFilters() {
        const term = userSearch.value.trim().toLowerCase();
        const role = roleFilter.value;
        const status = statusFilter.value;
        const filtered = allUsers.filter(user => {
            const matchesTerm = !term || `${user.username || ''} ${user.full_name || ''} ${user.role || ''}`.toLowerCase().includes(term);
            const matchesRole = !role || user.role === role;
            const matchesStatus = !status || (status === 'active' ? user.is_active : !user.is_active);
            return matchesTerm && matchesRole && matchesStatus;
        });
        renderUsersTable(filtered);
    }

    function renderUsersTable(users) {
        if (!users.length) {
            usersTableBody.innerHTML = '<tr><td colspan="6"><div class="ops-empty-state"><i class="bi bi-inbox"></i><span>No users found</span></div></td></tr>';
            return;
        }
        usersTableBody.innerHTML = users.map(user => `
            <tr>
                <td class="ops-table-id">${user.id}</td>
                <td>
                    <div class="ops-user-cell">
                        <span class="ops-user-avatar">${String(user.username || '?').slice(0, 1).toUpperCase()}</span>
                        <strong>${user.username}</strong>
                    </div>
                </td>
                <td class="ops-muted-cell">${user.full_name || '--'}</td>
                <td><span class="${roleBadge(user.role)}">${user.role}</span></td>
                <td><span class="ops-status-pill ${user.is_active ? 'is-active' : 'is-inactive'}"><span></span>${user.is_active ? 'Active' : 'Inactive'}</span></td>
                <td class="text-end ops-row-actions">
                    ${(Number(user.id) === Number(currentUser.id) || user.username === currentUser.username) ? '' : `
                    <button class="ops-table-action edit-btn" data-user-id="${user.id}" title="Edit"><i class="bi bi-pencil-square"></i></button>
                    <button class="ops-table-action danger delete-btn" data-user-id="${user.id}" data-username="${user.username}" title="Delete"><i class="bi bi-trash3"></i></button>
                    `}
                </td>
            </tr>
        `).join('');
    }

    async function fetchUsers() {
        usersTableBody.innerHTML = '<tr><td colspan="6"><div class="ops-empty-state"><span class="spinner-border spinner-border-sm"></span><span>Loading users</span></div></td></tr>';
        
        if (currentUser.role !== 'admin') {
            Array.from(roleFilter.options).forEach(opt => {
                if (opt.value !== '' && opt.value !== 'viewer') opt.style.display = 'none';
            });
        }
        
        allUsers = await apiJson(window.ApiRoutes.users.list);
        applyFilters();
    }

    async function fetchAllDevices() {
        allDevices = await apiJson(window.ApiRoutes.devices.list);
    }

    async function fetchAllRovers() {
        try {
            allRovers = await apiJson('/api/rovers/all');
        } catch (e) {
            allRovers = [];
            console.warn('Cannot fetch rovers:', e);
        }
    }

    async function fetchCasterSources() {
        const data = await apiJson(window.ApiRoutes.admin.casterSources);
        allCasterSources = data.all || [];
        return allCasterSources;
    }

    function shouldShowAccessSections(role) {
        return ['coordinator', 'viewer'].includes(role);
    }

    function updateRoleSections() {
        const show = shouldShowAccessSections(roleSelect.value);
        deviceAssignmentSection.classList.toggle('d-none', !show);
        
        if (roleSelect.value === 'viewer' || roleSelect.value === 'coordinator') {
            roverAssignmentSection.classList.toggle('d-none', false);
        } else {
            roverAssignmentSection.classList.toggle('d-none', true);
        }
        
        // Only Admin can see extra mountpoints and permission sections
        const isAdmin = currentUser.role === 'admin';
        extraMountpointsSection.classList.toggle('d-none', !(show && isAdmin));
        roverPermissionSection.classList.toggle('d-none', !(show && isAdmin));
        if (userPermissionSection) {
            userPermissionSection.classList.toggle('d-none', !(show && isAdmin));
        }
    }

    function renderDeviceChecklist(assignedSerials = []) {
        if (!allDevices.length) {
            deviceChecklist.innerHTML = '<div class="ops-empty-state"><i class="bi bi-inbox"></i><span>No devices</span></div>';
            return;
        }
        const assigned = new Set(assignedSerials.map(String));
        deviceChecklist.innerHTML = allDevices.map(device => `
            <label class="form-check d-flex align-items-center gap-2 mb-2">
                <input class="form-check-input device-checkbox" type="checkbox" value="${device.serial}" ${assigned.has(String(device.serial)) ? 'checked' : ''}>
                <span><strong>${device.name || device.serial}</strong><span class="text-muted ms-2">${device.serial}</span></span>
            </label>
        `).join('');
    }

    function renderRoverChecklist(assignedUsernames = []) {
        if (!allRovers.length) {
            roverChecklist.innerHTML = '<div class="ops-empty-state"><i class="bi bi-inbox"></i><span>No rovers available</span></div>';
            return;
        }
        const assigned = new Set(assignedUsernames.map(String));
        roverChecklist.innerHTML = allRovers.map(rover => `
            <label class="form-check d-flex align-items-center gap-2 mb-2">
                <input class="form-check-input rover-checkbox" type="checkbox" value="${rover.username}" ${assigned.has(String(rover.username)) ? 'checked' : ''}>
                <span><strong>${rover.username}</strong></span>
            </label>
        `).join('');
    }

    function renderExtraMpsChecklist(assignedMps = []) {
        const query = (searchExtraMpsInput.value || '').trim().toLowerCase();
        const assigned = new Set(assignedMps.map(String));
        const list = allCasterSources.filter(source => {
            const mp = getSourceMountpoint(source);
            if (!mp) return false;
            return !query || getSourceSearchText(source).includes(query);
        });
        if (!list.length) {
            extraMpsChecklist.innerHTML = '<div class="ops-empty-state"><i class="bi bi-inbox"></i><span>No mountpoints</span></div>';
            return;
        }
        extraMpsChecklist.innerHTML = list.map(source => {
            const mp = getSourceMountpoint(source);
            const meta = getSourceMeta(source);
            return `
            <label class="form-check d-flex align-items-center gap-2 mb-2">
                <input class="form-check-input extra-mp-checkbox" type="checkbox" value="${escapeHtml(mp)}" ${assigned.has(mp) ? 'checked' : ''}>
                <span><code>${escapeHtml(mp)}</code>${meta ? `<span class="text-muted ms-2">${escapeHtml(meta)}</span>` : ''}</span>
            </label>
        `;
        }).join('');
    }

    function resetForm() {
        userForm.reset();
        document.getElementById('user-id').value = '';
        document.getElementById('username').disabled = false;
        
        const isActiveCheckbox = document.getElementById('is_active');
        isActiveCheckbox.disabled = false;
        isActiveCheckbox.title = "";

        if (currentUser.role !== 'admin') {
            roleSelect.value = 'viewer';
            roleSelect.disabled = true;
            // Optionally, we can also ensure options are hidden
            Array.from(roleSelect.options).forEach(opt => {
                if(opt.value !== 'viewer') opt.style.display = 'none';
            });
        } else {
            roleSelect.disabled = false;
            Array.from(roleSelect.options).forEach(opt => {
                opt.style.display = '';
            });
        }
        isActiveCheckbox.checked = true;
        document.getElementById('password').placeholder = 'Required for new user';
        modalTitle.textContent = 'Add user';
        renderDeviceChecklist([]);
        renderRoverChecklist([]);
        renderExtraMpsChecklist([]);
        updateRoleSections();
    }

    async function openEditUser(userId) {
        const user = await apiJson(window.ApiRoutes.users.detail(userId));
        let userDevices = [];
        if (user.role !== 'viewer') {
            userDevices = await apiJson(window.ApiRoutes.users.devices(userId));
        }

        document.getElementById('user-id').value = user.id;
        
        const usernameInput = document.getElementById('username');
        usernameInput.value = user.username || '';
        usernameInput.disabled = true;

        document.getElementById('full_name').value = user.full_name || '';
        document.getElementById('role').value = user.role || 'viewer';
        if (currentUser.role !== 'admin') {
            roleSelect.disabled = true;
            Array.from(roleSelect.options).forEach(opt => {
                if(opt.value !== 'viewer' && opt.value !== user.role) {
                    opt.style.display = 'none';
                }
            });
        } else {
            roleSelect.disabled = false;
            Array.from(roleSelect.options).forEach(opt => {
                opt.style.display = '';
            });
        }
        
        const isActiveCheckbox = document.getElementById('is_active');
        isActiveCheckbox.checked = !!user.is_active;
        if (Number(user.id) === Number(currentUser.id)) {
            isActiveCheckbox.disabled = true;
            isActiveCheckbox.title = "Không thể vô hiệu hóa tài khoản của chính bạn";
        } else {
            isActiveCheckbox.disabled = false;
            isActiveCheckbox.title = "";
        }

        document.getElementById('can_create_rovers').checked = !!user.can_create_rovers;
        if (document.getElementById('can_create_users')) {
            document.getElementById('can_create_users').checked = !!user.can_create_users;
        }
        document.getElementById('password').value = '';
        document.getElementById('password').placeholder = 'Leave blank to keep current';
        modalTitle.textContent = `Edit user: ${user.username}`;

        const extraMps = String(user.extra_mountpoints || '').split(/[;,\s\n]+/).map(v => v.trim()).filter(Boolean);
        
        let assignedSerials = [];
        if (user.role === 'viewer') {
            if (Array.isArray(user.allowed_bases)) {
                assignedSerials = user.allowed_bases;
            } else if (typeof user.allowed_bases === 'string') {
                try { assignedSerials = JSON.parse(user.allowed_bases); } catch(e) {}
            }
        } else {
            assignedSerials = (userDevices || []).map(d => d.serial);
        }
        
        let assignedRovers = [];
        if (Array.isArray(user.allowed_rovers)) {
            assignedRovers = user.allowed_rovers;
        } else if (typeof user.allowed_rovers === 'string') {
            try { assignedRovers = JSON.parse(user.allowed_rovers); } catch(e) {}
        }

        renderDeviceChecklist(assignedSerials);
        renderRoverChecklist(assignedRovers);
        renderExtraMpsChecklist(extraMps);
        updateRoleSections();
        userModal.show();
    }

    async function deleteUser(userId, username) {
        const ok = await window.CorsUI.confirmAction({
            title: 'Delete user',
            message: `Delete user ${username}?`,
            confirmText: 'Delete',
            tone: 'danger'
        });
        if (!ok) return;
        await apiJson(window.ApiRoutes.users.detail(userId), { method: 'DELETE' });
        window.CorsUI.showToast('success', `Deleted ${username}`);
        await fetchUsers();
    }

    async function saveUser(event) {
        event.preventDefault();
        const userId = document.getElementById('user-id').value;
        const password = document.getElementById('password').value;
        // === KIỂM TRA MẬT KHẨU TRƯỚC KHI GỬI ===
        if (password) {
            const errors = [];
            if (password.length < 8) errors.push('Mật khẩu phải có ít nhất 8 ký tự');
            if (!/[A-Z]/.test(password)) errors.push('Mật khẩu phải chứa ít nhất 1 chữ hoa');
            if (!/[a-z]/.test(password)) errors.push('Mật khẩu phải chứa ít nhất 1 chữ thường');
            if (!/\d/.test(password)) errors.push('Mật khẩu phải chứa ít nhất 1 chữ số');
            if (!/[^a-zA-Z0-9]/.test(password)) errors.push('Mật khẩu phải chứa ít nhất 1 ký tự đặc biệt');
            if (/[ "'`&()|<>]/.test(password)) errors.push('Mật khẩu không được chứa: khoảng trắng, ", \', `, &, (, ), |, <, >');
            if (errors.length) {
                window.CorsUI.showToast('warning', errors[0]);
                return;
            }
        } else if (!userId) {
            window.CorsUI.showToast('warning', 'Password is required for new users');
            return;
        }

        const payload = {
            username: document.getElementById('username').value.trim(),
            full_name: document.getElementById('full_name').value.trim(),
            role: document.getElementById('role').value || 'viewer',
            is_active: document.getElementById('is_active').checked,
            can_create_rovers: document.getElementById('can_create_rovers').checked,
            can_create_users: document.getElementById('can_create_users') ? document.getElementById('can_create_users').checked : false,
            assigned_devices: Array.from(document.querySelectorAll('.device-checkbox:checked')).map(input => input.value),
            allowed_bases: Array.from(document.querySelectorAll('.device-checkbox:checked')).map(input => input.value),
            allowed_rovers: Array.from(document.querySelectorAll('.rover-checkbox:checked')).map(input => input.value),
            extra_mountpoints: Array.from(document.querySelectorAll('.extra-mp-checkbox:checked')).map(input => input.value).join(';')
        };
        if (password) payload.password = password;

        const url = userId ? window.ApiRoutes.users.detail(userId) : window.ApiRoutes.users.list;
        const method = userId ? 'PUT' : 'POST';
        await apiJson(url, { method, body: JSON.stringify(payload) });
        userModal.hide();
        window.CorsUI.showToast('success', 'User saved');
        await fetchUsers();
    }

    togglePasswordBtn.addEventListener('click', () => {
        const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
        passwordInput.setAttribute('type', type);
        togglePasswordIcon.className = type === 'password' ? 'bi bi-eye' : 'bi bi-eye-slash';
    });

    passwordInput.addEventListener('input', () => {
        const val = passwordInput.value;
        if (!val) {
            passwordReqs.style.display = 'none';
            return;
        }
        passwordReqs.style.display = 'block';
        
        const hasLength = val.length >= 8;
        const hasUpper = /[A-Z]/.test(val);
        const hasLower = /[a-z]/.test(val);
        const hasNumber = /\d/.test(val);
        const hasSpecial = /[^a-zA-Z0-9]/.test(val);
        const hasForbidden = /[ "'`&()|<>]/.test(val);

        reqLength.className = hasLength ? 'text-success' : 'text-danger';
        reqLength.innerHTML = `<i class="bi bi-${hasLength ? 'check' : 'x'}-circle me-1"></i>Ít nhất 8 ký tự`;
        
        reqUpper.className = hasUpper ? 'text-success' : 'text-danger';
        reqUpper.innerHTML = `<i class="bi bi-${hasUpper ? 'check' : 'x'}-circle me-1"></i>Ít nhất 1 chữ hoa`;
        
        reqLower.className = hasLower ? 'text-success' : 'text-danger';
        reqLower.innerHTML = `<i class="bi bi-${hasLower ? 'check' : 'x'}-circle me-1"></i>Ít nhất 1 chữ thường`;

        reqNumber.className = hasNumber ? 'text-success' : 'text-danger';
        reqNumber.innerHTML = `<i class="bi bi-${hasNumber ? 'check' : 'x'}-circle me-1"></i>Ít nhất 1 chữ số`;

        reqSpecial.className = hasSpecial ? 'text-success' : 'text-danger';
        reqSpecial.innerHTML = `<i class="bi bi-${hasSpecial ? 'check' : 'x'}-circle me-1"></i>Ít nhất 1 ký tự đặc biệt`;

        reqForbidden.className = !hasForbidden ? 'text-success' : 'text-danger';
        reqForbidden.innerHTML = `<i class="bi bi-${!hasForbidden ? 'check' : 'x'}-circle me-1"></i>Không chứa: khoảng trắng, ", ', \`, &, (, ), |, <, >`;
    });

    addUserBtn.addEventListener('click', resetForm);
    usersTableBody.addEventListener('click', async (event) => {
        const editBtn = event.target.closest('.edit-btn');
        const deleteBtn = event.target.closest('.delete-btn');
        try {
            if (editBtn) await openEditUser(editBtn.dataset.userId);
            if (deleteBtn) await deleteUser(deleteBtn.dataset.userId, deleteBtn.dataset.username);
        } catch (error) {
            window.CorsUI.showToast('danger', error.message);
        }
    });
    userForm.addEventListener('submit', (event) => saveUser(event).catch(error => window.CorsUI.showToast('danger', error.message)));
    roleSelect.addEventListener('change', updateRoleSections);
    searchExtraMpsInput.addEventListener('input', () => renderExtraMpsChecklist(Array.from(document.querySelectorAll('.extra-mp-checkbox:checked')).map(input => input.value)));
    [userSearch, roleFilter, statusFilter].forEach(el => el.addEventListener('input', applyFilters));

    try {
        const initPromises = [fetchAllDevices(), fetchAllRovers()];
        if (currentUser.role === 'admin') {
            initPromises.push(fetchCasterSources());
        }
        await Promise.all(initPromises);
        await fetchUsers();
    } catch (error) {
        usersTableBody.innerHTML = `<tr><td colspan="6"><div class="ops-empty-state text-danger"><i class="bi bi-exclamation-triangle"></i><span>${error.message}</span></div></td></tr>`;
    }
});
