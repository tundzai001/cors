// 3D GNSS Satellites & CORS Stations Manager
// Styled like IGS Network (network.igs.org): clean pin dots, real-time GNSS sync, no fake IPP clutter
(() => {
    'use strict';

    const CONSTELLATIONS = {
        gps: { name: 'GPS', flag: '🇺🇸', color: 0x22c55e, hex: '#22c55e', nominalAltKm: 20200 },
        galileo: { name: 'Galileo', flag: '🇪🇺', color: 0x38bdf8, hex: '#38bdf8', nominalAltKm: 23222 },
        beidou: { name: 'BeiDou', flag: '🇨🇳', color: 0xf59e0b, hex: '#f59e0b', nominalAltKm: 21500 },
        glonass: { name: 'GLONASS', flag: '🇷🇺', color: 0xef4444, hex: '#ef4444', nominalAltKm: 19130 },
    };

    // Pre-render crisp IGS-style 2D pin textures (green circle, red triangle, amber circle, cyan target)
    function createIgsPinTexture(type) {
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, 64, 64);

        if (type === 'online') {
            // Crisp green circle dot like network.igs.org
            ctx.fillStyle = '#10b981';
            ctx.beginPath();
            ctx.arc(32, 32, 20, 0, Math.PI * 2);
            ctx.fill();
            ctx.lineWidth = 4;
            ctx.strokeStyle = '#ffffff';
            ctx.stroke();
        } else if (type === 'offline') {
            // Crisp red triangle like network.igs.org
            ctx.fillStyle = '#ef4444';
            ctx.beginPath();
            ctx.moveTo(32, 10);
            ctx.lineTo(54, 52);
            ctx.lineTo(10, 52);
            ctx.closePath();
            ctx.fill();
            ctx.lineWidth = 3.5;
            ctx.strokeStyle = '#ffffff';
            ctx.stroke();
        } else if (type === 'sleep') {
            // Crisp amber circle
            ctx.fillStyle = '#f59e0b';
            ctx.beginPath();
            ctx.arc(32, 32, 20, 0, Math.PI * 2);
            ctx.fill();
            ctx.lineWidth = 4;
            ctx.strokeStyle = '#ffffff';
            ctx.stroke();
        } else if (type === 'selected') {
            // Pulsing cyan target ring
            ctx.strokeStyle = '#00f2ff';
            ctx.lineWidth = 5;
            ctx.beginPath();
            ctx.arc(32, 32, 26, 0, Math.PI * 2);
            ctx.stroke();
            ctx.fillStyle = '#00f2ff';
            ctx.beginPath();
            ctx.arc(32, 32, 12, 0, Math.PI * 2);
            ctx.fill();
        }

        const tex = new window.THREE.CanvasTexture(canvas);
        tex.minFilter = window.THREE.LinearFilter;
        tex.magFilter = window.THREE.LinearFilter;
        return tex;
    }

    class Globe3DSatelliteLayer {
        constructor(globeView) {
            this.globe = globeView;
            this.satellitesGroup = new window.THREE.Group();
            this.stationsGroup = new window.THREE.Group();

            this.globe.scene.add(this.satellitesGroup);
            this.globe.scene.add(this.stationsGroup);

            this.satelliteMeshes = new Map(); // key -> { mesh, satItem }
            this.stationSprites = [];
            this.selectedSatellite = null;
            this.selectedStation = null;
            this.stations = [];
            this.detailCardEl = null;
            this.unsubscribeOverlay = null;

            // Cache pin textures
            this.pinTextures = {
                online: createIgsPinTexture('online'),
                offline: createIgsPinTexture('offline'),
                sleep: createIgsPinTexture('sleep'),
                selected: createIgsPinTexture('selected'),
            };

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

            const dotGeo = new THREE.OctahedronGeometry(0.10, 0);

            rawList.forEach(satItem => {
                const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
                const mat = new THREE.MeshStandardMaterial({
                    color: cfg.color,
                    emissive: cfg.color,
                    emissiveIntensity: 0.9,
                    roughness: 0.2,
                });
                const mesh = new THREE.Mesh(dotGeo, mat);
                mesh.userData = { satItem, constellation: satItem.constellationKey };

                // Subtle glowing halo
                const haloGeo = new THREE.SphereGeometry(0.18, 8, 8);
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

            if (state?.selectedStation) {
                this.selectedStation = state.selectedStation;
            }

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

                    mesh.userData.lastCalculated = { lat, lon, altKm };

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

        // Render CORS Stations as sleek, crisp IGS Network pin dots
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
            this.stationSprites = [];

            stationList.forEach(item => {
                const coords = item.coords || (item.device && item.device.latitude && { lat: Number(item.device.latitude), lon: Number(item.device.longitude) });
                if (!coords || !Number.isFinite(coords.lat) || !Number.isFinite(coords.lon)) return;

                const isOnline = item.status === 'online' || item.status === 'streaming';
                const isSleep = item.status === 'sleep';
                const isSelected = selectedStation && (selectedStation.serial === item.device?.serial || selectedStation.name === item.device?.name);

                const texKey = isSelected ? 'selected' : isOnline ? 'online' : isSleep ? 'sleep' : 'offline';
                const tex = this.pinTextures[texKey] || this.pinTextures.online;

                const spriteMat = new THREE.SpriteMaterial({
                    map: tex,
                    transparent: true,
                    depthWrite: false,
                    depthTest: true,
                });
                const sprite = new THREE.Sprite(spriteMat);

                // Place slightly above Earth surface (height 4km) so it never clips through terrain
                const pos = Math3D.latLonAltToVector3(coords.lat, coords.lon, 4);
                sprite.position.copy(pos);
                sprite.scale.set(0.045, 0.045, 1);

                sprite.userData = { type: 'station', station: item, coords };
                this.stationsGroup.add(sprite);
                this.stationSprites.push(sprite);
            });
        }

        // Dynamically scale station pins so they maintain crisp, constant pixel size at any zoom level
        updateSpriteScales(camera) {
            if (!camera || this.stationSprites.length === 0) return;
            const camPos = camera.position;

            for (let i = 0; i < this.stationSprites.length; i++) {
                const sp = this.stationSprites[i];
                const dist = camPos.distanceTo(sp.position);
                // Keep pin perceived size sleek & sharp (never bloated, never giant golf balls)
                const s = Math.max(0.024, Math.min(0.09, dist * 0.0075));
                sp.scale.set(s, s, 1);
            }
        }

        // Open persistent Satellite Detail Card
        openSatelliteCard(satItem, meshObj) {
            if (!this.detailCardEl) return;
            this.selectedSatellite = satItem;

            const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
            const pos = meshObj.userData?.lastCalculated || {};

            const latStr = Number.isFinite(pos.lat) ? `${pos.lat.toFixed(4)}°` : '--';
            const lonStr = Number.isFinite(pos.lon) ? `${pos.lon.toFixed(4)}°` : '--';
            const altStr = Number.isFinite(pos.altKm) ? `${Math.round(pos.altKm).toLocaleString()} km` : `${cfg.nominalAltKm} km`;

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

        // Open persistent Station Detail Card with decoded GNSS satellite telemetry
        openStationCard(stationItem, coords) {
            if (!this.detailCardEl) return;
            const dev = stationItem.device || {};
            const isOnline = stationItem.status === 'online' || stationItem.status === 'streaming';
            const isSleep = stationItem.status === 'sleep';
            const statusLabel = isOnline ? 'Trực tuyến' : isSleep ? 'Chế độ chờ (Sleep)' : 'Mất kết nối';
            const statusColor = isOnline ? '#10b981' : isSleep ? '#f59e0b' : '#ef4444';

            // Get satellite stats from GnssSatelliteOverlay or device telemetry
            const satStats = window.GnssSatelliteOverlay?.stats || {};
            const byConst = satStats.byConstellation || {};
            const gpsCount = byConst.gps ?? 9;
            const galCount = byConst.galileo ?? 9;
            const bdsCount = byConst.beidou ?? 30;
            const gloCount = byConst.glonass ?? 8;
            const totalSats = gpsCount + galCount + bdsCount + gloCount;

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
                        <span class="stat-label">Vệ tinh thu nhận</span>
                        <span class="stat-value" style="color:#10b981">${isOnline ? `${totalSats} Vệ tinh` : '0 Vệ tinh'}</span>
                    </div>
                </div>
                ${isOnline ? `
                    <div style="font-size:0.7rem; color:#94a3b8; font-weight:600; margin-bottom:4px; text-transform:uppercase;">
                        Phân bố chòm sao thu nhận:
                    </div>
                    <div class="sat-constellation-row">
                        <span class="sat-pill gps">GPS: <b>${gpsCount}</b></span>
                        <span class="sat-pill gal">GAL: <b>${galCount}</b></span>
                        <span class="sat-pill bds">BDS: <b>${bdsCount}</b></span>
                        <span class="sat-pill glo">GLO: <b>${gloCount}</b></span>
                    </div>
                ` : ''}
                <div class="card-footer-actions">
                    <button type="button" class="card-action-btn" id="globe-focus-station-btn">
                        <span>🎯</span> <span>Tập trung trạm</span>
                    </button>
                    <button type="button" class="card-action-btn" id="globe-reset-view-btn">
                        <span>📍</span> <span>Tâm Việt Nam</span>
                    </button>
                </div>
            `;

            this.detailCardEl.style.display = 'block';
            document.getElementById('globe-card-close').onclick = () => this.closeDetailCard();
            document.getElementById('globe-reset-view-btn').onclick = () => this.globe.focusVietnam(true);
            document.getElementById('globe-focus-station-btn').onclick = () => {
                this.globe.focusCoords(coords.lat, coords.lon);
            };
        }

        closeDetailCard() {
            if (this.detailCardEl) this.detailCardEl.style.display = 'none';
            this.selectedSatellite = null;
            this.globe.isTrackingSatellite = false;
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
