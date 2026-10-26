# Tra cứu địa danh hành chính mới và cũ

## Nguồn và phiên bản

Dữ liệu lấy từ [thanglequoc/vietnamese-provinces-database](https://github.com/thanglequoc/vietnamese-provinces-database), giấy phép MIT. Bản sao giấy phép đặt cạnh dữ liệu, trong `app/data/vietnam_units_LICENSE.txt`; phiên bản, đường dẫn nguồn và SHA-256 được ghi ở `vietnam_units_sources.json`.

| Bộ | Mốc nguồn | Tỉnh | Huyện | Xã/phường | Cách đọc |
| --- | --- | --- | --- | --- | --- |
| `current` | v5.2.0 | 34 | 0 | 3.321 | Đọc các cột chuẩn từ PostgreSQL hai cấp; có catalogue JSON dự phòng. |
| `legacy` | v2.4.1 | 63 | 696 | 10.035 | PostgreSQL schema `vn_admin_legacy`; catalogue JSON chỉ dự phòng. |

Không trộn mã cũ vào bảng hiện hành. SQL chính thức v2.4.1 được giữ nguyên trong `app/data/legacy_v2_4_1`, kiểm tra SHA-256 trước khi nhập. Import chạy trong transaction, đặt search_path riêng, kiểm tra đủ 63/696/10.035 bản ghi và checksum bảng tỉnh/xã hiện hành trước khi commit. Không chạy SQL 63 tỉnh trực tiếp vào schema public.

## Schema SQL được hỗ trợ

SQL v5.2.0 nguyên bản không có bảng `districts`, cột `latitude`/`longitude`, `wards.district_code` hay bảng GIS. API đọc tên/mã và quan hệ `wards.province_code` bằng các cột tồn tại trong schema đó; không chọn toàn bộ ORM entity có các cột mở rộng chưa được tạo.

Ví dụ `Ninh Vân, Ninh Bình` hoặc `Ninh Van Ninh Binh` phải khớp bản lịch sử `14554`: Xã Ninh Vân, Thành phố Hoa Lư, Tỉnh Ninh Bình. Không thay bằng thành phố Ninh Bình hay Xã Vạn Ninh thuộc tỉnh khác. Dữ liệu chỉ có tên và quan hệ hành chính không chứng minh tọa độ của đơn vị.

## API tương thích

- `/api/public/search-administrative-units?q=...`: mặc định tìm cả hai bộ. Có thể chọn `dataset=current` hoặc `dataset=legacy`. Kết quả có `dataset` và `dataset_version` để phân biệt mã.
- `/api/public/vietnam-provinces`, `/vietnam-wards`, `/vietnam-districts`: mặc định `dataset=current`; thêm `dataset=legacy` để đọc tên và phân cấp cũ. `province_code`/`district_code` được hiểu trong bộ đã chọn. Bộ hiện hành không có huyện.
- Xã/huyện chưa có nguồn tọa độ trả `lat=null, lon=null`, không kế thừa tâm tỉnh/huyện. Các điểm tỉnh có sẵn được ghi `coordinate_source=known_province_point` khi dùng cho tìm kiếm.
- `/api/public/geocode?q=...`: bổ sung phân cấp lịch sử cho truy vấn geocoder, nhưng vẫn kiểm tra kết quả với tên cụ thể và tỉnh trong truy vấn gốc. Không coi điểm số cao của kết quả chỉ khớp tỉnh là đủ.

Phần tìm kiếm trên bản đồ bỏ qua tọa độ null và kết quả không khớp; danh sách dự phòng chỉ áp dụng cho truy vấn đúng tên địa danh dự phòng. Nếu chưa định vị được, báo chưa tìm thấy và cho người dùng thêm địa chỉ hoặc nhập lat,lon. Không lặng lẽ đưa truy vấn một xã về tọa độ thành phố.

## Kiểm tra và triển khai

```sh
cd cors_dashboard/backend
PYTHONPATH=. python3 -m unittest tests.test_administrative_catalog tests.test_map_geocode_proxy -q
```

Từ root repo:

```sh
node scripts/stations_map_search_selftest.js
node scripts/stations_map_search_selftest.js cors_dashboard/frontend/js/dist/stations-map-search.min.js
```

Đồng bộ module, dữ liệu lịch sử/giấy phép, JS đã build và cache version trong index.html. Khởi động lại backend để nạp code/cache catalogue mới. Trước khi triển khai cần sao lưu các file sẽ thay; khôi phục các file đó và khởi động lại backend là đường rollback, không cần thao tác lên bảng tỉnh/xã.

## Nạp bộ 63 tỉnh vào PostgreSQL

Từ thư mục backend, dùng kết nối DATABASE_URL hiện có trong .env:

```sh
python import_legacy_provinces.py
python import_legacy_provinces.py --apply
```

Lệnh đầu chỉ kiểm tra; --apply chỉ nạp vào schema vn_admin_legacy. Bản đã nạp đúng phiên bản/số lượng được giữ nguyên khi chạy lại; schema thiếu hoặc khác phiên bản báo lỗi thay vì tự xóa. Schema public không được sửa. Nếu import lỗi, transaction rollback phần nhập. Không tự chạy DROP schema để thử lại.

Kết quả tìm kiếm có storage=postgresql khi đọc SQL; storage=bundled_catalogue là đường dự phòng. Có dữ liệu tên hành chính trong database không đồng nghĩa đã có tọa độ GIS của đơn vị.
