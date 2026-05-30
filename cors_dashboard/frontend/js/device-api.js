'use strict';

(function () {
    function request(url, options = {}) {
        return window.ApiClient.apiFetch(url, options);
    }

    const DeviceApi = {
        rename(serial, name) {
            return request(window.ApiRoutes.devices.name(serial), {
                method: 'PUT',
                body: JSON.stringify({ name })
            });
        },

        remove(serial) {
            return request(window.ApiRoutes.devices.detail(serial), { method: 'DELETE' });
        },

        reset(serial) {
            return request(window.ApiRoutes.devices.reset(serial), { method: 'POST' });
        },

        sendCommand(serial, command, payload = {}) {
            return request(window.ApiRoutes.devices.command(serial), {
                method: 'POST',
                body: JSON.stringify({ command, payload })
            });
        },

        configureChip(serial, payload) {
            return request(window.ApiRoutes.devices.configureChip(serial), {
                method: 'POST',
                body: JSON.stringify({ command: 'CONFIGURE_CHIP', payload })
            });
        },

        configureService(serial, payload) {
            return request(window.ApiRoutes.devices.configureService(serial), {
                method: 'POST',
                body: JSON.stringify(payload)
            });
        },

        lock(serial) {
            return request(window.ApiRoutes.devices.lock(serial), { method: 'POST' });
        },

        unlock(serial) {
            return request(window.ApiRoutes.devices.unlock(serial), { method: 'POST' });
        }
    };

    window.DeviceApi = DeviceApi;
})();
