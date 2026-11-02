import logging
import base64
import math
import re
import time
from decimal import Decimal, InvalidOperation

UBLOX_SAVE_CONFIG = b'\xb5\x62\x06\x09\x0d\x00\x00\x00\x00\x00\xff\xff\x00\x00\x00\x00\x00\x00\x03\x1d\xab'

DEFAULT_GNSS_OPTIONS = {
    "constellations": {
        "gps": True,
        "glo": True,
        "gal": True,
        "bds": True,
        "qzss": False,
        "sbas": False,
    },
    "output_mode": "diagnostics_rtcm",
    "msm_level": 4,
    "ports": ["USB"],
    "interference_detection_enabled": True,
    "jamming_sensitivity_high": True,
}

UBLOX_NMEA_KEYS = {
    "GGA": {"UART1": 0x209100BB, "UART2": 0x209100BC, "USB": 0x209100BD},
    "GSA": {"UART1": 0x209100C0, "UART2": 0x209100C1, "USB": 0x209100C2},
    "GSV": {"UART1": 0x209100C5, "UART2": 0x209100C6, "USB": 0x209100C7},
    "GST": {"UART1": 0x209100D4, "UART2": 0x209100D5, "USB": 0x209100D6},
    "DTM": {"UART1": 0x209100A7, "UART2": 0x209100A8, "USB": 0x209100A9},
    "RMC": {"UART1": 0x209100AC, "UART2": 0x209100AD, "USB": 0x209100AE},
    "GNS": {"UART1": 0x209100B6, "UART2": 0x209100B7, "USB": 0x209100B8},
    "VTG": {"UART1": 0x209100B1, "UART2": 0x209100B2, "USB": 0x209100B3},
    "GLL": {"UART1": 0x209100CA, "UART2": 0x209100CB, "USB": 0x209100CC},
    "GRS": {"UART1": 0x209100CF, "UART2": 0x209100D0, "USB": 0x209100D1},
    "ZDA": {"UART1": 0x209100D9, "UART2": 0x209100DA, "USB": 0x209100DB},
    "GBS": {"UART1": 0x209100DE, "UART2": 0x209100DF, "USB": 0x209100E0},
    "VLW": {"UART1": 0x209100E8, "UART2": 0x209100E9, "USB": 0x209100EA},
    "PUBX00": {"UART1": 0x209100ED, "UART2": 0x209100EE, "USB": 0x209100EF},
    "PUBX03": {"UART1": 0x209100F2, "UART2": 0x209100F3, "USB": 0x209100F4},
    "PUBX04": {"UART1": 0x209100F7, "UART2": 0x209100F8, "USB": 0x209100F9},
}

UBLOX_NAV_KEYS = {
    "NAV_PVT": {"UART1": 0x20910007, "UART2": 0x20910008, "USB": 0x20910009},
    "NAV_HPPOSLLH": {"UART1": 0x20910034, "UART2": 0x20910035, "USB": 0x20910036},
}

# Message rates and output protocols are independent on u-blox receivers. A
# port left in RTCM-only mode keeps NMEA silent even when GGA/GSV/GST rates are
# subsequently enabled, unless its OUTPROT-NMEA key is enabled as well.
UBLOX_OUTPUT_PROTOCOL_KEYS = {
    "UART1": {"UBX": 0x10740001, "NMEA": 0x10740002, "RTCM3X": 0x10740004},
    "UART2": {"UBX": 0x10760001, "NMEA": 0x10760002, "RTCM3X": 0x10760004},
    "USB": {"UBX": 0x10780001, "NMEA": 0x10780002, "RTCM3X": 0x10780004},
}

UBLOX_RTCM_KEYS = {
    "1005": {"UART1": 0x209102BE, "UART2": 0x209102BF, "USB": 0x209102C0},
    "1074": {"UART1": 0x2091035F, "UART2": 0x20910360, "USB": 0x20910361},  # GPS MSM4
    "1084": {"UART1": 0x20910364, "UART2": 0x20910365, "USB": 0x20910366},  # GLONASS MSM4
    "1094": {"UART1": 0x20910369, "UART2": 0x2091036A, "USB": 0x2091036B},  # Galileo MSM4
    "1124": {"UART1": 0x2091036E, "UART2": 0x2091036F, "USB": 0x20910370},  # BeiDou MSM4
    
    "1077": {"UART1": 0x209102CD, "UART2": 0x209102CE, "USB": 0x209102CF},  # GPS MSM7
    "1087": {"UART1": 0x209102D2, "UART2": 0x209102D3, "USB": 0x209102D4},  # GLONASS MSM7
    "1097": {"UART1": 0x20910319, "UART2": 0x2091031A, "USB": 0x2091031B},  # Galileo MSM7
    "1127": {"UART1": 0x209102D7, "UART2": 0x209102D8, "USB": 0x209102D9},  # BeiDou MSM7
    
    "1230": {"UART1": 0x20910304, "UART2": 0x20910305, "USB": 0x20910306},  # GLONASS bias
}

UNICORE_NMEA_BY_CONSTELLATION = {
    "gps": ("gpgga", "gpgsa", "gpgsv", "gpgst"),
    "glo": ("glgga", "glgsa", "glgsv"),
    "gal": ("gagga", "gagsa", "gagsv"),
    "bds": ("bdgga", "bdgsa", "bdgsv"),
}

UNICORE_RTCM_BY_CONSTELLATION = {
    "gps": ("1074", "1077", "1019"),
    "glo": ("1084", "1087", "1020"),
    "gal": ("1094", "1097", "1045"),
    "bds": ("1124", "1127", "1042"),
    "qzss": ("1114", "1117", "1044"),
}


def _ubx_packet(msg_class: int, msg_id: int, payload: bytes) -> bytes:
    header = b'\xb5\x62' + bytes([msg_class, msg_id]) + len(payload).to_bytes(2, 'little')
    ck_a, ck_b = 0, 0
    for byte in header[2:] + payload:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return header + payload + bytes([ck_a, ck_b])


def select_ublox_profile(telemetry: dict, capabilities: dict) -> dict:
    """Use only agent-reported MON-VER, never a requested firmware from the UI."""
    info = telemetry.get('gnss_receiver') or {}
    try:
        age = float(info['age_seconds']) + max(0, time.time() - float(telemetry.get('server_received_at') or telemetry.get('timestamp') or 0))
        raw = base64.b64decode(info['payload_b64'], validate=True)
        if info.get('source') != 'ubx_mon_ver' or not 0 <= age <= 120 or not 40 <= len(raw) <= 520 or (len(raw)-40) % 30:
            raise ValueError()
        extensions = [raw[i:i+30].split(b'\0',1)[0].decode('ascii') for i in range(40,len(raw),30)]
        versions = [re.fullmatch(r'FWVER=HPG (1\.(?:13|30|32|50|51))', item) for item in extensions]
        versions = [match.group(1) for match in versions if match]
        if len(versions) != 1 or 'MOD=ZED-F9P' not in extensions:
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ValueError('Chưa có MON-VER ZED-F9P còn hạn hoặc firmware chưa được hỗ trợ. Đọc lại thông tin chip trước khi áp dụng.') from None
    modern = capabilities.get('gnss_valset_supported') is True
    itfm = versions[0] not in ('1.50','1.51')
    if not modern and not itfm:
        raise ValueError('Cập nhật agent để áp dụng cấu hình cho HPG 1.50/1.51 bằng VALSET/VALGET.')
    return dict(firmware='HPG '+versions[0], modern=modern, itfm=itfm)


def _ubx_tmode_entries(mode: bytes) -> dict[int, bytes]:
    # Internal canonical TMODE representation, not a legacy command sent to chip.
    entries = {0x20030001: mode[2:3], 0x20030002: bytes([mode[3] & 1])}
    for key, offset, size in ((0x40030009,4,4),(0x4003000a,8,4),(0x4003000b,12,4),
                              (0x2003000c,16,1),(0x2003000d,17,1),(0x2003000e,18,1),
                              (0x4003000f,20,4),(0x40030010,24,4),(0x40030011,28,4)):
        entries[key] = mode[offset:offset+size]
    return entries


def _ubx_base_commands(mode: bytes, options: dict | None, profile: dict | None) -> list[bytes]:
    modern = bool(profile and profile['modern'])
    commands = [_ubx_packet(6,0x8a,b'\0\x07\0\0'+b''.join(key.to_bytes(4,'little')+value for key,value in _ubx_tmode_entries(mode).items()))
                if modern else _ubx_packet(6,0x71,mode),
                build_ublox_output_config_command(options, modern=modern)]
    if profile is None or profile['itfm']:
        commands.append(_ubx_cfg_valset_u1({0x1041000D: normalize_gnss_options(options)['interference_detection_enabled']}))
    else:
        commands.append(_ubx_cfg_valset_u1({0x10F60051: normalize_gnss_options(options)['jamming_sensitivity_high']}))
    if not modern:
        commands.append(UBLOX_SAVE_CONFIG)
    return commands


def _ubx_cfg_valset_u1(entries: dict[int, int], layers: int = 0x07) -> bytes:
    # UBX-22008968: at most 64 key/value pairs per VALSET message.
    items = list(entries.items())
    packets = []
    for offset in range(0, len(items), 64):
        payload = bytearray([0x00, layers & 0x07, 0x00, 0x00])
        for key, value in items[offset:offset + 64]:
            payload.extend(int(key).to_bytes(4, 'little'))
            payload.append(1 if value else 0)
        packets.append(_ubx_packet(0x06, 0x8A, bytes(payload)))
    return b''.join(packets)

def _ubx_cfg_msg(msg_class: int, msg_id: int, ports: list[str], rate: int = 1) -> bytes:
    payload = bytearray([msg_class, msg_id, 0, 0, 0, 0, 0, 0])
    if "I2C" in ports: payload[2] = rate
    if "UART1" in ports: payload[3] = rate
    if "UART2" in ports: payload[4] = rate
    if "USB" in ports: payload[5] = rate
    if "SPI" in ports: payload[6] = rate
    return _ubx_packet(0x06, 0x01, bytes(payload))


def normalize_gnss_options(options: dict | None) -> dict:
    raw = options if isinstance(options, dict) else {}
    interference_enabled = raw.get("interference_detection_enabled", True)
    if not isinstance(interference_enabled, bool):
        raise ValueError("Phát hiện nhiễu GNSS phải là bật hoặc tắt.")
    sensitivity_high = raw.get("jamming_sensitivity_high", True)
    if not isinstance(sensitivity_high, bool):
        raise ValueError("Độ nhạy phát hiện nhiễu phải là cao hoặc thường.")
    constellations = dict(DEFAULT_GNSS_OPTIONS["constellations"])
    for key, value in (raw.get("constellations") or {}).items():
        normalized_key = str(key).strip().lower()
        if normalized_key in constellations:
            constellations[normalized_key] = bool(value)
    if not any(constellations.values()):
        constellations["gps"] = True

    output_mode = str(raw.get("output_mode") or DEFAULT_GNSS_OPTIONS["output_mode"]).strip().lower()
    if output_mode not in {"diagnostics_rtcm", "rtcm_only"}:
        output_mode = DEFAULT_GNSS_OPTIONS["output_mode"]

    try:
        raw_msm_level = raw.get("msm_level") or DEFAULT_GNSS_OPTIONS["msm_level"]
        msm_level = "both" if str(raw_msm_level).strip().lower() == "both" else int(raw_msm_level)
    except (TypeError, ValueError):
        msm_level = DEFAULT_GNSS_OPTIONS["msm_level"]
    if msm_level not in {4, 7, "both"}:
        msm_level = DEFAULT_GNSS_OPTIONS["msm_level"]
    raw_ports = raw.get("ports") or DEFAULT_GNSS_OPTIONS["ports"]
    if isinstance(raw_ports, str):
        raw_ports = [raw_ports]
    ports = []
    for port in raw_ports:
        normalized_port = str(port).strip().upper()
        if normalized_port in {"UART1", "UART2", "USB"} and normalized_port not in ports:
            ports.append(normalized_port)
    if not ports:
        ports = list(DEFAULT_GNSS_OPTIONS["ports"])

    return {
        "constellations": constellations,
        "output_mode": output_mode,
        "msm_level": msm_level,
        "ports": ports,
        "interference_detection_enabled": interference_enabled,
        "jamming_sensitivity_high": sensitivity_high,
    }


def build_ublox_output_config_command(gnss_options: dict | None = None, *, modern: bool = False) -> bytes:
    opts = normalize_gnss_options(gnss_options)
    constellations = opts["constellations"]
    output_mode = opts["output_mode"]
    msm_level = opts["msm_level"]
    ports = opts["ports"]
    diagnostics_enabled = output_mode != "rtcm_only"
    entries: dict[int, int] = {}

    for port in ports:
        protocol_keys = UBLOX_OUTPUT_PROTOCOL_KEYS[port]
        entries[protocol_keys["UBX"]] = 1
        entries[protocol_keys["NMEA"]] = 1 if diagnostics_enabled else 0
        entries[protocol_keys["RTCM3X"]] = 1

    if modern:
        for port in ports:
            entries[{'UART1': 0x10520005, 'UART2': 0x10530005, 'USB': 0x10650001}[port]] = 1
        # F9P 1.51 requires identical RTCM configuration on every emitting port.
        for port, prefix in (('I2C',0x10720000),('UART1',0x10740000),('UART2',0x10760000),('USB',0x10780000),('SPI',0x107a0000)):
            if port not in ports:
                entries[prefix | 4] = 0

    for message, keys_by_port in UBLOX_NMEA_KEYS.items():
        enabled = diagnostics_enabled and message in {"GGA", "GSA", "GSV", "GST"}
        for port in ports:
            entries[keys_by_port[port]] = 1 if enabled else 0

    for keys_by_port in UBLOX_NAV_KEYS.values():
        for port in ports:
            entries[keys_by_port[port]] = 1 if diagnostics_enabled else 0

    rtcm_enabled = {
        "1005": True,
        "1074": msm_level in {4, "both"} and constellations["gps"],
        "1084": msm_level in {4, "both"} and constellations["glo"],
        "1094": msm_level in {4, "both"} and constellations["gal"],
        "1124": msm_level in {4, "both"} and constellations["bds"],
        "1077": msm_level in {7, "both"} and constellations["gps"],
        "1087": msm_level in {7, "both"} and constellations["glo"],
        "1097": msm_level in {7, "both"} and constellations["gal"],
        "1127": msm_level in {7, "both"} and constellations["bds"],
        "1230": constellations["glo"],
    }
    for message, keys_by_port in UBLOX_RTCM_KEYS.items():
        for port in ports:
            entries[keys_by_port[port]] = 1 if rtcm_enabled.get(message, False) else 0

    command = _ubx_cfg_valset_u1(entries)

    if modern:
        return command

    # Legacy CFG-MSG fallback must explicitly configure both MSM families.
    # Otherwise a message enabled by an older setup (for example MSM7) keeps
    # streaming after the user switches to MSM4, causing duplicate/conflicting
    # observations on receivers that use CFG-MSG instead of VALSET.
    msm_ids = {
        4: {"gps": 0x4A, "glo": 0x54, "gal": 0x5E, "bds": 0x7C},
        7: {"gps": 0x4D, "glo": 0x57, "gal": 0x61, "bds": 0x7F},
    }
    for level, ids_by_constellation in msm_ids.items():
        level_enabled = msm_level == "both" or msm_level == level
        for constellation, msg_id in ids_by_constellation.items():
            enabled = level_enabled and constellations[constellation]
            command += _ubx_cfg_msg(0xF5, msg_id, ports if enabled else [], rate=1 if enabled else 0)
    return command


def build_ublox_verification_payload(config: dict, *, route_to_uart1: bool = False, receiver_profile: dict | None = None) -> dict:
    """Expected live configuration on the agent's configured output ports.

    Only constructs comparison data; the verifier sends poll requests, never
    replays these configuration packets to the receiver.
    """
    if not isinstance(config, dict) or config.get("base_setup_method") != "FIXED_LLA":
        raise ValueError("Chọn và lưu Fixed LLA trước khi xác minh cấu hình.")
    coords = config.get("coords") or {}
    if not isinstance(coords, dict) or not all(key in coords for key in ("lat", "lon", "alt")):
        raise ValueError("Chưa có đủ tọa độ Fixed LLA đã lưu.")
    # ESP32 routes USB/UART2 output requests to the GNSS UART1 connection.
    options = normalize_gnss_options(config.get("gnss_options"))
    if route_to_uart1:
        options["ports"] = ["UART1"]
    packets = build_base_fixed_lla_command(
        "Ublox", coords["lat"], coords["lon"], coords["alt"],
        config.get("accuracy", 10.0), options,
        altitude_reference=config.get("altitude_reference"),
    )
    if receiver_profile and receiver_profile['modern']:
        packets[1] = build_ublox_output_config_command(options, modern=True)
    output = bytearray()
    offset = 0
    while offset < len(packets[1]):
        length = int.from_bytes(packets[1][offset + 4:offset + 6], "little")
        if packets[1][offset + 2:offset + 4] == b'\x06\x8a':
            output.extend(packets[1][offset + 10:offset + 6 + length])
        offset += length + 8
    desired = {int.from_bytes(output[i:i + 4], 'little'): output[i + 4] for i in range(0, len(output), 5)}
    fields = ['UBX', 'NMEA', 'RTCM3X']
    message_keys = []
    for prefix, table in (('NMEA ', UBLOX_NMEA_KEYS), ('UBX ', UBLOX_NAV_KEYS), ('RTCM ', UBLOX_RTCM_KEYS)):
        for name, keys in table.items():
            fields.append(prefix + name.removeprefix('NAV_'))
            message_keys.append(keys['UART1'])
    inspection_ports = []
    enabled_keys = {'I2C': 0x10510003, 'UART1': 0x10520005, 'UART2': 0x10530005, 'USB': 0x10650001, 'SPI': 0x10640006}
    for port, delta, protocol in (('I2C', -1, 0x10720000), ('UART1', 0, 0x10740000),
                                  ('UART2', 1, 0x10760000), ('USB', 2, 0x10780000), ('SPI', 3, 0x107A0000)):
        keys = [protocol | suffix for suffix in (1, 2, 4)] + [key + delta for key in message_keys] + [enabled_keys[port]]
        records = b''.join(key.to_bytes(4, 'little') + b'\0' for key in keys)
        inspection_ports.append({'name': port, 'output_b64': base64.b64encode(records).decode('ascii')})
        if port == options['ports'][0]:
            expected_values = [desired[key] for key in keys[:-1]] + [1]
    return {
        "tmode_valget": bool(receiver_profile and receiver_profile["modern"]),
        "receiver_profile": receiver_profile,
        "tmode_b64": base64.b64encode(packets[0][6:-2]).decode("ascii"),
        "output_b64": base64.b64encode(output).decode("ascii"),
        "readback": {"fields": fields + ['PORT_ENABLED'], "expected": expected_values, "compare_ports": options['ports'], "ports": inspection_ports},
    }


def build_unicore_verification_payload(config: dict, *, current_port: bool = False) -> dict:
    if not isinstance(config, dict) or config.get("base_setup_method") != "FIXED_LLA":
        raise ValueError("Chọn và lưu Fixed LLA trước khi xác minh cấu hình.")
    coords = config.get("coords") or {}
    if not isinstance(coords, dict) or not all(key in coords for key in ("lat", "lon", "alt")):
        raise ValueError("Chưa có đủ tọa độ Fixed LLA đã lưu.")
    packets = build_base_fixed_lla_command(
        "Unicorecomm", coords["lat"], coords["lon"], coords["alt"],
        config.get("accuracy", 10.0), config.get("gnss_options"),
        altitude_reference=config.get("altitude_reference"),
    )
    logs = []
    for packet in packets:
        fields = packet.decode("ascii").strip().upper().split()
        if len(fields) == 3 and fields[1:] == ["COM3", "1"]:
            logs.append(fields[0])
    normalized_coords = {key: format(_coordinate_decimal(coords[key], key, low, high), 'f')
                         for key, low, high in (("lat", -90, 90), ("lon", -180, 180), ("alt", -12000, 100000))}
    return {"coords": normalized_coords, "altitude_reference": config.get("altitude_reference") or "LEGACY",
            "logs": logs, "port": "CURRENT" if current_port else "COM3"}


def _append_unicore_output_commands(commands: list[bytes], port: str, gnss_options: dict | None = None) -> None:
    opts = normalize_gnss_options(gnss_options)
    constellations = opts["constellations"]
    diagnostics_enabled = opts["output_mode"] != "rtcm_only"
    msm_level = opts["msm_level"]

    if diagnostics_enabled:
        for constellation, messages in UNICORE_NMEA_BY_CONSTELLATION.items():
            if not constellations.get(constellation):
                continue
            for message in messages:
                commands.append(f'{message} {port} 1\r\n'.encode('ascii'))
                commands.append(b'$DELAY_200$')

    rtcm_msgs = ["1006", "1033"]
    for constellation, messages in UNICORE_RTCM_BY_CONSTELLATION.items():
        if constellations.get(constellation):
            rtcm_msgs.extend(
                message for message in messages
                if (msm_level == "both" and message[-1:] in {"4", "7"})
                or message.endswith(str(msm_level))
                or message in {"1019", "1020", "1042", "1044", "1045"}
            )
    for message in dict.fromkeys(rtcm_msgs):
        commands.append(f'rtcm{message} {port} 1\r\n'.encode('ascii'))
        commands.append(b'$DELAY_200$')


def _bounded_finite_number(value, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def _coordinate_decimal(value, name: str, minimum: int, maximum: int) -> Decimal:
    try:
        number = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not number.is_finite() or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def build_base_survey_in_command(sensor_type: str, duration: int, accuracy: float, gnss_options: dict | None = None, *, receiver_profile: dict | None = None) -> list[bytes]:
    commands = []
    duration_value = _bounded_finite_number(duration, "Survey duration", 1, 604800)
    if not duration_value.is_integer():
        raise ValueError("Survey duration must be an integer number of seconds")
    duration = int(duration_value)
    accuracy = _bounded_finite_number(accuracy, "Survey accuracy", 0.001, 10000)
    
    if sensor_type == 'Ublox':
        # Tạo message với cấu trúc chính xác
        message = bytearray(b'\xb5\x62\x06\x71\x28\x00' + b'\x00' * 42)
        
        # Byte 8: Mode = 1 (Survey-In)
        message[8] = 1
        
        # Chuyển đổi duration và accuracy (ép kiểu float đề phòng dữ liệu là string)
        svinMinDur_bytes = int(duration).to_bytes(4, byteorder='little')
        svinAccLimit_bytes = int(float(accuracy) * 10000).to_bytes(4, byteorder='little')
        
        # Ghi vào vị trí đúng
        for i in range(4):
            message[30 + i] = svinMinDur_bytes[i]
            message[34 + i] = svinAccLimit_bytes[i]
        
        # Tính checksum
        CK_A, CK_B = 0, 0
        for i in range(2, 46):
            CK_A = (CK_A + message[i]) & 0xff
            CK_B = (CK_B + CK_A) & 0xff
        
        message[46] = CK_A
        message[47] = CK_B
        
        commands.extend(_ubx_base_commands(bytes(message[6:-2]), gnss_options, receiver_profile))

    elif sensor_type == 'Unicorecomm':
        # BƯỚC 1: Clear all logs (TẮT TẤT CẢ OUTPUT)
        commands.append(b'unlogall\r\n')
        commands.append(b'$DELAY_1000$')
        
        # BƯỚC 2: Set base mode (Survey-In)
        commands.append(b'config undulation 0.0\r\n')
        cmd_str = f'mode base time {duration} 0\r\n'
        logging.info(f"🔍 Survey-In command: {repr(cmd_str)}")
        commands.append(cmd_str.encode('ascii'))
        commands.append(b'$DELAY_2000$')
        
        logging.info("Configuring Unicore output messages on COM3")
        _append_unicore_output_commands(commands, "com3", gnss_options)
        
        # BƯỚC 5: Save config
        commands.append(b'saveconfig\r\n')
        
    return commands


def build_base_fixed_lla_command(sensor_type: str, lat: float, lon: float, alt: float, accuracy: float, gnss_options: dict | None = None, *, altitude_reference: str | None = None, receiver_profile: dict | None = None) -> list[bytes]:
    commands = []
    if altitude_reference not in (None, 'LEGACY', 'ELLIPSOID'):
        raise ValueError('Unsupported altitude reference')
    lat = _coordinate_decimal(lat, "Latitude", -90, 90)
    lon = _coordinate_decimal(lon, "Longitude", -180, 180)
    alt = _coordinate_decimal(alt, "Altitude", -12000, 100000)
    accuracy = _coordinate_decimal(accuracy, "Fixed position accuracy", Decimal("0.0001"), 10000)
    
    if sensor_type == 'Ublox':
        message = bytearray(b'\xb5\x62\x06\x71\x28\x00' + b'\x00' * 42)
        
        # Byte 8: Mode = 2 (Fixed)
        message[8] = 2
        # Byte 9: LLA mode = 1
        message[9] = 1
        
        # Keep decimal input exact until quantizing to UBX's integer/HP fields.
        f_lat = lat
        f_lon = lon
        f_alt = alt
        f_acc = accuracy

        multiplier = 10000000  # LLA multiplier
        
        # === XỬ LÝ LATITUDE ===
        XOrLat_int = int(f_lat * multiplier)
        XOrLat_hp_value = int(f_lat * multiplier * 100) - XOrLat_int * 100
        XOrLat_hp_bytes = XOrLat_hp_value.to_bytes(1, byteorder='little', signed=True)
        XOrLat_bytes = XOrLat_int.to_bytes(4, byteorder='little', signed=True)
        
        # === XỬ LÝ LONGITUDE ===
        YOrLon_int = int(f_lon * multiplier)
        YOrLon_hp_value = int(f_lon * multiplier * 100) - YOrLon_int * 100
        YOrLon_hp_bytes = YOrLon_hp_value.to_bytes(1, byteorder='little', signed=True)
        YOrLon_bytes = YOrLon_int.to_bytes(4, byteorder='little', signed=True)
        
        # === XỬ LÝ ALTITUDE ===
        ZOrAlt_int = int(f_alt * 100)
        ZOrAlt_hp_value = int(f_alt * 10000) - ZOrAlt_int * 100
        ZOrAlt_hp_bytes = ZOrAlt_hp_value.to_bytes(1, byteorder='little', signed=True)
        ZOrAlt_bytes = ZOrAlt_int.to_bytes(4, byteorder='little', signed=True)
        
        # Fixed Position Accuracy
        fixedPosAcc_bytes = int(f_acc * 10000).to_bytes(4, byteorder='little')
        
        # Ghi vào message
        for i in range(4):
            message[10 + i] = XOrLat_bytes[i]
            message[14 + i] = YOrLon_bytes[i]
            message[18 + i] = ZOrAlt_bytes[i]
            message[26 + i] = fixedPosAcc_bytes[i]
        
        # Ghi HP bytes
        message[22] = XOrLat_hp_bytes[0]
        message[23] = YOrLon_hp_bytes[0]
        message[24] = ZOrAlt_hp_bytes[0]
        
        # Tính checksum
        CK_A, CK_B = 0, 0
        for i in range(2, 46):
            CK_A = (CK_A + message[i]) & 0xff
            CK_B = (CK_B + CK_A) & 0xff
        
        message[46] = CK_A
        message[47] = CK_B
        
        commands.extend(_ubx_base_commands(bytes(message[6:-2]), gnss_options, receiver_profile))

    elif sensor_type == 'Unicorecomm':
        # BƯỚC 1: Clear all logs (TẮT TẤT CẢ OUTPUT)
        commands.append(b'unlogall\r\n')
        commands.append(b'$DELAY_1000$')
        
        # BƯỚC 2: Set fixed position - GIỮ NGUYÊN FORMAT NGƯỜI DÙNG NHẬP
        lat_str = format(lat, 'f')
        lon_str = format(lon, 'f')
        alt_str = format(alt, 'f')

        # MODE BASE otherwise interprets altitude using the receiver's undulation.
        # Missing metadata preserves legacy configurations instead of reinterpreting them.
        if altitude_reference == 'ELLIPSOID':
            commands.append(b'config undulation 0.0\r\n')
        
        cmd_str = f'mode base {lat_str} {lon_str} {alt_str}\r\n'
        logging.info(f"🔍 Fixed position command: {repr(cmd_str)}")
        commands.append(cmd_str.encode('ascii'))
        commands.append(b'$DELAY_2000$')
        logging.info("Configuring Unicore output messages on COM3")
        _append_unicore_output_commands(commands, "com3", gnss_options)
        
        commands.append(b'saveconfig\r\n')
        
    return commands


def build_lte_unicore_config(setup_method: str, duration: int = 60, lat: float = 0, lon: float = 0, alt: float = 0, *, altitude_reference: str | None = None, gnss_options: dict | None = None) -> list[bytes]:
    """
    Hàm chuyên biệt dành riêng cho chip Unicorecomm LTE.
    Sử dụng mặc định cổng COM2 và chuỗi lệnh tối ưu theo yêu cầu người dùng.
    Trình tự: FRESET -> unlogall -> mode base -> output profile -> saveconfig
    """
    if altitude_reference not in (None, 'LEGACY', 'ELLIPSOID'):
        raise ValueError('Unsupported altitude reference')
    commands = []
    port = "com2"
    
    # 1. Reset vật lý chip và xóa hết log cũ
    commands.append(b'FRESET\r\n')
    commands.append(b'unlogall\r\n')
    
    # 2. Cấu hình chế độ Base (Survey-In hoặc Fixed)
    if setup_method.upper() == 'SURVEY_IN':
        commands.append(b'config undulation 0.0\r\n')
        commands.append(f'mode base time {duration} 0\r\n'.encode('ascii'))
    else:
        # Giữ nguyên độ chính xác của tọa độ
        if altitude_reference == 'ELLIPSOID':
            commands.append(b'config undulation 0.0\r\n')
        commands.append(f'mode base {lat} {lon} {alt}\r\n'.encode('ascii'))
    
    # Preserve the old profile when an installation has no saved options.
    raw = gnss_options if isinstance(gnss_options, dict) else {}
    options = {"output_mode": "rtcm_only", "msm_level": "both", **raw}
    options["constellations"] = {
        **dict.fromkeys(("gps", "glo", "gal", "bds", "qzss", "sbas"), True),
        **(raw.get("constellations") or {}),
    }
    if not any(options["constellations"][key] for key in ("gps", "glo", "gal", "bds", "qzss")):
        options["constellations"]["gps"] = True
    _append_unicore_output_commands(commands, port, options)
    
    # 5. Lưu cấu hình
    commands.append(b'saveconfig\r\n')
    
    return commands


# === HÀM DEBUG ===
def debug_command(command_bytes: bytes) -> str:
    """In ra chuỗi byte dưới dạng hex để debug"""
    return ' '.join(f'{b:02x}' for b in command_bytes)
