document.addEventListener('DOMContentLoaded', async () => {
    'use strict';
    const user = await window.ApiClient.requireAuthenticatedPage();
    const status = document.getElementById('analytics-status');
    const error = document.getElementById('analytics-error');
    const content = document.getElementById('analytics-content');
    const days = document.getElementById('analytics-days');
    const refresh = document.getElementById('analytics-refresh');
    const number = value => Number(value || 0).toLocaleString('vi-VN');
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const date = value => new Date(`${value}T00:00:00+07:00`).toLocaleDateString('vi-VN', {timeZone:'Asia/Ho_Chi_Minh',day:'2-digit',month:'2-digit'});
    let pending = false;
    let lastReport;
    let series = 'logins';
    if (!user || user.role !== 'admin') {
        content.hidden = true;
        error.textContent = 'Chỉ quản trị viên được xem Analytics.';
        error.classList.remove('d-none');
        status.textContent = 'Không có quyền truy cập';
        refresh.disabled = true;
        days.disabled = true;
        return;
    }

    function trend(rows) {
        const container = document.getElementById('analytics-trend');
        const width = Math.max(280, container.clientWidth), height = container.clientHeight, left = 44, right = 20, top = 15, bottom = 32;
        const max = Math.max(4, ...rows.map(row => row[series] ?? 0));
        const ceiling = Math.ceil(max / 4) * 4;
        const x = i => left + i * (width - left - right) / Math.max(1, rows.length - 1);
        const y = value => height - bottom - value / ceiling * (height - top - bottom);
        const known = rows.map((row,i) => ({row,i})).filter(({row}) => row[series] != null);
        const points = known.map(({row,i}) => `${x(i)},${y(row[series])}`).join(' ');
        const labelName = series === 'logins' ? 'lần đăng nhập' : 'lượt xem';
        container.dataset.series = series;
        let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${series === 'logins' ? 'Đăng nhập' : 'Lượt xem'} theo ngày; khoảng chưa ghi nhận để trống; số liệu chi tiết trong bảng bên dưới">`;
        for (let i=0;i<=4;i++) {
            const value = i * ceiling / 4;
            svg += `<line class="grid" x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}"/><text x="${left-10}" y="${y(value)+4}" text-anchor="end">${number(value)}</text>`;
        }
        if (known.length > 1) svg += `<polygon class="analytics-area" points="${x(known[0].i)},${y(0)} ${points} ${x(known.at(-1).i)},${y(0)}" opacity=".08"/><polyline class="line" points="${points}"/>`;
        if (!known.length) svg += `<text x="${width/2}" y="${height/2}" text-anchor="middle">Chưa có dữ liệu</text>`;
        rows.forEach((row,i) => {
            const label = `${date(row.date)}: ${number(row[series])} ${labelName}`;
            if (row[series] != null) svg += `<circle tabindex="0" role="img" aria-label="${label}" cx="${x(i)}" cy="${y(row[series])}" r="3"><title>${label}</title></circle>`;
            if (i===0 || i===rows.length-1 || i===Math.floor(rows.length/2)) {
                svg += `<text x="${x(i)}" y="${height-4}" text-anchor="${i===0?'start':i===rows.length-1?'end':'middle'}">${date(row.date)}</text>`;
            }
        });
        document.getElementById('analytics-trend').innerHTML = svg + '</svg>';
        const measured = value => value == null ? '<span title="Chưa ghi nhận">—</span>' : number(value);
        document.getElementById('analytics-daily').innerHTML = rows.map(row => `<tr><th scope="row">${date(row.date)}</th><td>${measured(row.views)}</td><td>${measured(row.visitors)}</td><td>${measured(row.logins)}</td></tr>`).join('');
    }
    function render(data) {
        for (const [key,value] of Object.entries(data.totals)) {
            const metric = document.getElementById(`metric-${key}`);
            if (metric) metric.textContent = number(value);
        }
        document.getElementById('metric-logins').textContent = data.logins ? number(data.logins.total) : '—';
        document.getElementById('metric-logins-note').textContent = data.logins
            ? `${number(data.logins.accounts)} tài khoản · gồm lịch sử trong kỳ`
            : 'Đang chờ lịch sử đăng nhập';
        const dateTime = timestamp => new Date(timestamp*1000).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit',year:'numeric'});
        const coverage = data.traffic_started_at
            ? `Lượt xem ghi từ ${dateTime(data.traffic_started_at)}. Khoảng trước đó chưa có dữ liệu lượt xem.`
            : 'Bộ đếm lượt xem chưa nhận được dữ liệu.';
        const loginCoverage = data.logins?.started_at
            ? `Lịch sử đăng nhập từ ${dateTime(data.logins.started_at)}; không tính heartbeat.`
            : 'Chưa có lịch sử đăng nhập.';
        document.getElementById('analytics-coverage').textContent = `${coverage} ${loginCoverage}`;
        document.getElementById('realtime-total').textContent = number(data.totals.active);
        document.getElementById('analytics-range').textContent = `${date(data.daily[0].date)} – ${date(data.daily.at(-1).date)} · ${data.days} ngày gần nhất`;
        trend(data.daily);
        const max = Math.max(1,...data.realtime.map(row=>row.visitors));
        document.getElementById('analytics-realtime').innerHTML = data.realtime.map(row => `<span style="height:${row.visitors/max*100}%" title="${new Date(row.minute*1000).toLocaleTimeString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit'})}: ${number(row.visitors)} khách"></span>`).join('');
        document.getElementById('analytics-realtime').setAttribute('aria-label', `${number(data.totals.active)} khách trong 30 phút gần nhất, cột thể hiện phút hoạt động cuối`);
        const share = views => (views / Math.max(1,data.totals.views)*100).toFixed(1);
        const empty = '<div class="analytics-empty">Chưa có dữ liệu trong khoảng thời gian này.</div>';
        document.getElementById('breakdown-path').innerHTML = data.breakdown.path.length ? data.breakdown.path.map(row => `<tr><th scope="row" data-no-translate>${escape(row.name)}</th><td>${number(row.views)}</td><td>${number(row.visitors)}</td><td><div class="analytics-share"><span aria-hidden="true"><i style="width:${share(row.views)}%"></i></span>${share(row.views)}%</div></td></tr>`).join('') : `<tr><td colspan="4">${empty}</td></tr>`;
        for (const key of ['browser','device','os']) {
            document.getElementById(`breakdown-${key}`).innerHTML = data.breakdown[key].length ? data.breakdown[key].map(row => `<div class="analytics-dimension"><div class="analytics-dimension-label"><span>${escape(row.name)}</span><span>${number(row.views)} <small>· ${share(row.views)}%</small></span></div><div class="analytics-track" aria-hidden="true"><span style="width:${share(row.views)}%"></span></div></div>`).join('') : empty;
        }
    }
    async function load() {
        if (pending) return;
        pending = true;
        refresh.disabled = true;
        days.disabled = true;
        refresh.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Đang tải</span>';
        content.setAttribute('aria-busy','true');
        error.classList.add('d-none');
        status.textContent = 'Đang cập nhật báo cáo…';
        try {
            const response = await window.ApiClient.apiFetch(`/api/admin/analytics?days=${days.value}`,{},0);
            if (!response.ok) throw new Error(response.status===403?'Chỉ quản trị viên được xem Analytics.':'Không tải được báo cáo. Hãy thử làm mới.');
            const data = await response.json();
            lastReport = data;
            render(data);
            status.textContent = `Cập nhật lúc ${new Date(data.updated_at*1000).toLocaleTimeString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'})}`;
        } catch (err) {
            error.textContent = err.message;
            error.classList.remove('d-none');
            status.textContent = 'Cập nhật thất bại · số liệu trước đó có thể đã cũ';
        } finally {
            pending = false;
            refresh.disabled = false;
            days.disabled = false;
            content.setAttribute('aria-busy','false');
            refresh.innerHTML = '<i class="bi bi-arrow-clockwise" aria-hidden="true"></i><span>Làm mới</span>';
        }
    }
    for (const key of ['views','logins']) {
        document.getElementById(`series-${key}`).addEventListener('click', () => {
            series = key;
            for (const name of ['views','logins']) {
                const button = document.getElementById(`series-${name}`);
                button.setAttribute('aria-pressed',String(name === series));
                button.classList.toggle('active',name === series);
            }
            if (lastReport) trend(lastReport.daily);
        });
    }
    window.addEventListener('resize', () => { if (lastReport) trend(lastReport.daily); });
    window.addEventListener('cors:analytics-refresh',load);
    refresh.addEventListener('click',load);
    days.addEventListener('change',load);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden) void load();});
    setInterval(()=>{if(!document.hidden) void load();},30000);
    void load();
});
