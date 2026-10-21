# Coordinate conversion catalog

`coordinate_provinces.json` contains the 63 historical provincial projection
regions, their TM-3 central meridians, and their corresponding codes in the
existing 34-province `vietnam_units.json` snapshot. It is a lookup catalog, not
historical boundary geometry and not a replacement for the administrative DB.
No database migration or reseeding is needed.

Central meridians are transcribed from Appendix 01 of Circular
26/2024/TT-BTNMT (historical 63-province table):

- [Ministry PDF](https://dlrpd.monre.gov.vn/noidung/Lists/VBQPPL/Attachments/186/26-2024-tt-btnmt_Signed.pdf)
- [Searchable reproduction of the issued circular](https://files.thuvienphapluat.vn/uploads/DOC2HTM/VB_637676.htm)

The grouping uses the names and codes in the existing 34-province snapshot.
Selecting a merged province deliberately requires selecting the historical
region. This grouping does not prescribe a new province-wide central meridian.
Huế is listed as Thừa Thiên Huế in the historical catalog.

## Conversion contract

Page: `/coordinates` (also `/coordinates.html`). Public endpoints:
`GET /api/coordinates/catalog`, `POST /api/coordinates/convert`.

```json
{
  "source": "EPSG:5897",
  "target": "EPSG:4326",
  "points": [[2326345.3117, 588591.9440]]
}
```

The API accepts 1–1000 pairs. Geographic pairs are **latitude, longitude** in
decimal degrees. Projected pairs are **northing, easting** in metres (Vietnamese
X, Y). The service reverses those pairs internally for PROJ's `always_xy` order.
Only the 24 catalog entries are accepted; arbitrary PROJ expressions are rejected.
The service does not persist submitted points.

Supported entries: WGS 84 geographic, VN-2000 geographic, WGS 84 UTM 48N/49N,
VN-2000 UTM 48N/49N, Web Mercator, and 17 VN-2000 TM-3 meridians.
The EPSG definitions supply the projection and datum operations; no hand-written
projection formula or provincial Helmert parameters are introduced.

PROJ selects operations with a Vietnam area of interest and ballpark operations
disabled. This does not enforce the geographical area of use of every CRS.
Users must select a CRS appropriate to the location and survey documentation.
The typical VN-2000/WGS 84 operation is EPSG:6960, with published accuracy of 1 m.
Displayed decimal precision and numerical round-trip agreement do not establish
survey accuracy. Local calibration, dynamic reference frames/epochs, heights and
geoid conversion are outside this tool's scope.

## Development and validation

Install backend requirements, including `pyproj==3.7.2`, before starting the app.
Build frontend assets from the repository root:

```sh
node scripts/build_frontend_assets.mjs
cd cors_dashboard/backend
python -m unittest tests.test_coordinates
```

Checks cover catalog completeness, all EPSG TM-3 projection parameters, a fixed
Hanoi numerical regression, datum-shift presence, batch round trips through all
supported systems, and invalid inputs. The Hanoi regression is a PROJ/EPSG
numerical fixture, not an independently surveyed control point.
