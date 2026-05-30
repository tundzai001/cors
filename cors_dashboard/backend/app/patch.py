import sys

file_path = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

target = """@app.websocket("/ws/pi/{serial}")
async def pi_websocket_endpoint(websocket: WebSocket, serial: str):
    await pi_manager.connect(serial, websocket)"""

replacement = """@app.websocket("/ws/pi/{serial}")
async def pi_websocket_endpoint(websocket: WebSocket, serial: str, token: str = Query(None)):
    # --- Token Auth ---
    async with device_session_scope() as db:
        device = await crud.get_device_by_serial(db, serial)
        if device and getattr(device, "device_token", None):
            if token != device.device_token:
                logger.warning(f"Pi '{serial}' connection rejected: Invalid token.")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

    await pi_manager.connect(serial, websocket)"""

if target in content:
    content = content.replace(target, replacement)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched successfully")
else:
    print("Target not found. Looking for alternatives...")
    # Maybe line endings are different
    target_crlf = target.replace('\n', '\r\n')
    if target_crlf in content:
        content = content.replace(target_crlf, replacement.replace('\n', '\r\n'))
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("Patched successfully (CRLF)")
    else:
        print("Could not find the target block.")
