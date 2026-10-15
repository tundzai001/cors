// Real-time GNSS Satellite Orbit Propagation & Ionospheric Pierce Point (IPP) Overlay.
// Constellations: GPS, Galileo, BeiDou, GLONASS.
(() => {
    'use strict';

    const CONSTELLATION_META = {
        gps: { name: 'GPS (USA)', code: 'GPS', color: '#22c55e', prefix: 'G', flag: '🇺🇸', nominalAlt: '20,200 km' },
        galileo: { name: 'Galileo (EU)', code: 'GAL', color: '#3b82f6', prefix: 'E', flag: '🇪🇺', nominalAlt: '23,222 km' },
        beidou: { name: 'BeiDou (China)', code: 'BDS', color: '#f97316', prefix: 'C', flag: '🇨🇳', nominalAlt: '21,500 - 35,786 km' },
        glonass: { name: 'GLONASS (Russia)', code: 'GLO', color: '#ef4444', prefix: 'R', flag: '🇷🇺', nominalAlt: '19,130 km' },
    };

    const RE_EARTH_KM = 6371.0;
    const H_IONO_KM = 350.0;
    const TO_RAD = Math.PI / 180.0;
    const TO_DEG = 180.0 / Math.PI;

    class GnssSatelliteManager {
        constructor() {
            this.rawSatellites = [];
            this.activeSatellites = new Map(); // key -> { meta, satrec, marker, currentPos }
            this.mapInstance = null;
            this.layerGroup = null;
            this.rayLayerGroup = null;
            this.tickTimer = null;
            this.isPropagating = false;
            this.listeners = new Set();

            this.filters = {
                gps: true,
                galileo: true,
                beidou: true,
                glonass: true,
                onlyVisible: false,
            };

            this.selectedStation = null; // { lat, lon, alt, name, serial }
            this.stationRayMarkers = [];
            this.stats = { total: 0, visible: 0, byConstellation: {} };
        }

        subscribe(callback) {
            this.listeners.add(callback);
            return () => this.listeners.delete(callback);
        }

        notify() {
            this.listeners.forEach(fn => {
                try {
                    fn({
                        stats: this.stats,
                        filters: { ...this.filters },
                        selectedStation: this.selectedStation,
                        totalLoaded: this.rawSatellites.length,
                    });
                } catch (e) {
                    console.error('[GnssSatelliteManager] Notify error:', e);
                }
            });
        }

        async fetchTle() {
            try {
                const resp = await (window.apiFetch || fetch)('/api/gnss/satellites/tle');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                if (!data || !data.constellations) return;

                const satLib = window.satellite;
                if (!satLib) {
                    console.warn('[GnssSatelliteManager] satellite.js library not yet loaded.');
                    return;
                }

                const list = [];
                Object.entries(data.constellations).forEach(([constKey, sats]) => {
                    (sats || []).forEach(satItem => {
                        try {
                            const satrec = satLib.twoline2satrec(satItem.line1, satItem.line2);
                            if (satrec && !satrec.error) {
                                list.push({
                                    ...satItem,
                                    satrec,
                                    constellationKey: constKey,
                                    key: `${constKey}:${satItem.code}`,
                                });
                            }
                        } catch (err) {
                            // ignore malformed TLE line
                        }
                    });
                });

                this.rawSatellites = list;
                this.updateSatelliteMarkers();
                this.notify();
            } catch (err) {
                console.warn('[GnssSatelliteManager] Failed to fetch GNSS satellite TLEs:', err);
            }
        }

        createSatelliteIcon(code, constellationKey, isVisible) {
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            const meta = CONSTELLATION_META[constellationKey] || { color: '#94a3b8' };
            const borderColor = isVisible ? '#ffffff' : 'rgba(255, 255, 255, 0.65)';
            const glow = isVisible ? `box-shadow: 0 0 10px ${meta.color}, 0 0 2px #fff;` : '';
            const opacity = isVisible ? '1' : '0.75';

            return L.divIcon({
                className: 'gnss-sat-icon-wrap',
                html: `
                    <div class="gnss-sat-bubble" style="background:${meta.color}; border: 1.5px solid ${borderColor}; opacity:${opacity}; ${glow}">
                        <span class="gnss-sat-code">${code}</span>
                    </div>
                `,
                iconSize: [26, 26],
                iconAnchor: [13, 13],
            });
        }

        createIppIcon(tecVal) {
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            const color = tecVal !== null && tecVal !== undefined
                ? (tecVal > 55 ? '#ef4444' : tecVal > 35 ? '#f97316' : tecVal > 20 ? '#eab308' : '#38bdf8')
                : '#38bdf8';
            return L.divIcon({
                className: 'gnss-ipp-icon-wrap',
                html: `<div class="gnss-ipp-diamond" style="background:${color}; box-shadow:0 0 8px ${color};"></div>`,
                iconSize: [12, 12],
                iconAnchor: [6, 6],
            });
        }

        calculateIpp(obsLat, obsLon, elRad, azRad) {
            // Thin-shell model at 350km altitude
            const recLatRad = obsLat * TO_RAD;
            const recLonRad = obsLon * TO_RAD;

            // Earth central angle psi
            const psi = (Math.PI / 2) - elRad - Math.asin((RE_EARTH_KM / (RE_EARTH_KM + H_IONO_KM)) * Math.cos(elRad));

            // IPP Latitude
            const sinIppLat = Math.sin(recLatRad) * Math.cos(psi) + Math.cos(recLatRad) * Math.sin(psi) * Math.cos(azRad);
            const ippLatRad = Math.asin(Math.max(-1, Math.min(1, sinIppLat)));
            const ippLat = ippLatRad * TO_DEG;

            // IPP Longitude
            const sinIppLonDiff = (Math.sin(psi) * Math.sin(azRad)) / Math.cos(ippLatRad);
            const ippLonRad = recLonRad + Math.asin(Math.max(-1, Math.min(1, sinIppLonDiff)));
            let ippLon = ippLonRad * TO_DEG;
            while (ippLon < -180) ippLon += 360;
            while (ippLon > 180) ippLon -= 360;

            // Slant mapping function MF
            const mf = 1.0 / Math.sqrt(1.0 - Math.pow((RE_EARTH_KM / (RE_EARTH_KM + H_IONO_KM)) * Math.cos(elRad), 2));

            return { ippLat, ippLon, mf };
        }

        propagateTick() {
            const satLib = window.satellite;
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (!satLib || !this.mapInstance || !L) return;

            const now = new Date();
            const gmst = satLib.gstime(now);

            let obsGd = null;
            if (this.selectedStation && Number.isFinite(this.selectedStation.lat) && Number.isFinite(this.selectedStation.lon)) {
                obsGd = {
                    latitude: this.selectedStation.lat * TO_RAD,
                    longitude: this.selectedStation.lon * TO_RAD,
                    height: (this.selectedStation.alt || 50) / 1000.0,
                };
            }

            // Clear previous LOS rays and IPP markers
            if (this.rayLayerGroup) {
                this.rayLayerGroup.clearLayers();
            }

            let visibleCount = 0;
            const constCounts = { gps: 0, galileo: 0, beidou: 0, glonass: 0 };

            this.activeSatellites.forEach((satObj, key) => {
                const cfg = CONSTELLATION_META[satObj.constellationKey];
                const isConstEnabled = this.filters[satObj.constellationKey] !== false;

                try {
                    const posVel = satLib.propagate(satObj.satrec, now);
                    if (!posVel || !posVel.position) {
                        if (satObj.marker && this.layerGroup.hasLayer(satObj.marker)) {
                            this.layerGroup.removeLayer(satObj.marker);
                        }
                        return;
                    }

                    const gd = satLib.eciToGeodetic(posVel.position, gmst);
                    const lat = satLib.degreesLat(gd.latitude);
                    let lon = satLib.degreesLong(gd.longitude);
                    while (lon < -180) lon += 360;
                    while (lon > 180) lon -= 360;
                    const altKm = gd.height;

                    // Visibility & Look angles
                    let look = null;
                    let isVisible = false;
                    let azDeg = null;
                    let elDeg = null;

                    if (obsGd) {
                        const posEcf = satLib.eciToEcf(posVel.position, gmst);
                        look = satLib.ecfToLookAngles(obsGd, posEcf);
                        azDeg = ((look.azimuth * TO_DEG) + 360) % 360;
                        elDeg = look.elevation * TO_DEG;
                        isVisible = elDeg > 5.0; // Above 5° horizon
                    }

                    if (isVisible) {
                        visibleCount++;
                        constCounts[satObj.constellationKey] = (constCounts[satObj.constellationKey] || 0) + 1;
                    }

                    // Visibility filter: hide if onlyVisible is checked and satellite is not visible
                    const shouldDisplay = isConstEnabled && (!this.filters.onlyVisible || isVisible || !this.selectedStation);

                    if (!shouldDisplay) {
                        if (satObj.marker && this.layerGroup.hasLayer(satObj.marker)) {
                            this.layerGroup.removeLayer(satObj.marker);
                        }
                        return;
                    }

                    // Update or create marker
                    if (!satObj.marker) {
                        const marker = L.marker([lat, lon], {
                            icon: this.createSatelliteIcon(satObj.code, satObj.constellationKey, isVisible),
                            zIndexOffset: isVisible ? 1000 : 700,
                        });
                        satObj.marker = marker;
                        this.layerGroup.addLayer(marker);
                    } else {
                        satObj.marker.setLatLng([lat, lon]);
                        if (!this.layerGroup.hasLayer(satObj.marker)) {
                            this.layerGroup.addLayer(satObj.marker);
                        }
                    }

                    // If station is selected and satellite is visible, draw LOS Ray & IPP
                    if (obsGd && isVisible && look && this.rayLayerGroup) {
                        const { ippLat, ippLon, mf } = this.calculateIpp(this.selectedStation.lat, this.selectedStation.lon, look.elevation, look.azimuth);

                        // Sample TEC at IPP from Ionosphere overlay
                        let vtecVal = null;
                        let stecVal = null;
                        let delayM = null;
                        if (window.IonosphereOverlay?.sample) {
                            const probe = window.IonosphereOverlay.sample(ippLat, ippLon);
                            if (probe && probe.tec !== undefined) {
                                vtecVal = probe.tec;
                                stecVal = Math.round(vtecVal * mf * 10) / 10;
                                delayM = (stecVal * 0.1624).toFixed(2);
                            }
                        }

                        // Ray: Station -> IPP -> Satellite
                        const rayToIpp = L.polyline([[this.selectedStation.lat, this.selectedStation.lon], [ippLat, ippLon]], {
                            color: '#38bdf8',
                            weight: 2,
                            opacity: 0.9,
                        });
                        const rayToSat = L.polyline([[ippLat, ippLon], [lat, lon]], {
                            color: cfg.color,
                            weight: 1.5,
                            opacity: 0.45,
                            dashArray: '3 4',
                        });

                        // IPP Marker
                        const ippMarker = L.marker([ippLat, ippLon], {
                            icon: this.createIppIcon(vtecVal),
                            zIndexOffset: 950,
                        }).bindPopup(`
                            <div class="station-map-popup gnss-ipp-popup">
                                <div class="fw-bold mb-1" style="color:#38bdf8;">
                                    <i class="bi bi-bullseye me-1"></i>Điểm xuyên tầng điện ly (IPP)
                                </div>
                                <div class="small mb-1 text-muted">
                                    Vệ tinh: <b style="color:${cfg.color};">${satObj.code}</b> (${cfg.name})<br>
                                    Trạm: <b>${this.selectedStation.name || this.selectedStation.serial}</b>
                                </div>
                                <div class="station-map-popup-grid" style="font-size:0.75rem;">
                                    <span class="station-map-popup-label">Tọa độ IPP</span>
                                    <span class="station-map-popup-value">${ippLat.toFixed(2)}°, ${ippLon.toFixed(2)}°</span>
                                    <span class="station-map-popup-label">Độ cao IPP</span>
                                    <span class="station-map-popup-value">350 km</span>
                                    <span class="station-map-popup-label">Góc ngẩng / Az</span>
                                    <span class="station-map-popup-value">${elDeg.toFixed(1)}° / ${azDeg.toFixed(1)}°</span>
                                    <span class="station-map-popup-label">VTEC tại IPP</span>
                                    <span class="station-map-popup-value" style="color:#38bdf8;font-weight:700;">${vtecVal !== null ? `${vtecVal} TECU` : '--'}</span>
                                    <span class="station-map-popup-label">Slant TEC</span>
                                    <span class="station-map-popup-value">${stecVal !== null ? `${stecVal} TECU` : '--'}</span>
                                    <span class="station-map-popup-label">Trễ L1 (delay)</span>
                                    <span class="station-map-popup-value" style="color:#f59e0b;font-weight:700;">${delayM !== null ? `~${delayM} m` : '--'}</span>
                                </div>
                            </div>
                        `);

                        this.rayLayerGroup.addLayer(rayToIpp);
                        this.rayLayerGroup.addLayer(rayToSat);
                        this.rayLayerGroup.addLayer(ippMarker);
                    }

                    // Satellite Popup
                    const lookInfo = look ? `
                        <span class="station-map-popup-label">Góc ngẩng (El)</span>
                        <span class="station-map-popup-value" style="color:${isVisible ? '#22c55e' : '#f87171'};font-weight:700;">
                            ${elDeg.toFixed(1)}° (${isVisible ? 'Khả kiến' : 'Dưới chân trời'})
                        </span>
                        <span class="station-map-popup-label">Phương vị (Az)</span>
                        <span class="station-map-popup-value">${azDeg.toFixed(1)}°</span>
                    ` : '';

                    satObj.marker.bindPopup(`
                        <div class="station-map-popup gnss-sat-popup">
                            <div class="d-flex justify-content-between align-items-center mb-1">
                                <span class="fw-bold" style="color:${cfg.color};">${cfg.flag} ${satObj.code}</span>
                                <span class="badge" style="background:${cfg.color}; color:#fff;">${cfg.code}</span>
                            </div>
                            <div class="small text-muted mb-1">${satObj.name}</div>
                            <div class="station-map-popup-grid" style="font-size:0.75rem;">
                                <span class="station-map-popup-label">Vĩ độ / Kinh độ</span>
                                <span class="station-map-popup-value">${lat.toFixed(2)}°, ${lon.toFixed(2)}°</span>
                                <span class="station-map-popup-label">Độ cao quỹ đạo</span>
                                <span class="station-map-popup-value">${Math.round(altKm).toLocaleString()} km</span>
                                <span class="station-map-popup-label">Chòm vệ tinh</span>
                                <span class="station-map-popup-value">${cfg.name}</span>
                                <span class="station-map-popup-label">NORAD ID</span>
                                <span class="station-map-popup-value">${satObj.norad_id}</span>
                                ${lookInfo}
                            </div>
                        </div>
                    `);

                } catch (propErr) {
                    // Ignore single propagation failure
                }
            });

            this.stats = {
                total: this.rawSatellites.length,
                visible: visibleCount,
                byConstellation: constCounts,
            };
        }

        updateSatelliteMarkers() {
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (!this.layerGroup || !L) return;

            // Initialize activeSatellites map
            this.rawSatellites.forEach(s => {
                if (!this.activeSatellites.has(s.key)) {
                    this.activeSatellites.set(s.key, {
                        ...s,
                        marker: null,
                    });
                }
            });

            this.propagateTick();
        }

        setSelectedStation(station) {
            this.selectedStation = station;
            this.propagateTick();
            this.notify();
        }

        clearSelectedStation() {
            this.selectedStation = null;
            if (this.rayLayerGroup) {
                this.rayLayerGroup.clearLayers();
            }
            this.propagateTick();
            this.notify();
        }

        setFilter(key, value) {
            if (this.filters[key] !== undefined) {
                this.filters[key] = value;
                this.propagateTick();
                this.notify();
            }
        }

        attachToMap(map) {
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (!map || !L) return;

            this.mapInstance = map;
            if (!this.layerGroup) {
                this.layerGroup = L.layerGroup().addTo(map);
            } else if (!map.hasLayer(this.layerGroup)) {
                this.layerGroup.addTo(map);
            }

            if (!this.rayLayerGroup) {
                this.rayLayerGroup = L.layerGroup().addTo(map);
            } else if (!map.hasLayer(this.rayLayerGroup)) {
                this.rayLayerGroup.addTo(map);
            }

            if (!this.rawSatellites.length) {
                this.fetchTle();
            } else {
                this.updateSatelliteMarkers();
            }

            // Start 1Hz propagation timer
            if (this.tickTimer) clearInterval(this.tickTimer);
            this.tickTimer = setInterval(() => {
                if (document.visibilityState === 'hidden') return;
                this.propagateTick();
            }, 1000);

            this.notify();
        }

        detachFromMap() {
            if (this.tickTimer) {
                clearInterval(this.tickTimer);
                this.tickTimer = null;
            }
            if (this.mapInstance) {
                if (this.layerGroup && this.mapInstance.hasLayer(this.layerGroup)) {
                    this.mapInstance.removeLayer(this.layerGroup);
                }
                if (this.rayLayerGroup && this.mapInstance.hasLayer(this.rayLayerGroup)) {
                    this.mapInstance.removeLayer(this.rayLayerGroup);
                }
            }
            this.mapInstance = null;
            this.notify();
        }
    }

    window.GnssSatelliteOverlay = new GnssSatelliteManager();
})();
