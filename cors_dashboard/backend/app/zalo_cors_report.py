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
from rapidfuzz import fuzz, process

from .ionosphere_service import ionosphere_service
from .zalo_cors_dialogue import cors_intent, static_reply, STATIC_INTENTS, DIAGNOSTIC_INTENTS


def _plain(text):
    text = unicodedata.normalize('NFD', text.lower().replace('đ', 'd'))
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).strip()


@lru_cache(maxsize=1)
def _regions():
    regions_file = Path(__file__).parent / 'data/vietnam_regions.json'
    if regions_file.exists():
        raw = json.loads(regions_file.read_text(encoding='utf-8'))
        result = [(item['name'], tuple(item['bounds']), set(item['aliases'])) for item in raw]
    else:
        features = json.loads((Path(__file__).parent / 'data/vietnam_provinces.geojson').read_text(encoding='utf-8'))['features']
        result = []
        for feature in features:
            props, geometry = feature['properties'], feature['geometry']
            polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
            points = [point for polygon in polygons for point in polygon[0]]
            bounds = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
            result.append((props['name'], bounds, {_plain(props.get(key, '')) for key in ('name', 'nameEn')} - {''}))
    abbreviations = {'Hà Nội': {'hn'}, 'Hồ Chí Minh': {'hcm', 'tphcm', 'tp hcm', 'sg'},
                     'Bà Rịa - Vũng Tàu': {'brvt', 'br vt'},
                     'Đà Nẵng': {'dn'}, 'Đồng Nai': {'dn'}}
    for name, _, aliases in result:
        aliases.update({alias.replace(' ', '') for alias in aliases if len(alias.replace(' ', '')) >= 6})
        aliases.update(abbreviations.get(name, set()))
    return result


@lru_cache(maxsize=1)
def _fuzzy_aliases():
    groups = {}
    for name, bounds, aliases in _regions():
        for alias in sorted(aliases):
            if len(alias) >= 6:
                groups.setdefault(len(alias.split()), {})[alias] = (name, bounds)
    return groups


def region_options(question):
    return _region_options(_plain(question)[:1000])


@lru_cache(maxsize=256)
def _region_options(words):
    text = f' {words} '
    exact, covered = {}, []
    for name, bounds, aliases in _regions():
        for alias in aliases:
            for match in re.finditer(r'(?<!\S)' + re.escape(alias) + r'(?!\S)', text):
                exact[(name, bounds)] = 100
                covered.append(match.span())
    scores, spans = {}, {}
    tokens = list(re.finditer(r'\S+', text))[:100]
    for size, choices in _fuzzy_aliases().items():
        for index in range(len(tokens) - size + 1):
            start, end = tokens[index].start(), tokens[index + size - 1].end()
            if any(start < right and end > left for left, right in covered):
                continue  # Do not turn an already resolved Hà Nội into Hà Nam.
            for alias, score, _ in process.extract(text[start:end], list(choices), scorer=fuzz.ratio, score_cutoff=90, limit=3):
                region = choices[alias]
                if score > scores.get(region, 0):
                    scores[region], spans[region] = score, (start, end)
    ranked = sorted(scores, key=lambda region: (-scores[region], region[0]))
    if exact:
        # A second, separately written province must not be replaced by chat history.
        return tuple(dict.fromkeys([*exact, *ranked[:2]]))[:3]
    if len(ranked) > 1:
        first, second = ranked[:2]
        separate = spans[first][1] <= spans[second][0] or spans[second][1] <= spans[first][0]
        if separate or scores[first] - scores[second] < 5:
            return tuple(ranked[:3])
    return tuple(ranked[:1])


def region_for_question(question):
    options = region_options(question)
    return options[0] if len(options) == 1 else ('Việt Nam', (102, 8, 110, 24))


def is_region_only(question):
    words = re.sub(r'^(?:(?:the con|con|o|tai|tinh|thanh pho|tp)\s+)+', '', _plain(question))
    aliases = {alias for _, _, names in _regions() for alias in names}
    if words in aliases:
        return True
    return bool(process.extractOne(words, [alias for alias in aliases if len(alias) >= 6],
                                   scorer=fuzz.ratio, score_cutoff=90))


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
    return 'Anh đang dùng trạm hoặc mountpoint nào?'


async def report_text(health, question='', previous_intent='status'):
    words = _plain(question)
    intent = cors_intent(words, previous_intent)
    if intent in STATIC_INTENTS:
        return static_reply(intent)
    options = region_options(question)
    if len(options) > 1:
        names = ' hay '.join(option[0] for option in options)
        return f'Anh muốn kiểm tra {names}? Mình cần xác định đúng khu vực trước nhé.'
    name = region_for_question(question)[0]
    heading = f'📍 {name}\n' if name != 'Việt Nam' else ''
    status = health.get('status')
    headline = {
        'online': 'CORS Cục đang hoạt động.',
        'offline': 'CORS Cục đang gián đoạn.',
    }.get(status, 'Chưa xác định được trạng thái CORS Cục.')

    # Source availability is not evidence of local RTCM delivery or receiver FIX.
    # Ordinary questions need no NOAA request and no atmospheric data dump.
    if intent == 'summary' and previous_intent in DIAGNOSTIC_INTENTS:
        return (f'{heading}{headline} Nếu máy vẫn FLOAT, xem RTCM và tuổi hiệu chỉnh trước; '
                'dữ liệu vẫn đều thì kiểm tra che khuất và khoảng cách trạm.')
    if intent in {'status', 'stability', 'summary'}:
        if status == 'offline':
            return f'{heading}{headline} Nếu anh đang dùng nguồn này, chuyển sang nguồn dự phòng đã kiểm tra.'
        if status != 'online':
            return f'{heading}{headline} Anh đang dùng trạm hoặc mountpoint nào để mình khoanh vùng tiếp?'
        if name == 'Việt Nam':
            return f'{headline} Anh muốn kiểm tra khu vực hoặc trạm nào?'
        return (f'{heading}{headline} Nhưng mình chưa có số liệu RTCM/FIX tại khu vực này '
                'để xác nhận độ ổn định.\nAnh đang dùng trạm hoặc mountpoint nào?')

    if intent == 'diagnostic_steps':
        return (f'{heading}{headline}\n'
                '1. Xem số byte RTCM có tăng và tuổi hiệu chỉnh có cập nhật không.\n'
                '2. Nếu mất dữ liệu, kiểm tra mạng, tài khoản và đúng mountpoint.\n'
                '3. Nếu dữ liệu vẫn đều mà chưa FIX, kiểm tra che khuất và khoảng cách tới trạm.\n'
                'Anh gửi tuổi hiệu chỉnh hiện tại để mình khoanh vùng bước tiếp nhé.')

    if intent == 'diagnostic_age':
        age = re.search(r'\b(\d{1,3})\s*(?:s|giay)\b', words)
        age_text = f"{int(age[1])} giây" if age else 'một thời điểm'
        return (f'{heading}{headline}\nTuổi hiệu chỉnh {age_text} là một lần quan sát, '
                'chưa đủ để kết luận trạm hay mạng có lỗi. '
                'Anh nhìn thêm vài giây: số này giữ quanh mức đó hay tăng liên tục?')
    if intent == 'diagnostic_stream':
        return (f'{heading}{headline}\nNếu tuổi hiệu chỉnh tăng liên tục, dữ liệu hiệu chỉnh đang không cập nhật đều. '
                'Anh xem số byte RTCM còn tăng không; nếu đứng yên, kiểm tra mạng và kết nối đúng mountpoint trước.')
    if intent == 'diagnostic_signal':
        return (f'{heading}{headline}\nTuổi hiệu chỉnh cập nhật đều là một tín hiệu, chưa bảo đảm máy sẽ FIX. '
                'Nếu vẫn FLOAT, kiểm tra che khuất, khoảng cách tới trạm và đối chiếu máy khác cùng khu vực. '
                'Anh đang cách trạm khoảng bao nhiêu km?')

    async def fetch_ionosphere():
        if intent not in {'detail', 'detail_brief', 'forecast'}:
            return {}
        try:
            return await asyncio.wait_for(ionosphere_service.get_latest(format_type='geojson'), timeout=15)
        except Exception:
            return {}

    data, kp = await asyncio.gather(fetch_ionosphere(), geomagnetic_observation())
    value = kp['value'] if kp else None
    magnetic = (f"Địa từ Kp {value:.2f} · " +
                ('bão từ mạnh, nguy cơ gián đoạn định vị tăng.' if value >= 7 else
                 'đang có bão từ; cần theo dõi chất lượng định vị.' if value >= 5 else
                 'đang nhiễu động.' if value >= 4 else 'mức yên tĩnh.') +
                f" NOAA SWPC · {kp['stamp']}." if kp else
                'Chỉ số địa từ đang tạm mất cập nhật.')

    if intent == 'diagnostic':
        if status == 'offline':
            action = 'Xử lý nguồn hiệu chỉnh trước vì nguồn Cục đang gián đoạn. Chưa cần đổi thiết lập vệ tinh.'
        elif value is not None and value >= 5:
            action = ('Hiện có nhiễu động địa từ, nhưng chưa đủ để kết luận nguyên nhân mất FIX. '
                      'Anh gửi tuổi hiệu chỉnh và mountpoint đang dùng để mình khoanh vùng tiếp nhé.')
        else:
            action = ('Anh kiểm tra tuổi hiệu chỉnh trước nhé: nếu tuổi tăng liên tục hoặc mất RTCM, '
                      'kiểm tra mạng và mountpoint. Nếu hiệu chỉnh vẫn đều, kiểm tra khoảng cách trạm và che khuất. '
                      'Tuổi hiệu chỉnh hiện bao nhiêu giây?')
        return f'{heading}{headline}\n{action}' + (f'\n{magnetic}' if value is not None and value >= 4 else '')

    atmosphere = ionosphere_text(data, question)
    if intent == 'forecast':
        return (f'{heading}Mình chỉ có tình hình hiện tại, chưa phải dự báo cho ca đo anh hỏi.\n'
                f'{headline}\n{magnetic}\n{atmosphere}')
    if intent == 'detail_brief':
        observation = ionosphere_observation(data, question)
        if not observation:
            return f'{heading}{headline} Điện ly chưa có dữ liệu cập nhật để đánh giá.'
        delay = f"; trễ {observation['age_minutes']} phút" if observation['delayed'] else ''
        return (f"{heading}{headline} TEC {observation['tec']} TECU, {observation['comparison']} "
                f"(ước tính NOAA {observation['stamp']}{delay}).\n{magnetic}")
    if intent == 'detail':
        return (f'{heading}{headline}\n\n{atmosphere}\n{magnetic}\n'
                'TEC là ước tính điện ly khu vực, không phải sai số đo hay chất lượng FIX của máy.')

    if status == 'offline':
        decision = 'Cục đang gián đoạn, ca đo dùng nguồn này cần chuyển sang nguồn dự phòng hoặc chờ kết nối lại.'
    elif status != 'online':
        decision = 'Mình chưa xác nhận được nguồn Cục nên chưa thể chốt phương án đo dùng nguồn này.'
    elif value is not None and value >= 7:
        decision = 'Nên hoãn đo khống chế: hiện có bão từ mạnh, nguy cơ gián đoạn định vị tăng.'
    elif value is not None and value >= 4:
        decision = 'CORS Cục đang hoạt động, nhưng địa từ đang nhiễu động. Ca đo chính xác cao cần thận trọng.'
    else:
        decision = (f'{headline} Chưa có số liệu RTCM/FIX tại điểm đo để xác nhận ca đo ổn định.')
    advice = field_advice(status, value, words)
    if name == 'Việt Nam' and advice == 'Anh đang dùng trạm hoặc mountpoint nào?':
        advice = 'Anh đang đo ở tỉnh nào?'
    if value is None:
        advice += '\nChỉ số địa từ đang tạm mất cập nhật.'
    scope = '\nAnh đang đo ở tỉnh nào?' if name == 'Việt Nam' and '?' not in advice else ''
    return f'{heading}{decision}\n{advice}{scope}'
