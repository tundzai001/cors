// Native globe-to-street camera for the production station map.
(() => {
    'use strict';
    let sdkRequest;
    function loadSdk() {
        if (window.mapboxgl) return Promise.resolve(window.mapboxgl);
        if (!sdkRequest) sdkRequest = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = '/assets/js/dist/mapbox-gl-sdk.min.js?v=3.9.3';
            script.onload = () => resolve(window.mapboxgl);
            script.onerror = () => { sdkRequest = null; script.remove(); reject(new Error('Không tải được thư viện bản đồ.')); };
            document.head.append(script);
        });
        return sdkRequest;
    }
    const reducedMotion = () => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
    const spinStep = zoom => 3 * Math.max(0, Math.min(1, (5 - zoom) / 2));

    class MapboxGlobeView {
        constructor() {
            this.map = null;
            this.generation = 0;
            this.stations = [];
            this.spinning = false;
            this.flying = false;
            this.interacting = false;
            this.ready = false;
            this.resumeTimer = null;
            this._onVisibility = () => {
                if (!this.map) return;
                if (document.hidden) { this.flying = false; this.map.stop(); } else this._rotate();
            };
            this._resume = () => {
                clearTimeout(this.resumeTimer);
                this.resumeTimer = setTimeout(() => { this.interacting = false; this._rotate(); }, 650);
            };
        }

        async attach(container, options = {}) {
            this.detach();
            this.container = container;
            this.options = options;
            this.stations = options.stations || [];
            this.selectedStation = options.selectedStation;
            const generation = this.generation;
            this._status('Đang tải địa cầu…');
            try {
                const [sdk, response] = await Promise.all([
                    loadSdk(), (window.apiFetch || fetch)('/api/public/map-config'),
                ]);
                if (generation !== this.generation) return;
                if (!response.ok) throw new Error('Chưa tải được cấu hình bản đồ.');
                const config = await response.json();
                if (generation !== this.generation) return;
                if (!String(config.mapbox_public_token || '').startsWith('pk.')) throw new Error('Chưa cấu hình bản đồ Mapbox.');
                if (!sdk?.supported()) throw new Error('Trình duyệt chưa hỗ trợ địa cầu WebGL.');
                const center = options.center || { lat: 16.2, lng: 106.3 };
                const mapHost = document.createElement('div');
                mapHost.className = 'mapbox-globe-map';
                this.mapHost = mapHost;
                this.container.prepend(mapHost);
                this.map = new sdk.Map({
                    container: mapHost, accessToken: config.mapbox_public_token,
                    style: 'mapbox://styles/mapbox/satellite-streets-v12', projection: 'globe',
                    center: [center.lng, center.lat], zoom: options.autoZoom ? Math.max(0, options.zoom ?? 2) : 1.5,
                    minZoom: 0, maxZoom: 19, maxPitch: 65, renderWorldCopies: false,
                    attributionControl: { compact: true }, fadeDuration: reducedMotion() ? 0 : 300,
                    locale: { 'NavigationControl.ZoomIn': 'Phóng to', 'NavigationControl.ZoomOut': 'Thu nhỏ', 'NavigationControl.ResetBearing': 'Hướng Bắc' },
                });
                const map = this.map;
                map.addControl(new sdk.NavigationControl({ visualizePitch: true }), 'top-right');
                map.scrollZoom.setWheelZoomRate(1 / 200);
                map.on('style.load', () => {
                    if (this.map !== map) return;
                    map.setFog({ color: 'rgb(186, 210, 235)', 'high-color': 'rgb(36, 92, 223)',
                        'horizon-blend': 0.002, 'space-color': 'rgb(11, 11, 25)', 'star-intensity': 0.1 });
                    map.addSource('cors-stations', { type: 'geojson', data: this._stationData() });
                    map.addLayer({ id: 'cors-station-selected', type: 'circle', source: 'cors-stations',
                        filter: ['==', ['get', 'serial'], this.selectedStation?.serial || ''],
                        paint: { 'circle-radius': 13, 'circle-color': '#38bdf8', 'circle-opacity': 0.2,
                            'circle-stroke-color': '#7dd3fc', 'circle-stroke-width': 1.5 } });
                    map.addLayer({ id: 'cors-station-pins', type: 'circle', source: 'cors-stations', paint: {
                        'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 4, 8, 7],
                        'circle-color': ['match', ['get', 'status'], 'online', '#22c55e', 'streaming', '#22c55e', 'sleep', '#38bdf8', 'offline', '#ef4444', 'lost', '#ef4444', '#94a3b8'],
                        'circle-stroke-color': '#0f172a', 'circle-stroke-width': 1.5,
                    } });
                    map.on('click', 'cors-station-pins', event => {
                        const serial = event.features?.[0]?.properties?.serial;
                        const station = this.stations.find(item => item.device.serial === serial);
                        if (station) this._openStation(station);
                    });
                    map.on('mouseenter', 'cors-station-pins', () => { map.getCanvas().style.cursor = 'pointer'; });
                    map.on('mouseleave', 'cors-station-pins', () => { map.getCanvas().style.cursor = ''; });
                });
                map.on('load', () => {
                    if (this.map !== map || this.accessDenied) return;
                    this.ready = true; this._clearStatus();
                    this.setSpinning(!reducedMotion());
                });
                map.on('moveend', event => {
                    if (event.corsFlight) this.flying = false;
                    this._rotate();
                });
                map.on('error', event => {
                    if (this.map !== map) return;
                    if (event.error?.status === 401 || event.error?.status === 403) {
                        this.accessDenied = true; this.ready = false; this.setSpinning(false);
                        this._status('Mapbox chưa cho phép tải bản đồ tại địa chỉ này.', true);
                    } else if (!this.ready) this._status('Chưa tải được ảnh bản đồ. Vui lòng kiểm tra kết nối.', true);
                });
                const pause = () => {
                    this.interacting = true; this.flying = false;
                    clearTimeout(this.resumeTimer); map.stop();
                };
                const canvas = map.getCanvas();
                canvas.addEventListener('pointerdown', pause);
                canvas.addEventListener('wheel', () => { pause(); this._resume(); }, { passive: true });
                canvas.addEventListener('keydown', () => { pause(); this._resume(); });
                canvas.addEventListener('focus', () => this.setSpinning(false));
                window.addEventListener('pointerup', this._resume);
                window.addEventListener('pointercancel', this._resume);
                document.addEventListener('visibilitychange', this._onVisibility);
            } catch (error) {
                if (generation === this.generation) this._status(error.message || 'Chưa mở được địa cầu.', true);
            }
        }

        _status(message, error = false) {
            if (!this.container) return;
            this._clearStatus();
            const status = document.createElement('div');
            status.className = 'mapbox-globe-status'; status.setAttribute('role', error ? 'alert' : 'status');
            const text = document.createElement('span'); text.textContent = message; status.append(text);
            if (error) {
                const button = document.createElement('button'); button.type = 'button';
                button.className = 'btn btn-sm btn-outline-light'; button.textContent = 'Về bản đồ 2D';
                button.addEventListener('click', () => this.options.onSurfaceView?.(this.getSurfaceView()));
                status.append(button);
            } else {
                const spinner = document.createElement('span'); spinner.className = 'spinner-border spinner-border-sm';
                spinner.setAttribute('aria-hidden', 'true'); status.prepend(spinner);
            }
            this.status = status; this.container.append(status);
        }
        _clearStatus() { this.status?.remove(); this.status = null; }

        _stationData() {
            return { type: 'FeatureCollection', features: this.stations
                .filter(item => Number.isFinite(item.coords?.lat) && Number.isFinite(item.coords?.lon))
                .map(({ device, coords, status }) => ({ type: 'Feature', properties: {
                    serial: String(device.serial || ''), name: String(device.name || device.serial || ''), status,
                }, geometry: { type: 'Point', coordinates: [coords.lon, coords.lat] } })) };
        }
        updateStations(stations, selectedStation) {
            this.stations = stations || []; this.selectedStation = selectedStation;
            this.map?.getSource('cors-stations')?.setData(this._stationData());
            if (this.map?.getLayer('cors-station-selected')) this.map.setFilter('cors-station-selected', ['==', ['get', 'serial'], selectedStation?.serial || '']);
        }
        _openStation(item) {
            const { device, coords } = item;
            this.flyTo(coords.lat, coords.lon, 11);
            this.popup?.remove();
            const content = document.createElement('div'); content.className = 'mapbox-station-card';
            const name = document.createElement('strong'); name.textContent = device.name || device.serial;
            const coordinates = document.createElement('p'); coordinates.textContent = `${coords.lat.toFixed(5)}°, ${coords.lon.toFixed(5)}°`;
            const button = document.createElement('button'); button.type = 'button';
            button.className = 'btn btn-sm btn-primary'; button.textContent = 'Theo dõi vệ tinh';
            if (['offline', 'lost'].includes(item.status)) {
                button.disabled = true; button.textContent = 'Trạm ngoại tuyến';
            }
            button.addEventListener('click', () => this.options.onTrackStation?.({
                ...device, lat: coords.lat, lon: coords.lon, status: item.status,
            }));
            content.append(name, coordinates, button);
            this.popup = new window.mapboxgl.Popup({ offset: 12, closeButton: true })
                .setLngLat([coords.lon, coords.lat]).setDOMContent(content).addTo(this.map);
        }
        flyTo(lat, lng, zoom = 11) {
            if (!this.ready) return;
            this.interacting = true; this.map.stop(); this.interacting = false; this.flying = true;
            this.map.flyTo({ center: [lng, lat], zoom, bearing: 0, pitch: 0, speed: 1.15, curve: 1.42,
                maxDuration: 5000, ...(reducedMotion() ? { duration: 0 } : {}) }, { corsFlight: true });
        }
        focusVietnam(animate = true, distance = 7.8) {
            if (distance >= 90) {
                const center = this.map?.getCenter();
                this.flyTo(center?.lat ?? 16, center?.lng ?? 106, 1.5);
            } else this.flyTo(16.2, 106.3, 5.2);
        }
        setSpinning(enabled) {
            this.spinning = Boolean(enabled && !reducedMotion());
            if (!this.spinning) this.map?.stop();
            this.options?.onSpinChange?.(this.spinning);
            this._rotate();
        }
        _rotate() {
            if (!this.ready || !this.spinning || this.flying || this.interacting || document.hidden || reducedMotion() || this.map.isMoving()) return;
            const step = spinStep(this.map.getZoom());
            if (!step) return;
            const center = this.map.getCenter();
            this.map.easeTo({ center: [center.lng - step, center.lat], duration: 1000, easing: t => t });
        }
        getSurfaceView() {
            if (!this.map) return null;
            const center = this.map.getCenter();
            return { lat: center.lat, lng: ((center.lng + 180) % 360 + 360) % 360 - 180, zoom: this.map.getZoom() };
        }
        detach() {
            this.generation++;
            clearTimeout(this.resumeTimer);
            window.removeEventListener('pointerup', this._resume);
            window.removeEventListener('pointercancel', this._resume);
            document.removeEventListener('visibilitychange', this._onVisibility);
            this.ready = false; this.spinning = false; this.flying = false; this.interacting = false; this.accessDenied = false;
            this.popup?.remove(); this.popup = null;
            this.map?.remove(); this.map = null;
            this.mapHost?.remove(); this.mapHost = null;
            this._clearStatus(); this.container = null;
        }
    }
    window.MapboxGlobeView = new MapboxGlobeView();
})();
