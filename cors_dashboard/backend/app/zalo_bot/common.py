"""Shared command names, permissions and Vietnamese intent matching."""
from __future__ import annotations

import re
import unicodedata
from typing import List

from .. import auth
from ..runtime_config import _get_env_preferring_dotenv

BOT_COMMANDS = (
    "start", "menu", "help", "cors", "register", "link", "notifications", "status", "errors", "stations", "station",
    "config", "configset", "coord", "stationlock", "stationunlock", "stationreset",
    "stationdelete", "rovers", "rover", "rovercreate", "roverupdate", "roverdelete",
    "roverrenew", "roverblock", "roverunblock", "confirm", "cancel", "logout", "unlink",
)

# ---------------------------------------------------------------------------
# Smart dispatch: map command names to handler method names
# ---------------------------------------------------------------------------
_COMMAND_DISPATCH = {
    "cors": "_cors_cmd",
    "start": "_start_cmd",
    "help": "_help_cmd",
    "menu": "_menu_cmd",
    "register": "_register_cmd",
    "link": "_link_cmd",
    "notifications": "_notifications_cmd",
    "status": "_status_cmd",
    "errors": "_errors_cmd",
    "stations": "_stations_cmd",
    "station": "_station_cmd",
    "config": "_config_cmd",
    "configset": "_configset_cmd",
    "coord": "_coord_cmd",
    "rovers": "_rovers_cmd",
    "rover": "_rover_cmd",
    "rovercreate": "_rovercreate_cmd",
    "roverupdate": "_roverupdate_cmd",
    "roverdelete": "_roverdelete_cmd",
    "roverrenew": "_roverrenew_cmd",
    "roverblock": "_roverblock_cmd",
    "roverunblock": "_roverunblock_cmd",
    "stationlock": "_stationlock_cmd",
    "stationunlock": "_stationunlock_cmd",
    "stationreset": "_stationreset_cmd",
    "stationdelete": "_stationdelete_cmd",
    "confirm": "_confirm_cmd",
    "cancel": "_cancel_cmd",
    "logout": "_logout_cmd",
    "unlink": "_logout_cmd",
}


def _normalize_to_ascii(text: str) -> str:
    """Strip Vietnamese diacritics and lowercase for NLP matching."""
    text = text.lower().replace("đ", "d").strip()
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _is_cors_status_question(text: str) -> bool:
    """Recognize public Cục health questions with or without Vietnamese accents."""
    normalized = _normalize_to_ascii(text).lstrip("\u200b\u200c\u200d\ufeff").strip()
    if normalized.startswith("/"):
        return False  # Explicit commands keep their own permissions and arguments.
    words = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    if re.search(r"\b(?:base|rover)\b", words):
        return False
    from ..zalo_cors_report import region_for_question, region_options, is_region_only, _plain
    region = region_for_question(words)[0]
    from ..zalo_cors_dialogue import cors_intent, DIAGNOSTIC_INTENTS
    intent = cors_intent(words)
    if intent.startswith('faq_') or intent in DIAGNOSTIC_INTENTS or intent == 'fix_possible':
        return True
    if region_options(words) and intent in {"stability", "measurement", "detail", "diagnostic"}:
        return True
    if region_options(words) and re.search(r'\b(tinh trang|tinh hinh|trang thai)\b', words):
        return True
    if is_region_only(words):
        return True
    if region != "Việt Nam" and words in {_plain(region), f"con {_plain(region)}", f"the con {_plain(region)}"}:
        return True
    if re.search(r"\b(?:dien ly|dia tu|kp|float|khong fix|kho fix|fix kho|mat fix)\b", words):
        return True
    if region != "Việt Nam" and re.search(r"\b(?:khuyen nghi|tu van|khong che|bo tri)\b", words):
        return True
    caster_host = _get_env_preferring_dotenv("CORS_CUC_NTRIP_HOST", "vngeonet.vn").strip().lower()
    target = (re.search(r"\b(?:cuc|vngeonet)\b", words)
              or (caster_host and caster_host in normalized)
              or re.match(r"^cors\b", words))
    if not target:
        target = re.search(r"\bdo\b", words)
    if not target:
        return False
    if words in {"cuc", "cors", "cors cuc", "vngeonet"}:
        return True
    return bool(re.search(
        r"\b(?:tinh trang|tinh hinh|trang thai|the nao|ra sao|sap|song|chet|loi|"
        r"on|online|offline|hoat dong|ket noi|chay|kiem tra|check|status|mat|ngat|khuyen nghi|tu van|khong che|bo tri|dia hinh|"
        r"sao roi|bi gi|co van de|su co|dung (?:duoc|dc|dk)|"
        r"do (?:duoc|dc|dk|on|khong|ko|k)|co do|ok|down|up)\b",
        words,
    ))


# ---------------------------------------------------------------------------
# Vietnamese NLP intent patterns
# Each tuple: (compiled_regex, command_name)
# Patterns are matched against ASCII-normalized (diacritic-free) input.
# Order matters: more specific patterns first.
# ---------------------------------------------------------------------------
_NLP_INTENT_PATTERNS = [
    # === Rover: dangerous / mutating (Put Rover explicit matches first) ===
    (re.compile(r"^(?:xoa(?:\s+bo)?|delete)\s+rover\s+(.+)$", re.I), "roverdelete"),
    (re.compile(r"^(?:tao|create|them(?:\s+moi)?)\s+rover(?:\s+(.+))?$", re.I), "rovercreate"),
    (re.compile(r"^(?:sua|update|cap\s*nhat|chinh\s*sua)(?:\s+(?:cau\s*hinh|config))?\s+rover\s+(.+)$", re.I), "roverupdate"),
    (re.compile(r"^(?:gia\s*han(?:\s+them)?|renew)\s+rover\s+(.+)$", re.I), "roverrenew"),
    (re.compile(r"^(?:chan|block|khoa)\s+rover\s+(.+)$", re.I), "roverblock"),
    (re.compile(r"^(?:bo\s*chan|unblock|mo\s*chan|go\s*chan)\s+rover\s+(.+)$", re.I), "roverunblock"),

    # === Station: dangerous / mutating (Make "tram" optional but exclude "rover" matches) ===
    (re.compile(r"^(?:xoa(?:\s+bo)?|delete)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "stationdelete"),
    (re.compile(r"^(?:khoa|lock)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "stationlock"),
    (re.compile(r"^(?:mo\s*khoa|unlock|go\s*khoa)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "stationunlock"),
    (re.compile(r"^(?:reset|khoi\s*dong\s*lai|restart)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "stationreset"),

    # === Station: config / coord ===
    (re.compile(r"^(?:sua|chinh\s*sua|thay\s*doi|configset)(?:\s+(?:cau\s*hinh|config))?(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "configset"),
    (re.compile(r"^(?:xem|kiem\s*tra|doc|hien\s*thi)?\s*(?:cau\s*hinh|config)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "config"),
    (re.compile(r"^(?:dat\s+)?(?:toa\s*do|coord(?:inates?)?)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "coord"),

    # === Station: read ===
    (re.compile(r"^(?:xem|thong\s*tin|chi\s*tiet|info|kiem\s*tra)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "station"),
    (re.compile(r"^(?:tram|station)\s+(?!rover\s)(.+)$", re.I), "station"),
    (re.compile(r"^(?:danh\s*sach\s*(?:tram|station)|cac\s*tram|list\s*station)s?(?:\s+(.*))?$", re.I), "stations"),

    # === Rover: read ===
    (re.compile(r"^(?:xem|thong\s*tin|info|kiem\s*tra|cau\s*hinh|config)\s+rover\s+(.+)$", re.I), "rover"),
    (re.compile(r"^rover\s+(.+)$", re.I), "rover"),
    (re.compile(r"^(?:danh\s*sach\s*rover|cac\s*rover|list\s*rover)s?(?:\s+(.*))?$", re.I), "rovers"),

    # === General (no args or optional args) ===
    (re.compile(r"^(?:trang\s*thai|tinh\s*hinh|status|tong\s*quan|overview|toan\s*canh)$", re.I), "status"),
    (re.compile(r"^(?:loi|canh\s*bao|error|warning|alert|bao\s*loi)s?$", re.I), "errors"),
    (re.compile(r"^(?:tro\s*giup|huong\s*dan|help|ho\s*tro)$", re.I), "help"),
    (re.compile(r"^menu$", re.I), "menu"),
    (re.compile(r"^(?:thong\s*bao|notification)s?$", re.I), "notifications"),
    (re.compile(r"^(?:huy|cancel)(?:\s+(.+))?$", re.I), "cancel"),
    (re.compile(r"^(?:xac\s*nhan|confirm)(?:\s+(.+))?$", re.I), "confirm"),
    (re.compile(r"^(?:dang\s*xuat|logout|ngat\s*ket\s*noi)(?:\s+(.+))?$", re.I), "logout"),
]


class _SyntheticContext:
    """Minimal context for dispatching commands detected without '/' prefix."""
    def __init__(self, args: List[str]):
        self.args = args

SERVICE_CONFIG_CHOICES = {
    "1": ("server1_enabled", "Kích hoạt Server 1"),
    "2": ("server1_stream_on_demand", "S1: Ngủ khi không có rover"),
    "3": ("serverhost1", "S1: Địa chỉ máy chủ"),
    "4": ("port1", "S1: Cổng kết nối"),
    "5": ("mountpoint1", "S1: Mountpoint"),
    "6": ("username1", "S1: Tên đăng nhập"),
    "7": ("password1", "S1: Mật khẩu"),
    "8": ("ntrip_version1", "S1: Phiên bản NTRIP"),
    "9": ("server2_enabled", "Kích hoạt Server 2"),
    "10": ("server2_stream_on_demand", "S2: Ngủ khi không có rover"),
    "11": ("serverhost2", "S2: Địa chỉ máy chủ"),
    "12": ("port2", "S2: Cổng kết nối"),
    "13": ("mountpoint2", "S2: Mountpoint"),
    "14": ("username2", "S2: Tên đăng nhập"),
    "15": ("password2", "S2: Mật khẩu"),
    "16": ("ntrip_version2", "S2: Phiên bản NTRIP"),
    "17": ("reconnectioninterval", "Thời gian kết nối lại"),
    "18": ("rtcm_enabled", "Kích hoạt nhận RTCM"),
    "19": ("rtcmserver1", "RTCM: Địa chỉ máy chủ"),
    "20": ("rtcmport1", "RTCM: Cổng kết nối"),
    "21": ("rtcmmountpoint1", "RTCM: Mountpoint nguồn"),
    "22": ("rtcmusername1", "RTCM: Tên đăng nhập"),
    "23": ("rtcmpassword1", "RTCM: Mật khẩu"),
    "24": ("rtcmntripversion1", "RTCM: Phiên bản NTRIP"),
}

ROVER_UPDATE_CHOICES = {
    "1": ("password", "Password"),
    "2": ("mountpoints", "Mountpoints được phép"),
    "3": ("expiry_days", "Số ngày gia hạn"),
    "4": ("max_connections", "Số kết nối tối đa"),
    "5": ("show_position", "Hiển thị vị trí"),
    "6": ("rtcm_cycle_enabled", "Bật/tắt RTCM cycle"),
    "7": ("rtcm_disconnect_interval_seconds", "Chu kỳ ngắt RTCM (giây)"),
    "8": ("rtcm_disconnect_duration_seconds", "Thời gian ngắt RTCM (giây)"),
    "9": ("is_shared_with_managers", "Chia sẻ với quản lý"),
}

SERVICE_BOOLEAN_FIELDS = {
    "server1_enabled", "server1_stream_on_demand", "server2_enabled",
    "server2_stream_on_demand", "stream_on_demand", "rtcm_enabled",
}
ROVER_BOOLEAN_FIELDS = {"show_position", "rtcm_cycle_enabled", "is_shared_with_managers"}

ACTION_PERMISSIONS = {
    "overview": auth.Permission.VIEW_DEVICES,
    "alerts": auth.Permission.VIEW_CONFIG,
    "smart_search": auth.Permission.VIEW_DEVICES,
    "stations": auth.Permission.VIEW_DEVICES,
    "station": auth.Permission.VIEW_DEVICES,
    "config": auth.Permission.VIEW_CONFIG,
    "config_edit_context": auth.Permission.EDIT_SERVICE_CONFIG,
    "coord_set": auth.Permission.EDIT_COORDINATES,
    "config_set": auth.Permission.EDIT_SERVICE_CONFIG,
    "station_lock": auth.Permission.MANAGE_USERS,
    "station_unlock": auth.Permission.MANAGE_USERS,
    "station_reset": auth.Permission.DELETE_DEVICE,
    "station_delete": auth.Permission.DELETE_DEVICE,
    "rovers": auth.Permission.MANAGE_CASTER,
    "rover": auth.Permission.MANAGE_CASTER,
    "rover_edit_context": auth.Permission.MANAGE_CASTER,
    "rover_create": auth.Permission.MANAGE_CASTER,
    "rover_update": auth.Permission.MANAGE_CASTER,
    "rover_delete": auth.Permission.MANAGE_CASTER,
    "rover_renew": auth.Permission.MANAGE_CASTER,
    "rover_block": auth.Permission.MANAGE_CASTER,
    "rover_unblock": auth.Permission.MANAGE_CASTER,
}

DANGEROUS_ACTIONS = {
    "station_delete", "station_reset", "rover_delete", "rover_block",
    "security_block_ip", "security_disable", "license_bypass", "station_rename_serial",
}
