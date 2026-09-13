from fastapi import APIRouter, HTTPException, Query

from app.admin_regions import get_admin_region_provider


router = APIRouter(prefix="/api/admin-regions", tags=["admin-regions"])


@router.get("/search")
def search_admin_regions(
    name: str | None = None,
    adcode: str | None = None,
    level: str | None = None,
    parent: str | None = None,
) -> dict:
    return get_admin_region_provider().search(name=name, adcode=adcode, level=level, parent=parent)


@router.get("/source/status")
def get_admin_region_source_status() -> dict:
    provider = get_admin_region_provider()
    return {
        "available": provider.available,
        "source": provider.source_name,
        "record_count": len(provider.index().all()) if provider.available else 0,
    }


@router.get("/{region_id}/boundary")
def get_admin_region_boundary(region_id: str) -> dict:
    feature = get_admin_region_provider().boundary_geojson(region_id)
    if feature is None:
        raise HTTPException(status_code=404, detail={"code": "ADMIN_BOUNDARY_UNAVAILABLE"})
    return feature
