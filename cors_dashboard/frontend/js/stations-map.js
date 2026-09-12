// Command-center station map.
(() => {
    const { useEffect, useMemo, useRef, useState } = React;
    const COVERAGE_RADIUS_M = 20000;
    const PREFERRED_HOST = 'aitogy.com.vn';
    const DEFAULT_CENTER = [16.2, 106.3];
    const DEFAULT_ZOOM = 6;

    function toNumber(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    }

    function isVietnamCoord(lat, lon) {
        return lat >= 8 && lat <= 24 && lon >= 102 && lon <= 110;
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
        const ntrip = device?.ntrip_status || {};
        const servers = [1, 2].map((idx) => ({
            idx,
            host: String(cfg[`serverhost${idx}`] || '').trim(),
            mountpoint: String(cfg[`mountpoint${idx}`] || '').trim(),
            bps: Number(ntrip[`server${idx}`]?.bps || 0)
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
        return `
            <div class="station-map-popup">
                <div class="d-flex justify-content-between align-items-start gap-2">
                    <div>
                        <div class="station-map-popup-title">${device.name || device.serial || 'Station'}</div>
                        <div class="text-muted small">${device.serial || '--'}</div>
                    </div>
                    <span class="badge" style="background:${meta.color}; color:#fff;">${meta.label}</span>
                </div>
                <div class="station-map-popup-grid">
                    <span class="station-map-popup-label">Lat/Lon</span><span class="station-map-popup-value">${coords ? `${coords.lat.toFixed(6)}, ${coords.lon.toFixed(6)}` : '--'}</span>
                    <span class="station-map-popup-label">Host</span><span class="station-map-popup-value">${traffic.host || '--'}</span>
                    <span class="station-map-popup-label">Mountpoint</span><span class="station-map-popup-value">${traffic.mountpoint || '--'}</span>
                    <span class="station-map-popup-label">Traffic out</span><span class="station-map-popup-value">${window.CorsUI.formatSpeed(traffic.bps)}</span>
                </div>
                <button type="button" class="btn btn-sm btn-primary w-100 station-map-detail-btn" data-serial="${device.serial || ''}">Open detail</button>
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
        const [coverage, setCoverage] = useState(true);
        const [showStations, setShowStations] = useState(true);
        const [showRovers, setShowRovers] = useState(true);
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
                const lat = Number(rover.latitude);
                const lon = Number(rover.longitude);
                const mp = String(rover.mountpoint || '').trim();
                const isAllowed = allowedMountpoints === null || allowedMountpoints.has(mp) || allowedRoversList.includes(rover.username);
                return isAllowed && rover.show_position !== false && Number.isFinite(lat) && Number.isFinite(lon);
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
            if (!mapEl.current || mapRef.current) return;
            if (!L) {
                setMapError('Không tải được thư viện bản đồ. Vui lòng kiểm tra mạng và tải lại trang.');
                console.error('Stations map initialization skipped: Leaflet is unavailable or invalid.');
                return;
            }

            const map = L.map(mapEl.current, { zoomControl: true, preferCanvas: true }).setView(DEFAULT_CENTER, DEFAULT_ZOOM);
            mapRef.current = map;
            setMapError('');

            const googleAttribution = '&copy; Google';
            const tiles = {
                hybrid: L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', {
                    maxZoom: 20,
                    attribution: googleAttribution
                }),
                satellite: L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
                    maxZoom: 20,
                    attribution: googleAttribution
                }),
                streets: L.tileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', {
                    maxZoom: 20,
                    attribution: googleAttribution
                })
            };
            tiles.hybrid.addTo(map);
            map.__tiles = tiles;

            layerRef.current = L.layerGroup();
            coverageRef.current = L.layerGroup();
            coverageRef.current.addTo(map);
            layerRef.current.addTo(map);

            roverClusterRef.current = window.CorsDashboardShared?.createMarkerClusterGroup?.({
                    chunkedLoading: true,
                    removeOutsideVisibleBounds: true,
                    showCoverageOnHover: false,
                    spiderfyOnMaxZoom: false,
                    maxClusterRadius: 45,
                    disableClusteringAtZoom: 14
                }) || L.layerGroup();
            if (roverClusterRef.current) {
                roverClusterRef.current.addTo(map);
            }

            map.on('popupopen', (event) => {
                const btn = event.popup.getElement()?.querySelector('.station-map-detail-btn');
                btn?.addEventListener('click', () => {
                    const serial = btn.dataset.serial;
                    if (window.selectDashboardDevice) window.selectDashboardDevice(serial);
                    else if (window.globalState) window.globalState.selectedDeviceSerial = serial;
                    window.switchView?.('dashboard');
                    window.history.pushState({}, '', '/');
                }, { once: true });
            });

            map.on('click', (event) => {
                const state = measureRef.current;
                if (!state.enabled) return;
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
            });

            setTimeout(() => map.invalidateSize(), 80);
            return () => {
                Object.values(roverMarkersRef.current).forEach(marker => {
                    if (marker?.__animFrame) cancelAnimationFrame(marker.__animFrame);
                });
                roverMarkersRef.current = {};
                roverClusterRef.current = null;
                map.remove();
            };
        }, []);

        useEffect(() => {
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            if (!map || !L || !map.__tiles) return;
            Object.values(map.__tiles).forEach(layer => map.removeLayer(layer));
            map.__tiles[tileMode].addTo(map);
        }, [tileMode]);

        useEffect(() => {
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            if (!map || !L || !layerRef.current || !coverageRef.current) return;
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
        }, [stations, coverage]);

        useEffect(() => {
            const map = mapRef.current;
            const L = window.CorsDashboardShared?.getLeaflet?.();
            if (!map || !L) return;

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
                        <div class="station-map-popup-title">Rover: ${rover.username || 'N/A'}</div>
                        <div class="station-map-popup-grid">
                            <span class="station-map-popup-label">Receiver</span><span class="station-map-popup-value">${rover.receiver_id || '--'}</span>
                            <span class="station-map-popup-label">Mountpoint</span><span class="station-map-popup-value">${rover.mountpoint || '--'}</span>
                            <span class="station-map-popup-label">Status</span><span class="station-map-popup-value">${statusMeta.label}</span>
                            <span class="station-map-popup-label">nSV</span><span class="station-map-popup-value">${rover.nsv ?? '--'}</span>
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
        }, [visibleRovers]);

        useEffect(() => {
            measureRef.current.enabled = measureOn;
            if (!measureOn && mapRef.current) {
                measureRef.current.layers.forEach(layer => mapRef.current.removeLayer(layer));
                measureRef.current = { enabled: false, points: [], layers: [] };
            }
        }, [measureOn]);

        const locate = () => {
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

        const findNearestFromCoords = (lat, lon, label = 'Vi tri cua ban') => {
            window.StationsMapSearch?.findNearestFromCoords({
                ...nearestSearchOptions(),
                lat,
                lon,
                label
            });
        };

        const findNearestFromBrowser = () => {
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
            mapRef.current?.setView([withTraffic.coords.lat, withTraffic.coords.lon], 11);
        };

        return (
            <div className="station-map-container">
                <div ref={mapEl} className="station-map-canvas"></div>

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
                        <button className={`station-map-action ${filterOpen ? 'active' : ''}`} title="Filter" onClick={() => setFilterOpen(!filterOpen)}><i className="bi bi-funnel"></i></button>
                        <button className="station-map-action" title="Locate" onClick={locate}><i className="bi bi-geo-alt-fill"></i></button>
                        <button className="station-map-action" title="Tìm trạm gần nhất" onClick={findNearestFromBrowser} disabled={findingNearest}>
                            {findingNearest ? <span className="spinner-border spinner-border-sm"></span> : <i className="bi bi-crosshair"></i>}
                        </button>
                        <button className={`station-map-action ${searchOpen ? 'active' : ''}`} title="Search" onClick={() => setSearchOpen(!searchOpen)}><i className="bi bi-search"></i></button>
                        <button className="station-map-action" title="Layers" onClick={() => setTileMode(tileMode === 'hybrid' ? 'satellite' : tileMode === 'satellite' ? 'streets' : 'hybrid')}><i className="bi bi-layers"></i></button>
                        <button className={`station-map-action ${measureOn ? 'active' : ''}`} title="Measure" onClick={() => setMeasureOn(!measureOn)}><i className="bi bi-rulers"></i></button>
                    </div>
                </div>

                {filterOpen && (
                    <div className="station-map-filter-panel">
                        <div className="fw-bold mb-2">Filters</div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={showStations} onChange={(e) => setShowStations(e.target.checked)} id="station-toggle" />
                            <label className="form-check-label" htmlFor="station-toggle">CORS stations</label>
                        </div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={showRovers} onChange={(e) => setShowRovers(e.target.checked)} id="rover-toggle" disabled={!canViewRovers} />
                            <label className="form-check-label" htmlFor="rover-toggle">Rovers</label>
                        </div>
                        <div className="form-check form-switch mb-2">
                            <input className="form-check-input" type="checkbox" checked={coverage} onChange={(e) => setCoverage(e.target.checked)} id="coverage-toggle" />
                            <label className="form-check-label" htmlFor="coverage-toggle">Coverage 20km</label>
                        </div>
                        {['online', 'sleep', 'offline', 'locked'].map(key => (
                            <div className="form-check" key={key}>
                                <input className="form-check-input" type="checkbox" checked={statusFilters[key]} onChange={(e) => setStatusFilters({ ...statusFilters, [key]: e.target.checked })} id={`filter-${key}`} />
                                <label className="form-check-label text-capitalize" htmlFor={`filter-${key}`}>{key}</label>
                            </div>
                        ))}
                    </div>
                )}

                {searchOpen && (
                    <div className="station-map-search-panel">
                        <input className="form-control form-control-sm mb-2" placeholder="Find station / serial / mountpoint" value={search} onChange={(e) => setSearch(e.target.value)} />
                        <form className="d-flex gap-2" onSubmit={searchLocation}>
                            <input className="form-control form-control-sm" placeholder="Search location" value={locationSearch} onChange={(e) => setLocationSearch(e.target.value)} />
                            <button className="btn btn-sm btn-primary" disabled={isSearchingLoc} type="submit">{isSearchingLoc ? <span className="spinner-border spinner-border-sm"></span> : <i className="bi bi-arrow-return-left"></i>}</button>
                        </form>
                        <button className="btn btn-sm btn-outline-info w-100 mt-2" onClick={openLocationInGoogleMaps} disabled={!locationSearch.trim()}>
                            Mo tren Google Maps
                        </button>
                        <button className="btn btn-sm btn-outline-primary w-100 mt-2" onClick={findNearestFromBrowser} disabled={findingNearest}>
                            {findingNearest ? 'Đang tìm...' : 'Tìm trạm gần nhất từ vị trí của tôi'}
                        </button>
                        <button className="btn btn-sm btn-outline-secondary w-100 mt-2" onClick={selectNearest}>Focus active traffic</button>
                    </div>
                )}

                <div className="station-map-legend">
                    <div className="fw-bold mb-1">Legend</div>
                    <div className="station-map-legend-row"><span className="station-map-dot online"></span>Online</div>
                    <div className="station-map-legend-row"><span className="station-map-dot sleep"></span>Sleep</div>
                    <div className="station-map-legend-row"><span className="station-map-dot offline"></span>Offline</div>
                    <div className="station-map-legend-row"><span className="station-map-dot locked"></span>Locked</div>
                    <div className="station-map-legend-row"><span className="station-map-dot rover"></span>Rover ({visibleRovers.length})</div>
                </div>

            </div>
        );
    }

window.AllStationsMap = React.memo(AllStationsMap);
})();


