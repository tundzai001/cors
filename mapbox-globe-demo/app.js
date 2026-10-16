import { spinStep, flightOptions } from './motion.js';

const places = [
    { id: 'hanoi', name: 'Hà Nội', region: 'Việt Nam · 21.028° N', center: [105.8342, 21.0278], zoom: 11 },
    { id: 'danang', name: 'Đà Nẵng', region: 'Việt Nam · 16.054° N', center: [108.2022, 16.0544], zoom: 11 },
    { id: 'saigon', name: 'TP. Hồ Chí Minh', region: 'Việt Nam · 10.823° N', center: [106.6297, 10.8231], zoom: 11 },
    { id: 'tokyo', name: 'Tokyo', region: 'Nhật Bản · 35.676° N', center: [139.6503, 35.6762], zoom: 10 },
    { id: 'zurich', name: 'Zürich', region: 'Thụy Sĩ · 47.377° N', center: [8.5417, 47.3769], zoom: 10 },
];
const views = {
    world: { center: [105, 17], zoom: 1.35 },
    vietnam: { center: [106, 16], zoom: 5.2 },
};
const $ = id => document.getElementById(id);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let map;
let ready = false;
let spinning = false;
let flying = false;
let interacting = false;
let resumeTimer;
let tileFailures = 0;
let accessDenied = false;
let destination = '';

function enableControls(enabled) {
    document.querySelectorAll('.place, [data-view], #spin').forEach(button => { button.disabled = !enabled; });
}

function setLoading(message = '') {
    $('loading').hidden = !message;
    $('loading-text').textContent = message;
}
function updateSpinButton() {
    $('spin').setAttribute('aria-pressed', String(spinning));
    $('spin').querySelector('span').textContent = spinning ? 'Tắt tự xoay' : 'Bật tự xoay';
    $('spin').title = spinning && map?.getZoom() >= 5 ? 'Tự xoay tạm dừng khi zoom gần' : 'Xoay địa cầu tự động';
}
function rotate() {
    if (!ready || !spinning || flying || interacting || document.hidden || reducedMotion.matches || map.isMoving()) return;
    const step = spinStep(map.getZoom());
    if (!step) return;
    const center = map.getCenter();
    map.easeTo({ center: [center.lng - step, center.lat], duration: 1000, easing: t => t });
}
function selectPlace(id) {
    document.querySelectorAll('.place').forEach(button => {
        button.classList.toggle('selected', button.dataset.place === id);
        button.setAttribute('aria-pressed', String(button.dataset.place === id));
    });
    if (map?.getSource('places')) map.setFilter('place-selected', ['==', ['get', 'id'], id || '']);
}
function fly(view, id = null) {
    if (!ready) return;
    // stop() emits moveend for the previous flight; suppress rotation until it finishes.
    interacting = true;
    map.stop();
    interacting = false;
    flying = true;
    selectPlace(id);
    destination = places.find(place => place.id === id)?.name || (view === views.world ? 'Toàn cầu' : 'Việt Nam');
    $('flight-status').textContent = `Đang bay tới ${destination}…`;
    document.querySelectorAll('[data-view]').forEach(button => {
        const active = views[button.dataset.view] === view;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
    });
    map.flyTo(flightOptions(view.center, view.zoom, reducedMotion.matches), { demoFlight: true });
}
function updateReadout() {
    const center = map.getCenter();
    const longitude = ((center.lng + 180) % 360 + 360) % 360 - 180;
    $('latitude').textContent = `${Math.abs(center.lat).toFixed(3)}° ${center.lat >= 0 ? 'N' : 'S'}`;
    $('longitude').textContent = `${Math.abs(longitude).toFixed(3)}° ${longitude >= 0 ? 'E' : 'W'}`;
    $('zoom').textContent = map.getZoom().toFixed(2);
    updateSpinButton();
}
function mapPadding() {
    return innerWidth <= 600
        ? { top: 140, right: 0, bottom: 240, left: 0 }
        : { top: 95, right: innerWidth <= 900 ? 290 : 350, bottom: 90, left: 30 };
}
function pauseForInteraction() {
    interacting = true;
    flying = false;
    clearTimeout(resumeTimer);
    map.stop();
}
function resumeAfterInteraction() {
    clearTimeout(resumeTimer);
    resumeTimer = setTimeout(() => { interacting = false; rotate(); }, 650);
}

for (const [index, place] of places.entries()) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'place'; button.dataset.place = place.id;
    button.setAttribute('aria-pressed', 'false');
    button.innerHTML = `<span class="place-number">0${index + 1}</span><span><span class="place-name"></span><span class="place-region"></span></span><span class="place-arrow" aria-hidden="true">↗</span>`;
    button.querySelector('.place-name').textContent = place.name;
    button.querySelector('.place-region').textContent = place.region;
    button.addEventListener('click', () => fly(place, place.id));
    $('places-list').append(button);
}
enableControls(false);
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
    const view = views[button.dataset.view];
    fly(view, view.id || null);
}));
$('spin').addEventListener('click', () => {
    if (!ready) return;
    spinning = !spinning;
    flying = false;
    interacting = false;
    if (spinning && reducedMotion.matches) {
        spinning = false;
        setLoading('Thiết bị đang bật chế độ giảm chuyển động.');
        setTimeout(() => setLoading(), 3000);
    }
    map.stop(); updateSpinButton(); rotate();
});

function start(token) {
    if (!token.startsWith('pk.')) throw new Error('Dùng public token bắt đầu bằng pk.');
    if (!window.mapboxgl) throw new Error('Không tải được Mapbox. Kiểm tra kết nối rồi tải lại trang.');
    if (!mapboxgl.supported()) throw new Error('Trình duyệt chưa hỗ trợ WebGL cần thiết cho địa cầu.');
    if (map) map.remove();
    ready = false; tileFailures = 0; accessDenied = false;
    enableControls(false);
    setLoading('Đang tải ảnh địa cầu…');
    map = new mapboxgl.Map({
        container: 'map', accessToken: token,
        style: 'mapbox://styles/mapbox/satellite-streets-v12',
        projection: 'globe', center: views.world.center, zoom: views.world.zoom,
        minZoom: 0, maxZoom: 19, maxPitch: 65,
        renderWorldCopies: false, attributionControl: { compact: true },
        fadeDuration: reducedMotion.matches ? 0 : 300,
    });
    map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), 'top-left');
    map.scrollZoom.setWheelZoomRate(1 / 200);
    map.setPadding(mapPadding());
    map.on('style.load', () => {
        // Same atmosphere values observed in IGS Network's public source.
        map.setFog({ color: 'rgb(186, 210, 235)', 'high-color': 'rgb(36, 92, 223)',
            'horizon-blend': 0.002, 'space-color': 'rgb(11, 11, 25)', 'star-intensity': 0.1 });
        map.addSource('places', { type: 'geojson', data: { type: 'FeatureCollection', features: places.map(place => ({
            type: 'Feature', properties: { id: place.id, name: place.name }, geometry: { type: 'Point', coordinates: place.center },
        })) } });
        map.addLayer({ id: 'place-selected', type: 'circle', source: 'places', filter: ['==', ['get', 'id'], ''], paint: {
            'circle-radius': 13, 'circle-color': '#a6e5d5', 'circle-opacity': 0.15,
            'circle-stroke-width': 1, 'circle-stroke-color': '#a6e5d5',
        } });
        map.addLayer({ id: 'place-points', type: 'circle', source: 'places', paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 4, 7, 6],
            'circle-color': '#a6e5d5', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#142d30',
        } });
        map.on('click', 'place-points', event => {
            const place = places.find(item => item.id === event.features[0].properties.id);
            if (place) fly(place, place.id);
        });
        map.on('mouseenter', 'place-points', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'place-points', () => { map.getCanvas().style.cursor = ''; });
    });
    map.on('load', () => {
        if (accessDenied) return;
        ready = true;
        enableControls(true);
        setLoading(); updateReadout();
        spinning = !reducedMotion.matches;
        updateSpinButton(); rotate();
    });
    map.on('move', updateReadout);
    map.on('moveend', event => {
        if (event.demoFlight) {
            flying = false;
            if (destination) $('flight-status').textContent = `Đang xem ${destination}.`;
        }
        rotate();
    });
    map.on('error', event => {
        const status = event.error?.status;
        if (status === 401 || status === 403) {
            accessDenied = true;
            enableControls(false);
            ready = false; spinning = false; map.stop(); updateSpinButton(); setLoading();
            $('token-error').textContent = status === 403
                ? 'Mapbox chưa cho phép tải bản đồ tại địa chỉ này. Kiểm tra Allowed URLs của token.'
                : 'Public token chưa hợp lệ. Kiểm tra lại token Mapbox.';
            if (!$('token-dialog').open) $('token-dialog').showModal();
        } else if (++tileFailures >= 3) {
            setLoading('Một số ảnh bản đồ chưa tải được. Kiểm tra kết nối mạng.');
        }
    });
    map.on('idle', () => { if (ready && tileFailures < 3) setLoading(); });
    const canvas = map.getCanvas();
    canvas.addEventListener('pointerdown', pauseForInteraction);
    canvas.addEventListener('wheel', () => { pauseForInteraction(); resumeAfterInteraction(); }, { passive: true });
    canvas.addEventListener('keydown', () => { pauseForInteraction(); resumeAfterInteraction(); });
    canvas.addEventListener('contextmenu', resumeAfterInteraction);
    // Expose the actual map for inspecting camera state in browser DevTools.
    window.earthPreview = { map, fly, places };
}
window.addEventListener('pointerup', () => { if (map) resumeAfterInteraction(); });
window.addEventListener('pointercancel', () => { if (map) resumeAfterInteraction(); });
window.addEventListener('blur', () => { if (map) resumeAfterInteraction(); });
window.addEventListener('resize', () => {
    if (!ready) return;
    interacting = true; map.stop(); interacting = false; flying = true;
    map.easeTo({ padding: mapPadding(), duration: reducedMotion.matches ? 0 : 900 }, { demoFlight: true });
});
document.addEventListener('visibilitychange', () => {
    if (!map) return;
    if (document.hidden) { flying = false; map.stop(); } else rotate();
});
reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches && map) { spinning = false; map.stop(); updateSpinButton(); }
});
$('token-form').addEventListener('submit', event => {
    event.preventDefault();
    try {
        start($('token').value.trim());
        $('token-dialog').close();
        $('token').value = '';
    } catch (error) { $('token-error').textContent = error.message; }
});
try {
    const { default: token } = await import('./config.js');
    if (token) start(token); else throw new Error('Chưa cấu hình token.');
} catch (error) {
    setLoading();
    if (error.message !== 'Chưa cấu hình token.' && !error.message.includes('Failed to fetch')) $('token-error').textContent = error.message;
    $('token-dialog').showModal();
}
