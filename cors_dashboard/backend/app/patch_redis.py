import sys
import os

MAIN_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'

with open(MAIN_FILE, 'r', encoding='utf-8') as f:
    main_content = f.read()

# 1. Import redis_client
if "from .redis_client import redis_client" not in main_content:
    import_idx = main_content.find("from . import crud, models, schemas, command_builder, auth")
    if import_idx != -1:
        main_content = main_content[:import_idx] + "from .redis_client import redis_client\n" + main_content[import_idx:]

# 2. Add redis connect/disconnect to lifespan
lifespan_start = main_content.find("async def lifespan(app: FastAPI):")
if lifespan_start != -1:
    # Find the yield statement
    yield_idx = main_content.find("yield", lifespan_start)
    if yield_idx != -1:
        # Insert await redis_client.connect() before yield
        main_content = main_content[:yield_idx] + "await redis_client.connect()\n    " + main_content[yield_idx:]
        
        # Insert await redis_client.disconnect() after yield
        # Find next line after yield
        next_line_idx = main_content.find("\n", yield_idx)
        if next_line_idx != -1:
            main_content = main_content[:next_line_idx] + "\n    await redis_client.disconnect()" + main_content[next_line_idx:]

with open(MAIN_FILE, 'w', encoding='utf-8') as f:
    f.write(main_content)

print("Redis integrated into lifespan.")
