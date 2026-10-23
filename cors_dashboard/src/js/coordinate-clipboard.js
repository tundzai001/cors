// Named coordinates keep clipboard order, reference frame and height type explicit.
(() => {
    'use strict';
    const TYPE = 'aitogy/gnss-fixed-position';
    // Cut once at copy time; keep decimal text and trailing zeros through chip setup.
    function decimal10(value) {
        if (typeof value !== 'number' && typeof value !== 'string') return null;
        let text = String(value).trim().replace(',', '.');
        if (typeof value === 'number' && /e/i.test(text)) text = value.toFixed(20);
        if (!/^-?\d+(?:\.\d+)?$/.test(text) || !Number.isFinite(Number(text))) return null;
        const [whole, fraction = ''] = text.split('.');
        return `${whole}.${fraction.padEnd(10, '0').slice(0, 10)}`;
    }
    function validate(coords) {
        const values = coords && Object.fromEntries(['lat', 'lon', 'alt'].map(key => [key, decimal10(coords[key])]));
        if (!values || Object.values(values).some(value => value === null) ||
            Math.abs(Number(String(coords.lat).replace(',', '.'))) > 90 || Math.abs(Number(String(coords.lon).replace(',', '.'))) > 180) {
            throw new Error('Tọa độ sao chép thiếu hoặc không hợp lệ. Cần đủ vĩ độ, kinh độ và độ cao h.');
        }
        return values;
    }
    window.CorsCoordinateClipboard = {
        encode(coords) {
            return JSON.stringify({ type: TYPE, version: 1, crs: 'EPSG:4979', height_type: 'ellipsoidal', coords: validate(coords) });
        },
        parse(text) {
            let packet;
            try { packet = JSON.parse(text); } catch (_) { return null; }
            if (packet?.type !== TYPE) return null;
            if (packet.version !== 1 || packet.crs !== 'EPSG:4979' || packet.height_type !== 'ellipsoidal') {
                throw new Error('Dữ liệu dán chưa phải WGS 84 với độ cao h. Hãy dùng “Sao chép cho GNSS” trong công cụ chuyển tọa độ.');
            }
            return validate(packet.coords);
        }
    };
})();
