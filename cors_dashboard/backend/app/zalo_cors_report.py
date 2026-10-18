"""Public CORS replies with regional NOAA ionosphere observations."""
import asyncio
import json
import math
import re
import unicodedata
import time

import httpx
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from .ionosphere_service import ionosphere_service


def _plain(text):
    text = unicodedata.normalize('NFD', text.lower().replace('đ', 'd'))
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).strip()


@lru_cache(maxsize=1)
def _regions():
    features = json.loads((Path(__file__).parent / 'data/vietnam_provinces.geojson').read_text(encoding='utf-8'))['features']
    result = []
    for feature in features:
        props, geometry = feature['properties'], feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        points = [point for polygon in polygons for point in polygon[0]]
        bounds = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
        result.append((props['name'], bounds, {_plain(props.get(key, '')) for key in ('name', 'nameEn')} - {''}))
    return result


def region_for_question(question):
    text = f' {_plain(question)} '
    matches = [(name, bounds) for name, bounds, aliases in _regions() if any(f' {alias} ' in text for alias in aliases)]
    # Multiple provinces need a national report rather than silently choosing one.
    return matches[0] if len(matches) == 1 else ('Việt Nam', (102, 8, 110, 24))


def ionosphere_observation(data, question, now=None):
    name, bounds = region_for_question(question)
    unavailable = None
    try:
        observed = datetime.fromisoformat(data['metadata']['time_tag'].replace('Z', '+00:00'))
        age = ((now or datetime.now(timezone.utc)) - observed).total_seconds()
        if not -300 <= age <= 5400:
            return unavailable
        candidates = []
        west, south, east, north = bounds
        for feature in data.get('features', []):
            lon, lat = feature['geometry']['coordinates'][:2]
            props = feature['properties']
            tec, anomaly = float(props['tec']), float(props['anomaly'])
            if all(math.isfinite(v) for v in (lon, lat, tec, anomaly)) and 0 <= tec <= 300:
                candidates.append((lon, lat, tec, anomaly))
        cells = [p for p in candidates if west <= p[0] <= east and south <= p[1] <= north]
        if not cells and candidates and name != 'Việt Nam':
            lon, lat = (west + east) / 2, (south + north) / 2
            nearest = min(candidates, key=lambda p: (p[0]-lon)**2 + (p[1]-lat)**2)
            if abs(nearest[0]-lon) <= 5 and abs(nearest[1]-lat) <= 2.5:
                cells = [nearest]
        if not cells:
            return unavailable
        low, high = min(p[2] for p in cells), max(p[2] for p in cells)
        anomaly = sum(p[3] for p in cells) / len(cells)
        tec_text = f'{low:.1f}' if low == high else f'{low:.1f}–{high:.1f}'
        stamp = observed.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%H:%M %d/%m')
        comparison = (f'cao hơn nền 30 ngày khoảng {anomaly:.1f} TECU' if round(anomaly, 1) > 0
                      else f'thấp hơn nền 30 ngày khoảng {abs(anomaly):.1f} TECU' if round(anomaly, 1) < 0
                      else 'xấp xỉ mức nền 30 ngày')
        return {'name': name, 'tec': tec_text, 'comparison': comparison, 'stamp': stamp,
                'age_minutes': max(0, int(age // 60)),
                'delayed': age > 1800 or data['metadata'].get('is_stale', False)}
    except (KeyError, ValueError, TypeError, IndexError, OverflowError):
        return unavailable


def ionosphere_text(data, question, now=None):
    observation = ionosphere_observation(data, question, now)
    if not observation:
        return f'Tầng điện ly · {region_for_question(question)[0]}: chưa có dữ liệu cập nhật để đánh giá đo đạc.'
    delay = (f" · bản gần nhất, trễ {observation['age_minutes']} phút"
             if observation['delayed'] else '')
    return (f"Tầng điện ly · {observation['name']} (ước tính khu vực):\n"
            f"TEC {observation['tec']} TECU; {observation['comparison']}.\n"
            f"NOAA GloTEC · {observation['stamp']}{delay}.")


_kp_cache = None
_kp_fetched = 0
_kp_lock = asyncio.Lock()


async def geomagnetic_observation():
    """Share the public NOAA one-minute estimate; never reuse an old index as current."""
    global _kp_cache, _kp_fetched
    async with _kp_lock:
        if time.monotonic() - _kp_fetched < 300:
            if _kp_cache is None or (datetime.now(timezone.utc) - _kp_cache['observed']).total_seconds() <= 1800:
                return _kp_cache
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                response = await client.get('https://services.swpc.noaa.gov/json/planetary_k_index_1m.json')
                response.raise_for_status()
                rows = response.json()
            valid = []
            for row in rows:
                observed = datetime.fromisoformat(row['time_tag'].replace('Z', '+00:00'))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - observed).total_seconds()
                value = float(row['estimated_kp'])
                if -300 <= age <= 1800 and math.isfinite(value) and 0 <= value <= 9:
                    valid.append((observed, value))
            observed, value = max(valid)
            _kp_cache = {'value': value, 'observed': observed, 'stamp': observed.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%H:%M %d/%m')}
            _kp_fetched = time.monotonic()
            return _kp_cache
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            # Cache failures briefly too, without presenting yesterday's quiet Kp as current.
            _kp_cache, _kp_fetched = None, time.monotonic() - 240
            return None


async def report_text(health, question=''):
    headline = {
        'online': '🟢 CORS Cục đang hoạt động.',
        'offline': '🔴 CORS Cục đang gián đoạn.',
    }.get(health.get('status'), '⚪ Chưa xác định được trạng thái CORS Cục.')

    async def fetch_ionosphere():
        try:
            return await asyncio.wait_for(ionosphere_service.get_latest(format_type='geojson'), timeout=15)
        except Exception:
            return {}

    data, kp = await asyncio.gather(fetch_ionosphere(), geomagnetic_observation())
    observation = ionosphere_observation(data, question)
    words = _plain(question)
    name = region_for_question(question)[0]
    diagnostic = bool(re.search(r'\b(float|khong fix|kho fix|fix kho|nhay|mat fix)\b', words))
    detail = bool(re.search(r'\b(chi tiet|dien ly|tec|kp|dia tu)\b', words))
    measurement = bool(re.search(r'\b(do|trien khai|di lam)\b', words))
    future = bool(re.search(r'\b(chieu nay|toi nay|ngay mai|tuan sau)\b', words))
    value = kp['value'] if kp else None
    magnetic = (f"Địa từ Kp {value:.2f} · " +
                ('bão từ mạnh, nguy cơ gián đoạn định vị tăng.' if value >= 7 else
                 'đang có bão từ; cần theo dõi chất lượng định vị.' if value >= 5 else
                 'đang nhiễu động.' if value >= 4 else 'mức yên tĩnh.') +
                f" NOAA SWPC · {kp['stamp']}." if kp else
                'Chỉ số địa từ đang tạm mất cập nhật.')

    # Operational advice, not a claim to measure receiver accuracy from TEC or Kp.
    if health.get('status') == 'offline':
        decision = 'Tạm dừng phương án đo dùng CORS Cục; chuyển nguồn hiệu chỉnh dự phòng nếu có.'
    elif health.get('status') != 'online':
        decision = 'Chưa xác nhận được nguồn Cục; kiểm tra kết nối hiệu chỉnh trước khi triển khai.'
    elif value is not None and value >= 7:
        decision = 'Nên hoãn đo khống chế hoặc công việc cần độ chính xác cao: đang có bão từ mạnh.'
    elif value is not None and value >= 4:
        decision = 'Đo thận trọng: địa từ đang nhiễu động; ưu tiên ca đo ngắn, kiểm tra chéo điểm quan trọng.'
    elif observation and not observation['delayed'] and value is not None:
        decision = 'Có thể triển khai đo: nguồn Cục hoạt động, địa từ yên tĩnh.'
    elif observation and observation['delayed']:
        decision = 'Đo thận trọng: bản điện ly đang trễ. Có thể triển khai phần việc thông thường, chưa dùng bản tin này để chốt ca đo chính xác cao.'
    elif observation:
        decision = 'Nguồn Cục hoạt động, bản điện ly khu vực đã có. Địa từ mất cập nhật; ưu tiên công việc thông thường, kiểm tra chéo điểm quan trọng.'
    else:
        decision = 'Nguồn Cục sẵn sàng. Bản điện ly đang mất cập nhật; chưa có căn cứ quy lỗi cho tầng điện ly.'

    if diagnostic:
        if health.get('status') == 'offline':
            action = 'FLOAT lúc này: xử lý nguồn hiệu chỉnh trước; chưa cần đổi thiết lập vệ tinh.'
        elif value is not None and value >= 5:
            action = 'FLOAT kéo dài có thể liên quan nhiễu động hiện tại. So sánh với máy khác cùng khu vực trước khi đổi cấu hình.'
        else:
            action = ('FLOAT kéo dài: xem tuổi hiệu chỉnh và luồng RTCM trước. Tuổi tăng/mất gói thì kiểm tra mạng hoặc mountpoint; '
                      'hiệu chỉnh đều thì kiểm tra khoảng cách trạm, che khuất và đa đường. '
                      'Gửi tuổi hiệu chỉnh, số vệ tinh và khoảng cách trạm để khoanh vùng tiếp.')
        return f'{headline}\n\n{name} · {action}\n\n{magnetic}'

    atmosphere = ionosphere_text(data, question)
    if not observation:
        atmosphere = f'Tầng điện ly · {name}: bản NOAA khu vực đang mất cập nhật.'
    if future:
        decision = 'Đây là tình hình hiện tại, chưa phải dự báo cho ca đo sắp tới. ' + decision
    if detail or measurement:
        result = f'{headline}\n\n{name} · {decision}\n\n{atmosphere}\n{magnetic}'
        if detail:
            result += '\nTEC phản ánh lượng electron, không cho biết trực tiếp sai số máy hay trạng thái FIX.'
        return result
    # A quick status question gets a quick answer; detailed data is available on request.
    summary = (f"Tầng điện ly · {name}: TEC {observation['tec']} TECU · {observation['stamp']}"
               + (f" · bản gần nhất, trễ {observation['age_minutes']} phút." if observation['delayed'] else '.')
               if observation else atmosphere)
    return f'{headline}\n{decision}\n{summary}\n{magnetic}'
