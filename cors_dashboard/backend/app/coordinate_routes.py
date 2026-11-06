"""Public 2D/3D coordinate conversion. No user data is persisted."""
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, FiniteFloat, model_validator
from pyproj import CRS, Transformer
from pyproj.crs import GeocentricCRS
from pyproj.aoi import AreaOfInterest
from pyproj.exceptions import ProjError

router = APIRouter(prefix="/api/coordinates", tags=["Coordinates"])
DATA = Path(__file__).with_name("data")
# EPSG TM-3 definitions, keyed by central meridian. No inferred provincial CRS.
TM3 = dict(zip(
    [103, 104, 104.5, 104.75, 105, 105.5, 105.75, 106, 106.25, 106.5, 107, 107.25, 107.5, 107.75, 108, 108.25, 108.5],
    [9205, 9206, 9207, 9208, 5897, 9209, 9210, 9211, 9212, 9213, 9214, 9215, 9216, 5899, 5898, 9217, 9218],
))
EPSGS = [4326, 4756, 32648, 32649, 3405, 3406, 3857, 4978, *TM3.values()]


def get_crs(identifier):
    if identifier == "VN2000:ECEF":
        return GeocentricCRS(name="VN-2000 geocentric", datum=CRS(4756).datum)
    return CRS(identifier)


@lru_cache(maxsize=1)
def catalog():
    provinces = json.loads((DATA / "vietnam_units.json").read_text(encoding="utf-8"))
    legacy = json.loads((DATA / "coordinate_provinces.json").read_text(encoding="utf-8"))
    systems = []
    for identifier in [*(f"EPSG:{code}" for code in EPSGS), "VN2000:ECEF"]:
        crs = get_crs(identifier)
        code = int(identifier.split(":")[1]) if identifier.startswith("EPSG:") else None
        item = dict(id=identifier, name=crs.name, geographic=crs.is_geographic,
                    geocentric=crs.is_geocentric,
                    projection=None)
        if code in TM3.values() or code in (32648, 32649, 3405, 3406):
            params = {p.name: p.value for p in crs.coordinate_operation.params}
            item["projection"] = dict(
                central_meridian=params["Longitude of natural origin"],
                zone_width=3 if code in TM3.values() else 6,
                scale_factor=params["Scale factor at natural origin"],
            )
        if code in TM3.values():
            item["name"] = f"VN-2000 / TM-3 · kinh tuyến {item['projection']['central_meridian']:g}°"
        systems.append(item)
    return {
        "systems": systems,
        "provinces34": [dict(code=p["Code"], name=p["Name"]) for p in provinces],
        "provinces63": [dict(**p, crs=f"EPSG:{TM3[p['central_meridian']]}") for p in legacy],
    }


@router.get("/catalog")
def get_catalog():
    return catalog()


class ConversionRequest(BaseModel):
    source: str = Field(max_length=16)
    target: str = Field(max_length=16)
    # Geographic: latitude, longitude[, h]. Projected: northing, easting[, h].
    # Geocentric: X, Y, Z. Keep the previous 2D endpoint contract intact.
    points: list[tuple[FiniteFloat, FiniteFloat] | tuple[FiniteFloat, FiniteFloat, FiniteFloat]] = Field(min_length=1, max_length=1000)
    source_height: Literal["ellipsoidal", "orthometric"] = "ellipsoidal"
    target_height: Literal["ellipsoidal", "orthometric"] = "ellipsoidal"
    source_geoid: FiniteFloat | None = None
    target_geoid: FiniteFloat | None = None

    @model_validator(mode="after")
    def validate_dimensions(self):
        dimensions = {len(point) for point in self.points}
        if len(dimensions) != 1:
            raise ValueError("Không trộn điểm có độ cao và điểm không có độ cao.")
        for side in ("source", "target"):
            if getattr(self, f"{side}_height") == "orthometric":
                if dimensions != {3} or getattr(self, f"{side}_geoid") is None:
                    raise ValueError("Độ cao H cần điểm 3D và độ lệch geoid N của hệ tương ứng.")
        return self


@lru_cache(maxsize=128)
def transformer(source, target, dimensions=2):
    source_crs, target_crs = get_crs(source), get_crs(target)
    if dimensions == 3:
        # Like land_slide_web's LLH -> ECEF -> Helmert -> LLH -> NEh flow,
        # promoting BOTH CRS makes PROJ transform h instead of passing it through.
        source_crs, target_crs = source_crs.to_3d(), target_crs.to_3d()
    return Transformer.from_crs(source_crs, target_crs, always_xy=True, allow_ballpark=False,
                                area_of_interest=AreaOfInterest(102, 8, 110, 24))


@router.post("/convert")
def convert(request: ConversionRequest):
    allowed = {s["id"] for s in catalog()["systems"]}
    if request.source not in allowed or request.target not in allowed:
        raise HTTPException(422, "Hệ tọa độ không được hỗ trợ.")
    source, target = get_crs(request.source), get_crs(request.target)
    dimensions = len(request.points[0])
    if source.is_geocentric or target.is_geocentric:
        if dimensions != 3 or request.source_height != "ellipsoidal" or request.target_height != "ellipsoidal":
            raise HTTPException(422, "XYZ địa tâm cần đủ ba tọa độ; tùy chọn độ cao H không áp dụng cho XYZ.")
    if source.is_geographic and any(abs(p[0]) > 90 or abs(p[1]) > 180 for p in request.points):
        raise HTTPException(422, "Vĩ độ phải trong ±90°, kinh độ trong ±180°.")
    try:
        operation = transformer(request.source, request.target, dimensions)
        first = [p[0 if source.is_geocentric else 1] for p in request.points]
        second = [p[1 if source.is_geocentric else 0] for p in request.points]
        coordinates = [first, second]
        if dimensions == 3:
            coordinates.append([p[2] + (request.source_geoid if request.source_height == "orthometric" else 0)
                                for p in request.points])
        transformed = list(operation.transform(*coordinates, errcheck=True))
        if not target.is_geocentric:
            transformed[0], transformed[1] = transformed[1], transformed[0]
        if dimensions == 3 and request.target_height == "orthometric":
            transformed[2] = [h - request.target_geoid for h in transformed[2]]
        points = list(zip(*transformed))
        if not all(math.isfinite(v) for point in points for v in point):
            raise ValueError("Non-finite result")
        used = operation
        try:
            used = operation.get_last_used_operation()
        except ProjError:
            pass  # Identity operations have no last-used operation.
        return {"points": points, "source": request.source, "target": request.target,
                "operation": used.description,
                "accuracy_m": used.accuracy if used.accuracy >= 0 else None,
                "dimensions": dimensions,
                "note": ("Đã chuyển tọa độ 3D, gồm độ cao ellipsoid qua phép đổi datum. H chỉ được tính theo N bạn cung cấp; không tự áp mô hình geoid."
                         if dimensions == 3 else "Không nhập độ cao: chỉ chuyển tọa độ ngang.")
                        + " Chọn đúng vùng đo và kinh tuyến theo hồ sơ; kết quả không thay thế hiệu chỉnh địa phương."}
    except (ProjError, ValueError) as exc:
        raise HTTPException(422, "Không thể chuyển đổi. Kiểm tra hệ tọa độ, thứ tự trục và phạm vi tọa độ.") from exc


class AutoBaseLocationRequest(BaseModel):
    lat: FiniteFloat = Field(ge=-90, le=90)
    lon: FiniteFloat = Field(ge=-180, le=180)
    alt: FiniteFloat


@router.post('/auto-base-location')
def auto_base_location(request: AutoBaseLocationRequest):
    from .auto_base_location import resolve_location
    return resolve_location(float(request.lat), float(request.lon), float(request.alt))


class DisplayProjection(BaseModel):
    l0_deg: FiniteFloat = Field(ge=100, le=112)
    k0: FiniteFloat = Field(ge=0.999, le=1.001)
    false_easting: FiniteFloat = Field(default=500000, ge=-1e8, le=1e8)
    false_northing: FiniteFloat = Field(default=0, ge=-1e8, le=1e8)

    def crs(self):
        return CRS.from_dict(dict(proj='tmerc', lat_0=0, lon_0=self.l0_deg, k_0=self.k0,
                                  x_0=self.false_easting, y_0=self.false_northing,
                                  ellps='WGS84', units='m'))


class ReprojectionRequest(BaseModel):
    north: FiniteFloat = Field(ge=-2e7, le=2e7)
    east: FiniteFloat = Field(ge=-2e7, le=2e7)
    h: FiniteFloat
    source: DisplayProjection
    target_l0_deg: FiniteFloat = Field(ge=100, le=112)
    target_k0: Literal[0.9999, 0.9996]


class AutoBaseDatum(BaseModel):
    dX: FiniteFloat
    dY: FiniteFloat
    dZ: FiniteFloat
    rX: FiniteFloat
    rY: FiniteFloat
    rZ: FiniteFloat
    kT: FiniteFloat = Field(gt=0)
    resB: FiniteFloat
    resL: FiniteFloat
    resH: FiniteFloat
    resN: FiniteFloat
    resE: FiniteFloat
    resHS: FiniteFloat


class AutoBaseRecalculation(BaseModel):
    input_coord: AutoBaseLocationRequest
    parameters: AutoBaseDatum
    projection: DisplayProjection
    apply_residual: bool


def _coordinate_frame(lon, lat, height, x, y, z, rx, ry, rz, scale):
    operation = Transformer.from_pipeline(
        '+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad '
        '+step +proj=cart +ellps=WGS84 '
        f'+step +proj=helmert +convention=coordinate_frame +x={x} +y={y} +z={z} '
        f'+rx={rx} +ry={ry} +rz={rz} +s={(scale - 1) * 1e6} '
        '+step +inv +proj=cart +ellps=WGS84 '
        '+step +proj=unitconvert +xy_in=rad +xy_out=deg'
    )
    return operation.transform(lon, lat, height, errcheck=True)


@router.post('/recalculate-auto-base')
def recalculate_auto_base(request: AutoBaseRecalculation):
    """ITRF2vn2000.txt: llh2NEH followed by vn20002llh using the selected projection."""
    point, datum, projection = request.input_coord, request.parameters, request.projection
    try:
        lon, lat, h = _coordinate_frame(point.lon, point.lat, point.alt,
            datum.dX, datum.dY, datum.dZ, datum.rX, datum.rY, datum.rZ, datum.kT)
        if request.apply_residual:
            lon += math.degrees(datum.resL)
            lat += math.degrees(datum.resB)
            h += datum.resH
        geographic = CRS.from_epsg(4326)
        projected = projection.crs()
        east, north = Transformer.from_crs(geographic, projected, always_xy=True).transform(lon, lat, errcheck=True)
        east += datum.resE
        north += datum.resN
        h += datum.resHS
        lon, lat = Transformer.from_crs(projected, geographic, always_xy=True).transform(east, north, errcheck=True)
        lon, lat, alt = _coordinate_frame(lon, lat, h, -191.90441429, -39.30318279, -111.45032835,
                                         -0.00928836, 0.01975479, -0.00427372, 1.000000252906278)
        if not all(map(math.isfinite, (east, north, h, lat, lon, alt))) or not -1000 <= alt <= 100000:
            raise ValueError('Invalid Auto Base coordinates')
    except (ProjError, ValueError) as exc:
        raise HTTPException(422, 'Không tính được NEh và LLH từ dữ liệu phiên Auto Base.') from exc
    return dict(projected_coord=dict(north=north, east=east, h=h),
                calculated_coord=dict(lat=lat, lon=lon, alt=alt),
                central_meridian_deg=projection.l0_deg, k0=projection.k0)


@router.post('/reproject-display')
def reproject_display(request: ReprojectionRequest):
    """Change only the map projection of an existing VN-2000 result; no datum or receiver writes."""
    target = request.source.model_copy(update=dict(l0_deg=request.target_l0_deg, k0=request.target_k0))
    try:
        operation = Transformer.from_crs(request.source.crs(), target.crs(), always_xy=True)
        east, north = operation.transform(request.east, request.north, errcheck=True)
        if not math.isfinite(north) or not math.isfinite(east):
            raise ValueError('Non-finite projected coordinates')
    except (ProjError, ValueError) as exc:
        raise HTTPException(422, 'Không tính được múi hiển thị. Kiểm tra tọa độ gốc và L₀/k₀.') from exc
    return dict(projected_coord=dict(north=north, east=east, h=request.h),
                central_meridian_deg=target.l0_deg, k0=target.k0)
