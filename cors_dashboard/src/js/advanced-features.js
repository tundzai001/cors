// ==============================================================================
// advanced-features.js - PHASE 2 COMPLETE (FULL VERSION)
// ==============================================================================

// ============================================================================
// 1. REAL-TIME ACTIVITY FEED
// ============================================================================
window.ActivityFeed = React.memo(function ActivityFeed({ maxItems = 10 }) {
    const [activities, setActivities] = React.useState([]);
    const [isLoading, setIsLoading] = React.useState(true);
    const feedRef = React.useRef(null);
    
    // ✅ 1. FETCH LỊCH SỬ TỪ API KHI COMPONENT MOUNT
    React.useEffect(() => {
        async function fetchHistory() {
            try {
                const res = await window.apiFetch(`/api/activity/feed?limit=${maxItems}`);
                if (res.ok) {
                    const data = await res.json();
                    
                    // Convert timestamp (Unix seconds) sang Date object
                    const formatted = data.map(item => ({
                        id: item.id,
                        type: item.type,
                        message: item.message,
                        icon: item.icon,
                        timestamp: new Date(item.timestamp * 1000) // Unix to JS Date
                    }));
                    
                    setActivities(formatted);
                }
            } catch (e) {
                console.error('Failed to load activity history:', e);
            } finally {
                setIsLoading(false);
            }
        }
        
        void fetchHistory();
    }, [maxItems]);
    
    // ✅ 2. LISTEN FOR REAL-TIME EVENTS (GIỮ NGUYÊN CODE CŨ)
    React.useEffect(() => {
        const handleActivity = (event) => {
            const newActivity = {
                id: Date.now(), // Temporary ID
                type: event.detail.type,
                message: event.detail.message,
                timestamp: new Date(),
                icon: event.detail.icon || '📝'
            };
            
            // Thêm vào đầu danh sách
            setActivities(prev => [newActivity, ...prev].slice(0, maxItems));
            
            // Auto-scroll to top
            if (feedRef.current) {
                feedRef.current.scrollTop = 0;
            }
        };
        
        window.addEventListener('activity-log', handleActivity);
        return () => window.removeEventListener('activity-log', handleActivity);
    }, [maxItems]);
    
    const getActivityColor = (type) => {
        const colors = {
            success: '#51cf66',
            error: '#fa5252',
            warning: '#ffd43b',
            info: '#0d6efd'
        };
        return colors[type] || colors.info;
    };
    
    // ✅ 3. RENDER UI
    return (
        <div style={{
            background: 'linear-gradient(135deg, #1e2329 0%, #181c22 100%)',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            borderRadius: '12px',
            overflow: 'hidden',
            height: '100%'
        }}>
            {/* Header */}
            <div style={{
                padding: '12px 16px',
                background: 'rgba(255, 255, 255, 0.03)',
                borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
                display: 'flex', 
                alignItems: 'center', 
                gap: '8px'
            }}>
                <i className="bi bi-activity" style={{ color: '#0d6efd', fontSize: '1.2rem' }}></i>
                <span style={{ fontWeight: 600, color: '#e9ecef', fontSize: '0.9rem' }}>
                    Activity Feed
                </span>
                <span className="badge bg-primary ms-auto">{activities.length}</span>
            </div>
            
            {/* Body */}
            <div ref={feedRef} style={{ 
                maxHeight: '400px', 
                overflowY: 'auto', 
                padding: '8px' 
            }}>
                {isLoading && (
                    <div style={{ 
                        textAlign: 'center', 
                        padding: '40px 20px', 
                        color: '#868e96' 
                    }}>
                        <div className="spinner-border spinner-border-sm" role="status">
                            <span className="visually-hidden">Loading...</span>
                        </div>
                        <p className="mt-2 small">Loading history...</p>
                    </div>
                )}
                {!isLoading && activities.length === 0 && (
                    <div style={{ 
                        textAlign: 'center', 
                        padding: '40px 20px', 
                        color: '#868e96', 
                        fontSize: '0.85rem' 
                    }}>
                        <i className="bi bi-inbox" style={{ 
                            fontSize: '2rem', 
                            display: 'block', 
                            marginBottom: '8px', 
                            opacity: 0.3 
                        }}></i>
                        No recent activity
                    </div>
                )}
                {!isLoading && activities.length > 0 && (
                    activities.map(activity => (
                        <div 
                            key={activity.id} 
                            style={{
                                padding: '12px', 
                                marginBottom: '6px',
                                background: 'rgba(255, 255, 255, 0.03)',
                                borderLeft: `3px solid ${getActivityColor(activity.type)}`,
                                borderRadius: '6px', 
                                animation: 'slideInFromTop 0.3s ease', 
                                transition: 'all 0.2s ease'
                            }}
                            onMouseEnter={(e) => { 
                                e.currentTarget.style.background = 'rgba(255, 255, 255, 0.06)'; 
                            }}
                            onMouseLeave={(e) => { 
                                e.currentTarget.style.background = 'rgba(255, 255, 255, 0.03)'; 
                            }}
                        >
                            <div style={{ display: 'flex', alignItems: 'start', gap: '10px' }}>
                                <span style={{ fontSize: '1.2rem' }}>{activity.icon}</span>
                                <div style={{ flex: 1 }}>
                                    <div style={{ 
                                        fontSize: '0.85rem', 
                                        color: '#e9ecef', 
                                        marginBottom: '4px' 
                                    }}>
                                        {activity.message}
                                    </div>
                                    <div style={{ 
                                        fontSize: '0.7rem', 
                                        color: '#868e96', 
                                        fontFamily: 'monospace' 
                                    }}>
                                        {activity.timestamp.toLocaleTimeString()}
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
});

// Helper function to trigger activities (Global access) - GIỮ NGUYÊN
window.logActivity = function(type, message, icon) {
    window.dispatchEvent(new CustomEvent('activity-log', {
        detail: { type, message, icon }
    }));
};

// ============================================================================
// 2. INTERACTIVE HEATMAP
// ============================================================================
window.ActivityHeatmap = React.memo(function ActivityHeatmap({ 
    title = "Activity Heatmap" 
}) {
    const [gridData, setGridData] = React.useState([]);
    const [isLoading, setIsLoading] = React.useState(true);
    const [hoveredCell, setHoveredCell] = React.useState(null);
    
    // ✅ FETCH HEATMAP DATA FROM API
    React.useEffect(() => {
        async function fetchHeatmap() {
            try {
                const res = await window.apiFetch('/api/activity/heatmap');
                if (res.ok) {
                    const json = await res.json();
                    setGridData(json.data); // Backend trả về { data: [[...], [...]] }
                }
            } catch (e) {
                console.error('Failed to load heatmap:', e);
                // Fallback: Generate dummy data
                setGridData(
                    [...Array(7)].map(() => 
                        [...Array(24)].map(() => Math.floor(Math.random() * 100))
                    )
                );
            } finally {
                setIsLoading(false);
            }
        }
        
        void fetchHeatmap();
    }, []);
    
    // Calculate max value for color scaling
    const maxValue = React.useMemo(() => {
        if (!gridData || gridData.length === 0) return 1;
        return Math.max(...gridData.flat(), 1);
    }, [gridData]);
    
    const cellSize = 28;
    const labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    
    const getCellColor = (value) => {
        const intensity = value / maxValue;
        if (intensity > 0.75) return `rgba(250, 82, 82, ${0.3 + intensity * 0.7})`;
        if (intensity > 0.5) return `rgba(255, 212, 59, ${0.3 + intensity * 0.7})`;
        if (intensity > 0.25) return `rgba(13, 110, 253, ${0.3 + intensity * 0.7})`;
        return `rgba(81, 207, 102, ${0.2 + intensity * 0.8})`;
    };
    
    if (isLoading) {
        return (
            <div style={{
                background: 'linear-gradient(135deg, #1e2329 0%, #181c22 100%)',
                border: '1px solid rgba(255, 255, 255, 0.08)',
                borderRadius: '12px', 
                padding: '20px',
                textAlign: 'center'
            }}>
                <div className="spinner-border spinner-border-sm text-primary" role="status">
                    <span className="visually-hidden">Loading...</span>
                </div>
                <p className="mt-2 small text-muted">Loading heatmap...</p>
            </div>
        );
    }
    
    return (
        <div style={{
            background: 'linear-gradient(135deg, #1e2329 0%, #181c22 100%)',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            borderRadius: '12px', 
            padding: '20px', 
            position: 'relative'
        }}>
            <div style={{ 
                fontSize: '0.9rem', 
                fontWeight: 600, 
                color: '#e9ecef', 
                marginBottom: '16px',
                display: 'flex', 
                alignItems: 'center', 
                gap: '8px'
            }}>
                <i className="bi bi-grid-3x3-gap-fill" style={{ color: '#0d6efd' }}></i>
                {title}
            </div>
            
            <div style={{ 
                display: 'flex', 
                gap: '4px', 
                overflowX: 'auto', 
                paddingBottom: '10px' 
            }}>
                {/* Y-axis labels */}
                <div style={{ 
                    display: 'flex', 
                    flexDirection: 'column', 
                    gap: '4px', 
                    paddingTop: '24px' 
                }}>
                    {labels.map((label, i) => (
                        <div key={i} style={{ 
                            height: cellSize, 
                            display: 'flex', 
                            alignItems: 'center',
                            fontSize: '0.7rem', 
                            color: '#868e96', 
                            paddingRight: '8px', 
                            minWidth: '35px'
                        }}>
                            {label}
                        </div>
                    ))}
                </div>
                
                {/* Grid */}
                <div>
                    {/* X-axis labels */}
                    <div style={{ display: 'flex', gap: '4px', marginBottom: '4px' }}>
                        {[...Array(24)].map((_, i) => (
                            <div key={i} style={{ 
                                width: cellSize, 
                                textAlign: 'center', 
                                fontSize: '0.65rem',
                                color: '#868e96', 
                                height: '20px', 
                                display: 'flex', 
                                alignItems: 'center', 
                                justifyContent: 'center'
                            }}>
                                {i % 4 === 0 ? i : ''}
                            </div>
                        ))}
                    </div>
                    
                    {/* Cells */}
                    {gridData.map((row, y) => (
                        <div key={y} style={{ 
                            display: 'flex', 
                            gap: '4px', 
                            marginBottom: '4px' 
                        }}>
                            {row.map((value, x) => (
                                <div
                                    key={`${x}-${y}`}
                                    onMouseEnter={() => setHoveredCell({ value, x, y })}
                                    onMouseLeave={() => setHoveredCell(null)}
                                    style={{
                                        width: cellSize, 
                                        height: cellSize,
                                        background: getCellColor(value),
                                        border: '1px solid rgba(255, 255, 255, 0.1)', 
                                        borderRadius: '4px',
                                        transition: 'all 0.2s ease', 
                                        cursor: 'pointer',
                                        display: 'flex', 
                                        alignItems: 'center', 
                                        justifyContent: 'center',
                                        fontSize: '0.7rem', 
                                        color: value / maxValue > 0.5 ? '#fff' : '#868e96', 
                                        fontWeight: 600,
                                        transform: hoveredCell?.x === x && hoveredCell?.y === y 
                                            ? 'scale(1.2)' 
                                            : 'scale(1)',
                                        zIndex: hoveredCell?.x === x && hoveredCell?.y === y ? 10 : 1
                                    }}
                                >
                                    {value > 0 ? value : ''}
                                </div>
                            ))}
                        </div>
                    ))}
                </div>
            </div>
            
            {/* Tooltip */}
            {hoveredCell && (
                <div style={{
                    position: 'absolute', 
                    top: '60px', 
                    right: '20px',
                    background: 'rgba(0, 0, 0, 0.95)', 
                    border: '1px solid #0d6efd',
                    borderRadius: '8px', 
                    padding: '10px 14px', 
                    fontSize: '0.75rem', 
                    color: '#e9ecef',
                    pointerEvents: 'none', 
                    boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5)',
                    animation: 'fadeInScale 0.2s ease'
                }}>
                    <div style={{ marginBottom: '4px' }}>
                        <strong>{labels[hoveredCell.y]}</strong> at <strong>{hoveredCell.x}:00</strong>
                    </div>
                    <div style={{ color: '#0d6efd', fontWeight: 700, fontSize: '1rem' }}>
                        {hoveredCell.value} events
                    </div>
                </div>
            )}
        </div>
    );
});

// ============================================================================
// 3. ANIMATED PROGRESS RING
// ============================================================================
window.ProgressRing = React.memo(function ProgressRing({ 
    value, 
    max = 100, 
    size = 120, 
    strokeWidth = 10,
    color = '#0d6efd',
    label = '',
    subLabel = ''
}) {
    const [progress, setProgress] = React.useState(0);
    const percentage = Math.min(Math.max((value / max) * 100, 0), 100);
    const radius = (size - strokeWidth) / 2;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference - (progress / 100) * circumference;
    
    React.useEffect(() => {
        // Animate on mount
        const timer = setTimeout(() => {
            setProgress(percentage);
        }, 100);
        return () => clearTimeout(timer);
    }, [percentage]);
    
    return (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
            <div style={{ position: 'relative', width: size, height: size }}>
                <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
                    <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgba(255, 255, 255, 0.1)" strokeWidth={strokeWidth} />
                    <circle
                        cx={size / 2} cy={size / 2} r={radius} fill="none"
                        stroke={color} strokeWidth={strokeWidth} strokeLinecap="round"
                        strokeDasharray={circumference} strokeDashoffset={offset}
                        style={{ 
                            transition: 'stroke-dashoffset 1.5s cubic-bezier(0.23, 1, 0.32, 1)',
                            filter: `drop-shadow(0 0 6px ${color})`
                        }}
                    />
                </svg>
                
                <div style={{
                    position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', textAlign: 'center'
                }}>
                    <div style={{ fontSize: size / 4 + 'px', fontWeight: 700, color: '#e9ecef', fontFamily: 'monospace', lineHeight: 1 }}>
                        {Math.round(percentage)}
                    </div>
                    <div style={{ fontSize: size / 10 + 'px', color: '#868e96', marginTop: '2px' }}>%</div>
                </div>
            </div>
            
            {label && <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#e9ecef', textAlign: 'center' }}>{label}</div>}
            {subLabel && <div style={{ fontSize: '0.7rem', color: '#868e96', textAlign: 'center' }}>{subLabel}</div>}
        </div>
    );
});

// ============================================================================
console.log('✓ Advanced Features (Full Phase 2) loaded');
