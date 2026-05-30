import sys
import os

MAIN_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'
DEVICES_ROUTER = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\routers\devices.py'

with open(MAIN_FILE, 'r', encoding='utf-8') as f:
    main_content = f.read()

start_marker = "# === DEVICE ENDPOINTS ==="
end_marker = "# === CASTER USER MANAGEMENT (For non-DB users) ==="

if start_marker in main_content and end_marker in main_content:
    start_idx = main_content.find(start_marker)
    end_idx = main_content.find(end_marker)
    
    code_raw = main_content[start_idx:end_idx]
    
    # Remove it from main_content
    main_content = main_content[:start_idx] + main_content[end_idx:]
    
    # Process code: replace @app. with @router.
    code = code_raw.replace('@app.', '@router.')
    
    # Build the router file
    router_file_content = f"""from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from ..database import get_auth_db, get_db
from .. import crud, models, schemas, auth
from ..command_builder import send_command_to_pi
from ..database import settings
import logging
import re

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Devices"])

{code}
"""
    os.makedirs(os.path.dirname(DEVICES_ROUTER), exist_ok=True)
    with open(DEVICES_ROUTER, 'w', encoding='utf-8') as f:
        f.write(router_file_content)
    
    print("Created routers/devices.py")

    # Add router inclusion to main.py
    include_str = "app.include_router(users.router)"
    new_include = "app.include_router(users.router)\napp.include_router(devices.router)"
    main_content = main_content.replace(include_str, new_include)
    
    import_str = "from .routers import users"
    new_import = "from .routers import users, devices"
    main_content = main_content.replace(import_str, new_import)

    with open(MAIN_FILE, 'w', encoding='utf-8') as f:
        f.write(main_content)

    print("Main.py updated successfully.")
else:
    print("Markers not found.")
