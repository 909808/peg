"""Loader for the baked Natural Earth dataset.

The file shipped alongside this module (``earth.json.gz``, ~120 KiB) is the
whole of PEG's factual knowledge about Earth: where the land is, where the
lakes and glaciers are, the outlines of 225 mountain ranges and plateaus, 35
deserts, 895 river reaches and 711 named summits with their real heights.

Everything else the game knows about Earth -- elevation between the peaks,
climate, soil, what grows where, what a given square metre looks like -- is
derived from these anchors at runtime.

Regenerate the file with ``python3 tools/bake_earth.py``.
"""

from __future__ import annotations

import gzip
import json
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache

DATA_PATH = os.path.join(os.path.dirname(__file__), "earth.json.gz")

Ring = list[tuple[float, float]]


def _decode_ring(flat: list[int], quant: float) -> Ring:
    """Undo the delta+quantise encoding from the bake tool."""
    pts: Ring = []
    x = y = 0
    it = iter(flat)
    for dx in it:
        dy = next(it)
        x += dx
        y += dy
        pts.append((x / quant, y / quant))
    return pts


def _bbox(ring: Ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


@dataclass
class Region:
    """A named physical region with elevation statistics from real summits.

    ``elev_p50`` is the region's typical ground level, ``elev_p95`` its crest.
    """

    ring: Ring
    elev_p50: int
    elev_p95: int
    kind: str
    name: str
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)

    def __post_init__(self) -> None:
        self.bbox = _bbox(self.ring)


@dataclass
class Peak:
    lon: float
    lat: float
    elev_m: int
    name: str


@dataclass
class River:
    line: Ring
    rank: int
    name: str
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)

    def __post_init__(self) -> None:
        self.bbox = _bbox(self.line)

    @property
    def discharge_class(self) -> int:
        """1 (Amazon-scale) to 7 (a stream you can wade). Natural Earth's
        scalerank is inverted relative to size, which is what we want."""
        return max(1, min(7, self.rank))


@dataclass
class EarthData:
    land: list[Ring] = field(default_factory=list)
    lakes: list[Ring] = field(default_factory=list)
    glaciers: list[Ring] = field(default_factory=list)
    relief: list[Region] = field(default_factory=list)
    deserts: list[Region] = field(default_factory=list)
    peaks: list[Peak] = field(default_factory=list)
    rivers: list[River] = field(default_factory=list)
    source: str = ""

    # 1-degree bucket index -> indices into `rivers`, for nearest-river queries.
    _river_index: dict[tuple[int, int], list[int]] = field(default_factory=dict)
    _region_index: dict[tuple[int, int], list[int]] = field(default_factory=dict)

    def build_indexes(self) -> None:
        """Bucket rivers and regions into 1-degree cells.

        Without this, "how far is the nearest river" is a scan over 895
        polylines, which the site survey does thousands of times.
        """
        self._river_index.clear()
        for i, r in enumerate(self.rivers):
            x0, y0, x1, y1 = r.bbox
            for gx in range(int(math.floor(x0)), int(math.floor(x1)) + 1):
                for gy in range(int(math.floor(y0)), int(math.floor(y1)) + 1):
                    self._river_index.setdefault((gx, gy), []).append(i)
        self._region_index.clear()
        for i, reg in enumerate(self.relief):
            x0, y0, x1, y1 = reg.bbox
            for gx in range(int(math.floor(x0)), int(math.floor(x1)) + 1):
                for gy in range(int(math.floor(y0)), int(math.floor(y1)) + 1):
                    self._region_index.setdefault((gx, gy), []).append(i)

    def rivers_near(self, lon: float, lat: float, pad: int = 1) -> list[River]:
        out: list[River] = []
        seen: set[int] = set()
        gx0 = int(math.floor(lon))
        gy0 = int(math.floor(lat))
        for dx in range(-pad, pad + 1):
            for dy in range(-pad, pad + 1):
                for i in self._river_index.get((gx0 + dx, gy0 + dy), ()):
                    if i not in seen:
                        seen.add(i)
                        out.append(self.rivers[i])
        return out

    def regions_at(self, lon: float, lat: float) -> list[Region]:
        out = []
        for i in self._region_index.get((int(math.floor(lon)), int(math.floor(lat))), ()):
            reg = self.relief[i]
            if point_in_ring(lon, lat, reg.ring):
                out.append(reg)
        return out

    def nearest_peak(self, lon: float, lat: float, max_deg: float = 3.0) -> Peak | None:
        best = None
        best_d = max_deg * max_deg
        for p in self.peaks:
            d = (p.lon - lon) ** 2 + (p.lat - lat) ** 2
            if d < best_d:
                best_d = d
                best = p
        return best


def point_in_ring(x: float, y: float, ring: Ring) -> bool:
    """Even-odd ray cast. Exact for the polygon as given, including slivers."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def point_segment_km(lon: float, lat: float, ax: float, ay: float,
                     bx: float, by: float) -> float:
    """Distance from a point to a lon/lat segment, in km.

    Longitude degrees are scaled by cos(lat) first so the answer is a real
    ground distance rather than a degree-space fiction that would make polar
    rivers look 60x further away than they are.
    """
    kx = 111.32 * math.cos(math.radians(lat))
    ky = 110.57
    px = (lon - ax) * kx
    py = (lat - ay) * ky
    vx = (bx - ax) * kx
    vy = (by - ay) * ky
    seg2 = vx * vx + vy * vy
    if seg2 < 1e-9:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * vx + py * vy) / seg2))
    return math.hypot(px - t * vx, py - t * vy)


@lru_cache(maxsize=1)
def load() -> EarthData:
    """Load and index the dataset. Cached -- it is immutable and ~15 MB in
    memory once decoded, which is not worth doing twice."""
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(
            f"Earth dataset missing at {DATA_PATH}. "
            "Run: python3 tools/bake_earth.py"
        )
    with gzip.open(DATA_PATH, "rb") as f:
        raw = json.loads(f.read().decode("utf-8"))

    q = float(raw.get("quant", 100))
    data = EarthData(source=raw.get("source", ""))
    data.land = [_decode_ring(r, q) for r in raw["land"]]
    data.lakes = [_decode_ring(r, q) for r in raw["lakes"]]
    data.glaciers = [_decode_ring(r, q) for r in raw["glaciers"]]
    data.relief = [
        Region(_decode_ring(r, q), int(lo), int(hi), str(k), str(n))
        for r, lo, hi, k, n in raw["relief"]
    ]
    data.deserts = [
        Region(_decode_ring(r, q), 0, 0, "Desert", str(n)) for r, n in raw["deserts"]
    ]
    data.peaks = [
        Peak(x / q, y / q, int(e), str(n)) for x, y, e, n in raw["peaks"]
    ]
    data.rivers = [
        River(_decode_ring(r, q), int(rank), str(n)) for r, rank, n in raw["rivers"]
    ]
    data.build_indexes()
    return data
