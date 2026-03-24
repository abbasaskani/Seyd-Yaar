from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import numpy as np
from shapely.geometry import shape, Point
from shapely.prepared import prep

@dataclass(frozen=True)
class GridSpec:
    lon_min: float
    lon_max: float
    lat_min: float
    lat_max: float
    width: int
    height: int
    crs: str = "EPSG:4326"

    @property
    def dx(self) -> float:
        return (self.lon_max - self.lon_min) / max(self.width - 1, 1)

    @property
    def dy(self) -> float:
        return (self.lat_max - self.lat_min) / max(self.height - 1, 1)

    @property
    def lons(self) -> np.ndarray:
        return np.linspace(self.lon_min, self.lon_max, self.width, dtype=np.float32)

    @property
    def lats(self) -> np.ndarray:
        return np.linspace(self.lat_max, self.lat_min, self.height, dtype=np.float32)

    def lonlat_mesh(self) -> Tuple[np.ndarray, np.ndarray]:
        lon2d, lat2d = np.meshgrid(self.lons, self.lats)
        return lon2d, lat2d


def bbox_from_geojson(aoi_geojson: dict) -> Tuple[float, float, float, float]:
    geom = shape(aoi_geojson["features"][0]["geometry"])
    minx, miny, maxx, maxy = geom.bounds
    return float(minx), float(miny), float(maxx), float(maxy)


def mask_from_geojson(aoi_geojson: dict, grid: GridSpec) -> np.ndarray:
    geom = shape(aoi_geojson["features"][0]["geometry"])
    pg = prep(geom)
    lon2d, lat2d = grid.lonlat_mesh()
    mask = np.zeros(lon2d.shape, dtype=np.uint8)
    for i in range(mask.shape[0]):
        for j in range(mask.shape[1]):
            if pg.contains(Point(float(lon2d[i, j]), float(lat2d[i, j]))) or pg.touches(Point(float(lon2d[i, j]), float(lat2d[i, j]))):
                mask[i, j] = 1
    return mask
