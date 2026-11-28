(() => {
    'use strict';

    const OTP_INTERVAL_MS = 30000;
    const OTP_CIRCUMFERENCE = 2 * Math.PI * 28; // r=28 in SVG

    const state = {
        items: [], stations: [], bots: [], botFilter: '', isAdmin: false, query: '', modal: null, polling: null,
        otpStreaming: false, otpTimer: null, otpAnimFrame: null, otpStartTime: 0,
        otpExpiresAt: 0, otpDurationMs: OTP_INTERVAL_MS,
        currentCode: '', currentUser: null,
        groupOtpExpiresAt: 0, groupOtpTimer: null,
        system: { enabled: false, running: false, available: false, loading: false },
        corsCheck: { loaded: false, loading: false, rtcm_check_enabled: false, error: '' },
    };

    const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    })[ch]);
    const fmtTime = ts => Number(ts) ? new Date(Number(ts) * 1000).toLocaleString('vi-VN') : '--';

    async function apiJson(url, options = {}) {
        const response = await window.ApiClient.apiFetch(url, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        return data;
    }

    function bindingLabel(item) {
        return ({ cors_public: 'Public · CORS Cục', group_admin: 'Group quản trị', group_public: 'Group tra cứu', private: 'Theo tài khoản web' })[item.binding_type] || item.binding_type;
    }

    function botLabel(item) {
        return item.bot_name || (item.bot_key === 'tb' ? 'Bot TB CORS' : 'Bot CORS Manager');
    }

    function renderBotOverview() {
        const target = document.getElementById('zalo-bot-overview');
        if (!target) return;
        target.innerHTML = state.bots.map(bot => {
            const count = state.items.filter(item => (item.bot_key || 'tun') === bot.key).length;
            const status = !bot.token_configured ? 'Chưa cấu hình' : !state.system.enabled ? 'Đã tắt' : bot.running ? 'Đang chạy' : 'Chưa chạy';
            const color = bot.running && state.system.enabled ? 'bg-success' : !bot.token_configured ? 'bg-warning text-dark' : 'bg-secondary';
            return `<div class="col-12 col-md-6"><div class="ops-card zalo-bot-summary p-3 h-100">
                <div class="d-flex flex-wrap justify-content-between align-items-start gap-2"><div>
                    <h2 class="h6 mb-1">${escapeHtml(bot.name)}</h2><div class="small text-muted">Chủ bot: ${escapeHtml(bot.owner)}</div>
                </div><span class="badge ${color}">${status}</span></div>
                <div class="small mt-3">${count} liên kết${state.isAdmin ? '' : ' của bạn'}</div>
            </div></div>`;
        }).join('');
    }

    function scopeLabel(item) {
        if (item.delivery_mode === 'cors_only' || item.binding_type === 'cors_public') return 'Chỉ tình trạng CORS Cục';
        if (item.chat_type === 'private') {
            return (item.dashboard_role === 'admin') ? 'Toàn quyền' : 'Theo tài khoản web';
        }
        if (item.binding_type === 'group_admin') return 'Toàn quyền';
        if (item.station_scope_mode === 'none') return 'Không có trạm';
        if (item.station_scope_mode === 'selected') return `${(item.allowed_bases || []).length} trạm được chọn`;
        return 'Tất cả trạm';
    }

    function scopeBadgeClass(item) {
        const label = scopeLabel(item);
        return label === 'Toàn quyền' ? 'scope-full' : 'scope-partial';
    }

    function renderStats() {
        document.getElementById('stat-total').textContent = state.items.length;
        document.getElementById('stat-active').textContent = state.items.filter(item => item.is_active).length;
        document.getElementById('stat-private').textContent = state.items.filter(item => item.chat_type === 'private').length;
        document.getElementById('stat-groups').textContent = state.items.filter(item => item.chat_type === 'group').length;
    }

    function applyRoleLayout() {
        document.getElementById('group-stat-column').classList.toggle('d-none', !state.isAdmin);
        document.getElementById('group-otp-section').classList.toggle('d-none', !state.isAdmin);
        document.getElementById('zalo-system-section').classList.toggle('d-none', !state.isAdmin);
        document.getElementById('cors-check-section').classList.toggle('d-none', !state.isAdmin);
        document.querySelectorAll('.zalo-stat-column').forEach(column => {
            column.classList.toggle('col-md-3', state.isAdmin);
            column.classList.toggle('col-md-4', !state.isAdmin);
        });
    }

    function syncSystemBadge(badge, enabled, running, available) {
        if (!badge) return;
        badge.className = 'badge';
        let style = 'bg-secondary', text = 'Đã tắt';
        if (!available) {
            style = 'bg-warning text-dark'; text = 'Thiếu token';
        } else if (enabled && running) {
            style = 'bg-success'; text = 'Đang chạy';
        } else if (enabled) {
            style = 'bg-info text-dark'; text = 'Đã bật';
        }
        badge.classList.add(...style.split(' '));
        badge.textContent = text;
    }

    function getSystemDescription(available, sdkAvailable, tokenConfigured) {
        if (available) return 'Điều khiển cả Bot CORS Manager (Tun) và Bot TB CORS (Trường). Mỗi bot có liên kết và quyền riêng.';
        if (!sdkAvailable) return 'Thiếu Zalo SDK trong môi trường hiện tại nên không thể bật bot.';
        if (!tokenConfigured) return 'Chưa cấu hình token cho bot nào nên không thể bật hệ thống.';
        return 'Bot chưa sẵn sàng để bật.';
    }

    function syncSystemControls() {
        const section = document.getElementById('zalo-system-section');
        if (!section) return;

        const toggle = document.getElementById('zalo-system-toggle');
        const badge = document.getElementById('zalo-system-badge');
        const label = section.querySelector('.zalo-system-switch .form-check-label');
        const note = document.getElementById('zalo-system-note');
        const description = document.getElementById('zalo-system-description');
        const refreshBtn = document.getElementById('zalo-system-refresh');
        const otpToggle = document.getElementById('otp-toggle-btn');
        const otpCopy = document.getElementById('copy-link-cmd');
        const groupGenerate = document.getElementById('generate-group-otp');
        const groupCopy = document.getElementById('copy-group-otp');

        const enabled = Boolean(state.system.enabled);
        const running = Boolean(state.system.running);
        const available = Boolean(state.system.available);
        const tokenConfigured = Boolean(state.system.token_configured);
        const sdkAvailable = Boolean(state.system.sdk_available);

        if (toggle) {
            toggle.checked = enabled;
            toggle.disabled = state.system.loading || (!available && !enabled);
        }
        syncSystemBadge(badge, enabled, running, available);
        if (label) label.textContent = enabled ? 'Hệ thống đang bật' : 'Hệ thống đang tắt';
        if (description) description.textContent = getSystemDescription(available, sdkAvailable, tokenConfigured);
        if (note) note.textContent = enabled
            ? 'Khi tắt, webhook / polling và các lệnh bot sẽ dừng; khi bật, bot khởi động lại ngay nếu token hợp lệ.'
            : 'Bot đang tắt. Các lệnh Zalo, OTP và webhook sẽ không hoạt động cho đến khi bật lại.';

        if (refreshBtn) refreshBtn.disabled = state.system.loading;
        if (otpToggle) otpToggle.disabled = !enabled;
        if (otpCopy) otpCopy.disabled = !enabled;
        if (groupGenerate) groupGenerate.disabled = !enabled || !state.isAdmin;
        if (groupCopy) groupCopy.disabled = !enabled;

        const otpStatus = document.getElementById('otp-status-text');
        if (otpStatus && !state.otpStreaming) otpStatus.textContent = enabled ? 'Sẵn sàng' : 'Hệ thống đã tắt';
    }

    function corsCheckBadgeText(control) {
        if (control.loading) return 'Đang lưu / tải…';
        if (!control.loaded) return 'Chưa tải được';
        return control.rtcm_check_enabled ? 'Đang bật' : 'Đã tắt';
    }

    function corsCheckNoteText(control) {
        if (control.loading) return 'Đang cập nhật cài đặt…';
        if (!control.loaded) return 'Chưa tải được cài đặt. Nhấn Làm mới để thử lại.';
        if (control.rtcm_check_enabled) {
            return 'Đang bật: có RTCM hợp lệ mới báo hoạt động. Nhận dữ liệu là đóng kết nối ngay.';
        }
        return 'Đang tắt: chỉ kiểm tra form đăng nhập như cũ, không kết nối NTRIP tự động.';
    }

    function formatCheckInterval(seconds) {
        return seconds % 60 === 0 ? `${seconds / 60} phút` : `${seconds} giây`;
    }

    function syncCorsCheckControls() {
        const control = state.corsCheck;
        const toggle = document.getElementById('cors-check-toggle');
        toggle.checked = control.rtcm_check_enabled;
        toggle.disabled = control.loading || !control.loaded;
        document.getElementById('cors-check-refresh').disabled = control.loading;
        document.getElementById('cors-check-label').textContent = control.rtcm_check_enabled ? 'RTCM đang bật' : 'RTCM đang tắt';
        const badge = document.getElementById('cors-check-badge');
        badge.textContent = corsCheckBadgeText(control);
        badge.className = `badge ${control.loaded && control.rtcm_check_enabled ? 'bg-success' : 'bg-secondary'}`;
        document.getElementById('cors-check-note').textContent = corsCheckNoteText(control);
        if (control.loaded) {
            document.getElementById('cors-check-interval').textContent = `Web: ${formatCheckInterval(control.web_check_interval_seconds)} · RTCM: ${formatCheckInterval(control.rtcm_check_interval_seconds)}`;
        }
        const error = document.getElementById('cors-check-error');
        error.textContent = control.error;
        error.classList.toggle('d-none', !control.error);
    }

    async function updateCorsCheck(enabled) {
        if (!state.isAdmin || state.corsCheck.loading) return;
        state.corsCheck.loading = true;
        state.corsCheck.error = '';
        syncCorsCheckControls();
        try {
            const options = typeof enabled === 'boolean' ? {method: 'PUT', body: JSON.stringify({rtcm_check_enabled: enabled})} : {};
            const data = await apiJson(window.ApiRoutes.admin.referenceNetworkSettings, options);
            Object.assign(state.corsCheck, data, {loaded: true});
        } catch (error) {
            state.corsCheck.error = error.message || 'Chưa cập nhật được cài đặt';
        } finally {
            state.corsCheck.loading = false;
            syncCorsCheckControls();
        }
    }

    async function loadSystemState() {
        if (!state.isAdmin) return;
        state.system.loading = true;
        syncSystemControls();
        try {
            const data = await apiJson(window.ApiRoutes.admin.zaloSystem);
            state.system.enabled = Boolean(data.enabled);
            state.system.running = Boolean(data.running);
            state.system.available = Boolean(data.available);
            state.system.token_configured = Boolean(data.token_configured);
            state.system.sdk_available = Boolean(data.sdk_available);
            state.system.env_file = data.env_file || '.env';
            state.bots = data.bots || [];
            renderBotOverview();
        } catch (error) {
            window.CorsUI?.showToast?.('danger', `Không tải được trạng thái Zalo Bot: ${error.message}`);
        } finally {
            state.system.loading = false;
            syncSystemControls();
        }
    }

    async function setSystemEnabled(enabled) {
        if (!state.isAdmin || state.system.loading) return;
        const nextValue = Boolean(enabled);
        const previous = { ...state.system };
        state.system.loading = true;
        syncSystemControls();
        try {
            const data = await apiJson(window.ApiRoutes.admin.zaloSystem, {
                method: 'PATCH',
                body: JSON.stringify({ enabled: nextValue })
            });
            state.system.enabled = Boolean(data.enabled);
            state.system.running = Boolean(data.running);
            state.system.available = Boolean(data.available);
            state.system.token_configured = Boolean(data.token_configured);
            state.system.sdk_available = Boolean(data.sdk_available);
            state.system.env_file = data.env_file || '.env';
            state.bots = data.bots || [];
            renderBotOverview();
            if (state.system.enabled) {
                if (!state.otpStreaming) startOtpStream();
                await loadBindings({ silent: true });
                window.CorsUI?.showToast?.('success', 'Đã bật hệ thống Zalo.');
            } else {
                stopOtpStream();
                window.CorsUI?.showToast?.('success', 'Đã tắt hệ thống Zalo.');
            }
        } catch (error) {
            state.system = previous;
            window.CorsUI?.showToast?.('danger', error.message);
        } finally {
            state.system.loading = false;
            syncSystemControls();
        }
    }

    function renderGroupOtpStations() {
        const target = document.getElementById('group-otp-station-checklist');
        if (!state.isAdmin || target.children.length) return;
        target.innerHTML = state.stations.length ? state.stations.map(station => `
            <label class="zalo-group-station-item">
                <input class="form-check-input group-otp-station" type="checkbox" value="${escapeHtml(station.serial)}">
                <span><strong>${escapeHtml(station.name)}</strong><small>${escapeHtml(station.serial)}</small></span>
            </label>`).join('') : '<div class="text-muted">Chưa có trạm trong hệ thống.</div>';
    }

    function matchesBindingFilters(item, query) {
        if (state.botFilter && (item.bot_key || 'tun') !== state.botFilter) return false;
        if (!query) return true;
        return [item.display_name, item.chat_id, item.dashboard_username, item.binding_type, botLabel(item), item.bot_owner]
            .some(value => String(value || '').toLocaleLowerCase('vi').includes(query));
    }

    function bindingAccountDetails(item, isPublic, isGroup) {
        if (isPublic) {
            return {
                primary: 'Chỉ CORS Cục',
                secondary: item.binding_type === 'cors_public'
                    ? 'Không cần /sign hoặc OTP'
                    : 'Quyền gốc được giữ, không dùng để gửi tin',
            };
        }
        if (isGroup) {
            return {
                primary: bindingLabel(item),
                secondary: item.binding_type === 'group_admin'
                    ? 'Mọi thành viên có quyền quản trị'
                    : 'Mọi thành viên chỉ được tra cứu',
            };
        }
        return {
            primary: item.dashboard_username || 'Tài khoản không khả dụng',
            secondary: item.dashboard_role || 'Không xác định',
        };
    }

    function bindingAdminActions(item, isPublic) {
        if (!state.isAdmin) return '';
        const corsOnlyButton = isPublic ? ''
            : `<button class="btn btn-sm btn-outline-primary me-1" data-cors-only="${item.id}">Chỉ Cục</button>`;
        const toggleClass = item.is_active ? 'btn-outline-warning' : 'btn-outline-success';
        const toggleLabel = item.is_active ? 'Dừng gửi' : 'Cho gửi';
        return `${corsOnlyButton}<button class="btn btn-sm ${toggleClass} me-1" data-toggle-sending="${item.id}" title="Bật/tắt toàn bộ phản hồi và cảnh báo cho chat này">${toggleLabel}</button>`;
    }

    function bindingActions(item, isPublic) {
        return `${bindingAdminActions(item, isPublic)}<button class="btn btn-sm btn-outline-info me-1" data-edit="${item.id}" title="Cấu hình" aria-label="Cấu hình chat"><i class="bi bi-sliders"></i></button><button class="btn btn-sm btn-outline-danger" data-revoke="${item.id}" title="Xóa liên kết" aria-label="Xóa liên kết"><i class="bi bi-trash3"></i></button>`;
    }

    function bindingNotification(item, isPublic) {
        const notification = item.notifications || {};
        if (isPublic) {
            return `<span class="text-info">${notification.cors_notify_changes ? 'Cục đổi trạng thái' : 'Chỉ khi gọi /cors'}</span>`;
        }
        if (notification.enabled) {
            return `<span class="text-success"><i class="bi bi-bell-fill me-1"></i>${escapeHtml(notification.min_severity || 'critical')}</span>`;
        }
        return '<span class="text-muted"><i class="bi bi-bell-slash me-1"></i>Tắt</span>';
    }

    function renderBindingRow(item) {
        const isGroup = item.chat_type === 'group';
        const isPublic = item.delivery_mode === 'cors_only' || item.binding_type === 'cors_public';
        const fallbackName = isGroup ? `Nhóm Zalo …${String(item.chat_id || '').slice(-6)}` : 'Chat cá nhân';
        const account = bindingAccountDetails(item, isPublic, isGroup);
        const typeClass = isGroup ? 'bg-warning text-dark' : 'bg-info text-dark';
        const typeLabel = isGroup ? 'Nhóm' : 'Cá nhân';
        const statusClass = item.is_active ? 'bg-success' : 'bg-secondary';
        const statusLabel = item.is_active ? 'Đang hoạt động' : 'Đã tắt';
        return `<tr>
            <td data-label="Bot"><div><strong>${escapeHtml(botLabel(item))}</strong><div class="small text-muted">${escapeHtml(item.bot_owner || (item.bot_key === 'tb' ? 'Trường' : 'Tun'))}</div></div></td>
            <td data-label="Loại"><span class="badge ${typeClass}">${typeLabel}</span></td>
            <td data-label="Chat"><strong>${escapeHtml(item.display_name || fallbackName)}</strong><div class="small text-muted font-monospace">${escapeHtml(item.chat_id)}</div></td>
            <td data-label="Tài khoản"><div>${escapeHtml(account.primary)}</div><div class="small text-muted">${escapeHtml(account.secondary)}</div></td>
            <td data-label="Phạm vi"><span class="badge ${scopeBadgeClass(item)}">${escapeHtml(scopeLabel(item))}</span></td>
            <td data-label="Cảnh báo">${bindingNotification(item, isPublic)}</td>
            <td data-label="Lần cuối">${fmtTime(item.last_seen_at)}</td>
            <td data-label="Trạng thái"><span class="badge ${statusClass}">${statusLabel}</span></td>
            <td data-label="Thao tác" class="zalo-binding-actions text-end text-nowrap">${bindingActions(item, isPublic)}</td>
        </tr>`;
    }

    function renderBindings() {
        const body = document.getElementById('binding-list');
        const query = state.query.trim().toLocaleLowerCase('vi');
        const items = state.items.filter(item => matchesBindingFilters(item, query));
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="9"><div class="ops-empty-state"><i class="bi bi-chat"></i><span>Không có liên kết phù hợp</span></div></td></tr>';
            return;
        }
        body.innerHTML = items.map(renderBindingRow).join('');
    }

    async function loadBindings({ silent = false } = {}) {
        try {
            const data = await apiJson(window.ApiRoutes.auth.zaloBindings);
            state.items = data.items || [];
            state.stations = data.stations || [];
            state.isAdmin = Boolean(data.is_admin);
            state.system.enabled = Boolean(data.system_enabled);
            state.bots = data.bots || [];
            renderBotOverview();
            applyRoleLayout();
            renderGroupOtpStations();
            renderStats();
            renderBindings();
            syncSystemControls();
        } catch (error) {
            if (!silent) {
                document.getElementById('binding-list').innerHTML = `<tr><td colspan="9" class="text-danger p-3">${escapeHtml(error.message)}</td></tr>`;
                document.getElementById('zalo-bot-overview').textContent = 'Không tải được trạng thái bot. Bấm làm mới để thử lại.';
            }
        }
    }

    // ═══════════════════════════════════════════════════════════
    // OTP Stream (Google Authenticator style)
    // ═══════════════════════════════════════════════════════════

    async function fetchNewCode() {
        if (!state.system.enabled) {
            window.CorsUI?.showToast?.('warning', 'Hệ thống Zalo đang tắt.');
            return false;
        }
        try {
            const endpoint = state.isAdmin
                ? window.ApiRoutes.auth.zaloLinkCode
                : window.ApiRoutes.auth.zaloMyLinkCode;
            const data = await apiJson(endpoint, { method: 'POST', body: JSON.stringify({}) });
            state.currentCode = data.code || '';
            state.otpDurationMs = Math.max(1000, Number(data.expires_in || 30) * 1000);
            state.otpExpiresAt = Number(data.expires_at || 0) * 1000;
            state.otpStartTime = state.otpExpiresAt - state.otpDurationMs;
            return true;
        } catch (error) {
            console.error('[OTP] Failed to fetch code:', error);
            window.CorsUI?.showToast?.('danger', 'Không thể tạo mã: ' + error.message);
            return false;
        }
    }

    function formatCode(code) {
        if (!code || code.length < 4) return '--- ---';
        const mid = Math.ceil(code.length / 2);
        return code.slice(0, mid) + '  ' + code.slice(mid);
    }

    function updateOtpDisplay() {
        const codeEl = document.getElementById('otp-code-display');
        const linkCmd = document.getElementById('otp-link-cmd');

        // Animate code change
        codeEl.classList.add('refreshing');
        setTimeout(() => {
            codeEl.textContent = formatCode(state.currentCode);
            linkCmd.textContent = `/link ${state.currentCode}`;
            codeEl.classList.remove('refreshing');
        }, 200);

        // Pulse effect
        const section = document.getElementById('otp-section');
        section.classList.remove('pulse');
        requestAnimationFrame(() => requestAnimationFrame(() => section.classList.add('pulse')));
    }

    function animateCountdown() {
        if (!state.otpStreaming) return;

        const remaining = Math.max(0, state.otpExpiresAt - Date.now());
        const fraction = Math.min(1, remaining / state.otpDurationMs);
        const seconds = Math.ceil(remaining / 1000);

        // Update ring
        const ring = document.getElementById('otp-ring');
        const offset = OTP_CIRCUMFERENCE * (1 - fraction);
        ring.setAttribute('stroke-dashoffset', offset.toString());

        // Color transitions
        ring.classList.remove('warning', 'danger');
        if (seconds <= 5) ring.classList.add('danger');
        else if (seconds <= 10) ring.classList.add('warning');

        // Update seconds text
        document.getElementById('otp-seconds').textContent = seconds;

        if (remaining > 0) {
            state.otpAnimFrame = requestAnimationFrame(animateCountdown);
        }
    }

    async function otpCycle() {
        if (!state.otpStreaming) return;

        const ok = await fetchNewCode();
        if (!ok) {
            stopOtpStream();
            return;
        }
        updateOtpDisplay();
        animateCountdown();
        if (state.otpTimer) clearTimeout(state.otpTimer);
        state.otpTimer = setTimeout(otpCycle, Math.max(250, state.otpExpiresAt - Date.now() + 100));
    }

    function startOtpStream() {
        if (!state.system.enabled) return;
        state.otpStreaming = true;
        document.getElementById('otp-idle').classList.add('d-none');
        document.getElementById('otp-active').classList.remove('d-none');
        document.getElementById('otp-section').classList.add('streaming');

        const btn = document.getElementById('otp-toggle-btn');
        btn.innerHTML = '<i class="bi bi-stop-fill me-1"></i><span>Dừng</span>';
        btn.classList.replace('btn-outline-info', 'btn-outline-danger');
        document.getElementById('otp-status-text').textContent = 'Đang chạy';

        syncSystemControls();
        otpCycle().catch(error => console.error('otpCycle failed:', error));
    }

    function stopOtpStream() {
        state.otpStreaming = false;
        if (state.otpTimer) { clearTimeout(state.otpTimer); state.otpTimer = null; }
        if (state.otpAnimFrame) { cancelAnimationFrame(state.otpAnimFrame); state.otpAnimFrame = null; }

        document.getElementById('otp-idle').classList.remove('d-none');
        document.getElementById('otp-active').classList.add('d-none');
        document.getElementById('otp-section').classList.remove('streaming');

        const btn = document.getElementById('otp-toggle-btn');
        btn.innerHTML = '<i class="bi bi-play-fill me-1"></i><span>Bắt đầu</span>';
        btn.classList.replace('btn-outline-danger', 'btn-outline-info');
        document.getElementById('otp-status-text').textContent = state.system.enabled ? 'Sẵn sàng' : 'Hệ thống đã tắt';
        syncSystemControls();
    }

    function toggleOtpStream() {
        if (state.otpStreaming) stopOtpStream();
        else startOtpStream();
    }

    // Pause/resume on tab visibility
    function handleVisibility() {
        if (document.hidden && state.otpStreaming) {
            if (state.otpTimer) { clearTimeout(state.otpTimer); state.otpTimer = null; }
            if (state.otpAnimFrame) { cancelAnimationFrame(state.otpAnimFrame); state.otpAnimFrame = null; }
        } else if (!document.hidden && state.otpStreaming) {
            // Resume immediately with a new code
            otpCycle().catch(error => console.error('otpCycle failed:', error));
        }
    }

    function updateGroupOtpFields() {
        const type = document.getElementById('group-otp-type').value;
        const isCors = type === 'cors_public';
        const isBasePublic = type === 'group_public';
        const selected = document.getElementById('group-otp-scope').value === 'selected';
        document.getElementById('group-public-guide').classList.toggle('d-none', !isCors);
        document.getElementById('group-otp-action').classList.toggle('d-none', isCors);
        document.getElementById('group-otp-scope-field').classList.toggle('d-none', !isBasePublic);
        document.getElementById('group-otp-stations-wrap').classList.toggle('d-none', !isBasePublic || !selected);
        // A code belongs to the policy that generated it; never show it for another choice.
        document.getElementById('group-otp-result').classList.add('d-none');
        document.getElementById('copy-group-otp').disabled = true;
        if (state.groupOtpTimer) clearInterval(state.groupOtpTimer);
        state.groupOtpTimer = null;
    }

    function startGroupOtpCountdown() {
        if (state.groupOtpTimer) clearInterval(state.groupOtpTimer);
        const tick = () => {
            const seconds = Math.max(0, Math.ceil((state.groupOtpExpiresAt - Date.now()) / 1000));
            document.getElementById('group-otp-seconds').textContent = seconds;
            if (seconds <= 0) {
                clearInterval(state.groupOtpTimer);
                state.groupOtpTimer = null;
                document.getElementById('group-otp-command').textContent = 'OTP đã hết hạn';
                document.getElementById('copy-group-otp').disabled = true;
            }
        };
        tick();
        state.groupOtpTimer = setInterval(tick, 250);
    }

    async function generateGroupOtp() {
        const bindingType = document.getElementById('group-otp-type').value;
        if (bindingType === 'cors_public') return;
        const scopeMode = bindingType === 'group_admin' ? 'all' : document.getElementById('group-otp-scope').value;
        const allowedBases = [...document.querySelectorAll('.group-otp-station:checked')].map(item => item.value);
        if (bindingType === 'group_public' && scopeMode === 'selected' && !allowedBases.length) {
            window.CorsUI?.showToast?.('warning', 'Hãy chọn ít nhất một trạm cho group public.');
            return;
        }
        const button = document.getElementById('generate-group-otp');
        button.disabled = true;
        try {
            const data = await apiJson(window.ApiRoutes.auth.zaloGroupLinkCode, {
                method: 'POST',
                body: JSON.stringify({
                    binding_type: bindingType,
                    station_scope_mode: scopeMode,
                    allowed_bases: scopeMode === 'selected' ? allowedBases : []
                })
            });
            const command = `/register ${data.code}`;
            document.getElementById('group-otp-command').textContent = command;
            document.getElementById('group-otp-summary').textContent = bindingType === 'group_admin'
                ? 'Group admin · toàn quyền'
                : `Group public · ${scopeMode === 'all' ? 'tất cả trạm' : scopeMode === 'none' ? 'không có trạm' : `${allowedBases.length} trạm`}`;
            document.getElementById('group-otp-result').classList.remove('d-none');
            document.getElementById('copy-group-otp').disabled = false;
            state.groupOtpExpiresAt = Number(data.expires_at) * 1000;
            startGroupOtpCountdown();
        } catch (error) {
            window.CorsUI?.showToast?.('danger', error.message);
        } finally {
            button.disabled = false;
        }
    }

    // ═══════════════════════════════════════════════════════════
    // Modal Editor
    // ═══════════════════════════════════════════════════════════

    function renderStationChecklist(selected = []) {
        const selectedSet = new Set(selected || []);
        const checklist = document.getElementById('station-checklist');
        if (!state.stations.length) {
            checklist.innerHTML = '<div class="text-muted">Chưa có trạm trong hệ thống.</div>';
            return;
        }
        checklist.innerHTML = state.stations.map(station => `<label class="d-flex align-items-center gap-2 py-2 border-bottom">
            <input class="form-check-input station-scope-item" type="checkbox" value="${escapeHtml(station.serial)}" ${selectedSet.has(station.serial) ? 'checked' : ''}>
            <span class="flex-grow-1"><strong>${escapeHtml(station.name)}</strong><span class="small text-muted ms-2">${escapeHtml(station.serial)}</span></span>
            <span class="ops-connection-dot ${station.status === 'offline' || station.status === 'unknown' ? 'offline' : 'online'}"></span>
        </label>`).join('');
    }

    function updateScopeEditor() {
        const item = state.items.find(row => row.id === Number(document.getElementById('edit-binding-id').value));
        const isPrivate = item?.chat_type === 'private';
        const mode = document.querySelector('input[name="delivery-mode"]:checked')?.value || 'standard';
        const isPublic = mode === 'cors_only';
        document.getElementById('cors-delivery-settings').classList.toggle('d-none', !isPublic);
        document.getElementById('standard-scope-settings').classList.toggle('d-none', mode !== 'standard');
        document.getElementById('standard-alert-settings').classList.toggle('d-none', mode !== 'standard');
        document.getElementById('binding-type-field').classList.toggle('d-none', !state.isAdmin || mode !== 'standard');
        document.getElementById('delivery-preview').textContent = mode === 'paused'
            ? 'Bot sẽ không gửi phản hồi hoặc thông báo cho cuộc trò chuyện này. Cấu hình hiện tại được giữ nguyên.'
            : isPublic ? 'Ví dụ: “CORS Cục đang hoạt động” hoặc “CORS Cục đang gián đoạn”. Không kèm cảnh báo base hay rover.'
            : 'Bot hoạt động theo quyền và phạm vi đã liên kết. Các cảnh báo bên dưới chỉ gửi khi được bật.';
        const isGroupAdmin = !isPrivate && document.getElementById('edit-binding-type').value === 'group_admin';
        document.getElementById('private-scope-note').classList.toggle('d-none', !isPrivate || isPublic);
        document.getElementById('group-admin-scope-note').classList.toggle('d-none', !isGroupAdmin);
        document.getElementById('group-scope-editor').classList.toggle('d-none', isPrivate || isGroupAdmin || isPublic);
        document.getElementById('edit-binding-type').disabled = isPrivate;
        document.getElementById('station-checklist').classList.toggle('d-none', document.getElementById('edit-scope-mode').value !== 'selected');
    }

    function openEditor(id) {
        const item = state.items.find(row => row.id === Number(id));
        if (!item) return;
        const notification = item.notifications || {};
        document.getElementById('edit-binding-id').value = item.id;
        document.getElementById('edit-chat-id').textContent = `${botLabel(item)} · ${item.chat_id}`;
        document.getElementById('edit-display-name').value = item.display_name || '';
        document.getElementById('edit-binding-type').value = item.binding_type;
        document.getElementById('edit-active').checked = Boolean(item.is_active);
        const mode = item.is_active ? (item.delivery_mode || (item.binding_type === 'cors_public' ? 'cors_only' : 'standard')) : 'paused';
        document.querySelector(`input[name="delivery-mode"][value="${mode}"]`).checked = true;
        document.getElementById('delivery-standard').disabled = item.binding_type === 'cors_public';
        document.getElementById('edit-cors-notify').checked = Boolean(notification.cors_notify_changes);
        document.getElementById('delivery-mode-fields').classList.toggle('d-none', !state.isAdmin);
        document.getElementById('binding-type-field').classList.toggle('d-none', !state.isAdmin);
        document.getElementById('binding-active-field').classList.add('d-none');
        document.getElementById('edit-scope-mode').value = item.station_scope_mode || 'all';
        document.getElementById('edit-notifications').checked = Boolean(notification.enabled);
        document.getElementById('edit-severity').value = notification.min_severity || 'critical';
        document.getElementById('edit-quiet-start').value = notification.quiet_start || '';
        document.getElementById('edit-quiet-end').value = notification.quiet_end || '';
        document.getElementById('edit-critical-override').checked = notification.critical_override !== false;
        renderStationChecklist(item.allowed_bases || []);
        updateScopeEditor();
        state.modal.show();
    }

    function buildBindingPayload(item, selectedStations) {
        const payload = {
            display_name: document.getElementById('edit-display-name').value.trim(),
            notifications_enabled: document.getElementById('edit-notifications').checked,
            min_severity: document.getElementById('edit-severity').value,
            quiet_start: document.getElementById('edit-quiet-start').value || null,
            quiet_end: document.getElementById('edit-quiet-end').value || null,
            critical_override: document.getElementById('edit-critical-override').checked
        };
        if (state.isAdmin) {
            const mode = document.querySelector('input[name="delivery-mode"]:checked').value;
            if (mode === 'paused') {
                payload.delivery_mode = item.delivery_mode || (item.binding_type === 'cors_public' ? 'cors_only' : 'standard');
            } else {
                payload.delivery_mode = mode;
            }
            payload.cors_notify_changes = document.getElementById('edit-cors-notify').checked;
            payload.binding_type = document.getElementById('edit-binding-type').value;
            payload.is_active = mode !== 'paused';
        }
        if (item.chat_type === 'group' && payload.delivery_mode === 'standard') {
            if (payload.binding_type === 'cors_public') payload.station_scope_mode = 'none';
            else if (payload.binding_type === 'group_admin') payload.station_scope_mode = 'all';
            else payload.station_scope_mode = document.getElementById('edit-scope-mode').value;
            payload.allowed_bases = payload.station_scope_mode === 'selected' ? selectedStations : [];
            if (payload.station_scope_mode === 'selected' && !payload.allowed_bases.length) {
                window.CorsUI?.showToast?.('warning', 'Hãy chọn ít nhất một trạm hoặc đổi phạm vi.');
                return null;
            }
        }
        return payload;
    }

    async function saveBinding() {
        const id = Number(document.getElementById('edit-binding-id').value);
        const item = state.items.find(row => row.id === id);
        if (!item) return;
        const selectedStations = [...document.querySelectorAll('.station-scope-item:checked')].map(input => input.value);
        const payload = buildBindingPayload(item, selectedStations);
        if (!payload) return;
        const button = document.getElementById('save-binding');
        button.disabled = true;
        try {
            await apiJson(window.ApiRoutes.auth.zaloBinding(id), { method: 'PATCH', body: JSON.stringify(payload) });
            state.modal.hide();
            await loadBindings();
            window.CorsUI?.showToast?.('success', 'Đã cập nhật cấu hình Zalo.');
        } catch (error) {
            window.CorsUI?.showToast?.('danger', error.message);
        } finally {
            button.disabled = false;
        }
    }

    async function revokeBinding(id) {
        if (!window.confirm('Xóa vĩnh viễn liên kết Zalo này cùng cấu hình cảnh báo và phiên đang chờ?')) return;
        try {
            await apiJson(window.ApiRoutes.auth.revokeZaloBinding(id), { method: 'DELETE' });
            await loadBindings();
        } catch (error) {
            window.CorsUI?.showToast?.('danger', error.message);
        }
    }

    // ═══════════════════════════════════════════════════════════
    // Init
    // ═══════════════════════════════════════════════════════════

    document.addEventListener('DOMContentLoaded', async () => {
        const user = await window.ApiClient.requireAuthenticatedPage();
        if (!user) return;
        state.currentUser = user;
        state.isAdmin = user.role === 'admin';
        applyRoleLayout();
        if (!state.isAdmin) {
            document.getElementById('zalo-page-title').innerHTML = '<i class="bi bi-chat-dots-fill me-2"></i>Liên kết Zalo cá nhân';
            document.getElementById('zalo-page-subtitle').textContent = `Liên kết nhiều chat cá nhân với tài khoản ${user.username} (${user.role}); mỗi chat kế thừa đúng quyền và phạm vi trạm trên web.`;
        }

        state.modal = new bootstrap.Modal(document.getElementById('binding-modal'));

        // OTP controls
        document.getElementById('otp-toggle-btn').addEventListener('click', toggleOtpStream);
        document.getElementById('copy-link-cmd').addEventListener('click', async () => {
            await navigator.clipboard.writeText(document.getElementById('otp-link-cmd').textContent);
            window.CorsUI?.showToast?.('success', 'Đã sao chép lệnh liên kết cá nhân');
        });
        document.getElementById('copy-public-cors').addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText('/cors');
                window.CorsUI?.showToast?.('success', 'Đã sao chép /cors. Gửi trong nhóm để hỏi CORS Cục.');
            } catch { window.CorsUI?.showToast?.('warning', 'Không thể sao chép. Gửi /cors trong nhóm.'); }
        });
        document.getElementById('group-otp-type').addEventListener('change', updateGroupOtpFields);
        document.getElementById('group-otp-scope').addEventListener('change', updateGroupOtpFields);
        document.getElementById('generate-group-otp').addEventListener('click', generateGroupOtp);
        document.getElementById('copy-group-otp').addEventListener('click', async () => {
            await navigator.clipboard.writeText(document.getElementById('group-otp-command').textContent);
            window.CorsUI?.showToast?.('success', 'Đã sao chép OTP group');
        });
        updateGroupOtpFields();

        // Binding controls
        document.getElementById('refresh-bindings').addEventListener('click', () => loadBindings());
        document.getElementById('binding-search').addEventListener('input', event => { state.query = event.target.value; renderBindings(); });
        document.getElementById('binding-bot-filter').addEventListener('change', event => { state.botFilter = event.target.value; renderBindings(); });
        document.getElementById('edit-scope-mode').addEventListener('change', updateScopeEditor);
        document.getElementById('edit-binding-type').addEventListener('change', updateScopeEditor);
        document.querySelectorAll('input[name="delivery-mode"]').forEach(input => input.addEventListener('change', () => {
            if (input.value === 'cors_only') document.getElementById('edit-cors-notify').checked = true;
            updateScopeEditor();
        }));
        document.getElementById('save-binding').addEventListener('click', saveBinding);
        document.getElementById('binding-list').addEventListener('click', async event => {
            const corsOnly = event.target.closest('[data-cors-only]');
            if (corsOnly && !corsOnly.disabled) {
                corsOnly.disabled = true;
                corsOnly.textContent = 'Đang lưu…';
                try {
                    await apiJson(window.ApiRoutes.auth.zaloBinding(Number(corsOnly.dataset.corsOnly)), {method:'PATCH', body:JSON.stringify({delivery_mode:'cors_only', cors_notify_changes:true})});
                    await loadBindings({silent:true});
                    window.CorsUI?.showToast?.('success', 'Đã chọn chỉ CORS Cục và bật báo khi đổi trạng thái.');
                } catch (error) { window.CorsUI?.showToast?.('danger', error.message); }
                finally { renderBindings(); }
                return;
            }
            const toggle = event.target.closest('[data-toggle-sending]');
            if (toggle) {
                const item = state.items.find(row => row.id === Number(toggle.dataset.toggleSending));
                if (!item || toggle.disabled) return;
                toggle.disabled = true;
                toggle.textContent = 'Đang lưu…';
                try {
                    await apiJson(window.ApiRoutes.auth.zaloBinding(item.id), {method: 'PATCH', body: JSON.stringify({is_active: !item.is_active})});
                    await loadBindings({silent: true});
                    window.CorsUI?.showToast?.('success', item.is_active ? 'Đã dừng gửi cho chat này.' : 'Đã cho phép gửi lại.');
                } catch (error) {
                    window.CorsUI?.showToast?.('danger', error.message);
                } finally { renderBindings(); }
                return;
            }
            const edit = event.target.closest('[data-edit]');
            const revoke = event.target.closest('[data-revoke]');
            if (edit) openEditor(edit.dataset.edit);
            if (revoke) revokeBinding(revoke.dataset.revoke).catch(error => console.error('revokeBinding failed:', error));
        });
        document.getElementById('zalo-system-toggle').addEventListener('change', event => {
            setSystemEnabled(event.target.checked).catch(error => console.error('setSystemEnabled failed:', error));
        });
        document.getElementById('zalo-system-refresh').addEventListener('click', () => loadSystemState());
        document.getElementById('cors-check-toggle').addEventListener('change', event => updateCorsCheck(event.target.checked));
        document.getElementById('cors-check-refresh').addEventListener('click', () => updateCorsCheck());

        // Tab visibility
        document.addEventListener('visibilitychange', handleVisibility);

        await loadBindings();
        await Promise.all([loadSystemState(), updateCorsCheck()]);
        if (state.system.enabled) {
            startOtpStream();
        } else {
            stopOtpStream();
        }
        state.polling = window.setInterval(() => {
            if (!document.hidden && !document.getElementById('binding-modal').classList.contains('show')) loadBindings({ silent: true }).catch(error => console.error('loadBindings failed:', error));
        }, 5000);

        syncSystemControls();
    });
})();
