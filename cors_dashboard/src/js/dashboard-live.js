// ====================== Live GNSS dashboard panels ======================
(() => {
const { useState, useEffect, memo, useRef } = React;

function formatTelemetryNumber(value, digits, fallback) {
    if (typeof value !== 'number' && typeof value !== 'string') return fallback;
    if (typeof value === 'string' && !value.trim()) return fallback;
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : fallback;
}

function getGnssQuality(device, nmeaData, live = true, now = Date.now() / 1000) {
    if (!live) return null;
    const quality = device.gnss_quality;
    if (quality && ['ubx_nav_pvt', 'nmea_gst', 'unicore_bestnav_sigma'].includes(quality.source)) {
        const received = Number(device.server_received_at || device.timestamp);
        const age = quality.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0);
        const limit = quality.source === 'nmea_gst' ? 15 : 45;
        if (typeof quality.age_seconds !== 'number' || !Number.isFinite(age) || age < 0 || age > limit) return null;
        return {...quality, age_seconds: age,
            hacc_m: typeof quality.hacc_m === 'number' && Number.isFinite(quality.hacc_m)
                && quality.hacc_m >= 0 && quality.fix_valid !== false ? quality.hacc_m : null};
    }
    const gst = nmeaData?.gst;
    const age = now - gst?.received_at;
    if (gst?.hacc_source === 'gst_lat_lon_sigma' && typeof gst.hacc === 'number'
        && Number.isFinite(gst.hacc) && gst.hacc > 0 && age >= 0 && age <= 15) {
        return {hacc_m: gst.hacc, source: 'nmea_gst', age_seconds: age};
    }
    // Untagged legacy hacc can be mm, m or HDOP. Do not guess its units.
    return null;
}

function getGnssDop(device, data, live = true, now = Date.now() / 1000) {
    const usable = value => (typeof value === 'number' || (typeof value === 'string' && value.trim()))
        && Number.isFinite(Number(value)) && Number(value) > 0 && Number(value) < 99 ? Number(value) : null;
    if (!live) return {hdop: null, pdop: null, source: 'Thiết bị ngoại tuyến'};
    const dop = device.gnss_dop;
    const receipt = Number(device.server_received_at || device.timestamp);
    const age = dop?.age_seconds + (Number.isFinite(receipt) ? Math.max(0, now - receipt) : 0);
    const readback = dop?.source === 'ubx_nav_dop' && age >= 0 && age <= 45 ? dop : null;
    const fresh = entry => !entry?.received_at || now - entry.received_at <= 15;
    const gsa = fresh(data?.gsa) ? data?.gsa : null;
    const gga = fresh(data?.gga) ? data?.gga : null;
    const quality = getGnssQuality(device, data, live, now);
    const reported = value => typeof value === 'number' && Number.isFinite(value)
        && value >= 0 && value <= 655.35 ? value : null;
    const rawHdop = reported(readback?.hdop), rawPdop = reported(readback?.pdop);
    const hdop = rawHdop > 0 ? rawHdop : usable(gsa?.hdop) ?? usable(gga?.hdop);
    const pdop = rawPdop > 0 ? rawPdop : usable(gsa?.pdop) ?? usable(quality?.pdop);
    const timeOnly = quality?.source === 'ubx_nav_pvt' && quality.fix_type === 5;
    return {hdop, pdop, timeOnly, rawHdop, rawPdop, source: timeOnly
        ? 'Time-only: chip dùng tọa độ cố định, DOP vị trí không áp dụng.'
        : readback ? `NAV-DOP · ${Math.floor(age)} giây`
        : hdop !== null || pdop !== null ? 'GSA / GGA / NAV-PVT' : 'Chưa đọc được DOP từ chip'};
}

function getDashboardPosition(device, data) {
    const position = (lat, lon, source) => {
        const valid = value => (typeof value === 'number' || (typeof value === 'string' && value.trim()))
            && Number.isFinite(Number(value));
        return valid(lat) && valid(lon) && Math.abs(Number(lat)) <= 90 && Math.abs(Number(lon)) <= 180
            ? {lat, lon, source} : null;
    };
    const gga = data?.gga;
    const live = gga?.fix_status !== 'NO_FIX' && (!gga?.received_at || Date.now() / 1000 - gga.received_at <= 15)
        ? position(gga?.latitude, gga?.longitude, 'NMEA GGA') : null;
    if (live) return live;
    const readback = device.system_info?.receiver_base_readback;
    const receiver = readback?.state === 'ok' && readback.age_seconds <= 45
        ? window.CorsReceiverCoordinates?.fromReceiver(readback.bestpos) : null;
    const saved = device.base_config?.coords || device.base_config;
    return position(receiver?.lat, receiver?.lon, 'Chip trả về')
        || position(data?.base_coords?.lat, data?.base_coords?.lon, 'RTCM 1005/1006')
        || position(saved?.lat, saved?.lon, 'Cấu hình Base đã lưu');
}

function getReceiverSatellites(device, live = true, now = Date.now() / 1000) {
    const snapshot = device.gnss_satellites;
    if (!live || snapshot?.source !== 'ubx_nav_sat' || typeof snapshot.payload_b64 !== 'string'
        || snapshot.payload_b64.length > 2060 || typeof snapshot.age_seconds !== 'number') return null;
    const received = Number(device.server_received_at || device.timestamp);
    const age = snapshot.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0);
    if (!Number.isFinite(age) || age < 0 || age > 45) return null;
    try {
        const raw = atob(snapshot.payload_b64);
        const bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
        if (bytes.length < 8 || bytes[4] !== 1 || bytes[5] > 128 || bytes.length !== 8 + bytes[5] * 12) return null;
        const view = new DataView(bytes.buffer), seen = new Set(), satellites = [];
        const systems = ['us', 'sb', 'eu', 'cn', 'im', 'jp', 'ru', 'in'];
        for (let offset = 8; offset < bytes.length; offset += 12) {
            const gnss = bytes[offset], prn = bytes[offset + 1], key = `${gnss}:${prn}`;
            if (gnss === 6 && prn === 255) continue; // No known slot to correlate with decoded RTCM.
            if (gnss > 7 || prn === 0 || seen.has(key)) return null;
            seen.add(key);
            const cno = bytes[offset + 2], elevation = view.getInt8(offset + 3);
            const azimuth = view.getInt16(offset + 4, true), flags = view.getUint32(offset + 8, true);
            const geometry = elevation >= -90 && elevation <= 90 && azimuth >= 0 && azimuth <= 360;
            satellites.push({id: `UBX:${key}`, system: systems[gnss], prn, snr: cno,
                elevation: geometry ? elevation : null, azimuth: geometry ? azimuth : null,
                has_geometry: geometry, isTracking: (flags & 7) >= 2,
                used_in_solution: Boolean(flags & 8), source: 'ubx_nav_sat',
                lastSeen: (now - age) * 1000});
        }
        return {satellites, age_seconds: age};
    } catch (_) { return null; }
}

function getSatelliteTelemetrySource(sat) {
    if (sat?.telemetry_source) return sat.telemetry_source;
    const identifier = String(sat?.talker || sat?.id || '');
    if (/^RTCM(?::|$)/i.test(identifier)) return 'rtcm';
    if (/^(G[PNLABDQI]|BD|QZ)(?::|$)/i.test(identifier)) return 'nmea';
    return null;
}

function getSatellitePlotSource(device, data, live = true, now = Date.now() / 1000) {
    const receiver = getReceiverSatellites(device, live, now);
    const angles = new Map((receiver?.satellites || [])
        .filter(hasSatellitePosition).map(sat => [getSatelliteKey(sat), sat]));
    const satellites = live ? Object.values(data?.satellites || {})
        .filter(sat => getSatelliteTelemetrySource(sat) === 'rtcm').map(sat => {
            const angle = angles.get(getSatelliteKey(sat));
            return angle ? {...sat, azimuth: angle.azimuth, elevation: angle.elevation,
                has_geometry: true, geometry_source: 'ubx_nav_sat'} : sat;
        }) : [];
    return {satellites, source: 'RTCM', maxAgeMs: 7000};
}

function getAutomaticRfAssessment(device, live = true, now = Date.now() / 1000) {
    const report = device?.gnss_rf_monitor;
    if (device?.detected_chip_type !== 'Unicorecomm' && device?.gnss_receiver?.itfm_supported === false && report?.source !== 'ubx_sec_sig')
        return {tone:'secondary',title:'Chờ dữ liệu SEC-SIG',report:null};
    const received = Number(device?.server_received_at || device?.timestamp);
    const age = report?.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0);
    if (!report || !['ubx_mon_rf','unicore_rf','ubx_sec_sig'].includes(report.source)
        || !['sample_count','warning_samples','critical_samples','episodes','coincident_cno_drops','quality_comparisons'].every(key => Number.isInteger(report[key]) && report[key] >= 0 && report[key] <= 120)
        || typeof report.age_seconds !== 'number' || !Number.isFinite(age) || age < 0)
        return {tone:'secondary',title:'Chờ dữ liệu RF',report:null};
    if (!live || age > 45 || report.current_severity == null)
        return {tone:'warning',title:'Tạm thiếu mẫu RF',report};
    if (report.sample_count < 3) return {tone:'info',title:'Đang thu mẫu',report};
    return {tone:report.warning_samples ? 'warning' : 'info',report,
        title:report.coincident_cno_drops ? 'Cảnh báo RF kèm C/N₀ giảm'
            : report.warning_samples ? 'Có cảnh báo RF' : 'Không có cảnh báo trong các mẫu đã thu'};
}

function getRfMonitorWindow(device, live = true, now = Date.now() / 1000) {
    const assessment = getAutomaticRfAssessment(device, live, now), report = assessment.report;
    if (!report) return {...assessment, elapsed:0, age:null, samples:[]};
    const received = Number(device.server_received_at || device.timestamp);
    const age = report.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0);
    const duration = typeof report.observed_seconds === 'number' ? report.observed_seconds : report.last_sample_at - report.first_sample_at;
    const fixed = report.window_mode === 'fixed' && Number.isFinite(report.window_started_at) && report.window_ends_at - report.window_started_at === 600;
    const progress = fixed && live && age <= 45 ? now - report.window_started_at : duration;
    const elapsed = Number.isFinite(progress) ? Math.max(0,Math.min(600,progress)) : 0;
    const samples = Array.isArray(report.samples) && report.samples.length <= 120 ? report.samples.filter(sample =>
        Array.isArray(sample) && [3,7].includes(sample.length) && typeof sample[0] === 'number' && Number.isFinite(sample[0]) && sample[0] >= 0 && sample[0] + age <= 600
        && [0,1,2].includes(sample[1]) && (sample[2] === null || typeof sample[2] === 'number' && Number.isFinite(sample[2]))
        && (sample.length === 3 || [sample[3],sample[4]].every(value=>Number.isInteger(value)&&value>=0&&value<=128)
            && sample.slice(5).every(value=>value===null||typeof value==='number'&&Number.isFinite(value)&&Math.abs(value)<=255))) : [];
    return {...assessment,elapsed,age,samples,fixed};
}

function getRfMonitorHistory(report) {
    const rows = report?.history_24h?.hours;
    const stamp = report?.history_24h?.last_sample_at;
    if (typeof stamp !== 'number' || !Number.isFinite(stamp) || stamp <= 0 || !Array.isArray(rows) || rows.length > 25) return [];
    return rows.filter(row=>Array.isArray(row)&&row.length===7&&Number.isInteger(row[0])&&row[0]>0
        &&row.slice(1,6).every(value=>Number.isInteger(value)&&value>=0&&value<=20000)
        &&row[2]<=row[1]&&row[3]<=row[2]&&row[4]<=row[2]&&row[5]<=row[1]
        &&(row[6]===null||typeof row[6]==='number'&&Number.isFinite(row[6])&&Math.abs(row[6])<=255));
}

function getRfDiagnostic(device, field, live = true, now = Date.now() / 1000) {
    const snapshot = device?.[field], spectrum = field === 'gnss_spectrum';
    if (!live || !snapshot || snapshot.source !== (spectrum ? 'ubx_mon_span' : 'ubx_mon_rf')
        || typeof snapshot.payload_b64 !== 'string' || snapshot.payload_b64.length > 1456
        || typeof snapshot.age_seconds !== 'number') return null;
    const received = Number(device.server_received_at || device.timestamp);
    const age = snapshot.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0);
    if (!Number.isFinite(age) || age < 0 || age > 45) return null;
    try {
        const bytes = Uint8Array.from(atob(snapshot.payload_b64), c => c.charCodeAt(0));
        const stride = spectrum ? 272 : 24;
        if (bytes.length < 4 || bytes[0] !== 0 || bytes[1] < 1 || bytes[1] > 4
            || bytes.length !== 4 + bytes[1] * stride) return null;
        const view = new DataView(bytes.buffer), blocks = [];
        for (let o = 4; o < bytes.length; o += stride) {
            if (spectrum) {
                const span = view.getUint32(o + 256, true), resolution = view.getUint32(o + 260, true);
                const center = view.getUint32(o + 264, true);
                if (!span || !resolution || !center) return null;
                blocks.push({bins: Array.from(bytes.slice(o, o + 256)), span, resolution, center, pga: bytes[o + 268]});
            } else {
                const id = bytes[o], agc = view.getUint16(o + 14, true);
                blocks.push({id, index: blocks.length, agc_valid: agc <= 8191, jamming: bytes[o + 1] & 3, antenna: bytes[o + 2], power: bytes[o + 3],
                    noise: view.getUint16(o + 12, true), agc, cw: bytes[o + 16]});
            }
        }
        return {source: snapshot.source, age_seconds: age, blocks};
    } catch (_) { return null; }
}

function getSecurityDiagnostic(device, field = 'gnss_security', live = true, now = Date.now() / 1000) {
    const raw = device?.[field], log = field === 'gnss_security_log';
    const received = Number(device?.server_received_at || device?.timestamp);
    const age = raw?.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0);
    if (!live || raw?.source !== (log ? 'ubx_sec_siglog' : 'ubx_sec_sig') || typeof raw.age_seconds !== 'number' || !Number.isFinite(age) || age < 0 || age > 45) return null;
    if (raw.error) return {error:String(raw.error).slice(0,160),age_seconds:age};
    if (typeof raw.payload_b64 !== 'string' || raw.payload_b64.length > (log ? 184 : 92)) return null;
    try {
        const bytes = Uint8Array.from(atob(raw.payload_b64), c=>c.charCodeAt(0)), view = new DataView(bytes.buffer);
        if (log) {
            if (bytes.length < 8 || bytes[0] !== 1 || bytes[1] > 16 || bytes.length !== 8 + bytes[1]*8) return null;
            return {age_seconds:age,events:Array.from({length:bytes[1]},(_,i)=>{const o=8+i*8;return {elapsed_seconds:view.getUint32(o,true),detection:bytes[o+4],event:bytes[o+5]};})};
        }
        if (bytes.length < 4 || bytes[0] !== 2 || bytes[3] > 16 || bytes.length !== 4 + bytes[3]*4) return null;
        const flags=bytes[1], jam=(flags>>1)&3, spoof=(flags>>4)&7, frequencies=[];
        if (jam > 2 || spoof > 3) return null;
        for(let o=4;o<bytes.length;o+=4){const value=view.getUint32(o,true),khz=value&0xffffff;
            if(!khz || frequencies.some(row=>row.khz===khz))return null;
            frequencies.push({khz,jammed:Boolean(value&0x1000000)});
        }
        return {age_seconds:age,jam_enabled:Boolean(flags&1),jam_state:jam,spoof_enabled:Boolean(flags&8),spoof_state:spoof,frequencies};
    } catch (_) { return null; }
}

function getReceiverDiagnostic(device, live = true, now = Date.now() / 1000) {
    const raw = device?.gnss_receiver;
    const received = Number(device?.server_received_at || device?.timestamp);
    const age = raw?.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0);
    if (!live || raw?.source !== 'ubx_mon_ver' || typeof raw.age_seconds !== 'number' || !Number.isFinite(age) || age < 0 || age > 45) return null;
    const result = {age_seconds:age, itfm_enabled:typeof raw.itfm_enabled === 'boolean' ? raw.itfm_enabled : null,
        error:typeof raw.error === 'string' ? raw.error.slice(0,160) : null,
        itfm_error:typeof raw.itfm_error === 'string' ? raw.itfm_error.slice(0,160) : null};
    try {
        if (typeof raw.payload_b64 !== 'string' || raw.payload_b64.length > 696) return result;
        const bytes = atob(raw.payload_b64);
        if (bytes.length < 40 || bytes.length > 520 || (bytes.length - 40) % 30) return {...result,error:'Phản hồi MON-VER không hợp lệ.'};
        const text = (start, length) => bytes.slice(start,start+length).split('\0')[0].trim();
        const extensions = [];
        for (let i=40; i<bytes.length; i+=30) extensions.push(text(i,30));
        const fields = [text(0,30),text(30,10),...extensions];
        if (!fields[0] || fields.some(value=>/[^\x20-\x7e]/.test(value))) return {...result,error:'Phản hồi MON-VER không hợp lệ.'};
        const extension = key => extensions.find(value=>value.startsWith(key+'='))?.slice(key.length+1) || null;
        return {...result,itfm_supported:!['HPG 1.50','HPG 1.51'].includes(extension('FWVER')),software:fields[0],hardware:fields[1],firmware:extension('FWVER'),protocol:extension('PROTVER'),model:extension('MOD'),extensions};
    } catch (_) { return {...result,error:'Phản hồi MON-VER không hợp lệ.'}; }
}

function getReceiverVersionRows(receiver) {
    if (!receiver?.software) return [];
    const extensions = receiver.extensions || [];
    const rom = extensions.find(value=>/^ROM BASE\s+/.test(value));
    return [
        {label:'Phần mềm lõi (swVersion)',value:receiver.software},
        {label:'Phần cứng (hwVersion)',value:receiver.hardware || 'Chip không trả trường này'},
        {label:'ROM BASE',value:rom ? rom.replace(/^ROM BASE\s+/, '') : 'Chip không trả trường này'},
        ...extensions.map((value,index)=>({label:`extension[${index}]`,value:value || '(trống)'}))
    ];
}

function getInterferenceConfigStatus(device, receiver) {
    if (receiver?.itfm_supported === false) return {desired:null,label:'Firmware này không hỗ trợ công tắc ITFM cũ.',tone:'text-muted'};
    const config = device?.base_config;
    if (!config?.base_setup_method) return {desired:null, label:'Chưa lưu cấu hình BASE', tone:'text-muted'};
    const desired = config.gnss_options?.interference_detection_enabled !== false;
    if (typeof receiver?.itfm_enabled !== 'boolean' || receiver.itfm_error)
        return {desired,label:receiver?.itfm_error ? 'Lỗi đọc trạng thái chip' : 'Chưa xác minh',tone:'text-warning'};
    return {desired, label:receiver.itfm_enabled === desired ? 'Khớp trạng thái đang chạy' : 'Không khớp cấu hình đã lưu',
        tone:receiver.itfm_enabled === desired ? 'text-success' : 'text-warning'};
}

function getUnicoreRfDiagnostic(device, live = true, now = Date.now() / 1000) {
    const snapshot = device?.gnss_rf;
    if (!live || snapshot?.source !== 'unicore_rf') return null;
    const received = Number(device.server_received_at || device.timestamp);
    const fresh = value => {
        const age = value?.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0);
        return typeof value?.age_seconds === 'number' && Number.isFinite(age) && age >= 0 && age <= 45
            ? {...value, age_seconds: age} : null;
    };
    let agc = fresh(snapshot.agc), jamming = fresh(snapshot.jamming);
    if (agc?.source !== 'unicore_agca' || !Array.isArray(agc.values) || agc.values.length !== 10
        || !agc.values.every(value => Number.isInteger(value) && value >= -1 && value <= 255)) agc = null;
    const count = jamming?.source === 'unicore_freqjamstatusa' ? 3 : jamming?.source === 'unicore_jamstatusa' ? 1 : 0;
    if (!count || !Array.isArray(jamming.ratios) || !Array.isArray(jamming.flags)
        || jamming.ratios.length !== count || jamming.flags.length !== count
        || !jamming.ratios.every(value => Number.isInteger(value) && value >= 0 && value <= 255)
        || !jamming.flags.every(value => Number.isInteger(value) && value >= 0 && value <= 2)) jamming = null;
    return agc || jamming ? {source:'unicore_rf', agc, jamming} : null;
}

function getRfReadError(device, rf, live = true, now = Date.now() / 1000) {
    if (!live || device?.gnss_rf?.source !== 'ubx_mon_rf') return null;
    const raw = device.gnss_rf, error = raw.last_error || (raw.state === 'read_error' ? raw : null);
    const received = Number(device.server_received_at || device.timestamp);
    const fresh = value => typeof value?.age_seconds === 'number' && Number.isFinite(value.age_seconds)
        && value.age_seconds >= 0 && value.age_seconds + (Number.isFinite(received) ? Math.max(0, now - received) : 0) <= 45;
    if (error && fresh(error)) return {error_kind: error.error_kind, error: String(error.error || 'Lỗi truy vấn RF').slice(0,160)};
    if (!rf && typeof raw.payload_b64 === 'string' && fresh(raw))
        return {error_kind:'invalid_response', error:'Có phản hồi RF nhưng giao diện không giải mã được.'};
    return null;
}

function getRfBlockLabel(block, blocks) {
    const unique = blocks.filter(other => other.id === block.id).length === 1;
    return unique && block.id === 0 ? 'L1' : unique && block.id === 1 ? 'L2/L5'
        : `RF ${block.index + 1} (ID ${block.id})`;
}

function getRfAssessment({live, rf, um, readError, receiver, security, unicore = false}) {
    const unknown = (reason, next) => ({tone:'warning',title:'Chưa đủ dữ liệu kết luận',reason,next});
    if (!live) return unknown('Trạm không có dữ liệu trực tiếp.', 'Kết nối lại trạm rồi kiểm tra mẫu RF mới.');
    if (!unicore && (security || receiver?.itfm_supported === false)) {
        if (!security || security.error) return unknown(security?.error ? 'Lần đọc SEC-SIG chưa thành công.' : 'Đang chờ trạng thái nhiễu và giả mạo tín hiệu từ chip.', 'Pi tự đọc lại SEC-SIG; chưa có phản hồi mới để kết luận.');
        if (security.spoof_enabled && security.spoof_state >= 2) return {tone:'danger',title:security.spoof_state === 3 ? 'Chip xác nhận giả mạo tín hiệu' : 'Chip nghi ngờ giả mạo tín hiệu',reason:'Cảnh báo từ SEC-SIG; xem nhật ký sự kiện và trạng thái nhiễu bên dưới.',next:'Đối chiếu nguồn tín hiệu và tọa độ trước khi dùng kết quả đo.'};
        if (security.jam_enabled && security.jam_state === 2) return {tone:'warning',title:'Chip báo nhiễu ảnh hưởng thu tín hiệu',reason:'SEC-SIG báo nhiễu ảnh hưởng khả năng bám tín hiệu. Các tần số bị ảnh hưởng được liệt kê bên dưới.',next:'Theo dõi các đợt tiếp theo và mức C/N₀. Cảnh báo không xác định thiết bị phát gây nhiễu.'};
        if (!security.jam_enabled || !security.spoof_enabled || security.jam_state === 0 || security.spoof_state === 0) return unknown('Chip chưa có kết luận đầy đủ hoặc một bộ phát hiện đang tắt.', 'Xem riêng trạng thái nhiễu và giả mạo bên dưới; Pi tiếp tục thu mẫu.');
        return {tone:'info',title:'Chip chưa báo nhiễu hoặc giả mạo',reason:'SEC-SIG hiện không báo bất thường trong hai bộ phát hiện.',next:'Xem lịch sử để kiểm tra những thời điểm khác; trạng thái này không xác minh độ đúng tọa độ Base.'};
    }
    if (readError) return unknown(readError.error_kind === 'timeout'
        ? 'Agent đã hỏi MON-RF nhưng không nhận được phản hồi đúng hạn.'
        : readError.error_kind === 'invalid_response' ? 'Có phản hồi nhưng bộ đọc từ chối dữ liệu RF.'
        : 'Lần đọc MON-RF gần nhất bị lỗi.', 'Xem chi tiết lỗi đọc; chưa dùng riêng hình phổ để kết luận trạm sạch nhiễu.');
    if (unicore || um) {
        if (!um?.jamming) return unknown(um?.agc ? 'UM mới có AGC; chưa có chỉ báo nhiễu CW.' : 'Chưa nhận được chỉ báo nhiễu CW từ UM.', 'AGC đơn lẻ không đủ để phân loại nhiễu. Cần phản hồi JAMSTATUS hoặc FREQJAMSTATUS.');
        const severity = Math.max(...um.jamming.flags);
        return {tone: severity === 2 ? 'danger' : severity === 1 ? 'warning' : 'info',
            title: severity === 2 ? 'Chip báo nhiễu CW mạnh' : severity === 1 ? 'Chip báo có nhiễu CW' : 'Chưa ghi nhận nhiễu CW',
            reason:'Kết quả từ cờ cảnh báo CW của UM, không suy ra từ AGC hay hình phổ.',
            next: severity ? 'Giữ mẫu hiện tại, thử tách nguồn phát của mình khỏi anten rồi so sánh lại.'
                : 'Kết quả chỉ áp dụng cho nhiễu CW lúc đọc; nếu đo vẫn lệch, tiếp tục kiểm tra tọa độ, hệ quy chiếu và chiều cao anten.'};
    }
    if (!rf) return unknown('Chưa có MON-RF hợp lệ. Có phổ MON-SPAN cũng chưa thay thế được trạng thái cảnh báo.',
        'Kiểm tra phản hồi MON-RF; giữ mẫu phổ để so sánh trước/sau khi đổi vị trí anten hoặc tắt thiết bị phát của mình.');
    const band = block => getRfBlockLabel(block, rf.blocks);
    const fault = rf.blocks.filter(b => b.antenna === 3 || b.antenna === 4);
    if (fault.length) return {tone:'danger',title:'Chip báo lỗi đường anten',
        reason:`${fault.map(band).join(', ')}: có chỉ báo hở mạch hoặc ngắn mạch anten.`,
        next:'Kiểm tra cáp, đầu nối và nguồn anten.'};
    const severity = Math.max(...rf.blocks.map(b => b.jamming));
    if (severity >= 2) return {tone:severity === 3 ? 'danger' : 'warning',
        title:severity === 3 ? 'Chip báo nhiễu nghiêm trọng' : 'Chip báo có nhiễu',
        reason:`MON-RF báo nhiễu ở ${rf.blocks.filter(b=>b.jamming>=2).map(band).join(', ')}.`,
        next:'Giữ mẫu hiện tại, thử tách nguồn phát của mình khỏi anten rồi so sánh lại.'};
    if (rf.blocks.some(b=>b.agc_valid === false)) return unknown('Chip có trả RF nhưng có AGC ngoài dải mô tả.', 'Xem giá trị AGC gốc trong chi tiết; chưa dùng giá trị ngoài dải để đánh giá mức nhiễu.');
    if (rf.blocks.some(b=>b.jamming === 0)) {
        if (receiver?.itfm_enabled === false) return unknown('Chip xác nhận bộ phát hiện nhiễu đang tắt (CFG-ITFM-ENABLE = 0).',
            'Bật Phát hiện nhiễu GNSS trong cấu hình BASE rồi bấm Áp dụng cấu hình BASE.');
        if (receiver?.itfm_enabled === true) return unknown('Bộ phát hiện nhiễu đang bật, nhưng MON-RF vẫn trả trạng thái chưa xác định.',
            'Đối chiếu firmware chip và giữ mẫu RF; không coi cờ 0 là trạm sạch nhiễu.');
        return unknown('MON-RF trả trạng thái chưa xác định; chưa đọc được cấu hình bộ phát hiện nhiễu.',
            'Cần phản hồi MON-VER và cấu hình ITFM để phân biệt phiên bản và trạng thái bật/tắt.');
    }
    return {tone:'info',title:'Chưa ghi nhận nhiễu đáng kể',
        reason:`MON-RF của ${rf.blocks.map(band).join(', ')} đều báo không có nhiễu đáng kể ở lần đọc này.`,
        next:'Nếu đo vẫn lệch, tiếp tục kiểm tra tọa độ, hệ quy chiếu và chiều cao anten; kết quả RF không chứng minh tọa độ Base đúng.'};
}

function getLeaflet() {
    const leaflet = window.Leaflet || window.L;
    return leaflet && typeof leaflet.map === 'function' ? leaflet : null;
}
// === MAP COMPONENT ===
// ==================== MAP COMPONENT (FIXED HTTPS) ====================
const BaseLocationMap = memo(function BaseLocationMap({ coords, rtcmOnly = false }) {
    const mapContainerRef = useRef(null);
    const mapInstanceRef = useRef(null);
    const markerRef = useRef(null);

    useEffect(() => {
        // Kiểm tra tọa độ đầu vào
        if (!coords || !coords.lat || !coords.lon) {
            console.warn('[Map] No coords provided');
            return;
        }
        const lat = parseFloat(coords.lat);
        const lon = parseFloat(coords.lon);

        if (isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) return;

        // Kiểm tra Leaflet đã load chưa
        const leaflet = getLeaflet();
        if (!leaflet) {
            console.error('Leaflet chưa được load!');
            return;
        }

        // Khởi tạo Map
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
            if (mapInstanceRef.current) {
                mapInstanceRef.current.invalidateSize();
            }
        }, 300);

    }, [coords?.lat, coords?.lon]);

    // Handle Resize
    useEffect(() => {
        const handleResize = () => {
            if (mapInstanceRef.current) mapInstanceRef.current.invalidateSize();
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Hiển thị placeholder nếu chưa có tọa độ
    if (!coords || !coords.lat || (parseFloat(coords.lat) === 0 && parseFloat(coords.lon) === 0)) {
        console.warn('[Map] No valid coords - showing placeholder');
        return (
            <div className="d-flex align-items-center justify-content-center p-4 text-danger border rounded bg-dark"
                style={{ height: '250px', border: '2px solid red' }}>
                <div className="text-center">
                    <i className="bi bi-geo-alt-slash display-4 mb-2"></i>
                    <p className="mb-0">❌ Chưa có tọa độ Base</p>
                    {rtcmOnly ? <small>Chờ bản tin RTCM 1005/1006 từ trạm.</small> : <small>Debug: lat={coords?.lat || 'null'}, lon={coords?.lon || 'null'}</small>}
                </div>
            </div>
        );
    }

    if (!getLeaflet()) {
        console.error('[Map] ❌ LEAFLET CHƯA ĐƯỢC LOAD!');
        return (
            <div className="alert alert-danger" style={{ height: '250px' }}>
                <h5>⚠️ Lỗi: Leaflet chưa được tải</h5>
                <p>Kiểm tra file HTML có thêm Leaflet CSS/JS chưa.</p>
            </div>
        );
    }
    return (
        <div>
            <div className="d-flex justify-content-between align-items-center mb-2">
                <div className="d-flex align-items-center">
                    <h6 className="mb-0 me-3">{rtcmOnly ? 'Vị trí từ RTCM' : 'Vị trí trạm Base (Lưu cứng)'}</h6>
                </div>
                <a href={`https://www.google.com/maps?q=${coords.lat},${coords.lon}`} target="_blank" rel="noopener noreferrer" className="btn btn-sm btn-outline-primary">
                    <i className="bi bi-box-arrow-up-right me-1"></i>Google Maps
                </a>
            </div>
            <div style={{ position: 'relative' }}>
                <div ref={mapContainerRef} style={{ width: '100%', height: '300px', borderRadius: '8px', border: '2px solid #444', background: '#222', zIndex: 0 }} />
            </div>
            <div className="mt-2 small text-muted">Lat: {parseFloat(coords.lat).toFixed(8)} | Lon: {parseFloat(coords.lon).toFixed(8)}</div>
        </div>
    );
});

// ===============================================================================
// === DASHBOARD COMPONENT ===
// ===============================================================================

const flags = { us: new Image(), ru: new Image(), eu: new Image(), cn: new Image(), jp: new Image(), un: new Image() };
flags.us.src = '/assets/img/flags/us.jpg';
flags.ru.src = '/assets/img/flags/ru.jpg';
flags.eu.src = '/assets/img/flags/eu.jpg';
flags.cn.src = '/assets/img/flags/cn.jpg';
flags.jp.src = '/assets/img/flags/jp.jpg';
flags.un.src = '/assets/img/flags/un.jpg';

const colors = { us: '#00d47e', ru: '#ff5c5c', eu: '#5c9cff', cn: '#ffb95c', jp: '#ff77a9', un: '#aaaaaa' };
const constellationNames = { us: "GPS (USA)", ru: "GLONASS (Russia)", eu: "Galileo (EU)", cn: "BeiDou (China)", jp: "QZSS (Japan)", sb: "SBAS", im: "IMES", in: "NavIC", un: "Unknown" };

function getSatelliteSystem(sat) {
    if (!sat) return 'un';
    const sys = String(sat.system || '').toLowerCase();
    if (['us', 'ru', 'eu', 'cn', 'jp', 'sb', 'im', 'in'].includes(sys)) return sys;
    
    const mapping = {
        g: 'us', gps: 'us',
        r: 'ru', glonass: 'ru',
        e: 'eu', galileo: 'eu',
        c: 'cn', beidou: 'cn', compass: 'cn',
        j: 'jp', qzss: 'jp'
    };
    if (mapping[sys]) return mapping[sys];

    // Fallback: try extracting system from string PRN (e.g. "G01", "R07")
    const prnStr = String(sat.prn || '');
    if (prnStr.length >= 2) {
        const firstChar = prnStr[0].toLowerCase();
        if (mapping[firstChar]) return mapping[firstChar];
    }

    const prn = Number(sat.prn);
    if (prn >= 1 && prn <= 32) return 'us';
    if (prn >= 65 && prn <= 96) return 'ru';
    if (prn >= 301 && prn <= 336) return 'eu';
    if (prn >= 193 && prn <= 202) return 'jp';
    if ((prn >= 33 && prn <= 64) || (prn >= 401 && prn <= 463)) return 'cn';
    return 'un';
}

// Chuẩn hóa PRN về dải RTCM (1-based) để NMEA và RTCM cùng vệ tinh dùng chung key.
// GLONASS: NMEA dùng 65-96 → chuẩn hóa về 1-32 (RTCM)
// Galileo: NMEA dùng 301-336 → chuẩn hóa về 1-36 (RTCM)
// BeiDou: NMEA dùng 401-463 → chuẩn hóa về 1-63 (RTCM)
// QZSS: NMEA dùng 193-200 → chuẩn hóa về 1-8 (RTCM)
function getNormalizedPrn(sat) {
    const prn = Number(String(sat.prn).replace(/^[GRECJ]/i, ''));
    const sys = getSatelliteSystem(sat);
    if (sys === 'ru' && prn >= 65 && prn <= 96) return prn - 64;  // GLONASS NMEA→RTCM
    if (sys === 'eu' && prn >= 301 && prn <= 336) return prn - 300; // Galileo NMEA→RTCM
    if (sys === 'cn' && prn >= 401 && prn <= 463) return prn - 400; // BeiDou NMEA→RTCM
    if (sys === 'jp' && prn >= 193 && prn <= 202) return prn - 192; // QZSS NMEA→RTCM
    return prn; // GPS or already RTCM-space
}

function getSatelliteKey(sat) {
    return `${getSatelliteSystem(sat)}:${getNormalizedPrn(sat)}`;
}

function hasSatellitePosition(sat) {
    if (!sat || sat.has_geometry === false || sat.azimuth === null || sat.elevation === null) return false;
    const az = Number(sat.azimuth), el = Number(sat.elevation);
    return Number.isFinite(az) && Number.isFinite(el) && az >= 0 && az <= 360 && el >= 0 && el <= 90
        && (az !== 0 || el !== 0 || sat.has_geometry === true);
}

function getVisibleSatellites(satellites, maxAgeMs) {
    const now = Date.now();
    const visibleByKey = new Map();

    (satellites || []).forEach(sat => {
        const snr = Number(sat?.snr);
        if (!sat || !Number.isFinite(snr) || snr <= 0) return;

        let lastSeen = Number(sat.lastSeen || sat.last_seen || 0);
        if (lastSeen > 0 && lastSeen < 1e11) lastSeen *= 1000;
        if (lastSeen > 0 && now - lastSeen > maxAgeMs) return; // Uncommented to clear stale satellites

        if (!Number.isInteger(getNormalizedPrn(sat))) return;
        const key = getSatelliteKey(sat);
        const existing = visibleByKey.get(key);
        const best = !existing || snr > Number(existing.snr) ? sat : existing;
        const geometry = hasSatellitePosition(sat) ? sat : hasSatellitePosition(existing) ? existing : null;
        visibleByKey.set(key, geometry
            ? {...best, azimuth: geometry.azimuth, elevation: geometry.elevation, has_geometry: true} : best);
    });

    return Array.from(visibleByKey.values());
}

function getDecodedSatelliteCounts(satellites, maxAgeMs) {
    const bySource = {rtcm: [], nmea: []};
    (satellites || []).forEach(sat => {
        const source = getSatelliteTelemetrySource(sat);
        if (bySource[source]) bySource[source].push(sat);
    });
    return {rtcm: getVisibleSatellites(bySource.rtcm, maxAgeMs).length,
        nmea: getVisibleSatellites(bySource.nmea, maxAgeMs).length};
}

// ===============================================================================
// === ENHANCED DASHBOARD COMPONENT (REPLACED)
// ===============================================================================

function isDeviceStaleOffline(device, nowMs = Date.now()) {
    return window.CorsDashboardShared.isDeviceStaleOffline(device, nowMs);
}

function RfMonitorOverview({model}) {
    const {report,elapsed,age,samples} = model;
    const duration = seconds => `${Math.floor(seconds / 60).toString().padStart(2,'0')}:${Math.floor(seconds % 60).toString().padStart(2,'0')}`;
    const levels = ['Không cảnh báo','Cảnh báo','Mức mạnh'];
    const colors = ['var(--bs-info)','var(--bs-warning)','var(--bs-danger)'];
    const history = getRfMonitorHistory(report);
    const blocks = Array.isArray(report?.block_ranges) ? report.block_ranges.filter(row => Array.isArray(row) && row.length === 11
        && row.every(value => Number.isInteger(value) && value >= 0 && value <= 65535)
        && row[0] >= 1 && row[0] <= 4 && row[2] <= 120 && row[3] + row[4] <= row[2]).slice(0,4) : [];
    const hour = timestamp => new Date(timestamp*1000).toLocaleString('vi-VN',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
    const clock = offset => new Date((Date.now()/1000 - age - offset)*1000).toLocaleTimeString('vi-VN',{hour12:false});
    return <div className="border rounded-3 p-3 mb-3" aria-label="Cửa sổ theo dõi RF">
        <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
            <div><h6 className="mb-1">Theo dõi RF</h6><span className="small text-muted">{model.fixed ? 'Tự động từng đợt 10 phút' : '10 phút gần nhất'} · Tại Pi{report?.source === 'ubx_sec_sig' ? ' · SEC-SIG' : ''}</span></div>
            <span className={`badge text-bg-${model.tone}`} style={{whiteSpace:'normal',textAlign:'left'}} role="status">{model.title}</span>
        </div>
        <div className="d-flex flex-wrap justify-content-between gap-2 small mb-2">
            <span>{model.fixed ? `Đợt ${hour(report.window_started_at)} – ${hour(report.window_ends_at)}` : elapsed < 600 ? 'Đang mở rộng cửa sổ' : 'Cửa sổ cuốn liên tục'}</span>
            <span className="font-monospace">{duration(elapsed)} / 10:00</span>
        </div>
        <div className="progress mb-2" style={{height:6}} role="progressbar" aria-label="Độ dài cửa sổ đã theo dõi" aria-valuenow={Math.floor(elapsed)} aria-valuemin={0} aria-valuemax={600} aria-valuetext={`${duration(elapsed)} trên 10 phút`}>
            <div className="progress-bar bg-info" style={{width:`${elapsed/6}%`,transition:'none'}} />
        </div>
        <p className="small text-muted mb-3">{age === null ? 'Chưa nhận mẫu' : `Mẫu mới nhất: ${Math.floor(age)} giây trước`}</p>
        <div className="row g-2 mb-3">
            {[[report?.sample_count,'Mẫu đã thu'],[report?.warning_samples,'Mẫu cảnh báo'],[report?.episodes,'Đợt cảnh báo'],[report?.quality_comparisons ? report.coincident_cno_drops : null,'Kèm C/N₀ giảm']].map(([value,label]) => <div className="col-6 col-lg-3" key={label}>
                <div className="rounded border p-2 h-100"><strong className="fs-5 d-block">{value ?? '—'}</strong><span className="small text-muted">{label}</span></div>
            </div>)}
        </div>
        {model.fixed && <p className="small text-muted mb-3">{elapsed >= 600 ? 'Đang chờ mẫu đầu tiên của đợt mới.' : `Còn ${duration(600-elapsed)} đến đợt tiếp theo.`} Kết quả được tính vào lịch sử 24 giờ.</p>}
        {report?.source === 'ubx_sec_sig' && <p className="small mb-3">Nhiễu: {report.jamming_samples ?? 0} mẫu · Giả mạo tín hiệu: {report.spoofing_samples ?? 0} mẫu</p>}
        {report?.sample_count >= 3 && <div className="small border-start border-3 ps-3 mb-3" role="status" aria-atomic="true">
            <strong className="d-block">{report.warning_samples === report.sample_count ? 'Cảnh báo ở mọi lần đọc; chưa có mẫu không cảnh báo để so sánh.'
                : report.warning_samples ? 'Cảnh báo xuất hiện ở một phần các mẫu.' : 'Các mẫu đã thu không có cảnh báo từ chip.'}</strong>
            <span className="text-muted">{!report.quality_comparisons ? 'Chưa đủ dữ liệu đối chiếu C/N₀.'
                : report.coincident_cno_drops ? `${report.coincident_cno_drops} mẫu cảnh báo đi kèm C/N₀ giảm so với lần đọc trước.`
                : `Chưa thấy mức giảm C/N₀ đạt điều kiện trong ${report.quality_comparisons} cặp mẫu. Tín hiệu yếu sẵn từ đầu vẫn có thể không tạo ra mức giảm.`} Chưa xác định nguồn phát hoặc sai số tọa độ.</span>
        </div>}
        {blocks.length > 0 && <details className="mb-3"><summary className="small py-2" style={{minHeight:44}}>Biến động từng khối RF trong 10 phút</summary>
            <div className="table-responsive" tabIndex={0} role="region" aria-label="Biến động từng khối RF"><table className="table table-sm small" style={{minWidth:520}}>
                <thead><tr><th>Khối</th><th>Cảnh báo / mẫu</th><th>Mức mạnh</th><th>Nhiễu nền</th><th>AGC</th><th>jamInd</th></tr></thead>
                <tbody>{blocks.map(row=><tr key={row[0]}><td className="text-nowrap">RF {row[0]} (ID {row[1]})</td><td>{row[3]+row[4]} / {row[2]}</td><td>{row[4]}</td><td>{row[5]}–{row[6]}</td><td>{row[7]}–{row[8]}</td><td>{row[9]}–{row[10]}</td></tr>)}</tbody>
            </table></div>
            <p className="small text-muted mb-0">Thấp nhất–cao nhất trong các lần đọc. Giá trị thô, không phải dBm hay ngưỡng kết luận nguồn nhiễu. Khối giữ theo thứ tự chip trả về.</p>
        </details>}
        {samples.length ? <>
            <div className="d-flex flex-wrap gap-3 small mb-2"><span>● Không cảnh báo</span><span>▲ Cảnh báo</span><span>◆ Mức mạnh</span></div>
            <svg viewBox="0 0 600 105" role="img" aria-label={`${samples.length} mẫu RF trong 10 phút; các khoảng trống không có mẫu`} style={{width:'100%',height:'auto',minHeight:100}}>
                {[2,1,0].map(level => <line key={level} x1="10" x2="590" y1={20+(2-level)*30} y2={20+(2-level)*30} stroke="currentColor" opacity="0.12" />)}
                {samples.map(([offset,level,delta],index) => {
                    const x=10+(model.fixed ? (report.last_sample_at-offset-report.window_started_at)/600 : 1-(age+offset)/600)*580, y=20+(2-level)*30;
                    return <g key={index} fill={colors[level]}><title>{clock(offset)} · {levels[level]}{delta === null ? '' : ` · ΔC/N₀ ${delta} dB-Hz`}</title>
                        {level === 0 ? <circle cx={x} cy={y} r="4" /> : <path d={level === 1 ? `M${x} ${y-5}l5 10h-10z` : `M${x} ${y-6}l6 6l-6 6l-6-6z`} />}
                        {delta !== null && delta <= -3 && <circle cx={x} cy={y} r="8" fill="none" stroke="currentColor" />}
                    </g>;
                })}
            </svg>
            <div className="d-flex justify-content-between small text-muted mb-2"><span>{model.fixed ? 'Bắt đầu' : '−10 phút'}</span><span>{model.fixed ? '+5 phút' : '−5 phút'}</span><span>{model.fixed ? '+10 phút' : 'Hiện tại'}</span></div>
            <p className="small text-muted mb-2">Mỗi dấu là một lần đọc. Khoảng trống: không có mẫu. Vòng ngoài: C/N₀ giảm.</p>
            <details><summary className="small py-2" style={{minHeight:44}}>Xem mẫu và cách tính</summary>
                <div className="table-responsive" tabIndex={0} role="region" aria-label="Biến động C/N₀ từng mẫu; cuộn ngang để xem đủ"><table className="table table-sm small" style={{minWidth:600}}><caption className="visually-hidden">Các mẫu RF đã thu trong cửa sổ</caption><thead><tr><th>Thời điểm</th><th>RF</th><th>ΔC/N₀ trung vị</th><th>Thấp / cao nhất</th><th>Vệ tinh thay đổi</th></tr></thead>
                    <tbody>{samples.map(([offset,level,delta,matched,changed,minimum,maximum],index)=><tr key={index}><td className="font-monospace text-nowrap">{clock(offset)}</td><td>{levels[level]}</td><td>{delta === null ? '—' : `${delta > 0 ? '+' : ''}${delta} dB-Hz`}</td><td className="text-nowrap">{minimum == null ? '—' : `${minimum} / ${maximum} dB-Hz`}</td><td>{delta === null || matched == null ? '—' : `${changed} / ${matched}`}</td></tr>)}</tbody></table></div>
                <p className="small text-muted d-sm-none mb-1">Vuốt ngang để xem đủ các cột.</p>
                <p className="small text-muted mb-0">C/N₀ giảm: trung vị giảm từ 3 dB-Hz trên ít nhất 4 vệ tinh trùng nhau giữa hai lần đọc. {report?.quality_comparisons || 0} cặp mẫu đủ dữ liệu đối chiếu.</p>
                <p className="small text-muted mb-0">Trung vị bằng 0 vẫn có thể có vệ tinh tăng hoặc giảm. Cùng epoch NAV-SAT hoặc thiếu dữ liệu: không tính Δ.</p>
            </details>
        </> : <p className="small text-muted mb-0">Chưa có lịch sử từng mẫu. Số đếm phía trên vẫn cập nhật khi nhận báo cáo.</p>}
        <details className="mt-3 border-top pt-2"><summary className="py-2" style={{minHeight:44}}>Lịch sử 24 giờ</summary>
            {report?.history_error && <p className="small text-warning" role="status">Không lưu được lịch sử tại Pi. Các mẫu trực tiếp vẫn được hiển thị.</p>}
            {history.length ? <>
                <p className="small text-muted">Lưu tại Pi, giữ qua lần khởi động lại. Báo cáo mới nhất: {hour(report.history_24h.last_sample_at)}.</p>
                <p className="small">{history.reduce((sum,row)=>sum+row[1],0)} mẫu đã lưu · {history.reduce((sum,row)=>sum+row[2],0)} mẫu cảnh báo</p>
                <div className="table-responsive" tabIndex={0} role="region" aria-label="Lịch sử RF theo giờ; cuộn ngang để xem đủ"><table className="table table-sm small" style={{minWidth:480}}><caption className="visually-hidden">Tổng hợp RF theo giờ trong 24 giờ gần nhất</caption>
                    <thead><tr><th>Giờ bắt đầu</th><th>Mẫu</th><th>Cảnh báo</th><th>Mức mạnh</th><th>Kèm C/N₀ giảm</th></tr></thead>
                    <tbody>{history.map(row=><tr key={row[0]}><td className="text-nowrap">{hour(row[0])}</td><td>{row[1]}</td><td>{row[2]} / {row[1]}<div className="progress mt-1" style={{height:4}}><div className="progress-bar bg-warning" style={{width:`${row[1] ? row[2]/row[1]*100 : 0}%`}} /></div></td><td>{row[3]}</td><td>{row[5] ? row[4] : '—'}</td></tr>)}</tbody>
                </table></div>
                <p className="small text-muted mb-0">Đếm theo mẫu đã thu, không phải thời lượng nhiễu liên tục. Không có mẫu thì không suy ra trạng thái RF.</p>
            </> : <p className="small text-muted mb-0">Chưa có lịch sử đã lưu.</p>}
        </details>
    </div>;
}

function RfDiagnostics({device, live}) {
    const esp32 = device.system_info?.gnss_rf_supported === false || /^esp_/i.test(device.serial || '');
    const [, refreshClock] = useState(0);
    useEffect(() => {
        if (esp32) return;
        const timer = setInterval(() => refreshClock(value => value + 1), 1000);
        return () => clearInterval(timer);
    }, [device.serial, esp32]);
    const automatic = getRfMonitorWindow(device, live);
    const unicore = device.detected_chip_type === 'Unicorecomm';
    const um = unicore ? getUnicoreRfDiagnostic(device, live) : null;
    const rf = unicore ? null : getRfDiagnostic(device, 'gnss_rf', live);
    const spectrum = unicore ? null : getRfDiagnostic(device, 'gnss_spectrum', live);
    const hasData = Boolean(rf || spectrum || um || device.gnss_security || device.gnss_security_log);
    const readError = unicore ? null : getRfReadError(device, rf, live);
    const receiver = unicore ? null : getReceiverDiagnostic(device, live);
    const security = unicore ? null : getSecurityDiagnostic(device, 'gnss_security', live);
    const securityLog = unicore ? null : getSecurityDiagnostic(device, 'gnss_security_log', live);
    const interferenceConfig = getInterferenceConfigStatus(device, receiver);
    const assessment = getRfAssessment({live,rf,um,readError,receiver,security,unicore});
    const [reference, setReference] = useState(null);
    const serial = device.serial;
    const stateNames = ['Chưa xác định', 'Không báo nhiễu đáng kể', 'Cảnh báo nhiễu', 'Nhiễu nghiêm trọng'];
    const exportReading = () => {
        const data = {serial, captured_at: new Date().toISOString(), received_at: device.server_received_at || device.timestamp, security, security_log:securityLog, raw_security:device.gnss_security, raw_security_log:device.gnss_security_log, rf, spectrum, receiver, interference_config:interferenceConfig, raw_receiver:device.gnss_receiver || null, unicore: um, reference, assessment, automatic:device.gnss_rf_monitor || null, read_error:readError, raw_rf:device.gnss_rf || null, raw_spectrum:device.gnss_spectrum || null};
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:'application/json'}));
        const link = document.createElement('a'); link.href = url; link.download = 'gnss-rf.json'; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    if (esp32) return unicore ? null : <section className="card mt-3" aria-label="Thông tin chip GNSS">
        <div className="card-body">
            <h6>Thông tin MON-VER đầy đủ</h6>
            {receiver?.software ? <dl className="row g-2 mb-0" aria-label="MON-VER đầy đủ">
                {getReceiverVersionRows(receiver).map((row,index) => <React.Fragment key={index}>
                    <dt className="col-sm-4 fw-normal text-muted" style={{overflowWrap:'anywhere'}}>{row.label}</dt>
                    <dd className="col-sm-8 font-monospace mb-0" data-no-translate style={{overflowWrap:'anywhere',whiteSpace:'pre-wrap'}}>{row.value}</dd>
                </React.Fragment>)}
            </dl> : <p className="text-muted mb-0" role="status">Chưa nhận được MON-VER mới từ chip.</p>}
        </div>
    </section>;
    return <section className="card mt-3" aria-label="Chẩn đoán nhiễu GNSS">
        <div className="card-body">
            <RfMonitorOverview model={automatic} />
            <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
                <h6 className="mb-0">Chẩn đoán nhiễu GNSS</h6>
                <div className="d-flex flex-wrap gap-2">
                    <button className="btn btn-sm btn-outline-primary" disabled={!hasData}
                        onClick={() => setReference({captured_at:new Date().toISOString(), rf, spectrum, unicore:um})}>Giữ mẫu hiện tại</button>
                    <button className="btn btn-sm btn-outline-secondary" disabled={!hasData && !reference && !readError && !receiver}
                        onClick={exportReading}>Tải số liệu</button>
                </div>
            </div>
            {!unicore && <div className="small border rounded p-2 mb-2" aria-label="Thông tin chip GNSS">
                <div data-no-translate style={{overflowWrap:'anywhere'}}><strong>{receiver?.model || 'u-blox'}</strong> · Firmware: {receiver?.firmware || receiver?.software || 'Chưa đọc được'} · Protocol: {receiver?.protocol || 'Chưa đọc được'}</div>
                <h6 className="mt-3 mb-2">Thông tin MON-VER đầy đủ</h6>
                {receiver?.software ? <dl className="row g-2 mb-3" aria-label="MON-VER đầy đủ">
                    {getReceiverVersionRows(receiver).map((row,index)=><React.Fragment key={index}>
                        <dt className="col-sm-4 fw-normal text-muted" style={{overflowWrap:'anywhere'}}>{row.label}</dt>
                        <dd className="col-sm-8 font-monospace mb-0" data-no-translate style={{overflowWrap:'anywhere',whiteSpace:'pre-wrap'}}>{row.value}</dd>
                    </React.Fragment>)}
                </dl> : <p className="text-muted mb-2" role="status">Chưa nhận được MON-VER mới. Các trường chỉ hiện khi chip trả lời.</p>}
                {receiver?.itfm_supported === false ? <div className="small text-muted">Theo dõi nhiễu và giả mạo bằng SEC-SIG · Tự động tại Pi.</div> : <>
                    <div>Cấu hình đã lưu: <strong>{interferenceConfig.desired === null ? 'Chưa có' : interferenceConfig.desired ? 'Bật' : 'Tắt'}</strong></div>
                    <div>Chip đọc lại: <strong>{receiver?.itfm_enabled === true ? 'Đang bật' : receiver?.itfm_enabled === false ? 'Đang tắt' : 'Chưa đọc được'}</strong> · {receiver ? `Đọc ${Math.floor(receiver.age_seconds)} giây trước` : 'Chờ phản hồi từ chip'}</div>
                    <div className={interferenceConfig.tone} role="status">{interferenceConfig.label}</div>
                </>}
                {receiver?.firmware && /^HPG 1\.(?:0\d|1\d|2\d)(?:$|\s)/.test(receiver.firmware) && <div>Firmware trước HPG 1.30 có lỗi trùng ID khối RF đã được u-blox ghi nhận.</div>}
                {(receiver?.error || receiver?.itfm_error) && <details><summary>Chi tiết đọc thông tin chip</summary><p className="mb-0" data-no-translate style={{overflowWrap:'anywhere'}}>{[receiver.error,receiver.itfm_error].filter(Boolean).join(' · ')}</p></details>}
            </div>}
            <div className={`alert alert-${assessment.tone} mb-2`} role="status">
                <strong className="d-block mb-1">{assessment.title}</strong>
                <p className="mb-1">{assessment.reason}</p>
                <p className="mb-0"><strong>Bước tiếp theo: </strong>{assessment.next}</p>
            </div>
            {(security || receiver?.itfm_supported === false) && <div className="border rounded p-3 mb-3" aria-label="An toàn tín hiệu SEC-SIG">
                <h6 className="mb-2">Nhiễu và giả mạo tín hiệu · SEC-SIG</h6>
                {security && !security.error ? <>
                    <div className="row g-2 mb-2">
                        <div className="col-sm-6"><span className="text-muted">Phát hiện nhiễu: </span><strong>{!security.jam_enabled ? 'Đang tắt' : ['Chưa xác định','Không báo nhiễu','Có nhiễu ảnh hưởng thu tín hiệu'][security.jam_state]}</strong></div>
                        <div className="col-sm-6"><span className="text-muted">Phát hiện giả mạo: </span><strong>{!security.spoof_enabled ? 'Đang tắt' : ['Chưa xác định','Không báo giả mạo','Nghi ngờ giả mạo','Xác nhận giả mạo'][security.spoof_state]}</strong></div>
                    </div>
                    <p className="small text-muted mb-2">Đọc {Math.floor(security.age_seconds)} giây trước. Cờ cảnh báo do chip trả về.</p>
                    <div className="d-flex flex-wrap gap-2">{security.frequencies.map(row=><span key={row.khz} className={`badge ${row.jammed ? 'bg-warning text-dark' : 'bg-secondary'}`} style={{whiteSpace:'normal'}}>{(row.khz/1000).toFixed(3)} MHz · {row.jammed ? 'Bị nhiễu' : 'Không báo nhiễu'}</span>)}</div>
                    {!security.frequencies.length && <p className="small text-muted mb-0">Chip chưa trả tần số đủ dữ liệu đánh giá.</p>}
                </> : <p className="small mb-0" role="status">{security?.error ? <>Chưa đọc được SEC-SIG: <span data-no-translate>{security.error}</span></> : 'Đang chờ phản hồi SEC-SIG mới từ Pi.'}</p>}
                <details className="mt-2"><summary className="py-2" style={{minHeight:44}}>Nhật ký sự kiện trên chip · SEC-SIGLOG</summary>
                    {securityLog?.events ? securityLog.events.length ? <ul className="small mb-1 ps-3">{securityLog.events.map((event,index)=><li key={index}>
                        {event.elapsed_seconds === 0xffffffff ? 'Hơn 45 ngày trước' : `${Math.floor(event.elapsed_seconds + securityLog.age_seconds)} giây trước`} · {['Tín hiệu mô phỏng','Tín hiệu bất thường','Lệch INS/GNSS','Tín hiệu GNSS đổi đột ngột','Nhiễu','Xác thực thất bại','Tín hiệu phát lại'][event.detection] || `Loại ${event.detection}`} · {['Bắt đầu','Kết thúc','Kích hoạt','Hết thời gian'][event.event] || `Trạng thái ${event.event}`}
                    </li>)}</ul> : <p className="small mb-1">Chip chưa ghi sự kiện.</p> : <p className="small mb-1">{securityLog?.error ? <>Chưa đọc được nhật ký: <span data-no-translate>{securityLog.error}</span></> : 'Đang chờ nhật ký mới.'}</p>}
                    <p className="small text-muted mb-0">Tối đa 16 sự kiện gần nhất; nhật ký chip xóa khi khởi động lại. Lịch sử thu mẫu tại Pi được giữ riêng trong 24 giờ.</p>
                </details>
            </div>}
            <p className="small text-muted mb-2">Giữ mẫu trước khi đổi vị trí anten hoặc tắt nguồn phát của mình, rồi so sánh. Mốc được chọn chưa chắc sạch nhiễu.</p>
            {readError && <details className="mb-2"><summary>Chi tiết lỗi đọc MON-RF</summary><p className="small mb-0" data-no-translate>{readError.error}</p></details>}
            {!unicore && <p className="small text-muted mb-2">Trạng thái RF: {rf ? `${Math.floor(rf.age_seconds)} giây trước` : 'Chưa có'} · Phổ: {spectrum ? `${Math.floor(spectrum.age_seconds)} giây trước` : 'Chưa có'}</p>}
            {rf && <p className="small text-muted mb-2">RF1 và RF2 là các khối xử lý tín hiệu vô tuyến bên trong chip, không phải tên cổng USB/UART hay số anten. Khi ID hợp lệ: 0 là L1, 1 là L2/L5 tùy model. Nếu trùng ID, số RF chỉ thứ tự khối trả về, chưa xác định được băng của từng khối.</p>}
            <details className="mt-2 rf-details"><summary className="mb-2">Xem chỉ số và phổ chi tiết</summary>
            {!hasData && <p className="text-muted mb-2" role="status">Chưa có dữ liệu RF mới. Cần agent hỗ trợ và chip trả lời; dữ liệu quá 45 giây không được dùng để kết luận.</p>}
            {unicore && <div className="d-grid gap-2 mb-2">
                {um?.agc ? <div className="rf-band border rounded p-3">
                    <h6>AGCA · {Math.floor(um.agc.age_seconds)} giây trước</h6>
                    <dl className="mb-0" style={{display:'grid',gridTemplateColumns:'minmax(0, 1fr) auto',gap:'4px 12px'}}>
                        {[0,1,2,5,6,7].map((index, order) => <React.Fragment key={index}>
                            <dt className="fw-normal text-muted">ANT{order < 3 ? 1 : 2} · {['L1','L2','L5'][order % 3]}</dt>
                            <dd className="mb-0">{um.agc.values[index] === -1 ? 'Kênh không hợp lệ' : um.agc.values[index]}
                                {reference?.unicore?.agc?.values[index] >= 0 && <small className="text-muted"> · mốc: {reference.unicore.agc.values[index]}</small>}
                            </dd>
                        </React.Fragment>)}
                    </dl>
                </div> : <p className="small text-muted mb-0">Chưa có AGC mới từ UM.</p>}
                {um?.jamming ? <div className="rf-band border rounded p-3">
                    <h6>{um.jamming.source === 'unicore_freqjamstatusa' ? 'FREQJAMSTATUSA' : 'JAMSTATUSA'} · {Math.floor(um.jamming.age_seconds)} giây trước</h6>
                    {um.jamming.flags.map((flag, index) => <div className="d-flex flex-wrap gap-2 align-items-center mb-1" key={index}>
                        <strong>{um.jamming.flags.length === 3 ? ['L1','L2','L5'][index] : 'CW tổng'}</strong>
                        <span className={`badge ${['bg-success','bg-warning text-dark','bg-danger'][flag]}`}>{['Không báo nhiễu CW','Có nhiễu CW','Nhiễu CW mạnh'][flag]}</span>
                        <span>{um.jamming.ratios[index]} / 255</span>
                    </div>)}
                </div> : <p className="small text-muted mb-0">Chưa có cảnh báo CW mới từ UM; chưa thể kết luận.</p>}
                <p className="small text-muted mb-0">AGC UM: 0–255; −1 là kênh không hợp lệ. So sánh cùng thiết bị và anten, không áp ngưỡng u-blox. Chưa có dữ liệu phổ RF từ UM.</p>
            </div>}
            {rf && <>
                <p className="small text-muted mb-2">MON-RF · {Math.floor(rf.age_seconds)} giây trước</p>
                {new Set(rf.blocks.map(b=>b.id)).size !== rf.blocks.length && <p className="small text-warning">Chip báo trùng ID khối RF; giữ đủ các khối theo thứ tự, chưa gán tên băng.</p>}
                <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(min(100%, 280px), 1fr))',gap:12}}>
                    {rf.blocks.map(block => <div className="rf-band border rounded p-3" key={block.index}>
                        <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
                            <strong>{getRfBlockLabel(block, rf.blocks)}</strong>
                            <span className={`badge ${['bg-secondary','bg-success','bg-warning text-dark','bg-danger'][block.jamming]}`}>{stateNames[block.jamming]}</span>
                        </div>
                        <dl className="mb-0" style={{display:'grid',gridTemplateColumns:'minmax(0, 1fr) auto',gap:'4px 12px'}}>
                            <dt className="fw-normal text-muted">Nhiễu nền (thô)</dt><dd className="mb-0">{block.noise}</dd>
                            <dt className="fw-normal text-muted">AGC (thô)</dt><dd className="mb-0">{block.agc}{block.agc_valid ? ' / 8191' : ' · ngoài dải mô tả'}</dd>
                                <dt className="fw-normal text-muted">Chỉ báo nhiễu CW · jamInd (0–255)</dt><dd className="mb-0">{block.cw}</dd>
                            <dt className="fw-normal text-muted">Anten</dt><dd className="mb-0">{['Khởi tạo','Chưa xác định','OK','Ngắn mạch','Hở mạch'][block.antenna] || 'Chưa xác định'}</dd>
                        </dl>
                    </div>)}
                </div>
            </>}
            {reference && <p className="small mb-2" role="status">Mốc so sánh: {new Date(reference.captured_at).toLocaleString()}.
                <button className="btn btn-sm btn-link" onClick={() => setReference(null)}>Xóa mốc</button></p>}
            {!unicore && !spectrum && <p className="small text-muted mb-2">Chưa có phổ GNSS mới.</p>}
            {spectrum && <>
                <p className="small text-muted mb-2">MON-SPAN · {Math.floor(spectrum.age_seconds)} giây trước · Nét liền: hiện tại · Nét đứt: mốc so sánh</p>
                <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(min(100%, 320px), 1fr))',gap:12}}>
                {spectrum.blocks.map((block, index) => {
                    const previous = reference?.spectrum?.blocks.find(b => b.center === block.center && b.span === block.span && b.resolution === block.resolution);
                    const frequency = bin => ((block.center + block.span * (bin - 127) / 256) / 1e6).toFixed(2);
                    const points = bins => bins.map((value, i) => `${i * 2},${255 - value}`).join(' ');
                    return <figure className="mb-2" key={index}>
                        <figcaption className="small"><strong>{index === 0 ? 'L1' : index === 1 ? 'L2 / L5' : `RF ${index}`}</strong> · Tâm {(block.center / 1e6).toFixed(2)} MHz · Khuếch đại {block.pga} dB<br/>Mức phổ thô (0–255) · Bước {(block.resolution / 1e6).toFixed(2)} MHz</figcaption>
                        <svg viewBox="0 0 510 255" role="img" aria-label={`Phổ GNSS quanh ${block.center / 1e6} MHz, mức phổ thô 0 đến 255`}
                            style={{width:'100%',height:'auto',border:'1px solid var(--bs-border-color)',background:'var(--bs-tertiary-bg)'}}>
                            {[0,64,128,192,255].map(value => <g key={value}><line x1="0" x2="510" y1={255-value} y2={255-value} stroke="currentColor" opacity="0.12"/><text x="3" y={Math.max(12,251-value)} fontSize="12" fill="currentColor">{value}</text></g>)}
                            {previous && <polyline points={points(previous.bins)} fill="none" stroke="var(--bs-warning, #ffc107)" strokeWidth="2" strokeDasharray="6 4"/>}
                            <polyline points={points(block.bins)} fill="none" stroke="var(--bs-info, #0dcaf0)" strokeWidth="2"/>
                        </svg>
                        <div className="d-flex justify-content-between small"><span>{frequency(0)} MHz</span><span>{frequency(255)} MHz</span></div>
                        {previous && previous.pga !== block.pga && <p className="small text-warning mb-0">PGA đã đổi: {previous.pga} → {block.pga} dB; cần tính đến mức khuếch đại khi so sánh.</p>}
                    </figure>;
                })}</div>
            </>}
            <p className="small text-muted mb-0">{!unicore && 'Phổ thô không phải công suất dBm; đỉnh phổ đơn lẻ chưa chứng minh có nhiễu. '}Chỉ số RF không xác định nguồn gây nhiễu hoặc sai số tọa độ.</p>
            </details>
        </div>
    </section>;
}

const DashboardPanel = memo(function DashboardPanel({ device, nmeaData }) {
    const [offlineClockNow, setOfflineClockNow] = useState(Date.now());
    useEffect(() => {
        const interval = setInterval(() => setOfflineClockNow(Date.now()), 30000);
        return () => clearInterval(interval);
    }, [device?.serial]);
    const liveDataAvailable = !isDeviceStaleOffline(device, offlineClockNow);
    const isReadonly4g = window.CorsDashboardShared?.isReadonly4gBase?.(device) === true;
    const rtk_status = liveDataAvailable ? (device.rtk_fix_status || 'NO_FIX') : 'NO_FIX';
    const rtcmMessageTypes = Object.keys(device.rtcm_stats || {}).filter(
        type => /^\d{4}$/.test(type) && Number(device.rtcm_stats[type]?.count) > 0
    );

    let fixInfo = liveDataAvailable
        ? { label: 'NO FIX', color: '#ff0000', icon: '❌' }
        : { label: 'OFFLINE', color: '#6c757d', icon: '⏸️' };

    const isAutoSetupStatus = liveDataAvailable && (device.status || '').startsWith('auto_setup');

    if (isReadonly4g) {
        fixInfo = liveDataAvailable && window.CorsDashboardShared?.getMapEffectiveStatus?.(device) === 'online'
            ? { label: 'ONLINE', color: '#00ff88', icon: '📡' }
            : { label: 'OFFLINE', color: '#ff0000', icon: '❌' };
    } else if (isAutoSetupStatus) {
        const AUTO_SETUP_STEPS = {
            'auto_setup_rover': { label: 'Bước 1: Đặt chế độ ROVER', color: '#17a2b8', icon: '🔄', step: 1 },
            'auto_setup_wait_fix': { label: 'Bước 2: Chờ RTK Fix...', color: '#ffc107', icon: '📡', step: 2 },
            'auto_setup_averaging': { label: 'Bước 3: Lấy mẫu tọa độ...', color: '#17a2b8', icon: '📊', step: 3 },
            'auto_setup_base': { label: 'Bước 4: Cài đặt BASE', color: '#17a2b8', icon: '🏯', step: 4 }
        };
        const stepInfo = AUTO_SETUP_STEPS[device.status] || { label: 'Auto Setup...', color: '#17a2b8', icon: '⏳', step: 0 };
        fixInfo = { label: stepInfo.label, color: stepInfo.color, icon: stepInfo.icon };
    } else if (liveDataAvailable && device.base_mode_active) {
        const baseConfig = device.base_config || {};
        if (baseConfig.auto_configured) {
            fixInfo = { label: 'BASE (Auto)', color: '#00ff00', icon: '🏯' };
        } else if (baseConfig.base_setup_method === 'FIXED_LLA') {
            fixInfo = { label: 'BASE FIXED', color: '#00ff00', icon: '🏯' };
        } else {
            if (device.ntrip_connected) {
                fixInfo = { label: 'BASE READY', color: '#00ff00', icon: '📡' };
            } else {
                fixInfo = { label: 'SURVEYING...', color: '#ffaa00', icon: '⏳' };
            }
        }
    } else if (liveDataAvailable) {
        const FIX_STATUS_MAP = {
            'RTK_FIXED': { label: 'RTK Fixed', color: '#00ff00', icon: '🎯' },
            'RTK_FLOAT': { label: 'Float', color: '#ffaa00', icon: '📍' },
            'DGPS': { label: 'DGPS', color: '#ff9900', icon: '⚠️' },
            'GPS_FIX': { label: '3D', color: '#999999', icon: '📡' },
            'NO_FIX': { label: 'NO FIX', color: '#ff0000', icon: '❌' }
        };
        fixInfo = FIX_STATUS_MAP[rtk_status] || FIX_STATUS_MAP['NO_FIX'];
    }
    const bgCanvasRef = useRef(null);
    const satCanvasRef = useRef(null);
    const animationFrameRef = useRef(null);
    const lastSatelliteDataRef = useRef(null);
    const signalBarsContainerRef = useRef(null);

    const [satStats, setSatStats] = useState({ total: 0, tracking: 0, bySystem: {} });

    // Clean up satellite buffers when switching devices
    useEffect(() => {
        if (satelliteBufferRef.current) satelliteBufferRef.current.clear();
        lastSatelliteDataRef.current = null;

        // Clear canvases immediately
        if (satCanvasRef.current) {
            const ctx = satCanvasRef.current.getContext('2d');
            ctx.clearRect(0, 0, satCanvasRef.current.width, satCanvasRef.current.height);
        }
        if (signalBarsContainerRef.current) {
            signalBarsContainerRef.current.innerHTML = '';
        }
        setSatStats({ total: 0, tracking: 0, bySystem: {} });
    }, [device?.serial, liveDataAvailable]);

    // 🆕 METRIC TRACKING STATE
    const [metrics, setMetrics] = useState({
        fixQuality: { value: 0, history: [] },
        satellites: { value: null, history: [] },
        accuracy: { value: undefined, history: [] },
        hdop: { value: 0, history: [] },
        signalStrength: { value: 0, history: [] },
        throughput: { value: 0, history: [] }
    });

    useEffect(() => {
        if (liveDataAvailable) return;
        setMetrics({
            fixQuality: { value: 0, history: [] },
            satellites: { value: null, history: [] },
            accuracy: { value: undefined, history: [] },
            hdop: { value: 0, history: [] },
            signalStrength: { value: 0, history: [] },
            throughput: { value: 0, history: [] }
        });
    }, [device?.serial, liveDataAvailable]);

    const satelliteBufferRef = useRef(new Map());
    const SATELLITE_TIMEOUT = 5000;
    const FADE_DURATION = 2000;
    const safeNmeaData = liveDataAvailable
        ? (nmeaData || { gga: null, gsa: null, satellites: {} })
        : { gga: null, gsa: null, satellites: {} };
    const deviceRef = useRef(device);
    deviceRef.current = device;
    const safeNmeaDataRef = useRef(safeNmeaData);
    useEffect(() => {
        safeNmeaDataRef.current = safeNmeaData;
    }, [safeNmeaData]);
    const baseCoords = getDashboardPosition(device, safeNmeaData);

    const lastStatsUpdate = useRef(0);
    const lastCanvasUpdate = useRef(0);
    const lastBarsUpdate = useRef(0);

    const STATS_INTERVAL = 1000;
    const CANVAS_INTERVAL = 200;
    const BARS_INTERVAL = 500;

    // 🆕 METRICS UPDATE LOGIC
    const metricsNmeaDataRef = useRef(safeNmeaData);
    useEffect(() => {
        metricsNmeaDataRef.current = safeNmeaData;
    }, [safeNmeaData]);

    useEffect(() => {
        if (!device) return;
        const interval = setInterval(() => {
            const currentNmeaData = metricsNmeaDataRef.current || {};
            const gga = currentNmeaData.gga || {};
            const gsa = currentNmeaData.gsa || {};
            const plot = getSatellitePlotSource(device, currentNmeaData, liveDataAvailable);
            const sats = getVisibleSatellites(plot.satellites, plot.maxAgeMs);
            const quality = getGnssQuality(device, currentNmeaData, liveDataAvailable);
            const satCount = sats.length;

            const avgSnr = sats.length > 0
                ? sats.reduce((sum, sat) => sum + (sat.snr || 0), 0) / sats.length
                : 0;

            const currentHacc = quality?.hacc_m ?? undefined;

            setMetrics(prev => ({
                fixQuality: {
                    value: gga.fix_status === 'RTK_FIXED' ? 5 : gga.fix_status === 'DGPS' ? 3 : 1,
                    history: [...prev.fixQuality.history, gga.fix_status === 'RTK_FIXED' ? 5 : gga.fix_status === 'DGPS' ? 3 : 1].slice(-30)
                },
                satellites: {
                    value: satCount,
                    history: [...prev.satellites.history, satCount].slice(-30)
                },
                accuracy: {
                    value: currentHacc,
                    history: currentHacc === undefined ? [] : [...(prev.accuracy?.history || []), currentHacc].slice(-30)
                },
                hdop: {
                    value: gsa.hdop || 0,
                    history: [...prev.hdop.history, gsa.hdop || 0].slice(-30)
                },
                signalStrength: {
                    value: avgSnr.toFixed(1),
                    history: [...prev.signalStrength.history, avgSnr].slice(-30)
                },
                throughput: {
                    value: liveDataAvailable ? (device.bps || 0) : 0,
                    history: [...prev.throughput.history, liveDataAvailable ? (device.bps || 0) : 0].slice(-30)
                }
            }));
        }, 2000);
        return () => clearInterval(interval);
    }, [device?.serial, device?.bps, device?.gnss_quality, device?.gnss_satellites, device?.server_received_at, device?.satellite_count, liveDataAvailable]); // Update logic dependencies

    // SKYPLOT & CANVAS LOGIC
    useEffect(() => {
        if (!bgCanvasRef.current || !satCanvasRef.current) return;

        const bgCtx = bgCanvasRef.current.getContext('2d');
        const satCtx = satCanvasRef.current.getContext('2d');

        const drawSkyplotBase = () => {
            const w = bgCtx.canvas.width, h = bgCtx.canvas.height;
            if (w === 0 || h === 0) return;

            const centerX = w / 2, centerY = h / 2;
            const radius = Math.max(0, Math.min(centerX, centerY) - 36);

            bgCtx.clearRect(0, 0, w, h);

            // Background Gradient
            const grad = bgCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
            grad.addColorStop(0, '#1a2332');
            grad.addColorStop(0.5, '#0d1520');
            grad.addColorStop(1, '#000');
            bgCtx.fillStyle = grad;
            bgCtx.fillRect(0, 0, w, h);

            // Circles
            bgCtx.strokeStyle = '#3a4a5a';
            bgCtx.lineWidth = 1.5;
            bgCtx.font = '11px Arial';
            bgCtx.fillStyle = '#6a7a8a';
            bgCtx.textAlign = 'left';

            [{ frac: 0.33, label: '60°' }, { frac: 0.66, label: '30°' }, { frac: 1.0, label: '0°' }]
                .forEach(({ frac, label }) => {
                    const r = radius * frac;
                    bgCtx.beginPath();
                    bgCtx.arc(centerX, centerY, r, 0, 2 * Math.PI);
                    bgCtx.stroke();
                    bgCtx.fillText(label, centerX + r + 5, centerY);
                });

            // Cross Lines
            bgCtx.strokeStyle = '#2a3a4a';
            bgCtx.lineWidth = 1;
            bgCtx.beginPath();
            for (let i = 0; i < 12; i++) {
                const angle = i * 30 * Math.PI / 180;
                bgCtx.moveTo(centerX, centerY);
                bgCtx.lineTo(centerX + radius * Math.sin(angle), centerY - radius * Math.cos(angle));
            }
            bgCtx.stroke();

            // Labels
            bgCtx.fillStyle = '#fff';
            bgCtx.font = 'bold 18px Arial';
            bgCtx.textAlign = 'center';
            bgCtx.textBaseline = 'middle';
            const labelDistance = radius + 25;
            [{ t: 'N', a: 0 }, { t: 'E', a: 90 }, { t: 'S', a: 180 }, { t: 'W', a: 270 }].forEach(({ t, a }) => {
                const rad = a * Math.PI / 180;
                bgCtx.fillText(t, centerX + labelDistance * Math.sin(rad), centerY - labelDistance * Math.cos(rad));
            });
        };

        drawSkyplotBase();

        const drawSkyplotSatellites = (satellites, maxAgeMs = SATELLITE_TIMEOUT + FADE_DURATION) => {
            const now = Date.now();
            const buffer = satelliteBufferRef.current;

            getVisibleSatellites(satellites, maxAgeMs).forEach(sat => {
                let observedAt = Number(sat.lastSeen || sat.last_seen || now);
                if (observedAt < 1e11) observedAt *= 1000;
                buffer.set(getSatelliteKey(sat), {...sat, lastSeen: observedAt, opacity: 1.0});
            });

            for (const [satKey, satData] of buffer.entries()) {
                const timeSinceLastSeen = now - satData.lastSeen;
                if (timeSinceLastSeen > maxAgeMs) {
                    buffer.delete(satKey);
                } else if (timeSinceLastSeen > maxAgeMs - FADE_DURATION) {
                    const fadeProgress = (timeSinceLastSeen - maxAgeMs + FADE_DURATION) / FADE_DURATION;
                    satData.opacity = Math.max(0, 1 - fadeProgress);
                }
            }

            satCtx.clearRect(0, 0, satCtx.canvas.width, satCtx.canvas.height);
            const centerX = satCtx.canvas.width / 2;
            const centerY = satCtx.canvas.height / 2;
            const radius = Math.max(0, Math.min(centerX, centerY) - 36);

            if (satCtx.canvas.width === 0 || satCtx.canvas.height === 0) return;

            for (const satData of buffer.values()) {
                let az = Number(satData.azimuth);
                let el = Number(satData.elevation);
                let snr = Number(satData.snr);

                if (!hasSatellitePosition(satData) || !Number.isFinite(snr) || snr <= 0) continue;

                const az_rad = (az * Math.PI) / 180;
                const dist = radius * (90 - el) / 90;
                const x = centerX + dist * Math.sin(az_rad);
                const y = centerY - dist * Math.cos(az_rad);

                const system = getSatelliteSystem(satData);
                const flag = flags[system];
                const isTracking = satData.isTracking;

                satCtx.globalAlpha = satData.opacity || 1.0;
                const flagSize = isTracking ? 18 : 14;

                if (isTracking) {
                    satCtx.beginPath();
                    satCtx.arc(x, y, flagSize / 2 + 3, 0, 2 * Math.PI);
                    satCtx.fillStyle = (colors[system] || '#fff') + '40';
                    satCtx.fill();
                }

                if (flag && flag.complete && flag.naturalWidth > 0) {
                    satCtx.drawImage(flag, x - flagSize / 2, y - flagSize / 2, flagSize, flagSize);
                } else {
                    satCtx.beginPath();
                    satCtx.arc(x, y, 9, 0, 2 * Math.PI);
                    satCtx.fillStyle = colors[system] || '#ccc';
                    satCtx.fill();
                }

                satCtx.fillStyle = 'rgba(0, 0, 0, 0.8)';
                const prefix = {us:'G',ru:'R',eu:'E',cn:'C',jp:'J',sb:'S',im:'I',in:'N'}[system] || '?';
                const label = `${prefix}${getNormalizedPrn(satData)}`;
                const labelWidth = Math.max(20, label.length * 5);
                satCtx.fillRect(x - labelWidth / 2, y + flagSize / 2 + 2, labelWidth, 11);
                satCtx.fillStyle = '#fff';
                satCtx.font = 'bold 7px Arial';
                satCtx.textAlign = 'center';
                satCtx.textBaseline = 'middle';
                satCtx.fillText(label, x, y + flagSize / 2 + 7);
            }
            satCtx.globalAlpha = 1.0;
        };

        const drawSignalBars = (satellites, plot) => {
            const container = signalBarsContainerRef.current;
            if (!container) return;

            // Count only fresh satellites with a positive SNR; canvas fade remains visual-only.
            const activeSatellites = getVisibleSatellites(satellites, plot.maxAgeMs);

            // Tạo hash cấu trúc (Chỉ phụ thuộc số lượng và loại PRN - Không chứa giá trị SNR để tránh giật hình)
            const structureHash = activeSatellites.map(s => getSatelliteKey(s)).sort().join('|');

            // Cập nhật text Stats
            let totalSnr = 0;
            let lowSnrCount = 0;
            const groupedSats = activeSatellites.reduce((acc, sat) => {
                const system = getSatelliteSystem(sat);
                if (!acc[system]) acc[system] = [];
                acc[system].push(sat);
                if (sat.snr > 0) totalSnr += sat.snr;
                if (sat.snr < 25 && sat.snr > 0) lowSnrCount++;
                return acc;
            }, {});
            const avgSnr = activeSatellites.length > 0 ? totalSnr / activeSatellites.length : 0;
            setSatStats({ 
                total: activeSatellites.length, 
                tracking: activeSatellites.filter(s => s.isTracking).length, 
                bySystem: groupedSats,
                avgSnr: avgSnr,
                lowSnrCount: lowSnrCount,
                positioned: activeSatellites.filter(hasSatellitePosition).length,
                source: plot.source, age_seconds: plot.age_seconds
            });

            // 1. Nếu CẤU TRÚC bị đổi (thêm/bớt vệ tinh) -> Xóa toàn bộ và vẽ lại DOM
            if (lastSatelliteDataRef.current !== structureHash) {
                lastSatelliteDataRef.current = structureHash;

                const fragment = document.createDocumentFragment();

                Object.keys(constellationNames).forEach(system => {
                    if (groupedSats[system]) {
                        // Bắt chước phần mềm u-center: Phải sắp xếp cố định theo số PRN thay vì SNR để cột không bị đảo lộn vị trí!
                        const sats = groupedSats[system].sort((a, b) => parseFloat(a.prn) - parseFloat(b.prn));
                        const groupDiv = document.createElement('div');
                        groupDiv.className = 'constellation-group';

                        const headerDiv = document.createElement('div');
                        headerDiv.className = 'constellation-header';
                        headerDiv.innerHTML = `<img src="${(flags[system] || flags.un).src}" width="24" height="24" /> ${constellationNames[system]} (<span class="group-count">${sats.length}</span>)`;

                        const barsWrapper = document.createElement('div');
                        barsWrapper.className = 'bars-wrapper';

                        sats.forEach(sat => {
                            const barFillHeight = Math.min(100, sat.snr / 55 * 100);
                            let barColor = colors[system];
                            if (sat.snr >= 40) barColor = '#00ff88';
                            else if (sat.snr >= 20) barColor = '#ffc107';
                            else barColor = '#ff5252';

                            const itemDiv = document.createElement('div');
                            itemDiv.className = 'sat-bar-item';
                            // Gắn id bằng PRN để sau này chọc thẳng vào DOM update
                            itemDiv.id = `sat-bar-${getSatelliteKey(sat).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                            itemDiv.innerHTML = `
                                <div class="sat-bar-snr" style="color: ${barColor}; font-weight: bold;">${sat.snr}</div>
                                <div class="sat-bar"><div class="sat-bar-fill" style="height: ${barFillHeight}%; background: ${barColor};"></div></div>
                                <div class="sat-bar-prn">${getNormalizedPrn(sat)}</div>
                            `;
                            barsWrapper.appendChild(itemDiv);
                        });

                        groupDiv.appendChild(headerDiv);
                        groupDiv.appendChild(barsWrapper);
                        fragment.appendChild(groupDiv);
                    }
                });

                container.innerHTML = '';
                container.appendChild(fragment);
            }
            // 2. Chế độ NGẦM: Chỉ cập nhật trị SNR + Color (Không tái tạo DOM, giúp thanh Transition mượt mà và hết giật nền)
            else {
                activeSatellites.forEach(sat => {
                    const domId = `sat-bar-${getSatelliteKey(sat).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
                    const itemDiv = document.getElementById(domId);
                    if (itemDiv) {
                        const barFillHeight = Math.min(100, sat.snr / 55 * 100);
                        const system = getSatelliteSystem(sat);
                        let barColor = colors[system];
                        if (sat.snr >= 40) barColor = '#00ff88';
                        else if (sat.snr >= 20) barColor = '#ffc107';
                        else barColor = '#ff5252';

                        const snrText = itemDiv.querySelector('.sat-bar-snr');
                        const fillDiv = itemDiv.querySelector('.sat-bar-fill');

                        if (snrText) {
                            snrText.textContent = sat.snr;
                            snrText.style.color = barColor;
                        }
                        if (fillDiv) {
                            fillDiv.style.height = `${barFillHeight}%`;
                            fillDiv.style.background = barColor;
                        }
                    }
                });
            }
        };

        const animate = () => {
            const now = Date.now();
            const { satellites } = safeNmeaDataRef.current;

            // Text Stats Updates
            if (now - lastStatsUpdate.current >= STATS_INTERVAL) {

                const satCountEl = document.getElementById('db-sats');
                if (satCountEl) {
                    const counts = getDecodedSatelliteCounts(Object.values(satellites || {}), SATELLITE_TIMEOUT + FADE_DURATION);
                    satCountEl.textContent = counts.rtcm;
                }

                const dop = getGnssDop(deviceRef.current, safeNmeaDataRef.current,
                    !isDeviceStaleOffline(deviceRef.current), now / 1000);
                const dopEl = document.getElementById('db-dop');
                if (dopEl) dopEl.textContent = dop.timeOnly ? 'Không áp dụng'
                    : `${formatTelemetryNumber(dop.hdop, 2, '—')} / ${formatTelemetryNumber(dop.pdop, 2, '—')}`;
                const rawDop = document.getElementById('db-dop-raw');
                if (rawDop) rawDop.textContent = dop.timeOnly && (dop.rawHdop !== null || dop.rawPdop !== null)
                    ? `Chip trả: ${formatTelemetryNumber(dop.rawHdop, 2, '—')} / ${formatTelemetryNumber(dop.rawPdop, 2, '—')}` : '';
                const dopSource = document.getElementById('db-dop-source');
                if (dopSource) dopSource.textContent = dop.source;
                const position = getDashboardPosition(deviceRef.current, safeNmeaDataRef.current);
                const latEl = document.getElementById('db-lat');
                if (latEl) latEl.textContent = formatTelemetryNumber(position?.lat, 8, '—');
                const lonEl = document.getElementById('db-lon');
                if (lonEl) lonEl.textContent = formatTelemetryNumber(position?.lon, 8, '—');
                const positionSource = document.getElementById('db-position-source');
                if (positionSource) positionSource.textContent = position?.source || 'Chưa có tọa độ';

                lastStatsUpdate.current = now;
            }

            // Canvas & Bars Updates
            if (now - lastCanvasUpdate.current >= CANVAS_INTERVAL) {
                const plot = getSatellitePlotSource(deviceRef.current, safeNmeaDataRef.current,
                    !isDeviceStaleOffline(deviceRef.current), now / 1000);
                drawSkyplotSatellites(plot.satellites, plot.maxAgeMs);
                lastCanvasUpdate.current = now;
            }

            if (now - lastBarsUpdate.current >= BARS_INTERVAL) {
                const plot = getSatellitePlotSource(deviceRef.current, safeNmeaDataRef.current,
                    !isDeviceStaleOffline(deviceRef.current), now / 1000);
                drawSignalBars(plot.satellites, plot);
                lastBarsUpdate.current = now;
            }

            animationFrameRef.current = requestAnimationFrame(animate);
        };

        const handleResize = () => {
            if (bgCanvasRef.current && satCanvasRef.current) {
                const container = bgCanvasRef.current.parentElement;
                if (!container) return;
                const w = container.clientWidth;
                const h = container.clientHeight;

                if (w > 0 && h > 0) {
                    bgCanvasRef.current.width = w;
                    bgCanvasRef.current.height = h;
                    satCanvasRef.current.width = w;
                    satCanvasRef.current.height = h;
                    drawSkyplotBase();
                }
            }
        };

        handleResize();
        setTimeout(handleResize, 100);
        setTimeout(handleResize, 500);
        window.addEventListener('resize', handleResize);

        animate();

        return () => {
            window.removeEventListener('resize', handleResize);
            if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
        };
    }, []);
    /*
    const FIX_STATUS_MAP = {
        'RTK_FIXED': { label: 'RTK Fixed', color: '#00ff00', icon: '🎯', priority: 5 },
        'RTK_FLOAT': { label: 'Float', color: '#ffaa00', icon: '📍', priority: 4 },
        'DGPS': { label: 'DGPS', color: '#ff9900', icon: '⚠️', priority: 3 },
        'GPS_FIX': { label: '3D', color: '#999999', icon: '📡', priority: 2 },
        'NO_FIX': { label: 'NO FIX', color: '#ff0000', icon: '❌', priority: 1 }
    };
    
    const rtk_status = device.rtk_fix_status || 'NO_FIX';
    const fixInfo = FIX_STATUS_MAP[rtk_status] || FIX_STATUS_MAP['NO_FIX'];
    */

    const quality = getGnssQuality(device, safeNmeaData, liveDataAvailable);
    const acc = quality?.hacc_m;
    const fixedBase = device.system_info?.receiver_base_readback?.mode_kind === 'base'
        || (device.base_mode_active && device.base_config?.base_setup_method === 'FIXED_LLA');
    let accDisplayValue = "--";
    let accUnit = "m";
    let accColor = "#ff6b6b";

    if (typeof acc === "number" && !isNaN(acc)) {
        if (acc < 1) {
            accDisplayValue = (acc * 100).toFixed(1);
            accUnit = "cm";
        } else {
            accDisplayValue = acc.toFixed(2);
            accUnit = "m";
        }

        if (acc < 0.02) accColor = "#51cf66";
        else if (acc < 0.05) accColor = "#94d82d";
        else if (acc < 0.1) accColor = "#ffd43b";
    }

    if (fixedBase) accColor = 'var(--bs-info, #0dcaf0)';
    return (
        <div className="dashboard-master">
            {/* 🆕 METRIC CARDS WITH SPARKLINES (Nếu có component MetricCard) */}
            {window.MetricCard && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px', marginBottom: '16px' }}>
                    <window.MetricCard title="Throughput" value={(metrics.throughput.value / 1024).toFixed(1)} unit="KB/s" trend={0} sparklineData={metrics.throughput.history} color="#51cf66" icon="⚡" />
                    <window.MetricCard title="Vệ tinh RTCM" value={metrics.satellites.value ?? "—"} unit="" trend={0} sparklineData={metrics.satellites.history} color="#0d6efd" icon="🛰️" />
                    <window.MetricCard title="hAcc (ước lượng)" value={accDisplayValue} unit={accUnit} trend={0} sparklineData={metrics.accuracy?.history || []} color={accColor} icon="🎯" />
                    <window.MetricCard title="C/N0 RTCM" value={metrics.signalStrength.value} unit="dB-Hz" trend={0} sparklineData={metrics.signalStrength.history} color="#fa5252" icon="📶" />
                </div>
            )}

            {/* STATS STRIP */}
            <div className="stats-strip">
                {/* ✅ CẬP NHẬT FIX STATUS CARD */}
                <div className="stat-item">
                    <div className="stat-icon" style={{ fontSize: '2rem' }}>{fixInfo.icon}</div>
                    <div className="stat-content">
                        <div className="stat-label">{isReadonly4g ? 'Trạng thái RTCM' : 'Fix Status'}</div>
                        <div
                            className="stat-value"
                            style={{
                                color: fixInfo.color,
                                textShadow: `0 0 10px ${fixInfo.color}`,
                                fontWeight: 'bold'
                            }}
                        >
                            {fixInfo.label}
                        </div>
                    </div>
                </div>
                <div className="stat-item"><div className="stat-icon">🛰️</div><div className="stat-content"><div className="stat-label">Vệ tinh RTCM</div><div id="db-sats" className="stat-value">0</div></div></div>
                <div className="stat-item"><div className="stat-icon">📡</div><div className="stat-content"><div className="stat-label">HDOP / PDOP</div><div id="db-dop" className="stat-value">— / —</div><div id="db-dop-source" className="small text-muted">Đang đọc DOP</div><div id="db-dop-raw" className="small text-muted"></div></div></div>
                <div className="stat-item"><div className="stat-icon">📍</div><div className="stat-content"><div className="stat-label">Position</div><div className="stat-value stat-small"><span id="db-lat">—</span> / <span id="db-lon">—</span></div><div id="db-position-source" className="small text-muted"></div></div></div>
            </div>
            <div className="alert alert-info mt-3" role="status">
                {quality ? <div>
                    Nguồn: {quality.source === 'ubx_nav_pvt' ? 'UBX-NAV-PVT'
                        : quality.source === 'unicore_bestnav_sigma' ? 'Unicore BESTNAV (σ ngang tổng hợp)'
                        : 'NMEA GST (σ ngang tổng hợp)'}
                    {' · '}Đọc cách đây {Math.floor(quality.age_seconds)} giây.
                    {quality.hacc_m === null && ' Chip chưa báo độ chính xác hợp lệ.'}
                </div> : <div>Chưa có số liệu độ chính xác còn hiệu lực từ chip.</div>}
                <div className="small mt-1">
                    {fixedBase
                        ? 'Fixed LLA dùng tọa độ đã khai báo. hAcc không xác minh sai số tọa độ Base và không phải độ chính xác RTK của rover.'
                        : 'hAcc là ước lượng của chip, không phải sai số được đo với tọa độ chuẩn.'}
                </div>
            </div>

            {/* MAP */}
            <div className="map-section">
                <BaseLocationMap coords={baseCoords} rtcmOnly={isReadonly4g} />
            </div>

            {/* RTCM & ANTENNA ASSESSMENT - REDESIGNED */}
            <div className="card mb-3 bg-dark border-secondary">
                <div className="card-header d-flex align-items-center bg-dark">
                    <i className="bi bi-broadcast me-2 text-primary"></i>
                    <h6 className="mb-0">RTCM & Antenna Assessment</h6>
                </div>
                <div className="card-body p-3">
                    {(rtcmMessageTypes.length === 0) ? (
                        <div className="text-muted small text-center my-3">
                            <i className="bi bi-hourglass-split me-2"></i>
                            Đang chờ dữ liệu giải mã RTCM từ trạm...
                        </div>
                    ) : (() => {
                        const rtcmDescriptions = {
                            "1001": { name: "GPS L1 Obs (Basic)", desc: "Quan trắc GPS L1 cơ bản", category: "obs" },
                            "1002": { name: "GPS L1 Obs (Extended)", desc: "Quan trắc GPS L1 mở rộng", category: "obs" },
                            "1003": { name: "GPS L1/L2 Obs (Basic)", desc: "Quan trắc GPS L1/L2 cơ bản", category: "obs" },
                            "1004": { name: "GPS L1/L2 Obs (Extended)", desc: "Quan trắc GPS L1/L2 mở rộng", category: "obs" },
                            "1005": { name: "Base Position (ARP)", desc: "Tọa độ trạm Base (không có chiều cao ăng-ten)", category: "base" },
                            "1006": { name: "Base Position + Height", desc: "Tọa độ trạm Base kèm chiều cao ăng-ten", category: "base" },
                            "1007": { name: "Antenna Descriptor", desc: "Mô tả loại ăng-ten đang sử dụng", category: "base" },
                            "1008": { name: "Antenna Serial Number", desc: "Số serial ăng-ten", category: "base" },
                            "1019": { name: "GPS Ephemeris", desc: "Lịch thiên văn GPS – Quỹ đạo chính xác vệ tinh GPS", category: "eph" },
                            "1020": { name: "GLONASS Ephemeris", desc: "Lịch thiên văn GLONASS – Quỹ đạo vệ tinh GLONASS", category: "eph" },
                            "1033": { name: "Receiver & Antenna", desc: "Thông tin máy thu và ăng-ten (model, firmware)", category: "base" },
                            "1042": { name: "BeiDou Ephemeris", desc: "Lịch thiên văn BeiDou – Quỹ đạo vệ tinh BDS", category: "eph" },
                            "1044": { name: "QZSS Ephemeris", desc: "Lịch thiên văn QZSS – Quỹ đạo vệ tinh Nhật Bản", category: "eph" },
                            "1045": { name: "Galileo F/NAV Eph", desc: "Lịch thiên văn Galileo F/NAV – Quỹ đạo vệ tinh EU", category: "eph" },
                            "1046": { name: "Galileo I/NAV Eph", desc: "Lịch thiên văn Galileo I/NAV", category: "eph" },
                            "1074": { name: "GPS MSM4", desc: "Tín hiệu đo GPS đa tần – Pseudorange + Carrier Phase", category: "msm" },
                            "1077": { name: "GPS MSM7", desc: "Tín hiệu GPS đầy đủ (Full) – Độ chính xác cao nhất", category: "msm" },
                            "1084": { name: "GLONASS MSM4", desc: "Tín hiệu đo GLONASS đa tần", category: "msm" },
                            "1087": { name: "GLONASS MSM7", desc: "Tín hiệu GLONASS đầy đủ (Full)", category: "msm" },
                            "1094": { name: "Galileo MSM4", desc: "Tín hiệu đo Galileo đa tần", category: "msm" },
                            "1097": { name: "Galileo MSM7", desc: "Tín hiệu Galileo đầy đủ (Full)", category: "msm" },
                            "1114": { name: "QZSS MSM4", desc: "Tín hiệu đo QZSS đa tần (Nhật Bản)", category: "msm" },
                            "1117": { name: "QZSS MSM7", desc: "Tín hiệu QZSS đầy đủ (Full)", category: "msm" },
                            "1124": { name: "BeiDou MSM4", desc: "Tín hiệu đo BeiDou đa tần (Trung Quốc)", category: "msm" },
                            "1127": { name: "BeiDou MSM7", desc: "Tín hiệu BeiDou đầy đủ (Full)", category: "msm" },
                            "1230": { name: "GLONASS Code-Phase Bias", desc: "Hiệu chỉnh sai lệch mã-pha sóng GLONASS", category: "aux" },
                        };
                        const categoryLabels = {
                            "msm": { label: "📡 Tín hiệu vệ tinh (MSM)", color: "#0d6efd" },
                            "eph": { label: "🌍 Lịch thiên văn (Ephemeris)", color: "#6f42c1" },
                            "base": { label: "📍 Thông tin trạm Base", color: "#20c997" },
                            "obs": { label: "📊 Quan trắc cơ bản", color: "#fd7e14" },
                            "aux": { label: "🔧 Hiệu chỉnh & Phụ trợ", color: "#adb5bd" },
                        };
                        const grouped = {};
                        rtcmMessageTypes.sort().forEach(type => {
                            const info = rtcmDescriptions[type] || { name: `RTCM ${type}`, desc: "Bản tin RTCM khác", category: "aux" };
                            const cat = info.category;
                            if (!grouped[cat]) grouped[cat] = [];
                            grouped[cat].push({ type, stat: device.rtcm_stats[type], ...info });
                        });
                        const catOrder = ["msm", "base", "eph", "obs", "aux"];
                        return (
                            <div>
                                {catOrder.filter(c => grouped[c]).map(cat => {
                                    const catInfo = categoryLabels[cat] || { label: cat, color: "#6c757d" };
                                    return (
                                        <div key={cat} className="mb-3">
                                            <div className="d-flex align-items-center mb-2" style={{ borderLeft: `3px solid ${catInfo.color}`, paddingLeft: '8px' }}>
                                                <span className="small fw-bold" style={{ color: catInfo.color }}>{catInfo.label}</span>
                                            </div>
                                            {grouped[cat].map(item => {
                                                const isMsm = item.category === "msm";
                                                let statusColor = "#6c757d";
                                                let statusBg = "rgba(108,117,125,0.15)";
                                                if (isMsm) {
                                                    if (item.stat.interval <= 1.5) { statusColor = "#198754"; statusBg = "rgba(25,135,84,0.12)"; }
                                                    else if (item.stat.interval <= 3.0) { statusColor = "#ffc107"; statusBg = "rgba(255,193,7,0.12)"; }
                                                    else { statusColor = "#dc3545"; statusBg = "rgba(220,53,69,0.12)"; }
                                                } else {
                                                    if (item.stat.interval <= 15) { statusColor = "#0dcaf0"; statusBg = "rgba(13,202,240,0.10)"; }
                                                    else { statusColor = "#6c757d"; statusBg = "rgba(108,117,125,0.10)"; }
                                                }
                                                return (
                                                    <div key={item.type} className="d-flex align-items-center py-1 px-2 mb-1 rounded" style={{ backgroundColor: statusBg }}>
                                                        <div style={{ minWidth: '52px' }}>
                                                            <span className="badge fw-bold" style={{ backgroundColor: statusColor, fontSize: '0.8rem', minWidth: '44px', display: 'inline-block', textAlign: 'center' }}>{item.type}</span>
                                                        </div>
                                                        <div className="flex-grow-1 ms-2" style={{ lineHeight: 1.3 }}>
                                                            <div className="small fw-semibold text-light">{item.name}</div>
                                                            <div style={{ fontSize: '0.72rem', color: '#8b949e' }}>{item.desc}</div>
                                                        </div>
                                                        <div className="text-end ms-2" style={{ minWidth: '58px' }}>
                                                            <span className="badge bg-dark bg-opacity-75 border" style={{ borderColor: `${statusColor} !important`, color: statusColor, fontSize: '0.78rem' }}>{item.stat.interval}s</span>
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    );
                                })}
                            </div>
                        );
                    })()}
                    <div className="mt-3 pt-3" style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                        <div className="text-muted small mb-2 text-uppercase">Antenna Quality Diagnostics</div>
                        {(() => {
                            const avgSnr = Number(metrics.signalStrength.value) || 0;
                            const sats = isReadonly4g ? Number(device.satellite_count || 0) : metrics.satellites.value;
                            const hasCoords = rtcmMessageTypes.some(k => k === "1005" || k === "1006");
                            
                            const antennaStatus = device.antenna_status || device.system_info?.antenna_status;
                            const hasAgentAssessment = ['OK', 'FAULT'].includes(antennaStatus?.state);
                            const isAntennaFault = antennaStatus?.state === "FAULT";
                            const hasDecodedData = rtcmMessageTypes.length > 0;
                            const hasSignalData = Boolean(safeNmeaData.gga) || Object.keys(safeNmeaData.satellites || {}).length > 0;
                            
                            const rtcmInputBps = liveDataAvailable ? Number(
                                device.rtcm_input_bps ||
                                device.ntrip_stats?.rtcm_input_bps ||
                                device.ntrip_stats?.rtcm_in_bps ||
                                device.mqtt_stats?.source_bps ||
                                0
                            ) : 0;

                            const hasMsm = rtcmMessageTypes.some(k => {
                                const t = Number(k);
                                return t >= 1071 && t <= 1127;
                            });

                            return (
                                <ul className="mb-0 text-light small ps-3" style={{ listStyleType: "circle" }}>
                                    {!liveDataAvailable && (hasAgentAssessment || hasDecodedData) && (
                                        <li className="text-warning mb-1">Trạm offline · Hiển thị dữ liệu đánh giá lần cuối.</li>
                                    )}
                                    {hasAgentAssessment && (
                                        <li className={`${isAntennaFault ? 'text-danger' : 'text-success'} mb-1`}>
                                            {isAntennaFault ? 'Cảnh báo Ăng-ten: ' : 'Đánh giá từ agent: '}
                                            {antennaStatus.message || (isAntennaFault ? 'Phát hiện lỗi ăng-ten.' : 'Ăng-ten hoạt động bình thường.')}
                                        </li>
                                    )}
                                    {!hasAgentAssessment && !hasDecodedData && !hasSignalData && (
                                        <li className="text-info mb-1">Chưa có dữ liệu GNSS/RTCM để đánh giá.</li>
                                    )}
                                    {hasDecodedData && <>
                                    {hasCoords ? (
                                        <li className="text-success mb-1">Tọa độ Base (1005/1006) đã sẵn sàng.</li>
                                    ) : (
                                        <li className="text-danger mb-1">Chưa có gói tọa độ 1005/1006. Rover không thể FIX RTK!</li>
                                    )}

                                    {liveDataAvailable && !hasMsm && (
                                        <li className="text-danger mb-1 fw-semibold">
                                            <i className="bi bi-exclamation-triangle-fill me-1"></i>
                                            Lỗi Giải mã RTCM: Không nhận được bất kỳ trị đo vệ tinh nào (gói MSM). Rover không thể FIX RTK!
                                        </li>
                                    )}

                                    {liveDataAvailable && !hasAgentAssessment && rtcmInputBps > 0 && rtcmInputBps < 100 && !isAntennaFault && (
                                        <li className="text-danger mb-1 fw-semibold">
                                            <i className="bi bi-exclamation-triangle-fill me-1"></i>
                                            Cảnh báo dòng dữ liệu: Tốc độ nhận RTCM từ máy thu cực thấp ({rtcmInputBps} B/s). Ăng-ten có thể đã hỏng hoặc lỏng cáp.
                                        </li>
                                    )}
                                    
                                    </>}
                                    {liveDataAvailable && hasSignalData && <>
                                    {sats === 0 && !hasAgentAssessment && (
                                        <li className="text-danger mb-1 fw-semibold">
                                            <i className="bi bi-exclamation-triangle-fill me-1"></i>
                                            Lỗi Tín hiệu: Không nhận được vệ tinh nào (0 Sats). Kiểm tra kết nối ăng-ten.
                                        </li>
                                    )}
                                    
                                    {sats < 12 && sats > 0 && (
                                        <li className="text-warning mb-1">Số lượng vệ tinh quan sát được khá thấp ({sats}). Góc nhìn bầu trời hẹp.</li>
                                    )}
                                    
                                    {avgSnr > 0 && avgSnr < 35 && (
                                        <li className="text-warning mb-1">Cường độ tín hiệu thấp (SNR trung bình {avgSnr.toFixed(1)} dB-Hz). <strong>Khuyến nghị:</strong> Nâng cao ăng-ten hoặc đưa ra nơi trống trải.</li>
                                    )}
                                    {avgSnr >= 38 && (
                                        <li className="text-success mb-1">C/N0 trung bình của các vệ tinh RTCM: {avgSnr.toFixed(1)} dB-Hz.</li>
                                    )}
                                    {avgSnr >= 35 && avgSnr < 38 && (
                                        <li className="text-info mb-1">Tín hiệu ăng-ten đạt mức khá (SNR trung bình {avgSnr.toFixed(1)} dB-Hz).</li>
                                    )}
                                    </>}
                                </ul>
                            );
                        })()}
                    </div>
                </div>
            </div>

            {/* HARDWARE & ECO SAVINGS PANEL */}
            {!isReadonly4g && device.system_info && (
                <div className="card mb-3 border-0" style={{ background: 'linear-gradient(135deg, rgba(13,110,253,0.08), rgba(32,201,151,0.08))', border: '1px solid rgba(13,110,253,0.25)' }}>
                    <div className="card-header d-flex align-items-center border-0" style={{ background: 'transparent' }}>
                        <i className="bi bi-cpu-fill me-2" style={{ color: '#0dcaf0' }}></i>
                        <h6 className="mb-0 text-light">Hardware Telemetry & Eco Savings</h6>
                        <span className="badge ms-auto" style={{ backgroundColor: 'rgba(13,202,240,0.15)', color: '#0dcaf0', fontSize: '0.7rem' }}>Aitogy.com.vn</span>
                    </div>
                    <div className="card-body pt-0 pb-3 px-3">
                        {(() => {
                            const savings = device.savings_stats || device.system_info?.savings_stats || {};
                            let isAwake = liveDataAvailable && (device.status !== "sleep");
                            if (savings.is_sleeping !== undefined && savings.is_sleeping !== null) {
                                isAwake = !savings.is_sleeping;
                            } else {
                                const cfg = device.service_config || {};
                                if (cfg.server1_enabled) {
                                    const s1Active = cfg.server1_stream_active !== undefined ? cfg.server1_stream_active : cfg.stream_active;
                                    if (s1Active !== undefined && s1Active !== null) {
                                        isAwake = !!s1Active;
                                    }
                                }
                            }
                            if (!liveDataAvailable) isAwake = false;

                            const rtcmBps = liveDataAvailable ? Number(
                                device.rtcm_input_bps ||
                                device.ntrip_stats?.rtcm_input_bps ||
                                device.ntrip_stats?.rtcm_in_bps ||
                                device.mqtt_stats?.source_bps ||
                                0
                            ) : 0;
                            const telemetryBps = savings.telemetry_bps || 0;
                            const currentVolts = device.system_info?.cpu?.core_volts || null;

                            return (
                                <>
                                    <div className="row g-2 mb-3">
                                        {/* CỘT THỨC (AWAKE) */}
                                        <div className="col-6">
                                            <div className="rounded p-2" style={{ backgroundColor: isAwake ? 'rgba(25,135,84,0.1)' : 'rgba(255,255,255,0.02)', border: isAwake ? '1px solid #198754' : '1px solid transparent' }}>
                                                <div className="fw-bold mb-2 text-center" style={{ color: isAwake ? '#198754' : '#8b949e', fontSize: '0.85rem' }}>
                                                    <i className="bi bi-broadcast me-1"></i> Trạng thái Thức
                                                </div>
                                                <div className="d-flex justify-content-between mb-1" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Mạng:</span>
                                                    <span className="fw-bold text-light">{isAwake ? `${rtcmBps + telemetryBps} B/s` : '--'}</span>
                                                </div>
                                                <div className="d-flex justify-content-between mb-1" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Điện áp:</span>
                                                    <span className="fw-bold text-warning">{isAwake && currentVolts ? `${currentVolts}V` : '--'}</span>
                                                </div>
                                                <div className="d-flex justify-content-between" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Công suất:</span>
                                                    <span className="fw-bold text-success">{isAwake ? '3.5 W' : '--'}</span>
                                                </div>
                                            </div>
                                        </div>
                                        
                                        {/* CỘT NGỦ (SLEEP) */}
                                        <div className="col-6">
                                            <div className="rounded p-2" style={{ backgroundColor: !isAwake ? 'rgba(13,202,240,0.1)' : 'rgba(255,255,255,0.02)', border: !isAwake ? '1px solid #0dcaf0' : '1px solid transparent' }}>
                                                <div className="fw-bold mb-2 text-center" style={{ color: !isAwake ? '#0dcaf0' : '#8b949e', fontSize: '0.85rem' }}>
                                                    <i className="bi bi-moon-stars me-1"></i> Trạng thái Ngủ
                                                </div>
                                                <div className="d-flex justify-content-between mb-1" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Mạng:</span>
                                                    <span className="fw-bold text-light">{!isAwake ? `${telemetryBps} B/s` : '--'}</span>
                                                </div>
                                                <div className="d-flex justify-content-between mb-1" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Điện áp:</span>
                                                    <span className="fw-bold text-warning">{!isAwake && currentVolts ? `${currentVolts}V` : '--'}</span>
                                                </div>
                                                <div className="d-flex justify-content-between" style={{ fontSize: '0.75rem' }}>
                                                    <span className="text-muted">Công suất:</span>
                                                    <span className="fw-bold text-success">{!isAwake ? '2.8 W' : '--'}</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    
                                    {(device.savings_stats || device.system_info?.savings_stats) && (() => {
                                        const actualSavings = device.savings_stats || device.system_info?.savings_stats || {};
                                        return (
                                            <div className="rounded p-2 d-flex align-items-center justify-content-between" style={{ backgroundColor: 'rgba(25,135,84,0.12)', border: '1px solid rgba(25,135,84,0.3)' }}>
                                                <div className="d-flex align-items-center">
                                                    <i className="bi bi-shield-check fs-4 me-3" style={{ color: '#198754' }}></i>
                                                    <div>
                                                        <div style={{ fontSize: '0.7rem', color: '#8b949e', textTransform: 'uppercase' }}>Tích lũy tài nguyên đã tiết kiệm</div>
                                                        <div className="fw-bold fs-5" style={{ color: '#198754', lineHeight: 1.2 }}>
                                                            {((actualSavings.total_data_saved_bytes || 0) / 1048576).toFixed(2)} <span style={{fontSize: '0.65em'}}>MB</span>
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="text-end pe-2">
                                                    <div className="fw-bold fs-5 text-warning" style={{ lineHeight: 1.2 }}>
                                                        {actualSavings.total_saved_mwh ? actualSavings.total_saved_mwh.toFixed(1) : "0.0"} <span style={{fontSize: '0.65em'}}>mWh</span>
                                                    </div>
                                                </div>
                                            </div>
                                        );
                                    })()}
                                </>
                            );
                        })()}
                    </div>
                </div>
            )}

            {/* SKYPLOT & SIGNALS */}
            {device.detected_chip_type === 'Unicorecomm' && Object.keys(safeNmeaData.satellites || {}).length === 0 && (
                <div className="alert alert-info small" role="status">
                    Đang chờ dữ liệu vệ tinh từ decoder RTCM. Có danh sách bản tin chưa đồng nghĩa đã đủ dữ liệu vị trí quỹ đạo để vẽ biểu đồ.
                </div>
            )}
            {['Ublox','Unicorecomm'].includes(device.detected_chip_type) && <RfDiagnostics key={`${device.serial}:${device.detected_chip_type}`} device={device} live={liveDataAvailable} />}

            <div className="sky-grid">
                <div className="sky-panel skyplot-panel" style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                    <div className="panel-header" style={{ flexShrink: 0 }}>
                        <i className="bi bi-radar"></i><span>Sky View</span>
                        <span className="badge bg-primary-subtle text-primary ms-auto">{satStats.total} có tín hiệu · <span data-no-translate>RTCM</span></span>
                    </div>
                    <div className="small text-muted px-3 pb-1" data-no-translate>G GPS · R GLONASS · E Galileo · C BeiDou · J QZSS · S SBAS</div>
                    <div className="skyplot-wrapper" style={{ position: 'relative', width: '100%', height: '100%' }}>
                        <canvas ref={bgCanvasRef} className="skyplot-bg" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 1 }}></canvas>
                        {satStats.total > 0 && !satStats.positioned && <div className="position-absolute top-50 start-50 translate-middle text-center small text-muted px-3" style={{zIndex: 3, width: '80%'}}>
                            Có tín hiệu vệ tinh nhưng chưa có góc phương vị / độ cao để vẽ.
                        </div>}
                        <canvas ref={satCanvasRef} className="skyplot-sat" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 2 }}></canvas>
                    </div>
                </div>

                <div className="sky-panel signals-panel">
                    <div className="panel-header">
                        <i className="bi bi-bar-chart-fill"></i><span>Signal Strength (SNR)</span>
                        <div className="ms-auto d-flex gap-2">
                            <span className="badge bg-secondary">{satStats.total} có tín hiệu</span>
                        </div>
                    </div>
                    <div ref={signalBarsContainerRef} className="signals-scroll" style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '10px' }}></div>

                    {/* Antenna Assessment */}
                    <div className="antenna-assessment-section mt-2 p-2 mx-2 mb-2 rounded" style={{ backgroundColor: 'rgba(0,0,0,0.1)' }}>
                        <div className="d-flex justify-content-between align-items-center mb-1">
                            <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                                <i className="bi bi-broadcast me-1"></i>Antenna Health
                            </span>
                            <span className="badge rounded-pill" style={{ backgroundColor: satStats.total > 0 && satStats.avgSnr > 35 ? 'var(--success-color)' : 'var(--warning-color)' }}>
                                {satStats.total > 0 ? (satStats.avgSnr || 0).toFixed(1) + ' dBHz Avg' : 'N/A'}
                            </span>
                        </div>
                        <div className="d-flex justify-content-between small text-muted">
                            <div className="text-center">
                                <div>Diversity</div>
                                <div style={{ color: Object.keys(satStats.bySystem).length >= 3 ? 'var(--success-color)' : 'var(--warning-color)', fontWeight: 'bold' }}>
                                    {Object.keys(satStats.bySystem).length} Sys
                                </div>
                            </div>
                            <div className="text-center">
                                <div>Multipath Risk</div>
                                <div style={{ color: (satStats.lowSnrCount || 0) > 5 ? 'var(--danger-color)' : 'var(--success-color)', fontWeight: 'bold' }}>
                                    {(satStats.lowSnrCount || 0)} Low SNR
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

        </div>
    );
});
window.DashboardPanel = DashboardPanel;
})();
