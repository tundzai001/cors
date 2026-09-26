// Unified admin navigation for CORS Control Center.
const NavigationManager = {
    currentUser: null,
    currentPage: '',
    publicRoutes: ['/', '/station/', '/network-status', '/coverage', '/blog', '/api/docs'],

    isPublicRoute(pathname = window.location.pathname) {
        return this.publicRoutes.some(route => route === '/' ? pathname === '/' : pathname.startsWith(route));
    },

    async init() {
        try {
            if (!window.ApiClient?.hasToken?.()) {
                if (!this.isPublicRoute()) this.forceLogout('missing_token');
                return;
            }

            await this.loadCurrentUser();
            this.detectCurrentPage();
            this.renderNavigation();
            this.setupTopbar();
            this.setupLogoutButton();
        } catch (error) {
            if (error.message === 'Unauthorized') return;
            console.error('[Nav] init failed:', error);
            window.CorsUI?.showToast?.('danger', 'Cannot initialize admin navigation');
        }
    },

    async loadCurrentUser() {
        const response = await window.ApiClient.apiFetch(window.ApiRoutes.auth.me, {}, 0);
        if (!response.ok) {
            if (response.status === 401 || response.status === 403) {
                this.forceLogout(`auth_${response.status}`);
                throw new Error('Unauthorized');
            }
            throw new Error(`Auth check failed: ${response.status}`);
        }
        this.currentUser = await response.json();
        return this.currentUser;
    },

    detectCurrentPage() {
        const path = window.location.pathname;
        const params = new URLSearchParams(window.location.search);
        if ((path === '/' || path.endsWith('/index.html')) && params.get('view') === 'map') this.currentPage = 'map';
        else if (path === '/' || path.endsWith('/index.html')) this.currentPage = 'dashboard';
        else if (path.includes('operations')) this.currentPage = 'operations';
        else if (path.includes('users')) this.currentPage = 'users';
        else if (path.includes('security')) this.currentPage = 'security';
        else if (path.includes('rovers-global')) this.currentPage = 'rovers';
        else if (path.includes('zalo')) this.currentPage = 'zalo';
        else this.currentPage = 'dashboard';
    },

    renderNavigation() {
        const navContainer = document.getElementById('main-nav') || document.querySelector('.navbar-nav');
        if (!navContainer) return;
        navContainer.innerHTML = '';

        this.addGroup(navContainer, 'Operate');
        this.addNavItem(navContainer, { id: 'dashboard', icon: 'bi-grid-1x2-fill', text: 'Dashboard', href: '/', active: this.currentPage === 'dashboard' });
        this.addNavItem(navContainer, { id: 'map', icon: 'bi-map-fill', text: 'Station Map', href: '/?view=map', active: this.currentPage === 'map' });
        this.addNavItem(navContainer, { id: 'zalo', icon: 'bi-chat-dots-fill', text: 'Zalo Bot', href: '/zalo.html', active: this.currentPage === 'zalo' });

        if (this.currentUser?.role === 'admin') {
            this.addGroup(navContainer, 'Admin');
            this.addNavItem(navContainer, { id: 'operations', icon: 'bi-activity', text: 'Operations', href: '/operations.html', active: this.currentPage === 'operations' });
            this.addNavItem(navContainer, { id: 'security', icon: 'bi-shield-lock-fill', text: 'Security', href: '/security.html', active: this.currentPage === 'security' });
        }

        if (this.currentUser?.role === 'admin' || (this.currentUser?.role === 'coordinator' && this.currentUser?.can_create_users)) {
            if (this.currentUser?.role !== 'admin') {
                this.addGroup(navContainer, 'Admin');
            }
            this.addNavItem(navContainer, { id: 'users', icon: 'bi-people-fill', text: 'Users', href: '/users.html', active: this.currentPage === 'users' });
        }

        if (this.currentUser?.role === 'admin' || this.currentUser?.can_view_rovers || this.currentUser?.permissions?.includes('view:rovers')) {
            this.addGroup(navContainer, 'System');
            this.addNavItem(navContainer, { id: 'rovers', icon: 'bi-hdd-network-fill', text: 'Rover & Caster', href: '/rovers-global.html', active: this.currentPage === 'rovers' });
        }

        window.AppI18n?.apply?.(navContainer);
    },

    addGroup(container, label) {
        const li = document.createElement('li');
        li.className = 'nav-item ops-nav-group-label';
        li.textContent = label;
        container.appendChild(li);
    },

    addNavItem(container, { id, icon, text, href, active }) {
        const li = document.createElement('li');
        li.className = 'nav-item';
        const a = document.createElement('a');
        a.className = `nav-link ${active ? 'active' : ''}`;
        a.id = `nav-${id}`;
        a.href = href;
        a.setAttribute('aria-current', active ? 'page' : 'false');
        a.innerHTML = `<i class="bi ${icon}"></i><span>${text}</span>`;
        if (id === 'dashboard' || id === 'map') {
            a.addEventListener('click', (event) => {
                if (window.location.pathname === '/' && typeof window.switchView === 'function') {
                    event.preventDefault();
                    window.switchView(id === 'map' ? 'map' : 'dashboard');
                    window.history.pushState({}, '', href);
                    this.currentPage = id;
                    this.renderNavigation();
                }
            });
        }
        li.appendChild(a);
        container.appendChild(li);
    },

    setupTopbar() {
        const userInfo = document.getElementById('user-info');
        if (userInfo && this.currentUser) {
            userInfo.textContent = `${this.currentUser.full_name || this.currentUser.username} (${String(this.currentUser.role || '').toUpperCase()})`;
        }

        if (userInfo && !document.getElementById('connection-status-pill')) {
            const pill = document.createElement('span');
            pill.id = 'connection-status-pill';
            pill.className = 'badge text-bg-dark border border-secondary me-2';
            pill.innerHTML = '<span class="ops-connection-dot online me-1"></span>Connected';
            userInfo.parentElement?.insertBefore(pill, userInfo);
        }
    },

    setupLogoutButton() {
        const logoutBtn = document.getElementById('logout-btn');
        if (!logoutBtn) return;
        const freshBtn = logoutBtn.cloneNode(true);
        logoutBtn.parentNode.replaceChild(freshBtn, logoutBtn);
        freshBtn.addEventListener('click', async (event) => {
            event.preventDefault();
            const ok = await window.CorsUI.confirmAction({
                title: 'Logout',
                message: 'End this admin session?',
                confirmText: 'Logout',
                tone: 'danger'
            });
            if (ok) this.forceLogout('logout');
        });
    },

    forceLogout(reason = 'session_expired') {
        if (window.ApiClient?.forceLogout) {
            window.ApiClient.forceLogout(reason);
            return;
        }
        localStorage.removeItem('access_token');
        sessionStorage.removeItem('access_token');
        if (window.location.pathname === '/' || this.isPublicRoute()) return;
        try {
            window.history.replaceState({}, '', '/');
        } catch (_) {}
        if (typeof window.switchView === 'function') {
            window.switchView('homepage');
            return;
        }
        window.location.assign('/');
    }
};

window.NavigationManager = NavigationManager;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => NavigationManager.init(), { once: true });
} else {
    NavigationManager.init();
}
