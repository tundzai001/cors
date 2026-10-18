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
    regions_file = Path(__file__).parent / 'data/vietnam_regions.json'
    if regions_file.exists():
        raw = json.loads(regions_file.read_text(encoding='utf-8'))
        return [(item['name'], tuple(item['bounds']), set(item['aliases'])) for item in raw]
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
                'anomaly_low': min(p[3] for p in cells), 'anomaly_high': max(p[3] for p in cells),
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
            f"TEC {observation['tec']} TECU; trung bình {observation['comparison']}.\n"
            f"Độ lệch từng ô so với nền 30 ngày: {observation['anomaly_low']:+.1f} đến {observation['anomaly_high']:+.1f} TECU.\n"
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


def field_advice(status, kp, words):
    """Practical workflow advice; no inferred receiver accuracy or local RTCM status."""
    if status == 'offline':
        return ('Khuyến nghị: chuyển sang nguồn hiệu chỉnh dự phòng đã được kiểm tra. '
                'Nếu không có nguồn thay thế, tranh thủ chuẩn bị điểm và kiểm tra thiết bị; chưa chốt số đo bằng nguồn Cục lúc này.')
    if status != 'online':
        return 'Khuyến nghị: xác nhận máy có nhận hiệu chỉnh trước khi bố trí cả ca đo; trạng thái nguồn hiện chưa được xác minh.'
    if kp is not None and kp >= 7:
        return ('Khuyến nghị: chuyển ca này sang khảo sát hiện trường, chuẩn bị mốc hoặc xử lý số liệu. '
                'Dời phần đo khống chế sang khi cảnh báo giảm; nếu buộc phải đo, cần phương pháp kiểm tra độc lập.')
    control = bool(re.search(r'\b(khong che|moc|chinh xac cao|bo tri|cong trinh|dia chinh|ranh)\b', words))
    terrain = bool(re.search(r'\b(dia hinh|chi tiet|binh do)\b', words))
    if control:
        return ('Với khống chế/bố trí: kiểm tra trên mốc đã biết, đo lặp điểm quan trọng ở thời điểm khác '
                'và đối chiếu trước khi chốt. Bản tin điện ly không thay được kiểm tra độ chính xác tại công trình.')
    if kp is not None and kp >= 4:
        return ('Khuyến nghị: ưu tiên phần đo chi tiết ở khu vực thoáng, bố trí điểm kiểm tra trong ca. '
                'Các điểm khống chế hoặc ranh quan trọng nên đo lặp độc lập, tránh chốt theo một lượt đo.')
    if terrain:
        return ('Với đo địa hình: kiểm soát chất lượng ngay trong ca, nhất là khi số đo thay đổi giữa các lượt. '
                'Bố trí điểm kiểm tra đầu/cuối ca; ưu tiên chỗ thoáng, kiểm tra riêng các điểm sát nhà hoặc dưới tán cây.')
    return ('Gợi ý cho ca đo:\n'
            '• Đo địa hình: ưu tiên khu vực thoáng, bố trí điểm kiểm tra đầu/cuối ca.\n'
            '• Khống chế/bố trí: kiểm tra mốc đã biết và đo lặp điểm quan trọng ở thời điểm khác trước khi chốt.')


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
    detail = bool(re.search(r'\b(chi tiet|dien ly|tec|kp|dia tu)\b', words)) and not re.search(r'\b(do chi tiet|do dia hinh)\b', words)
    measurement = bool(re.search(r'\b(do|trien khai|di lam|khuyen nghi|tu van|khong che|bo tri)\b', words))
    future = bool(re.search(r'\b(chieu nay|toi nay|ngay mai|tuan sau)\b', words))
    value = kp['value'] if kp else None
    magnetic = (f"Địa từ Kp {value:.2f} · " +
                ('bão từ mạnh, nguy cơ gián đoạn định vị tăng.' if value >= 7 else
                 'đang có bão từ; cần theo dõi chất lượng định vị.' if value >= 5 else
                 'đang nhiễu động.' if value >= 4 else 'mức yên tĩnh.') +
                f" NOAA SWPC · {kp['stamp']}." if kp else
                'Chỉ số địa từ đang tạm mất cập nhật.')

    # Keep operational signals separate from observation age: a delayed model
    # is not evidence that the field conditions themselves have deteriorated.
    if health.get('status') == 'offline':
        decision = 'Cục đang gián đoạn, ca đo dùng nguồn này cần chuyển sang nguồn dự phòng hoặc chờ kết nối lại.'
    elif health.get('status') != 'online':
        decision = 'Tôi chưa xác nhận được nguồn Cục, nên chưa chốt được phương án đo dùng nguồn này.'
    elif value is not None and value >= 7:
        decision = 'Nên hoãn đo khống chế: bão từ mạnh đang làm tăng nguy cơ gián đoạn định vị.'
    elif value is not None and value >= 4:
        decision = 'Cục vẫn hoạt động, nhưng địa từ đang nhiễu động. Ca đo chính xác cao cần thận trọng.'
    elif value is not None:
        decision = 'CORS Cục đang hoạt động, địa từ yên tĩnh. Phần điện ly cần xem riêng: Kp thấp không chứng minh RTK tại điểm đo đang ổn định.'
    else:
        decision = 'Cục đang hoạt động. Địa từ mất cập nhật nên tôi chỉ xác nhận được tình trạng nguồn.'

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
        atmosphere = f'Tầng điện ly · {name}: NOAA đang mất cập nhật.'
    if future:
        return (f'{name}: đây là tình hình hiện tại, chưa phải dự báo cho ca đo anh hỏi.\n\n'
                f'{headline}\n{magnetic}\n{atmosphere}')
    if detail:
        return (f'{headline}\n\n{atmosphere}\n{magnetic}\n'
                'TEC là ước tính điện ly khu vực, không phải sai số đo hay chất lượng FIX của máy.')

    # Put the answer first. Keep model timing as evidence, not a blanket warning.
    if observation:
        timing = (f" · bản gần nhất, trễ {observation['age_minutes']} phút"
                  if observation['delayed'] else '')
        evidence = (f"Điện ly {name}: TEC tuyệt đối {observation['tec']} TECU (ước tính).\n"
                    f"So với nền 30 ngày: trung bình {observation['comparison']}; từng ô {observation['anomaly_low']:+.1f} đến {observation['anomaly_high']:+.1f} TECU.\n"
                    f"NOAA GloTEC {observation['stamp']}{timing}.")
    else:
        evidence = atmosphere
    short_magnetic = (f"Kp {value:.2f} · " +
                      ('bão từ mạnh' if value >= 7 else 'bão từ' if value >= 5 else
                       'nhiễu động' if value >= 4 else 'địa từ yên tĩnh') +
                      f" · {kp['stamp']}." if kp else 'Kp đang mất cập nhật.')
    if measurement or name != 'Việt Nam':
        opening = f'📍 {name}\n' if name != 'Việt Nam' else ''
        scope = ('\nAnh đang đo ở tỉnh nào?'
                 if name == 'Việt Nam' else '')
        return (f'{opening}{decision}\n\n{field_advice(health.get("status"), value, words)}'
                f'\n\n{evidence}\n{short_magnetic}{scope}')
    return (f'{headline}\n{short_magnetic}\n\n{evidence}\n'
            'Kp yên tĩnh hay TEC gần nền đều không xác nhận độ ổn định RTK tại điểm đo.')
