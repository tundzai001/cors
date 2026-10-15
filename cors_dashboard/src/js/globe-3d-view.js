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

    // Major country labels for crisp geospatial orientation
    const KEY_COUNTRY_LABELS = [
        { name: 'Việt Nam', lat: 15.5, lon: 107.8, isMain: true },
        { name: 'Lào', lat: 19.2, lon: 102.5 },
        { name: 'Campuchia', lat: 12.5, lon: 104.9 },
        { name: 'Thái Lan', lat: 15.5, lon: 100.8 },
        { name: 'Trung Quốc', lat: 34.0, lon: 104.0 },
        { name: 'Philippines', lat: 13.0, lon: 122.2 },
        { name: 'Malaysia', lat: 4.2, lon: 102.0 },
        { name: 'Indonesia', lat: -1.2, lon: 115.0 },
        { name: 'Singapore', lat: 1.35, lon: 103.8 },
        { name: 'Myanmar', lat: 21.0, lon: 96.0 },
        { name: 'Nhật Bản', lat: 36.2, lon: 138.2 },
        { name: 'Hàn Quốc', lat: 36.5, lon: 128.0 },
        { name: 'Đài Loan', lat: 23.7, lon: 121.0 },
        { name: 'Ấn Độ', lat: 21.0, lon: 78.0 },
        { name: 'Úc', lat: -25.0, lon: 134.0 },
        { name: 'Nga', lat: 60.0, lon: 100.0 },
        { name: 'Hoa Kỳ', lat: 38.0, lon: -97.0 },
        { name: 'Hoàng Sa (VN)', lat: 16.5, lon: 112.0, isIsland: true },
        { name: 'Trường Sa (VN)', lat: 9.8, lon: 114.5, isIsland: true },
    ];

    // Helper: Convert Lat/Lon (degrees) and Altitude (km) to 3D Cartesian coordinates (Three.js space)
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

    // Helper: Create a high-DPI text billboard sprite
    function createTextSprite(text, options = {}) {
        const THREE = window.THREE;
        if (!THREE) return null;

        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const fontSize = options.fontSize || 32;
        const fontFace = 'Inter, -apple-system, sans-serif';
        ctx.font = `bold ${fontSize}px ${fontFace}`;

        const textWidth = ctx.measureText(text).width;
        canvas.width = Math.ceil(textWidth + 32);
        canvas.height = Math.ceil(fontSize * 1.6);

        // Redraw with configured font after resizing canvas
        ctx.font = `bold ${fontSize}px ${fontFace}`;
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'center';

        // Background pill
        if (options.isMain) {
            ctx.fillStyle = 'rgba(14, 165, 233, 0.28)';
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.roundRect(4, 4, canvas.width - 8, canvas.height - 8, 8);
            ctx.fill();
            ctx.stroke();
        }

        // Text with subtle glow/shadow
        ctx.shadowColor = 'rgba(0, 0, 0, 0.9)';
        ctx.shadowBlur = 6;
        ctx.fillStyle = options.color || (options.isMain ? '#38bdf8' : '#cbd5e1');
        ctx.fillText(text, canvas.width / 2, canvas.height / 2);

        const texture = new THREE.CanvasTexture(canvas);
        texture.minFilter = THREE.LinearFilter;
        texture.magFilter = THREE.LinearFilter;

        const spriteMat = new THREE.SpriteMaterial({
            map: texture,
            transparent: true,
            depthWrite: false,
            opacity: options.opacity || 0.88,
        });

        const sprite = new THREE.Sprite(spriteMat);
        const aspect = canvas.width / canvas.height;
        const scaleY = options.scale || 0.22;
        sprite.scale.set(scaleY * aspect, scaleY, 1);
        return sprite;
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
            this.worldBordersGroup = null;
            this.countryLabelsGroup = null;
            this.satellitesGroup = null;
            this.orbitsGroup = null;
            this.stationsGroup = null;
            this.beamsGroup = null;
            this.activeOrbitMesh = null; // highlighted orbit of selected satellite

            // State
            this.isInitialized = false;
            this.isActive = false;
            this.stations = [];
            this.selectedStation = null;
            this.selectedSatellite = null;
            this.isTrackingSatellite = false;
            this.rawSatellites = [];
            this.satelliteMeshes = new Map(); // satKey -> { mesh, label, data, currentPos }
            this.orbitLines = new Map(); // satKey -> LineLoop
            this.countrySprites = [];

            this.filters = {
                gps: true,
                galileo: true,
                beidou: true,
                glonass: true,
                showOrbits: false, // minimalist by default: only highlight selected satellite's orbit
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
            this.detailCardEl = null;

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
                this._loadWorldCountries();
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

            // Update station markers on Earth surface as clean dots
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
            if (this.detailCardEl && this.detailCardEl.parentNode) {
                this.detailCardEl.parentNode.removeChild(this.detailCardEl);
                this.detailCardEl = null;
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
            this.worldBordersGroup = new THREE.Group();
            this.countryLabelsGroup = new THREE.Group();
            this.vietnamBoundariesGroup = new THREE.Group();

            this.scene.add(this.satellitesGroup);
            this.scene.add(this.orbitsGroup);
            this.scene.add(this.stationsGroup);
            this.scene.add(this.beamsGroup);
            this.scene.add(this.worldBordersGroup);
            this.scene.add(this.countryLabelsGroup);
            this.scene.add(this.vietnamBoundariesGroup);

            // Camera
            this.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
            this.camera.position.set(-2.0, 3.5, -9.5); // Initial view towards Vietnam

            // WebGL Renderer with High-DPI & Anisotropic Filtering
            this.renderer = new THREE.WebGLRenderer({
                antialias: true,
                alpha: false,
                powerPreference: 'high-performance',
            });
            this.renderer.setSize(width, height);
            this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2.5));
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
                this.controls.minDistance = 5.35; // ~250km altitude close-up
                this.controls.maxDistance = 75.0; // Deep space
                this.controls.target.set(0, 0, 0);
            }

            // Lighting
            const ambient = new THREE.AmbientLight(0xffffff, 0.65);
            this.scene.add(ambient);

            const sunLight = new THREE.DirectionalLight(0xffffff, 1.25);
            sunLight.position.set(20, 15, -25);
            this.scene.add(sunLight);

            const secondaryLight = new THREE.DirectionalLight(0x38bdf8, 0.4);
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

            // Pinned Detail Card (shown upon clicking satellite or station)
            this.detailCardEl = document.createElement('div');
            this.detailCardEl.className = 'globe-detail-card';
            this.detailCardEl.style.display = 'none';
            this.container.appendChild(this.detailCardEl);
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

                // Subtle twinkling star tints
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
            const earthGeo = new THREE.SphereGeometry(SCENE_EARTH_RADIUS, 96, 96);

            // Texture loader with anisotropic filtering for maximum sharpness
            const textureLoader = new THREE.TextureLoader();
            const textureUrl = '/assets/img/earth-dark.jpg';
            const earthTexture = textureLoader.load(textureUrl);
            earthTexture.generateMipmaps = true;
            earthTexture.minFilter = THREE.LinearMipmapLinearFilter;
            earthTexture.magFilter = THREE.LinearFilter;
            if (this.renderer) {
                earthTexture.anisotropy = Math.min(16, this.renderer.capabilities.getMaxAnisotropy());
            }

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

        async _loadWorldCountries() {
            try {
                const resp = await fetch('/api/public/world-countries-geojson');
                if (!resp.ok) return;
                const data = await resp.json();
                if (!data || !data.features || !window.THREE) return;

                const THREE = window.THREE;
                this.worldBordersGroup.clear();
                this.countryLabelsGroup.clear();
                this.countrySprites = [];

                const linePoints = [];
                const addRing = (ringCoords) => {
                    if (!Array.isArray(ringCoords) || ringCoords.length < 2) return;
                    for (let i = 0; i < ringCoords.length - 1; i++) {
                        const [lon1, lat1] = ringCoords[i];
                        const [lon2, lat2] = ringCoords[i + 1];
                        linePoints.push(latLonAltToVector3(lat1, lon1, 8));
                        linePoints.push(latLonAltToVector3(lat2, lon2, 8));
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
                    const borderMat = new THREE.LineBasicMaterial({
                        color: 0x2563eb,
                        transparent: true,
                        opacity: 0.38,
                    });
                    const borderLines = new THREE.LineSegments(borderGeo, borderMat);
                    this.worldBordersGroup.add(borderLines);
                }

                // Add Country Text Labels (Việt Nam, Lào, Campuchia, Trung Quốc, etc.)
                KEY_COUNTRY_LABELS.forEach(c => {
                    const sprite = createTextSprite(c.name, {
                        isMain: c.isMain,
                        color: c.isMain ? '#38bdf8' : c.isIsland ? '#f59e0b' : '#cbd5e1',
                        scale: c.isMain ? 0.38 : c.isIsland ? 0.2 : 0.25,
                    });
                    if (sprite) {
                        const pos = latLonAltToVector3(c.lat, c.lon, c.isMain ? 22 : 12);
                        sprite.position.copy(pos);
                        sprite.userData = { normal: pos.clone().normalize() };
                        this.countryLabelsGroup.add(sprite);
                        this.countrySprites.push(sprite);
                    }
                });
            } catch (e) {
                console.warn('[Globe3DView] Could not load world countries GeoJSON:', e);
            }
        }

        async _loadVietnamBoundaries() {
            try {
                const resp = await fetch('/api/public/vietnam-provinces-geojson');
                if (!resp.ok) return;
                const data = await resp.json();
                if (!data || !data.features || !window.THREE) return;

                const THREE = window.THREE;
                this.vietnamBoundariesGroup.clear();
                const lineMat = new THREE.LineBasicMaterial({
                    color: 0x00f2ff,
                    transparent: true,
                    opacity: 0.75,
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
                octahedron: new THREE.OctahedronGeometry(0.16, 0),
            };

            this.rawSatellites.forEach(satItem => {
                const constCfg = CONSTELLATION_CONFIG[satItem.constellationKey] || CONSTELLATION_CONFIG.gps;
                const mat = new THREE.MeshStandardMaterial({
                    color: constCfg.color,
                    emissive: constCfg.color,
                    emissiveIntensity: 0.65,
                    roughness: 0.2,
                    metalness: 0.85,
                });

                const mesh = new THREE.Mesh(sharedGeos.octahedron, mat);
                mesh.userData = {
                    satKey: satItem.key,
                    satItem: satItem,
                    constellation: satItem.constellationKey,
                    code: satItem.code,
                    name: satItem.name,
                };

                // Add subtle outer halo
                const haloGeo = new THREE.SphereGeometry(0.28, 12, 12);
                const haloMat = new THREE.MeshBasicMaterial({
                    color: constCfg.color,
                    transparent: true,
                    opacity: 0.28,
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
                    // Minimalist orbit: ultra-thin and subtle so screen is not cluttered
                    const lineMat = new THREE.LineBasicMaterial({
                        color: constCfg.color,
                        transparent: true,
                        opacity: 0.08,
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

        // Highlight a single satellite's orbit brightly in 3D
        highlightSatelliteOrbit(satItem) {
            const THREE = window.THREE;
            const satLib = window.satellite;
            if (!THREE || !satLib) return;

            // Remove previous highlighted orbit
            if (this.activeOrbitMesh) {
                this.scene.remove(this.activeOrbitMesh);
                if (this.activeOrbitMesh.geometry) this.activeOrbitMesh.geometry.dispose();
                this.activeOrbitMesh = null;
            }

            if (!satItem) return;

            const constCfg = CONSTELLATION_CONFIG[satItem.constellationKey] || CONSTELLATION_CONFIG.gps;
            const now = new Date();
            const sampleCount = 120;
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
                    opacity: 0.85,
                    blending: THREE.AdditiveBlending,
                    linewidth: 2,
                });
                this.activeOrbitMesh = new THREE.LineLoop(lineGeo, lineMat);
                this.scene.add(this.activeOrbitMesh);
            }
        }

        // Update station markers on Earth surface as clean pinpoint dots
        updateStations(stations = [], selectedStation = null) {
            this.stations = stations;
            this.selectedStation = selectedStation;
            const THREE = window.THREE;
            if (!THREE || !this.stationsGroup) return;

            this.stationsGroup.clear();

            // Minimalist dot geometries
            const dotGeo = new THREE.SphereGeometry(0.028, 12, 12);
            const ringGeo = new THREE.RingGeometry(0.038, 0.055, 16);

            stations.forEach(item => {
                const coords = item.coords || (item.device && item.device.latitude && { lat: Number(item.device.latitude), lon: Number(item.device.longitude) });
                if (!coords || !Number.isFinite(coords.lat) || !Number.isFinite(coords.lon)) return;

                const isOnline = item.status === 'online' || item.status === 'streaming';
                const isSelected = selectedStation && (selectedStation.serial === item.device?.serial || selectedStation.name === item.device?.name);
                const color = isSelected ? 0x00f2ff : isOnline ? 0x10b981 : 0x64748b;

                const pos = latLonAltToVector3(coords.lat, coords.lon, 2); // strictly on surface
                const stnGroup = new THREE.Group();
                stnGroup.position.copy(pos);

                // Orient along normal vector
                const normal = pos.clone().normalize();
                const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal);
                stnGroup.quaternion.copy(quat);

                // Small clean dot
                const dotMat = new THREE.MeshStandardMaterial({
                    color,
                    emissive: color,
                    emissiveIntensity: isSelected ? 1.2 : isOnline ? 0.8 : 0.2,
                    roughness: 0.3,
                });
                const dot = new THREE.Mesh(dotGeo, dotMat);
                stnGroup.add(dot);

                // Thin ground ring
                const ringMat = new THREE.MeshBasicMaterial({
                    color,
                    side: THREE.DoubleSide,
                    transparent: true,
                    opacity: isSelected ? 0.9 : 0.35,
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
            this.isTrackingSatellite = false;
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
                const tex = loader.load(path);
                tex.generateMipmaps = true;
                tex.minFilter = window.THREE.LinearMipmapLinearFilter;
                tex.magFilter = window.THREE.LinearFilter;
                if (this.renderer) {
                    tex.anisotropy = Math.min(16, this.renderer.capabilities.getMaxAnisotropy());
                }
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

            if (data?.satItem) {
                // Clicked on a Satellite: Show interactive satellite detail card and highlight its orbit!
                this.selectedSatellite = data.satItem;
                this.highlightSatelliteOrbit(data.satItem);
                this._showSatelliteDetailCard(data.satItem, this.hoveredObject);
            } else if (data?.station) {
                // Clicked on a CORS station: Select station, fire LOS beams & show station card
                this.selectedStation = {
                    lat: data.coords.lat,
                    lon: data.coords.lon,
                    name: data.station.device?.name || data.station.device?.serial,
                    serial: data.station.device?.serial,
                    status: data.station.status,
                    traffic: data.station.traffic,
                };
                if (window.GnssSatelliteOverlay) {
                    window.GnssSatelliteOverlay.setSelectedStation(this.selectedStation);
                }
                this.updateStations(this.stations, this.selectedStation);
                this._showStationDetailCard(data.station, data.coords);
            }
        }

        _showSatelliteDetailCard(satItem, meshObj) {
            if (!this.detailCardEl) return;
            const cfg = CONSTELLATION_CONFIG[satItem.constellationKey] || CONSTELLATION_CONFIG.gps;
            const pos = meshObj.userData?.lastCalculated || {};

            const latStr = Number.isFinite(pos.lat) ? `${pos.lat.toFixed(4)}°` : '--';
            const lonStr = Number.isFinite(pos.lon) ? `${pos.lon.toFixed(4)}°` : '--';
            const altStr = Number.isFinite(pos.altKm) ? `${Math.round(pos.altKm).toLocaleString()} km` : `${cfg.nominalAltKm} km`;
            const elStr = Number.isFinite(pos.elDeg) ? `${pos.elDeg.toFixed(1)}°` : '--';
            const azStr = Number.isFinite(pos.azDeg) ? `${pos.azDeg.toFixed(1)}°` : '--';
            const isVisible = Number.isFinite(pos.elDeg) && pos.elDeg > 5.0;

            // Approximate orbital speed: v = sqrt(GM / r) ~ 3.87 km/s for MEO, 3.07 km/s for GEO
            const rKm = RE_EARTH_KM + (pos.altKm || cfg.nominalAltKm);
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
                        <span>🎯</span> <span>${this.isTrackingSatellite ? 'Hủy bám' : 'Bám theo vệ tinh'}</span>
                    </button>
                    <button type="button" class="card-action-btn" id="globe-reset-view-btn">
                        <span>📍</span> <span>Về Việt Nam</span>
                    </button>
                </div>
            `;

            this.detailCardEl.style.display = 'block';

            // Bind click events on card buttons
            const closeBtn = document.getElementById('globe-card-close');
            if (closeBtn) closeBtn.onclick = () => {
                this.detailCardEl.style.display = 'none';
                this.selectedSatellite = null;
                this.isTrackingSatellite = false;
                if (this.activeOrbitMesh) {
                    this.scene.remove(this.activeOrbitMesh);
                    this.activeOrbitMesh = null;
                }
            };

            const trackBtn = document.getElementById('globe-track-sat-btn');
            if (trackBtn) trackBtn.onclick = () => {
                this.isTrackingSatellite = !this.isTrackingSatellite;
                trackBtn.innerHTML = `<span>🎯</span> <span>${this.isTrackingSatellite ? 'Hủy bám' : 'Bám theo vệ tinh'}</span>`;
            };

            const resetBtn = document.getElementById('globe-reset-view-btn');
            if (resetBtn) resetBtn.onclick = () => {
                this.focusVietnam(true);
            };
        }

        _showStationDetailCard(stationItem, coords) {
            if (!this.detailCardEl) return;
            const dev = stationItem.device || {};
            const isOnline = stationItem.status === 'online' || stationItem.status === 'streaming';
            const statusLabel = isOnline ? 'Trực tuyến (Streaming)' : stationItem.status || 'Offline';
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
                        <span class="stat-label">Serial / Mã</span>
                        <span class="stat-value">${dev.serial || '--'}</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Trạng thái</span>
                        <span class="stat-value" style="color:${statusColor}">${statusLabel}</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Tọa độ (Lat/Lon)</span>
                        <span class="stat-value">${coords.lat.toFixed(4)}°, ${coords.lon.toFixed(4)}°</span>
                    </div>
                    <div class="stat-box">
                        <span class="stat-label">Local VTEC điện ly</span>
                        <span class="stat-value" style="color:#38bdf8">${ionoTec}</span>
                    </div>
                </div>
                <div style="font-size:0.75rem; color:#38bdf8; margin-bottom:10px;">
                    ⚡ Đã kích hoạt tia sóng LOS 3D tới các vệ tinh khả kiến & điểm IPP (350km).
                </div>
                <div class="card-footer-actions">
                    <button type="button" class="card-action-btn" id="globe-reset-view-btn">
                        <span>📍</span> <span>Về Việt Nam</span>
                    </button>
                </div>
            `;

            this.detailCardEl.style.display = 'block';

            const closeBtn = document.getElementById('globe-card-close');
            if (closeBtn) closeBtn.onclick = () => {
                this.detailCardEl.style.display = 'none';
            };

            const resetBtn = document.getElementById('globe-reset-view-btn');
            if (resetBtn) resetBtn.onclick = () => {
                this.focusVietnam(true);
            };
        }

        _showTooltip(target, x, y) {
            if (!this.hoverTooltipEl || !target) return;
            try {
                const sat = target.userData?.satItem;
                const stn = target.userData?.station;

                if (sat) {
                    const cfg = CONSTELLATION_CONFIG[sat.constellationKey] || CONSTELLATION_CONFIG.gps;
                    const pos = target.userData?.lastCalculated || {};
                    const latStr = Number.isFinite(pos.lat) ? `${pos.lat.toFixed(2)}°` : '--';
                    const lonStr = Number.isFinite(pos.lon) ? `${pos.lon.toFixed(2)}°` : '--';
                    const altStr = Number.isFinite(pos.altKm) ? `${Math.round(pos.altKm).toLocaleString()} km` : `${cfg.nominalAltKm} km`;
                    const elLine = Number.isFinite(pos.elDeg) ? `<div><b>Góc ngẩng (El):</b> <span style="color:#38bdf8">${pos.elDeg.toFixed(1)}°</span></div>` : '';

                    this.hoverTooltipEl.innerHTML = `
                        <div class="tooltip-header" style="color:${cfg.hex}">
                            <span>${cfg.flag} <b>${sat.code || '--'}</b> (${cfg.name})</span>
                        </div>
                        <div class="tooltip-body">
                            <div><b>Tên:</b> ${sat.name || sat.code || '--'}</div>
                            <div><b>Vĩ/Kinh:</b> ${latStr}, ${lonStr}</div>
                            <div><b>Độ cao quỹ đạo:</b> ${altStr}</div>
                            ${elLine}
                            <div class="text-info mt-1" style="font-size:0.72rem;">Nhấp để ghim thông tin & xem quỹ đạo</div>
                        </div>
                    `;
                } else if (stn) {
                    const dev = stn.device || {};
                    const coords = target.userData?.coords || {};
                    const latStr = Number.isFinite(coords.lat) ? `${coords.lat.toFixed(4)}°` : '--';
                    const lonStr = Number.isFinite(coords.lon) ? `${coords.lon.toFixed(4)}°` : '--';

                    this.hoverTooltipEl.innerHTML = `
                        <div class="tooltip-header" style="color:#10b981">
                            <span>📡 <b>${dev.name || dev.serial || 'CORS Station'}</b></span>
                        </div>
                        <div class="tooltip-body">
                            <div><b>Serial:</b> ${dev.serial || '--'}</div>
                            <div><b>Trạng thái:</b> ${stn.status || 'online'}</div>
                            <div><b>Tọa độ:</b> ${latStr}, ${lonStr}</div>
                            <div class="text-info mt-1" style="font-size:0.72rem;">Nhấp để kết nối tia vệ tinh & IPP</div>
                        </div>
                    `;
                }

                this.hoverTooltipEl.style.left = `${x + 15}px`;
                this.hoverTooltipEl.style.top = `${y - 25}px`;
                this.hoverTooltipEl.style.display = 'block';
            } catch (err) {
                console.warn('[Globe3DView] Tooltip display error:', err);
            }
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

            // Update Country Labels Visibility (fade out when facing away from camera)
            if (this.camera && this.countrySprites.length > 0) {
                const camDir = this.camera.position.clone().normalize();
                this.countrySprites.forEach(sprite => {
                    const norm = sprite.userData?.normal;
                    if (norm) {
                        const dot = norm.dot(camDir);
                        sprite.visible = dot > 0.15; // Only visible on camera-facing hemisphere
                        if (sprite.material) {
                            sprite.material.opacity = Math.max(0, Math.min(1.0, (dot - 0.15) * 2.5));
                        }
                    }
                });
            }

            // Camera Tracking Satellite
            if (this.isTrackingSatellite && this.selectedSatellite && this.controls) {
                const satRecord = this.satelliteMeshes.get(this.selectedSatellite.key);
                if (satRecord && satRecord.mesh) {
                    this.controls.target.lerp(satRecord.mesh.position, 0.08);
                }
            }

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
                        const dir = pos3D.clone().sub(obsPos3D).normalize();
                        const b = 2 * obsPos3D.dot(dir);
                        const c = obsPos3D.lengthSq() - (SCENE_IONO_RADIUS * SCENE_IONO_RADIUS);
                        const discr = b * b - 4 * c;
                        if (discr > 0) {
                            const t = (-b + Math.sqrt(discr)) / 2;
                            const ippPos = obsPos3D.clone().add(dir.clone().multiplyScalar(t));

                            // Glowing IPP Sphere
                            const ippGeo = new THREE.SphereGeometry(0.08, 12, 12);
                            const ippMat = new THREE.MeshBasicMaterial({
                                color: 0x00f2ff,
                                transparent: true,
                                opacity: 0.9,
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

                    // Update real-time stats if this satellite is currently pinned in detail card
                    if (this.selectedSatellite && this.selectedSatellite.key === key && this.detailCardEl?.style.display === 'block') {
                        const latEl = document.getElementById('card-sat-lat');
                        const lonEl = document.getElementById('card-sat-lon');
                        const altEl = document.getElementById('card-sat-alt');
                        if (latEl && Number.isFinite(lat)) latEl.innerText = `${lat.toFixed(4)}°`;
                        if (lonEl && Number.isFinite(lon)) lonEl.innerText = `${lon.toFixed(4)}°`;
                        if (altEl && Number.isFinite(altKm)) altEl.innerText = `${Math.round(altKm).toLocaleString()} km`;
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
