document.addEventListener('DOMContentLoaded', async () => {
    const status = document.getElementById('page-status');
    const form = document.getElementById('key-form');
    const stationList = document.getElementById('station-list');
    const fieldList = document.getElementById('field-list');
    const tbody = document.getElementById('keys-body');
    const allStations = document.getElementById('all-stations');
    const saveButton = document.getElementById('save-key');
    const cancelButton = document.getElementById('cancel-edit');
    const newTokens = new Map();
    const dialog = document.getElementById('key-dialog');
    const formError = document.getElementById('form-error');
    let keys = [];
    let editingId = null;

    const neverExpires = document.getElementById('never-expires');
    const expiryInput = document.getElementById('expires-at');
    const guideDialog = document.getElementById('guide-dialog');
    const fields = {
        name: ['Tên trạm', 'Tên hiển thị của trạm.'],
        status: ['Trạng thái trạm', 'Trạm đang online, offline hoặc đang khởi động.'],
        lat: ['Vĩ độ', 'Tọa độ Bắc / Nam, đơn vị độ.'],
        lon: ['Kinh độ', 'Tọa độ Đông / Tây, đơn vị độ.'],
        alt: ['Độ cao', 'Độ cao trạm, đơn vị mét.'],
        timestamp: ['Lần cập nhật gần nhất', 'Thời điểm trạm gửi dữ liệu, Unix timestamp (giây UTC).'],
        offline_since: ['Thời điểm mất kết nối', 'Unix timestamp (giây UTC); null nếu chưa ghi nhận mất kết nối.'],
        mountpoint: ['Tên điểm phát dữ liệu', 'Mountpoint dùng để nhận dữ liệu hiệu chỉnh GNSS.'],
        streams: ['Danh sách điểm phát', 'Host, port, mountpoint và trạng thái bật của từng luồng NTRIP.'],
        ntrip_status: ['Chi tiết từng kết nối NTRIP', 'Trạng thái và tốc độ truyền của từng Caster.'],
        ntrip_connected: ['Đang kết nối Caster', 'true / false: trạm có kết nối gửi dữ liệu NTRIP hay không.'],
        bps: ['Tốc độ truyền dữ liệu', 'Tổng tốc độ truyền hiện tại của trạm.'],
        is_provisioned: ['Đã được cấp cấu hình', 'true / false: trạm đã được cấp cấu hình dịch vụ.'],
        service_config: ['Đọc cấu hình dịch vụ', 'Host, tài khoản và mật khẩu dịch vụ; chỉ chọn nếu tích hợp cần.']
    };
    const sample = {
        name: 'Trạm minh họa', status: 'online', lat: 21.0285, lon: 105.8542, alt: 12.5,
        timestamp: 1791516600, offline_since: null, mountpoint: 'BASE_DEMO',
        streams: [{stream_id: 's1', host: 'caster.example.com', port: 2101, mountpoint: 'BASE_DEMO', enabled: true}],
        ntrip_status: {server1: {connected: true, bps: 1250}}, ntrip_connected: true,
        bps: 1250, is_provisioned: true,
        service_config: {server1_enabled: true, serverhost1: 'caster.example.com', port1: 2101, mountpoint1: 'BASE_DEMO', username1: 'demo', password1: 'MAT_KHAU_MINH_HOA'}
    };

    function syncExpiry() {
        expiryInput.disabled = neverExpires.checked;
        expiryInput.required = !neverExpires.checked;
    }

    function showGuide(key) {
        document.getElementById('guide-title').textContent = `Hướng dẫn: ${key.name}`;
        document.getElementById('guide-scope').textContent = `${key.all_stations ? 'Tất cả trạm' : key.station_serials.length + ' trạm'} · ${key.data_fields.length} trường dữ liệu · ${key.edit_config ? 'Được sửa cấu hình' : 'Chỉ đọc'}`;
        document.getElementById('guide-state').textContent = keyState(key) === 'active' ? 'Dùng key đã lưu khi tạo. Trang này không thể hiển thị lại key cũ.' : 'Key này đã hết hạn hoặc bị thu hồi; yêu cầu sẽ bị từ chối.';
        const url = `${window.location.origin}/api/integrations/stations`;
        document.getElementById('guide-request').textContent = `curl --request GET '${url}' \\\n  --header 'Authorization: Bearer YOUR_API_KEY'`;
        const example = {serial: key.all_stations ? 'SERIAL_TRAM' : key.station_serials[0]};
        const descriptions = document.getElementById('guide-fields');
        descriptions.replaceChildren();
        for (const name of ['serial', ...key.data_fields]) {
            if (name !== 'serial') example[name] = sample[name];
            const term = document.createElement('dt'); term.textContent = name; term.setAttribute('data-no-translate', '');
            const description = document.createElement('dd'); description.textContent = name === 'serial' ? 'Mã định danh trạm, luôn có trong JSON.' : `${fields[name][0]}: ${fields[name][1]}`;
            descriptions.append(term, description);
        }
        document.getElementById('guide-json').textContent = JSON.stringify([example], null, 2);
        document.getElementById('guide-edit').hidden = !key.edit_config;
        document.getElementById('guide-edit-request').textContent = `curl --request POST '${url}/SERIAL_TRAM/configure-service' \\\n  --header 'Authorization: Bearer YOUR_API_KEY' \\\n  --header 'Content-Type: application/json' \\\n  --data '{"mountpoint1":"BASE_DEMO"}'`;
        document.getElementById('guide-chip-url').textContent = `${url}/SERIAL_TRAM/configure-chip`;
        document.getElementById('guide-copy-status').textContent = '';
        guideDialog.showModal();
        guideDialog.querySelector('.ak-guide-body').scrollTop = 0;
    }

    function showStatus(message, kind = 'info') {
        if (kind === 'success') {
            status.classList.add('d-none');
            window.CorsUI.showToast(kind, message, 5000);
            return;
        }
        status.className = `alert alert-${kind}`;
        status.textContent = message;
        status.classList.remove('d-none');
    }

    async function request(url, options = {}) {
        const response = await window.ApiClient.apiFetch(url, options, 0);
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join('; ') : body.detail || `HTTP ${response.status}`);
        }
        return response.status === 204 ? null : response.json();
    }

    function checkbox(container, name, label, checked = false, detail = '') {
        const wrap = document.createElement('label');
        wrap.className = 'ak-choice';
        const input = document.createElement('input');
        input.className = 'form-check-input';
        input.type = 'checkbox';
        input.value = name;
        input.defaultChecked = checked;
        input.checked = checked;
        const text = document.createElement('span');
        text.className = 'form-check-label';
        text.textContent = label;
        if (detail) { text.setAttribute('data-no-translate', ''); const sub = document.createElement('small'); sub.textContent = detail; text.append(sub); }
        wrap.append(input, text);
        container.append(wrap);
        return input;
    }

    function localDatetime(epoch) {
        const date = new Date(epoch * 1000);
        return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }

    const keyState = key => key.revoked_at ? 'revoked' : key.expires_at !== 0 && key.expires_at <= Date.now() / 1000 ? 'expired' : 'active';
    function renderKeys() {
        tbody.replaceChildren();
        const active = keys.filter(key => keyState(key) === 'active').length;
        document.getElementById('count-total').textContent = keys.length;
        document.getElementById('count-active').textContent = active;
        document.getElementById('count-inactive').textContent = keys.length - active;
        const query = document.getElementById('key-search').value.trim().toLocaleLowerCase('vi');
        const filter = document.getElementById('key-filter').value;
        const visible = keys.filter(key => key.name.toLocaleLowerCase('vi').includes(query) && (filter === 'all' || keyState(key) === filter));
        if (!visible.length) {
            const cell = tbody.insertRow().insertCell();
            cell.colSpan = 6;
            cell.className = 'ak-empty';
            cell.innerHTML = keys.length ? '<i class="bi bi-search" aria-hidden="true"></i><strong>Không tìm thấy key</strong><p>Thử tên khác hoặc đổi bộ lọc trạng thái.</p>' : '<i class="bi bi-key" aria-hidden="true"></i><strong>Kết nối đầu tiên bắt đầu từ đây</strong><p>Tạo API key để cấp dữ liệu trạm cho ứng dụng của bạn.</p>';
            return;
        }
        for (const key of visible) {
            const row = tbody.insertRow();
            const name = document.createElement('span'); name.className = 'ak-key-name'; name.setAttribute('data-no-translate', ''); name.textContent = key.name;
            const subtitle = document.createElement('span'); subtitle.className = 'ak-subtext'; subtitle.textContent = key.edit_config ? 'Đọc dữ liệu & sửa cấu hình' : 'Chỉ đọc dữ liệu';
            const identity = row.insertCell(); identity.append(name, subtitle);
            if (newTokens.has(key.id) && !key.revoked_at) {
                const reveal = document.createElement('details'); reveal.className = 'ak-row-token';
                const toggle = document.createElement('summary'); toggle.textContent = 'Hiển thị key';
                const group = document.createElement('div'); group.className = 'input-group';
                const input = document.createElement('input'); input.className = 'form-control font-monospace'; input.readOnly = true; input.value = newTokens.get(key.id); input.setAttribute('aria-label', `API key ${key.name}`); input.setAttribute('data-no-translate', '');
                const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'btn btn-outline-secondary'; copy.textContent = 'Sao chép';
                copy.addEventListener('click', async () => {
                    try { await navigator.clipboard.writeText(input.value); showStatus('Đã sao chép key.', 'success'); }
                    catch { input.select(); showStatus('Hãy sao chép key đang được chọn.', 'warning'); }
                });
                const note = document.createElement('small'); note.className = 'ak-subtext'; note.textContent = 'Lưu key trước khi tải lại trang.';
                group.append(input, copy); reveal.append(toggle, group, note); identity.append(reveal);
            }
            const date = row.insertCell();
            date.textContent = key.expires_at === 0 ? 'Vĩnh viễn' : new Date(key.expires_at * 1000).toLocaleDateString('vi-VN');
            if (key.expires_at !== 0) {
                const time = document.createElement('span'); time.className = 'ak-subtext'; time.textContent = new Date(key.expires_at * 1000).toLocaleTimeString('vi-VN', {hour:'2-digit',minute:'2-digit'}); date.append(time);
            }
            const scope = row.insertCell(); scope.textContent = key.all_stations ? 'Tất cả trạm' : `${key.station_serials.length} trạm`;
            const count = document.createElement('span'); count.className = 'ak-subtext'; count.textContent = `${key.data_fields.length} trường dữ liệu`; scope.append(count);
            const state = keyState(key);
            const badge = document.createElement('span'); badge.className = `ak-state ${state}`;
            const icon = document.createElement('i'); icon.className = `bi bi-${state === 'active' ? 'check-circle' : state === 'expired' ? 'clock' : 'slash-circle'}`; icon.setAttribute('aria-hidden', 'true');
            badge.append(icon, document.createTextNode(state === 'active' ? 'Đang hoạt động' : state === 'expired' ? 'Hết hạn' : 'Đã thu hồi'));
            row.insertCell().append(badge);
            const actions = document.createElement('div'); actions.className = 'ak-actions'; row.insertCell().append(actions);
            if (!key.revoked_at) {
                const edit = document.createElement('button'); edit.className = 'btn btn-outline-secondary'; edit.textContent = 'Sửa quyền'; edit.setAttribute('aria-label', `Sửa quyền ${key.name}`); edit.addEventListener('click', () => editKey(key));
                const revoke = document.createElement('button'); revoke.className = 'btn btn-outline-danger'; revoke.textContent = 'Thu hồi'; revoke.setAttribute('aria-label', `Thu hồi ${key.name}`); revoke.addEventListener('click', () => revokeKey(key));
                actions.append(edit, revoke);
            }
            const guide = document.createElement('button'); guide.className = 'btn btn-outline-secondary ak-guide-button'; guide.textContent = 'Hướng dẫn'; guide.setAttribute('aria-label', `Hướng dẫn sử dụng ${key.name}`); guide.addEventListener('click', () => showGuide(key)); row.insertCell().append(guide);
            [...row.cells].forEach((cell, index) => { cell.dataset.label = ['Tích hợp', 'Thời hạn', 'Phạm vi', 'Trạng thái', 'Thao tác', 'Hướng dẫn'][index]; });
        }
    }

    function updateSummary() {
        const count = stationList.querySelectorAll('input:checked').length;
        const selectedFields = fieldList.querySelectorAll('input:checked').length;
        const stations = allStations.checked ? 'Tất cả trạm' : `${count} trạm`;
        document.getElementById('station-count').textContent = allStations.checked ? 'Tất cả trạm' : `${count} trạm được chọn`;
        document.getElementById('permission-summary').textContent = `${stations} · ${selectedFields} trường dữ liệu · ${document.getElementById('edit-config').checked ? 'Được sửa cấu hình' : 'Chỉ đọc'}`;
    }
    function filterStations() {
        const query = document.getElementById('station-search').value.trim().toLocaleLowerCase('vi');
        let count = 0;
        stationList.querySelectorAll('label').forEach(label => { label.hidden = !label.textContent.toLocaleLowerCase('vi').includes(query); if (!label.hidden) count++; });
        document.getElementById('station-empty').classList.toggle('d-none', count > 0);
    }
    function openEditor() {
        formError.classList.add('d-none');
        document.getElementById('station-search').value = '';
        filterStations(); updateSummary(); dialog.showModal();
        form.scrollTop = 0;
        document.getElementById('key-name').focus();
    }

    function editKey(key) {
        editingId = key.id;
        document.getElementById('form-title').textContent = `Sửa quyền: ${key.name}`;
        document.getElementById('key-name').value = key.name;
        neverExpires.checked = key.expires_at === 0;
        expiryInput.value = localDatetime(key.expires_at || Math.floor(Date.now() / 1000) + 30 * 86400);
        syncExpiry();
        allStations.checked = key.all_stations;
        stationList.querySelectorAll('input').forEach(input => { input.checked = key.station_serials.includes(input.value); });
        fieldList.querySelectorAll('input').forEach(input => { input.checked = key.data_fields.includes(input.value); });
        document.getElementById('edit-config').checked = key.edit_config;
        allStations.dispatchEvent(new Event('change'));
        saveButton.textContent = 'Lưu quyền';
        openEditor();
    }

    function resetForm() {
        editingId = null;
        form.reset();
        syncExpiry();
        document.getElementById('expires-at').value = localDatetime(Math.floor(Date.now() / 1000) + 30 * 86400);
        document.getElementById('form-title').textContent = 'Tạo key';
        saveButton.textContent = 'Tạo key';
        updateSummary();
        allStations.dispatchEvent(new Event('change'));
    }

    async function revokeKey(key) {
        if (!window.confirm(`Thu hồi key “${key.name}”? Tích hợp đang dùng key sẽ bị từ chối ngay.`)) return;
        try {
            await request(`/api/admin/api-keys/${key.id}`, { method: 'DELETE' });
            keys = keys.map(item => item.id === key.id ? { ...item, revoked_at: Date.now() / 1000 } : item);
            newTokens.delete(key.id);
            renderKeys();
            showStatus('Đã thu hồi key.', 'success');
        } catch (error) { showStatus(error.message, 'danger'); }
    }

    try {
        const user = await window.ApiClient.requireAuthenticatedPage();
        if (!user) return;
        if (user.role !== 'admin') { showStatus('Chỉ admin được quản trị API key.', 'danger'); form.classList.add('d-none'); return; }
        const [devices, loadedKeys] = await Promise.all([request('/api/devices'), request('/api/admin/api-keys')]);
        keys = loadedKeys;
        for (const device of devices) checkbox(stationList, device.serial, device.name || device.serial, false, device.serial);
        const groups = [ ['Dữ liệu cơ bản', ['name','status','lat','lon','alt']], ['Theo dõi hoạt động', ['timestamp','offline_since','ntrip_connected','bps']], ['Dữ liệu nâng cao', ['mountpoint','streams','ntrip_status','is_provisioned','service_config']] ];
        for (const [title, names] of groups) {
            let container = fieldList;
            if (title === 'Dữ liệu nâng cao') {
                container = document.createElement('details'); container.className = 'ak-advanced';
                const summary = document.createElement('summary'); summary.textContent = 'Dữ liệu nâng cao · NTRIP & cấu hình'; container.append(summary); fieldList.append(container);
            } else {
                const heading = document.createElement('h4'); heading.className = 'ak-field-group'; heading.textContent = title; container.append(heading);
            }
            const options = document.createElement('div'); options.className = 'ak-field-options'; container.append(options);
            for (const name of names) checkbox(options, name, fields[name][0], ['name','status','lat','lon'].includes(name), fields[name][1]);
        }
        neverExpires.addEventListener('change', syncExpiry);
        document.getElementById('close-guide').addEventListener('click', () => guideDialog.close());
        document.getElementById('copy-request').addEventListener('click', async () => {
            const feedback = document.getElementById('guide-copy-status');
            try { await navigator.clipboard.writeText(document.getElementById('guide-request').textContent); feedback.textContent = 'Đã sao chép.'; }
            catch { feedback.textContent = 'Hãy chọn và sao chép mẫu bên trên.'; }
        });
        allStations.addEventListener('change', () => { stationList.querySelectorAll('input').forEach(input => { input.disabled = allStations.checked; }); updateSummary(); });
        document.getElementById('create-key').disabled = false;
        document.getElementById('create-key').addEventListener('click', () => { resetForm(); openEditor(); });
        cancelButton.addEventListener('click', () => dialog.close());
        dialog.addEventListener('cancel', event => { if (saveButton.disabled) event.preventDefault(); });
        document.getElementById('close-dialog').addEventListener('click', () => dialog.close());
        document.getElementById('key-search').addEventListener('input', renderKeys);
        document.getElementById('key-filter').addEventListener('change', renderKeys);
        document.getElementById('station-search').addEventListener('input', filterStations);
        form.addEventListener('change', updateSummary);
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const expiresAt = neverExpires.checked ? 0 : Math.floor(new Date(expiryInput.value).getTime() / 1000);
            const body = {
                name: document.getElementById('key-name').value.trim(), expires_at: expiresAt,
                all_stations: allStations.checked,
                station_serials: [...stationList.querySelectorAll('input:checked')].map(input => input.value),
                data_fields: [...fieldList.querySelectorAll('input:checked')].map(input => input.value),
                edit_config: document.getElementById('edit-config').checked
            };
            formError.classList.add('d-none');
            if (!body.name || !Number.isFinite(expiresAt) || (expiresAt !== 0 && expiresAt <= Date.now() / 1000) || (!body.all_stations && !body.station_serials.length) || !body.data_fields.length) {
                formError.textContent = 'Nhập tên, chọn Vĩnh viễn hoặc thời hạn trong tương lai, ít nhất một trạm và một trường dữ liệu.';
                formError.classList.remove('d-none'); formError.focus(); return;
            }
            saveButton.disabled = true;
            cancelButton.disabled = true;
            document.getElementById('close-dialog').disabled = true;
            saveButton.textContent = 'Đang lưu…';
            try {
                const result = await request(editingId ? `/api/admin/api-keys/${editingId}` : '/api/admin/api-keys', {
                    method: editingId ? 'PUT' : 'POST', body: JSON.stringify(body)
                });
                const { token, ...metadata } = result;
                if (token) newTokens.set(metadata.id, token);
                keys = editingId ? keys.map(key => key.id === editingId ? metadata : key) : [metadata, ...keys];
                renderKeys();
                dialog.close();
                resetForm();
                showStatus(result.token ? 'Đã tạo key. Xem và sao chép trong Key đã cấp.' : 'Đã cập nhật quyền key.', 'success');
            } catch (error) { formError.textContent = error.message; formError.classList.remove('d-none'); formError.focus(); }
            finally { cancelButton.disabled = false; document.getElementById('close-dialog').disabled = false; saveButton.disabled = false; saveButton.textContent = editingId ? 'Lưu quyền' : 'Tạo key'; }
        });
        resetForm();
        renderKeys();
        status.classList.add('d-none');
    } catch (error) { showStatus(error.message, 'danger'); }
});
