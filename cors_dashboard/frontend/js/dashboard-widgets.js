// ======================
// dashboard-widgets.js
// Shared small dashboard visualization widgets.
// ======================
(() => {
'use strict';
const { memo } = React;
const MetricLineChart = memo(function MetricLineChart({ label, data = [], color, unit = "" }) {
    const max = Math.max(...data, 1);
    const min = Math.min(...data, 0);
    const range = max - min || 1;

    const points = data.map((d, i) => {
        const x = (i / Math.max(data.length - 1, 1)) * 100;
        const y = 30 - ((d - min) / range) * 30;
        return `${x},${y}`;
    }).join(' ');

    return (
        <div style={{ padding: '0 8px' }}>
            <div className="d-flex justify-content-between align-items-center mb-1">
                <span className="small text-muted">{label}</span>
                <span className="fw-bold" style={{ color }}>
                    {data.length > 0 ? data[data.length - 1] : 0}{unit}
                </span>
            </div>
            <svg width="100%" height="30" viewBox="0 0 100 30" preserveAspectRatio="none">
                <polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
        </div>
    );
});
window.MetricLineChart = MetricLineChart;

const MetricCard = memo(function MetricCard({ title, value, unit, trend, sparklineData = [], color, icon }) {
    const max = Math.max(...sparklineData, 1);
    const min = Math.min(...sparklineData, 0);
    const range = max - min || 1;

    const points = sparklineData.map((d, i) => {
        const x = (i / Math.max(sparklineData.length - 1, 1)) * 100;
        const y = 30 - ((d - min) / range) * 30;
        return `${x},${y}`;
    }).join(' ');

    return (
        <div className="metric-card bg-dark border-secondary">
            <div className="d-flex justify-content-between mb-2">
                <div>
                    <div className="text-muted small text-uppercase mb-1">
                        {icon && <i className={`bi ${icon} me-1`}></i>}
                        {title}
                    </div>
                    <div className="fs-4 fw-bold text-light">
                        {value} <span className="fs-6 text-muted">{unit}</span>
                    </div>
                </div>
                {trend !== undefined && (
                    <div className={`text-end ${trend >= 0 ? 'text-success' : 'text-danger'}`}>
                        <i className={`bi ${trend >= 0 ? 'bi-arrow-up-right' : 'bi-arrow-down-right'}`}></i>
                        <div className="small">{Math.abs(trend)}%</div>
                    </div>
                )}
            </div>
            {sparklineData.length > 0 && (
                <div style={{ height: '30px', margin: '0 -15px -15px -15px' }}>
                    <svg width="100%" height="100%" viewBox="0 0 100 30" preserveAspectRatio="none">
                        <polyline points={points} fill="none" stroke={color || '#0d6efd'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                </div>
            )}
        </div>
    );
});
window.MetricCard = MetricCard;

const SatelliteBars = memo(function SatelliteBars({ satellites }) {
    if (!satellites) return null;
    const sats = Object.values(satellites);
    if (sats.length === 0) return null;

    const counts = {};
    const snrs = {};
    sats.forEach(sat => {
        const systemMap = { 
            us: "US", ru: "RU", eu: "EU", cn: "CN", jp: "JP", un: "UN",
            G: "US", R: "RU", E: "EU", C: "CN", J: "JP" 
        };
        let sys = systemMap[sat.system] || "UN";
        if (sys === "UN") {
            const prnStr = String(sat.prn || "");
            if (prnStr.startsWith('G')) sys = "US";
            else if (prnStr.startsWith('R')) sys = "RU";
            else if (prnStr.startsWith('E')) sys = "EU";
            else if (prnStr.startsWith('B')) sys = "CN";
            else if (prnStr.startsWith('Q')) sys = "JP";
        }

        counts[sys] = (counts[sys] || 0) + 1;
        snrs[sys] = (snrs[sys] || 0) + (Number(sat.snr) || 0);
    });

    const systems = Object.keys(counts).map(sys => ({
        sys: sys, count: counts[sys], avgSnr: snrs[sys] / counts[sys]
    }));

    return (
        <div className="d-flex justify-content-around small text-muted">
            {systems.map(s => (
                <div key={s.sys} className="text-center">
                    <div className="fw-bold" style={{ color: s.avgSnr > 35 ? '#00ff88' : '#ffc107' }}>
                        {s.avgSnr.toFixed(0)} dB
                    </div>
                    <div style={{ fontSize: '0.7rem' }}>{s.sys} ({s.count})</div>
                </div>
            ))}
        </div>
    );
});
window.SatelliteBars = SatelliteBars;
})();
