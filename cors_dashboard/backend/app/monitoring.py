# ==============================================================================
# == backend/app/monitoring.py - Production Monitoring & Metrics             ==
# ==============================================================================

import time
import psutil
import logging
from typing import Dict, Any
from datetime import datetime, timedelta
from collections import deque

logger = logging.getLogger(__name__)

class HealthMonitor:
    """Theo dõi sức khỏe hệ thống"""
    
    def __init__(self):
        self.start_time = time.time()
        self.request_count = 0
        self.error_count = 0
        self.websocket_connections = 0
        self.mqtt_reconnect_count = 0
        
        # Circular buffers để lưu metrics theo thời gian
        self.response_times = deque(maxlen=1000)
        self.error_log = deque(maxlen=100)
    
    def record_request(self, duration_ms: float):
        """Ghi nhận một request"""
        self.request_count += 1
        self.response_times.append(duration_ms)
    
    def record_error(self, error_type: str, details: str):
        """Ghi nhận một lỗi"""
        self.error_count += 1
        self.error_log.append({
            'timestamp': datetime.now().isoformat(),
            'type': error_type,
            'details': details
        })
    
    def get_health_status(self) -> Dict[str, Any]:
        """Trả về trạng thái sức khỏe chi tiết"""
        uptime = time.time() - self.start_time
        
        # CPU & Memory
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Response time stats
        avg_response_time = 0
        p95_response_time = 0
        if self.response_times:
            avg_response_time = sum(self.response_times) / len(self.response_times)
            sorted_times = sorted(self.response_times)
            p95_index = int(len(sorted_times) * 0.95)
            p95_response_time = sorted_times[p95_index] if p95_index < len(sorted_times) else 0
        
        # Error rate
        error_rate = (self.error_count / self.request_count * 100) if self.request_count > 0 else 0
        
        return {
            'status': 'healthy' if error_rate < 5 and cpu_percent < 80 else 'degraded',
            'uptime_seconds': int(uptime),
            'uptime_human': str(timedelta(seconds=int(uptime))),
            'system': {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_available_mb': memory.available // (1024 * 1024),
                'disk_percent': disk.percent,
                'disk_free_gb': disk.free // (1024 ** 3)
            },
            'application': {
                'total_requests': self.request_count,
                'total_errors': self.error_count,
                'error_rate_percent': round(error_rate, 2),
                'avg_response_time_ms': round(avg_response_time, 2),
                'p95_response_time_ms': round(p95_response_time, 2),
                'websocket_connections': self.websocket_connections,
                'mqtt_reconnect_count': self.mqtt_reconnect_count
            },
            'timestamp': datetime.now().isoformat()
        }
    
    def get_recent_errors(self, limit: int = 10) -> list:
        """Lấy danh sách lỗi gần đây"""
        return list(self.error_log)[-limit:]

# Singleton instance
health_monitor = HealthMonitor()


class CircuitBreaker:
    """Circuit Breaker pattern để xử lý lỗi liên tục"""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
    
    def call(self, func, *args, **kwargs):
        """Thực thi hàm với circuit breaker"""
        if self.state == 'OPEN':
            if time.time() - self.last_failure_time > self.recovery_timeout:
                logger.info("Circuit breaker entering HALF_OPEN state")
                self.state = 'HALF_OPEN'
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = func(*args, **kwargs)
            
            if self.state == 'HALF_OPEN':
                logger.info("Circuit breaker recovering to CLOSED state")
                self.state = 'CLOSED'
                self.failure_count = 0
            
            return result
        
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                logger.error(f"Circuit breaker opened after {self.failure_count} failures")
                self.state = 'OPEN'
            
            raise e


class RateLimiter:
    """Rate limiter đơn giản dựa trên IP"""
    
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, deque] = {}
    
    def is_allowed(self, identifier: str) -> bool:
        """Kiểm tra xem request có được phép không"""
        now = time.time()
        
        if identifier not in self.requests:
            self.requests[identifier] = deque()
        
        # Xóa các requests cũ
        while self.requests[identifier] and self.requests[identifier][0] < now - self.window_seconds:
            self.requests[identifier].popleft()
        
        if len(self.requests[identifier]) >= self.max_requests:
            return False
        
        self.requests[identifier].append(now)
        return True
    
    def cleanup(self):
        """Dọn dẹp dữ liệu cũ định kỳ"""
        now = time.time()
        to_remove = []
        
        for identifier, requests in self.requests.items():
            while requests and requests[0] < now - self.window_seconds:
                requests.popleft()
            
            if not requests:
                to_remove.append(identifier)
        
        for identifier in to_remove:
            del self.requests[identifier]

# Global rate limiter
global_rate_limiter = RateLimiter(max_requests=1000, window_seconds=60)