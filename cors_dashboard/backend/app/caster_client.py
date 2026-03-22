import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional
from dotenv import dotenv_values

logger = logging.getLogger(__name__)
ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"
CLIENTTABLE_DEBUG = str(
    os.getenv(
        "RTCM_KEEPALIVE_DEBUG",
        str(dotenv_values(ENV_FILE_PATH).get("RTCM_KEEPALIVE_DEBUG", "0")),
    )
).strip().lower() in ("1", "true", "yes", "on")

class CasterClient:
    def __init__(self, host: str, port: int, admin_user: str = "id"):
        self.host = host
        self.port = port
        self.admin_user = admin_user 

    async def _send_command(self, command: str) -> str:
        reader, writer = None, None
        try:
            # logger.info(f"Connecting to Caster {self.host}:{self.port}")
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), 
                timeout=5.0
            )
            # logger.debug(f"Sending TCP: {command.strip()}")
            
            writer.write(command.encode('utf-8'))
            await writer.drain()

            data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response = data.decode('utf-8', errors='ignore')
            
            # if response.strip():
            #     logger.debug(f"Caster Response: {response.strip()}")
            
            return response

        except asyncio.TimeoutError:
            logger.error("Caster connection timed out")
            raise Exception("Không thể kết nối đến NTRIP Caster (Timeout)")
        except Exception as e:
            logger.error(f"Caster Error: {e}")
            raise Exception(f"Lỗi kết nối Caster: {str(e)}")
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except:
                    pass

    async def get_users(self) -> List[dict]:
        cmd = f"GET /usertable HTTP/1.0/{self.admin_user}/mp1/password1/9999\r\n"
        
        try:
            response = await self._send_command(cmd)
            users = []
            
            lines = response.split('\n')
            for line in lines:
                if line.startswith('STR;'):
                    parts = line.split(';')
                    if len(parts) >= 9:
                        # === FIX 1: Cắt bỏ khoảng trắng khi parse mountpoints ===
                        raw_mps = parts[3]
                        mps_list = []
                        if raw_mps:
                            # Tách bằng dấu phẩy và strip() từng phần tử để xóa dấu cách thừa
                            mps_list = [mp.strip() for mp in raw_mps.split(',') if mp.strip()]

                        users.append({
                            "username": parts[1],
                            "password": parts[2],
                            "mountpoints": mps_list, # List sạch không có dấu cách
                            "datacount": int(parts[4]) if parts[4].isdigit() else 0,
                            "status": int(parts[5]) if parts[5].isdigit() else 0, 
                            "identifier": parts[6], 
                            "nclient": int(parts[7]) if parts[7].isdigit() else 0,
                            "maxclient": int(parts[8]) if parts[8].isdigit() else 0,
                        })
            return users
        except Exception as e:
            logger.error(f"Failed to fetch users from caster: {e}")
            return []

    async def create_user(self, creator: str, username: str, password: str, mountpoints: str, expiry_seconds: int = 31536000, max_users: int = 1) -> bool:
        clean_mountpoints = mountpoints.replace(";", ",").replace(" ", "").strip()
        
        # Log để kiểm tra xem chuỗi sạch chưa
        logger.info(f"Creating Caster User '{username}' with MPs: {clean_mountpoints}")

        cmd = f"GET /genuser HTTP/1.0/{creator}/{username}/{password}/{clean_mountpoints}/{expiry_seconds}/{max_users}\r\n\r\n"
        
        response = await self._send_command(cmd)
        if "Error" in response:
            logger.error(f"Caster returned error creating user: {response}")
            raise Exception(f"Caster từ chối tạo user: {response.strip()}")
            
        return True

    async def get_clienttable(
        self,
        admin_user: Optional[str] = None,
        mountpoint: str = "mp1",
        password: str = "password1",
        data_count: int = 9999,
    ) -> List[dict]:
        user = admin_user or self.admin_user
        cmd = f"GET /clienttable HTTP/1.0/{user}/{mountpoint}/{password}/{data_count}\r\n"

        try:
            response = await self._send_command(cmd)
            clients: List[dict] = []
            raw_str_rows: List[str] = []

            for raw_line in response.splitlines():
                line = raw_line.strip()
                if not line.startswith("STR;"):
                    continue
                if CLIENTTABLE_DEBUG:
                    raw_str_rows.append(line)

                parts = line.split(";")
                if len(parts) < 8:
                    continue

                lat = None
                lon = None
                try:
                    lat = float(parts[4])
                    lon = float(parts[5])
                except (TypeError, ValueError):
                    # Keep client row for keepalive even when coordinates are unavailable.
                    pass

                clients.append(
                    {
                        "username": parts[1].strip(),
                        "receiver_id": parts[2].strip(),
                        "mountpoint": parts[3].strip(),
                        "latitude": lat,
                        "longitude": lon,
                        "status": int(parts[6]) if parts[6].isdigit() else None,
                        "nsv": int(parts[7]) if parts[7].isdigit() else None,
                        "identifier": parts[8].strip() if len(parts) > 8 else "",
                        "client_id": parts[9].strip() if len(parts) > 9 else "",
                    }
                )

            if CLIENTTABLE_DEBUG:
                parsed_mps = sorted({str(item.get("mountpoint", "")).strip().lower() for item in clients if item.get("mountpoint")})
                logger.warning(
                    f"[RTCM DEBUG] clienttable request mp='{mountpoint}' parsed_rows={len(clients)} parsed_mountpoints={parsed_mps[:20]}"
                )
                if raw_str_rows:
                    logger.warning(f"[RTCM DEBUG] clienttable sample raw: {raw_str_rows[:3]}")

            return clients
        except Exception as e:
            logger.error(f"Failed to fetch clienttable from caster: {e}")
            return []

    async def delete_user(self, identifier: str, username: str) -> bool:
        cmd = f"GET /deluser HTTP/1.0/{identifier}/{username}\r\n"
        response = await self._send_command(cmd)
        
        if "have been deleted" in response:
            return True
        else:
            # logger.warning(f"Delete response unexpected: {response.strip()}")
            return True
