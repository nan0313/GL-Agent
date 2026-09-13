from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import struct
from typing import Any

from app.admin_regions.index import AdminRegionIndex, AdminRegionRecord, normalize_region_name


_PROVINCE_NAMES = {
    "11": "北京市", "12": "天津市", "13": "河北省", "14": "山西省", "15": "内蒙古自治区",
    "21": "辽宁省", "22": "吉林省", "23": "黑龙江省", "31": "上海市", "32": "江苏省",
    "33": "浙江省", "34": "安徽省", "35": "福建省", "36": "江西省", "37": "山东省",
    "41": "河南省", "42": "湖北省", "43": "湖南省", "44": "广东省", "45": "广西壮族自治区",
    "46": "海南省", "50": "重庆市", "51": "四川省", "52": "贵州省", "53": "云南省",
    "54": "西藏自治区", "61": "陕西省", "62": "甘肃省", "63": "青海省", "64": "宁夏回族自治区",
    "65": "新疆维吾尔自治区", "71": "台湾省", "81": "香港特别行政区", "82": "澳门特别行政区",
}


def _default_shp_path() -> Path | None:
    configured = os.getenv("ADMIN_REGION_SHP_PATH", "").strip()
    if configured:
        return Path(configured)
    workspace = Path(__file__).resolve().parents[2]
    candidates = [
        workspace / "vendor" / "webgl" / "ApplicationVue" / "dist" / "datas" / "BOUNT_poly.shp",
        workspace.parent / "EV-Globe-WebGL" / "ApplicationVue" / "dist" / "datas" / "BOUNT_poly.shp",
    ]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _default_province_geojson_path() -> Path | None:
    configured = os.getenv("ADMIN_REGION_PROVINCE_GEOJSON_PATH", "").strip()
    if configured:
        return Path(configured)
    workspace = Path(__file__).resolve().parents[2]
    candidate = workspace / "vendor" / "webgl" / "ApplicationVue" / "dist" / "datas" / "\u7701\u533a\u5c4a.json"
    return candidate if candidate.exists() else None


class LocalShapefileAdminRegionProvider:
    """Reads the WebGL project's existing SHP/DBF without adding external services."""

    source_name = "webgl_local_bount_poly"

    def __init__(
        self,
        shp_path: Path | str | None = None,
        province_geojson_path: Path | str | None = None,
    ) -> None:
        self.shp_path = Path(shp_path) if shp_path else _default_shp_path()
        self.dbf_path = self.shp_path.with_suffix(".dbf") if self.shp_path else None
        self.province_geojson_path = Path(province_geojson_path) if province_geojson_path else _default_province_geojson_path()
        self._shape_offsets: dict[str, tuple[int, int]] = {}
        self._province_features: dict[str, dict[str, Any]] = {}
        self._index: AdminRegionIndex | None = None

    @property
    def available(self) -> bool:
        return self._city_source_available or self._province_source_available

    @property
    def _city_source_available(self) -> bool:
        return bool(self.shp_path and self.shp_path.exists() and self.dbf_path and self.dbf_path.exists())

    @property
    def _province_source_available(self) -> bool:
        return bool(self.province_geojson_path and self.province_geojson_path.exists())

    def index(self) -> AdminRegionIndex:
        if self._index is None:
            self._index = AdminRegionIndex(self._load_records()) if self.available else AdminRegionIndex()
        return self._index

    def search(self, **kwargs: Any) -> dict[str, Any]:
        if not self.available:
            return {"success": False, "code": "ADMIN_REGION_SOURCE_UNAVAILABLE", "candidates": []}
        result = self.index().find(**kwargs)
        result["source_available"] = True
        return result

    def boundary_geojson(self, region_id: str) -> dict[str, Any] | None:
        self.index()
        if region_id in self._province_features:
            return self._province_features[region_id]
        if not self.available or region_id not in self._shape_offsets:
            return None
        offset, content_bytes = self._shape_offsets[region_id]
        with self.shp_path.open("rb") as stream:  # type: ignore[union-attr]
            stream.seek(offset)
            payload = stream.read(content_bytes)
        geometry = self._polygon_geometry(payload)
        record = next((item for item in self.index().all() if item.region_id == region_id), None)
        if not geometry or not record:
            return None
        return {
            "type": "Feature",
            "id": record.region_id,
            "properties": record.public_dict(),
            "geometry": geometry,
        }

    def _load_records(self) -> list[AdminRegionRecord]:
        records: list[AdminRegionRecord] = []
        records.extend(self._load_province_records())
        if not self._city_source_available:
            return records
        rows = self._read_dbf()
        offsets = self._read_shape_offsets(len(rows))
        for row in rows:
            shape_index = int(row.pop("_record_index"))
            adcode = str(row.get("ADCODE99") or "").strip()
            source_name = str(row.get("NAME99") or "").strip()
            if not source_name or not adcode:
                continue
            formal_name, level, aliases = self._classify_name(source_name, row)
            region_id = f"admin:{adcode}"
            longitude = self._float(row.get("CENTROID_X"))
            latitude = self._float(row.get("CENTROID_Y"))
            center = None
            center_source = None
            if longitude is not None and latitude is not None:
                center = {"longitude": longitude, "latitude": latitude, "height": 0.0}
                center_source = "data_native_center"
            elif shape_index < len(offsets):
                center = self._shape_bbox_center(offsets[shape_index])
                center_source = "geometry_bounding_box_center" if center else None
            if shape_index < len(offsets):
                self._shape_offsets[region_id] = offsets[shape_index]
            records.append(AdminRegionRecord(
                region_id=region_id,
                name=formal_name,
                aliases=aliases,
                adcode=adcode,
                level=level,
                parent_name=_PROVINCE_NAMES.get(adcode[:2]),
                parent_adcode=adcode[:2] + "0000",
                center=center,
                center_source=center_source,
                boundary_object_id=region_id if shape_index < len(offsets) else None,
                boundary_available=shape_index < len(offsets),
                source=self.source_name,
                source_confidence="local_authoritative_dataset",
                properties={**row, "SOURCE_NAME": source_name},
            ))
        return records

    def _load_province_records(self) -> list[AdminRegionRecord]:
        if not self._province_source_available:
            return []
        data = json.loads(self.province_geojson_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        code_by_name = {normalize_region_name(name): code for code, name in _PROVINCE_NAMES.items()}
        records: list[AdminRegionRecord] = []
        for feature in data.get("features", []):
            properties = feature.get("properties") or {}
            name = str(properties.get("NAME") or "").strip()
            code = code_by_name.get(normalize_region_name(name))
            geometry = feature.get("geometry")
            if not name or not code or not geometry:
                continue
            region_id = f"admin:{code}0000"
            center = self._geojson_bbox_center(geometry)
            public_feature = {
                "type": "Feature",
                "id": region_id,
                "properties": {**properties, "region_id": region_id, "name": name, "adcode": code + "0000", "level": "province"},
                "geometry": geometry,
            }
            self._province_features[region_id] = public_feature
            records.append(AdminRegionRecord(
                region_id=region_id,
                name=name,
                aliases=[normalize_region_name(name)],
                adcode=code + "0000",
                level="province",
                center=center,
                center_source="geometry_bounding_box_center" if center else None,
                boundary_object_id=region_id,
                boundary_available=True,
                source="webgl_bundled_province_geojson",
                source_confidence="local_authoritative_dataset",
                properties={**properties, "SOURCE_NAME": name},
            ))
        return records

    @staticmethod
    def _geojson_bbox_center(geometry: dict[str, Any]) -> dict[str, float] | None:
        points: list[tuple[float, float]] = []

        def collect(value: Any) -> None:
            if not isinstance(value, list):
                return
            if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
                points.append((float(value[0]), float(value[1])))
                return
            for item in value:
                collect(item)

        collect(geometry.get("coordinates"))
        if not points:
            return None
        longitudes = [point[0] for point in points]
        latitudes = [point[1] for point in points]
        return {
            "longitude": (min(longitudes) + max(longitudes)) / 2,
            "latitude": (min(latitudes) + max(latitudes)) / 2,
            "height": 0.0,
        }

    @staticmethod
    def _classify_name(name: str, row: dict[str, str]) -> tuple[str, str, list[str]]:
        if name.endswith("市市辖区"):
            formal = name[:-3]
            return formal, "city", list(dict.fromkeys([normalize_region_name(formal), name]))
        if name.endswith("区"):
            level = "district"
        elif name.endswith("县") or name.endswith("旗"):
            level = "county"
        elif name.endswith("市"):
            level = "county" if str(row.get("X2", "")) not in {"00", "01"} else "city"
        else:
            level = "unknown"
        return name, level, [normalize_region_name(name)]

    def _read_dbf(self) -> list[dict[str, str]]:
        data = self.dbf_path.read_bytes()  # type: ignore[union-attr]
        count = struct.unpack_from("<I", data, 4)[0]
        header_length, record_length = struct.unpack_from("<HH", data, 8)
        fields: list[tuple[str, int, int]] = []
        cursor, field_offset = 32, 1
        while cursor < header_length - 1 and data[cursor] != 0x0D:
            name = data[cursor : cursor + 11].split(b"\0", 1)[0].decode("ascii")
            length = data[cursor + 16]
            fields.append((name, field_offset, length))
            field_offset += length
            cursor += 32
        rows: list[dict[str, str]] = []
        for index in range(count):
            start = header_length + index * record_length
            if start >= len(data) or data[start] == 0x2A:
                continue
            row = {
                name: data[start + offset : start + offset + length].decode("gbk", errors="replace").strip()
                for name, offset, length in fields
            }
            row["_record_index"] = str(index)
            rows.append(row)
        return rows

    def _read_shape_offsets(self, expected: int) -> list[tuple[int, int]]:
        offsets: list[tuple[int, int]] = []
        with self.shp_path.open("rb") as stream:  # type: ignore[union-attr]
            stream.seek(100)
            while len(offsets) < expected:
                header = stream.read(8)
                if len(header) < 8:
                    break
                _, words = struct.unpack(">II", header)
                content_bytes = words * 2
                offset = stream.tell()
                offsets.append((offset, content_bytes))
                stream.seek(content_bytes, 1)
        return offsets

    @staticmethod
    def _polygon_geometry(payload: bytes) -> dict[str, Any] | None:
        if len(payload) < 44 or struct.unpack_from("<I", payload, 0)[0] not in {5, 15, 25}:
            return None
        num_parts, num_points = struct.unpack_from("<II", payload, 36)
        parts_offset = 44
        points_offset = parts_offset + num_parts * 4
        parts = list(struct.unpack_from(f"<{num_parts}I", payload, parts_offset)) + [num_points]
        rings = []
        for part_index in range(num_parts):
            ring = [list(struct.unpack_from("<dd", payload, points_offset + point_index * 16)) for point_index in range(parts[part_index], parts[part_index + 1])]
            if ring:
                rings.append(ring)
        if not rings:
            return None
        return {"type": "Polygon", "coordinates": rings}

    def _shape_bbox_center(self, shape_offset: tuple[int, int]) -> dict[str, float] | None:
        offset, content_bytes = shape_offset
        with self.shp_path.open("rb") as stream:  # type: ignore[union-attr]
            stream.seek(offset)
            payload = stream.read(min(content_bytes, 36))
        if len(payload) < 36 or struct.unpack_from("<I", payload, 0)[0] not in {5, 15, 25}:
            return None
        xmin, ymin, xmax, ymax = struct.unpack_from("<dddd", payload, 4)
        return {"longitude": (xmin + xmax) / 2, "latitude": (ymin + ymax) / 2, "height": 0.0}

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


@lru_cache(maxsize=1)
def get_admin_region_provider() -> LocalShapefileAdminRegionProvider:
    return LocalShapefileAdminRegionProvider()
