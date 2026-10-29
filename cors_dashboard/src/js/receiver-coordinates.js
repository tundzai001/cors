// Read the validated receiver log so JSON number formatting cannot shorten LLA.
(() => {
    'use strict';
    window.CorsReceiverCoordinates = {
        fromReceiver(position, allowUnfixed = false) {
            if (position?.message !== 'UBX') return this.fromUnicore(position, allowUnfixed);
            const isBaseFixed = position.position_type === 'FIXEDPOS';
            const isSurveyEstimate = allowUnfixed && position.position_type === 'SURVEY-IN-ESTIMATE';
            if (position.state !== 'ok' || (!isBaseFixed && !isSurveyEstimate)
                || position.datum !== 'WGS84' || position.altitude_reference !== 'ELLIPSOID') return null;
            const { lat, lon, alt } = position;
            if ([lat, lon, alt].some(value => typeof value !== 'string'
                || !/^-?\d+(?:\.\d+)?$/.test(value) || !Number.isFinite(Number(value)))
                || Math.abs(Number(lat)) > 90 || Math.abs(Number(lon)) > 180) return null;
            return { lat, lon, alt, heightMsl: alt, undulation: '0.0000', source: 'UBX',
                positionType: position.position_type, isBaseFixed };
        },
        fromUnicore(position, allowUnfixed = false) {
            if (position?.state !== 'ok' || (position.position_type !== 'FIXEDPOS'
                && !(allowUnfixed && position.position_type === 'SINGLE'))
                || typeof position.raw !== 'string' || !position.raw.startsWith('#BESTNAVA,')) return null;
            const fields = position.raw.split(';')[1]?.split('*')[0].split(',');
            if (!fields || fields[0] !== 'SOL_COMPUTED' || fields[1] !== position.position_type
                || fields[6] !== 'WGS84') return null;
            const values = fields.slice(2, 6).map(value => value.trim());
            if (values.length !== 4 || values.some(value => !/^-?\d+(?:\.\d+)?$/.test(value)
                || !Number.isFinite(Number(value)))) return null;
            const [lat, lon, heightMsl, undulation] = values;
            if (Math.abs(Number(lat)) > 90 || Math.abs(Number(lon)) > 180) return null;
            // BESTNAV reports H above MSL and N (geoid undulation); h = H + N.
            const heightEllipsoid = Number(heightMsl) + Number(undulation);
            if (!Number.isFinite(heightEllipsoid)) return null;
            return { lat, lon, alt: heightEllipsoid.toFixed(4), heightMsl, undulation,
                isBaseFixed: position.position_type === 'FIXEDPOS' };
        }
    };
})();
