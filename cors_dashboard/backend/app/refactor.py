import sys
import os

MAIN_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'
USERS_ROUTER = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\routers\users.py'

with open(MAIN_FILE, 'r', encoding='utf-8') as f:
    main_content = f.read()

# 1. Update log_system_event calls
main_content = main_content.replace('await log_system_event(', 'await crud.log_system_event(')

# 2. Extract the USER MANAGEMENT block
start_marker = "# === USER MANAGEMENT ==="
end_marker = "# === LICENSE ==="

if start_marker in main_content and end_marker in main_content:
    start_idx = main_content.find(start_marker)
    end_idx = main_content.find(end_marker)
    
    users_code_raw = main_content[start_idx:end_idx]
    
    # Remove it from main_content
    main_content = main_content[:start_idx] + main_content[end_idx:]
    
    # Process users_code: replace @app. with @router.
    users_code = users_code_raw.replace('@app.', '@router.')
    
    # Build the router file
    router_file_content = f"""from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from ..database import get_auth_db, get_db
from .. import crud, models, schemas, auth
from ..websocket import manager as ui_manager
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Users"])

{users_code}
"""
    os.makedirs(os.path.dirname(USERS_ROUTER), exist_ok=True)
    with open(USERS_ROUTER, 'w', encoding='utf-8') as f:
        f.write(router_file_content)
    
    print("Created routers/users.py")

# 3. Add router inclusion to main.py if not there
if "from .routers import" not in main_content:
    # Find app = FastAPI(...)
    app_decl_idx = main_content.find("app = FastAPI")
    if app_decl_idx != -1:
        # Find the next empty line after app declaration
        next_newline = main_content.find("\n\n", app_decl_idx)
        if next_newline != -1:
            include_code = "\nfrom .routers import users\napp.include_router(users.router)\n"
            main_content = main_content[:next_newline] + include_code + main_content[next_newline:]

# 4. Remove log_system_event definition
log_event_def_start = main_content.find("async def log_system_event(")
if log_event_def_start != -1:
    # Find the end of this function. Next def is around line 1340.
    # It ends with logger.error(f"Failed to write activity log: {e}")
    # We can just look for "async def _check_active_connections" which is the next function.
    next_def_idx = main_content.find("async def _check_active_connections", log_event_def_start)
    if next_def_idx != -1:
        main_content = main_content[:log_event_def_start] + main_content[next_def_idx:]

with open(MAIN_FILE, 'w', encoding='utf-8') as f:
    f.write(main_content)

print("Main.py updated successfully.")
