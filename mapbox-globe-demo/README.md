# CORS Earth — Mapbox preview

Bản demo độc lập, dùng Mapbox GL JS **3.9.3**, cùng phiên bản quan sát trên [IGS Network](https://network.igs.org/). Không thay bộ dựng bản đồ của dashboard CORS. Năm điểm đến là dữ liệu minh họa, không phải dữ liệu trạm trực tiếp.

## Chạy

```sh
cd mapbox-globe-demo
cp .env.example .env
# Điền MAPBOX_PUBLIC_TOKEN trong .env bằng public token pk. của bạn.
python3 server.py
```

Mở **http://127.0.0.1:8766**. Token hiện tại đã được lưu trong `.env` tại máy này. `.env` được bỏ qua trong Git; server không phục vụ file ẩn. `/config.js` chỉ cung cấp public token cho SDK phía trình duyệt; token public vẫn xuất hiện trong các request Mapbox theo thiết kế.

Public scopes: `styles:tiles`, `styles:read`, `fonts:read`; không cần secret scopes. Trong Allowed URLs, thêm `http://127.0.0.1:8766` và `http://localhost:8766`. Token chỉ cho phép domain sản xuất sẽ trả 403 khi tải tile từ bản demo local.

## Chuyển động

- Một Mapbox canvas, projection `globe`; SDK tự chuyển tiếp sang Mercator khi zoom gần. Không đổi canvas hoặc bộ dựng hình giữa địa cầu và đường phố.
- Dùng native `flyTo` cho điểm đến; zoom đích 10–11, speed 1.15, curve 1.42, maxDuration 5000 ms.
- Thông số quan sát trong JavaScript công khai của IGS: 120 giây/vòng; xoay tuyến tính từng chặng 1000 ms; chậm dần từ zoom 3, dừng từ zoom 5; wheel zoom rate 1/200; đổi padding trong 900 ms.
- Fog theo thông số IGS: color rgb(186,210,235), high-color rgb(36,92,223), horizon-blend 0.002, space-color rgb(11,11,25), star-intensity 0.1.
- Xoay tạm dừng khi tương tác, khi bay đến điểm chọn và khi tab ẩn. `prefers-reduced-motion` tắt tự xoay và đưa camera tới đích tức thời.
- Style `mapbox://styles/mapbox/satellite-streets-v12`, giữ logo và attribution của Mapbox.

IGS dùng các API camera native; phần giao diện và xử lý tương tác của demo được viết riêng. Không sử dụng token hoặc dữ liệu trạm của IGS. SDK và CSS được giữ nguyên từ CDN chính thức trong `vendor/` để tránh chặn lần vẽ đầu do CDN chậm. Điều khoản SDK: [Mapbox GL JS license](https://github.com/mapbox/mapbox-gl-js/blob/v3.9.3/LICENSE.txt).

## Kiểm tra

```sh
npm test
node --check app.js
```

Kiểm tra trực tiếp: Toàn cầu → Việt Nam → Hà Nội → Zürich → Toàn cầu; ngắt một chuyến bay bằng điểm đến khác; xoay, zoom bằng chuột; bật/tắt tự xoay; thu hẹp cửa sổ; dùng Tab/Enter. Mở DevTools và xem `window.earthPreview.map` để kiểm tra camera thật.

Nguồn: [IGS public bundle](https://network.igs.org/static/assets/js/output.361e09f9154d.js), [Mapbox globe](https://docs.mapbox.com/mapbox-gl-js/guides/globe/), [Mapbox flyTo](https://docs.mapbox.com/mapbox-gl-js/api/map/#map#flyto), [Mapbox token management](https://docs.mapbox.com/accounts/guides/tokens/).

## Đưa bản demo lên production

```sh
python3 build.py
```

Chép **nội dung `dist/`** lên một thư mục riêng của `https://cors.aitogy.com`, ví dụ `/earth-preview/`. Các đường dẫn tài nguyên và import trong bản xuất đều tương đối, nên hoạt động dưới thư mục con. Không chép `.env`, `server.py` hoặc thư mục nguồn ra public hosting. `dist/config.js` chứa public token cần thiết cho Mapbox trong trình duyệt; bản xuất được bỏ qua trong Git.

Token đã cung cấp hiện tải tile thành công với Referer `https://cors.aitogy.com/`, nhưng localhost trả 403. Vì vậy, kiểm tra animation với tile thật cần thực hiện trên domain được cho phép. Token không được thay đổi quyền hoặc mở rộng Allowed URLs trong quá trình làm demo này.
