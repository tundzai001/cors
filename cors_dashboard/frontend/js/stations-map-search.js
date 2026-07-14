// Search and nearest-station helpers for the station map.
(() => {
    const VIETNAM_VIEWBOX = '102,24,110,8';
    const AVAILABLE_STATUSES = new Set(['online', 'streaming', 'sleep']);
    const SEARCH_STOP_WORDS = new Set([
        'viet', 'nam', 'vietnam', 'vn', 'dia', 'diem', 'tim', 'kiem',
        'o', 'tai', 'gan', 'khu', 'vuc', 'gi', 'day', 'nay', 'kia', 'cho'
    ]);
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
            .replace(/\u0111/g, 'd')
            .replace(/\u0110/g, 'D')
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
        return [...new Set(variants.filter(Boolean))];
    }

    function getLocalFallback(query) {
        const normalized = normalizeSearchText(query);
        const item = LOCAL_FALLBACKS.find(candidate =>
            candidate.match.some(pattern => normalized.includes(pattern))
        );
        return item ? buildResult(item.lat, item.lon, item.label, 'local') : null;
    }

    function meaningfulTokens(value) {
        return normalizeSearchText(value)
            .split(' ')
            .filter(token => token.length >= 2 && !SEARCH_STOP_WORDS.has(token));
    }

    function isRelevantNominatimResult(query, item) {
        const countryCode = String(item?.address?.country_code || '').toLowerCase();
        if (countryCode && countryCode !== 'vn') return false;

        const queryTokens = [...new Set(meaningfulTokens(query))];
        if (!queryTokens.length) return false;

        const names = Object.values(item?.namedetails || {}).join(' ');
        const resultTokens = new Set(meaningfulTokens(`${item?.display_name || ''} ${names}`));
        const matches = queryTokens.filter(token => resultTokens.has(token)).length;
        const minimumMatches = queryTokens.length === 1
            ? 1
            : Math.min(2, Math.ceil(queryTokens.length / 2));
        return matches >= minimumMatches;
    }

    async function queryNominatim(query) {
        const params = new URLSearchParams({
            format: 'jsonv2',
            limit: '5',
            countrycodes: 'vn',
            viewbox: VIETNAM_VIEWBOX,
            bounded: '1',
            addressdetails: '1',
            namedetails: '1',
            dedupe: '1',
            'accept-language': 'vi,en',
            q: appendVietnam(query)
        });
        const response = await fetch(`https://nominatim.openstreetmap.org/search?${params.toString()}`);
        if (!response.ok) throw new Error(`Geocoding service error (${response.status})`);
        const data = await response.json();
        if (!Array.isArray(data)) return null;
        for (const item of data) {
            if (!isRelevantNominatimResult(query, item)) continue;
            const result = buildResult(Number(item.lat), Number(item.lon), item.display_name || query, 'osm');
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
            const osm = await queryNominatim(variant);
            if (osm) return osm;
        }

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
