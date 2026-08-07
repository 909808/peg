"""The playable map: Earth at one metre per tile.

A site is a tangent plane anchored at a real latitude and longitude, tiled at
1 m. Everything on it is derived from the world-scale survey of that point --
the soil under your feet is the soil the climate model weathered there, the
trees are the species that biome supports, the ore is the ore the geology
allows.

Generation is positional and deterministic: tile (x, y) of a site depends only
on the world seed and the site's coordinates, never on when or in what order it
was generated. Walking off the east edge of one site and surveying the ground
beyond gives the same answer either way.

Maps are chunked at 32 m and generated lazily. A default 192 m site is 36,864
tiles; the whole Earth at this resolution would be 5.1e14, which is exactly why
nothing is generated until someone looks at it.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass

from .. import geo
from .. import rng
from ..world import site as site_mod

CHUNK = 32


def _field(seed: int, x0: int, y0: int, w: int, step: int, scale: float,
           octaves: int, amp: float) -> list[float]:
    """An fBm field over a ``w`` x ``w`` tile block, sampled on a coarse
    lattice and bilinearly upsampled.

    Sampling coherent noise at every square metre is enormously wasteful when
    the feature size is 20 m: the values between lattice points are, by
    construction, a smooth interpolation of the ones on it. Sampling every
    ``step`` metres and interpolating gives a visually identical field for a
    fraction of the cost -- about eight times less work across a whole site,
    which is the difference between a map that appears and one you wait for.

    Output index ``j * w + k`` corresponds to global tile
    ``(x0 + k - 1, y0 + j - 1)``, matching the one-tile border the chunk
    generator needs for finite differences.
    """
    lw = (w - 1) // step + 3
    lat = [0.0] * (lw * lw)
    inv = 1.0 / scale
    for j in range(lw):
        gy = (y0 - 1 + j * step) * inv
        row = j * lw
        for k in range(lw):
            gx = (x0 - 1 + k * step) * inv
            lat[row + k] = rng.fbm2(seed, gx, gy, octaves) * amp

    if step == 1:
        return lat[:w * w] if lw == w else _resample(lat, lw, w, 1)
    return _resample(lat, lw, w, step)


def _resample(lat: list[float], lw: int, w: int, step: int) -> list[float]:
    out = [0.0] * (w * w)
    inv = 1.0 / step
    for j in range(w):
        fy = j * inv
        j0 = int(fy)
        ty = fy - j0
        r0 = j0 * lw
        r1 = (j0 + 1) * lw
        o = j * w
        for k in range(w):
            fx = k * inv
            k0 = int(fx)
            tx = fx - k0
            a = lat[r0 + k0]
            b = lat[r0 + k0 + 1]
            c = lat[r1 + k0]
            d = lat[r1 + k0 + 1]
            out[o + k] = (a + (b - a) * tx) + ((c + (d - c) * tx)
                                               - (a + (b - a) * tx)) * ty
    return out


# --------------------------------------------------------------------------
# terrain kinds
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Terrain:
    id: int
    key: str
    name: str
    glyph: str
    #: Seconds to walk one metre across it, for an unencumbered adult.
    walk_s: float
    #: Fraction of incoming fire this stops when you are lying in it, 0-1.
    cover: float
    buildable: bool
    #: Multiplier on soil fertility for crops planted here.
    fertility: float
    water: bool = False


_T: list[Terrain] = []


def _t(key, name, glyph, walk_s, cover, buildable, fertility, water=False):
    t = Terrain(len(_T), key, name, glyph, walk_s, cover, buildable, fertility, water)
    _T.append(t)
    return t


# Order matters only in that ids are assigned here; nothing else depends on it.
DEEP_WATER   = _t("deep_water",  "deep water",     "≈", 9.00, 0.00, False, 0.0, True)
WATER        = _t("water",       "water",          "~", 4.50, 0.30, False, 0.0, True)
SHALLOWS     = _t("shallows",    "shallows",       "-", 2.20, 0.15, False, 0.0, True)
MARSH        = _t("marsh",       "marsh",          "\"", 2.40, 0.25, False, 0.6)
MUD          = _t("mud",         "mud",            ",", 1.60, 0.05, True,  0.9)
SAND         = _t("sand",        "sand",           ".", 1.25, 0.00, True,  0.2)
GRAVEL       = _t("gravel",      "gravel",         ":", 1.10, 0.05, True,  0.3)
SOIL         = _t("soil",        "soil",           ".", 1.00, 0.00, True,  1.0)
RICH_SOIL    = _t("rich_soil",   "rich soil",      ".", 1.00, 0.00, True,  1.3)
GRASS        = _t("grass",       "grass",          "\"", 1.05, 0.05, True,  1.0)
SCREE        = _t("scree",       "scree",          "^", 1.70, 0.20, False, 0.0)
ROCK         = _t("rock",        "bare rock",      "^", 1.30, 0.10, True,  0.0)
CLIFF        = _t("cliff",       "cliff",          "#", 99.0, 0.85, False, 0.0)
SNOW         = _t("snow",        "snow",           "*", 1.60, 0.05, True,  0.0)
ICE          = _t("ice",         "ice",            "*", 1.45, 0.00, True,  0.0)
PACKED       = _t("packed",      "packed earth",   ".", 0.92, 0.00, True,  0.5)
ROAD         = _t("road",        "gravel road",    "=", 0.72, 0.00, True,  0.0)
CONCRETE     = _t("concrete",    "concrete",       "=", 0.80, 0.00, True,  0.0)
FLOOR        = _t("floor",       "board floor",    "=", 0.85, 0.00, True,  0.0)

TERRAIN_BY_ID = tuple(_T)
TERRAIN = {t.key: t for t in _T}


# --------------------------------------------------------------------------
# flora
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Species:
    key: str
    name: str
    glyph: str
    #: Mature height, metres. Drives line of sight and timber yield.
    height_m: float
    #: Wood in a mature specimen, kg.
    wood_kg: float
    #: Edible yield per mature plant per year, kcal. Zero for most trees.
    food_kcal: float
    years_to_mature: float
    #: Which biomes it grows in.
    biomes: tuple[str, ...]
    hardwood: bool = False


FLORA: tuple[Species, ...] = (
    Species("oak", "oak", "T", 22, 2400, 9000, 60,
            ("temp_broadleaf", "temp_rain", "shrubland"), True),
    Species("beech", "beech", "T", 26, 2100, 3200, 55,
            ("temp_broadleaf", "temp_rain"), True),
    Species("birch", "birch", "t", 16, 700, 0, 30,
            ("boreal", "temp_broadleaf", "tundra")),
    Species("pine", "pine", "T", 24, 1500, 1200, 40,
            ("boreal", "temp_conifer", "shrubland")),
    Species("spruce", "spruce", "T", 28, 1800, 0, 50,
            ("boreal", "temp_conifer", "alpine")),
    Species("fir", "fir", "T", 30, 2000, 0, 55, ("temp_conifer", "temp_rain")),
    Species("willow", "willow", "t", 12, 400, 0, 18, ("wetland", "temp_broadleaf")),
    Species("acacia", "acacia", "t", 8, 350, 800, 22, ("savanna", "semidesert")),
    Species("baobab", "baobab", "T", 18, 3000, 4000, 90, ("savanna",)),
    Species("mahogany", "mahogany", "T", 35, 4200, 0, 70, ("trop_rain", "trop_dry")),
    Species("kapok", "kapok", "T", 44, 5000, 0, 60, ("trop_rain",)),
    Species("palm", "palm", "t", 14, 250, 26000, 12, ("trop_rain", "trop_dry", "mangrove")),
    Species("mangrove", "mangrove", "t", 9, 300, 0, 20, ("mangrove",)),
    Species("olive", "olive", "t", 7, 200, 5200, 20, ("shrubland",)),
    Species("juniper", "juniper", "t", 5, 120, 400, 25, ("shrubland", "steppe", "alpine")),
    Species("dwarf_birch", "dwarf birch", "\"", 1, 20, 0, 8, ("tundra", "alpine")),
    Species("saxaul", "saxaul", "\"", 3, 90, 0, 15, ("desert", "semidesert", "steppe")),
    Species("bunchgrass", "bunchgrass", "\"", 1, 0, 260, 1,
            ("grassland", "steppe", "savanna", "tundra", "semidesert", "shrubland")),
    Species("reed", "reed", "\"", 3, 8, 300, 2, ("wetland", "mangrove")),
    Species("berry_bush", "berry bush", "\"", 2, 15, 3400, 4,
            ("temp_broadleaf", "temp_conifer", "boreal", "tundra", "temp_rain")),
)

FLORA_BY_KEY = {s.key: s for s in FLORA}


def _species_for(biome_key: str) -> tuple[tuple[Species, float], ...]:
    """Weighted species list for a biome. Trees are heavier than shrubs so a
    forest reads as a forest."""
    out = []
    for sp in FLORA:
        if biome_key in sp.biomes:
            w = 3.0 if sp.height_m > 10 else 1.0
            out.append((sp, w))
    return tuple(out) if out else ((FLORA_BY_KEY["bunchgrass"], 1.0),)


# --------------------------------------------------------------------------
# map objects
# --------------------------------------------------------------------------

OBJ_NONE = 0
OBJ_PLANT = 1
OBJ_BOULDER = 2
OBJ_ORE = 3
OBJ_BUILDING = 4
OBJ_DEADFALL = 5


@dataclass
class Plant:
    species: Species
    #: 0-1. Growth is tracked so that felling a sapling wastes it.
    growth: float
    age_days: float = 0.0

    @property
    def wood_kg(self) -> float:
        return self.species.wood_kg * (self.growth ** 2.2)

    @property
    def blocks_sight(self) -> bool:
        return self.species.height_m * self.growth > 1.7


@dataclass
class OreBody:
    mineral: str
    #: Remaining extractable mass, kg.
    kg: float
    #: Grade, 0-1: kg of metal per kg of rock moved.
    grade: float


# --------------------------------------------------------------------------
# the local map
# --------------------------------------------------------------------------


class LocalMap:
    """A site's tile grid. Chunked, lazily generated, deterministic."""

    __slots__ = ("survey", "frame", "size", "seed", "terrain", "elev_cm",
                 "fertility", "obj_kind", "objects", "_gen", "datum_m",
                 "_aspect", "_slope_rise", "_complete")

    def __init__(self, survey: site_mod.Survey, size: int = 192, seed: int = 0):
        self.survey = survey
        self.frame = geo.LocalFrame(survey.lat, survey.lon)
        self.size = size
        self.seed = rng.mix(seed, rng.tag("site"),
                            int(round(survey.lat * 10000)),
                            int(round(survey.lon * 10000)))
        n = size * size
        self.terrain = bytearray(n)
        self.elev_cm = array("h", [0]) * 0 or array("h", bytes(2 * n))
        self.fertility = bytearray(n)
        self.obj_kind = bytearray(n)
        self.objects: dict[int, object] = {}
        self._gen: set[tuple[int, int]] = set()
        self._complete = False
        self.datum_m = survey.elev_m

        # Slope direction and magnitude for the whole site, from the world
        # raster. A 192 m map on a 6-degree hillside drops 20 m corner to
        # corner, which matters for drainage, building and line of sight.
        self._slope_rise = math.tan(math.radians(min(35.0, survey.slope_deg)))
        h = rng.mix(self.seed, rng.tag("aspect"))
        self._aspect = rng.unit(h) * 2.0 * math.pi

    # ---- addressing ----

    def idx(self, x: int, y: int) -> int:
        return y * self.size + x

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def geo_of(self, x: int, y: int) -> tuple[float, float]:
        """The real latitude and longitude of a tile. Every tile in PEG has
        one; this is what makes the map a place rather than a level."""
        return self.frame.to_geo(x - self.size / 2.0, y - self.size / 2.0)

    def terrain_at(self, x: int, y: int) -> Terrain:
        self.ensure(x, y)
        return TERRAIN_BY_ID[self.terrain[self.idx(x, y)]]

    def elevation_at(self, x: int, y: int) -> float:
        """Metres above sea level."""
        self.ensure(x, y)
        return self.datum_m + self.elev_cm[self.idx(x, y)] / 100.0

    def object_at(self, x: int, y: int):
        self.ensure(x, y)
        return self.objects.get(self.idx(x, y))

    def set_terrain(self, x: int, y: int, t: Terrain) -> None:
        self.ensure(x, y)
        self.terrain[self.idx(x, y)] = t.id

    def clear_object(self, x: int, y: int) -> None:
        i = self.idx(x, y)
        self.obj_kind[i] = OBJ_NONE
        self.objects.pop(i, None)

    # ---- movement and sight ----

    def walk_cost(self, x: int, y: int) -> float:
        """Seconds to enter this tile. Infinite if impassable."""
        self.ensure(x, y)
        i = self.idx(x, y)
        t = TERRAIN_BY_ID[self.terrain[i]]
        c = t.walk_s
        k = self.obj_kind[i]
        if k == OBJ_BOULDER:
            return 99.0
        if k == OBJ_BUILDING:
            b = self.objects.get(i)
            return 99.0 if getattr(b, "blocks", True) else c
        if k == OBJ_PLANT:
            p = self.objects[i]
            if p.species.height_m > 4 and p.growth > 0.35:
                c += 1.4      # push through trunks and understorey
            else:
                c += 0.25
        return c

    def passable(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and self.walk_cost(x, y) < 20.0

    def cover_at(self, x: int, y: int) -> float:
        self.ensure(x, y)
        i = self.idx(x, y)
        c = TERRAIN_BY_ID[self.terrain[i]].cover
        k = self.obj_kind[i]
        if k == OBJ_BOULDER:
            c = max(c, 0.75)
        elif k == OBJ_BUILDING:
            c = max(c, 0.85)
        elif k == OBJ_PLANT:
            p = self.objects[i]
            if p.species.height_m > 4:
                c = max(c, 0.35 * p.growth)
            else:
                c = max(c, 0.20 * p.growth)
        return c

    def blocks_sight(self, x: int, y: int) -> bool:
        self.ensure(x, y)
        i = self.idx(x, y)
        k = self.obj_kind[i]
        if k == OBJ_BOULDER:
            return True
        if k == OBJ_BUILDING:
            return getattr(self.objects.get(i), "blocks", True)
        if k == OBJ_PLANT:
            return self.objects[i].blocks_sight
        return TERRAIN_BY_ID[self.terrain[i]] is CLIFF

    def line_of_sight(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        """Bresenham with elevation. Terrain between two points blocks sight
        if it is high enough to interrupt the straight line between them --
        which is why shooting uphill is a bad idea, and why a colony sited on
        a reverse slope is invisible until the enemy is on top of it.

        This is the hottest function in a firefight, so it reads the tile
        arrays directly rather than going through the accessors.
        """
        self.ensure(x0, y0)
        self.ensure(x1, y1)
        size = self.size
        okind = self.obj_kind
        objs = self.objects
        elev = self.elev_cm
        cliff_id = CLIFF.id
        terr = self.terrain

        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        x, y = x0, y0
        dist = max(1.0, math.hypot(x1 - x0, y1 - y0))
        z0 = elev[y0 * size + x0] + 150.0     # eye height, centimetres
        z1 = elev[y1 * size + x1] + 90.0      # centre of mass

        while True:
            if x == x1 and y == y1:
                return True
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
            if x < 0 or y < 0 or x >= size or y >= size:
                return False
            if x == x1 and y == y1:
                return True

            self.ensure(x, y)
            i = y * size + x
            k = okind[i]
            if k:
                if k == OBJ_BOULDER:
                    return False
                if k == OBJ_PLANT:
                    if objs[i].blocks_sight:
                        return False
                elif k == OBJ_BUILDING:
                    if getattr(objs.get(i), "blocks", True):
                        return False
            elif terr[i] == cliff_id:
                return False

            t = math.hypot(x - x0, y - y0) / dist
            if elev[i] > z0 + (z1 - z0) * t + 40.0:
                return False

    # ---- generation ----

    def ensure(self, x: int, y: int) -> None:
        if self._complete:
            return
        key = (x // CHUNK, y // CHUNK)
        if key not in self._gen:
            self._gen.add(key)
            self._gen_chunk(key[0], key[1])

    def ensure_all(self) -> None:
        for cy in range((self.size + CHUNK - 1) // CHUNK):
            for cx in range((self.size + CHUNK - 1) // CHUNK):
                if (cx, cy) not in self._gen:
                    self._gen.add((cx, cy))
                    self._gen_chunk(cx, cy)
        # Lets ensure() short-circuit. Line of sight walks a hundred tiles per
        # call, several times a second per fighter, and the chunk lookup was
        # costing more than the visibility maths.
        self._complete = True

    def _gen_chunk(self, cx: int, cy: int) -> None:
        s = self.survey
        seed = self.seed
        size = self.size
        half = size / 2.0
        b = s.biome
        species = _species_for(b.key)

        # Where the water is, if anywhere. A river within a few hundred metres
        # of the site centre actually crosses the map, and the map should show
        # that rather than pretending water is an abstract statistic.
        river_offset = None
        if s.river_km * 1000.0 < half + 40:
            rh = rng.mix(seed, rng.tag("river"))
            river_offset = (s.river_km * 1000.0 * (1 if rng.unit(rh) < 0.5 else -1),
                            rng.unit(rng.splitmix64(rh)) * math.pi)
        sea_dist_m = s.coast_km * 1000.0

        # Ore body placement for this site, if the geology allows one.
        ore_mineral, ore_center, ore_grade = self._pick_ore()
        tile_tag = rng.tag("tile")

        cos_a = math.cos(self._aspect)
        sin_a = math.sin(self._aspect)
        soil_depth_m = s.soil.depth_cm / 100.0
        rough = 0.35 + s.relief_m / 900.0

        x0 = cx * CHUNK
        y0 = cy * CHUNK
        w = CHUNK + 2

        # Precompute the noise fields for this chunk with a one-tile border.
        # Sampling them per tile instead cost six fBm evaluations per square
        # metre, which at 36,864 tiles per site is the difference between a
        # map that appears and a map you wait for.
        # Step sizes are chosen against each field's feature size: microrelief
        # varies over ~9 m so it is sampled every 2, the 55 m regional swell
        # every 8.
        fine = _field(seed ^ 0x22, x0, y0, w, 2, 9.0, 2, 0.45 * rough)
        coarse = _field(seed ^ 0x11, x0, y0, w, 8, 55.0, 3, 3.4 * rough)
        ground = _field(seed ^ 0x66, x0, y0, w, 4, 17.0, 2, 1.0)
        clump = _field(seed ^ 0x77, x0, y0, w, 4, 22.0, 2, 1.0)

        for yy in range(y0, min(y0 + CHUNK, size)):
            j = yy - y0 + 1
            for xx in range(x0, min(x0 + CHUNK, size)):
                i = yy * size + xx
                k = xx - x0 + 1
                o = j * w + k
                dx = xx - half
                dy = yy - half

                # --- height -------------------------------------------------
                # Regional slope plus fBm microrelief at two scales.
                h = (dx * cos_a + dy * sin_a) * self._slope_rise + coarse[o] + fine[o]

                # Local steepness by finite difference on the fine field.
                local_slope = s.slope_deg + math.degrees(math.atan(math.hypot(
                    (fine[o + 1] - fine[o - 1]) * 0.5,
                    (fine[o + w] - fine[o - w]) * 0.5)))

                terr = None

                # --- water --------------------------------------------------
                if sea_dist_m < half:
                    # The coast runs across the map; put sea on one side.
                    coast_line = sea_dist_m - half
                    wobble = rng.fbm2(seed ^ 0x33, xx / 30.0, 0.0, 3) * 14.0
                    if dy < coast_line + wobble:
                        depth = (coast_line + wobble) - dy
                        h = -depth * 0.09
                        terr = DEEP_WATER if depth > 42 else (
                            WATER if depth > 12 else SHALLOWS)

                if terr is None and river_offset is not None:
                    off, ang = river_offset
                    # Distance from a meandering line across the map.
                    proj = dx * math.cos(ang) + dy * math.sin(ang)
                    perp = -dx * math.sin(ang) + dy * math.cos(ang)
                    meander = rng.fbm2(seed ^ 0x44, proj / 40.0, 0.0, 3) * 11.0
                    width = max(2.0, 34.0 - s.river_rank * 4.2)
                    d_river = abs(perp - off - meander)
                    if d_river < width:
                        h = -1.1 - (1.0 - d_river / width) * 1.4
                        terr = WATER if d_river < width * 0.65 else SHALLOWS
                    elif d_river < width + 7:
                        h -= 0.7
                        if b.key in ("trop_rain", "wetland", "temp_rain"):
                            terr = MARSH
                        else:
                            terr = MUD

                # --- ground -------------------------------------------------
                if terr is None:
                    terr = self._ground(s, b, xx, yy, local_slope,
                                        soil_depth_m, ground[o])

                self.terrain[i] = terr.id
                self.elev_cm[i] = int(rng.clamp(h * 100.0, -32000, 32000))

                # One hash per tile, split into the several independent
                # values this tile needs. Hashing four times over was the
                # second-largest cost in generation after the noise.
                th = rng.mix(seed, tile_tag, xx, yy)
                h1 = rng.splitmix64(th)
                h2 = rng.splitmix64(th ^ 0x5DEECE66D)

                fert = s.soil.fertility * terr.fertility
                fert *= 0.85 + 0.3 * rng.unit(th)
                self.fertility[i] = int(rng.clamp01(fert) * 255)

                # --- objects ------------------------------------------------
                self._place_object(i, xx, yy, terr, b, species, s,
                                   ore_mineral, ore_center, ore_grade,
                                   th, h1, h2, clump[o])

    def _ground(self, s, b, xx: int, yy: int, local_slope: float,
                soil_depth_m: float, g: float):
        """Pick a ground terrain from soil depth, slope, biome and a patchiness
        field. ``g`` is fBm in roughly [-1, 1] and drives the mosaic: real
        ground is a patchwork of bare earth, turf and stone, not one texture
        stamped across a hectare."""
        seed = self.seed

        if b.key == "ice":
            return ICE
        if s.clim.coldest < -18 and s.clim.warmest < 4:
            return SNOW

        if local_slope > 42:
            return CLIFF
        if local_slope > 30 or soil_depth_m < 0.08:
            return ROCK if g > -0.15 else SCREE
        if local_slope > 22 and g > 0.35:
            return SCREE

        if s.soil.stoniness > 0.45 and g > 0.45:
            return GRAVEL

        if b.key in ("desert", "semidesert"):
            dune = rng.billow2(rng.mix(seed, rng.tag("dune")), xx / 40.0, yy / 40.0, 2)
            return SAND if dune > 0.32 else GRAVEL
        if b.key in ("wetland", "mangrove"):
            return MARSH if g > -0.4 else MUD
        if b.key in ("tundra", "alpine"):
            if local_slope > 14 or g > 0.4:
                return GRAVEL
            return SOIL if g > -0.2 else MUD

        # Temperate and tropical ground: turf where cover is good, bare soil
        # in the gaps, richer soil in the hollows where washed-out fines
        # collect.
        fert = s.soil.fertility
        if g < -0.35 and fert > 0.45:
            return RICH_SOIL
        if g > 0.42 and fert < 0.55:
            return SOIL
        # Under a closed canopy there is leaf litter, not turf.
        if b.trees_per_ha > 400:
            return SOIL if g > -0.62 else MUD
        return GRASS if b.cover > 0.45 else SOIL

    def _pick_ore(self):
        """Choose at most one workable ore body for this site."""
        s = self.survey
        best = max(("iron", "copper", "coal"), key=lambda m: s.minerals.get(m, 0.0))
        pot = s.minerals.get(best, 0.0)
        h = rng.mix(self.seed, rng.tag("orebody"))
        if pot < 0.35 or rng.unit(h) > pot:
            return None, (0, 0), 0.0
        cx = rng.unit(rng.splitmix64(h)) * self.size
        cy = rng.unit(rng.splitmix64(h ^ 0x9E3)) * self.size
        grade = 0.15 + pot * 0.45
        return best, (cx, cy), grade

    def _place_object(self, i: int, xx: int, yy: int, terr, b, species, s,
                      ore_mineral, ore_center, ore_grade,
                      th: int, h1: int, h2: int, clump: float) -> None:
        if terr.water or terr is CLIFF:
            return

        # Ore outcrops: only where the body is, and only in rock.
        if ore_mineral and terr in (ROCK, SCREE, GRAVEL):
            d = math.hypot(xx - ore_center[0], yy - ore_center[1])
            if d < 16 and rng.unit(h1) < 0.55 * (1.0 - d / 16.0):
                self.obj_kind[i] = OBJ_ORE
                self.objects[i] = OreBody(
                    ore_mineral, kg=900.0 + rng.unit(h2) * 3500.0,
                    grade=ore_grade)
                return

        # Boulders.
        boulder_p = 0.004 + s.soil.stoniness * 0.02
        if terr in (ROCK, SCREE):
            boulder_p += 0.05
        if rng.unit(h1) < boulder_p:
            self.obj_kind[i] = OBJ_BOULDER
            return

        # Plants. Density comes from the biome's stems per hectare converted
        # to a per-square-metre probability, so an "800 trees/ha" boreal forest
        # really does put a stem every 3.5 m. Test the probability before
        # choosing a species: on open ground this rejects 95% of tiles in one
        # comparison.
        if not species or terr.fertility <= 0.0:
            return
        tree_p = b.trees_per_ha / 10000.0
        shrub_p = b.cover * 0.22
        scale = terr.fertility * (0.5 + self.fertility[i] / 255.0)
        # Clumping: forests are patchy, not a uniform sprinkle.
        scale *= max(0.05, 1.0 + clump * 0.85)
        roll = rng.unit(th)
        if roll >= (tree_p + shrub_p) * scale:
            return

        r = rng.Rng(h2)
        want_tree = roll < tree_p * scale
        pool = [(sp, wt) for sp, wt in species
                if (sp.height_m > 4.0) == want_tree]
        if not pool:
            pool = list(species)
        sp = r.weighted(pool)
        growth = min(1.0, 0.18 + r.random() ** 0.6)
        self.obj_kind[i] = OBJ_PLANT
        self.objects[i] = Plant(sp, growth,
                                age_days=growth * sp.years_to_mature * 365)


def generate(survey: site_mod.Survey, size: int = 192, seed: int = 0,
             eager: bool = False) -> LocalMap:
    m = LocalMap(survey, size, seed)
    if eager:
        m.ensure_all()
    return m
