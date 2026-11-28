import time

class NMEAParser:
    """
    🚀 NÂNG CẤP V3.0 - Xử lý multi-constellation GSV
    """
    def __init__(self):
        # Bộ đệm GSV cho từng hệ thống (GP=GPS, GL=GLONASS, GA=Galileo, GB=BeiDou)
        self.gsv_buffer = {}
        self.gsv_expected_messages = {}
        self.gsv_last_update = 0
        self.gsv_timeout = 0.2  # 200ms timeout để gộp các GSV messages
        
        # Data cache
        self.last_gga = None
        self.last_gsa = None
        self.satellites = {}  # Key = PRN, Value = sat info

    def parse(self, sentence: str) -> dict | None:
        """Parse NMEA sentence và trả về dict nếu có data hoàn chỉnh"""
        sentence = str(sentence or "").strip()
        if not sentence.startswith('$') or '*' not in sentence:
            return None

        body, checksum = sentence[1:].rsplit('*', 1)
        checksum = checksum[:2]
        if len(checksum) != 2 or not all(char in "0123456789abcdefABCDEF" for char in checksum):
            return None
        calculated = 0
        for char in body:
            calculated ^= ord(char)
        if calculated != int(checksum, 16):
            return None
        
        try:
            parts = sentence.split('*')[0].split(',')
            if not parts:
                return None
            
            message_type = parts[0][3:]  # Bỏ $GP, $GN, etc.
            
            if message_type == 'GGA':
                return self._parse_gga(parts)
            elif message_type == 'GSA':
                return self._parse_gsa(parts)
            elif message_type == 'GSV':
                return self._parse_gsv(parts)
            elif message_type == 'GST':
                return self._parse_gst(parts)
        
        except (ValueError, IndexError):
            pass
        
        return None

    def _parse_gga(self, parts: list) -> dict | None:
        """Parse GGA - Tọa độ và trạng thái fix"""
        # No-fix GGA sentences intentionally leave coordinates and altitude
        # empty. Preserve the receiver status instead of dropping the sentence.
        if len(parts) < 10:
            return None
        
        try:
            fix_quality = int(parts[6]) if parts[6] else 0
            fix_map = {
                0: "INVALID", 1: "3D", 2: "DGPS",
                3: "PPS", 4: "RTK_FIXED", 5: "RTK_FLOAT", 6: "ESTIMATED"
            }
            
            altitude_msl = float(parts[9]) if parts[9] else None
            geoid_separation = float(parts[11]) if len(parts) > 11 and parts[11] else None
            altitude = altitude_msl + (geoid_separation or 0.0) if altitude_msl is not None else None

            self.last_gga = {
                "type": "GGA",
                "timestamp_utc": parts[1],
                "latitude": self._dms_to_dd(parts[2], parts[3]),
                "longitude": self._dms_to_dd(parts[4], parts[5]),
                "fix_status": fix_map.get(fix_quality, f"UNKNOWN_{fix_quality}"),
                "satellites": int(parts[7]) if parts[7] else 0,
                "hdop": float(parts[8]) if parts[8] else 99.99,
                # NMEA GGA: field 9 = MSL height H, field 11 = geoid separation N.
                # Ellipsoidal height h = H + N.
                "altitude": altitude,
                "altitude_msl": altitude_msl,
                "altitude_ref": "ELLIPSOID"
            }
            return self.last_gga
        
        except (ValueError, IndexError):
            return None

    def _parse_gsa(self, parts: list) -> dict | None:
        """Parse GSA - Vệ tinh đang tracking và DOP"""
        if len(parts) < 18:
            return None
        
        try:
            active_sats = [int(p) for p in parts[3:15] if p]
            
            self.last_gsa = {
                "type": "GSA",
                "active_sats": active_sats,
                "pdop": float(parts[15]) if parts[15] else 99.99,
                "hdop": float(parts[16]) if parts[16] else 99.99,
                "vdop": float(parts[17].split('*')[0]) if parts[17] else 99.99
            }
            
            # 🔥 Cập nhật trạng thái tracking cho satellites
            for sat in self.satellites.values():
                sat['isTracking'] = sat.get('prn') in active_sats
            
            return self.last_gsa
        
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _normalize_gsv_prn(talker_id: str, system: str, prn: int) -> tuple[str, int, bool]:
        if talker_id == 'GP':
            if 65 <= prn <= 96:
                return 'ru', prn - 64, True
            if 193 <= prn <= 202:
                return 'jp', prn - 192, True
            if prn > 100:
                return system, prn, False
        elif talker_id == 'GL' and prn > 64:
            prn -= 64
        return system, prn, True

    def _parse_gsv_satellite(self, talker_id: str, system: str, fields: list[str]):
        if len(fields) < 4 or not fields[0]:
            return system, None
        try:
            prn = int(fields[0])
            if prn < 1 or prn > 999:
                return system, None
            system, prn, accepted = self._normalize_gsv_prn(talker_id, system, prn)
            if not accepted or prn < 1 or prn > 200:
                return system, None
            elevation = int(fields[1]) if fields[1] else 0
            azimuth = int(fields[2]) if fields[2] else 0
            snr = int(fields[3].split('*')[0]) if fields[3] else 0
            if not 0 <= elevation <= 90 or not 0 <= azimuth <= 359 or not 0 <= snr <= 99:
                return system, None
        except (ValueError, IndexError):
            return system, None
        return system, {
            "id": f"{talker_id}:{prn}",
            "prn": prn,
            "talker": talker_id,
            "system": system,
            "elevation": elevation,
            "azimuth": azimuth,
            "snr": snr,
            "isTracking": False,
        }

    def _buffer_gsv_satellites(self, talker_id: str, system: str, sats_raw: list[str]) -> None:
        for offset in range(0, len(sats_raw), 4):
            system, satellite = self._parse_gsv_satellite(
                talker_id, system, sats_raw[offset:offset + 4]
            )
            if satellite and talker_id in self.gsv_buffer:
                self.gsv_buffer[talker_id].append(satellite)

    def _complete_gsv_buffer(self, talker_id: str) -> dict:
        all_sats = []
        for constellation_sats in self.gsv_buffer.values():
            all_sats.extend(constellation_sats)
        for sat in all_sats:
            self.satellites[sat.get('id') or sat['prn']] = sat
        if len(self.satellites) > 64:
            self.satellites = dict(list(self.satellites.items())[-64:])
            if self.last_gsa:
                sat['isTracking'] = sat['prn'] in self.last_gsa['active_sats']
        self.gsv_buffer.clear()
        self.gsv_expected_messages.clear()
        return {
            "type": "GSV",
            "talker": talker_id,
            "satellites": list(self.satellites.values())
        }

    def _flush_stale_gsv_buffer(self):
        if time.time() - self.gsv_last_update <= self.gsv_timeout or not self.gsv_buffer:
            return None
        all_sats = []
        for constellation_sats in self.gsv_buffer.values():
            all_sats.extend(constellation_sats)
        if not all_sats:
            return None
        for sat in all_sats:
            self.satellites[sat.get('id') or sat['prn']] = sat
        if len(self.satellites) > 64:
            self.satellites = dict(list(self.satellites.items())[-64:])
        self.gsv_buffer.clear()
        return {
            "type": "GSV",
            "satellites": list(self.satellites.values())
        }

    def _parse_gsv(self, parts: list) -> dict | None:
        if len(parts) < 4:
            return None
        try:
            talker_id = parts[0][1:3]
            system = {
                'GP': 'us', 'GL': 'ru', 'GA': 'eu', 'GB': 'cn',
                'BD': 'cn', 'GQ': 'jp', 'QZ': 'jp', 'GN': 'un',
            }.get(talker_id, 'un')
            num_messages = int(parts[1])
            msg_num = int(parts[2])
            if num_messages < 1 or num_messages > 9 or msg_num < 1 or msg_num > num_messages:
                return None
            if msg_num == 1:
                self.gsv_buffer[talker_id] = []
                self.gsv_expected_messages[talker_id] = num_messages
            self._buffer_gsv_satellites(talker_id, system, parts[4:])
            self.gsv_last_update = time.time()
            if msg_num == int(parts[1]) and msg_num == num_messages:
                return self._complete_gsv_buffer(talker_id)
        except (ValueError, IndexError):
            pass
        return self._flush_stale_gsv_buffer()

    @staticmethod
    def _gst_float(parts: list, index: int, strip_checksum: bool = False):
        if index >= len(parts) or not parts[index]:
            return None
        value = parts[index].split('*')[0] if strip_checksum else parts[index]
        return float(value)

    @staticmethod
    def _gst_horizontal_accuracy(lat_sigma, lon_sigma):
        if lat_sigma is None or lon_sigma is None:
            return None, None
        hacc = (lat_sigma ** 2 + lon_sigma ** 2) ** 0.5
        if hacc <= 0:
            return None, None
        return hacc, "gst_lat_lon_sigma"

    def _parse_gst(self, parts: list) -> dict | None:
        """Parse GST - GNSS pseudorange error statistics."""
        if len(parts) < 8:
            return None

        try:
            semi_major = self._gst_float(parts, 3)
            semi_minor = self._gst_float(parts, 4)
            lat_sigma = self._gst_float(parts, 6)
            lon_sigma = self._gst_float(parts, 7, strip_checksum=True)
            alt_sigma = self._gst_float(parts, 8, strip_checksum=True)
            hacc, hacc_source = self._gst_horizontal_accuracy(lat_sigma, lon_sigma)
            return {
                "type": "GST",
                "timestamp_utc": parts[1],
                "rms": self._gst_float(parts, 2),
                "semi_major_sigma": semi_major,
                "semi_minor_sigma": semi_minor,
                "orientation": self._gst_float(parts, 5),
                "lat_sigma": lat_sigma,
                "lon_sigma": lon_sigma,
                "alt_sigma": alt_sigma,
                "hacc": hacc,
                "hacc_source": hacc_source,
            }
        except (ValueError, IndexError):
            return None

    def _dms_to_dd(self, dms: str, direction: str) -> float | None:
        """Convert DMS to Decimal Degrees"""
        if not dms or not direction:
            return None
        
        try:
            dms_float = float(dms)
            degrees = int(dms_float / 100)
            minutes = dms_float % 100
            decimal_degrees = degrees + (minutes / 60)
            
            if direction in ['S', 'W']:
                decimal_degrees *= -1
            
            return round(decimal_degrees, 8)
        
        except (ValueError, TypeError):
            return None
