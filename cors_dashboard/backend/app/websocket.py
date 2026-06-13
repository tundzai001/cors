# backend/app/websocket.py
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState 
import logging
import asyncio

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.connection_contexts: dict[WebSocket, dict] = {}

    async def connect(self, websocket: WebSocket, context: dict | None = None, accepted: bool = False):
        if not accepted:
            await websocket.accept()
        self.active_connections.append(websocket)
        self.connection_contexts[websocket] = context or {}

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.connection_contexts.pop(websocket, None)

    def _can_receive(self, websocket: WebSocket, message: dict) -> bool:
        context = self.connection_contexts.get(websocket, {})
        if context.get("role") == "admin":
            return True

        message_type = message.get("type")
        if message_type in {"visitor_update", "activity_log_event"}:
            return False

        if message_type == "user_assignment_updated":
            data = message.get("data") or {}
            return data.get("user_id") == context.get("user_id")

        if message_type in {"status_update", "nmea_update", "rtcm_signal_update", "device_deleted"}:
            allowed_serials = context.get("allowed_serials")
            if not allowed_serials:
                return False
            data = message.get("data") or {}
            serial = message.get("serial") or data.get("serial")
            return str(serial or "") in allowed_serials

        return True

    async def broadcast(self, message: dict):
        # Tạo bản copy danh sách để tránh lỗi runtime khi list thay đổi trong lúc lặp
        connections_to_send = self.active_connections[:]
        
        for connection in connections_to_send:
            try:
                # 1. Kiểm tra xem socket còn mở không trước khi gửi
                if connection.client_state != WebSocketState.CONNECTED:
                    self.disconnect(connection)
                    continue
                
                # 2. Gửi tin nhắn
                if not self._can_receive(connection, message):
                    continue
                await connection.send_json(message)
                
            except (WebSocketDisconnect, RuntimeError, OSError) as e:
                # RuntimeError: Thường là "Cannot call 'send' once a close message has been sent."
                # OSError: Lỗi Broken pipe ở tầng OS
                # Gặp lỗi này thì xóa luôn, không cần log error làm gì cho rác
                self.disconnect(connection)
            except Exception as e:
                # Các lỗi lạ khác thì mới log warning nhẹ
                logging.warning(f"WebSocket broadcast error (ignored): {e}")
                self.disconnect(connection)

manager = ConnectionManager()

class VisitorManager:
    def __init__(self):
        # Dùng Set để lưu IP duy nhất (1 người mở 10 tab chỉ tính là 1)
        self.active_ips = set()
        self.active_sockets = []

    async def connect(self, websocket: WebSocket, client_ip: str):
        await websocket.accept()
        self.active_sockets.append(websocket)
        self.active_ips.add(client_ip)

    def disconnect(self, websocket: WebSocket, client_ip: str):
        if websocket in self.active_sockets:
            self.active_sockets.remove(websocket)
        
        # Kiểm tra xem IP này còn socket nào khác không
        ip_still_active = any(
            s.client.host == client_ip for s in self.active_sockets
        )
        
        if not ip_still_active and client_ip in self.active_ips:
            self.active_ips.remove(client_ip)

    def get_count(self):
        return len(self.active_ips)

visitor_manager = VisitorManager()
