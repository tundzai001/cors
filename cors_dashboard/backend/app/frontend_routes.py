"""Frontend page responses and restricted public asset serving."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


router = APIRouter()


@router.get("/login", include_in_schema=False)
async def serve_login():
    return RedirectResponse(url="/", status_code=307)

@router.get("/monitoring", include_in_schema=False)
async def serve_monitoring():
    return FileResponse("../frontend/monitoring.html")

@router.get("/users", include_in_schema=False)
async def serve_users():
    return FileResponse("../frontend/users.html")

@router.get("/api-keys", include_in_schema=False)
@router.get("/api-keys.html", include_in_schema=False)
async def serve_api_keys():
    # Navigation cannot send the JWT stored by ApiClient. Only the gate is public;
    # the page itself is fetched through the authenticated admin endpoint.
    return FileResponse("../frontend/api-keys-gate.html", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "frame-ancestors 'none'",
    })

@router.get("/security", include_in_schema=False)
async def serve_security():
    return FileResponse("../frontend/security.html")

@router.get("/operations", include_in_schema=False)
async def serve_operations():
    return FileResponse("../frontend/operations.html")

@router.get("/zalo", include_in_schema=False)
async def serve_zalo():
    return FileResponse("../frontend/zalo.html")

@router.get("/rovers-global", include_in_schema=False)
async def serve_rovers_global():
    return FileResponse("../frontend/rovers-global.html")

@router.get("/network-status", include_in_schema=False)
@router.get("/coverage", include_in_schema=False)
@router.get("/blog", include_in_schema=False)
@router.get("/blog/{rest_of_path:path}", include_in_schema=False)
@router.get("/station/{rest_of_path:path}", include_in_schema=False)
@router.get("/api/docs", include_in_schema=False)
async def serve_spa():
    return FileResponse("../frontend/index.html")

# === STATIC FILES ===
class PublicAssetFiles(StaticFiles):
    def __init__(self, directory: str, suffixes: tuple[str, ...], nested: bool = False):
        super().__init__(directory=directory)
        self.suffixes = suffixes
        self.nested = nested

    async def get_response(self, path: str, scope):
        if (not self.nested and ("/" in path or "\\" in path)) or not path.lower().endswith(self.suffixes):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


@router.get("/", include_in_schema=False)
@router.get("/index.html", include_in_schema=False)
async def serve_index():
    return FileResponse("../frontend/index.html")


@router.get("/robots.txt", include_in_schema=False)
async def serve_robots():
    return FileResponse("../frontend/robots.txt")


@router.get("/{page_name}.html", include_in_schema=False)
async def serve_public_html(page_name: str):
    if page_name not in {"operations", "rovers-global", "security", "users", "zalo"}:
        raise HTTPException(status_code=404)
    return FileResponse(f"../frontend/{page_name}.html")


