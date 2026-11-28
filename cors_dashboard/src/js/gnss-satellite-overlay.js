// Real-time GNSS Satellite Orbit Propagation & Ionospheric Pierce Point (IPP) Overlay.
// Constellations: GPS, Galileo, BeiDou, GLONASS, QZSS.
(() => {
    'use strict';

    const CONSTELLATION_META = {
        gps: { name: 'GPS (USA)', code: 'GPS', color: '#22c55e', prefix: 'G', flag: '🇺🇸', nominalAlt: '20,200 km' },
        galileo: { name: 'Galileo (EU)', code: 'GAL', color: '#3b82f6', prefix: 'E', flag: '🇪🇺', nominalAlt: '23,222 km' },
        beidou: { name: 'BeiDou (China)', code: 'BDS', color: '#f97316', prefix: 'C', flag: '🇨🇳', nominalAlt: '21,500 - 35,786 km' },
        glonass: { name: 'GLONASS (Russia)', code: 'GLO', color: '#ef4444', prefix: 'R', flag: '🇷🇺', nominalAlt: '19,130 km' },
        qzss: { name: 'QZSS (Japan)', code: 'QZSS', color: '#a855f7', prefix: 'J', flag: '🇯🇵', nominalAlt: '32,000 - 40,000 km' },
    };

    const RTCM_SYSTEM_ALIASES = {
        gps: 'gps', us: 'gps', g: 'gps',
        glonass: 'glonass', ru: 'glonass', r: 'glonass',
        galileo: 'galileo', eu: 'galileo', e: 'galileo',
        beidou: 'beidou', cn: 'beidou', c: 'beidou',
        qzss: 'qzss', jp: 'qzss', j: 'qzss',
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
                qzss: true,
                showIpp: true,      // Show/hide 350km IPP diamond markers
            };

            this.selectedStation = null; // { lat, lon, alt, name, serial, status }
            this.stationRayMarkers = [];
            this.stats = { total: 0, visible: 0, byConstellation: {}, isStationOffline: false, hasLiveRtcm: false, liveRtcmCount: 0, unmatchedRtcmCount: 0, unmatchedRtcmCodes: [] };
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
            if (this.tleRequest != null) return this.tleRequest;
            this.tleRequest = this._fetchTle();
            try {
                return await this.tleRequest;
            } finally {
                this.tleRequest = null;
            }
        }

        async _fetchTle() {
            try {
                const resp = await (window.apiFetch || fetch)('/api/gnss/satellites/tle');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                if (!data?.constellations) return;
                this.tleInfo = { is_stale: data.is_stale, updated_at_utc: data.updated_at_utc };

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
                                    key: `${constKey}:${satItem.norad_id || satItem.code}`,
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

        getSatelliteLabel(satellite) {
            return /^[GRECJI]\d{2,3}$/.test(satellite.code || '') ? satellite.code : `#${satellite.norad_id || satellite.name || 'N/A'}`;
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
            let color = '#38bdf8';
            if (tecVal !== null && tecVal !== undefined) {
                if (tecVal > 55) color = '#ef4444';
                else if (tecVal > 35) color = '#f97316';
                else if (tecVal > 20) color = '#eab308';
            }
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

        getLiveRtcmSatellites(station = this.selectedStation) {
            const targetSerial = String(station?.serial || '').trim();
            if (!targetSerial || String(window.globalState?.selectedDeviceSerial || '').trim() !== targetSerial) return [];
            const satellites = window.getCurrentNmeaData?.()?.satellites || {};
            const now = Date.now();
            const live = Object.values(satellites).filter(sat => {
                if (!sat || !(String(sat.id || '').toUpperCase().startsWith('RTCM:') || String(sat.talker || '').toUpperCase() === 'RTCM')) return false;
                const lastSeen = Number(sat.lastSeen) || 0;
                return lastSeen > 0 && now - lastSeen >= 0 && now - lastSeen <= 15000;
            });
            const unique = new Map();
            live.forEach(sat => {
                const key = this.getRtcmSatelliteKey(sat) || String(sat.id || `${sat.system || sat.navSystem}:${sat.prn}`);
                if (!unique.has(key)) unique.set(key, sat);
            });
            return [...unique.values()];
        }

        getRtcmSatelliteKey(satObj) {
            const system = RTCM_SYSTEM_ALIASES[String(satObj?.constellationKey || satObj?.system || satObj?.navSystem || '').toLowerCase()];
            if (satObj?.code && /[?#]/.test(String(satObj.code))) return null;
            let prn = Number.parseInt(String(satObj?.code || satObj?.prn || '').replace(/\D/g, ''), 10);
            if (!Number.isFinite(prn)) return null;
            if (system === 'qzss' && prn >= 1 && prn <= 10) {
                prn += 192;
            }
            return system ? `${system}:${prn}` : null;
        }

        getRtcmSatelliteCode(satObj) {
            const key = this.getRtcmSatelliteKey(satObj);
            if (!key) return String(satObj?.id || satObj?.prn || 'PRN?');
            const [system, prn] = key.split(':');
            const prefix = CONSTELLATION_META[system]?.prefix || '';
            return `${prefix}${String(prn).padStart(system === 'qzss' ? 3 : 2, '0')}`;
        }

        getLiveRtcmSatellite(satObj, station = this.selectedStation, liveSatellites = null) {
            if (!station) return null;
            const expectedKey = this.getRtcmSatelliteKey(satObj);
            if (!expectedKey) return null;
            return (liveSatellites || this.getLiveRtcmSatellites(station))
                .find(sat => this.getRtcmSatelliteKey(sat) === expectedKey) || null;
        }

        propagateTick() {
            const satLib = window.satellite;
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (!satLib || (!this.renderFrame && (!this.mapInstance || !L))) return;

            const now = new Date();
            const gmst = satLib.gstime(now);
            const context = this.createPropagationContext(satLib, L, now, gmst);
            this.activeSatellites.forEach(satObj => this.updateSatellite(satObj, context));
            this.cleanupRayLayers(context.activeRayKeys);
            this.updatePropagationStats(context);
        }

        createPropagationContext(satLib, L, now, gmst) {
            const isStationOffline = Boolean(
                this.selectedStation && ['offline', 'lost'].includes(this.selectedStation.status)
            );
            const station = this.selectedStation;
            const obsGd = station && !isStationOffline && Number.isFinite(station.lat) && Number.isFinite(station.lon)
                ? { latitude: station.lat * TO_RAD, longitude: station.lon * TO_RAD, height: (station.alt || 50) / 1000.0 }
                : null;

            // If station is offline or unselected, wipe all rays & IPP markers immediately.
            if ((isStationOffline || !obsGd) && this.rayLayerGroup && this.rayLayersCache.size > 0) {
                this.rayLayerGroup.clearLayers();
                this.rayLayersCache.clear();
            }

            const liveRtcmSatellites = this.getLiveRtcmSatellites(station);
            const hasLiveRtcm = !isStationOffline && liveRtcmSatellites.length > 0;
            const liveRtcmByKey = new Map(liveRtcmSatellites
                .map(sat => [this.getRtcmSatelliteKey(sat), sat])
                .filter(([key]) => Boolean(key)));

            return {
                satLib, L, now, gmst, isStationOffline, station, obsGd,
                liveRtcmSatellites, hasLiveRtcm, liveRtcmByKey, matchedRtcmKeys: new Set(),
                activeRayKeys: new Set(), frameFeatures: [], visibleCount: 0,
                constCounts: { gps: 0, galileo: 0, beidou: 0, glonass: 0, qzss: 0 },
            };
        }

        updateSatellite(satObj, context) {
            const cfg = CONSTELLATION_META[satObj.constellationKey];
            const shouldDisplay = this.filters[satObj.constellationKey] !== false;
            try {
                const state = this.getSatelliteState(satObj, context);
                if (!state) {
                    if (satObj.marker && this.layerGroup?.hasLayer(satObj.marker)) {
                        this.layerGroup.removeLayer(satObj.marker);
                    }
                    return;
                }

                if (state.isTrackedByRtcm) {
                    context.visibleCount++;
                    context.constCounts[satObj.constellationKey] = (context.constCounts[satObj.constellationKey] || 0) + 1;
                }

                if (this.renderFrame) {
                    if (shouldDisplay) this.appendSatelliteFeatures(satObj, cfg, state, context);
                    return;
                }
                if (!shouldDisplay) {
                    if (satObj.marker && this.layerGroup.hasLayer(satObj.marker)) {
                        this.layerGroup.removeLayer(satObj.marker);
                    }
                    return;
                }

                this.updateSatelliteMarker(satObj, state, context.L);
                this.updateIppLayers(satObj, cfg, state, context);
                this.bindSatellitePopup(satObj, cfg, state);
            } catch (propErr) {
                // Ignore single propagation failure.
            }
        }

        getSatelliteState(satObj, context) {
            const { satLib, now, gmst, obsGd, liveRtcmByKey, matchedRtcmKeys, hasLiveRtcm } = context;
            const posVel = satLib.propagate(satObj.satrec, now);
            if (!posVel?.position) return null;

            const gd = satLib.eciToGeodetic(posVel.position, gmst);
            const lat = satLib.degreesLat(gd.latitude);
            let lon = satLib.degreesLong(gd.longitude);
            while (lon < -180) lon += 360;
            while (lon > 180) lon -= 360;

            let look = null;
            let liveRtcmSat = null;
            if (obsGd) {
                const posEcf = satLib.eciToEcf(posVel.position, gmst);
                look = satLib.ecfToLookAngles(obsGd, posEcf);
                const rtcmKey = this.getRtcmSatelliteKey(satObj);
                if (hasLiveRtcm && rtcmKey && !matchedRtcmKeys.has(rtcmKey)) {
                    liveRtcmSat = liveRtcmByKey.get(rtcmKey) || null;
                    if (liveRtcmSat) matchedRtcmKeys.add(rtcmKey);
                }
            }

            const azDeg = look ? ((look.azimuth * TO_DEG) + 360) % 360 : null;
            const elDeg = look ? look.elevation * TO_DEG : null;
            return {
                lat, lon, altKm: gd.height, look, azDeg, elDeg, liveRtcmSat,
                isAboveHorizon: elDeg !== null && elDeg >= 0,
                isTrackedByRtcm: Boolean(liveRtcmSat),
            };
        }

        appendSatelliteFeatures(satObj, cfg, state, context) {
            const { frameFeatures } = context;
            frameFeatures.push({ type: 'Feature', properties: {
                kind: 'satellite', code: this.getSatelliteLabel(satObj), name: satObj.name, noradId: satObj.norad_id,
                identityStatus: satObj.identity_status || 'unconfirmed', identityKnown: /^[GRECJI]\d{2,3}$/.test(satObj.code || ''),
                altKm: state.altKm, tracked: state.isTrackedByRtcm, color: cfg.color,
            }, geometry: { type: 'Point', coordinates: [state.lon, state.lat] } });
            if (context.obsGd && state.isTrackedByRtcm && state.look && this.filters.showIpp !== false) {
                this.appendIppFeatures(satObj, cfg, state, context);
            }
        }

        appendIppFeatures(satObj, cfg, state, context) {
            const { ippLat, ippLon } = this.calculateIpp(
                context.station.lat, context.station.lon, state.look.elevation, state.look.azimuth
            );
            const probe = window.IonosphereOverlay?.sample?.(ippLat, ippLon);
            context.frameFeatures.push({ type: 'Feature', properties: {
                kind: 'ipp', code: satObj.code, tracked: true, color: '#38bdf8', vtec: probe?.tec ?? null,
            }, geometry: { type: 'Point', coordinates: [ippLon, ippLat] } });

            const coordinates = [[context.station.lon, context.station.lat], [ippLon, ippLat], [state.lon, state.lat]];
            for (let i = 1; i < coordinates.length; i++) {
                while (coordinates[i][0] - coordinates[i - 1][0] > 180) coordinates[i][0] -= 360;
                while (coordinates[i][0] - coordinates[i - 1][0] < -180) coordinates[i][0] += 360;
            }
            context.frameFeatures.push({ type: 'Feature', properties: {
                kind: 'ray', code: satObj.code, color: cfg.color,
            }, geometry: { type: 'LineString', coordinates } });
            this.appendOrbitFeatures(satObj, cfg, context);
        }

        appendOrbitFeatures(satObj, cfg, context) {
            if (!satObj.satrec) return;
            const { satLib, now } = context;
            const orbitPoints = [];
            const dtMins = 20;
            const steps = 24;
            for (let sIdx = -steps / 2; sIdx <= steps / 2; sIdx++) {
                const time = new Date(now.getTime() + sIdx * dtMins * 60 * 1000);
                const pv = satLib.propagate(satObj.satrec, time);
                if (!pv?.position) continue;
                const geo = satLib.eciToGeodetic(pv.position, satLib.gstime(time));
                let lon = satLib.degreesLong(geo.longitude);
                while (lon < -180) lon += 360;
                while (lon > 180) lon -= 360;
                orbitPoints.push([lon, satLib.degreesLat(geo.latitude)]);
            }
            this.appendOrbitSegments(satObj, cfg, orbitPoints, context.frameFeatures);
        }

        appendOrbitSegments(satObj, cfg, orbitPoints, frameFeatures) {
            if (orbitPoints.length < 2) return;
            let segment = [orbitPoints[0]];
            for (let i = 1; i < orbitPoints.length; i++) {
                if (Math.abs(orbitPoints[i][0] - orbitPoints[i - 1][0]) > 180) {
                    this.appendOrbitSegment(satObj, cfg, segment, frameFeatures);
                    segment = [];
                }
                segment.push(orbitPoints[i]);
            }
            this.appendOrbitSegment(satObj, cfg, segment, frameFeatures);
        }

        appendOrbitSegment(satObj, cfg, segment, frameFeatures) {
            if (segment.length < 2) return;
            frameFeatures.push({ type: 'Feature', properties: {
                kind: 'orbit', code: satObj.code, color: cfg.color,
            }, geometry: { type: 'LineString', coordinates: segment } });
        }

        updateSatelliteMarker(satObj, state, L) {
            if (!satObj.marker) {
                satObj.marker = L.marker([state.lat, state.lon], {
                    icon: this.createSatelliteIcon(this.getSatelliteLabel(satObj), satObj.constellationKey, state.isTrackedByRtcm),
                    zIndexOffset: state.isTrackedByRtcm ? 1000 : 700,
                });
                this.layerGroup.addLayer(satObj.marker);
                return;
            }
            satObj.marker.setLatLng([state.lat, state.lon]);
            if (!this.layerGroup.hasLayer(satObj.marker)) this.layerGroup.addLayer(satObj.marker);
        }

        updateIppLayers(satObj, cfg, state, context) {
            const { obsGd, isStationOffline, station, L } = context;
            if (!obsGd || isStationOffline || !state.isTrackedByRtcm || !state.look || !this.rayLayerGroup || this.filters.showIpp === false) return;

            const satKey = satObj.key;
            context.activeRayKeys.add(satKey);
            const { ippLat, ippLon, mf } = this.calculateIpp(
                station.lat, station.lon, state.look.elevation, state.look.azimuth
            );
            const measurements = this.getIppMeasurements(ippLat, ippLon, mf);
            const popupHtml = this.buildIppPopupHtml(satObj, cfg, state, ippLat, ippLon, measurements, context);
            const tooltipText = `IPP 350km: ${this.getSatelliteLabel(satObj)} (RTCM ${state.liveRtcmSat.messageType || ''} • SNR ${state.liveRtcmSat.snr || state.liveRtcmSat.cnr || '--'} dB-Hz)`;
            const rayData = { satKey, ippLat, ippLon, state, cfg, popupHtml, tooltipText, measurements, L };
            if (this.rayLayersCache.has(satKey)) this.updateCachedRayLayers(rayData, station);
            else this.createRayLayers(rayData, station);
        }

        getIppMeasurements(ippLat, ippLon, mf) {
            let vtecVal = null;
            let stecVal = null;
            let delayM = null;
            if (window.IonosphereOverlay?.sample) {
                const probe = window.IonosphereOverlay.sample(ippLat, ippLon);
                if (probe?.tec !== undefined) {
                    vtecVal = probe.tec;
                    stecVal = Math.round(vtecVal * mf * 10) / 10;
                    delayM = (stecVal * 0.1624).toFixed(2);
                }
            }
            return { vtecVal, stecVal, delayM };
        }

        buildIppPopupHtml(satObj, cfg, state, ippLat, ippLon, measurements, context) {
            const { vtecVal, stecVal, delayM } = measurements;
            const rtcmSat = state.liveRtcmSat;
            return `
                <div class="station-map-popup gnss-ipp-popup">
                    <div class="fw-bold mb-1" style="color:#38bdf8;">
                        <i class="bi bi-bullseye me-1"></i>Điểm xuyên tầng điện ly (IPP)
                    </div>
                    <div class="small mb-1 text-muted">
                        Vệ tinh: <b style="color:${cfg.color};">${this.getSatelliteLabel(satObj)}</b> (${cfg.name})<br>
                        Trạm: <b>${context.station.name || context.station.serial}</b>
                    </div>
                    <div class="mb-2 px-2 py-1 rounded" style="background:rgba(34,197,94,0.15); border:1px solid rgba(34,197,94,0.35); font-size:0.72rem; color:#86efac;">
                        <i class="bi bi-broadcast me-1"></i>Agent đã giải mã RTCM (SNR: <b>${rtcmSat.snr || rtcmSat.cnr || '--'} dB-Hz</b>)
                    </div>
                    <div class="station-map-popup-grid" style="font-size:0.75rem;">
                        <span class="station-map-popup-label">Tọa độ IPP</span>
                        <span class="station-map-popup-value">${ippLat.toFixed(2)}°, ${ippLon.toFixed(2)}°</span>
                        <span class="station-map-popup-label">Độ cao IPP</span>
                        <span class="station-map-popup-value">350 km (Lớp F2)</span>
                        <span class="station-map-popup-label">Góc ngẩng / Az</span>
                        <span class="station-map-popup-value">${state.elDeg.toFixed(1)}° / ${state.azDeg.toFixed(1)}°</span>
                        <span class="station-map-popup-label">VTEC tại IPP</span>
                        <span class="station-map-popup-value" style="color:#38bdf8;font-weight:700;">${vtecVal !== null ? `${vtecVal} TECU` : '--'}</span>
                        <span class="station-map-popup-label">Slant TEC (STEC)</span>
                        <span class="station-map-popup-value">${stecVal !== null ? `${stecVal} TECU` : '--'}</span>
                        <span class="station-map-popup-label">Trễ pha L1</span>
                        <span class="station-map-popup-value" style="color:#f59e0b;font-weight:700;">${delayM !== null ? `~${delayM} m` : '--'}</span>
                    </div>
                    <div class="small text-muted mt-2 border-top pt-1" style="font-size:0.7rem;">
                        * IPP là giao điểm tia sóng qua độ cao 350km gây trễ tín hiệu trạm Base.
                    </div>
                </div>
            `;
        }

        updateCachedRayLayers(rayData, station) {
            const { satKey, ippLat, ippLon, state, popupHtml, tooltipText } = rayData;
            const cached = this.rayLayersCache.get(satKey);
            cached.rayToIpp.setLatLngs([[station.lat, station.lon], [ippLat, ippLon]]);
            cached.rayToSat.setLatLngs([[ippLat, ippLon], [state.lat, state.lon]]);
            cached.ippMarker.setLatLng([ippLat, ippLon]);
            if (cached.ippMarker.getPopup()) cached.ippMarker.setPopupContent(popupHtml);
            if (cached.ippMarker.getTooltip()) cached.ippMarker.setTooltipContent(tooltipText);
            if (!this.rayLayerGroup.hasLayer(cached.ippMarker)) this.rayLayerGroup.addLayer(cached.ippMarker);
        }

        createRayLayers(rayData, station) {
            const { satKey, ippLat, ippLon, state, cfg, popupHtml, tooltipText, measurements, L } = rayData;
            const rayToIpp = L.polyline([[station.lat, station.lon], [ippLat, ippLon]], {
                color: '#38bdf8', weight: 2, opacity: 0.9,
            });
            const rayToSat = L.polyline([[ippLat, ippLon], [state.lat, state.lon]], {
                color: cfg.color, weight: 1.5, opacity: 0.45, dashArray: '3 4',
            });
            const ippMarker = L.marker([ippLat, ippLon], {
                icon: this.createIppIcon(measurements.vtecVal), zIndexOffset: 950,
            });
            ippMarker.bindPopup(popupHtml, { autoClose: false });
            ippMarker.bindTooltip(tooltipText, { direction: 'top', offset: [0, -6] });
            this.rayLayerGroup.addLayer(rayToIpp);
            this.rayLayerGroup.addLayer(rayToSat);
            this.rayLayerGroup.addLayer(ippMarker);
            this.rayLayersCache.set(satKey, { rayToIpp, rayToSat, ippMarker });
        }

        bindSatellitePopup(satObj, cfg, state) {
            const { lat, lon, altKm, look, isAboveHorizon, isTrackedByRtcm, elDeg, azDeg } = state;
            let lookInfo = '';
            if (look) {
                const horizonColor = isAboveHorizon ? '#22c55e' : '#f87171';
                const horizonLabel = isAboveHorizon ? 'Trên đường chân trời' : 'Dưới đường chân trời';
                let rtcmInfo = '';
                if (this.selectedStation) {
                    const rtcmColor = isTrackedByRtcm ? '#22c55e' : '#94a3b8';
                    const rtcmLabel = isTrackedByRtcm ? 'Agent đang nhận' : 'Không có PRN RTCM khớp';
                    rtcmInfo = `
                    <span class="station-map-popup-label">Trạng thái RTCM</span>
                    <span class="station-map-popup-value" style="color:${rtcmColor};font-weight:700;">${rtcmLabel}</span>`;
                }
                lookInfo = `
                <span class="station-map-popup-label">Góc ngẩng (El)</span>
                <span class="station-map-popup-value" style="color:${horizonColor};font-weight:700;">
                    ${elDeg.toFixed(1)}° (${horizonLabel})
                </span>
                <span class="station-map-popup-label">Phương vị (Az)</span>
                <span class="station-map-popup-value">${azDeg.toFixed(1)}°</span>
                ${rtcmInfo}`;
            }
            satObj.marker.bindPopup(`
                <div class="station-map-popup gnss-sat-popup">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span class="fw-bold" style="color:${cfg.color};">${cfg.flag} ${this.getSatelliteLabel(satObj)}</span>
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
        }

        cleanupRayLayers(activeRayKeys) {
            for (const [key, cached] of this.rayLayersCache.entries()) {
                if (activeRayKeys.has(key)) continue;
                if (this.rayLayerGroup) {
                    this.rayLayerGroup.removeLayer(cached.rayToIpp);
                    this.rayLayerGroup.removeLayer(cached.rayToSat);
                    this.rayLayerGroup.removeLayer(cached.ippMarker);
                }
                this.rayLayersCache.delete(key);
            }
        }

        updatePropagationStats(context) {
            const unmatchedRtcmSatellites = context.liveRtcmSatellites.filter(sat => {
                const key = this.getRtcmSatelliteKey(sat);
                return !key || !context.matchedRtcmKeys.has(key);
            });
            this.stats = {
                total: this.rawSatellites.length,
                visible: context.visibleCount,
                byConstellation: context.constCounts,
                isStationOffline: context.isStationOffline,
                hasLiveRtcm: context.hasLiveRtcm,
                liveRtcmCount: context.liveRtcmSatellites.length,
                unmatchedRtcmCount: unmatchedRtcmSatellites.length,
                unmatchedRtcmCodes: unmatchedRtcmSatellites.slice(0, 8).map(sat => this.getRtcmSatelliteCode(sat)),
            };
            if (!this.renderFrame) return;
            this.renderFrame({ type: 'FeatureCollection', features: context.frameFeatures });
            this.notify();
        }

        updateSatelliteMarkers() {
            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (!this.renderFrame && (!this.layerGroup || !L)) return;

            // Refresh identities/status as well as orbital elements for existing NORADs.
            const next = new Map();
            this.rawSatellites.forEach(s => {
                const previous = this.activeSatellites.get(s.key);
                if (previous?.code !== s.code) previous?.marker?.setIcon?.(this.createSatelliteIcon(this.getSatelliteLabel(s), s.constellationKey, false));
                next.set(s.key, { ...previous, ...s, marker: previous?.marker || null });
            });
            for (const [key, satellite] of this.activeSatellites) {
                if (!next.has(key) && satellite.marker) this.layerGroup?.removeLayer(satellite.marker);
            }
            this.activeSatellites = next;

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
                void this.fetchTle();
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

        attachToRenderer(renderFrame) {
            this.detachFromMap();
            this.renderFrame = renderFrame;
            if (!this.rawSatellites.length) void this.fetchTle();
            else this.updateSatelliteMarkers();
            this.tickTimer = setInterval(() => {
                if (document.visibilityState !== 'hidden') this.propagateTick();
            }, 1000);
            this.notify();
        }

        detachFromMap() {
            this.renderFrame?.({ type: 'FeatureCollection', features: [] });
            this.renderFrame = null;
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
            // Remove child layers too: Leaflet keeps them inside a detached group.
            this.rayLayerGroup?.clearLayers();
            if (this.rayLayersCache) {
                this.rayLayersCache.clear();
            }
            this.mapInstance = null;
            this.notify();
        }
    }

    window.GnssSatelliteOverlay = new GnssSatelliteManager();
})();
