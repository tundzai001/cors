import sys
import os

MAIN_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'
DEVICES_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\routers\devices.py'

def patch_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Import redis_client if not in file
    if "from .redis_client import redis_client" not in content and "from ..redis_client import redis_client" not in content:
        if "routers" in filepath:
            content = "from ..redis_client import redis_client\n" + content
        else:
            if "from .redis_client import redis_client" not in content:
                idx = content.find("from . import crud")
                if idx != -1:
                    content = content[:idx] + "from .redis_client import redis_client\n" + content[idx:]
    
    # 1. global_caster_data_cache.get(key)
    content = content.replace("global_caster_data_cache.get(", "await redis_client.get_caster_cache(")
    
    # 2. global_caster_data_cache.values() -> (await redis_client.get_all_caster_cache()).values()
    content = content.replace("global_caster_data_cache.values()", "(await redis_client.get_all_caster_cache()).values()")
    
    # 3. Setting values (a bit tricky)
    # We will just do a rough replace or rely on specific patterns.
    # In main.py:
    # global_caster_data_cache[key] = { ... }
    # Let's find these instances and manually replace them, or just use a generic regex approach.
    import re
    # global_caster_data_cache[key] = value -> await redis_client.set_caster_cache(key, value)
    
    # Let's actually look for specific lines in main.py since we know them:
    # global_caster_data_cache[key] = {"users": [], "clients": [], "host": host, "port": port}
    # global_caster_data_cache[key]["error"] = str(e)
    # global_caster_data_cache[key]["last_update"] = int(time.time())
    
    # Also "if key not in global_caster_data_cache:"
    # -> "if not await redis_client.get_caster_cache(key):"
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
        
patch_file(MAIN_FILE)
patch_file(DEVICES_FILE)

# Manual fix for main.py Caster Cache update logic
with open(MAIN_FILE, 'r', encoding='utf-8') as f:
    main_content = f.read()
    
# Replace the old dict declaration
main_content = main_content.replace("global_caster_data_cache = {}\n", "")

update_block_target = """        if result["status"] == "error":
            logger.warning(f"Failed to fetch caster data from {host}:{port} - {result.get('error')}")
            
            # Lưu trạng thái lỗi vào cache
            key = f"{host}:{port}"
            if key not in global_caster_data_cache:
                global_caster_data_cache[key] = {"users": [], "clients": [], "host": host, "port": port}
            global_caster_data_cache[key]["error"] = str(e)
            global_caster_data_cache[key]["last_update"] = int(time.time())"""
            
update_block_replacement = """        if result["status"] == "error":
            logger.warning(f"Failed to fetch caster data from {host}:{port} - {result.get('error')}")
            
            # Lưu trạng thái lỗi vào cache
            key = f"{host}:{port}"
            caster_cache = await redis_client.get_caster_cache(key)
            if not caster_cache:
                caster_cache = {"users": [], "clients": [], "host": host, "port": port}
            caster_cache["error"] = result.get("error", "Unknown error")
            caster_cache["last_update"] = int(time.time())
            await redis_client.set_caster_cache(key, caster_cache)"""
            
main_content = main_content.replace(update_block_target, update_block_replacement)

success_block_target = """        else:
            # Cập nhật cache thành công
            key = f"{host}:{port}"
            global_caster_data_cache[key] = {
                "users": result["users"],
                "clients": result["clients"],
                "host": host,
                "port": port,
                "error": None,
                "last_update": int(time.time())
            }"""
            
success_block_replacement = """        else:
            # Cập nhật cache thành công
            key = f"{host}:{port}"
            await redis_client.set_caster_cache(key, {
                "users": result["users"],
                "clients": result["clients"],
                "host": host,
                "port": port,
                "error": None,
                "last_update": int(time.time())
            })"""

main_content = main_content.replace(success_block_target, success_block_replacement)

with open(MAIN_FILE, 'w', encoding='utf-8') as f:
    f.write(main_content)
print("Caster Cache Patched")
