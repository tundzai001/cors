"""Public CORS replies with regional NOAA ionosphere observations."""
import asyncio
import json
import math
import re
import unicodedata
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
    features = json.loads((Path(__file__).parent / 'data/vietnam_provinces.geojson').read_text())['features']
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


def ionosphere_text(data, question, now=None):
    name, bounds = region_for_question(question)
    unavailable = f'Tầng điện ly · {name}: chưa có dữ liệu cập nhật để đánh giá đo đạc.'
    try:
        observed = datetime.fromisoformat(data['metadata']['time_tag'].replace('Z', '+00:00'))
        age = ((now or datetime.now(timezone.utc)) - observed).total_seconds()
        if data['metadata'].get('is_stale', True) or not -300 <= age <= 1800:
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
        return (f'Tầng điện ly · {name} (ước tính khu vực):\n'
                f'TEC {tec_text} TECU; {comparison}.\n'
                f'NOAA GloTEC · {stamp}. TEC chưa đủ để khẳng định độ ổn định RTK.')
    except (KeyError, ValueError, TypeError, IndexError, OverflowError):
        return unavailable


async def report_text(health, question=''):
    headline = {
        'online': '🟢 CORS Cục đang hoạt động.',
        'offline': '🔴 CORS Cục đang gián đoạn.',
    }.get(health.get('status'), '⚪ Chưa xác định được trạng thái CORS Cục.')
    try:
        data = await asyncio.wait_for(ionosphere_service.get_latest(format_type='geojson'), timeout=8)
    except Exception:
        data = {}
    atmosphere = ionosphere_text(data, question)
    if health.get('status') == 'offline':
        advice = 'Đo đạc: nên chờ CORS Cục hoạt động lại rồi kiểm tra FIX.'
    elif 'chưa có dữ liệu' in atmosphere or health.get('status') != 'online':
        advice = 'Đo đạc: chưa đủ dữ liệu đánh giá; kiểm tra FIX ổn định tại điểm đo.'
    else:
        advice = 'Đo đạc: có thể đo thử; chỉ lưu điểm khi FIX ổn định và kiểm tra mốc đạt yêu cầu.'
    return f'{headline}\n\n{atmosphere}\n\n{advice}'
