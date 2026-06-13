from __future__ import annotations

import math
import time
from typing import Any, NamedTuple


CRC24Q_POLY = 0x1864CFB
MAX_BUFFER_BYTES = 65536

# Ephemeris Constants
MU_GPS   = 3.9860050E14     # gravitational constant
MU_GAL   = 3.986004418E14   # galileo
MU_CMP   = 3.986004418E14   # beidou
OMGE     = 7.2921151467E-5  # earth angular velocity (IS-GPS)
OMGE_GAL = 7.2921151467E-5  # earth angular velocity (Galileo)
OMGE_CMP = 7.292115E-5      # earth angular velocity (BeiDou)
RTOL_KEPLER = 1E-13         # relative tolerance for Kepler equation
MAX_ITER_KEPLER = 30        # max number of iterations for Kepler


def _build_crc24q_table() -> list[int]:
    table: list[int] = []
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= CRC24Q_POLY
        table.append(crc & 0xFFFFFF)
    return table


CRC24Q_TABLE = _build_crc24q_table()


def crc24q(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ CRC24Q_TABLE[((crc >> 16) ^ byte) & 0xFF]) & 0xFFFFFF
    return crc


class BitReader:
    def __init__(self, data: bytes, bit_index: int = 0) -> None:
        self.data = data
        self.bit_index = bit_index

    def read_uint(self, bits: int) -> int:
        value = 0
        for _ in range(bits):
            byte_index = self.bit_index >> 3
            bit_offset = 7 - (self.bit_index & 7)
            self.bit_index += 1
            value = (value << 1) | ((self.data[byte_index] >> bit_offset) & 1)
        return value

    def read_int(self, bits: int) -> int:
        value = self.read_uint(bits)
        if value & (1 << (bits - 1)):
            value -= (1 << bits)
        return value

    def skip(self, bits: int) -> None:
        self.bit_index += bits


class MsmTypeInfo(NamedTuple):
    base: int
    system: str
    nav_system: str


MSM_TYPES = (
    MsmTypeInfo(1071, "us", "GPS"),
    MsmTypeInfo(1081, "ru", "GLONASS"),
    MsmTypeInfo(1091, "eu", "GALILEO"),
    MsmTypeInfo(1101, "un", "SBAS"),
    MsmTypeInfo(1111, "jp", "QZSS"),
    MsmTypeInfo(1121, "cn", "BEIDOU"),
    MsmTypeInfo(1131, "un", "IRNSS"),
)


def _message_type_info(message_type: int) -> tuple[MsmTypeInfo, int] | None:
    for info in MSM_TYPES:
        msm_level = message_type - info.base + 1
        if 1 <= msm_level <= 7:
            return info, msm_level
    return None


def _active_ids(mask: int, width: int) -> list[int]:
    return [idx for idx in range(1, width + 1) if (mask >> (width - idx)) & 1]


class RTCMSignalDecoder:
    def __init__(self) -> None:
        self._buffers: dict[str, bytes] = {}
        self._base_positions: dict[str, tuple[float, float, float]] = {}
        self._ephemeris_data: dict[str, dict[str, dict[str, Any]]] = {}
        self._azel_cache: dict[str, dict[str, tuple[float, float, float]]] = {}


    @staticmethod
    def _glonass_eph2pos(eph: dict[str, Any], time_s: float) -> tuple[float, float, float] | None:
        import time
        # time_s is gps epoch in ms / 1000, but GLONASS uses UTC/Moscow time.
        # It's easier to just use the current time because RTCM packets are real-time.
        now = time.time()
        utc_tod = now % 86400.0
        dt = utc_tod - eph['tb']
        if dt > 43200: dt -= 86400
        elif dt < -43200: dt += 86400
        
        pos = list(eph['pos'])
        vel = list(eph['vel'])
        acc = eph['acc']
        
        GM = 398600.4418e9
        J2 = 1.0826257e-3
        ae = 6378136.0
        
        def derivatives(p, v):
            x, y, z = p
            vx, vy, vz = v
            r2 = x*x + y*y + z*z
            r = math.sqrt(r2)
            r3 = r2 * r
            a = 1.5 * J2 * GM * (ae**2) / (r2 * r3)
            b = 5.0 * z*z / r2
            c = -GM / r3 - a * (1.0 - b)
            
            w = 7.292115e-5
            
            ax = c * x + 2.0 * w * vy + w*w * x + acc[0]
            ay = c * y - 2.0 * w * vx + w*w * y + acc[1]
            az = -GM * z / r3 - a * (3.0 - b) * z + acc[2]
            return [vx, vy, vz], [ax, ay, az]
            
        step = 60.0
        sgn = 1.0 if dt > 0 else -1.0
        rem = abs(dt)
        
        while rem > 0:
            h = step if rem > step else rem
            h *= sgn
            
            dp1, dv1 = derivatives(pos, vel)
            p_k1 = [pos[i] + dp1[i]*h/2 for i in range(3)]
            v_k1 = [vel[i] + dv1[i]*h/2 for i in range(3)]
            
            dp2, dv2 = derivatives(p_k1, v_k1)
            p_k2 = [pos[i] + dp2[i]*h/2 for i in range(3)]
            v_k2 = [vel[i] + dv2[i]*h/2 for i in range(3)]
            
            dp3, dv3 = derivatives(p_k2, v_k2)
            p_k3 = [pos[i] + dp3[i]*h for i in range(3)]
            v_k3 = [vel[i] + dv3[i]*h for i in range(3)]
            
            dp4, dv4 = derivatives(p_k3, v_k3)
            
            for i in range(3):
                pos[i] += h * (dp1[i] + 2*dp2[i] + 2*dp3[i] + dp4[i]) / 6.0
                vel[i] += h * (dv1[i] + 2*dv2[i] + 2*dv3[i] + dv4[i]) / 6.0
                
            rem -= abs(h)
            
        return pos[0], pos[1], pos[2]

    @staticmethod
    def _eph2pos(eph: dict[str, float], time_s: float, sys_name: str) -> tuple[float, float, float] | None:
        if sys_name == 'BEIDOU':
            MU = MU_CMP
            OMGE_SYS = OMGE_CMP
        elif sys_name == 'GALILEO':
            MU = MU_GAL
            OMGE_SYS = OMGE_GAL
        else:
            MU = MU_GPS
            OMGE_SYS = OMGE

        tk = time_s - eph['toe']
        if tk > 302400.0: tk -= 604800.0
        elif tk < -302400.0: tk += 604800.0

        A = eph.get('A', 0)
        if A <= 0.0:
            return None

        M = eph['M0'] + (math.sqrt(MU / (A**3)) + eph['deln']) * tk
        E = M
        for _ in range(MAX_ITER_KEPLER):
            Ek = E
            E = M + eph['e'] * math.sin(Ek)
            if abs(E - Ek) < RTOL_KEPLER:
                break

        sinE = math.sin(E)
        cosE = math.cos(E)
        nu = math.atan2(math.sqrt(1.0 - eph['e']**2) * sinE, cosE - eph['e'])
        phi = nu + eph['omg']
        sin2phi = math.sin(2.0 * phi)
        cos2phi = math.cos(2.0 * phi)

        u = phi + eph['cuc'] * cos2phi + eph['cus'] * sin2phi
        r = A * (1.0 - eph['e'] * cosE) + eph['crc'] * cos2phi + eph['crs'] * sin2phi
        i = eph['i0'] + eph['idot'] * tk + eph['cic'] * cos2phi + eph['cis'] * sin2phi

        x = r * math.cos(u)
        y = r * math.sin(u)

        if sys_name == 'BEIDOU' and eph.get('is_geo', False):
            O_mg = eph['OMG0'] + eph['OMGd'] * tk - OMGE_SYS * eph['toe']
            xg = x * math.cos(O_mg) - y * math.cos(i) * math.sin(O_mg)
            yg = x * math.sin(O_mg) + y * math.cos(i) * math.cos(O_mg)
            zg = y * math.sin(i)
            sina = math.sin(-5.0 * math.pi / 180.0)
            cosa = math.cos(-5.0 * math.pi / 180.0)
            rs_x =  xg * cosa + zg * sina
            rs_y =  yg
            rs_z = -xg * sina + zg * cosa
        else:
            O_mg = eph['OMG0'] + (eph['OMGd'] - OMGE_SYS) * tk - OMGE_SYS * eph['toe']
            cosO = math.cos(O_mg)
            sinO = math.sin(O_mg)
            cosi = math.cos(i)
            sini = math.sin(i)
            rs_x = x * cosO - y * cosi * sinO
            rs_y = x * sinO + y * cosi * cosO
            rs_z = y * sini

        return (rs_x, rs_y, rs_z)

    @staticmethod
    def _ecef2pos(x: float, y: float, z: float) -> tuple[float, float, float]:
        a = 6378137.0
        f = 1.0 / 298.257223563
        e2 = f * (2 - f)
        r = math.sqrt(x**2 + y**2)
        if r < 1e-12:
            return 0.0, math.pi/2 if z > 0 else -math.pi/2, z - a*(1-f)
        v = a
        lat = math.atan2(z, r * (1 - e2))
        for _ in range(10):
            sinp = math.sin(lat)
            v = a / math.sqrt(1 - e2 * sinp**2)
            lat_new = math.atan2(z + e2 * v * sinp, r)
            if abs(lat_new - lat) < 1e-12:
                break
            lat = lat_new
        lon = math.atan2(y, x)
        h = math.sqrt(r**2 + z**2) - v # simplified height
        return lat, lon, h

    @staticmethod
    def _satazel(sat_ecef: tuple[float, float, float], base_ecef: tuple[float, float, float]) -> tuple[float, float]:
        r = math.sqrt(sum((sat_ecef[i] - base_ecef[i])**2 for i in range(3)))
        if r < 1e-12: return 0.0, 0.0
        e = [(sat_ecef[i] - base_ecef[i]) / r for i in range(3)]
        lat, lon, _ = RTCMSignalDecoder._ecef2pos(*base_ecef)
        sinp = math.sin(lat)
        cosp = math.cos(lat)
        sinl = math.sin(lon)
        cosl = math.cos(lon)
        enu_x = -sinl * e[0] + cosl * e[1]
        enu_y = -sinp * cosl * e[0] - sinp * sinl * e[1] + cosp * e[2]
        enu_z = cosp * cosl * e[0] + cosp * sinl * e[1] + sinp * e[2]
        az = math.atan2(enu_x, enu_y)
        if az < 0: az += 2 * math.pi
        el = math.asin(enu_z)
        return math.degrees(az), math.degrees(el)

    def _decode_1005(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 138: return
        reader = BitReader(payload, 12)
        reader.skip(12) # stn_id
        reader.skip(6)  # itrf
        reader.skip(1)  # gps_ind
        reader.skip(1)  # glo_ind
        reader.skip(1)  # gal_ind
        reader.skip(1)  # ref_stn_ind
        x = reader.read_int(38) / 10000.0
        reader.skip(1)  # osc
        reader.skip(1)  # res
        y = reader.read_int(38) / 10000.0
        reader.skip(2)  # q_ind
        z = reader.read_int(38) / 10000.0
        self._base_positions[serial] = (x, y, z)

    def _decode_1019(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 476: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(6)
        reader.skip(10) # week
        reader.skip(4) # sva
        reader.skip(2) # code
        idot = reader.read_int(14) * (2**-43) * math.pi
        reader.skip(8) # iode
        toc = reader.read_uint(16) * 16.0
        f2 = reader.read_int(8) * (2**-55)
        f1 = reader.read_int(16) * (2**-43)
        f0 = reader.read_int(22) * (2**-31)
        reader.skip(10) # iodc
        crs = reader.read_int(16) * (2**-5)
        deln = reader.read_int(16) * (2**-43) * math.pi
        M0 = reader.read_int(32) * (2**-31) * math.pi
        cuc = reader.read_int(16) * (2**-29)
        e = reader.read_uint(32) * (2**-33)
        cus = reader.read_int(16) * (2**-29)
        sqrtA = reader.read_uint(32) * (2**-19)
        toes = reader.read_uint(16) * 16.0
        cic = reader.read_int(16) * (2**-29)
        OMG0 = reader.read_int(32) * (2**-31) * math.pi
        cis = reader.read_int(16) * (2**-29)
        i0 = reader.read_int(32) * (2**-31) * math.pi
        crc = reader.read_int(16) * (2**-5)
        omg = reader.read_int(32) * (2**-31) * math.pi
        OMGd = reader.read_int(24) * (2**-43) * math.pi
        
        sat_id = f"RTCM:us:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        self._ephemeris_data[serial][sat_id] = {
            'A': sqrtA**2, 'e': e, 'i0': i0, 'OMG0': OMG0, 'omg': omg, 'M0': M0,
            'deln': deln, 'OMGd': OMGd, 'idot': idot, 'crc': crc, 'crs': crs,
            'cuc': cuc, 'cus': cus, 'cic': cic, 'cis': cis, 'toe': toes, 'toc': toc,
            'f0': f0, 'f1': f1, 'f2': f2
        }


    def _decode_1020(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 336: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(6)
        freq = reader.read_uint(5) - 7
        reader.skip(4)
        tk_h = reader.read_uint(5)
        tk_m = reader.read_uint(6)
        tk_s = reader.read_uint(1) * 30.0
        bn = reader.read_uint(1)
        reader.skip(1)
        tb = reader.read_uint(7)
        vel_x = reader.read_int(24) * (2**-20) * 1000.0
        pos_x = reader.read_int(27) * (2**-11) * 1000.0
        acc_x = reader.read_int(5) * (2**-30) * 1000.0
        vel_y = reader.read_int(24) * (2**-20) * 1000.0
        pos_y = reader.read_int(27) * (2**-11) * 1000.0
        acc_y = reader.read_int(5) * (2**-30) * 1000.0
        vel_z = reader.read_int(24) * (2**-20) * 1000.0
        pos_z = reader.read_int(27) * (2**-11) * 1000.0
        acc_z = reader.read_int(5) * (2**-30) * 1000.0
        reader.skip(1)
        gamn = reader.read_int(11) * (2**-40)
        reader.skip(3)
        taun = reader.read_int(22) * (2**-30)
        
        tb_time = tb * 900.0 - 10800.0
        if tb_time < 0: tb_time += 86400.0
        
        sat_id = f"RTCM:ru:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        self._ephemeris_data[serial][sat_id] = {
            'type': 'glonass',
            'pos': (pos_x, pos_y, pos_z),
            'vel': (vel_x, vel_y, vel_z),
            'acc': (acc_x, acc_y, acc_z),
            'tb': tb_time
        }

    def _decode_1042(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 499: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(6)
        reader.skip(13) # week
        reader.skip(4) # sva
        idot = reader.read_int(14) * (2**-43) * math.pi
        reader.skip(5) # aodc
        toc = reader.read_uint(17) * 8.0
        f2 = reader.read_int(11) * (2**-66)
        f1 = reader.read_int(22) * (2**-50)
        f0 = reader.read_int(24) * (2**-33)
        reader.skip(5) # aode
        crs = reader.read_int(18) * (2**-6)
        deln = reader.read_int(16) * (2**-43) * math.pi
        M0 = reader.read_int(32) * (2**-31) * math.pi
        cuc = reader.read_int(18) * (2**-31)
        e = reader.read_uint(32) * (2**-33)
        cus = reader.read_int(18) * (2**-31)
        sqrtA = reader.read_uint(32) * (2**-19)
        toes = reader.read_uint(17) * 8.0
        cic = reader.read_int(18) * (2**-31)
        OMG0 = reader.read_int(32) * (2**-31) * math.pi
        cis = reader.read_int(18) * (2**-31)
        i0 = reader.read_int(32) * (2**-31) * math.pi
        crc = reader.read_int(18) * (2**-6)
        omg = reader.read_int(32) * (2**-31) * math.pi
        OMGd = reader.read_int(24) * (2**-43) * math.pi
        
        sat_id = f"RTCM:cn:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        is_geo = (1 <= prn <= 5) or (59 <= prn <= 63)
        self._ephemeris_data[serial][sat_id] = {
            'A': sqrtA**2, 'e': e, 'i0': i0, 'OMG0': OMG0, 'omg': omg, 'M0': M0,
            'deln': deln, 'OMGd': OMGd, 'idot': idot, 'crc': crc, 'crs': crs,
            'cuc': cuc, 'cus': cus, 'cic': cic, 'cis': cis, 'toe': toes, 'toc': toc,
            'f0': f0, 'f1': f1, 'f2': f2, 'is_geo': is_geo
        }


    def _decode_1044(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 473: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(4) + 192
        toc = reader.read_uint(16) * 16.0
        f2 = reader.read_int(8) * (2**-55)
        f1 = reader.read_int(16) * (2**-43)
        f0 = reader.read_int(22) * (2**-31)
        iode = reader.read_uint(8)
        crs = reader.read_int(16) * (2**-5)
        deln = reader.read_int(16) * (2**-43) * math.pi
        M0 = reader.read_int(32) * (2**-31) * math.pi
        cuc = reader.read_int(16) * (2**-29)
        e = reader.read_uint(32) * (2**-33)
        cus = reader.read_int(16) * (2**-29)
        sqrtA = reader.read_uint(32) * (2**-19)
        toes = reader.read_uint(16) * 16.0
        cic = reader.read_int(16) * (2**-29)
        OMG0 = reader.read_int(32) * (2**-31) * math.pi
        cis = reader.read_int(16) * (2**-29)
        i0 = reader.read_int(32) * (2**-31) * math.pi
        crc = reader.read_int(16) * (2**-5)
        omg = reader.read_int(32) * (2**-31) * math.pi
        OMGd = reader.read_int(24) * (2**-43) * math.pi
        idot = reader.read_int(14) * (2**-43) * math.pi
        
        sat_id = f"RTCM:jp:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        self._ephemeris_data[serial][sat_id] = {
            'A': sqrtA**2, 'e': e, 'i0': i0, 'OMG0': OMG0, 'omg': omg, 'M0': M0,
            'deln': deln, 'OMGd': OMGd, 'idot': idot, 'crc': crc, 'crs': crs,
            'cuc': cuc, 'cus': cus, 'cic': cic, 'cis': cis, 'toe': toes, 'toc': toc,
            'f0': f0, 'f1': f1, 'f2': f2
        }

    def _decode_1045(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 484: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(6)
        reader.skip(12 + 10 + 8) # week, iode, sva
        idot = reader.read_int(14) * (2**-43) * math.pi
        toc = reader.read_uint(14) * 60.0
        f2 = reader.read_int(6) * (2**-59)
        f1 = reader.read_int(21) * (2**-46)
        f0 = reader.read_int(31) * (2**-34)
        crs = reader.read_int(16) * (2**-5)
        deln = reader.read_int(16) * (2**-43) * math.pi
        M0 = reader.read_int(32) * (2**-31) * math.pi
        cuc = reader.read_int(16) * (2**-29)
        e = reader.read_uint(32) * (2**-33)
        cus = reader.read_int(16) * (2**-29)
        sqrtA = reader.read_uint(32) * (2**-19)
        toes = reader.read_uint(14) * 60.0
        cic = reader.read_int(16) * (2**-29)
        OMG0 = reader.read_int(32) * (2**-31) * math.pi
        cis = reader.read_int(16) * (2**-29)
        i0 = reader.read_int(32) * (2**-31) * math.pi
        crc = reader.read_int(16) * (2**-5)
        omg = reader.read_int(32) * (2**-31) * math.pi
        OMGd = reader.read_int(24) * (2**-43) * math.pi
        
        sat_id = f"RTCM:eu:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        self._ephemeris_data[serial][sat_id] = {
            'A': sqrtA**2, 'e': e, 'i0': i0, 'OMG0': OMG0, 'omg': omg, 'M0': M0,
            'deln': deln, 'OMGd': OMGd, 'idot': idot, 'crc': crc, 'crs': crs,
            'cuc': cuc, 'cus': cus, 'cic': cic, 'cis': cis, 'toe': toes, 'toc': toc,
            'f0': f0, 'f1': f1, 'f2': f2
        }

    async def decode(self, serial: str, payload: bytes) -> dict[str, Any] | None:
        if not payload or (not self._buffers.get(serial) and not self._looks_like_rtcm(payload)):
            return None
        return self.decode_sync(serial, payload)

    def decode_sync(self, serial: str, payload: bytes) -> dict[str, Any] | None:
        buffer = self._buffers.get(serial, b"") + payload
        packets: list[dict[str, Any]] = []
        satellites_by_id: dict[str, dict[str, Any]] = {}
        errors = 0

        while len(buffer) >= 3:
            start = buffer.find(b"\xD3")
            if start < 0:
                buffer = b""
                break
            if start > 0:
                buffer = buffer[start:]
            if len(buffer) < 3:
                break

            message_length = ((buffer[1] & 0x03) << 8) | buffer[2]
            packet_length = 3 + message_length + 3
            if len(buffer) < packet_length:
                break

            packet = buffer[:packet_length]
            buffer = buffer[packet_length:]

            expected_crc = int.from_bytes(packet[-3:], "big")
            if crc24q(packet[:-3]) != expected_crc:
                errors += 1
                continue

            payload_bytes = packet[3:-3]
            message_type = (payload_bytes[0] << 4) | (payload_bytes[1] >> 4) if len(payload_bytes) >= 2 else -1
            try:
                if message_type in (1005, 1006):
                    self._decode_1005(payload_bytes, serial)
                    decoded = None
                elif message_type == 1019:
                    self._decode_1019(payload_bytes, serial)
                    decoded = None
                elif message_type == 1020:
                    self._decode_1020(payload_bytes, serial)
                    decoded = None
                elif message_type == 1042:
                    self._decode_1042(payload_bytes, serial)
                    decoded = None
                elif message_type == 1044:
                    self._decode_1044(payload_bytes, serial)
                    decoded = None
                elif message_type == 1045:
                    self._decode_1045(payload_bytes, serial)
                    decoded = None
                else:
                    decoded = self._decode_msm_payload(message_type, payload_bytes, serial)
            except (IndexError, ValueError):
                decoded = None
                errors += 1
            if decoded:
                packets.append(
                    {
                        "messageType": message_type,
                        "navSystem": decoded["navSystem"],
                        "msmLevel": decoded["msmLevel"],
                        "satelliteCount": len(decoded["satellites"]),
                    }
                )
                for sat in decoded["satellites"]:
                    satellites_by_id[sat["id"]] = sat
            else:
                packets.append({"messageType": message_type, "satelliteCount": 0})

        if len(buffer) > MAX_BUFFER_BYTES:
            buffer = buffer[-MAX_BUFFER_BYTES:]
        self._buffers[serial] = buffer

        if not packets and not satellites_by_id and errors == 0:
            return None

        return {
            "type": "rtcm_signal",
            "serial": serial,
            "packets": packets,
            "satellites": list(satellites_by_id.values()),
            "errors": errors,
            "bufferedBytes": len(buffer),
        }

    def _decode_msm_payload(self, message_type: int, payload: bytes, serial: str) -> dict[str, Any] | None:
        type_info = _message_type_info(message_type)
        if not type_info:
            return None

        info, msm_level = type_info
        reader = BitReader(payload, 12)
        reader.skip(12)  # reference station id
        epoch_time_ms = reader.read_uint(30)  # gnss epoch time
        reader.skip(1)   # multiple message indicator
        reader.skip(3)   # issue of data station
        reader.skip(7)   # reserved
        reader.skip(2)   # clock steering
        reader.skip(2)   # external clock
        reader.skip(1)   # divergence-free smoothing
        reader.skip(3)   # smoothing interval

        satellite_mask = reader.read_uint(64)
        signal_mask = reader.read_uint(32)
        satellite_ids = _active_ids(satellite_mask, 64)
        signal_ids = _active_ids(signal_mask, 32)
        if not satellite_ids or not signal_ids:
            return {"navSystem": info.nav_system, "msmLevel": msm_level, "satellites": []}

        cell_mask: list[tuple[int, int]] = []
        for satellite_id in satellite_ids:
            for signal_id in signal_ids:
                if reader.read_uint(1):
                    cell_mask.append((satellite_id, signal_id))

        self._skip_msm_satellite_data(reader, msm_level, len(satellite_ids))
        signal_cnr = self._read_msm_signal_cnr(reader, msm_level, cell_mask)
        now_ms = int(time.time() * 1000)

        satellites: list[dict[str, Any]] = []
        for satellite_id in satellite_ids:
            best_signal_id, best_cnr = self._best_signal(signal_cnr, satellite_id)
            if best_cnr <= 0:
                continue
                
            sat_str_id = f"RTCM:{info.system}:{satellite_id}"
            
            elevation = 0.0
            azimuth = 0.0
            
            now = time.time()
            if serial not in self._azel_cache: self._azel_cache[serial] = {}
            cached = self._azel_cache[serial].get(sat_str_id)
            
            if cached and now - cached[0] < 5.0:
                azimuth, elevation = cached[1], cached[2]
            else:
                base_pos = self._base_positions.get(serial)
                eph = self._ephemeris_data.get(serial, {}).get(sat_str_id)
                
                if base_pos and eph:
                    if eph.get('type') == 'glonass':
                        sat_pos = self._glonass_eph2pos(eph, epoch_time_ms / 1000.0)
                    else:
                        sat_pos = self._eph2pos(eph, epoch_time_ms / 1000.0, info.nav_system)
                        
                    if sat_pos:
                        az, el = self._satazel(sat_pos, base_pos)
                        azimuth = az
                        elevation = el
                        self._azel_cache[serial][sat_str_id] = (now, azimuth, elevation)

            satellites.append(
                {
                    "id": sat_str_id,
                    "prn": satellite_id,
                    "system": info.system,
                    "talker": "RTCM",
                    "snr": round(best_cnr),
                    "cnr": best_cnr,
                    "signalId": best_signal_id,
                    "messageType": message_type,
                    "isTracking": True,
                    "elevation": elevation,
                    "azimuth": azimuth,
                    "lastSeen": now_ms,
                }
            )

        return {"navSystem": info.nav_system, "msmLevel": msm_level, "satellites": satellites}

    @staticmethod
    def _skip_msm_satellite_data(reader: BitReader, msm_level: int, satellite_count: int) -> None:
        if msm_level in (1, 2, 3):
            reader.skip(10 * satellite_count)
        elif msm_level in (4, 6):
            reader.skip((8 + 10) * satellite_count)
        elif msm_level in (5, 7):
            reader.skip((8 + 4 + 10 + 14) * satellite_count)

    @staticmethod
    def _read_msm_signal_cnr(
        reader: BitReader,
        msm_level: int,
        cell_mask: list[tuple[int, int]],
    ) -> dict[tuple[int, int], float]:
        signal_cnr: dict[tuple[int, int], float] = {}
        cell_count = len(cell_mask)

        if msm_level in (1, 2, 3):
            return signal_cnr
        if msm_level in (4, 5):
            reader.skip((15 + 22 + 4 + 1) * cell_count)
            for cell in cell_mask:
                signal_cnr[cell] = float(reader.read_uint(6))
            if msm_level == 5:
                reader.skip(15 * cell_count)
            return signal_cnr
        if msm_level in (6, 7):
            reader.skip((20 + 24 + 10 + 1) * cell_count)
            for cell in cell_mask:
                signal_cnr[cell] = reader.read_uint(10) / 16.0
            if msm_level == 7:
                reader.skip(15 * cell_count)
        return signal_cnr

    @staticmethod
    def _best_signal(signal_cnr: dict[tuple[int, int], float], satellite_id: int) -> tuple[int | None, float]:
        best_signal_id: int | None = None
        best_cnr = 0.0
        for (sat_id, signal_id), cnr in signal_cnr.items():
            if sat_id == satellite_id and cnr > best_cnr:
                best_signal_id = signal_id
                best_cnr = cnr
        return best_signal_id, best_cnr

    @staticmethod
    def _looks_like_rtcm(payload: bytes) -> bool:
        return payload[:1] == b"\xD3" or b"\xD3" in payload[:64]


rtcm_signal_decoder = RTCMSignalDecoder()
