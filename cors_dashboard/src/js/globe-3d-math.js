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

    // Export to window
    window.Globe3DMath = {
        RE_EARTH_KM,
        SCENE_EARTH_RADIUS,
        H_IONO_KM,
        SCENE_IONO_RADIUS,
        latLonAltToVector3,
        calculateIpp3D,
    };
})();
