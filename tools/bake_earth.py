#!/usr/bin/env python3
"""Bake Natural Earth vector layers into PEG's offline Earth dataset.

PEG needs a real Earth, but it must also run with no network and no heavy
dependencies. So this tool is a *build-time* step: it downloads a handful of
public-domain Natural Earth layers, simplifies and quantises them, and writes a
single gzipped file that ships in the repository. Players never run this.

Run it only to refresh the data:

    python3 tools/bake_earth.py

Source: Natural Earth (naturalearthdata.com), public domain. No attribution is
required by the licence; we give it anyway in docs/DATA.md.

There is deliberately no elevation raster here. Free global DEMs are hundreds
of megabytes and none is reachable from a sandbox. Instead we ship the *shape*
of Earth's relief -- real mountain-range outlines, real plateau outlines and
711 real named peaks with real elevations -- and let peg.world.raster synthesise
a height field that honours those anchors. Fuji ends up 3776 m high, in Honshu,
because the dataset says so; the shape of its slopes is procedural.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import sys
import urllib.request

BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
OUT = os.path.join(os.path.dirname(__file__), "..", "peg", "world", "earth.json.gz")
CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache", "naturalearth")

#: Coordinates are stored as integer hundredths of a degree. That is ~1.1 km at
#: the equator -- far finer than the 0.25 deg raster it feeds, and anything
#: below raster scale is synthesised procedurally anyway.
QUANT = 100

LAYERS = {
    "land": "ne_50m_land",
    "lakes": "ne_110m_lakes",
    "glaciers": "ne_110m_glaciated_areas",
    "regions": "ne_50m_geography_regions_polys",
    "peaks": "ne_10m_geography_regions_elevation_points",
    "rivers": "ne_50m_rivers_lake_centerlines",
}


def fetch(name: str) -> dict:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name + ".geojson")
    if not os.path.exists(path):
        url = f"{BASE}/{name}.geojson"
        print(f"  downloading {name} ...", flush=True)
        with urllib.request.urlopen(url, timeout=180) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
    with open(path, "rb") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------


def rings_of(geom: dict) -> list[list[tuple[float, float]]]:
    """Flatten any GeoJSON geometry into a list of coordinate rings."""
    t = geom["type"]
    c = geom["coordinates"]
    if t == "Polygon":
        return [[(p[0], p[1]) for p in ring] for ring in c]
    if t == "MultiPolygon":
        out = []
        for poly in c:
            out.extend([(p[0], p[1]) for p in ring] for ring in poly)
        return out
    if t == "LineString":
        return [[(p[0], p[1]) for p in c]]
    if t == "MultiLineString":
        return [[(p[0], p[1]) for p in line] for line in c]
    if t == "Point":
        return [[(c[0], c[1])]]
    return []


def simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterative so deep rings do not blow the stack."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = pts[lo]
        bx, by = pts[hi]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best_d = -1.0
        best_i = -1
        for i in range(lo + 1, hi):
            px, py = pts[i]
            if norm < 1e-12:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best_d:
                best_d = d
                best_i = i
        if best_d > tol:
            keep[best_i] = True
            stack.append((lo, best_i))
            stack.append((best_i, hi))
    return [p for p, k in zip(pts, keep) if k]


def encode_ring(pts: list[tuple[float, float]]) -> list[int]:
    """Quantise and delta-encode. Deltas are small integers, which is what
    makes the gzip stage effective -- raw coordinates barely compress."""
    out: list[int] = []
    px = py = 0
    for lon, lat in pts:
        x = int(round(lon * QUANT))
        y = int(round(lat * QUANT))
        out.append(x - px)
        out.append(y - py)
        px, py = x, y
    return out


def ring_area(pts: list[tuple[float, float]]) -> float:
    """Shoelace area in square degrees. Only used to drop specks."""
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5


def point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
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


# --------------------------------------------------------------------------
# layer bakers
# --------------------------------------------------------------------------


def bake_polygons(gj: dict, tol: float, min_area: float) -> list[list[int]]:
    rings = []
    for feat in gj["features"]:
        for ring in rings_of(feat["geometry"]):
            if ring_area(ring) < min_area:
                continue
            s = simplify(ring, tol)
            if len(s) >= 4:
                rings.append(encode_ring(s))
    return rings


def bake_peaks(gj: dict) -> list[list]:
    out = []
    for feat in gj["features"]:
        p = feat["properties"]
        elev = p.get("elevation")
        if not elev:
            continue
        # Natural Earth already signs below-sea-level features negative
        # (Qattara -133, Turpan -154). Do not "helpfully" negate depressions:
        # the Richat structure is classed as one and sits at +485 m.
        coords = feat["geometry"]["coordinates"]
        lon = p.get("long_x") if p.get("long_x") is not None else coords[0]
        lat = p.get("lat_y") if p.get("lat_y") is not None else coords[1]
        out.append([
            int(round(lon * QUANT)),
            int(round(lat * QUANT)),
            int(elev),
            p.get("name") or "",
        ])
    return out


#: Fallback elevations for range/plateau polygons that contain no named peak.
#: Keyed by Natural Earth's FEATURECLA. These are crest/surface heights, not
#: means -- the synthesiser blends them down towards the surrounding terrain.
CLASS_ELEV = {
    "Range/mtn": 1800,
    "Plateau": 900,
    "Foothills": 700,
    "Basin": 300,
    "Plain": 200,
    "Lowland": 80,
    "Tundra": 200,
    "Desert": 400,
    "Valley": 200,
    "Delta": 10,
    "Wetlands": 30,
}

#: Classes whose polygons act as relief features. Everything else in the layer
#: (islands, coasts, continents, geoareas) is either already implied by the
#: land mask or too vague to be useful as a height anchor.
RELIEF_CLASSES = ("Range/mtn", "Plateau", "Foothills", "Basin", "Plain",
                  "Lowland", "Tundra", "Valley")


def bake_regions(gj: dict, peaks: list[list]) -> tuple[list, list]:
    """Turn physical-region polygons into height anchors.

    Each region records two numbers derived from the real peaks inside it: a
    median (the typical height of the region's floor) and a 95th percentile
    (its crest). One number is not enough -- the Plateau of Tibet and the
    Himalayas overlap and contain the same summits, but Tibet is a flat 4500 m
    surface while the Himalayas are 8000 m spikes over deep valleys. The
    synthesiser picks between them per class.

    Regions containing no named peak fall back to a per-class default.
    """
    relief = []
    deserts = []
    peak_pts = [(p[0] / QUANT, p[1] / QUANT, p[2]) for p in peaks]

    for feat in gj["features"]:
        props = feat["properties"]
        cls = props.get("FEATURECLA")
        name = props.get("NAME") or props.get("NAME_EN") or ""
        if cls == "Desert":
            for ring in rings_of(feat["geometry"]):
                if ring_area(ring) < 1.0:
                    continue
                s = simplify(ring, 0.15)
                if len(s) >= 4:
                    deserts.append([encode_ring(s), name])
            continue
        if cls not in RELIEF_CLASSES:
            continue

        for ring in rings_of(feat["geometry"]):
            if ring_area(ring) < 0.5:
                continue
            s = simplify(ring, 0.1)
            if len(s) < 4:
                continue
            inside = sorted(e for (x, y, e) in peak_pts if point_in_ring(x, y, s))
            if inside:
                p50 = inside[len(inside) // 2]
                p95 = inside[min(len(inside) - 1, int(len(inside) * 0.95))]
            else:
                p50 = p95 = CLASS_ELEV.get(cls, 500)
            relief.append([encode_ring(s), int(p50), int(p95), cls, name])
    return relief, deserts


def bake_rivers(gj: dict) -> list[list]:
    out = []
    for feat in gj["features"]:
        p = feat["properties"]
        rank = p.get("scalerank", 10)
        if rank is None or rank > 7:
            continue
        name = p.get("name") or p.get("name_en") or ""
        for line in rings_of(feat["geometry"]):
            s = simplify(line, 0.06)
            if len(s) >= 2:
                out.append([encode_ring(s), int(rank), name])
    return out


def main() -> int:
    print("Baking Earth from Natural Earth ...")
    gj = {k: fetch(v) for k, v in LAYERS.items()}

    print("  land ...", flush=True)
    land = bake_polygons(gj["land"], tol=0.04, min_area=0.002)
    print("  lakes ...", flush=True)
    lakes = bake_polygons(gj["lakes"], tol=0.03, min_area=0.02)
    print("  glaciers ...", flush=True)
    glaciers = bake_polygons(gj["glaciers"], tol=0.08, min_area=0.05)
    print("  peaks ...", flush=True)
    peaks = bake_peaks(gj["peaks"])
    print("  relief regions ...", flush=True)
    relief, deserts = bake_regions(gj["regions"], peaks)
    print("  rivers ...", flush=True)
    rivers = bake_rivers(gj["rivers"])

    payload = {
        "format": 1,
        "source": "Natural Earth (naturalearthdata.com), public domain",
        "quant": QUANT,
        "land": land,
        "lakes": lakes,
        "glaciers": glaciers,
        "relief": relief,
        "deserts": deserts,
        "peaks": peaks,
        "rivers": rivers,
    }

    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with gzip.open(OUT, "wb", compresslevel=9) as f:
        f.write(raw)

    size = os.path.getsize(OUT)
    print(f"\nwrote {os.path.relpath(OUT)}  ({size/1024:.0f} KiB gz, {len(raw)/1024:.0f} KiB raw)")
    for key in ("land", "lakes", "glaciers", "relief", "deserts", "peaks", "rivers"):
        print(f"  {key:9s} {len(payload[key]):5d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
