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

import logging

class UvicornWebsocketFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if record.levelname == "ERROR" and "data transfer failed" in message:
            return False
        # These two lines become thousands of synchronous writes during a
        # reconnect herd and carry no station identity or diagnostic value.
        if record.levelno <= logging.INFO and message in {"connection open", "connection closed"}:
            return False
        return True

logging.getLogger("uvicorn.error").addFilter(UvicornWebsocketFilter())

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False, 
        workers=1
    )
