// ==============================================================================
// dashboard-charts.js - Advanced Chart Components (FULL VERSION)
// ==============================================================================

// ============================================================================
// 1. SPARKLINE COMPONENT (Mini chart)
// ============================================================================
window.Sparkline = React.memo(function Sparkline({ data, color = '#0d6efd', width = 80, height = 30 }) {
    const canvasRef = React.useRef(null);

    React.useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || !data || data.length < 2) return;
        
        const ctx = canvas.getContext('2d');
        canvas.width = width;
        canvas.height = height;
        
        const max = Math.max(...data, 1);
        const min = Math.min(...data, 0);
        const range = max - min || 1;
        
        ctx.clearRect(0, 0, width, height);
        
        // Draw line
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.shadowBlur = 4;
        ctx.shadowColor = color;
        
        data.forEach((val, i) => {
            const x = (width / (data.length - 1)) * i;
            const y = height - ((val - min) / range) * height;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.shadowBlur = 0;
        
        // Draw area fill
        ctx.lineTo(width, height);
        ctx.lineTo(0, height);
        ctx.closePath();
        
        const gradient = ctx.createLinearGradient(0, 0, 0, height);
        gradient.addColorStop(0, color + '40');
        gradient.addColorStop(1, color + '00');
        ctx.fillStyle = gradient;
        ctx.fill();
        
    }, [data, color, width, height]);

    return <canvas ref={canvasRef} style={{ display: 'block' }} />;
});

// ============================================================================
// 2. ENHANCED TIME SERIES CHART (Biểu đồ theo thời gian thực)
// ============================================================================
window.TimeSeriesChart = React.memo(function TimeSeriesChart({ 
    data, 
    title, 
    unit = '', 
    color = '#0d6efd',
    height = 250 
}) {
    const canvasRef = React.useRef(null);

    React.useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        
        const ctx = canvas.getContext('2d');
        // Handle High DPI displays
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);
        
        const width = rect.width;
        const chartHeight = rect.height; // Rename to avoid conflict with prop 'height'
        const padding = { top: 40, right: 30, bottom: 40, left: 60 };
        const chartAreaWidth = width - padding.left - padding.right;
        const chartAreaHeight = chartHeight - padding.top - padding.bottom;
        
        ctx.clearRect(0, 0, width, chartHeight);
        
        // Background
        ctx.fillStyle = 'rgba(13, 110, 253, 0.02)';
        ctx.fillRect(padding.left, padding.top, chartAreaWidth, chartAreaHeight);
        
        // Grid lines
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 5; i++) {
            const y = padding.top + (chartAreaHeight / 5) * i;
            ctx.beginPath();
            ctx.moveTo(padding.left, y);
            ctx.lineTo(padding.left + chartAreaWidth, y);
            ctx.stroke();
        }
        
        if (!data || data.length < 2) return;
        
        const maxValue = Math.max(...data, 10);
        const minValue = Math.min(...data, 0);
        const range = maxValue - minValue || 1;
        
        // Area fill
        ctx.beginPath();
        data.forEach((val, i) => {
            const x = padding.left + (chartAreaWidth / (data.length - 1)) * i;
            const y = padding.top + chartAreaHeight - ((val - minValue) / range) * chartAreaHeight;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.lineTo(padding.left + chartAreaWidth, padding.top + chartAreaHeight);
        ctx.lineTo(padding.left, padding.top + chartAreaHeight);
        ctx.closePath();
        
        const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + chartAreaHeight);
        gradient.addColorStop(0, color + '60');
        gradient.addColorStop(1, color + '00');
        ctx.fillStyle = gradient;
        ctx.fill();
        
        // Line
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5;
        ctx.shadowBlur = 8;
        ctx.shadowColor = color;
        data.forEach((val, i) => {
            const x = padding.left + (chartAreaWidth / (data.length - 1)) * i;
            const y = padding.top + chartAreaHeight - ((val - minValue) / range) * chartAreaHeight;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.shadowBlur = 0;
        
        // Y-axis labels
        ctx.fillStyle = '#868e96';
        ctx.font = '11px monospace';
        ctx.textAlign = 'right';
        for (let i = 0; i <= 5; i++) {
            const val = maxValue - (range / 5) * i;
            const y = padding.top + (chartAreaHeight / 5) * i + 4;
            ctx.fillText(val.toFixed(0) + unit, padding.left - 10, y);
        }
        
        // X-axis time labels
        ctx.textAlign = 'center';
        const timeLabels = ['60s', '45s', '30s', '15s', 'Now'];
        timeLabels.forEach((label, i) => {
            const x = padding.left + (chartAreaWidth / (timeLabels.length - 1)) * i;
            ctx.fillText(label, x, chartHeight - 15);
        });
        
    }, [data, color, unit, height]);

    return (
        <div style={{ position: 'relative', height: height + 'px', width: '100%' }}>
            <div style={{ 
                position: 'absolute', 
                top: '10px', 
                left: '15px', 
                color: '#e9ecef',
                fontSize: '0.85rem',
                fontWeight: 600,
                zIndex: 10
            }}>
                {title}
            </div>
            <canvas ref={canvasRef} style={{ width: '100%', height: '100%' }} />
        </div>
    );
});

// ============================================================================
// 3. RADIAL GAUGE (Đồng hồ đo tốc độ/chỉ số)
// ============================================================================
window.RadialGauge = React.memo(function RadialGauge({ 
    value, 
    max, 
    title, 
    unit = '', 
    color = '#51cf66' 
}) {
    const percentage = Math.min(Math.max((value / max) * 100, 0), 100);
    const circumference = 2 * Math.PI * 65;
    const offset = circumference - (percentage / 100) * circumference;
    
    return (
        <div style={{
            background: 'linear-gradient(135deg, #1e2329 0%, #181c22 100%)',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            borderRadius: '12px',
            padding: '20px',
            textAlign: 'center',
            position: 'relative',
            overflow: 'hidden'
        }}>
            <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#e9ecef', marginBottom: '16px' }}>
                {title}
            </div>
            
            <div style={{ position: 'relative', width: '150px', height: '150px', margin: '0 auto 16px' }}>
                <svg style={{ width: '100%', height: '100%', transform: 'rotate(-90deg)' }}>
                    <circle cx="75" cy="75" r="65" fill="none" stroke="rgba(255, 255, 255, 0.1)" strokeWidth="12" />
                    <circle
                        cx="75"
                        cy="75"
                        r="65"
                        fill="none"
                        stroke={color}
                        strokeWidth="12"
                        strokeLinecap="round"
                        strokeDasharray={circumference}
                        strokeDashoffset={offset}
                        style={{ 
                            transition: 'stroke-dashoffset 1s ease',
                            filter: `drop-shadow(0 0 8px ${color})`
                        }}
                    />
                </svg>
                
                <div style={{
                    position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                    fontSize: '2rem', fontWeight: 700, color: '#e9ecef', fontFamily: 'monospace'
                }}>
                    {value}
                    <div style={{ fontSize: '0.8rem', color: '#868e96', marginTop: '4px' }}>{unit}</div>
                </div>
            </div>
            
            <div style={{ fontSize: '0.75rem', color: '#868e96' }}>
                {percentage.toFixed(1)}% of {max} {unit}
            </div>
        </div>
    );
});

// ============================================================================
// 4. METRIC CARD WITH SPARKLINE (Thẻ chỉ số kèm biểu đồ nhỏ)
// ============================================================================
window.MetricCard = React.memo(function MetricCard({ 
    title, 
    value, 
    unit = '', 
    trend, 
    sparklineData, 
    color = '#0d6efd', 
    icon = '📊' 
}) {
    const trendDirection = trend > 0 ? 'up' : trend < 0 ? 'down' : 'neutral';
    const trendColor = trend > 0 ? '#51cf66' : trend < 0 ? '#fa5252' : '#868e96';
    
    return (
        <div className="hover-lift" style={{
            background: 'linear-gradient(135deg, #1e2329 0%, #181c22 100%)',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            borderRadius: '12px',
            padding: '16px',
            position: 'relative',
            overflow: 'hidden',
            cursor: 'pointer'
        }}>
            <div style={{
                position: 'absolute', right: '10px', top: '10px', fontSize: '2.5rem', opacity: 0.1, color: color
            }}>
                {icon}
            </div>
            
            <div style={{ position: 'relative', zIndex: 1 }}>
                <div style={{ fontSize: '0.75rem', color: '#868e96', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '8px' }}>
                    {title}
                </div>
                
                <div style={{ fontSize: '2rem', fontWeight: 700, color: '#e9ecef', fontFamily: 'monospace', marginBottom: '8px' }}>
                    {value}
                    <span style={{ fontSize: '1rem', color: '#868e96', marginLeft: '4px' }}>{unit}</span>
                </div>
                
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <i className={`bi bi-arrow-${trendDirection === 'up' ? 'up' : 'down'}-short`} 
                           style={{ color: trendColor, fontSize: '1.2rem' }}></i>
                        <span style={{ fontSize: '0.85rem', color: trendColor, fontWeight: 600 }}>
                            {Math.abs(trend).toFixed(1)}%
                        </span>
                    </div>
                    
                    {sparklineData && sparklineData.length > 0 && (
                        <window.Sparkline data={sparklineData} color={color} />
                    )}
                </div>
            </div>
        </div>
    );
});

console.log('✓ Dashboard Charts (Full) loaded');