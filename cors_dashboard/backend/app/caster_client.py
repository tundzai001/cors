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


CLIENTTABLE_DEBUG = str(
    _get_env_preferring_dotenv("RTCM_KEEPALIVE_DEBUG", "0")
).strip().lower() in ("1", "true", "yes", "on")

class CasterClient:
    def __init__(self, host: str, port: int, admin_user: str = "admin", admin_password: str = "12345"):
        self.host = host
        self.port = port
        self.admin_user = admin_user
        self.admin_password = admin_password

    async def _send_command(self, command: str) -> str:
        reader, writer = None, None
        try:
            if CLIENTTABLE_DEBUG:
                logger.info(f"--- THỰC HIỆN KẾT NỐI ĐẾN {self.host}:{self.port} ---")
            
            # 1. Mở kết nối TCP
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), 
                    timeout=10.0
                )
                if CLIENTTABLE_DEBUG:
                    logger.info("-> ĐÃ MỞ SOCKET THÀNH CÔNG!")
            except asyncio.TimeoutError:
                error_msg = f"Không thể KẾT NỐI TCP đến NTRIP Caster ({self.host}:{self.port}) - Timeout"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            # 2. Gửi lệnh
            if CLIENTTABLE_DEBUG:
                logger.info(f"-> GỬI LỆNH: {repr(command)}")
            elif "/clienttable" not in command: # Luôn log các lệnh quan trọng, ClientTable log nếu bật debug
                logger.debug(f"-> GỬI LỆNH: {repr(command)}")
            writer.write(command.encode('ascii', errors='ignore'))
            await writer.drain()

            # 3. Đọc dữ liệu
            if CLIENTTABLE_DEBUG:
                logger.info("-> ĐANG ĐỢI DỮ LIỆU TỪ CASTER...")
            try:
                chunks = []
                while True:
                    # Đọc dữ liệu theo từng chunk cho đến khi hết
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
                    if not chunk:
                        break
                    chunks.append(chunk)
                
                response = b"".join(chunks).decode('utf-8', errors='ignore')
                if CLIENTTABLE_DEBUG:
                    logger.info(f"-> NHẬN PHẢN HỒI ({len(response)} bytes) THÀNH CÔNG")
                return response
            except asyncio.TimeoutError:
                if not chunks:
                    error_msg = f"Đã kết nối nhưng CASTER KHÔNG PHẢN HỒI dữ liệu trong 10s ({self.host}:{self.port})"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                # Nếu đã đọc được một số chunks thì trả về luôn thay vì báo lỗi timeout
                response = b"".join(chunks).decode('utf-8', errors='ignore')
                return response

        except Exception as e:
            if not isinstance(e, asyncio.TimeoutError):
                error_msg = f"Lỗi không xác định khi gọi Caster ({self.host}:{self.port}): {str(e)}"
                logger.error(error_msg)
                raise Exception(error_msg)
            raise e
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except:
                    pass

    async def get_users(self) -> List[dict]:
        cmd = f"GET /usertable HTTP/1.0/{self.admin_user}/mp1/password1/9999\r\n\r\n"
        
        try:
            response = await self._send_command(cmd)
            
            # Fallback nếu admin_user không hợp lệ
            if "Error" in response and self.admin_user != "id":
                logger.warning(f"Caster rejected user '{self.admin_user}', trying 'id' fallback...")
                cmd = f"GET /usertable HTTP/1.0/id/mp1/password1/9999\r\n\r\n"
                response = await self._send_command(cmd)

            users = []
            
            lines = response.split('\n')
            for line in lines:
                if line.startswith('STR;'):
                    parts = line.split(';')
                    if len(parts) >= 9:
                        def _to_int(value: str, default: int = 0) -> int:
                            try:
                                return int(str(value).strip())
                            except Exception:
                                return default

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
                            "datacount": _to_int(parts[4], 0),
                            "status": _to_int(parts[5], 0), 
                            "identifier": parts[6], 
                            "nclient": _to_int(parts[7], 0),
                            "maxclient": _to_int(parts[8], 0),
                        })
            return users
        except Exception as e:
            logger.error(f"Failed to fetch users from caster: {e}")
            return None

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
        self, mountpoint: str = "all", password: str = None, data_count: int = 1, admin_user: str = None
    ) -> Optional[List[dict]]:
        user = admin_user or self.admin_user
        password = password or self.admin_password
        # Format chuẩn: GET /clienttable HTTP/1.0/id/[mp]/[pass]/[count]\r\n\r\n
        cmd = f"GET /clienttable HTTP/1.0/{user}/{mountpoint}/{password}/{data_count}\r\n\r\n"

        try:
            response = await self._send_command(cmd)
            
            # Kiểm tra Header phản hồi (CLIENTTABLE 200 OK)
            if "CLIENTTABLE 200 OK" not in response:
                # Nếu nhận phản hồi rỗng hoặc lỗi, và chưa dùng fallback 'id', thử lại với 'id'
                if (not response or "Error" in response) and user != "id":
                    logger.warning(f"Caster rejected user '{user}', trying 'id' fallback...")
                    return await self.get_clienttable(mountpoint, password, data_count, "id")
                
                # MẸO THỬ CUỐI CÙNG: Thử dùng id nhưng bỏ /clienttable (một số caster Aitogy cũ dùng thế này)
                if (not response or "Error" in response) and "/clienttable" in cmd:
                    logger.warning(f"Trying alternative command format (no /clienttable)...")
                    alt_cmd = f"GET /{user}/{mountpoint}/{password}/{data_count} HTTP/1.0\r\n\r\n"
                    # Gọi trực tiếp _send_command để tránh đệ quy vô tận
                    alt_response = await self._send_command(alt_cmd)
                    if "CLIENTTABLE 200 OK" in alt_response:
                        response = alt_response
                    else:
                        logger.warning(f"Alternative format also failed: {repr(alt_response[:100])}")
                
                if "CLIENTTABLE 200 OK" not in response:
                    logger.warning(f"Caster response header (MP={mountpoint}, User={user}): {repr(response[:200])}")
            
            clients: List[dict] = []

            def _to_int_or_none(value: str) -> Optional[int]:
                try:
                    return int(str(value).strip())
                except Exception:
                    return None
            
            for raw_line in response.splitlines():
                line = raw_line.strip()
                
                # Kết thúc khi gặp ENDCLIENTTABLE
                if "ENDCLIENTTABLE" in line:
                    break
                    
                if not line.startswith("STR;"):
                    continue

                parts = line.split(";")
                if len(parts) < 8:
                    continue

                lat = None
                lon = None
                try:
                    lat = float(parts[4])
                    lon = float(parts[5])
                except (TypeError, ValueError):
                    pass

                clients.append(
                    {
                        "username": parts[1].strip(),
                        "receiver_id": parts[2].strip(),
                        "mountpoint": parts[3].strip(),
                        "latitude": lat,
                        "longitude": lon,
                        "status": _to_int_or_none(parts[6]),
                        "nsv": _to_int_or_none(parts[7]),
                        "identifier": parts[8].strip() if len(parts) > 8 else "",
                        "client_id": parts[9].strip() if len(parts) > 9 else "",
                    }
                )

            if CLIENTTABLE_DEBUG:
                parsed_mps = sorted({str(item.get("mountpoint", "")).strip().lower() for item in clients if item.get("mountpoint")})
                logger.warning(
                    f"[RTCM DEBUG] clienttable request mp='{mountpoint}' parsed_rows={len(clients)} parsed_mountpoints={parsed_mps[:20]}"
                )

            return clients
        except Exception as e:
            logger.error(f"Failed to fetch clienttable from caster: {e}")
            return None

    async def delete_user(self, identifier: str, username: str) -> bool:
        cmd = f"GET /deluser HTTP/1.0/{identifier}/{username}\r\n\r\n"
        response = await self._send_command(cmd)
        
        if "have been deleted" in response:
            return True
        else:
            # logger.warning(f"Delete response unexpected: {response.strip()}")
            return True
