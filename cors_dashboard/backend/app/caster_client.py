import asyncio
import logging
import math
import os
import time
from pathlib import Path
from typing import List, Optional
from dotenv import dotenv_values

logger = logging.getLogger(__name__)
ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"
_ENV_VALUES = dotenv_values(ENV_FILE_PATH)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_SECONDS = 15.0

def _get_env_preferring_dotenv(key: str, default: str) -> str:
    value = os.getenv(key)
    if value is not None and value.strip():
        return value
    val = _ENV_VALUES.get(key)
    if val is not None and str(val).strip() != "":
        return str(val)
    return default

# Cấu hình Debug cho ClientTable
CLIENTTABLE_DEBUG = False

class CasterClient:
    _connection_errors = {}

    def __init__(self, host: str, port: int, admin_user: str | None = None, admin_password: str | None = None):
        self.host = host
        self.port = port
        env_master_user = _get_env_preferring_dotenv("CASTER_MASTER_USER", "") or _get_env_preferring_dotenv("CASTER_ADMIN_USER", "")
        env_master_password = _get_env_preferring_dotenv("CASTER_MASTER_PASSWORD", "") or _get_env_preferring_dotenv("CASTER_PASSWORD", "")
        self.admin_user = str(admin_user or env_master_user).strip()
        self.admin_password = str(admin_password or env_master_password).strip()

    async def _send_command(self, command: str) -> str:
        reader, writer = None, None
        chunks = []
        total_bytes = 0
        deadline = asyncio.get_running_loop().time() + MAX_RESPONSE_SECONDS
        try:
            if CLIENTTABLE_DEBUG:
                logger.info(f"--- THỰC HIỆN KẾT NỐI ĐẾN {self.host}:{self.port} ---")
            
            # 1. Mở kết nối TCP
            # Retry only before sending bytes: replaying a control command could
            # create/delete an account twice after a response is lost.
            for attempt in range(3):
                try:
                    remaining = deadline - asyncio.get_running_loop().time()
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(self.host, self.port),
                        timeout=max(0.01, min(10.0, remaining)),
                    )
                    if self._connection_errors.pop((self.host, self.port), None):
                        logger.info("Caster connection restored (%s:%s)", self.host, self.port)
                    break
                except (OSError, asyncio.TimeoutError):
                    delay = attempt + 1
                    if attempt == 2 or deadline - asyncio.get_running_loop().time() <= delay:
                        raise
                    await asyncio.sleep(delay)
            
            # 2. Gửi lệnh
            writer.write(command.encode('ascii', errors='ignore'))
            await writer.drain()

            # 3. Đọc dữ liệu
            # Phân biệt truy vấn bảng dữ liệu lớn (usertable, sourcetable, clienttable, hoặc standard NTRIP GET /) và lệnh điều khiển ngắn
            cmd_clean = command.strip()
            is_table_query = any(x in command for x in ["/usertable", "/sourcetable", "/clienttable"]) or cmd_clean.startswith("GET / HTTP/") or "\nGET / HTTP/" in command
            
            # Tăng timeout an toàn lên 5.0s để chờ Caster xử lý xong vòng lặp UI (tối đa 2.0s)
            timeout = 5.0
            marker_tail = b""
            
            while True:
                try:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > MAX_RESPONSE_BYTES:
                        logger.warning("Caster response exceeded the %s-byte limit", MAX_RESPONSE_BYTES)
                        return ""
                    
                    # Với các lệnh điều khiển ngắn, chỉ đọc gói dữ liệu đầu tiên rồi thoát ngay để tránh block kết nối
                    if not is_table_query:
                        break
                    
                    # Thoát sớm chỉ khi gặp marker kết thúc thật sự của response.
                    marker_window = (marker_tail + chunk).upper()
                    if any(marker in marker_window for marker in (b"ENDCLIENTTABLE", b"ENDUSERTABLE", b"ENDSOURCETABLE")):
                        break
                    marker_tail = marker_window[-32:]
                    
                    # Các lần đọc tiếp theo của bảng dữ liệu có thể dùng timeout ngắn hơn (1.0s)
                    timeout = min(1.0, remaining)
                except asyncio.TimeoutError:
                    # Chấp nhận timeout như là dấu hiệu kết thúc luồng dữ liệu
                    break
            
            return b"".join(chunks).decode('utf-8', errors='ignore')

        except Exception as e:
            if isinstance(e, (OSError, asyncio.TimeoutError)):
                key = (self.host, self.port)
                now = time.monotonic()
                last = self._connection_errors.get(key)
                if last is None or now - last >= 60:
                    logger.warning("Caster unavailable (%s:%s): %s; automatic polling will retry",
                                   self.host, self.port, type(e).__name__)
                    self._connection_errors[key] = now
            else:
                logger.error("Lỗi gọi Caster (%s:%s): %s", self.host, self.port, type(e).__name__)
            if chunks and total_bytes <= MAX_RESPONSE_BYTES:
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
        # Some caster variants place show_position at different indexes. In the
        # current usertable format, index 7 is "#connection" and index 8 is
        # "Max Connection", so index 8 must not be interpreted as show_position.
        candidate_indexes = [9]
        if not cls._has_connection_limit_columns(parts):
            candidate_indexes.append(8)
        for idx in candidate_indexes:
            if idx < len(parts):
                token = str(parts[idx]).strip()
                if token.lower() in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
                    return cls._parse_bool_token(token, default)

        if cls._has_connection_limit_columns(parts):
            return default

        for token in reversed(parts):
            normalized = str(token).strip().lower()
            if normalized in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
                return cls._parse_bool_token(normalized, default)

        return default

    @staticmethod
    def _is_int_token(value) -> bool:
        try:
            token = str(value).strip()
            if token == "":
                return False
            int(token)
            return True
        except Exception:
            return False

    @classmethod
    def _has_connection_limit_columns(cls, parts: list[str]) -> bool:
        return len(parts) >= 9 and cls._is_int_token(parts[7]) and cls._is_int_token(parts[8])

    @classmethod
    def _extract_maxclient(cls, parts: list[str], default: int = 0) -> int:
        # Current caster usertable:
        # STR;Username;Password;Mountpoint;Data;Status;Identifier;#connection;Max Connection
        if cls._has_connection_limit_columns(parts):
            return max(0, int(str(parts[8]).strip()))

        # Some variants place maxclient after identifier and before show_position.
        # Only parse when a following show_position-like field is present to avoid
        # treating legacy show_position-only rows as a connection limit.
        candidates = ((7, 8), (8, 9))
        for max_idx, show_pos_idx in candidates:
            if show_pos_idx >= len(parts) or max_idx >= len(parts):
                continue
            show_pos_token = str(parts[show_pos_idx]).strip().lower()
            if show_pos_token not in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
                continue
            try:
                token = str(parts[max_idx]).strip()
                if token:
                    return max(0, int(token))
            except Exception:
                continue
        return default

    @classmethod
    def _extract_nclient(cls, parts: list[str], default: int = 0) -> int:
        if cls._has_connection_limit_columns(parts):
            return max(0, int(str(parts[7]).strip()))
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

    @staticmethod
    def _parse_coordinate(value):
        try:
            coordinate = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        return coordinate if math.isfinite(coordinate) else None

    async def get_users(self, *, require_complete: bool = False) -> List[dict]:
        if not self.admin_user or not self.admin_password:
            logger.error("Caster usertable credentials are not configured")
            if require_complete:
                raise ValueError("Caster usertable credentials are not configured")
            return []
        cmd = f"GET /usertable HTTP/1.0/{self.admin_user}/mp1/{self.admin_password}/9999\r\n\r\n"
        try:
            response = await self._send_command(cmd)

            if require_complete and ("USERTABLE 200 OK" not in response or "ENDUSERTABLE" not in response):
                raise ValueError("Caster did not return a complete usertable")
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
                            "nclient": self._extract_nclient(parts, default=0),
                            "maxclient": self._extract_maxclient(parts, default=0),
                            "show_position": self._extract_show_position(parts, default=True),
                            "is_blocked": parts[12].strip() == "1" if len(parts) > 12 else False,
                            "block_reason": __import__("urllib").parse.unquote(parts[13].strip()) if len(parts) > 13 else ""
                        })
            return users
        except Exception:
            if require_complete:
                raise
            return []

    async def get_user_remaining_seconds(self, username: str) -> Optional[int]:
        users = await self.get_users()
        user = next((u for u in users if u["username"].lower() == username.lower()), None)
        return int(user["status"]) if user is not None else None

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
        auto_nearest: bool | None = None,
        auto_nearest_on_request: bool = False,
    ) -> bool:
        clean_mps = mountpoints.replace(";", ",").replace(" ", "").strip()
        show_pos_val = "1" if show_position else "0"
        rtcm_cycle_val = "1" if rtcm_cycle_enabled else "0"
        safe_cycle_interval = max(30, int(rtcm_disconnect_interval_seconds or 300))
        safe_cycle_duration = max(1, int(rtcm_disconnect_duration_seconds or 10))
        # Định dạng dài (>= 15 parts) để hỗ trợ show_position + rtcm_cycle fields ở cuối.
        # GET /genuser HTTP/1.0/creator/username/password/mounts/expiry/max/status/maxclient/showpos/rtcm_cycle_enabled/rtcm_cycle_interval/rtcm_cycle_duration
        nearest_mode = 2 if auto_nearest and auto_nearest_on_request else int(bool(auto_nearest))
        nearest_suffix = "" if auto_nearest is None else f"/{nearest_mode}"
        cmd = (
            f"GET /genuser HTTP/1.0/{creator}/{username}/{password}/{clean_mps}/-1/-1/"
            f"{expiry_seconds}/{max_users}/{show_pos_val}/{rtcm_cycle_val}/{safe_cycle_interval}/{safe_cycle_duration}{nearest_suffix}\r\n\r\n"
        )
        response = await self._send_command(cmd)
        return self._is_success_response(response) and (
            auto_nearest is None or f"AutoNearest={nearest_mode}" in response
        )

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
        if not user or not pw:
            logger.error("Caster clienttable credentials are not configured")
            return None
        
        # THỬ 3 PHƯƠNG ÁN LỆNH KHÁC NHAU
        cmds = [
            f"GET /clienttable HTTP/1.0/{user}/{mountpoint}/{pw}/{data_count}\r\n\r\n",
            f"GET /admin/clienttable?user={user}&pass={pw}&mountpoint={mountpoint}\r\n\r\n",
        ]

        response = ""
        for cmd in cmds:
            try:
                resp = await self._send_command(cmd)
                if (resp and "ENDCLIENTTABLE" in resp
                        and ("CLIENTTABLE 200 OK" in resp or "OK" in resp[:30])):
                    response = resp
                    if CLIENTTABLE_DEBUG: logger.info(f"-> THÀNH CÔNG VỚI LỆNH: {repr(cmd)}")
                    break
            except: continue

        if not response: return None

        if CLIENTTABLE_DEBUG:
            logger.info(f"[CASTER RAW] Response: {repr(response[:150])}")

        from urllib.parse import unquote

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
                requested = next((unquote(part[len("RequestedMP="):]) for part in parts[15:]
                                  if part.startswith("RequestedMP=")), None)
                clients.append({
                    "username": parts[1].strip(),
                    "receiver_id": parts[2].strip(),
                    "mountpoint": parts[3].strip(),
                    "requested_mountpoint": requested,
                    "selected_mountpoint": parts[3].strip() if requested else None,
                    "latitude": self._parse_coordinate(parts[4]) if parts[4] else None,
                    "longitude": self._parse_coordinate(parts[5]) if parts[5] else None,
                    "status": int(parts[6]) if parts[6].isdigit() else 1,
                    "nsv": int(parts[7]) if parts[7].isdigit() else 0,
                    "uptime": int(parts[8]) if len(parts) > 8 and parts[8].isdigit() else None,
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

    async def block_user(self, identifier: str, username: str, reason: str) -> bool:
        import urllib.parse
        encoded_reason = urllib.parse.quote(reason)
        cmd = f"GET /blockuser HTTP/1.0/{identifier}/{username}/{encoded_reason}\r\n\r\n"
        response = await self._send_command(cmd)
        return self._is_success_response(response)

    async def unblock_user(self, identifier: str, username: str) -> bool:
        cmd = f"GET /unblockuser HTTP/1.0/{identifier}/{username}\r\n\r\n"
        response = await self._send_command(cmd)
        return self._is_success_response(response)

    async def create_source(
        self,
        identifier: str,
        mountpoint: str,
        password: str | None = None,
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
        safe_password = str(password or "").strip()
        if not safe_password:
            raise ValueError("A non-empty source password is required")
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

    async def get_sources(self, prefer_standard_ntrip: bool = False) -> Optional[List[dict]]:
        """
        Fetch all available mountpoints/sources from NTRIP Caster via /sourcetable or standard NTRIP GET /.
        Returns list of sources with mountpoint, identifier, lat/lon, auth required, etc.
        """
        import base64
        headers = ["GET / HTTP/1.0", "User-Agent: NTRIP Aitogy/1.0"]
        if self.admin_user and self.admin_password:
            token = base64.b64encode(f"{self.admin_user}:{self.admin_password}".encode("latin1", errors="ignore")).decode("ascii")
            headers.append(f"Authorization: Basic {token}")
        headers.append("Connection: close")
        cmd_standard = "\r\n".join(headers) + "\r\n\r\n"
        cmd_custom = "GET /sourcetable HTTP/1.0\r\n\r\n"

        try:
            if prefer_standard_ntrip:
                response = await self._send_command(cmd_standard)
                if not response or "SOURCETABLE 200 OK" not in response:
                    response = await self._send_command(cmd_custom)
            else:
                response = await self._send_command(cmd_custom)
                if not response or "SOURCETABLE 200 OK" not in response:
                    response = await self._send_command(cmd_standard)

            if not response:
                return None
            if "SOURCETABLE 200 OK" not in response:
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
                        raw_auth = parts[15] if len(parts) > 15 else "N"
                        is_auth_req = str(raw_auth).strip().upper() in {"B", "D", "Y", "TRUE", "1"}
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
                            "auth_required": is_auth_req,
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
