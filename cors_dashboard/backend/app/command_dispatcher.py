import json
import logging
import secrets
import time

from fastapi import HTTPException

from . import mqtt as mqtt_handler
from .mqtt import mqtt_client
from .pi_websocket import pi_manager


async def send_command_to_pi(serial: str, command: dict) -> dict:
    command = dict(command)
    issued_at = int(time.time())
    command.update({
        "command_id": str(command.get("command_id") or secrets.token_urlsafe(18)),
        "issued_at": issued_at,
        "expires_at": issued_at + 300,
    })
    command_name = command.get("command")

    if command_name == "DEPLOY_LICENSE":
        success = await pi_manager.send_personal_message(serial, command)
        if success:
            return {"status": "command_sent", "channel": "websocket", "command": command_name}

    if mqtt_client and mqtt_client.is_connected():
        topic = f"pi/devices/{serial}/command"
        message = json.dumps(command)
        try:
            mqtt_handler.publish_message(topic, message)
            return {"status": "command_sent", "channel": "mqtt", "command": command_name}
        except Exception as exc:
            logging.warning("MQTT command publish failed for '%s' (%s). Trying WebSocket.", serial, type(exc).__name__)

    logging.warning("Trying WebSocket command delivery for '%s'.", serial)
    success = await pi_manager.send_personal_message(serial, command)
    if success:
        return {"status": "command_sent", "channel": "websocket", "command": command_name}

    raise HTTPException(
        status_code=503,
        detail=f"Cannot send command to '{serial}'. Both MQTT and WebSocket are unavailable.",
    )
