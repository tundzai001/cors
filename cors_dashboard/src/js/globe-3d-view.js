// 3D Earth Globe with Real-time Orbiting GNSS Satellites (GPS, Galileo, BeiDou, GLONASS)
// Interactive Three.js WebGL visualization inspired by satellitemap.space and gnssplanning.com
(() => {
    'use strict';

    const RE_EARTH_KM = 6371.0;
    const SCENE_EARTH_RADIUS = 5.0; // Three.js units for Earth radius
    const KM_TO_UNITS = SCENE_EARTH_RADIUS / RE_EARTH_KM;
    const H_IONO_KM = 350.0;
    const SCENE_IONO_RADIUS = SCENE_EARTH_RADIUS * (1.0 + H_IONO_KM / RE_EARTH_KM); // ~5.275 units

    const CONSTELLATION_CONFIG = {
        gps: { name: 'GPS', flag: '🇺🇸', color: 0x22c55e, hex: '#22c55e', nominalAltKm: 20200, count: 32 },
        galileo: { name: 'Galileo', flag: '🇪🇺', color: 0x38bdf8, hex: '#38bdf8', nominalAltKm: 23222, count: 31 },
        beidou: { name: 'BeiDou', flag: '🇨🇳', color: 0xf59e0b, hex: '#f59e0b', nominalAltKm: 21500, count: 53 },
        glonass: { name: 'GLONASS', flag: '🇷🇺', color: 0xef4444, hex: '#ef4444', nominalAltKm: 19130, count: 27 },
    };

    // Helper: Convert Lat/Lon (degrees) and Altitude (km) to 3D Cartesian coordinates (Three.js space)
    // Matches Three.js standard SphereGeometry UV equirectangular mapping:
    // Lat 0, Lon 0 is at (r, 0, 0); North Pole at (0, r, 0); Lon 90E at (0, 0, -r)
    function latLonAltToVector3(latDeg, lonDeg, altKm = 0) {
        const phi = (latDeg || 0) * (Math.PI / 180.0);
        const lambda = (lonDeg || 0) * (Math.PI / 180.0);
        const r = SCENE_EARTH_RADIUS * (1.0 + Math.max(0, altKm) / RE_EARTH_KM);
        return new window.THREE.Vector3(
            r * Math.cos(phi) * Math.cos(lambda),
            r * Math.sin(phi),
            -r * Math.cos(phi) * Math.sin(lambda)
        );
    }

    class Globe3DView {
        constructor() {
            this.container = null;
            this.scene = null;
            this.camera = null;
            this.renderer = null;
            this.controls = null;
            this.animFrameId = null;
            this.resizeObserver = null;

            this.earthMesh = null;
            this.atmosphereMesh = null;
            this.starfield = null;
            this.vietnamBoundariesGroup = null;
            this.satellitesGroup = null;
            this.orbitsGroup = null;
            this.stationsGroup = null;
            this.beamsGroup = null;

            // State
            this.isInitialized = false;
            this.isActive = false;
            this.stations = [];
            this.selectedStation = null;
            this.rawSatellites = [];
            this.satelliteMeshes = new Map(); // satKey -> { mesh, label, data, currentPos }
            this.orbitLines = new Map(); // satKey -> LineLoop

            this.filters = {
                gps: true,
                galileo: true,
                beidou: true,
                glonass: true,
                showOrbits: true,
                showBeams: true,
                showIonoShell: false,
                onlyVisible: false,
            };

            this.simSpeed = 1.0; // 1x Real-time, 5x, 30x, 120x, 0 (Paused)
            this.simTimeOffset = 0; // ms
            this.lastFrameTime = performance.now();
            this.currentSimDate = new Date();

            this.earthTextureMode = 'dark'; // 'dark' | 'night' | 'blue-marble'
            this.textureCache = {};

            // Camera focus target
            this.isTweening = false;
            this.tweenStartTime = 0;
            this.tweenDuration = 1200; // ms
            this.tweenStartPos = null;
            this.tweenEndPos = null;
            this.tweenStartTarget = null;
            this.tweenEndTarget = null;

            // Hover / Raycasting
            this.raycaster = null;
            this.mouse = null;
            this.hoveredObject = null;
            this.hoverTooltipEl = null;

            // Bound handlers
            this._onResize = this._onResize.bind(this);
            this._onPointerMove = this._onPointerMove.bind(this);
            this._onPointerClick = this._onPointerClick.bind(this);
            this._animate = this._animate.bind(this);
        }

        // Initialize 3D Scene inside container DOM element
        attach(containerEl, options = {}) {
            if (!containerEl || !window.THREE) {
                console.warn('[Globe3DView] Cannot attach: Container or Three.js missing.');
                return;
            }

            this.container = containerEl;
            this.stations = options.stations || this.stations;
            this.selectedStation = options.selectedStation || this.selectedStation;

            if (!this.isInitialized) {
                this._initThreeScene();
                this._createStarfield();
                this._createEarth();
                this._createGraticule();
                this._loadVietnamBoundaries();
                this.isInitialized = true;
            }

            this.isActive = true;
            this.container.style.display = 'block';
            this._onResize();

            // Setup ResizeObserver
            if (window.ResizeObserver && !this.resizeObserver) {
                this.resizeObserver = new ResizeObserver(() => this._onResize());
                this.resizeObserver.observe(this.container);
            }

            // Sync satellite data from GnssSatelliteOverlay manager
            this._syncSatellitesFromManager();

            // Update station markers on Earth surface
            this.updateStations(this.stations, this.selectedStation);

            // Focus on Vietnam by default
            this.focusVietnam(false);

            // Start animation loop
            this.lastFrameTime = performance.now();
            if (!this.animFrameId) {
                this.animFrameId = requestAnimationFrame(this._animate);
            }
        }

        detach() {
            this.isActive = false;
            if (this.animFrameId) {
                cancelAnimationFrame(this.animFrameId);
                this.animFrameId = null;
            }
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
                this.resizeObserver = null;
            }
            if (this.container) {
                this.container.style.display = 'none';
            }
            if (this.hoverTooltipEl && this.hoverTooltipEl.parentNode) {
                this.hoverTooltipEl.parentNode.removeChild(this.hoverTooltipEl);
                this.hoverTooltipEl = null;
            }
        }

        _initThreeScene() {
            const THREE = window.THREE;
            const width = this.container.clientWidth || window.innerWidth;
            const height = this.container.clientHeight || (window.innerHeight - 88);

            // Scene & Groups
            this.scene = new THREE.Scene();
            this.satellitesGroup = new THREE.Group();
            this.orbitsGroup = new THREE.Group();
            this.stationsGroup = new THREE.Group();
            this.beamsGroup = new THREE.Group();
            this.vietnamBoundariesGroup = new THREE.Group();

            this.scene.add(this.satellitesGroup);
            this.scene.add(this.orbitsGroup);
            this.scene.add(this.stationsGroup);
            this.scene.add(this.beamsGroup);
            this.scene.add(this.vietnamBoundariesGroup);

            // Camera
            this.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
            this.camera.position.set(-2.0, 3.5, -9.5); // Initial view towards Vietnam

            // WebGL Renderer
            this.renderer = new THREE.WebGLRenderer({
                antialias: true,
                alpha: false,
                powerPreference: 'high-performance',
            });
            this.renderer.setSize(width, height);
            this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
            this.renderer.setClearColor(0x020611, 1.0); // Deep cosmic navy/black
            this.container.innerHTML = '';
            this.container.appendChild(this.renderer.domElement);
            this.renderer.domElement.className = 'station-map-globe-canvas-element';

            // OrbitControls
            if (THREE.OrbitControls) {
                this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
                this.controls.enableDamping = true;
                this.controls.dampingFactor = 0.06;
                this.controls.rotateSpeed = 0.75;
                this.controls.zoomSpeed = 1.15;
                this.controls.minDistance = 5.65; // ~800km altitude close-up
                this.controls.maxDistance = 75.0; // Deep space
                this.controls.target.set(0, 0, 0);
            }

            // Lighting
            const ambient = new THREE.AmbientLight(0xffffff, 0.62);
            this.scene.add(ambient);

            const sunLight = new THREE.DirectionalLight(0xffffff, 1.15);
            sunLight.position.set(20, 15, -25);
            this.scene.add(sunLight);

            const secondaryLight = new THREE.DirectionalLight(0x38bdf8, 0.35);
            secondaryLight.position.set(-20, -10, 20);
            this.scene.add(secondaryLight);

            // Raycaster for hover/click interactions
            this.raycaster = new THREE.Raycaster();
            this.mouse = new THREE.Vector2();

            // DOM Event Listeners
            this.renderer.domElement.addEventListener('pointermove', this._onPointerMove);
            this.renderer.domElement.addEventListener('click', this._onPointerClick);
            window.addEventListener('resize', this._onResize);

            // Hover tooltip
            this.hoverTooltipEl = document.createElement('div');
            this.hoverTooltipEl.className = 'globe-satellite-tooltip';
            this.hoverTooltipEl.style.display = 'none';
            this.container.appendChild(this.hoverTooltipEl);
        }

        _createStarfield() {
            const THREE = window.THREE;
            const starCount = 2800;
            const geometry = new THREE.BufferGeometry();
            const positions = new Float32Array(starCount * 3);
            const colors = new Float32Array(starCount * 3);

            for (let i = 0; i < starCount; i++) {
                const radius = 180 + Math.random() * 120;
                const theta = Math.random() * Math.PI * 2;
                const phi = Math.acos(2 * Math.random() - 1);

                positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
                positions[i * 3 + 1] = radius * Math.cos(phi);
                positions[i * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta);

                // Subtle twinkling star tints (cool blue, warm white)
                const tint = Math.random();
                if (tint > 0.8) {
                    colors[i * 3] = 0.5; colors[i * 3 + 1] = 0.8; colors[i * 3 + 2] = 1.0;
                } else if (tint > 0.6) {
                    colors[i * 3] = 1.0; colors[i * 3 + 1] = 0.92; colors[i * 3 + 2] = 0.7;
                } else {
                    colors[i * 3] = 0.9; colors[i * 3 + 1] = 0.95; colors[i * 3 + 2] = 1.0;
                }
            }

            geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
            geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

            const material = new THREE.PointsMaterial({
                size: 1.25,
                vertexColors: true,
                transparent: true,
                opacity: 0.82,
                depthWrite: false,
            });

            this.starfield = new THREE.Points(geometry, material);
            this.scene.add(this.starfield);
        }

        _createEarth() {
            const THREE = window.THREE;
            const earthGeo = new THREE.SphereGeometry(SCENE_EARTH_RADIUS, 64, 64);

            // Texture loader
            const textureLoader = new THREE.TextureLoader();
            const textureUrl = '/assets/img/earth-dark.jpg';
            const earthTexture = textureLoader.load(textureUrl);

            const earthMat = new THREE.MeshStandardMaterial({
                map: earthTexture,
                roughness: 0.72,
                metalness: 0.12,
            });

            this.earthMesh = new THREE.Mesh(earthGeo, earthMat);
            this.scene.add(this.earthMesh);

            // Atmospheric Outer Rim Glow
            const atmoGeo = new THREE.SphereGeometry(SCENE_EARTH_RADIUS * 1.025, 64, 64);
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
                        float intensity = pow(0.68 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.8);
                        gl_FragColor = vec4(0.18, 0.68, 1.0, 1.0) * intensity * 1.35;
                    }
                `,
                blending: THREE.AdditiveBlending,
                side: THREE.BackSide,
                transparent: true,
                depthWrite: false,
            });
            this.atmosphereMesh = new THREE.Mesh(atmoGeo, atmoMat);
            this.scene.add(this.atmosphereMesh);

            // Ionosphere Thin Shell Sphere (350km altitude)
            const ionoGeo = new THREE.SphereGeometry(SCENE_IONO_RADIUS, 48, 48);
            const ionoMat = new THREE.MeshBasicMaterial({
                color: 0x00d2ff,
                transparent: true,
                opacity: 0.07,
                wireframe: true,
                depthWrite: false,
            });
            this.ionoShellMesh = new THREE.Mesh(ionoGeo, ionoMat);
            this.ionoShellMesh.visible = this.filters.showIonoShell;
            this.scene.add(this.ionoShellMesh);
        }

        _createGraticule() {
            const THREE = window.THREE;
            const r = SCENE_EARTH_RADIUS * 1.001;

            // Latitude circles every 30 deg
            [-60, -30, 0, 30, 60].forEach(lat => {
                const phi = lat * (Math.PI / 180.0);
                const circleRadius = r * Math.cos(phi);
                const circleY = r * Math.sin(phi);

                const curve = new THREE.EllipseCurve(0, 0, circleRadius, circleRadius, 0, Math.PI * 2);
                const points = curve.getPoints(120).map(p => new THREE.Vector3(p.x, circleY, p.y));
                const geometry = new THREE.BufferGeometry().setFromPoints(points);
                const isEquator = lat === 0;
                const material = new THREE.LineBasicMaterial({
                    color: isEquator ? 0x38bdf8 : 0x1e3a5f,
                    transparent: true,
                    opacity: isEquator ? 0.45 : 0.22,
                });
                const line = new THREE.Line(geometry, material);
                this.scene.add(line);
            });
        }

        async _loadVietnamBoundaries() {
            try {
                const resp = await fetch('/api/public/vietnam-provinces-geojson');
                if (!resp.ok) return;
                const data = await resp.json();
                if (!data || !data.features || !window.THREE) return;

                const THREE = window.THREE;
                const r = SCENE_EARTH_RADIUS * 1.0025; // Just slightly above Earth surface

                this.vietnamBoundariesGroup.clear();
                const lineMat = new THREE.LineBasicMaterial({
                    color: 0x38bdf8,
                    transparent: true,
                    opacity: 0.65,
                });

                data.features.forEach(feat => {
                    const geom = feat.geometry;
                    if (!geom) return;

                    const addLine = (ringCoords) => {
                        if (!Array.isArray(ringCoords) || ringCoords.length < 2) return;
                        const points = [];
                        for (let i = 0; i < ringCoords.length; i++) {
                            const [lon, lat] = ringCoords[i];
                            points.push(latLonAltToVector3(lat, lon, 15)); // 15km above surface
                        }
                        const geo = new THREE.BufferGeometry().setFromPoints(points);
                        const line = new THREE.Line(geo, lineMat);
                        this.vietnamBoundariesGroup.add(line);
                    };

                    if (geom.type === 'Polygon') {
                        geom.coordinates.forEach(ring => addLine(ring));
                    } else if (geom.type === 'MultiPolygon') {
                        geom.coordinates.forEach(poly => poly.forEach(ring => addLine(ring)));
                    }
                });
            } catch (e) {
                console.warn('[Globe3DView] Could not load Vietnam boundary GeoJSON:', e);
            }
        }

        _syncSatellitesFromManager() {
            const mgr = window.GnssSatelliteOverlay;
            if (!mgr || !Array.isArray(mgr.rawSatellites) || mgr.rawSatellites.length === 0) {
                // If not yet loaded, wait for TLEs
                setTimeout(() => this._syncSatellitesFromManager(), 1500);
                return;
            }

            this.rawSatellites = mgr.rawSatellites;
            this._buildSatelliteMeshes();
            this._buildOrbitTracks();
        }

        _buildSatelliteMeshes() {
            const THREE = window.THREE;
            if (!THREE || !this.satellitesGroup) return;

            this.satellitesGroup.clear();
            this.satelliteMeshes.clear();

            const sharedGeos = {
                octahedron: new THREE.OctahedronGeometry(0.18, 0),
            };

            this.rawSatellites.forEach(satItem => {
                const constCfg = CONSTELLATION_CONFIG[satItem.constellationKey] || CONSTELLATION_CONFIG.gps;
                const mat = new THREE.MeshStandardMaterial({
                    color: constCfg.color,
                    emissive: constCfg.color,
                    emissiveIntensity: 0.5,
                    roughness: 0.3,
                    metalness: 0.8,
                });

                const mesh = new THREE.Mesh(sharedGeos.octahedron, mat);
                mesh.userData = {
                    satKey: satItem.key,
                    satItem: satItem,
                    constellation: satItem.constellationKey,
                    code: satItem.code,
                    name: satItem.name,
                };

                // Add subtle outer halo / sprite glow
                const haloGeo = new THREE.SphereGeometry(0.32, 16, 16);
                const haloMat = new THREE.MeshBasicMaterial({
                    color: constCfg.color,
                    transparent: true,
                    opacity: 0.25,
                    depthWrite: false,
                });
                const halo = new THREE.Mesh(haloGeo, haloMat);
                mesh.add(halo);

                this.satellitesGroup.add(mesh);
                this.satelliteMeshes.set(satItem.key, {
                    mesh,
                    satItem,
                    constellationKey: satItem.constellationKey,
                    currentPos: null,
                });
            });
        }

        _buildOrbitTracks() {
            const THREE = window.THREE;
            const satLib = window.satellite;
            if (!THREE || !satLib || !this.orbitsGroup) return;

            this.orbitsGroup.clear();
            this.orbitLines.clear();

            const now = new Date();
            const sampleCount = 90; // Sample points along full orbital ellipse

            this.rawSatellites.forEach(satItem => {
                const constCfg = CONSTELLATION_CONFIG[satItem.constellationKey] || CONSTELLATION_CONFIG.gps;
                // Nominal period: MEO ~12 hours = 720 mins, GEO ~24 hours = 1440 mins
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
                        points.push(latLonAltToVector3(lat, lon, altKm));
                    }
                }

                if (points.length >= sampleCount * 0.8) {
                    const lineGeo = new THREE.BufferGeometry().setFromPoints(points);
                    const lineMat = new THREE.LineBasicMaterial({
                        color: constCfg.color,
                        transparent: true,
                        opacity: 0.22,
                        blending: THREE.AdditiveBlending,
                    });
                    const orbitLoop = new THREE.LineLoop(lineGeo, lineMat);
                    orbitLoop.userData = { constellation: satItem.constellationKey };
                    this.orbitsGroup.add(orbitLoop);
                    this.orbitLines.set(satItem.key, orbitLoop);
                }
            });

            this.orbitsGroup.visible = this.filters.showOrbits;
        }

        updateStations(stations = [], selectedStation = null) {
            this.stations = stations;
            this.selectedStation = selectedStation;
            const THREE = window.THREE;
            if (!THREE || !this.stationsGroup) return;

            this.stationsGroup.clear();

            const pinGeo = new THREE.CylinderGeometry(0.015, 0.05, 0.25, 12);
            const ringGeo = new THREE.RingGeometry(0.06, 0.12, 16);

            stations.forEach(item => {
                const coords = item.coords || (item.device && item.device.latitude && { lat: Number(item.device.latitude), lon: Number(item.device.longitude) });
                if (!coords || !Number.isFinite(coords.lat) || !Number.isFinite(coords.lon)) return;

                const isOnline = item.status === 'online' || item.status === 'streaming';
                const isSelected = selectedStation && (selectedStation.serial === item.device?.serial || selectedStation.name === item.device?.name);
                const color = isSelected ? 0x00f2ff : isOnline ? 0x10b981 : 0x94a3b8;

                const pos = latLonAltToVector3(coords.lat, coords.lon, 2); // on surface
                const stnGroup = new THREE.Group();
                stnGroup.position.copy(pos);

                // Orient pin along normal vector (pointing outwards from center)
                const normal = pos.clone().normalize();
                const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal);
                stnGroup.quaternion.copy(quat);

                // Pin pillar
                const pinMat = new THREE.MeshStandardMaterial({
                    color,
                    emissive: color,
                    emissiveIntensity: isSelected ? 0.9 : 0.4,
                });
                const pin = new THREE.Mesh(pinGeo, pinMat);
                pin.position.y = 0.125;
                stnGroup.add(pin);

                // Base pulsing ring on ground
                const ringMat = new THREE.MeshBasicMaterial({
                    color,
                    side: THREE.DoubleSide,
                    transparent: true,
                    opacity: isSelected ? 0.85 : 0.45,
                });
                const ring = new THREE.Mesh(ringGeo, ringMat);
                ring.rotation.x = Math.PI / 2;
                stnGroup.add(ring);

                stnGroup.userData = {
                    type: 'station',
                    station: item,
                    coords,
                };

                this.stationsGroup.add(stnGroup);
            });
        }

        // Focus smoothly on Vietnam
        focusVietnam(animate = true) {
            // Vietnam center ~ 16.0 N, 108.0 E
            const normal = latLonAltToVector3(16.0, 108.0, 0).normalize();
            const targetDist = 9.8; // Close-up camera distance
            const targetPos = new window.THREE.Vector3(
                normal.x * targetDist,
                normal.y * targetDist + 1.2,
                normal.z * targetDist
            );
            const targetLook = new window.THREE.Vector3(normal.x * 2.5, normal.y * 2.5, normal.z * 2.5);

            if (!animate || !this.camera) {
                this.camera.position.copy(targetPos);
                if (this.controls) {
                    this.controls.target.copy(targetLook);
                    this.controls.update();
                }
                return;
            }

            // Start smooth tween
            this.isTweening = true;
            this.tweenStartTime = performance.now();
            this.tweenStartPos = this.camera.position.clone();
            this.tweenEndPos = targetPos;
            this.tweenStartTarget = this.controls ? this.controls.target.clone() : new window.THREE.Vector3();
            this.tweenEndTarget = targetLook;
        }

        setFilter(key, value) {
            if (this.filters.hasOwnProperty(key)) {
                this.filters[key] = value;
                if (key === 'showOrbits' && this.orbitsGroup) {
                    this.orbitsGroup.visible = !!value;
                }
                if (key === 'showBeams' && this.beamsGroup) {
                    this.beamsGroup.visible = !!value;
                }
                if (key === 'showIonoShell' && this.ionoShellMesh) {
                    this.ionoShellMesh.visible = !!value;
                }
            }
        }

        setSimSpeed(speed) {
            this.simSpeed = Number(speed) || 1.0;
        }

        setEarthTexture(mode) {
            this.earthTextureMode = mode;
            if (!this.earthMesh || !window.THREE) return;

            const mapFiles = {
                dark: '/assets/img/earth-dark.jpg',
                night: '/assets/img/earth-night.jpg',
                'blue-marble': '/assets/img/earth-blue-marble.jpg',
            };
            const path = mapFiles[mode] || mapFiles.dark;

            if (!this.textureCache[mode]) {
                const loader = new window.THREE.TextureLoader();
                this.textureCache[mode] = loader.load(path);
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
            if (!this.container || !this.raycaster) return;
            const rect = this.renderer.domElement.getBoundingClientRect();
            this.mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            this.mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

            // Check intersections with satellites and stations
            this.raycaster.setFromCamera(this.mouse, this.camera);
            const intersects = this.raycaster.intersectObjects(
                [...this.satellitesGroup.children, ...this.stationsGroup.children],
                true
            );

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

        _onPointerClick(event) {
            if (!this.hoveredObject) return;
            const data = this.hoveredObject.userData;

            if (data?.station) {
                // Select this station
                this.selectedStation = {
                    lat: data.coords.lat,
                    lon: data.coords.lon,
                    name: data.station.device?.name || data.station.device?.serial,
                    serial: data.station.device?.serial,
                };
                if (window.GnssSatelliteOverlay) {
                    window.GnssSatelliteOverlay.setSelectedStation(this.selectedStation);
                }
                this.updateStations(this.stations, this.selectedStation);
            }
        }

        _showTooltip(target, x, y) {
            if (!this.hoverTooltipEl) return;
            const sat = target.userData?.satItem;
            const stn = target.userData?.station;

            if (sat) {
                const cfg = CONSTELLATION_CONFIG[sat.constellationKey] || CONSTELLATION_CONFIG.gps;
                const pos = target.userData?.lastCalculated || {};
                this.hoverTooltipEl.innerHTML = `
                    <div class="tooltip-header" style="color:${cfg.hex}">
                        <span>${cfg.flag} <b>${sat.code}</b> (${cfg.name})</span>
                    </div>
                    <div class="tooltip-body">
                        <div><b>Tên:</b> ${sat.name || sat.code}</div>
                        <div><b>Vĩ/Kinh:</b> ${pos.lat ? `${pos.lat.toFixed(2)}°, ${pos.lon.toFixed(2)}°` : '--'}</div>
                        <div><b>Độ cao quỹ đạo:</b> ${pos.altKm ? `${Math.round(pos.altKm).toLocaleString()} km` : cfg.nominalAltKm + ' km'}</div>
                        ${pos.elDeg !== undefined ? `<div><b>Góc ngẩng (El):</b> <span style="color:#38bdf8">${pos.elDeg.toFixed(1)}°</span></div>` : ''}
                    </div>
                `;
            } else if (stn) {
                const dev = stn.device || {};
                this.hoverTooltipEl.innerHTML = `
                    <div class="tooltip-header" style="color:#10b981">
                        <span>📡 <b>${dev.name || dev.serial || 'CORS Station'}</b></span>
                    </div>
                    <div class="tooltip-body">
                        <div><b>Serial:</b> ${dev.serial || '--'}</div>
                        <div><b>Trạng thái:</b> ${stn.status || 'online'}</div>
                        <div><b>Tọa độ:</b> ${target.userData.coords.lat.toFixed(4)}°, ${target.userData.coords.lon.toFixed(4)}°</div>
                        <div class="text-info mt-1" style="font-size:0.75rem;">Nhấp để kết nối tia vệ tinh & IPP</div>
                    </div>
                `;
            }

            this.hoverTooltipEl.style.left = `${x + 15}px`;
            this.hoverTooltipEl.style.top = `${y - 25}px`;
            this.hoverTooltipEl.style.display = 'block';
        }

        // Main Animation Loop
        _animate(now) {
            if (!this.isActive) return;
            this.animFrameId = requestAnimationFrame(this._animate);

            const dtSeconds = Math.min((now - this.lastFrameTime) / 1000.0, 0.1);
            this.lastFrameTime = now;

            // Handle Camera Tweening
            if (this.isTweening) {
                const progress = Math.min((now - this.tweenStartTime) / this.tweenDuration, 1.0);
                // Ease out cubic
                const ease = 1.0 - Math.pow(1.0 - progress, 3);
                this.camera.position.lerpVectors(this.tweenStartPos, this.tweenEndPos, ease);
                if (this.controls && this.tweenStartTarget && this.tweenEndTarget) {
                    this.controls.target.lerpVectors(this.tweenStartTarget, this.tweenEndTarget, ease);
                }
                if (progress >= 1.0) {
                    this.isTweening = false;
                }
            }

            // Update Simulation Time
            if (this.simSpeed > 0) {
                this.simTimeOffset += (dtSeconds * 1000.0) * (this.simSpeed - 1.0);
            }
            this.currentSimDate = new Date(Date.now() + this.simTimeOffset);

            // Propagate Satellites in 3D Space
            this._updateSatellites3D(this.currentSimDate);

            // Update Controls
            if (this.controls) {
                this.controls.update();
            }

            // Render Frame
            if (this.renderer && this.scene && this.camera) {
                this.renderer.render(this.scene, this.camera);
            }
        }

        _updateSatellites3D(simDate) {
            const satLib = window.satellite;
            if (!satLib || this.satelliteMeshes.size === 0) return;

            const gmst = satLib.gstime(simDate);
            const THREE = window.THREE;

            let obsGd = null;
            let obsPos3D = null;
            if (this.selectedStation && Number.isFinite(this.selectedStation.lat) && Number.isFinite(this.selectedStation.lon)) {
                obsGd = {
                    latitude: this.selectedStation.lat * (Math.PI / 180.0),
                    longitude: this.selectedStation.lon * (Math.PI / 180.0),
                    height: 0.05,
                };
                obsPos3D = latLonAltToVector3(this.selectedStation.lat, this.selectedStation.lon, 2);
            }

            // Clear previous line-of-sight laser beams
            if (this.beamsGroup) {
                this.beamsGroup.clear();
            }

            const visibleSats = [];

            this.satelliteMeshes.forEach((satRecord, key) => {
                const satItem = satRecord.satItem;
                const mesh = satRecord.mesh;
                const constKey = satRecord.constellationKey;

                // Check constellation filter
                const isConstEnabled = this.filters[constKey] !== false;
                if (!isConstEnabled) {
                    mesh.visible = false;
                    const orbit = this.orbitLines.get(key);
                    if (orbit) orbit.visible = false;
                    return;
                }

                // SGP4 Propagation
                try {
                    const posVel = satLib.propagate(satItem.satrec, simDate);
                    if (!posVel || !posVel.position) {
                        mesh.visible = false;
                        return;
                    }

                    const gd = satLib.eciToGeodetic(posVel.position, gmst);
                    const lat = satLib.degreesLat(gd.latitude);
                    const lon = satLib.degreesLong(gd.longitude);
                    const altKm = gd.height;

                    // Compute 3D Cartesian position in space
                    const pos3D = latLonAltToVector3(lat, lon, altKm);
                    mesh.position.copy(pos3D);
                    mesh.visible = true;

                    // Visibility & Look Angles relative to selected station
                    let isVisible = false;
                    let elDeg = null;
                    let azDeg = null;

                    if (obsGd) {
                        const posEcf = satLib.eciToEcf(posVel.position, gmst);
                        const look = satLib.ecfToLookAngles(obsGd, posEcf);
                        azDeg = ((look.azimuth * (180.0 / Math.PI)) + 360) % 360;
                        elDeg = look.elevation * (180.0 / Math.PI);
                        isVisible = elDeg > 5.0; // Above 5 deg horizon
                    }

                    mesh.userData.lastCalculated = { lat, lon, altKm, elDeg, azDeg };

                    // Visibility filter
                    if (this.filters.onlyVisible && this.selectedStation && !isVisible) {
                        mesh.visible = false;
                        return;
                    }

                    // Render Line-of-sight (LOS) Laser Ray & IPP in 3D
                    if (isVisible && obsPos3D && this.filters.showBeams && this.beamsGroup) {
                        visibleSats.push({ key, mesh, satItem, elDeg, azDeg, pos3D });

                        // 3D Laser Ray from station to satellite
                        const rayGeo = new THREE.BufferGeometry().setFromPoints([obsPos3D, pos3D]);
                        const rayMat = new THREE.LineBasicMaterial({
                            color: CONSTELLATION_CONFIG[constKey].color,
                            transparent: true,
                            opacity: 0.55,
                            blending: THREE.AdditiveBlending,
                        });
                        const rayLine = new THREE.Line(rayGeo, rayMat);
                        this.beamsGroup.add(rayLine);

                        // 3D IPP Point at 350km altitude
                        // Along vector from station to satellite, find intersection at r = SCENE_IONO_RADIUS
                        const dir = pos3D.clone().sub(obsPos3D).normalize();
                        // Solve |obsPos3D + t * dir| = SCENE_IONO_RADIUS
                        const b = 2 * obsPos3D.dot(dir);
                        const c = obsPos3D.lengthSq() - (SCENE_IONO_RADIUS * SCENE_IONO_RADIUS);
                        const discr = b * b - 4 * c;
                        if (discr > 0) {
                            const t = (-b + Math.sqrt(discr)) / 2;
                            const ippPos = obsPos3D.clone().add(dir.clone().multiplyScalar(t));

                            // Glowing IPP Sphere
                            const ippGeo = new THREE.SphereGeometry(0.12, 12, 12);
                            const ippMat = new THREE.MeshBasicMaterial({
                                color: 0x00f2ff,
                                transparent: true,
                                opacity: 0.85,
                            });
                            const ippMesh = new THREE.Mesh(ippGeo, ippMat);
                            ippMesh.position.copy(ippPos);
                            this.beamsGroup.add(ippMesh);
                        }
                    }

                    // Update orbit line visibility
                    const orbit = this.orbitLines.get(key);
                    if (orbit) {
                        orbit.visible = this.filters.showOrbits && isConstEnabled;
                    }
                } catch (err) {
                    // ignore propagation numerical singularities
                }
            });
        }
    }

    // Attach to window
    window.Globe3DView = new Globe3DView();
})();
