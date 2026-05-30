import os

BACKEND_ENV_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\.env'
BACKEND_MAIN_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'

# 1. Update .env
with open(BACKEND_ENV_FILE, 'r', encoding='utf-8') as f:
    env_content = f.read()

if "AGENT_SECRET_SALT" not in env_content:
    env_content += "\n# Authentication salt for Agent <-> Backend\nAGENT_SECRET_SALT=\"AITOGY_GNSS_2026\"\nENFORCE_AGENT_AUTH=\"False\"\n"
    with open(BACKEND_ENV_FILE, 'w', encoding='utf-8') as f:
        f.write(env_content)

# 2. Update main.py
with open(BACKEND_MAIN_FILE, 'r', encoding='utf-8') as f:
    main_content = f.read()

target = """        token = websocket.query_params.get("token") or websocket.headers.get("Authorization")
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

replacement = """        token = websocket.query_params.get("token") or websocket.headers.get("Authorization")
        
        enforce_auth = str(os.getenv("ENFORCE_AGENT_AUTH", "False")).lower() in ("true", "1", "yes")
        
        if not token:
            if enforce_auth:
                logger.warning(f"Device {serial} rejected: No token provided (ENFORCE_AGENT_AUTH=True)")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            else:
                logger.info(f"Device {serial} connected without token (Legacy Mode).")
        else:
            # Hardware-derived Auto Hash Authentication
            SALT_KEY = os.getenv("AGENT_SECRET_SALT", "AITOGY_GNSS_2026")
            import hashlib
            expected_token = hashlib.sha256(f"{serial}{SALT_KEY}".encode()).hexdigest()
            
            if token != expected_token:
                logger.warning(f"Device {serial} provided invalid auto-hash token.")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return"""

if "enforce_auth = str(os.getenv" not in main_content:
    main_content = main_content.replace(target, replacement)
    with open(BACKEND_MAIN_FILE, 'w', encoding='utf-8') as f:
        f.write(main_content)

print("Backend auth fallback patched!")
