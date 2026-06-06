// Search and nearest-station helpers for the station map.
(() => {
    const VIETNAM_VIEWBOX = '102,24,110,8';
    const AVAILABLE_STATUSES = new Set(['online', 'streaming', 'sleep']);
    const LOCAL_FALLBACKS = [
        {
            match: ['sin quyen', 'sinquyen'],
            lat: 22.585,
            lon: 103.82,
            label: 'Mo dong Sin Quyen, Bat Xat, Lao Cai, Viet Nam (uoc luong)'
        }
    ];

    function toast(type, message) {
        if (window.CorsUI?.showToast) {
            window.CorsUI.showToast(type, message);
        }
    }

    function normalizeSearchText(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/đ/g, 'd')
            .replace(/Đ/g, 'D')
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, ' ')
            .trim();
    }

    function isVietnamBBox(lat, lon) {
        return lat >= 8 && lat <= 24 && lon >= 102 && lon <= 110;
    }

    function buildResult(lat, lon, label, source) {
        if (!Number.isFinite(lat) || !Number.isFinite(lon) || !isVietnamBBox(lat, lon)) return null;
        return { lat, lon, label, source };
    }

    function parseCoordinateText(query) {
        const raw = String(query || '').trim();
        const decoded = decodeURIComponent(raw);
        const patterns = [
            /@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/,
            /!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/,
            /[?&](?:q|query|ll)=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/,
            /(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)/
        ];
        for (const pattern of patterns) {
            const match = decoded.match(pattern);
            if (!match) continue;
            const lat = Number(match[1]);
            const lon = Number(match[2]);
            const result = buildResult(lat, lon, `Toa do: ${lat}, ${lon}`, 'manual');
            if (result) return result;
        }
        return null;
    }

    function appendVietnam(query) {
        const q = String(query || '').trim();
        const normalized = normalizeSearchText(q);
        if (/\b(viet nam|vn)\b/i.test(normalized)) return q;
        return `${q}, Viet Nam`;
    }

    function buildQueryVariants(query) {
        const q = String(query || '').trim();
        const normalized = normalizeSearchText(q);
        const variants = [q];
        if (normalized.includes('sin quyen') || normalized.includes('sinquyen')) {
            variants.push(
                'Sin Quyen Mine, Bat Xat, Lao Cai, Viet Nam',
                'Mo dong Sin Quyen, Ban Vuoc, Bat Xat, Lao Cai, Viet Nam',
                'Sin Quyen, Ban Vuoc, Bat Xat, Lao Cai, Viet Nam'
            );
        }
        variants.push(
            q.replace(/\bmo\s*dong\b/i, '').trim(),
            `${q}, Lao Cai, Viet Nam`,
            `${q}, Viet Nam`
        );
        return [...new Set(variants.filter(Boolean))];
    }

    function getLocalFallback(query) {
        const normalized = normalizeSearchText(query);
        const item = LOCAL_FALLBACKS.find(candidate =>
            candidate.match.some(pattern => normalized.includes(pattern))
        );
        return item ? buildResult(item.lat, item.lon, item.label, 'local') : null;
    }

    async function queryNominatim(query) {
        const params = new URLSearchParams({
            format: 'json',
            limit: '3',
            countrycodes: 'vn',
            viewbox: VIETNAM_VIEWBOX,
            q: appendVietnam(query)
        });
        const response = await fetch(`https://nominatim.openstreetmap.org/search?${params.toString()}`);
        const data = await response.json();
        if (!Array.isArray(data)) return null;
        for (const item of data) {
            const result = buildResult(Number(item.lat), Number(item.lon), item.display_name || query, 'osm');
            if (result) return result;
        }
        return null;
    }

    async function queryWikipedia(query, lang = 'vi') {
        const params = new URLSearchParams({
            action: 'query',
            format: 'json',
            origin: '*',
            generator: 'search',
            gsrsearch: appendVietnam(query),
            gsrlimit: '5',
            prop: 'coordinates',
            colimit: '5'
        });
        const response = await fetch(`https://${lang}.wikipedia.org/w/api.php?${params.toString()}`);
        const data = await response.json();
        const pages = Object.values(data?.query?.pages || {});
        for (const page of pages) {
            const coord = page.coordinates?.[0];
            if (!coord) continue;
            const result = buildResult(Number(coord.lat), Number(coord.lon), page.title || query, `wikipedia-${lang}`);
            if (result) return result;
        }
        return null;
    }

    async function queryWikidata(query) {
        const sparql = `
SELECT ?item ?itemLabel ?coord WHERE {
  SERVICE wikibase:mwapi {
    bd:serviceParam wikibase:endpoint "www.wikidata.org";
                    wikibase:api "EntitySearch";
                    mwapi:search "${String(query).replace(/"/g, '\\"')}";
                    mwapi:language "vi".
    ?item wikibase:apiOutputItem mwapi:item.
  }
  ?item wdt:P625 ?coord.
  SERVICE wikibase:label { bd:serviceParam wikibase:language "vi,en". }
}
LIMIT 5`;
        const params = new URLSearchParams({ query: sparql, format: 'json' });
        const response = await fetch(`https://query.wikidata.org/sparql?${params.toString()}`);
        const data = await response.json();
        const rows = data?.results?.bindings || [];
        for (const row of rows) {
            const match = String(row.coord?.value || '').match(/Point\(([-\d.]+) ([-\d.]+)\)/);
            if (!match) continue;
            const lon = Number(match[1]);
            const lat = Number(match[2]);
            const result = buildResult(lat, lon, row.itemLabel?.value || query, 'wikidata');
            if (result) return result;
        }
        return null;
    }

    async function geocodeVietnam(query) {
        const q = String(query || '').trim();
        if (!q) throw new Error('Missing location query');

        const coord = parseCoordinateText(q);
        if (coord) return coord;

        for (const variant of buildQueryVariants(q)) {
            const wiki = await queryWikipedia(variant, 'vi');
            if (wiki) return wiki;
            const wikiEn = await queryWikipedia(variant, 'en');
            if (wikiEn) return wikiEn;
            const osm = await queryNominatim(variant);
            if (osm) return osm;
        }

        const wikidata = await queryWikidata(q);
        if (wikidata) return wikidata;

        const fallback = getLocalFallback(q);
        if (fallback) return fallback;

        throw new Error('Khong tim thay toa do. Thu dan link Google Maps hoac nhap lat,lon.');
    }

    function openGoogleMapsSearch(query) {
        const q = String(query || '').trim();
        if (!q) return;
        window.open(`https://www.google.com/maps/search/${encodeURIComponent(q)}`, '_blank', 'noopener,noreferrer');
    }

    function clearOverlay(map, nearestRef) {
        if (!map || !nearestRef?.current) return;
        if (nearestRef.current.marker) {
            map.removeLayer(nearestRef.current.marker);
            nearestRef.current.marker = null;
        }
        (nearestRef.current.layers || []).forEach(layer => map.removeLayer(layer));
        nearestRef.current.layers = [];
    }

    function buildStationItems(devices, getCoords, effectiveStatus, getPreferredServer) {
        return (devices || [])
            .map(device => ({
                device,
                coords: getCoords(device),
                status: effectiveStatus(device),
                traffic: getPreferredServer(device)
            }))
            .filter(item => item.coords && Number.isFinite(item.coords.lat) && Number.isFinite(item.coords.lon));
    }

    function findNearestItems(map, origin, stationItems, L) {
        const withDistance = stationItems.map(item => ({
            ...item,
            dist: map.distance(origin, L.latLng(item.coords.lat, item.coords.lon))
        }));
        const available = withDistance.filter(item =>
            AVAILABLE_STATUSES.has(item.status) || Number(item.traffic?.bps || 0) > 0
        );
        const offline = withDistance.filter(item => !available.includes(item));
        const byDistance = (a, b) => a.dist - b.dist;
        return {
            nearestAvailable: available.sort(byDistance)[0] || null,
            nearestOffline: offline.sort(byDistance)[0] || null
        };
    }

    function drawResult({ map, nearestRef, origin, label, nearestAvailable, nearestOffline }) {
        const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
        if (!L || !map) return;

        clearOverlay(map, nearestRef);

        nearestRef.current.marker = L.circleMarker(origin, {
            radius: 7,
            color: '#38bdf8',
            fillColor: '#0ea5e9',
            fillOpacity: 1,
            weight: 3
        }).addTo(map).bindPopup(label).openPopup();

        const drawLine = (target, color, prefix) => {
            if (!target) return;
            const line = L.polyline([origin, L.latLng(target.coords.lat, target.coords.lon)], {
                color,
                weight: 4,
                opacity: 0.95,
                dashArray: '10, 10'
            }).addTo(map);
            const km = (target.dist / 1000).toFixed(2);
            line.bindTooltip(`${prefix} ${target.device.name || target.device.serial}: ${km} km`, {
                permanent: true,
                direction: 'center',
                className: 'station-map-distance-label'
            }).openTooltip();
            nearestRef.current.layers.push(line);
        };

        drawLine(nearestAvailable, '#22c55e', 'Kha dung');
        drawLine(nearestOffline, '#ef4444', 'Offline');

        const bounds = L.latLngBounds([origin]);
        if (nearestAvailable) bounds.extend([nearestAvailable.coords.lat, nearestAvailable.coords.lon]);
        if (nearestOffline) bounds.extend([nearestOffline.coords.lat, nearestOffline.coords.lon]);
        map.flyToBounds(bounds, { padding: [70, 70], maxZoom: 13, duration: 0.9 });
    }

    function findNearestFromCoords(options) {
        const {
            lat,
            lon,
            label = 'Vi tri cua ban',
            map,
            devices,
            nearestRef,
            getCoords,
            effectiveStatus,
            getPreferredServer
        } = options || {};
        const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
        if (!L || !map) return null;

        const stationItems = buildStationItems(devices, getCoords, effectiveStatus, getPreferredServer);
        if (!stationItems.length) {
            toast('warning', 'Khong co tram nao co toa do hop le.');
            return null;
        }

        const origin = L.latLng(lat, lon);
        const result = findNearestItems(map, origin, stationItems, L);
        if (!result.nearestAvailable && !result.nearestOffline) return null;

        drawResult({ map, nearestRef, origin, label, ...result });

        const parts = [];
        if (result.nearestAvailable) {
            parts.push(`Kha dung: ${result.nearestAvailable.device.name || result.nearestAvailable.device.serial} (${(result.nearestAvailable.dist / 1000).toFixed(2)} km)`);
        }
        if (result.nearestOffline) {
            parts.push(`Offline: ${result.nearestOffline.device.name || result.nearestOffline.device.serial} (${(result.nearestOffline.dist / 1000).toFixed(2)} km)`);
        }
        toast('success', `Tram gan nhat - ${parts.join(' | ')}`);
        return result;
    }

    function findNearestFromBrowser(options, onDone) {
        if (!navigator.geolocation) {
            toast('warning', 'Trinh duyet khong ho tro lay vi tri.');
            onDone?.();
            return;
        }
        navigator.geolocation.getCurrentPosition((pos) => {
            findNearestFromCoords({
                ...options,
                lat: pos.coords.latitude,
                lon: pos.coords.longitude,
                label: 'Vi tri cua ban'
            });
            onDone?.();
        }, () => {
            toast('warning', 'Khong the lay vi tri hien tai.');
            onDone?.();
        }, { enableHighAccuracy: true, timeout: 10000 });
    }

    window.StationsMapSearch = {
        geocodeVietnam,
        openGoogleMapsSearch,
        clearOverlay,
        findNearestFromCoords,
        findNearestFromBrowser
    };
})();
