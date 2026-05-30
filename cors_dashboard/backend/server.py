"""
Quick start server:
    cd backend
    python server.py
"""
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"), override=False)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False, 
        workers=1
    )
