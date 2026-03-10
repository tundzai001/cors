# backend/app/pi_websocket.py
import logging
from typing import Dict
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

class PiConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, serial: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[serial] = websocket
        # logging.info(f"Pi '{serial}' connected via WebSocket.")

    def disconnect(self, serial: str):
        if serial in self.active_connections:
            del self.active_connections[serial]
            # logging.info(f"Pi '{serial}' disconnected from WebSocket.")

    async def send_personal_message(self, serial: str, message: dict) -> bool:
        if serial in self.active_connections:
            websocket = self.active_connections[serial]
            try:
                if websocket.client_state != WebSocketState.CONNECTED:
                    self.disconnect(serial)
                    return False

                await websocket.send_json(message)
                return True
            except (WebSocketDisconnect, RuntimeError, OSError):
                # Lỗi mạng -> Xóa kết nối im lặng
                self.disconnect(serial)
                return False
            except Exception as e:
                logging.warning(f"Send to Pi '{serial}' error: {e}")
                self.disconnect(serial)
                return False
        return False

pi_manager = PiConnectionManager()