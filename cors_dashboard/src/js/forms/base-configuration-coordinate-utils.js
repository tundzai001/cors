export const chipCoordinate = (value, digits) => {
    const text = String(value).trim().replace(',', '.');
    if (!/^-?\d+(?:\.\d+)?$/.test(text)) return text;
    const [whole, fraction = ''] = text.split('.');
    return `${whole}.${fraction.padEnd(digits, '0').slice(0, digits)}`;
};

export const formatApprovedAltitude = (value, chip) => chip === 'Ublox'
    ? chipCoordinate(value, 4) : Number(value).toFixed(4);
