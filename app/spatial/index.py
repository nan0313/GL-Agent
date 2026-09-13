from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt


@dataclass(frozen=True)
class SpatialObject:
    object_id: str
    object_name: str
    object_type: str
    longitude: float
    latitude: float
    layer_id: str | None = None
    properties: dict[str, object] | None = None
    feature_ref: str | None = None

    def to_result(self, distance: float) -> dict[str, object]:
        return {
            "object_id": self.object_id,
            "object_name": self.object_name,
            "object_type": self.object_type,
            "distance": round(distance, 2),
            "longitude": self.longitude,
            "latitude": self.latitude,
            "layer_id": self.layer_id,
            "properties": self.properties or {},
            "feature_ref": self.feature_ref,
        }


class SpatialObjectIndex:
    def __init__(self, objects: list[SpatialObject] | None = None) -> None:
        self.objects = objects or []

    def query_radius(
        self,
        longitude: float,
        latitude: float,
        radius: float,
        object_type: str | None = None,
        exclude_object_id: str | None = None,
    ) -> list[dict[str, object]]:
        matches: list[dict[str, object]] = []
        for item in self.objects:
            if exclude_object_id and item.object_id == exclude_object_id:
                continue
            if object_type and item.object_type != object_type:
                continue
            distance = haversine_meters(longitude, latitude, item.longitude, item.latitude)
            if distance <= radius:
                matches.append(item.to_result(distance))
        return sorted(matches, key=lambda item: float(item["distance"]))


class RealSpatialObjectIndex(SpatialObjectIndex):
    source = "webgl"

    @classmethod
    def build_from_webgl_objects(cls, objects: list[dict[str, object]] | None) -> "RealSpatialObjectIndex":
        spatial_objects: list[SpatialObject] = []
        for item in objects or []:
            normalized = normalize_webgl_spatial_object(item)
            if normalized is not None:
                spatial_objects.append(normalized)
        return cls(spatial_objects)


def normalize_webgl_spatial_object(item: dict[str, object]) -> SpatialObject | None:
    object_id = pick_first(item.get("object_id"), item.get("id"))
    if not object_id:
        return None
    longitude = pick_float(item.get("longitude"), item.get("lon"), item.get("lng"))
    latitude = pick_float(item.get("latitude"), item.get("lat"))
    if longitude is None or latitude is None:
        properties = item.get("properties")
        if isinstance(properties, dict):
            longitude = pick_float(properties.get("longitude"), properties.get("lon"), properties.get("lng"))
            latitude = pick_float(properties.get("latitude"), properties.get("lat"))
    if longitude is None or latitude is None:
        return None
    properties_value = item.get("properties")
    return SpatialObject(
        object_id=str(object_id),
        object_name=str(pick_first(item.get("object_name"), item.get("name"), object_id)),
        object_type=str(pick_first(item.get("object_type"), item.get("type"), "unknown")),
        longitude=longitude,
        latitude=latitude,
        layer_id=pick_optional_str(item.get("layer_id")),
        properties=properties_value if isinstance(properties_value, dict) else {},
        feature_ref=pick_optional_str(item.get("feature_ref")),
    )


def pick_first(*values: object) -> object | None:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def pick_optional_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def pick_float(*values: object) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def haversine_meters(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    earth_radius_m = 6371008.8
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * earth_radius_m * asin(sqrt(a))


default_spatial_index = SpatialObjectIndex(
    [
        SpatialObject(
            object_id="A001",
            object_name="测试杆塔A001",
            object_type="tower",
            longitude=116.390,
            latitude=39.900,
            layer_id="mock_spatial",
        ),
        SpatialObject(
            object_id="A002",
            object_name="测试杆塔A002",
            object_type="tower",
            longitude=116.392,
            latitude=39.902,
            layer_id="mock_spatial",
        ),
        SpatialObject(
            object_id="B001",
            object_name="测试馈线B001",
            object_type="feeder",
            longitude=116.395,
            latitude=39.904,
            layer_id="mock_spatial",
        ),
    ]
)
