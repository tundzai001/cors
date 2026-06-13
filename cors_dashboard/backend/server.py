"""
Quick start server:
    cd backend
    python server.py
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


load_dotenv(Path(__file__).with_name(".env"), override=False)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False, 
        workers=1
    )
