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

The API accepts 1–1000 points, with either two or three values consistently across
the batch. Geographic points are **latitude, longitude[, h]** in decimal degrees
and metres. Projected points are **northing, easting[, h]** in metres (Vietnamese
X, Y). Geocentric points are **X, Y, Z** in metres and require three coordinates.
The service reverses horizontal geographic/projected pairs internally for PROJ's
`always_xy` order, but preserves the order of geocentric XYZ.
Only catalog identifiers are accepted; arbitrary PROJ expressions are rejected.
The service does not persist submitted points.

Supported entries: WGS 84 geographic, VN-2000 geographic, WGS 84 UTM 48N/49N,
VN-2000 UTM 48N/49N, Web Mercator, 17 VN-2000 TM-3 meridians, WGS 84 geocentric
(EPSG:4978), and VN-2000 geocentric (`VN2000:ECEF`, constructed using the VN-2000
datum in PROJ).
The EPSG definitions supply the projection and datum operations; no hand-written
projection formula or provincial Helmert parameters are introduced.

PROJ selects operations with a Vietnam area of interest and ballpark operations
disabled. This does not enforce the geographical area of use of every CRS.
Users must select a CRS appropriate to the location and survey documentation.
The typical VN-2000/WGS 84 operation is EPSG:6960, with published accuracy of 1 m.
Displayed decimal precision and numerical round-trip agreement do not establish
survey accuracy. Local calibration and dynamic reference frames/epochs remain
outside this tool's scope.

## Height conversion and land_slide_web comparison

For three-value inputs, both CRS are promoted to 3D before transformation.
This transforms ellipsoidal height through geocentric XYZ and the datum Helmert
operation, rather than silently passing the height through a 2D transform.
The route follows the same mathematical flow as
`land_slide_web/backend/processors/geodetic.py`, using PROJ for the projection
and inverse operation. It does not import that sibling project at runtime.

Reference: WGS 84 latitude 20.845°, longitude 106.688°, h=30 m,
VN-2000 TM-3 central meridian 105°45′, k0=0.9999:

- land_slide_web default parameters: N=2306078.783347 m,
  E=597421.970766 m, h=51.733302 m.
- PROJ 3D: N=2306078.783299 m, E=597421.970804 m, h=51.733288 m.

This is a numerical comparison of default transformations, not evidence of
sub-millimetre survey accuracy. Local residual corrections configured in
land_slide_web are not automatically loaded into this tool.

`source_height` and `target_height` default to `ellipsoidal`. The optional
`orthometric` mode requires an explicit finite `source_geoid` or `target_geoid`,
respectively, using h=H+N before the datum transform and H=h−N afterwards.
The supplied N is constant across that request; use separate requests for
points with different known N values. There is no automatic geoid download,
interpolation, or conversion to the Hòn Dấu national height system.
Geocentric conversions require ellipsoidal mode.

## User interface

The page uses the shared navbar and NavigationManager. The converter appears
after existing management links under **Công cụ**, with active-page detection.
The homepage link is **Công cụ**, after the existing network/blog/API links.
Unauthenticated visitors receive public navigation; authenticated users retain
their role-specific management links.

Main choices are **WGS 84 / VN-2000**, followed by geographic, projected or
geocentric representation. Projection settings and height/geoid options are
separate controls. The 63-province catalog remains the default; the 34-province
catalog requires choosing an old region for merged provinces. EPSG identifiers
are internal rather than a long list presented as distinct WGS 84 systems.

Each point uses three labelled numeric fields. Geographic fields are displayed
as **longitude, latitude, height** and reordered when sent to the API to preserve
its existing latitude/longitude contract. Blank height selects the 2D path;
height is never fabricated as zero. Multiple points use additional rows of
fields, not whitespace-delimited text. Swapping a computed result preserves its
datum, representation and height options.

## Development and validation

Install backend requirements, including `pyproj==3.7.2`, before starting the app.
Build frontend assets from the repository root:

```sh
node scripts/build_frontend_assets.mjs
node scripts/coordinate_navigation_selftest.js
cd cors_dashboard/backend
python -m unittest tests.test_coordinates
```

Checks cover catalog completeness, all EPSG TM-3 projection parameters, a fixed
Hanoi numerical regression, datum-shift presence, batch round trips through all
supported systems, 3D comparison with land_slide_web, height/geoid validation,
geocentric round trips, and invalid inputs. The Hanoi regression is a PROJ/EPSG
numerical fixture, not an independently surveyed control point.
