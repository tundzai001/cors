import { GnssConfigVerification, GnssOutputPolicyPanel } from './gnss-configuration.js';
import { getBaseChipSelection, getAutoBaseProgressSummary, useAutoBaseReviewState, getBaseReceiverInfo } from './base-form-state.js';

import { chipCoordinate, formatApprovedAltitude } from './base-configuration-coordinate-utils.js';
import {
    autoBaseDrafts, buildAutoCorsPayload, buildBaseMethodParams, buildGnssOptions,
    getAutoBaseResultText, getBaseSubmitLabel, getLteConstellationError,
    hasValidApprovedBaseCoordinates, resetBaseFormValues, restoreBaseConfig, restoreBaseFormDraft,
} from './base-configuration-state.js';
import { BaseReceiverStatusCard } from './base-configuration-status.js';
import { AutoBaseReviewPanel, FixedLlaCoordinateFields } from './base-configuration-review.js';
import { AutoCorsConfigFields, BaseCoordinateMethodFields, SurveyInFields } from './base-configuration-fields.js';
import { AutoBaseMonitor } from './base-configuration-monitor.js';
import { Vn2000Result } from './base-configuration-projection.js';

(() => {
const { useState, useEffect, memo, useRef } = React;

const BaseConfigForm = memo(function BaseConfigForm({ device, currentUser = {}, onConfigureChip, onStopAutoBase, onReadBaseStatus, disabled }) {
    const [manualChip, setManualChip] = useState(device.detected_chip_type === 'RTCM3_Source' ? 'Unicorecomm' : 'Ublox');
    const [method, setMethod] = useState('AUTO_CORS');
    const {needsManualSelection, activeChip} = getBaseChipSelection(device, manualChip);
    const isLteStation = !!device?.is_lte || String(device?.connection_type || '').toUpperCase() === 'LTE';

    const ACTIVE_AUTO_BASE_PHASES = ['queued', 'awaiting_source', 'starting', 'set_rover', 'wait_fix', 'averaging', 'resolving_location', 'set_base'];
    const isAutoSetupStatus = (device?.status || '').startsWith('auto_setup');
    const autoProgress = device?.auto_base_progress || {};
    const isAutoSetupActive = isAutoSetupStatus || ACTIVE_AUTO_BASE_PHASES.includes(autoProgress.phase);
    const [isStoppingAutoBase, setIsStoppingAutoBase] = useState(false);
    useEffect(() => {
        if (!isAutoSetupActive) setIsStoppingAutoBase(false);
    }, [isAutoSetupActive]);
    useEffect(() => { setIsStoppingAutoBase(false); }, [device.serial]);
    useEffect(() => {
        if (!isStoppingAutoBase) return;
        const timer = setTimeout(() => {
            setIsStoppingAutoBase(false);
            window.showError?.('Chưa nhận được xác nhận dừng Auto Base. Kiểm tra kết nối rồi thử lại.');
        }, 45000);
        return () => clearTimeout(timer);
    }, [isStoppingAutoBase]);
    async function stopAutoBase() {
        if (disabled || isStoppingAutoBase || !onStopAutoBase) return;
        const ok = await window.CorsUI?.confirmAction?.({
            title: 'Dừng Auto Base',
            message: 'Dừng lấy mẫu và khôi phục cấu hình Base trước phiên này?',
            confirmText: 'Dừng Auto Base', confirmVariant: 'danger'
        });
        if (!ok) return;
        setIsStoppingAutoBase(true);
        try { await onStopAutoBase(device.serial); }
        catch { setIsStoppingAutoBase(false); }
    }

    const autoSummary = getAutoBaseProgressSummary(device, autoProgress);
    const {autoSamplesCollected, autoSamplesTarget, autoProgressPercent, rawGgaSentence,
        ggaAge, ggaFix, rtcmBytesReceived, rtcmPktsReceived, fixedStreak, fixedStreakReq,
        autoFixedResult} = autoSummary;
    const {conversionReport, reviewKey, setProjectionReview, currentReview, reviewCoords,
        reviewReport, provinceLabel} = useAutoBaseReviewState(device, autoProgress, autoFixedResult);
    const supportsAutoBaseReview = device.system_info?.auto_base_review_supported === true;
    const supportsAutoBaseNearest = device.system_info?.auto_base_nearest_supported === true;
    const autoResultText = getAutoBaseResultText(autoFixedResult, autoProgress);

    // Tọa độ Fixed
    const [latitude, setLatitude] = useState('');
    const [longitude, setLongitude] = useState('');
    const [altitude, setAltitude] = useState('');
    const [coordinatePasteNotice, setCoordinatePasteNotice] = useState(null);
    const [altitudeReference, setAltitudeReference] = useState('ELLIPSOID');
    const [fixedAccuracyM, setFixedAccuracyM] = useState('0.01'); // Meters, matching the API and receiver configuration.

    // CORS connection for Auto Base (per-station tab memory; never read from .env)
    const [corsHost, setCorsHost] = useState('');
    const [corsSourceMode, setCorsSourceMode] = useState('nearest');
    const [projectionMeridian, setProjectionMeridian] = useState('');
    const [projectionScale, setProjectionScale] = useState('');
    const [corsPort, setCorsPort] = useState('2101');
    const [corsUsername, setCorsUsername] = useState('');
    const [corsPassword, setCorsPassword] = useState('');
    const [corsSources, setCorsSources] = useState([]);
    const [selectedMountpoint, setSelectedMountpoint] = useState('');
    const [isLoadingCorsSources, setIsLoadingCorsSources] = useState(false);
    const [corsLookupError, setCorsLookupError] = useState('');
    const draftOwnerRef = useRef({serial:null, savedMethod:null});
    const skipDraftSaveRef = useRef(false);
    const corsRequestRef = useRef(0);

    // Survey-In Params
    const [svinDuration, setSvinDuration] = useState('300'); // Giây
    const [svinAccuracyM, setSvinAccuracyM] = useState('3.00'); // Đơn vị Mét cho Survey-In (Mặc định 3m)

    const [interferenceDetectionEnabled, setInterferenceDetectionEnabled] = useState(true);
    const [jammingSensitivityHigh, setJammingSensitivityHigh] = useState(true);
    const [outputMode, setOutputMode] = useState(isLteStation ? 'rtcm_only' : 'diagnostics_rtcm');
    const [msmLevel, setMsmLevel] = useState(isLteStation ? 'both' : '4');
    const [constellations, setConstellations] = useState({
        gps: true,
        glo: true,
        gal: true,
        bds: true,
        qzss: true,
        sbas: true
    });
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [isReadingReceiver, setIsReadingReceiver] = useState(false);
    const [pendingReadbackRevision, setPendingReadbackRevision] = useState(null);
    const readbackTimeoutRef = useRef(null);
    const pendingReadbackRevisionRef = useRef(null);
    const receiverInfo = getBaseReceiverInfo(device, currentUser, isReadingReceiver);
    const {isAdmin} = receiverInfo;
    const itfmUnsupported = device.gnss_receiver?.itfm_supported === false;
    const [receiverCopyNotice, setReceiverCopyNotice] = useState('');
    const {receiverReadback, receiverPosition, baseStartup, receiverLabel, supportsReceiverReadback,
        receiverOffline, surveyStatus, surveyState, surveyStartedAt, surveyElapsedSeconds, savedMethod,
        expectedReceiverMode, receiverReadbackIsFresh, receiverCoordinates, fixedCoordinateComparison,
        receiverInstantCoordinates, canUseReceiverCoordinates, receiverModeMismatch, savedMethodLabel} = receiverInfo;
    useEffect(() => setReceiverCopyNotice(''), [device.serial, receiverReadback.revision]);

    const handleReadReceiver = async () => {
        if (!onReadBaseStatus || isReadingReceiver) return;
        const startingRevision = Number(receiverReadback.revision || 0);
        setIsReadingReceiver(true);
        pendingReadbackRevisionRef.current = startingRevision;
        setPendingReadbackRevision(startingRevision);
        try {
            await onReadBaseStatus(device.serial);
            if (readbackTimeoutRef.current) window.clearTimeout(readbackTimeoutRef.current);
            readbackTimeoutRef.current = window.setTimeout(() => {
                if (pendingReadbackRevisionRef.current !== startingRevision) return;
                pendingReadbackRevisionRef.current = null;
                setPendingReadbackRevision(null);
                setIsReadingReceiver(false);
            }, 15000);
        } catch (error) {
            pendingReadbackRevisionRef.current = null;
            setPendingReadbackRevision(null);
            setIsReadingReceiver(false);
            window.showError(`Không đọc được trạng thái ${receiverLabel}: ${error.message}`);
        }
    };

    useEffect(() => {
        if (pendingReadbackRevisionRef.current === null
            || Number(receiverReadback.revision || 0) === pendingReadbackRevisionRef.current) return;
        pendingReadbackRevisionRef.current = null;
        if (readbackTimeoutRef.current) window.clearTimeout(readbackTimeoutRef.current);
        setPendingReadbackRevision(null);
        setIsReadingReceiver(false);
    }, [receiverReadback.revision]);

    useEffect(() => () => {
        pendingReadbackRevisionRef.current = null;
        if (readbackTimeoutRef.current) window.clearTimeout(readbackTimeoutRef.current);
    }, []);


    useEffect(() => {
        const setters = {
            setCorsHost, setCorsSourceMode, setCorsPort, setCorsUsername, setCorsPassword,
            setCorsSources, setSelectedMountpoint, setProjectionMeridian, setProjectionScale,
            setCorsLookupError, setIsLoadingCorsSources, setMethod, setManualChip, setLatitude,
            setLongitude, setAltitude, setCoordinatePasteNotice, setAltitudeReference,
            setFixedAccuracyM, setSvinDuration, setSvinAccuracyM, setInterferenceDetectionEnabled,
            setJammingSensitivityHigh, setOutputMode, setMsmLevel, setConstellations,
        };
        restoreBaseFormDraft({ device, isAutoSetupActive, draftOwnerRef, skipDraftSaveRef, corsRequestRef, setters });
        resetBaseFormValues(setters, device, isLteStation);
        restoreBaseConfig(device.base_config, activeChip, device.detected_chip_type, setters);
    }, [device.serial, device.detected_chip_type, device.base_config?.base_setup_method, device.system_info?.base_startup?.state, isAutoSetupActive]);

    useEffect(() => {
        if (skipDraftSaveRef.current) { skipDraftSaveRef.current = false; return; }
        autoBaseDrafts.set(device.serial, {method, sourceMode:corsSourceMode, host:corsHost, port:corsPort,
            username:corsUsername, password:corsPassword, sources:corsSources,
            mountpoint:selectedMountpoint, meridian:projectionMeridian, scale:projectionScale});
    }, [device.serial, method, corsSourceMode, corsHost, corsPort, corsUsername, corsPassword,
        corsSources, selectedMountpoint, projectionMeridian, projectionScale]);
    useEffect(() => () => { ++corsRequestRef.current; }, [device.serial]);

    const handleDecimalInputChange = (setter, maxDecimals) => (e) => {
        const value = e.target.value.replace(',', '.');
        if (maxDecimals !== undefined && (value.split('.')[1] || '').length > maxDecimals) return;
        // Cho phép nhập số thập phân âm/dương
        if (/^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]*)?$/.test(value)) {
            setter(value);
            setCoordinatePasteNotice(null);
        }
    };

    const formatCoord10 = (val) => {
        if (val === null || val === undefined || val === '') return '';
        return chipCoordinate(val, activeChip === 'Ublox' ? 9 : 10);
    };

    const formatAlt10 = (val) => {
        if (val === null || val === undefined || val === '') return '';
        if (activeChip === 'Ublox') return chipCoordinate(val, 4);
        return String(val);
    };

    const handleCoordinatePaste = (event) => {
        if (disabled || isSubmitting) return;
        try {
            const coords = window.CorsCoordinateClipboard?.parse(event.clipboardData?.getData('text/plain') || '');
            if (!coords) return; // A normal numeric paste still belongs to the focused field.
            event.preventDefault();
            setLatitude(chipCoordinate(coords.lat, activeChip === 'Ublox' ? 9 : 10));
            setLongitude(chipCoordinate(coords.lon, activeChip === 'Ublox' ? 9 : 10));
            setAltitude(activeChip === 'Ublox' ? chipCoordinate(coords.alt, 4) : coords.alt);
            setAltitudeReference('ELLIPSOID');
            setCoordinatePasteNotice({ error: false, message: 'Đã điền vĩ độ, kinh độ WGS 84 và độ cao h. Kiểm tra tọa độ rồi bấm áp dụng.' });
        } catch (error) {
            event.preventDefault();
            setCoordinatePasteNotice({ error: true, message: error.message });
        }
    };

    const handleUseReceiverCoordinates = () => {
        if (!canUseReceiverCoordinates || disabled || isSubmitting) return;
        setMethod('FIXED_LLA');
        setLatitude(chipCoordinate(receiverCoordinates.lat, activeChip === 'Ublox' ? 9 : 10));
        setLongitude(chipCoordinate(receiverCoordinates.lon, activeChip === 'Ublox' ? 9 : 10));
        setAltitude(receiverCoordinates.alt);
        setAltitudeReference('ELLIPSOID');
        setCoordinatePasteNotice({ error: false,
            message: `Đã điền tọa độ Base từ ${receiverLabel}. Kiểm tra tọa độ và sai số trước khi bấm áp dụng.` });
        window.requestAnimationFrame(() => document.getElementById(`base-latitude-${device.serial}`)?.focus());
    };

    const handleCopyReceiverCoordinates = async () => {
        if (!canUseReceiverCoordinates) return;
        setReceiverCopyNotice('');
        try {
            await navigator.clipboard.writeText(window.CorsCoordinateClipboard.encode(receiverCoordinates));
            setReceiverCopyNotice('Đã sao chép WGS 84 và độ cao h; có thể dán vào công cụ kiểm tra hoặc Fixed LLA.');
        } catch (_) {
            setReceiverCopyNotice('Không sao chép được. Hãy chọn và sao chép các giá trị bên trên.');
        }
    };

    const handleCorsFieldChange = (setter) => (e) => {
        ++corsRequestRef.current;
        setIsLoadingCorsSources(false);
        setter(e.target.value);
        setCorsSources([]);
        setSelectedMountpoint('');
        setCorsLookupError('');
    };

    const handleLoadCorsMountpoints = async () => {
        const host = corsHost.trim();
        const port = Number(corsPort);
        const username = corsUsername.trim();
        if (!host || !Number.isInteger(port) || port < 1 || port > 65535 || !username || !corsPassword) {
            setCorsLookupError('Vui lòng nhập đủ IP/Host, Port, Username và Password của Base.');
            return;
        }

        const request = ++corsRequestRef.current;
        setIsLoadingCorsSources(true);
        setCorsLookupError('');
        try {
            const response = await window.apiFetch(window.ApiRoutes.devices.corsMountpoints(device.serial), {
                method: 'POST',
                body: JSON.stringify({ host, port, username, password: corsPassword })
            });
            const data = await response.json().catch(() => ({}));
            if (request !== corsRequestRef.current) return;
            if (!response.ok) {
                throw new Error(data.detail || 'Không thể đọc danh sách mountpoint từ Base.');
            }

            const sources = Array.isArray(data.sources)
                ? data.sources.filter(source => source && String(source.mountpoint || '').trim())
                : [];
            setCorsSources(sources);
            setSelectedMountpoint(current => (
                sources.some(source => source.mountpoint === current)
                    ? current
                    : (sources[0]?.mountpoint || '')
            ));
            if (!sources.length) {
                setCorsLookupError('Base đã kết nối nhưng chưa trả về mountpoint nào.');
            } else {
                window.showSuccess(`Đã tải ${sources.length} mountpoint từ Base.`);
            }
        } catch (error) {
            if (request !== corsRequestRef.current) return;
            setCorsSources([]);
            setSelectedMountpoint('');
            setCorsLookupError(error.message);
        } finally {
            if (request === corsRequestRef.current) setIsLoadingCorsSources(false);
        }
    };

    const handleSubmit = async (e, approvedCoords = null) => {
        e.preventDefault();
        if (disabled || (approvedCoords && (isAutoSetupActive || !autoFixedResult || currentReview?.pending))) return;
        if (approvedCoords && !hasValidApprovedBaseCoordinates(approvedCoords)) {
            window.showError('Kết quả Auto Base thiếu LLH hợp lệ. Cần lấy lại tọa độ.');
            return;
        }
        const submittedMethod = approvedCoords ? 'FIXED_LLA' : method;
        setIsSubmitting(true);

        try {
            const finalChipType = needsManualSelection
                ? manualChip
                : device.detected_chip_type;

            const methodParams = buildBaseMethodParams({
                submittedMethod, approvedCoords, latitude, longitude, altitude, finalChipType, altitudeReference,
                autoProgress, autoFixedResult, reviewReport, conversionReport, fixedAccuracyM, svinDuration,
                svinAccuracyM, corsSourceMode, supportsAutoBaseNearest, corsHost, corsPort, corsUsername,
                corsPassword, selectedMountpoint, projectionMeridian, projectionScale,
            });
            if (methodParams.error) {
                window.showError(methodParams.error);
                return;
            }
            const params = methodParams.params;
            if (methodParams.coordinates && finalChipType === 'Ublox') {
                setLatitude(params.coords.lat);
                setLongitude(params.coords.lon);
                setAltitude(params.coords.alt);
            }
            const constellationError = getLteConstellationError(isLteStation, constellations);
            if (constellationError) {
                window.showError(constellationError);
                return;
            }
            const existingGnss = (device.base_config && typeof device.base_config.gnss_options === 'object')
                ? device.base_config.gnss_options
                : null;

            params.gnss_options = buildGnssOptions({
                isAdmin, existingGnss, activeChip, interferenceDetectionEnabled, jammingSensitivityHigh,
                outputMode, msmLevel, constellations, isLteStation,
            });

            const payload = {
                sensor_type: finalChipType,
                mode: 'BASE',
                params: params
            };

            if (submittedMethod === 'AUTO_CORS') {
                const autoCors = buildAutoCorsPayload({
                    corsSourceMode, corsHost, corsPort, corsUsername, corsPassword, selectedMountpoint,
                    projectionMeridian, projectionScale,
                });
                if (autoCors.error) {
                    window.showError(autoCors.error);
                    return;
                }
                payload.auto_cors = autoCors.payload;
                delete params.auto_cors;
            }

            await onConfigureChip(device.serial, payload);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <form onSubmit={handleSubmit}>
            <div className="alert alert-info small">
                <i className="bi bi-info-circle me-2"></i>
                Đây là bước cấu hình tọa độ gốc cho trạm phát. Các thay đổi sẽ reset chip GNSS.
            </div>

            {disabled && (
                <div className="alert alert-warning py-2 mb-3">
                    <i className="bi bi-eye-fill me-2"></i>
                    Chế độ xem: Không thể chỉnh sửa cấu hình GNSS.
                </div>
            )}

            {needsManualSelection && (
                <div className="mb-3 p-3 border border-warning rounded">
                    <div className="alert alert-warning small p-2">
                        <i className="bi bi-exclamation-triangle-fill me-2"></i>
                        {device.detected_chip_type === 'RTCM3_Source' ? (
                            <span>Chip nhận dạng là <strong>RTCM3_Source</strong> (thường là chip Unicorecomm/UM982 trả về mã chung). Vui lòng xác nhận loại chip để cấu hình chính xác.</span>
                        ) : (
                            <span>Không thể nhận diện loại chip (hiện tại là: <strong>{device.detected_chip_type}</strong>). Vui lòng chọn thủ công.</span>
                        )}
                    </div>
                    <label htmlFor="base-manual-chip" className="form-label fw-bold">Xác nhận Loại Chip</label>
                    <select id="base-manual-chip"
                        className="form-select"
                        value={manualChip}
                        onChange={(e) => setManualChip(e.target.value)}
                        disabled={disabled}
                    >
                        <option value="Ublox">Ublox</option>
                        <option value="Unicorecomm">Unicorecomm (Mặc định cho UM982 / RTCM3)</option>
                    </select>
                </div>
            )}

            <AutoBaseMonitor
                device={device}
                isAutoSetupStatus={isAutoSetupStatus}
                isAutoSetupActive={isAutoSetupActive}
                onStopAutoBase={onStopAutoBase}
                isStoppingAutoBase={isStoppingAutoBase}
                stopAutoBase={stopAutoBase}
                disabled={disabled}
                method={method}
                autoProgress={autoProgress}
                autoResultText={autoResultText}
                autoFixedResult={autoFixedResult}
                autoSamplesTarget={autoSamplesTarget}
                autoSamplesCollected={autoSamplesCollected}
                rawGgaSentence={rawGgaSentence}
                rtcmBytesReceived={rtcmBytesReceived}
                rtcmPktsReceived={rtcmPktsReceived}
                fixedStreak={fixedStreak}
                fixedStreakReq={fixedStreakReq}
                autoProgressPercent={autoProgressPercent}
                ggaFix={ggaFix}
                ggaAge={ggaAge}
                formatCoord10={formatCoord10}
            />

            {method !== 'AUTO_CORS' && isAutoSetupActive && (
                <div className="alert alert-warning small py-2 mb-3">
                    <i className="bi bi-exclamation-triangle-fill me-2"></i>
                    Trạm đang trong tiến trình Auto Base (Rover). Bạn có thể bấm <strong>"Dừng Auto Base"</strong> ở trên hoặc điền tọa độ và bấm <strong>"Áp dụng cấu hình BASE"</strong> bên dưới để dừng Auto Base và lưu tọa độ cố định.
                </div>
            )}

            <BaseReceiverStatusCard
                baseStartup={baseStartup}
                receiverOffline={receiverOffline}
                savedMethodLabel={savedMethodLabel}
                disabled={disabled}
                isReadingReceiver={isReadingReceiver}
                supportsReceiverReadback={supportsReceiverReadback}
                handleReadReceiver={handleReadReceiver}
                receiverReadback={receiverReadback}
                receiverLabel={receiverLabel}
                receiverModeMismatch={receiverModeMismatch}
                receiverReadbackIsFresh={receiverReadbackIsFresh}
                expectedReceiverMode={expectedReceiverMode}
                receiverCoordinates={receiverCoordinates}
                savedMethod={savedMethod}
                formatCoord10={formatCoord10}
                canUseReceiverCoordinates={canUseReceiverCoordinates}
                handleCopyReceiverCoordinates={handleCopyReceiverCoordinates}
                isSubmitting={isSubmitting}
                handleUseReceiverCoordinates={handleUseReceiverCoordinates}
                receiverCopyNotice={receiverCopyNotice}
                receiverInstantCoordinates={receiverInstantCoordinates}
                receiverPosition={receiverPosition}
                fixedCoordinateComparison={fixedCoordinateComparison}
                device={device}
                pendingReadbackRevision={pendingReadbackRevision}
                surveyStatus={surveyStatus}
                surveyState={surveyState}
                surveyStartedAt={surveyStartedAt}
                surveyElapsedSeconds={surveyElapsedSeconds}
            />

            <AutoBaseReviewPanel
                autoFixedResult={autoFixedResult}
                autoProgress={autoProgress}
                conversionReport={conversionReport}
                provinceLabel={provinceLabel}
                reviewReport={reviewReport}
                reviewKey={reviewKey}
                reviewCoords={reviewCoords}
                currentReview={currentReview}
                activeChip={activeChip}
                formatCoord10={formatCoord10}
                formatApprovedAltitude={formatApprovedAltitude}
                setProjectionReview={setProjectionReview}
                disabled={disabled}
                isSubmitting={isSubmitting}
                isAutoSetupActive={isAutoSetupActive}
                handleSubmit={handleSubmit}
            />

            {method !== 'AUTO_CORS' && !autoFixedResult && <Vn2000Result report={conversionReport} />}
            <div className="mb-3">
                {device.system_info?.survey_in_result?.bestpos && savedMethod !== 'SURVEY_IN' && (() => {
                    const saved = window.CorsReceiverCoordinates?.fromReceiver(device.system_info.survey_in_result.bestpos);
                    if (!saved) return null;
                    return <details className="border rounded p-3 mb-3">
                        <summary className="small fw-bold py-2">Kết quả Survey-In đã lưu</summary>
                        <div className="small text-muted mt-1">Kết quả trước khi chuyển cấu hình, dùng để đối chiếu với Fixed LLA hiện tại.</div>
                        <div className="small font-monospace text-break mt-2">WGS 84: {saved.lat}, {saved.lon} · h {saved.alt} m</div>
                    </details>;
                })()}
                <label htmlFor={`base-method-${device.serial}`} className="form-label fw-bold text-primary">Phương pháp xác định vị trí</label>
                <select
                    id={`base-method-${device.serial}`}
                    className="form-select border-primary"
                    value={method}
                    onChange={(e) => setMethod(e.target.value)}
                    disabled={disabled}
                >
                    <option value="AUTO_CORS">Tự lấy tọa độ từ CORS gần nhất</option>
                    <option value="SURVEY_IN">Tự động khảo sát (Survey-In - Độ chính xác thấp)</option>
                    <option value="FIXED_LLA">Nhập tọa độ thủ công (Fixed Mode - Độ chính xác cao)</option>
                </select>
            </div>

            <fieldset disabled={disabled}>
                {method === 'AUTO_CORS' ? (
                    <AutoCorsConfigFields
                        device={device}
                        supportsAutoBaseReview={supportsAutoBaseReview}
                        supportsAutoBaseNearest={supportsAutoBaseNearest}
                        corsSourceMode={corsSourceMode}
                        setCorsSourceMode={setCorsSourceMode}
                        disabled={disabled}
                        isAutoSetupActive={isAutoSetupActive}
                        autoFixedResult={autoFixedResult}
                        corsHost={corsHost}
                        setCorsHost={setCorsHost}
                        corsPort={corsPort}
                        setCorsPort={setCorsPort}
                        corsUsername={corsUsername}
                        setCorsUsername={setCorsUsername}
                        corsPassword={corsPassword}
                        setCorsPassword={setCorsPassword}
                        handleCorsFieldChange={handleCorsFieldChange}
                        handleLoadCorsMountpoints={handleLoadCorsMountpoints}
                        isLoadingCorsSources={isLoadingCorsSources}
                        isSubmitting={isSubmitting}
                        corsLookupError={corsLookupError}
                        corsSources={corsSources}
                        selectedMountpoint={selectedMountpoint}
                        setSelectedMountpoint={setSelectedMountpoint}
                        projectionMeridian={projectionMeridian}
                        setProjectionMeridian={setProjectionMeridian}
                        projectionScale={projectionScale}
                        setProjectionScale={setProjectionScale}
                        isAutoSetupStatus={isAutoSetupStatus}
                    />
                ) : (
                    <BaseCoordinateMethodFields
                        method={method}
                        device={device}
                        coordinatePasteNotice={coordinatePasteNotice}
                        latitude={latitude}
                        setLatitude={setLatitude}
                        longitude={longitude}
                        setLongitude={setLongitude}
                        handleDecimalInputChange={handleDecimalInputChange}
                        activeChip={activeChip}
                        handleCoordinatePaste={handleCoordinatePaste}
                        disabled={disabled}
                        isSubmitting={isSubmitting}
                        altitude={altitude}
                        setAltitude={setAltitude}
                        altitudeReference={altitudeReference}
                        setAltitudeReference={setAltitudeReference}
                        setCoordinatePasteNotice={setCoordinatePasteNotice}
                        fixedAccuracyM={fixedAccuracyM}
                        setFixedAccuracyM={setFixedAccuracyM}
                        svinDuration={svinDuration}
                        setSvinDuration={setSvinDuration}
                        svinAccuracyM={svinAccuracyM}
                        setSvinAccuracyM={setSvinAccuracyM}
                    />
                )}

                <GnssOutputPolicyPanel
                    device={device}
                    isAdmin={isAdmin}
                    isSubmitting={isSubmitting}
                    isLteStation={isLteStation}
                    outputMode={outputMode}
                    setOutputMode={setOutputMode}
                    msmLevel={msmLevel}
                    setMsmLevel={setMsmLevel}
                    activeChip={activeChip}
                    itfmUnsupported={itfmUnsupported}
                    jammingSensitivityHigh={jammingSensitivityHigh}
                    setJammingSensitivityHigh={setJammingSensitivityHigh}
                    interferenceDetectionEnabled={interferenceDetectionEnabled}
                    setInterferenceDetectionEnabled={setInterferenceDetectionEnabled}
                    constellations={constellations}
                    setConstellations={setConstellations}
                />

                {!disabled && isAutoSetupActive && onStopAutoBase && (
                    <button type="button" className="btn btn-outline-danger mt-4 w-100" style={{minHeight:44}}
                        disabled={isStoppingAutoBase} onClick={stopAutoBase}>
                        {isStoppingAutoBase ? <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" /> : <i className="bi bi-stop-circle-fill me-2" aria-hidden="true" />}
                        {isStoppingAutoBase ? 'Đang dừng…' : 'Dừng Auto Base'}
                    </button>
                )}
                {!disabled && (
                    <button
                        type="submit"
                        className="btn btn-primary mt-4 w-100 base-config-submit"
                        disabled={isSubmitting || isAutoSetupActive || isLoadingCorsSources || (method === 'AUTO_CORS' && (!supportsAutoBaseReview || (corsSourceMode === 'nearest' ? !supportsAutoBaseNearest : !selectedMountpoint)))}
                    >
                        {isSubmitting ? (
                            <>
                                <span className="spinner-border spinner-border-sm me-2"></span>
                                {method === 'AUTO_CORS' ? 'Đang gửi yêu cầu lấy tọa độ…' : 'Đang gửi...'}
                            </>
                        ) : (
                            <>
                                <i className="bi bi-lightning-charge-fill me-2"></i>
                                {getBaseSubmitLabel(method, isAutoSetupActive, corsSourceMode, autoFixedResult)}
                            </>
                        )}
                    </button>
                )}
            </fieldset>
        </form>
    );
});


window.BaseConfigForm = BaseConfigForm;
})();
