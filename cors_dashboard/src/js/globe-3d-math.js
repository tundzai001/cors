// Geospatial & 3D Mathematical utilities for Three.js Earth Globe
(() => {
    'use strict';

    const RE_EARTH_KM = 6371.0;
    const SCENE_EARTH_RADIUS = 5.0; // Three.js units for Earth radius
    const H_IONO_KM = 350.0;
    const SCENE_IONO_RADIUS = SCENE_EARTH_RADIUS * (1.0 + H_IONO_KM / RE_EARTH_KM); // ~5.275 units

    // Convert Lat/Lon (degrees) and Altitude (km) to Three.js Cartesian Vector3
    // Matches Three.js standard SphereGeometry UV equirectangular mapping:
    // Lat 0, Lon 0 is at (r, 0, 0); North Pole at (0, r, 0); Lon 90E at (0, 0, -r)
    function latLonAltToVector3(latDeg, lonDeg, altKm = 0) {
        const phi = (latDeg || 0) * (Math.PI / 180.0);
        const lambda = (lonDeg || 0) * (Math.PI / 180.0);
        const r = SCENE_EARTH_RADIUS * (1.0 + Math.max(0, altKm) / RE_EARTH_KM);
        return new window.THREE.Vector3(
            r * Math.cos(phi) * Math.cos(lambda),
            r * Math.sin(phi),
            -r * Math.cos(phi) * Math.sin(lambda)
        );
    }

    // Calculate line-of-sight intersection point with ionospheric thin-shell (350km altitude)
    function calculateIpp3D(stationPos3D, satellitePos3D) {
        if (!stationPos3D || !satellitePos3D) return null;
        const dir = satellitePos3D.clone().sub(stationPos3D).normalize();
        const b = 2 * stationPos3D.dot(dir);
        const c = stationPos3D.lengthSq() - (SCENE_IONO_RADIUS * SCENE_IONO_RADIUS);
        const discr = b * b - 4 * c;
        if (discr <= 0) return null;
        const t = (-b + Math.sqrt(discr)) / 2;
        return stationPos3D.clone().add(dir.multiplyScalar(t));
    }

    function groundRegion(features, lat, lon) {
        if (!Array.isArray(features)) return 'Đang tải tên khu vực…';
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return '--';
        const inRing = ring => {
            let inside = false;
            for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
                const [xi, yi] = ring[i], [xj, yj] = ring[j];
                if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
            }
            return inside;
        };
        for (const feature of features) {
            const geometry = feature.geometry;
            const polygons = geometry?.type === 'Polygon' ? [geometry.coordinates] : geometry?.type === 'MultiPolygon' ? geometry.coordinates : [];
            if (polygons.some(rings => rings[0] && inRing(rings[0]) && !rings.slice(1).some(inRing))) {
                const properties = feature.properties || {};
                return properties.name_vi || properties.ADMIN || properties.name || properties.NAME || 'Đất liền';
            }
        }
        return 'Biển / đại dương';
    }

    function surfaceViewFromRay(position, direction, fov, height) {
        const b = position.x * direction.x + position.y * direction.y + position.z * direction.z;
        const c = position.x ** 2 + position.y ** 2 + position.z ** 2 - SCENE_EARTH_RADIUS ** 2;
        const discriminant = b * b - c;
        if (discriminant < 0 || height <= 0) return null;
        const distance = -b - Math.sqrt(discriminant);
        if (distance <= 0) return null;
        const x = position.x + direction.x * distance;
        const y = position.y + direction.y * distance;
        const z = position.z + direction.z * distance;
        const lat = Math.max(-85, Math.min(85, Math.asin(Math.max(-1, Math.min(1, y / SCENE_EARTH_RADIUS))) * 180 / Math.PI));
        const lng = Math.atan2(-z, x) * 180 / Math.PI;
        const metersPerPixel = distance * RE_EARTH_KM / SCENE_EARTH_RADIUS * 2000 * Math.tan(fov * Math.PI / 360) / height;
        const zoom = Math.log2(156543.03392 * Math.cos(lat * Math.PI / 180) / metersPerPixel);
        return { lat, lng, zoom };
    }

    // Export to window
    window.Globe3DMath = {
        RE_EARTH_KM,
        SCENE_EARTH_RADIUS,
        H_IONO_KM,
        SCENE_IONO_RADIUS,
        latLonAltToVector3,
        calculateIpp3D,
        groundRegion,
        surfaceViewFromRay,
        // Keep the 2D map available down to world view; switch only at minimum zoom.
        shouldEnterGlobe: zoom => Number.isFinite(zoom) && zoom <= 2,
    };
})();
