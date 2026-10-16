// Match IGS Network's rotation: 120 seconds/revolution, slowing between zoom 3–5.
export function spinStep(zoom) {
    return 3 * Math.max(0, Math.min(1, (5 - zoom) / 2));
}

export function flightOptions(center, zoom, reducedMotion = false) {
    return {
        center, zoom, bearing: 0, pitch: 0,
        speed: 1.15, curve: 1.42, maxDuration: 5000,
        ...(reducedMotion ? { duration: 0 } : {}),
    };
}
