const { useEffect } = React;

const DEFAULT_SERVICE_FORM_STATE = {
    ncomport: '',
    reconnectioninterval: 10,
    stream_on_demand: true,
    stream_active: false,
    server1_enabled: false,
    server1_stream_on_demand: true,
    server1_stream_active: false,
    ntrip_version1: 1,
    username1: '',
    serverhost1: '',
    port1: '',
    mountpoint1: '',
    password1: '',
    server2_enabled: false,
    server2_stream_on_demand: true,
    server2_stream_active: false,
    ntrip_version2: 1,
    username2: '',
    serverhost2: '',
    port2: '',
    mountpoint2: '',
    password2: '',
    rtcm_enabled: false,
    rtcmntripversion1: 1,
    rtcmserver1: '',
    rtcmport1: '',
    rtcmmountpoint1: '',
    rtcmusername1: '',
    rtcmpassword1: '',
    command_host: '',
    command_port: '',
    command_pass: '',
    command_mp: '',
};

export const createDefaultServiceFormState = () => ({...DEFAULT_SERVICE_FORM_STATE});

export function getCasterIds(config) {
    return [...new Set([1, 2, ...Object.keys(config).map(key => {
        const match = key.match(/^server(\d+)_enabled$|^serverhost(\d+)$/);
        return match ? Number(match[1] || match[2]) : 0;
    }).filter(id => Number.isSafeInteger(id) && id > 0)])].sort((a, b) => a - b);
}

export function toBool(value, defaultValue = false) {
    if (value === null || value === undefined) return defaultValue;
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
        if (['false', '0', 'no', 'off'].includes(normalized)) return false;
    }
    return Boolean(value);
}

function resolveServicePassword(isAdmin, receivedConfig, previousConfig, key, isSerialChanged) {
    if (!isAdmin) return '';
    const receivedPassword = receivedConfig[key];
    if (receivedPassword != null) return receivedPassword;
    return isSerialChanged ? '' : previousConfig[key] ?? '';
}

function normalizeServerSettings(merged, receivedConfig, serverId, isAdmin, isSerialChanged, previousConfig) {
    const key = `server${serverId}`;
    merged[`password${serverId}`] = resolveServicePassword(
        isAdmin, receivedConfig, previousConfig, `password${serverId}`, isSerialChanged,
    );
    merged[`${key}_enabled`] = toBool(receivedConfig[`${key}_enabled`], false);
    merged[`${key}_stream_on_demand`] = toBool(
        receivedConfig[`${key}_stream_on_demand`], toBool(receivedConfig.stream_on_demand, true),
    );
    merged[`${key}_stream_active`] = toBool(
        receivedConfig[`${key}_stream_active`], toBool(receivedConfig.stream_active, false),
    );
    if (!merged[`${key}_stream_on_demand`]) merged[`${key}_stream_active`] = true;
}

function buildHydratedServiceState(deviceConfig, hostPolicy, isAdmin, isSerialChanged, previousConfig) {
    const defaults = createDefaultServiceFormState();
    if (!deviceConfig) return defaults;
    const merged = {
        ...defaults,
        ...deviceConfig,
        password1: resolveServicePassword(isAdmin, deviceConfig, previousConfig, 'password1', isSerialChanged),
        password2: resolveServicePassword(isAdmin, deviceConfig, previousConfig, 'password2', isSerialChanged),
        rtcmpassword1: resolveServicePassword(isAdmin, deviceConfig, previousConfig, 'rtcmpassword1', isSerialChanged),
        command_pass: resolveServicePassword(isAdmin, deviceConfig, previousConfig, 'command_pass', isSerialChanged),
    };
    for (const serverId of getCasterIds(merged)) {
        normalizeServerSettings(merged, deviceConfig, serverId, isAdmin, isSerialChanged, previousConfig);
    }
    Object.assign(merged, Object.fromEntries(Object.entries(hostPolicy).filter(([key]) => key !== 'locked_servers')));
    return merged;
}

export function useHydrateServiceForm({
    device, isAdmin, isFormDirty, hostPolicy, lastHydratedSerialRef, hydratedConfigRef,
    setVisiblePasswords, setRemovedServers, setFormState, setIsFormDirty,
}) {
    useEffect(() => {
        const isSerialChanged = lastHydratedSerialRef.current !== device.serial;
        if (isSerialChanged) {
            setVisiblePasswords({});
            setRemovedServers([]);
        }
        if (!isSerialChanged && isFormDirty) return;

        const hydratedState = buildHydratedServiceState(
            device.service_config, hostPolicy, isAdmin, isSerialChanged, hydratedConfigRef.current,
        );
        hydratedConfigRef.current = hydratedState;
        setFormState(hydratedState);
        setIsFormDirty(false);
        lastHydratedSerialRef.current = device.serial;
    }, [device.serial, device.service_config, device.server_config_lock, isAdmin]);
}
