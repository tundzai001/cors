// ======================
// satellite-sky-view.js
// GNSS skyplot and satellite signal-strength view.
// ======================
(() => {
'use strict';
const { useState, useEffect, useRef, memo } = React;

const flags = { us: new Image(), ru: new Image(), eu: new Image(), cn: new Image(), jp: new Image(), un: new Image() };
flags.us.src = '/assets/img/flags/us.jpg';
flags.ru.src = '/assets/img/flags/ru.jpg';
flags.eu.src = '/assets/img/flags/eu.jpg';
flags.cn.src = '/assets/img/flags/cn.jpg';
flags.jp.src = '/assets/img/flags/jp.jpg';
flags.un.src = '/assets/img/flags/un.jpg';

const colors = { us: '#00d47e', ru: '#ff5c5c', eu: '#5c9cff', cn: '#ffb95c', jp: '#ff77a9', un: '#aaaaaa' };
const constellationNames = { us: 'GPS (USA)', ru: 'GLONASS (Russia)', eu: 'Galileo (EU)', cn: 'BeiDou (China)', jp: 'QZSS (Japan)', un: 'Unknown' };

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

    return getSatelliteSystemFromPrn(Number(sat.prn)) || 'un';
}

function getSatelliteSystemFromPrn(prn) {
    if (prn >= 1 && prn <= 32) return 'us';
    if (prn >= 65 && prn <= 96) return 'ru';
    if (prn >= 301 && prn <= 336) return 'eu';
    if (prn >= 193 && prn <= 200) return 'jp';
    if ((prn >= 33 && prn <= 64) || (prn >= 401 && prn <= 463)) return 'cn';
    return null;
}

// Chuẩn hóa PRN về dải RTCM (1-based) để NMEA và RTCM cùng vệ tinh dùng chung key.
function getNormalizedPrn(sat) {
    const prnRaw = sat.prn;
    const prnStr = String(prnRaw || '');
    // Handle string PRNs from RTCM decoder (e.g. "G01", "R07", "E12", "C14")
    const match = prnStr.match(/^[A-Z]?(\d+)$/);
    const prn = match ? Number(match[1]) : Number(prnRaw);
    if (Number.isNaN(prn)) return prnStr; // fallback to raw string
    const sys = getSatelliteSystem(sat);
    if (sys === 'ru' && prn >= 65 && prn <= 96) return String(prn - 64);
    if (sys === 'eu' && prn >= 301 && prn <= 336) return String(prn - 300);
    if (sys === 'cn' && prn >= 401 && prn <= 463) return String(prn - 400);
    if (sys === 'jp' && prn >= 193 && prn <= 200) return String(prn - 192);
    return String(prn);
}

function getSatelliteKey(sat) {
    return `${getSatelliteSystem(sat)}:${getNormalizedPrn(sat)}`;
}

function updateSkyplotSatellite(buffer, sat, now) {
    const key = getSatelliteKey(sat);
    const existing = buffer.get(key);
    const satSnr = Number(sat.snr) || 0;
    const existingSnr = existing ? (Number(existing.snr) || 0) : 0;
    const newAz = Number(sat.azimuth) || 0;
    const newEl = Number(sat.elevation) || 0;
    const hasNewPos = newAz !== 0 || newEl !== 0;
    const hasOldPos = existing && (Number(existing.azimuth) !== 0 || Number(existing.elevation) !== 0);

    if (!existing || satSnr > 0 || existingSnr === 0) {
        const position = !hasNewPos && hasOldPos
            ? { azimuth: existing.azimuth, elevation: existing.elevation }
            : {};
        buffer.set(key, { ...sat, ...position, lastSeen: now, opacity: 1.0 });
        return;
    }

    existing.lastSeen = now;
    if (satSnr > 0) existing.snr = satSnr;
    if (hasNewPos) {
        existing.azimuth = newAz;
        existing.elevation = newEl;
    }
}

function refreshSkyplotSatelliteBuffer(buffer, satellites, now) {
    for (const sat of satellites) updateSkyplotSatellite(buffer, sat, now);
}

function updateSkyplotSatelliteOpacity(buffer, now, timeout, fadeDuration) {
    for (const [key, sat] of buffer.entries()) {
        const age = now - sat.lastSeen;
        if (age > timeout + fadeDuration) {
            buffer.delete(key);
        } else if (age > timeout) {
            sat.opacity = Math.max(0, 1 - ((age - timeout) / fadeDuration));
        } else {
            sat.opacity = 1.0;
        }
    }
}

function drawSkyplotSatellite(ctx, satData, centerX, centerY, radius) {
    let az = Number(satData.azimuth);
    let el = Number(satData.elevation);
    const snr = Number(satData.snr);
    if (Number.isNaN(snr) || snr === 0) return;
    if (Number.isNaN(az)) az = 0;
    if (Number.isNaN(el)) el = 0;

    const azRad = (az * Math.PI) / 180;
    const dist = radius * (90 - el) / 90;
    const x = centerX + dist * Math.sin(azRad);
    const y = centerY - dist * Math.cos(azRad);
    const system = getSatelliteSystem(satData);
    const flag = flags[system];
    const flagSize = satData.isTracking ? 18 : 14;

    ctx.globalAlpha = satData.opacity || 1.0;
    if (satData.isTracking) {
        ctx.beginPath();
        ctx.arc(x, y, flagSize / 2 + 3, 0, 2 * Math.PI);
        ctx.fillStyle = (colors[system] || '#fff') + '40';
        ctx.fill();
    }

    if (flag && flag.complete) {
        ctx.drawImage(flag, x - flagSize / 2, y - flagSize / 2, flagSize, flagSize);
    } else {
        ctx.beginPath();
        ctx.arc(x, y, 9, 0, 2 * Math.PI);
        ctx.fillStyle = colors[system] || '#ccc';
        ctx.fill();
    }

    ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
    ctx.fillRect(x - 8, y + flagSize / 2 + 2, 16, 10);
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 7px Arial';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(getNormalizedPrn(satData), x, y + flagSize / 2 + 7);
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

const skyPlotSystems = ['us', 'ru', 'eu', 'cn', 'jp', 'un'];

function getSatelliteStructureHash(groupedSatellites) {
    return skyPlotSystems
        .map(system => groupedSatellites[system] ? `${system}:${groupedSatellites[system].map(sat => sat.prn).join(',')}` : '')
        .join('|');
}

function satelliteBarColor(snr) {
    if (snr >= 40) return '#00ff88';
    if (snr >= 20) return '#ffc107';
    return '#ff5252';
}

function createSkySatelliteBar(sat) {
    const barFillHeight = Math.min(100, sat.snr / 55 * 100);
    const barColor = satelliteBarColor(sat.snr);
    const itemDiv = document.createElement('div');
    itemDiv.className = 'sat-bar-item';
    itemDiv.id = `sat-bar-${getSatelliteKey(sat).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
    itemDiv.innerHTML = `
        <div class="sat-bar-snr" style="color: ${barColor}; font-weight: bold;">${Number(sat.snr)}</div>
        <div class="sat-bar"><div class="sat-bar-fill" style="height: ${barFillHeight}%; background: ${barColor};"></div></div>
        <div class="sat-bar-prn">${Number.isFinite(Number(getNormalizedPrn(sat))) ? Number(getNormalizedPrn(sat)) : ''}</div>
    `;
    return itemDiv;
}

function createSkySatelliteGroup(system, satellites) {
    const groupDiv = document.createElement('div');
    groupDiv.className = 'constellation-group';
    const headerDiv = document.createElement('div');
    headerDiv.className = 'constellation-header';
    headerDiv.innerHTML = `<img src="${flags[system].src}" width="24" height="24" /> ${constellationNames[system]} (<span class="group-count">${satellites.length}</span>)`;
    const barsWrapper = document.createElement('div');
    barsWrapper.className = 'bars-wrapper';
    satellites.forEach(sat => barsWrapper.appendChild(createSkySatelliteBar(sat)));
    groupDiv.appendChild(headerDiv);
    groupDiv.appendChild(barsWrapper);
    return groupDiv;
}

function createSkySatelliteBars(groupedSatellites) {
    const fragment = document.createDocumentFragment();
    skyPlotSystems.forEach(system => {
        const satellites = groupedSatellites[system];
        if (!satellites || satellites.length === 0) return;
        fragment.appendChild(createSkySatelliteGroup(system, satellites));
    });
    return fragment;
}

function updateSkySatelliteBar(sat) {
    const itemDiv = document.getElementById(`sat-bar-${getSatelliteKey(sat).replace(/[^a-zA-Z0-9_-]/g, '-')}`);
    if (!itemDiv) return;
    const barFillHeight = Math.min(100, sat.snr / 55 * 100);
    const barColor = satelliteBarColor(sat.snr);
    const snrText = itemDiv.querySelector('.sat-bar-snr');
    const fillDiv = itemDiv.querySelector('.sat-bar-fill');
    if (snrText) { snrText.textContent = sat.snr; snrText.style.color = barColor; }
    if (fillDiv) { fillDiv.style.height = `${barFillHeight}%`; fillDiv.style.background = barColor; }
}

function updateSkySatelliteBars(groupedSatellites) {
    skyPlotSystems.forEach(system => {
        const satellites = groupedSatellites[system];
        if (!satellites) return;
        satellites.forEach(updateSkySatelliteBar);
    });
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
            refreshSkyplotSatelliteBuffer(buffer, satellites, now);
            updateSkyplotSatelliteOpacity(buffer, now, SATELLITE_TIMEOUT, FADE_DURATION);

            satCtx.clearRect(0, 0, satCtx.canvas.width, satCtx.canvas.height);
            if (satCtx.canvas.width === 0 || satCtx.canvas.height === 0) return;

            const centerX = satCtx.canvas.width / 2;
            const centerY = satCtx.canvas.height / 2;
            const radius = Math.min(centerX, centerY) * 0.82;

            for (const satData of buffer.values()) drawSkyplotSatellite(satCtx, satData, centerX, centerY, radius);
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
                    .sort((a, b) => Number.parseFloat(a.prn) - Number.parseFloat(b.prn));
            }

            const totalSats = allSats.length;
            const trackingSats = allSats.filter(s => s.isTracking).length;
            setSatStats({ total: totalSats, tracking: trackingSats, bySystem: groupedFinal });

            const structureHash = getSatelliteStructureHash(groupedFinal);

            if (lastSatelliteDataRef.current !== structureHash) {
                lastSatelliteDataRef.current = structureHash;
                container.innerHTML = '';
                container.appendChild(createSkySatelliteBars(groupedFinal));
                return;
            }

            // Fast in-place SNR update
            updateSkySatelliteBars(groupedFinal);
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

        const updateStatsIfDue = (now, gga, gsa, satellites) => {
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
        };

        const drawCanvasIfDue = (now, satellites) => {
            if (now - lastCanvasUpdate.current >= CANVAS_INTERVAL) {
                drawSkyplotSatellites(Object.values(satellites || {}));
                lastCanvasUpdate.current = now;
            }
        };

        const drawBarsIfDue = (now, satellites) => {
            if (now - lastBarsUpdate.current >= BARS_INTERVAL) {
                drawSignalBars(Object.values(satellites || {}));
                lastBarsUpdate.current = now;
            }
        };

        const animate = () => {
            const now = Date.now();
            const { gga, gsa, satellites } = safeNmeaDataRef.current;
            updateStatsIfDue(now, gga, gsa, satellites);
            drawCanvasIfDue(now, satellites);
            drawBarsIfDue(now, satellites);
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
