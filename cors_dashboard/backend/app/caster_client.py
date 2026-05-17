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
        env_master_user = _get_env_preferring_dotenv("CASTER_MASTER_USER", "").strip()
        env_master_password = _get_env_preferring_dotenv("CASTER_MASTER_PASSWORD", "").strip()

        # Prefer explicit arguments first; if missing, use optional master credentials from env.
        self.admin_user = str(admin_user or env_master_user or "admin").strip()
        self.admin_password = str(admin_password or env_master_password or "12345").strip()

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
                
                # Thoát sớm chỉ khi gặp marker kết thúc thật sự của response.
                # Không dừng ở chữ SOURCETABLE vì nó xuất hiện ngay trong header "SOURCETABLE 200 OK".
                partial_resp = chunk.decode('utf-8', errors='ignore')
                if "ENDCLIENTTABLE" in partial_resp or "ENDUSERTABLE" in partial_resp or "ENDSOURCETABLE" in partial_resp:
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

    @staticmethod
    def _parse_bool_token(raw_value, default: bool = True) -> bool:
        if raw_value is None:
            return default
        token = str(raw_value).strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
        return default

    @classmethod
    def _extract_show_position(cls, parts: list[str], default: bool = True) -> bool:
        # Some caster variants place show_position at different indexes.
        # Prefer common indexes first, then scan from tail for a boolean-like token.
        candidate_indexes = [9, 8]
        for idx in candidate_indexes:
            if idx < len(parts):
                token = str(parts[idx]).strip()
                if token.lower() in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
                    return cls._parse_bool_token(token, default)

        for token in reversed(parts):
            normalized = str(token).strip().lower()
            if normalized in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
                return cls._parse_bool_token(normalized, default)

        return default

    @staticmethod
    def _is_success_response(response: str) -> bool:
        text = str(response or "").strip().lower()
        if not text:
            return False
        if "error" in text or "denied" in text or "failed" in text:
            return False
        positive_tokens = (
            "200",
            "ok",
            "success",
            "have been added",
            "have been updated",
            "have been deleted",
        )
        return any(token in text for token in positive_tokens)

    @staticmethod
    def _is_no_change_warning(response: str) -> bool:
        text = str(response or "").strip().lower()
        return "warning: no matching user updated" in text

    @staticmethod
    def _parse_int(value, default: int = 0) -> int:
        try:
            token = str(value).strip()
            return int(token) if token else default
        except Exception:
            return default

    @staticmethod
    def _parse_float(value, default: float = 0.0) -> float:
        try:
            token = str(value).strip()
            return float(token) if token else default
        except Exception:
            return default

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
                            "status": int(parts[5]) if parts[5].isdigit() else 0,
                            "identifier": parts[6].strip() if len(parts) > 6 else "",
                            "show_position": self._extract_show_position(parts, default=True)
                        })
            return users
        except Exception: return []

    async def create_user(
        self,
        creator: str,
        username: str,
        password: str,
        mountpoints: str,
        expiry_seconds: int = 31536000,
        max_users: int = 1,
        show_position: bool = True,
        rtcm_cycle_enabled: bool = False,
        rtcm_disconnect_interval_seconds: int = 300,
        rtcm_disconnect_duration_seconds: int = 10,
    ) -> bool:
        clean_mps = mountpoints.replace(";", ",").replace(" ", "").strip()
        show_pos_val = "1" if show_position else "0"
        rtcm_cycle_val = "1" if rtcm_cycle_enabled else "0"
        safe_cycle_interval = max(30, int(rtcm_disconnect_interval_seconds or 300))
        safe_cycle_duration = max(1, int(rtcm_disconnect_duration_seconds or 10))
        # Định dạng dài (>= 15 parts) để hỗ trợ show_position + rtcm_cycle fields ở cuối.
        # GET /genuser HTTP/1.0/creator/username/password/mounts/expiry/max/status/maxclient/showpos/rtcm_cycle_enabled/rtcm_cycle_interval/rtcm_cycle_duration
        cmd = (
            f"GET /genuser HTTP/1.0/{creator}/{username}/{password}/{clean_mps}/-1/-1/"
            f"{expiry_seconds}/{max_users}/{show_pos_val}/{rtcm_cycle_val}/{safe_cycle_interval}/{safe_cycle_duration}\r\n\r\n"
        )
        response = await self._send_command(cmd)
        return self._is_success_response(response)

    async def toggle_show_pos(self, identifier: str, username: str, enabled: bool) -> bool:
        val = "1" if enabled else "0"
        cmd = f"GET /showpos HTTP/1.0/{identifier}/{username}/{val}\r\n\r\n"
        response = await self._send_command(cmd)
        return self._is_success_response(response) or self._is_no_change_warning(response)

    async def get_clienttable(
        self, mountpoint: str = "all", password: str = None, data_count: int = 1000, admin_user: str = None
    ) -> Optional[List[dict]]:
        user = admin_user or self.admin_user
        pw = password or self.admin_password
        
        # THỬ 3 PHƯƠNG ÁN LỆNH KHÁC NHAU
        cmds = [
            f"GET /clienttable HTTP/1.0/{user}/{mountpoint}/{pw}/{data_count}\r\n\r\n",
            f"GET /admin/clienttable?user={user}&pass={pw}&mountpoint={mountpoint}\r\n\r\n",
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
                    "uptime": int(parts[8]) if len(parts) > 8 and parts[8].isdigit() else 0,
                    "client_id": parts[9].strip() if len(parts) > 9 else "",
                })
            except: continue
        
        return clients

    async def delete_user(self, identifier: str, username: str) -> bool:
        # Tự động truy vấn và lấy identifier thực tế của user trên Caster trước khi thực hiện lệnh xóa
        real_identifier = identifier
        try:
            users = await self.get_users()
            target = next((u for u in users if u["username"].lower() == username.lower()), None)
            if target and target.get("identifier"):
                real_identifier = target["identifier"]
                logger.info(f"Dynamically detected real Caster identifier '{real_identifier}' for user '{username}'")
        except Exception as e:
            logger.error(f"Error querying real identifier for '{username}' deletion: {e}")

        cmd = f"GET /deluser HTTP/1.0/{real_identifier}/{username}\r\n\r\n"
        response = await self._send_command(cmd)
        return self._is_success_response(response)

    async def create_source(
        self,
        identifier: str,
        mountpoint: str,
        password: str = "12345",
        counter: int = 0,
        index: int | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        auth_required: bool = True,
        gga_required: bool = False,
        show_position: bool = False,
        overlap_data: bool = False,
        re_positioning: bool = False,
        storing_data: bool = False,
    ) -> bool:
        safe_identifier = str(identifier or "").strip()
        safe_mountpoint = str(mountpoint or "").strip()
        safe_password = str(password or "12345").strip()
        safe_counter = int(counter or 0)
        safe_index = int(index) if index is not None else -1
        safe_latitude = float(latitude) if latitude is not None else 21.0717
        safe_longitude = float(longitude) if longitude is not None else 105.7735
        safe_auth = 1 if auth_required else 0
        safe_gga = 1 if gga_required else 0
        safe_show_pos = 1 if show_position else 0
        safe_overlap = 1 if overlap_data else 0
        safe_re_positioning = 1 if re_positioning else 0
        safe_storing_data = 1 if storing_data else 0

        cmd = (
            f"GET /gensource HTTP/1.0/{safe_identifier}/{safe_mountpoint}/{safe_password}/{safe_counter}/"
            f"{safe_index}/{safe_latitude}/{safe_longitude}/{safe_auth}/{safe_gga}/{safe_show_pos}/{safe_overlap}/"
            f"{safe_re_positioning}/{safe_storing_data}\r\n\r\n"
        )
        response = await self._send_command(cmd)
        return self._is_success_response(response)

    async def delete_source(self, identifier: str, mountpoint: str) -> bool:
        safe_identifier = str(identifier or "").strip()
        safe_mountpoint = str(mountpoint or "").strip()
        cmd = f"GET /delsource HTTP/1.0/{safe_identifier}/{safe_mountpoint}\r\n\r\n"
        response = await self._send_command(cmd)
        return self._is_success_response(response)

    async def get_sources(self) -> Optional[List[dict]]:
        """
        Fetch all available mountpoints/sources from NTRIP Caster via /sourcetable.
        Returns list of sources with mountpoint, identifier, lat/lon, auth required, etc.
        """
        cmd = f"GET /sourcetable HTTP/1.0\r\n\r\n"
        try:
            response = await self._send_command(cmd)
            if not response or "SOURCETABLE 200 OK" not in response:
                logger.warning(f"Invalid sourcetable response from {self.host}:{self.port}")
                return None

            sources = []
            for line in response.splitlines():
                line = line.strip()
                if "STR;" not in line:
                    continue
                
                # Skip end marker
                if "ENDSOURCETABLE" in line:
                    break
                
                # Clean up trash bytes before STR; if any
                if not line.startswith("STR;"):
                    idx = line.find("STR;")
                    if idx >= 0:
                        line = line[idx:]
                    else:
                        continue

                parts = [p.strip() for p in line.split(";")]

                # Current caster variant returns:
                # STR;Identifier;Mountpoint;Password;Datacount;Latitude;Longitude;AuthRequired;GGARequired;ShowPos
                # Some older variants may return the full NTRIP sourcetable layout.
                try:
                    if len(parts) >= 10 and (parts[7].lower() in {"true", "false", "0", "1", "yes", "no", "on", "off"}):
                        sources.append({
                            "identifier": parts[1],
                            "mountpoint": parts[2],
                            "password": parts[3] if len(parts) > 3 else "",
                            "datacount": self._parse_int(parts[4], 0),
                            "latitude": self._parse_float(parts[5], 0.0),
                            "longitude": self._parse_float(parts[6], 0.0),
                            "auth_required": self._parse_bool_token(parts[7], default=True),
                            "gga_required": self._parse_bool_token(parts[8], default=False),
                            "show_position": self._parse_bool_token(parts[9], default=True),
                            "host": self.host,
                            "domain": self.host,
                            "format": "",
                            "format_details": "",
                            "carrier": 0,
                            "nav_system": "",
                            "network": "",
                            "country": "",
                        })
                        continue

                    # Fallback to full NTRIP table layout
                    if len(parts) >= 15:
                        sources.append({
                            "mountpoint": parts[1],
                            "identifier": parts[2],
                            "password": "",
                            "datacount": 0,
                            "format": parts[3],
                            "format_details": parts[4],
                            "carrier": self._parse_int(parts[5], 0),
                            "nav_system": parts[6],
                            "network": parts[7],
                            "country": parts[8],
                            "latitude": self._parse_float(parts[9], 0.0),
                            "longitude": self._parse_float(parts[10], 0.0),
                            "gga_required": self._parse_bool_token(parts[11], default=False),
                            "show_position": True,
                            "single_base_solution": self._parse_bool_token(parts[12], default=True),
                            "generator": parts[13],
                            "compression": parts[14] if len(parts) > 14 else "",
                            "auth_required": parts[15] if len(parts) > 15 else "N",
                            "fee": parts[16] if len(parts) > 16 else "N",
                            "bitrate": parts[17] if len(parts) > 17 else "",
                            "misc": parts[18] if len(parts) > 18 else "",
                            "host": self.host,
                            "domain": self.host,
                        })
                except Exception as e:
                    logger.warning(f"Failed to parse source line: {line[:80]}, error: {e}")
                    continue

            return sources
        except Exception as e:
            logger.error(f"Error fetching sources from {self.host}:{self.port}: {e}")
            return None
