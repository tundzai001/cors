import sys
import os

AGENT_FILE = r'c:\Users\len\Desktop\other_projects\cors\geodetic\agent_universal.py'
MAIN_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'

# 1. Update Agent
with open(AGENT_FILE, 'r', encoding='utf-8') as f:
    agent_content = f.read()

if "import hashlib" not in agent_content:
    idx = agent_content.find("import json")
    if idx != -1:
        agent_content = agent_content[:idx] + "import hashlib\n" + agent_content[idx:]

agent_target = """async def websocket_task(agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client):
    global active_websocket_connection
    ws_uri = f"ws://{BACKEND_HOST}:8000/ws/pi/{MACHINE_SERIAL}"
    
    while True:"""

agent_replacement = """async def websocket_task(agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client):
    global active_websocket_connection
    
    # Auto-Hash Authentication Token
    SALT_KEY = "AITOGY_GNSS_2026"
    import hashlib
    auth_token = hashlib.sha256(f"{MACHINE_SERIAL}{SALT_KEY}".encode()).hexdigest()
    
    ws_uri = f"ws://{BACKEND_HOST}:8000/ws/pi/{MACHINE_SERIAL}?token={auth_token}"
    
    while True:"""

if "auth_token = hashlib.sha256" not in agent_content:
    agent_content = agent_content.replace(agent_target, agent_replacement)
    with open(AGENT_FILE, 'w', encoding='utf-8') as f:
        f.write(agent_content)

# 2. Update Backend
with open(MAIN_FILE, 'r', encoding='utf-8') as f:
    main_content = f.read()
    
if "import hashlib" not in main_content:
    idx = main_content.find("import json")
    if idx != -1:
        main_content = main_content[:idx] + "import hashlib\n" + main_content[idx:]

backend_target = """        token = websocket.query_params.get("token") or websocket.headers.get("Authorization")
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
        device = crud.get_device_by_serial(db, serial=serial)
        if device and getattr(device, "device_token", None):
            if token != device.device_token:
                logger.warning(f"Device {serial} provided invalid token.")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return"""

backend_replacement = """        token = websocket.query_params.get("token") or websocket.headers.get("Authorization")
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
        # Hardware-derived Auto Hash Authentication
        SALT_KEY = "AITOGY_GNSS_2026"
        import hashlib
        expected_token = hashlib.sha256(f"{serial}{SALT_KEY}".encode()).hexdigest()
        
        if token != expected_token:
            logger.warning(f"Device {serial} provided invalid auto-hash token.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return"""

if "expected_token = hashlib.sha256" not in main_content:
    main_content = main_content.replace(backend_target, backend_replacement)
    # Also handle the patch.py case if there are multiple occurrences or variations
    
    with open(MAIN_FILE, 'w', encoding='utf-8') as f:
        f.write(main_content)

print("Auth Patched!")
