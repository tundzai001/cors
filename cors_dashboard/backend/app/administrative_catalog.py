"""Versioned administrative names; names alone do not establish coordinates."""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name('data')
VERSIONS = {'current': 'v5.2.0', 'legacy': 'v2.4.1'}
PREFIX = r'^(?:thanh pho|thi xa|thi tran|dac khu|tinh|huyen|quan|phuong|xa|tp|tx)\s+'


def normalize(value):
    text = unicodedata.normalize('NFD', str(value or '')).lower().replace('đ', 'd')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def query_parts(query):
    # Strip type labels once per address segment; do not erase words inside names.
    return [re.sub(PREFIX, '', normalize(part)) for part in re.split(r'[,;]', str(query or ''))
            if normalize(part) not in ('viet nam', 'vietnam', 'vn')]


def matches_place(query, name, display_name):
    parts = query_parts(query)
    tokens = ' '.join(parts).split()
    own = normalize(name)
    label = ' '.join(query_parts(display_name))
    if not tokens or not own:
        return False
    # A qualifier can narrow a name, but cannot turn every ward in a province into a hit.
    primary = parts[0] if parts else ''
    if len(parts) == 1 and own.startswith(primary):
        return True
    if primary != own and not primary.startswith(own + ' '):
        return False
    qualifiers = [primary[len(own):].strip(), *parts[1:]]
    return all((' ' + part + ' ') in (' ' + label + ' ') for part in qualifiers if part)


def name_matches_query(query, display_name):
    tokens = ' '.join(query_parts(query)).split()
    label = normalize(display_name)
    if not tokens:
        return False
    # The specific place phrase must survive, not just scattered province words.
    return (' ' + ' '.join(tokens[:2]) + ' ') in (' ' + label + ' ') and all(
        token in label.split() for token in tokens)


@lru_cache(maxsize=2)
def catalog(dataset):
    filename = 'vietnam_units_legacy.json' if dataset == 'legacy' else 'vietnam_units.json'
    provinces = json.loads((DATA / filename).read_text(encoding='utf-8'))
    rows = []

    def add(unit, kind, province=None, district=None):
        parents = [parent.get('FullName') or parent['Name'] for parent in (district, province) if parent]
        rows.append(dict(
            type=kind, code=unit['Code'], name=unit['Name'],
            full_name=unit.get('FullName') or unit['Name'],
            province_code=province['Code'] if province else unit['Code'],
            province_name=(province or unit).get('FullName'),
            district_code=district['Code'] if district else None,
            district_name=district.get('FullName') if district else None,
            display_name=', '.join([unit.get('FullName') or unit['Name'], *parents]),
            dataset=dataset, dataset_version=VERSIONS[dataset],
            lat=None, lon=None, coordinate_source=None,
            source='Cơ sở dữ liệu hành chính Việt Nam',
            storage='bundled_catalogue',
        ))

    for province in provinces:
        add(province, 'province')
        for ward in province.get('Wards') or province.get('Ward') or []:
            add(ward, 'ward', province)
        for district in province.get('District') or []:
            add(district, 'district', province)
            for ward in district.get('Ward') or []:
                add(ward, 'ward', province, district)
    return rows


def search_catalog(query, limit=15, dataset='all'):
    datasets = ('current', 'legacy') if dataset == 'all' else (dataset,)
    rows = [row for version in datasets for row in catalog(version)
            if matches_place(query, row['name'], row['display_name'])]
    parts = query_parts(query)
    first = parts[0] if parts else ''
    rows.sort(key=lambda row: (normalize(row['name']) != first,
                              row['dataset'] != 'current', len(normalize(row['name'])),
                              row['display_name']))
    return [dict(row) for row in rows[:limit]]
