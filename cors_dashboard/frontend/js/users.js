// frontend/js/users.js

document.addEventListener('DOMContentLoaded', () => {
    const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
    if (!token) {
        localStorage.removeItem('access_token');
        sessionStorage.removeItem('access_token');
        window.location.href = '/';
        return;
    }

    const API_HEADERS = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };

    const usersTableBody = document.getElementById('users-table-body');
    const userModal = new bootstrap.Modal(document.getElementById('user-modal'));
    const userForm = document.getElementById('user-form');
    const modalTitle = document.getElementById('user-modal-label');
    const addUserBtn = document.getElementById('add-user-btn');
    const roleSelect = document.getElementById('role');
    const deviceAssignmentSection = document.getElementById('device-assignment-section');
    const roverPermissionSection = document.getElementById('rover-permission-section');
    const deviceChecklist = document.getElementById('device-checklist');
    const extraMountpointsSection = document.getElementById('extra-mountpoints-section');
    const extraMpsChecklist = document.getElementById('extra-mps-checklist');
    const searchExtraMpsInput = document.getElementById('search-extra-mps');
    
    let allDevices = []; // LÆ°u danh sÃ¡ch táº¥t cáº£ cÃ¡c tráº¡m
    let allCasterSources = []; // LÆ°u danh sÃ¡ch MP tá»« Caster

    // HÃ m láº¥y danh sÃ¡ch users vÃ  hiá»ƒn thá»‹
    async function fetchAndDisplayUsers() {
        try {
            const response = await fetch('/api/users', { headers: API_HEADERS });
            if (!response.ok) {
                 if (response.status === 401 || response.status === 403) {
                     localStorage.removeItem('access_token');
                     sessionStorage.removeItem('access_token');
                     window.location.href = '/';
                 }
                 throw new Error('KhÃ´ng thá»ƒ láº¥y danh sÃ¡ch ngÆ°á»i dÃ¹ng.');
            }
            const users = await response.json();
            renderUsersTable(users);
        } catch (error) {
            console.error(error);
            usersTableBody.innerHTML = `<tr><td colspan="6" class="text-center text-danger">${error.message}</td></tr>`;
        }
    }
    
    // HÃ m láº¥y danh sÃ¡ch táº¥t cáº£ cÃ¡c tráº¡m Ä‘á»ƒ gÃ¡n
    async function fetchAllDevices() {
        try {
            const response = await fetch('/api/devices', { headers: API_HEADERS });
            if (!response.ok) throw new Error('KhÃ´ng thá»ƒ láº¥y danh sÃ¡ch tráº¡m.');
            allDevices = await response.json();
        } catch (error) {
            console.error(error);
        }
    }

    async function fetchCasterSources() {
        try {
            const response = await fetch('/api/admin/caster-sources', { headers: API_HEADERS });
            if (!response.ok) throw new Error('KhÃ´ng thá»ƒ láº¥y danh sÃ¡ch MountPoint tá»« Caster.');
            const data = await response.json();
            // Láº¥y danh sÃ¡ch MP duy nháº¥t tá»« táº¥t cáº£ cÃ¡c host
            allCasterSources = data.all || [];
            return allCasterSources;
        } catch (error) {
            console.error(error);
            return [];
        }
    }


    // HÃ m render báº£ng users
    function renderUsersTable(users) {
        usersTableBody.innerHTML = '';
        if (users.length === 0) {
            usersTableBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">ChÆ°a cÃ³ ngÆ°á»i dÃ¹ng nÃ o.</td></tr>';
            return;
        }
        users.forEach(user => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${user.id}</td>
                <td>${user.username}</td>
                <td>${user.full_name || ''}</td>
                <td><span class="badge ${getRoleBadgeClass(user.role)}">${user.role}</span></td>
                <td><span class="badge ${user.is_active ? 'bg-success' : 'bg-secondary'}">${user.is_active ? 'Active' : 'Inactive'}</span></td>
                <td>
                    <button class="btn btn-sm btn-outline-primary edit-btn" data-user-id="${user.id}"><i class="bi bi-pencil-fill"></i></button>
                    <button class="btn btn-sm btn-outline-danger delete-btn" data-user-id="${user.id}" data-username="${user.username}"><i class="bi bi-trash-fill"></i></button>
                </td>
            `;
            usersTableBody.appendChild(tr);
        });
    }

    // Má»Ÿ modal Ä‘á»ƒ thÃªm user má»›i
    addUserBtn.addEventListener('click', () => {
        userForm.reset();
        document.getElementById('user-id').value = '';
        modalTitle.textContent = 'ThÃªm User má»›i';
        document.getElementById('password').placeholder = 'Báº¯t buá»™c nháº­p';
        
        // === THÃŠM DÃ’NG NÃ€Y VÃ€O ===
        // Máº·c Ä‘á»‹nh kÃ­ch hoáº¡t tÃ i khoáº£n cho user má»›i
        document.getElementById('is_active').checked = true; 
        // ==========================

        deviceAssignmentSection.classList.add('d-none');
        extraMountpointsSection.classList.add('d-none'); 
        renderDeviceChecklist([], '');
        renderExtraMpsChecklist();
    });
    
    // Má»Ÿ modal Ä‘á»ƒ sá»­a user

    usersTableBody.addEventListener('click', async (e) => {
        // Chá»‰ xá»­ lÃ½ nÃºt edit
        const editButton = e.target.closest('.edit-btn');
        if (!editButton) return;

        const userId = editButton.dataset.userId;
        
        try {
            // Gá»i API Ä‘á»ƒ láº¥y thÃ´ng tin chi tiáº¿t user
            const userResponse = await fetch(`/api/users/${userId}`, { headers: API_HEADERS });
            if (!userResponse.ok) throw new Error('KhÃ´ng thá»ƒ láº¥y thÃ´ng tin user.');
            const user = await userResponse.json();

            // Gá»i API Ä‘á»ƒ láº¥y cÃ¡c tráº¡m cá»§a user nÃ y
            const deviceResponse = await fetch(`/api/devices?user_id=${userId}`, { headers: API_HEADERS });
            if (!deviceResponse.ok) throw new Error('KhÃ´ng thá»ƒ láº¥y danh sÃ¡ch tráº¡m cá»§a user.');
            const userDevices = await deviceResponse.json();

            // === Äiá»n thÃ´ng tin vÃ o form ===
            document.getElementById('user-id').value = user.id;
            document.getElementById('username').value = user.username;
            document.getElementById('full_name').value = user.full_name || '';
            document.getElementById('role').value = user.role;
            document.getElementById('is_active').checked = user.is_active; // Sáº½ sá»­a lá»—i tráº¡ng thÃ¡i active
            document.getElementById('password').value = '';
            document.getElementById('password').placeholder = 'Äá»ƒ trá»‘ng náº¿u khÃ´ng muá»‘n Ä‘á»•i';
            modalTitle.textContent = `Sá»­a User: ${user.username}`;
            document.getElementById('can_create_rovers').checked = user.can_create_rovers;
            
            // Hiá»ƒn thá»‹ cÃ¡c pháº§n cáº¥u hÃ¬nh náº¿u lÃ  coordinator/operator/viewer
            if(user.role === 'coordinator' || user.role === 'operator' || user.role === 'viewer') {
                deviceAssignmentSection.classList.remove('d-none');
                roverPermissionSection.classList.remove('d-none');
                extraMountpointsSection.classList.remove('d-none');
                
                // Láº¥y danh sÃ¡ch serial cá»§a cÃ¡c tráº¡m Ä‘Ã£ gÃ¡n
                const assignedSerials = userDevices.map(d => d.serial);
                renderDeviceChecklist(assignedSerials, user.role);
                
                // Parse danh sÃ¡ch MP bá»• sung
                const extraMps = (user.extra_mountpoints || "").split(/[;,\s\n]+/).map(m => m.trim()).filter(Boolean);
                renderExtraMpsChecklist(extraMps);
            } else {
                deviceAssignmentSection.classList.add('d-none');
                roverPermissionSection.classList.add('d-none');
                extraMountpointsSection.classList.add('d-none');
            }

            userModal.show();

        } catch (error) {
            console.error("Lá»—i khi má»Ÿ form sá»­a:", error);
            alert(error.message);
        }
    });

    // XÃ³a user
    usersTableBody.addEventListener('click', async (e) => {
         if (e.target.closest('.delete-btn')) {
            const button = e.target.closest('.delete-btn');
            const userId = button.dataset.userId;
            const username = button.dataset.username;

            if (confirm(`Báº¡n cÃ³ cháº¯c cháº¯n muá»‘n xÃ³a user '${username}' khÃ´ng?`)) {
                try {
                    const response = await fetch(`/api/users/${userId}`, { method: 'DELETE', headers: API_HEADERS });
                     if (!response.ok) throw new Error('XÃ³a tháº¥t báº¡i.');
                     fetchAndDisplayUsers(); // Táº£i láº¡i báº£ng
                } catch (error) {
                    alert(error.message);
                }
            }
        }
    });

    // Xá»­ lÃ½ khi submit form
    userForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const userId = document.getElementById('user-id').value;
        const passwordInput = document.getElementById('password');
        const password = passwordInput.value;

        // === PHáº¦N THÃŠM Má»šI: KIá»‚M TRA Máº¬T KHáº¨U TRÃŠN TRÃŒNH DUYá»†T ===
        // 1. Náº¿u lÃ  Táº O USER Má»šI (khÃ´ng cÃ³ userId) vÃ  máº­t kháº©u bá»‹ bá» trá»‘ng
        if (!userId && !password) {
            alert('Lá»—i: Máº­t kháº©u lÃ  báº¯t buá»™c khi táº¡o user má»›i.');
            passwordInput.focus(); // ÄÆ°a con trá» vÃ o Ã´ máº­t kháº©u
            return; // Dá»«ng viá»‡c gá»­i form
        }

        // 2. Náº¿u cÃ³ nháº­p máº­t kháº©u nhÆ°ng khÃ´ng Ä‘á»§ 8 kÃ½ tá»±
        if (password && password.length < 8) {
            alert('Lá»—i: Máº­t kháº©u pháº£i cÃ³ Ã­t nháº¥t 8 kÃ½ tá»±.');
            passwordInput.focus();
            return; // Dá»«ng viá»‡c gá»­i form
        }
        // ==========================================================

        let payload = {
            username: document.getElementById('username').value,
            full_name: document.getElementById('full_name').value,
            role: document.getElementById('role').value,
            is_active: document.getElementById('is_active').checked,
            can_create_rovers: false,
            extra_mountpoints: ""
        };
        
        if (password) {
            payload.password = password;
        }
        
        if (payload.role === 'coordinator' || payload.role === 'operator' || payload.role === 'viewer') {
            payload.assigned_devices = Array.from(deviceChecklist.querySelectorAll('input[type="checkbox"]:checked'))
                                            .map(cb => cb.value);
            
            payload.can_create_rovers = document.getElementById('can_create_rovers').checked;
            
            // Thu tháº­p MountPoints bá»• sung
            payload.extra_mountpoints = Array.from(extraMpsChecklist.querySelectorAll('input[type="checkbox"]:checked'))
                                             .map(cb => cb.value)
                                             .join(';');
        }

        const method = userId ? 'PUT' : 'POST';
        const url = userId ? `/api/users/${userId}` : '/api/users';

        try {
            const response = await fetch(url, {
                method: method,
                headers: API_HEADERS,
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                let errorMessage = `Lá»—i ${response.status}: ${response.statusText}`; // ThÃ´ng bÃ¡o máº·c Ä‘á»‹nh
                try {
                    // Thá»­ Ä‘á»c ná»™i dung lá»—i tá»« backend dÆ°á»›i dáº¡ng JSON
                    const errorData = await response.json();
                    if (errorData.detail) {
                        errorMessage = errorData.detail;
                    }
                } catch (jsonError) {
                    console.error("Could not parse error response as JSON.", jsonError);
                }
                // NÃ©m lá»—i vá»›i thÃ´ng Ä‘iá»‡p Ä‘Ã£ Ä‘Æ°á»£c xá»­ lÃ½
                throw new Error(errorMessage);
            }

            userModal.hide();
            fetchAndDisplayUsers();
        } catch(error) {
            // BÃ¢y giá» error.message sáº½ chá»©a thÃ´ng bÃ¡o lá»—i thá»±c sá»± tá»« server
            alert('Lá»—i: ' + error.message);
        }
    });
    // Hiá»ƒn thá»‹/áº©n pháº§n gÃ¡n tráº¡m khi vai trÃ² thay Ä‘á»•i trong modal
    roleSelect.addEventListener('change', (e) => {
        if (e.target.value === 'coordinator' || e.target.value === 'operator' || e.target.value === 'viewer') {
            deviceAssignmentSection.classList.remove('d-none');
            roverPermissionSection.classList.remove('d-none');
            extraMountpointsSection.classList.remove('d-none');
            // Láº¥y danh sÃ¡ch cÃ¡c tráº¡m Ä‘Ã£ Ä‘Æ°á»£c gÃ¡n hiá»‡n táº¡i Ä‘á»ƒ hiá»ƒn thá»‹ Ä‘Ãºng checkbox
            const assignedSerials = Array.from(deviceChecklist.querySelectorAll('input[type="checkbox"]:checked'))
                                         .map(cb => cb.value);
            renderDeviceChecklist(assignedSerials, e.target.value);
            
            const extraMps = Array.from(extraMpsChecklist.querySelectorAll('input[type="checkbox"]:checked'))
                                  .map(cb => cb.value);
            renderExtraMpsChecklist(extraMps);
        } else {    
            deviceAssignmentSection.classList.add('d-none');
            roverPermissionSection.classList.add('d-none');
            extraMountpointsSection.classList.add('d-none');
        }
    });

    
    // Render danh sÃ¡ch checkbox cÃ¡c tráº¡m
    function renderDeviceChecklist(assignedSerials = [], targetRole = '') {
        deviceChecklist.innerHTML = '';
        if (allDevices.length === 0) {
            deviceChecklist.innerHTML = '<p class="text-muted">KhÃ´ng cÃ³ tráº¡m nÃ o Ä‘á»ƒ gÃ¡n.</p>';
            return;
        }
        
        allDevices.forEach(device => {
            // Coordinator/Operator: Chá»‰ Ä‘Æ°á»£c gÃ¡n tráº¡m chÆ°a cÃ³ chá»§ hoáº·c chá»§ chÃ­nh lÃ  mÃ¬nh
            // Viewer: ÄÆ°á»£c phÃ©p gÃ¡n Báº¤T Ká»² tráº¡m nÃ o (vÃ¬ Viewer khÃ´ng chiáº¿m quyá»n sá»Ÿ há»¯u user_id)
            let isAssignedToOther = false;
            if (targetRole === 'coordinator' || targetRole === 'operator') {
                isAssignedToOther = device.user_id && !assignedSerials.includes(device.serial);
            }

            const isChecked = assignedSerials.includes(device.serial);

            const div = document.createElement('div');
            div.className = 'form-check';
            div.innerHTML = `
                <input class="form-check-input" type="checkbox" value="${device.serial}" id="device-${device.serial}" ${isChecked ? 'checked' : ''} ${isAssignedToOther ? 'disabled' : ''}>
                <label class="form-check-label ${isAssignedToOther ? 'text-muted' : ''}" for="device-${device.serial}">
                    ${device.name} (${device.serial}) ${isAssignedToOther ? '- (ÄÃ£ gÃ¡n cho user khÃ¡c)' : ''}
                </label>
            `;
            deviceChecklist.appendChild(div);
        });
    }

    // Render danh sÃ¡ch checkbox cÃ¡c MP tá»« Caster
    function renderExtraMpsChecklist(assignedMps = []) {
        extraMpsChecklist.innerHTML = '<div class="text-center text-muted small py-2">Äang táº£i danh sÃ¡ch...</div>';
        
        // Náº¿u Ä‘Ã£ cÃ³ dá»¯ liá»‡u rá»“i thÃ¬ render luÃ´n, khÃ´ng thÃ¬ fetch
        if (allCasterSources.length > 0) {
            doRender();
        } else {
            fetchCasterSources().then(() => doRender());
        }

        function doRender() {
            extraMpsChecklist.innerHTML = '';
            if (allCasterSources.length === 0) {
                extraMpsChecklist.innerHTML = '<div class="text-center text-muted small py-2">KhÃ´ng tÃ¬m tháº¥y MountPoint nÃ o trÃªn Caster.</div>';
                return;
            }

            // Láº¥y danh sÃ¡ch MP duy nháº¥t vÃ  sáº¯p xáº¿p
            const uniqueMps = Array.from(new Set(allCasterSources.map(s => s.mountpoint))).sort();

            uniqueMps.forEach(mp => {
                const isChecked = assignedMps.includes(mp);
                const div = document.createElement('div');
                div.className = 'form-check extra-mp-item';
                div.dataset.mountpoint = mp.toLowerCase();
                div.innerHTML = `
                    <input class="form-check-input" type="checkbox" value="${mp}" id="extra-mp-${mp}" ${isChecked ? 'checked' : ''}>
                    <label class="form-check-label" for="extra-mp-${mp}">
                        <code class="text-info">${mp}</code>
                    </label>
                `;
                extraMpsChecklist.appendChild(div);
            });
        }
    }

    // Xá»­ lÃ½ tÃ¬m kiáº¿m MP bá»• sung
    searchExtraMpsInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase().trim();
        const items = extraMpsChecklist.querySelectorAll('.extra-mp-item');
        
        items.forEach(item => {
            if (item.dataset.mountpoint.includes(query)) {
                item.classList.remove('d-none');
            } else {
                item.classList.add('d-none');
            }
        });
    });

    function getRoleBadgeClass(role) {
        if (role === 'admin') return 'bg-danger';
        if (role === 'coordinator' || role === 'operator') return 'bg-warning text-dark';
        return 'bg-info';
    }
    
    // HÃ m khá»Ÿi táº¡o
    async function init() {
        await fetchAllDevices();
        await fetchAndDisplayUsers();
    }
    
    init();
});
