# Vietnam GIS & Administrative Units Source Data

Thư mục này lưu trữ bản snapshot dữ liệu GIS và đơn vị hành chính Việt Nam nguyên bản từ upstream để đối chiếu và theo dõi khi có cập nhật trong tương lai.

## 1. Nguồn Dữ Liệu (Upstream Source)
- **Repository gốc**: [https://github.com/thanglequoc/vietnamese-provinces-database](https://github.com/thanglequoc/vietnamese-provinces-database)
- **Phiên bản snapshot**: `v5.2.0`
- **Căn cứ pháp lý hành chính**: Nghị quyết số `388/NQ-UBTVQH16` của Ủy ban Thường vụ Quốc hội về sắp xếp các đơn vị hành chính cấp huyện, cấp xã.
- **Ngày đồng bộ**: `14/10/2026`
- **Độ chính xác toạ độ**: Toạ độ nguyên gốc 100% (Raw un-rounded geodetic precision), không làm tròn số thập phân.

---

## 2. Danh Mục Tệp (File Inventory & Checksums)

| Tệp tin | Kích thước | SHA-256 Checksum | Mô tả |
| :--- | :--- | :--- | :--- |
| `vietnam_provinces.geojson` | ~2.25 MB | `f5b47e29247bb27ee618f945f5ff15e878ce3087a8ddf813c63016aad6931abd` | Toạ độ ranh giới 34 tỉnh/thành phố trực thuộc Trung ương |
| `vietnam_units.json` | ~2.5 MB | `ca73d43d2d40ee2572937c7d396d1d17ac6c9a76c1ae3ff94620ce8dacd701ca` | Cấu trúc phân cấp 63 tỉnh/thành, 696 quận/huyện/thị xã và 10.035 xã/phường/thị trấn kèm toạ độ và mã bưu chính |
| `postgres_CreateTables_vn_units.sql` | 2.3 KB | `9c9496fcef128ab2000a0314fe901608a11634d9743a97470e315942d5388d3d` | DDL tạo bảng PostgreSQL (regions, units, provinces, wards) |
| `postgres_ImportData_vn_units.sql` | 343 KB | `46b59e96ca105edf090005fb4e8cc6373cfb3d66e657e8d5d763f0cf5f8fbda4` | Script INSERT toàn bộ đơn vị hành chính vào PostgreSQL |
| `postgresql_CreateGISTables.sql` | 1.3 KB | `db69896fd3328de10114e6fc4a771f33fb2cb5724138216c3711c5723c3cc1cb` | DDL tích hợp không gian địa lý PostGIS |
| `version_manifest.json` | 1.5 KB | - | Manifest mô tả chi tiết phiên bản và mã băm SHA-256 |

---

## 3. Cách Kiểm Tra & Cập Nhật Khi Có Phiên Bản GIS Mới

Khi Nhà nước ban hành nghị quyết mới về sáp nhập hoặc chia tách địa giới hành chính, upstream repo `thanglequoc/vietnamese-provinces-database` sẽ phát hành bản cập nhật (ví dụ: `v5.3.0`):

1. **Kiểm tra phiên bản mới trên GitHub**:
   ```bash
   curl -s https://api.github.com/repos/thanglequoc/vietnamese-provinces-database/releases/latest | grep "tag_name"
   ```

2. **Nếu có bản mới, tải về thư mục `src/gis/`**:
   - Tải file GeoJSON và JSON mới.
   - Cập nhật SHA-256 trong `version_manifest.json` và `README.md`.

3. **Đồng bộ sang runtime backend**:
   ```bash
   cp cors_dashboard/src/gis/vietnam_provinces.geojson cors_dashboard/backend/app/data/
   cp cors_dashboard/src/gis/vietnam_units.json cors_dashboard/backend/app/data/
   cp cors_dashboard/src/gis/*.sql cors_dashboard/backend/app/data/
   ```

4. **Chạy lại seeder để cập nhật DB PostgreSQL**:
   ```bash
   cd cors_dashboard/backend
   python seed_provinces.py
   ```
