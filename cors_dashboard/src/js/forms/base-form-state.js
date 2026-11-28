const { useEffect, useState } = React;

export function getBaseChipSelection(device, manualChip) {
    const knownChips = ['Ublox', 'Unicorecomm'];
    const needsManualSelection = !knownChips.includes(device.detected_chip_type);
    return {
        needsManualSelection,
        activeChip: needsManualSelection ? manualChip : device.detected_chip_type,
    };
}

export function getAutoBaseProgressSummary(device, autoProgress) {
    const autoSamplesCollected = Number(autoProgress.samples_collected || 0);
    const autoSamplesTarget = Number(autoProgress.samples_target || 0);
    let ggaAge = null;
    if (autoProgress.gga_age_seconds !== undefined && autoProgress.gga_age_seconds !== null) {
        ggaAge = autoProgress.gga_age_seconds;
    } else if (device?.last_raw_gga_age !== undefined) {
        ggaAge = device.last_raw_gga_age;
    }
    return {
        autoSamplesCollected,
        autoSamplesTarget,
        autoProgressPercent: autoSamplesTarget > 0
            ? Math.min(100, Math.round((autoSamplesCollected / autoSamplesTarget) * 100))
            : null,
        rawGgaSentence: autoProgress.last_raw_gga || device?.last_raw_gga || '',
        ggaAge,
        ggaFix: autoProgress.gga_fix_status || device?.last_gga_fix_status || device?.rtk_fix_status || 'NO_FIX',
        rtcmBytesReceived: autoProgress.rtcm_bytes || 0,
        rtcmPktsReceived: autoProgress.rtcm_pkts || 0,
        fixedStreak: autoProgress.fixed_streak || 0,
        fixedStreakReq: autoProgress.fixed_streak_required || 5,
        autoFixedResult: ['ready_for_review', 'completed'].includes(autoProgress.phase)
            && autoProgress.fixed_samples_only === true,
    };
}

function isValidLocationInput(locationInput) {
    return ['lat', 'lon', 'alt'].every(key => locationInput?.[key] != null
        && typeof locationInput[key] !== 'boolean'
        && String(locationInput[key]).trim() !== ''
        && Number.isFinite(Number(locationInput[key])));
}

function useAutoBaseProvinceLookup(locationInput, locationKey, autoFixedResult, reportedProvince) {
    const [provinceLookup, setProvinceLookup] = useState(null);
    useEffect(() => {
        if (!autoFixedResult || reportedProvince || !isValidLocationInput(locationInput)
            || typeof window.apiFetch !== 'function') return;
        let active = true;
        const keys = ['lat', 'lon', 'alt'];
        const point = Object.fromEntries(keys.map(key => [key, Number(locationInput[key])]));
        setProvinceLookup({key:locationKey, state:'loading'});
        void (async () => {
            try {
                const response = await window.apiFetch('/api/coordinates/auto-base-location', {
                    method:'POST', body:JSON.stringify(point)
                });
                if (!response.ok) throw new Error('Máy chủ chưa trả được tỉnh/thành.');
                const location = await response.json();
                const matchesInput = keys.every(key => Number(location.input_coord?.[key]) === point[key]);
                if (!matchesInput) throw new Error('Phản hồi vị trí không khớp tọa độ của phiên này.');
                if (active) setProvinceLookup({key:locationKey, state:'done', location});
            } catch (error) {
                if (active) setProvinceLookup({key:locationKey, state:'error', error:error.message});
            }
        })();
        return () => { active = false; };
    }, [locationKey, autoFixedResult, reportedProvince]);
    return provinceLookup;
}

function buildAutoBaseReviewReport(conversionReport, recalculated) {
    if (!recalculated) return conversionReport;
    return {
        ...conversionReport,
        calculated_coord: Object.fromEntries(Object.entries(recalculated.calculated_coord).map(([key, value]) => [key, String(value)])),
        projected_coord: Object.fromEntries(Object.entries(recalculated.projected_coord).map(([key, value]) => [key, String(value)])),
        central_meridian_deg: String(recalculated.central_meridian_deg),
        k0: String(recalculated.k0),
        projection_source: 'manual',
        rtcm1025_used: false,
        zone_width_deg: Math.abs(Number(recalculated.k0) - 0.9999) <= 1e-9 ? 3 : 6,
    };
}

function getAutoBaseProvinceLabel(conversionReport, provinceLookup, locationKey, resolvedLocation) {
    if (conversionReport.location?.province_name || conversionReport.province_name)
        return conversionReport.location?.province_name || conversionReport.province_name;
    if (provinceLookup?.key === locationKey && provinceLookup.state === 'loading') return 'Đang tra tỉnh/thành từ tọa độ…';
    if (provinceLookup?.key === locationKey && provinceLookup.error) return provinceLookup.error;
    if (resolvedLocation?.state === 'outside_boundaries') return 'Tọa độ nằm ngoài vùng dữ liệu tỉnh/thành';
    return 'Chưa có kết quả tra tỉnh/thành';
}

export function useAutoBaseReviewState(device, autoProgress, autoFixedResult) {
    const reportedConversion = autoProgress.conversion_report || device.base_config?.conversion_report || {};
    const locationInput = reportedConversion.input_coord || autoProgress.raw_coord;
    const locationKey = `${device.serial}:${['lat','lon','alt'].map(key => locationInput?.[key] ?? '').join(':')}`;
    const reportedProvince = reportedConversion.location?.province_name || reportedConversion.province_name;
    const provinceLookup = useAutoBaseProvinceLookup(locationInput, locationKey, autoFixedResult, reportedProvince);
    const resolvedLocation = provinceLookup?.key === locationKey ? provinceLookup.location : null;
    const conversionReport = resolvedLocation ? {...reportedConversion, location:resolvedLocation,
        province_code:resolvedLocation.province_code, province_name:resolvedLocation.province_name} : reportedConversion;
    const reviewKey = JSON.stringify([device.serial, autoProgress.phase, autoProgress.updated_at,
        autoProgress.averaged_coord, reportedConversion]);
    const [projectionReview, setProjectionReview] = useState(null);
    const currentReview = projectionReview?.key === reviewKey ? projectionReview : null;
    const recalculated = currentReview?.result;
    const reviewCoords = recalculated?.calculated_coord || autoProgress.averaged_coord;
    const reviewReport = buildAutoBaseReviewReport(conversionReport, recalculated);
    const provinceLabel = getAutoBaseProvinceLabel(conversionReport, provinceLookup, locationKey, resolvedLocation);
    return {conversionReport, reviewKey, projectionReview, setProjectionReview, currentReview,
        reviewCoords, reviewReport, provinceLabel};
}

export function getBaseReceiverInfo(device, currentUser, isReadingReceiver) {
    const isAdmin = String(currentUser?.role || '').toLowerCase() === 'admin';
    const receiverReadback = device.system_info?.receiver_base_readback || {};
    const receiverPosition = receiverReadback.bestpos || {};
    const baseStartup = device.system_info?.base_startup || {};
    const receiverLabel = device.detected_chip_type === 'Ublox' ? 'u-blox' : 'UM';
    const supportsReceiverReadback = ['Ublox', 'Unicorecomm'].includes(device.detected_chip_type);
    const receiverOffline = device.status === 'offline'
        || !!window.CorsDashboardShared?.isDeviceStaleOffline?.(device);
    const surveyStatus = device.system_info?.survey_in_status || {};
    const surveyState = receiverOffline ? 'offline' : surveyStatus.state;
    const surveyElapsedSeconds = surveyStatus.source === 'NAV-SVIN'
        ? Number(surveyStatus.elapsed_seconds) + Number(surveyStatus.readback_age_seconds ?? receiverReadback.age_seconds ?? 0)
        : Number(surveyStatus.elapsed_since_ack_seconds);
    const statusReceivedAt = Number(device.timestamp);
    const surveyStartedAt = Number.isFinite(statusReceivedAt) && Number.isFinite(surveyElapsedSeconds)
        && statusReceivedAt > surveyElapsedSeconds
        ? new Date((statusReceivedAt - surveyElapsedSeconds) * 1000).toLocaleString('vi-VN', {
            timeZone: 'Asia/Ho_Chi_Minh', hour12: false
        })
        : '';
    const savedMethod = String(device.base_config?.base_setup_method || '').toUpperCase();
    let expectedReceiverMode = '';
    if (savedMethod === 'SURVEY_IN') expectedReceiverMode = 'survey_in';
    else if (['FIXED_LLA', 'AUTO_CORS'].includes(savedMethod)) expectedReceiverMode = 'base';
    const receiverReadbackIsFresh = receiverReadback.state === 'ok'
        && Number(receiverReadback.age_seconds) <= 60
        && !receiverOffline;
    const receiverCoordinates = window.CorsReceiverCoordinates?.fromReceiver(receiverPosition);
    const fixedCoordinateComparison = receiverReadbackIsFresh && receiverReadback.mode_kind === 'base'
        ? window.CorsReceiverCoordinates?.compareFixed(receiverCoordinates, device.base_config, device.detected_chip_type)
        : null;
    const receiverInstantCoordinates = window.CorsReceiverCoordinates?.fromReceiver(receiverPosition, true);
    const canUseReceiverCoordinates = !!receiverCoordinates && receiverReadbackIsFresh
        && !isReadingReceiver;
    const receiverModeMismatch = receiverReadbackIsFresh && expectedReceiverMode
        && ['survey_in', 'base', 'rover'].includes(receiverReadback.mode_kind)
        && receiverReadback.mode_kind !== expectedReceiverMode;
    const savedMethodLabel = ({
        FIXED_LLA: 'Fixed LLA', SURVEY_IN: 'Survey-In', AUTO_CORS: 'Auto Base'
    })[savedMethod] || 'Chưa có cấu hình Base đã lưu';
    return {isAdmin, receiverReadback, receiverPosition, baseStartup, receiverLabel, supportsReceiverReadback,
        receiverOffline, surveyStatus, surveyState, surveyStartedAt, surveyElapsedSeconds, savedMethod,
        expectedReceiverMode, receiverReadbackIsFresh, receiverCoordinates, fixedCoordinateComparison,
        receiverInstantCoordinates, canUseReceiverCoordinates, receiverModeMismatch, savedMethodLabel};
}
