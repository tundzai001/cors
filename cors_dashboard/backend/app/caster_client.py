import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional
from dotenv import dotenv_values

logger = logging.getLogger(__name__)
ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"
_ENV_VALUES = dotenv_values(ENV_FILE_PATH)

def _get_env_preferring_dotenv(key: str, default: str) -> str:
    val = _ENV_VALUES.get(key)
    if val is not None and str(val).strip() != "":
        return str(val)
    return str(os.getenv(key, default))

# Cấu hình Debug cho ClientTable
CLIENTTABLE_DEBUG = False

class CasterClient:
    def __init__(self, host: str, port: int, admin_user: str = "admin", admin_password: str = "12345"):
        self.host = host
        self.port = port
        self.admin_user = admin_user
        self.admin_password = admin_password

    async def _send_command(self, command: str) -> str:
        reader, writer = None, None
        chunks = []
        try:
            if CLIENTTABLE_DEBUG:
                logger.info(f"--- THỰC HIỆN KẾT NỐI ĐẾN {self.host}:{self.port} ---")
            
            # 1. Mở kết nối TCP
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), 
                timeout=10.0
            )
            
            # 2. Gửi lệnh
            writer.write(command.encode('ascii', errors='ignore'))
            await writer.drain()

            # 3. Đọc dữ liệu
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
                if not chunk:
                    break
                chunks.append(chunk)
                
                # Thoát sớm nếu thấy dấu hiệu kết thúc (Tăng tốc Polling)
                partial_resp = chunk.decode('utf-8', errors='ignore')
                if "ENDCLIENTTABLE" in partial_resp or "END USERTABLE" in partial_resp or "SOURCETABLE" in partial_resp:
                    break
            
            return b"".join(chunks).decode('utf-8', errors='ignore')

        except Exception as e:
            logger.error(f"Lỗi gọi Caster ({self.host}:{self.port}): {str(e)}")
            if chunks:
                return b"".join(chunks).decode('utf-8', errors='ignore')
            return ""
        finally:
            if writer:
                writer.close()
                try: await writer.wait_closed()
                except: pass

    async def get_users(self) -> List[dict]:
        cmd = f"GET /usertable HTTP/1.0/{self.admin_user}/mp1/password1/9999\r\n\r\n"
        try:
            response = await self._send_command(cmd)
            # Fallback 'id'
            if "Error" in response and self.admin_user != "id":
                cmd = f"GET /usertable HTTP/1.0/id/mp1/password1/9999\r\n\r\n"
                response = await self._send_command(cmd)

            users = []
            for line in response.split('\n'):
                if line.strip().startswith('STR;'):
                    parts = line.split(';')
                    if len(parts) >= 9:
                        users.append({
                            "username": parts[1].strip(),
                            "password": parts[2].strip(),
                            "mountpoints": [mp.strip() for mp in parts[3].split(',') if mp.strip()],
                            "datacount": int(parts[4]) if parts[4].isdigit() else 0,
                            "status": int(parts[5]) if parts[5].isdigit() else 0
                        })
            return users
        except Exception: return []

    async def create_user(self, creator: str, username: str, password: str, mountpoints: str, expiry_seconds: int = 31536000, max_users: int = 1) -> bool:
        clean_mps = mountpoints.replace(";", ",").replace(" ", "").strip()
        cmd = f"GET /genuser HTTP/1.0/{creator}/{username}/{password}/{clean_mps}/{expiry_seconds}/{max_users}\r\n\r\n"
        await self._send_command(cmd)
        return True

    async def get_clienttable(
        self, mountpoint: str = "all", password: str = None, data_count: int = 1000, admin_user: str = None
    ) -> Optional[List[dict]]:
        user = admin_user or self.admin_user
        pw = password or self.admin_password
        
        # THỬ 3 PHƯƠNG ÁN LỆNH KHÁC NHAU
        cmds = [
            f"GET /clienttable HTTP/1.0/{user}/{mountpoint}/{pw}/{data_count}\r\n",
            f"GET /clienttable HTTP/1.0/{user}/{mountpoint}/{pw}/{data_count}\r\n\r\n",
            f"GET /admin/clienttable?user={user}&pass={pw}&mountpoint={mountpoint}\r\n\r\n",
            f"GET /clienttable HTTP/1.0/id/all/password1/1000\r\n" # Lệnh Hercules chính chủ
        ]

        response = ""
        for cmd in cmds:
            try:
                resp = await self._send_command(cmd)
                if resp and ("CLIENTTABLE 200 OK" in resp or "OK" in resp[:30]):
                    response = resp
                    if CLIENTTABLE_DEBUG: logger.info(f"-> THÀNH CÔNG VỚI LỆNH: {repr(cmd)}")
                    break
            except: continue

        if not response: return None

        if CLIENTTABLE_DEBUG:
            logger.info(f"[CASTER RAW] Response: {repr(response[:150])}")

        clients = []
        for line in response.splitlines():
            line = line.strip()
            # Caster một số dòng bị thừa ký tự rác (như nSTR;) nên chúng ta tìm STR; trong chuỗi
            if "STR;" not in line: continue
            
            # Cắt bỏ phần rác trước STR; nếu có
            if not line.startswith("STR;"):
                idx = line.find("STR;")
                line = line[idx:]

            parts = line.split(";")
            if len(parts) < 8: continue
            
            try:
                clients.append({
                    "username": parts[1].strip(),
                    "receiver_id": parts[2].strip(),
                    "mountpoint": parts[3].strip(),
                    "latitude": float(parts[4]) if parts[4] else None,
                    "longitude": float(parts[5]) if parts[5] else None,
                    "status": int(parts[6]) if parts[6].isdigit() else 1,
                    "nsv": int(parts[7]) if parts[7].isdigit() else 0,
                    "client_id": parts[9].strip() if len(parts) > 9 else "",
                })
            except: continue
        
        return clients

    async def delete_user(self, identifier: str, username: str) -> bool:
        cmd = f"GET /deluser HTTP/1.0/{identifier}/{username}\r\n\r\n"
        await self._send_command(cmd)
        return True
