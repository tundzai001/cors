(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const sides = ['source', 'target'];
    let catalog, result = null, exportedRows = [], nextPoint = 0;
    const status = (text, error = false) => {
        $('status').textContent = text;
        $('status').classList.toggle('tool-error', error);
    };
    function invalidate() {
        result = null; exportedRows = []; $('result').hidden = true;
        status('Sẵn sàng chuyển đổi.');
    }
    function options(select, rows, placeholder) {
        select.replaceChildren();
        if (placeholder) select.add(new Option(placeholder, ''));
        rows.forEach(([value, label]) => select.add(new Option(label, value)));
    }
    function degrees(value) {
        const whole = Math.floor(Number(value));
        return `${whole}°${String(Math.round((Number(value) - whole) * 60)).padStart(2, '0')}′`;
    }
    sides.forEach((side, index) => {
        const title = index ? 'Hệ đích' : 'Hệ nguồn';
        const section = document.createElement('section');
        section.className = 'tool-card';
        section.setAttribute('aria-labelledby', side + '-title');
        section.innerHTML = `<div class="tool-card-title"><span aria-hidden="true">0${index + 1}</span><h2 id="${side}-title">${title}</h2></div>
            <label for="${side}-datum">Hệ quy chiếu</label><select id="${side}-datum" disabled><option value="WGS84">WGS 84</option><option value="VN2000">VN-2000</option></select>
            <label for="${side}-format">Dạng tọa độ</label><select id="${side}-format" disabled><option value="geographic">Kinh độ, vĩ độ, độ cao (BLh)</option><option value="projected">Tọa độ phẳng X, Y, độ cao (NEh)</option><option value="geocentric">XYZ địa tâm</option></select>
            <div id="${side}-location" hidden><label for="${side}-province">Tỉnh thành theo hồ sơ đo</label><select id="${side}-province" disabled></select><div id="${side}-region-field" hidden><label for="${side}-region">Vùng đo trước sáp nhập (bắt buộc)</label><select id="${side}-region" disabled></select></div></div>
            <p class="tool-projection-summary" id="${side}-summary" aria-live="polite"></p>
            <details class="tool-details" id="${side}-projection" hidden><summary>Thông số phép chiếu</summary><label for="${side}-width">Múi chiếu</label><select id="${side}-width" disabled><option value="3">3° — TM-3 địa chính</option><option value="6">6° — UTM</option></select><div id="${side}-meridian-field"><label for="${side}-meridian">Kinh tuyến trục</label><select id="${side}-meridian" disabled></select></div><div id="${side}-zone-field" hidden><label for="${side}-zone">Múi UTM</label><select id="${side}-zone" disabled><option value="48">48N — kinh tuyến 105°</option><option value="49">49N — kinh tuyến 111°</option></select></div></details>
            <input id="${side}" type="hidden">`;
        $('system-panels').append(section);
        const height = document.createElement('div');
        height.innerHTML = `<label for="${side}-height">${index ? 'Độ cao đầu ra' : 'Độ cao đầu vào'}</label><select id="${side}-height" disabled><option value="ellipsoidal">h — độ cao ellipsoid</option><option value="orthometric">H — cao độ theo geoid đã biết</option></select><div id="${side}-geoid-field" hidden><label for="${side}-geoid">Độ lệch geoid N ${index ? 'hệ đích' : 'hệ nguồn'} (m)</label><input id="${side}-geoid" type="number" step="any" placeholder="Nhập N tại điểm đo" disabled></div>`;
        $('height-fields').append(height);
    });
    const format = side => $(side + '-format').value;
    function labels(side) {
        if (format(side) === 'geocentric') return ['X địa tâm (m)', 'Y địa tâm (m)', 'Z địa tâm (m)'];
        const height = $(side + '-height').value === 'orthometric' ? 'Cao độ H (m)' : 'Độ cao h (m)';
        return format(side) === 'geographic' ? ['Kinh độ (°)', 'Vĩ độ (°)', height] : ['X / Bắc (m)', 'Y / Đông (m)', height];
    }
    function crs(side) {
        const vn = $(side + '-datum').value === 'VN2000';
        if (format(side) === 'geocentric') return vn ? 'VN2000:ECEF' : 'EPSG:4978';
        if (format(side) === 'geographic') return vn ? 'EPSG:4756' : 'EPSG:4326';
        if ($(side + '-width').value === '6') return 'EPSG:' + (vn ? ($(side + '-zone').value === '48' ? 3405 : 3406) : ($(side + '-zone').value === '48' ? 32648 : 32649));
        return catalog.systems.find(s => s.projection?.zone_width === 3 && s.projection.central_meridian === Number($(side + '-meridian').value))?.id || '';
    }
    function pointLabels() {
        const names = labels('source'), needsThird = sides.some(side => format(side) === 'geocentric');
        const rows = [...$('point-rows').children];
        rows.forEach((row, i) => {
            row.querySelectorAll('input').forEach((input, column) => {
                row.querySelector(`label[for="${input.id}"]`).textContent = `${rows.length > 1 ? `Điểm ${i + 1} · ` : ''}${names[column]}${column === 2 && !needsThird ? ' · tùy chọn' : ''}`;
                input.required = column < 2 || needsThird;
                input.min = ''; input.max = '';
                if (format('source') === 'geographic' && column < 2) {
                    input.min = column === 0 ? '-180' : '-90'; input.max = column === 0 ? '180' : '90';
                }
            });
            row.querySelector('.remove-point').setAttribute('aria-label', `Xóa điểm ${i + 1}`);
        });
    }
    function update(side) {
        const vn = $(side + '-datum').value === 'VN2000';
        const projected = format(side) === 'projected';
        if (!vn) $(side + '-width').value = '6';
        $(side + '-width').options[0].disabled = !vn;
        const tm3 = projected && $(side + '-width').value === '3';
        $(side + '-location').hidden = !tm3;
        $(side + '-projection').hidden = !projected;
        $(side + '-meridian-field').hidden = !tm3;
        $(side + '-zone-field').hidden = tm3;
        $(side).value = crs(side);
        const selected = catalog.systems.find(s => s.id === $(side).value);
        const projection = selected?.projection;
        $(side + '-summary').textContent = projected
            ? projection ? `Kinh tuyến ${degrees(projection.central_meridian)} · múi ${projection.zone_width}° · k₀ = ${String(projection.scale_factor).replace('.', ',')}` : 'Chọn vùng đo cũ để xác định kinh tuyến trục.'
            : format(side) === 'geographic' ? 'Kinh độ, vĩ độ theo độ thập phân.' : 'Ba tọa độ không gian, đơn vị mét.';
        const ecef = sides.some(s => format(s) === 'geocentric');
        $('height-options').hidden = ecef;
        if (ecef) sides.forEach(s => { $(s + '-height').value = 'ellipsoidal'; $(s + '-geoid-field').hidden = true; });
        $('height-help').textContent = ecef ? 'XYZ địa tâm cần đủ ba tọa độ. Khi đổi từ kinh độ/vĩ độ sang XYZ, cần nhập cả độ cao h.' : 'Bỏ trống độ cao nếu chỉ đổi tọa độ ngang. Đơn vị độ cao là mét.';
        pointLabels();
    }
    function applyRegion(side) {
        const region = catalog.provinces63.find(p => p.name === $(side + '-region').value);
        if (region) $(side + '-meridian').value = String(region.central_meridian);
        else if ($(side + '-province').value) $(side + '-meridian').value = '';
        update(side);
    }
    function regions(side) {
        const code = $(side + '-province').value, old = $('edition').value === '63';
        const rows = catalog.provinces63.filter(p => old ? p.name === code : p.current_code === code);
        options($(side + '-region'), rows.map(p => [p.name, `${p.name} · ${degrees(p.central_meridian)}`]), 'Chọn vùng đo trên hồ sơ');
        $(side + '-region-field').hidden = rows.length <= 1;
        if (rows.length === 1) $(side + '-region').value = rows[0].name;
        applyRegion(side);
    }
    function provinces() {
        const old = $('edition').value === '63';
        sides.forEach(side => {
            const rows = old ? catalog.provinces63.map(p => [p.name, `${p.name} · ${degrees(p.central_meridian)}`]) : catalog.provinces34.map(p => [p.code, p.name]);
            rows.sort((a, b) => a[1].localeCompare(b[1], 'vi'));
            options($(side + '-province'), rows, 'Chọn tỉnh hoặc đặt kinh tuyến bên dưới');
            regions(side);
        });
    }
    function addPoint(values = []) {
        if ($('point-rows').children.length >= 1000) { status('Tối đa 1.000 điểm mỗi lần.', true); return; }
        const id = ++nextPoint, row = document.createElement('div');
        row.className = 'tool-point-row';
        ['first', 'second', 'height'].forEach((name, i) => {
            const field = document.createElement('div'), label = document.createElement('label'), input = document.createElement('input');
            input.id = `point-${id}-${name}`; input.type = 'number'; input.step = 'any'; input.inputMode = 'decimal';
            input.value = values[i] ?? ''; label.htmlFor = input.id;
            field.append(label, input); row.append(field);
        });
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove-point';
        remove.innerHTML = '<i class="bi bi-trash" aria-hidden="true"></i>'; remove.setAttribute('aria-label', 'Xóa điểm này');
        remove.addEventListener('click', () => { row.remove(); pointLabels(); invalidate(); });
        row.append(remove); $('point-rows').append(row); pointLabels();
    }
    function busy(value) {
        $('converter').setAttribute('aria-busy', String(value));
        $('converter').querySelectorAll('button,select,input').forEach(el => { el.disabled = value; });
        $('convert').textContent = value ? 'Đang chuyển đổi…' : 'Chuyển đổi';
        if (!value && catalog) sides.forEach(update);
    }
    function parsePoints() {
        let dimensions;
        return [...$('point-rows').children].map((row, index) => {
            const inputs = [...row.querySelectorAll('input')];
            const values = inputs.map(input => input.value.trim());
            const point = values[2] === '' ? values.slice(0, 2) : values;
            if (point.some(v => v === '' || !Number.isFinite(Number(v)))) throw new Error(`Điểm ${index + 1}: nhập đủ tọa độ hợp lệ.`);
            if (sides.some(s => format(s) === 'geocentric') && point.length !== 3) throw new Error('Chuyển XYZ địa tâm cần đủ tọa độ và độ cao h.');
            if (dimensions && dimensions !== point.length) throw new Error('Nhập độ cao cho tất cả các điểm, hoặc bỏ trống độ cao cho tất cả.');
            dimensions = point.length;
            const numeric = point.map(Number);
            return format('source') === 'geographic' ? [numeric[1], numeric[0], ...numeric.slice(2)] : numeric;
        });
    }
    async function request(url, body) {
        const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(url, { signal: controller.signal, ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.map(e => e.msg).join(' · ') || 'Không tải được dữ liệu.');
            return data;
        } finally { clearTimeout(timeout); }
    }
    function showResult(data) {
        const names = labels('target'), geographic = format('target') === 'geographic';
        ['first-heading', 'second-heading', 'third-heading'].forEach((id, i) => { $(id).textContent = names[i]; });
        exportedRows = data.points.map(point => {
            const display = geographic ? [point[1], point[0], ...point.slice(2)] : point;
            return names.map((_, i) => display[i] == null ? '' : display[i].toFixed(geographic && i < 2 ? 9 : 4));
        });
        $('result-body').replaceChildren(...exportedRows.map((values, index) => {
            const row = document.createElement('tr');
            [index + 1, ...values].forEach(value => { const td = document.createElement('td'); td.textContent = value === '' ? '—' : String(value); row.append(td); });
            return row;
        }));
        result = { data, labels: names, heightType: $('target-height').value,
            geoid: $('target-height').value === 'orthometric' ? Number($('target-geoid').value) : null };
        options($('copy-point'), data.points.map((_, i) => [String(i), `Điểm ${i + 1}`]));
        $('copy-point-field').hidden = data.points.length === 1;
        $('result-summary').textContent = `${data.points.length} điểm · ${$('source-datum').selectedOptions[0].textContent} sang ${$('target-datum').selectedOptions[0].textContent} · ${data.dimensions === 3 ? 'Có chuyển độ cao' : 'Chỉ tọa độ ngang'}`;
        $('accuracy').textContent = `${data.accuracy_m == null ? '' : `Sai số công bố của phép đổi datum: ${data.accuracy_m} m; không phải độ chính xác điểm đo. `}${data.note}`;
        $('result').hidden = false; status(`Đã chuyển đổi ${data.points.length} điểm.`);
    }
    $('converter').addEventListener('input', invalidate);
    $('converter').addEventListener('submit', async event => {
        event.preventDefault(); invalidate();
        try {
            const points = parsePoints(), body = { source: crs('source'), target: crs('target'), points };
            for (const side of sides) {
                if (!body[side]) {
                    if ($(side + '-region-field').hidden) { $(side + '-projection').open = true; $(side + '-meridian').focus(); }
                    else $(side + '-region').focus();
                    throw new Error(`Chọn vùng đo hoặc kinh tuyến cho ${side === 'source' ? 'hệ nguồn' : 'hệ đích'}.`);
                }
                body[side + '_height'] = $(side + '-height').value;
                if (body[side + '_height'] === 'orthometric') {
                    const input = $(side + '-geoid');
                    if (!input.value.trim() || !Number.isFinite(Number(input.value))) { $('height-options').open = true; input.focus(); throw new Error('Nhập độ lệch geoid N để chuyển cao độ H.'); }
                    if (points[0].length !== 3) throw new Error('Nhập độ cao cho các điểm khi chọn chuyển cao độ H.');
                    body[side + '_geoid'] = Number(input.value);
                }
            }
            busy(true); status('Đang chuyển đổi tọa độ và độ cao…');
            showResult(await request('/api/coordinates/convert', body));
        } catch (error) { status(error.name === 'AbortError' ? 'Máy chủ chưa phản hồi. Thử lại.' : error.message, true); }
        finally { busy(false); }
    });
    sides.forEach(side => {
        $(side + '-datum').addEventListener('change', () => {
            $(side + '-format').value = $(side + '-datum').value === 'VN2000' ? 'projected' : 'geographic';
            $(side + '-width').value = $(side + '-datum').value === 'VN2000' ? '3' : '6';
            update(side); invalidate();
        });
        ['format', 'width', 'zone'].forEach(field => $(side + '-' + field).addEventListener('change', () => { update(side); invalidate(); }));
        $(side + '-province').addEventListener('change', () => { regions(side); invalidate(); });
        $(side + '-region').addEventListener('change', () => { applyRegion(side); invalidate(); });
        $(side + '-meridian').addEventListener('change', () => { $(side + '-province').value = ''; regions(side); invalidate(); });
        $(side + '-height').addEventListener('change', () => {
            $(side + '-geoid-field').hidden = $(side + '-height').value !== 'orthometric'; pointLabels(); invalidate();
        });
    });
    $('edition').addEventListener('change', () => { provinces(); invalidate(); });
    $('add-point').addEventListener('click', () => { addPoint(); invalidate(); $('point-rows').lastElementChild.querySelector('input').focus(); });
    $('swap').addEventListener('click', () => {
        const previous = result?.data.points;
        const fields = ['datum', 'format', 'width', 'meridian', 'zone', 'province', 'region', 'height', 'geoid'];
        const states = sides.map(side => Object.fromEntries(fields.map(field => [field, $(side + '-' + field).value])));
        sides.forEach((side, i) => {
            fields.forEach(field => { $(side + '-' + field).value = states[1 - i][field]; });
            regions(side); $(side + '-region').value = states[1 - i].region;
            $(side + '-meridian').value = states[1 - i].meridian; update(side);
            $(side + '-geoid-field').hidden = $(side + '-height').value !== 'orthometric';
        });
        if (previous) {
            $('point-rows').replaceChildren();
            previous.forEach(p => addPoint(format('source') === 'geographic' ? [p[1], p[0], ...p.slice(2)] : p));
        }
        pointLabels(); invalidate();
    });
    $('example').addEventListener('click', () => {
        $('source-datum').value = 'WGS84'; $('source-format').value = 'geographic';
        $('target-datum').value = 'VN2000'; $('target-format').value = 'projected'; $('target-width').value = '3';
        sides.forEach(side => { $(side + '-height').value = 'ellipsoidal'; $(side + '-geoid-field').hidden = true; });
        $('target-province').value = $('edition').value === '63' ? 'Hải Phòng' : '31'; regions('target');
        if ($('edition').value === '34') { $('target-region').value = 'Hải Phòng'; applyRegion('target'); }
        sides.forEach(update); $('point-rows').replaceChildren(); addPoint([106.688, 20.845, 30]); invalidate();
    });
    $('copy').addEventListener('click', async () => {
        if (!result) return;
        const snapshot = result, index = $('copy-point').value;
        const point = snapshot.data.points[Number(index)];
        if (!point || point.length !== 3) {
            status('Cần nhập cả độ cao h hoặc H rồi chuyển đổi để sao chép cho GNSS.', true);
            return;
        }
        $('copy').disabled = true; $('copy').setAttribute('aria-busy', 'true');
        $('copy').textContent = 'Đang chuẩn bị…';
        try {
            let wgs = point;
            if (snapshot.data.target !== 'EPSG:4326' || snapshot.heightType !== 'ellipsoidal') {
                const converted = await request('/api/coordinates/convert', {
                    source: snapshot.data.target, target: 'EPSG:4326', points: [point],
                    source_height: snapshot.heightType, source_geoid: snapshot.geoid,
                    target_height: 'ellipsoidal'
                });
                wgs = converted.points[0];
            }
            if (snapshot !== result || index !== $('copy-point').value) throw new Error('Kết quả hoặc điểm đã thay đổi. Hãy sao chép lại.');
            await navigator.clipboard.writeText(window.CorsCoordinateClipboard.encode({ lat: wgs[0], lon: wgs[1], alt: wgs[2] }));
            status(`Đã sao chép điểm ${Number(index) + 1} theo WGS 84 và độ cao h. Dán vào một ô tọa độ trong form GNSS.`);
        } catch (error) {
            status(error.name === 'NotAllowedError' ? 'Trình duyệt chưa cho phép sao chép. Hãy cho phép clipboard và thử lại.' : error.message || 'Không sao chép được. Thử lại.', true);
        } finally {
            $('copy').disabled = false; $('copy').removeAttribute('aria-busy');
            $('copy').textContent = 'Sao chép cho GNSS';
        }
    });
    $('download').addEventListener('click', () => {
        if (!result) return;
        const csv = '\uFEFF' + ['Điểm,' + result.labels.join(','), ...exportedRows.map((row, i) => [i + 1, ...row].join(','))].join('\r\n');
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        const a = document.createElement('a'); a.href = url; a.download = 'toa-do.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    async function initialize() {
        $('retry').hidden = true; busy(true); status('Đang tải danh mục…');
        try {
            catalog = await request('/api/coordinates/catalog');
            sides.forEach(side => options($(side + '-meridian'), catalog.systems.filter(s => s.projection?.zone_width === 3).map(s => [String(s.projection.central_meridian), degrees(s.projection.central_meridian)]), 'Chọn kinh tuyến'));
            $('source-datum').value = 'WGS84'; $('source-format').value = 'geographic';
            $('target-datum').value = 'VN2000'; $('target-format').value = 'projected';
            sides.forEach(side => { $(side + '-meridian').value = '105'; });
            provinces(); $('target-province').value = $('edition').value === '63' ? 'Hà Nội' : '01'; regions('target');
            if (!$('point-rows').children.length) addPoint();
            busy(false); status('Sẵn sàng chuyển đổi.');
        } catch (_) { $('converter').setAttribute('aria-busy', 'false'); $('retry').disabled = false; $('retry').hidden = false; status('Không tải được danh mục. Kiểm tra kết nối và thử lại.', true); }
    }
    $('retry').addEventListener('click', initialize);
    initialize();
})();
