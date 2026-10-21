"""Public, horizontal coordinate conversion. No user data is persisted."""
import json
import math
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, FiniteFloat
from pyproj import CRS, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.exceptions import ProjError

router = APIRouter(prefix="/api/coordinates", tags=["Coordinates"])
DATA = Path(__file__).with_name("data")
# EPSG TM-3 definitions, keyed by central meridian. No inferred provincial CRS.
TM3 = dict(zip(
    [103, 104, 104.5, 104.75, 105, 105.5, 105.75, 106, 106.25, 106.5, 107, 107.25, 107.5, 107.75, 108, 108.25, 108.5],
    [9205, 9206, 9207, 9208, 5897, 9209, 9210, 9211, 9212, 9213, 9214, 9215, 9216, 5899, 5898, 9217, 9218],
))
EPSGS = [4326, 4756, 32648, 32649, 3405, 3406, 3857, *TM3.values()]


@lru_cache(maxsize=1)
def catalog():
    provinces = json.loads((DATA / "vietnam_units.json").read_text(encoding="utf-8"))
    legacy = json.loads((DATA / "coordinate_provinces.json").read_text(encoding="utf-8"))
    systems = []
    for code in EPSGS:
        crs = CRS(code)
        item = dict(id=f"EPSG:{code}", name=crs.name, geographic=crs.is_geographic,
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
    # Geographic: latitude, longitude. Projected: northing, easting.
    points: list[tuple[FiniteFloat, FiniteFloat]] = Field(min_length=1, max_length=1000)


@lru_cache(maxsize=128)
def transformer(source, target):
    return Transformer.from_crs(source, target, always_xy=True, allow_ballpark=False,
                                area_of_interest=AreaOfInterest(102, 8, 110, 24))


@router.post("/convert")
def convert(request: ConversionRequest):
    allowed = {s["id"] for s in catalog()["systems"]}
    if request.source not in allowed or request.target not in allowed:
        raise HTTPException(422, "Hệ tọa độ không được hỗ trợ.")
    source, target = CRS(request.source), CRS(request.target)
    if source.is_geographic and any(abs(a) > 90 or abs(b) > 180 for a, b in request.points):
        raise HTTPException(422, "Vĩ độ phải trong ±90°, kinh độ trong ±180°.")
    try:
        operation = transformer(request.source, request.target)
        # UI uses latitude/longitude and X North/Y East; PROJ always_xy uses reverse order.
        east, north = operation.transform([p[1] for p in request.points],
                                           [p[0] for p in request.points], errcheck=True)
        points = list(zip(north, east))
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
                "note": "Chỉ chuyển tọa độ ngang. Không chuyển độ cao. Với VN-2000, chọn đúng vùng đo và kinh tuyến trên hồ sơ; kết quả không thay thế hiệu chỉnh địa phương."}
    except (ProjError, ValueError) as exc:
        raise HTTPException(422, "Không thể chuyển đổi. Kiểm tra hệ tọa độ, thứ tự trục và phạm vi tọa độ.") from exc
