// 3D Earth Globe WebGL Core (Three.js)
// Renders Earth sphere, atmosphere, country boundaries & labels, controls & interaction
(() => {
    'use strict';

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

    function createCountryTextSprite(text, options = {}) {
        const THREE = window.THREE;
        if (!THREE) return null;

        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const fontSize = options.fontSize || 30;
        ctx.font = `bold ${fontSize}px Inter, -apple-system, sans-serif`;

        const textWidth = ctx.measureText(text).width;
        canvas.width = Math.ceil(textWidth + 24);
        canvas.height = Math.ceil(fontSize * 1.5);

        ctx.font = `bold ${fontSize}px Inter, -apple-system, sans-serif`;
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'center';

        if (options.isMain) {
            ctx.fillStyle = 'rgba(14, 165, 233, 0.25)';
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.roundRect(3, 3, canvas.width - 6, canvas.height - 6, 6);
            ctx.fill();
            ctx.stroke();
        }

        ctx.shadowColor = 'rgba(0, 0, 0, 0.9)';
        ctx.shadowBlur = 4;
        ctx.fillStyle = options.color || (options.isMain ? '#38bdf8' : '#cbd5e1');
        ctx.fillText(text, canvas.width / 2, canvas.height / 2);

        const texture = new THREE.CanvasTexture(canvas);
        texture.minFilter = THREE.LinearFilter;
        texture.magFilter = THREE.LinearFilter;

        const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
        const sprite = new THREE.Sprite(spriteMat);
        const aspect = canvas.width / canvas.height;
        const scaleY = options.scale || 0.2;
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
            this.worldBordersGroup = null;
            this.countryLabelsGroup = null;
            this.vietnamBoundariesGroup = null;
            this.countrySprites = [];

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
                showOrbits: false,
                showBeams: true,
            };

            this.earthTextureMode = 'dark';
            this.textureCache = {};

            // Camera focus animation
            this.isTweening = false;
            this.tweenStartTime = 0;
            this.tweenDuration = 1000;
            this.tweenStartPos = null;
            this.tweenEndPos = null;
            this.tweenStartTarget = null;
            this.tweenEndTarget = null;

            // Raycaster
            this.raycaster = null;
            this.mouse = null;
            this.hoveredObject = null;
            this.hoverTooltipEl = null;

            this._onResize = this._onResize.bind(this);
            this._onPointerMove = this._onPointerMove.bind(this);
            this._onPointerClick = this._onPointerClick.bind(this);
            this._animate = this._animate.bind(this);
        }

        attach(containerEl, options = {}) {
            if (!containerEl || !window.THREE) return;
            this.container = containerEl;

            if (!this.isInitialized) {
                this._initThreeScene();
                this._createStarfield();
                this._createEarth();
                this._loadWorldCountries();
                this._loadVietnamBoundaries();
                if (window.Globe3DSatelliteLayer) {
                    this.satelliteLayer = new window.Globe3DSatelliteLayer(this);
                }
                this.isInitialized = true;
            }

            this.isActive = true;
            this.container.style.display = 'block';
            this._onResize();

            if (window.ResizeObserver && !this.resizeObserver) {
                this.resizeObserver = new ResizeObserver(() => this._onResize());
                this.resizeObserver.observe(this.container);
            }

            if (this.satelliteLayer && options.stations) {
                this.satelliteLayer.updateStations(options.stations, options.selectedStation);
            }

            this.focusVietnam(false);

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
            if (this.hoverTooltipEl?.parentNode) {
                this.hoverTooltipEl.parentNode.removeChild(this.hoverTooltipEl);
                this.hoverTooltipEl = null;
            }
            if (this.satelliteLayer) {
                this.satelliteLayer.closeDetailCard();
            }
        }

        _initThreeScene() {
            const THREE = window.THREE;
            const width = this.container.clientWidth || window.innerWidth;
            const height = this.container.clientHeight || (window.innerHeight - 88);

            this.scene = new THREE.Scene();
            this.worldBordersGroup = new THREE.Group();
            this.countryLabelsGroup = new THREE.Group();
            this.vietnamBoundariesGroup = new THREE.Group();

            this.scene.add(this.worldBordersGroup);
            this.scene.add(this.countryLabelsGroup);
            this.scene.add(this.vietnamBoundariesGroup);

            this.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
            this.camera.position.set(-2.0, 3.5, -9.5);

            this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
            this.renderer.setSize(width, height);
            this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2.0));
            this.renderer.setClearColor(0x020611, 1.0);
            this.container.innerHTML = '';
            this.container.appendChild(this.renderer.domElement);
            this.renderer.domElement.className = 'station-map-globe-canvas-element';

            if (THREE.OrbitControls) {
                this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
                this.controls.enableDamping = true;
                this.controls.dampingFactor = 0.08;
                this.controls.rotateSpeed = 0.75;
                this.controls.zoomSpeed = 1.15;
                this.controls.minDistance = 5.35;
                this.controls.maxDistance = 75.0;
                this.controls.target.set(0, 0, 0);
            }

            this.scene.add(new THREE.AmbientLight(0xffffff, 0.65));
            const sunLight = new THREE.DirectionalLight(0xffffff, 1.2);
            sunLight.position.set(20, 15, -25);
            this.scene.add(sunLight);

            this.raycaster = new THREE.Raycaster();
            this.mouse = new THREE.Vector2();

            this.renderer.domElement.addEventListener('pointermove', this._onPointerMove);
            this.renderer.domElement.addEventListener('click', this._onPointerClick);
            window.addEventListener('resize', this._onResize);

            this.hoverTooltipEl = document.createElement('div');
            this.hoverTooltipEl.className = 'globe-satellite-tooltip';
            this.hoverTooltipEl.style.display = 'none';
            this.container.appendChild(this.hoverTooltipEl);
        }

        _createStarfield() {
            const THREE = window.THREE;
            const starCount = 2000;
            const geometry = new THREE.BufferGeometry();
            const positions = new Float32Array(starCount * 3);

            for (let i = 0; i < starCount; i++) {
                const radius = 180 + Math.random() * 100;
                const theta = Math.random() * Math.PI * 2;
                const phi = Math.acos(2 * Math.random() - 1);
                positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
                positions[i * 3 + 1] = radius * Math.cos(phi);
                positions[i * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta);
            }

            geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
            const material = new THREE.PointsMaterial({ size: 1.1, color: 0xcfe8ff, transparent: true, opacity: 0.75, depthWrite: false });
            this.starfield = new THREE.Points(geometry, material);
            this.scene.add(this.starfield);
        }

        _createEarth() {
            const THREE = window.THREE;
            const Math3D = window.Globe3DMath;
            const R = Math3D?.SCENE_EARTH_RADIUS || 5.0;

            const earthGeo = new THREE.SphereGeometry(R, 96, 96);
            const textureLoader = new THREE.TextureLoader();
            const tex = textureLoader.load('/assets/img/earth-dark.jpg');
            tex.generateMipmaps = true;
            tex.minFilter = THREE.LinearMipmapLinearFilter;
            tex.magFilter = THREE.LinearFilter;
            if (this.renderer) tex.anisotropy = Math.min(16, this.renderer.capabilities.getMaxAnisotropy());

            const earthMat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.72, metalness: 0.1 });
            this.earthMesh = new THREE.Mesh(earthGeo, earthMat);
            this.scene.add(this.earthMesh);

            // Subtle atmospheric blue rim
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
                        float intensity = pow(0.68 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.8);
                        gl_FragColor = vec4(0.18, 0.68, 1.0, 1.0) * intensity * 1.3;
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

                const THREE = window.THREE;
                const Math3D = window.Globe3DMath;
                this.worldBordersGroup.clear();
                this.countryLabelsGroup.clear();
                this.countrySprites = [];

                const linePoints = [];
                const addRing = (ringCoords) => {
                    if (!Array.isArray(ringCoords) || ringCoords.length < 2) return;
                    for (let i = 0; i < ringCoords.length - 1; i++) {
                        const [lon1, lat1] = ringCoords[i];
                        const [lon2, lat2] = ringCoords[i + 1];
                        linePoints.push(Math3D.latLonAltToVector3(lat1, lon1, 6));
                        linePoints.push(Math3D.latLonAltToVector3(lat2, lon2, 6));
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
                    const borderMat = new THREE.LineBasicMaterial({ color: 0x2563eb, transparent: true, opacity: 0.35 });
                    this.worldBordersGroup.add(new THREE.LineSegments(borderGeo, borderMat));
                }

                // Add country labels
                KEY_COUNTRY_LABELS.forEach(c => {
                    const sprite = createCountryTextSprite(c.name, {
                        isMain: c.isMain,
                        color: c.isMain ? '#38bdf8' : c.isIsland ? '#f59e0b' : '#cbd5e1',
                        scale: c.isMain ? 0.35 : c.isIsland ? 0.18 : 0.22,
                    });
                    if (sprite) {
                        const pos = Math3D.latLonAltToVector3(c.lat, c.lon, c.isMain ? 18 : 10);
                        sprite.position.copy(pos);
                        sprite.userData = { normal: pos.clone().normalize() };
                        this.countryLabelsGroup.add(sprite);
                        this.countrySprites.push(sprite);
                    }
                });
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
                const lineMat = new THREE.LineBasicMaterial({ color: 0x00f2ff, transparent: true, opacity: 0.8 });

                data.features.forEach(feat => {
                    const geom = feat.geometry;
                    if (!geom) return;

                    const addLine = (ringCoords) => {
                        if (!Array.isArray(ringCoords) || ringCoords.length < 2) return;
                        const points = [];
                        for (let i = 0; i < ringCoords.length; i++) {
                            const [lon, lat] = ringCoords[i];
                            points.push(Math3D.latLonAltToVector3(lat, lon, 12));
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

        focusVietnam(animate = true) {
            const Math3D = window.Globe3DMath;
            const normal = (Math3D?.latLonAltToVector3(16.0, 108.0, 0) || new window.THREE.Vector3(-1.48, 1.38, -4.57)).normalize();
            const targetPos = new window.THREE.Vector3(normal.x * 9.8, normal.y * 9.8 + 1.2, normal.z * 9.8);
            const targetLook = new window.THREE.Vector3(normal.x * 2.5, normal.y * 2.5, normal.z * 2.5);

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

        setFilter(key, value) {
            this.filters[key] = value;
            if (key === 'showOrbits' && this.satelliteLayer?.singleOrbitGroup) {
                this.satelliteLayer.singleOrbitGroup.visible = !!value;
            }
            if (key === 'showBeams' && this.satelliteLayer?.beamsGroup) {
                this.satelliteLayer.beamsGroup.visible = !!value;
            }
        }

        setSimSpeed(speed) {
            // Realtime 1Hz synchronized mode
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
            ];

            const intersects = this.raycaster.intersectObjects(targets, true);
            if (intersects.length > 0) {
                let target = intersects[0].object;
                while (target && !target.userData?.satItem && !target.userData?.station && target.parent) {
                    target = target.parent;
                }
                if (target && (target.userData?.satItem || target.userData?.station)) {
                    this.hoveredObject = target;
                    this.renderer.domElement.style.cursor = 'pointer';
                    return;
                }
            }
            this.hoveredObject = null;
            this.renderer.domElement.style.cursor = 'grab';
        }

        _onPointerClick(event) {
            if (!this.hoveredObject) return;
            const data = this.hoveredObject.userData;

            if (data?.satItem && this.satelliteLayer) {
                this.satelliteLayer.openSatelliteCard(data.satItem, this.hoveredObject);
            } else if (data?.station && this.satelliteLayer) {
                const dev = data.station.device || {};
                const stnObj = {
                    lat: data.coords.lat,
                    lon: data.coords.lon,
                    name: dev.name || dev.serial,
                    serial: dev.serial,
                };
                if (window.GnssSatelliteOverlay) {
                    window.GnssSatelliteOverlay.setSelectedStation(stnObj);
                }
                this.satelliteLayer.updateStations(this.satelliteLayer.stations || [], stnObj);
                this.satelliteLayer.openStationCard(data.station, data.coords);
            }
        }

        _animate(now) {
            if (!this.isActive) return;
            this.animFrameId = requestAnimationFrame(this._animate);

            if (this.isTweening) {
                const progress = Math.min((now - this.tweenStartTime) / this.tweenDuration, 1.0);
                const ease = 1.0 - Math.pow(1.0 - progress, 3);
                this.camera.position.lerpVectors(this.tweenStartPos, this.tweenEndPos, ease);
                if (this.controls && this.tweenStartTarget && this.tweenEndTarget) {
                    this.controls.target.lerpVectors(this.tweenStartTarget, this.tweenEndTarget, ease);
                }
                if (progress >= 1.0) this.isTweening = false;
            }

            // Country labels visibility on camera-facing hemisphere
            if (this.camera && this.countrySprites.length > 0) {
                const camDir = this.camera.position.clone().normalize();
                this.countrySprites.forEach(sprite => {
                    const norm = sprite.userData?.normal;
                    if (norm) {
                        const dot = norm.dot(camDir);
                        sprite.visible = dot > 0.15;
                        if (sprite.material) sprite.material.opacity = Math.max(0, Math.min(1.0, (dot - 0.15) * 2.5));
                    }
                });
            }

            // Camera Tracking Satellite
            if (this.isTrackingSatellite && this.satelliteLayer?.selectedSatellite && this.controls) {
                const satRec = this.satelliteLayer.satelliteMeshes.get(this.satelliteLayer.selectedSatellite.key);
                if (satRec?.mesh) this.controls.target.lerp(satRec.mesh.position, 0.08);
            }

            if (this.controls) this.controls.update();
            if (this.renderer && this.scene && this.camera) this.renderer.render(this.scene, this.camera);
        }
    }

    window.Globe3DView = new Globe3DView();
})();
