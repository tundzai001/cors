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
            this.rayLayersCache = new Map(); // satKey -> { rayToIpp, rayToSat, ippMarker }
            this.tickTimer = null;
            this.isPropagating = false;
            this.listeners = new Set();

            this.filters = {
                gps: true,
                galileo: true,
                beidou: true,
                glonass: true,
                onlyVisible: false,
                minElevation: 12.0, // Default 12° CORS elevation mask cutoff (~29 sats)
                showIpp: true,      // Show/hide 350km IPP diamond markers
                matchRtcm: true,    // Match live RTCM stream from CORS edge when available
            };

            this.selectedStation = null; // { lat, lon, alt, name, serial, status }
            this.stationRayMarkers = [];
            this.stats = { total: 0, visible: 0, byConstellation: {}, isStationOffline: false, hasLiveRtcm: false, liveRtcmCount: 0 };
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

        getLiveRtcmSatellite(satObj) {
            if (!this.selectedStation) return null;
            const selSerial = window.globalState?.selectedDeviceSerial;
            const targetSerial = this.selectedStation.serial;
            if (selSerial && targetSerial && String(selSerial).trim() !== String(targetSerial).trim()) {
                return null;
            }
            const nmeaData = window.getCurrentNmeaData?.();
            if (!nmeaData || !nmeaData.satellites) return null;

            const satCode = String(satObj.code || '').toUpperCase(); // e.g. "G08", "C21"
            const constKey = satObj.constellationKey; // "gps", "glonass", "galileo", "beidou"
            const expectedSystem = constKey === 'gps' ? 'us' : constKey === 'glonass' ? 'ru' : constKey === 'galileo' ? 'eu' : constKey === 'beidou' ? 'cn' : '';
            const prnNum = parseInt(satCode.replace(/\D/g, ''), 10);

            for (const sat of Object.values(nmeaData.satellites)) {
                if (!sat) continue;
                const satSys = String(sat.system || '').toLowerCase();
                const sPrnStr = String(sat.prn || '').toUpperCase();
                const sPrnNum = parseInt(sPrnStr.replace(/\D/g, ''), 10);

                if (sPrnStr === satCode) return sat;
                if (satSys && expectedSystem && satSys === expectedSystem && sPrnNum === prnNum) {
                    return sat;
                }
            }
            return null;
        }

        propagateTick() {
            const satLib = window.satellite;
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (!satLib || !this.mapInstance || !L) return;

            const now = new Date();
            const gmst = satLib.gstime(now);

            const isStationOffline = Boolean(
                this.selectedStation && (
                    this.selectedStation.status === 'offline' ||
                    this.selectedStation.status === 'lost'
                )
            );

            let obsGd = null;
            if (this.selectedStation && !isStationOffline && Number.isFinite(this.selectedStation.lat) && Number.isFinite(this.selectedStation.lon)) {
                obsGd = {
                    latitude: this.selectedStation.lat * TO_RAD,
                    longitude: this.selectedStation.lon * TO_RAD,
                    height: (this.selectedStation.alt || 50) / 1000.0,
                };
            }

            // If station is offline or unselected, wipe all rays & IPP markers immediately
            if (isStationOffline || !obsGd) {
                if (this.rayLayerGroup && this.rayLayersCache.size > 0) {
                    this.rayLayerGroup.clearLayers();
                    this.rayLayersCache.clear();
                }
            }

            const nmeaData = window.getCurrentNmeaData?.();
            const liveSats = (nmeaData && nmeaData.satellites) ? Object.values(nmeaData.satellites) : [];
            const hasLiveRtcm = Boolean(
                this.selectedStation &&
                !isStationOffline &&
                liveSats.length > 0 &&
                (!window.globalState?.selectedDeviceSerial || String(window.globalState.selectedDeviceSerial).trim() === String(this.selectedStation.serial).trim())
            );

            const activeRayKeys = new Set();
            let visibleCount = 0;
            const constCounts = { gps: 0, galileo: 0, beidou: 0, glonass: 0 };
            const minEl = Number.isFinite(this.filters.minElevation) ? this.filters.minElevation : 12.0;

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
                    let liveRtcmSat = null;

                    if (obsGd) {
                        const posEcf = satLib.eciToEcf(posVel.position, gmst);
                        look = satLib.ecfToLookAngles(obsGd, posEcf);
                        azDeg = ((look.azimuth * TO_DEG) + 360) % 360;
                        elDeg = look.elevation * TO_DEG;
                        if (hasLiveRtcm) {
                            liveRtcmSat = this.getLiveRtcmSatellite(satObj);
                        }
                        if (hasLiveRtcm && this.filters.matchRtcm !== false) {
                            isVisible = Boolean(liveRtcmSat); // Match exact live RTCM tracked satellite
                        } else {
                            isVisible = elDeg >= minEl; // CORS Elevation cut-off mask
                        }
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

                    // If station is selected, online, and satellite is visible, maintain LOS Ray & IPP
                    if (obsGd && !isStationOffline && isVisible && look && this.rayLayerGroup) {
                        const satKey = satObj.key;
                        activeRayKeys.add(satKey);

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

                        const ippPopupHtml = `
                            <div class="station-map-popup gnss-ipp-popup">
                                <div class="fw-bold mb-1" style="color:#38bdf8;">
                                    <i class="bi bi-bullseye me-1"></i>Điểm xuyên tầng điện ly (IPP)
                                </div>
                                <div class="small mb-1 text-muted">
                                    Vệ tinh: <b style="color:${cfg.color};">${satObj.code}</b> (${cfg.name})<br>
                                    Trạm: <b>${this.selectedStation.name || this.selectedStation.serial}</b>
                                </div>
                                ${liveRtcmSat ? `
                                    <div class="mb-2 px-2 py-1 rounded" style="background:rgba(34,197,94,0.15); border:1px solid rgba(34,197,94,0.35); font-size:0.72rem; color:#86efac;">
                                        <i class="bi bi-broadcast me-1"></i>Đang khóa tín hiệu RTCM MSM (SNR: <b>${liveRtcmSat.snr || '--'} dB-Hz</b>)
                                    </div>
                                ` : `
                                    <div class="mb-2 px-2 py-1 rounded" style="background:rgba(56,189,248,0.1); border:1px solid rgba(56,189,248,0.25); font-size:0.72rem; color:#7dd3fc;">
                                        <i class="bi bi-clock me-1"></i>Mô phỏng quỹ đạo SGP4 (Góc ngẩng: ${elDeg.toFixed(1)}°)
                                    </div>
                                `}
                                <div class="station-map-popup-grid" style="font-size:0.75rem;">
                                    <span class="station-map-popup-label">Tọa độ IPP</span>
                                    <span class="station-map-popup-value">${ippLat.toFixed(2)}°, ${ippLon.toFixed(2)}°</span>
                                    <span class="station-map-popup-label">Độ cao IPP</span>
                                    <span class="station-map-popup-value">350 km (Lớp F2)</span>
                                    <span class="station-map-popup-label">Góc ngẩng / Az</span>
                                    <span class="station-map-popup-value">${elDeg.toFixed(1)}° / ${azDeg.toFixed(1)}°</span>
                                    <span class="station-map-popup-label">VTEC tại IPP</span>
                                    <span class="station-map-popup-value" style="color:#38bdf8;font-weight:700;">${vtecVal !== null ? `${vtecVal} TECU` : '--'}</span>
                                    <span class="station-map-popup-label">Slant TEC (STEC)</span>
                                    <span class="station-map-popup-value">${stecVal !== null ? `${stecVal} TECU` : '--'}</span>
                                    <span class="station-map-popup-label">Trễ pha L1</span>
                                    <span class="station-map-popup-value" style="color:#f59e0b;font-weight:700;">${delayM !== null ? `~${delayM} m` : '--'}</span>
                                </div>
                                <div class="small text-muted mt-2 border-top pt-1" style="font-size:0.7rem;">
                                    * IPP là giao điểm tia sóng qua độ cao 350km gây trễ tín hiệu trạm CORS.
                                </div>
                            </div>
                        `;
                        const tooltipText = liveRtcmSat
                            ? `IPP 350km: ${satObj.code} (${vtecVal !== null ? `${vtecVal} TECU` : '--'} • SNR ${liveRtcmSat.snr}dB)`
                            : `IPP 350km: ${satObj.code} (${vtecVal !== null ? `${vtecVal} TECU` : '--'})`;

                        const cached = this.rayLayersCache.get(satKey);
                        if (cached) {
                            // In-place update keeps Leaflet popup open continuously
                            cached.rayToIpp.setLatLngs([[this.selectedStation.lat, this.selectedStation.lon], [ippLat, ippLon]]);
                            cached.rayToSat.setLatLngs([[ippLat, ippLon], [lat, lon]]);
                            cached.ippMarker.setLatLng([ippLat, ippLon]);
                            if (cached.ippMarker.getPopup()) {
                                cached.ippMarker.setPopupContent(ippPopupHtml);
                            }
                            if (cached.ippMarker.getTooltip()) {
                                cached.ippMarker.setTooltipContent(tooltipText);
                            }

                            // Show or hide IPP diamond based on showIpp filter
                            const shouldShowIpp = this.filters.showIpp !== false;
                            if (shouldShowIpp && !this.rayLayerGroup.hasLayer(cached.ippMarker)) {
                                this.rayLayerGroup.addLayer(cached.ippMarker);
                            } else if (!shouldShowIpp && this.rayLayerGroup.hasLayer(cached.ippMarker)) {
                                this.rayLayerGroup.removeLayer(cached.ippMarker);
                            }
                        } else {
                            // First creation for this satellite
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
                            const ippMarker = L.marker([ippLat, ippLon], {
                                icon: this.createIppIcon(vtecVal),
                                zIndexOffset: 950,
                            });
                            ippMarker.bindPopup(ippPopupHtml, { autoClose: false });
                            ippMarker.bindTooltip(tooltipText, { direction: 'top', offset: [0, -6] });

                            this.rayLayerGroup.addLayer(rayToIpp);
                            this.rayLayerGroup.addLayer(rayToSat);
                            if (this.filters.showIpp !== false) {
                                this.rayLayerGroup.addLayer(ippMarker);
                            }

                            this.rayLayersCache.set(satKey, {
                                rayToIpp,
                                rayToSat,
                                ippMarker,
                            });
                        }
                    }

                    // Satellite Popup
                    const lookInfo = look ? `
                        <span class="station-map-popup-label">Góc ngẩng (El)</span>
                        <span class="station-map-popup-value" style="color:${isVisible ? '#22c55e' : '#f87171'};font-weight:700;">
                            ${elDeg.toFixed(1)}° (${isVisible ? `Khả kiến (≥${minEl}°)` : `Dưới góc cắt <${minEl}°`})
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

            // Clean up cached layers for satellites that are no longer active
            for (const [key, cached] of this.rayLayersCache.entries()) {
                if (!activeRayKeys.has(key)) {
                    if (this.rayLayerGroup) {
                        this.rayLayerGroup.removeLayer(cached.rayToIpp);
                        this.rayLayerGroup.removeLayer(cached.rayToSat);
                        this.rayLayerGroup.removeLayer(cached.ippMarker);
                    }
                    this.rayLayersCache.delete(key);
                }
            }

            this.stats = {
                total: this.rawSatellites.length,
                visible: visibleCount,
                byConstellation: constCounts,
                isStationOffline,
                hasLiveRtcm,
                liveRtcmCount: liveSats.length,
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
            if (this.rayLayerGroup) {
                this.rayLayerGroup.clearLayers();
            }
            this.rayLayersCache.clear();
            this.propagateTick();
            this.notify();
        }

        clearSelectedStation() {
            this.selectedStation = null;
            if (this.rayLayerGroup) {
                this.rayLayerGroup.clearLayers();
            }
            this.rayLayersCache.clear();
            this.propagateTick();
            this.notify();
        }

        setFilter(key, value) {
            this.filters[key] = value;
            this.propagateTick();
            this.notify();
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
            if (this.rayLayersCache) {
                this.rayLayersCache.clear();
            }
            this.mapInstance = null;
            this.notify();
        }
    }

    window.GnssSatelliteOverlay = new GnssSatelliteManager();
})();
