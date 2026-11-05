// 3D Earth Globe WebGL Core (Three.js)
// High-DPI NASA Blue Marble Earth, vector boundaries, sleek IGS Network station pins
(() => {
    'use strict';

    class Globe3DView {
        constructor() {
            this.container = null;
            this.scene = null;
            this.camera = null;
            this.renderer = null;
            this.controls = null;
            this.animFrameId = null;
            this.lastFrameAt = null;
            this.lastRenderAt = null;
            this.resizeObserver = null;

            this.earthMesh = null;
            this.atmosphereMesh = null;
            this.starfield = null;
            this.worldBordersGroup = null;
            this.vietnamBoundariesGroup = null;

            // Satellite & CORS layer delegate
            this.satelliteLayer = null;

            this.isInitialized = false;
            this.isActive = false;
            this.isTrackingSatellite = false;

            this.filters = {
                gps: true,
                galileo: true,
                beidou: true,
                glonass: true,
                qzss: true,
            };

            this.earthTextureMode = 'blue-marble';
            this.textureCache = {};

            // Camera focus animation
            this.isTweening = false;
            this.tweenStartTime = 0;
            this.tweenDuration = 1200;
            this.tweenStartPos = null;
            this.tweenEndPos = null;
            this.tweenStartTarget = null;
            this.tweenEndTarget = null;

            // Raycaster
            this.raycaster = null;
            this.mouse = null;
            this.hoveredObject = null;

            this._onResize = this._onResize.bind(this);
            this._onPointerMove = this._onPointerMove.bind(this);
            this._onPointerClick = this._onPointerClick.bind(this);
            this._animate = this._animate.bind(this);
            this._onVisibilityChange = this._onVisibilityChange.bind(this);
        }

        attach(containerEl, options = {}) {
            if (!containerEl || !window.THREE) return;
            this.container = containerEl;

            if (!this.isInitialized) {
                this._initThreeScene();
                this._createStarfield();
                this._createEarth();
                void this._loadWorldCountries();
                void this._loadVietnamBoundaries();
                if (window.Globe3DSatelliteLayer) {
                    this.satelliteLayer = new window.Globe3DSatelliteLayer(this);
                }
                this.isInitialized = true;
            }

            this.isActive = true;
            this.onSurfaceView = options.onSurfaceView || null;
            // React may mount a fresh container after leaving the map view.
            this.container.appendChild(this.renderer.domElement);
            this.container.appendChild(this.hoverTooltipEl);
            this.satelliteLayer?.start();
            this.container.style.display = 'block';
            this._onResize();

            if (window.ResizeObserver && !this.resizeObserver) {
                this.resizeObserver = new ResizeObserver(() => this._onResize());
                this.resizeObserver.observe(this.container);
            }

            if (this.satelliteLayer && options.stations) {
                this.satelliteLayer.updateStations(options.stations, options.selectedStation);
                this.satelliteLayer.restoreSelectedStationCard = Boolean(options.selectedStation?.serial);
                this.satelliteLayer.showSelectedStationCard();
            }

            const center = options.center || { lat: 16, lng: 108 };
            const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
            // Match the Leaflet scale in both directions. Automatic zoom-out
            // continues from this altitude instead of jumping to an overview.
            const metersPerPixel = 156543.03392 * Math.cos(center.lat * Math.PI / 180) / Math.pow(2, options.zoom || 6);
            const altitudeKm = metersPerPixel * this.container.clientHeight / 2000 / Math.tan(this.camera.fov * Math.PI / 360);
            const distance = Math.max(5.08, Math.min(90, 5 + altitudeKm / 6371 * 5));
            this.focusVietnam(false, options.autoZoom ? distance : reducedMotion ? 90 : distance, center);
            if (!options.autoZoom && !reducedMotion) {
                this.focusVietnam(true, 90, center);
            }

            document.addEventListener('visibilitychange', this._onVisibilityChange);
            this._onVisibilityChange();
        }

        _onVisibilityChange() {
            if (this.animFrameId) cancelAnimationFrame(this.animFrameId);
            this.animFrameId = null;
            this.lastFrameAt = null;
            this.lastRenderAt = null;
            if (this.isActive && !document.hidden) {
                this.satelliteLayer?.onOverlayTick();
                this.animFrameId = requestAnimationFrame(this._animate);
            }
        }

        detach() {
            this.isActive = false;
            this.onSurfaceView = null;
            document.removeEventListener('visibilitychange', this._onVisibilityChange);
            if (this.animFrameId) {
                cancelAnimationFrame(this.animFrameId);
                this.animFrameId = null;
            }
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
                this.resizeObserver = null;
            }
            if (this.container) {
                this.container.style.display = '';
            }
            if (this.satelliteLayer) {
                this.satelliteLayer.stop();
                this.satelliteLayer.closeDetailCard();
            }
            if (this.hoverTooltipEl) {
                this.hoverTooltipEl.style.display = 'none';
            }
        }

        _initThreeScene() {
            const THREE = window.THREE;
            const width = this.container.clientWidth || window.innerWidth;
            const height = this.container.clientHeight || (window.innerHeight - 88);

            this.scene = new THREE.Scene();
            this.worldBordersGroup = new THREE.Group();
            this.vietnamBoundariesGroup = new THREE.Group();

            this.scene.add(this.worldBordersGroup);
            this.scene.add(this.vietnamBoundariesGroup);

            this.camera = new THREE.PerspectiveCamera(45, width / height, 0.01, 1000);
            this.camera.position.set(-2.0, 3.5, -9.5);

            this.renderer = new THREE.WebGLRenderer({ antialias: false, alpha: false, powerPreference: 'low-power' });
            this.renderer.setSize(width, height);
            this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.25));
            this.renderer.setClearColor(0x020611, 1.0);
            this.container.innerHTML = '';
            this.container.appendChild(this.renderer.domElement);
            this.renderer.domElement.className = 'station-map-globe-canvas-element';

            this.hoverTooltipEl = document.createElement('div');
            this.hoverTooltipEl.className = 'globe-3d-tooltip';
            this.container.appendChild(this.hoverTooltipEl);

            if (THREE.OrbitControls) {
                this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
                this.controls.enableDamping = true;
                this.controls.dampingFactor = 0.08;
                this.controls.rotateSpeed = 0.8;
                this.controls.zoomSpeed = 1.25;
                this.controls.minDistance = 5.06; // Allows zooming down to ~75km above surface
                this.controls.maxDistance = 120.0; // Allows zooming out to view full orbits
                this.controls.target.set(0, 0, 0);
                this.controls.addEventListener('start', () => { this.isTweening = false; });
            }

            // Balanced lighting for true-color Blue Marble Earth
            this.scene.add(new THREE.AmbientLight(0xffffff, 0.85));
            const sunLight = new THREE.DirectionalLight(0xffffff, 1.35);
            sunLight.position.set(20, 15, -25);
            this.scene.add(sunLight);

            this.raycaster = new THREE.Raycaster();
            this.mouse = new THREE.Vector2();

            this.renderer.domElement.addEventListener('pointermove', this._onPointerMove);
            this.renderer.domElement.addEventListener('click', this._onPointerClick);
            window.addEventListener('resize', this._onResize);
        }

        _createStarfield() {
            const THREE = window.THREE;
            const starCount = 600;
            const geometry = new THREE.BufferGeometry();
            const positions = new Float32Array(starCount * 3);
            const colors = new Float32Array(starCount * 3);

            for (let i = 0; i < starCount; i++) {
                const radius = 150 + Math.random() * 120;
                const theta = Math.random() * Math.PI * 2;
                const phi = Math.acos(2 * Math.random() - 1);
                positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
                positions[i * 3 + 1] = radius * Math.cos(phi);
                positions[i * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta);

                // Subtle twinkling star tints (white, ice blue, golden)
                const cRand = Math.random();
                if (cRand > 0.8) {
                    colors[i * 3] = 0.7; colors[i * 3 + 1] = 0.85; colors[i * 3 + 2] = 1.0;
                } else if (cRand > 0.6) {
                    colors[i * 3] = 1.0; colors[i * 3 + 1] = 0.9; colors[i * 3 + 2] = 0.7;
                } else {
                    colors[i * 3] = 0.9; colors[i * 3 + 1] = 0.95; colors[i * 3 + 2] = 1.0;
                }
            }

            geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
            geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
            const material = new THREE.PointsMaterial({
                size: 0.7,
                vertexColors: true,
                transparent: true,
                opacity: 0.3,
                depthWrite: false,
            });
            this.starfield = new THREE.Points(geometry, material);
            this.scene.add(this.starfield);
        }

        _createEarth() {
            const THREE = window.THREE;
            const Math3D = window.Globe3DMath;
            const R = Math3D?.SCENE_EARTH_RADIUS || 5.0;

            const earthGeo = new THREE.SphereGeometry(R, 64, 64);
            const textureLoader = new THREE.TextureLoader();
            // Default to high-res NASA Blue Marble (1.4MB) with real blue oceans and continents
            const tex = textureLoader.load('/assets/img/earth-blue-marble.jpg');
            tex.generateMipmaps = true;
            tex.minFilter = THREE.LinearMipmapLinearFilter;
            tex.magFilter = THREE.LinearFilter;
            if (this.renderer) tex.anisotropy = Math.min(16, this.renderer.capabilities.getMaxAnisotropy());

            const earthMat = new THREE.MeshStandardMaterial({
                map: tex,
                roughness: 0.65,
                metalness: 0.08,
            });
            this.earthMesh = new THREE.Mesh(earthGeo, earthMat);
            this.scene.add(this.earthMesh);

            // Subtle atmospheric blue aura (like IGS Network / NASA)
            const atmoGeo = new THREE.SphereGeometry(R * 1.025, 48, 48);
            const atmoMat = new THREE.ShaderMaterial({
                vertexShader: `
                    varying vec3 vNormal;
                    void main() {
                        vNormal = normalize(normalMatrix * normal);
                        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
                    }
                `,
                fragmentShader: `
                    varying vec3 vNormal;
                    void main() {
                        float intensity = pow(0.70 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.8);
                        gl_FragColor = vec4(0.2, 0.65, 1.0, 1.0) * intensity * 1.2;
                    }
                `,
                blending: THREE.AdditiveBlending,
                side: THREE.BackSide,
                transparent: true,
                depthWrite: false,
            });
            this.atmosphereMesh = new THREE.Mesh(atmoGeo, atmoMat);
            this.scene.add(this.atmosphereMesh);
        }

        async _loadWorldCountries() {
            try {
                const resp = await fetch('/api/public/world-countries-geojson');
                if (!resp.ok) return;
                const data = await resp.json();
                if (!data || !data.features || !window.THREE) return;

                this.countryFeatures = data.features;

                const THREE = window.THREE;
                const Math3D = window.Globe3DMath;
                this.worldBordersGroup.clear();

                const linePoints = [];
                const addRing = (ringCoords) => {
                    if (!Array.isArray(ringCoords) || ringCoords.length < 2) return;
                    for (let i = 0; i < ringCoords.length - 1; i++) {
                        const [lon1, lat1] = ringCoords[i];
                        const [lon2, lat2] = ringCoords[i + 1];
                        linePoints.push(Math3D.latLonAltToVector3(lat1, lon1, 4));
                        linePoints.push(Math3D.latLonAltToVector3(lat2, lon2, 4));
                    }
                };

                data.features.forEach(feat => {
                    const geom = feat.geometry;
                    if (!geom) return;
                    if (geom.type === 'Polygon') {
                        geom.coordinates.forEach(ring => addRing(ring));
                    } else if (geom.type === 'MultiPolygon') {
                        geom.coordinates.forEach(poly => poly.forEach(ring => addRing(ring)));
                    }
                });

                if (linePoints.length > 0) {
                    const borderGeo = new THREE.BufferGeometry().setFromPoints(linePoints);
                    // Crisp, subtle country boundary lines
                    const borderMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.35 });
                    this.worldBordersGroup.add(new THREE.LineSegments(borderGeo, borderMat));
                }
            } catch (e) {
                console.warn('[Globe3DView] Could not load countries GeoJSON:', e);
            }
        }

        async _loadVietnamBoundaries() {
            try {
                const resp = await fetch('/api/public/vietnam-provinces-geojson');
                if (!resp.ok) return;
                const data = await resp.json();
                if (!data || !data.features || !window.THREE) return;

                const THREE = window.THREE;
                const Math3D = window.Globe3DMath;
                this.vietnamBoundariesGroup.clear();
                // Crisp cyan line for Vietnam provincial borders
                const lineMat = new THREE.LineBasicMaterial({ color: 0x00f2ff, transparent: true, opacity: 0.75 });

                data.features.forEach(feat => {
                    const geom = feat.geometry;
                    if (!geom) return;

                    const addLine = (ringCoords) => {
                        if (!Array.isArray(ringCoords) || ringCoords.length < 2) return;
                        const points = [];
                        for (let i = 0; i < ringCoords.length; i++) {
                            const [lon, lat] = ringCoords[i];
                            points.push(Math3D.latLonAltToVector3(lat, lon, 8));
                        }
                        this.vietnamBoundariesGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), lineMat));
                    };

                    if (geom.type === 'Polygon') {
                        geom.coordinates.forEach(ring => addLine(ring));
                    } else if (geom.type === 'MultiPolygon') {
                        geom.coordinates.forEach(poly => poly.forEach(ring => addLine(ring)));
                    }
                });
            } catch (e) {
                console.warn('[Globe3DView] Could not load VN boundary:', e);
            }
        }

        updateStations(stations = [], selectedStation = null) {
            if (this.satelliteLayer) {
                this.satelliteLayer.updateStations(stations, selectedStation);
            }
        }

        focusVietnam(animate = true, distance = 7.8, center) {
            const { lat, lng } = center ?? { lat: 16, lng: 108 };
            const Math3D = window.Globe3DMath;
            const normal = (Math3D?.latLonAltToVector3(lat, lng, 0) || new window.THREE.Vector3(-1.48, 1.38, -4.57)).normalize();
            const targetPos = normal.multiplyScalar(distance);
            const targetLook = new window.THREE.Vector3(0, 0, 0);

            if (!animate || !this.camera) {
                this.camera.position.copy(targetPos);
                if (this.controls) {
                    this.controls.target.copy(targetLook);
                    this.controls.update();
                }
                return;
            }

            this.isTweening = true;
            this.isTrackingSatellite = false;
            this.tweenStartTime = performance.now();
            this.tweenStartPos = this.camera.position.clone();
            this.tweenEndPos = targetPos;
            this.tweenStartTarget = this.controls ? this.controls.target.clone() : new window.THREE.Vector3();
            this.tweenEndTarget = targetLook;
        }

        focusCoords(lat, lon) {
            const Math3D = window.Globe3DMath;
            if (!Math3D || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
            const normal = Math3D.latLonAltToVector3(lat, lon, 0).normalize();
            // Zoom closely into station coordinates (~380km above surface) with center (0,0,0)
            const targetPos = new window.THREE.Vector3(normal.x * 5.30, normal.y * 5.30, normal.z * 5.30);
            const targetLook = new window.THREE.Vector3(0, 0, 0);

            this.isTweening = true;
            this.isTrackingSatellite = false;
            this.tweenStartTime = performance.now();
            this.tweenStartPos = this.camera.position.clone();
            this.tweenEndPos = targetPos;
            this.tweenStartTarget = this.controls ? this.controls.target.clone() : new window.THREE.Vector3();
            this.tweenEndTarget = targetLook;
        }

        setFilter(key, value) {
            this.filters[key] = value;
        }

        setEarthTexture(mode) {
            this.earthTextureMode = mode;
            if (!this.earthMesh || !window.THREE) return;

            const mapFiles = {
                'blue-marble': '/assets/img/earth-blue-marble.jpg',
                night: '/assets/img/earth-night.jpg',
                dark: '/assets/img/earth-dark.jpg',
            };
            const path = mapFiles[mode] || mapFiles['blue-marble'];

            if (!this.textureCache[mode]) {
                const loader = new window.THREE.TextureLoader();
                const tex = loader.load(path);
                tex.generateMipmaps = true;
                tex.minFilter = window.THREE.LinearMipmapLinearFilter;
                tex.magFilter = window.THREE.LinearFilter;
                if (this.renderer) tex.anisotropy = Math.min(16, this.renderer.capabilities.getMaxAnisotropy());
                this.textureCache[mode] = tex;
            }
            this.earthMesh.material.map = this.textureCache[mode];
            this.earthMesh.material.needsUpdate = true;
        }

        _onResize() {
            if (!this.container || !this.renderer || !this.camera) return;
            const width = this.container.clientWidth || window.innerWidth;
            const height = this.container.clientHeight || (window.innerHeight - 88);
            this.camera.aspect = width / height;
            this.camera.updateProjectionMatrix();
            this.renderer.setSize(width, height);
        }

        _onPointerMove(event) {
            if (!this.container || !this.raycaster || !this.camera) return;
            const rect = this.renderer.domElement.getBoundingClientRect();
            this.mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            this.mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

            this.raycaster.setFromCamera(this.mouse, this.camera);
            const targets = [
                ...(this.satelliteLayer?.satellitesGroup?.children || []),
                ...(this.satelliteLayer?.stationsGroup?.children || []),
                this.earthMesh,
            ].filter(object => object?.visible);

            const intersects = this.raycaster.intersectObjects(targets, true);
            if (intersects.length > 0) {
                let target = intersects[0].object;
                while (target && !target.userData?.satItem && !target.userData?.station && target.parent) {
                    target = target.parent;
                }
                if (target && (target.userData?.satItem || target.userData?.station)) {
                    this.hoveredObject = target;
                    this.renderer.domElement.style.cursor = 'pointer';
                    this._showTooltip(target, event.clientX - rect.left, event.clientY - rect.top);
                    return;
                }
            }
            this.hoveredObject = null;
            this.renderer.domElement.style.cursor = 'grab';
            if (this.hoverTooltipEl) this.hoverTooltipEl.style.display = 'none';
        }

        _showTooltip(target, x, y) {
            if (!this.hoverTooltipEl || !target) return;
            const sat = target.userData?.satItem;
            const stn = target.userData?.station;

            if (sat) {
                if (this.satelliteLayer?.selectedSatellite?.key === sat.key) {
                    this.hoverTooltipEl.style.display = 'none';
                    return;
                }
                const cfg = {
                    gps: { name: 'GPS', flag: '🇺🇸', hex: '#22c55e' },
                    galileo: { name: 'Galileo', flag: '🇪🇺', hex: '#38bdf8' },
                    beidou: { name: 'BeiDou', flag: '🇨🇳', hex: '#f59e0b' },
                    glonass: { name: 'GLONASS', flag: '🇷🇺', hex: '#ef4444' },
                    qzss: { name: 'QZSS', flag: '🇯🇵', hex: '#a855f7' },
                }[sat.constellationKey] || { name: 'GNSS', flag: '🛰️', hex: '#38bdf8' };

                const pos = this.satelliteLayer?.satelliteMeshes.get(sat.key)?.mesh.userData.lastCalculated || {};
                const altStr = Number.isFinite(pos.altKm) ? `${Math.round(pos.altKm).toLocaleString()} km` : '20,000 km';
                const cleanName = (sat.name || '').replace(/\([^()\r\n\u2028\u2029]*\)/g, '').trim();

                this.hoverTooltipEl.innerHTML = `
                    <div class="tooltip-title" style="color:${cfg.hex}">
                        ${cfg.flag} <b>${window.GnssSatelliteOverlay?.getSatelliteLabel?.(sat) || sat.code || sat.name}</b> (${cfg.name})
                    </div>
                    <div class="tooltip-sub">
                        ${cleanName ? `${cleanName} • ` : ''}${altStr}
                    </div>
                `;
                this.hoverTooltipEl.style.left = `${x}px`;
                this.hoverTooltipEl.style.top = `${y}px`;
                this.hoverTooltipEl.style.display = 'block';
            } else if (stn) {
                const dev = stn.device || {};
                const isOnline = stn.status === 'online' || stn.status === 'streaming';
                const statusColor = isOnline ? '#10b981' : '#ef4444';
                const statusLabel = isOnline ? 'Trực tuyến' : 'Ngoại tuyến';

                this.hoverTooltipEl.innerHTML = `
                    <div class="tooltip-title" style="color:#38bdf8">
                        📡 <b>${dev.name || dev.serial || 'Base Station'}</b>
                    </div>
                    <div class="tooltip-sub">
                        Trạng thái: <span style="color:${statusColor}">${statusLabel}</span>
                    </div>
                `;
                this.hoverTooltipEl.style.left = `${x}px`;
                this.hoverTooltipEl.style.top = `${y}px`;
                this.hoverTooltipEl.style.display = 'block';
            } else {
                this.hoverTooltipEl.style.display = 'none';
            }
        }

        _onPointerClick(event) {
            // Click/touch must raycast at its own position, even without hover.
            this._onPointerMove(event);
            if (!this.hoveredObject) return;
            let target = this.hoveredObject;
            while (target && !target.userData?.satItem && !target.userData?.station && target.parent) {
                target = target.parent;
            }
            if (!target) return;
            const data = target.userData;

            if (data?.satItem && this.satelliteLayer) {
                this.satelliteLayer.openSatelliteCard(data.satItem, target);
            } else if (data?.station && this.satelliteLayer) {
                const dev = data.station.device || {};
                const stnStatus = data.station.status || dev.status || 'offline';
                const stnObj = {
                    lat: data.coords.lat,
                    lon: data.coords.lon,
                    name: dev.name || dev.serial,
                    serial: dev.serial,
                    status: stnStatus,
                };
                if (window.GnssSatelliteOverlay) {
                    window.GnssSatelliteOverlay.setSelectedStation(stnObj);
                }
                if (dev.serial) window.selectDashboardDevice?.(dev.serial);
                this.satelliteLayer.updateStations(this.satelliteLayer.stations || [], stnObj);
                this.satelliteLayer.openStationCard(data.station, data.coords);
            }
        }

        _animate(now) {
            this.animFrameId = null;
            if (!this.isActive || document.hidden) return;
            this.animFrameId = requestAnimationFrame(this._animate);
            // Retina/120 Hz displays must not multiply idle WebGL work.
            const interval = 1000 / (this.isTweening ? 60 : 30);
            if (this.lastFrameAt !== null && now - this.lastFrameAt < interval - 0.5) return;
            this.lastFrameAt = now;

            let cameraAnimating = this.isTweening;
            if (this.isTweening) {
                const progress = Math.min((now - this.tweenStartTime) / this.tweenDuration, 1.0);
                const ease = progress * progress * progress * (progress * (progress * 6 - 15) + 10);
                this.camera.position.lerpVectors(this.tweenStartPos, this.tweenEndPos, ease);
                if (this.controls && this.tweenStartTarget && this.tweenEndTarget) {
                    this.controls.target.lerpVectors(this.tweenStartTarget, this.tweenEndTarget, ease);
                }
                if (progress >= 1.0) this.isTweening = false;
            }

            // Keep station pin dots constant perceived pixel size at any zoom (IGS Network style)
            if (this.camera && this.satelliteLayer?.updateSpriteScales) {
                this.satelliteLayer.updateSpriteScales(this.camera);
            }

            // Camera Tracking Satellite
            if (this.isTrackingSatellite && this.satelliteLayer?.selectedSatellite && this.controls) {
                const satRec = this.satelliteLayer.satelliteMeshes.get(this.satelliteLayer.selectedSatellite.key);
                if (satRec?.mesh) {
                    const delta = satRec.mesh.position.clone().sub(this.controls.target);
                    if (delta.lengthSq() > 0.000001) {
                        delta.multiplyScalar(0.2);
                        this.controls.target.add(delta);
                        this.camera.position.add(delta);
                        cameraAnimating = true;
                    }
                }
            }

            const cameraChanged = this.controls?.update();
            // OrbitControls limits distance to its target, which may be a
            // satellite. Enforce a separate Earth-centered camera boundary.
            this._constrainCamera();
            if (!this.isTweening && !this.isTrackingSatellite && this.onSurfaceView && this.camera.position.lengthSq() <= 5.45 ** 2) {
                const surface = this.getSurfaceView();
                if (surface) {
                    const handoff = this.onSurfaceView;
                    this.onSurfaceView = null;
                    handoff(surface);
                    return;
                }
            }
            // Orbit propagation is 1 Hz. A stationary camera needs no duplicate
            // frames between telemetry ticks; dragging/tweening stays at 30 FPS.
            if (!cameraAnimating && !cameraChanged && this.lastRenderAt !== null && now - this.lastRenderAt < 1000) return;
            if (this.renderer && this.scene && this.camera) {
                this.renderer.render(this.scene, this.camera);
                this.satelliteLayer?.updateSelectedLabel();
                this.lastRenderAt = now;
            }
        }

        _constrainCamera() {
            const minRadius = (window.Globe3DMath?.SCENE_EARTH_RADIUS || 5) + 0.06;
            if (this.camera?.position.lengthSq() < minRadius * minRadius) {
                if (this.camera.position.lengthSq() < 0.000001) this.camera.position.set(0, 0, minRadius);
                else this.camera.position.setLength(minRadius);
                this.camera.lookAt(this.controls?.target || this.scene.position);
                this.lastRenderAt = null;
            }
        }

        getSurfaceView() {
            if (!this.camera || !window.Globe3DMath) return null;
            const direction = this.camera.getWorldDirection(new window.THREE.Vector3());
            return window.Globe3DMath.surfaceViewFromRay(this.camera.position, direction, this.camera.fov, this.container.clientHeight);
        }
    }

    window.Globe3DView = new Globe3DView();
})();
