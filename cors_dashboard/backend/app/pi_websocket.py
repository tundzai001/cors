# backend/app/pi_websocket.py
import logging
from typing import Dict
from fastapi import WebSocket

class PiConnectionManager:
    def __init__(self):
        # Dùng một dictionary để map serial number -> WebSocket object
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, serial: str, websocket: WebSocket):
        """Chấp nhận và lưu kết nối từ một Pi."""
        await websocket.accept()
        self.active_connections[serial] = websocket
        logging.info(f"Pi '{serial}' connected via WebSocket.")

    def disconnect(self, serial: str):
        """Xóa kết nối khi Pi ngắt kết nối."""
        if serial in self.active_connections:
            del self.active_connections[serial]
            logging.info(f"Pi '{serial}' disconnected from WebSocket.")

    async def send_personal_message(self, serial: str, message: dict) -> bool:
        """Gửi lệnh đến một Pi cụ thể qua WebSocket."""
        if serial in self.active_connections:
            websocket = self.active_connections[serial]
            try:
                await websocket.send_json(message)
                logging.info(f"Sent command to Pi '{serial}' via WebSocket fallback.")
                return True
            except Exception as e:
                logging.warning(f"Could not send to Pi '{serial}' via WebSocket: {e}")
                # Có thể kết nối đã chết, xóa nó đi
                self.disconnect(serial)
                return False
        return False

# Tạo một instance để sử dụng trong toàn bộ ứng dụng
pi_manager = PiConnectionManager()