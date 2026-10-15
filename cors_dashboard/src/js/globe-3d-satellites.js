// 3D GNSS Satellites & CORS Stations Manager
// Synchronized 1Hz real-time propagation with 2D Leaflet GnssSatelliteOverlay
(() => {
    'use strict';

    const CONSTELLATIONS = {
        gps: { name: 'GPS', flag: '🇺🇸', color: 0x22c55e, hex: '#22c55e', nominalAltKm: 20200 },
        galileo: { name: 'Galileo', flag: '🇪🇺', color: 0x38bdf8, hex: '#38bdf8', nominalAltKm: 23222 },
        beidou: { name: 'BeiDou', flag: '🇨🇳', color: 0xf59e0b, hex: '#f59e0b', nominalAltKm: 21500 },
        glonass: { name: 'GLONASS', flag: '🇷🇺', color: 0xef4444, hex: '#ef4444', nominalAltKm: 19130 },
    };

    class Globe3DSatelliteLayer {
        constructor(globeView) {
            this.globe = globeView;
            this.satellitesGroup = new window.THREE.Group();
            this.stationsGroup = new window.THREE.Group();
            this.beamsGroup = new window.THREE.Group();
            this.singleOrbitGroup = new window.THREE.Group();

            this.globe.scene.add(this.satellitesGroup);
            this.globe.scene.add(this.stationsGroup);
            this.globe.scene.add(this.beamsGroup);
            this.globe.scene.add(this.singleOrbitGroup);

            this.satelliteMeshes = new Map(); // key -> { mesh, satItem }
            this.selectedSatellite = null;
            this.selectedStation = null;
            this.detailCardEl = null;
            this.unsubscribeOverlay = null;

            this._initDetailCard();
            this._setupOverlaySync();
        }

        _initDetailCard() {
            this.detailCardEl = document.createElement('div');
            this.detailCardEl.className = 'globe-detail-card';
            this.detailCardEl.style.display = 'none';
            this.globe.container.appendChild(this.detailCardEl);
        }

        _setupOverlaySync() {
            // Subscribe to 2D GnssSatelliteOverlay 1Hz tick for 100% synchronized realtime data
            const checkOverlay = () => {
                const mgr = window.GnssSatelliteOverlay;
                if (mgr && typeof mgr.subscribe === 'function') {
                    if (this.unsubscribeOverlay) this.unsubscribeOverlay();
                    this.unsubscribeOverlay = mgr.subscribe((state) => {
                        this.onOverlayTick(state);
                    });
                    this._buildSatelliteMeshes(mgr.rawSatellites || []);
                } else {
                    setTimeout(checkOverlay, 1000);
                }
            };
            checkOverlay();
        }

        _buildSatelliteMeshes(rawList) {
            const THREE = window.THREE;
            if (!THREE || !Array.isArray(rawList) || rawList.length === 0) return;

            this.satellitesGroup.clear();
            this.satelliteMeshes.clear();

            const dotGeo = new THREE.OctahedronGeometry(0.12, 0);

            rawList.forEach(satItem => {
                const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
                const mat = new THREE.MeshStandardMaterial({
                    color: cfg.color,
                    emissive: cfg.color,
                    emissiveIntensity: 0.8,
                    roughness: 0.2,
                });
                const mesh = new THREE.Mesh(dotGeo, mat);
                mesh.userData = { satItem, constellation: satItem.constellationKey };

                // Tiny glowing halo
                const haloGeo = new THREE.SphereGeometry(0.2, 8, 8);
                const haloMat = new THREE.MeshBasicMaterial({
                    color: cfg.color,
                    transparent: true,
                    opacity: 0.35,
                    depthWrite: false,
                });
                mesh.add(new THREE.Mesh(haloGeo, haloMat));

                // Invisible larger hit area for effortless clicking
                const hitGeo = new THREE.SphereGeometry(0.35, 6, 6);
                const hitMat = new THREE.MeshBasicMaterial({ visible: false });
                mesh.add(new THREE.Mesh(hitGeo, hitMat));

                this.satellitesGroup.add(mesh);
                this.satelliteMeshes.set(satItem.key, { mesh, satItem });
            });
        }

        // Called once every 1 second (1Hz) directly by GnssSatelliteOverlay
        onOverlayTick(state) {
            const satLib = window.satellite;
            const Math3D = window.Globe3DMath;
            if (!satLib || !Math3D || this.satelliteMeshes.size === 0) return;

            const now = new Date();
            const gmst = satLib.gstime(now);

            // Sync selected station from 2D
            if (state?.selectedStation) {
                this.selectedStation = state.selectedStation;
            }

            let obsGd = null;
            let obsPos3D = null;
            if (this.selectedStation && Number.isFinite(this.selectedStation.lat) && Number.isFinite(this.selectedStation.lon)) {
                obsGd = {
                    latitude: this.selectedStation.lat * (Math.PI / 180.0),
                    longitude: this.selectedStation.lon * (Math.PI / 180.0),
                    height: 0.05,
                };
                obsPos3D = Math3D.latLonAltToVector3(this.selectedStation.lat, this.selectedStation.lon, 2);
            }

            // Rebuild laser beams only once per second (GPU friendly)
            this.beamsGroup.clear();
            const THREE = window.THREE;

            this.satelliteMeshes.forEach(({ mesh, satItem }, key) => {
                const isEnabled = this.globe.filters[satItem.constellationKey] !== false;
                if (!isEnabled) {
                    mesh.visible = false;
                    return;
                }

                try {
                    const posVel = satLib.propagate(satItem.satrec, now);
                    if (!posVel || !posVel.position) {
                        mesh.visible = false;
                        return;
                    }

                    const gd = satLib.eciToGeodetic(posVel.position, gmst);
                    const lat = satLib.degreesLat(gd.latitude);
                    const lon = satLib.degreesLong(gd.longitude);
                    const altKm = gd.height;

                    const pos3D = Math3D.latLonAltToVector3(lat, lon, altKm);
                    mesh.position.copy(pos3D);
                    mesh.visible = true;

                    // Look angles
                    let isVisible = false;
                    let elDeg = null;
                    let azDeg = null;

                    if (obsGd) {
                        const posEcf = satLib.eciToEcf(posVel.position, gmst);
                        const look = satLib.ecfToLookAngles(obsGd, posEcf);
                        azDeg = ((look.azimuth * (180.0 / Math.PI)) + 360) % 360;
                        elDeg = look.elevation * (180.0 / Math.PI);
                        isVisible = elDeg > 5.0;
                    }

                    mesh.userData.lastCalculated = { lat, lon, altKm, elDeg, azDeg };

                    // Laser beam to visible satellite (clean & uncluttered)
                    const showAllBeams = this.globe.filters?.showBeams === true;
                    const isTargetSat = this.selectedSatellite?.key === key;

                    if (isVisible && obsPos3D && this.beamsGroup && (isTargetSat || showAllBeams)) {
                        const cfg = CONSTELLATIONS[satItem.constellationKey];
                        const rayGeo = new THREE.BufferGeometry().setFromPoints([obsPos3D, pos3D]);
                        const rayMat = new THREE.LineBasicMaterial({
                            color: isTargetSat ? 0x00f2ff : cfg.color,
                            transparent: true,
                            opacity: isTargetSat ? 0.9 : 0.12,
                            blending: THREE.AdditiveBlending,
                        });
                        this.beamsGroup.add(new THREE.Line(rayGeo, rayMat));

                        // 3D IPP Point at 350km
                        const ippPos = Math3D.calculateIpp3D(obsPos3D, pos3D);
                        if (ippPos && (isTargetSat || showAllBeams)) {
                            const ippGeo = new THREE.SphereGeometry(isTargetSat ? 0.08 : 0.035, 8, 8);
                            const ippMat = new THREE.MeshBasicMaterial({
                                color: isTargetSat ? 0x00f2ff : cfg.color,
                                transparent: true,
                                opacity: isTargetSat ? 0.95 : 0.35,
                            });
                            const ippDot = new THREE.Mesh(ippGeo, ippMat);
                            ippDot.position.copy(ippPos);
                            this.beamsGroup.add(ippDot);
                        }
                    }

                    // Update live values if this satellite is currently opened in card
                    if (this.selectedSatellite?.key === key && this.detailCardEl?.style.display === 'block') {
                        const latEl = document.getElementById('card-sat-lat');
                        const lonEl = document.getElementById('card-sat-lon');
                        const altEl = document.getElementById('card-sat-alt');
                        if (latEl && Number.isFinite(lat)) latEl.innerText = `${lat.toFixed(4)}°`;
                        if (lonEl && Number.isFinite(lon)) lonEl.innerText = `${lon.toFixed(4)}°`;
                        if (altEl && Number.isFinite(altKm)) altEl.innerText = `${Math.round(altKm).toLocaleString()} km`;
                    }
                } catch (e) {
                    // ignore calculation error
                }
            });
        }

        // Render CORS Stations as minimalist dots on Earth surface
        updateStations(stations = [], selectedStation = null) {
            this.selectedStation = selectedStation;
            if (Array.isArray(stations) && stations.length > 0) {
                this.stations = stations;
            }
            const stationList = (Array.isArray(stations) && stations.length > 0) ? stations : (this.stations || []);

            const THREE = window.THREE;
            const Math3D = window.Globe3DMath;
            if (!THREE || !Math3D || !this.stationsGroup) return;

            this.stationsGroup.clear();

            // Minimalist dot geometries (clean pinpoint markers)
            const dotGeo = new THREE.SphereGeometry(0.024, 12, 12);
            const ringGeo = new THREE.RingGeometry(0.034, 0.048, 16);
            const hitGeo = new THREE.SphereGeometry(0.14, 6, 6);
            const hitMat = new THREE.MeshBasicMaterial({ visible: false });

            stationList.forEach(item => {
                const coords = item.coords || (item.device && item.device.latitude && { lat: Number(item.device.latitude), lon: Number(item.device.longitude) });
                if (!coords || !Number.isFinite(coords.lat) || !Number.isFinite(coords.lon)) return;

                const isOnline = item.status === 'online' || item.status === 'streaming';
                const isSelected = selectedStation && (selectedStation.serial === item.device?.serial || selectedStation.name === item.device?.name);
                const color = isSelected ? 0x00f2ff : isOnline ? 0x10b981 : 0x64748b;

                const pos = Math3D.latLonAltToVector3(coords.lat, coords.lon, 2);
                const stnGroup = new THREE.Group();
                stnGroup.position.copy(pos);

                const normal = pos.clone().normalize();
                const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal);
                stnGroup.quaternion.copy(quat);

                // Small dot on surface
                const dotMat = new THREE.MeshStandardMaterial({
                    color,
                    emissive: color,
                    emissiveIntensity: isSelected ? 1.4 : isOnline ? 0.9 : 0.2,
                });
                stnGroup.add(new THREE.Mesh(dotGeo, dotMat));

                // Small ground ring
                const ringMat = new THREE.MeshBasicMaterial({
                    color,
                    side: THREE.DoubleSide,
                    transparent: true,
                    opacity: isSelected ? 0.9 : 0.4,
                });
                const ring = new THREE.Mesh(ringGeo, ringMat);
                ring.rotation.x = Math.PI / 2;
                stnGroup.add(ring);

                // Invisible larger hit area for effortless clicking
                stnGroup.add(new THREE.Mesh(hitGeo, hitMat));

                stnGroup.userData = { type: 'station', station: item, coords };
                this.stationsGroup.add(stnGroup);
            });
        }

        // Highlight single satellite orbit line on demand
        showSatelliteOrbit(satItem) {
            const THREE = window.THREE;
            const satLib = window.satellite;
            const Math3D = window.Globe3DMath;
            if (!THREE || !satLib || !Math3D) return;

            this.singleOrbitGroup.clear();
            this.singleOrbitGroup.visible = true;
            if (!satItem) return;

            const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
            const now = new Date();
            const sampleCount = 100;
            const periodMins = satItem.constellationKey === 'beidou' && satItem.code && (satItem.code.startsWith('C01') || satItem.code.startsWith('C02') || satItem.code.startsWith('C03')) ? 1440 : 720;
            const dtMins = periodMins / sampleCount;

            const points = [];
            for (let i = 0; i <= sampleCount; i++) {
                const sampleTime = new Date(now.getTime() + (i * dtMins * 60 * 1000));
                const gmst = satLib.gstime(sampleTime);
                const posVel = satLib.propagate(satItem.satrec, sampleTime);
                if (posVel && posVel.position) {
                    const gd = satLib.eciToGeodetic(posVel.position, gmst);
                    const lat = satLib.degreesLat(gd.latitude);
                    const lon = satLib.degreesLong(gd.longitude);
                    const altKm = gd.height;
                    points.push(Math3D.latLonAltToVector3(lat, lon, altKm));
                }
            }

            if (points.length >= sampleCount * 0.8) {
                const lineGeo = new THREE.BufferGeometry().setFromPoints(points);
                const lineMat = new THREE.LineBasicMaterial({
                    color: cfg.color,
                    transparent: true,
                    opacity: 0.9,
                    blending: THREE.AdditiveBlending,
                });
                this.singleOrbitGroup.add(new THREE.LineLoop(lineGeo, lineMat));
            }
        }

        // Open persistent Satellite Detail Card
        openSatelliteCard(satItem, meshObj) {
            if (!this.detailCardEl) return;
            this.selectedSatellite = satItem;
            this.showSatelliteOrbit(satItem);

            const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
            const pos = meshObj.userData?.lastCalculated || {};

            const latStr = Number.isFinite(pos.lat) ? `${pos.lat.toFixed(4)}°` : '--';
            const lonStr = Number.isFinite(pos.lon) ? `${pos.lon.toFixed(4)}°` : '--';
            const altStr = Number.isFinite(pos.altKm) ? `${Math.round(pos.altKm).toLocaleString()} km` : `${cfg.nominalAltKm} km`;
            const elStr = Number.isFinite(pos.elDeg) ? `${pos.elDeg.toFixed(1)}°` : '--';
            const azStr = Number.isFinite(pos.azDeg) ? `${pos.azDeg.toFixed(1)}°` : '--';
            const isVisible = Number.isFinite(pos.elDeg) && pos.elDeg > 5.0;

            const rKm = 6371.0 + (pos.altKm || cfg.nominalAltKm);
            const speedKmh = Math.round(Math.sqrt(398600.4418 / rKm) * 3600);
            const speedKms = (Math.sqrt(398600.4418 / rKm)).toFixed(2);

            this.detailCardEl.innerHTML = `
                <div class="card-top-row">
                    <div class="card-title" style="color:${cfg.hex}">
                        <span>${cfg.flag}</span>
                        <span><b>${satItem.code || satItem.name}</b> (${cfg.name})</span>
                    </div>
                    <button type="button" class="card-close-btn" id="globe-card-close" aria-label="Đóng">✕</button>
                </div>
                <div class="stat-grid">
                    <div class="stat-box">
                        <span class="stat-label">Vĩ độ (Lat)</span>
                        <span class="stat-value" id="card-sat-lat">${latStr}</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Kinh độ (Lon)</span>
                        <span class="stat-value" id="card-sat-lon">${lonStr}</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Độ cao quỹ đạo</span>
                        <span class="stat-value" id="card-sat-alt">${altStr}</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Vận tốc bay</span>
                        <span class="stat-value">${speedKms} km/s <span style="font-size:0.65rem;color:#94a3b8">(${speedKmh.toLocaleString()} km/h)</span></span>
                    </div>
                    ${this.selectedStation ? `
                        <div class="stat-box">
                            <span class="stat-label">Góc ngẩng (El)</span>
                            <span class="stat-value" style="color:${isVisible ? '#22c55e' : '#94a3b8'}">${elStr}</span>
                        </div>
                        <div class="stat-box">
                            <span class="stat-label">Phương vị (Az)</span>
                            <span class="stat-value">${azStr}</span>
                        </div>
                    ` : ''}
                </div>
                <div class="card-footer-actions">
                    <button type="button" class="card-action-btn" id="globe-track-sat-btn">
                        <span>🎯</span> <span>${this.globe.isTrackingSatellite ? 'Hủy bám' : 'Bám theo vệ tinh'}</span>
                    </button>
                    <button type="button" class="card-action-btn" id="globe-reset-view-btn">
                        <span>📍</span> <span>Tâm Việt Nam</span>
                    </button>
                </div>
            `;

            this.detailCardEl.style.display = 'block';

            document.getElementById('globe-card-close').onclick = () => this.closeDetailCard();
            document.getElementById('globe-track-sat-btn').onclick = () => {
                this.globe.isTrackingSatellite = !this.globe.isTrackingSatellite;
                document.getElementById('globe-track-sat-btn').innerHTML = `<span>🎯</span> <span>${this.globe.isTrackingSatellite ? 'Hủy bám' : 'Bám theo vệ tinh'}</span>`;
            };
            document.getElementById('globe-reset-view-btn').onclick = () => this.globe.focusVietnam(true);
        }

        // Open persistent Station Detail Card
        openStationCard(stationItem, coords) {
            if (!this.detailCardEl) return;
            const dev = stationItem.device || {};
            const isOnline = stationItem.status === 'online' || stationItem.status === 'streaming';
            const statusLabel = isOnline ? 'Trực tuyến' : stationItem.status || 'Offline';
            const statusColor = isOnline ? '#10b981' : '#94a3b8';

            let ionoTec = '--';
            if (window.IonosphereOverlay?.sample && coords) {
                const sample = window.IonosphereOverlay.sample(coords.lat, coords.lon);
                if (sample && sample.tec !== undefined) ionoTec = `${sample.tec.toFixed(1)} TECU`;
            }

            this.detailCardEl.innerHTML = `
                <div class="card-top-row">
                    <div class="card-title" style="color:#00f2ff">
                        <span>📡</span>
                        <span><b>${dev.name || dev.serial || 'CORS Station'}</b></span>
                    </div>
                    <button type="button" class="card-close-btn" id="globe-card-close" aria-label="Đóng">✕</button>
                </div>
                <div class="stat-grid">
                    <div class="stat-box">
                        <span class="stat-label">Mã / Serial</span>
                        <span class="stat-value">${dev.serial || '--'}</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Trạng thái</span>
                        <span class="stat-value" style="color:${statusColor}">${statusLabel}</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Tọa độ Lat/Lon</span>
                        <span class="stat-value">${coords.lat.toFixed(4)}°, ${coords.lon.toFixed(4)}°</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Local VTEC điện ly</span>
                        <span class="stat-value" style="color:#38bdf8">${ionoTec}</span>
                    </div>
                </div>
                <div style="font-size:0.75rem; color:#38bdf8; margin-bottom:10px;">
                    ⚡ Đã nối tia sóng 3D tới các vệ tinh khả kiến & điểm IPP (350km).
                </div>
                <div class="card-footer-actions">
                    <button type="button" class="card-action-btn" id="globe-reset-view-btn">
                        <span>📍</span> <span>Tâm Việt Nam</span>
                    </button>
                </div>
            `;

            this.detailCardEl.style.display = 'block';
            document.getElementById('globe-card-close').onclick = () => this.closeDetailCard();
            document.getElementById('globe-reset-view-btn').onclick = () => this.globe.focusVietnam(true);
        }

        closeDetailCard() {
            if (this.detailCardEl) this.detailCardEl.style.display = 'none';
            this.selectedSatellite = null;
            this.globe.isTrackingSatellite = false;
            this.singleOrbitGroup.clear();
        }

        destroy() {
            if (this.unsubscribeOverlay) {
                this.unsubscribeOverlay();
                this.unsubscribeOverlay = null;
            }
            if (this.detailCardEl?.parentNode) {
                this.detailCardEl.parentNode.removeChild(this.detailCardEl);
                this.detailCardEl = null;
            }
        }
    }

    window.Globe3DSatelliteLayer = Globe3DSatelliteLayer;
})();
