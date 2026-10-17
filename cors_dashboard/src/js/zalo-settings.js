(() => {
    'use strict';

    const OTP_INTERVAL_MS = 30000;
    const OTP_CIRCUMFERENCE = 2 * Math.PI * 28; // r=28 in SVG

    const state = {
        items: [], stations: [], isAdmin: false, query: '', modal: null, polling: null,
        otpStreaming: false, otpTimer: null, otpAnimFrame: null, otpStartTime: 0,
        otpExpiresAt: 0, otpDurationMs: OTP_INTERVAL_MS,
        currentCode: '', currentUser: null,
        groupOtpExpiresAt: 0, groupOtpTimer: null,
        system: { enabled: false, running: false, available: false, loading: false },
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

    function scopeLabel(item) {
        if (item.binding_type === 'cors_public') return 'Chỉ tình trạng CORS Cục';
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
        document.querySelectorAll('.zalo-stat-column').forEach(column => {
            column.classList.toggle('col-md-3', state.isAdmin);
            column.classList.toggle('col-md-4', !state.isAdmin);
        });
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

        if (badge) {
            badge.className = 'badge';
            if (!available) {
                badge.classList.add('bg-warning', 'text-dark');
                badge.textContent = 'Thiếu token';
            } else if (enabled && running) {
                badge.classList.add('bg-success');
                badge.textContent = 'Đang chạy';
            } else if (enabled) {
                badge.classList.add('bg-info', 'text-dark');
                badge.textContent = 'Đã bật';
            } else {
                badge.classList.add('bg-secondary');
                badge.textContent = 'Đã tắt';
            }
        }

        if (label) {
            label.textContent = enabled ? 'Hệ thống đang bật' : 'Hệ thống đang tắt';
        }

        if (description) {
            if (available) {
                description.textContent = `Trạng thái được lưu trực tiếp vào ${state.system.env_file || '.env'} và áp dụng ngay cho runtime.`;
            } else if (!sdkAvailable) {
                description.textContent = 'Thiếu Zalo SDK trong môi trường hiện tại nên không thể bật bot.';
            } else if (!tokenConfigured) {
                description.textContent = 'Thiếu ZALO_BOT_TOKEN trong .env nên không thể bật bot.';
            } else {
                description.textContent = 'Bot chưa sẵn sàng để bật.';
            }
        }

        if (note) {
            note.textContent = enabled
                ? 'Khi tắt, webhook / polling và các lệnh bot sẽ dừng; khi bật, bot khởi động lại ngay nếu token hợp lệ.'
                : 'Bot đang tắt. Các lệnh Zalo, OTP và webhook sẽ không hoạt động cho đến khi bật lại.';
        }

        if (refreshBtn) refreshBtn.disabled = state.system.loading;
        if (otpToggle) otpToggle.disabled = !enabled;
        if (otpCopy) otpCopy.disabled = !enabled;
        if (groupGenerate) groupGenerate.disabled = !enabled || !state.isAdmin;
        if (groupCopy) groupCopy.disabled = !enabled;

        const otpStatus = document.getElementById('otp-status-text');
        if (otpStatus && !state.otpStreaming) {
            otpStatus.textContent = enabled ? 'Sẵn sàng' : 'Hệ thống đã tắt';
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
            if (state.system.enabled) {
                if (!state.otpStreaming) startOtpStream();
                await loadBindings({ silent: true });
                window.CorsUI?.showToast?.('success', 'Đã bật Zalo Bot và cập nhật .env.');
            } else {
                stopOtpStream();
                window.CorsUI?.showToast?.('success', 'Đã tắt Zalo Bot và cập nhật .env.');
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

    function renderBindings() {
        const body = document.getElementById('binding-list');
        const query = state.query.trim().toLocaleLowerCase('vi');
        const items = state.items.filter(item => !query || [
            item.display_name, item.chat_id, item.dashboard_username, item.binding_type
        ].some(value => String(value || '').toLocaleLowerCase('vi').includes(query)));
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="8"><div class="ops-empty-state"><i class="bi bi-chat"></i><span>Không có liên kết phù hợp</span></div></td></tr>';
            return;
        }
        body.innerHTML = items.map(item => {
            const isGroup = item.chat_type === 'group';
            const fallbackName = isGroup ? `Nhóm Zalo …${String(item.chat_id || '').slice(-6)}` : 'Chat cá nhân';
            const notification = item.notifications || {};
            const isPublic = item.binding_type === 'cors_public';
            const accountPrimary = isPublic || isGroup ? bindingLabel(item) : (item.dashboard_username || 'Tài khoản không khả dụng');
            const accountSecondary = isPublic ? 'Không cần /sign hoặc OTP' : isGroup
                ? (item.binding_type === 'group_admin' ? 'Mọi thành viên có quyền quản trị' : 'Mọi thành viên chỉ được tra cứu')
                : (item.dashboard_role || 'Không xác định');
            const badgeClass = scopeBadgeClass(item);
            const actions = (state.isAdmin ? `<button class="btn btn-sm ${item.is_active ? 'btn-outline-warning' : 'btn-outline-success'} me-1" data-toggle-sending="${item.id}" title="Bật/tắt toàn bộ phản hồi và cảnh báo cho chat này">${item.is_active ? 'Dừng gửi' : 'Cho gửi'}</button>` : '') + `<button class="btn btn-sm btn-outline-info me-1" data-edit="${item.id}" title="Cấu hình"><i class="bi bi-sliders"></i></button><button class="btn btn-sm btn-outline-danger" data-revoke="${item.id}" title="Xóa liên kết"><i class="bi bi-trash3"></i></button>`;
            return `<tr>
                <td data-label="Loại"><span class="badge ${isGroup ? 'bg-warning text-dark' : 'bg-info text-dark'}">${isGroup ? 'Nhóm' : 'Cá nhân'}</span></td>
                <td data-label="Chat"><strong>${escapeHtml(item.display_name || fallbackName)}</strong><div class="small text-muted font-monospace">${escapeHtml(item.chat_id)}</div></td>
                <td data-label="Tài khoản"><div>${escapeHtml(accountPrimary)}</div><div class="small text-muted">${escapeHtml(accountSecondary)}</div></td>
                <td data-label="Phạm vi"><span class="badge ${badgeClass}">${escapeHtml(scopeLabel(item))}</span></td>
                <td data-label="Cảnh báo">${notification.enabled ? `<span class="text-success"><i class="bi bi-bell-fill me-1"></i>${escapeHtml(notification.min_severity || 'critical')}</span>` : '<span class="text-muted"><i class="bi bi-bell-slash me-1"></i>Tắt</span>'}</td>
                <td data-label="Lần cuối">${fmtTime(item.last_seen_at)}</td>
                <td data-label="Trạng thái"><span class="badge ${item.is_active ? 'bg-success' : 'bg-secondary'}">${item.is_active ? 'Đang hoạt động' : 'Đã tắt'}</span></td>
                <td data-label="Thao tác" class="zalo-binding-actions text-end text-nowrap">${actions}</td>
            </tr>`;
        }).join('');
    }

    async function loadBindings({ silent = false } = {}) {
        try {
            const data = await apiJson(window.ApiRoutes.auth.zaloBindings);
            state.items = data.items || [];
            state.stations = data.stations || [];
            state.isAdmin = Boolean(data.is_admin);
            state.system.enabled = Boolean(data.system_enabled);
            applyRoleLayout();
            renderGroupOtpStations();
            renderStats();
            renderBindings();
            syncSystemControls();
        } catch (error) {
            if (!silent) document.getElementById('binding-list').innerHTML = `<tr><td colspan="8" class="text-danger p-3">${escapeHtml(error.message)}</td></tr>`;
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
        void section.offsetWidth; // reflow
        section.classList.add('pulse');
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
        otpCycle();
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
            otpCycle();
        }
    }

    function updateGroupOtpFields() {
        const isPublic = document.getElementById('group-otp-type').value === 'group_public';
        const selected = document.getElementById('group-otp-scope').value === 'selected';
        document.getElementById('group-otp-scope-field').classList.toggle('d-none', !isPublic);
        document.getElementById('group-otp-stations-wrap').classList.toggle('d-none', !isPublic || !selected);
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
        const isPublic = document.getElementById('edit-binding-type').value === 'cors_public';
        const isGroupAdmin = !isPrivate && document.getElementById('edit-binding-type').value === 'group_admin';
        document.getElementById('private-scope-note').classList.toggle('d-none', !isPrivate || isPublic);
        document.getElementById('group-admin-scope-note').classList.toggle('d-none', !isGroupAdmin);
        document.getElementById('group-scope-editor').classList.toggle('d-none', isPrivate || isGroupAdmin || isPublic);
        document.getElementById('edit-notifications').disabled = isPublic;
        document.getElementById('edit-binding-type').disabled = isPrivate;
        document.getElementById('station-checklist').classList.toggle('d-none', document.getElementById('edit-scope-mode').value !== 'selected');
    }

    function openEditor(id) {
        const item = state.items.find(row => row.id === Number(id));
        if (!item) return;
        const notification = item.notifications || {};
        document.getElementById('edit-binding-id').value = item.id;
        document.getElementById('edit-chat-id').textContent = item.chat_id;
        document.getElementById('edit-display-name').value = item.display_name || '';
        document.getElementById('edit-binding-type').value = item.binding_type;
        document.getElementById('edit-active').checked = Boolean(item.is_active);
        document.getElementById('binding-type-field').classList.toggle('d-none', !state.isAdmin);
        document.getElementById('binding-active-field').classList.toggle('d-none', !state.isAdmin);
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

    async function saveBinding() {
        const id = Number(document.getElementById('edit-binding-id').value);
        const item = state.items.find(row => row.id === id);
        if (!item) return;
        const selectedStations = [...document.querySelectorAll('.station-scope-item:checked')].map(input => input.value);
        const payload = {
            display_name: document.getElementById('edit-display-name').value.trim(),
            notifications_enabled: document.getElementById('edit-notifications').checked,
            min_severity: document.getElementById('edit-severity').value,
            quiet_start: document.getElementById('edit-quiet-start').value || null,
            quiet_end: document.getElementById('edit-quiet-end').value || null,
            critical_override: document.getElementById('edit-critical-override').checked
        };
        if (state.isAdmin) {
            payload.binding_type = document.getElementById('edit-binding-type').value;
            payload.is_active = document.getElementById('edit-active').checked;
        }
        if (item.chat_type === 'group') {
            payload.station_scope_mode = payload.binding_type === 'cors_public' ? 'none' : payload.binding_type === 'group_admin'
                ? 'all' : document.getElementById('edit-scope-mode').value;
            payload.allowed_bases = payload.station_scope_mode === 'selected' ? selectedStations : [];
            if (payload.station_scope_mode === 'selected' && !payload.allowed_bases.length) {
                window.CorsUI?.showToast?.('warning', 'Hãy chọn ít nhất một trạm hoặc đổi phạm vi.');
                return;
            }
        }
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
        document.getElementById('edit-scope-mode').addEventListener('change', updateScopeEditor);
        document.getElementById('edit-binding-type').addEventListener('change', updateScopeEditor);
        document.getElementById('save-binding').addEventListener('click', saveBinding);
        document.getElementById('binding-list').addEventListener('click', async event => {
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
            if (revoke) revokeBinding(revoke.dataset.revoke);
        });
        document.getElementById('zalo-system-toggle').addEventListener('change', event => {
            setSystemEnabled(event.target.checked);
        });
        document.getElementById('zalo-system-refresh').addEventListener('click', () => loadSystemState());

        // Tab visibility
        document.addEventListener('visibilitychange', handleVisibility);

        await loadBindings();
        await loadSystemState();
        if (state.system.enabled) {
            startOtpStream();
        } else {
            stopOtpStream();
        }
        state.polling = window.setInterval(() => {
            if (!document.hidden && !document.getElementById('binding-modal').classList.contains('show')) loadBindings({ silent: true });
        }, 5000);

        syncSystemControls();
    });
})();
