"""The global raster: Earth reduced to a few arrays, built once and cached.

Nothing here is the playable world. This is the coarse layer of the LOD chain
-- roughly 55 km cells at the default resolution -- that the strategic map
reads directly and that local 1 m terrain generation uses as its low-frequency
term. Detail below one cell is synthesised procedurally at query time.

Build cost is a couple of seconds; the result is cached under ``.cache/peg/``
and keyed by resolution and generator version, so it happens once per machine.

Arrays, all row-major with row 0 at the north pole:

  ``land``     0/1 land mask
  ``lake``     0/1 inland water (subset of land cells)
  ``glacier``  0/1 permanent ice
  ``coast_km`` signed distance to coastline, positive inland, km
  ``elev``     elevation, metres, int16
  ``relief``   local relief amplitude, metres -- how rugged the cell is
"""

from __future__ import annotations

import math
import os
import struct
import sys
from array import array
from dataclasses import dataclass

from .. import rng
from . import earth

#: Bump when the synthesis changes in a way that invalidates cached rasters.
GENERATOR_VERSION = 3

DEFAULT_RES = 0.5

def _default_cache_dir() -> str:
    """Where to keep built rasters.

    The user's cache directory, not the package directory. An installed
    package lives somewhere read-only (and a zipapp is not a directory at
    all), so writing build artefacts next to the code only works when running
    from a source checkout.
    """
    override = os.environ.get("PEG_CACHE")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "peg", "cache")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/peg")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "peg")


CACHE_DIR = _default_cache_dir()

_MAGIC = b"PEGR"


# --------------------------------------------------------------------------
# scanline rasterisation
# --------------------------------------------------------------------------


def _fill_rings(rings, cols: int, rows: int, res: float) -> bytearray:
    """Even-odd scanline fill of a set of lon/lat rings.

    Even-odd is deliberate: Natural Earth encodes lakes-within-islands and
    similar nesting as extra rings in the same feature, and even-odd gets the
    holes right without us having to track ring winding or parentage.

    Cost is O(edges + crossings) rather than O(rows x edges), because each
    edge only touches the rows it actually spans.
    """
    grid = bytearray(cols * rows)
    crossings: list[list[float]] = [[] for _ in range(rows)]

    for ring in rings:
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if y1 == y2:
                continue
            if y1 < y2:
                ylo, yhi, xlo, xhi = y1, y2, x1, x2
            else:
                ylo, yhi, xlo, xhi = y2, y1, x2, x1
            # Rows whose cell-centre latitude lies in [ylo, yhi).
            j_min = int(math.floor((90.0 - yhi) / res - 0.5)) + 1
            j_max = int(math.floor((90.0 - ylo) / res - 0.5))
            if j_max < 0 or j_min >= rows:
                continue
            j_min = max(0, j_min)
            j_max = min(rows - 1, j_max)
            inv = (xhi - xlo) / (yhi - ylo)
            for j in range(j_min, j_max + 1):
                lat_c = 90.0 - (j + 0.5) * res
                crossings[j].append(xlo + (lat_c - ylo) * inv)

    for j in range(rows):
        xs = crossings[j]
        if not xs:
            continue
        xs.sort()
        base = j * cols
        for k in range(0, len(xs) - 1, 2):
            # Columns whose centre longitude lies in [xs[k], xs[k+1]).
            i0 = int(math.ceil((xs[k] + 180.0) / res - 0.5))
            i1 = int(math.ceil((xs[k + 1] + 180.0) / res - 0.5)) - 1
            if i1 < 0 or i0 >= cols:
                continue
            i0 = max(0, i0)
            i1 = min(cols - 1, i1)
            for i in range(i0, i1 + 1):
                grid[base + i] = 1
    return grid


# --------------------------------------------------------------------------
# distance transform
# --------------------------------------------------------------------------


def _chamfer_km(mask: bytearray, cols: int, rows: int, res: float,
                inside: int) -> array:
    """Distance in km from every cell to the nearest cell where
    ``mask != inside``.

    Two-pass weighted chamfer with true Euclidean step lengths, and per-row
    horizontal spacing so meridian convergence is respected. Overestimates
    long distances by about 3%, which is immaterial for the things that
    consume it (continentality, base elevation, shelf depth) and roughly
    fifteen times faster than an exact transform at this grid size.
    """
    INF = 1e9
    dist = array("f", [INF] * (cols * rows))

    dy = res * 110.574
    dx_row = [max(0.05, res * 111.320 * math.cos(math.radians(90.0 - (j + 0.5) * res)))
              for j in range(rows)]

    # Seed: any cell adjacent to the other class is half a cell from the edge.
    for j in range(rows):
        base = j * cols
        dxr = dx_row[j]
        for i in range(cols):
            if mask[base + i] != inside:
                dist[base + i] = 0.0
                continue
            # Neighbours, wrapping in longitude.
            w = base + (i - 1) % cols
            e = base + (i + 1) % cols
            if mask[w] != inside or mask[e] != inside:
                dist[base + i] = dxr * 0.5
                continue
            if j > 0 and mask[base - cols + i] != inside:
                dist[base + i] = dy * 0.5
            elif j < rows - 1 and mask[base + cols + i] != inside:
                dist[base + i] = dy * 0.5

    # Forward pass: north-west to south-east.
    for j in range(rows):
        base = j * cols
        dxr = dx_row[j]
        diag = math.hypot(dxr, dy)
        up = base - cols
        for i in range(cols):
            c = base + i
            d = dist[c]
            if d == 0.0:
                continue
            wi = (i - 1) % cols
            v = dist[base + wi] + dxr
            if v < d:
                d = v
            if j > 0:
                v = dist[up + i] + dy
                if v < d:
                    d = v
                v = dist[up + wi] + diag
                if v < d:
                    d = v
                v = dist[up + (i + 1) % cols] + diag
                if v < d:
                    d = v
            dist[c] = d

    # Backward pass: south-east to north-west.
    for j in range(rows - 1, -1, -1):
        base = j * cols
        dxr = dx_row[j]
        diag = math.hypot(dxr, dy)
        dn = base + cols
        for i in range(cols - 1, -1, -1):
            c = base + i
            d = dist[c]
            if d == 0.0:
                continue
            ei = (i + 1) % cols
            v = dist[base + ei] + dxr
            if v < d:
                d = v
            if j < rows - 1:
                v = dist[dn + i] + dy
                if v < d:
                    d = v
                v = dist[dn + ei] + diag
                if v < d:
                    d = v
                v = dist[dn + (i - 1) % cols] + diag
                if v < d:
                    d = v
            dist[c] = d

    return dist


def _box_blur(src: array, cols: int, rows: int, radius: int) -> array:
    """Separable box blur, wrapping in longitude and clamping at the poles.
    Used to turn hard polygon edges into ramps -- a mountain range should not
    end at a cliff just because its outline does."""
    tmp = array("f", [0.0] * (cols * rows))
    out = array("f", [0.0] * (cols * rows))
    win = radius * 2 + 1

    for j in range(rows):
        base = j * cols
        acc = 0.0
        for k in range(-radius, radius + 1):
            acc += src[base + (k % cols)]
        for i in range(cols):
            tmp[base + i] = acc / win
            acc += src[base + ((i + radius + 1) % cols)]
            acc -= src[base + ((i - radius) % cols)]

    for i in range(cols):
        acc = 0.0
        for k in range(-radius, radius + 1):
            acc += tmp[min(rows - 1, max(0, k)) * cols + i]
        for j in range(rows):
            out[j * cols + i] = acc / win
            acc += tmp[min(rows - 1, j + radius + 1) * cols + i]
            acc -= tmp[max(0, j - radius) * cols + i]
    return out


# --------------------------------------------------------------------------
# the raster
# --------------------------------------------------------------------------


@dataclass
class WorldRaster:
    res: float
    cols: int
    rows: int
    land: bytearray
    lake: bytearray
    glacier: bytearray
    coast_km: array
    elev: array
    relief: array

    # ---- addressing ----

    def index(self, lon: float, lat: float) -> int:
        i = int((lon + 180.0) / self.res) % self.cols
        j = int((90.0 - lat) / self.res)
        j = 0 if j < 0 else (self.rows - 1 if j >= self.rows else j)
        return j * self.cols + i

    def cell_center(self, idx: int) -> tuple[float, float]:
        j, i = divmod(idx, self.cols)
        return -180.0 + (i + 0.5) * self.res, 90.0 - (j + 0.5) * self.res

    def is_land(self, lon: float, lat: float) -> bool:
        return self.land[self.index(lon, lat)] == 1

    def is_lake(self, lon: float, lat: float) -> bool:
        return self.lake[self.index(lon, lat)] == 1

    def is_glacier(self, lon: float, lat: float) -> bool:
        return self.glacier[self.index(lon, lat)] == 1

    def sample(self, arr: array, lon: float, lat: float) -> float:
        """Bilinear sample. The raster is a smooth field being read at
        arbitrary points, so nearest-neighbour would produce visible 55 km
        terraces in climate and elevation."""
        fx = (lon + 180.0) / self.res - 0.5
        fy = (90.0 - lat) / self.res - 0.5
        i0 = math.floor(fx)
        j0 = math.floor(fy)
        tx = fx - i0
        ty = fy - j0
        i0 = int(i0)
        j0 = int(j0)
        j1 = min(self.rows - 1, max(0, j0 + 1))
        j0c = min(self.rows - 1, max(0, j0))
        i0c = i0 % self.cols
        i1c = (i0 + 1) % self.cols
        a = arr[j0c * self.cols + i0c]
        b = arr[j0c * self.cols + i1c]
        c = arr[j1 * self.cols + i0c]
        d = arr[j1 * self.cols + i1c]
        return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty

    def elevation(self, lon: float, lat: float) -> float:
        return self.sample(self.elev, lon, lat)

    def coast_distance_km(self, lon: float, lat: float) -> float:
        return self.sample(self.coast_km, lon, lat)

    def local_relief(self, lon: float, lat: float) -> float:
        return self.sample(self.relief, lon, lat)


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------


#: How much of a region's surveyed statistics its *ground* actually sits at.
#: A mountain range polygon covers the valleys as well as the summits, so the
#: ground inside it is nowhere near crest height; a plateau, by definition, is.
#: ``(source, factor)`` where source picks which percentile to read.
#: Named summits inside a plateau rise *above* its surface -- Tibet's peaks
#: run 6-7 km, its surface 4.5 km -- so the factor is well under one.
REGION_FLOOR = {
    "Range/mtn": ("p50", 0.42),
    "Plateau":   ("p50", 0.60),
    "Foothills": ("p50", 0.40),
    "Tundra":    ("p50", 0.30),
}

#: Low regions do the opposite job: they *cap* elevation. Without this the
#: blurred apron of the Andes lifts the Amazon basin to 900 m. The peak-cone
#: pass runs afterwards and still overrides the cap, so a genuinely mountainous
#: basin like the Great Basin keeps its ranges.
#: ``(percentile factor, absolute ceiling in metres)``
REGION_CAP = {
    "Basin":    (0.35, 1500.0),
    "Plain":    (0.20, 400.0),
    "Lowland":  (0.10, 200.0),
    "Valley":   (0.25, 500.0),
}


def _synthesize_elevation(r: WorldRaster, data: earth.EarthData, seed: int) -> None:
    """Build the height field from real anchors plus procedural filler.

    Terms are combined with ``max`` rather than a sum, so that overlapping
    anchors -- and a Himalayan summit is simultaneously inside the Himalayas,
    the Plateau of Tibet and a couple of sub-ranges -- do not stack into
    absurd altitudes.

    1. a gentle rise away from the coast, giving continental interiors a base
    2. region polygons at their *ground* height (see REGION_FLOOR), blurred
       outward so ranges have foothills instead of walls
    3. cones around each of the 711 named summits, stamped after the blur so
       they keep their true height and position
    4. fBm and ridged detail, amplitude scaled by local ruggedness
    5. named depressions punched back down

    What this gets right: where mountains are, how high their summits are, how
    high plateaus sit, where the plains are. What it cannot get right without a
    real DEM is any particular valley floor. Elevations away from a named
    anchor are plausible, not surveyed -- see docs/DATA.md.
    """
    cols, rows, res = r.cols, r.rows, r.res
    n = cols * rows
    anchor = array("f", [0.0] * n)

    # --- 2. relief polygons -------------------------------------------------
    # Low regions first, so a small high range overlapping a broad low plain
    # wins on the overlap.
    def floor_of(reg: earth.Region) -> float:
        src, fac = REGION_FLOOR.get(reg.kind, ("p50", 0.35))
        base = reg.elev_p50 if src == "p50" else reg.elev_p95
        return max(0.0, base * fac)

    for reg in sorted(data.relief, key=floor_of):
        e = floor_of(reg)
        if e <= 0:
            continue
        x0, y0, x1, y1 = reg.bbox
        sub = _fill_rings([reg.ring], cols, rows, res)
        j_lo = max(0, int((90.0 - y1) / res) - 1)
        j_hi = min(rows - 1, int((90.0 - y0) / res) + 1)
        i_lo = max(0, int((x0 + 180.0) / res) - 1)
        i_hi = min(cols - 1, int((x1 + 180.0) / res) + 1)
        for j in range(j_lo, j_hi + 1):
            base = j * cols
            for i in range(i_lo, i_hi + 1):
                c = base + i
                if sub[c] and e > anchor[c]:
                    anchor[c] = e

    # Ramp the polygon edges outward: a tight blur for the inner slope and a
    # wide one for the outer apron. Taking the max of the three keeps the
    # interior at its true value instead of inflating it.
    tight = _box_blur(anchor, cols, rows, 1)
    wide = _box_blur(anchor, cols, rows, 3)
    for c in range(n):
        v = anchor[c]
        t = tight[c]
        w = wide[c]
        if t > v:
            v = t
        if w > v:
            v = w
        anchor[c] = v

    # Low regions clamp the apron back down. The cap is kept around so the
    # coast-distance rise below is limited too -- otherwise the Amazon basin
    # simply rises again as a continental interior.
    capf = array("f", [1e9] * n)
    for reg in data.relief:
        spec = REGION_CAP.get(reg.kind)
        if spec is None:
            continue
        fac, ceiling = spec
        cap = min(ceiling, max(30.0, reg.elev_p50 * fac))
        x0, y0, x1, y1 = reg.bbox
        sub = _fill_rings([reg.ring], cols, rows, res)
        j_lo = max(0, int((90.0 - y1) / res) - 1)
        j_hi = min(rows - 1, int((90.0 - y0) / res) + 1)
        i_lo = max(0, int((x0 + 180.0) / res) - 1)
        i_hi = min(cols - 1, int((x1 + 180.0) / res) + 1)
        for j in range(j_lo, j_hi + 1):
            base = j * cols
            for i in range(i_lo, i_hi + 1):
                c = base + i
                if sub[c] and cap < capf[c]:
                    capf[c] = cap
    for c in range(n):
        if anchor[c] > capf[c]:
            anchor[c] = capf[c]

    # --- 3. named summits ---------------------------------------------------
    depressions: list[earth.Peak] = []
    for pk in data.peaks:
        if pk.elev_m <= 0:
            depressions.append(pk)
            continue
        # Higher mountains carry wider massifs: Everest's cone reaches ~2.4
        # degrees, a 1000 m hill about 0.6.
        rad_deg = 0.35 + (pk.elev_m / 8848.0) * 2.1
        rad_cells = max(1, int(math.ceil(rad_deg / res)))
        ci = int((pk.lon + 180.0) / res) % cols
        cj = int((90.0 - pk.lat) / res)
        for dj in range(-rad_cells, rad_cells + 1):
            j = cj + dj
            if j < 0 or j >= rows:
                continue
            base = j * cols
            latc = 90.0 - (j + 0.5) * res
            coslat = max(0.05, math.cos(math.radians(latc)))
            for di in range(-rad_cells, rad_cells + 1):
                i = (ci + di) % cols
                dlon = di * res * coslat
                dlat = dj * res
                d = math.hypot(dlon, dlat) / rad_deg
                if d >= 1.0:
                    continue
                v = pk.elev_m * (1.0 - d) ** 1.7
                c = base + i
                if v > anchor[c]:
                    anchor[c] = v

    # --- 1 & 4. combine with coast rise, ocean depth and noise --------------
    nseed = rng.mix(seed, rng.tag("elevation"))
    for j in range(rows):
        base = j * cols
        lat = 90.0 - (j + 0.5) * res
        for i in range(cols):
            c = base + i
            lon = -180.0 + (i + 0.5) * res
            d = r.coast_km[c]
            nx = lon / 6.0
            ny = lat / 6.0

            if r.land[c]:
                interior = 300.0 * (1.0 - math.exp(-d / 420.0))
                cap = capf[c]
                if interior > cap:
                    interior = cap
                a = anchor[c]
                base_h = a if a > interior else interior
                # Ruggedness: high ground is noisy, plains are not.
                rough = rng.clamp(base_h / 1100.0, 0.18, 3.2)
                detail = rng.fbm2(nseed, nx * 3.0, ny * 3.0, 4) * 80.0 * rough
                ridge = rng.ridged2(nseed ^ 0x5A5A, nx * 5.0, ny * 5.0, 3) - 0.45
                h = base_h + detail + ridge * 110.0 * min(rough, 2.2)

                if r.glacier[c]:
                    # Ice sheets: a dome thickening away from the margin.
                    h += 2700.0 * (1.0 - math.exp(-max(0.0, d) / 190.0))
                if r.lake[c]:
                    h -= 20.0
                r.elev[c] = int(rng.clamp(h, -430.0, 8848.0))
            else:
                # `d` is negative offshore; work with distance from the coast.
                off = -d
                shelf = -150.0 * (1.0 - math.exp(-off / 45.0))
                abyss = -4300.0 * (1.0 - math.exp(-max(0.0, off - 90.0) / 340.0))
                nse = rng.fbm2(nseed ^ 0x1F1F, nx * 2.5, ny * 2.5, 3) * 320.0
                r.elev[c] = int(rng.clamp(shelf + abyss + nse, -10900.0, -1.0))

    # --- 5. depressions -----------------------------------------------------
    # Death Valley and the Dead Sea are real places with real negative
    # elevations, and the max-blend above cannot express them.
    for pk in depressions:
        rad_deg = 0.4
        rad_cells = max(1, int(math.ceil(rad_deg / res)))
        ci = int((pk.lon + 180.0) / res) % cols
        cj = int((90.0 - pk.lat) / res)
        for dj in range(-rad_cells, rad_cells + 1):
            j = cj + dj
            if j < 0 or j >= rows:
                continue
            base = j * cols
            for di in range(-rad_cells, rad_cells + 1):
                i = (ci + di) % cols
                d = math.hypot(di * res, dj * res) / rad_deg
                if d >= 1.0:
                    continue
                c = base + i
                target = pk.elev_m * (1.0 - d) + r.elev[c] * d
                if target < r.elev[c]:
                    r.elev[c] = int(target)

    # --- local relief amplitude --------------------------------------------
    for j in range(rows):
        base = j * cols
        for i in range(cols):
            lo = hi = r.elev[base + i]
            for dj in (-1, 0, 1):
                jj = j + dj
                if jj < 0 or jj >= rows:
                    continue
                b2 = jj * cols
                for di in (-1, 0, 1):
                    v = r.elev[b2 + (i + di) % cols]
                    if v < lo:
                        lo = v
                    if v > hi:
                        hi = v
            r.relief[base + i] = min(32000, hi - lo)


def build(res: float = DEFAULT_RES, seed: int = 0, verbose: bool = False) -> WorldRaster:
    data = earth.load()
    cols = int(round(360.0 / res))
    rows = int(round(180.0 / res))
    n = cols * rows

    def say(msg: str) -> None:
        if verbose:
            print(f"  {msg}", flush=True)

    say(f"rasterising land ({cols}x{rows}) ...")
    land = _fill_rings(data.land, cols, rows, res)
    lake = _fill_rings(data.lakes, cols, rows, res)
    glacier = _fill_rings(data.glaciers, cols, rows, res)

    # Lakes only count where they sit on land; NE's lake layer includes a few
    # coastal lagoons that the land layer already reads as sea.
    for c in range(n):
        if lake[c] and not land[c]:
            lake[c] = 0

    say("distance to coast ...")
    inland = _chamfer_km(land, cols, rows, res, inside=1)
    offshore = _chamfer_km(land, cols, rows, res, inside=0)
    coast = array("f", [0.0] * n)
    for c in range(n):
        coast[c] = inland[c] if land[c] else -offshore[c]

    r = WorldRaster(
        res=res, cols=cols, rows=rows,
        land=land, lake=lake, glacier=glacier,
        coast_km=coast,
        elev=array("h", [0] * n),
        relief=array("h", [0] * n),
    )

    say("synthesising elevation ...")
    _synthesize_elevation(r, data, seed)
    return r


# --------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------


def _cache_path(res: float, seed: int) -> str:
    return os.path.join(
        CACHE_DIR, f"raster_v{GENERATOR_VERSION}_r{res:g}_s{seed & 0xFFFFFFFF:08x}.bin"
    )


def _save(r: WorldRaster, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(_MAGIC)
        f.write(struct.pack("<Bdii", GENERATOR_VERSION, r.res, r.cols, r.rows))
        f.write(bytes(r.land))
        f.write(bytes(r.lake))
        f.write(bytes(r.glacier))
        r.coast_km.tofile(f)
        r.elev.tofile(f)
        r.relief.tofile(f)
    os.replace(tmp, path)


def _load(path: str) -> WorldRaster | None:
    try:
        with open(path, "rb") as f:
            if f.read(4) != _MAGIC:
                return None
            ver, res, cols, rows = struct.unpack("<Bdii", f.read(1 + 8 + 4 + 4))
            if ver != GENERATOR_VERSION:
                return None
            n = cols * rows
            land = bytearray(f.read(n))
            lake = bytearray(f.read(n))
            glacier = bytearray(f.read(n))
            coast = array("f")
            coast.fromfile(f, n)
            elev = array("h")
            elev.fromfile(f, n)
            relief = array("h")
            relief.fromfile(f, n)
    except (OSError, EOFError, struct.error, ValueError):
        return None
    return WorldRaster(res, cols, rows, land, lake, glacier, coast, elev, relief)


_MEMO: dict[tuple[float, int], WorldRaster] = {}


def get(res: float = DEFAULT_RES, seed: int = 0, verbose: bool = False) -> WorldRaster:
    """Load the raster from cache, building it if needed."""
    key = (res, seed)
    if key in _MEMO:
        return _MEMO[key]
    path = _cache_path(res, seed)
    r = _load(path)
    if r is None:
        if verbose:
            print("Building global raster (first run only) ...", flush=True)
        r = build(res, seed, verbose=verbose)
        try:
            _save(r, path)
        except OSError:
            pass  # read-only filesystem: fine, we just rebuild next time
    _MEMO[key] = r
    return r
