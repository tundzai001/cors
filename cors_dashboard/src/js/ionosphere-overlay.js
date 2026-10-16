// NOAA GloTEC Ionosphere real-time TEC (Total Electron Content) GIS overlay for Leaflet.
(() => {
    // 256-color Lookup Table (LUT) generators for high-performance raster rendering
    const VTEC_STOPS = [
        { tec: 0, r: 20, g: 10, b: 65, a: 180 },
        { tec: 8, r: 25, g: 65, b: 180, a: 200 },
        { tec: 18, r: 0, g: 165, b: 215, a: 210 },
        { tec: 30, r: 35, g: 195, b: 85, a: 220 },
        { tec: 45, r: 245, g: 215, b: 25, a: 230 },
        { tec: 60, r: 245, g: 110, b: 15, a: 240 },
        { tec: 75, r: 225, g: 25, b: 25, a: 245 },
        { tec: 90, r: 245, g: 50, b: 210, a: 255 },
    ];

    const ANOMALY_STOPS = [
        { val: -20, r: 30, g: 60, b: 210, a: 230 },
        { val: -8, r: 56, g: 189, b: 248, a: 200 },
        { val: -2, r: 148, g: 163, b: 184, a: 110 },
        { val: 0, r: 100, g: 116, b: 139, a: 80 },
        { val: 2, r: 251, g: 191, b: 36, a: 140 },
        { val: 8, r: 249, g: 115, b: 22, a: 210 },
        { val: 20, r: 239, g: 68, b: 68, a: 240 },
    ];

    function createLut(stops, minVal, maxVal) {
        const lut = new Uint8ClampedArray(256 * 4);
        const span = maxVal - minVal || 1;

        for (let i = 0; i < 256; i++) {
            const val = minVal + (i / 255) * span;
            let c0 = stops[0];
            let c1 = stops[stops.length - 1];

            for (let j = 0; j < stops.length - 1; j++) {
                const sKey = stops[j].tec !== undefined ? 'tec' : 'val';
                if (val >= stops[j][sKey] && val <= stops[j + 1][sKey]) {
                    c0 = stops[j];
                    c1 = stops[j + 1];
                    break;
                }
            }

            const sKey = c0.tec !== undefined ? 'tec' : 'val';
            const range = (c1[sKey] - c0[sKey]) || 1;
            const factor = Math.max(0, Math.min(1, (val - c0[sKey]) / range));

            const idx = i * 4;
            lut[idx] = Math.round(c0.r + (c1.r - c0.r) * factor);
            lut[idx + 1] = Math.round(c0.g + (c1.g - c0.g) * factor);
            lut[idx + 2] = Math.round(c0.b + (c1.b - c0.b) * factor);
            lut[idx + 3] = Math.round(c0.a + (c1.a - c0.a) * factor);
        }
        return lut;
    }

    const VTEC_LUT = createLut(VTEC_STOPS, 0, 90);
    const ANOMALY_LUT = createLut(ANOMALY_STOPS, -20, 20);

    class IonosphereOverlayManager {
        constructor() {
            this.cachedData = null;
            this.imageOverlay = null;
            this.mapInstance = null;
            this.activeMode = 'tec'; // 'tec' or 'anomaly'
            this.opacity = 0.65;
            this.refreshTimer = null;
            this.isRefreshing = false;
            this.attachGeneration = 0;
            this.lastFetchMonotonic = 0;
            this.listeners = new Set();
        }

        subscribe(callback) {
            this.listeners.add(callback);
            return () => this.listeners.delete(callback);
        }

        notify() {
            this.listeners.forEach(fn => {
                try { fn(this.cachedData, this.activeMode, this.opacity); } catch (e) { console.error(e); }
            });
        }

        async fetchData(force = false) {
            if (this.isRefreshing) return this.cachedData;
            this.isRefreshing = true;
            try {
                const url = force ? '/api/ionosphere/latest?force=true' : '/api/ionosphere/latest';
                const resp = await (window.apiFetch || fetch)(url);
                if (!resp.ok) throw new Error(`HTTP error ${resp.status}`);
                const data = await resp.json();
                if (data && data.tec_grid && data.tec_grid.length) {
                    this.cachedData = data;
                    this.notify();
                }
                return this.cachedData;
            } catch (err) {
                console.warn('[IonosphereOverlay] Failed to load ionosphere data:', err);
                return this.cachedData;
            } finally {
                this.isRefreshing = false;
            }
        }

        // Render offscreen canvas raster representing the 72x72 grid
        generateRasterDataUrl(mode = 'tec', options = {}) {
            if (!this.cachedData || !this.cachedData.tec_grid || !this.cachedData.tec_grid.length) {
                return null;
            }

            const grid = mode === 'anomaly' ? this.cachedData.anomaly_grid : this.cachedData.tec_grid;
            const lut = mode === 'anomaly' ? ANOMALY_LUT : VTEC_LUT;
            const minVal = mode === 'anomaly' ? -20 : 0;
            const maxVal = mode === 'anomaly' ? 20 : 90;
            const span = maxVal - minVal;

            const gridRows = grid.length; // 72 rows (North to South)
            const gridCols = grid[0].length; // 72 cols (West to East)

            // Render on 288x144 canvas for smooth 4x bilinear interpolation
            const canvasWidth = 288;
            const canvasHeight = 144;
            const offscreenCanvas = document.createElement('canvas');
            offscreenCanvas.width = canvasWidth;
            offscreenCanvas.height = canvasHeight;
            const ctx = offscreenCanvas.getContext('2d');
            if (!ctx) return null;

            const imgData = ctx.createImageData(canvasWidth, canvasHeight);
            const data = imgData.data;

            for (let y = 0; y < canvasHeight; y++) {
                // Map pixel y to grid row [0, gridRows - 1]
                const fraction = y / (canvasHeight - 1);
                const latitude = options.mercator ? Math.atan(Math.sinh(Math.PI * (1 - 2 * fraction))) * 180 / Math.PI : 90 - 180 * fraction;
                const rowPos = (90 - latitude) / 180 * (gridRows - 1);
                const r0 = Math.floor(rowPos);
                const r1 = Math.min(gridRows - 1, r0 + 1);
                const dr = rowPos - r0;

                for (let x = 0; x < canvasWidth; x++) {
                    // Map pixel x to grid col [0, gridCols - 1]
                    const longitude = (options.west ?? -180) + x / (canvasWidth - 1) * ((options.east ?? 180) - (options.west ?? -180));
                    const colPos = (longitude + 180) / 360 * (gridCols - 1);
                    const c0 = Math.floor(colPos);
                    const c1 = (c0 + 1) % gridCols;
                    const dc = colPos - c0;

                    // Bilinear interpolation
                    const v00 = grid[r0][c0];
                    const v01 = grid[r0][c1];
                    const v10 = grid[r1][c0];
                    const v11 = grid[r1][c1];

                    const val = (1 - dr) * (1 - dc) * v00 +
                                (1 - dr) * dc * v01 +
                                dr * (1 - dc) * v10 +
                                dr * dc * v11;

                    // LUT index (0 to 255)
                    const norm = Math.max(0, Math.min(1, (val - minVal) / span));
                    const lutIdx = Math.floor(norm * 255) * 4;

                    const pIdx = (y * canvasWidth + x) * 4;
                    data[pIdx] = lut[lutIdx];
                    data[pIdx + 1] = lut[lutIdx + 1];
                    data[pIdx + 2] = lut[lutIdx + 2];
                    data[pIdx + 3] = lut[lutIdx + 3];
                }
            }

            ctx.putImageData(imgData, 0, 0);
            return offscreenCanvas.toDataURL('image/png');
        }

        // Bilinear probe for any lat, lon coordinate
        sample(lat, lon) {
            if (!this.cachedData || !this.cachedData.tec_grid || !this.cachedData.tec_grid.length) {
                return null;
            }

            const clampedLat = Math.max(-88.75, Math.min(88.75, lat));
            let clampedLon = lon;
            while (clampedLon < -180) clampedLon += 360;
            while (clampedLon > 180) clampedLon -= 360;
            clampedLon = Math.max(-177.5, Math.min(177.5, clampedLon));

            // GloTEC:
            // Rows are North to South: row 0 is +88.75, row 71 is -88.75
            // Cols are West to East: col 0 is -177.5, col 71 is +177.5
            const rowPos = Math.max(0, Math.min(71, (88.75 - clampedLat) / 2.5));
            const colPos = Math.max(0, Math.min(71, (clampedLon - (-177.5)) / 5.0));

            const r0 = Math.floor(rowPos);
            const r1 = Math.min(71, r0 + 1);
            const dr = rowPos - r0;

            const c0 = Math.floor(colPos);
            const c1 = (c0 + 1) % 72;
            const dc = colPos - c0;

            const interp = (grid) => {
                if (!grid || !grid.length) return 0.0;
                const v00 = grid[r0][c0];
                const v01 = grid[r0][c1];
                const v10 = grid[r1][c0];
                const v11 = grid[r1][c1];
                return (1 - dr) * (1 - dc) * v00 +
                       (1 - dr) * dc * v01 +
                       dr * (1 - dc) * v10 +
                       dr * dc * v11;
            };

            const tec = interp(this.cachedData.tec_grid);
            const anomaly = interp(this.cachedData.anomaly_grid);

            return {
                tec: Math.round(tec * 10) / 10,
                anomaly: Math.round(anomaly * 100) / 100,
                lat: clampedLat,
                lon: clampedLon,
                time_tag: this.cachedData.time_tag,
                observation_time_vn: this.cachedData.observation_time_vn,
                is_stale: this.cachedData.is_stale,
                status: this.cachedData.status,
                stats: this.cachedData.stats,
            };
        }

        async attachToMap(map, options = {}) {
            const generation = ++this.attachGeneration;
            this.mapInstance = map;
            if (options.opacity !== undefined) this.opacity = options.opacity;
            if (options.mode) this.activeMode = options.mode;

            if (!this.cachedData) {
                await this.fetchData();
            }
            if (generation !== this.attachGeneration || this.mapInstance !== map) return;

            const dataUrl = this.generateRasterDataUrl(this.activeMode);
            if (!dataUrl) return;

            const L = window.CorsDashboardShared?.getLeaflet?.() || window.L;
            if (!L) return;

            // GloTEC coverage world bounds
            const bounds = [[-90, -180], [90, 180]];

            if (this.imageOverlay && map.hasLayer(this.imageOverlay)) {
                this.imageOverlay.setUrl(dataUrl);
                this.imageOverlay.setOpacity(this.opacity);
            } else {
                this.imageOverlay = L.imageOverlay(dataUrl, bounds, {
                    opacity: this.opacity,
                    zIndex: 350,
                    interactive: false,
                    className: 'ionosphere-raster-layer',
                });
                this.imageOverlay.addTo(map);
            }

            this.startAutoRefresh();
            this.notify();
        }

        detachFromMap() {
            this.attachGeneration++;
            const map = this.mapInstance;
            if (map && this.imageOverlay && map.hasLayer(this.imageOverlay)) {
                map.removeLayer(this.imageOverlay);
            }
            this.imageOverlay = null;
            this.mapInstance = null;
            this.stopAutoRefresh();
            this.notify();
        }

        setOpacity(val) {
            this.opacity = Math.max(0.1, Math.min(1.0, val));
            if (this.imageOverlay) {
                this.imageOverlay.setOpacity(this.opacity);
            }
            this.notify();
        }

        setMode(mode) {
            if (mode !== 'tec' && mode !== 'anomaly') return;
            this.activeMode = mode;
            if (this.mapInstance && this.imageOverlay) {
                const dataUrl = this.generateRasterDataUrl(this.activeMode);
                if (dataUrl) this.imageOverlay.setUrl(dataUrl);
            }
            this.notify();
        }

        startAutoRefresh() {
            this.stopAutoRefresh();
            // Refresh every 10 minutes (matching NOAA SWPC cadence)
            this.refreshTimer = setInterval(async () => {
                if (document.visibilityState === 'hidden') return;
                const prevTag = this.cachedData?.time_tag;
                await this.fetchData();
                if (this.cachedData && this.cachedData.time_tag !== prevTag && this.mapInstance && this.imageOverlay) {
                    const dataUrl = this.generateRasterDataUrl(this.activeMode);
                    if (dataUrl) this.imageOverlay.setUrl(dataUrl);
                }
            }, 10 * 60 * 1000);
        }

        stopAutoRefresh() {
            if (this.refreshTimer) {
                clearInterval(this.refreshTimer);
                this.refreshTimer = null;
            }
        }
    }

    window.IonosphereOverlay = new IonosphereOverlayManager();
})();
