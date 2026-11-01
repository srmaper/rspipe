"""上传矢量文件解析：shp（zip 打包）/ geojson / kml → WGS84 面。"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ALLOWED = (".shp", ".geojson", ".json", ".kml", ".zip")


def parse_vector_bytes(filename: str, data: bytes) -> dict[str, Any]:
    """解析上传的矢量文件，返回 WGS84 面 + bbox + 面积。

    只接受 EPSG:4326 的 Polygon / MultiPolygon；坐标系缺失或不是 4326 直接报错，
    由前端提示「矢量文件有问题，请重新上传」。
    """
    import geopandas as gpd
    from pyproj import Geod
    from shapely.ops import unary_union

    name = Path(filename or "").name or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError("只支持 shp（需打包成 zip）、geojson、kml")

    with tempfile.TemporaryDirectory(prefix="rspipe_vec_") as tmp:
        tmpdir = Path(tmp)
        if suffix == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(tmpdir)
            except zipfile.BadZipFile as exc:
                raise ValueError("zip 文件损坏") from exc
            shps = sorted(tmpdir.rglob("*.shp"))
            if not shps:
                raise ValueError("zip 里没有找到 .shp 文件")
            src = shps[0]
        else:
            src = tmpdir / name
            src.write_bytes(data)

        try:
            gdf = gpd.read_file(src)
        except Exception as exc:  # 驱动/文件损坏
            raise ValueError(f"无法解析矢量文件：{exc}") from exc

        if gdf.empty:
            raise ValueError("矢量文件里没有要素")
        if gdf.crs is None:
            raise ValueError("缺少坐标系信息，请另存为 EPSG:4326 后重新上传")

        epsg = gdf.crs.to_epsg()
        if epsg != 4326:
            raise ValueError(
                f"坐标系不是 WGS84 经纬度（当前 EPSG:{epsg or '未知'}），"
                "请另存为 EPSG:4326 后重新上传"
            )

        polys = [
            g
            for g in gdf.geometry
            if g is not None and not g.is_empty and g.geom_type in ("Polygon", "MultiPolygon")
        ]
        if not polys:
            raise ValueError("文件里没有面要素（只支持 Polygon / MultiPolygon）")

        merged = unary_union(polys)
        minx, miny, maxx, maxy = merged.bounds
        if not (
            -180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90
        ):
            raise ValueError("坐标超出经纬度范围，可能是坐标系设置错误")

        geod = Geod(ellps="WGS84")
        area_m2, _ = geod.geometry_area_perimeter(merged)
        area_km2 = abs(area_m2) / 1e6
        if area_km2 <= 0:
            raise ValueError("面积无效")

        return {
            "ok": True,
            "name": name,
            "crs": "EPSG:4326",
            "features": len(polys),
            "geometry": json.loads(json.dumps(merged.__geo_interface__)),
            "bbox": [minx, miny, maxx, maxy],
            "area_km2": round(area_km2, 3),
        }
