// ============================================================
// security.js - v5.0 - FIXED THREAT LEVEL CALCULATION
// ============================================================

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

    async function apiFetch(url, options = {}) {
        try {
            const response = await fetch(url, { headers: API_HEADERS, ...options });
            if (response.status === 401 || response.status === 403) {
                if (response.status === 403) {
                    alert('[ACCESS DENIED] Admin only!');
                    window.location.href = '/';
                } else {
                    alert('[SESSION EXPIRED]');
                    localStorage.removeItem('access_token');
                    window.location.href = '/login.html';
                }
                throw new Error('Unauthorized');
            }
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || `Error ${response.status}`);
            }
            return options.method === 'POST' || options.method === 'DELETE' ? response : response.json();
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
        if (isManualReload) {
            refreshBtn.disabled = true;
            refreshBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> LOADING...';
        }

        try {
            const [statsData, settingsData] = await Promise.all([
                apiFetch('/api/admin/security/stats'),
                apiFetch('/api/admin/settings')
            ]);
            
            // === 🆕 KIỂM TRA NẾU API TRẢ VỀ LỖI (status: "error") ===
            if (statsData.status === 'error') {
                console.warn('⚠️ Security stats degraded:', statsData.error);
                showWarning(`Security monitoring degraded: ${statsData.error}`);
                
                // Vẫn hiển thị dữ liệu partial nếu có
                if (statsData.rate_limiter) {
                    updateDashboardMetrics(statsData.rate_limiter, settingsData);
                    renderBlacklistTable(statsData.rate_limiter.blacklisted_ips || []);
                    renderTemporaryBlocksTable(statsData.rate_limiter.temporary_blocked_ips || []);
                    renderSuspiciousIPs(statsData.rate_limiter.suspicious_ips || []);
                    renderTrafficChart(statsData.rate_limiter.traffic_history || Array(60).fill(0));
                    renderVpnIntelligence(statsData.rate_limiter.vpn_suspects || []);
                    handleAttackMode(statsData.rate_limiter.attack_mode || false);
                }
                
                // Vẫn hiển thị system health nếu có
                if (statsData.system_health) {
                    renderSystemHealth(statsData.system_health);
                }
                
                updateSwitchesState(settingsData);
                return; // Dừng ở đây, không throw error
            }
            
            // === LOGIC CŨ (Khi không có lỗi) ===
            updateDashboardMetrics(statsData.rate_limiter, settingsData);
            renderBlacklistTable(statsData.rate_limiter.blacklisted_ips);
            renderTemporaryBlocksTable(statsData.rate_limiter.temporary_blocked_ips);
            renderSuspiciousIPs(statsData.rate_limiter.suspicious_ips);
            updateSwitchesState(settingsData);
            renderTrafficChart(statsData.rate_limiter.traffic_history);
            renderVpnIntelligence(statsData.rate_limiter.vpn_suspects);
            handleAttackMode(statsData.rate_limiter.attack_mode);

            if (statsData.system_health) {
                renderSystemHealth(statsData.system_health);
            }
            
            if (statsData.rate_limiter.traffic_history.length > 0) {
                const lastVal = statsData.rate_limiter.traffic_history[statsData.rate_limiter.traffic_history.length - 1];
                currentRpsEl.textContent = lastVal;
            }

            if (isManualReload) {
                showSuccess('[SYNC COMPLETE] Security data reloaded!');
            }
            
        } catch (error) {
            console.error('❌ Failed to load security data:', error);
            
            // === 🆕 XỬ LÝ LỖI NETWORK/SERVER ===
            if (error.message === 'Unauthorized') {
                // apiFetch đã xử lý redirect, không cần làm gì
                return;
            }
            
            // Hiển thị thông báo lỗi cho user
            showError(`[CONNECTION ERROR] ${error.message || 'Cannot reach server'}`);
            
            // === 🆕 HIỂN thị UI "OFFLINE STATE" ===
            showOfflineState();
            
            // === 🆕 DỪNG POLLING NẾU LỖI LIÊN TỤC ===
            // (Tránh spam request khi server down)
            if (!window.securityErrorCount) window.securityErrorCount = 0;
            window.securityErrorCount++;
            
            if (window.securityErrorCount >= 3) {
                console.warn('⚠️ Too many errors, stopping auto-refresh');
                stopPolling();
                showError('[AUTO-REFRESH DISABLED] Click RELOAD to retry');
            }
            
        } finally {
            if (isManualReload) {
                refreshBtn.disabled = false;
                refreshBtn.innerHTML = '<i class="bi bi-arrow-clockwise me-2"></i>RELOAD DATA';
                
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
                // Hiệu ứng đổi màu text nếu cao
                elVal.style.color = val > 90 ? 'var(--neon-red)' : 'var(--neon-green)';
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
        
        // Draw Grid (Hacker Green lines)
        ctx.strokeStyle = 'rgba(0, 255, 65, 0.1)';
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
        gradient.addColorStop(0, 'rgba(0, 255, 65, 0.5)');
        gradient.addColorStop(1, 'rgba(0, 255, 65, 0.0)');
        ctx.fillStyle = gradient;
        ctx.fill();

        // Draw Line Path
        ctx.beginPath();
        ctx.strokeStyle = '#00ff41';
        ctx.lineWidth = 2;
        ctx.shadowBlur = 10;
        ctx.shadowColor = '#00ff41';
        
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
            vpnTableBody.innerHTML = `<tr><td colspan="3" class="text-center text-muted py-4"><i class="bi bi-shield-check me-2"></i>NO ANOMALIES DETECTED</td></tr>`;
            return;
        }

        suspects.forEach(item => {
            let badgeClass = 'bg-secondary';
            if (item.risk_level === 'CRITICAL') badgeClass = 'bg-danger text-white pulse-glow-danger';
            else if (item.risk_level === 'HIGH') badgeClass = 'bg-warning text-dark';

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>
                    <code style="color: var(--neon-blue)">${item.fingerprint.substring(0, 8)}...</code>
                    <div style="font-size: 0.7em; color: #666; max-width: 150px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${item.user_agent}</div>
                </td>
                <td class="text-center fw-bold" style="color: var(--neon-green)">${item.ip_count}</td>
                <td class="text-end"><span class="badge ${badgeClass}">${item.risk_level}</span></td>
            `;
            vpnTableBody.appendChild(tr);
        });
    }

    function handleAttackMode(isAttackMode) {
        if (isAttackMode) {
            attackAlert.classList.remove('d-none');
            document.body.style.border = "2px solid var(--neon-red)";
        } else {
            attackAlert.classList.add('d-none');
            document.body.style.border = "none";
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
        let statusColor = 'var(--neon-green)';
        
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
            statusText = 'CRITICAL: ALL PROTECTION DISABLED';
            statusClass = 'bg-danger pulse-glow-danger';
            statusColor = 'var(--neon-red)';
        } else if (activeModules === 1) {
            // ⚠️ CHỈ 1 MODULE HOẠT ĐỘNG = NGUY HIỂM
            if (threatLevel < 4) threatLevel = 4;
            statusText = 'HIGH RISK: MINIMAL PROTECTION';
            statusClass = 'bg-danger';
            statusColor = 'var(--neon-red)';
        } else if (activeModules === 2) {
            // ⚠️ 2/3 MODULE = CẢNH BÁO
            if (threatLevel < 3) threatLevel = 3;
            statusText = 'WARNING: DEGRADED PROTECTION';
            statusClass = 'bg-warning text-dark';
            statusColor = '#ffd700';
        }
        // Nếu cả 3 module đều bật, giữ nguyên threatLevel từ số IP bị khóa
        
        // BƯỚC 4: Override status text nếu có attack mode
        if (stats.attack_mode) {
            threatLevel = 5;
            statusText = '🚨 DDOS ATTACK IN PROGRESS';
            statusClass = 'bg-danger pulse-glow-danger';
            statusColor = 'var(--neon-red)';
        }
        
        // BƯỚC 5: Áp dụng UI
        updateThreatLevel(threatLevel);
        
        const statusBadge = document.getElementById('system-status-badge');
        if (statusBadge) {
            // Override nếu chưa tính toán ở trên
            if (!statusText.includes('CRITICAL') && !statusText.includes('HIGH RISK') && !stats.attack_mode) {
                const levels = {
                    1: { text: 'ALL SYSTEMS NOMINAL', class: 'bg-success', color: 'var(--neon-green)' },
                    2: { text: 'LOW THREAT DETECTED', class: 'bg-success', color: 'var(--neon-green)' },
                    3: { text: 'MODERATE THREAT', class: 'bg-warning text-dark', color: '#ffd700' },
                    4: { text: 'HIGH THREAT LEVEL', class: 'bg-danger', color: 'var(--neon-red)' },
                    5: { text: 'CRITICAL THREAT', class: 'bg-danger pulse-glow-danger', color: 'var(--neon-red)' }
                };
                const level = levels[threatLevel];
                statusText = level.text;
                statusClass = level.class;
                statusColor = level.color;
            }
            
            statusBadge.textContent = statusText;
            statusBadge.className = `badge ${statusClass}`;
            statusBadge.style.boxShadow = `0 0 10px ${statusColor}`;
        }
    }

    function renderBlacklistTable(blacklistedIps) {
        blacklistTableBody.innerHTML = '';
        if (blacklistedIps.length === 0) {
            blacklistTableBody.innerHTML = `<tr><td colspan="3"><div class="empty-state-hacker"><i class="bi bi-shield-check"></i><p class="mt-3 mb-0">[ SYSTEM SECURE ]</p></div></td></tr>`;
            return;
        }
        blacklistedIps.forEach(block => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><code>${block.ip}</code></td>
                <td>${block.reason || 'N/A'}</td>
                <td class="text-end">
                    <button class="btn btn-sm btn-hacker unblock-btn" data-ip="${block.ip}">
                        <i class="bi bi-unlock-fill me-1"></i>UNBLOCK
                    </button>
                </td>
            `;
            blacklistTableBody.appendChild(tr);
        });
    }
    
    function renderTemporaryBlocksTable(tempBlocks) {
        tempBlockedTableBody.innerHTML = '';
        if (tempBlocks.length === 0) {
            tempBlockedTableBody.innerHTML = `<tr><td colspan="4"><div class="empty-state-hacker"><i class="bi bi-shield-check"></i><p class="mt-3 mb-0">[ NO TEMPORARY BLOCKS ]</p></div></td></tr>`;
            return;
        }
        tempBlocks.forEach(block => {
            const minutes = Math.ceil(block.remaining_seconds / 60);
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><code>${block.ip}</code></td>
                <td style="color: #ffd700;">${minutes} phút</td>
                <td>${block.reason || 'N/A'}</td>
                <td class="text-end">
                    <button class="btn btn-sm btn-hacker unblock-btn" data-ip="${block.ip}">
                        <i class="bi bi-unlock-fill me-1"></i>UNBLOCK
                    </button>
                </td>
            `;
            tempBlockedTableBody.appendChild(tr);
        });
    }
    
    function renderSuspiciousIPs(suspiciousIPs) {
        suspiciousIpsContainer.innerHTML = '';
        if (suspiciousIPs.length === 0) {
            suspiciousIpsContainer.innerHTML = '<p class="text-success"><i class="bi bi-shield-check me-2"></i>No suspicious activity</p>';
            return;
        }
        suspiciousIPs.forEach(item => {
            const alertLevel = item.score >= 50 ? 'danger' : item.score >= 30 ? 'warning' : 'info';
            const div = document.createElement('div');
            div.className = `alert alert-${alertLevel} d-flex justify-content-between align-items-center mb-2`;
            div.innerHTML = `
                <div>
                    <code>${item.ip}</code>
                    <span class="badge bg-${alertLevel} ms-2">Score: ${item.score}</span>
                </div>
                <button class="btn btn-sm btn-danger" onclick="handleBlockSuspiciousIP('${item.ip}')">
                    <i class="bi bi-lock-fill me-1"></i>BLOCK
                </button>
            `;
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
                showSuccess(`[SAVED] ${settingKey} ${newValue ? 'ENABLED' : 'DISABLED'}`);
            } catch (error) {
                showError(`[FAILED] Cannot change setting`);
                switchEl.checked = !newValue;
            } finally {
                switchEl.disabled = false;
            }
        });
    }
    
    window.handleBlockSuspiciousIP = async function(ip) {
        if (!confirm(`⚠️ Block IP ${ip}?`)) return;
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
        if (!confirm(`Unblock IP '${ip}'?`)) return;
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
        if (!confirm(`Block IP '${ip}' permanently?`)) return;
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
        }
    });
    blockIpForm.addEventListener('submit', handleBlockIp);

    function showToast(message, type) {
        const toastContainer = document.getElementById('toast-container') || createToastContainer();
        const toastEl = document.createElement('div');
        let bgColor, iconClass;
        if (type === 'success') { bgColor = 'var(--neon-green)'; iconClass = 'bi-check-circle-fill'; }
        else if (type === 'danger') { bgColor = 'var(--neon-red)'; iconClass = 'bi-exclamation-triangle-fill'; }
        else { bgColor = 'var(--neon-blue)'; iconClass = 'bi-info-circle-fill'; }
        
        Object.assign(toastEl.style, {
            background: 'rgba(0, 0, 0, 0.95)',
            border: `1px solid ${bgColor}`,
            color: bgColor,
            fontFamily: "'Courier New', monospace",
            boxShadow: `0 0 20px ${bgColor}`
        });
        toastEl.className = 'toast align-items-center border-0';
        toastEl.setAttribute('role', 'alert');
        toastEl.innerHTML = `<div class="d-flex p-2"><div class="toast-body d-flex align-items-center"><i class="bi ${iconClass} me-2"></i>${message}</div><button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" style="filter: brightness(0) invert(1);"></button></div>`;
        
        toastContainer.appendChild(toastEl);
        const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
        toast.show();
        toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
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
        const metricsCards = document.querySelectorAll('.stat-card, .security-card');
        metricsCards.forEach(card => {
            card.style.opacity = '0.5';
            card.style.filter = 'grayscale(80%)';
        });
        
        // Hiển thị overlay cảnh báo
        const existingOverlay = document.getElementById('offline-overlay');
        if (existingOverlay) return; // Đã hiển thị rồi
        
        const overlay = document.createElement('div');
        overlay.id = 'offline-overlay';
        overlay.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: rgba(0, 0, 0, 0.95);
            border: 2px solid var(--neon-red);
            padding: 30px;
            border-radius: 10px;
            z-index: 9999;
            text-align: center;
            box-shadow: 0 0 30px var(--neon-red);
        `;
        overlay.innerHTML = `
            <i class="bi bi-wifi-off" style="font-size: 3rem; color: var(--neon-red);"></i>
            <h3 style="color: var(--neon-red); margin-top: 15px; font-family: 'Courier New', monospace;">
                CONNECTION LOST
            </h3>
            <p style="color: #888; margin-top: 10px;">
                Cannot reach security monitoring server
            </p>
            <button id="retry-connection-btn" class="btn btn-hacker mt-3">
                <i class="bi bi-arrow-clockwise me-2"></i>RETRY CONNECTION
            </button>
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

    // === CẬP NHẬT HÀM showToast ĐỂ HỖ TRỢ TYPE "WARNING" ===
    function showToast(message, type) {
        const toastContainer = document.getElementById('toast-container') || createToastContainer();
        const toastEl = document.createElement('div');
        let bgColor, iconClass;
        
        if (type === 'success') { 
            bgColor = 'var(--neon-green)'; 
            iconClass = 'bi-check-circle-fill'; 
        } else if (type === 'danger') { 
            bgColor = 'var(--neon-red)'; 
            iconClass = 'bi-exclamation-triangle-fill'; 
        } else if (type === 'warning') { // 🆕 THÊM WARNING
            bgColor = '#ffd700'; 
            iconClass = 'bi-exclamation-circle-fill'; 
        } else { 
            bgColor = 'var(--neon-blue)'; 
            iconClass = 'bi-info-circle-fill'; 
        }
        
        Object.assign(toastEl.style, {
            background: 'rgba(0, 0, 0, 0.95)',
            border: `1px solid ${bgColor}`,
            color: bgColor,
            fontFamily: "'Courier New', monospace",
            boxShadow: `0 0 20px ${bgColor}`
        });
        toastEl.className = 'toast align-items-center border-0';
        toastEl.setAttribute('role', 'alert');
        toastEl.innerHTML = `
            <div class="d-flex p-2">
                <div class="toast-body d-flex align-items-center">
                    <i class="bi ${iconClass} me-2"></i>${message}
                </div>
                <button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" 
                        style="filter: brightness(0) invert(1);"></button>
            </div>
        `;
        
        toastContainer.appendChild(toastEl);
        const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
        toast.show();
        toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
    }

    // ============================================================
    // === 🆕 WEBSOCKET INTEGRATION FOR SECURITY PAGE           ===
    // ============================================================
    
    let wsSecurity = null;
    let wsSecurityReconnectAttempts = 0;
    const MAX_WS_RECONNECT = 10;

    function connectSecurityWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/updates`; // Kết nối vào kênh Admin chung
        
        wsSecurity = new WebSocket(wsUrl);
        
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
        
        wsSecurity.onclose = () => {
            console.warn('[SEC] WebSocket Disconnected');
            if (wsSecurityReconnectAttempts < MAX_WS_RECONNECT) {
                const delay = Math.min(1000 * Math.pow(2, wsSecurityReconnectAttempts), 10000);
                wsSecurityReconnectAttempts++;
                setTimeout(connectSecurityWebSocket, delay);
            }
        };
    }

    function handleSecurityWebSocketMessage(message) {
        const { type, count } = message;
        
        // === 1. CẬP NHẬT SỐ LƯỢNG KHÁCH TRUY CẬP ===
        if (type === 'visitor_update') {
            const visitorEl = document.getElementById('live-visitors-count');
            if (visitorEl) {
                // Cập nhật số
                visitorEl.textContent = count;
                
                // Hiệu ứng nháy sáng
                visitorEl.style.color = '#ffffff';
                visitorEl.style.textShadow = '0 0 20px #ffffff';
                visitorEl.style.transform = 'scale(1.2)';
                visitorEl.style.transition = 'all 0.2s ease';
                
                setTimeout(() => {
                    visitorEl.style.color = '#ff00ff'; // Trả về màu tím neon
                    visitorEl.style.textShadow = '0 0 10px #ff00ff';
                    visitorEl.style.transform = 'scale(1)';
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