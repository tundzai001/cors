// ====================== Base location mini map ======================
(() => {
'use strict';
const { useState, useEffect, memo, useRef } = React;
const { getLeaflet } = window.CorsDashboardShared || {};

const BaseLocationMap = memo(function BaseLocationMap({ coords }) {
    const mapContainerRef = useRef(null);
    const mapInstanceRef = useRef(null);
    const markerRef = useRef(null);

    useEffect(() => {
        if (!coords || !coords.lat || !coords.lon) {
            console.warn('[Map] No coords provided');
            return;
        }

        const lat = Number.parseFloat(coords.lat);
        const lon = Number.parseFloat(coords.lon);
        if (Number.isNaN(lat) || Number.isNaN(lon) || (lat === 0 && lon === 0)) return;

        const leaflet = getLeaflet ? getLeaflet() : null;
        if (!leaflet) {
            console.error('Leaflet is not loaded');
            return;
        }

        if (!mapInstanceRef.current) {
            mapInstanceRef.current = leaflet.map(mapContainerRef.current).setView([lat, lon], 16);
            leaflet.tileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', {
                maxZoom: 20, attribution: '&copy; Google'
            }).addTo(mapInstanceRef.current);
            markerRef.current = leaflet.marker([lat, lon]).addTo(mapInstanceRef.current)
                .bindPopup(`<b>Vị trí Base</b><br>Lat: ${lat}<br>Lon: ${lon}`)
                .openPopup();
        } else {
            mapInstanceRef.current.setView([lat, lon], 16);
            if (markerRef.current) markerRef.current.setLatLng([lat, lon]);
        }

        setTimeout(() => {
            if (mapInstanceRef.current) mapInstanceRef.current.invalidateSize();
        }, 300);
    }, [coords?.lat, coords?.lon]);

    useEffect(() => {
        const handleResize = () => {
            if (mapInstanceRef.current) mapInstanceRef.current.invalidateSize();
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    if (!coords || !coords.lat || (Number.parseFloat(coords.lat) === 0 && Number.parseFloat(coords.lon) === 0)) {
        return (
            <div className="d-flex align-items-center justify-content-center p-4 text-danger border rounded bg-dark"
                style={{ height: '250px', border: '2px solid red' }}>
                <div className="text-center">
                    <i className="bi bi-geo-alt-slash display-4 mb-2"></i>
                    <p className="mb-0">Chưa có tọa độ Base</p>
                    <small>Debug: lat={coords?.lat || 'null'}, lon={coords?.lon || 'null'}</small>
                </div>
            </div>
        );
    }

    if (!getLeaflet || !getLeaflet()) {
        return (
            <div className="alert alert-danger" style={{ height: '250px' }}>
                <h5>Lỗi: Leaflet chưa được tải</h5>
                <p>Kiểm tra file HTML đã nạp Leaflet CSS/JS.</p>
            </div>
        );
    }

    return (
        <div>
            <div className="d-flex justify-content-between align-items-center mb-2">
                <div className="d-flex align-items-center">
                    <h6 className="mb-0 me-3">Vị trí trạm Base</h6>
                </div>
                <a href={`https://www.google.com/maps?q=${coords.lat},${coords.lon}`} target="_blank" rel="noopener noreferrer" className="btn btn-sm btn-outline-primary">
                    <i className="bi bi-box-arrow-up-right me-1"></i>Google Maps
                </a>
            </div>
            <div style={{ position: 'relative' }}>
                <div ref={mapContainerRef} style={{ width: '100%', height: '300px', borderRadius: '8px', border: '2px solid #444', background: '#222', zIndex: 0 }} />
            </div>
            <div className="mt-2 small text-muted">Lat: {Number.parseFloat(coords.lat).toFixed(8)} | Lon: {Number.parseFloat(coords.lon).toFixed(8)}</div>
        </div>
    );
});

window.BaseLocationMap = BaseLocationMap;
})();
