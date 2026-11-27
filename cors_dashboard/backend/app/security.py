# ==============================================================================
# == backend/app/security.py - v4.0 - ULTIMATE ANTI-DDOS PROTECTION        ==
# ==============================================================================

import time
import logging
import hashlib
import re
from collections import defaultdict, deque
from typing import Optional, Dict, Set
from fastapi import Request, HTTPException, status
import asyncio
import ipaddress
import os

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from . import models, crud
from .database import settings

logger = logging.getLogger(__name__)

# ==============================================================================
# === RATE LIMITER - ANTI-VPN DDOS                                 ===
# ==============================================================================

class AdvancedRateLimiter:    
    def __init__(self, max_attempts: int, window_minutes: int, 
                 block_duration_minutes: int, permanent_block_threshold: int):
        # === CORE TRACKING ===
        self.ip_attempts: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self.ip_fingerprints: Dict[str, set] = defaultdict(set)
        self.suspicious_patterns: Dict[str, int] = defaultdict(int)
        self.request_intervals: Dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
        
        # === 🆕 ADVANCED ANTI-VPN TRACKING ===
        self.fingerprint_to_ips: Dict[str, Set[str]] = defaultdict(set)  # Fingerprint → IPs
        self.user_agent_patterns: Dict[str, int] = defaultdict(int)      # Detect bot patterns
        self.global_request_rate: deque = deque(maxlen=1000)             # Global attack detection
        
        # === 🆕 ADDED FOR METRICS ===
        self._stats_cache = {}
        self.http_counts = {}
        self.http_ip_last_seen = {}
        self.recent_blocks = deque(maxlen=200)
        self.runtime_blocks = {}
        self.traffic_history = deque(maxlen=60) 
        self.last_tick = int(time.time())
        self.global_request_rate = deque(maxlen=1000) 
        
        # Lưu trữ thống kê Fingerprint chi tiết hơn
        self.fingerprint_history = defaultdict(lambda: {
            "ips": set(), 
            "ua": "Unknown", 
            "first_seen": int(time.time()), 
            "recent_count": 0
        })

        # === ADAPTIVE BLOCKING ===
        self.block_escalation: Dict[str, int] = defaultdict(int)
        self.attack_mode: bool = False  # Chế độ khẩn cấp khi phát hiện tấn công
        self.attack_mode_until: float = 0
        
        # === CONFIGURATION ===
        self.MAX_ATTEMPTS = max_attempts
        self.WINDOW_SECONDS = window_minutes * 60
        self.BLOCK_DURATION_SECONDS = block_duration_minutes * 60
        self.PERMANENT_THRESHOLD = permanent_block_threshold
        
        # === 🆕 ANTI-VPN THRESHOLDS ===
        self.MAX_FINGERPRINTS_PER_IP = 3      # Giảm từ 5 → 3 (nghiêm ngặt hơn)
        self.MAX_IPS_PER_FINGERPRINT = 10      # CRITICAL: Cùng fingerprint từ >10 IPs = VPN attack
        self.MIN_REQUEST_INTERVAL = 0.2        # Tăng từ 0.1 → 0.2s (chặn bot nhanh hơn)
        self.SUSPICIOUS_SCORE_THRESHOLD = 40   # Giảm từ 50 → 40 (nhạy hơn)
        
        # === 🆕 GLOBAL ATTACK DETECTION ===
        self.GLOBAL_RATE_THRESHOLD = 100       # >100 requests/5s = potential DDoS
        self.ATTACK_MODE_DURATION = 300        # 5 minutes in strict mode
        
        self.lock = asyncio.Lock()
        
        logger.info(f"✓ ULTIMATE Anti-DDoS Protection initialized")

    def record_http_request(self, ip: str, now: float = None):
        now = time.time() if now is None else now
        tick = int(now)
        if tick not in self.http_counts:
            self.http_counts = {second: count for second, count in self.http_counts.items() if second >= tick - 60}
            self.http_ip_last_seen = {peer: seen for peer, seen in self.http_ip_last_seen.items() if seen > now - 300}
        self.http_counts[tick] = self.http_counts.get(tick, 0) + 1
        if ip not in self.http_ip_last_seen and len(self.http_ip_last_seen) >= 10000:
            self.http_ip_last_seen.pop(next(iter(self.http_ip_last_seen)))
        self.http_ip_last_seen[ip] = now

    def record_block_event(self, ip: str, reason: str, source: str, blocked_until=None, permanent=False):
        now = time.time()
        # Coalesce repeated denials without hiding short-lived blocks after expiry.
        previous = next((item for item in self.recent_blocks if item['ip'] == ip and item['source'] == source and item['reason'] == reason), None)
        if previous and now - previous['last_seen'] < 60:
            previous['last_seen'] = now
            previous['count'] += 1
            previous['blocked_until'] = blocked_until
        else:
            self.recent_blocks.append({'ip': ip, 'reason': reason, 'source': source,
                                      'first_seen': now, 'last_seen': now, 'count': 1,
                                      'blocked_until': blocked_until, 'permanent': permanent})
        if source == 'http_rate_limit' and blocked_until:
            self.runtime_blocks[ip] = {'ip': ip, 'reason': reason, 'source': source, 'blocked_until': blocked_until}

    def merge_runtime_stats(self, stats: dict, banned_ips: list) -> dict:
        now = time.time()
        tick = int(now)
        result = dict(stats)
        # Completed seconds avoid showing a partially counted current second.
        result['traffic_history'] = [self.http_counts.get(second, 0) for second in range(tick - 60, tick)]
        result['global_request_rate'] = sum(result['traffic_history'][-5:])
        result['total_ips_tracked'] = sum(seen > now - 300 for seen in self.http_ip_last_seen.values())
        permanent = {item['ip']: dict(item) for item in stats.get('blacklisted_ips', [])}
        for ip in banned_ips:
            permanent.setdefault(ip, {'ip': ip, 'reason': 'Security Gate: repeated violations', 'source': 'security_gate'})
        temporary = {item['ip']: dict(item) for item in stats.get('temporary_blocked_ips', []) if item.get('blocked_until', 0) > now}
        self.runtime_blocks = {ip: item for ip, item in self.runtime_blocks.items() if item['blocked_until'] > now}
        for ip, item in self.runtime_blocks.items():
            if ip not in temporary or temporary[ip]['blocked_until'] < item['blocked_until']:
                temporary[ip] = dict(item)
        for ip in permanent:
            temporary.pop(ip, None)
        for item in temporary.values():
            item['remaining_seconds'] = max(0, int(item['blocked_until'] - now))
        result.update(blacklisted_ips=list(permanent.values()), permanent_blacklist_count=len(permanent),
                      temporary_blocked_ips=list(temporary.values()), blocked_ips_count=len(temporary),
                      recent_blocks=sorted((dict(item) for item in self.recent_blocks if item['last_seen'] > now - 3600), key=lambda item: item['last_seen'], reverse=True))
        return result

    # ==========================================================================
    # === MAIN ENTRY POINT WITH ATTACK MODE                                  ===
    # ==========================================================================
    
    async def check_and_record(self, db: AsyncSession, request: Request, 
                               username: Optional[str] = None, 
                               is_success: bool = False, 
                               user_role: Optional[str] = None):
        """
        🛡️ Multi-layer protection với attack mode detection
        Phiên bản: v5.0 (Tích hợp Data Recording cho Dashboard)
        """
        # === LOAD SETTINGS ===
        settings = await crud.get_system_settings(db)
        if not settings.get('firewall_enabled', True):
            logger.warning("⚠️ FIREWALL DISABLED - SYSTEM VULNERABLE!")
            return

        async with self.lock:
            now = time.time()
            client_ip = self._get_client_ip(request)
            
            # ==================================================================
            # 📊 DATA RECORDING PHASE (Bắt buộc cho get_stats & Dashboard)
            # ==================================================================
            
            # 1. Cập nhật Traffic History (Request/Second) cho biểu đồ
            current_tick = int(now)
            if current_tick > self.last_tick:
                # Nếu đã sang giây mới, điền số 0 cho những giây trôi qua không có request
                for _ in range(current_tick - self.last_tick - 1):
                    self.traffic_history.append(0)
                self.traffic_history.append(1) # Request đầu tiên của giây hiện tại
                self.last_tick = current_tick
            else:
                # Nếu vẫn trong giây cũ, tăng đếm
                if not self.traffic_history:
                    self.traffic_history.append(1)
                else:
                    self.traffic_history[-1] += 1

            # 2. Cập nhật Global Request Rate (để phát hiện DDoS tổng thể)
            self.global_request_rate.append(now)

            # 3. Cập nhật Fingerprint History (Cho bảng VPN Intelligence)
            # Tạo fingerprint sớm để lưu metadata
            fingerprint = self._generate_fingerprint(request)
            user_agent = request.headers.get("User-Agent", "Unknown")
            
            # Lấy hoặc tạo entry trong fingerprint_history
            fp_entry = self.fingerprint_history[fingerprint]
            fp_entry["ips"].add(client_ip) # Thêm IP vào set (để đếm số lượng IP unique)
            fp_entry["ua"] = user_agent
            
            # Logic đếm recent_count (Reset nếu quá 1 giờ)
            if now - fp_entry["first_seen"] > 3600:
                 fp_entry["recent_count"] = 1
                 fp_entry["first_seen"] = int(now)
                 fp_entry["ips"] = {client_ip} # Reset set IPs
            else:
                 fp_entry["recent_count"] += 1

            # ==================================================================
            # 🛡️ SECURITY LOGIC PHASE
            # ==================================================================

            # === STEP 1: DETECT GLOBAL ATTACK ===
            self._check_global_attack_pattern(now)
            
            # === STEP 2: DATABASE BLOCK CHECK ===
            # Kiểm tra xem IP đã bị block trong DB chưa
            block_record = await self._check_database_blocks(db, client_ip, now)
            if block_record:
                # Nếu đã bị block, return ngay (Request đã bị chặn bởi Middleware hoặc code này)
                # Lưu ý: Vẫn record traffic ở trên để admin thấy "Attempted Requests"
                return
            
            # === STEP 3: ADVANCED THREAT ANALYSIS ===
            threat_score = await self._advanced_threat_analysis(
                client_ip, 
                fingerprint, 
                request,
                now,
                settings
            )
            
            # === STEP 4: ATTACK MODE SCALING ===
            if self.attack_mode:
                threat_score *= 1.5  # Tăng độ nhạy lên 50% khi đang bị tấn công
                # Log cảnh báo nhưng hạn chế spam log quá nhiều
                if len(self.ip_attempts[client_ip]) % 5 == 0: 
                    logger.warning(f"⚠️ ATTACK MODE ACTIVE - Increased scrutiny for {client_ip}")
            
            # === STEP 5: SUCCESS HANDLING ===
            if is_success:
                self._clear_tracking(client_ip)
                return
            
            # === STEP 6: FAILED ATTEMPT TRACKING ===
            self.ip_attempts[client_ip].append(now)
            self._cleanup_old_attempts(client_ip, now)
            
            attempt_count = len(self.ip_attempts[client_ip])
            
            # === STEP 7: AUTO-BLOCK ON HIGH THREAT SCORE ===
            # Block ngay lập tức nếu hành vi quá đáng ngờ (kể cả khi chưa hết số lần thử)
            if threat_score >= self.SUSPICIOUS_SCORE_THRESHOLD:
                logger.critical(f"🚨 AUTO-BLOCK: {client_ip} (threat_score={threat_score})")
                await self.block_ip(db, client_ip, reason=f"Auto-blocked: threat_score={threat_score}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Suspicious activity detected. Your IP has been blocked."
                )
            
            if attempt_count > 1:
                 logger.warning(f"Failed login: IP={client_ip}, user={username}, "
                              f"attempts={attempt_count}/{self.MAX_ATTEMPTS}, "
                              f"threat={threat_score:.1f}")
            
            # === STEP 8: APPLY COUNT-BASED BLOCKS ===
            if attempt_count >= self.PERMANENT_THRESHOLD:
                await self.block_ip(db, client_ip, reason="Exceeded permanent threshold")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                  detail="Permanent ban")
            
            if attempt_count >= self.MAX_ATTEMPTS:
                await self._apply_temporary_block(db, client_ip, now)
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                  detail=f"Blocked for {self._get_block_duration(client_ip)/60:.0f} min")

    # ==========================================================================
    # === 🆕 GLOBAL ATTACK DETECTION                                         ===
    # ==========================================================================
    
    def _check_global_attack_pattern(self, now: float):
        """
        Phát hiện tấn công DDoS bằng cách theo dõi tổng số requests toàn hệ thống
        """
        self.global_request_rate.append(now)
        
        # Đếm số requests trong 5 giây gần nhất
        recent_requests = [t for t in self.global_request_rate if now - t < 5]
        
        if len(recent_requests) > self.GLOBAL_RATE_THRESHOLD:
            if not self.attack_mode:
                self.attack_mode = True
                self.attack_mode_until = now + self.ATTACK_MODE_DURATION
                logger.critical(
                    f"🚨🚨🚨 DDOS ATTACK DETECTED! {len(recent_requests)} requests/5s. "
                    f"Entering ATTACK MODE for {self.ATTACK_MODE_DURATION}s"
                )
        
        # Thoát attack mode nếu hết thời gian
        if self.attack_mode and now > self.attack_mode_until:
            self.attack_mode = False
            logger.info("✓ Exiting ATTACK MODE - Threat level normalized")

    # ==========================================================================
    # === 🆕 ADVANCED THREAT ANALYSIS                                        ===
    # ==========================================================================
    
    async def _advanced_threat_analysis(self, ip: str, fingerprint: str, 
                                       request: Request, now: float, 
                                       settings: dict) -> int:
        """
        🎯 Multi-dimensional threat scoring:
        1. VPN Rotation Detection (same fingerprint, many IPs)
        2. Bot Pattern Detection (User-Agent analysis)
        3. Timing Analysis (too fast requests)
        4. Burst Detection (spike in requests)
        5. Behavioral Anomalies
        """
        score = 0
        
        # === 1. 🆕 VPN ROTATION DETECTION (CRITICAL) ===
        if settings.get('vpn_detection_enabled', True):
            # Track fingerprint → IPs mapping
            self.fingerprint_to_ips[fingerprint].add(ip)
            unique_ips_for_fingerprint = len(self.fingerprint_to_ips[fingerprint])
            
            # CRITICAL: Cùng fingerprint từ nhiều IPs khác nhau = VPN rotation
            if unique_ips_for_fingerprint > self.MAX_IPS_PER_FINGERPRINT:
                score += 50  # Very high score
                logger.critical(
                    f"🚨 VPN ROTATION DETECTED! Fingerprint {fingerprint[:8]} "
                    f"seen from {unique_ips_for_fingerprint} different IPs"
                )
            
            # Track IP → fingerprints (multiple browsers/devices)
            self.ip_fingerprints[ip].add(fingerprint)
            if len(self.ip_fingerprints[ip]) > self.MAX_FINGERPRINTS_PER_IP:
                score += 25
                logger.warning(f"⚠️ IP {ip} using {len(self.ip_fingerprints[ip])} fingerprints")
        
        if settings.get('pattern_analysis_enabled', True):
            # === 2. 🆕 BOT USER-AGENT DETECTION ===
            ua = request.headers.get("User-Agent", "").lower()
            bot_keywords = ['bot', 'crawler', 'spider', 'curl', 'wget', 'python', 'java']
            if any(keyword in ua for keyword in bot_keywords):
                score += 20
                logger.warning(f"⚠️ Bot User-Agent detected from {ip}: {ua[:50]}")
            
            # === 3. REQUEST TIMING ANALYSIS ===
            intervals = self.request_intervals[ip]
            if len(intervals) > 0:
                interval = now - intervals[-1]
                if interval < self.MIN_REQUEST_INTERVAL:
                    score += 20
                    logger.warning(f"⚠️ IP {ip} too fast: {interval:.3f}s")
            intervals.append(now)
            
            # === 4. BURST DETECTION ===
            recent = [t for t in intervals if now - t < 5]
            if len(recent) > 10:
                score += 25
                logger.warning(f"⚠️ IP {ip} burst: {len(recent)} req/5s")
            
            # === 5. PERSISTENT SUSPICIOUS BEHAVIOR ===
            score += min(self.suspicious_patterns[ip], 25)
        
        self.suspicious_patterns[ip] = score
        # Cập nhật metadata cho VPN detection
        self.fingerprint_history[fingerprint]["ips"].add(ip)
        
        # 🆕 Cập nhật Traffic History (cho biểu đồ)
        current_tick = int(now)
        if current_tick > self.last_tick:
            # Điền số 0 cho các giây không có request (nếu server idle)
            for _ in range(current_tick - self.last_tick - 1):
                self.traffic_history.append(0)
            self.traffic_history.append(1)
            self.last_tick = current_tick
        else:
            if not self.traffic_history:
                self.traffic_history.append(1)
            else:
                self.traffic_history[-1] += 1
                
        return score # Trả về score như cũ

    # ==========================================================================
    # === FINGERPRINTING                                                     ===
    # ==========================================================================
    
    def _generate_fingerprint(self, request: Request) -> str:
        ua = request.headers.get("User-Agent", "")
        lang = request.headers.get("Accept-Language", "")
        encoding = request.headers.get("Accept-Encoding", "")
        accept = request.headers.get("Accept", "")
        
        # Tạo hash
        raw = f"{ua}|{lang}|{encoding}|{accept}".lower()
        fp = hashlib.md5(raw.encode()).hexdigest()[:16]
        
        # 🆕 Lưu metadata cho fingerprint để hiển thị trên UI
        if fp not in self.fingerprint_history:
            self.fingerprint_history[fp]["first_seen"] = int(time.time())
            self.fingerprint_history[fp]["ua"] = ua[:50] + "..." if len(ua) > 50 else ua
        
        return fp
    
    # ==========================================================================
    # === ADAPTIVE BLOCKING                                                  ===
    # ==========================================================================
    
    def _get_block_duration(self, ip: str) -> int:
        """Escalating block duration"""
        level = self.block_escalation[ip]
        durations = [
            self.BLOCK_DURATION_SECONDS,
            self.BLOCK_DURATION_SECONDS * 3,
            self.BLOCK_DURATION_SECONDS * 12,
            self.BLOCK_DURATION_SECONDS * 48
        ]
        return durations[min(level, len(durations) - 1)]

    # ==========================================================================
    # === DATABASE OPERATIONS                                                ===
    # ==========================================================================
    
    async def _check_database_blocks(self, db: AsyncSession, ip: str, now: float):
        result = await db.execute(
            select(models.IpSecurityLog).filter(models.IpSecurityLog.ip_address == ip)
        )
        block = result.scalars().first()
        
        if not block:
            return None
        
        if block.block_type == 'permanent':
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                              detail="Permanent ban")
        
        if block.block_type == 'temporary':
            if now < block.blocked_until:
                remaining = int(block.blocked_until - now)
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                  detail=f"Blocked. Try again in {remaining}s")
            else:
                await db.delete(block)
                await db.commit()
                return None
        
        return block
    
    async def _apply_temporary_block(self, db: AsyncSession, ip: str, now: float):
        self.block_escalation[ip] += 1
        duration = self._get_block_duration(ip)
        
        await db.execute(delete(models.IpSecurityLog).where(models.IpSecurityLog.ip_address == ip))
        
        new_block = models.IpSecurityLog(
            ip_address=ip,
            block_type='temporary',
            blocked_until=int(now + duration),
            created_at=int(now),
            reason=f"Rate limit (level {self.block_escalation[ip]})"
        )
        db.add(new_block)
        await db.commit()
        self._stats_cache.clear()
        self.record_block_event(ip, new_block.reason, 'login', blocked_until=new_block.blocked_until)

    async def block_ip(self, db: AsyncSession, ip: str, reason: str = "Manual"):
        await db.execute(delete(models.IpSecurityLog).where(models.IpSecurityLog.ip_address == ip))
        
        new_block = models.IpSecurityLog(
            ip_address=ip,
            block_type='permanent',
            blocked_until=None,
            created_at=int(time.time()),
            reason=reason
        )
        db.add(new_block)
        await db.commit()
        self._stats_cache.clear()
        from .redis_client import redis_client
        await redis_client.ban_ip(ip)
        self.record_block_event(ip, reason, 'login', permanent=True)

    async def unblock_ip(self, db: AsyncSession, ip: str) -> bool:
        result = await db.execute(
            delete(models.IpSecurityLog).where(models.IpSecurityLog.ip_address == ip)
        )
        await db.commit()
        self._clear_tracking(ip)
        self.block_escalation[ip] = 0
        self.runtime_blocks.pop(ip, None)
        self._stats_cache.clear()
        return result.rowcount > 0

    # ==========================================================================
    # === UTILITY & STATS                                                    ===
    # ==========================================================================
    
    def _get_client_ip(self, request: Request) -> str:
        remote = request.client.host if request.client else "unknown"
        try:
            remote_ip = ipaddress.ip_address(remote)
        except ValueError:
            return remote

        trusted = []
        trusted_proxy_ips = os.getenv("TRUSTED_PROXY_IPS", settings.TRUSTED_PROXY_IPS)
        for item in trusted_proxy_ips.split(","):
            try:
                trusted.append(ipaddress.ip_network(item.strip(), strict=False))
            except ValueError:
                continue
        if not any(remote_ip in network for network in trusted):
            return remote

        forwarded = request.headers.get("X-Forwarded-For", "")
        chain = []
        for raw_ip in forwarded.split(","):
            try:
                chain.append(ipaddress.ip_address(raw_ip.strip()))
            except ValueError:
                continue
        for candidate in reversed([*chain, remote_ip]):
            if any(candidate in network for network in trusted):
                continue
            return str(candidate)
        return remote
    
    def _cleanup_old_attempts(self, ip: str, now: float):
        cutoff = now - self.WINDOW_SECONDS
        while self.ip_attempts[ip] and self.ip_attempts[ip][0] < cutoff:
            self.ip_attempts[ip].popleft()
    
    def _clear_tracking(self, ip: str):
        for d in [self.ip_attempts, self.ip_fingerprints, self.request_intervals, self.suspicious_patterns]:
            if ip in d:
                del d[ip]
    
    async def get_stats(self, db: AsyncSession) -> dict:
        """
        Lấy thống kê bảo mật với caching và error handling
        """
        CACHE_TTL = 5.0
        now = time.time()
        
        # Không dùng lock ở đây vì có thể gây deadlock
        cache_key = "get_stats_v1"
        
        # Kiểm tra cache
        if hasattr(self, "_stats_cache") and cache_key in self._stats_cache:
            cached = self._stats_cache[cache_key]
            age = now - cached.get("ts", 0)
            
            # Chỉ dùng cache nếu còn mới VÀ attack_mode không đổi
            if (age < CACHE_TTL and 
                cached.get("attack_mode") == self.attack_mode):
                result = dict(cached["value"])
                result["_cache_age_seconds"] = round(age, 2)
                return result
        
        # === BUILD FRESH STATS ===
        try:
            # 1. Database queries
            permanent_result = await db.execute(
                select(models.IpSecurityLog).filter(
                    models.IpSecurityLog.block_type == 'permanent'
                )
            )
            permanent_blocks = permanent_result.scalars().all()

            temporary_result = await db.execute(
                select(models.IpSecurityLog).filter(
                    models.IpSecurityLog.block_type == 'temporary',
                    models.IpSecurityLog.blocked_until > now
                )
            )
            temporary_blocks = temporary_result.scalars().all()

            # 2. Suspicious IPs
            suspicious_ips = [
                {"ip": ip, "score": score}
                for ip, score in sorted(
                    self.suspicious_patterns.items(), 
                    key=lambda x: x[1], 
                    reverse=True
                )
                if score > 20
            ][:10]

            # 3. VPN Intelligence
            vpn_suspects_detailed = []
            for fp, data in self.fingerprint_history.items():
                ips_set = set(data.get("ips", []))
                unique_ips = len(ips_set)
                
                if unique_ips > 2:
                    ua = data.get("ua", "N/A")[:100]  # Limit UA length
                    first_seen = data.get("first_seen", 0)
                    recent_count = int(data.get("recent_count", 0))
                    
                    risk_score = unique_ips * 2 + min(recent_count, 50)
                    
                    if unique_ips > 10 or risk_score > 30:
                        risk_level = "CRITICAL"
                    elif unique_ips > 4 or risk_score > 15:
                        risk_level = "HIGH"
                    else:
                        risk_level = "MEDIUM"
                    
                    vpn_suspects_detailed.append({
                        "fingerprint": fp[:8],
                        "ip_count": unique_ips,
                        "user_agent": ua,
                        "first_seen": first_seen,
                        "recent_count": recent_count,
                        "risk_score": risk_score,
                        "risk_level": risk_level,
                        "suggested_mitigation": "challenge" if risk_level in ("HIGH", "CRITICAL") else "monitor"
                    })

            vpn_suspects_detailed.sort(key=lambda x: x["risk_score"], reverse=True)

            # 4. Traffic History (ensure 60 points)
            traffic_data = list(self.traffic_history)
            while len(traffic_data) < 60:
                traffic_data.insert(0, 0)
            traffic_data = traffic_data[-60:]  # Keep only last 60

            # 5. Build result
            result = {
                "blocked_ips_count": len(temporary_blocks),
                "permanent_blacklist_count": len(permanent_blocks),
                "blacklisted_ips": [
                    {
                        "ip": b.ip_address,
                        "reason": getattr(b, "reason", "N/A"),
                        "blocked_at": b.created_at
                    }
                    for b in permanent_blocks  # Limit to 50
                ],
                "temporary_blocked_ips": [
                    {
                        "ip": b.ip_address,
                        "blocked_until": b.blocked_until,
                        "remaining_seconds": max(0, int(b.blocked_until - now)),
                        "reason": getattr(b, "reason", "N/A")
                    }
                    for b in temporary_blocks  # Limit to 50
                ],
                "total_ips_tracked": len(self.ip_attempts),
                "suspicious_ips": suspicious_ips,
                "vpn_suspects": vpn_suspects_detailed[:20],
                "attack_mode": self.attack_mode,
                "traffic_history": traffic_data,
                "global_request_rate": len(self.global_request_rate),
                "_generated_at": now,
                "_cache_age_seconds": 0,
                "blocked_usernames_count": 0
            }

            # Cache the result
            if not hasattr(self, "_stats_cache"):
                self._stats_cache = {}
            
            self._stats_cache[cache_key] = {
                "value": result,
                "ts": now,
                "attack_mode": self.attack_mode
            }

            return result

        except Exception as e:
            logger.exception(f"Error in get_stats: {e}", exc_info=True)
            # Return minimal fallback data
            return {
                "error": "Failed to generate stats",
                "error_message": str(e),
                "blocked_ips_count": 0,
                "permanent_blacklist_count": 0,
                "total_ips_tracked": 0,
                "attack_mode": False,
                "traffic_history": [0] * 60,
                "_generated_at": now
            }

    def cleanup_stale_data(self):
        now = time.time()
        cutoff = now - (self.WINDOW_SECONDS * 3)
        
        stale_ips = [
            ip for ip, attempts in self.ip_attempts.items()
            if not attempts or attempts[-1] < cutoff
        ]
        for ip in stale_ips:
            self._clear_tracking(ip)
        
        # Cleanup fingerprint tracking
        stale_fps = [
            fp for fp, ips in self.fingerprint_to_ips.items()
            if len(ips) == 0
        ]
        for fp in stale_fps:
            del self.fingerprint_to_ips[fp]


# === SINGLETON ===
from .database import settings

rate_limiter = AdvancedRateLimiter(
    max_attempts=settings.RATE_LIMIT_MAX_ATTEMPTS,
    window_minutes=settings.RATE_LIMIT_WINDOW_MINUTES,
    block_duration_minutes=settings.RATE_LIMIT_BLOCK_MINUTES,
    permanent_block_threshold=settings.RATE_LIMIT_PERMANENT_THRESHOLD
)
