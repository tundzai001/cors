// ============================================================
// security.js - v5.0 - FIXED THREAT LEVEL CALCULATION
// ============================================================

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
}

document.addEventListener('DOMContentLoaded', async () => {
    const currentUser = await window.ApiClient.requireAuthenticatedPage();
    if (!currentUser) {
        return;
    }

    const refreshBtn = document.getElementById('refresh-btn');
    const blacklistTableBody = document.getElementById('blacklist-table-body');
    const tempBlockedTableBody = document.getElementById('temp-blocked-table-body');
    const suspiciousIpsContainer = document.getElementById('suspicious-ips-container');
    const blockIpForm = document.getElementById('block-ip-form');
    const ipToBlockInput = document.getElementById('ip-to-block');
    const userInfoSpan = document.getElementById('user-info');
    const logoutBtn = document.getElementById('logout-btn');
    let securityDataInterval = null;
    const POLLING_INTERVAL_MS = 1000;
    let loadingSecurityData = false;
    let cachedSecuritySettings = null;
    let settingsUpdatedAt = 0;

    async function apiFetch(url, options = {}) {
        try {
            const response = await window.ApiClient.apiFetch(url, options);
            if (response.status === 403) {
                window.CorsUI.showToast('danger', 'Admin only');
                window.ApiClient.forceLogout('forbidden');
                throw new Error('Unauthorized');
            }
            if (!response.ok) {
                let detail = `Error ${response.status}`;
                try {
                    const err = await response.json();
                    detail = err.detail || detail;
                } catch (_) {}
                throw new Error(detail);
            }
            if (response.status === 204) return null;
            const contentType = response.headers.get('content-type') || '';
            return contentType.includes('application/json') ? response.json() : response;
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                showError(error.message);
            }
            throw error;
        }
    }
    
    const trafficCanvas = document.getElementById('trafficChart');
    const ctx = trafficCanvas ? trafficCanvas.getContext('2d') : null;
    const vpnTableBody = document.getElementById('vpn-intel-body');
    const attackAlert = document.getElementById('attack-mode-alert');
    const currentRpsEl = document.getElementById('current-rps');

    async function loadSecurityData(isManualReload = false) {
        if (loadingSecurityData) return;
        loadingSecurityData = true;
        if (isManualReload) {
            refreshBtn.disabled = true;
            refreshBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> LOADING...';
        }

        try {
            const [statsData, settingsData, sessionsData] = await Promise.all([
                apiFetch('/api/admin/security/stats'),
                !cachedSecuritySettings || isManualReload || Date.now() - settingsUpdatedAt > 30000
                    ? apiFetch('/api/admin/settings').then(value => { cachedSecuritySettings = value; settingsUpdatedAt = Date.now(); return value; })
                    : Promise.resolve(cachedSecuritySettings),
                apiFetch('/api/admin/security/sessions').catch(err => {
                    console.warn('Could not load sessions:', err);
                    return { active_sessions: [], blocked_devices: [], error: err.message };
                })
            ]);
            
            // === 🆕 KIỂM TRA NẾU API TRẢ VỀ LỖI (status: "error") ===
            if (statsData.status === 'error' || statsData.rate_limiter?.error) {
                throw new Error(statsData.error || 'Security monitoring unavailable');
            }
            window.securityErrorCount = 0;
            document.getElementById('offline-overlay')?.remove();
            document.querySelectorAll('.ops-card').forEach(card => { card.style.opacity = ''; card.style.filter = ''; });
            document.getElementById('security-updated').textContent = `Cập nhật ${new Date(statsData.timestamp * 1000).toLocaleTimeString('vi-VN')}`;
            // === LOGIC CŨ (Khi không có lỗi) ===
            updateDashboardMetrics(statsData.rate_limiter, settingsData);
            renderBlacklistTable(statsData.rate_limiter.blacklisted_ips);
            renderTemporaryBlocksTable(statsData.rate_limiter.temporary_blocked_ips || []);
            renderBlockHistory(statsData.rate_limiter.recent_blocks || []);
            renderSuspiciousIPs(statsData.rate_limiter.suspicious_ips);
            updateSwitchesState(settingsData);
            renderTrafficChart(statsData.rate_limiter.traffic_history);
            renderVpnIntelligence(statsData.rate_limiter.vpn_suspects);
            handleAttackMode(statsData.rate_limiter.attack_mode);

            if (sessionsData) {
                renderActiveSessions(sessionsData.active_sessions || [], sessionsData.error);
                renderBlockedDevices(sessionsData.blocked_devices || [], sessionsData.error);
            }

            if (statsData.system_health) {
                renderSystemHealth(statsData.system_health);
            }
            
            if (statsData.rate_limiter.traffic_history.length > 0) {
                const lastVal = statsData.rate_limiter.traffic_history[statsData.rate_limiter.traffic_history.length - 1];
                currentRpsEl.textContent = lastVal;
            }

            if (isManualReload) {
                showSuccess('Security data reloaded');
            }
            
        } catch (error) {
            console.error('❌ Failed to load security data:', error);
            
            // === 🆕 XỬ LÝ LỖI NETWORK/SERVER ===
            if (error.message === 'Unauthorized') {
                // apiFetch đã xử lý redirect, không cần làm gì
                return;
            }
            
            // Hiển thị thông báo lỗi cho user
            showError(`Connection error: ${error.message || 'Cannot reach server'}`);
            
            // === 🆕 HIỂN thị UI "OFFLINE STATE" ===
            showOfflineState();
            document.getElementById('security-updated').textContent = 'Mất cập nhật · đang giữ dữ liệu lần cuối';
            
            // === 🆕 DỪNG POLLING NẾU LỖI LIÊN TỤC ===
            // (Tránh spam request khi server down)
            if (!window.securityErrorCount) window.securityErrorCount = 0;
            window.securityErrorCount++;
            
            if (window.securityErrorCount >= 3) {
                console.warn('⚠️ Too many errors, stopping auto-refresh');
                stopPolling();
                showError('Auto-refresh disabled. Click Refresh to retry');
            }
            
        } finally {
            loadingSecurityData = false;
            if (isManualReload) {
                refreshBtn.disabled = false;
                refreshBtn.innerHTML = '<i class="bi bi-arrow-clockwise me-2"></i>Refresh';
                
                // Reset error counter khi user manually reload
                window.securityErrorCount = 0;
            }
        }
    }

    function renderSystemHealth(health) {
        const sys = health.system;
        const app = health.application;

        // Helper đổi màu thanh progress (Xanh -> Vàng -> Đỏ)
        const getColor = (val) => val > 90 ? 'bg-danger' : val > 70 ? 'bg-warning' : 'bg-success';
        const setBar = (idBar, idVal, val) => {
            const elBar = document.getElementById(idBar);
            const elVal = document.getElementById(idVal);
            if(elBar && elVal) {
                elBar.style.width = `${val}%`;
                elBar.className = `progress-bar ${getColor(val)}`;
                elVal.textContent = `${val}%`;
                elVal.classList.toggle('ops-text-danger', val > 90);
                elVal.classList.toggle('ops-text-ok', val <= 90);
            }
        };

        // 1. CPU
        setBar('cpu-bar', 'cpu-val', sys.cpu_percent);

        // 2. RAM
        setBar('ram-bar', 'ram-val', sys.memory_percent);
        document.getElementById('ram-free').textContent = sys.memory_available_mb;

        // 3. DISK
        setBar('disk-bar', 'disk-val', sys.disk_percent);
        document.getElementById('disk-free').textContent = sys.disk_free_gb;

        // 4. UPTIME & LATENCY
        document.getElementById('uptime-val').textContent = health.uptime_human;
        document.getElementById('latency-val').textContent = `${app.avg_response_time_ms}ms`;
    }

    function renderTrafficChart(data) {
        if (!ctx) return;

        // Resize canvas fix pixelation
        const container = trafficCanvas.parentElement;
        trafficCanvas.width = container.clientWidth;
        trafficCanvas.height = 250; // Fixed height

        const width = trafficCanvas.width;
        const height = trafficCanvas.height;
        const padding = 20;
        
        // Clear canvas
        ctx.clearRect(0, 0, width, height);

        // Config
        const maxVal = Math.max(...data, 10); // Min scale 10
        const stepX = (width - padding * 2) / (data.length - 1);
        
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.16)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = 0; i < 5; i++) {
            const y = padding + (height - 2 * padding) * (i / 4);
            ctx.moveTo(padding, y);
            ctx.lineTo(width - padding, y);
        }
        ctx.stroke();

        // Draw Area Path (Gradient Fill)
        ctx.beginPath();
        ctx.moveTo(padding, height - padding);
        
        data.forEach((val, index) => {
            const x = padding + index * stepX;
            const y = height - padding - (val / maxVal) * (height - 2 * padding);
            ctx.lineTo(x, y);
        });
        
        ctx.lineTo(width - padding, height - padding);
        ctx.closePath();
        
        const gradient = ctx.createLinearGradient(0, 0, 0, height);
        gradient.addColorStop(0, 'rgba(14, 165, 233, 0.34)');
        gradient.addColorStop(1, 'rgba(14, 165, 233, 0.0)');
        ctx.fillStyle = gradient;
        ctx.fill();

        // Draw Line Path
        ctx.beginPath();
        ctx.strokeStyle = '#0ea5e9';
        ctx.lineWidth = 2;
        ctx.shadowBlur = 0;
        
        data.forEach((val, index) => {
            const x = padding + index * stepX;
            const y = height - padding - (val / maxVal) * (height - 2 * padding);
            if (index === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.shadowBlur = 0; // Reset shadow
    }

    function renderVpnIntelligence(suspects) {
        vpnTableBody.innerHTML = '';
        if (!suspects || suspects.length === 0) {
            vpnTableBody.innerHTML = `<tr><td colspan="3"><div class="ops-empty-state"><i class="bi bi-shield-check"></i><span>No anomalies</span></div></td></tr>`;
            return;
        }

        suspects.forEach(item => {
            let badgeClass = 'bg-secondary';
            if (item.risk_level === 'CRITICAL') badgeClass = 'bg-danger text-white pulse-glow-danger';
            else if (item.risk_level === 'HIGH') badgeClass = 'bg-warning text-dark';

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>
                    <code>${escapeHtml(String(item.fingerprint || '').substring(0, 8))}...</code>
                    <div class="ops-card-meta text-truncate" style="max-width: 150px;">${escapeHtml(item.user_agent)}</div>
                </td>
                <td class="text-center fw-bold">${item.ip_count}</td>
                <td class="text-end"><span class="badge ${badgeClass}">${item.risk_level}</span></td>
            `;
            vpnTableBody.appendChild(tr);
        });
    }

    function handleAttackMode(isAttackMode) {
        if (isAttackMode) {
            attackAlert.classList.remove('d-none');
        } else {
            attackAlert.classList.add('d-none');
        }
    }

    function updateDashboardMetrics(stats, settings) {
        // --- CẬP NHẬT CÁC METRICS CƠ BẢN ---
        document.getElementById('blocked-ips-count').textContent = stats.blocked_ips_count;
        document.getElementById('permanent-blacklist-count').textContent = stats.permanent_blacklist_count;
        document.getElementById('total-ips-tracked').textContent = stats.total_ips_tracked;
        
        const totalThreats = stats.blocked_ips_count + stats.permanent_blacklist_count;
        document.getElementById('threats-count').textContent = totalThreats;
        
        updateCircularProgress('circle-blocked-ips', stats.blocked_ips_count, 20);
        updateCircularProgress('circle-permanent', stats.permanent_blacklist_count, 10);
        updateCircularProgress('circle-tracked', stats.total_ips_tracked, 100);
        
        // --- 🔥 LOGIC TÍNH THREAT LEVEL MỚI (FIXED) ---
        let threatLevel = 1;
        let statusText = 'ALL SYSTEMS NOMINAL';
        let statusClass = 'bg-success';
        
        // BƯỚC 1: Kiểm tra trạng thái các module bảo vệ
        const firewallEnabled = settings.firewall_enabled;
        const vpnEnabled = settings.vpn_detection_enabled;
        const patternEnabled = settings.pattern_analysis_enabled;
        
        const activeModules = [firewallEnabled, vpnEnabled, patternEnabled].filter(Boolean).length;
        
        // BƯỚC 2: Tính threat level dựa trên số IP bị khóa
        if (totalThreats > 10) threatLevel = 5;
        else if (totalThreats > 5) threatLevel = 4;
        else if (totalThreats > 2) threatLevel = 3;
        else if (totalThreats > 0) threatLevel = 2;
        
        // BƯỚC 3: 🔥 LOGIC MỚI - XỬ LÝ MODULE BỊ TẮT
        if (activeModules === 0) {
            // ⚠️ TẤT CẢ MODULE TẮT = HỆ THỐNG HOÀN TOÀN VÔ PHÒNG THỦ
            threatLevel = 5;
            statusText = 'Critical: all protection disabled';
            statusClass = 'bg-danger pulse-glow-danger';
        } else if (activeModules === 1) {
            // ⚠️ CHỈ 1 MODULE HOẠT ĐỘNG = NGUY HIỂM
            if (threatLevel < 4) threatLevel = 4;
            statusText = 'High risk: minimal protection';
            statusClass = 'bg-danger';
        } else if (activeModules === 2) {
            // ⚠️ 2/3 MODULE = CẢNH BÁO
            if (threatLevel < 3) threatLevel = 3;
            statusText = 'Warning: degraded protection';
            statusClass = 'bg-warning text-dark';
        }
        // Nếu cả 3 module đều bật, giữ nguyên threatLevel từ số IP bị khóa
        
        // BƯỚC 4: Override status text nếu có attack mode
        if (stats.attack_mode) {
            threatLevel = 5;
            statusText = 'DDoS attack in progress';
            statusClass = 'bg-danger pulse-glow-danger';
        }
        
        // BƯỚC 5: Áp dụng UI
        updateThreatLevel(threatLevel);
        
        const statusBadge = document.getElementById('system-status-badge');
        if (statusBadge) {
            // Override nếu chưa tính toán ở trên
            if (!statusText.includes('CRITICAL') && !statusText.includes('HIGH RISK') && !stats.attack_mode) {
                const levels = {
                    1: { text: 'Nominal', class: 'bg-success' },
                    2: { text: 'Low threat', class: 'bg-success' },
                    3: { text: 'Moderate threat', class: 'bg-warning text-dark' },
                    4: { text: 'High threat', class: 'bg-danger' },
                    5: { text: 'Critical threat', class: 'bg-danger pulse-glow-danger' }
                };
                const level = levels[threatLevel];
                statusText = level.text;
                statusClass = level.class;
            }
            
            statusBadge.textContent = statusText;
            statusBadge.className = `badge ${statusClass}`;
            statusBadge.style.boxShadow = '';
        }
    }

    function renderBlacklistTable(blacklistedIps) {
        blacklistTableBody.innerHTML = '';
        if (blacklistedIps.length === 0) {
            blacklistTableBody.innerHTML = `<tr><td colspan="3"><div class="ops-empty-state"><i class="bi bi-shield-check"></i><span>No blacklisted IPs</span></div></td></tr>`;
            return;
        }
        blacklistedIps.forEach(block => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><code>${escapeHtml(block.ip)}</code></td>
                <td>${escapeHtml(block.reason || 'N/A')}</td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-primary unblock-btn" data-ip="${escapeHtml(block.ip)}">
                        <i class="bi bi-unlock-fill me-1"></i>Unblock
                    </button>
                </td>
            `;
            blacklistTableBody.appendChild(tr);
        });
    }
    
    function renderTemporaryBlocksTable(tempBlocks) {
        tempBlockedTableBody.innerHTML = '';
        if (tempBlocks.length === 0) {
            tempBlockedTableBody.innerHTML = `<tr><td colspan="4"><div class="ops-empty-state"><i class="bi bi-shield-check"></i><span>No temporary blocks</span></div></td></tr>`;
            return;
        }
        tempBlocks.forEach(block => {
            const seconds = Math.max(0, Math.ceil(block.remaining_seconds));
            const remaining = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><code>${escapeHtml(block.ip)}</code></td>
                <td class="ops-text-warn">${remaining}</td>
                <td>${escapeHtml(block.reason || 'N/A')}</td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-primary unblock-btn" data-ip="${escapeHtml(block.ip)}">
                        <i class="bi bi-unlock-fill me-1"></i>Unblock
                    </button>
                </td>
            `;
            tempBlockedTableBody.appendChild(tr);
        });
    }
    
    function renderBlockHistory(events) {
        const container = document.getElementById('block-history-body');
        if (!events.length) {
            container.innerHTML = '<tr><td colspan="5"><div class="ops-empty-state">Chưa ghi nhận lượt chặn gần đây</div></td></tr>';
            return;
        }
        container.innerHTML = events.map(event => {
            const sources = {http_rate_limit:'Giới hạn HTTP', security_gate:'Security Gate', login:'Đăng nhập / quản trị'};
            return `<tr><td>${escapeHtml(new Date(event.last_seen * 1000).toLocaleTimeString('vi-VN'))}</td><td><code>${escapeHtml(event.ip)}</code></td><td>${escapeHtml(sources[event.source] || event.source)}</td><td>${escapeHtml(event.reason)}</td><td class="text-end">${escapeHtml(event.count)}</td></tr>`;
        }).join('');
    }

    function formatRelativeTime(timestamp) {
        if (!timestamp) return '—';
        const diff = Math.max(0, Math.floor(Date.now() / 1000 - timestamp));
        if (diff < 15) return '<span class="text-success fw-bold"><i class="bi bi-circle-fill me-1" style="font-size:0.5rem;"></i>Vừa xong</span>';
        if (diff < 60) return `<span class="text-success">${diff}s trước</span>`;
        if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`;
        if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`;
        return `${Math.floor(diff / 86400)} ngày trước`;
    }

    function renderActiveSessions(sessions, error = null) {
        const tbody = document.getElementById('active-sessions-table-body');
        const badge = document.getElementById('active-sessions-badge');
        if (badge) badge.textContent = error ? 'Chưa xác định' : `${sessions.length} phiên đang hoạt động`;
        if (!tbody) return;

        if (error && !sessions.length) {
            tbody.innerHTML = `<tr><td colspan="7"><div class="ops-empty-state text-warning"><i class="bi bi-exclamation-triangle me-2"></i><span>Không thể tải danh sách phiên (${escapeHtml(error)})</span></div></td></tr>`;
            return;
        }

        if (!sessions.length) {
            tbody.innerHTML = '<tr><td colspan="7"><div class="ops-empty-state"><i class="bi bi-shield-check"></i><span>Không có phiên đăng nhập nào đang hoạt động</span></div></td></tr>';
            return;
        }

        const roleBadges = {
            admin: 'bg-danger',
            coordinator: 'bg-primary',
            viewer: 'bg-secondary'
        };

        tbody.innerHTML = sessions.map(s => {
            const roleBadge = roleBadges[s.role] || 'bg-secondary';
            const currentBadge = s.is_current 
                ? '<span class="badge bg-success ms-1"><i class="bi bi-person-check-fill me-1"></i>Phiên này (Bạn)</span>' 
                : '';

            const localIpDisplay = s.local_ip
                ? `<div class="d-flex align-items-center">
                    <span class="badge bg-dark-subtle border border-primary-subtle text-primary fw-medium">
                        <i class="bi bi-pc-display me-1"></i>${escapeHtml(s.local_ip)}
                    </span>
                   </div>`
                : `<span class="text-muted small"><i class="bi bi-hdd-network me-1"></i>Chung IP mạng</span>`;

            const deviceName = escapeHtml(s.device_name || 'Không xác định');
            const deviceIdShort = escapeHtml((s.device_id || '').slice(0, 10));

            const actionButtons = s.is_current
                ? `<span class="badge bg-dark-subtle text-muted border px-2 py-1"><i class="bi bi-shield-check me-1"></i>Đang sử dụng</span>`
                : `<div class="btn-group btn-group-sm">
                    <button class="btn btn-sm btn-outline-warning revoke-session-btn" 
                            data-session-id="${escapeHtml(s.session_id)}" 
                            data-username="${escapeHtml(s.username)}"
                            data-local-ip="${escapeHtml(s.local_ip || '')}"
                            data-device="${escapeHtml(s.device_name || '')}"
                            title="Đăng xuất riêng máy này">
                        <i class="bi bi-box-arrow-right me-1"></i>Đăng xuất
                    </button>
                    <button class="btn btn-sm btn-outline-danger block-device-btn" 
                            data-device-id="${escapeHtml(s.device_id)}" 
                            data-username="${escapeHtml(s.username)}"
                            data-device-name="${escapeHtml(s.device_name || '')}"
                            data-local-ip="${escapeHtml(s.local_ip || '')}"
                            data-ip="${escapeHtml(s.ip_address)}"
                            title="Chặn riêng máy tính này, không ảnh hưởng toàn mạng">
                        <i class="bi bi-ban me-1"></i>Chặn máy
                    </button>
                   </div>`;

            return `<tr>
                <td>
                    <div class="fw-bold">${escapeHtml(s.full_name || s.username)} ${currentBadge}</div>
                    <div class="small text-muted font-monospace">${escapeHtml(s.username)} <span class="badge ${roleBadge} ms-1" style="font-size:0.65rem;">${escapeHtml((s.role || '').toUpperCase())}</span></div>
                </td>
                <td>
                    <code class="px-2 py-1 bg-dark-subtle rounded border border-secondary-subtle">${escapeHtml(s.ip_address)}</code>
                </td>
                <td>
                    ${localIpDisplay}
                    <div class="small text-muted font-monospace" style="font-size: 0.72rem;">ID: ${deviceIdShort}…</div>
                </td>
                <td>
                    <div class="text-truncate" style="max-width: 220px;" title="${escapeHtml(s.user_agent || '')}">
                        <i class="bi bi-laptop me-1 text-info"></i>${deviceName}
                    </div>
                </td>
                <td><small class="text-muted">${escapeHtml(new Date(s.created_at * 1000).toLocaleString('vi-VN'))}</small></td>
                <td><small>${formatRelativeTime(s.last_active_at)}</small></td>
                <td class="text-end">${actionButtons}</td>
            </tr>`;
        }).join('');
    }

    function renderBlockedDevices(devices, error = null) {
        const tbody = document.getElementById('blocked-devices-table-body');
        const badge = document.getElementById('blocked-devices-badge');
        if (badge) badge.textContent = `${devices.length} máy`;
        if (!tbody) return;

        if (error && !devices.length) {
            tbody.innerHTML = `<tr><td colspan="8"><div class="ops-empty-state text-warning"><i class="bi bi-exclamation-triangle me-2"></i><span>Không thể tải danh sách máy bị chặn (${escapeHtml(error)})</span></div></td></tr>`;
            return;
        }

        if (!devices.length) {
            tbody.innerHTML = '<tr><td colspan="8"><div class="ops-empty-state"><i class="bi bi-shield-check"></i><span>Không có máy tính nào bị chặn</span></div></td></tr>';
            return;
        }

        tbody.innerHTML = devices.map(d => {
            return `<tr>
                <td><code class="font-monospace">${escapeHtml(d.device_id)}</code></td>
                <td><i class="bi bi-pc me-1 text-danger"></i>${escapeHtml(d.device_name || 'Thiết bị')}</td>
                <td>${d.local_ip ? `<span class="badge bg-dark-subtle border border-danger-subtle text-danger"><i class="bi bi-pc-display me-1"></i>${escapeHtml(d.local_ip)}</span>` : '<span class="text-muted">—</span>'}</td>
                <td><code>${escapeHtml(d.last_ip || 'N/A')}</code></td>
                <td><strong>${escapeHtml(d.username || '—')}</strong></td>
                <td><small class="text-muted">${escapeHtml(new Date(d.blocked_at * 1000).toLocaleString('vi-VN'))}</small></td>
                <td><span class="badge bg-danger-subtle text-danger border border-danger-subtle">${escapeHtml(d.reason || 'Chặn bởi admin')}</span></td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-success unblock-device-btn" data-device-id="${escapeHtml(d.device_id)}">
                        <i class="bi bi-unlock-fill me-1"></i>Bỏ chặn máy
                    </button>
                </td>
            </tr>`;
        }).join('');
    }

    function renderSuspiciousIPs(suspiciousIPs) {
        suspiciousIpsContainer.innerHTML = '';
        if (suspiciousIPs.length === 0) {
            suspiciousIpsContainer.innerHTML = '<div class="ops-empty-state"><i class="bi bi-shield-check"></i><span>No suspicious activity</span></div>';
            return;
        }
        suspiciousIPs.forEach(item => {
            const alertLevel = item.score >= 50 ? 'danger' : item.score >= 30 ? 'warning' : 'info';
            const div = document.createElement('div');
            div.className = `alert alert-${alertLevel} d-flex justify-content-between align-items-center mb-2`;
            div.innerHTML = `
                <div>
                    <code>${escapeHtml(item.ip)}</code>
                    <span class="badge bg-${alertLevel} ms-2">Score: ${Number(item.score) || 0}</span>
                </div>
                <button class="btn btn-sm btn-danger" type="button">
                    <i class="bi bi-lock-fill me-1"></i>Block
                </button>
            `;
            div.querySelector('button')?.addEventListener('click', () => handleBlockSuspiciousIP(String(item.ip || '')));
            suspiciousIpsContainer.appendChild(div);
        });
    }

    function updateSwitchesState(settings) {
        document.getElementById('firewall-switch').checked = settings.firewall_enabled;
        document.getElementById('vpn-switch').checked = settings.vpn_detection_enabled;
        document.getElementById('pattern-switch').checked = settings.pattern_analysis_enabled;
    }
    
    function setupSecuritySwitches() {
        const switchesContainer = document.querySelector('.accordion-body');
        switchesContainer.addEventListener('change', async (e) => {
            const switchEl = e.target;
            if (switchEl.type !== 'checkbox' || !switchEl.classList.contains('form-check-input')) return;

            const settingKeyMap = {
                'firewall-switch': 'firewall_enabled',
                'vpn-switch': 'vpn_detection_enabled',
                'pattern-switch': 'pattern_analysis_enabled'
            };
            const settingKey = settingKeyMap[switchEl.id];
            if (!settingKey) return;

            const newValue = switchEl.checked;
            switchEl.disabled = true;

            try {
                await apiFetch('/api/admin/settings', {
                    method: 'POST',
                    body: JSON.stringify({ [settingKey]: newValue })
                });
                showSuccess(`${settingKey} ${newValue ? 'enabled' : 'disabled'}`);
            } catch (error) {
                showError('Cannot change setting');
                switchEl.checked = !newValue;
            } finally {
                switchEl.disabled = false;
            }
        });
    }
    
    window.handleBlockSuspiciousIP = async function(ip) {
        const ok = await window.CorsUI.confirmAction({
            title: 'Block IP',
            message: `Block IP ${ip}?`,
            confirmText: 'Block',
            tone: 'danger'
        });
        if (!ok) return;
        try {
            await apiFetch('/api/admin/security/block-ip', {
                method: 'POST',
                body: JSON.stringify({ ip_address: ip })
            });
            showSuccess(`IP ${ip} blocked`);
            loadSecurityData();
        } catch (error) {}
    };
    
    async function handleUnblockIp(ip) {
        const ok = await window.CorsUI.confirmAction({
            title: 'Unblock IP',
            message: `Unblock IP ${ip}?`,
            confirmText: 'Unblock'
        });
        if (!ok) return;
        try {
            await apiFetch('/api/admin/security/unblock-ip', {
                method: 'POST',
                body: JSON.stringify({ ip_address: ip })
            });
            showSuccess(`IP ${ip} unblocked`);
            loadSecurityData();
        } catch (error) {}
    }

    async function handleBlockIp(e) {
        e.preventDefault();
        const ip = ipToBlockInput.value.trim();
        if (!ip) return;
        const ok = await window.CorsUI.confirmAction({
            title: 'Permanent blacklist',
            message: `Block IP ${ip} permanently?`,
            confirmText: 'Blacklist',
            tone: 'danger'
        });
        if (!ok) return;
        try {
            await apiFetch('/api/admin/security/block-ip', {
                method: 'POST',
                body: JSON.stringify({ ip_address: ip })
            });
            showSuccess(`IP ${ip} blacklisted`);
            ipToBlockInput.value = '';
            loadSecurityData();
        } catch (error) {}
    }

    async function handleRevokeSession(sessionId, username, localIp, deviceName) {
        const targetDesc = localIp ? ` (IP máy: ${localIp}, Thiết bị: ${deviceName})` : '';
        const ok = await window.CorsUI.confirmAction({
            title: 'Đăng xuất phiên làm việc',
            message: `Bạn có chắc muốn đăng xuất phiên của tài khoản "${username}"${targetDesc}? Máy này sẽ bị ngắt kết nối ngay lập tức.`,
            confirmText: 'Đăng xuất',
            tone: 'warning'
        });
        if (!ok) return;

        try {
            await apiFetch('/api/admin/security/sessions/revoke', {
                method: 'POST',
                body: JSON.stringify({ session_id: sessionId })
            });
            showSuccess(`Đã ngắt phiên làm việc của "${username}"`);
            loadSecurityData();
        } catch (error) {
            showError(`Không thể ngắt phiên: ${error.message}`);
        }
    }

    async function handleBlockDevice(deviceId, username, deviceName, localIp, publicIp) {
        const ipInfo = localIp ? `IP máy: ${localIp}` : `ID máy: ${deviceId.slice(0, 10)}…`;
        const ok = await window.CorsUI.confirmAction({
            title: 'Chặn riêng máy tính này',
            message: `Bạn có chắc muốn CHẶN riêng máy tính này (${ipInfo}, Thiết bị: ${deviceName || 'Không rõ'})?\n\nLưu ý: Chỉ riêng máy này bị chặn đăng nhập lại. Các máy tính khác trong cùng mạng (${publicIp}) hoàn toàn không bị ảnh hưởng.`,
            confirmText: 'Chặn máy này',
            tone: 'danger'
        });
        if (!ok) return;

        try {
            await apiFetch('/api/admin/security/sessions/block-device', {
                method: 'POST',
                body: JSON.stringify({
                    device_id: deviceId,
                    reason: `Chặn riêng máy (${localIp || deviceId.slice(0, 8)}) bởi admin`
                })
            });
            showSuccess(`Đã chặn riêng máy tính này. Các máy khác trong mạng ${publicIp} vẫn dùng bình thường.`);
            loadSecurityData();
        } catch (error) {
            showError(`Không thể chặn máy: ${error.message}`);
        }
    }

    async function handleUnblockDevice(deviceId) {
        const ok = await window.CorsUI.confirmAction({
            title: 'Bỏ chặn máy tính',
            message: `Bỏ chặn cho máy tính "${deviceId}"? Máy này sẽ có thể đăng nhập lại.`,
            confirmText: 'Bỏ chặn'
        });
        if (!ok) return;

        try {
            await apiFetch('/api/admin/security/sessions/unblock-device', {
                method: 'POST',
                body: JSON.stringify({ device_id: deviceId })
            });
            showSuccess('Đã bỏ chặn máy tính');
            loadSecurityData();
        } catch (error) {
            showError(`Không thể bỏ chặn: ${error.message}`);
        }
    }

    async function handleRevokeAllOthers() {
        const ok = await window.CorsUI.confirmAction({
            title: 'Đăng xuất tất cả các máy khác',
            message: 'Bạn có chắc chắn muốn đăng xuất tất cả các phiên làm việc khác trên mọi thiết bị? Chỉ phiên làm việc trên máy tính hiện tại của bạn được giữ lại.',
            confirmText: 'Đăng xuất tất cả máy khác',
            tone: 'danger'
        });
        if (!ok) return;

        try {
            const res = await apiFetch('/api/admin/security/sessions/revoke-all-others', {
                method: 'POST',
                body: JSON.stringify({})
            });
            showSuccess(res?.message || 'Đã đăng xuất tất cả các máy khác');
            loadSecurityData();
        } catch (error) {
            showError(`Lỗi: ${error.message}`);
        }
    }

    async function loadCurrentUser() {
        try {
            const user = await apiFetch('/api/auth/me');
            if (userInfoSpan) {
                userInfoSpan.textContent = `${user.full_name || user.username} [${user.role.toUpperCase()}]`;
            }
        } catch (error) {
            console.error("Error loading user:", error);
        }
    }

    refreshBtn.addEventListener('click', () => loadSecurityData(true));
    document.body.addEventListener('click', (e) => {
        if (e.target.closest('.unblock-btn')) {
            handleUnblockIp(e.target.closest('.unblock-btn').dataset.ip);
            return;
        }

        const revokeBtn = e.target.closest('.revoke-session-btn');
        if (revokeBtn) {
            const sid = revokeBtn.dataset.sessionId;
            const user = revokeBtn.dataset.username;
            const localIp = revokeBtn.dataset.localIp;
            const device = revokeBtn.dataset.device;
            handleRevokeSession(sid, user, localIp, device);
            return;
        }

        const blockDevBtn = e.target.closest('.block-device-btn');
        if (blockDevBtn) {
            const did = blockDevBtn.dataset.deviceId;
            const user = blockDevBtn.dataset.username;
            const name = blockDevBtn.dataset.deviceName;
            const localIp = blockDevBtn.dataset.localIp;
            const ip = blockDevBtn.dataset.ip;
            handleBlockDevice(did, user, name, localIp, ip);
            return;
        }

        const unblockDevBtn = e.target.closest('.unblock-device-btn');
        if (unblockDevBtn) {
            const did = unblockDevBtn.dataset.deviceId;
            handleUnblockDevice(did);
            return;
        }
    });

    const revokeOthersBtn = document.getElementById('revoke-all-others-btn');
    if (revokeOthersBtn) {
        revokeOthersBtn.addEventListener('click', handleRevokeAllOthers);
    }

    blockIpForm.addEventListener('submit', handleBlockIp);

    function showToast(message, type) {
        window.CorsUI.showToast(type, message);
    }

    function createToastContainer() {
        const container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container position-fixed top-0 end-0 p-3';
        container.style.zIndex = 1100;
        document.body.appendChild(container);
        return container;
    }

    function showSuccess(message) { showToast(message, 'success'); }
    function showError(message) { showToast(message, 'danger'); }

    function updateCircularProgress(elementId, value, max = 100) {
        const circle = document.getElementById(elementId);
        if (!circle) return;
        const circumference = 565.48;
        const percentage = Math.min(100, (value / max) * 100);
        circle.style.strokeDashoffset = circumference - (percentage / 100) * circumference;
    }
    
    function updateThreatLevel(level) {
        const bars = document.querySelectorAll('.threat-bar');
        bars.forEach((bar, index) => {
            bar.className = 'threat-bar';
            if (index < level) {
                if (level >= 4) bar.classList.add('danger');
                else if (level >= 3) bar.classList.add('warning');
                else bar.classList.add('active');
            }
        });
    }

    function startPolling() {
        if (securityDataInterval) {
            clearInterval(securityDataInterval);
        }
        loadSecurityData();
        securityDataInterval = setInterval(loadSecurityData, POLLING_INTERVAL_MS);
    }

    function stopPolling() {
        if (securityDataInterval) {
            clearInterval(securityDataInterval);
            securityDataInterval = null;
        }
    }

        // === 🆕 HÀM HIỂN THỊ CẢNH BÁO (KHÔNG PHẢI LỖI NGHIÊM TRỌNG) ===
    function showWarning(message) { 
        showToast(message, 'warning'); 
    }

    // === 🆕 HÀM HIỂN THỊ UI KHI MẤT KẾT NỐI SERVER ===
    function showOfflineState() {
        // Làm mờ các metrics để báo hiệu dữ liệu cũ
        const metricsCards = document.querySelectorAll('.ops-card');
        metricsCards.forEach(card => {
            card.style.opacity = '0.5';
            card.style.filter = 'grayscale(80%)';
        });
        
        // Hiển thị overlay cảnh báo
        const existingOverlay = document.getElementById('offline-overlay');
        if (existingOverlay) return; // Đã hiển thị rồi
        
        const overlay = document.createElement('div');
        overlay.id = 'offline-overlay';
        overlay.className = 'ops-confirm-backdrop';
        overlay.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            z-index: 9999;
            text-align: center;
        `;
        overlay.innerHTML = `
            <div class="ops-confirm">
                <div class="ops-confirm-head"><i class="bi bi-wifi-off me-2 text-danger"></i>Connection lost</div>
                <div class="ops-confirm-body">Cannot reach security monitoring server.</div>
                <div class="ops-confirm-actions">
                    <button id="retry-connection-btn" class="btn btn-primary">
                        <i class="bi bi-arrow-clockwise me-2"></i>Retry
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        
        // Xử lý nút Retry
        document.getElementById('retry-connection-btn').addEventListener('click', () => {
            overlay.remove();
            // Khôi phục UI
            metricsCards.forEach(card => {
                card.style.opacity = '1';
                card.style.filter = 'none';
            });
            // Reset error counter và thử lại
            window.securityErrorCount = 0;
            startPolling();
        });
    }

    function showToast(message, type) {
        window.CorsUI.showToast(type, message);
    }

    // ============================================================
    // === 🆕 WEBSOCKET INTEGRATION FOR SECURITY PAGE           ===
    // ============================================================
    
    let wsSecurity = null;
    let wsSecurityReconnectAttempts = 0;
    let wsSecurityReconnectTimerId = null;
    const MAX_WS_RECONNECT = 10;
    let visitorPulseTimerId = null;
    let lastVisitorCount = null;

    window.addEventListener('cors:session-expired', () => {
        if (wsSecurityReconnectTimerId) {
            clearTimeout(wsSecurityReconnectTimerId);
            wsSecurityReconnectTimerId = null;
        }
        if (wsSecurity && wsSecurity.readyState !== WebSocket.CLOSED) {
            try { wsSecurity.close(1000, 'session expired'); } catch (_) {}
        }
    });

    function connectSecurityWebSocket() {
        if (window.ApiClient?.isSessionInvalidated?.() || !window.ApiClient?.hasToken?.()) return;

        wsSecurity = window.ApiClient.createAuthenticatedWebSocket('/ws/updates');
        if (!wsSecurity) return;
        
        wsSecurity.onopen = () => {
            console.log('[SEC] WebSocket Connected');
            wsSecurityReconnectAttempts = 0;
            // Gửi message hello nếu cần thiết (tùy backend)
        };
        
        wsSecurity.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                handleSecurityWebSocketMessage(message);
            } catch (e) {
                console.error('WS Parse Error:', e);
            }
        };
        
        wsSecurity.onclose = (event) => {
            console.warn('[SEC] WebSocket Disconnected');
            if (window.ApiClient?.isAuthWebSocketClose?.(event)) {
                window.ApiClient.forceLogout('websocket_auth_failed');
                return;
            }
            if (window.ApiClient?.isSessionInvalidated?.() || !window.ApiClient?.hasToken?.()) return;
            if (wsSecurityReconnectAttempts < MAX_WS_RECONNECT) {
                const delay = Math.min(1000 * Math.pow(2, wsSecurityReconnectAttempts), 10000);
                wsSecurityReconnectAttempts++;
                wsSecurityReconnectTimerId = setTimeout(connectSecurityWebSocket, delay);
            }
        };
    }

    function handleSecurityWebSocketMessage(message) {
        const { type, count } = message;
        
        // === 1. CẬP NHẬT SỐ LƯỢNG KHÁCH TRUY CẬP ===
        if (type === 'visitor_update') {
            const visitorEl = document.getElementById('live-visitors-count');
            if (visitorEl) {
                if (lastVisitorCount === count) return;
                lastVisitorCount = count;
                // Cập nhật số
                visitorEl.textContent = count;
                
                visitorEl.style.color = '#ffffff';
                visitorEl.style.transform = 'scale(1.2)';
                visitorEl.style.transition = 'all 0.2s ease';
                
                if (visitorPulseTimerId) {
                    clearTimeout(visitorPulseTimerId);
                }
                visitorPulseTimerId = setTimeout(() => {
                    visitorEl.style.color = '';
                    visitorEl.style.textShadow = '';
                    visitorEl.style.transform = 'scale(1)';
                    visitorPulseTimerId = null;
                }, 300);
            }
        }
        
        // === 2. CẬP NHẬT BIỂU ĐỒ TRAFFIC REAL-TIME (NẾU CÓ) ===
        // Nếu Backend gửi thông tin về Traffic qua WS, bạn có thể update biểu đồ tại đây
        // Ví dụ: if (type === 'traffic_update') { ... }
    }


    async function init() {
        await loadCurrentUser();
        setupSecuritySwitches();
        startPolling();
        connectSecurityWebSocket(); 
        window.addEventListener('beforeunload', stopPolling);
    }
    
    init();
});
