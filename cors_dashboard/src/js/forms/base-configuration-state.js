import { chipCoordinate, formatApprovedAltitude } from './base-configuration-coordinate-utils.js';

const getAutoBaseResultText = (autoFixedResult, progress) => {
    if (autoFixedResult) {
        if (progress.auto_applied === true) {
            return `Đã tự áp dụng Fixed LLA sau ${progress.sample_count || 0} mẫu RTK Fixed.`;
        }
        return `Đã lấy ${progress.sample_count || 0} mẫu RTK Fixed. Xem tọa độ trước khi áp dụng Fixed LLA.`;
    }
    if (['completed', 'unverified'].includes(progress.phase)) {
        return 'Kết quả cũ chưa được xác nhận RTK Fixed. Cần chạy lại Auto Base.';
    }
    if (progress.phase === 'failed') return `Auto Base thất bại: ${progress.reason || 'Không đủ dữ liệu RTK Fixed.'}`;
    if (progress.phase === 'stopped') return 'Auto Base đã dừng.';
    return '';
};
const getAutoBaseReviewText = progress => {
    if (progress.auto_applied === true) return 'Fixed LLA đã được tự áp dụng sau khi receiver xác nhận tọa độ.';
    if (progress.phase === 'ready_for_review') return 'Chưa áp dụng tọa độ này vào máy.';
    return 'Kết quả đang chờ duyệt trên UI.';
};
const getAutoBaseIntroText = (sourceMode, autoFixedResult) => {
    if (sourceMode === 'nearest') {
        return 'ESP32 vào Rover, gửi vị trí lên backend, nhận cải chính từ trạm gần nhất, lấy mẫu RTK_FIXED rồi tự chuyển sang Fixed LLA.';
    }
    if (autoFixedResult) return 'Đã có kết quả. Kiểm tra tọa độ rồi xác nhận áp dụng Fixed LLA.';
    return 'Lấy mẫu RTK Fixed từ Base bạn nhập. Sau khi có kết quả, bạn kiểm tra và xác nhận áp dụng Fixed LLA.';
};
const getBaseSubmitLabel = (method, isAutoSetupActive, sourceMode, autoFixedResult) => {
    if (method !== 'AUTO_CORS') return 'Áp dụng cấu hình BASE';
    if (isAutoSetupActive) return 'Đang lấy tọa độ…';
    if (sourceMode === 'nearest') return 'Chuyển Rover & tìm trạm gần nhất';
    if (autoFixedResult) return 'Lấy lại tọa độ Auto Base';
    return 'Lấy tọa độ Auto Base';
};
// Keep per-station form drafts across navigation in this tab; credentials stay in memory.
const autoBaseDrafts = new Map();

function hasValidApprovedBaseCoordinates(coords) {
    return ['lat', 'lon', 'alt'].every(key =>
        coords[key] != null && String(coords[key]).trim() !== '' && Number.isFinite(Number(coords[key]))
    );
}

function buildFixedBaseParams({
    approvedCoords, latitude, longitude, altitude, finalChipType, altitudeReference,
    autoProgress, autoFixedResult, reviewReport, conversionReport, fixedAccuracyM,
}) {
    const latStr = String(approvedCoords ? approvedCoords.lat : latitude).trim();
    const lonStr = String(approvedCoords ? approvedCoords.lon : longitude).trim();
    const latNum = Number.parseFloat(latStr);
    const lonNum = Number.parseFloat(lonStr);
    if (!latStr || !lonStr || Number.isNaN(latNum) || Number.isNaN(lonNum)) {
        return { error: 'Vui lòng nhập vĩ độ và kinh độ hợp lệ.' };
    }

    let alt;
    if (approvedCoords) {
        alt = formatApprovedAltitude(approvedCoords.alt, finalChipType);
    } else if (finalChipType === 'Ublox') {
        alt = chipCoordinate(String(altitude).trim() || '0', 4);
    } else {
        alt = String(altitude).trim() || '0';
    }
    const coords = {
        lat: chipCoordinate(latStr, finalChipType === 'Ublox' ? 9 : 10),
        lon: chipCoordinate(lonStr, finalChipType === 'Ublox' ? 9 : 10),
        alt,
    };
    const params = {
        coords,
        altitude_reference: approvedCoords || finalChipType === 'Ublox' ? 'ELLIPSOID' : altitudeReference,
        accuracy: String(fixedAccuracyM).trim().replace(',', '.') || '0.01',
    };
    const candidate = autoProgress.averaged_coord;
    const isAutoBaseCoordinate = approvedCoords || (autoFixedResult && candidate && ['lat', 'lon', 'alt'].every(key =>
        Math.abs(Number(coords[key]) - Number(candidate[key])) <= (key === 'alt' ? 0.00011 : 1.01e-9)
    ));
    if (isAutoBaseCoordinate) {
        params.conversion_report = { ...(approvedCoords ? reviewReport : conversionReport), output_coord: { ...coords } };
        params.coordinate_source = 'AUTO_CORS';
    }
    return { params };
}

function getAutoBaseSubmissionError({
    corsSourceMode, supportsAutoBaseNearest, corsHost, corsPort, corsUsername, corsPassword,
    selectedMountpoint, projectionMeridian, projectionScale,
}) {
    if (corsSourceMode === 'nearest' && !supportsAutoBaseNearest) {
        return 'Cần cập nhật agent ESP32 để tự chọn CORS gần nhất.';
    }
    if (corsSourceMode !== 'nearest') {
        const port = Number(corsPort);
        if (!corsHost.trim() || !Number.isInteger(port) || port < 1 || port > 65535 || !corsUsername.trim() || !corsPassword) {
            return 'Vui lòng nhập đủ thông tin kết nối Base.';
        }
        if (!selectedMountpoint) {
            return 'Vui lòng bấm "Kết nối Base & lấy mountpoint" rồi chọn một mountpoint.';
        }
    }
    if ((projectionMeridian.trim() && !projectionScale) || (!projectionMeridian.trim() && projectionScale)) {
        return 'Nhập cả kinh tuyến trục và hệ số k₀, hoặc để trống cả hai.';
    }
    return '';
}

function buildGnssOptions({
    isAdmin, existingGnss, activeChip, interferenceDetectionEnabled, jammingSensitivityHigh,
    outputMode, msmLevel, constellations, isLteStation,
}) {
    if (!isAdmin && existingGnss) return { ...existingGnss };
    const isDefaultOptions = !isAdmin;
    let outputMsmLevel;
    if (msmLevel === 'both') {
        outputMsmLevel = 'both';
    } else {
        const level = isDefaultOptions ? msmLevel || 4 : msmLevel;
        outputMsmLevel = Number(level);
    }
    return {
        ...(activeChip === 'Ublox' ? {
            interference_detection_enabled: isDefaultOptions ? true : interferenceDetectionEnabled,
            jamming_sensitivity_high: jammingSensitivityHigh,
        } : {}),
        output_mode: isDefaultOptions ? outputMode || 'diagnostics_rtcm' : outputMode,
        msm_level: outputMsmLevel,
        constellations: constellations || {
            gps: true, glo: true, gal: true, bds: true, qzss: true, sbas: true,
        },
        ...(!isLteStation ? { ports: ['USB'] } : {}),
    };
}

function buildAutoCorsPayload({
    corsSourceMode, corsHost, corsPort, corsUsername, corsPassword, selectedMountpoint,
    projectionMeridian, projectionScale,
}) {
    const autoCors = corsSourceMode === 'nearest' ? { source_mode: 'nearest' } : {
        source_mode: 'manual',
        ip: corsHost.trim(),
        port: Number(corsPort),
        user: corsUsername.trim(),
        password: corsPassword,
        mountpoint: selectedMountpoint,
    };
    if (projectionMeridian.trim() || projectionScale) {
        const l0 = Number(projectionMeridian);
        const k0 = Number(projectionScale);
        if (!projectionMeridian.trim() || !projectionScale || !Number.isFinite(l0) || l0 < 100 || l0 > 112) {
            return { error: 'Nhập đủ kinh tuyến trục và hệ số k₀ hợp lệ.' };
        }
        autoCors.l0_deg = l0;
        autoCors.k0 = k0;
    }
    return { payload: autoCors };
}

function buildBaseMethodParams(options) {
    const { submittedMethod } = options;
    const params = { base_setup_method: submittedMethod };
    if (submittedMethod === 'FIXED_LLA') {
        const fixedBase = buildFixedBaseParams(options);
        if (fixedBase.error) return { error: fixedBase.error };
        return { params: { ...params, ...fixedBase.params }, coordinates: fixedBase.params.coords };
    }
    if (submittedMethod === 'SURVEY_IN') {
        params.survey_in_duration = Number.parseInt(options.svinDuration);
        // Độ chính xác Survey-In nhập theo mét; backend tự đổi đơn vị cho Ublox khi cần.
        params.survey_in_accuracy = Number.parseFloat(options.svinAccuracyM);
    }
    if (submittedMethod === 'AUTO_CORS') {
        const error = getAutoBaseSubmissionError(options);
        if (error) return { error };
    }
    return { params };
}

function getLteConstellationError(isLteStation, constellations) {
    if (!isLteStation || ['gps', 'glo', 'gal', 'bds', 'qzss'].some(key => constellations[key])) return '';
    return 'Chọn ít nhất một hệ vệ tinh đầu ra.';
}

function restoreBaseFormDraft({ device, isAutoSetupActive, draftOwnerRef, skipDraftSaveRef, corsRequestRef, setters }) {
    const switched = draftOwnerRef.current.serial !== device.serial;
    const savedMethod = device.base_config?.base_setup_method;
    const methodChanged = draftOwnerRef.current.savedMethod !== savedMethod;
    if (switched) {
        skipDraftSaveRef.current = true;
        ++corsRequestRef.current;
        const draft = autoBaseDrafts.get(device.serial);
        setters.setCorsHost(draft?.host || '');
        setters.setCorsSourceMode(draft?.sourceMode || device.base_config?.auto_cors_source_mode || 'nearest');
        setters.setCorsPort(draft?.port || '2101');
        setters.setCorsUsername(draft?.username || '');
        setters.setCorsPassword(draft?.password || '');
        setters.setCorsSources(draft?.sources || []);
        setters.setSelectedMountpoint(draft?.mountpoint || '');
        setters.setProjectionMeridian(draft?.meridian || '');
        setters.setProjectionScale(draft?.scale || '');
        setters.setCorsLookupError('');
        setters.setIsLoadingCorsSources(false);
        setters.setMethod(isAutoSetupActive ? 'AUTO_CORS' : draft?.method || savedMethod || 'AUTO_CORS');
    } else if (isAutoSetupActive) {
        setters.setMethod('AUTO_CORS');
    } else if (methodChanged && savedMethod) {
        setters.setMethod(savedMethod);
    }
    draftOwnerRef.current = { serial: device.serial, savedMethod };
}

function resetBaseFormValues(setters, device, isLteStation) {
    setters.setManualChip(device.detected_chip_type === 'RTCM3_Source' ? 'Unicorecomm' : 'Ublox');
    setters.setLatitude('');
    setters.setLongitude('');
    setters.setAltitude('');
    setters.setCoordinatePasteNotice(null);
    setters.setAltitudeReference('ELLIPSOID');
    setters.setFixedAccuracyM('0.01');
    setters.setSvinDuration('300');
    setters.setSvinAccuracyM('3.00');
    setters.setInterferenceDetectionEnabled(true);
    setters.setJammingSensitivityHigh(true);
    setters.setOutputMode(isLteStation ? 'rtcm_only' : 'diagnostics_rtcm');
    setters.setMsmLevel(isLteStation ? 'both' : '4');
    setters.setConstellations({ gps: true, glo: true, gal: true, bds: true, qzss: true, sbas: true });
}

function restoreBaseCoordinates(config, activeChip, detectedChipType, setters) {
    const coords = config.coords || ((config.lat !== undefined && config.lon !== undefined)
        ? { lat: config.lat, lon: config.lon, alt: config.alt }
        : null);
    if (coords) {
        const precision = activeChip === 'Ublox' ? 9 : 10;
        setters.setLatitude(coords.lat != null ? chipCoordinate(coords.lat, precision) : coords.lat ?? '');
        setters.setLongitude(coords.lon != null ? chipCoordinate(coords.lon, precision) : coords.lon ?? '');
        setters.setAltitude(coords.alt ?? '');
        setters.setAltitudeReference(config.altitude_reference === 'ELLIPSOID' || detectedChipType === 'Ublox'
            ? 'ELLIPSOID' : 'LEGACY');
    }
    if (config.accuracy !== undefined) setters.setFixedAccuracyM(String(config.accuracy));
}

function restoreBaseGnssOptions(options, setters) {
    if (!options || typeof options !== 'object') return;
    setters.setInterferenceDetectionEnabled(options.interference_detection_enabled !== false);
    setters.setJammingSensitivityHigh(options.jamming_sensitivity_high !== false);
    if (options.output_mode) setters.setOutputMode(String(options.output_mode));
    if ([4, 7].includes(Number(options.msm_level)) || options.msm_level === 'both') {
        setters.setMsmLevel(String(options.msm_level));
    }
    if (options.constellations && typeof options.constellations === 'object') {
        setters.setConstellations(previous => ({
            ...previous,
            ...Object.fromEntries(Object.entries(options.constellations)
                .filter(([key]) => Object.prototype.hasOwnProperty.call(previous, key))
                .map(([key, value]) => [key, !!value])),
        }));
    }
}

function restoreBaseConfig(config, activeChip, detectedChipType, setters) {
    if (!config) return;
    restoreBaseCoordinates(config, activeChip, detectedChipType, setters);
    if (config.survey_in_duration !== undefined) setters.setSvinDuration(String(config.survey_in_duration));
    if (config.survey_in_accuracy !== undefined) setters.setSvinAccuracyM(String(config.survey_in_accuracy));
    restoreBaseGnssOptions(config.gnss_options, setters);
}


export {
    getAutoBaseResultText, getAutoBaseReviewText, getAutoBaseIntroText, getBaseSubmitLabel,
    autoBaseDrafts, hasValidApprovedBaseCoordinates, buildFixedBaseParams,
    getAutoBaseSubmissionError, buildGnssOptions, buildAutoCorsPayload, buildBaseMethodParams,
    getLteConstellationError, restoreBaseFormDraft, resetBaseFormValues, restoreBaseConfig,
};
