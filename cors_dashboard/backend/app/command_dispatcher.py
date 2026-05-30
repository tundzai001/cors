import json
import logging

from fastapi import HTTPException

from . import mqtt as mqtt_handler
from .mqtt import mqtt_client
from .pi_websocket import pi_manager


async def send_command_to_pi(serial: str, command: dict) -> dict:
    if mqtt_client and mqtt_client.is_connected():
        topic = f"pi/devices/{serial}/command"
        message = json.dumps(command)
        mqtt_handler.publish_message(topic, message)
        return {"status": "command_sent", "channel": "mqtt", "command": command.get("command")}

    logging.warning("MQTT down. Trying WebSocket for '%s'.", serial)
    success = await pi_manager.send_personal_message(serial, command)
    if success:
        return {"status": "command_sent", "channel": "websocket", "command": command.get("command")}

    raise HTTPException(
        status_code=503,
        detail=f"Cannot send command to '{serial}'. Both MQTT and WebSocket are unavailable.",
    )
