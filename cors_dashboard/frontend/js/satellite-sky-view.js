// ======================
// satellite-sky-view.js
// GNSS skyplot and satellite signal-strength view.
// ======================
(() => {
'use strict';
const { useState, useEffect, useRef, memo } = React;

const flags = { us: new Image(), ru: new Image(), eu: new Image(), cn: new Image(), jp: new Image(), un: new Image() };
flags.us.src = '/img/flags/us.jpg';
flags.ru.src = '/img/flags/ru.jpg';
flags.eu.src = '/img/flags/eu.jpg';
flags.cn.src = '/img/flags/cn.jpg';
flags.jp.src = '/img/flags/jp.jpg';
flags.un.src = '/img/flags/un.jpg';

const colors = { us: '#00d47e', ru: '#ff5c5c', eu: '#5c9cff', cn: '#ffb95c', jp: '#ff77a9', un: '#aaaaaa' };
const constellationNames = { us: 'GPS (USA)', ru: 'GLONASS (Russia)', eu: 'Galileo (EU)', cn: 'BeiDou (China)', jp: 'QZSS (Japan)', un: 'Unknown' };

function getSatelliteSystem(sat) {
    if (sat.system && ['us', 'ru', 'eu', 'cn', 'jp'].includes(sat.system)) return sat.system;
    const prn = sat.prn;
    if (prn >= 1 && prn <= 32) return 'us';
    if (prn >= 65 && prn <= 96) return 'ru';
    if (prn >= 301 && prn <= 336) return 'eu';
    if (prn >= 193 && prn <= 200) return 'jp';
    if ((prn >= 33 && prn <= 64) || (prn >= 401 && prn <= 463)) return 'cn';
    return 'un';
}

function getSatelliteKey(sat) {
    return `${getSatelliteSystem(sat)}:${sat.prn}`;
}

function getVisibleSatellites(satellites, maxAgeMs) {
    const now = Date.now();
    const visibleByKey = new Map();

    (satellites || []).forEach(sat => {
        const snr = Number(sat?.snr);
        if (!sat || !Number.isFinite(snr) || snr <= 0) return;

        let lastSeen = Number(sat.lastSeen || sat.last_seen || 0);
        if (lastSeen > 0 && lastSeen < 1e11) lastSeen *= 1000;
        if (lastSeen > 0 && now - lastSeen > maxAgeMs) return;

        const key = getSatelliteKey(sat);
        const existing = visibleByKey.get(key);
        if (!existing || snr > (Number(existing.snr) || 0)) {
            visibleByKey.set(key, sat);
        }
    });

    return Array.from(visibleByKey.values());
}

const SatelliteSkyView = memo(function SatelliteSkyView({ nmeaData }) {
    const bgCanvasRef = useRef(null);
    const satCanvasRef = useRef(null);
    const animationFrameRef = useRef(null);
    const lastSatelliteDataRef = useRef(null);
    const signalBarsContainerRef = useRef(null);
    const satelliteBufferRef = useRef(new Map());
    const safeNmeaData = nmeaData || { gga: null, gsa: null, satellites: {} };
    const safeNmeaDataRef = useRef(safeNmeaData);
    const lastStatsUpdate = useRef(0);
    const lastCanvasUpdate = useRef(0);
    const lastBarsUpdate = useRef(0);
    const [satStats, setSatStats] = useState({ total: 0, tracking: 0, bySystem: {} });
    // Resolve SatelliteBars sau khi scripts load xong (tránh race condition)
    const [SatBars, setSatBars] = useState(() => window.SatelliteBars || null);
    useEffect(() => {
        if (window.SatelliteBars) { setSatBars(() => window.SatelliteBars); return; }
        // Poll ngắn để chờ script load
        let attempts = 0;
        const tid = setInterval(() => {
            if (window.SatelliteBars) { setSatBars(() => window.SatelliteBars); clearInterval(tid); }
            if (++attempts > 30) clearInterval(tid); // dừng sau 3 giây
        }, 100);
        return () => clearInterval(tid);
    }, []);

    useEffect(() => {
        safeNmeaDataRef.current = safeNmeaData;
    }, [safeNmeaData]);

    useEffect(() => {
        satelliteBufferRef.current.clear();
        lastSatelliteDataRef.current = null;
        if (satCanvasRef.current) {
            const ctx = satCanvasRef.current.getContext('2d');
            ctx.clearRect(0, 0, satCanvasRef.current.width, satCanvasRef.current.height);
        }
        if (signalBarsContainerRef.current) {
            signalBarsContainerRef.current.innerHTML = '';
        }
        setSatStats({ total: 0, tracking: 0, bySystem: {} });
    }, []);

    useEffect(() => {
        if (!bgCanvasRef.current || !satCanvasRef.current) return;

        const bgCtx = bgCanvasRef.current.getContext('2d');
        const satCtx = satCanvasRef.current.getContext('2d');
        const SATELLITE_TIMEOUT = 8000;
        const FADE_DURATION = 2000;
        const STATS_INTERVAL = 800;
        const CANVAS_INTERVAL = 100;   // ~10 FPS skyplot
        const BARS_INTERVAL = 150;     // ~7 FPS signal bars

        const drawSkyplotBase = () => {
            const w = bgCtx.canvas.width;
            const h = bgCtx.canvas.height;
            if (w === 0 || h === 0) return;

            const centerX = w / 2;
            const centerY = h / 2;
            const radius = Math.min(centerX, centerY) * 0.85;

            bgCtx.clearRect(0, 0, w, h);
            const grad = bgCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
            grad.addColorStop(0, '#1a2332');
            grad.addColorStop(0.5, '#0d1520');
            grad.addColorStop(1, '#000');
            bgCtx.fillStyle = grad;
            bgCtx.fillRect(0, 0, w, h);

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

            bgCtx.strokeStyle = '#2a3a4a';
            bgCtx.lineWidth = 1;
            bgCtx.beginPath();
            for (let i = 0; i < 12; i++) {
                const angle = i * 30 * Math.PI / 180;
                bgCtx.moveTo(centerX, centerY);
                bgCtx.lineTo(centerX + radius * Math.sin(angle), centerY - radius * Math.cos(angle));
            }
            bgCtx.stroke();

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
                const age = now - satData.lastSeen;
                if (age > SATELLITE_TIMEOUT + FADE_DURATION) {
                    buffer.delete(satKey);
                } else if (age > SATELLITE_TIMEOUT) {
                    satData.opacity = Math.max(0, 1 - ((age - SATELLITE_TIMEOUT) / FADE_DURATION));
                } else {
                    satData.opacity = 1.0;
                }
            }

            satCtx.clearRect(0, 0, satCtx.canvas.width, satCtx.canvas.height);
            if (satCtx.canvas.width === 0 || satCtx.canvas.height === 0) return;

            const centerX = satCtx.canvas.width / 2;
            const centerY = satCtx.canvas.height / 2;
            const radius = Math.min(centerX, centerY) * 0.82;

            for (const satData of buffer.values()) {
                let az = Number(satData.azimuth);
                let el = Number(satData.elevation);
                const snr = Number(satData.snr);
                if (isNaN(snr) || snr === 0) continue;
                if (isNaN(az)) az = 0;
                if (isNaN(el)) el = 0;

                const azRad = (az * Math.PI) / 180;
                const dist = radius * (90 - el) / 90;
                const x = centerX + dist * Math.sin(azRad);
                const y = centerY - dist * Math.cos(azRad);
                const system = getSatelliteSystem(satData);
                const flag = flags[system];
                const flagSize = satData.isTracking ? 18 : 14;

                satCtx.globalAlpha = satData.opacity || 1.0;
                if (satData.isTracking) {
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
                satCtx.fillText(satData.prn, x, y + flagSize / 2 + 7);
            }
            satCtx.globalAlpha = 1.0;
        };

        const drawSignalBars = (satellites) => {
            const container = signalBarsContainerRef.current;
            if (!container) return;

            // Use buffer so vệ tinh LTE không mất khi ephemeris cycle
            const buffer = satelliteBufferRef.current;
            const allSats = [];
            for (const satData of buffer.values()) {
                if ((Number(satData.snr) || 0) > 0) allSats.push(satData);
            }
            // Also include fresh sats not yet in buffer
            satellites.forEach(sat => {
                const snr = Number(sat?.snr) || 0;
                if (snr <= 0) return;
                if (!buffer.has(getSatelliteKey(sat))) allSats.push(sat);
            });

            const groupedSats = {};
            allSats.forEach(sat => {
                const system = getSatelliteSystem(sat);
                if (!groupedSats[system]) groupedSats[system] = new Map();
                const k = getSatelliteKey(sat);
                const ex = groupedSats[system].get(k);
                if (!ex || Number(sat.snr) > Number(ex.snr)) groupedSats[system].set(k, sat);
            });
            // Convert maps to sorted arrays
            const groupedFinal = {};
            for (const sys of Object.keys(groupedSats)) {
                groupedFinal[sys] = Array.from(groupedSats[sys].values())
                    .sort((a, b) => parseFloat(a.prn) - parseFloat(b.prn));
            }

            const totalSats = allSats.length;
            const trackingSats = allSats.filter(s => s.isTracking).length;
            setSatStats({ total: totalSats, tracking: trackingSats, bySystem: groupedFinal });

            const structureHash = ['us','ru','eu','cn','jp','un']
                .map(sys => groupedFinal[sys] ? `${sys}:${groupedFinal[sys].map(s => s.prn).join(',')}` : '')
                .join('|');

            if (lastSatelliteDataRef.current !== structureHash) {
                lastSatelliteDataRef.current = structureHash;
                const fragment = document.createDocumentFragment();
                ['us', 'ru', 'eu', 'cn', 'jp', 'un'].forEach(system => {
                    if (!groupedFinal[system] || groupedFinal[system].length === 0) return;
                    const sats = groupedFinal[system];
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
                        itemDiv.id = `sat-bar-${getSatelliteKey(sat).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                        itemDiv.innerHTML = `
                            <div class="sat-bar-snr" style="color: ${barColor}; font-weight: bold;">${sat.snr}</div>
                            <div class="sat-bar"><div class="sat-bar-fill" style="height: ${barFillHeight}%; background: ${barColor};"></div></div>
                            <div class="sat-bar-prn">${sat.prn}</div>
                        `;
                        barsWrapper.appendChild(itemDiv);
                    });
                    groupDiv.appendChild(headerDiv);
                    groupDiv.appendChild(barsWrapper);
                    fragment.appendChild(groupDiv);
                });
                container.innerHTML = '';
                container.appendChild(fragment);
                return;
            }

            // Fast in-place SNR update
            ['us', 'ru', 'eu', 'cn', 'jp', 'un'].forEach(system => {
                if (!groupedFinal[system]) return;
                groupedFinal[system].forEach(sat => {
                    const itemDiv = document.getElementById(`sat-bar-${getSatelliteKey(sat).replace(/[^a-zA-Z0-9_-]/g, '-')}`);
                    if (!itemDiv) return;
                    const barFillHeight = Math.min(100, sat.snr / 55 * 100);
                    let barColor = colors[system];
                    if (sat.snr >= 40) barColor = '#00ff88';
                    else if (sat.snr >= 20) barColor = '#ffc107';
                    else barColor = '#ff5252';
                    const snrText = itemDiv.querySelector('.sat-bar-snr');
                    const fillDiv = itemDiv.querySelector('.sat-bar-fill');
                    if (snrText) { snrText.textContent = sat.snr; snrText.style.color = barColor; }
                    if (fillDiv) { fillDiv.style.height = `${barFillHeight}%`; fillDiv.style.background = barColor; }
                });
            });
        };

        const handleResize = () => {
            if (!bgCanvasRef.current || !satCanvasRef.current) return;
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
        };

        const animate = () => {
            const now = Date.now();
            const { gga, gsa, satellites } = safeNmeaDataRef.current;
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

    return (
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

            <div className="sky-panel signals-panel" style={{ display: 'flex', flexDirection: 'column' }}>
                <div className="panel-header" style={{ flexShrink: 0 }}>
                    <i className="bi bi-bar-chart-fill"></i><span>Signal Strength (SNR)</span>
                    <div className="ms-auto d-flex gap-2">
                        <span className="badge bg-success">{satStats.tracking} Tracking</span>
                        <span className="badge bg-secondary">{satStats.total} Total</span>
                    </div>
                </div>
                <div ref={signalBarsContainerRef} className="signals-scroll" style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '10px' }}></div>

                {/* Constellation summary bar at the bottom — luôn hiển thị khi SatBars available */}
                {SatBars && (
                    <div style={{ flexShrink: 0, borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '6px', paddingBottom: '6px' }}>
                        <SatBars satellites={safeNmeaData.satellites} />
                    </div>
                )}
            </div>
        </div>
    );
});

window.SatelliteSkyView = SatelliteSkyView;
})();
