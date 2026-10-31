"""Resolve Auto Base locations using the existing administrative boundary snapshot."""
import json
import math
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name('data')


@lru_cache(maxsize=1)
def _catalog():
    features = json.loads((DATA / 'vietnam_provinces.geojson').read_text(encoding='utf-8'))['features']
    projections = json.loads((DATA / 'coordinate_provinces.json').read_text(encoding='utf-8'))
    return features, projections


def _in_ring(x, y, ring):
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        cross = (x - a[0]) * (b[1] - a[1]) - (y - a[1]) * (b[0] - a[0])
        if abs(cross) < 1e-12 and min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1]):
            return True
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def resolve_location(lat, lon, alt):
    if not all(math.isfinite(v) for v in (lat, lon, alt)) or abs(lat) > 90 or abs(lon) > 180:
        raise ValueError('LLH không hợp lệ')
    features, projections = _catalog()
    matches = []
    for feature in features:
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        if any(_in_ring(lon, lat, polygon[0]) and not any(_in_ring(lon, lat, hole) for hole in polygon[1:]) for polygon in polygons):
            matches.append(feature['properties'])
    result = dict(input_coord=dict(lat=lat, lon=lon, alt=alt), source='administrative_boundaries_34',
                  province_code=None, province_name=None, projection_options=[], l0_deg=None, k0=None)
    if len(matches) != 1:
        result['state'] = 'boundary_ambiguous' if matches else 'outside_boundaries'
        return result
    province = matches[0]
    options = [dict(region=p['name'], l0_deg=p['central_meridian'], k0=0.9999)
               for p in projections if p['current_code'] == province['code']]
    meridians = {option['l0_deg'] for option in options}
    result.update(province_code=province['code'], province_name=province['fullName'], projection_options=options,
                  state='resolved' if len(meridians) == 1 else 'projection_selection_required')
    if len(meridians) == 1:
        result.update(l0_deg=meridians.pop(), k0=0.9999)
    return result
