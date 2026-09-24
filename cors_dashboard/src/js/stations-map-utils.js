// ======================
// stations-map-utils.js
// Leaflet marker helpers and geographic utilities for station maps.
// ======================
(() => {
'use strict';
const { getLeaflet } = window.CorsDashboardShared || {};

const createColoredIcon = (color) => {
    const leaflet = getLeaflet ? getLeaflet() : null;
    if (!leaflet) return null;
    return leaflet.divIcon({
        className: 'custom-div-icon station-marker-icon',
        html: `<span class="station-marker-dot" style="--station-color: ${color};"></span>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11]
    });
};

const getRoverStatusMeta = (status) => {
    if (status === 4) return { label: 'Fixed', color: '#22c55e' };
    if (status === 5) return { label: 'Float', color: '#38bdf8' };
    if (status === 0) return { label: '3D', color: '#ef4444' };
    return { label: 'Unknown', color: '#a3a3a3' };
};

const createRoverIcon = (color) => {
    const leaflet = getLeaflet ? getLeaflet() : null;
    if (!leaflet) return null;
    const svg = `
        <svg fill="${color}" stroke="white" stroke-width="2" viewBox="0 0 24 24" width="24" height="24" style="filter: drop-shadow(0px 2px 3px rgba(0,0,0,0.8));">
            <path d="M12 2 L22 20 L2 20 Z" />
        </svg>
    `;
    return leaflet.divIcon({
        className: 'custom-div-icon rover-marker-icon',
        html: svg,
        iconSize: [24, 24],
        iconAnchor: [12, 12]
    });
};

const createRoverMarker = (lat, lon, color) => {
    const leaflet = getLeaflet ? getLeaflet() : null;
    if (!leaflet) return null;
    return leaflet.marker([lat, lon], {
        icon: createRoverIcon(color),
        riseOnHover: true,
        keyboard: false
    });
};

const animateMarkerLatLng = (marker, lat, lon, durationMs = 650) => {
    if (!marker) return;

    if (marker.__animFrame) {
        cancelAnimationFrame(marker.__animFrame);
        marker.__animFrame = null;
    }

    const from = marker.getLatLng();
    const fromLat = Number(from.lat);
    const fromLon = Number(from.lng);
    const toLat = Number(lat);
    const toLon = Number(lon);

    if (!Number.isFinite(fromLat) || !Number.isFinite(fromLon) || !Number.isFinite(toLat) || !Number.isFinite(toLon)) {
        marker.setLatLng([lat, lon]);
        return;
    }

    const dLat = toLat - fromLat;
    const dLon = toLon - fromLon;
    if (Math.abs(dLat) < 0.0000001 && Math.abs(dLon) < 0.0000001) return;

    const start = performance.now();
    const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);
    const step = (now) => {
        const t = Math.min(1, (now - start) / durationMs);
        const eased = easeOutCubic(t);
        marker.setLatLng([fromLat + dLat * eased, fromLon + dLon * eased]);
        if (t < 1) {
            marker.__animFrame = requestAnimationFrame(step);
        } else {
            marker.__animFrame = null;
        }
    };

    marker.__animFrame = requestAnimationFrame(step);
};

const getDistanceMeters = (lat1, lon1, lat2, lon2) => {
    const toRad = (deg) => (deg * Math.PI) / 180;
    const R = 6371000;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
        Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
};

window.StationsMapUtils = {
    createColoredIcon,
    getRoverStatusMeta,
    createRoverIcon,
    createRoverMarker,
    animateMarkerLatLng,
    getDistanceMeters
};
})();
