// frontend/js/users.js

document.addEventListener('DOMContentLoaded', () => {
    const token = localStorage.getItem('access_token');
    if (!token) {
        window.location.href = '/login.html';
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
    const deviceChecklist = document.getElementById('device-checklist');
    
    let allDevices = []; // Lưu danh sách tất cả các trạm

    // Hàm lấy danh sách users và hiển thị
    async function fetchAndDisplayUsers() {
        try {
            const response = await fetch('/api/users', { headers: API_HEADERS });
            if (!response.ok) {
                 if (response.status === 401 || response.status === 403) window.location.href = '/login.html';
                 throw new Error('Không thể lấy danh sách người dùng.');
            }
            const users = await response.json();
            renderUsersTable(users);
        } catch (error) {
            console.error(error);
            usersTableBody.innerHTML = `<tr><td colspan="6" class="text-center text-danger">${error.message}</td></tr>`;
        }
    }
    
    // Hàm lấy danh sách tất cả các trạm để gán
    async function fetchAllDevices() {
        try {
            const response = await fetch('/api/devices', { headers: API_HEADERS });
            if (!response.ok) throw new Error('Không thể lấy danh sách trạm.');
            allDevices = await response.json();
        } catch (error) {
            console.error(error);
        }
    }


    // Hàm render bảng users
    function renderUsersTable(users) {
        usersTableBody.innerHTML = '';
        if (users.length === 0) {
            usersTableBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">Chưa có người dùng nào.</td></tr>';
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

    // Mở modal để thêm user mới
    addUserBtn.addEventListener('click', () => {
        userForm.reset();
        document.getElementById('user-id').value = '';
        modalTitle.textContent = 'Thêm User mới';
        document.getElementById('password').placeholder = 'Bắt buộc nhập';
        
        // === THÊM DÒNG NÀY VÀO ===
        // Mặc định kích hoạt tài khoản cho user mới
        document.getElementById('is_active').checked = true; 
        // ==========================

        deviceAssignmentSection.classList.add('d-none');
        renderDeviceChecklist();
    });
    
    // Mở modal để sửa user

    usersTableBody.addEventListener('click', async (e) => {
        // Chỉ xử lý nút edit
        const editButton = e.target.closest('.edit-btn');
        if (!editButton) return;

        const userId = editButton.dataset.userId;
        
        try {
            // Gọi API để lấy thông tin chi tiết user
            const userResponse = await fetch(`/api/users/${userId}`, { headers: API_HEADERS });
            if (!userResponse.ok) throw new Error('Không thể lấy thông tin user.');
            const user = await userResponse.json();

            // Gọi API để lấy các trạm của user này
            const deviceResponse = await fetch(`/api/devices?user_id=${userId}`, { headers: API_HEADERS });
            if (!deviceResponse.ok) throw new Error('Không thể lấy danh sách trạm của user.');
            const userDevices = await deviceResponse.json();

            // === Điền thông tin vào form ===
            document.getElementById('user-id').value = user.id;
            document.getElementById('username').value = user.username;
            document.getElementById('full_name').value = user.full_name || '';
            document.getElementById('role').value = user.role;
            document.getElementById('is_active').checked = user.is_active; // Sẽ sửa lỗi trạng thái active
            document.getElementById('password').value = '';
            document.getElementById('password').placeholder = 'Để trống nếu không muốn đổi';
            modalTitle.textContent = `Sửa User: ${user.username}`;
            
            // Hiển thị phần gán trạm nếu là coordinator
            if(user.role === 'coordinator') {
                deviceAssignmentSection.classList.remove('d-none');
                // Lấy danh sách serial của các trạm đã gán
                const assignedSerials = userDevices.map(d => d.serial);
                renderDeviceChecklist(assignedSerials);
            } else {
                deviceAssignmentSection.classList.add('d-none');
            }

            userModal.show();

        } catch (error) {
            console.error("Lỗi khi mở form sửa:", error);
            alert(error.message);
        }
    });

    // Xóa user
    usersTableBody.addEventListener('click', async (e) => {
         if (e.target.closest('.delete-btn')) {
            const button = e.target.closest('.delete-btn');
            const userId = button.dataset.userId;
            const username = button.dataset.username;

            if (confirm(`Bạn có chắc chắn muốn xóa user '${username}' không?`)) {
                try {
                    const response = await fetch(`/api/users/${userId}`, { method: 'DELETE', headers: API_HEADERS });
                     if (!response.ok) throw new Error('Xóa thất bại.');
                     fetchAndDisplayUsers(); // Tải lại bảng
                } catch (error) {
                    alert(error.message);
                }
            }
        }
    });

    // Xử lý khi submit form
    userForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const userId = document.getElementById('user-id').value;
        const passwordInput = document.getElementById('password');
        const password = passwordInput.value;

        // === PHẦN THÊM MỚI: KIỂM TRA MẬT KHẨU TRÊN TRÌNH DUYỆT ===
        // 1. Nếu là TẠO USER MỚI (không có userId) và mật khẩu bị bỏ trống
        if (!userId && !password) {
            alert('Lỗi: Mật khẩu là bắt buộc khi tạo user mới.');
            passwordInput.focus(); // Đưa con trỏ vào ô mật khẩu
            return; // Dừng việc gửi form
        }

        // 2. Nếu có nhập mật khẩu nhưng không đủ 6 ký tự
        if (password && password.length < 6) {
            alert('Lỗi: Mật khẩu phải có ít nhất 6 ký tự.');
            passwordInput.focus();
            return; // Dừng việc gửi form
        }
        // ==========================================================

        let payload = {
            username: document.getElementById('username').value,
            full_name: document.getElementById('full_name').value,
            role: document.getElementById('role').value,
            is_active: document.getElementById('is_active').checked,
        };
        
        if (password) {
            payload.password = password;
        }
        
        if (payload.role === 'coordinator') {
            payload.assigned_devices = Array.from(deviceChecklist.querySelectorAll('input[type="checkbox"]:checked'))
                                            .map(cb => cb.value);
        }

        const method = userId ? 'PUT' : 'POST';
        const url = userId ? `/api/users/${userId}` : '/api/users';

        try {
            const response = await fetch(url, {
                method: method,
                headers: API_HEADERS,
                body: JSON.stringify(payload)
            });

            // === PHẦN CẢI TIẾN ĐỂ ĐỌC LỖI TỪ SERVER ===
            if (!response.ok) {
                const errorData = await response.json(); // Lấy nội dung lỗi từ server
                // FastAPI thường trả về lỗi trong trường "detail"
                const errorMessage = errorData.detail || 'Một lỗi không xác định đã xảy ra.';
                throw new Error(errorMessage);
            }
            // ============================================

            userModal.hide();
            fetchAndDisplayUsers();
        } catch(error) {
            // Bây giờ error.message sẽ chứa thông báo lỗi thực sự từ server
            alert('Lỗi: ' + error.message);
        }
    });
    // Hiển thị/ẩn phần gán trạm khi vai trò thay đổi trong modal
    roleSelect.addEventListener('change', (e) => {
        if (e.target.value === 'coordinator') {
            deviceAssignmentSection.classList.remove('d-none');
            // Lấy danh sách các trạm đã được gán hiện tại để hiển thị đúng checkbox
            const assignedSerials = Array.from(deviceChecklist.querySelectorAll('input[type="checkbox"]:checked'))
                                         .map(cb => cb.value);
            renderDeviceChecklist(assignedSerials);
        } else {
            deviceAssignmentSection.classList.add('d-none');
        }
    });

    
    // Render danh sách checkbox các trạm
    function renderDeviceChecklist(assignedSerials = []) {
        deviceChecklist.innerHTML = '';
        if (allDevices.length === 0) {
            deviceChecklist.innerHTML = '<p class="text-muted">Không có trạm nào để gán.</p>';
            return;
        }
        
        allDevices.forEach(device => {
            // Chỉ hiển thị các trạm chưa được gán, hoặc đã được gán cho chính user này
            const isAssignedToOther = device.user_id && !assignedSerials.includes(device.serial);
            const isChecked = assignedSerials.includes(device.serial);

            const div = document.createElement('div');
            div.className = 'form-check';
            div.innerHTML = `
                <input class="form-check-input" type="checkbox" value="${device.serial}" id="device-${device.serial}" ${isChecked ? 'checked' : ''} ${isAssignedToOther ? 'disabled' : ''}>
                <label class="form-check-label ${isAssignedToOther ? 'text-muted' : ''}" for="device-${device.serial}">
                    ${device.name} (${device.serial}) ${isAssignedToOther ? '- (Đã gán cho user khác)' : ''}
                </label>
            `;
            deviceChecklist.appendChild(div);
        });
    }

    function getRoleBadgeClass(role) {
        if (role === 'admin') return 'bg-danger';
        if (role === 'coordinator') return 'bg-warning text-dark';
        return 'bg-info';
    }
    
    // Hàm khởi tạo
    async function init() {
        await fetchAllDevices();
        await fetchAndDisplayUsers();
    }
    
    init();
});