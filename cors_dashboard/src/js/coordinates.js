(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const sides = ['source', 'target'];
    let catalog, result = null, csv = '';
    const status = (message, error = false) => {
        $('status').textContent = message;
        $('status').classList.toggle('error', error);
    };
    function invalidate() {
        result = null; csv = ''; $('result').hidden = true;
        status('Sẵn sàng chuyển đổi.');
    }
    function options(select, rows, placeholder) {
        select.replaceChildren(new Option(placeholder, ''));
        rows.forEach(([value, label]) => select.add(new Option(label, value)));
    }
    const system = side => catalog.systems.find(s => s.id === $(side).value);
    const geographic = side => system(side)?.geographic;
    function meridianText(value) {
        const degrees = Math.floor(value);
        return `${degrees}°${String(Math.round((value - degrees) * 60)).padStart(2, '0')}′`;
    }
    function updateAxis(side) {
        const selected = system(side);
        const projection = selected?.projection;
        $(side + '-meridian').textContent = projection
            ? `Kinh tuyến trục ${meridianText(projection.central_meridian)} · múi ${projection.zone_width}° · k₀ = ${String(projection.scale_factor).replace('.', ',')} · ${selected.id}`
            : selected ? (selected.geographic ? 'Tọa độ địa lý, không áp dụng kinh tuyến trục và múi chiếu.' : 'Web Mercator, không chia múi 3° / 6°.') : 'Chọn vùng đo hoặc hệ tọa độ để xác định kinh tuyến trục và múi chiếu.';
        $(side + '-axis').textContent = geographic(side) ? 'Vĩ độ, kinh độ · độ thập phân' : 'X (Bắc), Y (Đông) · mét';
        $(side + '-location').hidden = !catalog.provinces63.some(p => p.crs === $(side).value) && $(side).value !== '';
        $('points-label').textContent = geographic('source') ? 'Vĩ độ, kinh độ — mỗi dòng một điểm' : 'X (Bắc), Y (Đông) — mỗi dòng một điểm';
        $('points').placeholder = geographic('source') ? '21.0285 105.8542' : '2326345.312 588591.944';
    }
    function regions(side) {
        const code = $(side + '-province').value;
        const old = $('edition').value === '63';
        const rows = catalog.provinces63.filter(p => old ? p.name === code : p.current_code === code);
        options($(side + '-region'), rows.map(p => [p.name, `${p.name} · ${meridianText(p.central_meridian)} · múi 3°`]), code ? 'Chọn vùng đo theo hồ sơ' : 'Chọn tỉnh thành trước');
        if (rows.length === 1) { $(side + '-region').value = rows[0].name; applyRegion(side); }
        else if (code) { $(side).value = ''; updateAxis(side); }
    }
    function applyRegion(side) {
        const p = catalog.provinces63.find(p => p.name === $(side + '-region').value);
        if (!p) { $(side).value = ''; updateAxis(side); return; }
        $(side).value = p.crs;
        updateAxis(side);
    }
    function provinces() {
        const old = $('edition').value === '63';
        sides.forEach(side => {
            const rows = old ? catalog.provinces63.map(p => [p.name, `${p.name} · ${meridianText(p.central_meridian)} · múi 3°`]) : catalog.provinces34.map(p => [p.code, p.name]);
            rows.sort((a, b) => a[1].localeCompare(b[1], 'vi'));
            options($(side + '-province'), rows, 'Chọn tỉnh hoặc chọn trực tiếp hệ chiếu');
            regions(side);
        });
    }
    function busy(value) {
        $('converter').setAttribute('aria-busy', String(value));
        $('converter').querySelectorAll('button,select,textarea').forEach(el => { el.disabled = value; });
        $('convert').textContent = value ? 'Đang chuyển đổi…' : 'Chuyển đổi tọa độ';
    }
    function parsePoints() {
        const lines = $('points').value.trim().split(/\r?\n/);
        if (!lines[0] || lines.length > 1000) throw new Error('Nhập từ 1 đến 1.000 điểm.');
        return lines.map((line, i) => {
            const parts = line.trim().split(/[\s,;]+/);
            if (parts.length !== 2 || parts.some(p => !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(p) || !Number.isFinite(Number(p)))) {
                throw new Error(`Dòng ${i + 1}: cần đúng hai số. Dùng dấu chấm thập phân.`);
            }
            return parts.map(Number);
        });
    }
    $('converter').addEventListener('input', invalidate);
    $('converter').addEventListener('submit', async event => {
        event.preventDefault();
        invalidate();
        let points;
        try { points = parsePoints(); } catch (error) { status(error.message, true); $('points').focus(); return; }
        busy(true); status(`Đang chuyển đổi ${points.length} điểm…`);
        try {
            const response = await fetch('/api/coordinates/convert', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: $('source').value, target: $('target').value, points })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Dữ liệu không hợp lệ. Kiểm tra tọa độ và hệ chiếu.');
            const labels = geographic('target') ? ['Vĩ độ (°)', 'Kinh độ (°)'] : ['X — Bắc (m)', 'Y — Đông (m)'];
            const decimals = geographic('target') ? 9 : 4;
            const rows = data.points.map(p => p.map(v => v.toFixed(decimals)));
            $('first-heading').textContent = labels[0]; $('second-heading').textContent = labels[1];
            $('result-body').replaceChildren(...rows.map((row, i) => {
                const tr = document.createElement('tr');
                [String(i + 1), ...row].forEach(v => { const td = document.createElement('td'); td.textContent = v; tr.append(td); });
                return tr;
            }));
            csv = '\uFEFF' + ['Điểm,' + labels.join(','), ...rows.map((r, i) => [i + 1, ...r].join(','))].join('\r\n');
            result = data;
            $('result-summary').textContent = `${rows.length} điểm · ${data.source} sang ${data.target}`;
            $('accuracy').textContent = `${data.accuracy_m === null ? 'Chưa có mức sai số công bố.' : `Sai số phép biến đổi công bố: ${data.accuracy_m} m (không phải độ chính xác của điểm đo).`} ${data.note}`;
            $('result').hidden = false; status(`Đã chuyển đổi ${rows.length} điểm.`);
        } catch (error) { status(error.message || 'Không kết nối được máy chủ.', true); }
        finally { busy(false); }
    });
    $('edition').addEventListener('change', () => { invalidate(); provinces(); });
    sides.forEach(side => {
        $(side).addEventListener('change', () => {
            invalidate(); $(side + '-province').value = ''; regions(side); updateAxis(side);
        });
        $(side + '-province').addEventListener('change', () => { invalidate(); regions(side); });
        $(side + '-region').addEventListener('change', () => { invalidate(); applyRegion(side); });
    });
    $('swap').addEventListener('click', () => {
        const reversed = result?.points;
        const value = $('source').value; $('source').value = $('target').value; $('target').value = value;
        sides.forEach(side => { $(side + '-province').value = ''; regions(side); updateAxis(side); });
        if (reversed) $('points').value = reversed.map(p => p.join(' ')).join('\n');
        invalidate();
    });
    $('example').addEventListener('click', () => {
        $('source').value = 'EPSG:4326'; $('target').value = 'EPSG:5897';
        sides.forEach(side => { $(side + '-province').value = ''; regions(side); updateAxis(side); });
        $('points').value = '21.0285 105.8542\n21.036 105.828'; invalidate();
    });
    $('copy').addEventListener('click', async () => {
        if (!result) return;
        try { await navigator.clipboard.writeText(result.points.map(p => p.join('\t')).join('\n')); status('Đã sao chép kết quả.'); }
        catch (_) { status('Không sao chép được. Hãy tải CSV.', true); }
    });
    $('download').addEventListener('click', () => {
        if (!result) return;
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        const a = document.createElement('a'); a.href = url; a.download = 'toa-do-' + result.target.replace(':', '-') + '.csv'; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    fetch('/api/coordinates/catalog').then(async response => {
        if (!response.ok) throw new Error('Không tải được danh mục. Tải lại trang để thử lại.');
        catalog = await response.json();
        sides.forEach(side => options($(side), catalog.systems.map(s => [s.id, `${s.name} · ${s.id}`]), 'Chọn hệ tọa độ'));
        $('source').value = 'EPSG:4326'; $('target').value = 'EPSG:5897';
        provinces(); sides.forEach(updateAxis); busy(false); status('Sẵn sàng chuyển đổi.');
    }).catch(error => { $('converter').setAttribute('aria-busy', 'false'); status(error.message, true); });
})();
