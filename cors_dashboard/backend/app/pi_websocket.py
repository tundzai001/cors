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
        previous = self.active_connections.get(serial)
        self.active_connections[serial] = websocket
        if previous and previous is not websocket:
            try:
                await previous.close(code=1012, reason="superseded by newer connection")
            except Exception:
                pass
        # logging.info(f"Pi '{serial}' connected via WebSocket.")

    def disconnect(self, serial: str, websocket: WebSocket | None = None):
        current = self.active_connections.get(serial)
        if current is not None and (websocket is None or current is websocket):
            del self.active_connections[serial]
            # logging.info(f"Pi '{serial}' disconnected from WebSocket.")

    def has_replacement(self, serial: str, websocket: WebSocket) -> bool:
        current = self.active_connections.get(serial)
        return current is not None and current is not websocket

    async def send_personal_message(self, serial: str, message: dict) -> bool:
        if serial in self.active_connections:
            websocket = self.active_connections[serial]
            try:
                if websocket.client_state != WebSocketState.CONNECTED:
                    self.disconnect(serial, websocket)
                    return False

                await websocket.send_json(message)
                return True
            except (WebSocketDisconnect, RuntimeError, OSError):
                # Lỗi mạng -> Xóa kết nối im lặng
                self.disconnect(serial, websocket)
                return False
            except Exception as e:
                logging.warning(f"Send to Pi '{serial}' error: {e}")
                self.disconnect(serial, websocket)
                return False
        return False

pi_manager = PiConnectionManager()
