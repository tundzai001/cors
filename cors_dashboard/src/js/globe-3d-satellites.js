// 3D GNSS Satellites & CORS Stations Manager
// Styled like satellitemap.space & network.igs.org:
// - Luminous glowing star-like satellite particles (GPS, Galileo, BeiDou, GLONASS)
// - True Keplerian 3D orbital ellipses (GEO equatorial rings, IGSO tilted loops, MEO tracks)
// - Real-time station satellite tracking: calculates & draws live reception signal rays to visible satellites
// - Dynamic ground footprint coverage on Earth & 3D floating tag beside selected satellite
// - Crisp IGS Network station pins with adaptive zoom scaling
(() => {
    'use strict';

    const CONSTELLATIONS = {
        gps: { name: 'GPS', flag: '🇺🇸', color: 0x22c55e, hex: '#22c55e', nominalAltKm: 20200 },
        galileo: { name: 'Galileo', flag: '🇪🇺', color: 0x38bdf8, hex: '#38bdf8', nominalAltKm: 23222 },
        beidou: { name: 'BeiDou', flag: '🇨🇳', color: 0xf59e0b, hex: '#f59e0b', nominalAltKm: 21500 },
        glonass: { name: 'GLONASS', flag: '🇷🇺', color: 0xef4444, hex: '#ef4444', nominalAltKm: 19130 },
        qzss: { name: 'QZSS', flag: '🇯🇵', color: 0xa855f7, hex: '#a855f7', nominalAltKm: 35786 },
    };

    const formatVietnamTime = (date, includeDate = false) => {
        if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return '--';
        const options = {
            timeZone: 'Asia/Ho_Chi_Minh',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
        };
        if (includeDate) Object.assign(options, { day: '2-digit', month: '2-digit', year: 'numeric' });
        return new Intl.DateTimeFormat('vi-VN', options).format(date);
    };

    const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

    const satelliteLabel = satellite => window.GnssSatelliteOverlay?.getSatelliteLabel?.(satellite) || satellite.code || satellite.name || '';

    function disposeGroup(group) {
        group.traverse(child => {
            if (!child.isSprite) child.geometry?.dispose();
            if (child.userData?.ownsMap) child.material?.map?.dispose();
            if (Array.isArray(child.material)) child.material.forEach(material => material.dispose());
            else child.material?.dispose();
        });
        group.clear();
    }

    // Pre-render luminous star particle texture for satellite dots (satellitemap.space particle look)
    function createSatelliteTexture(colorHex, code) {
        const canvas = document.createElement('canvas');
        canvas.width = 128;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');

        const grad = ctx.createRadialGradient(22, 32, 2, 22, 32, 18);
        grad.addColorStop(0, '#ffffff');
        grad.addColorStop(0.20, colorHex);
        grad.addColorStop(0.55, colorHex);
        grad.addColorStop(1.0, 'rgba(0, 0, 0, 0)');

        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(22, 32, 18, 0, Math.PI * 2);
        ctx.fill();
        ctx.font = 'bold 24px system-ui, sans-serif';
        ctx.textBaseline = 'middle';
        ctx.strokeStyle = '#020611';
        ctx.lineWidth = 5;
        ctx.strokeText(code || '', 46, 33);
        ctx.fillStyle = '#f8fafc';
        ctx.fillText(code || '', 46, 33);

        const tex = new window.THREE.CanvasTexture(canvas);
        tex.minFilter = window.THREE.LinearFilter;
        tex.magFilter = window.THREE.LinearFilter;
        return tex;
    }

    // Pre-render crisp IGS-style 2D pin textures (green circle, red triangle, amber circle, cyan target)
    function createIgsPinTexture(type) {
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, 64, 64);

        if (type === 'online') {
            ctx.fillStyle = '#10b981';
            ctx.beginPath();
            ctx.arc(32, 32, 20, 0, Math.PI * 2);
            ctx.fill();
            ctx.lineWidth = 4;
            ctx.strokeStyle = '#ffffff';
            ctx.stroke();
        } else if (type === 'offline') {
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
            ctx.fillStyle = '#f59e0b';
            ctx.beginPath();
            ctx.arc(32, 32, 20, 0, Math.PI * 2);
            ctx.fill();
            ctx.lineWidth = 4;
            ctx.strokeStyle = '#ffffff';
            ctx.stroke();
        } else if (type === 'selected') {
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

    // Determine authentic orbital parameters & classification (GEO, IGSO, MEO)
    function getOrbitTypeInfo(satItem) {
        if (!satItem || !satItem.satrec) {
            return { type: 'MEO', label: 'MEO (Quỹ đạo tầm trung)', periodHours: 12.0, periodMins: 720, incDeg: 55 };
        }
        const incDeg = satItem.satrec.inclo ? (satItem.satrec.inclo * 180 / Math.PI) : 55;
        const periodMins = (satItem.satrec.no && satItem.satrec.no > 0)
            ? (2 * Math.PI / satItem.satrec.no)
            : 720;
        const periodHours = periodMins / 60;

        let type = 'MEO';
        let label = 'MEO (Quỹ đạo tầm trung)';

        if (periodHours > 20) {
            if (incDeg < 10) {
                type = 'GEO';
                label = 'GEO (Quỹ đạo địa tĩnh xích đạo)';
            } else {
                type = 'IGSO';
                label = 'IGSO (Quỹ đạo đồng bộ nghiêng)';
            }
        } else if (satItem.constellationKey === 'gps') {
            label = 'MEO (Quỹ đạo GPS)';
        } else if (satItem.constellationKey === 'galileo') {
            label = 'MEO (Quỹ đạo Galileo)';
        } else if (satItem.constellationKey === 'glonass') {
            label = 'MEO (Quỹ đạo GLONASS)';
        }

        return { type, label, periodHours, periodMins, incDeg };
    }

    // Create ground footprint circle on Earth's surface under the satellite
    function createFootprintLoop(centerPos3D, altKm, colorHex) {
        const THREE = window.THREE;
        if (!THREE || !centerPos3D) return null;

        const n = centerPos3D.clone().normalize();
        const arb = Math.abs(n.y) < 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0);
        const u = n.clone().cross(arb).normalize();
        const v = n.clone().cross(u).normalize();

        const rEarthKm = 6371.0;
        const validAlt = Math.max(1000, Number.isFinite(altKm) ? altKm : 20200);
        const psiRad = Math.acos(rEarthKm / (rEarthKm + validAlt));
        const cosPsi = Math.cos(psiRad);
        const sinPsi = Math.sin(psiRad);

        const sceneR = 5.015; // Placed slightly above Earth sphere to avoid z-fighting
        const segments = 64;
        const pts = [];

        for (let i = 0; i <= segments; i++) {
            const theta = (i / segments) * Math.PI * 2;
            const cosTheta = Math.cos(theta);
            const sinTheta = Math.sin(theta);

            const x = sceneR * (cosPsi * n.x + sinPsi * (cosTheta * u.x + sinTheta * v.x));
            const y = sceneR * (cosPsi * n.y + sinPsi * (cosTheta * u.y + sinTheta * v.y));
            const z = sceneR * (cosPsi * n.z + sinPsi * (cosTheta * u.z + sinTheta * v.z));
            pts.push(new THREE.Vector3(x, y, z));
        }

        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        const mat = new THREE.LineBasicMaterial({
            color: colorHex || 0x38bdf8,
            transparent: true,
            opacity: 0.42,
            blending: THREE.AdditiveBlending,
        });
        return new THREE.LineLoop(geo, mat);
    }

    class Globe3DSatelliteLayer {
        constructor(globeView) {
            this.globe = globeView;
            this.satellitesGroup = new window.THREE.Group();
            this.stationsGroup = new window.THREE.Group();
            this.singleOrbitGroup = new window.THREE.Group();
            this.stationTrackingGroup = new window.THREE.Group();

            this.globe.scene.add(this.satellitesGroup);
            this.globe.scene.add(this.stationsGroup);
            this.globe.scene.add(this.singleOrbitGroup);
            this.globe.scene.add(this.stationTrackingGroup);

            this.satelliteMeshes = new Map(); // key -> { mesh (group), sprite, satItem }
            this.stationSprites = [];
            this.selectedSatellite = null;
            this.selectedStation = null;
            this.stationCard = null;
            this.stationCardMode = null;
            this.stationCardRtcmSignature = null;
            this.restoreSelectedStationCard = false;
            this.footprintMesh = null;
            this.stations = [];
            this.detailCardEl = null;
            this.unsubscribeOverlay = null;
            this.active = false;
            this.tickTimer = null;
            this.catalog = null;
            this.loading = false;
            this.lastCatalogFetch = 0;

            // Cache textures
            this.pinTextures = {
                online: createIgsPinTexture('online'),
                offline: createIgsPinTexture('offline'),
                sleep: createIgsPinTexture('sleep'),
                selected: createIgsPinTexture('selected'),
            };

            this._initDetailCard();
            this.selectedLabelEl = document.createElement('div');
            this.selectedLabelEl.className = 'globe-selected-label';
            this.selectedLabelEl.style.display = 'none';
            this.statusEl = document.createElement('div');
            this.statusEl.className = 'globe-data-status';
            this.statusEl.setAttribute('role', 'status');
            this.globe.container.appendChild(this.statusEl);
        }

        _initDetailCard() {
            this.detailCardEl = document.createElement('div');
            this.detailCardEl.className = 'globe-detail-card';
            this.detailCardEl.style.display = 'none';
            this.globe.container.appendChild(this.detailCardEl);
        }

        start() {
            this.stop();
            this.active = true;
            this.globe.container.appendChild(this.detailCardEl);
            this.globe.container.appendChild(this.statusEl);
            this.globe.container.appendChild(this.selectedLabelEl);
            const mgr = window.GnssSatelliteOverlay;
            this.unsubscribeOverlay = mgr?.subscribe(() => this._syncCatalog());
            this._syncCatalog();
            if (!mgr?.rawSatellites?.length) this.loadCatalog();
            this.tickTimer = setInterval(() => {
                if (document.hidden || !this.active) return;
                this._syncCatalog();
                this.onOverlayTick();
                if (Date.now() - this.lastCatalogFetch > 3600000) this.loadCatalog();
            }, 1000);
        }

        stop() {
            this.active = false;
            if (this.tickTimer) clearInterval(this.tickTimer);
            this.tickTimer = null;
            this.unsubscribeOverlay?.();
            this.unsubscribeOverlay = null;
        }

        async loadCatalog() {
            if (this.loading || !this.active) return;
            this.loading = true;
            this.lastCatalogFetch = Date.now();
            this.statusEl.textContent = 'Đang tải quỹ đạo GNSS…';
            try {
                await window.GnssSatelliteOverlay?.fetchTle();
                if (!this.active) return;
                this._syncCatalog();
                if (!window.GnssSatelliteOverlay?.rawSatellites?.length) {
                    this.statusEl.textContent = 'Chưa tải được quỹ đạo GNSS. ';
                    const retry = document.createElement('button');
                    retry.type = 'button';
                    retry.textContent = 'Thử lại';
                    retry.onclick = () => this.loadCatalog();
                    this.statusEl.appendChild(retry);
                }
            } finally {
                this.loading = false;
            }
        }

        _syncCatalog() {
            if (!this.active || document.hidden) return;
            const rawList = window.GnssSatelliteOverlay?.rawSatellites;
            if (!rawList?.length || rawList === this.catalog) return;
            this.closeDetailCard();
            this._buildSatelliteMeshes(rawList);
            this.catalog = rawList;
            this.onOverlayTick();
            if (this.restoreSelectedStationCard) this.showSelectedStationCard();
        }

        _buildSatelliteMeshes(rawList) {
            const THREE = window.THREE;
            if (!THREE || !Array.isArray(rawList) || rawList.length === 0) return;

            disposeGroup(this.satellitesGroup);
            this.satelliteMeshes.clear();
            disposeGroup(this.singleOrbitGroup);
            disposeGroup(this.stationTrackingGroup);

            rawList.forEach(satItem => {
                const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
                const group = new THREE.Group();
                group.userData = { satItem, constellation: satItem.constellationKey, type: 'satellite' };

                // Luminous star sprite (satellitemap.space particle look)
                const spriteMat = new THREE.SpriteMaterial({
                    map: createSatelliteTexture(cfg.hex, satelliteLabel(satItem)),
                    transparent: true,
                    depthWrite: false,
                    blending: THREE.AdditiveBlending,
                });
                const sprite = new THREE.Sprite(spriteMat);
                // The luminous point is at x=22 in the 128 px label texture.
                // Keep its actual orbit position and hit target at that point.
                sprite.center.set(22 / 128, 0.5);
                sprite.userData.ownsMap = true;
                sprite.scale.set(0.18, 0.18, 1);
                group.add(sprite);

                // Invisible hit sphere for effortless raycasting click
                const hitGeo = new THREE.SphereGeometry(0.38, 6, 6);
                const hitMat = new THREE.MeshBasicMaterial({ visible: false });
                const hitMesh = new THREE.Mesh(hitGeo, hitMat);
                hitMesh.userData = { satItem, constellation: satItem.constellationKey, type: 'satellite' };
                group.add(hitMesh);

                this.satellitesGroup.add(group);
                this.satelliteMeshes.set(satItem.key, { mesh: group, sprite, hitMesh, satItem });
            });
        }

        // Orbit calculation is independent of the Leaflet layer and runs at 1 Hz.
        onOverlayTick(state) {
            if (!this.active || document.hidden) return;
            const satLib = window.satellite;
            const Math3D = window.Globe3DMath;
            if (!satLib || !Math3D || this.satelliteMeshes.size === 0) return;

            const now = new Date();
            const gmst = satLib.gstime(now);

            if (state?.selectedStation) {
                this.selectedStation = state.selectedStation;
            }

            this.satelliteMeshes.forEach(({ mesh, sprite, satItem }, key) => {
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
                    if (![lat, lon, altKm].every(Number.isFinite)) {
                        mesh.visible = false;
                        return;
                    }
                    const velocity = posVel.velocity;
                    const speedKms = velocity ? Math.hypot(velocity.x, velocity.y, velocity.z) : NaN;

                    const pos3D = Math3D.latLonAltToVector3(lat, lon, altKm);
                    mesh.position.copy(pos3D);
                    mesh.visible = true;

                    mesh.userData.lastCalculated = { lat, lon, altKm, speedKms, timestamp: now };

                    // Update live satellite card and floating label if currently selected
                    if (this.selectedSatellite?.key === key) {
                        if (this.detailCardEl?.style.display === 'block') {
                            const latEl = document.getElementById('card-sat-lat');
                            const lonEl = document.getElementById('card-sat-lon');
                            const altEl = document.getElementById('card-sat-alt');
                            if (latEl && Number.isFinite(lat)) latEl.innerText = `${lat.toFixed(4)}°`;
                            if (lonEl && Number.isFinite(lon)) lonEl.innerText = `${lon.toFixed(4)}°`;
                            if (altEl && Number.isFinite(altKm)) altEl.innerText = `${Math.round(altKm).toLocaleString()} km`;
                            const speedEl = this.detailCardEl.querySelector('#card-sat-speed');
                            if (speedEl) speedEl.textContent = Number.isFinite(speedKms) ? `${speedKms.toFixed(2)} km/s` : '--';
                            const regionEl = this.detailCardEl.querySelector('#card-sat-region');
                            if (regionEl) regionEl.textContent = Math3D.groundRegion(this.globe.countryFeatures, lat, lon);
                            const timeEl = this.detailCardEl.querySelector('#card-sat-time');
                            if (timeEl) timeEl.textContent = `${formatVietnamTime(now)} ICT (UTC+7)`;
                        }
                    }
                } catch (e) {
                    // ignore calculation error
                }
            });
            if (this.stationCardMode === 'station' && this.stationCard && this.detailCardEl?.style.display === 'block') {
                const liveIds = window.GnssSatelliteOverlay?.getLiveRtcmSatellites(this.stationCard.item.device || {})
                    .map(sat => sat.id || `${sat.system}:${sat.prn}`).sort() || [];
                const signature = liveIds.join('|');
                if (signature !== this.stationCardRtcmSignature) {
                    this.stationCardRtcmSignature = signature;
                    this.openStationCard(this.stationCard.item, this.stationCard.coords);
                }
            }
            const count = [...this.satelliteMeshes.values()].filter(record => record.mesh.visible).length;
            const stale = window.GnssSatelliteOverlay?.tleInfo?.is_stale ? ' · Dữ liệu quỹ đạo cũ' : '';
            const rtcmCount = window.GnssSatelliteOverlay?.getLiveRtcmSatellites(this.selectedStation).length || 0;
            this.statusEl.textContent = `${count} vệ tinh · ${rtcmCount ? `${rtcmCount} RTCM đang nhận · ` : ''}SGP4 1Hz · ${formatVietnamTime(now)} ICT (UTC+7)${stale}`;
            this.globe.lastRenderAt = null;
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

            disposeGroup(this.stationsGroup);
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

                const pos = Math3D.latLonAltToVector3(coords.lat, coords.lon, 4);
                sprite.position.copy(pos);
                sprite.scale.set(0.045, 0.045, 1);

                sprite.userData = { type: 'station', station: item, coords };
                this.stationsGroup.add(sprite);
                this.stationSprites.push(sprite);
            });
        }

        showSelectedStationCard() {
            const serial = String(this.selectedStation?.serial || '').trim();
            if (!serial || !this.satelliteMeshes.size) return false;
            const item = (this.stations || []).find(station => String(station.device?.serial || '').trim() === serial);
            const coords = item?.coords || (item?.device && {
                lat: Number(item.device.latitude),
                lon: Number(item.device.longitude),
            });
            if (!item || !Number.isFinite(coords?.lat) || !Number.isFinite(coords?.lon)) return false;
            this.openStationCard(item, coords);
            return true;
        }

        // Dynamically scale station pins & satellite particles so they maintain crisp, star-like appearance
        updateSpriteScales(camera) {
            if (!camera) return;
            const camPos = camera.position;
            const camDist = camPos.length();
            const altAboveGround = Math.max(0.06, camDist - 5.0);

            // CORS Station pins: adapt to zoom so close stations never clump or overlap
            for (let i = 0; i < this.stationSprites.length; i++) {
                const sp = this.stationSprites[i];
                const s = Math.max(0.014, Math.min(0.075, altAboveGround * 0.020));
                sp.scale.set(s, s, 1);
            }

            // Satellite star particles (satellitemap.space style)
            this.satelliteMeshes.forEach(({ sprite, mesh, hitMesh, satItem }) => {
                if (!sprite || !mesh) return;
                const dist = camPos.distanceTo(mesh.position);
                const isSelected = this.selectedSatellite?.key === satItem.key;
                const height = this.globe.container.clientHeight || 800;
                const unitsPerPixel = 2 * Math.tan(camera.fov * Math.PI / 360) * dist / height;
                const scale = isSelected ? 1.15 : 1;
                sprite.scale.set(unitsPerPixel * 48 * scale, unitsPerPixel * 24 * scale, 1);
                hitMesh?.scale.setScalar(Math.max(1, unitsPerPixel * 16 / 0.38));
            });
        }

        updateSelectedLabel() {
            const record = this.satelliteMeshes.get(this.selectedSatellite?.key);
            const label = this.selectedLabelEl;
            if (!record?.mesh.visible || !this.active) {
                label.style.display = 'none';
                return;
            }
            const camera = this.globe.camera;
            const position = record.mesh.position;
            const delta = position.clone().sub(camera.position);
            const distance = delta.length();
            const direction = delta.normalize();
            const b = camera.position.dot(direction);
            const discriminant = b * b - (camera.position.lengthSq() - 25);
            const hit = discriminant >= 0 ? -b - Math.sqrt(discriminant) : -1;
            const point = position.clone().project(camera);
            if ((hit > 0 && hit < distance) || point.z < -1 || point.z > 1 || Math.abs(point.x) > 1 || Math.abs(point.y) > 1) {
                label.style.display = 'none';
                return;
            }
            label.style.display = 'block';
            label.style.left = `${(point.x + 1) * this.globe.container.clientWidth / 2}px`;
            label.style.top = `${(1 - point.y) * this.globe.container.clientHeight / 2}px`;
        }

        // Generate and highlight authentic Keplerian 3D orbital ellipse (satellitemap.space style)
        showSatelliteOrbit(satItem) {
            const THREE = window.THREE;
            const satLib = window.satellite;
            const Math3D = window.Globe3DMath;
            if (!THREE || !satLib || !Math3D) return;

            disposeGroup(this.singleOrbitGroup);
            if (!satItem || !satItem.satrec) return;

            const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
            const now = new Date();
            // Freezing gmstNow for all sample points plots the orbit relative to the globe's current orientation,
            // producing a mathematically closed, smooth 3D ellipse in space!
            const gmstNow = satLib.gstime(now);

            // Orbital period derived from real mean motion (satrec.no is in rad/min)
            const periodMins = (satItem.satrec.no && satItem.satrec.no > 0)
                ? (2 * Math.PI) / satItem.satrec.no
                : 720;

            const sampleCount = 140;
            const dtMins = periodMins / sampleCount;
            const points = [];

            for (let i = 0; i <= sampleCount; i++) {
                const sampleTime = new Date(now.getTime() + (i * dtMins * 60 * 1000));
                const posVel = satLib.propagate(satItem.satrec, sampleTime);
                if (posVel && posVel.position) {
                    const gd = satLib.eciToGeodetic(posVel.position, gmstNow);
                    const lat = satLib.degreesLat(gd.latitude);
                    const lon = satLib.degreesLong(gd.longitude);
                    const altKm = gd.height;
                    points.push(Math3D.latLonAltToVector3(lat, lon, altKm));
                }
            }

            if (points.length >= sampleCount * 0.75) {
                const lineGeo = new THREE.BufferGeometry().setFromPoints(points);

                // Primary glowing ribbon
                const lineMat = new THREE.LineBasicMaterial({
                    color: cfg.color,
                    transparent: true,
                    opacity: 0.95,
                });
                this.singleOrbitGroup.add(new THREE.LineLoop(lineGeo, lineMat));

                // Secondary soft atmospheric halo
                const glowMat = new THREE.LineBasicMaterial({
                    color: cfg.color,
                    transparent: true,
                    opacity: 0.35,
                    blending: THREE.AdditiveBlending,
                });
                this.singleOrbitGroup.add(new THREE.LineLoop(lineGeo, glowMat));
            }
        }

        // Open persistent Satellite Detail Card
        openSatelliteCard(satItem, meshObj) {
            if (!this.detailCardEl) return;
            this.stationCardMode = 'satellite';
            this.stationCard = null;
            this.selectedSatellite = satItem;
            this.globe.isTrackingSatellite = false;

            // Clear station tracking rays
            disposeGroup(this.stationTrackingGroup);

            const satRec = this.satelliteMeshes.get(satItem.key);
            const satGroup = satRec?.mesh || meshObj;
            const currentPos3D = satGroup.position.clone();
            const pos = satGroup.userData?.lastCalculated || meshObj?.userData?.lastCalculated || {};
            const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
            const orbitInfo = getOrbitTypeInfo(satItem);

            // Render single true orbital ellipse
            this.showSatelliteOrbit(satItem);

            this.selectedLabelEl.textContent = `${satelliteLabel(satItem)} · ${satItem.name || ''}`;
            this.selectedLabelEl.style.borderColor = cfg.hex;
            this.globe.lastRenderAt = null;
            if (this.globe.hoverTooltipEl) this.globe.hoverTooltipEl.style.display = 'none';

            // Render ground coverage footprint cone on Earth
            this.footprintMesh = createFootprintLoop(currentPos3D, pos.altKm || cfg.nominalAltKm, cfg.color);
            if (this.footprintMesh) {
                this.singleOrbitGroup.add(this.footprintMesh);
            }

            const latStr = Number.isFinite(pos.lat) ? `${pos.lat.toFixed(4)}°` : '--';
            const lonStr = Number.isFinite(pos.lon) ? `${pos.lon.toFixed(4)}°` : '--';
            const altStr = Number.isFinite(pos.altKm) ? `${Math.round(pos.altKm).toLocaleString()} km` : `${cfg.nominalAltKm} km`;

            const speedKms = Number.isFinite(pos.speedKms) ? `${pos.speedKms.toFixed(2)} km/s` : '--';

            this.detailCardEl.innerHTML = `
                <div class="card-top-row">
                    <div class="card-title" style="color:${cfg.hex}">
                        <span>${cfg.flag}</span>
                        <span><b>${escapeHtml(satelliteLabel(satItem))}</b> • <span style="font-size:0.75rem;opacity:0.85">${orbitInfo.type}</span></span>
                    </div>
                    <button type="button" class="card-close-btn" id="globe-card-close" aria-label="Đóng">✕</button>
                </div>
                <div style="font-size:0.72rem; color:#cbd5e1; margin-bottom:8px; line-height:1.3;">
                    <b>${escapeHtml(satItem.name || satItem.code)}</b> — ${orbitInfo.label} · NORAD ${escapeHtml(satItem.norad_id)}
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
                        <span class="stat-label">Chu kỳ bay</span>
                        <span class="stat-value">${orbitInfo.periodHours.toFixed(2)}h <span style="font-size:0.65rem;color:#94a3b8">(${Math.round(orbitInfo.periodMins)} phút)</span></span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Góc nghiêng (Inc)</span>
                        <span class="stat-value">${orbitInfo.incDeg.toFixed(1)}°</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Vận tốc bay</span>
                        <span class="stat-value" id="card-sat-speed">${speedKms}</span>
                    </div>
                </div>
                <div class="globe-location-info">
                    Đang bay trên: <b id="card-sat-region">${escapeHtml(window.Globe3DMath.groundRegion(this.globe.countryFeatures, pos.lat, pos.lon))}</b><br>
                    Vị trí lúc <span id="card-sat-time">${pos.timestamp instanceof Date ? `${formatVietnamTime(pos.timestamp)} ICT (UTC+7)` : '--'}</span><br>
                    Dữ liệu quỹ đạo: ${escapeHtml(Number.isFinite(satItem.satrec.jdsatepoch) ? `${formatVietnamTime(new Date((satItem.satrec.jdsatepoch - 2440587.5) * 86400000), true)} ICT (UTC+7)` : '--')}
                </div>
                <div class="card-footer-actions">
                    <button type="button" class="card-action-btn" id="globe-track-sat-btn">
                        <span>🎯</span> <span>${this.globe.isTrackingSatellite ? 'Hủy bám' : 'Bám theo vệ tinh'}</span>
                    </button>
                    <button type="button" class="card-action-btn" id="globe-reset-view-btn">
                        <span>📍</span> <span>Tâm Việt Nam</span>
                    </button>
                </div>
                <div style="font-size:0.62rem; color:#64748b; text-align:center; margin-top:8px; border-top:1px dashed rgba(255,255,255,0.1); padding-top:4px;">
                    Nguồn: <a href="https://celestrak.org/NORAD/elements/" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;text-decoration:none;"><b>CelesTrak (NORAD GP)</b> <i class="bi bi-box-arrow-up-right" style="font-size:0.58rem;"></i></a> · Vị trí tính từ quỹ đạo, cập nhật 1 giây
                </div>
            `;

            this.detailCardEl.style.display = 'block';

            document.getElementById('globe-card-close').onclick = () => this.closeDetailCard();
            document.getElementById('globe-track-sat-btn').onclick = () => {
                this.globe.isTrackingSatellite = !this.globe.isTrackingSatellite;
                if (this.globe.isTrackingSatellite) {
                    this.globe.isTweening = false;
                    this.globe.controls.target.copy(satGroup.position);
                    this.globe.camera.position.copy(satGroup.position).add(satGroup.position.clone().normalize().multiplyScalar(8));
                    this.globe.controls.update();
                }
                this.globe.lastRenderAt = null;
                document.getElementById('globe-track-sat-btn').innerHTML = `<span>🎯</span> <span>${this.globe.isTrackingSatellite ? 'Hủy bám' : 'Bám theo vệ tinh'}</span>`;
            };
            document.getElementById('globe-reset-view-btn').onclick = () => this.globe.focusVietnam(true);
        }

        // Open persistent Station Detail Card with decoded GNSS satellite telemetry & live tracking rays
        openStationCard(stationItem, coords) {
            if (!this.detailCardEl) return;
            this.restoreSelectedStationCard = false;
            this.stationCardMode = 'station';
            this.stationCard = { item: stationItem, coords };
            this.globe.isTrackingSatellite = false;
            const dev = stationItem.device || {};
            const isOnline = stationItem.status === 'online' || stationItem.status === 'streaming';
            const isSleep = stationItem.status === 'sleep';
            const statusLabel = isOnline ? 'Trực tuyến' : isSleep ? 'Chế độ chờ (Sleep)' : 'Mất kết nối';
            const statusColor = isOnline ? '#10b981' : isSleep ? '#f59e0b' : '#ef4444';

            // Clear satellite orbit & previous tracking rays
            disposeGroup(this.singleOrbitGroup);
            disposeGroup(this.stationTrackingGroup);
            this.selectedSatellite = null;

            const Math3D = window.Globe3DMath;
            const satLib = window.satellite;
            const THREE = window.THREE;
            const now = new Date();
            const gmst = satLib.gstime(now);

            const obsGd = {
                latitude: coords.lat * (Math.PI / 180.0),
                longitude: coords.lon * (Math.PI / 180.0),
                height: 0.05,
            };
            const stnPos3D = Math3D.latLonAltToVector3(coords.lat, coords.lon, 4);

            // A station tracks only fresh RTCM observations matched to a loaded orbit.
            const trackedSats = [];
            const constCounts = { gps: 0, galileo: 0, beidou: 0, glonass: 0, qzss: 0 };
            const liveRtcmSatellites = window.GnssSatelliteOverlay?.getLiveRtcmSatellites(dev) || [];
            const hasLiveRtcm = liveRtcmSatellites.length > 0;
            const matchedRtcmKeys = new Set();

            this.satelliteMeshes.forEach(({ mesh, satItem }) => {
                if (!satItem?.satrec) return;
                try {
                    const posVel = satLib.propagate(satItem.satrec, now);
                    if (posVel && posVel.position) {
                        const posEcf = satLib.eciToEcf(posVel.position, gmst);
                        const look = satLib.ecfToLookAngles(obsGd, posEcf);
                        const elDeg = look.elevation * (180.0 / Math.PI);
                        const azDeg = ((look.azimuth * (180.0 / Math.PI)) + 360) % 360;
                        const overlay = window.GnssSatelliteOverlay;
                        const liveRtcmSat = overlay?.getLiveRtcmSatellite(satItem, dev, liveRtcmSatellites) || null;
                        const rtcmKey = overlay?.getRtcmSatelliteKey(satItem);
                        if (liveRtcmSat && rtcmKey && !matchedRtcmKeys.has(rtcmKey)) {
                            matchedRtcmKeys.add(rtcmKey);
                            trackedSats.push({ satItem, elDeg, azDeg, mesh, liveRtcmSat });
                            constCounts[satItem.constellationKey] = (constCounts[satItem.constellationKey] || 0) + 1;
                        }
                    }
                } catch (e) {}
            });

            // Sort by elevation descending (highest overhead satellites first)
            trackedSats.sort((a, b) => b.elDeg - a.elDeg);

            // Draw luminous reception signal rays from station to each tracked satellite (only if station is online or sleep)
            if (isOnline || isSleep) {
                trackedSats.forEach(({ satItem, elDeg, mesh }) => {
                    const cfg = CONSTELLATIONS[satItem.constellationKey] || CONSTELLATIONS.gps;
                    const rayGeo = new THREE.BufferGeometry().setFromPoints([stnPos3D, mesh.position]);
                    const rayMat = new THREE.LineBasicMaterial({
                        color: cfg.color,
                        transparent: true,
                        opacity: Math.min(0.65, Math.max(0.25, elDeg / 60.0)),
                        blending: THREE.AdditiveBlending,
                    });
                    this.stationTrackingGroup.add(new THREE.Line(rayGeo, rayMat));
                });
            }

            const totalTracked = trackedSats.length;
            const unmatchedRtcmCodes = liveRtcmSatellites
                .filter(sat => {
                    const key = window.GnssSatelliteOverlay?.getRtcmSatelliteKey(sat);
                    return !key || !matchedRtcmKeys.has(key);
                })
                .slice(0, 8)
                .map(sat => window.GnssSatelliteOverlay?.getRtcmSatelliteCode(sat) || sat.id);

            // Generate satellite chips HTML (clickable to view satellite orbit!)
            const satChipsHtml = trackedSats.slice(0, 24).map(({ satItem, elDeg, liveRtcmSat }) => {
                const cKey = satItem.constellationKey;
                const source = `RTCM ${liveRtcmSat.messageType || ''} · C/N₀ ${liveRtcmSat.cnr || liveRtcmSat.snr || '--'} dB-Hz`;
                return `<span class="tracked-sat-chip ${cKey}" data-sat-key="${satItem.key}" title="${satItem.name} · ${source}">
                    ${satItem.code} <small>RTCM</small>
                </span>`;
            }).join('');

            const offlineNoticeHtml = (!isOnline && !isSleep) ? `
                <div style="font-size:0.75rem; background:rgba(239,68,68,0.15); border:1px solid #ef4444; color:#fca5a5; border-radius:6px; padding:6px 8px; margin: 8px 0; text-align:center;">
                    ⚠️ Trạm mất kết nối (Offline) — Ngưng phát tia thu nhận tín hiệu
                </div>
            ` : '';

            this.detailCardEl.innerHTML = `
                <div class="card-top-row">
                    <div class="card-title" style="color:#00f2ff">
                        <span>📡</span>
                        <span><b>${dev.name || dev.serial || 'Base Station'}</b></span>
                    </div>
                    <button type="button" class="card-close-btn" id="globe-card-close" aria-label="Đóng">✕</button>
                </div>
                ${offlineNoticeHtml}
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
                        <span class="stat-label">Vệ tinh agent giải mã RTCM</span>
                    <span class="stat-value" style="color:${isOnline || isSleep ? '#10b981' : '#ef4444'}">${(isOnline || isSleep) ? `${totalTracked} khớp quỹ đạo / ${liveRtcmSatellites.length} RTCM` : '0 Vệ tinh'} <span style="font-size:0.65rem;color:#94a3b8">${hasLiveRtcm ? `(${Math.max(0, liveRtcmSatellites.length - totalTracked)} chưa ghép quỹ đạo${unmatchedRtcmCodes.length ? `: ${unmatchedRtcmCodes.join(', ')}${liveRtcmSatellites.length - totalTracked > 8 ? ', …' : ''}` : ''})` : '(đang chờ RTCM)'}</span></span>
                    </div>
                </div>
                <div style="font-size:0.7rem; color:#94a3b8; font-weight:600; margin-bottom:4px; text-transform:uppercase;">
                    Phân bố chòm sao thu nhận:
                </div>
                <div class="sat-constellation-row">
                    <span class="sat-pill gps">GPS: <b>${(isOnline || isSleep) ? constCounts.gps : 0}</b></span>
                    <span class="sat-pill gal">GAL: <b>${(isOnline || isSleep) ? constCounts.galileo : 0}</b></span>
                    <span class="sat-pill bds">BDS: <b>${(isOnline || isSleep) ? constCounts.beidou : 0}</b></span>
                    <span class="sat-pill glo">GLO: <b>${(isOnline || isSleep) ? constCounts.glonass : 0}</b></span>
                    <span class="sat-pill qzss">QZSS: <b>${(isOnline || isSleep) ? constCounts.qzss : 0}</b></span>
                </div>
                ${(isOnline || isSleep) && trackedSats.length > 0 ? `
                    <div class="tracked-sats-wrapper">
                        <div class="tracked-sats-header">
                            <span>Vệ tinh RTCM đang nhận (bấm để bám quỹ đạo):</span>
                            <span>${trackedSats.length} Sats</span>
                        </div>
                        <div class="tracked-sats-chips">
                            ${satChipsHtml}
                        </div>
                    </div>
                ` : ''}
                ${(isOnline || isSleep) && !trackedSats.length ? `
                    <div class="small text-muted mt-2">
                        ${hasLiveRtcm ? 'Có RTCM mới nhưng chưa khớp PRN với quỹ đạo đã nạp.' : 'Đang chờ agent gửi RTCM mới.'}
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
                <div style="font-size:0.62rem; color:#64748b; text-align:center; margin-top:8px; border-top:1px dashed rgba(255,255,255,0.1); padding-top:4px;">
                    Nguồn: <a href="https://celestrak.org/NORAD/elements/" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;text-decoration:none;"><b>CelesTrak (NORAD GP)</b> <i class="bi bi-box-arrow-up-right" style="font-size:0.58rem;"></i></a> • <b>SGP4 1Hz</b> • <b>RTCM 1074-1127</b>
                </div>
            `;

            this.stationCardRtcmSignature = liveRtcmSatellites.map(sat => sat.id || `${sat.system}:${sat.prn}`).sort().join('|');
            this.detailCardEl.style.display = 'block';
            document.getElementById('globe-card-close').onclick = () => this.closeDetailCard();
            document.getElementById('globe-reset-view-btn').onclick = () => this.globe.focusVietnam(true);
            document.getElementById('globe-focus-station-btn').onclick = () => {
                this.globe.focusCoords(coords.lat, coords.lon);
            };

            // Allow clicking any tracked satellite chip to immediately switch to its 3D orbit!
            this.detailCardEl.querySelectorAll('.tracked-sat-chip').forEach(chip => {
                chip.onclick = (e) => {
                    const key = e.currentTarget.getAttribute('data-sat-key');
                    const satRec = this.satelliteMeshes.get(key);
                    if (satRec) {
                        this.openSatelliteCard(satRec.satItem, satRec.mesh);
                    }
                };
            });
        }

        closeDetailCard() {
            if (this.detailCardEl) this.detailCardEl.style.display = 'none';
            if (this.selectedLabelEl) this.selectedLabelEl.style.display = 'none';
            this.stationCardMode = null;
            this.stationCard = null;
            this.selectedSatellite = null;
            this.footprintMesh = null;
            disposeGroup(this.singleOrbitGroup);
            disposeGroup(this.stationTrackingGroup);
            this.globe.isTrackingSatellite = false;
            this.globe.lastRenderAt = null;
        }

        destroy() {
            this.stop();
            this.closeDetailCard();
            disposeGroup(this.satellitesGroup);
            disposeGroup(this.stationsGroup);
            Object.values(this.pinTextures).forEach(texture => texture.dispose());
            this.statusEl.remove();
            this.selectedLabelEl.remove();
            if (this.detailCardEl?.parentNode) {
                this.detailCardEl.parentNode.removeChild(this.detailCardEl);
                this.detailCardEl = null;
            }
        }
    }

    window.Globe3DSatelliteLayer = Globe3DSatelliteLayer;
})();
