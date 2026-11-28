from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .schemas import redact_config_secrets


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self.connection_contexts: dict[WebSocket, dict] = {}
        self.connection_locks: dict[WebSocket, asyncio.Lock] = {}

    async def connect(self, websocket: WebSocket, context: dict | None = None, accepted: bool = False) -> None:
        if not accepted:
            await websocket.accept()
        self.active_connections.append(websocket)
        self.connection_contexts[websocket] = context or {}
        self.connection_locks[websocket] = asyncio.Lock()

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.connection_contexts.pop(websocket, None)
        self.connection_locks.pop(websocket, None)

    def _can_receive(self, websocket: WebSocket, message: dict) -> bool:
        context = self.connection_contexts.get(websocket, {})
        if context.get("role") == "admin":
            return True

        message_type = message.get("type")
        if message_type in {"visitor_update", "activity_log_event"}:
            return False

        if message_type == "user_assignment_updated":
            data = message.get("data") or {}
            if data.get("user_id") != context.get("user_id"):
                return False
            # The browser reconnects after this event. Stop using its old scope now.
            context["allowed_serials"] = set()
            return True

        if message_type in {"status_update", "nmea_update", "rtcm_signal_update", "device_deleted"}:
            allowed_serials = context.get("allowed_serials")
            if not allowed_serials:
                return False
            data = message.get("data") or {}
            serial = message.get("serial") or data.get("serial")
            normalized_serial = str(serial or "").strip().upper()
            return normalized_serial in allowed_serials

        return True

    def _viewer_safe_message(self, connection: WebSocket, message: dict) -> dict:
        context = self.connection_contexts.get(connection, {})
        if context.get("role") != "viewer" or message.get("type") != "status_update":
            return message
        data = message.get("data")
        if not isinstance(data, dict):
            return message
        safe_data = dict(data)
        for key in ("base_config", "service_config", "system_info"):
            if key in safe_data:
                safe_data[key] = redact_config_secrets(safe_data[key])
        return {**message, "data": safe_data}

    async def _send_one(self, connection: WebSocket, message: dict) -> None:
        lock = self.connection_locks.get(connection)
        if lock is None:
            return
        try:
            async with lock:
                if connection.client_state != WebSocketState.CONNECTED:
                    self.disconnect(connection)
                    return
                if not self._can_receive(connection, message):
                    return
                outbound = self._viewer_safe_message(connection, message)
                await asyncio.wait_for(connection.send_json(outbound), timeout=2.0)
        except (WebSocketDisconnect, RuntimeError, OSError):
            self.disconnect(connection)
        except Exception as exc:
            logging.warning("WebSocket broadcast error (ignored): %s", exc)
            self.disconnect(connection)

    async def broadcast(self, message: dict) -> None:
        # Writes are serialized per socket while independent clients are served
        # concurrently, preventing concurrent-send disconnects and slow-client stalls.
        connections_to_send = self.active_connections[:]
        if not connections_to_send:
            return
        await asyncio.gather(
            *(self._send_one(connection, message) for connection in connections_to_send),
            return_exceptions=True,
        )


manager = ConnectionManager()


class VisitorManager:
    def __init__(self, *, max_connections: int = 2000, max_per_ip: int = 20) -> None:
        self.active_ips = set()
        self.active_sockets = []
        self.max_connections = max_connections
        self.max_per_ip = max_per_ip

    async def connect(self, websocket: WebSocket, client_ip: str) -> bool:
        if len(self.active_sockets) >= self.max_connections or sum(
            1 for socket in self.active_sockets if socket.client and socket.client.host == client_ip
        ) >= self.max_per_ip:
            await websocket.close(code=1013, reason="Visitor connection limit reached")
            return False
        await websocket.accept()
        self.active_sockets.append(websocket)
        self.active_ips.add(client_ip)
        return True

    def disconnect(self, websocket: WebSocket, client_ip: str) -> None:
        if websocket in self.active_sockets:
            self.active_sockets.remove(websocket)

        ip_still_active = any(
            socket.client and socket.client.host == client_ip
            for socket in self.active_sockets
        )
        if not ip_still_active:
            self.active_ips.discard(client_ip)

    def get_count(self) -> int:
        return len(self.active_ips)


visitor_manager = VisitorManager()
