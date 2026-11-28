(() => {
    'use strict';
    function normalizeAngleInput(value, axis) {
        if (!['lat', 'lon'].includes(axis)) throw new Error('Trục tọa độ không hợp lệ.');
        let text = String(value).trim().toUpperCase().replace(/−/g, '-').replace(/,/g, '.');
        const prefix = text.match(/^([NSEW])\s*/);
        const suffix = text.match(/([NSEW])$/);
        if (prefix && suffix) throw new Error('Chỉ ghi hướng N/S/E/W một lần.');
        const direction = prefix?.[1] || suffix?.[1];
        if (direction && !(axis === 'lat' ? 'NS' : 'EW').includes(direction)) {
            throw new Error(axis === 'lat' ? 'Vĩ độ dùng N hoặc S.' : 'Kinh độ dùng E hoặc W.');
        }
        if (prefix) text = text.slice(prefix[0].length).trim();
        if (suffix) text = text.slice(0, text.length - suffix[0].length).trim();
        const explicitSign = /^[+-]/.test(text) ? text[0] : '';
        if (direction && explicitSign && (explicitSign === '-') !== 'SW'.includes(direction)) {
            throw new Error('Dấu tọa độ mâu thuẫn với hướng N/S/E/W.');
        }
        return {text, negative: direction ? 'SW'.includes(direction) : explicitSign === '-'};
    }

    function parseDecimalDegrees(text) {
        if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:E[+-]?\d+)?$/.test(text)) throw new Error('Nhập độ thập phân hợp lệ.');
        return {degrees: Math.abs(Number(text)), minutes: 0, seconds: 0};
    }

    function parseDms(text) {
        const match = text.match(/^[+-]?(\d{1,3})(?:\s*°\s*|\s+|:)(\d{1,2})(?:\s*['′’]\s*|\s+|:)(\d{1,2}(?:\.\d+)?)(?:\s*["″”])?$/);
        if (!match) throw new Error('Nhập DMS như 22°20′40.1″N hoặc 22 20 40.1.');
        return {degrees: Number(match[1]), minutes: Number(match[2]), seconds: Number(match[3])};
    }

    function parsePackedDms(text, axis) {
        if (!/^[+-]?\d+(?:\.\d+)?$/.test(text)) throw new Error('Nhập DMS gộp như 222040.1.');
        const [whole, fraction = ''] = text.replace(/^[+-]/, '').split('.');
        const width = axis === 'lat' ? 6 : 7;
        if (whole.length > width) throw new Error('DMS gộp có quá nhiều chữ số.');
        const digits = whole.padStart(width, '0');
        return {
            degrees: Number(digits.slice(0, -4)),
            minutes: Number(digits.slice(-4, -2)),
            seconds: Number(digits.slice(-2) + '.' + fraction),
        };
    }

    function parseAngleParts(text, axis, mode) {
        const parsers = {
            dd: () => parseDecimalDegrees(text),
            dms: () => parseDms(text),
            packed_dms: () => parsePackedDms(text, axis),
        };
        if (!parsers[mode]) throw new Error('Định dạng góc không hợp lệ.');
        return parsers[mode]();
    }

    function parse(value, axis, mode = 'dd') {
        const {text, negative} = normalizeAngleInput(value, axis);
        const {degrees, minutes, seconds} = parseAngleParts(text, axis, mode);
        if (minutes >= 60 || seconds >= 60) throw new Error('Phút và giây phải nhỏ hơn 60.');
        const angle = degrees + minutes / 60 + seconds / 3600;
        if (!Number.isFinite(angle) || angle > (axis === 'lat' ? 90 : 180)) {
            throw new Error(axis === 'lat' ? 'Vĩ độ phải trong khoảng −90° đến 90°.' : 'Kinh độ phải trong khoảng −180° đến 180°.');
        }
        return negative ? -angle : angle;
    }
    function format(value, axis, mode = 'dd') {
        const number = parse(String(value), axis);
        if (mode === 'dd') return String(number);
        const total = Math.round(Math.abs(number) * 3600 * 1e6);
        const degrees = Math.floor(total / 3600e6);
        const minutes = Math.floor(total % 3600e6 / 60e6);
        const seconds = String(Number((total % 60e6 / 1e6).toFixed(6)));
        if (mode === 'dms') {
            let direction;
            if (axis === 'lat') direction = number < 0 ? 'S' : 'N';
            else direction = number < 0 ? 'W' : 'E';
            return `${degrees}°${String(minutes).padStart(2, '0')}′${seconds}″${direction}`;
        }
        if (mode !== 'packed_dms') throw new Error('Định dạng góc không hợp lệ.');
        const [whole, fraction] = seconds.split('.');
        return `${number < 0 ? '-' : ''}${String(degrees).padStart(axis === 'lat' ? 2 : 3, '0')}${String(minutes).padStart(2, '0')}${whole.padStart(2, '0')}${fraction ? '.' + fraction : ''}`;
    }
    function parsePair(value) {
        // Direction suffixes identify the axes even when latitude is pasted first.
        const text = String(value).trim();
        const matches = [...text.matchAll(/([+-]?\d{1,3}(?:\s*°\s*|\s+|:)\d{1,2}(?:\s*['′’]\s*|\s+|:)\d{1,2}(?:[.,]\d+)?(?:\s*["″”])?\s*[NSEW])/gi)];
        if (matches.length !== 2) return null;
        if (text.replace(matches[0][0], '').replace(matches[1][0], '').replace(/[\s,;]+/g, '')) return null;
        const result = {};
        for (const match of matches) {
            const axis = /[NS]$/i.test(match[0]) ? 'lat' : 'lon';
            if (axis in result) throw new Error('Cặp tọa độ cần một vĩ độ N/S và một kinh độ E/W.');
            result[axis] = parse(match[0], axis, 'dms');
        }
        return result;
    }
    window.CorsCoordinateAngles = {parse, format, parsePair};
})();
