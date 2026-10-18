// Command-center station map.
(() => {
    const { useEffect, useMemo, useRef, useState } = React;
    const COVERAGE_RADIUS_M = 20000;
    const PREFERRED_HOST = 'aitogy.com.vn';
    const DEFAULT_CENTER = [16.2, 106.3];
    const DEFAULT_ZOOM = 6;

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[char]);
    }

    function toNumber(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    }

    function isVietnamCoord(lat, lon) {
        return lat >= 6 && lat <= 24 && lon >= 102 && lon <= 118;
    }

    function normalizeCoords(latRaw, lonRaw) {
        const lat = toNumber(latRaw);
        const lon = toNumber(lonRaw);
        if (lat === null || lon === null || (lat === 0 && lon === 0)) return null;
        if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
            if (isVietnamCoord(lat, lon)) return { lat, lon };
            if (isVietnamCoord(lon, lat)) return { lat: lon, lon: lat };
            return { lat, lon };
        }
        if (Math.abs(lon) <= 90 && Math.abs(lat) <= 180) {
            return { lat: lon, lon: lat };
        }
        return null;
    }

    function pointInRing(pt, ring) {
        let inside = false;
        const x = pt[0], y = pt[1];
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
            const xi = ring[i][0], yi = ring[i][1];
            const xj = ring[j][0], yj = ring[j][1];
            const intersect = ((yi > y) !== (yj > y))
                && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
            if (intersect) inside = !inside;
        }
        return inside;
    }

    function pointInGeometry(pt, geom) {
        if (!geom || !geom.coordinates) return false;
        if (geom.type === 'Polygon') {
            const coords = geom.coordinates;
            if (!coords[0] || !pointInRing(pt, coords[0])) return false;
            for (let i = 1; i < coords.length; i++) {
                if (pointInRing(pt, coords[i])) return false;
            }
            return true;
        }
        if (geom.type === 'MultiPolygon') {
            for (let p = 0; p < geom.coordinates.length; p++) {
                const coords = geom.coordinates[p];
                if (coords[0] && pointInRing(pt, coords[0])) {
                    let inHole = false;
                    for (let i = 1; i < coords.length; i++) {
                        if (pointInRing(pt, coords[i])) { inHole = true; break; }
                    }
                    if (!inHole) return true;
                }
            }
            return false;
        }
        return false;
    }

    function getCoords(device) {
        const cfg = device?.base_config || {};
        const cfgCoords = cfg.coords || {};
        const candidates = [
            [cfgCoords.lat, cfgCoords.lon],
            [cfgCoords.latitude, cfgCoords.longitude],
            [device?.lat, device?.lon],
            [device?.latitude, device?.longitude],
            [cfg.lat, cfg.lon],
            [cfg.latitude, cfg.longitude],
            [cfg.fixed_lat, cfg.fixed_lon]
        ];
        for (const [latRaw, lonRaw] of candidates) {
            const coords = normalizeCoords(latRaw, lonRaw);
            if (coords) return coords;
        }
        return null;
    }

    function effectiveStatus(device) {
        if (String(device?.status || '').toLowerCase() === 'locked' || device?.is_locked) return 'locked';
        return window.CorsDashboardShared?.getMapEffectiveStatus?.(device) || String(device?.status || 'offline').toLowerCase();
    }

    function getPreferredServer(device) {
        const cfg = device?.service_config || {};
        const servers = [1, 2].map((idx) => ({
            idx,
            host: String(cfg[`serverhost${idx}`] || '').trim(),
            mountpoint: String(cfg[`mountpoint${idx}`] || '').trim(),
            bps: Number(window.CorsDashboardShared?.getServerTelemetry?.(device, idx)?.bps ?? device?.bps ?? 0)
        }));
        return (
            servers.find(s => s.host.toLowerCase().includes(PREFERRED_HOST)) ||
            servers.find(s => s.host) ||
            { idx: 1, host: '', mountpoint: String(device?.mountpoint || '').trim(), bps: Number(device?.bps || 0) }
        );
    }

    function popupHtml(device, status, traffic) {
        const meta = window.CorsUI.getStatusMeta(status);
        const coords = getCoords(device);
        let ionoLine = '';
        if (coords && window.IonosphereOverlay?.sample) {
            const probe = window.IonosphereOverlay.sample(coords.lat, coords.lon);
            if (probe && probe.tec !== undefined) {
                ionoLine = `<span class="station-map-popup-label">Local TEC</span><span class="station-map-popup-value" style="color:#38bdf8;font-weight:700;">${probe.tec.toFixed(1)} TECU</span>`;
            }
        }
        return `
            <div class="station-map-popup">
                <div class="d-flex justify-content-between align-items-start gap-2">
                    <div>
                        <div class="station-map-popup-title">${escapeHtml(device.name || device.serial || 'Station')}</div>
                        <div class="text-muted small">${escapeHtml(device.serial || '--')}</div>
                    </div>
                    <span class="badge" style="background:${meta.color}; color:#fff;">${meta.label}</span>
                </div>
                <div class="station-map-popup-grid">
                    <span class="station-map-popup-label">Lat/Lon</span><span class="station-map-popup-value">${coords ? `${coords.lat.toFixed(6)}, ${coords.lon.toFixed(6)}` : '--'}</span>
                    <span class="station-map-popup-label">Host</span><span class="station-map-popup-value">${escapeHtml(traffic.host || '--')}</span>
                    <span class="station-map-popup-label">Mountpoint</span><span class="station-map-popup-value">${escapeHtml(traffic.mountpoint || '--')}</span>
                    <span class="station-map-popup-label">Traffic out</span><span class="station-map-popup-value">${window.CorsUI.formatSpeed(traffic.bps)}</span>
                    ${ionoLine}
                </div>
                <div class="d-flex gap-1 mt-2">
                    ${(status === 'offline' || status === 'lost')
                        ? `<button type="button" class="btn btn-sm btn-outline-secondary flex-fill station-map-sat-btn opacity-75" data-serial="${escapeHtml(device.serial || '')}" data-offline="true" title="Trạm đang ngoại tuyến - Không thể thu tín hiệu">🛰️ Trạm ngoại tuyến</button>`
                        : `<button type="button" class="btn btn-sm btn-outline-info flex-fill station-map-sat-btn" data-serial="${escapeHtml(device.serial || '')}">🛰️ Theo dõi RTCM</button>`
                    }
                    <button type="button" class="btn btn-sm btn-primary flex-fill station-map-detail-btn" data-serial="${escapeHtml(device.serial || '')}">Open detail</button>
                </div>
            </div>
        `;
    }

    function referenceNetworkMeta(health) {
        const fresh = health?.checked_at && Date.now() / 1000 - health.checked_at < 75;
        const status = fresh ? health.status : 'unknown';
        return status === 'online' ? {label: 'Online', color: '#22c55e'}
            : status === 'offline' ? {label: 'Đang báo lỗi', color: '#ef4444'}
            : {label: 'Chưa xác định', color: '#94a3b8'};
    }

    function referenceStationPopup(station) {
        const health = station.serviceHealth;
        const meta = referenceNetworkMeta(health);
        return `<div class="station-map-popup">
            <div class="station-map-popup-title">CORS Cục · ${escapeHtml(station.code)}</div>
            <div class="text-muted small">Mạng CORS Cục · VNGEONET</div>
            <div class="station-map-popup-grid">
                <span class="station-map-popup-label">Lat/Lon</span>
                <span class="station-map-popup-value">${station.lat.toFixed(9)}, ${station.lon.toFixed(9)}</span>
            </div>
            <div class="station-map-popup-grid">
                <span class="station-map-popup-label">Độ cao (m)</span>
                <span class="station-map-popup-value">${Number.isFinite(station.heightM) ? station.heightM.toFixed(3) : '—'}</span>
            </div>
            <div class="small mt-2">Hệ thống Cục: <strong style="color:${meta.color}">${meta.label}</strong></div>
            <div class="small mt-1">Trạng thái RTCM từng trạm: chưa công bố.</div>
            <div class="small mt-2">Nguồn: <a href="https://vngeonet.vn/User/SiteMap/SiteMapPublic" target="_blank" rel="noopener noreferrer">VNGEONET</a> · 17/10/2026</div>
            <div class="small mt-1">Chưa xác định hệ quy chiếu và loại độ cao.</div>
        </div>`;
    }

    function provincePopupHtml(feature, stations) {
        const p = feature.properties || {};
        const inProvince = stations.filter(s =>
            pointInGeometry([s.coords.lon, s.coords.lat], feature.geometry)
        );
        const onlineCount = inProvince.filter(s =>
            ['online', 'streaming', 'sleep'].includes(s.status)
        ).length;

        const areaStr = p.areaKm2 ? `${Number(p.areaKm2).toLocaleString('vi-VN')} km²` : '--';
        const postalStr = p.postalCodePrefix || '--';

        return `
            <div class="station-map-popup">
                <div class="station-map-popup-title">${escapeHtml(p.fullName || p.name || 'Tỉnh/Thành')}</div>
                <div class="text-muted small">${escapeHtml(p.nameEn || '')}</div>
                <div class="station-map-popup-grid">
                    <span class="station-map-popup-label">Mã hành chính</span><span class="station-map-popup-value">${escapeHtml(p.code || '--')}</span>
                    <span class="station-map-popup-label">Mã bưu chính</span><span class="station-map-popup-value">${escapeHtml(postalStr)}</span>
                    <span class="station-map-popup-label">Diện tích</span><span class="station-map-popup-value">${escapeHtml(areaStr)}</span>
                    <span class="station-map-popup-label">Trạm Base</span><span class="station-map-popup-value text-success font-monospace">${onlineCount} online / ${inProvince.length} tổng</span>
                </div>
            </div>
        `;
    }

    function AllStationsMap({ devices = [], currentUser = null }) {
        const mapEl = useRef(null);
        const mapRef = useRef(null);
        const layerRef = useRef(null);
        const coverageRef = useRef(null);
        const roverClusterRef = useRef(null);
        const roverMarkersRef = useRef({});
        const roverHashRef = useRef('');
        const measureRef = useRef({ enabled: false, points: [], layers: [] });
        const nearestRef = useRef({ marker: null, layers: [] });
        const provincesLayerRef = useRef(null);
        const provincesDataRef = useRef(null);
        const vietnamMaskLayerRef = useRef(null);
        const sovereigntyMarkersRef = useRef([]);
        const [coverage, setCoverage] = useState(false);
        const [showStations, setShowStations] = useState(true);
        const [showVngeonet, setShowVngeonet] = useState(true);
        const [vngeonetHealth, setVngeonetHealth] = useState(null);
        const [showRovers, setShowRovers] = useState(true);
        const [showProvinces, setShowProvinces] = useState(true);
        const [showVietnamMask, setShowVietnamMask] = useState(false);
        const [showIonosphere, setShowIonosphere] = useState(false);
        const [ionosphereMode, setIonosphereMode] = useState('tec');
        const [ionosphereOpacity, setIonosphereOpacity] = useState(0.65);
        const [ionosphereInfo, setIonosphereInfo] = useState(null);
        const showIonosphereRef = useRef(false);
        showIonosphereRef.current = showIonosphere;
        const [showSatellites, setShowSatellites] = useState(false);
        const [satelliteStats, setSatelliteStats] = useState({ total: 0, visible: 0, byConstellation: {} });
        const [satelliteFilters, setSatelliteFilters] = useState({ gps: true, galileo: true, beidou: true, glonass: true, qzss: true, showIpp: true });
        const [selectedSatStation, setSelectedSatStation] = useState(null);
        const globeEl = useRef(null);
        const mapboxEl = useRef(null);
        const [isGlobeMode, setIsGlobeMode] = useState(true);
        const [mapEngine, setMapEngine] = useState('mapbox');
        const [mapReady, setMapReady] = useState(false);
        const fallbackViewRef = useRef(null);
        const [useOrbitView, setUseOrbitView] = useState(false);
        const [mapboxSpinning, setMapboxSpinning] = useState(false);
        function enterGlobeMode() {
            if (mapEngine !== 'mapbox') return;
            window.MapboxGlobeView?.setProjection('globe');
            setIsGlobeMode(true);
        }
        function showSurfaceMap(surface) {
            if (useOrbitView) {
                setUseOrbitView(false);
                if (surface && mapEngine === 'mapbox') window.MapboxGlobeView?.flyTo(surface.lat, surface.lng, surface.zoom);
                else if (surface && mapRef.current) mapRef.current.setView([surface.lat, surface.lng], Math.max(2, Math.min(20, surface.zoom)));
                return;
            }
            if (mapEngine === 'mapbox') window.MapboxGlobeView?.setProjection('mercator');
            setIsGlobeMode(false);
        }
        const [globeTexture, setGlobeTexture] = useState('blue-marble');
        const [provincesLoaded, setProvincesLoaded] = useState(0);
        const [provincesList, setProvincesList] = useState([]);
        const [selectedProvince, setSelectedProvince] = useState('');
        const [roverClients, setRoverClients] = useState([]);
        const [statusFilters, setStatusFilters] = useState({ online: true, sleep: true, offline: true, locked: true });
        const [search, setSearch] = useState('');
        const [locationSearch, setLocationSearch] = useState('');
        const [isSearchingLoc, setIsSearchingLoc] = useState(false);
        const [searchOpen, setSearchOpen] = useState(false);
        const [filterOpen, setFilterOpen] = useState(false);
        const [measureOn, setMeasureOn] = useState(false);
        const [findingNearest, setFindingNearest] = useState(false);
        const [tileMode, setTileMode] = useState('hybrid');
        const [mapError, setMapError] = useState('');
        const role = String(currentUser?.role || '').toLowerCase();
        const canViewRovers = role === 'admin' || currentUser?.can_view_rovers === true || currentUser?.permissions?.includes('view:rovers');

        const allowedMountpoints = useMemo(() => {
            if (String(currentUser?.role || '').toLowerCase() === 'admin') return null;
            const mps = new Set();
            (devices || []).forEach(device => {
                const cfg = device.service_config || {};
                if (cfg.mountpoint1) mps.add(String(cfg.mountpoint1).trim());
                if (cfg.mountpoint2) mps.add(String(cfg.mountpoint2).trim());
            });
            return mps;
        }, [devices, currentUser?.role]);

        const visibleRovers = useMemo(() => {
            if (!showRovers || !canViewRovers) return [];
            const allowedRoversList = Array.isArray(currentUser?.allowed_rovers) ? currentUser.allowed_rovers : [];
            return roverClients.filter(rover => {
                const hasCoords = rover.latitude !== null && rover.latitude !== undefined && rover.longitude !== null && rover.longitude !== undefined
                    && String(rover.latitude).trim() !== '' && String(rover.longitude).trim() !== '';
                const lat = Number(rover.latitude);
                const lon = Number(rover.longitude);
                const mp = String(rover.mountpoint || '').trim();
                const isAllowed = allowedMountpoints === null || allowedMountpoints.has(mp) || allowedRoversList.includes(rover.username);
                const hasValidCoords = hasCoords && Number.isFinite(lat) && Number.isFinite(lon)
                    && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
                return isAllowed && rover.show_position !== false && hasValidCoords;
            });
        }, [roverClients, showRovers, canViewRovers, allowedMountpoints, currentUser?.allowed_rovers]);

        const stations = useMemo(() => {
            const term = search.trim().toLowerCase();
            return devices
                .map(device => ({ device, coords: getCoords(device), status: effectiveStatus(device), traffic: getPreferredServer(device) }))
                .filter(item => item.coords)
                .filter(() => showStations)
                .filter(item => statusFilters[item.status === 'streaming' ? 'online' : item.status] !== false)
                .filter(item => !term || `${item.device.name || ''} ${item.device.serial || ''} ${item.traffic.mountpoint || ''}`.toLowerCase().includes(term));
        }, [devices, search, statusFilters, showStations]);

        useEffect(() => {
            let active = true;
            let pending = false;
            let controller = null;
            async function refresh() {
                if (pending || document.visibilityState === 'hidden') return;
                pending = true;
                controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 12000);
                try {
                    const response = await fetch('/api/public/reference-network-status', {cache: 'no-store', signal: controller.signal});
                    if (!response.ok) throw new Error('Reference network unavailable');
                    const health = await response.json();
                    if (active) setVngeonetHealth(health);
                } catch (_) {
                    if (active) setVngeonetHealth({status: 'unknown', reason: 'Mất cập nhật trạng thái Cục'});
                } finally { clearTimeout(timeout); pending = false; }
            }
            refresh();
            const interval = setInterval(refresh, 30000);
            document.addEventListener('visibilitychange', refresh);
            return () => { active = false; controller?.abort(); clearInterval(interval); document.removeEventListener('visibilitychange', refresh); };
        }, []);

        const referenceStations = useMemo(() => {
            const term = search.trim().toLowerCase();
            return showVngeonet ? (window.VngeonetStations || []).filter(station =>
                !term || `cors cục vngeonet ${station.code}`.toLowerCase().includes(term))
                .map(station => ({...station, serviceHealth: vngeonetHealth, serviceColor: referenceNetworkMeta(vngeonetHealth).color})) : [];
        }, [showVngeonet, search, vngeonetHealth]);
        const referenceMeta = referenceNetworkMeta(vngeonetHealth);
        const ownOnlineCount = devices.filter(device => !['offline', 'lost'].includes(
            window.CorsDashboardShared?.getMapEffectiveStatus?.(device) || String(device?.status || 'offline').toLowerCase()
        )).length;

        useEffect(() => {
            let isMounted = true;

            const fetchRoverClients = async () => {
                if (document.visibilityState === 'hidden' || window.globalState?.currentView !== 'map') return;
                if (!canViewRovers) {
                    roverHashRef.current = '';
                    if (isMounted) setRoverClients([]);
                    return;
                }

                try {
                    const response = await (window.apiFetch || fetch)('/api/caster/clienttable');
                    if (!response?.ok) return;
                    const payload = await response.json();
                    const items = Array.isArray(payload.items) ? payload.items : [];
                    const nextHash = items
                        .map(r => `${r.username}|${r.receiver_id}|${r.mountpoint}|${r.latitude}|${r.longitude}|${r.status}|${r.nsv}|${r.show_position}`)
                        .join('#');
                    if (nextHash === roverHashRef.current) return;
                    roverHashRef.current = nextHash;
                    if (isMounted) setRoverClients(items);
                } catch (error) {
                    console.warn('Failed to fetch rover clienttable:', error);
                }
            };

            fetchRoverClients();
            const timer = setInterval(fetchRoverClients, 10000);
            return () => {
                isMounted = false;
                clearInterval(timer);
            };
        }, [canViewRovers]);

        useEffect(() => {
            const L = window.CorsDashboardShared?.getLeaflet?.();
            if (mapEngine !== 'leaflet' || !mapEl.current || mapRef.current) return;
            if (!L) {
                setMapError('Không tải được thư viện bản đồ. Vui lòng kiểm tra mạng và tải lại trang.');
                console.error('Stations map initialization skipped: Leaflet is unavailable or invalid.');
                return;
            }

            const map = L.map(mapEl.current, {
                zoomControl: true,
                preferCanvas: true,
                minZoom: 2,
                maxZoom: 20,
                zoomSnap: 0.5,
                zoomDelta: 1,
                wheelPxPerZoomLevel: 35,
                wheelDebounceTime: 20
            }).setView(DEFAULT_CENTER, DEFAULT_ZOOM);
            mapRef.current = map;
            if (fallbackViewRef.current) {
                const view = fallbackViewRef.current;
                map.setView([view.lat, view.lng], Math.max(2, Math.min(20, view.zoom)), { animate: false });
                map.__fitOnce = true;
            }
            setMapReady(true);
            setMapError('');

            const tiles = {
                hybrid: L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', { maxZoom: 20, attribution: '&copy; Google' }),
                satellite: L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', { maxZoom: 20, attribution: '&copy; Google' }),
                streets: L.tileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', { maxZoom: 20, attribution: '&copy; Google' })
            };
            tiles.hybrid.addTo(map);
            map.__tiles = tiles;

            layerRef.current = L.layerGroup();
            coverageRef.current = L.layerGroup();
            coverageRef.current.addTo(map);
            layerRef.current.addTo(map);

            roverClusterRef.current = L.layerGroup();
            if (roverClusterRef.current) {
                roverClusterRef.current.addTo(map);
            }

            map.on('popupopen', (event) => {
                const el = event.popup.getElement();
                const btn = el?.querySelector('.station-map-detail-btn');
                btn?.addEventListener('click', () => {
                    const serial = btn.dataset.serial;
                    if (window.selectDashboardDevice) window.selectDashboardDevice(serial);
                    else if (window.globalState) window.globalState.selectedDeviceSerial = serial;
                    window.switchView?.('dashboard');
                    window.history.pushState({}, '', '/');
                }, { once: true });

                const satBtn = el?.querySelector('.station-map-sat-btn');
                satBtn?.addEventListener('click', () => {
                    const serial = satBtn.dataset.serial;
                    const dev = (devices || []).find(d => String(d.serial).trim() === serial);
                    if (dev) {
                        const devStatus = dev.status || '';
                        if (satBtn.dataset.offline === 'true' || devStatus === 'offline' || devStatus === 'lost') {
                            window.CorsUI?.showToast('warning', `Trạm ${dev.name || dev.serial} đang Mất kết nối (Offline). Không thể thu nhận tín hiệu vệ tinh thời gian thực.`);
                            return;
                        }
                        const coords = getCoords(dev);
                        if (coords) {
                            window.selectDashboardDevice?.(serial);
                            setShowSatellites(true);
                            window.GnssSatelliteOverlay?.setSelectedStation({
                                lat: coords.lat,
                                lon: coords.lon,
                                alt: dev.alt || 50,
                                name: dev.name || dev.serial,
                                serial: dev.serial,
                                status: devStatus,
                            });
                            window.CorsUI?.showToast('info', 'Đã bật vị trí vệ tinh. Tia và IPP chỉ hiện khi PRN khớp RTCM của trạm.');
                        }
                    }
                }, { once: true });
            });

            map.on('click', (event) => {
                const state = measureRef.current;
                if (state.enabled) {
                    const marker = L.circleMarker(event.latlng, { radius: 5, color: '#0ea5e9', fillColor: '#0ea5e9', fillOpacity: 1 }).addTo(map);
                    state.points.push(event.latlng);
                    state.layers.push(marker);
                    if (state.points.length === 2) {
                        const [a, b] = state.points;
                        const line = L.polyline([a, b], { color: '#0ea5e9', weight: 3, dashArray: '6 6' }).addTo(map);
                        const dist = map.distance(a, b);
                        const label = L.marker(b, {
                            icon: L.divIcon({
                                className: 'station-map-measure-label',
                                html: window.CorsUI.formatDistance(dist),
                                iconSize: [92, 30],
                                iconAnchor: [46, 34]
                            })
                        }).addTo(map);
                        state.layers.push(line, label);
                        state.points = [];
                    }
                    return;
                }

                if (showIonosphereRef.current && window.IonosphereOverlay?.sample) {
                    const probe = window.IonosphereOverlay.sample(event.latlng.lat, event.latlng.lng);
                    if (probe && probe.tec !== undefined) {
                        const statusBadge = probe.is_stale
                            ? '<span class="badge bg-warning text-dark">STALE</span>'
                            : '<span class="badge bg-success">REALTIME</span>';
                        const anomalyColor = probe.anomaly >= 0 ? '#f87171' : '#60a5fa';
                        const sign = probe.anomaly >= 0 ? '+' : '';
                        const content = `
                            <div class="station-map-popup ionosphere-probe-popup">
                                <div class="d-flex justify-content-between align-items-center mb-1 gap-2">
                                    <div class="fw-bold" style="color:#38bdf8;font-size:0.85rem;">
                                        <i class="bi bi-broadcast-pin me-1"></i>Tầng điện ly GloTEC
                                    </div>
                                    ${statusBadge}
                                </div>
                                <div class="text-center my-1">
                                    <span style="font-size:1.6rem;font-weight:900;color:#f8fafc;letter-spacing:-0.02em;">${probe.tec.toFixed(1)}</span>
                                    <span class="ms-1 small" style="color:#94a3b8;font-weight:700;">TECU</span>
                                </div>
                                <div class="station-map-popup-grid" style="font-size:0.75rem;">
                                    <span class="station-map-popup-label">Tọa độ</span>
                                    <span class="station-map-popup-value">${probe.lat.toFixed(2)}°, ${probe.lon.toFixed(2)}°</span>
                                    <span class="station-map-popup-label">Dị thường</span>
                                    <span class="station-map-popup-value" style="color:${anomalyColor};font-weight:700;">${sign}${probe.anomaly.toFixed(2)} TECU</span>
                                    <span class="station-map-popup-label">Thời gian</span>
                                    <span class="station-map-popup-value">${probe.observation_time_vn || '--'}</span>
                                    <span class="station-map-popup-label">Nguồn</span>
                                    <span class="station-map-popup-value"><a href="https://www.swpc.noaa.gov/products/global-total-electron-content-glotec" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;text-decoration:none;">NOAA SWPC GloTEC <i class="bi bi-box-arrow-up-right" style="font-size:0.6rem;"></i></a></span>
                                </div>
                            </div>
                        `;
                        L.popup({ maxWidth: 280, className: 'ionosphere-leaflet-popup' })
                            .setLatLng(event.latlng)
                            .setContent(content)
                            .openOn(map);
                    }
                }
            });

            setTimeout(() => map.invalidateSize(), 80);
            return () => {
                window.IonosphereOverlay?.detachFromMap();
                window.GnssSatelliteOverlay?.detachFromMap();
                Object.values(roverMarkersRef.current).forEach(marker => {
                    if (marker?.__animFrame) cancelAnimationFrame(marker.__animFrame);
                });
                roverMarkersRef.current = {};
                roverClusterRef.current = null;
                map.remove();
                mapRef.current = null;
            };
        }, [mapEngine]);

        useEffect(() => {
            const manager = window.IonosphereOverlay;
            if (!manager) return;
            const unsub = manager.subscribe((data, mode, opacity) => {
                if (data) {
                    setIonosphereInfo({
                        status: data.status,
                        is_stale: data.is_stale,
                        time_tag: data.time_tag,
                        observation_time_vn: data.observation_time_vn,
                        stats: data.stats || {},
                        mode: mode || 'tec',
                        opacity: opacity ?? 0.65,
                    });
                }
            });
            return unsub;
        }, []);

        useEffect(() => {
            const manager = window.GnssSatelliteOverlay;
            if (!manager) return;
            const unsub = manager.subscribe(({ stats, filters, selectedStation }) => {
                setSatelliteStats(stats);
                setSatelliteFilters(filters);
                setSelectedSatStation(selectedStation);
            });
            return unsub;
        }, []);

        useEffect(() => {
            const map = mapRef.current;
            const manager = window.IonosphereOverlay;
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (mapEngine !== 'leaflet' || !mapReady || !map || !manager || !L) return;

            const southWest = L.latLng(5.0, 100.5);
            const northEast = L.latLng(24.5, 120.0);
            const vietnamBounds = L.latLngBounds(southWest, northEast);

            if (showIonosphere && !useOrbitView) {
                map.setMaxBounds(null);
                map.setMinZoom(2);
                manager.attachToMap(map, { mode: ionosphereMode, opacity: ionosphereOpacity });
            } else {
                manager.detachFromMap();
                if (!useOrbitView && showVietnamMask && !showSatellites) {
                    map.setMaxBounds(vietnamBounds);
                    map.setMinZoom(4);
                }
            }
        }, [showIonosphere, ionosphereMode, ionosphereOpacity, showVietnamMask, showSatellites, useOrbitView, mapEngine, mapReady]);

        useEffect(() => {
            const map = mapRef.current;
            const manager = window.GnssSatelliteOverlay;
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (mapEngine !== 'leaflet' || !mapReady || !map || !manager || !L) return;

            const southWest = L.latLng(5.0, 100.5);
            const northEast = L.latLng(24.5, 120.0);
            const vietnamBounds = L.latLngBounds(southWest, northEast);

            if (showSatellites && !useOrbitView) {
                map.setMaxBounds(null);
                map.setMinZoom(2);
                manager.attachToMap(map);
            } else {
                manager.detachFromMap();
                if (!useOrbitView && showVietnamMask && !showIonosphere) {
                    map.setMaxBounds(vietnamBounds);
                    map.setMinZoom(4);
                }
            }
        }, [showSatellites, showVietnamMask, showIonosphere, useOrbitView, mapEngine, mapReady]);

        useEffect(() => {
            if (mapEngine !== 'mapbox' || !mapboxEl.current) return;
            if (!window.MapboxGlobeView) { setMapEngine('leaflet'); setIsGlobeMode(false); return; }
            let active = true;
            window.MapboxGlobeView?.attach(mapboxEl.current, {
                stations, selectedStation: selectedSatStation,
                center: { lat: DEFAULT_CENTER[0], lng: DEFAULT_CENTER[1] }, zoom: DEFAULT_ZOOM,
                onReady: () => { if (active) setMapReady(true); },
                onFailure: ({ view }) => {
                    if (!active) return;
                    fallbackViewRef.current = view;
                    setMapReady(false);
                    setMapEngine('leaflet');
                    setUseOrbitView(false);
                    setIsGlobeMode(false);
                    window.CorsUI?.showToast('warning', 'Mapbox không khả dụng. Đã chuyển sang bản đồ Google dự phòng.');
                },
                onSpinChange: setMapboxSpinning,
                stationPopup: item => popupHtml(item.device, item.status, item.traffic),
                referenceStationPopup,
                provincePopup: provincePopupHtml,
                onOpenDetail: serial => {
                    window.selectDashboardDevice?.(serial);
                    window.switchView?.('dashboard');
                    window.history.pushState({}, '', '/');
                },
                onTrackStation: station => {
                    if (['offline', 'lost'].includes(station.status)) return;
                    if (window.selectDashboardDevice) window.selectDashboardDevice(station.serial);
                    else if (window.globalState) window.globalState.selectedDeviceSerial = station.serial;
                    setShowSatellites(true);
                    window.GnssSatelliteOverlay?.setSelectedStation(station);
                },
            });
            return () => { active = false; window.MapboxGlobeView?.detach(); };
        }, [mapEngine]);

        useEffect(() => {
            if (mapEngine === 'mapbox') {
                window.MapboxGlobeView?.setActive(true);
                return;
            }
            window.MapboxGlobeView?.setActive(!useOrbitView);
            if (!useOrbitView || !globeEl.current) return;
            const view = null;
            const renderer = window.Globe3DView;
            renderer?.attach(globeEl.current, {
                stations, selectedStation: selectedSatStation,
                center: mapRef.current?.getCenter(),
                zoom: mapRef.current?.getZoom(), onSurfaceView: showSurfaceMap,
            });
            if (renderer) {
                Object.entries(satelliteFilters).forEach(([key, value]) => renderer.setFilter(key, value));
                renderer.setEarthTexture(globeTexture);
            }
            return () => renderer?.detach();
        }, [useOrbitView, mapEngine]);

        useEffect(() => {
            if (mapEngine !== 'mapbox' && useOrbitView) window.Globe3DView?.updateStations(stations, selectedSatStation);
            if (mapEngine === 'mapbox') window.MapboxGlobeView?.updateStations(stations, selectedSatStation);
        }, [stations, selectedSatStation, useOrbitView, mapEngine]);

        useEffect(() => {
            if (mapEngine !== 'mapbox' || !mapReady) return;
            window.MapboxGlobeView?.updateLayers({
                rovers: visibleRovers, referenceStations, coverage, provinces: provincesDataRef.current,
                showProvinces, showVietnamMask, showIonosphere, ionosphereMode, ionosphereOpacity, tileMode,
            });
        }, [mapEngine, mapReady, visibleRovers, referenceStations, coverage, provincesLoaded, showProvinces, showVietnamMask, showIonosphere, ionosphereMode, ionosphereOpacity, tileMode]);

        useEffect(() => {
            if (mapEngine !== 'mapbox' || !mapReady || (mapEngine !== 'mapbox' && useOrbitView)) return;
            const manager = window.IonosphereOverlay;
            if (!showIonosphere || !manager) return;
            const render = () => window.MapboxGlobeView?.updateIonosphere();
            const unsubscribe = manager.subscribe(render);
            manager.setMode(ionosphereMode); manager.setOpacity(ionosphereOpacity);
            manager.fetchData(); manager.startAutoRefresh(); render();
            return () => { unsubscribe(); manager.stopAutoRefresh(); };
        }, [mapEngine, mapReady, useOrbitView, showIonosphere, ionosphereMode, ionosphereOpacity]);

        useEffect(() => {
            if (mapEngine !== 'mapbox' || !mapReady || (!showSatellites && !useOrbitView)) return;
            const manager = window.GnssSatelliteOverlay;
            manager?.attachToRenderer(frame => window.MapboxGlobeView?.updateGnss(frame));
            return () => manager?.detachFromMap();
        }, [mapEngine, mapReady, useOrbitView, showSatellites]);

        useEffect(() => {
            let active = true;
            Promise.all([
                fetch('/api/public/vietnam-provinces-geojson').then(r => r.ok ? r.json() : null),
                fetch('/api/public/vietnam-provinces').then(r => r.ok ? r.json() : [])
            ]).then(([geojson, summary]) => {
                if (!active) return;
                if (Array.isArray(summary)) setProvincesList(summary);
                if (geojson && geojson.type === 'FeatureCollection') {
                    provincesDataRef.current = geojson;
                    setProvincesLoaded(c => c + 1);
                }
            }).catch(err => {
                console.warn('[StationsMap] Could not load province dataset:', err);
            });
            return () => { active = false; };
        }, []);

        useEffect(() => {
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            const geojsonData = provincesDataRef.current;
            if (mapEngine !== 'leaflet' || !mapReady || !map || !L) return;

            // Clear previous mask and sovereignty markers
            if (vietnamMaskLayerRef.current && map.hasLayer(vietnamMaskLayerRef.current)) {
                map.removeLayer(vietnamMaskLayerRef.current);
                vietnamMaskLayerRef.current = null;
            }
            (sovereigntyMarkersRef.current || []).forEach(m => {
                if (map.hasLayer(m)) map.removeLayer(m);
            });
            sovereigntyMarkersRef.current = [];

            // Sovereign island markers for Hoang Sa & Truong Sa
            const hsMarker = L.marker([16.530, 112.020], {
                icon: L.divIcon({
                    className: 'station-map-sovereignty-badge',
                    html: `
                        <div class="sovereignty-tag">
                            <span class="flag">🇻🇳</span>
                            <span class="title">Quần đảo Hoàng Sa</span>
                            <span class="sub">Việt Nam</span>
                        </div>
                    `,
                    iconSize: [160, 44],
                    iconAnchor: [80, 22]
                }),
                interactive: true
            }).bindPopup('<b>🇻🇳 Quần đảo Hoàng Sa</b><br>Việt Nam');

            const tsMarker = L.marker([9.880, 114.250], {
                icon: L.divIcon({
                    className: 'station-map-sovereignty-badge',
                    html: `
                        <div class="sovereignty-tag">
                            <span class="flag">🇻🇳</span>
                            <span class="title">Quần đảo Trường Sa</span>
                            <span class="sub">Việt Nam</span>
                        </div>
                    `,
                    iconSize: [160, 44],
                    iconAnchor: [80, 22]
                }),
                interactive: true
            }).bindPopup('<b>🇻🇳 Quần đảo Trường Sa</b><br>Việt Nam');

            const bdMarker = L.marker([13.200, 112.000], {
                icon: L.divIcon({
                    className: 'station-map-sovereignty-badge',
                    html: `
                        <div class="sovereignty-tag maritime-tag">
                            <span class="flag">🇻🇳</span>
                            <span class="title">Biển Đông</span>
                        </div>
                    `,
                    iconSize: [160, 44],
                    iconAnchor: [80, 22]
                }),
                interactive: true
            }).bindPopup('<b>Biển Đông</b>');

            hsMarker.addTo(map);
            tsMarker.addTo(map);
            bdMarker.addTo(map);
            sovereigntyMarkersRef.current = [hsMarker, tsMarker, bdMarker];

            if (!showVietnamMask) {
                map.setMaxBounds(null);
                map.setMinZoom(2);
                return;
            }

            const southWest = L.latLng(5.0, 100.5);
            const northEast = L.latLng(24.5, 120.0);
            const vietnamBounds = L.latLngBounds(southWest, northEast);
            map.setMaxBounds(vietnamBounds);
            map.setMinZoom(4);

            if (!geojsonData?.features?.length) return;

            // World inverted mask: hides everything outside Vietnam sovereignty borders
            const worldRing = [
                [-180.0, -85.05112878],
                [180.0, -85.05112878],
                [180.0, 85.05112878],
                [-180.0, 85.05112878],
                [-180.0, -85.05112878]
            ];
            const holes = [];
            geojsonData.features.forEach(feat => {
                const geom = feat.geometry || {};
                if (geom.type === 'Polygon' && Array.isArray(geom.coordinates) && geom.coordinates[0]) {
                    holes.push(geom.coordinates[0]);
                } else if (geom.type === 'MultiPolygon' && Array.isArray(geom.coordinates)) {
                    geom.coordinates.forEach(poly => {
                        if (poly?.[0]) holes.push(poly[0]);
                    });
                }
            });

            if (!holes.length) return;

            const maskGeoJson = {
                type: 'Feature',
                properties: { name: 'Vietnam Territory Cutout' },
                geometry: {
                    type: 'Polygon',
                    coordinates: [worldRing, ...holes]
                }
            };

            const maskLayer = L.geoJSON(maskGeoJson, {
                renderer: L.svg ? L.svg({ padding: 0.5 }) : undefined,
                noClip: true,
                style: {
                    fillColor: '#070b14',
                    fillOpacity: 1.0,
                    stroke: true,
                    color: '#0284c7',
                    weight: 1.8,
                    interactive: false
                }
            });
            maskLayer.addTo(map);
            if (maskLayer.bringToBack) maskLayer.bringToBack();
            vietnamMaskLayerRef.current = maskLayer;
        }, [showVietnamMask, provincesLoaded, mapEngine, mapReady]);

        useEffect(() => {
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            const geojsonData = provincesDataRef.current;
            if (mapEngine !== 'leaflet' || !mapReady || !map || !L) return;

            if (!showProvinces) {
                if (provincesLayerRef.current && map.hasLayer(provincesLayerRef.current)) {
                    map.removeLayer(provincesLayerRef.current);
                }
                return;
            }

            if (!geojsonData) return;

            if (provincesLayerRef.current && map.hasLayer(provincesLayerRef.current)) {
                map.removeLayer(provincesLayerRef.current);
            }

            const defaultStyle = {
                color: '#0284c7',
                weight: 2.5,
                dashArray: '',
                opacity: 0.9,
                fillColor: '#38bdf8',
                fillOpacity: 0.04
            };

            const layer = L.geoJSON(geojsonData, {
                style: () => defaultStyle,
                onEachFeature: (feature, featLayer) => {
                    const p = feature.properties || {};
                    featLayer.on({
                        mouseover: (e) => {
                            const target = e.target;
                            target.setStyle({
                                weight: 2.5,
                                color: '#0369a1',
                                dashArray: '',
                                fillColor: '#0ea5e9',
                                fillOpacity: 0.16
                            });
                        },
                        mouseout: (e) => {
                            layer.resetStyle(e.target);
                        },
                        click: (e) => {
                            L.DomEvent.stopPropagation(e);
                            const bounds = featLayer.getBounds();
                            map.fitBounds(bounds, { padding: [40, 40], maxZoom: 11 });

                            L.popup()
                                .setLatLng(e.latlng)
                                .setContent(provincePopupHtml(feature, stations))
                                .openOn(map);
                        }
                    });
                }
            });

            layer.addTo(map);
            if (layer.bringToBack) layer.bringToBack();
            provincesLayerRef.current = layer;

            return () => {
                if (map && layer && map.hasLayer(layer)) {
                    map.removeLayer(layer);
                }
            };
        }, [showProvinces, stations, provincesList, mapEngine, mapReady]);

        useEffect(() => {
            const map = mapRef.current;
            if (!map?.__tiles) return;
            Object.values(map.__tiles).forEach(layer => map.removeLayer(layer));
            map.__tiles[tileMode].addTo(map);
        }, [tileMode, mapEngine, mapReady]);

        useEffect(() => {
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            if (mapEngine !== 'leaflet' || !mapReady || !map || !L) return;
            const layer = L.layerGroup().addTo(map);
            referenceStations.forEach(station => {
                L.circleMarker([station.lat, station.lon], {
                    radius: 6, color: station.serviceColor, weight: 2,
                    fillColor: '#a78bfa', fillOpacity: 0.95,
                }).bindTooltip(`CORS Cục · VNGEONET · ${station.code}`, { direction: 'top' })
                    .bindPopup(referenceStationPopup(station), { maxWidth: 300 }).addTo(layer);
            });
            return () => { if (map.hasLayer(layer)) map.removeLayer(layer); };
        }, [referenceStations, mapEngine, mapReady]);

        useEffect(() => {
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            if (mapEngine !== 'leaflet' || !mapReady || !map || !L || !layerRef.current || !coverageRef.current) return;
            layerRef.current.clearLayers();
            coverageRef.current.clearLayers();

            const bounds = [];
            stations.forEach(({ device, coords, status, traffic }) => {
                const meta = window.CorsUI.getStatusMeta(status);
                const marker = L.circleMarker([coords.lat, coords.lon], {
                    radius: 8,
                    color: '#ffffff',
                    weight: 2,
                    fillColor: meta.color,
                    fillOpacity: 0.95,
                    opacity: 0.95
                });
                marker.bindPopup(popupHtml(device, status, traffic), { closeButton: true, maxWidth: 300 });
                layerRef.current.addLayer(marker);
                if (marker.bringToFront) marker.bringToFront();

                if (coverage) {
                    const coverageCircle = L.circle([coords.lat, coords.lon], {
                        radius: COVERAGE_RADIUS_M,
                        color: meta.color,
                        weight: 3,
                        opacity: 0.72,
                        fillColor: meta.color,
                        fillOpacity: 0.18
                    });
                    coverageCircle.bindPopup(popupHtml(device, status, traffic), { closeButton: true, maxWidth: 300 });
                    coverageRef.current.addLayer(coverageCircle);
                }
                bounds.push([coords.lat, coords.lon]);
            });

            if (bounds.length && !map.__fitOnce) {
                map.fitBounds(bounds, { padding: [28, 28] });
                map.__fitOnce = true;
            }
        }, [stations, coverage, mapEngine, mapReady]);

        useEffect(() => {
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            if (mapEngine !== 'leaflet' || !mapReady || !map || !L) return;

            const roverMarkers = roverMarkersRef.current;
            const roverCluster = roverClusterRef.current;
            const validKeys = new Set();
            const utils = window.StationsMapUtils || {};

            const addMarker = (marker) => {
                if (roverCluster) {
                    if (!roverCluster.hasLayer(marker)) roverCluster.addLayer(marker);
                } else if (!map.hasLayer(marker)) {
                    marker.addTo(map);
                }
            };
            const removeMarker = (marker) => {
                if (roverCluster) {
                    if (roverCluster.hasLayer(marker)) roverCluster.removeLayer(marker);
                } else if (map.hasLayer(marker)) {
                    map.removeLayer(marker);
                }
            };

            visibleRovers.forEach(rover => {
                const lat = Number(rover.latitude);
                const lon = Number(rover.longitude);
                const key = `${rover.username || 'unknown'}_${rover.receiver_id || '0'}`;
                validKeys.add(key);
                const statusMeta = utils.getRoverStatusMeta
                    ? utils.getRoverStatusMeta(rover.status)
                    : { label: rover.status || 'Unknown', color: '#38bdf8' };
                const html = `
                    <div class="station-map-popup">
                        <div class="station-map-popup-title">Rover: ${escapeHtml(rover.username || 'N/A')}</div>
                        <div class="station-map-popup-grid">
                            <span class="station-map-popup-label">Receiver</span><span class="station-map-popup-value">${escapeHtml(rover.receiver_id || '--')}</span>
                            <span class="station-map-popup-label">Mountpoint</span><span class="station-map-popup-value">${escapeHtml(rover.mountpoint || '--')}</span>
                            <span class="station-map-popup-label">Status</span><span class="station-map-popup-value">${escapeHtml(statusMeta.label)}</span>
                            <span class="station-map-popup-label">nSV</span><span class="station-map-popup-value">${escapeHtml(rover.nsv ?? '--')}</span>
                        </div>
                    </div>
                `;

                if (roverMarkers[key]) {
                    const marker = roverMarkers[key];
                    const current = marker.getLatLng();
                    const distance = window.StationsMapUtils?.getDistanceMeters
                        ? window.StationsMapUtils.getDistanceMeters(current.lat, current.lng, lat, lon)
                        : map.distance(current, L.latLng(lat, lon));
                    if (distance > 1.5) {
                        if (distance <= 100 && utils.animateMarkerLatLng) utils.animateMarkerLatLng(marker, lat, lon, 550);
                        else marker.setLatLng([lat, lon]);
                    }
                    if (marker.__statusColor !== statusMeta.color && utils.createRoverIcon) {
                        marker.setIcon(utils.createRoverIcon(statusMeta.color));
                        marker.__statusColor = statusMeta.color;
                    }
                    marker.setPopupContent(html);
                    addMarker(marker);
                } else {
                    const marker = utils.createRoverMarker
                        ? utils.createRoverMarker(lat, lon, statusMeta.color)
                        : L.circleMarker([lat, lon], { radius: 6, color: '#ffffff', fillColor: statusMeta.color, fillOpacity: 0.95 });
                    marker.bindPopup(html, { closeButton: true, maxWidth: 300 });
                    marker.__statusColor = statusMeta.color;
                    roverMarkers[key] = marker;
                    addMarker(marker);
                }
            });

            Object.keys(roverMarkers).forEach(key => {
                if (!validKeys.has(key)) {
                    if (roverMarkers[key].__animFrame) cancelAnimationFrame(roverMarkers[key].__animFrame);
                    removeMarker(roverMarkers[key]);
                    delete roverMarkers[key];
                }
            });
        }, [visibleRovers, mapEngine, mapReady]);

        useEffect(() => {
            if (mapEngine === 'mapbox') { window.MapboxGlobeView?.setMeasureEnabled(measureOn); return; }
            measureRef.current.enabled = measureOn;
            if (!measureOn && mapRef.current) {
                measureRef.current.layers.forEach(layer => mapRef.current.removeLayer(layer));
                measureRef.current = { enabled: false, points: [], layers: [] };
            }
        }, [measureOn, mapEngine, mapReady]);

        const locate = () => {
            if (mapEngine === 'mapbox') { window.MapboxGlobeView?.locate(); return; }
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            if (!map || !L || !navigator.geolocation) return;
            navigator.geolocation.getCurrentPosition((pos) => {
                const latlng = [pos.coords.latitude, pos.coords.longitude];
                map.setView(latlng, 13);
                L.circleMarker(latlng, { radius: 7, color: '#0ea5e9', fillColor: '#0ea5e9', fillOpacity: 1 }).addTo(map).bindPopup('Your location').openPopup();
            }, () => window.CorsUI.showToast('warning', 'Cannot locate current position'), { enableHighAccuracy: true, timeout: 8000 });
        };

        const nearestSearchOptions = () => ({
            map: mapRef.current,
            devices,
            nearestRef,
            getCoords,
            effectiveStatus,
            getPreferredServer
        });

        const findNearestFromCoords = (lat, lon, label = 'Vị trí của bạn') => {
            if (mapEngine === 'mapbox') { window.MapboxGlobeView?.findNearest(lat, lon, label); return; }
            window.StationsMapSearch?.findNearestFromCoords({
                ...nearestSearchOptions(),
                lat,
                lon,
                label
            });
        };

        const findNearestFromBrowser = () => {
            if (mapEngine === 'mapbox') {
                setFindingNearest(true);
                if (!navigator.geolocation) { setFindingNearest(false); return; }
                navigator.geolocation.getCurrentPosition(pos => {
                    findNearestFromCoords(pos.coords.latitude, pos.coords.longitude); setFindingNearest(false);
                }, () => { setFindingNearest(false); window.CorsUI?.showToast('warning', 'Không xác định được vị trí.'); }, { timeout: 8000 });
                return;
            }
            setFindingNearest(true);
            if (!window.StationsMapSearch) {
                setFindingNearest(false);
                window.CorsUI.showToast('danger', 'Map search module is not loaded.');
                return;
            }
            window.StationsMapSearch.findNearestFromBrowser(nearestSearchOptions(), () => setFindingNearest(false));
        };
        const openLocationInGoogleMaps = () => {
            const q = locationSearch.trim();
            if (!q) return;
            window.StationsMapSearch?.openGoogleMapsSearch(q);
        };
        const searchLocation = async (event) => {
            event.preventDefault();
            const q = locationSearch.trim();
            if (!q) return;
            setIsSearchingLoc(true);
            try {
                const result = await window.StationsMapSearch.geocodeVietnam(q);
                findNearestFromCoords(result.lat, result.lon, result.label);
            } catch (error) {
                window.CorsUI.showToast('danger', error.message || 'Search failed');
            } finally {
                setIsSearchingLoc(false);
            }
        };

        const selectNearest = () => {
            const withTraffic = stations
                .filter(item => Number(item.traffic.bps || 0) > 0)
                .sort((a, b) => Number(b.traffic.bps || 0) - Number(a.traffic.bps || 0))[0] || stations[0];
            if (!withTraffic) return;
            if (mapEngine === 'mapbox') window.MapboxGlobeView?.flyTo(withTraffic.coords.lat, withTraffic.coords.lon, 11);
            else mapRef.current?.setView([withTraffic.coords.lat, withTraffic.coords.lon], 11);
        };

        return (
            <div className={`station-map-container station-map-engine-${mapEngine}`}>
                <div ref={mapboxEl} className={`station-map-globe-canvas${mapEngine === 'mapbox' ? ' is-visible' : ''}`} inert={mapEngine !== 'mapbox' ? '' : undefined}></div>
                <div ref={globeEl} className={`station-map-globe-canvas${mapEngine !== 'mapbox' && useOrbitView ? ' is-visible' : ''}`} inert={mapEngine === 'mapbox' || !useOrbitView ? '' : undefined}></div>
                <div className={`station-map-flat-layer${mapEngine !== 'leaflet' || useOrbitView ? ' is-hidden' : ''}`} inert={mapEngine !== 'leaflet' || useOrbitView ? '' : undefined}>
                    <div ref={mapEl} className="station-map-canvas"></div>
                </div>

                {mapError && (
                    <div className="station-map-load-error" role="alert">
                        <i className="bi bi-map me-2"></i>
                        <span>{mapError}</span>
                        <button type="button" className="btn btn-sm btn-outline-light" onClick={() => window.location.reload()}>
                            Tải lại
                        </button>
                    </div>
                )}

                <div className="station-map-toolbar">
                    <div className="station-map-toolgroup">
                        <button
                            className={`station-map-action ${isGlobeMode ? 'active text-info' : ''}`}
                            type="button"
                            title={useOrbitView ? "Trở về bản đồ" : mapEngine === 'leaflet' ? "Đang dùng bản đồ Google dự phòng" : isGlobeMode ? "Trở về bản đồ 2D" : "Mở địa cầu 3D"}
                            aria-label={useOrbitView ? "Trở về bản đồ" : isGlobeMode ? "Bản đồ 2D" : "Địa cầu 3D"}
                            data-action="toggle-globe"
                            disabled={(mapEngine === 'leaflet' || !mapReady) && !useOrbitView}
                            onClick={() => useOrbitView || isGlobeMode ? showSurfaceMap() : enterGlobeMode()}
                        >
                            <i className={`bi ${isGlobeMode || useOrbitView ? 'bi-map' : 'bi-globe-asia-australia'}`}></i>
                        </button>
                        {!useOrbitView && (
                            <React.Fragment>
                                <button className={`station-map-action ${filterOpen ? 'active' : ''}`} type="button" title="Bộ lọc bản đồ" aria-label="Bộ lọc bản đồ" aria-expanded={filterOpen} aria-controls="station-map-filters" onClick={() => { setFilterOpen(!filterOpen); setSearchOpen(false); }}><i className="bi bi-funnel" aria-hidden="true"></i></button>
                                <button className="station-map-action" type="button" title="Vị trí của tôi" aria-label="Vị trí của tôi" onClick={locate}><i className="bi bi-geo-alt-fill" aria-hidden="true"></i></button>
                                <button className="station-map-action" type="button" title="Tìm trạm gần nhất" aria-label={findingNearest ? 'Đang tìm trạm gần nhất' : 'Tìm trạm gần nhất'} aria-busy={findingNearest} onClick={findNearestFromBrowser} disabled={findingNearest}>
                                    {findingNearest ? <span className="spinner-border spinner-border-sm"></span> : <i className="bi bi-crosshair"></i>}
                                </button>
                                <button className={`station-map-action ${searchOpen ? 'active' : ''}`} type="button" title="Tìm kiếm" aria-label="Tìm kiếm" aria-expanded={searchOpen} aria-controls="station-map-search" onClick={() => { setSearchOpen(!searchOpen); setFilterOpen(false); }}><i className="bi bi-search" aria-hidden="true"></i></button>
                                <button className="station-map-action" type="button" title="Đổi lớp bản đồ" aria-label="Đổi lớp bản đồ" onClick={() => setTileMode(tileMode === 'hybrid' ? 'satellite' : tileMode === 'satellite' ? 'streets' : 'hybrid')}><i className="bi bi-layers"></i></button>
                                <button className={`station-map-action ${showSatellites ? 'active' : ''}`} type="button" title="Vệ tinh GNSS" aria-label="Vệ tinh GNSS" aria-pressed={showSatellites} onClick={() => { setShowSatellites(!showSatellites); setFilterOpen(false); setSearchOpen(false); }}><i className="bi bi-radar" aria-hidden="true"></i></button>
                                <button className={`station-map-action ${measureOn ? 'active' : ''}`} type="button" title="Đo khoảng cách" aria-label="Đo khoảng cách" aria-pressed={measureOn} onClick={() => setMeasureOn(!measureOn)}><i className="bi bi-rulers" aria-hidden="true"></i></button>
                            </React.Fragment>
                        )}
                    </div>
                </div>

                {filterOpen && !useOrbitView && (
                    <div className="station-map-filter-panel" id="station-map-filters">
                        <div className="station-map-panel-title">Bộ lọc bản đồ</div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={showStations} onChange={(e) => setShowStations(e.target.checked)} id="station-toggle" />
                            <label className="form-check-label" htmlFor="station-toggle">Base của mình ({devices.length})</label>
                        </div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={showVngeonet}
                                onChange={e => setShowVngeonet(e.target.checked)} id="vngeonet-toggle"
                                disabled={!window.VngeonetStations?.length} aria-describedby="vngeonet-help" />
                            <label className="form-check-label" htmlFor="vngeonet-toggle">CORS Cục · VNGEONET ({window.VngeonetStations?.length || 0})</label>
                            <div className="form-text" id="vngeonet-help">
                                {window.VngeonetStations?.length ? `Trang dịch vụ: ${referenceMeta.label} · kiểm tra mỗi 30 giây` : 'Không tải được danh sách VNGEONET'}
                            </div>
                        </div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={coverage} onChange={e => setCoverage(e.target.checked)} id="coverage-toggle" />
                            <label className="form-check-label" htmlFor="coverage-toggle">Vùng phủ 20 km</label>
                        </div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={showRovers} onChange={(e) => setShowRovers(e.target.checked)} id="rover-toggle" disabled={!canViewRovers} />
                            <label className="form-check-label" htmlFor="rover-toggle">Rovers</label>
                        </div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={showIonosphere} onChange={(e) => setShowIonosphere(e.target.checked)} id="ionosphere-toggle" />
                            <label className="form-check-label d-flex align-items-center justify-content-between" htmlFor="ionosphere-toggle">
                                <span>Tầng điện ly TEC</span>
                                {ionosphereInfo && (
                                    <span className={`badge ms-1 ${ionosphereInfo.is_stale ? 'bg-warning text-dark' : 'bg-success'}`} style={{ fontSize: '0.65rem' }}>
                                        {ionosphereInfo.is_stale ? 'STALE' : 'LIVE'}
                                    </span>
                                )}
                            </label>
                        </div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={showVietnamMask} onChange={(e) => setShowVietnamMask(e.target.checked)} id="mask-toggle" />
                            <label className="form-check-label" htmlFor="mask-toggle">Chỉ xem Việt Nam</label>
                        </div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={showProvinces} onChange={(e) => setShowProvinces(e.target.checked)} id="provinces-toggle" />
                            <label className="form-check-label" htmlFor="provinces-toggle">Ranh giới Tỉnh/Thành</label>
                        </div>
                        <div className="station-map-status-filters">{['online', 'sleep', 'offline', 'locked'].map(key => (
                            <div className="form-check" key={key}>
                                <input className="form-check-input" type="checkbox" checked={statusFilters[key]} onChange={(e) => setStatusFilters({ ...statusFilters, [key]: e.target.checked })} id={`filter-${key}`} />
                                <label className="form-check-label" htmlFor={`filter-${key}`}>{{ online: 'Trực tuyến', sleep: 'Sleep', offline: 'Mất kết nối', locked: 'Đã khóa' }[key]}</label>
                            </div>
                        ))}</div>
                    </div>
                )}

                {searchOpen && !useOrbitView && (
                    <div className="station-map-search-panel" id="station-map-search">
                        <input className="form-control form-control-sm mb-2" placeholder="Find station / serial / mountpoint" value={search} onChange={(e) => setSearch(e.target.value)} />
                        <form className="d-flex gap-2 mb-2" onSubmit={searchLocation}>
                            <input className="form-control form-control-sm" placeholder="Search location" value={locationSearch} onChange={(e) => setLocationSearch(e.target.value)} />
                            <button className="btn btn-sm btn-primary" disabled={isSearchingLoc} type="submit">{isSearchingLoc ? <span className="spinner-border spinner-border-sm"></span> : <i className="bi bi-arrow-return-left"></i>}</button>
                        </form>
                        {provincesList.length > 0 && (
                            <div className="mb-2">
                                <select
                                    className="form-select form-select-sm"
                                    value={selectedProvince}
                                    onChange={(e) => {
                                        const code = e.target.value;
                                        setSelectedProvince(code);
                                        if (!code) return;
                                        if (mapEngine === 'mapbox') { window.MapboxGlobeView?.fitProvince(code); return; }
                                        if (!provincesLayerRef.current) return;
                                        const layer = provincesLayerRef.current.getLayers().find(
                                            l => String(l.feature?.properties?.code) === String(code)
                                        );
                                        if (layer && mapRef.current) {
                                            const bounds = layer.getBounds();
                                            mapRef.current.fitBounds(bounds, { padding: [40, 40], maxZoom: 11 });
                                            layer.fire('click', { latlng: bounds.getCenter() });
                                        }
                                    }}
                                >
                                    <option value="">-- Nhảy đến Tỉnh/Thành ({provincesList.length}) --</option>
                                    {provincesList.map(p => (
                                        <option key={p.code} value={p.code}>
                                            {p.fullName || p.name}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        )}
                        <button className="btn btn-sm btn-outline-info w-100 mt-1" onClick={openLocationInGoogleMaps} disabled={!locationSearch.trim()}>
                            Mo tren Google Maps
                        </button>
                        <button className="btn btn-sm btn-outline-primary w-100 mt-2" onClick={findNearestFromBrowser} disabled={findingNearest}>
                            {findingNearest ? 'Đang tìm...' : 'Tìm trạm gần nhất từ vị trí của tôi'}
                        </button>
                        <button className="btn btn-sm btn-outline-secondary w-100 mt-2" onClick={selectNearest}>Focus active traffic</button>
                    </div>
                )}

                {!useOrbitView && (
                    <div className="station-map-legend">
                        <div className="fw-bold mb-1">Base của mình · {ownOnlineCount}/{devices.length} online</div>
                        <div className="station-map-legend-row"><span className="station-map-dot online"></span>Online</div>
                        <div className="station-map-legend-row"><span className="station-map-dot sleep"></span>Sleep</div>
                        <div className="station-map-legend-row"><span className="station-map-dot offline"></span>Offline</div>
                        <div className="station-map-legend-row"><span className="station-map-dot locked"></span>Locked</div>
                        <div className="station-map-legend-row"><span className="station-map-dot rover"></span>Rover ({visibleRovers.length})</div>
                        <div className="station-map-network" role="status" aria-atomic="true">
                            <div className="station-map-legend-row fw-bold"><span className="station-map-dot reference"></span>CORS Cục · VNGEONET</div>
                            <div style={{color: referenceMeta.color}}>Hệ thống Cục: {referenceMeta.label}</div>
                            <div className="text-muted" >Kiểm tra {vngeonetHealth?.checked_at ? new Date(vngeonetHealth.checked_at * 1000).toLocaleTimeString('vi-VN') : '…'}</div>
                            {showVngeonet && <div className="text-muted">{referenceStations.length} trạm · RTCM chưa xác định</div>}
                        </div>
                    </div>
                )}

                {showIonosphere && !useOrbitView && (
                    <div className="station-map-ionosphere-hud">
                        <div className="iono-hud-header">
                            <div className="d-flex align-items-center gap-2">
                                <i className="bi bi-broadcast-pin" style={{ color: '#38bdf8' }}></i>
                                <span className="iono-hud-title">NOAA GloTEC TEC</span>
                                <span className={`iono-hud-chip ${ionosphereInfo?.is_stale ? 'stale' : 'live'}`}>
                                    {ionosphereInfo?.is_stale ? 'STALE' : 'REALTIME'}
                                </span>
                            </div>
                            <button
                                type="button"
                                className="iono-hud-close"
                                onClick={() => setShowIonosphere(false)}
                                aria-label="Đóng lớp điện ly"
                            >
                                <i className="bi bi-x"></i>
                            </button>
                        </div>

                        <div className="iono-hud-time">
                            <i className="bi bi-clock me-1"></i>
                            <span>{ionosphereInfo?.observation_time_vn || 'Đang tải dữ liệu NOAA...'}</span>
                        </div>

                        <div className="iono-hud-mode-row">
                            <div className="btn-group btn-group-sm w-100" role="group">
                                <button
                                    type="button"
                                    className={`btn ${ionosphereMode === 'tec' ? 'btn-primary' : 'btn-outline-secondary'}`}
                                    onClick={() => {
                                        setIonosphereMode('tec');
                                        window.IonosphereOverlay?.setMode('tec');
                                    }}
                                >
                                    Ionosphere VTEC
                                </button>
                                <button
                                    type="button"
                                    className={`btn ${ionosphereMode === 'anomaly' ? 'btn-primary' : 'btn-outline-secondary'}`}
                                    onClick={() => {
                                        setIonosphereMode('anomaly');
                                        window.IonosphereOverlay?.setMode('anomaly');
                                    }}
                                >
                                    TEC Anomaly
                                </button>
                            </div>
                        </div>

                        <div className="iono-hud-gradient-wrap">
                            <div className={`iono-hud-gradient-bar ${ionosphereMode === 'anomaly' ? 'anomaly-bar' : 'tec-bar'}`}></div>
                            <div className="iono-hud-ticks">
                                {ionosphereMode === 'anomaly' ? (
                                    <React.Fragment>
                                        <span>-20</span>
                                        <span>-10</span>
                                        <span>0</span>
                                        <span>+10</span>
                                        <span>+20</span>
                                    </React.Fragment>
                                ) : (
                                    <React.Fragment>
                                        <span>0</span>
                                        <span>20</span>
                                        <span>40</span>
                                        <span>60</span>
                                        <span>80+ TECU</span>
                                    </React.Fragment>
                                )}
                            </div>
                        </div>

                        {ionosphereInfo?.stats && (
                            <div className="iono-hud-stats">
                                <span>Min: <b>{ionosphereInfo.stats.min_tec}</b></span>
                                <span>TB: <b>{ionosphereInfo.stats.avg_tec}</b></span>
                                <span>Max: <b>{ionosphereInfo.stats.max_tec}</b></span>
                            </div>
                        )}

                        <div className="iono-hud-opacity-row">
                            <span className="text-muted">Độ mờ: {Math.round(ionosphereOpacity * 100)}%</span>
                            <input
                                type="range"
                                className="form-range iono-hud-range"
                                min="0.2"
                                max="1.0"
                                step="0.05"
                                value={ionosphereOpacity}
                                onChange={(e) => {
                                    const val = parseFloat(e.target.value);
                                    setIonosphereOpacity(val);
                                    window.IonosphereOverlay?.setOpacity(val);
                                }}
                            />
                        </div>
                        <div className="iono-hud-tip">
                            <i className="bi bi-info-circle me-1"></i>Bấm vào điểm bất kỳ trên bản đồ để đo TEC.
                        </div>
                        <div className="iono-hud-source-credit mt-2 pt-2 border-top border-secondary border-opacity-25 d-flex justify-content-between align-items-center" style={{ fontSize: '0.7rem', color: '#94a3b8' }}>
                            <span>🌐 Nguồn:</span>
                            <a href="https://www.swpc.noaa.gov/products/global-total-electron-content-glotec" target="_blank" rel="noopener noreferrer" className="text-info text-decoration-none">
                                NOAA SWPC GloTEC <i className="bi bi-box-arrow-up-right" style={{ fontSize: '0.62rem' }}></i>
                            </a>
                        </div>
                    </div>
                )}

                {showSatellites && !useOrbitView && (
                    <div className="station-map-satellite-hud">
                        <div className="sat-hud-header">
                            <div className="d-flex align-items-center gap-1">
                                <i className="bi bi-radar" style={{ color: '#38bdf8' }}></i>
                                <span className="sat-hud-title">Vị trí vệ tinh GNSS</span>
                            </div>
                            <button
                                type="button"
                                className="iono-hud-close"
                                onClick={() => setShowSatellites(false)}
                                aria-label="Đóng lớp vệ tinh"
                            >
                                <i className="bi bi-x"></i>
                            </button>
                        </div>

                        {selectedSatStation ? (
                            (selectedSatStation.status === 'offline' || selectedSatStation.status === 'lost') ? (
                                <div className="sat-hud-station-banner border border-danger">
                                    <div className="d-flex justify-content-between align-items-center mb-1">
                                        <span>Trạm: <b>{selectedSatStation.name || selectedSatStation.serial}</b></span>
                                        <button
                                            type="button"
                                            className="btn btn-sm btn-outline-light sat-hud-stop-tracking"
                                            aria-label="Hủy theo dõi trạm"
                                            onClick={() => window.GnssSatelliteOverlay?.clearSelectedStation()}
                                        >
                                            Hủy theo dõi
                                        </button>
                                    </div>
                                    <div className="text-danger small fw-semibold">
                                        <i className="bi bi-exclamation-triangle-fill me-1"></i>
                                        Trạm ngoại tuyến — Ngưng phát tia & IPP
                                    </div>
                                </div>
                            ) : (
                                <div className="sat-hud-station-banner">
                                    <div className="d-flex justify-content-between align-items-center mb-1">
                                        <span>Trạm: <b>{selectedSatStation.name || selectedSatStation.serial}</b></span>
                                        <button
                                            type="button"
                                            className="btn btn-sm btn-outline-light sat-hud-stop-tracking"
                                            aria-label="Hủy theo dõi trạm"
                                            onClick={() => window.GnssSatelliteOverlay?.clearSelectedStation()}
                                        >
                                            Hủy theo dõi
                                        </button>
                                    </div>
                                    <div className="text-info small">
                                        <i className="bi bi-eye me-1"></i>
                                        <b>{satelliteStats.visible}</b> vệ tinh khớp quỹ đạo
                                        {satelliteStats.hasLiveRtcm ? ` · ${satelliteStats.liveRtcmCount} PRN RTCM` : ' · đang chờ RTCM'}
                                        {satelliteStats.unmatchedRtcmCount > 0 ? ` · ${satelliteStats.unmatchedRtcmCount} chưa ghép quỹ đạo (${(satelliteStats.unmatchedRtcmCodes || []).join(', ')}${satelliteStats.unmatchedRtcmCount > 8 ? ', …' : ''})` : ''}
                                    </div>
                                </div>
                            )
                        ) : (
                            <div className="small text-muted mb-2 fst-italic">
                                <i className="bi bi-cursor me-1"></i>Vị trí vệ tinh theo quỹ đạo TLE; chỉ vệ tinh khớp RTCM mới được tô sáng.
                            </div>
                        )}

                        <div className="sat-hud-toggles">
                            {[
                                { key: 'gps', label: 'GPS', shortLabel: 'GPS', color: '#22c55e', count: satelliteStats.byConstellation?.gps ?? 0 },
                                { key: 'galileo', label: 'Galileo', shortLabel: 'GAL', color: '#3b82f6', count: satelliteStats.byConstellation?.galileo ?? 0 },
                                { key: 'beidou', label: 'BeiDou', shortLabel: 'BDS', color: '#f97316', count: satelliteStats.byConstellation?.beidou ?? 0 },
                                { key: 'glonass', label: 'GLONASS', shortLabel: 'GLO', color: '#ef4444', count: satelliteStats.byConstellation?.glonass ?? 0 },
                                { key: 'qzss', label: 'QZSS', shortLabel: 'QZSS', color: '#a855f7', count: satelliteStats.byConstellation?.qzss ?? 0 },
                            ].map(item => (
                                <button
                                    type="button"
                                    key={item.key}
                                    className={`sat-hud-pill ${satelliteFilters[item.key] ? 'active' : ''}`}
                                    aria-pressed={satelliteFilters[item.key]}
                                    onClick={() => {
                                        const next = !satelliteFilters[item.key];
                                        window.GnssSatelliteOverlay?.setFilter(item.key, next);
                                    }}
                                >
                                    <span style={{ color: item.color }}>● <span className="sat-label-full">{item.label}</span><span className="sat-label-short d-none">{item.shortLabel}</span></span>
                                    {selectedSatStation ? (
                                        <span className="badge bg-dark ms-1" style={{ fontSize: '0.62rem' }}>
                                            {item.count}
                                        </span>
                                    ) : null}
                                </button>
                            ))}
                        </div>

                        <div className="form-check form-switch mb-1 small">
                            <input
                                className="form-check-input"
                                type="checkbox"
                                id="sat-show-ipp"
                                checked={satelliteFilters.showIpp !== false}
                                onChange={(e) => {
                                    window.GnssSatelliteOverlay?.setFilter('showIpp', e.target.checked);
                                }}
                            />
                            <label className="form-check-label" htmlFor="sat-show-ipp" style={{ fontSize: '0.72rem' }}>
                                Hiện tia RTCM và điểm IPP (350 km)
                            </label>
                        </div>

                        <div className="sat-hud-source-credit">
                            <div className="d-flex justify-content-between align-items-center mb-1">
                                <span>🛰️ Nguồn quỹ đạo:</span>
                                <a href="https://celestrak.org/NORAD/elements/" target="_blank" rel="noopener noreferrer" className="text-info text-decoration-none">
                                    CelesTrak (NORAD GP) <i className="bi bi-box-arrow-up-right" style={{ fontSize: '0.62rem' }}></i>
                                </a>
                            </div>
                            <div className="d-flex justify-content-between align-items-center mb-1 text-muted" style={{ fontSize: '0.68rem' }}>
                                <span>Định danh PRN:</span>
                                <a href="https://qzss.go.jp/en/technical/satellites/index.html" target="_blank" rel="noopener noreferrer" className="text-info text-decoration-none">
                                    Cabinet Office (QZSS) / IGS <i className="bi bi-box-arrow-up-right" style={{ fontSize: '0.6rem' }}></i>
                                </a>
                            </div>
                            {satelliteStats.hasLiveRtcm ? (
                                <div style={{ color: '#86efac', fontSize: '0.7rem' }}>
                                    ● RTCM: <b>{satelliteStats.liveRtcmCount} PRN</b> • khớp quỹ đạo: <b>{satelliteStats.visible}</b>
                                    {satelliteStats.unmatchedRtcmCount > 0 ? ` • chưa ghép quỹ đạo: ${satelliteStats.unmatchedRtcmCount} (${(satelliteStats.unmatchedRtcmCodes || []).join(', ')}${satelliteStats.unmatchedRtcmCount > 8 ? ', …' : ''})` : ''}
                                </div>
                            ) : (
                                <div className="text-muted" style={{ fontSize: '0.7rem' }}>Đang chờ RTCM • tia và IPP chỉ hiện với PRN khớp</div>
                            )}
                        </div>
                    </div>
                )}

                {(mapEngine === 'mapbox' || useOrbitView) && (
                    <div className="station-map-globe-dock">
                        <button
                            type="button"
                            className="globe-dock-btn"
                            onClick={() => {
                                const next = !useOrbitView;
                                setUseOrbitView(next);
                                if (mapEngine === 'mapbox') {
                                    if (next) {
                                        setShowSatellites(true);
                                        window.MapboxGlobeView?.focusVietnam?.(true, 90);
                                    } else {
                                        window.MapboxGlobeView?.focusVietnam?.(false);
                                    }
                                }
                            }}
                            title={useOrbitView ? 'Trở về địa cầu Mapbox' : 'Xem quỹ đạo vệ tinh GNSS'}
                        >
                            <i className={`bi ${useOrbitView ? 'bi-globe' : 'bi-radar'}`} aria-hidden="true"></i>{' '}
                            <span className="dock-btn-text-full">{useOrbitView ? 'Địa cầu' : 'Quỹ đạo GNSS'}</span>
                            <span className="dock-btn-text-short d-none">{useOrbitView ? 'Địa cầu' : 'Quỹ đạo'}</span>
                        </button>
                        {!useOrbitView && <button type="button" className="globe-dock-btn" onClick={() => { enterGlobeMode(); window.MapboxGlobeView?.focusVietnam?.(true, 90); }}><i className="bi bi-globe" aria-hidden="true"></i> Toàn cầu</button>}
                        <button
                            type="button"
                            className="globe-dock-btn"
                            onClick={() => (mapEngine !== 'mapbox' && useOrbitView ? window.Globe3DView : window.MapboxGlobeView)?.focusVietnam?.(true)}
                            title="Tập trung cận cảnh vào Việt Nam"
                        >
                            <i className="bi bi-crosshair2 text-info"></i> Tâm VN
                        </button>
                        {isGlobeMode && (
                            <button type="button" className={`globe-dock-btn ${mapboxSpinning ? 'active' : ''}`} aria-pressed={mapboxSpinning} onClick={() => window.MapboxGlobeView?.setSpinning?.(!mapboxSpinning)}>
                                <i className="bi bi-arrow-repeat" aria-hidden="true"></i>{' '}
                                <span className="dock-btn-text-full">{mapboxSpinning ? 'Tắt tự xoay' : 'Bật tự xoay'}</span>
                                <span className="dock-btn-text-short d-none">{mapboxSpinning ? 'Dừng xoay' : 'Tự xoay'}</span>
                            </button>
                        )}
                        {useOrbitView && <React.Fragment>
                        <div className="globe-dock-separator"></div>
                        {['gps', 'galileo', 'beidou', 'glonass', 'qzss'].map(k => (
                            <button
                                key={k}
                                type="button"
                                className={`globe-dock-btn ${satelliteFilters[k] ? 'active' : ''}`}
                                onClick={() => {
                                    const next = !satelliteFilters[k];
                                    setSatelliteFilters(prev => ({ ...prev, [k]: next }));
                                    window.Globe3DView?.setFilter(k, next);
                                    window.GnssSatelliteOverlay?.setFilter(k, next);
                                }}
                            >
                                <span style={{ color: k === 'gps' ? '#22c55e' : k === 'galileo' ? '#38bdf8' : k === 'beidou' ? '#f59e0b' : k === 'glonass' ? '#ef4444' : '#a855f7' }}>●</span>
                                <span className="text-uppercase">{k === 'galileo' ? 'GAL' : k === 'beidou' ? 'BDS' : k === 'glonass' ? 'GLO' : k === 'qzss' ? 'QZSS' : 'GPS'}</span>
                            </button>
                        ))}
                        <div className="globe-dock-separator"></div>
                        <button
                            type="button"
                            className="globe-dock-btn"
                            onClick={() => {
                                if (mapEngine === 'mapbox') {
                                    setTileMode(tileMode === 'satellite' ? 'streets' : 'satellite');
                                } else {
                                    const next = globeTexture === 'blue-marble' ? 'night' : globeTexture === 'night' ? 'dark' : 'blue-marble';
                                    setGlobeTexture(next);
                                    window.Globe3DView?.setEarthTexture(next);
                                }
                            }}
                            title="Đổi giao diện địa cầu (Blue Marble / City Lights / Cyber Dark)"
                        >
                            <i className="bi bi-palette"></i> {mapEngine === 'mapbox' ? (tileMode === 'satellite' ? 'Vệ tinh Mapbox' : 'Đường phố Mapbox') : (globeTexture === 'blue-marble' ? 'NASA Blue Marble' : globeTexture === 'night' ? 'City Lights' : 'Cyber Dark')}
                        </button>
                        </React.Fragment>}
                    </div>
                )}

            </div>
        );
    }

window.AllStationsMap = React.memo(AllStationsMap);
})();
