// ==============================================================================
// dashboard-charts.js
// Modern, real-time charting components for the Station Dashboard
// ==============================================================================

const { useEffect, useRef, memo } = React;

/**
 * A premium-styled line chart for tracking numeric metrics over time.
 * Optimized for performance in React.
 */
const MetricLineChart = memo(function MetricLineChart({ 
    label, 
    data, 
    color = '#0dcaf0', 
    maxPoints = 30,
    unit = ''
}) {
    const canvasRef = useRef(null);
    const chartRef = useRef(null);

    useEffect(() => {
        if (!canvasRef.current) return;

        const ctx = canvasRef.current.getContext('2d');
        
        // Create gradient for premium look
        const gradient = ctx.createLinearGradient(0, 0, 0, 200);
        gradient.addColorStop(0, color.replace(')', ', 0.3)').replace('rgb', 'rgba'));
        gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');

        chartRef.current = new Chart(ctx, {
            type: 'line',
            data: {
                labels: Array(maxPoints).fill(''),
                datasets: [{
                    label: label,
                    data: data,
                    borderColor: color,
                    backgroundColor: gradient,
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointBackgroundColor: color
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        backgroundColor: 'rgba(20, 20, 20, 0.9)',
                        titleColor: '#888',
                        bodyColor: '#fff',
                        borderColor: '#444',
                        borderWidth: 1,
                        callbacks: {
                            label: (context) => ` ${context.dataset.label}: ${context.parsed.y}${unit}`
                        }
                    }
                },
                scales: {
                    x: { display: false },
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: 'rgba(255, 255, 255, 0.05)',
                            drawBorder: false
                        },
                        ticks: {
                            color: '#666',
                            font: { size: 10 },
                            maxTicksLimit: 5
                        }
                    }
                },
                interaction: {
                    intersect: false,
                    mode: 'nearest',
                },
                animation: { duration: 0 }
            }
        });

        return () => {
            if (chartRef.current) {
                chartRef.current.destroy();
            }
        };
    }, []);

    // Efficiently update chart data without full re-render
    useEffect(() => {
        if (chartRef.current) {
            chartRef.current.data.datasets[0].data = data;
            chartRef.current.update('none');
        }
    }, [data]);

    return (
        <div style={{ height: '120px', width: '100%', position: 'relative' }}>
            <canvas ref={canvasRef}></canvas>
        </div>
    );
});

/**
 * Bar chart showing SNR/CNR for individual satellites.
 */
const SatelliteBars = memo(function SatelliteBars({ satellites = {} }) {
    const canvasRef = useRef(null);
    const chartRef = useRef(null);

    // Filter and sort satellites by strength
    const satList = Object.values(satellites)
        .filter(s => s.snr > 0)
        .sort((a, b) => b.snr - a.snr)
        .slice(0, 32);

    useEffect(() => {
        if (!canvasRef.current) return;

        const ctx = canvasRef.current.getContext('2d');
        
        chartRef.current = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: satList.map(s => s.id),
                datasets: [{
                    label: 'SNR',
                    data: satList.map(s => s.snr),
                    backgroundColor: satList.map(s => {
                        if (s.snr > 40) return '#198754'; // Excellent (Green)
                        if (s.snr > 30) return '#0dcaf0'; // Good (Cyan)
                        if (s.snr > 20) return '#ffc107'; // Fair (Yellow)
                        return '#dc3545'; // Poor (Red)
                    }),
                    borderRadius: 4,
                    barPercentage: 0.8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(20, 20, 20, 0.9)',
                        callbacks: {
                            label: (ctx) => ` SNR: ${ctx.parsed.y} dB-Hz`
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: '#888', font: { size: 9 } }
                    },
                    y: {
                        beginAtZero: true,
                        max: 60,
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: { color: '#666', font: { size: 9 } }
                    }
                },
                animation: { duration: 400, easing: 'easeOutQuart' }
            }
        });

        return () => {
            if (chartRef.current) chartRef.current.destroy();
        };
    }, []);

    // Update bars
    useEffect(() => {
        if (chartRef.current) {
            chartRef.current.data.labels = satList.map(s => s.id);
            chartRef.current.data.datasets[0].data = satList.map(s => s.snr);
            chartRef.current.data.datasets[0].backgroundColor = satList.map(s => {
                if (s.snr > 40) return '#198754';
                if (s.snr > 30) return '#0dcaf0';
                if (s.snr > 20) return '#ffc107';
                return '#dc3545';
            });
            chartRef.current.update();
        }
    }, [satellites]);

    return (
        <div style={{ height: '180px', width: '100%' }}>
            <canvas ref={canvasRef}></canvas>
        </div>
    );
});

// Export to global scope for react-components.js
window.MetricLineChart = MetricLineChart;
window.SatelliteBars = SatelliteBars;
