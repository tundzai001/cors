// ====================== Live GNSS dashboard panels ======================
(() => {
const { useState, useEffect, memo, useRef } = React;

function getLeaflet() {
    const leaflet = window.Leaflet || window.L;
    return leaflet && typeof leaflet.map === 'function' ? leaflet : null;
}
// === MAP COMPONENT ===
// ==================== MAP COMPONENT (FIXED HTTPS) ====================
const BaseLocationMap = memo(function BaseLocationMap({ coords }) {
    const mapContainerRef = useRef(null);
    const mapInstanceRef = useRef(null);
    const markerRef = useRef(null);
    const [mapType, setMapType] = useState('m'); // 'y' = Hybrid, 'm' = Roadmap
    const tileLayerRef = useRef(null);

    useEffect(() => {
        // Kiểm tra tọa độ đầu vào
        if (!coords || !coords.lat || !coords.lon) {
            console.warn('[Map] No coords provided');
            return;
        }
        const lat = parseFloat(coords.lat);
        const lon = parseFloat(coords.lon);

        if (isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) return;

        // Kiểm tra Leaflet đã load chưa
        const leaflet = getLeaflet();
        if (!leaflet) {
            console.error('Leaflet chưa được load!');
            return;
        }

        // Khởi tạo Map
        if (!mapInstanceRef.current) {
            mapInstanceRef.current = leaflet.map(mapContainerRef.current).setView([lat, lon], 16);

            tileLayerRef.current = leaflet.tileLayer(`https://mt1.google.com/vt/lyrs=${mapType}&x={x}&y={y}&z={z}`, {
                maxZoom: 20,
                attribution: 'Google Maps'
            }).addTo(mapInstanceRef.current);

            markerRef.current = leaflet.marker([lat, lon]).addTo(mapInstanceRef.current)
                .bindPopup(`<b>Vị trí Base</b><br>Lat: ${lat}<br>Lon: ${lon}`)
                .openPopup();
        } else {
            mapInstanceRef.current.setView([lat, lon], 16);
            if (markerRef.current) markerRef.current.setLatLng([lat, lon]);
        }

        // Cập nhật lớp bản đồ nếu thay đổi
        if (tileLayerRef.current) {
            tileLayerRef.current.setUrl(`https://mt1.google.com/vt/lyrs=${mapType}&x={x}&y={y}&z={z}`);
        }

        setTimeout(() => {
            if (mapInstanceRef.current) {
                mapInstanceRef.current.invalidateSize();
            }
        }, 300);

    }, [coords?.lat, coords?.lon, mapType]);

    // Handle Resize
    useEffect(() => {
        const handleResize = () => {
            if (mapInstanceRef.current) mapInstanceRef.current.invalidateSize();
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Hiển thị placeholder nếu chưa có tọa độ
    if (!coords || !coords.lat || (parseFloat(coords.lat) === 0 && parseFloat(coords.lon) === 0)) {
        console.warn('[Map] No valid coords - showing placeholder');
        return (
            <div className="d-flex align-items-center justify-content-center p-4 text-danger border rounded bg-dark"
                style={{ height: '250px', border: '2px solid red' }}>
                <div className="text-center">
                    <i className="bi bi-geo-alt-slash display-4 mb-2"></i>
                    <p className="mb-0">❌ Chưa có tọa độ Base</p>
                    <small>Debug: lat={coords?.lat || 'null'}, lon={coords?.lon || 'null'}</small>
                </div>
            </div>
        );
    }

    if (!getLeaflet()) {
        console.error('[Map] ❌ LEAFLET CHƯA ĐƯỢC LOAD!');
        return (
            <div className="alert alert-danger" style={{ height: '250px' }}>
                <h5>⚠️ Lỗi: Leaflet chưa được tải</h5>
                <p>Kiểm tra file HTML có thêm Leaflet CSS/JS chưa.</p>
            </div>
        );
    }
    return (
        <div>
            <div className="d-flex justify-content-between align-items-center mb-2">
                <div className="d-flex align-items-center">
                    <h6 className="mb-0 me-3">Vị trí trạm Base (Lưu cứng)</h6>
                    <div className="btn-group btn-group-sm" role="group">
                        <button
                            type="button"
                            className={`btn ${mapType === 'm' ? 'btn-primary' : 'btn-outline-primary'}`}
                            onClick={() => setMapType('m')}
                        >
                            Giao thông
                        </button>
                        <button
                            type="button"
                            className={`btn ${mapType === 'y' ? 'btn-primary' : 'btn-outline-primary'}`}
                            onClick={() => setMapType('y')}
                        >
                            Vệ tinh
                        </button>
                    </div>
                </div>
                <a href={`https://www.google.com/maps?q=${coords.lat},${coords.lon}`} target="_blank" className="btn btn-sm btn-outline-primary">
                    <i className="bi bi-box-arrow-up-right me-1"></i>Google Maps
                </a>
            </div>
            <div ref={mapContainerRef} style={{ width: '100%', height: '300px', borderRadius: '8px', border: '2px solid #444', zIndex: 0 }} />
            <div className="mt-2 small text-muted">Lat: {parseFloat(coords.lat).toFixed(8)} | Lon: {parseFloat(coords.lon).toFixed(8)}</div>
        </div>
    );
});

// ===============================================================================
// === DASHBOARD COMPONENT ===
// ===============================================================================

const flags = { us: new Image(), ru: new Image(), eu: new Image(), cn: new Image(), jp: new Image(), un: new Image() };
flags.us.src = '/img/flags/us.jpg';
flags.ru.src = '/img/flags/ru.jpg';
flags.eu.src = '/img/flags/eu.jpg';
flags.cn.src = '/img/flags/cn.jpg';
flags.jp.src = '/img/flags/jp.jpg';
flags.un.src = '/img/flags/un.jpg';

const colors = { us: '#00d47e', ru: '#ff5c5c', eu: '#5c9cff', cn: '#ffb95c', jp: '#ff77a9', un: '#aaaaaa' };
const constellationNames = { us: "GPS (USA)", ru: "GLONASS (Russia)", eu: "Galileo (EU)", cn: "BeiDou (China)", jp: "QZSS (Japan)", un: "Unknown" };

function getSatelliteSystem(sat) {
    if (!sat) return 'un';
    const sys = String(sat.system || '').toLowerCase();
    if (['us', 'ru', 'eu', 'cn', 'jp'].includes(sys)) return sys;
    
    const mapping = {
        g: 'us', gps: 'us',
        r: 'ru', glonass: 'ru',
        e: 'eu', galileo: 'eu',
        c: 'cn', beidou: 'cn', compass: 'cn',
        j: 'jp', qzss: 'jp'
    };
    if (mapping[sys]) return mapping[sys];

    // Fallback: try extracting system from string PRN (e.g. "G01", "R07")
    const prnStr = String(sat.prn || '');
    if (prnStr.length >= 2) {
        const firstChar = prnStr[0].toLowerCase();
        if (mapping[firstChar]) return mapping[firstChar];
    }

    const prn = Number(sat.prn);
    if (prn >= 1 && prn <= 32) return 'us';
    if (prn >= 65 && prn <= 96) return 'ru';
    if (prn >= 301 && prn <= 336) return 'eu';
    if (prn >= 193 && prn <= 200) return 'jp';
    if ((prn >= 33 && prn <= 64) || (prn >= 401 && prn <= 463)) return 'cn';
    return 'un';
}

// Chuẩn hóa PRN về dải RTCM (1-based) để NMEA và RTCM cùng vệ tinh dùng chung key.
// GLONASS: NMEA dùng 65-96 → chuẩn hóa về 1-32 (RTCM)
// Galileo: NMEA dùng 301-336 → chuẩn hóa về 1-36 (RTCM)
// BeiDou: NMEA dùng 401-463 → chuẩn hóa về 1-63 (RTCM)
// QZSS: NMEA dùng 193-200 → chuẩn hóa về 1-8 (RTCM)
function getNormalizedPrn(sat) {
    const prn = Number(sat.prn);
    const sys = getSatelliteSystem(sat);
    if (sys === 'ru' && prn >= 65 && prn <= 96) return prn - 64;  // GLONASS NMEA→RTCM
    if (sys === 'eu' && prn >= 301 && prn <= 336) return prn - 300; // Galileo NMEA→RTCM
    if (sys === 'cn' && prn >= 401 && prn <= 463) return prn - 400; // BeiDou NMEA→RTCM
    if (sys === 'jp' && prn >= 193 && prn <= 200) return prn - 192; // QZSS NMEA→RTCM
    return prn; // GPS or already RTCM-space
}

function getSatelliteKey(sat) {
    return `${getSatelliteSystem(sat)}:${getNormalizedPrn(sat)}`;
}

function getVisibleSatellites(satellites, maxAgeMs) {
    const now = Date.now();
    const visibleByKey = new Map();

    (satellites || []).forEach(sat => {
        const snr = Number(sat?.snr);
        if (!sat || !Number.isFinite(snr) || snr <= 0) return;

        let lastSeen = Number(sat.lastSeen || sat.last_seen || 0);
        if (lastSeen > 0 && lastSeen < 1e11) lastSeen *= 1000;
        if (lastSeen > 0 && now - lastSeen > maxAgeMs) return; // Uncommented to clear stale satellites

        const key = getSatelliteKey(sat);
        const existing = visibleByKey.get(key);
        // Only overwrite if this sat has better SNR
        if (!existing || snr > (Number(existing.snr) || 0)) {
            visibleByKey.set(key, sat);
        }
    });

    return Array.from(visibleByKey.values());
}

// ===============================================================================
// === ENHANCED DASHBOARD COMPONENT (REPLACED)
// ===============================================================================

const DashboardPanel = memo(function DashboardPanel({ device, nmeaData }) {
    const rtk_status = device.rtk_fix_status || 'NO_FIX';

    let fixInfo = { label: 'NO FIX', color: '#ff0000', icon: '❌' };

    const isAutoSetupStatus = (device.status || '').startsWith('auto_setup');

    if (isAutoSetupStatus) {
        const AUTO_SETUP_STEPS = {
            'auto_setup_rover': { label: 'Bước 1: Đặt chế độ ROVER', color: '#17a2b8', icon: '🔄', step: 1 },
            'auto_setup_wait_fix': { label: 'Bước 2: Chờ RTK Fix...', color: '#ffc107', icon: '📡', step: 2 },
            'auto_setup_averaging': { label: 'Bước 3: Lấy mẫu tọa độ...', color: '#17a2b8', icon: '📊', step: 3 },
            'auto_setup_base': { label: 'Bước 4: Cài đặt BASE', color: '#17a2b8', icon: '🏯', step: 4 }
        };
        const stepInfo = AUTO_SETUP_STEPS[device.status] || { label: 'Auto Setup...', color: '#17a2b8', icon: '⏳', step: 0 };
        fixInfo = { label: stepInfo.label, color: stepInfo.color, icon: stepInfo.icon };
    } else if (device.base_mode_active) {
        const baseConfig = device.base_config || {};
        if (baseConfig.auto_configured) {
            fixInfo = { label: 'BASE (Auto CORS)', color: '#00ff00', icon: '🏯' };
        } else if (baseConfig.base_setup_method === 'FIXED_LLA') {
            fixInfo = { label: 'BASE FIXED', color: '#00ff00', icon: '🏯' };
        } else {
            if (device.ntrip_connected) {
                fixInfo = { label: 'BASE READY', color: '#00ff00', icon: '📡' };
            } else {
                fixInfo = { label: 'SURVEYING...', color: '#ffaa00', icon: '⏳' };
            }
        }
    } else {
        const FIX_STATUS_MAP = {
            'RTK_FIXED': { label: 'RTK FIXED', color: '#00ff00', icon: '🎯' },
            'RTK_FLOAT': { label: 'RTK FLOAT', color: '#ffaa00', icon: '📍' },
            'DGPS': { label: 'DGPS', color: '#ff9900', icon: '⚠️' },
            'GPS_FIX': { label: 'GPS FIX', color: '#999999', icon: '📡' },
            'NO_FIX': { label: 'NO FIX', color: '#ff0000', icon: '❌' }
        };
        fixInfo = FIX_STATUS_MAP[rtk_status] || FIX_STATUS_MAP['NO_FIX'];
    }
    const bgCanvasRef = useRef(null);
    const satCanvasRef = useRef(null);
    const animationFrameRef = useRef(null);
    const lastSatelliteDataRef = useRef(null);
    const signalBarsContainerRef = useRef(null);

    const [satStats, setSatStats] = useState({ total: 0, tracking: 0, bySystem: {} });

    // Clean up satellite buffers when switching devices
    useEffect(() => {
        if (satelliteBufferRef.current) satelliteBufferRef.current.clear();
        lastSatelliteDataRef.current = null;

        // Clear canvases immediately
        if (satCanvasRef.current) {
            const ctx = satCanvasRef.current.getContext('2d');
            ctx.clearRect(0, 0, satCanvasRef.current.width, satCanvasRef.current.height);
        }
        if (signalBarsContainerRef.current) {
            signalBarsContainerRef.current.innerHTML = '';
        }
        setSatStats({ total: 0, tracking: 0, bySystem: {} });
    }, [device?.serial]);

    // 🆕 METRIC TRACKING STATE
    const [metrics, setMetrics] = useState({
        fixQuality: { value: 0, history: [] },
        satellites: { value: 0, history: [] },
        accuracy: { value: undefined, history: [] },
        hdop: { value: 0, history: [] },
        signalStrength: { value: 0, history: [] },
        throughput: { value: 0, history: [] }
    });

    const satelliteBufferRef = useRef(new Map());
    const SATELLITE_TIMEOUT = 5000;
    const FADE_DURATION = 2000;
    const safeNmeaData = nmeaData || { gga: null, gsa: null, satellites: {} };
    const safeNmeaDataRef = useRef(safeNmeaData);
    useEffect(() => {
        safeNmeaDataRef.current = safeNmeaData;
    }, [safeNmeaData]);
    const fallbackBaseCoords = device?.base_config?.coords || (
        device?.base_config?.lat !== undefined && device?.base_config?.lon !== undefined
            ? {
                lat: device.base_config.lat,
                lon: device.base_config.lon,
                alt: device.base_config.alt
            }
            : null
    );
    const baseCoords = (safeNmeaData.base_coords && safeNmeaData.base_coords.lat !== 0 && safeNmeaData.base_coords.lon !== 0) 
        ? safeNmeaData.base_coords 
        : fallbackBaseCoords;

    const lastStatsUpdate = useRef(0);
    const lastCanvasUpdate = useRef(0);
    const lastBarsUpdate = useRef(0);

    const STATS_INTERVAL = 1000;
    const CANVAS_INTERVAL = 200;
    const BARS_INTERVAL = 500;

    // 🆕 METRICS UPDATE LOGIC
    const metricsNmeaDataRef = useRef(nmeaData);
    useEffect(() => {
        metricsNmeaDataRef.current = nmeaData;
    }, [nmeaData]);

    useEffect(() => {
        if (!device) return;
        const interval = setInterval(() => {
            const currentNmeaData = metricsNmeaDataRef.current || {};
            const gga = currentNmeaData.gga || {};
            const gsa = currentNmeaData.gsa || {};
            const sats = Object.values(currentNmeaData.satellites || {}).filter(s => s.snr > 0);
            const satCount = sats.length || gga.satellites || 0;

            const avgSnr = sats.length > 0
                ? sats.reduce((sum, sat) => sum + (sat.snr || 0), 0) / sats.length
                : 0;

            let currentHacc = undefined;
            if (device.hacc !== undefined) {
                currentHacc = device.hacc / 1000;
            } else if (currentNmeaData.hacc !== undefined) {
                currentHacc = currentNmeaData.hacc / 1000;
            }

            setMetrics(prev => ({
                fixQuality: {
                    value: gga.fix_status === 'RTK_FIXED' ? 5 : gga.fix_status === 'DGPS' ? 3 : 1,
                    history: [...prev.fixQuality.history, gga.fix_status === 'RTK_FIXED' ? 5 : gga.fix_status === 'DGPS' ? 3 : 1].slice(-30)
                },
                satellites: {
                    value: satCount,
                    history: [...prev.satellites.history, satCount].slice(-30)
                },
                accuracy: {
                    value: currentHacc,
                    history: [...(prev.accuracy?.history || []), currentHacc !== undefined ? currentHacc : 0].slice(-30)
                },
                hdop: {
                    value: gsa.hdop || 0,
                    history: [...prev.hdop.history, gsa.hdop || 0].slice(-30)
                },
                signalStrength: {
                    value: avgSnr.toFixed(1),
                    history: [...prev.signalStrength.history, avgSnr].slice(-30)
                },
                throughput: {
                    value: device.bps || 0,
                    history: [...prev.throughput.history, device.bps || 0].slice(-30)
                }
            }));
        }, 2000);
        return () => clearInterval(interval);
    }, [device?.serial, device?.bps, device?.hacc]); // Update logic dependencies

    // SKYPLOT & CANVAS LOGIC
    useEffect(() => {
        if (!bgCanvasRef.current || !satCanvasRef.current) return;

        const bgCtx = bgCanvasRef.current.getContext('2d');
        const satCtx = satCanvasRef.current.getContext('2d');

        const drawSkyplotBase = () => {
            const w = bgCtx.canvas.width, h = bgCtx.canvas.height;
            if (w === 0 || h === 0) return;

            const centerX = w / 2, centerY = h / 2;
            const radius = Math.min(centerX, centerY) * 0.85;

            bgCtx.clearRect(0, 0, w, h);

            // Background Gradient
            const grad = bgCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
            grad.addColorStop(0, '#1a2332');
            grad.addColorStop(0.5, '#0d1520');
            grad.addColorStop(1, '#000');
            bgCtx.fillStyle = grad;
            bgCtx.fillRect(0, 0, w, h);

            // Circles
            bgCtx.strokeStyle = '#3a4a5a';
            bgCtx.lineWidth = 1.5;
            bgCtx.font = '11px Arial';
            bgCtx.fillStyle = '#6a7a8a';
            bgCtx.textAlign = 'left';

            [{ frac: 0.33, label: '60°' }, { frac: 0.66, label: '30°' }, { frac: 1.0, label: '0°' }]
                .forEach(({ frac, label }) => {
                    const r = radius * frac;
                    bgCtx.beginPath();
                    bgCtx.arc(centerX, centerY, r, 0, 2 * Math.PI);
                    bgCtx.stroke();
                    bgCtx.fillText(label, centerX + r + 5, centerY);
                });

            // Cross Lines
            bgCtx.strokeStyle = '#2a3a4a';
            bgCtx.lineWidth = 1;
            bgCtx.beginPath();
            for (let i = 0; i < 12; i++) {
                const angle = i * 30 * Math.PI / 180;
                bgCtx.moveTo(centerX, centerY);
                bgCtx.lineTo(centerX + radius * Math.sin(angle), centerY - radius * Math.cos(angle));
            }
            bgCtx.stroke();

            // Labels
            bgCtx.fillStyle = '#fff';
            bgCtx.font = 'bold 18px Arial';
            bgCtx.textAlign = 'center';
            bgCtx.textBaseline = 'middle';
            const labelDistance = radius + 25;
            [{ t: 'N', a: 0 }, { t: 'E', a: 90 }, { t: 'S', a: 180 }, { t: 'W', a: 270 }].forEach(({ t, a }) => {
                const rad = a * Math.PI / 180;
                bgCtx.fillText(t, centerX + labelDistance * Math.sin(rad), centerY - labelDistance * Math.cos(rad));
            });
        };

        drawSkyplotBase();

        const drawSkyplotSatellites = (satellites) => {
            const now = Date.now();
            const buffer = satelliteBufferRef.current;

            satellites.forEach(sat => {
                const key = getSatelliteKey(sat);
                const existing = buffer.get(key);
                const satSnr = Number(sat.snr) || 0;
                const existingSnr = existing ? (Number(existing.snr) || 0) : 0;
                const newAz = Number(sat.azimuth) || 0;
                const newEl = Number(sat.elevation) || 0;
                const hasNewPos = (newAz !== 0 || newEl !== 0);
                const hasOldPos = existing && (Number(existing.azimuth) !== 0 || Number(existing.elevation) !== 0);

                // Only update buffer if the new satellite has SNR, or if the existing one also has no SNR
                if (!existing || satSnr > 0 || existingSnr === 0) {
                    if (!hasNewPos && hasOldPos) {
                        // Giữ lại az/el cũ khi ephemeris chưa được tính lại
                        buffer.set(key, { ...sat, azimuth: existing.azimuth, elevation: existing.elevation, lastSeen: now, opacity: 1.0 });
                    } else {
                        buffer.set(key, { ...sat, lastSeen: now, opacity: 1.0 });
                    }
                } else {
                    existing.lastSeen = now; // keep it alive
                    // Cập nhật SNR nhưng giữ az/el cũ nếu mới = 0
                    if (satSnr > 0) existing.snr = satSnr;
                    if (hasNewPos) {
                        existing.azimuth = newAz;
                        existing.elevation = newEl;
                    }
                }
            });


            for (const [satKey, satData] of buffer.entries()) {
                const timeSinceLastSeen = now - satData.lastSeen;
                if (timeSinceLastSeen > SATELLITE_TIMEOUT + FADE_DURATION) {
                    buffer.delete(satKey);
                } else if (timeSinceLastSeen > SATELLITE_TIMEOUT) {
                    const fadeProgress = (timeSinceLastSeen - SATELLITE_TIMEOUT) / FADE_DURATION;
                    satData.opacity = Math.max(0, 1 - fadeProgress);
                }
            }

            satCtx.clearRect(0, 0, satCtx.canvas.width, satCtx.canvas.height);
            const centerX = satCtx.canvas.width / 2;
            const centerY = satCtx.canvas.height / 2;
            const radius = Math.min(centerX, centerY) * 0.82;

            if (satCtx.canvas.width === 0 || satCtx.canvas.height === 0) return;

            for (const satData of buffer.values()) {
                let az = Number(satData.azimuth);
                let el = Number(satData.elevation);
                let snr = Number(satData.snr);

                if (isNaN(snr) || snr === 0) continue;
                if (isNaN(az)) az = 0;
                if (isNaN(el)) el = 0;

                const az_rad = (az * Math.PI) / 180;
                const dist = radius * (90 - el) / 90;
                const x = centerX + dist * Math.sin(az_rad);
                const y = centerY - dist * Math.cos(az_rad);

                const system = getSatelliteSystem(satData);
                const flag = flags[system];
                const isTracking = satData.isTracking;

                satCtx.globalAlpha = satData.opacity || 1.0;
                const flagSize = isTracking ? 18 : 14;

                if (isTracking) {
                    satCtx.beginPath();
                    satCtx.arc(x, y, flagSize / 2 + 3, 0, 2 * Math.PI);
                    satCtx.fillStyle = (colors[system] || '#fff') + '40';
                    satCtx.fill();
                }

                if (flag && flag.complete) {
                    satCtx.drawImage(flag, x - flagSize / 2, y - flagSize / 2, flagSize, flagSize);
                } else {
                    satCtx.beginPath();
                    satCtx.arc(x, y, 9, 0, 2 * Math.PI);
                    satCtx.fillStyle = colors[system] || '#ccc';
                    satCtx.fill();
                }

                satCtx.fillStyle = 'rgba(0, 0, 0, 0.8)';
                satCtx.fillRect(x - 8, y + flagSize / 2 + 2, 16, 10);
                satCtx.fillStyle = '#fff';
                satCtx.font = 'bold 7px Arial';
                satCtx.textAlign = 'center';
                satCtx.textBaseline = 'middle';
                satCtx.fillText(getNormalizedPrn(satData), x, y + flagSize / 2 + 7);
            }
            satCtx.globalAlpha = 1.0;
        };

        const drawSignalBars = (satellites) => {
            const container = signalBarsContainerRef.current;
            if (!container) return;

            // Count only fresh satellites with a positive SNR; canvas fade remains visual-only.
            const activeSatellites = getVisibleSatellites(satellites, SATELLITE_TIMEOUT + FADE_DURATION);

            // Tạo hash cấu trúc (Chỉ phụ thuộc số lượng và loại PRN - Không chứa giá trị SNR để tránh giật hình)
            const structureHash = activeSatellites.map(s => getSatelliteKey(s)).sort().join('|');

            // Cập nhật text Stats
            let totalSnr = 0;
            let lowSnrCount = 0;
            const groupedSats = activeSatellites.reduce((acc, sat) => {
                const system = getSatelliteSystem(sat);
                if (!acc[system]) acc[system] = [];
                acc[system].push(sat);
                if (sat.snr > 0) totalSnr += sat.snr;
                if (sat.snr < 25 && sat.snr > 0) lowSnrCount++;
                return acc;
            }, {});
            const avgSnr = activeSatellites.length > 0 ? totalSnr / activeSatellites.length : 0;
            setSatStats({ 
                total: activeSatellites.length, 
                tracking: activeSatellites.filter(s => s.isTracking).length, 
                bySystem: groupedSats,
                avgSnr: avgSnr,
                lowSnrCount: lowSnrCount
            });

            // 1. Nếu CẤU TRÚC bị đổi (thêm/bớt vệ tinh) -> Xóa toàn bộ và vẽ lại DOM
            if (lastSatelliteDataRef.current !== structureHash) {
                lastSatelliteDataRef.current = structureHash;

                const fragment = document.createDocumentFragment();

                ['us', 'ru', 'eu', 'cn', 'jp', 'un'].forEach(system => {
                    if (groupedSats[system]) {
                        // Bắt chước phần mềm u-center: Phải sắp xếp cố định theo số PRN thay vì SNR để cột không bị đảo lộn vị trí!
                        const sats = groupedSats[system].sort((a, b) => parseFloat(a.prn) - parseFloat(b.prn));
                        const groupDiv = document.createElement('div');
                        groupDiv.className = 'constellation-group';

                        const headerDiv = document.createElement('div');
                        headerDiv.className = 'constellation-header';
                        headerDiv.innerHTML = `<img src="${flags[system].src}" width="24" height="24" /> ${constellationNames[system]} (<span class="group-count">${sats.length}</span>)`;

                        const barsWrapper = document.createElement('div');
                        barsWrapper.className = 'bars-wrapper';

                        sats.forEach(sat => {
                            const barFillHeight = Math.min(100, sat.snr / 55 * 100);
                            let barColor = colors[system];
                            if (sat.snr >= 40) barColor = '#00ff88';
                            else if (sat.snr >= 20) barColor = '#ffc107';
                            else barColor = '#ff5252';

                            const itemDiv = document.createElement('div');
                            itemDiv.className = 'sat-bar-item';
                            // Gắn id bằng PRN để sau này chọc thẳng vào DOM update
                            itemDiv.id = `sat-bar-${getSatelliteKey(sat).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                            itemDiv.innerHTML = `
                                <div class="sat-bar-snr" style="color: ${barColor}; font-weight: bold;">${sat.snr}</div>
                                <div class="sat-bar"><div class="sat-bar-fill" style="height: ${barFillHeight}%; background: ${barColor};"></div></div>
                                <div class="sat-bar-prn">${getNormalizedPrn(sat)}</div>
                            `;
                            barsWrapper.appendChild(itemDiv);
                        });

                        groupDiv.appendChild(headerDiv);
                        groupDiv.appendChild(barsWrapper);
                        fragment.appendChild(groupDiv);
                    }
                });

                container.innerHTML = '';
                container.appendChild(fragment);
            }
            // 2. Chế độ NGẦM: Chỉ cập nhật trị SNR + Color (Không tái tạo DOM, giúp thanh Transition mượt mà và hết giật nền)
            else {
                activeSatellites.forEach(sat => {
                    const domId = `sat-bar-${getSatelliteKey(sat).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                    const itemDiv = document.getElementById(domId);
                    if (itemDiv) {
                        const barFillHeight = Math.min(100, sat.snr / 55 * 100);
                        const system = getSatelliteSystem(sat);
                        let barColor = colors[system];
                        if (sat.snr >= 40) barColor = '#00ff88';
                        else if (sat.snr >= 20) barColor = '#ffc107';
                        else barColor = '#ff5252';

                        const snrText = itemDiv.querySelector('.sat-bar-snr');
                        const fillDiv = itemDiv.querySelector('.sat-bar-fill');

                        if (snrText) {
                            snrText.textContent = sat.snr;
                            snrText.style.color = barColor;
                        }
                        if (fillDiv) {
                            fillDiv.style.height = `${barFillHeight}%`;
                            fillDiv.style.background = barColor;
                        }
                    }
                });
            }
        };

        const animate = () => {
            const now = Date.now();
            const { gga, gsa, satellites } = safeNmeaDataRef.current;

            // Text Stats Updates
            if (now - lastStatsUpdate.current >= STATS_INTERVAL) {

                const satCountEl = document.getElementById('db-sats');
                if (satCountEl) {
                    const activeCount = getVisibleSatellites(Object.values(satellites || {}), SATELLITE_TIMEOUT + FADE_DURATION).length;
                    satCountEl.textContent = activeCount || gga?.satellites || 0;
                }

                const dopEl = document.getElementById('db-dop');
                if (dopEl) {
                    const hText = gsa?.hdop >= 99 ? 'N/A' : (gsa?.hdop?.toFixed(2) || '0.0');
                    const pText = gsa?.pdop >= 99 ? 'N/A' : (gsa?.pdop?.toFixed(2) || '0.0');
                    dopEl.textContent = `${hText} / ${pText}`;
                }

                const latEl = document.getElementById('db-lat');
                if (latEl) latEl.textContent = gga?.latitude?.toFixed(8) || '0.0';

                const lonEl = document.getElementById('db-lon');
                if (lonEl) lonEl.textContent = gga?.longitude?.toFixed(8) || '0.0';

                lastStatsUpdate.current = now;
            }

            // Canvas & Bars Updates
            if (now - lastCanvasUpdate.current >= CANVAS_INTERVAL) {
                drawSkyplotSatellites(Object.values(satellites || {}));
                lastCanvasUpdate.current = now;
            }

            if (now - lastBarsUpdate.current >= BARS_INTERVAL) {
                drawSignalBars(Object.values(satellites || {}));
                lastBarsUpdate.current = now;
            }

            animationFrameRef.current = requestAnimationFrame(animate);
        };

        const handleResize = () => {
            if (bgCanvasRef.current && satCanvasRef.current) {
                const container = bgCanvasRef.current.parentElement;
                if (!container) return;
                const w = container.clientWidth;
                const h = container.clientHeight;

                if (w > 0 && h > 0) {
                    bgCanvasRef.current.width = w;
                    bgCanvasRef.current.height = h;
                    satCanvasRef.current.width = w;
                    satCanvasRef.current.height = h;
                    drawSkyplotBase();
                }
            }
        };

        handleResize();
        setTimeout(handleResize, 100);
        setTimeout(handleResize, 500);
        window.addEventListener('resize', handleResize);

        animate();

        return () => {
            window.removeEventListener('resize', handleResize);
            if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
        };
    }, []);
    /*
    const FIX_STATUS_MAP = {
        'RTK_FIXED': { label: 'RTK FIXED', color: '#00ff00', icon: '🎯', priority: 5 },
        'RTK_FLOAT': { label: 'RTK FLOAT', color: '#ffaa00', icon: '📍', priority: 4 },
        'DGPS': { label: 'DGPS', color: '#ff9900', icon: '⚠️', priority: 3 },
        'GPS_FIX': { label: 'GPS FIX', color: '#999999', icon: '📡', priority: 2 },
        'NO_FIX': { label: 'NO FIX', color: '#ff0000', icon: '❌', priority: 1 }
    };
    
    const rtk_status = device.rtk_fix_status || 'NO_FIX';
    const fixInfo = FIX_STATUS_MAP[rtk_status] || FIX_STATUS_MAP['NO_FIX'];
    */

    const acc = metrics.accuracy?.value;
    let accDisplayValue = "--";
    let accUnit = "m";
    let accColor = "#ff6b6b";

    if (typeof acc === "number" && !isNaN(acc)) {
        if (acc < 1) {
            accDisplayValue = (acc * 100).toFixed(1);
            accUnit = "cm";
        } else {
            accDisplayValue = acc.toFixed(2);
            accUnit = "m";
        }

        if (acc < 0.02) accColor = "#51cf66";
        else if (acc < 0.05) accColor = "#94d82d";
        else if (acc < 0.1) accColor = "#ffd43b";
    }

    return (
        <div className="dashboard-master">
            {/* 🆕 METRIC CARDS WITH SPARKLINES (Nếu có component MetricCard) */}
            {window.MetricCard && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px', marginBottom: '16px' }}>
                    <window.MetricCard title="Throughput" value={(metrics.throughput.value / 1024).toFixed(1)} unit="KB/s" trend={0} sparklineData={metrics.throughput.history} color="#51cf66" icon="⚡" />
                    <window.MetricCard title="Active Sats" value={metrics.satellites.value} unit="" trend={0} sparklineData={metrics.satellites.history} color="#0d6efd" icon="🛰️" />
                    <window.MetricCard title="hAcc" value={accDisplayValue} unit={accUnit} trend={0} sparklineData={metrics.accuracy?.history || []} color={accColor} icon="🎯" />
                    <window.MetricCard title="Avg SNR" value={metrics.signalStrength.value} unit="dB" trend={0} sparklineData={metrics.signalStrength.history} color="#fa5252" icon="📶" />
                </div>
            )}

            {/* STATS STRIP */}
            <div className="stats-strip">
                {/* ✅ CẬP NHẬT FIX STATUS CARD */}
                <div className="stat-item">
                    <div className="stat-icon" style={{ fontSize: '2rem' }}>{fixInfo.icon}</div>
                    <div className="stat-content">
                        <div className="stat-label">Fix Status</div>
                        <div
                            className="stat-value"
                            style={{
                                color: fixInfo.color,
                                textShadow: `0 0 10px ${fixInfo.color}`,
                                fontWeight: 'bold'
                            }}
                        >
                            {fixInfo.label}
                        </div>
                    </div>
                </div>
                <div className="stat-item"><div className="stat-icon">🛰️</div><div className="stat-content"><div className="stat-label">Satellites</div><div id="db-sats" className="stat-value">0</div></div></div>
                <div className="stat-item"><div className="stat-icon">📡</div><div className="stat-content"><div className="stat-label">HDOP / PDOP</div><div id="db-dop" className="stat-value">0.0 / 0.0</div></div></div>
                <div className="stat-item"><div className="stat-icon">📍</div><div className="stat-content"><div className="stat-label">Position</div><div className="stat-value stat-small"><span id="db-lat">0.0</span> / <span id="db-lon">0.0</span></div></div></div>
            </div>
            {/* BASE MODE WARNING */}
            {device.base_mode_active && rtk_status !== 'RTK_FIXED' && (
                <div className="alert alert-warning mt-3" role="alert">
                    <i className="bi bi-exclamation-triangle-fill me-2"></i>
                    <strong>Base Station Mode Active</strong> - Waiting for RTK convergence...
                    {rtk_status === 'DGPS' && (
                        <div className="small mt-1">
                            Currently in DGPS mode. RTK Fixed typically requires 5-10 minutes after power-on.
                        </div>
                    )}
                </div>
            )}

            {/* MAP */}
            <div className="map-section">
                <BaseLocationMap coords={baseCoords} />
            </div>

            {/* RTCM & ANTENNA ASSESSMENT - REDESIGNED */}
            <div className="card mb-3 bg-dark border-secondary">
                <div className="card-header d-flex align-items-center bg-dark">
                    <i className="bi bi-broadcast me-2 text-primary"></i>
                    <h6 className="mb-0">RTCM & Antenna Assessment</h6>
                </div>
                <div className="card-body p-3">
                    {(!device.rtcm_stats || Object.keys(device.rtcm_stats).length === 0) ? (
                        <div className="text-muted small text-center my-3">
                            <i className="bi bi-hourglass-split me-2"></i>
                            Đang chờ dữ liệu giải mã RTCM từ trạm...
                        </div>
                    ) : (() => {
                        const rtcmDescriptions = {
                            "1001": { name: "GPS L1 Obs (Basic)", desc: "Quan trắc GPS L1 cơ bản", category: "obs" },
                            "1002": { name: "GPS L1 Obs (Extended)", desc: "Quan trắc GPS L1 mở rộng", category: "obs" },
                            "1003": { name: "GPS L1/L2 Obs (Basic)", desc: "Quan trắc GPS L1/L2 cơ bản", category: "obs" },
                            "1004": { name: "GPS L1/L2 Obs (Extended)", desc: "Quan trắc GPS L1/L2 mở rộng", category: "obs" },
                            "1005": { name: "Base Position (ARP)", desc: "Tọa độ trạm Base (không có chiều cao ăng-ten)", category: "base" },
                            "1006": { name: "Base Position + Height", desc: "Tọa độ trạm Base kèm chiều cao ăng-ten", category: "base" },
                            "1007": { name: "Antenna Descriptor", desc: "Mô tả loại ăng-ten đang sử dụng", category: "base" },
                            "1008": { name: "Antenna Serial Number", desc: "Số serial ăng-ten", category: "base" },
                            "1019": { name: "GPS Ephemeris", desc: "Lịch thiên văn GPS – Quỹ đạo chính xác vệ tinh GPS", category: "eph" },
                            "1020": { name: "GLONASS Ephemeris", desc: "Lịch thiên văn GLONASS – Quỹ đạo vệ tinh GLONASS", category: "eph" },
                            "1033": { name: "Receiver & Antenna", desc: "Thông tin máy thu và ăng-ten (model, firmware)", category: "base" },
                            "1042": { name: "BeiDou Ephemeris", desc: "Lịch thiên văn BeiDou – Quỹ đạo vệ tinh BDS", category: "eph" },
                            "1044": { name: "QZSS Ephemeris", desc: "Lịch thiên văn QZSS – Quỹ đạo vệ tinh Nhật Bản", category: "eph" },
                            "1045": { name: "Galileo F/NAV Eph", desc: "Lịch thiên văn Galileo F/NAV – Quỹ đạo vệ tinh EU", category: "eph" },
                            "1046": { name: "Galileo I/NAV Eph", desc: "Lịch thiên văn Galileo I/NAV", category: "eph" },
                            "1074": { name: "GPS MSM4", desc: "Tín hiệu đo GPS đa tần – Pseudorange + Carrier Phase", category: "msm" },
                            "1077": { name: "GPS MSM7", desc: "Tín hiệu GPS đầy đủ (Full) – Độ chính xác cao nhất", category: "msm" },
                            "1084": { name: "GLONASS MSM4", desc: "Tín hiệu đo GLONASS đa tần", category: "msm" },
                            "1087": { name: "GLONASS MSM7", desc: "Tín hiệu GLONASS đầy đủ (Full)", category: "msm" },
                            "1094": { name: "Galileo MSM4", desc: "Tín hiệu đo Galileo đa tần", category: "msm" },
                            "1097": { name: "Galileo MSM7", desc: "Tín hiệu Galileo đầy đủ (Full)", category: "msm" },
                            "1114": { name: "QZSS MSM4", desc: "Tín hiệu đo QZSS đa tần (Nhật Bản)", category: "msm" },
                            "1117": { name: "QZSS MSM7", desc: "Tín hiệu QZSS đầy đủ (Full)", category: "msm" },
                            "1124": { name: "BeiDou MSM4", desc: "Tín hiệu đo BeiDou đa tần (Trung Quốc)", category: "msm" },
                            "1127": { name: "BeiDou MSM7", desc: "Tín hiệu BeiDou đầy đủ (Full)", category: "msm" },
                            "1230": { name: "GLONASS Code-Phase Bias", desc: "Hiệu chỉnh sai lệch mã-pha sóng GLONASS", category: "aux" },
                        };
                        const categoryLabels = {
                            "msm": { label: "📡 Tín hiệu vệ tinh (MSM)", color: "#0d6efd" },
                            "eph": { label: "🌍 Lịch thiên văn (Ephemeris)", color: "#6f42c1" },
                            "base": { label: "📍 Thông tin trạm Base", color: "#20c997" },
                            "obs": { label: "📊 Quan trắc cơ bản", color: "#fd7e14" },
                            "aux": { label: "🔧 Hiệu chỉnh & Phụ trợ", color: "#adb5bd" },
                        };
                        const grouped = {};
                        Object.keys(device.rtcm_stats).sort().forEach(type => {
                            const info = rtcmDescriptions[type] || { name: `RTCM ${type}`, desc: "Bản tin RTCM khác", category: "aux" };
                            const cat = info.category;
                            if (!grouped[cat]) grouped[cat] = [];
                            grouped[cat].push({ type, stat: device.rtcm_stats[type], ...info });
                        });
                        const catOrder = ["msm", "base", "eph", "obs", "aux"];
                        return (
                            <div>
                                {catOrder.filter(c => grouped[c]).map(cat => {
                                    const catInfo = categoryLabels[cat] || { label: cat, color: "#6c757d" };
                                    return (
                                        <div key={cat} className="mb-3">
                                            <div className="d-flex align-items-center mb-2" style={{ borderLeft: `3px solid ${catInfo.color}`, paddingLeft: '8px' }}>
                                                <span className="small fw-bold" style={{ color: catInfo.color }}>{catInfo.label}</span>
                                            </div>
                                            {grouped[cat].map(item => {
                                                const isMsm = item.category === "msm";
                                                let statusColor = "#6c757d";
                                                let statusBg = "rgba(108,117,125,0.15)";
                                                if (isMsm) {
                                                    if (item.stat.interval <= 1.5) { statusColor = "#198754"; statusBg = "rgba(25,135,84,0.12)"; }
                                                    else if (item.stat.interval <= 3.0) { statusColor = "#ffc107"; statusBg = "rgba(255,193,7,0.12)"; }
                                                    else { statusColor = "#dc3545"; statusBg = "rgba(220,53,69,0.12)"; }
                                                } else {
                                                    if (item.stat.interval <= 15) { statusColor = "#0dcaf0"; statusBg = "rgba(13,202,240,0.10)"; }
                                                    else { statusColor = "#6c757d"; statusBg = "rgba(108,117,125,0.10)"; }
                                                }
                                                return (
                                                    <div key={item.type} className="d-flex align-items-center py-1 px-2 mb-1 rounded" style={{ backgroundColor: statusBg }}>
                                                        <div style={{ minWidth: '52px' }}>
                                                            <span className="badge fw-bold" style={{ backgroundColor: statusColor, fontSize: '0.8rem', minWidth: '44px', display: 'inline-block', textAlign: 'center' }}>{item.type}</span>
                                                        </div>
                                                        <div className="flex-grow-1 ms-2" style={{ lineHeight: 1.3 }}>
                                                            <div className="small fw-semibold text-light">{item.name}</div>
                                                            <div style={{ fontSize: '0.72rem', color: '#8b949e' }}>{item.desc}</div>
                                                        </div>
                                                        <div className="text-end ms-2" style={{ minWidth: '58px' }}>
                                                            <span className="badge bg-dark bg-opacity-75 border" style={{ borderColor: `${statusColor} !important`, color: statusColor, fontSize: '0.78rem' }}>{item.stat.interval}s</span>
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    );
                                })}
                            </div>
                        );
                    })()}
                    <div className="mt-3 pt-3" style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                        <div className="text-muted small mb-2 text-uppercase">Antenna Quality Diagnostics</div>
                        {(() => {
                            const avgSnr = Number(metrics.signalStrength.value) || 0;
                            const sats = metrics.satellites.value;
                            const hasCoords = device.rtcm_stats && Object.keys(device.rtcm_stats).some(k => k === "1005" || k === "1006");
                            
                            return (
                                <ul className="mb-0 text-light small ps-3" style={{ listStyleType: "circle" }}>
                                    {hasCoords ? (
                                        <li className="text-success mb-1">Tọa độ Base (1005/1006) đã sẵn sàng.</li>
                                    ) : (
                                        <li className="text-danger mb-1">Chưa có gói tọa độ 1005/1006. Rover không thể FIX RTK!</li>
                                    )}
                                    
                                    {sats < 12 && sats > 0 && (
                                        <li className="text-warning mb-1">Số lượng vệ tinh quan sát được khá thấp ({sats}). Góc nhìn bầu trời hẹp.</li>
                                    )}
                                    
                                    {avgSnr > 0 && avgSnr < 35 && (
                                        <li className="text-warning mb-1">Cường độ tín hiệu thấp (SNR trung bình {avgSnr.toFixed(1)} dB-Hz). <strong>Khuyến nghị:</strong> Nâng cao ăng-ten hoặc đưa ra nơi trống trải.</li>
                                    )}
                                    {avgSnr >= 38 && (
                                        <li className="text-success mb-1">Tín hiệu thu được từ ăng-ten rất tốt và ổn định (SNR trung bình {avgSnr.toFixed(1)} dB-Hz).</li>
                                    )}
                                    {avgSnr >= 35 && avgSnr < 38 && (
                                        <li className="text-info mb-1">Tín hiệu ăng-ten đạt mức khá (SNR trung bình {avgSnr.toFixed(1)} dB-Hz).</li>
                                    )}
                                </ul>
                            );
                        })()}
                    </div>
                </div>
            </div>

            {/* HARDWARE & ECO SAVINGS PANEL */}
            {device.system_info && (
                <div className="card mb-3 border-0" style={{ background: 'linear-gradient(135deg, rgba(13,110,253,0.08), rgba(32,201,151,0.08))', border: '1px solid rgba(13,110,253,0.25)' }}>
                    <div className="card-header d-flex align-items-center border-0" style={{ background: 'transparent' }}>
                        <i className="bi bi-cpu-fill me-2" style={{ color: '#0dcaf0' }}></i>
                        <h6 className="mb-0 text-light">Hardware Telemetry & Eco Savings</h6>
                        <span className="badge ms-auto" style={{ backgroundColor: 'rgba(13,202,240,0.15)', color: '#0dcaf0', fontSize: '0.7rem' }}>Aitogy.com.vn</span>
                    </div>
                    <div className="card-body pt-0 pb-3 px-3">
                        {(() => {
                            const savings = device.savings_stats || {};
                            const isAwake = (savings.is_sleeping !== undefined && savings.is_sleeping !== null)
                                ? !savings.is_sleeping
                                : (device.status !== "sleep");

                            const rtcmBps = device.ntrip_stats?.rtcm_input_bps || 0;
                            const telemetryBps = device.savings_stats?.telemetry_bps || 0;
                            const currentVolts = device.system_info?.cpu?.core_volts || null;

                            return (
                                <>
                                    <div className="row g-2 mb-3">
                                        {/* CỘT THỨC (AWAKE) */}
                                        <div className="col-6">
                                            <div className="rounded p-2" style={{ backgroundColor: isAwake ? 'rgba(25,135,84,0.1)' : 'rgba(255,255,255,0.02)', border: isAwake ? '1px solid #198754' : '1px solid transparent' }}>
                                                <div className="fw-bold mb-2 text-center" style={{ color: isAwake ? '#198754' : '#8b949e', fontSize: '0.85rem' }}>
                                                    <i className="bi bi-broadcast me-1"></i> Trạng thái Thức
                                                </div>
                                                <div className="d-flex justify-content-between mb-1" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Mạng:</span>
                                                    <span className="fw-bold text-light">{isAwake ? `${rtcmBps + telemetryBps} B/s` : '--'}</span>
                                                </div>
                                                <div className="d-flex justify-content-between mb-1" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Điện áp:</span>
                                                    <span className="fw-bold text-warning">{isAwake && currentVolts ? `${currentVolts}V` : '--'}</span>
                                                </div>
                                                <div className="d-flex justify-content-between" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Công suất:</span>
                                                    <span className="fw-bold text-success">{isAwake ? '3.5 W' : '--'}</span>
                                                </div>
                                            </div>
                                        </div>
                                        
                                        {/* CỘT NGỦ (SLEEP) */}
                                        <div className="col-6">
                                            <div className="rounded p-2" style={{ backgroundColor: !isAwake ? 'rgba(13,202,240,0.1)' : 'rgba(255,255,255,0.02)', border: !isAwake ? '1px solid #0dcaf0' : '1px solid transparent' }}>
                                                <div className="fw-bold mb-2 text-center" style={{ color: !isAwake ? '#0dcaf0' : '#8b949e', fontSize: '0.85rem' }}>
                                                    <i className="bi bi-moon-stars me-1"></i> Trạng thái Ngủ
                                                </div>
                                                <div className="d-flex justify-content-between mb-1" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Mạng:</span>
                                                    <span className="fw-bold text-light">{!isAwake ? `${telemetryBps} B/s` : '--'}</span>
                                                </div>
                                                <div className="d-flex justify-content-between mb-1" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Điện áp:</span>
                                                    <span className="fw-bold text-warning">{!isAwake && currentVolts ? `${currentVolts}V` : '--'}</span>
                                                </div>
                                                <div className="d-flex justify-content-between" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Công suất:</span>
                                                    <span className="fw-bold text-success">{!isAwake ? '2.8 W' : '--'}</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    
                                    {device.savings_stats && (
                                        <div className="rounded p-2 d-flex align-items-center justify-content-between" style={{ backgroundColor: 'rgba(25,135,84,0.12)', border: '1px solid rgba(25,135,84,0.3)' }}>
                                            <div className="d-flex align-items-center">
                                                <i className="bi bi-shield-check fs-4 me-3" style={{ color: '#198754' }}></i>
                                                <div>
                                                    <div style={{ fontSize: '0.7rem', color: '#8b949e', textTransform: 'uppercase' }}>Tích lũy tài nguyên đã tiết kiệm</div>
                                                    <div className="fw-bold fs-5" style={{ color: '#198754', lineHeight: 1.2 }}>
                                                        {((device.savings_stats.total_data_saved_bytes || 0) / 1048576).toFixed(2)} <span style={{fontSize: '0.65em'}}>MB</span>
                                                    </div>
                                                </div>
                                            </div>
                                            <div className="text-end pe-2">
                                                <div className="fw-bold fs-5 text-warning" style={{ lineHeight: 1.2 }}>
                                                    {device.savings_stats.total_saved_mwh ? device.savings_stats.total_saved_mwh.toFixed(1) : "0.0"} <span style={{fontSize: '0.65em'}}>mWh</span>
                                                </div>
                                            </div>
                                        </div>
                                    )}
                                </>
                            );
                        })()}
                    </div>
                </div>
            )}

            {/* SKYPLOT & SIGNALS */}
            <div className="sky-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div className="sky-panel skyplot-panel" style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                    <div className="panel-header" style={{ flexShrink: 0 }}>
                        <i className="bi bi-radar"></i><span>Sky View</span>
                        <span className="badge bg-primary-subtle text-primary ms-auto">{satStats.total} Sats</span>
                    </div>
                    <div className="skyplot-wrapper" style={{ position: 'relative', width: '100%', height: '100%' }}>
                        <canvas ref={bgCanvasRef} className="skyplot-bg" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 1 }}></canvas>
                        <canvas ref={satCanvasRef} className="skyplot-sat" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 2 }}></canvas>
                    </div>
                </div>

                <div className="sky-panel signals-panel">
                    <div className="panel-header">
                        <i className="bi bi-bar-chart-fill"></i><span>Signal Strength (SNR)</span>
                        <div className="ms-auto d-flex gap-2">
                            <span className="badge bg-success">{satStats.tracking} Tracking</span>
                            <span className="badge bg-secondary">{satStats.total} Total</span>
                        </div>
                    </div>
                    <div ref={signalBarsContainerRef} className="signals-scroll" style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '10px' }}></div>

                    {/* Antenna Assessment */}
                    <div className="antenna-assessment-section mt-2 p-2 mx-2 mb-2 rounded" style={{ backgroundColor: 'rgba(0,0,0,0.1)' }}>
                        <div className="d-flex justify-content-between align-items-center mb-1">
                            <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                                <i className="bi bi-broadcast me-1"></i>Antenna Health
                            </span>
                            <span className="badge rounded-pill" style={{ backgroundColor: satStats.total > 0 && satStats.avgSnr > 35 ? 'var(--success-color)' : 'var(--warning-color)' }}>
                                {satStats.total > 0 ? (satStats.avgSnr || 0).toFixed(1) + ' dBHz Avg' : 'N/A'}
                            </span>
                        </div>
                        <div className="d-flex justify-content-between small text-muted">
                            <div className="text-center">
                                <div>Diversity</div>
                                <div style={{ color: Object.keys(satStats.bySystem).length >= 3 ? 'var(--success-color)' : 'var(--warning-color)', fontWeight: 'bold' }}>
                                    {Object.keys(satStats.bySystem).length} Sys
                                </div>
                            </div>
                            <div className="text-center">
                                <div>Multipath Risk</div>
                                <div style={{ color: (satStats.lowSnrCount || 0) > 5 ? 'var(--danger-color)' : 'var(--success-color)', fontWeight: 'bold' }}>
                                    {(satStats.lowSnrCount || 0)} Low SNR
                                </div>
                            </div>
                        </div>
                    </div>
                    {/* Premium Chart Overlay */}
                    {window.SatelliteBars && safeNmeaData?.satellites && Object.keys(safeNmeaData.satellites).length > 0 && (
                        <div className="mt-auto pt-2 border-top border-secondary-subtle">
                            <window.SatelliteBars satellites={safeNmeaData.satellites} />
                        </div>
                    )}
                </div>
            </div>

        </div>
    );
});
window.DashboardPanel = DashboardPanel;
})();
