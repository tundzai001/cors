"""Resolve Auto Base locations using the existing administrative boundary snapshot."""
import json
import math
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name('data')


@lru_cache(maxsize=1)
def _catalog():
    features = json.loads((DATA / 'vietnam_provinces.geojson').read_text(encoding='utf-8'))['features']
    return features


@lru_cache(maxsize=1)
def _projections():
    return json.loads((DATA / 'coordinate_provinces.json').read_text(encoding='utf-8'))


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
    features = _catalog()
    matches = []
    for feature in features:
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        if any(_in_ring(lon, lat, polygon[0]) and not any(_in_ring(lon, lat, hole) for hole in polygon[1:]) for polygon in polygons):
            matches.append(feature['properties'])
    result = {'input_coord':{'lat':lat, 'lon':lon, 'alt':alt}, 'source':'administrative_boundaries_34',
                  'province_code':None, 'province_name':None, 'projection_options':[], 'l0_deg':None, 'k0':None}
    if len(matches) != 1:
        result['state'] = 'boundary_ambiguous' if matches else 'outside_boundaries'
        return result
    province = matches[0]
    # The reference allows local provincial L0/k0 or RTCM 1025, not an inferred UTM zone.
    options = [{'region':p['name'], 'l0_deg':p['central_meridian'], 'k0':0.9999, 'zone_width_deg':3}
               for p in _projections() if p['current_code'] == province['code']]
    result.update(province_code=province['code'], province_name=province['fullName'], projection_options=options,
                  state='projection_ambiguous')
    meridians = {p['l0_deg'] for p in options}
    if len(meridians) == 1:
        result.update(state='resolved', l0_deg=meridians.pop(), k0=0.9999, zone_width_deg=3)
    return result
