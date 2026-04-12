// ==============================================================================
// nav-manager.js - UNIVERSAL NAVIGATION SYSTEM
// ==============================================================================

const NavigationManager = {
    currentUser: null,
    currentPage: '',

    // Helper: Lấy token từ cả local hoặc session
    getToken() {
        return localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
    },

    async init() {
        try {
            const token = this.getToken();
            if (!token) {
                console.warn('⚠️ No token found, redirecting to login');
                this.forceLogout();
                return;
            }

            await this.loadCurrentUser();
            this.detectCurrentPage();
            this.renderNavigation();
            this.setupLogoutButton();
            this.updatePageTitle();
            this.updateUserInfo();

            console.log('✓ Navigation initialized:', {
                page: this.currentPage,
                user: this.currentUser.username,
                role: this.currentUser.role
            });
        } catch (error) {
            console.error('❌ Navigation init error:', error);
            if (error.message !== 'Unauthorized') {
                alert('Lỗi khởi tạo menu. Vui lòng tải lại trang.');
            }
        }
    },

    async loadCurrentUser() {
        const token = this.getToken();
        const response = await fetch('/api/auth/me', {
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            if (response.status === 401 || response.status === 403) {
                console.error(`🔴 Session ${response.status === 401 ? 'expired' : 'forbidden'}, clearing state`);
                this.forceLogout();
                return;
            }
            throw new Error('Failed to load user info');
        }

        this.currentUser = await response.json();
    },

    detectCurrentPage() {
        const path = window.location.pathname;
        
        if (path === '/' || path === '/index.html') {
            this.currentPage = 'dashboard';
        } else if (path.includes('/users')) {
            this.currentPage = 'users';
        } else if (path.includes('/security')) {
            this.currentPage = 'security';
        } else if (path.includes('/rovers-global')) {
            this.currentPage = 'rovers';
        } else if (path.includes('/monitoring')) {
            this.currentPage = 'monitoring';
        } else {
            this.currentPage = 'dashboard';
        } 
    },

    renderNavigation() {
        const navContainer = document.getElementById('main-nav') || 
                           document.querySelector('.navbar-nav');

        if (!navContainer) {
            console.error('❌ Không tìm thấy #main-nav container!');
            return;
        }

        navContainer.innerHTML = ''; // Xóa menu cũ

        // === 1. DASHBOARD TRẠM (LUÔN HIỂN THỊ) ===
        this.addNavItem(navContainer, {
            id: 'dashboard',
            icon: 'bi-grid-1x2-fill',
            text: 'Dashboard Trạm',
            href: '/',
            active: this.currentPage === 'dashboard'
        });

        // === 2. BẢN ĐỒ TRẠM (LUÔN HIỂN THỊ) ===
        const liMap = document.createElement('li');
        liMap.className = 'nav-item';
        const aMap = document.createElement('a');
        aMap.className = 'nav-link';
        aMap.style.cursor = 'pointer';
        aMap.id = 'nav-map';
        aMap.onclick = (e) => {
            e.preventDefault();
            if (this.currentPage === 'dashboard' && window.switchView) {
                window.switchView('map');
                // Cập nhật lại URL để có thể share link hoặc tải lại trang vẫn đúng
                window.history.pushState({}, '', '/?view=map');
            } else {
                // Đang ở trang khác thì nhét URL thẳng về Dashboard với tham số
                window.location.href = '/?view=map';
            }
        };
        aMap.innerHTML = '<i class="bi bi-map-fill me-1"></i>Bản đồ trạm';
        
        // Active nếu đang ở dashboard và có param 
        if (this.currentPage === 'dashboard' && window.location.search.includes('view=map')) {
            aMap.classList.add('active');
        }
        
        liMap.appendChild(aMap);
        navContainer.appendChild(liMap);

        // === 3. QUẢN LÝ USER (CHỈ ADMIN) ===
        if (this.currentUser?.role === 'admin') {
            this.addNavItem(navContainer, {
                id: 'users',
                icon: 'bi-people-fill',
                text: 'Quản lý User',
                href: '/users.html',
                active: this.currentPage === 'users'
            });
        }

        // === 4. BẢO MẬT (CHỈ ADMIN) ===
        if (this.currentUser?.role === 'admin') {
            this.addNavItem(navContainer, {
                id: 'security',
                icon: 'bi-shield-lock-fill',
                text: 'Bảo mật',
                href: '/security.html',
                active: this.currentPage === 'security'
            });
        }

        // === 5. QUẢN LÝ ROVER & CASTER (ADMIN HOẶC COORDINATOR) ===
        if (this.currentUser?.role === 'admin' || 
            (this.currentUser?.role === 'coordinator' && this.currentUser?.can_create_rovers)) {
            this.addNavItem(navContainer, {
                id: 'rovers',
                icon: 'bi-hdd-network-fill',
                text: 'Quản lý Rover & Caster',
                href: '/rovers-global.html',
                active: this.currentPage === 'rovers'
            });
        }

        // === 6. SYSTEM MONITORING  ===
        /*
        if (this.currentUser?.role === 'admin') {
            this.addNavItem(navContainer, {
                id: 'monitoring',
                icon: 'bi-activity',
                text: 'Giám sát & Logs',
                href: '/monitoring.html',
                active: this.currentPage === 'monitoring'
            });
        }
        */

        console.log(`✓ Menu rendered: ${navContainer.children.length} items`);
    },

    addNavItem(container, { id, icon, text, href, active }) {
        const li = document.createElement('li');
        li.className = 'nav-item';

        const a = document.createElement('a');
        a.className = `nav-link ${active ? 'active' : ''}`;
        if (id) a.id = `nav-${id}`;
        
        // Xử lý click đặc biệt cho Dashboard
        if (id === 'dashboard') {
            a.style.cursor = 'pointer';
            a.onclick = (e) => {
                e.preventDefault();
                if (this.currentPage === 'dashboard' && window.switchView) {
                    window.switchView('dashboard');
                } else {
                    window.location.href = href;
                }
            };
        } else {
            a.href = href;
        }

        a.setAttribute('aria-current', active ? 'page' : '');
        a.innerHTML = `<i class="bi ${icon} me-1"></i>${text}`;

        li.appendChild(a);
        container.appendChild(li);
    },

    setupLogoutButton() {
        const logoutBtn = document.getElementById('logout-btn');
        
        if (!logoutBtn) {
            console.warn('⚠️ Không tìm thấy nút Logout');
            return;
        }

        // Xóa event listener cũ (tránh duplicate)
        const newLogoutBtn = logoutBtn.cloneNode(true);
        logoutBtn.parentNode.replaceChild(newLogoutBtn, logoutBtn);

        newLogoutBtn.addEventListener('click', (e) => {
            e.preventDefault();
            this.handleLogout();
        });

        console.log('✓ Logout button configured');
    },

    forceLogout() {
        localStorage.removeItem('access_token');
        sessionStorage.removeItem('access_token');
        // Xóa các thông tin state khác nếu có
        this.currentUser = null;
        window.location.href = '/login.html';
    },

    handleLogout() {
        if (confirm('Bạn có chắc muốn đăng xuất không?')) {
            this.forceLogout();
        }
    },

    updatePageTitle() {
        const titles = {
            'dashboard': 'Dashboard - CORS Control Center',
            'users': 'Quản lý User - CORS Control Center',
            'security': 'Security Dashboard - CORS Control Center',
            'rovers': 'Quản lý Rover & Caster - CORS Control Center'
        };

        document.title = titles[this.currentPage] || 'CORS Control Center';
    },

    updateUserInfo() {
        const userInfoEl = document.getElementById('user-info');

        if (userInfoEl && this.currentUser) {
            const displayName = this.currentUser.full_name || this.currentUser.username;
            const role = this.currentUser.role.toUpperCase();
            userInfoEl.textContent = `${displayName} (${role})`;
        }

        // Cập nhật avatar (nếu có)
        const avatarEl = document.getElementById('user-avatar-text');
        if (avatarEl && this.currentUser) {
            avatarEl.textContent = this.currentUser.username.charAt(0).toUpperCase();
        }

        console.log('✓ User info updated');
    }
};

// ==============================================================================
// AUTO-INIT
// ==============================================================================

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => NavigationManager.init());
} else {
    NavigationManager.init();
}

window.NavigationManager = NavigationManager;