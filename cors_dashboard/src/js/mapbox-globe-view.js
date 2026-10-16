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
            this.active = true;
            this.projection = 'globe';
            this.layerState = {};
            this.featureData = {}; this.selectedProvinceCode = '';
            this.tileErrors = 0;
            this.styleName = null; this.gnssFrame = null; this.rasterKeys = {};
            this.measureEnabled = false; this.measurePoints = []; this.measureFeatures = [];
            this._status('Đang tải bản đồ…');
            this.loadTimer = setTimeout(() => this._fail('Mapbox tải quá thời gian chờ.'), 15000);
            try {
                const [sdk, response] = await Promise.all([
                    loadSdk(), (window.apiFetch || fetch)('/api/public/map-config'),
                ]);
                if (generation !== this.generation || this.failed) return;
                if (!response.ok) throw new Error('Chưa tải được cấu hình bản đồ.');
                const config = await response.json();
                if (generation !== this.generation || this.failed) return;
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
                    center: [center.lng, center.lat], zoom: options.zoom ?? 6,
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
                        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2,
                    } });
                    this._installLayers();
                });
                map.on('click', 'cors-station-pins', event => {
                    const serial = event.features?.[0]?.properties?.serial;
                    const station = this.stations.find(item => item.device.serial === serial);
                    if (station) this._openStation(station);
                });
                map.on('mouseenter', 'cors-station-pins', () => { map.getCanvas().style.cursor = 'pointer'; });
                map.on('mouseleave', 'cors-station-pins', () => { map.getCanvas().style.cursor = ''; });
                map.on('load', () => {
                    if (this.map !== map || this.failed) return;
                    this.ready = true; this._clearStatus();
                    clearTimeout(this.loadTimer);
                    this.options.onReady?.(map);
                });
                map.on('moveend', event => {
                    if (event.corsFlight) this.flying = false;
                    this._rotate();
                });
                map.on('error', event => {
                    if (this.map !== map) return;
                    if (String(event.error?.url || '').startsWith('https://events.mapbox.com/')) return;
                    if (event.error?.status === 401 || event.error?.status === 403) {
                        this.accessDenied = true; this.ready = false; this.setSpinning(false);
                        this._fail('Mapbox không chấp nhận token hoặc địa chỉ hiện tại.');
                    } else if (!this.ready || ++this.tileErrors >= 3) this._fail('Chưa tải được ảnh bản đồ Mapbox.');
                });
                map.on('sourcedata', event => { if (event.isSourceLoaded) this.tileErrors = 0; });
                map.on('click', event => this._mapClick(event));
                map.on('click', 'cors-provinces-fill', event => {
                    if (!this.measureEnabled && event.features?.[0] && !this._hasPointFeature(event)) this.fitProvince(event.features[0].properties.code, { fit: false, lngLat: event.lngLat });
                });
                map.on('mouseenter', 'cors-provinces-fill', () => { if (!this.measureEnabled) map.getCanvas().style.cursor = 'pointer'; });
                map.on('mouseleave', 'cors-provinces-fill', () => { map.getCanvas().style.cursor = this.measureEnabled ? 'crosshair' : ''; });
                map.on('click', 'cors-island-labels', event => {
                    const p = event.features?.[0]?.properties;
                    if (p && !this.measureEnabled) this._textPopup(event.lngLat, p.name.replace('\n', ' · '), p.kind === 'island' ? 'Quần đảo thuộc chủ quyền Việt Nam' : 'Biển Đông');
                });
                map.on('click', 'cors-rover-pins', event => {
                    const properties = event.features?.[0]?.properties;
                    if (properties && !this.measureEnabled) this._textPopup(event.lngLat, `Rover: ${properties.username}`, `Mountpoint: ${properties.mountpoint} · ${properties.statusLabel} · ${properties.nsv} vệ tinh`);
                });
                map.on('click', 'cors-rover-clusters', event => {
                    if (this.measureEnabled) return;
                    const feature = event.features?.[0];
                    if (feature) map.getSource('cors-rovers').getClusterExpansionZoom(feature.properties.cluster_id, (error, zoom) => {
                        if (!error && this.map === map) this.flyTo(feature.geometry.coordinates[1], feature.geometry.coordinates[0], zoom);
                    });
                });
                for (const id of ['cors-gnss-points', 'cors-gnss-ipp']) map.on('click', id, event => {
                    const properties = event.features?.[0]?.properties;
                    if (properties && !this.measureEnabled) this._textPopup(event.lngLat, properties.code, properties.kind === 'ipp' ? `IPP 350 km · VTEC ${properties.vtec ?? '--'} TECU` : `${properties.name} · NORAD ${properties.noradId} · ${Math.round(properties.altKm)} km · ${properties.identityStatus === 'retired' ? 'Đã ngừng khai thác · không ghép RTCM' : properties.identityKnown === false ? 'Chưa xác nhận PRN hiện hành; không ghép RTCM' : properties.tracked ? 'Agent đang nhận RTCM' : 'Không có PRN RTCM khớp'}`);
                });
                const pause = () => {
                    this.interacting = true; this.flying = false;
                    clearTimeout(this.resumeTimer);
                };
                const canvas = map.getCanvas();
                canvas.addEventListener('webglcontextlost', () => this._fail('WebGL bị gián đoạn.'));
                canvas.addEventListener('pointerdown', () => { pause(); map.stop(); });
                canvas.addEventListener('wheel', () => { pause(); this._resume(); }, { passive: true });
                canvas.addEventListener('keydown', () => { pause(); this._resume(); });
                canvas.addEventListener('focus', () => this.setSpinning(false));
                window.addEventListener('pointerup', this._resume);
                window.addEventListener('pointercancel', this._resume);
                document.addEventListener('visibilitychange', this._onVisibility);
            } catch (error) {
                if (generation === this.generation) this._fail(error.message || 'Chưa mở được bản đồ.');
            }
        }

        _fail(reason) {
            if (this.failed || !this.container) return;
            this.failed = true; this.ready = false;
            clearTimeout(this.loadTimer);
            this.setSpinning(false);
            if (this.options.onFailure) this.options.onFailure({ reason, view: this.getSurfaceView() });
            else this._status(reason, true);
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
            if (!this.active) return;
            this.map?.getSource('cors-stations')?.setData(this._stationData());
            this._updateCoverage();
            if (this.map?.getLayer('cors-station-selected')) this.map.setFilter('cors-station-selected', ['==', ['get', 'serial'], selectedStation?.serial || '']);
        }
        _openStation(item) {
            const { device, coords } = item;
            if (this.measureEnabled) return;
            this.flyTo(coords.lat, coords.lon, 11);
            this.popup?.remove();
            const content = document.createElement('div'); content.className = 'mapbox-station-card';
            if (this.options.stationPopup) {
                content.innerHTML = this.options.stationPopup(item);
                content.querySelector('.station-map-sat-btn')?.addEventListener('click', () => this.options.onTrackStation?.({ ...device, lat: coords.lat, lon: coords.lon, status: item.status }));
                content.querySelector('.station-map-detail-btn')?.addEventListener('click', () => this.options.onOpenDetail?.(device.serial));
                this._popup(content, [coords.lon, coords.lat]);
                return;
            }
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
            this._popup(content, [coords.lon, coords.lat]);
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
            if (!this.ready || !this.active || this.projection !== 'globe' || !this.spinning || this.flying || this.interacting || document.hidden || reducedMotion() || this.map.isMoving()) return;
            const step = spinStep(this.map.getZoom());
            if (!step) return;
            const center = this.map.getCenter();
            this.map.easeTo({ center: [center.lng - step, center.lat], duration: 1000, easing: t => t });
        }
        setProjection(projection) {
            this.projection = projection;
            if (this.map?.getSource('cors-stations')) this.map.setProjection(projection);
            if (projection !== 'globe') this.setSpinning(false);
        }
        setActive(active) {
            this.active = active;
            if (!active) { this.popup?.remove(); this.interacting = true; this.map?.stop(); this.interacting = false; }
            else { this.map?.resize(); this.updateStations(this.stations, this.selectedStation); this.updateLayers(this.layerState); this._rotate(); }
        }
        _data(id, features) { this.featureData ||= {}; this.featureData[id] = features; this.map?.getSource(id)?.setData({ type: 'FeatureCollection', features }); }
        _visibility(id, visible) { if (this.map?.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none'); }
        _installLayers() {
            const map = this.map;
            const empty = { type: 'FeatureCollection', features: [] };
            for (const id of ['cors-coverage', 'cors-provinces', 'cors-mask', 'cors-search', 'cors-measure', 'cors-gnss', 'cors-islands']) map.addSource(id, { type: 'geojson', data: { ...empty, features: this.featureData?.[id] || [] } });
            map.addSource('cors-rovers', { type: 'geojson', data: empty, cluster: true, clusterRadius: 45, clusterMaxZoom: 13 });
            const before = 'cors-station-selected';
            const layer = (id, type, source, paint, filter) => map.addLayer({ id, type, source, paint, ...(filter ? { filter } : {}) }, before);
            layer('cors-mask-fill', 'fill', 'cors-mask', { 'fill-color': '#070b14', 'fill-opacity': 1 });
            layer('cors-provinces-fill', 'fill', 'cors-provinces', { 'fill-color': '#38bdf8', 'fill-opacity': 0.04 });
            layer('cors-provinces-halo', 'line', 'cors-provinces', { 'line-color': '#0c2540', 'line-width': 5, 'line-opacity': 0.6 });
            layer('cors-provinces-line', 'line', 'cors-provinces', { 'line-color': '#38bdf8', 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 1.4, 8, 2.5], 'line-opacity': 0.95 });
            const selected = ['==', ['to-string', ['get', 'code']], this.selectedProvinceCode || ''];
            layer('cors-province-selected-fill', 'fill', 'cors-provinces', { 'fill-color': '#0ea5e9', 'fill-opacity': 0.15 }, selected);
            layer('cors-province-selected-line', 'line', 'cors-provinces', { 'line-color': '#fbbf24', 'line-width': 3.5, 'line-opacity': 1 }, selected);
            layer('cors-coverage-fill', 'fill', 'cors-coverage', { 'fill-color': ['get', 'color'], 'fill-opacity': 0.12 });
            layer('cors-coverage-line', 'line', 'cors-coverage', { 'line-color': ['get', 'color'], 'line-width': 2, 'line-opacity': 0.6 });
            layer('cors-search-line', 'line', 'cors-search', { 'line-color': ['get', 'color'], 'line-width': 3, 'line-dasharray': [3, 3] }, ['==', ['geometry-type'], 'LineString']);
            layer('cors-search-point', 'circle', 'cors-search', { 'circle-color': '#38bdf8', 'circle-radius': 7, 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' }, ['==', ['geometry-type'], 'Point']);
            layer('cors-measure-line', 'line', 'cors-measure', { 'line-color': '#38bdf8', 'line-width': 3 }, ['==', ['geometry-type'], 'LineString']);
            layer('cors-measure-point', 'circle', 'cors-measure', { 'circle-color': '#38bdf8', 'circle-radius': 5 }, ['==', ['geometry-type'], 'Point']);
            layer('cors-gnss-rays', 'line', 'cors-gnss', { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-opacity': 0.65 }, ['==', ['get', 'kind'], 'ray']);
            layer('cors-gnss-points', 'circle', 'cors-gnss', { 'circle-color': ['get', 'color'], 'circle-radius': ['case', ['get', 'tracked'], 6, 3], 'circle-stroke-width': 1, 'circle-stroke-color': '#0f172a' }, ['==', ['get', 'kind'], 'satellite']);
            map.addLayer({ id: 'cors-gnss-labels', type: 'symbol', source: 'cors-gnss', filter: ['==', ['get', 'kind'], 'satellite'], layout: { 'text-field': ['get', 'code'], 'text-size': 12, 'text-offset': [0, 1.2] }, paint: { 'text-color': '#f8fafc', 'text-halo-color': '#0f172a', 'text-halo-width': 1.5 } });
            layer('cors-island-pins', 'circle', 'cors-islands', { 'circle-color': '#ef4444', 'circle-radius': 5, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 }, ['==', ['get', 'kind'], 'island']);
            map.addLayer({ id: 'cors-island-labels', type: 'symbol', source: 'cors-islands', minzoom: 2, layout: { 'text-field': ['get', 'name'], 'text-size': ['interpolate', ['linear'], ['zoom'], 2, 12, 7, 16], 'text-offset': [0, 1.7], 'text-line-height': 1.35, 'text-max-width': 16, 'text-allow-overlap': true }, paint: { 'text-color': '#ffffff', 'text-halo-color': '#075985', 'text-halo-width': 2.5 } });
            this._installMarkerImages();
            map.addLayer({ id: 'cors-gnss-ipp', type: 'symbol', source: 'cors-gnss', filter: ['==', ['get', 'kind'], 'ipp'], layout: { 'icon-image': 'cors-ipp', 'icon-size': 0.6, 'icon-allow-overlap': true } });
            layer('cors-rover-clusters', 'circle', 'cors-rovers', { 'circle-radius': 17, 'circle-color': '#075985', 'circle-stroke-color': '#7dd3fc', 'circle-stroke-width': 1 }, ['has', 'point_count']);
            map.addLayer({ id: 'cors-rover-count', type: 'symbol', source: 'cors-rovers', filter: ['has', 'point_count'], layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 12 }, paint: { 'text-color': '#fff' } });
            map.addLayer({ id: 'cors-rover-pins', type: 'symbol', source: 'cors-rovers', filter: ['!', ['has', 'point_count']], layout: { 'icon-image': ['match', ['get', 'color'], '#22c55e', 'cors-rover-fixed', '#38bdf8', 'cors-rover-float', '#ef4444', 'cors-rover-3d', 'cors-rover-unknown'], 'icon-size': 1, 'icon-allow-overlap': true, 'icon-ignore-placement': true } });
            this.updateLayers(this.layerState);
            this.updateGnss(this.gnssFrame || empty);
            this.setProjection(this.projection);
        }
        _installMarkerImages() {
            for (const [name, color] of [['fixed', '#22c55e'], ['float', '#38bdf8'], ['3d', '#ef4444'], ['unknown', '#a3a3a3'], ['ipp', '#38bdf8']]) {
                const id = name === 'ipp' ? 'cors-ipp' : `cors-rover-${name}`;
                if (this.map.hasImage(id)) continue;
                const canvas = document.createElement('canvas'); canvas.width = 56; canvas.height = 56;
                const ctx = canvas.getContext('2d');
                ctx.scale(2, 2); ctx.translate(2, 2);
                ctx.beginPath();
                if (name === 'ipp') { ctx.moveTo(12, 3); ctx.lineTo(21, 12); ctx.lineTo(12, 21); ctx.lineTo(3, 12); }
                else { ctx.moveTo(12, 2); ctx.lineTo(22, 20); ctx.lineTo(2, 20); }
                ctx.closePath();
                ctx.fillStyle = color; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2; ctx.lineJoin = 'round';
                ctx.fill(); ctx.stroke();
                this.map.addImage(id, ctx.getImageData(0, 0, 56, 56), { pixelRatio: 2 });
            }
        }
        updateLayers(state) {
            this.layerState = { ...this.layerState, ...state };
            if (!this.active || !this.map?.getSource('cors-rovers')) return;
            const s = this.layerState;
            this._data('cors-rovers', (s.rovers || []).map(rover => {
                const meta = window.StationsMapUtils.getRoverStatusMeta(rover.status);
                return { type: 'Feature', geometry: { type: 'Point', coordinates: [Number(rover.longitude), Number(rover.latitude)] }, properties: { username: rover.username || '', mountpoint: rover.mountpoint || '', nsv: rover.nsv || 0, statusLabel: meta.label, color: meta.color } };
            }));
            this._data('cors-provinces', s.showProvinces ? (s.provinces?.features || []) : []);
            const holes = s.showVietnamMask ? (s.provinces?.features || []).flatMap(feature => feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates[0]] : feature.geometry.coordinates.map(polygon => polygon[0])) : [];
            this._data('cors-mask', s.showVietnamMask && holes.length ? [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]], ...holes] } }] : []);
            this._data('cors-islands', [[112.020, 16.530, 'Quần đảo Hoàng Sa\nVIỆT NAM', 'island'], [114.250, 9.880, 'Quần đảo Trường Sa\nVIỆT NAM', 'island'], [112, 13.2, 'Biển Đông', 'sea']].map(([lon, lat, name, kind]) => ({ type: 'Feature', properties: { name, kind }, geometry: { type: 'Point', coordinates: [lon, lat] } })));
            this.map.setMaxBounds(s.showVietnamMask ? [[100.5, 5], [120, 24.5]] : null);
            this._updateCoverage();
            this.updateIonosphere();
            const style = s.tileMode === 'streets' ? 'streets-v12' : 'satellite-streets-v12';
            if (this.styleName && this.styleName !== style) { this.styleName = style; this.map.setStyle(`mapbox://styles/mapbox/${style}`, { diff: false }); return; }
            this.styleName = style;
            for (const item of this.map.getStyle().layers || []) if (item.type === 'symbol' && !item.id.startsWith('cors-')) this._visibility(item.id, s.tileMode !== 'satellite');
        }
        _updateCoverage() {
            if (!this.map?.getSource('cors-coverage')) return;
            const features = this.layerState?.coverage ? this.stations.map(item => {
                const lat = item.coords.lat * Math.PI / 180, lon = item.coords.lon * Math.PI / 180, angle = 20 / 6371;
                const coordinates = [];
                for (let i = 0; i <= 48; i++) {
                    const bearing = i / 48 * 2 * Math.PI;
                    const phi = Math.asin(Math.sin(lat) * Math.cos(angle) + Math.cos(lat) * Math.sin(angle) * Math.cos(bearing));
                    const lambda = lon + Math.atan2(Math.sin(bearing) * Math.sin(angle) * Math.cos(lat), Math.cos(angle) - Math.sin(lat) * Math.sin(phi));
                    coordinates.push([lambda * 180 / Math.PI, phi * 180 / Math.PI]);
                }
                return { type: 'Feature', properties: { color: window.CorsUI.getStatusMeta(item.status).color }, geometry: { type: 'Polygon', coordinates: [coordinates] } };
            }) : [];
            this._data('cors-coverage', features);
        }
        updateIonosphere() {
            if (!this.map?.getLayer('cors-station-selected')) return;
            const manager = window.IonosphereOverlay, s = this.layerState || {};
            for (const [suffix, west, east] of [['west', -180, 0], ['east', 0, 180]]) {
                const id = `cors-tec-${suffix}`;
                if (!s.showIonosphere || !manager?.cachedData) { this._visibility(id, false); continue; }
                const key = `${manager.cachedData.time_tag}:${s.ionosphereMode}`;
                this.rasterKeys ||= {};
                if (!this.map.getSource(id) || this.rasterKeys[id] !== key) {
                    const url = manager.generateRasterDataUrl(s.ionosphereMode, { mercator: true, west, east });
                    if (!url) continue;
                    const coordinates = [[west + 1e-7, 85.05112878], [east - 1e-7, 85.05112878], [east - 1e-7, -85.05112878], [west + 1e-7, -85.05112878]];
                    if (this.map.getSource(id)) this.map.getSource(id).updateImage({ url, coordinates });
                    else {
                        this.map.addSource(id, { type: 'image', url, coordinates });
                        this.map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': s.ionosphereOpacity ?? 0.65, 'raster-fade-duration': 0 } }, 'cors-mask-fill');
                    }
                    this.rasterKeys[id] = key;
                }
                this._visibility(id, true);
                this.map.setPaintProperty(id, 'raster-opacity', s.ionosphereOpacity ?? 0.65);
            }
        }
        updateGnss(frame) { this.gnssFrame = frame; this.map?.getSource('cors-gnss')?.setData(frame); }
        fitProvince(code, options = {}) {
            const feature = this.layerState.provinces?.features?.find(item => String(item.properties.code) === String(code));
            if (!feature) return;
            const points = [];
            const collect = value => { if (typeof value[0] === 'number') points.push(value); else value.forEach(collect); };
            collect(feature.geometry.coordinates);
            this.selectedProvinceCode = String(code);
            for (const id of ['cors-province-selected-fill', 'cors-province-selected-line']) this.map.setFilter(id, ['==', ['to-string', ['get', 'code']], this.selectedProvinceCode]);
            let west = Infinity, east = -Infinity, south = Infinity, north = -Infinity;
            for (const [lon, lat] of points) { west = Math.min(west, lon); east = Math.max(east, lon); south = Math.min(south, lat); north = Math.max(north, lat); }
            if (options.fit !== false) this._fit(points, 11);
            const content = document.createElement('div');
            if (this.options.provincePopup) content.innerHTML = this.options.provincePopup(feature, this.stations);
            else content.textContent = feature.properties.fullName || feature.properties.name;
            const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-sm btn-outline-info w-100 mt-2'; button.textContent = 'Phóng tới tỉnh / thành';
            button.addEventListener('click', () => this.fitProvince(code)); content.append(button);
            this._popup(content, options.lngLat || [(west + east) / 2, (south + north) / 2]);
        }
        _fit(points, maxZoom = 13) {
            if (!points.length || !this.ready) return;
            const bounds = [[Infinity, Infinity], [-Infinity, -Infinity]];
            for (const [lon, lat] of points) {
                bounds[0][0] = Math.min(bounds[0][0], lon); bounds[0][1] = Math.min(bounds[0][1], lat);
                bounds[1][0] = Math.max(bounds[1][0], lon); bounds[1][1] = Math.max(bounds[1][1], lat);
            }
            this.interacting = true; this.map.stop(); this.interacting = false; this.flying = true;
            this.map.fitBounds(bounds, { padding: 60, maxZoom, duration: reducedMotion() ? 0 : 900 }, { corsFlight: true });
        }
        _textPopup(lngLat, title, description) {
            this.popup?.remove();
            const content = document.createElement('div'); content.className = 'mapbox-station-card';
            const heading = document.createElement('strong'); heading.textContent = title;
            const text = document.createElement('p'); text.textContent = description;
            content.append(heading, text);
            this._popup(content, lngLat);
        }
        _popup(content, lngLat) {
            this.popup?.remove();
            this.popup = new window.mapboxgl.Popup({ offset: 12, maxWidth: '340px', focusAfterOpen: false })
                .setLngLat(lngLat).setDOMContent(content).addTo(this.map);
            const close = this.popup.getElement().querySelector('.mapboxgl-popup-close-button');
            close?.removeAttribute('aria-hidden');
            close?.setAttribute('aria-label', 'Đóng thông tin');
        }
        setMeasureEnabled(enabled) {
            this.measureEnabled = enabled; this.measurePoints = []; this.measureFeatures = [];
            this._data('cors-measure', []);
            if (this.map) this.map.getCanvas().style.cursor = enabled ? 'crosshair' : '';
        }
        _hasPointFeature(event) {
            return this.map.queryRenderedFeatures(event.point, { layers: ['cors-station-pins', 'cors-rover-pins', 'cors-rover-clusters', 'cors-gnss-points', 'cors-gnss-ipp', 'cors-island-labels'].filter(id => this.map.getLayer(id)) }).length > 0;
        }
        _mapClick(event) {
            if (!this.measureEnabled && this.map.queryRenderedFeatures(event.point, { layers: ['cors-station-pins', 'cors-rover-pins', 'cors-rover-clusters', 'cors-gnss-points', 'cors-gnss-ipp', 'cors-provinces-fill', 'cors-island-labels'].filter(id => this.map.getLayer(id)) }).length) return;
            if (this.measureEnabled) {
                const point = [event.lngLat.lng, event.lngLat.lat];
                this.measurePoints.push(point);
                this.measureFeatures.push({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: point } });
                if (this.measurePoints.length === 2) {
                    const [a, b] = this.measurePoints;
                    this.measureFeatures.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [a, b] } });
                    const distance = window.StationsMapUtils.getDistanceMeters(a[1], a[0], b[1], b[0]);
                    this._textPopup(event.lngLat, 'Khoảng cách', window.CorsUI.formatDistance(distance));
                    this.measurePoints = [];
                }
                this._data('cors-measure', this.measureFeatures);
            } else if (this.layerState.showIonosphere) {
                const probe = window.IonosphereOverlay?.sample(event.lngLat.lat, event.lngLat.lng);
                if (probe) this._textPopup(event.lngLat, 'NOAA GloTEC TEC', `${probe.tec.toFixed(1)} TECU · ${probe.observation_time_vn || ''}`);
            }
        }
        locate() {
            if (!navigator.geolocation) { window.CorsUI?.showToast('warning', 'Trình duyệt không hỗ trợ định vị.'); return; }
            navigator.geolocation.getCurrentPosition(pos => {
                if (!this.ready) return;
                const { latitude: lat, longitude: lng } = pos.coords;
                this.flyTo(lat, lng, 13);
                this._data('cors-search', [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [lng, lat] } }]);
                this._textPopup([lng, lat], 'Vị trí của bạn', `${lat.toFixed(5)}°, ${lng.toFixed(5)}°`);
            }, () => window.CorsUI?.showToast('warning', 'Không xác định được vị trí.'), { timeout: 8000 });
        }
        findNearest(lat, lon, label) {
            const rows = this.stations.map(item => ({ ...item, distance: window.StationsMapUtils.getDistanceMeters(lat, lon, item.coords.lat, item.coords.lon) }));
            const available = row => ['online', 'streaming', 'sleep'].includes(row.status) || Number(row.traffic?.bps) > 0;
            const nearest = [rows.filter(available), rows.filter(row => !available(row))].map(items => items.sort((a, b) => a.distance - b.distance)[0]).filter(Boolean);
            const origin = [lon, lat];
            this._data('cors-search', [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: origin } }, ...nearest.map(row => ({ type: 'Feature', properties: { color: available(row) ? '#22c55e' : '#ef4444' }, geometry: { type: 'LineString', coordinates: [origin, [row.coords.lon, row.coords.lat]] } }))]);
            this._fit([origin, ...nearest.map(row => [row.coords.lon, row.coords.lat])]);
            this._textPopup(origin, label, nearest.map(row => `${row.device.name || row.device.serial}: ${(row.distance / 1000).toFixed(2)} km`).join(' · ') || 'Không có trạm phù hợp.');
        }

        getSurfaceView() {
            if (!this.map) return null;
            const center = this.map.getCenter();
            return { lat: center.lat, lng: ((center.lng + 180) % 360 + 360) % 360 - 180, zoom: this.map.getZoom() };
        }
        detach() {
            this.generation++;
            clearTimeout(this.loadTimer);
            this.failed = false; this.tileErrors = 0;
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
